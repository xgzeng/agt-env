"""Pi rules from docs/agents/pi.md, without the real agent."""

import json
from pathlib import Path

import pytest
from conftest import write_skill

from agt_env.adapters.base import Context
from agt_env.adapters.pi import Pi, _git_location
from agt_env.model import Kind, Scope, Status

SERVER = {"command": "/bin/true"}


class Fixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.home = root / "home"
        self.project = root / "project"
        self.agent = self.home / ".pi" / "agent"
        self.agent.mkdir(parents=True)
        self.project.mkdir()

    def write(self, path: Path, content: object) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content if isinstance(content, str) else json.dumps(content))
        return path

    def trust(self, **settings) -> None:
        self.write(self.agent / "settings.json", {"defaultProjectTrust": "always", **settings})

    def scan(self):
        context = Context(
            home=self.home, cwd=self.project, env={"PATH": ""}, system_root=self.root / "system"
        )
        return Pi().scan(context)

    def rows(self, kind):
        return [item for item in self.scan().items if item.kind is kind]

    def statuses(self, kind):
        return {item.name: item.status for item in self.rows(kind)}


@pytest.fixture
def fx(tmp_path):
    return Fixture(tmp_path)


def package_with_skill(fx, pi):
    root = fx.agent / "pk"
    write_skill(root / "skills" / "s", "description: d")
    fx.write(root / "package.json", {"pi": pi})
    fx.write(fx.agent / "settings.json", {"packages": ["./pk"]})


@pytest.mark.parametrize("pi", [{}, {"changelogPath": "x"}, {"gallery": {"image": "x"}}])
def test_a_manifest_with_only_metadata_keeps_the_conventional_directories(fx, pi):
    package_with_skill(fx, pi)
    assert set(fx.statuses(Kind.SKILL)) == {"s"}


@pytest.mark.parametrize("pi", [{"prompts": []}, {"extensions": []}, {"themes": []}])
def test_a_manifest_with_another_resource_replaces_them(fx, pi):
    package_with_skill(fx, pi)
    assert not fx.statuses(Kind.SKILL)


@pytest.mark.parametrize(
    ("project_entry", "user_status"),
    [
        ("npm:tool@2", Status.SHADOWED),
        ({"source": "npm:tool"}, Status.SHADOWED),
        ({"source": "npm:tool", "autoload": False}, Status.ENABLED),
    ],
)
def test_a_project_package_replaces_the_users(fx, project_entry, user_status):
    fx.trust(packages=["npm:tool@1"])
    fx.write(fx.project / ".pi" / "settings.json", {"packages": [project_entry]})
    rows = {item.scope: item for item in fx.rows(Kind.PLUGIN)}
    assert rows[Scope.USER].status is user_status
    assert rows[Scope.PROJECT].status is Status.ENABLED


def test_an_untrusted_project_doesnt_replace_a_package(fx):
    fx.write(fx.agent / "settings.json", {"packages": ["npm:tool"]})
    fx.write(fx.project / ".pi" / "settings.json", {"packages": ["npm:tool"]})
    rows = {item.scope: item.status for item in fx.rows(Kind.PLUGIN)}
    assert rows == {Scope.USER: Status.ENABLED, Scope.PROJECT: Status.PENDING}


def test_git_packages_match_however_written(fx):
    fx.trust(packages=["ssh://git@github.com/acme/tools.git"])
    fx.write(fx.project / ".pi" / "settings.json", {"packages": ["git:github.com/acme/tools@v2"]})
    rows = {item.scope: item.status for item in fx.rows(Kind.PLUGIN)}
    assert rows[Scope.USER] is Status.SHADOWED


