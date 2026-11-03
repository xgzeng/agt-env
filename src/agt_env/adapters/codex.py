"""Codex adapter.

Discovery rules, official references, and undocumented findings are in
docs/agents/codex.md. Update that file when these rules change. tests/test_codex_oracle.py
compares results with the real agent.
"""

import functools
import hashlib
import json
import os
import re
import stat
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from agt_env.adapters.base import (
    MISSING_PLUGIN,
    Context,
    config_dir,
    is_inside_plugin,
    listed_paths,
    load_once,
    loads_skills,
    mark_bundled,
    mark_shadowed,
    marketplace_suffix,
    mcp_item,
    read_json,
    unique_roots,
)
from agt_env.hooks import Handler, handlers, hook_item
from agt_env.mcp import remote_details, stdio_details
from agt_env.model import AgentReport, Finding, Item, Kind, Scope, Status
from agt_env.skills import FoundSkill, scan_skills

_MAX_DEPTH = 6
_MAX_NAME_LENGTH = 64
_MANIFESTS = (
    ".codex-plugin/plugin.json",
    ".claude-plugin/plugin.json",
    ".cursor-plugin/plugin.json",
)
_AGENT_PLUGIN_MANIFEST = "plugin.json"
_AGENT_PLUGIN_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
_AGENT_PLUGIN_SCHEMA_PREFIX = "https://agent-plugins.org/schemas/"
_DEFAULT_PLUGIN_VERSION = "local"
_DEFAULT_HOOK_TIMEOUT = 600
_DEFAULT_CONTEXT_LIMIT = 2500
_SEMVER = re.compile(
    r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)(?:\.(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*))*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
)
_VERSION_SEGMENT = re.compile(r"[0-9A-Za-z._+-]+")
_PLUGIN_NAME = re.compile(r"[0-9A-Za-z_-]+(?:\.[0-9A-Za-z_-]+)*")
_MARKETPLACE_NAME = re.compile(r"[0-9A-Za-z_-]+")
# Everything else directly under `[agents]` declares a role.
_AGENT_SETTINGS = frozenset(
    {
        "enabled",
        "max_threads",
        "max_concurrent_threads_per_session",
        "max_depth",
        "default_subagent_model",
        "default_subagent_reasoning_effort",
        "job_max_runtime_seconds",
        "interrupt_message",
    }
)
_NEEDS_DESCRIPTION = "needs a description, which Codex requires"
_MATCHER_EVENTS = frozenset(
    {
        "PreToolUse",
        "PermissionRequest",
        "PostToolUse",
        "SessionStart",
        "SessionEnd",
        "SubagentStart",
        "SubagentStop",
        "PreCompact",
        "PostCompact",
    }
)
# Events Codex reads. Any other key under `hooks` is ignored.
_HOOK_EVENTS = _MATCHER_EVENTS | {
    "PreModelRequest",
    "PostModelRequest",
    "UserPromptSubmit",
    "Stop",
    "Interrupt",
}
_CONTEXT_EVENTS = frozenset(
    {"PreToolUse", "PostToolUse", "SessionStart", "UserPromptSubmit", "SubagentStart"}
)
_SHORT_TIMEOUT_EVENTS = frozenset({"SessionEnd", "Interrupt"})
_SHORT_TIMEOUT_DEFAULT = 1
_SHORT_TIMEOUT_MAX = 3
# Stdio and HTTP servers each refuse the other's fields.
_STDIO_ONLY = ("args", "env", "env_vars", "cwd")
_HTTP_ONLY = (
    "bearer_token_env_var",
    "http_headers",
    "http_headers_helper",
    "env_http_headers",
    "oauth",
    "oauth_resource",
    "auth",
)
# A turned-off project entry still replaces the user's.
_ENTRY_NAME_TAKERS = frozenset({Status.ENABLED, Status.DISABLED})
_REJECTED_HOOK_FINDING = "blocked by allow_managed_hooks_only in requirements.toml"


@dataclass
class _Layer:
    config: dict
    scope: Scope
    path: Path
    default: Status
    managed: bool = False

    @property
    def folder(self) -> Path:
        return self.path.parent


@dataclass
class _PluginFiles:
    root: Path
    manifest: dict
    agent_format: bool


@dataclass
class _Scan:
    """What one scan has established so far."""

    report: AgentReport
    home: Path
    codex_home: Path
    plugin_files: dict[Path, "_PluginFiles | None"] = field(default_factory=dict)
    rules: list[tuple[str, str, bool]] = field(default_factory=list)
    # Whether Codex would refuse to start, so nothing can be said about what loads.
    rejected: bool = False


