"""Compare results with Claude Code's own listings on the same fixture.

Runs only when AGT_ENV_CLAUDE points at a real Claude Code binary, and never touches
the developer's configuration: HOME is temporary. `claude mcp list` starts servers to
check their health, so fixture servers only run /bin/true.

Tests of managed settings give Claude Code a private /etc in a mount namespace, so the real
one is never written.
"""

import http.server
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from conftest import write_skill

from agt_env.adapters.base import Context
from agt_env.adapters.claude_code import ClaudeCode
from agt_env.model import Kind, Scope, Status

CLAUDE = os.environ.get("AGT_ENV_CLAUDE")
STDIO = {"command": "/bin/true"}

pytestmark = pytest.mark.skipif(not CLAUDE, reason="AGT_ENV_CLAUDE is not set")


def _private_etc(root, managed, command):
    """Wrap a command so it sees the managed directory at /etc/claude-code."""
    upper, work = root / "etc-upper", root / "etc-work"
    # A previous overlay's work directory can't be removed from outside its namespace.
    number = len(list(root.glob("etc-upper*")))
    upper, work = upper.with_name(f"etc-upper{number}"), work.with_name(f"etc-work{number}")
    upper.mkdir()
    work.mkdir()
    return [
        "unshare",
        "-Urm",
        "sh",
        "-c",
        (
            f"mount -t overlay overlay -o lowerdir=/etc,upperdir={upper},workdir={work},userxattr"
            f" /etc && mkdir -p /etc/claude-code && cp -a {shlex.quote(str(managed))}/."
            f" /etc/claude-code/ && exec {shlex.join(command)}"
        ),
    ]


def managed_dir(tmp_path):
    return tmp_path / "system" / "etc" / "claude-code"


def claude(home, cwd, *args, env=None):
    command = [CLAUDE, *args]
    managed = managed_dir(home.parent)
    if managed.exists():
        command = _private_etc(home.parent, managed, command)
    return subprocess.run(
        command,
        check=False,
        cwd=cwd,
        env={
            "PATH": os.environ["PATH"],
            "HOME": str(home),
            "DISABLE_AUTOUPDATER": "1",
            **(env or {}),
        },
        capture_output=True,
        text=True,
        timeout=60,
    ).stdout


def scan(tmp_path, home, project, env=None):
    context = Context(
        home=home, cwd=project, env={"PATH": "", **(env or {})}, system_root=tmp_path / "system"
    )
    return ClaudeCode().scan(context).items


def their_mcp_statuses(output):
    """Parse `name: target - <status>` lines. Connection results both mean enabled."""
    statuses = {}
    for line in output.splitlines():
        if " - " not in line or ": " not in line:
            continue
        name, state = line.split(": ", 1)[0], line.rsplit(" - ", 1)[1]
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
def test_plugin_skills_match_claude_details(tmp_path, built_in, manifest, layout):
    home, project = tmp_path / "home", tmp_path / "project"
    home.mkdir()
    project.mkdir()

    def build(root):
        for directory in layout:
            write_skill(root / directory, "description: d")

    installed_through_a_marketplace(home, project, build, manifest)
    details = claude(home, project, "plugin", "details", "p@m")
    line = next(line for line in details.splitlines() if "Skills (" in line)
    # This lists folders as they are. The startup listing shows the names they load under.
    theirs = {
        "p:" + re.sub(r"[^a-zA-Z0-9_-]", "-", name.strip())
        for name in line.partition(")")[2].split(",")
        if name.strip()
    }
    assert theirs == set(startup_listing(home, project)["skills"]) - built_in[2]
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


def startup_listing(home, project, env=None):
    """Claude Code prints this before it finds that no one is signed in."""
    output = claude(
        home, project, "-p", "hi", "--output-format", "stream-json", "--verbose", env=env
    )
    for line in output.splitlines():
        message = json.loads(line)
        if message.get("subtype") == "init":
            return message
    raise AssertionError("no startup listing")


@pytest.fixture(scope="module")
def built_in(tmp_path_factory):
    home, project = tmp_path_factory.mktemp("home"), tmp_path_factory.mktemp("project")
    listing = startup_listing(home, project)
    return set(listing["agents"]), set(listing["slash_commands"]), set(listing["skills"])


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


@pytest.fixture
def machine_dirs(tmp_path):
    home, project = tmp_path / "home", tmp_path / "project"
    home.mkdir()
    project.mkdir()
    return tmp_path, home, project


@pytest.fixture
def private_etc():
    """Managed settings can only be tested where a private /etc can be mounted."""
    if not sys.platform.startswith("linux") or not shutil.which("unshare"):
        pytest.skip("needs a Linux mount namespace")
    probe = subprocess.run(["unshare", "-Urm", "true"], check=False, capture_output=True)
    if probe.returncode:
        pytest.skip("user namespaces aren't available")


def compare_skills(tmp_path, home, project, built_in):
    """Skills it lists as loaded are the ones we show as enabled with nothing holding them back."""
    listing = startup_listing(home, project)
    theirs = set(listing["skills"]) - built_in[2]
    ours = {
        item.name
        for item in scan(tmp_path, home, project)
        if item.kind is Kind.SKILL and item.status is Status.ENABLED and not item.findings
    }
    assert ours == theirs
    return theirs


