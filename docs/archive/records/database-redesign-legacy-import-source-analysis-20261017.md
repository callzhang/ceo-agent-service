# Legacy import mapping: source analysis before the next unit

Planning only while the canonical Email component undergoes independent review.
No runtime, table, field, counter, source body or policy is changed here. The
accepted actual13 consistent snapshot (receipt6b8c310c) has0 LegacyLink rows.
Zero rows does not prove that the live import behavior can simply be deleted.

## Existing source facts and actual caller

The exact declaration in source capsule79f04ccf has12 fields: id;
signal_id/task_id/cluster_id/anchor_id/project_id/project_candidate_id/
attention_item_id; work_project_id/work_todo_id/work_update_id; created_at.
Its first seven endpoints have an exactly-one CHECK; its last three source
endpoints also have exactly-one and individual UNIQUE constraints. Multiple
legacy sources may target one formal object, but one legacy source has at most
one import mapping. Public identity roles remain source-specific.

`apply_task_semantic_import_manifest` in app/task_semantic_import.py checks the
manifest/source digest before writing, runs each item in business_task_transaction,
looks up an existing mapping by its unique old-object column and verifies the
existing mapping before skipping it. It creates a mapping only after either
matching an official Project or creating a formal Task. The actual source writer
uses only project_id/task_id target columns. History-only items create no mapping.
It returns created/skipped/formal/match/history counts, not a mapping row ID.

The current matcher uses task/project IDs and supporting evidence/digest. This
bounded scan finds no caller reading the mapping id/created_at as business output,
but that is a cancellation candidate, not a current approved retirement. Other
schema endpoints exist in DDL even though this actual writer does not use them;
no new source snapshot or proof of permanent non-use is asserted.

## Why moving the pointer is not the whole constraint migration

The old inbound mapping FK also prevents deleting/changing a referenced legacy
WorkProject/WorkTodo/WorkUpdate. Moving an outgoing pointer onto that source row
would lose this restriction unless its existing behavior is explicitly preserved.
WorkUpdate guard predicates currently query business_legacy_links. Changing those
predicates is a translation of existing constraints, not permission to add new
policy. A simple new business_id pointer alone is not an exact source migration.

## Bounded destination options, no target DDL selected yet

- Inline the actual target FK on its old-object owner. This follows direct-owner
  preference, but must resolve source-specific target scope, existing project_id
  name meanings, deletion/update restrictions, current-fact inverse and old
  public lookup behavior. Do not invent unrelated business fields.
- Represent the already-existing import result in Actions, alongside execution
  outcomes and receipts. Its source row is written only after the internal import
  succeeds and is used for idempotent import lookup, so this is an existing
  operation result rather than a new generic relations feature. A finite mapping
  can preserve the actual old fields, IDs, unique old-source keys and FKs; no new
  provider receipt or business fact is fabricated. This removes one table without
  adding Business mutation guards merely to recover an old inbound FK.

The second option is the smaller preservation path to evaluate after canonical
Email caller acceptance, using the existing typed Actions mechanisms. It is a
recommendation for exact next-plan analysis, not an activated schema or an excuse
to rename arbitrary relations as actions. Capture the then-current exact Actions
and dependency graph first; preserve current facts and source/inverse identities.
No implementation is dispatched by this note.

After Email13→12, retiring this physical mapping table would produce11 ordinary
tables while SourceDocuments is still present. That count alone is not the
approved final structure: SourceDocuments must still become only accurate shared
provenance/inline identity with real source access and body lifecycle. Do not
claim final10core+ledger until both names and data semantics match the spec.
