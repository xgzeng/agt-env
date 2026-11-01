import os
import subprocess
import sys
from pathlib import Path

import pytest

SCOPE_WORDS = {"P": "project", "U": "user", "S": "system"}
ENTRY_POINT = "from agt_view.cli import main; raise SystemExit(main())"


def write_skill(directory: Path, frontmatter: str = "description: does a thing") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(f"---\n{frontmatter}\n---\nBody text\n")
    return directory


def _managed_path(mac_name: str, linux_name: str) -> str:
    if sys.platform == "darwin":
        return f"Library/Application Support/{mac_name}"
    return f"etc/{linux_name}"


def _spawn_env(machine, extra):
    env = {"HOME": str(machine.home), "PATH": str(machine.bin), "TERM": "xterm", **extra}
    if "PYTHONPATH" in os.environ:
        env["PYTHONPATH"] = os.environ["PYTHONPATH"]
    return env


def run_on_terminal(machine, *args, code=ENTRY_POINT, env=None):
    """Run in a new process on a real terminal, and return what it printed."""
    pty = pytest.importorskip("pty")
    leader, follower = pty.openpty()
    process = subprocess.Popen(
        [sys.executable, "-c", code, *args],
        cwd=machine.project,
        env=_spawn_env(machine, {"PYTHONUTF8": "1", **(env or {})}),
        stdin=subprocess.DEVNULL,
        stdout=follower,
        stderr=subprocess.DEVNULL,
    )
    os.close(follower)
    chunks = []
    while True:
        try:
            chunk = os.read(leader, 65536)
        except OSError:  # Linux reports the closed terminal this way.
            break
        if not chunk:
            break
        chunks.append(chunk)
    os.close(leader)
    assert process.wait() == 0
    return b"".join(chunks).decode().replace("\r\n", "\n")


def run_piped(machine, *args, code=ENTRY_POINT, env=None):
    """Run in a new process with its output piped, and return what it printed."""
    return subprocess.run(
        [sys.executable, "-c", code, *args],
        cwd=machine.project,
        env=_spawn_env(machine, env or {}),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def assert_broken_items_say_why(report):
    from agt_view.model import Status

    for item in report.items:
        if item.status is Status.BROKEN:
            assert item.findings and item.findings[0].text, item


@pytest.fixture(autouse=True)
def broken_items_say_why(monkeypatch):
    """Every scan in every test checks that a broken item has a reason."""
    from agt_view.adapters import ADAPTERS

    for adapter in ADAPTERS:
        original = type(adapter).scan

        def scan(self, context, original=original):
            report = original(self, context)
            assert_broken_items_say_why(report)
            return report

        monkeypatch.setattr(type(adapter), "scan", scan)


@pytest.fixture
def machine(tmp_path, monkeypatch):
    """An isolated machine: empty HOME, empty project, nothing on PATH."""

    class Machine:
        home = tmp_path / "home"
        project = tmp_path / "project"
        bin = tmp_path / "bin"
        system = tmp_path / "system"

        @property
        def claude_managed(self) -> Path:
            return self.system / _managed_path("ClaudeCode", "claude-code")

        @property
        def opencode_managed(self) -> Path:
            return self.system / _managed_path("opencode", "opencode")

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
