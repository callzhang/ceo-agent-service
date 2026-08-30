# Feedback Reopen Processing Rounds Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a legal, versioned `resolved -> pending` feedback reopen flow that preserves every prior receipt and reuses the existing claim, Workbench, and batch resolution workflow.

**Architecture:** `feedback_processing_items` stays the current projection, while new round rows own immutable per-processing receipts and append-only transition rows own status history. Reopen clears only the current projection, claim creates a new round and batch, and resolve validates only the current round before atomically resolving the batch. The existing React Feedback page gains a reason dialog and compact history; the Agent UI remains unchanged.

**Tech Stack:** Python 3.12, Pydantic, SQLite, FastAPI, pytest, React, TypeScript, Vitest, pnpm.

---

## File map

- Modify `app/feedback_processing.py`: strict round/transition models, projection payloads, and receipt validation contract.
- Modify `app/store.py`: additive schema/backfill plus atomic round, transition, reopen, claim, patch, read, and resolve persistence.
- Modify `app/web_api/registration.py`: local reopen route and round-aware item/batch payloads.
- Modify `skills/ceo-feedback-processing/SKILL.md`: exact reopen and current-round rules.
- Modify `frontend/src/api/console.ts`: typed feedback detail/reopen client functions.
- Modify `frontend/src/pages/FeedbackPage.tsx`: reopen dialog, success/error feedback, and history rendering.
- Modify `frontend/src/pages/FeedbackPage.test.tsx`: React behavior coverage.
- Modify `frontend/src/styles.css`: only the dialog/history/loading styles required by this page.
- Modify `tests/test_feedback_processing.py`: schema, backfill, and Store state-machine tests.
- Modify `tests/test_feedback_processing_e2e.py`: local API round/reopen/second-resolution E2E.
- Modify `tests/test_feedback_skill_contract.py`: repository Skill contract.
- Add `tests/test_feedback_processing_api.py` only if the full audit app remains blocked by the independently moving email branch; it must register the real console routes on a minimal FastAPI app and must not replace the full E2E.
- Modify `CHANGELOG.md`: user-visible feedback reopen capability and evidence isolation.

### Task 1: Add strict processing-round persistence and idempotent backfill

**Files:**
- Modify: `app/feedback_processing.py`
- Modify: `app/store.py`
- Test: `tests/test_feedback_processing.py`

- [ ] **Step 1: Write failing schema/model tests**

Add tests that initialize a fresh database and assert:

```python
def test_feedback_round_schema_is_additive_and_idempotent(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "rounds.sqlite3")
    with store._connect() as db:
        tables = {row[0] for row in db.execute("select name from sqlite_master where type='table'")}
        columns = {row[1] for row in db.execute("pragma table_info(feedback_processing_items)")}
        assert {"feedback_processing_rounds", "feedback_processing_transitions"} <= tables
        assert "current_round_id" in columns
    AutoReplyStore(tmp_path / "rounds.sqlite3")
    with store._connect() as db:
        assert db.execute("select count(*) from feedback_processing_rounds").fetchone()[0] == 0
```

Add a legacy fixture with one pending, one processing, and one resolved item.
Assert processing/resolved rows backfill exactly one round with verbatim
associations/evidence/timestamps, pending receives no round, repeated
initialization adds nothing, and no source feedback text changes.

- [ ] **Step 2: Run the new tests and verify RED**

Run:

```bash
/Users/derek/miniforge3/bin/python -m pytest tests/test_feedback_processing.py -k 'round_schema or round_backfill' -q
```

Expected: failures because the round/transition tables, current pointer, and
models do not exist.

- [ ] **Step 3: Add strict models**

In `app/feedback_processing.py`, add strict models with no extra fields:

