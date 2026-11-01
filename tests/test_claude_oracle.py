"""Compare results with Claude Code's own listings on the same fixture.

Runs only when AGT_VIEW_CLAUDE points at a real Claude Code binary, and never touches
the developer's configuration: HOME is temporary. `claude mcp list` starts servers to
check their health, so fixture servers only run /bin/true.
"""

import json
import os
import subprocess

import pytest
from conftest import write_skill

from agt_view.adapters.base import Context
from agt_view.adapters.claude_code import ClaudeCode
from agt_view.model import Kind, Scope, Status

CLAUDE = os.environ.get("AGT_VIEW_CLAUDE")
STDIO = {"command": "/bin/true"}

pytestmark = pytest.mark.skipif(not CLAUDE, reason="AGT_VIEW_CLAUDE is not set")


def claude(home, cwd, *args):
    return subprocess.run(
        [CLAUDE, *args],
        check=False,
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
            {
                "mcpServers": {
                    name: STDIO for name in ("p-none", "p-entry", "p-shared", "p-local", "p-user")
                }
            }
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


def test_mcp_precedence_matches_claude_listing(tmp_path):
    home, project = tmp_path / "home", tmp_path / "project"
    (home / ".claude").mkdir(parents=True)
    project.mkdir()
    (home / ".claude.json").write_text(
        json.dumps(
            {
                "mcpServers": {name: STDIO for name in ("all", "approved", "pending", "user-only")},
                "projects": {
                    str(project): {
                        "hasTrustDialogAccepted": True,
                        "mcpServers": {"all": {"command": "/bin/false"}},
                        "enabledMcpjsonServers": ["approved"],
                    }
                },
            }
        )
    )
    (project / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    name: {"command": "/bin/echo"} for name in ("all", "approved", "pending")
                }
            }
        )
    )
    theirs = {}
    for line in claude(home, project, "mcp", "list").splitlines():
        if " - " in line and ": " in line:
            name, rest = line.split(": ", 1)
            theirs[name] = rest.split(" - ")[0].strip()
    ours = {
        item.name: item.details["command"]
        for item in scan(tmp_path, home, project)
        if item.kind is Kind.MCP and item.status is Status.ENABLED
    }
    assert ours == theirs


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
    ours = {item.name: item for item in scan(tmp_path, home, project) if item.kind is Kind.PLUGIN}
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


def installed_through_a_marketplace(home, project, build, manifest):
    marketplace = home / "marketplace"
    (marketplace / ".claude-plugin").mkdir(parents=True)
    (marketplace / ".claude-plugin" / "marketplace.json").write_text(
        json.dumps(
            {
                "name": "m",
                "owner": {"name": "o"},
                "plugins": [{"name": "p", "source": "./p", "description": "d"}],
            }
        )
    )
    root = marketplace / "p"
    root.mkdir()
    if manifest is not None:
        (root / ".claude-plugin").mkdir()
        (root / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({"name": "p", "version": "1.0.0", **manifest})
        )
    build(root)
    claude(home, project, "plugin", "marketplace", "add", str(marketplace))
    claude(home, project, "plugin", "install", "p@m")


@pytest.mark.parametrize(
    ("manifest", "layout"),
    [
        (None, ["skills/a"]),
        ({}, ["skills/a", "skills/b"]),
        ({}, ["skills/group/nested"]),
        ({}, ["skills/.hidden"]),
        ({}, ["."]),
        ({}, [".", "skills/a"]),
        ({"skills": "./extra"}, ["extra/a", "skills/b"]),
        ({"skills": ["./extra", "./more"]}, ["extra/a", "more/b", "skills/c"]),
        ({"skills": "./one"}, ["one"]),
        ({"skills": "./"}, ["sub", "skills/a"]),
    ],
)
def test_plugin_skills_match_claude_details(tmp_path, manifest, layout):
    home, project = tmp_path / "home", tmp_path / "project"
    home.mkdir()
    project.mkdir()

    def build(root):
        for directory in layout:
            write_skill(root / directory, "description: d")

    installed_through_a_marketplace(home, project, build, manifest)
    details = claude(home, project, "plugin", "details", "p@m")
    line = next(line for line in details.splitlines() if "Skills (" in line)
    theirs = {f"p:{name.strip()}" for name in line.partition(")")[2].split(",") if name.strip()}
    ours = {
        item.name
        for item in scan(tmp_path, home, project)
        if item.kind is Kind.SKILL and item.status is Status.ENABLED
    }
    assert ours == theirs


def test_only_a_disabled_plugin_hides_its_skills(tmp_path):
    home, project = tmp_path / "home", tmp_path / "project"
    home.mkdir()
    project.mkdir()
    installed_through_a_marketplace(
        home, project, lambda root: write_skill(root / "skills" / "a", "description: d"), {}
    )
    assert "p:a" in {item.name for item in scan(tmp_path, home, project)}
    claude(home, project, "plugin", "disable", "p@m")
    enabled = [p["enabled"] for p in json.loads(claude(home, project, "plugin", "list", "--json"))]
    assert enabled == [False]
    assert "p:a" not in {item.name for item in scan(tmp_path, home, project)}


