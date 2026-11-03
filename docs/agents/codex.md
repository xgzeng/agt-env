# Codex

Tested version: 0.166.0. See [evidence labels](README.md#evidence-labels).

## Official documentation

- [Build skills](https://learn.chatgpt.com/docs/build-skills)
- [Config reference](https://learn.chatgpt.com/docs/config-file/config-reference)
- [Advanced config](https://learn.chatgpt.com/docs/config-file/config-advanced)
- [MCP](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)
- [Plugins](https://learn.chatgpt.com/docs/plugins)

These pages are long, and parts of the config, MCP, and plugin pages were read through summaries rather than verbatim. A rule labelled **Tested** may therefore be documented somewhere we didn't read closely. The `developers.openai.com/codex` addresses redirect to these pages.

Rules labelled **Source** were read in [`openai/codex`](https://github.com/openai/codex) at tag `rust-v0.166.0`.

## Configuration

| Rule | Evidence |
|---|---|
| State and config live in `~/.codex`, moved by `CODEX_HOME` | Doc |
| User config is `config.toml` there | Doc + Tested |
| Project config is `.codex/config.toml`, and loads only in a trusted project | Doc + Tested |
| Trust is `projects."<path>".trust_level` in user config, either `trusted` or `untrusted` | Doc + Tested |
| System config is `/etc/codex/config.toml`, the lowest layer. Layers merge field by field | Doc + Tested |
| Codex refuses to start if a loaded file doesn't parse, or if a field has the wrong type. So one bad server, skill rule, or `[hooks]` entry stops everything | Tested |
| An untrusted project's file can't stop it, because it isn't read | Tested |
| `/etc/codex/requirements.toml` is read too, and a corrupt one also stops it. Unknown keys there and in `config.toml` are accepted | Tested |

`agt-env` checks the sections it reads, not every field. When it finds such a problem it warns, marks what's wrong `broken`, and shows everything else as `unknown`, because nothing loads until the file is fixed.

## Skills

| Rule | Evidence |
|---|---|
| User skills are `~/.agents/skills` | Doc + Tested |
| Project skills are `.agents/skills`, in each directory from the current one up to the repository root | Doc + Tested for the current directory |
| Admin skills are `/etc/codex/skills` | Doc + Tested |
| A skill is a directory with `SKILL.md`, and symlinked skill folders are followed | Doc + Tested |
| Duplicate names aren't merged, and all of them load | Doc + Tested |
| `[[skills.config]]` turns a skill on or off, by `path` or `name`. An entry with both or neither is ignored | Doc + Tested |
| Rules apply in order and the last match wins. Only the user's config counts: rules in system and project config are ignored | Tested |
| `name` matches the skill's own name, and a plugin skill's full `plugin:skill` name | Tested |
| In `path`, `~` is the home directory, and a relative path starts at the config file's directory | Tested |
| `[skills.bundled] enabled = false` turns off bundled skills, in any loaded layer. Other system skills stay on | Doc + Tested |
| A name longer than 64 characters is refused, including a name taken from the directory | Tested |

## MCP servers

| Rule | Evidence |
|---|---|
| Servers are `[mcp_servers.<name>]` in `config.toml` | Doc + Tested |
| `command` means stdio, and `url` means streamable HTTP | Doc + Tested |
| `enabled = false` turns a server off | Doc + Tested |
| In a loaded layer, a same-named entry is merged over the one below it field by field, including `enabled`, and `env` key by key | Tested |
| A project entry with only `enabled` or `env` is valid, because it inherits the user's `command` | Tested |
| An entry with both `command` and `url`, after merging, makes Codex reject the config | Tested |
| So does an entry with neither, or a wrong type such as `enabled = "yes"` | Tested |
| Each transport refuses the other's fields, such as `args` on a `url` server | Source |

## Plugins

| Rule | Evidence |
|---|---|
| Enablement is `[plugins."name@marketplace"]`, on unless `enabled = false` | Doc + Tested |
| `[plugins._default] enabled = false` turns off every plugin without its own `enabled = true`, from any loaded layer. It isn't a plugin | Tested |
| A plugin's skills load only when it has an entry. A cached plugin with no entry contributes none | Tested |
| A trusted project's entry decides over the user's, in either direction, and without `enabled` it inherits the user's. An untrusted project's doesn't count | Tested |
| IDs split at the last `@`. The name allows letters, digits, `_`, `-`, and dots between segments, and the marketplace no dots. Others load nothing | Tested |
| `local` wins if present. Otherwise only the highest cached version counts, even if it has no valid manifest, and then the plugin contributes no skills | Tested |
| Version directories with other characters in their names, and links, are skipped | Source |
| The manifest is the first present of `.codex-plugin`, `.claude-plugin`, and `.cursor-plugin`'s `plugin.json`. A later one doesn't rescue an invalid one | Tested |
| A root `plugin.json` counts first, only if its `$schema` names the Agent Plugins format. These read only skills directly inside a skill folder, and no hooks | Tested |
| `name`, `version`, `description`, `keywords`, or `interface` of the wrong type invalidates a manifest. A bad `skills`, `hooks`, or `mcpServers` doesn't | Tested |
| Skills are in `skills/`, or the manifest's `skills`, a path or list of `./` paths, which replaces it. If no path is valid, `skills/` is used | Tested |
| A path is invalid without `./`, when it leaves the plugin, or when it's the plugin root | Tested |
| A plugin skill's name is `<manifest name>:<name>`, using the name of the version's directory if the manifest's is missing or empty | Tested |
| Plugin skills use the same rules as others: six levels deep, hidden directories skipped, and `description` required | Tested |
| A skill reachable both from a plugin and from an ordinary root loads once, as the plugin's | Tested |
| A plugin skill and a same-named skill elsewhere both load | Tested |
| Servers are in `.mcp.json`, with or without an `mcpServers` wrapper, or the manifest's `mcpServers`, a path or an object. `mcp.json` isn't read | Tested |
| A configured server beats a plugin's of the same name, and among plugins the first by ID wins | Tested |
| `[plugins."p@m".mcp_servers.<name>] enabled = false` turns one off. A bad entry costs only itself, not the file | Tested |
| An Agent Plugins `.mcp.json` must name its schema | Source |

## Subagents

| Rule | Evidence |
|---|---|
| Agents are TOML files in `agents` next to each loaded config file, at any depth | Doc + Tested |
| `[agents.<name>]` in `config.toml` also declares a role, with `description` and optionally `config_file`, resolved from the config file's directory | Tested |
| A file a role declares isn't read a second time. Its `name` and `description` override the declaration | Tested |
| A file found on its own needs `name` and `developer_instructions`. All text fields refuse a blank value | Doc + Tested |
| A role with no description is refused, but it can inherit one from a same-named role in a lower layer | Tested |
| A repeated name in one layer refuses the later one. A higher layer replaces a lower one's | Tested |
| `agents.enabled = false` turns off multi-agent tools, unless `features.multi_agent_v2` is on | Doc, Source for the exception |

These were compared using the `configWarning` messages a new thread reports, which name each refused role. They don't list the accepted ones, so a role's being usable is inferred from having no warning.

## Hooks

| Rule | Evidence |
|---|---|
| Hooks are in `hooks.json` and `[hooks]` in `config.toml`, next to each loaded config. Both forms load together | Doc + Tested |
| An untrusted project's hooks don't load | Doc + Tested |
| A plugin's are in `hooks/hooks.json`, or what its manifest's `hooks` names instead: a path, a list of paths, or inline hooks. They load only with an enabled plugin | Doc + Tested |
| A non-managed hook must be reviewed before it runs. Review state is read from the user's config | Doc + Tested |
| `[features] hooks = false` turns all hooks off, from any loaded layer. `codex_hooks` is an old name for it, and `hooks` wins if both are set | Doc + Tested |
| Hooks in the system files, and in `requirements.toml` under `[hooks]`, are managed: they need no review | Doc + Tested |
| `allow_managed_hooks_only = true` in `requirements.toml` stops user, project, and plugin hooks. In `config.toml` it means nothing | Doc + Tested, Source for `config.toml` |

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
- **Missing `CODEX_HOME`:** `codex app-server` exited when the directory didn't exist.
- **Listing commands:** `codex mcp list --json` works, and `codex app-server` offers `skills/list`, `plugin/list`, and `hooks/list`. The docs don't mention them.
- **Hook state:** `[hooks.state."<key>"]` in `config.toml` holds `trusted_hash` and `enabled`. The key is `<file>:<event_in_snake_case>:<group>:<handler>`, or `<plugin id>:<file in plugin>:...` for a plugin's, where inline hooks use `plugin.json#hooks[<n>]` as the file.
- **Hook fingerprint:** `trusted_hash` is `sha256:` over compact sorted JSON of `{"event_name", "matcher", "hooks": [handler]}`. `agt-env` recomputes it, so an edited hook shows as `pending`. It's built from normalized values:
  - A command handler has `type`, `command`, `timeout`, `async`, and any `statusMessage`. `commandWindows` isn't included.
  - `additionalContextLimit` is included only on `PreToolUse`, `PostToolUse`, `SessionStart`, `UserPromptSubmit`, and `SubagentStart`, and not at its default of 2500.
  - An MCP handler has `type`, `server`, `tool`, `input`, `timeout`, and any `statusMessage`.
  - `timeout` defaults to 600 and is at least 1. On `SessionEnd` and `Interrupt` it defaults to 1 and is held between 1 and 3.
  - `matcher` counts only on events that use one, so not on `Stop`, `UserPromptSubmit`, `Interrupt`, or the model-request events.
- **Plugin hook fingerprint:** it has a second part that changes with any file in the plugin, and a stored copy of the current value still reported `modified`. So a plugin hook with a stored fingerprint is `unknown`.
- **Invalid hook files:** a handler that can't be read makes Codex reject that whole `hooks.json` with a warning: a missing `type`, an unknown `type`, a missing `command`, `server`, or `tool`, or a field of the wrong type. `agt-env` warns too, and lists nothing from it. In `[hooks]` in `config.toml`, the same thing stops Codex starting.
- **Unknown events:** other keys under `hooks` are ignored, whatever their value.
- **Skipped handlers:** these are skipped with a warning while the rest of the file loads: `prompt` and `agent` handlers, a blank `command`, `server`, or `tool`, an MCP handler on `SessionEnd`, and all handlers under an invalid matcher. `agt-env` reports each as `broken`.
- **Plugin files:** `plugins/cache/<marketplace>/<name>/<version>` holds plugins.
- **Version order:** versions compare as semver when both are valid, and otherwise as text. So `10.0.0` beats `9.0.0`, `1.0.0-alpha.10` beats `1.0.0-alpha.2`, `1.0.0+b` beats `1.0.0`, and `1.9` beats `1.10`.
- **Plugin skill scope:** `skills/list` calls a plugin skill `user`, even when a project's entry enabled it. `agt-env` uses the scope of the entry that enables the plugin.
- **Cached plugins:** the cache can hold plugins with no config entry, so being cached doesn't show that a plugin is enabled.

## How `agt-env` reports this

- The user's entry is `shadowed` by a trusted project's entry, even when the project's turns the server off. The project's row shows the merged result.
- Duplicate skills all load, so they're never `shadowed`.
- The user's plugin entry is `shadowed` by a trusted project's entry, even when the project's turns the plugin off.
- A plugin marked enabled whose manifest is missing or invalid is `broken`, matching the error Codex records for it.
- A plugin's server that loses to another of the same name is `shadowed`.
- A role that loses to one in a higher layer is `shadowed`.
- A user or project hook stopped by `allow_managed_hooks_only` is `disabled`, with a finding that says why.

## Assumptions

- A plugin's own row relies on the documented config key and the cache layout above. `plugin/list` returned nothing without a marketplace, so only its skills and servers could be compared with the real agent.
- A plugin marked enabled with no cached copy is reported as `broken`.
- Project roles are assumed to wait for trust like other project config.
- A plugin's enabled hooks and servers are assumed to wait with it when a project's entry enabled it and the project isn't trusted.
- An invalid matcher is detected with Python's regular expressions, which differ a little from the ones Codex uses. Patterns using syntax that differs aren't flagged.
- `/etc/codex` was tested through a private mount namespace on Linux. Other platforms' paths weren't tested.

## Not covered by `agt-env`

- Agents in plugins, because Codex doesn't load them.
- Custom prompts. The docs mention `/prompts:<name>`, but the source of the tested version has no code that loads prompt files, and no listing shows them.
- MDM and cloud-managed config, and the `mcp_servers` and other restrictions in `requirements.toml`.
- `nickname_candidates` validation on agent roles.
- Commands that a plugin's legacy format migrates into skills.
- Parent project directories, because `agt-env` treats the current directory as the project root.
- Plugins available only through an account.
