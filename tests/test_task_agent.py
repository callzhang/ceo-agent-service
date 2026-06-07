import json
import sqlite3

import pytest

from app.process_runner import ProcessRunResult
from app.store import AutoReplyStore
from app.task_agent import TaskAgentRunner, apply_task_agent_decision, process_work_item
from app.task_models import TaskAgentDecision, WorkItem


class FakeCodex:
    last_session_id = "task-session-1"
    last_transcript_start_line = 1
    last_transcript_end_line = 10

    def __init__(self, payload):
        self.payload = payload
        self.prompts = []

    def decide(self, *, prompt, session_id=None):
        self.prompts.append(prompt)
        return TaskAgentDecision.model_validate(self.payload)


class FakeCodexWithoutSession(FakeCodex):
    last_session_id = None


def _work_item(project_name="售前知识库"):
    return WorkItem.model_validate(
        {
            "source": {
                "type": "reply_attempt",
                "ref": "1",
                "title": "售前推进",
                "conversation_id": "cid-1",
                "conversation_title": "售前群",
                "created_at": "2026-06-07 09:00:00",
            },
            "summary": "售前知识库需要补齐来源链接，owner 是 Alex。",
            "project_name": project_name,
            "context": {
                "sender": "Mina",
                "participants": ["Alex"],
                "source_conversation_kind": "group",
                "source_conversation_title": "售前群",
            },
        }
    )


