# Feedback Processing API and Workbench Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a local feedback-processing API, a repository Skill for evidence-backed Agent service improvement, and a minimal React Workbench right drawer that atomically imports selected user feedback into an Agent conversation.

**Architecture:** Keep `feedback_events` as immutable user-feedback facts and add separate SQLite processing-batch/item records. Extend the existing `/api/console/feedback` resource and envelope rather than creating a second API namespace. React adds only a `FeedbackDrawer` and `TaskList` callback; the repository Skill owns diagnosis, implementation, tests, commit, restart, readback, and evidence writeback.

**Tech Stack:** Python 3, FastAPI, SQLite, Pydantic, React 19, TypeScript, React Router, Vitest, Testing Library, Vite, launchd, pytest.

---

## Implementation constraints

- Work in a dedicated implementation worktree branched from the commit containing the approved spec (`afa08f1`).
- Do not modify or delete user-owned uncommitted files. The current repository has unrelated untracked `.superpowers/` and `docs/ui-audit-2026-08-29/` directories; leave them untouched.
- Do not restore `service_bugfix_candidates` routes or create a second feedback queue.
- Do not call a model while listing, claiming, or importing feedback. The startup message is deterministic formatting of persisted fields.
- Do not let any evidence-free endpoint or UI action mark feedback resolved.
- Preserve the existing local-only service boundary. No public listener, token, CORS policy, or remote authentication is added.

## File map

- Modify `app/store.py`: additive schema, processing models, atomic claim/evidence/resolve methods, and feedback projections.
- Create `app/feedback_processing.py`: typed processing payloads, summary/reference projection, deterministic startup-message builder, and evidence validation helpers.
- Modify `app/web_api/registration.py`: Console feedback list/detail/batch/evidence/resolve routes and compatibility behavior for direct resolve.
- Modify `frontend/src/api/console.ts`: extend `FeedbackItem` parsing only where the existing page needs the new fields.
- Create `frontend/src/api/feedback.ts`: typed batch/detail/evidence calls using the existing Console envelope and error semantics.
- Create `frontend/src/components/FeedbackDrawer.tsx`: responsive multi-select drawer using the existing Inspector drawer behavior.
- Modify `frontend/src/components/TaskList.tsx`: render the button immediately below `新任务` and expose the callback/count props.
- Modify `frontend/src/app.tsx`: own FeedbackDrawer state and orchestrate task reuse/creation, claim, turn creation, and batch-turn association.
- Modify `frontend/src/pages/FeedbackPage.tsx`: show processing state and remove evidence-free direct resolution.
- Create `skills/ceo-feedback-processing/SKILL.md`: repository-level Agent operating contract.
- Add backend tests in `tests/test_feedback_processing.py` and `tests/test_console_web_api.py` (or the existing nearest test module when a fixture is already local).
- Add frontend tests in `frontend/src/api/feedback.test.ts`, `frontend/src/components/FeedbackDrawer.test.tsx`, and existing `TaskList.test.tsx`/`app.test.tsx`.
- Update `CHANGELOG.md` and the approved spec only if implementation behavior requires a documented correction; do not rewrite the approved design silently.

### Task 1: Add processing models and additive SQLite schema

**Files:**
- Create: `app/feedback_processing.py`
- Modify: `app/store.py` near `_initialize()` and the existing feedback methods around `upsert_feedback_event()`/`list_user_feedback_items()`
- Test: `tests/test_feedback_processing.py`

- [ ] **Step 1: Write failing model/projection tests.**

  Add tests that seed a `feedback_events` row and assert the new projection exposes `feedback_key`, current status, persisted summary, and detail references without changing the original event. Add a migration test that opens a fresh `AutoReplyStore`, verifies both processing tables and indexes, and reopens the store to prove idempotency.

  The first test should assert this shape:

  ```python
  item = store.get_feedback_processing_item("feedback-1")
  assert item is not None
  assert item.status == "pending"
  assert item.feedback_key == "feedback-1"
  assert store.get_feedback_event("feedback-1").comment == "原始反馈"
  ```

