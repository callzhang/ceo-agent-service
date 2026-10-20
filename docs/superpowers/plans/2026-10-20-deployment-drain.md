# Deployment Drain Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement inline.

**Goal:** Pause new execution claims, drain active work and resume on every exit.
**Architecture:** Stable database-adjacent admission turnstile and drain flock; shared
locks span source claim transactions, exclusive deploy scope spans drain/update/
health. Intake stays live.
**Tech Stack:** Python, POSIX flock, SQLite, pytest.

- [x] Write tests/test_deployment_drain.py with real lock exclusion, claim commit
  race ordering, cancellation/process exit unlock, preserved pending input and
  the actual dispatcher/email/Workbench/meeting claim APIs. Run red first.
- [x] Implement app/deployment_drain.py: deployment_drain(path) exclusive context;
  claim_connection(store, immediate=False) shared nonblocking context yielding
  a transactional connection or None. Lock scope ends after connection commit.
- [x] Wrap only initial claim transactions in dispatcher adapters, email task/
  direct actions, Workbench and meeting delivery/memory functions. Existing
  claim failure return contracts stay None/empty list. Renewal/results/intake
  remain available while drained. Verify focused regressions red then green.
- [x] Wrap RepositoryUpdater.execute (inside repo mutex) and restart_only around
  the exclusive drain context. Existing wait/stop/backup and rollback sequence
  stays intact. Verify pause precedes quiet, stays through health and releases
  for timeout/cancel/error, including queued rows being retained.
- [x] Cover independently claimed email/Workbench/meeting work in in_flight_work
  before runtime rows exist; preserve current ownership/state contracts.
- [x] Run tests/test_deployment_drain.py and relevant selected deploy/updater,
  dispatcher, email, Workbench and meeting tests, scoped Ruff and diff checks.
- [ ] Update architecture/runtime documentation in the feature commit. Review
  the final patch, push without rewriting concurrent work, deploy normally and
  read back version/PID/health/queues/Attention/History and admission resumed.


Execution ledger: adopted the user's explicit running-only drain boundary.
Ruling: use a stable native flock instead of a persisted pause lease; kernel
release covers cancellation/crash without a TTL/renewal daemon or schema change.
Shared lock remains held until the original connection context commits.
RED: seven initial tests lacked deployment_drain; GREEN: basic real-lock, process
exit, renewal/intake, email and Workbench cases passed. RED: four updater/restart
phase tests proved claims were still admitted; GREEN after exclusive integration.
RED: classifier pre-runtime claim counted zero; added independent claim reads.
Current focused feature suite: 22 passed, including all dispatcher families,
rollback admission, cancellation and process-exit unlock. Broader targeted checks
are running. First installation still needs a normal quiet window on old code.

Final review: two Important findings reproduced red, then corrected: direct email
Store claims now participate in admission and count before runtime creation; a
short admission turnstile closes before waiting for old claim transactions.
Supported Store19.7 archived-source regression reproduced missing lifecycle
columns; in-flight counts now use the established source-shaped task projection
ABI. No task data repair or retry change. Broader checks: 116 passed and one
two-second dispatcher wake deadline failure under SQLite load; 68 claim/lease/
recovery cases passed. The timing case is being rerun in isolation.

Review-fix verification: 27 passed (26 feature tests plus the isolated existing
wake test); the timing test passed without changing its two-second assertion.
Direct reply Store claim/peek/delay regressions: 8 passed. Ruff, compile and diff
checks pass. The final deploy/dispatcher batch is running on the corrected tree.

Final writer-boundary self-check: email provider's separate preflight and writer
connections required admission on the second connection. Native exclusive-flock
probe inside the writer recheck reproduced RED before the correction. Corrected
feature/provider batch: 27 passed, 303 deselected. Existing empty read probe and
provider action eligibility/mutation are preserved.

Corrected final deploy/dispatcher regression batch: 117 passed in219.39s,
including the unchanged two-second wake case. No remaining test failure.
Independent review findings addressed in one RED/GREEN fix pass; no rereview.
