# Task 3C2B2C — current Action/adoption inverse materializer

## Scope and API

Implemented the migration-only public entry point
`restore_run_adoptions(db, source_metadata, forward_metadata, *, task_metadata,
task_inverse_metadata, run_inverse_metadata)` in `app/run_adoption_inverse.py`.
It requires the caller's active FK-off transaction after Task inverse and Run
inverse, calls the identity planner once, then composes the retained and adopted
row builders with that same plan. It rebuilds the complete source `actions`,
`action_receipts`, `action_observers`, and `action_entity_identity_map`
catalogs from current native rows and accepted source declarations. It inserts
provider receipt lineage after all receipt rows exist, retires the native
adoption tables/map, preserves current `sqlite_sequence` rows, and returns only
counts/version metadata. Commit and rollback remain caller-owned.

The forward representability check in `app/run_adoption_schema.py` now derives
retired-only fields from actual source and target stored columns and refuses a
retained row with a non-NULL value in any such field before rebuilding Actions.
The inverse row builder derives its source-only NULL reconstruction from the
same source/target stored-column difference; no retired-value JSON stash or
additional identity registry was added.

## Verification

- Real fixture composition uses `_populated_source(remap_tasks=True,
  remap_runs=True, nonagent_run_owner=True, stop_before_adoption=True)`, then
  actual C2A forward → Task inverse → Run inverse → adoption inverse. It
  confirms exact Actions/receipt/observer rows and full source Action child
  catalog, verifies adoption tables are retired, and checks FK and SQLite
  integrity. Result metadata: 10 Actions, 2 receipts, 2 observers, 1 retained
  Action, 9 adopted Actions, zero reassigned identities.
- Existing current-fact tests cover adoption physical-ID collision planning,
  retained typed Task/Run/Feedback references, current Review/Progress edits,
  supplement retirement/addition, and new native Review/Run facts. The new
  native Review case now also passes through the actual materializer and checks
  the restored source Review row and FK integrity.
- The materializer collision regression occupies a mapped adoption physical ID
  with a current generic Action and adds two linked receipts, an observer, and
  an Action identity-map row. After inverse, those references remain on the
  current generic Action while the reconstructed adoption uses the planner's
  reassigned ID; provider lineage and FK checks remain intact.
- Weak Run references resolve through sparse handoff edges, or retain the same
  ID only when the actual restored `agent_runs` table contains that owner.
  Coverage includes a remapped ID, a direct source member, zero, NULL, and an
  unresolved positive reference.
- The root-owned frozen source probe first exposed receipt `agent_run_id=25191`:
  the restored source has an actual `runs(id=25191, record_type='agent_runs')`
  owner, while no Action row uses that value. The previous forward-direction
  reference translator incorrectly demanded a sparse map edge. The inverse now
  resolves through the sparse edge or actual source membership; it still rejects
  an unresolved positive ID. The same source readback showed receipt provider
  lineage `(id=2 → provider_receipt_id=1)` and zero observer references, which
  the current-fact reconstruction preserves.
- The root-owned collision review found a separate ownership hazard: overlaying
  a reassigned adoption ID onto the generic current Action map could redirect
  current receipt, observer, and identity-map references. The materializer now
  keeps current Action IDs identity-mapped; the new regression with two linked
  receipts, observer, and map row proves current owner references survive while
  the candidate receives its planned reassigned ID.
- Public materializer rejects positive unresolved Run and typed Task references
  before mutating native Actions. The generalized retired-field regression
  confirms populated retained `task_id` is rejected before loss.
- Final focused suite:
  `pytest -q tests/test_run_adoption_inverse.py
  tests/test_run_adoption_inverse_rows.py tests/test_run_adoption_schema.py
  --basetemp=/tmp/task3c2b2-child-test` — 32 passed in 56.91s.
- `ruff check` on the two implementation modules and three focused test files,
  `python -m compileall -q app/run_adoption_inverse.py app/run_adoption_schema.py`,
  and `git diff --check` all pass on the final implementation.

## Boundary

This does not activate a constructor or runtime caller, change DDL, run against
providers/production, or cover genuine predecessor cold writes. Root owns the
frozen source-RO-to-memory inverse/reforward probe and its final current-source
result. That result must be recorded separately from this focused fixture
verification. Cold predecessor writes and two process cycles remain the later
B3 acceptance; source-selection deferred-FK parity remains queued separately.

## Commit

Implementation, focused tests, and behavior documentation were committed as
`b1724b286 Complete current Action adoption inverse materializer`. This report
close-out and release of the file claim were committed as `43d85d137`. The
root-owned frozen source-RO-to-memory inverse/reforward checkpoint is managed
separately; its result is not included in the focused-test claims above.
