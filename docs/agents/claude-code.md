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
| The config directory is `~/.claude`, moved by `CLAUDE_CONFIG_DIR` | Doc + Tested |
| With `CLAUDE_CONFIG_DIR` set, `.claude.json` is read from that directory instead of `HOME`, even if the variable names `~/.claude` | Tested |
| Managed settings live in `/etc/claude-code` on Linux and `/Library/Application Support/ClaudeCode` on macOS. Only the Linux path was tested | Doc + Tested |
| Settings layers are user `settings.json`, project `.claude/settings.json`, project `.claude/settings.local.json`, and managed settings | Doc |
| Managed settings are `managed-settings.json`, then non-hidden `*.json` files in `managed-settings.d/`, in name order. A later file wins for a single value, and lists join | Doc + Tested |
| A corrupt file in `managed-settings.d/` stops Claude Code starting. `agt-env` warns and reports `unknown` | Tested |
| Run from the home directory, `.claude` is the user's directory and is read once | Tested |
| `.claude.json` keeps a project under its repository's root, or under the directory if it isn't in a repository. An entry for any other directory, including the current one, is ignored there. This covers trust, local servers, and `disabledMcpServers` | Tested |
| Keys in `.claude.json` are real paths, not symlinks | Tested |
| Project settings count before the folder is trusted: `claude mcp list` obeys a project's `deniedMcpServers`, `claude plugin list` its `enabledPlugins`, and `claude -p` its `skillOverrides`. `agt-env` merges them whatever the trust | Tested |
| Managed `strictPluginOnlyCustomization`, `true` or a list of `skills`, `agents`, `hooks`, and `mcp`, turns off what users and projects supply for those. Plugins stay. In settings outside the managed files it does nothing | Doc + Tested |

## Skills

`claude -p`'s startup listing shows which skills load, and tests compare with it. It leaves out a skill with `user-invocable: false` or matching `paths`, though these still load when used, so `agt-env` keeps those enabled.

| Rule | Evidence |
|---|---|
| Personal skills are `~/.claude/skills/<name>/SKILL.md`, moved with `CLAUDE_CONFIG_DIR` | Doc + Tested |
| Project skills are `.claude/skills/<name>/SKILL.md`, in the start directory and each parent up to the repository root, or the worktree root in a linked worktree | Doc |
| Skills in `<subdir>/.claude/skills` load only once a file in that subdirectory is read or edited | Doc |
| Enterprise skills are `.claude/skills/<name>/SKILL.md` in the managed directory | Doc + Tested |
| Plugin skills are `<plugin>/skills/<name>/SKILL.md`, one level deep, and are namespaced as `plugin:skill` | Doc + Tested |
| claude.ai account skills sync into `~/.claude/skills/synced` | Doc |
| A skill entry can be a symlink to a directory, and is loaded once even if several locations point at it | Doc |
| A hidden folder such as `skills/.name` is a skill too | Tested |
| The file must be named exactly `SKILL.md` here. `skill.md` isn't read | Tested |
| A skill's identity is its folder: the name in the startup listing, `skillOverrides`, and precedence. `agt-env` lists it by folder | Tested |
| `name` is what the `/` menu shows, and can also be typed. `--detail` shows it as `Display name`, and a deny rule can use it. The docs say both names invoke the skill | Doc |
| All frontmatter is optional, and `description` defaults to the first non-empty body line | Doc |
| If the frontmatter's YAML doesn't parse, the skill still loads with no fields set | Doc |
| A folder with `<`, `>`, zero-width, or control characters is refused, though a `name` with them loads | Tested |
| `anthropic-skills` and `anthropic-skills:*`, in any case, are refused as a folder or as a `name` | Doc + Tested |
| On a conflict between folders, enterprise beats personal, and personal beats project. `agt-env` reports the loser as `shadowed` | Doc + Tested |
| `skillOverrides: "off"` hides a skill. `"name-only"` and `"user-invocable-only"` keep it in the `/` menu. Plugin skills aren't affected | Doc + Tested |
| With both `disable-model-invocation: true` and `user-invocable: false`, nobody can invoke it, so `agt-env` reports `disabled`. This also applies to commands and plugin skills | Tested |
| A skill with `paths` other than `**` loads only when a matching file is used. `agt-env` keeps it enabled and adds a finding. Plugin skills and commands ignore `paths` | Doc + Tested |
| A `permissions.deny` rule of `Skill`, `Skill(name)`, or `Skill(name *)` blocks skills. The last is a prefix match | Doc |

