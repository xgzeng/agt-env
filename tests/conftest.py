from pathlib import Path

import pytest


def write_skill(directory: Path, frontmatter: str = "description: does a thing") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(f"---\n{frontmatter}\n---\nBody text\n")
    return directory


@pytest.fixture
def machine(tmp_path, monkeypatch):
    """An isolated machine: empty HOME, empty project, nothing on PATH."""

    class Machine:
        home = tmp_path / "home"
        project = tmp_path / "project"
        bin = tmp_path / "bin"
        system = tmp_path / "system"

        def install(self, *binaries: str) -> None:
            for binary in binaries:
                path = self.bin / binary
                # Executing this would fail the test: it leaves a marker behind.
                path.write_text(f"#!/bin/sh\ntouch {tmp_path}/executed\n")
                path.chmod(0o755)

        @property
        def executed(self) -> bool:
            return (tmp_path / "executed").exists()

    for directory in (Machine.home, Machine.project, Machine.bin, Machine.system):
        directory.mkdir()
    for variable in (
        "CLAUDE_CONFIG_DIR",
        "CODEX_HOME",
        "NO_COLOR",
        "OPENCODE_CONFIG",
        "OPENCODE_CONFIG_DIR",
        "PI_CODING_AGENT_DIR",
        "XDG_CONFIG_HOME",
    ):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv("HOME", str(Machine.home))
    monkeypatch.setenv("PATH", str(Machine.bin))
    monkeypatch.chdir(Machine.project)

    # Keep machine-wide locations inside the fixture too.
    from agt_view.adapters import base

    original = base.Context.__init__

    def init(self, *args, **kwargs):
        kwargs["system_root"] = Machine.system
        original(self, *args, **kwargs)

    monkeypatch.setattr(base.Context, "__init__", init)
    return Machine()
