# Codex

Tested version: 0.166.0. See [evidence labels](README.md#evidence-labels).

## Official documentation

- [Build skills](https://learn.chatgpt.com/docs/build-skills)
- [Config reference](https://learn.chatgpt.com/docs/config-file/config-reference)
- [Advanced config](https://learn.chatgpt.com/docs/config-file/config-advanced)
- [MCP](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)
- [Plugins](https://learn.chatgpt.com/docs/plugins)

These pages are long, and parts of the config, MCP, and plugin pages were read through summaries rather than verbatim. A rule labelled **Tested** may therefore be documented somewhere we didn't read closely.

## Configuration

| Rule | Evidence |
|---|---|
| State and config live in `~/.codex`, moved by `CODEX_HOME` | Doc |
| User config is `config.toml` there | Doc + Tested |
| Project config is `.codex/config.toml`, and loads only in a trusted project | Doc + Tested |
| Trust is `projects."<path>".trust_level` in user config, either `trusted` or `untrusted` | Doc + Tested |

## Skills

| Rule | Evidence |
|---|---|
| User skills are `~/.agents/skills` | Doc + Tested |
| Project skills are `.agents/skills`, in each directory from the current one up to the repository root | Doc + Tested for the current directory |
| Admin skills are `/etc/codex/skills` | Doc |
| A skill is a directory with `SKILL.md`, and symlinked skill folders are followed | Doc + Tested |
| Duplicate names aren't merged, and all of them load | Doc + Tested |
| `[[skills.config]]` with `enabled = false` disables a skill | Doc + Tested |

## MCP servers

| Rule | Evidence |
|---|---|
| Servers are `[mcp_servers.<name>]` in `config.toml` | Doc + Tested |
| `command` means stdio, and `url` means streamable HTTP | Doc + Tested |
| `enabled = false` turns a server off | Doc + Tested |

## Plugins

| Rule | Evidence |
|---|---|
| Enablement is `[plugins."name@marketplace"] enabled = true` | Doc |

## Findings not in the documentation

- **More skill locations:** `$CODEX_HOME/skills` and a project's `.codex/skills` are both read, though the docs only list `.agents/skills`.
- **Trust doesn't gate skills:** project skills in both `.agents/skills` and `.codex/skills` loaded with no trust entry, even though project config did not.
- **Bundled skills:** these are extracted to `$CODEX_HOME/skills/.system` and reported with `system` scope.
- **Discovery depth:** it recurses six levels below a root, and goes inside other skills. A skill nested in a skill also loads.
- **Hidden directories:** these are skipped, apart from `.system`.
- **Frontmatter:** `description` is required. Without it, or without frontmatter at all, Codex reports an error and doesn't load the skill. `name` defaults to the directory name.
- **Lenient YAML:** `name: [unclosed` loaded with that literal text as the name.
- **Disabling a skill:** the docs conflict on whether `path` names the file or the folder. Only the `SKILL.md` path works, and a folder path is ignored.
- **Same skill through two roots:** it loads once, as a user skill, when linked from both user and project roots.
- **Broken links:** dangling symlinks are silently ignored, with no error reported. `agt-view` reports them as `broken`.
- **Invalid servers:** an entry with neither `command` nor `url` makes Codex reject the entire config file.
- **Missing `CODEX_HOME`:** `codex app-server` exited when the directory didn't exist.
- **Listing commands:** `codex mcp list --json` works, and `codex app-server` offers `skills/list` and `plugin/list`. The docs don't mention them.
- **Plugin files:** `plugins/cache/<marketplace>/<name>/<version>` holds plugins, with a manifest at `.codex-plugin/plugin.json`. This was seen on a real install, not confirmed by a test.
- **Cached plugins:** the cache can hold plugins with no config entry, so being cached doesn't show that a plugin is enabled.

## Assumptions

- Plugin status relies on the documented config key and the cache layout above. `plugin/list` returned nothing without a marketplace, so plugins couldn't be compared with the real agent.
- A plugin marked enabled with no cached copy is reported as `broken`.
- `/etc/codex/skills` wasn't tested, to avoid writing outside the temporary `HOME`.

## Not covered by `agt-view`

- Skills and MCP servers bundled in plugins.
- Parent project directories, because `agt-view` treats the current directory as the project root.
- Plugins available only through an account.