```python
class FeedbackProcessingRound(_StrictProcessingModel):
    id: int
    feedback_key: str
    round_number: int
    batch_id: str
    status: Literal["processing", "resolved"]
    workbench_task_id: str = ""
    workbench_turn_id: str = ""
    attempt_id: int = 0
    agent_run_id: int = 0
    commit_sha: str = ""
    test_evidence: dict[str, object] = Field(default_factory=dict)
    restart_evidence: dict[str, object] = Field(default_factory=dict)
    health_evidence: dict[str, object] = Field(default_factory=dict)
    note: str = ""
    started_at: str = ""
    resolved_at: str = ""
    reopened_at: str = ""
    reopen_reason: str = ""
    created_at: str = ""
    updated_at: str = ""

class FeedbackProcessingTransition(_StrictProcessingModel):
    id: int
    feedback_key: str
    round_id: int = 0
    batch_id: str = ""
    from_status: Literal["", "pending", "processing", "resolved"]
    to_status: Literal["pending", "processing", "resolved"]
    reason: str = ""
    workbench_task_id: str = ""
    workbench_turn_id: str = ""
    created_at: str = ""
```

Add `current_round_id: int = 0` to `FeedbackProcessingItem`.

- [ ] **Step 4: Add additive schema and backfill**

Create the two tables and indexes in the Store initialization script. Add
`current_round_id` through the established idempotent missing-column migration
path. Backfill with `insert or ignore` and set current pointers from the unique
round rows. Do not create a round for never-claimed pending items and do not
invent transitions absent in legacy data.

- [ ] **Step 5: Run tests and verify GREEN**

Run:

```bash
/Users/derek/miniforge3/bin/python -m pytest tests/test_feedback_processing.py -q
```

Expected: all existing and new Store tests pass.

- [ ] **Step 6: Commit Task 1**

```bash
git add app/feedback_processing.py app/store.py tests/test_feedback_processing.py
git commit -m "feat: persist feedback processing rounds"
```

### Task 2: Make claim, evidence, resolve, and reopen round-aware

**Files:**
- Modify: `app/feedback_processing.py`
- Modify: `app/store.py`
- Test: `tests/test_feedback_processing.py`

- [ ] **Step 1: Write failing state-transition tests**

Add focused tests for:

```python
reopened = store.reopen_feedback_processing_item("feedback-1", reason="premature resolution")
assert reopened.status == "pending"
assert reopened.current_round_id == 0
assert store.list_feedback_processing_rounds("feedback-1")[0].reopen_reason == "premature resolution"
```

The tests must also prove:

- resolved reopen appends exactly one `resolved -> pending` transition;
- pending reopen is an idempotent no-op;
- processing reopen raises `FeedbackProcessingReopenError` without mutation;
- missing/incomplete history rolls back atomically;
- new claim creates round 2 in a new batch;
- round 2 starts with empty associations and evidence;
- patch writes only to the current processing round;
- old resolved rounds reject mutation;
- resolve rejects evidence from round 1 and resolves round 2 atomically.

- [ ] **Step 2: Run tests and verify RED**

```bash
/Users/derek/miniforge3/bin/python -m pytest tests/test_feedback_processing.py -k 'reopen or current_round or stale_round' -q
```

Expected: failures for missing reopen/round-aware Store operations.

- [ ] **Step 3: Implement Store operations**

Add one typed `FeedbackProcessingReopenError` with stable `error_code`. Add
helpers that read a round and transition without accepting arbitrary statuses.
Implement three public Store methods with these exact signatures and contracts:

- `reopen_feedback_processing_item(self, feedback_key: str, *, reason: str) -> FeedbackProcessingItem | None` performs the atomic resolved-to-pending transition and returns the refreshed projection;
- `list_feedback_processing_rounds(self, feedback_key: str) -> list[FeedbackProcessingRound]` returns newest round first;
- `list_feedback_processing_transitions(self, feedback_key: str) -> list[FeedbackProcessingTransition]` returns newest transition first.

Use `_immediate_write_transaction()` for reopen, claim, and resolve. Reopen
must preserve the old round, clear current projection fields, clear source
`feedback_events.resolved_at`, and append one transition. Claim must allocate
`max(round_number)+1`, insert the new round and transition, and point the item
at it. Association/evidence patch and resolve must require that exact round.

- [ ] **Step 4: Strengthen receipt validation**

