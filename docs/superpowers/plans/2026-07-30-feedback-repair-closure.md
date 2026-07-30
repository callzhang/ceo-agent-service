# Feedback Repair Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn actionable negative feedback into one idempotent Direct Agent rerun and track it through execution, verification, and feedback resolution.

**Architecture:** Extend the existing `service_bugfix_candidates` record into a durable repair lifecycle and bridge eligible candidates to the existing manual-rerun generation of the original `reply_task`. The same Direct Agent re-reads the original request and reviewer feedback; no second Agent client or self-modifying background developer is added. Machine-verifiable failures with no confirmed side effect may rerun automatically, while product changes, ambiguous feedback, missing targets, and any unknown/confirmed prior side effect remain review-gated.

**Tech Stack:** Python 3.12, SQLite, Pydantic v2, native Direct Agent runtime, FastAPI History UI, pytest, launchd.

---

## Prerequisite And Safety Boundary

This plan starts after `codex/agent-runtime-simplification` is merged and the plan `2026-07-30-agent-confirmed-fact-grounding.md` is complete.

The repair bridge is for finishing the original authorized user action. It does not autonomously edit application code from a feedback comment. A code, prompt, schema, product, or experience optimization becomes `needs_review` with evidence and a proposed test plan; Derek's approval is required before a development task is created.

Automatic rerun is allowed only when all conditions are true:

1. the feedback event is linked to one sent reply and one original attempt;
2. the original attempt or Agent run has a structured failed/retryable outcome;
3. persisted execution evidence says no prior effect was confirmed or left unknown;
4. the original trigger and target identifiers remain available;
5. no newer successful attempt or sent reply already resolved the trigger;
6. the same feedback event has not already created a rerun generation.

Everything else is review-gated. In particular, `side_effect_state in {confirmed, unknown}` must never be auto-rerun.

## State Machine

`service_bugfix_candidates.status` uses these values:

- `pending`: feedback was ingested and awaits deterministic evidence classification;
- `queued`: exactly one manual-rerun generation was created;
- `processing`: the linked `reply_task` or `agent_run` is active;
- `needs_review`: automatic rerun is unsafe or the feedback requests product/code behavior change;
- `resolved`: the linked rerun reached a verified terminal result and the feedback event was resolved;
- `blocked`: execution is confirmed impossible under current rules or the required target cannot be recovered from available evidence;
- `failed`: retry budget is exhausted with a clear final error.

`resolved`, `blocked`, and `failed` are terminal. `blocked` is used only for a concrete external prerequisite, not as a generic Agent answer.

## File Map

- Modify: `app/store.py`
  - Add lifecycle fields, atomic claim/update methods, and the candidate-to-rerun link.
- Replace: `app/feedback_bugfix.py`
  - Remove marker lists and classify only from structured feedback, attempt, run, and side-effect evidence.
- Modify: `app/worker.py`
  - Add one maintenance pass that creates, queues, and reconciles repair candidates.
- Modify: `app/agent_context.py`
  - Render the feedback event key and reviewer feedback through the existing manual-rerun instruction only.
- Modify: `app/audit_web.py`
  - Show candidate state, linked task/run/attempt, evidence, final result, and explicit review controls.
- Modify: `tests/test_store.py`
  - Cover state transitions, idempotency, claims, and crash recovery.
- Modify: `tests/test_worker.py`
  - Cover eligibility, automatic rerun, no-duplicate rules, retry, and terminal synchronization.
- Modify: `tests/test_agent_context.py`
  - Cover minimal feedback context and secret exclusion.
- Modify: `tests/test_audit_web.py`
  - Cover filters, linked records, and review actions.
- Modify: `docs/reply-worker-reliability.md`
  - Document the lifecycle, safety gates, and operational verification.

### Task 1: Persist A Durable Candidate Lifecycle

**Files:**
- Modify: `app/store.py`
- Test: `tests/test_store.py`

- [ ] **Step 1: Write failing schema and idempotency tests**

Add tests that create a candidate, claim it once, and verify the linked rerun cannot change after queueing:

