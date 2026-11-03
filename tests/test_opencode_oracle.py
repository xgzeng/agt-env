"""Compare results with OpenCode's own server API on the same fixture.

Runs only when AGT_ENV_OPENCODE points at a real OpenCode binary, and never touches the
developer's configuration: HOME is temporary, and so is the background service, which
gets its own port and is stopped afterwards.
"""

import base64
import itertools
import json
import os
import socket
import subprocess
import time
import urllib.request

import pytest
from conftest import write_skill

from agt_env.adapters.base import Context
from agt_env.adapters.opencode import OpenCode
from agt_env.model import Kind, Status

OPENCODE = os.environ.get("AGT_ENV_OPENCODE")
PLUGIN = "export default {id: 'x', setup: async () => ({})}\n"
LOCAL = {"type": "local", "command": ["/bin/true"]}

pytestmark = pytest.mark.skipif(not OPENCODE, reason="AGT_ENV_OPENCODE is not set")


def write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content if isinstance(content, str) else json.dumps(content))


@pytest.fixture
def api(tmp_path):
    home, project = tmp_path / "home", tmp_path / "project"
    (project / ".git").mkdir(parents=True)
    home.mkdir()
    env = {"HOME": str(home), "PATH": os.environ["PATH"], "TERM": "dumb"}

    def run(*args):
        return subprocess.run(
            [OPENCODE, *args],
            check=False,
            cwd=project,
            env=env,
            capture_output=True,
            text=True,
            timeout=90,
        )

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    run("service", "set", "port", str(port))

    def query(route):
        run("debug", "config")  # Starts the service with this fixture's config.
        time.sleep(3)
        password = json.loads((home / ".config" / "opencode" / "service.json").read_text())[
            "password"
        ]
        token = base64.b64encode(f"opencode:{password}".encode()).decode()
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}{route}",
            headers={
                "Accept": "application/json",
                "Authorization": f"Basic {token}",
                "x-opencode-directory": str(project),
            },
        )
        for attempt in range(40):
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    return json.load(response)["data"]
            except OSError:
                time.sleep(0.5)
        raise AssertionError(f"no answer from {route}")

    query.home, query.project, query.tmp = home, project, tmp_path
    yield query
    run("service", "stop")


def scan(api, kind):
    context = Context(
        home=api.home, cwd=api.project, env={"PATH": ""}, system_root=api.tmp / "system"
    )
    return [item for item in OpenCode().scan(context).items if item.kind is kind]


