# Task 2D7I Workbench attachment inverse report

This bounded unit adds an explicit migration/operator file handoff for the historical Workbench reader. It does not activate the converter in runtime paths or alter the frozen predecessor source.

## Exact interfaces and ordering

`capture_workbench_attachment_files(db, managed_root)` snapshots canonical attachment ID, parent ID, exact storage locator, verified SHA-256, and byte count. `stage_workbench_attachment_inverse_files(db, managed_root, snapshot, inverse)` runs only inside the caller-owned FK-off SQL transaction. It resolves restored source owner/parent identities from converter rows and typed identity edges, hard-links only files whose source-compatible locator differs, updates restored source payload locators in that same transaction, fsyncs the staged file and directory, and returns a content-free portable identity/file handoff plus a narrow file plan. The caller commits. `finalize_workbench_attachment_inverse_files(db, plan, identity, root)` rejects open transactions, reads the committed projection and verifies owner, locator, bytes, hash, and size before unlinking only staged old locators. On rollback, `rollback_staged_workbench_attachment_files(plan, identity, root)` prevalidates entries and removes only planned hard links.

Reforward uses `stage_workbench_attachment_forward_files(db, root, portable_identity)` before `migrate_task_entities(...)` in the caller-owned transaction; it restores exact native locators and canonical owner relationships, commits, then calls `finalize_workbench_attachment_forward_files(...)`. No file bodies enter SQLite. The operator wraps each capture→SQL/file stage→commit→finalize window in the existing `WorkbenchStore._attachment_lock`; it releases the lock before launching the predecessor constructor, which acquires that same lock itself.

New native attachments receive UUID source public identities while retaining native integer canonical IDs. Existing source attachment IDs and parent IDs retain their SQLite types. The pure SQL inverse corrects the raw physical identity translation: raw source exceptions resolve through the reverse `(kind, target_id) → source physical ID` map, preserving non-diagonal source5→native4 and source6→native5 mappings. A diagonal fallback requires an actual `(kind, source ID)` row in current native target membership, covering accepted natural-key owners whose identity edge is intentionally absent.

## RED/GREEN and focused checks

First red after adding the strict reverse-map helper:

```text
pytest -q tests/test_task_entity_schema.py::test_inverse_translates_raw_source_physical_ids_through_native_owner_edges tests/test_task_entity_schema.py::test_inverse_preserves_hidden_task12_rowid_legacy_key_for_natural_key_owner tests/test_workbench_native_parent.py::test_native_attachment_inverse_stages_source_locator_and_preserves_bytes tests/test_task_entity_schema.py::test_inverse_handoff_json_keeps_captured_blob_parent_out_of_uuid_file_map
```

Observed: 1 passed, 3 failed. The hidden natural-key owner exposed a captured diagonal source/native row without a `task_run_identity_map` public edge; the other two failures were the same missing proof path. The helper was narrowed to allow the diagonal only when the current native target inventory contains it. The Workbench acceptance then exposed a separate handoff assembly omission: staged legacy UUID attachments whose native locator differed had a file row but their generated identity row was not copied into returned portable metadata. The explicit staging operation now persists those generated identity rows.

Final focused regression command and output:

```text
pytest -q tests/test_task_entity_schema.py::test_inverse_translates_raw_source_physical_ids_through_native_owner_edges tests/test_task_entity_schema.py::test_inverse_preserves_hidden_task12_rowid_legacy_key_for_natural_key_owner tests/test_task_entity_schema.py::test_inverse_handoff_json_keeps_captured_blob_parent_out_of_uuid_file_map tests/test_workbench_native_parent.py::test_native_attachment_inverse_stages_source_locator_and_preserves_bytes
....                                                                     [100%]
4 passed in 3.71s
```

The Workbench hermetic regression calls current `WorkbenchStore` attachment APIs, retains a migrated control file, adds files under both a captured empty parent and a new parent, injects failure on the second hard link, rolls back and verifies original files remain, exercises staged-plan rollback, rejects finalize before commit, finalizes only after commit, JSON round-trips handoff data, and completes two inverse/forward cycles. The typed-BLOB test verifies captured parent identity remains a BLOB and does not get UUID-normalized by the file gate. The mapping regression asserts source5→target4 and source6→target5 while preserving a proven diagonal source8→target8.

An initial broad two-file run was also performed:

```text
pytest -q tests/test_task_entity_schema.py tests/test_workbench_native_parent.py
32 passed, 3 failed in 32.71s
```

