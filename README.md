# agt-view

A read-only CLI that shows what each coding agent on your machine can use from the current directory.

**Status:** early development. It currently lists **skills**, **MCP servers**, **plugins**, **hooks**, **subagents**, and **commands** for **Claude Code**, **Codex**, **OpenCode**, **Pi**, and **Antigravity CLI**. More agents are planned; see the [product requirements](docs/PRD.md). [Agent discovery rules](docs/agents/README.md) describes how each agent finds these.

## Usage

Run it from your project root to include project-level configuration. No install is needed with [uv](https://docs.astral.sh/uv/):

```sh
uvx agt-view                            # list everything for active agents
uvx agt-view --all                      # also show agents not both installed and configured
uvx agt-view -a codex -k mcp            # filter by agent and kind
uvx agt-view -n my-skill --detail       # filter by name, and add paths and other details
uvx agt-view "browser test" lint        # search all fields: (browser and test) or lint
```

`agt-view` only reads files. It never changes your configuration or runs your agents.

Output is plain text when piped, or with `--no-color` or `NO_COLOR`.

Problems are shown with the items they affect: why an item is `broken`, and when a same-named item is used instead (`shadowed`). Search for them like anything else, as in `uvx agt-view shadowed`.

## Development

```sh
uv run agt-view   # run from source
uv run pytest     # run the tests

# Also compare results with real agents, in a temporary HOME
AGT_VIEW_CLAUDE=/path/to/claude AGT_VIEW_CODEX=/path/to/codex \
AGT_VIEW_OPENCODE=/path/to/opencode AGT_VIEW_PI=/path/to/pi AGT_VIEW_AGY=/path/to/agy \
uv run pytest
```