```python
def test_service_bugfix_candidate_claim_and_rerun_link_are_idempotent(tmp_path):
    store = AutoReplyStore(tmp_path / "db.sqlite3")
    candidate = _create_feedback_candidate(store)

    claimed = store.claim_service_bugfix_candidate(candidate.id)
    duplicate_claim = store.claim_service_bugfix_candidate(candidate.id)

    assert claimed is not None
    assert claimed.status == "processing"
    assert duplicate_claim is None

    assert store.link_service_bugfix_candidate_to_reply_task(
        candidate.id,
        reply_task_id=91,
        execution_generation="generation-1",
    ) is True
    assert store.link_service_bugfix_candidate_to_reply_task(
        candidate.id,
        reply_task_id=92,
        execution_generation="generation-2",
    ) is False
```

Add one migration test that opens a pre-change database and verifies existing `pending` rows survive with empty lifecycle fields.

- [ ] **Step 2: Run the tests and verify they fail**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_store.py \
  -k 'service_bugfix_candidate_claim or service_bugfix_candidate_migration'
```

Expected: FAIL because the claim/link APIs and lifecycle columns do not exist.

- [ ] **Step 3: Extend the candidate model and schema**

Add these fields to `ServiceBugfixCandidate` and the migration in `app/store.py`:

```python
class ServiceBugfixCandidate(BaseModel):
    id: int
    feedback_event_key: str
    feedback_token: str = ""
    attempt_id: int = 0
    status: str = "pending"
    disposition: str = ""
    evidence_json: str = "{}"
    reply_task_id: int = 0
    execution_generation: str = ""
    attempts: int = 0
    available_at: str = ""
    locked_at: str = ""
    error: str = ""
    resolution_summary: str = ""
    resolved_at: str = ""
    title: str
    reason: str
    feedback_comment: str
    conversation_title: str = ""
    trigger_text: str = ""
    created_at: str
    updated_at: str
```

Use one `ALTER TABLE service_bugfix_candidates ADD COLUMN` migration statement for each new column on existing databases. Add indexes on `(status, available_at, id)` and `reply_task_id`. Do not create a second task or Agent-run table.

- [ ] **Step 4: Add atomic lifecycle methods**

Implement these store methods with `begin immediate` where a claim or link must be atomic:

- `claim_service_bugfix_candidate(self, candidate_id: int, *, now: str = "") -> ServiceBugfixCandidate | None`
- `defer_service_bugfix_candidate(self, candidate_id: int, *, error: str, available_at: str) -> bool`
- `link_service_bugfix_candidate_to_reply_task(self, candidate_id: int, *, reply_task_id: int, execution_generation: str) -> bool`
- `finish_service_bugfix_candidate(self, candidate_id: int, *, status: Literal["needs_review", "resolved", "blocked", "failed"], summary: str, error: str = "") -> bool`

The SQL transition predicates must reject terminal-to-nonterminal transitions and a second link with a different task/generation.

- [ ] **Step 5: Run store tests**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_store.py -k 'service_bugfix_candidate'
```

Expected: PASS.

- [ ] **Step 6: Commit the lifecycle**

```bash
git add app/store.py tests/test_store.py
git commit -m "feat: persist feedback repair lifecycle"
```

### Task 2: Replace Keyword Classification With Structured Evidence

**Files:**
- Modify: `app/feedback_bugfix.py`
- Test: `tests/test_worker.py`

- [ ] **Step 1: Write failing classification tests**

Add tests for four deterministic dispositions:

```python
@pytest.mark.parametrize(
    ("attempt_status", "run_status", "side_effect_state", "expected"),
    [
        ("failed", "failed", "none", "auto_rerun"),
        ("blocked", "failed", "none", "needs_review"),
        ("failed", "unknown", "unknown", "needs_review"),
        ("sent", "completed", "confirmed", "needs_review"),
    ],
)
def test_feedback_repair_disposition_uses_execution_evidence(
    attempt_status, run_status, side_effect_state, expected
):
    result = classify_feedback_repair(
        event=_negative_feedback(),
        attempt=_attempt(send_status=attempt_status),
        run=_agent_run(status=run_status, side_effect_state=side_effect_state),
        newer_resolution_exists=False,
    )

    assert result.disposition == expected
```

Add tests that positive feedback returns `resolve`, an unlinked event returns `needs_review`, and a newer successful result returns `already_resolved`.

