"""Claude Code rules found by comparing with the real agent. See docs/agents/claude-code.md."""

import json
import sys

import pytest
from conftest import write_skill

from agt_env.adapters.base import Context
from agt_env.adapters.claude_code import ClaudeCode
from agt_env.model import Kind, Scope, Status

STDIO = {"command": "/bin/true"}
COMMAND_HOOK = {"hooks": [{"type": "command", "command": "tool"}]}


class Fixture:
    def __init__(self, tmp_path):
        self.root = tmp_path
        self.home = tmp_path / "home"
        self.project = tmp_path / "project"
        self.dir = self.home / ".claude"
        self.managed = (
            tmp_path / "system" / "Library" / "Application Support" / "ClaudeCode"
            if sys.platform == "darwin"
            else tmp_path / "system" / "etc" / "claude-code"
        )
        self.env = {"PATH": ""}
        self.project.mkdir()
        self.home.mkdir()

    def write(self, path, content=""):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content if isinstance(content, str) else json.dumps(content))
        return path

    def scan(self, cwd=None):
        context = Context(
            home=self.home,
            cwd=cwd or self.project,
            env=self.env,
            system_root=self.root / "system",
        )
        return ClaudeCode().scan(context)

    def items(self, kind=None, cwd=None):
        return [item for item in self.scan(cwd).items if kind is None or item.kind is kind]

    def statuses(self, kind, cwd=None):
        return {item.name: item.status for item in self.items(kind, cwd)}

    def plugin(self, name="p", *, build=None, manifest=None, scope="user", enabled=True, **extra):
        root = self.root / "plugins" / name
        root.mkdir(parents=True)
        if manifest is not None:
            self.write(root / ".claude-plugin" / "plugin.json", {"name": name, **manifest})
        if build:
            build(root)
        record_path = self.dir / "plugins" / "installed_plugins.json"
        record = json.loads(record_path.read_text()) if record_path.exists() else {"plugins": {}}
        record["plugins"].setdefault(f"{name}@m", []).append(
            {"scope": scope, "installPath": str(root), "version": "1", **extra}
        )
        self.write(record_path, record)
        if enabled:
            self.settings({"enabledPlugins": {f"{name}@m": True}})
        return root

    def settings(self, data):
        path = self.dir / "settings.json"
        current = json.loads(path.read_text()) if path.exists() else {}
        for key, value in data.items():
            if isinstance(value, dict) and isinstance(current.get(key), dict):
                current[key] = {**current[key], **value}
            else:
                current[key] = value
        self.write(path, current)


@pytest.fixture
def claude(tmp_path):
    return Fixture(tmp_path)


# Malformed entries


@pytest.mark.parametrize(
    "entry",
    [
        {"command": "/bin/true", "args": None},
        {"command": "/bin/true", "args": 3},
        {"command": "/bin/true", "args": "--flag"},
        {"command": "/bin/true", "args": [1]},
        {"command": "/bin/true", "env": None},
        {"command": "/bin/true", "env": {"A": 1}},
        {"type": "http", "url": "http://host/a", "headers": "x"},
    ],
)
def test_server_it_would_skip_is_broken_and_does_not_crash_policy(claude, entry):
    claude.write(claude.home / ".claude.json", {"mcpServers": {"x": entry}})
    claude.settings({"deniedMcpServers": [{"serverCommand": ["/bin/true", "--flag"]}]})
    assert claude.statuses(Kind.MCP) == {"x": Status.BROKEN}


def test_command_policy_matches_arguments(claude):
    servers = {"a": {"command": "c", "args": ["x"]}, "b": {"command": "c", "args": ["y"]}}
    claude.write(claude.home / ".claude.json", {"mcpServers": servers})
    claude.settings({"deniedMcpServers": [{"serverCommand": ["c", "x"]}]})
    assert claude.statuses(Kind.MCP) == {"a": Status.DISABLED, "b": Status.ENABLED}


@pytest.mark.parametrize(
    ("entry", "status"),
    [
        ({"type": "http", "url": ""}, Status.BROKEN),
        ({"type": "http", "url": "not-a-url"}, Status.BROKEN),
        ({"type": "http", "url": "ftp://host/a"}, Status.BROKEN),
        ({"type": "sse", "url": "nope"}, Status.BROKEN),
        ({"type": "ws", "url": "nope"}, Status.BROKEN),
        ({"type": "http", "url": "http://host/a"}, Status.ENABLED),
        ({"type": "streamable-http", "url": "https://host/a"}, Status.ENABLED),
        ({"type": "ws", "url": "wss://host/a"}, Status.ENABLED),
        ({"type": "http", "url": "${SERVER_URL}"}, Status.ENABLED),
    ],
)
def test_server_url_it_can_never_connect_to_is_broken(claude, entry, status):
    claude.write(claude.home / ".claude.json", {"mcpServers": {"x": entry}})
    assert claude.statuses(Kind.MCP) == {"x": status}


# Running from the home directory


def test_home_directory_is_not_read_twice(claude):
    claude.write(claude.dir / "commands" / "c.md", "Body")
    claude.write(claude.dir / "agents" / "a.md", "---\nname: a\ndescription: d\n---\n")
    claude.settings({"hooks": {"SessionStart": [COMMAND_HOOK]}})
    write_skill(claude.dir / "skills" / "s", "description: d")
    items = {(i.kind, i.name): (i.scope, i.status) for i in claude.items(cwd=claude.home)}
    assert len(claude.items(cwd=claude.home)) == len(items) == 4
    assert all(value == (Scope.USER, Status.ENABLED) for value in items.values())


def test_config_dir_inside_the_project_is_read_once(claude):
    claude.env = {"PATH": "", "CLAUDE_CONFIG_DIR": str(claude.project / ".claude")}
    claude.write(claude.project / ".claude" / "commands" / "c.md", "Body")
    assert [(i.name, i.scope) for i in claude.items(Kind.COMMAND)] == [("c", Scope.USER)]


# Configuration


