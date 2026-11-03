"""Pi adapter.

Discovery rules, official references, and undocumented findings are in
docs/agents/pi.md. Update that file when these rules change. tests/test_pi_oracle.py
compares results with the real agent.
"""

import fnmatch
import glob
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from agt_env.adapters.base import (
    Context,
    config_dir,
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
from agt_env.mcp import remote_details, stdio_details, without_credentials
from agt_env.model import AgentReport, Finding, Item, Kind, Scope, Status
from agt_env.skills import (
    SKILL_FILE,
    FoundSkill,
    find_markdown,
    read_markdown,
    scan_markdown_skills,
    scan_skills,
    skills_at,
)

_SKILL_SCAN_DEPTH = 32  # Discovery is recursive; this only bounds a pathological tree.
_EXTENSION_SUFFIXES = (".ts", ".js")
_MCP_EXTENSION = "builtin:mcp"
_RESOURCE_KEYS = frozenset({"extensions", "skills", "prompts", "themes"})
_SERVER_NAME = re.compile(r"[A-Za-z0-9_-]+")
_GIT_URL = re.compile(r"(?:https?|ssh|git)://", re.IGNORECASE)
_DESCRIPTION_LIMIT = 60
_HTTP_TYPES = (None, "http", "streamable-http")
# A project entry with only these adjusts the user's server of the same name.
_OVERRIDE_KEYS = frozenset({"enabled", "exposure", "toolExposure"})


@dataclass
class _Resources:
    """A settings resource list: paths to add, and patterns that exclude."""

    base: Path
    home: Path
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
        return self.base / _expanded(value, self.home)

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
_RANK_PACKAGES = 6  # Each package ranks below the one before it.
# A turned-off project server still replaces the user's.
_SERVER_NAME_TAKERS = frozenset({Status.ENABLED, Status.DISABLED})


class Pi:
    id = "pi"

    def scan(self, context: Context) -> AgentReport:
        agent_dir, override = config_dir(
            context, "PI_CODING_AGENT_DIR", context.home / ".pi" / "agent"
        )
        project_dir = context.cwd / ".pi"
        config_path = first_existing(
            agent_dir,
            project_dir,
            context.home / ".agents" / "skills",
            context.cwd / ".agents" / "skills",
        )
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
            self._extensions(context, agent_dir, project_dir, layers, trust, reliable, report)
        )
        report.items.extend(self._packages(context, layers, trust, reliable))
        mcp_off = _built_in_is_off(
            _MCP_EXTENSION, user_settings, project_settings if trust is Status.ENABLED else {}
        )
        report.items.extend(self._servers(agent_dir, project_dir, trust, mcp_off, reliable, report))
        report.items.extend(
            self._prompts(context, agent_dir, project_dir, layers, trust, reliable, report)
        )
        return report

    def _prompts(
        self, context, agent_dir, project_dir, layers, trust, reliable, report
    ) -> list[Item]:
        bases = {Scope.USER: agent_dir, Scope.PROJECT: project_dir}
        resources = {scope: _Resources(base, context.home) for scope, base in bases.items()}
        # Conventional directories hold direct children only. Listed ones are searched. A project's
        # come first, so a template linked from both keeps the project's row.
        found: list[tuple[FoundSkill, Scope, int, str | None]] = [
            (prompt, Scope.PROJECT, _RANK_PI_SKILLS, None)
            for prompt in find_markdown(
                project_dir / "prompts", report.warnings, recursive=False, include_hidden=False
            )
        ] + [
            (prompt, Scope.USER, _RANK_AGENT_DIR, None)
            for prompt in find_markdown(
                agent_dir / "prompts", report.warnings, recursive=False, include_hidden=False
            )
        ]
        for settings, base, scope, _ in reversed(layers):
            listed = _Resources(base, context.home)
            listed.add(settings.get("prompts"))
            resources[scope].excluded_paths |= listed.excluded_paths
            resources[scope].excluded_globs += listed.excluded_globs
            rank = _RANK_USER_LISTED if scope is Scope.USER else _RANK_PROJECT_LISTED
            for path in listed.paths:
                found.extend((p, scope, rank, None) for p in _prompts_at(path, report.warnings))
        for rank, (source, scope, prompts) in enumerate(
            _package_resources(
                layers, trust, "prompts", _prompts_at, report.warnings, context.home
            ),
            _RANK_PACKAGES,
        ):
            found.extend((prompt, scope, rank, source) for prompt in prompts)

        items, seen = [], set()
        rank_of: dict[int, int] = {}
        for prompt, scope, rank, source in found:
            if prompt.resolved_path in seen:
                continue
            seen.add(prompt.resolved_path)
            default = Status.ENABLED if scope is Scope.USER else trust
            status = default if reliable else Status.UNKNOWN
            if source is None and resources[scope].excludes(prompt.path, bases[scope]):
                status = Status.DISABLED
            fields = prompt.fields or {}
            item = definition_item(
                self.id,
                prompt,
                Kind.COMMAND,
                "package prompt" if source else "prompt template",
                scope,
                status,
                name=prompt.path.stem,
                description=fields.get("description") or _first_line_summary(prompt.body),
            )
            if source:
                item.details["plugin"] = source
            rank_of[id(item)] = rank
            items.append(item)
        competing = [i for i in items if i.scope is Scope.USER or trust is Status.ENABLED]
        mark_shadowed(sorted(competing, key=lambda i: rank_of[id(i)]))
        return items

    def _skills(
        self, context, agent_dir, project_dir, layers, trust, reliable, report
    ) -> list[Item]:
        # Exclusions only apply to skills of the scope whose settings list them.
        bases = {Scope.USER: agent_dir, Scope.PROJECT: project_dir}
        resources = {scope: _Resources(base, context.home) for scope, base in bases.items()}
        found: list[tuple[FoundSkill, Scope, int]] = []
        ranks = {
            project_dir / "skills": _RANK_PI_SKILLS,
            context.cwd / ".agents" / "skills": _RANK_PROJECT_AGENTS,
            agent_dir / "skills": _RANK_AGENT_DIR,
            context.home / ".agents" / "skills": _RANK_USER_AGENTS,
        }
        for scope, root in unique_roots(
            [
                # A project's come first, so a skill linked from both keeps the project's row.
                (Scope.PROJECT, project_dir / "skills"),
                (Scope.PROJECT, context.cwd / ".agents" / "skills"),
                (Scope.USER, agent_dir / "skills"),
                (Scope.USER, context.home / ".agents" / "skills"),
            ]
        ):
            skills, warnings = scan_skills(
                root, max_depth=_SKILL_SCAN_DEPTH, search_inside_skills=False
            )
            # Standalone files count at a `.pi` root, and only below an `.agents` root.
            if root.parent.name == ".agents":
                skills += _nested_markdown_skills(root, warnings)
            else:
                standalone, more_warnings = scan_markdown_skills(root)
                skills += standalone
                warnings += more_warnings
            report.warnings.extend(warnings)
            found.extend((skill, scope, ranks[root]) for skill in skills)
        for settings, base, scope, _ in reversed(layers):
            listed = _Resources(base, context.home)
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
            item = _skill_row(self.id, skill, scope, status)
            rank_of[id(item)] = rank
            items.append(item)
        for rank, (source, scope, skills) in enumerate(
            _package_resources(layers, trust, "skills", _skills_in, report.warnings, context.home),
            _RANK_PACKAGES,
        ):
            default = Status.ENABLED if scope is Scope.USER else trust
            for skill in skills:
                # A package's skills ignore the `skills` setting; its own entry filters them.
                status = _skill_status(
                    skill, default, _Resources(bases[scope], context.home), reliable, bases[scope]
                )
                item = _skill_row(self.id, skill, scope, status)
                item.native_kind = "package skill"
                item.details["plugin"] = source
                rank_of[id(item)] = rank
                items.append(item)
        items = load_once(items)
        # An untrusted project's skills don't load, so they can't take a name. In one source, the
        # later skill wins.
        competing = [i for i in reversed(items) if i.scope is Scope.USER or trust is Status.ENABLED]
        mark_shadowed(sorted(competing, key=lambda i: rank_of[id(i)]))
        return items

    def _extensions(
        self, context, agent_dir, project_dir, layers, trust, reliable, report
    ) -> list[Item]:
        bases = {Scope.USER: agent_dir, Scope.PROJECT: project_dir}
        resources = {scope: _Resources(base, context.home) for scope, base in bases.items()}
        # A project's come first, so an extension linked from both keeps the project's row.
        candidates: list[tuple[Path, Scope]] = []
        for path, scope in (
            (project_dir / "extensions", Scope.PROJECT),
            (agent_dir / "extensions", Scope.USER),
        ):
            candidates.extend((entry, scope) for entry in list_entries(path, report.warnings))
        for settings, base, scope, _ in reversed(layers):
            listed = _Resources(base, context.home)
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
            for name, entry in _extension_entries(path):
                real = os.path.realpath(entry)
                if real in seen:
                    continue
                seen.add(real)
                default = Status.ENABLED if scope is Scope.USER else trust
                if not os.path.exists(entry):
                    status = Status.BROKEN
                elif resources[scope].excludes(path, bases[scope]) or resources[scope].excludes(
                    entry, bases[scope]
                ):
                    status = Status.DISABLED
                else:
                    status = default if reliable else Status.UNKNOWN
                items.append(plugin_item(self.id, name, "extension", scope, status, entry))
        return items

    def _packages(self, context, layers, trust, reliable) -> list[Item]:
        items = []
        by_scope: dict[Scope, dict[str, tuple[Item, object]]] = {}
        for settings, base, scope, path in layers:
            packages = settings.get("packages")
            for package in packages if isinstance(packages, list) else ():
                source = package.get("source") if isinstance(package, dict) else package
                if not isinstance(source, str):
                    continue
                default = Status.ENABLED if scope is Scope.USER else trust
                status = default if reliable else Status.UNKNOWN
                item = plugin_item(
                    self.id, without_credentials(source), "package", scope, status, path
                )
                items.append(item)
                identity = _package_identity(source, base, context.home)
                by_scope.setdefault(scope, {})[identity] = (item, package)
        if trust is Status.ENABLED:
            # A trusted project's entry replaces the personal one, unless it only filters it.
            project = by_scope.get(Scope.PROJECT, {})
            for identity, (item, _) in by_scope.get(Scope.USER, {}).items():
                winner = project.get(identity)
                if winner is None or item.status is not Status.ENABLED:
                    continue
                entry = winner[1]
                if isinstance(entry, dict) and entry.get("autoload") is False:
                    continue
                item.status = Status.SHADOWED
                item.findings.append(Finding("shadowed by the entry in", winner[0].source_path))
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
            if isinstance(entry, dict)
            and not {"command", "url", "type"} & entry.keys()
            and entry.keys() <= _OVERRIDE_KEYS
        }
        user_items, project_items = [], []
        # Names differing only in `-` and `_` share a namespace, and the first one keeps it.
        namespaces: dict[str, str] = {}
        for name, entry in user_servers.items():
            enabled = entry.get("enabled", True) if isinstance(entry, dict) else True
            if name in overrides and trust is Status.ENABLED:
                enabled = overrides[name].get("enabled", enabled)
            status = _server_status(enabled, Status.ENABLED, mcp_off, reliable and user_ok)
            problem = _server_problem(name, entry, Scope.USER, namespaces)
            user_items.append(
                _server_item(self.id, name, entry, problem, Scope.USER, status, user_path)
            )
        for name, entry in project_servers.items():
            if name in overrides and name in user_servers:
                continue
            enabled = entry.get("enabled", True) if isinstance(entry, dict) else True
            status = _server_status(enabled, trust, mcp_off, reliable and project_ok)
            problem = _server_problem(name, entry, Scope.PROJECT, namespaces)
            project_items.append(
                _server_item(self.id, name, entry, problem, Scope.PROJECT, status, project_path)
            )
        if trust is Status.ENABLED:
            mark_shadowed([*project_items, *user_items], winners=_SERVER_NAME_TAKERS)
        return [*user_items, *project_items]


