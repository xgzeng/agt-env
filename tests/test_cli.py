import json
import os
import re

import pytest
from conftest import SCOPE_WORDS, write_skill

from agt_view.cli import main


def run(capsys, *args):
    code = main(list(args))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def rows(out):
    """Parse item rows into (agent, kind, name, scope, status)."""
    result, agent = [], None
    for line in out.splitlines():
        if line and not line.startswith(" "):
            agent = line.split()[0]
        elif line.strip() and not line.strip().startswith(("SCOPE/KIND", "no items", "!")):
            first, *others = re.split(r"\s{2,}", line.strip())
            letter, _, kind = first.partition("/")
            result.append((agent, kind, others[0], SCOPE_WORDS[letter], *others[1:]))
    return result


def claude(machine):
    machine.install("claude")
    return machine.home / ".claude"


def codex(machine):
    machine.install("codex")
    home = machine.home / ".codex"
    home.mkdir(exist_ok=True)
    return home


def test_clean_start_exits_zero_with_message(machine, capsys):
    code, out, err = run(capsys)
    assert code == 0
    assert "No supported agents detected" in out
    assert err == ""


def test_lists_items_by_scope_and_sorted(machine, capsys):
    skills = claude(machine) / "skills"
    write_skill(skills / "zeta")
    write_skill(skills / "alpha")
    write_skill(machine.project / ".claude" / "skills" / "repo")
    _, out, _ = run(capsys)
    assert out.splitlines()[0] == "claude-code  ~/.claude"
    assert [(r[2], r[3], r[4]) for r in rows(out)] == [
        ("repo", "project", "enabled"),
        ("alpha", "user", "enabled"),
        ("zeta", "user", "enabled"),
    ]


def test_scope_sorts_before_kind_and_name(machine, capsys):
    codex(machine)
    write_skill(machine.system / "etc" / "codex" / "skills" / "a-admin")
    write_skill(machine.home / ".agents" / "skills" / "b-user")
    write_skill(machine.project / ".agents" / "skills" / "z-repo")
    config = machine.project / ".codex" / "config.toml"
    config.parent.mkdir()
    config.write_text('[mcp_servers.c-server]\ncommand = "x"\n')
    assert [(r[1], r[2], r[3]) for r in rows(run(capsys)[1])] == [
        ("skill", "z-repo", "project"),
        ("mcp", "c-server", "project"),
        ("skill", "b-user", "user"),
        ("skill", "a-admin", "system"),
    ]


def details(out):
    """Parse --detail output into {item name: {label: value}}."""
    result, current = {}, None
    for line in out.splitlines():
        match = re.fullmatch(r"\s+([A-Z][A-Za-z' ]*):\s+(.*)", line)
        if match:
            current[match[1]] = match[2]
        elif re.match(r"[PUS]/", line):
            current = result.setdefault(re.split(r"\s{2,}", line)[1], {})
    return result


def test_paths_only_appear_as_a_detail(machine, capsys):
    write_skill(claude(machine) / "skills" / "personal")
    assert "skills/personal" not in run(capsys)[1]
    assert "skills/personal" in run(capsys, "--detail")[1]


def test_detail_shows_where_each_skill_was_found(machine, capsys, tmp_path):
    skills = claude(machine) / "skills"
    write_skill(skills / "personal")
    write_skill(machine.project / ".claude" / "skills" / "repo")
    target = write_skill(tmp_path / "elsewhere" / "target")
    (skills / "linked").symlink_to(target)
    (skills / "gone").symlink_to(tmp_path / "missing")
    found = {name: d["Path"] for name, d in details(run(capsys, "--detail")[1]).items()}
    assert found == {
        "gone": "~/.claude/skills/gone (symlink)",
        "linked": "~/.claude/skills/linked (symlink)",
        "personal": "~/.claude/skills/personal",
        "repo": "./.claude/skills/repo",
    }


def test_only_a_linked_skill_is_marked_not_a_linked_parent(machine, capsys, tmp_path):
    real_skills = tmp_path / "real-skills"
    write_skill(real_skills / "one")
    claude(machine).mkdir()
    (machine.home / ".claude" / "skills").symlink_to(real_skills)
    assert details(run(capsys, "--detail")[1])["one"]["Path"] == "~/.claude/skills/one"