def write_skills(api):
    config = api.home / ".config" / "opencode"
    skills = config / "skills"
    for name in ("own", "denied"):
        write_skill(skills / name, f"name: {name}\ndescription: d")
    write_skill(skills / "group" / "deep" / "nested", "name: nested\ndescription: d")
    write_skill(skills / "dir-name", "name: fm-name\ndescription: d")
    write_skill(skills / "no-name", "description: d")
    write_skill(skills / "no-desc", "name: no-desc")
    write_skill(skills / "Bad_Name", "name: Bad_Name\ndescription: d")
    write(skills / "no-fm" / "SKILL.md", "no frontmatter")
    write(skills / "standalone.md", "---\nname: standalone\ndescription: d\n---\n")
    write_skill(config / "skill" / "singular", "name: singular\ndescription: d")
    write_skill(
        api.home / ".claude" / "skills" / "from-claude", "name: from-claude\ndescription: d"
    )
    write_skill(
        api.home / ".claude" / "skills" / "grp" / "deep-claude", "name: deep-claude\ndescription: d"
    )
    write_skill(
        api.home / ".agents" / "skills" / "from-agents", "name: from-agents\ndescription: d"
    )
    write_skill(api.home / ".agents" / "skills" / ".hidden" / "hid", "name: hid\ndescription: d")
    write_skill(api.project / ".opencode" / "skills" / "repo", "name: repo\ndescription: d")
    write_skill(
        api.project / ".claude" / "skills" / "repo-claude", "name: repo-claude\ndescription: d"
    )
    write_skill(
        api.project / ".agents" / "skills" / "repo-agents", "name: repo-agents\ndescription: d"
    )
    # Separate branches, because a skill's directory isn't searched for more skills.
    for depth in (5, 6, 7):
        branch = "/".join(f"b{depth}-{level}" for level in range(depth))
        write_skill(skills / branch, f"name: depth{depth}\ndescription: d")
    write_skill(skills / "outer", "name: outer\ndescription: d")
    write_skill(skills / "outer" / "inner", "name: inner\ndescription: d")
    write_skill(skills / "node_modules" / "pkg" / "in-modules", "name: in-modules\ndescription: d")
    write(skills / "group" / "sub.md", "---\nname: sub\ndescription: d\n---\n")
    write(
        api.home / ".claude" / "skills" / "claude-root.md",
        "---\nname: claude-root\ndescription: d\n---\n",
    )
    write(
        api.home / ".agents" / "skills" / "agents-root.md",
        "---\nname: agents-root\ndescription: d\n---\n",
    )
    write_skill(api.project / "relative" / "rel-skill", "name: rel-skill\ndescription: d")
    write_skill(api.tmp / "absolute" / "abs-skill", "name: abs-skill\ndescription: d")
    write(api.tmp / "absolute" / "abs-root.md", "---\nname: abs-root\ndescription: d\n---\n")
    write_skill(api.tmp / "one-skill", "name: one-skill\ndescription: d")
    write(
        config / "opencode.json",
        {"skills": ["relative", str(api.tmp / "absolute"), str(api.tmp / "one-skill")]},
    )


def test_skills_match(api):
    write_skills(api)
    theirs = {
        (skill["name"], skill["path"])
        for skill in api("/api/skill")
        if not skill["path"].startswith("/builtin/")
    }
    ours = {
        (
            item.name,
            str(
                item.source_path
                if item.source_path.suffix == ".md"
                else item.source_path / "SKILL.md"
            ),
        )
        for item in scan(api, Kind.SKILL)
    }
    assert ours == theirs


def test_skill_precedence_matches(api):
    home, project = api.home, api.project
    sources = {
        "config": api.tmp / "cfg",
        "opencode": project / ".opencode" / "skills",
        "global": home / ".config" / "opencode" / "skills",
        "project-agents": project / ".agents" / "skills",
        "user-agents": home / ".agents" / "skills",
        "project-claude": project / ".claude" / "skills",
        "user-claude": home / ".claude" / "skills",
    }
    write(home / ".config" / "opencode" / "opencode.json", {"skills": [str(sources["config"])]})
    for a, b in itertools.combinations(sources, 2):
        for source in (a, b):
            write_skill(sources[source] / f"{a}-{b}", f"name: {a}-{b}\ndescription: {source}")
    own, global_skills = sources["opencode"], sources["global"]
    # Matched by directory name, not the `name` field.
    write_skill(own / "samedir", "name: name-a\ndescription: d")
    write_skill(global_skills / "samedir", "name: name-b\ndescription: d")
    write_skill(own / "dir-1", "name: same-name\ndescription: d")
    write_skill(global_skills / "dir-2", "name: same-name\ndescription: d")
    # Within one source.
    write_skill(global_skills / "a" / "nest", "name: first\ndescription: d")
    write_skill(global_skills / "b" / "nest", "name: second\ndescription: d")
    write_skill(global_skills / "mix", "name: directory\ndescription: d")
    write(global_skills / "mix.md", "---\nname: file\ndescription: d\n---\n")
    write_skill(global_skills / "sing", "name: plural\ndescription: d")
    write_skill(global_skills.parent / "skill" / "sing", "name: singular\ndescription: d")
    # What doesn't take the name.
    (own / "dangling").symlink_to(home / "gone")
    write_skill(global_skills / "dangling", "name: dangling\ndescription: d")
    # What does.
    write(own / "standalone.md", "---\nname: as-file\ndescription: d\n---\n")
    write_skill(global_skills / "standalone", "name: as-directory\ndescription: d")
    write_skill(own / "no-description", "name: no-description")
    write_skill(global_skills / "no-description", "name: other\ndescription: d")
    write(own / "no-frontmatter" / "SKILL.md", "text\n")
    write_skill(global_skills / "no-frontmatter", "name: another\ndescription: d")

    theirs = {
        (skill["name"], skill["path"])
        for skill in api("/api/skill")
        if not skill["path"].startswith("/builtin/")
    }
    ours = {
        (
            item.name,
            str(
                item.source_path
                if item.source_path.suffix == ".md"
                else item.source_path / "SKILL.md"
            ),
        )
        for item in scan(api, Kind.SKILL)
        if item.status not in (Status.SHADOWED, Status.BROKEN)
    }
    assert ours == theirs


