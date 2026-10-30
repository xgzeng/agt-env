"""Claude Code adapter.

Sources: https://code.claude.com/docs/en/skills, /mcp, /managed-mcp, /settings, and
/plugins-reference, checked against Claude Code 2.1.314 where it can report on them;
see tests/test_claude_oracle.py. Claude Code has no non-interactive skill listing, so
skill rules are documented rather than observed.

Observed but undocumented:
- With `CLAUDE_CONFIG_DIR`, `.claude.json` lives in that directory, not HOME.
- User MCP servers are the top-level `mcpServers` in `.claude.json`, and approvals can
  also be stored in its entry for the project.
- `plugins/installed_plugins.json` records installed plugins.

Assumed where the docs are silent: `CLAUDE_CONFIG_DIR` also moves personal skills. The
docs say a plugin missing from `enabledPlugins` starts enabled, but 2.1.314 reported one
as disabled, so that state is `unknown`.

Not listed yet: MCP servers and skills bundled in plugins, and `skills/synced`, which
holds claude.ai account skills in an undocumented layout.
"""

import fnmatch
import json
import os
import re
import sys
import unicodedata
from pathlib import Path

from agt_view.adapters.base import Context, config_dir, load_once, unique_roots
from agt_view.mcp import remote_details, stdio_details
from agt_view.model import AgentReport, Item, Kind, Scope, Status
from agt_view.skills import FoundSkill, scan_skills

_VISIBLE_OVERRIDES = {"on", "name-only", "user-invocable-only"}
_DENY_RULE = re.compile(r"^Skill\((.+?)(?:(?: |:)\*)?\)$")
_REMOTE_TRANSPORTS = {"http", "streamable-http", "sse", "ws"}
_PLUGIN_SCOPES = {"user": Scope.USER, "project": Scope.PROJECT, "local": Scope.PROJECT}
_PLUGIN_MANIFEST = Path(".claude-plugin") / "plugin.json"


class ClaudeCode:
    id = "claude-code"

    def scan(self, context: Context) -> AgentReport:
        user_dir, override = config_dir(context, "CLAUDE_CONFIG_DIR", context.home / ".claude")
        user_state_path = (user_dir if override else context.home) / ".claude.json"
        project_dir = context.cwd / ".claude"
        project_mcp_path = context.cwd / ".mcp.json"
        managed_dir = _managed_dir(context)

        report = AgentReport(
            agent=self.id,
            installed=context.on_path("claude"),
            config_path=_first_existing(user_dir, user_state_path, project_dir, project_mcp_path),
            override_var=override,
        )

        settings = _Settings(
            user=user_dir / "settings.json",
            project=project_dir / "settings.json",
            local=project_dir / "settings.local.json",
            managed=managed_dir / "managed-settings.json",
            warnings=report.warnings,
        )
        state, state_ok = _read_json(user_state_path, report.warnings)
        projects = state.get("projects")
        project_state = projects.get(str(context.cwd)) if isinstance(projects, dict) else None
        if not isinstance(project_state, dict):
            project_state = {}
        trusted = project_state.get("hasTrustDialogAccepted") is True or os.path.realpath(
            context.cwd
        ) in (os.path.realpath(context.home), os.path.realpath(user_dir))

        for scope, root in unique_roots(
            [
                (Scope.SYSTEM, managed_dir / ".claude" / "skills"),
                (Scope.USER, user_dir / "skills"),
                (Scope.PROJECT, project_dir / "skills"),
            ]
        ):
            found, warnings = scan_skills(root, skip=frozenset({"synced"}))
            report.warnings.extend(warnings)
            report.items.extend(self._skill(skill, scope, settings) for skill in found)
        report.items = load_once(report.items)

        mcp = _McpRules(settings, project_state, trusted, state_ok, managed_dir, report.warnings)
        report.items.extend(mcp.items(self.id, state, user_state_path, project_mcp_path))
        report.items.extend(self._plugins(user_dir, context, settings, report.warnings))
        return report

    def _skill(self, skill: FoundSkill, scope: Scope, settings: "_Settings") -> Item:
        fields = skill.fields or {}
        name = fields.get("name") or skill.path.name
        description = fields.get("description") or _first_line(skill.body)
        if skill.broken or not _is_loadable_name(name):
            status = Status.BROKEN
        else:
            status = settings.skill_status(name)
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

    def _plugins(
        self, user_dir: Path, context: Context, settings: "_Settings", warnings: list[str]
    ) -> list[Item]:
        record_path = user_dir / "plugins" / "installed_plugins.json"
        record, _ = _read_json(record_path, warnings)
        plugins = record.get("plugins")
        items = []
        for plugin_id, installs in plugins.items() if isinstance(plugins, dict) else ():
            for install in installs if isinstance(installs, list) else ():
                if not isinstance(install, dict):
                    continue
                scope = _PLUGIN_SCOPES.get(install.get("scope"), Scope.USER)
                if scope is Scope.PROJECT and install.get("projectPath") != str(context.cwd):
                    continue
                items.append(self._plugin(plugin_id, install, scope, record_path, settings))
        return items

    def _plugin(
        self,
        plugin_id: str,
        install: dict,
        scope: Scope,
        record_path: Path,
        settings: "_Settings",
    ) -> Item:
        install_path = install.get("installPath")
        path = Path(install_path) if isinstance(install_path, str) else record_path
        exists = isinstance(install_path, str) and os.path.isdir(path)
        manifest = _quiet_json(path / _PLUGIN_MANIFEST) if exists else {}
        description = manifest.get("description")
        return Item(
            name=plugin_id,
            kind=Kind.EXTENSION,
            native_kind="plugin",
            agent=self.id,
            scope=scope,
            status=settings.plugin_status(plugin_id) if exists else Status.BROKEN,
            description=description if isinstance(description, str) else "",
            source_path=path,
            resolved_path=Path(os.path.realpath(path)),
            details={"version": install.get("version")},
        )


