# Task2B native-created Task identity and inverse handoff

Date: 2026-10-22

This report covers the bounded, unactivated Task entity conversion only. It does not cover the parent-owned full database copy, caller integration, publication journal implementation, runtime activation, providers, native CLI, production, push, or deploy.

## Interface and sparse metadata

`restore_task_entities(db, source_metadata, forward_metadata)` now returns two JSON-serializable values:

```json
{
  "inverse_identity": {
    "version": 1,
    "owners": [
      {"source_kind": "reply_task_inputs", "source_physical_id": 416922, "canonical_id": 404225}
    ]
  },
  "source_context": {
    "version": 1,
    "handoff_required": true,
    "handoff_sha256": "<sha256 of canonical JSON inverse_identity>"
  }
}
```

The IDs above are synthetic test IDs. The handoff contains only typed source-kind and identity scalars for facts whose restored source physical ID differs from the current native canonical ID. `migrate_task_entities(db, metadata, *, source_context=None, inverse_identity=None)` consumes it. When context says a handoff is required, the forward conversion rejects an omitted handoff and rejects a digest mismatch before creating staging tables or mutating owner tables. Both values survive a JSON encode/decode round-trip.

The forward metadata no longer serializes `public_owner_rows`. The inverse rebuilds typed owner indexes in memory from actual native owner tables, sparse `identity_edges`, raw hidden-key exceptions, and `task_run_identity_map`. It retains one source owner high-water integer per kind to distinguish captured owners from subsequently appended owner IDs. The source-wide `tasks` AUTOINCREMENT sequence is captured separately and remains the allocation boundary for new restored source rows. New source physical IDs are assigned above that water while all known source physical IDs are reserved globally.

The saved context and handoff belong in future release-journal or backup metadata outside the business database. From a fresh exact old database alone, it is impossible to determine whether an operator discarded both values; the database contains no evidence of that loss. The future publication coordinator must retain the known context/handoff and load it before capture and forward. No business-table marker, row archive, or guessed recovery path was added.

## New-fact behavior and evidence

Focused regressions use real native storage APIs for reply Task creation, input appends and binding, business-key association, and Workbench-turn Task creation. Domain writes use valid target owner SQL with fields accepted by the actual declarations. Tests establish:

- A valid source input at physical/public ID `404224` is followed by native input canonical ID `404225`, even though the original global Tasks sequence is `416921`. Inverse assigns the new input a source physical ID above `416921`; it does not create source Task `404225`. Current and claimed pointers both reference the allocated source input. Reforward after serializing/reloading the context and handoff preserves native input ID `404225` and its value. Missing and wrong handoffs reject before mutation. Repeating inverse/forward preserves the same mapping.
- A native input canonical ID `207` collides with a genuine source meeting Task ID `207`; the existing Task survives, while the input receives a new source physical ID above the global water. A second input with the same revision key but a distinct trigger also survives. Claimed/current pointers and versions `1`/`2` survive reforward.
- A new native business-key ID `225` collides with a source Task of another kind. The inverse allocates a collision-free source ID, then the old Task12 point update works. A cold old Task12 insert after inverse gets a valid source-allocated ID and survives reforward alongside both native keys.
- A Workbench session canonical ID `203` collides with an existing turn Task ID `203`. Inverse maps the Workbench turn parent through the typed `workbench_tasks` owner to the allocated source physical ID; reforward restores canonical session ID `203`. UUID public identity, `provider_session_ref`, and `client_request_id` retain their types and values.
- A native Task inserted and then deleted advances the native Tasks AUTOINCREMENT high-water. The inverse restores at least that sequence water; after reforward, the next native Task ID remains above the deleted ID.

The initial focused regression was red for three distinct defects: wrong source-ID reuse below the source global water, typed Workbench parent handling, and full owner-inventory dependence. The cases are green after the implementation. The three focused new-fact tests passed in 4.04 seconds. Final focused verification on this tree: `pytest -q tests/test_task_entity_schema.py tests/test_task_entity_migration.py tests/test_task_entity_storage.py` — 36 passed in 26.19 seconds. `ruff check app/task_entity_schema.py tests/test_task_entity_schema.py`, `python -m compileall -q app/task_entity_schema.py tests/test_task_entity_schema.py`, and `git diff --check` passed.

## Metadata measurement and remaining boundary

On the checked-in synthetic fixture, serialized forward metadata was 406,717 UTF-8 bytes including captured source declarations. It carried 7 sparse identity edges, 7 raw-key exceptions, 2 unresolved reference exceptions, and 20 owner high-water scalars. The equivalent reconstructed complete public-owner vector for those 21 fixture rows was 1,964 bytes. This is only a fixture measurement; it is not a full-database size claim. The important reduction is structural: durable forward metadata no longer scales with every owner row.

The Task target declares `tasks.id` as `INTEGER PRIMARY KEY AUTOINCREMENT`; inverse preserves the current native `sqlite_sequence` water even when its highest native Task was deleted. `task_inputs.id` and `task_business_keys.id` are plain `INTEGER PRIMARY KEY`, so SQLite allocates from the current maximum and intentionally reuses a deleted maximum; these tables have no separate durable AUTOINCREMENT sequence to capture. The old Task12 business-key writer's independent allocation remains covered by the cold source insert case.

## Hidden-owner identity fix (2026-10-22)

The first new-fact round-trip exposed a repeated-cycle gap: after inverse allocated a source physical ROWID for a new `business_object_tasks` owner, forward conversion retained its raw-key exception but did not retain the physical-to-canonical owner edge. A second inverse therefore could not associate raw key `"225"` with native canonical owner `225`. Forward conversion now stores only non-derivable hidden-owner associations as sparse `task_run_identity_map` rows with `public_key=NULL` and `public_key_type='null'`, and includes those nullable edges in its serializable sparse metadata. Inverse builds a canonical-to-source-physical index from those rows before resolving raw exceptions; public-key lookup remains separate and typed.

The real-storage regression now performs two inverse/forward cycles around a native business-key ID 225 colliding with another Task owner. It checks the hidden physical ID, raw key and SQLite type remain identical; old Task12 point updates by public key 225 succeed in both restored-source windows; and each forward preserves native key ID 225. See `task-2b-newfacts-fix-1-report.md` for scoped verification and limits.

The conversion still requires caller-owned FK-off transaction/rollback and caller-side `foreign_key_check`, source-predicate verification, and pre-mutation retention of inverse context/handoff. Full-copy/cold-native composition, all-domain parity, and coordinator journal durability remain parent-owned gates. The source remains free of body archives and test-side table cleanup.
