# Task 3A native Run owner validation

`app.run_entity_storage` is a low-level storage owner over the Task/Run target DDL assembled by `task_run_entity_assembly_objects()`. It accepts a caller-owned SQLite connection, performs no transaction boundary or lifecycle decision, and returns stored SQL facts. Run creation omits the primary key and leaves `created_at` to the actual DDL default when the caller has no source clock. CAS compares only caller-selected fields and writes only caller-supplied fields; a stale match returns `None`.

The primitive covers native Run and RuntimeAttempt rows and the existing typed `run_provenance`, `run_projections`, and `run_failures` children. RuntimeAttempt correction lineage immutability, parent/workload mapping, status, ordinal, and active-route rules are enforced by assembled SQLite DDL. Session and transcript observation fields remain updateable. Projection values remain separate from candidate adoption, and reads return the raw SQLite value and storage class without coercion. Failure children record server-side structured failures and do not store CLI logs.

Focused tests execute the actual assembled object declarations with `PRAGMA foreign_keys=ON` and actual native Task storage. They cover all nine Task kinds with absent Run role/revision facts, valid and invalid professional provenance, one Run with no attempts, multiple parented attempts, a standalone attempt, unique ordinal and route constraints, fresh and resume session/transcript observations through completion, all three correction lineage fields independently, source-paired TEXT and BLOB projection value/storage-class preservation, typed projection/failure children, unknown fields, stale CAS, and caller rollback of Run, RuntimeAttempt, provenance, projection, failure, and `sqlite_sequence` state.

This foundation does not activate a constructor, migrate source Run rows, close domain callers, route execution, persist adopted results, or establish the complete native CLI/runtime lifecycle. Those remain separate integration and review gates. The predecessor constructor's seven reproduced failures remain a root-owned cutover obligation; they are not tested or claimed by this primitive unit.

## Task3B2 existing-identity current-fact inverse

The migration-only `restore_run_entities(db, source_metadata, forward_metadata,
*, task_metadata=...)` inverse restores the exact selected-current
`logical_run_current_schema.current_run11_objects()` declaration from the
current native owners. Callers first run the Task2B forward conversion, then
the Run forward conversion; on inverse, restore Run while native Tasks still
exist, then restore Tasks. Pass the actual Task2B forward metadata returned by
`migrate_task_entities`. The inverse requires the caller-owned FK-off
transaction and does not commit it.

It reconstructs all eight existing source kinds from the typed identity map and
native Run, provenance, projection, RuntimeAttempt, failure, schedule
occurrence, Workbench event, session index, runtime sample, and setup event
facts. It restores source physical IDs, raw public-key values and storage
classes, typed Task and professional Run references, source workload keys,
structural NULL slots, and captured source `sqlite_sequence` rows. Current
native edits are preserved. New native Run identities, unsupported kinds, or
missing existing native owners are rejected; their allocation and portable
identity handoff are reserved for B3. The inverse retires its owned native Run
tables, indexes, triggers, and identity map itself, so immediate forward
conversion can reuse the exact selected-current catalog. This bounded unit
does not establish composed Task/Run cold writes, runtime activation, full
copy/inverse, CLI/UI, or release acceptance.