## MCP servers

| Rule | Evidence |
|---|---|
| User servers are the top-level `mcpServers` in `.claude.json` | Tested |
| Local servers are `projects["<path>"].mcpServers` in `.claude.json` | Doc + Tested |
| Project servers are `mcpServers` in `.mcp.json` at the project root | Doc + Tested |
| Managed servers are `managed-mcp.json` and the `managedMcpServers` setting. The file beats the setting on a shared name, and either beats local, project, and user servers | Doc + Tested |
| When `managed-mcp.json` exists, even corrupt or not a file, only managed servers load. `agt-env` reports the others as `disabled` rather than `unknown`, since this is known | Doc + Tested |
| `managedMcpServers` takes only `http`, `sse`, and `streamable-http` entries with an `https://` URL. Program keys, `${VAR}`, and control characters are refused, and the rest still load. The docs leave out `streamable-http` | Doc + Tested |
| An entry with no `type` is stdio. One with a `url` but no `type` is skipped with an error | Doc + Tested |
| `type` can be `stdio`, `http`, `streamable-http`, `sse`, or `ws`. `sdk` entries are skipped | Doc |
| `args` must be a list of strings, and `env` and `headers` must map to strings. Anything else skips the entry | Tested |
| A remote entry with a bad URL is listed but never connects. `agt-env` reports it as `broken`. For `http`, `sse`, and `streamable-http` that means anything but `http` or `https` with a host. `ws` tries any scheme | Tested |
| Precedence is local, project, user, plugin, then claude.ai connectors. The whole entry from the winning source is used. `agt-env` reports the loser as `shadowed` | Doc + Tested |
| A project server waiting for approval doesn't replace the user's server | Tested |
| `disabledMcpServers` in the project's `.claude.json` entry turns off user, local, and plugin servers there. A plugin server is listed as `plugin:<plugin>:<server>` | Doc + Tested |
| Project servers wait for approval until `enabledMcpjsonServers` or `enableAllProjectMcpServers` applies | Doc + Tested |
| Approvals in project settings count only once the folder is trusted. Approvals in user settings always count | Doc + Tested |
| `disabledMcpjsonServers` in any settings file rejects a server | Doc + Tested |
| `deniedMcpServers` filters every server by name, command, or URL, and wins over any allowlist | Doc + Tested |
| `allowedMcpServers` filters servers that users add. Managed servers skip it, except a `managed-mcp.json` entry with `${VAR}` outside `env` | Doc + Tested |

## Plugins

| Rule | Evidence |
|---|---|
| Installed plugins are recorded in `plugins/installed_plugins.json` under the config directory | Tested |
| `enabledPlugins` in settings holds `name@marketplace` keys with true or false, and later layers override earlier ones | Tested |
| A local or project install applies when its `projectPath` is the directory or its repository's root | Tested |
| A plugin installed in two scopes has both listed by `claude plugin list`, but loads once. Which copy's files load when they differ wasn't determined, because the test's local marketplace read from its own folder. `agt-env` shows the first | Tested |
| A plugin's manifest is `.claude-plugin/plugin.json`, which is optional | Doc + Tested |
| A corrupt manifest doesn't stop a plugin loading its default directories | Tested |
| The manifest's `skills`, a path or list of `./` paths, adds to `skills/`. Each is a directory of skills or one skill's folder | Doc + Tested |
| A `SKILL.md` at the plugin root counts only with no `skills/` and no `skills` key | Doc + Tested |
| A skill in a directory of skills is named after its folder, whatever `name` says. A skill at the plugin root or named by a manifest path uses `name` if it has one. Either way, characters other than letters, digits, `_`, and `-` become `-` | Tested |
| A root skill with no `name` is named after the plugin | Doc + Tested |
| A plugin skill and a same-named skill elsewhere both load, because of the prefix | Doc |
| Disabling a plugin unloads its skills | Doc + Tested |
| `skillOverrides` doesn't affect plugin skills | Doc |
| Plugin servers are in `.mcp.json`, and the manifest's `mcpServers` adds a path, an object, or a list of those. A file can hold them with or without the `mcpServers` wrapper | Doc + Tested |
| A plugin's server doesn't load if an enabled server, or an earlier plugin's, has the same command and arguments or the same URL. The name doesn't matter. `agt-env` reports it as `shadowed` | Tested |
| A folder in a skills directory whose manifest has a `name` is a plugin, `name@skills-dir`. It's on unless `enabledPlugins` or the manifest's `defaultEnabled` says otherwise, and a project's waits for trust. Strict customization of `skills` turns it off | Doc + Tested |
| In such a plugin, a root `SKILL.md` is named after the plugin alone, whatever `name` says. With no named manifest, the folder is an ordinary skill | Tested |