@pytest.mark.parametrize("version", ["v1", "v2"])
def test_servers_match(api, version):
    config = api.home / ".config" / "opencode" / "opencode.json"
    if version == "v1":
        write(
            config,
            {
                "mcp": {
                    "on": LOCAL,
                    "off": {**LOCAL, "enabled": False},
                    "remote": {"type": "remote", "url": "http://127.0.0.1:9/m"},
                    "partial": LOCAL,
                }
            },
        )
        write(
            api.project / "opencode.json", {"mcp": {"partial": {"enabled": False}, "repo": LOCAL}}
        )
    else:
        write(
            config,
            {
                "mcp": {
                    "servers": {
                        "on": LOCAL,
                        "off": {**LOCAL, "disabled": True},
                        "ignored-flag": {**LOCAL, "enabled": False},
                        "remote": {"type": "remote", "url": "http://127.0.0.1:9/m"},
                        "partial": LOCAL,
                    }
                }
            },
        )
        write(
            api.project / "opencode.json",
            {"mcp": {"servers": {"partial": {"disabled": True}, "repo": LOCAL}}},
        )
    write(api.project / ".opencode" / "opencode.json", {"mcp": {"servers": {"dot": LOCAL}}})
    (api.home / ".config" / "opencode" / "opencode.jsonc").write_text(
        '{ // comment\n "mcp": {"servers": {"from-jsonc": {"type": "local", "command": ["/bin/true"],},},},\n}'
    )

    theirs = {
        server["name"]: server["status"]["status"] != "disabled" for server in api("/api/mcp")
    }
    ours = {item.name: item.status is Status.ENABLED for item in scan(api, Kind.MCP)}
    assert ours == theirs


def test_local_plugins_match(api):
    config = api.home / ".config" / "opencode"
    write(config / "plugins" / "local.ts", PLUGIN)
    write(config / "plugins" / "other.js", PLUGIN)
    write(config / "plugins" / "not-loaded.mjs", PLUGIN)
    write(config / "plugins" / "dir" / "index.ts", PLUGIN)
    write(config / "plugin" / "singular.js", PLUGIN)
    write(api.project / ".opencode" / "plugins" / "repo.js", PLUGIN)
    theirs = {
        plugin["source"]["path"]
        for plugin in api("/api/plugin")
        if plugin.get("source", {}).get("type") == "local"
    }
    ours = {str(item.source_path) for item in scan(api, Kind.PLUGIN)}
    assert ours == theirs


def plugin(plugin_id):
    return f"export default {{ id: '{plugin_id}', setup: async () => ({{}}) }}\n"


def their_plugins(api):
    return {
        entry["source"]["path"]: entry["state"]["status"]
        for entry in api("/api/plugin")
        if entry.get("source", {}).get("type") == "local"
    }


