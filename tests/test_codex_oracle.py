"""Compare discovery with Codex's own listing on the same fixture.

Runs only when AGT_ENV_CODEX points at a real Codex binary, and never touches
the developer's configuration: HOME and CODEX_HOME are temporary.
"""

import json
import os
import shlex
import shutil
import subprocess

import pytest
from conftest import write_skill

from agt_env.adapters.base import Context
from agt_env.adapters.codex import Codex
from agt_env.model import Kind, Scope, Status

CODEX = os.environ.get("AGT_ENV_CODEX")


def codex_skills(home, cwd):
    process = subprocess.Popen(
        [CODEX, "app-server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        env={"PATH": os.environ["PATH"], "HOME": str(home), "CODEX_HOME": str(home / ".codex")},
        cwd=cwd,
    )

    def send(message):
        process.stdin.write(json.dumps(message) + "\n")
        process.stdin.flush()

    def response(request_id):
        for line in process.stdout:
            message = json.loads(line)
            if message.get("id") == request_id:
                return message["result"]
        raise AssertionError("codex app-server exited early")

    try:
        send(
            {
                "id": 1,
                "method": "initialize",
                "params": {"clientInfo": {"name": "t", "version": "0"}},
            }
        )
        response(1)
        send({"method": "initialized", "params": {}})
        send(
            {"id": 2, "method": "skills/list", "params": {"cwds": [str(cwd)], "forceReload": True}}
        )
        return response(2)["data"][0]
    finally:
        process.kill()
        process.wait()


@pytest.mark.skipif(not CODEX, reason="AGT_ENV_CODEX is not set")
def test_matches_codex_listing(tmp_path, monkeypatch):
    home, project = tmp_path / "home", tmp_path / "project"
    agents = home / ".agents" / "skills"
    (home / ".codex").mkdir(parents=True)
    write_skill(agents / "plain", "name: plain\ndescription: d")
    write_skill(agents / "dir-name", "name: fm-name\ndescription: d")
    write_skill(agents / "no-name")
    write_skill(agents / "group" / "nested")
    write_skill(agents / "outer")
    write_skill(agents / "outer" / "inner")
    write_skill(agents / ".hidden")
    write_skill(agents / "no-desc", "name: no-desc")
    write_skill(agents / "multi", "description: >\n  folded\n  text")
    write_skill(agents / "off-file")
    write_skill(agents / "off-dir")
    deep = agents
    for level in range(1, 9):
        deep = deep / f"l{level}"
        write_skill(deep, f"name: depth{level}\ndescription: d")
    write_skill(tmp_path / "elsewhere" / "target", "name: linked\ndescription: d")
    (agents / "link").symlink_to(tmp_path / "elsewhere" / "target")
    write_skill(home / ".codex" / "skills" / "legacy")
    (home / ".codex" / "skills" / "link").symlink_to(tmp_path / "elsewhere" / "target")
    write_skill(agents / "same-name", "name: dup\ndescription: d")
    write_skill(home / ".codex" / "skills" / "same-name", "name: dup\ndescription: d")
    write_skill(project / ".agents" / "skills" / "repo-a")
    write_skill(project / ".codex" / "skills" / "repo-b")
    write_skill(tmp_path / "elsewhere" / "both", "name: both\ndescription: d")
    (agents / "both").symlink_to(tmp_path / "elsewhere" / "both")
    (project / ".agents" / "skills" / "both").symlink_to(tmp_path / "elsewhere" / "both")
    (home / ".codex" / "config.toml").write_text(
        f'[[skills.config]]\npath = "{agents}/off-file/SKILL.md"\nenabled = false\n'
        f'[[skills.config]]\npath = "{agents}/off-dir"\nenabled = false\n'
    )

    theirs = codex_skills(home, project)
    expected = sorted(
        (skill["name"], {"repo": "project"}.get(skill["scope"], skill["scope"]), skill["enabled"])
        for skill in theirs["skills"]
    )
    failed = {os.path.basename(os.path.dirname(error["path"])) for error in theirs["errors"]}

    report = Codex().scan(
        Context(home=home, cwd=project, env={"PATH": ""}, system_root=tmp_path / "system")
    )
    ours = sorted(
        (item.name, item.scope.value, item.status is Status.ENABLED)
        for item in report.items
        if item.kind is Kind.SKILL and item.status is not Status.BROKEN
    )
    assert ours == expected
    assert {item.name for item in report.items if item.status is Status.BROKEN} == failed
    assert any(item.scope is Scope.SYSTEM for item in report.items)


def codex_mcp_servers(home, cwd):
    output = subprocess.run(
        [CODEX, "mcp", "list", "--json"],
        check=False,
        cwd=cwd,
        env={"PATH": os.environ["PATH"], "HOME": str(home), "CODEX_HOME": str(home / ".codex")},
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout
    return {server["name"]: server for server in json.loads(output)}


@pytest.mark.skipif(not CODEX, reason="AGT_ENV_CODEX is not set")
@pytest.mark.parametrize("trusted", [False, True])
def test_mcp_matches_codex_listing(tmp_path, trusted):
    home, project = tmp_path / "home", tmp_path / "project"
    (home / ".codex").mkdir(parents=True)
    (project / ".codex").mkdir(parents=True)
    config = """
[mcp_servers.u-stdio]
command = "/bin/true"
[mcp_servers.u-http]
url = "http://127.0.0.1:9/mcp"
[mcp_servers.u-off]
command = "/bin/true"
enabled = false
"""
    if trusted:
        config += f'[projects."{project}"]\ntrust_level = "trusted"\n'
    (home / ".codex" / "config.toml").write_text(config)
    (project / ".codex" / "config.toml").write_text('[mcp_servers.p-srv]\ncommand = "/bin/true"\n')

    theirs = codex_mcp_servers(home, project)
    report = Codex().scan(
        Context(home=home, cwd=project, env={"PATH": ""}, system_root=tmp_path / "system")
    )
    ours = {item.name: item for item in report.items if item.kind is Kind.MCP}

    # An untrusted project's servers aren't loaded yet, so Codex doesn't list them.
    assert ours["p-srv"].status is (Status.ENABLED if trusted else Status.PENDING)
    loaded = {name: item for name, item in ours.items() if item.status is not Status.PENDING}
    assert set(loaded) == set(theirs)
    for name, server in theirs.items():
        assert (loaded[name].status is Status.ENABLED) is server["enabled"]
        assert loaded[name].details["transport"] == server["transport"]["type"]


@pytest.mark.skipif(not CODEX, reason="AGT_ENV_CODEX is not set")
def test_mcp_precedence_matches_codex_listing(tmp_path):
    home, project = tmp_path / "home", tmp_path / "project"
    (home / ".codex").mkdir(parents=True)
    (project / ".codex").mkdir(parents=True)
    (home / ".codex" / "config.toml").write_text(
        f"""
[mcp_servers.both]
command = "/bin/true"
[mcp_servers.off-in-user]
command = "/bin/true"
enabled = false
[mcp_servers.off-in-project]
command = "/bin/true"
[mcp_servers.on-again]
command = "/bin/true"
enabled = false
[mcp_servers.env-only]
command = "/bin/true"
[mcp_servers.user-only]
command = "/bin/true"
[projects."{project}"]
trust_level = "trusted"
"""
    )
    (project / ".codex" / "config.toml").write_text(
        """
[mcp_servers.both]
command = "/bin/false"
[mcp_servers.off-in-user]
command = "/bin/false"
[mcp_servers.off-in-project]
command = "/bin/false"
enabled = false
[mcp_servers.on-again]
enabled = true
[mcp_servers.env-only.env]
FROM_PROJECT = "1"
"""
    )
    theirs = codex_mcp_servers(home, project)
    report = Codex().scan(
        Context(home=home, cwd=project, env={"PATH": ""}, system_root=tmp_path / "system")
    )
    ours = {
        item.name: (item.status is Status.ENABLED, item.details["command"])
        for item in report.items
        if item.kind is Kind.MCP and item.status is not Status.SHADOWED
    }
    assert ours == {
        name: (server["enabled"], server["transport"]["command"]) for name, server in theirs.items()
    }


PLUGIN_ON = '[plugins."p@m"]\nenabled = true\n'


def codex_plugin(home, version="1.0.0", manifest=None):
    root = home / ".codex" / "plugins" / "cache" / "m" / "p" / version
    manifest = {"name": "p"} if manifest is None else manifest
    (root / ".codex-plugin").mkdir(parents=True)
    (root / ".codex-plugin" / "plugin.json").write_text(json.dumps(manifest))
    return root


def plain_plugin(home):
    write_skill(codex_plugin(home) / "skills" / "a", "name: a\ndescription: d")


def renamed_plugin(home):
    root = codex_plugin(home, manifest={"name": "renamed"})
    write_skill(root / "skills" / "folder", "name: from-field\ndescription: d")


def unnamed_plugin(home):
    write_skill(codex_plugin(home, manifest={}) / "skills" / "a", "name: a\ndescription: d")


def replacing_paths(home):
    root = codex_plugin(home, manifest={"name": "p", "skills": ["./x/", "./y/"]})
    write_skill(root / "x" / "a", "name: a\ndescription: d")
    write_skill(root / "y" / "b", "name: b\ndescription: d")
    write_skill(root / "skills" / "default", "name: default\ndescription: d")


def invalid_path_falls_back(home):
    root = codex_plugin(home, manifest={"name": "p", "skills": "../../outside/"})
    write_skill(root.parent.parent / "outside" / "a", "name: a\ndescription: d")
    write_skill(root / "skills" / "default", "name: default\ndescription: d")


def versions(*names):
    def build(home):
        for name in names:
            root = codex_plugin(home, version=name)
            write_skill(root / "skills" / name, f"name: {name}\ndescription: d")

    return build


def highest_version_has_no_manifest(home):
    write_skill(codex_plugin(home) / "skills" / "a", "name: a\ndescription: d")
    (home / ".codex" / "plugins" / "cache" / "m" / "p" / "2.0.0").mkdir()


def nested_and_hidden(home):
    root = codex_plugin(home)
    write_skill(root / "skills" / "a" / "b" / "c" / "d" / "e" / "f", "name: six\ndescription: d")
    write_skill(
        root / "skills" / "a" / "b" / "c" / "d" / "e" / "f" / "g", "name: seven\ndescription: d"
    )
    write_skill(root / "skills" / ".hidden", "name: hidden\ndescription: d")


def same_name_as_a_user_skill(home):
    write_skill(codex_plugin(home) / "skills" / "a", "name: a\ndescription: d")
    write_skill(home / ".codex" / "skills" / "a", "name: a\ndescription: d")


def no_description(home):
    write_skill(codex_plugin(home) / "skills" / "a", "name: a")


@pytest.mark.skipif(not CODEX, reason="AGT_ENV_CODEX is not set")
@pytest.mark.parametrize(
    ("config", "build"),
    [
        (PLUGIN_ON, plain_plugin),
        ('[plugins."p@m"]\nenabled = false\n', plain_plugin),
        ("", plain_plugin),
        (PLUGIN_ON, renamed_plugin),
        (PLUGIN_ON, unnamed_plugin),
        (PLUGIN_ON, replacing_paths),
        (PLUGIN_ON, invalid_path_falls_back),
        (PLUGIN_ON, versions("9.0.0", "10.0.0")),
        (PLUGIN_ON, versions("1.0.0", "1.0.0-beta")),
        (PLUGIN_ON, versions("1.0.0-alpha", "1.0.0-beta")),
        (PLUGIN_ON, versions("1.9", "1.10")),
        (PLUGIN_ON, versions("0abc", "9.9.9")),
        (PLUGIN_ON, versions("local", "1.0.0")),
        (PLUGIN_ON, highest_version_has_no_manifest),
        (PLUGIN_ON, nested_and_hidden),
        (PLUGIN_ON, same_name_as_a_user_skill),
        (PLUGIN_ON, no_description),
    ],
)
def test_plugin_skills_match_codex_listing(tmp_path, config, build):
    home, project = tmp_path / "home", tmp_path / "project"
    (home / ".codex").mkdir(parents=True)
    project.mkdir()
    (home / ".codex" / "config.toml").write_text(config)
    build(home)

    listing = codex_skills(home, project)
    theirs = {
        os.path.realpath(skill["path"]): skill["name"]
        for skill in listing["skills"]
        if skill["scope"] != "system"
    }
    context = Context(home=home, cwd=project, env={"PATH": ""}, system_root=tmp_path / "system")
    items = [item for item in Codex().scan(context).items if item.kind is Kind.SKILL]
    ours = {
        os.path.realpath(item.source_path / "SKILL.md"): item.name
        for item in items
        if item.status is Status.ENABLED and item.scope is not Scope.SYSTEM
    }
    assert ours == theirs
    assert {os.path.realpath(error["path"]) for error in listing["errors"]} == {
        os.path.realpath(item.source_path / "SKILL.md")
        for item in items
        if item.status is Status.BROKEN
    }


@pytest.mark.skipif(not CODEX, reason="AGT_ENV_CODEX is not set")
@pytest.mark.parametrize(
    ("user", "project_entry", "loads"),
    [
        ("", PLUGIN_ON, True),
        (PLUGIN_ON, '[plugins."p@m"]\nenabled = false\n', False),
        ('[plugins."p@m"]\nenabled = false\n', PLUGIN_ON, True),
    ],
)
def test_trusted_project_decides_plugin_state(tmp_path, user, project_entry, loads):
    home, project = tmp_path / "home", tmp_path / "project"
    (home / ".codex").mkdir(parents=True)
    (project / ".codex").mkdir(parents=True)
    (home / ".codex" / "config.toml").write_text(
        f'{user}\n[projects."{project}"]\ntrust_level = "trusted"\n'
    )
    (project / ".codex" / "config.toml").write_text(project_entry)
    plain_plugin(home)

    theirs = {s["name"] for s in codex_skills(home, project)["skills"] if s["scope"] != "system"}
    assert theirs == ({"p:a"} if loads else set())
    context = Context(home=home, cwd=project, env={"PATH": ""}, system_root=tmp_path / "system")
    ours = {
        item.name
        for item in Codex().scan(context).items
        if item.kind is Kind.SKILL
        and item.status is Status.ENABLED
        and item.scope is not Scope.SYSTEM
    }
    assert ours == theirs


@pytest.mark.skipif(not CODEX, reason="AGT_ENV_CODEX is not set")
def test_a_disabled_plugin_skill_matches_codex(tmp_path):
    home, project = tmp_path / "home", tmp_path / "project"
    (home / ".codex").mkdir(parents=True)
    project.mkdir()
    plain_plugin(home)
    skill_file = home / ".codex/plugins/cache/m/p/1.0.0/skills/a/SKILL.md"
    (home / ".codex" / "config.toml").write_text(
        f'{PLUGIN_ON}\n[[skills.config]]\npath = "{skill_file}"\nenabled = false\n'
    )
    theirs = {s["name"]: s["enabled"] for s in codex_skills(home, project)["skills"]}
    assert theirs["p:a"] is False
    context = Context(home=home, cwd=project, env={"PATH": ""}, system_root=tmp_path / "system")
    ours = {item.name: item.status for item in Codex().scan(context).items}
    assert ours["p:a"] is Status.DISABLED


def codex_hooks(home, cwd):
    process = subprocess.Popen(
        [CODEX, "app-server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        env={"PATH": os.environ["PATH"], "HOME": str(home), "CODEX_HOME": str(home / ".codex")},
        cwd=cwd,
    )

    def send(message):
        process.stdin.write(json.dumps(message) + "\n")
        process.stdin.flush()

    def response(request_id):
        for line in process.stdout:
            message = json.loads(line)
            if message.get("id") == request_id:
                return message["result"]
        raise AssertionError("codex app-server exited early")

    try:
        send(
            {
                "id": 1,
                "method": "initialize",
                "params": {
                    "clientInfo": {"name": "t", "version": "0"},
                    "capabilities": {"experimentalApi": True},
                },
            }
        )
        response(1)
        send({"method": "initialized", "params": {}})
        send({"id": 2, "method": "hooks/list", "params": {"cwds": [str(cwd)]}})
        return response(2)["data"][0]
    finally:
        process.kill()
        process.wait()


def our_hooks(tmp_path, home, project):
    context = Context(home=home, cwd=project, env={"PATH": ""}, system_root=tmp_path / "system")
    return [item for item in Codex().scan(context).items if item.kind is Kind.HOOK]


def hook_row(hook):
    event = hook["eventName"][0].upper() + hook["eventName"][1:]
    return f"{event}:{hook['matcher']}" if hook["matcher"] else event


def write_hooks(path, events):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"hooks": events}))


