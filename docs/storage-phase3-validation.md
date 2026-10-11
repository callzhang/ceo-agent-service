# Phase3 input storage validation

Status: deployed through the normal quiet workflow; source/copy/inverse/recovery/live readbacks passed. Full Quality CI on the runtime commit and follow-up fixture-only commit is still pending.

Approved scope: retain provenance, necessary lifecycle state, adopted business results and execution receipts; canonical current/claimed reply inputs; no retention deadline or new audit/recovery policy. Task inputs omit only unused scheduled Skill material. Existing WSI compression is preserved.

## Baseline and source integration

The consistent SQLite backup API copy was captured from production code `b998f225`, Store `2026-10-10.2`, Email49. Implementation integrates main through `e264a3f8`, including the lossless WSI codec, writer timing and manual rerun frozen context. No production source was edited.

150 tables; 7,261 reply tasks; 7,366 input rows; 9,080 terminal WSI rows. Old source startup on the copy changed no table fingerprints. One pre-existing Meeting FK violation remains identical; this migration does not repair unrelated business history.

## Forward and inverse evidence

| Check | Result |
|---|---|
| Forward unaffected tables | 146 of150 byte-value fingerprints identical; only two reply tables, Store marker and expected input sequence changed |
| Original task projection | All7,261 APIs equal, excluding the two newly exposed reference fields |
| Original input identity/content | All7,366 IDs, revisions, source metadata, provenance and original/compact API meaning equal |
| Unmatched current snapshots |422 exact legacy snapshots appended; old task watermarks unchanged, no original input overwritten |
| FK/integrity | Full FK violation fingerprint equals baseline; quick_check=ok |
| Inverse with replacement writes |147 tables unchanged semantically; only reply schema/content format and Store marker reverted; all sequence values exact |
| Replacement task projection |All7,265 APIs equal before/after inverse, including four copy-only synthetic tasks from repeated exercises |
| Replacement data |Distinct current/claimed snapshots, new revision, synthetic adopted result/receipt and exact compressed WSI payload retained |
| Actual old runtime |Captured b998f225 Store+Email startup, orphan recovery, claim, current frozen Skill read and requeue passed; no provider calls |
| Re-forward/restart |Re-forward and repeated startup passed; zero retired copied JSON values |

The inverse restores the captured original parent declarations and index SQL, rather than adding columns with different defaults. It rebuilds from the latest canonical data, preserving replacement writes; it does not overwrite a database backup. Raw `sqlite_sequence` row order may change after table recreation; the named sequence values are identical.

Historical claimed pointers are not invented. Each queue claim captures the then-current input with generation validation, preserving existing watermark and reclaim behavior. This does not claim an immutable input across all later claims on the same generation.

## Space

| Measurement | Bytes |
|---|---:|
| Original parent payload/provenance copies |6,922,755|
| Original input payload/provenance |7,068,808|
| Canonical historical input payload/provenance, including422 preserved snapshots |4,861,995|
| Logical duplicate bytes removed |9,129,568|
| Copy file before VACUUM |414,822,400|
| Copy file after VACUUM |376,016,896|

The copy-only VACUUM figure includes pre-existing free pages and packing of unaffected tables. It is not a Phase3-only saving or a production file-size result. Production VACUUM is outside this release.

The new Task envelope fixture is22,338→276bytes (98.8% reduction), with byte-identical actual Task prompt and unchanged original shared context. This is a synthetic fixture, not a production historical cleanup.

## Verification and review

Focused selections:588 Store/task/dispatcher/retirement/Skill/index tests;730 Email/receipt/API/adapter/Scheduled consumer tests;116 combined Task/WSI/scanner/prompt/history/import tests;34 release/publication tests;6 dispatcher/terminal CLI tests;66 quality/CLI checks. Selections overlap and must not be summed as a unique total. Post-review selection:55 canonical input/system action/WeChat finalizer checks pass;8 Store release tests cover five updater failure stages with post-start receipts.

Independent migration review found and required repair of body-only single-chat refresh semantics, an old quality-check column reader and a current-versus-claimed system action reader. All three confirmed review findings were repaired with failing-then-passing regressions. The explicit-generation WeChat finalizer also uses the claimed source, without changing its manual/current delivery API or queue policy. Independent final review recommends GO with no remaining P1/P2 blocker. It rechecked the confirmed fixes, matched inverse and copy evidence; production readback subsequently passed (below).

Read-only unsubscribe count/latest comparisons on equal old/new copy rows preserved exact results. Median count1.26→4.14ms, latest1.15→4.07ms; current projection adds canonical primary-key reads. This actual corpus showed no release-blocking query cost.

## Production acceptance

Normal deploy completed `b998f225→984168d6`; PID28507. Store2026-10-11.1, Email49.7,261tasks and7,788inputs; invalid current/claimed references0; retired copied JSON0. Healthz/Status/Attention/History200; History returns20items. No new failed IDs in reply, summary, Agent, Meeting or Task runs compared with the pre-release snapshot. The original Meeting orphan remains identical.

All eight checked business/receipt tables retain the original fingerprints: external action results281, Agent execution receipts115, review candidates308, candidate reviews301, candidate action attempts76, sent replies2291, reply attempts14676 and WSI9080.

The production file remains414,822,400bytes; WAL35,782,232bytes at readback; page size4096, page count101275, freelist4587. No production VACUUM. Exactly one complete, quick-check-ok backup is retained: `backups/auto-reply-2026-10-11.sqlite3`, Store2026-10-10.2/Email49,7261tasks.