@pytest.mark.parametrize("override", [True, False])
def test_state_file_follows_the_variable_even_to_the_default(claude, override):
    claude.write(claude.home / ".claude.json", {"mcpServers": {"home": STDIO}})
    claude.write(claude.dir / ".claude.json", {"mcpServers": {"config": STDIO}})
    if override:
        claude.env = {"PATH": "", "CLAUDE_CONFIG_DIR": str(claude.dir)}
    assert set(claude.statuses(Kind.MCP)) == {"config" if override else "home"}


def test_drop_in_files_add_to_managed_settings(claude):
    write_skill(claude.dir / "skills" / "a", "description: d")
    write_skill(claude.dir / "skills" / "b", "description: d")
    write_skill(claude.dir / "skills" / "c", "description: d")
    write_skill(claude.dir / "skills" / "d", "description: d")
    claude.write(claude.managed / "managed-settings.json", {"skillOverrides": {"a": "off"}})
    drop_ins = claude.managed / "managed-settings.d"
    claude.write(drop_ins / "10.json", {"skillOverrides": {"b": "off"}})
    claude.write(drop_ins / ".hidden.json", {"skillOverrides": {"c": "off"}})
    claude.write(drop_ins / "20.txt", json.dumps({"skillOverrides": {"d": "off"}}))
    assert claude.statuses(Kind.SKILL) == {
        "a": Status.DISABLED,
        "b": Status.DISABLED,
        "c": Status.ENABLED,
        "d": Status.ENABLED,
    }


def test_later_drop_in_wins_and_lists_join(claude):
    claude.settings({"hooks": {"SessionStart": [COMMAND_HOOK]}})
    claude.write(claude.home / ".claude.json", {"mcpServers": {"x": STDIO, "y": STDIO, "z": STDIO}})
    drop_ins = claude.managed / "managed-settings.d"
    claude.write(
        drop_ins / "10.json",
        {"disableAllHooks": True, "deniedMcpServers": [{"serverName": "x"}]},
    )
    claude.write(
        drop_ins / "20.json",
        {"disableAllHooks": False, "deniedMcpServers": [{"serverName": "y"}]},
    )
    assert claude.statuses(Kind.HOOK) == {"SessionStart": Status.ENABLED}
    assert claude.statuses(Kind.MCP) == {
        "x": Status.DISABLED,
        "y": Status.DISABLED,
        "z": Status.ENABLED,
    }


def test_hook_in_a_drop_in_reports_that_file(claude):
    path = claude.write(
        claude.managed / "managed-settings.d" / "10.json",
        {"hooks": {"SessionStart": [COMMAND_HOOK]}},
    )
    hook = claude.items(Kind.HOOK)[0]
    assert (hook.source_path, hook.scope, hook.status) == (path, Scope.SYSTEM, Status.ENABLED)


def test_corrupt_drop_in_warns_and_makes_status_unknown(claude):
    write_skill(claude.dir / "skills" / "a", "description: d")
    claude.write(claude.managed / "managed-settings.d" / "10.json", "{bad")
    report = claude.scan()
    assert report.items[0].status is Status.UNKNOWN
    assert any("10.json" in warning for warning in report.warnings)


def test_project_settings_count_before_the_folder_is_trusted(claude):
    """CC-05: `claude mcp list`, `claude plugin list`, and `claude -p` all honor them."""
    claude.write(claude.home / ".claude.json", {"mcpServers": {"u": STDIO}})
    claude.write(
        claude.project / ".claude" / "settings.json", {"deniedMcpServers": [{"serverName": "u"}]}
    )
    assert claude.statuses(Kind.MCP) == {"u": Status.DISABLED}


def test_corrupt_state_makes_project_hooks_unknown(claude):
    claude.write(claude.home / ".claude.json", "{bad")
    claude.write(
        claude.project / ".claude" / "settings.json", {"hooks": {"SessionStart": [COMMAND_HOOK]}}
    )
    claude.settings({"hooks": {"Stop": [COMMAND_HOOK]}})
    assert claude.statuses(Kind.HOOK) == {"Stop": Status.ENABLED, "SessionStart": Status.UNKNOWN}


# The project's entry in the state file


def repository(claude):
    (claude.project / ".git").mkdir()
    below = claude.project / "a" / "b"
    below.mkdir(parents=True)
    claude.write(below / ".mcp.json", {"mcpServers": {"s": STDIO}})
    claude.write(below / ".claude" / "settings.json", {"enabledMcpjsonServers": ["s"]})
    return below


def test_state_entry_is_the_repository_root(claude):
    below = repository(claude)
    claude.write(
        claude.home / ".claude.json",
        {
            "projects": {
                str(claude.project): {
                    "hasTrustDialogAccepted": True,
                    "mcpServers": {"at-root": STDIO},
                },
                str(below): {"hasTrustDialogAccepted": False, "mcpServers": {"at-cwd": STDIO}},
            }
        },
    )
    assert claude.statuses(Kind.MCP, below) == {"s": Status.ENABLED, "at-root": Status.ENABLED}


@pytest.mark.parametrize("key", ["cwd", "middle", "above"])
def test_state_entry_is_not_any_other_directory(claude, key):
    below = repository(claude)
    path = {"cwd": below, "middle": below.parent, "above": claude.root}[key]
    claude.write(
        claude.home / ".claude.json", {"projects": {str(path): {"hasTrustDialogAccepted": True}}}
    )
    assert claude.statuses(Kind.MCP, below) == {"s": Status.PENDING}


def test_state_entry_uses_the_real_path(claude):
    link = claude.root / "link"
    link.symlink_to(claude.project)
    claude.write(
        claude.home / ".claude.json",
        {"projects": {str(claude.project): {"mcpServers": {"s": STDIO}}}},
    )
    assert claude.statuses(Kind.MCP, link) == {"s": Status.ENABLED}


def test_local_plugin_matches_a_real_project_path(claude):
    link = claude.root / "link"
    link.symlink_to(claude.project)
    claude.plugin(scope="local", projectPath=str(claude.project))
    assert set(claude.statuses(Kind.PLUGIN, link)) == {"p@m"}


# Skills