def test_skills_match_startup_listing(machine_dirs, built_in):
    tmp_path, home, project = machine_dirs
    skills = home / ".claude" / "skills"
    write_skill(skills / ".hidden")
    write_skill(project / ".claude" / "skills" / ".here")
    write_skill(skills / "dir-one", "name: fm-one\ndescription: d")
    write_skill(skills / "dir-two", "name: fm-two\ndescription: d")
    write_skill(skills / "angle", 'name: "a<b>c"\ndescription: d')
    write_skill(skills / "bad<dir>", "name: clean\ndescription: d")
    write_skill(skills / "ok-dir", "name: anthropic-skills\ndescription: d")
    write_skill(skills / "anthropic-skills", "name: fine\ndescription: d")
    write_skill(skills / "Anthropic-Skills", "description: d")
    write_skill(skills / "both", "disable-model-invocation: true\nuser-invocable: false")
    write_skill(skills / "model-off", "disable-model-invocation: true\ndescription: d")
    write_skill(skills / "conditional", "paths: '**/*.py'\ndescription: d")
    write_skill(skills / "listed-paths", "paths:\n  - src/**\n  - '*.md'\ndescription: d")
    write_skill(skills / "every-path", "paths: '**'\ndescription: d")
    write_text(skills / "lower" / "skill.md", "---\ndescription: d\n---\n")
    write_text(
        home / ".claude" / "settings.json",
        json.dumps({"skillOverrides": {"fm-one": "off", "dir-two": "off"}}),
    )
    theirs = compare_skills(tmp_path, home, project, built_in)
    assert {".hidden", "dir-one", "angle", "model-off", "every-path"} <= theirs


def test_a_skill_loses_by_folder_to_a_higher_scope(machine_dirs, built_in):
    _, home, project = machine_dirs
    # What's hidden from the menu tells which of the two loaded.
    write_skill(home / ".claude" / "skills" / "x", "user-invocable: false\ndescription: d")
    write_skill(project / ".claude" / "skills" / "x")
    write_skill(home / ".claude" / "skills" / "a", "name: same\ndescription: d")
    write_skill(project / ".claude" / "skills" / "b", "name: same\ndescription: d")
    assert set(startup_listing(home, project)["skills"]) - built_in[2] == {"a", "b"}
    items = [i for i in scan(machine_dirs[0], home, project) if i.name == "x"]
    assert {i.scope: i.status for i in items} == {
        Scope.USER: Status.ENABLED,
        Scope.PROJECT: Status.SHADOWED,
    }


def test_shared_folder_skills_and_other_agents_directories(machine_dirs, built_in):
    tmp_path, home, project = machine_dirs
    write_skill(home / ".agents" / "skills" / "a")
    write_skill(project / ".agents" / "skills" / "b")
    assert compare_skills(tmp_path, home, project, built_in) == set()


def test_commands_with_skill_files_match_startup_listing(machine_dirs, built_in):
    tmp_path, home, project = machine_dirs
    commands = home / ".claude" / "commands"
    write_text(commands / "review" / "SKILL.md", "Body\n")
    write_text(commands / "review" / "NOTES.md", "Body\n")
    write_text(commands / "group" / "inner" / "skill.md", "Body\n")
    write_text(commands / "group" / "inner" / "deep" / "other.md", "Body\n")
    write_text(commands / "SKILL.md", "Body\n")
    write_text(
        commands / "both.md",
        "---\ndisable-model-invocation: true\nuser-invocable: false\n---\nBody\n",
    )
    theirs = set(startup_listing(home, project)["slash_commands"]) - built_in[1]
    assert enabled(tmp_path, home, project, Kind.COMMAND) == theirs
    assert "review" in theirs


def test_command_precedence_matches_startup_listing(machine_dirs, built_in):
    tmp_path, home, project = machine_dirs
    hidden = "---\nuser-invocable: false\n---\nBody\n"
    # User wins, so x is hidden and y isn't.
    write_text(home / ".claude" / "commands" / "x.md", hidden)
    write_text(project / ".claude" / "commands" / "x.md", "Body\n")
    write_text(project / ".claude" / "commands" / "y.md", hidden)
    write_text(home / ".claude" / "commands" / "y.md", "Body\n")
    # A hidden skill wins over a command in either scope, so neither is listed.
    write_skill(project / ".claude" / "skills" / "up", "user-invocable: false\ndescription: d")
    write_text(home / ".claude" / "commands" / "up.md", "Body\n")
    write_skill(home / ".claude" / "skills" / "down", "user-invocable: false\ndescription: d")
    write_text(project / ".claude" / "commands" / "down.md", "Body\n")
    assert set(startup_listing(home, project)["slash_commands"]) - built_in[1] == {"y"}
    ours = {
        (item.name, item.scope): item.status
        for item in scan(tmp_path, home, project)
        if item.kind is Kind.COMMAND
    }
    assert ours[("x", Scope.USER)] is Status.ENABLED
    assert ours[("x", Scope.PROJECT)] is Status.SHADOWED
    assert ours[("y", Scope.USER)] is Status.ENABLED
    assert ours[("y", Scope.PROJECT)] is Status.SHADOWED
    assert ours[("up", Scope.USER)] is Status.SHADOWED
    assert ours[("down", Scope.PROJECT)] is Status.SHADOWED


