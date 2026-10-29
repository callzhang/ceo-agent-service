# Task2B hidden-owner identity fix, round 1

Date: 2026-10-22

## Scope

This change fixes the repeated inverse/forward identity loss for source owners without a projected public ID. It changes only the migration utility, its focused regression, and migration validation reports. It does not activate the converter or perform source/production writes, provider or native CLI calls, schema redesign, push, or deploy.

## Behavior change

Forward migration now retains a sparse `task_run_identity_map` edge for a hidden owner only when its restored source physical ROWID differs from its native canonical ID. Such rows have `public_key=NULL` and `public_key_type='null'`; no natural key is guessed and no owner inventory is stored. The same nullable edge is included in serializable sparse `identity_edges` metadata.

The inverse constructs a typed canonical-to-source-physical index from the actual identity-map rows and sparse edges. It maintains a separate source-physical-to-canonical index for resolving raw-key exceptions. Public owner resolution remains keyed by typed public identity, and nullable edges are excluded from that public-key index. The two index directions are no longer mixed in the return value used by source reconstruction.

## Regression and evidence

Extended `test_native_business_key_collision_uses_predecessor_hidden_rowid_allocator` to create a new native business-key owner at canonical ID 225, inverse to Task12, update it through the old Task12 point writer, and forward again. It then inverses a second time without native fact edits and verifies the same source physical ID, raw key (`"225"`) and SQLite storage type (`text`). A second old Task12 point update by public key 225 succeeds and a second forward retains native business-key ID 225 with that update. The test also checks the sparse native identity-map row associates the hidden physical source ID to canonical 225 while carrying no public key.

The new repeated-cycle assertion was red on base `26df92389`: after first refoward, the second inverse could not recover the hidden row under raw key `"225"`. After the fix, the focused tests passed:

- `pytest -q tests/test_task_entity_schema.py tests/test_task_entity_migration.py tests/test_task_entity_storage.py` — 36 passed in 27.31s.
- `ruff check app/task_entity_schema.py tests/test_task_entity_schema.py` — passed.
- `python -m compileall -q app/task_entity_schema.py tests/test_task_entity_schema.py` — passed.
- `git diff --check` — passed.

The existing input-ID 404225 test still binds both `current_input_id` and `claimed_input_id`, checks the original `foreign_key_check` boundary, serializes/reloads the handoff, and rejects missing/mismatched handoffs before mutation. Existing caller-rollback coverage remains in the same focused group. The previous full frozen Task-only harness was run by the parent before this round; this report does not claim that harness was rerun after the fix.

## Persistence boundary

Inverse context and handoff remain body-free, serializable release metadata that a future publication coordinator must persist outside the business database and reload before another forward conversion. An unchanged exact source database cannot reveal that an operator discarded both values; no marker or heuristic was added to claim detection of that case. Sparse metadata remains migration-only, and public identity resolver callers continue to use typed public keys rather than nullable hidden-owner rows.