def test_hidden_skill_directories_are_listed(claude):
    write_skill(claude.dir / "skills" / ".hidden", "description: d")
    write_skill(claude.project / ".claude" / "skills" / ".here", "description: d")
    assert set(claude.statuses(Kind.SKILL)) == {".hidden", ".here"}


def test_skill_identity_is_its_folder(claude):
    write_skill(claude.dir / "skills" / "dir-one", "name: fm-one\ndescription: d")
    write_skill(claude.dir / "skills" / "dir-two", "name: fm-two\ndescription: d")
    claude.settings({"skillOverrides": {"fm-one": "off", "dir-two": "off"}})
    assert claude.statuses(Kind.SKILL) == {"dir-one": Status.ENABLED, "dir-two": Status.DISABLED}


def test_a_shared_name_in_different_folders_does_not_shadow(claude):
    write_skill(claude.dir / "skills" / "x", "name: same\ndescription: d")
    write_skill(claude.project / ".claude" / "skills" / "y", "name: same\ndescription: d")
    assert claude.statuses(Kind.SKILL) == {"x": Status.ENABLED, "y": Status.ENABLED}


@pytest.mark.parametrize(
    ("folder", "name", "status"),
    [
        ("angle", '"a<b>c"', Status.ENABLED),
        ("bad<dir>", "clean", Status.BROKEN),
        ("ok", "anthropic-skills", Status.BROKEN),
        ("anthropic-skills", "fine", Status.BROKEN),
        ("anthropic-skills:foo", "fine", Status.BROKEN),
        ("Anthropic-Skills", "fine", Status.BROKEN),
    ],
)
def test_names_it_refuses(claude, folder, name, status):
    write_skill(claude.dir / "skills" / folder, f"name: {name}\ndescription: d")
    assert claude.statuses(Kind.SKILL) == {folder: status}


@pytest.mark.parametrize(
    ("fields", "status", "conditional"),
    [
        ("user-invocable: false", Status.ENABLED, False),
        ("disable-model-invocation: true", Status.ENABLED, False),
        ("disable-model-invocation: true\nuser-invocable: false", Status.DISABLED, False),
        ("disable-model-invocation: 'true'\nuser-invocable: 'false'", Status.DISABLED, False),
        ("paths: '**/*.py'", Status.ENABLED, True),
        ("paths:\n  - src/**\n  - '*.md'", Status.ENABLED, True),
        ("paths: '**'", Status.ENABLED, False),
    ],
)
def test_invocation_fields(claude, fields, status, conditional):
    write_skill(claude.dir / "skills" / "x", f"{fields}\ndescription: d")
    item = claude.items(Kind.SKILL)[0]
    assert item.status is status
    assert bool(item.findings) is conditional


@pytest.mark.parametrize("name", ["skill.md", "Skill.md"])
def test_skill_file_name_is_exact_in_skill_directories(claude, name):
    claude.write(claude.dir / "skills" / "x" / name, "---\ndescription: d\n---\n")
    assert claude.statuses(Kind.SKILL) == {}


@pytest.mark.parametrize(
    ("rule", "disabled"),
    [
        ("Skill", {"p:a", "p:b", "deploy", "deploy-all", "other"}),
        ("Skill(*)", {"p:a", "p:b", "deploy", "deploy-all", "other"}),
        ("Skill(other)", {"other"}),
        ("Skill(/other)", {"other"}),
        ("Skill(p:*)", {"p:a", "p:b"}),
        ("Skill(/p:*)", {"p:a", "p:b"}),
        ("Skill(p:a)", {"p:a"}),
        ("Skill(deploy *)", {"deploy", "deploy-all"}),
        ("Skill(deploy:*)", {"deploy", "deploy-all"}),
        ("Agent(other)", set()),
    ],
)
def test_skill_deny_rules(claude, rule, disabled):
    def build(root):
        write_skill(root / "skills" / "a", "description: d")
        write_skill(root / "skills" / "b", "description: d")

    claude.plugin(build=build)
    for folder in ("deploy", "deploy-all", "other"):
        write_skill(claude.dir / "skills" / folder, "description: d")
    claude.settings({"permissions": {"deny": [rule]}})
    statuses = claude.statuses(Kind.SKILL)
    assert {name for name, status in statuses.items() if status is Status.DISABLED} == disabled


def test_deny_rule_can_use_the_display_name(claude):
    write_skill(claude.dir / "skills" / "folder", "name: shown\ndescription: d")
    claude.settings({"permissions": {"deny": ["Skill(shown)"]}})
    assert claude.statuses(Kind.SKILL) == {"folder": Status.DISABLED}


def test_agent_deny_rule_has_no_prefix(claude):
    claude.write(claude.dir / "agents" / "abc.md", "---\nname: abc\ndescription: d\n---\n")
    claude.settings({"permissions": {"deny": ["Agent(ab *)"]}})
    assert claude.statuses(Kind.SUBAGENT) == {"abc": Status.ENABLED}


# Plugins kept in skill directories


def skills_dir_plugin(claude, root=None, manifest=None, *, folder="fold"):
    base = (root or claude.dir / "skills") / folder
    claude.write(base / ".claude-plugin" / "plugin.json", {"name": "fold"} | (manifest or {}))
    write_skill(base / "skills" / "q", "description: d")
    claude.write(base / "commands" / "k.md", "Body")
    return base


def test_skills_dir_plugin_is_on_by_default(claude):
    skills_dir_plugin(claude)
    assert claude.statuses(Kind.PLUGIN) == {"fold@skills-dir": Status.ENABLED}
    assert claude.statuses(Kind.SKILL) == {"fold:q": Status.ENABLED}
    assert claude.statuses(Kind.COMMAND) == {"fold:k": Status.ENABLED}


def test_skills_dir_plugin_takes_its_name_from_the_manifest(claude):
    skills_dir_plugin(claude, manifest={"name": "other"})
    assert set(claude.statuses(Kind.SKILL)) == {"other:q"}


