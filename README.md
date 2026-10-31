# agt-view

A read-only CLI that shows what each coding agent on your machine can use from the current directory.

**Status:** early development. It currently lists **skills**, **MCP servers**, and **extensions** for **Claude Code**, **Codex**, **OpenCode**, **Pi**, and **Antigravity CLI**. More agents are planned; see the [product requirements](docs/PRD.md). [Agent discovery rules](docs/agents/README.md) describes how each agent finds these.

## Usage

Run it from your project root to include project-level configuration. No install is needed with [uv](https://docs.astral.sh/uv/):

```sh
uvx agt-view                            # list everything for active agents
uvx agt-view --all                      # also show agents not both installed and configured
uvx agt-view codex --kind mcp           # filter by agent and kind
uvx agt-view show my-skill              # all details of an item, across every agent
```

`agt-view` only reads files. It never changes your configuration or runs your agents.

## Development

```sh
uv run agt-view   # run from source
uv run pytest     # run the tests

# Also compare results with real agents, in a temporary HOME
AGT_VIEW_CLAUDE=/path/to/claude AGT_VIEW_CODEX=/path/to/codex \
AGT_VIEW_OPENCODE=/path/to/opencode AGT_VIEW_PI=/path/to/pi AGT_VIEW_AGY=/path/to/agy \
uv run pytest
```
