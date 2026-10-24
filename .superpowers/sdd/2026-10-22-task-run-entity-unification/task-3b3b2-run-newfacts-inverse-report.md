# B3B2 Task-first new-fact Run inverse report

## Result

`restore_run_entities` now supports a migration-only Task-first path for current native Run facts that were created after B3B1 forward. Callers pass the actual return value from `restore_task_entities` as `task_inverse_metadata`; the Run inverse uses its digest-checked sparse identity edges and reads the restored selected-source Task projections for typed Task→Run references. The result adds a portable `run_handoff` using B3B1's frozen version 1 JSON shape: sparse owner/source-physical/canonical edges, all eight independent native owner high-waters, selected-source context, and digest.

The existing Run identity path remains for unchanged fixtures. Native rows deleted before inverse are not recreated. New source physical IDs are globally allocated above the captured source `runs` water, all retained source IDs, and all eight captured native owner high-waters. The restored `runs` global AUTOINCREMENT water retains that same floor, so a subsequent genuine old writer cannot reuse a child-owner insert/delete ID. Child-owner waters remain independently captured and handed forward.

The inverse reports the sequence rows reread from the restored SQLite catalog after inserts, so a newly created per-kind sequence includes its actual SQLite `rowid` instead of the insertion placeholder.

New generic Consumer/Audit `agent_runs` are accepted without fabricated professional provenance. New professional Runs use the selected source's actual legacy-key trigger effect: their public key is the text form of the assigned physical ID, while existing professional public keys and raw SQLite types remain untouched. `runs:published` tracks the non-RUN11-new physical allocator; the three RUN11-new kinds retain separate public-key sequence counters. Typed parent, runtime-attempt, failure, schedule, Workbench event, and Meeting search references are resolved through precomputed owner maps. Forward FK validation permits only Task remaps explained by Task2B edges and compares against the genuine captured orphan set.

The Task inverse also sets a newly restored Reply Task's public identity from its already assigned source physical ID when canonical ID reuse collides with an old auxiliary Task physical ID.

## Verification

- `pytest -q tests/test_run_entity_schema.py --basetemp /private/tmp/ceo-task-run-phase2-_ntbufyi/b3b2-run-module-final2` — 30 passed.
- `pytest -q tests/test_task_entity_schema.py -k native_business_key_collision_uses_predecessor_hidden_rowid_allocator --basetemp /private/tmp/ceo-task-run-phase2-_ntbufyi/b3b2-taskreply-final2` — 1 passed.
- `ruff check app/run_entity_schema.py app/task_entity_schema.py tests/test_run_entity_schema.py tests/test_task_entity_schema.py` — passed.
- `git diff --check` — passed.
- `python -m compileall -q app/run_entity_schema.py app/task_entity_schema.py` — passed.

The committed mixed regression creates synthetic native facts across all eight Run owners, including generic Consumer/Audit and professional Run/projection, fresh/resume parented attempts, parentless Email and Workbench attempts, failure, schedule, new Workbench event against the new Workbench TURN, Meeting search, Email sample, and Setup event. It checks Email hash and Workbench TURN UUID workload identities with the existing runtime validator, typed fact preservation, owner insert/delete high-water, a genuine `create_logical_run` old write above every owner water, portable JSON handoff, and two Task-first inverse/reforward cycles.

The parent independently ran the frozen 37,421-Task/262,889-Run synthetic source probe successfully before the final focused child-water and Workbench FK regression additions. It covered both inverse/old-write/JSON/reforward cycles, all eight owners, typed hashes, the inherited orphan, and monotonic owner waters. This is synthetic migration evidence, not runtime/CLI/Action/UI or release acceptance.

## Fix 1 review follow-up: current-fact retirement

The review finding said an original source-mapped native row missing at inverse was rejected. Inspection found the current-owner filter already runs before the required-identity check: absent source-mapped owner facts are omitted from reconstructed source rows and from the sparse handoff, while a missing identity still referenced by a retained fact remains an error. No product-code defect was demonstrated, so this follow-up changes no migration behavior. It adds a regression for an unreferenced source-mapped schedule occurrence and cache row, verifies current source reconstruction and repeated JSON forward/inverse cycles do not resurrect either row, checks all eight captured owner high-waters and the inherited orphan, and proves savepoint rollback restores native owner rows, catalog, and sequence facts.

Verification for the Fix 1 regression:

- `pytest -q tests/test_run_entity_schema.py -k inverse_retires_deleted_mapped_occurrence_and_cache_without_resurrection --basetemp /private/tmp/ceo-task-run-phase2-_ntbufyi/fix1-focused --tb=short` — 1 passed, 30 deselected.
- `pytest -q tests/test_run_entity_schema.py --basetemp /private/tmp/ceo-task-run-phase2-_ntbufyi/fix1-module --tb=short` — 31 passed.
- `ruff check app/run_entity_schema.py tests/test_run_entity_schema.py` — passed; `python -m compileall -q app/run_entity_schema.py` — passed; `git diff --check` — passed.

This is regression evidence for existing retirement behavior; it does not establish a broad deletion policy or authorize runtime retention changes. No production/native CLI/provider action, push, or deploy was performed.

## Scope boundary

No runtime closure, Action conversion, provider/native CLI execution, production checkout, push, or deploy was performed. B3B2 remains migration-only. The full repository suite and production behavior were not tested.
