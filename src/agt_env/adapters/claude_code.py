"""Claude Code adapter.

Discovery rules, official references, and undocumented findings are in
docs/agents/claude-code.md. Update that file when these rules change. tests/test_claude_oracle.py
compares results with the real agent.
"""

import json
import os
import re
import sys
import unicodedata
from collections.abc import Iterable
from pathlib import Path
from urllib.parse import urlsplit

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
from agt_env.frontmatter import parse_frontmatter
from agt_env.hooks import INVALID_HOOK, handler_details, handlers, hook_item
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
_DENY_RULE = re.compile(r"^(Skill|Agent)\((.+)\)$")
_REMOTE_TRANSPORTS = {"http", "streamable-http", "sse", "ws"}
_MANAGED_TRANSPORTS = {"http", "streamable-http", "sse"}
_MANAGED_FORBIDDEN_KEYS = ("command", "args", "env", "headersHelper")
_CONTROL_CATEGORIES = ("Cc", "Cf")
_RESERVED_SKILL_NAME = "anthropic-skills"
_GUARD_EVENTS = ("PreToolUse", "PermissionRequest")
_SKILLS_DIR_MARKETPLACE = "skills-dir"
_SKILL_IN_COMMANDS = "skill.md"
_PLUGIN_SCOPES = {"user": Scope.USER, "project": Scope.PROJECT, "local": Scope.PROJECT}
_PLUGIN_MANIFEST = Path(".claude-plugin") / "plugin.json"
_MAX_AGENT_NAME = 256
_NEEDS_NAME_AND_DESCRIPTION = "needs a name and a description, which Claude Code requires"
_REFUSED_NAME = "Claude Code refuses this name"
_REFUSED_AGENT_NAME = "Claude Code refuses this name: it starts with -, has a colon, or is too long"
_REJECTED_FILE = Finding(
    "not loaded: this file has an invalid PreToolUse or PermissionRequest hook"
)
_INVALID_MANAGED_SERVER = Finding(
    "invalid in managed settings, which allow only https servers with literal values"
)
_INVALID_COMMAND_ENTRY = Finding(
    "not loaded: an entry in the manifest's commands is invalid, so Claude Code ignores them all"
)
_CONDITIONAL_SKILL = Finding("loads only when a file matching its paths is used")
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
        # Setting the variable moves this file, even if it names the default directory.
        state_dir = user_dir if context.env.get("CLAUDE_CONFIG_DIR") else context.home
        user_state_path = state_dir / ".claude.json"
        project_dir = context.cwd / ".claude"
        project_mcp_path = context.cwd / ".mcp.json"
        managed_dir = _managed_dir(context)
        # In the home directory, `.claude` is the user's own directory, and it's read only once.
        project_is_user = os.path.realpath(project_dir) == os.path.realpath(user_dir)

        config_path = first_existing(user_dir, user_state_path, project_dir, project_mcp_path)
        report = AgentReport(
            agent=self.id,
            installed=context.on_path("claude"),
            config_path=config_path,
            override_var=override if config_path in (user_dir, user_state_path) else None,
        )

        state, state_ok = read_json(user_state_path, report.warnings)
        settings = _Settings(
            user=user_dir / "settings.json",
            project=None if project_is_user else project_dir / "settings.json",
            local=project_dir / "settings.local.json",
            managed=managed_dir / "managed-settings.json",
            warnings=report.warnings,
            state_ok=state_ok,
        )
        projects = state.get("projects")
        project_key = _project_key(context.cwd)
        project_state = projects.get(project_key) if isinstance(projects, dict) else None
        if not isinstance(project_state, dict):
            project_state = {}
        trusted = project_state.get("hasTrustDialogAccepted") is True or os.path.realpath(
            context.cwd
        ) in (os.path.realpath(context.home), os.path.realpath(user_dir))

        skills_dir_roots = [(Scope.USER, user_dir / "skills")]
        if not project_is_user:
            skills_dir_roots.append((Scope.PROJECT, project_dir / "skills"))
        skills_dir_plugins = _skills_dir_plugins(
            skills_dir_roots, trusted, settings, report.warnings
        )
        plugin_folders = {plugin.source_path for plugin in skills_dir_plugins}

        for scope, root in unique_roots(
            [
                (Scope.SYSTEM, managed_dir / ".claude" / "skills"),
                (Scope.USER, user_dir / "skills"),
                (Scope.PROJECT, project_dir / "skills"),
            ]
        ):
            skip = {"synced"} | {path.name for path in plugin_folders if path.parent == root}
            found, warnings = scan_skills(root, skip=frozenset(skip), include_hidden=True)
            report.warnings.extend(warnings)
            report.items.extend(self._skill(skill, scope, settings) for skill in found)
        report.items = load_once(report.items)
        # Roots are in precedence order: enterprise, personal, project.
        mark_shadowed(report.items)
        skills = list(report.items)

        mcp = _McpRules(settings, project_state, trusted, state_ok, managed_dir, report.warnings)
        report.items.extend(mcp.items(self.id, state, user_state_path, project_mcp_path))
        plugins = [
            *self._plugins(user_dir, context, project_key, settings, report.warnings),
            *skills_dir_plugins,
        ]
        report.items.extend(plugins)
        # Tested: a plugin installed in two scopes loads once.
        loaded_plugins = _first_per_name(plugin for plugin in plugins if loads_skills(plugin))
        report.items.extend(
            load_once(
                [
                    skill
                    for plugin in loaded_plugins
                    for skill in self._plugin_skills(plugin, settings, report.warnings)
                ]
            )
        )
        report.items.extend(mcp.plugin_items(self.id, loaded_plugins, report.warnings))

        # A skill beats a same-named command, and otherwise the order is as for skills.
        command_roots = [
            (Scope.SYSTEM, managed_dir / ".claude" / "commands"),
            (Scope.USER, user_dir / "commands"),
        ]
        if not project_is_user:
            command_roots.append((Scope.PROJECT, project_dir / "commands"))
        commands = [
            self._command(found, root, scope, settings)
            for scope, root in command_roots
            for found in _command_files(root, report.warnings)
        ]
        mark_shadowed([*skills, *commands])
        report.items.extend(commands)

        # Highest precedence first: managed, project, user. Plugin agents carry a prefix.
        agent_roots = [(Scope.SYSTEM, managed_dir / ".claude" / "agents")]
        if not project_is_user:
            agent_roots.append((Scope.PROJECT, project_dir / "agents"))
        agent_roots.append((Scope.USER, user_dir / "agents"))
        agents_by_source = [
            [self._agent(found, scope, settings) for found in find_markdown(root, report.warnings)]
            for scope, root in agent_roots
        ]
        # Which of two same-named files in one directory loads isn't defined, so neither is flagged.
        mark_shadowed(item for source in agents_by_source for item in _one_per_name(source))
        report.items.extend(item for source in agents_by_source for item in source)

        for data, scope, path in settings.hook_sources():
            status = settings.hook_status(scope, trusted)
            rejected = path in settings.rejected
            report.items.extend(
                _claude_hook(
                    self.id,
                    handler,
                    scope,
                    Status.DISABLED if rejected else status,
                    path,
                    settings,
                    rejected=rejected,
                )
                for handler in handlers(data.get("hooks"))
            )
        for plugin in loaded_plugins:
            report.items.extend(self._plugin_extras(plugin, settings, trusted, report.warnings))
        return report

    def _command(self, found: FoundSkill, root: Path, scope: Scope, settings: "_Settings") -> Item:
        name = _command_name(found.path, root)
        status = Status.DISABLED if settings.blocks("skills") else settings.skill_status(name)
        return definition_item(
            self.id,
            found,
            Kind.COMMAND,
            "command",
            scope,
            Status.DISABLED if _nobody_can_invoke(found) else status,
            name=name,
            description=_description(found),
        )

    def _agent(self, found: FoundSkill, scope: Scope, settings: "_Settings") -> Item:
        fields = found.fields or {}
        name, description = fields.get("name"), fields.get("description")
        complete = bool(name and description)
        if not complete or not _is_agent_name(name):
            status = Status.BROKEN
        elif settings.restricts("agents", scope):
            status = Status.DISABLED
        else:
            status = settings.agent_status(name)
        return definition_item(
            self.id,
            found,
            Kind.SUBAGENT,
            "agent",
            scope,
            status,
            name=name or found.path.stem,
            description=description or "",
            problem=_REFUSED_AGENT_NAME if complete else _NEEDS_NAME_AND_DESCRIPTION,
        )

    def _plugin_extras(
        self, plugin: Item, settings: "_Settings", trusted: bool, warnings: list[str]
    ) -> list[Item]:
        """Return a loaded plugin's commands, agents, and hooks."""
        root = plugin.source_path
        prefix = plugin.name.partition("@")[0]
        manifest = _quiet_json(root / _PLUGIN_MANIFEST)
        items: list[Item] = []

        declared = manifest.get("commands")
        if isinstance(declared, dict):
            items.extend(self._declared_commands(declared, plugin, prefix, settings, warnings))
            # Tested: when an entry is invalid, the default directory is used instead.
            if not _valid_command_entries(declared):
                declared = None
        if not isinstance(declared, dict):
            for path in _replacing_paths(declared, "commands"):
                base = root / path
                if os.path.isfile(base):
                    items.append(
                        self._plugin_command(
                            read_markdown(base, warnings), plugin, f"{prefix}:{base.stem}", settings
                        )
                    )
                    continue
                for found in _command_files(base, warnings):
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
        # Tested: a file named twice, or also the default one, loads once.
        sources = [(read_json(path, warnings)[0], path) for path in _once_each(hook_files)]
        sources += [(entry, root / _PLUGIN_MANIFEST) for entry in inline]
        for data, path in sources:
            rejected = _has_unloadable_guard(data.get("hooks"))
            for handler in handlers(data.get("hooks")):
                status = settings.hook_status(
                    plugin.scope, trusted or plugin.scope is not Scope.PROJECT, plugin=plugin.name
                )
                item = _claude_hook(
                    self.id,
                    handler,
                    plugin.scope,
                    Status.DISABLED if rejected else status,
                    path,
                    settings,
                    "plugin hook",
                    rejected=rejected,
                )
                items.append(mark_bundled(item, plugin, item.name, "plugin hook"))
        return items

    def _declared_commands(
        self, declared: dict, plugin: Item, prefix: str, settings: "_Settings", warnings: list[str]
    ) -> list[Item]:
        """Read the object form of a manifest's commands."""
        manifest_path = plugin.source_path / _PLUGIN_MANIFEST
        valid = _valid_command_entries(declared)
        items = []
        for name, entry in declared.items():
            source = entry.get("source") if isinstance(entry, dict) else None
            if valid and isinstance(source, str):
                found = read_markdown(plugin.source_path / source, warnings)
            else:
                found = FoundSkill(
                    manifest_path, manifest_path, Path(os.path.realpath(manifest_path))
                )
                if valid:
                    found.fields, found.body = parse_frontmatter(entry["content"])
            item = self._plugin_command(found, plugin, f"{prefix}:{name}", settings)
            if not valid:
                item.status = Status.BROKEN
                item.findings = [_INVALID_COMMAND_ENTRY]
            elif isinstance(entry.get("description"), str):
                item.description = entry["description"]
            items.append(item)
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
            Status.DISABLED if _nobody_can_invoke(found) else settings.plugin_skill_status(name),
            name=name,
            description=_description(found),
        )
        prefix = f"{plugin.name.partition('@')[0]}:"
        return mark_bundled(item, plugin, name, "plugin command", prefix=prefix)

    def _skill(self, skill: FoundSkill, scope: Scope, settings: "_Settings") -> Item:
        fields = skill.fields or {}
        # Tested: a skill's identity is its folder. `name` changes only how it's displayed.
        name = skill.path.name
        display_name = fields.get("name", "")
        description = fields.get("description") or first_line(skill.body)
        if skill.broken or not _is_loadable_name(name, display_name):
            status = Status.BROKEN
        elif (
            _nobody_can_invoke(skill)
            or settings.restricts("skills", scope)
            or (display_name and settings.is_skill_denied(display_name))
        ):
            status = Status.DISABLED
        else:
            status = settings.skill_status(name)
        item = Item(
            name=name,
            kind=Kind.SKILL,
            native_kind="skill",
            agent=self.id,
            scope=scope,
            status=status,
            description=description,
            source_path=skill.path,
            resolved_path=skill.resolved_path,
            details={"display_name": display_name} if display_name != name else {},
        )
        if status is Status.BROKEN:
            item.findings.append(Finding(skill.problem or _REFUSED_NAME))
        elif status is Status.ENABLED and _is_conditional(fields.get("paths", "")):
            item.findings.append(_CONDITIONAL_SKILL)
        return item

    def _plugins(
        self,
        user_dir: Path,
        context: Context,
        project_key: str,
        settings: "_Settings",
        warnings: list[str],
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
                # Tested: inside a repository, its root also names the project.
                project_paths = (project_key, os.path.realpath(context.cwd))
                if scope is Scope.PROJECT and install.get("projectPath") not in project_paths:
                    continue
                items.append(
                    self._plugin(plugin_id, install, scope, record_path, settings, warnings)
                )
        return items

    def _plugin(
        self,
        plugin_id: str,
        install: dict,
        scope: Scope,
        record_path: Path,
        settings: "_Settings",
        warnings: list[str],
    ) -> Item:
        install_path = install.get("installPath")
        path = Path(install_path) if isinstance(install_path, str) else record_path
        exists = isinstance(install_path, str) and os.path.isdir(path)
        # A bad manifest doesn't stop a plugin loading, but it does lose what the manifest says.
        manifest = read_json(path / _PLUGIN_MANIFEST, warnings)[0] if exists else {}
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
        # (skill, whether a path names it directly rather than a directory holding it)
        found: list[tuple[FoundSkill, bool]] = []
        if not added and not os.path.isdir(root / "skills"):
            if os.path.lexists(root / SKILL_FILE):
                found = [(read_skill(root, warnings), True)]
        else:
            for directory in _once_each([root / path for path in ("skills", *added)]):
                direct = os.path.lexists(directory / SKILL_FILE)
                skills = skills_at(directory, warnings, include_hidden=True)
                found.extend((skill, direct) for skill in skills)

        items = []
        for skill, direct in found:
            fields = skill.fields or {}
            is_root = skill.path == root
            if plugin.name.endswith(f"@{_SKILLS_DIR_MARKETPLACE}") and is_root:
                # Tested: it's named after the plugin alone, whatever `name` says.
                full_name, shown_prefix = prefix, ""
            else:
                leaf = _plugin_skill_leaf(fields.get("name", ""), prefix, skill, is_root, direct)
                full_name, shown_prefix = f"{prefix}:{leaf}", f"{prefix}:"
            if skill.broken:
                status = Status.BROKEN
            elif _nobody_can_invoke(skill):
                status = Status.DISABLED
            else:
                status = settings.plugin_skill_status(full_name)
            item = skill_item(self.id, skill, plugin.scope, status)
            item.description = fields.get("description") or first_line(skill.body)
            items.append(mark_bundled(item, plugin, full_name, prefix=shown_prefix))
        return items


class _Settings:
    """Settings layers. Merged views give later layers precedence."""

    def __init__(
        self,
        *,
        user: Path,
        project: Path | None,
        local: Path,
        managed: Path,
        warnings: list[str],
        state_ok: bool = True,
    ) -> None:
        self.reliable = True
        self.state_ok = state_ok
        self.warnings = warnings
        self.rejected: set[Path] = set()
        self.user_path, self.project_path, self.local_path = user, project, local
        self.user = self._load_rejectable(user)
        self.project = self._load_rejectable(project) if project else {}
        self.local = self._load_rejectable(local)
        self.managed_files = [(managed, self._load(managed))]
        for path in _drop_ins(managed.with_name("managed-settings.d"), warnings):
            self.managed_files.append((path, self._load(path)))
        self.managed: dict = {}
        for _, data in self.managed_files:
            _merge_managed(self.managed, data)
        self.layers = (self.user, self.project, self.local, self.managed)

    def _load(self, path: Path) -> dict:
        data, ok = read_json(path, self.warnings)
        self.reliable = self.reliable and ok
        return data

    def _load_rejectable(self, path: Path) -> dict:
        """Tested: an invalid guard hook costs the whole file, so only its hooks are kept to show."""
        data = self._load(path)
        if not _has_unloadable_guard(data.get("hooks")):
            return data
        self.rejected.add(path)
        self.warnings.append(
            f"Claude Code won't load {path}: it has an invalid PreToolUse or PermissionRequest hook"
        )
        return {"hooks": data["hooks"]}

    def hook_sources(self) -> list[tuple[dict, Scope, Path]]:
        return [
            *((data, Scope.SYSTEM, path) for path, data in self.managed_files),
            (self.user, Scope.USER, self.user_path),
            (self.project, Scope.PROJECT, self.project_path or self.user_path),
            (self.local, Scope.PROJECT, self.local_path),
        ]

    def blocks(self, surface: str) -> bool:
        """Whether managed settings let only plugins and managed files supply this surface."""
        strict = self.managed.get("strictPluginOnlyCustomization")
        return strict is True or (isinstance(strict, list) and surface in strict)

    def restricts(self, surface: str, scope: Scope) -> bool:
        return scope is not Scope.SYSTEM and self.reliable and self.blocks(surface)

    def blocks_http_hook(self, url: str) -> bool:
        lists = [layer["allowedHttpHookUrls"] for layer in self.layers if _is_list_at(layer)]
        if not lists:
            return False
        patterns = [pattern for entries in lists for pattern in entries]
        return not any(isinstance(p, str) and _url_matches(p, url) for p in patterns)

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
            if match and match[1] == tool and _rule_names(match[2], name, tool):
                return True
        return False

    def is_skill_denied(self, name: str) -> bool:
        return self.reliable and self._is_denied(name)

    def agent_status(self, name: str) -> Status:
        if not self.reliable:
            return Status.UNKNOWN
        return Status.DISABLED if self._is_denied(name, "Agent") else Status.ENABLED

    def hook_status(self, scope: Scope, trusted: bool, *, plugin: str | None = None) -> Status:
        if not self.reliable:
            return Status.UNKNOWN
        if self.managed.get("disableAllHooks") is True:
            return Status.DISABLED
        if scope is Scope.SYSTEM:
            return Status.ENABLED
        if plugin is None and self.blocks("hooks"):
            return Status.DISABLED
        # The last layer that sets this decides, so a project can undo a user's setting.
        disabled = False
        for layer in (self.user, self.project, self.local):
            if isinstance(layer.get("disableAllHooks"), bool):
                disabled = layer["disableAllHooks"]
        if disabled or self._only_managed_hooks_run(plugin):
            return Status.DISABLED
        if trusted or scope is Scope.USER:
            return Status.ENABLED
        return Status.PENDING if self.state_ok else Status.UNKNOWN

    def _only_managed_hooks_run(self, plugin: str | None) -> bool:
        if self.managed.get("allowManagedHooksOnly") is not True:
            return False
        enabled_by_managed = self.managed.get("enabledPlugins")
        return not (isinstance(enabled_by_managed, dict) and enabled_by_managed.get(plugin) is True)

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

    def skills_dir_plugin_status(self, plugin_id: str, manifest: dict) -> Status:
        """Tested: unlike an installed plugin, one in a skills directory is on by default."""
        if not self.reliable:
            return Status.UNKNOWN
        if self.blocks("skills"):
            return Status.DISABLED
        chosen = self._merged_dict("enabledPlugins").get(plugin_id)
        if isinstance(chosen, bool):
            return Status.ENABLED if chosen else Status.DISABLED
        return Status.DISABLED if manifest.get("defaultEnabled") is False else Status.ENABLED


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
        # Tested: when this file exists, Claude Code loads only managed servers, even if it can't
        # read it. So what it blocks is known, and the others are disabled rather than unknown.
        self.exclusive = os.path.lexists(self.managed_path)
        if self.exclusive and not os.path.isfile(self.managed_path):
            warnings.append(f"cannot read {self.managed_path}: not a regular file")
        # What a server runs or connects to, which hides a plugin's server doing the same.
        self.manual: list[tuple[Item, str]] = []

    def items(self, agent: str, state: dict, user_path: Path, project_path: Path) -> list[Item]:
        managed_file, managed_ok = read_json(self.managed_path, self.warnings)
        project_file, project_ok = read_json(project_path, self.warnings)
        settings_path = self.managed_path.with_name("managed-settings.json")
        sources: list[tuple[object, Scope, Path, bool]] = [
            (managed_file.get("mcpServers"), Scope.SYSTEM, self.managed_path, managed_ok),
            (self.settings.managed.get("managedMcpServers"), Scope.SYSTEM, settings_path, True),
            (state.get("mcpServers"), Scope.USER, user_path, self.state_ok),
            (self.project_state.get("mcpServers"), Scope.PROJECT, user_path, self.state_ok),
            (project_file.get("mcpServers"), Scope.PROJECT, project_path, project_ok),
        ]
        groups = []
        for servers, scope, path, ok in sources:
            needs_approval = path == project_path
            group = []
            for name, entry in servers.items() if isinstance(servers, dict) else ():
                item = self._item(agent, name, entry, scope, path, ok, needs_approval)
                group.append(item)
                self.manual.append((item, _server_identity(entry)))
            groups.append(group)
        managed, delivered, user, local, project = groups
        mark_shadowed([*managed, *delivered, *local, *project, *user])
        return [item for group in groups for item in group]

    def plugin_items(self, agent: str, plugins: list[Item], warnings: list[str]) -> list[Item]:
        """Return plugins' servers, which load unless an enabled server does the same thing."""
        taken = {identity: item for item, identity in self.manual if item.status is Status.ENABLED}
        items = []
        for plugin in plugins:
            prefix = f"plugin:{plugin.name.partition('@')[0]}:"
            for name, entry, path, ok in _plugin_servers(plugin.source_path, warnings):
                item = self._item(
                    agent, f"{prefix}{name}", entry, plugin.scope, path, ok, False, from_plugin=True
                )
                mark_bundled(item, plugin, item.name, "plugin mcp server", prefix=prefix)
                identity = _server_identity(entry)
                winner = taken.get(identity)
                if item.status is Status.ENABLED and winner is not None:
                    item.status = Status.SHADOWED
                    item.findings.append(Finding("shadowed by", winner.source_path))
                elif item.status is Status.ENABLED:
                    taken[identity] = item
                items.append(item)
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
        *,
        from_plugin: bool = False,
    ) -> Item:
        is_delivered = scope is Scope.SYSTEM and path.name == "managed-settings.json"
        details = _managed_server_details(entry) if is_delivered else _server_details(entry)
        managed_alike = scope is Scope.SYSTEM
        # Managed servers don't depend on the user's state file.
        readable = source_ok and self.settings.reliable and (managed_alike or self.state_ok)
        if details is None:
            status, details = Status.BROKEN, {}
        elif self.exclusive and not managed_alike:
            status = Status.DISABLED
        elif not readable:
            status = Status.UNKNOWN
        elif (self.settings.blocks("mcp") and not managed_alike and not from_plugin) or (
            self._policy_blocks(name, entry, managed_alike)
        ):
            status = Status.DISABLED
        elif needs_approval:
            status = self._approval(name)
        elif self._in_list(self.project_state, "disabledMcpServers", name):
            status = Status.DISABLED
        else:
            status = Status.ENABLED
        invalid = _INVALID_MANAGED_SERVER if is_delivered else INVALID_SERVER
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
            findings=[invalid] if status is Status.BROKEN else [],
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

    def _policy_blocks(self, name: str, entry: object, managed: bool) -> bool:
        settings = self.settings
        denied = [rule for layer in settings.layers for rule in _rules(layer, "deniedMcpServers")]
        if any(_rule_matches(rule, name, entry) for rule in denied):
            return True
        # The allowlist is for servers users add. Managed ones skip it, unless they expand a
        # variable outside `env`.
        if managed and not _expands_variable(entry):
            return False
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
    args = entry.get("args", [])
    if (
        isinstance(command, list)
        and isinstance(args, list)
        and command == [entry.get("command"), *args]
    ):
        return True
    pattern, url = rule.get("serverUrl"), entry.get("url")
    return isinstance(pattern, str) and isinstance(url, str) and _url_matches(pattern, url)


