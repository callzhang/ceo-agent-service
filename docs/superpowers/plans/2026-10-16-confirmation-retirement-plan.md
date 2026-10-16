# Retire the legacy Workbench confirmation feature

> **For agentic workers:** REQUIRED SUB-SKILL: subagent-driven-development. Complete canonical proposals and regressions precede application; fresh SPEC then QUALITY are required before and after selection.

**Goal:** Fulfil the approved design cancellation of `workbench_confirmations` and its old execution/API/UI machinery, preserving native Agent CLI approval and ordinary turn lifecycle, leases, recovery, stops, artifacts and input privacy.

**Architecture:** Remove the old feature across current Store/schema/Workbench/runtime/API/frontend code together. Preserve historical source factories. The paired unpublished Store20/Email52 release binds original confirmation declarations and confirms that the canceled feature has no source rows, waiting turns or confirmation events. Forward converts the current Tasks layout and retires the empty native table inside the existing transaction; inverse recreates its original empty declarations from metadata and restores the original Tasks layout. No receipt snapshot, synthetic resolution, replacement approval gate or extra schema version is introduced.

**Tech Stack:** Pinned Python3.13.12/SQLite3.50.4, current native runtime contracts, existing TypeScript/Vite/Vitest, disposable DB/code copies and immutable production predecessor.

## Complete code units before application

- [x] Root Store/schema/facade proposal: remove current confirmation creator/recognizer/required manifest/startup reconciliation, all old Workbench Store CRUD/recovery and ordinary-path joins; current timeline tuple becomes six elements without confirmations, stats/cursors lose only confirmation fields. Remove current waiting state from Task20 CHECK/active index; retain all other source fields and native transaction statements. Historical `task_storage`/`workbench_storage` stay exact.
- [x] Executor/models/runtime/native-events proposal: remove only local confirmation execution/proposer/lease/receipt branches and the current waiting state/event; ordinary native execution/recovery/stop/lease/shutdown remain. Keep native `agent_cli` and service runtime behavior unchanged.
- [x] API/frontend proposal: remove the old confirm/cancel endpoints (ordinary unmatched-route behavior: standalone router404; integrated SPA catch-all405), public confirmation DTO/list/pagination/SSE/statistics fields, read-only card and obsolete client exports. Keep every other field, ownership rule and pagination behavior. No compatibility arrays or stubs.
- [x] Root pure release and paired coordinator: exact original empty declaration metadata; current source data checked again immediately before retirement; forward/inverse own no commits. Capture original source closure before target-only CHECK changes can reject an unexpected waiting turn. All facts other than the explicitly canceled empty feature remain current-fact inverse inputs.
- [x] Complete focused regression and installed-copy checker update. The current frozen source has0confirmation rows/0waiting turns/0confirmation_required events; these are evidence at that snapshot, not a guarantee about current production. Recheck latest source before final publication.

## Required evidence

First demonstrate the old code still creates the table, exposes the old DTO/resources/methods and accepts the old current waiting/event contract. The new combined candidate must remove those paths and reject the retired current state/event without modifying native CLI approval configuration. Execute real ordinary queued→running→completed/failed/stopped/recovered flows and API/frontend timeline/diagnostics/pagination; ensure no query references the retired table. Both fresh construction and nonempty copied source must yield54ordinary tables, still an intermediate result.

The source-bound conversion must bind actual/fresh table/index/trigger/FK SQL, reject an unexpected nonempty canceled-feature closure before its DDL, preserve transaction rollback and restore the original empty table/guards on inverse. Native ordinary tasks written after migration must survive inverse and a genuine production predecessor read/write/reforward. Do not reconstruct a historical business-row snapshot. A copied current turn with unrelated event/session/artifact data is a required positive control, not permission to normalize it.

Run only focused backend and frontend tests. Explicitly retire tests of the approved canceled feature; convert old physical SQL fixtures without changing their business assertions. Existing Task20 old-fixture gaps remain visible until converted. Preserve exact canonical hashes, concise counts and typed-fact comparisons; delete owned temporary copies, keep the original active frozen input. No main merge, push, production migration, restart, deploy or real outbound sends. Final55→11 remainder/native CLI/UI/performance/CI/human-approved publication remains open.

## Selected checkpoint

Selected exact41-file candidate in `0ca8454b9e22449e89245999486dd58021c73a24`. Canonical SPEC and QUALITY approved; mechanical writer397 focused tests and21-file Ruff passed. Root actual-copy preflight315 passed, then116→54→116→54 completed382.274s with genuine44ee predecessor, current typed writes, original declarations/markers/ledger/counters restored, original input SHA970a909a9cfcb4fe604d2111d5572560d4744978df20fb317c4e433059566320 unchanged and owned temporary copies removed. Whole API52 and frontend135/build passed on byte-identical candidate. Retained Store module47pass3known original unsupported hybrid source13 fixture failures; not wholly green. Fresh applied SPEC184 plus three54-table constructor orders and applied QUALITY54/Ruff13 approved the exact installed candidate. Final11/nativeCLI/UI/performance/CI/publication remain separate open work.

The proposal builders and cumulative preflight are historical proofs against base `7111ea47546ac7cb242ff7267993868eacec6561`; they must be run on that archived base, not reapply their patches over the selected candidate. Current installed-copy verification uses `scripts/validate_task20_runtime_copy.py` directly. The selected source is bound by the41-file mechanical manifest.
