import json

import pytest
from conftest import write_skill

from agt_view.cli import main

SECRET = "hunter2-do-not-print"


def run(capsys, *args):
    code = main(["show", *args])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


@pytest.fixture
def agents(machine):
    machine.install("claude", "codex")
    (machine.home / ".claude").mkdir()
    (machine.home / ".codex").mkdir()
    return machine


def test_shows_skill_details(agents, capsys, tmp_path):
    target = write_skill(tmp_path / "shared" / "common", "name: common\ndescription: Does a thing")
    (agents.home / ".claude" / "skills").mkdir()
    (agents.home / ".claude" / "skills" / "common").symlink_to(target)
    code, out, err = run(capsys, "common")
    assert (code, err) == (0, "")
    assert out.splitlines() == [
        "common  (skill, claude-code)",
        "  Scope:        user",
        "  Status:       enabled",
        "  Description:  Does a thing",
        "  Path:         ~/.claude/skills/common (symlink)",
        f"  Real path:    {target}",
    ]


def test_shows_matches_across_agents_with_sharing(agents, capsys, tmp_path):
    target = write_skill(tmp_path / "shared" / "common", "name: common\ndescription: d")
    for directory in (".claude", ".codex"):
        (agents.home / directory / "skills").mkdir()
        (agents.home / directory / "skills" / "common").symlink_to(target)
    code, out, _ = run(capsys, "common")
    assert code == 0
    assert "common  (skill, claude-code)" in out and "common  (skill, codex)" in out
    assert "Shared with:  codex" in out and "Shared with:  claude-code" in out
    assert out.count("\n\n") == 1


def test_shows_server_details_without_secrets(agents, capsys):
    write_json(
        agents.home / ".claude.json",
        {
            "mcpServers": {
                "local": {
                    "command": f"https://user:{SECRET}@x.test/run",
                    "args": ["--token", SECRET],
                    "env": {"API_KEY": SECRET, "OTHER": SECRET},
                },
                "remote": {
                    "type": "http",
                    "url": f"https://user:{SECRET}@x.test/mcp?key={SECRET}",
                    "headers": {"Authorization": f"Bearer {SECRET}"},
                },
            }
        },
    )
    code, out, err = run(capsys, "local")
    assert code == 0
    assert "Agent's term:  mcp server" in out
    assert "Transport:     stdio" in out
    assert "Command:       https://x.test/run" in out
    assert "Env names:     API_KEY, OTHER" in out
    assert SECRET not in out + err
    code, out, err = run(capsys, "remote")
    assert "URL:           https://x.test/mcp" in out
    assert "Header names:  Authorization" in out
    assert SECRET not in out + err


def test_plugin_found_with_or_without_marketplace(agents, capsys):
    directory = agents.home / ".claude" / "plugins" / "cache" / "p"
    write_json(directory / ".claude-plugin" / "plugin.json", {"description": "A plugin"})
    write_json(
        agents.home / ".claude" / "plugins" / "installed_plugins.json",
        {
            "version": 2,
            "plugins": {
                "p@m": [{"scope": "user", "installPath": str(directory), "version": "1.2"}]
            },
        },
    )
    for name in ("p@m", "p"):
        code, out, _ = run(capsys, name)
        assert code == 0
        assert "p@m  (extension, claude-code)" in out
        assert "Agent's term:  plugin" in out
        assert "Version:       1.2" in out
        assert "Description:   A plugin" in out


def test_scoped_package_name_is_not_split(machine, capsys):
    machine.install("opencode")
    write_json(machine.home / ".config" / "opencode" / "opencode.json", {"plugin": ["@org/tool"]})
    assert run(capsys, "@org/tool")[0] == 0
    assert run(capsys, "")[0] == 1


def test_no_match_exits_one(agents, capsys):
    code, out, err = run(capsys, "missing")
    assert (code, out) == (1, "")
    assert 'no item named "missing"' in err


def test_partial_name_does_not_match(agents, capsys):
    write_skill(agents.home / ".claude" / "skills" / "common", "name: common\ndescription: d")
    assert run(capsys, "comm")[0] == 1


def test_finds_item_of_hidden_agent_and_shows_its_state(machine, capsys):
    write_skill(machine.home / ".agents" / "skills" / "kept", "name: kept\ndescription: d")
    (machine.home / ".codex").mkdir()  # configured, not installed
    code, out, _ = run(capsys, "kept", "--agent", "codex")
    assert code == 0
    assert "Agent:        not installed, configured at ~/.codex" in out


def test_undetected_agents_are_not_searched(machine, capsys):
    # OpenCode reads this directory, but it's neither installed nor configured here.
    machine.install("claude")
    write_skill(machine.home / ".claude" / "skills" / "one", "name: one\ndescription: d")
    _, out, _ = run(capsys, "one")
    assert "claude-code" in out and "opencode" not in out
    _, out, _ = run(capsys, "one", "--agent", "opencode")
    assert "Agent:        not installed, not configured" in out


def test_filters(agents, capsys):
    write_skill(agents.home / ".claude" / "skills" / "same", "name: same\ndescription: d")
    write_skill(agents.project / ".claude" / "skills" / "same", "name: same\ndescription: d")
    write_skill(agents.home / ".codex" / "skills" / "same", "name: same\ndescription: d")
    assert run(capsys, "same", "--agent", "codex")[1].count("(skill,") == 1
    assert run(capsys, "same", "--scope", "project")[1].count("(skill,") == 1
    assert run(capsys, "same", "--kind", "mcp")[0] == 1
    assert run(capsys, "same")[1].count("(skill,") == 3


def test_cwd_flag(agents, capsys, tmp_path):
    write_skill(tmp_path / "other" / ".claude" / "skills" / "there", "name: there\ndescription: d")
    assert run(capsys, "there")[0] == 1
    assert run(capsys, "there", "--cwd", str(tmp_path / "other"))[0] == 0


def test_flags_can_come_first(agents, capsys):
    write_skill(agents.home / ".claude" / "skills" / "one", "name: one\ndescription: d")
    assert run(capsys, "--agent", "claude-code", "one")[0] == 0


def test_name_is_required(agents):
    with pytest.raises(SystemExit) as error:
        main(["show"])
    assert error.value.code == 2


def test_warning_explains_a_missing_item(agents, capsys):
    (agents.home / ".claude.json").write_text("{ not json")
    code, _, err = run(capsys, "gone")
    assert code == 1
    assert "warning: claude-code: cannot read" in err


def test_hostile_text_is_cleaned(agents, capsys):
    skill = agents.home / ".claude" / "skills" / "evil"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        '---\nname: evil\ndescription: "a\x1b[2J\x1b]0;title\x07b\\nnext\u202eline"\n---\n'
    )
    code, out, _ = run(capsys, "evil")
    assert code == 0
    assert "\x1b" not in out and "\x07" not in out and "\u202e" not in out
    assert "Description:  ab next" in out


def test_hostile_name_argument_is_cleaned(agents, capsys):
    _, _, err = run(capsys, "x\x1b[2Jy")
    assert "\x1b" not in err and '"xy"' in err


def test_never_starts_a_server(agents, capsys, tmp_path):
    marker = tmp_path / "started"
    script = tmp_path / "server.sh"
    script.write_text(f"#!/bin/sh\ntouch {marker}\n")
    script.chmod(0o755)
    write_json(agents.home / ".claude.json", {"mcpServers": {"s": {"command": str(script)}}})
    assert run(capsys, "s")[0] == 0
    assert not marker.exists() and not agents.executed
