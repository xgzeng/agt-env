"""Codex rules from docs/agents/codex.md, without the real agent."""

import json
import shutil
from pathlib import Path

import pytest
from conftest import write_skill

from agt_env.adapters.base import Context
from agt_env.adapters.codex import Codex, _active_version, _compare_versions, _hook_hash
from agt_env.hooks import handlers
from agt_env.model import Kind, Scope, Status

COMMAND = {"type": "command", "command": "/bin/true"}
SERVER = {"command": "/bin/true"}
ON = '[plugins."p@m"]\nenabled = true\n'


class Fixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.home = root / "home"
        self.project = root / "project"
        self.codex = self.home / ".codex"
        self.system = root / "system" / "etc" / "codex"
        self.codex.mkdir(parents=True)
        self.project.mkdir()

    @property
    def trust(self) -> str:
        return f'[projects."{self.project}"]\ntrust_level = "trusted"\n'

    def write(self, path: Path, content: object) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content if isinstance(content, str) else json.dumps(content))
        return path

    def config(self, text: str) -> None:
        self.write(self.codex / "config.toml", text)

    def project_config(self, text: str) -> None:
        self.write(self.project / ".codex" / "config.toml", text)

    def plugin(self, plugin_id="p@m", version="1.0.0", manifest=None, where=None) -> Path:
        name, _, marketplace = plugin_id.partition("@")
        root = self.codex / "plugins" / "cache" / marketplace / name / version
        root.mkdir(parents=True)
        self.write(
            root / (where or ".codex-plugin/plugin.json"),
            {"name": name} if manifest is None else manifest,
        )
        return root

    def scan(self):
        return Codex().scan(
            Context(
                home=self.home,
                cwd=self.project,
                env={"PATH": ""},
                system_root=self.root / "system",
            )
        )

    def rows(self, kind):
        return {
            (item.name, item.scope): item.status for item in self.scan().items if item.kind is kind
        }

    def statuses(self, kind):
        return {item.name: item.status for item in self.scan().items if item.kind is kind}

    def item(self, kind, name):
        return next(item for item in self.scan().items if item.kind is kind and item.name == name)


@pytest.fixture
def fx(tmp_path):
    return Fixture(tmp_path)


# Servers and rejected configs


@pytest.mark.parametrize(
    "config",
    [
        '[mcp_servers.bad]\nargs = ["x"]\n',
        '[mcp_servers.bad]\ncommand = "x"\nurl = "https://x.test"\n',
        '[mcp_servers.bad]\ncommand = "x"\nenabled = "yes"\n',
        '[mcp_servers.bad]\ncommand = "x"\nhttp_headers = {}\n',
        '[mcp_servers.bad]\nurl = "https://x.test"\nargs = []\n',
    ],
)
def test_an_invalid_server_makes_codex_reject_everything(fx, config):
    write_skill(fx.home / ".agents" / "skills" / "a")
    fx.config(config + '[mcp_servers.good]\ncommand = "x"\n')
    report = fx.scan()
    statuses = {item.name: item.status for item in report.items}
    assert statuses["bad"] is Status.BROKEN
    assert statuses["good"] is Status.UNKNOWN
    assert statuses["a"] is Status.UNKNOWN
    assert any("Codex rejects" in warning for warning in report.warnings)


def test_a_merged_entry_with_both_transports_is_rejected(fx):
    fx.config(fx.trust + '[mcp_servers.s]\ncommand = "x"\n[mcp_servers.other]\ncommand = "x"\n')
    fx.project_config('[mcp_servers.s]\nurl = "https://x.test"\n')
    report = fx.scan()
    assert any("Codex rejects" in warning for warning in report.warnings)
    assert {i.name: i.status for i in report.items if i.kind is Kind.MCP}["other"] is Status.UNKNOWN


@pytest.mark.parametrize("project", ["not toml [", '[mcp_servers.bad]\nargs = ["x"]\n'])
def test_only_a_trusted_project_can_stop_codex(fx, project):
    fx.project_config(project)
    fx.config('[mcp_servers.good]\ncommand = "x"\n')
    assert fx.rows(Kind.MCP)[("good", Scope.USER)] is Status.ENABLED
    fx.config(fx.trust + '[mcp_servers.good]\ncommand = "x"\n')
    assert fx.rows(Kind.MCP)[("good", Scope.USER)] is Status.UNKNOWN


def test_a_corrupt_user_config_leaves_everything_unknown(fx):
    fx.config("not toml [")
    fx.write(fx.codex / "hooks.json", {"hooks": {"Stop": [{"hooks": [COMMAND]}]}})
    fx.write(
        fx.codex / "agents" / "a.toml",
        'name = "a"\ndescription = "d"\ndeveloper_instructions = "x"\n',
    )
    write_skill(fx.home / ".agents" / "skills" / "s")
    assert {item.status for item in fx.scan().items} == {Status.UNKNOWN}


