"""Codex adapter.

Sources: the Codex skills and config documentation, plus behavior observed from
`codex app-server` (`skills/list`) with Codex 0.166.0; see
tests/test_codex_oracle.py. Observed but undocumented:

- `$CODEX_HOME/skills` and a project's `.codex/skills` are also read, the latter
  even when the project isn't trusted.
- Discovery recurses six levels below a root, into skill directories too, and
  skips hidden directories. `$CODEX_HOME/skills/.system` holds bundled skills.
- `description` is required and `name` defaults to the directory name.
- A skill linked from both user and project roots loads once, as a user skill.
  Different skills sharing a name all load.
- `[[skills.config]]` disables a skill only when `path` names its SKILL.md file.
"""

import os
import tomllib
from pathlib import Path

from agent_view.adapters.base import Context, config_dir, load_once, unique_roots
from agent_view.model import AgentReport, Item, Kind, Scope, Status
from agent_view.skills import FoundSkill, scan_skills

_MAX_DEPTH = 6


class Codex:
    id = "codex"

    def scan(self, context: Context) -> AgentReport:
        codex_home, override = config_dir(context, "CODEX_HOME", context.home / ".codex")
        project_dir = context.cwd / ".codex"
        config_path = next(
            (path for path in (codex_home, project_dir) if os.path.isdir(path)), None
        )
        report = AgentReport(
            agent=self.id,
            installed=context.on_path("codex"),
            config_path=config_path,
            override_var=override,
        )

        disabled = _disabled_skill_files(codex_home / "config.toml", report.warnings)
        roots = unique_roots(
            [
                (Scope.SYSTEM, context.system_root / "etc" / "codex" / "skills"),
                (Scope.SYSTEM, codex_home / "skills" / ".system"),
                (Scope.USER, context.home / ".agents" / "skills"),
                (Scope.USER, codex_home / "skills"),
                (Scope.PROJECT, context.cwd / ".agents" / "skills"),
                (Scope.PROJECT, project_dir / "skills"),
            ]
        )
        for scope, root in roots:
            found, warnings = scan_skills(root, max_depth=_MAX_DEPTH)
            report.warnings.extend(warnings)
            report.items.extend(self._item(skill, scope, disabled) for skill in found)
        report.items = load_once(report.items)
        return report

    def _item(self, skill: FoundSkill, scope: Scope, disabled: set[str] | None) -> Item:
        fields = skill.fields or {}
        return Item(
            name=fields.get("name") or skill.path.name,
            kind=Kind.SKILL,
            native_kind="skill",
            agent=self.id,
            scope=scope,
            status=_status(skill, disabled),
            description=fields.get("description", ""),
            source_path=skill.path,
            resolved_path=skill.resolved_path,
        )


def _status(skill: FoundSkill, disabled: set[str] | None) -> Status:
    if skill.broken or not (skill.fields or {}).get("description"):
        return Status.BROKEN
    if disabled is None:
        return Status.UNKNOWN
    if os.path.realpath(skill.skill_file) in disabled:
        return Status.DISABLED
    return Status.ENABLED


def _disabled_skill_files(config: Path, warnings: list[str]) -> set[str] | None:
    """Return real paths of disabled SKILL.md files, or None if config is unreadable."""
    if not os.path.isfile(config):
        return set()
    try:
        with open(config, "rb") as file:
            data = tomllib.load(file)
    except (OSError, ValueError) as error:
        warnings.append(f"cannot read {config}: {error}")
        return None

    skills = data.get("skills")
    entries = skills.get("config") if isinstance(skills, dict) else None
    disabled: set[str] = set()
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            continue
        real = os.path.realpath(Path(entry["path"]).expanduser())
        if entry.get("enabled") is False:
            disabled.add(real)
        else:
            disabled.discard(real)
    return disabled
