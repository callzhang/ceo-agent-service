# Workbench Runtime Auto Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove Workbench-owned approval and redaction policy while keeping runtime execution correctness intact.

**Architecture:** Limit behavior changes to the Workbench Codex adapter and Workbench frontend presentation boundary. Keep confirmation persistence/API compatibility for historical data, but remove every path that creates new confirmations. Run Codex with its native non-interactive bypass and preserve the existing executor/store lease and recovery machinery.

**Tech Stack:** Python 3.12, Codex JSONL runtime adapter, React 19, TypeScript, Vitest, pytest, Vite.

---

### Task 1: Lock the new runtime contract with failing tests

**Files:**
- Modify: `tests/test_workbench_codex_runtime.py`
- Modify: `frontend/src/components/ExecutionStep.test.tsx`

- [ ] Replace the command-construction expectation with assertions that `--dangerously-bypass-approvals-and-sandbox` is present and no `workbench_confirmation` overlay or instruction is present.
- [ ] Add a normalizer regression in which a tool named `workbench_confirmation.request_reviewed_action` completes as an ordinary MCP call and emits no `confirmation_required` event.
- [ ] Change credential-shaped assistant/tool payload regressions to require exact round-trip values.
- [ ] Add a frontend regression requiring credential-shaped summary text to render unchanged.
- [ ] Run the focused tests and confirm they fail for the old approval and redaction behavior.

### Task 2: Remove the Workbench security policy from the Codex adapter

**Files:**
- Modify: `app/workbench/codex_runtime.py`
- Delete: `app/workbench/confirmation_mcp.py`

- [ ] Remove the confirmation server constants, injected MCP overlay, risk developer prompt, proposal parser, and special confirmation event emission.
- [ ] Start Codex with `use_approval_bypass=True` and no Workbench-owned developer instructions.
- [ ] Remove Workbench credential rejection and recursive redaction from assistant and tool event normalization.
- [ ] Stop rewriting the user's Codex home to remove a confirmation server; use the native Codex environment directly.
- [ ] Keep output-size, JSON validity, correlation, timeout, stop, process-group, and session-reference validation unchanged.
- [ ] Run the focused backend tests until green.

### Task 3: Make the frontend white-box boundary literal

**Files:**
- Modify: `frontend/src/components/ExecutionStep.tsx`
- Modify: `frontend/src/components/ExecutionStep.test.tsx`
- Modify: `docs/user-guide.md`

- [ ] Remove credential-pattern replacement from `safeDisplayText`; retain only control-character cleanup and empty fallback handling.
- [ ] Document automatic runtime execution, raw tool evidence, historical-only confirmation compatibility, and preserved stop/idempotency guarantees.
- [ ] Run the focused frontend test and the full frontend suite.

### Task 4: Verify, commit, release, and accept

**Files:**
- Generated build only: `app/static/workbench/` (ignored deployment output)

- [ ] Run Ruff, Workbench runtime/executor/store/API/SSE tests, frontend tests, and the production build.
- [ ] Run `git diff --check` and inspect the scoped diff.
- [ ] Commit the feature and merge it into `/Users/derek/Documents/Projects/ceo-agent-service-release` without touching the existing `.venv` symlinks.
- [ ] Build the release frontend, restart `com.ceo-agent-service.main`, and verify the launchd PID changed.
- [ ] Verify claimed work is resumable/idempotent, no processing backlog is stuck, and external-action reconciliation reports no new ambiguity.
- [ ] Run one harmless local-command task through the production API/browser and verify no confirmation event, exact command details, and a terminal result.