@pytest.mark.parametrize(
    "config",
    [
        '[[skills.config]]\nname = "a"\n',
        '[plugins."p@m"]\nenabled = "x"\n',
        "plugins = 3\n",
        '[plugins._default]\nenabled = "x"\n',
        "agents = 3\n",
        '[skills.bundled]\nenabled = "x"\n',
        '[[hooks.Stop]]\n[[hooks.Stop.hooks]]\ncommand = "x"\n',
    ],
)
def test_sections_of_the_wrong_shape_reject_the_file(fx, config):
    fx.config(config + '[mcp_servers.good]\ncommand = "x"\n')
    report = fx.scan()
    assert any("Codex rejects" in warning for warning in report.warnings)
    assert {item.status for item in report.items if item.kind is Kind.MCP} == {Status.UNKNOWN}


def test_unknown_fields_and_events_are_accepted(fx):
    fx.config(
        '[mcp_servers.good]\ncommand = "x"\nnope = 1\n'
        '[plugins."p@m"]\nnope = 1\nenabled = false\n'
        '[[hooks.Nope]]\n[[hooks.Nope.hooks]]\ntype = "command"\ncommand = "x"\n'
    )
    report = fx.scan()
    assert not report.warnings
    assert fx.rows(Kind.MCP)[("good", Scope.USER)] is Status.ENABLED
    assert not fx.rows(Kind.HOOK)


# Skills


@pytest.mark.parametrize(
    ("rules", "expected"),
    [
        ('[[skills.config]]\nname = "a"\nenabled = false\n', Status.DISABLED),
        ('[[skills.config]]\nname = " a "\nenabled = false\n', Status.DISABLED),
        (
            '[[skills.config]]\npath = "~/.agents/skills/a/SKILL.md"\nenabled = false\n',
            Status.DISABLED,
        ),
        (
            '[[skills.config]]\npath = "../.agents/skills/a/SKILL.md"\nenabled = false\n',
            Status.DISABLED,
        ),
        (
            (
                '[[skills.config]]\nname = "a"\nenabled = false\n'
                '[[skills.config]]\npath = "~/.agents/skills/a/SKILL.md"\nenabled = true\n'
            ),
            Status.ENABLED,
        ),
        (
            (
                '[[skills.config]]\npath = "~/.agents/skills/a/SKILL.md"\nenabled = false\n'
                '[[skills.config]]\nname = "a"\nenabled = true\n'
            ),
            Status.ENABLED,
        ),
        (
            '[[skills.config]]\npath = "~/.agents/skills/a/SKILL.md"\nname = "a"\nenabled = false\n',
            Status.ENABLED,
        ),
        ('[[skills.config]]\nname = "  "\nenabled = false\n', Status.ENABLED),
    ],
)
def test_skill_rules(fx, rules, expected):
    write_skill(fx.home / ".agents" / "skills" / "a")
    fx.config(rules)
    assert fx.statuses(Kind.SKILL)["a"] is expected


def test_a_project_skill_rule_doesnt_count(fx):
    write_skill(fx.project / ".agents" / "skills" / "a")
    fx.config(fx.trust)
    fx.project_config('[[skills.config]]\nname = "a"\nenabled = false\n')
    assert fx.statuses(Kind.SKILL)["a"] is Status.ENABLED


def test_a_name_rule_needs_a_plugin_skills_full_name(fx):
    write_skill(fx.plugin() / "skills" / "s")
    fx.config(ON + '[[skills.config]]\nname = "s"\nenabled = false\n')
    assert fx.statuses(Kind.SKILL)["p:s"] is Status.ENABLED
    fx.config(ON + '[[skills.config]]\nname = "p:s"\nenabled = false\n')
    assert fx.statuses(Kind.SKILL)["p:s"] is Status.DISABLED


def test_turning_bundled_skills_off(fx):
    write_skill(fx.codex / "skills" / ".system" / "bundled")
    write_skill(fx.home / ".agents" / "skills" / "mine")
    assert fx.statuses(Kind.SKILL)["bundled"] is Status.ENABLED
    fx.config("[skills.bundled]\nenabled = false\n")
    statuses = fx.statuses(Kind.SKILL)
    assert statuses["bundled"] is Status.DISABLED
    assert statuses["mine"] is Status.ENABLED


def test_a_trusted_project_can_turn_bundled_skills_off(fx):
    write_skill(fx.codex / "skills" / ".system" / "bundled")
    fx.project_config("[skills.bundled]\nenabled = false\n")
    assert fx.statuses(Kind.SKILL)["bundled"] is Status.ENABLED
    fx.config(fx.trust)
    assert fx.statuses(Kind.SKILL)["bundled"] is Status.DISABLED