- [ ] **Step 2: Run the focused tests and verify they fail.**

  Run:

  ```bash
  .venv/bin/python -m pytest tests/test_feedback_processing.py -q
  ```

  Expected: FAIL because the processing models, store tables, and methods do not yet exist.

- [ ] **Step 3: Implement the typed processing layer and schema.**

  In `app/feedback_processing.py`, define strict Pydantic models with these fields:

  ```python
  class FeedbackProcessingItem(BaseModel):
      feedback_key: str
      batch_id: str = ""
      status: Literal["pending", "processing", "resolved"] = "pending"
      workbench_task_id: str = ""
      workbench_turn_id: str = ""
      attempt_id: int = 0
      agent_run_id: int = 0
      commit_sha: str = ""
      test_evidence: dict[str, object] = Field(default_factory=dict)
      restart_evidence: dict[str, object] = Field(default_factory=dict)
      health_evidence: dict[str, object] = Field(default_factory=dict)
      note: str = ""
      resolved_at: str = ""
      created_at: str = ""
      updated_at: str = ""
  ```

  Add `feedback_processing_batches` and `feedback_processing_items` in the store initializer with unique `feedback_key`, indexed status/batch columns, and timestamps. Add idempotent store methods for creating a batch, claiming all keys in one transaction, associating a turn, reading a batch, patching item evidence, and resolving a complete batch. Keep all original `feedback_events` columns untouched.

- [ ] **Step 4: Run the focused tests and verify they pass.**

  Run the same pytest command. Expected: PASS, including the fresh-store and reopen/idempotency migration assertions.

- [ ] **Step 5: Commit the storage unit.**

  ```bash
  git add app/store.py app/feedback_processing.py tests/test_feedback_processing.py
  git commit -m "feat: persist feedback processing batches"
  ```

### Task 2: Implement deterministic feedback projections and evidence rules

**Files:**
- Modify: `app/feedback_processing.py`
- Modify: `app/store.py` feedback list/count projections
- Test: `tests/test_feedback_processing.py`

- [ ] **Step 1: Write failing projection, claim, conflict, and resolve tests.**

  Cover these cases explicitly:

  Use these concrete pytest names and assertions: `test_claim_is_atomic_when_one_key_is_already_processing` asserts a 409-style domain error and no partial rows; `test_repeat_claim_returns_same_batch_without_duplicate_items` asserts the original batch ID and one item per key; `test_resolve_rejects_missing_test_restart_or_health_evidence` asserts each missing evidence category is rejected; `test_resolve_requires_current_head_and_zero_test_exit_codes` asserts stale HEAD and non-zero test receipts are rejected; `test_resolve_marks_every_item_in_batch_together` asserts one transaction changes every item; and `test_processing_count_excludes_claimed_items_from_pending_count` asserts pending counts exclude processing rows.

  Seed a stored attempt with `audit_summary`/`codex_reason` and assert the summary precedence is deterministic. Assert that missing summary yields `""`, never a generated sentence. Seed an existing `resolved_at` event and assert migration does not re-claim it.

- [ ] **Step 2: Run the tests and verify they fail.**

  ```bash
  .venv/bin/python -m pytest tests/test_feedback_processing.py -q
  ```

  Expected: FAIL on missing claim/evidence methods and validation.

- [ ] **Step 3: Implement the rules.**

  Add four pure helpers in `app/feedback_processing.py`: `persisted_feedback_summary(item: UserFeedbackItem) -> str`, `detail_references(item: UserFeedbackItem) -> list[dict[str, str]]`, `build_feedback_start_message(batch_id: str, items: Sequence[FeedbackImportItem]) -> str`, and `validate_resolution_evidence(evidence: ResolutionEvidence, *, current_head: str) -> None`.

  The summary helper reads only persisted attempt/audit fields in a documented fixed order and returns an empty string if all are empty. The reference helper emits only routes backed by actual IDs (`/attempts/{id}`, `/attempts/{id}/execution/{role}`, `/codex/{session_id}`, `/tasks/{project_id}`) plus human-readable `task#`, `attempt#`, and `run#` labels. The message builder includes the batch ID, Skill path, keys, persisted summaries, and references; it never calls a model or copies the full feedback body.

  Make the claim transaction reject any non-pending key with `feedback_already_processing` and roll back all changes. Make evidence writes compare normalized JSON so retries are idempotent. Make resolve verify commit SHA format/current HEAD, every test exit code `0`, launchd label and before/after PID, successful local health evidence, and complete item associations before one transaction sets all items resolved.

