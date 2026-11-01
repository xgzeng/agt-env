"""Claude Code adapter.

Discovery rules, official references, and undocumented findings are in
docs/agents/claude-code.md. Update that file when these rules change. tests/test_claude_oracle.py
compares results with the real agent.
"""

import fnmatch
import os
import re
import sys
import unicodedata
from pathlib import Path

from agt_env.adapters.base import (
    INVALID_SERVER,
    MISSING_PLUGIN,
    Context,
    config_dir,
    definition_item,
    first_existing,
    first_line,
    is_inside_plugin,
    listed_paths,
    load_once,
    loads_skills,
    mark_bundled,
    mark_shadowed,
    marketplace_suffix,
    read_json,
    skill_item,
    unique_roots,
)
from agt_env.hooks import handlers, hook_item
from agt_env.mcp import remote_details, stdio_details
from agt_env.model import AgentReport, Finding, Item, Kind, Scope, Status
from agt_env.skills import (
    SKILL_FILE,
    FoundSkill,
    find_markdown,
    read_markdown,
    read_skill,
    scan_skills,
    skills_at,
)

_VISIBLE_OVERRIDES = {"on", "name-only", "user-invocable-only"}
_DENY_RULE = re.compile(r"^(Skill|Agent)\((.+?)(?:(?: |:)\*)?\)$")
_REMOTE_TRANSPORTS = {"http", "streamable-http", "sse", "ws"}
_PLUGIN_SCOPES = {"user": Scope.USER, "project": Scope.PROJECT, "local": Scope.PROJECT}
_PLUGIN_MANIFEST = Path(".claude-plugin") / "plugin.json"
_MAX_AGENT_NAME = 256
_NEEDS_NAME_AND_DESCRIPTION = "needs a name and a description, which Claude Code requires"
_HOOK_EVENTS = frozenset(
    {
        "SessionStart",
        "Setup",
        "UserPromptSubmit",
        "UserPromptExpansion",
        "PreToolUse",
        "PermissionRequest",
        "PermissionDenied",
        "PostToolUse",
        "PostToolUseFailure",
        "PostToolBatch",
        "Notification",
        "MessageDisplay",
        "SubagentStart",
        "SubagentStop",
        "TaskCreated",
        "TaskCompleted",
        "Stop",
        "StopFailure",
        "TeammateIdle",
        "InstructionsLoaded",
        "ConfigChange",
        "CwdChanged",
        "DirectoryAdded",
        "FileChanged",
        "WorktreeCreate",
        "WorktreeRemove",
        "PreCompact",
        "PostCompact",
        "PreModelSwitch",
        "PostModelSwitch",
        "Elicitation",
        "ElicitationResult",
        "SessionEnd",
    }
)


