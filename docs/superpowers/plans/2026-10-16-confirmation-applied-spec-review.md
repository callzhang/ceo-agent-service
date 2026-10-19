# Confirmation retirement: fresh applied SPEC review

Status: **APPLIED SPEC APPROVE** for selected application commit `0ca8454b9e22449e89245999486dd58021c73a24`.

This is a bounded review of the applied 41-file confirmation retirement against approved design `docs/superpowers/specs/2026-10-11-database-redesign-design.md:108`, the narrow retirement plan, selection manifest, and prior canonical SPEC/QUALITY reviews. No actionable application or specification mismatch was found. This approval does not establish production readiness or complete the broader database redesign.

## Exact selection evidence

Fresh independent SHA256 verification found all **41 manifest after hashes** identical in both the working tree and the selected commit, including the removed ConfirmationCard path. All **13 proposal patch hashes**, the canonical-to-installed regression copy, and **five unchanged source/native pins** matched. The selected commit changes exactly those 41 paths plus its implementation claim row in `docs/agent-claims.md`.

The five unchanged files are `app/task_storage.py`, `app/workbench_storage.py`, `app/agent_cli.py`, `app/workbench/service_runtime.py`, and `app/native_cli_metadata.py`. This proves the historical source factories and native CLI/configuration adapter bytes were preserved; no real native CLI/provider execution is claimed.

During this review, HEAD advanced to documentation-only commit `d363e7d847b1b635b3a767c8446a506425d70832`. Its 12 paths record proposal evidence and claims. A fresh diff against the selected application commit showed no changes in app, frontend/src, tests, the installed copy checker, or the two behavior documents. Approval remains bound to application commit `0ca8454b9e22449e89245999486dd58021c73a24` and the manifest after hashes.

## Applied contract findings

- Current Store schema creation/recognition removes the confirmation table and its required index, rejects its retired native name, and removes the old startup reconciliation. Current Tasks20 CHECK and active-turn index omit only `waiting_confirmation`. Historical source factories remain exact.
- Current Workbench models, executor, Store, runtime events, terminal native-event cache selection, API and frontend consistently remove the old confirmation resource and state. The retained turn states are queued, running, completed, stopped and failed. Ordinary native execution, two-worker claims, lease/recovery/stop/shutdown, artifacts, input validation and private resume context retain their existing paths.
- The timeline contract is exactly `(task, turns, events, attachments, artifacts, page)`. DTOs, cursors, statistics, event projections, frontend types and client resources consistently remove confirmation fields. The deleted confirm/cancel endpoints are absent from registered routes/OpenAPI. Standalone404 and the existing integrated SPA catch-all405 are consistent with the approved removal; no replacement endpoint or fallback was introduced.
- A repository-wide current app/frontend search found no remaining references to removed Workbench confirmation types, methods or cursor fields. Remaining confirmation-required handling in AutoReplyStore belongs to separate reply-run/provider result behavior. Remaining migration/source references are intentional. Older Phase4C documentation sections describe historical source releases; the current introductory contract in both behavior documents explicitly describes the removal and intermediate54-table state.
- The pure migration captures the exact empty source declaration before Tasks20 conversion, including indexes/triggers/FKs through fixed source declaration hash `e215afb029d2993691a8585b07a614667aca8cddedd8dd21b9b0a3f3335d982d`. It checks source table rows, waiting turns and confirmation events, rechecks the current feature closure before retirement, and requires the existing caller-owned FK-off transaction. Inverse recreates only the original empty declaration before Tasks20 restoration. It stores no business-row or receipt snapshot and introduces no new approval mechanism or schema version.
- Paired source preparation/recheck, target verification, forward and inverse invoke these helpers in the correct order. The updated target declaration hash `1afb0380b4280bc061eea303bfbfc43262c0768aad47ead8f5e2d60dbc2da9ff` matches the current status/index declaration. Unrelated facts remain current inverse inputs.

## Fresh independent execution

Pinned interpreter: `/Users/derek/Projects/ceo-agent-service/.worktrees/task-first-tasks/.venv/bin/python`, with `-B` and `PYTHONDONTWRITEBYTECODE=1`. No cache provider was used; pytest data and constructor databases were confined to one owned temporary directory.

The following six selected test modules ran together: `test_confirmation_schema_release.py`, `test_task20_current_schema.py`, `test_workbench_confirmation_retirement.py`, `test_workbench_confirmation_retirement_api.py`, `test_workbench_runtime.py`, and `test_workbench_executor.py`.

Result: **184 passed in17.72s**, with one existing Starlette/anyio BlockingPortal deprecation warning. This includes empty-feature refusal and late-write checks, transaction ownership/rollback/exact inverse, current schema semantics, removed resources/states/events, six-element API timeline/cursors, native review-payload preservation, ordinary runtime and executor lifecycle, and recovery lease/time/error behavior.

Three separate subprocesses then created new databases using these constructor orders:

| First constructor | Remaining constructors | Observed result |
| --- | --- | --- |
| AutoReplyStore | EmailStore, WorkbenchStore |54 ordinary tables, retired table absent, current Tasks20, clean FK check |
| EmailStore | WorkbenchStore, AutoReplyStore |54 ordinary tables, retired table absent, current Tasks20, clean FK check |
| WorkbenchStore | AutoReplyStore, EmailStore |54 ordinary tables, retired table absent, current Tasks20, clean FK check |

Every case also reopened through WorkbenchStore, created an ordinary fake-runtime task and turn without starting a runtime, verified a six-element timeline, and verified there were no current SQL declarations containing the retired table name or waiting state. Total test-plus-constructor elapsed time was21.686s.

Owned temporary directory `/tmp/confirmation-applied-spec-l68hn3hj` was removed by its context manager and its absence was verified. No temporary review artifact remains.

## Prior receipt and remaining boundaries

The inspected `2026-10-16-confirmation-coupled-preflight-result.json` reports the preselection cumulative candidate at base commit `7111ea47546ac7cb242ff7267993868eacec6561`: source116 → target54 → restored116, followed by genuine predecessor cold writes and cold reforward. It records exact restored source declarations, retained current source allocators, unchanged original rows outside the three proof-owned witness exclusions, public State/Email/WeChat writes surviving inverse/reforward, and315 focused migration cases. Its internal SQLite backup SHA256 is `2584df8d2c60f102f1238e9932b1941fcb5a2f8efcc8a2e051a75a0bab2ecaa7`; that is distinct from the root-owned frozen input file hash. This reviewer inspected the receipt and matching installed application/checker hashes, but did not rerun the copy workflow or independently rehash the root-owned input.

The mechanical owner reported397 selected focused passes and Ruff on21 changed Python files. Prior full API52 passes and frontend135 passes/build are preserved acceptance evidence for these exact selected bytes, not executions repeated by this reviewer. The retained Store module's three unchanged unsupported source13 hybrid-fixture failures remain explicitly documented; they were not normalized, skipped or treated as new regressions.

The selected commit's three extra blank lines at EOF in executor/models/Store originate in the accepted canonical patches. They are cosmetic and do not alter this SPEC result; this reviewer made no application edits.

Fresh applied QUALITY remains a separate next review. Latest-production source eligibility, final ten core tables plus the Email migration ledger, native CLI/fake-provider/UI/performance/CI acceptance, and human-approved publication remain open. This review performed no full suite, full API/frontend rerun, production database operation, real native CLI/provider/network/outbound call, merge, push, restart or deploy. Only this review file and this reviewer's claim row were edited during verification. After approval, root explicitly authorized a separate documentation-only commit containing this review and the HEAD claim content plus this reviewer's row; no source or test changes are included.