- [ ] **Step 4: Run tests and commit.**

  ```bash
  .venv/bin/python -m pytest tests/test_feedback_processing.py -q
  git add app/store.py app/feedback_processing.py tests/test_feedback_processing.py
  git commit -m "feat: validate feedback completion evidence"
  ```

  Expected: all focused tests pass.

### Task 3: Extend the existing React Console feedback API

**Files:**
- Modify: `app/web_api/registration.py`
- Modify: `frontend/src/api/console.ts`
- Create: `frontend/src/api/feedback.ts`
- Test: `tests/test_console_web_api.py`, `frontend/src/api/feedback.test.ts`

- [ ] **Step 1: Add failing API contract tests.**

  In `tests/test_console_web_api.py`, seed two feedback events, create one batch, and assert:

  - `GET /api/console/feedback?status=pending` returns `items + meta`, summary, status, and real detail references;
  - `POST /api/console/feedback/batches` claims both keys atomically;
  - a second client attempting one claimed key gets `409` with `feedback_already_processing`;
  - `GET /api/console/feedback/batches/{id}` returns item state;
  - direct `POST /api/console/feedback/{id}/resolve` returns `409 feedback_batch_required`;
  - batch resolve rejects incomplete evidence and accepts the complete fixture.

  In `frontend/src/api/feedback.test.ts`, assert strict parsing of the batch/detail/evidence response envelopes and conversion of non-2xx payloads to `ConsoleApiError`.

- [ ] **Step 2: Run failing backend and frontend tests.**

  ```bash
  .venv/bin/python -m pytest tests/test_console_web_api.py -q
  npm test --prefix frontend -- --run src/api/feedback.test.ts
  ```

  Expected: FAIL because the new routes and client functions do not exist.

- [ ] **Step 3: Register the routes using the existing Console envelope.**

  Add strict request parsing in `app/web_api/registration.py` and register static `/batches` routes before the dynamic `{feedback_id}` route. The route contract is: `GET /api/console/feedback/{feedback_id}` returns one detail projection or 404; `POST /api/console/feedback/batches` atomically claims the requested keys and returns the deterministic startup payload; `GET /api/console/feedback/batches/{batch_id}` returns batch and item state; `PATCH /api/console/feedback/batches/{batch_id}` associates the Workbench task/turn; `PATCH /api/console/feedback/items/{feedback_id}` records evidence fields; and `POST /api/console/feedback/batches/{batch_id}/resolve` validates all evidence before resolving the batch.

  Extend the existing list route to use processing projection state and add `summary`, `references`, `batch_id`, and `processing_task_id` fields. Keep the current bounded sync action. Change the current direct resolve route to return `409 feedback_batch_required`; do the same for the legacy HTML resolve handler so no route bypasses the evidence contract.

  In `frontend/src/api/console.ts`, extend `FeedbackItem` with `summary`, `references`, `batch_id`, and `processing_task_id`. In `frontend/src/api/feedback.ts`, implement `listPendingFeedback`, `getFeedbackBatch`, `claimFeedbackBatch`, `associateFeedbackTurn`, `patchFeedbackItem`, and `resolveFeedbackBatch` on top of the shared request/error behavior.

- [ ] **Step 4: Run tests and commit the API unit.**

  ```bash
  .venv/bin/python -m pytest tests/test_console_web_api.py tests/test_feedback_processing.py -q
  npm test --prefix frontend -- --run src/api/feedback.test.ts
  git add app/web_api/registration.py frontend/src/api/console.ts frontend/src/api/feedback.ts tests/test_console_web_api.py frontend/src/api/feedback.test.ts
  git commit -m "feat: expose local feedback processing API"
  ```

