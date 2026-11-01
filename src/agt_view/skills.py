"""Find skill directories on disk. Agent-specific rules live in the adapters."""

import os
from dataclasses import dataclass
from pathlib import Path

from agt_view.frontmatter import parse_frontmatter

SKILL_FILE = "SKILL.md"
_MAX_READ_BYTES = 64 * 1024
_MISSING_TARGET = "symlink target is missing"


@dataclass
class FoundSkill:
    path: Path
    skill_file: Path
    resolved_path: Path
    broken: bool = False
    fields: dict[str, str] | None = None
    body: str = ""
    standalone: bool = False  # A single Markdown file rather than a directory.
    problem: str = ""  # Why it's broken.

    @property
    def default_name(self) -> str:
        return self.path.stem if self.standalone else self.path.name


def scan_skills(
    root: Path,
    *,
    max_depth: int = 1,
    skip: frozenset[str] = frozenset(),
    include_hidden: bool = False,
    ignore: frozenset[str] = frozenset(),
    search_inside_skills: bool = True,
) -> tuple[list[FoundSkill], list[str]]:
    """Return skills under root and warnings. Hidden directories are skipped by default.

    A missing root is normal and produces no warning; an unreadable one does.
    `skip` names entries to ignore directly under root, and `ignore` names at any depth.
    """
    found: list[FoundSkill] = []
    warnings: list[str] = []

    def visit(directory: Path, depth: int, ancestors: frozenset[str]) -> None:
        try:
            with os.scandir(directory) as iterator:
                names = sorted(entry.name for entry in iterator)
        except (FileNotFoundError, NotADirectoryError):
            return
        except OSError as error:
            warnings.append(f"cannot read {directory}: {error.strerror}")
            return

        for name in names:
            if (name.startswith(".") and not include_hidden) or (depth == 1 and name in skip):
                continue
            if name in ignore:
                continue
            path = directory / name
            if not os.path.exists(path):
                if os.path.islink(path):
                    found.append(_broken(path))
                continue
            if not os.path.isdir(path):
                continue
            real = os.path.realpath(path)
            if real in ancestors:
                continue
            is_skill = os.path.lexists(path / SKILL_FILE)
            if is_skill:
                found.append(read_skill(path, warnings))
            if depth < max_depth and (search_inside_skills or not is_skill):
                visit(path, depth + 1, ancestors | {real})

    visit(root, 1, frozenset({os.path.realpath(root)}))
    return found, warnings


def skills_at(
    path: Path, warnings: list[str], *, max_depth: int = 1, include_hidden: bool = False
) -> list[FoundSkill]:
    """Read what a listed path names: a SKILL.md, a skill directory, or a directory of skills."""
    if path.name == SKILL_FILE:
        path = path.parent
    if os.path.lexists(path / SKILL_FILE):
        return [read_skill(path, warnings)]
    skills, scan_warnings = scan_skills(path, max_depth=max_depth, include_hidden=include_hidden)
    warnings.extend(scan_warnings)
    return skills


def scan_markdown_skills(root: Path) -> tuple[list[FoundSkill], list[str]]:
    """Return standalone `*.md` skills directly under root."""
    found: list[FoundSkill] = []
    warnings: list[str] = []
    try:
        with os.scandir(root) as iterator:
            names = sorted(entry.name for entry in iterator)
    except OSError:
        return found, warnings  # scan_skills reports an unreadable root.
    for name in names:
        path = root / name
        if name.startswith(".") or not name.endswith(".md") or name == SKILL_FILE:
            continue
        skill = FoundSkill(
            path,
            path,
            Path(os.path.realpath(path)),
            broken=True,
            standalone=True,
            problem=_MISSING_TARGET if _is_dangling(path) else "file can't be read",
        )
        found.append(skill)
        if os.path.isfile(path):
            _load(skill, warnings)
    return found, warnings


def _is_dangling(path: Path) -> bool:
    return os.path.islink(path) and not os.path.exists(path)


def _broken(path: Path) -> FoundSkill:
    return FoundSkill(
        path,
        path / SKILL_FILE,
        Path(os.path.realpath(path)),
        broken=True,
        problem=_MISSING_TARGET if _is_dangling(path) else f"{SKILL_FILE} can't be read",
    )


def read_skill(path: Path, warnings: list[str]) -> FoundSkill:
    """Read the skill in this directory. Broken if its SKILL.md can't be read."""
    skill = _broken(path)
    # Refuse FIFOs and devices: opening one could block or never finish.
    if os.path.isfile(skill.skill_file):
        _load(skill, warnings)
    return skill


def _load(skill: FoundSkill, warnings: list[str]) -> None:
    # A description can come from a skill file's text, so a link in a cloned repository could
    # otherwise print a key or token from any file it names. The skill stays broken.
    if not os.path.realpath(skill.skill_file).lower().endswith(".md"):
        warnings.append(f"not reading {skill.skill_file}: it links to a non-Markdown file")
        skill.problem = "not read: links to a non-Markdown file"
        return
    try:
        with open(skill.skill_file, "rb") as file:
            content = file.read(_MAX_READ_BYTES)
    except OSError as error:
        warnings.append(f"cannot read {skill.skill_file}: {error.strerror}")
        return
    skill.broken = False
    skill.problem = ""
    skill.fields, skill.body = parse_frontmatter(content.decode("utf-8", errors="replace"))
