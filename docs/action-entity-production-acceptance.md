# Action Entity Phase 1 production acceptance

**Status:** complete and live. Production runtime code is `87a34d299d5749af1af454bfdd01c48a936799a4` (`87a34d299`). The docs-only closeout commit is separate and does not represent a new runtime release.

## What shipped

The first external-Action unit (Tasks 1–7) is deployed through the standard release path. The production Store marker is `2026-10-21.1`, Email schema ledger 53, and the current schema has 14 ordinary tables. Production readback counted 15,472 common Actions, 18,621 Action Receipts and 506 Action Observers. Each of the seven populated source kinds retired by this unit has zero rows. This closes the defined Phase 1 boundary; specialized unsubscribe continuation, other Action kinds and the broader Task/Run/Email/business-record program remain open. No full Actions retirement or whole-database unification is claimed.

## Production readback

The deployed source moved from production HEAD `036b14624cddbcff04098ebec7697644f95370c0`, through `bf07de8d6210709cd76887cc14ebb9ef23fa5600`, to `87a34d299d5749af1af454bfdd01c48a936799a4`. Production PID is 2903. Health, status, queues, Attention and History each returned HTTP 200. The original and only FK finding remains `runs(190042) -> tasks(1)`.

At pre-deploy readback there was one active process and two active queue items. After deploy, process and active queue counts were zero. Queued count changed 146→155 through natural arrivals during the interval; this does not mean all queued work completed. Failed count remained 36 and Attention remained 19. History read 20 rows. The sent-detail readback returned Attempt 15914, Action 105320 and receipt 5046, with parsed delivery status 200.

At deployment acceptance, the retained verified backup was `/Users/derek/Library/Application Support/ceo-agent-service/backups/auto-reply-before-upgrade-deploy-9351777a-c7be-4845-889a-5759fd342790.sqlite3`, 501,170,176 bytes, with `backup_is_complete=True`. Production was not VACUUMed. Its main database file was 501,170,176 bytes and freelist was 1,037 pages (about 4.05 MiB); WAL size is variable. A separate same-cohort source-derived copy measured 494,678,016→492,015,616 bytes after VACUUM, a decrease of 2,662,400 bytes (about 2.54 MiB); table pages rose 1,548 and index pages fell 2,187. The copy result is not a production-size or performance claim.

## Verification boundaries

Before publication, the fresh production-derived copy passed forward migration, current-fact inverse, archived production Store cold read/write and re-forward. It matched 520,789 stored rows, 103,674 typed Action identities, 506 Sent references, exact schema/sequences and the same inherited FK finding, with zero out-of-scope Action-ID remaps. Final integrated code also completed one native Consumer/Audit approval on the production-derived copy, using a strict in-memory provider substitute: one send, verification, receipt and Sent fact; no real provider call. Six targeted backup/inverse tests passed (`6 passed in 13.01s`).

The repository updater quiet-source repair changed the existing in-flight-work reader to count current common-Action processing rows through the existing publication selector while preserving schema selection and Store-only behavior. Its 19 focused quiet-source tests passed and the quiet-source artifact received independent SPEC and QUALITY approval. This preserves the existing deployment quiet policy; it does not add a new audit, authorization or safety rule.

Post-deploy evidence was generated in `/tmp/action-entity-task7.BduYnC/postdeploy-final-readback.json`, `/tmp/action-entity-task7.BduYnC/predeploy-readback.json`, and `/tmp/action-entity-task7.BduYnC/postdeploy-sent-detail-readback.json`. Those are private operational artifacts; this record includes only required counts and identifiers, not message bodies or secret settings. The detailed pre-deploy candidate proof and limits are in the [Task 7 integration report](../.superpowers/sdd/2026-10-21-action-entity-unification/task-7-integration-report.md), with the [quiet-source repair report](../.superpowers/sdd/2026-10-21-action-entity-unification/task-7-quiet-source-report.md), [final whole-branch review](../.superpowers/sdd/2026-10-21-action-entity-unification/final-whole-branch-review.md), [all execution rulings](archive/records/action-entity-execution-rulings.md), and [validation history](archive/records/action-entity-unification-validation.md).


## 2026-10-22 temporary-artifact cleanup status

Derek explicitly authorized deletion of the following seven completed test directories while retaining the production database, newest verified backup and native Codex trajectories. Immediately before the attempt, the roots were confirmed as real task-owned directories (not root symlinks); `lsof` found no open handles. They contained 22,921,090,840 logical file bytes in total (22.92 GB / 21.35 GiB); this is not a measurement of recoverable APFS disk blocks.

- `/private/tmp/ceo-action-entity-validation.8rUAvK`
- `/private/tmp/action-entity-capacity-proof.jlai9a45`
- `/private/tmp/action-entity-task6-corrected.IH2XQu`
- `/private/tmp/task6a2.fjw5dC`
- `/private/tmp/action-entity-task6a3.VOOfhl`
- `/private/tmp/action-entity-task6b.vESxM3`
- `/private/tmp/action-entity-task7.BduYnC`

**Cleanup is blocked, not complete.** After explicit authorization, the exact `rm -rf --` invocation was still rejected before process creation: `approval required by policy, but AskForApproval is set to Never`. No alternative deletion mechanism was used. A subsequent readback confirmed all seven directories still exist; no reclaimed-space claim is made. User authorization is already present; the remaining blocker is execution-policy approval, not another user decision.

The production database remains at `/Users/derek/Library/Application Support/ceo-agent-service/auto-reply.sqlite3`, and native trajectories remain under `/Users/derek/.codex/sessions`. Neither is a cleanup target. The routine backup workflow has since rotated the deployment-time backup to `/Users/derek/Library/Application Support/ceo-agent-service/backups/auto-reply-2026-10-22.sqlite3`; the older deployment-time filename above is historical evidence, not the current retained filename. The current backup exists, is 501,170,176 bytes, and its workflow completion marker was read-only verified as `0x43454F42`, matching `backup_is_complete`; SHA-256 is `22073682e94fd2572d1e8dad6a4baed61ecef856f2a741311c808cdbd56a8a1e`. The cleanup attempt did not modify the backup. No production migration, VACUUM, service restart or provider send was performed during this documentation/cleanup follow-up.
