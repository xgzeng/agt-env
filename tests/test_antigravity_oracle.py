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
        {"my-hook": {"Stop": [{"command": "/bin/true"}]}},
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


# Rules from the review in docs/reviews/antigravity.md. Print mode answers some slash
# commands without signing in or starting a turn, so these compare with `/skills`,
# `/agents`, `/hooks`, and `/plugin list`.


def listing(home, project, command):
    output = agy(home, project, "-p", command, "--output-format", "json")
    return json.loads(output)["command"]["data"]


def trusted(home, project):
    write(
        home / ".gemini" / "antigravity-cli" / "settings.json",
        {"trustedWorkspaces": [str(project)]},
    )


def write_skill_dir(path, name="NAME", description="d"):
    path.mkdir(parents=True, exist_ok=True)
    name = path.name if name == "NAME" else name
    front = (f"name: {name}\n" if name else "") + f"description: {description}\n"
    (path / "SKILL.md").write_text(f"---\n{front}---\nbody\n")


def their_skills(home, project):
    return sorted(
        (skill["name"], os.path.realpath(skill["path"]))
        for skill in listing(home, project, "/skills")["skills"]
        if not skill.get("builtin")
    )


def our_skills(tmp_path, home, project):
    return sorted(
        (item.name, os.path.realpath(item.source_path / "SKILL.md"))
        for item in scan(tmp_path, home, project)
        if item.kind is Kind.SKILL and item.status is Status.ENABLED
    )


def agent_text(name, description="d", body="b"):
    return f"---\nname: {name}\ndescription: {description}\n---\n{body}\n"


@pytest.mark.parametrize(
    "places",
    [
        [".agents/skills", "~/.gemini/config/skills"],
        ["~/.gemini/antigravity-cli/skills", "~/.gemini/config/skills", "~/.gemini/skills"],
        ["~/.gemini/config/skills", "~/.gemini/skills"],
        [".agents/skills", ".agent/skills"],
        [".agents/skills", "_agents/skills"],
        [".agents/skills", "_agent/skills"],
        ["_agents/skills", "_agent/skills"],
        [".agent/skills", "_agent/skills"],
    ],
)
def test_skill_precedence_matches(dirs, tmp_path, places):
    home, project = dirs
    trusted(home, project)
    for place in places:
        root = home / place[2:] if place.startswith("~/") else project / place
        write_skill_dir(root / "same")
    assert our_skills(tmp_path, home, project) == their_skills(home, project)


def test_unnamed_skills_collide_like_agy(dirs, tmp_path):
    home, project = dirs
    write_skill_dir(home / ".gemini" / "config" / "skills" / "u1", name=None)
    write_skill_dir(home / ".gemini" / "config" / "skills" / "u2", name=None)
    assert our_skills(tmp_path, home, project) == their_skills(home, project)


@pytest.mark.parametrize("workspace", ["_agents", "_agent", ".agent"])
def test_other_workspace_directories_match(dirs, tmp_path, workspace):
    home, project = dirs
    trusted(home, project)
    root = project / workspace
    write_skill_dir(root / "skills" / "sk")
    (root / "agents").mkdir(parents=True)
    (root / "agents" / "ag.md").write_text(agent_text("ag"))
    write(root / "hooks.json", {"h": {"Stop": [{"command": "/bin/h"}]}})
    write(root / "plugins" / "pl" / "plugin.json", {"name": "pl"})
    assert our_skills(tmp_path, home, project) == their_skills(home, project)
    ours = scan(tmp_path, home, project)
    assert listing(home, project, "/agents")["agents"] == [
        i.name for i in ours if i.kind is Kind.SUBAGENT
    ]
    assert [h["name"] for h in listing(home, project, "/hooks")["hooks"]] == [
        i.name for i in ours if i.kind is Kind.HOOK
    ]
    assert [p["name"] for p in listing(home, project, "/plugin list")["plugins"]] == [
        i.name for i in ours if i.kind is Kind.PLUGIN
    ]


def declare(tmp_path, project, config):
    shared = tmp_path / "shared"
    for name in ("a", "b", "exp-1"):
        write_skill_dir(shared / name)
    write_skill_dir(shared / "cat" / "nested")
    write(project / ".agents" / "skills.json", config(shared))


