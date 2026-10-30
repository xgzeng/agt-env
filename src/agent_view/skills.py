"""Find skill directories on disk. Agent-specific rules live in the adapters."""

import os
from dataclasses import dataclass
from pathlib import Path

from agent_view.frontmatter import parse_frontmatter

SKILL_FILE = "SKILL.md"
_MAX_READ_BYTES = 64 * 1024


@dataclass
class FoundSkill:
    path: Path
    skill_file: Path
    resolved_path: Path
    broken: bool = False
    fields: dict[str, str] | None = None
    body: str = ""


def scan_skills(
    root: Path, *, max_depth: int = 1, skip: frozenset[str] = frozenset()
) -> tuple[list[FoundSkill], list[str]]:
    """Return skills under root and warnings. Hidden directories are not entered.

    A missing root is normal and produces no warning; an unreadable one does.
    `skip` names entries to ignore directly under root.
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
            if name.startswith(".") or (depth == 1 and name in skip):
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
            if os.path.lexists(path / SKILL_FILE):
                found.append(_read_skill(path, warnings))
            if depth < max_depth:
                visit(path, depth + 1, ancestors | {real})

    visit(root, 1, frozenset({os.path.realpath(root)}))
    return found, warnings


def _broken(path: Path) -> FoundSkill:
    return FoundSkill(path, path / SKILL_FILE, Path(os.path.realpath(path)), broken=True)


def _read_skill(path: Path, warnings: list[str]) -> FoundSkill:
    skill = _broken(path)
    # Refuse FIFOs and devices: opening one could block or never finish.
    if not os.path.isfile(skill.skill_file):
        return skill
    try:
        with open(skill.skill_file, "rb") as file:
            text = file.read(_MAX_READ_BYTES).decode("utf-8", errors="replace")
    except OSError as error:
        warnings.append(f"cannot read {skill.skill_file}: {error.strerror}")
        return skill
    skill.broken = False
    skill.fields, skill.body = parse_frontmatter(text)
    return skill
