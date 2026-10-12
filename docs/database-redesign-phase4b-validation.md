# Phase4B first task-storage unit validation

Status: isolated implementation and copy validation; unpublished. Latest production checkout readback is 54e6c209950898f60178fc9926b9857b9409c1ad (the newer role-Skill configuration performance commit); the copy evidence below was captured under 7baa1f0e and Store 2026-10-11.2. The final candidate must refresh the live source before publication. This is the first internal Phase4B unit, not completion of all Phase4B or Phase4C.

The precise source-to-target field inventory is the existing Phase4 CSV and captured source DDL. Reply tasks/inputs/work inputs use typed tasks; Task Agent runs extend runs. No inferred business fields or fabricated source FK are added.

The new live-source copy was captured with SQLite backup through a read-only production connection. Both forward operations use the real AutoReplyStore constructor through the paired release, rather than a mocked migration callback. All four source projections compare every original value, including retired caches confirmed empty. The 141 unrelated tables compare all original values. Baseline inverse compares every original table value, named sequence, service-state value and captured DDL. The existing meeting-alignment FK finding is unchanged.

Copy validation results:

```json
{
  "original_core_runs_forward_exact": true,
  "normal_constructor_forward": true,
  "task_projection_original_values_exact": true,
  "unaffected_tables_exact": 141,
  "baseline_inverse_all_data_ddl_sequences_exact": true,
  "repeated_forward_verified": true,
  "known_fk_unchanged": true,
  "new_writes_inverse_exact": true,
  "new_receipt_exact": true,
  "deleted_public_id_watermark_retained": true,
  "providers_called": 0,
  "counts": {
    "work_summary_inputs": 9084,
    "task_agent_runs": 10718,
    "reply_tasks": 7287,
    "reply_task_inputs": 7821
  }
}
```

Replacement-only synthetic writes include a reply and canonical input, raw BLOB work input, Task run projection, a logical run and an execution receipt. Inverse keeps exact work payload/result/receipt bytes and the deleted public ID watermark. No Agent/provider call or external action is executed.

The old app tree is extracted from the actual production commit, then each file is checked against its Git blob. Old constructor startup, new reply read, claim CAS and deleted-ID non-reuse passed. Re-forward preserves claimed state, input body, work/run IDs and the new receipt.

```json
{
  "actual_old_commit": "7baa1f0e5622f474e0db99eac6d41975e37c4301",
  "actual_old_constructor": true,
  "new_reply_read": true,
  "claim_cas": true,
  "deleted_public_id_never_reused": true,
  "providers_called": 0
}
{
  "actual_old_app_git_blobs_exact": 321,
  "new_writes_repeated_forward": true,
  "claimed_input_and_state_preserved": true,
  "store_version": "2026-10-11.3",
  "providers_called": 0
}
```

Focused checks: the five new storage/source/manifest/reader files passed 71 cases before adding the five publication-stage regressions. The strengthened dependencies/verification/restart/health/loaded regressions then passed 5 cases; these use real RepositoryUpdater and inverse with offline stop/start/health and forward callbacks. They do not represent five launchd restarts. Normal constructor/actual old-code execution is independently covered by the copy exercises above. Receipt assertions capture the exact new ID and compare the complete row, so an existing receipt cannot satisfy the oracle.

Legacy retirement and current invariants: 599 tests passed across eight affected files. Seven uncalled historical input migration/inverse methods and the obsolete 10.2→11.1 StoreInputSchemaRelease branch were removed. Current Task12→13 and retained Run11.1→12 publication remain. Historical migration-success premises were retired; session evidence constraints, malformed-current rejection without any persisted mutation, Task/Attention receipt preservation, OA alias resolution and latest business-object binding were replaced with valid13 assertions. Current claimed/current input separation, physical FK, append-only revisions, compaction, rerun, snapshot/capabilities/ownership and fresh/missing-index WeChat coverage remain. No fallback, skip or xfail was introduced. Independent bounded specification/quality review found no P1/P2 in this retirement.

Attention: query-plan 10 cases and bounded Attention/queue 45 cases passed. The failed reply ID selector explicitly uses the existing compact status index, preserving the prior case-insensitive index-scan behavior. Typed public lookup and canonical input use their key indexes. Authorizer and bytecode evidence show the selector reads status/public identity from the index and excludes input bodies. The prior implementation also scanned the status index; no range-seek claim is made. Existing business/order/limit/freshness assertions remain. Independent review found no P1/P2.

Independent core review checked the enhanced scripts and reports: original4A run columns now compare exactly during forward, and claimed reply/input versions/generation, Task projection and the complete receipt compare against old-code claim snapshots during re-forward. Independent backup captures have different natural row counts; each comparison uses its own frozen source, never cross-snapshot counts as proof.

