# Skill Search and Install Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add repository URL/public-name search and per-Skill installation to the existing Settings Skills page, usable after a fresh public deployment.

**Architecture:** A native Git reader resolves exact repository commits and complete Skill directories; a discovery service combines it with public skills.sh lookup. The installer reuses existing package staging and managed revisions/configuration. React adds an embedded search panel and an installed list; no enterprise wizard or second runtime loader.

**Tech Stack:** Python 3.12+, FastAPI, native Git, existing Python HTTP client and Store, React/TypeScript, Vitest/Testing Library, pytest.

**Spec:** `docs/superpowers/specs/2026-10-28-skill-search-install-design.md`

**Execution status (2026-10-28):** Native implementation and one independent
whole-branch review/fix pass completed. PR335 merged; standard exact-commit
deployment of `da5e1585` and live UI/API/load-receipt readback completed.
See [acceptance evidence](../../skill-search-install-validation.md) for test
counts, baseline CI failures and retained scope limits. Company Skill migration
and the separate unpublished OA optimization are still pending.

## Global Constraints

- Use the existing Settings route and managed revision/config/load receipt model.
- Initial repository support is HTTPS and SSH Git repository URLs.
- Search at most 10 public matches per submission; retain provider ordering and group repository reads. Hydrate at most two repositories concurrently.
- Native Git commands have a 30-second timeout and the search request has a 120-second total budget.
- Install means package installed and revision recorded. Managed process loading still follows the existing next-start config and load receipt flow.
- Ordinary external Skills need no ceo-agent-service ownership marker, and their exact SKILL.md bytes must not be rewritten to add one.
- No new Audit review, authorization policy, human approval stage, runtime-route branch or task lifecycle is part of this feature.
- No actual private repo creation, company-rule migration, history rewrite or publication of the existing OA candidate in this plan.
- Read `/Users/derek/.agents/AGENTS.md`, `docs/agent-claims.md`, `docs/architecture.md` and `docs/runtime-mechanism.md`; claim actual files before edits and preserve concurrent work.
- Run related pytest files individually with the existing 2 GB/10-minute limits. Never run the full laptop suite or test/edit the production checkout.

## Review Focus

1. Fresh installation without company Skills must still boot and render the search page — Task 1 tests.
2. A private URL/auth failure must not become a public keyword query or leak its content to public artifacts — Tasks 2/3 tests.
3. Multiple Skills, duplicate declared names and missing display metadata need distinct usable results — Tasks 2/3 tests.
4. Branch movement, duplicate clicks, content collisions and filesystem/DB failures must not change installed identity or corrupt existing data — Task 4 tests.
5. A successful installation is visible without a feature group and must not be labelled runtime-active without existing load evidence — Tasks 5/6 tests.

## Source map and dependency order

Planning baseline observed: `dde6148bee3fc7a91671d4f62c3a30f3006d6358`.
The shared tree contains unrelated Store/WeChat edits; this plan makes no changes
to those files. Recheck upstream and claims at execution time. Use the worktree
skill if implementation needs isolation; do not reuse the old OA candidate as
the feature branch or accidentally publish its internal assets.

| File | Responsibility |
| --- | --- |
| `app/business_skills.py`, `app/managed_skills.py` | Public baseline vs explicitly installed optional Skills; reuse package primitives and revision loading |
| `app/skill_repositories.py` (new) | Native Git reads, exact source identity, Skill metadata and deterministic package digest |
| `app/skill_discovery.py` (new) | Typed results and URL/public-index orchestration |
| `app/skill_installation.py` (new) | Complete package installation and existing runtime revision/config integration |
| `app/store.py` (one new bounded method) | One transaction for installed Skill/revision/config using current Configs storage, no schema change |
| `app/web_api/skill_installation.py` (new) | Two narrow HTTP routes and existing error envelopes |
| `app/web_api/registration.py` | Register those routes with a service factory; no unrelated route changes |
| `frontend/src/api/skills.ts` | Typed search/install HTTP functions |
| `frontend/src/components/settings/SkillSearchPanel.tsx` (new) | Search form, metadata rows and per-item Install state |
| `frontend/src/components/settings/ManagedSkillsPanel.tsx` | Embed search; installed list independent of feature selection |
| Focused new tests named below | Synthetic repositories, API contracts, UI behavior; no real company payloads |

