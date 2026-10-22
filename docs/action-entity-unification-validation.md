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

The strict same-cohort old-schema comparator matched 518,458 stored rows across 11 tables, all 102,815 typed Action identities, 488 Sent references, schema, sequences and the same single inherited FK finding. There were 29,147 physical ID remaps across six members of the actual nine-type retired set: 44 candidate attempts, 1,241 postfixes, 2,509 Sent rows, 11,387 email actions, 13,478 email attempts and 488 observers. Every other Action ID remained exact. The full semantic digest was `4a8983b12694d2d70e3654ae294ac8ab548091c9ae6e46da3b6a1f8bff837938`; the detailed snapshots and native trajectory pointers are in `.superpowers/sdd/2026-10-21-action-entity-unification/task-6a2-report.md`.

The same-database current-fact check matched all nine family projections by typed business identity and all business values. It permits only the source IDs for the one new candidate and one new Sent to appear during inverse, and proves the new observer points to that exact typed Sent identity. The old cold prepare adds one separate postfix fact. This scope accepts only the approve effect and its roundtrip; revision, recovery, historical readback, UI, and full Task 6 review remain open.
