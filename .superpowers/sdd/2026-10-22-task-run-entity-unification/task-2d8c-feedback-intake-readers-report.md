# Task2D8C Feedback intake and backlog readers

## Scope and status

Base: `d930a2493a14239b03e2fe71c507c290be69715e`

Final: this report is included in the implementation commit; the exact final HEAD is recorded in the task handoff.
Status: Fix1 implements the independent review finding locally; scoped re-review and later constructor/whole-phase acceptance remain open.

This slice converts `AutoReplyStore.upsert_feedback_event`, `list_user_feedback_items`, `list_user_feedback_items_by_keys`, and `count_pending_user_feedback_items` to current native owners. It does not activate constructors, workers, providers, the native CLI, production, or the whole Task2/3 runtime.

## Method ownership and preserved behavior

- `upsert_feedback_event` still records/updates the source Action Feedback event through the existing helper and its original conflict semantics. When no native detail exists, it creates one native `feedback_processing` Task and detail in the same transaction. The source event's `resolved_at` initializes the Task to `resolved` or `pending`; `tasks` owns status and lifecycle timestamps. On repeat, a nonblank source `resolved_at` sets the existing Task to `resolved` and copies that timestamp to the Feedback detail; a blank source timestamp retains current Task status and detail resolution timestamp. Repeats preserve association, evidence, and rounds.
- Public list/by-key projection reads processing state and detail fields through `feedback_processing_items` joined to native `tasks`, instead of `task12_projection`.
- Reply context still comes from `REPLY_ATTEMPT_COMMON_SQL`, preserving latest sent-token and exact manual-key selection, reviewer/correction visibility, and independent source ReplyAttempt ID. Role comes from native `runs`; `codex_session_id` remains sourced from the ReplyAttempt projection. Reply context `attempt_id`/`agent_run_id` remain distinct from Feedback processing association fields.
- Pending count keeps its prior source-unresolved, `pending`, no-reviewer-feedback, and no-corrected-reply criteria.
- Actual API acceptance uses `register_console_routes` with the already assembled native Store fixture. It covers registered list/by-key/detail/batch/count and resolve paths; this is backend route evidence, not browser UI evidence.

## Verification

All commands ran in this worktree against the bounded native fixture unless noted:

- `pytest -q tests/test_store_task_entity_creator.py -k 'feedback_event_upsert or feedback_event_and_task_intake or feedback_backlog_keeps_latest_token'` — 3 passed, 27 deselected.
- `pytest -q tests/test_task_entity_schema.py::test_native_feedback_claim_after_converted_reopen_clears_captured_unresolved_refs` — 1 passed.
- `pytest -q tests/test_task_entity_schema.py::test_feedback_intake_task_survives_source_inverse_and_reforward` — 1 passed; starts from absent source Feedback item/round, runs source-to-native migration and real native intake, edits current facts, checks inverse source public projection, then re-forwards and verifies the current facts and updated Action Feedback event. The migration/DDL was committed before backup to a fresh destination.
- `pytest -q tests/test_console_feedback_iteration_api.py` — 5 passed.
- `pytest -q tests/test_feedback_processing_e2e.py -k 'batch_detail_reads_exact_item_beyond or batch_detail_database_query_count or resolve_fails_closed or resolve_does_not_call_cached or resolve_requires_strict or resolve_preserves_real_git_commit'` — 14 passed, 3 deselected.
- `ruff check app/store.py tests/test_store_task_entity_creator.py tests/test_task_entity_schema.py tests/test_feedback_processing_e2e.py tests/test_console_feedback_iteration_api.py` — passed.
- `python -m py_compile app/store.py tests/test_store_task_entity_creator.py tests/test_task_entity_schema.py tests/test_feedback_processing_e2e.py tests/test_console_feedback_iteration_api.py` — passed.
- `git diff --check` — passed.

The later source-constructor worker test was run separately and remains a concrete gate: `pytest -q tests/test_feedback_processing_e2e.py::test_attempt_8308_feedback_processing_requires_complete_receipts` fails in `_seed_attempt_8308` when its `AutoReplyStore(db_path)` source fixture reaches the new native intake method and raises `sqlite3.OperationalError: no such table: feedback_processing_items`. This fixture is not skipped or weakened. It requires conversion to the assembled native constructor/worker fixture in a later constructor acceptance; no full worker run is claimed here.

## Remaining source/static paths and boundaries

- There is no remaining app runtime occurrence of `task12_projection('feedback_processing_items')` in the converted intake/list/by-key/count path. Existing `task12_projection('feedback_processing_items')` and `task12_set_parts('feedback_processing_items', ...)` references are in historical `tests/test_feedback_processing.py` and `tests/test_feedback_processing_e2e.py` fixture/oracle code; these were not rewritten as part of this bounded reader slice.
- `AGENT_RUN_SQL` remains used by unrelated legacy runtime paths elsewhere in `app/store.py`; the converted public feedback readers use direct native `runs` for ReplyAttempt role only; ReplyAttempt `codex_session_id` remains the source context field.
- The current API route fixture was changed to use the actual assembled native Store. Full `create_audit_app`/worker constructor activation, browser UI, independent SPEC/QUALITY review, and complete Task2/3 cutover remain open.
- Phase 2 remains unactivated. No production write/readback, push, main integration, deploy, provider action, or native CLI action occurred. The only existing production observation in the public status documents remains the dated read-only weekly count at `2026-10-23T13:46:56Z`.
## Fix1 review response (base `1d18fe579bb8a4c4332eaf6194d20562c98325d3`)

The scoped review found that the existing-detail branch omitted the original conditional source-resolution projection. It now reads the resulting source event in the same intake transaction. A nonblank source timestamp sets native Task status to `resolved` and copies the source timestamp to the Feedback detail; a blank timestamp updates Task `updated_at` while retaining current Task status and detail resolution timestamp. Association, evidence, and rounds are untouched.

- Command: `pytest -q tests/test_store_task_entity_creator.py::test_feedback_event_repeat_projects_resolved_source_and_preserves_blank_source_facts tests/test_store_task_entity_creator.py::test_feedback_event_upsert_creates_one_native_task_and_preserves_processing_state tests/test_store_task_entity_creator.py::test_feedback_event_and_task_intake_roll_back_together`
- Output: `3 passed in 10.53s`. The new native regression checks a processing Task projected to resolved by a newly resolved source event, then a blank-source repeat preserving manually current processing/resolution facts and all association/evidence values.
- Command: `ruff check app/store.py tests/test_store_task_entity_creator.py && python -m py_compile app/store.py tests/test_store_task_entity_creator.py && git diff --check`
- Output: `All checks passed!`
