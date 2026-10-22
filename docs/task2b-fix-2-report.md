# Task2B complete owned inverse fix round 2 report (2026-10-22)

Status: the owned-schema inverse blocker from the independent round-two review is reproduced and fixed. Parent acceptance remains pending, including the full frozen 37,358-row harness and the separate native-created identity-collision slice.

`restore_task_entities()` now stages current Task/domain facts, then drops every exact target table declared by `task_run_entity_objects()` before rebuilding the captured source `tasks` declaration. SQLite retires attached indexes and triggers with each owner. The inverse does not sweep by name or touch non-owned tables. The source table, indexes, triggers, current facts and original Task allocator entry are then restored by the existing source reconstruction path. The inverse returns `sparse_identity_metadata` containing identity edges, raw-key exceptions and unresolved-reference markers before `task_run_identity_map` is retired.

Composition ordering: a future Run/Action/State coordinator must read and stage any needed current facts from the owned native Task/domain relations before calling this inverse. The caller remains responsible for the FK-off transaction and rollback. The returned sparse identity metadata remains available after the native identity map table has been dropped. This inverse does not claim a complete Run/Action/State coordinator or full-store cold start.

The earlier reforward tests no longer drop native owner tables manually. The mutated-current-fact test now verifies the genuine predecessor `task20_schema_is_current()` predicate, reads the natural-key family using the predecessor Task12 ROWID projection, performs an `update_task12()` point write, and immediately forwards from the resulting source schema. The hidden ROWID test also refowards directly. A sentinel test proves exact catalog and all-sequence preservation for unrelated table/index/trigger/data. The rollback test captures all owned table rows, full schema catalog and every sequence entry before forcing an inverse constraint failure after retirement began, rolls back as caller, and compares the native state exactly.

Fail-first evidence: on clean pinned `3da27e551`, the no-cleanup tests failed because `task20_schema_is_current()` was false after inverse. After inverse retirement, the same tests pass; all manual `DROP TABLE` cleanup was removed from the test cases.

Verification:

- `pytest -q tests/test_task_entity_schema.py tests/test_task_entity_migration.py` — 17 passed.
- `ruff check app/task_entity_schema.py tests/test_task_entity_schema.py` — passed.
- `python -m compileall -q app/task_entity_schema.py tests/test_task_entity_schema.py` — passed.
- `git diff --check` — passed.

No production/source-database write, provider/native CLI, runtime activation, push, or deploy occurred. The parent-provided frozen source harness remains the next full-population verification step after this commit.
