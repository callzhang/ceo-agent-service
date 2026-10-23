# Task2D8B Feedback completion report

Base: `770cac667f0f751c69e54306df387bdf669302e4`.

Base-to-final implementation range: `770cac667f0f751c69e54306df387bdf669302e4`..`cf304daba5e9d5fc00a8b44f62845bc5a95408a2`. Writer/docs commit: `9251a0205263d9b9c84a93c96b01bc295bbf7e17`; native scope coverage commit: `cf304daba5e9d5fc00a8b44f62845bc5a95408a2`. This report is committed in a report-only follow-up commit. No push, upstream merge, constructor activation, provider/native CLI use, production write, or deploy.

## Native ownership implemented

The bounded association/evidence/decision/resolve/reopen APIs and the validators/readers reached from those operations now use the assembled Task/Feedback owners. `tasks(kind='feedback_processing')` owns item status and item lifecycle timestamps. `feedback_processing_items` owns the item's current association and evidence/detail fields. `feedback_processing_rounds` owns round status, association/evidence snapshots, resolution/reopen metadata and current scope receipts. `feedback_processing_batches` owns batch lifecycle; `feedback_iteration_decisions` and its existing Action decision-item rows own typed decision membership; existing Action transitions and Feedback source events remain in their prior owners. Pair updates and existing event/transition writes remain in the same immediate transaction.

The changed Store paths are `record_feedback_iteration_decision`, `list_feedback_iteration_decisions`, `reopen_feedback_processing_item`, `associate_feedback_processing_turn`, `patch_feedback_processing_item_evidence`, `_resolution_feedback_iteration_decision`, `resolve_feedback_processing_batch`, and the batch/round/current-lineage/decision-membership validators reached by them. These paths no longer use the legacy `task12_projection`, `task12_set_parts`, or `task12_insert_parts` mixed-Task helpers. The unactivated constructor remains untouched. Receipt validation rules, receipt versions, capability behavior and Skill/runtime-config policy are unchanged.

The receipt converter maps only `decision_id` and `evidence.associations[*].workbench_task_id` / `workbench_turn_id`. Other receipt JSON, independent `attempt_id` / `agent_run_id` facts, non-UTF8 or malformed values, unresolved references and the input SQLite TEXT/BLOB storage class are preserved. Inverse conversion uses existing reference-exception metadata only while the current native value still equals the captured unresolved value; current edits are converted through the actual owner index and are not overwritten by capture metadata.

## Verification

Passing focused commands:

- `pytest -q tests/test_store_task_entity_creator.py -k native_feedback` — 10 passed, 17 deselected. Covers native claim/association/evidence/typed decision/resolve/idempotent repeat/reopen/new batch and round; capability/create refusal and competing claim behavior; actual Skill-only, runtime-config, mixed and needs-human scope behavior.
- `pytest -q tests/test_task_entity_schema.py -k 'feedback_'` — 8 passed, 27 deselected.
- `pytest -q tests/test_task_entity_schema.py -k 'feedback_workbench_and_current_round_refs or native_feedback_claim_after_converted_reopen or new_native_feedback_resolution_receipt'` — 3 passed, 32 deselected. This includes historical source decision 227→native 1227 mapping, source BLOB preservation, inverse/reforward, current edits, and a converted-reopen/native-claim cycle.
- `pytest -q tests/test_task_entity_schema.py -k new_native_feedback_resolution_receipt_survives_inverse_reforward_cycles` — 1 passed, 34 deselected. It writes a new complete native resolution receipt, inverses to the source projection, calls the existing `_scope_resolution_receipt` parser, verifies the restored source decision row and Workbench public references plus independent attempt/run numbers, then forwards and inverses a second time.
- `pytest -q tests/test_task_entity_migration.py` — 4 passed.
- `ruff check app/store.py app/task_entity_migration.py app/task_entity_schema.py tests/test_store_task_entity_creator.py tests/test_task_entity_schema.py`; `python -m compileall -q app/store.py app/task_entity_migration.py app/task_entity_schema.py`; `git diff --check` — all passed.

The new current-receipt integration fixture initially blocked because SQLite `backup()` was invoked while the source migration connection still held its write transaction. The fixture now commits completed migration/assembly writes before backup. Parent, with human authorization, terminated the old blocked focused process PID 61120; it is absent. The repaired focused test above passed on a fresh pytest process.

An accidental broader command, `pytest -q tests/test_feedback_processing.py`, was started as PID 58090. Its first captured output was `F`; its final pytest summary was not captured, so that run is unclassified and is not counted as verification. A SIGINT request targeting PID 58090 was rejected by the execution tool with `approval required by policy, but AskForApproval is set to Never`. The process was absent on the later read-only `ps` check. No retry or alternate process-control path was used.

Native scope resolution is now exercised for `skill_only`, `runtime_config`, and `mixed` using the existing managed Skill/revision, runtime config, and `record_runtime_skill_load` APIs against the assembled native Task/Feedback owners. The successful receipts pass the unchanged `_validate_runtime_load_receipt` and `_validate_decision_resolution_receipt` paths; alternately recorded load receipts remain rejected on resolved-batch replay. A native `needs_human` decision remains processing after the unchanged refusal. Test-local PIDs and SHA values exercise the persisted DB binding contract only; they do not prove an external daemon reload or Skill installation.

## Remaining cutover

- Feedback UI/backlog projections remain on the source mixed-Task projection: `list_user_feedback_items`, `list_user_feedback_items_by_keys`, and `count_pending_user_feedback_items` in `app/store.py` (their joins to `feedback_processing_items`). These were not needed by the new write validators and remain the next bounded reader unit.
- `upsert_feedback_event` at `app/store.py` still maintains the legacy Task-style Feedback item projection. It was outside the requested association/evidence/decision/resolve/reopen writer slice and was not converted here.
- `_feedback_processing_round_storage_is_valid` remains on the source Task projection for schema-manifest checks; the Store constructor/migration activation path is intentionally not enabled by this work. Reaching it on the assembled target requires a separately scoped constructor/manifest cutover.
- Complete Task2/Task3 caller cutover, backlog/UI readers, constructor activation, whole-service behavior and production release remain open. Nothing in this commit is a release or production-readback claim.
