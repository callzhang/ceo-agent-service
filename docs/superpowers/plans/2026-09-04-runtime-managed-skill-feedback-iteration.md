# Runtime-Managed Skill Feedback Iteration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace direct Settings-to-directory Skill edits with immutable runtime-managed Skill revisions, and let feedback iterations choose and verify Skill, configuration, code, or mixed repairs.

**Architecture:** SQLite stores immutable Skill revisions, immutable next-start runtime configurations, and actual startup load receipts. The Consumer receives the exact active configuration at invocation start. Feedback iteration is a separately managed system capability; it persists a typed decision and validates resolution evidence according to that decision scope.

**Tech Stack:** Python 3, SQLite via `app.store.AutoReplyStore`, Pydantic, existing console web API, React/TypeScript/Vitest, Playwright browser acceptance, launchd local service.

---

## File structure and ownership

- Create: `app/managed_skills.py` — immutable revision/config/load-receipt models, validation, resolver and repository import/export adapter.
- Modify: `app/store.py` — additive schema, migrations, transactional CRUD for managed Skills/configurations/receipts and feedback decisions.
- Modify: `app/business_skills.py` — retain bundled compatibility helpers; make Consumer catalog construction accept managed resolved entries rather than discovering a mutable runtime directory.
- Modify: `app/consumer_agent.py` — capture one runtime Skill snapshot per invocation and render that exact catalog.
- Modify: `app/main.py` or the existing service startup composition root — initialize a pending configuration, perform its load, write receipt, and expose the active resolver to workers.
- Modify: `app/skill_features.py` and `data/config/skill-features.json` — keep producer-routing semantics explicit; register feedback iteration separately rather than as a business producer feature.
- Modify: `app/feedback_processing.py` and `app/store.py` — typed iteration decisions and discriminated resolution receipts.
- Modify: `app/web_api/registration.py` — console routes for managed Skills/configuration/receipts and feedback iteration capability/decisions.
- Modify: `frontend/src/api.ts`, `frontend/src/api/feedback.ts`, `frontend/src/pages/SettingsPage.tsx`, `frontend/src/pages/FeedbackPage.tsx`, and relevant CSS — Settings runtime controls and disabled feedback processing action.
- Create/modify: `skills/ceo-feedback-iteration/SKILL.md` — repository protocol for the bounded feedback-discussion profile; keep generic `brainstorming` unchanged.
- Tests: `tests/test_managed_skills.py`, `tests/test_managed_skill_runtime.py`, `tests/test_feedback_iteration.py`, `tests/test_console_managed_skills_api.py`, `tests/test_console_feedback_iteration_api.py`, `frontend/src/pages/SettingsPage.test.tsx`, `frontend/src/pages/FeedbackPage.test.tsx`, `frontend/src/api/feedback.test.ts`, and `tests/browser/test_console_acceptance.py`.

### Task 1: Add immutable managed-Skill persistence

**Files:**
- Create: `tests/test_managed_skills.py`
- Create: `app/managed_skills.py`
- Modify: `app/store.py`

- [ ] **Step 1: Write failing persistence and immutability tests**

```python
def test_create_revision_keeps_prior_body_and_hash(tmp_path: Path) -> None:
    store = AutoReplyStore(tmp_path / "skills.sqlite3")
    skill = store.create_managed_skill("ceo-test", "Test Skill")
    first = store.create_managed_skill_revision(skill.id, SKILL_V1, source="settings")
    second = store.create_managed_skill_revision(skill.id, SKILL_V2, source="settings")

    assert first.id != second.id
    assert store.get_managed_skill_revision(first.id).content == SKILL_V1
    assert store.get_managed_skill_revision(first.id).sha256 != second.sha256


def test_invalid_frontmatter_creates_no_revision(tmp_path: Path) -> None:
    store = AutoReplyStore(tmp_path / "skills.sqlite3")
    skill = store.create_managed_skill("ceo-test", "Test Skill")
    with pytest.raises(ManagedSkillValidationError, match="metadata.managed_by"):
        store.create_managed_skill_revision(skill.id, "---\nname: ceo-test\n---\n")
    assert store.list_managed_skill_revisions(skill.id) == ()
```

