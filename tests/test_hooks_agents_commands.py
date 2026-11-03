"""Hooks, subagents, and commands. The oracle tests check these rules against real agents."""

import json
import re

import pytest
from conftest import write_skill

from agt_env import cli
from agt_env.cli import main

SECRET = "hunter2-do-not-print"
COLOR_CODE = re.compile(r"\x1b\[[0-9;]*m")


def run(capsys, *args):
    assert main(list(args)) == 0
    return capsys.readouterr().out


def rows(capsys, *args):
    found = {}
    for line in run(capsys, *args).splitlines():
        parts = line.split()
        if line.startswith("  ") and parts and re.match(r"[PUS]/", parts[0]):
            found[(parts[0], parts[1])] = parts[2]
    return found


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def write_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def agent_file(name, description="d"):
    return f"---\nname: {name}\ndescription: {description}\n---\nBody\n"


def hook(command="/bin/true", **fields):
    return {"type": "command", "command": command, **fields}


def stop(*handlers, matcher=None):
    group = {"hooks": list(handlers)}
    if matcher:
        group["matcher"] = matcher
    return {"hooks": {"Stop": [group]}}


@pytest.fixture
def claude(machine):
    machine.install("claude")
    machine.dir = machine.home / ".claude"
    machine.dir.mkdir()
    machine.settings = machine.dir / "settings.json"
    machine.state = machine.home / ".claude.json"
    return machine


def trust(machine):
    write_json(
        machine.state, {"projects": {str(machine.project): {"hasTrustDialogAccepted": True}}}
    )


def test_hook_name_is_event_and_matcher(claude, capsys):
    write_json(claude.settings, stop(hook(), matcher="Bash"))
    assert rows(capsys, "-k", "hook") == {("U/hook", "Stop:Bash"): "enabled"}


def test_hook_description_is_what_it_runs(claude, capsys):
    write_json(claude.settings, stop(hook("bash ~/.claude/notify.sh --event stop")))
    out = run(capsys, "--detail")
    assert re.search(r"Description:\s+bash ~/.claude/notify.sh --event stop", out)
    assert re.search(r"Command:\s+bash", out)
    assert re.search(r"Event:\s+Stop", out) and re.search(r"Handler:\s+command", out)


def test_hook_description_is_one_line(claude, capsys):
    write_json(claude.settings, stop(hook("first\n  second\tthird")))
    assert re.search(r"Description:\s+first second third", run(capsys, "--detail"))


@pytest.mark.parametrize(
    ("command", "kept"),
    [
        (f"TOKEN={SECRET} /bin/run", "TOKEN=*** /bin/run"),
        (f"env A='{SECRET} x' /bin/run", "env A=*** /bin/run"),
        (f"/bin/notify --token {SECRET}", "/bin/notify --token ***"),
        (f"/bin/notify --api-key={SECRET} --now", "/bin/notify --api-key=*** --now"),
        (f'/bin/notify --password "{SECRET} x" --now', "/bin/notify --password *** --now"),
        (f'curl -H "Authorization: Bearer {SECRET}" u', 'curl -H "Authorization: ***" u'),
        (f"curl -H 'X-Api-Key: {SECRET}' u", "curl -H 'X-Api-Key: ***' u"),
        (f"curl -u admin:{SECRET} u", "curl -u *** u"),
        (f"curl https://u:{SECRET}@example.com/x", "curl https://example.com/x"),
        (f"curl https://example.com/x?key={SECRET}", "curl https://example.com/x?***"),
        ("run ghp_abcdefghijklmnop", "run ***"),
        ("run sk-abcdefghijklmnop", "run ***"),
        ("run AKIAABCDEFGHIJKLMNOP", "run ***"),
        ("run " + "a1B2" * 10, "run ***"),
    ],
)
def test_hook_description_masks_what_looks_like_a_secret(claude, capsys, command, kept):
    write_json(claude.settings, stop(hook(command)))
    out = run(capsys, "--detail")
    assert re.search(rf"Description:\s+{re.escape(kept)}", out)
    assert SECRET not in out
    assert not rows(capsys, SECRET)


