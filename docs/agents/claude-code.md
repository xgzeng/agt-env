# Claude Code

Tested version: 2.1.316. See [evidence labels](README.md#evidence-labels).

## Official documentation

- [Skills](https://code.claude.com/docs/en/skills)
- [MCP](https://code.claude.com/docs/en/mcp)
- [Managed MCP](https://code.claude.com/docs/en/managed-mcp)
- [Settings](https://code.claude.com/docs/en/settings)
- [Plugins reference](https://code.claude.com/docs/en/plugins-reference)

## Configuration

| Rule | Evidence |
|---|---|
| The config directory is `~/.claude`, moved by `CLAUDE_CONFIG_DIR` | Doc |
| Managed settings live in `/etc/claude-code` on Linux and `/Library/Application Support/ClaudeCode` on macOS | Doc |
| Settings layers are user `settings.json`, project `.claude/settings.json`, project `.claude/settings.local.json`, and managed `managed-settings.json` | Doc |
| With `CLAUDE_CONFIG_DIR` set, `.claude.json` is read from that directory instead of `HOME` | Tested |

## Skills

Claude Code has no non-interactive skill listing, so these rules weren't confirmed against the real agent.

| Rule | Evidence |
|---|---|
| Personal skills are `~/.claude/skills/<name>/SKILL.md` | Doc |
| Project skills are `.claude/skills/<name>/SKILL.md`, in the start directory and each parent up to the repository root, or the worktree root in a linked worktree | Doc |
| Skills in `<subdir>/.claude/skills` load only once a file in that subdirectory is read or edited | Doc |
| Enterprise skills are `.claude/skills/<name>/SKILL.md` in the managed directory | Doc |
| Plugin skills are `<plugin>/skills/<name>/SKILL.md`, one level deep, and are namespaced as `plugin:skill` | Doc + Tested |
| claude.ai account skills sync into `~/.claude/skills/synced` | Doc |
| A skill entry can be a symlink to a directory, and is loaded once even if several locations point at it | Doc |
| All frontmatter is optional. `name` defaults to the directory name, and `description` to the first non-empty body line | Doc |
| If the frontmatter's YAML doesn't parse, the skill still loads with no fields set | Doc |
| Names containing `<`, `>`, zero-width, or control characters are refused, as are `anthropic-skills` and `anthropic-skills:*` outside a plugin | Doc |
| On a name conflict, enterprise beats personal, and personal beats project. `agt-view` reports the loser as `shadowed` | Doc |
| `skillOverrides: "off"` hides a skill. `"name-only"` and `"user-invocable-only"` keep it in the `/` menu. Plugin skills aren't affected | Doc |
| A `permissions.deny` rule of `Skill`, `Skill(name)`, or `Skill(name *)` blocks skills | Doc |

## MCP servers

| Rule | Evidence |
|---|---|
| User servers are the top-level `mcpServers` in `.claude.json` | Tested |
| Local servers are `projects["<path>"].mcpServers` in `.claude.json` | Doc + Tested |
| Project servers are `mcpServers` in `.mcp.json` at the project root | Doc + Tested |
| Managed servers are `managed-mcp.json` and the `managedMcpServers` setting | Doc |
| When `managed-mcp.json` exists, only managed servers load | Doc |
| An entry with no `type` is stdio. One with a `url` but no `type` is skipped with an error | Doc + Tested |
| `type` can be `stdio`, `http`, `streamable-http`, `sse`, or `ws`. `sdk` entries are skipped | Doc |
| Precedence is local, project, user, plugin, then claude.ai connectors. The whole entry from the winning source is used. `agt-view` reports the loser as `shadowed` | Doc + Tested |
| A project server waiting for approval doesn't replace the user's server | Tested |
| `disabledMcpServers` in the project's `.claude.json` entry turns off user and local servers there | Doc + Tested |
| Project servers wait for approval until `enabledMcpjsonServers` or `enableAllProjectMcpServers` applies | Doc + Tested |
| Approvals in project settings count only once the folder is trusted. Approvals in user settings always count | Doc + Tested |
| `disabledMcpjsonServers` in any settings file rejects a server | Doc + Tested |
| `allowedMcpServers` and `deniedMcpServers` filter servers by name, command, or URL. Denial wins | Doc |

## Plugins

| Rule | Evidence |
|---|---|
| Installed plugins are recorded in `plugins/installed_plugins.json` under the config directory | Tested |
| `enabledPlugins` in settings holds `name@marketplace` keys with true or false, and later layers override earlier ones | Tested |
| A plugin's manifest is `.claude-plugin/plugin.json`, which is optional | Doc + Tested |
| The manifest's `skills`, a path or list of `./` paths, adds to `skills/`. Each is a directory of skills or one skill's folder | Doc + Tested |
| A `SKILL.md` at the plugin root counts only with no `skills/` and no `skills` key | Doc + Tested |
| `name` replaces the last segment of a plugin skill's name, and a name already carrying the prefix doesn't get it twice | Doc |
| A root skill with no `name` is named after the plugin | Doc + Tested |
| A plugin skill and a same-named skill elsewhere both load, because of the prefix | Doc |
| Disabling a plugin unloads its skills | Doc + Tested |
| `skillOverrides` doesn't affect plugin skills | Doc |

## Subagents

| Rule | Evidence |
|---|---|
| Agents are Markdown files under `agents/` in the config directory and `.claude/agents`, searched recursively, including hidden files | Doc + Tested |
| Managed agents are in the managed directory | Doc, and the path is assumed to match skills |
| Identity is `name`, and the file name doesn't matter | Doc + Tested |
| No `name`, no `description`, a `name` with `:`, or one starting with `-` means the file isn't loaded. `agt-view` reports it as `broken` | Doc + Tested |
| Precedence from highest is managed, project, user, then plugin. `agt-view` reports the loser as `shadowed` | Doc |
| Two same-named files in one directory load one, by read order, so neither is flagged | Doc |
| `Agent` in `permissions.deny` blocks all, and `Agent(name)` blocks one | Doc |
| Plugin agents are under `agents/`, named `plugin:folder:name`, and fall back to the file name | Doc + Tested |
| The manifest's `agents` replaces the default, and takes files. A directory there loaded nothing | Doc + Tested |

## Commands

| Rule | Evidence |
|---|---|
| Commands are `*.md` under `commands/` in the config directory and `.claude/commands`, searched recursively, including hidden entries | Doc + Tested |
| The name comes from the path, with `:` between folders. A `name` field is ignored | Doc + Tested |
| A skill beats a same-named command. `agt-view` reports the command as `shadowed` | Doc |
| `skillOverrides: "off"` hides a command | Tested |
| Plugin commands are under `commands/` and named `plugin:folder:name` | Doc + Tested |
| The manifest's `commands` replaces the default. It takes a directory, a file, a list of those, or an object whose keys are the names | Doc + Tested |

## Hooks

| Rule | Evidence |
|---|---|
| Hooks are under `hooks` in user, project, local, and managed settings, and they merge | Doc + Tested |
| A plugin's are in `hooks/hooks.json`, and the manifest's `hooks` adds to them | Doc + Tested |
| The form is event, then matcher group, then handlers. Handlers have no names, so `agt-view` shows `event:matcher` | Doc |
| `disableAllHooks` turns off all but managed hooks. The last settings layer that sets it decides, so a project can undo a user's value | Doc + Tested |
| `allowManagedHooksOnly` in managed settings blocks every other hook | Doc |
| An invalid handler or an unknown event is skipped, and the rest still run | Tested |

## Findings not in the documentation

- **Startup listing:** `claude -p hi --output-format stream-json --verbose` prints an `init` message with `agents`, `slash_commands`, and `skills` before it finds that no one is signed in, and session-start hooks run first. So it works in a temporary `HOME` and tests compare with it. It lists a skill by its folder name, even where `name` differs, so a differing `name` still follows the docs.
- **`defaultEnabled`:** a plugin with no `enabledPlugins` entry doesn't load, even with `defaultEnabled: true`. `agt-view` reports it as `disabled`.

- **`installed_plugins.json` shape:** `{"version": 2, "plugins": {"name@marketplace": [{"scope", "installPath", "version", ...}]}}`. Local-scope entries also carry `projectPath`.
- **Approvals in `.claude.json`:** `enabledMcpjsonServers` also works in the project's entry there, but only when the folder is trusted.
- **Rejected servers:** the docs show a `Rejected` status, but `claude mcp list` left out a server rejected through `disabledMcpjsonServers`.
- **Plugin contents:** `claude plugin details <id>` lists a plugin's skills, and does so for a disabled plugin too, so it says what a plugin contains rather than what loads. The startup listing shows a disabled plugin's skills, agents, and commands are gone.
- **Manifest paths:** a `skills` path without `./`, or one that leaves the plugin, makes installing it fail.
- **Hidden plugin skills:** `skills/.name` is listed, and a Markdown file directly under `skills/` isn't.
- **Synced skill layout:** `skills/synced/<bucket>/<skill>/SKILL.md` was seen on a real install, one level deeper than other skills.
- **Synced plugins:** `claude plugin list` can report `name@synced` plugins that aren't in `installed_plugins.json`.
- **`claude mcp list` starts servers:** it checks each server's health, so it isn't safe on an untrusted project. It's also why `agt-view` doesn't call it.

## Assumptions

- A `Skill(name)` or `Agent(name)` deny rule blocks that item, as the docs say. The startup listing still shows it, so a session would be needed to confirm it.
- A hook in a project's settings waits for the workspace trust, like approvals in those settings. `claude -p` skips the trust dialog, so this couldn't be confirmed, and `agt-view` shows `pending`.
- `CLAUDE_CONFIG_DIR` also moves personal skills. The docs say it moves settings, session history, and plugins, without mentioning skills.

## Not covered by `agt-view`

- MCP servers bundled in plugins, and synced skills and plugins.
- Parent and nested project directories, because `agt-view` treats the current directory as the project root.
- Built-in skills, agents, and commands, and claude.ai connectors, which aren't in local config.
- Hooks in skill and agent frontmatter, which only run with that skill or agent.
- `--agents`, `--add-dir`, and other per-session sources.
