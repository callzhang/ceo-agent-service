# DingTalk Todo Sync Design

Date: 2026-06-27

## Goal

Connect internal `work_todos` with DingTalk Todo so high-confidence action items
show up in the owner's DingTalk task list while CEO Agent keeps `work_todos` as
the project-management source of truth.

The integration should reduce duplicate follow-up messages. Before sending a
TODO follow-up, the service checks the linked DingTalk Todo state. If the owner
has already completed the DingTalk Todo, the internal TODO is closed and the
follow-up is skipped.

## Decisions

- `work_todos` remains the primary project-management data.
- DingTalk Todo is an execution layer for the owner, not Derek's management
  dashboard.
- Only high-confidence internal TODOs create DingTalk Todo tasks.
- DingTalk Todo is created immediately after the task agent creates or updates a
  qualifying internal TODO.
- Derek is not added as a DingTalk Todo executor by default. Derek continues to
  use `/tasks` for the management view.
- Completion wins. Strong completion evidence on either side closes the other
  side.
- The first implementation does not delete DingTalk Todo tasks and does not
  synchronize title or deadline edits back and forth.

## Current Context

The service already has:

- `work_projects`, `work_todos`, `work_updates`, `follow_up_drafts`, and
  `task_agent_runs`.
- A task-agent flow that creates and updates internal TODOs under projects.
- Follow-up delivery that checks local completion evidence before sending.
- DWS support for DingTalk Todo commands:
  - `dws todo task create`
  - `dws todo task get`
  - `dws todo task done`
  - `dws todo task update`
  - `dws todo task delete`

The DingTalk Todo skill marks the API surface as experimental, so the service
must treat DWS Todo failures as synchronization failures, not as reasons to roll
back internal task state.

## Architecture

Add a `todo_sync.py` module with deterministic side-effect handling. The task
agent still only creates or updates internal TODOs. It does not call DWS and does
not decide whether an external task was successfully created.

`todo_sync.py` owns three operations:

- `maybe_create_dingtalk_todo(work_todo_id)`: create a DingTalk Todo for a
  qualifying internal TODO.
- `pull_dingtalk_todo_statuses()`: refresh linked DingTalk Todo states and close
  internal TODOs when DingTalk says they are done.
- `sync_completed_todo_to_dingtalk(work_todo_id, evidence)`: mark the linked
  DingTalk Todo done after CEO Agent closes an internal TODO from strong
  evidence.

`process_due_follow_ups` calls the sync layer before sending a follow-up for a
TODO. If the linked DingTalk Todo is already done, it closes the internal TODO
and skips the follow-up.

## Data Model

Add `work_todo_dingtalk_links`.

Columns:

- `id`
- `work_todo_id`
- `dingtalk_task_id`
- `executor_user_id`
- `executor_name`
- `title_snapshot`
- `deadline_at_snapshot`
- `priority_snapshot`
- `status`
- `last_dingtalk_done`
- `last_dingtalk_payload_json`
- `last_pull_at`
- `last_push_at`
- `last_error`
- `created_at`
- `updated_at`

Status values:

- `creating`: a creation attempt is in progress or reserved.
- `active`: DingTalk Todo exists and the internal TODO is still open.
- `done`: completion has been confirmed on one or both sides.
- `cancelled`: the internal TODO was cancelled and this link should no longer
  sync.
- `failed`: create, pull, or push failed and needs retry or inspection.

Indexes and constraints:

- Index `work_todo_id`.
- Unique index `dingtalk_task_id` when non-empty.
- Enforce at most one `creating` or `active` link per `work_todo_id`.

`work_todos` should not receive DingTalk-specific columns. This keeps internal
task state independent from the external execution system.

## Creation Rules

Create DingTalk Todo only when all hard conditions are true:

- The internal TODO status is `open` or `waiting_owner`.
- `owner_user_id` is present.
- `deadline_at` is present.
- The title is a concrete action item, not a generic prompt such as "follow up".
- The project or TODO is not sensitive and is suitable for formal assignment.
- `completion_evidence_json` is empty.
- No `creating` or `active` DingTalk link exists for the TODO.

The system should run these hard checks after `apply_task_agent_decision`
persists TODO changes. The model may imply that a TODO is actionable, but the
code makes the final side-effect decision.

Priority mapping:

- `P0` -> DingTalk priority `40`
- `P1` -> DingTalk priority `30`
- `P2` -> DingTalk priority `20`
- `none` -> DingTalk priority `20`

Create command shape:

```sh
dws todo task create \
  --title "<todo title>" \
  --executors <owner_user_id> \
  --due "<deadline ISO-8601>" \
  --priority <10|20|30|40> \
  --format json
```

After create succeeds, call `dws todo task get --task-id <id> --format json` to
verify the task is readable. If verification fails after creation, keep the task
id in the link, mark the link `failed`, and do not create a duplicate.

## Pull Sync

