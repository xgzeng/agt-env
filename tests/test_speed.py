"""The PRD's speed target: under 200 ms for 5 agents and 500 items, in the default listing."""

import json
import time

import pytest
from conftest import run_on_terminal, run_piped, write_skill

LIMIT_SECONDS = 0.2
ATTEMPTS = 5  # The best run, so that a busy machine doesn't fail the test.
PER_AGENT = 100
DESCRIPTION = "Does a thing, and says when to use it. " * 5


@pytest.fixture
def reference_fixture(machine):
    machine.install("claude", "codex", "opencode", "pi", "agy")
    home = machine.home
    # Each directory is read by one agent only, so that this makes exactly 500 items.
    servers = {f"server-{i}": {"command": "/bin/tool"} for i in range(PER_AGENT)}
    (home / ".claude").mkdir()
    (home / ".claude.json").write_text(json.dumps({"mcpServers": servers}))
    for directory in (
        ".codex",
        ".config/opencode",
        ".pi/agent",
        ".gemini/antigravity-cli",
    ):
        for i in range(PER_AGENT):
            name = f"skill-{i}"
            write_skill(
                home / directory / "skills" / name, f"name: {name}\ndescription: {DESCRIPTION}"
            )
    return machine


def best_time(run, *args):
    times = []
    for _ in range(ATTEMPTS):
        start = time.perf_counter()
        output = run(*args)
        times.append(time.perf_counter() - start)
    return min(times), output


def test_fixture_has_500_items(reference_fixture):
    output = run_piped(reference_fixture)
    assert sum(line.startswith("  U/") for line in output.splitlines()) == 500


@pytest.mark.parametrize("run", [run_piped, run_on_terminal], ids=["plain", "rich"])
def test_listing_is_fast_enough(reference_fixture, run):
    seconds, _ = best_time(run, reference_fixture)
    print(f"\n{run.__name__}: {seconds * 1000:.0f} ms")
    assert seconds < LIMIT_SECONDS, f"took {seconds * 1000:.0f} ms"