Follow-up `01b4b031` updates only remaining test fixtures;15Email fault cases,55routed execution cases and7CLI/reviewed-human cases passed without assertion changes. No second runtime deployment is required for this test-only commit.

Concise machine-readable evidence is stored in the phase3-copy-checks and phase3-live-checks JSON files beside the design. Disposable data/code copies remain task-owned temporary files until final CI/readback cleanup.

## Mainline WSI codec boundary follow-up

The pre-Phase3 e264a3f8 Quality baseline had12failures,11583passes. Eleven reproduced failures were direct WSI BLOB reads/CAS or a raw-BLOB test fixture; the twelfth was missing backup-retention wording in the documentation. This is baseline evidence, not a claim that the current pending full CI has completed.

Attention now uses the existing decoder only on its query connection before the existing JSON paths, preserving status/order/limit/render and making no writes. Offline exact replay compares decoded input but retains raw TEXT/BLOB CAS and encoded writes, keeping all source/hash/state/lease/copy guards.174original boundary cases and4new TEXT/BLOB cases plus4existing nearby cases pass in one182-case selection;13documentation contracts and11independent review cases pass.

A synthetic copy input of17,116UTF-8bytes stored as439BLOBbytes survived Attention reading, requeue, claim, defer and reclaim without changing raw body, decoded body, original hash/bytes/time or compact flag. No provider, Agent run or receipt was created. Schema/inverse/Store version remain unchanged. This follow-up has independent GO and was deployed normally984168d6→3cdc72ec; PID48118. Store11.1/Email49, reference checks, fourAPIs and all eight original business/receipt fingerprints remain correct; no new failed IDs. Final file414822400B/WAL35782232B/freelist4570 at the second readback. Full Quality CI38138524827 remains pending.

## Final Attention index and deterministic fixture correction

The test-only01b4 full Quality run finished with14failures/11609passes: the12identified baseline failures plus the two remaining Attention plan/calendar fixture failures. Attention failed-ID filtering now reads only the compact parent status index before canonical PK hydration. Query-plan assertions require the covering status index and both PK reads, rejecting payload scans; captured Workitem EXPLAIN registers the same local decoder as the executed query. The deterministic calendar fixture now claims the task exactly as dispatchers do.37focused query/Attention cases and2deterministic end-to-end cases pass. No schema, migration, queue or business policy changes.

Final normal runtime deployment completed3cdc72ec→5d25497e, PID61974. Production Store2026-10-11.1/Email49,7261tasks/7788inputs; invalid current/claimed references and retired copied bodies are0. Four APIs200, healthok, History20; all eight original business/receipt fingerprints match and no new failed IDs. The original single Meeting FK orphan remains unchanged; quick_checkok. Main file414822400B, WAL523272B, freelist4552 at this readback. Independent final query/fixture review GO with11cases passing, root11cases and13documentation contracts pass. Required full Quality38140069414 remains pending.

Final target table pages (excluding indexes) are reply_tasks11153408B, reply_task_inputs11272192B and work_summary_inputs4521984B.7736retired reply inputs have0text/JSON bytes and4124164provenance bytes; all9080terminal WSI payloads total18160B. These are measured retained-storage figures, not deleted identities.

The3cdc full Quality run38138524827 completed with3failures,11624passes,97skips and44deselections in1927.79seconds. All3remaining failures are the Attention index plan, independent Workitem EXPLAIN decoder registration and deterministic calendar claim fixture corrected in5d25497e; no additional failed case appeared. Final5d254 full Quality38140069414 remains in progress.

## Final acceptance and cleanup

The final runtime commit is `5d25497e0079eb2f726fe6810c8d3564a1639537`, deployed through the normal quiet flow; PID **61974**. [Quality CI38140069414](https://github.com/callzhang/ceo-agent-service/actions/runs/38140069414) completed successfully: **11,627 Python tests passed**,97 skipped/44 deselected, **722 frontend tests passed**,2 skipped, **2 browser tests passed**, and the production frontend build succeeded. Python duration1971.94seconds. This supersedes the earlier pending CI observations.

Final live readback: Store `2026-10-11.1`, Email49; **7,262 tasks /7,789 inputs**, no invalid current/claimed reference or retired copied body, quick_checkok, no new failed IDs; Healthz/Status/Attention/History all200, History20. The original Meeting FK orphan is unchanged. Main file **414,822,400bytes**, WAL **4,140,632bytes**, freelist4523 at this readback; no production VACUUM.

During the CI wait, one new live task completed. Its current/claimed pointers are valid, task status is `done`, persisted action status `verified`, and reply send status `completed`. Exact primary-key row comparison against the verified pre-migration backup confirms **every original row in all eight checked business/receipt tables remains unchanged**. New rows are recorded separately: one each in external_action_results, review_candidates, candidate_reviews, candidate_action_attempts, sent_replies and reply_attempts; none in agent_execution_receipts or work_summary_inputs. This is persisted service evidence, not an independent provider delivery readback.

Exactly one previously verified complete pre-migration backup remains, with quick_checkok and Store2026-10-10.2/Email49/7,261tasks reconfirmed. Disposable copy databases, copied old code, logs and the task-specific SDD ledger are removed after saving this concise evidence; committed tests/evidence and the managed worktree remain. No native trajectory copy is retained.
