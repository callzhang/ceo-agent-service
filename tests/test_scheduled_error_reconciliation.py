from pathlib import Path

import pytest

from app import task_entity_storage
from app.action_storage import error_set_parts
from app.agent_cron.models import ScheduledTaskSnapshot
from app.scheduled_config_storage import intern_scheduled_config
from app.store import AutoReplyStore, REPLY_TASK_SQL
from tests.support.native_run_rows import insert_scheduled_occurrence


@pytest.mark.parametrize(
    ("task_status", "expected_resolutions"),
    [("done", 1), ("skipped", 1), ("failed", 0), ("processing", 0)],
)
def test_same_occurrence_terminal_task_covers_late_scheduled_error(
    tmp_path: Path, task_status: str, expected_resolutions: int
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    scheduled_task = store.create_scheduled_task(
        name="Sync meeting notes",
        prompt="$ceo-minutes-sync",
        cron_expression="* * * * *",
        timezone_name="UTC",
        runtime_id="codex_api",
    )
    event_id = "same-scheduled-occurrence"
    assert store.enqueue_reply_task(
        conversation_id=f"scheduled-task:{scheduled_task.id}",
        conversation_title="Sync meeting notes",
        single_chat=True,
        trigger_message_id=event_id,
        trigger_create_time="2026-09-18T12:00:00Z",
        trigger_sender="scheduler",
        trigger_text="sync",
        channel="scheduled",
    )
    with store._immediate_write_transaction() as db:
        snapshot_id = intern_scheduled_config(
            db, ScheduledTaskSnapshot.from_task(scheduled_task).to_json()
        )
        reply_task = db.execute(
            f"select id from ({REPLY_TASK_SQL}) where trigger_message_id=?",
            (event_id,),
        ).fetchone()
        assert reply_task is not None
        reply_task_id = int(reply_task[0])
        updated_task = task_entity_storage.update_task_cas(
            db,
            task_id=reply_task_id,
            expected={"status": "pending"},
            changes={"status": task_status, "updated_at": "2026-09-18 12:02:00"},
        )
        assert updated_task is not None
        insert_scheduled_occurrence(
            db,
            {
                "event_id": event_id,
                "scheduled_task_id": scheduled_task.id,
                "trigger_kind": "scheduled",
                "scheduled_for": "2026-09-18T12:00:00Z",
                "first_scheduled_for": "2026-09-18T12:00:00Z",
                "dispatch_status": "dispatched",
                "snapshot_id": snapshot_id,
                "execution_kind": "reply_task",
                "execution_id": str(reply_task_id),
                "dispatched_at": "2026-09-18 12:00:32",
            },
        )
    store.record_error(
        f"scheduled-task:{scheduled_task.id}",
        event_id,
        "scheduled_task_execution_unavailable",
        "runtime route unavailable",
    )
    with store._connect() as db:
        parts, parameters = error_set_parts({"created_at": "2026-09-18 12:02:02"})
        db.execute(
            f"update actions set {parts} where record_type='errors' and error_message_id=?",
            (*parameters, event_id),
        )

    assert (
        store.resolve_errors_recovered_by_scheduled_reply_task()
        == expected_resolutions
    )
    errors = store.list_errors()
    assert len(errors) == 1
    if expected_resolutions:
        assert errors[0].resolved_at
        assert errors[0].resolution == "covered by terminal scheduled reply task"
    else:
        assert errors[0].resolved_at == ""
