# Task 1 report: Task-only pure entity contract

Date: 2026-10-22
Worktree: `/Users/derek/.codex/worktrees/task-run-entity-phase2/ceo-agent-service`
Branch: `codex/task-run-entity-phase2-20261022`
Base: `130b3fd42`

## Deliverable

Implemented an unactivated, pure Task target contract in `app/task_run_entity_definitions.py`, with a read-only SQLite inspector, tests, and generated SQL/JSON documentation. `task_run_entity_objects()` returns a pinned native DDL dictionary; it does not consult the source factory. Source imports and loops are used only by explicit mapping/validation helpers. The Task-only mapping covers exactly 20 current logical Task20 projections (not pre-Task20 historical tables): 267 logical projection fields and 20 separate physical-ID mapping rows, 287 rows total. The in-memory schema executes all 62 declared objects and `validate_task_run_field_map()` checks every destination against actual target columns and fails duplicate, missing, unknown, or unmapped fields.

Task physical IDs are retained as target IDs. Technical inverse metadata is keyed by `(source_kind, physical_id)` and stores original public-key value/type separately; it does not assume public IDs are unique across source kinds or equal to the physical ID. `workbench_turns.final_text` stays unpersisted because its current source declaration has `storage='none'`. No Run or Action tables, adopted-result fields, or native result columns are declared in this Task-only contract; Run/Action remains a separate assembly subunit. Native result-envelope columns that are absent from storage were not invented.

Request parameters are native SQLite JSONB and per-kind triggers reject unknown keys and missing no-default request keys. The request field whitelist is explicit. Reply TaskInput keeps source revision identity, and current reply uniqueness is derived through each Task's bound current input; another Task cannot bind the same channel/conversation/trigger identity even at a different input revision, while multiple revisions for one Task remain valid. Source `current_input_id`/`claimed_input_id` remain nullable and FK-backed. The schema does not impose a same-parent rule absent from current source enforcement. Workbench client request identity is globally unique; sequence is unique within its Workbench Task.

## Source/index and identity concerns retained for parent review

This is a bounded Task unit, not a claim that all combined target parity is done. Remaining source contracts include active Workbench-turn uniqueness, some non-unique query indexes, and domain-parent FK/default parity. Source published unique keys transferred to target include WorkSummary `(source_type, source_ref)`, Meeting `meeting_id`, OKR review `(conversation_id, trigger_message_id)`, weekly analysis `(week_end, manager_user_id, source_digest)`, reply current-input identity, Workbench global `client_request_id`, and per-Task sequence. Parent should review these and the remaining declared/native indexes before assembly. Source Workbench `final_text` is intentionally retired from persisted schema.

Run source wrappers, result framing/encoding, adopted-result identity, Run source public keys, and Run/Action DDL are unresolved by this Task-only change and must be handled by their separately assigned contract unit. No old full row/body shadow copies are introduced. No source data was mutated.

## RED/GREEN and verification

- RED: before implementation, collection failed because `app.task_run_entity_definitions` could not be imported.
- GREEN: `/Users/derek/miniforge3/bin/python -m pytest -q tests/test_task_run_entity_definitions.py` — 8 passed.
- Baseline: `/Users/derek/miniforge3/bin/python -m pytest -q tests/test_task20_definitions.py tests/test_task_lifecycle_storage.py tests/test_logical_run_owner.py` — 363 passed, 3 failed in 30.24s. All 3 are the known parameterized `test_consumer_parent_contract_rejects_standalone_in_all_existing_refs` fixture failure. Each fixture inserts retired `external_action_results.first_agent_run_id`; current Action storage raises `sqlite3.OperationalError: table actions has no column named first_agent_run_id` before the test reaches the assertion. This pre-existing baseline is recorded, not hidden or fixed in Task1.
- Schema creation: 62 declared objects executed in in-memory SQLite 3.53.4 with `PRAGMA foreign_keys=ON`; field-map validation accepted 287 rows. The DDL fixture is generated from the pinned target-object dictionary.
- `python -m py_compile app/task_run_entity_definitions.py` passed.
- No full suite was run. No production database, production checkout, backup, provider, native session, push, deploy, or activation was touched. The parent-provided immutable read-only source snapshot has integrity OK; parent separately identified one pre-existing Run FK defect (actual missing task id `404225` for Run `190042`), which remains untouched and is not masked by this Task-only DDL.

## Commit

`304598fc14d183c4c4a28b7ca00f5a60a1a77a0e` — Add pure Task entity schema contract. The claim row was removed after completion in a separate cleanup commit.
