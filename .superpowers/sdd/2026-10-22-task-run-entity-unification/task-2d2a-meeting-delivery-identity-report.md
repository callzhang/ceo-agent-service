# Task2D2A Meeting delivery identity

Status: locally implemented and focused verification passed at BASE `f8ada9e54`. The Task/Run constructor remains unactivated. No provider, production database, main checkout, push, deploy, or full suite was used.

## Contract

- The existing native table is unchanged: `task_business_keys(id INTEGER PRIMARY KEY, task_id INTEGER NOT NULL REFERENCES tasks(id), business_key TEXT NOT NULL UNIQUE, created_at TEXT, updated_at TEXT)`. The row ID uses ordinary SQLite rowid allocation; no new column or table was added.
- A Meeting's one association is the exact string `meeting-alignment:{source meeting public id}:{meeting_id}`. For the fixture's old source public ID `221` and `meeting_id='meeting'`, this is `meeting-alignment:221:meeting`. Existing source public ID remains provenance; the association is addressed by canonical Task ID.
- New field-map sidecar `task_identity_associations` distinguishes the existing Reply source relation (`business_object_tasks`, one or more source keys per Reply Task) from the Meeting native-only association. The ordinary field map and Reply multi-key semantics remain as before.
- Runtime read interface for the next Meeting writer is `app.task_entity_storage.get_meeting_delivery_key(db, task_id) -> str`. It requires `tasks.kind='meeting_alignment'` and exactly one non-empty stored key; it does not inspect legacy identity maps or reconstruct/parses the key from Task ID.
- No Action, prepared target, receipt, sensitive sibling, provider request, or UUID5 code was changed. Meeting delivery and its actual sender were not run in this unit.

## Forward and inverse

Forward migration derives the key from each source Meeting's public `id` and `meeting_id`, then inserts a TaskBusinessKey with `task_id` set to the canonical Task ID. If the inverse handoff includes the source Meeting, it reuses the handed-off row ID. The key text is always rederived from current source facts.

Inverse inspects the actual parent Task kind. Reply-owned TaskBusinessKeys alone become legacy `business_object_tasks` rows; Meeting-owned keys are omitted from the old schema. Portable `inverse_identity.meeting_business_keys` contains only `{source_meeting_physical_id, task_business_key_row_id}`. The overall inverse identity SHA-256 covers the new list, and `source_context.handoff_required` is true when it is non-empty. A missing source Meeting between inverse and forward is treated as a retired owner: its edge is dropped and no Task or key is recreated.

Inverse includes every live Meeting key row ID in the existing global `sqlite_sequence('tasks')` allocation water, not only passive `owner_highwaters`. Forward rejects duplicate/malformed edge metadata, wrong-kind source physical references, row IDs that collide with source Reply key IDs, unsupported native key-owner kinds, and current Meeting key/source-fact mismatches before target schema replacement. Inverse also preflights the Meeting association timestamps: non-NULL `created_at` or `updated_at` is refused before replacement because this handoff intentionally contains only source physical ID and TaskBusinessKey row ID. The next Meeting creator must leave both association dates NULL; `Task.created_at` owns actual creation time. Reply timestamps are unaffected. The Task target DDL did not change.

## Evidence

`pytest -q tests/test_task_entity_meeting_delivery_identity.py tests/test_task_entity_schema.py tests/test_task_run_entity_assembly.py` — 46 passed. Coverage includes canonical Task ID `1221` with source public ID `221`, exact key read, two JSON serialize/deserialize inverse/forward cycles preserving the Meeting key-row ID, an authentic old `insert_task12` Reply-key write between cycles, retired source Meeting no-resurrection, collision rejection before schema replacement, mismatch refusal, Reply multi-key behavior, and generated assembly field-map parity.

`ruff check` on the six changed Python files, `python -m compileall -q` on those files, and `git diff --check` — passed.

The pinned-BASE RED was reproduced without a historical checkout or source factory changes. A temporary module loaded by `git show f8ada9e54:app/task_entity_schema.py` ran the actual `restore_task_entities` against the same candidate target fixture built from `tests.test_task_entity_schema._source()` and `migrate_task_entities`: `ValueError: unresolved native business_object_tasks parent identity`. The current inverse on a fresh instance of that same fixture succeeds. The fixture contains a Meeting-owned `task_business_keys` association, so this is the concrete baseline failure the association metadata fixes.

The assembled-store regression uses a fresh `AutoReplyStore` database (whose normal bootstrap calls `create_current_database_schema`), inserts and reads a Meeting using unchanged `upsert_meeting_alignment_job` / `get_meeting_alignment_job`, and prepares real primary and sensitive Actions using `prepare_meeting_alignment_message`. It records provider-result JSON through the real receipt API with synthetic provider data only; no provider call occurred. Full `actions` rows for both delivery keys, their `action_receipts`, and the two target-binding State rows are snapshotted before conversion and compare byte-for-byte after forward → helper → inverse → JSON handoff → forward. The primary and sensitive key strings remain `meeting-alignment:1:meeting-real-store` and `meeting-alignment:1:meeting-real-store:sensitive`; their deterministic `uuid5(NAMESPACE_URL, 'dingtalk:' + key)` values are asserted. This proves prepared identity/receipt preservation on the actual Store schema and APIs. It does not claim sender execution, recovery, or the native external-FK/cold-Store gate assigned to Task5.

`pytest -q tests/test_task_entity_meeting_delivery_identity.py` — 7 passed after the timestamp refusal and real-store regression. The wider pre-existing focused selection remains `pytest -q tests/test_task_entity_meeting_delivery_identity.py tests/test_task_entity_schema.py tests/test_task_run_entity_assembly.py`. Task2D2B Meeting creator/producer cutover remains separate; this change does not activate it.
