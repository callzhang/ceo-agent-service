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
