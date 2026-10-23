# Task 3A Run owner Fix 1 report

Corrected the projection getter to return the exact SQLite column value (`object | None`) and widened the setter input annotation to represent native SQLite scalar values. There is no storage-class-specific branch, decoding, validation, or coercion.

Added a source-paired regression using `logical_run_objects()["runs"]`. A valid professional source row stores projection bytes in a TEXT-affinity column; the native setter stores the same bytes and `typeof(...)` result. The API now reads back the original BLOB unchanged. The same test covers ordinary TEXT projection values. Before the fix, the BLOB assertion failed because `get_run_projection` returned a `str` representation; after the fix, the paired test passes.

Added a valid resume RuntimeAttempt with a source session and correction lineage, then updated observed session/transcript values and completion through CAS while asserting lineage is preserved. Expanded caller-rollback coverage to include Run, RuntimeAttempt, provenance, projection, and failure facts, and compared `sqlite_sequence` before and after rollback. The committed Task establishes the transaction boundary for this owner test.

Validation: `pytest -q tests/test_run_entity_storage.py tests/test_task_run_entity_assembly.py tests/test_run_entity_definitions.py` — expected 33 passed; Ruff, compileall, and `git diff --check` also run before commit. No source migration, constructor/runtime activation, provider, CLI, production, push, or deploy behavior was changed. Independent scoped rereview remains pending.
