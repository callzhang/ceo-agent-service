# Task2D4 weekly history and maintenance

## Scope and evidence boundary

At BASE `6b0ed0d081b8609ea40f8aeacc46afc19a18fb24`, repository-wide `app/` and managed weekly Skill searches found no production caller for `begin_weekly_okr_analysis_job`, `finish_weekly_okr_analysis_job`, or `reclaim_weekly_okr_analysis_job_cache_miss`; their only callers were tests. The two startup calls to `complete_superseded_stale_weekly_okr_analysis_jobs` and `complete_superseded_failed_weekly_okr_analysis_jobs` were real and remain. The configured production database was read-only at `2026-10-23T13:46:56.608798Z`: 183 weekly Tasks all completed, 353 weekly RuntimeAttempts all terminal, zero active weekly Tasks/attempts. This supports retiring the unused writer chain; it does not authorize dropping historical facts or creating a new producer.

## Change

Removed from `app/store.py`:

- `WeeklyOkrAnalysisJobClaimOutcome` and `WeeklyOkrAnalysisJobClaim`.
- `AutoReplyStore.begin_weekly_okr_analysis_job`.
- `AutoReplyStore.finish_weekly_okr_analysis_job`.
- `AutoReplyStore.reclaim_weekly_okr_analysis_job_cache_miss`.
- The runtime-only `WEEKLY_JOB_SQL` binding.

The retained weekly operation-parent lookup now resolves an `okr_analysis` Task from its typed parameters and source-preserved lifecycle state (`running`); its five-part form checks the canonical Task ID and Task lease owner. The unchanged converter oracle confirms the boundary: a source weekly row has physical `status='running'` but virtual `lifecycle_status='processing'`, and migration writes native `Task.status='running'`. Weekly native Task status values therefore remain `running`, `completed`, and `failed`; the generated lifecycle projection is not the native owner value. The workload grammar and independent RuntimeAttempt operation lease remain supported.

The actual stale-runtime recovery and both startup supersession cleanups now update native Task lifecycle fields and store the source completion clock in `task_results(task_id, result_kind='okr_analysis', finished_at)`. They compare source-preserved `running`/`completed` Task status strings, as the frozen source DAO does. Stale recovery requires the expired Task lease and the matching failed RuntimeAttempt with `failure_code='runtime_lease_expired'`. Supersession requires a later completed week for the same manager; failed rows require one of the three source technical errors and a later successful completion by week or `created_at`. The methods preserve TaskResults' existing result payload and write no result body. CLI startup continues to call both real cleanup methods. Generic Task kind, Task/Result timestamps, operation history, source factories, and inverse oracles remain available.

Removed the dead writer behavior from the current caller exercise and legacy lifecycle tests. Added native Task/TaskResult maintenance tests using `create_task` typed JSONB parameters and source status strings for expired versus live leases, matching versus nonmatching failed attempts, no-later/equal-week cases, technical versus business failure, `created_at` ordering, finished-at ownership, 3/5-part parent identity and owner checks, and absence of the retired APIs. No dashboard hybrid was added: the shared all-family queue still constructs a Task20 source projection and its detail path still uses old public source identities. Production has no weekly queue entries; converting that common queue/detail reader is a separate all-family dependency.

## Verification

Passed:

- RED before repair: `pytest -q tests/test_okr_weekly_native_maintenance.py::test_native_weekly_runtime_recovery_requires_expired_task_and_matching_failed_attempt` — failed as expected (`0 == 1`) because the native query only matched `processing`, while the frozen forward facts preserve `running`. An in-memory unchanged-converter proof independently observed source `(status='running', lifecycle_status='processing')` and migrated target `Task.status='running'`.
- `pytest -q tests/test_okr_weekly_native_maintenance.py` — 5 passed before the final focused combined run.
- `pytest -q tests/test_okr_weekly_native_maintenance.py tests/test_cli.py::test_service_start_closes_superseded_failed_weekly_okr_jobs tests/test_store.py::test_weekly_okr_report_run_lease_is_single_owner_and_recoverable` — 7 passed.
- `ruff check app/store.py app/web_api/dashboard.py app/cli.py tests/test_okr_weekly_native_maintenance.py tests/test_okr_weekly_storage_readers.py tests/task_lifecycle_caller_fixture.py` — passed.
- `python -m compileall -q app/store.py app/web_api/dashboard.py app/cli.py` — passed.
- `git diff --check` — passed.
- `rg -n 'begin_weekly_okr_analysis_job|finish_weekly_okr_analysis_job|reclaim_weekly_okr_analysis_job_cache_miss|WeeklyOkrAnalysisJobClaim' app` — no matches.

A wider attempted selection also ran adjacent current OKR-review tests and the Task lifecycle release case. The OKR-review tests failed because this isolated Task2D3 candidate calls native `kind` against the legacy Store constructor; the lifecycle release case stopped with `unsupported current Action source schema; explicit paired release required`. Neither path is changed by Task2D4. The weekly-native tests and actual startup cleanup call test passed as listed above.

## Remaining dependencies and state

The shared dashboard queue/detail reader still depends on the Task20 source projection and old source identity. It was intentionally not converted partially. Constructor activation, full all-family reader conversion, full inverse/release proof, production work, push, and deployment remain outside this unit. Frozen source factories and inverse tools are unchanged. Two unrelated dirty reports present at entry were preserved.
