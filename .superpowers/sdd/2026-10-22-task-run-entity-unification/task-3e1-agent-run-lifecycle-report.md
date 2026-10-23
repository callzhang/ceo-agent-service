# Task3E1 — Consumer/Audit Run lifecycle report

## Scope completed

The assigned isolated checkout was `/Users/derek/.codex/worktrees/task-run-entity-phase2/ceo-agent-service`, pinned at `5359a5f5f33f8719640f09fd6dc23bc80d756654`. `app/store.py` now reads the Consumer/Audit Run row directly from `runs`, with adopted candidate/review JSON from `run_adopted_results` and `run_adopted_reviews`. The turn lookup, attempt numbering, business-object latest-run lookup, task-generation list, terminal summaries, and active-lease read use native Run/Task identities.

The lifecycle writer path now claims into `runs`, validates Consumer/Audit ancestry there, reclaims expired Run leases, renews leases, stores session/transcript range, transitions completion/failure, deletes a genuinely unstarted Run, and marks an expired Run failed. Completion and adopted candidate/review persistence remain in one write transaction through the already-native `ReviewedCandidateStoreMixin` methods. Run result and event reads query `runtime_attempts.parent_id`; native trajectory hydration accepts native Run keys directly. No legacy column detection or duplicate Run projection was added. Generic failed Run completion does not create a new failure event; source state is preserved.

## Verification

Focused assembled native Store command:

```text
pytest -q --basetemp=/tmp/task3e1-agent-run-lifecycle.full-focused.20261023 tests/test_store_task_entity_creator.py
```

Output:

```text
.............                                                            [100%]
13 passed in 9.03s
```

The new cases exercise Consumer claim/read/lease/session/failure, native Run ownership, a Consumer-to-Audit adopted proposal/review, distinct Review and Audit Run IDs, exact repeated terminal completion, and rejection of changed terminal content. The first native lifecycle test attempt exposed the old trajectory helper's dependency on source-shaped column names (`IndexError: No item with that key`); after switching the helper to the actual native Run and RuntimeAttempt fields, the cases passed.

Additional checks:

```text
python -m compileall -q app/store.py app/native_trajectory.py
ruff check app/store.py app/native_trajectory.py tests/test_store_task_entity_creator.py
```

Result: compilation succeeded; `All checks passed!`. `git diff --check` exited 0.

## Boundaries and remaining gates

This is an unactivated Store/Run unit. The Store constructor/schema selection was not changed, and no production worktree, provider, CLI call, push, deployment, or runtime readback occurred. RuntimeAttempt writers and their independent retry/session lifecycle remain the next unit. Professional historical Run paths, the broader recovery coordinator and History aggregation still contain source-layout callers and require their own scoped conversion and validation. The assembled tests insert no fake provider invocation and do not establish live CLI, History, or production behavior. Independent SPEC/QUALITY review remains outstanding.

The RED/GREEN evidence available in this run is the fail-first trajectory integration defect above and its passing correction. A separate clean pinned-base execution of the newly authored acceptance tests was not performed; no base-RED claim is made.

Additional native owner integration verification:

```text
pytest -q --basetemp=/tmp/task3e1-agent-run-lifecycle.final-focused.20261023 tests/test_store_task_entity_creator.py tests/test_native_reviewed_candidate_store.py
```

Output:

```text
25 passed in 12.20s
```

This includes the 12 native reviewed-candidate Store cases. The Audit regression now reads its adopted review with no `runtime_attempts` row and an unavailable native transcript: the adopted value is returned, tool events stay empty, and no CLI result is fabricated. Generic `fail_agent_run` retains zero `run_failures`; `fail_expired_agent_run` preserves the source recovery writer's `terminal_failure` phase/body/time in the native `run_failures` owner. Discard removes that native child before deleting its unstarted parent.

The scoped aggregate attempt also included legacy constructor-bound `tests/test_native_only_core.py` and `tests/test_agent_metadata_lock_boundary.py`; it reported 30 failures / 32 passes. The failures occur while those tests initialize `AutoReplyStore` and call the pre-activation constructor, which lacks `task_inputs` (`sqlite3.OperationalError: no such table: task_inputs`), before exercising this lifecycle code. This is the assigned constructor activation gate, not a native Run lifecycle assertion. One failure in the new test was a missing `json` import and was fixed; the assembled two-file command above is the clean rerun.