@pytest.mark.parametrize(
    "config",
    [
        lambda shared: {"entries": [{"path": str(shared)}]},
        lambda shared: {"entries": [{"path": str(shared), "exclude": ["b"]}]},
        lambda shared: {"entries": [{"path": str(shared), "exclude": ["exp-.*"]}]},
        lambda shared: {"entries": [{"path": str(shared), "include_only": ["cat/nested"]}]},
        lambda shared: {"entries": [{"path": str(shared)}], "exclude": ["a"]},
        lambda shared: {"entries": [{"path": "../shared"}]},
        lambda shared: {"entries": [{"path": str(shared / "a")}]},
        lambda shared: {"entries": ["x", {"path": str(shared)}]},
    ],
)
def test_declared_skills_match(dirs, tmp_path, config):
    home, project = dirs
    trusted(home, project)
    declare(tmp_path, project, config)
    assert our_skills(tmp_path, home, project) == their_skills(home, project)


def test_inherited_declarations_match(dirs, tmp_path):
    home, project = dirs
    trusted(home, project)
    shared = tmp_path / "shared"
    write_skill_dir(shared / "a")
    write_skill_dir(shared / "b")
    write(tmp_path / "inherit.json", {"entries": [{"path": str(shared)}]})
    write(
        project / ".agents" / "skills.json",
        {"inherits": [{"path": str(tmp_path / "inherit.json"), "exclude": ["b"]}]},
    )
    assert our_skills(tmp_path, home, project) == their_skills(home, project)


@pytest.mark.parametrize(
    ("declared_in", "plain"),
    [
        (".agents", ".agents/skills"),
        (".agents", "~/.gemini/config/skills"),
        ("~/.gemini/config", "~/.gemini/config/skills"),
        ("~/.gemini/config", "~/.gemini/antigravity-cli/skills"),
        ("~/.gemini/config", ".agents/skills"),
    ],
)
def test_declared_skill_precedence_matches(dirs, tmp_path, declared_in, plain):
    home, project = dirs
    trusted(home, project)
    write_skill_dir(tmp_path / "declared" / "same")
    root = home / declared_in[2:] if declared_in.startswith("~/") else project / declared_in
    write(root / "skills.json", {"entries": [{"path": str(tmp_path / "declared")}]})
    place = home / plain[2:] if plain.startswith("~/") else project / plain
    write_skill_dir(place / "same")
    assert our_skills(tmp_path, home, project) == their_skills(home, project)


def test_declared_plugins_and_agents_match(dirs, tmp_path):
    home, project = dirs
    trusted(home, project)
    write(tmp_path / "pl" / "jp" / "plugin.json", {"name": "jp"})
    write_skill_dir(tmp_path / "pl" / "jp" / "skills" / "pjs")
    write(project / ".agents" / "plugins.json", {"entries": [{"path": str(tmp_path / "pl")}]})
    (tmp_path / "ag").mkdir()
    (tmp_path / "ag" / "ja.md").write_text(agent_text("ja"))
    write(
        home / ".gemini" / "config" / "agents.json", {"entries": [{"path": str(tmp_path / "ag")}]}
    )
    ours = scan(tmp_path, home, project)
    assert [p["name"] for p in listing(home, project, "/plugin list")["plugins"]] == [
        i.name for i in ours if i.kind is Kind.PLUGIN
    ]
    assert listing(home, project, "/agents")["agents"] == [
        i.name for i in ours if i.kind is Kind.SUBAGENT
    ]
    assert our_skills(tmp_path, home, project) == their_skills(home, project)


def their_hooks(home, project):
    return sorted(
        (hook["name"], hook["enabled"], hook["source"], len(hook.get("actions") or []))
        for hook in listing(home, project, "/hooks")["hooks"]
    )


def our_hooks(tmp_path, home, project):
    return sorted(
        (
            item.name,
            item.status is Status.ENABLED,
            str(item.source_path),
            0 if item.status is Status.BROKEN else len(item.details["event"]),
        )
        for item in scan(tmp_path, home, project)
        if item.kind is Kind.HOOK
        and item.status in (Status.ENABLED, Status.DISABLED, Status.BROKEN)
    )


