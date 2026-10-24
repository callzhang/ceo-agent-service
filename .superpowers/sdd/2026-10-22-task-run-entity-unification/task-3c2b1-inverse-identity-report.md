# Task3C2B1 inverse identity planner

## Result and boundary

Added `app.run_adoption_inverse.plan_run_adoption_inverse(db, source_metadata, forward_metadata, *, run_inverse_metadata)`. It is a caller-owned, FK-off, read-only planner that returns content-free native-owner identities, source Run physical IDs, retired mapping count, collision-safe Action physical assignments, typed public IDs, and unchanged source/current/planned sequence facts. A later C2B materializer can consume the plan within its transaction.

This slice does not materialize old Action/adoption rows, receipts, or observers; it does not restore source DDL or run a cold open/reforward cycle. It does not activate constructors, runtime readers/writers, production, or a new allocator/registry. Full C2B remains incomplete.

## Identity and allocator contract

- Candidate and execution-alias owners use the canonical Consumer Run identity resolved through the actual Run inverse `run_handoff` and source `runs.record_type='agent_runs'` membership. The resolver accepts the complete Run inverse return object, validates its source context and digest, and ignores valid edges for the other seven Run owner kinds.
- A Review owner is `(Audit Run, Review business ID)`. Reusing a Review ID under another Audit Run does not reuse its stale typed mapping. Selection and Supplement identities include their Consumer Run and child ID. A native fact without its exact current owner is treated as new; unmapped old rows are reported as retired.
- Source public IDs retain their raw SQLite value and storage type. Public-key uniqueness/allocation for Candidate and alias uses SQLite `CAST(... AS INTEGER)` semantics; newly generated values are stored as TEXT to match the legacy-key column. Review/Selection/Supplement public IDs derive from the native child business ID.
- The selected source/target `actions.id` declaration is plain `INTEGER PRIMARY KEY`, with no global `sqlite_sequence` owner. Existing current Action IDs are reserved first. A colliding old adoption physical ID is reassigned by SQLite ordinary ROWID allocation while its mapped public key and native business owner stay unchanged. Per-kind child AUTOINCREMENT facts are not treated as a global Actions counter. Captured sequence rows, if present, are reported without being used as a fictitious global allocator.
- Planning does not mutate the caller connection or return adoption bodies/provider payloads.

## Evidence

The integration regression uses `tests/test_run_adoption_schema.py::_populated_source(stop_before_adoption=True)` with Task and Run remapping enabled. It runs actual C2A forward conversion, Task inverse, then Run inverse, and supplies the complete returned Run metadata to the planner. The real Run handoff contains both `agent_runs` and `agent_runtime_attempts` edges; equal canonical integers in those distinct owner namespaces do not create ambiguity. The real fixture preserves Review 7's raw TEXT public key, maps it to the actual source Audit Run physical ID, and confirms the assembled Actions declaration has no global `actions` sequence. Planning twice produces identical output and leaves Actions, native Reviews, the identity map, and all sqlite_sequence rows unchanged.

Focused RED/GREEN command and result:

```text
python -m pytest -q --basetemp=$(mktemp -d /tmp/task3c2b1-XXXXXX) tests/test_run_adoption_inverse.py
```

During fixture tightening, the initial focused run was RED (`2 failed, 4 passed`): it exposed that whole-database `foreign_key_check` is not a valid post-inverse assertion while source typed references are intentionally in transition, and that a handcrafted fixture had duplicate canonical/source Run rows. The tests now assert the Review owner directly in the lawful-reuse fixture and test missing source membership by removing all relevant Run rows. Final result:

```text
......                                                                   [100%]
6 passed in 3.09s
```

The six tests cover retained and new identities, missing positive Run owners, Review-ID reuse under another Audit, TEXT public-key collision allocation, retained Action collision reassignment, and the actual C2A→Task inverse→Run inverse integration. The collision unit fixture uses the exact plain `id INTEGER PRIMARY KEY` allocator declaration; the assembled source integration independently verifies the actual selected catalog and absent global sequence. No full suite or large-copy probe was run.

## Verification and limits

Verification passed:

```text
python -m ruff check app/run_adoption_inverse.py tests/test_run_adoption_inverse.py tests/test_run_adoption_schema.py
All checks passed!

python -m py_compile app/run_adoption_inverse.py tests/test_run_adoption_inverse.py tests/test_run_adoption_schema.py
# exit 0

git diff --check
# exit 0
```

The result is a local helper and focused test evidence only; native materialization, old-code cold read, reforward, and publication remain unverified and belong to the next C2B slice.

## Independent review fix 1: global physical identity uniqueness

Review found that duplicate source physical IDs were checked as `(source_kind, physical_id)`, even though every kind shares the global `actions.id` namespace. `_map_rows` now rejects any repeated `source_physical_id` across all kinds before producing a plan. This is malformed migration-map validation; it does not reject a valid current Action occupying an adoption ID, since those collisions still take the existing reassignment path.

The regression first gives Review and Selection the same public integer `7` in their distinct per-kind public namespaces and confirms planning succeeds. It then makes the Selection mapping reuse the Review's global physical ID, updates forward metadata consistently, and confirms the planner rejects it without changing Actions or the identity map.

Focused RED/GREEN evidence:

```text
python -m pytest -q --basetemp=$(mktemp -d /tmp/task3c2b1fix1-red-XXXXXX) tests/test_run_adoption_inverse.py -k cross_kind_duplicate
FAILED: Failed: DID NOT RAISE ValueError
1 failed, 6 deselected

python -m pytest -q --basetemp=$(mktemp -d /tmp/task3c2b1fix1-green-XXXXXX) tests/test_run_adoption_inverse.py
.......                                                                  [100%]
7 passed in 3.93s

python -m ruff check app/run_adoption_inverse.py tests/test_run_adoption_inverse.py
All checks passed!

python -m py_compile app/run_adoption_inverse.py tests/test_run_adoption_inverse.py
# exit 0

git diff --check
# exit 0
```
