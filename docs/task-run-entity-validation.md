# Task entity contract validation (2026-10-22, Task1A revision)

This is a pure, unactivated Task-only contract. It covers 20 current logical Task20 projections, not historical pre-Task20 tables. The map contains 267 logical fields and 20 distinct physical-ID rows (287 total). Target DDL is pinned in `task_run_entity_objects()` and no longer builds target schema by iterating the source factory. Map validation uses the frozen Task20 catalog only to verify source ownership metadata, and checks every destination against actual target table columns.

The 65 target objects execute in in-memory SQLite 3.53.4 with foreign keys enabled. Target metadata parity validation checks source SQL affinity, required/null status, default and storage wrapper for every logical source field. Target request triggers reject unknown JSON keys and required-key omissions. They intentionally do not enforce JSON scalar types: the source is not STRICT and its INTEGER affinity does not imply a `typeof()` constraint. A paired test uses current `create_current_task20_schema()` plus native `task8_insert_parts()` to insert `force_new_decision='true'`; SQLite stores the generated source value as TEXT. A JSONB target parameter with the same string value is accepted and remains JSON type `text`, preserving source admission and encoding rather than introducing a new validation policy.

The active Workbench-turn source predicate is represented by target triggers on turn insert/reparent and Task status update: only `queued` or `running` turns collide, for the same Workbench parent. Tests cover target conflict, target terminal-status allowance and reactivation conflict. A source-side test creates the current Task20 partial unique index and verifies the same parent/predicate behavior.

Reply current-input identity uniqueness remains derived through the bound input row, and multiple revisions for one Task remain allowed. WorkSummary, Meeting, OKR review, weekly analysis, Workbench global `client_request_id`, and per-Task sequence uniqueness are represented. The inverse map is keyed by `(source_kind, physical_id)` and retains public-key value/type separately. `workbench_turns.final_text` remains non-persisted because its source declaration is `storage='none'`.

## Open parity concerns

Run/Action DDL, adopted-result ownership, Run identity/public keys, and wrapper/result framing remain separate assembly work. Task-domain parent FK/default parity and remaining source non-unique query indexes still need a table-by-table review. In particular, `okr_review_items.request_id` and all source parent edges should be explicitly checked before assembly. No source or production data was modified.

## Verification

- Focused GREEN: `/Users/derek/miniforge3/bin/python -m pytest -q tests/test_task_run_entity_definitions.py` — 11 passed.
- Added paired current-source checks for (1) active Workbench-turn partial uniqueness and (2) actual SQLite INTEGER-affinity admission of a string value through the source insert helper and JSONB target.
- Metadata tamper tests independently change SQL type, null contract, source default, and storage wrapper; each is rejected by `validate_task_run_field_map()`.
- Schema proof: 65 target objects execute; validation passes all 287 map rows.
- The prior baseline remains 363 passed / 3 known pre-assertion failures: the fixture inserts retired `external_action_results.first_agent_run_id`; current Action storage raises before the assertions. This revision does not alter that behavior.
- No full suite, runtime activation, provider, production, backup or deploy operation was performed.
