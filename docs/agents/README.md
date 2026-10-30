# Agent discovery rules

How each supported agent finds its skills, MCP servers, and extensions, where those rules are documented, and what testing found that the documentation doesn't say.

| Agent | Binary | Version tested | How it was tested |
|---|---|---|---|
| [Claude Code](claude-code.md) | `claude` | 2.1.314 | `claude mcp list`, `claude plugin list --json` |
| [Codex](codex.md) | `codex` | 0.166.0 | `codex app-server`, `codex mcp list --json` |
| [OpenCode](opencode.md) | `opencode` | 2.2.2 | Server API (`/api/skill`, `/api/mcp`, `/api/plugin`) |
| [Pi](pi.md) | `pi` | 1.4.0 | `pi --mode rpc`, `pi mcp list` |
| [Antigravity CLI](antigravity.md) | `agy` | 1.4.11 | `agy mcp list`, `agy plugin list`, signed-in `/skills`, `/mcp`, and `/plugin` |

Findings were recorded on 2026-10-30. Agents change often, so treat these as true of the versions above. Except where a page says otherwise, each agent ran against test fixtures in a temporary `HOME`.

## Evidence labels

Every rule is labelled with where it comes from:

| Label | Meaning |
|---|---|
| **Doc** | Stated in official documentation, not confirmed by running the agent |
| **Tested** | Observed from the real agent, and not stated in the documentation we read |
| **Doc + Tested** | Stated in the documentation and confirmed by the real agent |
| **Assumed** | Neither documented nor tested. `agt-view` relies on it, but it may be wrong |

## Skill directories across agents

Some directories are read by more than one agent. This answers the PRD's open question on shared skill directories.

| Directory | Claude Code | Codex | OpenCode | Pi | Antigravity CLI |
|---|---|---|---|---|---|
| `~/.agents/skills` | No (not documented) | Yes | Yes | Yes | No (not listed by `/skills`) |
| `.agents/skills` | No (not documented) | Yes | Yes | Yes | Yes |
| `~/.claude/skills` | Yes | No (not documented) | Yes | No (not documented) | No (not listed by `/skills`) |
| `.claude/skills` | Yes | No (not documented) | Yes | No (not documented) | No (not documented) |

## Reproducing the tests

The real-agent comparison tests run only when pointed at a binary, and always use a temporary `HOME`:

```sh
AGT_VIEW_CLAUDE=/path/to/claude AGT_VIEW_CODEX=/path/to/codex \
AGT_VIEW_OPENCODE=/path/to/opencode AGT_VIEW_PI=/path/to/pi AGT_VIEW_AGY=/path/to/agy \
uv run pytest
```

Antigravity skills and project items need a signed-in session, so they were checked by hand and have no automated test.