Dependencies: Task 1 → 2 → 3 → 4 → 5 → 6 → 7. Each task has one test/reviewable
deliverable. Public/private repository migration is a separate plan; record it
as pending rather than mixing it into these commits.

### Task 1: Make first-start Skill loading independent of enterprise packages

**Files:**
- Modify: `app/business_skills.py` (`bundled_business_skills_root`, `load_bundled_business_skills`).
- Modify: `app/managed_skills.py` (`_repository_managed_skills`, `import_repository_managed_skills`, `capture_runtime_skill_edits`).
- Modify: `scripts/bootstrap-local-components.sh` (`ensure_ceo_business_skills`).
- Modify: `scripts/install-auto-reply-agents.sh` (initial public baseline installation before first worker startup only).
- Test: new `tests/test_skill_first_install.py`; relevant existing `tests/test_managed_skills.py`, `tests/test_bootstrap_local_components.py`.

**Interfaces:**
- Consumes existing bundled content validation, Store revisions and `resolve_pending_runtime_skills(store, *, pid)`.
- Produce `install_public_skill_baseline(source_root: Path, target_root: Path) -> tuple[InstalledBusinessSkill, ...]` in `app/business_skills.py`. Source is the release's `ci/shared-skills`; enumerate exactly existing `REPOSITORY_MANAGED_SKILL_NAMES`, never adjacent company folders. Reuse staging, installed-name conflict checks and idempotence; preserve existing installed/user revisions. Call it from initial installation before launch, not on each Agent turn or web request.
- Produces a public baseline that loads when no optional company package exists; runtime capture enumerates explicit installed revision identities rather than requiring hardcoded enterprise names.
- Preserve the existing required public-core baseline; missing required core content remains an error. Installed enterprise content is not imported by scanning arbitrary Git clones.

- [ ] **Step 1: Add `test_fresh_public_install_needs_no_enterprise_skills`.** Use a test-owned release source containing synthetic content for exactly `REPOSITORY_MANAGED_SKILL_NAMES`, plus an initially empty runtime root; run baseline installation, initialize Store, resolve startup and serve Settings. Assert the full generic baseline is installed, no company folder is required/copied, no Git invocation occurs, and public-core revisions load. Add `test_existing_frozen_revision_survives_optional_skill_absence` and assert the original saved body/revision is preserved.
- [ ] **Step 2: Run `python -m pytest -q tests/test_skill_first_install.py`.** Expected RED because current bootstrap only verifies files and the initial baseline installer is absent. Record the actual failure before editing; runtime-only company names already skip missing files, so do not claim that existing optional-file behavior is a new fix.
- [ ] **Step 3: Implement the public baseline installer and explicit optional installed Skill capture.** Keep service-required generic assets explicit and reuse the current local installation root. Do not convert every unreadable file into an optional Skill or suppress required-core failures. Bootstrap verifies the complete baseline, and the deployment installer supplies it before startup. Optional capture expands to explicit imported names so later repository installs do not depend on a fixed company name tuple.
- [ ] **Step 4: Run the new test file and then `tests/test_managed_skills.py` and `tests/test_bootstrap_local_components.py`, one process per file.** Expected PASS; existing active/pending/frozen revision assertions remain intact.
- [ ] **Step 5: Update the affected startup paragraphs in `docs/architecture.md` and `docs/runtime-mechanism.md`; commit only Task 1 files.** Message: `refactor: separate public Skill baseline from enterprise installation`.

### Task 2: Read a repository at an exact commit and list its Skill metadata

**Files:**
- Create: `app/skill_repositories.py`.
- Test: new `tests/test_skill_repositories.py`.

