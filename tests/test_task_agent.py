import json
import sqlite3
from types import SimpleNamespace

import pytest

from app.store import AutoReplyStore
from app.agent_runtime_router import CodexCommandFactory, RoutedResultValidationError
from app.task_agent import (
    TaskAgentCodexRunner,
    TaskAgentRunner,
    apply_task_agent_decision,
    build_task_agent_prompt,
    process_work_item,
    _parse_task_agent_decision,
    _task_result_validation_repair_prompt,
)
from app.leak_check import contains_credential, contains_local_runtime_leak
from app.task_models import TaskAgentDecision, WorkItem, WorkItemSourceType
from app.task_business_resolution import BusinessResolutionService
from app.task_semantic_service import (
    RecordCandidate,
    RecordFormalTask,
    SourceSignal,
    TaskSemanticService,
)
from app.task_semantic_rules import FormalityEvidence
from app.task_semantic_models import BusinessActorKind, BusinessTaskDateType, FormalTaskBasis
from app.task_agent_session import TaskAgentSessionLeaseLost


def test_task_agent_parser_uses_valid_result_after_failed_tool_event():
    decision = {"task_decisions": [{
        "action": "skip", "transition": "none",
        "skip_reason": "No durable work was identified.",
    }]}
    raw = "\n".join(
        [
            json.dumps(
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "item_completed",
                        "item": {"type": "McpToolCall", "status": "failed"},
                    },
                }
            ),
            json.dumps(
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "task_complete",
                        "last_agent_message": json.dumps(decision),
                    },
                }
            ),
        ]
    )

    assert _parse_task_agent_decision(raw) == TaskAgentDecision.model_validate(decision)





def test_task_agent_parser_recovers_complete_object_with_repeated_continuation():
    decision = {"task_decisions": [{
        "action": "skip", "transition": "none",
        "skip_reason": "The source is informational only.",
    }]}
    malformed = json.dumps(decision) + ',"task_decisions":[]}'

    assert _parse_task_agent_decision(malformed) == TaskAgentDecision.model_validate(decision)


def test_task_agent_parser_marks_missing_decision_as_validation_failure():
    with pytest.raises(RoutedResultValidationError, match="No TaskAgentDecision JSON found") as raised:
        _parse_task_agent_decision("not a decision")

    assert raised.value.raw_output == "not a decision"


def _agent_message_jsonl(*messages: str) -> str:
    return "\n".join(
        json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": message}})
        for message in messages
    )


def _null_evidence_decision(**overrides) -> dict:
    decision = {"task_decisions": [{
        "action": "skip", "transition": "none", "skip_reason": "No change.",
        "untrusted_runtime_value": "Confirm the vendor quote with Zhang",
    }]}
    decision.update(overrides)
    return decision


def test_task_agent_parser_reports_field_errors_of_last_candidate():
    raw = _agent_message_jsonl(json.dumps(_null_evidence_decision()))

    with pytest.raises(
        RoutedResultValidationError,
        match=r"task_decisions\.0\.untrusted_runtime_value: Extra inputs are not permitted",
    ) as raised:
        _parse_task_agent_decision(raw)

    message = str(raised.value)
    assert "untrusted_runtime_value" in message
    assert "No TaskAgentDecision JSON found" not in message
    assert raised.value.raw_output == raw


def test_task_agent_parser_accepts_minimax_null_optional_fields():
    decision = {"task_decisions": [{"action": "skip", "transition": "none",
        "skip_reason": "No source-grounded Task."}]}
    parsed = _parse_task_agent_decision(_agent_message_jsonl(json.dumps(decision)))
    assert parsed == TaskAgentDecision.model_validate(decision)



def test_task_agent_parser_ignores_event_objects_when_naming_failing_candidate():
    raw = "\n".join(
        [
            json.dumps({"type": "turn.started"}),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"type": "mcp_tool_call", "status": "failed"},
                }
            ),
            _agent_message_jsonl(json.dumps(_null_evidence_decision())),
            json.dumps({"type": "turn.completed", "usage": {"input_tokens": 1}}),
        ]
    )

    with pytest.raises(RoutedResultValidationError) as raised:
        _parse_task_agent_decision(raw)

    message = str(raised.value)
    assert "task_decisions.0.untrusted_runtime_value" in message
    assert "type:" not in message
    assert "item:" not in message
    assert "usage:" not in message
    assert "action: Field required" not in message


def test_task_agent_parser_message_never_echoes_field_values():
    decision = _null_evidence_decision()
    decision["task_decisions"][0]["untrusted_runtime_value"] = \
        "Read /tmp/ceo-agent-service/notes.md with sk-proj-abcdefghijklmnop"
    raw = json.dumps(decision)
    assert contains_local_runtime_leak(raw)
    assert contains_credential(raw)

    with pytest.raises(RoutedResultValidationError) as raised:
        _parse_task_agent_decision(raw)

    message = str(raised.value)
    assert not contains_local_runtime_leak(message)
    assert not contains_credential(message)
    assert "notes.md" not in message


def test_task_result_validation_repair_prompt_lists_field_errors_and_rules():
    raw = _agent_message_jsonl(json.dumps(_null_evidence_decision()))

    prompt = _task_result_validation_repair_prompt(raw)

    assert "- task_decisions.0.untrusted_runtime_value: Extra inputs are not permitted" in prompt
    assert "task_decisions" in prompt
    assert "apply_acceptance requires accepted polarity" in prompt
    assert "verified reply-to source reference" in prompt
    assert "memory_recall" in prompt
    assert "live directory read" in prompt
    assert (
        "A source path may appear only in an evidence field whose key is "
        "exactly source or source_ref"
    ) in prompt
    assert "Confirm the vendor quote" not in prompt
    assert "Vendor quote still pending" not in prompt


def test_task_result_validation_repair_prompt_for_prose_only_output():
    prompt = _task_result_validation_repair_prompt("I could not find any durable work here.")

    assert "did not contain a TaskAgentDecision JSON object" in prompt
    assert "without prose or code fences" in prompt
    assert "Do not include local filesystem paths" in prompt


def test_task_result_validation_repair_prompt_after_runtime_path_leak():
    decision = {
        "task_decisions": [{"action": "skip", "transition": "none",
            "skip_reason": "Could not read /tmp/ceo-agent-service/todo.md"}],
    }

    prompt = _task_result_validation_repair_prompt(_agent_message_jsonl(json.dumps(decision)))

    assert "satisfied the schema but a business field contained a runtime path" in prompt
    assert "Do not include local filesystem paths" in prompt
    assert not contains_local_runtime_leak(prompt)


def test_task_result_validation_repair_prompt_caps_problem_list():
    decision = {"task_decisions": [
        {"action": "skip", "transition": "none", "untrusted_field": str(index)}
        for index in range(15)
    ]}

    prompt = _task_result_validation_repair_prompt(json.dumps(decision))

    problem_section = prompt.split("Rules that must hold:")[0]
    assert problem_section.count("\n- ") == 12
    assert "task_decisions.11.untrusted_field" in problem_section
    assert "task_decisions.12.untrusted_field" not in problem_section


class FakeCodex:
    last_session_id = "task-session-1"
    last_transcript_start_line = 1
    last_transcript_end_line = 10

    def __init__(self, payload):
        self.payload = payload
        self.prompts = []
        self.calls = []

    def decide(self, *, prompt, session_id=None, workload_key=None, session_scope_id=None):
        self.prompts.append(prompt)
        self.calls.append(
            {
                "workload_key": workload_key,
                "session_scope_id": session_scope_id,
            }
        )
        return TaskAgentDecision.model_validate(self.payload)


class FakeCodexWithoutSession(FakeCodex):
    last_session_id = None


class FakeCodexWithAuditEvents(FakeCodex):
    def __init__(self, payload, audit_tool_events):
        super().__init__(payload)
        self.last_audit_tool_events = audit_tool_events


class FakeRoutedTaskExecution:
    def __init__(self, raw, *, session_id="", transcript_end=0):
        self.raw = raw
        self.session_id = session_id
        self.transcript_end = transcript_end
        self.calls = []

    def execute(self, **kwargs):
        self.calls.append(kwargs)
        raw = self.raw() if callable(self.raw) else self.raw
        value = kwargs["parser"](raw)
        value = kwargs["result_codec"].decode(kwargs["result_codec"].encode(value))
        return SimpleNamespace(
            value=value,
            route_name="codex_oauth",
            attempt_id=1,
            session_id=self.session_id,
            transcript_start=0,
            transcript_end=self.transcript_end,
        )


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
                "sender": "Avery",
                "participants": ["Alex"],
                "source_conversation_kind": "group",
                "source_conversation_title": "售前群",
            },
        }
    )






def _low_confidence_minutes_work_item() -> WorkItem:
    return WorkItem.model_validate(
        {
            "source": {
                "type": "local_file",
                "ref": "/tmp/HR周例会.md#sha256=abc",
                "title": "HR周例会.md",
                "conversation_id": "",
                "conversation_title": "",
                "created_at": "2026-06-22 13:33:31",
            },
            "summary": "\n".join(
                [
                    "> **参与人**: 磊哥, susu, 刘瑞安Alan, 胡明, 张静, Avery",
                    "# Transcript",
                    "[00:01] 刘瑞安Alan: 第一段",
                    "[00:02] 刘瑞安Alan: 第二段",
                    "[00:03] 刘瑞安Alan: 第三段",
                    "[00:04] 刘瑞安Alan: 第四段",
                    "[00:05] 刘瑞安Alan: 第五段",
                ]
            ),
            "project_name": "HR周例会.md",
            "context": {
                "sender": "",
                "participants": [],
                "source_conversation_kind": "file",
                "source_conversation_title": "HR周例会.md",
            },
        }
    )


def test_process_work_item_opens_and_completes_runtime_parent_before_decision(tmp_path):
    store = AutoReplyStore(tmp_path / "task-lifecycle.sqlite3")
    item = _work_item()
    input_id = store.enqueue_work_summary_input(
        item.source.type.value, item.source.ref, item.model_dump_json()
    )
    work_input = store.claim_work_summary_inputs(limit=1)[0]
    payload = {"task_decisions": [{"action": "skip", "transition": "none",
        "skip_reason": "no durable update"}]}

    class LifecycleCodex(FakeCodex):
        last_audit_tool_events = []

        def decide(self, **kwargs):
            run_id = int(kwargs["workload_key"])
            with store._connect() as db:
                row = db.execute(
                    "select status from task_agent_runs where id=?", (run_id,)
                ).fetchone()
            assert row["status"] == "running"
            return super().decide(**kwargs)

    codex = LifecycleCodex(payload)
    process_work_item(store, TaskAgentRunner(codex), work_input)

    with store._connect() as db:
        runs = db.execute(
            "select * from task_agent_runs where summary_input_id=?", (input_id,)
        ).fetchall()
    assert len(runs) == 1
    assert runs[0]["status"] == "completed"
    assert json.loads(runs[0]["decision_json"])["task_decisions"][0]["action"] == "skip"
    assert store.get_work_summary_input(input_id).status.value == "skipped"
    assert codex.calls[0]["session_scope_id"] == "task-agent:work-tracking:v1"