### Task 4: Add the React FeedbackDrawer and task-list entry point

**Files:**
- Create: `frontend/src/components/FeedbackDrawer.tsx`
- Modify: `frontend/src/components/TaskList.tsx`
- Modify: `frontend/src/styles.css`
- Test: `frontend/src/components/FeedbackDrawer.test.tsx`, `frontend/src/components/TaskList.test.tsx`

- [ ] **Step 1: Write failing component tests.**

  Test that `TaskList` renders `处理反馈 · N` immediately after `新任务`, invokes `onProcessFeedback`, and preserves existing task actions. Test `FeedbackDrawer` with mocked API data for loading, empty, error, single-select, select-all, deselect, and disabled-import states. Assert each row displays the persisted summary and only API-provided reference links.

- [ ] **Step 2: Run the tests and verify failure.**

  ```bash
  npm test --prefix frontend -- --run src/components/FeedbackDrawer.test.tsx src/components/TaskList.test.tsx
  ```

  Expected: FAIL because the component and new props do not exist.

- [ ] **Step 3: Implement the smallest drawer.**

  Define props with explicit async callbacks:

  ```tsx
  export interface FeedbackDrawerProps {
    open: boolean;
    pending: FeedbackItem[];
    loading: boolean;
    error: string;
    selected: ReadonlySet<string>;
    submitting: boolean;
    onToggle: (feedbackKey: string) => void;
    onSelectAll: () => void;
    onImport: () => void | Promise<void>;
    onClose: () => void;
  }
  ```

  Reuse the existing Inspector drawer classes and focus/scrim conventions from `app.tsx`; add only feedback-specific classes where the existing layout cannot express the list. Use native checkbox controls, stable keys from `feedback_key`, and `aria-modal`, `aria-labelledby`, and focus return. Do not add coding/evidence/restart controls.

  Add `pendingFeedbackCount` and `onProcessFeedback` to `TaskListProps`; render the button directly below `onNewTask`'s button. Keep the task search/list behavior unchanged.

- [ ] **Step 4: Run tests, then commit the UI unit.**

  ```bash
  npm test --prefix frontend -- --run src/components/FeedbackDrawer.test.tsx src/components/TaskList.test.tsx
  git add frontend/src/components/FeedbackDrawer.tsx frontend/src/components/TaskList.tsx frontend/src/styles.css frontend/src/components/FeedbackDrawer.test.tsx frontend/src/components/TaskList.test.tsx
  git commit -m "feat: add workbench feedback drawer"
  ```

### Task 5: Orchestrate import in the existing Workbench

**Files:**
- Modify: `frontend/src/app.tsx`
- Modify: `frontend/src/api.ts` only if shared task/turn types need a non-breaking helper
- Test: `frontend/src/app.test.tsx`

- [ ] **Step 1: Write failing orchestration tests.**

  Add tests for:

  - opening the drawer from the button under `新任务`;
  - loading pending feedback from the Console API;
  - using the selected task when one is open;
  - creating `runtime_kind="codex"` with a feedback-oriented title when no task is open;
  - calling claim before turn creation;
  - creating exactly one deterministic startup turn with the returned batch ID and keys;
  - associating the turn after creation;
  - closing the drawer and selecting the task after success;
  - retaining `processing` and showing an error if turn creation fails.

  Assert the startup message contains persisted summaries and `task#`/`attempt#`/`run#` references, and assert it does not call any model/summarization function.

- [ ] **Step 2: Run the tests and verify failure.**

  ```bash
  npm test --prefix frontend -- --run src/app.test.tsx
  ```

  Expected: FAIL because App does not yet own feedback drawer state or import orchestration.

