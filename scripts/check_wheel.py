"""Check a built wheel from a clean environment, the way a new user would run it with uvx.

Usage: uv run --no-project scripts/check_wheel.py dist/*.whl
"""

import os
import pty
import subprocess
import sys
import tempfile
from pathlib import Path


def run_piped(command: list[str], cwd: Path, env: dict[str, str]) -> str:
    return subprocess.run(
        command, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, check=True
    ).stdout.decode()


def run_on_terminal(command: list[str], cwd: Path, env: dict[str, str]) -> str:
    leader, follower = pty.openpty()
    process = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=follower)
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
    if process.wait() != 0:
        raise SystemExit(f"exit code {process.returncode} on a terminal")
    return b"".join(chunks).decode()


def expect(output: str, *texts: str) -> None:
    for text in texts:
        if text not in output:
            raise SystemExit(f"expected {text!r} in the output:\n{output}")


def uv_directory(*command: str) -> str:
    return subprocess.run(
        ["uv", *command], stdout=subprocess.PIPE, text=True, check=True
    ).stdout.strip()


def main() -> None:
    wheel = Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory).resolve()
        home, project, binaries = root / "home", root / "project", root / "bin"
        marker = root / "executed"
        skill = home / ".claude" / "skills" / "smoke-skill"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("---\nname: smoke-skill\ndescription: Works\n---\n")
        project.mkdir()
        binaries.mkdir()
        claude = binaries / "claude"
        claude.write_text(f"#!/bin/sh\ntouch {marker}\n")
        claude.chmod(0o755)

        env = {key: value for key, value in os.environ.items() if key != "NO_COLOR"}
        env.update(
            HOME=str(home),
            PATH=f"{binaries}{os.pathsep}{os.environ['PATH']}",
            TERM="xterm",
            PYTHONUTF8="1",
            UV_NO_PROGRESS="1",
            # A new HOME must not make uv fetch Python again.
            UV_CACHE_DIR=uv_directory("cache", "dir"),
            UV_PYTHON_INSTALL_DIR=uv_directory("python", "dir"),
        )
        command = ["uvx", "--isolated", "--from", str(wheel), "agt-env"]

        expect(run_piped([*command, "--version"], project, env), "agt-env ")
        expect(run_piped(command, project, env), "claude-code", "U/skill", "smoke-skill", "enabled")
        expect(run_piped([*command, "--detail"], project, env), "Description:  Works")
        # The terminal output needs Rich, so this also checks that the wheel declares it.
        expect(run_on_terminal(command, project, env), "╭", "●", "smoke-skill", "Works")

        if marker.exists():
            raise SystemExit("agt-env ran an agent binary")
    print(f"{wheel.name} works")


if __name__ == "__main__":
    main()
