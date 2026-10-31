"""OpenCode, Pi, and Antigravity CLI. Behavior here was checked against the real agents."""

import json

import pytest
from conftest import SCOPE_WORDS, write_skill

from agt_view.adapters.base import Context
from agt_view.adapters.opencode import OpenCode
from agt_view.adapters.pi import Pi
from agt_view.cli import main

DESCRIBED = "description: does a thing"


def named(name):
    return f"name: {name}\n{DESCRIBED}"


def statuses(capsys, *args):
    assert main(list(args)) == 0
    found = {}
    for line in capsys.readouterr().out.splitlines():
        parts = line.split()
        scope, _, kind = parts[0].partition("/") if parts else ("", "", "")
        if line.startswith("  ") and kind in ("skill", "mcp", "plugin"):
            found[(kind, parts[1], SCOPE_WORDS[scope])] = parts[2]
    return found


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    return path


@pytest.fixture
def opencode(machine):
    machine.install("opencode")
    machine.config = machine.home / ".config" / "opencode"
    machine.config.mkdir(parents=True)
    return machine


@pytest.fixture
def pi(machine):
    machine.install("pi")
    machine.agent = machine.home / ".pi" / "agent"
    machine.agent.mkdir(parents=True)
    return machine


@pytest.fixture
def agy(machine):
    machine.install("agy")
    machine.cli = machine.home / ".gemini" / "antigravity-cli"
    machine.cli.mkdir(parents=True)
    machine.shared = machine.home / ".gemini" / "config"
    machine.shared.mkdir()
    return machine


def test_opencode_skill_locations(opencode, capsys):
    write_skill(opencode.config / "skills" / "own", named("own"))
    write_skill(opencode.home / ".claude" / "skills" / "from-claude", named("from-claude"))
    write_skill(opencode.home / ".agents" / "skills" / "from-agents", named("from-agents"))
    write_skill(opencode.project / ".opencode" / "skills" / "repo", named("repo"))
    write_skill(opencode.project / ".claude" / "skills" / "repo-claude", named("repo-claude"))
    write_skill(opencode.project / ".agents" / "skills" / "repo-agents", named("repo-agents"))
    assert statuses(capsys) == {
        ("skill", "from-agents", "user"): "enabled",
        ("skill", "from-claude", "user"): "enabled",
        ("skill", "own", "user"): "enabled",
        ("skill", "repo", "project"): "enabled",
        ("skill", "repo-agents", "project"): "enabled",
        ("skill", "repo-claude", "project"): "enabled",
    }


def test_opencode_skills_are_not_validated(opencode, capsys):
    skills = opencode.config / "skills"
    write_skill(skills / "no-name", DESCRIBED)
    write_skill(skills / "no-desc", "name: no-desc")
    write_skill(skills / "dir-a", named("other-name"))
    write_skill(skills / "Bad_Name", named("Bad_Name"))
    (skills / "no-fm").mkdir()
    (skills / "no-fm" / "SKILL.md").write_text("no frontmatter")
    write_skill(skills / "group" / "deep" / "nested", named("nested"))
    write_skill(opencode.config / "skill" / "singular", named("singular"))
    (skills / "standalone.md").write_text(f"---\n{named('standalone')}\n---\n")
    assert statuses(capsys) == {
        ("skill", name, "user"): "enabled"
        for name in (
            "Bad_Name",
            "nested",
            "no-desc",
            "no-fm",
            "no-name",
            "other-name",
            "singular",
            "standalone",
        )
    }


def test_opencode_discovery_limits(opencode, capsys):
    skills = opencode.config / "skills"
    path = skills
    for level in range(1, 8):
        path = path / f"l{level}"
        write_skill(path, named(f"depth{level}"))
    write_skill(skills / "outer", named("outer"))
    write_skill(skills / "outer" / "inner", named("inner"))
    write_skill(skills / "node_modules" / "pkg" / "in-modules", named("in-modules"))
    write_skill(skills / ".hidden" / "hid", named("hid"))
    write_skill(skills / "group" / "nested", named("nested"))
    (skills / "group" / "sub.md").write_text(f"---\n{named('sub')}\n---\n")
    (opencode.home / ".agents" / "skills").mkdir(parents=True)
    (opencode.home / ".agents" / "skills" / "root.md").write_text("---\ndescription: d\n---\n")
    assert {key[1] for key in statuses(capsys)} == {"depth1", "nested", "outer", "root"}


def test_opencode_depth_limit_within_nesting(opencode, capsys):
    path = opencode.config / "skills"
    for level in range(1, 7):
        path = path / f"l{level}"
    write_skill(path, named("too-deep"))
    write_skill(path.parent, named("deepest"))
    assert {key[1] for key in statuses(capsys)} == {"deepest"}