class Codex:
    id = "codex"

    def scan(self, context: Context) -> AgentReport:
        codex_home, override = config_dir(context, "CODEX_HOME", context.home / ".codex")
        project_dir = context.cwd / ".codex"
        config_path = next(
            (
                path
                for path in (
                    codex_home,
                    project_dir,
                    context.home / ".agents" / "skills",
                    context.cwd / ".agents" / "skills",
                )
                if os.path.isdir(path)
            ),
            None,
        )
        report = AgentReport(
            agent=self.id,
            installed=context.on_path("codex"),
            config_path=config_path,
            override_var=override if config_path == codex_home else None,
        )
        scan = _Scan(report, context.home, codex_home)

        system_dir = context.system_root / "etc" / "codex"
        user_path = codex_home / "config.toml"
        user_config = self._load(user_path, scan)
        trust = _project_trust(user_config, context.cwd)
        layers = [
            _Layer(
                self._load(system_dir / "config.toml", scan) or {},
                Scope.SYSTEM,
                system_dir / "config.toml",
                Status.ENABLED,
                managed=True,
            ),
            _Layer(user_config or {}, Scope.USER, user_path, Status.ENABLED),
        ]
        project_path = project_dir / "config.toml"
        if os.path.realpath(project_path) != os.path.realpath(user_path):
            project_config = _read_toml(project_path, report.warnings)
            # Codex doesn't read an untrusted project's config, so that can't stop it starting.
            if project_config is None and trust is Status.ENABLED:
                scan.rejected = True
            layers.append(_Layer(project_config or {}, Scope.PROJECT, project_path, trust))
        loaded = [layer for layer in layers if layer.default is Status.ENABLED]
        effective = functools.reduce(lambda base, layer: _merged(base, layer.config), loaded, {})
        for layer in loaded:
            self._check_config(layer, scan)
        requirements = self._load(system_dir / "requirements.toml", scan) or {}

        self._skills(context, scan, user_path, user_config, effective, project_dir)

        servers_by_layer = self._mcp_servers(layers, trust)
        report.items.extend(item for servers in servers_by_layer for item in servers)
        self._check_servers(layers, effective, scan)

        plugins_by_layer = [self._plugins(layer, layers, effective, scan) for layer in layers]
        if trust is Status.ENABLED:
            _mark_replaced_plugins(plugins_by_layer, project_path)
        plugins = [plugin for layer in plugins_by_layer for plugin in layer]
        report.items.extend(plugins)
        active = [plugin for plugin in plugins if loads_skills(plugin)]
        self._plugin_contents(active, effective, scan, servers_by_layer, trust)

        report.items.extend(_agents(layers, loaded, effective, scan))

        policy = _HookPolicy(user_config, effective, requirements)
        report.items.extend(policy.requirement_items(self.id, system_dir / "requirements.toml"))
        for layer in layers:
            hooks_file = layer.folder / "hooks.json"
            hooks, ok = read_json(hooks_file, report.warnings)
            if ok and os.path.isfile(hooks_file):
                events = hooks.get("hooks")
                problem = _hooks_problem(events)
                if problem:
                    report.warnings.append(f"cannot read {hooks_file}: {problem}")
                else:
                    report.items.extend(policy.items(self.id, events, layer, hooks_file))
            report.items.extend(policy.items(self.id, layer.config.get("hooks"), layer, layer.path))
        for plugin in active:
            report.items.extend(_plugin_hooks(plugin, policy, scan))

        if scan.rejected:
            _mark_all_unknown(report.items)
        return report

    def _load(self, path: Path, scan: _Scan) -> dict | None:
        config = _read_toml(path, scan.report.warnings)
        if config is None:
            scan.rejected = True
        return config

    def _check_config(self, layer: _Layer, scan: _Scan) -> None:
        problem = _config_problem(layer.config)
        if problem:
            scan.report.warnings.append(f"Codex rejects {layer.path}: {problem}")
            scan.rejected = True

    def _check_servers(self, layers: list[_Layer], effective: dict, scan: _Scan) -> None:
        servers = effective.get("mcp_servers")
        for name, entry in servers.items() if isinstance(servers, dict) else ():
            if _server_details(entry) is None:
                path = next(
                    (
                        layer.path
                        for layer in reversed(layers)
                        if layer.default is Status.ENABLED
                        and name in (layer.config.get("mcp_servers") or {})
                    ),
                    layers[-1].path,
                )
                scan.report.warnings.append(
                    f"Codex rejects {path}: invalid MCP server entry {name}"
                )
                scan.rejected = True

    def _skills(
        self,
        context: Context,
        scan: _Scan,
        user_path: Path,
        user_config: dict | None,
        effective: dict,
        project_dir: Path,
    ) -> None:
        report = scan.report
        rules = _skill_rules(user_config or {}, user_path.parent, context.home)
        bundled_root = scan.codex_home / "skills" / ".system"
        bundled_off = _bundled_skills_off(effective)
        for scope, root in unique_roots(
            [
                (Scope.SYSTEM, context.system_root / "etc" / "codex" / "skills"),
                (Scope.SYSTEM, bundled_root),
                (Scope.USER, context.home / ".agents" / "skills"),
                (Scope.USER, scan.codex_home / "skills"),
                (Scope.PROJECT, context.cwd / ".agents" / "skills"),
                (Scope.PROJECT, project_dir / "skills"),
            ]
        ):
            found, warnings = scan_skills(root, max_depth=_MAX_DEPTH)
            report.warnings.extend(warnings)
            for skill in found:
                item = _skill_item(self.id, skill, scope, rules)
                if bundled_off and root == bundled_root and item.status is Status.ENABLED:
                    item.status = Status.DISABLED
                report.items.append(item)
        report.items = load_once(report.items)
        scan.rules = rules

    def _mcp_servers(self, layers: list[_Layer], trust: Status) -> list[list[Item]]:
        by_layer = []
        base: dict = {}
        for layer in layers:
            servers = layer.config.get("mcp_servers")
            rows = []
            for name, entry in servers.items() if isinstance(servers, dict) else ():
                if layer.default is Status.ENABLED:
                    # A loaded layer's entry is merged over the ones below it, field by field.
                    entry = _merged(base.get(name), entry)
                details = _server_details(entry)
                status = (
                    Status.BROKEN
                    if details is None
                    else _switched(entry.get("enabled", True), layer.default)
                )
                rows.append(mcp_item(self.id, name, details, layer.scope, status, layer.path))
            by_layer.append(rows)
            if layer.default is Status.ENABLED and isinstance(servers, dict):
                base = _merged(base, servers)
        # A project entry that turns a server off still replaces the user's.
        mark_shadowed(
            [
                item
                for layer, rows in reversed(list(zip(layers, by_layer, strict=True)))
                if layer.default is Status.ENABLED
                for item in rows
            ],
            winners=_ENTRY_NAME_TAKERS,
        )
        return by_layer

    def _plugins(
        self, layer: _Layer, layers: list[_Layer], effective: dict, scan: _Scan
    ) -> list[Item]:
        plugins = layer.config.get("plugins")
        default_on = _plugins_on_by_default(effective)
        base: dict = {}
        for lower in layers:
            if lower is layer:
                break
            if lower.default is Status.ENABLED and isinstance(lower.config.get("plugins"), dict):
                base = _merged(base, lower.config["plugins"])
        items = []
        for plugin_id, entry in plugins.items() if isinstance(plugins, dict) else ():
            if plugin_id == "_default" or not isinstance(entry, dict):
                continue
            if layer.default is Status.ENABLED:
                entry = _merged(base.get(plugin_id), entry)
            segments = _plugin_segments(plugin_id)
            directory = (
                scan.codex_home / "plugins" / "cache" / segments[1] / segments[0]
                if segments
                else layer.path
            )
            cached = segments is not None and os.path.isdir(directory)
            enabled = entry.get("enabled")
            status = _switched(enabled if isinstance(enabled, bool) else default_on, layer.default)
            finding = None
            # Enabling a plugin that isn't on disk can't work.
            if status is Status.ENABLED and segments is None:
                status, finding = Status.BROKEN, Finding("invalid plugin ID")
            elif status is Status.ENABLED and not cached:
                status, finding = Status.BROKEN, MISSING_PLUGIN
            files = _plugin_files(directory, scan) if cached else None
            if status is Status.ENABLED and files is None:
                status, finding = Status.BROKEN, Finding("missing or invalid plugin manifest")
            description = files.manifest.get("description") if files else ""
            source = directory if cached else layer.path
            items.append(
                Item(
                    name=plugin_id,
                    name_suffix=marketplace_suffix(plugin_id),
                    kind=Kind.PLUGIN,
                    native_kind="plugin",
                    agent=self.id,
                    scope=layer.scope,
                    status=status,
                    description=description if isinstance(description, str) else "",
                    source_path=source,
                    resolved_path=Path(os.path.realpath(source)),
                    findings=[finding] if finding else [],
                )
            )
        return items

    def _plugin_contents(
        self,
        active: list[Item],
        effective: dict,
        scan: _Scan,
        servers_by_layer: list[list[Item]],
        trust: Status,
    ) -> None:
        report = scan.report
        skills: list[Item] = []
        replaced: set[Path] = set()
        for plugin in active:
            found = _plugin_skills(self.id, plugin, scan)
            skills.extend(found)
            if plugin.status is Status.ENABLED:
                replaced.update(item.resolved_path for item in found)
        # A skill also linked from an ordinary root loads once, as the plugin's.
        report.items = [
            item
            for item in report.items
            if item.kind is not Kind.SKILL
            or item.status is Status.BROKEN
            or item.resolved_path not in replaced
        ]
        report.items.extend(load_once(skills))

        settings = effective.get("plugins")
        settings = settings if isinstance(settings, dict) else {}
        rows = [
            item
            for plugin in sorted(active, key=lambda plugin: plugin.name)
            for item in _plugin_servers(self.id, plugin, settings.get(plugin.name), scan)
        ]
        # A configured server wins, then the first plugin in plugin ID order.
        configured = [item for items in reversed(servers_by_layer) for item in items]
        if trust is not Status.ENABLED:
            configured = [item for item in configured if item.scope is not Scope.PROJECT]
        mark_shadowed([*configured, *rows], winners=_ENTRY_NAME_TAKERS)
        report.items.extend(rows)


