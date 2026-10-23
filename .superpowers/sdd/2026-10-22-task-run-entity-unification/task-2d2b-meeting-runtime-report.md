# Task2D2B Meeting runtime cutover report

Status: DONE for the complete Meeting-specific Task2D2B implementation, from base `fd3974dbc31a363d0d4e4bdd7a55caf217e17dd7` through the final slice described below. This is not a claim that the entire Task2D2B release is ready: exact shared dispatcher, all-family dashboard/History, generic search, and scheduled-consumer source dependencies remain for the coordinated follow-on and root review. No constructor activation, source migration, real provider, CLI runtime, production, main checkout, push, deploy, or full suite was used. Shared C2A and Task2D0 report appendices were preserved.

## Implemented in this candidate

- `AutoReplyStore.upsert_meeting_alignment_job` inserts a native `tasks(kind='meeting_alignment')` row with the source request in typed native parameters and associates `meeting-alignment:{canonical task id}:{meeting_id}` using `get_meeting_delivery_key` ownership in the same write transaction. Existing Meeting identity returns the existing canonical Task ID; only the source `waiting` upsert refreshes request fields.
- The Meeting DTO reader loads native Task lifecycle fields, parameters, and the `task_results(result_kind='meeting_alignment')` row. `get_meeting_delivery_key` reads the accepted stored key by canonical Task ID. Segment attachment, title/meeting lookup, sent listing, ended-range listing, and the analysis claim now query native Task ownership. Analysis eligibility/order remains `waiting`/`pending`/`retry`, due by `eligible_at` and `available_at`, with incremented attempts and a processing lock; `ready_to_send` is not in this query.
- `update_meeting_alignment_job` writes lifecycle fields through native Task CAS, request fields through native parameters, and output fields through the Meeting TaskResult owner. Meeting analysis now calls `claim_professional_run` and `_record_agent_run` calls `finish_professional_run` with the current runner session/transcript bounds.
- Meeting Run listing and detail lookup use native `runs.id`, `runs.task_id`, `run_provenance`, and native trajectory references. Meeting HTML and API detail readers resolve the native Run then hydrate the Meeting Task from its native parent. The Meeting Run DTO now separates `task_id` from optional source provenance `job_id`; missing native parents produce the existing unavailable/404 detail behavior.
- Sender/reconciliation call sites in `meeting_alignment.py` obtain the primary key from the persisted TaskBusinessKey, preserving the exact sensitive suffix and prepared Action identity.

## Initial checkpoint evidence (superseded by final slice below)

`pytest -q tests/test_meeting_task_runtime_native.py` — 1 passed against `task_run_entity_assembly_objects()` and actual `AutoReplyStore` methods. The fixture creates an unrelated Task first, proving the Meeting's canonical Task ID is 2; it then verifies the accepted saved key, native DTO, due analysis claim and attempt count, professional Run claim/finish/list, and TaskResult write/read.

`python -m compileall -q app/store.py app/meeting_alignment.py app/audit_web.py app/web_api/registration.py app/meeting_alignment_models.py` — passed.

`ruff check app/store.py app/meeting_alignment.py app/audit_web.py app/web_api/registration.py app/meeting_alignment_models.py tests/test_meeting_task_runtime_native.py` — passed.

The focused test also runs the actual pre-cutover `MEETING_JOB_SQL` reader against the complete assembled target and captures its RED: `sqlite3.OperationalError: no such column: legacy_key`. The native Store path then passes on the same database. A sender substitute-provider interruption/readback scenario has not been completed; the test does not prove provider-effect reconciliation.

## Checkpoint state before delivery work (historical)

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

At the delivery checkpoint, Meeting startup reset, replay/rerun, baseline, ASR recovery, recurring targets and non-delivery recovery/liveness checks were still open. The final slice below closes those Meeting-specific items and supersedes this checkpoint note. The shared dispatcher and all-family dashboard/History readers remain exact cross-family dependencies. No provider, constructor, production, main, push, deploy, or full-suite work occurred.


