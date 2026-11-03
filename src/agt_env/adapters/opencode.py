"""OpenCode adapter.

Discovery rules, official references, and undocumented findings are in
docs/agents/opencode.md. Update that file when these rules change. tests/test_opencode_oracle.py
compares results with the real agent.
"""

import fnmatch
import getpass
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

from agt_env.adapters.base import (
    Context,
    definition_item,
    first_existing,
    list_entries,
    load_once,
    mark_shadowed,
    mcp_item,
    plugin_item,
    read_json,
    skill_item,
    unique_roots,
)
from agt_env.mcp import remote_details, stdio_details, without_credentials
from agt_env.model import AgentReport, Finding, Item, Kind, Scope, Status
from agt_env.skills import (
    SKILL_FILE,
    FoundSkill,
    find_markdown,
    read_skill,
    scan_markdown_skills,
    scan_skills,
)

_PLUGIN_SUFFIXES = (".js", ".ts")
_PLUGIN_ENTRY_NAMES = ("server", "index")
_PLUGIN_ENTRY_SUFFIXES = (".ts", ".tsx", ".js")
_PLUGIN_ID = re.compile(r"""\bid\s*:\s*["']([^"']+)["']""")
_MAX_PLUGIN_READ_BYTES = 64 * 1024
_GUI_ENTRY_FILES = ("gui-experimental.cjs", "gui-experimental.js")
_LOCAL_PLUGIN_PREFIXES = ("./", "../", "/", "file://")
_SKILL_PLUGIN = "opencode.config.skill"
_MCP_PLUGIN = "opencode.config.mcp"
_COMPATIBILITY_PLUGIN = "opencode.config.compatibility"
_AGENT_PLUGIN = "opencode.config.agent"
_COMMAND_PLUGIN = "opencode.config.command"
_BUILT_INS = (_SKILL_PLUGIN, _MCP_PLUGIN, _COMPATIBILITY_PLUGIN, _AGENT_PLUGIN, _COMMAND_PLUGIN)
_PREFERENCES_FILE = "ai.opencode.managed.plist"
_PREFERENCE_METADATA = frozenset(
    {
        "PayloadDisplayName",
        "PayloadIdentifier",
        "PayloadType",
        "PayloadUUID",
        "PayloadVersion",
        "_manualProfile",
    }
)
_SKILL_SCAN_DEPTH = 6
_SKILL_SCAN_IGNORED = frozenset({"node_modules"})
_DECISIONS = {"allow": Status.ENABLED, "ask": Status.ENABLED, "deny": Status.DISABLED}


@dataclass
class _Layer:
    path: Path
    scope: Scope
    data: dict


