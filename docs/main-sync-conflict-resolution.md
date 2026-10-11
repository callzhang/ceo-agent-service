# Main synchronization conflict resolution

Date: 2026-10-10

Local main incorporated origin/main in merge commit `0b9860e5`. Applying the
previous uncommitted work produced twelve conflicts. The original changes
remain recoverable in stash `8d5f03d1c583a97f0e943fe164b600e254a5d49e`.

## External dependency retry

The restored code changed `app/agent_orchestrator.py`, `app/consumer_agent.py`,
`app/worker.py` and their three test files. The current main implementation
already supports `dependency_read_unavailable` and
`agent_context_refresh_failed` as external dependency waits, including
DingTalk attempt refunds and exclusion from the six-turn technical failure
limit. Its prompt distinguishes an actual dependency outage from a successful
empty read or incomplete business evidence.

Resolution preserves the current implementation and tests. The obsolete
orchestrator methods from the stash are not reintroduced. The current
architecture, runtime mechanism and error catalog already document this
behavior. Fifteen focused dependency/retry checks passed, covering both
Consumer and Audit and the worker's exhausted-attempt path.

Shared claim conflicts retain the current board and restore the missing
patch-handoff, deployment-backup, entitlement-leave and runtime-draft entries.
The original storage and Workbench entries are already present in main.

## Native prompt tests

`tests/test_codex_runner.py` keeps the current new-session and resume tests:
they parse the native configuration JSON and check the supplied explicit role
instructions. Assertions about an older globally injected Pydantic contract
or escaped newlines are not restored.

The Consumer prompt unit test now explicitly selects the repository's default
Developer and User templates. Before this correction it failed by loading an
obsolete local User template, before reaching the assertions under test.
The five focused Consumer prompt and native new/resume command checks passed
without changing local or production prompt configuration.

## Deployment backups

The previous uncommitted proposal refused deployment when the scheduled backup
was missing. Derek corrected that behavior: prefer a recent verified backup;
when none qualifies, create and verify a fresh one and continue deployment.

`RepositoryUpdater._backup` now reuses the newest complete backup for its
database within 24 hours, including earlier deployment backups. Backup creation
is inside the existing upgrade rollback block, so a creation failure does not
leave the original service stopped or advance the checkout. The deployment
description, architecture and runtime mechanism describe the same behavior.

The pre-change regression run reproduced unnecessary copies and an uncaught
backup creation failure. The updater tests cover reuse without changing the
existing snapshot, missing/stale/incomplete/future snapshots, candidate
selection for the correct database, and restart of the original service on
creation failure. All tests use temporary local Git repositories and SQLite
databases; no production deployment is performed.

Verification: `tests/test_repository_updater.py` and
`tests/test_database_backup.py` passed together (50 tests). Ruff passed for the
changed Python files, and the new documentation/code diff has no whitespace
errors. Unrelated Task Agent tests, leave-rule Skill changes and local draft
files remain uncommitted.
