# Phase3 input storage validation

Status: local implementation and copy rehearsal; production release not yet performed.

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

Independent migration review found and required repair of body-only single-chat refresh semantics, an old quality-check column reader and a current-versus-claimed system action reader. All three confirmed review findings were repaired with failing-then-passing regressions. The explicit-generation WeChat finalizer also uses the claimed source, without changing its manual/current delivery API or queue policy. Independent final review recommends GO with no remaining P1/P2 blocker. It rechecked the confirmed fixes, matched inverse and copy evidence; production readback remains required.

Read-only unsubscribe count/latest comparisons on equal old/new copy rows preserved exact results. Median count1.26→4.14ms, latest1.15→4.07ms; current projection adds canonical primary-key reads. This actual corpus showed no release-blocking query cost.