class OpenCode:
    id = "opencode"

    def scan(self, context: Context) -> AgentReport:
        xdg = context.env.get("XDG_CONFIG_HOME")
        default_base = context.home / ".config"
        xdg_dir = (Path(xdg) if xdg else default_base) / "opencode"
        moved = bool(xdg) and os.path.realpath(xdg) != os.path.realpath(default_base)
        # This replaces the global directory, whatever the docs say.
        moved_value = context.env.get("OPENCODE_CONFIG_DIR")
        global_dir = Path(moved_value) if moved_value else xdg_dir
        project_on = not _project_config_is_off(context)
        project_dir = context.cwd / ".opencode"
        project_files = [context.cwd / "opencode.json", context.cwd / "opencode.jsonc"]
        custom = _custom_config(context)
        managed = _managed_dir(context)
        claude_on = not (
            _is_flag(context, "OPENCODE_DISABLE_CLAUDE_CODE")
            or _is_flag(context, "OPENCODE_DISABLE_CLAUDE_CODE_SKILLS")
        )

        config_path = first_existing(
            *([global_dir] if moved_value else []),
            *([custom] if custom else []),
            global_dir,
            *([*project_files, project_dir] if project_on else []),
            context.home / ".agents" / "skills",
            *([context.cwd / ".agents" / "skills"] if project_on else []),
        )
        override_var = None
        if config_path is not None and config_path == custom:
            override_var = "OPENCODE_CONFIG"
        elif moved_value and config_path == global_dir:
            override_var = "OPENCODE_CONFIG_DIR"
        elif moved and config_path == global_dir:
            override_var = "XDG_CONFIG_HOME"
        report = AgentReport(
            agent=self.id,
            installed=context.on_path("opencode"),
            config_path=config_path,
            override_var=override_var,
        )

        layers, reliable = self._layers(
            context, global_dir, custom, project_dir, managed, project_on, report
        )
        status = _SkillPolicy(layers, reliable)

        # Highest precedence first, so a skill linked from two sources keeps the higher one's row.
        native_roots = [
            *[
                (layer.scope, path)
                for layer in reversed(layers)
                for path in reversed(_configured_skill_paths(layer, context))
            ],
            (Scope.SYSTEM, managed / "skills"),
            (Scope.SYSTEM, managed / "skill"),
            *(
                [(Scope.PROJECT, project_dir / "skills"), (Scope.PROJECT, project_dir / "skill")]
                if project_on
                else []
            ),
            (Scope.USER, global_dir / "skills"),
            (Scope.USER, global_dir / "skill"),
        ]
        compatibility_roots = [
            *([(Scope.PROJECT, context.cwd / ".agents" / "skills")] if project_on else []),
            (Scope.USER, context.home / ".agents" / "skills"),
            *(
                [(Scope.PROJECT, context.cwd / ".claude" / "skills")]
                if claude_on and project_on
                else []
            ),
            *([(Scope.USER, context.home / ".claude" / "skills")] if claude_on else []),
        ]
        compatibility_skills: list[Item] = []
        skills: list[Item] = []
        ranked_roots = unique_roots([*native_roots, *compatibility_roots])
        skills_by_root: dict[Path, list[Item]] = {}
        for scope, root in ranked_roots:
            found = _skills_in_source(root, report.warnings)
            items = [skill_item(self.id, skill, scope, status.of(skill)) for skill in found]
            skills_by_root[root] = items
            skills.extend(items)
            if (scope, root) in compatibility_roots:
                compatibility_skills.extend(items)
        report.items = load_once(skills)

        report.items.extend(self._servers(layers, reliable))
        plugins = _PluginList(self.id, reliable)
        for scope, root in unique_roots(
            [
                (Scope.USER, global_dir / "plugins"),
                (Scope.USER, global_dir / "plugin"),
                *(
                    [
                        (Scope.PROJECT, project_dir / "plugins"),
                        (Scope.PROJECT, project_dir / "plugin"),
                    ]
                    if project_on
                    else []
                ),
                (Scope.SYSTEM, managed / "plugins"),
                (Scope.SYSTEM, managed / "plugin"),
            ]
        ):
            plugins.discover(root, scope, report.warnings)
        for layer in layers:
            plugins.apply_config(layer)
        report.items.extend(plugins.items())

        # A directory's Markdown files apply after the config files in that directory.
        places = [
            (global_dir, Scope.USER),
            (context.cwd, None),
            *([(project_dir, Scope.PROJECT)] if project_on else []),
            (managed, Scope.SYSTEM),
        ]
        for kind in (Kind.SUBAGENT, Kind.COMMAND):
            report.items.extend(self._definitions(kind, places, layers, reliable, report))
        _apply_built_in_removals(report.items, compatibility_skills, plugins)

        # What a removed built-in provided no longer takes a skill's ID.
        loaded = {id(item) for item in report.items}
        ranked: list[Item] = []
        for _, root in ranked_roots:
            ranked.extend(
                _in_source_precedence([item for item in skills_by_root[root] if id(item) in loaded])
            )
        mark_shadowed(ranked, key=_skill_id)
        return report

    def _definitions(
        self,
        kind: Kind,
        places: list[tuple[Path, Scope | None]],
        layers: list[_Layer],
        reliable: bool,
        report: AgentReport,
    ) -> list[Item]:
        """Combine Markdown files and config entries. A later definition replaces an earlier one."""
        singular, plural = ("agent", "agents") if kind is Kind.SUBAGENT else ("command", "commands")
        positions = {directory: index for index, (directory, _) in enumerate(places)}
        ordered: list[tuple[float, str, Item]] = []
        for layer in layers:
            # A config file named by a variable comes after the global directory's.
            position = positions.get(layer.path.parent, 0.75 if layer.scope is Scope.USER else 99)
            for key in (singular, plural):
                entries = layer.data.get(key)
                for name, entry in entries.items() if isinstance(entries, dict) else ():
                    item = _config_definition(self.id, kind, singular, name, entry, layer)
                    ordered.append((position, name, item))
        for directory, scope in places:
            if scope is None:
                continue
            # Files apply in sorted order, so the plural directory beats the singular one. Modes
            # come last, and only directly in their directory.
            sources = [(directory / singular, singular, True), (directory / plural, singular, True)]
            if kind is Kind.SUBAGENT:
                sources += [
                    (directory / "mode", "mode", False),
                    (directory / "modes", "mode", False),
                ]
            for root, native_kind, recursive in sources:
                for found in find_markdown(root, report.warnings, recursive=recursive):
                    name = found.path.relative_to(root).with_suffix("").as_posix()
                    fields = found.fields or {}
                    off = kind is Kind.SUBAGENT and _is_turned_off(fields)
                    item = definition_item(
                        self.id,
                        found,
                        kind,
                        native_kind,
                        scope,
                        Status.DISABLED if off else Status.ENABLED,
                        name=name,
                        description=fields.get("description") or "",
                    )
                    ordered.append((positions[directory] + 0.5, name, item))
        ordered.sort(key=lambda entry: entry[0])

        winners: dict[str, Item] = {}
        replaced: dict[str, Path] = {}
        for _, name, item in ordered:
            if name in winners and item.status is not Status.BROKEN:
                replaced[name] = winners[name].source_path
                item.description = item.description or winners[name].description
                winners[name] = item
            elif name not in winners:
                winners[name] = item
        for name, item in winners.items():
            if not reliable and item.status is Status.ENABLED:
                item.status = Status.UNKNOWN
            if name in replaced:
                item.findings.append(Finding("overrides the entry in", replaced[name]))
        return list(winners.values())

    def _layers(
        self,
        context: Context,
        global_dir: Path,
        custom: Path | None,
        project_dir: Path,
        managed: Path,
        project_on: bool,
        report: AgentReport,
    ) -> tuple[list[_Layer], bool]:
        """Read config files, lowest precedence first, and say if all could be read."""
        candidates = [
            (global_dir / "opencode.json", Scope.USER),
            (global_dir / "opencode.jsonc", Scope.USER),
        ]
        if custom:
            candidates.append((custom, Scope.USER))
        if project_on:
            candidates += [
                (context.cwd / "opencode.json", Scope.PROJECT),
                (context.cwd / "opencode.jsonc", Scope.PROJECT),
                (project_dir / "opencode.json", Scope.PROJECT),
                (project_dir / "opencode.jsonc", Scope.PROJECT),
            ]
        candidates += [
            (managed / "opencode.json", Scope.SYSTEM),
            (managed / "opencode.jsonc", Scope.SYSTEM),
        ]

        layers = []
        reliable = True
        seen: set[str] = set()
        for path, scope in candidates:
            real = os.path.realpath(path)
            if real in seen:
                continue
            seen.add(real)
            data, ok = read_json(path, report.warnings, jsonc=True)
            reliable = reliable and ok
            if data:
                layers.append(_Layer(path, scope, data))
        preferences = _managed_preferences(context, report.warnings)
        if preferences:
            layers.append(preferences)
        return layers, reliable

    def _servers(self, layers: list[_Layer], reliable: bool) -> list[Item]:
        """Combine servers across layers. A later valid entry replaces an earlier one."""
        winners: dict[str, tuple[object, _Layer]] = {}
        replaced: dict[str, Path] = {}
        for layer in layers:
            for name, entry in _server_entries(layer.data).items():
                if name not in winners or _server_details(entry) is not None:
                    if name in winners:
                        replaced[name] = winners[name][1].path
                    winners[name] = (entry, layer)
        items = []
        for name, (entry, layer) in winners.items():
            status = _server_status(entry, _is_native_server(layer.data, name), reliable)
            item = mcp_item(self.id, name, _server_details(entry), layer.scope, status, layer.path)
            if name in replaced:
                item.findings.append(Finding("overrides the entry in", replaced[name]))
            items.append(item)
        return items


