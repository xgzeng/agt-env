"""Compare results with Pi's own RPC mode and `pi mcp list` on the same fixture.

Runs only when AGT_VIEW_PI points at a real Pi binary, and never touches the developer's
configuration: HOME is temporary. Fixture extensions only register a command, and fixture
servers only run /bin/true.
"""

import json
import os
import select
import subprocess
import time

import pytest
from conftest import write_skill

from agt_view.adapters.base import Context
from agt_view.adapters.pi import Pi
from agt_view.model import Kind, Scope, Status

PI = os.environ.get("AGT_VIEW_PI")

pytestmark = pytest.mark.skipif(not PI, reason="AGT_VIEW_PI is not set")


def extension(name):
    return (
        "export default function (pi) {\n"
        f"  pi.registerCommand('{name}', {{description: 'd', handler: async () => {{}}}});\n"
        "}\n"
    )


def write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content if isinstance(content, str) else json.dumps(content))


def pi_env(home):
    return {"HOME": str(home), "PATH": os.environ["PATH"], "TERM": "dumb"}


def their_commands(home, project):
    process = subprocess.Popen(
        [PI, "--mode", "rpc", "--no-session"],
        cwd=project,
        env=pi_env(home),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    try:
        process.stdin.write(json.dumps({"id": "1", "type": "get_commands"}) + "\n")
        process.stdin.flush()
        deadline = time.time() + 60
        while time.time() < deadline:
            if select.select([process.stdout], [], [], 1)[0]:
                line = process.stdout.readline()
                if not line:
                    break
                try:
                    message = json.loads(line)
                except ValueError:
                    continue
                if message.get("id") == "1":
                    return message["data"]["commands"]
        raise AssertionError("no answer from pi")
    finally:
        process.kill()
        process.wait()


def scan(tmp_path, home, project):
    context = Context(home=home, cwd=project, env={"PATH": ""}, system_root=tmp_path / "system")
    return Pi().scan(context).items


@pytest.fixture
def machine_dirs(tmp_path):
    home, project = tmp_path / "home", tmp_path / "project"
    agent = home / ".pi" / "agent"
    agent.mkdir(parents=True)
    (project / ".git").mkdir(parents=True)
    return home, project, agent


def build_fixture(home, project, agent):
    skills = agent / "skills"
    write_skill(skills / "own", "name: own\ndescription: d")
    write_skill(skills / "group" / "deep" / "nested", "name: nested\ndescription: d")
    write_skill(skills / "no-name", "description: d")
    write_skill(skills / "dir-name", "name: fm-name\ndescription: d")
    write_skill(skills / "no-desc", "name: no-desc")
    write_skill(skills / "excluded", "name: excluded\ndescription: d")
    write(skills / "no-fm" / "SKILL.md", "no frontmatter")
    write(skills / "standalone.md", "---\nname: standalone\ndescription: d\n---\n")
    write_skill(home / ".agents" / "skills" / "shared", "name: shared\ndescription: d")
    write_skill(project / ".pi" / "skills" / "repo", "name: repo\ndescription: d")
    write_skill(project / ".agents" / "skills" / "repo-agents", "name: repo-agents\ndescription: d")
    extensions = agent / "extensions"
    write(extensions / "file.ts", extension("ext-file"))
    write(extensions / "indexed" / "index.ts", extension("ext-indexed"))
    write(extensions / "packaged" / "package.json", {"pi": {"extensions": ["./a.ts"]}})
    write(extensions / "packaged" / "a.ts", extension("ext-packaged"))
    write(extensions / "legacy.ts", extension("ext-legacy"))
    write(extensions / "not-an-extension.md", "x")
    write(project / ".pi" / "extensions" / "repo.ts", extension("ext-repo"))


TRUST_CASES = {
    "no-decision": ({}, None, Status.PENDING),
    "default-always": ({}, "always", Status.ENABLED),
    "default-never": ({}, "never", Status.DISABLED),
    "saved-yes": (True, "never", Status.ENABLED),
    "saved-no": (False, "always", Status.DISABLED),
}


@pytest.mark.parametrize("case", TRUST_CASES)
def test_skills_and_extensions_match(machine_dirs, tmp_path, case):
    home, project, agent = machine_dirs
    build_fixture(home, project, agent)
    saved, default, expected = TRUST_CASES[case]
    settings = {"skills": ["!excl*"], "extensions": ["!legacy*"]}
    if default:
        settings["defaultProjectTrust"] = default
    write(agent / "settings.json", settings)
    if saved != {}:
        write(agent / "trust.json", {str(project.resolve()): saved})

    commands = their_commands(home, project)
    their_skills = {c["name"].removeprefix("skill:") for c in commands if c["source"] == "skill"}
    their_extensions = {
        c["name"].removeprefix("ext-")
        for c in commands
        if c["source"] == "extension" and c["name"].startswith("ext-")
    }

    items = scan(tmp_path, home, project)
    loaded = {Status.ENABLED}
    our_skills = {i.name for i in items if i.kind is Kind.SKILL and i.status in loaded}
    our_extensions = {i.name for i in items if i.kind is Kind.PLUGIN and i.status in loaded}
    assert our_skills == their_skills
    assert our_extensions == their_extensions

    project_items = [i for i in items if i.scope is Scope.PROJECT]
    assert {i.status for i in project_items} == {expected}


def test_servers_match(machine_dirs, tmp_path):
    home, project, agent = machine_dirs
    write(
        agent / "mcp.json",
        {
            "mcpServers": {
                "on": {"command": "/bin/true"},
                "remote": {"url": "http://127.0.0.1:9/m"},
                "off": {"command": "/bin/true", "enabled": False},
                "ignored-flag": {"command": "/bin/true", "disabled": True},
            }
        },
    )
    output = subprocess.run(
        [PI, "mcp", "list"],
        check=False,
        cwd=project,
        env=pi_env(home),
        capture_output=True,
        text=True,
        timeout=90,
    ).stdout
    theirs = {}
    for line in output.splitlines():
        if line and not line.startswith(" ") and ": " in line:
            name, state = line.split(": ", 1)
            theirs[name] = not state.startswith("disabled")
    ours = {
        item.name: item.status is Status.ENABLED
        for item in scan(tmp_path, home, project)
        if item.kind is Kind.MCP
    }
    assert ours == theirs


def test_invalid_server_is_reported_as_config_error(machine_dirs, tmp_path):
    home, project, agent = machine_dirs
    write(agent / "mcp.json", {"mcpServers": {"bad": {"args": []}}})
    result = subprocess.run(
        [PI, "mcp", "list"],
        check=False,
        cwd=project,
        env=pi_env(home),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert 'server "bad"' in result.stdout + result.stderr
    assert [i.status for i in scan(tmp_path, home, project) if i.name == "bad"] == [Status.BROKEN]
