"""Skills that plugins and packages bring. The oracle tests check these rules against real agents."""

import json
import os
import re

import pytest
from conftest import write_skill

from agt_view.cli import main

SECRET = "hunter2-do-not-print"


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


def skills(capsys, *args):
    return {key: status for key, status in rows(capsys, *args).items() if "/skill" in key[0]}


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def named(name):
    return f"name: {name}\ndescription: d"


@pytest.fixture
def claude(machine):
    machine.install("claude")
    machine.dir = machine.home / ".claude"
    machine.dir.mkdir()
    machine.plugin = machine.dir / "plugins" / "cache" / "m" / "p" / "1.0.0"
    machine.plugin.mkdir(parents=True)
    write_json(
        machine.dir / "plugins" / "installed_plugins.json",
        {
            "version": 2,
            "plugins": {
                "p@m": [{"scope": "user", "installPath": str(machine.plugin), "version": "1.0.0"}]
            },
        },
    )
    write_json(machine.dir / "settings.json", {"enabledPlugins": {"p@m": True}})
    return machine


def test_claude_skill_is_named_after_its_plugin(claude, capsys):
    write_skill(claude.plugin / "skills" / "folder", "description: d")
    write_skill(claude.plugin / "skills" / "other", named("from-field"))
    write_skill(claude.plugin / "skills" / "third", named("p:prefixed"))
    assert skills(capsys) == {
        ("U/skill", "p:folder"): "enabled",
        ("U/skill", "p:from-field"): "enabled",
        ("U/skill", "p:prefixed"): "enabled",
    }


def test_claude_skill_at_the_plugin_root(claude, capsys):
    write_skill(claude.plugin, "description: d")
    assert skills(capsys) == {("U/skill", "p:p"): "enabled"}


def test_claude_root_skill_is_ignored_beside_a_skills_directory(claude, capsys):
    write_skill(claude.plugin, named("root"))
    write_skill(claude.plugin / "skills" / "a", named("a"))
    assert skills(capsys) == {("U/skill", "p:a"): "enabled"}


def test_claude_manifest_paths_add_to_the_default(claude, capsys):
    write_json(claude.plugin / ".claude-plugin" / "plugin.json", {"skills": ["./x", "./one"]})
    write_skill(claude.plugin / "skills" / "a", named("a"))
    write_skill(claude.plugin / "x" / "b", named("b"))
    write_skill(claude.plugin / "one", named("c"))
    assert set(skills(capsys)) == {("U/skill", f"p:{n}") for n in "abc"}


@pytest.mark.parametrize("path", ["../outside", "/etc", "x", "./../outside", 7])
def test_claude_manifest_paths_stay_inside_the_plugin(claude, capsys, path):
    write_json(claude.plugin / ".claude-plugin" / "plugin.json", {"skills": path})
    write_skill(claude.plugin.parent / "outside" / "a", named("a"))
    write_skill(claude.plugin / "x" / "b", named("b"))
    assert skills(capsys) == {}


@pytest.mark.parametrize(
    ("enabled", "expected"),
    [
        (False, {}),
        (None, {}),
    ],
)
def test_claude_skill_follows_its_plugin(claude, capsys, enabled, expected):
    write_skill(claude.plugin / "skills" / "a", named("a"))
    plugins = {} if enabled is None else {"p@m": enabled}
    write_json(claude.dir / "settings.json", {"enabledPlugins": plugins})
    assert skills(capsys) == expected


def test_claude_plugin_with_missing_files_has_no_skills(claude, capsys):
    write_skill(claude.plugin / "skills" / "a", named("a"))
    plugin = claude.plugin
    plugin.rename(plugin.with_name("moved"))
    assert skills(capsys) == {}
    assert rows(capsys)[("U/plugin", "p@m")] == "broken"