@dataclass
class _Plugin:
    item: Item
    plugin_id: str | None
    status: Status  # Before any removal.


class _PluginList:
    """Plugins in load order: auto-discovered first, then config entries in sequence."""

    def __init__(self, agent: str, reliable: bool) -> None:
        self.agent = agent
        self.reliable = reliable
        self.plugins: dict[str, _Plugin] = {}
        self.built_ins = dict.fromkeys(_BUILT_INS, True)

    def discover(self, root: Path, scope: Scope, warnings: list[str]) -> None:
        for path in list_entries(root, warnings):
            if os.path.isdir(path):
                if _is_plugin_directory(path):
                    self._add(path.name, "plugin", scope, Status.ENABLED, path)
            elif path.suffix in _PLUGIN_SUFFIXES:
                status = Status.ENABLED if os.path.exists(path) else Status.BROKEN
                self._add(path.stem, "plugin", scope, status, path)

    def apply_config(self, layer: _Layer) -> None:
        for key in ("plugin", "plugins"):
            entries = layer.data.get(key)
            for entry in entries if isinstance(entries, list) else ():
                source = _plugin_source(entry)
                if source is None:
                    continue
                if source.startswith("-"):
                    self._remove(source[1:])
                elif self._select(source):
                    continue
                elif source.startswith(_LOCAL_PLUGIN_PREFIXES):
                    path = _local_plugin_path(source, layer.path.parent)
                    # A configured local path must be a plugin directory; a file is skipped.
                    valid = os.path.isdir(path) and _is_plugin_directory(path)
                    status = Status.ENABLED if valid else Status.BROKEN
                    self._add(path.name, "plugin", layer.scope, status, path)
                else:
                    name = without_credentials(source)
                    self._add(name, "npm plugin", layer.scope, Status.UNKNOWN, layer.path, key=name)

    def _add(
        self,
        name: str,
        native_kind: str,
        scope: Scope,
        status: Status,
        path: Path,
        key: str | None = None,
    ) -> None:
        item = plugin_item(self.agent, name, native_kind, scope, status, path)
        is_package = key is not None
        plugin_id = None if is_package else _plugin_id(path)
        self.plugins[key or os.path.realpath(path)] = _Plugin(item, plugin_id, status)

    def _remove(self, pattern: str) -> None:
        for built_in in self.built_ins:
            if _id_matches(built_in, pattern):
                self.built_ins[built_in] = False
        for plugin in self.plugins.values():
            if plugin.item.status is Status.BROKEN:
                continue
            if pattern == "*" or (plugin.plugin_id and _id_matches(plugin.plugin_id, pattern)):
                plugin.item.status = Status.DISABLED
            elif plugin.plugin_id is None:
                plugin.item.status = Status.UNKNOWN

    def _select(self, pattern: str) -> bool:
        """Turn back on what a pattern matches. Say whether it selects plugins, not a package."""
        selects = pattern == "*" or pattern.endswith(".*") or pattern.startswith("opencode.")
        for built_in in self.built_ins:
            if _id_matches(built_in, pattern):
                self.built_ins[built_in] = True
                selects = True
        for plugin in self.plugins.values():
            if pattern == "*" or (plugin.plugin_id and _id_matches(plugin.plugin_id, pattern)):
                plugin.item.status = plugin.status
                selects = True
        return selects

    def removed(self, built_in_id: str) -> bool:
        return not self.built_ins[built_in_id]

    def items(self) -> list[Item]:
        items = [plugin.item for plugin in self.plugins.values()]
        if not self.reliable:
            for item in items:
                if item.status is Status.ENABLED:
                    item.status = Status.UNKNOWN
        return items


