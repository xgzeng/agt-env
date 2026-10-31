# agt-view: Product Requirements

| | |
|---|---|
| **Status** | Draft v0.1 |
| **Date** | 2026-10-30 |
| **Name** | `agt-view` (command and package) |

## 1. Summary

`agt-view` is a read-only CLI that answers one question: **what can each coding agent on my machine use from this directory?** It finds the skills, MCP servers, and plugins configured for each agent, puts them in one model, and shows them in the terminal.

## 2. Problem

Anyone running several coding agents has capabilities scattered across incompatible locations and formats, and no single place to see them. Common situations make this harder than listing a few directories:

| Situation | Why it matters |
|---|---|
| Each agent has its own locations, file formats, scopes, and terminology | Checking each one by hand doesn't scale past one or two agents |
| The same skill can be linked or copied into several agents' directories | A plain listing overstates what exists and hides what is shared |
| Copies of a skill can change independently | Agents can end up with different versions and no warning |
| Agents may cache plugins they haven't enabled | Treating a directory's contents as available would mislead |
| An agent can be installed but unconfigured, or leave config behind after being uninstalled | "Installed" and "configured" are separate states |
| Some capabilities are built into the agent or attached to a cloud account | Files on disk don't describe everything available at runtime |
| Agent CLIs can be slow to start, need authentication or network access, or have side effects | Calling each CLI is too slow and unreliable to be the default |

## 3. Goals and non-goals

### Goals

1. List capabilities across all agents in one command, with no setup, including no prior install.
2. Be accurate about what is known: report status and source, and say "unknown" rather than guess.
3. Resolve context from the current directory, covering both user and project scope.
4. Expose shared, duplicated, and shadowed items instead of hiding them.
5. Make adding a new agent a small, isolated change.

### Non-goals (v1)

- **Changing anything:** no install, remove, enable, disable, or sync. These may come later, but v1 is strictly read-only.
- **Machine-readable output:** v1 is for people reading a terminal. JSON or similar output may come later.
- **Marketplace browsing:** v1 covers what is on the local machine, not what could be installed.
- **Exact session state:** v1 doesn't report what a particular running session has loaded.
- **Account-level discovery:** remote connectors that exist only in an agent's cloud account aren't enumerated.
- **Windows support:** Linux and macOS come first.

## 4. Users

- **Primary: multi-agent developer.** Switches between agents and wants to know why one of them can't see a skill or MCP server.
- **Secondary: security-conscious user.** Audits what is configured, especially in an unfamiliar repository.

### Core scenarios

1. "What do I have across all my agents?" → `agt-view`
2. "Which MCP servers can Codex use here?" → `agt-view -a codex -k mcp`
3. "Where does this skill come from, and who else has it?" → `agt-view -n my-skill --detail`
4. "What does this repo add to my agents?" → `agt-view --scope project`

## 5. Functional requirements

Priorities: **P0** is required for v1, **P1** follows soon after, and **P2** is later.

### 5.1 Commands

| Command | Purpose | Priority |
|---|---|---|
| `agt-view [keyword...]` | Agents and their items, filterable | P0 |
| `agt-view doctor` | Broken symlinks, parse failures, drifted copies, shadowing | P1 |
| `agt-view probe` | Opt-in live checks through agent CLIs (see 5.5) | P2 |

Agents appear in rank order (see section 6), each with a header showing its state and config location, followed by its items sorted by scope (project, user, system), then kind, then name.

**Flags:** `--all`, `--detail`, `-a`/`--agent <agent>` (repeatable), `-n`/`--name <text>` (repeatable), `-k`/`--kind <kind>` (repeatable), `-s`/`--scope project|user|system`, `-p`/`--project <path>`, `--no-color`.

**Filters:** different filters narrow each other.

- `-a` shows only the named agents, even ones hidden by default.
- `-n` matches a case-insensitive part of the item name. Repeating it widens the results.
- Keywords match a case-insensitive part of everything shown for an item, including its details and path. Words in one argument narrow and separate arguments widen, so `agt-view "browser test" lint` finds (browser and test) or lint.
- With `-n` or keywords, agents with no matches are left out. If none are left, the output says so.

