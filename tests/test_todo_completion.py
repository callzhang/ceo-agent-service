import json
import os
from datetime import datetime, timezone
from types import SimpleNamespace
from pathlib import Path

from app.store import AutoReplyStore
from app.todo_completion import (
    close_todo_with_completion_evidence,
    enqueue_follow_up_completion_checks,
    enqueue_todo_completion_evidence_checks,
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
        send_result_json=json.dumps({"message_id": "msg-1"}, ensure_ascii=False),
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
    assert json.loads(follow_up.send_result_json) == {"message_id": "msg-1"}
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
        rows = db.execute(
            "select source_type, source_ref, payload_json from work_summary_inputs"
        ).fetchall()
    assert len(rows) == 1
    assert rows[0]["source_type"] == "todo_completion_check"
    assert rows[0]["source_ref"].startswith("todo-completion-check:")
    assert "dws_message:msg-1" not in rows[0]["payload_json"]
    payload = json.loads(rows[0]["payload_json"])
    summary = json.loads(payload["summary"])
    assert summary["search_policy"]["limits"]["max_tool_calls"] == 8
    assert "dws_message" in summary["search_policy"]["allowed_sources"]
    assert store.get_work_todo(first_todo_id).status == "open"


def test_todo_completion_scanner_enqueues_open_todo_without_follow_up(tmp_path):
    store = _store(tmp_path)
    project_id = store.create_work_project(
        title="技术部招聘",
        category="recruiting",
        status="active",
        priority="P1",
        risk_level="medium",
    )
    todo_id = store.create_work_todo(
        project_id=project_id,
        title="完成候选人 Colin 终面反馈",
        description="需要确认候选人评估是否已经同步。",
        owner_user_id="owner-1",
        owner_name="Mina",
        status="open",
        priority="P1",
    )

    class FakeDws:
        def __init__(self):
            self.search_calls = 0

        def search_messages(self, keyword, start, end, limit, cursor="0"):
            self.search_calls += 1
            return []

        def list_minutes(self, *, scope="all", limit=20, cursor="", start="", end=""):
            return []

    dws = FakeDws()
    checked = enqueue_todo_completion_evidence_checks(
        store,
        dws,
        workspace=None,
        now="2026-06-28 12:00:00",
        limit=50,
    )

    assert checked == 1
    candidates = store.list_todo_evidence_candidates(todo_id=todo_id)
    assert candidates == []
    assert dws.search_calls == 0
    inputs = store.claim_work_summary_inputs(limit=5)
    assert len(inputs) == 1
    assert inputs[0].source_type == "todo_completion_check"
    payload = json.loads(inputs[0].payload_json)
    summary = json.loads(payload["summary"])
    assert summary["todo"]["id"] == todo_id
    assert summary["search_policy"]["time_window"]["default_days"] == 14
    assert "dws_minutes" in summary["search_policy"]["allowed_sources"]


def test_todo_completion_scanner_collects_minutes_and_workspace_candidates(tmp_path):
    store = _store(tmp_path)
    project_id = store.create_work_project(
        title="客户验收",
        category="projects",
        status="active",
        priority="P2",
        risk_level="low",
    )
    todo_id = store.create_work_todo(
        project_id=project_id,
        title="归档验收材料",
        description="客户验收材料需要归档。",
        owner_user_id="owner-2",
        owner_name="Blair",
        status="open",
        priority="P2",
    )
    workspace = tmp_path / "CEO_WORKSPACE"
    workspace.mkdir()
    (workspace / "acceptance.md").write_text(
        "无关的会议准备内容。\n客户验收 归档验收材料 已经上传到项目文件夹。\n其他归档规范。",
        encoding="utf-8",
    )

    class FakeDws:
        def search_messages(self, keyword, start, end, limit, cursor="0"):
            return []

        def list_minutes(self, *, scope="all", limit=20, cursor="", start="", end=""):
            return [
                {
                    "taskUuid": "minutes-1",
                    "title": "客户验收例会",
                    "summary": "先讨论客户验收后续沟通。\n归档验收材料已经完成。\n再讨论下周例会安排。",
                    "actionItems": [{"text": "检查客户验收归档结果"}],
                    "createdAt": "2026-06-28 09:00:00",
                }
            ]

    checked = enqueue_todo_completion_evidence_checks(
        store,
        FakeDws(),
        workspace=workspace,
        now="2026-06-28 12:00:00",
        limit=50,
    )

    candidates = store.list_todo_evidence_candidates(todo_id=todo_id)
    assert checked == 1
    assert candidates == []
    inputs = store.claim_work_summary_inputs(limit=10)
    assert len(inputs) == 1
    payloads = [
        json.loads(item.payload_json)
        for item in inputs
    ]
    joined_payloads = json.dumps(payloads, ensure_ascii=False)
    assert "无关的会议准备内容" not in joined_payloads
    assert "下周例会安排" not in joined_payloads
    summary = json.loads(payloads[0]["summary"])
    assert summary["search_policy"]["local_files"]["root"] == "CEO_WORKSPACE"


def test_todo_completion_scanner_only_reads_workspace_files_changed_since_last_check(
    tmp_path,
):
    store = _store(tmp_path)
    project_id = store.create_work_project(
        title="客户验收",
        category="projects",
        status="active",
        priority="P1",
        risk_level="medium",
    )
    todo_id = store.create_work_todo(
        project_id=project_id,
        title="确认验收报告",
        description="客户验收报告需要确认完成。",
        owner_user_id="owner-1",
        owner_name="Alex",
        status="open",
        priority="P1",
    )
    store.set_daily_scan_state(
        "todo_completion_evidence",
        last_success_at="2026-06-28 11:00:00",
        cursor_json=json.dumps({"last_checked_at": "2026-06-28 11:00:00"}),
    )
    workspace = tmp_path / "CEO_WORKSPACE"
    workspace.mkdir()
    old_file = workspace / "old-report.md"
    old_file.write_text(
        "客户验收 确认验收报告 已经完成，但这是上次检测前的旧文件。",
        encoding="utf-8",
    )
    new_file = workspace / "new-report.md"
    new_file.write_text(
        "客户验收 确认验收报告 已经完成，这是上次检测后的新文件。",
        encoding="utf-8",
    )
    old_ts = datetime(2026, 6, 28, 10, 30, tzinfo=timezone.utc).timestamp()
    new_ts = datetime(2026, 6, 28, 11, 30, tzinfo=timezone.utc).timestamp()
    os.utime(old_file, (old_ts, old_ts))
    os.utime(new_file, (new_ts, new_ts))

    class FakeDws:
        def search_messages(self, keyword, start, end, limit, cursor="0"):
            return []

        def list_minutes(self, *, scope="all", limit=20, cursor="", start="", end=""):
            return []

    checked = enqueue_todo_completion_evidence_checks(
        store,
        FakeDws(),
        workspace=workspace,
        now="2026-06-28 12:00:00",
        limit=50,
    )

    candidates = store.list_todo_evidence_candidates(todo_id=todo_id)
    assert checked == 1
    assert candidates == []
    inputs = store.claim_work_summary_inputs(limit=10)
    assert len(inputs) == 1
    payload = json.loads(inputs[0].payload_json)
    summary = json.loads(payload["summary"])
    assert summary["search_policy"]["time_window"]["changed_files_since"] == (
        "2026-06-28 11:00:00"
    )


def test_todo_completion_scanner_delegates_unstructured_search_to_agent(tmp_path):
    store = _store(tmp_path)
    project_id = store.create_work_project(
        title="客户验收",
        category="projects",
        status="active",
        priority="P1",
        risk_level="medium",
    )
    todo_id = store.create_work_todo(
        project_id=project_id,
        title="确认验收报告",
        description="客户验收报告需要确认完成。",
        owner_user_id="owner-1",
        owner_name="Alex",
        status="open",
        priority="P1",
    )
    store.set_daily_scan_state(
        "todo_completion_evidence",
        last_success_at="2026-06-28 11:00:00",
        cursor_json=json.dumps({"last_checked_at": "2026-06-28 11:00:00"}),
    )
    workspace = tmp_path / "CEO_WORKSPACE"
    workspace.mkdir()
    (workspace / "new-report.md").write_text(
        "客户验收 确认验收报告 已经完成，这是上次检测后的新文件。",
        encoding="utf-8",
    )

    class FakeDws:
        search_calls = 0
        minutes_calls = 0

        def search_messages(self, keyword, start, end, limit, cursor="0"):
            self.search_calls += 1
            return []

        def list_minutes(self, *, scope="all", limit=20, cursor="", start="", end=""):
            self.minutes_calls += 1
            return []

    dws = FakeDws()
    checked = enqueue_todo_completion_evidence_checks(
        store,
        dws,
        workspace=workspace,
        now="2026-06-28 12:00:00",
        limit=50,
    )

    assert checked == 1
    assert dws.search_calls == 0
    assert dws.minutes_calls == 0
    assert store.list_todo_evidence_candidates(todo_id=todo_id) == []
    inputs = store.claim_work_summary_inputs(limit=10)
    assert len(inputs) == 1
    assert inputs[0].source_type == "todo_completion_check"
    payload = json.loads(inputs[0].payload_json)
    summary = json.loads(payload["summary"])
    assert summary["todo"]["id"] == todo_id
    assert summary["search_policy"]["time_window"]["changed_files_since"] == (
        "2026-06-28 11:00:00"
    )
    assert summary["search_policy"]["limits"]["max_tool_calls"] == 8
    assert summary["search_policy"]["limits"]["max_raw_reads"] == 3
    assert summary["search_policy"]["limits"]["max_sources_to_return"] == 3
    assert summary["search_policy"]["allowed_sources"] == [
        "dws_message",
        "dws_minutes",
        "lark_message",
        "lark_doc",
        "lark_task",
        "email",
        "local_file_under_CEO_WORKSPACE",
        "memory_recall_for_background_only",
    ]


def test_todo_completion_scanner_keeps_structured_dingtalk_done_candidate(tmp_path):
    store = _store(tmp_path)
    project_id = store.create_work_project(
        title="客户验收",
        category="projects",
        status="active",
        priority="P1",
        risk_level="medium",
    )
    todo_id = store.create_work_todo(
        project_id=project_id,
        title="确认验收报告",
        description="客户验收报告需要确认完成。",
        owner_user_id="owner-1",
        owner_name="Alex",
        status="open",
        priority="P1",
    )
    store.create_work_todo_dingtalk_link(
        work_todo_id=todo_id,
        dingtalk_task_id="task-1",
        status="active",
        last_dingtalk_done=True,
        last_pull_at="2026-06-28 11:30:00",
        last_dingtalk_payload_json=json.dumps({"done": True}, ensure_ascii=False),
    )

    class FakeDws:
        def search_messages(self, keyword, start, end, limit, cursor="0"):
            raise AssertionError("scanner must not search unstructured DWS messages")

        def list_minutes(self, *, scope="all", limit=20, cursor="", start="", end=""):
            raise AssertionError("scanner must not search AI minutes")

    checked = enqueue_todo_completion_evidence_checks(
        store,
        FakeDws(),
        workspace=None,
        now="2026-06-28 12:00:00",
        limit=50,
    )

    candidates = store.list_todo_evidence_candidates(todo_id=todo_id)
    assert checked == 1
    assert len(candidates) == 1
    assert candidates[0].source_type == "dingtalk_todo"
    assert candidates[0].source_ref == "dingtalk_todo:task-1"
    inputs = store.claim_work_summary_inputs(limit=10)
    assert len(inputs) == 1
    assert inputs[0].source_type == "todo_completion_evidence_candidate"


def test_todo_completion_scanner_ignores_done_todo(tmp_path):
    store = _store(tmp_path)
    project_id = store.create_work_project(
        title="客户验收",
        category="projects",
        status="active",
        priority="P1",
        risk_level="medium",
    )
    todo_id = store.create_work_todo(
        project_id=project_id,
        title="已完成事项",
        status="done",
        priority="P1",
    )

    class FakeDws:
        def search_messages(self, keyword, start, end, limit, cursor="0"):
            return [
                SimpleNamespace(
                    open_message_id="msg-done",
                    open_conversation_id="cid-1",
                    conversation_title="客户验收",
                    sender_name="Alex",
                    create_time="2026-06-28 10:00:00",
                    content="已完成事项已经完成。",
                )
            ]

    checked = enqueue_todo_completion_evidence_checks(
        store,
        FakeDws(),
        workspace=None,
        now="2026-06-28 12:00:00",
        limit=50,
    )

    assert checked == 0
    assert store.list_todo_evidence_candidates(todo_id=todo_id) == []