Keep strict test/restart/health validation and add required backlog evidence to
`ResolutionEvidence`. Validate the commit with Git ancestry at the API boundary
rather than requiring equality with the service checkout HEAD. Store resolve
receives a boolean or validated target ref result and never runs Git itself.

- [ ] **Step 5: Run focused and full Store tests**

```bash
/Users/derek/miniforge3/bin/python -m pytest tests/test_feedback_processing.py -q
```

Expected: all pass with no warnings.

- [ ] **Step 6: Commit Task 2**

```bash
git add app/feedback_processing.py app/store.py tests/test_feedback_processing.py
git commit -m "feat: reopen feedback into new processing rounds"
```

### Task 3: Expose round-aware local API and update repository Skill

**Files:**
- Modify: `app/web_api/registration.py`
- Modify: `skills/ceo-feedback-processing/SKILL.md`
- Test: `tests/test_feedback_processing_e2e.py`
- Test: `tests/test_feedback_skill_contract.py`
- Optional create under the baseline condition described in the file map: `tests/test_feedback_processing_api.py`

- [ ] **Step 1: Write failing API and Skill tests**

Cover `POST /api/console/feedback/items/{key}/reopen` with required reason,
not-found, processing conflict, pending idempotency, successful reopen, and
history readback. Extend the attempt-8308 E2E through new batch/round 2 and
prove old evidence cannot resolve it. Assert the Skill includes the literal
reopen operation and forbids evidence reuse.

- [ ] **Step 2: Run tests and verify RED**

```bash
/Users/derek/miniforge3/bin/python -m pytest tests/test_feedback_processing_e2e.py tests/test_feedback_skill_contract.py -q
```

Expected: reopen route/history assertions fail. If collection fails solely on
the documented email branch signature mismatch, run the minimal real-route API
test as additional RED evidence and retain the full E2E for the final gate.

- [ ] **Step 3: Implement API payloads and reopen route**

Add the route with exact body validation and map Store errors to the stable
codes in the spec. Extend feedback detail with:

```json
{"current_processing": null, "processing_history": []}
```

Make batch detail source its items from batch round rows. Change resolve Git
validation to `git cat-file -e <sha>^{commit}` followed by
`git merge-base --is-ancestor <sha> main`; reject nonzero results without
changing state.

- [ ] **Step 4: Update the repository Skill**

Add the exact reopen endpoint and rules: factual reason, return to pending,
claim new batch, never copy old evidence, current-round-only resolution, and
no direct SQLite writes.

- [ ] **Step 5: Run API, Skill, and Store tests**

```bash
/Users/derek/miniforge3/bin/python -m pytest tests/test_feedback_processing.py tests/test_feedback_processing_e2e.py tests/test_feedback_skill_contract.py -q
```

Expected: all pass once the independent email route baseline is consistent.

- [ ] **Step 6: Commit Task 3**

```bash
git add app/web_api/registration.py skills/ceo-feedback-processing/SKILL.md tests/test_feedback_processing_e2e.py tests/test_feedback_skill_contract.py tests/test_feedback_processing_api.py
git commit -m "feat: expose local feedback reopen API"
```

Omit the optional test path from `git add` when it was not needed.

### Task 4: Add minimal React reopen interaction and history

**Files:**
- Modify: `frontend/src/api/console.ts`
- Modify: `frontend/src/pages/FeedbackPage.tsx`
- Modify: `frontend/src/pages/FeedbackPage.test.tsx`
- Modify: `frontend/src/styles.css`

- [ ] **Step 1: Write failing React tests**

Add tests proving resolved-only button visibility, dialog opening, required
reason, disabled/loading confirm, successful pending refresh and success
message, error text with preserved input, history ordering, and retained
attempt/run/Workbench links.

- [ ] **Step 2: Run the page test and verify RED**

```bash
pnpm --dir frontend test -- FeedbackPage.test.tsx
```

Expected: failures because the API client, button, dialog, and history do not
exist.

- [ ] **Step 3: Add typed API client**

Extend `FeedbackItem` with optional `current_processing` and
`processing_history`. Add:

