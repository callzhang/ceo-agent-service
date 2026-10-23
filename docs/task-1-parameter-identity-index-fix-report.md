# Task1 parameter identity index parity

## Result

The unactivated Task1 target now preserves the source identity distinction between a BLOB value and ordinary TEXT containing the BLOB codec's marker JSON. The source accepts both values as distinct keys; the prior `json_extract` target index converted both to identical TEXT and failed while creating `idx_meeting_source_identity` during migration.

The three target indexes use the same scalar key expression for every existing component: when a parameter is a JSON object tagged with `t='blob'`, `unhex(v)` yields the original BLOB key; all other values use `jsonb_extract` unchanged. This means ordinary TEXT such as `{"t":"blob","v":"70726f76696465722d6964"}` remains TEXT. It also preserves NULL as SQL NULL. The original index component order, partial predicates, and uniqueness are unchanged. Finite REAL and infinity values passed through the real source writer have already become TEXT in the source TEXT-affinity fields; the regression records `6.0` and `Inf` as TEXT before migration and as TEXT in the native parameters after migration.

No runtime lookup caller or source schema changed. This is a correction to the unactivated Task1 DDL contract only; Task2, Run, full-copy and runtime cutover gates remain open.

## Evidence

- RED: the source fixture admits a BLOB `meeting_id` and its marker-looking TEXT counterpart as separate keys. Under the prior target `json_extract` index, both expressions produced the same TEXT identity, so target index creation raised `UNIQUE constraint failed: index 'idx_meeting_source_identity'` during migration (the reported Task1 counterexample).
- GREEN: `tests/test_task_entity_schema.py::test_meeting_identity_index_preserves_source_blob_marker_text_and_text_affinity` passes using `_source()`, the actual `source_insert` writer, actual source unique index, `capture_task_entities`, and `migrate_task_entities`. It verifies the source storage classes and values, source rejection of a second equal BLOB, successful migration of distinct BLOB/TEXT keys, and native target rejection of a repeated BLOB key.
- Focused schema/assembly coverage: `pytest -q tests/test_task_entity_schema.py tests/test_task_run_entity_assembly.py` — 34 passed.
- `python -m ruff check app/task_run_entity_definitions.py tests/test_task_entity_schema.py` — passed.
- `python -m compileall -q app/task_run_entity_definitions.py tests/test_task_entity_schema.py` — passed.
- `git diff --check` — passed after the final edits.

## Commits

- Product, regression, candidate SQL, behavior docs and report: `4b1bb37449a7293cb3e9b7e6f02efee8058f3b1a`.
- Claim release: `ebdbdfbd71ce2f317086793bf37ece4b76879594`.

## Artifacts

Updated `app/task_run_entity_definitions.py`, the focused regression in `tests/test_task_entity_schema.py`, regenerated `docs/task-run-entity-assembly-target.sql`, and the candidate contract paragraphs in `docs/architecture.md` and `docs/runtime-mechanism.md`.
