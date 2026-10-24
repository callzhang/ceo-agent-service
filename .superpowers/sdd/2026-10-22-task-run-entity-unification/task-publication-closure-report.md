# Phase 2 shared publication closure — local implementation report

Date: 2026-10-23

BASE: `4083546c048d94e55a84e27b8c8c526bc7308d15` (clean Task4 review baseline plus normal `origin/main` integration).

## Boundary and source contract

This report records a local implementation candidate only. No production database was copied or written, no process/provider/native CLI action was performed, and no push or deploy occurred. The predecessor declaration is Store `2026-10-21.1` / Email53. Its frozen schema catalog contains 505 objects and hashes to `920b7eedb6138439ab94d4fbf109e60258ce8842c212d3a2ee3eec56fb4ca290` using the exact ordered `(type,name,tbl_name,sql)` tuple representation. Target Store is `2026-10-23.1`, Email53. The paired release captures Task, Run and adopted-result source facts before the first mutation, verifies strict current manifest and integrity, persists the target marker in the same FK-off transaction, restores exact source marker timestamp/catalog/FK findings on inverse, and supports a re-forward cycle. `finalize()` delegates to loaded-pair validation. `rollback()` without forward metadata is a no-op only after exact frozen source marker/catalog, original FK result and quick-check all verify; otherwise missing inverse metadata remains an error.

## Native owner exits in this batch

The current candidate routes shared dashboard queues/details, Attention attempt details, TaskHistory API, CLI Task/Meeting lookup, History operation-log projection, Email classifier/provenance/runtime sample reads, scheduled recovery, work-summary payload maintenance, deferral, and quality-gate Action catalog checks through target owner declarations. The strict Action catalog gate remains enabled and compares `actions` against the canonical current schema manifest; the pinned source `current_action_objects()` declaration remains untouched for explicit converters. Public WeChat delivery IDs remain Action subtype identities distinct from physical Action IDs. The final direct shared owner batch also moves unresolved Reply problem list/count/per-row adopted-review checks, open OA needs-human reads, recent direct-message activity, WeChat delivery current-generation guard, and completed-message Run/Task owner lookup to native projections.

Several live API boundary defects found by actual call execution were fixed: public Reply Task association creation and provisional promotion use the actual transaction timestamps; `claim_agent_run` stores NULL in the source-absent Run error field; current reviewed candidate reads provide native `task_id` and `proposal_revision` needed by the prepared DingTalk owner; `begin_reviewed_action` uses the native candidate-attempt projection; completed-message delivery verifies Run and Task with native projections. Direct Email action retry tests now exercise the named `claim_direct_email_action` owner with the actual current `started_at`.

## Local verification

- `pytest -q tests/test_task_run_entity_schema_release.py`: 4 passed in 11.00s. Includes exact 505-object source digest, paired publish/inverse/reforward, public enqueue+claim current-fact inverse, strict source/mixed rejection and no-write failure, finalize, prepare rollback, and injected Run-converter failure after Task mutation followed by transaction rollback and verified source no-op rollback.
- `pytest -q tests/test_action_entity_callers.py tests/test_console_dashboard.py::test_active_direct_email_retry_counts_completed_attempts`: 7 passed in 9.23s.
- `pytest -q tests/test_history_task_view.py`: 3 passed in 12.37s.
- `pytest -q tests/test_console_attempt_detail_api.py::test_attempt_detail_loads_task_runs_when_attempt_has_no_run_id`: 1 passed in 5.99s.
- `pytest -q tests/test_quality_gate.py::test_quality_gate_ignores_legacy_follow_up_drafts`: 1 passed in 1.98s.
- `pytest -q tests/test_task_storage_readers.py -k fresh`: 3 passed in 3.21s after moving the authorizer behind current schema initialization, allowing the retained WorkSummary metadata owner while keeping `payload_json` forbidden during the History read, and narrowing terminal compaction/recovery/list queries to Task plus `work_summary_inputs` metadata.
- `pytest -q tests/test_task_storage_readers.py -k migrated`: 3 setup errors in 15.03s. The fixture still builds the older source13/Store17 chain; the new explicit paired release accepts the exact frozen 2026-10-21.1 predecessor, so this fixture now reaches `unsupported current Action source schema; explicit paired release required`. The old schema-based migration fixture must be replaced with an explicit frozen source creator and declared source path; it remains an open test gate.
- Earlier broad selected-console/quality-gate/deferral groups have known legacy schema fixture failures and are not reported as green. A previously started four-file combined reader/WeChat/Attention/History command printed partial `EEEEEE............FF`; final output was not captured, so it is not counted as validation.
- `git diff --check` and scoped `py_compile` passed for modified runtime/release/test modules at the time of report.

## Remaining release gates

The complete runtime source-exit inventory and focused current-target reader suite must be closed before claiming a publishable constructor. The exact TaskHistory and shared caller changes still require full BASE-to-HEAD SPEC and QUALITY review by the root agent. Later acceptance remains a fresh production-derived disposable backup, exact source schema/data read, native public API and current-fact inverse/reforward, pinned predecessor cold-open plus harmless old public mutation, real Luna/high native CLI with deterministic fake effect and persisted ACK/crash recovery, current browser UI, and same-source packed net-size comparison. No production release, deployment or post-deploy readback is claimed here.
