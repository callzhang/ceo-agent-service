# Task 3A native Run owner implementation report

## Result

Added `app/run_entity_storage.py` as the low-level owner for native `runs`, `runtime_attempts`, `run_provenance`, `run_projections`, and `run_failures` rows. Every method takes the caller's connection. No function begins, commits, or rolls back a transaction; performs a retry; chooses a lifecycle transition; manufactures clocks or invocation facts; or dispatches on a source namespace.

The Run field allowlist is derived from the approved `RUN_FIELDS`, excluding only generated `id`. RuntimeAttempt accepts the 29 non-id columns declared by the actual DDL. Create returns `RETURNING *`; get returns the stored row; CAS compares only supplied expectation fields using SQL `IS`, updates only supplied fields, and returns `None` on a miss. Neither update path timestamps automatically. SQLite assigns a Run ID and uses the real Run `created_at` default when it is omitted.

Typed child operations explicitly write/read provenance, the Task Agent projection, and append/list server failure facts. Projection remains a JSON text value in `run_projections` and is separate from adopted-result tables. RuntimeAttempt parent/workload mapping, the three correction-lineage immutability rules, ordinal uniqueness, and active-route uniqueness remain enforced by the assembled schema.

## Evidence

Tests execute `task_run_entity_assembly_objects()` with foreign keys enabled and use `task_entity_storage.create_task` with actual per-kind parameter contracts. The tests assert Run and RuntimeAttempt PRAGMA column inventories match actual native declarations. They cover all nine Task kinds with null absent role/revision fields; valid and invalid professional provenance; a Run with no attempt and one with multiple attempts; parentless structured workload; ordinal and active-route conflicts; observed session/transcript updates; each of the three immutable correction fields independently; projection/failure child separation; unknown Run fields; stale CAS; and caller rollback.

Pre-implementation red: all three initial focused API tests failed at import because `app.run_entity_storage` did not exist. Post-implementation: `pytest -q tests/test_run_entity_storage.py tests/test_task_run_entity_assembly.py tests/test_run_entity_definitions.py` passed, 32 tests. `ruff check app/run_entity_storage.py tests/test_run_entity_storage.py`, `python -m compileall -q app/run_entity_storage.py tests/test_run_entity_storage.py`, and `git diff --check` passed.

## Remaining gates

This unit does not migrate existing source Run rows, activate a runtime constructor, close domain callers, route native CLI/runtime execution, persist adopted results, schedule work, or prove full-copy/inverse behavior. The seven predecessor constructor failures reproduced by the parent remain required constructor-cutover work. Fresh independent SPEC and QUALITY reviews remain outstanding after this implementation report. No provider, CLI, production, push, or deploy action was performed.
