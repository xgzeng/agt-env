# Antigravity (`agy`) Discovery Behavior Review

This document provides a comprehensive technical review of `agt-env`'s Antigravity adapter ([`antigravity.py`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/antigravity.py)), its documentation ([`antigravity.md`](file:///home/zengxg/projects/agent-env/docs/agents/antigravity.md)), shared helpers ([`hooks.py`](file:///home/zengxg/projects/agent-env/src/agt_env/hooks.py), [`mcp.py`](file:///home/zengxg/projects/agent-env/src/agt_env/mcp.py), [`skills.py`](file:///home/zengxg/projects/agent-env/src/agt_env/skills.py), [`base.py`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/base.py)), and test suite ([`test_antigravity_oracle.py`](file:///home/zengxg/projects/agent-env/tests/test_antigravity_oracle.py), [`test_safety.py`](file:///home/zengxg/projects/agent-env/tests/test_safety.py)) against the official Antigravity Customization System specification, official guide skills, binary changelog/release notes, and the live `agy` v1.4.12 binary behavior.

---

## Summary of Findings

| ID | Severity | Area | Summary | Done |
|---|---|---|---|:---:|
| `AG-01` | **High** | MCP / Plugins | MCP servers bundled in plugins (`plugins/<name>/mcp_config.json`) are completely dropped from discovery | [x] |
| `AG-02` | **High** | Hooks | Flat lifecycle hook handlers (`SessionStart`, `PreInvocation`, etc.) are falsely marked as broken (`hooks.py`) | [x] |
| `AG-03` | **High** | Precedence | Workspace-over-global precedence and shadowing for same-named skills and servers are completely unhandled | [x] |
| `AG-04` | **High** | Skills / Symlinks | Symlink deduplication order scans user directories before workspace directories, inverting project precedence | [x] |
| `AG-05` | **High** | Workspace Discovery | Alternative documented workspace roots (`_agents/` and `_agent/`) are omitted across all customization types | [x] |
| `AG-06` | **High** | Configuration | Declared configuration manifests (`skills.json`, `plugins.json`, `agents.json`) are completely ignored | [x] |
| `AG-07` | **Medium** | Plugins | Recognized native plugin manifest `.gemini-plugin/plugin.json` is missing from plugin manifest discovery | [x] |
| `AG-08` | **Medium** | Documentation / Testing | Read-only TUI slash commands work non-interactively in print mode, disproving core documentation assumptions | [x] |
| `AG-09` | **Medium** | Skills | Unnamed skill fallback collapses all unnamed skills into `"SKILL"`, causing collisions and dropped items | [x] |
| `AG-10` | **Medium** | Configuration | Primary `config_path` resolves to a generic directory (`~/.gemini/antigravity-cli`) rather than a config file | [ ] |
| `AG-11` | **Medium** | Workspaces / Trust | Workspace trust check diverges from `agy` semantics when symlinked project paths are listed in `trustedWorkspaces` | [x] |
| `AG-12` | **Medium** | MCP | Dual-transport servers with both `command` and `url` unconditionally assume stdio without checking transport | [x] |
| `AG-13` | **Low** | Subagents | Subagent validation checks only frontmatter name/description, accepting empty prompt bodies as valid | [ ] |
| `AG-14` | **Low** | Subagents | Subagents with duplicate names across workspace and global scopes are both reported as enabled | [x] |
| `AG-15` | **Low** | Documentation | Assumption in `antigravity.md` regarding `~/.gemini/skills` is verified and confirmed by real agent execution | [x] |
| `AG-16` | **Low** | Documentation | Assumption that global `config.json` disables workspace plugins is verified and confirmed by real agent execution | [x] |
| `AG-17` | **Low** | Safety | Safety audit test lacks explicit assertions for Antigravity state files like `jetski_state.pbtxt` | [x] |

---

## Detailed Findings

### `AG-01`: MCP Servers Bundled in Plugins Are Dropped from Discovery