**Illustrative output:**

```text
$ agt-view
agent-a  ~/.agent-a
  SCOPE/KIND  NAME        STATUS
  P/skill     repo-skill  enabled
  U/skill     my-skill    enabled
  U/mcp       my-server   enabled
  U/plugin    my-plugin   disabled

agent-b  /opt/agent-b (set by AGENT_B_HOME)
  SCOPE/KIND  NAME      STATUS
  U/skill     my-skill  enabled
  U/plugin    other     unknown

Hidden: agent-c, agent-d. Use --all for details.
```

`P`, `U`, and `S` stand for the project, user, and system scopes. `--detail` adds lines under each item, starting at the `NAME` column, for everything the columns don't show, and a line of dashes after the table head and between items:

```text
$ agt-view -a agent-b -n my-skill --detail
agent-b  /opt/agent-b (set by AGENT_B_HOME)
SCOPE/KIND  NAME      STATUS
-----------------------------
U/skill     my-skill  enabled
            Description:  Does a thing
            Path:         /opt/agent-b/skills/my-skill (symlink)
            Real path:    /opt/shared/my-skill
            Shared with:  agent-a
```

With `--all`, hidden agents are added with their state:

```text
agent-c  not installed, configured at ~/.agent-c
agent-d  installed, not configured
```

### 5.2 Capability kinds

| Kind | Meaning | Priority |
|---|---|---|
| `skill` | Skill directory or file, usually `SKILL.md` | P0 |
| `mcp` | Configured MCP server | P0 |
| `plugin` | Plugin or extension, with the agent's own term kept in `native_kind` | P0 |
| `hook` | Lifecycle hook | P1 |
| `subagent` | Custom agent definition | P1 |
| `command` | Custom slash command | P1 |
| `instructions` | Agent instruction and rules files | P2 |

### 5.3 Discovery

- **FR-1 (P0):** Detect each agent independently through its binary on `PATH` and its config locations, and report the two separately. An agent is configured if it has config at user or project scope. A version-manager shim on `PATH` counts as a binary, but detection must not execute it.
- **FR-2 (P0):** Discover items at user and project scope. The current directory, or `--project` if given, is assumed to be the project root. Parent directories are not searched, so running from a subdirectory doesn't find the project's items. System-level locations are read where the agent defines them.
- **FR-3 (P0):** Get all results by reading files on disk. Only `probe` (FR-14) may run other programs, so all other commands must not launch agent CLIs or start MCP servers.
- **FR-4 (P0):** Determine status from the agent's own configuration. An item in a plugin cache that is not enabled must not be reported as `enabled`.
- **FR-5 (P0):** Follow symlinks and record both the path found and the resolved path. Report a dangling symlink as `broken`.
- **FR-6 (P0):** Treat items that resolve to the same real path as shared, naming the other agents under `--detail`, while still listing them under each agent.
- **FR-7 (P1):** Flag same-named skills at different real paths with different content as drift.
- **FR-8 (P1):** Show when a project item shadows a user item, following that agent's precedence rules.
- **FR-9 (P1):** List skills bundled in enabled plugins, with namespaced names such as `my-plugin:my-skill`.
- **FR-10 (P0):** Only treat a directory as a skill if it meets the agent's definition. A directory that only groups other skills must be descended into or skipped, not counted.
- **FR-20 (P0):** Resolve locations from the environment, not fixed paths: honor `HOME`, XDG variables, and each agent's documented config-directory overrides. When an override moves an agent's location away from its default, the output shows the location in use and the variable that set it in the agent's header.
- **FR-21 (P0):** Work from any starting state. With no agents detected, or an agent's directories missing or unreadable, the tool still succeeds and says what it found. A missing location isn't a warning; an unreadable one is.
- **FR-22 (P0):** By default, only agents that are both installed and configured are shown, and the output names the agents it hid. `--all` shows every detected agent, and `-a` always shows the agent it names, in each case with its state.

