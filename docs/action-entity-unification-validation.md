# Action Entity copy validation

`tools/validate_action_entity_copy.py` runs Action Entity migration checks on an owned workspace beneath `/tmp`. It takes a production SQLite source through a `mode=ro` connection and SQLite's backup API, applies the paired Store/Email schema release to an owned copy, writes and reads one current prepared action through `AutoReplyStore`, inverses the current schema, makes an old-code public cold write in a separate process, and re-forwards the same cohort. Its report contains digests and structural counts, not action bodies or provider trajectories.

The independent same-cohort comparison keys Actions by `(record_type, legacy_key)`. It permits physical `actions.id` changes only for members of `app.current_action_schema.RETIRED_ACTION_TYPES` and checks `sent_reply_id` through the exact typed Sent identity. It still requires unique typed identities, unchanged action values and SQLite value types, unchanged row/key sets and counts, exact DDL and indexes, exact sequences, exact foreign-key findings, and unchanged physical IDs for all uncovered Action types. The tests include body-loss, wrong-Sent, missing-Sent, and out-of-scope-ID mutations, and assert a `candidate_action_attempts` remap is covered by the declared retired set.

Run the focused regression suite with:

```sh
python -m pytest -q tests/test_validate_action_entity_copy.py
```

Run the copy validator only against an explicit source, new empty `/tmp` workspace, pinned old code root, and selected current code root:

```sh
python tools/validate_action_entity_copy.py \
  --source /path/to/readable/source.sqlite3 \
  --workspace /tmp/action-entity-validation.XXXXXX/copy \
  --old-root /path/to/pinned-old-code \
  --new-root /path/to/current-code
```

The current validator measures an old-schema baseline and a re-forwarded current database after VACUUM, and reports actual `PRAGMA page_size` together with table and index pages/bytes, freelist, file size, index changes, and Action Entity table overhead. It requires both measured databases to have the same positive page size, population digest, and VACUUM state before comparing capacity. It does not VACUUM a production database or claim a business outcome from the native CLI or UI. Native CLI and browser acceptance are separate gates.

## Task 6A saved-copy evidence

The corrected full-copy lifecycle was run before the typed-identity comparator update at `/tmp/action-entity-task6-corrected.IH2XQu/copy`; its process log is `/tmp/task6-corrected-full-run.log` (SHA-256 `1dee340efc335b6ff69e9bd2852ff1f4ad1f75391d99df9ff0b094e49771b4b7`). The lifecycle's current writer/readback, inverse, actual pinned old-code cold write, and re-forward checks passed. Its then-current generic whole-row digest failed because inverse conversion remapped physical IDs for covered retired Action types. The original low-level `final_body` SQL failure trace was not retained, so that earlier failure's cause remains unproven. The separate first-red artifact `/tmp/task6-first-red.txt` (SHA-256 `5cf4573c8433d596067c34cd34de4d80f7d8c7cdbe3becf99a3caf951f8dcb55`) records pytest collection failing because the validator module did not exist; it is not the SQL failure trace.

The corrected comparator was run against the preserved lifecycle copies:

- Baseline: `/tmp/action-entity-task6-corrected.IH2XQu/copy/before-inverse.sqlite3`, SHA-256 `7778183eeb462d95871e23076e9ab58562b219811a7272ebe47b6bcd3a2d8148`.
- Independently inverted current cohort: `/tmp/action-entity-task6-corrected.IH2XQu/copy/capacity-inverse.sqlite3`, SHA-256 `f912b605fbeb7450167de97f3853b4262c9b233c807c7b3928e962f15e87c7a0`.
- Re-forwarded current cohort: `/tmp/action-entity-task6-corrected.IH2XQu/copy/cohort-current.sqlite3`, SHA-256 `176ad9cbfb2d835933f72a714252467cbc54209297e7f26bc4a2f2c58363538b`.

The comparator matched 102,804 typed Action identities and 518,438 stored rows with exact schema, row counts, sequences, and the same single inherited foreign-key finding. It found 27,915 permitted ID remaps: 11,387 `email_actions`, 13,478 `email_action_attempts`, 55 `outbound_postfixes`, 2,508 `sent_replies`, and 487 `sent_reply_observers`; uncovered remaps were zero. All 487 observer Sent references resolved to the same typed Sent identity. The matching semantic population digest was `ef6a2160a56b41f1aa6fd7b27586d4f67923dd93db6ad1c62b40f36f482a95b1`.