- [ ] **Step 2: Run the tests and verify they fail**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_worker.py -k 'feedback_repair_disposition'
```

Expected: FAIL because the existing classifier depends on text marker lists and does not accept execution evidence.

- [ ] **Step 3: Replace `app/feedback_bugfix.py` with typed evidence classification**

Delete `SERVICE_BUGFIX_REQUEST_MARKERS`, `SERVICE_BUGFIX_PROBLEM_MARKERS`, `ARBITRARY_DEVELOPMENT_MARKERS`, and `NEW_FEATURE_REQUEST_MARKERS`.

Define this public result:

```python
class FeedbackRepairDisposition(StrEnum):
    RESOLVE = "resolve"
    ALREADY_RESOLVED = "already_resolved"
    AUTO_RERUN = "auto_rerun"
    NEEDS_REVIEW = "needs_review"


@dataclass(frozen=True)
class FeedbackRepairClassification:
    disposition: FeedbackRepairDisposition
    reason_code: str
    evidence: dict[str, object]
```

Implement `classify_feedback_repair` from typed fields only:

- positive rating with an empty comment -> `resolve`;
- newer sent/completed result for the same trigger -> `already_resolved`;
- linked retryable failed run, `side_effect_state == "none"`, intact trigger, and no newer result -> `auto_rerun`;
- every other negative, blocked, ambiguous, unlinked, confirmed-effect, or unknown-effect event -> `needs_review`.

Do not inspect feedback prose with substrings, keywords, regular expressions, or language-specific static lists. Preserve the comment as Agent/reviewer evidence, not as a service routing input.

- [ ] **Step 4: Run focused classification tests**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_worker.py -k 'feedback_repair_disposition'
```

Expected: PASS.

- [ ] **Step 5: Commit evidence classification**

```bash
git add app/feedback_bugfix.py tests/test_worker.py
git commit -m "refactor: classify feedback repairs from execution evidence"
```

### Task 3: Bridge Eligible Feedback To One Manual Rerun

**Files:**
- Modify: `app/store.py`
- Modify: `app/worker.py`
- Modify: `app/agent_context.py`
- Test: `tests/test_store.py`
- Test: `tests/test_worker.py`
- Test: `tests/test_agent_context.py`

- [ ] **Step 1: Write a failing end-to-end queue test**

Add a test that creates a failed Agent run with no side effect, adds negative feedback, runs one maintenance pass twice, and verifies one rerun generation:

```python
def test_feedback_maintenance_enqueues_exactly_one_manual_rerun(worker):
    source = _persist_failed_agent_attempt(worker, side_effect_state="none")
    event = _persist_negative_feedback(worker.store, source)

    assert worker.process_feedback_repairs_once() == 1
    assert worker.process_feedback_repairs_once() == 0

    candidate = worker.store.get_service_bugfix_candidate_for_feedback(event.key)
    task = worker.store.get_reply_task(candidate.reply_task_id)
    assert candidate.status == "queued"
    assert task.manual_rerun_attempt_id == source.attempt_id
    assert task.execution_generation == candidate.execution_generation
    assert task.force_new_decision is True
```

Add negative tests for `confirmed`, `unknown`, an unlinked event, a newer sent reply, and a candidate already linked to another generation.

- [ ] **Step 2: Run the tests and verify they fail**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_worker.py \
  -k 'feedback_maintenance or feedback_rerun'
```

Expected: FAIL because there is no maintenance bridge.

- [ ] **Step 3: Add an idempotency key to manual rerun enqueue**

Extend `enqueue_manual_rerun_reply_task` with:

```python
revision_source: str = "manual"
revision_id: str = ""
```

Include both values in `_manual_rerun_revision_key`. For feedback repair use `revision_source="feedback_event"` and `revision_id=event.key`. Preserve the current manual-review fields in the hash. This makes one feedback event map to one rerun generation while allowing a later distinct feedback event to request another reviewed generation.

- [ ] **Step 4: Implement `process_feedback_repairs_once()`**

Add a worker maintenance method that:

1. syncs current feedback through the existing feedback-event mechanism;
2. creates one candidate per unresolved negative event;
3. atomically claims due `pending` candidates;
4. loads the linked sent reply, attempt, reply task, Agent run, and newer trigger results;
5. calls `classify_feedback_repair`;
6. resolves positive/already-resolved feedback immediately;
7. marks unsafe cases `needs_review` with structured evidence;
8. writes the feedback comment into the source attempt's reviewer-feedback field without replacing an existing human correction;
9. enqueues one manual rerun for `auto_rerun`;
10. links candidate and rerun in the same database transaction.

Call this pass from task maintenance after stale Agent-run reconciliation and before normal pending-task consumption. Process a bounded page per pass.

- [ ] **Step 5: Render only minimal feedback evidence to the Direct Agent**

Extend `ManualRerunInstruction` with `feedback_event_key: str = ""`. Render:

```json
{
  "source_attempt_id": 42,
  "feedback_event_key": "feedback-event-key",
  "reviewer_feedback": "The original action was diagnosed but not executed.",
  "suggested_reply_text": ""
}
```

Do not render feedback tokens, raw JSON, signed URLs, credentials, or unrelated feedback events.

- [ ] **Step 6: Run bridge and context tests**

Run:

```bash
.venv/bin/python -m pytest -q \
  tests/test_store.py \
  tests/test_worker.py \
  tests/test_agent_context.py \
  -k 'service_bugfix or feedback_repair or manual_rerun'
