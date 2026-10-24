# Task 4 schedule occurrence closure — implementation checkpoint

Status: **partial; not a Task 4 acceptance or activation**.

Base: `48ef2164f931a68de6b98836d7767f83d23e9602`. Worktree: `codex/task-run-entity-phase2-speed-20261023`.

## Implemented in this checkpoint

Scheduled occurrence CRUD, dedupe reads, current-version creation, claim/link/finish lifecycle, direct readers in the dispatcher, dashboard queue, recovery, reached History/Attention SQL, and Store projections now target `schedule_occurrences`. Snapshot JSON is read through `configs.schedule_snapshot_public_id`; the schedule/config owner and Task-backed execution adapter remain separate. The existing full-state claim index and pending-only dispatch index declarations were not changed. The scheduled history page now renders each persisted aggregate interval/count as returned and no longer creates a durable-looking merge from generic `result_summary` parsing. A native-store regression exercises CRUD through dispatch completion, verifies the native table/index declarations, checks a pending query plan and bounds a pending lookup after inserting 8,000 terminal rows. A two-thread claim test proves one winner for one pending occurrence.

The release drain is intentionally still a frozen-source read: `app.consumer_system_release._prepare_publication` reads `SCHEDULED_RUN_SQL` before native migration. Changing it to an unconditional `schedule_occurrences` read would make pre-migration quiescence fail against the real old production schema. It is an explicit publication-precondition reader, not a runtime fallback. The source oracle and `publication_scheduled_source` remain unchanged.

## Verification and exact boundary

- `pytest -q tests/test_scheduled_occurrence_native.py`: 2 passed. The test uses `tests.test_store_task_entity_creator._target_store`, the assembled native target Store.
- `python -m ruff check` on changed Python files: passed.
- `python -m py_compile` on changed Python modules: passed.
- `git diff --check`: passed.
- The previously run legacy source-schema cron set reported 35 failed / 43 passed because old fixtures lack `schedule_occurrences`; those failures are known fixture/schema incompatibility, not counted as a green result and were not rerun after that boundary was clarified.
- `npx vitest run src/pages/ScheduledTasksPage.test.tsx` could not start: the checkout lacks `@vitejs/plugin-react` and `vitest/config` dependencies. No frontend test pass is claimed.

## Still required before Task 4 can be accepted

Typed transient command counts have not been threaded through the registry/Consumer. No transactional zero-effect aggregation or covered-instant restart/concurrent-create deduplication is implemented. No evidence exists here for cross-config/timezone/command boundaries, all protected references, or rollback during aggregation. Declared typed occurrence IDs embedded in full/compacted Task inputs and Meeting source parameters have not yet been converted forward/current-inverse; the confirmed public-ID/native-ID collision therefore remains a cutover blocker. Two composed portable Task/Run/adoption cycles with new occurrence facts were not run. Query plans/timing for latest/page/reply-execution/instant lookups were not verified. No history has been retired, no schema/manifest/constructor was activated, and no production/provider/main/push/deploy action was taken.

## Target/index facts

The test's pending oldest-due lookup produced `SEARCH schedule_occurrences USING INDEX idx_schedule_occurrences_claim (dispatch_status=? AND scheduled_for<?)` on the assembled target. The other reviewed target declarations remain `idx_schedule_occurrences_dispatch` (pending-only partial), `idx_schedule_occurrences_latest`, `idx_schedule_occurrences_reply_execution`, and `idx_schedule_occurrences_scheduled_instant`. The current test checks their presence but does not establish each access path.
