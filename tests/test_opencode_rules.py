"""OpenCode rules from docs/agents/opencode.md, without the real agent."""

import json
import plistlib
import sys
from pathlib import Path

import pytest
from conftest import write_skill

from agt_env.adapters.base import Context
from agt_env.adapters.opencode import OpenCode
from agt_env.model import Kind, Scope, Status

LOCAL = {"type": "local", "command": ["/bin/true"]}
PLUGIN = "export default {id: '%s', setup: async () => ({})}\n"


class Fixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.home = root / "home"
        self.project = root / "project"
        self.config = self.home / ".config" / "opencode"
        self.system = root / "system" / "etc" / "opencode"
        self.home.mkdir()
        self.project.mkdir()
        self.env: dict[str, str] = {}

    def write(self, path: Path, content: object) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content if isinstance(content, str) else json.dumps(content))
        return path

    def scan(self):
        context = Context(
            home=self.home,
            cwd=self.project,
            env={"PATH": "", **self.env},
            system_root=self.root / "system",
        )
        return OpenCode().scan(context)

    def statuses(self, kind):
        return {item.name: item.status for item in self.scan().items if item.kind is kind}

    def item(self, kind, name):
        return next(item for item in self.scan().items if item.kind is kind and item.name == name)


@pytest.fixture
def fx(tmp_path):
    return Fixture(tmp_path)


def test_a_moved_directory_replaces_the_global_one(fx):
    moved = fx.root / "moved"
    write_skill(fx.config / "skills" / "old")
    write_skill(moved / "skills" / "new")
    fx.write(fx.config / "opencode.json", {"mcp": {"servers": {"old": LOCAL}}})
    fx.write(moved / "opencode.json", {"mcp": {"servers": {"new": LOCAL}}})
    fx.write(fx.config / "commands" / "old.md", "t")
    fx.write(moved / "commands" / "new.md", "t")
    fx.write(fx.config / "plugins" / "old.ts", PLUGIN % "o")
    fx.env["OPENCODE_CONFIG_DIR"] = str(moved)
    assert set(fx.statuses(Kind.SKILL)) == {"new"}
    assert set(fx.statuses(Kind.MCP)) == {"new"}
    assert set(fx.statuses(Kind.COMMAND)) == {"new"}
    assert not fx.statuses(Kind.PLUGIN)
    report = fx.scan()
    assert report.config_path == moved
    assert report.override_var == "OPENCODE_CONFIG_DIR"


@pytest.mark.parametrize(("depth", "found"), [(5, True), (6, True), (7, False)])
def test_skill_search_depth(fx, depth, found):
    path = "/".join(f"d{level}" for level in range(depth))
    write_skill(fx.config / "skills" / path)
    assert bool(fx.statuses(Kind.SKILL)) is found


def test_the_plural_directory_wins(fx):
    fx.write(fx.config / "commands" / "c.md", "---\ndescription: plural\n---\nt")
    fx.write(fx.config / "command" / "c.md", "---\ndescription: singular\n---\nt")
    fx.write(fx.config / "agents" / "a.md", "---\ndescription: plural\n---\nt")
    fx.write(fx.config / "agent" / "a.md", "---\ndescription: singular\n---\nt")
    assert fx.item(Kind.COMMAND, "c").description == "plural"
    assert fx.item(Kind.SUBAGENT, "a").description == "plural"


def test_modes_are_agents_and_arent_searched_deeper(fx):
    fx.write(fx.config / "modes" / "one.md", "---\ndescription: d\n---\nt")
    fx.write(fx.config / "mode" / "two.md", "---\ndescription: d\n---\nt")
    fx.write(fx.config / "modes" / "sub" / "three.md", "---\ndescription: d\n---\nt")
    assert set(fx.statuses(Kind.SUBAGENT)) == {"one", "two"}
    assert fx.item(Kind.SUBAGENT, "one").native_kind == "mode"


def test_a_mode_beats_a_same_named_agent(fx):
    fx.write(fx.config / "agents" / "m.md", "---\ndescription: agent\n---\nt")
    fx.write(fx.config / "modes" / "m.md", "---\ndescription: mode\n---\nt")
    assert fx.item(Kind.SUBAGENT, "m").description == "mode"


