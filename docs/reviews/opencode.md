# OpenCode Discovery & Documentation Review

Audit of `opencode` resource discovery across `src/agt_env/adapters/opencode.py` (and shared helpers in `adapters/base.py`, `skills.py`, and `mcp.py`), `docs/agents/opencode.md`, `docs/agents/README.md`, `docs/PRD.md`, and `tests/`.

**Verification baseline:** Upstream `anomalyco/opencode` open-source TypeScript/Bun repository (`v2.2.5`, commit `9bbb8ce18c80f099e0e448775f59d965ead40a21`), cross-checked against live isolated non-interactive runs of `opencode v2.2.5` (`opencode` daemon service API and CLI commands inside temporary `HOME` and private background daemon ports), official documentation at `https://opencode.ai/docs/*`, `docs/agents/opencode.md`, `src/agt_env/adapters/opencode.py`, and `tests/test_opencode_oracle.py`.

---

## Summary of Findings

| Done | ID | Severity | Area | Summary |
|---|---|---|---|---|
| [x] | OC-01 | High | Config | `OPENCODE_CONFIG_DIR` overrides `Global.Path.config` in OpenCode, but adapter scans both it and `~/.config/opencode`, fabricating phantom items |
| [x] | OC-02 | High | Skills | Skill scan max depth is 6 directories in OpenCode, not 5; adapter misses 6th-level skills due to miscounting depth |
| [x] | OC-03 | High | Commands/Agents | Plural vs singular precedence is inverted: OpenCode sorts alphabetically (`commands/` > `command/`, `agents/` > `agent/`), but adapter evaluates plural first so singular erroneously wins |
| [x] | OC-04 | High | MCP | Global `mcp.timeout` in config is misparsed as an MCP server named `"timeout"` with status `Status.BROKEN` |
| [x] | OC-05 | High | MCP | Mixed V1 (`mcp.<name>`) and V2 (`mcp.servers.<name>`) in the same file are dropped by adapter; OpenCode merges both |
| [x] | OC-06 | High | Plugins | Removing `-opencode.config.skill` disables all skills (including compatibility skills), but OpenCode keeps compatibility skills active under separate plugin `opencode.config.compatibility` |
| [x] | OC-07 | High | Env Vars | Env var truthiness check (`_is_set`) treats `"0"` and `"false"` as True, disabling features when OpenCode keeps them enabled |
| [x] | OC-08 | High | Subagents | Mode files in `{mode,modes}/*.md` are discovered as primary agents in OpenCode, but adapter only looks in `agent/` and `agents/` |
| [x] | OC-09 | Medium | Precedence | User vs project skill deduplication via `load_once` preserves user item if project symlinks to user, inverting precedence |
| [x] | OC-10 | Medium | Plugins | Built-in plugin removals `-opencode.config.agent` and `-opencode.config.command` are ignored by adapter |
| [x] | OC-11 | Medium | Plugins | Wildcard `-*` removal cannot be overridden by subsequent additions because adapter permanently accumulates removals |
| [ ] | OC-12 | Medium | Precedence | Invalid server in higher-precedence layer does not override lower-precedence valid server in `winners` dictionary |
| [x] | OC-13 | Medium | MCP | Enabled-only V1 entry without type is reported as `Status.BROKEN` by adapter, but OpenCode ignores it with a warning |
| [x] | OC-14 | Medium | Discovery | System-managed resources (`/etc/opencode/skills`, `agents`, `commands`, `plugins`) are ignored by adapter |
| [x] | OC-15 | Medium | Isolation | Process-global `expanduser()` in `_configured_skill_paths` and `_local_plugin_path` leaks host `HOME` |
| [x] | OC-16 | Medium | Plugins | Local plugin prefix `"~"` is treated as local by adapter, but OpenCode treats it as an npm package |
| [ ] | OC-17 | Medium | Precedence | `mark_shadowed` only treats `Status.ENABLED` as winners, leaving conflicting disabled skills unshadowed |
| [x] | OC-18 | Medium | Plugins | Local GUI-only plugins (`gui-experimental.cjs`/`.js`) without backend entrypoint are misreported as `Status.BROKEN` |
| [x] | OC-19 | Medium | Commands | Command description extracted from raw markdown body instead of frontmatter only, fabricating bash lines as descriptions |
| [x] | OC-20 | Low | Config | `OPENCODE_CONFIG` environment variable override is missing from `config_path` discovery |
| [x] | OC-21 | Low | Env Vars | `OPENCODE_CONFIG_PROJECT_DISABLE` alias is ignored by adapter |
| [x] | OC-22 | Low | Configured | Configured check ignores `~/.agents/skills` and `.agents/skills` |
| [x] | OC-23 | Low | Discovery | macOS MDM Managed Preferences Plist (`ai.opencode.managed.plist`) is ignored |
| [ ] | OC-24 | Low | Platform | Windows system directory path mismatch (`%ProgramData%\opencode` vs `\etc\opencode`) |
| [x] | OC-25 | Low | Safety / Docs | Credential store documentation lists `auth.json` as assumed, whereas OpenCode uses `opencode.db` and `service.json` |
| [x] | OC-26 | Low | Docs | `docs/agents/opencode.md` claims `OPENCODE_DISABLE_*` env vars are "seen only in binary", but they are officially documented |
| [x] | OC-27 | Low | Tests | Oracle test depth test had a directory structure flaw masking the depth 5 vs 6 discrepancy |

