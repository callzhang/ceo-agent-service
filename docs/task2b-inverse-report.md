# Task2B current-fact inverse report (2026-10-22)

Status: local implementation and focused checks pass. This is not the combined Task2B acceptance or runtime activation.

Implemented signature: `restore_task_entities(db, source_metadata, forward_metadata)`. The caller owns the FK-off transaction and rollback. The inverse reads current native Task/domain rows, sparse public identity and raw-key metadata, and owner-scoped unresolved Workbench reference markers. It rebuilds the captured current lifecycle Tasks declaration and preserves its original allocator entry. It does not retain source row bodies or replay a pre-forward snapshot.

Implementation commit: `93a8100f4` (`Implement current-fact Task20 inverse`).

The forward handoff metadata now also includes `identity_edges` and `public_owner_rows`, preserving the prior worker's uncommitted changes. A separate deterministic physical lifecycle crosswalk declares all 12 scalar fields for each family: the nine executable Task kinds map to current `tasks` fields; eleven auxiliary domain families explicitly have no lifecycle owner and restore SQL NULL in these scalar slots.

The focused fixture covers 20 families and 21 rows. Before/after comparison includes every logical field value and SQLite `typeof()`, source physical IDs, raw key values/types, physical parent/current/claimed input pointers, all 12 decoded lifecycle scalar values/types and the source Task sequence row. The native-edit test changes current status/lease facts, a request parameter, current input text, a business key and a business result, then checks the values after inverse and after reforward. Workbench references cover unresolved numeric text `202`/`203` while those native IDs exist and exact restoration of resolved `wb-task-a`/`wb-turn-a` references. An admitted BLOB public identity remains BLOB through the persistent sparse identity map and resolver. Extra target DDL is rejected before mutation; a source constraint failure during inverse is rolled back by the caller.

Verification:

- `pytest -q tests/test_task_entity_schema.py` — 9 passed.
- `ruff check app/task_entity_schema.py app/task_lifecycle_definitions.py tests/test_task_entity_schema.py` — passed.
- `python -m compileall -q app/task_entity_schema.py app/task_lifecycle_definitions.py tests/test_task_entity_schema.py` — passed.
- `git diff --check` — passed.

Remaining acceptance belongs to the parent slice: native-created Task/input/domain facts with independent-table ID collisions, collision-free allocation above the global source Task water, and content-free inverse identity metadata accepted by reforward. That must verify exact restored IDs/references and valid UUID namespace for new native Workbench IDs. This implementation does not claim whole-database FK closure or combined-copy readiness. No runtime activation, provider/native CLI, source database write, push or deploy was performed.
