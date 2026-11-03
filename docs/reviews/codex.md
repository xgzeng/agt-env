# Codex Discovery & Documentation Review

Audit of `codex` resource discovery across `src/agt_env/adapters/codex.py` (and shared helpers in `adapters/base.py`, `skills.py`, `mcp.py`, and `hooks.py`), `docs/agents/codex.md`, `docs/agents/README.md`, `docs/PRD.md`, and `tests/`.

**Verification baseline:** Upstream `openai/codex` open-source Rust repository (`rust-v0.166.0`, commit `6ff1e1ca3b30`) cross-checked against live isolated non-interactive runs of `codex-cli 0.166.0` (`codex app-server` RPCs `skills/list` and `hooks/list`, and `codex mcp list --json` inside temporary `HOME` and `CODEX_HOME` directories) and official documentation at `https://developers.openai.com/codex/*`.

---

## Summary of Findings

| Done | ID | Severity | Area | Summary |
|---|---|---|---|---|
| [x] | CX-01 | High | MCP / Precedence | `_server_details` accepts merged entries with both `command` and `url` as valid stdio servers, contradicting `codex.md:44` and Codex's strict deserialization which rejects the config layer |
| [x] | CX-02 | High | MCP / Resilience | An invalid server entry causes Codex to reject the entire `config.toml` file, whereas `codex.py` reports the bad server as `broken` and treats sibling servers, plugins, hooks, and agents as `enabled` |
| [x] | CX-03 | High | Skills / Config | `_disabled_skill_files` ignores `name` selectors in `[[skills.config]]` (`name = "..."`), failing to disable matching skills by name |
| [x] | CX-04 | High | Skills / Config | `_disabled_skill_files` resolves paths via process `os.environ["HOME"]` instead of `context.home`, and resolves relative paths against process `os.getcwd()` instead of config directory |
| [x] | CX-05 | Medium | Skills / Bundled | `[skills.bundled] enabled = false` is unsupported and ignored; `agt-env` always reports `$CODEX_HOME/skills/.system` skills as `Scope.SYSTEM` `enabled` |
| [x] | CX-06 | High | Plugins / Status | Configured plugins omitting explicit `enabled = true` (e.g. `[plugins."p@m"]`) are marked `unknown`, whereas Codex enables them by default (`plugin.enabled.unwrap_or(true)`) |
| [x] | CX-07 | High | Plugins / Policy | Global plugin policy `[plugins._default] enabled = false` is parsed as a spurious plugin named `_default` instead of disabling plugins by default |
| [x] | CX-08 | High | Plugins / Versions | `_active_version` compares prerelease segments lexicographically (`"alpha.10" < "alpha.2"`), allows path traversal, and fails to give `"local"` unconditional priority over SemVer versions |
| [x] | CX-09 | High | Plugins / Manifests | `_PLUGIN_MANIFEST` hardcodes `.codex-plugin/plugin.json`, missing root `plugin.json` (Agent Plugins standard), `.claude-plugin/plugin.json`, and `.cursor-plugin/plugin.json` which Codex natively discovers |
| [x] | CX-10 | High | Plugins / PRD | Plugin-bundled MCP servers (`mcpServers` / `mcp.json`) and plugin-bundled agents are omitted, violating PRD Section 8 Metric 1 (`codex mcp list` parity) and Section 5.1 |
| [x] | CX-11 | Medium | Plugins / Hooks | `_plugin_hooks` passes `Status.ENABLED` instead of `plugin.status`, reporting plugin hooks as `unknown`/`enabled` even when the plugin is pending workspace trust |
| [x] | CX-12 | Medium | Plugins / Validation | `plugin_id.partition("@")` permits empty plugin names (`"@m"` resolves to `plugins/cache/m`, treating plugin directories as versions) and unvalidated path traversal characters |
| [x] | CX-13 | Low | Plugins / Diagnostics | `_plugin_manifest` passes `[]` to `read_json`, silently swallowing corrupt-JSON warnings on plugin manifests, and treats JSON arrays as valid empty objects |
| [x] | CX-14 | High | Hooks / Hashing | `_hook_hash` omits `statusMessage` on `mcp_tool` handlers, misreporting trusted `mcp_tool` hooks as `Status.PENDING` ("changed since it was trusted") |
| [x] | CX-15 | Medium | Hooks / Hashing | `_hook_hash` omits `additionalContextLimit` and `commandWindows`, producing mismatched fingerprints when hooks configure context limits or platform commands |
| [x] | CX-16 | Medium | Hooks / Validation | `_hooks_are_valid` only validates `command` handlers; invalid `mcp_tool` handlers or unknown hook event names in `hooks.json` are not caught, while Codex rejects the entire file |
| [x] | CX-17 | Medium | Hooks / Validation | `config.get("hooks")` in `config.toml` bypasses `_hooks_are_valid`, emitting individual broken items instead of failing the file |
| [x] | CX-18 | Low | Hooks / Features | `_HookPolicy` only checks `features.get("hooks") is False`, missing the recognized alias `[features] codex_hooks = false` |
| [x] | CX-19 | Medium | Hooks / Admin Policy | `requirements.toml` top-level `allow_managed_hooks_only = true` (which blocks user, project, and session hooks in Codex) and managed hooks are neither read nor documented |
| [x] | CX-20 | High | Subagents / Validation | `_agents` uses `isinstance(val, str)`, reporting blank/empty strings (`name = ""`, `description = "  "`, `developer_instructions = ""`) as `Status.ENABLED`, whereas Codex rejects blank fields |
| [x] | CX-21 | Medium | Subagents / Diagnostics | Unparseable TOML files and dangling symlinks in `agents/` falsely produce the finding `_AGENT_FIELDS` ("needs a name, description, and developer_instructions, which Codex requires") instead of a syntax/stat error |
| [x] | CX-22 | Medium | Subagents / Shadowing | `Codex.scan` does not deduplicate or shadow same-named subagents across user and project layers; same-named subagents are emitted twice as `enabled` |
| [x] | CX-23 | Medium | Subagents / Inline Roles | User-defined roles declared inline or via `config_file` under `[agents.<role_name>]` in `config.toml` are completely ignored |
| [x] | CX-24 | Medium | Skills / Deduplication | `Codex.scan` calls `load_once` in two isolated passes, failing to deduplicate a skill symlinked between a standard skill root and a plugin |
| [x] | CX-25 | Medium | Skills / Validation | Skill frontmatter validation does not enforce `MAX_NAME_LEN = 64` characters, misreporting skills exceeding 64 chars as `Status.ENABLED` |
| [x] | CX-26 | Medium | Config / Resilience | Unreadable `~/.codex/config.toml` defaults `_HookPolicy` to unmanaged empty state, reporting user hooks in `hooks.json` as `pending` instead of `unknown`, and reporting user subagents as `enabled` instead of `unknown` |
| [x] | CX-27 | Medium | Config / Resilience | Unreadable `.codex/config.toml` in a trusted project falls back to `{}` instead of `unknown`, leaving user MCP servers and plugins that the project might override as `enabled` and unshadowed |
| [x] | CX-28 | Low | Config / Detection | `config_path` only checks `~/.codex` and `.codex`, marking Codex as "not configured" when skills are configured solely in official `~/.agents/skills` or `.agents/skills` directories |
| [x] | CX-29 | Low | Config / System Layers | `/etc/codex/config.toml` (system config layer) and `/etc/codex/requirements.toml` are ignored, scanning only `/etc/codex/skills` |
| [x] | CX-30 | Low | Docs / Accuracy & URLs | Official doc URLs in `docs/agents/codex.md:7-11` point to outdated `learn.chatgpt.com` instead of `developers.openai.com/codex/*`, and `codex.md:122` misattributes TUI diff review custom prompts as slash commands |
| [x] | CX-31 | Low | Safety Tests | `test_safety.py` only audits `~/.codex/auth.json`, missing `$CODEX_HOME/auth.json` when `CODEX_HOME` is overridden |

