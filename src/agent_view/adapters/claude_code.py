"""Claude Code adapter.

Sources: https://code.claude.com/docs/en/skills and /settings, checked against
Claude Code 2.1.314. Claude Code has no non-interactive skill listing to test
against, so these rules are documented rather than observed. Assumed where the
docs are silent: `CLAUDE_CONFIG_DIR` also moves the personal skills directory,
and the managed directory on macOS is /Library/Application Support/ClaudeCode.

`skills/synced` holds claude.ai account skills in an undocumented layout, so it
is skipped for now.
"""

import json
import os
import re
import sys
import unicodedata
from pathlib import Path

from agent_view.adapters.base import Context, config_dir, load_once, unique_roots
from agent_view.model import AgentReport, Item, Kind, Scope, Status
from agent_view.skills import FoundSkill, scan_skills

_VISIBLE_OVERRIDES = {"on", "name-only", "user-invocable-only"}
_DENY_RULE = re.compile(r"^Skill\((.+?)(?:(?: |:)\*)?\)$")


class ClaudeCode:
    id = "claude-code"

    def scan(self, context: Context) -> AgentReport:
        user_dir, override = config_dir(context, "CLAUDE_CONFIG_DIR", context.home / ".claude")
        user_state = (user_dir if override else context.home) / ".claude.json"
        project_dir = context.cwd / ".claude"
        managed_dir = _managed_dir(context)

        report = AgentReport(
            agent=self.id,
            installed=context.on_path("claude"),
            config_path=_first_existing(
                user_dir, user_state, project_dir, context.cwd / ".mcp.json"
            ),
            override_var=override,
        )

        settings = _Settings(
            [
                user_dir / "settings.json",
                project_dir / "settings.json",
                project_dir / "settings.local.json",
                managed_dir / "managed-settings.json",
            ],
            report.warnings,
        )
        roots = unique_roots(
            [
                (Scope.SYSTEM, managed_dir / ".claude" / "skills"),
                (Scope.USER, user_dir / "skills"),
                (Scope.PROJECT, project_dir / "skills"),
            ]
        )
        for scope, root in roots:
            found, warnings = scan_skills(root, skip=frozenset({"synced"}))
            report.warnings.extend(warnings)
            report.items.extend(self._item(skill, scope, settings) for skill in found)
        report.items = load_once(report.items)
        return report

    def _item(self, skill: FoundSkill, scope: Scope, settings: "_Settings") -> Item:
        fields = skill.fields or {}
        name = fields.get("name") or skill.path.name
        description = fields.get("description") or _first_line(skill.body)
        if skill.broken or not _is_loadable_name(name):
            status = Status.BROKEN
        else:
            status = settings.status(name)
        return Item(
            name=name,
            kind=Kind.SKILL,
            native_kind="skill",
            agent=self.id,
            scope=scope,
            status=status,
            description=description,
            source_path=skill.path,
            resolved_path=skill.resolved_path,
        )


class _Settings:
    """Skill visibility merged from settings files, lowest precedence first."""

    def __init__(self, paths: list[Path], warnings: list[str]) -> None:
        self.reliable = True
        self.overrides: dict[str, object] = {}
        self.deny: list[object] = []
        for path in paths:
            data = self._load(path, warnings)
            overrides = data.get("skillOverrides")
            if isinstance(overrides, dict):
                self.overrides.update(overrides)
            permissions = data.get("permissions")
            if isinstance(permissions, dict) and isinstance(permissions.get("deny"), list):
                self.deny.extend(permissions["deny"])

    def _load(self, path: Path, warnings: list[str]) -> dict:
        if not os.path.isfile(path):
            return {}
        try:
            with open(path, encoding="utf-8") as file:
                data = json.load(file)
        except (OSError, ValueError) as error:
            warnings.append(f"cannot read {path}: {error}")
            self.reliable = False
            return {}
        return data if isinstance(data, dict) else {}

    def status(self, name: str) -> Status:
        if not self.reliable:
            return Status.UNKNOWN
        if self._denied(name):
            return Status.DISABLED
        override = self.overrides.get(name, "on")
        if override == "off":
            return Status.DISABLED
        return Status.ENABLED if override in _VISIBLE_OVERRIDES else Status.UNKNOWN

    def _denied(self, name: str) -> bool:
        for rule in self.deny:
            if rule == "Skill":
                return True
            match = _DENY_RULE.match(rule) if isinstance(rule, str) else None
            if match and match[1] in (name, "*"):
                return True
        return False


def _managed_dir(context: Context) -> Path:
    if sys.platform == "darwin":
        return context.system_root / "Library" / "Application Support" / "ClaudeCode"
    return context.system_root / "etc" / "claude-code"


def _first_existing(*paths: Path) -> Path | None:
    return next((path for path in paths if os.path.exists(path)), None)


def _first_line(body: str) -> str:
    return next((line.strip() for line in body.splitlines() if line.strip()), "")


def _is_loadable_name(name: str) -> bool:
    """Claude Code refuses these names, per the skills documentation."""
    if name == "anthropic-skills" or name.startswith("anthropic-skills:"):
        return False
    return not any(
        char in "<>" or unicodedata.category(char) in ("Cc", "Cf") for char in name
    )