## Subagents

| Rule | Evidence |
|---|---|
| Agents are Markdown files under `agents/` in the config directory and `.claude/agents`, searched recursively, including hidden files | Doc + Tested |
| Managed agents are in `.claude/agents` in the managed directory | Doc + Tested |
| Identity is `name`, and the file name doesn't matter | Doc + Tested |
| No `name`, no `description`, a `name` with `:`, one starting with `-`, or one over 256 characters means the file isn't loaded. `agt-env` reports it as `broken` | Doc + Tested |
| Precedence from highest is managed, project, user, then plugin. `agt-env` reports the loser as `shadowed` | Doc |
| Two same-named files in one directory load one, by read order, so neither is flagged | Doc |
| `Agent` in `permissions.deny` blocks all, and `Agent(name)` blocks one | Doc |
| Plugin agents are under `agents/`, named `plugin:folder:name`, and fall back to the file name. Unlike other agents, they need no frontmatter or `description` | Doc + Tested |
| The manifest's `agents` replaces the default, and takes files. A directory there loaded nothing | Doc + Tested |

## Commands

| Rule | Evidence |
|---|---|
| Commands are `*.md` under `commands/` in the config directory and `.claude/commands`, searched recursively, including hidden entries | Doc + Tested |
| Managed commands are in `.claude/commands` in the managed directory | Tested |
| The name comes from the path, with `:` between folders. A `name` field is ignored | Doc + Tested |
| In a folder with a `SKILL.md`, in any case, the other Markdown files don't count, and the command is named after the folder. Subfolders still count | Tested |
| Precedence is managed, user, then project. `agt-env` reports the loser as `shadowed` | Tested |
| A skill beats a same-named command, in any scope. `agt-env` reports the command as `shadowed` | Doc + Tested |
| `skillOverrides: "off"` hides a command | Tested |
| Strict customization of `skills` also turns off commands, including managed ones. `commands` isn't a value it knows | Tested |
| Plugin commands are under `commands/` and named `plugin:folder:name` | Doc + Tested |
| The manifest's `commands` replaces the default. It takes a directory, a file, a list of those, or an object whose keys are the names | Doc + Tested |
| Each object entry needs either `source`, a `./` path to Markdown, or `content`, and can set `description`. A `source` that doesn't exist costs only that entry | Doc + Tested |
| Any other entry makes `claude plugin install` refuse the plugin. If the manifest changes later, all the object's entries are dropped and `commands/` is used. `agt-env` reports those entries as `broken` | Tested |

## Hooks