def _server_item(agent, name, entry, problem, scope, status, path) -> Item:
    item = mcp_item(agent, name, None if problem else _server_details(entry), scope, status, path)
    if problem:
        item.findings = [Finding(problem)]
    return item


def _is_string_map(value: object) -> bool:
    return isinstance(value, dict) and all(isinstance(item, str) for item in value.values())


def _server_problem(
    name: str, entry: object, scope: Scope, namespaces: dict[str, str]
) -> str | None:
    """Say why Pi refuses a server. A refused one doesn't take the namespace."""
    problem = _entry_problem(name, entry, scope)
    if problem:
        return problem
    namespace = name.replace("-", "_")
    other = namespaces.setdefault(namespace, name)
    return None if other == name else f"conflicts with the server {other}"


def _entry_problem(name: str, entry: object, scope: Scope) -> str | None:
    if not _SERVER_NAME.fullmatch(name):
        return "invalid server name"
    if not isinstance(entry, dict):
        return "invalid server entry"
    kind = entry.get("type")
    if not isinstance(entry.get("enabled", True), bool):
        return "enabled must be true or false"
    if kind == "sse":
        return "Pi doesn't support the legacy SSE transport"
    if isinstance(entry.get("url"), str) and kind in _HTTP_TYPES:
        if urlsplit(entry["url"]).scheme not in ("http", "https"):
            return "url must be http or https"
        if "headers" in entry and not _is_string_map(entry["headers"]):
            return "headers must map names to text"
        if scope is Scope.PROJECT and entry.get("auth"):
            return "auth is only allowed in the user's mcp.json"
        return None
    if isinstance(entry.get("command"), str) and kind in (None, "stdio"):
        args = entry.get("args", [])
        if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
            return "args must be a list of text"
        if "env" in entry and not _is_string_map(entry["env"]):
            return "env must map names to text"
        return None
    return "needs a command or a url"


