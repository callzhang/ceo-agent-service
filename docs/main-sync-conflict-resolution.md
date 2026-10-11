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
