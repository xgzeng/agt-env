# Agent discovery rules

How each supported agent finds its skills, MCP servers, extensions, hooks, subagents, and commands, where those rules are documented, and what testing found that the documentation doesn't say.

| Agent | Binary | Version tested | How it was tested |
|---|---|---|---|
| [Claude Code](claude-code.md) | `claude` | 2.1.316 | `claude mcp list`, `claude plugin list --json`, `claude plugin details`, the startup listing of `claude -p` |
| [Codex](codex.md) | `codex` | 0.166.0 | `codex app-server`, `codex mcp list --json` |
| [OpenCode](opencode.md) | `opencode` | 2.2.5 | Server API (`/api/skill`, `/api/mcp`, `/api/plugin`, `/api/agent`, `/api/command`) |
| [Pi](pi.md) | `pi` | 1.5.0 | `pi --mode rpc`, `pi mcp list` |
| [Antigravity CLI](antigravity.md) | `agy` | 1.4.12 | `agy mcp list`, `agy plugin list`, `agy agents`, signed-in `/skills`, `/mcp`, and `/plugin` |

Findings were last checked on 2026-11-02. Agents change often, so treat these as true of the versions above. Except where a page says otherwise, each agent ran against test fixtures in a temporary `HOME`.

## Evidence labels

Every rule is labelled with where it comes from:

| Label | Meaning |
|---|---|
| **Doc** | Stated in official documentation, not confirmed by running the agent |
| **Tested** | Observed from the real agent, and not stated in the documentation we read |
| **Doc + Tested** | Stated in the documentation and confirmed by the real agent |
| **Source** | Read in the agent's published source at the tested version, not confirmed by running it |
| **Assumed** | Neither documented nor tested. `agt-env` relies on it, but it may be wrong |

## Skill directories across agents

Some directories are read by more than one agent. This answers the PRD's open question on shared skill directories.

| Directory | Claude Code | Codex | OpenCode | Pi | Antigravity CLI |
|---|---|---|---|---|---|
| `~/.agents/skills` | No (tested) | Yes | Yes | Yes | No (not listed by `/skills`) |
| `.agents/skills` | No (tested) | Yes | Yes | Yes | Yes |
| `~/.claude/skills` | Yes | No (not documented) | Yes | No (not documented) | No (not listed by `/skills`) |
| `.claude/skills` | Yes | No (not documented) | Yes | No (not documented) | No (not documented) |

## Credential stores

`agt-env` never opens these, and `tests/test_safety.py` plants each one and audits what a run opens.

| Agent | File | Evidence |
|---|---|---|
| Claude Code | `~/.claude/.credentials.json`, or `.credentials.json` in `CLAUDE_CONFIG_DIR` | Tested |
| Codex | `~/.codex/auth.json` | Tested |
| OpenCode | `~/.local/share/opencode/opencode.db`, `service.json` in the global config directory | Tested |
| OpenCode, older versions | `~/.local/share/opencode/auth.json`, `~/.config/opencode/auth.json` | Assumed |
| Pi | `~/.pi/agent/auth.json` | Assumed |
| Antigravity CLI | `~/.gemini/oauth_creds.json`, `~/.gemini/google_accounts.json`, `~/.gemini/antigravity-cli/oauth_creds.json` | Assumed |

Tested means the file was seen on a signed-in machine, by name only. Assumed locations are the usual ones for that agent, and they may be wrong. Adapters only open the config files their pages list, so a wrong location here doesn't change what is read.

For every agent, a skill file that is a symlink to anything but a Markdown file isn't opened, and the skill is reported as `broken`. This is `agt-env`'s own rule, not the agent's: an agent may still load that file. Without it, a link in a cloned repository could print any file, because Claude Code's descriptions can come from a skill's text.

## Reproducing the tests

The real-agent comparison tests run only when pointed at a binary, and always use a temporary `HOME`:

```sh
AGT_ENV_CLAUDE=/path/to/claude AGT_ENV_CODEX=/path/to/codex \
AGT_ENV_OPENCODE=/path/to/opencode AGT_ENV_PI=/path/to/pi AGT_ENV_AGY=/path/to/agy \
uv run pytest
```

Antigravity skills and project items need a signed-in session, so they were checked by hand and have no automated test.
