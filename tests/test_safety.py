"""FR-11 and FR-12, checked from outside the code: Python's audit hook reports what a run opens."""

import hashlib
import json
import re

import pytest
from conftest import run_on_terminal, run_piped, write_skill

from agt_env.cli import main

SECRET = "hunter2-do-not-print"
# The usual locations. See docs/agents/README.md for which of these were confirmed.
CREDENTIAL_STORES = [
    ".claude/.credentials.json",
    ".codex/auth.json",
    ".local/share/opencode/auth.json",
    ".config/opencode/auth.json",
    ".pi/agent/auth.json",
    ".gemini/oauth_creds.json",
    ".gemini/google_accounts.json",
    ".gemini/antigravity-cli/oauth_creds.json",
]
CREDENTIAL_NAME = re.compile(r"auth|cred|oauth|token|secret|account|\.env", re.IGNORECASE)
CONFIG_READ = [
    ".claude.json",
    ".claude/skills/from-claude/SKILL.md",
    ".codex/config.toml",
    ".codex/skills/from-codex/SKILL.md",
    ".config/opencode/opencode.json",
    ".config/opencode/skills/from-opencode/SKILL.md",
    ".pi/agent/settings.json",
    ".pi/agent/skills/from-pi/SKILL.md",
    ".gemini/config/mcp_config.json",
    ".gemini/antigravity-cli/skills/from-agy/SKILL.md",
]
# Runs the command, recording every file opened and every change to the file system.
AUDITED = """
import json, os, sys

CHANGES = {
    "os.chmod", "os.chown", "os.link", "os.mkdir", "os.remove", "os.rename", "os.rmdir",
    "os.symlink", "os.truncate", "os.utime", "shutil.copyfile", "shutil.move", "shutil.rmtree",
}
WRITING = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC
opened, changed = [], []

def hook(event, args):
    if event == "open" and isinstance(args[0], (str, bytes)):
        path = os.fsdecode(args[0])
        opened.append(path)
        if isinstance(args[2], int) and args[2] & WRITING:
            changed.append(["open", path])
    elif event in CHANGES:
        changed.append([event, repr(args)])

sys.addaudithook(hook)

from agt_env.cli import main

code = main()
log = {"opened": list(opened), "changed": list(changed)}
with open(os.environ["AUDIT_LOG"], "w") as file:
    json.dump(log, file)
raise SystemExit(code)
"""
RUNNERS = {"piped": run_piped, "terminal": run_on_terminal}
ARGUMENTS = [(), ("--all", "--detail"), (SECRET,), ("-n", "from", "--detail")]


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


@pytest.fixture
def agents(machine):
    """All five agents with skills and servers, and a credential store for each."""
    machine.install("claude", "codex", "opencode", "pi", "agy")
    home = machine.home
    for directory, name in [
        (".claude", "from-claude"),
        (".codex", "from-codex"),
        (".config/opencode", "from-opencode"),
        (".pi/agent", "from-pi"),
        (".gemini/antigravity-cli", "from-agy"),
    ]:
        write_skill(home / directory / "skills" / name, f"name: {name}\ndescription: d")
    server = {"command": "/bin/tool", "env": {"API_KEY": "x"}}
    write_json(home / ".claude.json", {"mcpServers": {"s": server}})
    (home / ".codex" / "config.toml").write_text('[mcp_servers.s]\ncommand = "/bin/tool"\n')
    write_json(
        home / ".config" / "opencode" / "opencode.json",
        {"mcp": {"s": {"type": "local", "command": ["/bin/tool"]}}},
    )
    write_json(home / ".pi" / "agent" / "settings.json", {"packages": []})
    write_json(home / ".gemini" / "config" / "mcp_config.json", {"mcpServers": {"s": server}})
    write_skill(machine.project / ".claude" / "skills" / "local", "name: local\ndescription: d")
    write_json(machine.project / ".mcp.json", {"mcpServers": {"r": server}})
    for store in CREDENTIAL_STORES:
        write_json(home / store, {"access_token": SECRET, "description": SECRET, "name": SECRET})
    write_json(machine.project / ".env", {"KEY": SECRET})
    return machine


