# Task 2D7A Workbench parent and attachment runtime report

Status: **Implementation complete locally; independent whole-BASE..HEAD SPEC/QUALITY review and the later assembled global Task5 gate remain pending.** The native Workbench constructor is not activated.

## Runtime boundary

Workbench parent and attachment reads/writes use assembled native `workbench_tasks` and `workbench_attachments`. Canonical native integer IDs cross the existing string DTO/API boundary as decimal strings. Parent routing, list cursors, archive fencing and provider-session ownership use native IDs; the active Turn fence reads raw queued/running `tasks.status` through native `workbench_turns` ownership. Attachment `client_request_id` remains the UUID business idempotency key, and persisted `storage_path` remains independent filesystem provenance. Reconciliation follows the saved locator under the managed attachment root; it neither infers ownership from path segments nor relocates migrated source files. New writes retain the existing staged file/metadata transaction protocol.

The actual API regression confirmed native attachment ID reuse: migrate two real source attachments with sparse source identity, reconcile away the highest file/row, then POST a replacement through the registered API. Before the fix, SQLite reused the retired canonical ID (`replacement_id == retired_canonical_id == 235`), and the fail-first regression stopped at `assert int(replacement_id) > retired_canonical_id`; this allowed its sparse inverse identity to refer to a different upload. The target attachment PK is now `INTEGER PRIMARY KEY AUTOINCREMENT`; explicit inverse identity carries the current attachment allocator high-water, and reforward restores it. The regression verifies replacement request ID, filename, media type, content size/hash, new source UUID identity, exact bytes and parent ownership; it performs a second portable inverse/reforward and proves the next API upload receives a greater canonical ID while retaining its own request/hash.

The deterministic target DDL is emitted by `app/task_run_entity_definitions.py::task_run_entity_objects()` and checked in as `docs/task-run-entity-target.sql`. Review found that snapshot stale: this task's AUTOINCREMENT delta was missing, along with pre-existing generator drift from earlier units (ten generated objects missing and other declarations/indexes changed). The complete snapshot was regenerated from the current generator; the broader pre-existing differences are recorded as artifact synchronization, not attributed to Task2D7A. The regenerated `workbench_attachments` SQL is exactly `CREATE TABLE workbench_attachments (id INTEGER PRIMARY KEY AUTOINCREMENT,"workbench_task_id" INTEGER NOT NULL REFERENCES workbench_tasks(id),"client_request_id" TEXT NOT NULL,"filename" TEXT NOT NULL,"media_type" TEXT NOT NULL,"size_bytes" INTEGER NOT NULL,"content_sha256" TEXT NOT NULL,"storage_path" TEXT NOT NULL,"created_at" TEXT NOT NULL DEFAULT current_timestamp)`. Its SHA-256 is `528e7a6467dcc81030cf11356e6c2b9adfb6eb1151024d490b684432272130a2`; deterministic `task_run_field_map()` SHA-256 remains `48597a8ff5c98265172e0f7c59bd620a55aa41d022e4c04b77722171268e83b5`.

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

## Fix1 — tracked target SQL snapshot

The review identified that the tracked SQL snapshot did not match the current generator. Regenerated all 76 declared objects into `docs/task-run-entity-target.sql` with `serialize_schema_sql(task_run_entity_objects())`. The parsed tracked field map already matched `list(task_run_field_map())` exactly, so it was not rewritten. Source factories remain unchanged. The full snapshot includes prior generator deltas from other units as artifact synchronization; the Task2D7A-specific change is the attachment AUTOINCREMENT declaration.

Commands and results:

```text
python -c "import hashlib,json; from pathlib import Path; from app.schema_sql import serialize_schema_sql; from app.task_run_entity_definitions import task_run_entity_objects,task_run_field_map; target=Path('docs/task-run-entity-target.sql'); sql=serialize_schema_sql(task_run_entity_objects()); target.write_text(sql); current=json.loads(Path('docs/task-run-entity-field-map.json').read_text()); generated=task_run_field_map(); print('sql_objects='+str(len(task_run_entity_objects()))); print('sql_bytes='+str(len(sql.encode()))); print('sql_sha256='+hashlib.sha256(sql.encode()).hexdigest()); print('field_map_equal='+str(current==list(generated))); print('field_map_rows='+str(len(generated))); assert current==list(generated)"
sql_objects=76
sql_bytes=25027
sql_sha256=989d78f3898a08f7f554a2fa9a7d31f79260a66cf93671808363191cb14faf37
field_map_equal=True
field_map_rows=287

python -c "import json,sqlite3; from pathlib import Path; from app.task_run_entity_definitions import task_run_entity_objects,task_run_field_map; from app.task8_storage import register_task8_functions; from app.schema_sql import serialize_schema_sql; generated=task_run_entity_objects(); a=sqlite3.connect(':memory:'); b=sqlite3.connect(':memory:'); register_task8_functions(a); register_task8_functions(b); a.executescript(Path('docs/task-run-entity-target.sql').read_text()); b.executescript(serialize_schema_sql(generated)); objects=lambda db:{r[0]:r[1] for r in db.execute(\"select name,sql from sqlite_schema where name not like 'sqlite_%'\")}; assert objects(a)==objects(b); cols={r[1]:r for r in a.execute('pragma table_info(workbench_attachments)')}; assert cols['id'][2].upper()=='INTEGER' and cols['id'][5]==1; assert a.execute(\"select sql from sqlite_schema where name='workbench_attachments'\").fetchone()[0]==generated['workbench_attachments']; assert a.execute('pragma foreign_key_check').fetchall()==[]; field_map=json.loads(Path('docs/task-run-entity-field-map.json').read_text()); assert field_map==list(task_run_field_map()); print('target_objects_equal=True; attachment_pk=INTEGER AUTOINCREMENT; foreign_key_check=0; field_map_equal=True')"
target_objects_equal=True; attachment_pk=INTEGER AUTOINCREMENT; foreign_key_check=0; field_map_equal=True

pytest -q tests/test_task_run_entity_definitions.py
14 passed

pytest -q tests/test_run_entity_definitions.py::test_committed_run_sql_executes_after_task_and_native_schedule_config_prerequisites
1 passed
```

Task 2D7A: fix round 1/5 (1 addressed, 0 open — regenerate tracked target SQL and correct report artifact claim; commit recorded after tests).

## Remaining boundary

The accepted Task2D7I file coordinator remains explicit migration tooling. No Workbench Turn lifecycle, runtime constructor selection, Task5/global assembly, release or production behavior is claimed. Independent SPEC/QUALITY review must cover original BASE `eff9c4695ec50be92faf237e24af6a8dcaf8092e` through the final integrated HEAD and account for the interleaved Task2D7I prerequisite separately.
