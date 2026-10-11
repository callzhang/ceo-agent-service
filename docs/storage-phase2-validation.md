# Phase 2 canonical email action-plan validation

Approved scope: remove email_classifications.action_plan_json and read the existing immutable email_action_plans through current_action_plan_id. Preserve adopted classifications, plan revisions, authorization semantics, execution state and receipts. No new retention or recovery policy.

## Focused verification

650 distinct focused cases passed:369 store/reference/index tests;57 selected training-storage/task-adapter/reply-delivery/worker migration cases;181 Web API tests;43 release/publication/deploy-maintenance cases. Ruff and whitespace checks passed. No full repository suite was run.

New regression evidence includes transactional48→49 removal, corrupt old snapshot/pointer rejection, unchanged classification API and business rows, one-query list hydration, feedback replay and correction, inverse preserving a post-migration classification and v2, byte-identical full/stripped legacy authorization snapshots and empty markers, and atomic failure while deleting schema metadata.

Independent high-risk review also rehearsed a post-migration v2, completed direct Email action, persisted attempt/provider receipt, exact inverse preservation and actual old48 runtime startup. No unresolved code blocker was found. These independent tests overlap some listed focused cases and are not added to650.

## Consistent production-copy rehearsal

Source runtime21bfe750/schema48. 149 tables, including sqlite_sequence, fingerprinted using named columns/ordered data. Only the removed derived cache column and schema migration metadata are excluded from business comparison.

* 48→49: all business data identical;8,482 classification APIs have identical digests.
* Add a new classification, append its v2 current plan and insert a real external_action_results receipt on the copy; inverse49→48 preserves every business field and restores every original cache byte form exactly.
* Actual21bfe750/schema48 EmailStore source opens the inverse copy and performs full durable validation. Its API result digest equals the49 result, including post-migration writes.
* Repeat48→49: all business fingerprints still match. quick_check remains ok. One pre-existing meeting_alignment_runs→meeting_alignment_jobs FK violation is identical throughout; no new violation and no unrelated repair.

Removed duplicate JSON payload:12,566,693 bytes. Forward migration frees internal pages:freelist3,095→4,968 on this copy. Copy-only VACUUM yields387,063,808 bytes from414,822,400 bytes; the27,758,592-byte difference includes already-free pages and is not solely this phase's payload reduction. Production VACUUM is excluded.

Rollback may append the reconstructed column at a different physical column position; old48 reads/writes use named columns, and actual old48 startup/API checks verify compatibility. No old database snapshot is restored over new business writes.

Temporary copies contain working validation data only and are removed after acceptance; committed evidence contains counts and hashes, no original inputs or duplicate trajectory.

## Live deployment

Normal updated `python -m app.deploy` completed:21bfe750→f7ada916. No quiet bypass was used. An initial tool execution was interrupted before checkout; retry waited for a normal quiet window and finished successfully. Actual installed commit is verified by production HEAD and deploy completion, including the concurrent History fixes that landed on main during this work.

PID75090→58988; Email schema49; action_plan_json column absent;8,487 classifications and zero missing current-plan references. quick_check ok and the one pre-existing FK orphan unchanged. healthz, Status, Attention, History and Email classifications all HTTP200. No new failed IDs in reply_tasks, email_agent_classification_tasks, email_actions or agent_runtime_attempts compared with pre-deploy evidence.

Live SQLite file414,822,400 bytes; freelist3,094 before→4,851 at readback; WAL35,782,232 bytes. The file has not been compacted; freed internal pages are available for reuse. These live observations include normal intervening writes; the consistent-copy figures isolate schema migration behavior. Latest retained backup:exactly one SQLite backup, schema49, verified-complete marker and quick_check ok.

The13 tests covering the concurrent Task Skill merge passed separately; they are additional to this phase's650 cases. History changes have their own committed validation report. Acceptance publication is documentation-only; production runtime remainsf7ada916.
