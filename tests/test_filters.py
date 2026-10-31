import json

import pytest
from conftest import write_skill

from agt_view.cli import main


def names(capsys, *args):
    assert main(list(args)) == 0
    found = []
    for line in capsys.readouterr().out.splitlines():
        parts = line.split()
        if line.startswith("  ") and parts[0] != "SCOPE/KIND" and parts[0] != "no":
            found.append(parts[1])
    return sorted(found)


@pytest.fixture
def agents(machine):
    machine.install("claude", "codex")
    claude = machine.home / ".claude"
    write_skill(claude / "skills" / "Browser-Tools", "name: Browser-Tools\ndescription: Open pages")
    write_skill(claude / "skills" / "linter", "name: linter\ndescription: Check browser code")
    write_skill(machine.project / ".claude" / "skills" / "tests", "name: tests\ndescription: Run")
    write_skill(machine.home / ".codex" / "skills" / "reader", "name: reader\ndescription: Read")
    (machine.home / ".claude.json").write_text(
        json.dumps({"mcpServers": {"srv": {"command": "/bin/tool", "env": {"API_KEY": "v"}}}})
    )
    return machine


def test_name_is_a_case_insensitive_substring(agents, capsys):
    assert names(capsys, "-n", "BROWSER") == ["Browser-Tools"]
    assert names(capsys, "--name", "rowser-t") == ["Browser-Tools"]


def test_name_ignores_other_fields(agents, capsys):
    assert names(capsys, "-n", "pages") == []


def test_repeated_names_widen(agents, capsys):
    assert names(capsys, "-n", "linter", "-n", "reader") == ["linter", "reader"]


def test_keyword_searches_more_than_the_name(agents, capsys):
    assert names(capsys, "browser") == ["Browser-Tools", "linter"]


def test_words_in_one_argument_narrow(agents, capsys):
    assert names(capsys, "browser check") == ["linter"]
    assert names(capsys, "browser nothing") == []


def test_separate_arguments_widen(agents, capsys):
    assert names(capsys, "pages", "reader") == ["Browser-Tools", "reader"]


def test_narrow_and_widen_combine(agents, capsys):
    assert names(capsys, "browser check", "reader") == ["linter", "reader"]


def test_empty_keyword_is_ignored(agents, capsys):
    assert names(capsys, "") == names(capsys)
    assert names(capsys, " ", "reader") == ["reader"]


@pytest.mark.parametrize(
    ("keyword", "expected"),
    [
        ("mcp", ["srv"]),  # kind
        ("mcp server", ["srv"]),  # the agent's own term, as two words
        ("project", ["tests"]),  # scope
        ("P/skill", ["tests"]),  # the first column as printed
        ("project/skill", ["tests"]),  # and with the whole word
        ("api_key", ["srv"]),  # env name
        ("/bin/tool", ["srv"]),  # command
        ("stdio", ["srv"]),  # transport
        ("codex", ["reader"]),  # agent, also in the path
        ("~/.codex/skills", ["reader"]),  # path
        ("./.claude", ["tests"]),  # project path
    ],
)
def test_each_field_is_searched(agents, capsys, keyword, expected):
    assert names(capsys, keyword) == expected


def test_status_and_sharing_agents_are_searched(machine, capsys, tmp_path):
    machine.install("claude", "codex")
    target = write_skill(tmp_path / "shared" / "common", "name: common\ndescription: d")
    for directory in (".claude", ".codex"):
        (machine.home / directory / "skills").mkdir(parents=True)
        (machine.home / directory / "skills" / "common").symlink_to(target)
    write_skill(machine.home / ".claude" / "skills" / "own", "name: own\ndescription: d")
    assert names(capsys, "-a", "claude-code", "codex") == ["common"]  # shared with
    assert names(capsys, "-a", "claude-code", "symlink") == ["common"]  # the mark
    assert names(capsys, "-a", "claude-code", "enabled") == ["common", "own"]


def test_home_and_project_directory_paths_do_not_match_every_item(agents, capsys, tmp_path):
    assert names(capsys, agents.home.name) == []
    project = tmp_path / "distinctive-project"
    write_skill(project / ".claude" / "skills" / "here", "name: here\ndescription: d")
    assert names(capsys, "-p", str(project), "here") == ["here"]
    assert names(capsys, "-p", str(project), "distinctive") == []


def test_path_is_searched_though_not_printed(agents, capsys):
    write_skill(agents.home / ".claude" / "skills" / "dir-only", "name: renamed\ndescription: d")
    assert names(capsys, "dir-only") == ["renamed"]


def test_different_filters_narrow_each_other(agents, capsys):
    assert names(capsys, "browser", "-k", "mcp") == []
    assert names(capsys, "browser", "-s", "project") == []
    assert names(capsys, "browser", "-n", "lint") == ["linter"]
    assert names(capsys, "skills", "-a", "codex") == ["reader"]
    assert names(capsys, "-n", "e", "-a", "codex", "-k", "skill", "-s", "user") == ["reader"]


def test_agent_with_no_match_is_left_out(agents, capsys):
    main(["reader"])
    out = capsys.readouterr().out
    assert "codex" in out and "claude-code" not in out
    main(["-n", "reader"])
    assert "claude-code" not in capsys.readouterr().out


def test_no_match_message_exits_zero(agents, capsys):
    assert main(["nothing-matches"]) == 0
    assert capsys.readouterr().out == "No items match.\n"


def test_kind_and_scope_alone_keep_the_empty_section(agents, capsys):
    main(["-a", "codex", "-k", "mcp"])
    out = capsys.readouterr().out
    assert "codex  ~/.codex" in out and "no items" in out


def test_hidden_line_stays_while_searching(machine, capsys):
    machine.install("claude")
    write_skill(machine.home / ".claude" / "skills" / "one", "name: one\ndescription: d")
    (machine.home / ".codex").mkdir()  # configured, not installed
    assert main(["one"]) == 0
    out = capsys.readouterr().out
    assert "one" in out and out.endswith("\n\nHidden: codex. Use --all for details.\n")
    assert main(["nothing"]) == 0
    assert capsys.readouterr().out == "No items match.\nHidden: codex. Use --all for details.\n"


def test_hidden_agents_are_searched_with_all_or_agent(machine, capsys):
    write_skill(machine.home / ".agents" / "skills" / "kept", "name: kept\ndescription: d")
    (machine.home / ".codex").mkdir()  # configured, not installed
    assert names(capsys, "kept") == []
    assert names(capsys, "kept", "--all") == ["kept"]
    assert names(capsys, "kept", "-a", "codex") == ["kept"]


def test_former_command_names_are_keywords(agents, capsys):
    write_skill(agents.home / ".claude" / "skills" / "list", "name: list\ndescription: d")
    assert names(capsys, "list") == ["list"]
    assert names(capsys, "show", "reader") == ["reader"]


def test_hostile_keyword_never_reaches_output(agents, capsys):
    main(["x\x1b[2Jy"])
    captured = capsys.readouterr()
    assert "\x1b" not in captured.out + captured.err
