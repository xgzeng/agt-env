import json
import sys

import pytest
from conftest import SCOPE_WORDS

from agt_env.adapters.base import Context
from agt_env.adapters.claude_code import ClaudeCode
from agt_env.adapters.codex import Codex
from agt_env.cli import main
from agt_env.mcp import safe_url
from agt_env.model import Kind

STDIO = {"command": "/bin/true"}


def statuses(capsys, *args):
    """Return {(kind, name, scope): status} from the output."""
    assert main(list(args)) == 0
    found = {}
    for line in capsys.readouterr().out.splitlines():
        parts = line.split()
        scope, _, kind = parts[0].partition("/") if parts else ("", "", "")
        if line.startswith("  ") and kind in ("skill", "mcp", "plugin"):
            found[(kind, parts[1], SCOPE_WORDS[scope])] = parts[2]
    return found


@pytest.fixture
def claude(machine):
    machine.install("claude")
    (machine.home / ".claude").mkdir()
    return machine


@pytest.fixture
def codex(machine):
    machine.install("codex")
    (machine.home / ".codex").mkdir()
    return machine


def write_state(machine, **fields):
    (machine.home / ".claude.json").write_text(json.dumps(fields))


def write_settings(path, **fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(fields))


def write_codex_config(machine, text):
    (machine.home / ".codex" / "config.toml").write_text(text)


def test_claude_user_and_local_servers(claude, capsys):
    write_state(
        claude,
        mcpServers={"personal": STDIO, "off-here": STDIO},
        projects={
            str(claude.project): {
                "mcpServers": {"just-here": {"type": "http", "url": "https://x.test/mcp"}},
                "disabledMcpServers": ["off-here"],
            }
        },
    )
    assert statuses(capsys) == {
        ("mcp", "personal", "user"): "enabled",
        ("mcp", "off-here", "user"): "disabled",
        ("mcp", "just-here", "project"): "enabled",
    }


def test_claude_other_projects_servers_not_listed(claude, capsys):
    write_state(claude, projects={"/elsewhere": {"mcpServers": {"not-here": STDIO}}})
    assert statuses(capsys) == {}


@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        ({"url": "https://x.test"}, "broken"),  # url without type
        ({"type": "http"}, "broken"),
        ({"type": "sdk"}, "broken"),
        ({}, "broken"),
        ("not an object", "broken"),
        ({"type": "stdio", "command": "x"}, "enabled"),
        ({"type": "streamable-http", "url": "https://x.test"}, "enabled"),
        ({"type": "sse", "url": "https://x.test"}, "enabled"),
        ({"type": "ws", "url": "wss://x.test"}, "enabled"),
    ],
)
def test_claude_entry_validity(claude, capsys, entry, expected):
    write_state(claude, mcpServers={"s": entry})
    assert statuses(capsys)[("mcp", "s", "user")] == expected


def project_server_status(machine, capsys, trusted=False):
    (machine.project / ".mcp.json").write_text(json.dumps({"mcpServers": {"s": STDIO}}))
    write_state(machine, projects={str(machine.project): {"hasTrustDialogAccepted": trusted}})
    return statuses(capsys)[("mcp", "s", "project")]


def test_claude_project_server_waits_for_approval(claude, capsys):
    assert project_server_status(claude, capsys) == "pending"
    assert project_server_status(claude, capsys, trusted=True) == "pending"


@pytest.mark.parametrize(
    ("layer", "trusted", "expected"),
    [
        (".claude/settings.json", False, "pending"),
        (".claude/settings.json", True, "enabled"),
        (".claude/settings.local.json", False, "pending"),
        (".claude/settings.local.json", True, "enabled"),
    ],
)
def test_claude_project_approvals_need_trust(claude, capsys, layer, trusted, expected):
    write_settings(claude.project / layer, enabledMcpjsonServers=["s"])
    assert project_server_status(claude, capsys, trusted) == expected


def test_claude_user_approval_applies_without_trust(claude, capsys):
    write_settings(claude.home / ".claude" / "settings.json", enableAllProjectMcpServers=True)
    assert project_server_status(claude, capsys) == "enabled"


def test_claude_rejection_wins_in_any_settings_file(claude, capsys):
    write_settings(claude.home / ".claude" / "settings.json", enableAllProjectMcpServers=True)
    write_settings(claude.project / ".claude" / "settings.json", disabledMcpjsonServers=["s"])
    assert project_server_status(claude, capsys) == "disabled"