Diagnostic callers: inspector and replay scripts now use public Task/WSI projections and typed physical updates. Their 36 focused tests passed, covering divergent IDs and decoys, public run ordering, seven source-field drift rejections before a fake runner, exact changed-row retention and lease release, TEXT/BLOB codec, read-only inspection and native unavailability. Existing replay CAS parameter tuples and predicates remain; independent read-only code review found no P1/P2. Tests use temporary copies and fake runners, with no actual provider replay.

Still required before release: integrate current main without losing newer behavior, rerun final copy gates on the final candidate, publication-failure exercises, remote CI, normal deploy and live readback. No production B migration, VACUUM or capacity-reduction claim has been made.


## Meeting internal unit boundary

Meeting core, Store DAO and outer readers are implemented and separately reviewed in the isolated branch, unpublished. Core21 cases, Store9 cases and the67-case overlapping combination passed; outer24 focused cases passed. Review found and closed one DAO timestamp omission: begin/direct-record again explicitly write SQLite current_timestamp, while the source empty-string default is unchanged. No other P1/P2 remained within these reviewed scopes.

Current production readback remains54e6c209 and Store2026-10-11.2. A read-only check of SOURCE_TASK_DECLARATIONS matched all six actual source table declarations without running migration. Final live-source copy refresh still remains. Existing raw old-table fixtures are being adapted; this is not the complete Meeting behavior gate or fullB acceptance. An escalated maintenance run passed the receipt/unknown_tool race cases; other_task then exposed a remaining rawreply fixture, distinct from the resolved ps sandbox limitation.


Meeting fixture closure: six affected test files passed 284 cases excluding four tests that require ps access. The four maintenance recovery/race cases passed separately with that permission, using temporary databases and test callbacks; no live service was stopped. The two malformed-source/current-schema tests additionally compare complete logical dumps and user_version before/after rejection, including retained rows. They cover the exact supported-layout boundary rather than resurrecting pre-marker calendar backfills. The final scoped fixture SPEC and QUALITY review passed with no P1/P2. All six test-function counts remain unchanged, no skip/xfail was added, and the source/current rejection assertions were strengthened.


## OKR and weekly internal unit boundary

Nine-table core and OKR/weekly readers are implemented and independently reviewed with no P1/P2 in their scopes. Core focused190 cases passed; independent review ran125 cases and compared current production DDL read-only. A separate old-source oracle compared complete inverse rows/DDL/FK across15 source tables, including BLOB, SQLite-permitted scalar values, absent request relationships, and replacement deleted-allocation watermarks. This remains fixture proof, not the final whole production copy. The 4A prefix, function ASTs,19 source declarations and include_task_runs=False objects remain unchanged.

Reader tests cover failed-only upsert, public/physical divergence, multiple native runs, raw item request references, lease successor/expiry, cache hit/miss, superseded technical versus business failures, Dispatcher generations and typed quality/Attention. Seven selected cases passed; independent review additionally passed four new cases and six relevant Store cases. Broad raw-table fixture migration is still in progress. The22 old run-storage failures were reproduced on clean8a7ce193 and need coherent pure-A/current-B test setups, with no runtime legacy fallback.

For final old-code rehearsal,321 app files from current production54e6c209 were extracted into a task-owned temporary directory and verified against Git blobs. Final consistent database capture, combined-candidate copy validation, new-fact inverse, real old-code startup/claim, CI and normal deploy have not yet been executed for complete B.


Runtime fixture review closure (2026-10-12): root ran all ten affected files: 1,439 passed and two failed in 110.03s. Both failures are unchanged obsolete Store constructor fixtures at lines1889/1918; they remain explicit pending repairs, not waived checks. Independent SPEC then QUALITY review found no blocking findings, identical1,244 test-function counts, no removed per-test assertions or new skip/xfail. All57 changed functions (64 parametrized cases) passed independently in23.93s. Latest direct production Git readback remains54e6c209950898f60178fc9926b9857b9409c1ad with clean app paths and Store2026-10-11.2. Complete B copy/reversal/deploy gates remain unexecuted.


Extended runtime dependencies (2026-10-12): eight focused worker/email/Console/Task files yielded1,676 passed,74 failed,2 existing skipped in90.08s. Several failures directly reference retired reply_tasks/work_summary_inputs tables in test setup/readback; old source-body migration premises also require separate diagnosis. These74 failures remain unresolved gates; the run is not accepted as passing and no migration guard is relaxed. A read-only call to the actual nine-table source-declaration preflight against the current production database passed; it performed no migration or live mutation.


Legacy core fixture closure: the direct inventory11.1 fixture and explicit migration/inverse tests plus two Store sentinel cases passed60 checks. Independent review found no additional blocking issue in that scope, but identified four adjacent Agent-turn tests that still require receipt-table construction and an explicit11.1 migration/current13 DAO boundary. Those four and an accurate renamed rejection test are required before committing this fixture unit. Final copy-oracle review also identified missing deleted-run allocation coverage; task-only sequence checks cannot support a combined tasks/runs watermark claim. That verifier gap is pending repair before execution.


