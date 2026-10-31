import io
import json
import re
import sys

import pytest
from conftest import run_on_terminal, run_piped, write_skill
from rich.cells import cell_len
from rich.console import Console

from agt_view import cli
from agt_view.cli import main
from agt_view.model import Item, Kind, Scope, Status
from agt_view.render_pretty import _cells

SECRET = "hunter2-do-not-print"
COLOR_CODE = re.compile(r"\x1b\[[0-9;]*m")
BORDER = re.compile(r"[│╭╮╰╯─]")
STATUS_MARKS = {
    Status.ENABLED: ("●", "32"),
    Status.DISABLED: ("○", "2"),
    Status.PENDING: ("◐", "33"),
    Status.BROKEN: ("✖", "1;31"),
    Status.UNKNOWN: ("?", "33"),
}


@pytest.fixture
def rich_mode(monkeypatch):
    monkeypatch.setattr(cli, "_use_rich", lambda no_color_flag: True)
    monkeypatch.setenv("COLUMNS", "100")
    monkeypatch.setenv("TERM", "xterm")
    monkeypatch.delenv("COLORTERM", raising=False)


@pytest.fixture
def agent(machine, rich_mode):
    machine.install("claude")
    (machine.home / ".claude").mkdir()
    return machine


def run(capsys, *args):
    """The raw output and the output without color codes."""
    assert main(list(args)) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    return captured.out, COLOR_CODE.sub("", captured.out)


def without_layout(text):
    return "".join(BORDER.sub("", text).split())


def test_panel_holds_the_state_and_the_table(agent, capsys):
    write_skill(agent.home / ".claude" / "skills" / "one", "name: one\ndescription: d")
    raw, out = run(capsys)
    assert "\x1b[" in raw
    lines = out.splitlines()
    assert lines[0].startswith("╭─ claude-code ─")
    assert lines[1].startswith("│ ~/.claude ")
    assert lines[2].split() == ["│", "SCOPE/KIND", "NAME", "STATUS", "DESCRIPTION", "│"]
    assert set(lines[3].strip("│ ")) == {"─"}
    assert lines[4].split() == ["│", "U/skill", "one", "●", "enabled", "d", "│"]
    assert lines[5].startswith("╰") and "skill 1" in lines[5]


def test_every_status_has_a_mark_and_a_color(tmp_path):
    path = tmp_path / "x"
    for status, (mark, code) in STATUS_MARKS.items():
        item = Item("x", Kind.SKILL, "skill", "a", Scope.USER, status, "", path, path)
        console = Console(file=io.StringIO(), force_terminal=True, color_system="standard")
        console.print(_cells(item)[2])
        assert f"\x1b[{code}m{mark} {status}" in console.file.getvalue()


def test_counts_cover_the_items_shown(agent, capsys):
    skills = agent.home / ".claude" / "skills"
    write_skill(skills / "one", "name: one\ndescription: d")
    write_skill(skills / "two", "name: two\ndescription: d")
    (agent.home / ".claude.json").write_text(json.dumps({"mcpServers": {"s": {"command": "c"}}}))
    assert "skill 2 · mcp 1" in run(capsys)[1]
    assert "skill 1 ─" in run(capsys, "-n", "one")[1]


def test_inactive_agent_shows_its_state(machine, rich_mode, capsys):
    (machine.home / ".codex").mkdir()
    raw, out = run(capsys, "-a", "codex")
    assert "not installed, configured at ~/.codex" in out
    assert "no items" in out
    assert "\x1b[33mnot installed" in raw


def test_detail_starts_at_the_name_column(agent, capsys, tmp_path):
    target = write_skill(tmp_path / "shared" / "one", "name: one\ndescription: Does a thing")
    (agent.home / ".claude" / "skills").mkdir()
    (agent.home / ".claude" / "skills" / "one").symlink_to(target)
    write_skill(agent.home / ".claude" / "skills" / "two", "name: two\ndescription: d")
    _, out = run(capsys, "--detail")
    lines = out.splitlines()
    name_column = next(line for line in lines if "SCOPE/KIND" in line).index("NAME")
    for label in ("Description", "Path", "Real path"):
        line = next(line for line in lines if f" {label} " in line)
        assert line.index(label) == name_column
    assert "Does a thing" in out
    assert "~/.claude/skills/one (symlink)" in out
    # After the head, and before each item.
    assert sum(set(line.strip("│ ")) == {"─"} for line in lines) == 2


def test_no_details_without_the_flag(agent, capsys):
    write_skill(agent.home / ".claude" / "skills" / "one", "name: one\ndescription: Words")
    out = run(capsys)[1]
    assert "Words" in out
    assert "Path" not in out and "Description" not in out