- **Severity:** High
- **Area:** MCP / Plugins
- **Source Reference:** [`src/agt_env/adapters/antigravity.py:182-195`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/antigravity.py#L182-L195) (`_plugin_extras`), [`docs/agents/antigravity.md:118`](file:///home/zengxg/projects/agent-env/docs/agents/antigravity.md#L118)
- **Upstream / Docs Reference:**
  - Official Plugin Guide (`plugins.md:18, 71-72`): Directory structure defines `plugins/<name>/mcp_config.json` ("Optional: MCP servers exposed by the plugin").
  - Official MCP Guide (`mcp_servers.md:21-22, 89-91`): *"Plugin Configuration: `plugins/<plugin_name>/mcp_config.json` (active when the plugin is enabled)... Plugin Servers: Only active when the parent plugin is loaded. Their tools are automatically prefixed or namespaced if necessary to avoid conflicts."*
  - Binary Changelog / Embedded Release Notes: *"Fixed MCP servers bundled inside plugins colliding with each other or with user-configured servers in `mcp_config.json` when they shared the same server name by automatically namespacing plugin MCP servers as `<plugin>_<server>`."*
  - Live empirical testing: `agy plugin list --json` on a plugin containing `mcp_config.json` explicitly outputs:
    ```json
    [{"name": "p", "enabled": true, "path": "...", "mcpServers": ["plugin-srv"]}]
    ```
- **agt-env Behavior:**
  In [`antigravity.py:182-195`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/antigravity.py#L182-L195), `_plugin_extras` only scans subagents (`plugin.source_path / "agents"`) and hooks (`plugin.source_path / "hooks.json"`). It completely omits `mcp_config.json`. [`docs/agents/antigravity.md:118`](file:///home/zengxg/projects/agent-env/docs/agents/antigravity.md#L118) notes this as an intentional exclusion: *"Not covered by agt-env: Built-in skills, and MCP servers bundled in plugins."*
- **Impact:**
  Directly violates [`docs/PRD.md`](file:///home/zengxg/projects/agent-env/docs/PRD.md) Section 5.1 ("MCP servers, including those added by plugins"). Tools and servers provided by enabled plugins are invisible to users and automation inspecting the environment via `agt-env`.
- **Suggested Fix:**
  In `_plugin_extras` (or directly in plugin processing in `scan`), load `plugin.source_path / "mcp_config.json"` via `self._servers`, prefix server names with the plugin name (`f"{plugin.name}_{server.name}"`), and bundle them using `mark_bundled`.

---

### `AG-02`: Flat Lifecycle Hook Handlers Falsely Marked as Broken

- **Severity:** High
- **Area:** Hooks
- **Source Reference:** [`src/agt_env/hooks.py:31-43`](file:///home/zengxg/projects/agent-env/src/agt_env/hooks.py#L31-L43) (`handlers`), [`src/agt_env/adapters/antigravity.py:154-156`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/antigravity.py#L154-L156)
- **Upstream / Docs Reference:**
  - Official Lifecycle Hooks Guide (`hooks.md:86-100` "Supported Event Types"):
    - Grouped events (`PreToolUse`, `PostToolUse`): Uses `matcher` and `hooks` wrapper (`[{"matcher": "...", "hooks": [...]}]`).
    - Flat events (`SessionStart`, `PreInvocation`, `PostInvocation`, `Stop`): Uses flat handler objects directly (`[{"type": "command", "command": "./scripts/reminder.sh"}]`).
  - Live empirical testing:
    ```json
    {"flat-hook": {"Stop": [{"command": "/bin/true"}]}}
    ```
    Running `agy plugin list --json` and `agy -p "/hooks" --output-format json` recognizes and enables `flat-hook` without error.
- **agt-env Behavior:**
  [`hooks.py:31-43`](file:///home/zengxg/projects/agent-env/src/agt_env/hooks.py#L31-L43) assumes the nested Claude Code schema (`matcher` and `hooks` wrapper) for every event. In `handlers()`, `group.get("hooks")` returns `None` for flat events, yielding zero handlers. In [`antigravity.py:154-156`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/antigravity.py#L154-L156), `found` is empty, causing `valid = False`. The hook is reported as `Status.BROKEN` with finding `"invalid hook entry"`.
  The oracle test in [`tests/test_antigravity_oracle.py:238-241`](file:///home/zengxg/projects/agent-env/tests/test_antigravity_oracle.py#L238-L241) masked this bug by artificially wrapping `Stop` in `{"Stop": [{"hooks": [{"command": "/bin/true"}]}]}`.
- **Impact:**
  Any valid Antigravity hook configuring `SessionStart`, `PreInvocation`, `PostInvocation`, or `Stop` is falsely reported as broken with an error finding.
- **Suggested Fix:**
  Update `handlers()` in `hooks.py` to recognize flat handler dictionaries directly when `group.get("hooks")` is not present, or provide an Antigravity hook handler parser that unpacks both flat handlers and grouped matchers.

---

### `AG-03`: Missing Precedence and Shadowing for Same-Named Skills and Servers

- **Severity:** High
- **Area:** Precedence / Shadowing
- **Source Reference:** [`src/agt_env/adapters/antigravity.py:76, 82, 114`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/antigravity.py#L76), [`docs/agents/antigravity.md:109`](file:///home/zengxg/projects/agent-env/docs/agents/antigravity.md#L109)
- **Upstream / Docs Reference:**
  - Official Customization Guide (`SKILL.md:58-75` "Loading Priority and Precedence"):
    *"When multiple customizations are discovered, they are loaded and applied in a specific order. If there are naming conflicts (e.g., two skills with the same name), the higher-priority customization overrides the lower-priority one. Priority: 1. Workspace Project, 2. Declared Configurations, 3. Global Discovery..."*
  - Live empirical testing: Setting up `my-skill` in `.agents/skills/my-skill` and `~/.gemini/config/skills/my-skill`, running `agy -p "/skills" --output-format json` outputs only the workspace skill; the global skill is completely overridden.
- **agt-env Behavior:**
  [`antigravity.py`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/antigravity.py) never calls `mark_shadowed` on skills, MCP servers, or subagents. Both items are reported as `Status.ENABLED`. [`docs/agents/antigravity.md:109`](file:///home/zengxg/projects/agent-env/docs/agents/antigravity.md#L109) asserts: *"Assumptions: Precedence between same-named skills or servers isn't known, because the listings that show them need a signed-in session. agt-env never reports these as shadowed."*
- **Impact:**
  Violates [`AGENTS.md`](file:///home/zengxg/projects/agent-env/AGENTS.md) and [`docs/PRD.md`](file:///home/zengxg/projects/agent-env/docs/PRD.md) shadowing requirements. The user sees duplicate active skills/servers when in fact the workspace item shadows the global one at runtime.
- **Suggested Fix:**
  Call `mark_shadowed` for skills, MCP servers, and subagents, ordered by precedence: Project > User.

---

### `AG-04`: Inverted Deduplication Priority for Symlinked Skills

- **Severity:** High
- **Area:** Skills / Symlinks
- **Source Reference:** [`src/agt_env/adapters/antigravity.py:66-76`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/antigravity.py#L66-L76)
- **Upstream / Docs Reference:**
  - Official loading priority (`SKILL.md:60-70`): Workspace Project is priority #1, Global Discovery is priority #3.
- **agt-env Behavior:**
  In [`antigravity.py:66-70`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/antigravity.py#L66-L70):
  ```python
  skill_roots = [
      *[(Scope.USER, root, Status.ENABLED) for root in user_skill_dirs],
      (Scope.PROJECT, workspace_dir / "skills", project_status),
      (Scope.PROJECT, context.cwd / ".agent" / "skills", project_status),
  ]
  ```
  User roots are scanned before project roots. On line 76, `load_once(report.items)` deduplicates by `resolved_path` (`os.path.realpath`). If a workspace project skill is a symlink pointing to a user skill, `load_once` encounters the `Scope.USER` item first and retains it, discarding the `Scope.PROJECT` item.
- **Impact:**
  Directly inverts precedence: the workspace-scoped skill is dropped, and the item is misreported as a user-scoped skill.
- **Suggested Fix:**
  Reverse the scanning order in `skill_roots` so that workspace project paths (`Scope.PROJECT`) are scanned before global user paths (`Scope.USER`), ensuring `load_once` preserves the project item.

---

### `AG-05`: Missing Alternative Workspace Dot-Directories (`_agents` and `_agent`)

- **Severity:** High
- **Area:** Workspace Discovery
- **Source Reference:** [`src/agt_env/adapters/antigravity.py:41, 68-70, 89, 111-112, 117`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/antigravity.py#L41)
- **Upstream / Docs Reference:**
  - Official Customization Guide (`SKILL.md:41-43`): *"Path: `.agents/` (or `.agent/`, `_agents/`, `_agent/`) at the root of your project."*
  - Live empirical testing: Running `agy agents` in a project with `_agents/agents` or `_agent/agents` loads the custom agents successfully.
- **agt-env Behavior:**
  [`antigravity.py`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/antigravity.py) defines `workspace_dir = context.cwd / ".agents"`. It checks `workspace_dir` and optionally `.agent` for skills and subagents, but never checks `_agents` or `_agent` for anything. Furthermore, plugins, hooks, and MCP servers do not even check `.agent`.
- **Impact:**
  Customizations located in `_agents/` (frequently used on Windows or in repos avoiding hidden folders) or `_agent/` are completely omitted from discovery.
- **Suggested Fix:**
  Support all four documented workspace root directory aliases (`.agents`, `.agent`, `_agents`, `_agent`) uniformly across skills, MCP servers, plugins, hooks, and subagents.

---

### `AG-06`: Omission of Declared Configuration Manifests (`skills.json`, `plugins.json`, `agents.json`)

- **Severity:** High
- **Area:** Configuration
- **Source Reference:** [`src/agt_env/adapters/antigravity.py:66-76, 87-95, 107-114`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/antigravity.py#L66-L76)
- **Upstream / Docs Reference:**
  - Official JSON Configurations Guide (`json_configs.md:1-118`): *"JSON configuration files allow you to explicitly register and manage customizations that are stored outside the default discovery locations... Skills: `skills.json`, Plugins: `plugins.json`... Top-Level Fields: `entries`, `inherits`... with `include_only` and `exclude`."*
  - Embedded binary release notes: *"Added support for a root-level `agents.json` in plugins, so a plugin can declare its subagents the same way it declares skills and rules with `skills.json` and `rules.json`."*
  - Live empirical testing: Verified that skills and plugins declared in `.agents/skills.json` and `.agents/plugins.json` are loaded and reported by `agy -p "/skills" --output-format json` and `agy -p "/plugin list" --output-format json`.
- **agt-env Behavior:**
  [`antigravity.py`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/antigravity.py) only performs filesystem folder discovery in standard directories. It never checks for or reads `skills.json`, `plugins.json`, or `agents.json`.
- **Impact:**
  Projects or organizations that share customizations through explicit JSON manifests (e.g., pointing to monorepo submodules or shared team directories) will have their skills, plugins, and agents completely missing in `agt-env`.
- **Suggested Fix:**
  Implement manifest parsing for `skills.json`, `plugins.json`, and `agents.json` in workspace and global roots, expanding `entries` with single-level scans and filtering by `include_only` and `exclude`.

---

### `AG-07`: Missing Native Plugin Manifest `.gemini-plugin/plugin.json`

- **Severity:** Medium
- **Area:** Plugins
- **Source Reference:** [`src/agt_env/adapters/antigravity.py:273-275`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/antigravity.py#L273-L275) (`_plugin_manifest`)
- **Upstream / Docs Reference:**
  - Official Plugin Guide (`plugins.md:67-68`): Native plugins are defined by any directory containing `plugin.json`, `.antigravity-plugin/plugin.json`, or `.gemini-plugin/plugin.json`.
  - Live empirical testing: Setting up `plugins/gemini-style/.gemini-plugin/plugin.json`, `agy plugin list --json` successfully detects and enables the plugin.
- **agt-env Behavior:**
  In [`antigravity.py:273-275`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/antigravity.py#L273-L275):
  ```python
  def _plugin_manifest(plugin_dir: Path) -> Path:
      path = plugin_dir / "plugin.json"
      return path if os.path.exists(path) else plugin_dir / ".antigravity-plugin" / "plugin.json"
  ```
  It ignores `.gemini-plugin/plugin.json`.
- **Impact:**
  Plugins structured with `.gemini-plugin/` (migrated extensions or dual-targeted plugins) are ignored by `agt-env`.
- **Suggested Fix:**
  Add `.gemini-plugin/plugin.json` as a fallback manifest location in `_plugin_manifest`.

---

### `AG-08`: TUI Slash Commands Work Non-Interactively in Print Mode

- **Severity:** Medium
- **Area:** Documentation / Testing
- **Source Reference:** [`docs/agents/antigravity.md:19, 109, 111`](file:///home/zengxg/projects/agent-env/docs/agents/antigravity.md#L19), [`tests/test_antigravity_oracle.py`](file:///home/zengxg/projects/agent-env/tests/test_antigravity_oracle.py)
- **Upstream / Docs Reference:**
  - Antigravity Release Notes (v1.1.20+):
    *"Added non-interactive answers for more read-only slash commands in print mode, so `-p "/permissions"`, `/hooks`, `/help`, `/changelog` and `/config` each emit one tab-separated record per line or a structured payload under `--output-format json` and `stream-json` without starting an agent turn, spending quota or leaving a conversation behind..."*
  - Live empirical testing:
    - `agy -p "/skills" --output-format json` emits full skill metadata (name, description, path, builtin, model_invocable) instantly in a temporary `HOME` without sign-in.
    - `agy -p "/hooks" --output-format json` emits full hook configurations with events and actions.
    - `agy -p "/agents" --output-format json` emits custom agents list.
    - `agy -p "/plugin list" --output-format json` emits workspace and global plugins.
- **agt-env Behavior:**
  [`docs/agents/antigravity.md`](file:///home/zengxg/projects/agent-env/docs/agents/antigravity.md) repeatedly states that skills, workspace items, hooks, and precedence cannot be verified automatically because they require an interactive signed-in session:
  - Line 19: *"Skills and project items only appear inside a session, so they were checked once by hand..."*
  - Line 109: *"Precedence between same-named skills or servers isn't known, because the listings that show them need a signed-in session."*
  - Line 111: *"User-level and workspace hooks can't be compared, because no listing shows them and running them needs a session."*
- **Impact:**
  These incorrect assumptions caused oracle tests to omit skills, workspace items, hooks, and precedence comparisons, allowing bugs `AG-01`, `AG-02`, `AG-03`, `AG-04`, and `AG-06` to go undetected.
- **Suggested Fix:**
  Update [`docs/agents/antigravity.md`](file:///home/zengxg/projects/agent-env/docs/agents/antigravity.md) to document print-mode JSON commands and expand [`test_antigravity_oracle.py`](file:///home/zengxg/projects/agent-env/tests/test_antigravity_oracle.py) to compare skills and hooks automatically against `agy -p "/skills" --output-format json` and `agy -p "/hooks" --output-format json`.

---

### `AG-09`: Unnamed Skill Fallback Collapses All Unnamed Skills into `"SKILL"`

- **Severity:** Medium
- **Area:** Skills
- **Source Reference:** [`src/agt_env/adapters/antigravity.py:214-215`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/antigravity.py#L214-L215) (`_skill`), [`docs/agents/antigravity.md:83`](file:///home/zengxg/projects/agent-env/docs/agents/antigravity.md#L83)
- **Upstream / Docs Reference:**
  - Agent Skills Standard: A skill's identifier defaults to its directory name when omitted from frontmatter.
  - Live empirical testing: In `agy`, an unnamed skill is given the name `"SKILL"`. When multiple skills lack `name`, they collide under `"SKILL"`, and only one is loaded.
- **agt-env Behavior:**
  In [`antigravity.py:214-215`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/antigravity.py#L214-L215):
  ```python
  if skill.fields is not None:
      skill.fields["name"] = skill.fields.get("name") or _UNNAMED_SKILL
  ```
  This overrides `skill_item`'s default fallback to `skill.default_name` (the folder name). All unnamed skills are given `name = "SKILL"`. If multiple exist, they appear with identical names without indicating their source folder or collision status.
- **Impact:**
  Confusing item listings when multiple unnamed skills exist in workspace or plugin directories.
- **Suggested Fix:**
  Preserve the folder name or note the unnamed fallback in findings so users can distinguish colliding unnamed skills.

---

### `AG-10`: Primary `config_path` Resolves to Directory Rather Than Config File

- **Severity:** Medium
- **Area:** Configuration
- **Source Reference:** [`src/agt_env/adapters/antigravity.py:49-61`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/antigravity.py#L49-L61)
- **Upstream / Docs Reference:**
  - [`docs/PRD.md`](file:///home/zengxg/projects/agent-env/docs/PRD.md) Section 5.1: *"The configuration file that determined the agent's presence or configuration, if one exists."*
- **agt-env Behavior:**
  In [`antigravity.py:49-61`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/antigravity.py#L49-L61), `first_existing` is passed `cli_dir` (`~/.gemini/antigravity-cli`) as its very first argument. Since `cli_dir` is created when `agy` runs, `report.config_path` always resolves to `.../.gemini/antigravity-cli` (a directory), overshadowing actual configuration files such as `settings.json` or `mcp_config.json`.
- **Impact:**
  Consumers of `AgentReport.config_path` receive a generic directory path rather than the specific configuration file that controls agent settings.
- **Suggested Fix:**
  Pass `cli_dir / "settings.json"` to `first_existing` instead of `cli_dir`.

---

### `AG-11`: Workspace Status Verification Diverges on Symlinked Paths in `trustedWorkspaces`

- **Severity:** Medium
- **Area:** Workspaces / Trust
- **Source Reference:** [`src/agt_env/adapters/antigravity.py:286-295`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/antigravity.py#L286-L295) (`_workspace_status`)
- **Upstream / Docs Reference:**
  - Live empirical testing:
    - If `trustedWorkspaces` contains `"/tmp/real_proj"`, running `agy` with `cwd="/tmp/sym_proj"` succeeds (resolves `cwd` to realpath).
    - If `trustedWorkspaces` contains `"/tmp/sym_proj"`, running `agy` with `cwd="/tmp/real_proj"` prompts for trust and treats the workspace as untrusted (`""`). `agy` does not resolve the paths listed in `trustedWorkspaces`.
- **agt-env Behavior:**
  In [`antigravity.py:291-293`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/antigravity.py#L291-L293):
  ```python
  if isinstance(trusted, list) and any(
      isinstance(path, str) and os.path.realpath(path) == real for path in trusted
  ):
      return Status.ENABLED
  ```
  `agt-env` calls `os.path.realpath(path)` on every entry in `trusted`. When `trusted` contains `sym_project` and cwd is `real_project`, `agt-env` marks the workspace as `Status.ENABLED`, while the real `agy` binary treats it as untrusted.
- **Impact:**
  `agt-env` reports workspace items as active/enabled when `agy` will prompt and refuse to load them.
- **Suggested Fix:**
  Align trust checking with `agy`'s exact semantics: check `real in trusted` or match raw string against `str(cwd)`.

---

### `AG-12`: Dual Transport MCP Servers Unconditionally Assume `stdio`

- **Severity:** Medium
- **Area:** MCP
- **Source Reference:** [`src/agt_env/adapters/antigravity.py:301-305`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/antigravity.py#L301-L305) (`_server_details`)
- **Upstream / Docs Reference:**
  - Official MCP Guide (`mcp_servers.md:28-66`): Transport is either `stdio` (requires `command`) or `streamable_http` (requires `url` / `serverUrl`).
- **agt-env Behavior:**
  In [`antigravity.py:301-305`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/antigravity.py#L301-L305):
  ```python
  if isinstance(entry.get("command"), str):
      return stdio_details(entry["command"], entry.get("env"))
  url = entry.get("serverUrl", entry.get("url"))
  if isinstance(url, str):
      return remote_details("streamable_http", url, entry.get("headers"))
  ```
  If a server entry contains both `command` (e.g., a local helper/bridge script) and `url`, `antigravity.py` unconditionally classifies it as `stdio` without checking `type` or validating whether it is an HTTP server.
- **Impact:**
  Configurations with both keys can be misclassified as stdio servers rather than remote endpoints.
- **Suggested Fix:**
  Inspect `entry.get("type")` if declared, or flag conflicting dual-transport declarations.

---

### `AG-13`: Missing Subagent Instruction Verification

- **Severity:** Low
- **Area:** Subagents
- **Source Reference:** [`src/agt_env/adapters/antigravity.py:129-143`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/antigravity.py#L129-L143) (`_agents`)
- **Upstream / Docs Reference:**
  - Official Subagents Guide: Subagents require prompt instructions / markdown body to guide the model.
- **agt-env Behavior:**
  [`antigravity.py:139`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/antigravity.py#L139) only verifies: `default if name and description else Status.BROKEN`. An agent file with frontmatter but an empty markdown body is reported as `Status.ENABLED`.
- **Impact:**
  Empty skeleton agent files are reported as enabled subagents.
- **Suggested Fix:**
  Verify that the markdown body or instruction content is non-empty before marking the subagent enabled.

---

### `AG-14`: Subagents with Duplicate Names Across Scopes Both Reported as Enabled

- **Severity:** Low
- **Area:** Subagents
- **Source Reference:** [`src/agt_env/adapters/antigravity.py:107-114`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/antigravity.py#L107-L114)
- **Upstream / Docs Reference:**
  - Live empirical testing: When `dupe.md` exists in both `.agents/agents` and `~/.gemini/config/agents`, `agy agents` lists `dupe` only once (workspace overriding global).
- **agt-env Behavior:**
  Lines 107-114 append all discovered subagents without deduplication or `mark_shadowed`. Both the user and project subagents are reported as `Status.ENABLED`.
- **Impact:**
  Duplicates appear in subagent listings when only one subagent is active in `agy`.
- **Suggested Fix:**
  Apply `mark_shadowed` across subagents, ordered Project > User.

---

### `AG-15`: Confirmation of `~/.gemini/skills` Discovery

- **Severity:** Low
- **Area:** Documentation
- **Source Reference:** [`docs/agents/antigravity.md:110`](file:///home/zengxg/projects/agent-env/docs/agents/antigravity.md#L110)
- **Upstream / Docs Reference:**
  - Live empirical testing: Running `agy -p "/skills" --output-format json` with a skill in `~/.gemini/skills/shared-skill/SKILL.md` discovers and loads the skill:
    ```json
    {"name": "shared-skill", "path": ".../.gemini/skills/shared-skill/SKILL.md", "builtin": false, "model_invocable": true}
    ```
- **agt-env Behavior:**
  The code in `antigravity.py:45` already scans `gemini_dir / "skills"`, but [`antigravity.md:110`](file:///home/zengxg/projects/agent-env/docs/agents/antigravity.md#L110) records it as an unverified assumption: *"No skills were placed there, so we haven't confirmed that it loads them."*
- **Impact:**
  Documentation uncertainty regarding an established feature.
- **Suggested Fix:**
  Update `antigravity.md` to remove the unconfirmed assumption and mark `~/.gemini/skills` as tested and verified.

---

### `AG-16`: Confirmation of Global `config.json` Workspace Plugin Disabling

- **Severity:** Low
- **Area:** Documentation
- **Source Reference:** [`docs/agents/antigravity.md:112`](file:///home/zengxg/projects/agent-env/docs/agents/antigravity.md#L112)
- **Upstream / Docs Reference:**
  - Live empirical testing: Creating a workspace plugin in `.agents/plugins/ws-plugin` and writing `{"plugins": {"ws-plugin": {"enabled": false}}}` to `~/.gemini/config/config.json`, `agy -p "/plugin list" --output-format json` confirms the workspace plugin is reported as `"enabled": false`.
- **agt-env Behavior:**
  The code in `antigravity.py:84-91` already applies global `config.json` plugin state to workspace plugins, but [`antigravity.md:112`](file:///home/zengxg/projects/agent-env/docs/agents/antigravity.md#L112) lists it as an unconfirmed assumption.
- **Impact:**
  Documentation uncertainty regarding an established feature.
- **Suggested Fix:**
  Update `antigravity.md` to mark this rule as tested and verified.

---

### `AG-17`: Additional Credential & Sensitive State Locations in Safety Audit

- **Severity:** Low
- **Area:** Safety / Tests
- **Source Reference:** [`tests/test_safety.py:20-23`](file:///home/zengxg/projects/agent-env/tests/test_safety.py#L20-L23)
- **Upstream / Docs Reference:**
  - Antigravity stores persistent CLI state, session caches, and potential session credentials in `~/.gemini/antigravity-cli/jetski_state.pbtxt`, `jetbox_summaries_proto.pb`, and `conversation_summaries.db`.
- **agt-env Behavior:**
  [`tests/test_safety.py:20-23`](file:///home/zengxg/projects/agent-env/tests/test_safety.py#L20-L23) audits `.gemini/oauth_creds.json`, `.gemini/google_accounts.json`, and `.gemini/antigravity-cli/oauth_creds.json`. It does not explicitly check that `jetski_state.pbtxt` or conversation databases remain unopened and isolated.
- **Impact:**
  Omission in the safety audit test fixture assertions.
- **Suggested Fix:**
  Add `jetski_state.pbtxt` and session database files to the audited paths in `test_safety.py`.

---

## Resolution

Every claim was checked against `agy` 1.4.12 and its bundled customization guide, in a temporary `HOME`. Of 17 findings, 15 led to a change. AG-10 and AG-13 were left as they are. Rules and evidence are in `docs/agents/antigravity.md`.

| ID | Outcome | What differed from the review |
|---|---|---|
| AG-01 | Fixed | `agy plugin list` lists them by bare name, and the changelog quoted about `<plugin>_<server>` names wasn't found in this version's changelog. So they're listed by bare name and don't clash with global servers |
| AG-02 | Fixed | A hook in the wrong shape still appears in `/hooks`, with nothing to run, so it's `broken`. A handler of an unknown type, or an event that isn't a list, drops the whole file |
| AG-03 | Fixed | Applies when the workspace is trusted. Among user directories, `antigravity-cli` beats `config`, which beats `~/.gemini/skills`. Agents and servers follow the docs, as no listing shows which wins. Hooks are the reverse: the later one of a name wins, and a plugin's beats the global file |
| AG-04 | Fixed | |
| AG-05 | Fixed | `.agent` beats `.agents`, which beats `_agent` and then `_agents` |
| AG-06 | Fixed | Declared skills beat discovered ones in both places, unlike the docs. `exclude` and `include_only` match names exactly, `~/` paths didn't load, and a non-object entry drops the whole file. A declared plugin's skills have no prefix |
| AG-07 | Fixed | |
| AG-08 | Fixed | `/mcp` isn't available in print mode, and print mode ignores trust, so trust is compared with `agy agents` |
| AG-09 | Fixed | `agy` does call them all `SKILL`, so the name stays. The collision is now shown: all but the first are `shadowed` |
| AG-10 | **Not valid, unchanged** | The quoted PRD text isn't in `docs/PRD.md`, and every adapter shows a directory there. Using `settings.json` would hide an installed agent that has no settings yet |
| AG-11 | Fixed | |
| AG-12 | Fixed | A URL wins, as `agy mcp list` shows |
| AG-13 | **Not valid, unchanged** | An agent with an empty body is listed by `/agents` |
| AG-14 | Fixed | By the docs, as the listing shows names only |
| AG-15 | Fixed | |
| AG-16 | Fixed | |
| AG-17 | Fixed | |

Testing also found that a manifest's `"disabled": true` ships a plugin turned off, which `config.json` can override. That's handled too.