| Rule | Evidence |
|---|---|
| Hooks are under `hooks` in user, project, local, and managed settings, including `managed-settings.d/`, and they merge | Doc + Tested |
| A plugin's are in `hooks/hooks.json`, and the manifest's `hooks` adds to them. A file named twice loads once | Doc + Tested |
| The form is event, then matcher group, then handlers. Handlers have no names, so `agt-env` shows `event:matcher` | Doc |
| `disableAllHooks` turns off all but managed hooks. The last settings layer that sets it decides, so a project can undo a user's value | Doc + Tested |
| `allowManagedHooksOnly` in managed settings blocks every other hook, except those of plugins that managed settings enable | Doc + Tested |
| Strict customization of `hooks` blocks user and project hooks, but not plugin or managed ones | Doc + Tested |
| An invalid handler or an unknown event is skipped, and the rest still run. This includes an `http` hook with a malformed URL | Tested |
| An invalid entry under `PreToolUse` or `PermissionRequest` loses the whole user, project, or local settings file, or plugin hook file. So permissions in that file can't apply without their guard. `agt-env` warns, ignores the file's settings, and reports its other hooks as `disabled`. Managed settings don't lose anything this way | Tested |
| `allowedHttpHookUrls`, patterns with `*`, limits `http` hooks. `[]` blocks them all | Doc + Tested |

## Findings not in the documentation

- **Startup listing:** `claude -p hi --output-format stream-json --verbose` prints an `init` message with `agents`, `slash_commands`, `skills`, and `plugins` before it finds that no one is signed in, and session-start hooks run first. So it works in a temporary `HOME` and tests compare with it.
- **Testing managed settings:** on Linux, a user namespace can mount an overlay on `/etc`, which lets the real agent read `/etc/claude-code` without touching the real one. `tests/test_claude_oracle.py` does this.
- **`http` hooks at session start:** an `http` handler under `SessionStart` didn't run when `UserPromptSubmit`'s did. `agt-env` doesn't treat this specially.
- **`defaultEnabled`:** an installed plugin with no `enabledPlugins` entry doesn't load, even with `defaultEnabled: true`. `agt-env` reports it as `disabled`.
- **`installed_plugins.json` shape:** `{"version": 2, "plugins": {"name@marketplace": [{"scope", "installPath", "version", ...}]}}`. Local-scope entries also carry `projectPath`.
- **Approvals in `.claude.json`:** `enabledMcpjsonServers` also works in the project's entry there, but only when the folder is trusted.
- **Rejected servers:** the docs show a `Rejected` status, but `claude mcp list` left out a server rejected through `disabledMcpjsonServers`.
- **Plugin contents:** `claude plugin details <id>` lists a plugin's skills, and does so for a disabled plugin too, so it says what a plugin contains rather than what loads. The startup listing shows a disabled plugin's skills, agents, and commands are gone.
- **Manifest paths:** a `skills` path without `./`, or one that leaves the plugin, makes installing it fail.
- **Hidden plugin skills:** `skills/.name` is listed, and a Markdown file directly under `skills/` isn't.
- **Synced skill layout:** `skills/synced/<bucket>/<skill>/SKILL.md` was seen on a real install, one level deeper than other skills.
- **Synced plugins:** `claude plugin list` can report `name@synced` plugins that aren't in `installed_plugins.json`.
- **`claude mcp list` starts servers:** it checks each server's health, so it isn't safe on an untrusted project. It's also why `agt-env` doesn't call it.

## Assumptions

- A `Skill(name)` or `Agent(name)` deny rule blocks that item, as the docs say. The startup listing still shows it, so a session would be needed to confirm it.
- `Skill(/name)` and `Skill(prefix:*)` work like `Skill(name)` and `Skill(prefix *)`, and a prefix is plain text, so `Skill(deploy *)` also covers `deploy-all`. This comes from the matching code in the binary, not from a run. That code may also match a plugin skill by its unprefixed name, which `agt-env` doesn't check.
- A hook in a project's settings, or in a project-scoped plugin, waits for the workspace trust, like approvals in those settings. `claude -p` skips the trust dialog, so this couldn't be confirmed, and `agt-env` shows `pending`.

## Not covered by `agt-env`

- Synced skills and plugins, and `.mcpb` bundles in a plugin's `mcpServers`.
- Parent and nested project directories, because `agt-env` treats the current directory as the project root. Only the `.claude.json` entry follows the repository's root.
- Built-in skills, agents, and commands, and claude.ai connectors, which aren't in local config.
- Hooks in skill and agent frontmatter, which only run with that skill or agent.
- `--agents`, `--add-dir`, and other per-session sources.
