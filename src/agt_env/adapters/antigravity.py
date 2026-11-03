"""Antigravity CLI adapter. The binary is `agy`.

Discovery rules, official references, and undocumented findings are in
docs/agents/antigravity.md. Update that file when these rules change. tests/test_antigravity_oracle.py
compares results with the real agent.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from agt_env.adapters.base import (
    Context,
    definition_item,
    first_existing,
    list_entries,
    listed_paths,
    load_once,
    loads_skills,
    mark_bundled,
    mark_shadowed,
    mcp_item,
    plugin_item,
    read_json,
    skill_item,
)
from agt_env.hooks import handler_content, handler_details
from agt_env.mcp import remote_details, stdio_details
from agt_env.model import AgentReport, Finding, Item, Kind, Scope, Status
from agt_env.skills import SKILL_FILE, FoundSkill, read_markdown, read_skill, scan_skills

_UNNAMED_SKILL = "SKILL"
_NEEDS_NAME_AND_DESCRIPTION = "needs a name and a description, which Antigravity CLI requires"
# Workspace directories, highest precedence first for skills.
_WORKSPACE_DIRS = (".agent", ".agents", "_agent", "_agents")
_MANIFESTS = ("plugin.json", ".antigravity-plugin/plugin.json", ".gemini-plugin/plugin.json")
# Events that take handlers directly, and those that group them under a matcher.
_FLAT_EVENTS = frozenset({"SessionStart", "PreInvocation", "PostInvocation", "Stop"})
_GROUPED_EVENTS = frozenset({"PreToolUse", "PostToolUse"})
_HANDLER_TYPES = frozenset({"command", "prompt", "http", "mcp_tool", "agent"})
_NO_ACTIONS = "has no handlers Antigravity CLI runs"


@dataclass
class _Root:
    """A customization directory: a workspace one, or the global `~/.gemini/config`."""

    path: Path
    scope: Scope
    status: Status


class Antigravity:
    id = "antigravity"

    def scan(self, context: Context) -> AgentReport:
        gemini_dir = context.home / ".gemini"
        cli_dir = gemini_dir / "antigravity-cli"
        shared_dir = gemini_dir / "config"
        workspace_dirs = [context.cwd / name for name in _WORKSPACE_DIRS]

        report = AgentReport(
            agent=self.id,
            installed=context.on_path("agy"),
            config_path=first_existing(
                cli_dir,
                shared_dir / "mcp_config.json",
                *[directory / "mcp_config.json" for directory in workspace_dirs],
                shared_dir / "skills",
                gemini_dir / "skills",
                shared_dir / "plugins",
                shared_dir / "agents",
                shared_dir / "hooks.json",
                gemini_dir / "agents",
                *[directory / "agents" for directory in workspace_dirs],
                *[directory / "hooks.json" for directory in workspace_dirs],
            ),
        )

        settings, settings_ok = read_json(cli_dir / "settings.json", report.warnings)
        project_status = _workspace_status(settings, settings_ok, context.cwd)
        workspaces = [_Root(path, Scope.PROJECT, project_status) for path in workspace_dirs]
        shared = _Root(shared_dir, Scope.USER, Status.ENABLED)
        user = [
            _Root(cli_dir, Scope.USER, Status.ENABLED),
            shared,
            _Root(gemini_dir, Scope.USER, Status.ENABLED),
        ]

        # Highest precedence first: what a workspace declares, its directories, what the
        # global config declares, then the user directories.
        skill_sources = [
            *[
                (root, declared.items())
                for root in workspaces
                for declared in _declared(root, "skills.json", context, report.warnings)
            ],
            *[(root, _skill_dirs(root.path / "skills", report.warnings)) for root in workspaces],
            *[
                (shared, declared.items())
                for declared in _declared(shared, "skills.json", context, report.warnings)
            ],
            *[(root, _skill_dirs(root.path / "skills", report.warnings)) for root in user],
        ]
        skills: list[Item] = []
        for source, paths in skill_sources:
            for path in paths:
                if os.path.lexists(path / SKILL_FILE):
                    skill = read_skill(path, report.warnings)
                    skills.append(self._skill(skill, source.scope, source.status))
        report.items = load_once(skills)
        mark_shadowed(report.items)

        servers = [
            item
            for root in [*workspaces, shared]
            for item in self._servers(
                root.path / "mcp_config.json", root.scope, root.status, report
            )
        ]
        # The docs say a workspace's customizations override the global ones.
        mark_shadowed(servers)
        report.items.extend(servers)

        state, state_ok = read_json(shared_dir / "config.json", report.warnings)
        states = state.get("plugins")
        states = states if isinstance(states, dict) else {}
        plugin_dirs: list[tuple[_Root, Path, bool]] = []
        for root in [*workspaces, shared]:
            plugin_dirs.extend(
                (root, path, False) for path in list_entries(root.path / "plugins", [])
            )
            for declared in _declared(root, "plugins.json", context, report.warnings):
                plugin_dirs.extend((root, path, True) for path in declared.items())
        plugins = []
        seen_plugins: set[str] = set()
        # A plugin that a JSON config declares gives its skills no prefix.
        unprefixed: set[int] = set()
        for root, path, declared in plugin_dirs:
            real = os.path.realpath(path)
            if real in seen_plugins:
                continue
            item = self._plugin(path, root, states, state_ok, report.warnings)
            if item:
                seen_plugins.add(real)
                plugins.append(item)
                if declared:
                    unprefixed.add(id(item))
        report.items.extend(plugins)
        plugin_hooks: list[Item] = []
        for plugin in plugins:
            if loads_skills(plugin):
                extras = self._plugin_extras(plugin, context, report.warnings)
                plugin_hooks.extend(item for item in extras if item.kind is Kind.HOOK)
                report.items.extend(extras)
        report.items.extend(
            load_once(
                [
                    skill
                    for plugin in plugins
                    if loads_skills(plugin)
                    for skill in self._plugin_skills(
                        plugin, report.warnings, prefixed=id(plugin) not in unprefixed
                    )
                ]
            )
        )

        agent_sources = [
            *[
                (root, declared.items())
                for root in workspaces
                for declared in _declared(root, "agents.json", context, report.warnings)
            ],
            *[
                (root, _hidden_included_entries(root.path / "agents", report.warnings))
                for root in workspaces
            ],
            *[
                (shared, declared.items())
                for declared in _declared(shared, "agents.json", context, report.warnings)
            ],
            *[
                (root, _hidden_included_entries(root.path / "agents", report.warnings))
                for root in user
            ],
        ]
        agents = []
        for source, paths in agent_sources:
            agents.extend(self._agents(paths, source.scope, source.status, report.warnings))
        # The docs say a workspace's agent overrides a global one of the same name.
        mark_shadowed(agents)
        report.items.extend(agents)

        # A later hook of the same name replaces an earlier one, so the order is the reverse.
        hooks = []
        for root in [shared, *reversed(workspaces)]:
            hooks.extend(self._hooks(root.path / "hooks.json", root.scope, root.status, report))
        mark_shadowed([*reversed(plugin_hooks), *hooks])
        report.items.extend(hooks)
        return report

    def _agents(
        self, paths: list[Path], scope: Scope, default: Status, warnings: list[str]
    ) -> list[Item]:
        """An agent is `<name>.md` or `<name>/agent.md`."""
        items = []
        for path in paths:
            file = path / "agent.md" if os.path.isdir(path) else path
            if file.suffix != ".md" or not os.path.lexists(file):
                continue
            found = read_markdown(file, warnings)
            fields = found.fields or {}
            name, description = fields.get("name"), fields.get("description")
            items.append(
                definition_item(
                    self.id,
                    found,
                    Kind.SUBAGENT,
                    "agent",
                    scope,
                    default if name and description else Status.BROKEN,
                    name=name or path.stem,
                    description=description or "",
                    problem=_NEEDS_NAME_AND_DESCRIPTION,
                )
            )
        return items

    def _hooks(self, path: Path, scope: Scope, default: Status, report: AgentReport) -> list[Item]:
        """The top-level keys are hook names, each with its own events."""
        data, ok = read_json(path, report.warnings)
        problem = _hooks_problem(data)
        if problem:
            report.warnings.append(f"cannot read {path}: {problem}")
            return []
        items = []
        for name, entry in data.items():
            fields = entry if isinstance(entry, dict) else {}
            events = {key: value for key, value in fields.items() if key != "enabled"}
            actions = [
                (event, handler)
                for event, handlers in events.items()
                for handler in _actions(event, handlers)
            ]
            summaries = [handler_details(handler) for _, handler in actions]
            valid = bool(actions) and all(summaries)
            status = default if ok else Status.UNKNOWN
            if fields.get("enabled") is False:
                status = Status.DISABLED
            details: dict[str, object] = {"event": list(events)}
            if valid:
                details["command"] = [s["command"] for s in summaries if s.get("command")]
            items.append(
                Item(
                    name=name,
                    kind=Kind.HOOK,
                    native_kind="hook",
                    agent=self.id,
                    scope=scope,
                    status=status if valid else Status.BROKEN,
                    description="; ".join(
                        content for _, handler in actions if (content := handler_content(handler))
                    ),
                    source_path=path,
                    resolved_path=Path(os.path.realpath(path)),
                    details=details,
                    findings=[] if valid else [Finding(_NO_ACTIONS)],
                )
            )
        return items

    def _plugin_extras(self, plugin: Item, context: Context, warnings: list[str]) -> list[Item]:
        root = _Root(plugin.source_path, plugin.scope, plugin.status)
        paths = [
            item
            for declared in _declared(
                root, "agents.json", context, warnings, base=plugin.source_path
            )
            for item in declared.items()
        ]
        paths += _hidden_included_entries(plugin.source_path / "agents", warnings)
        items = self._agents(paths, plugin.scope, plugin.status, warnings)
        report = AgentReport(agent=self.id, installed=False, config_path=None)
        items += self._hooks(plugin.source_path / "hooks.json", plugin.scope, plugin.status, report)
        items += self._servers(
            plugin.source_path / "mcp_config.json", plugin.scope, plugin.status, report
        )
        warnings.extend(report.warnings)
        kinds = {
            Kind.SUBAGENT: "plugin agent",
            Kind.HOOK: "plugin hook",
            Kind.MCP: "plugin mcp server",
        }
        for item in items:
            mark_bundled(item, plugin, item.name, kinds[item.kind])
        return items

    def _plugin_skills(self, plugin: Item, warnings: list[str], *, prefixed: bool) -> list[Item]:
        manifest_path = _manifest(plugin.source_path)
        manifest, _ = read_json(manifest_path, []) if manifest_path else ({}, True)
        items = []
        # A manifest's paths add to the default directory.
        for path in ("skills", *listed_paths(manifest.get("skills"))):
            found, scan_warnings = scan_skills(plugin.source_path / path, include_hidden=True)
            warnings.extend(scan_warnings)
            for skill in found:
                item = self._skill(skill, plugin.scope, plugin.status)
                prefix = f"{plugin.name}:" if prefixed else ""
                items.append(mark_bundled(item, plugin, f"{prefix}{item.name}", prefix=prefix))
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

    def _plugin(
        self, path: Path, root: _Root, states: dict, state_ok: bool, warnings: list[str]
    ) -> Item | None:
        manifest_path = _manifest(path)
        if manifest_path is None:
            return None
        manifest, _ = read_json(manifest_path, [])
        description = manifest.get("description")
        return plugin_item(
            self.id,
            path.name,
            "plugin",
            root.scope,
            _plugin_status(path.name, manifest, states, state_ok, root.status),
            path,
            description if isinstance(description, str) else "",
        )


def _hidden_included_entries(root: Path, warnings: list[str]) -> list[Path]:
    try:
        return [root / name for name in sorted(os.listdir(root))]
    except (FileNotFoundError, NotADirectoryError):
        return []
    except OSError as error:
        warnings.append(f"cannot read {root}: {error.strerror}")
        return []


def _skill_dirs(directory: Path, warnings: list[str]) -> list[Path]:
    """Skills are one level down, and hidden ones load too."""
    return [path for path in _hidden_included_entries(directory, warnings) if os.path.isdir(path)]


@dataclass
class _Declared:
    """A path a JSON config names: one item, or a directory of them less `skipped` names."""

    path: Path
    is_item: bool
    skipped: frozenset[str] = frozenset()

    def items(self) -> list[Path]:
        if self.is_item:
            return [self.path]
        return [
            path
            for path in _hidden_included_entries(self.path, [])
            if path.name not in self.skipped
        ]


def _declared(
    root: _Root, name: str, context: Context, warnings: list[str], base: Path | None = None
) -> list[_Declared]:
    """Read `skills.json`, `plugins.json`, or `agents.json` in a root, in order.

    Entries and inherited files can narrow what they add, by exact name. A relative path is
    from the workspace, or from the plugin for a plugin's own file.
    """
    found: list[_Declared] = []
    _read_declared(root.path / name, base or context.cwd, warnings, found, set(), [], [])
    return found


def _is_one_item(path: Path) -> bool:
    return (
        os.path.isfile(path)
        or os.path.lexists(path / SKILL_FILE)
        or os.path.lexists(path / "agent.md")
        or _manifest(path) is not None
    )


def _is_path_list(value: object) -> bool:
    return isinstance(value, list) and all(
        isinstance(entry, dict) and isinstance(entry.get("path"), str) for entry in value
    )


def _read_declared(
    path: Path,
    base: Path,
    warnings: list[str],
    found: list[_Declared],
    visited: set[str],
    include_only: list[str],
    exclude: list[str],
) -> None:
    real = os.path.realpath(path)
    if real in visited or not os.path.isfile(path):
        return
    visited.add(real)
    data, ok = read_json(path, warnings)
    if not ok:
        return
    entries, inherits = data.get("entries", []), data.get("inherits", [])
    if not (_is_path_list(entries) and _is_path_list(inherits)):
        warnings.append(f"cannot read {path}: invalid entries")
        return
    top_exclude = [*exclude, *_names(data.get("exclude"))]
    for entry in inherits:
        _read_declared(
            base / entry["path"],
            base,
            warnings,
            found,
            visited,
            [*include_only, *_names(entry.get("include_only"))],
            [*top_exclude, *_names(entry.get("exclude"))],
        )
    for entry in entries:
        directory = base / entry["path"]
        chosen = [*include_only, *_names(entry.get("include_only"))]
        skipped = frozenset([*top_exclude, *_names(entry.get("exclude"))])
        if chosen:
            # `include_only` can name a nested item, such as `group/name`.
            found.extend(
                _Declared(directory / name, True)
                for name in chosen
                if Path(name).name not in skipped
            )
        else:
            found.append(_Declared(directory, _is_one_item(directory), skipped))


def _names(value: object) -> list[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def _manifest(plugin_dir: Path) -> Path | None:
    return next(
        (plugin_dir / name for name in _MANIFESTS if os.path.exists(plugin_dir / name)), None
    )


def _plugin_status(
    name: str, manifest: dict, states: dict, state_ok: bool, default: Status
) -> Status:
    """`config.json` decides when it has an entry, and otherwise the manifest's `disabled`."""
    if not state_ok:
        return Status.UNKNOWN
    entry = states.get(name)
    if isinstance(entry, dict) and isinstance(entry.get("enabled"), bool):
        enabled = entry["enabled"]
    else:
        enabled = manifest.get("disabled") is not True
    return default if enabled else Status.DISABLED