class _Settings:
    """Settings layers. Merged views give later layers precedence."""

    def __init__(
        self, *, user: Path, project: Path, local: Path, managed: Path, warnings: list[str]
    ) -> None:
        self.reliable = True
        self.user = self._load(user, warnings)
        self.project = self._load(project, warnings)
        self.local = self._load(local, warnings)
        self.managed = self._load(managed, warnings)
        self.layers = (self.user, self.project, self.local, self.managed)

    def _load(self, path: Path, warnings: list[str]) -> dict:
        data, ok = _read_json(path, warnings)
        self.reliable = self.reliable and ok
        return data

    def _merged_dict(self, key: str) -> dict:
        merged: dict = {}
        for layer in self.layers:
            value = layer.get(key)
            if isinstance(value, dict):
                merged.update(value)
        return merged

    def _deny_rules(self) -> list[object]:
        rules: list[object] = []
        for layer in self.layers:
            permissions = layer.get("permissions")
            if isinstance(permissions, dict) and isinstance(permissions.get("deny"), list):
                rules.extend(permissions["deny"])
        return rules

    def skill_status(self, name: str) -> Status:
        if not self.reliable:
            return Status.UNKNOWN
        for rule in self._deny_rules():
            if rule == "Skill":
                return Status.DISABLED
            match = _DENY_RULE.match(rule) if isinstance(rule, str) else None
            if match and match[1] in (name, "*"):
                return Status.DISABLED
        override = self._merged_dict("skillOverrides").get(name, "on")
        if override == "off":
            return Status.DISABLED
        return Status.ENABLED if override in _VISIBLE_OVERRIDES else Status.UNKNOWN

    def plugin_status(self, plugin_id: str) -> Status:
        value = self._merged_dict("enabledPlugins").get(plugin_id)
        if not self.reliable or not isinstance(value, bool):
            return Status.UNKNOWN
        return Status.ENABLED if value else Status.DISABLED


