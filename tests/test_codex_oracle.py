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
        send({"id": 1, "method": "initialize", "params": {"clientInfo": {"name": "t", "version": "0"}}})
        response(1)
        send({"method": "initialized", "params": {}})
        send({"id": 2, "method": "skills/list", "params": {"cwds": [str(cwd)], "forceReload": True}})
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
