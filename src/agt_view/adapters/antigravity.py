"""Antigravity CLI adapter. The binary is `agy`.

Discovery rules, official references, and undocumented findings are in
docs/agents/antigravity.md. Update that file when these rules change. tests/test_antigravity_oracle.py
compares results with the real agent.
"""

import os
from pathlib import Path

from agt_view.adapters.base import (
    Context,
    first_existing,
    list_entries,
    listed_paths,
    load_once,
    loads_skills,
    mark_bundled,
    mcp_item,
    plugin_item,
    read_json,
    skill_item,
)
from agt_view.mcp import remote_details, stdio_details
from agt_view.model import AgentReport, Item, Scope, Status
from agt_view.skills import FoundSkill, scan_skills

_UNNAMED_SKILL = "SKILL"


class Antigravity:
    id = "antigravity"

    def scan(self, context: Context) -> AgentReport:
        gemini_dir = context.home / ".gemini"
        cli_dir = gemini_dir / "antigravity-cli"
        shared_dir = gemini_dir / "config"
        workspace_dir = context.cwd / ".agents"
        user_mcp = shared_dir / "mcp_config.json"
        project_mcp = workspace_dir / "mcp_config.json"

        user_skill_dirs = [cli_dir / "skills", shared_dir / "skills", gemini_dir / "skills"]
        report = AgentReport(
            agent=self.id,
            installed=context.on_path("agy"),
            config_path=first_existing(
                cli_dir, user_mcp, project_mcp, *user_skill_dirs[1:], shared_dir / "plugins"
            ),
        )

        settings, settings_ok = read_json(cli_dir / "settings.json", report.warnings)
        project_status = _workspace_status(settings, settings_ok, context.cwd)

        skill_roots = [
            *[(Scope.USER, root, Status.ENABLED) for root in user_skill_dirs],
            (Scope.PROJECT, workspace_dir / "skills", project_status),
            (Scope.PROJECT, context.cwd / ".agent" / "skills", project_status),
        ]
        for scope, root, default in skill_roots:
            found, warnings = scan_skills(root, include_hidden=True)
            report.warnings.extend(warnings)
            for skill in found:
                report.items.append(self._skill(skill, scope, default))
        report.items = load_once(report.items)

        for path, scope, default in (
            (user_mcp, Scope.USER, Status.ENABLED),
            (project_mcp, Scope.PROJECT, project_status),
        ):
            report.items.extend(self._servers(path, scope, default, report))

        state, state_ok = read_json(shared_dir / "config.json", report.warnings)
        states = state.get("plugins")
        states = states if isinstance(states, dict) else {}
        for root, scope, default in (
            (shared_dir / "plugins", Scope.USER, Status.ENABLED),
            (workspace_dir / "plugins", Scope.PROJECT, project_status),
        ):
            plugins = self._plugins(root, scope, states, state_ok, report.warnings, default)
            report.items.extend(plugins)
            report.items.extend(
                load_once(
                    [
                        skill
                        for plugin in plugins
                        if loads_skills(plugin)
                        for skill in self._plugin_skills(plugin, report.warnings)
                    ]
                )
            )
        return report

    def _plugin_skills(self, plugin: Item, warnings: list[str]) -> list[Item]:
        manifest, _ = read_json(_plugin_manifest(plugin.source_path), [])
        items = []
        # A manifest's paths add to the default directory.
        for path in ("skills", *listed_paths(manifest.get("skills"))):
            found, scan_warnings = scan_skills(plugin.source_path / path, include_hidden=True)
            warnings.extend(scan_warnings)
            for skill in found:
                item = self._skill(skill, plugin.scope, plugin.status)
                items.append(mark_bundled(item, plugin, f"{plugin.name}:{item.name}"))
        return items

    def _skill(self, skill: FoundSkill, scope: Scope, default: Status) -> Item:
        status = default if not skill.broken and skill.fields is not None else Status.BROKEN
        if skill.fields is not None:
            skill.fields["name"] = skill.fields.get("name") or _UNNAMED_SKILL
        return skill_item(
            self.id, skill, scope, status, "has no frontmatter, which Antigravity CLI requires"
        )

    def _servers(
        self, path: Path, scope: Scope, default: Status, report: AgentReport
    ) -> list[Item]:
        data, ok = read_json(path, report.warnings, blank_ok=True)
        servers = data.get("mcpServers")
        items = []
        for name, entry in servers.items() if isinstance(servers, dict) else ():
            status = default if ok else Status.UNKNOWN
            if isinstance(entry, dict) and entry.get("disabled") is True:
                status = Status.DISABLED
            items.append(mcp_item(self.id, name, _server_details(entry), scope, status, path))
        return items

    def _plugins(
        self,
        root: Path,
        scope: Scope,
        states: dict,
        state_ok: bool,
        warnings: list[str],
        default: Status,
    ) -> list[Item]:
        items = []
        for path in list_entries(root, warnings):
            manifest_path = _plugin_manifest(path)
            if not os.path.exists(manifest_path):
                continue
            manifest, _ = read_json(manifest_path, [])
            description = manifest.get("description")
            items.append(
                plugin_item(
                    self.id,
                    path.name,
                    "plugin",
                    scope,
                    _plugin_status(path.name, states, state_ok, default),
                    path,
                    description if isinstance(description, str) else "",
                )
            )
        return items


def _plugin_manifest(plugin_dir: Path) -> Path:
    path = plugin_dir / "plugin.json"
    return path if os.path.exists(path) else plugin_dir / ".antigravity-plugin" / "plugin.json"


def _plugin_status(name: str, states: dict, state_ok: bool, default: Status) -> Status:
    if not state_ok:
        return Status.UNKNOWN
    entry = states.get(name)
    enabled = entry.get("enabled", True) if isinstance(entry, dict) else True
    return Status.DISABLED if enabled is False else default


def _workspace_status(settings: dict, settings_ok: bool, cwd: Path) -> Status:
    if not settings_ok:
        return Status.UNKNOWN
    trusted = settings.get("trustedWorkspaces")
    real = os.path.realpath(cwd)
    if isinstance(trusted, list) and any(
        isinstance(path, str) and os.path.realpath(path) == real for path in trusted
    ):
        return Status.ENABLED
    return Status.PENDING


def _server_details(entry: object) -> dict[str, object] | None:
    if not isinstance(entry, dict):
        return None
    if isinstance(entry.get("command"), str):
        return stdio_details(entry["command"], entry.get("env"))
    url = entry.get("serverUrl", entry.get("url"))
    if isinstance(url, str):
        return remote_details("streamable_http", url, entry.get("headers"))
    return None