**Interfaces:**
- `SkillRepositoryReader.list_skills(repo_url: str, *, commit: str | None = None) -> tuple[RepositorySkill, ...]`.
- `SkillRepositoryReader.read_package(repo_url: str, commit: str, skill_path: str) -> SkillPackage`.
- `RepositorySkill`: repo_url, commit, skill_path, name, description, author, author_kind, updated_at, available, unavailable_reason.
- `SkillPackage`: candidate: RepositorySkill, files: tuple[PackageFile, ...], package_sha256: str; `PackageFile`: path: str, content: bytes, executable: bool.
- Native Git authentication uses the service account. Inject a command runner for synthetic/local tests; production accepts the spec's HTTPS/SSH repository URL forms, including non-GitHub private hosts.

- [ ] **Step 1: Add repository tests.** `test_multi_skill_repository_lists_only_real_skill_directories` asserts root plus both supported directory layouts. `test_author_and_time_are_sourced_not_invented` asserts author vs maintainer labels and path-specific Git timestamp. `test_duplicate_declared_name_is_unavailable` leaves valid siblings installable. `test_exact_commit_survives_branch_move` compares original package bytes after a new commit. `test_private_auth_failure_never_queries_public_index` asserts a native Git error and no HTTP call. Use local Git repos via the injected runner, not a real private URL.

  The branch-move fixture must finish with these assertions (the test creates
  `commit_before_move` and `original_bytes` before advancing its local branch):

  ```python
  package = reader.read_package(repo_url, commit_before_move, "skills/example")
  assert package.candidate.commit == commit_before_move
  assert next(f.content for f in package.files if f.path == "SKILL.md") == original_bytes
  ```
- [ ] **Step 2: Run `python -m pytest -q tests/test_skill_repositories.py`.** Expected RED until the reader exists.
- [ ] **Step 3: Implement the two signatures.** Use argv-based Git subprocesses, the standard URL parser and the existing frontmatter parser; resolve default HEAD once. Read tracked file bytes and directory-specific commit timestamps at the immutable ref. Reuse existing path/package semantics, stage only task-owned temporary checkouts and clean them on exit. Do not execute package scripts or introduce credential fallback/proxy logic.
- [ ] **Step 4: Add timeout/invalid URL/malformed-frontmatter tests and run the whole new test file.** A repository URL that fails authentication remains a repo error; unsupported tree/archive URLs produce explicit invalid-source errors, never public search. Include a synthetic non-GitHub SSH host to prove generic Git access. Expected PASS.
- [ ] **Step 5: Commit the reader and its tests.** Message: `feat: discover Skills from exact repository commits`.

### Task 3: Add public-name discovery with complete display metadata

**Files:**
- Create: `app/skill_discovery.py`.
- Test: new `tests/test_skill_discovery.py`.

**Interfaces:**
- `SkillDiscoveryService.search(query: str) -> SkillSearchResult`.
- Constructor accepts repository reader and `httpx.Client`; both injectable in tests. Use the project's existing httpx dependency, not a new CLI or HTTP library.
- `SkillSearchResult`: items: tuple[RepositorySkill, ...], errors: tuple[SkillSearchError, ...]. `SkillSearchError`: source: str, code: str, message: str.
- Public lookup: GET `https://skills.sh/api/search` with query params `q` and `limit=10`; consume `skills[].name`, `skills[].source`, `skills[].id`, then hydrate exact repository Skills through Task 2.

- [ ] **Step 1: Add `test_public_name_hydrates_description_author_and_skill_time`.** Mock an index hit with name/source/id and assert metadata comes from the matching declared Skill, not the repo README or index slug. Add `test_repository_query_never_calls_index`, `test_provider_failure_is_not_empty_success`, and `test_unavailable_match_preserves_usable_siblings`.
- [ ] **Step 2: Run `python -m pytest -q tests/test_skill_discovery.py`.** Expected RED for the absent service.
- [ ] **Step 3: Implement search dispatch and hydration.** Strip surrounding whitespace, classify URL-shaped input with the standard parser, preserve public provider ordering, group reads by repository and hydrate with at most two workers. Enforce the spec's 120-second request budget without returning empty success on failure. Missing optional metadata remains nullable and labelled; no new search provider or ranking algorithm.
- [ ] **Step 4: Add `test_limit_and_repository_deduplication`, `test_late_metadata_timeout_is_explicit`, and `test_repo_substring_is_not_broadcast_after_clone_error`; run the new test file.** Expected PASS, maximum 10 candidates and no private-repo string sent to the public index.
- [ ] **Step 5: Commit discovery and tests.** Message: `feat: search public Skills and hydrate repository metadata`.

