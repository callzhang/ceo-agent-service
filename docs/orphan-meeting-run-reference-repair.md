# Historical Meeting Run reference repair — 2026-10-26

## Confirmed cause and repair

Derek explicitly requested fixing the historical FK finding after the Signal
release. A fresh production read confirmed Run190042 still refers to absent
native Task404225. Its source is `meeting_alignment_runs`, original Job4908;
RuntimeAttempt167325 remains failed with its original session identity. Neither
the native Task nor an exact historical Task identity mapping exists. The native
session file was not found in the configured Codex sessions/archived_sessions.
The available evidence does not establish when or why the original Task was
removed. The invalid edge predates the latest Signal migration; that migration
preserved it under its explicitly limited scope.

The existing schema already represents a historical Meeting Run without a
current Task: `runs.task_id=NULL`, with the original source Job retained in
`run_provenance.job_id`. Repair only that optional native pointer. Do not create
a Task, guess a replacement by integer/title, remove the Run or its call/error
history, change its retry status, or replay it. No schema/version, runtime,
review, authorization or provider change is required.

`scripts/repair_orphan_meeting_run.py` is an explicit maintenance tool, never
called from Store initialization. It requires the exact Run/missing-Task/source-
Job identities, rejects a live Task or active Run/call, and only updates the one
pointer. The CLI defaults to a real transaction followed by rollback; `--apply`
commits. The caller supplies a verified backup before production maintenance.

## Verification before production

The new narrow regression first failed because the repair tool was absent.
After implementation,10 focused cases passed in7.71s, including existing native
Meeting provenance and HTML/API orphan readers. Ruff and diff checks pass.

A current production-derived copy (Store25.3/Email54,476 objects) passed in23.44s:

- Exact source FK finding: `('runs',190042,'tasks',1)`.
- Default repair rehearsal followed by rollback restores the exact original
  database file SHA256, including the invalid pointer.
- Applied repair changes only `runs[190042].task_id` from404225 toNULL.
- Typed row hashes of all49 tables (including allocation sequences), normalized
  only for that one cell, and the complete schema declaration remain equal.
- FK findings become empty; full integrity_check is `ok`.
- Original Job4908, failed RuntimeAttempt167325 and session identity remain.
- Actual deployed old code cold-starts the Store and renders the Run as HTTP200
  with `Meeting Task unavailable` and original source Job4908.
- Real provider calls:0.

## Production and cleanup

Pending the independently reviewed maintenance operation. No runtime deployment
or restart is necessary for this data-only correction. The production operation
will refresh and verify its backup, execute the explicit single-row transaction,
read back FK/integrity/schema/Run/child and live UI/API state, then remove all
of this repair's backups and disposable test copies under Derek's existing
cleanup authorization. Earlier release evidence remains dated historical fact.