def test_skills_dir_plugin_root_skill_has_the_plugin_name(claude):
    base = skills_dir_plugin(claude)
    (base / "skills" / "q" / "SKILL.md").unlink()
    (base / "skills" / "q").rmdir()
    (base / "skills").rmdir()
    write_skill(base, "name: ignored\ndescription: d")
    assert claude.statuses(Kind.SKILL) == {"fold": Status.ENABLED}


@pytest.mark.parametrize(
    ("settings", "manifest"),
    [
        ({"enabledPlugins": {"fold@skills-dir": False}}, {}),
        ({}, {"defaultEnabled": False}),
    ],
)
def test_skills_dir_plugin_turned_off(claude, settings, manifest):
    skills_dir_plugin(claude, manifest=manifest)
    claude.settings(settings)
    assert claude.statuses(Kind.PLUGIN) == {"fold@skills-dir": Status.DISABLED}
    assert claude.statuses(Kind.SKILL) == {}


@pytest.mark.parametrize("trusted", [True, False])
def test_project_skills_dir_plugin_waits_for_trust(claude, trusted):
    skills_dir_plugin(claude, claude.project / ".claude" / "skills")
    claude.write(
        claude.home / ".claude.json",
        {"projects": {str(claude.project): {"hasTrustDialogAccepted": trusted}}},
    )
    status = Status.ENABLED if trusted else Status.PENDING
    assert claude.statuses(Kind.PLUGIN) == {"fold@skills-dir": status}
    assert claude.statuses(Kind.SKILL) == {"fold:q": status}


@pytest.mark.parametrize("manifest", ["{bad", {}])
def test_folder_without_a_named_manifest_stays_a_skill(claude, manifest):
    base = claude.dir / "skills" / "fold"
    claude.write(base / ".claude-plugin" / "plugin.json", manifest)
    write_skill(base, "description: d")
    assert claude.statuses(Kind.PLUGIN) == {}
    assert claude.statuses(Kind.SKILL) == {"fold": Status.ENABLED}


# Commands


def test_managed_commands(claude):
    claude.write(claude.managed / ".claude" / "commands" / "x.md", "Body")
    claude.write(claude.dir / "commands" / "x.md", "Body")
    assert [(i.scope, i.status) for i in claude.items(Kind.COMMAND)] == [
        (Scope.SYSTEM, Status.ENABLED),
        (Scope.USER, Status.SHADOWED),
    ]


def test_skill_file_in_a_commands_folder(claude):
    commands = claude.dir / "commands"
    claude.write(commands / "review" / "SKILL.md", "Body")
    claude.write(commands / "review" / "NOTES.md", "Body")
    claude.write(commands / "group" / "inner" / "skill.md", "Body")
    claude.write(commands / "group" / "inner" / "deep" / "other.md", "Body")
    claude.write(commands / "SKILL.md", "Body")
    assert set(claude.statuses(Kind.COMMAND)) == {
        "commands",
        "review",
        "group:inner",
        "group:inner:deep:other",
    }


def test_skill_file_in_a_plugin_commands_folder(claude):
    def build(root):
        claude.write(root / "commands" / "group" / "skill.md", "Body")
        claude.write(root / "commands" / "group" / "note.md", "Body")

    claude.plugin(build=build)
    assert set(claude.statuses(Kind.COMMAND)) == {"p:group"}


def test_user_command_beats_project_command(claude):
    claude.write(claude.dir / "commands" / "x.md", "Body")
    claude.write(claude.project / ".claude" / "commands" / "x.md", "Body")
    assert {i.scope: i.status for i in claude.items(Kind.COMMAND)} == {
        Scope.USER: Status.ENABLED,
        Scope.PROJECT: Status.SHADOWED,
    }


@pytest.mark.parametrize("skill_in_project", [True, False])
def test_skill_beats_command_across_scopes(claude, skill_in_project):
    skill_root, command_root = claude.project / ".claude", claude.dir
    if not skill_in_project:
        skill_root, command_root = command_root, skill_root
    write_skill(skill_root / "skills" / "x", "description: d")
    claude.write(command_root / "commands" / "x.md", "Body")
    assert claude.statuses(Kind.COMMAND) == {"x": Status.SHADOWED}


def test_command_nobody_can_invoke_is_disabled(claude):
    claude.write(
        claude.dir / "commands" / "x.md",
        "---\ndisable-model-invocation: true\nuser-invocable: false\n---\nBody",
    )
    assert claude.statuses(Kind.COMMAND) == {"x": Status.DISABLED}


# Managed servers

HTTPS = {"type": "http", "url": "https://host/a"}


@pytest.mark.parametrize(
    ("entry", "status"),
    [
        (HTTPS, Status.ENABLED),
        ({"type": "sse", "url": "https://host/a"}, Status.ENABLED),
        ({"type": "streamable-http", "url": "https://host/a"}, Status.ENABLED),
        (STDIO, Status.BROKEN),
        ({**HTTPS, "command": "x"}, Status.BROKEN),
        ({"type": "ws", "url": "wss://host/a"}, Status.BROKEN),
        ({"type": "http", "url": "http://host/a"}, Status.BROKEN),
        ({"type": "http", "url": "https://${HOST}/a"}, Status.BROKEN),
        ({**HTTPS, "headers": {"A": "${TOKEN}"}}, Status.BROKEN),
        ({**HTTPS, "headers": {"A": "a\u200bb"}}, Status.BROKEN),
    ],
)
def test_managed_settings_servers_are_stricter(claude, entry, status):
    claude.write(claude.managed / "managed-settings.json", {"managedMcpServers": {"x": entry}})
    item = claude.items(Kind.MCP)[0]
    assert item.status is status
    assert bool(item.findings) is (status is Status.BROKEN)


def test_allowlist_does_not_apply_to_managed_servers(claude):
    claude.write(claude.managed / "managed-mcp.json", {"mcpServers": {"file": STDIO}})
    claude.write(
        claude.managed / "managed-settings.json",
        {"managedMcpServers": {"setting": HTTPS}, "allowedMcpServers": []},
    )
    claude.write(claude.home / ".claude.json", {"mcpServers": {"user": STDIO}})
    assert claude.statuses(Kind.MCP) == {
        "file": Status.ENABLED,
        "setting": Status.ENABLED,
        "user": Status.DISABLED,
    }