- [ ] **Step 3: Implement import orchestration.**

  Add state in `App` for drawer open/loading/error, pending rows, selected keys, and submit ownership. Implement `loadPendingFeedback()` with an `AbortController`. Implement `importFeedback()` in this order:

  ```tsx
  const task = selectedTask ?? await createTask("处理反馈", "codex", { signal });
  const batch = await claimFeedbackBatch(selectedKeys, task.id, { signal });
  const turn = await createTurn(task.id, batch.start_message, clientRequestId, { signal });
  await associateFeedbackTurn(batch.batch_id, turn.id, { signal });
  selectTask(task.id);
  closeFeedbackDrawer();
  ```

  Use the existing request cancellation/idempotency conventions and preserve the returned `clientRequestId` on retry. If turn creation or association fails, do not unclaim or resolve items; keep the batch processing and show a resumable error.

- [ ] **Step 4: Run the focused frontend suite and commit.**

  ```bash
  npm test --prefix frontend -- --run src/app.test.tsx src/components/FeedbackDrawer.test.tsx src/components/TaskList.test.tsx
  git add frontend/src/app.tsx frontend/src/app.test.tsx
  git commit -m "feat: import feedback into workbench conversations"
  ```

### Task 6: Update the React 用户反馈 page and compatibility behavior

**Files:**
- Modify: `frontend/src/pages/FeedbackPage.tsx`
- Modify: `frontend/src/components/status/StatusBadge.tsx` only if `processing` is not rendered correctly
- Modify: `app/audit_web.py` legacy resolve handler
- Test: `frontend/src/pages/FeedbackPage.test.tsx` (create if absent), `tests/test_audit_web.py`

- [ ] **Step 1: Write failing page/compatibility tests.**

  Assert the page renders `processing` rows, shows a link to the associated Workbench task/batch when present, and no longer renders the evidence-free `标记已处理` action. Assert the legacy HTML resolve post returns a conflict and leaves `resolved_at` empty.

- [ ] **Step 2: Implement read-only status rendering.**

  Keep search, status filter, sync, and expandable data-list behavior. Replace direct resolve state mutation with links to the attempt and processing task/batch. The page remains a read/read-sync surface; only the Agent Skill's batch resolve API can resolve.

- [ ] **Step 3: Run tests and commit.**

  ```bash
  npm test --prefix frontend -- --run src/pages/FeedbackPage.test.tsx
  .venv/bin/python -m pytest tests/test_audit_web.py -q
  git add frontend/src/pages/FeedbackPage.tsx frontend/src/components/status/StatusBadge.tsx app/audit_web.py frontend/src/pages/FeedbackPage.test.tsx tests/test_audit_web.py
  git commit -m "fix: require evidence for feedback resolution"
  ```

### Task 7: Add the repository-level feedback Skill

**Files:**
- Create: `skills/ceo-feedback-processing/SKILL.md`
- Test: `tests/test_feedback_skill_contract.py`

- [ ] **Step 1: Write the Skill contract test first.**

  Assert the file contains the local Console API base, the exact list/get/claim/evidence/resolve operations, repository/worktree preservation, regression-test requirement, commit, launchd restart, PID/health readback, and the rule that incomplete evidence leaves items processing. Assert it forbids direct SQLite writes and evidence-free resolve.

- [ ] **Step 2: Run the test and verify failure.**

  ```bash
  .venv/bin/python -m pytest tests/test_feedback_skill_contract.py -q
  ```

  Expected: FAIL because the Skill file does not exist.

- [ ] **Step 3: Write the Skill.**

  The Skill must instruct the Agent to call the local route family rooted at `http://127.0.0.1:8765/api/console/feedback/`, use `feedback_key` as the stable identity, read supplied attempt/run/task references, preserve user changes, reproduce before editing, add a regression test, commit, run the required tests, restart `com.ceo-agent-service.main`, verify a new PID/health/backlog, patch evidence, and call batch resolve only after every item is complete. It must direct the Agent to use brainstorming for design dialogue and explicitly state that import formatting itself never calls a model.