@pytest.mark.parametrize(
    ("source", "location"),
    [
        ("git:github.com/a/b", "github.com/a/b"),
        ("git:github.com/a/b.git", "github.com/a/b"),
        ("git:github.com/a/b@v1", "github.com/a/b"),
        ("git:git@github.com:a/b", "github.com/a/b"),
        ("git:git@github.com:a/b.git", "github.com/a/b"),
        ("git://github.com/a/b", "github.com/a/b"),
        ("https://github.com/a/b", "github.com/a/b"),
        ("https://user:token@github.com/a/b", "github.com/a/b"),
        ("ssh://git@github.com/a/b.git", "github.com/a/b"),
        ("ssh://git@github.com:22/a/b", "github.com/a/b"),
        ("git:host.example/g/s/t", "host.example/g/s/t"),
        ("git@github.com:a/b", None),
        ("./local", None),
        ("npm:x", None),
    ],
)
def test_git_locations(source, location):
    assert _git_location(source) == location


def test_a_standalone_skill_takes_its_directorys_name(fx):
    fx.write(fx.agent / "skills" / "helper.md", "---\ndescription: d\n---\nb")
    fx.write(fx.agent / "skills" / "named.md", "---\nname: n\ndescription: d\n---\nb")
    assert set(fx.statuses(Kind.SKILL)) == {"skills", "n"}


def test_standalone_skills_without_a_name_collide(fx):
    fx.write(fx.agent / "skills" / "a.md", "---\ndescription: d\n---\nb")
    fx.write(fx.agent / "skills" / "b.md", "---\ndescription: d\n---\nb")
    assert sorted(item.status.value for item in fx.rows(Kind.SKILL)) == ["enabled", "shadowed"]


def test_markdown_below_an_agents_root(fx):
    root = fx.home / ".agents" / "skills"
    fx.write(root / "root.md", "---\nname: at-root\ndescription: d\n---\nb")
    fx.write(root / "g" / "tool.md", "---\nname: tool\ndescription: d\n---\nb")
    fx.write(root / "g" / "deep" / "d.md", "---\nname: deep\ndescription: d\n---\nb")
    fx.write(root / "g" / "readme.md", "plain text")
    fx.write(root / "s" / "inner.md", "---\nname: inner\ndescription: d\n---\nb")
    write_skill(root / "s", "name: real\ndescription: d")
    fx.write(root / ".hidden" / "h.md", "---\nname: hidden\ndescription: d\n---\nb")
    fx.write(root / "node_modules" / "n.md", "---\nname: dep\ndescription: d\n---\nb")
    assert set(fx.statuses(Kind.SKILL)) == {"tool", "deep", "real"}


def test_nested_markdown_only_counts_in_agents_roots(fx):
    fx.write(fx.agent / "skills" / "g" / "nested.md", "---\nname: n\ndescription: d\n---\nb")
    assert not fx.statuses(Kind.SKILL)


def test_a_linked_resource_keeps_the_projects_row(fx):
    fx.trust()
    write_skill(fx.agent / "skills" / "s", "description: d")
    (fx.project / ".pi" / "skills").mkdir(parents=True)
    (fx.project / ".pi" / "skills" / "s").symlink_to(fx.agent / "skills" / "s")
    fx.write(fx.agent / "prompts" / "p.md", "t")
    (fx.project / ".pi" / "prompts").mkdir(parents=True)
    (fx.project / ".pi" / "prompts" / "p.md").symlink_to(fx.agent / "prompts" / "p.md")
    fx.write(fx.agent / "extensions" / "e.ts", "x")
    (fx.project / ".pi" / "extensions").mkdir(parents=True)
    (fx.project / ".pi" / "extensions" / "e.ts").symlink_to(fx.agent / "extensions" / "e.ts")
    for kind in (Kind.SKILL, Kind.COMMAND, Kind.PLUGIN):
        assert [item.scope for item in fx.rows(kind)] == [Scope.PROJECT]


def test_both_transports_mean_http(fx):
    fx.write(
        fx.agent / "mcp.json",
        {"mcpServers": {"both": {"command": "/bin/true", "url": "https://x.test"}}},
    )
    assert fx.rows(Kind.MCP)[0].details["transport"] == "streamable_http"


def test_a_typed_server_uses_only_its_own_transport(fx):
    fx.write(
        fx.agent / "mcp.json",
        {
            "mcpServers": {
                "both": {"type": "stdio", "command": "/bin/true", "url": "https://x.test"}
            }
        },
    )
    assert fx.rows(Kind.MCP)[0].details.get("transport") != "streamable_http"