def test_opencode_configured_skill_sources(opencode, capsys, tmp_path):
    write_skill(opencode.project / "relative" / "rel-skill", named("rel-skill"))
    write_skill(tmp_path / "absolute" / "abs-skill", named("abs-skill"))
    (tmp_path / "absolute" / "abs-root.md").write_text(f"---\n{named('abs-root')}\n---\n")
    write_skill(tmp_path / "one-skill", named("one-skill"))
    write_json(
        opencode.config / "opencode.json",
        {
            "skills": [
                "relative",
                str(tmp_path / "absolute"),
                str(tmp_path / "one-skill"),
                "https://x.test/s",
            ]
        },
    )
    assert {key[1] for key in statuses(capsys)} == {
        "abs-root",
        "abs-skill",
        "one-skill",
        "rel-skill",
    }


def test_opencode_v1_configured_skill_paths(opencode, capsys):
    write_skill(opencode.project / "relative" / "rel-skill", named("rel-skill"))
    write_json(opencode.config / "opencode.json", {"skills": {"paths": ["relative"]}})
    assert list(statuses(capsys)) == [("skill", "rel-skill", "user")]


def test_opencode_permissions_match_the_id_not_the_name(opencode, capsys):
    write_skill(opencode.config / "skills" / "the-id", named("display"))
    write_json(
        opencode.config / "opencode.json",
        {"permissions": [{"action": "skill", "resource": "the-id", "effect": "deny"}]},
    )
    assert statuses(capsys) == {("skill", "display", "user"): "disabled"}


def test_opencode_can_ignore_claude_skills(opencode, capsys, monkeypatch):
    write_skill(opencode.config / "skills" / "own", named("own"))
    write_skill(opencode.home / ".claude" / "skills" / "from-claude", named("from-claude"))
    monkeypatch.setenv("OPENCODE_DISABLE_CLAUDE_CODE_SKILLS", "1")
    assert list(statuses(capsys)) == [("skill", "own", "user")]


def test_opencode_v2_permissions(opencode, capsys):
    write_skill(opencode.config / "skills" / "one", named("one"))
    write_skill(opencode.config / "skills" / "two", named("two"))
    write_json(
        opencode.config / "opencode.json",
        {
            "permissions": [
                {"action": "skill", "resource": "*", "effect": "allow"},
                {"action": "skill", "resource": "one", "effect": "deny"},
                {"action": "bash", "resource": "two", "effect": "deny"},
            ]
        },
    )
    assert statuses(capsys) == {
        ("skill", "one", "user"): "disabled",
        ("skill", "two", "user"): "enabled",
    }


@pytest.mark.parametrize(
    ("rules", "expected"),
    [
        ({"*": "allow", "one": "deny"}, "disabled"),
        ({"o*": "deny"}, "disabled"),
        ({"o*": "deny", "one": "allow"}, "enabled"),
        ({"one": "ask"}, "enabled"),
        ({"other": "deny"}, "enabled"),
        ("deny", "disabled"),
        ({"one": "future"}, "unknown"),
    ],
)
def test_opencode_skill_permissions(opencode, capsys, rules, expected):
    write_skill(opencode.config / "skills" / "one", named("one"))
    write_json(opencode.config / "opencode.json", {"permission": {"skill": rules}})
    assert statuses(capsys)[("skill", "one", "user")] == expected


def test_opencode_jsonc_config_and_mcp(opencode, capsys):
    (opencode.config / "opencode.jsonc").write_text(
        """{
        // comments and trailing commas are allowed
        "mcp": {
            "local": {"type": "local", "command": ["npx", "-y", "x"],},
            "remote": {"type": "remote", "url": "https://x.test/mcp"},
            "off": {"type": "local", "command": ["x"], "enabled": false},
            "bad-command": {"type": "local", "command": "not-a-list"},
            "bad-type": {"type": "other"},
        },
    }"""
    )
    assert statuses(capsys) == {
        ("mcp", "bad-command", "user"): "broken",
        ("mcp", "bad-type", "user"): "broken",
        ("mcp", "local", "user"): "enabled",
        ("mcp", "off", "user"): "disabled",
        ("mcp", "remote", "user"): "enabled",
    }


def test_opencode_v2_servers(opencode, capsys):
    local = {"type": "local", "command": ["x"]}
    write_json(
        opencode.config / "opencode.json",
        {
            "mcp": {
                "servers": {
                    "on": local,
                    "off": {**local, "disabled": True},
                    "enabled-flag-ignored": {**local, "enabled": False},
                    "bad": {"type": "other"},
                }
            }
        },
    )
    assert statuses(capsys) == {
        ("mcp", "bad", "user"): "broken",
        ("mcp", "enabled-flag-ignored", "user"): "enabled",
        ("mcp", "off", "user"): "disabled",
        ("mcp", "on", "user"): "enabled",
    }


@pytest.mark.parametrize("override", [{"enabled": False}, {"disabled": True}])
def test_opencode_partial_project_entry_changes_nothing(opencode, capsys, override):
    server = {"type": "local", "command": ["x"]}
    write_json(opencode.config / "opencode.json", {"mcp": {"shared": server}})
    write_json(opencode.project / "opencode.json", {"mcp": {"shared": override}})
    assert statuses(capsys) == {("mcp", "shared", "user"): "enabled"}


def test_opencode_later_full_entry_wins(opencode, capsys):
    server = {"type": "local", "command": ["x"]}
    write_json(opencode.config / "opencode.json", {"mcp": {"shared": server}})
    write_json(
        opencode.project / "opencode.json", {"mcp": {"shared": {**server, "enabled": False}}}
    )
    assert statuses(capsys) == {("mcp", "shared", "project"): "disabled"}