def test_configured_plugins_match(api):
    config = api.home / ".config" / "opencode"
    for name in ("listed", "uri", "obj"):
        write(config / name / "index.ts", plugin(f"id-{name}"))
    write(config / "single.ts", plugin("id-single"))
    write(
        config / "opencode.json",
        {
            "plugins": [
                "./listed",
                f"file://{config}/uri",
                {"package": "./obj"},
                "./single.ts",
                "./gone",
            ]
        },
    )
    theirs = their_plugins(api)
    ours = {str(item.source_path): item.status for item in scan(api, Kind.PLUGIN)}
    active = {path for path, state in theirs.items() if state == "active"}
    assert {path for path, status in ours.items() if status is Status.ENABLED} == active
    assert ours[str(config / "gone")] is Status.BROKEN and theirs[str(config / "gone")] == "failed"
    # A single file is skipped, so OpenCode doesn't report it at all.
    assert (
        ours[str(config / "single.ts")] is Status.BROKEN and str(config / "single.ts") not in theirs
    )


@pytest.mark.parametrize(
    "removals",
    [
        ["-id-two", "-local", "-id-*"],
        ["-team.*"],
        ["-id-obj", {"package": "./obj"}],
        ["-*", "./kept"],
    ],
)
def test_plugin_removals_match(api, removals):
    config = api.home / ".config" / "opencode"
    write(config / "plugins" / "local.ts", plugin("id-local"))
    write(config / "plugins" / "two.js", plugin("id-two"))
    write(config / "plugins" / "group.ts", plugin("team.one"))
    write(config / "obj" / "index.ts", plugin("id-obj"))
    write(config / "kept" / "index.ts", plugin("id-kept"))
    write(config / "opencode.json", {"plugins": removals})
    theirs = {path for path, state in their_plugins(api).items() if state == "active"}
    ours = {
        str(item.source_path) for item in scan(api, Kind.PLUGIN) if item.status is Status.ENABLED
    }
    assert ours == theirs


def test_removing_everything_removes_skills_and_servers(api):
    config = api.home / ".config" / "opencode"
    write_skill(config / "skills" / "one", "name: one\ndescription: d")
    write(config / "opencode.json", {"plugins": ["-*"], "mcp": {"servers": {"s": LOCAL}}})
    assert not [s for s in api("/api/skill") if not s["path"].startswith("/builtin/")]
    assert not [s for s in api("/api/mcp") if s["status"]["status"] != "disabled"]
    assert {i.status for i in scan(api, Kind.SKILL)} == {Status.DISABLED}
    assert {i.status for i in scan(api, Kind.MCP)} == {Status.DISABLED}


@pytest.mark.parametrize(
    ("removal", "skills_gone", "servers_gone"),
    [
        ("-opencode.config.skill", True, False),
        ("-opencode.skill", False, False),
        ("-opencode.config.mcp", False, True),
    ],
)
def test_removing_one_built_in(api, removal, skills_gone, servers_gone):
    config = api.home / ".config" / "opencode"
    write_skill(config / "skills" / "one", "name: one\ndescription: d")
    write(config / "opencode.json", {"plugins": [removal], "mcp": {"servers": {"s": LOCAL}}})
    theirs_skills = [s for s in api("/api/skill") if not s["path"].startswith("/builtin/")]
    theirs_servers = [s for s in api("/api/mcp") if s["status"]["status"] != "disabled"]
    assert bool(theirs_skills) is not skills_gone
    assert bool(theirs_servers) is not servers_gone
    ours_skill = scan(api, Kind.SKILL)[0].status
    ours_server = scan(api, Kind.MCP)[0].status
    assert (ours_skill is Status.DISABLED) is skills_gone
    assert (ours_server is Status.DISABLED) is servers_gone