def startup_listing(home, project):
    """Claude Code prints this before it finds that no one is signed in."""
    output = claude(home, project, "-p", "hi", "--output-format", "stream-json", "--verbose")
    for line in output.splitlines():
        message = json.loads(line)
        if message.get("subtype") == "init":
            return message
    raise AssertionError("no startup listing")


@pytest.fixture(scope="module")
def built_in(tmp_path_factory):
    home, project = tmp_path_factory.mktemp("home"), tmp_path_factory.mktemp("project")
    listing = startup_listing(home, project)
    return set(listing["agents"]), set(listing["slash_commands"])


def write_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def agent_file(name, description="d"):
    return f"---\nname: {name}\ndescription: {description}\n---\nBody\n"


def enabled(tmp_path, home, project, kind):
    return {
        item.name
        for item in scan(tmp_path, home, project)
        if item.kind is kind and item.status is Status.ENABLED
    }


def test_agents_match_startup_listing(tmp_path, built_in):
    home, project = tmp_path / "home", tmp_path / "project"
    agents = home / ".claude" / "agents"
    project.mkdir()
    write_text(agents / "file.md", agent_file("from-field"))
    write_text(agents / "folder" / "nested.md", agent_file("nested"))
    write_text(agents / ".hidden.md", agent_file("hidden"))
    write_text(agents / "no-name.md", "---\ndescription: d\n---\nBody\n")
    write_text(agents / "no-description.md", "---\nname: no-description\n---\nBody\n")
    write_text(agents / "colon.md", agent_file("a:b"))
    write_text(agents / "dash.md", agent_file("-dash"))
    write_text(agents / "plain.md", "Body\n")
    write_text(agents / "not-markdown.txt", agent_file("not-markdown"))
    write_text(project / ".claude" / "agents" / "here.md", agent_file("here"))
    theirs = set(startup_listing(home, project)["agents"]) - built_in[0]
    assert enabled(tmp_path, home, project, Kind.SUBAGENT) == theirs
    assert theirs


def test_commands_match_startup_listing(tmp_path, built_in):
    home, project = tmp_path / "home", tmp_path / "project"
    commands = home / ".claude" / "commands"
    project.mkdir()
    write_text(commands / "plain.md", "Body\n")
    write_text(commands / "group" / "deep" / "inner.md", "Body\n")
    write_text(commands / "named.md", "---\nname: other\ndescription: d\n---\nBody\n")
    write_text(commands / ".hidden.md", "Body\n")
    write_text(commands / ".folder" / "inner.md", "Body\n")
    write_text(commands / "text.txt", "Body\n")
    write_text(commands / "off.md", "Body\n")
    write_text(project / ".claude" / "commands" / "group" / "here.md", "Body\n")
    write_text(home / ".claude" / "settings.json", json.dumps({"skillOverrides": {"off": "off"}}))
    theirs = set(startup_listing(home, project)["slash_commands"]) - built_in[1]
    assert enabled(tmp_path, home, project, Kind.COMMAND) == theirs
    assert theirs


@pytest.mark.parametrize(
    ("manifest", "files", "agents", "commands"),
    [
        ({}, ["agents/a.md", "agents/folder/b.md"], {"p:a", "p:folder:b"}, set()),
        ({}, ["commands/a.md", "commands/folder/b.md"], set(), {"p:a", "p:folder:b"}),
        ({"agents": "./other/a.md"}, ["other/a.md", "agents/b.md"], {"p:a"}, set()),
        ({"agents": ["./other"]}, ["other/a.md"], set(), set()),
        ({"commands": "./other"}, ["other/a.md", "commands/b.md"], set(), {"p:a"}),
        ({"commands": "./other/a.md"}, ["other/a.md", "commands/b.md"], set(), {"p:a"}),
        (
            {"commands": ["./other", "./commands"]},
            ["other/a.md", "commands/b.md"],
            set(),
            {"p:a", "p:b"},
        ),
        (
            {"commands": {"about": {"source": "./c/x.md"}, "inline": {"content": "Hi"}}},
            ["c/x.md"],
            set(),
            {"p:about", "p:inline"},
        ),
    ],
)
def test_plugin_definitions_match_startup_listing(
    tmp_path, built_in, manifest, files, agents, commands
):
    home, project = tmp_path / "home", tmp_path / "project"
    home.mkdir()
    project.mkdir()

    def build(root):
        for file in files:
            write_text(root / file, agent_file(file.rpartition("/")[2][:-3]))

    installed_through_a_marketplace(home, project, build, manifest)
    listing = startup_listing(home, project)
    assert set(listing["agents"]) - built_in[0] == agents
    assert set(listing["slash_commands"]) - built_in[1] == commands
    assert enabled(tmp_path, home, project, Kind.SUBAGENT) == agents
    assert enabled(tmp_path, home, project, Kind.COMMAND) == commands


