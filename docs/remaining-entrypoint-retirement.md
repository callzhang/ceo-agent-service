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
- Independent SPEC and QUALITY reviews found no code blockers.
- No stored fact, provenance, receipt, source locator, schema, Audit/retry policy
  or provider action is modified. No real test sends or new model calls.

Publication, fresh production readback and authorized artifact cleanup are
recorded below only after completion.