def _expanded(value: str, home: Path) -> str:
    """Pi expands a leading `~` itself, against the home directory."""
    if value == "~":
        return str(home)
    return str(home / value[2:]) if value.startswith("~/") else value


def _matches_override(target: str, pattern: str) -> bool:
    return fnmatch.fnmatchcase(target, pattern)


def _built_in_is_off(target: str, user_settings: dict, project_settings: dict) -> bool:
    """A project's matching entry decides, the last winning. Otherwise the user's do."""
    project = _override_patterns(project_settings)
    decision = None
    for pattern in project:
        if _matches_override(target, pattern[1:]):
            decision = pattern[0] == "+"
    if decision is not None:
        return not decision
    # In the user's, `!` excludes, then `+` includes, then `-` excludes.
    user = _override_patterns(user_settings)
    enabled = True
    for marker, value in (("!", False), ("+", True), ("-", False)):
        if any(p[0] == marker and _matches_override(target, p[1:]) for p in user):
            enabled = value
    return not enabled


def _override_patterns(settings: dict) -> list[str]:
    values = settings.get("extensions")
    return [
        value
        for value in (values if isinstance(values, list) else ())
        if isinstance(value, str) and value[:1] in ("+", "-", "!") and len(value) > 1
    ]


def _first_line_summary(body: str) -> str:
    line = first_line(body)
    return f"{line[:_DESCRIPTION_LIMIT]}..." if len(line) > _DESCRIPTION_LIMIT else line