def test_a_commands_body_isnt_its_description(fx):
    fx.write(fx.config / "commands" / "bare.md", "git status --porcelain\n")
    assert fx.item(Kind.COMMAND, "bare").description == ""


@pytest.mark.parametrize("timeout", [5000, {"catalog": 5000}])
def test_a_timeout_isnt_a_server(fx, timeout):
    fx.write(fx.config / "opencode.json", {"mcp": {"timeout": timeout, "one": LOCAL}})
    assert fx.statuses(Kind.MCP) == {"one": Status.ENABLED}


def test_a_server_named_timeout_or_servers_is_allowed(fx):
    fx.write(fx.config / "opencode.json", {"mcp": {"timeout": LOCAL, "servers": LOCAL}})
    assert fx.statuses(Kind.MCP) == {"timeout": Status.ENABLED, "servers": Status.ENABLED}


def test_v1_and_v2_servers_in_one_file_merge(fx):
    fx.write(
        fx.config / "opencode.json",
        {
            "mcp": {
                "legacy": {**LOCAL, "enabled": False},
                "both": {"type": "remote", "url": "https://x.test"},
                "servers": {"native": {**LOCAL, "enabled": False}, "both": LOCAL},
            }
        },
    )
    assert fx.statuses(Kind.MCP) == {
        "legacy": Status.DISABLED,
        "native": Status.ENABLED,
        "both": Status.ENABLED,
    }
    assert fx.item(Kind.MCP, "both").details["transport"] != "remote"


def test_enabled_only_entries_are_dropped(fx):
    fx.write(
        fx.config / "opencode.json", {"mcp": {"a": {"enabled": True}, "b": {"enabled": False}}}
    )
    assert not fx.statuses(Kind.MCP)


def test_an_invalid_higher_entry_doesnt_replace_a_valid_one(fx):
    fx.write(fx.config / "opencode.json", {"mcp": {"servers": {"s": LOCAL}}})
    fx.write(fx.project / "opencode.json", {"mcp": {"servers": {"s": {"type": "nope"}}}})
    item = fx.item(Kind.MCP, "s")
    assert item.status is Status.ENABLED
    assert item.scope is Scope.USER


def rich_fixture(fx, plugins):
    write_skill(fx.config / "skills" / "own")
    write_skill(fx.home / ".agents" / "skills" / "compat")
    fx.write(fx.config / "commands" / "cm.md", "t")
    fx.write(fx.config / "agents" / "am.md", "---\ndescription: d\n---\nt")
    fx.write(fx.config / "plugins" / "lp.ts", PLUGIN % "lp.id")
    fx.write(fx.config / "opencode.json", {"plugins": plugins, "mcp": {"servers": {"m": LOCAL}}})


@pytest.mark.parametrize(
    ("plugins", "off"),
    [
        (["-opencode.config.skill"], {"own"}),
        (["-opencode.config.compatibility"], {"compat"}),
        (["-opencode.config.agent"], {"am"}),
        (["-opencode.config.command"], {"cm"}),
        (["-opencode.config.mcp"], {"m"}),
        (["-*"], {"own", "compat", "am", "cm", "m", "lp"}),
        (["-*", "opencode.config.skill"], {"compat", "am", "cm", "m", "lp"}),
        (["-*", "opencode.config.*"], {"lp"}),
        (["-*", "*"], set()),
        (["-opencode.config.skill", "opencode.config.skill"], set()),
        (["-lp.id", "lp.id"], set()),
        (["-lp.id"], {"lp"}),
    ],
)
def test_plugin_operations_apply_in_order(fx, plugins, off):
    rich_fixture(fx, plugins)
    items = {item.name: item.status for item in fx.scan().items}
    assert {name for name, status in items.items() if status is Status.DISABLED} == off


def test_a_removed_source_doesnt_shadow(fx):
    write_skill(fx.config / "skills" / "same")
    write_skill(fx.home / ".agents" / "skills" / "same")
    fx.write(fx.config / "opencode.json", {"plugins": ["-opencode.config.skill"]})
    rows = [item for item in fx.scan().items if item.kind is Kind.SKILL]
    assert sorted(item.status.value for item in rows) == ["disabled", "enabled"]


