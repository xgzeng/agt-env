"""Compare discovery with Codex's own listing on the same fixture.

Runs only when AGT_VIEW_CODEX points at a real Codex binary, and never touches
the developer's configuration: HOME and CODEX_HOME are temporary.
"""

import json
import os
import subprocess

import pytest
from conftest import write_skill

from agt_view.adapters.base import Context
from agt_view.adapters.codex import Codex
from agt_view.model import Kind, Scope, Status

CODEX = os.environ.get("AGT_VIEW_CODEX")


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


@pytest.mark.skipif(not CODEX, reason="AGT_VIEW_CODEX is not set")
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


@pytest.mark.skipif(not CODEX, reason="AGT_VIEW_CODEX is not set")
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


@pytest.mark.skipif(not CODEX, reason="AGT_VIEW_CODEX is not set")
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


@pytest.mark.skipif(not CODEX, reason="AGT_VIEW_CODEX is not set")
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


@pytest.mark.skipif(not CODEX, reason="AGT_VIEW_CODEX is not set")
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


@pytest.mark.skipif(not CODEX, reason="AGT_VIEW_CODEX is not set")
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
