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
import tomllib
from pathlib import Path

from agt_view.adapters.base import (
    INVALID_SERVER,
    MISSING_PLUGIN,
    Context,
    config_dir,
    is_inside_plugin,
    list_entries,
    listed_paths,
    load_once,
    loads_skills,
    mark_bundled,
    mark_shadowed,
    marketplace_suffix,
    read_json,
    unique_roots,
)
from agt_view.hooks import Handler, handlers, hook_item
from agt_view.mcp import remote_details, stdio_details
from agt_view.model import AgentReport, Finding, Item, Kind, Scope, Status
from agt_view.skills import FoundSkill, scan_skills

_MAX_DEPTH = 6
_PLUGIN_MANIFEST = Path(".codex-plugin") / "plugin.json"
_DEFAULT_HOOK_TIMEOUT = 600
_SUPPORTED_HANDLERS = ("command", "mcp_tool")
_SEMVER = re.compile(r"(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?")
_AGENT_FIELDS = "needs a name, description, and developer_instructions, which Codex requires"
# A turned-off project entry still replaces the user's.
_ENTRY_NAME_TAKERS = frozenset({Status.ENABLED, Status.DISABLED})


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
            override_var=override if config_path == codex_home else None,
        )

        user_path = codex_home / "config.toml"
        user_config = _read_toml(user_path, report.warnings)
        disabled = None if user_config is None else _disabled_skill_files(user_config)
        for scope, root in unique_roots(
            [
                (Scope.SYSTEM, context.system_root / "etc" / "codex" / "skills"),
                (Scope.SYSTEM, codex_home / "skills" / ".system"),
                (Scope.USER, context.home / ".agents" / "skills"),
                (Scope.USER, codex_home / "skills"),
                (Scope.PROJECT, context.cwd / ".agents" / "skills"),
                (Scope.PROJECT, project_dir / "skills"),
            ]
        ):
            found, warnings = scan_skills(root, max_depth=_MAX_DEPTH)
            report.warnings.extend(warnings)
            report.items.extend(self._skill(skill, scope, disabled) for skill in found)
        report.items = load_once(report.items)

        trust = _project_trust(user_config, context.cwd)
        layers = [(user_config or {}, Scope.USER, user_path, Status.ENABLED)]
        project_path = project_dir / "config.toml"
        if os.path.realpath(project_path) != os.path.realpath(user_path):
            project_config = _read_toml(project_path, report.warnings) or {}
            layers.append((project_config, Scope.PROJECT, project_path, trust))
        user_servers = (user_config or {}).get("mcp_servers")
        servers_by_layer = []
        plugins_by_layer = []
        for config, scope, path, default in layers:
            # A trusted project's entry is merged over the user's, field by field.
            base = user_servers if scope is Scope.PROJECT and trust is Status.ENABLED else None
            servers = self._mcp_servers(config, scope, path, default, base)
            servers_by_layer.append(servers)
            report.items.extend(servers)
            plugins_by_layer.append(self._plugins(config, scope, path, default, codex_home))
        if trust is Status.ENABLED:
            # A project entry that turns a server off still replaces the user's.
            mark_shadowed(
                [item for servers in reversed(servers_by_layer) for item in servers],
                winners=_ENTRY_NAME_TAKERS,
            )
            _mark_replaced_plugins(plugins_by_layer, project_path)
        plugins = [plugin for layer in plugins_by_layer for plugin in layer]
        report.items.extend(plugins)
        loaded = [plugin for plugin in plugins if loads_skills(plugin)]
        report.items.extend(
            load_once(
                [
                    skill
                    for plugin in loaded
                    for skill in self._plugin_skills(plugin, disabled, report.warnings)
                ]
            )
        )

        policy = _HookPolicy(user_config)
        for config, scope, path, default in layers:
            # Agents and hooks in a project's `.codex` wait for the same trust as its config.
            directory = path.parent
            report.items.extend(self._agents(directory / "agents", scope, default, config, report))
            hooks_file = directory / "hooks.json"
            hooks, ok = read_json(hooks_file, report.warnings)
            if ok and _hooks_are_valid(hooks, hooks_file, report.warnings):
                report.items.extend(
                    policy.items(self.id, hooks.get("hooks"), scope, default, hooks_file)
                )
            report.items.extend(policy.items(self.id, config.get("hooks"), scope, default, path))
        for plugin in loaded:
            report.items.extend(self._plugin_hooks(plugin, policy, report.warnings))
        return report

    def _agents(
        self, root: Path, scope: Scope, default: Status, config: dict, report: AgentReport
    ) -> list[Item]:
        settings = config.get("agents")
        off = isinstance(settings, dict) and settings.get("enabled") is False
        items = []
        for path in list_entries(root, report.warnings):
            if path.suffix != ".toml":
                continue
            definition = _read_toml(path, report.warnings)
            fields = definition or {}
            valid = all(
                isinstance(fields.get(key), str)
                for key in ("name", "description", "developer_instructions")
            )
            status = Status.DISABLED if off else default
            name = fields.get("name") if isinstance(fields.get("name"), str) else path.stem
            description = fields.get("description")
            items.append(
                Item(
                    name=name,
                    kind=Kind.SUBAGENT,
                    native_kind="agent",
                    agent=self.id,
                    scope=scope,
                    status=status if valid else Status.BROKEN,
                    description=description if isinstance(description, str) else "",
                    source_path=path,
                    resolved_path=Path(os.path.realpath(path)),
                    findings=[] if valid else [Finding(_AGENT_FIELDS)],
                )
            )
        return items

    def _plugin_hooks(self, plugin: Item, policy: "_HookPolicy", warnings: list[str]) -> list[Item]:
        root = _active_version(plugin.source_path)
        manifest = None if root is None else _plugin_manifest(root)
        if manifest is None:
            return []
        declared = manifest.get("hooks")
        # A manifest's path replaces the default file.
        relative = (
            os.path.normpath(declared)
            if isinstance(declared, str) and is_inside_plugin(declared)
            else "hooks/hooks.json"
        )
        hooks, ok = read_json(root / relative, warnings)
        if not ok or not _hooks_are_valid(hooks, root / relative, warnings):
            return []
        items = policy.items(
            self.id,
            hooks.get("hooks"),
            plugin.scope,
            Status.ENABLED,
            root / relative,
            key_source=f"{plugin.name}:{relative}",
            native_kind="plugin hook",
        )
        return [mark_bundled(item, plugin, item.name, "plugin hook") for item in items]

    def _plugin_skills(
        self, plugin: Item, disabled: set[str] | None, warnings: list[str]
    ) -> list[Item]:
        root = _active_version(plugin.source_path)
        manifest = None if root is None else _plugin_manifest(root)
        if manifest is None:
            return []
        # A manifest's paths replace the default, unless none of them is valid.
        paths = [path for path in listed_paths(manifest.get("skills")) if _is_skill_path(path)]
        # Without a name in the manifest, Codex uses the name of the version's directory.
        prefix = manifest.get("name") if isinstance(manifest.get("name"), str) else root.name
        items = []
        for path in paths or ["skills"]:
            found, scan_warnings = scan_skills(root / path, max_depth=_MAX_DEPTH)
            warnings.extend(scan_warnings)
            for skill in found:
                item = self._skill(skill, plugin.scope, disabled)
                items.append(
                    mark_bundled(item, plugin, f"{prefix}:{item.name}", prefix=f"{prefix}:")
                )
        return items

    def _skill(self, skill: FoundSkill, scope: Scope, disabled: set[str] | None) -> Item:
        fields = skill.fields or {}
        status = _skill_status(skill, disabled)
        return Item(
            name=fields.get("name") or skill.path.name,
            kind=Kind.SKILL,
            native_kind="skill",
            agent=self.id,
            scope=scope,
            status=status,
            description=fields.get("description", ""),
            source_path=skill.path,
            resolved_path=skill.resolved_path,
            findings=(
                [Finding(skill.problem or "has no description, which Codex requires")]
                if status is Status.BROKEN
                else []
            ),
        )

    def _mcp_servers(
        self, config: dict, scope: Scope, path: Path, default: Status, base: object = None
    ) -> list[Item]:
        servers = config.get("mcp_servers")
        items = []
        for name, entry in servers.items() if isinstance(servers, dict) else ():
            if isinstance(base, dict):
                entry = _merged(base.get(name), entry)
            details = _server_details(entry)
            if details is None:
                status, details = Status.BROKEN, {}
            else:
                status = _switched(entry.get("enabled", True), default)
            items.append(
                Item(
                    name=name,
                    kind=Kind.MCP,
                    native_kind="mcp server",
                    agent=self.id,
                    scope=scope,
                    status=status,
                    description="",
                    source_path=path,
                    resolved_path=Path(os.path.realpath(path)),
                    details=details,
                    findings=[INVALID_SERVER] if status is Status.BROKEN else [],
                )
            )
        return items

    def _plugins(
        self, config: dict, scope: Scope, path: Path, default: Status, codex_home: Path
    ) -> list[Item]:
        plugins = config.get("plugins")
        items = []
        for plugin_id, entry in plugins.items() if isinstance(plugins, dict) else ():
            if not isinstance(entry, dict):
                continue
            name, _, marketplace = plugin_id.partition("@")
            directory = codex_home / "plugins" / "cache" / marketplace / name
            cached = bool(marketplace) and os.path.isdir(directory)
            enabled = entry.get("enabled")
            if isinstance(enabled, bool):
                status = _switched(enabled, default)
            else:
                status = Status.UNKNOWN
            # Enabling a plugin that isn't on disk can't work.
            if status is Status.ENABLED and not cached:
                status = Status.BROKEN
            source = directory if cached else path
            items.append(
                Item(
                    name=plugin_id,
                    name_suffix=marketplace_suffix(plugin_id),
                    kind=Kind.PLUGIN,
                    native_kind="plugin",
                    agent=self.id,
                    scope=scope,
                    status=status,
                    description=_plugin_description(directory) if cached else "",
                    source_path=source,
                    resolved_path=Path(os.path.realpath(source)),
                    findings=[MISSING_PLUGIN] if status is Status.BROKEN else [],
                )
            )
        return items


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
    """Return a safe summary, or None if the entry has no valid transport."""
    if not isinstance(entry, dict):
        return None
    if isinstance(entry.get("command"), str):
        return stdio_details(entry["command"], entry.get("env"))
    if isinstance(entry.get("url"), str):
        return remote_details("streamable_http", entry["url"], entry.get("http_headers"))
    return None