Adjacent legacy fixture closure completed: full Agent-turn/run-storage/release files and the two Store sentinel cases passed120 tests in5.48s. Independent SPEC then QUALITY re-review passed, with six adjacent cases passing separately in2.09s. Receipt source DDL, explicit11.1 migration, fresh13 DAO reads, original receipt/state identity, native absence, and complete rejection nonmutation remain covered. The shared helper caller gap is closed; extended eight-file74-failure gate remains pending. This internal unit is ready for a private commit, not publication.


## Complete B source-copy exercise (candidate51e380f9)

SQLite backup captured actual production54e6c209 through a read-only connection; candidate app files were clean and loaded from the isolated candidate. Normal constructor migration compared all original columns of nine source types, the original three core run types, seven declared child tables with exact physical parent mapping, and129 unaffected table values. Baseline inverse compared all table values, source DDL, sequence rows, complete service_state, application_id and user_version; all matched. The constructor then successfully migrated the same copy again. Source counts: reply7303, input7841, WSI9084, Meeting jobs689/runs2535, OKR requests20/runs22, weekly169, Task runs10718.

The new-fact exercise also passed on that same copy. Synthetic replacement writes retained raw BLOB, TEXT and valid compressed WSI1 payload bytes, Task session identity, Meeting/OKR transcript references, adopted Meeting decision/target/mentions/final/send/calendar values, an active weekly lease, a delivery claim, raw OKR item references and a complete new execution receipt. Full nine-table projections and declared children compared exactly after inverse. Deleted task/domain-run/core-run allocation watermarks and original FK/integrity findings remained correct. No provider or Agent call was made. These are full-copy baseline/new-fact results, not production migration.

The candidate copy currently has source12 restored. Actual old54e6c209 constructor/claim/allocation non-reuse and full-value repeated forward are prepared for independent review and remain pending; so do five publication-failure stages, remaining runtime fixtures, remote CI, normal deployment and live readback. No capacity-reduction claim is made.


Actual old-code/re-forward closure:321 extracted app files were rechecked by SHA-256 against source54e6c209. Its real AutoReplyStore constructor opened the inverse copy; immediately afterward all prior nine-type values, core rows, declared children, OKR items, receipt, complete service_state and sequences still matched. New reply read and exact claim CAS passed. Actual old logical-run claim and source domain-run/WSI/weekly allocations exceeded the deleted watermarks (runs/domain190257 and WSI/weekly404226), proving non-reuse for this copy. Re-forward through the normal candidate constructor retained every source-projection value, core row, child/receipt, claim/input state,129 unaffected table values, complete service_state with only version12→13, and unrelated sequences. Candidate remains51e380f9; copy is now Store13. Providers called0.

Current runtime fixture closure: seven worker/email/Console/Task test files passed1258 tests with two existing skips in66.83s; the authorized two WeChat helper/mismatch spans passed24 focused cases and three affected Console cases. Independent SPEC then QUALITY static review found no blocking issue: original tests, parametrizations, mocks and business assertions remain; current input references remain physical, outward IDs remain public, and typed mutations include record_type. Six historical business-signal fixture failures remain a separate pending unit.


Publication-stage closure on combined B code: five parametrized failure-stage cases (dependencies, verification, restart, health, loaded) and nine related RepositoryUpdater publication cases passed14 tests in3.21s. They execute real updater/inverse logic with temporary databases and offline stop/start/health callbacks. They verify inverse precedes old start and replacement writes/receipt retention; they are not actual launchd failures or restarts. The complete copy exercise separately covers the normal constructor and actual old-code startup. Broader related dependencies, historical business-signal fixtures, remote CI and normal deployment remain pending.


Final related-fixture closure: the six scheduled-recovery/rule-retirement/follow-up/codec/scanner/WeChat files passed96 cases; independent review reran96 and found no P1/P2, with87 functions and429 assertion nodes unchanged. Historical business-signal fixtures passed492 cases and independent review found no blocking issue. The subsequent eight-file baseline had46 failures/580 passed; most were raw retired-table fixture dependencies, but it also exposed a genuine Email reader omission. Four runtime sqlite_master gates still named reply_tasks and suppressed valid typed lineage, in-flight detail, lists, verified URL and queue summaries. The gates now use tasks/runs; two Email files passed530 cases, and independent review found no blocking issue. The historical v36 fixture now uses actual source12 declarations, preserving original input/parent fields and version while testing the unchanged historical policy-key cleanup.

Latest production Git readback after fetching main is e07abdf0d44993d28ec2da81f6c7f40b29df1c13 (PR53 source-verified Email retry), with clean app paths. Earlier copy proof remains explicitly against54e6c209/candidate51e380f9; it is not current-source publication proof. Preserve the new Email schema/runtime behavior when integrating main, rerun affected checks, then refresh the final copy/old-code evidence before release.