def _expands_variable(entry: object) -> bool:
    fields = entry.items() if isinstance(entry, dict) else ()
    return "${" in json.dumps({key: value for key, value in fields if key != "env"})


def _url_matches(pattern: str, url: str) -> bool:
    """`*` is the only wildcard in these patterns."""
    parts = (re.escape(part) for part in pattern.split("*"))
    return re.fullmatch(".*".join(parts), url) is not None


def _is_list_at(layer: dict) -> bool:
    return isinstance(layer.get("allowedHttpHookUrls"), list)


def _rule_names(rule_name: str, name: str, tool: str) -> bool:
    """Whether a deny rule's argument covers this item. Only skill rules have prefixes."""
    if rule_name in ("*", name):
        return True
    if tool != "Skill":
        return False
    rule_name = rule_name.removeprefix("/")
    if rule_name.endswith((":*", " *")):
        return name.startswith(rule_name[:-2])
    return rule_name == name


def _is_string_map(value: object) -> bool:
    return isinstance(value, dict) and all(isinstance(entry, str) for entry in value.values())


def _server_details(entry: object) -> dict[str, object] | None:
    """Return a safe summary, or None if Claude Code would skip this entry or can't connect."""
    if not isinstance(entry, dict):
        return None
    transport = entry.get("type")
    if transport in (None, "stdio"):
        # An entry with a url but no type is a configuration error, per the docs.
        if not isinstance(entry.get("command"), str) or "url" in entry:
            return None
        args = entry.get("args", [])
        if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
            return None
        if "env" in entry and not _is_string_map(entry["env"]):
            return None
        return stdio_details(entry["command"], entry.get("env"))
    if "headers" in entry and not _is_string_map(entry["headers"]):
        return None
    url = entry.get("url")
    if transport in _REMOTE_TRANSPORTS and isinstance(url, str) and _can_connect(transport, url):
        return remote_details(transport, url, entry.get("headers"))
    return None


