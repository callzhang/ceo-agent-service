from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.agent_cron.models import ScheduledTaskSkillRef
from app.scheduled_task_recovery import close_superseded_scheduled_reply_tasks
from app.store import AutoReplyStore


UTC = timezone.utc
NOW = datetime(2026, 9, 8, 16, 0, tzinfo=UTC)


def _managed_ref(store: AutoReplyStore) -> ScheduledTaskSkillRef:
    skill = store.create_managed_skill("ceo-weekly-report", "Weekly OKR report")
    revision = store.create_managed_skill_revision(
        skill.id,
        "---\n"
        "name: ceo-weekly-report\n"
        "description: Test\n"
        "metadata:\n"
        "  managed_by: ceo-agent-service\n"
        "---\n\n# Test\n",
        source="settings",
    )
    return ScheduledTaskSkillRef(
        skill_source="managed",
        skill_name=skill.name,
        managed_skill_id=skill.id,
        managed_revision_id=revision.id,
        position=0,
    )


def test_failed_agent_task_is_closed_after_service_command_migration(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "superseded-scheduled-reply.sqlite3")
    legacy = store.create_scheduled_task(
        migration_key="weekly-okr-report-sunday-v1",
        name="Sunday OKR report",
        prompt="$ceo-weekly-report",
        cron_expression="0 0 18 * * 0",
        timezone_name="Asia/Shanghai",
        runtime_id="codex_oauth",
        runtime_options={"model": "gpt-5.5"},
        working_directory="/tmp/ceo-agent-service",
        skill_refs=(_managed_ref(store),),
        now=NOW,
    )
    scheduled_run = store.create_scheduled_task_run(
        legacy.id,
        trigger_kind="manual",
        scheduled_for=NOW,
        now=NOW,
        event_id="legacy-weekly-run",
    )
    current = store.adopt_scheduled_task_service_command(
        migration_key="weekly-okr-report-sunday-v1",
        command="weekly-okr-report",
        seed_enabled=True,
        now=NOW + timedelta(minutes=1),
    )
    assert current is not None

    store.enqueue_reply_task(
        channel="scheduled",
        conversation_id=f"scheduled-task-run:{scheduled_run.id}",
        conversation_title="Sunday OKR report",
        single_chat=False,
        trigger_message_id=scheduled_run.event_id,
        trigger_create_time=scheduled_run.scheduled_for.isoformat(),
        trigger_sender="Agent Cron",
        trigger_text="$ceo-weekly-report",
        trigger_message_json='{"schema":"scheduled_agent_execution.v1"}',
        execution_generation=f"scheduled-run-{scheduled_run.id}",
        business_object_key=f"scheduled-task-run:{scheduled_run.id}",
    )
    attempt_id = store.record_reply_attempt(
        channel="scheduled",
        conversation_id=f"scheduled-task-run:{scheduled_run.id}",
        conversation_title="Sunday OKR report",
        trigger_message_id=scheduled_run.event_id,
        trigger_sender="Agent Cron",
        trigger_text="$ceo-weekly-report",
        action="agent_run",
        sensitivity_kind="general",
        send_status="failed",
    )
    with store._connect() as db:
        db.execute(
            "update reply_tasks set status='failed', error='codex_process_failed' "
            "where trigger_message_id=?",
            (scheduled_run.event_id,),
        )
        task_id = db.execute(
            "select id from reply_tasks where trigger_message_id=?",
            (scheduled_run.event_id,),
        ).fetchone()["id"]
        db.execute(
            "update scheduled_task_runs set dispatch_status='dispatched', "
            "execution_kind='reply_task', execution_id=? where id=?",
            (str(task_id), scheduled_run.id),
        )

    assert close_superseded_scheduled_reply_tasks(
        store,
        now=NOW + timedelta(minutes=2),
    ) == 1
    assert close_superseded_scheduled_reply_tasks(
        store,
        now=NOW + timedelta(minutes=3),
    ) == 0
    with store._connect() as db:
        task_row = db.execute(
            "select status, error, recovery_code from reply_tasks "
            "where trigger_message_id=?",
            (scheduled_run.event_id,),
        ).fetchone()
        attempt_row = db.execute(
            "select send_status, resolved_at, resolution from reply_attempts where id=?",
            (attempt_id,),
        ).fetchone()
    assert tuple(task_row) == (
        "done",
        "",
        "superseded_by_service_command",
    )
    assert attempt_row["send_status"] == "failed"
    assert attempt_row["resolved_at"] == "2026-09-08 16:02:00"
    assert "current service command" in attempt_row["resolution"]
