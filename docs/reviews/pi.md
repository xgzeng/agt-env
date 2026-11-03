# Pi Discovery & Documentation Review

Audit of `pi` resource discovery across `src/agt_env/adapters/pi.py` (and shared helpers in `adapters/base.py`, `skills.py`, and `mcp.py`), `docs/agents/pi.md`, `docs/agents/README.md`, `docs/PRD.md`, and `tests/test_pi_oracle.py`.

**Verification baseline:** Upstream `earendil-works/pi` open-source TypeScript repository (`v1.5.0`, commit `f9a26365568127461c47a07a37dcb42c140a3ac5`), cross-checked against live isolated non-interactive runs of `pi 1.5.0` (`pi --mode rpc --no-session` commands and `pi mcp list` inside temporary `HOME` directories), official documentation at `docs/*.md` in the Pi repository, `docs/agents/pi.md`, `src/agt_env/adapters/pi.py`, and `tests/test_pi_oracle.py`.

---

## Summary of Findings

| Done | ID | Severity | Area | Summary |
|---|---|---|---|---|
| [x] | PI-01 | High | Packages | `package.json` manifest with empty or metadata-only `pi` object drops conventional package skills and prompts |
| [x] | PI-02 | High | Packages | Packages in `settings.json` are not deduplicated across scopes and never marked shadowed |
| [x] | PI-03 | High | Packages | Package source regex fails to parse SCP-style git URLs (`git:git@host:path`) and `git://` protocol |
| [x] | PI-04 | High | Skills | Standalone Markdown skills without frontmatter `name` take folder name in Pi, but take filename stem in adapter |
| [x] | PI-05 | High | Skills | Subdirectory Markdown skills in `.agents/skills` (`*/*.md`) are discovered by Pi, but ignored by adapter |
| [x] | PI-06 | Medium | Precedence | User vs project skill deduplication via `load_once` preserves user item if project symlinks to user, inverting precedence |
| [x] | PI-07 | Medium | Skills | Skills in directories with `.gitignore`, `.ignore`, or `.fdignore` are filtered by Pi but discovered by adapter |
| [x] | PI-08 | High | MCP | Dual transport servers (`command` + `url`) prioritize `command` (stdio) in adapter, but prioritize `url` (HTTP) in Pi |
| [x] | PI-09 | High | MCP | Legacy SSE transport (`type: "sse"`) is rejected by Pi as a config error, but reported as valid enabled HTTP by adapter |
| [x] | PI-10 | High | MCP | Disabling built-in MCP via `!builtin:mcp` or `!builtin:*` is ignored, and project re-enablement `+builtin:mcp` fails |
| [x] | PI-11 | Medium | MCP | Server namespace conflict check between `-` and `_` is omitted by adapter |
| [x] | PI-12 | Medium | MCP | Project HTTP server with provider `auth` is rejected by Pi, but accepted by adapter |
| [x] | PI-13 | Medium | MCP | Invalid server name characters (spaces, dots, slashes) are rejected by Pi, but accepted by adapter |
| [x] | PI-14 | High | Extensions | Subdirectory extensions declaring multiple entry points via `pi.extensions` are reported as a single folder item |
| [ ] | PI-15 | High | Extensions | Extensions configured as package specifiers (`npm:...`, `git:...`) in `extensions` setting are silently dropped |
| [ ] | PI-16 | Medium | Precedence | Extensions are not deduplicated or shadowed across user and project layers |
| [x] | PI-17 | Medium | Precedence | User vs project extension deduplication via `seen` preserves user item if project symlinks to user |
| [x] | PI-18 | Medium | Commands | Prompt template fallback description is truncated to 60 chars with `...` in Pi, but kept full length in adapter |
| [x] | PI-19 | Medium | Precedence | User vs project prompt deduplication via `seen` preserves user prompt if project symlinks to user |
| [ ] | PI-20 | High | Trust | Unreadable user `settings.json` causes adapter to treat project as trusted and load project settings into layers |
| [x] | PI-21 | Medium | Isolation | Process-global `expanduser()` in `_Resources._resolve` and `_package_dir` leaks host `HOME` |
| [x] | PI-22 | Low | Configured | Configured check ignores `~/.agents/skills` and `.agents/skills` |
| [ ] | PI-23 | Low | Platform | Case-sensitive trust path comparison on Windows |
| [x] | PI-24 | Low | Docs | Documentation in `docs/agents/pi.md:106` regarding missing packages blocking startup is outdated in 1.5.0 |
| [x] | PI-25 | Low | Docs | `PI_PACKAGE_DIR` environment variable is not documented |

