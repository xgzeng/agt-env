# AGENTS.md

This file provides guidance to coding agents when working with code in this repository. `CLAUDE.md` is a symlink to it.

`agt-view` is a read-only CLI that lists the skills, MCP servers, and plugins each coding agent can use from the current directory. Requirements are in `docs/PRD.md`.

## Commands

```sh
uv run agt-view                  # run from source
uv run pytest                    # all tests; real-agent comparisons are skipped
uv run pytest tests/test_cli.py::test_filters    # one test
uv run --isolated --python 3.11 --group dev pytest    # oldest supported Python
uvx ruff format                  # format; configured in pyproject.toml
uvx ruff check                   # lint
uvx --from actionlint-py actionlint .github/workflows/*.yml
uv build && uv run --no-project scripts/check_wheel.py dist/*.whl    # the built wheel, as a new user runs it
```

Real-agent comparison tests run only when given a binary, and always use a temporary `HOME`. Pass the real binary, because a version-manager shim may not work in a temporary `HOME` (with mise, use `mise which codex`):

```sh
AGT_VIEW_CLAUDE=... AGT_VIEW_CODEX=... AGT_VIEW_OPENCODE=... AGT_VIEW_PI=... AGT_VIEW_AGY=... uv run pytest
```

## Architecture

`cli.py` runs every adapter in `adapters.ADAPTERS` (registry order is display order), marks skills shared between agents by real path, and passes what `filters.py` selects to an output: `render_pretty.py` (Rich) on a color terminal, otherwise `render_plain.py` (plain text, whose exact form scripts can rely on). It hides agents that aren't both installed and configured, unless `--all` or `-a` is used.

Each adapter returns an `AgentReport`: install and config state, plus `Item`s (`model.py`). All agent-specific rules live in that adapter. Shared helpers are in `adapters/base.py` (reading config, building items), `skills.py` (scanning, with per-agent options for depth and hidden directories), `mcp.py` (safe server summaries), `frontmatter.py`, and `jsonc.py`.

Adapters are independent of each other. The same directory can be scanned by several adapters, because agents read each other's skill directories.

## Rules that span the code

- **Default commands only read files.** They never run agent binaries, start MCP servers, or load plugins. `shutil.which` is the only install check.
- **Secret values must never reach an `Item`.** MCP `details` come from `mcp.py`, which keeps env and header names only, drops arguments, and strips URL credentials and queries. Plugin and package names go through `without_credentials`. Credential stores are never opened, and `skills.py` won't follow a skill file's symlink to a non-Markdown file. `tests/test_safety.py` checks this, and that nothing is written, by auditing what a real run opens; add new credential locations there and to `docs/agents/README.md`.
- **Inspected text is untrusted.** Everything printed goes through `render_plain.clean()`, and reaches Rich only as a `Text`, because Rich parses plain strings as markup. Files are read with a size limit, and non-regular files aren't opened.
- **Status needs evidence.** Use `unknown` rather than guessing `enabled`, and `pending` when an item waits on user approval or trust. A config file that can't be read gives a warning and `unknown`, and must not affect other agents.
- **The current directory, or `--project`, is the project root.** Parent directories aren't searched, even for agents that search them.

## Changing agent rules

Agent docs are often incomplete or wrong, so check behavior against the real agent, in a temporary `HOME`. When rules change, update both the adapter and its `docs/agents/<agent>.md`, which is the single record of each rule and whether it's documented or tested. Adapter header comments only point to that file.

To add an agent, write the adapter, a line in `adapters/__init__.py`, a doc page, and tests. The priority order is in the PRD.

Tests must stay isolated: the `machine` fixture in `tests/conftest.py` provides a temporary `HOME`, an empty `PATH`, and a redirected `system_root`.

OpenCode's comparison tests start a background service, so they give it its own port and stop it afterwards. Don't stop a service running under a real `HOME`.

## Docs

- Keep `docs/PRD.md` independent of any one machine: no personal paths or installed tools, and no scope or priority decided by them. Use placeholder examples.
- Don't change the document version in the PRD unless asked.

## Releasing

Before tagging, run the real-agent comparison tests above, because CI doesn't have the agents.

The version is in both `pyproject.toml` and `src/agt_view/__init__.py`, and `uv.lock` must be refreshed with `uv lock`. The workflows only check the tag against `pyproject.toml`.

Pushing a tag `v<version>` on a commit in `main` runs `release.yml`, which creates the GitHub release and starts `pypi.yml`. A release created by a workflow can't trigger other workflows, so `release.yml` starts `pypi.yml` explicitly. `pypi.yml` uploads through PyPI trusted publishing, with environment `pypi`.
