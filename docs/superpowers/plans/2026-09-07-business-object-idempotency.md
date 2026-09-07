# Business Object Idempotency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure one real business object has one current workflow and every equivalent external action executes at most once across triggers, revisions, generations, and Agent runs.

**Architecture:** Add a stable business-object mapping and append-only task inputs to the store, then derive a stable external-action key from that object and the typed action identity. Execute proposal actions in order, reuse successful provider results, and send every successful message through one History projection path.

**Tech Stack:** Python 3, Pydantic v2, SQLite, pytest, DingTalk DWS adapter.

---

### Task 1: Stable business-object identity and input history

**Files:**
- Create: `app/business_identity.py`
- Modify: `app/store.py`
- Test: `tests/test_store.py`

- [ ] Add failing tests proving two OA triggers with the same process/task identifiers return one current task while preserving two input rows.
- [ ] Add a failing test proving unrelated messages retain separate tasks.
- [ ] Add a failing migration test proving historical duplicate tasks remain present while one is selected as the current projection.
- [ ] Implement strict key normalization and OA URL/metadata extraction in `app/business_identity.py`.
- [ ] Add `business_object_key`, input versions, `business_object_tasks`, and `reply_task_inputs` through the normal store migration.
- [ ] Route `enqueue_reply_task`, `ensure_reply_task`, and grouped task insertion through one transactional current-task resolver.
- [ ] Run `pytest -q tests/test_store.py -k 'business_object or reply_task_input'` and commit the task.

### Task 2: Requeue a task when input arrives during processing

**Files:**
- Modify: `app/store.py`
- Modify: `app/worker.py`
- Test: `tests/test_store.py`
- Test: `tests/test_worker.py`

- [ ] Add a failing store test that appends input while a task is processing and expects completion to requeue the same task with a new generation.
- [ ] Add a failing worker test proving the later run receives the latest trigger projection.
- [ ] Capture the input version during claim and compare it during completion/failure settlement.
- [ ] Rotate only the execution generation; preserve task ID, attempts, runs, sessions, events, and task inputs.
- [ ] Reload the current task before building the next Agent context.
- [ ] Run the two focused tests and commit the task.

### Task 3: Stable typed external-action identity

**Files:**
- Modify: `app/agent_contracts.py`
- Modify: `app/agent_context.py`
- Modify: `app/consumer_agent.py`
- Modify: `app/audit_agent.py`
- Modify: `app/store.py`
- Test: `tests/test_agent_contracts.py`
- Test: `tests/test_audit_agent.py`
- Test: `tests/test_store.py`

- [ ] Add failing contract tests requiring a non-empty `action_identity` on every proposed action and documenting reuse across equivalent revisions.
- [ ] Add failing Audit/store tests proving identical business object, action identity, operation, and target produce one external-action key across different runs.
- [ ] Add `action_identity` to `ProposedAction` and update the Consumer/Audit wire instructions.
- [ ] Persist the external-action key with every effect intent and create `external_action_results` keyed by it.
- [ ] Keep authorization and receipt IDs run-scoped; use the new key only for cross-run provider idempotency and completed-result reuse.
- [ ] Run the contract, Audit, and store tests and commit the task.

### Task 4: Ordered execution and completed-result reuse

**Files:**
- Modify: `app/agent_cli.py`
- Modify: `app/agent_turn_runner.py`
- Modify: `app/store.py`
- Test: `tests/test_agent_cli.py`
- Test: `tests/test_audit_agent.py`
- Test: `tests/test_agent_orchestrator.py`

- [ ] Add a failing regression test reproducing run 8410: action 0 returns a provider error and action 1 must never dispatch.
- [ ] Add a failing test where action 0 was completed by an earlier run and action 1 continues without replaying action 0.
- [ ] Before each write, load earlier action results; reject dispatch when an earlier action is incomplete or failed.
- [ ] Return the stored receipt when the exact external action is already complete.
- [ ] Record the full successful provider receipt atomically with acknowledgement.
- [ ] Derive DingTalk provider idempotency from the stable external-action key.
- [ ] Run agent CLI, Audit, and orchestrator tests and commit the task.

### Task 5: Unified successful-message History projection

**Files:**
- Modify: `app/agent_turn_runner.py`
- Modify: `app/store.py`
- Modify: `app/audit_web.py`
- Test: `tests/test_agent_turn_runner.py`
- Test: `tests/test_audit_web.py`

- [ ] Add a failing regression test reproducing run 8409: a successful Audit message with provider IDs must appear in `sent_replies` and History.
- [ ] Add a failing test proving a reused external-action result does not create a second outbound message.
- [ ] Replace trigger-local message recording with one transactional projection method that stores the provider receipt and links every observing run.
- [ ] Render reused delivery status and the original message reference in History.
- [ ] Run the focused runner and History tests and commit the task.

### Task 6: Documentation, migration verification, and deployment

**Files:**
- Modify: `docs/architecture.md`
- Modify: `docs/runtime-mechanism.md`
- Modify: `docs/error-catalog.md`
- Modify: `CHANGELOG.md`
- Test: `tests/test_agent_contracts.py`
- Test: `tests/test_store.py`
- Test: `tests/test_worker.py`
- Test: `tests/test_agent_cli.py`
- Test: `tests/test_audit_agent.py`
- Test: `tests/test_agent_orchestrator.py`
- Test: `tests/test_audit_web.py`

- [ ] Document the business-object, append-only input, stable action, ordered execution, and History rules without introducing read-only recovery or unknown-effect states.
- [ ] Run the focused suites and resolve every regression without deleting valuable tests.
- [ ] Run the complete test suite and resolve the 10 recorded baseline failures before deployment.
- [ ] Back up the production database, initialize a copy with the new store, and verify row counts plus foreign-key integrity before touching the live service.
- [ ] Merge the feature branch into `main`, restart `com.ceo-agent-service.main`, verify a new PID, and confirm no unresolved failed or processing backlog.
- [ ] Read the Wu Kexin OA object and message History after deployment; verify no new outbound action was produced by migration or restart.
- [ ] Commit final documentation and report exact commits, tests, migration evidence, service PID, backlog, and remaining blockers.
