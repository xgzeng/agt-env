import json

import pytest
from conftest import write_skill

from agt_env.cli import main

SECRET = "hunter2-do-not-print"


def run(capsys, *args):
    code = main(["--detail", *args])
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


def test_lines_start_at_the_name_column(agents, capsys, tmp_path):
    target = write_skill(tmp_path / "shared" / "common", "name: common\ndescription: Does a thing")
    (agents.home / ".claude" / "skills").mkdir()
    (agents.home / ".claude" / "skills" / "common").symlink_to(target)
    code, out, err = run(capsys, "-a", "claude-code")
    assert (code, err) == (0, "")
    assert out.splitlines() == [
        "claude-code  ~/.claude",
        "SCOPE/KIND  NAME",
        "------------------",
        "U/skill     common",
        "            Status:       enabled",
        "            Description:  Does a thing",
        "            Path:         ~/.claude/skills/common (symlink)",
        f"            Real path:    {target}",
    ]


def test_a_line_separates_the_head_and_each_item(agents, capsys):
    skills = agents.home / ".claude" / "skills"
    write_skill(skills / "one", "name: one\ndescription: d")
    write_skill(skills / "two", "name: two\ndescription: d")
    assert run(capsys, "-a", "claude-code")[1].splitlines() == [
        "claude-code  ~/.claude",
        "SCOPE/KIND  NAME",
        "----------------",
        "U/skill     one",
        "            Status:       enabled",
        "            Description:  d",
        "            Path:         ~/.claude/skills/one",
        "----------------",
        "U/skill     two",
        "            Status:       enabled",
        "            Description:  d",
        "            Path:         ~/.claude/skills/two",
    ]


def test_no_separator_without_detail(agents, capsys):
    skills = agents.home / ".claude" / "skills"
    write_skill(skills / "one", "name: one\ndescription: d")
    write_skill(skills / "two", "name: two\ndescription: d")
    assert main(["-a", "claude-code"]) == 0
    assert "---" not in capsys.readouterr().out


def test_names_the_agents_sharing_a_skill(agents, capsys, tmp_path):
    target = write_skill(tmp_path / "shared" / "common", "name: common\ndescription: d")
    for directory in (".claude", ".codex"):
        (agents.home / directory / "skills").mkdir()
        (agents.home / directory / "skills" / "common").symlink_to(target)
    code, out, _ = run(capsys)
    assert code == 0
    assert "Shared with:  codex" in out and "Shared with:  claude-code" in out


def test_server_details_without_secrets(agents, capsys):
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
    code, out, err = run(capsys, "-n", "local")
    assert code == 0
    assert "Agent's term:  mcp server" in out
    assert "Transport:     stdio" in out
    assert "Command:       https://x.test/run" in out
    assert "Env names:     API_KEY, OTHER" in out
    assert SECRET not in out + err
    code, out, err = run(capsys, "-n", "remote")
    assert "URL:           https://x.test/mcp" in out
    assert "Header names:  Authorization" in out
    assert SECRET not in out + err


def test_plugin_details(agents, capsys):
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
    _, out, _ = run(capsys, "-n", "p@m")
    assert "U/plugin    p@m" in out
    assert "Version:      1.2" in out
    assert "Description:  A plugin" in out
    # The kind is the agent's own term here, so it isn't repeated.
    assert "Agent's term" not in out


def test_agents_own_term_is_kept_for_a_renamed_kind(machine, capsys):
    machine.install("pi")
    extensions = machine.home / ".pi" / "agent" / "extensions"
    extensions.mkdir(parents=True)
    (extensions / "tool.ts").write_text("export default () => {}")
    _, out, _ = run(capsys)
    assert "U/plugin    tool" in out
    assert "Agent's term:  extension" in out


def test_without_the_flag_there_are_no_details(agents, capsys):
    write_skill(agents.home / ".claude" / "skills" / "one", "name: one\ndescription: Words")
    assert main([]) == 0
    assert "Words" not in capsys.readouterr().out


def test_hidden_agent_needs_all_or_agent(machine, capsys):
    write_skill(machine.home / ".agents" / "skills" / "kept", "name: kept\ndescription: d")
    (machine.home / ".codex").mkdir()  # configured, not installed
    code, out, _ = run(capsys, "-n", "kept")
    assert code == 0
    assert "kept" not in out and "Hidden: codex" in out
    for args in (("--all",), ("-a", "codex")):
        out = run(capsys, "-n", "kept", *args)[1]
        assert "codex  not installed, configured at ~/.codex" in out
        assert "~/.agents/skills/kept" in out


def test_filters_select_what_gets_details(agents, capsys):
    write_skill(agents.home / ".claude" / "skills" / "same", "name: same\ndescription: d")
    write_skill(agents.project / ".claude" / "skills" / "same", "name: same\ndescription: d")
    write_skill(agents.home / ".codex" / "skills" / "same", "name: same\ndescription: d")
    assert run(capsys, "-n", "same", "-a", "codex")[1].count("Path:") == 1
    assert run(capsys, "-n", "same", "--scope", "project")[1].count("Path:") == 1
    assert run(capsys, "-n", "same")[1].count("Path:") == 3


def test_hostile_text_is_cleaned(agents, capsys):
    skill = agents.home / ".claude" / "skills" / "evil"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        '---\nname: evil\ndescription: "a\x1b[2J\x1b]0;title\x07b\\nnext\u202eline"\n---\n'
    )
    code, out, _ = run(capsys)
    assert code == 0
    assert "\x1b" not in out and "\x07" not in out and "\u202e" not in out
    assert "Description:  ab next" in out


def test_never_starts_a_server(agents, capsys, tmp_path):
    marker = tmp_path / "started"
    script = tmp_path / "server.sh"
    script.write_text(f"#!/bin/sh\ntouch {marker}\n")
    script.chmod(0o755)
    write_json(agents.home / ".claude.json", {"mcpServers": {"s": {"command": str(script)}}})
    assert run(capsys, "-n", "s")[0] == 0
    assert not marker.exists() and not agents.executed
