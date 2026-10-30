# OpenCode

Tested version: 2.2.2. See [evidence labels](README.md#evidence-labels).

## Official documentation

OpenCode has two sets of docs, and the unversioned ones describe V1. Use the V2 pages for current versions.

**V2:**
- [Config](https://opencode.ai/v2/docs/config)
- [Skills](https://opencode.ai/v2/docs/skills)
- [MCP servers](https://opencode.ai/v2/docs/mcp-servers/)
- [Permissions](https://opencode.ai/v2/docs/permissions/)
- [Plugins](https://opencode.ai/v2/docs/plugins)
- [Building plugins](https://opencode.ai/v2/docs/build/plugins/)
- [Migrating from V1](https://opencode.ai/v2/docs/migrate-v1/)

**V1:**
- [Config](https://opencode.ai/docs/config/)
- [Skills](https://opencode.ai/docs/skills/)
- [MCP servers](https://opencode.ai/docs/mcp-servers/)
- [Plugins](https://opencode.ai/docs/plugins/)

## Configuration

| Rule | Evidence |
|---|---|
| Global config is `~/.config/opencode/opencode.json` or `.jsonc` | Doc + Tested |
| Project config is `opencode.json(c)` and `.opencode/opencode.json(c)` | Doc + Tested |
| Project config is searched from the current directory to the filesystem root | Doc |
| A `.opencode` config overrides every direct config, and managed config overrides everything | Doc |
| Managed config is in `/etc/opencode` on Linux and `/Library/Application Support/opencode` on macOS | Doc |
| V1 config keeps working and is converted in memory | Doc + Tested |

## Skills

| Rule | Evidence |
|---|---|
| Sources are `~/.config/opencode/skills`, `~/.claude/skills`, `~/.agents/skills`, `.opencode/skills`, `.claude/skills`, and `.agents/skills` | Doc + Tested |
| Project sources are searched from the current directory up to the project root | Doc |
| A `skills` config array adds sources, with relative paths resolved from the working directory | Doc + Tested |
| Precedence from lowest is built-in, `.claude/skills`, `.agents/skills`, the global directory, `.opencode/skills`, then the config's `skills` entries | Doc |
| `SKILL.md` can be nested in grouping directories | Doc + Tested |
| Search stops six directories below the source root | Doc + Tested |
| A directory containing `SKILL.md` is one skill, and isn't searched for more | Doc + Tested |
| Hidden files and directories and `node_modules` are skipped | Doc + Tested |
| Markdown files count as skills only at a source root | Doc + Tested |
| All frontmatter is optional. Names aren't validated and needn't match the directory | Doc + Tested |
| Skills without a description aren't advertised to the model, but they still exist | Doc, Tested that they're listed |
| `.opencode/skill/` works as well as `.opencode/skills/` | Doc |

Permissions control skills:

| Rule | Evidence |
|---|---|
| V2 uses `permissions: [{action, resource, effect}]`, with `action: "skill"` and the skill's ID as the resource | Doc |
| The last matching rule wins | Doc |
| With no matching rule, the effect is `ask` | Doc |
| `deny` hides a skill from the model and rejects loading | Doc |

## MCP servers

| Rule | Evidence |
|---|---|
| V2 servers are `mcp.servers.<name>`, and V1 servers are `mcp.<name>` | Doc + Tested |
| `type` is required, either `local` with a `command` array, or `remote` with a `url` | Doc + Tested |
| `disabled: true` turns off a V2 server, and it defaults to false | Doc + Tested |
| A V2 server's `enabled` field is ignored | Doc + Tested |
| V1's `enabled: false` still works | Doc + Tested |
| An `enabled`-only V1 entry without a `type` is ignored with a warning | Doc + Tested |
| A higher-precedence entry replaces the whole server object, without merging fields | Doc |

## Plugins

| Rule | Evidence |
|---|---|
| Listed plugins are the `plugins` array in V2, or `plugin` in V1 | Doc + Tested |
| Entries can be package names, local paths, `file://` URLs, or `{package, options}` objects | Doc, Tested except for packages |
| Configured local paths must be directories, and a single file is skipped | Doc + Tested |
| Auto-discovery under `plugins/` loads direct `.ts` and `.js` files and immediate package directories | Doc + Tested |
| A package directory uses `package.json` exports, or a root `server` or `index` file | Doc, Tested for `index.ts` |
| V1 plugin implementations don't run in V2 | Doc + Tested |
| A `-` prefix on an ID or wildcard disables a plugin | Doc + Tested |

## Findings not in the documentation

- **Skill IDs:** a skill's ID is its directory or file name, without any parent path. `skills/group/nested/SKILL.md` has the ID `nested`, and the `name` field only changes the display name. Permission rules are documented to match IDs.
- **What "six directories" counts:** `SKILL.md` can be at most five directories below a source. The file itself is the sixth level.
- **Standalone skills in every source:** root `*.md` files load from `.claude/skills`, `.agents/skills`, and the `skills` config too. A nested `.md` file doesn't.
- **The `skills` config:** a path entry can be a directory of skills or one skill directory. The V1 form `{"paths": [...]}` still works.
- **Singular directories globally:** `~/.config/opencode/skill/` and `~/.config/opencode/plugin/` both work. The docs only mention the project forms.
- **`.mjs` plugins:** these aren't loaded. The docs just don't mention `.mjs`.
- **Compatibility directories nest too:** `~/.claude/skills/<group>/<skill>/SKILL.md` loads. So skills under Claude Code's `skills/synced` directory can appear in OpenCode, though Claude Code's own layout differs.
- **Denied skills stay in the API:** a denied skill still appears in `/api/skill`, so that list doesn't reflect permissions.
- **Plugin removal order:** `plugins` entries apply in order, after auto-discovered plugins. A removal affects what came before it, so a later entry can re-add a plugin.
- **Plugin removal patterns:** a removal matches an exact ID, `*`, or `prefix.*`. `-id-*` does nothing, and neither does a file name such as `-local`, because IDs are defined in the plugin's code.
- **Removing built-ins:** `-*` also removes built-in plugins. `-opencode.config.skill` makes all skills on disk disappear, `-opencode.config.mcp` does the same for servers, and `-opencode.skill` only removes built-in skills. `-opencode.config.compatibility` drops the `.claude/skills` and `.agents/skills` sources at user and project level, and keeps OpenCode's own.
- **Plugin load failures:** `/api/plugin` shows a failed plugin as `state.status: "failed"`, and a missing configured directory fails the same way. A configured single file is skipped without appearing at all.
- **Invalid V2 entries are dropped:** a server with an unknown `type` doesn't appear in `/api/mcp`. `agt-view` shows it as `broken`.
- **Server API:** `/api/skill`, `/api/mcp`, `/api/plugin`, and `/api/config` need Basic auth, with user `opencode` and the password from `service.json`. The `x-opencode-directory` header selects the project. A `?directory=` query is ignored, and answers for the service's own directory.
- **`opencode mcp list` reported "No MCP servers configured"** while `/api/mcp` listed the same servers, so it can't be used as a check.
- **Background service:** most CLI commands start a service on a fixed port (49374 by default). A service for another `HOME` on that port makes them fail until you run `opencode service set port <port>`. It also needs a moment to load config after starting.
- **Environment variables seen only in the binary:** `OPENCODE_DISABLE_CLAUDE_CODE`, `OPENCODE_DISABLE_CLAUDE_CODE_SKILLS`, and `OPENCODE_DISABLE_PROJECT_CONFIG` appear in the binary, but not in the pages we read.

## How `agt-view` handles what it can't observe

- **Plugin IDs:** these are found by reading `id: "..."` from the plugin's source, without running it. If a removal is configured and a plugin's ID can't be read, that plugin is `unknown`.
- **Package plugins:** these are always `unknown`, because they install at startup, which can fail. They weren't tested, to avoid network installs.
- **Plugin load failures:** a plugin whose code fails when loaded is shown as `enabled`, because detecting that means running it.
- **Package-directory plugins:** a directory with `package.json` is accepted when it has `name` and `exports`, as documented. Only `index.ts` directories were tested.

## Assumptions

- `XDG_CONFIG_HOME` moves the global config directory. The V2 docs state this only for `cli.json`.
- `opencode.json` and `opencode.jsonc` in one directory are both read, with `.jsonc` applied later. Testing confirmed both are read, but not which wins.
- The `OPENCODE_DISABLE_*` variables above do what their names say. `agt-view` honors them, but their effect wasn't tested.
- `plugin` (V1) entries apply before `plugins` (V2) within one config file.

## Not covered by `agt-view`

- Built-in skills and plugins.
- Parent project directories, because `agt-view` treats the current directory as the project root.
- Remote config and `OPENCODE_CONFIG_CONTENT`.
- Skills from a URL in the `skills` config.
- `policies`, which can hard-deny after permission rules.