def test_long_path_is_not_cut(machine, capsys, tmp_path):
    machine.install("claude")
    skills = machine.home / ".claude" / "skills"
    write_skill(skills / ("x" * 60), "name: long\ndescription: d")
    (skills / ("y" * 60)).symlink_to(write_skill(tmp_path / "target", "name: link\ndescription: d"))
    found = details(run(capsys, "--detail")[1])
    assert found["long"]["Path"] == f"~/.claude/skills/{'x' * 60}"
    assert found["link"]["Path"] == f"~/.claude/skills/{'y' * 60} (symlink)"


def test_agent_with_no_items(machine, capsys):
    claude(machine).mkdir()
    assert "no items" in run(capsys)[1]


@pytest.mark.parametrize("configured", [True, False])
def test_inactive_agents_hidden_and_named(machine, capsys, configured):
    claude(machine).mkdir()
    if configured:
        (machine.home / ".codex").mkdir()  # configured, not installed
    else:
        machine.install("codex")  # installed, not configured
    _, out, _ = run(capsys)
    assert "Hidden: codex. Use --all for details." in out


def test_all_shows_inactive_agent_state_and_items(machine, capsys):
    claude(machine).mkdir()
    write_skill(machine.home / ".agents" / "skills" / "kept")
    (machine.home / ".codex").mkdir()
    _, out, _ = run(capsys, "--all")
    assert "codex  not installed, configured at ~/.codex" in out
    assert "kept" in out
    assert "Hidden" not in out


def test_installed_not_configured_state(machine, capsys):
    machine.install("claude")
    _, out, _ = run(capsys, "--all")
    assert "claude-code  installed, not configured" in out


def test_named_agent_shown_even_when_undetected(machine, capsys):
    _, out, _ = run(capsys, "-a", "codex")
    assert "codex  not installed, not configured" in out


def test_project_config_alone_counts_as_configured(machine, capsys):
    machine.install("claude")
    write_skill(machine.project / ".claude" / "skills" / "repo")
    _, out, _ = run(capsys)
    assert out.splitlines()[0] == "claude-code  ./.claude"


def test_filters(machine, capsys):
    write_skill(claude(machine) / "skills" / "personal")
    write_skill(machine.project / ".claude" / "skills" / "repo")
    write_skill(codex(machine) / "skills" / "other")
    _, out, _ = run(capsys, "--scope", "project", "--agent", "claude-code")
    assert [r[2] for r in rows(out)] == ["repo"]
    assert "codex" not in out


def test_agent_flag_is_repeatable(machine, capsys):
    write_skill(claude(machine) / "skills" / "personal")
    write_skill(codex(machine) / "skills" / "other")
    (machine.home / ".pi" / "agent").mkdir(parents=True)
    machine.install("pi")
    _, out, _ = run(capsys, "-a", "codex", "--agent", "claude-code")
    assert {r[0] for r in rows(out)} == {"claude-code", "codex"}
    assert out.startswith("claude-code") and "pi " not in out


def test_flags_and_keywords_can_be_mixed(machine, capsys):
    write_skill(claude(machine) / "skills" / "personal")
    write_skill(codex(machine) / "skills" / "other")
    for args in (
        ("personal", "--kind", "skill"),
        ("--kind", "skill", "personal"),
        ("-a", "claude-code", "personal", "-k", "skill", "skill"),
    ):
        assert [r[2] for r in rows(run(capsys, *args)[1])] == ["personal"]


def test_short_flags(machine, capsys, tmp_path):
    write_skill(claude(machine) / "skills" / "personal")
    other = tmp_path / "other"
    write_skill(other / ".claude" / "skills" / "there")
    _, out, _ = run(capsys, "-k", "skill", "-s", "project", "-p", str(other))
    assert [r[2] for r in rows(out)] == ["there"]
    _, out, _ = run(capsys, "-k", "mcp", "-p", str(other))
    assert "there" not in out


