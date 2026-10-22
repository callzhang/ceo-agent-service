# Task 7 integration and fresh-copy report

> **Final status addendum (2026-10-22):** The report below records the integrated pre-deploy candidate proof. The parent final whole-branch SPEC review and QUALITY review passed after the quiet-source correction; the standard deploy then completed through production commits `036b14624cddbcff04098ebec7697644f95370c0` → `bf07de8d6210709cd76887cc14ebb9ef23fa5600` → `87a34d299d5749af1af454bfdd01c48a936799a4`. Post-deploy production readback confirmed Store `2026-10-21.1` / Email53, 14 tables, 15,472 Actions, 18,621 receipts, 506 observers, seven retired populated source kinds at zero, health/status/queues/Attention/History HTTP 200, PID 2903, failed count 36 unchanged, Attention 19 unchanged, and sent detail Attempt 15914 / Action 105320 / receipt 5046 with parsed delivery status 200. Active queue items and process count were zero; queued count rose 146→155 through normal arrivals. The verified retained backup is `auto-reply-before-upgrade-deploy-9351777a-c7be-4845-889a-5759fd342790.sqlite3` (501,170,176 bytes, complete). Full details and boundaries are in [the committed production acceptance record](../../../docs/action-entity-production-acceptance.md).

Status: integrated release candidate complete for parent whole-branch review. Fresh production-derived forward/inverse/old-code cold read-write/re-forward, final-code native approval with strict in-memory provider, six focused backup/inverse tests, and documentation truth updates passed. Nothing has been pushed or deployed; production remains on its observed predecessor checkout.

## Integrated source and production source

- Isolated worktree: `/Users/derek/.codex/worktrees/action-entity-plans/ceo-agent-service`.
- Base was Task 6B candidate `254d07d61`; `origin/main` was fetched at `c2b4ab0155225fa7e0d66d342549c765c4e16c16` and integrated by ordinary merge `cfce5260cefe0c9232a13bc22b50111ed4b3250b`. The one `app/audit_web.py` conflict was resolved by retaining both the upstream verified-OA-return query and the Action-aware Attention row behavior. Upstream Email retry changes were merged intact.
- Production checkout was read only at `036b14624cddbcff04098ebec7697644f95370c0`; it was one commit behind the fetched `origin/main` at observation. The launchd database is `/Users/derek/Library/Application Support/ceo-agent-service/auto-reply.sqlite3`.
- Fresh SQLite online backup: `/tmp/action-entity-task7.BduYnC/production-current.sqlite3`, opened from the production database using SQLite URI `mode=ro` and `PRAGMA query_only=ON`; SHA-256 `9c12e8d44085838844a8304723fca6b414d437612c5b350e48c3476da678b06b`; `integrity_check=ok`; 516 schema objects and 11 tables. Store marker was `2026-10-19.12` at `2026-10-21T10:09:00.072102+00:00`; latest Email migration is 53 at `2026-10-20T01:16:18.144829+00:00`; source schema SHA-256 `9d97aae7d76124ab74af9091cf82260ee24a30857c54dc7435e80ab1e898e007`; source is the `fresh_memory_fk` variant.
- Exact production source was archived from commit `036b14624cddbcff04098ebec7697644f95370c0` to `/tmp/action-entity-task7.BduYnC/production-predecessor`. Archived `app/store.py` SHA-256 is `978992e537e5f7bd496d48fc800def6135eeb6be17ac883163984b66a682e8a2`; archived `app/email_store.py` SHA-256 is `788e6e70f6ebdf7fe0f7d7a8d95c0b143b73b12a0aeafc87e1eb59992f8dc49f`.
- The fresh source's only FK finding is `runs(190042) -> tasks(1)`. It is carried unchanged in the copy comparison; no zero-FK claim is made.

## Fresh-source forward, inverse, old cold write, re-forward

Command:

```sh
python tools/validate_action_entity_copy.py \
  --source /tmp/action-entity-task7.BduYnC/production-current.sqlite3 \
  --workspace /tmp/action-entity-task7.BduYnC/lifecycle \
  --old-root /tmp/action-entity-task7.BduYnC/production-predecessor \
  --new-root /Users/derek/.codex/worktrees/action-entity-plans/ceo-agent-service
```

The existing release path accepted the exact Store19.12/Email53 source and published Store `2026-10-21.1`/Email53 on its owned copy. A real current public prepared-Action write/readback survived the current-data inverse. The archived production Store then performed a public prepared-Action cold write; current re-forward preserved both new prepared facts. This first lifecycle did not read a native Sent fact; the separate native run below proves that. The source SHA remained unchanged. The independent same-population check passed with 520,789 stored rows, 103,674 typed Action identities, 506 Sent references, exact schema/sequences and one inherited FK finding; out-of-scope physical Action-ID remaps were zero.

The lifecycle report is `/tmp/action-entity-task7.BduYnC/lifecycle-report.json`. The source-derived capacity pair used the same 4,096-byte pages, VACUUM packing and zero freelist pages: 494,678,016 bytes before and 492,015,616 after, a decrease of 2,662,400 bytes. Table pages increased by 1,548 and index pages decreased by 2,187. This is a measured copy result, not a production-size promise. The target has 40 Action discriminator values: 15,470 unified `action` rows and 39 non-Action values. Seven source Action families and 30,700 rows were retired from their old discriminator values: candidate attempts 424, Email actions 11,543, Email attempts 13,567, external results 600, prepared postfixes 1,533, Sent replies 2,527, and Sent observers 506. The validator reports 293 source indexes and 284 target indexes (`action_index_delta_count=-9`); exact additions/removals and byte totals are in its JSON. The current identity map has 31,817 rows; its table uses 2,768,896 bytes and its two indexes use 2,940,928 bytes (5,709,824 bytes total). Current Action/receipt/observer tables occupy 148,869,120 bytes including that map. Field ownership and exact old/new DDL are in `docs/action-entity-field-map.json` and `docs/action-entity-current-target.sql`.

## Integrated-code native check

The original Task 6 native validator stays pinned to its accepted A1 cohort and was not edited. A temporary copy at `/tmp/action-entity-task7.BduYnC/validate_integrated_native.py` changed only the input-copy SHA guard to the just-validated production-derived current copy (`/tmp/action-entity-task7.BduYnC/lifecycle/after-reforward.sqlite3`, SHA-256 `5d3d6676647dc43b28a803067937fd6c720839f98d458bbc13243be4dfd78391`). Original validator SHA-256: `70a087694fc1df8227ce0e234aa1960545ebb6c9655916699ec57fe8573b6a06`; temporary harness SHA-256: `b4a86f31cf56e83a1d19f7f22a37ea19e22107fc00e5d3ec60ac18958675f071`. Provenance is recorded in `/tmp/action-entity-task7.BduYnC/native-source-manifest.json`.

The final integrated source completed a genuine native Consumer run `392636` and Audit run `392638` using `codex_oauth`, `gpt-6-luna`, high reasoning, CLI `0.163.0`, and the existing `InMemoryProvider` substitute. Task `5104454596968379` reached `done`; Audit approved; exactly one substitute send, verification, receipt and Sent row were recorded. No real provider client was configured. In the separate roundtrip, archived production `app/store.py` was loaded from `/tmp/action-entity-task7.BduYnC/production-predecessor/app/store.py` (SHA-256 `978992e537e5f7bd496d48fc800def6135eeb6be17ac883163984b66a682e8a2`); it read the native Sent body and provider receipt linked to Audit run `392638`, then wrote a cold prepared Action. The native result is `/tmp/action-entity-task7.BduYnC/native/approve/native-result.json`; original trajectories remain in `~/.codex/sessions` and are referenced there.