```

Expected: PASS.

- [ ] **Step 7: Commit the bridge**

```bash
git add app/store.py app/worker.py app/agent_context.py \
  tests/test_store.py tests/test_worker.py tests/test_agent_context.py
git commit -m "feat: bridge failed feedback to one agent rerun"
```

### Task 4: Reconcile Candidate State To A Verified Terminal Result

**Files:**
- Modify: `app/store.py`
- Modify: `app/worker.py`
- Test: `tests/test_store.py`
- Test: `tests/test_worker.py`

- [ ] **Step 1: Write failing synchronization tests**

Cover these mappings:

```python
@pytest.mark.parametrize(
    (
        "task_status",
        "run_status",
        "outcome",
        "side_effect_state",
        "candidate_status",
    ),
    [
        ("processing", "running", "", "none", "processing"),
        ("done", "completed", "completed", "confirmed", "resolved"),
        ("done", "completed", "no_action", "none", "resolved"),
        ("done", "failed", "needs_human", "none", "needs_review"),
        ("failed", "failed", "failed", "none", "failed"),
        ("processing", "unknown", "", "unknown", "needs_review"),
    ],
)
def test_feedback_candidate_tracks_linked_agent_terminal_state(
    task_status, run_status, outcome, side_effect_state, candidate_status
):
    actual = candidate_status_for_linked_execution(
        task_status=task_status,
        run_status=run_status,
        outcome=outcome,
        side_effect_state=side_effect_state,
        retryable=False,
        retries_remaining=False,
    )

    assert actual == candidate_status
```

For `completed + none`, the test must use `outcome=no_action` with a verified explanation. A requested effectful action cannot reach this combination because completion-evidence validation rejects it.

- [ ] **Step 2: Run the tests and verify they fail**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_worker.py \
  -k 'candidate_tracks_linked_agent_terminal_state'
```

Expected: FAIL because candidate status is not synchronized from the linked task/run.

- [ ] **Step 3: Implement reconciliation without replaying Agent work**

Add the pure `candidate_status_for_linked_execution` mapping used by the test, then add `reconcile_feedback_repair_candidates_once` after Agent-run reconciliation. Reconciliation must only read linked task/run/attempt state and update the candidate:

- running task/run -> `processing`;
- verified `completed` or valid `no_action` -> resolve candidate and feedback event in one transaction;
- retryable failed run with no effect and remaining budget -> defer candidate to the linked task's `available_at`;
- `unknown` effect -> `needs_review`, never replay;
- `needs_human` -> `needs_review` with the Agent reason;
- exhausted retry -> `failed` with the final structured error;
- transient provider or authorization failure -> defer with the linked task retry schedule;
- confirmed wrong owner, missing target after all allowed reads, or a rule that forbids execution -> `blocked` with the exact unrecoverable reason.

Store the final attempt ID and summary in `resolution_summary`; do not copy raw tool output.

- [ ] **Step 4: Add crash recovery**

Reset stale candidate claims only when no linked reply task or Agent run is active. A candidate linked to a running generation stays `processing` and follows the Agent-run lease rules.

- [ ] **Step 5: Run lifecycle tests**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_store.py tests/test_worker.py \
  -k 'feedback_repair or service_bugfix_candidate'
