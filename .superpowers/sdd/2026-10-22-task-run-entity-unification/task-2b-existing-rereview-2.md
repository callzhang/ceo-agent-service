# Independent Task2B existing inverse rereview 2

Candidate: `2b9a04ba32530018b0de1d13b791206fd447b511`. Scope: existing Task/domain facts, including the corrected current-fact inverse and round-2 retirement of Task-owned target relations. The separate native-created identity-collision/allocator slice and parent full frozen-source harness remain outside this review.

## SPEC verdict: APPROVE for this bounded subunit

Round 2 closes the owned-schema inverse blocker. `restore_task_entities()` stages the current values needed to rebuild all 20 source families, then drops the 18 exact `CREATE TABLE` owners in `task_run_entity_objects()` in dependency-safe reverse order before recreating the captured predecessor Tasks table and its objects. It does not drop by wildcard or scan names. The native Task identity map is retired with the other owned relations, while identity edges, raw-key exceptions and unresolved-reference markers are returned as sparse metadata. The documentation states the ordering requirement for the later composed Run/Action/State coordinator.

The source-factory test now checks the genuine `task20_schema_is_current()` predicate after inverse, queries the Task12 `business_object_tasks` ROWID projection, performs an `update_task12()` point write, and immediately forwards the resulting source state without test-side table cleanup. The catalog/sentinel test requires the complete schema catalog and every `sqlite_sequence` row to equal their pre-forward values, with unrelated data unchanged. The injected source constraint failure occurs after the owner tables are retired; caller rollback restores all owned rows, catalog entries and sequence rows.

I independently reran the focused suite, including those paths, and it passed. The earlier JSON-lookalike TEXT and hidden `legacy_key` corrections remain in the candidate and their focused regressions still pass.

## QUALITY verdict: APPROVE for this bounded subunit

The round-2 change is narrow and follows the Task converter's explicit ownership catalog. The new regressions no longer repair the result in test code. They cover current native fact preservation, predecessor read/write behavior, immediate reforward, catalog and allocator preservation, and caller-owned rollback. The returned sparse metadata assertion verifies that identity information survives retirement of the native identity-map table.

## Verification performed

- `pytest -q tests/test_task_entity_schema.py tests/test_task_entity_migration.py` — **17 passed** in 17.90 seconds.
- Inspected `task-2b-fix-2-report.md`, the scoped fix brief, implementation diff, updated tests and validation documentation.
- Verified the reforward assertions contain no manual native-owner deletion. The full predecessor-predicate, Task12 point-read/write and immediate-reforward path is asserted in `test_current_fact_inverse_reads_mutated_native_owners_and_reforwards_them`.
- Verified exact catalog plus all-sequence comparison with unrelated sentinel table/index/trigger/data in `test_inverse_retires_owned_target_catalog_and_preserves_unrelated_schema_and_sequences`, and full native-graph restoration after caller rollback in `test_mid_inverse_failure_rolls_back_when_caller_rolls_back`.
- No product files were edited; no production, provider or native CLI was accessed.

These approvals cover only the existing-facts Task/domain inverse subunit. They do not approve the full 37,358-row frozen-source harness, whole-store cold start, combined Run/Action/State conversion, or the remaining native-created cross-table ID collision and inverse identity-handoff gate.
