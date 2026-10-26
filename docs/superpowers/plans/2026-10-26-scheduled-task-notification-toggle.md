# Scheduled Task Notification Toggle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let each scheduled task independently enable or suppress its terminal desktop notifications.

**Architecture:** Persist `notify_on_completion` on the current schedule record with default `true`, include it in each immutable `ScheduledTaskSnapshot`, and have both scheduled consumers honor the snapshot. Expose the field through the existing strict API and shared create/edit form; treat the exact prior snapshot shape as enabled so existing run history stays readable.

**Tech Stack:** Python, SQLite native schedule declarations and Store, FastAPI/Pydantic, TypeScript/React, pytest, Vitest.

**Spec:** `docs/superpowers/specs/2026-10-26-scheduled-task-notification-toggle-design.md`

## Global Constraints

- Default new and existing scheduled tasks to notification enabled.
- Apply the preference to manual and cron runs, service-command and Agent tasks.
- Changing a preference affects only later triggers; never rewrite prior snapshots.
- Disabling notifications must not alter run state, Attention, or History.
- Preserve current native schema and strict API contracts; use the repository’s schema release path.

## Review Focus

- Existing scheduled-run snapshots omit the new field; they must remain readable and represent the prior enabled behavior.
- A task edit after trigger creation must not change that run’s notification choice.
- The service-command and Agent paths must both suppress notifications when the frozen value is false.
- The checkbox must be independent from schedule activation (`enabled`) and preserve its value through create/edit round trips.
- Invalid non-boolean values must be rejected at the strict API/model boundary.

---

## File Structure

- `app/schedules_definitions.py`: add the default-on persisted schedule field to current schema projection and DDL.
- `app/store.py`: map, create, update, and snapshot the preference using the current schedule version/CAS flow.
- `app/agent_cron/models.py`: add the task/snapshot field, strict JSON serialization, and explicit prior-shape decoding.
- `app/web_api/scheduled_tasks.py`: validate, return, and freeze the field in task and run payloads.
- `app/agent_cron/consumer.py`: gate terminal notifications using the immutable run snapshot.
- `frontend/src/api/scheduledTasks.ts`: type and validate the preference on task/snapshot/draft payloads.
- `frontend/src/pages/ScheduledTasksPage.tsx` and `frontend/src/styles.css`: add the shared create/edit checkbox.
- Focused Store, API, consumer, frontend API and page tests: cover default, round trip, freezing, and suppression.
- `tests/gate_files.txt`, `docs/architecture.md`, `docs/runtime-mechanism.md`: include the focused test and document behavior.

## Tasks

### Task 1: Persist and snapshot the preference

**Files:**
- Modify: `app/schedules_definitions.py`
- Modify: `app/agent_cron/models.py`
- Modify: `app/store.py`
- Test: `tests/test_scheduled_task_store.py`
- Test: schema declaration tests that assert `SCHEDULE_FIELDS` and fresh DDL parity

**Interfaces:**
- `ScheduledTask.notify_on_completion: bool`
- `ScheduledTaskSnapshot.notify_on_completion: bool`
- `AutoReplyStore.create_scheduled_task(..., notify_on_completion: bool = True)`
- `AutoReplyStore.update_scheduled_task(..., notify_on_completion: bool | None = None)`

- [ ] Add `test_notification_preference_defaults_enabled_and_roundtrips` to `tests/test_scheduled_task_store.py`, asserting default `true`, then persisted `false` and `true` after updates.
- [ ] Add `test_notification_preference_is_frozen_in_snapshot` and `test_prior_snapshot_without_notification_preference_reads_enabled` to `tests/test_scheduled_task_store.py`; assert the serialized new field and exact old-shape behavior, and reject a present non-boolean value.
- [ ] Run `pytest -q tests/test_scheduled_task_store.py tests/test_configs_target_integrated.py tests/test_run_adoption_definitions.py`; confirm the new assertions fail before implementation.
- [ ] Add the default-on current schedule column and projection field; map it in Store read/create/update while preserving existing version CAS semantics.
- [ ] Add the strict snapshot field to serialization and decode only the explicit old key set as the prior enabled behavior.
- [ ] Re-run `pytest -q tests/test_scheduled_task_store.py tests/test_configs_target_integrated.py tests/test_run_adoption_definitions.py`.