---

## 1. MCP Servers & Configuration Precedence

### CX-01 — Merged `command` + `url` servers reported as valid stdio (High)
- **Where:** `src/agt_env/adapters/codex.py:303-310, 331-340`, `docs/agents/codex.md:44`
- **Current behavior:** In `codex.py:331-340`:
  ```python
  if isinstance(entry.get("command"), str):
      return stdio_details(entry["command"], entry.get("env"))
  if isinstance(entry.get("url"), str):
      return remote_details("streamable_http", entry["url"], entry.get("http_headers"))
  ```
  When a project overrides a user stdio server with a remote HTTP server (or an entry contains both `command` and `url`), `_merged()` produces a dict with both keys. `_server_details()` checks `command` first and returns `stdio_details`, misreporting the server as a valid `enabled` stdio server.
- **Confirmed Codex source & live behavior:** In `codex-rs/config/src/mcp_types.rs:650-684`, `McpServerTransportConfig` is defined as:
  ```rust
  #[derive(Serialize, Deserialize, Debug, Clone, PartialEq, JsonSchema)]
  #[serde(untagged, deny_unknown_fields, rename_all = "snake_case")]
  pub enum McpServerTransportConfig {
      Stdio { command: String, ... },
      StreamableHttp { url: String, ... },
  }
  ```
  Because both enum variants use `deny_unknown_fields`, an entry with both `command` and `url` cannot be deserialized as `Stdio` (unknown field `url`) or `StreamableHttp` (unknown field `command`). As documented in `codex.md:44` (*"A merged entry with both `command` and `url` makes Codex reject the config | Tested"*), tested live with `codex mcp list --json`: Codex exits with code 1 and outputs nothing.