def test_opencode_managed_config_wins(opencode, capsys):
    server = {"type": "local", "command": ["x"]}
    write_json(opencode.config / "opencode.json", {"mcp": {"s": server}})
    write_json(
        opencode.system / "etc" / "opencode" / "opencode.json",
        {"mcp": {"s": {**server, "enabled": False}}},
    )
    assert statuses(capsys) == {("mcp", "s", "system"): "disabled"}


def test_opencode_project_config_can_be_ignored(opencode, capsys, monkeypatch):
    write_json(
        opencode.project / "opencode.json", {"mcp": {"s": {"type": "local", "command": ["x"]}}}
    )
    monkeypatch.setenv("OPENCODE_DISABLE_PROJECT_CONFIG", "1")
    assert statuses(capsys) == {}


def test_opencode_plugins(opencode, capsys):
    write_json(
        opencode.config / "opencode.json",
        {
            "plugins": ["v2-listed"],
            "plugin": ["npm-one", "@org/npm-two", ["with-options", {"a": 1}]],
        },
    )
    write_json(opencode.project / "opencode.json", {"plugin": ["npm-one"]})
    plugins = opencode.config / "plugins"
    plugins.mkdir()
    (plugins / "local.ts").write_text("x")
    (plugins / "other.js").write_text("x")
    (plugins / "notes.md").write_text("x")
    (plugins / "not-loaded.mjs").write_text("x")
    (plugins / "dir").mkdir()
    (plugins / "dir" / "index.ts").write_text("x")
    (plugins / "no-index").mkdir()
    (plugins / "gone.ts").symlink_to(opencode.home / "missing")
    singular = opencode.config / "plugin"
    singular.mkdir()
    (singular / "single.js").write_text("x")
    project_plugins = opencode.project / ".opencode" / "plugins"
    project_plugins.mkdir(parents=True)
    (project_plugins / "repo.js").write_text("x")
    assert statuses(capsys) == {
        ("plugin", "@org/npm-two", "user"): "unknown",
        ("plugin", "dir", "user"): "enabled",
        ("plugin", "gone", "user"): "broken",
        ("plugin", "local", "user"): "enabled",
        ("plugin", "npm-one", "project"): "unknown",
        ("plugin", "other", "user"): "enabled",
        ("plugin", "repo", "project"): "enabled",
        ("plugin", "single", "user"): "enabled",
        ("plugin", "v2-listed", "user"): "unknown",
        ("plugin", "with-options", "user"): "unknown",
    }


def plugin_source(plugin_id):
    return f"export default {{ id: '{plugin_id}', setup: async () => ({{}}) }}\n"


def test_opencode_configured_local_plugins(opencode, capsys):
    for name in ("listed", "uri", "obj", "server-entry"):
        directory = opencode.config / name
        directory.mkdir()
        entry = "server.ts" if name == "server-entry" else "index.ts"
        (directory / entry).write_text(plugin_source(f"id-{name}"))
    write_json(opencode.config / "package-dir" / "package.json", {"name": "p", "exports": "./a.js"})
    (opencode.config / "single.ts").write_text(plugin_source("id-single"))
    (opencode.config / "empty").mkdir()
    write_json(
        opencode.config / "opencode.json",
        {
            "plugins": [
                "./listed",
                f"file://{opencode.config}/uri",
                {"package": "./obj"},
                "./server-entry",
                "./package-dir",
                "./single.ts",
                "./missing",
                "./empty",
                {"package": "npm-object", "options": {}},
            ]
        },
    )
    assert statuses(capsys) == {
        ("plugin", "empty", "user"): "broken",
        ("plugin", "listed", "user"): "enabled",
        ("plugin", "missing", "user"): "broken",
        ("plugin", "npm-object", "user"): "unknown",
        ("plugin", "obj", "user"): "enabled",
        ("plugin", "package-dir", "user"): "enabled",
        ("plugin", "server-entry", "user"): "enabled",
        ("plugin", "single.ts", "user"): "broken",
        ("plugin", "uri", "user"): "enabled",
    }


def test_opencode_plugin_removals_are_ordered_and_match_ids(opencode, capsys):
    plugins = opencode.config / "plugins"
    plugins.mkdir()
    (plugins / "local.ts").write_text(plugin_source("id-local"))
    (plugins / "two.js").write_text(plugin_source("id-two"))
    (plugins / "group.ts").write_text(plugin_source("team.one"))
    (opencode.config / "obj").mkdir()
    (opencode.config / "obj" / "index.ts").write_text(plugin_source("id-obj"))
    write_json(
        opencode.config / "opencode.json",
        {"plugins": ["-id-two", "-local", "-id-*", "-team.*", "-id-obj", {"package": "./obj"}]},
    )
    assert statuses(capsys) == {
        ("plugin", "group", "user"): "disabled",
        ("plugin", "local", "user"): "enabled",
        ("plugin", "obj", "user"): "enabled",
        ("plugin", "two", "user"): "disabled",
    }


