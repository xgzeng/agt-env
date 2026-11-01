"""The adapter interface and helpers shared by adapters."""

import json
import os
import shutil
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from agt_view.jsonc import loads_jsonc
from agt_view.model import AgentReport, Finding, Item, Kind, Scope, Status
from agt_view.skills import FoundSkill


@dataclass(frozen=True)
class Context:
    home: Path
    cwd: Path
    env: Mapping[str, str]
    # Prefix for machine-wide locations, so tests never read the real system.
    system_root: Path = field(default=Path("/"))

    def on_path(self, binary: str) -> bool:
        """Check PATH without executing anything; shims count as installed."""
        return shutil.which(binary, path=self.env.get("PATH", "")) is not None


INVALID_SERVER = Finding("invalid server entry")
MISSING_PLUGIN = Finding("plugin files are missing")


class Adapter(Protocol):
    id: str

    def scan(self, context: Context) -> AgentReport: ...


def config_dir(context: Context, variable: str, default: Path) -> tuple[Path, str | None]:
    """Return an agent's config directory and the variable that moved it, if any."""
    value = context.env.get(variable)
    if not value:
        return default, None
    path = Path(value).expanduser()
    return path, variable if os.path.realpath(path) != os.path.realpath(default) else None


def read_json(
    path: Path, warnings: list[str], *, jsonc: bool = False, blank_ok: bool = False
) -> tuple[dict, bool]:
    """Return the file's top-level object and whether it could be read.

    A missing file is normal. A non-object is treated as empty.
    """
    if not os.path.isfile(path):
        return {}, True
    try:
        with open(path, encoding="utf-8") as file:
            text = file.read()
        if blank_ok and not text.strip():
            return {}, True
        data = loads_jsonc(text) if jsonc else json.loads(text)
    except (OSError, ValueError) as error:
        warnings.append(f"cannot read {path}: {error}")
        return {}, False
    return (data if isinstance(data, dict) else {}), True


def first_existing(*paths: Path) -> Path | None:
    return next((path for path in paths if os.path.exists(path)), None)


def list_entries(directory: Path, warnings: list[str]) -> list[Path]:
    """Return a directory's non-hidden entries. Only an unreadable directory warns."""
    try:
        with os.scandir(directory) as iterator:
            names = sorted(entry.name for entry in iterator)
    except (FileNotFoundError, NotADirectoryError):
        return []
    except OSError as error:
        warnings.append(f"cannot read {directory}: {error.strerror}")
        return []
    return [directory / name for name in names if not name.startswith(".")]


def skill_item(
    agent: str, skill: FoundSkill, scope: Scope, status: Status, problem: str = ""
) -> Item:
    """Build a skill item. `problem` is why the agent refuses it, if it was readable."""
    fields = skill.fields or {}
    return Item(
        name=fields.get("name") or skill.default_name,
        kind=Kind.SKILL,
        native_kind="skill",
        agent=agent,
        scope=scope,
        status=status,
        description=fields.get("description", ""),
        source_path=skill.path,
        resolved_path=skill.resolved_path,
        findings=[Finding(skill.problem or problem)] if status is Status.BROKEN else [],
    )


def mcp_item(
    agent: str, name: str, details: dict | None, scope: Scope, status: Status, path: Path
) -> Item:
    """Build a server item. Details of None mean the entry is invalid."""
    return Item(
        name=name,
        kind=Kind.MCP,
        native_kind="mcp server",
        agent=agent,
        scope=scope,
        status=Status.BROKEN if details is None else status,
        description="",
        source_path=path,
        resolved_path=Path(os.path.realpath(path)),
        details=details or {},
        findings=[INVALID_SERVER] if details is None else [],
    )


def plugin_item(
    agent: str,
    name: str,
    native_kind: str,
    scope: Scope,
    status: Status,
    path: Path,
    description: str = "",
) -> Item:
    return Item(
        name=name,
        kind=Kind.PLUGIN,
        native_kind=native_kind,
        agent=agent,
        scope=scope,
        status=status,
        description=description,
        source_path=path,
        resolved_path=Path(os.path.realpath(path)),
        findings=[MISSING_PLUGIN] if status is Status.BROKEN else [],
    )


def loads_skills(plugin: Item) -> bool:
    """A plugin that's turned off, replaced, or missing contributes no skills."""
    return plugin.status not in (Status.DISABLED, Status.SHADOWED, Status.BROKEN)


def mark_bundled(skill: Item, plugin: Item, name: str) -> Item:
    """Make a skill item report the plugin it came from, under the name the agent gives it."""
    skill.name = name
    skill.native_kind = "plugin skill"
    skill.details["plugin"] = plugin.name
    if skill.status is Status.ENABLED:
        skill.status = plugin.status
    return skill


def listed_paths(value: object) -> list[str]:
    """Read a manifest field that holds a path or a list of paths."""
    values = value if isinstance(value, list) else [value]
    return [entry for entry in values if isinstance(entry, str)]


def is_inside_plugin(path: str) -> bool:
    """Whether a manifest path has the form `./something`, staying inside the plugin."""
    return path.startswith("./") and ".." not in Path(path).parts


def mark_shadowed(
    ranked: Iterable[Item],
    *,
    key: Callable[[Item], str] = lambda item: item.name,
    winners: frozenset[Status] = frozenset({Status.ENABLED}),
) -> None:
    """Mark what loses to a same-named item. `ranked` is highest precedence first.

    `winners` is the statuses that take the name. In some agents a turned-off item still does.
    """
    won: dict[str, Item] = {}
    for item in ranked:
        winner = won.get(key(item))
        if winner is None:
            if item.status in winners:
                won[key(item)] = item
        elif item.status is Status.ENABLED:
            item.status = Status.SHADOWED
            item.findings.append(Finding("shadowed by", winner.source_path))


def load_once(items: list[Item]) -> list[Item]:
    """Keep the first skill per real path: agents load a linked skill only once."""
    seen: set[Path] = set()
    unique = []
    for item in items:
        if item.kind is not Kind.SKILL or item.status is Status.BROKEN:
            unique.append(item)
        elif item.resolved_path not in seen:
            seen.add(item.resolved_path)
            unique.append(item)
    return unique


def unique_roots(roots: list[tuple[Scope, Path]]) -> list[tuple[Scope, Path]]:
    """Drop roots that are the same directory, keeping the first (cwd may be HOME)."""
    seen: set[str] = set()
    unique = []
    for label, root in roots:
        real = os.path.realpath(root)
        if real not in seen:
            seen.add(real)
            unique.append((label, root))
    return unique
