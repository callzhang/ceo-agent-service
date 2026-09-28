# Agent Progress Watchdog Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let long Agent turns continue while structured provider progress arrives, while retaining a five-minute idle watchdog and a two-hour emergency process ceiling.

**Architecture:** Keep process execution bounded by the existing runner. Raise only the shared task-agent emergency ceiling to 7200 seconds, make streamed stdout lines the progress signal when the Agent callback is installed, keep the 300-second idle watchdog, and document that elapsed time is not a normal failure condition. Validate the launchd contract and focused runner tests before deployment.

**Tech Stack:** Python, pytest, launchd plist, Markdown architecture/spec documentation.

---

### Task 1: Lock the launchd and runner contract with failing tests

**Files:**
- Modify: `tests/test_process_runner.py`
- Modify: `tests/test_hourly_dry_run_launchd.py`

- [x] **Step 1: Add a regression test that stderr activity does not reset the idle watchdog when streamed Agent lines are being tracked.
- [x] **Step 2: Change the launchd assertion from `900` to `7200` and retain the idle assertion at `300`.
- [x] **Step 3: Run the focused tests and observe the expected failure against the current plist/default.

### Task 2: Apply the emergency ceiling and document progress semantics

**Files:**
- Modify: `app/agent_effects.py`
- Modify: `launchd/com.ceo-agent-service.main.plist`
- Modify: `docs/architecture.md`
- Modify: `docs/runtime-mechanism.md`

- [x] **Step 1: Set the task-agent emergency process ceiling to 7200 seconds while leaving the idle watchdog at 300 seconds.
- [x] **Step 2: Update the runtime documentation to state that structured provider events are the normal progress signal, five minutes without one is idle, and 7200 seconds is emergency protection only.
- [x] **Step 3: Run the focused tests and verify they pass.

### Task 3: Verify session recovery and deployment

**Files:**
- No additional source files.

- [x] **Step 1: Run `pytest -q tests/test_process_runner.py tests/test_hourly_dry_run_launchd.py tests/test_agent_orchestrator.py::test_an_audit_that_keeps_reporting_one_failure_stops_after_the_bound`.
- [ ] **Step 2: Commit only the claimed files with message `fix(runtime): use progress watchdog for long agent turns`.
- [ ] **Step 3: Run `python -m app.deploy`.
- [ ] **Step 4: Read back launchd environment, PID, `/healthz`, runtime skill load, pending queues, and the latest weekly-report scheduled runs.