def test_running_in_the_home_directory_matches(machine_dirs, built_in):
    tmp_path, home, _ = machine_dirs
    write_text(home / ".claude" / "commands" / "c.md", "Body\n")
    write_text(home / ".claude" / "agents" / "a.md", agent_file("a"))
    write_text(
        home / ".claude" / "settings.json",
        json.dumps({"hooks": {"SessionStart": [counting_hook(home)]}}),
    )
    listing = startup_listing(home, home)
    assert (home / "ran").read_text().count("x") == 1
    items = scan(tmp_path, home, home)
    assert [i.name for i in items if i.kind is Kind.COMMAND] == ["c"]
    assert [i.name for i in items if i.kind is Kind.SUBAGENT] == ["a"]
    assert sum(i.kind is Kind.HOOK for i in items) == 1
    assert set(listing["slash_commands"]) - built_in[1] == {"c"}


def counting_hook(home):
    return {"hooks": [{"type": "command", "command": f"echo x >> {home}/ran"}]}


def test_config_variable_set_to_the_default_moves_the_state_file(machine_dirs):
    tmp_path, home, project = machine_dirs
    env = {"CLAUDE_CONFIG_DIR": str(home / ".claude")}
    write_text(home / ".claude.json", json.dumps({"mcpServers": {"in-home": STDIO}}))
    write_text(home / ".claude" / ".claude.json", json.dumps({"mcpServers": {"in-config": STDIO}}))
    theirs = their_mcp_statuses(claude(home, project, "mcp", "list", env=env))
    ours = {i.name for i in scan(tmp_path, home, project, env) if i.kind is Kind.MCP}
    assert set(theirs) == ours == {"in-config"}


def test_config_variable_moves_personal_skills(machine_dirs, built_in):
    tmp_path, home, project = machine_dirs
    env = {"CLAUDE_CONFIG_DIR": str(tmp_path / "moved")}
    write_skill(tmp_path / "moved" / "skills" / "there")
    write_skill(home / ".claude" / "skills" / "here")
    assert set(startup_listing(home, project, env)["skills"]) - built_in[2] == {"there"}
    names = {i.name for i in scan(tmp_path, home, project, env) if i.kind is Kind.SKILL}
    assert names == {"there"}


def test_invalid_server_shapes_match_mcp_list(machine_dirs):
    tmp_path, home, project = machine_dirs
    servers = {
        "args-null": {"command": "/bin/true", "args": None},
        "args-text": {"command": "/bin/true", "args": "--x"},
        "args-number": {"command": "/bin/true", "args": [1]},
        "env-null": {"command": "/bin/true", "env": None},
        "env-number": {"command": "/bin/true", "env": {"A": 1}},
        "headers-text": {"type": "http", "url": "http://127.0.0.1:9/a", "headers": "x"},
        "good": {"command": "/bin/true", "args": ["--x"]},
    }
    write_text(home / ".claude.json", json.dumps({"mcpServers": servers}))
    theirs = their_mcp_statuses(claude(home, project, "mcp", "list"))
    assert set(theirs) == {"good"}
    assert enabled(tmp_path, home, project, Kind.MCP) == {"good"}


@pytest.mark.parametrize(
    ("entry", "says"),
    [
        ({"type": "http", "url": "not-a-url"}, "not a valid URL"),
        ({"type": "sse", "url": "nope"}, "not a valid URL"),
        ({"type": "http", "url": "ftp://host/a"}, "protocol must be"),
        ({"type": "http", "url": ""}, "Not configured"),
    ],
)
def test_server_url_it_can_never_connect_to_fails_there_too(machine_dirs, entry, says):
    tmp_path, home, project = machine_dirs
    write_text(home / ".claude.json", json.dumps({"mcpServers": {"x": entry}}))
    assert says in claude(home, project, "mcp", "list")
    assert [i.status for i in scan(tmp_path, home, project)] == [Status.BROKEN]


def test_state_entry_matches_mcp_list_inside_a_repository(machine_dirs):
    tmp_path, home, project = machine_dirs
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    below = project / "sub"
    below.mkdir()
    write_text(below / ".mcp.json", json.dumps({"mcpServers": {"approved": STDIO}}))
    write_text(below / ".claude" / "settings.json", '{"enabledMcpjsonServers": ["approved"]}')
    write_text(
        home / ".claude.json",
        json.dumps(
            {
                "projects": {
                    str(project): {
                        "hasTrustDialogAccepted": True,
                        "mcpServers": {"at-root": STDIO},
                    },
                    str(below): {"hasTrustDialogAccepted": False, "mcpServers": {"at-sub": STDIO}},
                }
            }
        ),
    )
    theirs = their_mcp_statuses(claude(home, below, "mcp", "list"))
    ours = {i.name: i.status for i in scan(tmp_path, home, below) if i.kind is Kind.MCP}
    assert ours == theirs
    assert set(theirs) == {"approved", "at-root"}