def test_claude_managed_file_takes_exclusive_control(claude, capsys):
    managed = claude.claude_managed / "managed-mcp.json"
    write_settings(managed, mcpServers={"company": STDIO})
    write_state(claude, mcpServers={"personal": STDIO})
    assert statuses(capsys) == {
        ("mcp", "company", "system"): "enabled",
        ("mcp", "personal", "user"): "disabled",
    }


@pytest.mark.parametrize(
    ("policy", "expected"),
    [
        ({"deniedMcpServers": [{"serverName": "s"}]}, "disabled"),
        ({"deniedMcpServers": [{"serverCommand": ["/bin/true"]}]}, "disabled"),
        ({"deniedMcpServers": [{"serverName": "other"}]}, "enabled"),
        ({"allowedMcpServers": []}, "disabled"),
        ({"allowedMcpServers": [{"serverName": "s"}]}, "enabled"),
        ({"allowedMcpServers": [{"serverName": "other"}]}, "disabled"),
    ],
)
def test_claude_policy_lists(claude, capsys, policy, expected):
    write_settings(claude.claude_managed / "managed-settings.json", **policy)
    write_state(claude, mcpServers={"s": STDIO})
    assert statuses(capsys)[("mcp", "s", "user")] == expected


def test_claude_url_policy_wildcard(claude, capsys):
    write_settings(
        claude.home / ".claude" / "settings.json",
        deniedMcpServers=[{"serverUrl": "https://*.bad.test/*"}],
    )
    write_state(claude, mcpServers={"s": {"type": "http", "url": "https://a.bad.test/mcp"}})
    assert statuses(capsys)[("mcp", "s", "user")] == "disabled"


def test_claude_managed_only_allowlist_ignores_user_allowlist(claude, capsys):
    write_settings(
        claude.claude_managed / "managed-settings.json",
        allowManagedMcpServersOnly=True,
        allowedMcpServers=[],
    )
    write_settings(
        claude.home / ".claude" / "settings.json", allowedMcpServers=[{"serverName": "s"}]
    )
    write_state(claude, mcpServers={"s": STDIO})
    assert statuses(capsys)[("mcp", "s", "user")] == "disabled"


def test_claude_state_file_follows_config_dir_override(machine, capsys, tmp_path, monkeypatch):
    machine.install("claude")
    moved = tmp_path / "moved"
    moved.mkdir()
    (moved / ".claude.json").write_text(json.dumps({"mcpServers": {"moved": STDIO}}))
    write_state(machine, mcpServers={"home": STDIO})
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(moved))
    assert list(statuses(capsys)) == [("mcp", "moved", "user")]


def write_claude_plugins(machine, plugins):
    path = machine.home / ".claude" / "plugins" / "installed_plugins.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 2, "plugins": plugins}))


def install_claude_plugin(machine, plugin_id, **fields):
    directory = machine.home / ".claude" / "plugins" / "cache" / plugin_id
    (directory / ".claude-plugin").mkdir(parents=True)
    (directory / ".claude-plugin" / "plugin.json").write_text('{"description": "does things"}')
    return {"scope": "user", "installPath": str(directory), "version": "1.0", **fields}


def test_claude_plugin_status(claude, capsys):
    write_claude_plugins(
        claude,
        {
            "on@m": [install_claude_plugin(claude, "on@m")],
            "off@m": [install_claude_plugin(claude, "off@m")],
            "unset@m": [install_claude_plugin(claude, "unset@m")],
            "gone@m": [{"scope": "user", "installPath": "/does/not/exist"}],
        },
    )
    write_settings(
        claude.home / ".claude" / "settings.json",
        enabledPlugins={"on@m": True, "off@m": False, "gone@m": True},
    )
    assert statuses(capsys) == {
        ("plugin", "on@m", "user"): "enabled",
        ("plugin", "off@m", "user"): "disabled",
        ("plugin", "unset@m", "user"): "disabled",
        ("plugin", "gone@m", "user"): "broken",
    }


def test_claude_project_settings_override_plugin_state(claude, capsys):
    write_claude_plugins(claude, {"p@m": [install_claude_plugin(claude, "p@m")]})
    write_settings(claude.home / ".claude" / "settings.json", enabledPlugins={"p@m": True})
    write_settings(claude.project / ".claude" / "settings.json", enabledPlugins={"p@m": False})
    assert statuses(capsys)[("plugin", "p@m", "user")] == "disabled"


