"""Antigravity CLI rules from docs/agents/antigravity.md, without the real agent."""

import json
from pathlib import Path

import pytest

from agt_env.adapters.antigravity import Antigravity
from agt_env.adapters.base import Context
from agt_env.model import Kind, Scope, Status


class Fixture:
    def __init__(self, root: Path, *, trusted: bool = True) -> None:
        self.root = root
        self.home = root / "home"
        self.project = root / "project"
        self.gemini = self.home / ".gemini"
        self.config = self.gemini / "config"
        self.project.mkdir(parents=True)
        self.config.mkdir(parents=True)
        if trusted:
            self.write(
                self.gemini / "antigravity-cli" / "settings.json",
                {"trustedWorkspaces": [str(self.project)]},
            )

    def write(self, path: Path, content: object) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content if isinstance(content, str) else json.dumps(content))
        return path

    def skill(self, directory: Path, name: str | None = "NAME", description: str = "d") -> None:
        name = directory.name if name == "NAME" else name
        front = (f"name: {name}\n" if name else "") + f"description: {description}\n"
        self.write(directory / "SKILL.md", f"---\n{front}---\nbody\n")

    def agent(self, path: Path, name: str, body: str = "b") -> None:
        self.write(path, f"---\nname: {name}\ndescription: d\n---\n{body}\n")

    def scan(self):
        context = Context(
            home=self.home, cwd=self.project, env={"PATH": ""}, system_root=self.root / "system"
        )
        return Antigravity().scan(context)

    def rows(self, kind):
        return [item for item in self.scan().items if item.kind is kind]

    def winner(self, kind):
        return [item for item in self.rows(kind) if item.status is Status.ENABLED]


@pytest.fixture
def fx(tmp_path):
    return Fixture(tmp_path)


@pytest.mark.parametrize(
    ("places", "expected"),
    [
        ([".agents/skills", "~/config/skills"], ".agents/skills"),
        (["~/antigravity-cli/skills", "~/config/skills", "~/skills"], "~/antigravity-cli/skills"),
        (["~/config/skills", "~/skills"], "~/config/skills"),
        ([".agents/skills", ".agent/skills"], ".agent/skills"),
        ([".agents/skills", "_agents/skills"], ".agents/skills"),
        (["_agents/skills", "_agent/skills"], "_agent/skills"),
    ],
)
def test_skill_precedence(fx, places, expected):
    def where(place):
        return fx.gemini / place[2:] if place.startswith("~/") else fx.project / place

    for place in places:
        fx.skill(where(place) / "same")
    [winner] = fx.winner(Kind.SKILL)
    assert winner.source_path == where(expected) / "same"


def test_an_untrusted_workspace_doesnt_shadow(tmp_path):
    fx = Fixture(tmp_path, trusted=False)
    fx.skill(fx.project / ".agents" / "skills" / "same")
    fx.skill(fx.config / "skills" / "same")
    assert {item.scope: item.status for item in fx.rows(Kind.SKILL)} == {
        Scope.PROJECT: Status.PENDING,
        Scope.USER: Status.ENABLED,
    }


def test_unnamed_skills_collide(fx):
    fx.skill(fx.config / "skills" / "u1", name=None)
    fx.skill(fx.config / "skills" / "u2", name=None)
    rows = fx.rows(Kind.SKILL)
    assert [item.name for item in rows] == ["SKILL", "SKILL"]
    assert [item.status for item in rows] == [Status.ENABLED, Status.SHADOWED]


def test_a_linked_skill_keeps_the_workspace_row(fx):
    fx.skill(fx.config / "skills" / "s")
    (fx.project / ".agents" / "skills").mkdir(parents=True)
    (fx.project / ".agents" / "skills" / "s").symlink_to(fx.config / "skills" / "s")
    assert [item.scope for item in fx.rows(Kind.SKILL)] == [Scope.PROJECT]


@pytest.mark.parametrize("workspace", ["_agents", "_agent", ".agent"])
def test_every_workspace_directory_is_read(fx, workspace):
    root = fx.project / workspace
    fx.skill(root / "skills" / "sk")
    fx.agent(root / "agents" / "ag.md", "ag")
    fx.write(root / "hooks.json", {"h": {"Stop": [{"command": "/bin/h"}]}})
    fx.write(root / "plugins" / "pl" / "plugin.json", {})
    fx.write(root / "mcp_config.json", {"mcpServers": {"m": {"command": "/bin/true"}}})
    for kind in (Kind.SKILL, Kind.SUBAGENT, Kind.HOOK, Kind.PLUGIN, Kind.MCP):
        assert [item.scope for item in fx.rows(kind)] == [Scope.PROJECT]