def test_claude_local_plugin_skill_has_project_scope(claude, capsys):
    write_json(
        claude.dir / "plugins" / "installed_plugins.json",
        {
            "version": 2,
            "plugins": {
                "p@m": [
                    {
                        "scope": "local",
                        "projectPath": str(claude.project),
                        "installPath": str(claude.plugin),
                    }
                ]
            },
        },
    )
    write_skill(claude.plugin / "skills" / "a", named("a"))
    assert skills(capsys) == {("P/skill", "p:a"): "enabled"}


def test_claude_overrides_do_not_touch_plugin_skills_but_deny_rules_do(claude, capsys):
    write_skill(claude.plugin / "skills" / "a", named("a"))
    write_skill(claude.plugin / "skills" / "b", named("b"))
    write_json(
        claude.dir / "settings.json",
        {
            "enabledPlugins": {"p@m": True},
            "skillOverrides": {"p:a": "off", "a": "off"},
            "permissions": {"deny": ["Skill(p:b)"]},
        },
    )
    assert skills(capsys) == {("U/skill", "p:a"): "enabled", ("U/skill", "p:b"): "disabled"}


def test_claude_plugin_skill_and_personal_skill_both_load(claude, capsys):
    write_skill(claude.plugin / "skills" / "a", named("a"))
    write_skill(claude.dir / "skills" / "a", named("a"))
    assert skills(capsys) == {("U/skill", "a"): "enabled", ("U/skill", "p:a"): "enabled"}


def test_claude_description_falls_back_to_the_body(claude, capsys):
    (claude.plugin / "skills" / "plain").mkdir(parents=True)
    (claude.plugin / "skills" / "plain" / "SKILL.md").write_text("First line\n")
    assert re.search(r"Description:\s+First line", run(capsys, "--detail", "-n", "p:plain"))


def test_detail_names_the_plugin(claude, capsys):
    write_skill(claude.plugin / "skills" / "a", named("a"))
    out = run(capsys, "--detail", "-n", "p:a")
    assert "Agent's term:  plugin skill" in out
    assert "Plugin:        p@m" in out


def test_plugin_skill_is_found_by_plugin_name(claude, capsys):
    write_skill(claude.plugin / "skills" / "a", named("a"))
    write_skill(claude.dir / "skills" / "other", named("other"))
    assert skills(capsys, "plugin skill") == {("U/skill", "p:a"): "enabled"}


def test_broken_plugin_skill_says_why(claude, capsys):
    (claude.plugin / "skills").mkdir()
    (claude.plugin / "skills" / "gone").symlink_to(claude.home / "nowhere")
    out = run(capsys)
    assert "p:gone" in out and "! symlink target is missing" in out


def test_plugin_skill_link_to_a_credential_is_not_read(claude, capsys):
    (claude.home / "secret.env").write_text(f"TOKEN={SECRET}\n")
    (claude.plugin / "skills" / "evil").mkdir(parents=True)
    (claude.plugin / "skills" / "evil" / "SKILL.md").symlink_to(claude.home / "secret.env")
    out = run(capsys, "--detail")
    assert SECRET not in out and "not read: links to a non-Markdown file" in out


@pytest.fixture
def codex(machine):
    machine.install("codex")
    (machine.home / ".codex").mkdir()
    (machine.project / ".codex").mkdir()
    machine.config = machine.home / ".codex" / "config.toml"
    machine.trust = f'[projects."{machine.project}"]\ntrust_level = "trusted"\n'
    machine.cache = machine.home / ".codex" / "plugins" / "cache" / "m" / "p"
    return machine


ON = '[plugins."p@m"]\nenabled = true\n'
OFF = '[plugins."p@m"]\nenabled = false\n'


def codex_version(machine, version="1.0.0", manifest=None):
    root = machine.cache / version
    write_json(
        root / ".codex-plugin" / "plugin.json",
        {"name": "p", "description": version} if manifest is None else manifest,
    )
    return root


def test_codex_needs_an_enabled_entry(codex, capsys):
    write_skill(codex_version(codex) / "skills" / "a", named("a"))
    assert skills(capsys) == {}
    codex.config.write_text(OFF)
    assert skills(capsys) == {}
    codex.config.write_text(ON)
    assert skills(capsys) == {("U/skill", "p:a"): "enabled"}


