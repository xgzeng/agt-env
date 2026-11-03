# Pi

Tested version: 1.5.0. See [evidence labels](README.md#evidence-labels).

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
| A `~` at the start of a listed path or local package means the home directory | Source |
| Corrupt user settings don't stop Pi, and without them it doesn't load the project | Tested |
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
| Directories containing `SKILL.md` are discovered recursively, but not inside another skill | Doc + Tested |
| Malformed `SKILL.md` files and skills without descriptions aren't loaded | Doc + Tested |
| A name needn't match its directory | Doc + Tested |
| On a name conflict, one skill is kept, with a warning | Doc |
| Precedence from highest is project-listed paths, `.pi/skills`, project `.agents/skills`, user-listed paths, `<agent-dir>/skills`, then `~/.agents/skills` | Tested |
| Skills conflict by `name`, not directory. Within one source, the later directory wins | Tested |
| An invalid or excluded skill doesn't take the name, so the next one loads | Tested |
| The `skills` setting adds paths and exclusions | Doc, Tested for exclusions |
| `*.md` directly in `<agent-dir>/skills` and `.pi/skills` are skills, and not below them | Doc, Tested for `<agent-dir>/skills` |
| In `.agents/skills` it's the reverse: `*.md` below the root are skills, at any depth, and not directly in it. Without a description they're silently skipped | Tested |
| A standalone skill without `name` takes its directory's name, not its own, so several in one directory collide | Tested |
| `.gitignore`, `.ignore`, and `.fdignore` at any level hide skills | Tested |

## MCP servers

| Rule | Evidence |
|---|---|
| Servers are `mcpServers` in `<agent-dir>/mcp.json` and `.pi/mcp.json` | Doc + Tested |
| `command` means stdio, and `url` means streamable HTTP. With both, `url` wins unless `type` says otherwise | Doc + Tested |
| `type: "sse"` is refused, as is a `url` that isn't http or https | Tested |
| Names may only use letters, digits, `_`, and `-` | Tested |
| Two names that differ only in `-` and `_` clash, and the later one is refused | Tested |
| `auth` is refused in a project's `mcp.json` | Source |
| `enabled: false` keeps an entry without connecting to it | Doc + Tested |
| An entry with neither `command` nor `url` is a config error | Doc + Tested |
| A project entry with no `command`, `url`, or `type` only overrides `enabled`, `exposure`, and `toolExposure` of the same-named user server. Other keys make it an error | Doc, Source for the error |
| A project entry with a transport replaces the whole user entry, even when it has `enabled: false`, and doesn't inherit `enabled` from it | Tested |
| `-builtin:mcp`, `!builtin:mcp`, or a glob like `!builtin:*` in the `extensions` setting turns off built-in MCP support | Doc + Tested |
| A trusted project's matching entry decides over the user's, in either direction | Doc + Tested |

## Extensions and packages

| Rule | Evidence |
|---|---|
| Extensions are `*.ts` or `*.js`, `*/index.ts` or `index.js`, or a directory whose `package.json` has `pi.extensions`, under `<agent-dir>/extensions` and `.pi/extensions` | Doc + Tested |
| Discovery goes one level deep | Doc |
| A directory's `pi.extensions` lists its files, and those that exist load, instead of its index file. If none exist, the index file is used | Tested |
| Same-named extensions in different places all load | Tested |
| `npm:` and `git:` entries in the `extensions` setting are ignored. Only `packages` installs those | Tested |
| The `extensions` setting adds paths and exclusions | Doc, Tested for exclusions |
| Project extensions load only after trust | Doc + Tested |
| The `packages` setting lists npm, git, or local sources, as strings or `{source, ...}` objects | Doc + Tested |
| Installed npm packages are in `npm/node_modules/<name>` and git packages in `git/<host>/<path>`, under the agent directory or `.pi`, by the scope of the setting | Tested |
| Local sources are relative to the settings file's directory | Doc + Tested |
| A git source can be `git:host/path`, `git:user@host:path`, `https://`, `git://`, or `ssh://`. A `@ref` and `.git` aren't part of the path. `user@host:path` without `git:` isn't a git source | Tested |
| Skills are in the package's `skills/`, with the same rules as other skills. With a `pi` key in `package.json` that lists any resource, only `pi.skills` counts. A `pi` key with only other things, such as `changelogPath`, changes nothing | Doc + Tested |
| `pi.skills` entries are globs relative to the package, and `!` excludes. An exclusion matches like other patterns, so `!./res/b` excludes nothing and `!**/b/**` works | Doc + Tested |
| An entry's own `skills` filter narrows the package: `[]` loads none, `!` excludes, and plain patterns select | Doc + Tested |
| The `skills` setting's exclusions don't apply to package skills | Tested |
| A trusted project's entry replaces a personal entry for the same package, unless it has `autoload: false`. An untrusted one doesn't | Doc + Tested |
| Sources name the same package when they have the same npm name, git host and path, or resolved local path | Source |
| Package skills keep their names, with no prefix | Tested |
| Package skills lose to every other skill source, and a project's package beats a personal one. The first listed package wins, and within a package the later directory wins | Tested |

## Prompt templates

Pi's custom commands are prompt templates. Pi has no hook config or custom agents: extensions add such things in code, which `agt-env` doesn't run.

| Rule | Evidence |
|---|---|
| Templates are `*.md` directly in `<agent-dir>/prompts` and `.pi/prompts`. Folders there aren't searched | Doc + Tested |
| The `prompts` setting adds files and directories, and a directory there is searched recursively | Doc + Tested |
| The file name is the command name, and the description falls back to the first line, cut to 60 characters with `...` | Doc + Tested |
| A project's wait for trust | Doc + Tested |
| Exclusions and package filters work as for skills | Doc + Tested |
| A package's are in `prompts/`, searched recursively, or `pi.prompts` instead | Doc + Tested |
| Precedence matches skills: project sources beat user sources, listed paths beat conventional directories, and packages lose to everything, with the first package winning | Tested |

## Findings not in the documentation

- **`trust.json` format:** a JSON object mapping canonical paths to `true` or `false`. The format comes from the code inside the Pi binary. A `true` or `false` value written to the file then changed what Pi loaded.
- **`disabled` is ignored:** `disabled: true` on a server has no effect, and Pi still tries to connect.
- **Where standalone skills work:** `*.md` directly under `<agent-dir>/skills` loads. The docs don't say where these are accepted.
- **`pi mcp list` and trust:** it reported a project as untrusted even with `defaultProjectTrust: "always"`, while RPC mode loaded that project's skills and extensions. So `pi mcp list` can't confirm project servers through that setting.
- **A package that can't install:** Pi 1.5.0 skips it and keeps running, as its docs say. In 1.4.0 it blocked startup.
- **`PI_PACKAGE_DIR`:** this moves Pi's own install assets. It doesn't move anything `agt-env` reads.
- **Listing through RPC:** `{"type": "get_commands"}` in `--mode rpc` lists skills as `skill:<name>`, and commands that extensions register. An extension that registers no command doesn't appear in it.

Rules labelled **Source** were read in [`earendil-works/pi`](https://github.com/earendil-works/pi) at tag `v1.5.0`.

## How `agt-env` reports this

- A user's package replaced by a trusted project's entry is `shadowed`.
- A directory with several extension entries has one row per file, named `<directory>/<file>`.
- A refused server is `broken`, with a finding that says why.

## Assumptions

- A package's skills are listed only if its files are on disk. A package that Pi would install at startup shows its own row with no skills.
- Standalone Markdown skills in `.pi/skills` work as they do in `<agent-dir>/skills`. This wasn't tested.

## Not covered by `agt-env`

- Extensions contributed by Pi packages, and `autoload: false` entries, which act as a filter over the personal entry.
- Skills and servers that extensions add at runtime.
- Ignore files, so a skill they hide is still listed as `enabled`.
- Windows, where trust paths compare without regard to case. The PRD puts Linux and macOS first.
- Built-in extensions such as `builtin:mcp`, other than noticing when MCP is turned off.
- Parent project directories, because `agt-env` treats the current directory as the project root.
