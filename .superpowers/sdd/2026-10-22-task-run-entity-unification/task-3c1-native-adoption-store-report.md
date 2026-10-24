# Task 3C1 native adoption Store report

Base: `d590dd4eef9e44cafccf8201cd4ec8c7893d8faa` in the isolated `task-run-entity-phase2` worktree. This implementation is local and unactivated; the Store constructor, runtime source-copy conversion, execution/receipt writer and release cutover are still pending.

## Changed behavior and APIs

`ReviewedCandidateStoreMixin` now uses `run_adopted_results` for candidate adoption and reads, `run_adopted_reviews` for Audit decisions, `run_adopted_selections` for the chosen option, and `run_adopted_supplements` for supplements and invalidation. Candidate IDs and execution/progress lookups are Consumer Run IDs; review IDs are Audit Run IDs. Returned DTOs retain the existing candidate/review names where they represent those meanings, derived from native owners. `get_candidate_execution` reads native `run_execution_progress` by Consumer Run ID.

The converted methods are `_candidate_context`, `_review_context`, `_check_historical_runtime_refusal`, `_adopt_review_candidate_in_connection`, `persist_review_candidate`, `adopted_candidate_for_consumer_run`, `get_review_candidate`, `reviewed_candidate_for_audit_run`, `_adopt_candidate_review_in_connection`, `record_candidate_review`, `current_reviewed_candidate`, `get_candidate_selection`, `select_candidate_option`, `record_candidate_supplement`, `invalidate_review_candidate`, `get_candidate_execution`, and `get_candidate_review_for_audit_run`.

Candidate and Audit result bodies are stored as their existing canonical JSON bytes with the same SHA-256 digest. Current business-object context comes from the Task's current `task_inputs` row. Historical refusal lookup reads matching TaskInput business-object keys. Selection branch reads now resolve the native selection-to-adopted-result relationship in `model_action_schema.selected_branch`.

For a later stage, a newly submitted candidate carries the actual native Audit Run ID for the previous stage, and Store validation matches that ID to the approved prior-stage result and completed execution. A same-stage revision is parented by the Audit Run that reviewed the previous same-stage candidate, while its predecessor lineage remains the prior adopted result's `predecessor_audit_run_id`. Migrated candidate JSON/digest stay immutable. The historical fixture that embeds `predecessor_review_id=7` is seeded as an existing adopted row whose relational metadata points to the actual Audit Run; newly submitted revision bodies use that actual ID, and a newly submitted `7` is rejected. `_business_revision` and `_stage_for_parent` read the relational metadata.

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

## Fix 1 — native stage revision and generation regressions

The independent review found that stage>0 adoption had treated the new Consumer Run's immediate parent Audit as the candidate's stage predecessor. That works for a later stage but rejects a same-stage revision because its parent Audit reviews the previous candidate at that same stage. The Store now validates the submitted predecessor field directly against native Audit Run ownership, prior-stage index, approval, and done/skipped execution, preserving the original Store contract. The orchestrator already supplies the previous stage's relational predecessor for a same-stage revision, so no immediate-parent transition gate or new stage policy was added. Stage 0 keeps its existing behavior. Candidate JSON and SHA-256 are unchanged.

The native DDL regression now creates a real stage-0 result and a valid stage-1 result, then seeds a migrated stage-1 adopted row whose immutable JSON contains `predecessor_review_id=7` while its relational predecessor references the real Audit Run. It invokes the actual `AgentOrchestrator._business_revision` and `_stage_for_parent` methods over Store reads, proves a same-stage Consumer revision accepts the real relational predecessor, and proves a new body containing `7` is rejected. The no-attempt missing-source case and stored adopted-row read with unavailable trajectory source are also covered. A separate native DDL case verifies stale-generation read rejection and current projection exclusion, plus rollback of a Store adoption write and child row without consuming the rolled-back supplement ROWID or creating an AUTOINCREMENT sequence.

Verification for Fix 1:

- `pytest -q --basetemp="$(mktemp -d /tmp/task3c1-native-adoption-fix1.XXXXXX)" tests/test_native_reviewed_candidate_store.py`: 3 passed.
- Existing caller checks: `pytest -q --basetemp="$(mktemp -d /tmp/task3c1-native-adoption-fix1-callers.XXXXXX)" tests/test_oa_consumer_stage_lineage_binding.py tests/test_oa_audit_return_same_stage.py tests/test_material_request_stage.py`: 4 passed, 1 failed before the changed orchestration methods because the pre-cutover `AutoReplyStore` fixture's SQLite database has no native `task_inputs` table. The two OA caller files alone pass 4 tests.
- `python -m ruff check app/reviewed_candidate_store.py app/agent_orchestrator.py tests/test_native_reviewed_candidate_store.py`: passed. `python -m py_compile` on the same three files: passed. `git diff --check`: passed.
- The previous verification paragraph's `2 passed` records the original C1 submission only; Fix 1's native suite is the 3-test run above.