---

## 1. Packages & Package Manifests

### PI-01 — `package.json` manifest with empty or metadata-only `pi` object drops conventional package skills and prompts (High)
- **Where:** [`src/agt_env/adapters/pi.py:431-433`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/pi.py#L431-L433), [`docs/agents/pi.md:78`](file:///home/zengxg/projects/agent-env/docs/agents/pi.md#L78)
- **Pi behavior:** In Pi (`packages/coding-agent/src/core/package-manager.ts:686-687, 2382`):
  ```typescript
  // A `pi` object counts as an explicit resource manifest only when it lists resources. Packages whose
  // `pi` object holds only metadata (changelogPath, gallery image/video) still use directory auto-discovery.
  function hasManifestResourceFields(manifest: PiManifest): boolean {
      return RESOURCE_TYPES.some((resourceType) => manifest[resourceType] !== undefined);
  }
  ```
  A package's `pi` object only replaces conventional discovery if it explicitly defines one of `extensions`, `skills`, `prompts`, or `themes`. If `pi` is `{}` or contains only metadata like `changelogPath`, `hasManifestResourceFields` is false, and Pi falls back to conventional directories (`skills/`, `prompts/`).
- **agt-env behavior:** In [`pi.py:431-433`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/pi.py#L431-L433):
  ```python
  manifest = package.get("pi") if isinstance(package, dict) else None
  if not isinstance(manifest, dict):
      return find(root / key, warnings)
  declared = manifest.get(key)
  ```
  `agt-env` assumes that ANY dictionary under `pi` replaces conventional directories. If `pi: {}`, `manifest.get("skills")` is None, returning `[]` and dropping all conventional skills and prompts.
- **Impact:** Directly breaks oracle test `test_package_skills_match[empty_manifest]`, causing real skills in packages with `pi: {}` or metadata to be dropped.
- **Fix:** Check whether `manifest` contains any resource fields (`extensions`, `skills`, `prompts`, `themes`); if none are present, fall back to conventional directory search.

### PI-02 — Packages in `settings.json` are not deduplicated across scopes and never marked shadowed (High)
- **Where:** [`src/agt_env/adapters/pi.py:308-323`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/pi.py#L308-L323), [`src/agt_env/adapters/pi.py:127`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/pi.py#L127), [`docs/agents/pi.md:82, 84`](file:///home/zengxg/projects/agent-env/docs/agents/pi.md#L82)
- **Pi behavior:** In Pi (`packages/coding-agent/src/core/package-manager.ts:988, 1854-1875` `dedupePackages`), project-scoped packages take precedence over user-scoped packages with the same package identity (`npm:<name>`, `git:<host>/<path>`, `local:<path>`). If a trusted project defines the same package as global settings, the project package replaces the user package.
- **agt-env behavior:** `_packages` iterates all layers (user, then project) and appends a `plugin_item` for every package entry. `Pi.scan` never calls `mark_shadowed` or deduplication on package items.
- **Impact:** When both user and project configure the same package, `agt-env` reports both as `Status.ENABLED`, neither being shadowed.
- **Fix:** Deduplicate packages by package identity and mark conflicting lower-precedence packages as `Status.SHADOWED`.

### PI-03 — Package source regex fails to parse SCP-style git URLs (`git:git@host:path`) and `git://` protocol (High)
- **Where:** [`src/agt_env/adapters/pi.py:46, 412`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/pi.py#L46), [`docs/agents/pi.md:107`](file:///home/zengxg/projects/agent-env/docs/agents/pi.md#L107)
- **Pi behavior:** In Pi (`packages/coding-agent/src/utils/git.ts:172-225`, `test/git-ssh-url.test.ts:37`), `parseGitUrl` fully supports SCP-style URLs like `git:git@github.com:user/repo` and URLs with `git://`, resolving them to host `github.com` and path `user/repo` (installed at `git/github.com/user/repo`).
- **agt-env behavior:** In `pi.py:46`:
  ```python
  _GIT_SOURCE = re.compile(r"(?:git:|https?://|ssh://)(?:[^/@]+@)?([^:@]+?)(?:\.git)?(?:@[^/]*)?")
  ```
  The `[^:@]+?` pattern stops at `:` in `git:git@github.com:user/repo` and fails to match the repository path, and `git://` is omitted from the protocol alternatives. `docs/agents/pi.md:107` noted this bug as an assumption (*"`git@host:path` sources: a source in this form found no installed copy"*).
- **Impact:** Skills and prompts from packages defined with SCP-style or `git://` URLs are completely missed by `agt-env`.
- **Fix:** Update `_GIT_SOURCE` to support SCP-style colon syntax and `git://` protocol.

---

## 2. Skills Discovery & Precedence

### PI-04 — Standalone Markdown skills without frontmatter `name` take folder name in Pi, but take filename stem in adapter (High)
- **Where:** [`src/agt_env/adapters/pi.py:239`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/pi.py#L239), [`src/agt_env/adapters/base.py:92`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/base.py#L92), [`docs/agents/pi.md:46`](file:///home/zengxg/projects/agent-env/docs/agents/pi.md#L46)
- **Pi behavior:** In Pi (`packages/coding-agent/src/core/skills.ts:311, 321`):
  ```typescript
  const skillDir = dirname(filePath);
  const parentDirName = basename(skillDir);
  const name = frontmatterName || parentDirName;
  ```
  For standalone files in `skills/` (e.g. `~/.pi/agent/skills/helper.md`), if `name` is omitted from frontmatter, Pi uses `parentDirName`, which is `"skills"` (resulting in `skill:skills`).
- **agt-env behavior:** In `base.py:92`, `skill_item` uses `fields.get("name") or skill.default_name`. For standalone files, `default_name` is `path.stem` (`"helper"`).
- **Impact:** Discrepancy in skill naming. Multiple standalone skills without explicit names collide in Pi under `"skills"` (causing collision warnings), but appear distinct in `agt-env`.
- **Fix:** In `pi.py`, set default skill name for standalone skills to `skill.path.parent.name` to match Pi's behavior.

### PI-05 — Subdirectory Markdown skills in `.agents/skills` (`*/*.md`) are discovered by Pi, but ignored by adapter (High)
- **Where:** [`src/agt_env/adapters/pi.py:217-220`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/pi.py#L217-L220), [`docs/agents/pi.md:113`](file:///home/zengxg/projects/agent-env/docs/agents/pi.md#L113)
- **Pi behavior:** In Pi (`packages/coding-agent/src/core/package-manager.ts:451-459`):
  ```typescript
  const shouldIncludeMarkdownFile =
      isFile &&
      entry.name.endsWith(".md") &&
      !ig.ignores(relPath) &&
      ((mode === "pi" && dir === root) || (mode === "agents" && dir !== root));
  ```
  For `mode === "agents"` (`.agents/skills`), Pi discovers and loads markdown files inside subdirectories (e.g. `.agents/skills/group/tool.md`).
- **agt-env behavior:** `pi.py:217` explicitly checks `if root.parent.name != ".agents": scan_markdown_skills(root)`, skipping markdown skills for `.agents`. `scan_skills` only looks for `SKILL.md` in subdirectories.
- **Impact:** `docs/agents/pi.md:113` claims as an untested assumption that standalone skills in `.agents/skills` don't load. In reality, Pi loads `.agents/skills/*/*.md`, while `agt-env` completely omits them.
- **Fix:** Allow scanning subdirectory `.md` files in `.agents/skills` roots.

### PI-06 — User vs project skill deduplication via `load_once` preserves user item if project symlinks to user (Medium)
- **Where:** [`src/agt_env/adapters/pi.py:208-215, 261`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/pi.py#L208-L215)
- **Pi behavior:** In Pi, project skills take precedence over user skills (`_RANK_PI_SKILLS < _RANK_AGENT_DIR`).
- **agt-env behavior:** `unique_roots` receives user roots before project roots. When `load_once(items)` runs on line 261, it keeps the first item with a given real path (`os.path.realpath`). If a project skill directory symlinks to a user skill, `load_once` keeps the user-scoped item and drops the project-scoped item, inverting precedence.
- **Impact:** Project overrides that symlink to user files lose their project scope and are incorrectly ranked as user skills.
- **Fix:** Populate project roots before user roots or ensure `load_once` prefers `Scope.PROJECT` items over `Scope.USER`.

### PI-07 — Skills in directories with `.gitignore`, `.ignore`, or `.fdignore` are filtered by Pi but discovered by adapter (Medium)
- **Where:** [`src/agt_env/adapters/pi.py:216`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/pi.py#L216), [`src/agt_env/skills.py:36-60`](file:///home/zengxg/projects/agent-env/src/agt_env/skills.py#L36-L60)
- **Pi behavior:** In Pi (`skills.ts:16, 51-64, 189`), Pi scans for `.gitignore`, `.ignore`, and `.fdignore` at every directory level, and skips any files/directories matched by ignore rules.
- **agt-env behavior:** `scan_skills` only ignores hardcoded `.git` and `node_modules`. It never parses ignore files.
- **Impact:** Skills ignored by git or ignore files are loaded by `agt-env` as enabled.
- **Fix:** Support ignore patterns or document the limitation in `pi.md`.

---

## 3. MCP Servers & Configuration

### PI-08 — Dual transport servers (`command` + `url`) prioritize `command` (stdio) in adapter, but prioritize `url` (HTTP) in Pi (High)
- **Where:** [`src/agt_env/adapters/pi.py:533-536`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/pi.py#L533-L536), [`docs/agents/pi.md:59`](file:///home/zengxg/projects/agent-env/docs/agents/pi.md#L59)
- **Pi behavior:** In Pi (`packages/coding-agent/src/core/mcp-servers.ts:284, 304`), `url` is checked first when `type` is undefined (`if (typeof value.url === "string" && (type === undefined ...))`). If both `command` and `url` are present, Pi treats the server as HTTP streamable.
- **agt-env behavior:** `_server_details` checks `if isinstance(entry.get("command"), str): return stdio_details(...)` first, returning stdio transport.
- **Impact:** Servers with both `command` and `url` are reported as stdio by `agt-env`, while Pi connects via HTTP.
- **Fix:** Check `url` before `command` when `type` is unspecified, matching Pi's evaluation order.

### PI-09 — Legacy SSE transport (`type: "sse"`) is rejected by Pi as a config error, but reported as valid enabled HTTP by adapter (High)
- **Where:** [`src/agt_env/adapters/pi.py:535`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/pi.py#L535), [`docs/agents/pi.md:59`](file:///home/zengxg/projects/agent-env/docs/agents/pi.md#L59)
- **Pi behavior:** In Pi (`packages/coding-agent/src/core/mcp-servers.ts:282`):
  ```typescript
  if (type === "sse") return `server "${name}": legacy SSE transport is not supported; use the streamable HTTP URL`;
  ```
  Pi explicitly rejects `type: "sse"`.
- **agt-env behavior:** `_server_details` ignores `type` when `url` is present, returning `remote_details("streamable_http", ...)` and marking the server `Status.ENABLED`.
- **Impact:** Broken SSE server configurations are reported as active and enabled.
- **Fix:** Return `None` from `_server_details` when `entry.get("type") == "sse"`.

### PI-10 — Disabling built-in MCP via `!builtin:mcp` or `!builtin:*` is ignored, and project re-enablement `+builtin:mcp` fails (High)
- **Where:** [`src/agt_env/adapters/pi.py:128-132, 281-285`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/pi.py#L128-L132)
- **Pi behavior:** In Pi (`packages/coding-agent/src/core/package-manager.ts:1027-1039`), built-in extensions (including `builtin:mcp`) are evaluated using `applyAutoloadDisabledPatterns`, which accepts `!builtin:mcp`, `!builtin:*`, and `-builtin:mcp`. A matching `+builtin:mcp` in project settings re-enables MCP even if user settings disabled it.
- **agt-env behavior:** `mcp_off = any(f"-{_MCP_EXTENSION}" in values for settings, ...)` only checks exact string `"-builtin:mcp"`. In `listed.add`, `"builtin:" not in v` discards any `builtin:` patterns, so globs like `!builtin:*` are ignored. Furthermore, `any(...)` never allows project settings to override user settings with `+builtin:mcp`.
- **Impact:** MCP remains reported as enabled when disabled via `!`, and remains disabled when project re-enables it.
- **Fix:** Parse override patterns (`+`, `-`, `!`) with glob matching for `builtin:` extensions and let project layer override user layer.

### PI-11 — Server namespace conflict check between `-` and `_` is omitted by adapter (Medium)
- **Where:** [`src/agt_env/adapters/pi.py:329-354`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/pi.py#L329-L354)
- **Pi behavior:** In Pi (`packages/coding-agent/src/extensions/mcp/config.ts:129`, `core/mcp-servers.ts:167` `mcpNamespace`), server tool namespaces convert `-` to `_`. Pi rejects any two servers that share the same normalized namespace (e.g. `foo-bar` and `foo_bar`) with: `server "foo_bar" conflicts with "foo-bar"`.
- **agt-env behavior:** `pi.py` indexes servers by raw dictionary key `name`. It reports both servers as valid and enabled.
- **Impact:** Conflicting servers are reported as valid when Pi refuses to load the latter.
- **Fix:** Track normalized `name.replace("-", "_")` namespaces and report clashing servers as `Status.BROKEN`.

### PI-12 — Project HTTP server with provider `auth` is rejected by Pi, but accepted by adapter (Medium)
- **Where:** [`src/agt_env/adapters/pi.py:350-354`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/pi.py#L350-L354)
- **Pi behavior:** In Pi (`packages/coding-agent/src/extensions/mcp/config.ts:134-137`, `CHANGELOG.md:499`), `"auth": { "provider": "..." }` is strictly prohibited in project `mcp.json`. Pi rejects it with `auth is only allowed in the global mcp.json`.
- **agt-env behavior:** `pi.py` accepts `auth` in project `mcp.json` and reports the server as `Status.ENABLED`.
- **Impact:** Disallowed project credential delegations are falsely shown as active.
- **Fix:** Mark project servers with `"auth"` as `Status.BROKEN`.

### PI-13 — Invalid server name characters (spaces, dots, slashes) are rejected by Pi, but accepted by adapter (Medium)
- **Where:** [`src/agt_env/adapters/pi.py:329-354`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/pi.py#L329-L354)
- **Pi behavior:** In Pi (`core/mcp-servers.ts:164, 261`), `SERVER_NAME = /^[A-Za-z0-9_-]+$/`. Names with spaces, dots, or slashes fail validation.
- **agt-env behavior:** `pi.py` accepts any string key in `mcpServers`.
- **Impact:** Malformed server names are reported as valid.
- **Fix:** Validate server name against `^[A-Za-z0-9_-]+$`.

---

## 4. Extensions Discovery & Packages

### PI-14 — Subdirectory extensions declaring multiple entry points via `pi.extensions` are reported as a single folder item (High)
- **Where:** [`src/agt_env/adapters/pi.py:492-510, 304-306`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/pi.py#L492-L510), [`docs/agents/pi.md:70`](file:///home/zengxg/projects/agent-env/docs/agents/pi.md#L70)
- **Pi behavior:** In Pi (`packages/coding-agent/src/core/extensions/loader.ts:753-769`), if a subdirectory extension's `package.json` contains `pi.extensions: ["./ext1.ts", "./ext2.ts"]`, Pi resolves and loads each declared extension file individually.
- **agt-env behavior:** `_declares_extensions` returns a boolean `bool(pi.get("extensions"))`, and `_extensions` emits a single `plugin_item` whose path is the directory `path`.
- **Impact:** Multiple extension entrypoints within a package are collapsed into a single directory item, missing individual extension files and names.
- **Fix:** If `pi.extensions` is present, emit items for each declared extension file in the manifest.

### PI-15 — Extensions configured as package specifiers (`npm:...`, `git:...`) in `extensions` setting are silently dropped (High)
- **Where:** [`src/agt_env/adapters/pi.py:277-306`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/pi.py#L277-L306)
- **Pi behavior:** In Pi (`packages/coding-agent/src/core/package-manager.ts:1045-1060` `resolveExtensionSources`), entries in `extensions: [...]` that are not local paths are resolved and installed as npm/git packages.
- **agt-env behavior:** `pi.py:288` resolves all listed extensions as `base / Path(value)`. An npm or git package specifier is not a local directory, fails `_is_extension`, and is silently dropped.
- **Impact:** Extensions configured via package specifiers in `extensions` are completely omitted.
- **Fix:** Parse package specifiers in `extensions` setting and discover them via `_package_dir`.

### PI-16 — Extensions are not deduplicated or shadowed across user and project layers (Medium)
- **Where:** [`src/agt_env/adapters/pi.py:290-307`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/pi.py#L290-L307)
- **Pi behavior:** In Pi, project extensions override same-named user extensions.
- **agt-env behavior:** `_extensions` appends all candidates and never calls `mark_shadowed`. Same-named extensions across user and project are both emitted as `Status.ENABLED`.
- **Impact:** Shadowed user extensions are incorrectly reported as enabled.
- **Fix:** Call `mark_shadowed` on extensions across scopes.

### PI-17 — User vs project extension deduplication via `seen` preserves user item if project symlinks to user (Medium)
- **Where:** [`src/agt_env/adapters/pi.py:272-296`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/pi.py#L272-L296)
- **Pi behavior:** Project resources take precedence over user resources.
- **agt-env behavior:** `candidates` iterates user extensions before project extensions. When `real in seen` runs on line 294, the project extension is discarded and the user extension is preserved.
- **Impact:** Symlinked project extensions are misreported as `Scope.USER`.
- **Fix:** Iterate project extensions before user extensions or prioritize `Scope.PROJECT` in `seen`.

---

## 5. Prompts (Custom Commands)

### PI-18 — Prompt template fallback description is truncated to 60 characters with `...` in Pi, but kept full length in adapter (Medium)
- **Where:** [`src/agt_env/adapters/pi.py:185`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/pi.py#L185), [`docs/agents/pi.md:94`](file:///home/zengxg/projects/agent-env/docs/agents/pi.md#L94)
- **Pi behavior:** In Pi (`packages/coding-agent/src/core/prompt-templates.ts:141-143`):
  ```typescript
  description = firstLine.slice(0, 60);
  if (firstLine.length > 60) description += "...";
  ```
- **agt-env behavior:** `description = fields.get("description") or first_line(prompt.body)`. `first_line` retains the entire line without truncation.
- **Impact:** Discrepancy in command description lengths and formatting between `agt-env` and Pi's TUI/RPC.
- **Fix:** Truncate fallback prompt descriptions to 60 characters and append `...` if longer.

### PI-19 — User vs project prompt deduplication via `seen` preserves user prompt if project symlinks to user (Medium)
- **Where:** [`src/agt_env/adapters/pi.py:141-171`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/pi.py#L141-L171)
- **Pi behavior:** Project prompt templates take precedence over user prompt templates (`_RANK_PI_SKILLS` rank 1 beats `_RANK_AGENT_DIR` rank 4).
- **agt-env behavior:** User prompts are added to `found` before project prompts. If a project prompt symlinks to a user prompt, `prompt.resolved_path in seen` drops the project prompt and preserves the user prompt, lowering its rank to 4.
- **Impact:** Inverted precedence hierarchy for symlinked prompt templates.
- **Fix:** Order project prompts before user prompts in `found`.

---

## 6. Project Trust & Configuration

### PI-20 — Unreadable user `settings.json` causes adapter to treat project as trusted and load project settings into layers (High)
- **Where:** [`src/agt_env/adapters/pi.py:112-119, 517`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/pi.py#L112-L119)
- **Pi behavior:** In Pi (`packages/coding-agent/src/core/trust-manager.ts`), project trust protects project settings. If user configuration cannot be validated or is missing, Pi refuses to run untrusted project settings.
- **agt-env behavior:** If `user_ok == False`, `_project_trust` returns `Status.UNKNOWN`. Then on line 116:
  ```python
  if trust is not Status.DISABLED:
      layers.append((project_settings, project_dir, Scope.PROJECT, ...))
  ```
  Because `Status.UNKNOWN is not Status.DISABLED`, project settings are loaded into `layers` and evaluated as active!
- **Impact:** Untrusted project configurations are loaded when user settings are unreadable.
- **Fix:** Only append project settings if `trust in (Status.ENABLED, Status.PENDING)` and `user_ok` is true.

### PI-21 — Process-global `expanduser()` in `_Resources._resolve` and `_package_dir` leaks host `HOME` (Medium)
- **Where:** [`src/agt_env/adapters/pi.py:70, 417`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/pi.py#L70)
- **Pi behavior:** Pi expands tildes relative to the active home directory.
- **agt-env behavior:** `Path(value).expanduser()` uses `os.environ["HOME"]` (host runner environment), bypassing `context.home`.
- **Impact:** Tildes expand to the host machine's home directory instead of the test or isolated context.
- **Fix:** Replace `expanduser()` with a helper that resolves `~` against `context.home`.

### PI-22 — Configured check ignores `~/.agents/skills` and `.agents/skills` (Low)
- **Where:** [`src/agt_env/adapters/pi.py:102`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/pi.py#L102)
- **Pi behavior:** Pi discovers and loads skills from `.agents/skills` and `~/.agents/skills` out of the box.
- **agt-env behavior:** `config_path = first_existing(agent_dir, project_dir)` only checks `~/.pi/agent` and `.pi`. If skills exist only in `.agents/skills`, Pi is marked as unconfigured.
- **Impact:** Agent is hidden by default when configured solely with standard `.agents/skills`.
- **Fix:** Include `.agents/skills` and `~/.agents/skills` in `first_existing` candidate check.

### PI-23 — Case-sensitive trust path comparison on Windows (Low)
- **Where:** [`src/agt_env/adapters/pi.py:521-523`](file:///home/zengxg/projects/agent-env/src/agt_env/adapters/pi.py#L521-L523)
- **Pi behavior:** In Pi (`trust-manager.ts:46`), `findTrustFileKeys` uses case-insensitive path equality (`pathsEqual`).
- **agt-env behavior:** `saved.get(str(directory))` performs case-sensitive dictionary lookup.
- **Impact:** Drive letter casing mismatches on Windows cause saved trust decisions to be missed.
- **Fix:** Perform case-insensitive path comparison when looking up `trust.json` entries on Windows.

### PI-24 — Documentation in `docs/agents/pi.md:106` regarding missing packages blocking startup is outdated in 1.5.0 (Low)
- **Where:** [`docs/agents/pi.md:106`](file:///home/zengxg/projects/agent-env/docs/agents/pi.md#L106)
- **Documentation text:** *"A missing package blocks startup: with an uninstallable npm package in settings, `pi list` and RPC mode failed with the npm error. The docs say Pi warns, skips the package, and keeps running."*
- **Reality:** Upstream Pi 1.5.0 fixed this in PR #11304 (CHANGELOG line 88): *"Fixed a configured package failing to install crashing startup. pi now skips the package, reports an extension warning, and retries on the next start or /reload"*.
- **Fix:** Update `pi.md:106` to record that this is fixed in 1.5.0.

### PI-25 — `PI_PACKAGE_DIR` environment variable is not documented (Low)
- **Where:** [`docs/agents/pi.md:21`](file:///home/zengxg/projects/agent-env/docs/agents/pi.md#L21)
- **Reality:** Pi recognizes `PI_PACKAGE_DIR` (`packages/coding-agent/src/config.ts:420`) to override the install root directory for packages and themes.
- **Fix:** Document `PI_PACKAGE_DIR` in `pi.md`.

---

## Resolution

Every claim was checked against `pi` 1.5.0, its source at tag `v1.5.0`, and the docs. Of 25 findings, 21 led to a change. PI-15, PI-16, PI-20, and PI-23 were left as they are. Rules and evidence are in `docs/agents/pi.md`.

| ID | Outcome | What differed from the review |
|---|---|---|
| PI-01 | Fixed | |
| PI-02 | Fixed | A project entry with `autoload: false` only filters, so the user's stays |
| PI-03 | Fixed | `git@host:path` without `git:` isn't a git source |
| PI-04 | Fixed | Unnamed files in one directory collide, so all but one are `shadowed` |
| PI-05 | Fixed | Any depth below the root, not in a skill's directory, and not directly in the root. Without a description they're silently skipped, so they get no row. Skills inside skills also don't load, which the code had listed |
| PI-06 | Fixed | |
| PI-07 | Documented | Confirmed, and recorded as not covered, as the review offered |
| PI-08 | Fixed | Unless `type` says otherwise |
| PI-09 | Fixed | |
| PI-10 | Fixed | In the user's settings `-` beats `+`, which beats `!`. In a project's, the last match wins. Only a trusted project counts |
| PI-11 | Fixed | Across the user's and project's files too |
| PI-12 | Fixed | |
| PI-13 | Fixed | |
| PI-14 | Fixed | One row per existing file. If none exist it falls back to the index file, and otherwise isn't an extension |
| PI-15 | **Not valid, unchanged** | `resolveExtensionSources` is for the `-e` flag. In the setting these entries are treated as paths and ignored, which is what `agt-env` does |
| PI-16 | **Not valid, unchanged** | Same-named extensions both load, tested with one in each scope. They don't shadow |
| PI-17 | Fixed | |
| PI-18 | Fixed | |
| PI-19 | Fixed | |
| PI-20 | **Not valid as described** | Those rows were `unknown`, not active, which is right when settings can't be read. Pi doesn't load the project then. The one real effect, an untrusted project turning MCP off, is fixed under PI-10 |
| PI-21 | Fixed | |
| PI-22 | Fixed | |
| PI-23 | **Out of scope, unchanged** | The PRD puts Linux and macOS first |
| PI-24 | Fixed | Tested in 1.5.0 |
| PI-25 | Fixed | It moves Pi's own install assets, which `agt-env` doesn't read |
