# Phase4A validation boundary

Status: Phase4A deployed and read back successfully at7baa1f0e (Store11.2/Email50). Consistent-copy, exact inverse, independent specification/quality review and complete remote Quality all passed. Phase4B/4C remain incomplete and unpublished.

## Exact scope

Store 2026-10-11.1 -> 2026-10-11.2; Email schema50 unchanged. Three existing core record types move to physical runs: agent_runs, agent_runtime_attempts, agent_run_state_events. Empty agent_run_events is retired. No new business fields, native trajectory copies, old SQL views, runtime legacy fallback or long-term double writes.

Logical physical/public IDs stay equal; provider/state public IDs remain their original integer keys. New physical allocations stay above captured source highwater. All role/generation/revision/turn/session/range/workload/lease/correction state remains. Source-specific fields retain their exact values and original defaults; nonapplicable fields stay NULL.

The three source PK domains are INTEGER. One UNIQUE(CAST(legacy_key AS INTEGER), record_type) index enforces their numeric public identity and replaces duplicate TEXT uniqueness. Existing parent and workload range indexes remain; public ordering is unchanged. UUID source types are outside4A and require separate actual TEXT constraints in their release.

## Latest consistent-copy evidence

Source production commit: 947f4f24475e64845bbfddc80f84fa9fc2f0cceb. Source Store11.1/Email50. SQLite backup API copied to a task-specific temporary directory; production was only read.

| Check | Result |
|---|---|
| Original logical runs | 21,706 complete projected-row fingerprints equal |
| Original provider attempts | 22,836 complete projected-row fingerprints equal |
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

Old-code verification uses the actual947f4f24 source tree, not an earlier rollout or a changed report label. Original projected values are compared by public identity. Explicit synthetic copy-only rows are excluded from original-subset comparisons and validated separately.

## Regression and query cost

Focused core/turn/release/readers/metadata-lock/Email-PK/current-repair tests:130 passed. Original current-schema missing-table and recovery-index repair oracles remain unchanged. Unknown/malformed/mixed sources fail without structure or row mutation.

Publication failure at dependencies/import verification/restart/health/loaded stages restores schema before old-code restart and retains replacement writes. Inverse interruption after child rebuilding rolls back atomically. The inverse never overwrites the DB file with an old backup.

A 5,000-unrelated-provider regression reproduces the former global-scan plan. After the numeric-first index repair, real native snapshot/latest-route/exact-workload queries seek parent or workload ranges and remain below 2,000 VM instructions. Target/empty snapshots and exact workload lookups invoke no 100-opcode progress callback; parent batch uses 100 counted instructions. Returned source values and original public IDs7/9000 remain equal despite physical remapping. No INDEXED BY hint, physical-ID sorting, native selection heuristic or covering index was added. Existing Task repair LIKE behavior measured40,000 VM instructions on both source and target and remains unchanged.

The35-module caller fixture group passed2,493 tests/6 skips before the index fix. Post-index plus latest-main expanded run passed2,933 tests/6 skips; its12 failures were five raw old-table updates in new native-input test fixtures. Those updates were switched to typed provider predicates and all26 native/prompt tests passed. Remote complete Quality remains required. Ruff passes across app and tests.

## Capacity evidence

Consistent-copy page measurements after repeated forward/inverse:

| Bytes | Source | Target |
|---|---:|---:|
| File pages | 414,822,400 | 420,986,880 |
| Free pages | 14,364,672 | 16,977,920 |
| Used pages | 400,457,728 | 404,008,960 |

Used-page change is +3,551,232 B, about0.89%. The target includes explicitly declared synthetic rows and repeated migration cycles; this is not a production disk-reclamation claim. The core mapping and identity indexes add storage while reducing table fragmentation in the design. The duplicate TEXT index was eliminated; numeric public index occupies1,400,832 B in this copy. No production VACUUM or new retention policy was introduced. Final Phase4 size must be measured after4B/4C.

Disposable databases, copied old code and full test logs remain temporary until the release gates close, then are removed. Only regression code and concise acceptance evidence belong in the repository. Latest verified production backup follows the existing deploy retention workflow.

Independent final review: 114 additional focused tests passed against the same runtime code. Actual-copy old/new result values and ordering match; populated parent, batch and workload lookups use their range indexes. That review preceded the later source-declaration finding described below; it does not close the final release gate.

Latest main integration: 3195cd3b rerun metadata-only lookup retained unchanged; targeted rerun/query-plan/status tests17 passed. A fresh3195 consistent copy repeated full projection/other-table checks, exact baseline-only inverse and replacement-write inverse;302 old source Python files matched Git blobs, actual old code startup/claim and repeated forward passed. Baseline-only sequence identity compares name/seq, not sqlite_sequence insertion rowid; all151 table declarations and values match. The latest docs-only main merge is da9b6b56. Later current-manifest/source-declaration repairs require their own final review and full CI before deployment.

## Final-candidate repairs and completed release

