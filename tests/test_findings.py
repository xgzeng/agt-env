import itertools
import json
import os
import re

import pytest
from conftest import assert_broken_items_say_why, write_skill

from agt_view import cli
from agt_view.cli import main
from agt_view.model import AgentReport, Finding, Item, Kind, Scope, Status

SECRET = "hunter2-do-not-print"
STDIO = {"command": "/bin/true"}
COLOR_CODE = re.compile(r"\x1b\[[0-9;]*m")


def run(capsys, *args):
    assert main(list(args)) == 0
    return capsys.readouterr().out


def rows(capsys, *args):
    """Return {(first column, name): status}, in order. The same key twice keeps the last."""
    found = {}
    for line in run(capsys, *args).splitlines():
        parts = line.split()
        if line.startswith("  ") and parts and re.match(r"[PUS]/", parts[0]):
            found[(parts[0], parts[1])] = parts[2]
    return found


def findings(capsys, *args):
    """Return each finding as `<first column> <name>: <text>`, using the row above it."""
    found, label = [], ""
    for line in run(capsys, *args).splitlines():
        parts = line.split()
        if line.startswith("  ") and parts and re.match(r"[PUS]/", parts[0]):
            label = f"{parts[0]} {parts[1]}"
        elif parts and parts[0] == "!":
            found.append(f"{label}: {line.strip()[2:]}")
    return found


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def named(name, description="d"):
    return f"name: {name}\ndescription: {description}"


@pytest.fixture
def claude(machine):
    machine.install("claude")
    (machine.home / ".claude").mkdir()
    machine.skills = machine.home / ".claude" / "skills"
    machine.project_skills = machine.project / ".claude" / "skills"
    return machine


@pytest.fixture
def codex(machine):
    machine.install("codex")
    (machine.home / ".codex").mkdir()
    (machine.project / ".codex").mkdir()
    machine.config = machine.home / ".codex" / "config.toml"
    machine.project_config = machine.project / ".codex" / "config.toml"
    machine.trust = f'[projects."{machine.project}"]\ntrust_level = "trusted"\n'
    return machine


@pytest.fixture
def pi(machine):
    machine.install("pi")
    machine.agent = machine.home / ".pi" / "agent"
    machine.agent.mkdir(parents=True)
    write_json(machine.agent / "settings.json", {"defaultProjectTrust": "always"})
    write_json(machine.agent / "trust.json", {str(machine.project): True})
    return machine


@pytest.fixture
def opencode(machine):
    machine.install("opencode")
    machine.config = machine.home / ".config" / "opencode"
    machine.config.mkdir(parents=True)
    return machine


@pytest.fixture
def agy(machine):
    machine.install("agy")
    (machine.home / ".gemini" / "antigravity-cli").mkdir(parents=True)
    write_json(
        machine.home / ".gemini" / "antigravity-cli" / "settings.json",
        {"trustedWorkspaces": [str(machine.project)]},
    )
    return machine


# Why an item is broken


def test_dangling_symlink_says_so(claude, capsys):
    claude.skills.mkdir()
    (claude.skills / "old").symlink_to(claude.home / "gone")
    assert findings(capsys) == ["U/skill old: symlink target is missing"]


def test_unreadable_skill_file_says_so(claude, capsys):
    (claude.skills / "pipe").mkdir(parents=True)
    os.mkfifo(claude.skills / "pipe" / "SKILL.md")
    assert findings(capsys) == ["U/skill pipe: SKILL.md can't be read"]


def test_link_to_non_markdown_says_so(claude, capsys):
    (claude.home / "notes.txt").write_text("x")
    (claude.skills / "link").mkdir(parents=True)
    (claude.skills / "link" / "SKILL.md").symlink_to(claude.home / "notes.txt")
    assert findings(capsys) == ["U/skill link: not read: links to a non-Markdown file"]


def test_claude_refused_name_says_so(claude, capsys):
    write_skill(claude.skills / "x", "name: anthropic-skills\ndescription: d")
    assert findings(capsys) == ["U/skill anthropic-skills: Claude Code refuses this name"]