def test_process_work_item_creates_project_todo_update_and_run(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    item = _work_item()
    input_id = store.enqueue_work_summary_input(
        source_type=item.source.type.value,
        source_ref=item.source.ref,
        payload_json=item.model_dump_json(),
    )
    assert input_id > 0
    work_input = store.claim_work_summary_inputs(limit=1)[0]
    codex = FakeCodex(
        {
            "action": "create_project",
            "project": {
                "title": "售前知识库建设",
                "category": "sales",
                "tags": ["售前"],
                "status": "active",
                "priority": "P1",
                "risk_level": "medium",
                "needs_derek_attention": False,
                "owner_user_id": "owner-1",
                "owner_name": "Alex",
                "related_people": [],
                "goal": "沉淀售前材料",
                "background": "售前知识库项目。",
                "facts": [
                    {
                        "description": "需要补齐来源链接。",
                        "source": "reply_attempt:1",
                        "created": "2026-06-07",
                        "updated": "2026-06-07",
                    }
                ],
                "current_state": "已识别来源链接缺口。",
                "blocker": "",
                "next_step": "Alex 补齐来源链接。",
                "next_follow_up_at": "2026-06-10 09:00:00",
                "follow_up_mode": "draft",
                "source_conversations": [{"conversation_id": "cid-1", "title": "售前群"}],
            },
            "todo_changes": [
                {
                    "action": "create",
                    "title": "补齐来源链接",
                    "owner_user_id": "owner-1",
                    "owner_name": "Alex",
                    "status": "open",
                    "priority": "P1",
                    "next_follow_up_at": "2026-06-10 09:00:00",
                    "follow_up_question": "来源链接现在补齐到哪一步了？",
                    "completion_evidence": None,
                    "blocker": "",
                }
            ],
            "follow_up_drafts": [],
            "update_summary": "创建售前知识库项目。",
            "merge_reason": "无现有项目匹配，且事项名称稳定。",
            "memory_recall_used": True,
            "confidence": 0.9,
        }
    )

    process_work_item(store, TaskAgentRunner(codex), work_input)

    projects = store.list_work_projects()
    assert len(projects) == 1
    assert projects[0].title == "售前知识库建设"
    assert store.list_work_todos(project_id=projects[0].id)[0].title == "补齐来源链接"
    assert store.list_work_updates(project_id=projects[0].id)[0].summary == "创建售前知识库项目。"
    assert store.claim_work_summary_inputs(limit=1) == []
    assert "memory_recall" in codex.prompts[0]
    assert "候选项目" in codex.prompts[0]
    with sqlite3.connect(tmp_path / "task.sqlite3") as db:
        input_row = db.execute(
            "select status, error from work_summary_inputs where id=?",
            (input_id,),
        ).fetchone()
        run_row = db.execute(
            """
            select summary_input_id, codex_session_id, audit_summary, memory_recall_used
            from task_agent_runs
            """,
        ).fetchone()
    assert input_row == ("done", "")
    assert run_row == (input_id, "task-session-1", "创建售前知识库项目。", 1)


def test_apply_decision_closes_todo_with_completion_evidence(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(
        title="客户交付",
        category="projects",
        status="active",
        priority="P0",
        risk_level="high",
    )
    todo_id = store.create_work_todo(
        project_id=project_id,
        title="给出交付 ETA",
        status="open",
        priority="P0",
    )
    decision = TaskAgentDecision.model_validate(
        {
            "action": "update_project",
            "project": {"id": project_id, "title": "客户交付", "category": "projects"},
            "todo_changes": [
                {
                    "action": "close",
                    "todo_id": todo_id,
                    "title": "给出交付 ETA",
                    "status": "done",
                    "completion_evidence": {
                        "source": "ai_minutes:minutes-1",
                        "summary": "会议纪要明确 ETA 已发送客户。",
                        "confidence": 0.93,
                    },
                }
            ],
            "follow_up_drafts": [],
            "update_summary": "关闭 ETA 待办。",
            "merge_reason": "同一客户交付项目。",
            "memory_recall_used": False,
            "confidence": 0.93,
        }
    )

    apply_task_agent_decision(
        store,
        summary_input_id=0,
        work_item=_work_item("客户交付"),
        decision=decision,
        codex_session_id="session-1",
    )

    todo = store.list_work_todos(project_id=project_id)[0]
    assert todo.status == "done"
    assert "ETA 已发送客户" in todo.completion_evidence_json


def test_discard_decision_records_run_and_marks_input_discarded(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    item = _work_item()
    input_id = store.enqueue_work_summary_input(
        item.source.type.value,
        item.source.ref,
        item.model_dump_json(),
    )
    work_input = store.claim_work_summary_inputs(limit=1)[0]
    codex = FakeCodex(
        {
            "action": "discard",
            "discard_reason": "不是稳定任务。",
            "todo_changes": [],
            "follow_up_drafts": [],
            "update_summary": "丢弃输入。",
            "merge_reason": "",
            "memory_recall_used": False,
            "confidence": 0.8,
        }
    )

    process_work_item(store, TaskAgentRunner(codex), work_input)

    with sqlite3.connect(tmp_path / "task.sqlite3") as db:
        input_row = db.execute(
            "select status, error from work_summary_inputs where id=?",
            (input_id,),
        ).fetchone()
        run_row = db.execute(
            "select summary_input_id, audit_summary from task_agent_runs",
        ).fetchone()
    assert input_row == ("discarded", "不是稳定任务。")
    assert run_row == (input_id, "丢弃输入。")


def test_follow_up_drafts_are_created_with_risk_check(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    decision = TaskAgentDecision.model_validate(
        {
            "action": "create_project",
            "project": {
                "title": "售前知识库建设",
                "category": "sales",
                "status": "active",
            },
            "todo_changes": [],
            "follow_up_drafts": [
                {
                    "owner_user_id": "owner-1",
                    "owner_name": "Alex",
                    "target_conversation_id": "cid-1",
                    "target_kind": "group",
                    "question_text": "项目目标和 owner 是否确认？",
                    "scheduled_at": "2026-06-08 09:00:00",
                    "risk_check": {"owner_in_group": True, "sensitive": False},
                    "status": "draft",
                }
            ],
            "update_summary": "需要追问项目边界。",
            "merge_reason": "",
            "memory_recall_used": False,
            "confidence": 0.7,
        }
    )

    project_id = apply_task_agent_decision(
        store,
        summary_input_id=0,
        work_item=_work_item(),
        decision=decision,
    )

    drafts = store.list_follow_up_drafts(statuses=("draft",))
    assert project_id is not None
    assert drafts[0].project_id == project_id
    assert drafts[0].question_text == "项目目标和 owner 是否确认？"
    assert json.loads(drafts[0].risk_check_json) == {
        "owner_in_group": True,
        "sensitive": False,
    }


def test_update_project_without_id_raises_value_error(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    decision = TaskAgentDecision.model_validate(
        {
            "action": "update_project",
            "project": {"title": "客户交付", "category": "projects"},
            "todo_changes": [],
            "follow_up_drafts": [],
            "update_summary": "更新客户交付。",
            "merge_reason": "",
            "memory_recall_used": False,
            "confidence": 0.5,
        }
    )

    with pytest.raises(ValueError, match="project.id"):
        apply_task_agent_decision(
            store,
            summary_input_id=0,
            work_item=_work_item("客户交付"),
            decision=decision,
        )


def test_process_work_item_failure_does_not_create_partial_project(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    item = _work_item()
    input_id = store.enqueue_work_summary_input(
        item.source.type.value,
        item.source.ref,
        item.model_dump_json(),
    )
    work_input = store.claim_work_summary_inputs(limit=1)[0]
    codex = FakeCodex(
        {
            "action": "create_project",
            "project": {"title": "售前知识库建设", "category": "sales"},
            "todo_changes": [{"action": "close", "title": "补齐来源链接"}],
            "follow_up_drafts": [],
            "update_summary": "坏的待办更新。",
            "merge_reason": "",
            "memory_recall_used": False,
            "confidence": 0.4,
        }
    )

    with pytest.raises(ValueError, match="requires todo_id"):
        process_work_item(store, TaskAgentRunner(codex), work_input)

    with sqlite3.connect(tmp_path / "task.sqlite3") as db:
        input_row = db.execute(
            "select status from work_summary_inputs where id=?",
            (input_id,),
        ).fetchone()
        project_count = db.execute("select count(*) from work_projects").fetchone()
        update_count = db.execute("select count(*) from work_updates").fetchone()
        run_count = db.execute("select count(*) from task_agent_runs").fetchone()
    assert input_row == ("failed",)
    assert project_count == (0,)
    assert update_count == (0,)
    assert run_count == (1,)


def test_sparse_todo_update_preserves_existing_status_and_priority(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(
        title="客户交付",
        category="projects",
        status="active",
        priority="P0",
        risk_level="high",
    )
    todo_id = store.create_work_todo(
        project_id=project_id,
        title="给出交付 ETA",
        status="waiting_owner",
        priority="P0",
    )
    decision = TaskAgentDecision.model_validate(
        {
            "action": "update_project",
            "project": {"id": project_id, "title": "客户交付", "category": "projects"},
            "todo_changes": [
                {
                    "action": "update",
                    "todo_id": todo_id,
                    "blocker": "等待 owner 回复",
                }
            ],
            "follow_up_drafts": [],
            "update_summary": "补充阻塞原因。",
            "merge_reason": "同一客户交付项目。",
            "memory_recall_used": False,
            "confidence": 0.8,
        }
    )

    apply_task_agent_decision(
        store,
        summary_input_id=0,
        work_item=_work_item("客户交付"),
        decision=decision,
    )

    todo = store.list_work_todos(project_id=project_id)[0]
    assert todo.status == "waiting_owner"
    assert todo.priority == "P0"
    assert todo.blocker == "等待 owner 回复"
    update = store.list_work_updates(project_id=project_id)[0]
    todo_change = json.loads(update.changes_json)["todo_changes"][0]
    assert todo_change == {
        "action": "update",
        "todo_id": todo_id,
        "blocker": "等待 owner 回复",
    }


def test_discard_with_malformed_todo_change_marks_failed(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    item = _work_item()
    input_id = store.enqueue_work_summary_input(
        item.source.type.value,
        item.source.ref,
        item.model_dump_json(),
    )
    work_input = store.claim_work_summary_inputs(limit=1)[0]
    codex = FakeCodex(
        {
            "action": "discard",
            "discard_reason": "不是稳定任务。",
            "todo_changes": [{"action": "close", "title": "补齐来源链接"}],
            "follow_up_drafts": [],
            "update_summary": "丢弃输入。",
            "merge_reason": "",
            "memory_recall_used": False,
            "confidence": 0.8,
        }
    )

    with pytest.raises(ValueError, match="requires todo_id"):
        process_work_item(store, TaskAgentRunner(codex), work_input)

    with sqlite3.connect(tmp_path / "task.sqlite3") as db:
        input_row = db.execute(
            "select status from work_summary_inputs where id=?",
            (input_id,),
        ).fetchone()
    assert input_row == ("failed",)


def test_process_work_item_accepts_none_session_id(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    item = _work_item()
    input_id = store.enqueue_work_summary_input(
        item.source.type.value,
        item.source.ref,
        item.model_dump_json(),
    )
    work_input = store.claim_work_summary_inputs(limit=1)[0]
    codex = FakeCodexWithoutSession(
        {
            "action": "discard",
            "discard_reason": "一次性对话。",
            "todo_changes": [],
            "follow_up_drafts": [],
            "update_summary": "丢弃。",
            "merge_reason": "",
            "memory_recall_used": False,
            "confidence": 0.9,
        }
    )

    process_work_item(store, TaskAgentRunner(codex), work_input)

    with sqlite3.connect(tmp_path / "task.sqlite3") as db:
        run_row = db.execute(
            "select summary_input_id, codex_session_id from task_agent_runs",
        ).fetchone()
    assert run_row == (input_id, "")


def test_task_agent_codex_runner_parses_jsonl_payload(tmp_path):
    from app.task_agent import TaskAgentCodexRunner

    def executor(command, prompt):
        return (
            '{"type":"session_meta","payload":{"id":"session-task-1"}}\n'
            '{"item":{"type":"agent_message","text":"'
            '{\\"action\\":\\"discard\\",'
            '\\"discard_reason\\":\\"没有状态变化\\",'
            '\\"todo_changes\\":[],'
            '\\"follow_up_drafts\\":[],'
            '\\"update_summary\\":\\"无变化\\",'
            '\\"merge_reason\\":\\"\\",'
            '\\"memory_recall_used\\":false,'
            '\\"confidence\\":0.7}'
            '"}}\n'
        )

    runner = TaskAgentCodexRunner(workspace=tmp_path, executor=executor)
    decision = runner.decide(prompt="x")

    assert decision.action == "discard"
    assert runner.last_session_id == "session-task-1"


def test_task_agent_codex_runner_parses_response_item_output_text(tmp_path):
    from app.task_agent import TaskAgentCodexRunner

    def executor(command, prompt):
        return "\n".join(
            [
                json.dumps({"type": "thread.started", "thread_id": "session-task-2"}),
                json.dumps(
                    {
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": json.dumps(
                                        {
                                            "action": "discard",
                                            "discard_reason": "只是确认收到",
                                            "project": None,
                                            "todo_changes": [],
                                            "follow_up_drafts": [],
                                            "update_summary": "无新增事项",
                                            "merge_reason": "",
                                            "memory_recall_used": False,
                                            "confidence": 0.8,
                                        },
                                        ensure_ascii=False,
                                    ),
                                }
                            ],
                        },
                    },
                    ensure_ascii=False,
                ),
            ]
        )

    runner = TaskAgentCodexRunner(workspace=tmp_path, executor=executor)
    decision = runner.decide(prompt="x")

    assert decision.action == "discard"
    assert decision.discard_reason == "只是确认收到"
    assert runner.last_session_id == "session-task-2"


def test_task_agent_schema_uses_strict_object_shapes():
    from app.task_agent import TASK_AGENT_DECISION_SCHEMA_PATH

    schema = json.loads(TASK_AGENT_DECISION_SCHEMA_PATH.read_text(encoding="utf-8"))

    def visit(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node.get("additionalProperties") is False
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for item in node:
                visit(item)

    visit(schema)


def test_task_agent_codex_runner_uses_process_runner_signature(tmp_path):
    from app.task_agent import TaskAgentCodexRunner
    from app.task_agent import TASK_AGENT_DECISION_SCHEMA_PATH
    from app.codex_runner import CODEX_DECISION_SCHEMA_PATH

    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return ProcessRunResult(
            returncode=0,
            stdout=json.dumps(
                {
                    "action": "discard",
                    "discard_reason": "没有状态变化",
                    "todo_changes": [],
                    "follow_up_drafts": [],
                    "update_summary": "无变化",
                    "merge_reason": "",
                    "memory_recall_used": False,
                    "confidence": 0.7,
                },
                ensure_ascii=False,
            ),
            stderr="",
        )

    runner = TaskAgentCodexRunner(
        workspace=tmp_path,
        timeout_seconds=7,
        idle_timeout_seconds=3,
    )
    runner._run_process_with_idle_timeout = fake_run

    decision = runner.decide(prompt="decide")

    assert decision.action == "discard"
    assert calls
    command = calls[0][0]
    assert calls[0][1]["prompt"] == "decide"
    assert calls[0][1]["env"] == runner.runner.build_env()
    assert calls[0][1]["total_timeout_seconds"] == 7
    assert calls[0][1]["idle_timeout_seconds"] == 3
    assert "--output-schema" in command
    assert "--ignore-user-config" in command
    assert str(TASK_AGENT_DECISION_SCHEMA_PATH) in command
    assert str(CODEX_DECISION_SCHEMA_PATH) not in command


def test_task_agent_codex_runner_timeout_raises_reason(tmp_path):
    from app.task_agent import TaskAgentCodexRunner

    def fake_run(command, **kwargs):
        return ProcessRunResult(
            returncode=-15,
            stdout="",
            stderr="",
            timed_out=True,
            timeout_kind="idle",
            timeout_reason="process produced no output for 3 seconds",
        )

    runner = TaskAgentCodexRunner(workspace=tmp_path)
    runner._run_process_with_idle_timeout = fake_run

    with pytest.raises(RuntimeError, match="no output for 3 seconds"):
        runner.decide(prompt="decide")