def _mark_all_unknown(items: list[Item]) -> None:
    for item in items:
        if item.status is not Status.BROKEN:
            item.status = Status.UNKNOWN
            item.findings = []


def _merged(base: object, entry: object) -> object:
    if not isinstance(base, dict) or not isinstance(entry, dict):
        return entry
    merged = dict(base)
    for key, value in entry.items():
        merged[key] = _merged(base.get(key), value)
    return merged


def _switched(enabled: object, default: Status) -> Status:
    """Combine an entry's `enabled` with what its config layer allows."""
    if enabled is False:
        return Status.DISABLED
    return default if enabled is True else Status.UNKNOWN


def _project_trust(user_config: dict | None, cwd: Path) -> Status:
    """Project config loads only when trusted. Without a decision, it awaits the user."""
    if user_config is None:
        return Status.UNKNOWN
    projects = user_config.get("projects")
    entry = projects.get(str(cwd)) if isinstance(projects, dict) else None
    level = entry.get("trust_level") if isinstance(entry, dict) else None
    if level == "trusted":
        return Status.ENABLED
    return Status.DISABLED if level == "untrusted" else Status.PENDING


def _server_details(entry: object) -> dict[str, object] | None:
    """Return a safe summary, or None if Codex can't deserialize the entry."""
    if not isinstance(entry, dict) or not isinstance(entry.get("enabled", True), bool):
        return None
    command, url = entry.get("command"), entry.get("url")
    if isinstance(command, str) and "url" not in entry:
        if any(key in entry for key in _HTTP_ONLY):
            return None
        return stdio_details(command, entry.get("env"))
    if isinstance(url, str) and "command" not in entry:
        if any(key in entry for key in _STDIO_ONLY):
            return None
        return remote_details("streamable_http", url, entry.get("http_headers"))
    return None