def test_codex_missing_description_says_so(machine, capsys):
    machine.install("codex")
    (machine.home / ".codex").mkdir()
    write_skill(machine.home / ".agents" / "skills" / "x", "name: x")
    assert findings(capsys) == ["U/skill x: has no description, which Codex requires"]


def test_pi_missing_description_says_so(pi, capsys):
    write_skill(pi.agent / "skills" / "x", "name: x")
    assert findings(capsys) == ["U/skill x: has no description, which Pi requires"]


def test_antigravity_missing_frontmatter_says_so(agy, capsys):
    skill = agy.home / ".gemini" / "antigravity-cli" / "skills" / "x"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("just text\n")
    assert findings(capsys) == ["U/skill x: has no frontmatter, which Antigravity CLI requires"]


def test_invalid_server_says_so(claude, capsys):
    write_json(claude.home / ".claude.json", {"mcpServers": {"bad": {"args": []}}})
    assert findings(capsys) == ["U/mcp bad: invalid server entry"]


def test_missing_plugin_files_say_so(claude, capsys):
    write_json(
        claude.home / ".claude" / "plugins" / "installed_plugins.json",
        {"plugins": {"p@m": [{"scope": "user", "installPath": str(claude.home / "gone")}]}},
    )
    assert findings(capsys) == ["U/plugin p@m: plugin files are missing"]


def test_codex_enabled_plugin_with_no_files_says_so(codex, capsys):
    codex.config.write_text('[plugins."p@m"]\nenabled = true\n')
    assert findings(capsys) == ["U/plugin p@m: plugin files are missing"]


def test_broken_items_must_have_a_reason(tmp_path):
    # Every scan in every test runs this check. Prove that it can fail.
    item = Item("x", Kind.SKILL, "skill", "a", Scope.USER, Status.BROKEN, "", tmp_path, tmp_path)
    report = AgentReport("a", True, tmp_path, items=[item])
    with pytest.raises(AssertionError):
        assert_broken_items_say_why(report)
    item.findings.append(Finding("a reason"))
    assert_broken_items_say_why(report)


# Shadowing: Claude Code


def test_claude_personal_skill_beats_project(claude, capsys):
    write_skill(claude.skills / "lint", named("lint"))
    write_skill(claude.project_skills / "lint", named("lint"))
    assert rows(capsys) == {("P/skill", "lint"): "shadowed", ("U/skill", "lint"): "enabled"}
    assert findings(capsys) == ["P/skill lint: shadowed by ~/.claude/skills/lint"]


def test_claude_enterprise_skill_beats_personal(claude, capsys):
    write_skill(claude.claude_managed / ".claude" / "skills" / "lint", named("lint"))
    write_skill(claude.skills / "lint", named("lint"))
    assert rows(capsys) == {("U/skill", "lint"): "shadowed", ("S/skill", "lint"): "enabled"}


def test_claude_broken_skill_does_not_shadow(claude, capsys):
    claude.skills.mkdir()
    (claude.skills / "lint").symlink_to(claude.home / "gone")
    write_skill(claude.project_skills / "lint", named("lint"))
    assert rows(capsys) == {("P/skill", "lint"): "enabled", ("U/skill", "lint"): "broken"}


def test_claude_disabled_skill_is_not_called_shadowed(claude, capsys):
    write_skill(claude.skills / "lint", named("lint"))
    write_skill(claude.project_skills / "lint", named("lint"))
    write_json(claude.home / ".claude" / "settings.json", {"skillOverrides": {"lint": "off"}})
    assert rows(capsys) == {("P/skill", "lint"): "disabled", ("U/skill", "lint"): "disabled"}


def claude_state(machine, *, user=None, local=None, **project_state):
    write_json(
        machine.home / ".claude.json",
        {
            "mcpServers": user or {},
            "projects": {
                str(machine.project): {
                    "hasTrustDialogAccepted": True,
                    "mcpServers": local or {},
                    **project_state,
                }
            },
        },
    )


def test_claude_local_server_beats_project_and_user(claude, capsys):
    claude_state(claude, user={"s": STDIO}, local={"s": STDIO}, enabledMcpjsonServers=["s"])
    write_json(claude.project / ".mcp.json", {"mcpServers": {"s": STDIO}})
    assert findings(capsys) == [
        "P/mcp s: shadowed by ~/.claude.json",
        "U/mcp s: shadowed by ~/.claude.json",
    ]


