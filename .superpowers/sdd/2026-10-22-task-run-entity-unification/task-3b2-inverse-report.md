# Task3B2 current-fact inverse report

Status: bounded implementation and focused verification complete. This is an
unactivated migration-only inverse for the eight existing source Run identities.
Parent full migration integration, independent frozen-source harness, review,
and release proof remain separate gates.

## API and composition order

`restore_run_entities(db, source_metadata, forward_metadata, *, task_metadata)`
uses the Run capture plus the Run forward result and the actual Task2B forward
metadata returned by `migrate_task_entities`. Run forward metadata now includes
only content-free Run identity edges in addition to the selected source object
catalog, source sequence rows, and captured FK findings. The inverse checks
those identity edges against the current native identity map, checks the exact
assembled native Run-owned catalogs, and rejects a new or missing native Run
identity.

The composed order is Task2B forward, then Run forward. On inverse, restore Run
first while native Tasks still exist, then call Task2B's `restore_task_entities`
with its original source capture and forward result. The focused composition
test then exercises `logical_run_projection` and `finish_logical_run` against
the restored predecessor schema, re-captures and forwards Tasks, and immediately
forwards Runs again.

## Restored facts and boundaries

The inverse reconstructs every selected-current source column, including
structural NULL slots, from the exact selected `logical_run_current_schema`
factory and current native facts. It retains physical Run IDs, raw public-key
SQLite types, professional source namespaces and parent references, schedule
execution Task identities, workload-key transformations, session search Run
IDs, native projection and server-failure facts, and the captured source
sequence rowids/names/values. It removes its Run-owned target tables, indexes,
triggers, and identity map itself. Unrelated schema and sequence rows remain.

Only identities represented by the forward's content-free identity map are
supported. New native Run rows, native cross-owner allocation, and portable
identity handoff are B3 work and are rejected here. The known captured missing
physical Meeting Task edge is retained only for its captured source Run; a new
missing or wrong-kind Task reference is rejected. This report does not claim
new identity allocation, broad composed-copy acceptance, native CLI/UI
acceptance, runtime activation, provider behavior, production migration, or
release.

## Verification

- `pytest -q tests/test_run_entity_schema.py --basetemp=/tmp/task3b2-inverse-runroot/run-20`: **17 passed**.
- `ruff check app/run_entity_schema.py tests/test_run_entity_schema.py`: passed.
- `python -m compileall -q app/run_entity_schema.py tests/test_run_entity_schema.py`: passed.
- `git diff --check`: passed.

The temporary pytest base was a unique child of the task-owned temporary root;
no full suite, production checkout, provider, native CLI, push, or deploy was
used.