def test_codex_project_entry_waits_for_trust(codex, capsys):
    write_skill(codex_version(codex) / "skills" / "a", named("a"))
    (codex.project / ".codex" / "config.toml").write_text(ON)
    assert skills(capsys) == {("P/skill", "p:a"): "pending"}


def test_codex_trusted_project_can_turn_a_plugin_off(codex, capsys):
    write_skill(codex_version(codex) / "skills" / "a", named("a"))
    codex.config.write_text(ON + codex.trust)
    (codex.project / ".codex" / "config.toml").write_text(OFF)
    out = run(capsys)
    assert "p:a" not in out
    assert re.search(r"U/plugin\s+p@m\s+shadowed", out)
    assert "! shadowed by the entry in ./.codex/config.toml" in out


def test_codex_untrusted_project_cannot_turn_a_plugin_off(codex, capsys):
    write_skill(codex_version(codex) / "skills" / "a", named("a"))
    codex.config.write_text(ON)
    (codex.project / ".codex" / "config.toml").write_text(OFF)
    assert skills(capsys) == {("U/skill", "p:a"): "enabled"}


@pytest.mark.parametrize(
    ("versions", "expected"),
    [
        (("9.0.0", "10.0.0"), "10.0.0"),
        (("1.0.0", "1.0.0-beta"), "1.0.0"),
        (("1.0.0-alpha", "1.0.0-beta"), "1.0.0-beta"),
        (("1.9", "1.10"), "1.9"),
        (("0abc", "9.9.9"), "9.9.9"),
        (("local", "1.0.0"), "local"),
    ],
)
def test_codex_uses_the_highest_version(codex, capsys, versions, expected):
    codex.config.write_text(ON)
    for version in versions:
        write_skill(codex_version(codex, version) / "skills" / "x", named(f"v{version}"))
    assert set(skills(capsys)) == {("U/skill", f"p:v{expected}")}
    assert f"Description:  {expected}" in run(capsys, "--detail", "-k", "plugin")


def test_codex_highest_version_without_a_manifest_gives_nothing(codex, capsys):
    codex.config.write_text(ON)
    write_skill(codex_version(codex) / "skills" / "a", named("a"))
    (codex.cache / "2.0.0").mkdir()
    assert skills(capsys) == {}


def test_codex_prefix_comes_from_the_manifest(codex, capsys):
    codex.config.write_text(ON)
    write_skill(codex_version(codex, manifest={"name": "other"}) / "skills" / "a", named("a"))
    assert set(skills(capsys)) == {("U/skill", "other:a")}


def test_codex_prefix_without_a_manifest_name_is_the_version(codex, capsys):
    codex.config.write_text(ON)
    write_skill(codex_version(codex, manifest={}) / "skills" / "a", named("a"))
    assert set(skills(capsys)) == {("U/skill", "1.0.0:a")}


@pytest.mark.parametrize(
    ("paths", "expected"),
    [
        ("./custom/", "p:custom"),
        (["./custom/"], "p:custom"),
        (["./custom/", "../../outside/"], "p:custom"),
        ("custom/", "p:default"),
        ("../../outside/", "p:default"),
        ("./", "p:default"),
        ([], "p:default"),
        (7, "p:default"),
    ],
)
def test_codex_manifest_paths_replace_the_default(codex, capsys, paths, expected):
    codex.config.write_text(ON)
    root = codex_version(codex, manifest={"name": "p", "skills": paths})
    write_skill(root / "custom" / "x", named("custom"))
    write_skill(root / "skills" / "y", named("default"))
    write_skill(root.parent.parent / "outside" / "z", named("outside"))
    assert set(skills(capsys)) == {("U/skill", expected)}


def test_codex_skill_without_a_description_is_broken(codex, capsys):
    codex.config.write_text(ON)
    write_skill(codex_version(codex) / "skills" / "a", "name: a")
    out = run(capsys)
    assert re.search(r"U/skill\s+p:a\s+broken", out)
    assert "! has no description, which Codex requires" in out