@pytest.mark.parametrize("where", ["cwd", "middle", "above"])
def test_only_the_repository_root_entry_trusts_a_folder(machine_dirs, where):
    tmp_path, home, project = machine_dirs
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    below = project / "a" / "b"
    below.mkdir(parents=True)
    write_text(below / ".mcp.json", json.dumps({"mcpServers": {"s": STDIO}}))
    write_text(below / ".claude" / "settings.json", '{"enabledMcpjsonServers": ["s"]}')
    path = {"cwd": below, "middle": below.parent, "above": tmp_path}[where]
    write_text(
        home / ".claude.json",
        json.dumps({"projects": {str(path): {"hasTrustDialogAccepted": True}}}),
    )
    theirs = their_mcp_statuses(claude(home, below, "mcp", "list"))
    ours = {i.name: i.status for i in scan(tmp_path, home, below) if i.kind is Kind.MCP}
    assert ours == theirs == {"s": Status.PENDING}


def test_project_settings_apply_before_trust(machine_dirs):
    """So untrusted project settings stay in the merged view."""
    tmp_path, home, project = machine_dirs
    write_text(home / ".claude.json", json.dumps({"mcpServers": {"u": STDIO}}))
    write_text(
        project / ".claude" / "settings.json",
        json.dumps({"deniedMcpServers": [{"serverName": "u"}]}),
    )
    assert "No MCP servers configured" in claude(home, project, "mcp", "list")
    assert [i.status for i in scan(tmp_path, home, project)] == [Status.DISABLED]


@pytest.mark.parametrize(
    "manifest",
    [
        {"mcpServers": {"inline": {"command": "/bin/true", "args": ["i"]}}},
        {"mcpServers": "./o.json"},
        {"mcpServers": ["./o.json", {"inline": {"command": "/bin/true", "args": ["i"]}}]},
        {},
    ],
)
def test_plugin_servers_match_mcp_list(machine_dirs, manifest):
    tmp_path, home, project = machine_dirs

    def build(root):
        write_text(root / ".mcp.json", json.dumps({"dot": {"command": "/bin/true", "args": ["d"]}}))
        write_text(
            root / "o.json",
            json.dumps({"mcpServers": {"o": {"command": "/bin/true", "args": ["o"]}}}),
        )

    installed_through_a_marketplace(home, project, build, manifest)
    theirs = their_mcp_statuses(claude(home, project, "mcp", "list"))
    assert enabled(tmp_path, home, project, Kind.MCP) == set(theirs)
    assert "plugin:p:dot" in theirs


def test_plugin_server_identity_matches_mcp_list(machine_dirs):
    tmp_path, home, project = machine_dirs

    def build(root):
        servers = {
            "copy": {"command": "/bin/true"},
            "same": {"command": "/bin/false"},
            "bad": {"type": "http"},
            "first": {"type": "http", "url": "http://127.0.0.1:9/a"},
            "second": {"type": "sse", "url": "http://127.0.0.1:9/a"},
        }
        write_text(root / ".mcp.json", json.dumps({"mcpServers": servers}))

    installed_through_a_marketplace(home, project, build, {})
    state = json.loads((home / ".claude.json").read_text())
    state["mcpServers"] = {"mine": {"command": "/bin/true"}, "same": {"command": "/bin/echo"}}
    (home / ".claude.json").write_text(json.dumps(state))
    theirs = their_mcp_statuses(claude(home, project, "mcp", "list"))
    assert enabled(tmp_path, home, project, Kind.MCP) == set(theirs)
    assert set(theirs) == {"mine", "same", "plugin:p:same", "plugin:p:first"}


def test_a_disabled_plugin_has_no_servers_in_mcp_list(machine_dirs):
    tmp_path, home, project = machine_dirs
    installed_through_a_marketplace(
        home,
        project,
        lambda root: write_text(root / ".mcp.json", json.dumps({"mcpServers": {"pm": STDIO}})),
        {},
    )
    claude(home, project, "plugin", "disable", "p@m")
    assert "plugin:p:pm" not in claude(home, project, "mcp", "list")
    assert enabled(tmp_path, home, project, Kind.MCP) == set()


@pytest.mark.parametrize(
    ("entry", "commands"),
    [
        ({"x": {"content": "---\ndescription: D\n---\nB"}}, {"p:x"}),
        ({"g": {"source": "./a.md"}, "x": {"source": "./no.md"}}, {"p:g"}),
    ],
)
def test_manifest_command_entries_match_startup_listing(machine_dirs, built_in, entry, commands):
    tmp_path, home, project = machine_dirs
    installed_through_a_marketplace(
        home, project, lambda root: write_text(root / "a.md", "Body\n"), {"commands": entry}
    )
    assert set(startup_listing(home, project)["slash_commands"]) - built_in[1] == commands
    assert enabled(tmp_path, home, project, Kind.COMMAND) == commands


def test_invalid_manifest_commands_fall_back_to_the_default(machine_dirs, built_in):
    tmp_path, home, project = machine_dirs

    def build(root):
        write_text(root / "a.md", "Body\n")
        write_text(root / "commands" / "z.md", "Body\n")

    installed_through_a_marketplace(home, project, build, {})
    record = json.loads((home / ".claude" / "plugins" / "installed_plugins.json").read_text())
    install_path = Path(record["plugins"]["p@m"][0]["installPath"])
    write_text(
        install_path / ".claude-plugin" / "plugin.json",
        json.dumps(
            {
                "name": "p",
                "version": "1.0.0",
                "commands": {"g": {"source": "./a.md"}, "x": {"source": "../esc.md"}},
            }
        ),
    )
    assert set(startup_listing(home, project)["slash_commands"]) - built_in[1] == {"p:z"}
    assert enabled(tmp_path, home, project, Kind.COMMAND) == {"p:z"}