def _apply_built_in_removals(
    items: list[Item], compatibility_skills: list[Item], plugins: _PluginList
) -> None:
    """Each kind of item on disk comes from a built-in plugin, which config can remove."""
    compatibility = {id(item) for item in compatibility_skills}
    for item in items:
        if item.status is Status.BROKEN:
            continue
        if item.kind is Kind.SKILL:
            owner = _COMPATIBILITY_PLUGIN if id(item) in compatibility else _SKILL_PLUGIN
        elif item.kind is Kind.MCP:
            owner = _MCP_PLUGIN
        elif item.kind is Kind.SUBAGENT:
            owner = _AGENT_PLUGIN
        elif item.kind is Kind.COMMAND:
            owner = _COMMAND_PLUGIN
        else:
            continue
        if plugins.removed(owner):
            item.status = Status.DISABLED


def _id_matches(plugin_id: str, pattern: str) -> bool:
    if pattern == "*":
        return True
    if pattern.endswith(".*"):
        return plugin_id.startswith(pattern[:-1])
    return plugin_id == pattern


def _plugin_source(entry: object) -> str | None:
    """Return the name or path from a string, `[name, options]`, or `{package, options}`."""
    if isinstance(entry, list) and entry:
        entry = entry[0]
    elif isinstance(entry, dict):
        entry = entry.get("package")
    return entry if isinstance(entry, str) and entry else None