def test_project_flag_selects_project(machine, capsys, tmp_path):
    claude(machine).mkdir()
    other = tmp_path / "other"
    write_skill(other / ".claude" / "skills" / "there")
    assert "there" in run(capsys, "--project", str(other))[1]


def test_usage_errors_exit_two(machine, capsys):
    for args in (
        ["-a", "nope"],
        ["--project", "/does/not/exist"],
        ["--bogus"],
        ["-k", "extension"],
    ):
        with pytest.raises(SystemExit) as error:
            main(args)
        assert error.value.code == 2


def test_override_location_is_reported(machine, capsys, tmp_path, monkeypatch):
    machine.install("claude")
    moved = tmp_path / "moved"
    write_skill(moved / "skills" / "relocated")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(moved))
    _, out, _ = run(capsys)
    assert out.splitlines()[0] == f"claude-code  {moved} (set by CLAUDE_CONFIG_DIR)"
    assert "relocated" in out


def test_override_to_default_location_not_reported(machine, capsys, monkeypatch):
    home = codex(machine)
    monkeypatch.setenv("CODEX_HOME", str(home))
    assert "set by" not in run(capsys)[1]


def test_shared_skills_marked_under_each_agent(machine, capsys, tmp_path):
    target = write_skill(tmp_path / "shared" / "common")
    (claude(machine) / "skills").mkdir(parents=True)
    (machine.home / ".claude" / "skills" / "common").symlink_to(target)
    (codex(machine) / "skills").mkdir()
    (machine.home / ".codex" / "skills" / "common").symlink_to(target)
    write_skill(machine.home / ".codex" / "skills" / "solo")
    out = run(capsys, "--detail")[1]
    assert out.count("Shared with:") == 2  # Not on solo.
    assert "Shared with:  codex" in out and "Shared with:  claude-code" in out
    assert "SHARED" not in run(capsys)[1]


def test_skill_linked_twice_within_an_agent_listed_once(machine, capsys, tmp_path):
    target = write_skill(tmp_path / "shared" / "common")
    home = codex(machine)
    (machine.home / ".agents" / "skills").mkdir(parents=True)
    (machine.home / ".agents" / "skills" / "common").symlink_to(target)
    (home / "skills").mkdir()
    (home / "skills" / "common").symlink_to(target)
    assert [r[2] for r in rows(run(capsys)[1])] == ["common"]


def test_different_skills_sharing_a_name_both_listed(machine, capsys):
    home = codex(machine)
    write_skill(machine.home / ".agents" / "skills" / "dup")
    write_skill(home / "skills" / "dup")
    assert [r[2] for r in rows(run(capsys)[1])] == ["dup", "dup"]


def test_inactive_agent_not_listed_as_sharing(machine, capsys, tmp_path):
    target = write_skill(tmp_path / "shared" / "common")
    (claude(machine) / "skills").mkdir(parents=True)
    (machine.home / ".claude" / "skills" / "common").symlink_to(target)
    (machine.home / ".codex" / "skills").mkdir(parents=True)  # not installed
    (machine.home / ".codex" / "skills" / "common").symlink_to(target)
    assert "Shared with" not in run(capsys, "--detail")[1]


def test_dangling_symlink_is_broken(machine, capsys, tmp_path):
    skills = claude(machine) / "skills"
    skills.mkdir(parents=True)
    (skills / "gone").symlink_to(tmp_path / "missing")
    assert rows(run(capsys)[1])[0][2:5] == ("gone", "user", "broken")


def test_non_skill_entries_are_not_counted(machine, capsys):
    skills = claude(machine) / "skills"
    (skills / "group-only").mkdir(parents=True)
    write_skill(skills / "group-only" / "nested")  # Claude Code doesn't recurse
    write_skill(skills / "synced" / "account" / "pdf")
    write_skill(skills / ".trash" / "old")
    (skills / "loose.md").write_text("x")
    assert "no items" in run(capsys)[1]