def test_process_work_items_keep_run_keys_but_share_task_agent_session_scope(tmp_path):
    store = AutoReplyStore(tmp_path / "task-shared-session-scope.sqlite3")
    first_item = _work_item()
    second_payload = first_item.model_dump(mode="json")
    second_payload["source"]["ref"] = "2"
    second_item = WorkItem.model_validate(second_payload)
    for item in (first_item, second_item):
        store.enqueue_work_summary_input(
            item.source.type.value,
            item.source.ref,
            item.model_dump_json(),
        )
    work_inputs = store.claim_work_summary_inputs(limit=2)
    codex = FakeCodex(
        {
            "task_decisions": [
                {
                    "action": "skip",
                    "transition": "none",
                    "skip_reason": "No new durable work.",
                }
            ]
        }
    )
    runner = TaskAgentRunner(codex)

    for work_input in work_inputs:
        process_work_item(store, runner, work_input)

    assert [call["session_scope_id"] for call in codex.calls] == [
        "task-agent:work-tracking:v1",
        "task-agent:work-tracking:v1",
    ]
    assert codex.calls[0]["workload_key"] != codex.calls[1]["workload_key"]


def test_process_work_item_does_not_apply_decision_after_session_lease_loss(tmp_path):
    store = AutoReplyStore(tmp_path / "task-session-lease-lost.sqlite3")
    item = _work_item()
    input_id = store.enqueue_work_summary_input(
        item.source.type.value,
        item.source.ref,
        item.model_dump_json(),
    )
    work_input = store.claim_work_summary_inputs(limit=1)[0]
    decision = {
        "task_decisions": [
            {
                "action": "record_candidate",
                "transition": "none",
                "source_excerpt": "补齐来源链接",
                "source_ref": item.source.ref,
                "title": "补齐报价来源链接",
                "missing_evidence": ["owner"],
            }
        ]
    }

    class LeaseLostBeforeApply:
        calls = 0

        def assert_owned(self):
            self.calls += 1
            if self.calls == 2:
                raise TaskAgentSessionLeaseLost("session lease expired")

    with pytest.raises(TaskAgentSessionLeaseLost):
        process_work_item(
            store,
            TaskAgentRunner(FakeCodex(decision)),
            work_input,
            session_lease=LeaseLostBeforeApply(),
        )

    assert not store.list_business_tasks()
    assert store.get_work_summary_input(input_id).status.value == "failed"
    with store._connect() as db:
        run = db.execute(
            "select status from task_agent_runs where summary_input_id=?",
            (input_id,),
        ).fetchone()
    assert run["status"] == "failed"


def test_process_work_item_success_commits_task_and_terminal_run_and_input(tmp_path, monkeypatch):
    monkeypatch.setattr("app.task_agent.memory_connector_config_issue", lambda: "")
    store = AutoReplyStore(tmp_path / "task-success-lifecycle.sqlite3")
    item = _work_item()
    input_id = store.enqueue_work_summary_input(
        item.source.type.value, item.source.ref, item.model_dump_json()
    )
    work_input = store.claim_work_summary_inputs(limit=1)[0]
    payload = {"task_decisions": [{
        "action": "record_candidate", "transition": "none",
        "source_excerpt": "补齐来源链接", "source_ref": item.source.ref,
        "title": "补齐报价来源链接", "missing_evidence": ["owner"],
    }]}

    process_work_item(store, TaskAgentRunner(FakeCodexWithAuditEvents(payload, [])), work_input)

    assert len(store.list_business_tasks()) == 1
    assert store.get_work_summary_input(input_id).status.value == "done"
    with sqlite3.connect(tmp_path / "task-success-lifecycle.sqlite3") as db:
        run = db.execute(
            "select status, error from task_agent_runs where summary_input_id=?", (input_id,)
        ).fetchone()
    assert run == ("completed", "")


def test_work_item_accepts_task_routing_signals():
    item = WorkItem.model_validate(
        {
            "source": {
                "type": "reply_attempt",
                "ref": "1992",
                "title": "Riley",
                "conversation_id": "cid-lily",
                "conversation_title": "Riley",
                "created_at": "2026-06-28 09:44:05",
            },
            "summary": "Riley反馈海外数据合规P0追错owner。",
            "project_name": "",
            "context": {
                "sender": "Riley",
                "sender_user_id": "lily-user-1",
                "participants": ["Riley"],
                "source_conversation_kind": "direct",
                "source_conversation_title": "Riley",
            },
            "task_signals": {
                "possible_task_update": True,
                "mentions_follow_up": True,
                "progress_claim": False,
                "owner_correction": True,
                "complaint_about_followup": True,
                "signal_reason": "同一会话里有近期已发送follow-up，且用户反馈追错owner。",
            },
        }
    )

    assert item.task_signals.possible_task_update is True
    assert item.context.sender_user_id == "lily-user-1"
    assert item.task_signals.owner_correction is True
    assert item.task_signals.complaint_about_followup is True
    assert "追错owner" in item.task_signals.signal_reason



def test_task_agent_prompt_does_not_embed_candidate_specific_workflow():
    item = WorkItem.model_validate(
        {
            "source": {
                "type": "local_file",
                "ref": "/tmp/刘芸婷一面.md#sha256=abc",
                "title": "刘芸婷 - 国际销售工程师（北京） - 一面",
                "conversation_id": "",
                "conversation_title": "",
                "created_at": "2026-07-10T13:59:28+08:00",
            },
            "summary": "刘芸婷一面记录显示需要判断后续推进状态。",
            "project_name": "刘芸婷国际销售工程师候选人评估与后续推进",
            "context": {
                "sender": "张静",
                "participants": ["张静", "Morgan", "刘芸婷"],
                "source_conversation_kind": "minutes",
                "source_conversation_title": "刘芸婷一面",
            },
            "task_signals": {
                "possible_task_update": True,
                "mentions_follow_up": False,
                "signal_reason": "关键候选人流程状态需要跟进。",
            },
        }
    )

    prompt = build_task_agent_prompt(item, "候选项目:\n[]\n\n近期 follow-up 候选:\n[]")

    assert "xiaoqing_interview" not in prompt
    assert "当前阶段、最终决策、决策时间和决策说明" not in prompt
    assert "刘芸婷一面记录显示需要判断后续推进状态" in prompt





def test_process_work_item_continues_when_memory_connector_unavailable(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        "app.task_agent.memory_connector_config_issue",
        lambda: "memory connector token is expired",
    )
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    item = _work_item()
    input_id = store.enqueue_work_summary_input(
        item.source.type.value,
        item.source.ref,
        item.model_dump_json(),
    )
    work_input = store.claim_work_summary_inputs(limit=1)[0]
    codex = FakeCodexWithAuditEvents(
        {"task_decisions": [{"action": "skip", "transition": "none",
            "skip_reason": "No source-grounded task."}]},
        audit_tool_events=[],
    )

    process_work_item(store, TaskAgentRunner(codex), work_input)

    with sqlite3.connect(tmp_path / "task.sqlite3") as db:
        input_row = db.execute(
            "select status, error from work_summary_inputs where id=?",
            (input_id,),
        ).fetchone()
        run_count = db.execute("select count(*) from task_agent_runs").fetchone()[0]
    assert input_row[0] == "skipped"
    assert run_count == 1
    assert "不可用：memory connector token is expired" in codex.prompts[0]
    assert "Memory is background only" in codex.prompts[0]


def test_task_agent_codex_runner_uses_standard_runtime_factory():
    routed = FakeRoutedTaskExecution(
        json.dumps(
            {
                "task_decisions": [{"action": "skip", "transition": "none",
                    "skip_reason": "输入不足以形成稳定事项。"}],
            }
        )
    )
    runner = TaskAgentCodexRunner(routed_execution=routed)

    runner.decide(prompt="{}", workload_key="1")

    assert routed.calls[0]["workload_kind"] == "task"
    assert isinstance(routed.calls[0]["command_factory"], CodexCommandFactory)


def test_task_agent_prompt_loads_work_tracking_skill_and_schema_contract():
    prompt = build_task_agent_prompt(
        _work_item(),
        "无候选项目",
        memory_issue="",
    )

    assert "# CEO Work Tracking" in prompt
    assert '"title": "TaskAgentDecision"' in prompt
    assert "Memory connector status:" in prompt
    assert "Memory is background only" in prompt
    assert "focused live directory/contact lookup" in prompt
    prompt = " ".join(prompt.split())
    assert "use connected tools only for read-only discovery" in prompt.lower()
    assert "Do not use CLI, API, or MCP tools to create, update, delete, send, or complete" in prompt
    assert "the service validates and applies supported operations" in prompt.lower()
    assert "Prior session turns are background only" in prompt


def test_task_agent_prompt_prioritizes_weekly_report_then_meeting_evidence():
    prompt = " ".join(build_task_agent_prompt(_work_item(), "候选上下文为空。").split())

    assert "most recent confirmed official weekly report first" in prompt
    assert "project, owner, target, DDL, status, and next-task fields" in prompt
    assert "Confirmed meeting evidence" in prompt
    assert "Chat or message evidence only supplements" in prompt
    assert "cannot create an official Project or override an explicit weekly-report field" in prompt
    assert "preserve the exact source reference/excerpt" in prompt


def test_task_agent_prompt_uses_scheduled_consumer_prompt_and_targeted_skill():
    payload = _work_item().model_dump(mode="json")
    payload["scheduled_consumer"] = {
        "schema": "scheduled_consumer.v1",
        "scheduled_task_id": 7,
        "scheduled_task_run_id": 11,
        "prompt": "只处理 $ceo-work-tracking 能确认的真实工作项。",
        "skill_names": ["ceo-work-tracking"],
        "skill_protocol": "# Old Work Tracking Snapshot\nReturn update_project with todo_changes.",
    }

    prompt = build_task_agent_prompt(
        WorkItem.model_validate(payload),
        "无候选项目",
    )

    assert "## Scheduled Consumer Prompt" in prompt
    assert "只处理 $ceo-work-tracking 能确认的真实工作项。" in prompt
    assert "# Old Work Tracking Snapshot" in prompt
    assert "Return update_project with todo_changes." in prompt
    assert prompt.count("Return update_project with todo_changes.") == 1
    assert "# CEO Work Tracking" in prompt
    assert "task_decisions" in prompt
    assert "Current Task-first decision envelope controls output" in prompt




def test_task_agent_prompt_uses_skill_for_important_vs_routine_process_boundary():
    work_item = _work_item()
    work_item.summary = "Avery: 这种事情没必要创建待办，我不办这人也没法发 offer。"
    prompt = build_task_agent_prompt(
        work_item,
        candidate_prompt="候选上下文为空。",
    )

    assert "source-backed low-impact work when its source workflow needs a record" in prompt
    assert "keep it outside attention" in prompt
    assert "Do not use keyword routers" in prompt


def test_task_agent_prompt_discards_non_actionable_reference_material():
    item = WorkItem.model_validate(
        {
            "source": {
                "type": "local_file",
                "ref": "/tmp/reference.md#sha256=abc",
                "title": "内部演讲稿",
            },
            "summary": "一份内部培训演讲稿，未记录负责人、截止时间或下一步。",
            "context": {
                "sender": "Derek",
                "participants": ["Derek"],
                "source_conversation_kind": "file",
                "source_conversation_title": "内部培训演讲稿",
            },
        }
    )

    prompt = build_task_agent_prompt(item, "候选上下文为空。")

    assert "If the source contains no plausible action or decision, skip it" in prompt
    assert "Never invent a task" in prompt
    assert "source-backed deliverable" in prompt
    assert "Project" in prompt