def test_claude_approved_project_server_beats_user(claude, capsys):
    claude_state(claude, user={"s": STDIO}, enabledMcpjsonServers=["s"])
    write_json(claude.project / ".mcp.json", {"mcpServers": {"s": STDIO}})
    assert rows(capsys) == {("P/mcp", "s"): "enabled", ("U/mcp", "s"): "shadowed"}


def test_claude_pending_project_server_does_not_shadow(claude, capsys):
    claude_state(claude, user={"s": STDIO})
    write_json(claude.project / ".mcp.json", {"mcpServers": {"s": STDIO}})
    assert rows(capsys) == {("P/mcp", "s"): "pending", ("U/mcp", "s"): "enabled"}


# Shadowing: Codex


def test_codex_duplicate_skills_all_load(codex, capsys):
    write_skill(codex.home / ".agents" / "skills" / "a", named("dup"))
    write_skill(codex.project / ".agents" / "skills" / "b", named("dup"))
    assert rows(capsys) == {("P/skill", "dup"): "enabled", ("U/skill", "dup"): "enabled"}


def test_codex_trusted_project_server_replaces_the_user_one(codex, capsys):
    codex.config.write_text(f'[mcp_servers.s]\ncommand = "/bin/true"\n{codex.trust}')
    codex.project_config.write_text('[mcp_servers.s]\ncommand = "/bin/false"\n')
    assert rows(capsys) == {("P/mcp", "s"): "enabled", ("U/mcp", "s"): "shadowed"}


def test_codex_project_server_can_turn_the_user_one_off(codex, capsys):
    codex.config.write_text(f'[mcp_servers.s]\ncommand = "/bin/true"\n{codex.trust}')
    codex.project_config.write_text("[mcp_servers.s]\nenabled = false\n")
    assert rows(capsys) == {("P/mcp", "s"): "disabled", ("U/mcp", "s"): "shadowed"}


def test_codex_project_entry_inherits_the_user_fields(codex, capsys):
    codex.config.write_text(
        f'[mcp_servers.off]\ncommand = "/bin/true"\nenabled = false\n'
        f'[mcp_servers.env]\ncommand = "/bin/true"\n{codex.trust}'
    )
    codex.project_config.write_text(
        '[mcp_servers.off]\ncommand = "/bin/false"\n[mcp_servers.env.env]\nFROM_PROJECT = "x"\n'
    )
    out = run(capsys, "-s", "project", "--detail")
    # Without the user's command, the `env` entry alone would be invalid.
    assert "broken" not in out
    assert re.search(r"Command: +/bin/true", out) and re.search(r"Command: +/bin/false", out)
    assert rows(capsys, "-s", "project") == {
        ("P/mcp", "off"): "disabled",
        ("P/mcp", "env"): "enabled",
    }


def test_codex_untrusted_project_changes_nothing(codex, capsys):
    codex.config.write_text('[mcp_servers.s]\ncommand = "/bin/true"\n')
    codex.project_config.write_text('[mcp_servers.s]\ncommand = "/bin/false"\n')
    assert rows(capsys) == {("P/mcp", "s"): "pending", ("U/mcp", "s"): "enabled"}


# Shadowing: Pi


def pi_sources(pi):
    return {
        "agent": pi.agent / "skills",
        "user-agents": pi.home / ".agents" / "skills",
        "pi": pi.project / ".pi" / "skills",
        "project-agents": pi.project / ".agents" / "skills",
    }


@pytest.mark.parametrize(
    ("winner", "loser", "expected"),
    [
        ("agent", "user-agents", "U/skill x: shadowed by ~/.pi/agent/skills/x"),
        ("pi", "project-agents", "P/skill x: shadowed by ./.pi/skills/x"),
        ("pi", "agent", "U/skill x: shadowed by ./.pi/skills/x"),
        ("project-agents", "agent", "U/skill x: shadowed by ./.agents/skills/x"),
        ("project-agents", "user-agents", "U/skill x: shadowed by ./.agents/skills/x"),
    ],
)
def test_pi_skill_precedence(pi, capsys, winner, loser, expected):
    sources = pi_sources(pi)
    write_skill(sources[winner] / "x", named("x"))
    write_skill(sources[loser] / "x", named("x"))
    assert findings(capsys) == [expected]