```typescript
export function reopenFeedback(feedbackKey: string, reason: string) {
  return command(`/api/console/feedback/items/${encodeURIComponent(feedbackKey)}/reopen`, { reason });
}
```

- [ ] **Step 4: Implement the minimal page interaction**

Use existing button, status, page-state, and dialog styling conventions. Keep
dialog state local to `FeedbackPage`; do not create a workflow framework. On
success close the dialog, show the success message, and refresh list data. On
failure keep the dialog and reason. Render compact round summaries in expanded
detail.

- [ ] **Step 5: Run page tests, frontend tests, lint, and build**

```bash
pnpm --dir frontend test -- FeedbackPage.test.tsx
pnpm --dir frontend test
pnpm --dir frontend lint
pnpm --dir frontend build
```

Expected: all commands exit zero.

- [ ] **Step 6: Commit Task 4**

```bash
git add frontend/src/api/console.ts frontend/src/pages/FeedbackPage.tsx frontend/src/pages/FeedbackPage.test.tsx frontend/src/styles.css
git commit -m "feat: reopen resolved feedback from the console"
```

### Task 5: Integrate, document, migrate safely, and verify runtime

**Files:**
- Modify: `CHANGELOG.md`
- Verify all files from Tasks 1-4

- [ ] **Step 1: Add changelog after tests pass**

Document the local reopen action, immutable historical rounds, new-batch
reprocessing, current-round receipt enforcement, and absence of new Agent
workflow/authentication.

- [ ] **Step 2: Run complete related verification**

```bash
/Users/derek/miniforge3/bin/python -m pytest tests/test_feedback_processing.py tests/test_feedback_processing_e2e.py tests/test_feedback_skill_contract.py tests/test_console_web_api.py -q
/Users/derek/miniforge3/bin/python -m ruff check app/feedback_processing.py app/store.py app/web_api/registration.py tests/test_feedback_processing.py tests/test_feedback_processing_e2e.py tests/test_feedback_skill_contract.py
pnpm --dir frontend test
pnpm --dir frontend lint
pnpm --dir frontend build
git diff --check
```

Record exact pass/skip counts and distinguish unrelated pre-existing failures
instead of weakening the gate.

- [ ] **Step 3: Dispatch final spec and quality review**

Review every spec requirement against the diff and test coverage. Fix all
missing or extra behavior, then repeat verification.

- [ ] **Step 4: Commit integration documentation**

```bash
git add CHANGELOG.md
git commit -m "docs: document feedback reopen history"
```

- [ ] **Step 5: Integrate against the latest consistent main**

Fetch `origin/main`. Confirm the committed email dependency baseline is now in
`main`; if not, keep feedback commits isolated rather than copying uncommitted
email files. Once consistent, integrate the feedback commits without rewriting
unrelated history and rerun the complete related verification on the merged
result.

- [ ] **Step 6: Back up and verify the production database**

Use SQLite online backup to a timestamped explicit file beside the production
database. Open the backup read-only and compare counts for
`feedback_events`, `feedback_processing_items`, and
`feedback_processing_batches`. Keep only the newest verified backup created by
this workflow.

- [ ] **Step 7: Restart and verify runtime**

Capture the current PID, run:

```bash
launchctl kickstart -k gui/$(id -u)/com.ceo-agent-service.main
launchctl print gui/$(id -u)/com.ceo-agent-service.main
curl -sS http://127.0.0.1:8765/healthz
curl -sS http://127.0.0.1:8765/api/console/status
```

Require a new PID, HTTP 200/`ok=true`, zero quality-gate violations, and zero
required processing/failed/retryable backlog.

- [ ] **Step 8: Run the non-delivering local E2E and read back**

Create a local test feedback, exercise round 1 resolve, reopen, round 2 claim,
stale receipt rejection, and round 2 resolution. Read item, old batch, new
batch, history, and UI state. Do not send an external message.

- [ ] **Step 9: Finish the branch**

Use `finishing-a-development-branch`: verify tests on the integrated result,
merge only after the dependency baseline is present, push the resulting main,
verify remote SHA, and remove the clean temporary feedback worktree/branch.