- [ ] **Step 4: Run the contract test and commit.**

  ```bash
  .venv/bin/python -m pytest tests/test_feedback_skill_contract.py -q
  git add skills/ceo-feedback-processing/SKILL.md tests/test_feedback_skill_contract.py
  git commit -m "docs: add feedback processing skill"
  ```

### Task 8: End-to-end regression, build, documentation, and live verification

**Files:**
- Modify: `tests/test_console_web_api.py` or create `tests/test_feedback_processing_e2e.py`
- Modify: `CHANGELOG.md`
- Build output: `app/static/workbench/` generated by Vite; do not hand-edit generated assets

- [ ] **Step 1: Add the attempt-8308 regression fixture.**

  Seed the exact shape of an unresolved feedback event associated with attempt `8308`, then assert all of the following in one integration test:

  ```python
  assert client.get("/api/console/feedback?status=pending").json()["items"]
  batch = client.post(
      "/api/console/feedback/batches",
      json={"feedback_keys": ["feedback-8308"], "workbench_task_id": "task-1"},
  ).json()
  assert "feedback_key" in batch["items"][0]
  assert "attempt#8308" in batch["start_message"]
  assert client.post(f"/api/console/feedback/batches/{batch['batch_id']}/resolve", json=incomplete).status_code == 409
  ```

  Complete the fixture with mocked current HEAD, test receipts, PID change, and health readback; assert the feedback page/API then reports `resolved` and the original event comment is unchanged.

- [ ] **Step 2: Run all focused backend and frontend tests.**

  ```bash
  .venv/bin/python -m pytest tests/test_feedback_processing.py tests/test_console_web_api.py tests/test_audit_web.py tests/test_feedback_processing_e2e.py -q
  npm test --prefix frontend -- --run
  ```

  Expected: all selected pytest tests and all Vitest tests pass.

- [ ] **Step 3: Build the React Workbench.**

  ```bash
  npm run build:workbench
  ```

  Expected: TypeScript check and Vite build pass, and `app/static/workbench/index.html` references the newly generated hashed assets.

- [ ] **Step 4: Update documentation and commit the integrated unit.**

  Add a concise CHANGELOG entry describing the local API, processing states, React drawer, and Skill; link the approved spec and plan. Do not claim production completion in the changelog.

  ```bash
  git add CHANGELOG.md tests/test_feedback_processing_e2e.py
  git commit -m "docs: document feedback processing workflow"
  ```

- [ ] **Step 5: Restart and verify the actual launchd service.**

  Because runtime code and generated Workbench assets changed, restart the service and verify a new process:

  ```bash
  launchctl kickstart -k gui/$(id -u)/com.ceo-agent-service.main
  launchctl print gui/$(id -u)/com.ceo-agent-service.main | sed -n '1,80p'
  curl -sS http://127.0.0.1:8765/api/console/feedback?status=pending
  ```

  Also read the live health/status endpoint, verify the Workbench root serves the built React index, and inspect queue state for no new failed or stuck processing work. Record the new PID, response status, and feedback item state in the handoff; do not claim resolved solely from a passing test command.

## Plan self-review

- **Spec coverage:** Tasks 1–2 cover separate processing tables, deterministic summaries/references, atomic claim, evidence validation, idempotency, and migration. Task 3 covers the local Console API and evidence-free compatibility rejection. Tasks 4–6 cover the React drawer, existing Inspector behavior, task/turn orchestration, current feedback page, and legacy route. Task 7 covers the repo Skill. Task 8 covers attempt `8308`, build, documentation, restart, and live readback.
- **Placeholder scan:** No unfinished markers or ellipsis placeholders remain. Every task has concrete files, tests, commands, and expected outcomes.
- **Type consistency:** The plan uses `feedback_key`, `batch_id`, `workbench_task_id`, `workbench_turn_id`, `commit_sha`, `test_evidence`, `restart_evidence`, and `health_evidence` consistently across storage, API, frontend, and Skill sections.
- **Scope:** The API, storage, React drawer, and Skill are coupled through the batch/turn contract and form one implementation plan; no independent product subsystem is hidden inside the plan.