### CX-02 — Invalid MCP server fails the entire config file in Codex (High)
- **Where:** `src/agt_env/adapters/codex.py:243-247`, `docs/agents/codex.md:93`
- **Current behavior:** In `codex.py`, when a server has invalid fields (e.g. `[mcp_servers.bad] args = ["no-command"]`), `_server_details(entry)` returns `None`. `codex.py` assigns `status = Status.BROKEN` with finding `INVALID_SERVER` to `bad`, while all other servers, plugins, hooks, and agents in that `config.toml` continue to be processed and reported as `enabled`.
- **Confirmed Codex source & live behavior:** In `codex-rs/config/src/config_toml.rs:301`, `mcp_servers: HashMap<String, McpServerConfig>` is a field of `ConfigToml`. A deserialization error in any one MCP server causes the entire `ConfigToml` parse to fail. Confirmed live: in a `config.toml` containing one valid server (`[mcp_servers.good] command = "/bin/true"`) and one invalid server (`[mcp_servers.bad] args = ["x"]`), `codex mcp list --json` exits with code 1 and lists zero servers (failing `good` as well).

---

## 2. Skills Discovery & Enablement Controls

### CX-03 — `[[skills.config]]` name selector ignored (High)
- **Where:** `src/agt_env/adapters/codex.py:526-539`, `docs/agents/codex.md:33`
- **Current behavior:** `_disabled_skill_files()` only checks `entry.get("path")`:
  ```python
  if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
      continue
  ```
  If a user configures `[[skills.config]] name = "my-skill", enabled = false`, `codex.py` ignores the entry and reports `my-skill` as `Status.ENABLED`.
- **Confirmed Codex source & live behavior:** In `codex-rs/config/src/skills_config.rs:20-30, 110-140, 185-215`, `SkillConfig` defines both `pub path: Option<AbsolutePathBuf>` and `pub name: Option<String>`. In `SkillConfigRules::resolve_disabled_paths()`, matching skills are disabled by either canonical path or skill name. Tested live with `codex app-server` `skills/list`: `[[skills.config]] name = "my-skill", enabled = false` reports `'enabled': False`.

### CX-04 — Environment and process-cwd leak in `_disabled_skill_files` (High)
- **Where:** `src/agt_env/adapters/codex.py:534`
- **Current behavior:**
  ```python
  real = os.path.realpath(Path(entry["path"]).expanduser())
  ```
  1. `Path(...).expanduser()` expands `~` using process `os.environ["HOME"]`, ignoring `context.home`. In isolated environments or test harnesses, rules targeting `~` resolve to the developer's real home directory instead of `context.home`.
  2. For relative paths in `entry["path"]`, `os.path.realpath(...)` resolves them against the Python process current directory (`os.getcwd()`), not `context.cwd` or `codex_home`.
- **Confirmed Codex source:** In `codex-rs/config/src/skills_config.rs:24`, `pub path: Option<AbsolutePathBuf>` requires an absolute path. In `codex-rs/config/src/loader/layer_io.rs:160-220`, relative paths in config layers are normalized against the layer's own directory (`codex_home` or `project_dir`).

### CX-05 — `[skills.bundled] enabled = false` unsupported (Medium)
- **Where:** `src/agt_env/adapters/codex.py:69`
- **Current behavior:** `codex.py` always scans `codex_home / "skills" / ".system"` as `Scope.SYSTEM` and reports discovered bundled skills as `Status.ENABLED`.
- **Confirmed Codex source & live behavior:** In `codex-rs/config/src/skills_config.rs:40-45, 144-162`, `SkillsConfig` includes `pub bundled: Option<BundledSkillsConfig>`. When `[skills.bundled] enabled = false`, `bundled_skills_enabled_from_stack()` returns `false`, and bundled skills are excluded. Tested live with `codex app-server` `skills/list`: `system skills count = 0`.

---

## 3. Plugins, Versions, & Marketplaces

### CX-06 — Plugins enabled by default misreported as `unknown` (High)
- **Where:** `src/agt_env/adapters/codex.py:276-280`, `docs/agents/codex.md:50`
- **Current behavior:** In `codex.py:276-280`:
  ```python
  enabled = entry.get("enabled")
  if isinstance(enabled, bool):
      status = _switched(enabled, default)
  else:
      status = Status.UNKNOWN
  ```
  When a plugin is configured without an explicit `enabled` key (e.g. `[plugins."p@m"]`), `codex.py` reports `Status.UNKNOWN`.
