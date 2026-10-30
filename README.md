# agt-view

A read-only CLI that shows what each coding agent on your machine can use from the current directory.

**Status:** early development. It currently lists **skills**, **MCP servers**, and **extensions** for **Claude Code** and **Codex**. More agents are planned; see the [product requirements](docs/PRD.md).

## Usage

Run it from your project root to include project-level configuration. No install is needed with [uv](https://docs.astral.sh/uv/):

```sh
uvx agt-view                            # list everything for active agents
uvx agt-view --all                      # also show agents not both installed and configured
uvx agt-view --agent codex --kind mcp   # filter by agent and kind
```

`agt-view` only reads files. It never changes your configuration or runs your agents.

## Development

```sh
uv run agt-view   # run from source
uv run pytest     # run the tests

# Also compare results with real agents, in a temporary HOME
AGT_VIEW_CODEX=/path/to/codex AGT_VIEW_CLAUDE=/path/to/claude uv run pytest
```