To keep evidence non-destructive, the capacity run backed up each saved database to its own measurement copy under `/tmp/action-entity-capacity-proof.jlai9a45` before VACUUM. The measured baseline copy SHA-256 is `7778183eeb462d95871e23076e9ab58562b219811a7272ebe47b6bcd3a2d8148`; the measured current copy SHA-256 is `61dc58997d740b2eca4a96ab520ffb670c4b2ae52c8bf425803d1e5f13b18566`.

| VACUUMed measure | Old-schema baseline | Re-forwarded current | Change |
| --- | ---: | ---: | ---: |
| File bytes | 490,471,424 | 487,849,984 | -2,621,440 (-0.53%) |
| Table pages | 90,318 | 91,857 | +1,539 |
| Index pages, including SQLite autoindexes | 28,915 | 26,747 | -2,168 |
| Index bytes | 118,435,840 | 109,555,712 | -8,880,128 |
| Freelists | 0 | 0 | 0 |
| Page size | 4,096 | 4,096 | unchanged |
| User-created indexes | 293 | 284 | -9 |

The current database's four Action Entity tables occupy 146,780,160 bytes: `actions` 133,779,456; `action_receipts` 10,252,288; `action_observers` 24,576; and `action_entity_identity_map` 2,723,840. The identity-map indexes add 2,895,872 bytes (2,543,616 unique autoindex plus 352,256 lookup index). The net file reduction is small: current table pages grew by 1,539 (6,303,744 bytes) while index pages fell by 2,168 (8,880,128 bytes). These figures describe the tested same-fact copy and do not imply a production VACUUM or an operational performance change.

The original A1 focused run passed 9 cases. After the page-size review fix, the focused suite passes 10 cases. The reusable CLI was not rerun end-to-end after the comparator correction; the saved-copy comparator and paired VACUUM measurement were run directly to avoid repeating the already completed full lifecycle. Native CLI, browser acceptance, and full Task 6 review remain separate work.

## Review fix round 1

Task 6A1 review identified that the reusable capacity assertion did not record or compare page size, although the saved measurement pair was both 4,096 bytes per page. `_measure` now records the actual `PRAGMA page_size`; `assert_capacity_pair` requires matching, positive page sizes as well as matching populations and VACUUM state. A focused measurement test uses an actual 1,024-byte SQLite page size, and the pair test rejects mismatched and nonpositive sizes. The saved 4,096-byte capacity evidence above remains unchanged.

## Task 6A2 native lifecycle

Task 6A2 continues from the reviewed A1 copy at `/tmp/action-entity-task6-corrected.IH2XQu/copy/cohort-current.sqlite3` (SHA-256 `176ad9cbfb2d835933f72a714252467cbc54209297e7f26bc4a2f2c58363538b`). Its native Consumer/Audit calls use `codex_oauth`, `gpt-6-luna`, `high`, and CLI `0.163.0`; the native home remains `~/.codex`. The test-only in-memory provider is passed through the normal `SystemExecutor`. The entrypoint and exact acceptance evidence are tracked in `.superpowers/sdd/2026-10-21-action-entity-unification/task-6a2-report.md`.

The first two fail-first calls ended in `no_action` with 0 substitute sends and 0 verifications. Their original trajectories showed the test prompt requested a send while also forbidding sending. A third native run used the corrected local-substitute instruction but still had no synthetic group roster; Audit asked for those facts, Consumer selected an undeclared `reply_to_message`, and the restricted executor failed with `read_dingtalk_messages_in_window is not a function`. That run ended `failed` with 0 substitute sends, receipts, or Sent rows. The harness now supplies an authoritative synthetic roster and explicitly states that only the registered `send_group_message` operation is available; its focused tests pass (`2 passed`).

Historical saved-copy preflight found a `candidate_action_attempts.external_action_key` conversion loss when no canonical result owner existed. The exact orphan-key case was fixed in starting Task 6A2 HEAD `6ba0c52a4` and separately reviewed. The corrected approve lifecycle and roundtrip are recorded below; revision/recovery, historical readback, UI, and full Task 6 review remain open.

