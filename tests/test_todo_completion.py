import json
import os
from datetime import datetime, timezone
from types import SimpleNamespace
from pathlib import Path

import pytest

from app.store import AutoReplyStore
from app.task_attention_projection import AttentionProposal, BusinessAttentionProjection
from app.task_models import WorkItem
from app.task_semantic_models import AttentionCategory
from app.todo_completion import complete_business_task_from_external_todo
from app.skill_features import FeatureRegistry
from app.todo_completion import (
    close_todo_with_completion_evidence,
    enqueue_follow_up_completion_checks,
    enqueue_todo_completion_evidence_checks,
)
from app.task_completion_agent import TaskCompletionDecision, process_task_completion_work_item


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


def test_disabled_work_tracking_does_not_enqueue_todo_completion_checks(tmp_path):
    store = _store(tmp_path)
    _project_todo_follow_up(store)
    registry = FeatureRegistry(state_path=tmp_path / "skill-state.json")
    registry.set_enabled("work_tracking", False)

    class Dws:
        def search_messages(self, *args, **kwargs):
            raise AssertionError("disabled scanner must not search messages")

        def list_minutes(self, *args, **kwargs):
            raise AssertionError("disabled scanner must not read AI minutes")

    assert (
        enqueue_todo_completion_evidence_checks(
            store,
            Dws(),
            now="2026-06-28 12:00:00",
            feature_registry=registry,
        )
        == 0
    )
    assert store.claim_work_summary_inputs(limit=1) == []


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
        owner_name="Avery",
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
        "dingtalk_todo",
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
    payload = json.loads(inputs[0].payload_json)
    summary = json.loads(payload["summary"])
    assert summary["search_policy"]["allowed_sources"]
    assert "dingtalk_todo" in summary["search_policy"]["allowed_sources"]

    from app.task_models import WorkItem

    work_item = WorkItem.model_validate_json(inputs[0].payload_json)
    candidate = candidates[0]
    assert work_item.source.ref == f"todo-evidence:{candidate.id}"
    assert summary["evidence_candidate"]["id"] == candidate.id
    decision = TaskCompletionDecision.model_validate(
        {
            "search_trace": [
                {
                    "source_kind": candidate.source_type,
                    "source_ref": candidate.source_ref,
                    "source_created_at": candidate.source_created_at,
                    "result": "The linked DingTalk TODO has a completion status candidate.",
                    "reason": "Read the current structured TODO status.",
                }
            ],
            "update_summary": "The status alone does not establish the TODO's completion condition.",
        }
    )

    class CompletionRunner:
        def decide(self, **kwargs):
            return decision

        last_audit_tool_events = [
            {
                "tool": "dingtalk_todo_get",
                "call_id": "current-read",
                "output": candidate.source_ref,
            }
        ]

    process_task_completion_work_item(store, CompletionRunner(), inputs[0], now="2026-06-28 12:00:00")
    assert store.get_work_todo(todo_id).status == "open"
    assert store.get_todo_evidence_candidate(candidate.id).status.value == "rejected"


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
def test_external_dingtalk_completion_closes_only_the_linked_business_task(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    first = store.create_business_task(
        title="客户验收", stage="formal", formal_basis="explicit_assignment",
        commitment_status="accepted", owner_user_id="alex", owner_name="Alex",
    )
    sibling = store.create_business_task(
        title="客户合同", stage="formal", formal_basis="explicit_assignment",
        commitment_status="accepted", owner_user_id="alex", owner_name="Alex",
    )
    store.create_business_task_dingtalk_link(
        business_task_id=first, dingtalk_task_id="dt-1", status="active"
    )

    assert complete_business_task_from_external_todo(
        store, business_task_id=first,
        evidence={"source": "dingtalk_todo:dt-1", "reason": "DingTalk Todo marked done"},
    )

    assert store.get_business_task(first).status.value == "done"
    assert store.get_business_task(sibling).status.value == "open"
    with store._connect() as db:
        event = db.execute("select after_json from business_task_events where task_id=?", (first,)).fetchone()
        signal = db.execute(
            "select s.source_ref from business_task_signals s "
            "join business_task_evidence e on e.signal_id=s.id "
            "where e.task_id=? and e.evidence_role='completion'", (first,)
        ).fetchone()
    assert 'dingtalk_todo:dt-1' in event["after_json"]
    assert signal["source_ref"] == "dingtalk_todo:dt-1"


def test_external_todo_completion_rejects_unlinked_task(tmp_path):
    store = _store(tmp_path)
    task_id = store.create_business_task(title="Unlinked", stage="formal", formal_basis="explicit_assignment", owner_user_id="alex", owner_name="Alex")

    assert not complete_business_task_from_external_todo(
        store, business_task_id=task_id,
        evidence={"source": "dingtalk_todo:dt-1", "reason": "done"},
    )
    assert store.get_business_task(task_id).status.value == "open"


def _business_task_for_completion(store, *, title="客户验收", with_follow_up=False):
    task_id = store.create_business_task(
        title=title, stage="formal", formal_basis="explicit_assignment",
        owner_user_id="alex", owner_name="Alex", business_relevance="relevant",
    )
    with store.business_task_transaction() as db:
        signal_id = store.create_business_task_signal_in_transaction(
            source_type="reply_attempt", source_ref=f"message:assigned:{task_id}",
            source_time="2026-09-23T09:00:00Z", conversation_id="cid-customer",
            evidence_text=f"Alex 负责{title}，2026-09-24 10:00 检查进展。",
            dedupe_key=f"assignment:{task_id}", _db=db,
        )
        store.link_business_task_evidence_in_transaction(
            task_id=task_id, signal_id=signal_id, evidence_role="assignment", _db=db,
        )
        db.execute(
            "update business_tasks set created_at=?, updated_at=? where id=?",
            ("2026-09-23T09:00:00Z", "2026-09-23T09:00:00Z", task_id),
        )
        if with_follow_up:
            store.create_business_task_follow_up(
                business_task_id=task_id, source_signal_id=signal_id,
                target_conversation_id="cid-customer", target_kind="group",
                question_text="请确认验收进展", scheduled_at="2026-09-24T10:00:00Z",
                owner_user_id="alex", owner_name="Alex",
                dedupe_key=f"check:{task_id}", _db=db,
            )
    return task_id, signal_id


@pytest.mark.parametrize("completion_source", ["dingtalk", "task_agent"])
def test_business_task_completion_recomputes_attention_membership(tmp_path, completion_source):
    store = _store(tmp_path)
    task_id, signal_id = _business_task_for_completion(store)
    sibling_id, _ = _business_task_for_completion(store, title="客户合同")
    with store.business_task_transaction() as db:
        anchor_id = store.create_business_anchor_in_transaction(
            anchor_type="customer", anchor_ref="customer:1", title="客户", _db=db,
        )
        for linked_task_id in (task_id, sibling_id):
            store.create_business_task_anchor_link_in_transaction(
                task_id=linked_task_id, anchor_id=anchor_id, status="confirmed",
                active=True, evidence_signal_id=signal_id, _db=db,
            )
    attention_id = BusinessAttentionProjection(store).upsert(AttentionProposal(
        stable_key="customer:1:delivery", category=AttentionCategory.PUSH,
        title="客户交付需关注", business_area="交付", why_attention="客户等待交付",
        current_state="推进中", ceo_action="确认交付安排", anchor_id=anchor_id,
        task_ids=(task_id, sibling_id), evidence_signal_id=signal_id,
    ))
    evidence = {
        "source": "dingtalk_todo:dt-task-1" if completion_source == "dingtalk" else "dws_message:done-1",
        "reason": "负责人确认已交付", "description": "客户验收材料已提交。",
        "completed_at": "2026-09-24T09:00:00Z", "checked_at": "2026-09-24T10:00:00Z",
    }
    if completion_source == "dingtalk":
        store.create_business_task_dingtalk_link(
            business_task_id=task_id, dingtalk_task_id="dt-task-1", status="active",
        )
        assert complete_business_task_from_external_todo(
            store, business_task_id=task_id, evidence=evidence,
        )
    else:
        item = WorkItem.model_validate({
            "source": {"type": "todo_completion_check", "ref": f"business-task-completion-check:{task_id}:2026-09-24"},
            "context": {"source_conversation_kind": "group"},
            "summary": json.dumps({
                "business_task": {"id": task_id},
                "search_policy": {
                    "time_window": {"prefer_since": "2026-09-23T09:00:00Z", "end": "2026-09-24T10:00:00Z"},
                    "limits": {"max_tool_calls": 8, "max_sources_to_return": 3},
                    "allowed_sources": ["dws_message"],
                },
            }),
        })
        store.enqueue_work_summary_input(item.source.type.value, item.source.ref, item.model_dump_json())
        [work_input] = store.claim_work_summary_inputs(limit=1)
        decision = TaskCompletionDecision.model_validate({
            "todo_changes": [{"action": "close", "business_task_id": task_id, "completion_evidence": evidence}],
            "search_trace": [{
                "source_kind": "dws_message", "source_ref": evidence["source"],
                "source_created_at": evidence["completed_at"],
                "result": "负责人确认已交付", "reason": "原始回复直接确认该交付物完成。",
            }],
            "update_summary": "负责人已完成客户验收交付。",
        })
        runner = SimpleNamespace(
            decide=lambda **kwargs: decision,
            last_audit_tool_events=[{"tool": "dws_message_get", "call_id": "read-1", "output": evidence["source"]}],
        )
        process_task_completion_work_item(store, runner, work_input, now="2026-09-24T10:00:00Z")

    assert store.get_business_task(task_id).status.value == "done"
    assert [link.task_id for link in store.list_business_attention_tasks(attention_id)] == [sibling_id]
    events = store.list_business_attention_events(attention_id)
    assert json.loads(events[-1].after_json)["task_ids"] == [sibling_id]


def test_business_task_completion_scanner_pages_standalone_tasks_once_per_day(tmp_path):
    store = _store(tmp_path)
    first, _ = _business_task_for_completion(store)
    second, _ = _business_task_for_completion(store, title="客户合同", with_follow_up=True)
    store.create_business_task(title="未明确的小事项", stage="candidate")
    registry = FeatureRegistry(state_path=tmp_path / "skill-state.json")
    registry.set_enabled("work_tracking", True)
    kwargs = {"now": "2026-09-24T10:00:00Z", "limit": 1, "feature_registry": registry}

    assert enqueue_todo_completion_evidence_checks(store, object(), **kwargs) == 1
    assert enqueue_todo_completion_evidence_checks(store, object(), **kwargs) == 1
    assert enqueue_todo_completion_evidence_checks(store, object(), **kwargs) == 0
    inputs = store.claim_work_summary_inputs(limit=10)
    assert len(inputs) == 2
    summaries = [json.loads(json.loads(item.payload_json)["summary"]) for item in inputs]
    assert [summary["business_task"]["id"] for summary in summaries] == [first, second]
    assert all("project" not in summary and "todo" not in summary for summary in summaries)
    assert summaries[0]["source_signals"][0]["source_ref"] == f"message:assigned:{first}"
    assert summaries[1]["follow_ups"][0]["business_task_id"] == second
    assert all(item.source_ref.startswith("business-task-completion-check:") for item in inputs)
    assert all("dws_message" in summary["search_policy"]["allowed_sources"] for summary in summaries)


def test_business_task_completion_scanner_can_require_existing_follow_up(tmp_path):
    store = _store(tmp_path)
    _business_task_for_completion(store)
    followed, _ = _business_task_for_completion(store, title="客户合同", with_follow_up=True)
    registry = FeatureRegistry(state_path=tmp_path / "skill-state.json")
    registry.set_enabled("work_tracking", True)

    checked = enqueue_todo_completion_evidence_checks(
        store, object(), now="2026-09-24T10:00:00Z", require_follow_up=True,
        feature_registry=registry,
    )

    assert checked == 1
    [work_input] = store.claim_work_summary_inputs(limit=10)
    summary = json.loads(json.loads(work_input.payload_json)["summary"])
    assert summary["business_task"]["id"] == followed