def _local_plugin_path(source: str, base: Path) -> Path:
    if source.startswith("file://"):
        return Path(unquote(urlsplit(source).path))
    return base / source


def _plugin_entry_file(directory: Path) -> Path | None:
    for name in _PLUGIN_ENTRY_NAMES:
        for suffix in _PLUGIN_ENTRY_SUFFIXES:
            if os.path.isfile(directory / f"{name}{suffix}"):
                return directory / f"{name}{suffix}"
    return None


def _is_plugin_directory(directory: Path) -> bool:
    if _plugin_entry_file(directory) is not None:
        return True
    if any(os.path.isfile(directory / name) for name in _GUI_ENTRY_FILES):
        return True
    manifest, _ = read_json(directory / "package.json", [])
    return bool(manifest.get("name")) and "exports" in manifest


def _plugin_id(path: Path) -> str | None:
    """Find the `id` in a plugin's source text. The code is never run."""
    source = _plugin_entry_file(path) if os.path.isdir(path) else path
    if source is None and any(os.path.isfile(path / name) for name in _GUI_ENTRY_FILES):
        # Without a server part, a plugin's ID is its package name or directory name.
        manifest, _ = read_json(path / "package.json", [])
        name = manifest.get("name")
        return name if isinstance(name, str) and name else path.name
    if source is None or not os.path.isfile(source):
        return None
    try:
        with open(source, "rb") as file:
            text = file.read(_MAX_PLUGIN_READ_BYTES).decode("utf-8", errors="replace")
    except OSError:
        return None
    match = _PLUGIN_ID.search(text)
    return match[1] if match else None


def _skill_id(item: Item) -> str:
    """OpenCode matches skills by directory or file name, not by the `name` field."""
    path = item.source_path
    return path.stem if path.suffix == ".md" else path.name