@pytest.mark.parametrize(
    ("name", "entry", "reason"),
    [
        ("s", {"type": "sse", "url": "https://x.test"}, "SSE"),
        ("s", {"url": "ftp://x.test"}, "http or https"),
        ("s", {"url": "https://x.test", "headers": {"a": 1}}, "headers"),
        ("s", {"command": "x", "args": [1]}, "args"),
        ("s", {"command": "x", "env": {"A": 1}}, "env"),
        ("s", {"command": "x", "enabled": "yes"}, "enabled"),
        ("s", {"args": []}, "command or a url"),
        ("bad name", SERVER, "invalid server name"),
        ("a.b", SERVER, "invalid server name"),
        ("a/b", SERVER, "invalid server name"),
    ],
)
def test_refused_servers_say_why(fx, name, entry, reason):
    fx.write(fx.agent / "mcp.json", {"mcpServers": {name: entry}})
    item = fx.rows(Kind.MCP)[0]
    assert item.status is Status.BROKEN
    assert reason in item.findings[0].text


def test_a_shared_namespace_is_refused(fx):
    fx.write(fx.agent / "mcp.json", {"mcpServers": {"foo-bar": SERVER, "foo_bar": SERVER}})
    statuses = fx.statuses(Kind.MCP)
    assert statuses == {"foo-bar": Status.ENABLED, "foo_bar": Status.BROKEN}
    assert "foo-bar" in fx.rows(Kind.MCP)[1].findings[0].text


def test_a_project_server_can_clash_with_a_users(fx):
    fx.trust()
    fx.write(fx.agent / "mcp.json", {"mcpServers": {"foo-bar": SERVER}})
    fx.write(fx.project / ".pi" / "mcp.json", {"mcpServers": {"foo_bar": SERVER}})
    assert fx.statuses(Kind.MCP)["foo_bar"] is Status.BROKEN


def test_the_same_name_in_a_project_isnt_a_clash(fx):
    fx.trust()
    fx.write(fx.agent / "mcp.json", {"mcpServers": {"s": SERVER}})
    fx.write(fx.project / ".pi" / "mcp.json", {"mcpServers": {"s": SERVER}})
    rows = {item.scope: item.status for item in fx.rows(Kind.MCP)}
    assert rows == {Scope.USER: Status.SHADOWED, Scope.PROJECT: Status.ENABLED}


def test_auth_is_only_for_the_users_file(fx):
    fx.trust()
    server = {"url": "https://x.test", "auth": {"provider": "p"}}
    fx.write(fx.agent / "mcp.json", {"mcpServers": {"mine": server}})
    fx.write(fx.project / ".pi" / "mcp.json", {"mcpServers": {"theirs": server}})
    statuses = fx.statuses(Kind.MCP)
    assert statuses["mine"] is Status.ENABLED
    assert statuses["theirs"] is Status.BROKEN


def test_an_override_with_other_keys_is_refused(fx):
    fx.trust()
    fx.write(fx.agent / "mcp.json", {"mcpServers": {"s": SERVER}})
    fx.write(fx.project / ".pi" / "mcp.json", {"mcpServers": {"s": {"enabled": False, "x": 1}}})
    rows = {item.scope: item.status for item in fx.rows(Kind.MCP)}
    assert rows[Scope.PROJECT] is Status.BROKEN
    assert rows[Scope.USER] is Status.ENABLED


@pytest.mark.parametrize(
    ("user", "project", "off"),
    [
        (["-builtin:mcp"], None, True),
        (["!builtin:mcp"], None, True),
        (["!builtin:*"], None, True),
        (["!*"], None, True),
        (["-builtin:other"], None, False),
        (["!builtin:mcp", "+builtin:mcp"], None, False),
        (["+builtin:mcp", "-builtin:mcp"], None, True),
        (["-builtin:mcp"], ["+builtin:mcp"], False),
        ([], ["-builtin:mcp"], True),
        ([], ["+builtin:mcp", "!builtin:*"], True),
    ],
)
def test_turning_built_in_mcp_off(fx, user, project, off):
    fx.trust(extensions=user)
    fx.write(fx.agent / "mcp.json", {"mcpServers": {"s": SERVER}})
    if project:
        fx.write(fx.project / ".pi" / "settings.json", {"extensions": project})
    assert fx.rows(Kind.MCP)[0].status is (Status.DISABLED if off else Status.ENABLED)