def test_opencode_removing_everything_also_removes_skills_and_servers(opencode, capsys):
    write_skill(opencode.config / "skills" / "one", named("one"))
    (opencode.config / "plugins").mkdir()
    (opencode.config / "plugins" / "local.ts").write_text(plugin_source("id-local"))
    (opencode.config / "kept").mkdir()
    (opencode.config / "kept" / "index.ts").write_text(plugin_source("id-kept"))
    write_json(
        opencode.config / "opencode.json",
        {
            "plugins": ["-*", "./kept"],
            "mcp": {"servers": {"s": {"type": "local", "command": ["x"]}}},
        },
    )
    assert statuses(capsys) == {
        ("skill", "one", "user"): "disabled",
        ("mcp", "s", "user"): "disabled",
        ("plugin", "kept", "user"): "enabled",
        ("plugin", "local", "user"): "disabled",
    }


def test_opencode_removing_compatibility_plugin_drops_those_sources(opencode, capsys):
    write_skill(opencode.config / "skills" / "own", named("own"))
    write_skill(opencode.home / ".claude" / "skills" / "from-claude", named("from-claude"))
    write_skill(opencode.project / ".agents" / "skills" / "repo-agents", named("repo-agents"))
    write_json(opencode.config / "opencode.json", {"plugins": ["-opencode.config.compatibility"]})
    assert statuses(capsys) == {
        ("skill", "from-claude", "user"): "disabled",
        ("skill", "own", "user"): "enabled",
        ("skill", "repo-agents", "project"): "disabled",
    }


def test_opencode_removal_makes_plugin_without_readable_id_unknown(opencode, capsys):
    (opencode.config / "plugins").mkdir()
    (opencode.config / "plugins" / "opaque.js").write_text("export default make()\n")
    write_json(opencode.config / "opencode.json", {"plugins": ["-something"]})
    assert statuses(capsys) == {("plugin", "opaque", "user"): "unknown"}


def test_opencode_config_overrides(opencode, capsys, tmp_path, monkeypatch):
    extra = tmp_path / "extra"
    write_skill(extra / "skills" / "extra-skill", named("extra-skill"))
    custom = write_json(
        tmp_path / "custom.json", {"mcp": {"custom": {"type": "local", "command": ["x"]}}}
    )
    monkeypatch.setenv("OPENCODE_CONFIG_DIR", str(extra))
    monkeypatch.setenv("OPENCODE_CONFIG", str(custom))
    assert main([]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0] == f"opencode  {extra} (set by OPENCODE_CONFIG_DIR)"
    assert "extra-skill" in out and "custom" in out