def _can_connect(transport: str, url: str) -> bool:
    """Tested: Claude Code lists a server with a bad URL, but it always fails to connect."""
    if "${" in url:
        return True  # Only known once the variable is expanded.
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    if transport == "ws":
        return bool(parts.scheme and parts.netloc)
    return parts.scheme in ("http", "https") and bool(parts.netloc)


def _managed_server_details(entry: object) -> dict[str, object] | None:
    """`managedMcpServers` is stricter than other server lists. See the agent's doc page."""
    if not isinstance(entry, dict) or entry.get("type") not in _MANAGED_TRANSPORTS:
        return None
    if any(key in entry for key in _MANAGED_FORBIDDEN_KEYS):
        return None
    url = entry.get("url")
    if not isinstance(url, str):
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme != "https" or not parts.hostname:
        return None
    if any(_has_control(text) or "${" in text for text in _strings_in(entry)):
        return None
    return _server_details(entry)


def _strings_in(value: object) -> list[str]:
    """Every string in a parsed JSON value, keys included."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for key, item in value.items() for text in (*_strings_in(item), key)]
    if isinstance(value, list):
        return [text for item in value for text in _strings_in(item)]
    return []


def _has_control(text: str) -> bool:
    return any(unicodedata.category(char) in _CONTROL_CATEGORIES for char in text)


def _server_identity(entry: object) -> str:
    """Tested: Claude Code compares a command with its arguments, or a URL. The name doesn't count."""
    if not isinstance(entry, dict):
        return ""
    if isinstance(entry.get("url"), str):
        return f"url:{entry['url']}"
    return f"command:{json.dumps([entry.get('command'), *_list_or_empty(entry.get('args'))])}"