def test_long_names_are_refused(fx):
    agents = fx.home / ".agents" / "skills"
    write_skill(agents / "ok", f"name: {'n' * 64}\ndescription: d")
    write_skill(agents / "long", f"name: {'m' * 65}\ndescription: d")
    write_skill(agents / ("x" * 65))
    statuses = fx.statuses(Kind.SKILL)
    assert statuses["n" * 64] is Status.ENABLED
    assert statuses["m" * 65] is Status.BROKEN
    assert statuses["x" * 65] is Status.BROKEN
    assert "longer than 64" in fx.item(Kind.SKILL, "m" * 65).findings[0].text


def test_a_skill_linked_into_a_plugin_is_the_plugins(fx):
    root = fx.plugin()
    write_skill(root / "skills" / "s")
    (fx.home / ".agents" / "skills").mkdir(parents=True)
    (fx.home / ".agents" / "skills" / "s").symlink_to(root / "skills" / "s")
    fx.config(ON)
    assert set(fx.statuses(Kind.SKILL)) == {"p:s"}
    fx.config('[plugins."p@m"]\nenabled = false\n')
    assert set(fx.statuses(Kind.SKILL)) == {"s"}


# Plugins


def test_plugins_are_on_without_an_explicit_setting(fx):
    write_skill(fx.plugin() / "skills" / "s")
    fx.config('[plugins."p@m"]\n')
    assert fx.statuses(Kind.PLUGIN) == {"p@m": Status.ENABLED}
    assert fx.statuses(Kind.SKILL)["p:s"] is Status.ENABLED


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        ('[plugins._default]\nenabled = false\n[plugins."p@m"]\n', Status.DISABLED),
        ('[plugins._default]\nenabled = false\n[plugins."p@m"]\nenabled = true\n', Status.ENABLED),
        ('[plugins._default]\nenabled = true\n[plugins."p@m"]\n', Status.ENABLED),
    ],
)
def test_the_plugin_default(fx, config, expected):
    fx.plugin()
    fx.config(config)
    assert fx.statuses(Kind.PLUGIN) == {"p@m": expected}


def test_a_project_can_set_the_plugin_default(fx):
    fx.plugin()
    fx.config(fx.trust + '[plugins."p@m"]\n')
    fx.project_config("[plugins._default]\nenabled = false\n")
    assert fx.statuses(Kind.PLUGIN) == {"p@m": Status.DISABLED}


def test_a_project_entry_without_enabled_inherits_the_users(fx):
    fx.plugin()
    fx.config(fx.trust + '[plugins."p@m"]\nenabled = false\n')
    fx.project_config('[plugins."p@m"]\n')
    assert fx.rows(Kind.PLUGIN)[("p@m", Scope.PROJECT)] is Status.DISABLED


@pytest.mark.parametrize("plugin_id", ["@m", "p@", "../x@m", "a/b@m", "noat", "p@m.x", ".p@m"])
def test_invalid_plugin_ids_are_broken(fx, plugin_id):
    write_skill(fx.codex / "plugins" / "cache" / "m" / "other" / "1.0.0" / "skills" / "s")
    fx.config(f'[plugins."{plugin_id}"]\nenabled = true\n')
    assert fx.statuses(Kind.PLUGIN) == {plugin_id: Status.BROKEN}
    assert fx.item(Kind.PLUGIN, plugin_id).findings[0].text == "invalid plugin ID"
    assert not [name for name in fx.statuses(Kind.SKILL) if ":" in name]


def test_an_id_uses_the_last_at_sign(fx):
    fx.config('[plugins."a@b@m"]\nenabled = true\n')
    assert fx.item(Kind.PLUGIN, "a@b@m").findings[0].text == "invalid plugin ID"


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        ("1.0.0-alpha.2", "1.0.0-alpha.10", -1),
        ("1.0.0-alpha", "1.0.0-alpha.1", -1),
        ("1.0.0-alpha.1", "1.0.0-beta", -1),
        ("1.0.0-1", "1.0.0-a", -1),
        ("1.0.0-rc.1", "1.0.0", -1),
        ("1.0.0", "1.0.0+b", -1),
        ("1.0.0+1", "1.0.0+2", -1),
        ("10.0.0", "9.0.0", 1),
        ("1.2.3", "1.2.3", 0),
        ("1.10", "1.9", -1),
        ("01.0.0", "1.0.0", -1),
    ],
)
def test_version_order(left, right, expected):
    assert _compare_versions(left, right) == expected
    assert _compare_versions(right, left) == -expected


def test_local_beats_any_version(tmp_path):
    for name in ("local", "10.0.0", "zzz"):
        (tmp_path / name).mkdir()
    assert _active_version(tmp_path) == tmp_path / "local"


def test_versions_with_invalid_names_and_links_are_skipped(tmp_path):
    (tmp_path / "1.0.0").mkdir()
    (tmp_path / "9 bad").mkdir()
    (tmp_path / "2.0.0").symlink_to(tmp_path / "1.0.0")
    assert _active_version(tmp_path) == tmp_path / "1.0.0"