def test_an_untrusted_project_cant_change_built_in_mcp(fx):
    fx.write(fx.agent / "settings.json", {"extensions": ["-builtin:mcp"]})
    fx.write(fx.agent / "mcp.json", {"mcpServers": {"s": SERVER}})
    fx.write(fx.project / ".pi" / "settings.json", {"extensions": ["+builtin:mcp"]})
    assert fx.rows(Kind.MCP)[0].status is Status.DISABLED


def test_each_declared_extension_is_a_row(fx):
    root = fx.agent / "extensions" / "multi"
    fx.write(root / "package.json", {"pi": {"extensions": ["./a.ts", "./b.ts", "./gone.ts"]}})
    fx.write(root / "a.ts", "x")
    fx.write(root / "b.ts", "x")
    fx.write(root / "index.ts", "x")
    assert set(fx.statuses(Kind.PLUGIN)) == {"multi/a", "multi/b"}


def test_a_directory_whose_entries_are_missing_uses_its_index(fx):
    root = fx.agent / "extensions" / "d"
    fx.write(root / "package.json", {"pi": {"extensions": ["./gone.ts"]}})
    assert not fx.statuses(Kind.PLUGIN)
    fx.write(root / "index.ts", "x")
    assert set(fx.statuses(Kind.PLUGIN)) == {"d"}


def test_same_named_extensions_arent_shadowed(fx):
    fx.trust()
    fx.write(fx.agent / "extensions" / "same.ts", "x")
    fx.write(fx.project / ".pi" / "extensions" / "same.ts", "x")
    assert {item.status for item in fx.rows(Kind.PLUGIN)} == {Status.ENABLED}


def test_a_package_specifier_in_extensions_isnt_an_extension(fx):
    fx.write(fx.agent / "settings.json", {"extensions": ["npm:x", "git:github.com/a/b"]})
    assert not fx.rows(Kind.PLUGIN)


def test_long_first_lines_are_cut(fx):
    fx.write(fx.agent / "prompts" / "long.md", "x" * 80)
    fx.write(fx.agent / "prompts" / "exact.md", "y" * 60)
    fx.write(fx.agent / "prompts" / "own.md", f"---\ndescription: {'z' * 80}\n---\nt")
    rows = {item.name: item.description for item in fx.rows(Kind.COMMAND)}
    assert rows["long"] == "x" * 60 + "..."
    assert rows["exact"] == "y" * 60
    assert rows["own"] == "z" * 80


def test_a_tilde_uses_the_scanned_home(fx):
    write_skill(fx.home / "elsewhere" / "s", "description: d")
    fx.write(fx.home / "plug" / "package.json", {"name": "p"})
    write_skill(fx.home / "plug" / "skills" / "from-pkg", "description: d")
    fx.write(fx.agent / "settings.json", {"skills": ["~/elsewhere"], "packages": ["~/plug"]})
    assert set(fx.statuses(Kind.SKILL)) == {"s", "from-pkg"}


@pytest.mark.parametrize("location", ["home", "project"])
def test_agents_skills_alone_count_as_configured(tmp_path, location):
    home, project = tmp_path / "home", tmp_path / "project"
    home.mkdir()
    project.mkdir()
    root = home if location == "home" else project
    write_skill(root / ".agents" / "skills" / "s", "description: d")
    context = Context(home=home, cwd=project, env={"PATH": ""}, system_root=tmp_path / "system")
    assert Pi().scan(context).config_path == root / ".agents" / "skills"


def test_corrupt_user_settings_leave_things_unknown(fx):
    fx.write(fx.agent / "settings.json", "{bad")
    write_skill(fx.agent / "skills" / "mine", "description: d")
    write_skill(fx.project / ".pi" / "skills" / "theirs", "description: d")
    report = fx.scan()
    assert report.warnings
    assert {item.status for item in report.items} == {Status.UNKNOWN}
