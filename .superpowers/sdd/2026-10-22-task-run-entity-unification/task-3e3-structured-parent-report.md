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