def test_opencode_follows_xdg_config_home(machine, capsys, tmp_path, monkeypatch):
    machine.install("opencode")
    moved = tmp_path / "xdg" / "opencode"
    write_skill(moved / "skills" / "moved-skill", named("moved-skill"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert main([]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0] == f"opencode  {moved} (set by XDG_CONFIG_HOME)"
    assert "moved-skill" in out


def test_opencode_corrupt_config_makes_status_unknown(opencode, capsys):
    write_skill(opencode.config / "skills" / "one", named("one"))
    (opencode.config / "opencode.json").write_text("{ not json")
    assert main([]) == 0
    captured = capsys.readouterr()
    assert " unknown" in captured.out
    assert "warning: opencode: cannot read" in captured.err


def test_pi_skill_locations_and_recursion(pi, capsys):
    write_skill(pi.agent / "skills" / "own", named("own"))
    write_skill(pi.agent / "skills" / "group" / "deep" / "nested", named("nested"))
    write_skill(pi.home / ".agents" / "skills" / "shared", named("shared"))
    write_skill(pi.project / ".pi" / "skills" / "repo", named("repo"))
    write_skill(pi.project / ".agents" / "skills" / "repo-agents", named("repo-agents"))
    (pi.agent / "skills" / "standalone.md").write_text(f"---\n{named('standalone')}\n---\n")
    (pi.agent / "skills" / "no-desc.md").write_text("---\nname: no-desc\n---\n")
    (pi.home / ".agents" / "skills" / "ignored.md").write_text(f"---\n{named('ignored')}\n---\n")
    assert statuses(capsys) == {
        ("skill", "nested", "user"): "enabled",
        ("skill", "no-desc", "user"): "broken",
        ("skill", "own", "user"): "enabled",
        ("skill", "repo", "project"): "pending",
        ("skill", "repo-agents", "project"): "pending",
        ("skill", "shared", "user"): "enabled",
        ("skill", "standalone", "user"): "enabled",
    }


def test_pi_skill_needs_description_but_not_name(pi, capsys):
    write_skill(pi.agent / "skills" / "no-name")
    write_skill(pi.agent / "skills" / "no-desc", "name: no-desc")
    assert statuses(capsys) == {
        ("skill", "no-desc", "user"): "broken",
        ("skill", "no-name", "user"): "enabled",
    }


@pytest.mark.parametrize(
    ("trust", "expected"),
    [("always", "enabled"), ("never", "disabled"), ("ask", "pending"), (None, "pending")],
)
def test_pi_project_items_depend_on_trust(pi, capsys, trust, expected):
    if trust:
        write_json(pi.agent / "settings.json", {"defaultProjectTrust": trust})
    write_json(pi.project / ".pi" / "mcp.json", {"mcpServers": {"s": {"command": "x"}}})
    extensions = pi.project / ".pi" / "extensions"
    extensions.mkdir()
    (extensions / "ext.ts").write_text("x")
    assert statuses(capsys) == {
        ("mcp", "s", "project"): expected,
        ("plugin", "ext", "project"): expected,
    }


@pytest.mark.parametrize(
    ("decision", "default", "expected"),
    [(True, "never", "enabled"), (False, "always", "disabled")],
)
@pytest.mark.parametrize("use_parent", [False, True])
def test_pi_saved_trust_beats_the_default(pi, capsys, decision, default, expected, use_parent):
    write_json(pi.agent / "settings.json", {"defaultProjectTrust": default})
    target = pi.project.parent if use_parent else pi.project
    write_json(pi.agent / "trust.json", {str(target.resolve()): decision})
    write_json(pi.project / ".pi" / "mcp.json", {"mcpServers": {"s": {"command": "x"}}})
    assert statuses(capsys) == {("mcp", "s", "project"): expected}


def test_pi_closest_trust_decision_wins(pi, capsys):
    write_json(
        pi.agent / "trust.json",
        {str(pi.project.parent.resolve()): False, str(pi.project.resolve()): True},
    )
    write_json(pi.project / ".pi" / "mcp.json", {"mcpServers": {"s": {"command": "x"}}})
    assert statuses(capsys) == {("mcp", "s", "project"): "enabled"}


def test_pi_corrupt_trust_store_warns(pi, capsys):
    (pi.agent / "trust.json").write_text("{ not json")
    write_json(pi.project / ".pi" / "mcp.json", {"mcpServers": {"s": {"command": "x"}}})
    assert main([]) == 0
    captured = capsys.readouterr()
    assert " unknown" in captured.out and "trust.json" in captured.err


def test_pi_project_entry_can_override_user_enabled(pi, capsys):
    write_json(pi.agent / "settings.json", {"defaultProjectTrust": "always"})
    write_json(pi.agent / "mcp.json", {"mcpServers": {"s": {"command": "x"}}})
    write_json(pi.project / ".pi" / "mcp.json", {"mcpServers": {"s": {"enabled": False}}})
    assert statuses(capsys) == {("mcp", "s", "user"): "disabled"}


def test_pi_extension_layouts(pi, capsys, tmp_path):
    extensions = pi.agent / "extensions"
    extensions.mkdir()
    (extensions / "file.ts").write_text("x")
    (extensions / "other.js").write_text("x")
    (extensions / "notes.md").write_text("x")
    (extensions / "indexed").mkdir()
    (extensions / "indexed" / "index.ts").write_text("x")
    write_json(extensions / "packaged" / "package.json", {"pi": {"extensions": ["./src/a.ts"]}})
    write_json(extensions / "plain-package" / "package.json", {"name": "not-an-extension"})
    (extensions / "empty").mkdir()
    (extensions / "gone.ts").symlink_to(tmp_path / "missing")
    listed = tmp_path / "listed.ts"
    listed.write_text("x")
    write_json(pi.agent / "settings.json", {"extensions": [str(listed)]})
    assert statuses(capsys) == {
        ("plugin", "file", "user"): "enabled",
        ("plugin", "gone", "user"): "broken",
        ("plugin", "indexed", "user"): "enabled",
        ("plugin", "listed", "user"): "enabled",
        ("plugin", "other", "user"): "enabled",
        ("plugin", "packaged", "user"): "enabled",
    }


def test_pi_settings_exclusions(pi, capsys):
    write_skill(pi.agent / "skills" / "kept", named("kept"))
    write_skill(pi.agent / "skills" / "by-glob", named("by-glob"))
    write_skill(pi.agent / "skills" / "by-path", named("by-path"))
    extensions = pi.agent / "extensions"
    extensions.mkdir()
    (extensions / "kept.ts").write_text("x")
    (extensions / "legacy.ts").write_text("x")
    write_json(
        pi.agent / "settings.json",
        {
            "skills": ["!by-*", "-skills/by-path"],
            "extensions": ["!legacy*", "builtin:llama.cpp"],
        },
    )
    assert statuses(capsys) == {
        ("skill", "by-glob", "user"): "disabled",
        ("skill", "by-path", "user"): "disabled",
        ("skill", "kept", "user"): "enabled",
        ("plugin", "kept", "user"): "enabled",
        ("plugin", "legacy", "user"): "disabled",
    }


def test_pi_listed_skill_paths(pi, capsys, tmp_path):
    write_skill(tmp_path / "one-skill", named("one-skill"))
    write_skill(tmp_path / "many" / "from-dir", named("from-dir"))
    write_skill(tmp_path / "by-file", named("by-file"))
    write_json(
        pi.agent / "settings.json",
        {
            "skills": [
                str(tmp_path / "one-skill"),
                str(tmp_path / "many"),
                f"+{tmp_path}/by-file/SKILL.md",
            ]
        },
    )
    assert set(statuses(capsys)) == {
        ("skill", "by-file", "user"),
        ("skill", "from-dir", "user"),
        ("skill", "one-skill", "user"),
    }


def test_pi_mcp(pi, capsys):
    write_json(
        pi.agent / "mcp.json",
        {
            "mcpServers": {
                "local": {"command": "x"},
                "remote": {"url": "https://x.test/mcp"},
                "invalid": {"args": []},
                "off": {"command": "x", "enabled": False},
                "ignored-flag": {"command": "x", "disabled": True},
            }
        },
    )
    assert statuses(capsys) == {
        ("mcp", "ignored-flag", "user"): "enabled",
        ("mcp", "invalid", "user"): "broken",
        ("mcp", "local", "user"): "enabled",
        ("mcp", "off", "user"): "disabled",
        ("mcp", "remote", "user"): "enabled",
    }


def test_pi_builtin_mcp_can_be_turned_off(pi, capsys):
    write_json(pi.agent / "mcp.json", {"mcpServers": {"s": {"command": "x"}}})
    write_json(pi.agent / "settings.json", {"extensions": ["-builtin:mcp"]})
    assert statuses(capsys) == {("mcp", "s", "user"): "disabled"}


def test_pi_packages(pi, capsys):
    write_json(
        pi.agent / "settings.json",
        {
            "packages": [
                "npm:@example/tools",
                {"source": "git:github.com/example/more@v1", "skills": []},
                {"no-source": True},
            ]
        },
    )
    assert statuses(capsys) == {
        ("plugin", "git:github.com/example/more@v1", "user"): "enabled",
        ("plugin", "npm:@example/tools", "user"): "enabled",
    }


def test_pi_agent_dir_override(machine, capsys, tmp_path, monkeypatch):
    machine.install("pi")
    moved = tmp_path / "moved"
    write_skill(moved / "skills" / "moved-skill", named("moved-skill"))
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(moved))
    assert main([]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0] == f"pi  {moved} (set by PI_CODING_AGENT_DIR)"
    assert "moved-skill" in out


def test_pi_corrupt_settings_warn(pi, capsys):
    write_skill(pi.agent / "skills" / "one", named("one"))
    (pi.agent / "settings.json").write_text("{ not json")
    assert main([]) == 0
    captured = capsys.readouterr()
    assert " unknown" in captured.out and "warning: pi: cannot read" in captured.err


def test_antigravity_skill_locations(agy, capsys):
    write_skill(agy.cli / "skills" / "cli-skill", named("cli-skill"))
    write_skill(agy.shared / "skills" / "config-skill", named("config-skill"))
    write_skill(agy.home / ".gemini" / "skills" / "shared-skill", named("shared-skill"))
    write_skill(agy.project / ".agents" / "skills" / "repo", named("repo"))
    write_skill(agy.project / ".agent" / "skills" / "legacy-repo", named("legacy-repo"))
    write_skill(agy.home / ".gemini" / "antigravity" / "skills" / "ide-legacy", named("ide-legacy"))
    write_skill(agy.cli / "builtin" / "skills" / "built-in", named("built-in"))
    assert statuses(capsys) == {
        ("skill", "cli-skill", "user"): "enabled",
        ("skill", "config-skill", "user"): "enabled",
        ("skill", "legacy-repo", "project"): "pending",
        ("skill", "repo", "project"): "pending",
        ("skill", "shared-skill", "user"): "enabled",
    }


def test_antigravity_skill_rules(agy, capsys):
    skills = agy.cli / "skills"
    write_skill(skills / "no-desc", "name: no-desc")
    write_skill(skills / "no-name", DESCRIBED)
    write_skill(skills / ".hidden-dir", named("hidden-skill"))
    write_skill(skills / "group" / "nested", named("nested"))
    (skills / "no-fm").mkdir()
    (skills / "no-fm" / "SKILL.md").write_text("no frontmatter")
    (skills / "standalone.md").write_text(f"---\n{named('standalone')}\n---\n")
    assert statuses(capsys) == {
        ("skill", "SKILL", "user"): "enabled",
        ("skill", "hidden-skill", "user"): "enabled",
        ("skill", "no-desc", "user"): "enabled",
        ("skill", "no-fm", "user"): "broken",
    }


def test_antigravity_shared_skill_directory_counts_as_configured(machine, capsys):
    machine.install("agy")
    write_skill(machine.home / ".gemini" / "config" / "skills" / "one", named("one"))
    assert main([]) == 0
    assert capsys.readouterr().out.startswith("antigravity  ~/.gemini/config/skills")


def test_antigravity_mcp(agy, capsys):
    write_json(
        agy.shared / "mcp_config.json",
        {
            "mcpServers": {
                "local": {"command": "x"},
                "remote": {"serverUrl": "https://x.test/mcp"},
                "alias": {"url": "https://x.test/mcp"},
                "off": {"command": "x", "disabled": True},
                "unsupported": {"httpUrl": "https://x.test/mcp"},
            }
        },
    )
    write_json(
        agy.project / ".agents" / "mcp_config.json", {"mcpServers": {"repo": {"command": "x"}}}
    )
    assert statuses(capsys) == {
        ("mcp", "alias", "user"): "enabled",
        ("mcp", "local", "user"): "enabled",
        ("mcp", "off", "user"): "disabled",
        ("mcp", "remote", "user"): "enabled",
        ("mcp", "repo", "project"): "pending",
        ("mcp", "unsupported", "user"): "broken",
    }


def test_antigravity_blank_mcp_config_is_not_an_error(agy, capsys):
    (agy.shared / "mcp_config.json").write_text("")
    assert main([]) == 0
    assert capsys.readouterr().err == ""


def test_antigravity_trusted_workspace_enables_project_items(agy, capsys):
    write_json(agy.cli / "settings.json", {"trustedWorkspaces": [str(agy.project)]})
    write_json(
        agy.project / ".agents" / "mcp_config.json", {"mcpServers": {"repo": {"command": "x"}}}
    )
    assert statuses(capsys) == {("mcp", "repo", "project"): "enabled"}


def test_antigravity_plugins(agy, capsys):
    write_json(agy.shared / "plugins" / "dir-name" / "plugin.json", {"name": "manifest-name"})
    write_json(agy.shared / "plugins" / "nested" / ".antigravity-plugin" / "plugin.json", {})
    write_json(agy.shared / "plugins" / "off" / "plugin.json", {"name": "off"})
    write_json(agy.project / ".agents" / "plugins" / "repo" / "plugin.json", {"name": "repo"})
    write_json(agy.cli / "plugins" / "not-read" / "plugin.json", {"name": "not-read"})
    (agy.shared / "plugins" / "no-manifest").mkdir()
    write_json(agy.shared / "config.json", {"plugins": {"off": {"enabled": False}}})
    assert statuses(capsys) == {
        ("plugin", "dir-name", "user"): "enabled",
        ("plugin", "nested", "user"): "enabled",
        ("plugin", "off", "user"): "disabled",
        ("plugin", "repo", "project"): "pending",
    }


def test_antigravity_trusted_workspace_enables_project_plugin(agy, capsys):
    write_json(agy.cli / "settings.json", {"trustedWorkspaces": [str(agy.project)]})
    write_json(agy.project / ".agents" / "plugins" / "repo" / "plugin.json", {"name": "repo"})
    assert statuses(capsys) == {("plugin", "repo", "project"): "enabled"}


def test_antigravity_corrupt_plugin_state_warns(agy, capsys):
    write_json(agy.shared / "plugins" / "one" / "plugin.json", {"name": "one"})
    (agy.shared / "config.json").write_text("{ not json")
    assert main([]) == 0
    captured = capsys.readouterr()
    assert " unknown" in captured.out and "config.json" in captured.err


def test_skill_shared_between_new_and_old_agents(machine, capsys, tmp_path):
    machine.install("claude", "opencode", "pi")
    (machine.home / ".pi" / "agent").mkdir(parents=True)
    (machine.home / ".config" / "opencode").mkdir(parents=True)
    write_skill(machine.home / ".claude" / "skills" / "common", named("common"))
    write_skill(machine.home / ".agents" / "skills" / "both", named("both"))
    assert main(["--detail"]) == 0
    out = capsys.readouterr().out
    # OpenCode reads Claude Code's directory and shares ~/.agents/skills with Pi.
    assert out.count("Shared with:  opencode") == 2
    assert out.count("Shared with:  claude-code") == 1
    assert out.count("Shared with:  pi") == 1


def test_detail_names_all_the_other_agents(machine, capsys):
    machine.install("codex", "opencode", "pi")
    (machine.home / ".codex").mkdir()
    (machine.home / ".pi" / "agent").mkdir(parents=True)
    (machine.home / ".config" / "opencode").mkdir(parents=True)
    write_skill(machine.home / ".agents" / "skills" / "both", named("both"))
    assert main(["--detail"]) == 0
    out = capsys.readouterr().out
    assert "Shared with:  opencode, pi" in out
    assert "Shared with:  codex, pi" in out
    assert "Shared with:  codex, opencode" in out


def test_override_note_only_when_the_override_is_what_was_found(
    machine, capsys, monkeypatch, tmp_path
):
    machine.install("claude", "codex", "pi", "opencode")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "absent"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "absent"))
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(tmp_path / "absent"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "absent"))
    for directory in (".claude", ".codex", ".pi", ".opencode"):
        (machine.project / directory).mkdir()
    assert main([]) == 0
    assert "set by" not in capsys.readouterr().out


