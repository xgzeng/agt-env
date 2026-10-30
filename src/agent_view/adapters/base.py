"""The adapter interface and helpers shared by adapters."""

import os
import shutil
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from agent_view.model import AgentReport, Item, Kind, Scope, Status


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