def _config_problem(config: dict) -> str | None:
    """Say why Codex would refuse this file, for the sections read here.

    Codex refuses a config file over any field of the wrong type, and this doesn't check them all.
    """
    plugins = config.get("plugins", {})
    if not isinstance(plugins, dict):
        return "plugins isn't a table"
    for plugin_id, entry in plugins.items():
        if not isinstance(entry, dict) or not isinstance(entry.get("enabled", True), bool):
            return f"invalid entry for plugin {plugin_id}"
    skills = config.get("skills", {})
    if not isinstance(skills, dict):
        return "skills isn't a table"
    bundled = skills.get("bundled", {})
    if not isinstance(bundled, dict) or not isinstance(bundled.get("enabled", True), bool):
        return "invalid skills.bundled"
    rules = skills.get("config", [])
    if not isinstance(rules, list) or not all(_is_valid_skill_rule(rule) for rule in rules):
        return "invalid skills.config entry"
    agents = config.get("agents", {})
    if not isinstance(agents, dict) or not isinstance(agents.get("enabled", True), bool):
        return "invalid agents table"
    for name, role in agents.items():
        if name not in _AGENT_SETTINGS and not isinstance(role, dict):
            return f"invalid agent role {name}"
    if "hooks" in config:
        problem = _hooks_problem(config["hooks"])
        if problem:
            return problem
    return None


def _is_valid_skill_rule(rule: object) -> bool:
    return (
        isinstance(rule, dict)
        and isinstance(rule.get("enabled"), bool)
        and isinstance(rule.get("path", ""), str)
        and isinstance(rule.get("name", ""), str)
    )


class _HookPolicy:
    """Decides a hook's status. A hook that isn't managed must be reviewed before it runs."""

    def __init__(self, user_config: dict | None, effective: dict, requirements: dict) -> None:
        self.off = _hooks_turned_off(effective)
        hooks = (user_config or {}).get("hooks")
        state = hooks.get("state") if isinstance(hooks, dict) else None
        self.state = state if isinstance(state, dict) else {}
        self.managed_only = requirements.get("allow_managed_hooks_only") is True
        self.required = requirements.get("hooks")

    def requirement_items(self, agent: str, path: Path) -> list[Item]:
        if self.required is None or _hooks_problem(self.required):
            return []
        layer = _Layer({}, Scope.SYSTEM, path, Status.ENABLED, managed=True)
        return self.items(agent, self.required, layer, path, native_kind="managed hook")

    def items(
        self,
        agent: str,
        events: object,
        layer: _Layer,
        path: Path,
        *,
        key_source: str | None = None,
        native_kind: str = "hook",
        default: Status | None = None,
    ) -> list[Item]:
        default = layer.default if default is None else default
        items = []
        for handler in handlers(events):
            if handler.event not in _HOOK_EVENTS:
                continue
            key = _hook_key(key_source or str(path), handler)
            state = self.state.get(key)
            state = state if isinstance(state, dict) else {}
            status = default
            finding = None
            problem = _handler_problem(handler)
            if problem:
                status, finding = Status.BROKEN, Finding(problem)
            elif self.off:
                status = Status.DISABLED
            elif layer.managed:
                status = Status.ENABLED
            elif self.managed_only:
                status, finding = Status.DISABLED, Finding(_REJECTED_HOOK_FINDING)
            elif state.get("enabled") is False:
                status = Status.DISABLED
            elif default is Status.ENABLED:
                trusted = state.get("trusted_hash")
                if trusted is None:
                    status = Status.PENDING
                elif key_source is not None:
                    # A plugin hook's fingerprint also covers the plugin's files.
                    status = Status.UNKNOWN
                elif trusted != _hook_hash(handler):
                    status = Status.PENDING
                    finding = Finding("changed since it was trusted")
            item = hook_item(agent, handler, layer.scope, status, path, native_kind)
            if finding:
                item.status = status
                item.findings = [finding]
            items.append(item)
        return items


def _hooks_turned_off(effective: dict) -> bool:
    """The `hooks` feature decides, or its old name when that isn't set."""
    features = effective.get("features")
    if not isinstance(features, dict):
        return False
    value = features.get("hooks", features.get("codex_hooks"))
    return value is False