def test_hook_file_named_twice_runs_once(machine_dirs):
    tmp_path, home, project = machine_dirs
    installed_through_a_marketplace(
        home,
        project,
        lambda root: write_text(
            root / "hooks" / "hooks.json",
            json.dumps({"hooks": {"SessionStart": [counting_hook(home)]}}),
        ),
        {"hooks": "./hooks/hooks.json"},
    )
    startup_listing(home, project)
    assert (home / "ran").read_text().count("x") == 1
    assert sum(i.kind is Kind.HOOK for i in scan(tmp_path, home, project)) == 1


def test_plugin_in_two_scopes_loads_once(machine_dirs, built_in):
    tmp_path, home, project = machine_dirs

    def build(root):
        write_skill(root / "skills" / "a")
        write_text(root / "commands" / "c.md", "Body\n")

    installed_through_a_marketplace(home, project, build, {})
    claude(home, project, "plugin", "install", "p@m", "--scope", "project")
    listing = startup_listing(home, project)
    assert sorted(set(listing["slash_commands"]) - built_in[1]) == ["p:a", "p:c"]
    items = scan(tmp_path, home, project)
    assert sum(i.kind is Kind.PLUGIN for i in items) == 2
    assert sum(i.kind is Kind.COMMAND for i in items) == 1
    assert sum(i.kind is Kind.SKILL for i in items) == 1


def test_plugin_skill_names_match_startup_listing(machine_dirs, built_in):
    tmp_path, home, project = machine_dirs

    def build(root):
        write_skill(root / "skills" / "dira", "name: fma\ndescription: d")
        write_skill(root / "skills" / "dir.b")
        write_skill(root / "skills" / "dir c")
        write_skill(root / "skills" / "dird", "name: p:fmd\ndescription: d")
        write_skill(root / "skills" / "cond", "paths: '*.py'\ndescription: d")
        write_skill(
            root / "skills" / "both",
            "disable-model-invocation: true\nuser-invocable: false\ndescription: d",
        )

    installed_through_a_marketplace(home, project, build, {})
    theirs = compare_skills(tmp_path, home, project, built_in)
    assert theirs == {"p:dira", "p:dir-b", "p:dir-c", "p:dird", "p:cond"}


def test_root_plugin_skill_name_matches_startup_listing(machine_dirs, built_in):
    tmp_path, home, project = machine_dirs
    installed_through_a_marketplace(
        home, project, lambda root: write_skill(root, "name: rooty\ndescription: d"), {}
    )
    assert compare_skills(tmp_path, home, project, built_in) == {"p:rooty"}


@pytest.mark.parametrize(
    ("manifest", "settings", "plugin"),
    [
        ({"name": "fold"}, {}, "fold@skills-dir"),
        ({"name": "other"}, {}, "other@skills-dir"),
        ({"name": "fold"}, {"enabledPlugins": {"fold@skills-dir": False}}, None),
        ({"name": "fold", "defaultEnabled": False}, {}, None),
    ],
)
def test_skills_dir_plugins_match_startup_listing(
    machine_dirs, built_in, manifest, settings, plugin
):
    tmp_path, home, project = machine_dirs
    folder = home / ".claude" / "skills" / "fold"
    write_text(folder / ".claude-plugin" / "plugin.json", json.dumps(manifest))
    write_skill(folder / "skills" / "q")
    write_text(folder / "commands" / "k.md", "Body\n")
    write_text(home / ".claude" / "settings.json", json.dumps(settings))
    listing = startup_listing(home, project)
    loaded = {p["source"] for p in listing["plugins"] if "skills-dir" in p["source"]}
    assert loaded == ({plugin} if plugin else set())
    assert compare_skills(tmp_path, home, project, built_in) == set(listing["skills"]) - built_in[2]
    ours = {i.name: i.status for i in scan(tmp_path, home, project) if i.kind is Kind.PLUGIN}
    assert {name for name, status in ours.items() if status is Status.ENABLED} == loaded
    commands = set(listing["slash_commands"]) - built_in[1] - set(listing["skills"])
    assert enabled(tmp_path, home, project, Kind.COMMAND) == commands


@pytest.mark.parametrize("trusted", [False, True])
def test_project_skills_dir_plugin_matches_startup_listing(machine_dirs, trusted):
    tmp_path, home, project = machine_dirs
    folder = project / ".claude" / "skills" / "fold"
    write_text(folder / ".claude-plugin" / "plugin.json", '{"name": "fold"}')
    write_skill(folder / "skills" / "q")
    write_text(
        home / ".claude.json",
        json.dumps({"projects": {str(project): {"hasTrustDialogAccepted": trusted}}}),
    )
    listing = startup_listing(home, project)
    assert ("fold:q" in listing["skills"]) is trusted
    status = {i.name: i.status for i in scan(tmp_path, home, project)}["fold@skills-dir"]
    assert status is (Status.ENABLED if trusted else Status.PENDING)