def _workspace_status(settings: dict, settings_ok: bool, cwd: Path) -> Status:
    """The current directory's real path must be listed as is. A listed link doesn't count."""
    if not settings_ok:
        return Status.UNKNOWN
    trusted = settings.get("trustedWorkspaces")
    if isinstance(trusted, list) and os.path.realpath(cwd) in trusted:
        return Status.ENABLED
    return Status.PENDING


def _actions(event: str, handlers: object) -> list[object]:
    """Return the handlers an event runs. A wrong shape or unknown event runs nothing."""
    if not isinstance(handlers, list):
        return []
    if event in _FLAT_EVENTS:
        return [
            handler for handler in handlers if isinstance(handler, dict) and "hooks" not in handler
        ]
    if event in _GROUPED_EVENTS:
        return [
            handler
            for group in handlers
            if isinstance(group, dict) and isinstance(group.get("hooks"), list)
            for handler in group["hooks"]
        ]
    return []


def _hooks_problem(data: dict) -> str | None:
    """Say why Antigravity CLI would ignore a whole hooks file."""
    for entry in data.values():
        if not isinstance(entry, dict):
            return "invalid hook entry"
        for key, value in entry.items():
            if key == "enabled":
                continue
            if not isinstance(value, list):
                return "invalid hook entry"
            for handler in value:
                handlers = handler.get("hooks") if isinstance(handler, dict) else None
                for item in handlers if isinstance(handlers, list) else [handler]:
                    if not isinstance(item, dict):
                        return "invalid hook entry"
                    kind = item.get("type", "command")
                    if "hooks" not in item and kind not in _HANDLER_TYPES:
                        return "invalid hook entry"
    return None


def _server_details(entry: object) -> dict[str, object] | None:
    if not isinstance(entry, dict):
        return None
    # A server with both is an HTTP one.
    url = entry.get("serverUrl", entry.get("url"))
    if isinstance(url, str):
        return remote_details("streamable_http", url, entry.get("headers"))
    if isinstance(entry.get("command"), str):
        return stdio_details(entry["command"], entry.get("env"))
    return None