def codex_dirs(tmp_path):
    home, project = tmp_path / "home", tmp_path / "project"
    (home / ".codex").mkdir(parents=True)
    project.mkdir()
    return home, project


COMMAND = {"type": "command", "command": "/bin/true"}
HOOK_VARIANTS = [
    {"Stop": [{"hooks": [COMMAND]}]},
    {"PreToolUse": [{"matcher": "Bash", "hooks": [COMMAND]}]},
    {
        "PreToolUse": [
            {"matcher": "Bash|Edit", "hooks": [COMMAND, {**COMMAND, "command": "/bin/b"}]}
        ]
    },
    {"Stop": [{"hooks": [{**COMMAND, "timeout": 5}]}]},
    {"Stop": [{"hooks": [{**COMMAND, "statusMessage": "working"}]}]},
    {"Stop": [{"hooks": [{**COMMAND, "async": True}]}]},
    {"Stop": [{"hooks": [{**COMMAND, "command": "/bin/ünï code"}]}]},
    {"Stop": [{"hooks": [{"type": "mcp_tool", "server": "s", "tool": "t"}]}]},
    {"SessionStart": [{"hooks": [COMMAND]}], "UserPromptSubmit": [{"hooks": [COMMAND]}]},
]


@pytest.mark.skipif(not CODEX, reason="AGT_ENV_CODEX is not set")
@pytest.mark.parametrize("events", HOOK_VARIANTS)
def test_hook_fingerprints_match_codex(tmp_path, events):
    from agt_env.adapters.codex import _hook_hash
    from agt_env.hooks import handlers

    home, project = codex_dirs(tmp_path)
    write_hooks(home / ".codex" / "hooks.json", events)
    theirs = [hook["currentHash"] for hook in codex_hooks(home, project)["hooks"]]
    assert theirs and [_hook_hash(handler) for handler in handlers(events)] == theirs