### Task 4: Install one complete package using existing revisions

**Files:**
- Create: `app/skill_installation.py`.
- Modify: `app/business_skills.py` (extract/reuse package staging helpers only as required).
- Modify: `app/managed_skills.py` (explicit external-source registration/capture, if Task 1 did not supply it).
- Modify: `app/store.py` (new `install_repository_skill` only; claim this method separately from peers before implementation).
- Test: new `tests/test_skill_installation.py`; existing `tests/test_business_skills.py`, `tests/test_managed_skill_runtime.py`.

**Interfaces:**
- `SkillInstallationService.install(repo_url: str, commit: str, skill_path: str) -> SkillInstallResult`.
- `SkillInstallResult`: name, skill_id, revision_id, sha256, package_sha256, source, install_status (installed|already_installed), activation_status (pending_restart|active).
- `source` is versioned JSON in the existing revision source field: `{"kind":"git-skill","version":1,"repo_url":...,"commit":...,"skill_path":...,"package_sha256":...}`. Skill SHA remains exact SKILL.md UTF-8 SHA. Package SHA includes all tracked installed files and executable flags in deterministic relative-path order.
- Consume Task 2 `read_package`; use existing Store `create_managed_skill_revision(..., require_managed_marker=False)` for ordinary external content. Preserve existing config parent checks and unchanged bindings. No new schema/table or ownership marker rewriting.
- Produce `AutoReplyStore.install_repository_skill(self, name: str, display_name: str, content: str, *, source: str, expected_parent_id: int | None) -> tuple[ManagedSkillRevision, RuntimeSkillConfig]`. Reuse the current revision validator with `require_managed_marker=False` and existing Configs insert helpers inside one `_immediate_write_transaction`. Do not call multiple independently committing public DAO methods and describe that as atomic. Return the exact revision and pending config; preserve all unrelated bindings and current source/config conflict semantics.

- [ ] **Step 1: Add `test_install_copies_scripts_references_and_exact_skill_bytes`.** Assert all package files/digest/source identity and a revision with unchanged content. Add `test_install_does_not_export_into_service_checkout`, `test_same_install_is_idempotent`, and `test_name_collision_preserves_existing_skill`.

  Repeat-install assertions must compare persisted identity, not just HTTP success:

  ```python
  first = installer.install(repo_url, commit, "skills/example")
  again = installer.install(repo_url, commit, "skills/example")
  assert again.revision_id == first.revision_id
  assert again.install_status == "already_installed"
  assert len(store.list_managed_skill_revisions(first.skill_id)) == 1
  ```
- [ ] **Step 2: Run `python -m pytest -q tests/test_skill_installation.py`.** Expected RED until installer exists.
- [ ] **Step 3: Implement install using the existing filesystem staging and Store transaction boundary.** Read/validate the complete exact-commit package before mutation. Reuse the existing transaction mechanism for creating Skill/revision/config as one database operation and roll back the staged filesystem swap if it fails. Preserve previous target bytes on failure. Do not call the explicit repository export API. Same commit/package returns the prior result; no automatic update/replacement operation.
- [ ] **Step 4: Add `test_concurrent_duplicate_installs_return_one_revision`, `test_failed_db_write_restores_package`, `test_failed_package_swap_creates_no_revision`, and `test_pending_config_does_not_claim_loaded`.** Exercise filesystem and real temporary SQLite transaction failure, not just mock successful helper calls. Verify installed optional content remains discoverable and captureable after a normal process restart. Run new installer tests and each named existing file separately. Expected PASS.
- [ ] **Step 5: Update architecture/runtime installation behavior in the same commit.** Message: `feat: install repository Skill packages into versioned runtime state`.

