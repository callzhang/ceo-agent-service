# Task 2D7A Workbench parent and attachment runtime report

Status: **Implementation complete locally; independent whole-BASE..HEAD SPEC/QUALITY review and the later assembled global Task5 gate remain pending.** The native Workbench constructor is not activated.

## Runtime boundary

Workbench parent and attachment reads/writes use assembled native `workbench_tasks` and `workbench_attachments`. Canonical native integer IDs cross the existing string DTO/API boundary as decimal strings. Parent routing, list cursors, archive fencing and provider-session ownership use native IDs; the active Turn fence reads raw queued/running `tasks.status` through native `workbench_turns` ownership. Attachment `client_request_id` remains the UUID business idempotency key, and persisted `storage_path` remains independent filesystem provenance. Reconciliation follows the saved locator under the managed attachment root; it neither infers ownership from path segments nor relocates migrated source files. New writes retain the existing staged file/metadata transaction protocol.

The actual API regression confirmed native attachment ID reuse: migrate two real source attachments with sparse source identity, reconcile away the highest file/row, then POST a replacement through the registered API. Before the fix, SQLite reused the retired canonical ID (`replacement_id == retired_canonical_id == 235`), and the fail-first regression stopped at `assert int(replacement_id) > retired_canonical_id`; this allowed its sparse inverse identity to refer to a different upload. The target attachment PK is now `INTEGER PRIMARY KEY AUTOINCREMENT`; explicit inverse identity carries the current attachment allocator high-water, and reforward restores it. The regression verifies replacement request ID, filename, media type, content size/hash, new source UUID identity, exact bytes and parent ownership; it performs a second portable inverse/reforward and proves the next API upload receives a greater canonical ID while retaining its own request/hash.

The deterministic target DDL is emitted by `app/task_run_entity_definitions.py::task_run_entity_objects()`; no static generated artifact exists to update. The regenerated `workbench_attachments` SQL is exactly `CREATE TABLE workbench_attachments (id INTEGER PRIMARY KEY AUTOINCREMENT,"workbench_task_id" INTEGER NOT NULL REFERENCES workbench_tasks(id),"client_request_id" TEXT NOT NULL,"filename" TEXT NOT NULL,"media_type" TEXT NOT NULL,"size_bytes" INTEGER NOT NULL,"content_sha256" TEXT NOT NULL,"storage_path" TEXT NOT NULL,"created_at" TEXT NOT NULL DEFAULT current_timestamp)`. Its SHA-256 is `528e7a6467dcc81030cf11356e6c2b9adfb6eb1151024d490b684432272130a2`; deterministic `task_run_field_map()` SHA-256 remains `48597a8ff5c98265172e0f7c59bd620a55aa41d022e4c04b77722171268e83b5`.

The test `test_native_reconciliation_preserves_target_injected_source_style_locator` manually inserts a source-style UUID locator into a native target row. It is not a source-migration test. Actual migration is covered by the native API regression and the explicit Task2D7I operator.

## Evidence

The native-target first-run writer previously failed because the source-shaped code tried to insert removed `_kind` (`sqlite3.OperationalError: table tasks has no column named _kind`). The assembled-target test now passes. Store methods in that fixture are bound via `object.__new__`, which does not claim native constructor activation.

Commands and results from the assigned worktree:

```text
pytest -q tests/test_workbench_native_parent.py::test_highest_reconciled_attachment_identity_is_not_reused_across_portable_inverse
1 passed

pytest -q tests/test_task_run_entity_definitions.py tests/test_workbench_native_parent.py
22 passed

pytest -q tests/test_task_run_entity_definitions.py tests/test_workbench_native_parent.py tests/test_task_entity_schema.py
48 passed, 2 failed
```

The two broader-selection failures were `test_native_input_new_fact_below_global_water_keeps_canonical_id_on_reforward`, whose exact inverse-identity dictionary expected no Workbench allocation-highwater key, and `test_new_native_email_task_inverse_uses_predecessor_runtime_identity`, which fails in unchanged `AutoReplyStore._validate_runtime_operation_workload` because that test passes a non-decimal Email workload key. The direct expected handoff was updated to the new contract and passed in the focused rerun below. The unrelated validator/test mismatch remains outside Task2D7A and was not changed.

Final focused rerun:

```text
pytest -q tests/test_task_run_entity_definitions.py tests/test_workbench_native_parent.py tests/test_task_entity_schema.py::test_native_input_new_fact_below_global_water_keeps_canonical_id_on_reforward
23 passed
```

The explicit unchanged-predecessor operator was rerun after the allocator change:

```text
WORK_DIR=$(mktemp -d /private/tmp/ceo-task-run-phase2-_ntbufyi/task-2d7a-resume-XXXXXX) && python scripts/validate_task_2d7i_workbench_inverse.py --predecessor-root /private/tmp/ceo-task-run-phase2-_ntbufyi/predecessor-c38ce12cf --work-dir "$WORK_DIR"
```

Result: exit 0. It used predecessor SHA `b5c5b92fc0985ef21d865379193a948df171d02a3f8d43186a6023d78f1d4a66`; both cycles constructed the unchanged old WorkbenchStore, preserved the captured source control attachment bytes/hash and parent facts, retained new API-created attachments with their source UUIDs and metadata, allowed the old API write in cycle 1, and completed reforward. The run-owned output lives under the reported fresh task directory; no pre-existing temp directories were touched.

Ruff, compileall and `git diff --check` passed for the changed Python files. The exact deterministic assembled target DDL test passes with AUTOINCREMENT, and source schema factories were not modified. No full suite, production, provider, CLI, constructor activation, main merge, push or deploy was run.

## Remaining boundary

The accepted Task2D7I file coordinator remains explicit migration tooling. No Workbench Turn lifecycle, runtime constructor selection, Task5/global assembly, release or production behavior is claimed. Independent SPEC/QUALITY review must cover original BASE `eff9c4695ec50be92faf237e24af6a8dcaf8092e` through the final integrated HEAD and account for the interleaved Task2D7I prerequisite separately.