- **Confirmed Codex source & live behavior:** In `codex-rs/core-plugins/src/loader.rs:188-191`:
  ```rust
  for (plugin_id, plugin) in &mut configured_plugins {
      plugin.enabled =
          Some(plugin.enabled.unwrap_or(true) && plugin_policy.allows_plugin(plugin_id));
  }
  ```
  `plugin.enabled.unwrap_or(true)` defaults to `true`. Tested live with `codex app-server` `skills/list`: `[plugins."p@m"]` without `enabled = true` loads all skills with `'enabled': True`.

### CX-07 — Global `[plugins._default]` policy parsed as a plugin (High)
- **Where:** `src/agt_env/adapters/codex.py:270-285`
- **Current behavior:** In `codex.py:270`, `for plugin_id, entry in plugins.items():` iterates all keys in `[plugins]`. If the user configures `[plugins._default] enabled = false`, `codex.py` treats `_default` as a plugin ID (`name = "_default", marketplace = ""`), reports it as an item, and fails to apply the default-disabled policy to other plugins.
- **Confirmed Codex source & live behavior:** In `codex-rs/config/src/types.rs:1101-1125`, `PluginsConfigToml` explicitly deserializes `#[serde(rename = "_default")] pub default: Option<PluginsDefaultConfig>` and flattens the remaining keys into `plugins`. In `allows_plugin()`, if `_default.enabled == false`, all plugins lacking explicit `enabled = true` are disabled. Tested live with `codex app-server` `skills/list`: with `[plugins._default] enabled = false` and `[plugins."p@m"]`, zero plugin skills are loaded.

### CX-08 — Plugin version selection violates SemVer and `"local"` priority (High)
- **Where:** `src/agt_env/adapters/codex.py:464-476, 480-488`, `docs/agents/codex.md:102`
- **Current behavior:**
  1. `_compare_versions()` compares prerelease identifiers as plain strings (`left, right = left_pre, right_pre; return (left > right) - (left < right)`), treating `"1.0.0-alpha.10"` as older than `"1.0.0-alpha.2"`.
  2. `_active_version()` sorts version directories solely by version comparison without special-casing `"local"`.
- **Confirmed Codex source & live behavior:** In `codex-rs/core-plugin-common/src/installed.rs:40-62`:
  ```rust
  pub fn compare_plugin_versions(left: &str, right: &str) -> Ordering {
      match (Version::parse(left), Version::parse(right)) {
          (Ok(left), Ok(right)) => left.cmp(&right),
          _ => left.cmp(right),
      }
  }

  pub fn active_plugin_version(plugin_base_root: &AbsolutePathBuf) -> Option<String> {
      ...
      if discovered_versions.iter().any(|version| version == DEFAULT_PLUGIN_VERSION) {
          Some(DEFAULT_PLUGIN_VERSION.to_string())
      } else {
          discovered_versions.pop()
      }
  }
  ```
  `DEFAULT_PLUGIN_VERSION = "local"`. If `"local"` exists in the cache, Codex picks `"local"` unconditionally over all SemVer versions (e.g. `10.0.0`). Tested live: with both `local` and `10.0.0` cached, `codex app-server` `skills/list` loads `p:vlocal`.

### CX-09 — Standard and cross-agent plugin manifests ignored (High)
- **Where:** `src/agt_env/adapters/codex.py:38, 490-494`, `docs/agents/codex.md:54`
- **Current behavior:** `_PLUGIN_MANIFEST = Path(".codex-plugin") / "plugin.json"`. Plugins using other manifest formats are treated as having no manifest, contributing no skills.
- **Confirmed Codex source & live behavior:** In `codex-rs/utils/plugins/src/plugin_namespace.rs:30-80` and `codex-rs/core-plugins/src/agent_plugin_manifest.rs:1-180`, Codex searches `DISCOVERABLE_PLUGIN_MANIFEST_PATHS` in order:
  1. Root `plugin.json` (Agent Plugins specification schema `https://agent-plugins.org/schemas/1.0.0/plugin.schema.json`)
  2. `.codex-plugin/plugin.json`
  3. `.claude-plugin/plugin.json`
  4. `.cursor-plugin/plugin.json`
  Tested live: a plugin providing only `.claude-plugin/plugin.json` successfully loads in `codex app-server` `skills/list` as `claude-style:c-skill`.