Remote Quality runs38194962994 and38197184165 failed on the same45 historical migration fixtures. The second run had11,721 passed,99 skipped and44 deselected. These fixtures changed only a global version marker or built an unsupported partial core. The repair exercises the existing historical business migration primitive on valid current core, or constructs the exact supported11.1 source, while retaining original value, provenance, receipt, FK, sequence, quiet-claim and atomicity assertions. All10 affected test files passed1,017 tests locally; no skip or runtime source fallback was added. This is focused evidence, not a complete CI pass.

The current-layout manifest previously folded SQL literal case, allowing a changed predicate to pass as current. It now compares SQLite declarations created by the same DDL helper using exact strings with only leading/trailing whitespace removed. Six regression cases failed before the repair and passed after it; root focused73 tests and independent71 tests passed. Missing-object repair behavior remains.

A separate source-declaration P2 was reproduced and repaired: same column names do not prove compatible CHECK/FK/default/index/trigger behavior. An extra source CHECK can accept forward migration yet reject a new replacement record during inverse. The guard now compares all19 explicit declarations (four tables, eight indexes, seven triggers) before mutation. The new test file first had27 failures and5 passes, then the focused guard/core/manifest/release selection passed96 tests. Rejection assertions compare schema/full rows/named sequences/marker before caller rollback and record zero SQLite writer authorizer actions. The actual947f4f24 captured source matches all19 declarations. A fresh production-copy forward retained45,693 original core rows and145 other table fingerprints; baseline-only inverse restored all151 table declarations, original values, named sequences and marker exactly. Replacement-write inverse retained receipt/result bytes exactly. All302 actual947 source Python files matched Git blobs; old code opened, read the new records, exercised claim CAS and never reused an allocated-then-deleted ID. Repeated forward retained all45,693 original core values and145 other table fingerprints. Independent final review and complete CI remain required. No production migration has occurred.

Latest source947f4f24 supersedes the3195 copy above in all acceptance tables and capacity values. Concurrent main action-contract changes are preserved by integration; Store/Email/deploy/schema-release code was unchanged from3195 to the captured source. Main integration must finish before final candidate CI.

Independent final source-guard review passed specification and quality checks:96 tests, scoped Ruff and diff check. The reviewer compared all19 declarations against inventory and captured947 DDL and inspected the copy acceptance JSON; it did not repeat full private-row fingerprints. A later fixture review found two weakened execution preconditions (second migration fast path and parent repair before process barrier). Tests now invoke the second actual migration and leave the old parent/attachment columns unrepaired until spawned locked constructors run. Four focused parameterized cases and all610 tests in the three affected files passed; Bounded independent re-review closed both fixture P2 findings:4 original cases passed with actual call/pre-barrier profiling. Project migration executed again with its completion marker present; Workbench reconciliation executed again without duplicate events. At the process barrier, the WeChat parent retained old cross-channel uniqueness, input/attachment columns were missing, the marker remained11.2, FK findings were empty and schema_current was false; both processes then used the existing locked constructor repair. All original behavior assertions remained. No open P1/P2 finding remains in this final repair scope.

Final main integration includes fda0bc5d and preserves current action-contract selection and Audit field-evidence behavior. The reply metadata test preserves main's single-source-pass assertion plus the existing authorizer proof that no reply_task_inputs body is read. The scoped integration selection passed79 tests; whole app/tests Ruff and diff check passed. Final candidate complete remote Quality is still pending before any deployment.

## Production release receipt

PR50 merged2026-10-12T04:30:17Z as7baa1f0e5622f474e0db99eac6d41975e37c4301. Its code tree matches tested2eb56d79 exactly. Quality run38201495460 passed11,808 Python tests (99 skipped,44 deselected),723 frontend tests (2 skipped), build and2 browser tests. Earlier45 fixture failures were fixed; real repeated/concurrent migration coverage was restored before this complete run.

Normal `python -m app.deploy` waited for quiet and deployed947f4f24→7baa1f0e. No skip-quiet, manual process interruption, production edits or task replay. Readback: PID16960 running, health ok, Store2026-10-11.2/Email50. All45,715 original public IDs captured before deployment remain (21,717 logical,22,847 provider,1,151 state); all115 original execution receipt row hashes equal. Four retired physical source tables absent, current manifest exact, quick_check=ok and the original single meeting FK finding unchanged.

Status/Attention/History APIs returned200. Attention retained the same two pre-release records with unchanged timestamps; failure summary stayed3, pending email actions3. An email action naturally became processing after startup; no new failed/stuck item observed at this readback. One real historical attempt's10 provider references explicitly showed native_session_unavailable with valid public run IDs; no body/cached-result fallback generated. This point-in-time readback does not prove every later business action succeeds.

Live page snapshot: file pages414,822,400→421,232,640 B; used pages400,822,272→404,402,176 B. Natural writes occurred around deployment; these are measured snapshots, not an isolated migration-only delta. Subsequent measurement: SQLite file421,232,640 B, WAL32,593,352 B. No production VACUUM;4A did not reduce physical storage. B/C capacity results remain unverified.

Newest existing daily backup was verified before release: application_id0x43454F42, integrity_check=ok, Store11.1/Email50. Concise evidence is retained here; copies/temporary DBs/full logs are removed after handoff under canonical cleanup.
