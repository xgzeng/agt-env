"""Compare results with Pi's own RPC mode and `pi mcp list` on the same fixture.

Runs only when AGT_VIEW_PI points at a real Pi binary, and never touches the developer's
configuration: HOME is temporary. Fixture extensions only register a command, and fixture
servers only run /bin/true.
"""

import itertools
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


def test_skill_precedence_matches(machine_dirs, tmp_path):
    home, project, agent = machine_dirs
    sources = {
        "project-list": tmp_path / "project-list",
        "pi": project / ".pi" / "skills",
        "project-agents": project / ".agents" / "skills",
        "user-list": tmp_path / "user-list",
        "agent": agent / "skills",
        "user-agents": home / ".agents" / "skills",
    }
    for a, b in itertools.combinations(sources, 2):
        for source in (a, b):
            write_skill(sources[source] / f"{a}-{b}", f"name: {a}-{b}\ndescription: {source}")
    # Matched by name, not directory.
    write_skill(sources["agent"] / "one", "name: same\ndescription: d")
    write_skill(sources["pi"] / "two", "name: same\ndescription: d")
    # Within one source.
    write_skill(sources["agent"] / "first", "name: same-source\ndescription: d")
    write_skill(sources["agent"] / "second", "name: same-source\ndescription: d")
    # Neither an invalid skill nor an excluded one takes the name.
    write_skill(sources["agent"] / "invalid", "name: invalid\ndescription: d")
    write_skill(sources["pi"] / "invalid", "name: invalid")
    write_skill(sources["user-agents"] / "lower", "name: excluded\ndescription: d")
    write_skill(sources["agent"] / "gone", "name: excluded\ndescription: d")
    write(
        agent / "settings.json",
        {
            "defaultProjectTrust": "always",
            "skills": [str(sources["user-list"]), "!**/.pi/agent/skills/gone/**"],
        },
    )
    write(project / ".pi" / "settings.json", {"skills": [str(sources["project-list"])]})
    write(agent / "trust.json", {str(project.resolve()): True})

    theirs = {
        (command["name"].removeprefix("skill:"), command["sourceInfo"]["path"])
        for command in their_commands(home, project)
        if command["source"] == "skill"
    }
    ours = {
        (item.name, str(item.source_path / "SKILL.md"))
        for item in scan(tmp_path, home, project)
        if item.kind is Kind.SKILL and item.status is Status.ENABLED
    }
    assert ours == theirs


def test_server_precedence_matches(machine_dirs, tmp_path):
    home, project, agent = machine_dirs
    write(
        agent / "mcp.json",
        {
            "mcpServers": {
                "both": {"command": "/bin/true"},
                "off-in-user": {"command": "/bin/true", "enabled": False},
                "off-in-project": {"command": "/bin/true"},
                "user-only": {"command": "/bin/true"},
            }
        },
    )
    write(
        project / ".pi" / "mcp.json",
        {
            "mcpServers": {
                "both": {"command": "/bin/false"},
                "off-in-user": {"command": "/bin/false"},
                "off-in-project": {"command": "/bin/false", "enabled": False},
            }
        },
    )
    write(agent / "trust.json", {str(project.resolve()): True})
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
    lines = output.splitlines()
    for line, following in itertools.pairwise(lines):
        if line and not line.startswith(" ") and ": " in line:
            name, state = line.split(": ", 1)
            theirs[name] = (not state.startswith("disabled"), following.strip())
    ours = {
        item.name: (item.status is Status.ENABLED, item.details["command"])
        for item in scan(tmp_path, home, project)
        if item.kind is Kind.MCP and item.status is not Status.SHADOWED
    }
    assert ours == theirs


@pytest.mark.parametrize("layer", ["user", "project"])
def test_exclusions_apply_only_to_their_own_scope(machine_dirs, tmp_path, layer):
    home, project, agent = machine_dirs
    for label, root in {
        "agent": agent,
        "user-agents": home / ".agents",
        "pi": project / ".pi",
        "project-agents": project / ".agents",
    }.items():
        write_skill(root / "skills" / "victim", f"name: skill-{label}\ndescription: d")
    write(agent / "extensions" / "victim.ts", extension("ext-user"))
    write(project / ".pi" / "extensions" / "victim.ts", extension("ext-project"))
    patterns = {"skills": ["!victim"], "extensions": ["!victim*"]}
    write(agent / "trust.json", {str(project.resolve()): True})
    write(agent / "settings.json", patterns if layer == "user" else {})
    if layer == "project":
        write(project / ".pi" / "settings.json", patterns)

    commands = their_commands(home, project)
    theirs_skills = {c["name"].removeprefix("skill:") for c in commands if c["source"] == "skill"}
    theirs_extensions = {
        c["name"].removeprefix("ext-")
        for c in commands
        if c["source"] == "extension" and c["name"].startswith("ext-")
    }
    items = scan(tmp_path, home, project)
    ours_skills = {i.name for i in items if i.kind is Kind.SKILL and i.status is Status.ENABLED}
    assert ours_skills == theirs_skills
    ours_extensions = {
        i.scope.value for i in items if i.kind is Kind.PLUGIN and i.status is Status.ENABLED
    }
    assert ours_extensions == theirs_extensions