@pytest.mark.parametrize(
    ("where", "manifest", "prefix"),
    [
        (".claude-plugin/plugin.json", {"name": "q"}, "q"),
        (".cursor-plugin/plugin.json", {"name": "q"}, "q"),
        (
            "plugin.json",
            {
                "name": "q",
                "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
            },
            "q",
        ),
        (".codex-plugin/plugin.json", {"name": ""}, "1.0.0"),
    ],
)
def test_manifest_locations(fx, where, manifest, prefix):
    write_skill(fx.plugin(manifest=manifest, where=where) / "skills" / "s")
    fx.config(ON)
    assert fx.statuses(Kind.SKILL) == {f"{prefix}:s": Status.ENABLED}


def test_a_root_manifest_needs_the_schema(fx):
    write_skill(fx.plugin(manifest={"name": "q"}, where="plugin.json") / "skills" / "s")
    fx.config(ON)
    assert fx.statuses(Kind.PLUGIN) == {"p@m": Status.BROKEN}
    assert not fx.statuses(Kind.SKILL)


def test_the_first_manifest_present_counts_even_when_invalid(fx):
    root = fx.plugin(manifest="{bad")
    fx.write(root / ".claude-plugin" / "plugin.json", {"name": "q"})
    write_skill(root / "skills" / "s")
    fx.config(ON)
    report = fx.scan()
    assert not [item for item in report.items if item.kind is Kind.SKILL]
    assert len([w for w in report.warnings if "plugin.json" in w]) == 1


@pytest.mark.parametrize(
    "manifest",
    ["[1, 2]", "{bad", {"name": 3}, {"name": "p", "version": 3}, {"name": "p", "keywords": 3}],
)
def test_an_invalid_manifest_warns_once_and_breaks_the_plugin(fx, manifest):
    write_skill(fx.plugin(manifest=manifest) / "skills" / "s")
    fx.config(ON)
    report = fx.scan()
    plugin = next(item for item in report.items if item.kind is Kind.PLUGIN)
    assert plugin.status is Status.BROKEN and plugin.findings
    assert len([w for w in report.warnings if "plugin.json" in w]) == 1


def test_a_bad_optional_field_doesnt_cost_the_manifest(fx):
    write_skill(fx.plugin(manifest={"name": "p", "skills": 3, "hooks": 3}) / "skills" / "s")
    fx.config(ON)
    assert fx.statuses(Kind.SKILL) == {"p:s": Status.ENABLED}


def test_agent_plugins_read_only_direct_skills_and_no_hooks(fx):
    root = fx.plugin(
        manifest={
            "name": "q",
            "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
        },
        where="plugin.json",
    )
    write_skill(root / "skills" / "top")
    write_skill(root / "skills" / "group" / "deep")
    fx.write(root / "hooks" / "hooks.json", {"hooks": {"Stop": [{"hooks": [COMMAND]}]}})
    fx.config(ON)
    assert set(fx.statuses(Kind.SKILL)) == {"q:top"}
    assert not fx.statuses(Kind.HOOK)


# Plugin servers


@pytest.mark.parametrize("content", [{"mcpServers": {"s": SERVER}}, {"s": SERVER}])
def test_plugin_servers_are_listed(fx, content):
    fx.write(fx.plugin() / ".mcp.json", content)
    fx.config(ON)
    item = fx.item(Kind.MCP, "s")
    assert item.status is Status.ENABLED
    assert item.details["plugin"] == "p@m"
    assert item.native_kind == "plugin mcp server"


def test_an_inline_or_named_server_file(fx):
    fx.plugin(manifest={"name": "p", "mcpServers": {"inline": SERVER}})
    root = fx.plugin("a@m", manifest={"name": "a", "mcpServers": "./x.json"})
    fx.write(root / "x.json", {"mcpServers": {"named": SERVER}})
    fx.config(ON + '[plugins."a@m"]\nenabled = true\n')
    assert fx.statuses(Kind.MCP) == {"inline": Status.ENABLED, "named": Status.ENABLED}


def test_mcp_json_without_the_dot_isnt_read(fx):
    fx.write(fx.plugin() / "mcp.json", {"mcpServers": {"s": SERVER}})
    fx.config(ON)
    assert not fx.statuses(Kind.MCP)


def test_a_disabled_or_untrusted_plugin_brings_no_running_servers(fx):
    fx.write(fx.plugin() / ".mcp.json", {"mcpServers": {"s": SERVER}})
    fx.config('[plugins."p@m"]\nenabled = false\n')
    assert not fx.statuses(Kind.MCP)
    fx.config("")
    fx.project_config(ON)
    assert fx.statuses(Kind.MCP) == {"s": Status.PENDING}