@pytest.mark.skipif(not CODEX, reason="AGT_ENV_CODEX is not set")
def test_hook_status_matches_codex(tmp_path):
    home, project = codex_dirs(tmp_path)
    write_hooks(
        home / ".codex" / "hooks.json",
        {
            "Stop": [{"hooks": [COMMAND, {**COMMAND, "command": "/bin/b"}]}],
            "PreToolUse": [{"matcher": "Bash", "hooks": [COMMAND]}],
            "PostToolUse": [{"hooks": [COMMAND]}],
        },
    )
    first = {hook["key"]: hook for hook in codex_hooks(home, project)["hooks"]}
    keys = sorted(first)
    state = [
        f'[hooks.state."{keys[0]}"]\ntrusted_hash = "{first[keys[0]]["currentHash"]}"\n',
        f'[hooks.state."{keys[1]}"]\ntrusted_hash = "sha256:00"\n',
        f'[hooks.state."{keys[2]}"]\nenabled = false\n',
    ]
    (home / ".codex" / "config.toml").write_text("\n".join(state))

    theirs = codex_hooks(home, project)["hooks"]
    assert {hook["trustStatus"] for hook in theirs} == {"trusted", "modified", "untrusted"}
    expected = sorted(
        (
            hook_row(hook),
            "disabled"
            if not hook["enabled"]
            else "enabled"
            if hook["trustStatus"] == "trusted"
            else "pending",
        )
        for hook in theirs
    )
    assert sorted((i.name, i.status.value) for i in our_hooks(tmp_path, home, project)) == expected


@pytest.mark.skipif(not CODEX, reason="AGT_ENV_CODEX is not set")
def test_config_hooks_and_project_hooks_match_codex(tmp_path):
    home, project = codex_dirs(tmp_path)
    write_hooks(home / ".codex" / "hooks.json", {"Stop": [{"hooks": [COMMAND]}]})
    write_hooks(project / ".codex" / "hooks.json", {"Stop": [{"hooks": [COMMAND]}]})
    inline = (
        '[[hooks.SessionStart]]\nmatcher = "startup"\n'
        '[[hooks.SessionStart.hooks]]\ntype = "command"\ncommand = "/bin/inline"\n'
    )
    (project / ".codex" / "config.toml").write_text(inline)
    (home / ".codex" / "config.toml").write_text(
        f'{inline}\n[projects."{project}"]\ntrust_level = "trusted"\n'
    )
    theirs = sorted((hook_row(h), h["source"]) for h in codex_hooks(home, project)["hooks"])
    ours = sorted((i.name, i.scope.value) for i in our_hooks(tmp_path, home, project))
    assert ours == theirs and len(ours) == 4

    (home / ".codex" / "config.toml").write_text(inline)
    assert {h["source"] for h in codex_hooks(home, project)["hooks"]} == {"user"}
    ours = {i.scope: i.status for i in our_hooks(tmp_path, home, project)}
    assert ours[Scope.PROJECT] is Status.PENDING


