# Needs Human Feedback and Session Reuse Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Turn `needs_human` into a Skill-policy feedback workflow that can record one-time facts and Skill updates together, reuse the existing Consumer Codex session for reruns, persist confirmed feedback through memory-connector, and terminate retry loops with explicit outcomes.

**Architecture:** Add independent feedback metadata (`feedback_text`, `feedback_scope`, and optional Skill-update candidate) to the actionable-attempt path. A rerun creates a new execution generation but keeps the conversation's compatible Consumer runtime session; Skill-update feedback is validated and receipt-bound before the current task is rerun. Technical failures remain technical failures, while unsupported policy classes produce `needs_human` with reusable choices. Retry/reconciliation writes a terminal state when configured timeout or attempt limits are reached.

**Tech Stack:** Python, Pydantic, SQLite, FastAPI/audit web, Codex CLI runtime, memory-connector MCP, pytest.

---

### Task 1: Lock the intended feedback and retry contracts with tests

**Files:**
- Modify: `tests/test_audit_web.py`
- Modify: `tests/test_agent_context.py`
- Modify: `tests/test_agent_turn_runner.py`
- Modify: `tests/test_agent_orchestrator.py`
- Modify: `tests/test_agent_runtime_worker.py`

- [ ] **Step 1: Write failing tests**

Add tests asserting that the human-decision card offers independent `feedback_scope` and `skill_update` controls, that posting both persists both values, that manual reruns keep the prior Consumer session id while changing `execution_generation`, and that unsupported OA authorization is emitted as a reusable policy gap rather than a terminal technical failure. Add a worker test proving a retry with no external effect reaches a terminal `failed` attempt after the configured retry deadline rather than remaining `processing`.

- [ ] **Step 2: Run focused tests and confirm the expected failures**

Run:

```sh
pytest -q tests/test_audit_web.py -k "feedback_scope or skill_update" tests/test_agent_context.py -k "manual_rerun" tests/test_agent_turn_runner.py -k "reuse" tests/test_agent_orchestrator.py -k "policy_gap" tests/test_agent_runtime_worker.py -k "retry_deadline"
```

Expected: failures because the request payload has no feedback dimensions, forced reruns clear route sessions, and retry exhaustion does not yet assert a bounded terminal state.

- [ ] **Step 3: Commit the red tests**

```sh
git add tests/test_audit_web.py tests/test_agent_context.py tests/test_agent_turn_runner.py tests/test_agent_orchestrator.py tests/test_agent_runtime_worker.py
git commit -m "test: define skill feedback and session reuse contracts"
```

### Task 2: Add independent feedback metadata and UI/API handling

**Files:**
- Modify: `app/store.py`
- Modify: `app/audit_web.py`
- Modify: `app/agent_context.py`
- Modify: `tests/test_audit_web.py`
- Modify: `tests/test_agent_context.py`

- [ ] **Step 1: Extend the persisted model and schema**

Add nullable/defaulted columns to `reply_attempts` for `feedback_text`, `feedback_scope` (`one_time` or `reusable_policy`), `skill_update_requested`, `skill_candidate_id`, and `source_session_id`. Keep existing rows valid with defaults and preserve the immutable source attempt.

- [ ] **Step 2: Parse and validate the two dimensions**

Update `handle_needs_human_decision_post` to accept `instruction`, `feedback_scope`, and `skill_update_requested`. Require non-empty feedback for either dimension, allow both together, and reject unsupported enum values with HTTP 400. Store the source attempt as `decision_selected`, then enqueue one rerun carrying the complete feedback object.

- [ ] **Step 3: Render the corrected 7211-style card**

Render two explicit choices: “仅本次采用反馈” and “沉淀为 Skill 规则并用新规则处理本次”. Make clear that the second choice also applies the new rule to the current task. Preserve the existing risk-boundary wording (“可以做 A，但注意 X；不要做 Y”).

- [ ] **Step 4: Verify the tests pass**

Run:

```sh
pytest -q tests/test_audit_web.py -k "feedback_scope or skill_update" tests/test_agent_context.py -k "manual_rerun"
```

- [ ] **Step 5: Commit**

```sh
git add app/store.py app/audit_web.py app/agent_context.py tests/test_audit_web.py tests/test_agent_context.py
git commit -m "feat: persist independent skill feedback dimensions"
```

### Task 3: Reuse the existing Consumer session for new generations

**Files:**
- Modify: `app/agent_turn_runner.py`
- Modify: `app/consumer_agent.py`
- Modify: `app/store.py`
- Modify: `tests/test_agent_turn_runner.py`
- Modify: `tests/test_agent_runtime_worker.py`

- [ ] **Step 1: Remove the forced-rerun session clearing behavior**

Stop clearing compatible conversation route sessions solely because `force_new_decision` is set. Keep the new execution generation, but pass the persisted `source_session_id` to the Consumer runtime when the contract hash and route are compatible. Clear the session only for explicit `session_route_incompatible` evidence.

- [ ] **Step 2: Persist sessions for the active Codex route**

Ensure the observed Codex session id is written to `conversation_runtime_sessions` for the actual route name, including `codex_api`, and that the next Consumer rerun reads it. Persist `source_session_id` on the runtime attempt for auditability.

- [ ] **Step 3: Include feedback in the resumed prompt without losing history**

Render the feedback object as a new turn in the existing session: identify the source attempt, the one-time/reusable scope, and any Skill receipt. Do not replay the old tool command; require the resumed turn to re-read current evidence and return a fresh typed result.

- [ ] **Step 4: Verify same-session/new-generation behavior**