def _list_or_empty(value: object) -> list:
    return value if isinstance(value, list) else []


def _plugin_servers(root: Path, warnings: list[str]) -> list[tuple[str, object, Path, bool]]:
    """Read a plugin's servers: `.mcp.json`, then what its manifest's `mcpServers` adds."""
    manifest_path = root / _PLUGIN_MANIFEST
    declared = _quiet_json(manifest_path).get("mcpServers")
    sources: list[Path | dict] = [root / ".mcp.json"]
    for entry in declared if isinstance(declared, list) else [declared]:
        if isinstance(entry, dict):
            sources.append(entry)
        elif isinstance(entry, str) and entry.endswith(".json") and is_inside_plugin(entry):
            sources.append(root / entry)
    found = []
    for source in sources:
        if isinstance(source, dict):
            data, path, ok = source, manifest_path, True
        else:
            data, ok = read_json(source, warnings)
            path = source
        # Tested: a file may hold the servers directly, without the `mcpServers` wrapper.
        servers = data.get("mcpServers", data)
        for name, entry in servers.items() if isinstance(servers, dict) else ():
            found.append((name, entry, path, ok))
    return found


def _project_key(cwd: Path) -> str:
    """Tested: `.claude.json` keeps a project under its repository's root, or else the directory.

    This reads only that one entry. Nothing else about parent directories is used.
    """
    real = Path(os.path.realpath(cwd))
    for directory in (real, *real.parents):
        if os.path.lexists(directory / ".git"):
            return str(directory)
    return str(real)