@pytest.mark.skipif(not CODEX, reason="AGT_ENV_CODEX is not set")
@pytest.mark.parametrize(
    ("hooks_key", "config"),
    [(None, PLUGIN_ON), ("./other.json", PLUGIN_ON), (None, '[plugins."p@m"]\nenabled = false\n')],
)
def test_plugin_hooks_match_codex(tmp_path, hooks_key, config):
    home, project = codex_dirs(tmp_path)
    (home / ".codex" / "config.toml").write_text(config)
    manifest = {"name": "p", **({"hooks": hooks_key} if hooks_key else {})}
    root = codex_plugin(home, manifest=manifest)
    write_hooks(root / "hooks" / "hooks.json", {"Stop": [{"hooks": [COMMAND]}]})
    write_hooks(root / "other.json", {"PreToolUse": [{"hooks": [COMMAND]}]})
    theirs = sorted(hook_row(hook) for hook in codex_hooks(home, project)["hooks"])
    assert sorted(i.name for i in our_hooks(tmp_path, home, project)) == theirs


@pytest.mark.skipif(not CODEX, reason="AGT_ENV_CODEX is not set")
def test_plugin_hook_fingerprint_covers_more_than_the_hook(tmp_path):
    """This is why a reviewed plugin hook is `unknown`, not `enabled`."""
    home, project = codex_dirs(tmp_path)
    (home / ".codex" / "config.toml").write_text(PLUGIN_ON)
    root = codex_plugin(home)
    write_hooks(root / "hooks" / "hooks.json", {"Stop": [{"hooks": [COMMAND]}]})
    hook = codex_hooks(home, project)["hooks"][0]
    assert hook["trustStatus"] == "untrusted" and "/" in hook["currentHash"]
    assert [i.status for i in our_hooks(tmp_path, home, project)] == [Status.PENDING]

    (root / "unrelated.txt").write_text("x")
    assert codex_hooks(home, project)["hooks"][0]["currentHash"] != hook["currentHash"]

    (home / ".codex" / "config.toml").write_text(
        f'{PLUGIN_ON}\n[hooks.state."{hook["key"]}"]\ntrusted_hash = "{hook["currentHash"]}"\n'
    )
    assert [i.status for i in our_hooks(tmp_path, home, project)] == [Status.UNKNOWN]


@pytest.mark.skipif(not CODEX, reason="AGT_ENV_CODEX is not set")
def test_a_bad_hooks_file_gives_nothing_like_codex(tmp_path):
    home, project = codex_dirs(tmp_path)
    write_hooks(
        home / ".codex" / "hooks.json",
        {"Stop": [{"hooks": [{"type": "command"}, COMMAND]}]},
    )
    assert codex_hooks(home, project)["hooks"] == []
    assert our_hooks(tmp_path, home, project) == []


@pytest.mark.skipif(not CODEX, reason="AGT_ENV_CODEX is not set")
def test_turning_hooks_off_matches_codex(tmp_path):
    home, project = codex_dirs(tmp_path)
    write_hooks(home / ".codex" / "hooks.json", {"Stop": [{"hooks": [COMMAND]}]})
    (home / ".codex" / "config.toml").write_text("[features]\nhooks = false\n")
    assert codex_hooks(home, project)["hooks"] == []
    assert [i.status for i in our_hooks(tmp_path, home, project)] == [Status.DISABLED]


# Rules from the review in docs/reviews/codex.md, each compared with the real agent.

NEEDS_CODEX = pytest.mark.skipif(not CODEX, reason="AGT_ENV_CODEX is not set")
MANIFEST = {"name": "p"}
ON = '[plugins."p@m"]\nenabled = true\n'


def scan_codex(tmp_path, home, project):
    return Codex().scan(
        Context(
            home=home,
            cwd=project,
            env={"PATH": "", "CODEX_HOME": str(home / ".codex")},
            system_root=tmp_path / "system",
        )
    )


def our_skills(tmp_path, home, project):
    report = scan_codex(tmp_path, home, project)
    return sorted(
        (item.name, item.status is Status.ENABLED)
        for item in report.items
        if item.kind is Kind.SKILL
        and item.status not in (Status.BROKEN,)
        and "/.system/" not in str(item.source_path)
        and not (item.status is Status.DISABLED and "/.system/" in str(item.source_path))
    )


def their_skills(home, project):
    return sorted(
        (skill["name"], skill["enabled"])
        for skill in codex_skills(home, project)["skills"]
        if "/.system/" not in skill["path"]
    )


def write_plugin_skill(root, name="s"):
    write_skill(root / "skills" / name)


@NEEDS_CODEX
@pytest.mark.parametrize(
    "rules",
    [
        '[[skills.config]]\nname = "a"\nenabled = false\n',
        '[[skills.config]]\nname = "front"\nenabled = false\n',
        '[[skills.config]]\nname = "folder"\nenabled = false\n',
        '[[skills.config]]\npath = "~/.agents/skills/a/SKILL.md"\nenabled = false\n',
        '[[skills.config]]\npath = "skills/c/SKILL.md"\nenabled = false\n',
        (
            '[[skills.config]]\nname = "a"\nenabled = false\n'
            '[[skills.config]]\npath = "~/.agents/skills/a/SKILL.md"\nenabled = true\n'
        ),
        (
            '[[skills.config]]\npath = "~/.agents/skills/a/SKILL.md"\nenabled = true\n'
            '[[skills.config]]\nname = "a"\nenabled = false\n'
        ),
        '[[skills.config]]\npath = "~/.agents/skills/a/SKILL.md"\nname = "a"\nenabled = false\n',
        '[[skills.config]]\nname = "p:s"\nenabled = false\n' + ON,
        '[[skills.config]]\nname = "s"\nenabled = false\n' + ON,
    ],
)
def test_skill_rules_match_codex(tmp_path, rules):
    home, project = codex_dirs(tmp_path)
    agents = home / ".agents" / "skills"
    write_skill(agents / "a")
    write_skill(agents / "folder", "name: front\ndescription: d")
    write_skill(home / ".codex" / "skills" / "c")
    write_plugin_skill(codex_plugin(home, manifest=MANIFEST))
    (home / ".codex" / "config.toml").write_text(rules)
    assert our_skills(tmp_path, home, project) == their_skills(home, project)