def test_claude_frontmatter_name_and_defaults(machine, capsys):
    skills = claude(machine) / "skills"
    write_skill(skills / "dir-name", "name: fm-name")
    (skills / "bare").mkdir()
    (skills / "bare" / "SKILL.md").write_text("no frontmatter at all")
    found = {r[2]: r[4] for r in rows(run(capsys)[1])}
    assert found == {"bare": "enabled", "fm-name": "enabled"}


def test_claude_reserved_name_is_broken(machine, capsys):
    write_skill(claude(machine) / "skills" / "x", "name: anthropic-skills:pdf")
    assert rows(run(capsys)[1])[0][4] == "broken"


@pytest.mark.parametrize(
    ("settings", "expected"),
    [
        ({"skillOverrides": {"one": "off"}}, "disabled"),
        ({"skillOverrides": {"one": "name-only"}}, "enabled"),
        ({"skillOverrides": {"one": "user-invocable-only"}}, "enabled"),
        ({"skillOverrides": {"one": "future-value"}}, "unknown"),
        ({"skillOverrides": {"other": "off"}}, "enabled"),
        ({"permissions": {"deny": ["Skill"]}}, "disabled"),
        ({"permissions": {"deny": ["Skill(one)"]}}, "disabled"),
        ({"permissions": {"deny": ["Skill(one *)"]}}, "disabled"),
        ({"permissions": {"deny": ["Skill(other)", "Bash"]}}, "enabled"),
        ({"unrelated": {"nested": True}}, "enabled"),
    ],
)
def test_claude_status_from_settings(machine, capsys, settings, expected):
    home = claude(machine)
    write_skill(home / "skills" / "one")
    (home / "settings.json").write_text(json.dumps(settings))
    assert rows(run(capsys)[1])[0][4] == expected


def test_claude_project_local_settings_override_user(machine, capsys):
    home = claude(machine)
    write_skill(home / "skills" / "one")
    (home / "settings.json").write_text('{"skillOverrides": {"one": "off"}}')
    local = machine.project / ".claude"
    local.mkdir()
    (local / "settings.local.json").write_text('{"skillOverrides": {"one": "on"}}')
    assert rows(run(capsys)[1])[0][4] == "enabled"


def test_codex_discovery_rules(machine, capsys):
    home = codex(machine)
    agents = machine.home / ".agents" / "skills"
    write_skill(agents / "user-skill", "name: user-skill\ndescription: d")
    write_skill(agents / "no-name")
    write_skill(agents / "group" / "nested")
    write_skill(agents / ".hidden")
    write_skill(home / "skills" / "legacy")
    write_skill(home / "skills" / ".system" / "bundled")
    write_skill(machine.project / ".agents" / "skills" / "repo-a")
    write_skill(machine.project / ".codex" / "skills" / "repo-b")
    write_skill(machine.system / "etc" / "codex" / "skills" / "admin")
    found = {r[2]: r[3] for r in rows(run(capsys)[1])}
    assert found == {
        "admin": "system",
        "bundled": "system",
        "legacy": "user",
        "nested": "user",
        "no-name": "user",
        "repo-a": "project",
        "repo-b": "project",
        "user-skill": "user",
    }


def test_codex_depth_limit(machine, capsys):
    codex(machine)
    path = machine.home / ".agents" / "skills"
    for level in range(1, 9):
        path = path / f"l{level}"
        write_skill(path)
    assert {r[2] for r in rows(run(capsys)[1])} == {f"l{i}" for i in range(1, 7)}


def test_codex_required_description(machine, capsys):
    codex(machine)
    agents = machine.home / ".agents" / "skills"
    write_skill(agents / "no-desc", "name: no-desc")
    (agents / "no-fm").mkdir()
    (agents / "no-fm" / "SKILL.md").write_text("none")
    assert {r[2]: r[4] for r in rows(run(capsys)[1])} == {
        "no-desc": "broken",
        "no-fm": "broken",
    }


def test_codex_disable_requires_skill_file_path(machine, capsys):
    home = codex(machine)
    agents = machine.home / ".agents" / "skills"
    by_file = write_skill(agents / "by-file")
    by_dir = write_skill(agents / "by-dir")
    (home / "config.toml").write_text(
        f'[[skills.config]]\npath = "{by_file}/SKILL.md"\nenabled = false\n'
        f'[[skills.config]]\npath = "{by_dir}"\nenabled = false\n'
    )
    assert {r[2]: r[4] for r in rows(run(capsys)[1])} == {
        "by-dir": "enabled",
        "by-file": "disabled",
    }