The orphan-key loss was fixed in the starting Task 6A2 HEAD `6ba0c52a4` and separately reviewed, with 14 migration tests and an owned-copy proof covering all 402 candidate attempts. The narrow approve-only continuation then completed one actual native Consumer/Audit approve, one in-memory substitute effect and verification, one receipt, and one Sent row. The Sent fact survived current inverse → pinned old Store public read and cold prepare → current re-forward. The old module was `/private/tmp/ceo-action-entity-validation.8rUAvK/old-code-897227b/app/store.py` with SHA-256 `0d426abbfe0042247cdbc40f41d559f294b1f7299436724602f0e38155268d4c`; its Sent read returned the native body SHA-256 `5abf84fadcaa8093e22fe633ad0ed4effd80a07c0e3373f4d11ad71f3033ca03`, provider receipt JSON SHA-256 `9e6db84058746131ea132cd8d5373fa2ffadfbb56ac35269946f73ff884f7734`, and Audit AgentRun `391694`. Its separate cold-prepared body SHA-256 was `2f4920805354dc18c18dbd505142f9ed33a70948395c330e90bc88399bc86378` and was validated against its own re-forwarded `final_body`.
The same-database current-fact check matched all nine family projections by typed business identity and all business values. It permits only the source IDs for the one new candidate and one new Sent to appear during inverse, and proves the new observer points to that exact typed Sent identity. The old cold prepare adds one separate postfix fact. This A2 scope accepts only the approve effect and its roundtrip; the A3 revision and recovery branches are recorded below. Historical readback, UI, and full Task 6 review remain open.


## Task 6A3 native branch matrix

Task 6A3 used the same reviewed A1 cohort and genuine `codex_oauth` / `gpt-6-luna` / `high` Consumer and Audit runs. The provider remained an in-memory substitute connected through the normal `SystemExecutor`. Both successful cases completed a same-cohort current inverse, pinned old Store public Sent read and cold prepare, and current re-forward. Exact native trajectory pointers, copy hashes, all nine current fact-family counts, and typed ID comparison evidence are in `.superpowers/sdd/2026-10-21-action-entity-unification/task-6a3-report.md`.

| Case | Native decision path | Persisted substitute facts | Same-cohort roundtrip | Outcome |
| --- | --- | --- | --- | --- |
| Revision first run | Audit returned stale 2 / 50 facts; later returns identified unsupported historical claims and a candidate/purpose-output body mismatch; bounded run ended failed at revision 3 | 0 sends, 0 receipts, 0 Sent rows | No Sent existed for this failed candidate, so the native Sent read roundtrip does not apply | Authentic failed feedback-cycle evidence; preserved separately from the passing rerun |
| Revision controlled rerun | Audit returned revision 0 for stale facts; Consumer corrected to 3 / 100; Audit approved revision 1 | 1 send, 1 verification, 1 receipt, 1 Sent; Sent links to approving Audit run 391698 | Passed; 518,465 rows and 102,818 typed Action identities; 29,148 allowed retired-type remaps; 0 out-of-scope remaps; 488 typed Sent references | Native revision and roundtrip passed |
| Settlement recovery | Consumer and Audit approved revision 0; acknowledged receipt and Sent committed before the test-only task-finalization interruption; reopen completed from persisted facts | 1 send, 1 verification, 1 receipt, 1 Sent; exactly 2 native runs total and no second send | Passed; 518,458 rows and 102,815 typed Action identities; 29,147 allowed retired-type remaps; 0 out-of-scope remaps; 488 typed Sent references | Native settlement recovery and roundtrip passed |

The two successful cohorts each add the one old-code cold-prepare postfix during roundtrip; other current fact projections match by typed business identity and values. The first revision failure is not counted as a successful case and remains part of the recorded behavior. Historical completed-run/no-candidate behavior coverage is documented in Task 6A4 below; browser/UI acceptance and the full Task 6 review remain separate gates.

## Task 6A4 historical readback fixture

| Case | Fixture lineage | Readback result | Preserved evidence | Scope |
| --- | --- | --- | --- | --- |
| Completed legacy reply with no candidate; permission and receipt-target mutations | Synthetic Store 19.12 / Email 53 `fresh_memory_fk` source schema with Task8 canonical input, completed AgentRun, completed unreviewed ReplyAttempt, Sent row, and canonical provider receipt; explicitly prepared and published through the current Action Entity release | Exact readback settles the historical receipt and task without inventing candidate/Audit approval. Permission and canonical-target mutations are rejected before further changes. | Original provider result body and ambiguous verification, canonical receipt's first AgentRun identity and run row, and the one Sent identity/body remain represented; no candidate or candidate-action-attempt facts appear before or after. Negative preconditions remain unchanged by the rejected readback. | Three focused nodes and all 31 tests in `tests/test_dingtalk_delivery_repair.py` pass. Synthetic local fixture only; readback invocation has no dispatch-capable handler, so the test does not measure provider sends. |