## Final Meeting-specific slice from checkpoint `fab661be6`

- Converted Meeting startup reset, replay/rerun, baseline restore, ASR attachment, recurring-target reads, capacity deferral, recovery-fence checks, and terminal Task/Run/RuntimeAttempt ownership to native Task/TaskResult/Run/Attempt IDs. Analysis startup recovery performs Task CAS and closes only running Meeting Runs with their native RuntimeAttempts; effect-bearing or saved-key protected results remain fenced by their existing receipt/Action checks. Calendar result fields retain their separate native TaskResult ownership.
- Converted Meeting Memory event parent validation and operational queries to canonical Meeting Task IDs and native Meeting results. This is the event's existing operational foreign key; source provenance remains separate.
- Similar-Meeting search now has a native Meeting-only path using Meeting Task fields, native Run IDs, and `native_run_ref`; Meeting execution no longer writes a source search-index record. The generic search path remains for non-Meeting consumers. Removed the unused `_meeting_alignment_job_from_row` hydrator that translated `legacy_key` into the domain ID. `_meeting_alignment_run_from_row` remains used by actual Run list/get paths and maps native `task_id` directly; it preserves `run_provenance.job_id` only when supplied by provenance and never aliases Task ID into it.
- Added assembled-target regressions for actual Meeting production/source validation through professional Run and TaskResult, startup reset with/without an effect-start timestamp, replay/rerun/baseline and calendar preservation, ASR recovery, native search, and terminal compaction no-op behavior when no scheduled-consumer context exists. The earlier actual sender substitute test remains in the same focused file.

Validation: `pytest -q --basetemp=/tmp/task-run-entity-phase2-tests/task2d2b-final-o6bge964 tests/test_meeting_task_runtime_native.py` — 11 passed. `python -m compileall -q app/store.py app/meeting_alignment.py tests/test_meeting_task_runtime_native.py`, `ruff check app/store.py app/meeting_alignment.py tests/test_meeting_task_runtime_native.py`, and `git diff --check` — passed. The suite captures the pre-cutover `MEETING_JOB_SQL` RED (`no such column: legacy_key`) against the complete target assembly, then exercises the actual native Store methods.

## Exact remaining shared dependencies for root review

These are not Meeting-owned paths in this slice and were left source-shaped rather than introducing compatibility facades:

- `recover_expired_terminal_task_runtime_attempts` around `app/store.py:8174` is shared all-family recovery; its Meeting participation still traverses source projections and is pending the dispatcher/recovery unit.
- `_history_items_query` around `app/store.py:21768-21790` includes Meeting's source Run/Task branch in the all-family History union.
- `search_codex_sessions` around `app/store.py:22512-22513` retains source Meeting joins in the generic source-index branch. The Meeting consumer uses the native-only branch; other generic consumers still need a coordinated migration.
- `_list_failed_operation_logs_with_count` around `app/store.py:25855-25856` and `_operation_logs_base_query` around `app/store.py:26532-26533` retain Meeting source projections for all-family operation logs/History.
- `compact_meeting_consumer_input` now reads native Meeting Task/Run/RuntimeAttempt parents, but if the typed request contains `scheduled_consumer`, `scheduled_task_run_id` is still resolved by the cross-domain `get_scheduled_task_run` source-shaped reader. Complete assembled-target testing demonstrated `sqlite3.OperationalError: no such column: legacy_key` for that external source object. The current send-path fixture has no such context, so it verifies the actual compaction call safely no-ops and preserves the input; context-bearing compaction remains an explicit scheduled-run metadata migration dependency.
- `MEETING_JOB_SQL` and `MEETING_LOGICAL_RUN_SQL` constants remain only for the shared readers above (and the frozen RED test). Actual Meeting producers, lifecycle/result writers, delivery/recovery methods, native Run readers, and Meeting-specific search no longer depend on these source projections.

The full assembled constructor remains unactivated, and no production behavior is claimed. The exact source projections listed here need the common all-family follow-on and independent review; this report closes the Meeting-specific implementation slice only.
