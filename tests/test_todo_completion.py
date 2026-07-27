import json
from types import SimpleNamespace
from pathlib import Path

from app.store import AutoReplyStore
from app.todo_completion import (
    close_todo_with_completion_evidence,
    enqueue_follow_up_completion_checks,
)


def _store(tmp_path: Path) -> AutoReplyStore:
    return AutoReplyStore(tmp_path / "task.sqlite3")


def _project_todo_follow_up(store: AutoReplyStore):
    project_id = store.create_work_project(
        title="客户交付",
        category="projects",
        status="active",
        priority="P1",
        risk_level="medium",
    )
    todo_id = store.create_work_todo(
        project_id=project_id,
        title="给客户同步验收 ETA",
        description="客户需要验收 ETA 和下一步安排。",
        owner_user_id="owner-1",
        owner_name="Alex",
        status="open",
        priority="P1",
    )
    follow_up_id = store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id="owner-1",
        owner_name="Alex",
        target_conversation_id="cid-1",
        target_kind="group",
        question_text="基于客户交付项目，请确认验收 ETA 是否已同步。",
        scheduled_at="2026-06-27 09:00:00",
        status="sent",
        sent_at="2026-06-27 09:05:00",
    )
    return project_id, todo_id, follow_up_id


def test_close_todo_completion_evidence_completes_bound_follow_ups(tmp_path):
    store = _store(tmp_path)
    _, todo_id, follow_up_id = _project_todo_follow_up(store)

    closed = close_todo_with_completion_evidence(
        store,
        todo_id=todo_id,
        evidence={
            "source": "reply_attempt:7",
            "reason": "Alex 明确回复验收 ETA 已同步。",
            "completed_at": "2026-06-27 10:00:00",
        },
        now="2026-06-27 10:00:00",
        source_type="reply_attempt",
        source_ref="7",
        merge_reason="reply_completion_evidence",
    )

    assert closed is True
    todo = store.get_work_todo(todo_id)
    assert todo is not None
    assert todo.status == "done"
    evidence = json.loads(todo.completion_evidence_json)
    assert evidence["source"] == "reply_attempt:7"
    assert evidence["reason"] == "Alex 明确回复验收 ETA 已同步。"
    follow_up = store.get_follow_up_draft(follow_up_id)
    assert follow_up is not None
    assert follow_up.status == "completed"
    check = json.loads(follow_up.evidence_check_json)
    assert check["source"] == "reply_attempt:7"
    assert check["reason"] == "Alex 明确回复验收 ETA 已同步。"


def test_completion_check_enqueues_one_evidence_work_item(tmp_path):
    store = _store(tmp_path)
    _, first_todo_id, first_follow_up_id = _project_todo_follow_up(store)
    second_todo_id = store.create_work_todo(
        project_id=1,
        title="归档客户验收材料",
        owner_user_id="owner-2",
        owner_name="Blair",
        status="open",
        priority="P1",
    )
    second_follow_up_id = store.create_follow_up_draft(
        project_id=1,
        todo_id=second_todo_id,
        owner_user_id="owner-2",
        owner_name="Blair",
        target_conversation_id="cid-1",
        target_kind="group",
        question_text="请确认验收材料是否已归档。",
        scheduled_at="2026-06-27 09:10:00",
        status="sent",
        sent_at="2026-06-27 09:15:00",
    )

    class FakeDws:
        def search_messages(self, keyword, start, end, limit, cursor="0"):
            return [
                SimpleNamespace(
                    open_message_id="msg-1",
                    open_conversation_id="cid-1",
                    conversation_title="客户交付群",
                    sender_name="Alex",
                    create_time="2026-06-27 10:00:00",
                    content="验收 ETA 已同步给客户，请看群里确认。",
                )
            ]

        def list_minutes(self, *, scope="all", limit=20, cursor="", start="", end=""):
            return []

    checked = enqueue_follow_up_completion_checks(
        store,
        FakeDws(),
        now="2026-06-28 02:00:00",
        limit=1,
    )

    assert checked == 1
    first_follow_up = store.get_follow_up_draft(first_follow_up_id)
    second_follow_up = store.get_follow_up_draft(second_follow_up_id)
    assert first_follow_up is not None
    assert second_follow_up is not None
    assert "completion_check_checked_at" in first_follow_up.evidence_check_json
    assert second_follow_up.evidence_check_json == "{}"
    with store._connect() as db:
        rows = db.execute("select source_type, source_ref, payload_json from work_summary_inputs").fetchall()
    assert len(rows) == 1
    assert rows[0]["source_type"] == "follow_up_completion_check"
    assert rows[0]["source_ref"] == f"follow-up:{first_follow_up_id}:2026-06-28"
    assert "dws_message:msg-1" in rows[0]["payload_json"]
    assert store.get_work_todo(first_todo_id).status == "open"
