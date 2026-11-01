"""OpenCode adapter.

Discovery rules, official references, and undocumented findings are in
docs/agents/opencode.md. Update that file when these rules change. tests/test_opencode_oracle.py
compares results with the real agent.
"""

import fnmatch
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

from agt_view.adapters.base import (
    Context,
    definition_item,
    first_existing,
    first_line,
    list_entries,
    load_once,
    mark_shadowed,
    mcp_item,
    plugin_item,
    read_json,
    skill_item,
    unique_roots,
)
from agt_view.mcp import remote_details, stdio_details, without_credentials
from agt_view.model import AgentReport, Finding, Item, Kind, Scope, Status
from agt_view.skills import (
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
_LOCAL_PLUGIN_PREFIXES = ("./", "../", "/", "~", "file://")
_SKILL_PLUGIN = "opencode.config.skill"
_MCP_PLUGIN = "opencode.config.mcp"
_COMPATIBILITY_PLUGIN = "opencode.config.compatibility"
_SKILL_SCAN_DEPTH = 5
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
        global_dir = (Path(xdg).expanduser() if xdg else default_base) / "opencode"
        moved = bool(xdg) and os.path.realpath(xdg) != os.path.realpath(default_base)
        extra_value = context.env.get("OPENCODE_CONFIG_DIR")
        extra_dir = Path(extra_value).expanduser() if extra_value else None
        project_dir = context.cwd / ".opencode"
        project_files = [context.cwd / "opencode.json", context.cwd / "opencode.jsonc"]

        config_path = first_existing(
            *([extra_dir] if extra_dir else []), global_dir, *project_files, project_dir
        )
        override_var = None
        if config_path is not None and config_path == extra_dir:
            override_var = "OPENCODE_CONFIG_DIR"
        elif moved and config_path == global_dir:
            override_var = "XDG_CONFIG_HOME"
        report = AgentReport(
            agent=self.id,
            installed=context.on_path("opencode"),
            config_path=config_path,
            override_var=override_var,
        )

        layers, reliable = self._layers(context, global_dir, extra_dir, project_dir, report)
        status = _SkillPolicy(layers, reliable)
        user_dirs = [global_dir, *([extra_dir] if extra_dir else [])]

        roots = [
            *[(Scope.USER, directory / "skills") for directory in user_dirs],
            *[(Scope.USER, directory / "skill") for directory in user_dirs],
            (Scope.PROJECT, project_dir / "skills"),
            (Scope.PROJECT, project_dir / "skill"),
        ]
        compatibility_roots = [
            (Scope.USER, context.home / ".agents" / "skills"),
            (Scope.PROJECT, context.cwd / ".agents" / "skills"),
        ]
        if not _is_set(
            context, "OPENCODE_DISABLE_CLAUDE_CODE", "OPENCODE_DISABLE_CLAUDE_CODE_SKILLS"
        ):
            compatibility_roots += [
                (Scope.USER, context.home / ".claude" / "skills"),
                (Scope.PROJECT, context.cwd / ".claude" / "skills"),
            ]
        roots += compatibility_roots
        for layer in layers:
            roots += [(layer.scope, path) for path in _configured_skill_paths(layer, context.cwd)]
        compatibility_skills: list[Item] = []
        skills_by_root: dict[Path, list[Item]] = {}
        for scope, root in unique_roots(roots):
            found = _skills_in_source(root, report.warnings)
            items = [skill_item(self.id, skill, scope, status.of(skill)) for skill in found]
            skills_by_root[root] = items
            report.items.extend(items)
            if (scope, root) in compatibility_roots:
                compatibility_skills.extend(items)
        report.items = load_once(report.items)

        report.items.extend(self._servers(layers, reliable))
        plugins = _PluginList(self.id, reliable)
        for scope, root in unique_roots(
            [
                *[(Scope.USER, directory / "plugins") for directory in user_dirs],
                *[(Scope.USER, directory / "plugin") for directory in user_dirs],
                (Scope.PROJECT, project_dir / "plugins"),
                (Scope.PROJECT, project_dir / "plugin"),
            ]
        ):
            plugins.discover(root, scope, report.warnings)
        for layer in layers:
            plugins.apply_config(layer)
        report.items.extend(plugins.items())
        _apply_built_in_removals(report.items, compatibility_skills, plugins)

        loaded = {id(item) for item in report.items}
        ranked: list[Item] = []
        for root in [
            *[
                path
                for layer in reversed(layers)
                for path in reversed(_configured_skill_paths(layer, context.cwd))
            ],
            project_dir / "skills",
            project_dir / "skill",
            *[directory / "skills" for directory in reversed(user_dirs)],
            *[directory / "skill" for directory in reversed(user_dirs)],
            context.cwd / ".agents" / "skills",
            context.home / ".agents" / "skills",
            context.cwd / ".claude" / "skills",
            context.home / ".claude" / "skills",
        ]:
            ranked.extend(
                _in_source_precedence(
                    [item for item in skills_by_root.pop(root, []) if id(item) in loaded]
                )
            )
        mark_shadowed(ranked, key=_skill_id)

        # A directory's Markdown files apply after the config files in that directory.
        places = [
            (global_dir, Scope.USER),
            (context.cwd, None),
            (project_dir, Scope.PROJECT),
            *([(extra_dir, Scope.USER)] if extra_dir else []),
        ]
        for kind in (Kind.SUBAGENT, Kind.COMMAND):
            report.items.extend(self._definitions(kind, places, layers, reliable, report))
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
            for root in (directory / plural, directory / singular):
                for found in find_markdown(root, report.warnings):
                    name = found.path.relative_to(root).with_suffix("").as_posix()
                    fields = found.fields or {}
                    off = kind is Kind.SUBAGENT and _is_turned_off(fields)
                    item = definition_item(
                        self.id,
                        found,
                        kind,
                        singular,
                        scope,
                        Status.DISABLED if off else Status.ENABLED,
                        name=name,
                        description=fields.get("description")
                        or (first_line(found.body) if kind is Kind.COMMAND else ""),
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
        extra_dir: Path | None,
        project_dir: Path,
        report: AgentReport,
    ) -> tuple[list[_Layer], bool]:
        """Read config files, lowest precedence first, and say if all could be read."""
        candidates = [
            (global_dir / "opencode.json", Scope.USER),
            (global_dir / "opencode.jsonc", Scope.USER),
        ]
        custom = context.env.get("OPENCODE_CONFIG")
        if custom:
            candidates.append((Path(custom).expanduser(), Scope.USER))
        if not _is_set(context, "OPENCODE_DISABLE_PROJECT_CONFIG"):
            candidates += [
                (context.cwd / "opencode.json", Scope.PROJECT),
                (context.cwd / "opencode.jsonc", Scope.PROJECT),
                (project_dir / "opencode.json", Scope.PROJECT),
                (project_dir / "opencode.jsonc", Scope.PROJECT),
            ]
        if extra_dir:
            candidates += [
                (extra_dir / "opencode.json", Scope.USER),
                (extra_dir / "opencode.jsonc", Scope.USER),
            ]
        managed = _managed_dir(context)
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
            status = _server_status(entry, layer.data, reliable)
            item = mcp_item(self.id, name, _server_details(entry), layer.scope, status, layer.path)
            if name in replaced:
                item.findings.append(Finding("overrides the entry in", replaced[name]))
            items.append(item)
        return items


@dataclass
class _Plugin:
    item: Item
    plugin_id: str | None


class _PluginList:
    """Plugins in load order: auto-discovered first, then config entries in sequence."""

    def __init__(self, agent: str, reliable: bool) -> None:
        self.agent = agent
        self.reliable = reliable
        self.plugins: dict[str, _Plugin] = {}
        self.removals: list[str] = []

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
        self.plugins[key or os.path.realpath(path)] = _Plugin(item, plugin_id)

    def _remove(self, pattern: str) -> None:
        self.removals.append(pattern)
        for plugin in self.plugins.values():
            if plugin.item.status is Status.BROKEN:
                continue
            if pattern == "*" or (plugin.plugin_id and _id_matches(plugin.plugin_id, pattern)):
                plugin.item.status = Status.DISABLED
            elif plugin.plugin_id is None:
                plugin.item.status = Status.UNKNOWN

    def removed(self, built_in_id: str) -> bool:
        return any(_id_matches(built_in_id, pattern) for pattern in self.removals)

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
    skills_off = plugins.removed(_SKILL_PLUGIN)
    servers_off = plugins.removed(_MCP_PLUGIN)
    for item in items:
        off = (item.kind is Kind.SKILL and skills_off) or (item.kind is Kind.MCP and servers_off)
        if off and item.status is not Status.BROKEN:
            item.status = Status.DISABLED
    if plugins.removed(_COMPATIBILITY_PLUGIN):
        for item in compatibility_skills:
            if item.status is not Status.BROKEN:
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
    return base / Path(source).expanduser()


def _plugin_entry_file(directory: Path) -> Path | None:
    for name in _PLUGIN_ENTRY_NAMES:
        for suffix in _PLUGIN_ENTRY_SUFFIXES:
            if os.path.isfile(directory / f"{name}{suffix}"):
                return directory / f"{name}{suffix}"
    return None


def _is_plugin_directory(directory: Path) -> bool:
    if _plugin_entry_file(directory) is not None:
        return True
    manifest, _ = read_json(directory / "package.json", [])
    return bool(manifest.get("name")) and "exports" in manifest


def _plugin_id(path: Path) -> str | None:
    """Find the `id` in a plugin's source text. The code is never run."""
    source = _plugin_entry_file(path) if os.path.isdir(path) else path
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


def _configured_skill_paths(layer: _Layer, cwd: Path) -> list[Path]:
    """Read `skills` as a list (V2) or `{paths}` (V1). URLs can't be listed from disk."""
    skills = layer.data.get("skills")
    entries = skills.get("paths") if isinstance(skills, dict) else skills
    paths = []
    for entry in entries if isinstance(entries, list) else ():
        if isinstance(entry, str) and "://" not in entry:
            paths.append(cwd / Path(entry).expanduser())
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


def _is_set(context: Context, *variables: str) -> bool:
    return any(context.env.get(variable) for variable in variables)


def _server_entries(data: dict) -> dict:
    """Return servers from `mcp.servers` (V2) or `mcp.<name>` (V1)."""
    mcp = data.get("mcp")
    if not isinstance(mcp, dict):
        return {}
    servers = mcp.get("servers")
    if isinstance(servers, dict) and "type" not in servers:
        return servers
    return mcp


def _server_status(entry: object, data: dict, reliable: bool) -> Status:
    if not reliable or not isinstance(entry, dict):
        return Status.UNKNOWN
    is_v2 = _server_entries(data) is not data.get("mcp")
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