---

## 1. Configuration & Environment Variables

### OC-01 — `OPENCODE_CONFIG_DIR` replaces global config directory, but adapter scans both (High)
- **Where:** [`src/agt_env/adapters/opencode.py:92-94, 243-247`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/opencode.py#L92-L94), [`docs/agents/opencode.md:86`](file:///home/zengxg/projects/agent-env/docs/agents/opencode.md#L86)
- **OpenCode behavior:** In OpenCode (`packages/util/src/global.ts:79`, `packages/cli/src/server-process.ts:123`, `packages/server/src/routes.ts:133`), `Global.Path.config` is evaluated as:
  ```typescript
  Global.Path.config = process.env.OPENCODE_CONFIG_DIR || path.join(os.homedir(), ".config", "opencode")
  ```
  When `OPENCODE_CONFIG_DIR` is set in the environment, it **overrides and replaces** `~/.config/opencode`. OpenCode never reads `~/.config/opencode` for config files, skills, plugins, agents, or commands.
- **agt-env behavior:** In [`opencode.py:92-94`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/opencode.py#L92-L94) and [`opencode.py:243`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/opencode.py#L243):
  ```python
  global_dir = context.home / ".config" / "opencode"
  extra_dir = Path(context.env["OPENCODE_CONFIG_DIR"]) if "OPENCODE_CONFIG_DIR" in context.env else None
  user_dirs = [global_dir, *([extra_dir] if extra_dir else [])]
  ```
  `agt-env` keeps `global_dir` and treats `extra_dir` as an *additive* directory, scanning both `~/.config/opencode` and `$OPENCODE_CONFIG_DIR`.
- **Impact:** When a user isolates their environment using `OPENCODE_CONFIG_DIR`, `agt-env` hallucinates skills, MCP servers, plugins, and commands from `~/.config/opencode` that OpenCode will never execute or load.
- **Fix:** If `context.env.get("OPENCODE_CONFIG_DIR")` is set, `global_dir` should be replaced by that path rather than treating it as an additive directory.

### OC-07 — Env var truthiness check (`_is_set`) treats `"0"` and `"false"` as True (High)
- **Where:** [`src/agt_env/adapters/opencode.py:551-553`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/opencode.py#L551-L553)
- **OpenCode behavior:** In OpenCode (`packages/cli/src/server-process.ts:223`, `packages/core/src/flag.ts`), boolean environment flags are parsed strictly:
  ```typescript
  function isFlag(value: string | undefined): boolean {
    return value === "1" || value?.toLowerCase() === "true"
  }
  ```
  Setting `OPENCODE_DISABLE_PROJECT_CONFIG=0` or `OPENCODE_DISABLE_CLAUDE_CODE=false` keeps project config and Claude compatibility enabled.
- **agt-env behavior:** In [`opencode.py:551-553`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/opencode.py#L551-L553):
  ```python
  def _is_set(context: AgentContext, *vars: str) -> bool:
      return any(context.env.get(var) for var in vars)
  ```
  In Python, any non-empty string like `"0"`, `"false"`, or `"no"` evaluates to `True`.
- **Impact:** When a user sets `OPENCODE_DISABLE_PROJECT_CONFIG=0` or `OPENCODE_DISABLE_CLAUDE_CODE=false` in their shell, `agt-env` disables project config and Claude skills, while OpenCode continues to load them.
- **Fix:** Check `context.env.get(var) in ("1", "true", "True")`.

### OC-20 — `OPENCODE_CONFIG` environment variable override is missing from `config_path` discovery (Low)
- **Where:** [`src/agt_env/adapters/opencode.py:75-80`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/opencode.py#L75-L80)
- **OpenCode behavior:** In OpenCode (`packages/core/src/config/source.ts:38`), `OPENCODE_CONFIG` points directly to an explicit config file path (e.g. `OPENCODE_CONFIG=/custom/opencode.json`).
- **agt-env behavior:** `opencode.py` only checks for config files inside `OPENCODE_CONFIG_DIR`, `~/.config/opencode`, and `.opencode/`. It never inspects `OPENCODE_CONFIG`.
- **Impact:** If an explicit config file is configured via `OPENCODE_CONFIG`, `agt-env` may report OpenCode as "not configured" and fail to load layers.
- **Fix:** Inspect `context.env.get("OPENCODE_CONFIG")` in `config_path` and `_layers`.

### OC-21 — `OPENCODE_CONFIG_PROJECT_DISABLE` alias is ignored by adapter (Low)
- **Where:** [`src/agt_env/adapters/opencode.py:250`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/opencode.py#L250)
- **OpenCode behavior:** OpenCode accepts both `OPENCODE_DISABLE_PROJECT_CONFIG` and `OPENCODE_CONFIG_PROJECT_DISABLE` (`packages/cli/src/server-process.ts:223`).
- **agt-env behavior:** [`opencode.py:250`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/opencode.py#L250) only checks `OPENCODE_DISABLE_PROJECT_CONFIG`.
- **Impact:** If `OPENCODE_CONFIG_PROJECT_DISABLE=1` is set, `agt-env` scans project configuration when OpenCode ignores it.
- **Fix:** Add `"OPENCODE_CONFIG_PROJECT_DISABLE"` to the `_is_set` call at line 250.

### OC-22 — Configured check ignores `~/.agents/skills` and `.agents/skills` (Low)
- **Where:** [`src/agt_env/adapters/opencode.py:75-86`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/opencode.py#L75-L86)
- **OpenCode behavior:** OpenCode discovers skills in `.agents/skills` and `~/.agents/skills` out of the box via `opencode.config.compatibility`.
- **agt-env behavior:** `config_path` checks `~/.config/opencode`, `.opencode`, `~/.claude/skills`, and `.claude/skills`, but does not check `.agents/skills` or `~/.agents/skills`.
- **Impact:** If a user only sets up skills in standard `.agents/skills`, `agt-env` marks OpenCode as unconfigured unless `--all` is passed.
- **Fix:** Include `.agents/skills` and `~/.agents/skills` in the configured probe list in `config_path`.

---

## 2. Skills Discovery & Precedence

### OC-02 — Skill scan maximum depth is 6 directories, not 5 (High)
- **Where:** [`src/agt_env/adapters/opencode.py:50`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/opencode.py#L50), [`docs/agents/opencode.md:110`](file:///home/zengxg/projects/agent-env/docs/agents/opencode.md#L110)
- **OpenCode behavior:** In OpenCode (`packages/core/src/config/plugin/skill-scan.ts:7, 38-43`):
  ```typescript
  const MAX_DEPTH = 6;
  ...
  if (item.depth < MAX_DEPTH)
    queue.push(
      ...children
        .filter((child) => child.type === "directory")
        .map((child) => ({ directory: child.path, depth: item.depth + 1 })),
    )
  ```
  The queue starts at `depth: 0` for the skill root. Subdirectories are pushed with `depth: 1` through `depth: 6`. When an item at `depth: 6` is dequeued, `skillFiles(dir)` checks for `SKILL.md` directly inside it. This means `root/d1/d2/d3/d4/d5/d6/SKILL.md` (6 directory levels deep) is discovered.
- **agt-env behavior:** [`opencode.py:50`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/opencode.py#L50) defines `_SKILL_SCAN_DEPTH = 5`. [`docs/agents/opencode.md:110`](file:///home/zengxg/projects/agent-env/docs/agents/opencode.md#L110) rationalizes this: *"What 'six directories' counts: SKILL.md can be at most five directories below a source. The file itself is the sixth level."*
- **Impact:** Skills located 6 directory levels below a skill root are discovered by OpenCode but completely omitted by `agt-env`.
- **Fix:** Change `_SKILL_SCAN_DEPTH = 6` in `opencode.py` and update the documentation in `opencode.md`.

### OC-09 — User vs project skill deduplication via `load_once` preserves user item if project symlinks to user (Medium)
- **Where:** [`src/agt_env/adapters/opencode.py:94-123`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/opencode.py#L94-L123)
- **OpenCode behavior:** In OpenCode, project-level configurations and resources take precedence over user-level configurations.
- **agt-env behavior:** In `opencode.py`:
  ```python
  roots = [
      *[(Scope.USER, directory / "skills") for directory in user_dirs],
      *[(Scope.USER, directory / "skill") for directory in user_dirs],
      (Scope.PROJECT, project_dir / "skills"),
      (Scope.PROJECT, project_dir / "skill"),
  ]
  ...
  report.items = load_once(report.items)
  ```
  `load_once` retains the first item with a given real path (`os.path.realpath(item.source_path)`). Because user roots are evaluated before project roots, the user item is added first. If a project skill directory symlinks to a user skill, `load_once` keeps the user-scoped item and deletes the project-scoped item.
- **Impact:** When precedence is calculated, the item remains `Scope.USER`, inverting the precedence hierarchy.
- **Fix:** Populate project roots before user roots or ensure `load_once` prefers `Scope.PROJECT` items over `Scope.USER`.

### OC-15 — Process-global `expanduser()` in `_configured_skill_paths` leaks host `HOME` (Medium)
- **Where:** [`src/agt_env/adapters/opencode.py:484`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/opencode.py#L484), [`src/agt_env/adapters/opencode.py:416`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/opencode.py#L416)
- **OpenCode behavior:** OpenCode resolves tildes (`~`) using the configured user home directory.
- **agt-env behavior:** Both functions call `Path(entry).expanduser()`. In Python, `Path.expanduser()` resolves to `os.environ["HOME"]` (the runner's host environment), ignoring `context.home`.
- **Impact:** In test harnesses or when `context.home` is customized, paths with `~` resolve to the host system rather than the isolated context.
- **Fix:** Replace `expanduser()` with a helper that replaces `~` with `context.home`.

### OC-17 — `mark_shadowed` only treats `Status.ENABLED` as winners, leaving conflicting disabled skills unshadowed (Medium)
- **Where:** [`src/agt_env/adapters/opencode.py:126`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/opencode.py#L126), [`src/agt_env/adapters/base.py:227`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/base.py#L227)
- **OpenCode behavior:** In OpenCode, a skill defined at project scope occupies that skill name in the registry regardless of whether it is permitted or denied by permissions. If a project skill is disabled, the same-named user skill does not activate.
- **agt-env behavior:** `mark_shadowed(reversed(report.items))` uses default `winners = frozenset({Status.ENABLED})`. If a project skill has `Status.DISABLED`, it is not added to `won`. When the same-named user skill is subsequently evaluated, it is not shadowed and may remain `Status.ENABLED`.
- **Impact:** A user skill appears as `enabled` when OpenCode actually shadowed/suppressed it with the project's disabled skill.
- **Fix:** Pass `winners=frozenset({Status.ENABLED, Status.DISABLED})` to `mark_shadowed`.

### OC-27 — Oracle test depth test had a directory structure flaw masking the depth 5 vs 6 discrepancy (Low)
- **Where:** [`tests/test_opencode_oracle.py:124-127`](file:///home/zengxg/projects/agent-env/tests/test_opencode_oracle.py#L124-L127)
- **Analysis:** In `test_opencode_oracle.py`:
  ```python
  deep = skills
  for level in range(1, 8):
      deep = deep / f"l{level}"
      write_skill(deep, f"name: depth{level}\ndescription: d") if level >= 5 else deep.mkdir()
  ```
  `write_skill` created `l1/l2/l3/l4/l5/SKILL.md`. In OpenCode's directory crawler (`skill-scan.ts:30`), once a directory contains `SKILL.md`, it pushes the skill and executes `continue`, never recursing into child directories. Therefore, `l6` (which was placed inside `l5`) was skipped by OpenCode because it was inside an existing skill.
- **Impact:** The oracle comparison test passed because OpenCode stopped at `l5` due to the nested skill directory, hiding the fact that `agt-env`'s `_SKILL_SCAN_DEPTH = 5` would miss a valid 6th-level standalone skill directory.
- **Fix:** In the oracle test fixture, place deep skills in distinct branch paths (e.g. `skills/a/b/c/d/e/f/SKILL.md`) without placing skills in parent directories.

---

## 3. Commands & Subagents Discovery

### OC-03 — Plural vs singular precedence is inverted (High)
- **Where:** [`src/agt_env/adapters/opencode.py:199-204`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/opencode.py#L199-L204), [`docs/agents/opencode.md:58-61`](file:///home/zengxg/projects/agent-env/docs/agents/opencode.md#L58-L61)
- **OpenCode behavior:** In OpenCode (`packages/core/src/config/plugin/command.ts:161` and `agent.ts:153`):
  ```typescript
  const files = await fs.scan("{command,commands}/**/*.md");
  files.toSorted();
  ```
  OpenCode sorts file paths with `files.toSorted()`. In ASCII alphabetical sorting, `"command/"` sorts before `"commands/"`. When OpenCode loops through sorted files and writes `result[name] = ...`, `"commands/"` is processed later and **overwrites** `"command/"`. Plural wins over singular.
- **agt-env behavior:** In `opencode.py:199`:
  ```python
  for root in (directory / plural, directory / singular):
  ```
  `agt-env` processes plural first and singular second. If an item was found in plural, singular overwrites it and reports:
  ```python
  Finding(f"overrides the entry in {plural}")
  ```
- **Impact:** When both `commands/deploy.md` and `command/deploy.md` exist, OpenCode executes `commands/deploy.md`, but `agt-env` marks `command/deploy.md` as `enabled` and `commands/deploy.md` as `shadowed`.
- **Fix:** Swap the iteration order to `(directory / singular, directory / plural)` so that plural processes second and wins.

### OC-08 — Mode files in `{mode,modes}/*.md` are discovered as primary agents in OpenCode, but adapter only looks in `agent/` and `agents/` (High)
- **Where:** [`src/agt_env/adapters/opencode.py:185`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/opencode.py#L185)
- **OpenCode behavior:** In OpenCode (`packages/core/src/config/plugin/agent.ts:23, 164-186`):
  ```typescript
  const legacySources = [
    { pattern: "{agent,agents}/**/*.md", primary: false },
    { pattern: "{mode,modes}/*.md", primary: true },
  ] as const
  ```
  OpenCode explicitly scans `{mode,modes}/*.md` as primary subagents.
- **agt-env behavior:** [`opencode.py:185`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/opencode.py#L185) defines:
  ```python
  _AGENT_SOURCES = ("agents", "agent")
  ```
  It never scans `mode/` or `modes/`.
- **Impact:** Subagents configured under `modes/` or `mode/` are completely invisible to `agt-env`.
- **Fix:** Include `("modes", "mode")` in `_AGENT_SOURCES` (with primary agent semantics).

### OC-10 — Built-in plugin removals `-opencode.config.agent` and `-opencode.config.command` are ignored by adapter (Medium)
- **Where:** [`src/agt_env/adapters/opencode.py:381-394`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/opencode.py#L381-L394)
- **OpenCode behavior:** OpenCode's subagents and commands are implemented as internal plugins: `opencode.config.agent` and `opencode.config.command` (`packages/core/src/config/plugin/agent.ts:36`, `command.ts:22`). Adding `-opencode.config.agent` or `-opencode.config.command` to `plugins = [...]` disables subagent or command loading.
- **agt-env behavior:** [`opencode.py:381-394`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/opencode.py#L381-L394) only checks `opencode.config.skill` and `opencode.config.compatibility`. It completely ignores `-opencode.config.agent` and `-opencode.config.command`.
- **Impact:** If an administrator or user disables subagents or commands via plugin removals, `agt-env` continues to report them as `Status.ENABLED`.
- **Fix:** Extend `_apply_built_in_removals` to check for `opencode.config.agent` and `opencode.config.command` and disable matching `Kind.SUBAGENT` and `Kind.COMMAND` items.

### OC-19 — Command description extracted from raw markdown body instead of frontmatter only (Medium)
- **Where:** [`src/agt_env/adapters/opencode.py:213`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/opencode.py#L213)
- **OpenCode behavior:** In OpenCode (`packages/core/src/config/plugin/command.ts:167-172`), command description is read strictly from markdown frontmatter `description`:
  ```typescript
  const info = Option.getOrUndefined(decodeCommand({ ...markdown.data, template: markdown.content.trim() }))
  ```
  The markdown body (`markdown.content`) is the command executable shell template. If `description` is omitted from frontmatter, `info.description` is undefined.
- **agt-env behavior:** In `opencode.py:213`:
  ```python
  description = (
      str(found.frontmatter["description"]).strip()
      if "description" in found.frontmatter
      else first_line(found.body)
  )
  ```
  When frontmatter description is missing, `agt-env` takes the first line of the shell template as the description.
- **Impact:** A shell script line like `#!/usr/bin/env bash` or `git status --porcelain` is fabricated as the user-facing command description.
- **Fix:** Set description to `None` (or empty string) if not present in `found.frontmatter`.

---

## 4. MCP Servers & Configuration

### OC-04 — Config `mcp.timeout` is misparsed as an MCP server named `"timeout"` with status `Status.BROKEN` (High)
- **Where:** [`src/agt_env/adapters/opencode.py:555-564`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/opencode.py#L555-L564)
- **OpenCode behavior:** In OpenCode (`packages/core/src/config/normalize.ts:282`):
  ```typescript
  if (name === "timeout" && !isDirectLegacyMcp(value)) {
    normalizeMcpTimeout(..., timeout, path, diagnostics)
    return
  }
  ```
  `mcp.timeout` in config files configures MCP request timeouts (either as a number or an object like `{"catalog": 5000, "call": 30000}`).
- **agt-env behavior:** In [`opencode.py:557-564`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/opencode.py#L557-L564):
  ```python
  def _server_entries(raw: object) -> dict[str, dict[str, object]]:
      if not isinstance(raw, dict):
          return {}
      servers = raw.get("servers")
      if isinstance(servers, dict):
          return {name: entry for name, entry in servers.items() if isinstance(entry, dict)}
      return {name: entry for name, entry in raw.items() if isinstance(entry, dict)}
  ```
  If `servers` is not specified, any dictionary in `mcp` is treated as a server entry. If `timeout` is configured as a dictionary, `_server_entries` yields `name="timeout"`. Then `_server_details` fails to find `type`, `command`, or `url`, returning `None`. Consequently, [`opencode.py:288`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/opencode.py#L288) creates:
  `Item(name="timeout", kind=Kind.MCP, status=Status.BROKEN, findings=["invalid server entry"])`.
- **Impact:** A valid MCP configuration timeout produces a spurious broken MCP server named `"timeout"`.
- **Fix:** Explicitly exclude the `"timeout"` key from `_server_entries(raw)`.

### OC-05 — Mixed V1 (`mcp.<name>`) and V2 (`mcp.servers.<name>`) in the same file are dropped by adapter (High)
- **Where:** [`src/agt_env/adapters/opencode.py:558-564`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/opencode.py#L558-L564)
- **OpenCode behavior:** In OpenCode (`packages/core/src/config/normalize.ts:276-297`), `normalizeMcp` populates both `legacyServers` and `nativeServers`, and merges them with:
  ```typescript
  const servers = mergeMaps(legacyServers, nativeServers, ["mcp", "servers"], diagnostics)
  ```
  If a configuration file has both legacy top-level servers and `servers: {...}`, OpenCode merges both maps.
- **agt-env behavior:** In `opencode.py:558-564`:
  ```python
  servers = raw.get("servers")
  if isinstance(servers, dict):
      return {name: entry for name, entry in servers.items() if isinstance(entry, dict)}
  return {name: entry for name, entry in raw.items() if isinstance(entry, dict)}
  ```
  If `servers` exists, `opencode.py` returns *only* `servers.items()` and completely drops all sibling top-level servers.
- **Impact:** Any top-level MCP servers in the same configuration file are silently lost.
- **Fix:** Merge top-level dict entries (excluding `"servers"` and `"timeout"`) with `raw["servers"]`.

### OC-12 — Invalid server in higher-precedence layer does not override lower-precedence valid server in `winners` (Medium)
- **Where:** [`src/agt_env/adapters/opencode.py:288-292`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/opencode.py#L288-L292)
- **OpenCode behavior:** When a project configuration defines a server with the same name as a user-level server, the project-level definition overrides the user-level server. If the project's definition is invalid, OpenCode rejects or warns on that server, but does not fall back to the user configuration.
- **agt-env behavior:** In `opencode.py:288`:
  ```python
  for layer in layers:
      for name, entry in _server_entries(layer.data).items():
          if name not in winners or _server_details(entry) is not None:
              if name in winners:
                  replaced[name] = winners[name][1].path
              winners[name] = (entry, layer)
  ```
  If `name` is already in `winners` (from a user layer) and the project layer's `entry` is invalid (`_server_details(entry) is None`), `name not in winners or _server_details(entry) is not None` evaluates to `False`. The higher layer's entry is ignored, and the user layer's server remains in `winners`.
- **Impact:** An invalid server definition in project config fails to override the user server, resurrecting the lower layer's server as `Status.ENABLED`.
- **Fix:** Always update `winners[name] = (entry, layer)` for later layers regardless of `_server_details` validity.

### OC-13 — Enabled-only V1 entry without type is reported as `Status.BROKEN` by adapter, but OpenCode ignores it with a warning (Medium)
- **Where:** [`src/agt_env/adapters/opencode.py:555`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/opencode.py#L555), [`docs/agents/opencode.md:72`](file:///home/zengxg/projects/agent-env/docs/agents/opencode.md#L72)
- **OpenCode behavior:** In OpenCode (`packages/core/src/config/normalize.ts:272, 891`):
  ```typescript
  if (isEnabledOnlyMcp(value)) {
    diagnostics.push({ kind: "unsupported", path, message: "omitted enabled-only legacy MCP entry" })
    return
  }
  ```
  An enabled-only legacy entry (`{"enabled": true}`) without a type is discarded with an unsupported diagnostic.
- **agt-env behavior:** [`docs/agents/opencode.md:72`](file:///home/zengxg/projects/agent-env/docs/agents/opencode.md#L72) states: *"An enabled-only V1 entry without a type is ignored with a warning."* However, `opencode.py:555` includes the dict entry, fails `_server_details`, and emits `Status.BROKEN` with finding `"invalid server entry"`.
- **Impact:** Discrepancy between code and documentation; emits broken items instead of ignoring them.
- **Fix:** Filter out `isEnabledOnlyMcp` entries (or drop them with a warning) as documented in `opencode.md:72`.

---

## 5. Plugins & Built-in Removals

### OC-06 — Removing `-opencode.config.skill` disables compatibility skills, but OpenCode keeps them active under `opencode.config.compatibility` (High)
- **Where:** [`src/agt_env/adapters/opencode.py:384-389`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/opencode.py#L384-L389)
- **OpenCode behavior:** In OpenCode (`packages/core/src/config/plugin/skill.ts:21` and `compatibility.ts:14`), native skills are loaded by `opencode.config.skill`, while `.claude/skills` and `.agents/skills` are loaded by a distinct internal plugin: `opencode.config.compatibility`. If a user configures `plugins = ["-opencode.config.skill"]`, only native OpenCode skills are disabled; compatibility skills in `.claude/skills` and `.agents/skills` remain active.
- **agt-env behavior:** In `opencode.py:384-389`:
  ```python
  for name in ("opencode.config.skill", "opencode.config.compatibility"):
      if plugins.removed(name):
          report.status = Status.DISABLED
          for item in report.items:
              if item.kind == Kind.SKILL:
                  item.status = Status.DISABLED
                  item.findings.append(f"disabled by removing {name}")
  ```
  Removing `-opencode.config.skill` disables **all** skills in the report, including compatibility skills. Conversely, removing `-opencode.config.compatibility` disables native skills as well.
- **Impact:** Disabling OpenCode native skills falsely marks Claude and Agents compatibility skills as disabled.
- **Fix:** Track which skills originated from compatibility roots versus native roots, and only disable the corresponding subset.

### OC-11 — Wildcard `-*` removal cannot be overridden by subsequent additions because adapter permanently accumulates removals (Medium)
- **Where:** [`src/agt_env/adapters/opencode.py:360, 370`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/opencode.py#L360#L370)
- **OpenCode behavior:** In OpenCode (`packages/core/src/config/plugin/source.ts:150-180`), plugin operations apply sequentially in array order:
  ```json
  "plugins": ["-*", "opencode.config.compatibility"]
  ```
  The wildcard `-*` removes all prior plugins, and subsequent entries re-enable specific plugins.
- **agt-env behavior:** `_PluginList` stores `self.removals = set()`. When `"-*"` is encountered, `"*"` is added to `self.removals`. Any subsequent check to `plugins.removed(name)` checks `fnmatch(name, "*")` and returns `True`, ignoring any subsequent re-additions.
- **Impact:** Configurations that disable all plugins and selectively re-enable one (e.g. `["-*", "my-plugin"]`) report all plugins as disabled.
- **Fix:** Evaluate plugin operations sequentially in order rather than storing an immutable removals set.

### OC-16 — Local plugin prefix `"~"` is treated as local by adapter, but OpenCode treats it as an npm package (Medium)
- **Where:** [`src/agt_env/adapters/opencode.py:46, 335`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/opencode.py#L46#L335)
- **OpenCode behavior:** In OpenCode (`packages/core/src/config/plugin/source.ts:140-145`):
  ```typescript
  const target = operation.target.startsWith("file://")
    ? fileURLToPath(operation.target)
    : operation.target.startsWith("./") || operation.target.startsWith("../")
      ? path.resolve(directory, operation.target)
      : operation.target
  ```
  OpenCode only treats `file://`, `./`, and `../` as local file targets. Any target beginning with `~` is left as-is and resolved via npm/bun package resolution.
- **agt-env behavior:** [`opencode.py:46`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/opencode.py#L46) defines:
  ```python
  _LOCAL_PLUGIN_PREFIXES = ("file://", "./", "../", "~")
  ```
  It expands `~` and attempts to treat it as a local plugin directory.
- **Impact:** Non-local package specifiers beginning with `~` are mistakenly handled as local paths.
- **Fix:** Remove `"~"` from `_LOCAL_PLUGIN_PREFIXES`.

### OC-18 — Local GUI-only plugins without backend entrypoint are misreported as `Status.BROKEN` (Medium)
- **Where:** [`src/agt_env/adapters/opencode.py:427-432`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/opencode.py#L427-L432)
- **OpenCode behavior:** In OpenCode (`packages/core/src/config/plugin/source.ts:168`), a plugin directory without a server entrypoint is valid if it has a GUI entrypoint (`gui-experimental.cjs` or `gui-experimental.js`):
  ```typescript
  const entrypoint = entrypoints.server ?? entrypoints.gui
  if (!entrypoint) return []
  ```
- **agt-env behavior:** `opencode.py:427-432` only checks `server.*`, `index.*`, or `package.json`. If a directory only contains `gui-experimental.cjs`, `_local_plugin_is_valid` returns `False`, and `agt-env` reports it as `Status.BROKEN` with `"missing server or index entry file"`.
- **Impact:** Valid GUI-experimental plugins are falsely marked `broken`.
- **Fix:** Add `gui-experimental` to `_PLUGIN_ENTRY_NAMES`.

---

## 6. System & Platform Discovery

### OC-14 — System-managed resources (`/etc/opencode/skills`, `agents`, `commands`, `plugins`) are ignored by adapter (Medium)
- **Where:** [`src/agt_env/adapters/opencode.py:90-125`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/opencode.py#L90-L125), [`docs/agents/opencode.md:86`](file:///home/zengxg/projects/agent-env/docs/agents/opencode.md#L86)
- **OpenCode behavior:** OpenCode discovers system-level configuration entries from `/etc/opencode` (on Linux) via `config.entries()`. This includes skills in `/etc/opencode/skills`, subagents in `/etc/opencode/agents`, commands in `/etc/opencode/commands`, and plugins in `/etc/opencode/plugins`.
- **agt-env behavior:** `opencode.py` only reads `/etc/opencode/opencode.json` (as a config layer in `_layers`), but completely omits `/etc/opencode` from `roots` in `scan()`, from `_AGENT_SOURCES`, and from plugin search paths.
- **Impact:** System-wide installed skills, subagents, commands, and plugins deployed under `/etc/opencode/` are omitted from the report.
- **Fix:** Include `system_dir` in `roots`, `_AGENT_SOURCES`, and plugin discovery.

### OC-23 — macOS MDM Managed Preferences Plist (`ai.opencode.managed.plist`) is ignored (Low)
- **Where:** [`src/agt_env/adapters/opencode.py:240-250`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/opencode.py#L240-L250)
- **OpenCode behavior:** On macOS, OpenCode reads enterprise MDM policy from `/Library/Preferences/ai.opencode.managed.plist` (`packages/core/src/config/source.ts:88`).
- **agt-env behavior:** Only `/etc/opencode` is probed as the system configuration directory.
- **Impact:** Managed settings deployed via macOS MDM are not inspected.
- **Fix:** Check for macOS managed preferences plist when running on darwin.

### OC-24 — Windows system directory path mismatch (`%ProgramData%\opencode` vs `\etc\opencode`) (Low)
- **Where:** [`src/agt_env/adapters/opencode.py:241`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/opencode.py#L241)
- **OpenCode behavior:** On Windows, OpenCode locates system configuration in `%ProgramData%\opencode`.
- **agt-env behavior:** `system_dir` hardcodes `/etc/opencode` on all platforms.
- **Impact:** Windows system-level OpenCode configurations are not discovered.
- **Fix:** Use platform-appropriate system directory (`%ProgramData%\opencode` on Windows).

---

## 7. Documentation, Safety, & Testing

### OC-25 — Credential store documentation lists `auth.json` as assumed, whereas OpenCode uses `opencode.db` and `service.json` (Low)
- **Where:** [`docs/agents/README.md:12`](file:///home/zengxg/projects/agent-env/docs/agents/README.md#L12), [`tests/test_safety.py:31`](file:///home/zengxg/projects/agent-env/tests/test_safety.py#L31)
- **OpenCode behavior:** OpenCode stores authenticated provider credentials in an SQLite database at `~/.local/share/opencode/opencode.db` (`CredentialTable` in `packages/core/src/credential/sql.ts`). Daemon service authentication tokens are stored in `~/.local/share/opencode/service.json` (`packages/core/src/persistent-pty/daemon.ts:208`). Legacy `auth.json` was migrated into SQLite via database migration `20260805200742_import_legacy_credentials.ts`.
- **agt-env behavior:** `docs/agents/README.md` and `test_safety.py` list `~/.local/share/opencode/auth.json` under "Assumed" credentials, omitting `opencode.db` and `service.json`.
- **Impact:** Safety audits do not monitor the active database or service token files.
- **Fix:** Update `docs/agents/README.md` and `tests/test_safety.py` to audit `opencode.db` and `service.json`.

### OC-26 — `docs/agents/opencode.md` claims `OPENCODE_DISABLE_*` env vars are "seen only in binary", but they are officially documented (Low)
- **Where:** [`docs/agents/opencode.md:127-128`](file:///home/zengxg/projects/agent-env/docs/agents/opencode.md#L127-L128)
- **Documentation text:** *"Environment variables seen only in the binary: OPENCODE_DISABLE_CLAUDE_CODE, OPENCODE_DISABLE_CLAUDE_CODE_SKILLS, OPENCODE_DISABLE_PROJECT_CONFIG, OPENCODE_DISABLE_AUTO_UPDATE, OPENCODE_CONFIG_DIR, OPENCODE_PORT, OPENCODE_HOSTNAME."*
- **Reality:** `OPENCODE_CONFIG_DIR`, `OPENCODE_DISABLE_PROJECT_CONFIG`, `OPENCODE_PORT`, and `OPENCODE_HOSTNAME` are prominently documented in OpenCode's official documentation (`https://opencode.ai/docs/cli`, `rules.mdx`).
- **Fix:** Update documentation notes in `opencode.md` to reflect official documentation status.

---

## Resolution

Every claim was checked against `opencode` 2.2.5, its source at tag `v2.2.5`, and the docs. Of 27 findings, 24 led to a change, and OC-12, OC-17, and OC-24 were left as they are. Rules and evidence are in `docs/agents/opencode.md`.

| ID | Outcome | What differed from the review |
|---|---|---|
| OC-01 | Fixed | The V1 docs say the directory adds a layer, but the real agent replaces the global one |
| OC-02 | Fixed | |
| OC-03 | Fixed | |
| OC-04 | Fixed | A number was misparsed too. A `timeout` with a `type` of `local` or `remote` still is a server |
| OC-05 | Fixed | V2 wins a name in both |
| OC-06 | Fixed | The code differed from the review's quote, but the effect was as described |
| OC-07 | Fixed | |
| OC-08 | Fixed | Modes aren't searched in subdirectories, and beat a same-named agent |
| OC-09 | Fixed | |
| OC-10 | Fixed | |
| OC-11 | Fixed | Applies by ID and pattern. `opencode.*` entries never load a package |
| OC-12 | **Not valid, unchanged** | An invalid entry is dropped when its file is read, so the lower one survives. Tested in V1 and V2 form. The code was right |
| OC-13 | Fixed | |
| OC-14 | Fixed | The system directory wins over all others, tested in a private `/etc` |
| OC-15 | Fixed | Only for `skills` entries starting `~/`. Variables in `XDG_CONFIG_HOME` and the like are no longer expanded either, as OpenCode doesn't |
| OC-16 | Fixed | |
| OC-17 | **Not valid, unchanged** | Permissions match by ID, so same-ID skills get the same decision, and nothing differs. A skill off because its built-in plugin is removed doesn't take the ID, so the current rule is right |
| OC-18 | Fixed | A `.ts` file doesn't count |
| OC-19 | Fixed | |
| OC-20 | Fixed | `_layers` already read it, so only detection was missing. A relative path doesn't load through the service, so it's ignored |
| OC-21 | Fixed | The newer name decides when set, even to something false |
| OC-22 | Fixed | The review's quote is off: `.claude/skills` wasn't checked either, and isn't added, since that directory says nothing about OpenCode |
| OC-23 | Fixed | Read with `plistlib`, without running anything. Tested only with fixtures |
| OC-24 | **Out of scope, unchanged** | The PRD puts Linux and macOS first, and no adapter handles Windows paths |
| OC-25 | Fixed | `service.json` is in the global config directory, not `~/.local/share/opencode` |
| OC-26 | Fixed | |
| OC-27 | Fixed | |

Testing also found that turning project config off drops a project's skills, agents, commands, and plugins too, not just its config files, so that's fixed as well.
