# Independent Task2B existing-current-fact inverse review

Candidate: `d5ecf5e05..677f96c0c` (HEAD `677f96c0ca84856f2f31c6a2e882900fdccf56ae`). Scope: current native Task/domain facts back to the genuine current lifecycle Tasks schema. This review does not assess runtime activation or the separately assigned native-created ID collision/allocator/identity handoff slice.

## SPEC verdict: REJECT

The inverse violates the requirement to preserve a current native parameter's value and SQLite storage type. At `app/task_entity_schema.py:559-572`, `_native_value` extracts one parameter with `json_extract()`, then treats any extracted TEXT that parses as an object with a `t` marker as an encoded scalar. `json_extract()` has already erased whether that object was an ordinary JSON string or an encoded codec value, so a valid ordinary TEXT value can be reinterpreted as BLOB/REAL/infinity.

Actual-source counterexample: using the existing `_source()` fixture, which creates the real current lifecycle schema and uses its `source_insert` oracle, I set the source `meeting_alignment_jobs.meeting_id` value to the ordinary TEXT string `{"t":"blob","v":"ff"}`. The source projection returned that string with `typeof(...)='text'`; forward migration stored the same string as native parameter TEXT. After changing that current native parameter to the same ordinary TEXT string and calling inverse, the source projection returned `b'\xff'` with `typeof(...)='blob'`. This reproduces silent value and type corruption in the exact post-migration-current-fact path required by the brief.

The behavior is limited to parameter-mapped fields whose ordinary text happens to match a recognized codec marker, but the contract does not reserve those strings. The native whole-object decoder retains the distinction; the inverse's per-value extraction loses it.

## QUALITY verdict: REJECT

The focused suite passes but does not cover this codec-lookalike boundary. Its changed-parameter regression uses an ordinary title (`tests/test_task_entity_schema.py:279`), and the fixture-level SQL-type comparison covers values present before forward migration. Neither exercises an ordinary TEXT marker-shaped value read from current native `parameters_json` during inverse. The targeted source-oracle probe above is a concrete failing case.

## Verification performed

- `pytest -q tests/test_task_entity_schema.py tests/test_task_entity_migration.py` — **13 passed**.
- Read the Task2B implementation/inverse briefs, current inverse report, scoped validation doc, relevant design requirement at `docs/superpowers/specs/2026-10-22-task-run-entity-unification-design.md:67`, and inspected the pinned implementation and focused tests.
- Ran an additional in-memory counterexample against the actual current-schema fixture and projection. No provider/native CLI, production database, source database, or live service was accessed. No product files were edited.

## Required correction before accepting this subunit

Preserve the native JSON parameter's original JSON value/type boundary when decoding codec scalars, so an encoded BLOB/REAL tag is distinguished from an ordinary TEXT string with the same characters. Add the counterexample as a focused regression that proves source and restored value plus `typeof()` are both TEXT, while retaining coverage for actual encoded BLOB/REAL values. Then rerun the two focused files and request fresh SPEC and QUALITY reviews. The parent-owned new-native-fact collision and identity-handoff gate remains a separate later acceptance requirement and is not included in these verdicts.