def declared(fx, config, where=None):
    shared = fx.root / "shared"
    for name in ("a", "b"):
        fx.skill(shared / name)
    fx.skill(shared / "cat" / "nested")
    fx.write((where or fx.project / ".agents") / "skills.json", config(shared))


@pytest.mark.parametrize(
    ("config", "names"),
    [
        (lambda shared: {"entries": [{"path": str(shared)}]}, {"a", "b"}),
        (lambda shared: {"entries": [{"path": str(shared), "exclude": ["b"]}]}, {"a"}),
        (lambda shared: {"entries": [{"path": str(shared), "exclude": ["b.*"]}]}, {"a", "b"}),
        (lambda shared: {"entries": [{"path": str(shared)}], "exclude": ["a"]}, {"b"}),
        (
            lambda shared: {"entries": [{"path": str(shared), "include_only": ["cat/nested"]}]},
            {"nested"},
        ),
        (lambda shared: {"entries": [{"path": "../shared"}]}, {"a", "b"}),
        (lambda shared: {"entries": [{"path": str(shared / "a")}]}, {"a"}),
        (lambda shared: {"entries": ["x", {"path": str(shared)}]}, set()),
    ],
)
def test_declared_skills(fx, config, names):
    declared(fx, config)
    assert {item.name for item in fx.rows(Kind.SKILL)} == names


def test_inherited_declarations(fx):
    fx.skill(fx.root / "shared" / "a")
    fx.skill(fx.root / "shared" / "b")
    fx.write(fx.root / "inherit.json", {"entries": [{"path": str(fx.root / "shared")}]})
    fx.write(
        fx.project / ".agents" / "skills.json",
        {"inherits": [{"path": str(fx.root / "inherit.json"), "exclude": ["b"]}]},
    )
    assert {item.name for item in fx.rows(Kind.SKILL)} == {"a"}


@pytest.mark.parametrize(
    ("declared_in", "plain", "declared_wins"),
    [
        (".agents", ".agents/skills", True),
        ("~/config", "~/config/skills", True),
        ("~/config", "~/antigravity-cli/skills", True),
        ("~/config", ".agents/skills", False),
    ],
)
def test_declared_skill_precedence(fx, declared_in, plain, declared_wins):
    def where(place):
        return fx.gemini / place[2:] if place.startswith("~/") else fx.project / place

    fx.skill(fx.root / "declared" / "same")
    fx.write(where(declared_in) / "skills.json", {"entries": [{"path": str(fx.root / "declared")}]})
    fx.skill(where(plain) / "same")
    [winner] = fx.winner(Kind.SKILL)
    assert (winner.source_path == fx.root / "declared" / "same") is declared_wins


def test_a_corrupt_declaration_warns_and_adds_nothing(fx):
    fx.write(fx.project / ".agents" / "skills.json", "{bad")
    fx.skill(fx.project / ".agents" / "skills" / "plain")
    report = fx.scan()
    assert report.warnings
    assert [item.name for item in report.items if item.kind is Kind.SKILL] == ["plain"]


def test_declared_plugins_and_agents(fx):
    fx.write(fx.root / "pl" / "jp" / "plugin.json", {})
    fx.skill(fx.root / "pl" / "jp" / "skills" / "pjs")
    fx.write(fx.project / ".agents" / "plugins.json", {"entries": [{"path": str(fx.root / "pl")}]})
    fx.agent(fx.root / "ag" / "ja.md", "ja")
    fx.write(fx.config / "agents.json", {"entries": [{"path": str(fx.root / "ag")}]})
    assert [item.name for item in fx.rows(Kind.PLUGIN)] == ["jp"]
    assert [item.name for item in fx.rows(Kind.SKILL)] == ["pjs"]
    assert [item.name for item in fx.rows(Kind.SUBAGENT)] == ["ja"]


def test_a_plugins_own_agents_file(fx):
    root = fx.config / "plugins" / "p"
    fx.write(root / "plugin.json", {})
    fx.agent(root / "elsewhere" / "pja.md", "pja")
    fx.write(root / "agents.json", {"entries": [{"path": "elsewhere"}]})
    assert [item.name for item in fx.rows(Kind.SUBAGENT)] == ["pja"]


def test_flat_and_grouped_hooks(fx):
    fx.write(
        fx.config / "hooks.json",
        {
            "flat": {"Stop": [{"command": "/bin/a"}]},
            "grouped": {"PreToolUse": [{"matcher": "x", "hooks": [{"command": "/bin/b"}]}]},
            "wrapped": {"Stop": [{"hooks": [{"command": "/bin/c"}]}]},
            "flat-on-grouped": {"PreToolUse": [{"command": "/bin/d"}]},
            "unknown": {"Nope": [{"command": "/bin/e"}]},
            "empty": {"Stop": []},
        },
    )
    statuses = {item.name: item.status for item in fx.rows(Kind.HOOK)}
    assert statuses == {
        "flat": Status.ENABLED,
        "grouped": Status.ENABLED,
        "wrapped": Status.BROKEN,
        "flat-on-grouped": Status.BROKEN,
        "unknown": Status.BROKEN,
        "empty": Status.BROKEN,
    }