def description_rows(out):
    return [line for line in out.splitlines() if "enabled" in line]


def test_short_description_is_shown_in_full(agent, capsys):
    write_skill(agent.home / ".claude" / "skills" / "one", "name: one\ndescription: Does a thing")
    out = run(capsys)[1]
    assert "Does a thing" in out and "…" not in out


def test_long_description_is_cut_to_the_space_left(agent, capsys, monkeypatch):
    monkeypatch.setenv("COLUMNS", "70")
    text = "Long words " * 30
    write_skill(agent.home / ".claude" / "skills" / "one", f"name: one\ndescription: {text}")
    out = run(capsys)[1]
    assert all(len(line) == 70 for line in out.splitlines())
    [row] = description_rows(out)
    assert "Long words" in row and row.endswith("… │")
    head = next(line for line in out.splitlines() if "SCOPE/KIND" in line)
    assert row.index("Long words") == head.index("DESCRIPTION")


def test_description_cut_counts_wide_characters(agent, capsys, monkeypatch):
    monkeypatch.setenv("COLUMNS", "70")
    text = "技能" * 40
    write_skill(agent.home / ".claude" / "skills" / "one", f"name: one\ndescription: {text}")
    [row] = description_rows(run(capsys)[1])
    assert cell_len(row) == 70 and "技能" in row and "…" in row


def test_description_never_takes_space_from_the_name(agent, capsys, monkeypatch):
    monkeypatch.setenv("COLUMNS", "70")
    name = "a-name-of-thirty-characters-ok"
    text = "Long words " * 30
    write_skill(agent.home / ".claude" / "skills" / "one", f"name: {name}\ndescription: {text}")
    assert name in run(capsys)[1]


def test_description_is_left_out_without_enough_space(agent, capsys, monkeypatch):
    monkeypatch.setenv("COLUMNS", "40")
    write_skill(agent.home / ".claude" / "skills" / "one", "name: one\ndescription: Words")
    out = run(capsys)[1]
    assert "DESCRIPTION" not in out and "Words" not in out


def test_no_description_column_without_descriptions(agent, capsys):
    (agent.home / ".claude.json").write_text(json.dumps({"mcpServers": {"s": {"command": "c"}}}))
    assert "DESCRIPTION" not in run(capsys, "-k", "mcp")[1]


def test_detail_has_no_description_column(agent, capsys):
    write_skill(agent.home / ".claude" / "skills" / "one", "name: one\ndescription: Words")
    assert "DESCRIPTION" not in run(capsys, "--detail")[1]


def test_description_in_the_table_is_untrusted(agent, capsys):
    skill = agent.home / ".claude" / "skills" / "evil"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        '---\nname: evil\ndescription: "[red]a\x1b[2J\x1b]0;title\x07b\\nnext\u202e"\n---\n'
    )
    _, out = run(capsys)
    assert "\x1b" not in out and "\x07" not in out and "\u202e" not in out
    assert "[red]ab next" in out


@pytest.mark.parametrize("text", ["[red]x[/red]", ":smile:", "[link=https://e.test]y[/link]"])
def test_markup_is_literal(agent, capsys, text):
    skill = agent.home / ".claude" / "skills" / "dir"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(f'---\nname: "{text}"\ndescription: "{text}"\n---\n')
    raw, out = run(capsys, "--detail")
    assert out.count(text) == 2
    assert "\x1b]8" not in raw


def test_markup_in_a_path_is_literal(agent, capsys):
    write_skill(agent.home / ".claude" / "skills" / "[red]dir", "name: one\ndescription: d")
    assert "skills/[red]dir" in run(capsys, "--detail")[1]


def test_hostile_text_is_cleaned(agent, capsys):
    skill = agent.home / ".claude" / "skills" / "evil"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        '---\nname: evil\ndescription: "a\x1b[2J\x1b]0;title\x07b\\nnext\u202eline"\n---\n'
    )
    _, out = run(capsys, "--detail")
    assert "\x1b" not in out and "\x07" not in out and "\u202e" not in out
    assert "ab next" in out


def test_secrets_stay_out(agent, capsys):
    (agent.home / ".claude.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "local": {
                        "command": f"https://user:{SECRET}@x.test/run",
                        "args": ["--token", SECRET],
                        "env": {"API_KEY": SECRET},
                    },
                    "remote": {
                        "type": "http",
                        "url": f"https://user:{SECRET}@x.test/mcp?key={SECRET}",
                        "headers": {"Authorization": f"Bearer {SECRET}"},
                    },
                }
            }
        )
    )
    _, out = run(capsys, "--detail")
    assert "API_KEY" in out and "Authorization" in out
    assert SECRET not in out