SECRET = "hunter2-do-not-print"


def plant_new_agent_secrets(machine):
    local = {"command": "server", "args": ["--token", SECRET], "env": {"API_KEY": SECRET}}
    remote_url = f"https://user:{SECRET}@x.test/mcp?key={SECRET}"
    headers = {"Authorization": f"Bearer {SECRET}"}
    write_json(
        machine.home / ".config" / "opencode" / "opencode.json",
        {
            "mcp": {
                "o-local": {
                    "type": "local",
                    "command": ["server", "--token", SECRET],
                    "environment": {"API_KEY": SECRET},
                },
                "o-remote": {"type": "remote", "url": remote_url, "headers": headers},
            },
            "plugin": [f"https://user:{SECRET}@x.test/plugin.tgz"],
        },
    )
    write_json(
        machine.home / ".pi" / "agent" / "mcp.json",
        {"mcpServers": {"p-local": local, "p-remote": {"url": remote_url, "headers": headers}}},
    )
    write_json(
        machine.home / ".pi" / "agent" / "settings.json",
        {"packages": [f"git:https://user:{SECRET}@x.test/repo"]},
    )
    (machine.home / ".gemini" / "antigravity-cli").mkdir(parents=True)
    write_json(
        machine.home / ".gemini" / "config" / "mcp_config.json",
        {
            "mcpServers": {
                "a-local": local,
                "a-remote": {"serverUrl": remote_url, "headers": headers},
            }
        },
    )