def test_claude_project_plugin_only_listed_in_its_project(claude, capsys):
    write_claude_plugins(
        claude,
        {
            "here@m": [
                install_claude_plugin(
                    claude, "here@m", scope="project", projectPath=str(claude.project)
                )
            ],
            "there@m": [
                install_claude_plugin(claude, "there@m", scope="local", projectPath="/elsewhere")
            ],
        },
    )
    assert list(statuses(capsys)) == [("plugin", "here@m", "project")]


def test_codex_servers(codex, capsys):
    write_codex_config(
        codex,
        """
[mcp_servers.local]
command = "/bin/true"
[mcp_servers.remote]
url = "https://x.test/mcp"
[mcp_servers.off]
command = "/bin/true"
enabled = false
""",
    )
    assert statuses(capsys) == {
        ("mcp", "local", "user"): "enabled",
        ("mcp", "off", "user"): "disabled",
        ("mcp", "remote", "user"): "enabled",
    }


@pytest.mark.parametrize(
    ("trust", "expected"),
    [
        ("", "pending"),
        ('trust_level = "untrusted"', "disabled"),
        ('trust_level = "trusted"', "enabled"),
    ],
)
def test_codex_project_servers_depend_on_trust(codex, capsys, trust, expected):
    (codex.project / ".codex").mkdir()
    (codex.project / ".codex" / "config.toml").write_text(
        '[mcp_servers.repo]\ncommand = "/bin/true"\n'
    )
    write_codex_config(codex, f'[projects."{codex.project}"]\n{trust}\n')
    assert statuses(capsys) == {("mcp", "repo", "project"): expected}


def test_codex_project_config_not_duplicated_when_cwd_is_home(codex, capsys, monkeypatch):
    write_codex_config(codex, '[mcp_servers.s]\ncommand = "x"\n')
    monkeypatch.chdir(codex.home)
    assert statuses(capsys) == {("mcp", "s", "user"): "enabled"}


def test_codex_plugins(codex, capsys):
    cache = codex.home / ".codex" / "plugins" / "cache" / "m"
    for name in ("on", "off", "unset", "never-configured"):
        path = cache / name / "1.0" / ".codex-plugin" / "plugin.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"name": name}))
    write_codex_config(
        codex,
        """
[plugins."on@m"]
enabled = true
[plugins."off@m"]
enabled = false
[plugins."unset@m"]
[plugins."missing@m"]
enabled = true
""",
    )
    # A cached plugin with no config entry isn't reported as available.
    assert statuses(capsys) == {
        ("plugin", "missing@m", "user"): "broken",
        ("plugin", "off@m", "user"): "disabled",
        ("plugin", "on@m", "user"): "enabled",
        ("plugin", "unset@m", "user"): "enabled",
    }


def test_kind_filter_and_order(claude, capsys):
    write_state(claude, mcpServers={"a-server": STDIO})
    write_claude_plugins(claude, {"a-plugin@m": [install_claude_plugin(claude, "a-plugin@m")]})
    skill = claude.home / ".claude" / "skills" / "z-skill"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("x")
    assert [key[0] for key in statuses(capsys)] == ["skill", "mcp", "plugin"]
    assert list(statuses(capsys, "--kind", "mcp")) == [("mcp", "a-server", "user")]


def test_servers_with_one_config_file_are_not_marked_shared(claude, capsys):
    write_state(claude, mcpServers={"one": STDIO, "two": STDIO})
    main(["--detail"])
    assert "Shared with" not in capsys.readouterr().out


def test_mcp_path_detail_shows_config_file(claude, capsys):
    write_state(claude, mcpServers={"s": STDIO})
    (claude.project / ".mcp.json").write_text('{"mcpServers": {"r": {"command": "x"}}}')
    main(["--detail"])
    out = capsys.readouterr().out
    assert "~/.claude.json" in out and "./.mcp.json" in out


SECRET = "hunter2-do-not-print"


def plant_secrets(machine):
    entry = {
        "command": "server",
        "args": ["--token", SECRET],
        "env": {"API_KEY": SECRET},
    }
    remote = {
        "type": "http",
        "url": f"https://user:{SECRET}@x.test/mcp?key={SECRET}",
        "headers": {"Authorization": f"Bearer {SECRET}"},
    }
    write_state(machine, mcpServers={"local": entry, "remote": remote})
    (machine.home / ".codex").mkdir()
    write_codex_config(
        machine,
        f"""
[mcp_servers.local]
command = "server"
args = ["--token", "{SECRET}"]
[mcp_servers.local.env]
API_KEY = "{SECRET}"
[mcp_servers.remote]
url = "https://user:{SECRET}@x.test/mcp?key={SECRET}"
[mcp_servers.remote.http_headers]
Authorization = "Bearer {SECRET}"
""",
    )