Two failures were transient implementation defects above and are covered by the passing focused command. One remaining failure is the unrelated existing `test_new_native_email_task_inverse_uses_predecessor_runtime_identity`, whose call passes a hashed `email-classification:<sha256>` value to `AutoReplyStore._validate_runtime_operation_workload("email_classification", ...)`, while the current validator requires a decimal persisted task ID. This unit does not edit `app/store.py` or that test; it is reported, not changed.

Static verification:

```text
ruff check app/task_entity_schema.py app/task_entity_inverse_files.py tests/test_task_entity_schema.py tests/test_workbench_native_parent.py scripts/validate_task_2d7i_workbench_inverse.py
All checks passed!

python -m py_compile app/task_entity_schema.py app/task_entity_inverse_files.py scripts/validate_task_2d7i_workbench_inverse.py
(exit 0; no output)

git diff --check
(exit 0; no output)
```

## Actual predecessor operator acceptance

Command (fresh work directory generated per invocation):

```text
WORK_DIR=$(mktemp -d /private/tmp/task-2d7i-acceptance-locked-XXXXXX) && python scripts/validate_task_2d7i_workbench_inverse.py --predecessor-root /private/tmp/ceo-task-run-phase2-_ntbufyi/predecessor-c38ce12cf --work-dir "$WORK_DIR"
```

Result: exit 0. `predecessor_sha256=b5c5b92fc0985ef21d865379193a948df171d02a3f8d43186a6023d78f1d4a66`; both `constructor_file` and `reconcile_file` resolved to `/private/tmp/ceo-task-run-phase2-_ntbufyi/predecessor-c38ce12cf/app/workbench/store.py`. Cycle 1 inverse staged 2 files; the full old constructor read both attachments with exact UUID parent/attachment path, content hash, byte count, filename and media type; its attachment row count increased 3→4 after the harmless old API write. Cycle 2 inverse staged 4 files; the full constructor read both new attachments and the original control attachment with matching bytes/hash and row count remained 6→6. Reforward completed after each old-constructor cycle.

## Boundaries and remaining review

The report does not claim runtime activation, whole-phase acceptance, production behavior, native CLI acceptance, or a push/deploy. No full suite was run. The operator acceptance depends on the explicit pinned predecessor directory and fresh `/private/tmp` workdir supplied at invocation. Generic file recovery is intentionally narrow to these exact attachment identities and managed-root locators. Commit is the caller's responsibility; finalize cannot run while the DB transaction remains open. The independent whole-BASE..HEAD SPEC+QUALITY review remains required before the original runtime writer resumes integration.

## Fix round 1 (FIX_BASE `de927e6562651ac5442e1673b0acb6192f7ab6db`)

Addressed the two Important findings from `task-2d7i-workbench-file-inverse-review.md`.

Inverse finalization now establishes the committed projection and exact replacement hash/size before cleanup. It verifies an old locator when it still exists and accepts an already-missing old locator only when the exact staged plan says cleanup was required and the verified committed replacement remains present. Forward finalization applies the symmetric rule: the committed native row, locator, parent, hash, and size plus the verified native file establish completion; an absent source locator is accepted only for a staged cleanup entry. Unchanged locators must still exist. Both retry paths skip only the already-removed planned source.

Portable validation binds each file row's canonical and source public UUIDs to its exact attachment identity, and binds `source_path` to the file entry's parent UUID plus attachment UUID. Forward staging prevalidates all entries before creating any directory or link. For every entry it checks the actual restored attachment row's UUID and parent physical ID, the parent row's UUID, current attachment projection locator/parent, source file hash/size, and native destination under the managed root. A stale/swapped ID, inconsistent parent UUID, or mismatching child path therefore fails before filesystem or database mutation.

The Hermetic Workbench test now injects an interruption after the first old-locator unlink in both cleanup directions, confirms the first locator was removed and the rest remain, then retries from the JSON-portable handoff successfully. It also tampers with swapped attachment identities, a parent UUID/path pair that is internally consistent but disagrees with the actual restored parent, and a child path with the wrong UUID; each attempt asserts no Task rows or file bytes changed. The pre-existing second-hardlink failure and uncommitted-finalize rejection remain covered.

Exact focused command and output:

```text
pytest -q tests/test_workbench_native_parent.py::test_native_attachment_inverse_stages_source_locator_and_preserves_bytes tests/test_task_entity_schema.py::test_inverse_handoff_json_keeps_captured_blob_parent_out_of_uuid_file_map tests/test_task_entity_schema.py::test_inverse_translates_raw_source_physical_ids_through_native_owner_edges tests/test_task_entity_schema.py::test_inverse_preserves_hidden_task12_rowid_legacy_key_for_natural_key_owner
....                                                                     [100%]
4 passed in 2.63s
```

Static checks:

```text
ruff check app/task_entity_schema.py app/task_entity_inverse_files.py tests/test_workbench_native_parent.py
All checks passed!

python -m py_compile app/task_entity_schema.py app/task_entity_inverse_files.py
(exit 0; no output)

git diff --check
(exit 0; no output)
```

The genuine predecessor operator was rerun in a fresh child of the approved task root:

```text
WORK_DIR=$(mktemp -d /private/tmp/ceo-task-run-phase2-_ntbufyi/task-2d7i-fix1-XXXXXX) && python scripts/validate_task_2d7i_workbench_inverse.py --predecessor-root /private/tmp/ceo-task-run-phase2-_ntbufyi/predecessor-c38ce12cf --work-dir "$WORK_DIR"
```

Exit 0. Predecessor digest remained `b5c5b92fc0985ef21d865379193a948df171d02a3f8d43186a6023d78f1d4a66`; both old constructor and reconcile modules loaded from the exact pinned predecessor `app/workbench/store.py`. Two file inverse/forward cycles completed. The actual unchanged predecessor constructor read both newly written files with correct UUID parent/attachment IDs, paths, SHA-256, sizes, filenames, and media type; cycle 1 harmless old API write changed row count 3→4, and cycle 2 retained row count 6→6. The original control attachment and exact hash remained available. This is the bounded Workbench Task migration/operator acceptance only; it is not whole-service, whole-phase, runtime-activation, or production proof.

This fix round changed only the explicit converter/file helper and focused Workbench tests. The two unrelated report appendices remain untouched by this round. No whole suite, provider, native CLI, push, or deploy was run. FIX_HEAD will be recorded after commit.

## Fix round 2 (FIX_BASE `971a4b249f7faf3c5a987352a31b21c9e4da65ea`)

Addressed the remaining Important finding from `task-2d7i-workbench-file-inverse-rereview1.md`: a file entry could change only its numeric `parent_canonical_id` and matching native directory path while keeping the actual source parent UUID/physical ID valid.

The inverse now includes a content-free `workbench_parent_identities` map built from the actual restored `source_physical_by_native` and `source_public_by_native` mappings. The sparse owner edges remain authoritative: forward staging requires a present non-diagonal `workbench_tasks` edge to match the file's canonical parent; when no sparse edge exists, the persisted parent mapping must explicitly establish the diagonal (`source physical ID == canonical ID`). File-entry canonical parent, source parent physical ID, source parent UUID, and native numeric directory are bound to that map and to the actual restored parent row before any directory or link mutation. This retains remapped/new parents without inferring `public ID == physical ID`.

The focused Workbench regression now adds a tamper that changes only `parent_canonical_id` and changes `native_path` to the matching numeric directory. It verifies rejection before any filesystem or Task row changes, alongside the previous attachment-identity, source UUID/path, and retry-finalization cases.

Exact focused command and output:

```text
pytest -q tests/test_workbench_native_parent.py::test_native_attachment_inverse_stages_source_locator_and_preserves_bytes tests/test_task_entity_schema.py::test_inverse_handoff_json_keeps_captured_blob_parent_out_of_uuid_file_map tests/test_task_entity_schema.py::test_inverse_translates_raw_source_physical_ids_through_native_owner_edges tests/test_task_entity_schema.py::test_inverse_preserves_hidden_task12_rowid_legacy_key_for_natural_key_owner
....                                                                     [100%]
4 passed in 2.97s
```

Static checks:

```text
ruff check app/task_entity_schema.py app/task_entity_inverse_files.py tests/test_workbench_native_parent.py
All checks passed!

python -m py_compile app/task_entity_schema.py app/task_entity_inverse_files.py
(exit 0; no output)

git diff --check
(exit 0; no output)
```

The genuine predecessor operator was rerun in fresh workdir `/private/tmp/ceo-task-run-phase2-_ntbufyi/task-2d7i-fix2-NO50uC` with:

```text
WORK_DIR=$(mktemp -d /private/tmp/ceo-task-run-phase2-_ntbufyi/task-2d7i-fix2-XXXXXX) && python scripts/validate_task_2d7i_workbench_inverse.py --predecessor-root /private/tmp/ceo-task-run-phase2-_ntbufyi/predecessor-c38ce12cf --work-dir "$WORK_DIR"
```

Exit 0. Predecessor digest `b5c5b92fc0985ef21d865379193a948df171d02a3f8d43186a6023d78f1d4a66`; constructor and reconciler both resolved to the pinned predecessor `app/workbench/store.py`. Cycle 1 verified both new attachment files and harmless old API write (rows 3→4); cycle 2 verified both files and the retained control attachment (rows 6→6). This remains Task-only Workbench operator acceptance, not whole-service or runtime proof.
