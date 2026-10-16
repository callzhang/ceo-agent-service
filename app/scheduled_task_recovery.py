from __future__ import annotations
from app.run_storage import SCHEDULED_RUN_SQL
from app.task20_definitions import task20_column, task20_projection
from app.task8_storage import task8_set_parts
from app.action_storage import EXTERNAL_ACTION_RESULT_SQL, SENT_REPLY_SQL, REPLY_ATTEMPT_COMMON_SQL, update_action

from datetime import datetime, timezone

from app.store import AutoReplyStore
from app.run_storage import AGENT_RUN_SQL
from app.configs_definitions import config_projection
from app.schedules_definitions import schedule_projection


REPLY_TASK_SQL = task20_projection('reply_tasks')


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
            from ({REPLY_TASK_SQL}) as task
            join ({SCHEDULED_RUN_SQL}) as run
              on run.event_id=task.trigger_message_id
             and run.execution_kind='reply_task'
             and run.execution_id=cast(task.id as text)
             and run.dispatch_status='dispatched'
            join ({config_projection('scheduled_task_config_versions')}) as config on config.id=run.snapshot_id
            join ({schedule_projection()}) as current_task
              on current_task.id=run.scheduled_task_id
            join ({REPLY_ATTEMPT_COMMON_SQL}) as latest_attempt
              on latest_attempt.channel=task.channel
             and latest_attempt.conversation_id=task.conversation_id
             and latest_attempt.trigger_message_id=task.trigger_message_id
             and latest_attempt.id=(
                     select max(candidate.id)
                     from ({REPLY_ATTEMPT_COMMON_SQL}) as candidate
                     where candidate.channel=task.channel
                       and candidate.conversation_id=task.conversation_id
                       and candidate.trigger_message_id=task.trigger_message_id
                 )
            where task.channel='scheduled'
              and task.status='failed'
              and task.error in ({placeholders})
              and current_task.deleted_at is null
              and trim(current_task.command)<>''
              and json_valid(config.snapshot_json)
              and json_type(config.snapshot_json)='object'
              and trim(coalesce(json_extract(config.snapshot_json, '$.command'), ''))=''
              and cast(json_extract(config.snapshot_json, '$.task_version') as integer)
                  < current_task.version
              and latest_attempt.send_status='failed'
              and trim(coalesce(latest_attempt.resolved_at, ''))=''
              and not exists (
                  select 1
                  from ({AGENT_RUN_SQL}) as active_run
                  where active_run.reply_task_id=task.id
                    and active_run.execution_generation=task.execution_generation
                    and active_run.status in ('pending', 'running')
              )
              and not exists (
                  select 1
                  from ({REPLY_ATTEMPT_COMMON_SQL}) as later_attempt
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
                  from ({SENT_REPLY_SQL}) as sent
                  where sent.conversation_id=task.conversation_id
                    and sent.trigger_message_id=task.trigger_message_id
              )
              and not exists (
                  select 1
                  from ({EXTERNAL_ACTION_RESULT_SQL}) as external_result
                  where external_result.business_object_key=task.business_object_key
              )
            order by task.id
            """,
            _LEGACY_AGENT_FAILURES,
        ).fetchall()

        closed = 0
        for row in rows:
            task_cursor = db.execute(
                f"""
                update tasks
                   set {task8_set_parts('reply_tasks', {}, update_sql={'status': "'done'", 'error': "''", 'available_at': "''", 'locked_at': 'null', 'recovery_code': "'superseded_by_service_command'", 'updated_at': '?'}).sql}
                 where record_type='reply_tasks' and id=? and {task20_column('reply_tasks', 'status')}='failed'
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
            update_action(
                db, "reply_attempts", int(row["attempt_id"]),
                {"resolved_at": now_text, "resolution": resolution, "updated_at": now_text},
                where_sql="send_status='failed' and trim(coalesce(resolved_at, ''))=''",
            )
            closed += 1
        return closed
