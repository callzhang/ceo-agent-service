# Task2D2B Meeting runtime cutover report

Status: partial local implementation at base `fd3974dbc31a363d0d4e4bdd7a55caf217e17dd7`. No constructor activation, source migration, real provider, CLI runtime, production, main checkout, push, deploy, or full suite was used. Shared C2A and Task2D0 appendices were preserved.

## Implemented in this candidate

- `AutoReplyStore.upsert_meeting_alignment_job` inserts a native `tasks(kind='meeting_alignment')` row with the source request in typed native parameters and associates `meeting-alignment:{canonical task id}:{meeting_id}` using `get_meeting_delivery_key` ownership in the same write transaction. Existing Meeting identity returns the existing canonical Task ID; only the source `waiting` upsert refreshes request fields.
- The Meeting DTO reader loads native Task lifecycle fields, parameters, and the `task_results(result_kind='meeting_alignment')` row. `get_meeting_delivery_key` reads the accepted stored key by canonical Task ID. Segment attachment, title/meeting lookup, sent listing, ended-range listing, and the analysis claim now query native Task ownership. Analysis eligibility/order remains `waiting`/`pending`/`retry`, due by `eligible_at` and `available_at`, with incremented attempts and a processing lock; `ready_to_send` is not in this query.
- `update_meeting_alignment_job` writes lifecycle fields through native Task CAS, request fields through native parameters, and output fields through the Meeting TaskResult owner. Meeting analysis now calls `claim_professional_run` and `_record_agent_run` calls `finish_professional_run` with the current runner session/transcript bounds.
- Meeting Run listing and detail lookup use native `runs.id`, `runs.task_id`, `run_provenance`, and native trajectory references. Meeting HTML and API detail readers resolve the native Run then hydrate the Meeting Task from its native parent. The Meeting Run DTO now separates `task_id` from optional source provenance `job_id`; missing native parents produce the existing unavailable/404 detail behavior.
- Sender/reconciliation call sites in `meeting_alignment.py` obtain the primary key from the persisted TaskBusinessKey, preserving the exact sensitive suffix and prepared Action identity.

## Focused evidence

`pytest -q tests/test_meeting_task_runtime_native.py` — 1 passed against `task_run_entity_assembly_objects()` and actual `AutoReplyStore` methods. The fixture creates an unrelated Task first, proving the Meeting's canonical Task ID is 2; it then verifies the accepted saved key, native DTO, due analysis claim and attempt count, professional Run claim/finish/list, and TaskResult write/read.

`python -m compileall -q app/store.py app/meeting_alignment.py app/audit_web.py app/web_api/registration.py app/meeting_alignment_models.py` — passed.

`ruff check app/store.py app/meeting_alignment.py app/audit_web.py app/web_api/registration.py app/meeting_alignment_models.py tests/test_meeting_task_runtime_native.py` — passed.

The focused test also runs the actual pre-cutover `MEETING_JOB_SQL` reader against the complete assembled target and captures its RED: `sqlite3.OperationalError: no such column: legacy_key`. The native Store path then passes on the same database. A sender substitute-provider interruption/readback scenario has not been completed; the test does not prove provider-effect reconciliation.

## Still source-backed and blocking complete cutover

The runtime switch is not complete. The following current call paths still use source projections or source IDs and need the next bounded implementation/review:

- delivery single/bulk claims, release, reset, retry and sender recovery/receipt reconciliation Store methods; their Action claim rows and exact primary/sensitive saved-key checks still need assembled-target end-to-end coverage;
- analysis startup reset, replay/rerun, baseline, ASR recovery, consumer-input compaction, recurring targets, recovery fences and several Meeting-specific checks still contain `record_type`, `legacy_key`, `MEETING_JOB_SQL`, `MEETING_LOGICAL_RUN_SQL`, source claim projections, or source runtime-attempt projections;
- generic dispatcher claim/renew/release/error/recovery is shared and remains an exact pending Task2 unit. This candidate does not claim Meeting dispatcher readiness or alter the other adapters;
- `AutoReplyStore._operation_logs_base_query` and shared History/Attention/dashboard readers still emit all-family source IDs. Meeting cards/details were changed to native Run/Task readers, but the page's source SQL is pending, so no full Meeting History page pass is claimed;
- current production construction still uses the source Store. No constructor/bootstrap activation was attempted.

Accordingly, this is implementation progress, not a complete Meeting Task/Run/delivery cutover or a deployable runtime. The next unit should finish delivery/recovery callers and their fake-provider effect-reconciliation tests, then complete the shared dispatcher and all-family reader dependency before constructor activation.