### CX-10 — Plugin-bundled MCP servers and agents omitted (High)
- **Where:** `src/agt_env/adapters/codex.py:87-115`, `docs/agents/codex.md:121`
- **Current behavior:** `codex.py` only extracts skills from enabled plugins. Plugin-bundled MCP servers and agents are not discovered or reported.
- **Confirmed Codex source & PRD parity:** In `codex-rs/core-plugins/src/loader.rs:969-975, 1431-1460` and `codex-rs/core-plugins/src/manifest.rs:70-80`, plugin manifests support `mcp_servers` (`mcp.json` or inline `mcpServers`). `codex mcp list --json` and `app-server` load and expose these servers. PRD Section 8 Metric 1 (*"for each P0 agent, MCP results match that agent's own listing command"*) and Section 5.1 (*"What a plugin brings is listed directly below its row"*) require plugin-bundled servers to be listed under `codex`.

### CX-11 — Untrusted-project plugin hooks reported as `Status.ENABLED` (Medium)
- **Where:** `src/agt_env/adapters/codex.py:186`
- **Current behavior:**
  ```python
  items = policy.items(
      self.id,
      hooks.get("hooks"),
      plugin.scope,
      Status.ENABLED,
      root / relative,
      ...
  )
  ```
  `_plugin_hooks` passes `default = Status.ENABLED` to `policy.items()`. When a plugin is enabled by a project layer that is untrusted (`plugin.status is Status.PENDING` or `Status.UNKNOWN`), its hooks are evaluated with `Status.ENABLED`, potentially reporting them as `Status.UNKNOWN` rather than `Status.PENDING`.

### CX-12 — Unchecked plugin ID segment path traversal (Medium)
- **Where:** `src/agt_env/adapters/codex.py:273-275`
- **Current behavior:**
  ```python
  name, _, marketplace = plugin_id.partition("@")
  directory = codex_home / "plugins" / "cache" / marketplace / name
  cached = bool(marketplace) and os.path.isdir(directory)
  ```
  If `plugin_id = "@m"` (empty `name`), `directory` resolves to `plugins/cache/m`, which is a directory (the marketplace folder). `cached` evaluates to `True`, and `_active_version()` treats other plugin directories under `m` as version strings. If `plugin_id` contains `../` or `/`, it traverses outside the cache.
- **Confirmed Codex source:** In `codex-rs/core-plugin-common/src/plugin_id.rs:51-80`, `validate_plugin_segment()` enforces that `name` and `marketplace` are non-empty, reject `.` and `..`, reject path separators, and allow only alphanumeric, `-`, `_`, and `.` characters.

### CX-13 — `_plugin_manifest` swallows corrupt JSON warnings (Low)
- **Where:** `src/agt_env/adapters/codex.py:490-494`
- **Current behavior:** `_plugin_manifest()` calls `read_json(path, [])` with an empty warning list, silently suppressing warnings on malformed `plugin.json` files. Furthermore, `read_json()` returns `({}, True)` for non-dict JSON (e.g. `[1, 2]`), causing `_plugin_manifest` to treat non-object JSON as a valid empty manifest.

---

## 4. Hooks & Admin Policy

### CX-14 — `mcp_tool` hook fingerprint omits `statusMessage` (High)
- **Where:** `src/agt_env/adapters/codex.py:410-417`
- **Current behavior:** `_hook_hash()` serializes `mcp_tool` handlers as:
  ```python
  normalized = {
      "type": "mcp_tool",
      "server": entry.get("server"),
      "tool": entry.get("tool"),
      "input": entry.get("input", {}),
      "timeout": timeout,
  }
  ```
  `statusMessage` is excluded for `mcp_tool` handlers (it is only included for `command` handlers at lines 425-426).
- **Confirmed Codex source & live behavior:** In `codex-rs/protocol/src/hooks.rs:170-179`, `HookHandlerConfig::McpTool` defines `pub status_message: Option<String>`. In `codex-rs/hooks/src/engine/discovery.rs:981-998`, the entire `HookHandlerConfig` is serialized into `NormalizedHookIdentity` before hashing. Tested live with `codex app-server` `hooks/list`: adding `"statusMessage": "Scanning..."` to an `mcp_tool` hook changes `currentHash`. In `agt-env`, any trusted `mcp_tool` hook configuring `statusMessage` produces a hash mismatch and is falsely reported as `Status.PENDING` ("changed since it was trusted").

### CX-15 — `_hook_hash` omits `additionalContextLimit` and `commandWindows` (Medium)
- **Where:** `src/agt_env/adapters/codex.py:419-427`
- **Current behavior:** `_hook_hash()` does not serialize `additionalContextLimit` or `commandWindows` for `command` hooks.
- **Confirmed Codex source & live behavior:** In `codex-rs/protocol/src/hooks.rs:152-167`, `HookHandlerConfig::Command` includes `command_windows` and `additional_context_limit`. Tested live with `codex app-server` `hooks/list`: specifying `"additionalContextLimit": 5000` changes `currentHash`. Hooks using these fields fail hash matching in `agt-env`.