### Task 5: Expose typed discovery/install APIs

**Files:**
- Create: `app/web_api/skill_installation.py`.
- Modify: `app/web_api/registration.py` (register factory only).
- Modify: `frontend/src/api/skills.ts`, `frontend/src/api/skills.test.ts`.
- Test: new `tests/test_console_skill_installation_api.py`.

**Interfaces:**
- `register_skill_installation_routes(app: FastAPI, *, discovery_factory, installation_factory) -> None`.
- POST `/api/console/settings/skill-search` accepts `{query: string}` and returns Task 3 `{items, errors}`.
- POST `/api/console/settings/skill-installs` accepts `{repo_url: string, commit: string, skill_path: string}` and returns Task 4 result. Return 201 for newly installed and 200 for idempotent repeat.
- TS: `searchSkills(query: string, signal?: AbortSignal): Promise<SkillSearchResult>` and `installSkill(candidate: Pick<RepositorySkill, "repo_url" | "commit" | "skill_path">): Promise<SkillInstallResult>`.

- [ ] **Step 1: Write API tests for valid URL/name searches, exact install parameters, malformed input 422, collision 409, upstream 502, timeout 504 and partial unavailable rows.** Reuse the console error envelope and synthetic factories. Assert no unexpected HEAD lookup or public source write occurs during install.
- [ ] **Step 2: Run `python -m pytest -q tests/test_console_skill_installation_api.py` and `npm --prefix frontend test -- --run src/api/skills.test.ts`, separately.** Expected RED for absent route/functions.
- [ ] **Step 3: Register the narrow route module and implement matching TypeScript types/functions.** Reuse the existing `request` transport; do not put provider-specific branching into the large Settings component or add a second credential store. Backend exceptions map to existing envelopes with no credential-bearing command output.
- [ ] **Step 4: Rerun both files, then `python -m pytest -q tests/test_console_managed_skills_api.py`.** Expected PASS and unchanged existing create/edit/config/export contracts.
- [ ] **Step 5: Commit API adapter/client and tests.** Message: `feat: expose Skill search and installation APIs`.

### Task 6: Add the approved interaction to the existing Skills page

**Files:**
- Create: `frontend/src/components/settings/SkillSearchPanel.tsx` and `.test.tsx`.
- Modify: `frontend/src/components/settings/ManagedSkillsPanel.tsx`.
- Modify: `frontend/src/pages/SettingsPage.test.tsx` (mock extensions and narrow integration case only).
- Modify: `frontend/src/styles.css` (existing Settings styles around `.skills-project-row` and `.managed-skill-grid`; reuse before adding styles).

**Interfaces:**
- `SkillSearchPanel({onInstalled}: {onInstalled: (result: SkillInstallResult) => void}): ReactElement` (`ReactElement` imported as a React type).
- On successful install, parent reloads existing managed Skills/revisions/config; expose an installed list with no feature association required. Existing feature tile selection still filters its own associated list.
- Form label `搜索 Skills`, placeholder `输入 repo URL 或公开 Skill 名称`, submit `搜索`; row action `Install`, in-flight `Installing…`, completed `已安装`, pending suffix `待加载`. Source author fallback is labelled `维护者`, missing time `更新时间未知`.

- [ ] **Step 1: Add UI tests for URL and public-name submissions, metadata rendering, keyboard Enter, empty results and private-auth errors.** Assert no search until submit and each Install call contains that row's exact repo/commit/path. Use Testing Library and injected/mocked API responses.
- [ ] **Step 2: Run `npm --prefix frontend test -- --run src/components/settings/SkillSearchPanel.test.tsx`.** Expected RED for missing component.
- [ ] **Step 3: Implement the form and result rows using existing Settings components/styles.** Keep the form available during an empty installed list; disable only the selected in-flight Install row and cancel/ignore stale searches. Do not add a new route, wizard, marketplace review screen or token entry field.
- [ ] **Step 4: Add tests `late_search_does_not_replace_newer_results`, `duplicate_click_installs_once`, `installed_unassigned_skill_remains_visible`, and `pending_is_not_active`.** Integrate the component with ManagedSkillsPanel, preserve existing feature controls and expose all installed Skills. Verify an imported optional Skill appears in existing scheduled task selection via the current managed options service; if not, repair only that discovery mismatch and add a focused `tests/test_agent_cron_options.py` case, without enabling a task.
- [ ] **Step 5: Run the new component file, `src/pages/SettingsPage.test.tsx`, and `src/api/skills.test.ts` separately; run `npm --prefix frontend run build`.** Expected PASS. Existing skipped tests are not acceptance; write live tests for the new flow.
- [ ] **Step 6: Update the user-facing installation runbook and commit.** Message: `feat: search and install Skills from existing Settings page`.