### 5.4 Data model

Every item has these fields, and adapters must fill them consistently:

| Field | Meaning | Example |
|---|---|---|
| `id` | Unique identity of the item | `agent-a:skill:user:my-skill` |
| `name` | Display name | `my-skill` |
| `kind` | Normalized kind (see 5.2) | `plugin` |
| `native_kind` | The agent's own term | `plugin` |
| `agent` | Agent ID (see section 6) | `agent-a` |
| `scope` | `user`, `project`, or `system` | `user` |
| `status` | `enabled`, `disabled`, `pending` (waiting for the user to approve or trust it), `broken`, or `unknown` | `enabled` |
| `description` | Short description, if the source has one | What the skill does and when to use it. |
| `source_path` | Where the item was found | `/home/me/.agent-a/skills/my-skill` |
| `resolved_path` | Real path after following symlinks | `/home/me/shared-skills/my-skill` |
| `shared_with` | Other agents using the same real path | `agent-b` |
| `details` | Kind-specific information | See below |

**MCP `details`** contains transport, command or URL, and the *names* of env vars and headers, never their values.

### 5.5 Safety

- **FR-11 (P0):** Never modify files in agent config directories or anywhere else.
- **FR-12 (P0):** Never display secret values in any output. Env values, header values, tokens, and URL credentials are omitted or masked. Files an adapter identifies as agent credential stores are never opened.
- **FR-13 (P0):** Never run code from inspected configuration. Listing an MCP server must not start it, because a repository's project config can name any command.
- **FR-14 (P2):** `probe` is the only command that may run other programs. It must be explicitly invoked, apply a timeout per agent, and show what it will execute.
- **FR-15 (P0):** Treat inspected file contents as untrusted. Strip terminal escape sequences from names and descriptions before display.

### 5.6 Output and errors

- **FR-16 (P0):** On a color terminal, show each agent in a colored panel, with a symbol and a color for each status. Use plain aligned text when piped, with `--no-color`, when `NO_COLOR` is set, when `TERM` is `dumb`, or when the output encoding isn't UTF-8. The illustrative output above is the plain form. Without `--detail`, the terminal table also shows the start of each description, cut to the space left in the terminal, and leaves the column out when too little space is left. Other long values wrap on a terminal and are never cut.
- **FR-17 (P0):** A broken config for one agent must not stop the others. It produces a warning on stderr naming the agent and file, and items from that agent's other sources still appear.
- **FR-18 (P0):** Exit codes: `0` for success, including partial results with warnings and empty results; `1` for a fatal runtime error; `2` for a usage error.
- **FR-19 (P0):** Unknown fields in agent configs are ignored without error, so a new agent release doesn't break parsing.

## 6. Agent support

Each agent is one adapter behind a common interface: detect the agent, then return items and warnings. An adapter must not depend on any other adapter.

Adapters are built in rank order. P0 agents are required for v1, and P2 agents are added after v1, when there is demand.

| Rank | Agent | ID | Priority |
|---|---|---|---|
| 1 | Claude Code | `claude-code` | P0 |
| 2 | Codex | `codex` | P0 |
| 3 | OpenCode | `opencode` | P0 |
| 4 | Pi | `pi` | P0 |
| 5 | Antigravity CLI | `antigravity` | P0 |
| 6 | Gemini CLI | `gemini` | P2 |
| 7 | Cursor | `cursor` | P2 |
| 8 | GitHub Copilot CLI | `copilot` | P2 |
| 9 | Amp | `amp` | P2 |
| 10 | Qwen Code | `qwen` | P2 |
| 11 | Crush | `crush` | P2 |
| 12 | Kiro | `kiro` | P2 |
| 13 | Goose | `goose` | P2 |
| 14 | Cline | `cline` | P2 |
| 15 | Windsurf | `windsurf` | P2 |

The ranking can change without affecting the architecture.

### Adapter rules

