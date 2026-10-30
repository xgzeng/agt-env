# agent-view

A read-only CLI that shows what each coding agent on your machine can use from the current directory: skills, MCP servers, and plugins/extensions.

**Status:** in design, with nothing to install or run yet. See the [product requirements](docs/PRD.md).

## Planned usage

```sh
uvx agent-view                                # list everything for active agents
uvx agent-view list --agent codex --kind mcp  # filter by agent and kind
uvx agent-view show my-skill                  # show one item across agents
```

Run it from your project root to include project-level configuration.