Task 6A4 repairs the source fixture for historical behavior coverage; it does not change runtime policy. The test verifies stable persisted Action/Sent identities and absence of candidate execution facts. It does not exercise or measure provider dispatch.

## Task 6B copied-console browser acceptance

Task 6B used the exact approved Task 6A3 revision-rerun candidate `/tmp/action-entity-task6a3.VOOfhl/owned-revision-rerun/revision/candidate.sqlite3` (SHA-256 `73515ca8c30230a0bb67149aa9773a4584ac418caaa0050d9eee7960bd6091d0`) and created an SQLite online backup at `/tmp/action-entity-task6b.vESxM3/acceptance.sqlite3` (initial SHA-256 `1d816993bea6fbaf6f31f2b0ff7d89c8a24ba78645258fb52e1846663370e23c`). The source SHA-256 remained unchanged. After browser writes, a second SQLite online backup at `/tmp/action-entity-task6b.vESxM3/accepted-final.sqlite3` has SHA-256 `7732ba9cac36047b19d497aeb5ad1959995e9f2d034119ccc9983920f21abdc5` and was used for final persisted-state readback.

The copied app was imported from isolated checkout HEAD `fd6aaeb4275a515c650104fb17b4b21c54838be0`; server startup called `app.audit_web.create_audit_app` with the built assets at `app/static/workbench`, an empty `RuntimeRegistry`, and the existing `tests.test_email_web_api._NonExecutingExecutor`. Uvicorn served `http://127.0.0.1:8876` with `lifespan="off"`, one process and no workers. `app.audit_web.DwsClient` was replaced only in the task-owned launcher at `/tmp/action-entity-task6b.vESxM3/serve_acceptance.py`; the strict fake refused configured robot credentials and allowed exactly one recall target: conversation `native-action-entity:1189dc59-97ca-4228-8b41-4e83b28db344`, message `native-copy-provider-receipt`. The client has no send method, and no background scheduler or live provider was started.

Through the visible Chrome UI, History was filtered to the native fixture and the matching row was opened as Attempt #15875. Its detail displayed the business result, Action #119688, provider receipt #1153 with message ID `native-copy-provider-receipt`, and the historical-source-unavailable label `来源事实未提供`. Feedback saved from the page reads back through `AutoReplyStore.get_reply_attempt(15875)` on the final backup as `Task 6B UI acceptance: feedback is linked to this original Attempt and Action.` The original Attempt remains completed.

The same detail page's recall button was used once. The strict fake log `/tmp/action-entity-task6b.vESxM3/fake-dws-calls.jsonl` contains exactly one `recall_message` call for the allowed receipt. On the final backup, Action #119688 has `recall_status=recalled`, an empty recall error, and `recalled_at=2026-10-22 10:36:27`. The immutable prepared-body SHA-256 is `02850290395b37a6bab9660cfbf69a6ba438fe3af369b9e9bcffc6eccf584b64`; provider receipt #1153 still has result digest `efaf3ca21cda0f0e1d9854852ead10e1670abb1f9f471ea2f82241460357c9a2` and the original Sent verification. The browser visibly showed the recalled state and saved feedback. This is copied-console and strict-fake evidence only; it does not claim a real provider recall or production outcome.

CUA emitted browser screenshots inline for the filtered History result, original Attempt detail/receipt, saved feedback, and recalled detail. The desktop `screencapture` command could not capture this display, and CUA exposes screenshot bytes only through its documented inline observation API in this session; therefore no screenshot file is claimed. The inline CUA observations, task-owned server launcher/log, final SQLite online backup, and safe readback above are retained for parent review. The visible copied Dashboard also reported a service-status read failure and its unrelated failed-item cards contain copied historical content; those cards were not used as acceptance evidence. No production code or frontend source was changed.

### Dashboard reader fix visible-browser recheck

After the reviewed Dashboard-reader fix at HEAD `1aadefb95e63282e574b8009f9294cab6014c9ac`, a new SQLite online backup was made from the accepted final copy at `/tmp/action-entity-task6b.vESxM3/dashboard-recheck.sqlite3`. The served worktree changed only Python/docs/tests from the earlier build, so the existing frontend build assets were unchanged. The launcher reused the same no-worker `create_audit_app` seam, empty runtime registry, non-executing executor, and strict recall-only fake; the fake was not called. The new final backup `/tmp/action-entity-task6b.vESxM3/dashboard-recheck-final.sqlite3` has SHA-256 `7732ba9cac36047b19d497aeb5ad1959995e9f2d034119ccc9983920f21abdc5`, identical to its accepted-copy input. The Task 6A3 source hash also remained unchanged.