def test_a_plugin_entry_can_turn_its_server_off(fx):
    fx.write(fx.plugin() / ".mcp.json", {"mcpServers": {"s": SERVER}})
    fx.config(ON + '[plugins."p@m".mcp_servers.s]\nenabled = false\n')
    assert fx.statuses(Kind.MCP) == {"s": Status.DISABLED}


def test_a_configured_server_beats_a_plugins(fx):
    fx.write(fx.plugin() / ".mcp.json", {"mcpServers": {"s": SERVER}})
    fx.config(ON + '[mcp_servers.s]\nurl = "https://x.test"\n')
    rows = [item for item in fx.scan().items if item.kind is Kind.MCP]
    assert {item.details.get("plugin"): item.status for item in rows} == {
        None: Status.ENABLED,
        "p@m": Status.SHADOWED,
    }


def test_the_first_plugin_in_id_order_gets_a_shared_server_name(fx):
    for name in ("p", "a"):
        fx.write(fx.plugin(f"{name}@m") / ".mcp.json", {"mcpServers": {"s": SERVER}})
    fx.config(ON + '[plugins."a@m"]\nenabled = true\n')
    rows = [item for item in fx.scan().items if item.kind is Kind.MCP]
    assert {item.details["plugin"]: item.status for item in rows} == {
        "a@m": Status.ENABLED,
        "p@m": Status.SHADOWED,
    }


def test_a_bad_plugin_server_only_costs_itself(fx):
    fx.write(fx.plugin() / ".mcp.json", {"mcpServers": {"bad": {"args": []}, "ok": SERVER}})
    fx.config(ON)
    assert fx.statuses(Kind.MCP) == {"bad": Status.BROKEN, "ok": Status.ENABLED}


def test_a_corrupt_server_file_warns(fx):
    fx.write(fx.plugin() / ".mcp.json", "{bad")
    fx.config(ON)
    report = fx.scan()
    assert any(".mcp.json" in warning for warning in report.warnings)
    assert not [item for item in report.items if item.kind is Kind.MCP]


def test_plugin_server_secrets_stay_hidden(fx):
    secret = "sk-supersecretvalue"
    fx.write(
        fx.plugin() / ".mcp.json",
        {"mcpServers": {"s": {"command": "/bin/true", "args": [secret], "env": {"K": secret}}}},
    )
    fx.config(ON)
    assert secret not in repr(fx.item(Kind.MCP, "s"))


# Hooks


@pytest.mark.parametrize(
    ("event", "extra", "limit_counts"),
    [
        ("PreToolUse", {"additionalContextLimit": 5000}, True),
        ("PreToolUse", {"additionalContextLimit": 2500}, False),
        ("Stop", {"additionalContextLimit": 5000}, False),
        ("PreToolUse", {"commandWindows": "x"}, False),
    ],
)
def test_what_the_fingerprint_covers(event, extra, limit_counts):
    plain = {event: [{"hooks": [COMMAND]}]}
    changed = {event: [{"hooks": [{**COMMAND, **extra}]}]}
    assert (
        _hook_hash(next(handlers(plain))) != _hook_hash(next(handlers(changed)))
    ) is limit_counts


def test_a_status_message_counts_for_an_mcp_hook():
    plain = {"type": "mcp_tool", "server": "s", "tool": "t"}
    events = [{"Stop": [{"hooks": [hook]}]} for hook in (plain, {**plain, "statusMessage": "x"})]
    assert _hook_hash(next(handlers(events[0]))) != _hook_hash(next(handlers(events[1])))


def test_a_matcher_counts_only_on_events_that_use_one():
    bare = next(handlers({"Stop": [{"hooks": [COMMAND]}]}))
    matched = next(handlers({"Stop": [{"matcher": "x", "hooks": [COMMAND]}]}))
    assert _hook_hash(bare) == _hook_hash(matched)


def test_timeouts_are_normalized_before_the_fingerprint():
    def fingerprint(event, **fields):
        return _hook_hash(next(handlers({event: [{"hooks": [{**COMMAND, **fields}]}]})))

    assert fingerprint("SessionEnd") == fingerprint("SessionEnd", timeout=1)
    assert fingerprint("SessionEnd", timeout=9999) == fingerprint("SessionEnd", timeout=3)
    assert fingerprint("Interrupt") == fingerprint("Interrupt", timeout=1)
    assert fingerprint("Stop", timeout=0) == fingerprint("Stop", timeout=1)
    assert fingerprint("Stop") == fingerprint("Stop", timeout=600)


@pytest.mark.parametrize(
    "hook",
    [
        {"type": "mcp_tool", "server": "s"},
        {"command": "x"},
        {"type": "zzz"},
        {**COMMAND, "timeout": "x"},
        {"type": "mcp_tool", "server": "s", "tool": "t", "input": {"a": None}},
    ],
)
def test_one_invalid_handler_costs_the_whole_file(fx, hook):
    fx.write(fx.codex / "hooks.json", {"hooks": {"Stop": [{"hooks": [COMMAND, hook]}]}})
    report = fx.scan()
    assert not [item for item in report.items if item.kind is Kind.HOOK]
    assert any("hooks.json" in warning for warning in report.warnings)