@pytest.mark.parametrize(
    "hook",
    [
        {"Stop": [{"command": "/bin/true"}]},
        {"SessionStart": [{"type": "command", "command": "/bin/a"}]},
        {"PreToolUse": [{"matcher": "x", "hooks": [{"type": "command", "command": "/bin/b"}]}]},
        {"enabled": False, "Stop": [{"command": "/bin/f"}]},
    ],
)
def test_hook_shapes_match(dirs, tmp_path, hook):
    home, project = dirs
    write(home / ".gemini" / "config" / "hooks.json", {"h": hook})
    assert our_hooks(tmp_path, home, project) == their_hooks(home, project)


@pytest.mark.parametrize(
    "hook",
    [
        {"Stop": [{"hooks": [{"type": "command", "command": "/bin/c"}]}]},
        {"PreToolUse": [{"command": "/bin/d"}]},
        {"Unknown": [{"command": "/bin/e"}]},
        {"Stop": []},
    ],
)
def test_hooks_that_run_nothing_are_broken(dirs, tmp_path, hook):
    home, project = dirs
    write(home / ".gemini" / "config" / "hooks.json", {"h": hook})
    theirs = listing(home, project, "/hooks")["hooks"]
    assert [h["name"] for h in theirs] == ["h"]
    assert not [a for a in theirs[0].get("actions") or [] if a.get("command")]
    ours = [i for i in scan(tmp_path, home, project) if i.kind is Kind.HOOK]
    assert [i.status for i in ours] == [Status.BROKEN]


@pytest.mark.parametrize(
    "content",
    [
        {"h": {"Stop": [{"type": "zzz"}]}, "ok": {"Stop": [{"command": "x"}]}},
        {"h": {"Stop": {"command": "x"}}, "ok": {"Stop": [{"command": "x"}]}},
        {"h": "x", "ok": {"Stop": [{"command": "x"}]}},
    ],
)
def test_a_bad_hooks_file_loads_nothing_like_agy(dirs, tmp_path, content):
    home, project = dirs
    write(home / ".gemini" / "config" / "hooks.json", content)
    assert listing(home, project, "/hooks")["hooks"] == []
    assert not [i for i in scan(tmp_path, home, project) if i.kind is Kind.HOOK]


@pytest.mark.parametrize(
    "places",
    [
        [".agents", "~/.gemini/config"],
        [".agent", ".agents"],
        ["_agent", "_agents"],
        [".agents", "_agents"],
        [".agents", "plugin"],
        ["~/.gemini/config", "plugin"],
    ],
)
def test_hook_name_precedence_matches(dirs, tmp_path, places):
    home, project = dirs
    trusted(home, project)
    for number, place in enumerate(places):
        if place == "plugin":
            root = home / ".gemini" / "config" / "plugins" / "p"
            write(root / "plugin.json", {})
        else:
            root = home / place[2:] if place.startswith("~/") else project / place
        write(root / "hooks.json", {"h": {"Stop": [{"command": f"/bin/{number}"}]}})
    theirs = [h["source"] for h in listing(home, project, "/hooks")["hooks"]]
    ours = [
        str(i.source_path)
        for i in scan(tmp_path, home, project)
        if i.kind is Kind.HOOK and i.status is Status.ENABLED
    ]
    assert ours == theirs


def test_plugin_servers_are_listed(dirs, tmp_path):
    home, project = dirs
    root = home / ".gemini" / "config" / "plugins" / "p"
    write(root / "plugin.json", {"name": "p"})
    write(root / "mcp_config.json", {"mcpServers": {"ps": {"command": "/bin/true"}, "bad": {}}})
    theirs = json.loads(agy(home, project, "plugin", "list", "--json"))[0]["mcpServers"]
    ours = [i for i in scan(tmp_path, home, project) if i.kind is Kind.MCP]
    assert sorted(i.name for i in ours) == sorted(theirs)
    assert {i.name: i.status for i in ours} == {"ps": Status.ENABLED, "bad": Status.BROKEN}
    assert all(i.details.get("plugin") == "p" for i in ours)


