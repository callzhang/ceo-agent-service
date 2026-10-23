# Task3E3 structured parent portability report

Status: bounded migration helper implemented locally; producer/runtime cutover, constructor activation, provider use, full copy validation and production are not part of this unit.

## Contract implemented

The version 2 `run_handoff` has one required `runtime_attempt_parent_edges` array. Each entry contains only `attempt_source_physical_id` and `run_source_physical_id`, and the array participates in the deterministic digest. Forward validates exact object keys, integer endpoints, uniqueness/order, endpoint existence and owner kinds, source structured workload, null source parent representation, OKR provenance and equality of the typed request Task owners before changing source DDL. It resolves the parent through the existing Run identity map. Inverse derives the relationship from current native attempt parent, Run provenance and canonical OKR request Task; source `runs.parent_id` remains NULL and the JSON edge carries the native-only relationship across serialization. The adoption inverse caller validates the expanded exact handoff contract. Existing Task and Meeting edges retain the workload-key derivation path; unsupported non-Agent edges still refuse before DDL.

No product schema fields, source snapshots, body copies, workload-key guesses, CLI ordering changes or runtime caller changes were made. The OKR producer still creates its professional Run after CLI validation and must be cut over separately to bind the exact returned attempt ID.

## Identity and tests

The new focused round-trip remaps source Run 1103 and source attempt 1205 to native ID 9113 in their separate owner tables, demonstrating that cross-owner numeric collision is legal. It performs inverse → JSON serialization → forward twice, checking the exact parent, source NULL representation and allocator high-water. Invalid duplicate, dangling, wrong-kind, wrong-provenance and digest-tampered edges are refused before source table replacement. Existing Task/Meeting and unsupported-parent cases remain covered by the neighboring schema tests.

Focused acceptance: 10 tests passed for the new structured cycle, malformed portable edges, the existing Task/Meeting two-cycle and unsupported-parent selections. The expanded Run schema + adoption inverse selection completed 56 passed and 5 failed. Those five failures are in adjacent existing adoption inverse code where `_task_source_physical` iterates the current Task inverse object as if it were the `owners` submapping; current inverse metadata also carries `meeting_business_keys`. They are not caused by the new Run handoff edge. Ruff, compileall and `git diff --check` passed for the changed Python files.

## Evidence boundary and remaining work

Before this change, `restore_run_entities` checked every non-Agent parent against only `_professional_attempt_parent`'s Task/Meeting derivations and refused a structured parent as unrepresentable. The focused test now exercises that exact native OKR parent and confirms the portable inverse/forward path. No producer or runtime cutover was attempted. Independent SPEC/QUALITY review over the full assigned BASE..final range remains pending.

## Task inverse direct-consumer integration follow-up

The first broader selection exposed that `task_entity_schema._validate_inverse_identity` returns `{owners: ..., meeting_business_keys: ...}`. The adoption inverse had returned that wrapper as if it were the typed owner map, and it treated a Meeting-key-only inverse as `handoff_required=false`. `_task_inverse_owners` now validates the actual inverse metadata shape and full digest, computes `handoff_required` from both owner and Meeting key edge collections, then returns only the `owners` submap to typed Task reference resolvers. No Meeting key rewrite or Task inverse behavior changed.

Direct regressions use real `restore_task_entities` results from the complete assembled fixture in both modes: Meeting-key edges only (`remap_tasks=False`) and Task owner plus Meeting-key edges (`remap_tasks=True`). Both call the adoption inverse consumer and compare its result with the canonical validator. Each also mutates a Meeting key edge without updating the digest and confirms rejection.

Pinned BASE RED was executed by loading `app/run_entity_schema.py` directly from commit `7a87a36dfe78dedfebb9982a08d725191f5c1ae6` with `git show`, creating the real `_run_source()` fixture from `tests/test_run_entity_schema.py`, migrating source Run 1103 to native Run 9113 with the BASE version-1 identity handoff, setting structured attempt 1205's real native parent to 9113, then calling the BASE `restore_run_entities`. Exact stdout:

```text
BASE 7a87a36dfe78dedfebb9982a08d725191f5c1ae6 RED: ValueError: native non-Agent RuntimeAttempt Run owner cannot be represented by the selected source
```

Exact GREEN and static commands/results:

```text
pytest -q tests/test_run_entity_schema.py tests/test_run_adoption_inverse.py tests/test_run_adoption_inverse_rows.py
73 passed in 47.30s

ruff check app/run_entity_schema.py app/run_adoption_inverse.py tests/test_run_entity_schema.py tests/test_run_adoption_inverse.py tests/test_run_adoption_inverse_rows.py
All checks passed!

python -m compileall -q app/run_entity_schema.py app/run_adoption_inverse.py
git diff --check
```

The earlier 56-pass/5-failure result is superseded: all 73 tests in that same scoped selection now pass. This remains conversion-only evidence; independent SPEC/QUALITY review is still pending.

## Fix1/5: allocate identities before portable parent edges

The first structured-parent implementation checked the native owner maps before allocating identities for new native rows. A new structured RuntimeAttempt pointing at a newly inserted OKR Run therefore had no source identity to serialize; a mapped Run with a new Attempt had the same problem on the Attempt endpoint. The inverse now validates native parent/request semantics before source DDL, allocates all newly observed owner identities, and only then resolves queued structured parent edges through the completed Run and RuntimeAttempt maps. The existing global allocator remains responsible for collision-free identities.

`test_new_structured_okr_run_and_attempt_parent_edges_allocate_before_inverse` uses the real Task-first inverse sequence and current owner facts. It covers a mapped Run with a newly inserted Attempt, a newly inserted OKR Run with a newly inserted Attempt, a preserved parentless unrelated attempt, JSON serialization and two inverse/forward cycles. It verifies exact parent edges, distinct allocated source IDs, source-side NULL parent representation, exact owner rows after reforward and stable allocator water after the first cycle. `test_new_structured_parent_wrong_request_task_refuses_without_mutation` verifies a mismatched canonical request Task is refused before owner-row or sequence mutation.

Pinned BASE `09239253d19a9de112d2003573e88ccc8be7d58b` RED was run by loading its `app/run_entity_schema.py` into an isolated module and applying only an in-memory substitution to its stale `select kind from tasks` check. The substitution lets the BASE reach the allocation-order defect despite Task-first inverse having already restored the source Task declaration. The fixture was the real `_run_source(include_task_source_metadata=True)`, with Run 1103 mapped to 9113, a new native OKR Run 1800 and a new structured RuntimeAttempt parented to 1800. BASE then failed before DDL with `KeyError: 1215`: the attempt identity did not exist in the preallocation map. This isolates the intended failure; no repository source was changed for the RED run.

Exact current verification:

```text
pytest -q tests/test_run_entity_schema.py tests/test_run_adoption_inverse.py tests/test_run_adoption_inverse_rows.py
75 passed in 48.47s

ruff check app/run_entity_schema.py tests/test_run_entity_schema.py tests/test_run_adoption_inverse.py tests/test_run_adoption_inverse_rows.py
All checks passed!

python -m compileall -q app/run_entity_schema.py tests/test_run_entity_schema.py
git diff --check
```

This remains a migration-helper change only. The exact returned-attempt producer and native Task/RuntimeAttempt caller cutover are still subsequent work; no production activation or deployment occurred. Independent SPEC/QUALITY review across the assigned BASE..final range remains pending.
