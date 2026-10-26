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

## Production readback and cleanup

Explicit repair commit `d598a3618` was independently reviewed without blockers
and pushed before execution. A fresh production recovery backup passed complete
marker and full integrity checks. The exact CLI transaction committed at02:02
America/Los_Angeles on2026-10-26, reporting one changed row and FK findings1→0.

Fresh readback confirmed `runs[190042].task_id=NULL`, unchanged retry/error/time
facts, original Job4908 and failed RuntimeAttempt167325/session identity, no
fabricated Task404225, empty complete foreign_key_check and integrity_check=ok.
The exact476-object schema digest remains
`234e17843afc171b4fa8cb42f3e1d959ef2ba415f43e295dc7db640ab3d02221`.
Health and the real Meeting detail API return200. Chrome opened the actual
Meeting190042 page and expanded Runtime details: Task unavailable, source Job4908,
original retry status and startup-interruption error all remain visible. The
unavailable native process record was not reconstructed or represented as found.
No provider call, business replay, schema change, runtime deployment or service
restart was performed; this is a data-only maintenance correction.

The task-owned temporary root (test outputs, verified source, working copy and
fresh production recovery backup) was deleted after readback and verified absent. Concise
[structured evidence](validation/2026-10-26-orphan-run-reference-repair.json)
remains; original production/native trajectory files are preserved.
