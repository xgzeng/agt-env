"""Compare results with Claude Code's own listings on the same fixture.

Runs only when AGENT_VIEW_CLAUDE points at a real Claude Code binary, and never touches
the developer's configuration: HOME is temporary. `claude mcp list` starts servers to
check their health, so fixture servers only run /bin/true.
"""

import json
import os
import subprocess

import pytest

from agent_view.adapters.base import Context
from agent_view.adapters.claude_code import ClaudeCode
from agent_view.model import Kind, Scope, Status

CLAUDE = os.environ.get("AGENT_VIEW_CLAUDE")
STDIO = {"command": "/bin/true"}

pytestmark = pytest.mark.skipif(not CLAUDE, reason="AGENT_VIEW_CLAUDE is not set")


def claude(home, cwd, *args):
    return subprocess.run(
        [CLAUDE, *args],
        cwd=cwd,
        env={"PATH": os.environ["PATH"], "HOME": str(home), "DISABLE_AUTOUPDATER": "1"},
        capture_output=True,
        text=True,
        timeout=60,
    ).stdout


def scan(tmp_path, home, project):
    context = Context(home=home, cwd=project, env={"PATH": ""}, system_root=tmp_path / "system")
    return ClaudeCode().scan(context).items


def their_mcp_statuses(output):
    """Parse `name: target - <status>` lines. Connection results both mean enabled."""
    statuses = {}
    for line in output.splitlines():
        if " - " not in line or ": " not in line:
            continue
        name, state = line.split(":", 1)[0], line.rsplit(" - ", 1)[1]
        if "Pending" in state:
            statuses[name] = Status.PENDING
        elif "Disabled" in state or "Rejected" in state:
            statuses[name] = Status.DISABLED
        else:
            statuses[name] = Status.ENABLED
    return statuses


@pytest.mark.parametrize("trusted", [False, True])
def test_mcp_matches_claude_listing(tmp_path, trusted):
    home, project = tmp_path / "home", tmp_path / "project"
    (home / ".claude").mkdir(parents=True)
    (project / ".claude").mkdir(parents=True)
    (home / ".claude.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "u-stdio": STDIO,
                    "u-http": {"type": "http", "url": "http://127.0.0.1:9/mcp"},
                    "u-off": STDIO,
                },
                "projects": {
                    str(project): {
                        "hasTrustDialogAccepted": trusted,
                        "mcpServers": {"l-stdio": STDIO},
                        "disabledMcpServers": ["u-off"],
                        "enabledMcpjsonServers": ["p-entry"],
                    }
                },
            }
        )
    )
    (project / ".mcp.json").write_text(
        json.dumps(
            {"mcpServers": {name: STDIO for name in ("p-none", "p-entry", "p-shared", "p-local", "p-user")}}
        )
    )
    (project / ".claude" / "settings.json").write_text('{"enabledMcpjsonServers": ["p-shared"]}')
    (project / ".claude" / "settings.local.json").write_text(
        '{"enabledMcpjsonServers": ["p-local"]}'
    )
    (home / ".claude" / "settings.json").write_text('{"enabledMcpjsonServers": ["p-user"]}')

    theirs = their_mcp_statuses(claude(home, project, "mcp", "list"))
    ours = {i.name: i.status for i in scan(tmp_path, home, project) if i.kind is Kind.MCP}
    assert theirs and ours == theirs


def test_rejected_project_server_is_disabled(tmp_path):
    home, project = tmp_path / "home", tmp_path / "project"
    (home / ".claude").mkdir(parents=True)
    project.mkdir()
    (project / ".mcp.json").write_text(json.dumps({"mcpServers": {"p-rej": STDIO}}))
    (home / ".claude" / "settings.json").write_text('{"disabledMcpjsonServers": ["p-rej"]}')

    theirs = claude(home, project, "mcp", "list")
    ours = {i.name: i.status for i in scan(tmp_path, home, project) if i.kind is Kind.MCP}
    # Claude Code either says it's rejected or leaves it out; it never offers it.
    assert their_mcp_statuses(theirs).get("p-rej", Status.DISABLED) is Status.DISABLED
    assert ours == {"p-rej": Status.DISABLED}


def test_plugins_match_claude_listing(tmp_path):
    home, project = tmp_path / "home", tmp_path / "project"
    plugins = home / ".claude" / "plugins"
    project.mkdir()
    installs = {}
    for plugin, scope, extra in (
        ("on", "user", {}),
        ("off", "user", {}),
        ("unset", "user", {}),
        ("here", "local", {"projectPath": str(project)}),
        ("there", "local", {"projectPath": str(tmp_path / "elsewhere")}),
    ):
        directory = plugins / "cache" / "mk" / plugin / "1.0.0"
        (directory / ".claude-plugin").mkdir(parents=True)
        (directory / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({"name": plugin, "version": "1.0.0"})
        )
        installs[f"{plugin}@mk"] = [
            {"scope": scope, "installPath": str(directory), "version": "1.0.0", **extra}
        ]
    (plugins / "installed_plugins.json").write_text(json.dumps({"version": 2, "plugins": installs}))
    (home / ".claude" / "settings.json").write_text(
        json.dumps({"enabledPlugins": {"on@mk": True, "off@mk": False, "here@mk": True}})
    )

    theirs = {
        (p["id"], p["scope"], p["enabled"], p.get("projectPath"))
        for p in json.loads(claude(home, project, "plugin", "list", "--json"))
    }
    ours = {
        item.name: item
        for item in scan(tmp_path, home, project)
        if item.kind is Kind.EXTENSION
    }
    print(theirs)
    # Theirs may include other projects' installs, marked with their projectPath.
    visible = {t for t in theirs if t[3] in (None, str(project))}
    assert {t[0] for t in visible} == set(ours)
    for plugin_id, scope, enabled, _ in visible:
        item = ours[plugin_id]
        assert item.scope is (Scope.USER if scope == "user" else Scope.PROJECT)
        if item.status is Status.UNKNOWN:
            assert plugin_id == "unset@mk"
        else:
            assert (item.status is Status.ENABLED) is enabled
