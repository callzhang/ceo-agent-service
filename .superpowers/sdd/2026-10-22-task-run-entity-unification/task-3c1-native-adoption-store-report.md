# Task 3C1 native adoption Store report

Base: `d590dd4eef9e44cafccf8201cd4ec8c7893d8faa` in the isolated `task-run-entity-phase2` worktree. This implementation is local and unactivated; the Store constructor, runtime source-copy conversion, execution/receipt writer and release cutover are still pending.

## Changed behavior and APIs

`ReviewedCandidateStoreMixin` now uses `run_adopted_results` for candidate adoption and reads, `run_adopted_reviews` for Audit decisions, `run_adopted_selections` for the chosen option, and `run_adopted_supplements` for supplements and invalidation. Candidate IDs and execution/progress lookups are Consumer Run IDs; review IDs are Audit Run IDs. Returned DTOs retain the existing candidate/review names where they represent those meanings, derived from native owners. `get_candidate_execution` reads native `run_execution_progress` by Consumer Run ID.

The converted methods are `_candidate_context`, `_review_context`, `_check_historical_runtime_refusal`, `_adopt_review_candidate_in_connection`, `persist_review_candidate`, `adopted_candidate_for_consumer_run`, `get_review_candidate`, `reviewed_candidate_for_audit_run`, `_adopt_candidate_review_in_connection`, `record_candidate_review`, `current_reviewed_candidate`, `get_candidate_selection`, `select_candidate_option`, `record_candidate_supplement`, `invalidate_review_candidate`, `get_candidate_execution`, and `get_candidate_review_for_audit_run`.

Candidate and Audit result bodies are stored as their existing canonical JSON bytes with the same SHA-256 digest. Current business-object context comes from the Task's current `task_inputs` row. Historical refusal lookup reads matching TaskInput business-object keys. Selection branch reads now resolve the native selection-to-adopted-result relationship in `model_action_schema.selected_branch`.

For a later stage, the adopted result's `predecessor_audit_run_id` comes from the native Consumer Run parent and is validated against the related Audit Run, prior adopted result/review and `run_execution_progress`. The immutable candidate JSON is not rewritten: the regression fixture keeps embedded `predecessor_review_id=7` and its original digest while relational metadata resolves to the actual Audit Run ID.

`native_trajectory.read_native_result_json` now selects completed `runtime_attempts` by native `parent_id`, and its no-attempt path reads the native Run fields `session_id`, `transcript_start`, and `transcript_end`. It reuses the existing parser and native file reader.

## Verification

- `pytest -q --basetemp=/tmp/task3c1-native-adoption-store-test.<unique-child> tests/test_native_reviewed_candidate_store.py`: 2 passed. The persisted business case uses `AutoReplyStore` methods and the actual assembled native Task/Run DDL with Task, TaskInput, Consumer Run, Audit Run and RuntimeAttempt primitives. It checks exact content/digest, current-input context with two associated business keys, approved/returned/rejected Audit binding, review idempotence and conflict, native transcript import, selection derivation/idempotence, supplement invalidation, native owner IDs, and predecessor metadata.
- Native no-attempt result read: included in the same focused file; native file read returns the parsed object, and an unavailable file returns the existing empty/unavailable result.
- `python -m py_compile app/reviewed_candidate_store.py app/model_action_schema.py app/agent_orchestrator.py app/native_trajectory.py`: passed.
- `python -m ruff check app/reviewed_candidate_store.py app/model_action_schema.py app/agent_orchestrator.py app/native_trajectory.py tests/test_native_reviewed_candidate_store.py`: passed.
- `git diff --check`: passed.
- One legacy fixture check was run: `tests/test_reviewed_candidate_store.py -k test_review_binding_and_restart` fails before reaching the changed methods because the existing constructor-created database has no `task_inputs` table (`no such table: task_inputs`). This constructor/source fixture predates and remains outside this unactivated slice; it is not reported as a native Store behavior failure.

## Remaining cutover work

The current constructor does not activate the assembled native DDL. Execution and receipt writes (`wake_selected_candidate_execution`, claim/finish/recovery, action attempts and receipts), remaining human-decision evidence readers, full RunStore/History trajectory readers, and the source-copy converter/inverse remain to be closed by C2/C3 and the full runtime cutover. Current old-schema consumers of `selected_branch` in `shared_action_lifecycle`, `dingtalk_delivery_repair`, and legacy selection tests need their planned native caller conversion before publication. No production DB, provider, native Agent CLI, push, or deploy action was used.