### Task 7: Verify first use, document and prepare standard release

**Files:**
- Modify: `README.md`, `docs/agent-installation-runbook.md`, `docs/architecture.md`, `docs/runtime-mechanism.md`.
- Modify: `tests/gate_files.txt` for fast new offline regression files only.
- Create: `docs/skill-search-install-validation.md`.

**Interfaces:**
- Consumes all preceding behavior and existing `python -m app.deploy` release process; no separate installer daemon, schema release or runtime route.
- Produces explicit evidence for search, complete package installation, pending/active readback and initial public startup. Private-repo migration/history treatment remain separately pending.

- [ ] **Step 1: Run one synthetic first-use integration scenario.** Start from a fresh test-only home/Skill root/database; open Settings, search a synthetic repository through the injected reader, install exactly one of two Skills, verify support files and managed/task selection, then perform a normal test process startup and verify matching revision SHA/load receipt. Move source HEAD and confirm the installed package does not change. Do not repurpose the real HOME or use live business providers.
- [ ] **Step 2: Run targeted Python files from Tasks 1–5, one process per file, then the named frontend files/build.** Run Ruff on changed Python and `git diff --check`; add fast deterministic files to gate manifest. Expected PASS. Do not rerun broad tests absent a new issue.
- [ ] **Step 3: Correct installation documentation.** README currently says bootstrap installs business Skills while the shell only verifies them; write the actual post-change flow in the same behavior commits. Document service-account Git authentication, default-branch commit pinning, name-index source, metadata meaning, conflict handling and installed-vs-loaded status. No nonexistent command examples.
- [ ] **Step 4: Review public payload before any publication.** This plan's public source/tests/docs contain only generic interfaces and synthetic repos; preserve and exclude the unpushed company OA branch/results. Do not interpret earlier OA implementation authorization as public disclosure consent. Follow any actual platform review restriction rather than changing tools to bypass it.
- [ ] **Step 5: Commit validation docs, create/attach PR when warranted, and use the established standard deploy once implementation/review/publication are authorized.** Record tested/installed exact commit. Mandatory deploy gate, quiet wait, backups and rollback remain unchanged. Do not invoke restart/kill directly from a web install handler.
- [ ] **Step 6: Read back PID, healthz, queues, Attention, History and installed/runtime Skill hashes/load receipts.** Verify the Settings UI in a real browser, including empty-install search, row metadata and installed visibility; a fixture browser or HTTP 200 alone is insufficient. No completed OA replay or real message send is needed for this feature.
- [ ] **Step 7: Record remaining migration scope and remove only proven task-owned temporary clones.** Final report distinguishes implemented/tested/UI-verified/deployed and company-repo migration. Commit docs: `docs: document repository Skill installation and verification`.

## Self-review (plan, not implementation acceptance)

- User's annotated existing-page search, name/URL input, description/author/time and per-row Install map to Tasks 2/3/5/6.
- First public startup and existing managed integration map to Tasks 1/4; public CI uses synthetic content.
- All five Review Focus classes have named owning tests; exact commit and byte identity flow through the same RepositorySkill/SkillPackage/SkillInstallResult names.
- No new database fields, Audit/auth policy, route-specific behavior, auto-update scheduler or private payload publication is assumed.
- No live tests, implementation, private repository creation, push or deployment have been performed as part of writing this plan.
