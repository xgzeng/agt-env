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

`agy mcp list` and `agy plugin list` work without signing in, so they're compared automatically in a temporary `HOME`. Skills and project items only appear inside a session, so they were checked once by hand: `/skills`, `/mcp`, and `/plugin` in a signed-in session, against a scratch project.

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
| Workspace skills are `.agents/skills/<name>/`, and `.agent/skills` still works | Doc + Tested |
| Plugin skills are listed as `plugin:skill` | Tested |

## MCP servers

| Rule | Evidence |
|---|---|
| Global servers are `mcpServers` in `~/.gemini/config/mcp_config.json` | Doc + Tested |
| Workspace servers are `mcpServers` in `.agents/mcp_config.json` | Doc + Tested |
| `command` means stdio, and `serverUrl` or `url` means streamable HTTP | Doc + Tested |
| `disabled: true` turns a server off | Doc + Tested |

## Plugins

| Rule | Evidence |
|---|---|
| Global plugins are in `~/.gemini/config/plugins/<name>/` | Doc + Tested |
| Workspace plugins are in `.agents/plugins/<name>/` | Doc + Tested |
| The manifest is `plugin.json` or `.antigravity-plugin/plugin.json` | Doc + Tested |
| A directory without a manifest isn't a plugin | Tested |
| A plugin's skills are in `skills/`, one level deep, including hidden directories | Tested |
| The manifest's `skills`, a path or a list, adds to `skills/`, and may point outside the plugin | Tested |
| A `SKILL.md` at the plugin root doesn't load | Tested |
| A plugin skill follows the same rules as others: named by `name`, or `SKILL` without one, and it needs frontmatter | Tested |

## Subagents

| Rule | Evidence |
|---|---|
| Agents are `<name>.md` or `<name>/agent.md`, one level down, in `~/.gemini/config/agents` and `.agents/agents` | Doc + Tested |
| `name` and `description` are required, and without them the file isn't loaded. `agt-view` reports it as `broken` | Doc + Tested |
| A plugin's are in its `agents/`, by bare name, and only load with an enabled plugin | Doc + Tested |

## Hooks

| Rule | Evidence |
|---|---|
| Hooks are in `~/.gemini/config/hooks.json` and `.agents/hooks.json`, and a plugin's `hooks.json` | Doc, Tested for plugins |
| Top-level keys are hook names, each with its events. `enabled: false` turns one off | Doc |
| Hooks in `~/.gemini/antigravity-cli/settings.json` aren't loaded | Doc |

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
- **Workspace agents:** `agy agents` lists them only once the workspace is trusted. `agt-view` shows them as `pending` until then.
- **Same-named agents:** the listing shows one name once. Which one wins isn't known, so nothing is flagged.
- **Plugin components:** `agy plugin list --json` reports `agents` and `hooks`. It doesn't report a plugin's `commands` or `workflows` folder.
- **`~/.gemini/skills`:** `/skills` names this as the "Shared" location. No skills were placed there, so we haven't confirmed that it loads them.
- **Skill discovery:** it's one level deep, so nested skills don't load. Standalone Markdown files don't load either. A skill without frontmatter doesn't load, with no error shown.
- **Hidden skill directories load:** `.agents/skills/.name/SKILL.md` appears in `/skills`.
- **Built-in skills:** these are in `~/.gemini/antigravity-cli/builtin/skills`, but only some are offered, and 5 of 9 appeared in one test.
- **Plugin state:** plugins are enabled unless `~/.gemini/config/config.json` has `{"plugins": {"<name>": {"enabled": false}}}`. `agy plugin disable` writes this.
- **Plugin names:** a plugin is named after its directory, even when its manifest has a different `name`.
- **Listing commands:** `agy mcp list` and `agy plugin list` only show global items, not workspace ones. A session's `/mcp` and `/plugin` show workspace ones.
- **Plugin skill listing:** `agy plugin list --json` has each plugin's `skills`, by bare name. It still lists them for a disabled plugin, so it says what a plugin contains rather than what loads.
- **Trust prompt:** an untrusted folder gets a prompt before anything loads, and declining exits. `trustedWorkspaces` as a list of paths skips the prompt.
- **Blank config:** an empty `mcp_config.json` means no servers, while malformed JSON is an error.
- **Servers with no command or URL:** these are listed as enabled stdio servers but can't start. `agt-view` shows them as `broken`.
- **Sign-in message:** the startup screen briefly says "You are currently not signed in" while it checks, even when you are signed in.

## Assumptions

- Precedence between same-named skills or servers isn't known, because the listings that show them need a signed-in session. `agt-view` never reports these as `shadowed`.
- `~/.gemini/skills` loads skills like the other user directories, based only on `/skills` naming it.
- User-level and workspace hooks can't be compared, because no listing shows them and running them needs a session. Their rows follow the docs.
- `config.json` can disable a workspace plugin as it does a global one.
- A disabled plugin's skills don't load. The docs imply it, and the listing that would show it needs a signed-in session.
- Workspace plugin skills follow the same layout as global ones, and wait for the workspace trust.

## Not covered by `agt-view`

- Built-in skills, and MCP servers bundled in plugins.
- Rules, and custom commands: the docs only describe built-in ones.