In visible Chrome, the Dashboard loaded `Service 运行中`, with Active `0` and Queued `56`. Selecting Queued showed Email actions. Expanding one visible Email row displayed typed queue ID `email_actions:126046`, status `pending`, and first attempt. Its `detail_url` is empty, as is the established queue projection for an unlinked Email action; the corresponding Attention detail helper has no Email-action-to-Attempt mapping. The UI therefore showed no direct detail link for this row, and no common Action detail URL was claimed or guessed.

Settings → Attention visibly loaded 17 unresolved items. Expanding an Email-action group showed its error detail and the existing text `此问题尚无关联的 Attempt 详情。` A read-only `AutoReplyStore` projection on the final backup returned the two Email-action Attention records as string IDs `134250` and `134221`, both `failed`; direct read-only lookup confirmed both IDs in the current `actions` table, with operation `move` and status `failed`. The matching Dashboard queue row and Attention IDs were also checked via the existing readers on that same snapshot. Dashboard service status, queue Email rows, and Attention now load in the browser. This verifies the fixed readers and current UI state without adding an unimplemented link behavior.

No new screenshot file was created: the prior host screenshot failure and CUA inline-only screenshot limitation still apply, and this recheck avoided broad screenshots because copied Dashboard/Attention cards include unrelated private history. CUA accessibility observations recorded the visible states. The server was stopped through its owned PTY with Ctrl+C; feedback, recall receipt, body, and all previous acceptance evidence remain intact.


## Task 7 — integrated fresh-production-source candidate (2026-10-22)

The isolated candidate merged the latest observed `origin/main` commit `c2b4ab0155225fa7e0d66d342549c765c4e16c16` into `cfce5260cefe0c9232a13bc22b50111ed4b3250b`, preserving both Action-aware Attention behavior and upstream verified-OA-return behavior. Production was observed read-only at Store `2026-10-19.12` / Email53, checkout `036b14624cddbcff04098ebec7697644f95370c0`. A SQLite online backup from the actual launchd database was opened read-only, integrity-checked, and paired to an archive of that exact production commit. The source contained 11 tables and the target declares 14, adding `action_receipts`, `action_observers`, and `action_entity_identity_map`.

The fresh source has nine covered legacy Action discriminator declarations; seven had populated rows (30,700 total): `candidate_action_attempts` 424, `email_actions` 11,543, `email_action_attempts` 13,567, `external_action_results` 600, `outbound_postfixes` 1,533, `sent_replies` 2,527, and `sent_reply_observers` 506. `native_reply_dispatches` and `outbound_postfix_receipts` were declared but had zero rows in this source; their actual child facts/DDL map to the prepared Action and receipt/observer ownership contracts. This is partial retirement: specialized unsubscribe, other Action kinds, and direct retry control rows remain. The source had 293 indexes, target 284 (delta -9), not a nine-index increase.

The complete fresh-source copy validator passed forward, current-fact inverse, a public prepared-Action write through archived production Store code, and re-forward; source SHA remained unchanged. It matched 520,789 stored rows, 103,674 typed Action identities, 506 Sent references, exact schema/sequences, zero uncovered Action-ID remaps, and the same inherited FK finding. The native approve used the final integrated code against this production-derived re-forwarded copy. Genuine Consumer/Audit runs 392636/392638 completed task 5104454596968379 as `done` with Audit approved and exactly one strict `InMemoryProvider` send/verification/receipt/Sent fact; no real provider call was configured. The archived production Store read that native Sent body/receipt and performed the old-code cold prepared-Action write. The native helper's captured schema contract matched the fresh source exactly; its baseline data copy was from the earlier A1 cohort, while the separate full lifecycle above used the fresh production source for all data-sensitive inverse/re-forward checks. Exact hashes, paths, contract limits, and trajectories are in the [Task 7 report](../.superpowers/sdd/2026-10-21-action-entity-unification/task-7-integration-report.md).

Targeted checks passed: six existing tests covering verified-backup replacement ordering, prior-copy preservation on backup/integrity/completion-marker failure, and Action inverse/conflicting-identity behavior (`6 passed in 13.01s`). No full suite was run. The capacity pair used identical 4,096-byte pages and VACUUM state: 494,678,016 bytes before and 492,015,616 after (-2,662,400 bytes); table pages +1,548, index pages -2,187. These are copy measurements, not a production-size or runtime-performance claim.

The branch is an integrated candidate only. It has not been pushed or deployed; production remains on the observed predecessor. Parent whole-branch review precedes any release action.