@pytest.mark.parametrize("events", [{"Stop": {}}, {"Stop": [3]}, {"Stop": [{"matcher": 3}]}])
def test_a_badly_shaped_event_costs_the_whole_file(fx, events):
    fx.write(fx.codex / "hooks.json", {"hooks": events})
    assert any("hooks.json" in warning for warning in fx.scan().warnings)


def test_unknown_events_are_ignored(fx):
    fx.write(
        fx.codex / "hooks.json",
        {"hooks": {"Nope": 3, "Other": [{"hooks": [COMMAND]}], "Stop": [{"hooks": [COMMAND]}]}},
    )
    report = fx.scan()
    assert not report.warnings
    assert [item.name for item in report.items if item.kind is Kind.HOOK] == ["Stop"]


@pytest.mark.parametrize(
    ("group", "reason"),
    [
        ({"hooks": [{"type": "prompt", "prompt": "x"}]}, "doesn't support prompt"),
        ({"hooks": [{"type": "command", "command": "  "}]}, "empty hook command"),
        ({"hooks": [{"type": "mcp_tool", "server": " ", "tool": "t"}]}, "server and a tool"),
    ],
)
def test_a_skipped_handler_keeps_the_rest_of_the_file(fx, group, reason):
    fx.write(
        fx.codex / "hooks.json",
        {"hooks": {"Stop": [group], "SessionStart": [{"hooks": [COMMAND]}]}},
    )
    statuses = fx.statuses(Kind.HOOK)
    assert statuses["Stop"] is Status.BROKEN
    assert statuses["SessionStart"] is Status.PENDING
    assert reason in fx.item(Kind.HOOK, "Stop").findings[0].text


def test_an_mcp_hook_cant_run_on_session_end(fx):
    hook = {"type": "mcp_tool", "server": "s", "tool": "t"}
    fx.write(fx.codex / "hooks.json", {"hooks": {"SessionEnd": [{"hooks": [hook]}]}})
    assert fx.statuses(Kind.HOOK) == {"SessionEnd": Status.BROKEN}


def test_an_invalid_matcher_is_broken(fx):
    fx.write(
        fx.codex / "hooks.json",
        {"hooks": {"PreToolUse": [{"matcher": "(", "hooks": [COMMAND]}]}},
    )
    assert fx.item(Kind.HOOK, "PreToolUse:(").findings[0].text == "invalid matcher"


def test_the_old_feature_name(fx):
    fx.write(fx.codex / "hooks.json", {"hooks": {"Stop": [{"hooks": [COMMAND]}]}})
    fx.config("[features]\ncodex_hooks = false\n")
    assert fx.statuses(Kind.HOOK) == {"Stop": Status.DISABLED}
    fx.config("[features]\nhooks = true\ncodex_hooks = false\n")
    assert fx.statuses(Kind.HOOK) == {"Stop": Status.PENDING}
    fx.config("[features]\nhooks = false\ncodex_hooks = true\n")
    assert fx.statuses(Kind.HOOK) == {"Stop": Status.DISABLED}


def test_a_trusted_project_can_turn_hooks_off(fx):
    fx.write(fx.codex / "hooks.json", {"hooks": {"Stop": [{"hooks": [COMMAND]}]}})
    fx.config(fx.trust)
    fx.project_config("[features]\nhooks = false\n")
    assert fx.statuses(Kind.HOOK) == {"Stop": Status.DISABLED}


def test_a_plugin_hook_waits_with_its_plugin(fx):
    fx.write(fx.plugin() / "hooks" / "hooks.json", {"hooks": {"Stop": [{"hooks": [COMMAND]}]}})
    fx.project_config(ON)
    fx.config("")
    assert fx.statuses(Kind.PLUGIN) == {"p@m": Status.PENDING}
    assert fx.statuses(Kind.HOOK) == {"Stop": Status.PENDING}


def test_inline_and_listed_plugin_hooks(fx):
    hook = {"hooks": {"Stop": [{"hooks": [COMMAND]}]}}
    fx.plugin(manifest={"name": "p", "hooks": hook})
    root = fx.plugin("a@m", manifest={"name": "a", "hooks": ["./one.json", "./two.json"]})
    fx.write(root / "one.json", hook)
    fx.write(root / "two.json", hook)
    fx.config(ON + '[plugins."a@m"]\nenabled = true\n')
    assert len([item for item in fx.scan().items if item.kind is Kind.HOOK]) == 3


# System files and requirements