@pytest.mark.parametrize(
    "command",
    [
        "/home/me/some-really-long-directory-name-for-hooks/run.sh",
        "bash -c 'echo done'",
        "python3 scripts/check_formatting_of_changed_files_now.py",
    ],
)
def test_hook_description_leaves_ordinary_commands_alone(claude, capsys, command):
    write_json(claude.settings, stop(hook(command)))
    assert re.search(rf"Description:\s+{re.escape(command)}", run(capsys, "--detail"))


def test_masking_cannot_recognize_a_bare_secret(claude, capsys):
    """The known limit, recorded in FR-12: a secret can look like any other word."""
    write_json(claude.settings, stop(hook(f"/bin/run {SECRET}")))
    assert SECRET in run(capsys, "--detail")


@pytest.mark.parametrize("command", [f"TOKEN={SECRET} /bin/run", ""])
def test_hook_command_field_leaves_out_what_it_cannot_tell_is_safe(claude, capsys, command):
    write_json(claude.settings, stop(hook(command)))
    out = run(capsys, "--detail")
    assert SECRET not in out and "Command:" not in out


def test_rich_shows_hook_content_in_the_description_column(claude, capsys, monkeypatch):
    monkeypatch.setattr(cli, "_use_rich", lambda no_color_flag: True)
    monkeypatch.setenv("COLUMNS", "100")
    monkeypatch.setenv("TERM", "xterm")
    write_json(claude.settings, stop(hook(f"/bin/notify [red]x[/red] --token {SECRET}")))
    out = COLOR_CODE.sub("", run(capsys, "-k", "hook"))
    assert "/bin/notify [red]x[/red] --token ***" in out and SECRET not in out


def test_http_hook_summary_has_no_credentials(claude, capsys):
    write_json(
        claude.settings,
        stop({"type": "http", "url": f"https://u:{SECRET}@example.com/x?key={SECRET}"}),
    )
    out = run(capsys, "--detail")
    assert SECRET not in out and "https://example.com/x" in out


def test_prompt_hook_description_is_masked(claude, capsys):
    write_json(claude.settings, stop({"type": "prompt", "prompt": f"Check. token: {SECRET}"}))
    out = run(capsys, "--detail")
    assert re.search(r"Description:\s+Check. token: \*\*\*", out)
    assert SECRET not in out


def test_mcp_tool_hook_description_names_the_tool(claude, capsys):
    write_json(claude.settings, stop({"type": "mcp_tool", "server": "s", "tool": "t"}))
    assert re.search(r"Description:\s+s/t", run(capsys, "--detail"))


def test_project_hook_waits_for_trust(claude, capsys):
    write_json(claude.project / ".claude" / "settings.json", stop(hook()))
    assert rows(capsys, "-k", "hook") == {("P/hook", "Stop"): "pending"}
    trust(claude)
    assert rows(capsys, "-k", "hook") == {("P/hook", "Stop"): "enabled"}


def test_managed_settings_rule_hooks(claude, capsys):
    managed = claude.claude_managed / "managed-settings.json"
    write_json(claude.settings, stop(hook()))
    write_json(managed, {**stop(hook(), matcher="Managed"), "allowManagedHooksOnly": True})
    assert rows(capsys, "-k", "hook") == {
        ("U/hook", "Stop"): "disabled",
        ("S/hook", "Stop:Managed"): "enabled",
    }
    write_json(managed, {**stop(hook(), matcher="Managed"), "disableAllHooks": True})
    assert rows(capsys, "-k", "hook")[("S/hook", "Stop:Managed")] == "disabled"


def test_user_setting_does_not_turn_off_managed_hooks(claude, capsys):
    write_json(claude.settings, {"disableAllHooks": True})
    write_json(claude.claude_managed / "managed-settings.json", stop(hook()))
    assert rows(capsys, "-k", "hook") == {("S/hook", "Stop"): "enabled"}


def test_unknown_event_is_unknown_and_invalid_handler_is_broken(claude, capsys):
    write_json(
        claude.settings,
        {
            "hooks": {
                "SomethingNew": [{"hooks": [hook()]}],
                "Stop": [{"hooks": [{"type": "command"}]}],
            }
        },
    )
    out = run(capsys)
    assert re.search(r"U/hook\s+SomethingNew\s+unknown", out)
    assert re.search(r"U/hook\s+Stop\s+broken", out) and "! invalid hook entry" in out


