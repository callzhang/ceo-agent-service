from pathlib import Path

import app.audit_web as audit_web_module
from app.store import AutoReplyStore
from tests.support.native_run_rows import insert_scheduled_occurrence


def test_scheduled_service_attention_shows_latest_occurrence_per_command(tmp_path: Path):
    from dataclasses import replace
    from app.agent_cron.models import ScheduledTaskSnapshot
    from app.scheduled_config_storage import intern_scheduled_config

    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task = store.create_scheduled_task(
        name="Scheduled polling", command="sync-minutes-once",
        cron_expression="0 12 * * *", timezone_name="UTC",
    )
    agent = store.create_scheduled_task(
        name="Periodic report", prompt="prepare report", runtime_id="codex_oauth",
        cron_expression="0 12 * * *", timezone_name="UTC",
    )
    base = ScheduledTaskSnapshot.from_task(task)
    report = ScheduledTaskSnapshot.from_task(agent)
    entries = [
        ("poll-old", base),
        ("poll-new-version", replace(base, task_version=base.task_version + 1)),
        ("other-command", replace(base, task_version=base.task_version + 2, command="scan-meetings-once")),
        ("poll-latest", replace(base, task_version=base.task_version + 3)),
        ("report-first", report),
        ("report-second", report),
    ]
    for index, (event, snapshot) in enumerate(entries):
        with store._immediate_write_transaction() as db:
            snapshot_id = intern_scheduled_config(db, snapshot.to_json())
            insert_scheduled_occurrence(db, {
                "event_id": event, "scheduled_task_id": snapshot.task_id,
                "trigger_kind": "manual", "scheduled_for": f"2026-11-0{index + 1}T12:00:00+00:00",
                "dispatch_status": "failed", "snapshot_id": snapshot_id,
            })
        store.record_error(
            f"scheduled-task:{snapshot.task_id}", event,
            "scheduled_task_service_command_failed", event,
        )
    store.record_error(
        f"scheduled-task:{task.id}", "missing-event",
        "scheduled_task_service_command_failed", "unbound error",
    )

    rows = audit_web_module._queue_attention_rows(store)
    shown = {row["summary"] for row in rows if row["category"] == "Scheduled task"}
    assert shown == {"poll-latest", "other-command", "report-first", "report-second", "unbound error"}
    assert len(store.list_errors()) == 7
    assert all(not error.resolved_at for error in store.list_errors())

    with store._immediate_write_transaction() as db:
        snapshot_id = intern_scheduled_config(db, base.to_json())
        insert_scheduled_occurrence(db, {
            "event_id": "poll-success", "scheduled_task_id": task.id,
            "trigger_kind": "manual", "scheduled_for": "2026-11-07T12:00:00+00:00",
            "dispatch_status": "dispatched", "snapshot_id": snapshot_id,
            "execution_kind": "service_command", "execution_id": "sync-minutes-once",
            "dispatched_at": "2026-11-07T12:00:00+00:00",
        })
    shown = {
        row["summary"] for row in audit_web_module._queue_attention_rows(store)
        if row["category"] == "Scheduled task"
    }
    assert shown == {"other-command", "report-first", "report-second", "unbound error"}
    assert all(not error.resolved_at for error in store.list_errors())

