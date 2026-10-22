# Task 1 report: Task-only pure entity contract

Date: 2026-10-22
Worktree: `/Users/derek/.codex/worktrees/task-run-entity-phase2/ceo-agent-service`
Branch: `codex/task-run-entity-phase2-20261022`
Revision base: `292331f40717be4338c8151fe80659606f47e1e2`
Review: `.superpowers/sdd/2026-10-22-task-run-entity-unification/task-1a-review.md`

## Review findings addressed

- **Active Workbench turn uniqueness:** Added target triggers for insert, parent reassignment, and Task status updates. They implement the current source partial unique predicate exactly: a Workbench parent may have at most one child turn in `queued` or `running`; terminal statuses do not conflict. The paired source test builds the current Task20 schema and verifies the published partial index rejects a second active child and permits one after the first becomes `completed`. Target tests cover duplicate active turns, terminal allowance, and conflict when reactivating a turn.
- **JSON request parameter scalar types:** No strict JSON type validator was added because it would tighten source behavior. Evidence: using `create_current_task20_schema()` with the source runtime helper `task8_insert_parts()` inserts `force_new_decision='true'` into an INTEGER-affinity source projection. The source generated value has `typeof(...)='text'`; it is not a STRICT table and has no `typeof()` check for this field. The target JSONB parameter with the same string scalar remains `json_type(...)='text'` and is accepted. This paired test preserves the SQLite affinity/codec behavior without coercion or new rejection policy.
- **Field-map metadata parity:** `validate_task_run_field_map()` now checks each logical row's source affinity, nullable/required declaration, default, and storage wrapper against `TASK20_COLUMNS`. Physical identity rows have a separate fixed contract. Tests mutate each metadata field and verify rejection.

## Deliverable and counts

The Task-only native target remains pure and unactivated. `task_run_entity_objects()` returns a pinned DDL dictionary and does not use the source factory. The field map and validation helpers use current Task20 source declarations as the migration oracle. It covers exactly 20 logical Task20 projections (not historical pre-Task20 tables): 267 logical source fields and 20 physical-ID mappings, 287 mapping rows total. All 65 target objects execute in in-memory SQLite 3.53.4 with `PRAGMA foreign_keys=ON`; map validation resolves all rows to actual target columns or explicit retirements.

Physical IDs remain distinct from public IDs. Inverse metadata is keyed by source kind and preserved physical ID, with original public-key value/type separate. No source row/body shadow copies were added. `workbench_turns.final_text` stays non-persisted because its source declaration is `storage='none'`. No Run/Action tables, native result-envelope columns, or runtime writers were added.

## RED/GREEN and tests

- Original Task1 RED before implementation: test collection failed because `app.task_run_entity_definitions` did not exist.
- Task1A review RED against base `292331f40717be4338c8151fe80659606f47e1e2`: the metadata-tamper and active-Workbench-turn tests failed because the old validator accepted altered source metadata and the old target allowed a second queued turn under the same parent (2 failed, 9 deselected).
- Task1A focused GREEN: `/Users/derek/miniforge3/bin/python -m pytest -q tests/test_task_run_entity_definitions.py` — 11 passed.
- The three original source baseline fixture failures remain: baseline command over `test_task20_definitions.py`, `test_task_lifecycle_storage.py`, and `test_logical_run_owner.py` produced 363 passed, 3 failed in 30.24s. The failures all occur before assertions when fixtures insert retired `external_action_results.first_agent_run_id`; current Action storage reports `table actions has no column named first_agent_run_id`. This Task1A revision does not skip or alter those cases.
- No full suite was run. No production DB, production checkout, backup, provider, native session, push, deploy or activation was touched. Parent-provided snapshot remains immutable; the pre-existing Run FK defect (`runs.id=190042`, missing `tasks.id=404225`) remains untouched.

## Open concerns

Run/Action DDL, adopted-result ownership, Run identity/public keys and wrapper/result framing are separate assembly work. Source-to-target Task domain-parent FK/default parity and remaining source non-unique query-index parity still need a table-by-table review. `okr_review_items.request_id` is one known parent edge requiring explicit verification. This report does not claim complete Task/Run assembly or production migration readiness.

## Commits

Pending at report creation; the revision hash will be supplied in the handoff.
