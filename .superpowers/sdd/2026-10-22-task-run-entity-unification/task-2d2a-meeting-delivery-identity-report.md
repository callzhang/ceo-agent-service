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

Inverse includes every live Meeting key row ID in the existing global `sqlite_sequence('tasks')` allocation water, not only passive `owner_highwaters`. Forward rejects duplicate/malformed edge metadata, wrong-kind source physical references, row IDs that collide with source Reply key IDs, unsupported native key-owner kinds, and current Meeting key/source-fact mismatches before target schema replacement. The Task target DDL did not change.

## Evidence

`pytest -q tests/test_task_entity_meeting_delivery_identity.py tests/test_task_entity_schema.py tests/test_task_run_entity_assembly.py` — 46 passed. Coverage includes canonical Task ID `1221` with source public ID `221`, exact key read, two JSON serialize/deserialize inverse/forward cycles preserving the Meeting key-row ID, an authentic old `insert_task12` Reply-key write between cycles, retired source Meeting no-resurrection, collision rejection before schema replacement, mismatch refusal, Reply multi-key behavior, and generated assembly field-map parity.

`ruff check` on the six changed Python files, `python -m compileall -q` on those files, and `git diff --check` — passed.

The exact pinned-BASE fail-first command/output was not captured before the implementation edits; the RED evidence gate from the brief therefore remains incomplete. The source-only inverse fixture does not contain the assembled outbound Action/receipt tables, so byte-for-byte Action, target-binding, receipt, sensitive sibling, UUID5, and provider-payload comparison remains for the actual assembled migration/cold-copy acceptance. The next unit is Task2D2B Meeting creator/producer cutover; this change does not activate it.