def _drop_ins(directory: Path, warnings: list[str]) -> list[Path]:
    """Managed settings files that add to `managed-settings.json`, in the order they apply."""
    try:
        names = sorted(os.listdir(directory))
    except (FileNotFoundError, NotADirectoryError):
        return []
    except OSError as error:
        warnings.append(f"cannot read {directory}: {error.strerror}")
        return []
    return [directory / name for name in names if name.endswith(".json") and name[0] != "."]


def _merge_managed(merged: dict, data: dict) -> None:
    """Later files win for single values. Lists join, and objects merge."""
    for key, value in data.items():
        current = merged.get(key)
        if isinstance(current, list) and isinstance(value, list):
            merged[key] = [*current, *value]
        elif isinstance(current, dict) and isinstance(value, dict):
            _merge_managed(current, value)
        else:
            merged[key] = value


def _skills_dir_plugins(
    roots: list[tuple[Scope, Path]], trusted: bool, settings: _Settings, warnings: list[str]
) -> list[Item]:
    """Return plugins kept in skill directories: a folder with a manifest that names it."""
    items = []
    for scope, root in roots:
        try:
            names = sorted(os.listdir(root))
        except OSError:
            continue  # The skill scan reports an unreadable directory.
        for folder in names:
            path = root / folder
            manifest, _ = read_json(path / _PLUGIN_MANIFEST, warnings)
            name = manifest.get("name")
            if not isinstance(name, str) or not name:
                continue
            plugin_id = f"{name}@{_SKILLS_DIR_MARKETPLACE}"
            status = settings.skills_dir_plugin_status(plugin_id, manifest)
            # Tested: a project's only load once its folder is trusted.
            if status is Status.ENABLED and scope is Scope.PROJECT and not trusted:
                status = Status.PENDING if settings.state_ok else Status.UNKNOWN
            description = manifest.get("description")
            items.append(
                Item(
                    name=plugin_id,
                    name_suffix=marketplace_suffix(plugin_id),
                    kind=Kind.PLUGIN,
                    native_kind="skills directory plugin",
                    agent=ClaudeCode.id,
                    scope=scope,
                    status=status,
                    description=description if isinstance(description, str) else "",
                    source_path=path,
                    resolved_path=Path(os.path.realpath(path)),
                )
            )
    return items


