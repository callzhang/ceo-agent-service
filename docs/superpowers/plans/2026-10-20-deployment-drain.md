# Deployment Drain Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement inline.

**Goal:** Pause new execution claims, drain active work and resume on every exit.
**Architecture:** Stable database-adjacent flock; shared locks span source claim
transactions, exclusive deploy lock spans drain/update/health. Intake stays live.
**Tech Stack:** Python, POSIX flock, SQLite, pytest.

- [ ] Write tests/test_deployment_drain.py with real lock exclusion, claim commit
  race ordering, cancellation/process exit unlock, preserved pending input and
  the actual dispatcher/email/Workbench/meeting claim APIs. Run red first.
- [ ] Implement app/deployment_drain.py: deployment_drain(path) exclusive context;
  claim_connection(store, immediate=False) shared nonblocking context yielding
  a transactional connection or None. Lock scope ends after connection commit.
- [ ] Wrap only initial claim transactions in dispatcher adapters, email task/
  direct actions, Workbench and meeting delivery/memory functions. Existing
  claim failure return contracts stay None/empty list. Renewal/results/intake
  remain available while drained. Verify focused regressions red then green.
- [ ] Wrap RepositoryUpdater.execute (inside repo mutex) and restart_only around
  the exclusive drain context. Existing wait/stop/backup and rollback sequence
  stays intact. Verify pause precedes quiet, stays through health and releases
  for timeout/cancel/error, including queued rows being retained.
- [ ] Cover independently claimed email/Workbench/meeting work in in_flight_work
  before runtime rows exist; preserve current ownership/state contracts.
- [ ] Run tests/test_deployment_drain.py and relevant selected deploy/updater,
  dispatcher, email, Workbench and meeting tests, scoped Ruff and diff checks.
- [ ] Update architecture/runtime documentation in the feature commit. Review
  the final patch, push without rewriting concurrent work, deploy normally and
  read back version/PID/health/queues/Attention/History and admission resumed.