Run:

```sh
pytest -q tests/test_agent_turn_runner.py -k "reuse" tests/test_agent_runtime_worker.py -k "manual_rerun or session"
```

- [ ] **Step 5: Commit**

```sh
git add app/agent_turn_runner.py app/consumer_agent.py app/store.py tests/test_agent_turn_runner.py tests/test_agent_runtime_worker.py
git commit -m "feat: resume consumer session across feedback generations"
```

### Task 4: Classify unsupported policy gaps and persist feedback to memory-connector

**Files:**
- Modify: `app/agent_orchestrator.py`
- Modify: `app/audit_agent.py`
- Modify: `app/consumer_agent.py`
- Modify: `app/worker.py`
- Modify: `tests/test_agent_orchestrator.py`
- Modify: `tests/test_agent_runtime_worker.py`

- [ ] **Step 1: Add a reusable policy-gap classifier**

Map cases such as missing manager/approver authorization mapping, temporary travel/leave scope, and recurring-calendar scope ambiguity to `needs_human` with 2–4 reusable choices. Map malformed JSON, schema mismatch, Codex transport failure, and unavailable runtime evidence to technical `failed` with a bounded retry path; never let them overwrite a policy-gap conclusion.

- [ ] **Step 2: Add memory-connector feedback persistence**

After a confirmed user feedback submission, call the configured `mcp__memory_connector__memory_write` path with a concise source sentence, source attempt id, feedback scope, and project provenance. Store one-time feedback as an episodic event that is not treated as a standing policy; store reusable Skill feedback as a policy candidate linked to the Skill name/version. Do not write raw logs or transient errors.

- [ ] **Step 3: Add Skill-update candidate handling**

For `skill_update_requested`, generate a versioned candidate rule and receipt metadata, validate it against the applicable Skill contract/tests, then resume the same Consumer session with the new receipt. If validation fails, record a terminal technical failure with no external action; do not ask the user to execute the task.

- [ ] **Step 4: Verify classification and memory hooks**

Run:

```sh
pytest -q tests/test_agent_orchestrator.py -k "policy_gap or authorization" tests/test_agent_runtime_worker.py -k "memory or feedback"
```

- [ ] **Step 5: Commit**

```sh
git add app/agent_orchestrator.py app/audit_agent.py app/consumer_agent.py app/worker.py tests/test_agent_orchestrator.py tests/test_agent_runtime_worker.py
git commit -m "feat: route policy gaps through skill feedback and memory"
```

### Task 5: Bound runtime retries and reconcile live 7211 history

**Files:**
- Modify: `app/agent_effects.py`
- Modify: `app/agent_turn_runner.py`
- Modify: `app/worker.py`
- Modify: `tests/test_agent_runtime_worker.py`
- Modify: `tests/test_agent_turn_runner.py`

- [ ] **Step 1: Add explicit retry deadline/attempt limits**

Use the configured total/idle Codex timeouts (currently 900/300 seconds from launchd) plus a small bounded role retry count. On repeated `codex_result_missing`, `codex_session_locked`, schema mismatch, or reviewed-write authorization failure with `side_effect_state=none`, stop retrying and persist the exact terminal code, root cause, attempted actions, and “no external action” state.

- [ ] **Step 2: Recover stale processing rows safely**

On worker startup and each recovery pass, reclaim processing rows whose lease expired and have no running/unknown Agent run. Do not rotate generations when any external effect is unknown. Leave only explicit future/backoff tasks pending.

- [ ] **Step 3: Run focused and regression tests**

Run:

```sh
pytest -q tests/test_agent_runtime_worker.py -k "retry_deadline or stale or reconciliation" tests/test_agent_turn_runner.py -k "timeout or session"
```

Then run the full relevant suites:

```sh
pytest -q tests/test_agent_orchestrator.py tests/test_agent_runtime_worker.py tests/test_agent_turn_runner.py tests/test_audit_web.py
```

Record any pre-existing failures separately from regressions.

- [ ] **Step 4: Commit**

```sh
git add app/agent_effects.py app/agent_turn_runner.py app/worker.py tests/test_agent_runtime_worker.py tests/test_agent_turn_runner.py
git commit -m "fix: bound agent retries and reconcile stale tasks"
```

### Task 6: Deploy, rerun 7211, and verify readback

**Files:**
- No source changes expected; verify the live release root and SQLite database.

- [ ] **Step 1: Restart and verify launchd**

```sh
launchctl kickstart -k gui/$(id -u)/com.ceo-agent-service.main
launchctl print gui/$(id -u)/com.ceo-agent-service.main | sed -n '1,80p'
```

Confirm a new PID and `state = running`.

- [ ] **Step 2: Submit 7211 feedback in both dimensions**

Use the local audit endpoint to submit a reusable-policy feedback record with the one-time fact “我去美国出差” and the policy rule “临时出差默认只影响覆盖的日程实例，不修改整个重复系列，除非明确要求”。 Verify the source is `decision_selected`, a new generation exists, and the Consumer run has the previous session id.

- [ ] **Step 3: Verify terminal outcome and external readback**

Confirm the new attempt is `executed`, `skipped`, or a documented Skill-policy result; confirm no duplicate calendar mutation or message send; confirm history links the old attempt to the new generation and displays the Skill version/feedback scope.

- [ ] **Step 4: Verify backlog closure**

Query current unresolved attempts and task states. No related 7211/feedback task may remain unexplained in `processing`, `pending`, `failed`, or `needs_human`; unrelated existing tasks must be listed separately with their exact root causes.

