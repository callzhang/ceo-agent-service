from datetime import UTC, datetime, timedelta

import pytest

from app.agent_cron.scheduler import PREVIOUS_EXECUTION_ACTIVE, RUNTIME_UNAVAILABLE
from app.agent_cron.seeds import REPORT_SCHEDULE_MIGRATION_KEYS, WEEKLY_REPORT_MIGRATION_KEY
from app.report_schedule_attention import report_schedule_attention_rows
from app.store import AutoReplyStore

START = datetime(2026, 10, 24, 19, 0, tzinfo=UTC)


@pytest.fixture
def store(tmp_path):
    return AutoReplyStore(tmp_path / "report-schedule-attention.sqlite3")


def _schedule(store, *, key=WEEKLY_REPORT_MIGRATION_KEY, enabled=True, name="准备 CEO 管理周报"):
    return store.create_scheduled_task(
        name=name, prompt="p", cron_expression="0 0 12 * * 6",
        timezone_name="UTC", runtime_id="codex_oauth", migration_key=key,
        enabled=enabled, now=START,
    )


def _run(store, task, *, minutes, trigger="scheduled", status, reason="", task_status=None):
    at = START + timedelta(minutes=minutes)
    run = store.create_scheduled_task_run(
        task.id, trigger_kind=trigger, scheduled_for=at, now=at,
    )
    execution_id = ""
    if task_status is not None:
        reply = store.ensure_reply_task(
            conversation_id=f"scheduled-task-run:{run.id}", conversation_title=task.name,
            single_chat=False, trigger_message_id=f"run-{run.id}",
            trigger_create_time="2026-10-24 19:00:00", trigger_sender="scheduler",
            trigger_text="report", business_object_key=f"scheduled:{run.id}",
        )
        with store._connect() as db:
            db.execute("update tasks set status=? where id=?", (task_status, reply.id))
        execution_id = str(reply.id)
    with store._connect() as db:
        db.execute(
            "update schedule_occurrences set dispatch_status=?,skip_or_error_reason=?,"
            "execution_kind=?,execution_id=? where id=?",
            (status, reason, "reply_task" if execution_id else "", execution_id, run.id),
        )
    return run


def test_only_the_three_report_schedules_are_declared():
    assert len(REPORT_SCHEDULE_MIGRATION_KEYS) == 3


def test_a_planned_run_skipped_before_any_task_exists_is_shown(store):
    task = _schedule(store)
    _run(store, task, minutes=0, status="skipped", reason=RUNTIME_UNAVAILABLE)
    [row] = report_schedule_attention_rows(store)
    assert row["category"] == "Scheduled task"
    assert row["context"] == "准备 CEO 管理周报"
    assert row["error"] == RUNTIME_UNAVAILABLE
    assert row["detail_url"] == f"/scheduled-tasks?id={task.id}"


def test_a_task_that_ended_skipped_is_shown(store):
    task = _schedule(store)
    _run(store, task, minutes=0, status="dispatched", task_status="skipped")
    assert len(report_schedule_attention_rows(store)) == 1


@pytest.mark.parametrize("task_status", ["done", "failed", "processing"])
def test_done_failed_and_running_tasks_add_no_row(store, task_status):
    # A failed task has its own Reply task row; done delivered; processing is in flight.
    _run(store, _schedule(store), minutes=0, status="dispatched", task_status=task_status)
    assert report_schedule_attention_rows(store) == []


def test_waiting_for_the_previous_run_is_not_a_miss(store):
    _run(store, _schedule(store), minutes=0, status="skipped", reason=PREVIOUS_EXECUTION_ACTIVE)
    assert report_schedule_attention_rows(store) == []


def test_a_later_successful_run_clears_the_row(store):
    task = _schedule(store)
    _run(store, task, minutes=0, status="skipped", reason=RUNTIME_UNAVAILABLE)
    assert len(report_schedule_attention_rows(store)) == 1
    _run(store, task, minutes=30, trigger="manual", status="dispatched", task_status="done")
    assert report_schedule_attention_rows(store) == []


def test_a_later_failed_run_does_not_clear_the_row(store):
    task = _schedule(store)
    _run(store, task, minutes=0, status="skipped", reason=RUNTIME_UNAVAILABLE)
    _run(store, task, minutes=30, trigger="manual", status="dispatched", task_status="failed")
    assert len(report_schedule_attention_rows(store)) == 1


def test_only_the_latest_planned_run_counts(store):
    task = _schedule(store)
    _run(store, task, minutes=0, status="skipped", reason=RUNTIME_UNAVAILABLE)
    _run(store, task, minutes=60 * 24 * 7, status="dispatched", task_status="done")
    assert report_schedule_attention_rows(store) == []


def test_paused_and_other_schedules_are_ignored(store):
    paused = _schedule(store, enabled=False)
    other = _schedule(store, key="email-message-check-v1", name="分类新邮件")
    for task in (paused, other):
        _run(store, task, minutes=0, status="skipped", reason=RUNTIME_UNAVAILABLE)
    assert report_schedule_attention_rows(store) == []