def test_project_scope_not_duplicated_when_cwd_is_home(machine, capsys, monkeypatch):
    write_skill(claude(machine) / "skills" / "one")
    monkeypatch.chdir(machine.home)
    assert [(r[2], r[3]) for r in rows(run(capsys)[1])] == [("one", "user")]


def test_broken_config_warns_and_leaves_other_agents_intact(machine, capsys):
    home = claude(machine)
    write_skill(home / "skills" / "one")
    (home / "settings.json").write_text("{ not json")
    codex_home = codex(machine)
    write_skill(codex_home / "skills" / "two")
    code, out, err = run(capsys)
    assert code == 0
    assert {r[2]: r[4] for r in rows(out)} == {"one": "unknown", "two": "enabled"}
    assert "warning: claude-code:" in err and "settings.json" in err


def test_broken_codex_toml_warns(machine, capsys):
    home = codex(machine)
    write_skill(home / "skills" / "one")
    (home / "config.toml").write_text("not = = toml")
    code, out, err = run(capsys)
    assert code == 0
    assert rows(out)[0][4] == "unknown"
    assert "config.toml" in err


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores permissions")
def test_unreadable_directory_warns_but_missing_does_not(machine, capsys):
    skills = claude(machine) / "skills"
    skills.mkdir(parents=True)
    assert run(capsys)[2] == ""
    skills.chmod(0)
    try:
        code, _, err = run(capsys)
    finally:
        skills.chmod(0o755)
    assert code == 0
    assert "cannot read" in err


def test_hostile_content_cannot_control_the_terminal(machine, capsys):
    skills = claude(machine) / "skills"
    write_skill(skills / "evil", 'name: "\\x1b"')
    (skills / "evil" / "SKILL.md").write_text(
        "---\nname: a\x1b[31mred\x1b]0;title\x07b\u202ec\n---\n"
    )
    (skills / "tab\x1b[2Jdir").mkdir()
    (skills / "tab\x1b[2Jdir" / "SKILL.md").write_text("x")
    code, out, _ = run(capsys, "--detail")
    assert code == 0
    assert "\x1b" not in out and "\x07" not in out and "\u202e" not in out
    assert "aredbc" in out


def test_non_regular_skill_file_is_broken_and_does_not_hang(machine, capsys):
    skill = claude(machine) / "skills" / "fifo"
    skill.mkdir(parents=True)
    os.mkfifo(skill / "SKILL.md")
    assert rows(run(capsys)[1])[0][2:5] == ("fifo", "user", "broken")


def test_symlink_cycle_terminates(machine, capsys):
    codex(machine)
    agents = machine.home / ".agents" / "skills"
    write_skill(agents / "real")
    (agents / "real" / "loop").symlink_to(agents)
    assert [r[2] for r in rows(run(capsys)[1])] == ["real"]


def test_never_executes_agent_binaries(machine, capsys):
    machine.install("claude", "codex")
    run(capsys, "--all")
    assert not machine.executed


def test_no_color_when_piped(machine, capsys):
    write_skill(claude(machine) / "skills" / "one")
    assert "\x1b" not in run(capsys)[1]


@pytest.mark.parametrize(("alias", "agent"), [("claude", "claude-code"), ("agy", "antigravity")])
@pytest.mark.parametrize("flag", ["-a", "--agent"])
def test_agent_option_accepts_the_binary_name(machine, capsys, alias, agent, flag):
    assert main([flag, alias]) == 0
    aliased = capsys.readouterr().out
    assert main([flag, agent]) == 0
    assert aliased == capsys.readouterr().out
    assert aliased.startswith(agent)


def test_agent_alias_combines_with_other_agents(machine, capsys):
    assert main(["-a", "claude", "-a", "codex", "-a", "claude-code"]) == 0
    out = capsys.readouterr().out
    assert out.count("claude-code") == 1 and "codex" in out
