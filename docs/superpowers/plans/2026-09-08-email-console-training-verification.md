# Email Console implementation verification

Status: Email implementation deployed and live-verified; task cleanup is blocked by execution policy.

## Scope

Implements the approved Email density and model-training design. No automatic
model promotion, mailbox mutation, reply, or unsubscribe is performed by the
verification workflow. Browser interactions use an in-memory fixture.

## Implementation notes

- Frontend modules live under `frontend/src/pages/email/`, beside their page
  owner, rather than a second global component hierarchy.
- Running mode and its transition history are committed in one atomic Registry
  manifest replacement. Configuration and category-description versions live in
  the existing Email SQLite store.
- Missing historical metrics remain unmeasured. Classifier-head timings cannot
  stand in for resident end-to-end timings.
- Read-only Registry inventory preserves readable versions when a separate
  artifact is corrupt; promotion still requires the complete integrity checks.

## Browser checks performed

Local fixture: `http://127.0.0.1:5181/workbench-assets/email-fixture.html`.
Fixture messages and models are synthetic; every API operation is in memory.

- Desktop list: 44-pixel single rows; 50 messages on page one, five on page two.
- Pending review: complete text and quoted text visible in the drawer;
  attachments shown as metadata only.
- Confirmation: count changed from 55 to 54 and selection advanced.
- Ready candidate: explicit confirmation changed the fixture to model-primary,
  checked the toggle, and removed the ready red dot.
- 390-pixel viewport: model overview wraps. Rechecked the footer correction: all
  nine choices, including fixed Trash, are visible and wrap inside the drawer.
- Category editor displays core definition, inclusion examples, exclusions,
  description/config versions, and provider-folder bindings.

## Deployment and live verification

- Fast-forwarded main to `8a718503`, built the actual `app/static/workbench`
  assets, and restarted launchd. Supervisor changed from PID 94468 to 18899;
  API 18902 and independent Email worker 18903 started successfully.
- Verified a fresh schema-33 SQLite backup before deployment. Live schema is 34,
  `quick_check=ok`, and the migration preserved all 350 classification rows.
- A parallel task independently resolved a pre-existing weekly-job startup
  status mismatch in `d659c559`. That commit is not an Email policy change.
  Subsequent stable PIDs: supervisor 23543, service 23556, API 23557, Email 23558.
  The follow-up exact weekly regression plus CLI suite passed: 226 tests.
- Read-only `/healthz`, Email learning, category config, list and detail checks
  succeeded. Runtime is `agent_primary`, no current or candidate online model,
  no Registry integrity issues, and the primary-model toggle is disabled.
- Real browser: pending-feedback total 123, 50 rows per page, measured row height
  44px, page two works, full saved body is visible, and attachments remain metadata.
  Category configuration exposes core/include/exclude and folder bindings.
- Real data exposed a legacy-label UI defect: an old TF-IDF registry `active`
  record was incorrectly labelled as current primary. `d304131c` fixes only the
  historical row/detail labels and adds a red-green regression. Independent review
  passed; final frontend suite: 333 passed, two skipped, build passed. Main
  integration `423b779d` was rebuilt and real browser readback now says historical
  version for both TF-IDF records, with the primary toggle still disabled.
- Post-merge Email controls/worker regression: 184 passed. Existing backlog was
  preserved: 42 failed email actions and three failed classifier tasks, unchanged
  after deployment; no new Email failures in the deployment window. Final reply
  queue has 5,113 done and no failed/running/processing items. Pending Email work
  continues through the existing worker; no manual replay was performed.

## Remaining cleanup

- The verified latest backup is `pre-deploy-final-schema33.sqlite3` inside the
  private `email-console-deploy-20260908-8Ep6WG` deployment directory. The earlier
  backup and migration-rehearsal copy remain because their exact-target deletion
  was rejected by the current execution policy (approval unavailable).
- The temporary fixture browser tab is closed. Stopping the task-owned Vite
  preview PID 11740 was also rejected, so the clean, fully merged Email worktree
  and branch remain rather than removing an in-use directory. No policy bypass
  or unrelated-file deletion was attempted. The live Email tab is retained.

## Final pre-deployment evidence

- Frozen code `b3cb22d7`, including main `5024bbe6`: **7,165 passed, 86 skipped,
  zero failed**, four existing deprecation warnings, 362.72 seconds.
- Frontend: **332 passed, two skipped**, TypeScript and production Vite build passed.
- Independent persistence, runtime/API, training and frontend reviews closed all
  reported P1/P2 findings. Input/cache equivalence and browser fixture checks passed.