def test_codex_config_can_disable_a_plugin_skill(codex, capsys):
    skill = write_skill(codex_version(codex) / "skills" / "a", named("a"))
    codex.config.write_text(
        f'{ON}\n[[skills.config]]\npath = "{skill / "SKILL.md"}"\nenabled = false\n'
    )
    assert skills(capsys) == {("U/skill", "p:a"): "disabled"}


def test_hostile_manifest_name_is_cleaned(codex, capsys):
    codex.config.write_text(ON)
    write_skill(codex_version(codex, manifest={"name": "ev\x1b[2Jil"}) / "skills" / "a", named("a"))
    out = run(capsys)
    assert "\x1b" not in out and "evil:a" in out


@pytest.fixture
def agy(machine):
    machine.install("agy")
    machine.plugin = machine.home / ".gemini" / "config" / "plugins" / "p"
    write_json(machine.plugin / "plugin.json", {"name": "from-manifest"})
    return machine


def test_antigravity_uses_the_directory_as_prefix(agy, capsys):
    write_skill(agy.plugin / "skills" / "folder", named("from-field"))
    assert skills(capsys) == {("U/skill", "p:from-field"): "enabled"}


def test_antigravity_manifest_paths_add_to_the_default(agy, capsys):
    write_json(agy.plugin / "plugin.json", {"skills": "./x"})
    write_skill(agy.plugin / "x" / "a", named("a"))
    write_skill(agy.plugin / "skills" / "b", named("b"))
    assert set(skills(capsys)) == {("U/skill", "p:a"), ("U/skill", "p:b")}


def test_antigravity_key_naming_the_default_lists_once(agy, capsys):
    write_json(agy.plugin / "plugin.json", {"skills": "./skills"})
    write_skill(agy.plugin / "skills" / "a", named("a"))
    assert run(capsys).count("p:a") == 1


def test_antigravity_disabled_plugin_has_no_skills(agy, capsys):
    write_skill(agy.plugin / "skills" / "a", named("a"))
    write_json(
        agy.home / ".gemini" / "config" / "config.json", {"plugins": {"p": {"enabled": False}}}
    )
    assert skills(capsys) == {}


def test_antigravity_workspace_plugin_waits_for_trust(agy, capsys):
    plugin = agy.project / ".agents" / "plugins" / "w"
    write_json(plugin / "plugin.json", {"name": "w"})
    write_skill(plugin / "skills" / "a", named("a"))
    assert skills(capsys) == {("P/skill", "w:a"): "pending"}


def test_antigravity_skill_without_frontmatter_is_broken(agy, capsys):
    (agy.plugin / "skills" / "a").mkdir(parents=True)
    (agy.plugin / "skills" / "a" / "SKILL.md").write_text("Body\n")
    assert "! has no frontmatter, which Antigravity CLI requires" in run(capsys)


@pytest.fixture
def pi(machine):
    machine.install("pi")
    machine.agent = machine.home / ".pi" / "agent"
    machine.agent.mkdir(parents=True)
    machine.settings = machine.agent / "settings.json"
    machine.package = machine.home / "package"
    machine.package.mkdir()
    return machine


def test_pi_package_skill_keeps_its_name(pi, capsys):
    write_skill(pi.package / "skills" / "a", named("a"))
    write_json(pi.settings, {"packages": [str(pi.package)]})
    assert skills(capsys) == {("U/skill", "a"): "enabled"}
    out = run(capsys, "--detail", "-k", "skill")
    assert "Agent's term:  package skill" in out and f"Plugin:        {pi.package}" in out


def test_pi_package_source_hides_credentials(pi, capsys):
    checkout = pi.agent / "git" / "github.com" / "acme" / "tools"
    write_skill(checkout / "skills" / "a", named("a"))
    write_json(pi.settings, {"packages": [f"https://user:{SECRET}@github.com/acme/tools"]})
    out = run(capsys, "--detail")
    assert re.search(r"U/skill\s+a\n\s+Status:\s+enabled", out)
    assert SECRET not in out