def _in_source_precedence(items: list[Item]) -> list[Item]:
    """In one source, a directory beats a same-named file, and a later directory beats an earlier."""
    files = [item for item in items if item.source_path.suffix == ".md"]
    directories = [item for item in items if item.source_path.suffix != ".md"]
    return [*reversed(directories), *reversed(files)]


def _skills_in_source(root: Path, warnings: list[str]) -> list[FoundSkill]:
    """Return the skills in a source, which can itself be one skill directory."""
    if os.path.lexists(root / SKILL_FILE):
        return [read_skill(root, warnings)]
    found, scan_warnings = scan_skills(
        root,
        max_depth=_SKILL_SCAN_DEPTH,
        ignore=_SKILL_SCAN_IGNORED,
        search_inside_skills=False,
    )
    warnings.extend(scan_warnings)
    standalone, markdown_warnings = scan_markdown_skills(root)
    warnings.extend(markdown_warnings)
    return found + standalone


def _configured_skill_paths(layer: _Layer, context: Context) -> list[Path]:
    """Read `skills` as a list (V2) or `{paths}` (V1). URLs can't be listed from disk."""
    skills = layer.data.get("skills")
    entries = skills.get("paths") if isinstance(skills, dict) else skills
    paths = []
    for entry in entries if isinstance(entries, list) else ():
        if isinstance(entry, str) and "://" not in entry:
            if entry.startswith("~/"):
                paths.append(context.home / entry[2:])
            else:
                paths.append(context.cwd / entry)
    return paths


class _SkillPolicy:
    """Skill rules from `permissions` (V2) and `permission.skill` (V1). The last match wins."""

    def __init__(self, layers: list[_Layer], reliable: bool) -> None:
        self.reliable = reliable
        self.rules: list[tuple[str, object]] = []
        for layer in layers:
            rules = layer.data.get("permissions")
            for rule in rules if isinstance(rules, list) else ():
                if isinstance(rule, dict) and rule.get("action") in ("skill", "*"):
                    self.rules.append((str(rule.get("resource", "*")), rule.get("effect")))
            permission = layer.data.get("permission")
            skill = permission.get("skill") if isinstance(permission, dict) else None
            if isinstance(skill, dict):
                self.rules.extend(skill.items())
            elif isinstance(skill, str):
                self.rules.append(("*", skill))

    def of(self, skill: FoundSkill) -> Status:
        if skill.broken:
            return Status.BROKEN
        if not self.reliable:
            return Status.UNKNOWN
        decision: object = "allow"
        for pattern, value in self.rules:
            if fnmatch.fnmatchcase(skill.default_name, pattern):
                decision = value
        return (
            _DECISIONS.get(decision, Status.UNKNOWN)
            if isinstance(decision, str)
            else Status.UNKNOWN
        )


def _is_turned_off(fields: dict) -> bool:
    """`disabled` is the V2 field and `disable` the V1 one. Markdown values are text."""
    return any(fields.get(key) in (True, "true") for key in ("disabled", "disable"))


def _config_definition(
    agent: str, kind: Kind, native_kind: str, name: str, entry: object, layer: _Layer
) -> Item:
    fields = entry if isinstance(entry, dict) else {}
    # OpenCode drops a config command with no template.
    invalid = not isinstance(entry, dict) or (
        kind is Kind.COMMAND and not isinstance(fields.get("template"), str)
    )
    description = fields.get("description")
    status = Status.DISABLED if kind is Kind.SUBAGENT and _is_turned_off(fields) else Status.ENABLED
    return Item(
        name=name,
        kind=kind,
        native_kind=native_kind,
        agent=agent,
        scope=layer.scope,
        status=Status.BROKEN if invalid else status,
        description=description if isinstance(description, str) else "",
        source_path=layer.path,
        resolved_path=Path(os.path.realpath(layer.path)),
        findings=[Finding(f"invalid {native_kind} entry")] if invalid else [],
    )


def _is_flag(context: Context, variable: str) -> bool:
    return _is_true(context.env.get(variable))


def _is_true(value: str | None) -> bool:
    return value is not None and value.lower() in ("1", "true")


