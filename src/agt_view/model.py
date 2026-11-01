"""The data model shared by every adapter and the renderer."""

import os
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path


class Kind(StrEnum):
    SKILL = "skill"
    MCP = "mcp"
    PLUGIN = "plugin"
    HOOK = "hook"
    SUBAGENT = "subagent"
    COMMAND = "command"


class Scope(StrEnum):
    """In display order."""

    PROJECT = "project"
    USER = "user"
    SYSTEM = "system"


class Status(StrEnum):
    ENABLED = "enabled"
    DISABLED = "disabled"
    PENDING = "pending"
    BROKEN = "broken"
    UNKNOWN = "unknown"
    SHADOWED = "shadowed"  # It would load, but the agent uses a same-named item instead.


@dataclass(frozen=True)
class Finding:
    """A problem to show with an item. The text is fixed, never taken from a file."""

    text: str
    path: Path | None = None


@dataclass
class Item:
    name: str
    kind: Kind
    native_kind: str
    agent: str
    scope: Scope
    status: Status
    description: str
    source_path: Path
    resolved_path: Path
    shared_with: list[str] = field(default_factory=list)
    details: dict[str, object] = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)
    # The parts of `name` that only say where the item came from.
    name_prefix: str = ""
    name_suffix: str = ""

    @property
    def id(self) -> str:
        return f"{self.agent}:{self.kind}:{self.scope}:{self.name}"

    @property
    def is_symlink(self) -> bool:
        return os.path.islink(self.source_path)


@dataclass
class AgentReport:
    agent: str
    installed: bool
    config_path: Path | None
    override_var: str | None = None
    items: list[Item] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def configured(self) -> bool:
        return self.config_path is not None

    @property
    def active(self) -> bool:
        return self.installed and self.configured
