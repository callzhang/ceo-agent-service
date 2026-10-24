# Task 2D8A — native Feedback claim implementation

BASE: `95cbc897160252780fd460ed3b7ad94a5be947af`.

FINAL HEAD for implementation, tests and behavior docs: `48ee84a0fedc1075b04cfdf461dc24e64e5cb350`. This report is recorded in a follow-up documentation commit.

## Result and exact scope

Implemented native Feedback batch creation, item creation, batch/item/round reads, atomic claim, same-batch idempotent claim replay, and the processing-lineage readers reached by those methods in `app/store.py`. They now use the assembled current owners: Feedback item detail in `feedback_processing_items`, item lifecycle status and timestamps in its parent `tasks` row (`kind='feedback_processing'`), batch and rounds in their native Feedback tables, and claim transitions through the existing Action owner. No old `task12_projection`, `record_type`, legacy UUID lookup, or source-shaped compatibility view is used by these converted methods or the claim validators they reach. `app/task_entity_schema.py` also fixes the reproduced inverse defect: a captured unresolved marker is applied only while its native field still equals the captured value, so the actual claim reset to empty workbench refs and zero current-round survives inverse.

New Feedback Tasks store empty parameters as SQLite JSONB through `task_entity_storage.create_task`; the Feedback child stores the remaining domain fields. Claim writes the same single timestamp to each new round and its transition; it changes Task `status`/`updated_at`, resets the current projection fields on the child, and changes batch state/timestamp. It appends one native round and Action transition per key. Same-batch replay verifies existing lineage and adds no Task, round, or transition. Existing source event validation remains on `FEEDBACK_EVENT_SQL`.

Current callers in `app/web_api/registration.py` use the native item, batch and round readers and `claim_feedback_processing_items` from the feedback console endpoints (including batch claim, item detail/history and batch refresh); `app/audit_web.py` reads item projection for the audit console. The public DTOs remain unchanged. `create_feedback_processing_batch` remains an available current Store API and now writes native owners; the console batch-claim endpoint creates a batch atomically in `claim_feedback_processing_items` when one does not already exist.

The converter regression starts with the real source fixture, provides unequal typed Workbench Task/Turn and Feedback round handoff IDs, captures reopened source history and unresolved numeric-looking refs, forwards to the actual assembled target schema, invokes the real native claim, restores through a JSON-portable inverse handoff, re-forwards, and restores once more. It proves the current clear remains empty in both inverse views, the claimed Task/item stays processing, the previous resolved/reopened round is preserved, and the new processing round is round 2. The separate focused converter test also proves a current reset of captured unresolved refs and `current_round_id=0` survives inverse. The converter cannot distinguish an intentional edit whose SQLite lexical value exactly equals the captured unresolved marker from an unchanged fact; it preserves the source marker for that indistinguishable case rather than inferring an edit.

No constructor activation, source fallback, association/evidence/decision/resolve/reopen conversion, worker/policy, UI rewrite, provider, production write, main integration, push, or deployment occurred. Association and evidence writers, decision and resolution/reopen validators outside the claim path, Feedback backlog/UI projection and whole Task/Run cutover remain for later bounded work. The controller's separately probed `scope_receipt_json.decision_id` handoff issue belongs to the next decision/resolution converter unit and is not reached by this claim path; no validation was weakened to accommodate it.

## Verification

Focused acceptance command:

```sh
pytest -q \
  tests/test_store_task_entity_creator.py::test_native_feedback_replay_rejects_malformed_current_round_lineage \
  tests/test_store_task_entity_creator.py::test_native_feedback_batch_claim_replay_and_public_reads \
  tests/test_store_task_entity_creator.py::test_native_feedback_create_and_capability_refusals_are_atomic \
  tests/test_store_task_entity_creator.py::test_native_feedback_competing_connections_have_one_claim_winner \
  tests/test_store_task_entity_creator.py::test_native_feedback_reopened_history_claims_next_round \
  tests/test_task_entity_schema.py::test_feedback_current_reference_clears_survive_inverse_despite_captured_markers \
  tests/test_task_entity_schema.py::test_native_feedback_claim_after_converted_reopen_clears_captured_unresolved_refs
```

Result: `7 passed in 6.60s`. These tests cover malformed current-round replay refusal; actual target schema and current public reads; dedupe, same-batch replay and atomic multi-key creation/claim; missing/resolved-event refusal without partial owner creation; capability-disabled refusal; two-connection single-winner behavior; reopened history's next round; and forward/current-claim/inverse/portable-reforward/inverse behavior with unequal typed IDs.

Static checks:

```sh
ruff check app/store.py app/task_entity_schema.py tests/test_store_task_entity_creator.py tests/test_task_entity_schema.py
python -m compileall -q app/store.py app/task_entity_schema.py
git diff --check
```

Result: Ruff `All checks passed!`; compileall and diff check exited successfully. The full suite was not run per repository instruction.

## Fix round 1 — make the competing-connection regression observe contention

Reviewer finding: the two claim futures previously started without a synchronization point, so sequential execution could pass without proving contention. Updated only `test_native_feedback_competing_connections_have_one_claim_winner` in `tests/test_store_task_entity_creator.py`. Both calls now meet at a test-local barrier at the native transaction boundary. The test holds an independent real SQLite `BEGIN IMMEDIATE` writer lock and instruments only the test's connection wrapper to record each claim's actual `BEGIN IMMEDIATE` call before delegating to SQLite. While the external lock is still held, it asserts both different threads reached that begin call and neither future completed; then it releases the lock and retains the original one-winner, one-round, and one-transition assertions. No product/runtime code changed and no sleep-based timing was added.

Covering command and output:

```sh
pytest -q tests/test_store_task_entity_creator.py::test_native_feedback_competing_connections_have_one_claim_winner
```

`1 passed in 1.59s`

Static verification:

```sh
ruff check tests/test_store_task_entity_creator.py
git diff --check
```

Ruff reported `All checks passed!`; `git diff --check` exited successfully. Self-review confirms the lock is acquired through SQLite itself, both observed begin attempts occur before releasing it, and the same native Store methods and business assertions remain under test.