@pytest.mark.parametrize(
    "hooks",
    [
        {"PreToolUse": [{"hooks": [{"type": "command"}]}]},
        {"PermissionRequest": [{"hooks": [{"type": "command"}]}]},
        {"PreToolUse": [{"hooks": [{"type": "nope", "command": "x"}]}]},
        {"PreToolUse": [{"hooks": [{"type": "http", "url": "nope"}]}]},
        {"PreToolUse": [{"hooks": [1]}]},
        {"PreToolUse": [{}]},
        {"PreToolUse": "x"},
    ],
)
def test_invalid_guard_hook_costs_the_settings_file(machine_dirs, built_in, hooks):
    tmp_path, home, project = machine_dirs
    write_skill(home / ".claude" / "skills" / "s")
    write_text(
        home / ".claude" / "settings.json",
        json.dumps(
            {
                "skillOverrides": {"s": "off"},
                "hooks": {**hooks, "SessionStart": [marker_hook_group(home)]},
            }
        ),
    )
    compare_skills(tmp_path, home, project, built_in)
    assert "s" in set(startup_listing(home, project)["skills"])
    assert not list(home.glob("*.ran"))
    statuses = {i.name: i.status for i in scan(tmp_path, home, project) if i.kind is Kind.HOOK}
    assert statuses["SessionStart"] is Status.DISABLED


def marker_hook_group(home):
    return {"hooks": [marker_hook(home, "hook")]}


def test_bad_url_on_another_event_costs_only_that_hook(machine_dirs):
    tmp_path, home, project = machine_dirs
    write_text(
        home / ".claude" / "settings.json",
        json.dumps(
            {
                "hooks": {
                    "SessionStart": [
                        {"hooks": [{"type": "http", "url": "nope"}, marker_hook(home, "good")]}
                    ]
                }
            }
        ),
    )
    startup_listing(home, project)
    assert [path.name for path in home.glob("*.ran")] == ["good.ran"]
    statuses = sorted(i.status for i in scan(tmp_path, home, project) if i.kind is Kind.HOOK)
    assert statuses == sorted([Status.BROKEN, Status.ENABLED])


@pytest.mark.parametrize(
    ("allowed", "runs"),
    [(None, True), ([], False), (["http://127.0.0.1:{port}/a*"], True), (["https://x/*"], False)],
)
def test_http_hook_allowlist_matches_what_runs(machine_dirs, allowed, runs):
    tmp_path, home, project = machine_dirs
    calls = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            calls.append(self.path)
            self.rfile.read(int(self.headers.get("content-length", 0)))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    settings = {
        "hooks": {
            "UserPromptSubmit": [{"hooks": [{"type": "http", "url": f"http://127.0.0.1:{port}/a"}]}]
        }
    }
    if allowed is not None:
        settings["allowedHttpHookUrls"] = [pattern.format(port=port) for pattern in allowed]
    write_text(home / ".claude" / "settings.json", json.dumps(settings))
    startup_listing(home, project)
    server.shutdown()
    assert bool(calls) is runs
    status = next(i.status for i in scan(tmp_path, home, project) if i.kind is Kind.HOOK)
    assert status is (Status.ENABLED if runs else Status.DISABLED)


# Managed settings


def write_managed(tmp_path, name, data):
    write_text(managed_dir(tmp_path) / name, data if isinstance(data, str) else json.dumps(data))


def test_drop_ins_match_startup_listing(machine_dirs, built_in, private_etc):
    tmp_path, home, project = machine_dirs
    for name in "abcd":
        write_skill(home / ".claude" / "skills" / name)
    write_managed(tmp_path, "managed-settings.json", {"skillOverrides": {"a": "off"}})
    write_managed(tmp_path, "managed-settings.d/10.json", {"skillOverrides": {"b": "off"}})
    write_managed(tmp_path, "managed-settings.d/.hidden.json", {"skillOverrides": {"c": "off"}})
    write_managed(tmp_path, "managed-settings.d/20.txt", '{"skillOverrides": {"d": "off"}}')
    assert compare_skills(tmp_path, home, project, built_in) == {"c", "d"}


def test_later_drop_in_wins_for_a_single_value(machine_dirs, private_etc):
    tmp_path, home, project = machine_dirs
    write_managed(tmp_path, "managed-settings.json", {"disableAllHooks": True})
    write_managed(tmp_path, "managed-settings.d/20.json", {"disableAllHooks": False})
    write_text(
        home / ".claude" / "settings.json",
        json.dumps({"hooks": {"SessionStart": [marker_hook_group(home)]}}),
    )
    startup_listing(home, project)
    assert len(list(home.glob("*.ran"))) == 1
    assert [i.status for i in scan(tmp_path, home, project)] == [Status.ENABLED]


def test_drop_in_lists_join_in_mcp_list(machine_dirs, private_etc):
    tmp_path, home, project = machine_dirs
    write_managed(
        tmp_path, "managed-settings.d/10.json", {"deniedMcpServers": [{"serverName": "x"}]}
    )
    write_managed(
        tmp_path, "managed-settings.d/20.json", {"deniedMcpServers": [{"serverName": "y"}]}
    )
    servers = {name: {"command": "/bin/true", "args": [name]} for name in "xyz"}
    write_text(home / ".claude.json", json.dumps({"mcpServers": servers}))
    assert set(their_mcp_statuses(claude(home, project, "mcp", "list"))) == {"z"}
    assert enabled(tmp_path, home, project, Kind.MCP) == {"z"}


