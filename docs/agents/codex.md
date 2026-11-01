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
| In a trusted project, a same-named entry is merged over the user's entry field by field, including `enabled`, and `env` key by key | Tested |
| A project entry with only `enabled` or `env` is valid, because it inherits the user's `command` | Tested |
| A merged entry with both `command` and `url` makes Codex reject the config | Tested |

## Plugins

| Rule | Evidence |
|---|---|
| Enablement is `[plugins."name@marketplace"] enabled = true` | Doc + Tested |
| A plugin's skills load only when its entry is enabled. A cached plugin with no entry contributes none | Tested |
| A trusted project's entry decides over the user's, in either direction. An untrusted project's doesn't count | Tested |
| Only the highest cached version counts, even if it has no valid manifest, and then the plugin contributes no skills | Tested |
| A manifest at `.codex-plugin/plugin.json` is required for skills | Tested |
| Skills are in `skills/`, or the manifest's `skills`, a path or list of `./` paths, which replaces it. If no path is valid, `skills/` is used | Tested |
| A path is invalid without `./`, when it leaves the plugin, or when it's the plugin root | Tested |
| A plugin skill's name is `<manifest name>:<name>`, using the name of the version's directory if the manifest has none | Tested |
| Plugin skills use the same rules as others: six levels deep, hidden directories skipped, and `description` required | Tested |
| `[[skills.config]]` can disable a plugin skill | Tested |
| A plugin skill and a same-named skill elsewhere both load | Tested |

## Subagents

| Rule | Evidence |
|---|---|
| Agents are TOML files in `$CODEX_HOME/agents` and `.codex/agents` | Doc |
| `name`, `description`, and `developer_instructions` are required | Doc |
| `agents.enabled = false` turns off multi-agent tools | Doc |

## Hooks

| Rule | Evidence |
|---|---|
| Hooks are in `hooks.json` and `[hooks]` in `config.toml`, in `$CODEX_HOME` and a trusted project's `.codex`. Both forms load together | Doc + Tested |
| An untrusted project's hooks don't load | Doc + Tested |
| A plugin's are in `hooks/hooks.json`, or the file its manifest's `hooks` names instead. They load only with an enabled plugin | Doc + Tested |
| A non-managed hook must be reviewed before it runs | Doc + Tested |
| `[features] hooks = false` turns all hooks off | Doc + Tested |
| `requirements.toml` can define managed hooks and `allow_managed_hooks_only` | Doc |

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
- **Broken links:** dangling symlinks are silently ignored, with no error reported. `agt-env` reports them as `broken`.
- **Invalid servers:** an entry with neither `command` nor `url` makes Codex reject the entire config file.
- **Missing `CODEX_HOME`:** `codex app-server` exited when the directory didn't exist.
- **Listing commands:** `codex mcp list --json` works, and `codex app-server` offers `skills/list`, `plugin/list`, and `hooks/list`. The docs don't mention them.
- **Hook state:** `[hooks.state."<key>"]` in `config.toml` holds `trusted_hash` and `enabled`. The key is `<file>:<event_in_snake_case>:<group>:<handler>`, or `<plugin id>:<file in plugin>:...` for a plugin's.
- **Hook fingerprint:** `trusted_hash` is `sha256:` over compact sorted JSON of `{"event_name", "matcher", "hooks": [handler]}`. A command handler has `type`, `command`, `timeout` (600 by default), `async`, and any `statusMessage`. An MCP one has `type`, `server`, `tool`, `input`, and `timeout`. `agt-env` recomputes it, so an edited hook shows as `pending`.
- **Plugin hook fingerprint:** it has a second part that changes with any file in the plugin, and a stored copy of the current value still reported `modified`. So a plugin hook with a stored fingerprint is `unknown`.
- **Invalid hook files:** a command handler with no `command` makes Codex reject that whole file with a warning. `agt-env` warns too, and lists nothing from it.
- **Handler types:** `prompt` and `agent` handlers are skipped with a warning that they aren't supported yet. `agt-env` reports them as `broken`.
- **Plugin files:** `plugins/cache/<marketplace>/<name>/<version>` holds plugins, with a manifest at `.codex-plugin/plugin.json`.
- **Version order:** versions compare as semver when both are in `x.y.z` form, and otherwise as text. So `10.0.0` beats `9.0.0`, while `1.9` beats `1.10` and `local` beats `1.0.0`.
- **Plugin skill scope:** `skills/list` calls a plugin skill `user`, even when a project's entry enabled it. `agt-env` uses the scope of the entry that enables the plugin.
- **Cached plugins:** the cache can hold plugins with no config entry, so being cached doesn't show that a plugin is enabled.

## How `agt-env` reports this

- The user's entry is `shadowed` by a trusted project's entry, even when the project's turns the server off. The project's row shows the merged result.
- Duplicate skills all load, so they're never `shadowed`.
- The user's plugin entry is `shadowed` by a trusted project's entry, even when the project's turns the plugin off.

## Assumptions

- A plugin's own row relies on the documented config key and the cache layout above. `plugin/list` returned nothing without a marketplace, so only its skills could be compared with the real agent.
- A plugin marked enabled with no cached copy is reported as `broken`.
- No listing shows custom agents, so their rows follow the docs. Project agents are assumed to wait for trust like other project config, and only files directly in `agents` are read.
- `/etc/codex/skills` wasn't tested, to avoid writing outside the temporary `HOME`.

## Not covered by `agt-env`

- MCP servers bundled in plugins, and agents in plugins.
- Custom prompts. The docs say they appear as `/prompts:<name>` without saying where they live, and no listing shows them.
- Managed hooks from `requirements.toml`.
- Parent project directories, because `agt-env` treats the current directory as the project root.
- Plugins available only through an account.
