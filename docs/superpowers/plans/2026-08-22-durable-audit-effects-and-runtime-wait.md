# Durable Audit Effects and Runtime Wait Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist safe Audit write evidence and prevent transient runtime-route unavailability from being terminally exhausted.

**Architecture:** The Audit process prepares action-bound intent rows before any write. The write MCP endpoint consumes and acknowledges those rows around the external command, while the store preserves unknown-state diagnosis. A separate worker policy treats only transient no-route evidence as a provider-recovery wait.

**Tech Stack:** Python 3, SQLite, Pydantic, pytest, Ruff, launchd.

---

### Task 1: Audit write intent durability

**Files:**
- Modify: `app/store.py`
- Modify: `app/audit_agent.py`
- Modify: `app/agent_cli.py`
- Test: `tests/test_store.py`
- Test: `tests/test_audit_agent.py`
- Test: `tests/test_agent_cli.py`

- [ ] Write focused tests for one-shot dispatch, durable acknowledgement, malformed action rejection, and command-delivery receipts.
- [ ] Run each new test and confirm it fails against the pre-feature behavior where applicable.
- [ ] Implement only the rows, authorization propagation, local consumption, acknowledgement, and receipt behavior required by those tests.
- [ ] Run `pytest tests/test_agent_cli.py tests/test_audit_agent.py tests/test_store.py -q`.
- [ ] Run `ruff check app/agent_cli.py app/audit_agent.py app/agent_turn_runner.py app/store.py tests/test_agent_cli.py tests/test_audit_agent.py tests/test_store.py`.
- [ ] Commit the source, tests, root-cause analysis, spec, and plan as one durable-Audit-effects feature.

### Task 2: Runtime-route transient wait

**Files:**
- Modify: `app/agent_runtime_router.py`
- Modify: `app/agent_orchestrator.py` if the retryable signal is not preserved
- Modify: `app/worker.py`
- Test: `tests/test_routed_codex_execution.py`
- Test: `tests/test_agent_orchestrator.py`
- Test: `tests/test_worker.py`

- [ ] Write a worker regression test that injects a transient `runtime_route_unavailable` at `max_task_attempts` and asserts `pending`, the unchanged error code, and no same-pass retry.
- [ ] Run that test and confirm it fails because the old worker marks the task `failed`.
- [ ] Trace the router’s failure classification into the orchestration result; add the smallest explicit provider-recovery signal necessary for transient no-route conditions.
- [ ] Run the focused worker, orchestrator, and routed-execution suites.
- [ ] Run Ruff for every changed file and commit the runtime-route wait fix separately.

### Task 3: Release verification

**Files:**
- Verify: `git status --short`, launchd service state, live SQLite backlog

- [ ] Run all suites directly covering both commits.
- [ ] Confirm `git diff --check` and `git status --short` show no tracked source or test changes.
- [ ] Restart `com.ceo-agent-service.main`, confirm a new launchd PID, then inspect live runtime route state and `failed`/`processing` reply-task backlog.
- [ ] Push the reviewed commits and report exact verification results plus any pre-existing backlog that cannot be safely altered.