- Each adapter declares its locations, formats, scopes, and precedence rules, taken from the agent's documentation and a clean install. They live with the adapter rather than in this PRD, because they change with agent releases.
- Every format claim in an adapter is backed by a fixture and the agent version it was checked against.
- Tests run against fixtures in an isolated `HOME`, never against a developer's real configuration.
- Where an agent has a read-only listing command, it is used as a test oracle during development, not at runtime.
- An adapter attributes a cross-agent shared location to an agent only if that agent is confirmed to read it.
- Agents without an adapter are ignored: they aren't detected, listed, or mentioned in any output.

## 7. Non-functional requirements

| Area | Requirement |
|---|---|
| **Performance** | `agt-view` finishes in under 200 ms on a reference fixture of 10 agents and 500 items, on a typical developer laptop; no subprocesses are spawned. The target excludes the time `uvx` needs to set up its environment |
| **Distribution** | Published to PyPI and runnable with `uvx agt-view`, with no prior install; `uv tool install` also works. Requires Python 3.11 or later, with as few third-party dependencies as practical; Rich is used for terminal output, and plain output doesn't load it |
| **Portability** | Linux and macOS, on any home directory layout, shell, or version manager |
| **Extensibility** | A new adapter touches only its own module, fixtures, and one registry line |

**Implementation language:** Python.

## 8. Success metrics

These are acceptance checks, all run against fixtures in CI:

1. **Accuracy:** for each P0 agent, MCP results match that agent's own listing command on the same fixture, wherever such a command exists.
2. **No false claims:** no item is reported as `enabled` without evidence in that agent's config.
3. **Secret safety:** a test config with planted secrets produces no secret text in any command's output.
4. **Inertness:** a test project config naming a marker-writing command leaves no marker after any P0 command.
5. **Resilience:** corrupting any single agent's config still produces full results for the others, with exit code 0 and a warning.
6. **Clean start:** with an empty `HOME` and no agents on `PATH`, `agt-view` exits 0 with a clear message.
7. **Speed:** the 200 ms target holds on the reference fixture.
8. **Zero-install run:** in a clean environment, `uvx` runs the built package and `agt-view` produces correct output.

## 9. Risks

| Risk | Mitigation |
|---|---|
| Agent formats change often and without notice | Tolerant parsing, per-adapter fixtures, and visible warnings instead of crashes |
| Users read the output as exactly what a session has loaded | Label output as configured state; document the gaps; add `probe` later |
| Enablement rules are undocumented or complex | Use `unknown` status rather than guessing |
| Secrets leak through unexpected fields, such as credentials in MCP args | Allowlist the fields to display; mask URL credentials and values following secret-like flags |
| Terminal injection through a malicious skill description | Sanitize all displayed strings (FR-15) |

## 10. Milestones

1. **M1, foundation:** data model, adapter interface, skill discovery, Claude Code and Codex adapters, listing, packaging for `uvx`.
2. **M2, P0 complete:** remaining P0 adapters, MCP and plugins, details and filters, safety tests.
3. **M3, diagnostics:** `doctor`, drift and shadow detection, plugin-bundled skills, hooks, subagents, commands.
4. **M4, breadth:** `probe`, and P2 adapters in rank order as demand appears.

## 11. Decisions and open questions

### Decisions made for this draft

| Decision | Reasoning |
|---|---|
| Read-only in v1 | Keeps the tool safe to run anywhere and the scope small |
| Static inspection by default | Fast and inert, with no dependence on an agent's startup, authentication, or network |
| Unified `plugin` kind | "Plugin" and "extension" vary by agent; `native_kind` keeps the original term |
| Shared items listed under each agent | Each agent really can use them; sharing is shown separately |

### Open questions

1. **Runtime gap:** should built-in skills and account-level connectors, which aren't in local config, be documented as out of scope permanently, or added through `probe`?
2. **Shared skill directories:** some skill directories sit outside any one agent's own config and may be read by several agents. Which of these directories exist, and which agents read each one? This decides which agents a skill is listed under: the output could otherwise claim an agent has a skill it never loads, or miss one it does. The answer is needed for each agent when its adapter is built. Until it is confirmed, skills from that directory aren't listed under that agent.