class ClaudeCode:
    id = "claude-code"

    def scan(self, context: Context) -> AgentReport:
        user_dir, override = config_dir(context, "CLAUDE_CONFIG_DIR", context.home / ".claude")
        user_state_path = (user_dir if override else context.home) / ".claude.json"
        project_dir = context.cwd / ".claude"
        project_mcp_path = context.cwd / ".mcp.json"
        managed_dir = _managed_dir(context)

        config_path = first_existing(user_dir, user_state_path, project_dir, project_mcp_path)
        report = AgentReport(
            agent=self.id,
            installed=context.on_path("claude"),
            config_path=config_path,
            override_var=override if config_path in (user_dir, user_state_path) else None,
        )

        settings = _Settings(
            user=user_dir / "settings.json",
            project=project_dir / "settings.json",
            local=project_dir / "settings.local.json",
            managed=managed_dir / "managed-settings.json",
            warnings=report.warnings,
        )
        state, state_ok = read_json(user_state_path, report.warnings)
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
        # Roots are in precedence order: enterprise, personal, project.
        mark_shadowed(report.items)
        skills = list(report.items)

        mcp = _McpRules(settings, project_state, trusted, state_ok, managed_dir, report.warnings)
        report.items.extend(mcp.items(self.id, state, user_state_path, project_mcp_path))
        plugins = self._plugins(user_dir, context, settings, report.warnings)
        report.items.extend(plugins)
        loaded_plugins = [plugin for plugin in plugins if loads_skills(plugin)]
        report.items.extend(
            load_once(
                [
                    skill
                    for plugin in loaded_plugins
                    for skill in self._plugin_skills(plugin, settings, report.warnings)
                ]
            )
        )

        # A skill beats a same-named command, and otherwise the order is as for skills.
        commands = [
            self._command(found, root, scope, settings)
            for scope, root in (
                (Scope.USER, user_dir / "commands"),
                (Scope.PROJECT, project_dir / "commands"),
            )
            for found in find_markdown(root, report.warnings)
        ]
        mark_shadowed([*skills, *commands])
        report.items.extend(commands)

        # Highest precedence first: managed, project, user. Plugin agents carry a prefix.
        agents_by_source = [
            [self._agent(found, scope, settings) for found in find_markdown(root, report.warnings)]
            for scope, root in (
                (Scope.SYSTEM, managed_dir / ".claude" / "agents"),
                (Scope.PROJECT, project_dir / "agents"),
                (Scope.USER, user_dir / "agents"),
            )
        ]
        # Which of two same-named files in one directory loads isn't defined, so neither is flagged.
        mark_shadowed(item for source in agents_by_source for item in _one_per_name(source))
        report.items.extend(item for source in agents_by_source for item in source)

        layers = [
            (settings.managed, Scope.SYSTEM, settings.paths[3]),
            (settings.user, Scope.USER, settings.paths[0]),
            (settings.project, Scope.PROJECT, settings.paths[1]),
            (settings.local, Scope.PROJECT, settings.paths[2]),
        ]
        for data, scope, path in layers:
            report.items.extend(
                _claude_hook(self.id, handler, scope, settings.hook_status(scope, trusted), path)
                for handler in handlers(data.get("hooks"))
            )
        for plugin in loaded_plugins:
            report.items.extend(self._plugin_extras(plugin, settings, report.warnings))
        return report

    def _command(self, found: FoundSkill, root: Path, scope: Scope, settings: "_Settings") -> Item:
        name = _command_name(found.path, root)
        return definition_item(
            self.id,
            found,
            Kind.COMMAND,
            "command",
            scope,
            settings.skill_status(name),
            name=name,
            description=_description(found),
        )

    def _agent(self, found: FoundSkill, scope: Scope, settings: "_Settings") -> Item:
        fields = found.fields or {}
        name, description = fields.get("name"), fields.get("description")
        valid = bool(name and description) and _is_agent_name(name)
        status = Status.BROKEN if not valid else settings.agent_status(name)
        return definition_item(
            self.id,
            found,
            Kind.SUBAGENT,
            "agent",
            scope,
            status,
            name=name or found.path.stem,
            description=description or "",
            problem=_NEEDS_NAME_AND_DESCRIPTION,
        )

    def _plugin_extras(
        self, plugin: Item, settings: "_Settings", warnings: list[str]
    ) -> list[Item]:
        """Return a loaded plugin's commands, agents, and hooks."""
        root = plugin.source_path
        prefix = plugin.name.partition("@")[0]
        manifest = _quiet_json(root / _PLUGIN_MANIFEST)
        items: list[Item] = []

        declared = manifest.get("commands")
        if isinstance(declared, dict):
            for name, entry in declared.items():
                source = entry.get("source") if isinstance(entry, dict) else None
                if isinstance(source, str) and is_inside_plugin(source):
                    found = read_markdown(root / source, warnings)
                else:
                    found = FoundSkill(
                        root / _PLUGIN_MANIFEST,
                        root / _PLUGIN_MANIFEST,
                        Path(os.path.realpath(root / _PLUGIN_MANIFEST)),
                    )
                items.append(self._plugin_command(found, plugin, f"{prefix}:{name}", settings))
        else:
            for path in _replacing_paths(declared, "commands"):
                base = root / path
                if os.path.isfile(base):
                    items.append(
                        self._plugin_command(
                            read_markdown(base, warnings), plugin, f"{prefix}:{base.stem}", settings
                        )
                    )
                    continue
                for found in find_markdown(base, warnings):
                    name = f"{prefix}:{_command_name(found.path, base)}"
                    items.append(self._plugin_command(found, plugin, name, settings))

        declared = manifest.get("agents")
        if declared is None:
            sources = [
                (found, root / "agents") for found in find_markdown(root / "agents", warnings)
            ]
        else:
            # Tested: unlike commands, these name files, not directories.
            sources = [
                (read_markdown(root / path, warnings), (root / path).parent)
                for path in _replacing_paths(declared, "agents")
                if os.path.isfile(root / path)
            ]
        for found, base in sources:
            fields = found.fields or {}
            name = fields.get("name")
            leaf = name if name and _is_agent_name(name) else found.path.stem
            folders = found.path.relative_to(base).parent.parts
            full_name = ":".join((prefix, *folders, leaf))
            item = definition_item(
                self.id,
                found,
                Kind.SUBAGENT,
                "agent",
                plugin.scope,
                settings.agent_status(full_name),
                name=full_name,
                description=fields.get("description", ""),
            )
            items.append(mark_bundled(item, plugin, full_name, "plugin agent", prefix=f"{prefix}:"))

        hook_files = [root / "hooks" / "hooks.json"]
        inline: list[object] = []
        for entry in (
            manifest.get("hooks")
            if isinstance(manifest.get("hooks"), list)
            else [manifest.get("hooks")]
        ):
            if isinstance(entry, str) and is_inside_plugin(entry):
                hook_files.append(root / entry)
            elif isinstance(entry, dict):
                inline.append(entry)
        sources = [(_quiet_json(path), path) for path in hook_files]
        sources += [(entry, root / _PLUGIN_MANIFEST) for entry in inline]
        for data, path in sources:
            for handler in handlers(data.get("hooks")):
                status = settings.hook_status(plugin.scope, trusted=True)
                item = _claude_hook(self.id, handler, plugin.scope, status, path, "plugin hook")
                items.append(mark_bundled(item, plugin, item.name, "plugin hook"))
        return items

    def _plugin_command(
        self, found: FoundSkill, plugin: Item, name: str, settings: "_Settings"
    ) -> Item:
        item = definition_item(
            self.id,
            found,
            Kind.COMMAND,
            "command",
            plugin.scope,
            settings.plugin_skill_status(name),
            name=name,
            description=_description(found),
        )
        prefix = f"{plugin.name.partition('@')[0]}:"
        return mark_bundled(item, plugin, name, "plugin command", prefix=prefix)

    def _skill(self, skill: FoundSkill, scope: Scope, settings: "_Settings") -> Item:
        fields = skill.fields or {}
        name = fields.get("name") or skill.path.name
        description = fields.get("description") or first_line(skill.body)
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
            findings=(
                [Finding(skill.problem or "Claude Code refuses this name")]
                if status is Status.BROKEN
                else []
            ),
        )

    def _plugins(
        self, user_dir: Path, context: Context, settings: "_Settings", warnings: list[str]
    ) -> list[Item]:
        record_path = user_dir / "plugins" / "installed_plugins.json"
        record, _ = read_json(record_path, warnings)
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
            name_suffix=marketplace_suffix(plugin_id),
            kind=Kind.PLUGIN,
            native_kind="plugin",
            agent=self.id,
            scope=scope,
            status=settings.plugin_status(plugin_id) if exists else Status.BROKEN,
            description=description if isinstance(description, str) else "",
            source_path=path,
            resolved_path=Path(os.path.realpath(path)),
            details={"version": install.get("version")},
            findings=[] if exists else [MISSING_PLUGIN],
        )

    def _plugin_skills(
        self, plugin: Item, settings: "_Settings", warnings: list[str]
    ) -> list[Item]:
        root = plugin.source_path
        prefix = plugin.name.partition("@")[0]
        manifest = _quiet_json(root / _PLUGIN_MANIFEST)
        added = [path for path in listed_paths(manifest.get("skills")) if is_inside_plugin(path)]
        if not added and not os.path.isdir(root / "skills"):
            found = [read_skill(root, warnings)] if os.path.lexists(root / SKILL_FILE) else []
        else:
            found = []
            for directory in ("skills", *added):
                found.extend(skills_at(root / directory, warnings, include_hidden=True))

        items = []
        for skill in found:
            fields = skill.fields or {}
            name = fields.get("name") or (prefix if skill.path == root else skill.path.name)
            full_name = name if name.startswith(f"{prefix}:") else f"{prefix}:{name}"
            status = Status.BROKEN if skill.broken else settings.plugin_skill_status(full_name)
            item = skill_item(self.id, skill, plugin.scope, status)
            item.description = fields.get("description") or first_line(skill.body)
            items.append(mark_bundled(item, plugin, full_name, prefix=f"{prefix}:"))
        return items