def test_task_agent_prompt_does_not_inject_retrieved_business_examples():
    work_item = _work_item(project_name="宝马项目客户 Demo 推进")
    work_item.summary = (
        "宝马项目周末攻坚要准备客户 Demo 原型，原型应该产品同学负责，"
        "之前拆给测试不对。"
    )

    prompt = build_task_agent_prompt(
        work_item,
        candidate_prompt="候选上下文为空。",
    )

    assert "可召回样例" not in prompt
    assert "不要把“做原型”拆给测试" not in prompt
    assert "候选上下文为空。" in prompt






def test_process_work_item_does_not_require_memory_recall_receipt(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr("app.task_agent.memory_connector_config_issue", lambda: "")
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    item = _work_item("客户交付")
    input_id = store.enqueue_work_summary_input(
        item.source.type.value,
        item.source.ref,
        item.model_dump_json(),
    )
    work_input = store.claim_work_summary_inputs(limit=1)[0]
    update = {"task_decisions": [{"action": "skip", "transition": "none",
        "skip_reason": "No source-grounded task; memory receipt is not a service gate."}]}

    class CodexWithoutMemoryRecallReceipt(FakeCodexWithAuditEvents):
        def __init__(self):
            super().__init__(update, [])

    codex = CodexWithoutMemoryRecallReceipt()
    process_work_item(store, TaskAgentRunner(codex), work_input)

    with sqlite3.connect(tmp_path / "task.sqlite3") as db:
        input_row = db.execute(
            "select status, error from work_summary_inputs where id=?",
            (input_id,),
        ).fetchone()
        runs = db.execute(
            "select status, error from task_agent_runs "
            "where summary_input_id=? order by id",
            (input_id,),
        ).fetchall()

    assert input_row[0] == "skipped"
    assert runs == [("completed", "")]
    assert len(codex.prompts) == 1


def test_process_work_item_rolls_back_batch_and_marks_input_and_run_failed(tmp_path, monkeypatch):
    monkeypatch.setattr("app.task_agent.memory_connector_config_issue", lambda: "")
    store = AutoReplyStore(tmp_path / "task-batch-failure.sqlite3")
    item = _work_item()
    input_id = store.enqueue_work_summary_input(
        item.source.type.value, item.source.ref, item.model_dump_json()
    )
    work_input = store.claim_work_summary_inputs(limit=1)[0]
    payload = {"task_decisions": [
        {"action": "record_candidate", "transition": "none",
         "source_excerpt": "补齐来源链接", "source_ref": item.source.ref,
         "title": "有效候选", "missing_evidence": ["owner"]},
        {"action": "record_candidate", "transition": "none",
         "source_excerpt": "不在原文中的内容", "source_ref": item.source.ref,
         "title": "必须回滚", "missing_evidence": ["owner"]},
    ]}
    codex = FakeCodexWithAuditEvents(payload, [])

    with pytest.raises(ValueError, match="exact source substring"):
        process_work_item(store, TaskAgentRunner(codex), work_input)

    assert store.list_business_tasks() == ()
    with sqlite3.connect(tmp_path / "task-batch-failure.sqlite3") as db:
        input_status = db.execute(
            "select status from work_summary_inputs where id=?", (input_id,)
        ).fetchone()[0]
        run = db.execute(
            "select status, error from task_agent_runs where summary_input_id=?",
            (input_id,),
        ).fetchone()
    assert input_status == "failed"
    assert run[0] == "failed"
    assert "exact source substring" in run[1]







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
        {"task_decisions": [{"action": "skip", "transition": "none",
            "skip_reason": "一次性对话。"}]}
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
        return "\n".join([
            json.dumps({"type": "session_meta", "payload": {"id": "session-task-1"}}),
            json.dumps({"item": {"type": "agent_message", "text": json.dumps({
                "task_decisions": [{"action": "skip", "transition": "none", "skip_reason": "没有状态变化"}]
            }, ensure_ascii=False)}}, ensure_ascii=False),
        ])

    runner = TaskAgentCodexRunner(
        routed_execution=FakeRoutedTaskExecution(
            executor([], "x"), session_id="session-task-1"
        )
    )
    decision = runner.decide(prompt="x", workload_key="1")

    assert decision.task_decisions[0].action == "skip"
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
                                                "task_decisions": [{"action": "skip", "transition": "none",
                                                    "skip_reason": "只是确认收到"}],
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

    runner = TaskAgentCodexRunner(
        routed_execution=FakeRoutedTaskExecution(
            executor([], "x"), session_id="session-task-2"
        )
    )
    decision = runner.decide(prompt="x", workload_key="1")

    assert decision.task_decisions[0].action == "skip"
    assert decision.task_decisions[0].skip_reason == "只是确认收到"
    assert runner.last_session_id == "session-task-2"


def test_task_agent_prompt_schema_is_generated_from_validation_model():
    prompt = build_task_agent_prompt(
        WorkItem.model_validate(
            {
                "source": {"type": "local_file", "ref": "schema-test"},
                "summary": "schema contract test",
                "context": {"source_conversation_kind": "file"},
            }
        ),
        "候选项目:\n[]\n\n近期 follow-up 候选:\n[]",
    )
    prompt_schema = json.loads(
        prompt.split("TaskAgentDecision Pydantic JSON schema:\n", 1)[1]
    )

    assert prompt_schema == TaskAgentDecision.model_json_schema()




def test_task_agent_codex_runner_uses_routed_execution_contract():
    routed = FakeRoutedTaskExecution(
        json.dumps(
            {
                "task_decisions": [{"action": "skip", "transition": "none",
                    "skip_reason": "没有状态变化"}],
            },
            ensure_ascii=False,
        )
    )
    runner = TaskAgentCodexRunner(routed_execution=routed)

    decision = runner.decide(prompt="decide", workload_key="9")

    assert decision.task_decisions[0].action == "skip"
    assert routed.calls[0]["workload_key"] == "9"
    assert routed.calls[0]["conversation_id"] is None
    assert routed.calls[0]["required_capabilities"] == frozenset(
        {
            "structured_output",
            "local_schema_validation",
        }
    )



def test_task_agent_codex_runner_requires_injected_execution():
    with pytest.raises(TypeError):
        TaskAgentCodexRunner()


def test_task_agent_codex_runner_reads_audit_events_from_session():
    routed = FakeRoutedTaskExecution(
        json.dumps(
            {"task_decisions": [{"action": "skip", "transition": "none",
                "skip_reason": "无需记录候选人 follow-up。"}]},
            ensure_ascii=False,
        ),
        session_id="019f0000-0000-7000-8000-000000000000",
        transcript_end=8,
    )
    runner = TaskAgentCodexRunner(routed_execution=routed)
    observed_limits = []

    def fake_session_events(session_id, start_line=0, end_line=None, limit=40):
        observed_limits.append(limit)
        if limit <= 40:
            return [{"tool": "exec_command", "arguments": "{}"}]
        return [{"tool": "mcp__memory_connector__memory_recall", "arguments": "{}"}]

    runner._extract_codex_audit_events_from_session = fake_session_events

    decision = runner.decide(prompt="decide", workload_key="1")

    assert decision.task_decisions[0].action == "skip"
    assert runner.last_transcript_start_line == 0
    assert runner.last_transcript_end_line == 8
    assert observed_limits == [200]
    assert runner.last_audit_tool_events == [
        {"tool": "mcp__memory_connector__memory_recall", "arguments": "{}"}
    ]


def test_task_agent_codex_runner_propagates_routed_failure():
    from app.agent_runtime_router import RoutedCodexExecutionError

    class FailingRoutedExecution:
        def execute(self, **kwargs):
            raise RoutedCodexExecutionError("runtime_execution_failed")

    runner = TaskAgentCodexRunner(routed_execution=FailingRoutedExecution())

    with pytest.raises(RoutedCodexExecutionError, match="runtime_execution_failed"):
        runner.decide(prompt="decide", workload_key="1")


def test_task_agent_codex_runner_translates_exhausted_transport_for_outer_retry():
    from app.agent_runtime_contracts import RuntimeFailureClass
    from app.agent_runtime_router import RoutedCodexExecutionError
    from app.external_retry import ExternalDependencyError

    class FailingRoutedExecution:
        def execute(self, **kwargs):
            raise RoutedCodexExecutionError(
                "runtime_execution_failed",
                failure_class=RuntimeFailureClass.TRANSPORT,
                failure_code="codex_total_timeout",
                retryable_external_dependency=True,
            )

    runner = TaskAgentCodexRunner(routed_execution=FailingRoutedExecution())

    with pytest.raises(ExternalDependencyError) as raised:
        runner.decide(prompt="decide", workload_key="1")
    assert raised.value.dependency == "codex"


@pytest.mark.parametrize("failure_code", ["codex_login_required", "runtime_result_invalid"])
def test_task_agent_codex_runner_keeps_nonretryable_routed_failures_terminal(
    failure_code,
):
    from app.agent_runtime_router import RoutedCodexExecutionError

    class FailingRoutedExecution:
        def execute(self, **kwargs):
            raise RoutedCodexExecutionError(
                "runtime_execution_failed",
                failure_code=failure_code,
                retryable_external_dependency=False,
            )

    runner = TaskAgentCodexRunner(routed_execution=FailingRoutedExecution())
    with pytest.raises(RoutedCodexExecutionError) as raised:
        runner.decide(prompt="decide", workload_key="1")
    assert raised.value.failure_code == failure_code


def test_task_agent_parser_finds_decision_embedded_in_prose():
    decision = {"task_decisions": [{"action": "skip", "transition": "none",
        "skip_reason": "No source-grounded task was found."}]}
    message = (
        "Based on my search within the allowed sources, I found:\n\n"
        "1. **Memory**: background only {not a decision}.\n\n"
        + json.dumps({"task_decisions": []})
        + "\n\nFinal decision:\n\n"
        + json.dumps(decision, indent=2)
        + "\n"
    )
    raw = json.dumps(
        {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": message},
        }
    )

    assert _parse_task_agent_decision(raw) == TaskAgentDecision.model_validate(decision)
    assert _parse_task_agent_decision(message) == TaskAgentDecision.model_validate(decision)


def _repair_codex(payloads):
    class RepairingCodex(FakeCodexWithAuditEvents):
        def __init__(self):
            super().__init__(payloads[0], [])
            self.payloads = list(payloads)
            self.calls = 0

        def decide(self, **kwargs):
            self.prompts.append(kwargs["prompt"])
            payload = self.payloads[self.calls]
            self.calls += 1
            self.last_audit_tool_events = (
                [] if self.calls == 1 else [{"tool": "memory_recall"}]
            )
            return TaskAgentDecision.model_validate(payload)

    return RepairingCodex()


def _claimed_work_input(store):
    item = _work_item()
    input_id = store.enqueue_work_summary_input(
        item.source.type.value,
        item.source.ref,
        item.model_dump_json(),
    )
    return input_id, store.claim_work_summary_inputs(limit=1)[0]






def _work_item(project_name="售前知识库", **context):
    return WorkItem.model_validate({
        "source": {
            "type": "reply_attempt", "ref": "message:1", "title": project_name,
            "conversation_id": "conversation:1", "conversation_title": "客户群",
            "created_at": "2026-09-20T09:00:00+08:00",
        },
        "summary": "补齐来源链接；owner 是 Alex。",
        "context": {
            "sender": "Avery", "sender_user_id": "avery-id",
            "source_conversation_kind": "group", **context,
        },
    })


def _candidate_decision(item, *, excerpt="补齐来源链接", title="补齐报价来源链接"):
    return TaskAgentDecision.model_validate({"task_decisions": [{
        "action": "record_candidate", "transition": "none",
        "source_excerpt": excerpt, "source_ref": item.source.ref,
        "title": title, "missing_evidence": ["owner"],
    }]})


def test_parser_accepts_zero_to_many_task_decisions():
    assert _parse_task_agent_decision('{"task_decisions": []}').task_decisions == []
    decision = _parse_task_agent_decision(json.dumps({"task_decisions": [
        {"action": "skip", "transition": "none", "skip_reason": "no task"},
        {"action": "record_candidate", "transition": "none", "source_excerpt": "补齐来源链接",
         "source_ref": "message:1", "title": "补齐来源链接", "missing_evidence": ["owner"]},
    ]}, ensure_ascii=False))
    assert len(decision.task_decisions) == 2


def test_parser_rejects_project_first_decision():
    with pytest.raises(ValueError, match="No TaskAgentDecision"):
        _parse_task_agent_decision('{"action":"update_project","project":{"id":1}}')


def test_multi_decision_batch_records_each_source_grounded_item(tmp_path):
    store = AutoReplyStore(tmp_path / "multi-task.sqlite3")
    item = _work_item(assignment_authorized=True)
    decision = TaskAgentDecision.model_validate({"task_decisions": [
        {"action": "record_candidate", "transition": "none", "source_excerpt": "补齐来源链接",
         "source_ref": item.source.ref, "title": "补齐报价来源链接", "missing_evidence": ["owner"]},
        {"action": "create_task", "transition": "none", "source_excerpt": "owner 是 Alex",
         "source_ref": item.source.ref, "title": "确认负责人", "formal_basis": "explicit_assignment",
         "owner_name": "Alex", "owner_evidence": {"source_ref": item.source.ref, "excerpt": "owner 是 Alex"}},
    ]})

    result = apply_task_agent_decision(store, summary_input_id=1, work_item=item, decision=decision, record_run=False)

    assert len(result.task_ids) == 2
    assert [task.stage.value for task in store.list_business_tasks()] == ["candidate", "formal"]
    assert len(store.list_business_task_signals()) == 2


def test_source_dedupe_distinguishes_owner_but_replays_identical_decision(tmp_path):
    store = AutoReplyStore(tmp_path / "owner-sensitive-dedupe.sqlite3")
    excerpt = "Alex and Bob are assigned to prepare the release report."
    work_items = [
        _work_item(assignment_authorized=True,
            owner_identity={"name": "Alex", "user_id": "alex-id"}).model_copy(
                update={"summary": excerpt}
            ),
        _work_item(assignment_authorized=True,
            owner_identity={"name": "Bob", "user_id": "bob-id"}).model_copy(
                update={"summary": excerpt}
            ),
    ]
    decisions = [TaskAgentDecision.model_validate({"task_decisions": [{
        "action": "create_task", "transition": "none", "formal_basis": "explicit_assignment",
        "source_excerpt": excerpt, "source_ref": item.source.ref, "title": "Prepare release report",
        "owner_name": owner_name,
        "owner_evidence": {"source_ref": item.source.ref, "excerpt": excerpt,
            "name": owner_name, "user_id": owner_id},
    }]}) for item, owner_name, owner_id in zip(
        work_items, ("Alex", "Bob"), ("alex-id", "bob-id"), strict=True
    )]

    (alex_task_id,) = apply_task_agent_decision(
        store, summary_input_id=1, work_item=work_items[0], decision=decisions[0], record_run=False
    )
    (replayed_alex_task_id,) = apply_task_agent_decision(
        store, summary_input_id=2, work_item=work_items[0], decision=decisions[0], record_run=False
    )
    (bob_task_id,) = apply_task_agent_decision(
        store, summary_input_id=3, work_item=work_items[1], decision=decisions[1], record_run=False
    )

    assert replayed_alex_task_id == alex_task_id
    assert bob_task_id != alex_task_id
    assert len(store.list_business_tasks()) == 2
    assert {task.owner_user_id for task in store.list_business_tasks()} == {"alex-id", "bob-id"}


def test_creation_replay_ignores_description_and_audit_wording(tmp_path):
    store = AutoReplyStore(tmp_path / "presentation-independent-dedupe.sqlite3")
    item = _work_item(assignment_authorized=True,
        owner_identity={"name": "Alex", "user_id": "alex-id"}).model_copy(update={
            "summary": "Alex is assigned to prepare the release report."
        })
    base = {
        "action": "create_task", "transition": "none", "formal_basis": "explicit_assignment",
        "source_excerpt": item.summary, "source_ref": item.source.ref,
        "title": "Prepare release report", "owner_name": "Alex",
        "owner_evidence": {"source_ref": item.source.ref, "excerpt": item.summary, "name": "Alex"},
    }
    first = TaskAgentDecision.model_validate({"task_decisions": [{
        **base, "description": "Prepare the report for launch.", "update_summary": "Owner confirmed."
    }]})
    replay = TaskAgentDecision.model_validate({"task_decisions": [{
        **base, "description": "The release report needs preparation.", "update_summary": "Clear owner evidence."
    }]})

    (first_id,) = apply_task_agent_decision(
        store, summary_input_id=1, work_item=item, decision=first, record_run=False
    )
    (replay_id,) = apply_task_agent_decision(
        store, summary_input_id=2, work_item=item, decision=replay, record_run=False
    )

    assert replay_id == first_id
    assert len(store.list_business_tasks()) == 1


def test_replayed_creation_with_new_date_requires_explicit_task_update(tmp_path):
    store = AutoReplyStore(tmp_path / "replay-date-effect.sqlite3")
    item = _work_item().model_copy(update={"summary": "补齐来源链接；Requested due 2026-09-25."})
    common = {
        "action": "record_candidate", "transition": "none",
        "source_excerpt": item.summary, "source_ref": item.source.ref,
        "title": "报价候选", "missing_evidence": ["owner"],
    }
    without_date = TaskAgentDecision.model_validate({"task_decisions": [common]})
    with_new_date = TaskAgentDecision.model_validate({"task_decisions": [{
        **common, "date_evidence": [{"kind": "requested_deadline_at", "value": "2026-09-25",
            "source_ref": item.source.ref, "source_excerpt": "2026-09-25"}],
    }]})

    (task_id,) = apply_task_agent_decision(store, summary_input_id=1, work_item=item,
        decision=without_date, record_run=False)
    with pytest.raises(ValueError, match="update the existing Task explicitly"):
        apply_task_agent_decision(store, summary_input_id=2, work_item=item,
            decision=with_new_date, record_run=False)

    assert [task.id for task in store.list_business_tasks()] == [task_id]
    assert store.list_business_task_date_evidence(task_id) == ()


def test_update_dedupe_identity_preserves_a_real_status_transition(tmp_path):
    store = AutoReplyStore(tmp_path / "status-effect-dedupe.sqlite3")
    task = TaskSemanticService(store).record_candidate(RecordCandidate(
        title="报价跟进", signal=SourceSignal(source_type="seed", source_ref="seed:status",
            evidence_text="报价跟进", dedupe_key="seed:status")
    ))
    item = _work_item().model_copy(update={"summary": "报价任务状态已更新"})

    for status in ("waiting", "done"):
        decision = TaskAgentDecision.model_validate({"task_decisions": [{
            "action": "update_task", "transition": "update_fields", "task_id": task.task_id,
            "source_excerpt": item.summary, "source_ref": item.source.ref,
            "title": "报价跟进", "status": status,
        }]})
        apply_task_agent_decision(store, summary_input_id=1, work_item=item,
            decision=decision, record_run=False)

    assert store.get_business_task(task.task_id).status.value == "done"
    assert len(store.list_business_task_signals()) == 3


def test_update_task_decision_applies_evidence_backed_description_change(tmp_path):
    store = AutoReplyStore(tmp_path / "description-update.sqlite3")
    task = TaskSemanticService(store).record_candidate(RecordCandidate(
        title="核对客户材料",
        description="整理收到的客户材料。",
        signal=SourceSignal(
            source_type="seed", source_ref="seed:description-update",
            evidence_text="核对客户材料", dedupe_key="seed:description-update",
        ),
    ))
    item = _work_item().model_copy(update={
        "summary": "客户材料已到齐并补齐缺项",
    })
    decision = TaskAgentDecision.model_validate({"task_decisions": [{
        "action": "update_task", "transition": "update_fields", "task_id": task.task_id,
        "source_excerpt": "客户材料已到齐并补齐缺项", "source_ref": item.source.ref,
        "title": "核对客户材料", "description": "客户材料已到齐，核对后补齐缺项。",
        "update_summary": "The latest source clarifies the remaining deliverable.",
    }]})

    apply_task_agent_decision(
        store, summary_input_id=1, work_item=item, decision=decision, record_run=False,
    )

    updated = store.get_business_task(task.task_id)
    assert updated.title == "核对客户材料"
    assert updated.description == "客户材料已到齐，核对后补齐缺项。"
    assert store.list_business_task_events(task.task_id)[-1].event_type.value == "details_changed"


def test_owner_evidence_uses_exact_decision_source_excerpt(tmp_path):
    store = AutoReplyStore(tmp_path / "owner-evidence-excerpt.sqlite3")
    item = _work_item(
        assignment_authorized=True,
    ).model_copy(update={"summary": "Alex 负责提交周报，周五前完成。"})
    decision = TaskAgentDecision.model_validate({"task_decisions": [{
        "action": "create_task", "transition": "none",
        "source_excerpt": item.summary, "source_ref": item.source.ref,
        "title": "提交周报", "owner_name": "Alex",
        "owner_evidence": {"source_ref": item.source.ref, "excerpt": "Alex负责提交周报"},
        "formal_basis": "explicit_assignment",
    }]})

    result = apply_task_agent_decision(
        store, summary_input_id=1, work_item=item, decision=decision, record_run=False,
    )

    task = store.get_business_task(result.task_ids[0])
    assert task is not None
    evidence = json.loads(task.owner_evidence_json)
    assert evidence["excerpt"] == item.summary


def _seed_identity_task(store, source_ref, *, external_task_id=""):
    context = {"owner_identity": {"name": "Alex", "user_id": "alex-id"}}
    if external_task_id:
        context["external_task_id"] = external_task_id
    return TaskSemanticService(store).record_formal_task(RecordFormalTask(
        title="提交周报",
        signal=SourceSignal(source_type="message", source_ref=source_ref,
            evidence_text="Alex 负责提交周报", dedupe_key=source_ref,
            conversation_id="conversation:weekly", author_user_id="avery-id",
            author_name="Avery", author_kind=BusinessActorKind.HUMAN,
            context_json=json.dumps(context)),
        formality=FormalityEvidence(basis=FormalTaskBasis.EXPLICIT_ASSIGNMENT,
            assigner_is_authorized=True, deliverable_is_explicit=True, owner_is_explicit=True),
        owner_user_id="alex-id", owner_name="Alex",
        owner_evidence_json=json.dumps({"source_ref": source_ref, "excerpt": "Alex 负责提交周报",
            "user_id": "alex-id", "name": "Alex"}),
    ))


def test_recurring_same_title_tasks_cannot_be_identity_merged(tmp_path):
    store = AutoReplyStore(tmp_path / "weekly-no-merge.sqlite3")
    first = _seed_identity_task(store, "message:week-1")
    second = _seed_identity_task(store, "message:week-2")
    item = _work_item().model_copy(update={"summary": "本周周报已提交"})
    decision = TaskAgentDecision.model_validate({"task_decisions": [{
        "action": "update_task", "transition": "merge_identity", "task_id": first.task_id,
        "target_task_id": second.task_id, "source_excerpt": "本周周报已提交",
        "source_ref": item.source.ref, "title": "提交周报",
        "identity_proposal": {"source_task_id": first.task_id, "target_task_id": second.task_id,
            "identity_evidence": {"basis": "same_deliverable_owner_context_time",
                "source_signal_id": first.signal_id, "target_signal_id": second.signal_id}},
    }]})

    with pytest.raises(ValueError, match="can link or cluster Tasks but cannot merge identity"):
        apply_task_agent_decision(store, summary_input_id=1, work_item=item, decision=decision, record_run=False)
    assert store.get_business_task(first.task_id).status.value != "merged"
    assert store.get_business_task(second.task_id).status.value != "merged"


def test_same_external_task_id_can_support_identity_merge_and_recompute_both_tasks(tmp_path, monkeypatch):
    from app.task_agent import BusinessAttentionProjection

    store = AutoReplyStore(tmp_path / "external-id-merge.sqlite3")
    first = _seed_identity_task(store, "message:external-1", external_task_id="dingtalk:task-44")
    second = _seed_identity_task(store, "message:external-2", external_task_id="dingtalk:task-44")
    item = _work_item().model_copy(update={"summary": "同步外部待办记录"})
    decision = TaskAgentDecision.model_validate({"task_decisions": [{
        "action": "update_task", "transition": "merge_identity", "task_id": first.task_id,
        "target_task_id": second.task_id, "source_excerpt": "同步外部待办记录",
        "source_ref": item.source.ref, "title": "提交周报",
        "identity_proposal": {"source_task_id": first.task_id, "target_task_id": second.task_id,
            "reason": "Same external task ID dingtalk:task-44",
            "identity_evidence": {"basis": "same_external_task_id",
                "source_signal_id": first.signal_id, "target_signal_id": second.signal_id}},
    }]})

    recomputed = []
    original_recompute = BusinessAttentionProjection.recompute_for_tasks

    def capture_recompute(self, task_ids):
        recomputed.append(tuple(task_ids))
        return original_recompute(self, task_ids)

    monkeypatch.setattr(BusinessAttentionProjection, "recompute_for_tasks", capture_recompute)
    result = apply_task_agent_decision(store, summary_input_id=1, work_item=item,
        decision=decision, record_run=False)

    assert result.task_ids == (second.task_id,)
    assert result.affected_task_ids == (second.task_id, first.task_id)
    assert recomputed == [(second.task_id, first.task_id)]
    assert store.get_business_task(first.task_id).status.value == "merged"
    assert store.get_business_task(second.task_id).status.value != "merged"


def test_batch_rolls_back_task_signal_and_all_proposals_on_later_invalid_evidence(tmp_path):
    store = AutoReplyStore(tmp_path / "atomic-batch.sqlite3")
    semantic = TaskSemanticService(store)
    source_id = semantic.record_candidate(RecordCandidate(
        title="报价跟进", signal=SourceSignal(source_type="seed", source_ref="seed:1", evidence_text="报价", dedupe_key="seed:1")
    )).task_id
    target_id = semantic.record_candidate(RecordCandidate(
        title="准备客户材料", signal=SourceSignal(source_type="seed", source_ref="seed:2", evidence_text="材料", dedupe_key="seed:2")
    )).task_id
    resolution = BusinessResolutionService(store)
    cluster_id = resolution.create_cluster(title="客户事项", task_ids=[source_id, target_id])
    anchor_id = resolution.register_anchor(anchor_type="customer", anchor_ref="customer:1", title="客户")
    item = _work_item()
    decision = TaskAgentDecision.model_validate({"task_decisions": [
        {"action": "update_task", "transition": "update_fields", "task_id": source_id,
         "source_excerpt": "补齐来源链接", "source_ref": item.source.ref, "title": "报价跟进",
         "status": "waiting", "relation_proposals": [{"from_task_id": source_id, "to_task_id": target_id,
         "relation_type": "related_to", "reason": "共享客户目标"}],
         "cluster_proposal": {"cluster_id": cluster_id, "task_ids": [source_id, target_id], "reason": "同一目标"},
         "anchor_match_proposals": [{"anchor_id": anchor_id, "reason": "客户事项"}],
         "project_candidate_proposal": {"cluster_id": cluster_id, "title": "客户项目候选", "reason": "持续任务"}},
        {"action": "record_candidate", "transition": "none", "source_excerpt": "不存在的原文",
         "source_ref": item.source.ref, "title": "无来源候选", "missing_evidence": ["source"]},
    ]})
    original = store.get_business_task(source_id)

    with pytest.raises(ValueError, match="exact source substring"):
        apply_task_agent_decision(store, summary_input_id=1, work_item=item, decision=decision, record_run=False)

    assert store.get_business_task(source_id) == original
    assert store.list_business_task_relations(task_id=source_id) == ()
    assert store.list_business_task_anchor_links(task_id=source_id) == ()
    assert len(store.list_business_task_signals()) == 2
    assert len(store.list_business_work_cluster_tasks(cluster_id=cluster_id)) == 2
    with store._connect() as db:
        assert db.execute("select count(*) from business_project_candidates").fetchone()[0] == 0


def test_same_task_multiple_decisions_keep_positional_task_signal_mapping(tmp_path):
    store = AutoReplyStore(tmp_path / "same-task.sqlite3")
    service = TaskSemanticService(store)
    created = service.record_candidate(RecordCandidate(
        title="报价跟进", signal=SourceSignal(source_type="seed", source_ref="seed:1", evidence_text="报价跟进", dedupe_key="seed:1")
    ))
    item = _work_item()
    decision = TaskAgentDecision.model_validate({"task_decisions": [
        {"action": "update_task", "transition": "update_fields", "task_id": created.task_id,
         "source_excerpt": "补齐来源链接", "source_ref": item.source.ref, "title": "报价跟进", "status": "waiting",
         "attention_proposal": {"category": "watch", "title": "报价延期风险", "why_attention": "有明确风险",
         "current_state": "待补来源", "ceo_action": "确认推进", "anchor_id": 1,
         "material_trigger": "risk_escalation", "trigger_evidence": "补齐来源链接"}},
        {"action": "update_task", "transition": "update_fields", "task_id": created.task_id,
         "source_excerpt": "owner 是 Alex", "source_ref": item.source.ref, "title": "报价跟进",
         "business_relevance": "relevant"},
    ]})

    result = apply_task_agent_decision(store, summary_input_id=1, work_item=item, decision=decision, record_run=False)

    assert result.task_ids == (created.task_id, created.task_id)
    assert len(result.attention_proposals) == 1
    assert result.attention_proposals[0][1] == created.task_id
    assert result.attention_proposals[0][2] != created.signal_id


def test_attention_projection_runs_after_outer_domain_transaction_commit(tmp_path, monkeypatch):
    from app.task_agent import BusinessAttentionProjection
    from app.task_semantic_service import TaskDateInput

    store = AutoReplyStore(tmp_path / "attention-after-commit.sqlite3")
    deadline = "2026-09-25"
    seed = TaskSemanticService(store).record_formal_task(RecordFormalTask(
        title="报价方案",
        signal=SourceSignal(source_type="message", source_ref="message:attention-assignment",
            evidence_text=f"Alex 承诺 {deadline} 交付报价方案", dedupe_key="seed:attention",
            conversation_id="conversation:1", author_kind=BusinessActorKind.HUMAN,
            author_user_id="alex-id", author_name="Alex"),
        formality=FormalityEvidence(basis=FormalTaskBasis.EXPLICIT_COMMITMENT,
            assigner_is_authorized=True, deliverable_is_explicit=True, owner_is_explicit=True),
        owner_user_id="alex-id", owner_name="Alex",
        owner_evidence_json=json.dumps({"source_ref": "message:attention-assignment",
            "excerpt": f"Alex 承诺 {deadline} 交付报价方案", "user_id": "alex-id", "name": "Alex"}),
        date_facts=(TaskDateInput(
            date_type=BusinessTaskDateType.COMMITTED_DEADLINE_AT, value_at=deadline,
            raw_phrase=deadline, actor_kind=BusinessActorKind.HUMAN,
            actor_user_id="alex-id", actor_name="Alex",
        ),),
    ))
    resolution = BusinessResolutionService(store)
    anchor_id = resolution.register_anchor(anchor_type="customer", anchor_ref="customer:attention",
        title="关键客户交付")
    resolution.confirm_anchor_match(task_id=seed.task_id, anchor_id=anchor_id,
        evidence_signal_id=seed.signal_id, reason="确认是关键客户业务事项")
    item = _work_item(sender="Alex", sender_user_id="alex-id").model_copy(update={
        "summary": "Alex says the delivery is at risk."
    })
    decision = TaskAgentDecision.model_validate({"task_decisions": [{
        "action": "update_task", "transition": "update_fields", "task_id": seed.task_id,
        "source_excerpt": item.summary, "source_ref": item.source.ref,
        "title": "报价方案", "business_relevance": "relevant",
        "attention_proposal": {"category": "watch", "title": "承诺交付风险", "why_attention": "负责人报告已接受承诺有风险",
         "current_state": "交付存在风险", "ceo_action": "核实交付状态", "anchor_id": anchor_id,
         "material_trigger": "threatened_commitment", "trigger_evidence": "delivery is at risk"},
    }]})
    seen = []
    original_upsert = BusinessAttentionProjection.upsert

    def check_committed(self, proposal):
        with store._connect() as other:
            task = other.execute(
                "select business_relevance from business_tasks where id=?", (seed.task_id,)
            ).fetchone()
            linked = other.execute(
                "select count(*) from business_task_evidence where task_id=? and signal_id=?",
                (seed.task_id, proposal.evidence_signal_id),
            ).fetchone()[0]
            confirmed = other.execute(
                "select count(*) from business_task_anchor_links where task_id=? and anchor_id=? "
                "and status='confirmed' and active=1", (seed.task_id, anchor_id),
            ).fetchone()[0]
        seen.append((task[0], linked, confirmed))
        return original_upsert(self, proposal)

    monkeypatch.setattr(BusinessAttentionProjection, "upsert", check_committed)
    with store.task_agent_domain_apply_transaction() as db:
        result = apply_task_agent_decision(store, summary_input_id=1, work_item=item,
            decision=decision, record_run=False, _db=db)
        assert seen == []
    from app.task_agent import _project_task_attention
    _project_task_attention(store, result.attention_proposals, result.affected_task_ids)

    assert seen == [("relevant", 1, 1)]
    (attention_item,) = store.list_business_attention_items()
    assert "threatened_commitment" in attention_item.why_attention
    assert "delivery is at risk" in attention_item.why_attention


def test_routine_progress_without_attention_proposal_is_not_projected(tmp_path):
    store = AutoReplyStore(tmp_path / "ordinary-progress-not-attention.sqlite3")
    seed = TaskSemanticService(store).record_candidate(RecordCandidate(
        title="报价跟进", signal=SourceSignal(source_type="seed", source_ref="seed:attention-candidate",
            evidence_text="报价跟进", dedupe_key="seed:attention-candidate")
    ))
    item = _work_item().model_copy(update={"summary": "补齐来源链接"})
    decision = TaskAgentDecision.model_validate({"task_decisions": [{
        "action": "update_task", "transition": "update_fields", "task_id": seed.task_id,
        "source_excerpt": "补齐来源链接", "source_ref": item.source.ref,
        "title": "报价跟进", "business_relevance": "relevant",
    }]})

    apply_task_agent_decision(store, summary_input_id=1, work_item=item,
        decision=decision, record_run=False)

    assert store.list_business_attention_items() == ()


@pytest.mark.parametrize("change", [
    {"status": "done"},
    {"status": "cancelled"},
    {"business_relevance": "not_relevant"},
])
def test_agent_recomputes_existing_attention_after_terminal_or_irrelevant_change(tmp_path, change):
    from app.task_attention_projection import AttentionProposal, BusinessAttentionProjection

    store = AutoReplyStore(tmp_path / f"attention-recompute-{next(iter(change.values()))}.sqlite3")
    seed = _seed_identity_task(store, f"message:attention-recompute-{next(iter(change.values()))}")
    relevance_item = _work_item().model_copy(update={"summary": "报价任务进入主营业务范围"})
    relevance = TaskAgentDecision.model_validate({"task_decisions": [{
        "action": "update_task", "transition": "update_fields", "task_id": seed.task_id,
        "source_excerpt": relevance_item.summary, "source_ref": relevance_item.source.ref,
        "title": "提交周报", "business_relevance": "relevant",
    }]})
    apply_task_agent_decision(store, summary_input_id=1, work_item=relevance_item,
        decision=relevance, record_run=False)
    resolution = BusinessResolutionService(store)
    anchor_id = resolution.register_anchor(anchor_type="customer", anchor_ref=f"customer:{seed.task_id}",
        title="客户交付")
    resolution.confirm_anchor_match(task_id=seed.task_id, anchor_id=anchor_id,
        evidence_signal_id=seed.signal_id, reason="已确认主营业务锚点")
    projection = BusinessAttentionProjection(store)
    attention_id = projection.upsert(AttentionProposal(
        stable_key=f"test:task:{seed.task_id}", category="watch", title="任务需关注",
        business_area="客户交付", why_attention="source-backed material trigger: 交付受到影响",
        current_state="处理中", ceo_action="核实推进", anchor_id=anchor_id,
        task_ids=(seed.task_id,), evidence_signal_id=seed.signal_id,
    ))
    change_item = _work_item().model_copy(update={"summary": "Task finished."})
    decision = TaskAgentDecision.model_validate({"task_decisions": [{
        "action": "update_task", "transition": "update_fields", "task_id": seed.task_id,
        "source_excerpt": change_item.summary, "source_ref": change_item.source.ref,
        "title": "提交周报", **change,
    }]})

    result = apply_task_agent_decision(store, summary_input_id=2, work_item=change_item,
        decision=decision, record_run=False)

    assert result.affected_task_ids == (seed.task_id,)
    assert store.list_business_attention_tasks(attention_id) == ()


def test_unlinked_owner_reply_does_not_accept_any_task(tmp_path):
    store = AutoReplyStore(tmp_path / "unlinked-acceptance.sqlite3")
    service = TaskSemanticService(store)
    assigned = service.record_formal_task(RecordFormalTask(
        title="报价方案", signal=SourceSignal(source_type="message", source_ref="message:assignment",
            evidence_text="Alex 负责报价方案", dedupe_key="assignment:1", author_user_id="alex-id",
            author_name="Alex", author_kind=BusinessActorKind.HUMAN),
        formality=FormalityEvidence(basis=FormalTaskBasis.MEETING_ACTION_ITEM,
            assigner_is_authorized=True, deliverable_is_explicit=True, owner_is_explicit=True),
        owner_user_id="alex-id", owner_name="Alex",
        owner_evidence_json=json.dumps({"source_ref": "message:assignment",
            "excerpt": "Alex 负责报价方案", "user_id": "alex-id", "name": "Alex"}),
    ))
    item = _work_item(sender="Alex", sender_user_id="alex-id")
    decision = TaskAgentDecision.model_validate({"task_decisions": [{
        "action": "update_task", "transition": "apply_acceptance", "task_id": assigned.task_id,
        "acceptance_polarity": "accepted", "acceptance_target_signal_id": assigned.signal_id,
        "source_excerpt": "补齐来源链接", "source_ref": item.source.ref, "title": "报价方案",
    }]})

    result = apply_task_agent_decision(store, summary_input_id=1, work_item=item, decision=decision, record_run=False)

    task = store.get_business_task(assigned.task_id)
    assert result.task_ids == ()
    assert result.skipped_reasons and "no verified reply-to reference" in result.skipped_reasons[0]
    assert task.commitment_status.value == "assigned_unaccepted"


def test_owner_evidence_does_not_itself_authorize_assignment(tmp_path):
    store = AutoReplyStore(tmp_path / "unauthorized-assignment.sqlite3")
    item = _work_item()
    decision = TaskAgentDecision.model_validate({"task_decisions": [{
        "action": "create_task", "transition": "none", "source_excerpt": "owner 是 Alex",
        "source_ref": item.source.ref, "title": "确认负责人", "formal_basis": "explicit_assignment",
        "owner_name": "Alex", "owner_evidence": {"source_ref": item.source.ref, "excerpt": "owner 是 Alex"},
    }]})

    with pytest.raises(ValueError, match="authorized source metadata"):
        apply_task_agent_decision(store, summary_input_id=1, work_item=item, decision=decision, record_run=False)
    assert store.list_business_tasks() == ()


@pytest.mark.parametrize("sender, sender_user_id, accepted", [
    ("Avery", "avery-id", False),
    ("Alex", "alex-id", True),
])
def test_new_commitment_requires_the_named_owner_to_author_it(tmp_path, sender, sender_user_id, accepted):
    store = AutoReplyStore(tmp_path / f"owner-authored-commitment-{sender}.sqlite3")
    excerpt = "Alex 承诺 2026-09-25 交付报价方案"
    item = _work_item(
        sender=sender, sender_user_id=sender_user_id,
        owner_identity={"name": "Alex", "user_id": "alex-id"},
    ).model_copy(update={"summary": excerpt})
    decision = TaskAgentDecision.model_validate({"task_decisions": [{
        "action": "create_task", "transition": "none", "formal_basis": "explicit_commitment",
        "source_excerpt": excerpt, "source_ref": item.source.ref, "title": "交付报价方案",
        "owner_name": "Alex", "owner_evidence": {"source_ref": item.source.ref,
            "excerpt": excerpt, "name": "Alex", "user_id": "alex-id"},
        "date_evidence": [{"kind": "committed_deadline_at", "value": "2026-09-25",
            "source_ref": item.source.ref, "source_excerpt": "2026-09-25",
            "actor_user_id": "alex-id", "actor_name": "Alex"}],
    }]})

    if accepted:
        (task_id,) = apply_task_agent_decision(
            store, summary_input_id=1, work_item=item, decision=decision, record_run=False
        )
        task = store.get_business_task(task_id)
        assert task.commitment_status.value == "accepted"
        date_fact = next(
            fact for fact in store.list_business_task_date_evidence(task_id)
            if fact.date_type.value == "committed_deadline_at"
        )
        assert (date_fact.actor_user_id, date_fact.actor_name) == ("alex-id", "Alex")
    else:
        with pytest.raises(ValueError, match="authored by its identified owner"):
            apply_task_agent_decision(
                store, summary_input_id=1, work_item=item, decision=decision, record_run=False
            )
        assert store.list_business_tasks() == ()


@pytest.mark.parametrize(
    "basis, expected_error",
    [
        ("meeting_action_item", "sourced meeting action-item record"),
        ("external_todo", "trusted external TODO source metadata"),
    ],
)
def test_formal_basis_cannot_be_selected_for_an_unrelated_reply_source(
    tmp_path, basis, expected_error
):
    store = AutoReplyStore(tmp_path / f"invalid-source-basis-{basis}.sqlite3")
    item = _work_item(
        owner_identity={"name": "Alex", "user_id": "alex-id"},
        external_task_id="dingtalk-task-1",
    )
    decision = TaskAgentDecision.model_validate({"task_decisions": [{
        "action": "create_task", "transition": "none", "formal_basis": basis,
        "source_excerpt": "补齐来源链接", "source_ref": item.source.ref,
        "title": "补齐报价来源链接", "owner_name": "Alex",
        "owner_evidence": {"source_ref": item.source.ref, "excerpt": "补齐来源链接; owner 是 Alex",
            "name": "Alex", "user_id": "alex-id"},
    }]})

    with pytest.raises(ValueError, match=expected_error):
        apply_task_agent_decision(
            store, summary_input_id=1, work_item=item, decision=decision, record_run=False
        )
    assert store.list_business_tasks() == ()


def test_meeting_action_item_requires_minutes_action_item_source(tmp_path):
    store = AutoReplyStore(tmp_path / "minutes-action-item-source.sqlite3")
    item = WorkItem.model_validate({
        "source": {"type": "ai_minutes", "ref": "minutes-1#todos-sha256=abc",
            "title": "客户交付会议行动项", "created_at": "2026-09-20T09:00:00+08:00"},
        "summary": "Alex 负责补齐报价来源链接。",
        "context": {"sender": "", "source_conversation_kind": "minutes",
            "owner_identity": {"name": "Alex", "user_id": "alex-id"}},
    })
    decision = TaskAgentDecision.model_validate({"task_decisions": [{
        "action": "create_task", "transition": "none", "formal_basis": "meeting_action_item",
        "source_excerpt": "Alex 负责补齐报价来源链接。", "source_ref": item.source.ref,
        "title": "补齐报价来源链接", "owner_name": "Alex",
        "owner_evidence": {"source_ref": item.source.ref,
            "excerpt": "Alex 负责补齐报价来源链接。", "name": "Alex", "user_id": "alex-id"},
    }]})

    (task_id,) = apply_task_agent_decision(
        store, summary_input_id=1, work_item=item, decision=decision, record_run=False
    )

    assert store.get_business_task(task_id).commitment_status.value == "assigned_unaccepted"


def test_next_check_date_uses_identified_agent_actor(tmp_path):
    store = AutoReplyStore(tmp_path / "next-check-date.sqlite3")
    item = _work_item().model_copy(update={
        "summary": "补齐来源链接；下次检查 2026-10-01T09:00:00+08:00"
    })
    decision = TaskAgentDecision.model_validate({"task_decisions": [{
        "action": "record_candidate", "transition": "none", "source_excerpt": item.summary,
        "source_ref": item.source.ref, "title": "补齐报价来源链接", "missing_evidence": ["owner"],
        "date_evidence": [{"kind": "next_check_at", "value": "2026-10-01T09:00:00+08:00",
            "source_ref": item.source.ref, "source_excerpt": "2026-10-01T09:00:00+08:00"}],
    }]})

    (task_id,) = apply_task_agent_decision(store, summary_input_id=1, work_item=item, decision=decision, record_run=False)
    (date_fact,) = store.list_business_task_date_evidence(task_id)
    assert (date_fact.actor_kind.value, date_fact.actor_user_id, date_fact.actor_name) == ("agent", "task-agent", "CEO Agent")


def test_source_target_and_next_check_create_task_keyed_follow_up(tmp_path):
    store = AutoReplyStore(tmp_path / "task-follow-up.sqlite3")
    item = _work_item(
        assignment_authorized=True,
        owner_identity={"name": "Alex", "user_id": "alex-id"},
    ).model_copy(update={
        "summary": "Avery assigns Alex to confirm quote. Check 2026-10-01T09:00:00+08:00."
    })
    decision = TaskAgentDecision.model_validate({"task_decisions": [{
        "action": "create_task", "transition": "none", "formal_basis": "explicit_assignment",
        "source_excerpt": item.summary, "source_ref": item.source.ref,
        "title": "Confirm quote", "owner_name": "Alex",
        "owner_evidence": {"source_ref": item.source.ref,
            "excerpt": "Avery assigns Alex", "name": "Alex", "user_id": "alex-id"},
        "date_evidence": [{"kind": "next_check_at", "value": "2026-10-01T09:00:00+08:00",
            "source_ref": item.source.ref, "source_excerpt": "2026-10-01T09:00:00+08:00"}],
    }]})

    (task_id,) = apply_task_agent_decision(
        store, summary_input_id=1, work_item=item, decision=decision, record_run=False
    )

    [draft] = store.list_business_task_follow_ups(business_task_id=task_id)
    assert draft["business_task_id"] == task_id
    assert draft["target_conversation_id"] == item.source.conversation_id
    assert draft["target_kind"] == "group"
    assert draft["owner_user_id"] == "alex-id"
    assert draft["scheduled_at"] == "2026-10-01T09:00:00+08:00"


def test_noncommitment_date_types_keep_exact_source_and_human_actor(tmp_path):
    store = AutoReplyStore(tmp_path / "typed-date-provenance.sqlite3")
    item = _work_item(sender="Avery", sender_user_id="avery-id", assignment_authorized=True,
        owner_identity={"name": "Alex", "user_id": "alex-id"}).model_copy(update={
            "summary": "Avery assigns Alex on 2026-09-20. Request due 2026-09-25; "
                       "external deadline 2026-09-26; estimate 2026-09-27."
        })
    decision = TaskAgentDecision.model_validate({"task_decisions": [{
        "action": "create_task", "transition": "none", "formal_basis": "explicit_assignment",
        "source_excerpt": item.summary, "source_ref": item.source.ref,
        "title": "客户报价跟进", "owner_name": "Alex",
        "owner_evidence": {"source_ref": item.source.ref, "excerpt": "Avery assigns Alex",
            "name": "Alex", "user_id": "alex-id"},
        "date_evidence": [
            {"kind": "requested_deadline_at", "value": "2026-09-25",
             "source_ref": item.source.ref, "source_excerpt": "2026-09-25"},
            {"kind": "external_deadline_at", "value": "2026-09-26",
             "source_ref": item.source.ref, "source_excerpt": "2026-09-26"},
            {"kind": "estimated_deadline_at", "value": "2026-09-27",
             "source_ref": item.source.ref, "source_excerpt": "2026-09-27"},
        ],
    }]})

    (task_id,) = apply_task_agent_decision(
        store, summary_input_id=1, work_item=item, decision=decision, record_run=False
    )

    facts = store.list_business_task_date_evidence(task_id)
    facts_by_type = {fact.date_type.value: fact for fact in facts}
    assert set(facts_by_type) == {
        "assigned_at", "requested_deadline_at", "external_deadline_at", "estimated_deadline_at"
    }
    assert {kind: fact.value_at for kind, fact in facts_by_type.items()} == {
        "assigned_at": item.source.created_at,
        "requested_deadline_at": "2026-09-25",
        "external_deadline_at": "2026-09-26",
        "estimated_deadline_at": "2026-09-27",
    }
    assert all(fact.source_signal_id == store.list_business_task_evidence(task_id)[0].signal_id for fact in facts)
    assert {kind: fact.raw_phrase for kind, fact in facts_by_type.items()} == {
        "assigned_at": item.source.created_at,
        "requested_deadline_at": "2026-09-25",
        "external_deadline_at": "2026-09-26",
        "estimated_deadline_at": "2026-09-27",
    }
    assert {kind: (fact.actor_kind.value, fact.actor_user_id, fact.actor_name)
            for kind, fact in facts_by_type.items()} == {
        "assigned_at": ("human", "avery-id", "Avery"),
        "requested_deadline_at": ("human", "avery-id", "Avery"),
        "external_deadline_at": ("human", "avery-id", "Avery"),
        "estimated_deadline_at": ("human", "avery-id", "Avery"),
    }


@pytest.mark.parametrize("date_patch, message", [
    ({"source_ref": "message:other"}, "date evidence source_ref must match"),
    ({"source_excerpt": "猜测出来的日期"}, "date evidence source_excerpt must be an exact source substring"),
    ({"value": "2026-09-26"}, "date value must match an exact, parseable date phrase"),
    ({"value": "2026-09-25T00:00:00"}, "date value must match an exact, parseable date phrase"),
    ({"actor_user_id": "other-id"}, "date actor_user_id must match the trusted date actor"),
    ({"actor_name": "Other person"}, "date actor_name must match the trusted date actor"),
])
def test_date_evidence_rejects_wrong_reference_or_non_source_excerpt(tmp_path, date_patch, message):
    store = AutoReplyStore(tmp_path / "invalid-date-provenance.sqlite3")
    item = _work_item().model_copy(update={
        "summary": "补齐来源链接；请求截止日期为 2026-09-25。"
    })
    date_fact = {"kind": "requested_deadline_at", "value": "2026-09-25",
        "source_ref": item.source.ref, "source_excerpt": "2026-09-25", **date_patch}
    decision = TaskAgentDecision.model_validate({"task_decisions": [{
        "action": "record_candidate", "transition": "none",
        "source_excerpt": "补齐来源链接", "source_ref": item.source.ref,
        "title": "报价候选", "missing_evidence": ["owner"], "date_evidence": [date_fact],
    }]})

    with pytest.raises(ValueError, match=message):
        apply_task_agent_decision(
            store, summary_input_id=1, work_item=item, decision=decision, record_run=False
        )
    assert store.list_business_tasks() == ()


def test_ai_minutes_date_actor_requires_trusted_speaker_mapping(tmp_path):
    store = AutoReplyStore(tmp_path / "minutes-date-attribution.sqlite3")
    item = _work_item(sender="Meeting host", sender_user_id="host-id").model_copy(update={
        "source": _work_item().source.model_copy(update={"type": WorkItemSourceType.AI_MINUTES}),
        "summary": "Alex 承诺 2026-09-25 交付报价方案",
    })
    decision = TaskAgentDecision.model_validate({"task_decisions": [{
        "action": "record_candidate", "transition": "none", "source_excerpt": item.summary,
        "source_ref": item.source.ref, "title": "报价方案", "missing_evidence": ["owner"],
        "date_evidence": [{"kind": "requested_deadline_at", "value": "2026-09-25",
            "source_ref": item.source.ref, "source_excerpt": "2026-09-25",
            "actor_user_id": "alex-id", "actor_name": "Alex"}],
    }]})

    with pytest.raises(ValueError, match="AI Minutes date actor cannot be attributed"):
        apply_task_agent_decision(store, summary_input_id=1, work_item=item, decision=decision, record_run=False)
    assert store.list_business_tasks() == ()


def test_candidate_cannot_record_committed_deadline_without_owner_acceptance(tmp_path):
    store = AutoReplyStore(tmp_path / "unaccepted-commitment-date.sqlite3")
    item = _work_item().model_copy(update={"summary": "补齐来源链接；2026-09-25"})
    decision = TaskAgentDecision.model_validate({"task_decisions": [{
        "action": "record_candidate", "transition": "none",
        "source_excerpt": "补齐来源链接", "source_ref": item.source.ref,
        "title": "报价候选", "missing_evidence": ["owner"],
        "date_evidence": [{"kind": "committed_deadline_at", "value": "2026-09-25",
            "source_ref": item.source.ref, "source_excerpt": "2026-09-25",
            "actor_user_id": "avery-id", "actor_name": "Avery"}],
    }]})

    with pytest.raises(ValueError, match="committed deadline requires owner acceptance"):
        apply_task_agent_decision(
            store, summary_input_id=1, work_item=item, decision=decision, record_run=False
        )
    assert store.list_business_tasks() == ()


def test_promoting_candidate_derives_assigned_at_from_explicit_assignment_source(tmp_path):
    store = AutoReplyStore(tmp_path / "promote-assignment-date.sqlite3")
    candidate = TaskSemanticService(store).record_candidate(RecordCandidate(
        title="提交报价", signal=SourceSignal(source_type="seed", source_ref="seed:promotion",
            evidence_text="报价", dedupe_key="seed:promotion")
    ))
    item = _work_item(assignment_authorized=True,
        owner_identity={"name": "Alex", "user_id": "alex-id"}).model_copy(update={
            "summary": "Avery formally assigns Alex on 2026-09-22."
        })
    decision = TaskAgentDecision.model_validate({"task_decisions": [{
        "action": "update_task", "transition": "promote_candidate", "task_id": candidate.task_id,
        "formal_basis": "explicit_assignment", "source_excerpt": item.summary,
        "source_ref": item.source.ref, "title": "提交报价", "owner_name": "Alex",
        "owner_evidence": {"source_ref": item.source.ref, "excerpt": item.summary,
            "name": "Alex", "user_id": "alex-id"},
    }]})

    apply_task_agent_decision(store, summary_input_id=1, work_item=item,
        decision=decision, record_run=False)

    (date_fact,) = store.list_business_task_date_evidence(candidate.task_id)
    assert (date_fact.date_type.value, date_fact.value_at, date_fact.raw_phrase) == (
        "assigned_at", item.source.created_at, item.source.created_at
    )


def test_unparseable_relative_date_stays_only_in_linked_source_evidence(tmp_path):
    store = AutoReplyStore(tmp_path / "relative-date-not-normalized.sqlite3")
    item = _work_item().model_copy(update={"summary": "提交报价，下周五前完成"})
    decision = TaskAgentDecision.model_validate({"task_decisions": [{
        "action": "record_candidate", "transition": "none", "source_excerpt": item.summary,
        "source_ref": item.source.ref, "title": "提交报价", "missing_evidence": ["owner"],
        "date_evidence": [{"kind": "requested_deadline_at", "value": "2026-09-25",
            "source_ref": item.source.ref, "source_excerpt": "下周五前"}],
    }]})

    (task_id,) = apply_task_agent_decision(store, summary_input_id=1, work_item=item,
        decision=decision, record_run=False)

    assert store.list_business_task_date_evidence(task_id) == ()
    (evidence,) = store.list_business_task_evidence(task_id)
    signal = store.get_business_task_signal(evidence.signal_id)
    assert "下周五前" in signal.evidence_text


def test_estimate_keeps_source_speaker_and_rejects_model_attribution_override(tmp_path):
    store = AutoReplyStore(tmp_path / "estimate-source-actor.sqlite3")
    item = _work_item(sender="Avery", sender_user_id="avery-id").model_copy(update={
        "summary": "Avery estimates completion on 2026-09-27."
    })
    decision = TaskAgentDecision.model_validate({"task_decisions": [{
        "action": "record_candidate", "transition": "none", "source_excerpt": item.summary,
        "source_ref": item.source.ref, "title": "报价交付", "missing_evidence": ["owner"],
        "date_evidence": [{"kind": "estimated_deadline_at", "value": "2026-09-27",
            "source_ref": item.source.ref, "source_excerpt": "2026-09-27",
            "actor_user_id": "alex-id", "actor_name": "Alex"}],
    }]})

    with pytest.raises(ValueError, match="date actor_user_id must match the trusted date actor"):
        apply_task_agent_decision(store, summary_input_id=1, work_item=item,
            decision=decision, record_run=False)
    assert store.list_business_tasks() == ()


def _assigned_formal_task_for_acceptance(store):
    service = TaskSemanticService(store)
    return service.record_formal_task(RecordFormalTask(
        title="报价方案",
        signal=SourceSignal(
            source_type="message", source_ref="message:assignment",
            evidence_text="Alex 负责报价方案", dedupe_key="assignment:exact",
            conversation_id="conversation:1", author_user_id="avery-id",
            author_name="Avery", author_kind=BusinessActorKind.HUMAN,
            context_json=json.dumps({"owner_identity": {"name": "Alex", "user_id": "alex-id"},
                                     "assignment_authorized": True}),
        ),
        formality=FormalityEvidence(
            basis=FormalTaskBasis.EXPLICIT_ASSIGNMENT, assigner_is_authorized=True,
            deliverable_is_explicit=True, owner_is_explicit=True,
        ),
        owner_user_id="alex-id", owner_name="Alex",
        owner_evidence_json=json.dumps({"source_ref": "message:assignment",
            "excerpt": "Alex 负责报价方案", "user_id": "alex-id", "name": "Alex"}),
    ))


def test_exact_owner_reply_accepts_only_cited_assignment_and_records_committed_date(tmp_path):
    store = AutoReplyStore(tmp_path / "linked-acceptance.sqlite3")
    assigned = _assigned_formal_task_for_acceptance(store)
    item = _work_item(
        sender="Alex", sender_user_id="alex-id", reply_to_source_ref="message:assignment"
    ).model_copy(update={"summary": "我接受报价方案，承诺于 2026-09-25 交付"})
    decision = TaskAgentDecision.model_validate({"task_decisions": [{
        "action": "update_task", "transition": "apply_acceptance", "task_id": assigned.task_id,
        "acceptance_polarity": "accepted", "acceptance_target_signal_id": assigned.signal_id,
        "source_excerpt": "我接受报价方案，承诺于 2026-09-25 交付", "source_ref": item.source.ref,
        "title": "报价方案",
        "date_evidence": [{"kind": "committed_deadline_at", "value": "2026-09-25",
            "source_ref": item.source.ref, "source_excerpt": "2026-09-25",
            "actor_user_id": "alex-id", "actor_name": "Alex"}],
    }]})

    result = apply_task_agent_decision(
        store, summary_input_id=1, work_item=item, decision=decision, record_run=False
    )

    assert result.task_ids == (assigned.task_id,)
    task = store.get_business_task(assigned.task_id)
    assert task.commitment_status.value == "accepted"
    (fact,) = store.list_business_task_date_evidence(assigned.task_id)
    assert (fact.date_type.value, fact.value_at, fact.raw_phrase) == (
        "committed_deadline_at", "2026-09-25", "2026-09-25"
    )
    assert (fact.source_signal_id, fact.actor_kind.value, fact.actor_user_id, fact.actor_name) == (
        result.task_ids[0] and store.list_business_task_evidence(assigned.task_id)[-1].signal_id,
        "human", "alex-id", "Alex",
    )
    [mirror_intent] = store.list_business_task_todo_sync_outbox()
    assert mirror_intent["business_task_id"] == assigned.task_id
    assert mirror_intent["operation"] == "create"


def test_source_grounded_completion_updates_task_status_without_todo_write(tmp_path):
    store = AutoReplyStore(tmp_path / "source-task-completion.sqlite3")
    assigned = _assigned_formal_task_for_acceptance(store)
    store.create_business_task_dingtalk_link(
        business_task_id=assigned.task_id,
        dingtalk_task_id="dt-task-1", status="active",
    )
    item = _work_item().model_copy(update={
        "summary": "客户验收已完成，报价方案交付物通过验收。"
    })
    decision = TaskAgentDecision.model_validate({"task_decisions": [{
        "action": "update_task", "transition": "update_fields", "task_id": assigned.task_id,
        "source_excerpt": "客户验收已完成，报价方案交付物通过验收。",
        "source_ref": item.source.ref, "title": "报价方案", "status": "done",
        "update_summary": "来源明确记录交付物通过客户验收。",
    }]})

    result = apply_task_agent_decision(
        store, summary_input_id=1, work_item=item, decision=decision, record_run=False
    )

    assert result.task_ids == (assigned.task_id,)
    assert store.get_business_task(assigned.task_id).status.value == "done"
    with store._connect() as db:
        assert db.execute("select count(*) from work_todos").fetchone()[0] == 0
    [intent] = store.list_business_task_todo_sync_outbox()
    assert intent["business_task_id"] == assigned.task_id
    assert intent["operation"] == "complete"


@pytest.mark.parametrize("reply_context", [
    {"conversation_id": "conversation:other", "reply_to_source_ref": "message:assignment"},
    {"conversation_id": "conversation:1", "reply_to_source_ref": "message:other"},
])
def test_acceptance_with_wrong_conversation_or_reply_reference_is_not_applied(tmp_path, reply_context):
    store = AutoReplyStore(tmp_path / "mismatched-acceptance.sqlite3")
    assigned = _assigned_formal_task_for_acceptance(store)
    item = _work_item(sender="Alex", sender_user_id="alex-id",
        reply_to_source_ref=reply_context["reply_to_source_ref"]).model_copy(
            update={"source": _work_item().source.model_copy(update={
                "conversation_id": reply_context["conversation_id"]
            }), "summary": "我接受报价方案"}
        )
    decision = TaskAgentDecision.model_validate({"task_decisions": [{
        "action": "update_task", "transition": "apply_acceptance", "task_id": assigned.task_id,
        "acceptance_polarity": "accepted", "acceptance_target_signal_id": assigned.signal_id,
        "source_excerpt": "我接受报价方案", "source_ref": item.source.ref, "title": "报价方案",
    }]})

    result = apply_task_agent_decision(
        store, summary_input_id=1, work_item=item, decision=decision, record_run=False
    )

    assert result.task_ids == ()
    assert result.skipped_reasons
    assert store.get_business_task(assigned.task_id).commitment_status.value == "assigned_unaccepted"
    assert store.list_business_task_date_evidence(assigned.task_id) == ()


def test_task_agent_prompt_reads_minutes_owners_from_the_conversation_around_each_item():
    """DingTalk leaves an action item's executor empty; the scanner attaches the conversation, and the prompt says how to use it."""
    prompt = build_task_agent_prompt(_work_item(), "context")

    assert "transcript_excerpts" in prompt
    assert "speaker label included" in prompt
    assert '"excerpt"' in prompt  # the owner_evidence key the service reads
    assert "发言人 N" in prompt  # DingTalk's placeholder for an unnamed speaker is not an owner


def test_update_restating_current_fields_adds_the_owner_and_an_unchanged_item_is_skipped_not_fatal(tmp_path):
    """One item that only repeats what a Task already says must not fail the meeting's other items."""
    store = AutoReplyStore(tmp_path / "restated.sqlite3")
    service = TaskSemanticService(store)

    def seed(title, key):
        return service.record_candidate(RecordCandidate(
            title=title, signal=SourceSignal(source_type="ai_minutes", source_ref=f"m:{key}#todos-sha256=old",
                evidence_text=title, dedupe_key=f"seed:{key}"),
        )).task_id

    first, second = seed("整理访谈问题清单", "a"), seed("收集用户诉求", "b")
    line = "Zoey：那这个我们可以先列一个list吧，给你看一下。"
    item = _work_item().model_copy(update={"summary": json.dumps({"transcript_excerpts": [{"lines": [line]}]}, ensure_ascii=False)})

    def update(task_id, title, **extra):
        return {
            "action": "update_task", "transition": "update_fields", "task_id": task_id,
            "source_excerpt": line, "source_ref": item.source.ref, "title": title,
            "status": "open", "business_relevance": "unknown", **extra,
        }

    decision = TaskAgentDecision.model_validate({"task_decisions": [
        update(first, "整理访谈问题清单", owner_name="Zoey", owner_evidence={"excerpt": line}),
        update(second, "收集用户诉求"),
    ]})

    result = apply_task_agent_decision(store, summary_input_id=1, work_item=item, decision=decision, record_run=False)

    assert store.get_business_task(first).owner_name == "Zoey"
    assert [event.event_type.value for event in store.list_business_task_events(first)] == ["created", "owner_changed"]
    assert store.get_business_task(second).owner_name == ""
    assert [event.event_type.value for event in store.list_business_task_events(second)] == ["created"]
    assert any(f"Task {second} already matches this source" in reason for reason in result.skipped_reasons)


def test_an_exact_owner_excerpt_from_another_line_is_kept_when_the_work_was_handed_over(tmp_path):
    """磊哥 assigns (“你写下来”) and Claire takes it on: her line, not the assigning one, names the owner."""
    store = AutoReplyStore(tmp_path / "handover.sqlite3")
    task = TaskSemanticService(store).record_candidate(RecordCandidate(
        title="结构化撰写内容产出计划",
        signal=SourceSignal(source_type="ai_minutes", source_ref="m:1#todos-sha256=old", evidence_text="结构化撰写内容产出计划", dedupe_key="seed:handover"),
    ))
    assigning, taking = "磊哥：你写下来，结构化的写下来。", "Claire：你认的话我就写下来呗。"
    item = _work_item().model_copy(update={"summary": json.dumps({"lines": [assigning, taking]}, ensure_ascii=False)})
    decision = TaskAgentDecision.model_validate({"task_decisions": [{
        "action": "update_task", "transition": "update_fields", "task_id": task.task_id,
        "source_excerpt": assigning, "source_ref": item.source.ref, "title": "结构化撰写内容产出计划",
        "owner_name": "Claire", "owner_evidence": {"source_ref": item.source.ref, "excerpt": taking},
    }]})

    apply_task_agent_decision(store, summary_input_id=1, work_item=item, decision=decision, record_run=False)

    updated = store.get_business_task(task.task_id)
    assert updated.owner_name == "Claire"
    assert json.loads(updated.owner_evidence_json)["excerpt"] == taking


def test_an_owner_the_source_does_not_establish_skips_that_item_and_not_the_whole_meeting(tmp_path):
    store = AutoReplyStore(tmp_path / "bad-owner.sqlite3")
    service = TaskSemanticService(store)

    def seed(title, key):
        return service.record_candidate(RecordCandidate(
            title=title, signal=SourceSignal(source_type="ai_minutes", source_ref=f"m:{key}#todos-sha256=old",
                evidence_text=title, dedupe_key=f"seed:{key}"),
        )).task_id

    first, second = seed("整理清单", "a"), seed("发送文档", "b")
    good, other = "Zoey：我先列一个list。", "陈思睿：好的。"
    item = _work_item().model_copy(update={"summary": json.dumps({"lines": [good, other]}, ensure_ascii=False)})

    def update(task_id, title, name, excerpt):
        return {
            "action": "update_task", "transition": "update_fields", "task_id": task_id, "title": title,
            "source_excerpt": excerpt, "source_ref": item.source.ref, "owner_name": name,
            "owner_evidence": {"source_ref": item.source.ref, "excerpt": excerpt},
        }

    decision = TaskAgentDecision.model_validate({"task_decisions": [
        update(first, "整理清单", "陈思睿", good),  # the quoted line is Zoey's: it does not name 陈思睿
        update(second, "发送文档", "陈思睿", other),
    ]})

    result = apply_task_agent_decision(store, summary_input_id=1, work_item=item, decision=decision, record_run=False)

    assert store.get_business_task(first).owner_name == ""
    assert store.get_business_task(second).owner_name == "陈思睿"
    assert any(f"Task {first} owner was not applied" in reason for reason in result.skipped_reasons)