def _has_unloadable_guard(hooks: object) -> bool:
    """Tested: an invalid entry under these events costs the whole file, not just the entry."""
    if not isinstance(hooks, dict):
        return False
    return any(event in hooks and not _is_valid_guard(hooks[event]) for event in _GUARD_EVENTS)


def _is_valid_guard(groups: object) -> bool:
    if not isinstance(groups, list):
        return False
    for group in groups:
        if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
            return False
        if "matcher" in group and not isinstance(group["matcher"], str):
            return False
        if not all(_is_valid_handler(entry) for entry in group["hooks"]):
            return False
    return True


def _is_valid_handler(entry: object) -> bool:
    if handler_details(entry) is None:
        return False
    url = entry.get("url") if entry.get("type") == "http" else None
    return url is None or _is_valid_hook_url(url)


def _is_valid_hook_url(url: str) -> bool:
    """Claude Code checks the form of these URLs when it loads settings."""
    if "${" in url:
        return True
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return bool(parts.netloc) if parts.scheme in ("http", "https") else bool(parts.scheme)


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
    if path.name.lower() == _SKILL_IN_COMMANDS:
        # Tested: it's named after its folder, even when that's the `commands` folder itself.
        return ":".join(relative.parent.parts) or root.name
    return ":".join((*relative.parent.parts, relative.stem))


