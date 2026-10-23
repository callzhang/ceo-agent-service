# Task 1 Feedback batch parity repair — 2026-10-22

## Result

The Task1 target now preserves the source contract for `feedback_processing_items.batch_id`. It remains required TEXT with its existing batch lookup index, while the target no longer invents an FK to `feedback_processing_batches`. The batch table and all other actual Feedback owner relationships remain declared as before.

This repairs both observed failure modes without rewriting data: empty batch sentinels and nonempty source-valid unresolved batch strings remain unchanged. The source's assigned batch values also remain unchanged.

## Evidence and checks

The parent read the provided fresh source snapshot read-only and reported 52 Feedback items: 50 with `batch_id=''` and two matching actual batches; all three rounds matched. The source has no `feedback_processing_items.batch_id` FK or existence trigger. The only Task FK finding in that source concerns the separate `runs(190042) -> tasks(1)` edge and is outside this repair.

The new source-backed regression uses the actual current Task12 writer and Task2B conversion. For each of `''`, `batch-a`, and `historical-missing`, it checks the source write, forward conversion, inverse, and re-forward value. All three cases pass. A separate target-schema regression inserts both empty and unresolved values with foreign keys enabled and verifies the retained `task_id -> tasks.id` FK is still present.

Regenerated schema SQL artifacts execute successfully: the Task base artifact creates 66 objects and the full Task/Run assembly artifact creates 473 objects; both have exact expected object-name sets and empty `foreign_key_check` results. The Run-adoption artifact is unchanged by the declaration change.

Checks passed:

- Focused Task/Run and source conversion selections: 20 passed, 31 deselected.
- Full `tests/test_task_run_entity_assembly.py` and `tests/test_task_run_entity_definitions.py`: 28 passed.
- `ruff check` on the three changed Python files.
- `py_compile` on the three changed Python files.
- `git diff --check`.

All pytest runs used unique `--basetemp` child directories under `/private/tmp/ceo-task-run-phase2-_ntbufyi/`.

## Scope boundary

This is a local schema-contract and regression repair only. No migration was run on the source snapshot or production database. No runtime callers, provider behavior, migration policy, push, or deployment changed. The parent retains the separate Task/Run integration and release gates.
