# Task2D1 WorkSummary runtime cutover

Base: `c3f8652e8` on `codex/task-run-entity-phase2-20261022`. This is a local unactivated candidate. No constructor switch, provider/native CLI call, database migration, production read/write, push, deployment, or full suite was run.

## Changed runtime owners

- `AutoReplyStore.enqueue_work_summary_input` now creates a native `kind='work_summary'` Task through `task_entity_storage.create_task`, inserts the one-to-one `work_summary_inputs.task_id` child, and returns `tasks.id`. Duplicate source identity returns that canonical ID. Pending/failed payload refresh updates only the payload and source-derived provenance; processing/done/skipped inputs keep their body. Existing child uniqueness is the idempotency boundary.
- WorkSummary DTO reads, due claims, terminal compaction, failed retry, retry scheduling, capacity deferral, stale reset and startup reset now read/write `tasks` plus the typed child. Claims keep pending/due filtering, ID order, attempts increment, error clear and available-at clear in one immediate transaction. The terminal path clears only the payload and sets the compact marker; prior source-created time, hash and byte count remain unchanged.
- Task Agent optional completed Run creation and the actual processing claim, success finish and failure finish now call the accepted professional Run API with native Task/Run IDs. Domain apply, WorkSummary terminal transition, Run finish, and projection receipt remain in the existing transaction and order. Projection stays in `run_projections`.
- WorkSummary-specific in-flight and orphan recovery now resolve `runs.task_id` plus `run_provenance`, finish native Runs, and close only native child `runtime_attempts` of workload kind `task` whose status is starting/running and whose first effect has not started. Effect-started attempts remain with the existing reconciliation path.
- WorkSummary History rows use the canonical Task ID. Task project repair resolves completed native Task Agent Runs with `native_run_ref`. The Task-specific runtime parent checks use native provenance/parent IDs.

## Evidence and checks

The pinned BASE showed the source-shaped WorkSummary projection and `task_agent.py` calls to `claim_logical_run` / `finish_logical_run`. Fail-first equivalent command imported the BASE `task20_projection("work_summary_inputs")` and queried it against actual native Task DDL; exact output: `OperationalError: no such column: legacy_key`. The focused native target fixture uses the actual Task/Run DDL objects. `source_ref='42'` deliberately differs from the allocated canonical Task ID; the test exercises enqueue deduplication and pending payload refresh, processing protection, native Run claim/finish/readback, terminal body retirement and provenance retention, explicit failed retry and capacity deferral.

- `python -m pytest -q tests/test_work_summary_task_runtime.py --basetemp=/tmp/task-run-entity-phase2-tests-2d1` — 2 passed.
- `python -m compileall -q app/store.py app/task_agent.py app/task_project_repair.py` — passed.
- `ruff check app/store.py app/task_agent.py app/task_project_repair.py tests/test_work_summary_task_runtime.py` — passed.
- `git diff --check` — passed.

The task-owned test temp directory was removed after the focused run.

## Explicit remaining work

- Generic dispatcher claim/lease infrastructure is still a subsequent all-kinds unit; this report does not claim it has moved or that constructor activation is safe. `app/web_api/dashboard.py:_runtime_metadata` remains a source-shaped Task Agent/WorkSummary runtime reader (`record_type`, `legacy_key`, and `summary_input_id`) and needs a separate native reader conversion before dashboard reads are ready.
- Meeting and OKR producer/readers, other family History branches, remaining full CLI/runtime and constructor integration, cold-open and complete copy/inverse verification, and release gates remain pending.
- `todo_evidence_candidate.work_summary_input_id` (`app/business_definitions.py`, stored INTEGER, default/sentinel `0`) is the known typed crossing. New writes receive the canonical Task ID from enqueue. Historical values still need explicit coordinator forward/inverse conversion and a remapped-ID regression before historical lookup can be considered ready. No runtime identity-map lookup or numeric fallback was added.
- Remaining source logical Run projection references in Store are family-general or unrelated APIs; the WorkSummary recovery and producer paths changed here no longer use them. `latest_task_ref` was removed from the WorkSummary repair caller.