def _command_files(root: Path, warnings: list[str]) -> list[FoundSkill]:
    """Tested: in a folder with a `SKILL.md`, in any case, the other Markdown files don't count."""
    found = find_markdown(root, warnings)
    with_skill = {
        file.path.parent for file in found if file.path.name.lower() == _SKILL_IN_COMMANDS
    }
    return [
        file
        for file in found
        if file.path.parent not in with_skill or file.path.name.lower() == _SKILL_IN_COMMANDS
    ]


def _valid_command_entries(declared: dict) -> bool:
    """Tested: each needs one of `source`, a `./` path to Markdown, or `content`, but not both."""
    for entry in declared.values():
        if not isinstance(entry, dict) or ("source" in entry) == ("content" in entry):
            return False
        source = entry.get("source")
        if source is not None and not (
            isinstance(source, str) and source.endswith(".md") and is_inside_plugin(source)
        ):
            return False
        if "content" in entry and not isinstance(entry["content"], str):
            return False
    return True


def _once_each(paths: list[Path]) -> list[Path]:
    """Drop paths that are the same file or directory as an earlier one."""
    return list({os.path.realpath(path): path for path in reversed(paths)}.values())[::-1]


def _first_per_name(items: Iterable[Item]) -> list[Item]:
    first: dict[str, Item] = {}
    for item in items:
        first.setdefault(item.name, item)
    return list(first.values())