@pytest.mark.parametrize(
    ("env", "project_on"),
    [
        ({"OPENCODE_DISABLE_PROJECT_CONFIG": "0"}, True),
        ({"OPENCODE_DISABLE_PROJECT_CONFIG": "false"}, True),
        ({"OPENCODE_DISABLE_PROJECT_CONFIG": "no"}, True),
        ({"OPENCODE_DISABLE_PROJECT_CONFIG": "1"}, False),
        ({"OPENCODE_DISABLE_PROJECT_CONFIG": "TRUE"}, False),
        ({"OPENCODE_CONFIG_PROJECT_DISABLE": "1"}, False),
        ({"OPENCODE_CONFIG_PROJECT_DISABLE": "0", "OPENCODE_DISABLE_PROJECT_CONFIG": "1"}, True),
    ],
)
def test_turning_project_config_off(fx, env, project_on):
    fx.env.update(env)
    write_skill(fx.project / ".opencode" / "skills" / "own")
    write_skill(fx.project / ".agents" / "skills" / "compat")
    write_skill(fx.project / ".claude" / "skills" / "claude")
    fx.write(fx.project / ".opencode" / "commands" / "pc.md", "t")
    fx.write(fx.project / ".opencode" / "plugins" / "pp.ts", PLUGIN % "pp")
    fx.write(fx.project / "opencode.json", {"mcp": {"servers": {"pm": LOCAL}}})
    expected = {"own", "compat", "claude"} if project_on else set()
    assert set(fx.statuses(Kind.SKILL)) == expected
    assert bool(fx.statuses(Kind.MCP)) is project_on
    assert bool(fx.statuses(Kind.COMMAND)) is project_on
    assert bool(fx.statuses(Kind.PLUGIN)) is project_on


@pytest.mark.parametrize(
    ("env", "claude_on"),
    [
        ({"OPENCODE_DISABLE_CLAUDE_CODE": "false"}, True),
        ({"OPENCODE_DISABLE_CLAUDE_CODE": "0"}, True),
        ({"OPENCODE_DISABLE_CLAUDE_CODE": "1"}, False),
        ({"OPENCODE_DISABLE_CLAUDE_CODE_SKILLS": "true"}, False),
    ],
)
def test_turning_claude_skills_off(fx, env, claude_on):
    fx.env.update(env)
    write_skill(fx.home / ".claude" / "skills" / "c")
    assert bool(fx.statuses(Kind.SKILL)) is claude_on


def test_a_skill_linked_from_the_project_keeps_the_projects_row(fx):
    write_skill(fx.config / "skills" / "s")
    (fx.project / ".opencode" / "skills").mkdir(parents=True)
    (fx.project / ".opencode" / "skills" / "s").symlink_to(fx.config / "skills" / "s")
    rows = [item for item in fx.scan().items if item.kind is Kind.SKILL]
    assert [item.scope for item in rows] == [Scope.PROJECT]


def test_a_tilde_in_skills_config_uses_the_scanned_home(fx):
    write_skill(fx.home / "elsewhere" / "s")
    fx.write(fx.config / "opencode.json", {"skills": ["~/elsewhere"]})
    assert set(fx.statuses(Kind.SKILL)) == {"s"}


def test_a_tilde_plugin_is_a_package(fx):
    fx.write(fx.home / "x" / "index.ts", PLUGIN % "x")
    fx.write(fx.config / "opencode.json", {"plugins": ["~/x"]})
    item = fx.item(Kind.PLUGIN, "~/x")
    assert item.native_kind == "npm plugin"
    assert item.status is Status.UNKNOWN


@pytest.mark.parametrize(
    ("name", "listed"),
    [("gui-experimental.cjs", True), ("gui-experimental.js", True), ("gui-experimental.ts", False)],
)
def test_gui_only_plugins(fx, name, listed):
    fx.write(fx.config / "plugins" / "g" / name, "x")
    assert ("g" in fx.statuses(Kind.PLUGIN)) is listed


def test_a_configured_gui_only_directory_isnt_broken(fx):
    fx.write(fx.project / "g" / "gui-experimental.cjs", "x")
    fx.write(fx.project / "opencode.json", {"plugins": ["./g"]})
    assert fx.statuses(Kind.PLUGIN) == {"g": Status.ENABLED}