def test_managed_definitions_match_startup_listing(machine_dirs, built_in, private_etc):
    tmp_path, home, project = machine_dirs
    root = managed_dir(tmp_path) / ".claude"
    write_text(root / "commands" / "mc.md", "Body\n")
    write_skill(root / "skills" / "ms")
    write_text(root / "agents" / "ma.md", agent_file("ma"))
    listing = startup_listing(home, project)
    assert set(listing["slash_commands"]) - built_in[1] == {"mc", "ms"}
    assert set(listing["agents"]) - built_in[0] == {"ma"}
    assert enabled(tmp_path, home, project, Kind.COMMAND) == {"mc"}
    assert enabled(tmp_path, home, project, Kind.SKILL) == {"ms"}
    assert enabled(tmp_path, home, project, Kind.SUBAGENT) == {"ma"}


def test_managed_command_beats_a_user_command(machine_dirs, built_in, private_etc):
    tmp_path, home, project = machine_dirs
    write_text(
        managed_dir(tmp_path) / ".claude" / "commands" / "x.md",
        "---\nuser-invocable: false\n---\nBody\n",
    )
    write_text(home / ".claude" / "commands" / "x.md", "Body\n")
    assert "x" not in startup_listing(home, project)["slash_commands"]
    ours = {i.scope: i.status for i in scan(tmp_path, home, project) if i.kind is Kind.COMMAND}
    assert ours == {Scope.SYSTEM: Status.ENABLED, Scope.USER: Status.SHADOWED}


REMOTE = "https://example.invalid/a"


def test_managed_settings_servers_match_mcp_list(machine_dirs, private_etc):
    tmp_path, home, project = machine_dirs
    write_managed(
        tmp_path,
        "managed-settings.json",
        {
            "managedMcpServers": {
                "http": {"type": "http", "url": REMOTE},
                "sse": {"type": "sse", "url": REMOTE + "b"},
                "streamable": {"type": "streamable-http", "url": REMOTE + "c"},
                "stdio": STDIO,
                "ws": {"type": "ws", "url": "wss://example.invalid/w"},
                "plain": {"type": "http", "url": "http://example.invalid/b"},
                "variable": {"type": "http", "url": "https://${HOST}/c"},
                "header": {"type": "http", "url": REMOTE, "headers": {"A": "${TOKEN}"}},
            }
        },
    )
    theirs = their_mcp_statuses(claude(home, project, "mcp", "list"))
    assert enabled(tmp_path, home, project, Kind.MCP) == set(theirs)
    assert set(theirs) == {"http", "sse", "streamable"}


@pytest.mark.parametrize(
    ("file_servers", "settings", "user", "expected"),
    [
        (
            None,
            {"managedMcpServers": {"g": {"type": "http", "url": REMOTE}}, "allowedMcpServers": []},
            {"u": STDIO},
            {"g"},
        ),
        ({"m": STDIO}, {"allowedMcpServers": []}, {}, {"m"}),
        ({"m": STDIO}, {"deniedMcpServers": [{"serverName": "m"}]}, {}, set()),
        ({"m": {"command": "/bin/true", "args": ["${X}"]}}, {"allowedMcpServers": []}, {}, set()),
        (
            {"m": {"command": "/bin/true", "env": {"A": "${X}"}}},
            {"allowedMcpServers": []},
            {},
            {"m"},
        ),
        (
            {"m": STDIO},
            {"managedMcpServers": {"g": {"type": "http", "url": REMOTE}}},
            {"u": {"command": "/bin/false"}},
            {"m", "g"},
        ),
        (
            None,
            {"managedMcpServers": {"same": {"type": "http", "url": REMOTE}}},
            {"same": STDIO},
            {"same"},
        ),
    ],
)
def test_managed_policy_matches_mcp_list(
    machine_dirs, private_etc, file_servers, settings, user, expected
):
    tmp_path, home, project = machine_dirs
    write_managed(tmp_path, "managed-settings.json", settings)
    if file_servers is not None:
        write_managed(tmp_path, "managed-mcp.json", {"mcpServers": file_servers})
    write_text(home / ".claude.json", json.dumps({"mcpServers": user}))
    theirs = their_mcp_statuses(claude(home, project, "mcp", "list", env={"X": "1"}))
    assert set(theirs) == expected
    assert enabled(tmp_path, home, project, Kind.MCP) == expected


@pytest.mark.parametrize("corrupt", [True, False])
def test_unreadable_managed_file_keeps_control_in_mcp_list(machine_dirs, private_etc, corrupt):
    tmp_path, home, project = machine_dirs
    if corrupt:
        write_managed(tmp_path, "managed-mcp.json", "{bad")
    else:
        (managed_dir(tmp_path) / "managed-mcp.json").mkdir(parents=True)
    write_text(home / ".claude.json", json.dumps({"mcpServers": {"u": STDIO}}))
    assert "No MCP servers configured" in claude(home, project, "mcp", "list")
    assert [i.status for i in scan(tmp_path, home, project)] == [Status.DISABLED]