def test_compatibility_plugin_controls_compatibility_skills(api):
    write_skill(api.home / ".claude" / "skills" / "h-claude", "name: h-claude\ndescription: d")
    write_skill(api.home / ".agents" / "skills" / "h-agents", "name: h-agents\ndescription: d")
    write_skill(api.project / ".claude" / "skills" / "p-claude", "name: p-claude\ndescription: d")
    write_skill(api.project / ".agents" / "skills" / "p-agents", "name: p-agents\ndescription: d")
    config = api.home / ".config" / "opencode"
    write_skill(config / "skills" / "own", "name: own\ndescription: d")
    write_skill(api.project / ".opencode" / "skills" / "p-own", "name: p-own\ndescription: d")
    write(config / "opencode.json", {"plugins": ["-opencode.config.compatibility"]})
    theirs = {s["id"] for s in api("/api/skill") if not s["path"].startswith("/builtin/")}
    ours = {i.name for i in scan(api, Kind.SKILL) if i.status is Status.ENABLED}
    assert theirs == {"own", "p-own"}
    assert ours == theirs


def test_a_plugin_folder_contributes_no_skills(api):
    plugin = api.home / ".config" / "opencode" / "plugins" / "with-skill"
    write(plugin / "index.ts", PLUGIN)
    write_skill(plugin / "skills" / "inside", "name: inside\ndescription: d")
    write_skill(api.home / ".config" / "opencode" / "skills" / "control", "description: d")
    theirs = {skill["id"] for skill in api("/api/skill")}
    assert "control" in theirs and "inside" not in theirs
    assert "inside" not in {item.name for item in scan(api, Kind.SKILL)}


def definition_file(description="d", extra=""):
    return f"---\ndescription: {description}\n{extra}---\nBody\n"


def write_definitions(api, plural, singular, *, frontmatter_off=False):
    config, project = api.home / ".config" / "opencode", api.project / ".opencode"
    write(config / plural / "global.md", definition_file())
    write(config / plural / "team" / "nested.md", definition_file())
    write(config / singular / "singular.md", definition_file())
    write(config / plural / ".hidden.md", definition_file())
    write(config / plural / "plain.md", "Body\n")
    write(config / plural / "text.txt", definition_file())
    write(config / plural / "both.md", definition_file("global"))
    write(project / plural / "both.md", definition_file("project"))
    write(project / plural / "here.md", definition_file())
    write(project / singular / "here-singular.md", definition_file())
    write(api.home / ".claude" / plural / "claude-global.md", definition_file())
    write(api.project / ".claude" / plural / "claude-here.md", definition_file())


def test_agents_match(api):
    write_definitions(api, "agents", "agent")
    config = api.home / ".config" / "opencode"
    write(config / "agents" / "off.md", definition_file(extra="disabled: true\n"))
    write(config / "agents" / "off-v1.md", definition_file(extra="disable: true\n"))
    write(config / "agents" / "shy.md", definition_file(extra="hidden: true\n"))
    write(config / "agents" / "named.md", definition_file(extra="name: other\n"))
    write(
        config / "opencode.json",
        {
            "agents": {
                "from-v2": {"description": "d"},
                "from-v2-off": {"description": "d", "disabled": True},
                "plan": {"disabled": True},
                "global": {"description": "config"},
            },
            "agent": {"from-v1": {"description": "d"}, "from-v1-off": {"disable": True}},
        },
    )
    write(config / "agents" / "over-global.md", definition_file("global file"))
    write(
        api.project / "opencode.json",
        {
            "agents": {
                "both": {"description": "root config"},
                "over-global": {"description": "root config"},
            }
        },
    )
    theirs = {agent["id"]: agent.get("description") or "" for agent in api("/api/agent")}
    assert "plan" not in theirs
    ours = {
        item.name: item.description
        for item in scan(api, Kind.SUBAGENT)
        if item.status is Status.ENABLED
    }
    assert {name: theirs[name] for name in ours} == ours
    assert theirs["over-global"] == "root config" and theirs["both"] == "project"
    assert theirs["global"] == "d"
    built_in = {"build", "general", "explore", "compaction", "title", "summary"}
    assert set(ours) == set(theirs) - built_in


