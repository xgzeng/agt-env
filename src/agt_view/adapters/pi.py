"""Pi adapter.

Discovery rules, official references, and undocumented findings are in
docs/agents/pi.md. Update that file when these rules change. tests/test_pi_oracle.py
compares results with the real agent.
"""

import fnmatch
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from agt_view.adapters.base import (
    Context,
    config_dir,
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
from agt_view.mcp import remote_details, stdio_details, without_credentials
from agt_view.model import AgentReport, Item, Scope, Status
from agt_view.skills import (
    SKILL_FILE,
    FoundSkill,
    read_skill,
    scan_markdown_skills,
    scan_skills,
)

_SKILL_SCAN_DEPTH = 32  # Discovery is recursive; this only bounds a pathological tree.
_EXTENSION_SUFFIXES = (".ts", ".js")
_MCP_EXTENSION = "builtin:mcp"


@dataclass
class _Resources:
    """A settings resource list: paths to add, and patterns that exclude."""

    base: Path
    paths: list[Path] = field(default_factory=list)
    excluded_paths: set[str] = field(default_factory=set)
    excluded_globs: list[str] = field(default_factory=list)

    def add(self, values: object) -> None:
        for value in values if isinstance(values, list) else ():
            if not isinstance(value, str) or not value:
                continue
            if value[0] == "!":
                self.excluded_globs.append(value[1:])
            elif value[0] == "-":
                self.excluded_paths.add(os.path.realpath(self._resolve(value[1:])))
            else:
                self.paths.append(self._resolve(value.removeprefix("+")))

    def _resolve(self, value: str) -> Path:
        return self.base / Path(value).expanduser()

    def excludes(self, path: Path, relative_to: Path) -> bool:
        if os.path.realpath(path) in self.excluded_paths:
            return True
        # Pi also matches the file inside a skill, so `!**/name/**` excludes that skill.
        candidates = [str(path), path.name, str(path / SKILL_FILE)]
        if path.is_relative_to(relative_to):
            candidates.append(str(path.relative_to(relative_to)))
        return any(
            fnmatch.fnmatchcase(candidate, pattern)
            for pattern in self.excluded_globs
            for candidate in candidates
        )


# Skill sources, highest precedence first, as tested against Pi. See docs/agents/pi.md.
_RANK_PROJECT_LISTED, _RANK_PI_SKILLS, _RANK_PROJECT_AGENTS = 0, 1, 2
_RANK_USER_LISTED, _RANK_AGENT_DIR, _RANK_USER_AGENTS = 3, 4, 5
# A turned-off project server still replaces the user's.
_SERVER_NAME_TAKERS = frozenset({Status.ENABLED, Status.DISABLED})


class Pi:
    id = "pi"

    def scan(self, context: Context) -> AgentReport:
        agent_dir, override = config_dir(
            context, "PI_CODING_AGENT_DIR", context.home / ".pi" / "agent"
        )
        project_dir = context.cwd / ".pi"
        config_path = first_existing(agent_dir, project_dir)
        report = AgentReport(
            agent=self.id,
            installed=context.on_path("pi"),
            config_path=config_path,
            override_var=override if config_path == agent_dir else None,
        )

        user_settings, user_ok = read_json(agent_dir / "settings.json", report.warnings)
        project_settings, project_ok = read_json(project_dir / "settings.json", report.warnings)
        reliable = user_ok and project_ok
        trust = _project_trust(agent_dir, context.cwd, user_settings, user_ok, report.warnings)
        # Project settings don't count until the project is trusted.
        layers = [(user_settings, agent_dir, Scope.USER, agent_dir / "settings.json")]
        if trust is not Status.DISABLED:
            layers.append(
                (project_settings, project_dir, Scope.PROJECT, project_dir / "settings.json")
            )

        report.items.extend(
            self._skills(context, agent_dir, project_dir, layers, trust, reliable, report)
        )
        report.items.extend(
            self._extensions(agent_dir, project_dir, layers, trust, reliable, report)
        )
        report.items.extend(self._packages(layers, trust, reliable))
        mcp_off = any(
            f"-{_MCP_EXTENSION}" in values
            for settings, _, _, _ in layers
            if isinstance(values := settings.get("extensions"), list)
        )
        report.items.extend(self._servers(agent_dir, project_dir, trust, mcp_off, reliable, report))
        return report

    def _skills(
        self, context, agent_dir, project_dir, layers, trust, reliable, report
    ) -> list[Item]:
        # Exclusions only apply to skills of the scope whose settings list them.
        bases = {Scope.USER: agent_dir, Scope.PROJECT: project_dir}
        resources = {scope: _Resources(base) for scope, base in bases.items()}
        found: list[tuple[FoundSkill, Scope, int]] = []
        ranks = {
            project_dir / "skills": _RANK_PI_SKILLS,
            context.cwd / ".agents" / "skills": _RANK_PROJECT_AGENTS,
            agent_dir / "skills": _RANK_AGENT_DIR,
            context.home / ".agents" / "skills": _RANK_USER_AGENTS,
        }
        for scope, root in unique_roots(
            [
                (Scope.USER, agent_dir / "skills"),
                (Scope.USER, context.home / ".agents" / "skills"),
                (Scope.PROJECT, project_dir / "skills"),
                (Scope.PROJECT, context.cwd / ".agents" / "skills"),
            ]
        ):
            skills, warnings = scan_skills(root, max_depth=_SKILL_SCAN_DEPTH)
            if root.parent.name != ".agents":
                standalone, more_warnings = scan_markdown_skills(root)
                skills += standalone
                warnings += more_warnings
            report.warnings.extend(warnings)
            found.extend((skill, scope, ranks[root]) for skill in skills)
        for settings, base, scope, _ in layers:
            listed = _Resources(base)
            listed.add(settings.get("skills"))
            resources[scope].excluded_paths |= listed.excluded_paths
            resources[scope].excluded_globs += listed.excluded_globs
            rank = _RANK_USER_LISTED if scope is Scope.USER else _RANK_PROJECT_LISTED
            for path in listed.paths:
                found.extend(
                    (skill, scope, rank) for skill in _listed_skills(path, report.warnings)
                )

        items = []
        rank_of: dict[int, int] = {}
        for skill, scope, rank in found:
            default = Status.ENABLED if scope is Scope.USER else trust
            status = _skill_status(skill, default, resources[scope], reliable, bases[scope])
            item = skill_item(
                self.id, skill, scope, status, "has no description, which Pi requires"
            )
            rank_of[id(item)] = rank
            items.append(item)
        items = load_once(items)
        # An untrusted project's skills don't load, so they can't take a name. In one source, the
        # later skill wins.
        competing = [i for i in reversed(items) if i.scope is Scope.USER or trust is Status.ENABLED]
        mark_shadowed(sorted(competing, key=lambda i: rank_of[id(i)]))
        return items

    def _extensions(self, agent_dir, project_dir, layers, trust, reliable, report) -> list[Item]:
        bases = {Scope.USER: agent_dir, Scope.PROJECT: project_dir}
        resources = {scope: _Resources(base) for scope, base in bases.items()}
        candidates: list[tuple[Path, Scope]] = []
        for path, scope in (
            (agent_dir / "extensions", Scope.USER),
            (project_dir / "extensions", Scope.PROJECT),
        ):
            candidates.extend((entry, scope) for entry in list_entries(path, report.warnings))
        for settings, base, scope, _ in layers:
            listed = _Resources(base)
            values = settings.get("extensions")
            # Built-in entries such as builtin:mcp aren't paths.
            listed.add(
                [v for v in values if isinstance(v, str) and "builtin:" not in v]
                if isinstance(values, list)
                else []
            )
            resources[scope].excluded_paths |= listed.excluded_paths
            resources[scope].excluded_globs += listed.excluded_globs
            candidates.extend((path, scope) for path in listed.paths)

        items = []
        seen: set[str] = set()
        for path, scope in candidates:
            real = os.path.realpath(path)
            if real in seen or not _is_extension(path):
                continue
            seen.add(real)
            default = Status.ENABLED if scope is Scope.USER else trust
            if not os.path.exists(path):
                status = Status.BROKEN
            elif resources[scope].excludes(path, bases[scope]):
                status = Status.DISABLED
            else:
                status = default if reliable else Status.UNKNOWN
            name = path.stem if path.suffix in _EXTENSION_SUFFIXES else path.name
            items.append(plugin_item(self.id, name, "extension", scope, status, path))
        return items

    def _packages(self, layers, trust, reliable) -> list[Item]:
        items = []
        for settings, _, scope, path in layers:
            packages = settings.get("packages")
            for package in packages if isinstance(packages, list) else ():
                source = package.get("source") if isinstance(package, dict) else package
                if not isinstance(source, str):
                    continue
                default = Status.ENABLED if scope is Scope.USER else trust
                status = default if reliable else Status.UNKNOWN
                items.append(
                    plugin_item(
                        self.id, without_credentials(source), "package", scope, status, path
                    )
                )
        return items

    def _servers(self, agent_dir, project_dir, trust, mcp_off, reliable, report) -> list[Item]:
        user_path, project_path = agent_dir / "mcp.json", project_dir / "mcp.json"
        user_data, user_ok = read_json(user_path, report.warnings)
        project_data, project_ok = read_json(project_path, report.warnings)
        user_servers = _server_entries(user_data)
        project_servers = _server_entries(project_data)

        # A project entry without a transport only adjusts the same-named user server.
        overrides = {
            name: entry
            for name, entry in project_servers.items()
            if isinstance(entry, dict) and not {"command", "url", "type"} & entry.keys()
        }
        user_items, project_items = [], []
        for name, entry in user_servers.items():
            enabled = entry.get("enabled", True) if isinstance(entry, dict) else True
            if name in overrides and trust is Status.ENABLED:
                enabled = overrides[name].get("enabled", enabled)
            status = _server_status(enabled, Status.ENABLED, mcp_off, reliable and user_ok)
            user_items.append(
                mcp_item(self.id, name, _server_details(entry), Scope.USER, status, user_path)
            )
        for name, entry in project_servers.items():
            if name in overrides and name in user_servers:
                continue
            enabled = entry.get("enabled", True) if isinstance(entry, dict) else True
            status = _server_status(enabled, trust, mcp_off, reliable and project_ok)
            project_items.append(
                mcp_item(self.id, name, _server_details(entry), Scope.PROJECT, status, project_path)
            )
        if trust is Status.ENABLED:
            mark_shadowed([*project_items, *user_items], winners=_SERVER_NAME_TAKERS)
        return [*user_items, *project_items]


def _server_entries(data: dict) -> dict:
    servers = data.get("mcpServers")
    return servers if isinstance(servers, dict) else {}


def _server_status(enabled: object, default: Status, mcp_off: bool, reliable: bool) -> Status:
    if not reliable or not isinstance(enabled, bool):
        return Status.UNKNOWN
    if mcp_off or not enabled:
        return Status.DISABLED
    return default


def _listed_skills(path: Path, warnings: list[str]) -> list[FoundSkill]:
    """Resolve a settings entry: a SKILL.md, a skill directory, or a directory of skills."""
    if path.name == SKILL_FILE:
        path = path.parent
    if os.path.lexists(path / SKILL_FILE):
        return [read_skill(path, warnings)]
    skills, scan_warnings = scan_skills(path, max_depth=_SKILL_SCAN_DEPTH)
    warnings.extend(scan_warnings)
    return skills


def _skill_status(
    skill: FoundSkill, default: Status, resources: _Resources, reliable: bool, base: Path
) -> Status:
    if skill.broken or not (skill.fields or {}).get("description"):
        return Status.BROKEN
    if resources.excludes(skill.path, base):
        return Status.DISABLED
    return default if reliable else Status.UNKNOWN


def _is_extension(path: Path) -> bool:
    if path.suffix in _EXTENSION_SUFFIXES and (os.path.isfile(path) or not os.path.exists(path)):
        return True
    if not os.path.isdir(path):
        return False
    if any(os.path.isfile(path / f"index{suffix}") for suffix in _EXTENSION_SUFFIXES):
        return True
    return _declares_extensions(path / "package.json")


def _declares_extensions(manifest: Path) -> bool:
    try:
        with open(manifest, encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, ValueError):
        return False
    pi = data.get("pi") if isinstance(data, dict) else None
    return isinstance(pi, dict) and bool(pi.get("extensions"))


def _project_trust(
    agent_dir: Path, cwd: Path, user_settings: dict, user_ok: bool, warnings: list[str]
) -> Status:
    """A saved decision for this or the closest parent directory beats the default."""
    saved, saved_ok = read_json(agent_dir / "trust.json", warnings)
    if not saved_ok or not user_ok:
        return Status.UNKNOWN
    current = Path(os.path.realpath(cwd))
    for directory in (current, *current.parents):
        decision = saved.get(str(directory))
        if isinstance(decision, bool):
            return Status.ENABLED if decision else Status.DISABLED
    default = user_settings.get("defaultProjectTrust")
    if default == "always":
        return Status.ENABLED
    return Status.DISABLED if default == "never" else Status.PENDING


def _server_details(entry: object) -> dict[str, object] | None:
    if not isinstance(entry, dict):
        return None
    if isinstance(entry.get("command"), str):
        return stdio_details(entry["command"], entry.get("env"))
    if isinstance(entry.get("url"), str):
        return remote_details("streamable_http", entry["url"], entry.get("headers"))
    return None
