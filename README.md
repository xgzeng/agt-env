# agent-view

A read-only CLI that shows what each coding agent on your machine can use from the current directory.

**Status:** early development, and not yet on PyPI. It currently lists **skills**, **MCP servers**, and **extensions** for **Claude Code** and **Codex**. More agents are planned; see the [product requirements](docs/PRD.md).

## Usage

The PyPI package is named `agt-view`. Once it's published, you can run it without installing:

```sh
uvx agt-view
```

Run it from your project root to include project-level configuration. If you install it, the command is `agent-view`:

```sh
agent-view                              # list everything for active agents
agent-view --all                        # also show agents not both installed and configured
agent-view --agent codex --kind mcp     # filter by agent and kind
```

`agent-view` only reads files. It never changes your configuration or runs your agents.

## Development

```sh
uv run agent-view   # run from source
uv run pytest       # run the tests

# Also compare results with real agents, in a temporary HOME
AGENT_VIEW_CODEX=/path/to/codex AGENT_VIEW_CLAUDE=/path/to/claude uv run pytest
```