def _is_true(value: str | None) -> bool:
    return (value or "").strip().lower() == "true"


def _is_false(value: str | None) -> bool:
    return (value or "").strip().lower() == "false"


def _nobody_can_invoke(found: FoundSkill) -> bool:
    """With both set, neither the user nor the model can use it, and the listing drops it."""
    fields = found.fields or {}
    return _is_true(fields.get("disable-model-invocation")) and _is_false(
        fields.get("user-invocable")
    )


def _is_conditional(paths: str) -> bool:
    """Whether `paths` limits a skill. Tested: `**` alone doesn't."""
    globs = {glob.strip("'\" ") for glob in re.split(r"[,\n]|\s-\s|^-\s", paths)}
    return bool(globs - {"", "**"})


def _plugin_skill_leaf(
    name: str, prefix: str, skill: FoundSkill, is_root: bool, direct: bool
) -> str:
    """Tested: the last part of a plugin skill's name, with other characters replaced by `-`.

    In a directory of skills it comes from the folder. A skill named directly by the plugin's root
    or a manifest path uses `name` if it has one.
    """
    leaf = skill.path.name
    if direct:
        stated = name.strip().removeprefix(f"{prefix}:")
        leaf = stated or (prefix if is_root else skill.path.name)
    return re.sub(r"[^a-zA-Z0-9_-]", "-", leaf)


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
    agent: str,
    handler,
    scope: Scope,
    status: Status,
    path: Path,
    settings: _Settings,
    native_kind: str = "hook",
    *,
    rejected: bool = False,
) -> Item:
    entry = handler.entry
    url = entry.get("url") if isinstance(entry, dict) and entry.get("type") == "http" else None
    if isinstance(url, str) and settings.blocks_http_hook(url) and status is not Status.UNKNOWN:
        status = Status.DISABLED
    # An event this list doesn't know may be a newer one, or may do nothing.
    if handler.event not in _HOOK_EVENTS and status is Status.ENABLED:
        status = Status.UNKNOWN
    item = hook_item(agent, handler, scope, status, path, native_kind)
    if isinstance(url, str) and not _is_valid_hook_url(url):
        item.status, item.findings = Status.BROKEN, [INVALID_HOOK]
    elif rejected and item.status is not Status.BROKEN:
        item.findings.append(_REJECTED_FILE)
    return item


def _is_loadable_name(folder: str, name: str = "") -> bool:
    """Tested: both a folder and a `name` can be reserved, but only a folder has bad characters."""
    if any(char in "<>" or unicodedata.category(char) in _CONTROL_CATEGORIES for char in folder):
        return False
    return not (_is_reserved_name(folder) or _is_reserved_name(name))


def _is_reserved_name(name: str) -> bool:
    folded = unicodedata.normalize("NFKC", name).casefold()
    return folded == _RESERVED_SKILL_NAME or folded.startswith(f"{_RESERVED_SKILL_NAME}:")