def test_new_agent_secrets_never_printed_or_kept(machine, capsys):
    machine.install("opencode", "pi", "agy")
    plant_new_agent_secrets(machine)
    assert main(["--detail"]) == 0
    captured = capsys.readouterr()
    for name in ("o-local", "o-remote", "p-local", "p-remote", "a-local", "a-remote"):
        assert name in captured.out
    assert SECRET not in captured.out + captured.err

    from agt_view.adapters.antigravity import Antigravity

    context = Context(home=machine.home, cwd=machine.project, env={"PATH": ""})
    for adapter in (OpenCode(), Pi(), Antigravity()):
        assert SECRET not in repr(adapter.scan(context).items)


def test_new_agents_never_start_anything(machine, capsys, tmp_path):
    machine.install("opencode", "pi", "agy")
    marker = tmp_path / "started"
    script = tmp_path / "server.sh"
    script.write_text(f"#!/bin/sh\ntouch {marker}\n")
    script.chmod(0o755)
    server = {"command": str(script)}
    write_json(
        machine.project / "opencode.json",
        {"mcp": {"o": {"type": "local", "command": [str(script)]}}},
    )
    write_json(machine.project / ".pi" / "mcp.json", {"mcpServers": {"p": server}})
    write_json(machine.project / ".agents" / "mcp_config.json", {"mcpServers": {"a": server}})
    plugin = machine.project / ".opencode" / "plugins" / "plugin.js"
    plugin.parent.mkdir(parents=True)
    plugin.write_text(f"require('fs').writeFileSync('{marker}', '')")
    extension = machine.project / ".pi" / "extensions" / "ext.ts"
    extension.parent.mkdir(parents=True)
    extension.write_text(f"require('fs').writeFileSync('{marker}', '')")
    assert main(["--all"]) == 0
    assert not marker.exists() and not machine.executed


