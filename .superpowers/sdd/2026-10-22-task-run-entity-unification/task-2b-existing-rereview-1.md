# Independent Task2B existing inverse rereview 1

Candidate: `affd9e283f4984f570773740413afdc56860e801`, compared with parent review commit `29ae9c024`. Scope: both round-1 existing-fact fixes and the parent-reported Task-owned inverse cleanup boundary. No runtime/native/provider/production checks.

## SPEC verdict: REJECT

Round-1 findings A and B are closed in the tested existing-fact paths, but the inverse still does not reconstruct the genuine predecessor state needed for immediate old-schema reforward.

**Confirmed blocker — inverse leaves its Task-owned target tables behind.** Starting from the actual current source fixture, I ran forward then `restore_task_entities`, and queried the real `task_lifecycle_current_schema.task20_schema_is_current()` predicate. It returned `False`: 12 retired Task/domain names remained, including `workbench_tasks`, `workbench_turns`, `work_summary_inputs`, `email_historical_candidates`, and the feedback/OKR/notification owners. Calling capture and forward again immediately then raised `ValueError: Task target owners already exist`, listing 17 target tables. These are the Task converter's own targets; this does not require converting or retiring later Run/Action/State owners.

Both existing reforward tests hide this gap: `tests/test_task_entity_schema.py:301-304` and `:390-394` manually drop the target tables before calling forward again. The inverse should stage the current Task/domain facts it needs, restore the current source Tasks declaration/data, and retire its own target tables as part of that same caller-owned inverse transaction. Add a regression that requires the predecessor predicate to pass and immediately refowards without test-side deletion.

The separate parent-reported frozen full-source probe found 8,680 hidden `legacy_key` differences in those four Task12 families before the fix. I did not rerun that population-level probe. The code path and synthetic actual-source fixture below confirm the repair principle: preserve the raw scalar identity, independently of mutable business fields.

## QUALITY verdict: REJECT

The requested focused suite passes, and the new A/B regressions are meaningful. However, the test named `test_current_fact_inverse_reads_mutated_native_owners_and_reforwards_them` only achieves reforward after manually deleting all target owner tables. It therefore passes while inverse output fails the repository's genuine current schema predicate and cannot itself be reforwarded. The hidden-key test has the same cleanup, so current coverage does not enforce inverse completeness.

## Round-1 A/B verification

- **A, marker-shaped ordinary TEXT:** I loaded the prior implementation directly from `git show 29ae9c024:app/task_entity_schema.py` in memory and ran the actual current-source fixture. Its inverse returned `b'\xff'/'blob'` for source TEXT `{"t":"blob","v":"ff"}` (RED). On `affd9e283`, the same actual-source current-fact path returned the original string with `typeof='text'` (GREEN).
- **B, hidden Task12 row identity:** On the prior implementation, a `business_object_tasks` physical row with `legacy_key='7'` restored as `'224'/'text'` (RED). On the candidate, forward metadata retained `'7'/'text'`; after changing the current native business key to `current-business-change`, inverse kept that current business fact while restoring `legacy_key='7'/'text'` (GREEN). It did not derive the technical identity from the natural key.
- The new technical crosswalk explicitly has one `legacy_key` row per source family and distinguishes projected public identity from hidden Task12 ROWID. The four no-public-id families are covered by the separate scalar exception metadata. This review did not remeasure the 8,680-row frozen-source count.

## Verification performed

- `pytest -q tests/test_task_entity_schema.py tests/test_task_entity_migration.py` — **16 passed** in 17.73 seconds.
- Independently ran the two round-1 counterexamples against both the old implementation loaded from the pinned Git object and the candidate, using the current source fixture and current projection.
- Independently ran forward → inverse → `task20_schema_is_current` → immediate forward against the actual current source fixture. Predicate result was `False`; immediate forward raised the owner-collision error above.
- No product files were edited. The parent-owned new-native-fact collision/allocator gate remains outside this rereview.

The round-1 codec and raw-key corrections are supported by focused evidence, but this existing inverse subunit remains unaccepted until it returns the database to a valid predecessor Task schema and passes immediate reforward without external cleanup.