### CX-16 — `_hooks_are_valid` misses invalid `mcp_tool` entries and unknown events (Medium)
- **Where:** `src/agt_env/adapters/codex.py:434-443`
- **Current behavior:** `_hooks_are_valid()` only checks `entry.get("type", "command") == "command" and not isinstance(entry.get("command"), str)`. Invalid `mcp_tool` entries (missing `server` or `tool`) and invalid event names pass validation. `policy.items()` then marks individual invalid entries as `broken` and emits the rest.
- **Confirmed Codex source:** In `codex-rs/hooks/src/engine/discovery.rs:410-419`, `load_hooks_json()` parses the entire file into `HooksFile` via `serde_json::from_str`. If any handler or event name fails deserialization, Codex rejects the entire `hooks.json` file and loads zero hooks from it.

### CX-17 — `config.toml` `[hooks]` table bypasses `_hooks_are_valid` (Medium)
- **Where:** `src/agt_env/adapters/codex.py:128`
- **Current behavior:** `_hooks_are_valid` is called for `hooks.json`, but `config.get("hooks")` in `config.toml` is passed directly to `policy.items()`. Invalid hook entries in `config.toml` are emitted as individual `broken` items, whereas Codex rejects the `ConfigToml` parse.

### CX-18 — `[features] codex_hooks = false` unrecognized (Low)
- **Where:** `src/agt_env/adapters/codex.py:348`
- **Current behavior:** `self.off = isinstance(features, dict) and features.get("hooks") is False`.
- **Confirmed Codex source:** In `codex-rs/features/src/legacy.rs:49-51` and `codex-rs/features/src/tests.rs:565-568`, `codex_hooks` is a stable alias for `Feature::CodexHooks`. If a user specifies `[features] codex_hooks = false`, `codex.py` does not turn off hooks.

### CX-19 — `requirements.toml` `allow_managed_hooks_only` not read or documented (Medium)
- **Where:** `src/agt_env/adapters/codex.py:117-130`, `docs/agents/codex.md:79, 123`
- **Current behavior:** `codex.py` does not read `/etc/codex/requirements.toml` or evaluate `allow_managed_hooks_only`.
- **Confirmed Codex source & docs:** In `openai/codex/docs/config.md:14-20` and `codex-rs/hooks/src/engine/discovery.rs:1030-1080`, admins can set top-level `allow_managed_hooks_only = true` in `requirements.toml` to disable all user, project, and session hook configs. `agt-env` continues to report user and project hooks as `pending`/`enabled`.

---

## 5. Subagents & Agent Roles

### CX-20 — Blank subagent fields accepted as valid (High)
- **Where:** `src/agt_env/adapters/codex.py:144-147`
- **Current behavior:**
  ```python
  valid = all(
      isinstance(fields.get(key), str)
      for key in ("name", "description", "developer_instructions")
  )
  ```
  If `name = ""`, `description = "  "`, or `developer_instructions = ""`, `isinstance("", str)` evaluates to `True`, and `codex.py` reports the subagent as `Status.ENABLED`.
- **Confirmed Codex source:** In `codex-rs/agent-roles/src/agent_role_config.rs:50-130`, `normalize_agent_role_description()`, `validate_agent_role_file_developer_instructions()`, and role name trimming explicitly reject blank/whitespace strings with `std::io::ErrorKind::InvalidInput` ("cannot be blank", "must define a non-empty `name`").

### CX-21 — Corrupt agent TOML misreported as missing required fields (Medium)
- **Where:** `src/agt_env/adapters/codex.py:142-163`
- **Current behavior:** When an agent file in `.codex/agents/*.toml` has syntax errors (e.g. `bad.toml` containing `not toml [`) or is a dangling symlink, `_read_toml()` returns `None`. `fields` becomes `{}`, `valid` becomes `False`, and `codex.py` emits:
  `Finding("needs a name, description, and developer_instructions, which Codex requires")`.
- **Expected behavior:** Per PRD `FR-23`, `broken` items must state their actual failure reason (e.g. `invalid TOML syntax` or `symlink target is missing`), rather than claiming fields are missing when the file failed to parse.

### CX-22 — Same-named subagents not shadowed across layers (Medium)
- **Where:** `src/agt_env/adapters/codex.py:121`
- **Current behavior:** `report.items.extend(self._agents(directory / "agents", scope, default, config, report))` is called for each layer. Subagents are never passed to `mark_shadowed()`. If a user defines `~/.codex/agents/worker.toml` and a project defines `.codex/agents/worker.toml`, `codex.py` emits both as `enabled`.
- **Confirmed Codex source:** In `codex-rs/agent-roles/src/loader.rs:90-105`, `load_agent_roles()` iterates layers from low to high (`layers_low_to_high`). A project role replaces/merges over the user's role of the same name. In `agt-env`, the user subagent should be marked `shadowed`.