@NEEDS_CODEX
def test_long_names_match_codex(tmp_path):
    home, project = codex_dirs(tmp_path)
    agents = home / ".agents" / "skills"
    write_skill(agents / "ok", f"name: {'n' * 64}\ndescription: d")
    write_skill(agents / "long", f"name: {'n' * 65}\ndescription: d")
    write_skill(agents / ("x" * 65))
    assert our_skills(tmp_path, home, project) == their_skills(home, project)


@NEEDS_CODEX
def test_a_skill_linked_into_a_plugin_loads_once_like_codex(tmp_path):
    home, project = codex_dirs(tmp_path)
    root = codex_plugin(home, manifest=MANIFEST)
    write_plugin_skill(root)
    (home / ".agents" / "skills").mkdir(parents=True)
    (home / ".agents" / "skills" / "s").symlink_to(root / "skills" / "s")
    (home / ".codex" / "config.toml").write_text(ON)
    assert our_skills(tmp_path, home, project) == their_skills(home, project)


@NEEDS_CODEX
@pytest.mark.parametrize(
    ("user", "project_config"),
    [
        ('[plugins."p@m"]\n', None),
        ('[plugins._default]\nenabled = false\n[plugins."p@m"]\n', None),
        ('[plugins._default]\nenabled = false\n[plugins."p@m"]\nenabled = true\n', None),
        ('[plugins."p@m"]\n', "[plugins._default]\nenabled = false\n"),
        ('[plugins."p@m"]\nenabled = false\n', '[plugins."p@m"]\n'),
    ],
)
def test_plugin_defaults_match_codex(tmp_path, user, project_config):
    home, project = codex_dirs(tmp_path)
    write_plugin_skill(codex_plugin(home, manifest=MANIFEST))
    trust = f'[projects."{project}"]\ntrust_level = "trusted"\n'
    (home / ".codex" / "config.toml").write_text(user + trust)
    if project_config:
        (project / ".codex").mkdir()
        (project / ".codex" / "config.toml").write_text(project_config)
    assert our_skills(tmp_path, home, project) == their_skills(home, project)


@NEEDS_CODEX
@pytest.mark.parametrize(
    "names",
    [
        ["local", "10.0.0"],
        ["1.0.0-alpha.2", "1.0.0-alpha.10"],
        ["1.0.0", "1.0.0+b"],
        ["1.0.0-rc.1", "1.0.0"],
        ["1.2.3.4", "1.2.3"],
        ["01.0.0", "1.0.0"],
        ["1.0.0", "bad version!"],
    ],
)
def test_version_choice_matches_codex(tmp_path, names):
    home, project = codex_dirs(tmp_path)
    for version in names:
        label = "".join(character for character in version if character.isalnum())
        write_plugin_skill(codex_plugin(home, version, {"name": label}))
    (home / ".codex" / "config.toml").write_text(ON)
    assert our_skills(tmp_path, home, project) == their_skills(home, project)


AGENT_SCHEMA = {"$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"}


@NEEDS_CODEX
@pytest.mark.parametrize(
    "manifests",
    [
        {".claude-plugin/plugin.json": {"name": "q"}},
        {".cursor-plugin/plugin.json": {"name": "q"}},
        {"plugin.json": {"name": "q"}},
        {"plugin.json": {"name": "q", **AGENT_SCHEMA}},
        {".codex-plugin/plugin.json": {"name": "a"}, ".claude-plugin/plugin.json": {"name": "b"}},
        {".codex-plugin/plugin.json": "{bad", ".claude-plugin/plugin.json": {"name": "b"}},
        {".codex-plugin/plugin.json": "[1, 2]"},
        {".codex-plugin/plugin.json": "{bad"},
        {".codex-plugin/plugin.json": {"name": 3}},
        {".codex-plugin/plugin.json": {"name": "p", "version": 3}},
        {".codex-plugin/plugin.json": {"name": "p", "keywords": 3}},
        {".codex-plugin/plugin.json": {"name": "p", "skills": 3}},
        {".codex-plugin/plugin.json": {"name": ""}},
    ],
)
def test_manifests_match_codex(tmp_path, manifests):
    home, project = codex_dirs(tmp_path)
    root = codex_plugin(home)
    write_plugin_skill(root)
    write_skill(root / "skills" / "group" / "deep")
    for relative, manifest in manifests.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(manifest if isinstance(manifest, str) else json.dumps(manifest))
    (home / ".codex" / "config.toml").write_text(ON)
    assert our_skills(tmp_path, home, project) == their_skills(home, project)


@NEEDS_CODEX
@pytest.mark.parametrize("plugin_id", ["@m", "p@", "../x@m", "a/b@m", "noat"])
def test_invalid_plugin_ids_bring_nothing_like_codex(tmp_path, plugin_id):
    home, project = codex_dirs(tmp_path)
    write_plugin_skill(home / ".codex" / "plugins" / "cache" / "m" / "other" / "1.0.0")
    (home / ".codex" / "config.toml").write_text(f'[plugins."{plugin_id}"]\nenabled = true\n')
    assert our_skills(tmp_path, home, project) == their_skills(home, project)
    rows = scan_codex(tmp_path, home, project).items
    assert [item.status for item in rows if item.kind is Kind.PLUGIN] == [Status.BROKEN]


def our_servers(tmp_path, home, project):
    """Return the name and enabled state of what would run, as `codex mcp list` shows it."""
    report = scan_codex(tmp_path, home, project)
    return {
        item.name: item.status is Status.ENABLED
        for item in report.items
        if item.kind is Kind.MCP and item.status in (Status.ENABLED, Status.DISABLED)
    }


def their_servers(home, project):
    return {name: server["enabled"] for name, server in codex_mcp_servers(home, project).items()}


SERVER = {"command": "/bin/true"}


@NEEDS_CODEX
@pytest.mark.parametrize(
    ("files", "config"),
    [
        ({".mcp.json": {"mcpServers": {"s": SERVER}}}, ON),
        ({".mcp.json": {"s": SERVER}}, ON),
        ({"mcp.json": {"mcpServers": {"s": SERVER}}}, ON),
        ({".mcp.json": {"mcpServers": {"s": SERVER}}}, ""),
        (
            {".mcp.json": {"mcpServers": {"s": SERVER}}},
            ON + '[plugins."p@m".mcp_servers.s]\nenabled = false\n',
        ),
        (
            {".mcp.json": {"mcpServers": {"s": SERVER}}},
            ON + '[mcp_servers.s]\nurl = "https://x.test/mcp"\n',
        ),
        ({".mcp.json": {"mcpServers": {"bad": {"args": ["x"]}, "ok": SERVER}}}, ON),
        ({".mcp.json": "{bad"}, ON),
    ],
)
def test_plugin_servers_match_codex(tmp_path, files, config):
    home, project = codex_dirs(tmp_path)
    root = codex_plugin(home, manifest=MANIFEST)
    for relative, content in files.items():
        (root / relative).write_text(content if isinstance(content, str) else json.dumps(content))
    (home / ".codex" / "config.toml").write_text(config)
    assert our_servers(tmp_path, home, project) == their_servers(home, project)