def test_the_system_config_is_the_lowest_layer(fx):
    fx.write(
        fx.system / "config.toml",
        '[mcp_servers.sys]\ncommand = "x"\n[mcp_servers.s]\ncommand = "x"\n',
    )
    fx.config("[mcp_servers.s]\nenabled = false\n")
    rows = fx.rows(Kind.MCP)
    assert rows[("sys", Scope.SYSTEM)] is Status.ENABLED
    assert rows[("s", Scope.SYSTEM)] is Status.SHADOWED
    assert rows[("s", Scope.USER)] is Status.DISABLED


def test_system_settings_count(fx):
    write_skill(fx.codex / "skills" / ".system" / "bundled")
    write_skill(fx.home / ".agents" / "skills" / "a")
    fx.write(fx.plugin() / "x.txt", "x")
    fx.config('[plugins."p@m"]\n')
    fx.write(
        fx.system / "config.toml",
        "[skills.bundled]\nenabled = false\n[plugins._default]\nenabled = false\n"
        '[[skills.config]]\nname = "a"\nenabled = false\n',
    )
    skills = fx.statuses(Kind.SKILL)
    assert skills["bundled"] is Status.DISABLED
    assert skills["a"] is Status.ENABLED, "a system skill rule doesn't count"
    assert fx.statuses(Kind.PLUGIN) == {"p@m": Status.DISABLED}


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("config.toml", "bad ["),
        ("config.toml", '[mcp_servers.bad]\nargs = ["x"]\n'),
        ("requirements.toml", "bad ["),
    ],
)
def test_system_files_can_stop_codex(fx, name, content):
    fx.write(fx.system / name, content)
    fx.config('[mcp_servers.u]\ncommand = "x"\n')
    assert fx.rows(Kind.MCP)[("u", Scope.USER)] is Status.UNKNOWN


def test_system_hooks_need_no_review(fx):
    fx.write(fx.system / "hooks.json", {"hooks": {"Stop": [{"hooks": [COMMAND]}]}})
    fx.write(
        fx.system / "config.toml",
        '[[hooks.PreToolUse]]\n[[hooks.PreToolUse.hooks]]\ntype = "command"\ncommand = "x"\n',
    )
    assert fx.rows(Kind.HOOK) == {
        ("Stop", Scope.SYSTEM): Status.ENABLED,
        ("PreToolUse", Scope.SYSTEM): Status.ENABLED,
    }


def test_required_hooks_are_listed_as_managed(fx):
    fx.write(
        fx.system / "requirements.toml",
        '[[hooks.Stop]]\n[[hooks.Stop.hooks]]\ntype = "command"\ncommand = "/bin/true"\n',
    )
    item = fx.item(Kind.HOOK, "Stop")
    assert item.status is Status.ENABLED
    assert item.scope is Scope.SYSTEM
    assert item.native_kind == "managed hook"


def test_allow_managed_hooks_only(fx):
    hook = {"hooks": {"SessionStart": [{"hooks": [COMMAND]}]}}
    fx.write(fx.system / "requirements.toml", "allow_managed_hooks_only = true\n")
    fx.write(fx.system / "hooks.json", {"hooks": {"Stop": [{"hooks": [COMMAND]}]}})
    fx.write(fx.codex / "hooks.json", hook)
    fx.write(
        fx.plugin() / "hooks" / "hooks.json", {"hooks": {"PreCompact": [{"hooks": [COMMAND]}]}}
    )
    fx.config(ON)
    statuses = fx.statuses(Kind.HOOK)
    assert statuses["Stop"] is Status.ENABLED
    assert statuses["SessionStart"] is Status.DISABLED
    assert statuses["PreCompact"] is Status.DISABLED
    assert "allow_managed_hooks_only" in fx.item(Kind.HOOK, "SessionStart").findings[0].text


def test_allow_managed_hooks_only_in_a_config_file_means_nothing(fx):
    fx.write(fx.codex / "hooks.json", {"hooks": {"Stop": [{"hooks": [COMMAND]}]}})
    fx.config("allow_managed_hooks_only = true\n")
    assert fx.statuses(Kind.HOOK) == {"Stop": Status.PENDING}


# Subagents


GOOD = 'description = "d"\ndeveloper_instructions = "x"\n'


@pytest.mark.parametrize(
    ("content", "reason"),
    [
        (f'name = ""\n{GOOD}', "needs a name"),
        (f'name = "  "\n{GOOD}', "needs a name"),
        (
            'name = "b"\ndescription = " "\ndeveloper_instructions = "x"\n',
            "description can't be blank",
        ),
        (
            'name = "b"\ndescription = "d"\ndeveloper_instructions = " "\n',
            "developer_instructions can't",
        ),
        ('name = "b"\ndescription = "d"\n', "needs developer_instructions"),
        ('name = "b"\ndeveloper_instructions = "x"\n', "needs a description"),
        (f"name = 3\n{GOOD}", "name isn't text"),
        ("not toml [", "isn't valid TOML"),
    ],
)
def test_invalid_role_files_say_why(fx, content, reason):
    fx.write(fx.codex / "agents" / "file.toml", content)
    item = next(item for item in fx.scan().items if item.kind is Kind.SUBAGENT)
    assert item.status is Status.BROKEN
    assert reason in item.findings[0].text


