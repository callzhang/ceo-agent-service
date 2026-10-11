# Phase 1 storage and schema rollback validation

User authorized phase1 implementation, consistent-copy validation and proven schema rollback before deployment. This report separates offline evidence from live deployment.

## Offline verification

* Candidate targeted suite: **206 passed in 22.07s** across provider index, classification locator, startup validation, folder truth/provider actions, schema release, repository updater/publication, deploy maintenance and CLI parser.
* Before implementation the corrected index regression suite had 3 failed/2 passed; after implementation 5 passed. Plan fixture corrected to explicit PROCESSED status; no runtime validator weakened.
* Independent astra review found no unresolved blocking code finding in exact47→48/inverse path. Its independent release/publication runs passed25 distinct tests; these overlap the206 total and are not added to it.

## Consistent production-copy rehearsal

Source commit `3249d40e74039c0b031ef10724fc7b7b354b0636`. Migrator is the updated isolated worktree, not the old running updater. **149 tables** fingerprinted by ordered fields/rows; sqlite_sequence counters also compared. Provider inventory: **72,004 rows**.

1. 47→48: all application data fingerprints unchanged; only email_schema_migrations metadata changed. Primary-key point lookup and folder-index lookup unchanged.
2. Add a test-only provider receipt on the copy after migration, inverse48→47: every business row including that receipt preserved; original index SQL/version history restored.
3. Load actual old schema47 email_store.py source from the source commit in a task-temporary package facade: EmailStore opens and fully validates inverse copy successfully. No new mutation beyond original metadata; business fingerprints remain exact.
4. Remove only test-only copy receipt, repeat forward migration and compare every application table again. A new migration applied_at timestamp is expected; historical rows remain accurate.
5. quick_check and integrity_check return ok. foreign_key_check reveals **one pre-existing meeting_alignment_runs→meeting_alignment_jobs violation**, with identical full violation fingerprint before/upgraded/inverse; no new violation. This report does not claim a globally FK-clean starting database or repair that legacy row.

Removed index pages: **11,579,392 bytes** (11.58 MB). Copy logical size before: **414,822,400 bytes**; after copy-only VACUUM: **399,876,096 bytes**. Difference includes pre-existing free pages, so not all attributed to this change. Live file reclamation is not implied by copy results.

## Rollback scenarios

Covered dependency failure before migration; import verification failure after migration; partial bootstrap; failed health; loaded-schema mismatch; partial optional asset publication (asset inverse→schema inverse→old start); inverse transaction failure; unexpected49 rejection; old required-index behavior; relative paths; module/CLI callback wiring; native label absent versus permission failure.

No database snapshot overwrite after replacement writes. Inverse failure keeps target checkout stopped with needs_manual. Alternate entrypoints are wired for future updated callers; first rollout uses updated app.deploy explicitly. Production→target diff must exclude unrelated startup schema changes.

## Live deployment

Pending: push reviewed commits, normal quiet deploy from updated worktree, then read PID, healthz, queues, Attention, History, schema48/index absence/remaining indexes and actual file/WAL/freelist sizes. No production VACUUM in default deploy.
