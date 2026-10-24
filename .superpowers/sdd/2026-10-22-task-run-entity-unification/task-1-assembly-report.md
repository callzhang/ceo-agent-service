# Task 1 assembly report — 2026-10-22

## Result

Task 1's schema-only contract is assembled and locally validated. The result is an unactivated SQLite schema target; no migration, runtime reader/writer selection, database copy, provider operation, production access/write, push, or deploy occurred.

## Contract assembled

- `app/task_run_entity_assembly.py` composes the target Task, Run, Action, adoption and receipt declarations with actual retained-owner creators for schedules, configs, State, provider state, provenance, Email, Business and the Email migration ledger. It does not invoke `create_current_database_schema`, `current_objects`, or old Task20/Run11 schema factories. A test patches those whole/source factories to raise and confirms target construction still succeeds.
- The retained-owner source-oracle test compares unchanged current declarations byte-for-byte. Source Run field metadata is still validated explicitly against actual legacy Run DDL through `validate_run_entity_contract`; this source validator is separate from target construction. Task's 267 logical fields plus 20 physical identity rows, 8 Run kinds × 79 fields, and 43 adoption fields are frozen in `docs/task-run-entity-assembly-field-map.json`.
- Full deterministic SQL is frozen in `docs/task-run-entity-assembly-target.sql` (479 objects). The retirement inventory is in `docs/task-run-entity-assembly-retirement.sql`. Target construction rejects duplicate actual SQLite object names, including collisions masked by Python dictionary keys.
- The Action owner preserves current native Action kinds and type codes, retires the six adopted source kinds and their candidate-only indexes, and remaps the retained feedback parent FKs to real target domain tables. The candidate Task-kind guard moves to `run_adopted_results.consumer_run_id -> runs.task_id -> tasks.kind='reply'`; no nonexistent `actions.task_id` reference remains. The generic Action Run parent rejects a native Run with `run_provenance`.
- Receipt `execution_id` is replaced by `execution_run_id REFERENCES run_execution_progress(consumer_run_id)`. The result owner must be a Consumer Run on a reply Task; review and progress rows retain matching Consumer/Audit Run lineage. `run_projections` remains the professional Run projection owner, separate from adopted result facts.
- Task kind immutability is preserved, with status changes still allowed. `okr_review_items.request_id` references the actual Task row and its guard requires `kind='okr_review'`.

## Verification

Focused command:

```text
pytest -q tests/test_task_run_entity_assembly.py tests/test_run_adoption_definitions.py tests/test_task_run_entity_definitions.py tests/test_run_entity_definitions.py
42 passed
```

Ruff and `git diff --check` pass. The assembled full schema executes in in-memory SQLite with foreign keys enabled; its object-name set equals the executed `sqlite_schema` catalog and `PRAGMA foreign_key_check` is empty. A wider focused run across six relevant files produced 56 passed and 2 failures in `tests/test_final_action_schema.py`: the legacy `seed_task` helper inserts the old Task `status` field into the unified current `tasks` declaration. Those two failures are outside the four passing Task/Run assembly/definition files; they remain explicit and are not counted as green. Tests exercise real adopted-result, professional Run projection, execution-progress, receipt, retained execution-receipt, meeting-claim, feedback-parent, and reply-memory-action writes, with wrong-owner and missing-reference failures. The unchanged current retained-owner DDL matches the source oracle exactly. Source metadata validation covers nullability and defaults; Task/Run validators reject incomplete or tampered mappings. The inspector regression confirms index names come from `PRAGMA index_list` name values.

The three known historical `first_agent_run_id` fixture failures remain Task3 work and are not skipped or reported as green. No full suite was run.

## Remaining boundary

Independent parent review remains. Runtime caller conversion, migration/copy, current-fact inverse, old-code cold read/write/reforward, production sizing, and release selection are outside Task 1; this report makes no claim about them or current production state.
