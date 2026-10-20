# Dashboard Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Implement the approved Dashboard with one Service row, Failed/Active/Queued tabs and retained History.

**Architecture:** Add a read-only Dashboard queue projection and endpoint in app/web_api/dashboard.py; register in app/audit_web.py. Reuse existing Attention and Status APIs. Embed the existing History content under a Dashboard component, without duplicating its fetch/filter logic. Preserve all runtime and Attention semantics.

**Tech Stack:** Python/FastAPI/SQLite, React/TypeScript, Vitest/Testing Library.

## Task 1: Read-only queue projection

- [x] Add tests/test_console_dashboard.py covering initial queue, retry ordinals, current leases/generations, superseded objects, expired leases, pagination, metadata-only reads and read-only behavior.
- [x] Run the tests before implementation; missing Dashboard API/projection must fail.
- [x] Implement app/web_api/dashboard.py using current Task/Run source projections. Use current metadata only, no native transcript reads. Register endpoint in app/audit_web.py.
- [x] Run `python -m pytest tests/test_console_dashboard.py -q`; all focused assertions must pass.

## Task 2: Dashboard presentation

- [x] Add frontend/src/pages/DashboardPage.test.tsx and frontend/src/api/dashboard.test.ts. Cover Failed-first, empty Failed -> Active, explicit user tab preserved after refresh, queue pagination/retry labels, failures/loading and detail links.
- [x] Run focused tests and observe missing Dashboard failures.
- [x] Implement DashboardPage.tsx, api/dashboard.ts, Dashboard CSS; reuse AttentionPanel with optional chrome/refresh integration and the embedded HistoryPage. Change GlobalNav label and router at /history; preserve deep links.
- [x] Run Dashboard, API, navigation, router, Attention and History tests, and `npm run build`.

## Task 3: Review, release and live verification

- [x] Update docs/architecture.md and docs/runtime-mechanism.md with the Dashboard contract in the same feature commit.
- [x] Review the complete diff, check git diff --check and run focused backend/frontend coverage.
- [x] Verify desktop/mobile rendering and tab/detail behavior against the approved mockup.
- [x] Commit only this worktree's feature, reconcile remote increments without rewriting others, push and use `python -m app.deploy`.
- [x] Read back deployed commit/PID/healthz/queue/Attention/History before reporting deployed.

## Execution ledger

Worktree created from origin/main; unrelated shared-tree edits are excluded.
The user approved the mockup and explicitly requested development; no additional
design approval or deployment confirmation is needed.


Validation before release: 12 focused backend tests and 95 frontend tests passed;
TypeScript/Vite build, scoped Ruff and diff checks passed. Independent review
found no Critical issues. Corrected reply detail identity, meeting delivery wait
and eligible/backoff time, source-specific retry counters, Workbench runtime
ownership/title/start, refresh detail persistence and shrinking-page pagination;
regressions cover these behaviors. Independent OKR/WeChat import runtime metadata
is included, with unknown counters kept unknown. Browser plugin unavailable;
used built-in CUA browser for a rendered 1280px desktop and 390px mobile check:
Failed/Active/Queued switching, original issue statuses, History presence and no
app error overlay; console had no warnings/errors. The temporary QA server served
the built frontend and read queue metadata from the live database in read-only
mode; existing Console GETs came from the running service. No provider action.
Queued IDs were moved into details; absent eligibility no longer shows creation
as execution time. Mobile health remains available under system details to keep
the Service row on one line. This is an intentional compact layout difference.

Release follow-up: source inspection confirmed email classifier task owners and
native router owners are independent (email-classifier PID versus routed UUID).
The earlier same-owner fixture hid missing model/start metadata. Reproduced with
different valid owners, then changed the read association to workload identity
plus both valid leases, the same contract used for Workbench. No worker change.

Bound Consumer/Audit native attempts use their current Task lease rather than a
standalone router lease. A fixture with an empty native lease reproduced missing
Agent/model metadata; the read now accepts that exact current parent only while
its Task lease is valid. An expired-Task assertion rejects the same running row.


Release readback (2026-10-20 22:35 UTC): the initial waiting CLI was interrupted
before stop/backup/checkout (its stack remained wait_until_quiet); a separate
release operation then published the latest pushed version. Persisted receipt
`deploy-419785bf-6b76-4d95-be84-1d8a81935acf` is succeeded, original b6f87c14,
target and installed 763caecd3, empty error. A fresh standard `python -m app.deploy`
returned already current at 763caecd. Main service PID 75708 and web listener
75712 replaced the old PIDs. healthz, Dashboard queue API, Attention and History
returned 200; the queue snapshot had active4/queued18, Attention2 and History36567
(they are changing operational snapshots, not acceptance constants). Existing
system health remained degraded due reply recovery-lease violations; that is not
represented as overall healthy. The live /history UI was separately opened in
CUA, selected Failed first, switched Active, expanded a scheduled row and retained
both selection and detail after refresh. Desktop, 390px and 320px QA confirmed no
horizontal overflow, no framework overlay and no browser app errors. Temporary
QA server/scripts/dependency symlink were removed; delivered screenshots remain
under the task visualization folder. No maintenance approval is needed anymore.
