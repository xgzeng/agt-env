# Antigravity CLI

Tested version: 1.4.12. The binary is `agy`. See [evidence labels](README.md#evidence-labels).

The Antigravity CLI, IDE, and App are separate products that share some files. This page covers the CLI.

## Official documentation

- [Getting started](https://antigravity.google/docs/getting-started)
- [Skills](https://antigravity.google/docs/skills)
- [MCP](https://antigravity.google/docs/mcp)
- [Plugins](https://antigravity.google/docs/plugins)
- [Rules](https://antigravity.google/docs/rules)
- [CLI reference](https://antigravity.google/docs/cli/reference)
- [Migrating from Gemini CLI](https://antigravity.google/docs/cli/gcli-migration)

## How this was tested

`agy mcp list` and `agy plugin list` work without signing in, so they're compared automatically in a temporary `HOME`. So are `/skills`, `/agents`, `/hooks`, and `/plugin list`: in print mode, `agy -p "/skills" --output-format json` answers without signing in or starting a turn. `/mcp` isn't available that way, so workspace servers were checked once by hand in a signed-in session.

Print mode doesn't apply workspace trust, so trust is compared with `agy agents`, which does.

`agy` writes to its own history and state files during a session, so the hand check needs a real `HOME` and leaves entries in them. `--settings` with `trustedWorkspaces` avoids saving trust to `settings.json`.

## Configuration

| Rule | Evidence |
|---|---|
| CLI settings are `~/.gemini/antigravity-cli/settings.json` | Doc + Tested |
| Shared config is `~/.gemini/config` | Doc + Tested |
| `--settings` applies settings for one session, without saving them | Doc + Tested |
| No environment variable to move these directories is documented | Doc |

## Skills

| Rule | Evidence |
|---|---|
| Global skills are `~/.gemini/antigravity-cli/skills/<name>/` | Doc |
| Workspace skills are `.agents/skills/<name>/`, and `.agent/skills`, `_agents/skills`, and `_agent/skills` work too | Doc + Tested |
| On a name clash, from highest: what a workspace's `skills.json` declares, then `.agent`, `.agents`, `_agent`, `_agents`, then what `~/.gemini/config/skills.json` declares, then `~/.gemini/antigravity-cli`, `~/.gemini/config`, and `~/.gemini/skills` | Tested, contradicts Doc for declared skills |
| Within one source, the first by directory name wins. So unnamed skills, all called `SKILL`, collide | Tested |
| `~/.gemini/skills` is read | Tested |
| Plugin skills are listed as `plugin:skill` | Tested |

## MCP servers

| Rule | Evidence |
|---|---|
| Global servers are `mcpServers` in `~/.gemini/config/mcp_config.json` | Doc + Tested |
| Workspace servers are `mcpServers` in `.agents/mcp_config.json` | Doc + Tested |
| `command` means stdio, and `serverUrl` or `url` means streamable HTTP | Doc + Tested |
| `disabled: true` turns a server off | Doc + Tested |
| With both `command` and a URL, it's an HTTP server | Tested |
| The other workspace directories also hold `mcp_config.json`, and a workspace's server overrides a same-named global one | Doc |
| A plugin's are in its `mcp_config.json`, and load only with the plugin. A bad entry is listed but can't start | Doc + Tested |

## Plugins

| Rule | Evidence |
|---|---|
| Global plugins are in `~/.gemini/config/plugins/<name>/` | Doc + Tested |
| Workspace plugins are in `.agents/plugins/<name>/` | Doc + Tested |
| The manifest is `plugin.json`, `.antigravity-plugin/plugin.json`, or `.gemini-plugin/plugin.json`. `.claude-plugin` and `gemini-extension.json` don't count | Doc + Tested |
| `"disabled": true` in the manifest ships a plugin turned off, unless `config.json` turns it on | Doc + Tested |
| `config.json` can turn off a workspace plugin as it does a global one | Tested |
| A directory without a manifest isn't a plugin | Tested |
| A plugin's skills are in `skills/`, one level deep, including hidden directories | Tested |
| The manifest's `skills`, a path or a list, adds to `skills/`, and may point outside the plugin | Tested |
| A `SKILL.md` at the plugin root doesn't load | Tested |
| A plugin skill follows the same rules as others: named by `name`, or `SKILL` without one, and it needs frontmatter | Tested |

## Subagents

| Rule | Evidence |
|---|---|
| Agents are `<name>.md` or `<name>/agent.md`, one level down, in `~/.gemini/config/agents` and `.agents/agents` | Doc + Tested |
| `name` and `description` are required, and without them the file isn't loaded. `agt-env` reports it as `broken` | Doc + Tested |
| A plugin's are in its `agents/`, by bare name, and only load with an enabled plugin | Doc + Tested |
| A plugin's root `agents.json` declares more, with paths from the plugin | Tested |
| An agent with no body still loads | Tested |
| A workspace agent overrides a same-named global one. Among the global directories, `agt-env` assumes the order skills use | Doc |

## Hooks

| Rule | Evidence |
|---|---|
| Hooks are in `~/.gemini/config/hooks.json`, each workspace directory's `hooks.json`, and a plugin's `hooks.json` | Doc + Tested |
| Top-level keys are hook names, each with its events. `enabled: false` turns one off | Doc + Tested |
| `SessionStart`, `PreInvocation`, `PostInvocation`, and `Stop` take handlers directly. `PreToolUse` and `PostToolUse` group them under `matcher` and `hooks` | Doc + Tested |
| A hook in the wrong shape, or for an unknown event, is listed but runs nothing. `agt-env` shows it as `broken` | Tested |
| A handler of an unknown type, or an event that isn't a list, drops the whole file | Tested |
| A hook name used twice keeps the later one loaded: `_agents`, `_agent`, `.agents`, then `.agent` lose to the global file, which loses to a plugin's | Tested for pairs of these |
| Hooks in `~/.gemini/antigravity-cli/settings.json` aren't loaded | Doc |

## Declared configuration

`skills.json`, `plugins.json`, and `agents.json` in a workspace directory or `~/.gemini/config` add items from elsewhere.

| Rule | Evidence |
|---|---|
| `entries` is a list of `{path}` objects. Anything else in it drops the whole file | Doc + Tested |
| An entry's directory is read one level deep, or it can name one item directly | Doc + Tested |
| `include_only` names items under the entry, and can reach a nested one. `exclude`, in an entry or at the top level, skips names. Both match names exactly, not as patterns | Tested, contradicts Doc for patterns |
| `inherits` reads another file of the same kind, which an `exclude` there can narrow | Doc + Tested |
| A relative path is from the workspace. `~/` didn't work, though the docs say it does | Tested, contradicts Doc |
| A declared plugin's skills have no `plugin:` prefix | Tested |

## Findings that differ from the documentation

| Topic | Documentation | What `agy` does |
|---|---|---|
| `~/.gemini/config/skills` | Listed for the IDE and App, not the CLI | The CLI reads it |
| Skill with no `name` | Defaults to the folder name | Named `SKILL` |
| Skill with no `description` | Required | Loads, using the first body line as its description |
| CLI plugin directory | The skills page says `~/.gemini/antigravity-cli/plugins/<name>/` | That directory isn't read, only `~/.gemini/config/plugins` |
| Server with `httpUrl` | Not supported | Listed as an enabled stdio server with no command |

## Findings not in the documentation

- **More agent locations:** `~/.gemini/antigravity-cli/agents`, `~/.gemini/agents`, and `.agent/agents` also load, and hidden names load too.
- **Workspace agents:** `agy agents` lists them only once the workspace is trusted. `agt-env` shows them as `pending` until then.
- **Plugin components:** `agy plugin list --json` reports `agents` and `hooks`. It doesn't report a plugin's `commands` or `workflows` folder.
- **Skill discovery:** it's one level deep, so nested skills don't load. Standalone Markdown files don't load either. A skill without frontmatter doesn't load, with no error shown.
- **Hidden skill directories load:** `.agents/skills/.name/SKILL.md` appears in `/skills`.
- **Built-in skills:** these are in `~/.gemini/antigravity-cli/builtin/skills`, but only some are offered, and 5 of 9 appeared in one test.
- **Plugin state:** plugins are enabled unless `~/.gemini/config/config.json` has `{"plugins": {"<name>": {"enabled": false}}}`. `agy plugin disable` writes this.
- **Plugin names:** a plugin is named after its directory, even when its manifest has a different `name`.
- **Listing commands:** `agy mcp list` and `agy plugin list` only show global items, not workspace ones. A session's `/mcp` and `/plugin` show workspace ones.
- **Plugin skill listing:** `agy plugin list --json` has each plugin's `skills`, by bare name. It still lists them for a disabled plugin, so it says what a plugin contains rather than what loads.
- **Trust prompt:** an untrusted folder gets a prompt before anything loads, and declining exits. `trustedWorkspaces` as a list of paths skips the prompt.
- **Trust and links:** the current directory's real path must be listed exactly. A listed link to it doesn't count, and nor does a trailing `/`.
- **Plugin MCP names:** `agy plugin list --json` lists a plugin's servers by bare name. The docs say tools are namespaced when needed, so a plugin's server isn't treated as clashing with a global one.
- **Blank config:** an empty `mcp_config.json` means no servers, while malformed JSON is an error.
- **Servers with no command or URL:** these are listed as enabled stdio servers but can't start. `agt-env` shows them as `broken`.
- **Sign-in message:** the startup screen briefly says "You are currently not signed in" while it checks, even when you are signed in.

## Assumptions

- A disabled plugin's skills don't load. The docs imply it, and `/skills` didn't list them.
- Workspace plugin skills follow the same layout as global ones, and wait for the workspace trust.
- Workspace servers in the other workspace directories follow the docs, because `agy mcp list` shows only global servers.

## Not covered by `agt-env`

- Built-in skills.
- `skills.json` inside a plugin, whose skills `agy` names after their path.
- Rules, and custom commands: the docs only describe built-in ones.