@pytest.mark.parametrize(
    ("where", "listed"),
    [
        ("plugin.json", True),
        (".antigravity-plugin/plugin.json", True),
        (".gemini-plugin/plugin.json", True),
        (".claude-plugin/plugin.json", False),
        ("gemini-extension.json", False),
    ],
)
def test_plugin_manifest_locations_match(dirs, tmp_path, where, listed):
    home, project = dirs
    write(home / ".gemini" / "config" / "plugins" / "p" / where, {"name": "p"})
    theirs = json.loads(agy(home, project, "plugin", "list", "--json") or "[]")
    ours = [i for i in scan(tmp_path, home, project) if i.kind is Kind.PLUGIN]
    assert bool(theirs) is bool(ours) is listed


@pytest.mark.parametrize(
    ("config", "enabled"),
    [(None, False), ({"plugins": {"p": {"enabled": True}}}, True)],
)
def test_a_plugin_can_ship_turned_off(dirs, tmp_path, config, enabled):
    home, project = dirs
    write(home / ".gemini" / "config" / "plugins" / "p" / "plugin.json", {"disabled": True})
    if config:
        write(home / ".gemini" / "config" / "config.json", config)
    theirs = listing(home, project, "/plugin list")["plugins"][0]["enabled"]
    plugin = next(i for i in scan(tmp_path, home, project) if i.kind is Kind.PLUGIN)
    assert theirs is enabled
    assert (plugin.status is Status.ENABLED) is enabled


def test_a_server_with_a_url_and_a_command_is_http_like_agy(dirs, tmp_path):
    home, project = dirs
    write(
        home / ".gemini" / "config" / "mcp_config.json",
        {"mcpServers": {"both": {"command": "/bin/true", "url": "http://127.0.0.1:9/m"}}},
    )
    line = agy(home, project, "mcp", "list").splitlines()[1].split()
    item = next(i for i in scan(tmp_path, home, project) if i.kind is Kind.MCP)
    assert line[1] == "http"
    assert item.details.get("transport") == "streamable_http"


@pytest.mark.parametrize("listed", ["link", "real"])
def test_trust_with_a_link_matches_agy(dirs, tmp_path, listed):
    home, _ = dirs
    real = tmp_path / "real"
    link = tmp_path / "link"
    (real / ".agents" / "agents").mkdir(parents=True)
    (real / ".agents" / "agents" / "wsa.md").write_text(agent_text("wsa"))
    link.symlink_to(real)
    write(
        home / ".gemini" / "antigravity-cli" / "settings.json",
        {"trustedWorkspaces": [str(link if listed == "link" else real)]},
    )
    for cwd in (real, link):
        theirs = "wsa" in agy(home, cwd, "agents")
        agent = next(
            i for i in scan(tmp_path, home, cwd) if i.kind is Kind.SUBAGENT and i.name == "wsa"
        )
        assert theirs is (agent.status is Status.ENABLED)


def test_an_agent_with_no_body_still_loads(dirs, tmp_path):
    home, project = dirs
    (home / ".gemini" / "config" / "agents").mkdir(parents=True)
    (home / ".gemini" / "config" / "agents" / "empty.md").write_text(agent_text("empty", body=""))
    assert listing(home, project, "/agents")["agents"] == ["empty"]
    agent = next(i for i in scan(tmp_path, home, project) if i.kind is Kind.SUBAGENT)
    assert agent.status is Status.ENABLED


def test_shared_skills_and_a_turned_off_workspace_plugin_match(dirs, tmp_path):
    home, project = dirs
    trusted(home, project)
    write_skill_dir(home / ".gemini" / "skills" / "shared-skill")
    write(project / ".agents" / "plugins" / "ws" / "plugin.json", {"name": "ws"})
    write_skill_dir(project / ".agents" / "plugins" / "ws" / "skills" / "pw")
    write(home / ".gemini" / "config" / "config.json", {"plugins": {"ws": {"enabled": False}}})
    assert our_skills(tmp_path, home, project) == their_skills(home, project)
    plugin = next(i for i in scan(tmp_path, home, project) if i.kind is Kind.PLUGIN)
    assert listing(home, project, "/plugin list")["plugins"][0]["enabled"] is False
    assert plugin.status is Status.DISABLED