def test_commands_match(api):
    write_definitions(api, "commands", "command")
    config = api.home / ".config" / "opencode"
    write(config / "commands" / "review.md", definition_file("mine"))
    write(config / "commands" / "empty.md", "")
    write(
        config / "opencode.json",
        {
            "commands": {"from-v2": {"template": "t"}, "no-template": {"description": "d"}},
            "command": {"from-v1": {"template": "t"}},
        },
    )
    write(config / "commands" / "over-global.md", definition_file("global file"))
    write(
        api.project / "opencode.json",
        {
            "commands": {
                "both": {"template": "t", "description": "root config"},
                "over-global": {"template": "t", "description": "root config"},
            }
        },
    )
    theirs = {command["name"]: command.get("description") or "" for command in api("/api/command")}
    ours = {
        item.name: item.description
        for item in scan(api, Kind.COMMAND)
        if item.status is Status.ENABLED
    }
    assert set(ours) == set(theirs) - {"init", "goal"}
    assert theirs["review"] == "mine" and ours["review"] == "mine"
    assert theirs["both"] == ours["both"] == "project"
    assert theirs["over-global"] == ours["over-global"] == "root config"


# Rules from the review in docs/reviews/opencode.md, each compared with the real agent.


def scan_with(api, env=None):
    context = Context(
        home=api.home,
        cwd=api.project,
        env={"PATH": "", **(env or {})},
        system_root=api.tmp / "system",
    )
    return OpenCode().scan(context).items


def running(items, kind):
    return sorted(
        item.name for item in items if item.kind is kind and item.status is Status.ENABLED
    )


def ids(data):
    return sorted(entry.get("id") or entry.get("name") for entry in data)


FIXTURE_SKILLS = {
    "own",
    "proj",
    "agents-user",
    "claude-user",
    "agents-project",
    "claude-project",
    "own-project",
}


def on_disk(skills):
    """Leave out OpenCode's built-in skills, which aren't listed here."""
    return sorted(skill["id"] for skill in skills if skill["id"] in FIXTURE_SKILLS)


def their_skills(api):
    return on_disk(api("/api/skill"))


def their_servers(api):
    return sorted(server["name"] for server in api("/api/mcp"))


def enabled_or_broken_servers(items):
    return sorted(
        item.name for item in items if item.kind is Kind.MCP and item.status is Status.ENABLED
    )


def test_a_moved_config_directory_replaces_the_global_one(api):
    moved = api.tmp / "moved"
    config = api.home / ".config" / "opencode"
    write_skill(config / "skills" / "global-skill")
    write_skill(moved / "skills" / "moved-skill")
    write(config / "opencode.json", {"mcp": {"servers": {"global-server": LOCAL}}})
    write(moved / "opencode.json", {"mcp": {"servers": {"moved-server": LOCAL}}})
    ours = scan_with(api, {"OPENCODE_CONFIG_DIR": str(moved)})
    assert running(ours, Kind.SKILL) == ["moved-skill"]
    assert running(ours, Kind.MCP) == ["moved-server"]


def test_mode_and_plural_directories_match(api):
    config = api.home / ".config" / "opencode"
    write(config / "commands" / "d.md", "---\ndescription: plural\n---\nt")
    write(config / "command" / "d.md", "---\ndescription: singular\n---\nt")
    write(config / "agents" / "a.md", "---\ndescription: plural\n---\nt")
    write(config / "agent" / "a.md", "---\ndescription: singular\n---\nt")
    write(config / "modes" / "m1.md", "---\ndescription: mode\n---\nt")
    write(config / "mode" / "m2.md", "---\ndescription: mode\n---\nt")
    write(config / "modes" / "sub" / "m3.md", "---\ndescription: mode\n---\nt")
    write(config / "agents" / "m1.md", "---\ndescription: agent\n---\nt")
    write(config / "commands" / "bare.md", "git status\n")
    theirs = {agent["id"]: agent.get("description") for agent in api("/api/agent")}
    commands = {command["name"]: command.get("description") for command in api("/api/command")}
    ours = scan_with(api)
    agents = {item.name: item.description for item in ours if item.kind is Kind.SUBAGENT}
    for name, description in agents.items():
        assert theirs[name] == description
    assert "sub/m3" not in theirs and "sub/m3" not in agents
    ours_commands = {item.name: item.description for item in ours if item.kind is Kind.COMMAND}
    assert ours_commands["d"] == commands["d"] == "plural"
    assert not commands["bare"] and not ours_commands["bare"]