def strict_fixture(tmp_path, home, project, strict):
    def build(root):
        write_text(root / "commands" / "pc.md", "Body\n")
        write_text(root / "agents" / "pa.md", agent_file("pa"))
        write_skill(root / "skills" / "ps")
        write_text(
            root / "hooks" / "hooks.json",
            json.dumps({"hooks": {"SessionStart": [{"hooks": [marker_hook(home, "plugin")]}]}}),
        )
        write_text(
            root / ".mcp.json", json.dumps({"mcpServers": {"pm": {"command": "/bin/false"}}})
        )

    installed_through_a_marketplace(home, project, build, {})
    settings_path = home / ".claude" / "settings.json"
    settings = json.loads(settings_path.read_text())
    settings["hooks"] = {"SessionStart": [{"hooks": [marker_hook(home, "user")]}]}
    settings_path.write_text(json.dumps(settings))
    state = json.loads((home / ".claude.json").read_text())
    state["mcpServers"] = {"um": STDIO}
    (home / ".claude.json").write_text(json.dumps(state))
    write_text(home / ".claude" / "commands" / "uc.md", "Body\n")
    write_skill(home / ".claude" / "skills" / "us")
    write_text(home / ".claude" / "agents" / "ua.md", agent_file("ua"))
    root = managed_dir(tmp_path) / ".claude"
    write_text(root / "commands" / "mc.md", "Body\n")
    write_skill(root / "skills" / "ms")
    write_text(root / "agents" / "ma.md", agent_file("ma"))
    write_managed(
        tmp_path,
        "managed-settings.json",
        {
            "strictPluginOnlyCustomization": strict,
            "hooks": {"SessionStart": [{"hooks": [marker_hook(home, "managed")]}]},
        },
    )


@pytest.mark.parametrize("strict", [True, ["skills"], ["agents"], ["hooks"], ["mcp"], ["commands"]])
def test_strict_customization_matches_the_agent(machine_dirs, built_in, private_etc, strict):
    tmp_path, home, project = machine_dirs
    strict_fixture(tmp_path, home, project, strict)
    listing = startup_listing(home, project)
    assert enabled(tmp_path, home, project, Kind.SUBAGENT) == set(listing["agents"]) - built_in[0]
    assert (
        enabled(tmp_path, home, project, Kind.COMMAND)
        | enabled(tmp_path, home, project, Kind.SKILL)
        == set(listing["slash_commands"]) - built_in[1]
    )
    ran = {path.name.removesuffix(".ran") for path in home.glob("*.ran")}
    hooks = {
        i.details.get("plugin", i.scope): i.status
        for i in scan(tmp_path, home, project)
        if i.kind is Kind.HOOK
    }
    assert {
        {Scope.SYSTEM: "managed", Scope.USER: "user", "p@m": "plugin"}[source]
        for source, status in hooks.items()
        if status is Status.ENABLED
    } == ran
    theirs = their_mcp_statuses(claude(home, project, "mcp", "list"))
    assert enabled(tmp_path, home, project, Kind.MCP) == set(theirs)


@pytest.mark.parametrize(
    ("managed", "ran"),
    [
        ({"allowManagedHooksOnly": True}, set()),
        ({"allowManagedHooksOnly": True, "enabledPlugins": {"p@m": True}}, {"plugin"}),
    ],
)
def test_managed_hooks_only_matches_what_runs(machine_dirs, private_etc, managed, ran):
    tmp_path, home, project = machine_dirs
    installed_through_a_marketplace(
        home,
        project,
        lambda root: write_text(
            root / "hooks" / "hooks.json",
            json.dumps({"hooks": {"SessionStart": [{"hooks": [marker_hook(home, "plugin")]}]}}),
        ),
        {},
    )
    write_managed(tmp_path, "managed-settings.json", managed)
    startup_listing(home, project)
    assert {path.name.removesuffix(".ran") for path in home.glob("*.ran")} == ran
    statuses = [i.status for i in scan(tmp_path, home, project) if i.kind is Kind.HOOK]
    assert statuses == [Status.ENABLED if ran else Status.DISABLED]


def test_invalid_guard_hook_in_managed_settings_is_kept(machine_dirs, built_in, private_etc):
    tmp_path, home, project = machine_dirs
    write_skill(home / ".claude" / "skills" / "s")
    write_managed(
        tmp_path,
        "managed-settings.json",
        {
            "skillOverrides": {"s": "off"},
            "hooks": {"PreToolUse": [{"hooks": [{"type": "command"}]}]},
        },
    )
    assert compare_skills(tmp_path, home, project, built_in) == set()


def test_strict_customization_blocks_skills_dir_plugins_in_the_agent(
    machine_dirs, built_in, private_etc
):
    tmp_path, home, project = machine_dirs
    folder = home / ".claude" / "skills" / "fold"
    write_text(folder / ".claude-plugin" / "plugin.json", '{"name": "fold"}')
    write_skill(folder / "skills" / "q")
    write_managed(tmp_path, "managed-settings.json", {"strictPluginOnlyCustomization": ["skills"]})
    assert compare_skills(tmp_path, home, project, built_in) == set()
