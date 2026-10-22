# Task entity contract validation (2026-10-22)

This is a pure, unactivated Task-only contract. It covers the 20 logical Task20 projections in `app/task20_definitions.py`, not historical pre-Task20 tables. The field map has 267 logical projection fields and 20 separate physical-ID mapping rows. Every row resolves to a real target table/column or an explicit retirement reason; schema creation and map validation are executable through `app.task_run_entity_definitions`.

The target schema currently creates 62 objects, including tables, indexes, and triggers. The Task core retains source lifecycle values without an invented narrow status enum. Request parameters use native SQLite JSONB storage, and per-kind triggers reject unknown keys and missing no-default request keys. Reply TaskInput values remain independently versioned; current-input identity uniqueness is checked through the bound input row, while same-task versions remain valid. WorkSummary, Meeting, OKR review, and weekly analysis source unique keys have target indexes; Workbench client request ID is globally unique and sequence is unique per Workbench Task. Named domain facts remain in separate tables. The technical inverse map uses `(source_kind, physical_id)` and carries public-key value/type separately.

`workbench_turns.final_text` is explicitly retired from persisted storage because its source declaration is `storage='none'`. Input-version pointers remain nullable for source kinds without that concept. The inverse map stores source public-key values/type only where needed; it does not copy source payload bodies.

## Boundaries and open items

Run and Action target DDL, adopted-result ownership, Run identity/source key mapping, and source wrapper/result encoding/framing are deliberately not declared complete here. A separate bounded unit must add those exact declarations before the assembled target is called complete. This Task-only schema has not been activated and has no migration/writer integration. Source query indexes, active Workbench turn uniqueness and domain-parent FK/default parity still need source-by-source parity review before assembly.

## Verification

- RED: initial test collection failed because `app.task_run_entity_definitions` did not exist.
- GREEN: `/Users/derek/miniforge3/bin/python -m pytest -q tests/test_task_run_entity_definitions.py` — 8 passed.
- Baseline: `/Users/derek/miniforge3/bin/python -m pytest -q tests/test_task20_definitions.py tests/test_task_lifecycle_storage.py tests/test_logical_run_owner.py` — 363 passed, 3 failed. The same three parameterized `test_consumer_parent_contract_rejects_standalone_in_all_existing_refs` cases fail before the assertion: their fixture inserts retired `external_action_results.first_agent_run_id`, and current Action storage rejects that source field. This is pre-existing and outside Task1.
- Schema proof: all 62 generated objects execute in in-memory SQLite 3.53.4 with foreign keys enabled; `validate_task_run_field_map()` succeeds for all 287 mapping rows.
- No production database was opened or changed by this unit. Parent-provided read-only snapshot metadata was integrity-checked separately.
