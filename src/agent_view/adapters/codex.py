"""Codex adapter.

Sources: the Codex skills, config, MCP, and plugin documentation, plus behavior observed
from `codex app-server` and `codex mcp list` with Codex 0.166.0; see
tests/test_codex_oracle.py.

Observed but undocumented:
- `$CODEX_HOME/skills` and a project's `.codex/skills` are also read, the latter even
  when the project isn't trusted.
- Discovery recurses six levels below a root, into skill directories too, and skips
  hidden directories. `$CODEX_HOME/skills/.system` holds bundled skills.
- `description` is required and `name` defaults to the directory name.
- A skill linked from both user and project roots loads once, as a user skill.
  Different skills sharing a name all load.
- `[[skills.config]]` disables a skill only when `path` names its SKILL.md file.
- An MCP server without `enabled` is enabled. One with neither `command` nor `url` makes
  Codex reject the whole config file.

Plugin rules are less certain. `[plugins."name@marketplace"]` is documented, but the
`plugins/cache/<marketplace>/<name>/<version>` layout and `.codex-plugin/plugin.json`
were only seen in real installs, and Codex's plugin listing can't confirm them without
a marketplace. Cached plugins with no config entry aren't listed: being cached doesn't
mean they're available.
"""

import json
import os
import tomllib
from pathlib import Path

from agent_view.adapters.base import Context, config_dir, load_once, unique_roots
from agent_view.mcp import remote_details, stdio_details
from agent_view.model import AgentReport, Item, Kind, Scope, Status
from agent_view.skills import FoundSkill, scan_skills

_MAX_DEPTH = 6


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
            override_var=override,
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
        for config, scope, path, default in layers:
            report.items.extend(self._mcp_servers(config, scope, path, default))
            report.items.extend(self._plugins(config, scope, path, default, codex_home))
        return report

    def _skill(self, skill: FoundSkill, scope: Scope, disabled: set[str] | None) -> Item:
        fields = skill.fields or {}
        return Item(
            name=fields.get("name") or skill.path.name,
            kind=Kind.SKILL,
            native_kind="skill",
            agent=self.id,
            scope=scope,
            status=_skill_status(skill, disabled),
            description=fields.get("description", ""),
            source_path=skill.path,
            resolved_path=skill.resolved_path,
        )

    def _mcp_servers(
        self, config: dict, scope: Scope, path: Path, default: Status
    ) -> list[Item]:
        servers = config.get("mcp_servers")
        items = []
        for name, entry in servers.items() if isinstance(servers, dict) else ():
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
                    kind=Kind.EXTENSION,
                    native_kind="plugin",
                    agent=self.id,
                    scope=scope,
                    status=status,
                    description=_plugin_description(directory) if cached else "",
                    source_path=source,
                    resolved_path=Path(os.path.realpath(source)),
                )
            )
        return items


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


def _plugin_description(directory: Path) -> str:
    """Read the newest cached version's manifest. A bad one just means less detail."""
    try:
        versions = sorted(os.listdir(directory), reverse=True)
    except OSError:
        return ""
    for version in versions:
        try:
            with open(directory / version / ".codex-plugin" / "plugin.json", encoding="utf-8") as f:
                manifest = json.load(f)
        except (OSError, ValueError):
            continue
        description = manifest.get("description") if isinstance(manifest, dict) else None
        return description if isinstance(description, str) else ""
    return ""


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