def test_allowlist_applies_to_a_managed_file_server_reading_the_environment(claude):
    entry = {"command": "/bin/true", "args": ["${X}"]}
    claude.write(claude.managed / "managed-mcp.json", {"mcpServers": {"m": entry}})
    claude.write(claude.managed / "managed-settings.json", {"allowedMcpServers": []})
    assert claude.statuses(Kind.MCP) == {"m": Status.DISABLED}


def test_allowlist_ignores_a_variable_in_env(claude):
    entry = {"command": "/bin/true", "env": {"A": "${X}"}}
    claude.write(claude.managed / "managed-mcp.json", {"mcpServers": {"m": entry}})
    claude.write(claude.managed / "managed-settings.json", {"allowedMcpServers": []})
    assert claude.statuses(Kind.MCP) == {"m": Status.ENABLED}


def test_denylist_applies_to_managed_servers(claude):
    claude.write(claude.managed / "managed-mcp.json", {"mcpServers": {"file": STDIO}})
    claude.write(
        claude.managed / "managed-settings.json",
        {
            "managedMcpServers": {"setting": HTTPS},
            "deniedMcpServers": [{"serverName": "file"}, {"serverName": "setting"}],
        },
    )
    assert set(claude.statuses(Kind.MCP).values()) == {Status.DISABLED}


@pytest.mark.parametrize("corrupt", [True, False])
def test_unreadable_managed_file_still_has_exclusive_control(claude, corrupt):
    path = claude.managed / "managed-mcp.json"
    if corrupt:
        claude.write(path, "{bad")
    else:
        path.mkdir(parents=True)
    claude.write(claude.home / ".claude.json", {"mcpServers": {"u": STDIO}})
    report = claude.scan()
    assert [item.status for item in report.items] == [Status.DISABLED]
    assert any("managed-mcp.json" in warning for warning in report.warnings)


def test_corrupt_state_does_not_affect_managed_servers(claude):
    claude.write(claude.managed / "managed-mcp.json", {"mcpServers": {"m": STDIO}})
    claude.write(claude.home / ".claude.json", "{bad")
    assert claude.statuses(Kind.MCP) == {"m": Status.ENABLED}


def test_managed_server_beats_a_same_named_server(claude):
    claude.write(claude.managed / "managed-settings.json", {"managedMcpServers": {"same": HTTPS}})
    claude.write(
        claude.home / ".claude.json",
        {
            "mcpServers": {"same": STDIO},
            "projects": {str(claude.project): {"mcpServers": {"same": STDIO}}},
        },
    )
    assert [(i.scope, i.status) for i in claude.items(Kind.MCP)] == [
        (Scope.SYSTEM, Status.ENABLED),
        (Scope.USER, Status.SHADOWED),
        (Scope.PROJECT, Status.SHADOWED),
    ]


def test_managed_file_beats_managed_settings(claude):
    claude.write(claude.managed / "managed-mcp.json", {"mcpServers": {"same": STDIO}})
    claude.write(claude.managed / "managed-settings.json", {"managedMcpServers": {"same": HTTPS}})
    assert [i.status for i in claude.items(Kind.MCP)] == [Status.ENABLED, Status.SHADOWED]


# Strict customization


def everything(claude):
    write_skill(claude.dir / "skills" / "us", "description: d")
    claude.write(claude.dir / "commands" / "uc.md", "Body")
    claude.write(claude.dir / "agents" / "ua.md", "---\nname: ua\ndescription: d\n---\n")
    claude.settings({"hooks": {"Stop": [COMMAND_HOOK]}})
    claude.write(claude.home / ".claude.json", {"mcpServers": {"um": STDIO}})
    write_skill(claude.managed / ".claude" / "skills" / "ms", "description: d")
    claude.write(claude.managed / ".claude" / "commands" / "mc.md", "Body")
    claude.write(
        claude.managed / ".claude" / "agents" / "ma.md", "---\nname: ma\ndescription: d\n---\n"
    )

    def build(root):
        write_skill(root / "skills" / "ps", "description: d")
        claude.write(root / "commands" / "pc.md", "Body")
        claude.write(root / "agents" / "pa.md", "---\nname: pa\ndescription: d\n---\n")
        claude.write(root / "hooks" / "hooks.json", {"hooks": {"SessionEnd": [COMMAND_HOOK]}})
        claude.write(root / ".mcp.json", {"mcpServers": {"pm": {"command": "/bin/false"}}})

    claude.plugin(build=build)


def off(claude):
    return {i.name for i in claude.items() if i.status is Status.DISABLED}


@pytest.mark.parametrize(
    ("strict", "expected"),
    [
        (True, {"us", "uc", "mc", "ua", "Stop", "um"}),
        (["skills"], {"us", "uc", "mc"}),
        (["agents"], {"ua"}),
        (["hooks"], {"Stop"}),
        (["mcp"], {"um"}),
        (["commands"], set()),
        (False, set()),
    ],
)
def test_strict_customization(claude, strict, expected):
    everything(claude)
    claude.write(
        claude.managed / "managed-settings.json", {"strictPluginOnlyCustomization": strict}
    )
    assert off(claude) == expected


def test_strict_customization_only_counts_in_managed_settings(claude):
    everything(claude)
    claude.settings({"strictPluginOnlyCustomization": True})
    assert off(claude) == set()


@pytest.mark.parametrize("strict", [True, ["skills"]])
def test_strict_customization_blocks_skills_dir_plugins(claude, strict):
    skills_dir_plugin(claude)
    claude.write(
        claude.managed / "managed-settings.json", {"strictPluginOnlyCustomization": strict}
    )
    assert claude.statuses(Kind.PLUGIN) == {"fold@skills-dir": Status.DISABLED}


# Plugin servers


def plugin_servers(claude, servers, **manifest):
    def build(root):
        claude.write(root / ".mcp.json", {"mcpServers": servers})

    return claude.plugin(build=build, manifest=manifest)