- [ ] **Step 2: Run the focused tests to establish the failing baseline**

Run: `./.venv/bin/python -m pytest tests/test_managed_skills.py -q`

Expected: FAIL because the managed-Skill store API does not exist.

- [ ] **Step 3: Add additive schema and immutable record types**

Create `app/managed_skills.py` with immutable domain models and validation:

```python
@dataclass(frozen=True)
class ManagedSkillRevision:
    id: int
    skill_id: int
    revision_number: int
    content: str
    sha256: str
    parent_revision_id: int | None
    source: str
    created_at: str


def validate_managed_skill_content(name: str, content: str) -> str:
    frontmatter = _parse_frontmatter(content, Path(f"managed:{name}/SKILL.md"))
    if _required_scalar(frontmatter, "name", Path(name)) != name:
        raise ManagedSkillValidationError("Skill name does not match managed Skill")
    if frontmatter.get("metadata", {}).get("managed_by") != MANAGED_BY:
        raise ManagedSkillValidationError("Skill missing managed marker")
    return hashlib.sha256(content.encode("utf-8")).hexdigest()
```

In `AutoReplyStore._initialize`, create `managed_skills` and
`managed_skill_revisions`; add unique `(skill_id, revision_number)` and
`(skill_id, sha256)` indexes. Implement `create_managed_skill`,
`create_managed_skill_revision`, `get_managed_skill_revision`, and list
methods in one immediate transaction. Do not add update/delete methods for a
revision.

- [ ] **Step 4: Run persistence tests and existing Skill validation regressions**

Run: `./.venv/bin/python -m pytest tests/test_managed_skills.py tests/test_skill_files.py tests/test_business_skills.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the isolated persistence slice**

```bash
git add app/managed_skills.py app/store.py tests/test_managed_skills.py
git commit -m "feat: persist immutable managed skill revisions"
```

### Task 2: Version runtime configurations and startup load receipts

**Files:**
- Modify: `tests/test_managed_skills.py`
- Create: `tests/test_managed_skill_runtime.py`
- Modify: `app/managed_skills.py`
- Modify: `app/store.py`
- Modify: the service startup composition root identified by `rg -n "AutoReplyStore\(" app`

- [ ] **Step 1: Write tests for next-start activation, receipt, and rollback**

```python
def test_pending_config_becomes_active_only_after_matching_load_receipt(tmp_path: Path) -> None:
    store, revision = configured_store(tmp_path)
    pending = store.create_runtime_skill_config({revision.skill_id: revision.id})
    assert pending.status == "pending_restart"

    receipt = store.record_runtime_skill_load(pending.id, pid=7654, loaded={revision.skill_id: revision.sha256})
    assert receipt.config_id == pending.id
    assert store.get_active_runtime_skill_config().id == pending.id


def test_failed_load_keeps_previous_active_config(tmp_path: Path) -> None:
    store, active = active_configured_store(tmp_path)
    broken = store.create_runtime_skill_config({active.skill_id: active.id + 999})
    store.record_runtime_skill_load_failure(broken.id, "revision missing")
    assert store.get_active_runtime_skill_config().id != broken.id
    assert store.get_runtime_skill_config(broken.id).status == "load_failed"
```

- [ ] **Step 2: Run the new runtime tests and confirm failure**

Run: `./.venv/bin/python -m pytest tests/test_managed_skill_runtime.py -q`

Expected: FAIL because runtime configurations and receipts are absent.

- [ ] **Step 3: Implement config/binding/receipt transactions and resolver**

Add `runtime_skill_configs`, `runtime_skill_bindings`, and
`runtime_skill_load_receipts`. `create_runtime_skill_config(expected_parent_id,
bindings)` creates a new immutable config with `pending_restart`. Implement:

```python
def resolve_pending_runtime_skills(store: AutoReplyStore, *, pid: int) -> RuntimeSkillSnapshot:
    config = store.get_pending_or_active_runtime_skill_config()
    revisions = tuple(store.get_managed_skill_revision(binding.revision_id)
                      for binding in store.list_runtime_skill_bindings(config.id)
                      if binding.enabled)
    store.record_runtime_skill_load(config.id, pid=pid,
        loaded={revision.skill_id: revision.sha256 for revision in revisions})
    return RuntimeSkillSnapshot(config_id=config.id, revisions=revisions)