def test_narrow_terminal_keeps_everything(agent, capsys, monkeypatch):
    monkeypatch.setenv("COLUMNS", "50")
    long_name = "a-very-long-name-" * 4 + "end"
    directory = "a-very-long-directory-" * 5 + "end"
    write_skill(agent.home / ".claude" / "skills" / directory, f"name: {long_name}\ndescription: d")
    _, out = run(capsys, "--detail")
    lines = out.splitlines()
    assert all(len(line) <= 50 for line in lines)
    assert f"~/.claude/skills/{directory}" in without_layout(out)

    # The name folds within its own column, leaving the others intact.
    head = next(line for line in lines if "SCOPE/KIND" in line)
    first = next(number for number, line in enumerate(lines) if "U/skill" in line)
    last = next(number for number, line in enumerate(lines) if "Description" in line)
    column = [line[head.index("NAME") : head.index("STATUS")] for line in lines[first:last]]
    assert "".join(part.strip() for part in column) == long_name
    assert "● enabled" in lines[first]


def test_very_narrow_terminal_still_fits(agent, capsys, monkeypatch):
    monkeypatch.setenv("COLUMNS", "30")
    write_skill(agent.home / ".claude" / "skills" / "one", "name: one\ndescription: d")
    assert all(len(line) <= 30 for line in run(capsys, "--detail")[1].splitlines())


def test_wide_characters_keep_columns_aligned(agent, capsys):
    write_skill(agent.home / ".claude" / "skills" / "one", "name: 技能\ndescription: d")
    write_skill(agent.home / ".claude" / "skills" / "two", "name: abcdef\ndescription: d")
    rows = [line for line in run(capsys)[1].splitlines() if "enabled" in line]
    assert len(rows) == 2
    assert len({cell_len(row[: row.index("●")]) for row in rows}) == 1


def test_messages(agent, capsys):
    (agent.home / ".codex").mkdir()
    write_skill(agent.home / ".claude" / "skills" / "one", "name: one\ndescription: d")
    _, out = run(capsys, "nothing-matches-this")
    assert "No items match." in out
    assert "Hidden: codex. Use --all for details." in out
    assert "╭" not in out


def test_message_when_nothing_is_detected(machine, rich_mode, capsys):
    _, out = run(capsys)
    assert "No supported agents detected." in out


class FakeStdout:
    def __init__(self, tty=True, encoding="utf-8"):
        self.tty, self.encoding = tty, encoding

    def isatty(self):
        return self.tty


@pytest.mark.parametrize(
    ("flag", "env", "stdout", "expected"),
    [
        (False, {}, FakeStdout(), True),
        (False, {}, FakeStdout(encoding="UTF-8"), True),
        (True, {}, FakeStdout(), False),
        (False, {"NO_COLOR": "1"}, FakeStdout(), False),
        (False, {"TERM": "dumb"}, FakeStdout(), False),
        (False, {}, FakeStdout(tty=False), False),
        (False, {}, FakeStdout(encoding="latin-1"), False),
    ],
)
def test_when_rich_is_used(monkeypatch, flag, env, stdout, expected):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm")
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(sys, "stdout", stdout)
    assert cli._use_rich(flag) is expected


@pytest.fixture
def real_machine(machine):
    machine.install("claude")
    (machine.home / ".claude").mkdir()
    write_skill(machine.home / ".claude" / "skills" / "one", "name: one\ndescription: Words")
    return machine


def test_terminal_gets_rich_output(real_machine):
    out = run_on_terminal(real_machine)
    assert "╭" in out and "●" in out and "\x1b[" in out


@pytest.mark.parametrize("args", [(), ("--detail",)])
def test_no_color_on_a_terminal_matches_piped_output(real_machine, args):
    terminal = run_on_terminal(real_machine, "--no-color", *args)
    assert terminal == run_piped(real_machine, *args)
    assert "\x1b" not in terminal and "╭" not in terminal


def test_plain_output_does_not_load_rich(real_machine):
    run_piped(
        real_machine,
        code=(
            "import sys\n"
            "from agt_view.cli import main\n"
            "main(['--no-color'])\n"
            "assert not any(name.split('.')[0] == 'rich' for name in sys.modules)\n"
        ),
    )


@pytest.mark.parametrize(
    ("scope", "letter"), [(Scope.PROJECT, "P"), (Scope.USER, "U"), (Scope.SYSTEM, "S")]
)
def test_scope_is_one_letter(tmp_path, scope, letter):
    item = Item("x", Kind.MCP, "mcp", "a", scope, Status.ENABLED, "", tmp_path, tmp_path)
    assert _cells(item)[0].plain == f"{letter}/mcp"
