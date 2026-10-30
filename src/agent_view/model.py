"""The data model shared by every adapter and the renderer."""

import os
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path


class Kind(StrEnum):
    SKILL = "skill"


class Scope(StrEnum):
    USER = "user"
    PROJECT = "project"
    SYSTEM = "system"


class Status(StrEnum):
    ENABLED = "enabled"
    DISABLED = "disabled"
    BROKEN = "broken"
    UNKNOWN = "unknown"


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
