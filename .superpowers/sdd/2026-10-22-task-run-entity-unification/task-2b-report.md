# Task2B report — forward slice only

Status: **incomplete; inverse handoff required**. Commit `da582aec3` implements the forward current-lifecycle Task/domain conversion. It does not provide the required current-fact inverse, inverse identity allocator, inverse→reforward round trip, or native-new-fact collision proof. Do not treat Task2B as accepted or use this as a publication candidate.

`app/task_entity_schema.py` exposes:

- `capture_task_entities(db)`: requires a caller-owned FK-off transaction; validates the exact `task_lifecycle_current_schema.task_lifecycle_objects()` declaration and captures source objects, `tasks` sequence and external Tasks FK/trigger inventory.
- `migrate_task_entities(db, metadata)`: stages the 20 Task lifecycle projections and decoded lifecycle scalar fields transiently, installs native Task/domain owners and returns counts, identity/public-owner edges, sparse raw-key and reference exceptions, and source allocator/declaration metadata. Caller retains transaction, FK-check and broader Run/Action/State composition ownership.
- `resolve_source_public_owner(...)` and `resolve_dispatcher_claim_source_id(...)`: exact typed identity handoff for composed Action-parent and Task-backed State rewrites. Unresolved State history returns `None`; it does not manufacture a Task.

The focused fixture uses the actual current lifecycle declaration and writer. It covers all 20 families, different physical/public input IDs with independent current/claimed pointers, selected parent identity translations, extra lifecycle dispatch scalar facts and SQL NULLs, declaration rejection, and caller rollback. Semantic helper tests cover unresolved Workbench references and typed Action parents. Last verified focused command, including the two uncommitted additions (`identity_edges`/`public_owner_rows` return metadata and rollback regression test): `pytest -q tests/test_task_entity_schema.py tests/test_task_entity_migration.py` — 8 passed. Ruff and `py_compile` passed before those additions; the additions remain uncommitted.

The uncommitted product files are `app/task_entity_schema.py` and `tests/test_task_entity_schema.py`. They contain only the two additions identified above. No runtime activation, publication, provider/native CLI, production access, or full-suite test occurred.