class _HookPolicy:
    """Decides a hook's status from the user's config. A hook must be reviewed before it runs."""

    def __init__(self, user_config: dict | None) -> None:
        config = user_config or {}
        features = config.get("features")
        self.off = isinstance(features, dict) and features.get("hooks") is False
        hooks = config.get("hooks")
        state = hooks.get("state") if isinstance(hooks, dict) else None
        self.state = state if isinstance(state, dict) else {}

    def items(
        self,
        agent: str,
        events: object,
        scope: Scope,
        default: Status,
        path: Path,
        *,
        key_source: str | None = None,
        native_kind: str = "hook",
    ) -> list[Item]:
        items = []
        for handler in handlers(events):
            if handler.event == "state":
                continue
            entry = handler.entry if isinstance(handler.entry, dict) else {}
            kind = entry.get("type", "command")
            key = _hook_key(key_source or str(path), handler)
            state = self.state.get(key)
            state = state if isinstance(state, dict) else {}
            status = default
            finding = None
            if kind not in _SUPPORTED_HANDLERS:
                finding = Finding(f"Codex doesn't support {kind} hooks")
                status = Status.BROKEN
            elif self.off or state.get("enabled") is False:
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
            item = hook_item(agent, handler, scope, status, path, native_kind)
            if finding:
                item.status = status
                item.findings = [finding]
            items.append(item)
        return items