def audit(machine, tmp_path, mode, args):
    log = tmp_path / "audit.json"
    output = RUNNERS[mode](
        machine,
        *args,
        code=AUDITED,
        env={"AUDIT_LOG": str(log), "PYTHONDONTWRITEBYTECODE": "1"},
    )
    return output, json.loads(log.read_text())


def under_test_directories(machine, opened):
    roots = (f"{machine.home}/", f"{machine.project}/")
    return {path for path in opened if path.startswith(roots)}


def snapshot(*roots):
    found = {}
    for root in roots:
        for path in sorted(root.rglob("*")):
            status = path.lstat()
            if path.is_symlink():
                content = str(path.readlink())
            elif path.is_file():
                content = hashlib.sha256(path.read_bytes()).hexdigest()
            else:
                content = None
            found[str(path)] = (status.st_mode, status.st_mtime_ns, status.st_size, content)
    return found


@pytest.mark.parametrize("mode", RUNNERS)
def test_the_audit_sees_config_being_read(agents, tmp_path, mode):
    # Without this, the tests below would also pass if the audit saw nothing.
    _, log = audit(agents, tmp_path, mode, ())
    opened = under_test_directories(agents, log["opened"])
    assert {str(agents.home / path) for path in CONFIG_READ} <= opened


@pytest.mark.parametrize("args", ARGUMENTS)
@pytest.mark.parametrize("mode", RUNNERS)
def test_credential_stores_are_never_opened(agents, tmp_path, mode, args):
    output, log = audit(agents, tmp_path, mode, args)
    opened = under_test_directories(agents, log["opened"])
    assert not {str(agents.home / store) for store in CREDENTIAL_STORES} & opened
    # Anything that merely looks like one, except the agents' own config.
    assert not [
        path
        for path in opened
        if CREDENTIAL_NAME.search(path.rsplit("/", 1)[1]) and not path.endswith("/SKILL.md")
    ]
    assert SECRET not in output


@pytest.mark.parametrize(
    "link",
    [
        ".claude/skills/evil/SKILL.md",
        ".agents/skills/evil/SKILL.md",
        ".pi/skills/evil.md",
    ],
)
@pytest.mark.parametrize("target", [".codex/auth.json", ".ssh/id_rsa", ".env"])
def test_a_link_cannot_print_another_file(agents, tmp_path, capsys, link, target):
    # The file has no frontmatter, so its text would otherwise become the description.
    secret_file = agents.home / target
    secret_file.parent.mkdir(parents=True, exist_ok=True)
    secret_file.write_text(f"{SECRET}\n")
    path = agents.project / link
    path.parent.mkdir(parents=True, exist_ok=True)
    path.symlink_to(secret_file)
    (agents.home / ".pi" / "agent" / "settings.json").write_text(
        '{"defaultProjectTrust": "always"}'
    )

    assert main(["--all", "--detail"]) == 0
    captured = capsys.readouterr()
    assert SECRET not in captured.out + captured.err
    assert "evil" in captured.out and " broken" in captured.out
    assert f"not reading {path}" in captured.err


def test_a_link_to_markdown_is_still_read(machine, capsys, tmp_path):
    machine.install("claude")
    guide = tmp_path / "shared" / "guide.md"
    guide.parent.mkdir()
    guide.write_text("---\nname: linked\ndescription: From the guide\n---\n")
    skill = machine.home / ".claude" / "skills" / "linked"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").symlink_to(guide)
    assert main(["--detail"]) == 0
    captured = capsys.readouterr()
    assert "From the guide" in captured.out and " enabled" in captured.out
    assert captured.err == ""


@pytest.mark.parametrize("args", ARGUMENTS)
@pytest.mark.parametrize("mode", RUNNERS)
def test_nothing_is_written(agents, tmp_path, mode, args):
    before = snapshot(agents.home, agents.project)
    _, log = audit(agents, tmp_path, mode, args)
    assert log["changed"] == []
    assert snapshot(agents.home, agents.project) == before
    assert not agents.executed