def test_plugin_servers_from_every_source(claude):
    def build(root):
        claude.write(root / ".mcp.json", {"a": {"command": "a"}})
        claude.write(root / "b.json", {"mcpServers": {"b": {"command": "b"}}})
        claude.write(root / "c.json", {"c": {"command": "c"}})

    claude.plugin(
        build=build,
        manifest={"mcpServers": ["./b.json", "./c.json", {"d": {"command": "d"}}, "../x.json"]},
    )
    items = claude.items(Kind.MCP)
    assert {i.name for i in items} == {"plugin:p:a", "plugin:p:b", "plugin:p:c", "plugin:p:d"}
    assert all(i.details["plugin"] == "p@m" and i.name_prefix == "plugin:p:" for i in items)


def test_plugin_server_with_the_same_command_is_shadowed(claude):
    plugin_servers(claude, {"other": STDIO})
    claude.write(claude.home / ".claude.json", {"mcpServers": {"mine": STDIO}})
    assert claude.statuses(Kind.MCP) == {"mine": Status.ENABLED, "plugin:p:other": Status.SHADOWED}


def test_plugin_server_with_the_same_name_is_not_shadowed(claude):
    plugin_servers(claude, {"same": {"command": "/bin/false"}})
    claude.write(claude.home / ".claude.json", {"mcpServers": {"same": STDIO}})
    assert set(claude.statuses(Kind.MCP).values()) == {Status.ENABLED}


def test_identity_includes_arguments_but_not_the_remote_transport(claude):
    plugin_servers(
        claude,
        {
            "a": {"command": "c", "args": ["1"]},
            "b": {"command": "c", "args": ["2"]},
            "h": {"type": "http", "url": "http://host/a"},
            "s": {"type": "sse", "url": "http://host/a"},
        },
    )
    assert claude.statuses(Kind.MCP) == {
        "plugin:p:a": Status.ENABLED,
        "plugin:p:b": Status.ENABLED,
        "plugin:p:h": Status.ENABLED,
        "plugin:p:s": Status.SHADOWED,
    }


@pytest.mark.parametrize("state", ["pending", "disabled"])
def test_server_that_does_not_load_shadows_nothing(claude, state):
    plugin_servers(claude, {"pm": STDIO})
    if state == "pending":
        claude.write(claude.project / ".mcp.json", {"mcpServers": {"mine": STDIO}})
    else:
        claude.write(
            claude.home / ".claude.json",
            {
                "mcpServers": {"mine": STDIO},
                "projects": {str(claude.project): {"disabledMcpServers": ["mine"]}},
            },
        )
    assert claude.statuses(Kind.MCP)["plugin:p:pm"] is Status.ENABLED


def test_first_plugin_keeps_a_shared_server(claude):
    plugin_servers(claude, {"one": STDIO})
    claude.plugin(
        "q", build=lambda root: claude.write(root / ".mcp.json", {"mcpServers": {"two": STDIO}})
    )
    assert claude.statuses(Kind.MCP) == {
        "plugin:p:one": Status.ENABLED,
        "plugin:q:two": Status.SHADOWED,
    }


def test_disabled_plugin_has_no_servers(claude):
    plugin_servers(claude, {"pm": STDIO})
    claude.settings({"enabledPlugins": {"p@m": False}})
    assert claude.statuses(Kind.MCP) == {}


@pytest.mark.parametrize(
    "policy",
    [
        {"deniedMcpServers": [{"serverName": "plugin:p:pm"}]},
        {"deniedMcpServers": [{"serverCommand": ["/bin/true"]}]},
    ],
)
def test_policy_applies_to_plugin_servers(claude, policy):
    plugin_servers(claude, {"pm": STDIO})
    claude.settings(policy)
    assert claude.statuses(Kind.MCP) == {"plugin:p:pm": Status.DISABLED}


def test_plugin_server_turned_off_in_the_project(claude):
    plugin_servers(claude, {"pm": STDIO})
    claude.write(
        claude.home / ".claude.json",
        {"projects": {str(claude.project): {"disabledMcpServers": ["plugin:p:pm"]}}},
    )
    assert claude.statuses(Kind.MCP) == {"plugin:p:pm": Status.DISABLED}


def test_invalid_plugin_server_is_broken_and_keeps_no_secret(claude):
    plugin_servers(claude, {"bad": {"type": "http"}, "ok": {**STDIO, "env": {"TOKEN": "hunter2"}}})
    items = {i.name: i for i in claude.items(Kind.MCP)}
    assert items["plugin:p:bad"].status is Status.BROKEN and items["plugin:p:bad"].findings
    assert "hunter2" not in str(items["plugin:p:ok"].details)


# Plugin manifest entries


@pytest.mark.parametrize(
    "entry",
    [
        {"source": "../x.md"},
        {},
        123,
        {"source": "a.md"},
        {"source": "./a.txt"},
        {"source": "./a.md", "content": "x"},
    ],
)
def test_invalid_command_entry_costs_them_all_and_restores_the_default(claude, entry):
    def build(root):
        claude.write(root / "a.md", "Body")
        claude.write(root / "commands" / "z.md", "Body")

    claude.plugin(build=build, manifest={"commands": {"good": {"source": "./a.md"}, "bad": entry}})
    statuses = claude.statuses(Kind.COMMAND)
    assert statuses == {
        "p:good": Status.BROKEN,
        "p:bad": Status.BROKEN,
        "p:z": Status.ENABLED,
    }
    assert all(i.findings for i in claude.items(Kind.COMMAND) if i.status is Status.BROKEN)


def test_missing_command_source_costs_only_that_entry(claude):
    claude.plugin(
        build=lambda root: claude.write(root / "a.md", "Body"),
        manifest={"commands": {"good": {"source": "./a.md"}, "gone": {"source": "./no.md"}}},
    )
    assert claude.statuses(Kind.COMMAND) == {"p:good": Status.ENABLED, "p:gone": Status.BROKEN}