@pytest.mark.parametrize(
    "corrupted",
    [
        ".config/opencode/opencode.json",
        ".pi/agent/mcp.json",
        ".pi/agent/settings.json",
        ".gemini/config/mcp_config.json",
        ".gemini/antigravity-cli/settings.json",
    ],
)
def test_one_corrupt_agent_leaves_the_others_intact(machine, capsys, corrupted):
    machine.install("opencode", "pi", "agy", "codex")
    for directory in (
        ".config/opencode",
        ".pi/agent",
        ".gemini/config",
        ".gemini/antigravity-cli",
        ".codex",
    ):
        (machine.home / directory).mkdir(parents=True)
    (machine.home / ".codex" / "config.toml").write_text('[mcp_servers.fine]\ncommand = "x"\n')
    (machine.home / corrupted).write_text("{ not json")
    assert main([]) == 0
    captured = capsys.readouterr()
    assert "fine" in captured.out
    assert f"cannot read {machine.home / corrupted}" in captured.err


def test_hostile_names_are_cleaned_for_every_new_agent(machine, capsys):
    machine.install("opencode", "pi", "agy")
    evil = "evil\x1b[2J\x1b]0;title\x07name"
    server = {"command": "x"}
    write_json(
        machine.home / ".config" / "opencode" / "opencode.json",
        {"mcp": {evil + "-o": {"type": "local", "command": ["x"]}}, "plugin": [evil + "-plugin"]},
    )
    write_json(machine.home / ".pi" / "agent" / "mcp.json", {"mcpServers": {evil + "-p": server}})
    (machine.home / ".gemini" / "antigravity-cli").mkdir(parents=True)
    write_json(
        machine.home / ".gemini" / "config" / "mcp_config.json",
        {"mcpServers": {evil + "-a": server}},
    )
    assert main(["--detail"]) == 0
    out = capsys.readouterr().out
    assert "\x1b" not in out and "\x07" not in out
    assert all(f"evilname-{suffix}" in out for suffix in ("o", "plugin", "p", "a"))


def test_every_agent_is_listed_in_rank_order(machine, capsys):
    names = ["antigravity", "pi", "opencode", "codex", "claude-code"]
    assert main([argument for name in names for argument in ("-a", name)]) == 0
    headers = [
        line.split()[0]
        for line in capsys.readouterr().out.splitlines()
        if line and not line.startswith(" ")
    ]
    assert headers == ["claude-code", "codex", "opencode", "pi", "antigravity"]