class _McpRules:
    def __init__(
        self,
        settings: _Settings,
        project_state: dict,
        trusted: bool,
        state_ok: bool,
        managed_dir: Path,
        warnings: list[str],
    ) -> None:
        self.settings = settings
        self.project_state = project_state
        self.trusted = trusted
        self.state_ok = state_ok
        self.managed_path = managed_dir / "managed-mcp.json"
        self.warnings = warnings
        # When this file exists, Claude Code loads only managed servers.
        self.exclusive = os.path.exists(self.managed_path)

    def items(
        self, agent: str, state: dict, user_path: Path, project_path: Path
    ) -> list[Item]:
        managed_file, _ = _read_json(self.managed_path, self.warnings)
        project_file, project_ok = _read_json(project_path, self.warnings)
        sources: list[tuple[object, Scope, Path, bool]] = [
            (managed_file.get("mcpServers"), Scope.SYSTEM, self.managed_path, True),
            (
                self.settings.managed.get("managedMcpServers"),
                Scope.SYSTEM,
                self.managed_path.with_name("managed-settings.json"),
                True,
            ),
            (state.get("mcpServers"), Scope.USER, user_path, self.state_ok),
            (self.project_state.get("mcpServers"), Scope.PROJECT, user_path, self.state_ok),
            (project_file.get("mcpServers"), Scope.PROJECT, project_path, project_ok),
        ]
        items = []
        for servers, scope, path, ok in sources:
            if not isinstance(servers, dict):
                continue
            needs_approval = path == project_path
            for name, entry in servers.items():
                items.append(self._item(agent, name, entry, scope, path, ok, needs_approval))
        return items

    def _item(
        self,
        agent: str,
        name: str,
        entry: object,
        scope: Scope,
        path: Path,
        source_ok: bool,
        needs_approval: bool,
    ) -> Item:
        details = _server_details(entry)
        if details is None:
            status, details = Status.BROKEN, {}
        elif not (source_ok and self.settings.reliable and self.state_ok):
            status = Status.UNKNOWN
        elif (self.exclusive and scope is not Scope.SYSTEM) or self._policy_blocks(name, entry):
            status = Status.DISABLED
        elif needs_approval:
            status = self._approval(name)
        elif self._in_list(self.project_state, "disabledMcpServers", name):
            status = Status.DISABLED
        else:
            status = Status.ENABLED
        return Item(
            name=name,
            kind=Kind.MCP,
            native_kind="mcp server",
            agent=agent,
            scope=scope,
            status=status,
            description="",
            source_path=path,
            resolved_path=Path(os.path.realpath(path)),
            details=details,
        )

    def _approval(self, name: str) -> Status:
        layers = self.settings.layers
        if any(self._in_list(layer, "disabledMcpjsonServers", name) for layer in layers):
            return Status.DISABLED
        if self._in_list(self.project_state, "disabledMcpjsonServers", name):
            return Status.DISABLED
        # Approvals inside the project only count once its folder is trusted.
        approving = [self.settings.user, self.settings.managed]
        if self.trusted:
            approving += [self.settings.project, self.settings.local, self.project_state]
        for layer in approving:
            if layer.get("enableAllProjectMcpServers") is True:
                return Status.ENABLED
            if self._in_list(layer, "enabledMcpjsonServers", name):
                return Status.ENABLED
        return Status.PENDING

    def _policy_blocks(self, name: str, entry: object) -> bool:
        settings = self.settings
        denied = [rule for layer in settings.layers for rule in _rules(layer, "deniedMcpServers")]
        if any(_rule_matches(rule, name, entry) for rule in denied):
            return True
        allow_layers = settings.layers
        if settings.managed.get("allowManagedMcpServersOnly") is True:
            allow_layers = (settings.managed,)
        if not any(isinstance(layer.get("allowedMcpServers"), list) for layer in allow_layers):
            return False
        allowed = [rule for layer in allow_layers for rule in _rules(layer, "allowedMcpServers")]
        return not any(_rule_matches(rule, name, entry) for rule in allowed)

    @staticmethod
    def _in_list(layer: dict, key: str, name: str) -> bool:
        values = layer.get(key)
        return isinstance(values, list) and name in values


def _rules(layer: dict, key: str) -> list[object]:
    rules = layer.get(key)
    return rules if isinstance(rules, list) else []


def _rule_matches(rule: object, name: str, entry: object) -> bool:
    if not isinstance(rule, dict) or not isinstance(entry, dict):
        return False
    if rule.get("serverName") == name:
        return True
    command = rule.get("serverCommand")
    if isinstance(command, list) and command == [entry.get("command"), *entry.get("args", [])]:
        return True
    pattern, url = rule.get("serverUrl"), entry.get("url")
    return isinstance(pattern, str) and isinstance(url, str) and fnmatch.fnmatchcase(url, pattern)


def _server_details(entry: object) -> dict[str, object] | None:
    """Return a safe summary, or None if Claude Code would skip this entry."""
    if not isinstance(entry, dict):
        return None
    transport = entry.get("type")
    if transport in (None, "stdio"):
        # An entry with a url but no type is a configuration error, per the docs.
        if not isinstance(entry.get("command"), str) or "url" in entry:
            return None
        return stdio_details(entry["command"], entry.get("env"))
    if transport in _REMOTE_TRANSPORTS and isinstance(entry.get("url"), str):
        return remote_details(transport, entry["url"], entry.get("headers"))
    return None


def _read_json(path: Path, warnings: list[str]) -> tuple[dict, bool]:
    """Return the file's top-level object and whether it could be read.

    A missing file is normal. A non-object is treated as empty.
    """
    if not os.path.isfile(path):
        return {}, True
    try:
        with open(path, encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, ValueError) as error:
        warnings.append(f"cannot read {path}: {error}")
        return {}, False
    return (data if isinstance(data, dict) else {}), True


def _quiet_json(path: Path) -> dict:
    """Read optional metadata, where a bad file just means less detail."""
    return _read_json(path, [])[0]


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
