# Email Worker Status Structure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show one Email worker process with three runtime loops, accounts, and collapsible internal checks while excluding health records from earlier process instances.

**Architecture:** The health writer attaches one generated instance identifier to every record and the process row declares its actual runtime-loop scopes. The backend status projection filters by the current process instance and returns typed groups; the React page renders those groups without interpreting component names.

**Tech Stack:** Python 3, SQLite service state, FastAPI/Pydantic, React 19, TypeScript, Vitest, pytest.

---

### Task 1: Define process-instance health metadata

**Files:**
- Modify: `app/email_worker.py`
- Test: `tests/test_email_worker.py`

- [ ] **Step 1: Write failing tests** asserting that the production health recorder attaches one stable non-empty `instance_id` to all writes in a worker run, and that process health publishes `runtime_loops`, `runtime_loop_scopes`, `readiness_ready`, and `readiness_total` instead of `components`.
- [ ] **Step 2: Run tests to verify red** with `pytest -q tests/test_email_worker.py -k 'health_instance or readiness_barrier or startup_records_process'`; expect assertions for the new metadata to fail.
- [ ] **Step 3: Implement minimal metadata** by generating one instance identifier in `build_production_email_worker_dependencies`, merging it in `record_health`, deriving loop scopes from `email_worker_components`, and letting `EmailWorkerReadiness` publish explicit readiness counts.
- [ ] **Step 4: Run focused tests** with the same command; expect all selected tests to pass.

### Task 2: Replace flat health projection with typed current-instance groups

**Files:**
- Modify: `app/audit_web.py`
- Modify: `app/web_api/status.py`
- Test: `tests/test_console_web_api.py`
- Test: `tests/test_console_status_response.py`

- [ ] **Step 1: Write failing API tests** seeding one current instance plus an older instance and asserting the response contains only `process`, `runtime_loops`, `accounts`, and `checks` for the current instance.
- [ ] **Step 2: Run tests to verify red** with `pytest -q tests/test_console_web_api.py -k 'worker_status_projects_email_health' tests/test_console_status_response.py`; expect the old `entries` shape to fail.
- [ ] **Step 3: Implement the projection** by validating safe scalar/list fields, selecting the process row, filtering on its `instance_id`, classifying loop scopes from `runtime_loop_scopes`, and updating strict Pydantic response models.
- [ ] **Step 4: Run focused backend tests** with the same command; expect all selected tests to pass.

### Task 3: Render the structured Status UI

**Files:**
- Modify: `frontend/src/api/console.ts`
- Modify: `frontend/src/api/console.test.ts`
- Modify: `frontend/src/pages/StatusPage.tsx`
- Modify: `frontend/src/pages/StatusPage.test.tsx`
- Modify: `frontend/src/styles.css`

- [ ] **Step 1: Write failing client and page tests** for strict grouped-response validation, one process summary, three loop rows, account rows, healthy checks collapsed, and degraded checks expanded.
- [ ] **Step 2: Run tests to verify red** with `npm --prefix frontend test -- --run src/api/console.test.ts src/pages/StatusPage.test.tsx`; expect the grouped contract and headings to fail.
- [ ] **Step 3: Implement typed client models and validators** for the process summary and grouped health entries with no legacy `entries` fallback.
- [ ] **Step 4: Implement the dense page layout** with summary metrics, separate tables, and a native disclosure for internal checks; add narrow-screen styles consistent with the existing Status page.
- [ ] **Step 5: Run focused frontend tests** with the same command; expect all selected tests to pass.

### Task 4: Document, build, and verify live behavior

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `docs/agent-claims.md`

- [ ] **Step 1: Run regression suites** with focused pytest, frontend Vitest, frontend typecheck/build, and Python import checks; expect zero failures.
- [ ] **Step 2: Inspect runtime safety state** for resumable claimed tasks, meeting jobs, persisted external actions, and any failed/processing backlog before service restart.
- [ ] **Step 3: Add the changelog entry** describing the corrected process/loop/readiness semantics and stale-instance filtering.
- [ ] **Step 4: Build frontend assets** with the repository build command and verify generated assets contain the new headings.
- [ ] **Step 5: Commit the feature** staging only task-owned hunks with a detailed `fix(console): structure email worker health` commit.
- [ ] **Step 6: Restart and verify** `com.ceo-agent-service.main`, confirm a new PID, inspect `/api/console/status`, verify the browser shows one process and three runtime loops, and confirm no new failed or stuck work.
- [ ] **Step 7: Release the claim** in a separate commit after all live verification evidence is captured.
