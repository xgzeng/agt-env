# Antigravity CLI

Tested version: 1.4.11. The binary is `agy`. See [evidence labels](README.md#evidence-labels).

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

## Findings that differ from the documentation

| Topic | Documentation | What `agy` does |
|---|---|---|
| `~/.gemini/config/skills` | Listed for the IDE and App, not the CLI | The CLI reads it |
| Skill with no `name` | Defaults to the folder name | Named `SKILL` |
| Skill with no `description` | Required | Loads, using the first body line as its description |
| CLI plugin directory | The skills page says `~/.gemini/antigravity-cli/plugins/<name>/` | That directory isn't read, only `~/.gemini/config/plugins` |
| Server with `httpUrl` | Not supported | Listed as an enabled stdio server with no command |

## Findings not in the documentation

- **`~/.gemini/skills`:** `/skills` names this as the "Shared" location. No skills were placed there, so we haven't confirmed that it loads them.
- **Skill discovery:** it's one level deep, so nested skills don't load. Standalone Markdown files don't load either. A skill without frontmatter doesn't load, with no error shown.
- **Hidden skill directories load:** `.agents/skills/.name/SKILL.md` appears in `/skills`.
- **Built-in skills:** these are in `~/.gemini/antigravity-cli/builtin/skills`, but only some are offered, and 5 of 9 appeared in one test.
- **Plugin state:** plugins are enabled unless `~/.gemini/config/config.json` has `{"plugins": {"<name>": {"enabled": false}}}`. `agy plugin disable` writes this.
- **Plugin names:** a plugin is named after its directory, even when its manifest has a different `name`.
- **Listing commands:** `agy mcp list` and `agy plugin list` only show global items, not workspace ones. A session's `/mcp` and `/plugin` show workspace ones.
- **Trust prompt:** an untrusted folder gets a prompt before anything loads, and declining exits. `trustedWorkspaces` as a list of paths skips the prompt.
- **Blank config:** an empty `mcp_config.json` means no servers, while malformed JSON is an error.
- **Servers with no command or URL:** these are listed as enabled stdio servers but can't start. `agt-view` shows them as `broken`.
- **Sign-in message:** the startup screen briefly says "You are currently not signed in" while it checks, even when you are signed in.

## Assumptions

- Precedence between same-named skills or servers isn't known, because the listings that show them need a signed-in session. `agt-view` never reports these as `shadowed`.
- `~/.gemini/skills` loads skills like the other user directories, based only on `/skills` naming it.
- `config.json` can disable a workspace plugin as it does a global one.

## Not covered by `agt-view`

- Built-in skills, and skills and MCP servers bundled in plugins.
- Rules, agents, and hooks.