class _Settings:
    """Settings layers. Merged views give later layers precedence."""

    def __init__(
        self, *, user: Path, project: Path, local: Path, managed: Path, warnings: list[str]
    ) -> None:
        self.reliable = True
        self.paths = (user, project, local, managed)
        self.user = self._load(user, warnings)
        self.project = self._load(project, warnings)
        self.local = self._load(local, warnings)
        self.managed = self._load(managed, warnings)
        self.layers = (self.user, self.project, self.local, self.managed)

    def _load(self, path: Path, warnings: list[str]) -> dict:
        data, ok = read_json(path, warnings)
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

    def _is_denied(self, name: str, tool: str = "Skill") -> bool:
        for rule in self._deny_rules():
            if rule == tool:
                return True
            match = _DENY_RULE.match(rule) if isinstance(rule, str) else None
            if match and match[1] == tool and match[2] in (name, "*"):
                return True
        return False

    def agent_status(self, name: str) -> Status:
        if not self.reliable:
            return Status.UNKNOWN
        return Status.DISABLED if self._is_denied(name, "Agent") else Status.ENABLED

    def hook_status(self, scope: Scope, trusted: bool) -> Status:
        if not self.reliable:
            return Status.UNKNOWN
        if self.managed.get("disableAllHooks") is True:
            return Status.DISABLED
        if scope is Scope.SYSTEM:
            return Status.ENABLED
        # The last layer that sets this decides, so a project can undo a user's setting.
        disabled = False
        for layer in (self.user, self.project, self.local):
            if isinstance(layer.get("disableAllHooks"), bool):
                disabled = layer["disableAllHooks"]
        if disabled or self.managed.get("allowManagedHooksOnly") is True:
            return Status.DISABLED
        return Status.ENABLED if trusted or scope is Scope.USER else Status.PENDING

    def plugin_skill_status(self, name: str) -> Status:
        """`skillOverrides` doesn't apply to plugin skills, per the skills documentation."""
        if not self.reliable:
            return Status.UNKNOWN
        return Status.DISABLED if self._is_denied(name) else Status.ENABLED

    def skill_status(self, name: str) -> Status:
        if not self.reliable:
            return Status.UNKNOWN
        if self._is_denied(name):
            return Status.DISABLED
        override = self._merged_dict("skillOverrides").get(name, "on")
        if override == "off":
            return Status.DISABLED
        return Status.ENABLED if override in _VISIBLE_OVERRIDES else Status.UNKNOWN

    def plugin_status(self, plugin_id: str) -> Status:
        if not self.reliable:
            return Status.UNKNOWN
        # Tested: a plugin with no entry doesn't load, whatever `defaultEnabled` says.
        return (
            Status.ENABLED
            if self._merged_dict("enabledPlugins").get(plugin_id) is True
            else Status.DISABLED
        )


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

    def items(self, agent: str, state: dict, user_path: Path, project_path: Path) -> list[Item]:
        managed_file, _ = read_json(self.managed_path, self.warnings)
        project_file, project_ok = read_json(project_path, self.warnings)
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
        groups = []
        for servers, scope, path, ok in sources:
            needs_approval = path == project_path
            groups.append(
                [
                    self._item(agent, name, entry, scope, path, ok, needs_approval)
                    for name, entry in (servers.items() if isinstance(servers, dict) else ())
                ]
            )
        _, _, user, local, project = groups
        mark_shadowed([*local, *project, *user])
        return [item for group in groups for item in group]

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
            findings=[INVALID_SERVER] if status is Status.BROKEN else [],
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