def test_bad_settings_make_hooks_unknown(claude, capsys):
    write_json(claude.settings, stop(hook()))
    write_text(claude.project / ".claude" / "settings.json", "{broken")
    assert rows(capsys, "-k", "hook") == {("U/hook", "Stop"): "unknown"}


def test_agent_precedence_is_managed_project_user(claude, capsys):
    write_text(claude.claude_managed / ".claude" / "agents" / "x.md", agent_file("a"))
    write_text(claude.project / ".claude" / "agents" / "x.md", agent_file("a"))
    write_text(claude.dir / "agents" / "x.md", agent_file("a"))
    assert rows(capsys, "-k", "subagent") == {
        ("S/subagent", "a"): "enabled",
        ("P/subagent", "a"): "shadowed",
        ("U/subagent", "a"): "shadowed",
    }


def test_same_named_agents_in_one_directory_are_not_flagged(claude, capsys):
    write_text(claude.dir / "agents" / "x.md", agent_file("a"))
    write_text(claude.dir / "agents" / "y.md", agent_file("a"))
    assert "shadowed" not in run(capsys)


def test_agent_deny_rules(claude, capsys):
    write_text(claude.dir / "agents" / "x.md", agent_file("a"))
    write_text(claude.dir / "agents" / "y.md", agent_file("b"))
    write_json(claude.settings, {"permissions": {"deny": ["Agent(a)", "Skill(b)"]}})
    assert rows(capsys, "-k", "subagent") == {
        ("U/subagent", "a"): "disabled",
        ("U/subagent", "b"): "enabled",
    }
    write_json(claude.settings, {"permissions": {"deny": ["Agent"]}})
    assert set(rows(capsys, "-k", "subagent").values()) == {"disabled"}


@pytest.mark.parametrize(
    "text",
    [
        "Body\n",
        "---\ndescription: d\n---\n",
        "---\nname: x\n---\n",
    ],
)
def test_agent_files_it_refuses_are_broken(claude, capsys, text):
    write_text(claude.dir / "agents" / "bad.md", text)
    assert "! needs a name and a description, which Claude Code requires" in run(capsys)


@pytest.mark.parametrize("name", ["a:b", "-x", "x" * 257])
def test_agent_names_it_refuses_say_so(claude, capsys, name):
    write_text(claude.dir / "agents" / "bad.md", agent_file(name))
    out = run(capsys)
    assert "! Claude Code refuses this name" in out and "needs a name" not in out


def test_agent_link_to_a_credential_is_not_read(claude, capsys):
    (claude.home / "secret.env").write_text(f"name: {SECRET}\ndescription: {SECRET}\n")
    (claude.dir / "agents").mkdir()
    (claude.dir / "agents" / "evil.md").symlink_to(claude.home / "secret.env")
    (claude.dir / "commands").mkdir()
    (claude.dir / "commands" / "evil.md").symlink_to(claude.home / "secret.env")
    out = run(capsys, "--detail")
    assert SECRET not in out and out.count("not read: links to a non-Markdown file") == 2


def test_skill_beats_a_same_named_command(claude, capsys):
    write_skill(claude.dir / "skills" / "same", "description: d")
    write_text(claude.dir / "commands" / "same.md", "Body\n")
    out = run(capsys)
    assert re.search(r"U/command\s+same\s+shadowed", out)
    assert "! shadowed by ~/.claude/skills/same" in out
    assert re.search(r"U/skill\s+same\s+enabled", out)


def test_command_description_falls_back_to_the_body(claude, capsys):
    write_text(claude.dir / "commands" / "folder" / "plain.md", "First line\n")
    out = run(capsys, "--detail")
    assert re.search(r"U/command\s+folder:plain\n\s+Status:\s+enabled", out)
    assert re.search(r"Description:\s+First line", out)