def test_command_entry_descriptions(claude):
    claude.plugin(
        build=lambda root: claude.write(root / "a.md", "---\ndescription: from file\n---\n"),
        manifest={
            "commands": {
                "plain": {"source": "./a.md"},
                "over": {"source": "./a.md", "description": "from manifest"},
                "inline": {"content": "---\ndescription: inline\n---\nBody"},
                "body": {"content": "First line\nmore"},
            }
        },
    )
    assert {i.name: i.description for i in claude.items(Kind.COMMAND)} == {
        "p:plain": "from file",
        "p:over": "from manifest",
        "p:inline": "inline",
        "p:body": "First line",
    }


def test_hook_file_named_twice_loads_once(claude):
    def build(root):
        claude.write(root / "hooks" / "hooks.json", {"hooks": {"Stop": [COMMAND_HOOK]}})

    claude.plugin(build=build, manifest={"hooks": ["./hooks/hooks.json", "./hooks/hooks.json"]})
    assert len(claude.items(Kind.HOOK)) == 1


def test_skills_directory_named_in_the_manifest_loads_once(claude):
    def build(root):
        write_skill(root / "skills" / "a", "description: d")
        (root / "skills" / "gone").mkdir()
        (root / "skills" / "gone" / "SKILL.md").symlink_to(root / "missing.md")

    claude.plugin(build=build, manifest={"skills": "./skills"})
    assert sorted(i.name for i in claude.items(Kind.SKILL)) == ["p:a", "p:gone"]


@pytest.mark.parametrize("file", ["hooks/hooks.json", ".claude-plugin/plugin.json", ".mcp.json"])
def test_corrupt_plugin_file_warns(claude, file):
    claude.plugin(build=lambda root: claude.write(root / file, "{bad"))
    assert any(file in warning for warning in claude.scan().warnings)


def test_corrupt_manifest_warns_once_and_defaults_still_load(claude):
    def build(root):
        claude.write(root / ".claude-plugin" / "plugin.json", "{bad")
        write_skill(root / "skills" / "a", "description: d")

    claude.plugin(build=build)
    report = claude.scan()
    assert sum("plugin.json" in warning for warning in report.warnings) == 1
    assert {i.name: i.status for i in report.items if i.kind is Kind.SKILL} == {
        "p:a": Status.ENABLED
    }


def test_plugin_in_two_scopes_lists_both_but_loads_once(claude):
    def build(root):
        write_skill(root / "skills" / "a", "description: d")
        claude.write(root / "commands" / "c.md", "Body")
        claude.write(root / "hooks" / "hooks.json", {"hooks": {"Stop": [COMMAND_HOOK]}})
        claude.write(root / ".mcp.json", {"mcpServers": {"pm": STDIO}})

    root = claude.plugin(build=build)
    record_path = claude.dir / "plugins" / "installed_plugins.json"
    record = json.loads(record_path.read_text())
    record["plugins"]["p@m"].append(
        {
            "scope": "project",
            "installPath": str(root),
            "version": "1",
            "projectPath": str(claude.project),
        }
    )
    claude.write(record_path, record)
    items = claude.items()
    assert sum(i.kind is Kind.PLUGIN for i in items) == 2
    for kind in (Kind.SKILL, Kind.COMMAND, Kind.HOOK, Kind.MCP):
        assert sum(i.kind is kind for i in items) == 1


@pytest.mark.parametrize(
    ("folder", "fields", "expected"),
    [
        ("dira", "name: fma\ndescription: d", "p:dira"),
        ("dir.b", "description: d", "p:dir-b"),
        ("dir c", "description: d", "p:dir-c"),
        ("dird", "name: p:fmd\ndescription: d", "p:dird"),
    ],
)
def test_skill_in_a_plugin_directory_is_named_after_its_folder(claude, folder, fields, expected):
    claude.plugin(build=lambda root: write_skill(root / "skills" / folder, fields))
    assert set(claude.statuses(Kind.SKILL)) == {expected}


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ("name: rooty\ndescription: d", "p:rooty"),
        ("name: p:rooty\ndescription: d", "p:rooty"),
        ("name: 'r.y'\ndescription: d", "p:r-y"),
        ("description: d", "p:p"),
    ],
)
def test_skill_at_the_plugin_root_uses_its_name(claude, fields, expected):
    claude.plugin(build=lambda root: write_skill(root, fields))
    assert set(claude.statuses(Kind.SKILL)) == {expected}


def test_skill_named_by_a_manifest_path_uses_its_name(claude):
    claude.plugin(
        build=lambda root: write_skill(root / "one", "name: fm\ndescription: d"),
        manifest={"skills": "./one"},
    )
    assert set(claude.statuses(Kind.SKILL)) == {"p:fm"}


def test_plugin_skill_ignores_paths_but_not_both_invocation_fields(claude):
    def build(root):
        write_skill(root / "skills" / "cond", "paths: '*.py'\ndescription: d")
        write_skill(
            root / "skills" / "both",
            "disable-model-invocation: true\nuser-invocable: false\ndescription: d",
        )

    claude.plugin(build=build)
    assert claude.statuses(Kind.SKILL) == {"p:cond": Status.ENABLED, "p:both": Status.DISABLED}
    assert not any(i.findings for i in claude.items(Kind.SKILL))


# Hooks in plugins


def plugin_hook(claude, **kwargs):
    return claude.plugin(
        build=lambda root: claude.write(
            root / "hooks" / "hooks.json", {"hooks": {"Stop": [COMMAND_HOOK]}}
        ),
        **kwargs,
    )


@pytest.mark.parametrize(("trusted", "status"), [(True, Status.ENABLED), (False, Status.PENDING)])
def test_project_plugin_hook_waits_for_trust(claude, trusted, status):
    plugin_hook(claude, scope="project", projectPath=str(claude.project))
    claude.write(
        claude.home / ".claude.json",
        {"projects": {str(claude.project): {"hasTrustDialogAccepted": trusted}}},
    )
    assert claude.statuses(Kind.HOOK) == {"Stop": status}


def test_user_plugin_hook_does_not_wait_for_trust(claude):
    plugin_hook(claude)
    assert claude.statuses(Kind.HOOK) == {"Stop": Status.ENABLED}


