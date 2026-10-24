# Task 5 final current-copy acceptance

Status: **local Task 5 acceptance complete; no release/deploy claim**. The final post-native cold inverse/reforward completed on a disposable clone. Production remained read-only.

## Frozen inputs and capacity

- Pinned predecessor source: commit `3a46d14eed6175746bd805f6be3f1b12f10556ca`, archived tracked code at `/var/folders/74/yj2lxqs162q7rqzm0mj8nv1c0000gn/T/task5-final-acceptance.3nfz1yaa/predecessor-code`.
- Frozen production-derived predecessor DB: `fresh-frozen-production-source.sqlite3`, SHA-256 `ae1b9bec6029bc674a87c45cfdc2c8acc8a33ac6a0e4802ef82d020948cf20f2`, 514,310,144 bytes; Store `2026-10-21.1` / Email 53; the exact pre-existing FK finding `('runs', 190042, 'tasks', 1)` remains present and is not repaired.
- First forward aggregation recorded from that exact source: 197,530 occurrences scanned; 94,859 approved scheduled occurrences retired; 92,461 excluded; 105,069 historical zero candidates; 102,671 scheduled identity edges retained. These are from the exact first-forward receipt, not a later inverse/reforward capture.
- Same-source packed result: source 507,576,320 bytes; converted target 435,142,656 bytes; canonical inverse sidecar 30,764,762 bytes; net 41,668,902 bytes (39.72 MiB) saved after sidecar. Gross packed DB difference is 72,433,664 bytes (69.08 MiB). Both live-format main DB copies are 514,310,144 bytes; target has 18,185 free pages. No physical compaction was performed, so this is packed-space evidence, not an immediate on-disk shrink claim.

## Current-data native and browser evidence

- Real `gpt-6-luna` / high native CLI runs used the native home and fixture-only tools with fake outbound provider. Actual Consumer/Audit approval and revision chain persisted; current Task IDs: `4652553108102791`, `4652553108720543`, `4652553108721135`.
- Retry/resume receipt re-read from durable native artifacts: the same Run has a `superseded` capacity attempt followed by a successful resume in the same observed native session; the capacity classification itself was deterministic harness injection after actual CLI session evidence. An earlier pre-start injected error case produced no resume and is not counted as a successful retry gate. No model rerun is planned.
- Durable fake-provider settlement interruption was recovered in an independent process after the local external-action receipt was persisted. The fake ledger retained exactly one accepted effect and recovery made zero provider calls. This proves post-receipt settlement recovery only; provider ACK before local receipt was not established, and no new reconciliation policy was added.
- Root's worker-disabled, fake-only browser acceptance is recorded in `root-ui-first-pass.md` and `root-ui-final-recheck.md`: status/Attention/history/task details, feedback, receipt, schedule aggregates, and Email learning were clicked; Email history detail passed only with in-memory fake source/provider (one fake mark-read, no send/save/star/flag). Source-unavailable UI entry was not found and remains unverified. The UI copy server and tab were closed by root.

## Source-identity continuation repair

Initial actual inverse/reforward cycles exposed that the canonical target handoff did not preserve `restore_task_entities()`'s Meeting delivery-key identities for the next source forward. Independently regenerated expected-baseline Meeting IDs therefore differ from the actual test target's IDs; these pre-fix, test-only renumberings are not production data and are not used to claim identity preservation. The paired release now atomically writes a sparse `ceo-task-run-source-identity-v1` continuation on inverse, reads it on every subsequent paired source preparation when present, and passes its validated identity/context into the existing Task converter. The continuation retains technical IDs/context only, no source bodies. A focused fixture proves publish → inverse → continuation → reforward keeps the exact Meeting `(id, task_id, business_key)`.

The new comparator retains exact DDL, SQL stored-value/type checks and FK comparison. For the independently forwarded baseline comparison only, `task_business_keys` uses its unique `business_key` and omits its converter-generated row `id`; the final clone separately hashes every Task business-key row including IDs, values and timestamps before/after inverse/reforward. The only State-value exception is `store_schema_version.updated_at`, and only when the service-state key and schema-version value are identical.

## Final current-data clone run

Command:

```sh
python tools/validate_task_run_entity_copy.py --native-roundtrip \
  --target /var/folders/74/yj2lxqs162q7rqzm0mj8nv1c0000gn/T/task5-final-acceptance.3nfz1yaa/corrected-final-native-clone.sqlite3 \
  --frozen-source /var/folders/74/yj2lxqs162q7rqzm0mj8nv1c0000gn/T/task5-final-acceptance.3nfz1yaa/fresh-frozen-production-source.sqlite3 \
  --predecessor-root /var/folders/74/yj2lxqs162q7rqzm0mj8nv1c0000gn/T/task5-final-acceptance.3nfz1yaa/predecessor-code \
  --work-dir /var/folders/74/yj2lxqs162q7rqzm0mj8nv1c0000gn/T/task5-final-acceptance.3nfz1yaa/corrected-roundtrip-evidence \
  --native-task-id 4652553108102791 --native-task-id 4652553108720543 \
  --native-task-id 4652553108721135
```

Completed with exit code 0. The independent JSON inverse child succeeded, the genuine pinned predecessor process cold-read Task `4652553108721135`, created Task `4652553108721136`, and read that new Task back; reforward from the same frozen source passed. The current-schema expected-native comparison checked all 47 user tables with exact DDL and FK contracts. The content-free source continuation carried 842 Meeting business-key identities (SHA-256 `9887241454d5e4f5e94b492db20a5a134155416bd819aabbdc6c57f5fe21b3bf`). Across inverse/reforward, all 8,632 Task-business-key rows, including row IDs and timestamps, matched their before hash `a40085c3f0bfbd8b2ec935d917e61004b273f9363e83aea45b6494cc24120d23`; Task/Run/runtime-attempt allocation high-waters were unchanged. All three actual native Task semantic hashes listed above were unchanged. The sole FK finding remained exactly `('runs', 190042, 'tasks', 1)`. Durable result: `/var/folders/74/yj2lxqs162q7rqzm0mj8nv1c0000gn/T/task5-final-acceptance.3nfz1yaa/corrected-roundtrip-evidence/post-native-roundtrip-result.json`.

The corrected source-shaped diagnostic identified the physical inverse differences: Tasks 37,678 matched source identities with 7,624 changed stored rows, all JSONB payload re-encodings and zero logical payload differences; Actions had 90,772 identities matched and 3,487 JSONB byte-only changes; observer rows matched by their declared composite primary key with zero changes. The 7,056 Run value changes were only scheduled-occurrence `first_scheduled_for` / `occurrence_count`; 94,859 missing Run identities were all scheduled occurrences and 31 were new. The exact first-forward receipt above establishes the scheduled aggregation counts. This is a migration diagnostic, not a blanket waiver: the expected-native vs actual reforward comparison and pre/post full business-key identity hash both passed.

## Focused validation and known test debt

The focused current owner regressions passed, including the source continuation and rollback/write-failure paths. The relevant focused command most recently passed 4 tests in 17.37 seconds:

```sh
pytest -q --assert=plain --tb=short -o faulthandler_timeout=20 \
  tests/test_task_run_entity_schema_release.py::test_paired_inverse_identity_continuation_preserves_meeting_key_id \
  tests/test_task_run_entity_schema_release.py::test_paired_task_run_inverse_handoff_is_durable_and_process_portable \
  tests/test_task_run_entity_schema_release.py::test_failed_handoff_write_rolls_back_and_release_rollback_accepts_source \
  tests/test_task_run_entity_copy.py::test_cross_forward_comparison_uses_declared_business_key_and_schema_marker
```

A prior bounded five-file legacy/current regression run returned 316 passed, 59 failed, 3 setup errors. The three setup errors were paired-publication setup failures in source-shaped email fixtures (`ValueError: unsupported current Action source schema; explicit paired release required`). The other failures include source-shaped fixtures still selecting retired Task payload fields (`sqlite3.OperationalError: no such column: tasks.payload_json`) and old Action fixtures violating current discriminator checks (`sqlite3.IntegrityError: CHECK constraint failed`). These are not accepted as green or globally dismissed as legacy; actual current caller regressions touched by this change have separate focused tests and browser evidence. The old full run is not repeated. A separately running pytest process PID 91825 was not stopped because the platform refused the attempted command; it is not counted as evidence for this report.

## Remaining gates

Remaining gates: independent whole-branch SPEC and QUALITY review, normal merge/integration with current main, affected validation, and root's standard deployment/readback. No push or deploy was performed by this task.
