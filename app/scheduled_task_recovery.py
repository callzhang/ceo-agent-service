from __future__ import annotations

from datetime import datetime, timezone

from app.store import AutoReplyStore


_LEGACY_AGENT_FAILURES = (
    "codex_process_failed",
    "consumer_retry_exhausted",
    "runtime_route_paused",
)


def close_superseded_scheduled_reply_tasks(
    store: AutoReplyStore,
    *,
    now: datetime | None = None,
) -> int:
    """Close stale Agent executions after a task became a service command.

    A task migration can leave an already-dispatched Agent execution behind
    the current service-command definition. Reopening that old execution would
    run a stale prompt/runtime. This only closes a failed task when its run
    snapshot proves the old Agent form, the current task has a newer command
    version, every run is terminal, and no message or external action was
    recorded.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("superseded scheduled task close time needs a timezone")
    now_text = now.astimezone(timezone.utc).replace(microsecond=0).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    placeholders = ",".join("?" for _ in _LEGACY_AGENT_FAILURES)

    with store._immediate_write_transaction() as db:
        rows = db.execute(
            f"""
            select
                task.id as task_id,
                task.business_object_key as business_object_key,
                task.execution_generation as task_generation,
                run.scheduled_task_id as scheduled_task_id,
                current_task.version as current_version,
                latest_attempt.id as attempt_id
            from reply_tasks as task
            join scheduled_task_runs as run
              on run.event_id=task.trigger_message_id
             and run.execution_kind='reply_task'
             and run.execution_id=cast(task.id as text)
             and run.dispatch_status='dispatched'
            join scheduled_tasks as current_task
              on current_task.id=run.scheduled_task_id
            join reply_attempts as latest_attempt
              on latest_attempt.channel=task.channel
             and latest_attempt.conversation_id=task.conversation_id
             and latest_attempt.trigger_message_id=task.trigger_message_id
             and latest_attempt.id=(
                     select max(candidate.id)
                     from reply_attempts as candidate
                     where candidate.channel=task.channel
                       and candidate.conversation_id=task.conversation_id
                       and candidate.trigger_message_id=task.trigger_message_id
                 )
            where task.channel='scheduled'
              and task.status='failed'
              and task.error in ({placeholders})
              and current_task.deleted_at is null
              and trim(current_task.command)<>''
              and json_valid(run.snapshot_json)
              and json_type(run.snapshot_json)='object'
              and trim(coalesce(json_extract(run.snapshot_json, '$.command'), ''))=''
              and cast(json_extract(run.snapshot_json, '$.task_version') as integer)
                  < current_task.version
              and latest_attempt.send_status='failed'
              and trim(coalesce(latest_attempt.resolved_at, ''))=''
              and not exists (
                  select 1
                  from agent_runs as active_run
                  where active_run.reply_task_id=task.id
                    and active_run.execution_generation=task.execution_generation
                    and active_run.status in ('pending', 'running')
              )
              and not exists (
                  select 1
                  from reply_attempts as later_attempt
                  where later_attempt.channel=latest_attempt.channel
                    and later_attempt.conversation_id=latest_attempt.conversation_id
                    and later_attempt.trigger_message_id=latest_attempt.trigger_message_id
                    and later_attempt.id>latest_attempt.id
                    and later_attempt.send_status in (
                        'sent', 'completed', 'skipped', 'needs_human',
                        'reacted', 'commented', 'calendar', 'document'
                    )
              )
              and not exists (
                  select 1
                  from sent_replies as sent
                  where sent.conversation_id=task.conversation_id
                    and sent.trigger_message_id=task.trigger_message_id
              )
              and not exists (
                  select 1
                  from external_action_results as external_result
                  where external_result.business_object_key=task.business_object_key
              )
            order by task.id
            """,
            _LEGACY_AGENT_FAILURES,
        ).fetchall()

        closed = 0
        for row in rows:
            task_cursor = db.execute(
                """
                update reply_tasks
                   set status='done',
                       error='',
                       available_at='',
                       locked_at=null,
                       recovery_code='superseded_by_service_command',
                       updated_at=?
                 where id=? and status='failed'
                """,
                (now_text, int(row["task_id"])),
            )
            if task_cursor.rowcount != 1:
                continue
            resolution = (
                "superseded by current service command "
                f"for scheduled task {int(row['scheduled_task_id'])} "
                f"version {int(row['current_version'])}"
            )
            db.execute(
                """
                update reply_attempts
                   set resolved_at=?, resolution=?, updated_at=?
                 where id=?
                   and send_status='failed'
                   and trim(coalesce(resolved_at, ''))=''
                """,
                (now_text, resolution, now_text, int(row["attempt_id"])),
            )
            closed += 1
        return closed