### CX-23 — Roles declared in `config.toml` `[agents.<name>]` ignored (Medium)
- **Where:** `src/agt_env/adapters/codex.py:121, 133-165`
- **Current behavior:** `codex.py` only scans `.codex/agents/*.toml`. Roles declared under `[agents.<role_name>]` in `config.toml` (or referenced via `config_file = "..."`) are completely ignored.
- **Confirmed Codex source:** In `codex-rs/config/src/config_toml.rs:788-795` and `codex-rs/agent-roles/src/loader.rs:35-65`, `AgentsToml::roles` allows user-defined roles to be declared directly in `config.toml`.

---

## 6. Shared Helpers, Skills, & Safety Tests

### CX-24 — Double `load_once` fails cross-group skill deduplication (Medium)
- **Where:** `src/agt_env/adapters/codex.py:79, 108`
- **Current behavior:** Line 79 executes `report.items = load_once(report.items)` for filesystem skills. Line 108 executes a separate `load_once(...)` for plugin skills, and line 107 appends them to `report.items`. Because `seen` sets are separate, a skill sharing a real path between a filesystem skill directory and a plugin is emitted twice instead of deduplicated.
- **Confirmed Codex source:** In `codex-rs/ext/skills/src/host_roots.rs:114`, `dedupe_skill_roots_by_path(&mut roots)` deduplicates roots across both host roots and plugin skill roots before loading.

### CX-25 — Skill frontmatter max name length (64 chars) unenforced (Medium)
- **Where:** `src/agt_env/adapters/codex.py:215-234`, `src/agt_env/skills.py:193-208`
- **Current behavior:** `agt-env` accepts skill names of arbitrary length.
- **Confirmed Codex source:** In `codex-rs/skills/src/parser.rs:5, 83, 153`, `MAX_NAME_LEN = 64`. Names longer than 64 characters fail parsing with `SkillParseError::InvalidField { field: "name", reason: "exceeds maximum length of 64 characters" }`, and Codex refuses to load the skill.

### CX-26 — Corrupt `~/.codex/config.toml` status inversions (Medium)
- **Where:** `src/agt_env/adapters/codex.py:64, 82, 117-128`
- **Current behavior:** When `~/.codex/config.toml` is unreadable/corrupt, `user_config` is `None`. `_HookPolicy(None)` sets `self.off = False` and `self.state = {}`. User hooks in `~/.codex/hooks.json` are reported as `pending` instead of `unknown`. In addition, user subagents in `layers[0]` receive `default = Status.ENABLED`, reporting them `enabled` even though `agents.enabled` could not be read.
- **Expected behavior:** Per `AGENTS.md` (*"Status needs evidence. Use `unknown` rather than guessing `enabled`... A config file that can't be read gives a warning and `unknown`"*), unreadable configuration must mark dependent items `Status.UNKNOWN`.

### CX-27 — Corrupt project `config.toml` clears project overrides (Medium)
- **Where:** `src/agt_env/adapters/codex.py:85-86`
- **Current behavior:** `project_config = _read_toml(project_path, report.warnings) or {}`. When a trusted project's `.codex/config.toml` is unreadable, `project_config` evaluates to `{}`. Any user MCP servers or plugins that the project intended to disable remain `Status.ENABLED` and unshadowed.
- **Expected behavior:** Unreadable project config in a trusted workspace must mark affected user items as `Status.UNKNOWN`.

### CX-28 — Configured detection ignores `~/.agents/skills` and `.agents/skills` (Low)
- **Where:** `src/agt_env/adapters/codex.py:53-55`
- **Current behavior:** `config_path` checks only `codex_home` (`~/.codex`) and `project_dir` (`.codex`). When a user or project configures skills solely via official `~/.agents/skills` or `.agents/skills`, `config_path` is `None`. Codex is treated as "not configured" and hidden by default under PRD `FR-22`.

### CX-29 — System config layers `/etc/codex/config.toml` and `requirements.toml` ignored (Low)
- **Where:** `src/agt_env/adapters/codex.py:66-75`, `docs/agents/codex.md:30`
- **Current behavior:** `codex.py` scans `/etc/codex/skills`, but completely ignores `/etc/codex/config.toml` and `/etc/codex/requirements.toml`.
- **Confirmed Codex source:** In `codex-rs/config/src/loader/layer_io.rs:22` and `codex-rs/config/src/loader/README.md:35`, `/etc/codex/config.toml` is the base system config layer.

