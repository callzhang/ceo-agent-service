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

## Delivery substep after checkpoint `e2b2ad93`

- The `ready_to_send` bulk and single claims now select native Meeting Tasks by due time and canonical Task ID, reserve them with Task CAS, and retain the existing `meeting_alignment_delivery_claims` Action token against the native Task ID. Release checks the exact token and lease timestamp, then unlocks by CAS. Retry increments the native Task attempt count and preserves `ready_to_send`; startup reset clears native delivery locks and the existing delivery claim Actions. Analysis claims remain independent and never select `ready_to_send`.
- Failed-delivery requeue reads the persisted `TaskBusinessKey` through `get_meeting_delivery_key`, checks the matching primary receipt and native Task lease/status, then reopens only the same reviewed unsent result. It does not reconstruct a key from Task ID. Confirmed-delivery restore likewise requires the exact saved key and exact prepared body, primary receipt, target binding, no sensitive receipt, and current failed Task/result facts before writing the existing result owner and transitioning to `ready_to_send`. New native Meeting Tasks initialize the same result defaults expected by those CAS checks (`send_result_json='{}'`, empty reviewed fields, and calendar `not_started`/`{}`); updates also materialize that default row when needed.
- Meeting terminal-input compaction now joins native `runtime_attempts.parent_id` to native `runs.id` with Meeting `run_provenance`, then compare-and-swaps the typed `source_json` Task parameter only when the terminal status and original parameter value still match. Existing provenance proof and compaction rules in `meeting_consumer_source` remain unchanged.
- Added an actual `deliver_meeting_alignment` + `ServiceMessageSender` test using the assembled target Store and an idempotent substitute DWS adapter. An injected receipt-insert interruption leaves the provider effect and prepared Action but no receipt; retry uses the identical UUID derived from the stored key, completes one durable receipt, and replay does not call the provider again. Native delivery claim/release/retry/reset and failed requeue tests also assert Task IDs and identity preservation.

Evidence: `pytest -q --basetemp=/tmp/task-run-entity-phase2-tests/task2d2b-delivery-akamjics tests/test_meeting_task_runtime_native.py` — 5 passed, including exact-key confirmed restore and negative wrong-key checks. Attempted the adjacent pre-cutover suites `tests/test_meeting_alignment_delivery.py tests/test_meeting_confirmed_recovery.py`: 53 passed, 28 failed because those fixtures instantiate the source Store schema (`no such column: kind`) or an old in-memory sender contract (`operation` keyword mismatch); they are not assembled-target acceptance tests. No claim is made that those source fixtures validate the native Store methods. The earlier source SQL RED remains recorded above. Candidate remains unactivated.

Still pending from the original scope: Meeting analysis startup reset, replay/rerun, baseline, ASR recovery, recurring targets and non-delivery recovery/liveness checks still contain source ownership; shared dispatcher and all-family dashboard/History readers remain exact cross-family dependencies. No provider, constructor, production, main, push, deploy, or full-suite work occurred.