def test_a_gui_only_plugin_is_removed_by_its_directory_name(fx):
    fx.write(fx.config / "plugins" / "g" / "gui-experimental.cjs", "x")
    fx.write(fx.config / "opencode.json", {"plugins": ["-g"]})
    assert fx.statuses(Kind.PLUGIN) == {"g": Status.DISABLED}


def test_system_directories_are_read_and_win(fx):
    write_skill(fx.system / "skills" / "s", "description: system")
    write_skill(fx.config / "skills" / "s", "description: user")
    write_skill(fx.project / ".opencode" / "skills" / "s", "description: project")
    fx.write(fx.system / "agents" / "a.md", "---\ndescription: system\n---\nt")
    fx.write(fx.project / ".opencode" / "agents" / "a.md", "---\ndescription: project\n---\nt")
    fx.write(fx.system / "commands" / "c.md", "---\ndescription: system\n---\nt")
    fx.write(fx.system / "plugins" / "p.ts", PLUGIN % "p")
    rows = [item for item in fx.scan().items if item.kind is Kind.SKILL]
    assert {item.scope: item.status for item in rows} == {
        Scope.SYSTEM: Status.ENABLED,
        Scope.USER: Status.SHADOWED,
        Scope.PROJECT: Status.SHADOWED,
    }
    assert fx.item(Kind.SUBAGENT, "a").scope is Scope.SYSTEM
    assert fx.item(Kind.COMMAND, "c").scope is Scope.SYSTEM
    assert fx.item(Kind.PLUGIN, "p").scope is Scope.SYSTEM


def test_an_absolute_config_file_counts_as_configured(fx):
    custom = fx.write(fx.root / "custom.json", {"mcp": {"servers": {"c": LOCAL}}})
    fx.env["OPENCODE_CONFIG"] = str(custom)
    report = fx.scan()
    assert report.config_path == custom
    assert report.override_var == "OPENCODE_CONFIG"
    assert {item.name for item in report.items if item.kind is Kind.MCP} == {"c"}


def test_a_relative_config_file_isnt_read(fx):
    fx.write(fx.project / "custom.json", {"mcp": {"servers": {"c": LOCAL}}})
    fx.env["OPENCODE_CONFIG"] = "custom.json"
    assert not fx.statuses(Kind.MCP)


@pytest.mark.parametrize("location", ["home", "project"])
def test_agents_skills_alone_count_as_configured(fx, location):
    root = fx.home if location == "home" else fx.project
    write_skill(root / ".agents" / "skills" / "s")
    assert fx.scan().config_path == root / ".agents" / "skills"


def test_macos_managed_preferences_win(fx, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr("getpass.getuser", lambda: "me")
    fx.write(fx.config / "opencode.json", {"mcp": {"servers": {"s": LOCAL}}})
    plist = fx.root / "system" / "Library" / "Managed Preferences" / "ai.opencode.managed.plist"
    plist.parent.mkdir(parents=True)
    plist.write_bytes(
        plistlib.dumps(
            {"PayloadUUID": "x", "mcp": {"servers": {"s": {**LOCAL, "disabled": True}, "m": LOCAL}}}
        )
    )
    statuses = fx.statuses(Kind.MCP)
    assert statuses == {"s": Status.DISABLED, "m": Status.ENABLED}
    assert fx.item(Kind.MCP, "m").scope is Scope.SYSTEM


def test_the_users_preferences_file_goes_first(fx, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr("getpass.getuser", lambda: "me")
    root = fx.root / "system" / "Library" / "Managed Preferences"
    (root / "me").mkdir(parents=True)
    (root / "me" / "ai.opencode.managed.plist").write_bytes(
        plistlib.dumps({"mcp": {"servers": {"mine": LOCAL}}})
    )
    (root / "ai.opencode.managed.plist").write_bytes(
        plistlib.dumps({"mcp": {"servers": {"everyone": LOCAL}}})
    )
    assert set(fx.statuses(Kind.MCP)) == {"mine"}


def test_a_bad_preferences_file_warns(fx, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr("getpass.getuser", lambda: "me")
    fx.write(
        fx.root / "system" / "Library" / "Managed Preferences" / "ai.opencode.managed.plist", "x"
    )
    assert any("plist" in warning for warning in fx.scan().warnings)
