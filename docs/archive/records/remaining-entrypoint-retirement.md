# Remaining entrypoint retirement — 2026-10-26

This scoped follow-up removes code entrypoints, not historical data. Its source
baseline is `3290c9703` in an isolated worktree. No schema or migration is needed.

| Retired entry | Actual callers and disposition |
| --- | --- |
| `latest_task_ref` | Inspection, replay and fixture readers now use the existing `native_run_ref(..., "task_agent_runs")`. Both select the newest completed Task call by exact Run parent; a newer failed call does not override it. |
| `task_project_repair` / `repair-task-projects-plan` / `task_project_value` | Only a manual CLI generated old Project restoration/archive suggestions. Its apply command was already retired; no worker, scheduler or current semantic importer calls it. Remove planner, CLI, Project-shaped decoder, dedicated tests and obsolete README instructions. |
| `_index_meeting_codex_session` / `upsert_codex_session_search_index` | The helper has no application caller, and the Store writer's sole application reference is that helper. Remove both; retain existing search metadata, current readers and in-memory native corpus/scoring. Tests seed retained metadata directly with an insert-only fixture. |

Retired tests cover only the removed planner/writer. Reader assertions remain:
metadata title/identity/order, unavailable source, native input, FTS/embedding,
History filtering, and current CLI semantic import parsing. Two existing Meeting
fixtures omitted the actual RuntimeAttempt parent; they now bind the exact Run,
matching the real producer. Restoring the original metadata writer reproduces
the same original History failure before that fixture repair; production code
is not changed to accommodate the fixture.

## Focused verification

- Before removal,20 native/metadata cases passed.
- After integration,41 native/professional/readers/Store FTS/embedding/History
  cases pass in6.84s.
-31 inspection/replay-readback/retired/current CLI cases pass in7.30s.
-15 current Workbench/schema/inverse cases pass in0.71s.
- Ruff/diff checks pass; independent SPEC and QUALITY reviews found no blockers.
- No stored fact, provenance, receipt, source locator, schema, Audit/retry policy
  or provider action is modified. No real test sends or new model calls.

## Production and cleanup

Standard exact-target deployment completed `136a44962 → 8e4007913`; the mandatory
clean-clone gate passed under the unchanged2GB/5minute limits. At19:36 UTC,
PID21568 loaded that exact commit. Health, status, Attention and History returned
200. Failed Tasks27 and Attention summary5 match the pre-release baseline.
The UI still exposes the independently existing WeChat sender error
`prepared WeChat delivery is required`; no WeChat code changed between the
immediate production baseline and this release. This cleanup does not claim
to resolve those existing business issues.

Before publication, all5340 completed Task Run references exactly matched the
shared reader. After publication, all476 schema objects and all795 retained
metadata rows have the same complete checksums as before, and FK findings are
empty. Installed sources have none of the retired entries. The installed
inspection script reads actual input417552, completed Run399688 and its available
native result. Chrome History search reads2509 matching results; Meeting399558
shows its native execution reason and summary without the unavailable notice.
The ephemeral tab is closed.

The deployment workflow backup passed its complete marker and full SQLite
integrity check. After readback and zero-open-file checks, the exact verified
backup and the entire owned temporary root were deleted and their absence
verified. Production DB and original native trajectories are preserved.
No physical DB savings are claimed from temporary artifact deletion.

[Body-free acceptance record](validation/2026-10-26-remaining-entrypoints.json).
