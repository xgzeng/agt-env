"""Compare results with `agy mcp list` and `agy plugin list` on the same fixture.

Runs only when AGT_ENV_AGY points at a real agy binary, and never touches the developer's
configuration: HOME is temporary.
"""

import json
import os
import subprocess

import pytest

from agt_env.adapters.antigravity import Antigravity
from agt_env.adapters.base import Context
from agt_env.model import Kind, Scope, Status

AGY = os.environ.get("AGT_ENV_AGY")

pytestmark = pytest.mark.skipif(not AGY, reason="AGT_ENV_AGY is not set")


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


def skill(directory, frontmatter="name: a\ndescription: d"):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(f"---\n{frontmatter}\n---\nBody\n")


def manifest_key(value):
    def build(root):
        write(root / "plugin.json", {"name": "p", "skills": value})
        skill(root / "extra" / "x", "name: x\ndescription: d")
        skill(root / "skills" / "y", "name: y\ndescription: d")

    return build


def plain_layout(*directories, frontmatter="name: a\ndescription: d"):
    def build(root):
        write(root / "plugin.json", {"name": "p"})
        for directory in directories:
            skill(root / directory, frontmatter)

    return build


def escaping_key(root):
    write(root / "plugin.json", {"name": "p", "skills": "../outside"})
    skill(root.parent / "outside" / "x", "name: x\ndescription: d")
    skill(root / "skills" / "y", "name: y\ndescription: d")


def without_frontmatter(root):
    write(root / "plugin.json", {"name": "p"})
    (root / "skills" / "plain").mkdir(parents=True)
    (root / "skills" / "plain" / "SKILL.md").write_text("Body only\n")


@pytest.mark.parametrize(
    "build",
    [
        plain_layout("skills/folder"),
        plain_layout("skills/folder", frontmatter="description: d"),
        plain_layout("skills/a/b"),
        plain_layout("skills/.hidden"),
        plain_layout("."),
        manifest_key("./extra"),
        manifest_key(["./extra"]),
        escaping_key,
        without_frontmatter,
    ],
)
def test_plugin_skills_match(dirs, tmp_path, build):
    home, project = dirs
    plugins = home / ".gemini" / "config" / "plugins"
    build(plugins / "p")

    listed = json.loads(agy(home, project, "plugin", "list", "--json"))
    theirs = {f"p:{name}" for plugin in listed for name in plugin.get("skills") or ()}
    ours = {
        item.name
        for item in scan(tmp_path, home, project)
        if item.kind is Kind.SKILL and item.status is Status.ENABLED
    }
    assert ours == theirs


def agent_file(name, description="d"):
    return f"---\nname: {name}\ndescription: {description}\n---\nBody\n"


def write_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def enabled_agents(tmp_path, home, project):
    return {
        item.name
        for item in scan(tmp_path, home, project)
        if item.kind is Kind.SUBAGENT and item.status is Status.ENABLED
    }


def test_user_agents_match(dirs, tmp_path):
    home, project = dirs
    agents = home / ".gemini" / "config" / "agents"
    write_text(agents / "file.md", agent_file("from-field"))
    write_text(agents / "folder" / "agent.md", agent_file("from-folder"))
    write_text(agents / "folder-without" / "other.md", agent_file("not-loaded"))
    write_text(agents / "deep" / "deeper" / "agent.md", agent_file("not-loaded-deep"))
    write_text(agents / ".hidden.md", agent_file("hidden"))
    write_text(agents / ".hidden-folder" / "agent.md", agent_file("hidden-folder"))
    write_text(agents / "no-name.md", "---\ndescription: d\n---\nBody\n")
    write_text(agents / "no-description.md", "---\nname: no-description\n---\nBody\n")
    write_text(agents / "plain.md", "Body\n")
    write_text(agents / "text.txt", agent_file("text"))
    write_text(home / ".gemini" / "antigravity-cli" / "agents" / "c.md", agent_file("cli"))
    write_text(home / ".gemini" / "agents" / "s.md", agent_file("shared"))
    theirs = set(agy(home, project, "agents").split())
    assert theirs and enabled_agents(tmp_path, home, project) == theirs


@pytest.mark.parametrize("trusted", [False, True])
def test_workspace_agents_match(dirs, tmp_path, trusted):
    home, project = dirs
    write(
        home / ".gemini" / "antigravity-cli" / "settings.json",
        {"trustedWorkspaces": [str(project)] if trusted else []},
    )
    write_text(project / ".agents" / "agents" / "a.md", agent_file("a"))
    write_text(project / ".agent" / "agents" / "b.md", agent_file("b"))
    theirs = set(agy(home, project, "agents").split())
    assert enabled_agents(tmp_path, home, project) == theirs


def test_plugin_agents_and_hooks_match(dirs, tmp_path):
    home, project = dirs
    root = home / ".gemini" / "config" / "plugins" / "p"
    write(root / "plugin.json", {"name": "p"})
    write_text(root / "agents" / "one.md", agent_file("one"))
    write_text(root / "agents" / "two" / "agent.md", agent_file("two"))
    write_text(root / "agents" / "deep" / "x.md", agent_file("not-loaded"))
    write(
        root / "hooks.json",
        {"my-hook": {"Stop": [{"hooks": [{"command": "/bin/true"}]}]}},
    )
    listed = json.loads(agy(home, project, "plugin", "list", "--json"))[0]
    assert set(agy(home, project, "agents").split()) == set(listed["agents"]) == {"one", "two"}
    items = scan(tmp_path, home, project)
    assert enabled_agents(tmp_path, home, project) == set(listed["agents"])
    assert {i.name for i in items if i.kind is Kind.HOOK} == set(listed["hooks"])


def test_disabled_plugin_agents_do_not_load(dirs, tmp_path):
    home, project = dirs
    root = home / ".gemini" / "config" / "plugins" / "p"
    write(root / "plugin.json", {"name": "p"})
    write_text(root / "agents" / "one.md", agent_file("one"))
    agy(home, project, "plugin", "disable", "p")
    assert agy(home, project, "agents").split() == []
    assert enabled_agents(tmp_path, home, project) == set()