The native helper's roundtrip baseline path still names the older Task 6 A1 cohort. To bound that mismatch, both source contracts were read using the current read-only release preflight. The fresh production backup and the referenced A1 baseline have identical source marker including timestamp, all Email migration versions/timestamps, source schema digest, source variant, FK findings, and target versions; each normalized contract SHA-256 is `1b0f6b0668e362c54ec23cb4f449d1892a1980814b182bcfe559220748a3a9e5`. The separate full lifecycle above independently performed forward/inverse/old production cold write/re-forward from the fresh source. The native helper's roundtrip also matched 520,809 rows, exact schema/sequences and the same FK finding, with zero out-of-scope Action-ID remaps. No second native turn was run.

## Source-to-target retirement matrix and verification

The exact field-level disposition for every source column, typed identity, FK, target field, inverse rule, index and retirement decision remains machine-readable in `docs/action-entity-field-map.json`; target DDL is `docs/action-entity-current-target.sql`. The fresh production source declared nine covered Action discriminator types; seven were populated. In the matrix, `Action` means the common `_kind=70` Action owner, `Receipt` means `action_receipts`, and `Observer` means `action_observers`.

| Legacy source family | Source rows | Target owner(s) | Verification |
| --- | ---: | --- | --- |
| `candidate_action_attempts` | 424 | Action + per-execution Receipt | Old public prepared-action cold write and current re-forward; typed current-fact comparison |
| `email_actions` | 11,543 | Action | Full source-to-target schema/population comparison and current-data inverse |
| `email_action_attempts` | 13,567 | Receipt linked to its typed Action | Current facts and attempt ordering survive inverse/re-forward |
| `external_action_results` | 600 | Receipt; shared exact results retain typed identity | Receipt identities, values, FK findings and sequences matched |
| `outbound_postfixes` | 1,533 | Prepared Action | Archived production Store public cold write and re-forward |
| `sent_replies` | 2,527 | Action + immutable provider/Sent Receipt | Native Sent body/receipt read by archived production Store; inverse/re-forward |
| `sent_reply_observers` | 506 | Observer linked by typed Sent identity | All 506 typed Sent references matched; no uncovered ID remaps |
| `native_reply_dispatches` | 0 | Dispatch facts represented on prepared Action receipt | Zero-row declaration verified in source inventory; no source row invented |
| `outbound_postfix_receipts` | 0 | Receipt linked to prepared Action | Zero-row declaration verified in source inventory; no source row invented |

The nine declared names therefore do not mean nine populated families or nine detached legacy tables. The two zero-row names are child facts whose target owner is the existing prepared Action/receipt design. Across the complete source-derived copy, the validator matched 520,789 stored rows, 103,674 typed Action identities and 506 Sent references; exact DDL/sequences and the original single FK finding matched, and uncovered Action-ID remaps were zero. The physical schema contains 11 source tables and 14 target tables, with three new tables: receipt, observer and identity map. Seven populated families totaling 30,700 rows retired from their prior discriminator values. Index inventory moved from 293 to 284 (delta -9). This is scoped retirement, not full Actions retirement.

## Completion and release boundary

The existing backup verification/failure-preserves-previous tests and paired Action release tests passed: six selected tests, `6 passed in 13.01s`, using:

```sh
python -m pytest -q \
  tests/test_database_backup.py::test_a_backup_verifies_before_removing_earlier_copies \
  tests/test_database_backup.py::test_a_failed_backup_preserves_previous_verified_copy \
  tests/test_database_backup.py::test_integrity_failure_preserves_previous_copy_and_source \
  tests/test_database_backup.py::test_missing_completion_marker_preserves_same_destination \
  tests/test_action_entity_schema_release.py::test_new_action_and_recall_survive_inverse \
  tests/test_action_entity_schema_release.py::test_conflicting_identity_aborts_without_mutation
```

Design/spec, detailed plan, overall program/progress, architecture/runtime docs, changelog and this report now describe the verified candidate and its limits. The first release covers the actual reviewed/direct-email/prepared/Sent Action paths; unsubscribe browser continuation/steps and later Task/Run/Email ownership work remain outside this unit. It does not retire every Actions type.

The production predecessor remains live; no post-deploy schema, PID, queue, Attention or History claim applies. Parent owns final whole-branch review and any later push/deploy.