@NEEDS_CODEX
def test_inline_and_path_plugin_servers_match_codex(tmp_path):
    home, project = codex_dirs(tmp_path)
    write_plugin = codex_plugin(home, manifest={**MANIFEST, "mcpServers": {"inline": SERVER}})
    assert write_plugin.is_dir()
    other = home / ".codex" / "plugins" / "cache" / "m" / "a" / "1.0.0"
    (other / ".codex-plugin").mkdir(parents=True)
    (other / ".codex-plugin" / "plugin.json").write_text(
        json.dumps({"name": "a", "mcpServers": "./x.json"})
    )
    (other / "x.json").write_text(json.dumps({"mcpServers": {"at-path": SERVER}}))
    (home / ".codex" / "config.toml").write_text(ON + '[plugins."a@m"]\nenabled = true\n')
    assert our_servers(tmp_path, home, project) == their_servers(home, project)


@NEEDS_CODEX
def test_a_duplicate_plugin_server_goes_to_the_first_plugin_like_codex(tmp_path):
    home, project = codex_dirs(tmp_path)
    for name in ("p", "a"):
        root = home / ".codex" / "plugins" / "cache" / "m" / name / "1.0.0"
        (root / ".codex-plugin").mkdir(parents=True)
        (root / ".codex-plugin" / "plugin.json").write_text(json.dumps({"name": name}))
        (root / ".mcp.json").write_text(
            json.dumps({"mcpServers": {"s": {"command": f"/bin/{name}"}}})
        )
    (home / ".codex" / "config.toml").write_text(ON + '[plugins."a@m"]\nenabled = true\n')
    theirs = codex_mcp_servers(home, project)["s"]["transport"]["command"]
    report = scan_codex(tmp_path, home, project)
    ours = [item for item in report.items if item.kind is Kind.MCP and item.name == "s"]
    assert {item.details["plugin"]: item.status for item in ours} == {
        "a@m": Status.ENABLED,
        "p@m": Status.SHADOWED,
    }
    assert theirs == "/bin/a"


@NEEDS_CODEX
@pytest.mark.parametrize(
    "config",
    [
        '[mcp_servers.bad]\nargs = ["x"]\n',
        '[mcp_servers.bad]\ncommand = "/bin/true"\nurl = "https://x.test/m"\n',
        '[mcp_servers.bad]\ncommand = "/bin/true"\nenabled = "yes"\n',
        "[mcp_servers.bad]\ncommand = 3\n",
        '[[skills.config]]\nname = "a"\n',
        '[plugins."p@m"]\nenabled = "x"\n',
        "plugins = 3\n",
        '[plugins._default]\nenabled = "x"\n',
        "agents = 3\n",
        '[skills.bundled]\nenabled = "x"\n',
        '[[hooks.Stop]]\n[[hooks.Stop.hooks]]\ntype = "mcp_tool"\nserver = "s"\n',
        '[[hooks.Stop]]\n[[hooks.Stop.hooks]]\ncommand = "x"\n',
        "not toml [\n",
    ],
)
def test_a_config_codex_rejects_gives_unknown(tmp_path, config):
    home, project = codex_dirs(tmp_path)
    write_skill(home / ".agents" / "skills" / "a")
    (home / ".codex" / "config.toml").write_text(config + '\n[mcp_servers.good]\ncommand = "x"\n')
    process = subprocess.run(
        [CODEX, "mcp", "list", "--json"],
        check=False,
        cwd=project,
        env={"PATH": os.environ["PATH"], "HOME": str(home), "CODEX_HOME": str(home / ".codex")},
        capture_output=True,
        timeout=30,
    )
    assert process.returncode != 0
    report = scan_codex(tmp_path, home, project)
    assert report.warnings
    assert {item.status for item in report.items} <= {Status.UNKNOWN, Status.BROKEN}


@NEEDS_CODEX
@pytest.mark.parametrize(
    "config",
    [
        '[plugins."p@m"]\nnope = 1\n',
        "[agents.role]\n",
        "[agents.role]\nnope = 1\n",
        '[mcp_servers.good]\ncommand = "/bin/true"\nnope = 1\n',
        '[[hooks.Nope]]\n[[hooks.Nope.hooks]]\ntype = "command"\ncommand = "x"\n',
    ],
)
def test_what_codex_accepts_isnt_rejected(tmp_path, config):
    home, project = codex_dirs(tmp_path)
    (home / ".codex" / "config.toml").write_text(config)
    assert codex_mcp_servers(home, project) is not None
    assert not scan_codex(tmp_path, home, project).warnings


@NEEDS_CODEX
@pytest.mark.parametrize(
    ("project_config", "rejected"),
    [("not toml [\n", True), ('[mcp_servers.bad]\nargs = ["x"]\n', True)],
)
def test_a_trusted_project_can_stop_codex_but_an_untrusted_one_cant(
    tmp_path, project_config, rejected
):
    home, project = codex_dirs(tmp_path)
    (project / ".codex").mkdir()
    (project / ".codex" / "config.toml").write_text(project_config)
    server = '[mcp_servers.good]\ncommand = "/bin/true"\n'
    (home / ".codex" / "config.toml").write_text(server)
    assert their_servers(home, project) == {"good": True}
    assert scan_codex(tmp_path, home, project).items[0].status is Status.ENABLED

    (home / ".codex" / "config.toml").write_text(
        server + f'[projects."{project}"]\ntrust_level = "trusted"\n'
    )
    report = scan_codex(tmp_path, home, project)
    assert {item.status for item in report.items} <= {Status.UNKNOWN, Status.BROKEN}


STATUS = {"statusMessage": "working"}
FINGERPRINT_VARIANTS = [
    {"PreToolUse": [{"hooks": [{**COMMAND, "commandWindows": "x"}]}]},
    {"PreToolUse": [{"hooks": [{**COMMAND, "additionalContextLimit": 5000}]}]},
    {"PreToolUse": [{"hooks": [{**COMMAND, "additionalContextLimit": 2500}]}]},
    {"Stop": [{"hooks": [{**COMMAND, "additionalContextLimit": 5000}]}]},
    {"PreToolUse": [{"hooks": [{"type": "mcp_tool", "server": "s", "tool": "t", **STATUS}]}]},
    {"SessionEnd": [{"hooks": [COMMAND]}]},
    {"SessionEnd": [{"hooks": [{**COMMAND, "timeout": 9999}]}]},
    {"Interrupt": [{"hooks": [COMMAND]}]},
    {"PreToolUse": [{"hooks": [{**COMMAND, "timeout": 0}]}]},
    {"Stop": [{"matcher": "x", "hooks": [COMMAND]}]},
    {"SessionEnd": [{"hooks": [{**COMMAND, "async": True}]}]},
]


@NEEDS_CODEX
@pytest.mark.parametrize("events", FINGERPRINT_VARIANTS)
def test_normalized_fingerprints_match_codex(tmp_path, events):
    from agt_env.adapters.codex import _hook_hash
    from agt_env.hooks import handlers

    home, project = codex_dirs(tmp_path)
    write_hooks(home / ".codex" / "hooks.json", events)
    theirs = [hook["currentHash"] for hook in codex_hooks(home, project)["hooks"]]
    assert theirs and [_hook_hash(handler) for handler in handlers(events)] == theirs