@pytest.fixture
def plugin(claude):
    root = claude.dir / "plugins" / "cache" / "m" / "p" / "1.0.0"
    root.mkdir(parents=True)
    write_json(
        claude.dir / "plugins" / "installed_plugins.json",
        {"version": 2, "plugins": {"p@m": [{"scope": "user", "installPath": str(root)}]}},
    )
    write_json(claude.settings, {"enabledPlugins": {"p@m": True}})
    claude.plugin = root
    return claude


def test_plugin_definitions_name_their_plugin(plugin, capsys):
    write_text(plugin.plugin / "agents" / "folder" / "x.md", agent_file("a"))
    write_text(plugin.plugin / "agents" / "plain.md", "Body\n")
    write_text(plugin.plugin / "commands" / "folder" / "c.md", "Body\n")
    write_json(plugin.plugin / "hooks" / "hooks.json", stop(hook()))
    assert {key: status for key, status in rows(capsys).items() if "plugin" not in key[0]} == {
        ("U/subagent", "p:folder:a"): "enabled",
        ("U/subagent", "p:plain"): "enabled",
        ("U/command", "p:folder:c"): "enabled",
        ("U/hook", "Stop"): "enabled",
    }
    out = run(capsys, "--detail", "-k", "hook")
    assert re.search(r"Agent's term:\s+plugin hook", out) and re.search(r"Plugin:\s+p@m", out)


@pytest.mark.parametrize("key", ["agents", "commands", "hooks"])
def test_plugin_manifest_paths_stay_inside_the_plugin(plugin, capsys, key):
    outside = plugin.plugin.parent / "outside"
    write_text(outside / "x.md", agent_file("escaped"))
    write_json(outside / "x.json", stop(hook(f"/bin/{SECRET}")))
    target = "../outside/x.json" if key == "hooks" else "../outside/x.md"
    write_json(plugin.plugin / ".claude-plugin" / "plugin.json", {key: target})
    out = run(capsys, "--detail")
    assert "escaped" not in out and SECRET not in out


def test_plugin_inline_hooks_and_command_entries(plugin, capsys):
    write_json(
        plugin.plugin / ".claude-plugin" / "plugin.json",
        {
            "hooks": stop(hook(), matcher="Inline"),
            "commands": {"about": {"content": "Hi"}, "gone": {"source": "./missing.md"}},
        },
    )
    found = rows(capsys)
    assert found[("U/hook", "Stop:Inline")] == "enabled"
    assert found[("U/command", "p:about")] == "enabled"
    assert found[("U/command", "p:gone")] == "broken"


@pytest.fixture
def codex(machine):
    machine.install("codex")
    machine.dir = machine.home / ".codex"
    machine.dir.mkdir()
    machine.config = machine.dir / "config.toml"
    machine.hooks = machine.dir / "hooks.json"
    return machine


def test_codex_hook_description_is_masked(codex, capsys):
    write_json(codex.hooks, stop(hook(f"/bin/run --token {SECRET}")))
    out = run(capsys, "--detail")
    assert re.search(r"Description:\s+/bin/run --token \*\*\*", out) and SECRET not in out


def test_codex_hook_waits_for_review(codex, capsys):
    write_json(codex.hooks, stop(hook()))
    assert rows(capsys, "-k", "hook") == {("U/hook", "Stop"): "pending"}


def test_codex_hook_state(codex, capsys):
    from agt_env.adapters.codex import _hook_hash
    from agt_env.hooks import handlers

    write_json(codex.hooks, stop(hook()))
    fingerprint = _hook_hash(next(handlers(stop(hook())["hooks"])))
    key = f"{codex.hooks}:stop:0:0"
    codex.config.write_text(f'[hooks.state."{key}"]\ntrusted_hash = "{fingerprint}"\n')
    assert rows(capsys, "-k", "hook") == {("U/hook", "Stop"): "enabled"}

    write_json(codex.hooks, stop(hook("/bin/edited")))
    out = run(capsys)
    assert re.search(r"U/hook\s+Stop\s+pending", out)
    assert "! changed since it was trusted" in out

    codex.config.write_text(f'[hooks.state."{key}"]\nenabled = false\n')
    assert rows(capsys, "-k", "hook") == {("U/hook", "Stop"): "disabled"}


