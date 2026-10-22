# Task2B inverse fix round 1 report (2026-10-22)

Status: both mandatory independent-review findings reproduced against the clean pinned implementation at `29ae9c024`, fixed, and covered by focused regressions. Parent acceptance of the full frozen source copy and the separate new-native-fact collision slice remain outstanding.

The parameter inverse now reads `json(parameters_json)` for the complete native object, parses it once, and applies the existing native scalar decoder only to its already-typed JSON value. This retains the distinction between ordinary TEXT whose characters look like a codec object and actual encoded BLOB/REAL/infinity scalars. The new source/current-fact regression confirmed `meeting_id='{"t":"blob","v":"ff"}'` remains identical TEXT before forward, as native parameter TEXT after current edit, and after inverse (`typeof='text'`). Actual tagged BLOB, REAL, positive infinity and negative infinity remain decoded; TEXT affinity produces the source values/types `b'\xff'/blob`, `'6.0'/text`, `'Inf'/text`, and `'-Inf'/text`.

Forward metadata now retains the physical `legacy_key` value and SQLite type for source families whose public logical projection has no `id` or `task_id`. These are the four natural-key Task12 families: `business_object_tasks`, `email_historical_candidates`, `feedback_processing_batches`, and `feedback_processing_items`. Inverse binds the stored raw value directly; it does not guess from a business key. The new regression uses `business_object_tasks` with physical Task ID 224 and legacy key `'7'`, verifies the source projection after inverse, and verifies the same scalar edge on reforward. The separate 20-row technical identity crosswalk now describes `legacy_key` ownership/semantics for every source family; the previous 287 logical-field/physical-ID map is explicitly not a complete stored-column inventory.

Metadata size measured only on the checked-in 21-row focused fixture: `raw_key_exceptions` contains 7 scalar rows encoded as 628 compact JSON bytes. This is a fixture measurement, not a whole-population size estimate. The parent’s pre-fix frozen-source probe had identified 8,680 actual differing hidden-rowid keys across the four families; post-fix full-population recount is reserved for parent acceptance.

Fail-first evidence: before implementation, both new regressions failed against the pinned implementation: marker-shaped TEXT restored as `b'\xff'/blob`, and the hidden Task12 key was absent from `raw_key_exceptions`. After implementation:

- `pytest -q tests/test_task_entity_schema.py tests/test_task_entity_migration.py` — 16 passed.
- `ruff check app/task_entity_schema.py app/task_lifecycle_definitions.py tests/test_task_entity_schema.py` — passed.
- `python -m compileall -q app/task_entity_schema.py app/task_lifecycle_definitions.py tests/test_task_entity_schema.py` — passed.
- `git diff --check` — passed.

Implementation remains unactivated. No source database write, provider/native CLI, production data access, push, or deploy occurred.