@NEEDS_CODEX
@pytest.mark.parametrize(
    "events",
    [
        {"Nope": [{"hooks": [COMMAND]}], "Stop": [{"hooks": [COMMAND]}]},
        {"Nope": 3, "Stop": [{"hooks": [COMMAND]}]},
        {"Stop": [{"hooks": [COMMAND, {"type": "mcp_tool", "server": "s"}]}]},
        {"Stop": [{"hooks": [COMMAND, {"command": "x"}]}]},
        {"Stop": [{"hooks": [COMMAND, {"type": "zzz"}]}]},
        {"Stop": [{"hooks": [{**COMMAND, "timeout": "x"}]}]},
        {"Stop": [{"matcher": 3, "hooks": [COMMAND]}]},
        {"Stop": {}},
        {"Stop": [3]},
        {"Stop": [{}]},
        {
            "Stop": [
                {"hooks": [{"type": "mcp_tool", "server": "s", "tool": "t", "input": {"a": None}}]}
            ]
        },
        {"Stop": [{"hooks": [COMMAND, {"type": "prompt", "prompt": "x"}]}]},
        {"Stop": [{"hooks": [COMMAND, {"type": "command", "command": "  "}]}]},
        {"Stop": [{"hooks": [COMMAND, {"type": "mcp_tool", "server": " ", "tool": "t"}]}]},
        {
            "SessionEnd": [{"hooks": [{"type": "mcp_tool", "server": "s", "tool": "t"}]}],
            "Stop": [{"hooks": [COMMAND]}],
        },
        {"PreToolUse": [{"matcher": "(", "hooks": [COMMAND]}, {"hooks": [COMMAND]}]},
    ],
)
def test_hook_files_match_codex(tmp_path, events):
    home, project = codex_dirs(tmp_path)
    write_hooks(home / ".codex" / "hooks.json", events)
    theirs = sorted(hook["key"] for hook in codex_hooks(home, project)["hooks"])
    report = scan_codex(tmp_path, home, project)
    hooks = [item for item in report.items if item.kind is Kind.HOOK]
    assert len([item for item in hooks if item.status is not Status.BROKEN]) == len(theirs)


@NEEDS_CODEX
@pytest.mark.parametrize(
    ("features", "loads"),
    [
        ("codex_hooks = false", False),
        ("hooks = false", False),
        ("hooks = true\ncodex_hooks = false", True),
        ("hooks = false\ncodex_hooks = true", False),
    ],
)
def test_the_hooks_feature_and_its_old_name_match_codex(tmp_path, features, loads):
    home, project = codex_dirs(tmp_path)
    write_hooks(home / ".codex" / "hooks.json", {"Stop": [{"hooks": [COMMAND]}]})
    (home / ".codex" / "config.toml").write_text(f"[features]\n{features}\n")
    assert bool(codex_hooks(home, project)["hooks"]) is loads
    report = scan_codex(tmp_path, home, project)
    hooks = [item for item in report.items if item.kind is Kind.HOOK]
    assert [item.status is not Status.DISABLED for item in hooks] == [loads]


@NEEDS_CODEX
def test_inline_and_listed_plugin_hooks_match_codex(tmp_path):
    home, project = codex_dirs(tmp_path)
    hook = {"hooks": {"Stop": [{"hooks": [COMMAND]}]}}
    inline = codex_plugin(home, manifest={**MANIFEST, "hooks": hook})
    assert inline.is_dir()
    listed = home / ".codex" / "plugins" / "cache" / "m" / "a" / "1.0.0"
    (listed / ".codex-plugin").mkdir(parents=True)
    (listed / ".codex-plugin" / "plugin.json").write_text(
        json.dumps({"name": "a", "hooks": ["./one.json", "./two.json"]})
    )
    (listed / "one.json").write_text(json.dumps(hook))
    (listed / "two.json").write_text(json.dumps(hook))
    (home / ".codex" / "config.toml").write_text(ON + '[plugins."a@m"]\nenabled = true\n')
    theirs = codex_hooks(home, project)["hooks"]
    report = scan_codex(tmp_path, home, project)
    assert len([item for item in report.items if item.kind is Kind.HOOK]) == len(theirs) == 3


@NEEDS_CODEX
def test_an_agent_plugin_has_no_hooks_and_only_direct_skills_like_codex(tmp_path):
    home, project = codex_dirs(tmp_path)
    root = codex_plugin(home)
    (root / "plugin.json").write_text(json.dumps({"name": "q", **AGENT_SCHEMA}))
    write_plugin_skill(root, "top")
    write_skill(root / "skills" / "group" / "deep")
    write_hooks(root / "hooks" / "hooks.json", {"Stop": [{"hooks": [COMMAND]}]})
    (home / ".codex" / "config.toml").write_text(ON)
    assert codex_hooks(home, project)["hooks"] == []
    report = scan_codex(tmp_path, home, project)
    assert not [item for item in report.items if item.kind is Kind.HOOK]
    assert our_skills(tmp_path, home, project) == their_skills(home, project)


