# Phase4A validation boundary

Status: implementation and consistent-copy gates passed; final independent quality re-review and remote Quality CI pending. Not deployed. Phase4B/4C remain pending.

## Exact scope

Store 2026-10-11.1 -> 2026-10-11.2; Email schema50 unchanged. Three existing core record types move to physical runs: agent_runs, agent_runtime_attempts, agent_run_state_events. Empty agent_run_events is retired. No new business fields, native trajectory copies, old SQL views, runtime legacy fallback or long-term double writes.

Logical physical/public IDs stay equal; provider/state public IDs remain their original integer keys. New physical allocations stay above captured source highwater. All role/generation/revision/turn/session/range/workload/lease/correction state remains. Source-specific fields retain their exact values and original defaults; nonapplicable fields stay NULL.

The three source PK domains are INTEGER. One UNIQUE(CAST(legacy_key AS INTEGER), record_type) index enforces their numeric public identity and replaces duplicate TEXT uniqueness. Existing parent and workload range indexes remain; public ordering is unchanged. UUID source types are outside4A and require separate actual TEXT constraints in their release.

## Latest consistent-copy evidence

Source production commit: 29e0156775b8bb697b2c55484d879193011931cf. Source Store11.1/Email50. SQLite backup API copied to a task-specific temporary directory; production was only read.

| Check | Result |
|---|---|
| Original logical runs | 21,647 complete projected-row fingerprints equal |
| Original provider attempts | 22,775 complete projected-row fingerprints equal |
| Original service state events | 1,151 complete projected-row fingerprints equal |
| Other original tables | 145 original row counts and full-value fingerprints equal |
| Physical table count | 151 ->148 |
| FK findings | Same existing meeting_alignment_runs row2298 orphan; no new finding |
| Integrity | quick_check=ok |
| Baseline-only inverse | Exact captured declarations/indexes/triggers and named sequences restored |
| Writes after migration | New Consumer/Audit/provider/state records and new business result/receipt retained after inverse; receipt/result byte-exact |
| Allocate then delete | Existing discard_unstarted_agent_run invoked on copy; inverse preserves consumed ID watermark |
| Real old code | 302 Python files match source Git blobs; exact old source opens/read/claims copy and never reuses deleted ID |
| Repeated forward | Original row fingerprints/FK/integrity still equal after real old-code read/write and new forward |
| Provider calls / production writes | Zero |

Old-code verification uses the actual29e source tree, not an earlier rollout or a changed report label. Original projected values are compared by public identity. Explicit synthetic copy-only rows are excluded from original-subset comparisons and validated separately.

## Regression and query cost

Focused core/turn/release/readers/metadata-lock/Email-PK/current-repair tests:130 passed. Original current-schema missing-table and recovery-index repair oracles remain unchanged. Unknown/malformed/mixed sources fail without structure or row mutation.

Publication failure at dependencies/import verification/restart/health/loaded stages restores schema before old-code restart and retains replacement writes. Inverse interruption after child rebuilding rolls back atomically. The inverse never overwrites the DB file with an old backup.

A5,000-unrelated-provider regression reproduces the former global-scan plan. After the numeric-first index repair, real native snapshot/latest-route/exact-workload queries seek parent or workload ranges and remain below2,000 VM instructions. Target/empty snapshots and exact workload lookups invoke no100-opcode progress callback; parent batch uses100 counted instructions. Returned source values and original public IDs7/9000 remain equal despite physical remapping. No INDEXED BY hint, physical-ID sorting, native selection heuristic or covering index was added. Existing Task repair LIKE behavior measured40,000 VM instructions on both source and target and remains unchanged.

The35-module caller fixture group passed2,493 tests/6 skips before the index fix. Post-index plus latest-main expanded run passed2,933 tests/6 skips; its12 failures were five raw old-table updates in new native-input test fixtures. Those updates were switched to typed provider predicates and all26 native/prompt tests passed. Remote complete Quality remains required. Ruff passes across app and tests.

## Capacity evidence

Consistent-copy page measurements after repeated forward/inverse:

| Bytes | Source | Target |
|---|---:|---:|
| File pages | 414,822,400 | 419,573,760 |
| Free pages | 15,728,640 | 16,936,960 |
| Used pages | 399,093,760 | 402,636,800 |

Used-page change is +3,543,040 B, about0.89%. The target includes explicitly declared synthetic rows and repeated migration cycles; this is not a production disk-reclamation claim. The core mapping and identity indexes add storage while reducing table fragmentation in the design. The duplicate TEXT index was eliminated; numeric public index occupies1,400,832 B in this copy. No production VACUUM or new retention policy was introduced. Final Phase4 size must be measured after4B/4C.

Disposable databases, copied old code and full test logs remain temporary until the release gates close, then are removed. Only regression code and concise acceptance evidence belong in the repository. Latest verified production backup follows the existing deploy retention workflow.
