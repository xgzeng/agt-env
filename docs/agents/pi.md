# Pi

Tested version: 1.4.0. See [evidence labels](README.md#evidence-labels).

## Official documentation

The docs are in the [Pi repository](https://github.com/earendil-works/pi), and a copy ships with each install under `docs/`:

- [Skills](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/skills.md)
- [Extensions](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/extensions.md)
- [Packages](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/packages.md)
- [Settings](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/settings.md)
- [Configuration](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/configuration.md)
- [MCP](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/mcp.md)
- [Security and project trust](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/security.md)

## Configuration

| Rule | Evidence |
|---|---|
| The agent directory is `~/.pi/agent`, moved by `PI_CODING_AGENT_DIR` | Doc |
| User settings are `settings.json` there, and project settings are `.pi/settings.json` | Doc + Tested |
| Project settings override user settings, and resource lists are combined | Doc |
| Resource lists take paths, `!glob` to exclude, `+path` to include, and `-path` to exclude | Doc, Tested for `!glob` |
| An exclusion only applies to skills and extensions of the scope whose settings list it | Tested |
| `!**/name/**` excludes a skill, because it also matches the file inside | Tested |

## Project trust

| Rule | Evidence |
|---|---|
| Trust protects `.pi/settings.json`, `.pi/mcp.json`, `.pi/extensions`, `.pi/skills`, and project `.agents/skills` | Doc + Tested |
| A saved decision for the current directory or the closest parent applies first | Doc + Tested for the current directory |
| Without a saved decision, `defaultProjectTrust` applies: `ask`, `always`, or `never` | Doc + Tested |
| Non-interactive modes skip protected resources on `ask` or `never` | Doc + Tested |
| Saved decisions are in `trust.json` in the agent directory, using canonical paths | Doc + Tested |

## Skills

| Rule | Evidence |
|---|---|
| Sources are `<agent-dir>/skills`, `~/.agents/skills`, `.pi/skills`, and `.agents/skills` | Doc + Tested |
| Project `.agents/skills` is searched from the current directory up to the repository root | Doc |
| Directories containing `SKILL.md` are discovered recursively | Doc + Tested |
| Malformed `SKILL.md` files and skills without descriptions aren't loaded | Doc + Tested |
| A name needn't match its directory | Doc + Tested |
| On a name conflict, one skill is kept, with a warning | Doc |
| Precedence from highest is project-listed paths, `.pi/skills`, project `.agents/skills`, user-listed paths, `<agent-dir>/skills`, then `~/.agents/skills` | Tested |
| Skills conflict by `name`, not directory. Within one source, the later directory wins | Tested |
| An invalid or excluded skill doesn't take the name, so the next one loads | Tested |
| The `skills` setting adds paths and exclusions | Doc, Tested for exclusions |
| Some standalone Markdown skills are accepted | Doc + Tested |

## MCP servers

| Rule | Evidence |
|---|---|
| Servers are `mcpServers` in `<agent-dir>/mcp.json` and `.pi/mcp.json` | Doc + Tested |
| `command` means stdio, and `url` means streamable HTTP | Doc + Tested |
| `enabled: false` keeps an entry without connecting to it | Doc + Tested |
| An entry with neither `command` nor `url` is a config error | Doc + Tested |
| A project entry with no `command`, `url`, or `type` only overrides `enabled`, `exposure`, and `toolExposure` of the same-named user server | Doc |
| A project entry with a transport replaces the whole user entry, even when it has `enabled: false`, and doesn't inherit `enabled` from it | Tested |
| `-builtin:mcp` in the `extensions` setting turns off built-in MCP support | Doc |

## Extensions and packages

| Rule | Evidence |
|---|---|
| Extensions are `*.ts` or `*.js`, `*/index.ts` or `index.js`, or a directory whose `package.json` has `pi.extensions`, under `<agent-dir>/extensions` and `.pi/extensions` | Doc + Tested |
| Discovery goes one level deep | Doc |
| The `extensions` setting adds paths and exclusions | Doc, Tested for exclusions |
| Project extensions load only after trust | Doc + Tested |
| The `packages` setting lists npm, git, or local sources, as strings or `{source, ...}` objects | Doc, Tested for a local string |

## Findings not in the documentation

- **`trust.json` format:** a JSON object mapping canonical paths to `true` or `false`. The format comes from the code inside the Pi binary. A `true` or `false` value written to the file then changed what Pi loaded.
- **`disabled` is ignored:** `disabled: true` on a server has no effect, and Pi still tries to connect.
- **Where standalone skills work:** `*.md` directly under `<agent-dir>/skills` loads. The docs don't say where these are accepted.
- **`pi mcp list` and trust:** it reported a project as untrusted even with `defaultProjectTrust: "always"`, while RPC mode loaded that project's skills and extensions. So `pi mcp list` can't confirm project servers through that setting.
- **A missing package blocks startup:** with an uninstallable npm package in settings, `pi list` and RPC mode failed with the npm error. The docs say Pi warns, skips the package, and keeps running.
- **Listing through RPC:** `{"type": "get_commands"}` in `--mode rpc` lists skills as `skill:<name>`, and commands that extensions register. An extension that registers no command doesn't appear in it.

## Assumptions

- Standalone Markdown skills in `.pi/skills` work as they do in `<agent-dir>/skills`, and those in `.agents/skills` don't load. Neither was tested.

## Not covered by `agt-view`

- Skills and extensions contributed by Pi packages.
- Skills and servers that extensions add at runtime.
- Built-in extensions such as `builtin:mcp`, other than noticing when MCP is turned off.
- Parent project directories, because `agt-view` treats the current directory as the project root.