def test_pi_package_loses_to_other_skills(pi, capsys):
    write_skill(pi.package / "skills" / "a", named("a"))
    write_skill(pi.agent / "skills" / "a", named("a"))
    write_json(pi.settings, {"packages": [str(pi.package)]})
    out = run(capsys, "--detail", "-k", "skill")
    assert "Finding:       shadowed by ~/.pi/agent/skills/a" in out


@pytest.mark.parametrize(
    "source",
    [
        "npm:../../../outside",
        "git:../../outside",
        "git:github.com/../../../outside",
        "git@github.com:acme/outside",
        "npm:not-installed",
        "git:github.com/acme/not-installed",
        "./not-there",
    ],
)
def test_pi_sources_only_name_what_pi_keeps(pi, capsys, source):
    write_skill(pi.home / "outside" / "skills" / "a", named("a"))
    write_json(pi.settings, {"packages": [source]})
    assert skills(capsys) == {}


def test_pi_single_file_source_has_no_skills(pi, capsys):
    (pi.package / "one.ts").write_text("export default () => {}\n")
    write_json(pi.settings, {"packages": [str(pi.package / "one.ts")]})
    assert skills(capsys) == {}


def test_pi_project_package_waits_for_trust(pi, capsys):
    write_skill(pi.package / "skills" / "a", named("a"))
    write_json(pi.project / ".pi" / "settings.json", {"packages": [str(pi.package)]})
    assert skills(capsys) == {("P/skill", "a"): "pending"}


def test_pi_refused_project_package_has_no_skills(pi, capsys):
    write_skill(pi.package / "skills" / "a", named("a"))
    write_json(pi.settings, {"defaultProjectTrust": "never"})
    write_json(pi.project / ".pi" / "settings.json", {"packages": [str(pi.package)]})
    assert skills(capsys) == {}


def test_pi_package_skill_without_a_description_is_broken(pi, capsys):
    write_skill(pi.package / "skills" / "a", "name: a")
    write_json(pi.settings, {"packages": [str(pi.package)]})
    assert "! has no description, which Pi requires" in run(capsys)


def test_pi_bad_package_json_uses_the_conventional_directory(pi, capsys):
    (pi.package / "package.json").write_text("{broken")
    write_skill(pi.package / "skills" / "a", named("a"))
    write_json(pi.settings, {"packages": [str(pi.package)]})
    assert skills(capsys) == {("U/skill", "a"): "enabled"}


def test_opencode_plugin_folder_brings_no_skills(machine, capsys):
    machine.install("opencode")
    plugin = machine.home / ".config" / "opencode" / "plugins" / "with-skill"
    plugin.mkdir(parents=True)
    (plugin / "index.ts").write_text("export default {}\n")
    write_skill(plugin / "skills" / "inside", named("inside"))
    assert skills(capsys) == {}


def test_reading_plugin_skills_runs_nothing(claude, capsys):
    write_skill(claude.plugin / "skills" / "a", named("a"))
    run(capsys)
    assert not claude.executed
    assert os.listdir(claude.plugin) == ["skills"]


def order(capsys, *args):
    return [
        (line.split()[0], line.split()[1])
        for line in run(capsys, *args).splitlines()
        if line.startswith("  ") and re.match(r"[PUS]/", line.split()[0])
    ]


def fill_claude_plugin(claude):
    write_skill(claude.plugin / "skills" / "z", named("z"))
    (claude.plugin / "commands").mkdir()
    (claude.plugin / "commands" / "c.md").write_text("Body\n")
    (claude.plugin / "agents").mkdir()
    (claude.plugin / "agents" / "a.md").write_text("Body\n")
    write_json(
        claude.plugin / "hooks" / "hooks.json",
        {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "/bin/a"}]}]}},
    )
    write_skill(claude.dir / "skills" / "own", named("own"))
    (claude.dir / "commands").mkdir()
    (claude.dir / "commands" / "mine.md").write_text("Body\n")