def test_secrets_never_printed(machine, capsys):
    machine.install("claude", "codex")
    (machine.home / ".claude").mkdir()
    plant_secrets(machine)
    assert main(["--all", "--detail"]) == 0
    captured = capsys.readouterr()
    assert "local" in captured.out and "remote" in captured.out
    assert SECRET not in captured.out + captured.err


def test_secrets_not_kept_in_item_details(machine):
    (machine.home / ".claude").mkdir()
    plant_secrets(machine)
    context = Context(home=machine.home, cwd=machine.project, env={"PATH": ""})
    items = [*ClaudeCode().scan(context).items, *Codex().scan(context).items]
    servers = [item for item in items if item.kind is Kind.MCP]
    assert len(servers) == 4
    assert SECRET not in repr(servers)
    remote = next(s.details for s in servers if s.details.get("transport") == "http")
    assert remote == {
        "transport": "http",
        "url": "https://x.test/mcp",
        "headers": ["Authorization"],
    }


def test_listing_never_starts_a_server(machine, capsys, tmp_path):
    machine.install("claude", "codex")
    (machine.home / ".claude").mkdir()
    (machine.home / ".codex").mkdir()
    marker = tmp_path / "started"
    script = tmp_path / "server.sh"
    script.write_text(f"#!/bin/sh\ntouch {marker}\n")
    script.chmod(0o755)
    server = {"command": str(script)}
    write_state(machine, mcpServers={"s": server})
    (machine.project / ".mcp.json").write_text(json.dumps({"mcpServers": {"r": server}}))
    write_codex_config(machine, f'[mcp_servers.s]\ncommand = "{script}"\n')
    assert main(["--all"]) == 0
    assert "s" in capsys.readouterr().out
    assert not marker.exists() and not machine.executed


@pytest.mark.parametrize(
    "corrupted",
    [
        ".claude.json",
        ".mcp.json",
        ".claude/settings.json",
        ".claude/plugins/installed_plugins.json",
    ],
)
def test_corrupt_claude_files_warn_and_keep_other_agents(machine, capsys, corrupted):
    machine.install("claude", "codex")
    (machine.home / ".claude" / "plugins").mkdir(parents=True)
    (machine.home / ".codex").mkdir()
    write_codex_config(machine, '[mcp_servers.fine]\ncommand = "x"\n')
    base = machine.project if corrupted == ".mcp.json" else machine.home
    (base / corrupted).write_text("{ not json")
    assert main([]) == 0
    captured = capsys.readouterr()
    assert "fine" in captured.out
    assert f"warning: claude-code: cannot read {base / corrupted}" in captured.err


def test_corrupt_project_mcp_file_does_not_hide_user_servers(claude, capsys):
    write_state(claude, mcpServers={"s": STDIO})
    (claude.project / ".mcp.json").write_text("{ not json")
    assert statuses(capsys)[("mcp", "s", "user")] == "enabled"


def test_corrupt_settings_make_server_status_unknown(claude, capsys):
    write_state(claude, mcpServers={"s": STDIO})
    (claude.home / ".claude" / "settings.json").write_text("{ not json")
    assert statuses(capsys)[("mcp", "s", "user")] == "unknown"


def test_hostile_server_name_cannot_control_the_terminal(claude, capsys):
    write_state(claude, mcpServers={"evil\x1b[2J\x1b]0;title\x07name": STDIO})
    main(["--detail"])
    out = capsys.readouterr().out
    assert "\x1b" not in out and "\x07" not in out and "evilname" in out


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://a:b@x.test:8080/p?k=v#f", "https://x.test:8080/p"),
        ("https://x.test/mcp", "https://x.test/mcp"),
        (123, None),
    ],
)
def test_safe_url(url, expected):
    assert safe_url(url) == expected


@pytest.mark.parametrize(
    ("platform", "directory"),
    [("linux", "etc/claude-code"), ("darwin", "Library/Application Support/ClaudeCode")],
)
def test_claude_managed_location_depends_on_the_platform(
    claude, capsys, monkeypatch, platform, directory
):
    monkeypatch.setattr(sys, "platform", platform)
    write_settings(claude.system / directory / "managed-mcp.json", mcpServers={"company": STDIO})
    assert statuses(capsys) == {("mcp", "company", "system"): "enabled"}