def test_pi_listed_skills_rank_by_the_settings_they_came_from(pi, capsys, tmp_path):
    sources = pi_sources(pi)
    write_skill(tmp_path / "user-list" / "x", named("x"))
    write_skill(tmp_path / "project-list" / "y", named("y"))
    for name in ("x", "y"):
        write_skill(sources["agent"] / name, named(name))
        write_skill(sources["pi"] / name, named(name))
    write_json(
        pi.agent / "settings.json",
        {"defaultProjectTrust": "always", "skills": [str(tmp_path / "user-list")]},
    )
    write_json(pi.project / ".pi" / "settings.json", {"skills": [str(tmp_path / "project-list")]})
    out = run(capsys, "--detail")
    # A user-listed path loses to .pi/skills, and a project-listed one beats it.
    assert out.count("Finding:      shadowed by ./.pi/skills/x") == 2
    assert out.count(f"Finding:      shadowed by {tmp_path}/project-list/y") == 2


def test_pi_matches_by_name_not_directory(pi, capsys):
    sources = pi_sources(pi)
    write_skill(sources["agent"] / "one", named("same"))
    write_skill(sources["pi"] / "two", named("same"))
    assert rows(capsys) == {("P/skill", "same"): "enabled", ("U/skill", "same"): "shadowed"}


def test_pi_invalid_skill_does_not_shadow(pi, capsys):
    sources = pi_sources(pi)
    write_skill(sources["agent"] / "x", named("x"))
    write_skill(sources["pi"] / "x", "name: x")
    assert rows(capsys) == {("P/skill", "x"): "broken", ("U/skill", "x"): "enabled"}


@pytest.mark.parametrize("pattern", ["!**/gone/**", "!gone", "!**/gone"])
def test_pi_excluded_skill_does_not_take_the_name(pi, capsys, pattern):
    sources = pi_sources(pi)
    write_skill(sources["agent"] / "gone", named("x"))
    write_skill(sources["user-agents"] / "lower", named("x"))
    write_json(pi.agent / "settings.json", {"skills": [pattern]})
    assert findings(capsys) == []
    out = run(capsys)
    assert out.count(" disabled") == 1 and out.count(" enabled") == 1


@pytest.mark.parametrize(
    ("settings", "excluded", "kept"),
    [("user", "agent", "pi"), ("user", "user-agents", "project-agents")],
)
def test_pi_user_exclusions_only_reach_user_skills(pi, capsys, settings, excluded, kept):
    sources = pi_sources(pi)
    write_skill(sources[excluded] / "victim", named("a"))
    write_skill(sources[kept] / "victim", named("b"))
    write_json(pi.agent / "settings.json", {"defaultProjectTrust": "always", "skills": ["!victim"]})
    assert rows(capsys) == {("P/skill", "b"): "enabled", ("U/skill", "a"): "disabled"}


def test_pi_exclusions_only_reach_extensions_of_their_scope(pi, capsys):
    (pi.agent / "extensions").mkdir()
    (pi.agent / "extensions" / "victim.ts").write_text("x")
    (pi.project / ".pi" / "extensions").mkdir(parents=True)
    (pi.project / ".pi" / "extensions" / "victim.ts").write_text("x")
    write_json(
        pi.agent / "settings.json", {"defaultProjectTrust": "always", "extensions": ["!victim*"]}
    )
    assert rows(capsys) == {("P/plugin", "victim"): "enabled", ("U/plugin", "victim"): "disabled"}


def test_pi_project_exclusions_only_reach_project_skills(pi, capsys):
    sources = pi_sources(pi)
    write_skill(sources["agent"] / "victim", named("a"))
    write_skill(sources["pi"] / "victim", named("b"))
    write_json(pi.project / ".pi" / "settings.json", {"skills": ["!victim"]})
    assert rows(capsys) == {("P/skill", "b"): "disabled", ("U/skill", "a"): "enabled"}