def _skill_row(agent: str, skill: FoundSkill, scope: Scope, status: Status) -> Item:
    item = skill_item(agent, skill, scope, status, "has no description, which Pi requires")
    # Without a `name`, a standalone file takes its directory's name, not its own.
    if skill.standalone and not (skill.fields or {}).get("name"):
        item.name = skill.path.parent.name
    return item


def _nested_markdown_skills(root: Path, warnings: list[str]) -> list[FoundSkill]:
    """Find `*.md` skills below an `.agents` root. Files with no description are just documents."""
    found: list[FoundSkill] = []
    for directory, names, files in os.walk(root, followlinks=True):
        names[:] = sorted(n for n in names if not n.startswith(".") and n != "node_modules")
        if SKILL_FILE in files:
            names[:] = []
            continue
        if (
            Path(directory) == root
            or len(Path(directory).parts) > len(root.parts) + _SKILL_SCAN_DEPTH
        ):
            continue
        for name in sorted(files):
            if name.endswith(".md") and not name.startswith("."):
                skill = read_markdown(Path(directory) / name, warnings)
                if not skill.broken and (skill.fields or {}).get("description"):
                    found.append(skill)
    return found


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
    return skills_at(path, warnings, max_depth=_SKILL_SCAN_DEPTH)


def _prompts_at(path: Path, warnings: list[str]) -> list[FoundSkill]:
    if os.path.isfile(path):
        return [read_markdown(path, warnings)] if path.suffix == ".md" else []
    return find_markdown(path, warnings, include_hidden=False)


