"""Compare results with Pi's own RPC mode and `pi mcp list` on the same fixture.

Runs only when AGT_ENV_PI points at a real Pi binary, and never touches the developer's
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

from agt_env.adapters.base import Context
from agt_env.adapters.pi import Pi
from agt_env.model import Kind, Scope, Status

PI = os.environ.get("AGT_ENV_PI")

pytestmark = pytest.mark.skipif(not PI, reason="AGT_ENV_PI is not set")


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


def package(home, name="pk", pi=None, **skills):
    root = home / name
    root.mkdir(parents=True, exist_ok=True)
    manifest = {"name": name}
    if pi is not None:
        manifest["pi"] = pi
    write(root / "package.json", manifest)
    for directory, skill_name in skills.items():
        write_skill(root / directory.replace("__", "/"), f"name: {skill_name}\ndescription: d")
    return root


def conventional(home, agent, project):
    return {"packages": [str(package(home, skills__a="a", skills__x__y__z="deep"))]}


def declared_paths(home, agent, project):
    root = package(home, pi={"skills": ["./res"]}, res__a="a", skills__b="b")
    return {"packages": [str(root)]}


def declared_globs(home, agent, project):
    root = package(home, pi={"skills": ["./res/*", "!**/b/**"]}, res__a="a", res__b="b", res__c="c")
    return {"packages": [str(root)]}


def literal_exclusion_does_nothing(home, agent, project):
    root = package(home, pi={"skills": ["./res/*", "!./res/b"]}, res__a="a", res__b="b")
    return {"packages": [str(root)]}


def manifest_without_skills(home, agent, project):
    root = package(home, pi={"extensions": ["./e.ts"]}, skills__a="a")
    return {"packages": [str(root)]}


def empty_manifest(home, agent, project):
    return {"packages": [str(package(home, pi={}, skills__a="a"))]}


def no_package_json(home, agent, project):
    write_skill(home / "bare" / "skills" / "a", "name: a\ndescription: d")
    return {"packages": [str(home / "bare")]}


def standalone(home, agent, project):
    root = package(home)
    write(root / "skills" / "solo.md", "---\nname: solo\ndescription: d\n---\nBody\n")
    return {"packages": [str(root)]}


def filtered(*chosen):
    def build(home, agent, project):
        root = package(home, skills__a="a", skills__b="b")
        return {"packages": [{"source": str(root), "skills": list(chosen)}]}

    return build


def relative_source(home, agent, project):
    package(agent, "rel", skills__a="a")
    return {"packages": ["./rel"]}


def installed(relative, source):
    def build(home, agent, project):
        write_skill(agent / relative / "skills" / "a", "name: a\ndescription: d")
        write(agent / relative / "package.json", {"name": "x", "version": "1.0.0"})
        return {"packages": [source]}

    return build


def setting_exclusion_is_ignored(home, agent, project):
    return {"packages": [str(package(home, skills__a="a"))], "skills": ["!**/a/**"]}


def clash(where):
    def build(home, agent, project):
        write_skill(where(home, agent, project) / "a", "name: a\ndescription: d")
        settings = {"packages": [str(package(home, skills__a="a"))]}
        if where is listed_dir:
            settings["skills"] = [str(home / "listed")]
        return settings

    return build


def listed_dir(home, agent, project):
    return home / "listed"


def two_packages(home, agent, project):
    first, second = package(home, "one", skills__a="a"), package(home, "two", skills__a="a")
    return {"packages": [str(first), str(second)]}


def same_package_twice_named(home, agent, project):
    root = package(home, skills__x__a="a", skills__y__a="a")
    return {"packages": [str(root)]}


def project_package(home, agent, project):
    root = package(home, skills__a="a")
    write(project / ".pi" / "settings.json", {"packages": [str(root)]})
    return {"defaultProjectTrust": "always"}


def project_package_untrusted(home, agent, project):
    root = package(home, skills__a="a")
    write(project / ".pi" / "settings.json", {"packages": [str(root)]})
    return {}


def project_replaces_user_entry(home, agent, project):
    root = package(home, skills__a="a", skills__b="b")
    write(
        project / ".pi" / "settings.json",
        {"packages": [{"source": str(root), "skills": ["skills/b"]}]},
    )
    return {"packages": [str(root)], "defaultProjectTrust": "always"}


def untrusted_project_does_not_replace(home, agent, project):
    root = package(home, skills__a="a")
    write(project / ".pi" / "settings.json", {"packages": [{"source": str(root), "skills": []}]})
    return {"packages": [str(root)]}


def project_beats_user_package(home, agent, project):
    user, ours = package(home, "up", skills__a="a"), package(home, "pp", skills__a="a")
    write(project / ".pi" / "settings.json", {"packages": [str(ours)]})
    return {"packages": [str(user)], "defaultProjectTrust": "always"}


def project_managed(relative, source):
    def build(home, agent, project):
        write_skill(project / ".pi" / relative / "skills" / "a", "name: a\ndescription: d")
        write(project / ".pi" / relative / "package.json", {"name": "x", "version": "1.0.0"})
        write(project / ".pi" / "settings.json", {"packages": [source]})
        return {"defaultProjectTrust": "always"}

    return build


GIT_CHECKOUT = "git/github.com/acme/tools"


@pytest.mark.parametrize(
    "build",
    [
        conventional,
        declared_paths,
        declared_globs,
        literal_exclusion_does_nothing,
        manifest_without_skills,
        empty_manifest,
        no_package_json,
        standalone,
        filtered(),
        filtered("!**/a/**"),
        filtered("skills/b"),
        filtered("skills/*"),
        relative_source,
        setting_exclusion_is_ignored,
        two_packages,
        same_package_twice_named,
        project_package,
        project_package_untrusted,
        project_replaces_user_entry,
        untrusted_project_does_not_replace,
        project_beats_user_package,
        clash(lambda home, agent, project: agent / "skills"),
        clash(lambda home, agent, project: project / ".pi" / "skills"),
        clash(lambda home, agent, project: home / ".agents" / "skills"),
        clash(listed_dir),
        installed(GIT_CHECKOUT, "git:github.com/acme/tools"),
        installed(GIT_CHECKOUT, "git:github.com/acme/tools@v1"),
        installed(GIT_CHECKOUT, "https://github.com/acme/tools"),
        installed(GIT_CHECKOUT, "https://github.com/acme/tools.git"),
        installed(GIT_CHECKOUT, "ssh://git@github.com/acme/tools"),
        installed("npm/node_modules/acme", "npm:acme"),
        installed("npm/node_modules/@acme/tools", "npm:@acme/tools@1.0.0"),
        project_managed(GIT_CHECKOUT, "git:github.com/acme/tools"),
        project_managed("npm/node_modules/acme", "npm:acme"),
    ],
)
def test_package_skills_match(machine_dirs, tmp_path, build):
    home, project = machine_dirs[:2]
    agent = home / ".pi" / "agent"
    agent.mkdir(parents=True, exist_ok=True)
    write(agent / "settings.json", build(home, agent, project))

    theirs = {
        os.path.realpath(command["path"] if "path" in command else command["sourceInfo"]["path"])
        for command in their_commands(home, project)
        if command["name"].startswith("skill:")
    }
    ours = set()
    for item in scan(tmp_path, home, project):
        if item.kind is Kind.SKILL and item.status is Status.ENABLED:
            standalone_file = item.source_path.suffix == ".md"
            file = item.source_path if standalone_file else item.source_path / "SKILL.md"
            ours.add(os.path.realpath(file))
    assert ours == theirs


def prompt_file(description="d"):
    return f"---\ndescription: {description}\n---\nBody\n"


def prompts_everywhere(home, agent, project):
    write(agent / "prompts" / "user.md", prompt_file())
    write(agent / "prompts" / "plain.md", "First line\n")
    write(agent / "prompts" / "folder" / "nested.md", prompt_file())
    write(agent / "prompts" / ".hidden.md", prompt_file())
    write(agent / "prompts" / "text.txt", prompt_file())
    write(project / ".pi" / "prompts" / "here.md", prompt_file())
    write(home / "extra" / "listed.md", prompt_file())
    write(home / "extra" / "deep" / "listed-deep.md", prompt_file())
    write(home / "one.md", prompt_file())
    return {
        "prompts": [str(home / "extra"), str(home / "one.md")],
        "defaultProjectTrust": "always",
    }


def prompts_untrusted(home, agent, project):
    write(project / ".pi" / "prompts" / "here.md", prompt_file())
    return {}


def prompt_clash(*places):
    def build(home, agent, project):
        settings = {"defaultProjectTrust": "always"}
        for place in places:
            if place == "user":
                write(agent / "prompts" / "same.md", prompt_file())
            elif place == "project":
                write(project / ".pi" / "prompts" / "same.md", prompt_file())
            elif place == "listed":
                write(home / "listed" / "same.md", prompt_file())
                settings["prompts"] = [str(home / "listed")]
            elif place == "project-listed":
                write(home / "plisted" / "same.md", prompt_file())
                write(project / ".pi" / "settings.json", {"prompts": [str(home / "plisted")]})
            elif place == "package":
                root = package(home)
                write(root / "prompts" / "same.md", prompt_file())
                settings.setdefault("packages", []).append(str(root))
            elif place == "second-package":
                root = package(home, "second")
                write(root / "prompts" / "same.md", prompt_file())
                settings.setdefault("packages", []).append(str(root))
        return settings

    return build


def prompt_excluded(home, agent, project):
    write(agent / "prompts" / "kept.md", prompt_file())
    write(agent / "prompts" / "gone.md", prompt_file())
    return {"prompts": ["!**/gone.md"]}


def package_prompts(pi=None, chosen=None):
    def build(home, agent, project):
        root = package(home, pi=pi)
        write(root / "prompts" / "a.md", prompt_file())
        write(root / "prompts" / "deep" / "b.md", prompt_file())
        write(root / "res" / "c.md", prompt_file())
        entry = {"source": str(root)}
        if chosen is not None:
            entry["prompts"] = chosen
        return {"packages": [entry]}

    return build


@pytest.mark.parametrize(
    "build",
    [
        prompts_everywhere,
        prompts_untrusted,
        prompt_excluded,
        prompt_clash("user", "project"),
        prompt_clash("user", "listed"),
        prompt_clash("project", "project-listed"),
        prompt_clash("listed", "project"),
        prompt_clash("user", "package"),
        prompt_clash("listed", "package"),
        prompt_clash("project", "package"),
        prompt_clash("package", "second-package"),
        package_prompts(),
        package_prompts(pi={"prompts": ["./res/*.md"]}),
        package_prompts(chosen=["prompts/a.md"]),
        package_prompts(chosen=[]),
    ],
)
def test_prompts_match(machine_dirs, tmp_path, build):
    home, project = machine_dirs[:2]
    agent = home / ".pi" / "agent"
    write(agent / "settings.json", build(home, agent, project))
    theirs = {
        os.path.realpath(command["sourceInfo"]["path"])
        for command in their_commands(home, project)
        if command["source"] == "prompt"
    }
    ours = {
        os.path.realpath(item.source_path)
        for item in scan(tmp_path, home, project)
        if item.kind is Kind.COMMAND and item.status is Status.ENABLED
    }
    assert ours == theirs
