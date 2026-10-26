# Scheduled Task Notification Toggle

## Intent

Let Derek silence only the scheduled tasks whose completion notifications are too frequent, while leaving notification behavior enabled for other tasks.

## Goals

- Add a `发送完成通知` checkbox to the create and edit form for every scheduled task.
- Default new and existing tasks to enabled, preserving current behavior.
- Persist the preference on the scheduled task and freeze it into each new run snapshot.
- Apply it to manual and cron triggers, service-command tasks, and Agent tasks.
- When disabled, suppress only the desktop notification. Keep run status, Attention, and History unchanged.

## Approaches

1. **Per-task boolean in the schedule and run snapshot — selected by Derek.** This matches the need to silence only frequent tasks. The run snapshot keeps an already-created occurrence bound to the setting it was created with.
2. **One global notification switch.** Simpler storage, but disables notifications for every task and does not solve the selective-noise problem.
3. **Automatic rate limiting or aggregation.** Could reduce volume, but adds policy and hides the explicit per-task choice requested here.

## Design

- Add a persisted boolean `notify_on_completion`, defaulting to `true`, to the current schedule model and its versioned configuration.
- Add the boolean to `ScheduledTaskSnapshot`; newly created run snapshots copy the current task value. Existing immutable run snapshots using the prior exact shape are interpreted as enabled, preserving their historical behavior.
- Render and submit the checkbox in the existing shared create/edit form. The task API returns the value and includes it in create/update payloads.
- Both scheduled consumers check the run snapshot before sending the existing success/failure desktop notification. No notification is sent for nonterminal outcomes, retries, or skips, regardless of this preference.
- Update the schedule schema declarations and focused regression tests to preserve current schema/read/write contracts.

## Non-goals and constraints

- No global switch, notification frequency policy, aggregation, new notification channel, or change to task execution, terminal status, Attention, or History.
- Do not mutate prior run snapshots when a task preference changes.
- Follow the repository’s current schema declaration/release and standard deploy gates; no direct production data edits.

## Verification

Cover default-on and off persistence, snapshot freezing across task edits, old snapshot reads, API validation/round trips, and notification suppression for both task execution forms. Run the focused backend and frontend suites plus the repository’s mandatory deployment gate.