def _snake_case(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def _hook_key(source: str, handler: Handler) -> str:
    return f"{source}:{_snake_case(handler.event)}:{handler.group}:{handler.index}"


def _hook_hash(handler: Handler) -> str:
    """Reproduce how Codex fingerprints a hook, which is how it notices a changed one."""
    entry = handler.entry if isinstance(handler.entry, dict) else {}
    timeout = entry.get("timeout", _DEFAULT_HOOK_TIMEOUT)
    if entry.get("type") == "mcp_tool":
        normalized = {
            "type": "mcp_tool",
            "server": entry.get("server"),
            "tool": entry.get("tool"),
            "input": entry.get("input", {}),
            "timeout": timeout,
        }
    else:
        normalized = {
            "type": "command",
            "command": entry.get("command"),
            "timeout": timeout,
            "async": entry.get("async", False),
        }
        if entry.get("statusMessage") is not None:
            normalized["statusMessage"] = entry["statusMessage"]
    identity: dict[str, object] = {"event_name": _snake_case(handler.event), "hooks": [normalized]}
    if handler.matcher is not None:
        identity["matcher"] = handler.matcher
    text = json.dumps(identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return f"sha256:{hashlib.sha256(text.encode()).hexdigest()}"


def _hooks_are_valid(data: dict, path: Path, warnings: list[str]) -> bool:
    """Codex rejects a whole hooks file if one command handler can't parse."""
    for handler in handlers(data.get("hooks")):
        entry = handler.entry
        if not isinstance(entry, dict) or (
            entry.get("type", "command") == "command" and not isinstance(entry.get("command"), str)
        ):
            warnings.append(f"cannot read {path}: invalid hook entry")
            return False
    return True


def _mark_replaced_plugins(plugins_by_layer: list[list[Item]], project_path: Path) -> None:
    """A trusted project's entry decides, even when it turns the plugin off."""
    project = {item.name: item for item in plugins_by_layer[-1]}
    for item in plugins_by_layer[0] if len(plugins_by_layer) > 1 else ():
        replacement = project.get(item.name)
        if (
            replacement
            and item.status is Status.ENABLED
            and replacement.status in _ENTRY_NAME_TAKERS
        ):
            item.status = Status.SHADOWED
            item.findings.append(Finding("shadowed by the entry in", project_path))


def _is_skill_path(path: str) -> bool:
    return is_inside_plugin(path) and Path(path) != Path(".")


def _compare_versions(left: str, right: str) -> int:
    """Compare as semver when both are, otherwise as text, which is what Codex does."""
    left_match, right_match = _SEMVER.fullmatch(left), _SEMVER.fullmatch(right)
    if left_match and right_match:
        left_key = [int(part) for part in left_match.groups()[:3]]
        right_key = [int(part) for part in right_match.groups()[:3]]
        if left_key != right_key:
            return -1 if left_key < right_key else 1
        left_pre, right_pre = left_match[4], right_match[4]
        if left_pre is None or right_pre is None:
            return (left_pre is None) - (right_pre is None)
        left, right = left_pre, right_pre
    return (left > right) - (left < right)


def _active_version(directory: Path) -> Path | None:
    """Codex uses only the highest cached version, even when that one has no valid manifest."""
    try:
        versions = [name for name in os.listdir(directory) if os.path.isdir(directory / name)]
    except OSError:
        return None
    if not versions:
        return None
    return directory / max(versions, key=functools.cmp_to_key(_compare_versions))


def _plugin_manifest(root: Path) -> dict | None:
    path = root / _PLUGIN_MANIFEST
    manifest, ok = read_json(path, [])
    return manifest if ok and os.path.isfile(path) else None


def _plugin_description(directory: Path) -> str:
    """Read the active version's manifest. A bad one just means less detail."""
    root = _active_version(directory)
    manifest = None if root is None else _plugin_manifest(root)
    description = manifest.get("description") if manifest else None
    return description if isinstance(description, str) else ""


def _skill_status(skill: FoundSkill, disabled: set[str] | None) -> Status:
    if skill.broken or not (skill.fields or {}).get("description"):
        return Status.BROKEN
    if disabled is None:
        return Status.UNKNOWN
    if os.path.realpath(skill.skill_file) in disabled:
        return Status.DISABLED
    return Status.ENABLED


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


def _disabled_skill_files(config: dict) -> set[str]:
    """Return real paths of SKILL.md files disabled by `[[skills.config]]`."""
    skills = config.get("skills")
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