def test_plugin_definitions_follow_the_plugin(tmp_path, built_in):
    home, project = tmp_path / "home", tmp_path / "project"
    home.mkdir()
    project.mkdir()

    def build(root):
        write_text(root / "agents" / "a.md", agent_file("a"))
        write_text(root / "commands" / "c.md", "Body\n")
        write_skill(root / "skills" / "s", "description: d")

    installed_through_a_marketplace(home, project, build, {})
    claude(home, project, "plugin", "disable", "p@m")
    listing = startup_listing(home, project)
    assert set(listing["agents"]) == built_in[0]
    assert set(listing["slash_commands"]) == built_in[1]
    assert {i.name for i in scan(tmp_path, home, project) if i.kind is not Kind.PLUGIN} == set()


def test_a_plugin_with_no_entry_does_not_load(tmp_path, built_in):
    home, project = tmp_path / "home", tmp_path / "project"
    home.mkdir()
    project.mkdir()
    installed_through_a_marketplace(
        home,
        project,
        lambda root: write_skill(root / "skills" / "s", "description: d"),
        {"defaultEnabled": True},
    )
    settings = home / ".claude" / "settings.json"
    settings.write_text(json.dumps({"enabledPlugins": {}}))
    assert set(startup_listing(home, project)["slash_commands"]) == built_in[1]
    plugin = next(i for i in scan(tmp_path, home, project) if i.kind is Kind.PLUGIN)
    assert plugin.status is Status.DISABLED


def marker_hook(home, name):
    return {"type": "command", "command": f"touch {home}/{name}.ran"}


def session_start(*handlers):
    return {"hooks": {"SessionStart": [{"hooks": list(handlers)}]}}


@pytest.mark.parametrize(
    ("user", "project_settings", "local"),
    [
        ({}, {}, {}),
        ({"disableAllHooks": True}, {}, {}),
        ({}, {"disableAllHooks": True}, {}),
        ({"disableAllHooks": True}, {"disableAllHooks": False}, {}),
        ({}, {"disableAllHooks": False}, {"disableAllHooks": True}),
    ],
)
def test_hooks_that_run_are_the_enabled_ones(tmp_path, user, project_settings, local):
    """Each fixture hook only touches a file in the temporary HOME."""
    home, project = tmp_path / "home", tmp_path / "project"
    home.mkdir()
    project.mkdir()
    write_text(
        home / ".claude.json",
        json.dumps({"projects": {str(project): {"hasTrustDialogAccepted": True}}}),
    )
    write_text(
        home / ".claude" / "settings.json",
        json.dumps({**session_start(marker_hook(home, "user")), **user}),
    )
    write_text(
        project / ".claude" / "settings.json",
        json.dumps({**session_start(marker_hook(home, "project")), **project_settings}),
    )
    write_text(
        project / ".claude" / "settings.local.json",
        json.dumps({**session_start(marker_hook(home, "local")), **local}),
    )
    startup_listing(home, project)
    ran = len(list(home.glob("*.ran")))
    ours = [i for i in scan(tmp_path, home, project) if i.kind is Kind.HOOK]
    assert len(ours) == 3
    assert sum(i.status is Status.ENABLED for i in ours) == ran


def test_invalid_hooks_do_not_stop_valid_ones(tmp_path):
    home, project = tmp_path / "home", tmp_path / "project"
    home.mkdir()
    project.mkdir()
    write_text(
        home / ".claude" / "settings.json",
        json.dumps(
            {
                "hooks": {
                    "NoSuchEvent": [{"hooks": [marker_hook(home, "event")]}],
                    "SessionStart": [
                        {
                            "hooks": [
                                {"type": "command"},
                                {"type": "nope", "command": f"touch {home}/type.ran"},
                                marker_hook(home, "good"),
                            ]
                        }
                    ],
                }
            }
        ),
    )
    startup_listing(home, project)
    assert [path.name for path in home.glob("*.ran")] == ["good.ran"]
    statuses = sorted(i.status for i in scan(tmp_path, home, project) if i.kind is Kind.HOOK)
    assert statuses == sorted([Status.ENABLED, Status.UNKNOWN, Status.BROKEN, Status.BROKEN])


@pytest.mark.parametrize("via_key", [False, True])
def test_plugin_hooks_run_when_listed(tmp_path, via_key):
    home, project = tmp_path / "home", tmp_path / "project"
    home.mkdir()
    project.mkdir()

    def build(root):
        write_text(
            root / "hooks" / "hooks.json", json.dumps(session_start(marker_hook(home, "default")))
        )
        write_text(root / "more.json", json.dumps(session_start(marker_hook(home, "key"))))

    installed_through_a_marketplace(
        home, project, build, {"hooks": "./more.json"} if via_key else {}
    )
    startup_listing(home, project)
    ours = [i for i in scan(tmp_path, home, project) if i.kind is Kind.HOOK]
    assert len(list(home.glob("*.ran"))) == len(ours) == (2 if via_key else 1)
    assert all(i.status is Status.ENABLED and i.details["plugin"] == "p@m" for i in ours)