### Task 2: Expose the setting through the strict API

**Files:**
- Modify: `app/web_api/scheduled_tasks.py`
- Test: `tests/test_console_scheduled_tasks_api.py`

**Interfaces:**
- `ScheduledTaskCreatePayload.notify_on_completion: bool = True`
- Task payload and run snapshot payload include `notify_on_completion: bool`.

- [ ] Add `test_scheduled_task_api_notification_preference_roundtrip` and `test_scheduled_run_api_returns_frozen_notification_preference` to `tests/test_console_scheduled_tasks_api.py`, covering default-on create, false create/update, and run snapshots.
- [ ] Run `pytest -q tests/test_console_scheduled_tasks_api.py`; confirm the new assertions fail.
- [ ] Pass the field to Store create/update and include it in task and run snapshot payloads.
- [ ] Re-run `pytest -q tests/test_console_scheduled_tasks_api.py` and its strict payload validation controls.

### Task 3: Gate terminal notifications by the frozen preference

**Files:**
- Modify: `app/agent_cron/consumer.py`
- Test: `tests/test_scheduled_agent_consumer.py`

**Interfaces:**
- Both `ScheduledTaskTriggerConsumer` and `ScheduledAgentConsumer` read `run.snapshot.notify_on_completion` before sending terminal notifications.

- [ ] Add `test_service_command_notification_preference_suppresses_terminal_notifications` and `test_agent_notification_preference_suppresses_terminal_notifications` to `tests/test_scheduled_agent_consumer.py`, covering success and failure while terminal persistence still completes.
- [ ] Add `test_notification_preference_edit_does_not_change_dispatched_run` to `tests/test_scheduled_agent_consumer.py`, proving the trigger snapshot determines notification behavior after a current task edit.
- [ ] Run those three exact nodes with `pytest -q`; confirm the new assertions fail before implementation.
- [ ] Gate the existing notification sends using the frozen run snapshot; preserve status/Attention code paths.
- [ ] Re-run `pytest -q tests/test_scheduled_agent_consumer.py`.

### Task 4: Add the create/edit checkbox and finish documentation

**Files:**
- Modify: `frontend/src/api/scheduledTasks.ts`
- Modify: `frontend/src/pages/ScheduledTasksPage.tsx`
- Modify: `frontend/src/styles.css`
- Test: `frontend/src/api/scheduledTasks.test.ts`
- Test: `frontend/src/pages/ScheduledTasksPage.test.tsx`
- Modify: `tests/gate_files.txt`
- Modify: `docs/architecture.md`
- Modify: `docs/runtime-mechanism.md`

**Interfaces:**
- Shared draft field: `notify_on_completion: boolean`, default `true`.
- Checkbox label: `发送完成通知`.

- [ ] Add `test_scheduled_task_api_round_trips_notification_preference` to `frontend/src/api/scheduledTasks.test.ts` and `test_notification_checkbox_defaults_on_and_roundtrips_edit_state` to `frontend/src/pages/ScheduledTasksPage.test.tsx`; assert the request field does not replace schedule `enabled`.
- [ ] From `frontend/`, run `npm test -- --run src/api/scheduledTasks.test.ts src/pages/ScheduledTasksPage.test.tsx`; confirm the new assertions fail before implementation.
- [ ] Add the field to the TypeScript interfaces and strict response validators; include it in draft conversion, create/update payloads, and the shared form checkbox.
- [ ] Ensure `tests/test_scheduled_task_store.py`, `tests/test_configs_target_integrated.py`, `tests/test_run_adoption_definitions.py`, `tests/test_console_scheduled_tasks_api.py`, and `tests/test_scheduled_agent_consumer.py` are included in `tests/gate_files.txt`; update architecture/runtime documentation for the preference and frozen semantics.
- [ ] Run the focused pytest files above, from `frontend/` run `npm test -- --run src/api/scheduledTasks.test.ts src/pages/ScheduledTasksPage.test.tsx`, run Ruff on changed Python files, and run `git diff --check`.
- [ ] Commit the completed feature as a scoped change; use the standard deployment workflow only after the repository gate passes.