@pytest.mark.parametrize(
    "bad",
    [{"Stop": [{"type": "zzz"}]}, {"Stop": {"command": "x"}}, "x", {"Stop": [3]}],
)
def test_a_bad_entry_costs_the_whole_hooks_file(fx, bad):
    fx.write(fx.config / "hooks.json", {"bad": bad, "ok": {"Stop": [{"command": "x"}]}})
    report = fx.scan()
    assert not [item for item in report.items if item.kind is Kind.HOOK]
    assert any("hooks.json" in warning for warning in report.warnings)


@pytest.mark.parametrize(
    ("places", "expected"),
    [
        ([".agents", "~/config"], "~/config"),
        ([".agent", ".agents"], ".agents"),
        (["_agent", "_agents"], "_agents"),
        ([".agents", "_agents"], "_agents"),
        ([".agents", "plugin"], "plugin"),
        (["~/config", "plugin"], "plugin"),
    ],
)
def test_the_last_hook_of_a_name_wins(fx, places, expected):
    def where(place):
        if place == "plugin":
            return fx.config / "plugins" / "p"
        return fx.gemini / place[2:] if place.startswith("~/") else fx.project / place

    for place in places:
        if place == "plugin":
            fx.write(where(place) / "plugin.json", {})
        fx.write(where(place) / "hooks.json", {"h": {"Stop": [{"command": "/bin/x"}]}})
    [winner] = fx.winner(Kind.HOOK)
    assert winner.source_path == where(expected) / "hooks.json"


def test_plugin_servers(fx):
    root = fx.config / "plugins" / "p"
    fx.write(root / "plugin.json", {})
    fx.write(root / "mcp_config.json", {"mcpServers": {"ps": {"command": "/bin/true"}, "bad": {}}})
    rows = {item.name: item for item in fx.rows(Kind.MCP)}
    assert rows["ps"].status is Status.ENABLED and rows["ps"].details["plugin"] == "p"
    assert rows["bad"].status is Status.BROKEN
    fx.write(fx.config / "config.json", {"plugins": {"p": {"enabled": False}}})
    assert not fx.rows(Kind.MCP)


@pytest.mark.parametrize(
    ("where", "listed"),
    [
        (".gemini-plugin/plugin.json", True),
        (".claude-plugin/plugin.json", False),
        ("gemini-extension.json", False),
    ],
)
def test_manifest_locations(fx, where, listed):
    fx.write(fx.config / "plugins" / "p" / where, {})
    assert bool(fx.rows(Kind.PLUGIN)) is listed


@pytest.mark.parametrize(
    ("config", "status"),
    [(None, Status.DISABLED), ({"plugins": {"p": {"enabled": True}}}, Status.ENABLED)],
)
def test_a_plugin_can_ship_turned_off(fx, config, status):
    fx.write(fx.config / "plugins" / "p" / "plugin.json", {"disabled": True})
    if config:
        fx.write(fx.config / "config.json", config)
    assert [item.status for item in fx.rows(Kind.PLUGIN)] == [status]


def test_a_url_beats_a_command(fx):
    fx.write(
        fx.config / "mcp_config.json",
        {"mcpServers": {"both": {"command": "/bin/true", "url": "http://127.0.0.1:9/m"}}},
    )
    assert fx.rows(Kind.MCP)[0].details["transport"] == "streamable_http"


@pytest.mark.parametrize(("listed", "trusted"), [("real", True), ("link", False)])
def test_trust_compares_the_real_path_with_what_is_listed(tmp_path, listed, trusted):
    fx = Fixture(tmp_path, trusted=False)
    link = tmp_path / "link"
    link.symlink_to(fx.project)
    fx.write(
        fx.gemini / "antigravity-cli" / "settings.json",
        {"trustedWorkspaces": [str(link if listed == "link" else fx.project)]},
    )
    fx.agent(fx.project / ".agents" / "agents" / "a.md", "a")
    for cwd in (fx.project, link):
        context = Context(home=fx.home, cwd=cwd, env={"PATH": ""}, system_root=tmp_path / "s")
        [agent] = [i for i in Antigravity().scan(context).items if i.kind is Kind.SUBAGENT]
        assert (agent.status is Status.ENABLED) is trusted


def test_workspace_agents_win(fx):
    fx.agent(fx.project / ".agents" / "agents" / "x.md", "same")
    fx.agent(fx.config / "agents" / "y.md", "same")
    [winner] = fx.winner(Kind.SUBAGENT)
    assert winner.scope is Scope.PROJECT