def _snake_case(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def _hook_key(source: str, handler: Handler) -> str:
    return f"{source}:{_snake_case(handler.event)}:{handler.group}:{handler.index}"


def _hook_timeout(event: str, timeout: object) -> int:
    if event in _SHORT_TIMEOUT_EVENTS:
        value = _SHORT_TIMEOUT_DEFAULT if timeout is None else timeout
        return min(max(value, 1), _SHORT_TIMEOUT_MAX)
    return max(_DEFAULT_HOOK_TIMEOUT if timeout is None else timeout, 1)


def _hook_hash(handler: Handler) -> str:
    """Reproduce how Codex fingerprints a hook, which is how it notices a changed one."""
    entry = handler.entry if isinstance(handler.entry, dict) else {}
    timeout = _hook_timeout(handler.event, entry.get("timeout"))
    if entry.get("type") == "mcp_tool":
        normalized = {
            "type": "mcp_tool",
            "server": entry.get("server"),
            "tool": entry.get("tool"),
            "input": entry.get("input") or {},
            "timeout": timeout,
        }
    else:
        normalized = {
            "type": "command",
            "command": entry.get("command"),
            "timeout": timeout,
            "async": entry.get("async", False),
        }
        limit = entry.get("additionalContextLimit")
        if handler.event in _CONTEXT_EVENTS and limit not in (None, _DEFAULT_CONTEXT_LIMIT):
            normalized["additionalContextLimit"] = limit
    if entry.get("statusMessage") is not None:
        normalized["statusMessage"] = entry["statusMessage"]
    identity: dict[str, object] = {"event_name": _snake_case(handler.event), "hooks": [normalized]}
    if handler.matcher is not None and handler.event in _MATCHER_EVENTS:
        identity["matcher"] = handler.matcher
    text = json.dumps(identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return f"sha256:{hashlib.sha256(text.encode()).hexdigest()}"


def _is_count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_representable(value: object) -> bool:
    if isinstance(value, dict):
        return all(_is_representable(item) for item in value.values())
    if isinstance(value, list):
        return all(_is_representable(item) for item in value)
    return value is not None


def _is_valid_handler(entry: object) -> bool:
    """Whether Codex can deserialize a handler. One that can't costs the whole hooks table."""
    if not isinstance(entry, dict):
        return False
    kind = entry.get("type")
    if kind in ("prompt", "agent"):
        return True
    if kind not in ("command", "mcp_tool"):
        return False
    if entry.get("timeout") is not None and not _is_count(entry["timeout"]):
        return False
    if not isinstance(entry.get("statusMessage") or "", str):
        return False
    if kind == "mcp_tool":
        tool_input = entry.get("input", {})
        return (
            isinstance(entry.get("server"), str)
            and isinstance(entry.get("tool"), str)
            and isinstance(tool_input, dict)
            and _is_representable(tool_input)
        )
    limit = entry.get("additionalContextLimit")
    return (
        isinstance(entry.get("command"), str)
        and isinstance(entry.get("commandWindows") or "", str)
        and isinstance(entry.get("async", False), bool)
        and (limit is None or _is_count(limit))
    )


def _hooks_problem(events: object) -> str | None:
    """Say why Codex would refuse a whole hooks table, or None. Unknown events are ignored."""
    if events is None:
        return None
    if not isinstance(events, dict):
        return "invalid hook entry"
    for event, groups in events.items():
        if event not in _HOOK_EVENTS:
            continue
        if not isinstance(groups, list):
            return "invalid hook entry"
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("matcher") or "", str):
                return "invalid hook entry"
            entries = group.get("hooks", [])
            if not isinstance(entries, list) or not all(map(_is_valid_handler, entries)):
                return "invalid hook entry"
    return None


def _is_invalid_matcher(matcher: str) -> bool:
    """Python's regular expressions stand in for Rust's, except for syntax only Rust has."""
    if matcher in ("", "*") or "\\p" in matcher or "\\P" in matcher or "(?" in matcher:
        return False
    try:
        re.compile(matcher)
    except re.error:
        return True
    return False


def _handler_problem(handler: Handler) -> str | None:
    """Say why Codex skips this handler while keeping the rest of its file."""
    entry = handler.entry if isinstance(handler.entry, dict) else {}
    kind = entry.get("type", "command")
    if kind in ("prompt", "agent"):
        return f"Codex doesn't support {kind} hooks"
    if kind not in ("command", "mcp_tool"):
        return f"Codex doesn't support {kind} hooks"
    if (
        handler.event in _MATCHER_EVENTS
        and handler.matcher is not None
        and _is_invalid_matcher(handler.matcher)
    ):
        return "invalid matcher"
    if kind == "command":
        command = entry.get("command")
        return "empty hook command" if isinstance(command, str) and not command.strip() else None
    if handler.event == "SessionEnd":
        return "Codex doesn't support MCP hooks on SessionEnd"
    server, tool = entry.get("server"), entry.get("tool")
    if not (isinstance(server, str) and server.strip() and isinstance(tool, str) and tool.strip()):
        return "needs a server and a tool"
    return None


def _mark_replaced_plugins(plugins_by_layer: list[list[Item]], project_path: Path) -> None:
    """A trusted project's entry decides, even when it turns the plugin off."""
    project = {item.name: item for item in plugins_by_layer[-1]}
    for layer in plugins_by_layer[:-1]:
        for item in layer:
            replacement = project.get(item.name)
            if (
                replacement
                and item.status is Status.ENABLED
                and replacement.status in _ENTRY_NAME_TAKERS
            ):
                item.status = Status.SHADOWED
                item.findings.append(Finding("shadowed by the entry in", project_path))


def _plugins_on_by_default(effective: dict) -> bool:
    plugins = effective.get("plugins")
    default = plugins.get("_default") if isinstance(plugins, dict) else None
    return not (isinstance(default, dict) and default.get("enabled") is False)


def _plugin_segments(plugin_id: str) -> tuple[str, str] | None:
    """Split `name@marketplace`, or None if Codex refuses this ID."""
    name, at, marketplace = plugin_id.rpartition("@")
    if not at or not _PLUGIN_NAME.fullmatch(name) or not _MARKETPLACE_NAME.fullmatch(marketplace):
        return None
    return name, marketplace


def _is_skill_path(path: str) -> bool:
    return is_inside_plugin(path) and Path(path) != Path(".")


def _identifier_key(identifier: str) -> tuple[int, int, str]:
    """Numbers sort numerically, and before anything else."""
    return (0, int(identifier), "") if identifier.isdigit() else (1, 0, identifier)


def _compare_identifiers(left: str, right: str) -> int:
    left_key = [_identifier_key(part) for part in left.split(".")]
    right_key = [_identifier_key(part) for part in right.split(".")]
    return (left_key > right_key) - (left_key < right_key)


def _compare_versions(left: str, right: str) -> int:
    """Compare as semver when both are, otherwise as text, which is what Codex does."""
    left_match, right_match = _SEMVER.fullmatch(left), _SEMVER.fullmatch(right)
    if not (left_match and right_match):
        return (left > right) - (left < right)
    left_core = [int(part) for part in left_match.groups()[:3]]
    right_core = [int(part) for part in right_match.groups()[:3]]
    if left_core != right_core:
        return -1 if left_core < right_core else 1
    left_pre, right_pre = left_match[4], right_match[4]
    if left_pre != right_pre:
        if left_pre is None or right_pre is None:
            return 1 if left_pre is None else -1
        return _compare_identifiers(left_pre, right_pre)
    # Unlike the semver spec, the library Codex uses also orders build metadata.
    left_build, right_build = left_match[5], right_match[5]
    if left_build == right_build:
        return 0
    if left_build is None or right_build is None:
        return 1 if right_build is None else -1
    return _compare_identifiers(left_build, right_build)


def _active_version(directory: Path) -> Path | None:
    """Codex uses `local` if present, or else only the highest cached version."""
    try:
        with os.scandir(directory) as iterator:
            versions = [
                entry.name
                for entry in iterator
                if entry.is_dir(follow_symlinks=False)
                and entry.name not in (".", "..")
                and _VERSION_SEGMENT.fullmatch(entry.name)
            ]
    except OSError:
        return None
    if not versions:
        return None
    if _DEFAULT_PLUGIN_VERSION in versions:
        return directory / _DEFAULT_PLUGIN_VERSION
    return directory / max(versions, key=functools.cmp_to_key(_compare_versions))


def _agent_plugin_manifest(root: Path) -> tuple[Path | None, bool]:
    """Return a root `plugin.json` that declares the Agent Plugins schema, and whether to go on."""
    path = root / _AGENT_PLUGIN_MANIFEST
    try:
        mode = os.lstat(path).st_mode
    except FileNotFoundError:
        return None, True
    except OSError:
        return None, False
    if not stat.S_ISREG(mode):
        return None, False
    try:
        with open(path, encoding="utf-8") as file:
            schema = json.load(file).get("$schema")
    except (OSError, ValueError, AttributeError):
        return None, True
    if isinstance(schema, str) and schema.startswith(_AGENT_PLUGIN_SCHEMA_PREFIX):
        return path, True
    return None, True


def _manifest_path(root: Path) -> Path | None:
    """Find a plugin's manifest. The first one present counts, even if it's invalid."""
    agent_manifest, go_on = _agent_plugin_manifest(root)
    if agent_manifest or not go_on:
        return agent_manifest
    for relative in _MANIFESTS:
        path = root / relative
        try:
            if not stat.S_ISDIR(os.lstat(path.parent).st_mode):
                return None
        except OSError:
            continue
        try:
            return path if stat.S_ISREG(os.lstat(path).st_mode) else None
        except FileNotFoundError:
            continue
        except OSError:
            return None
    return None


def _is_valid_manifest(manifest: object) -> bool:
    """Fields that cost the whole manifest when they have the wrong type."""
    if not isinstance(manifest, dict):
        return False
    keywords = manifest.get("keywords", [])
    return (
        all(
            isinstance(manifest.get(key) or "", str)
            for key in ("name", "version", "description", "apps")
        )
        and isinstance(keywords, list)
        and all(isinstance(word, str) for word in keywords)
        and isinstance(manifest.get("interface") or {}, dict)
    )


def _plugin_files(directory: Path, scan: _Scan) -> _PluginFiles | None:
    """Read a cached plugin's active version once per scan. None means it brings nothing."""
    if directory in scan.plugin_files:
        return scan.plugin_files[directory]
    files = None
    root = _active_version(directory)
    path = None if root is None else _manifest_path(root)
    if path is not None:
        try:
            with open(path, encoding="utf-8") as file:
                manifest = json.load(file)
        except (OSError, ValueError) as error:
            scan.report.warnings.append(f"cannot read {path}: {error}")
        else:
            agent_format = path.name == _AGENT_PLUGIN_MANIFEST and path.parent == root
            schema = manifest.get("$schema") if isinstance(manifest, dict) else None
            supported = not agent_format or schema == _AGENT_PLUGIN_SCHEMA
            if not (_is_valid_manifest(manifest) and supported):
                scan.report.warnings.append(f"cannot read {path}: invalid plugin manifest")
            elif not agent_format or manifest.get("name"):
                files = _PluginFiles(root, manifest, agent_format)
    scan.plugin_files[directory] = files
    return files


def _plugin_skills(agent: str, plugin: Item, scan: _Scan) -> list[Item]:
    files = scan.plugin_files.get(plugin.source_path)
    if files is None:
        return []
    # A manifest's paths replace the default, unless none of them is valid.
    paths = [path for path in listed_paths(files.manifest.get("skills")) if _is_skill_path(path)]
    # Without a name in the manifest, Codex uses the name of the version's directory.
    prefix = files.manifest.get("name") or files.root.name
    depth = 1 if files.agent_format else _MAX_DEPTH
    items = []
    for path in paths or ["skills"]:
        found, scan_warnings = scan_skills(files.root / path, max_depth=depth)
        scan.report.warnings.extend(scan_warnings)
        for skill in found:
            name = _skill_name(skill)
            full_name = f"{prefix}:{name}"
            item = _skill_item(agent, skill, plugin.scope, scan.rules, rule_name=full_name)
            items.append(mark_bundled(item, plugin, full_name, prefix=f"{prefix}:"))
    return items


def _plugin_servers(agent: str, plugin: Item, settings: object, scan: _Scan) -> list[Item]:
    files = scan.plugin_files.get(plugin.source_path)
    if files is None:
        return []
    declared = files.manifest.get("mcpServers")
    if isinstance(declared, dict):
        data, path = declared, files.root / _manifest_relative(files)
    else:
        relative = (
            os.path.normpath(declared)
            if isinstance(declared, str) and is_inside_plugin(declared)
            else ".mcp.json"
        )
        path = files.root / relative
        data, ok = read_json(path, scan.report.warnings)
        if not ok:
            return []
        # The Agent Plugins format has its own file, which must name its schema.
        if files.agent_format and "$schema" not in data:
            return []
    servers = data.get("mcpServers", data)
    policies = settings.get("mcp_servers") if isinstance(settings, dict) else None
    policies = policies if isinstance(policies, dict) else {}
    items = []
    for name, entry in servers.items() if isinstance(servers, dict) else ():
        if name == "$schema":
            continue
        policy = policies.get(name)
        off = isinstance(policy, dict) and policy.get("enabled") is False
        details = _server_details(entry)
        status = Status.BROKEN if details is None else Status.ENABLED
        if off and details is not None:
            status = Status.DISABLED
        item = mcp_item(agent, name, details, plugin.scope, status, path)
        items.append(mark_bundled(item, plugin, name, "plugin mcp server"))
    return items


def _manifest_relative(files: _PluginFiles) -> str:
    path = _manifest_path(files.root)
    return os.path.relpath(path, files.root) if path else _MANIFESTS[0]


def _plugin_hooks(plugin: Item, policy: _HookPolicy, scan: _Scan) -> list[Item]:
    files = scan.plugin_files.get(plugin.source_path)
    if files is None or files.agent_format:
        return []
    declared = files.manifest.get("hooks")
    sources: list[tuple[object, Path, str]] = []
    inline = declared if isinstance(declared, list) else [declared]
    if isinstance(declared, dict) or (
        isinstance(declared, list) and declared and all(isinstance(e, dict) for e in declared)
    ):
        path = files.root / _manifest_relative(files)
        for index, entry in enumerate(inline):
            sources.append((entry.get("hooks"), path, f"plugin.json#hooks[{index}]"))
    else:
        # A manifest's paths replace the default file.
        paths = [path for path in listed_paths(declared) if is_inside_plugin(path)]
        for relative in [os.path.normpath(path) for path in paths] or ["hooks/hooks.json"]:
            path = files.root / relative
            hooks, ok = read_json(path, scan.report.warnings)
            if ok and os.path.isfile(path):
                sources.append((hooks.get("hooks"), path, relative))
    items = []
    agent = scan.report.agent
    for events, path, relative in sources:
        problem = _hooks_problem(events)
        if problem:
            scan.report.warnings.append(f"cannot read {path}: {problem}")
            continue
        layer = _Layer({}, plugin.scope, path, plugin.status)
        items.extend(
            policy.items(
                agent,
                events,
                layer,
                path,
                key_source=f"{plugin.name}:{relative}",
                native_kind="plugin hook",
            )
        )
    return [mark_bundled(item, plugin, item.name, "plugin hook") for item in items]


def _skill_name(skill: FoundSkill) -> str:
    return (skill.fields or {}).get("name") or skill.path.name


def _skill_item(
    agent: str,
    skill: FoundSkill,
    scope: Scope,
    rules: list[tuple[str, str, bool]],
    *,
    rule_name: str | None = None,
) -> Item:
    fields = skill.fields or {}
    name = _skill_name(skill)
    problem = skill.problem
    if not skill.broken:
        if len(name) > _MAX_NAME_LENGTH:
            problem = f"name is longer than {_MAX_NAME_LENGTH} characters, which Codex refuses"
        elif not fields.get("description", "").strip():
            problem = "has no description, which Codex requires"
    if skill.broken or problem:
        status = Status.BROKEN
    else:
        status = (
            Status.DISABLED
            if _skill_is_turned_off(rules, skill, rule_name or name)
            else Status.ENABLED
        )
    return Item(
        name=name,
        kind=Kind.SKILL,
        native_kind="skill",
        agent=agent,
        scope=scope,
        status=status,
        description=fields.get("description", ""),
        source_path=skill.path,
        resolved_path=skill.resolved_path,
        findings=[Finding(problem or "file can't be read")] if status is Status.BROKEN else [],
    )


def _read_toml(path: Path, warnings: list[str]) -> dict | None:
    """Return the parsed file, {} if it's missing, or None if it can't be read."""
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "rb") as file:
            return tomllib.load(file)
    except (OSError, ValueError) as error:
        warnings.append(f"cannot read {path}: {error}")
        return None