```

Expected: PASS with no duplicate Agent generation after repeated maintenance passes.

- [ ] **Step 6: Commit terminal reconciliation**

```bash
git add app/store.py app/worker.py tests/test_store.py tests/test_worker.py
git commit -m "fix: reconcile feedback repairs to terminal state"
```

### Task 5: Add Reviewable History Controls

**Files:**
- Modify: `app/audit_web.py`
- Test: `tests/test_audit_web.py`

- [ ] **Step 1: Write failing History tests**

Add tests that assert:

- status filters include all seven lifecycle states;
- each row links to the original attempt and linked rerun task;
- `needs_review` shows evidence, proposed action, test plan, risk, and restart requirement;
- feedback tokens and raw JSON are absent;
- approving a reviewed rerun creates exactly one generation;
- dismissing a product suggestion resolves the candidate without running an Agent.

- [ ] **Step 2: Run the tests and verify they fail**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_audit_web.py -k 'service_bugfix or feedback_repair'
```

Expected: FAIL because the current page only lists pending candidates.

- [ ] **Step 3: Render lifecycle state and safe evidence**

Update `render_service_bugfix_candidates()` to show:

- state and last update;
- original attempt link;
- linked rerun task and Agent run links;
- reason code and redacted evidence summary;
- retry schedule or final terminal reason;
- review actions only for `needs_review`.

Use POST routes with the existing CSRF pattern. Approval must call the same idempotent bridge used by maintenance; it must not directly invoke Codex inside the HTTP request.

- [ ] **Step 4: Run History tests**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_audit_web.py -k 'service_bugfix or feedback_repair'
```

Expected: PASS.

- [ ] **Step 5: Commit History support**

```bash
git add app/audit_web.py tests/test_audit_web.py
git commit -m "feat: expose feedback repair lifecycle in History"
```

### Task 6: Document, Deploy, And Verify The Full Loop

**Files:**
- Modify: `docs/reply-worker-reliability.md`

- [ ] **Step 1: Document eligibility and terminal-state rules**

Add a `Feedback repair closure` section containing the six automatic-rerun gates, the seven candidate states, and the rule that unknown/confirmed prior effects never auto-rerun.

- [ ] **Step 2: Run focused suites**

Run:

```bash
.venv/bin/python -m pytest -q \
  tests/test_agent_context.py \
  tests/test_agent_result.py \
  tests/test_agent_runner.py \
  tests/test_agent_runtime_worker.py \
  tests/test_store.py \
  tests/test_worker.py \
  tests/test_audit_web.py
```

Expected: PASS.

- [ ] **Step 3: Run the full suite**

Run:

```bash
.venv/bin/python -m pytest -q
```

Expected: PASS. Any unrelated pre-existing failure must be recorded with its existing baseline before this branch is merged.

- [ ] **Step 4: Commit documentation**

```bash
git add docs/reply-worker-reliability.md
git commit -m "docs: define feedback repair closure operations"
```

- [ ] **Step 5: Push and restart the service**

Push the reviewed branch, then run:

```bash
launchctl kickstart -k gui/$(id -u)/com.ceo-agent-service.main
launchctl print gui/$(id -u)/com.ceo-agent-service.main | sed -n '1,80p'
```

Expected: a new running process.

- [ ] **Step 6: Verify one safe live repair**

Use one negative feedback event linked to a confirmed failed/no-effect run. Verify:

1. one candidate is created;
2. one manual-rerun generation is linked;
3. the Direct Agent sees the original trigger and reviewer feedback;
4. the requested action is executed or reaches a specific review/blocked reason;
5. candidate and feedback event reach the matching terminal state;
6. a second maintenance pass creates no duplicate task or external action.

- [ ] **Step 7: Verify operational backlog**

Confirm:

- `reply_tasks.status in ('failed', 'processing')` is empty or each row has a named terminal external reason;
- no `service_bugfix_candidates` row is stuck in `processing` without an active lease;
- no linked Agent run remains `unknown` without `needs_review` surfacing;
- History filters and links load on the real port-8765 service.

## Acceptance Criteria

- No marker list, keyword router, regular expression, or second Agent client remains in `app/feedback_bugfix.py`.
- One feedback event creates at most one automatic rerun generation.
- Confirmed or unknown prior side effects are never automatically replayed.
- Product/code/experience optimization remains `needs_review` until Derek approves it.
- The same Direct Agent receives the original trigger plus minimal reviewer feedback.
- Candidate state follows task/run state through `resolved`, `needs_review`, `blocked`, or `failed`.
- Feedback is resolved only after a verified terminal result or explicit review dismissal.
- Focused and full tests pass, the service restarts on a new process, and the live repair produces no duplicate external action.
