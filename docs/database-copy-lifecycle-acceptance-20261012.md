# Database copy lifecycle acceptance — 2026-10-12

Derek requested validation of writes and the complete new-task lifecycle on a
SQLite copy before any live execution, without sending real external messages.

## Version and isolation

- Tested production commit: `5743f01e3a3e392d70ae523e6beb9201e774d967`.
- Reused isolated worktree: `database-redesign-plan/ceo-agent-service`.
- Source: online SQLite backup of the production database, quick_check `ok`.
- Snapshot: 471,347,200 bytes, 136 physical tables, 25,227 tasks and 219,407 runs.
- Snapshot SHA-256: `1e9575e009f55bb142fa6cf833f8746a21a2078eaa880ebc0623964cde0ebb8c`.
- Store schema `2026-10-12.1`; Email schema 51.
- Each case used a separate disposable copy. No production constructors, writes,
  queue consumers, deployment, service restart or real Agent CLI were invoked.
- Model outputs and provider replies were scripted. The real store, message
  preparation, candidate adoption, audit binding, orchestrator, SystemExecutor,
  message sender, worker settlement and dispatch transitions were exercised.
- SystemExecutor used its execution path, not dry-run. Only its provider client
  was an in-memory substitute. Socket connections and subprocess creation were
  refused within every copy case.
- Native-format JSONL and the native path index were generated in temporary
  CODEX_HOME directories. They are synthetic reference fixtures, not evidence
  of live model behavior. Current native readers resolve the saved session IDs
  and exact 0–4 line bounds; raw final_result_json is absent from physical runs.

## Acceptance cases

| Case | Evidence required and observed |
| --- | --- |
| Approve | New task pending → claimed processing; Consumer → bound Audit approve → provider dispatch → verified action/delivery → business Attempt → done. Exactly one simulated dispatch and one verified action receipt. |
| Audit return | Revision 0 preserved; feedback produces revision 1 and a new Consumer/Audit pair; only corrected content dispatched. Four linked runs. |
| Audit reject | Substantively changed replacement accepted; previous candidate/review preserved; only replacement dispatched. |
| No action | Consumer and Audit complete; skipped business Attempt, done task, zero dispatches and zero verified delivery receipts. |
| Technical retry | Failed attempt preserved; second Consumer attempt stays at revision 0 with turn_attempt 1; Audit follows successful attempt. Technical retry does not become a content-feedback parent chain. |
| Provider failure | Business Attempt failed; task not done; no verified external-action or sent-reply projection; execution input retained for recovery. |
| Uncertain provider result | Unknown verification is not projected as success; no verified action/delivery, unresolved task and recoverable input retained. |
| Scheduled task | Configuration and trigger created → actual trigger dispatch → linked scheduled reply task → Consumer/Audit → system execution → receipt → done; guard completion permits input compaction. Source/run/execution link survives reopening. |
| Work-summary business result | New work input → claimed processing → TaskAgent application → one business candidate and completed domain run → done input with `{}` body marker and retained source/hash/byte-count provenance. Duplicate source does not create another input. This exercises the existing TaskAgent domain path, not a new Consumer/Audit implementation. |
| Settlement interruption | SQLite trigger aborts final task-status transaction after system receipt persistence. Attempt insertion rolls back and task remains processing. Reopening with empty scripted role queues and no execution handlers settles from existing completed state; dispatch count and verified receipt count remain one. |
| Workbench creation API | In-process POST creates a task and turn in the copied DB; repeated client_request_id returns the same turn; timeline and reopening read back the stored task/input. Scheduler executor deliberately does not run a native Agent. |

Every case also compares all original rows and field values across **131 original
application tables** using source EXCEPT candidate. No original application row
was changed or lost. FTS virtual/shadow internals and SQLite allocation counters
are excluded from this comparison because inserting facts changes those internals.
Integrity checks pass and foreign_key_check has no new findings. The original
`runs` physical row 190042 referencing missing `tasks` physical row 404225 remains
unchanged; this acceptance does not repair that historical finding.

## Fresh verification

- Copy lifecycle tests: **11 passed in 46.93 seconds**, with original-row, quick_check and foreign
  key checks for every case. Command:

```sh
CEO_COPY_LIFECYCLE_SOURCE=<owned-temp-root>/source.sqlite3 \
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q \
  tests/test_database_copy_lifecycle_acceptance.py \
  --basetemp=<owned-temp-root>/pytest
```

The supplied source must be an owned, integrity-verified temporary snapshot with
its adjacent `snapshot.sha256`; the test is opt-in and skips without the source.
The acceptance workflow deletes all database copies and synthetic trajectories
after recording concise results. It does not create a historical backup archive.

- Focused adjacent suites: **269 passed in 53.98 seconds**, covering orchestrator,
  bounded feedback, reviewed-candidate store, SystemExecutor, scheduled consumer
  and creation API, Workbench API/executor, input references, and atomic worker
  finalization after generation changes. No live tests or full-suite run.
- Scoped Ruff and diff checks pass.
- Final production readback: tested production commit remains deployed;
  healthz `ok`; Email schema 51. Counts for test task markers, test business
  titles, `copy-scripted` runtime attempts and `copy-provider-message` receipts
  are all **zero** in production. Snapshot SHA remains unchanged.

## Evidence boundary

This verifies service state transitions, writes, persisted review/action bindings,
provenance, business projections, failure semantics and receipt-based reopening
against the deployed database shape and existing historical data. It does not
measure live model judgment, execute a real Codex CLI, prove real provider
delivery, or claim that a Workbench native turn ran. Those boundaries are
intentional under Derek's instruction not to trigger actual external sends.
No runtime or schema changes are required by these results.

## Cleanup and ownership

The exact task-owned temporary root was deleted after final passing JUnit (11 tests, zero failures/errors), source hash verification and no open handles. Removed 863,499,135 bytes of temporary database copies, focused-test files, logs and synthetic native records. Only this report and the reusable opt-in tests remain; existing production recovery backups were untouched.