def _resolved(value: str, base: Path, home: Path) -> str:
    """Resolve a path from config the way Codex does: `~` is home, and others are from the file."""
    if value == "~" or value.startswith("~/"):
        path = home / value[2:]
    else:
        path = base / value
    return os.path.realpath(path)


def _skill_rules(config: dict, base: Path, home: Path) -> list[tuple[str, str, bool]]:
    """Read `[[skills.config]]` in order, as (selector kind, value, enabled).

    A repeated selector moves to the end. Only the user's config counts. An entry with both
    selectors, or neither, is ignored.
    """
    skills = config.get("skills")
    entries = skills.get("config") if isinstance(skills, dict) else None
    rules: list[tuple[str, str, bool]] = []
    for entry in entries if isinstance(entries, list) else []:
        if not _is_valid_skill_rule(entry):
            continue
        path, name = entry.get("path"), entry.get("name")
        if path is not None and name is None:
            selector = ("path", _resolved(path, base, home))
        elif name is not None and path is None and name.strip():
            selector = ("name", name.strip())
        else:
            continue
        rules = [rule for rule in rules if rule[:2] != selector]
        rules.append((*selector, entry["enabled"]))
    return rules


def _skill_is_turned_off(rules: list[tuple[str, str, bool]], skill: FoundSkill, name: str) -> bool:
    """The last rule that matches a skill decides."""
    real = os.path.realpath(skill.skill_file)
    off = False
    for kind, value, enabled in rules:
        if value == (real if kind == "path" else name):
            off = not enabled
    return off