- No production model promotion, new training run, SMTP action or mailbox mutation
  was performed by this verification workflow.

## Intermediate test evidence

- Persistence at `f202763e`: main-agent rerun, 373 passed in 77.37 seconds;
  independent reviewer also verified 373 passing tests and closed prior findings.
- Frontend at `cfe6661a`: main-agent rerun, 36 focused tests passed; independent
  re-review closed stale-request, keyboard disclosure, and failed-switch readback
  findings. The implementer also completed TypeScript and production build checks.
- Frozen runtime/API/training slice: main-agent combined run, 580 passed in
  31.24 seconds with four file-scoped test workers; three existing deprecation
  warnings. Scoped Ruff and staged whitespace checks passed. This is a test-suite
  execution setting, not a measurement of model performance.
- Independent training review verified exact input/cache identity, metadata-only
  attachments, no folder-label leakage, separate remote/cache measurements, and
  healthy-row preservation. It did not call the live GPU or mailbox.
- Final runtime/API review closed active-model corrupt-inventory handling and
  required API-level category CAS. Store-level callers may omit CAS; HTTP edits
  must carry a nonblank current version. All named P1/P2 review findings are closed.
- Production-database rehearsal: verified schema-33 backup, upgraded only its
  second copy to schema 34, and checked integrity plus preserved counts of 325
  classifications, 325 message rows, and 5,107 tasks. Actual production migration
  remains a separate deployment step.
- First full Python run overlapped active TDD edits: 6,984 passed, 24 failed,
  86 skipped, 44 deselected in 1,108.80 seconds. Twenty-three failures exercised
  the earlier in-memory persistence implementation before the fixes above. The
  remaining managed-thread test used a one-second event timeout and requires
  deterministic synchronization review. This run is not final release evidence.
- Read-only live body availability: all three sampled pending-feedback details
  returned text (323, 3,662, and 15,143 characters). One of three processed samples
  had neither stored body nor preview; the UI must not fabricate missing content.
- Final integration regression: canonical embedding input was incorrectly sent
  to the legacy redacted classification column. The worker now derives that
  column with the existing redacted serializer while saving readable body in the
  message column. Prediction, snapshot input and embedding cache keys are unchanged.
  The original failing production-closure test now checks persisted body, redacted
  legacy text and absence of an Agent fallback task. Related tests: 176 passed;
  independent review: 25 passed, no P1/P2 findings.
- Merged frontend full suite: 332 passed, two skipped; TypeScript and Vite build
  passed. Full Python integration run found three failures among 7,164 tests:
  the worker issue above and two test fixtures (missing category CAS version,
  ambient runtime route). All three focused regressions now pass; a fresh full
  run is in progress. Test-only fixture changes do not change WeChat behavior.
- The next full run completed with 7,158 passed, six failed and 86 skipped.
  Five failures shared a reproducible cross-test environment leak: the Console
  settings API test wrote runtime routes into `os.environ`, leaving subsequent
  tests with an API route but no key. A two-test sequence reproduced the failure.
  The test-client context now restores the environment at shutdown; the real
  settings writer and its file output are still exercised. A dedicated regression
  checks both restoration and preservation of the written test file.
- The sixth failure was cold subprocess startup exceeding a test-only three-second
  wait. The test now waits for readiness separately from the unchanged four-second
  watchdog exit requirement and cleans up the parent on early failure. The seven
  targeted reproductions passed, followed by all 150 tests in the affected files.
  No production runtime, authorization or unsubscribe behavior changed.
- Integrated main `5024bbe6` (separate Connector and worker channel inventories),
  and reverted the earlier test-only three-channel expectation adjustment.
- Independent source review identified the same environment leak in direct
  legacy settings-handler tests in `test_audit_web.py`. The producer followed by
  runtime-refresh test reproduced it (one passed, one failed); a module-local,
  function-scoped fixture now restores each test's process environment without forcing any
  runtime default. Both source/victim checks and the Console restoration check
  pass (three tests). The just-started full run was stopped at 462 passing tests
  so this confirmed second source could be fixed before freezing another run.

Feedback `ok=true` acknowledges the persisted classification feedback, not the
completion of asynchronously executed mailbox actions. The page may advance to
the next classification after that acknowledgement; actual folder/flag/action
outcomes remain separately visible in the existing observability/detail evidence.
No synchronous action executor or new authorization rule is introduced here.