@pytest.mark.parametrize("decision", [False, None])
def test_pi_untrusted_project_skill_does_not_shadow(pi, capsys, decision):
    sources = pi_sources(pi)
    write_skill(sources["agent"] / "x", named("x"))
    write_skill(sources["pi"] / "x", named("x"))
    write_json(pi.agent / "settings.json", {})
    write_json(pi.agent / "trust.json", {} if decision is None else {str(pi.project): decision})
    assert rows(capsys)[("U/skill", "x")] == "enabled"


def test_pi_project_server_replaces_the_user_one(pi, capsys):
    write_json(pi.agent / "mcp.json", {"mcpServers": {"s": STDIO}})
    write_json(pi.project / ".pi" / "mcp.json", {"mcpServers": {"s": {"command": "/bin/false"}}})
    assert rows(capsys) == {("P/mcp", "s"): "enabled", ("U/mcp", "s"): "shadowed"}


def test_pi_disabled_project_server_still_replaces_the_user_one(pi, capsys):
    write_json(pi.agent / "mcp.json", {"mcpServers": {"s": STDIO}})
    write_json(
        pi.project / ".pi" / "mcp.json",
        {"mcpServers": {"s": {"command": "/bin/false", "enabled": False}}},
    )
    assert rows(capsys) == {("P/mcp", "s"): "disabled", ("U/mcp", "s"): "shadowed"}


# Shadowing: OpenCode


def opencode_sources(machine):
    return {
        "config": machine.home / "cfg-skills",
        "opencode": machine.project / ".opencode" / "skills",
        "global": machine.config / "skills",
        "project-agents": machine.project / ".agents" / "skills",
        "user-agents": machine.home / ".agents" / "skills",
        "project-claude": machine.project / ".claude" / "skills",
        "user-claude": machine.home / ".claude" / "skills",
    }


def test_opencode_skill_precedence(opencode, capsys):
    sources = opencode_sources(opencode)
    write_json(opencode.config / "opencode.json", {"skills": [str(sources["config"])]})
    order = list(sources)
    for high, low in itertools.pairwise(order):
        write_skill(sources[high] / f"{high}-{low}", named(f"{high}-{low}"))
        write_skill(sources[low] / f"{high}-{low}", named(f"{high}-{low}"))
    lines = findings(capsys, "-a", "opencode")
    assert len(lines) == len(order) - 1
    assert all("shadowed by" in line for line in lines)
    for high, low in itertools.pairwise(order):
        path = sources[high] / f"{high}-{low}"
        expected = str(path).replace(str(opencode.project), ".").replace(str(opencode.home), "~")
        assert any(f"{high}-{low}: shadowed by {expected}" in line for line in lines)


def test_opencode_matches_by_id_not_name(opencode, capsys):
    sources = opencode_sources(opencode)
    # Same directory, different names: one wins.
    write_skill(sources["opencode"] / "samedir", named("name-a"))
    write_skill(sources["global"] / "samedir", named("name-b"))
    # Same name, different directories: both load.
    write_skill(sources["opencode"] / "dir-1", named("same-name"))
    write_skill(sources["global"] / "dir-2", named("same-name"))
    assert rows(capsys) == {
        ("P/skill", "name-a"): "enabled",
        ("P/skill", "same-name"): "enabled",
        ("U/skill", "name-b"): "shadowed",
        ("U/skill", "same-name"): "enabled",
    }


def test_opencode_standalone_file_counts_as_the_same_skill(opencode, capsys):
    sources = opencode_sources(opencode)
    sources["opencode"].mkdir(parents=True)
    (sources["opencode"] / "x.md").write_text(f"---\n{named('file')}\n---\n")
    write_skill(sources["global"] / "x", named("dir"))
    assert rows(capsys) == {("P/skill", "file"): "enabled", ("U/skill", "dir"): "shadowed"}


