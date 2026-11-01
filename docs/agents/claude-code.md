# Claude Code

Tested version: 2.1.314. See [evidence labels](README.md#evidence-labels).

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
| Plugin skills are `<plugin>/skills/<name>/SKILL.md`, and are namespaced as `plugin:skill` | Doc |
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

## Findings not in the documentation

- **`installed_plugins.json` shape:** `{"version": 2, "plugins": {"name@marketplace": [{"scope", "installPath", "version", ...}]}}`. Local-scope entries also carry `projectPath`.
- **Approvals in `.claude.json`:** `enabledMcpjsonServers` also works in the project's entry there, but only when the folder is trusted.
- **Rejected servers:** the docs show a `Rejected` status, but `claude mcp list` left out a server rejected through `disabledMcpjsonServers`.
- **Plugins not in `enabledPlugins`:** the docs say `defaultEnabled` is true by default, but `claude plugin list --json` reported an unset plugin as disabled. That plugin's marketplace wasn't installed, which may explain it.
- **Synced skill layout:** `skills/synced/<bucket>/<skill>/SKILL.md` was seen on a real install, one level deeper than other skills.
- **Synced plugins:** `claude plugin list` can report `name@synced` plugins that aren't in `installed_plugins.json`.
- **`claude mcp list` starts servers:** it checks each server's health, so it isn't safe on an untrusted project. It's also why `agt-view` doesn't call it.

## Assumptions

- `CLAUDE_CONFIG_DIR` also moves personal skills. The docs say it moves settings, session history, and plugins, without mentioning skills.

## Not covered by `agt-view`

- Skills and MCP servers bundled in plugins, and synced skills and plugins.
- Parent and nested project directories, because `agt-view` treats the current directory as the project root.
- Built-in skills and claude.ai connectors, which aren't in local config.