def test_plugin_items_are_listed_below_the_plugin(claude, capsys):
    fill_claude_plugin(claude)
    assert order(capsys) == [
        ("U/skill", "own"),
        ("U/plugin", "p@m"),
        ("U/skill", "p:z"),
        ("U/hook", "Stop"),
        ("U/subagent", "p:a"),
        ("U/command", "p:c"),
        ("U/command", "mine"),
    ]


def test_each_plugin_keeps_its_own_items(claude, capsys):
    other = claude.dir / "plugins" / "cache" / "m" / "a" / "1.0.0"
    write_skill(other / "skills" / "s", named("s"))
    write_skill(claude.plugin / "skills" / "s", named("s"))
    installs = {
        "p@m": [{"scope": "user", "installPath": str(claude.plugin)}],
        "a@m": [{"scope": "user", "installPath": str(other)}],
    }
    write_json(
        claude.dir / "plugins" / "installed_plugins.json", {"version": 2, "plugins": installs}
    )
    write_json(claude.dir / "settings.json", {"enabledPlugins": {"p@m": True, "a@m": True}})
    assert order(capsys) == [
        ("U/plugin", "a@m"),
        ("U/skill", "a:s"),
        ("U/plugin", "p@m"),
        ("U/skill", "p:s"),
    ]


def test_filtering_out_the_plugin_leaves_items_in_their_normal_place(claude, capsys):
    fill_claude_plugin(claude)
    assert order(capsys, "-k", "skill") == [("U/skill", "own"), ("U/skill", "p:z")]


def test_filtering_the_items_leaves_the_plugin_alone(claude, capsys):
    fill_claude_plugin(claude)
    assert order(capsys, "-k", "plugin") == [("U/plugin", "p@m")]


def test_same_plugin_in_two_scopes_keeps_items_with_their_scope(agy, capsys):
    write_skill(agy.plugin / "skills" / "u", named("u"))
    workspace = agy.project / ".agents" / "plugins" / "p"
    write_json(workspace / "plugin.json", {"name": "p"})
    write_skill(workspace / "skills" / "w", named("w"))
    assert order(capsys) == [
        ("P/plugin", "p"),
        ("P/skill", "p:w"),
        ("U/plugin", "p"),
        ("U/skill", "p:u"),
    ]


def test_pi_package_items_are_below_the_package(pi, capsys):
    write_skill(pi.package / "skills" / "a", named("a"))
    (pi.package / "prompts").mkdir()
    (pi.package / "prompts" / "b.md").write_text("Body\n")
    write_skill(pi.agent / "skills" / "own", named("own"))
    write_json(pi.settings, {"packages": [str(pi.package)]})
    assert order(capsys) == [
        ("U/skill", "own"),
        ("U/plugin", str(pi.package)),
        ("U/skill", "a"),
        ("U/command", "b"),
    ]


def test_plain_output_does_not_indent_plugin_items(claude, capsys):
    fill_claude_plugin(claude)
    row = next(line for line in run(capsys).splitlines() if "U/skill" in line and "p:z" in line)
    plugin = next(line for line in run(capsys).splitlines() if "U/plugin" in line)
    assert row.index("p:z") == plugin.index("p@m")


@pytest.mark.parametrize("flags", [(), ("--detail",)])
def test_rich_does_not_indent_plugin_items(claude, capsys, monkeypatch, flags):
    from agt_view import cli

    monkeypatch.setattr(cli, "_use_rich", lambda no_color_flag: True)
    monkeypatch.setenv("COLUMNS", "100")
    monkeypatch.setenv("TERM", "xterm")
    fill_claude_plugin(claude)
    lines = re.sub(r"\x1b\[[0-9;]*m", "", run(capsys, *flags)).splitlines()
    plugin = next(line for line in lines if "p@m" in line)
    skill = next(line for line in lines if "p:z" in line)
    assert skill.index("p:z") == plugin.index("p@m")
    assert lines.index(skill) > lines.index(plugin)
