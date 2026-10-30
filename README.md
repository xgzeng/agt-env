# agent-view

A read-only CLI that shows what each coding agent on your machine can use from the current directory.

**Status:** early development, and not yet on PyPI. It currently lists **skills** for **Claude Code** and **Codex**. MCP servers, extensions, and more agents are planned; see the [product requirements](docs/PRD.md).

## Usage

Run it from your project root to include project-level configuration:

```sh
agent-view                              # list skills for active agents
agent-view --all                        # also show agents not both installed and configured
agent-view --agent codex --scope user   # filter by agent and scope
```

`agent-view` only reads files. It never changes your configuration or runs your agents.

## Development

```sh
uv run agent-view   # run from source
uv run pytest       # run the tests

# Also compare skill discovery with a real Codex install, in a temporary HOME
AGENT_VIEW_CODEX=/path/to/codex uv run pytest
```