@pytest.mark.parametrize(
    "mcp",
    [
        {"timeout": {"catalog": 5000}},
        {"timeout": 5000, "one": LOCAL},
        {"legacy": {**LOCAL, "enabled": False}, "servers": {"native": LOCAL}},
        {"both": {"type": "remote", "url": "https://x.test"}, "servers": {"both": LOCAL}},
        {"e": {"enabled": True}, "f": {"enabled": False}},
    ],
)
def test_server_shapes_match(api, mcp):
    write(api.home / ".config" / "opencode" / "opencode.json", {"mcp": mcp})
    theirs = their_servers(api)
    assert sorted(item.name for item in scan_with(api) if item.kind is Kind.MCP) == theirs


@pytest.mark.parametrize(
    ("low", "high"),
    [
        ({"servers": {"s": LOCAL}}, {"servers": {"s": {"type": "nope"}}}),
        ({"s": LOCAL}, {"s": {"type": "local"}}),
    ],
)
def test_an_invalid_higher_entry_leaves_the_lower_one(api, low, high):
    write(api.home / ".config" / "opencode" / "opencode.json", {"mcp": low})
    write(api.project / "opencode.json", {"mcp": high})
    assert their_servers(api) == ["s"]
    assert running(scan_with(api), Kind.MCP) == ["s"]


@pytest.mark.parametrize(
    "plugins",
    [
        ["-opencode.config.skill"],
        ["-opencode.config.compatibility"],
        ["-opencode.config.agent"],
        ["-opencode.config.command"],
        ["-*"],
        ["-*", "opencode.config.skill"],
        ["-*", "opencode.config.*"],
        ["-opencode.config.skill", "opencode.config.skill"],
        ["-lp.id", "lp.id"],
    ],
)
def test_plugin_operations_apply_in_order(api, plugins):
    config = api.home / ".config" / "opencode"
    write_skill(config / "skills" / "own")
    write_skill(api.project / ".opencode" / "skills" / "proj")
    write_skill(api.home / ".agents" / "skills" / "agents-user")
    write_skill(api.home / ".claude" / "skills" / "claude-user")
    write_skill(api.project / ".agents" / "skills" / "agents-project")
    write(config / "commands" / "cm.md", "---\ndescription: d\n---\nt")
    write(config / "agents" / "am.md", "---\ndescription: d\n---\nt")
    write(config / "plugins" / "lp.ts", "export default {id: 'lp.id', setup: async () => ({})}\n")
    write(
        config / "opencode.json",
        {
            "plugins": plugins,
            "commands": {"cfgc": {"template": "t"}},
            "mcp": {"servers": {"m": LOCAL}},
        },
    )
    skills = their_skills(api)
    servers = their_servers(api)
    commands = {command["name"] for command in api("/api/command")}
    agents = {agent["id"] for agent in api("/api/agent")}
    active = {
        plugin.get("id")
        for plugin in api("/api/plugin")
        if (plugin.get("state") or {}).get("status") == "active"
    }
    ours = scan_with(api)
    assert running(ours, Kind.SKILL) == skills
    assert running(ours, Kind.MCP) == servers
    assert set(running(ours, Kind.COMMAND)) == commands & {"cm", "cfgc"}
    assert set(running(ours, Kind.SUBAGENT)) == agents & {"am"}
    assert ("lp" in running(ours, Kind.PLUGIN)) is ("lp.id" in active)