def test_opencode_within_a_source(opencode, capsys):
    skills = opencode_sources(opencode)["global"]
    write_skill(skills / "a" / "nest", named("first"))
    write_skill(skills / "b" / "nest", named("second"))
    write_skill(skills / "mix", named("directory"))
    (skills / "mix.md").write_text(f"---\n{named('file')}\n---\n")
    write_skill(opencode.config / "skill" / "sing", named("singular"))
    write_skill(skills / "sing", named("plural"))
    assert rows(capsys) == {
        ("U/skill", "directory"): "enabled",
        ("U/skill", "file"): "shadowed",
        ("U/skill", "first"): "shadowed",
        ("U/skill", "plural"): "enabled",
        ("U/skill", "second"): "enabled",
        ("U/skill", "singular"): "shadowed",
    }


def test_opencode_dangling_link_does_not_shadow(opencode, capsys):
    sources = opencode_sources(opencode)
    sources["opencode"].mkdir(parents=True)
    (sources["opencode"] / "x").symlink_to(opencode.home / "gone")
    write_skill(sources["global"] / "x", named("x"))
    assert rows(capsys) == {("P/skill", "x"): "broken", ("U/skill", "x"): "enabled"}


def test_opencode_server_override_is_reported_on_the_winner(opencode, capsys):
    server = {"type": "local", "command": ["/bin/true"]}
    write_json(opencode.config / "opencode.json", {"mcp": {"s": server}})
    write_json(opencode.project / "opencode.json", {"mcp": {"s": server}})
    assert findings(capsys) == ["P/mcp s: overrides the entry in ~/.config/opencode/opencode.json"]


# Shadowing: Antigravity


def test_antigravity_is_never_flagged(agy, capsys):
    write_skill(agy.home / ".gemini" / "antigravity-cli" / "skills" / "x", named("x"))
    write_skill(agy.project / ".agents" / "skills" / "x", named("x"))
    write_json(agy.home / ".gemini" / "config" / "mcp_config.json", {"mcpServers": {"s": STDIO}})
    write_json(agy.project / ".agents" / "mcp_config.json", {"mcpServers": {"s": STDIO}})
    assert "shadowed" not in run(capsys)


# Between agents


def test_same_named_skills_in_different_agents_are_not_compared(machine, capsys):
    # A shared name doesn't show a shared origin, so a difference between them means nothing.
    machine.install("claude", "pi")
    (machine.home / ".pi" / "agent").mkdir(parents=True)
    write_skill(machine.home / ".claude" / "skills" / "lint", named("lint", "one"))
    write_skill(machine.home / ".pi" / "agent" / "skills" / "lint", named("lint", "two"))
    assert findings(capsys) == []


# Output


def test_no_finding_rows_without_findings(claude, capsys):
    write_skill(claude.skills / "fine", named("fine"))
    assert run(capsys).splitlines() == [
        "claude-code  ~/.claude",
        "  SCOPE/KIND  NAME  STATUS",
        "  U/skill     fine  enabled",
    ]


def test_findings_are_rows_below_their_item(claude, capsys):
    claude.skills.mkdir()
    (claude.skills / "old").symlink_to(claude.home / "gone")
    write_skill(claude.skills / "fine", named("fine"))
    write_skill(claude.skills / "lint", named("lint", "one"))
    write_skill(claude.project_skills / "lint", named("lint", "two"))
    assert run(capsys).splitlines() == [
        "claude-code  ~/.claude",
        "  SCOPE/KIND  NAME  STATUS",
        "  P/skill     lint  shadowed",
        "              ! shadowed by ~/.claude/skills/lint",
        "  U/skill     fine  enabled",
        "  U/skill     lint  enabled",
        "  U/skill     old   broken",
        "              ! symlink target is missing",
    ]


def test_long_findings_do_not_widen_the_columns(claude, capsys):
    claude.skills.mkdir()
    (claude.skills / "a").symlink_to(claude.home / "gone")
    assert run(capsys).splitlines()[1] == "  SCOPE/KIND  NAME  STATUS"


def test_filters_select_which_findings_show(claude, capsys):
    claude.skills.mkdir()
    (claude.skills / "old").symlink_to(claude.home / "gone")
    (claude.skills / "older").symlink_to(claude.home / "gone")
    write_skill(claude.skills / "fine", named("fine"))
    assert findings(capsys, "-n", "fine") == []
    assert findings(capsys, "-n", "older") == ["U/skill older: symlink target is missing"]