### CX-30 — Outdated documentation URLs and custom prompts misconception (Low)
- **Where:** `docs/agents/codex.md:7-11, 122`
- **Current behavior:**
  1. Lines 7-11 link to `https://learn.chatgpt.com/docs/*`.
  2. Line 122 states: *"Custom prompts. The docs say they appear as `/prompts:<name>` without saying where they live, and no listing shows them."*
- **Confirmed Codex source & docs:**
  1. Canonical documentation is published at `https://developers.openai.com/codex/{skills,config-basic,config-advanced,config-reference,cli/slash-commands}`.
  2. Codex does not load file-based custom prompts or user slash commands; the term refers to the TUI diff review custom prompt popup (`codex-rs/tui/src/bottom_pane/custom_prompt_view.rs`).

### CX-31 — Safety test omits overridden `CODEX_HOME/auth.json` (Low)
- **Where:** `tests/test_safety.py:16`
- **Current behavior:** `CREDENTIAL_STORES` hardcodes `.codex/auth.json` under `home`. When `CODEX_HOME` is overridden to a custom path, `auth.json` is stored at `$CODEX_HOME/auth.json` (as confirmed in `codex-rs/login/src/auth/storage.rs:163`), which is not tested for credential leak protection.

---

## Resolution

Every claim was checked against `codex-cli 0.166.0`, its source at tag `rust-v0.166.0`, and the docs. All 31 findings needed a change, but several were different from what's described above. Rules and evidence are in `docs/agents/codex.md`.

| ID | Outcome | What differed from the review |
|---|---|---|
| CX-01 | Fixed | |
| CX-02 | Fixed | Goes further than the review says: `skills/list` and `hooks/list` also fail. So everything shows as `unknown`, and only the bad entry as `broken` |
| CX-03 | Fixed | Rules apply in order and the last match wins. Entries with both selectors are ignored. A plugin skill matches only by its full `plugin:skill` name |
| CX-04 | Fixed | |
| CX-05 | Fixed | Also applies from a trusted project or the system config |
| CX-06 | Fixed | |
| CX-07 | Fixed | Applies from any loaded layer. An explicit `enabled = true` still wins |
| CX-08 | Fixed | No path traversal was possible here, as names come from `scandir`. Build metadata also counts in the order, and leading zeros aren't semver |
| CX-09 | Partly valid, fixed | A root `plugin.json` counts only if its `$schema` names the Agent Plugins format, and that format reads only direct skills and no hooks. The first manifest present counts even if invalid |
| CX-10 | Partly valid, fixed | Servers fixed. The file is `.mcp.json`, and `mcp.json` isn't read. Codex doesn't load agents from plugins, so nothing to list there |
| CX-11 | Fixed | |
| CX-12 | Fixed | IDs split at the last `@`, and marketplace names can't contain dots |
| CX-13 | Fixed | Such a plugin is now `broken`, matching the error Codex records |
| CX-14 | Fixed | |
| CX-15 | Partly valid, fixed | `commandWindows` isn't in the fingerprint at all. `additionalContextLimit` is, only on five events and not at its default. Testing also showed timeouts and `matcher` were wrong on some events, now fixed |
| CX-16 | Partly valid, fixed | Unknown event names are ignored, not rejected, so they're no longer listed. A missing `type` does reject the file, which the code had treated as `command` |
| CX-17 | Fixed | Stronger than described: Codex refuses to start. Unknown events in `[hooks]` are accepted |
| CX-18 | Fixed | `hooks` wins when both are set |
| CX-19 | Fixed | Also lists hooks from `[hooks]` in `requirements.toml` and the system files, which need no review |
| CX-20 | Fixed | |
| CX-21 | Fixed | |
| CX-22 | Fixed | A higher layer's role can inherit a lower one's description |
| CX-23 | Fixed | Role files are also found at any depth, which the code had missed |
| CX-24 | Fixed | The source reference is about roots, but testing confirmed it holds per skill: the skill loads once, as the plugin's. So the plain row goes away |
| CX-25 | Fixed | Applies to a name from the directory too |
| CX-26 | Fixed | Covered by the rejected-config handling, as Codex doesn't start at all |
| CX-27 | Fixed | Same. Only for a trusted project: an untrusted one's corrupt file has no effect |
| CX-28 | Fixed | |
| CX-29 | Fixed | Live-tested in a private `/etc` |
| CX-30 | Partly valid | The `developers.openai.com/codex` URLs redirect to the `learn.chatgpt.com` pages already linked, so links stay, with a note. The custom prompts note is corrected |
| CX-31 | Fixed | |