def _package_resources(
    layers: list, trust: Status, key: str, find, warnings: list[str], home: Path
) -> list[tuple[str, Scope, list[FoundSkill]]]:
    """Return what each package has under one key, highest precedence first."""
    # A trusted project's entry replaces the personal entry for the same package.
    ordered = reversed(layers) if trust is Status.ENABLED else layers
    packages: dict[str, tuple[str, Scope, list[FoundSkill]]] = {}
    for settings, base, scope, _ in ordered:
        entries = settings.get("packages")
        for entry in entries if isinstance(entries, list) else ():
            source = entry.get("source") if isinstance(entry, dict) else entry
            root = _package_dir(source, base, home) if isinstance(source, str) else None
            if root is None or (identity := os.path.realpath(root)) in packages:
                continue
            found = _declared(root, key, find, warnings)
            chosen = entry.get(key) if isinstance(entry, dict) else None
            if isinstance(chosen, list):
                found = [skill for skill in found if _is_chosen(skill.path, root, chosen)]
            packages[identity] = (without_credentials(source), scope, found)
    return list(packages.values())


def _git_location(source: str) -> str | None:
    """Return a git source's `host/path`, or None if it isn't one. A ref isn't part of the path."""
    text = source.strip()
    prefixed = text.startswith("git:") and not text.startswith("git://")
    url = text[4:].strip() if prefixed else text
    scheme = _GIT_URL.match(url)
    if not (prefixed or scheme):
        return None
    if scheme:
        url = url[scheme.end() :]
    url = url.split("#", 1)[0]
    head, separator, tail = url.partition("/")
    # `user@host:path` writes the path after a colon. With a scheme, that colon comes before a port.
    if "@" in head:
        head = head.rsplit("@", 1)[1]
    if ":" in head:
        head, _, rest = head.partition(":")
        if scheme:
            url = f"{head}/{tail}"
        else:
            url = f"{head}/{rest}{separator}{tail}"
    else:
        url = f"{head}/{tail}" if separator else head
    host, _, path = url.partition("/")
    path = path.split("@", 1)[0].removesuffix(".git").strip("/")
    return f"{host}/{path}" if host and path else None


def _package_identity(source: str, base: Path, home: Path) -> str:
    """Pi treats these as one package, however each is written."""
    if source.startswith("npm:"):
        spec = source[len("npm:") :].strip()
        name, at, _ = spec[1:].rpartition("@")
        return f"npm:{spec[: len(name) + 1] if at else spec}"
    location = _git_location(source)
    if location:
        return f"git:{location}"
    return f"local:{os.path.realpath(base / _expanded(source, home))}"


def _package_dir(source: str, base: Path, home: Path) -> Path | None:
    """Find where Pi keeps a package. It may not be installed, so this can name nothing."""
    location = _git_location(source)
    if source.startswith("npm:"):
        spec = source[len("npm:") :]
        # An `@` at the start belongs to a scoped name, not a version.
        name, at, _ = spec[1:].rpartition("@")
        managed = Path("npm") / "node_modules" / (spec[: len(name) + 1] if at else spec)
    elif location:
        managed = Path("git") / location
    elif ":" in source:
        return None
    else:
        root = base / _expanded(source, home)
        return root if os.path.isdir(root) else None
    if ".." in managed.parts:
        return None
    return base / managed if os.path.isdir(base / managed) else None


