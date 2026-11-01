"""Compare results with OpenCode's own server API on the same fixture.

Runs only when AGT_VIEW_OPENCODE points at a real OpenCode binary, and never touches the
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

from agt_view.adapters.base import Context
from agt_view.adapters.opencode import OpenCode
from agt_view.model import Kind, Status

OPENCODE = os.environ.get("AGT_VIEW_OPENCODE")
PLUGIN = "export default {id: 'x', setup: async () => ({})}\n"
LOCAL = {"type": "local", "command": ["/bin/true"]}

pytestmark = pytest.mark.skipif(not OPENCODE, reason="AGT_VIEW_OPENCODE is not set")


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
    deep = skills
    for level in range(1, 8):
        deep = deep / f"l{level}"
        write_skill(deep, f"name: depth{level}\ndescription: d") if level >= 5 else deep.mkdir()
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
