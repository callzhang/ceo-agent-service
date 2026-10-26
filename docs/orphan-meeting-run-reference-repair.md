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

The one-off maintenance script used for this repair was removed after successful
production readback. It is no longer an available code path. Its source and
execution history remain in the dated repair commit, not in the current tree.
The current schema/readers retain the already normalized facts; no legacy
schema reader, auto-healing branch or persistent repair command is necessary.

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

## Current-code cleanup — 2026-10-26

Derek explicitly requires no old structure compatibility or old runtime logic.
The completed repair script and its eight tool-only parameterized test cases
are removed. Historical execution evidence above remains; it is not an active
command guide. The current native contract tests replace the obsolete broken-
structure fixture: create a valid NULL-parent Run with source Job4908 through
current owners; keep all guards/FKs enabled; require a non-NULL missing Task
update to fail. HTML/API/session-related history still render valid source
facts, while errors reading a non-NULL current Task propagate. Each of the three
catch-and-continue branches had a failing regression before its removal.

The37 focused current Meeting/professional/related-history cases pass in15.43s;
Ruff and diff checks pass; two additional valid-parent History readers pass
in3.56s. Independent final review approved after closing the third-reader and
documentation findings. Standard deployment completed49ef0784→8e0f66b00; the
exact-target mandatory gate passed, PID83730/health200 and all three installed
reader functions have zero exception handlers. The one-off command is absent
from production. Actual NULL-parent/valid-parent detail APIs and Chrome pages
load, with source Job4908 and original error retained; a real native session
timeline also loads. Schema remains the exact476 objects and FK findings are
empty. Failed36/Attention9 match the immediate pre-release baseline. The first
standard attempt stopped when another deploy moved production; it was retried
through the same gates, without a bypass. Test outputs and the integrity-verified
workflow backup were deleted and path absence verified. No schema/production
data change or real external test send was part of this cleanup.

[Exact reader deployment/cleanup evidence](validation/2026-10-26-current-meeting-readers.json).