def _declared(root: Path, key: str, find, warnings: list[str]) -> list[FoundSkill]:
    """A `pi` key in package.json replaces the conventional directories."""
    try:
        with open(root / "package.json", encoding="utf-8") as file:
            package = json.load(file)
    except (OSError, ValueError):
        package = None
    manifest = package.get("pi") if isinstance(package, dict) else None
    # A `pi` key with only metadata leaves the conventional directories in place.
    if not isinstance(manifest, dict) or not _RESOURCE_KEYS & manifest.keys():
        return find(root / key, warnings)
    declared = manifest.get(key)
    entries = (
        [entry for entry in declared if isinstance(entry, str)]
        if isinstance(declared, list)
        else []
    )
    found: list[FoundSkill] = []
    for entry in entries:
        if not entry.startswith("!"):
            for match in sorted(glob.glob(f"{glob.escape(str(root))}/{entry}")):
                found.extend(find(Path(os.path.normpath(match)), warnings))
    excluded = [entry[1:] for entry in entries if entry.startswith("!")]
    return [
        skill
        for skill in found
        if not any(_matches(skill.path, root, pattern) for pattern in excluded)
    ]


def _skills_in(path: Path, warnings: list[str]) -> list[FoundSkill]:
    found = _listed_skills(path, warnings)
    if not os.path.lexists(path / SKILL_FILE):
        standalone, more_warnings = scan_markdown_skills(path)
        found += standalone
        warnings.extend(more_warnings)
    return found


def _matches(path: Path, root: Path, pattern: str) -> bool:
    relative = os.path.relpath(path, root)
    return any(
        fnmatch.fnmatchcase(candidate, pattern)
        for candidate in (relative, f"{relative}/{SKILL_FILE}", path.name)
    )


def _is_chosen(path: Path, root: Path, patterns: list[object]) -> bool:
    """Apply a package entry's filter: `[]` loads nothing, and exclusions alone keep the rest."""
    chosen = [entry for entry in patterns if isinstance(entry, str)]
    excluded = [entry[1:] for entry in chosen if entry[:1] in ("!", "-")]
    included = [entry.lstrip("+") for entry in chosen if entry[:1] not in ("!", "-")]
    if not chosen or any(_matches(path, root, pattern) for pattern in excluded):
        return False
    return not included or any(
        _matches(path, root, pattern.removeprefix("./")) for pattern in included
    )


def _skill_status(
    skill: FoundSkill, default: Status, resources: _Resources, reliable: bool, base: Path
) -> Status:
    if skill.broken or not (skill.fields or {}).get("description"):
        return Status.BROKEN
    if resources.excludes(skill.path, base):
        return Status.DISABLED
    return default if reliable else Status.UNKNOWN


def _extension_entries(path: Path) -> list[tuple[str, Path]]:
    """Return the (name, file) of each extension a path holds. A dangling link counts as one."""
    if path.suffix in _EXTENSION_SUFFIXES and (os.path.isfile(path) or not os.path.exists(path)):
        return [(path.stem, path)]
    if not os.path.isdir(path):
        return []
    declared = _declared_extensions(path)
    if len(declared) == 1:
        return [(path.name, path)]
    if declared:
        return [(f"{path.name}/{entry.stem}", entry) for entry in declared]
    # Only entries that exist count. Without any, a directory uses its index file.
    if any(os.path.isfile(path / f"index{suffix}") for suffix in _EXTENSION_SUFFIXES):
        return [(path.name, path)]
    return []


def _declared_extensions(directory: Path) -> list[Path]:
    try:
        with open(directory / "package.json", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, ValueError):
        return []
    pi = data.get("pi") if isinstance(data, dict) else None
    declared = pi.get("extensions") if isinstance(pi, dict) else None
    entries = [
        directory / entry
        for entry in (declared if isinstance(declared, list) else ())
        if isinstance(entry, str)
    ]
    return [Path(os.path.normpath(entry)) for entry in entries if os.path.exists(entry)]


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
    # A server with both is an HTTP one.
    if isinstance(entry.get("url"), str) and entry.get("type") in _HTTP_TYPES:
        return remote_details("streamable_http", entry["url"], entry.get("headers"))
    if isinstance(entry.get("command"), str):
        return stdio_details(entry["command"], entry.get("env"))
    return None