def test_codex_untrusted_project_hook_waits(codex, capsys):
    write_json(codex.project / ".codex" / "hooks.json", stop(hook()))
    assert rows(capsys, "-k", "hook") == {("P/hook", "Stop"): "pending"}


def test_codex_refused_project_hook_is_disabled(codex, capsys):
    codex.config.write_text(f'[projects."{codex.project}"]\ntrust_level = "untrusted"\n')
    write_json(codex.project / ".codex" / "hooks.json", stop(hook()))
    assert rows(capsys, "-k", "hook") == {("P/hook", "Stop"): "disabled"}


def test_codex_inline_hook_state_table_is_not_a_hook(codex, capsys):
    codex.config.write_text('[hooks.state."x"]\nenabled = false\n')
    assert rows(capsys, "-k", "hook") == {}


def test_codex_unsupported_handler_is_broken(codex, capsys):
    write_json(codex.hooks, stop({"type": "prompt", "prompt": "x"}))
    assert "! Codex doesn't support prompt hooks" in run(capsys)


def test_codex_bad_hooks_file_warns_and_lists_nothing(codex, capsys):
    write_json(codex.hooks, stop({"type": "command"}, hook()))
    assert main([]) == 0
    captured = capsys.readouterr()
    assert "/hook" not in captured.out and "invalid hook entry" in captured.err


def test_codex_agents(codex, capsys):
    write_text(
        codex.dir / "agents" / "good.toml",
        'name = "good"\ndescription = "d"\ndeveloper_instructions = "x"\n',
    )
    write_text(codex.dir / "agents" / "short.toml", 'name = "short"\n')
    write_text(codex.dir / "agents" / "bad.toml", "not toml [\n")
    write_text(codex.dir / "agents" / "notes.md", "ignored\n")
    write_text(
        codex.project / ".codex" / "agents" / "here.toml",
        'name = "here"\ndescription = "d"\ndeveloper_instructions = "x"\n',
    )
    assert main([]) == 0
    captured = capsys.readouterr()
    assert re.search(r"U/subagent\s+good\s+enabled", captured.out)
    assert re.search(r"U/subagent\s+short\s+broken", captured.out)
    assert re.search(r"U/subagent\s+bad\s+broken", captured.out)
    assert re.search(r"P/subagent\s+here\s+pending", captured.out)
    assert "notes" not in captured.out
    assert "! needs developer_instructions, which Codex requires" in captured.out
    assert "! file isn't valid TOML" in captured.out
    assert "bad.toml" in captured.err


def test_codex_can_turn_agents_off(codex, capsys):
    codex.config.write_text("[agents]\nenabled = false\n")
    write_text(
        codex.dir / "agents" / "a.toml",
        'name = "a"\ndescription = "d"\ndeveloper_instructions = "x"\n',
    )
    assert rows(capsys, "-k", "subagent") == {("U/subagent", "a"): "disabled"}


@pytest.fixture
def opencode(machine):
    machine.install("opencode")
    machine.dir = machine.home / ".config" / "opencode"
    machine.dir.mkdir(parents=True)
    return machine


def test_opencode_override_names_what_it_replaced(opencode, capsys):
    write_text(opencode.dir / "agents" / "a.md", "---\ndescription: global\n---\nBody\n")
    write_text(opencode.project / ".opencode" / "agents" / "a.md", "---\nmode: subagent\n---\nB\n")
    out = run(capsys, "--detail")
    assert re.search(r"P/subagent\s+a\n\s+Status:\s+enabled", out) and "U/subagent" not in out
    assert "Finding:" in out and "overrides the entry in ~/.config/opencode/agents/a.md" in out
    # Scalars merge, so a value the later definition leaves out stays.
    assert re.search(r"Description:\s+global", out)


def test_opencode_later_definition_can_bring_an_agent_back(opencode, capsys):
    write_text(opencode.dir / "agents" / "a.md", "---\ndisabled: true\n---\nBody\n")
    write_text(opencode.project / ".opencode" / "agents" / "a.md", "Body\n")
    assert rows(capsys, "-k", "subagent") == {("P/subagent", "a"): "enabled"}