@pytest.mark.parametrize(
    "env",
    [
        {"OPENCODE_DISABLE_PROJECT_CONFIG": "0"},
        {"OPENCODE_DISABLE_PROJECT_CONFIG": "1"},
        {"OPENCODE_DISABLE_PROJECT_CONFIG": "TRUE"},
        {"OPENCODE_CONFIG_PROJECT_DISABLE": "1"},
        {"OPENCODE_CONFIG_PROJECT_DISABLE": "0", "OPENCODE_DISABLE_PROJECT_CONFIG": "1"},
        {"OPENCODE_DISABLE_CLAUDE_CODE": "false"},
        {"OPENCODE_DISABLE_CLAUDE_CODE": "1"},
        {"OPENCODE_DISABLE_CLAUDE_CODE_SKILLS": "true"},
    ],
)
def test_environment_flags_match(tmp_path, env):
    """Runs its own service, because the fixture's doesn't get these variables."""
    home, project = tmp_path / "home", tmp_path / "project"
    (project / ".git").mkdir(parents=True)
    write_skill(home / ".claude" / "skills" / "claude-user")
    write_skill(project / ".claude" / "skills" / "claude-project")
    write_skill(project / ".opencode" / "skills" / "own-project")
    write_skill(home / ".agents" / "skills" / "agents-user")
    write(project / "opencode.json", {"mcp": {"servers": {"pm": LOCAL}}})
    full_env = {"HOME": str(home), "PATH": os.environ["PATH"], "TERM": "dumb", **env}

    def run(*args):
        return subprocess.run(
            [OPENCODE, *args],
            check=False,
            cwd=project,
            env=full_env,
            capture_output=True,
            text=True,
            timeout=90,
        )

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    run("service", "set", "port", str(port))
    try:
        run("debug", "config")
        time.sleep(3)
        password = json.loads((home / ".config" / "opencode" / "service.json").read_text())[
            "password"
        ]
        token = base64.b64encode(f"opencode:{password}".encode()).decode()

        def query(route):
            request = urllib.request.Request(
                f"http://127.0.0.1:{port}{route}",
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Basic {token}",
                    "x-opencode-directory": str(project),
                },
            )
            for _ in range(40):
                try:
                    with urllib.request.urlopen(request, timeout=30) as response:
                        return json.load(response)["data"]
                except OSError:
                    time.sleep(0.5)
            raise AssertionError(f"no answer from {route}")

        skills = on_disk(query("/api/skill"))
        servers = sorted(server["name"] for server in query("/api/mcp"))
    finally:
        run("service", "stop")
    context = Context(home=home, cwd=project, env={"PATH": "", **env}, system_root=tmp_path / "sys")
    items = OpenCode().scan(context).items
    assert running(items, Kind.SKILL) == skills
    assert running(items, Kind.MCP) == servers


def test_gui_plugins_and_a_tilde_match(api):
    config = api.home / ".config" / "opencode"
    write(config / "plugins" / "gui-cjs" / "gui-experimental.cjs", "module.exports = {}\n")
    write(config / "plugins" / "gui-js" / "gui-experimental.js", "module.exports = {}\n")
    write(config / "plugins" / "gui-ts" / "gui-experimental.ts", "export default {}\n")
    write(config / "plugins" / "empty" / "x.txt", "x")
    write(api.home / "x" / "index.ts", PLUGIN)
    write(config / "opencode.json", {"plugins": ["~/x"]})
    theirs = {
        plugin.get("id"): (plugin.get("state") or {}).get("status")
        for plugin in api("/api/plugin")
        if not str(plugin.get("id")).startswith("opencode.")
    }
    assert theirs.get("gui-cjs") == theirs.get("gui-js") == "active"
    assert "gui-ts" not in theirs and "empty" not in theirs
    ours = {item.name: item for item in scan_with(api) if item.kind is Kind.PLUGIN}
    assert ours["gui-cjs"].status is ours["gui-js"].status is Status.ENABLED
    assert "gui-ts" not in ours and "empty" not in ours
    assert ours["~/x"].native_kind == "npm plugin"
