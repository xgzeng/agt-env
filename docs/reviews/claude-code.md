# Claude Code Discovery & Documentation Review

Audit of `claude-code` resource discovery across `src/agt_env/adapters/claude_code.py` (and shared helpers in `adapters/base.py`, `skills.py`, `mcp.py`, and `hooks.py`), `docs/agents/claude-code.md`, `docs/agents/README.md`, `docs/PRD.md`, and `tests/`.

**Verification baseline:** Claude Code `2.1.316` (isolated temporary-`HOME` CLI runs via `claude mcp list`, `claude plugin list --json`, `claude plugin details`, and `claude -p hi --output-format stream-json --verbose`, cross-checked against official documentation at `code.claude.com/docs/en/*` and `2.1.316`'s embedded JS bundle).

---

## Summary of Findings

| Done | ID | Severity | Area | Summary |
|---|---|---|---|---|
| [x] | CC-01 | High | MCP / Resilience | `_rule_matches` crashes with `TypeError` when an MCP server has non-iterable `"args"` (e.g. `null`) and `serverCommand` policy rules exist |
| [x] | CC-02 | High | Commands / Agents / Hooks | Running in `HOME` (or when project `.claude` resolves to user config dir) duplicates commands, subagents, and hooks, and falsely marks winners/losers `shadowed` |
| [x] | CC-03 | Medium | Configuration | `CLAUDE_CONFIG_DIR` explicitly set to `~/.claude` reads `~/.claude.json` instead of `~/.claude/.claude.json` |
| [x] | CC-04 | High | Configuration | Managed drop-in directory `<managed_dir>/managed-settings.d/*.json` is neither read nor documented |
| [ ] | CC-05 | High | Settings / Trust | Untrusted project settings (`.claude/settings.json`, `.claude/settings.local.json`) are merged unconditionally into `_Settings` |
| [x] | CC-06 | Medium | Hooks / Resilience | Unreadable/corrupt `.claude.json` yields `pending` instead of `unknown` for project hooks |
| [x] | CC-07 | Low | Configuration / Paths | `ClaudeCode.scan` matches `.claude.json` `projects` and `installed_plugins.json` `projectPath` with `str(context.cwd)` rather than `realpath`, and omits ancestor git-root trust inheritance |
| [x] | CC-08 | High | Skills | Hidden skill directories (`.claude/skills/.name/SKILL.md`, `~/.claude/skills/.name/SKILL.md`) are skipped, though Claude Code loads them |
| [x] | CC-09 | High | Skills | Non-plugin skill identity uses frontmatter `name` instead of directory name, breaking reported names, `skillOverrides` matching, and name validation |
| [x] | CC-10 | Medium | Skills | Skill frontmatter `disable-model-invocation`, `user-invocable`, and conditional `paths` are ignored |
| [x] | CC-11 | Medium | Permissions | `_DENY_RULE` fails on `Skill(plugin:*)` wildcards, prefix wildcards (`Skill(prefix *)` / `Skill(prefix:*)`), and leading slashes (`Skill(/name)`) |
| [x] | CC-12 | Low | Skills / Plugins | Case-insensitive `SKILL.md` matching and `skills-dir` ("skills-as-plugins") directories containing `.claude-plugin/plugin.json` are unimplemented and undocumented |
| [x] | CC-13 | Medium | Commands | Managed/enterprise commands at `<managed_dir>/.claude/commands` are neither scanned nor documented |
| [x] | CC-14 | Medium | Commands | Subdirectories in `commands/` containing `SKILL.md` are emitted as `<dir>:SKILL` (plus sibling `.md` files) instead of `<dir>` |
| [x] | CC-15 | Low | Commands / Docs | User-over-project command precedence and cross-scope skill-over-command precedence are implemented in code without evidence rows in `docs/agents/claude-code.md` |
| [x] | CC-16 | High | Managed MCP | `managedMcpServers` accepts `stdio`, `ws`, `http://` (non-HTTPS), and `${VAR}` entries as `enabled`, though Claude Code rejects them |
| [x] | CC-17 | High | Managed MCP | `allowedMcpServers` wrongly disables `managedMcpServers` and literal `managed-mcp.json` servers |
| [x] | CC-18 | Medium | Managed MCP | Corrupt/non-file `managed-mcp.json` disables user/project servers instead of `unknown`, while corrupt `.claude.json` wrongly marks `Scope.SYSTEM` servers `unknown` |
| [x] | CC-19 | Medium | MCP | `mark_shadowed` omits `managedMcpServers`/`managed-mcp.json`, and `remote_details` accepts arbitrary non-URL strings as `enabled` |
| [x] | CC-20 | High | Plugins / PRD | Plugin-bundled MCP servers (`.mcp.json` / `plugin.json` `mcpServers`) are omitted, violating PRD Section 8 Metric 1 (`claude mcp list` parity) and Section 5.1 |
| [x] | CC-21 | Medium | Plugins | `manifest["commands"]` dict entries emit invalid/escaping entries as `enabled` and drop `description`/`content` metadata |
| [x] | CC-22 | Medium | Plugins | Duplicate manifest paths (`./skills`, `./hooks/hooks.json`), multi-scope plugin installs, and `_quiet_json` cause duplicate items and swallowed corrupt-JSON warnings |
| [x] | CC-23 | Medium | Plugins / Hooks | Subdirectory plugin skills use frontmatter `name` without sanitization; project-scoped plugin hooks ignore workspace trust; `allowManagedHooksOnly` blocks managed-enabled plugin hooks |
| [x] | CC-24 | Low | Subagents | Invalid subagent `name` (`-foo`, `a:b`, `>256` chars) produces the misleading finding `"needs a name and a description, which Claude Code requires"` |
| [x] | CC-25 | Medium | Hooks | Invalid HTTP hook URLs are reported `enabled`; `allowedHttpHookUrls`, `strictPluginOnlyCustomization`, and `PreToolUse`/`PermissionRequest` guard-hook file rejection are ignored |
| [x] | CC-26 | Medium | Docs / Tests | `docs/agents/claude-code.md` contradicts itself on non-interactive skill listing, and `tests/test_claude_oracle.py` ignores `init["skills"]` |
| [x] | CC-27 | Low | Docs | Non-standard evidence label on managed subagents and undocumented plugin subagent optional-`description` behavior |
| [x] | CC-28 | Low | Docs / Safety Tests | `docs/agents/README.md` leaves `.agents/skills` as `"not documented"`, and `tests/test_safety.py` omits `$CLAUDE_CONFIG_DIR/.credentials.json` |

---

## 1. Runtime Crashes & Resilience

### CC-01 — Unhandled `TypeError` crash in MCP `serverCommand` policy matching (High)
- **Where:** `src/agt_env/adapters/claude_code.py:647-649`
- **Current behavior:** `_rule_matches` evaluates:
  ```python
  if isinstance(command, list) and command == [entry.get("command"), *entry.get("args", [])]:
  ```
  If any configured MCP server has `"args": null`, an integer, or a boolean—and any settings layer defines an `allowedMcpServers` or `deniedMcpServers` rule with `serverCommand`—`agt-env` crashes with `TypeError: Value after * must be an iterable, not NoneType` instead of returning items or a warning. In addition, if `"args": "--flag"` (a string), `*entry.get("args", [])` unpacks it character-by-character (`["cmd", "-", "-", "f", ...]`).
- **Expected / Claude Code behavior:** Malformed or unexpected field types in one entry must never crash the scan (`FR-17`, `FR-19`, PRD Section 8 Metric 5).

---

## 2. Configuration, Overrides, & Workspace Trust

### CC-02 — Running in `HOME` duplicates commands, subagents, and hooks (High)
- **Where:** `src/agt_env/adapters/claude_code.py:111-117`, `158-167`, `170-180`, `182-192`
- **Current behavior:** `ClaudeCode.scan` deduplicates skill roots with `unique_roots` (`line 127`), because `cwd` may be `HOME` (making `project_dir` `~/.claude` identical to `user_dir` `~/.claude`), or `CLAUDE_CONFIG_DIR` may point to `<cwd>/.claude`. However, `commands`, `agents`, and `settings` hooks do **not** deduplicate roots/files:
  - Every command in `~/.claude/commands` is emitted twice: `U/command <name> (enabled)` and `P/command <name> (shadowed by ~/.claude/commands/<name>.md)`.
  - Every subagent in `~/.claude/agents` is emitted twice: `P/subagent <name> (enabled)` and `U/subagent <name> (shadowed by ./.claude/agents/<name>.md)`.
  - Every hook in `~/.claude/settings.json` is emitted twice (`U/hook` and `P/hook`), and `_Settings` loads `~/.claude/settings.json` twice (`self.user` and `self.project`).
- **Confirmed Claude Code behavior:** Tested live in `HOME`: `claude -p` loads each command, subagent, and hook from `~/.claude` once.

### CC-03 — `CLAUDE_CONFIG_DIR=~/.claude` reads the wrong `.claude.json` (Medium)
- **Where:** `src/agt_env/adapters/claude_code.py:97-98`, `src/agt_env/adapters/base.py:45`
- **Current behavior:** `config_dir()` returns `override = None` whenever `os.path.realpath(path) == os.path.realpath(default)` (intended so `(set by CLAUDE_CONFIG_DIR)` is hidden in headers when pointing at the default directory). `claude_code.py:98` then computes:
  ```python
  user_state_path = (user_dir if override else context.home) / ".claude.json"
  ```
  When a user sets `CLAUDE_CONFIG_DIR=$HOME/.claude`, `override` is `None`, so `agt-env` reads `$HOME/.claude.json`.
- **Confirmed Claude Code behavior:** Tested live with `CLAUDE_CONFIG_DIR=$HOME/.claude claude mcp list`: Claude Code checks whether `CLAUDE_CONFIG_DIR` is set in the environment and reads `$HOME/.claude/.claude.json`, ignoring `$HOME/.claude.json`.

### CC-04 — `<managed_dir>/managed-settings.d/*.json` drop-in files are ignored (High)
- **Where:** `src/agt_env/adapters/claude_code.py:115, 428-437`, `docs/agents/claude-code.md:19`
- **Current behavior:** Only `<managed_dir>/managed-settings.json` is read and documented.
- **Confirmed Claude Code behavior:** Both official documentation and Claude Code `2.1.316` (`getDropInDir()` / `Tbr`) load `managed-settings.json` first and then merge all non-hidden `*.json` files and symlinks (`name.endswith(".json") and not name.startswith(".")`) in `<managed_dir>/managed-settings.d/` in alphabetical order, with later drop-in files overriding earlier ones for scalar settings and merging arrays/objects.

### CC-05 — Untrusted project settings are merged unconditionally into `_Settings` (High)
- **Where:** `src/agt_env/adapters/claude_code.py:433-458, 469-515, 618-630`
- **Current behavior:** `_Settings` merges `self.project` (`.claude/settings.json`) and `self.local` (`.claude/settings.local.json`) regardless of `trusted`. Only MCP approvals (`_McpRules._approval`, `lines 608-610`) and settings-file hook execution (`hook_status`, `line 488`) check `trusted`.
- **Confirmed Claude Code behavior:** In Claude Code `2.1.316`, `.claude/settings.json` and `.claude/settings.local.json` count only once the workspace is trusted (and `.claude/settings.local.json` also has git-tracking/symlink safety checks). Because `agt-env` merges untrusted project settings unconditionally, an untrusted cloned repo can:
  - flip `enabledPlugins` to `true` (causing `agt-env` to report installed plugins and their bundled skills/commands/agents/hooks as `enabled`),
  - set `skillOverrides`, `permissions.deny`, `disableAllHooks`, `allowedMcpServers`, or `deniedMcpServers` (causing `agt-env` to report user skills, subagents, hooks, and MCP servers as `disabled`).

### CC-06 — Corrupt `.claude.json` yields `pending` instead of `unknown` for project hooks (Medium)
- **Where:** `src/agt_env/adapters/claude_code.py:118-125, 474-489`
- **Current behavior:** When `.claude.json` fails to parse (`state_ok == False`), `read_json` emits a stderr warning and returns `{}`. `trusted` then evaluates to `False` (unless `cwd` is `HOME`/`user_dir`), and `settings.hook_status(Scope.PROJECT, trusted)` checks `self.reliable` (which only tracks the four `settings.json` files, not `state_ok`) and returns `Status.PENDING`.
- **Expected behavior:** Per `FR-17` and `AGENTS.md` (*"Status needs evidence. Use `unknown` rather than guessing ... A config file that can't be read gives a warning and `unknown`"*), whether the project folder is trusted cannot be determined when `.claude.json` is unreadable, so project hooks must be `Status.UNKNOWN` (matching `_McpRules._item` at `line 577`, which already checks `self.state_ok`).

### CC-07 — Non-canonical `context.cwd` lookup and workspace trust inheritance (Low)
- **Where:** `src/agt_env/adapters/claude_code.py:120, 366`
- **Current behavior:**
  1. `projects.get(str(context.cwd))` (`line 120`) and `install.get("projectPath") != str(context.cwd)` (`line 366`) compare raw `str(context.cwd)` without `os.path.realpath`, even though `line 123` uses `os.path.realpath(context.cwd)`. `cli.py:203` masks this by calling `.resolve()`, but direct callers of `ClaudeCode().scan(Context(...))` on a symlinked project path miss `.claude.json` project state (`trusted`, local MCP servers, `disabledMcpServers`) and local-scope plugins.
  2. Tested live and confirmed in `2.1.316` (`Vh`/`Yh`): Claude Code stores canonical `realpath` keys in `.claude.json` `projects` and `installed_plugins.json` `projectPath`, and also inherits `hasTrustDialogAccepted: true` from a trusted parent directory up to the git repository root. (`FR-2` intentionally avoids searching parent directories for items, but `docs/agents/claude-code.md` does not note that trust in `.claude.json` can be recorded on an ancestor/repo-root entry rather than exact `cwd`.)

---

## 3. Skills Discovery, Identity, & Status

### CC-08 — Hidden skill directories are skipped (High)
- **Where:** `src/agt_env/adapters/claude_code.py:134`, `src/agt_env/skills.py:35, 58`
- **Current behavior:** `ClaudeCode.scan` calls `scan_skills(root, skip=frozenset({"synced"}))` with the default `include_hidden=False`, skipping any skill directory starting with `.` (such as `~/.claude/skills/.my-skill/SKILL.md` or `.claude/skills/.my-skill/SKILL.md`). Notably, `_plugin_skills` (`line 411`) passes `include_hidden=True`.
- **Confirmed Claude Code behavior:** Tested live against `2.1.316`: `~/.claude/skills/.hid_skill/SKILL.md` is loaded and listed in both `init["skills"]` and `init["slash_commands"]`.

### CC-09 — Non-plugin skill identity uses frontmatter `name` instead of directory name (High)
- **Where:** `src/agt_env/adapters/claude_code.py:331, 333-336, 501`, `docs/agents/claude-code.md:35, 37, 39, 114`
- **Current behavior:** `claude_code.py:331` computes `name = fields.get("name") or skill.path.name`, and `docs/agents/claude-code.md:114` assumes: *"It lists a skill by its folder name, even where `name` differs, so a differing `name` still follows the docs."*
- **Confirmed Claude Code behavior:** Tested live against `2.1.316` and verified in Claude Code's skill loader (`VRr` / `_Ce` / `TZe`):
  1. **Canonical name is always the directory name:** A personal, project, or enterprise skill at `<root>/<dir>/SKILL.md` with frontmatter `name: custom` has canonical `skillName = <dir>`; `frontmatter.name` only sets `displayName`. Both `init["skills"]` and `init["slash_commands"]` list `<dir>`, never `custom`.
  2. **`skillOverrides` keys by directory name, not frontmatter `name`:** Tested live with `dir_one/SKILL.md` (`name: fm_one`) and `dir_two/SKILL.md` (`name: fm_two`): setting `"skillOverrides": {"fm_one": "off", "dir_two": "off"}` turned off `dir_two` and left `dir_one` active. `agt-env` reports the exact opposite (`fm_one` disabled, `fm_two` enabled).
  3. **Shadowing keys by directory name, not frontmatter `name`:** Two skills in different scopes conflict when their directory names match, not when only their frontmatter `name` matches.
  4. **Name validation mismatch:** Tested live:
     - `bad_angle/SKILL.md` with `name: "a<b>c"` **loads** in Claude Code as `bad_angle` (`agt-env` falsely reports `a<b>c` as `broken`).
     - `bad<dir>/SKILL.md` with `name: clean` is **refused** by Claude Code (`agt-env` falsely reports `clean` as `enabled`).
     - Reserved names (`anthropic-skills`, `anthropic-skills:*`, plus Unicode/case foldings in `TZe`) are checked by Claude Code against **both** directory name and frontmatter `name`; `ok_dir/SKILL.md` with `name: anthropic-skills` is refused by Claude Code, whereas `agt-env` would miss a reserved directory name if overridden by a non-reserved frontmatter `name`.

### CC-10 — Skill frontmatter `disable-model-invocation`, `user-invocable`, and `paths` are ignored (Medium)
- **Where:** `src/agt_env/adapters/claude_code.py:329-352, 414-422`, `docs/agents/claude-code.md:26-40`
- **Current behavior:** Frontmatter invocation and activation controls are neither inspected nor documented.
- **Confirmed Claude Code behavior:**
  - Tested live against `2.1.316`:
    - `user-invocable: false` removes the skill from `slash_commands` (`/` menu) while keeping it in `skills` (model `Skill` tool).
    - `disable-model-invocation: true` keeps it in `slash_commands` while hiding it from model auto-invocation (equivalent to `skillOverrides: "user-invocable-only"`).
    - Setting **both** `disable-model-invocation: true` and `user-invocable: false` removes the skill from **both** `init["skills"]` and `init["slash_commands"]` (neither user nor model can invoke it, equivalent to `skillOverrides: "off"`), yet `agt-env` reports `Status.ENABLED`.
  - Skills with non-trivial `paths:` globs in frontmatter (`VRr` -> `conditionalSkills`) are held out of active skills at startup and loaded conditionally only when matching files are touched (paralleling `<subdir>/.claude/skills`, which `docs/agents/claude-code.md:30` documents, while `paths:` is undocumented).

### CC-11 — `permissions.deny` `Skill(...)` / `Agent(...)` matching misses wildcards and leading `/` (Medium)
- **Where:** `src/agt_env/adapters/claude_code.py:48, 460-467`, `docs/agents/claude-code.md:40, 86, 129`
- **Current behavior:**
  ```python
  _DENY_RULE = re.compile(r"^(Skill|Agent)\((.+?)(?:(?: |:)\*)?\)$")
  ...
  if match and match[1] == tool and match[2] in (name, "*"):
  ```
  1. For `Skill(my-plugin:*)`, `match[2]` is `"my-plugin"`, and `"my-plugin" in ("my-plugin:alpha", "*")` is `False`—so plugin-prefix deny rules (`Skill(p:*)`) fail to mark `p:alpha` as `disabled`, only matching an exact skill named `"my-plugin"`.
  2. For prefix rules like `Skill(deploy *)` or `Skill(ns:*)`, Claude Code (`dAe` / `jne`) strips `:*` or ` *` as a prefix and matches `target === ruleName || target.startsWith(prefix)` (with plugin/packaging boundary checks in `jjo`), whereas `agt-env` only checks exact equality with `match[2]`.
  3. Claude Code (`dAe`) strips a leading `/` in `Skill(/name)` or `Skill(/p:*)`; `_DENY_RULE` keeps `/` in `match[2]`, failing to match `name`.

### CC-12 — Case-insensitive `SKILL.md` and `skills-dir` plugins are undocumented/unhandled (Low)
- **Where:** `src/agt_env/skills.py:9, 72`, `docs/agents/claude-code.md:28-40, 63-75`
- **Confirmed Claude Code behavior:**
  1. Several Claude Code skill/command checks match `/^skill\.md$/i` case-insensitively (`fZr`, `EZe`), whereas `skills.py` checks exact `"SKILL.md"` (`os.path.lexists(path / "SKILL.md")`).
  2. Claude Code `2.1.316` (`W2r`, `skills-as-plugins`) scans `~/.claude/skills/<dir>` and (when trusted) `.claude/skills/<dir>` for `.claude-plugin/plugin.json` and loads qualifying folders as plugins under `<name>@skills-dir` (controlled by `enabledPlugins` / `defaultEnabled`). Neither `claude_code.py` nor `docs/agents/claude-code.md` mentions this feature (not even under `"Not covered by agt-env"`).

---

## 4. Commands Discovery & Precedence

### CC-13 — Managed commands (`<managed_dir>/.claude/commands`) are not scanned or documented (Medium)
- **Where:** `src/agt_env/adapters/claude_code.py:158-165`, `docs/agents/claude-code.md:90-100`
- **Current behavior:** `ClaudeCode.scan` scans `<managed_dir>/.claude/skills` (`line 129`) and `<managed_dir>/.claude/agents` (`line 173`), but `commands` (`lines 160-163`) only scans `user_dir / "commands"` and `project_dir / "commands"`.
- **Confirmed Claude Code behavior:** Claude Code's `qRr` / `Hue("commands", ...)` / `IEt("policySettings", "commands")` resolves `<managed_dir>/.claude/commands` alongside user and project commands.

### CC-14 — `SKILL.md` inside `commands/` subdirectories is misnamed `<dir>:SKILL` (Medium)
- **Where:** `src/agt_env/adapters/claude_code.py:164, 197-208, 259-261, 684-687`, `src/agt_env/skills.py:137-167`
- **Current behavior:** `find_markdown` collects every `*.md` file under `commands/`, and `_command_name` joins parent folders with `file.stem`. A directory `commands/review/SKILL.md` (with optional helper `commands/review/NOTES.md`) is reported as two commands: `review:SKILL` and `review:NOTES`.
- **Confirmed Claude Code behavior:** In `2.1.316` (`jRr`/`WRr` for personal/project commands and `mXo`/`DZt` for plugin commands), if a directory under `commands/` contains a file matching `/^skill\.md$/i`, Claude Code selects only that `SKILL.md` (ignoring sibling `.md` files in the same folder) and names the command after its directory (`review`, or `group:review` if nested), not `review:SKILL`.

### CC-15 — Command shadowing rules lack documented evidence in `docs/agents/claude-code.md` (Low)
- **Where:** `src/agt_env/adapters/claude_code.py:157-166`, `docs/agents/claude-code.md:90-100`
- **Current behavior:** `claude_code.py:160-166` orders commands `[Scope.USER, Scope.PROJECT]` and runs `mark_shadowed([*skills, *commands])`, so:
  - a personal command shadows a same-named project command, and
  - a project skill shadows a same-named personal command (tested live against `2.1.316`: confirmed that `.claude/skills/conflict_up/SKILL.md` wins over `~/.claude/commands/conflict_up.md`).
- **Problem:** `AGENTS.md` and `FR-8` require that *"shadowed needs a precedence rule with evidence, recorded in the agent's doc page"*. `docs/agents/claude-code.md:96` only records *"A skill beats a same-named command"* (`Doc`) without specifying cross-scope behavior or personal-vs-project command precedence.

---

## 5. MCP Servers & Enterprise Policy

### CC-16 — `managedMcpServers` accepts `stdio`, `ws`, `http://`, and `${VAR}` entries as `enabled` (High)
- **Where:** `src/agt_env/adapters/claude_code.py:541-558, 574, 654-666`, `docs/agents/claude-code.md:49, 52`
- **Current behavior:** `managedMcpServers` entries from `managed-settings.json` are validated with the same `_server_details` helper as `.mcp.json` and `.claude.json`, reporting `stdio` (`command`), `ws`, `streamable-http`, plaintext `http://`, and `${VAR}`-interpolated entries as `Status.ENABLED`.
- **Confirmed Claude Code behavior:** Official documentation and `2.1.316`'s `managedMcpServers` validator (`fr()` / `to()`) enforce strict security rules on `managedMcpServers` (unlike `managed-mcp.json`):
  - Only `"type": "http"` and `"type": "sse"` are accepted (`"managed settings can only deliver \"http\" or \"sse\" servers"`).
  - Program-execution keys such as `"command"` / `"args"` / `"env"` are rejected.
  - `"url"` must be a valid `https://` URL (`"managed settings servers must use a valid https:// url"`).
  - `${VAR}` references and control/invisible format characters anywhere in keys or values are rejected without loading the entry.

### CC-17 — `allowedMcpServers` wrongly blocks `Scope.SYSTEM` managed servers (High)
- **Where:** `src/agt_env/adapters/claude_code.py:579, 618-630`, `docs/agents/claude-code.md:59`
- **Current behavior:** `_McpRules._item` calls `self._policy_blocks(name, entry)` for all scopes, including `Scope.SYSTEM`. When `allowedMcpServers` is set (for example `[]` to block all user-added MCP servers, or an allowlist of user-selectable servers), `agt-env` marks `managedMcpServers` and `managed-mcp.json` entries `Status.DISABLED` unless they also match `allowedMcpServers`.
- **Confirmed Claude Code behavior:** Per `2.1.316`'s settings schema and policy engine:
  - `deniedMcpServers` blocks matching servers across **all** scopes, including enterprise/managed.
  - `allowedMcpServers` governs user-added servers (`user`, `project`, `local`, plugins, `--mcp-config`, connectors); **`managedMcpServers` and `managed-mcp.json` entries without `${VAR}` in `command`, `args`, `url`, or `headers` are automatically allowed without being listed in `allowedMcpServers`** (only `managed-mcp.json` entries containing `${VAR}` must match `allowedMcpServers`).

### CC-18 — Corrupt `managed-mcp.json` or `.claude.json` produces wrong MCP statuses (Medium)
- **Where:** `src/agt_env/adapters/claude_code.py:534, 537, 540-546, 577-580`
- **Current behavior:**
  1. `self.exclusive = os.path.exists(self.managed_path)` (`line 534`) is `True` even if `managed-mcp.json` is a directory, unreadable (`PermissionError`), or invalid JSON. `line 537` ignores the `ok` boolean from `read_json(self.managed_path, self.warnings)`, and `lines 540, 545` hardcode `source_ok = True` for both `managed-mcp.json` and `managedMcpServers`. If `managed-mcp.json` is unreadable or corrupt, `agt-env` marks every user/project MCP server `Status.DISABLED` rather than `Status.UNKNOWN`.
  2. Conversely, `line 577` checks `if not (source_ok and self.settings.reliable and self.state_ok): status = Status.UNKNOWN` for **every** scope—so a corrupt `~/.claude.json` (`state_ok == False`) marks `Scope.SYSTEM` servers from `managed-mcp.json` and `managed-settings.json` as `Status.UNKNOWN`, even though system-managed MCP servers do not depend on `~/.claude.json`.

### CC-19 — Missing `managedMcpServers` shadowing and unvalidated remote MCP URLs (Medium)
- **Where:** `src/agt_env/adapters/claude_code.py:560-561, 664-665`, `src/agt_env/mcp.py:19-28`
- **Current behavior:**
  1. `mark_shadowed([*local, *project, *user])` (`line 561`) excludes `groups[0]` (`managed-mcp.json`) and `groups[1]` (`managedMcpServers`). When `managedMcpServers` (which is non-exclusive, unlike `managed-mcp.json`) defines a server with the same name as a user, project, or local server, both are reported `Status.ENABLED` instead of marking the loser `Status.SHADOWED`.
  2. `remote_details` (`claude_code.py:664-665`) accepts any `isinstance(entry.get("url"), str)` as valid because `safe_url` (`mcp.py:19-28`) calls ` urllib.parse.urlsplit`, which never rejects schemeless/hostless strings like `""` or `"not-a-url"`. Claude Code validates remote MCP `url` fields as URLs (`It()`) and skips invalid entries.

---

## 6. Plugins & Bundled Items

### CC-20 — Plugin-bundled MCP servers are omitted, violating PRD Metric 1 (High)
- **Where:** `docs/agents/claude-code.md:135`, `docs/PRD.md:114, 154-161, 254`, `src/agt_env/adapters/claude_code.py:227-311`
- **Current behavior:** `claude_code.py` does not scan plugin `.mcp.json` or `manifest["mcpServers"]`; `docs/agents/claude-code.md:135` lists *"MCP servers bundled in plugins"* under `"Not covered by agt-env"`.
- **Confirmed Claude Code behavior & PRD conflict:**
  - Tested live against `2.1.316`: installing an enabled plugin containing `.mcp.json` causes **`claude mcp list` to list `plugin:<plugin>:<server>`** alongside user/project/local MCP servers. (Note: in `2.1.316`, `claude mcp list` listed `.mcp.json` servers while `claude plugin details` listed both `.mcp.json` and inline `plugin.json` `mcpServers`.)
  - `docs/PRD.md` Section 8 Metric 1 (P0 acceptance check) requires: *"for each P0 agent, MCP results match that agent's own listing command on the same fixture, wherever such a command exists."* Section 5.1 (`line 114`) likewise states *"What a plugin brings is listed directly below its row"*, and `docs/agents/claude-code.md:53` already documents plugin MCP precedence (*"Precedence is local, project, user, plugin, then claude.ai connectors"*).

### CC-21 — `manifest["commands"]` object form emits invalid entries as `enabled` and drops inline descriptions (Medium)
- **Where:** `src/agt_env/adapters/claude_code.py:237-249`
- **Current behavior:**
  ```python
  if isinstance(declared, dict):
      for name, entry in declared.items():
          source = entry.get("source") if isinstance(entry, dict) else None
          if isinstance(source, str) and is_inside_plugin(source):
              found = read_markdown(root / source, warnings)
          else:
              found = FoundSkill(root / _PLUGIN_MANIFEST, root / _PLUGIN_MANIFEST, ...)
          items.append(self._plugin_command(found, plugin, f"{prefix}:{name}", settings))
  ```
  1. If `entry` has an escaping path (`{"escape": {"source": "../outside.md"}}`), an empty object (`{"empty": {}}`), or a non-dict value (`{"bad": 123}`), it enters `else:`, builds a non-broken `FoundSkill`, and emits `p:<name>` as `Status.ENABLED`. Claude Code (`dbe`) only loads object entries with a valid in-plugin `source` `.md` file or `content` (when `source` is absent), skipping invalid entries.
  2. For valid inline `content` entries (`{"inline": {"content": "---\ndescription: Desc\n---\nBody"}}`) or `description` metadata overrides (`{"about": {"source": "./a.md", "description": "Override"}}`), `claude_code.py` ignores both `entry["content"]` and `entry["description"]`, leaving `description=""` (or failing to apply the metadata override).

### CC-22 — Duplicate plugin paths, multi-scope installs, and swallowed corrupt-manifest warnings (Medium)
- **Where:** `src/agt_env/adapters/claude_code.py:233, 293-305, 361-369, 382, 404, 410-412, 669-671`
- **Current behavior:**
  1. **Duplicate hook files (`lines 293-305`):** `hook_files` always starts with `root / "hooks" / "hooks.json"` and appends `manifest["hooks"]` entries without `realpath` deduplication. If `manifest["hooks"]` lists `"./hooks/hooks.json"` (or lists the same file twice), every hook in that file is emitted twice. Claude Code (`Xn`/`Kn` sets) explicitly deduplicates `hooks/hooks.json` and manifest hook paths by `realpath`.
  2. **Duplicate broken plugin skills (`lines 410-411`, `base.py:249`):** `for directory in ("skills", *added)` scans `root / "skills"` twice if `manifest["skills"]` includes `"./skills"`. `load_once` (`base.py:249`) does not deduplicate `Status.BROKEN` skills, so broken skills under `skills/` are emitted twice. Claude Code filters `skills/` out of `skillsPaths` before scanning.
  3. **Swallowed JSON errors (`_quiet_json`, `lines 669-671`):** `_quiet_json` passes a throwaway `[]` warnings list to `read_json`. Corrupt JSON in `.claude-plugin/plugin.json` or `hooks/hooks.json` produces **no stderr warning** (`FR-17`), and a plugin with corrupt `hooks/hooks.json` silently omits its hooks while reporting the plugin `enabled`.
  4. **Multi-scope plugin installs (`lines 361-369`):** If `installed_plugins.json` has both a `user` install and a `project`/`local` install for the same `plugin@marketplace`, `claude_code.py` emits both `PLUGIN` items as `enabled` (if enabled in settings) and duplicates every skill, command, subagent, and hook from both copies (`load_once` only deduplicates skills if both scopes point to the exact same `installPath`). Claude Code deduplicates active plugins/hooks by plugin name.

### CC-23 — Plugin skill naming, untrusted project plugin hooks, and `allowManagedHooksOnly` mismatches (Medium)
- **Where:** `src/agt_env/adapters/claude_code.py:308, 415-421, 486`, `docs/agents/claude-code.md:70, 109`
- **Current behavior & Claude Code discrepancies:**
  1. **Subdirectory plugin skill names (`lines 415-417`, `claude-code.md:70`):** `claude_code.py` uses `fields.get("name") or skill.path.name` for all plugin skills and performs no character sanitization. In Claude Code `2.1.316` (`NZt`), a **root** `SKILL.md` uses `frontmatter.name || basename(dir)`, whereas **subdirectory** skills (`skills/<dir>/SKILL.md`) construct their canonical ID from the directory name `Ae.name.replace(/[^a-zA-Z0-9_-]/g, "-")` (using `frontmatter.name` only as a display/invocation alias in `XK`).
  2. **Untrusted project-scope plugin hooks (`line 308`):** `_plugin_extras` calls `settings.hook_status(plugin.scope, trusted=True)`, hardcoding `trusted=True` even when `plugin.scope` is `Scope.PROJECT` and the workspace is untrusted (`trusted=False`). In Claude Code, `shouldSkipHookDueToTrust` skips non-managed hooks in untrusted workspaces.
  3. **`allowManagedHooksOnly` (`line 486`):** `hook_status` marks all non-`Scope.SYSTEM` hooks `Status.DISABLED` when `managed.get("allowManagedHooksOnly") is True`. In Claude Code `2.1.316`, `allowManagedHooksOnly` allows hooks from `managed-settings.json` **and** from plugins enabled by `managed-settings.json` (`"only hooks from managed settings and from plugins that managed settings enable run"`).

---

## 7. Subagents & Hooks

### CC-24 — Misleading `broken` finding text when a subagent's `name` is invalid (Low)
- **Where:** `src/agt_env/adapters/claude_code.py:53, 210-225, 690-691`
- **Current behavior:** When a subagent file has both `name` and `description` in frontmatter, but `name` starts with `-`, contains `:`, or exceeds 256 characters, `_agent` marks it `Status.BROKEN` with finding `"needs a name and a description, which Claude Code requires"`.
- **Expected behavior:** The finding (`FR-23`) should accurately state that Claude Code refuses the agent's `name` when `name` and `description` are both present.

### CC-25 — Hook URL validation, `allowedHttpHookUrls`, `strictPluginOnlyCustomization`, and guard-hook errors (Medium)
- **Where:** `src/agt_env/hooks.py:52-53`, `src/agt_env/mcp.py:19-28`, `src/agt_env/adapters/claude_code.py:182-192, 474-489`
- **Current behavior & Claude Code discrepancies:**
  1. **Unvalidated HTTP hook URLs:** `handler_details` (`hooks.py:52-53`) accepts any string `url` (including `""` or `"not-a-url"`), reporting `Status.ENABLED`, whereas Claude Code's Zod schema (`url: o().url()`) rejects invalid URLs and strips the hook.
  2. **`allowedHttpHookUrls` ignored:** Claude Code settings support `allowedHttpHookUrls` (an array of URL wildcard patterns; `[]` blocks all HTTP hooks, and non-matching HTTP hook URLs are blocked). `claude_code.py` ignores `allowedHttpHookUrls` and reports blocked HTTP hooks as `Status.ENABLED`.
  3. **`strictPluginOnlyCustomization` ignored:** Managed settings in `2.1.316` support `strictPluginOnlyCustomization` (`true` or a list of surfaces `["skills", "agents", "commands", "hooks", "mcp"]`), which blocks non-plugin user/project `~/.claude/{surface}/`, `.claude/{surface}/`, `settings.json` hooks, and `.mcp.json` while keeping managed and plugin customizations active.
  4. **Invalid `PreToolUse` / `PermissionRequest` guard hooks:** For most events (such as `SessionStart`, tested in `tests/test_claude_oracle.py:477`), Claude Code strips an invalid hook entry and runs the rest. For `PreToolUse` and `PermissionRequest` (`R8` / `unloadableGuards` in `2.1.316`), an invalid hook entry causes Claude Code to reject **the entire settings file** containing it so permissions declared alongside a broken guard hook cannot take effect without their guard. `docs/agents/claude-code.md:110` overgeneralizes *"An invalid handler or an unknown event is skipped, and the rest still run"* from `SessionStart` alone.

---

## 8. Documentation, Evidence Labels, Safety Audit, & Test Gaps

### CC-26 — Self-contradiction in `docs/agents/claude-code.md` and missing `init["skills"]` oracle test (Medium)
- **Where:** `docs/agents/claude-code.md:24` vs `docs/agents/claude-code.md:114`, `tests/test_claude_oracle.py:283-288`
- **Problem:**
  - `docs/agents/claude-code.md:24` states: *"Claude Code has no non-interactive skill listing, so these rules weren't confirmed against the real agent"*, leaving almost every row in the `Skills` table (`lines 28-40`) at `Doc`.
  - `docs/agents/claude-code.md:114` directly contradicts this: *"`claude -p hi --output-format stream-json --verbose` prints an `init` message with `agents`, `slash_commands`, and `skills` before it finds that no one is signed in ... So it works in a temporary `HOME` and tests compare with it."*
  - `tests/test_claude_oracle.py:287` only reads `listing["agents"]` and `listing["slash_commands"]`, never asserting against `listing["skills"]`. Adding `listing["skills"]` oracle assertions would have immediately caught CC-08 (hidden skills), CC-09 (folder name vs frontmatter `name` and `skillOverrides`), and CC-10 (`disable-model-invocation: true` + `user-invocable: false`).

### CC-27 — Non-standard evidence label and undocumented plugin subagent rule (Low)
- **Where:** `docs/agents/claude-code.md:81, 83, 87`, `docs/agents/README.md:19-25`
- **Problem:**
  1. `docs/agents/claude-code.md:81` uses the evidence label `"Doc, and the path is assumed to match skills"`, which is not one of the four labels defined in `docs/agents/README.md:19-25` (`Doc`, `Tested`, `Doc + Tested`, `Assumed`). Note also that `2.1.316`'s `IEt("policySettings", "agents")` confirms the path actually is `<managed_dir>/.claude/agents`.
  2. `docs/agents/claude-code.md:83` states that a subagent file with no `name` or no `description` isn't loaded, while `line 87` only notes that plugin agents *"fall back to the file name"*. Tested live against `2.1.316` (and implemented in `claude_code.py:276-291`): plugin subagents load even with **no frontmatter and no `description` at all** (`plain.md`), which should be explicitly recorded in `docs/agents/claude-code.md`.

### CC-28 — Shared skill directory evidence and `CLAUDE_CONFIG_DIR` credential-store audit gap (Low)
- **Where:** `docs/agents/README.md:32-33, 43`, `tests/test_safety.py:15`
- **Problem:**
  1. `docs/agents/README.md:32-33` marks `~/.agents/skills` and `.agents/skills` under Claude Code as `No (not documented)`. Tested live against `2.1.316` (and verified in the binary, where `.agents/skills` only appears in a Cursor-import command, never in skill discovery): Claude Code does **not** load `~/.agents/skills` or `.agents/skills`, so this can be recorded as tested rather than merely undocumented.
  2. `docs/agents/README.md:43` and `tests/test_safety.py:15` only list/plant `~/.claude/.credentials.json`. When `CLAUDE_CONFIG_DIR` moves the config directory, Claude Code stores credentials at `$CLAUDE_CONFIG_DIR/.credentials.json`; `tests/test_safety.py` has no audit test exercising `CLAUDE_CONFIG_DIR` with a planted `.credentials.json` inside the overridden directory.

---

## Resolution

Every finding was rechecked against Claude Code `2.1.316` in a temporary `HOME`, with managed settings given through a private `/etc` in a mount namespace. Everything else was fixed as described, except for the corrections below, and `tests/test_claude_oracle.py` now compares these rules with the real agent. `docs/agents/claude-code.md` records the results.

| ID | Outcome | What testing found |
|---|---|---|
| CC-05 | **Not valid, unchanged** | Project settings count before the folder is trusted: `claude mcp list` obeys a project's `deniedMcpServers`, `claude plugin list` its `enabledPlugins`, and `claude -p` its `skillOverrides`. Only MCP approvals and hooks wait for trust. Whether an interactive session differs can't be tested without one, but it can't start until the folder is trusted anyway |
| CC-07 | Fixed differently | Trust isn't inherited through parents. `.claude.json` keeps a project under its repository's root, or the directory if there's no repository. An entry for the current directory or a middle one is ignored inside a repository. This also applies to local servers and `disabledMcpServers` |
| CC-09 | Fixed, with a caveat | Listings, `skillOverrides`, and precedence do use the folder, so items are now listed by it. But the docs say `name` is what the `/` menu shows, so `--detail` shows it as `Display name`, and a deny rule can use it |
| CC-10 | Fixed, with a correction | `user-invocable: false` removes a skill from both `skills` and `slash_commands` in `init`, not just the second. So the listing can't separate it from a skill with both flags, and `disabled` for both flags rests on what the flags mean. `paths` applies only to non-plugin skills |
| CC-12 | Half valid | `skill.md` isn't loaded from skill directories, or in plugins, so nothing changed there. It's only accepted under `commands/`, covered by CC-14. `skills-dir` plugins are real and now listed |
| CC-16 | Fixed, with a correction | `streamable-http` is also accepted, not only `http` and `sse` |
| CC-17 | Fixed, with a correction | The `${VAR}` exception doesn't count `env`, per the docs |
| CC-18 | Half valid | Part 1 isn't valid: a corrupt or non-file `managed-mcp.json` keeps exclusive control, so `disabled` is right, and it's now documented. It now warns when it's not a file. Part 2 is fixed |
| CC-19 | Fixed, with a correction | Bad URLs aren't skipped. They're listed and fail to connect, so they're reported as `broken` |
| CC-20 | Fixed, with a correction | The `plugin details`/`mcp list` difference was caused by the same command: a plugin's server is hidden if another has the same command and arguments or URL, not by name. Those are reported as `shadowed` |
| CC-21 | Fixed, with a correction | Invalid entries make installing fail, and if the manifest changes afterwards, all the object's entries go and `commands/` is used. A missing `source` costs only that entry |
| CC-22 | Fixed, with a limit | A plugin in two scopes keeps both rows, as `claude plugin list` does, but its contents appear once. Which copy's files load when they differ wasn't determined |
| CC-23 | Fixed | Part 2 stays an assumption, since `claude -p` skips the trust dialog. `shouldSkipHookDueToTrust` does exist, but it takes no scope, so the code alone doesn't show how it treats a project-scoped plugin's hook |
| CC-25 | Fixed, with a correction | `commands` isn't a value `strictPluginOnlyCustomization` knows: `skills` covers commands, including managed ones, which stay blocked. An invalid guard hook doesn't cost managed settings their file. It does cost a plugin's hook file |