def test_opencode_config_entries_and_turning_off(opencode, capsys):
    write_json(
        opencode.dir / "opencode.json",
        {
            "agents": {"plan": {"disabled": True}, "bad": "nope"},
            "commands": {"c": {"template": "t"}, "no-template": {"description": "d"}},
        },
    )
    found = rows(capsys)
    assert found[("U/subagent", "plan")] == "disabled"
    assert found[("U/subagent", "bad")] == "broken"
    assert found[("U/command", "c")] == "enabled"
    assert found[("U/command", "no-template")] == "broken"


def test_opencode_bad_config_makes_definitions_unknown(opencode, capsys):
    write_text(opencode.dir / "agents" / "a.md", "Body\n")
    write_text(opencode.dir / "opencode.json", "{broken")
    assert rows(capsys, "-k", "subagent") == {("U/subagent", "a"): "unknown"}


@pytest.fixture
def pi(machine):
    machine.install("pi")
    machine.agent = machine.home / ".pi" / "agent"
    machine.agent.mkdir(parents=True)
    machine.settings = machine.agent / "settings.json"
    return machine


def test_pi_prompts(pi, capsys):
    write_text(pi.agent / "prompts" / "a.md", "---\ndescription: from field\n---\nBody\n")
    write_text(pi.agent / "prompts" / "b.md", "First line\n")
    write_text(pi.agent / "prompts" / "folder" / "c.md", "Body\n")
    write_json(pi.settings, {"prompts": ["!**/b.md"]})
    assert rows(capsys, "-k", "command") == {
        ("U/command", "a"): "enabled",
        ("U/command", "b"): "disabled",
    }
    out = run(capsys, "--detail", "-k", "command", "-n", "a")
    assert re.search(r"Agent's term:\s+prompt template", out)


def test_pi_project_prompt_waits_and_then_wins(pi, capsys):
    write_text(pi.agent / "prompts" / "a.md", "Body\n")
    write_text(pi.project / ".pi" / "prompts" / "a.md", "Body\n")
    assert rows(capsys, "-k", "command") == {
        ("U/command", "a"): "enabled",
        ("P/command", "a"): "pending",
    }
    write_json(pi.settings, {"defaultProjectTrust": "always"})
    assert rows(capsys, "-k", "command") == {
        ("U/command", "a"): "shadowed",
        ("P/command", "a"): "enabled",
    }


def test_pi_package_prompt_names_its_package(pi, capsys):
    package = pi.home / "package"
    write_text(package / "prompts" / "a.md", "Body\n")
    write_json(pi.settings, {"packages": [str(package)]})
    out = run(capsys, "--detail", "-k", "command")
    assert re.search(r"Agent's term:\s+package prompt", out)
    assert re.search(rf"Plugin:\s+{re.escape(str(package))}", out)


def test_pi_prompts_do_not_change_skill_listing(pi, capsys):
    write_skill(pi.agent / "skills" / "a", "name: a\ndescription: d")
    write_text(pi.agent / "prompts" / "a.md", "Body\n")
    assert rows(capsys) == {("U/skill", "a"): "enabled", ("U/command", "a"): "enabled"}


@pytest.fixture
def agy(machine):
    machine.install("agy")
    machine.config = machine.home / ".gemini" / "config"
    machine.config.mkdir(parents=True)
    return machine


def test_a_bad_antigravity_hooks_file_lists_nothing(agy, capsys):
    write_json(agy.config / "hooks.json", {"ok": {"Stop": [hook()]}, "worse": "nope"})
    assert rows(capsys, "-k", "hook") == {}


def test_antigravity_hooks(agy, capsys):
    write_json(
        agy.config / "hooks.json",
        {
            "lint": {"PostToolUse": [{"matcher": "x", "hooks": [hook("/bin/lint --key abc")]}]},
            "off": {"enabled": False, "Stop": [hook()]},
            "bad": {"Stop": [{"type": "command"}]},
            "wrapped": {"Stop": [{"hooks": [hook()]}]},
            "unknown": {"Nope": [hook()]},
        },
    )
    found = rows(capsys, "-k", "hook")
    assert found == {
        ("U/hook", "lint"): "enabled",
        ("U/hook", "off"): "disabled",
        ("U/hook", "bad"): "broken",
        ("U/hook", "wrapped"): "broken",
        ("U/hook", "unknown"): "broken",
    }
    out = run(capsys, "--detail", "-n", "lint")
    assert re.search(r"Event:\s+PostToolUse", out) and re.search(r"Command:\s+/bin/lint", out)
    assert re.search(r"Description:\s+/bin/lint --key \*\*\*", out) and "abc" not in out