def _bundled_skills_off(effective: dict) -> bool:
    skills = effective.get("skills")
    bundled = skills.get("bundled") if isinstance(skills, dict) else None
    return isinstance(bundled, dict) and bundled.get("enabled") is False


def _feature_is_on(value: object) -> bool:
    return value is True or (isinstance(value, dict) and value.get("enabled") is True)


def _agents_turned_off(effective: dict) -> bool:
    settings = effective.get("agents")
    features = effective.get("features")
    if isinstance(features, dict) and _feature_is_on(features.get("multi_agent_v2")):
        return False
    return isinstance(settings, dict) and settings.get("enabled") is False


def _role_files(root: Path) -> list[Path]:
    """Find `*.toml` files at any depth, in Codex's order. Dangling links show as broken."""
    found = []
    for directory, _, names in os.walk(root):
        found.extend(Path(directory) / name for name in names if name.endswith(".toml"))
    return sorted(found)


def _read_role_file(path: Path, warnings: list[str], *, declared: bool) -> tuple[dict, str | None]:
    """Return a role file's fields and why Codex refuses it, if it does."""
    if os.path.islink(path) and not os.path.exists(path):
        return {}, "symlink target is missing"
    if not os.path.isfile(path):
        return {}, "file can't be read"
    fields = _read_toml(path, warnings)
    if fields is None:
        return {}, "file isn't valid TOML"
    for key in ("name", "description", "developer_instructions"):
        if key in fields and not isinstance(fields[key], str):
            return {}, f"{key} isn't text"
    if not fields.get("description", "x").strip():
        return fields, "description can't be blank"
    instructions = fields.get("developer_instructions")
    if instructions is None and not declared:
        return fields, "needs developer_instructions, which Codex requires"
    if instructions is not None and not instructions.strip():
        return fields, "developer_instructions can't be blank"
    if not declared and not fields.get("name", "").strip():
        return fields, "needs a name, which Codex requires"
    return fields, None