def test_detail_shows_findings_as_fields(claude, capsys):
    write_skill(claude.skills / "lint", named("lint", "one"))
    write_skill(claude.project_skills / "lint", named("lint", "two"))
    out = run(capsys, "--detail", "-s", "project")
    assert out.splitlines() == [
        "claude-code  ~/.claude",
        "SCOPE/KIND  NAME  STATUS",
        "--------------------------",
        "P/skill     lint  shadowed",
        "            Description:  two",
        "            Path:         ./.claude/skills/lint",
        "            Finding:      shadowed by ~/.claude/skills/lint",
    ]


@pytest.mark.parametrize("keyword", ["shadowed", "symlink target"])
def test_findings_are_searchable(claude, capsys, keyword):
    write_skill(claude.skills / "lint", named("lint", "one"))
    write_skill(claude.project_skills / "lint", named("lint", "two"))
    (claude.skills / "old").symlink_to(claude.home / "gone")
    write_skill(claude.skills / "fine", named("fine"))
    names = {name for _, name in rows(capsys, keyword)}
    assert "fine" not in names and names


def test_a_broken_item_still_exits_zero(claude, capsys):
    claude.skills.mkdir()
    (claude.skills / "old").symlink_to(claude.home / "gone")
    assert main([]) == 0


@pytest.fixture
def rich_mode(monkeypatch):
    monkeypatch.setattr(cli, "_use_rich", lambda no_color_flag: True)
    monkeypatch.setenv("COLUMNS", "100")
    monkeypatch.setenv("TERM", "xterm")


def test_rich_shows_the_same_findings(claude, rich_mode, capsys):
    claude.skills.mkdir()
    (claude.skills / "old").symlink_to(claude.home / "gone")
    write_skill(claude.skills / "lint", named("lint"))
    write_skill(claude.project_skills / "lint", named("lint"))
    raw = run(capsys)
    out = COLOR_CODE.sub("", raw)
    assert "◌ shadowed" in out
    lines = out.splitlines()
    name_column = next(line for line in lines if "SCOPE/KIND" in line).index("NAME")
    for text in ("! shadowed by ~/.claude/skills/lint", "! symlink target is missing"):
        line = next(line for line in lines if text in line)
        assert line.index(text) == name_column
        # Directly below its item.
        assert lines[lines.index(line) - 1].split()[1] in ("P/skill", "U/skill")
    assert "\x1b[1;31m! " in raw and "\x1b[1;33m! " in raw
    # Inside the panel: nothing printed after the bottom border.
    assert out.rstrip().endswith("╯")


def test_rich_detail_shows_findings_under_the_item(claude, rich_mode, capsys):
    claude.skills.mkdir()
    (claude.skills / "old").symlink_to(claude.home / "gone")
    out = COLOR_CODE.sub("", run(capsys, "--detail"))
    assert "Finding" in out and "symlink target is missing" in out
    assert "! " not in out


# Safety


@pytest.mark.parametrize("plain", [True, False])
def test_hostile_text_in_findings_is_cleaned(claude, capsys, monkeypatch, plain):
    if not plain:
        monkeypatch.setattr(cli, "_use_rich", lambda no_color_flag: True)
        monkeypatch.setenv("COLUMNS", "200")
    name = "ev\\x1b[2Jil[red]x[/red]\\u202e"
    write_skill(claude.skills / "a[red]b", f'name: "{name}"\ndescription: d')
    write_skill(claude.project_skills / "other", f'name: "{name}"\ndescription: e')
    out = COLOR_CODE.sub("", run(capsys))
    assert "\x07" not in out and "\u202e" not in out and "\x1b" not in out
    assert "[red]x[/red]" in out and "a[red]b" not in out.split("!")[0]
    assert "~/.claude/skills/a[red]b" in out


def test_findings_do_not_print_file_contents(claude, capsys):
    (claude.home / "secret.env").write_text(f"TOKEN={SECRET}\n")
    skill = claude.project_skills / "evil"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").symlink_to(claude.home / "secret.env")
    captured = run(capsys, "--detail")
    assert SECRET not in captured
    assert "not read: links to a non-Markdown file" in captured