def test_antigravity_hook_description_joins_its_handlers(agy, capsys):
    write_json(
        agy.config / "hooks.json",
        {"h": {"Stop": [hook("/bin/a"), hook("/bin/b")]}},
    )
    assert re.search(r"Description:\s+/bin/a; /bin/b", run(capsys, "--detail"))


def test_antigravity_workspace_waits_for_trust(agy, capsys):
    write_json(agy.project / ".agents" / "hooks.json", {"h": {"Stop": [hook()]}})
    write_text(agy.project / ".agents" / "agents" / "a.md", agent_file("a"))
    assert rows(capsys, "-k", "hook", "-k", "subagent") == {
        ("P/hook", "h"): "pending",
        ("P/subagent", "a"): "pending",
    }


def test_antigravity_same_named_agents_keep_the_first(agy, capsys):
    write_text(agy.config / "agents" / "x.md", agent_file("a"))
    write_text(agy.home / ".gemini" / "agents" / "y.md", agent_file("a"))
    assert "! shadowed by ~/.gemini/config/agents/x.md" in run(capsys)


def test_antigravity_refused_agent_is_broken(agy, capsys):
    write_text(agy.config / "agents" / "x.md", "---\nname: x\n---\nBody\n")
    assert "! needs a name and a description, which Antigravity CLI requires" in run(capsys)


def test_antigravity_plugin_definitions_follow_the_plugin(agy, capsys):
    root = agy.config / "plugins" / "p"
    write_json(root / "plugin.json", {"name": "p"})
    write_text(root / "agents" / "a.md", agent_file("a"))
    write_json(root / "hooks.json", {"h": {"Stop": [hook()]}})
    assert rows(capsys, "-k", "hook", "-k", "subagent") == {
        ("U/subagent", "a"): "enabled",
        ("U/hook", "h"): "enabled",
    }
    write_json(agy.config / "config.json", {"plugins": {"p": {"enabled": False}}})
    assert rows(capsys, "-k", "hook", "-k", "subagent") == {}


def test_kinds_filter_and_hostile_text(claude, capsys):
    write_text(claude.dir / "agents" / "x.md", agent_file('"ev\\x1b[2Jil[red]x[/red]"'))
    write_json(claude.settings, stop(hook("/bin/a"), matcher="we\x1b[2Jird"))
    out = run(capsys, "-k", "hook", "-k", "subagent")
    assert "\x1b" not in out and "weird" in out
    assert "[red]x[/red]" in out


@pytest.mark.parametrize("kind", ["hook", "subagent", "command"])
def test_rich_output_shows_the_new_kinds(claude, capsys, monkeypatch, kind):
    monkeypatch.setattr(cli, "_use_rich", lambda no_color_flag: True)
    monkeypatch.setenv("COLUMNS", "100")
    monkeypatch.setenv("TERM", "xterm")
    write_json(claude.settings, stop(hook(), matcher="[red]x[/red]"))
    write_text(claude.dir / "agents" / "a.md", agent_file("a"))
    write_text(claude.dir / "commands" / "c.md", "Body\n")
    out = COLOR_CODE.sub("", run(capsys, "-k", kind))
    assert f"{kind.upper()}" in out
    if kind == "hook":
        assert "[red]x[/red]" in out


def test_nothing_is_run_or_written(claude, capsys):
    marker = claude.home / "ran"
    write_json(claude.settings, stop(hook(f"touch {marker}")))
    before = sorted(path for path in claude.home.rglob("*"))
    run(capsys, "--detail")
    assert not marker.exists() and not claude.executed
    assert sorted(path for path in claude.home.rglob("*")) == before