```

Startup must record a failure when validation/loading fails and retain the last
active config. Inject the returned immutable `RuntimeSkillSnapshot` into worker
and Consumer construction; do not reread Settings during an invocation.

- [ ] **Step 4: Run runtime and Consumer regressions**

Run: `./.venv/bin/python -m pytest tests/test_managed_skill_runtime.py tests/test_consumer_agent.py tests/test_agent_skill_usage.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the runtime configuration slice**

```bash
git add app/managed_skills.py app/store.py app/consumer_agent.py app/business_skills.py app/main.py tests/test_managed_skills.py tests/test_managed_skill_runtime.py
git commit -m "feat: load versioned skills from runtime configuration"
```

### Task 3: Import existing managed Skills and retire direct Settings file writes

**Files:**
- Modify: `app/managed_skills.py`
- Modify: `app/skill_files.py`
- Modify: `app/web_api/registration.py`
- Modify: `tests/test_managed_skills.py`
- Modify: `tests/test_console_skills_api.py`

- [ ] **Step 1: Add failing migration and API boundary tests**

```python
def test_initial_import_creates_revisions_for_every_service_owned_skill(tmp_path: Path) -> None:
    store = AutoReplyStore(tmp_path / "import.sqlite3")
    imported = import_repository_managed_skills(store, PROJECT_SKILLS_ROOT)
    assert {entry.name for entry in imported} >= set(BUNDLED_BUSINESS_SKILL_NAMES)
    assert all(entry.revision_number == 1 for entry in imported)


def test_settings_save_creates_revision_and_never_calls_runtime_directory_sync(client):
    response = client.post("/api/console/settings/managed-skills/ceo-test/revisions", json={"content": SKILL_V2})
    assert response.status_code == 201
    assert response.json()["revision_number"] == 2
```

- [ ] **Step 2: Run migration/API tests and confirm failure**

Run: `./.venv/bin/python -m pytest tests/test_managed_skills.py tests/test_console_skills_api.py -q`

Expected: FAIL because the managed-Skill routes and import adapter do not exist.

- [ ] **Step 3: Implement one-time idempotent import and replacement endpoints**

Import the seven repository-owned Skills into managed revisions at schema
initialization only when no managed record has that name/SHA. Add console routes
for list/create Skill, list/create revision, create next-start configuration,
read active/pending config, and read load receipts. Remove `PUT` access from the
old direct file editor route; return a migration response pointing the UI to
managed revisions. Do not call `SkillFileService.save_skill` or
`sync_bundled_skill` from Settings routes.

- [ ] **Step 4: Run API plus legacy Skill tests**

Run: `./.venv/bin/python -m pytest tests/test_managed_skills.py tests/test_console_skills_api.py tests/test_skill_files.py tests/test_business_skills.py -q`

Expected: PASS.

- [ ] **Step 5: Commit managed API migration**

```bash
git add app/managed_skills.py app/skill_files.py app/web_api/registration.py tests/test_managed_skills.py tests/test_console_skills_api.py
git commit -m "feat: manage skill revisions through runtime settings"
```

### Task 4: Rework Settings Skills UI around revision/configuration state

**Files:**
- Modify: `frontend/src/api.ts`
- Create or modify: `frontend/src/api/skills.ts`
- Modify: `frontend/src/pages/SettingsPage.tsx`
- Modify: `frontend/src/pages/SettingsPage.test.tsx`
- Modify: relevant `frontend/src/*.css`

- [ ] **Step 1: Write Settings tests for revision selection and terminology**

```tsx
it("saves a candidate revision and activates it for the next start", async () => {
  render(<SettingsPage />);
  await user.click(await screen.findByRole("button", { name: "新建修订" }));
  await user.type(screen.getByLabelText("Skill 正文"), skillV2);
  await user.click(screen.getByRole("button", { name: "保存修订" }));
  await user.click(await screen.findByRole("button", { name: "下次启动启用 revision 2" }));
  expect(await screen.findByText("配置已更新，等待服务重启")).toBeInTheDocument();
});

it("labels a business switch as new-task routing rather than Skill loading", async () => {
  render(<SettingsPage />);
  expect(await screen.findByText("仅控制新任务创建，不控制 Skill 加载")).toBeInTheDocument();
});
```

