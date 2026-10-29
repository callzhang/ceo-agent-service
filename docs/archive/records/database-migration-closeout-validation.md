# Database migration closeout — 2026-10-21

This is the user-authorized follow-up to the completed Store19.11 / Email53 migration: reconcile total progress and the acceptance matrix, test migration-affected gaps on a fresh copy, repair verified-new-before-old backup replacement, then deploy through the normal lifecycle. No new Audit policy, business fields, source-body cache or fact-check tool is included. Tests do not send to real providers.

## Evidence and completion boundary

The initial production copy came from `cf501b3778768830b95eb99a99660b93428f7985`, already Store19.11 / Email53. SQLite readonly backup: 487,153,664 bytes, SHA256 `f15dcf77dcd0410910ed7e21ce3fdf9018f195000000397c8fb518543e5d1186`, integrity `ok`. The original FK multiset remains only `runs / 190042 / tasks / 1`. This task does not repair that historical orphan or all historical business failures.

No schema, migration converter or inverse changed in this closeout. The prior full-copy current-data inverse / actual old-code cold-open / reforward and real GPT-6 Luna + substitute-receipt acceptance remain the migration evidence; they are not claimed as rerun here. See [final migration publication](database-gpt6-release-validation.md).

## Acceptance scope and matrix reconciliation

The old matrix inventories 36 routes / 342 scenarios, with only six A1 browser observations recorded. The remaining generic loading/empty/error/back states were not observed failures. Later B1 Email and Store19.11 Project54 / Task736 browser evidence is now linked only to the actual observed scenarios. Frontend mock tests, real-copy HTTP, SQL digests and native-model evidence are separately identified. No mocked test or API response is called a browser click. All9,669 previews on the new copy were checked with Python Unicode length: maximum1,000, violations0.

The release gates are the `migration_checks` entries in [the matrix](business-model-ui-coverage.json): changed public identities/deep links, retained old business history and distinct formal tasks, 1,000-character preview without source fetch, explicit unavailable original without stored-body fallback, immutable read paths, task claims and canonical receipts, backup failure preservation and fresh deployment snapshot. Unaffected general UI product states remain `not_run` without becoming artificial DB migration gates.

## Focused validation so far

- Seven affected frontend files: 173 passed (Tasks, Task detail/project/attention, History, Email and Attempt detail). These are component tests with mocked HTTP.
- Five focused backend files: 66 passed (logical Run owner, Prepared Action owner, Task lifecycle storage, Email exact-source HTTP and history actions).
- An expanded 26-case API/recovery batch initially had 22 passes and four stale-fixture failures: two wrote retired physical tables, one seeded only a passive lease ledger, and one expected retained evidence text without an actual source reader. Fixture corrections use current writers/claim ownership and an exact synthetic source; no product storage or evidence assertion is relaxed. The full three corrected files passed 40 tests; the final additional assertion that the common Run index exists (and the retired index does not) passed its single-case rerun. Two extra stale meeting expectations were corrected: the retired index and the earlier malformed lifecycle-schema error.
- Fresh-copy API traversal uses `scripts/validate_database_closeout_copy.py`, never starts the application lifespan or workers, blocks network connects, and substitutes the Email source reader. It compares every SQL value/type digest before and after the GET traversal. All 15 checks passed: 40 distinct entries across two History pages, Meeting public2539 versus physical190283, Project54 with eight Todos and20 Updates, formal Task744, attempt15814, preview-only Email list without source fetch and explicit502 for unavailable original. All persisted SQL-value/type digests after startup were unchanged. Results are in [JSON evidence](validation/2026-10-21-database-closeout.json).

## Backup repair and independent review

The old implementation removed earlier backups before copying, wrote directly to the final destination, and reused any recent complete deployment backup. The replacement stages one copy under the existing directory lock, validates SQLite integrity and the completion marker, closes it, atomically publishes it, then retires older verified files. Deployment obtains the stopped current database under its operation identity instead of reusing stale daily snapshots. Daily once-per-day behavior remains.

Five regressions failed against the original implementation. Initial repaired backup/updater suite: 66 passed; maintenance suite: 14 passed. A real 487 MB copy also passed verification-before-old-deletion and injected completion-marker failure preserving the prior target SHA. These results do not close the independent review findings below until their fixes are rechecked.

Independent SPEC review caught two additional interleavings: the restarted daily loop could retire a pre-deploy recovery source before `verify_loaded`/rollback completed, and a crashed staging file could fill the disk before success-only cleanup. Both are now resolved: the updater holds the existing folder flock from snapshot completion (while the service is stopped) through loaded verification/finalization or rollback; the restarted daily loop waits. Reserved `.ceo-backup-stage-` files are cleaned under that lock before allocation, even if the new copy then fails for lack of space; unrelated hidden temporary files survive. Final affected backup/updater/maintenance suite: 83 passed in37.00s. Independent final SPEC passed; QUALITY approved. The final487,153,664-byte copy rerun passed both verify-before-prune and failed same-name replacement preserving the previous SHA; only one backup remains after success. Code commit: `740745e5b`.

## Publication

Standard deployment completed at 2026-10-21 00:44 Pacific (07:44:49 UTC), operation `deploy-57b5ce76-7cc2-4609-8126-c342db99a457`, target/installed `15939b46f6a43e0a0e23038dbced69c50e6988d3`. It waited for existing work, including a provider Email action, to finish naturally; no skip-quiet or manual stop/kill was used. Source was current production `0e448359e`, supervisor68652. The merged source preserved that already-deployed Dashboard change; its15backend and9frontend checks passed. The runtime patch remained byte-identical to the reviewed backup code.

Production readback: new supervisor88119, clean checkout, Store19.11 / Email53, eleven ordinary tables, integrity ok, original FK finding unchanged. Health, Status, Attention, History, Dashboard, upgrade status and selected runtime-settings reads returned200. Effective primary model remains GPT-6 Luna/high. Failed count17 equals the pre-deploy observation; initial post-deploy pending13/processing1 shows normal work resumed, not a requirement for permanent zero processing.

The persisted operation receipt names a fresh `auto-reply-before-upgrade-deploy-57b5ce76-7cc2-4609-8126-c342db99a457.sqlite3`; it did not reuse the earlier daily snapshot. After successful deployment and lock release, the daily thread produced the newer `auto-reply-2026-10-21.sqlite3`, verified complete/integrity/schema19.11/53,487,153,664bytes, SHA256 `0ff1b9c55c651898378842b6b4a769977258aae9d1f023eefc22cc96e9e8c99f`. Exactly one complete backup and zero staging leftovers were present. Retiring the deployment snapshot after deployment settled is the intended latest-only policy; its receipt is historical and does not promise that old file remains available.

All twelve scoped migration checks in the matrix passed. Production and normal authorized worker activity are distinct from isolated zero-send tests. No schema/migration/inverse code changed and no VACUUM was run. Historical orphan/failed work and unobserved generic browser states are not relabeled as repaired or observed.