def test_a_dangling_link_says_so(fx):
    (fx.codex / "agents").mkdir()
    (fx.codex / "agents" / "gone.toml").symlink_to(fx.root / "nowhere")
    item = fx.item(Kind.SUBAGENT, "gone")
    assert item.status is Status.BROKEN
    assert item.findings[0].text == "symlink target is missing"


def test_role_files_are_found_at_any_depth(fx):
    fx.write(fx.codex / "agents" / "a" / "b" / "deep.toml", f'name = "deep"\n{GOOD}')
    assert fx.statuses(Kind.SUBAGENT) == {"deep": Status.ENABLED}


def test_a_higher_layer_replaces_a_role(fx):
    fx.write(fx.codex / "agents" / "w.toml", f'name = "worker"\n{GOOD}')
    fx.write(fx.project / ".codex" / "agents" / "w.toml", f'name = "worker"\n{GOOD}')
    assert fx.rows(Kind.SUBAGENT) == {
        ("worker", Scope.USER): Status.ENABLED,
        ("worker", Scope.PROJECT): Status.PENDING,
    }
    fx.config(fx.trust)
    assert fx.rows(Kind.SUBAGENT) == {
        ("worker", Scope.USER): Status.SHADOWED,
        ("worker", Scope.PROJECT): Status.ENABLED,
    }


def test_a_role_can_inherit_a_description_from_a_lower_layer(fx):
    fx.write(fx.codex / "agents" / "w.toml", f'name = "worker"\n{GOOD}')
    fx.write(
        fx.project / ".codex" / "agents" / "w.toml",
        'name = "worker"\ndeveloper_instructions = "y"\n',
    )
    fx.config(fx.trust)
    assert fx.rows(Kind.SUBAGENT)[("worker", Scope.PROJECT)] is Status.ENABLED
    assert fx.item(Kind.SUBAGENT, "worker").description == "d"


def test_a_repeated_name_in_one_layer_is_refused(fx):
    fx.write(fx.codex / "agents" / "a.toml", f'name = "same"\n{GOOD}')
    fx.write(fx.codex / "agents" / "b.toml", f'name = "same"\n{GOOD}')
    rows = [item for item in fx.scan().items if item.kind is Kind.SUBAGENT]
    assert sorted(item.status.value for item in rows) == ["broken", "enabled"]


def test_inline_roles(fx):
    fx.config('[agents]\nmax_threads = 2\n[agents.inline]\ndescription = "d"\n[agents.empty]\n')
    assert fx.statuses(Kind.SUBAGENT) == {"inline": Status.ENABLED, "empty": Status.BROKEN}


def test_a_role_config_file(fx):
    fx.write(fx.codex / "roles" / "r.toml", 'name = "renamed"\ndescription = "from file"\n')
    fx.config('[agents.declared]\nconfig_file = "./roles/r.toml"\n')
    item = fx.item(Kind.SUBAGENT, "renamed")
    assert item.status is Status.ENABLED
    assert item.description == "from file"


def test_a_file_a_role_declares_isnt_listed_twice(fx):
    fx.write(fx.codex / "agents" / "r.toml", 'name = "x"\ndescription = "d"\n')
    fx.config('[agents.x]\nconfig_file = "./agents/r.toml"\n')
    assert fx.statuses(Kind.SUBAGENT) == {"x": Status.ENABLED}


def test_a_missing_role_config_file_is_broken(fx):
    fx.config('[agents.gone]\nconfig_file = "./nope.toml"\n')
    assert fx.statuses(Kind.SUBAGENT) == {"gone": Status.BROKEN}


def test_turning_agents_off_uses_the_effective_setting(fx):
    fx.write(fx.codex / "agents" / "a.toml", f'name = "a"\n{GOOD}')
    fx.config(fx.trust)
    fx.project_config("[agents]\nenabled = false\n")
    assert fx.statuses(Kind.SUBAGENT) == {"a": Status.DISABLED}
    fx.config(fx.trust + "[features]\nmulti_agent_v2 = true\n")
    assert fx.statuses(Kind.SUBAGENT) == {"a": Status.ENABLED}


# Detection


@pytest.mark.parametrize("location", ["home", "project"])
def test_agents_skills_alone_count_as_configured(fx, location):
    shutil.rmtree(fx.codex)
    root = fx.home if location == "home" else fx.project
    write_skill(root / ".agents" / "skills" / "s")
    report = fx.scan()
    assert report.config_path == root / ".agents" / "skills"