- [ ] **Step 2: Run the Settings tests and confirm failure**

Run: `pnpm test -- SettingsPage.test.tsx`

Expected: FAIL because the old editor exposes direct file save/sync semantics.

- [ ] **Step 3: Implement managed Skill cards and config controls**

Replace the source-path/editor status with revision history, SHA, parent
revision, active/pending configuration status, last startup receipt and action
buttons. Preserve Markdown preview. Place business routing switches under an
explicit label: `新任务创建开关（不控制 Skill 加载）`. Add a `系统能力` group for
feedback iteration; do not present it as a task producer.

- [ ] **Step 4: Run frontend unit tests and production build**

Run: `pnpm test -- SettingsPage.test.tsx && pnpm build`

Expected: PASS.

- [ ] **Step 5: Commit the Settings UI slice**

```bash
git add frontend/src/api.ts frontend/src/api/skills.ts frontend/src/pages/SettingsPage.tsx frontend/src/pages/SettingsPage.test.tsx frontend/src
git commit -m "feat: configure runtime managed skills in settings"
```

### Task 5: Add feedback-iteration capability and deterministic decision records

**Files:**
- Create: `skills/ceo-feedback-iteration/SKILL.md`
- Create: `tests/test_feedback_iteration.py`
- Modify: `app/feedback_processing.py`
- Modify: `app/store.py`
- Modify: `app/web_api/registration.py`
- Modify: `tests/test_feedback_processing.py`
- Create: `tests/test_console_feedback_iteration_api.py`

- [ ] **Step 1: Write failing capability and decision tests**

```python
def test_disabled_feedback_iteration_rejects_batch_claim(store: AutoReplyStore) -> None:
    store.set_feedback_iteration_enabled(False)
    with pytest.raises(FeedbackIterationDisabledError):
        store.claim_feedback_processing_items("batch-1", ["manual:8308"])


def test_decision_context_uses_existing_summary_and_reference_only(store: AutoReplyStore) -> None:
    message = build_feedback_start_message("batch-1", [fixture_item])
    assert "persisted summary:" in message
    assert "attempt#8308" in message
    assert "generated summary" not in message


def test_skill_only_decision_requires_skill_load_receipt_not_commit() -> None:
    decision = FeedbackIterationDecision(scope="skill_only", ...)
    assert validate_resolution_receipt(decision, skill_only_receipt()) is None
```

- [ ] **Step 2: Run feedback-iteration tests and confirm failure**

Run: `./.venv/bin/python -m pytest tests/test_feedback_iteration.py tests/test_feedback_processing.py -q`

Expected: FAIL because capability state, decisions, and discriminated receipts do not exist.

- [ ] **Step 3: Implement capability state, decisions, and protocol Skill**

Create `feedback_iteration_capability` state in the managed runtime config.
Create append-only `feedback_iteration_decisions` linked to batch/round/feedback
keys. Use a strict Pydantic model with scope values exactly `skill_only`,
`runtime_config`, `code`, `mixed`, `needs_human`; reject missing references or
acceptance. Add `ceo-feedback-iteration/SKILL.md` that requires factual context
inspection, bounded brainstorm discussion only for material uncertainty, typed
decision persistence, and path-specific verification. Do not modify generic
`brainstorming`.

- [ ] **Step 4: Run feedback store/API/Skill tests**

Run: `./.venv/bin/python -m pytest tests/test_feedback_iteration.py tests/test_feedback_processing.py tests/test_console_feedback_iteration_api.py tests/test_feedback_skill_contract.py -q`

Expected: PASS.

- [ ] **Step 5: Commit feedback decision slice**

```bash
git add app/feedback_processing.py app/store.py app/web_api/registration.py skills/ceo-feedback-iteration/SKILL.md tests/test_feedback_iteration.py tests/test_feedback_processing.py tests/test_console_feedback_iteration_api.py
git commit -m "feat: classify feedback iteration decisions"
```

