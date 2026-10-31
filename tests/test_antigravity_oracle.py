"""Compare results with `agy mcp list` and `agy plugin list` on the same fixture.

Runs only when AGT_VIEW_AGY points at a real agy binary, and never touches the developer's
configuration: HOME is temporary.
"""

import json
import os
import subprocess

import pytest

from agt_view.adapters.antigravity import Antigravity
from agt_view.adapters.base import Context
from agt_view.model import Kind, Scope, Status

AGY = os.environ.get("AGT_VIEW_AGY")

pytestmark = pytest.mark.skipif(not AGY, reason="AGT_VIEW_AGY is not set")


def write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(content))


def agy(home, project, *args):
    return subprocess.run(
        [AGY, *args],
        check=False,
        cwd=project,
        env={"HOME": str(home), "PATH": os.environ["PATH"], "TERM": "dumb"},
        capture_output=True,
        text=True,
        timeout=60,
    ).stdout


def scan(tmp_path, home, project):
    context = Context(home=home, cwd=project, env={"PATH": ""}, system_root=tmp_path / "system")
    return Antigravity().scan(context).items


@pytest.fixture
def dirs(tmp_path):
    home, project = tmp_path / "home", tmp_path / "project"
    (home / ".gemini" / "antigravity-cli").mkdir(parents=True)
    project.mkdir()
    return home, project


def test_user_servers_match(dirs, tmp_path):
    home, project = dirs
    write(
        home / ".gemini" / "config" / "mcp_config.json",
        {
            "mcpServers": {
                "cmd": {"command": "/bin/true"},
                "server": {"serverUrl": "http://127.0.0.1:9/m"},
                "url": {"url": "http://127.0.0.1:9/m"},
                "off": {"command": "/bin/true", "disabled": True},
            }
        },
    )
    theirs = {}
    for line in agy(home, project, "mcp", "list").splitlines()[1:]:
        if line.strip():
            parts = line.split()
            theirs[parts[0]] = parts[2] == "enabled"
    ours = {
        item.name: item.status is Status.ENABLED
        for item in scan(tmp_path, home, project)
        if item.kind is Kind.MCP
    }
    assert theirs and ours == theirs


def test_user_plugins_match(dirs, tmp_path):
    home, project = dirs
    plugins = home / ".gemini" / "config" / "plugins"
    write(plugins / "on" / "plugin.json", {"name": "on", "version": "1.0.0"})
    write(plugins / "off" / "plugin.json", {"name": "off"})
    write(plugins / "dir-name" / "plugin.json", {"name": "from-manifest"})
    write(plugins / "nested" / ".antigravity-plugin" / "plugin.json", {"name": "nested"})
    (plugins / "no-manifest").mkdir()
    write(
        home / ".gemini" / "antigravity-cli" / "plugins" / "not-read" / "plugin.json",
        {"name": "not-read"},
    )
    agy(home, project, "plugin", "disable", "off")

    theirs = {
        plugin["name"]: plugin["enabled"]
        for plugin in json.loads(agy(home, project, "plugin", "list", "--json"))
    }
    ours = {
        item.name: item.status is Status.ENABLED
        for item in scan(tmp_path, home, project)
        if item.kind is Kind.PLUGIN and item.scope is Scope.USER
    }
    assert theirs and ours == theirs


def test_blank_config_is_valid_but_malformed_is_not(dirs, tmp_path):
    home, project = dirs
    config = home / ".gemini" / "config" / "mcp_config.json"
    config.parent.mkdir(parents=True)
    config.write_text("")
    assert "No MCP servers" in agy(home, project, "mcp", "list")
    context = Context(home=home, cwd=project, env={"PATH": ""}, system_root=tmp_path / "system")
    assert Antigravity().scan(context).warnings == []

    config.write_text("{ not json")
    assert "No MCP servers" not in agy(home, project, "mcp", "list")
    assert Antigravity().scan(context).warnings