def _quiet_json(path: Path) -> dict:
    """Read optional metadata, where a bad file just means less detail."""
    return read_json(path, [])[0]


def _managed_dir(context: Context) -> Path:
    if sys.platform == "darwin":
        return context.system_root / "Library" / "Application Support" / "ClaudeCode"
    return context.system_root / "etc" / "claude-code"


def _description(found: FoundSkill) -> str:
    return (found.fields or {}).get("description") or first_line(found.body)


def _command_name(path: Path, root: Path) -> str:
    """Folders under `commands` become part of the name, joined by `:`."""
    relative = path.relative_to(root)
    return ":".join((*relative.parent.parts, relative.stem))


def _is_agent_name(name: str) -> bool:
    return not name.startswith("-") and ":" not in name and len(name) <= _MAX_AGENT_NAME


def _one_per_name(items: list[Item]) -> list[Item]:
    return list({item.name: item for item in reversed(items)}.values())


def _replacing_paths(declared: object, default: str) -> list[str]:
    """A manifest's paths replace a plugin's default directory."""
    return (
        [path for path in listed_paths(declared) if is_inside_plugin(path)]
        if declared is not None
        else [default]
    )


def _claude_hook(
    agent: str, handler, scope: Scope, status: Status, path: Path, native_kind: str = "hook"
) -> Item:
    # An event this list doesn't know may be a newer one, or may do nothing.
    if handler.event not in _HOOK_EVENTS and status is Status.ENABLED:
        status = Status.UNKNOWN
    return hook_item(agent, handler, scope, status, path, native_kind)


def _is_loadable_name(name: str) -> bool:
    """Claude Code refuses these names, per the skills documentation."""
    if name == "anthropic-skills" or name.startswith("anthropic-skills:"):
        return False
    return not any(char in "<>" or unicodedata.category(char) in ("Cc", "Cf") for char in name)