### Task 6: Make feedback resolution evidence scope-aware

**Files:**
- Modify: `app/feedback_processing.py`
- Modify: `app/store.py`
- Modify: `tests/test_feedback_processing.py`
- Modify: `tests/test_feedback_iteration.py`

- [ ] **Step 1: Add failing evidence matrix tests**

```python
@pytest.mark.parametrize("scope,receipt,error", [
    ("skill_only", valid_skill_receipt(), None),
    ("skill_only", valid_skill_receipt(load_receipt=None), "load receipt"),
    ("runtime_config", valid_config_receipt(), None),
    ("code", valid_code_receipt(commit_sha=""), "commit"),
    ("mixed", valid_mixed_receipt(config_receipt=None), "runtime configuration"),
])
def test_resolution_receipt_matches_decision_scope(scope, receipt, error):
    if error is None:
        validate_resolution_receipt(FeedbackIterationDecision(scope=scope, ...), receipt)
    else:
        with pytest.raises(ValueError, match=error):
            validate_resolution_receipt(FeedbackIterationDecision(scope=scope, ...), receipt)
```

- [ ] **Step 2: Run evidence tests and confirm failure**

Run: `./.venv/bin/python -m pytest tests/test_feedback_iteration.py::test_resolution_receipt_matches_decision_scope -q`

Expected: FAIL because `ResolutionEvidence` universally requires a commit.

- [ ] **Step 3: Implement discriminated receipt validation**

Define `SkillOnlyResolutionEvidence`, `RuntimeConfigResolutionEvidence`,
`CodeResolutionEvidence`, and `MixedResolutionEvidence`; share successful
scenario/test, restart, health, and zero-backlog checks. Require commit SHA
and local-main ancestry only for `code` and `mixed`. Require active config ID,
revision/SHA, and matching successful load receipt for `skill_only`; require
previous/target config and successful target receipt for `runtime_config`.
Reject `needs_human` in resolve before mutating batch state.

- [ ] **Step 4: Run full feedback regression suite**

Run: `./.venv/bin/python -m pytest tests/test_feedback_processing.py tests/test_feedback_iteration.py tests/test_feedback_processing_e2e.py -q`

Expected: PASS.

- [ ] **Step 5: Commit scope-aware resolution**

```bash
git add app/feedback_processing.py app/store.py tests/test_feedback_processing.py tests/test_feedback_iteration.py
git commit -m "feat: verify feedback evidence by iteration scope"
```

### Task 7: Implement Feedback page disabled behavior, decision history, and browser acceptance

**Files:**
- Modify: `frontend/src/api/feedback.ts`
- Modify: `frontend/src/api/feedback.test.ts`
- Modify: `frontend/src/pages/FeedbackPage.tsx`
- Modify: `frontend/src/pages/FeedbackPage.test.tsx`
- Modify: relevant `frontend/src/*.css`
- Modify: `tests/browser/test_console_acceptance.py`

- [ ] **Step 1: Write UI/API tests for the disabled control and decision history**

```tsx
it("keeps 处理反馈 visible but disabled when feedback iteration is off", async () => {
  mockFeedbackCapability({ enabled: false });
  render(<FeedbackPage />);
  const button = await screen.findByRole("button", { name: "处理反馈" });
  expect(button).toBeDisabled();
  expect(screen.getByText("反馈迭代已关闭；启用后可处理未解决反馈")).toBeInTheDocument();
});

it("renders a persisted skill-only decision and its active revision receipt", async () => {
  mockFeedbackDetail({ decisions: [skillOnlyDecision] });
  render(<FeedbackPage />);
  await user.click(await screen.findByRole("button", { name: "展开详情" }));
  expect(await screen.findByText("skill_only")).toBeInTheDocument();
  expect(screen.getByText("revision #13")).toBeInTheDocument();
});
```

- [ ] **Step 2: Run UI tests and confirm failure**

Run: `pnpm test -- FeedbackPage.test.tsx feedback.test.ts`

Expected: FAIL because the page has no capability read, disabled processing control, or decision projection.

- [ ] **Step 3: Implement API client and React projections**

