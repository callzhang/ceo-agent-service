import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from app.store import AutoReplyStore
from reclassify_unmirrored_todo_outbox import LEGACY_ERROR, reclassify


def _store(tmp_path: Path) -> AutoReplyStore:
    return AutoReplyStore(tmp_path / "task.sqlite3")


def _todo(store: AutoReplyStore, **values) -> int:
    project_id = store.create_work_project(
        title="客户交付",
        category="projects",
        tags_json=json.dumps(["客户交付"], ensure_ascii=False),
        status="active",
        priority="P1",
        risk_level="medium",
        background="客户交付项目需要持续确认验收安排。",
        related_people_json=json.dumps([], ensure_ascii=False),
    )
    defaults = {
        "project_id": project_id,
        "title": "给客户同步验收 ETA",
        "owner_user_id": "owner-1",
        "owner_name": "Alex",
        "status": "open",
        "priority": "P1",
        "deadline_at": "2026-07-01 18:00:00",
    }
    defaults.update(values)
    return store.create_work_todo(**defaults)


def _exhausted_row(store: AutoReplyStore, *, todo_id: int, key: str, error: str):
    store.enqueue_task_todo_sync_outbox(
        operation_key=key, work_todo_id=todo_id, operation="create"
    )
    with store._connect() as db:
        db.execute(
            "update task_todo_sync_outbox set status='failed', attempt_count=3, error=? "
            "where operation_key=?",
            (error, key),
        )
        return int(
            db.execute(
                "select id from task_todo_sync_outbox where operation_key=?", (key,)
            ).fetchone()["id"]
        )


def test_reclassifies_only_the_ineligible_undelivered_rows(tmp_path):
    store = _store(tmp_path)
    ineligible = _exhausted_row(
        store,
        todo_id=_todo(store, deadline_at=""),
        key="ineligible",
        error=LEGACY_ERROR,
    )
    eligible = _exhausted_row(
        store, todo_id=_todo(store), key="eligible", error=LEGACY_ERROR
    )
    other_error = _exhausted_row(
        store,
        todo_id=_todo(store, deadline_at=""),
        key="other-error",
        error="dingtalk_todo_security_check_failed",
    )

    preview, _ = reclassify(store)
    assert preview == [ineligible]

    applied, remaining = reclassify(store, dry_run=False)
    assert applied == [ineligible]
    assert remaining == 2

    with store._connect() as db:
        statuses = {
            int(row["id"]): (str(row["status"]), str(row["error"]))
            for row in db.execute("select id, status, error from task_todo_sync_outbox")
        }
    assert statuses[ineligible][0] == "skipped"
    assert "dingtalk_todo_not_eligible_for_mirror" in statuses[ineligible][1]
    assert LEGACY_ERROR in statuses[ineligible][1]
    assert statuses[eligible][0] == "failed"
    assert statuses[other_error][0] == "failed"


def test_row_with_any_dingtalk_link_is_left_alone(tmp_path):
    store = _store(tmp_path)
    todo_id = _todo(store, deadline_at="")
    row_id = _exhausted_row(
        store, todo_id=todo_id, key="linked", error=LEGACY_ERROR
    )
    store.create_work_todo_dingtalk_link(
        work_todo_id=todo_id,
        dingtalk_task_id="dt-task-1",
        executor_user_id="owner-1",
        title_snapshot="给客户同步验收 ETA",
        deadline_at_snapshot="2026-07-01 18:00:00",
        priority_snapshot="P1",
        status="failed",
    )

    assert reclassify(store)[0] == []
    with store._connect() as db:
        status = db.execute(
            "select status from task_todo_sync_outbox where id=?", (row_id,)
        ).fetchone()["status"]
    assert status == "failed"