@pytest.mark.parametrize(
    ("managed", "status"),
    [
        ({"allowManagedHooksOnly": True}, Status.DISABLED),
        ({"allowManagedHooksOnly": True, "enabledPlugins": {"p@m": True}}, Status.ENABLED),
    ],
)
def test_managed_hooks_only_keeps_plugins_managed_settings_enable(claude, managed, status):
    plugin_hook(claude)
    claude.write(claude.managed / "managed-settings.json", managed)
    assert claude.statuses(Kind.HOOK) == {"Stop": status}


# HTTP hooks


@pytest.mark.parametrize(
    ("url", "status"),
    [
        ("not-a-url", Status.BROKEN),
        ("", Status.BROKEN),
        ("http://", Status.BROKEN),
        ("https://host/a", Status.ENABLED),
        ("${HOOK_URL}", Status.ENABLED),
    ],
)
def test_http_hook_url_form(claude, url, status):
    claude.settings({"hooks": {"Stop": [{"hooks": [{"type": "http", "url": url}]}]}})
    item = claude.items(Kind.HOOK)[0]
    assert item.status is status
    assert bool(item.findings) is (status is Status.BROKEN)


@pytest.mark.parametrize(
    ("allowed", "status"),
    [
        (None, Status.ENABLED),
        ([], Status.DISABLED),
        (["https://host/*"], Status.ENABLED),
        (["https://other/*"], Status.DISABLED),
    ],
)
def test_http_hook_allowlist(claude, allowed, status):
    claude.settings({"hooks": {"Stop": [{"hooks": [{"type": "http", "url": "https://host/a"}]}]}})
    if allowed is not None:
        claude.settings({"allowedHttpHookUrls": allowed})
    assert claude.statuses(Kind.HOOK) == {"Stop": status}


def test_http_hook_allowlists_join_across_layers(claude):
    claude.settings(
        {
            "hooks": {"Stop": [{"hooks": [{"type": "http", "url": "https://two/a"}]}]},
            "allowedHttpHookUrls": ["https://one/*"],
        }
    )
    claude.write(
        claude.project / ".claude" / "settings.json", {"allowedHttpHookUrls": ["https://two/*"]}
    )
    assert claude.statuses(Kind.HOOK) == {"Stop": Status.ENABLED}


def test_allowlist_does_not_touch_command_hooks(claude):
    claude.settings({"hooks": {"Stop": [COMMAND_HOOK]}, "allowedHttpHookUrls": []})
    assert claude.statuses(Kind.HOOK) == {"Stop": Status.ENABLED}


# Guard hooks

BAD_GUARDS = [
    {"PreToolUse": "x"},
    {"PreToolUse": [1]},
    {"PreToolUse": [{}]},
    {"PreToolUse": [{"hooks": "x"}]},
    {"PreToolUse": [{"hooks": [1]}]},
    {"PreToolUse": [{"hooks": [{"type": "command"}]}]},
    {"PreToolUse": [{"hooks": [{"type": "nope", "command": "x"}]}]},
    {"PreToolUse": [{"hooks": [{"type": "http", "url": "nope"}]}]},
    {"PreToolUse": [{"matcher": 123, **COMMAND_HOOK}]},
    {"PermissionRequest": [{"hooks": [{"type": "command"}]}]},
]


@pytest.mark.parametrize("hooks", BAD_GUARDS)
def test_invalid_guard_hook_costs_the_whole_settings_file(claude, hooks):
    write_skill(claude.dir / "skills" / "s", "description: d")
    claude.settings({"skillOverrides": {"s": "off"}, "hooks": {**hooks, "Stop": [COMMAND_HOOK]}})
    report = claude.scan()
    statuses = {i.name: i for i in report.items}
    assert statuses["s"].status is Status.ENABLED  # The override went with the file.
    assert statuses["Stop"].status is Status.DISABLED and statuses["Stop"].findings
    assert any("settings.json" in warning for warning in report.warnings)


def test_invalid_guard_hook_in_project_settings_costs_that_file_only(claude):
    write_skill(claude.dir / "skills" / "s", "description: d")
    claude.settings({"hooks": {"Stop": [COMMAND_HOOK]}})
    claude.write(
        claude.project / ".claude" / "settings.json",
        {"skillOverrides": {"s": "off"}, "hooks": BAD_GUARDS[5]},
    )
    assert claude.statuses(Kind.SKILL) == {"s": Status.ENABLED}
    assert claude.statuses(Kind.HOOK)["Stop"] is Status.ENABLED


def test_invalid_guard_hook_in_managed_settings_is_not_rejected(claude):
    write_skill(claude.dir / "skills" / "s", "description: d")
    claude.write(
        claude.managed / "managed-settings.json",
        {"skillOverrides": {"s": "off"}, "hooks": BAD_GUARDS[5]},
    )
    assert claude.statuses(Kind.SKILL) == {"s": Status.DISABLED}


def test_invalid_guard_hook_costs_a_plugin_hook_file(claude):
    claude.plugin(
        build=lambda root: claude.write(
            root / "hooks" / "hooks.json",
            {"hooks": {**BAD_GUARDS[5], "Stop": [COMMAND_HOOK]}},
        )
    )
    assert claude.statuses(Kind.HOOK)["Stop"] is Status.DISABLED


def test_invalid_hook_on_other_events_costs_only_that_hook(claude):
    write_skill(claude.dir / "skills" / "s", "description: d")
    claude.settings(
        {
            "skillOverrides": {"s": "off"},
            "hooks": {
                "Stop": [{"hooks": [{"type": "command"}]}],
                "SessionEnd": [COMMAND_HOOK],
            },
        }
    )
    assert claude.statuses(Kind.SKILL) == {"s": Status.DISABLED}
    assert claude.statuses(Kind.HOOK) == {"Stop": Status.BROKEN, "SessionEnd": Status.ENABLED}


def test_valid_guard_hook_keeps_the_file(claude):
    claude.settings({"hooks": {"PreToolUse": [{"matcher": "Bash", **COMMAND_HOOK}]}})
    assert not claude.scan().warnings
    assert claude.statuses(Kind.HOOK) == {"PreToolUse:Bash": Status.ENABLED}