Fetch capability state with feedback list/detail. Render `处理反馈` for open
items; when capability is false set the native `disabled` attribute and do not
attach a click handler that calls claim. In detail/history render decision
scope, root cause, source routes, selected revision/config and receipt summary.
Continue to render existing attempt/run/batch links unchanged.

- [ ] **Step 4: Add and run browser acceptance**

Add one Playwright scenario that creates a local candidate Skill revision,
selects it for next start, verifies the pending status, then disables feedback
iteration and verifies `处理反馈` is visibly disabled without network mutation.

Run: `pnpm test -- FeedbackPage.test.tsx feedback.test.ts && ./.venv/bin/python -m pytest tests/browser/test_console_acceptance.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the final UI and browser slice**

```bash
git add frontend/src/api/feedback.ts frontend/src/api/feedback.test.ts frontend/src/pages/FeedbackPage.tsx frontend/src/pages/FeedbackPage.test.tsx frontend/src tests/browser/test_console_acceptance.py
git commit -m "feat: expose feedback iteration state and history"
```

### Task 8: Perform migration, release verification, and documentation reconciliation

**Files:**
- Modify: `docs/architecture.md`
- Modify: `docs/runtime-mechanism.md`
- Modify: `docs/superpowers/specs/2026-09-04-runtime-managed-skill-feedback-iteration-design.md` only if implementation discoveries require an approved spec correction
- Modify: focused test fixtures only when their expected API contract changes

- [ ] **Step 1: Write a migration smoke test against an existing-schema fixture**

```python
def test_managed_skill_migration_is_additive_for_existing_feedback_database(tmp_path: Path) -> None:
    db_path = copy_existing_feedback_fixture(tmp_path)
    store = AutoReplyStore(db_path)
    assert store.list_managed_skills()
    assert store.get_active_runtime_skill_config() is not None
    assert store.get_feedback_processing_item("manual:8308") is not None
```

- [ ] **Step 2: Run migration and complete focused regression suites**

Run: `./.venv/bin/python -m pytest tests/test_managed_skills.py tests/test_managed_skill_runtime.py tests/test_console_skills_api.py tests/test_console_feedback_iteration_api.py tests/test_feedback_processing.py tests/test_feedback_iteration.py tests/test_consumer_agent.py -q`

Expected: PASS.

- [ ] **Step 3: Rebuild the React assets and run the browser suite**

Run: `pnpm build && ./.venv/bin/python -m pytest tests/browser/test_console_acceptance.py -q`

Expected: PASS.

- [ ] **Step 4: Restart and read back the local service**

Run:

```bash
launchctl kickstart -k gui/$(id -u)/com.ceo-agent-service.main
launchctl print gui/$(id -u)/com.ceo-agent-service.main | sed -n '1,80p'
curl -sS http://127.0.0.1:8765/healthz
```

Expected: a new process ID, HTTP 200, `ok=true`, and a load receipt matching the selected active runtime config.

- [ ] **Step 5: Read back operational invariants and update runtime documentation**

Query the local feedback API/store and verify `processing=0`, `failed=0`, and
`retryable=0`; verify no processing feedback remains after a disabled
capability transition. Update architecture/runtime documentation with managed
revision/config/load-receipt semantics and path-specific feedback resolution.

- [ ] **Step 6: Commit release documentation and migration evidence**

```bash
git add docs/architecture.md docs/runtime-mechanism.md tests
git commit -m "docs: describe managed skill runtime lifecycle"
```

## Plan self-review

- Spec coverage: Tasks 1–3 cover revision/config/load/import and direct-write retirement; Task 4 covers Settings semantics; Tasks 5–7 cover feedback capability, brainstorm profile, decisions, receipts, disabled UI, API and browser flows; Task 8 covers migration, restart, health and backlog verification.
- No placeholders: every task supplies exact paths, an executable test command, expected result, and a concrete code/API behavior. The composition-root filename must be resolved before Task 2 because this repository's startup wiring may move; no behavior is left unspecified.
- Type consistency: `ManagedSkillRevision`, `RuntimeSkillSnapshot`, `FeedbackIterationDecision`, and the five fixed scope values are used consistently. `pending_restart`, `active`, and `load_failed` are the only configuration states introduced by this plan.