def _project_config_is_off(context: Context) -> bool:
    """The newer name decides whenever it's set, even to something false."""
    value = context.env.get("OPENCODE_CONFIG_PROJECT_DISABLE")
    return _is_true(
        value if value is not None else context.env.get("OPENCODE_DISABLE_PROJECT_CONFIG")
    )


def _custom_config(context: Context) -> Path | None:
    """OpenCode's service, not the current directory, resolves a relative path, so it's ignored."""
    value = context.env.get("OPENCODE_CONFIG")
    return Path(value) if value and os.path.isabs(value) else None


def _is_direct_legacy_server(value: object) -> bool:
    return isinstance(value, dict) and value.get("type") in ("local", "remote")


def _legacy_server_entries(mcp: dict) -> dict:
    entries = {}
    for name, value in mcp.items():
        if name in ("servers", "timeout") and not _is_direct_legacy_server(value):
            continue
        # OpenCode drops an entry with only `enabled` and no `type`.
        if (
            isinstance(value, dict)
            and "type" not in value
            and isinstance(value.get("enabled"), bool)
        ):
            continue
        entries[name] = value
    return entries


def _native_server_entries(mcp: dict) -> dict:
    servers = mcp.get("servers")
    return servers if isinstance(servers, dict) and not _is_direct_legacy_server(servers) else {}


def _server_entries(data: dict) -> dict:
    """Merge servers from `mcp.<name>` (V1) and `mcp.servers` (V2). V2 wins a name in both."""
    mcp = data.get("mcp")
    if not isinstance(mcp, dict):
        return {}
    return {**_legacy_server_entries(mcp), **_native_server_entries(mcp)}


def _is_native_server(data: dict, name: str) -> bool:
    mcp = data.get("mcp")
    return isinstance(mcp, dict) and name in _native_server_entries(mcp)


def _server_status(entry: object, is_v2: bool, reliable: bool) -> Status:
    if not reliable or not isinstance(entry, dict):
        return Status.UNKNOWN
    if is_v2:
        off = entry.get("disabled", False)
    else:
        off = (
            not entry["enabled"]
            if isinstance(entry.get("enabled"), bool)
            else entry.get("disabled", False)
        )
    if not isinstance(off, bool):
        return Status.UNKNOWN
    return Status.DISABLED if off else Status.ENABLED


def _server_details(entry: object) -> dict[str, object] | None:
    if not isinstance(entry, dict):
        return None
    if entry.get("type") == "local":
        command = entry.get("command")
        if isinstance(command, list) and command and isinstance(command[0], str):
            return stdio_details(command[0], entry.get("environment"))
    if entry.get("type") == "remote" and isinstance(entry.get("url"), str):
        return remote_details("remote", entry["url"], entry.get("headers"))
    return None


def _managed_dir(context: Context) -> Path:
    if sys.platform == "darwin":
        return context.system_root / "Library" / "Application Support" / "opencode"
    return context.system_root / "etc" / "opencode"


def _managed_preferences(context: Context, warnings: list[str]) -> _Layer | None:
    """Read macOS MDM preferences, which outrank the system directory. The first present counts."""
    if sys.platform != "darwin":
        return None
    root = context.system_root / "Library" / "Managed Preferences"
    for path in (root / getpass.getuser() / _PREFERENCES_FILE, root / _PREFERENCES_FILE):
        if not os.path.isfile(path):
            continue
        import plistlib  # Only here, because it's slow to import and only macOS needs it.

        try:
            with open(path, "rb") as file:
                data = plistlib.load(file)
        except (OSError, ValueError, plistlib.InvalidFileException) as error:
            warnings.append(f"cannot read {path}: {error}")
            return None
        if not isinstance(data, dict):
            return None
        config = {key: value for key, value in data.items() if key not in _PREFERENCE_METADATA}
        return _Layer(path, Scope.SYSTEM, config)
    return None