def thread_warnings(home, project):
    """Start a thread, which reports the agent roles Codex ignores. No model is called."""
    process = subprocess.Popen(
        [CODEX, "app-server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        env={"PATH": os.environ["PATH"], "HOME": str(home), "CODEX_HOME": str(home / ".codex")},
        cwd=project,
    )

    def send(message):
        process.stdin.write(json.dumps(message) + "\n")
        process.stdin.flush()

    summaries = []
    try:
        send(
            {
                "id": 1,
                "method": "initialize",
                "params": {"clientInfo": {"name": "t", "version": "0"}},
            }
        )
        send({"method": "initialized", "params": {}})
        send({"id": 2, "method": "thread/start", "params": {"cwd": str(project)}})
        for line in process.stdout:
            message = json.loads(line)
            if message.get("method") == "configWarning":
                summaries.append(message["params"]["summary"])
            if message.get("id") == 2:
                return summaries
    finally:
        process.kill()
        process.wait()
    raise AssertionError("codex app-server exited early")


@NEEDS_CODEX
def test_refused_agent_roles_match_codex(tmp_path):
    home, project = codex_dirs(tmp_path)
    agents = home / ".codex" / "agents"
    good = 'description = "d"\ndeveloper_instructions = "x"\n'
    agents.mkdir(parents=True)
    (agents / "ok.toml").write_text(f'name = "ok"\n{good}')
    (agents / "blank-name.toml").write_text(f'name = ""\n{good}')
    (agents / "blank-text.toml").write_text(
        'name = "b"\ndescription = " "\ndeveloper_instructions = "x"\n'
    )
    (agents / "blank-rules.toml").write_text(
        'name = "c"\ndescription = "d"\ndeveloper_instructions = " "\n'
    )
    (agents / "no-rules.toml").write_text('name = "e"\ndescription = "d"\n')
    (agents / "bad.toml").write_text("not toml [")
    (agents / "z-dup.toml").write_text(f'name = "ok"\n{good}')
    (agents / "sub").mkdir()
    (agents / "sub" / "deep.toml").write_text('name = "deep"\ndeveloper_instructions = "x"\n')
    (agents / "sub" / "fine.toml").write_text(f'name = "fine"\n{good}')
    (home / ".codex" / "config.toml").write_text(
        '[agents.inline]\ndescription = "d"\n[agents.nodesc]\n[agents.missing]\nconfig_file = "./nofile.toml"\n'
    )
    theirs = thread_warnings(home, project)
    report = scan_codex(tmp_path, home, project)
    rows = [item for item in report.items if item.kind is Kind.SUBAGENT]
    # Each refused role is one warning from Codex and one broken row here.
    assert len(theirs) == len([item for item in rows if item.status is Status.BROKEN]) == 9
    assert {item.name for item in rows if item.status is Status.ENABLED} == {
        "ok",
        "fine",
        "inline",
    }


def _private_etc(tmp_path, command):
    """Wrap a command so it sees the fixture at /etc/codex. The real /etc is never touched."""
    upper, work = tmp_path / "etc-upper", tmp_path / "etc-work"
    number = len(list(tmp_path.glob("etc-upper*")))
    upper, work = upper.with_name(f"etc-upper{number}"), work.with_name(f"etc-work{number}")
    upper.mkdir()
    work.mkdir()
    source = tmp_path / "system" / "etc" / "codex"
    return [
        "unshare",
        "-Urm",
        "sh",
        "-c",
        (
            f"mount -t overlay overlay -o lowerdir=/etc,upperdir={upper},workdir={work},userxattr"
            f" /etc && rm -rf /etc/codex && cp -a {shlex.quote(str(source))} /etc/codex"
            f" && exec {shlex.join(command)}"
        ),
    ]


def can_use_private_etc():
    if not CODEX or shutil.which("unshare") is None:
        return False
    probe = subprocess.run(
        ["unshare", "-Urm", "true"], check=False, capture_output=True, timeout=10
    )
    return probe.returncode == 0


NEEDS_PRIVATE_ETC = pytest.mark.skipif(
    not can_use_private_etc(), reason="needs AGT_ENV_CODEX and a Linux mount namespace"
)


def system_servers(tmp_path, home, project):
    process = subprocess.run(
        _private_etc(tmp_path, [CODEX, "mcp", "list", "--json"]),
        check=False,
        cwd=project,
        env={"PATH": os.environ["PATH"], "HOME": str(home), "CODEX_HOME": str(home / ".codex")},
        capture_output=True,
        text=True,
        timeout=30,
    )
    if process.returncode:
        return None
    return {server["name"]: server["enabled"] for server in json.loads(process.stdout)}


def write_system(tmp_path, files):
    root = tmp_path / "system" / "etc" / "codex"
    root.mkdir(parents=True, exist_ok=True)
    for relative, content in files.items():
        (root / relative).write_text(content)


@NEEDS_PRIVATE_ETC
@pytest.mark.parametrize(
    ("system", "user"),
    [
        ('[mcp_servers.sys]\ncommand = "/bin/true"\n', '[mcp_servers.u]\ncommand = "/bin/true"\n'),
        ('[mcp_servers.s]\ncommand = "/bin/true"\n', "[mcp_servers.s]\nenabled = false\n"),
    ],
)
def test_system_config_matches_codex(tmp_path, system, user):
    home, project = codex_dirs(tmp_path)
    write_system(tmp_path, {"config.toml": system})
    (home / ".codex" / "config.toml").write_text(user)
    assert our_servers(tmp_path, home, project) == system_servers(tmp_path, home, project)


@NEEDS_PRIVATE_ETC
@pytest.mark.parametrize(
    "files",
    [
        {"config.toml": "bad ["},
        {"config.toml": '[mcp_servers.bad]\nargs = ["x"]\n'},
        {"requirements.toml": "bad ["},
    ],
)
def test_system_files_codex_rejects_give_unknown(tmp_path, files):
    home, project = codex_dirs(tmp_path)
    write_system(tmp_path, files)
    (home / ".codex" / "config.toml").write_text('[mcp_servers.u]\ncommand = "/bin/true"\n')
    assert system_servers(tmp_path, home, project) is None
    report = scan_codex(tmp_path, home, project)
    assert report.warnings
    assert {item.status for item in report.items} <= {Status.UNKNOWN, Status.BROKEN}


def system_hooks(tmp_path, home, project):
    process = subprocess.Popen(
        _private_etc(tmp_path, [CODEX, "app-server"]),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        env={"PATH": os.environ["PATH"], "HOME": str(home), "CODEX_HOME": str(home / ".codex")},
        cwd=project,
    )

    def send(message):
        process.stdin.write(json.dumps(message) + "\n")
        process.stdin.flush()

    try:
        send(
            {
                "id": 1,
                "method": "initialize",
                "params": {"clientInfo": {"name": "t", "version": "0"}},
            }
        )
        send({"method": "initialized", "params": {}})
        send({"id": 2, "method": "hooks/list", "params": {"cwds": [str(project)]}})
        for line in process.stdout:
            message = json.loads(line)
            if message.get("id") == 2:
                return message["result"]["data"][0]["hooks"]
    finally:
        process.kill()
        process.wait()
    raise AssertionError("codex app-server exited early")


TOML_HOOK = '[[hooks.PreToolUse]]\n[[hooks.PreToolUse.hooks]]\ntype = "command"\ncommand = "x"\n'
REQUIRED_HOOK = '[[hooks.Stop]]\n[[hooks.Stop.hooks]]\ntype = "command"\ncommand = "/bin/true"\n'


@NEEDS_PRIVATE_ETC
@pytest.mark.parametrize(
    "files",
    [
        {"config.toml": TOML_HOOK},
        {"hooks.json": json.dumps({"hooks": {"Stop": [{"hooks": [COMMAND]}]}})},
        {"requirements.toml": REQUIRED_HOOK},
        {"requirements.toml": "allow_managed_hooks_only = true\n", "config.toml": TOML_HOOK},
        {"requirements.toml": "allow_managed_hooks_only = true\n" + REQUIRED_HOOK},
    ],
)
def test_managed_hooks_match_codex(tmp_path, files):
    home, project = codex_dirs(tmp_path)
    write_system(tmp_path, files)
    write_hooks(home / ".codex" / "hooks.json", {"SessionStart": [{"hooks": [COMMAND]}]})
    theirs = system_hooks(tmp_path, home, project)
    report = scan_codex(tmp_path, home, project)
    hooks = [item for item in report.items if item.kind is Kind.HOOK]
    assert sorted(hook["isManaged"] for hook in theirs) == sorted(
        item.scope is Scope.SYSTEM for item in hooks if item.status is not Status.DISABLED
    )
    assert {item.status for item in hooks if item.scope is Scope.SYSTEM} <= {Status.ENABLED}