def _layer_roles(
    layer: _Layer, status: Status, home: Path, warnings: list[str]
) -> list[tuple[str, str, Path, str | None]]:
    """Return (name, description, source, problem) for each role a layer defines."""
    roles = []
    claimed = set()
    settings = layer.config.get("agents")
    for key, value in settings.items() if isinstance(settings, dict) else ():
        if key in _AGENT_SETTINGS or not isinstance(value, dict):
            continue
        name, description, problem, source = key, value.get("description"), None, layer.path
        if isinstance(description, str) and not description.strip():
            problem = "description can't be blank"
        config_file = value.get("config_file")
        if isinstance(config_file, str):
            real = _resolved(config_file, layer.folder, home)
            source = Path(real)
            claimed.add(real)
            fields, file_problem = _read_role_file(source, warnings, declared=True)
            problem = problem or file_problem
            name = fields.get("name", "").strip() or key
            description = fields.get("description", description)
        roles.append((name, description if isinstance(description, str) else "", source, problem))
    for path in _role_files(layer.folder / "agents"):
        if os.path.realpath(path) in claimed:
            continue
        fields, problem = _read_role_file(path, warnings, declared=False)
        name = fields.get("name", "").strip() or path.stem
        description = fields.get("description", "")
        roles.append((name, description, path, problem))
    return roles


def _agents(layers: list[_Layer], loaded: list[_Layer], effective: dict, scan: _Scan) -> list[Item]:
    """List roles from low to high precedence, as a layer's role can inherit a description."""
    off = _agents_turned_off(effective)
    descriptions: dict[str, str] = {}
    rows_by_layer: list[tuple[_Layer, list[Item]]] = []
    for layer in layers:
        status = Status.DISABLED if off else layer.default
        rows = []
        seen = set()
        for name, description, source, problem in _layer_roles(
            layer, status, scan.home, scan.report.warnings
        ):
            if not problem and name in seen:
                problem = "another role in this layer has the same name"
            seen.add(name)
            if not problem and layer in loaded:
                description = description or descriptions.get(name, "")
                if description:
                    descriptions[name] = description
            if not problem and not description:
                problem = _NEEDS_DESCRIPTION
            rows.append(
                Item(
                    name=name,
                    kind=Kind.SUBAGENT,
                    native_kind="agent",
                    agent=scan.report.agent,
                    scope=layer.scope,
                    status=Status.BROKEN if problem else status,
                    description=description,
                    source_path=source,
                    resolved_path=Path(os.path.realpath(source)),
                    findings=[Finding(problem)] if problem else [],
                )
            )
        rows_by_layer.append((layer, rows))
    mark_shadowed(
        [item for layer, rows in reversed(rows_by_layer) if layer in loaded for item in rows]
    )
    return [item for _, rows in rows_by_layer for item in rows]
