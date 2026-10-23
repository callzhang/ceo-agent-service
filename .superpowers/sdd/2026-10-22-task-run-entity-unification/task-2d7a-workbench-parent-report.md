# Task 2D7A Workbench parent and attachment runtime report

Status: **PARTIAL — native parent CRUD/API and attachment runtime implemented; Task 2D7A remains incomplete pending the separate inverse-tool/full-constructor/portable-cycle gate.** This report does not claim native constructor cold-start acceptance.

## Implementation boundary

The runtime now uses the assembled native `workbench_tasks` and `workbench_attachments` tables. `workbench_tasks.id` and `workbench_attachments.id` are native integer identities exposed through the existing string DTOs as canonical decimal strings. Attachment ownership uses `workbench_attachments.workbench_task_id`; source `client_request_id` remains a UUID business key, and source `storage_path` remains the independent file locator. Attachment bytes remain filesystem-backed.

Task create/get/summary/list/paginated-list/rename/archive and parent-path API operations use native integer parent identity. Summary and archive fencing read raw queued/running state by joining native `workbench_turns` to its `tasks.status`. Provider-session update resolves the parent from the native turn row. Existing API routes and DTOs remain in place; task IDs are accepted as canonical positive decimal path strings, while turn and artifact IDs retain UUID parsing.

Attachment save/list use native ownership and native attachment IDs, retaining UUID request-key idempotency and the existing staged-file, metadata transaction, rename, fsync, and rollback cleanup protocol. Current attachment reconciliation now validates persisted `storage_path` against the managed root and file protocol rather than inferring parent/attachment identity from locator path components. This preserves captured UUID-locator files after migration. No source predecessor implementation was edited.

## Evidence

Before implementation, the new native-target CRUD test failed at the original Workbench parent writer: against the actual assembled target DDL it attempted to insert the removed `_kind` source column (`sqlite3.OperationalError: table tasks has no column named _kind`). The original source-shaped implementation therefore does not satisfy the target contract. The native implementation then passed the same assembled-target fixture. The tests bind current WorkbenchStore methods to the target schema with `object.__new__`; this intentionally does **not** exercise the still-source `WorkbenchStore` constructor.

The fixture executes the actual Task/Run assembled target DDL. Focused tests cover native parent CRUD/state/cursor/archive fencing, running-turn fencing, native-turn provider-session ownership, numeric attachment API IDs, request-key replay/conflict, exact stored path/hash/bytes, preservation of a migrated historical UUID attachment locator, API create/get/rename/list/upload, and rollback cleanup when metadata commit fails.

Commands run from the assigned worktree:

```text
ruff check app/workbench/store.py app/workbench/api.py tests/test_workbench_native_parent.py
python -m compileall -q app/workbench/store.py app/workbench/api.py tests/test_workbench_native_parent.py
git diff --check
pytest -q tests/test_workbench_native_parent.py
```

Result: Ruff passed, compileall passed, diff check passed, **6 focused tests passed**.

## Remaining gate and limits

The actual unchanged predecessor proof in `workbench-attachment-inverse-preparation.md` establishes that Task inverse currently leaves a newly created native attachment incompatible with the frozen source reader; the predecessor constructor deletes the row and file, including when the public ID is UUID-substituted but the persisted path remains independent. The approved remedy is a separate explicit inverse filesystem handoff that allocates source-compatible attachment identity, relocates only newly created native attachment files during inverse, updates restored source `storage_path`, preserves source bytes until verified commit, and carries portable data for exact re-forward restoration. This implementation does not add that tool or alter the frozen predecessor.

The genuine source constructor has not been changed or claimed native. There is no accepted cold native constructor, actual API-to-inverse integration, unchanged-predecessor post-inverse preservation, or two-cycle portable re-forward proof yet. Those remain required before Task 2D7A can be marked done. No full suite, production operation, provider action, CLI action, push, or deploy was run.