Task maintenance should periodically pull linked DingTalk Todo states.

Flow:

1. Select `active` links.
2. Call `dws todo task get --task-id <dingtalk_task_id> --format json`.
3. Store the latest response summary in `last_dingtalk_payload_json`.
4. If DingTalk says the task is done:
   - Set `work_todos.status='done'`.
   - Set `work_todos.completed_at`.
   - Write `completion_evidence_json` with source
     `dingtalk_todo:<dingtalk_task_id>`.
   - Mark the link `done`.
   - Add a `work_updates` timeline entry.
5. If DingTalk says the task is not done, only refresh pull metadata.
6. If pull fails, set `last_error` and keep the internal TODO unchanged.

## Push Completion

When the internal TODO is closed from strong evidence, the sync layer should mark
the linked DingTalk Todo done.

Completion sources include:

- task-agent decision with explicit completion evidence
- message or follow-up reaction with clear completion evidence
- AI minutes or local document evidence that clearly states completion
- DingTalk Todo pull result

Flow:

1. Find the active DingTalk link for the internal TODO.
2. Call `dws todo task done --task-id <id> --status true --format json`.
3. If successful, mark the link `done` and set `last_push_at`.
4. If it fails, keep the internal TODO done, set `last_error`, and leave the link
   retryable.

The internal TODO is not rolled back because internal completion evidence is the
source of truth for project state.

## Follow-Up Guard

Before sending a follow-up bound to a TODO:

1. Find an active DingTalk link for `draft.todo_id`.
2. If one exists, pull `dws todo task get`.
3. If DingTalk says done, close the internal TODO, mark the link done, and skip
   the follow-up with reason `dingtalk_todo_done`.
4. If DingTalk is not done, continue existing local evidence checks, reaction
   suppression, sensitivity routing, and frequency caps.
5. If DingTalk pull fails, record the sync error and continue with existing
   internal evidence checks. DingTalk unavailability should not block all
   follow-up behavior.

This guard prevents duplicate reminders when the owner already completed the
formal DingTalk Todo.

## Error Handling

- Create failure: keep the internal TODO, mark link `failed`, and record
  `last_error`.
- Create success but get failure: store `dingtalk_task_id`, mark `failed`, and
  avoid duplicate create.
- Pull failure: record `last_error`; do not change internal TODO status.
- Push completion failure: keep internal TODO done; record `last_error` for
  retry or audit.
- DWS login required: defer the sync attempt and record `last_error` without
  changing internal TODO state.
- DingTalk task deleted or inaccessible: mark link `failed`; internal TODO
  remains governed by internal evidence and follow-up rules.
- Internal TODO cancelled: mark active link `cancelled`; do not delete the
  DingTalk Todo in the first version.
- Title or deadline mismatch: record snapshot mismatch in link metadata or a
  work update; do not auto-update DingTalk Todo in the first version.

## UI and Audit

Task detail pages should show DingTalk Todo link state for each TODO:

- linked task id
- executor
- sync status
- last pull time
- last push time
- last error
- whether DingTalk currently says done

Operation logs should include DingTalk Todo sync attempts so failures are visible
next to task and follow-up activity.

## Out of Scope

- Creating DingTalk Todo for every internal TODO.
- Adding Derek as a DingTalk Todo executor or watcher.
- Deleting DingTalk Todo tasks automatically.
- Bidirectional title, description, deadline, or priority edit sync.
- Treating DingTalk Todo as the project-management source of truth.
- Replacing existing follow-up messages with DingTalk Todo entirely.

## Tests

Add focused tests for:

- A high-confidence TODO creates a DingTalk link and calls fake DWS create.
- Missing owner, missing deadline, sensitive project, completion evidence, and
  existing active link each prevent creation.
- Create failure records a failed link while leaving the internal TODO intact.
- Create success followed by get failure records the task id and avoids duplicate
  creation.
- Pulling a done DingTalk Todo closes the internal TODO and writes completion
  evidence.
- Internal completion evidence calls DingTalk `done true`.
- Push completion failure keeps the internal TODO done and records link error.
- Follow-up sends are skipped when the linked DingTalk Todo is already done.
- Follow-up behavior continues when DingTalk status pull fails.
- Task maintenance invokes DingTalk Todo pull.
- Task detail and operation log rendering include link status and errors.

## Rollout

1. Add schema and store methods for `work_todo_dingtalk_links`.
2. Add DWS client wrappers for Todo create/get/done.
3. Add `todo_sync.py` with create, pull, and push-completion operations.
4. Call `maybe_create_dingtalk_todo` after task-agent TODO persistence.
5. Call pull sync from task maintenance.
6. Call push completion when internal TODOs close with evidence.
7. Add follow-up guard before follow-up send.
8. Add task detail and operation log visibility.
9. Add focused tests.
10. Restart `com.ceo-agent-service.main` after implementation and commit.
