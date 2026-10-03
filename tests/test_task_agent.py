import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.store import AutoReplyStore
from app.agent_runtime_router import CodexCommandFactory, RoutedResultValidationError
from app.task_agent import (
    TaskAgentCodexRunner,
    TaskAgentRunner,
    apply_task_agent_decision,
    build_task_agent_prompt,
    process_work_item,
    _parse_task_agent_decision,
    _canonicalize_current_source_provenance,
    _report_project_registry_title,
    _task_result_validation_repair_prompt,
)
from app.leak_check import contains_credential, contains_local_runtime_leak
from app.task_models import TaskAgentDecision, TaskDecision, WorkItem, WorkItemSourceKind, WorkItemSourceType
from app.task_business_resolution import BusinessResolutionService
from app.task_attention_projection import AttentionProposal, BusinessAttentionProjection
from app.task_semantic_service import (
    RecordCandidate,
    RecordFormalTask,
    SourceSignal,
    TaskSemanticService,
)
from app.task_semantic_rules import FormalityEvidence
from app.task_semantic_models import (
    AttentionCategory,
    BusinessActorKind,
    BusinessRelevance,
    BusinessTaskDateType,
    FormalTaskBasis,
)
from app.task_agent_session import TaskAgentSessionLeaseLost


def test_task_agent_parser_uses_valid_result_after_failed_tool_event():
    decision = {"project_assessments": [], "update_summary": "本轮没有相关 Project。", "task_decisions": [{
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
    decision = {"project_assessments": [], "update_summary": "本轮没有相关 Project。", "task_decisions": [{
        "action": "skip", "transition": "none",
        "skip_reason": "The source is informational only.",
    }]}
    malformed = json.dumps(decision) + ',"project_assessments": [], "update_summary": "本轮没有相关 Project。", "task_decisions":[]}'

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
    decision = {"project_assessments": [], "update_summary": "本轮没有相关 Project。", "task_decisions": [{
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
    decision = {"project_assessments": [], "update_summary": "本轮没有相关 Project。", "task_decisions": [{"action": "skip", "transition": "none",
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
    assert "Any non-empty owner_name or owner_user_id requires owner_evidence" in prompt
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
        "project_assessments": [], "update_summary": "本轮没有相关 Project。", "task_decisions": [{"action": "skip", "transition": "none",
            "skip_reason": "Could not read /tmp/ceo-agent-service/todo.md"}],
    }

    prompt = _task_result_validation_repair_prompt(_agent_message_jsonl(json.dumps(decision)))

    assert "satisfied the schema but a business field contained a runtime path" in prompt
    assert "Do not include local filesystem paths" in prompt
    assert not contains_local_runtime_leak(prompt)


def test_task_result_validation_repair_prompt_caps_problem_list():
    decision = {"project_assessments": [], "update_summary": "本轮没有相关 Project。", "task_decisions": [
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


@pytest.mark.parametrize("fault,problem", [
    ("link", "new Task Attention requires"),
    ("history", "historical_comparison requires"),
    ("relation", "relation_proposals.0.direction"),
])
def test_attention_shape_omissions_use_existing_same_session_repair(fault, problem):
    import copy

    attention = {"assessment_basis": "historical_comparison", "category": "watch", "title": "交付风险",
        "why_attention": "当前延期较原计划扩大", "current_state": "验收再次延期", "ceo_action": "观察验收",
        "anchor_id": 3, "material_trigger": "risk_escalation", "evidence": [
            {"source_ref": "chat:current", "source_excerpt": "验收再次延期"},
            {"signal_id": 7, "source_ref": "report:prior", "source_excerpt": "原计划延期"}]}
    valid = {"project_assessments": [{
        "project_title": "示例项目", "anchor_id": 3, "outcome": "needs_attention",
        "reason": "当前延期较原计划扩大，可能影响交付。", "assessment_basis": "historical_comparison",
        "evidence": attention["evidence"], "decision_indexes": [0], "task_ids": [],
    }], "task_decisions": [{"action": "record_candidate", "transition": "none", "title": "核对验收计划",
        "source_ref": "chat:current", "source_excerpt": "复核示例项目验收计划", "attention_proposal": attention,
        "project_link_proposal": {"anchor_id": 3, "source_excerpt": "复核示例项目验收计划", "reason": "明确项目行动"}}]}
    if fault == "relation":
        valid["task_decisions"][0]["relation_proposals"] = [{"related_task_id": 7,
            "direction": "current_to_related", "relation_type": "supports"}]
    invalid = copy.deepcopy(valid)
    if fault == "link":
        invalid["task_decisions"][0].pop("project_link_proposal")
    elif fault == "history":
        invalid["task_decisions"][0]["attention_proposal"]["evidence"].pop()
    else:
        invalid["task_decisions"][0]["relation_proposals"][0].pop("direction")

    class RepairingExecution:
        def execute(self, **kwargs):
            retry = kwargs["result_validation_retry"]
            assert retry.resume_same_session
            assert kwargs["conversation_id"] == "contract-repair-session"
            raw = _agent_message_jsonl(json.dumps(invalid))
            with pytest.raises(RoutedResultValidationError):
                kwargs["parser"](raw)
            correction = retry.correction_prompt(raw)
            assert problem in correction
            assert "same task decision" in correction
            value = kwargs["parser"](_agent_message_jsonl(json.dumps(valid)))
            return SimpleNamespace(value=value, session_id="contract-repair-session", transcript_start=0, transcript_end=2)

    decision = TaskAgentCodexRunner(routed_execution=RepairingExecution()).decide(
        prompt="decide", workload_key="1", session_scope_id="contract-repair-session")
    assert decision == TaskAgentDecision.model_validate(valid)


@pytest.mark.parametrize("fault", ["missing_assessment", "contradictory_attention"])
def test_project_assessment_envelope_uses_one_same_session_parser_correction(fault):
    import copy

    valid = {
        "project_assessments": [{
            "project_title": "示例项目", "anchor_id": 3, "outcome": "needs_attention",
            "reason": "验收延期会影响客户上线。", "assessment_basis": "current_observation",
            "decision_indexes": [0], "task_ids": [],
            "evidence": [{"source_ref": "chat:current", "source_excerpt": "示例项目验收延期"}],
        }],
        "task_decisions": [{
            "action": "record_candidate", "transition": "none", "title": "核对验收计划",
            "source_ref": "chat:current", "source_excerpt": "示例项目验收延期",
            "project_link_proposal": {"anchor_id": 3, "source_excerpt": "示例项目验收延期", "reason": "明确项目行动"},
            "attention_proposal": {
                "assessment_basis": "current_observation", "category": "watch", "title": "验收延期",
                "why_attention": "影响客户上线", "current_state": "验收延期", "ceo_action": "观察验收",
                "anchor_id": 3, "material_trigger": "risk_escalation",
                "evidence": [{"source_ref": "chat:current", "source_excerpt": "示例项目验收延期"}],
            },
        }],
    }
    invalid = copy.deepcopy(valid)
    if fault == "missing_assessment":
        invalid.pop("project_assessments")
    else:
        invalid["project_assessments"][0]["outcome"] = "not_needed"

    class OneRepairExecution:
        execute_calls = 0
        parser_calls = 0
        correction_calls = 0
        store_calls = 0

        def execute(self, **kwargs):
            self.execute_calls += 1
            assert kwargs["conversation_id"] == "assessment-repair-session"
            retry = kwargs["result_validation_retry"]
            assert retry.resume_same_session
            raw = _agent_message_jsonl(json.dumps(invalid))
            self.parser_calls += 1
            with pytest.raises(RoutedResultValidationError):
                kwargs["parser"](raw)
            self.correction_calls += 1
            correction = retry.correction_prompt(raw)
            assert "project_assessments" in correction
            assert "one outcome, concrete reason, and original evidence" in correction
            assert "attention_proposal requires needs_attention" in correction
            assert "current source and current Tasks' confirmed Project links" in correction
            assert "not limited to structured selectors" in correction
            self.parser_calls += 1
            value = kwargs["parser"](_agent_message_jsonl(json.dumps(valid)))
            return SimpleNamespace(value=value, session_id="assessment-repair-session",
                                   transcript_start=0, transcript_end=2)

    routed = OneRepairExecution()
    decision = TaskAgentCodexRunner(routed_execution=routed).decide(
        prompt="decide", workload_key="1", session_scope_id="assessment-repair-session")

    assert decision == TaskAgentDecision.model_validate(valid)
    assert (routed.execute_calls, routed.parser_calls, routed.correction_calls, routed.store_calls) == (1, 2, 1, 0)


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
    payload = {"project_assessments": [], "update_summary": "本轮没有相关 Project。", "task_decisions": [{"action": "skip", "transition": "none",
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
            "project_assessments": [], "update_summary": "本轮没有相关 Project。", "task_decisions": [
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
        "project_assessments": [], "update_summary": "本轮没有相关 Project。", "task_decisions": [
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
    payload = {"project_assessments": [], "update_summary": "本轮没有相关 Project。", "task_decisions": [{
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


def test_process_work_item_persists_final_assessment_readback_without_rewriting_judgment(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr("app.task_agent.memory_connector_config_issue", lambda: "")
    store = AutoReplyStore(tmp_path / "task-assessment-readback.sqlite3")
    seed, anchor_id = _stored_project_task(store)
    item = _work_item()
    input_id = store.enqueue_work_summary_input(
        item.source.type.value, item.source.ref, item.model_dump_json(),
    )
    work_input = store.claim_work_summary_inputs(limit=1)[0]
    payload = {
        "project_assessments": [_stored_project_assessment(item, seed, anchor_id)],
        "task_decisions": [],
    }

    process_work_item(store, TaskAgentRunner(FakeCodex(payload)), work_input)

    with store._connect() as db:
        run = db.execute(
            "select decision_json, projection_json from task_agent_runs "
            "where summary_input_id=?",
            (input_id,),
        ).fetchone()
    stored_decision = json.loads(run["decision_json"])
    stored_projection = json.loads(run["projection_json"])
    assert stored_decision["project_assessments"][0]["outcome"] == "not_needed"
    assert stored_decision["project_assessments"][0]["reason"] == payload["project_assessments"][0]["reason"]
    assert "assessment_results" not in stored_decision
    assert stored_projection["status"] == "no_proposal"
    assert stored_projection["project_assessments"][0]["status"] == "recorded"
    assert stored_projection["project_assessments"][0]["anchor_id"] == anchor_id
    assert stored_projection["project_assessments"][0]["task_ids"] == [seed.task_id]


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
        {"project_assessments": [], "update_summary": "本轮没有相关 Project。", "task_decisions": [{"action": "skip", "transition": "none",
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
                "project_assessments": [], "update_summary": "本轮没有相关 Project。", "task_decisions": [{"action": "skip", "transition": "none",
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
    assert "`status` or `business_relevance`" in prompt
    assert "`transition` to `update_fields`" in prompt
    assert "`transition=apply_acceptance`" in prompt
    assert "do not emit `date_evidence` for dates spoken in the meeting" in prompt
    # Derek 2026-09-25: earlier session evidence and Memory provenance may be relied on.
    assert "You may rely on evidence you read earlier in this session" in prompt
    assert 'set evidence_origin to "session" or "memory"' in prompt
    assert "Prior session turns are background only" not in prompt


def test_task_agent_prompt_uses_all_source_risk_evidence_with_official_project_authority():
    prompt = " ".join(build_task_agent_prompt(_work_item(), "候选上下文为空。").split())

    assert "Reports, meetings, and chats all supply Task and risk evidence" in prompt
    assert "weekly report is neither the sole risk source nor a prerequisite" in prompt
    assert "cannot create an official Project or silently overwrite official fields" in prompt
    assert "preserve both cited sources and their times" in prompt
    assert "at most one unique assessment/card per Project per round" in prompt
    assert "current_state" in prompt and "无需你处理" in prompt
    assert "historical evidence requires a real positive persisted signal_id" in prompt
    assert "emit `project_proposal`" in prompt
    assert "explicitly decides to start, approve, 立项" in prompt
    assert "required even when the related Task is an `update_task`" in prompt
    assert "Read the complete meeting_summary, transcript_excerpts, and action item text" in prompt


def test_task_agent_prompt_requires_report_owner_rows_and_project_proposals():
    item = _work_item()
    item.source.type = WorkItemSourceType.MANAGEMENT_WEEKLY_REPORT
    item.source.ref = "dingtalk-doc:weekly#sha256=abc"
    prompt = build_task_agent_prompt(item, "候选上下文为空。")

    assert "Weekly-report source rules (non-negotiable)" in prompt
    assert "contains every\nnamed owner" in prompt
    assert "emit `project_proposal` for each named project/workstream" in prompt
    assert "authority to this source type" in prompt
    assert "`date_evidence.source_excerpt` must be copied literally" in prompt
    assert "preserving exact spaces and punctuation" in " ".join(prompt.split())


def test_action_and_date_guidance_is_delivered_next_to_output_fields(monkeypatch):
    import app.task_agent as task_agent
    from app.task_models import TaskDecision, TaskDateEvidence

    monkeypatch.setattr(task_agent, "load_skill_text", lambda paths: "")
    prompt = build_task_agent_prompt(_work_item(), "候选上下文为空。")
    action_field = TaskDecision.model_json_schema()["properties"]["source_excerpt"]
    date_fields = TaskDateEvidence.model_json_schema()["properties"]
    checks = [
        (action_field, "not Project registration scope already covered by concrete actions"),
        (date_fields["value"], "Do not move Project registry deadlines onto Tasks"),
        (date_fields["source_excerpt"], "Quote only the complete parseable date phrase"),
        (date_fields["actor_user_id"], "trusted WorkItem.context.sender_user_id"),
        (date_fields["actor_name"], "Report/document names are not date actors"),
    ]
    for field, rule in checks:
        descriptions = [field.get("description", ""), *[
            branch.get("description", "") for branch in field.get("anyOf", [])]]
        assert any(rule in description for description in descriptions)
        assert any(rule in description and description in prompt for description in descriptions)


@pytest.mark.parametrize("surface", ["prompt", "skill"])
def test_relation_and_same_action_project_link_guidance_is_delivered(monkeypatch, surface):
    import app.task_agent as task_agent
    from app.task_models import TaskProjectLinkProposal

    monkeypatch.setattr(task_agent, "load_skill_text", lambda paths: "")
    text = (build_task_agent_prompt(_work_item(), "候选上下文为空。") if surface == "prompt"
        else (Path(__file__).resolve().parents[1] / "ci/shared-skills/ceo-work-tracking/SKILL.md").read_text(encoding="utf-8"))
    text = " ".join(text.split())
    assert "Relations name the existing `related_task_id` and direction relative to this applied Task" in text
    assert "never guess a new Task's ID or use unrelated endpoints" in text
    field = TaskProjectLinkProposal.model_json_schema()["properties"]["source_excerpt"]
    assert "same-action compound quote" in field["description"]
    assert "not another paragraph or the whole report" in field["description"]


@pytest.mark.parametrize("surface", ["prompt", "skill"])
def test_action_date_source_guidance_is_consistent(monkeypatch, surface):
    import app.task_agent as task_agent

    monkeypatch.setattr(task_agent, "load_skill_text", lambda paths: "")
    text = (build_task_agent_prompt(_work_item(), "候选上下文为空。") if surface == "prompt"
        else (Path(__file__).resolve().parents[1] / "ci/shared-skills/ceo-work-tracking/SKILL.md").read_text(encoding="utf-8"))
    text = " ".join(text.split())
    assert "Task action excerpts do not originate extra Tasks from Project registration scope already covered by concrete actions" in text
    assert "Quote only the complete parseable date phrase, not a registry row" in text
    assert "Use trusted WorkItem.context.sender_user_id/sender for source-derived date actors" in text
    assert "Report/document names are not date actors" in text
    assert "Without a trusted actor or complete parseable date phrase, retain the wording in the original source without typed date_evidence" in text


@pytest.mark.parametrize("surface", ["prompt", "skill"])
def test_task_agent_comparison_assessments_cite_selective_original_history(monkeypatch, surface):
    import app.task_agent as task_agent

    monkeypatch.setattr(task_agent, "load_skill_text", lambda paths: "")
    text = (build_task_agent_prompt(_work_item(), "候选上下文为空。") if surface == "prompt"
        else (Path(__file__).resolve().parents[1] / "ci/shared-skills/ceo-work-tracking/SKILL.md").read_text(encoding="utf-8"))
    text = " ".join(text.split())
    assert "Set required `assessment_basis`" in text
    assert "requires both current null-ID and positive persisted-ID original evidence" in text
    assert "verify that comparison against the originals and use historical_comparison" in text
    assert "A current source's reference to an earlier report is a current claim, not a citation of that original report" in text
    assert "Select relevant originals, not all retrieved sources or a required source type" in text
    assert "A first assessment based only on current facts remains allowed" in text
    assert "If the original history is unavailable, mark the comparison uncertain" in text


@pytest.mark.parametrize("surface", ["prompt", "skill"])
def test_task_agent_existing_project_link_contract_is_distinct_from_registration(monkeypatch, surface):
    import app.task_agent as task_agent

    monkeypatch.setattr(task_agent, "load_skill_text", lambda paths: "")
    text = (build_task_agent_prompt(_work_item(), "候选上下文为空。") if surface == "prompt"
        else (Path(__file__).resolve().parents[1] / "ci/shared-skills/ceo-work-tracking/SKILL.md").read_text(encoding="utf-8"))
    text = " ".join(text.split())
    assert "Attention.anchor_id selects the Project assessment; it does not confirm a Task's Project link" in text
    assert "emit `project_link_proposal`" in text
    assert "derives relevant business_relevance without promoting its stage" in text
    assert "Do not set business_relevance on a new Task decision" in text
    assert "Reuse existing confirmed Task links" in text
    assert "Uncertain matches remain `anchor_match_proposals`" in text
    assert "Do not infer aliases or identity from a title prefix or similarity" in text


@pytest.mark.parametrize("action", ["record_candidate", "update_task"])
@pytest.mark.parametrize("change", [{"anchor_id": 2}, {"source_excerpt": "复核示例项目付款计划。"}])
def test_project_link_effect_identity_does_not_change_creation_task_identity(action, change):
    from app.task_agent import _task_source_signal

    item = _work_item()
    base = {"action": action, "transition": "update_fields" if action == "update_task" else "none",
        "task_id": 1 if action == "update_task" else None, "source_ref": item.source.ref,
        "source_excerpt": "复核示例项目验收及付款计划。", "title": "复核验收付款计划", "description": "当前来源补充。",
        "project_link_proposal": {"anchor_id": 1, "source_excerpt": "复核示例项目验收及付款计划。", "reason": "关联解释"}}
    def signal(payload):
        decision = TaskDecision.model_validate(payload)
        return _task_source_signal(item, decision).dedupe_key
    changed = {**base, "project_link_proposal": {**base["project_link_proposal"], **change}}
    assert (signal(base) != signal(changed)) is (action == "update_task")
    reason_only = {**base, "project_link_proposal": {**base["project_link_proposal"], "reason": "同一关联的不同解释"}}
    assert signal(base) == signal(reason_only)


@pytest.mark.parametrize("surface", ["prompt", "skill"])
def test_task_agent_registry_scope_does_not_originate_umbrella_tasks(monkeypatch, surface):
    import app.task_agent as task_agent

    monkeypatch.setattr(task_agent, "load_skill_text", lambda paths: "")
    text = (build_task_agent_prompt(_work_item(), "候选上下文为空。") if surface == "prompt"
        else (Path(__file__).resolve().parents[1] / "ci/shared-skills/ceo-work-tracking/SKILL.md").read_text(encoding="utf-8"))
    text = " ".join(text.split())
    assert "Project registration scope, objectives, and categories are not separate Tasks when concrete source actions already cover that work" in text
    assert "Keep genuine explicit actions wherever they occur in the source" in text


@pytest.mark.parametrize("surface", ["prompt", "skill"])
def test_task_agent_requires_complete_project_assessment_envelope(monkeypatch, surface):
    import app.task_agent as task_agent

    monkeypatch.setattr(task_agent, "load_skill_text", lambda paths: "")
    text = (build_task_agent_prompt(_work_item(), "候选上下文为空。") if surface == "prompt"
        else (Path(__file__).resolve().parents[1] / "ci/shared-skills/ceo-work-tracking/SKILL.md").read_text(encoding="utf-8"))
    text = " ".join(text.split())
    assert "one outcome, concrete reason, and original evidence" in text
    assert "Reports, meetings, and chats are all valid inputs" in text
    assert "A report is not the sole input or a prerequisite" in text
    assert "Candidate Tasks may support a needs_attention assessment without promotion" in text
    assert "existing_attention_id" in text
    assert "Negative assessments never close an existing card" in text
    assert "Do not fabricate a Task, Project, proposal, or ID" in text
    assert "Do not infer Project identity from aliases, prefixes, similarity, or keywords" in text
    assert "source facts separate from business inference" in text
    assert "Do not perform a whole-company or full-history scan" in text
    assert "native CLI compaction" in text
    assert "related business Project or Project clue in the current source" in text
    assert "current Tasks' confirmed Project links" in text
    assert "Emitting no Project selector does not prove that no relevant Project exists" in text
    assert "specific missing identity, Task, or risk evidence" in text
    assert "Use [] only when the current source and current Tasks contain no relevant business Project" in text


@pytest.mark.parametrize("surface", ["prompt", "skill"])
def test_current_risk_update_does_not_claim_unverified_retained_card(monkeypatch, surface):
    import app.task_agent as task_agent

    monkeypatch.setattr(task_agent, "load_skill_text", lambda paths: "")
    text = (
        build_task_agent_prompt(_work_item(), "候选上下文为空。")
        if surface == "prompt"
        else (Path(__file__).resolve().parents[1] / "ci/shared-skills/ceo-work-tracking/SKILL.md").read_text(encoding="utf-8")
    )
    text = " ".join(text.split())
    assert "existing_attention_id is an original-proof claim, not an update target" in text
    assert "its Project key reuses the existing card" in text
    assert "Leave existing_attention_id null unless you cite and verify that card's stored original evidence" in text


def test_existing_attention_schema_explains_original_proof_not_upsert_target():
    from app.task_models import TaskProjectAssessment

    description = TaskProjectAssessment.model_fields["existing_attention_id"].description
    assert "original-proof claim, not an update target" in description
    assert "Project key reuses the existing card" in description
    assert "null unless you cite and verify that card's stored original evidence" in description


@pytest.mark.parametrize("surface", ["prompt", "skill"])
def test_same_project_proposal_keeps_shared_facts_not_individual_actions(monkeypatch, surface):
    import app.task_agent as task_agent

    monkeypatch.setattr(task_agent, "load_skill_text", lambda paths: "")
    text = (
        build_task_agent_prompt(_work_item(), "候选上下文为空。")
        if surface == "prompt"
        else (Path(__file__).resolve().parents[1] / "ci/shared-skills/ceo-work-tracking/SKILL.md").read_text(encoding="utf-8")
    )
    text = " ".join(text.split())
    assert "Project-level risk facts, not per-Task action summaries" in text
    assert "Copy one Project-level proposal unchanged" in text
    assert "Task-specific actions belong in Task description or update_summary" in text


def test_attention_current_state_schema_describes_shared_project_facts():
    from app.task_models import TaskAttentionProposal

    description = TaskAttentionProposal.model_fields["current_state"].description or ""
    assert "Project-level risk facts, not per-Task action summaries" in description
    assert "identical across supporting Tasks" in description


@pytest.mark.parametrize("surface", ["prompt", "skill"])
def test_task_agent_repeats_identical_assessment_for_new_supporting_task_members(monkeypatch, surface):
    import app.task_agent as task_agent

    monkeypatch.setattr(task_agent, "load_skill_text", lambda paths: "")
    text = (build_task_agent_prompt(_work_item(), "候选上下文为空。") if surface == "prompt"
        else (Path(__file__).resolve().parents[1] / "ci/shared-skills/ceo-work-tracking/SKILL.md").read_text(encoding="utf-8"))
    text = " ".join(text.split())
    assert "Return at most one unique assessment/card per Project per round" in text
    assert "For multiple newly created Tasks supporting the same Project and risk, repeat the identical `attention_proposal` on each supporting TaskDecision" in text
    assert "Return one unique Project assessment/card per round" not in text
    assert "The service folds those identical proposals into one card and combines their Task membership" in text
    assert "Use `related_task_ids` only for real existing Task IDs; never invent IDs for new decisions" in text
    assert "Keep unrelated Project Tasks outside this assessment; conflicting proposal payloads are rejected" in text
    assert "at most one Attention proposal per Project per round" not in text


def test_task_agent_prompt_allows_initial_risk_with_project_registered_this_turn(monkeypatch):
    import app.task_agent as task_agent

    # Isolate the prompt's own contract so matching Skill text cannot mask a regression.
    monkeypatch.setattr(task_agent, "load_skill_text", lambda paths: "")
    prompt = " ".join(build_task_agent_prompt(_work_item(), "候选上下文为空。").split())

    assert "an existing confirmed official Project or a valid current-authority `project_proposal` in this same TaskDecision" in prompt
    assert "First assessment of a source-observed unresolved material business risk" in prompt
    assert "does not require a prior card or a fresh delta against a nonexistent assessment" in prompt
    assert "An existing card already reflecting the same facts does not need a new proposal" in prompt
    assert "Candidate Tasks may support Attention without a formal owner or accepted commitment" in prompt
    assert "Attention requires a registered official Project anchor plus a material trigger" not in prompt
    assert "Labels, relevance, routine progress, and date proximity alone do not explain material impact" in prompt
    assert "Never invent a Task, owner, assignment, commitment, or date to fill a card" in prompt


def test_fresh_task_agent_loads_initial_risk_rules_from_selected_skill_root(monkeypatch):
    import runpy

    code_root = Path(__file__).resolve().parents[1]
    skill_root = code_root / "ci/shared-skills"
    monkeypatch.setenv("CEO_SKILLS_ROOT", str(skill_root))
    fresh_agent = runpy.run_path(str(code_root / "app/task_agent.py"))
    assert fresh_agent["WORK_TRACKING_SKILL_PATH"] == skill_root / "ceo-work-tracking/SKILL.md"

    class CapturingCodex:
        def decide(self, **kwargs):
            self.prompt = kwargs["prompt"]
            return TaskAgentDecision(project_assessments=[], task_decisions=[],
                                     update_summary="本轮没有相关 Project。")

    codex = CapturingCodex()
    fresh_agent["TaskAgentRunner"](codex).decide(
        _work_item(), "无候选项目", run_id=11, session_scope_id="isolated-initial-risk-test"
    )
    loaded_skill = fresh_agent["WORK_TRACKING_SKILL_PATH"].read_text(encoding="utf-8")
    assert loaded_skill in codex.prompt
    skill_text = " ".join(loaded_skill.split())
    assert "an existing confirmed official Project or a valid current-authority `project_proposal` in this same TaskDecision" in skill_text
    assert "First assessment of a source-observed unresolved material business risk" in skill_text
    assert "does not require a prior card or a fresh delta against a nonexistent assessment" in skill_text
    assert "An existing card already reflecting the same facts does not need a new proposal" in skill_text
    assert "Candidate Tasks may support Attention without a formal owner or accepted commitment" in skill_text
    assert "Relevance, labels, acceptance, routine progress, and date proximity alone do not establish material impact" in skill_text
    assert "Project registration scope, objectives, and categories are not separate Tasks" in skill_text
    assert "repeat the identical `attention_proposal` on each supporting TaskDecision" in skill_text
    assert "Attention.anchor_id selects the Project assessment" in skill_text
    assert "requires both current null-ID and positive persisted-ID original evidence" in skill_text
    assert "A current source's reference to an earlier report is a current claim" in skill_text
    assert "A first assessment based only on current facts remains allowed" in skill_text
    assert "If the original history is unavailable, mark the comparison uncertain" in skill_text
    assert "verify that comparison against the originals and use historical_comparison" in skill_text
    assert "scope/content additions to an existing deliverable update that Task by its real ID" in skill_text
    assert "Quote only the complete parseable date phrase, not a registry row" in skill_text
    assert "Report/document names are not date actors" in skill_text
    assert "Relations name the existing `related_task_id` and direction relative to this applied Task" in skill_text
    assert "A complete same-action compound quote may supply the stored Project name" in skill_text


def test_task_agent_prompt_uses_scheduled_consumer_prompt_and_targeted_skill(monkeypatch):
    import app.task_agent as task_agent
    skill_path = Path(__file__).resolve().parents[1] / "ci/shared-skills/ceo-work-tracking/SKILL.md"
    monkeypatch.setattr(task_agent, "WORK_TRACKING_SKILL_PATH", skill_path)
    payload = _work_item().model_dump(mode="json")
    payload["scheduled_consumer"] = {
        "schema": "scheduled_consumer.v1",
        "scheduled_task_id": 7,
        "scheduled_task_run_id": 11,
        "prompt": "只处理 $ceo-work-tracking 能确认的真实工作项。",
        "skill_names": ["ceo-work-tracking"],
        "skill_protocol": "# Old Work Tracking Snapshot\nReturn update_project with todo_changes.",
    }

    item = WorkItem.model_validate(payload)
    original_payload = item.scheduled_consumer.copy()
    class CapturingCodex:
        def decide(self, **kwargs):
            self.prompt = kwargs["prompt"]
            return TaskAgentDecision(project_assessments=[], task_decisions=[],
                                     update_summary="本轮没有相关 Project。")
    codex = CapturingCodex()
    TaskAgentRunner(codex).decide(item, "无候选项目", run_id=11, session_scope_id="isolated-test")
    prompt = codex.prompt

    assert "## Scheduled Consumer Prompt" in prompt
    assert "只处理 $ceo-work-tracking 能确认的真实工作项。" in prompt
    assert "# Old Work Tracking Snapshot" not in prompt
    assert "Return update_project with todo_changes." not in prompt
    assert "Scheduled Consumer Skill Snapshot" not in prompt
    assert '"scheduled_task_run_id": 11' in prompt
    assert "# CEO Work Tracking" in prompt
    assert "version: 3" in prompt
    assert "## Shared Source-Driven Task Session" in prompt
    assert "## TODO Completion Discovery" not in prompt
    assert "are not currently applied by the service" in prompt
    assert "native CLI manages compaction" in prompt
    assert item.scheduled_consumer == original_payload
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
    update = {"project_assessments": [], "update_summary": "本轮没有相关 Project。", "task_decisions": [{"action": "skip", "transition": "none",
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
    payload = {"project_assessments": [], "update_summary": "本轮没有相关 Project。", "task_decisions": [
        {"action": "record_candidate", "transition": "none",
         "source_excerpt": "补齐来源链接", "source_ref": item.source.ref,
         "title": "有效候选", "missing_evidence": ["owner"]},
        {"action": "record_candidate", "transition": "none",
         "source_excerpt": "另一个来源里的话", "source_ref": "other-source-ref",
         "title": "必须回滚", "missing_evidence": ["owner"]},
    ]}
    codex = FakeCodexWithAuditEvents(payload, [])

    process_work_item(store, TaskAgentRunner(codex), work_input)

    assert len(store.list_business_tasks()) == 2
    with sqlite3.connect(tmp_path / "task-batch-failure.sqlite3") as db:
        input_status = db.execute(
            "select status from work_summary_inputs where id=?", (input_id,)
        ).fetchone()[0]
        run = db.execute(
            "select status, error from task_agent_runs where summary_input_id=?",
            (input_id,),
        ).fetchone()
    assert input_status == "done"
    assert run[0] == "completed"
    assert run[1] == ""







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
        {"project_assessments": [], "update_summary": "本轮没有相关 Project。", "task_decisions": [{"action": "skip", "transition": "none",
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
                "project_assessments": [], "update_summary": "本轮没有相关 Project。", "task_decisions": [{"action": "skip", "transition": "none", "skip_reason": "没有状态变化"}]
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
                                                "project_assessments": [], "update_summary": "本轮没有相关 Project。", "task_decisions": [{"action": "skip", "transition": "none",
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
    schema_text = prompt.split("TaskAgentDecision Pydantic JSON schema:\n", 1)[1]
    prompt_schema, _ = json.JSONDecoder().raw_decode(schema_text.lstrip())

    assert prompt_schema == TaskAgentDecision.model_json_schema()




def test_task_agent_codex_runner_uses_routed_execution_contract():
    routed = FakeRoutedTaskExecution(
        json.dumps(
            {
                "project_assessments": [], "update_summary": "本轮没有相关 Project。", "task_decisions": [{"action": "skip", "transition": "none",
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
            {"project_assessments": [], "update_summary": "本轮没有相关 Project。", "task_decisions": [{"action": "skip", "transition": "none",
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
    decision = {"project_assessments": [], "update_summary": "本轮没有相关 Project。", "task_decisions": [{"action": "skip", "transition": "none",
        "skip_reason": "No source-grounded task was found."}]}
    message = (
        "Based on my search within the allowed sources, I found:\n\n"
        "1. **Memory**: background only {not a decision}.\n\n"
        + json.dumps({"project_assessments": [], "update_summary": "本轮没有相关 Project。", "task_decisions": []})
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
    return TaskAgentDecision.model_validate({"project_assessments": [], "update_summary": "本轮没有相关 Project。", "task_decisions": [{
        "action": "record_candidate", "transition": "none",
        "source_excerpt": excerpt, "source_ref": item.source.ref,
        "title": title, "missing_evidence": ["owner"],
    }]})


def test_current_ai_minutes_provenance_is_canonical_and_not_external_todo():
    base = _work_item()
    item = base.model_copy(update={"source": base.source.model_copy(update={
        "type": WorkItemSourceType.AI_MINUTES,
        "ref": "minutes:1#todos-sha256=abc",
    })})
    decision = TaskAgentDecision.model_validate({"project_assessments": [],
        "update_summary": "本轮没有相关 Project。", "task_decisions": [{
        "action": "create_task", "transition": "none",
        "source_excerpt": "补齐来源链接", "source_ref": "wrong-ref",
        "title": "补齐报价来源链接", "formal_basis": "external_todo",
        "owner_name": "Alex", "owner_evidence": {
            "source_ref": "wrong-ref", "excerpt": "Alex 负责补齐来源链接"
        },
    }]})

    normalized = _canonicalize_current_source_provenance(decision, work_item=item)
    normalized_item = normalized.task_decisions[0]
    assert normalized_item.source_ref == item.source.ref
    assert normalized_item.owner_evidence["source_ref"] == item.source.ref
    assert normalized_item.formal_basis is FormalTaskBasis.MEETING_ACTION_ITEM


def test_current_ai_minutes_commitment_is_canonicalized_to_meeting_action_item():
    base = _work_item()
    item = base.model_copy(update={
        "source": base.source.model_copy(update={
            "type": WorkItemSourceType.AI_MINUTES,
            "ref": "minutes:health-metrics#todos-sha256=abc",
        }),
        "context": base.context.model_copy(update={
            "source_conversation_kind": WorkItemSourceKind.MINUTES,
        }),
    })
    decision = TaskAgentDecision.model_validate({"project_assessments": [], "update_summary": "本轮没有相关 Project。", "task_decisions": [{
        "action": "create_task", "transition": "none",
        "source_excerpt": "张玲玲更新数据",
        "source_ref": item.source.ref,
        "title": "更新健康度数据",
        "formal_basis": "explicit_commitment",
        "owner_name": "张玲玲",
        "owner_kind": "individual",
        "owner_relation": "self_commitment",
        "owner_evidence": {"source_ref": item.source.ref, "excerpt": "张玲玲更新数据"},
    }]})
    normalized = _canonicalize_current_source_provenance(decision, work_item=item)
    assert normalized.task_decisions[0].formal_basis is FormalTaskBasis.MEETING_ACTION_ITEM


def test_current_ai_minutes_assignment_is_canonicalized_to_meeting_action_item():
    base = _work_item()
    item = base.model_copy(update={
        "source": base.source.model_copy(update={
            "type": WorkItemSourceType.AI_MINUTES,
            "ref": "minutes:health-metrics#todos-sha256=abc",
        }),
        "context": base.context.model_copy(update={
            "source_conversation_kind": WorkItemSourceKind.MINUTES,
        }),
    })
    decision = TaskAgentDecision.model_validate({"project_assessments": [], "update_summary": "本轮没有相关 Project。", "task_decisions": [{
        "action": "create_task", "transition": "none",
        "source_excerpt": "张玲玲更新数据",
        "source_ref": item.source.ref,
        "title": "更新健康度数据",
        "formal_basis": "explicit_assignment",
        "owner_name": "张玲玲",
        "owner_kind": "individual",
        "owner_relation": "explicit_assignment",
        "owner_evidence": {"source_ref": item.source.ref, "excerpt": "张玲玲更新数据"},
    }]})
    normalized = _canonicalize_current_source_provenance(decision, work_item=item)
    assert normalized.task_decisions[0].formal_basis is FormalTaskBasis.MEETING_ACTION_ITEM


@pytest.mark.parametrize(
    "owner_kind, owner_relation, expected_action",
    [
        ("individual", "explicit_assignment", "create_task"),
        ("individual", "self_commitment", "create_task"),
        ("individual", "meeting_summary_action_item", "create_task"),
        ("team", "explicit_assignment", "record_candidate"),
        ("individual", "speaker_only", "record_candidate"),
    ],
)
def test_ai_minutes_owner_relation_controls_formal_task_promotion(
    owner_kind, owner_relation, expected_action
):
    base = _work_item()
    item = base.model_copy(
        update={
            "source": base.source.model_copy(
                update={
                    "type": WorkItemSourceType.AI_MINUTES,
                    "ref": "minutes:marketing#todos-sha256=abc",
                }
            ),
            "context": base.context.model_copy(
                update={"source_conversation_kind": WorkItemSourceKind.MINUTES}
            ),
        }
    )
    decision = TaskAgentDecision.model_validate(
        {
            "project_assessments": [], "update_summary": "本轮没有相关 Project。", "task_decisions": [
                {
                    "action": "record_candidate",
                    "transition": "none",
                    "source_excerpt": "明天补齐技术问题并预约沟通",
                    "source_ref": "wrong-ref",
                    "title": "补齐技术问题并预约沟通",
                    "owner_name": "Alex",
                    "owner_evidence": {
                        "source_ref": "wrong-ref",
                        "excerpt": "Alex：我来负责补齐技术问题并预约沟通",
                    },
                    "owner_kind": owner_kind,
                    "owner_relation": owner_relation,
                }
            ]
        }
    )

    normalized = _canonicalize_current_source_provenance(decision, work_item=item)

    normalized_item = normalized.task_decisions[0]
    assert normalized_item.action == expected_action
    if expected_action == "create_task":
        assert normalized_item.formal_basis is FormalTaskBasis.MEETING_ACTION_ITEM
    else:
        assert normalized_item.formal_basis is None


def test_session_provenance_is_not_rewritten():
    item = _work_item()
    decision = TaskAgentDecision.model_validate({"project_assessments": [], "update_summary": "本轮没有相关 Project。", "task_decisions": [{
        "action": "record_candidate", "transition": "none",
        "evidence_origin": "session", "source_excerpt": "旧来源证据",
        "source_ref": "message:original", "source_description": "群聊 / Avery",
        "title": "候选任务", "missing_evidence": ["owner"],
    }]})

    normalized = _canonicalize_current_source_provenance(decision, work_item=item)
    assert normalized.task_decisions[0].source_ref == "message:original"


def test_parser_accepts_zero_to_many_task_decisions():
    assert _parse_task_agent_decision('{"project_assessments": [], "update_summary": "本轮没有相关 Project。", "task_decisions": []}').task_decisions == []
    decision = _parse_task_agent_decision(json.dumps({"project_assessments": [], "update_summary": "本轮没有相关 Project。", "task_decisions": [
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
    decision = TaskAgentDecision.model_validate({"project_assessments": [],
        "update_summary": "本轮没有相关 Project。", "task_decisions": [
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


def test_explicit_meeting_project_proposal_registers_project_and_links_task(tmp_path):
    store = AutoReplyStore(tmp_path / "meeting-project.sqlite3")
    base = _work_item(assignment_authorized=True, source_conversation_kind="minutes")
    item = base.model_copy(update={
        "source": base.source.model_copy(update={"type": WorkItemSourceType.AI_MINUTES}),
        "summary": json.dumps({"meeting": {"summary":
            "会议明确决定启动美国客户成交项目，并由 Alex 负责报价"}}, ensure_ascii=True),
    })
    decision = TaskAgentDecision.model_validate({"project_assessments": [{
        "project_title": "美国客户成交", "project_decision_index": 0,
        "outcome": "not_needed", "reason": "本轮为正常立项和报价行动，没有额外重大风险。",
        "assessment_basis": "current_observation", "decision_indexes": [0], "task_ids": [],
        "evidence": [{"source_ref": item.source.ref, "source_excerpt": "会议明确决定启动美国客户成交项目，并由 Alex 负责报价"}],
    }], "task_decisions": [{
        "action": "create_task", "transition": "none",
        "source_excerpt": "会议明确决定启动美国客户成交项目，并由 Alex 负责报价",
        "source_ref": item.source.ref, "title": "准备美国客户报价",
        "formal_basis": "explicit_assignment", "owner_name": "Alex",
        "owner_evidence": {"source_ref": item.source.ref, "excerpt": "Alex 负责报价"},
        "project_proposal": {
            "title": "美国客户成交", "reason": "会议明确决定启动该项目",
            "authority": "meeting_decision",
            "source_excerpt": "会议明确决定启动美国客户成交项目，并由 Alex 负责报价",
        },
    }]})

    result = apply_task_agent_decision(store, summary_input_id=1, work_item=item, decision=decision, record_run=False)

    assert len(result.task_ids) == 1
    projects = store.list_business_projects()
    assert len(projects) == 1
    assert projects[0].title == "美国客户成交"
    assert projects[0].registry_source.startswith("meeting_decision:")
    assert [link.anchor_id for link in store.list_business_task_anchor_links(task_id=result.task_ids[0])] == [
        projects[0].canonical_anchor_id
    ]
    assert store.get_business_task(result.task_ids[0]).business_relevance.value == "relevant"

    replay = apply_task_agent_decision(store, summary_input_id=2, work_item=item, decision=decision, record_run=False)
    assert replay.task_ids == result.task_ids
    assert len(store.list_business_projects()) == 1
    assert len(store.list_business_task_anchor_links(task_id=result.task_ids[0])) == 1

    second_item = item.model_copy(update={
        "source": item.source.model_copy(update={"ref": "minutes:second"}),
    })
    second_decision = decision.model_copy(update={
        "project_assessments": [decision.project_assessments[0].model_copy(update={
            "evidence": [decision.project_assessments[0].evidence[0].model_copy(update={
                "source_ref": second_item.source.ref,
            })],
        })],
        "task_decisions": [decision.task_decisions[0].model_copy(update={
            "source_ref": second_item.source.ref,
            "owner_evidence": {
                "source_ref": second_item.source.ref,
                "excerpt": "Alex 负责报价",
            },
        })],
    })
    second_result = apply_task_agent_decision(
        store, summary_input_id=3, work_item=second_item, decision=second_decision, record_run=False
    )
    assert len(store.list_business_projects()) == 1
    assert len(store.list_business_task_anchor_links(task_id=second_result.task_ids[0])) == 1


def test_generic_department_project_proposal_is_not_promoted(tmp_path):
    store = AutoReplyStore(tmp_path / "generic-project.sqlite3")
    item = _work_item(assignment_authorized=True)
    decision = TaskAgentDecision.model_validate({"project_assessments": [{
        "project_title": "项目管理部", "project_decision_index": 0,
        "outcome": "not_needed", "reason": "该标题不构成有效 Project 登记，且没有重大风险。",
        "assessment_basis": "current_observation", "decision_indexes": [0], "task_ids": [],
        "evidence": [{"source_ref": item.source.ref, "source_excerpt": "补齐来源链接"}],
    }], "task_decisions": [{
        "action": "create_task", "transition": "none",
        "source_excerpt": "补齐来源链接；owner 是 Alex。",
        "source_ref": item.source.ref, "title": "补齐交付清单",
        "formal_basis": "explicit_assignment", "owner_name": "Alex",
        "owner_evidence": {"source_ref": item.source.ref, "excerpt": "owner 是 Alex"},
        "project_proposal": {
            "title": "项目管理部", "reason": "报告章节标题", "authority": "project_weekly_report",
            "source_excerpt": "项目管理部",
        },
    }]})

    with pytest.raises(ValueError, match="cited report registry row"):
        apply_task_agent_decision(store, summary_input_id=1, work_item=item,
                                  decision=decision, record_run=False)
    assert store.list_business_projects() == []


def test_weekly_report_task_section_project_proposal_is_not_promoted(tmp_path):
    store = AutoReplyStore(tmp_path / "report-task-section-project.sqlite3")
    item = _work_item(assignment_authorized=True).model_copy(update={
        "source": _work_item().source.model_copy(update={
            "type": WorkItemSourceType.PROJECT_WEEKLY_REPORT,
        }),
    })
    decision = TaskAgentDecision.model_validate({"project_assessments": [{
        "project_title": "中汽对账", "project_decision_index": 0,
        "outcome": "not_needed", "reason": "任务章节不构成有效 Project 登记，且没有重大风险。",
        "assessment_basis": "current_observation", "decision_indexes": [0], "task_ids": [],
        "evidence": [{"source_ref": item.source.ref, "source_excerpt": "补齐来源链接"}],
    }], "task_decisions": [{
        "action": "create_task", "transition": "none",
        "source_excerpt": "补齐来源链接；owner 是 Alex。",
        "source_ref": item.source.ref, "title": "中汽对账",
        "formal_basis": "explicit_assignment", "owner_name": "Alex",
        "owner_evidence": {"source_ref": item.source.ref, "excerpt": "owner 是 Alex"},
        "project_proposal": {
            "title": "中汽对账", "reason": "周报将“中汽对账”列为下周工作重点",
            "authority": "project_weekly_report",
            "source_excerpt": "中汽对账",
        },
    }]})

    with pytest.raises(ValueError, match="cited report registry row"):
        apply_task_agent_decision(store, summary_input_id=1, work_item=item,
                                  decision=decision, record_run=False)
    assert store.list_business_projects() == []


def test_project_weekly_report_registry_row_becomes_official_project(tmp_path):
    store = AutoReplyStore(tmp_path / "report-project.sqlite3")
    row = "| 大众底盘采集 | 100 张内部试标 | 下周起量 | 下周 | ⌛️进行中 |"
    item = _work_item().model_copy(update={
        "source": _work_item().source.model_copy(update={
            "type": WorkItemSourceType.PROJECT_WEEKLY_REPORT,
            "ref": "report:project-weekly",
        }),
        "summary": json.dumps({
            "report": {"title": "项目周报"},
            "markdown": f"## **手头项目**\n\n| 项目名 | 负责内容 |\n|---|---|\n{row}\n",
        }, ensure_ascii=False),
    })
    decision = TaskAgentDecision.model_validate({"project_assessments": [{
        "project_title": "大众底盘采集", "project_decision_index": 0,
        "outcome": "not_needed", "reason": "登记行显示正常推进，没有需要 CEO 关注的重大影响。",
        "assessment_basis": "current_observation", "decision_indexes": [0], "task_ids": [],
        "evidence": [{"source_ref": item.source.ref, "source_excerpt": row}],
    }], "task_decisions": [{
        "action": "record_candidate", "transition": "none",
        "source_excerpt": row, "source_ref": item.source.ref,
        "title": "完成大众底盘采集内部试标、规则固化、数据打包与算法预标注",
        "missing_evidence": ["owner"],
        "project_proposal": {"title": "大众底盘采集", "reason": "登记表列出项目",
                             "authority": "project_weekly_report", "source_excerpt": row},
    }]})

    assert _report_project_registry_title(item, decision.task_decisions[0].project_proposal.source_excerpt) == "大众底盘采集"
    result = apply_task_agent_decision(store, summary_input_id=1, work_item=item,
                                       decision=decision, record_run=False)

    assert len(result.task_ids) == 1
    projects = store.list_business_projects()
    assert [project.title for project in projects] == ["大众底盘采集"]
    with store._connect() as db:
        assert db.execute("select count(*) from business_project_candidates").fetchone()[0] == 0
    assert [project.id for project in store.list_business_task_project_links(task_id=result.task_ids[0])] == [projects[0].id]


def test_management_weekly_report_registry_row_becomes_official_project(tmp_path):
    store = AutoReplyStore(tmp_path / "management-report-project.sqlite3")
    row = "| 陈凯 | 江淮私有化 | 本周交付与合同边界确认 | ⌛️进行中 |"
    item = _work_item().model_copy(update={
        "source": _work_item().source.model_copy(update={
            "type": WorkItemSourceType.MANAGEMENT_WEEKLY_REPORT,
            "ref": "report:management-weekly",
        }),
        "summary": json.dumps({
            "report": {"title": "管理周报"},
            "markdown": f"## **手头项目**\n\n| 负责人 | 项目 | 当前状态 |\n|---|---|---|\n{row}\n",
        }, ensure_ascii=False),
    })
    decision = TaskAgentDecision.model_validate({"project_assessments": [{
        "project_title": "江淮私有化", "project_decision_index": 0,
        "outcome": "not_needed", "reason": "登记行显示正常推进，没有需要 CEO 关注的重大影响。",
        "assessment_basis": "current_observation", "decision_indexes": [0], "task_ids": [],
        "evidence": [{"source_ref": item.source.ref, "source_excerpt": row}],
    }], "task_decisions": [{
        "action": "record_candidate", "transition": "none",
        "source_excerpt": row, "source_ref": item.source.ref,
        "title": "完成江淮私有化本周交付和合同边界确认",
        "missing_evidence": ["owner"],
        "project_proposal": {"title": "江淮私有化", "reason": "登记表列出项目",
                             "authority": "management_weekly_report", "source_excerpt": row},
    }]})

    result = apply_task_agent_decision(store, summary_input_id=1, work_item=item,
                                       decision=decision, record_run=False)

    assert len(result.task_ids) == 1
    assert [project.title for project in store.list_business_projects()] == ["江淮私有化"]


def test_weekly_report_registry_accepts_excerpt_without_leading_pipe(tmp_path):
    store = AutoReplyStore(tmp_path / "report-project-unwrapped-row.sqlite3")
    row = "标注工厂（大同标注基地） | 亏损类型止损和后续产能确认 | 停止 OD，仅保留可持平或盈利的 LD、OCC | 待客户确认 | ⌛️待确认"
    item = _work_item().model_copy(update={
        "source": _work_item().source.model_copy(update={
            "type": WorkItemSourceType.PROJECT_WEEKLY_REPORT,
            "ref": "report:project-weekly-unwrapped",
        }),
        "summary": json.dumps({
            "report": {"title": "项目周报"},
            "markdown": "## **手头项目**\n\n| 项目名 | 负责内容 | 目标 | DDL | 状态 |\n|---|---|---|---|---|\n| " + row + " |\n",
        }, ensure_ascii=False),
    })
    decision = TaskAgentDecision.model_validate({"project_assessments": [{
        "project_title": "标注工厂（大同标注基地）", "project_decision_index": 0,
        "outcome": "not_needed", "reason": "本测试只验证登记行解析，不新增 Attention 提案。",
        "assessment_basis": "current_observation", "decision_indexes": [0], "task_ids": [],
        "evidence": [{"source_ref": item.source.ref, "source_excerpt": row}],
    }], "task_decisions": [{
        "action": "record_candidate", "transition": "none",
        "source_excerpt": row, "source_ref": item.source.ref,
        "title": "停止大同标注基地亏损业务类型并确认后续产能量级",
        "missing_evidence": ["owner"],
        "project_proposal": {"title": "标注工厂（大同标注基地）", "reason": "登记表列出项目",
                             "authority": "project_weekly_report", "source_excerpt": row},
    }]})

    assert _report_project_registry_title(item, decision.task_decisions[0].project_proposal.source_excerpt) == "标注工厂（大同标注基地）"
    result = apply_task_agent_decision(store, summary_input_id=1, work_item=item,
                                       decision=decision, record_run=False)

    assert len(result.task_ids) == 1
    assert [project.title for project in store.list_business_projects()] == ["标注工厂（大同标注基地）"]


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
    decisions = [TaskAgentDecision.model_validate({"project_assessments": [], "update_summary": "本轮没有相关 Project。", "task_decisions": [{
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
    first = TaskAgentDecision.model_validate({"project_assessments": [], "update_summary": "本轮没有相关 Project。", "task_decisions": [{
        **base, "description": "Prepare the report for launch.", "update_summary": "Owner confirmed."
    }]})
    replay = TaskAgentDecision.model_validate({"project_assessments": [], "update_summary": "本轮没有相关 Project。", "task_decisions": [{
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
    without_date = TaskAgentDecision.model_validate({"project_assessments": [], "update_summary": "本轮没有相关 Project。", "task_decisions": [common]})
    with_new_date = TaskAgentDecision.model_validate({"project_assessments": [], "update_summary": "本轮没有相关 Project。", "task_decisions": [{
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
        decision = TaskAgentDecision.model_validate({"project_assessments": [], "update_summary": "本轮没有相关 Project。", "task_decisions": [{
            "action": "update_task", "transition": "update_fields", "task_id": task.task_id,
            "source_excerpt": item.summary, "source_ref": item.source.ref,
            "title": "报价跟进", "status": status,
        }]})
        apply_task_agent_decision(store, summary_input_id=1, work_item=item,
            decision=decision, record_run=False)

    assert store.get_business_task(task.task_id).status.value == "done"
    assert len(store.list_business_task_signals()) == 3


@pytest.mark.parametrize("change", ["owner", "status", "unchanged", "promotion"])
def test_existing_task_update_can_omit_title(tmp_path, change):
    store = AutoReplyStore(tmp_path / "title-update.sqlite3")
    seed = TaskSemanticService(store).record_candidate(RecordCandidate(
        title="提交报价", signal=SourceSignal(source_type="seed", source_ref="seed:title",
            evidence_text="提交报价", dedupe_key="seed:title")))
    item = _work_item(assignment_authorized=True,
        owner_identity={"name": "Alex", "user_id": "alex-id"}).model_copy(update={
            "summary": "Avery assigns Alex to submit the quote; the quote is waiting."})
    payload = {"action": "update_task", "transition": "update_fields", "task_id": seed.task_id,
        "source_ref": item.source.ref, "source_excerpt": item.summary}
    if change in {"owner", "promotion"}:
        payload.update(owner_name="Alex", owner_evidence={"source_ref": item.source.ref,
            "excerpt": item.summary, "name": "Alex", "user_id": "alex-id"})
    if change == "status":
        payload["status"] = "waiting"
    if change == "promotion":
        payload.update(transition="promote_candidate", formal_basis="explicit_assignment")
    result = apply_task_agent_decision(store, summary_input_id=1, work_item=item,
        decision=TaskAgentDecision.model_validate({"project_assessments": [], "update_summary": "本轮没有相关 Project。", "task_decisions": [payload]}), record_run=False)
    task = store.get_business_task(seed.task_id)
    assert task.title == "提交报价"
    if change == "owner":
        assert task.owner_name == "Alex"
    elif change == "status":
        assert task.status.value == "waiting"
    elif change == "promotion":
        assert task.stage.value == "formal"
        assert task.owner_user_id == "alex-id"
        assert task.formal_basis is FormalTaskBasis.EXPLICIT_ASSIGNMENT
    else:
        assert result.skipped_reasons
        assert len(store.list_business_task_signals()) == 1


@pytest.mark.parametrize("action", ["record_candidate", "create_task"])
@pytest.mark.parametrize("title", [None, "", "   "])
def test_new_task_requires_title_in_decision_shape(action, title):
    payload = {"action": action, "transition": "none", "source_ref": "source:1",
        "source_excerpt": "Submit the quote"}
    if title is not None:
        payload["title"] = title
    if action == "create_task":
        payload["formal_basis"] = "explicit_assignment"
    with pytest.raises(ValidationError, match="new task decision requires title"):
        TaskAgentDecision.model_validate({"project_assessments": [], "update_summary": "本轮没有相关 Project。", "task_decisions": [payload]})


def test_update_whitespace_title_rejected_in_shape_before_domain_write(tmp_path):
    store = AutoReplyStore(tmp_path / "whitespace-title.sqlite3")
    seed = TaskSemanticService(store).record_candidate(RecordCandidate(
        title="提交报价", signal=SourceSignal(source_type="seed", source_ref="seed:whitespace",
            evidence_text="提交报价", dedupe_key="seed:whitespace")))
    original = store.get_business_task(seed.task_id)
    signals = store.list_business_task_signals()
    payload = {"action": "update_task", "transition": "update_fields", "task_id": seed.task_id,
        "source_ref": "source:update", "source_excerpt": "报价等待回复", "title": "   ", "status": "waiting"}
    with pytest.raises(ValidationError, match="provided update title must be nonblank"):
        TaskAgentDecision.model_validate({"project_assessments": [], "update_summary": "本轮没有相关 Project。", "task_decisions": [payload]})
    assert store.get_business_task(seed.task_id) == original
    assert store.list_business_task_signals() == signals


@pytest.mark.parametrize("title", [None, "", "核对报价"])
def test_update_title_boundary_preserves_omitted_empty_or_applies_valid_title(tmp_path, title):
    store = AutoReplyStore(tmp_path / "title-boundary.sqlite3")
    seed = TaskSemanticService(store).record_candidate(RecordCandidate(
        title="提交报价", signal=SourceSignal(source_type="seed", source_ref="seed:boundary",
            evidence_text="提交报价", dedupe_key="seed:boundary")))
    item = _work_item().model_copy(update={"summary": "报价等待客户回复"})
    payload = {"action": "update_task", "transition": "update_fields", "task_id": seed.task_id,
        "source_ref": item.source.ref, "source_excerpt": item.summary, "status": "waiting"}
    if title is not None:
        payload["title"] = title
    apply_task_agent_decision(store, summary_input_id=1, work_item=item,
        decision=TaskAgentDecision.model_validate({"project_assessments": [], "update_summary": "本轮没有相关 Project。", "task_decisions": [payload]}), record_run=False)
    task = store.get_business_task(seed.task_id)
    assert task.title == (title if title else "提交报价")
    assert task.status.value == "waiting"


def test_missing_new_task_title_uses_existing_same_session_repair():
    valid = {"project_assessments": [], "update_summary": "本轮没有相关 Project。", "task_decisions": [{"action": "record_candidate", "transition": "none",
        "source_ref": "source:1", "source_excerpt": "Submit the quote", "title": "Submit quote"}]}
    invalid = {"project_assessments": [], "update_summary": "本轮没有相关 Project。", "task_decisions": [{key: value for key, value in valid["task_decisions"][0].items()
        if key != "title"}]}

    class RepairingExecution:
        def execute(self, **kwargs):
            retry = kwargs["result_validation_retry"]
            assert retry.resume_same_session
            raw = _agent_message_jsonl(json.dumps(invalid))
            with pytest.raises(RoutedResultValidationError):
                kwargs["parser"](raw)
            assert "new task decision requires title" in retry.correction_prompt(raw)
            value = kwargs["parser"](_agent_message_jsonl(json.dumps(valid)))
            return SimpleNamespace(value=value, session_id="title-repair", transcript_start=0, transcript_end=2)

    decision = TaskAgentCodexRunner(routed_execution=RepairingExecution()).decide(
        prompt="decide", workload_key="1", session_scope_id="title-repair")
    assert decision == TaskAgentDecision.model_validate(valid)


def test_process_work_item_commits_titleless_update_and_new_candidate_batch(tmp_path):
    store = AutoReplyStore(tmp_path / "title-batch.sqlite3")
    seed = TaskSemanticService(store).record_candidate(RecordCandidate(
        title="提交报价", signal=SourceSignal(source_type="seed", source_ref="seed:batch",
            evidence_text="提交报价", dedupe_key="seed:batch")))
    item = _work_item().model_copy(update={"summary": "报价等待客户回复；另需准备独立演示。"})
    input_id = store.enqueue_work_summary_input(item.source.type.value, item.source.ref, item.model_dump_json())
    work_input, = store.claim_work_summary_inputs(limit=1)
    decision = {"project_assessments": [], "update_summary": "本轮没有相关 Project。", "task_decisions": [
        {"action": "update_task", "transition": "update_fields", "task_id": seed.task_id,
         "source_ref": item.source.ref, "source_excerpt": item.summary, "status": "waiting"},
        {"action": "record_candidate", "transition": "none", "title": "准备独立演示",
         "source_ref": item.source.ref, "source_excerpt": "另需准备独立演示"},
    ]}
    process_work_item(store, TaskAgentRunner(FakeCodex(decision)), work_input)
    old = store.get_business_task(seed.task_id)
    assert (old.title, old.status.value) == ("提交报价", "waiting")
    assert {task.title for task in store.list_business_tasks()} == {"提交报价", "准备独立演示"}
    with store._connect() as db:
        assert db.execute("select status from work_summary_inputs where id=?", (input_id,)).fetchone()[0] == "done"
        assert db.execute("select status from task_agent_runs where summary_input_id=?", (input_id,)).fetchone()[0] == "completed"


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
    decision = TaskAgentDecision.model_validate({"project_assessments": [], "update_summary": "本轮没有相关 Project。", "task_decisions": [{
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


def test_owner_evidence_keeps_the_citation_the_agent_gave(tmp_path):
    """Derek 2026-09-25: a citation is a sentence from the source; it need not be word for word."""
    store = AutoReplyStore(tmp_path / "owner-evidence-excerpt.sqlite3")
    item = _work_item(
        assignment_authorized=True,
    ).model_copy(update={"summary": "Alex 负责提交周报，周五前完成。"})
    decision = TaskAgentDecision.model_validate({"project_assessments": [], "update_summary": "本轮没有相关 Project。", "task_decisions": [{
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
    assert evidence["excerpt"] == "Alex负责提交周报"


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
    decision = TaskAgentDecision.model_validate({"project_assessments": [], "update_summary": "本轮没有相关 Project。", "task_decisions": [{
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


@pytest.mark.parametrize("omit_title", [False, True])
def test_same_external_task_id_can_support_identity_merge_and_recompute_both_tasks(tmp_path, monkeypatch, omit_title):
    from app.task_agent import BusinessAttentionProjection

    store = AutoReplyStore(tmp_path / "external-id-merge.sqlite3")
    first = _seed_identity_task(store, "message:external-1", external_task_id="dingtalk:task-44")
    second = _seed_identity_task(store, "message:external-2", external_task_id="dingtalk:task-44")
    item = _work_item().model_copy(update={"summary": "同步外部待办记录"})
    decision = TaskAgentDecision.model_validate({"project_assessments": [], "update_summary": "本轮没有相关 Project。", "task_decisions": [{
        "action": "update_task", "transition": "merge_identity", "task_id": first.task_id,
        "target_task_id": second.task_id, "source_excerpt": "同步外部待办记录",
        "source_ref": item.source.ref, "title": "提交周报",
        "identity_proposal": {"source_task_id": first.task_id, "target_task_id": second.task_id,
            "reason": "Same external task ID dingtalk:task-44",
            "identity_evidence": {"basis": "same_external_task_id",
                "source_signal_id": first.signal_id, "target_signal_id": second.signal_id}},
    }]})

    if omit_title:
        payload = decision.model_dump(mode="json")
        del payload["task_decisions"][0]["title"]
        decision = TaskAgentDecision.model_validate(payload)
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
    assert store.get_business_task(first.task_id).title == "提交周报"
    assert store.get_business_task(second.task_id).title == "提交周报"


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
    decision = TaskAgentDecision.model_validate({"project_assessments": [],
        "update_summary": "本轮没有相关 Project。", "task_decisions": [
        {"action": "update_task", "transition": "update_fields", "task_id": source_id,
         "source_excerpt": "补齐来源链接", "source_ref": item.source.ref, "title": "报价跟进",
         "status": "waiting", "relation_proposals": [{"related_task_id": target_id, "direction": "current_to_related",
         "relation_type": "related_to", "reason": "共享客户目标"}],
         "cluster_proposal": {"cluster_id": cluster_id, "task_ids": [source_id, target_id], "reason": "同一目标"},
         "anchor_match_proposals": [{"anchor_id": anchor_id, "reason": "客户事项"}],
         "project_candidate_proposal": {"cluster_id": cluster_id, "title": "客户项目候选", "reason": "持续任务"}},
        {"action": "record_candidate", "transition": "none", "source_excerpt": "另一个来源里的话",
         "source_ref": "other-source-ref", "title": "无来源候选", "missing_evidence": ["source"]},
    ]})
    original = store.get_business_task(source_id)

    with pytest.raises(ValueError, match="must match the Work Item source"):
        apply_task_agent_decision(store, summary_input_id=1, work_item=item, decision=decision, record_run=False)

    assert store.get_business_task(source_id) == original
    assert store.list_business_task_relations(task_id=source_id) == ()
    assert store.list_business_task_anchor_links(task_id=source_id) == ()
    assert len(store.list_business_task_signals()) == 2
    assert len(store.list_business_work_cluster_tasks(cluster_id=cluster_id)) == 2
    with store._connect() as db:
        assert db.execute("select count(*) from business_project_candidates").fetchone()[0] == 0


@pytest.mark.parametrize("action", ["record_candidate", "update_task"])
@pytest.mark.parametrize("direction", ["current_to_related", "related_to_current"])
def test_decision_relative_relation_binds_actual_applied_task_and_replays(tmp_path, action, direction):
    store = AutoReplyStore(tmp_path / "relative-relation.sqlite3")
    semantic = TaskSemanticService(store)
    seeds = [semantic.record_candidate(RecordCandidate(title=f"已有交付{i}",
        signal=SourceSignal(source_type="seed", source_ref=f"seed:{i}", evidence_text="既有交付", dedupe_key=f"seed:{i}")))
        for i in range(2)]
    item = _work_item().model_copy(update={"summary": "核对供应商延期付款安排。"})
    payload = {"action": action, "transition": "update_fields" if action == "update_task" else "none",
        "task_id": seeds[0].task_id if action == "update_task" else None,
        "source_ref": item.source.ref, "source_excerpt": item.summary, "title": "核对付款安排",
        "description": item.summary, "relation_proposals": [{"related_task_id": seeds[1].task_id,
            "direction": direction, "relation_type": "supports", "reason": "当前行动支持已有交付"}]}
    decision = TaskAgentDecision.model_validate({"project_assessments": [], "update_summary": "本轮没有相关 Project。", "task_decisions": [payload]})
    result = apply_task_agent_decision(store, summary_input_id=1, work_item=item, decision=decision, record_run=False)
    current_id, = result.task_ids
    assert current_id == (seeds[0].task_id if action == "update_task" else 3)
    relation, = store.list_business_task_relations(task_id=current_id)
    assert (relation.from_task_id, relation.to_task_id) == (
        (current_id, seeds[1].task_id) if direction == "current_to_related" else (seeds[1].task_id, current_id))
    assert relation.status.value == "proposed"
    assert relation.supporting_signal_id == store.list_business_task_signals()[-1].id
    tasks = store.list_business_tasks()
    apply_task_agent_decision(store, summary_input_id=1, work_item=item, decision=decision, record_run=False)
    assert store.list_business_tasks() == tasks
    assert store.list_business_task_relations(task_id=current_id) == (relation,)


@pytest.mark.parametrize("actual_self", [False, True])
def test_new_action_relation_rejects_missing_or_deduped_actual_self_target_atomically(tmp_path, actual_self):
    store = AutoReplyStore(tmp_path / "invalid-relative.sqlite3")
    item = _work_item().model_copy(update={"summary": "复核付款安排。"})
    payload = {"action": "record_candidate", "transition": "none", "source_ref": item.source.ref,
        "source_excerpt": item.summary, "title": "复核付款安排"}
    if actual_self:
        seed = apply_task_agent_decision(store, summary_input_id=1, work_item=item,
            decision=TaskAgentDecision.model_validate({"project_assessments": [], "update_summary": "本轮没有相关 Project。", "task_decisions": [payload]}), record_run=False)
        target_id = seed.task_ids[0]
    else:
        target_id = 999
    tasks, signals = store.list_business_tasks(), store.list_business_task_signals()
    payload["relation_proposals"] = [{"related_task_id": target_id, "direction": "current_to_related", "relation_type": "related_to"}]
    with pytest.raises(ValueError):
        apply_task_agent_decision(store, summary_input_id=1, work_item=item,
            decision=TaskAgentDecision.model_validate({"project_assessments": [], "update_summary": "本轮没有相关 Project。", "task_decisions": [payload]}), record_run=False)
    assert store.list_business_tasks() == tasks
    assert store.list_business_task_signals() == signals


@pytest.mark.parametrize("action", ["record_candidate", "update_task"])
def test_relative_relation_effect_fingerprint_preserves_direction_and_ignores_reason(action):
    from app.task_agent import _task_source_signal
    item = _work_item()
    payload = {"action": action, "transition": "update_fields" if action == "update_task" else "none",
        "task_id": 1 if action == "update_task" else None, "source_ref": item.source.ref,
        "source_excerpt": "复核付款安排。", "title": "付款复核", "description": "补充调整方案", "relation_proposals": [
            {"related_task_id": 2, "direction": "current_to_related", "relation_type": "supports", "reason": "解释"}]}
    def key(relation):
        decision = TaskAgentDecision.model_validate({"project_assessments": [], "update_summary": "本轮没有相关 Project。", "task_decisions": [{**payload, "relation_proposals": [relation]}]}).task_decisions[0]
        return _task_source_signal(item, decision).dedupe_key
    original = payload["relation_proposals"][0]
    assert key(original) == key({**original, "reason": "同一业务关系的新解释"})
    for change in ({"direction": "related_to_current"}, {"related_task_id": 3}, {"relation_type": "blocks"}):
        assert (key(original) != key({**original, **change})) is (action == "update_task")


def test_same_task_multiple_decisions_keep_positional_task_signal_mapping(tmp_path):
    store = AutoReplyStore(tmp_path / "same-task.sqlite3")
    service = TaskSemanticService(store)
    created = service.record_candidate(RecordCandidate(
        title="报价跟进", signal=SourceSignal(source_type="seed", source_ref="seed:1", evidence_text="报价跟进", dedupe_key="seed:1")
    ))
    resolution = BusinessResolutionService(store)
    anchor_id = resolution.register_anchor(
        anchor_type="project", anchor_ref="project:quote", title="报价项目"
    )
    resolution.register_official_project(
        anchor_id=anchor_id, registry_source="report:quote"
    )
    resolution.confirm_anchor_match(
        task_id=created.task_id, anchor_id=anchor_id,
        evidence_signal_id=created.signal_id, reason="正式报价项目",
        relevance=BusinessRelevance.RELEVANT,
    )
    item = _work_item()
    decision = TaskAgentDecision.model_validate({"project_assessments": [{
        "project_title": "报价项目", "anchor_id": anchor_id, "outcome": "needs_attention",
        "reason": "报价延期存在明确风险。", "assessment_basis": "current_observation",
        "decision_indexes": [0], "task_ids": [created.task_id],
        "evidence": [{"source_ref": item.source.ref, "source_excerpt": "补齐来源链接"}],
    }], "task_decisions": [
        {"action": "update_task", "transition": "update_fields", "task_id": created.task_id,
         "source_excerpt": "补齐来源链接", "source_ref": item.source.ref, "title": "报价跟进", "status": "waiting",
         "attention_proposal": {"category": "watch", "title": "报价延期风险", "why_attention": "有明确风险",
         "current_state": "待补来源", "ceo_action": "确认推进", "anchor_id": anchor_id,
         "assessment_basis": "current_observation", "material_trigger": "risk_escalation", "evidence": [
             {"source_ref": item.source.ref, "source_excerpt": "补齐来源链接"}]}},
        {"action": "update_task", "transition": "update_fields", "task_id": created.task_id,
         "source_excerpt": "owner 是 Alex", "source_ref": item.source.ref, "title": "报价跟进",
         "business_relevance": "relevant"},
    ]})

    result = apply_task_agent_decision(store, summary_input_id=1, work_item=item, decision=decision, record_run=False)

    assert result.task_ids == (created.task_id, created.task_id)
    assert len(result.attention_proposals) == 1
    assert result.attention_proposals[0].task_id == created.task_id
    assert result.attention_proposals[0].signal_id != created.signal_id


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
    anchor_id = resolution.register_anchor(anchor_type="project", anchor_ref="project:attention",
        title="关键客户交付")
    resolution.register_official_project(anchor_id=anchor_id, registry_source="report:attention")
    resolution.confirm_anchor_match(task_id=seed.task_id, anchor_id=anchor_id,
        evidence_signal_id=seed.signal_id, reason="确认是关键客户业务事项")
    item = _work_item(sender="Alex", sender_user_id="alex-id").model_copy(update={
        "summary": "Alex says the delivery is at risk."
    })
    decision = TaskAgentDecision.model_validate({"project_assessments": [{
        "project_title": "关键客户交付", "anchor_id": anchor_id, "outcome": "needs_attention",
        "reason": "负责人报告已接受承诺存在交付风险。", "assessment_basis": "current_observation",
        "decision_indexes": [0], "task_ids": [seed.task_id],
        "evidence": [{"source_ref": item.source.ref, "source_excerpt": "delivery is at risk"}],
    }], "task_decisions": [{
        "action": "update_task", "transition": "update_fields", "task_id": seed.task_id,
        "source_excerpt": item.summary, "source_ref": item.source.ref,
        "title": "报价方案", "business_relevance": "relevant",
        "attention_proposal": {"category": "watch", "title": "承诺交付风险", "why_attention": "负责人报告已接受承诺有风险",
         "current_state": "交付存在风险", "ceo_action": "核实交付状态", "anchor_id": anchor_id,
         "assessment_basis": "current_observation", "material_trigger": "threatened_commitment", "evidence": [
             {"source_ref": item.source.ref, "source_excerpt": "delivery is at risk"}]},
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
    _project_task_attention(store, result.attention_proposals, result.affected_task_ids,
                            receipt=result.projection_receipt)

    assert seen == [("relevant", 1, 1)]
    (attention_item,) = store.list_business_attention_items()
    assert attention_item.why_attention == "负责人报告已接受承诺有风险"
    assessment = json.loads(attention_item.assessment_json)
    assert assessment["material_trigger"] == "threatened_commitment"
    assert assessment["evidence"][0]["source_excerpt"] == "delivery is at risk"


def test_two_applied_project_proposals_fold_into_one_assessment_readback(tmp_path):
    store = AutoReplyStore(tmp_path / "assessment-folded-proposals.sqlite3")
    first, anchor_id = _stored_project_task(store)
    second = TaskSemanticService(store).record_candidate(RecordCandidate(
        title="售前知识库回款",
        signal=SourceSignal(source_type="seed", source_ref="seed:folded-second",
                            evidence_text="售前知识库回款", dedupe_key="seed:folded-second"),
    ))
    BusinessResolutionService(store).confirm_anchor_match(
        task_id=second.task_id, anchor_id=anchor_id, evidence_signal_id=second.signal_id,
        reason="同一正式 Project", relevance=BusinessRelevance.RELEVANT,
    )
    item = _work_item().model_copy(update={"summary": "售前知识库交付风险需要观察。"})
    proposal = {
        "category": "watch", "title": "售前知识库交付风险",
        "why_attention": "交付风险需要观察", "current_state": "等待交付与回款结果",
        "ceo_action": "观察结果", "anchor_id": anchor_id,
        "assessment_basis": "current_observation", "material_trigger": "risk_escalation",
        "evidence": [{"source_ref": item.source.ref, "source_excerpt": "交付风险需要观察"}],
    }
    decision = TaskAgentDecision.model_validate({
        "project_assessments": [{
            "project_title": "售前知识库", "anchor_id": anchor_id,
            "outcome": "needs_attention", "reason": "交付风险同时影响交付与回款。",
            "assessment_basis": "current_observation", "decision_indexes": [0, 1],
            "task_ids": [first.task_id, second.task_id],
            "evidence": [{"source_ref": item.source.ref, "source_excerpt": "交付风险需要观察"}],
        }],
        "task_decisions": [
            {
                "action": "update_task", "transition": "update_fields", "task_id": first.task_id,
                "title": "售前知识库交付",
                "status": "waiting", "source_ref": item.source.ref,
                "source_excerpt": "交付风险需要观察", "attention_proposal": proposal,
            },
            {
                "action": "update_task", "transition": "update_fields", "task_id": second.task_id,
                "title": "售前知识库回款", "status": "waiting", "source_ref": item.source.ref,
                "source_excerpt": "交付风险需要观察", "attention_proposal": proposal,
            },
        ],
    })

    result = apply_task_agent_decision(
        store, summary_input_id=1, work_item=item, decision=decision, record_run=False,
    )

    assert result.projection_receipt is not None
    [readback] = result.projection_receipt.project_assessments
    assert readback.status == "applied"
    assert readback.anchor_id == anchor_id
    assert readback.task_ids == sorted([first.task_id, second.task_id])
    assert readback.attention_id == store.list_business_attention_items()[0].id
    assert len({outcome.attention_id for outcome in result.projection_receipt.outcomes}) == 1


def test_mixed_applied_and_unapplied_support_keeps_card_and_rejection_reason(tmp_path):
    store = AutoReplyStore(tmp_path / "assessment-mixed-proposal-outcomes.sqlite3")
    first, anchor_id = _stored_project_task(store)
    second = TaskSemanticService(store).record_candidate(RecordCandidate(
        title="售前知识库回款",
        signal=SourceSignal(source_type="seed", source_ref="seed:mixed-second",
                            evidence_text="售前知识库回款", dedupe_key="seed:mixed-second"),
    ))
    BusinessResolutionService(store).confirm_anchor_match(
        task_id=second.task_id, anchor_id=anchor_id, evidence_signal_id=second.signal_id,
        reason="同一正式 Project", relevance=BusinessRelevance.RELEVANT,
    )
    item = _work_item().model_copy(update={"summary": "售前知识库交付风险需要观察。"})
    proposal = {
        "category": "watch", "title": "售前知识库交付风险",
        "why_attention": "交付风险需要观察", "current_state": "等待交付与回款结果",
        "ceo_action": "观察结果", "anchor_id": anchor_id,
        "assessment_basis": "current_observation", "material_trigger": "risk_escalation",
        "evidence": [{"source_ref": item.source.ref, "source_excerpt": "交付风险需要观察"}],
    }
    decision = TaskAgentDecision.model_validate({
        "project_assessments": [{
            "project_title": "售前知识库", "anchor_id": anchor_id,
            "outcome": "needs_attention", "reason": "交付风险同时影响交付与回款。",
            "assessment_basis": "current_observation", "decision_indexes": [0, 1],
            "task_ids": [first.task_id, second.task_id],
            "evidence": [{"source_ref": item.source.ref, "source_excerpt": "交付风险需要观察"}],
        }],
        "task_decisions": [
            {
                "action": "update_task", "transition": "update_fields", "task_id": first.task_id,
                "title": "售前知识库交付", "status": "waiting",
                "source_ref": item.source.ref, "source_excerpt": "交付风险需要观察",
                "attention_proposal": proposal,
            },
            {
                "action": "update_task", "transition": "update_fields", "task_id": second.task_id,
                "title": "售前知识库回款",
                "source_ref": item.source.ref, "source_excerpt": "交付风险需要观察",
                "attention_proposal": proposal,
            },
        ],
    })

    result = apply_task_agent_decision(
        store, summary_input_id=1, work_item=item, decision=decision, record_run=False,
    )

    assert result.projection_receipt is not None
    assert result.projection_receipt.status == "partial"
    [readback] = result.projection_receipt.project_assessments
    assert readback.status == "applied"
    assert readback.reason == "Attention proposal applied; proposal has no applied Task decision"
    assert readback.anchor_id == anchor_id
    assert readback.task_ids == sorted([first.task_id, second.task_id])
    assert readback.attention_id is not None
    assert [
        link.task_id for link in store.list_business_attention_tasks(readback.attention_id)
    ] == [first.task_id]


def test_task_ids_only_support_receives_recompute_error(tmp_path, monkeypatch):
    store = AutoReplyStore(tmp_path / "assessment-task-id-recompute-error.sqlite3")
    seed, anchor_id = _stored_project_task(store)
    item = _work_item().model_copy(update={"summary": "售前知识库交付补齐本轮来源。"})
    decision = TaskAgentDecision.model_validate({
        "project_assessments": [{
            "project_title": "售前知识库", "anchor_id": anchor_id,
            "outcome": "not_needed", "reason": "本轮只补来源，没有新增经营影响。",
            "assessment_basis": "current_observation", "decision_indexes": [],
            "task_ids": [seed.task_id],
            "evidence": [{"source_ref": item.source.ref, "source_excerpt": "补齐本轮来源"}],
        }],
        "task_decisions": [{
            "action": "update_task", "transition": "update_fields", "task_id": seed.task_id,
            "title": "售前知识库交付", "status": "waiting",
            "source_ref": item.source.ref, "source_excerpt": "补齐本轮来源",
        }],
    })

    def fail_recompute(_self, *_args, **_kwargs):
        raise RuntimeError("review recompute exploded")

    monkeypatch.setattr(BusinessAttentionProjection, "recompute_for_tasks", fail_recompute)
    result = apply_task_agent_decision(
        store, summary_input_id=1, work_item=item, decision=decision, record_run=False,
    )

    assert result.projection_receipt is not None
    assert result.projection_receipt.recompute_error == "review recompute exploded"
    [readback] = result.projection_receipt.project_assessments
    assert readback.status == "error"
    assert readback.reason == "review recompute exploded"
    assert readback.task_ids == [seed.task_id]
    assert readback.evidence[0].signal_id == result.applied_decisions[0].signal_id
    assert decision.project_assessments[0].outcome == "not_needed"


def test_no_change_known_project_support_keeps_verified_decision_task_id(tmp_path):
    store = AutoReplyStore(tmp_path / "assessment-no-change-known-task-id.sqlite3")
    seed, anchor_id = _stored_project_task(store)
    item = _work_item().model_copy(update={"summary": "售前知识库交付风险需要观察。"})
    decision = TaskAgentDecision.model_validate({
        "project_assessments": [{
            "project_title": "售前知识库", "anchor_id": anchor_id,
            "outcome": "needs_attention", "reason": "交付风险需要观察。",
            "assessment_basis": "current_observation", "decision_indexes": [0],
            "task_ids": [],
            "evidence": [{"source_ref": item.source.ref, "source_excerpt": "交付风险需要观察"}],
        }],
        "task_decisions": [{
            "action": "update_task", "transition": "update_fields", "task_id": seed.task_id,
            "title": "售前知识库交付", "source_ref": item.source.ref,
            "source_excerpt": "交付风险需要观察",
            "attention_proposal": {
                "category": "watch", "title": "售前知识库交付风险",
                "why_attention": "交付风险需要观察", "current_state": "等待交付结果",
                "ceo_action": "观察结果", "anchor_id": anchor_id,
                "assessment_basis": "current_observation", "material_trigger": "risk_escalation",
                "evidence": [{"source_ref": item.source.ref, "source_excerpt": "交付风险需要观察"}],
            },
        }],
    })
    signals_before = store.list_business_task_signals()

    result = apply_task_agent_decision(
        store, summary_input_id=1, work_item=item, decision=decision, record_run=False,
    )

    assert result.applied_decisions == ()
    assert result.projection_receipt is not None
    [readback] = result.projection_receipt.project_assessments
    assert readback.status == "rejected"
    assert readback.reason == "proposal has no applied Task decision"
    assert readback.anchor_id == anchor_id
    assert readback.task_ids == [seed.task_id]
    assert readback.attention_id is None
    assert store.list_business_task_signals() == signals_before


@pytest.mark.parametrize(
    ("failure_method", "failure_message"),
    [("upsert", "projection exploded"), ("recompute_for_tasks", "recompute exploded")],
)
def test_projection_error_is_preserved_in_assessment_application_readback(
    tmp_path, monkeypatch, failure_method, failure_message,
):
    store = AutoReplyStore(tmp_path / "assessment-projection-error.sqlite3")
    seed, anchor_id = _stored_project_task(store)
    item = _work_item().model_copy(update={"summary": "售前知识库交付风险需要观察。"})
    decision = TaskAgentDecision.model_validate({
        "project_assessments": [{
            "project_title": "售前知识库", "anchor_id": anchor_id,
            "outcome": "needs_attention", "reason": "交付风险需要观察。",
            "assessment_basis": "current_observation", "decision_indexes": [0],
            "task_ids": [seed.task_id],
            "evidence": [{"source_ref": item.source.ref, "source_excerpt": "交付风险需要观察"}],
        }],
        "task_decisions": [{
            "action": "update_task", "transition": "update_fields", "task_id": seed.task_id,
            "title": "售前知识库交付", "status": "waiting", "source_ref": item.source.ref,
            "source_excerpt": "交付风险需要观察",
            "attention_proposal": {
                "category": "watch", "title": "售前知识库交付风险",
                "why_attention": "交付风险需要观察", "current_state": "等待交付结果",
                "ceo_action": "观察结果", "anchor_id": anchor_id,
                "assessment_basis": "current_observation", "material_trigger": "risk_escalation",
                "evidence": [{"source_ref": item.source.ref, "source_excerpt": "交付风险需要观察"}],
            },
        }],
    })

    def fail_projection(_self, *_args, **_kwargs):
        raise RuntimeError(failure_message)

    monkeypatch.setattr(BusinessAttentionProjection, failure_method, fail_projection)
    result = apply_task_agent_decision(
        store, summary_input_id=1, work_item=item, decision=decision, record_run=False,
    )

    assert result.projection_receipt is not None
    [readback] = result.projection_receipt.project_assessments
    assert readback.status == "error"
    assert readback.reason == failure_message
    assert readback.anchor_id == anchor_id
    assert readback.task_ids == [seed.task_id]
    assert (readback.attention_id is None) is (failure_method == "upsert")


def test_routine_progress_without_attention_proposal_is_not_projected(tmp_path):
    store = AutoReplyStore(tmp_path / "ordinary-progress-not-attention.sqlite3")
    seed = TaskSemanticService(store).record_candidate(RecordCandidate(
        title="报价跟进", signal=SourceSignal(source_type="seed", source_ref="seed:attention-candidate",
            evidence_text="报价跟进", dedupe_key="seed:attention-candidate")
    ))
    item = _work_item().model_copy(update={"summary": "补齐来源链接"})
    decision = TaskAgentDecision.model_validate({"project_assessments": [], "update_summary": "本轮没有相关 Project。", "task_decisions": [{
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
    relevance = TaskAgentDecision.model_validate({"project_assessments": [], "update_summary": "本轮没有相关 Project。", "task_decisions": [{
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
    decision = TaskAgentDecision.model_validate({"project_assessments": [], "update_summary": "本轮没有相关 Project。", "task_decisions": [{
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
    decision = TaskAgentDecision.model_validate({"project_assessments": [], "update_summary": "本轮没有相关 Project。", "task_decisions": [{
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
    decision = TaskAgentDecision.model_validate({"project_assessments": [], "update_summary": "本轮没有相关 Project。", "task_decisions": [{
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
    decision = TaskAgentDecision.model_validate({"project_assessments": [], "update_summary": "本轮没有相关 Project。", "task_decisions": [{
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
    decision = TaskAgentDecision.model_validate({"project_assessments": [], "update_summary": "本轮没有相关 Project。", "task_decisions": [{
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
    decision = TaskAgentDecision.model_validate({"project_assessments": [], "update_summary": "本轮没有相关 Project。", "task_decisions": [{
        "action": "create_task", "transition": "none", "formal_basis": "meeting_action_item",
            "source_excerpt": "Alex 负责补齐报价来源链接。", "source_ref": item.source.ref,
            "title": "补齐报价来源链接", "owner_name": "Alex",
            "owner_evidence": {"source_ref": item.source.ref,
                "excerpt": "Alex 负责补齐报价来源链接。", "name": "Alex", "user_id": "alex-id"},
            "owner_kind": "individual", "owner_relation": "meeting_summary_action_item",
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
    decision = TaskAgentDecision.model_validate({"project_assessments": [], "update_summary": "本轮没有相关 Project。", "task_decisions": [{
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
    decision = TaskAgentDecision.model_validate({"project_assessments": [], "update_summary": "本轮没有相关 Project。", "task_decisions": [{
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
    decision = TaskAgentDecision.model_validate({"project_assessments": [], "update_summary": "本轮没有相关 Project。", "task_decisions": [{
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
    decision = TaskAgentDecision.model_validate({"project_assessments": [], "update_summary": "本轮没有相关 Project。", "task_decisions": [{
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
    decision = TaskAgentDecision.model_validate({"project_assessments": [], "update_summary": "本轮没有相关 Project。", "task_decisions": [{
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
    decision = TaskAgentDecision.model_validate({"project_assessments": [], "update_summary": "本轮没有相关 Project。", "task_decisions": [{
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
    decision = TaskAgentDecision.model_validate({"project_assessments": [], "update_summary": "本轮没有相关 Project。", "task_decisions": [{
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
    decision = TaskAgentDecision.model_validate({"project_assessments": [], "update_summary": "本轮没有相关 Project。", "task_decisions": [{
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
    decision = TaskAgentDecision.model_validate({"project_assessments": [], "update_summary": "本轮没有相关 Project。", "task_decisions": [{
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


@pytest.mark.parametrize("omit_title", [False, True])
def test_exact_owner_reply_accepts_only_cited_assignment_and_records_committed_date(tmp_path, omit_title):
    store = AutoReplyStore(tmp_path / "linked-acceptance.sqlite3")
    assigned = _assigned_formal_task_for_acceptance(store)
    item = _work_item(
        sender="Alex", sender_user_id="alex-id", reply_to_source_ref="message:assignment"
    ).model_copy(update={"summary": "我接受报价方案，承诺于 2026-09-25 交付"})
    decision = TaskAgentDecision.model_validate({"project_assessments": [], "update_summary": "本轮没有相关 Project。", "task_decisions": [{
        "action": "update_task", "transition": "apply_acceptance", "task_id": assigned.task_id,
        "acceptance_polarity": "accepted", "acceptance_target_signal_id": assigned.signal_id,
        "source_excerpt": "我接受报价方案，承诺于 2026-09-25 交付", "source_ref": item.source.ref,
        "title": "报价方案",
        "date_evidence": [{"kind": "committed_deadline_at", "value": "2026-09-25",
            "source_ref": item.source.ref, "source_excerpt": "2026-09-25",
            "actor_user_id": "alex-id", "actor_name": "Alex"}],
    }]})

    if omit_title:
        payload = decision.model_dump(mode="json")
        del payload["task_decisions"][0]["title"]
        decision = TaskAgentDecision.model_validate(payload)
    result = apply_task_agent_decision(
        store, summary_input_id=1, work_item=item, decision=decision, record_run=False
    )

    assert result.task_ids == (assigned.task_id,)
    task = store.get_business_task(assigned.task_id)
    assert task.title == "报价方案"
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
    decision = TaskAgentDecision.model_validate({"project_assessments": [], "update_summary": "本轮没有相关 Project。", "task_decisions": [{
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
    decision = TaskAgentDecision.model_validate({"project_assessments": [], "update_summary": "本轮没有相关 Project。", "task_decisions": [{
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
    # Derek 2026-09-28: a narrow transcript window can miss the sentence that names the owner;
    # the meeting's own DingTalk summary is a second source and must be checked too.
    assert "meeting_summary" in prompt
    assert "not a team or department" in prompt


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

    decision = TaskAgentDecision.model_validate({"project_assessments": [], "update_summary": "本轮没有相关 Project。", "task_decisions": [
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
    decision = TaskAgentDecision.model_validate({"project_assessments": [], "update_summary": "本轮没有相关 Project。", "task_decisions": [{
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

    decision = TaskAgentDecision.model_validate({"project_assessments": [], "update_summary": "本轮没有相关 Project。", "task_decisions": [
        update(first, "整理清单", "陈思睿", good),  # the quoted line is Zoey's: it does not name 陈思睿
        update(second, "发送文档", "陈思睿", other),
    ]})

    result = apply_task_agent_decision(store, summary_input_id=1, work_item=item, decision=decision, record_run=False)

    assert store.get_business_task(first).owner_name == ""
    assert store.get_business_task(second).owner_name == "陈思睿"
    assert any(f"Task {first} owner was not applied" in reason for reason in result.skipped_reasons)


def _earlier_evidence_decision(existing_task_id, item, **overrides):
    return {
        "action": "update_task", "transition": "update_fields", "task_id": existing_task_id,
        "evidence_origin": "memory", "source_ref": "meeting:2026-09-10#todos-sha256=abc",
        "source_link": "https://shanji.example/transcribes/abc",
        "source_excerpt": "Zoey：我来负责访谈问题清单。", "title": "整理访谈问题清单",
        "owner_name": "Zoey", "owner_evidence": {"excerpt": "Zoey：我来负责访谈问题清单。"},
        **overrides,
    }


def test_earlier_or_remembered_evidence_can_refine_a_task_and_is_kept_as_its_own_source(tmp_path):
    """Derek 2026-09-25: the Agent may use earlier evidence and Memory provenance; it is recorded as cited, not observed."""
    store = AutoReplyStore(tmp_path / "earlier.sqlite3")
    task = TaskSemanticService(store).record_candidate(RecordCandidate(
        title="整理访谈问题清单",
        signal=SourceSignal(source_type="ai_minutes", source_ref="m:1#todos-sha256=a", evidence_text="整理访谈问题清单", dedupe_key="seed:earlier"),
    ))
    item = _work_item().model_copy(update={"summary": "本次来源没有提到负责人。"})
    decision = TaskAgentDecision.model_validate({"project_assessments": [], "update_summary": "本轮没有相关 Project。", "task_decisions": [_earlier_evidence_decision(task.task_id, item)]})

    apply_task_agent_decision(store, summary_input_id=1, work_item=item, decision=decision, record_run=False)

    updated = store.get_business_task(task.task_id)
    assert updated.owner_name == "Zoey"
    signals = {signal.id: signal for signal in store.list_business_task_signals()}
    cited = [signals[row.signal_id] for row in store.list_business_task_evidence(task.task_id) if signals[row.signal_id].source_type == "memory_provenance"]
    assert [(s.source_ref, s.evidence_text) for s in cited] == [("meeting:2026-09-10#todos-sha256=abc", "Zoey：我来负责访谈问题清单。")]
    assert json.loads(cited[0].context_json) == {
        "evidence_origin": "memory", "cited_while_processing": item.source.ref,
        "source_link": "https://shanji.example/transcribes/abc",
    }


@pytest.mark.parametrize(
    "extra",
    [
        {"action": "create_task", "transition": "none", "formal_basis": "explicit_assignment", "task_id": None},
        {"transition": "promote_candidate"},
        {"transition": "apply_acceptance", "acceptance_polarity": "accepted"},
        {"date_evidence": [{"kind": "next_check_at", "value": "2026-10-01", "raw_phrase": "x", "source_ref": "meeting:2026-09-10#todos-sha256=abc", "source_excerpt": "x"}]},
    ],
)
def test_earlier_evidence_does_not_stand_in_for_the_current_sources_authority_or_dates(extra):
    item = _work_item()
    with pytest.raises(ValidationError):
        TaskAgentDecision.model_validate({"project_assessments": [], "update_summary": "本轮没有相关 Project。", "task_decisions": [_earlier_evidence_decision(1, item, **extra)]})


def test_earlier_evidence_needs_its_link_or_else_a_description_of_where_it_is():
    """A link whenever there is one; without a link, words (a DingTalk message is its group and person)."""
    item = _work_item()
    for missing in ({"source_link": ""}, {"source_link": "", "source_group": "产品群"}, {"source_link": "", "source_person": "Zoey"}):
        with pytest.raises(ValidationError, match="its source link, or, when there is none, a description"):
            TaskAgentDecision.model_validate({"project_assessments": [], "update_summary": "本轮没有相关 Project。", "task_decisions": [_earlier_evidence_decision(1, item, **missing)]})
    for given in ({"source_link": "", "source_description": "产品群里 Zoey 的消息"}, {"source_link": "", "source_group": "产品群", "source_person": "Zoey"}):
        TaskAgentDecision.model_validate({"project_assessments": [], "update_summary": "本轮没有相关 Project。", "task_decisions": [_earlier_evidence_decision(1, item, **given)]})


def test_the_current_sources_link_or_group_and_person_are_recorded_and_the_excerpt_may_be_an_extract(tmp_path):
    store = AutoReplyStore(tmp_path / "locator.sqlite3")
    minutes = _work_item().model_copy(update={"summary": json.dumps({"meeting": {"shareUrl": "https://shanji.example/transcribes/1"}, "lines": ["Zoey：我先列一个list给你看。"]}, ensure_ascii=False)})
    decision = TaskAgentDecision.model_validate({"project_assessments": [], "update_summary": "本轮没有相关 Project。", "task_decisions": [{
        "action": "record_candidate", "transition": "none", "title": "列问题清单",
        "source_ref": minutes.source.ref, "source_excerpt": "Zoey 说先列个清单给看",  # an extract, not word for word
    }]})
    apply_task_agent_decision(store, summary_input_id=1, work_item=minutes, decision=decision, record_run=False)

    [signal] = store.list_business_task_signals()
    assert json.loads(signal.context_json)["source_link"] == "https://shanji.example/transcribes/1"

    chat = _work_item().model_copy(update={"summary": "王明：周五前交报价。"})
    chat = chat.model_copy(update={"source": chat.source.model_copy(update={"ref": "message:9", "conversation_title": "报价群"}),
                                   "context": chat.context.model_copy(update={"sender": "王明"})})
    decision = TaskAgentDecision.model_validate({"project_assessments": [], "update_summary": "本轮没有相关 Project。", "task_decisions": [{
        "action": "record_candidate", "transition": "none", "title": "交报价",
        "source_ref": "message:9", "source_excerpt": "周五前交报价",
    }]})
    apply_task_agent_decision(store, summary_input_id=2, work_item=chat, decision=decision, record_run=False)

    signal = next(row for row in store.list_business_task_signals() if row.source_ref == "message:9")
    assert (signal.conversation_title, signal.author_name) == ("报价群", "王明")


def _stored_project_task(store, *, title="售前知识库", source_type="seed"):
    semantic = TaskSemanticService(store)
    seed = semantic.record_candidate(RecordCandidate(
        title=f"{title}交付",
        signal=SourceSignal(
            source_type=source_type,
            source_ref=f"{source_type}:{title}",
            evidence_text=f"{title}历史交付风险",
            dedupe_key=f"{source_type}:{title}",
        ),
    ))
    resolution = BusinessResolutionService(store)
    anchor_id = resolution.register_anchor(
        anchor_type="project", anchor_ref=f"project:{title}", title=title,
    )
    resolution.register_official_project(
        anchor_id=anchor_id, registry_source=f"report:{title}",
    )
    resolution.confirm_anchor_match(
        task_id=seed.task_id,
        anchor_id=anchor_id,
        evidence_signal_id=seed.signal_id,
        reason="正式 Project 的现有 Task",
        relevance=BusinessRelevance.RELEVANT,
    )
    return seed, anchor_id


def _stored_project_assessment(item, seed, anchor_id, **updates):
    payload = {
        "project_title": "售前知识库",
        "anchor_id": anchor_id,
        "outcome": "not_needed",
        "reason": "本轮仅补齐来源，没有新增经营影响。",
        "assessment_basis": "current_observation",
        "evidence": [{
            "source_ref": item.source.ref,
            "source_excerpt": "补齐来源链接",
        }],
        "decision_indexes": [],
        "task_ids": [seed.task_id],
    }
    payload.update(updates)
    return payload


def _stored_project_identity_merge(store, item, *, include_assessment):
    source = _seed_identity_task(
        store, "message:merge-project-source", external_task_id="dingtalk:task-88",
    )
    target = _seed_identity_task(
        store, "message:merge-project-target", external_task_id="dingtalk:task-88",
    )
    resolution = BusinessResolutionService(store)
    anchor_id = resolution.register_anchor(
        anchor_type="project", anchor_ref="project:售前知识库", title="售前知识库",
    )
    resolution.register_official_project(
        anchor_id=anchor_id, registry_source="report:售前知识库",
    )
    resolution.confirm_anchor_match(
        task_id=target.task_id, anchor_id=anchor_id,
        evidence_signal_id=target.signal_id, reason="目标 Task 已确认属于正式 Project",
        relevance=BusinessRelevance.RELEVANT,
    )
    assessments = []
    if include_assessment:
        assessments.append({
            "project_title": "售前知识库", "anchor_id": anchor_id,
            "outcome": "not_needed", "reason": "本轮只合并重复身份。",
            "assessment_basis": "current_observation", "decision_indexes": [],
            "task_ids": [target.task_id],
            "evidence": [{
                "source_ref": item.source.ref,
                "source_excerpt": "同步外部待办记录",
            }],
        })
    decision = TaskAgentDecision.model_validate({
        "project_assessments": assessments,
        "update_summary": "本轮没有相关 Project。" if not assessments else "已判断目标 Project。",
        "task_decisions": [{
            "action": "update_task", "transition": "merge_identity",
            "task_id": source.task_id, "target_task_id": target.task_id,
            "source_excerpt": "同步外部待办记录", "source_ref": item.source.ref,
            "identity_proposal": {
                "source_task_id": source.task_id, "target_task_id": target.task_id,
                "reason": "Same external task ID dingtalk:task-88",
                "identity_evidence": {
                    "basis": "same_external_task_id",
                    "source_signal_id": source.signal_id,
                    "target_signal_id": target.signal_id,
                },
            },
        }],
    })
    return source, target, anchor_id, decision


def test_identity_merge_target_confirmed_project_requires_assessment_before_writes(tmp_path):
    store = AutoReplyStore(tmp_path / "assessment-merge-target-coverage.sqlite3")
    item = _work_item().model_copy(update={"summary": "同步外部待办记录"})
    source, target, _anchor_id, decision = _stored_project_identity_merge(
        store, item, include_assessment=False,
    )
    signals_before = store.list_business_task_signals()

    with pytest.raises(ValueError, match=f"current Task {target.task_id} confirmed Project"):
        apply_task_agent_decision(
            store, summary_input_id=1, work_item=item, decision=decision, record_run=False,
        )

    assert store.get_business_task(source.task_id).status.value != "merged"
    assert store.list_business_task_signals() == signals_before


def test_identity_merge_target_confirmed_project_accepts_explicit_target_assessment(tmp_path):
    store = AutoReplyStore(tmp_path / "assessment-merge-target-covered.sqlite3")
    item = _work_item().model_copy(update={"summary": "同步外部待办记录"})
    source, target, anchor_id, decision = _stored_project_identity_merge(
        store, item, include_assessment=True,
    )

    result = apply_task_agent_decision(
        store, summary_input_id=1, work_item=item, decision=decision, record_run=False,
    )

    assert result.task_ids == (target.task_id,)
    assert result.applied_decisions[0].task_id == target.task_id
    assert result.applied_decisions[0].anchor_id == anchor_id
    assert store.get_business_task(source.task_id).status.value == "merged"


def _stored_attention_card(store, *, seed, anchor_id, title="售前知识库"):
    assessment_json = json.dumps({
        "assessment_basis": "historical_comparison",
        "material_trigger": "risk_escalation",
        "inference": "历史交付风险仍需观察",
        "evidence": [{
            "signal_id": seed.signal_id,
            "source_ref": f"seed:{title}",
            "source_excerpt": f"{title}历史交付风险",
            "source_time": "",
            "source_link": "",
        }],
    }, ensure_ascii=False, sort_keys=True)
    return BusinessAttentionProjection(store).upsert(AttentionProposal(
        stable_key=f"project:{anchor_id}",
        category=AttentionCategory.WATCH,
        title=f"{title}交付风险",
        business_area="",
        why_attention="历史交付风险仍需观察",
        current_state="等待新的交付结果",
        ceo_action="暂不介入，观察结果",
        anchor_id=anchor_id,
        task_ids=(seed.task_id,),
        evidence_signal_id=seed.signal_id,
        assessment_json=assessment_json,
    ))


def test_stored_assessment_requires_current_known_project_link_coverage(tmp_path):
    store = AutoReplyStore(tmp_path / "assessment-known-project-coverage.sqlite3")
    seed, _anchor_id = _stored_project_task(store)
    item = _work_item()
    decision = TaskAgentDecision.model_validate({
        "project_assessments": [],
        "update_summary": "错误地声称本轮没有相关 Project。",
        "task_decisions": [{
            "action": "update_task", "transition": "update_fields",
            "task_id": seed.task_id, "title": "售前知识库交付",
            "source_ref": item.source.ref, "source_excerpt": "补齐来源链接",
        }],
    })

    with pytest.raises(ValueError, match="confirmed Project.*requires exactly one assessment"):
        apply_task_agent_decision(
            store, summary_input_id=1, work_item=item, decision=decision, record_run=False,
        )

    assert len(store.list_business_task_signals()) == 1


@pytest.mark.parametrize(
    ("evidence_update", "problem"),
    [
        ({"source_ref": "message:guessed"}, "current assessment evidence must cite the immutable Work Item"),
        ({"source_excerpt": "不存在的当前原文"}, "current assessment quote is absent"),
    ],
)
def test_stored_assessment_rejects_wrong_current_citation_atomically(
    tmp_path, evidence_update, problem,
):
    store = AutoReplyStore(tmp_path / "assessment-current-citation.sqlite3")
    seed, anchor_id = _stored_project_task(store)
    item = _work_item()
    assessment = _stored_project_assessment(item, seed, anchor_id)
    assessment["evidence"] = [{**assessment["evidence"][0], **evidence_update}]
    decision = TaskAgentDecision.model_validate({
        "project_assessments": [assessment], "task_decisions": [],
    })

    with pytest.raises(ValueError, match=problem):
        apply_task_agent_decision(
            store, summary_input_id=1, work_item=item, decision=decision, record_run=False,
        )

    assert len(store.list_business_task_signals()) == 1


@pytest.mark.parametrize(
    ("historical_update", "source_type", "problem"),
    [
        ({"signal_id": 999}, "seed", "historical assessment evidence signal does not exist"),
        ({"source_ref": "seed:wrong"}, "seed", "historical assessment evidence signal/source_ref does not match"),
        ({"source_excerpt": "不存在的历史原文"}, "seed", "historical assessment quote is absent"),
        ({}, "memory_provenance", "historical assessment evidence must be observed original source"),
    ],
)
def test_stored_assessment_rejects_false_historical_provenance(
    tmp_path, historical_update, source_type, problem,
):
    store = AutoReplyStore(tmp_path / "assessment-historical-citation.sqlite3")
    seed, anchor_id = _stored_project_task(store, source_type=source_type)
    item = _work_item()
    historical = {
        "signal_id": seed.signal_id,
        "source_ref": f"{source_type}:售前知识库",
        "source_excerpt": "售前知识库历史交付风险",
        **historical_update,
    }
    assessment = _stored_project_assessment(
        item, seed, anchor_id,
        outcome="needs_attention",
        reason="当前信息与历史风险需要一起判断。",
        assessment_basis="historical_comparison",
        existing_attention_id=1,
        evidence=[
            {"source_ref": item.source.ref, "source_excerpt": "补齐来源链接"},
            historical,
        ],
    )
    decision = TaskAgentDecision.model_validate({
        "project_assessments": [assessment], "task_decisions": [],
    })

    with pytest.raises(ValueError, match=problem):
        apply_task_agent_decision(
            store, summary_input_id=1, work_item=item, decision=decision, record_run=False,
        )


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        ("guessed_anchor", "registered active official Project"),
        ("inactive_anchor", "registered active official Project"),
        ("wrong_title", "project_title must match the canonical stored Project title"),
        ("missing_task", "supporting Task 999 does not exist"),
        ("unrelated_task", "supporting Task is not confirmed to the assessed Project"),
    ],
)
def test_stored_assessment_rejects_guessed_project_or_unrelated_task(tmp_path, change, problem):
    store = AutoReplyStore(tmp_path / "assessment-project-identity.sqlite3")
    seed, anchor_id = _stored_project_task(store)
    unrelated = TaskSemanticService(store).record_candidate(RecordCandidate(
        title="无关工作",
        signal=SourceSignal(source_type="seed", source_ref="seed:unrelated",
                            evidence_text="无关工作", dedupe_key="seed:unrelated"),
    ))
    item = _work_item()
    assessment = _stored_project_assessment(item, seed, anchor_id)
    if change == "guessed_anchor":
        assessment["anchor_id"] = 999
    elif change == "inactive_anchor":
        with store.business_task_transaction() as db:
            db.execute("update business_anchors set active=0 where id=?", (anchor_id,))
    elif change == "wrong_title":
        assessment["project_title"] = "猜测的项目名"
    elif change == "missing_task":
        assessment["task_ids"] = [999]
    else:
        assessment["task_ids"] = [unrelated.task_id]
    decision = TaskAgentDecision.model_validate({
        "project_assessments": [assessment], "task_decisions": [],
    })

    with pytest.raises(ValueError, match=problem):
        apply_task_agent_decision(
            store, summary_input_id=1, work_item=item, decision=decision, record_run=False,
        )


def test_existing_attention_repeat_with_no_task_field_change_has_no_new_effect(tmp_path):
    store = AutoReplyStore(tmp_path / "assessment-existing-card-repeat.sqlite3")
    seed, anchor_id = _stored_project_task(store)
    attention_id = _stored_attention_card(store, seed=seed, anchor_id=anchor_id)
    item = _work_item()
    decision = TaskAgentDecision.model_validate({
        "project_assessments": [_stored_project_assessment(
            item, seed, anchor_id,
            outcome="needs_attention",
            reason="同一事实已由现有关注卡表示。",
            existing_attention_id=attention_id,
            decision_indexes=[0],
            assessment_basis="historical_comparison",
            evidence=[
                {"source_ref": item.source.ref, "source_excerpt": "补齐来源链接"},
                {"signal_id": seed.signal_id, "source_ref": "seed:售前知识库",
                 "source_excerpt": "售前知识库历史交付风险"},
            ],
        )],
        "task_decisions": [{
            "action": "update_task", "transition": "update_fields",
            "task_id": seed.task_id, "title": "售前知识库交付",
            "source_ref": item.source.ref, "source_excerpt": "补齐来源链接",
        }],
    })
    signals_before = store.list_business_task_signals()
    events_before = store.list_business_attention_events(attention_id)

    result = apply_task_agent_decision(
        store, summary_input_id=1, work_item=item, decision=decision, record_run=False,
    )

    assert result.task_ids == ()
    assert result.applied_decisions == ()
    assert result.projection_receipt is not None
    [readback] = result.projection_receipt.project_assessments
    assert readback.status == "existing"
    assert readback.anchor_id == anchor_id
    assert readback.task_ids == [seed.task_id]
    assert readback.attention_id == attention_id
    assert [citation.signal_id for citation in readback.evidence] == [
        None, seed.signal_id,
    ]
    assert store.list_business_task_signals() == signals_before
    assert store.list_business_attention_events(attention_id) == events_before

    replay = apply_task_agent_decision(
        store, summary_input_id=2, work_item=item, decision=decision, record_run=False,
    )

    assert replay.projection_receipt is not None
    assert replay.projection_receipt.project_assessments == result.projection_receipt.project_assessments
    assert store.list_business_task_signals() == signals_before
    assert store.list_business_attention_events(attention_id) == events_before


def test_existing_attention_keeps_card_id_but_reports_current_proposal_error(
    tmp_path, monkeypatch,
):
    store = AutoReplyStore(tmp_path / "assessment-existing-card-proposal-error.sqlite3")
    seed, anchor_id = _stored_project_task(store)
    attention_id = _stored_attention_card(store, seed=seed, anchor_id=anchor_id)
    item = _work_item().model_copy(update={"summary": "售前知识库交付风险继续扩大。"})
    decision = TaskAgentDecision.model_validate({
        "project_assessments": [_stored_project_assessment(
            item, seed, anchor_id,
            outcome="needs_attention",
            reason="当前风险扩大，仍需保留关注。",
            existing_attention_id=attention_id,
            decision_indexes=[0],
            assessment_basis="historical_comparison",
            evidence=[
                {"source_ref": item.source.ref, "source_excerpt": "交付风险继续扩大"},
                {"signal_id": seed.signal_id, "source_ref": "seed:售前知识库",
                 "source_excerpt": "售前知识库历史交付风险"},
            ],
        )],
        "task_decisions": [{
            "action": "update_task", "transition": "update_fields", "task_id": seed.task_id,
            "title": "售前知识库交付", "status": "waiting",
            "source_ref": item.source.ref, "source_excerpt": "交付风险继续扩大",
            "attention_proposal": {
                "category": "watch", "title": "售前知识库交付风险",
                "why_attention": "交付风险继续扩大", "current_state": "等待交付结果",
                "ceo_action": "观察结果", "anchor_id": anchor_id,
                "assessment_basis": "historical_comparison", "material_trigger": "risk_escalation",
                "evidence": [
                    {"source_ref": item.source.ref, "source_excerpt": "交付风险继续扩大"},
                    {"signal_id": seed.signal_id, "source_ref": "seed:售前知识库",
                     "source_excerpt": "售前知识库历史交付风险"},
                ],
            },
        }],
    })
    events_before = store.list_business_attention_events(attention_id)

    def fail_projection(_self, _proposal):
        raise RuntimeError("existing card proposal failed")

    monkeypatch.setattr(BusinessAttentionProjection, "upsert", fail_projection)
    result = apply_task_agent_decision(
        store, summary_input_id=1, work_item=item, decision=decision, record_run=False,
    )

    assert result.projection_receipt is not None
    assert result.projection_receipt.status == "failed"
    assert result.projection_receipt.outcomes[0].reason == "existing card proposal failed"
    [readback] = result.projection_receipt.project_assessments
    assert readback.status == "error"
    assert readback.reason == "existing card proposal failed"
    assert readback.anchor_id == anchor_id
    assert readback.task_ids == [seed.task_id]
    assert readback.attention_id == attention_id
    assert decision.project_assessments[0].outcome == "needs_attention"
    assert store.list_business_attention_events(attention_id) == events_before


def test_negative_assessment_records_known_project_without_closing_existing_card(tmp_path):
    store = AutoReplyStore(tmp_path / "assessment-negative-keeps-card.sqlite3")
    seed, anchor_id = _stored_project_task(store)
    attention_id = _stored_attention_card(store, seed=seed, anchor_id=anchor_id)
    item = _work_item()
    decision = TaskAgentDecision.model_validate({
        "project_assessments": [_stored_project_assessment(item, seed, anchor_id)],
        "task_decisions": [],
    })
    events_before = store.list_business_attention_events(attention_id)

    result = apply_task_agent_decision(
        store, summary_input_id=1, work_item=item, decision=decision, record_run=False,
    )

    assert result.projection_receipt is not None
    [readback] = result.projection_receipt.project_assessments
    assert readback.status == "recorded"
    assert readback.anchor_id == anchor_id
    assert readback.task_ids == [seed.task_id]
    assert readback.attention_id is None
    assert store.get_business_attention_item(attention_id).status.value == "active"
    assert store.list_business_attention_events(attention_id) == events_before


@pytest.mark.parametrize(
    ("card_change", "problem"),
    [
        ("guessed", "existing Attention card does not exist"),
        ("unrelated", "existing Attention card belongs to a different Project"),
        ("inactive", "existing Attention card must be active"),
        ("wrong_member", "does not contain the assessment's supporting Tasks"),
        ("bad_proof", "existing Attention original evidence signal/source_ref does not match"),
    ],
)
def test_existing_attention_identity_and_original_proof_are_stored_facts(
    tmp_path, card_change, problem,
):
    store = AutoReplyStore(tmp_path / "assessment-existing-card-proof.sqlite3")
    seed, anchor_id = _stored_project_task(store)
    attention_id = _stored_attention_card(store, seed=seed, anchor_id=anchor_id)
    if card_change == "unrelated":
        other, other_anchor = _stored_project_task(store, title="其他项目")
        attention_id = _stored_attention_card(
            store, seed=other, anchor_id=other_anchor, title="其他项目",
        )
    elif card_change == "bad_proof":
        with store.business_task_transaction() as db:
            row = store.get_business_attention_item_in_transaction(item_id=attention_id, _db=db)
            assert row is not None
            broken = json.loads(row.assessment_json)
            broken["evidence"][0]["source_ref"] = "seed:wrong"
            store.update_business_attention_item_in_transaction(
                item=row.model_copy(update={"assessment_json": json.dumps(broken, ensure_ascii=False, sort_keys=True)}),
                _db=db,
            )
    elif card_change == "inactive":
        BusinessAttentionProjection(store).resolve(
            item_id=attention_id,
            resolution_signal_id=seed.signal_id,
            reason="历史卡片已解决",
        )
    elif card_change == "wrong_member":
        with store.business_task_transaction() as db:
            store.replace_business_attention_tasks_in_transaction(
                attention_item_id=attention_id, task_ids=(), _db=db,
            )
    elif card_change == "guessed":
        attention_id = 999
    item = _work_item()
    assessment = _stored_project_assessment(
        item, seed, anchor_id,
        outcome="needs_attention",
        reason="同一事实已由现有关注卡表示。",
        existing_attention_id=attention_id,
    )

    with pytest.raises(ValueError, match=problem):
        apply_task_agent_decision(
            store,
            summary_input_id=1,
            work_item=item,
            decision=TaskAgentDecision.model_validate({
                "project_assessments": [assessment], "task_decisions": [],
            }),
            record_run=False,
        )


def test_two_current_tasks_share_one_stored_project_judgment(tmp_path):
    store = AutoReplyStore(tmp_path / "assessment-two-tasks.sqlite3")
    first, anchor_id = _stored_project_task(store)
    second = TaskSemanticService(store).record_candidate(RecordCandidate(
        title="售前知识库回款",
        signal=SourceSignal(source_type="seed", source_ref="seed:collection",
                            evidence_text="售前知识库回款", dedupe_key="seed:collection"),
    ))
    BusinessResolutionService(store).confirm_anchor_match(
        task_id=second.task_id, anchor_id=anchor_id, evidence_signal_id=second.signal_id,
        reason="同一正式 Project", relevance=BusinessRelevance.RELEVANT,
    )
    item = _work_item()
    assessment = _stored_project_assessment(
        item, first, anchor_id,
        decision_indexes=[0, 1], task_ids=[first.task_id, second.task_id],
    )
    decisions = [{
        "action": "update_task", "transition": "update_fields", "task_id": seed.task_id,
        "title": title, "status": "waiting", "source_ref": item.source.ref,
        "source_excerpt": "补齐来源链接",
    } for seed, title in ((first, "售前知识库交付"), (second, "售前知识库回款"))]

    result = apply_task_agent_decision(
        store, summary_input_id=1, work_item=item,
        decision=TaskAgentDecision.model_validate({
            "project_assessments": [assessment], "task_decisions": decisions,
        }),
        record_run=False,
    )

    assert result.task_ids == (first.task_id, second.task_id)
    assert [(entry.decision_index, entry.task_id, entry.anchor_id) for entry in result.applied_decisions] == [
        (0, first.task_id, anchor_id), (1, second.task_id, anchor_id),
    ]


def test_current_project_proposal_reuses_stored_exact_title_and_maps_actual_identity(tmp_path):
    store = AutoReplyStore(tmp_path / "assessment-proposal-existing-project.sqlite3")
    _seed, anchor_id = _stored_project_task(store)
    base = _work_item()
    item = base.model_copy(update={
        "source": base.source.model_copy(update={"type": WorkItemSourceType.AI_MINUTES}),
        "context": base.context.model_copy(update={"source_conversation_kind": WorkItemSourceKind.MINUTES}),
        "summary": "会议决定启动售前知识库，先完成试点交付。",
    })
    decision = TaskAgentDecision.model_validate({
        "project_assessments": [{
            "project_title": "售前知识库", "project_decision_index": 0,
            "outcome": "not_needed", "reason": "当前是按计划启动，没有新增经营风险。",
            "assessment_basis": "current_observation", "decision_indexes": [0], "task_ids": [],
            "evidence": [{"source_ref": item.source.ref, "source_excerpt": "启动售前知识库"}],
        }],
        "task_decisions": [{
            "action": "record_candidate", "transition": "none", "title": "完成试点交付",
            "source_ref": item.source.ref, "source_excerpt": "完成试点交付",
            "project_proposal": {
                "title": "售前知识库", "authority": "meeting_decision",
                "source_excerpt": "会议决定启动售前知识库", "reason": "会议明确立项",
            },
        }],
    })

    result = apply_task_agent_decision(
        store, summary_input_id=1, work_item=item, decision=decision, record_run=False,
    )

    assert len(store.list_business_projects()) == 1
    assert result.applied_decisions[0].decision_index == 0
    assert result.applied_decisions[0].task_id == result.task_ids[0]
    assert result.applied_decisions[0].signal_id > 0
    assert result.applied_decisions[0].anchor_id == anchor_id


def test_unknown_current_project_clue_is_evidence_only_and_creates_nothing(tmp_path):
    store = AutoReplyStore(tmp_path / "assessment-unknown-clue.sqlite3")
    item = _work_item().model_copy(update={"summary": "也许与远期海外机会有关，但无法确认 Project 或行动。"})
    decision = TaskAgentDecision.model_validate({
        "project_assessments": [{
            "project_title": "远期海外机会", "outcome": "insufficient_evidence",
            "reason": "只有线索，无法确认 Project 身份、Task 或经营影响。",
            "assessment_basis": "current_observation", "decision_indexes": [], "task_ids": [],
            "evidence": [{"source_ref": item.source.ref, "source_excerpt": "远期海外机会"}],
        }],
        "task_decisions": [],
    })

    result = apply_task_agent_decision(
        store, summary_input_id=1, work_item=item, decision=decision, record_run=False,
    )

    assert result.task_ids == ()
    assert result.applied_decisions == ()
    assert result.projection_receipt is not None
    [readback] = result.projection_receipt.project_assessments
    assert readback.assessment_index == 0
    assert readback.status == "recorded"
    assert readback.anchor_id is None
    assert readback.task_ids == []
    assert readback.attention_id is None
    assert readback.reason == "Assessment recorded without an applied Project or Attention identity."
    assert readback.evidence[0].model_dump() == {
        "source_ref": item.source.ref,
        "source_excerpt": "远期海外机会",
        "signal_id": None,
        "source_time": item.source.created_at,
        "source_link": "",
    }
    assert store.list_business_tasks() == ()
    assert store.list_business_task_signals() == ()
    assert store.list_business_projects() == []
    assert store.list_business_attention_items() == ()


def test_no_field_change_attention_proposal_remains_unapplied_without_new_effect(tmp_path):
    store = AutoReplyStore(tmp_path / "assessment-no-change-proposal.sqlite3")
    seed = TaskSemanticService(store).record_candidate(RecordCandidate(
        title="完成试点交付",
        signal=SourceSignal(source_type="seed", source_ref="seed:trial",
                            evidence_text="完成试点交付", dedupe_key="seed:trial"),
    ))
    base = _work_item()
    item = base.model_copy(update={
        "source": base.source.model_copy(update={"type": WorkItemSourceType.AI_MINUTES}),
        "context": base.context.model_copy(update={"source_conversation_kind": WorkItemSourceKind.MINUTES}),
        "summary": "会议决定启动新试点项目，并继续完成试点交付。",
    })
    decision = TaskAgentDecision.model_validate({
        "project_assessments": [{
            "project_title": "新试点项目", "project_decision_index": 0,
            "outcome": "needs_attention", "reason": "当前原文提出风险，但 Task 字段没有变化。",
            "assessment_basis": "current_observation", "decision_indexes": [0],
            "task_ids": [seed.task_id],
            "evidence": [{"source_ref": item.source.ref, "source_excerpt": "启动新试点项目"}],
        }],
        "task_decisions": [{
            "action": "update_task", "transition": "update_fields",
            "task_id": seed.task_id, "title": "完成试点交付",
            "source_ref": item.source.ref, "source_excerpt": "完成试点交付",
            "project_proposal": {
                "title": "新试点项目", "authority": "meeting_decision",
                "source_excerpt": "会议决定启动新试点项目", "reason": "会议明确立项",
            },
            "attention_proposal": {
                "category": "watch", "title": "新试点项目风险",
                "why_attention": "需要观察", "current_state": "等待来源",
                "ceo_action": "暂不介入", "anchor_id": None,
                "assessment_basis": "current_observation",
                "material_trigger": "risk_escalation",
                "evidence": [{
                    "source_ref": item.source.ref,
                    "source_excerpt": "启动新试点项目",
                }],
            },
        }],
    })
    signals_before = store.list_business_task_signals()

    result = apply_task_agent_decision(
        store, summary_input_id=1, work_item=item, decision=decision, record_run=False,
    )

    assert result.applied_decisions == ()
    assert result.attention_proposals == ()
    assert result.projection_receipt is not None
    assert [outcome.reason for outcome in result.projection_receipt.outcomes] == [
        "proposal has no applied Task decision"
    ]
    [readback] = result.projection_receipt.project_assessments
    assert readback.status == "rejected"
    assert readback.anchor_id is None
    assert readback.task_ids == []
    assert readback.attention_id is None
    assert readback.reason == "proposal has no applied Task decision"
    assert store.list_business_task_signals() == signals_before
    assert store.list_business_projects() == []
    assert store.list_business_task_anchor_links(task_id=seed.task_id) == ()
    assert store.list_business_attention_items() == ()


def test_unapplied_exact_title_proposal_keeps_verified_existing_project_and_task_ids(tmp_path):
    store = AutoReplyStore(tmp_path / "assessment-existing-project-no-change.sqlite3")
    seed, anchor_id = _stored_project_task(store)
    base = _work_item()
    item = base.model_copy(update={
        "source": base.source.model_copy(update={"type": WorkItemSourceType.AI_MINUTES}),
        "context": base.context.model_copy(update={"source_conversation_kind": WorkItemSourceKind.MINUTES}),
        "summary": "会议再次确认售前知识库，售前知识库交付风险需要观察。",
    })
    decision = TaskAgentDecision.model_validate({
        "project_assessments": [{
            "project_title": "售前知识库", "project_decision_index": 0,
            "outcome": "needs_attention", "reason": "交付风险需要观察。",
            "assessment_basis": "current_observation", "decision_indexes": [0],
            "task_ids": [seed.task_id],
            "evidence": [{"source_ref": item.source.ref, "source_excerpt": "交付风险需要观察"}],
        }],
        "task_decisions": [{
            "action": "update_task", "transition": "update_fields", "task_id": seed.task_id,
            "title": "售前知识库交付", "source_ref": item.source.ref,
            "source_excerpt": "售前知识库交付风险需要观察",
            "project_proposal": {
                "title": "售前知识库", "authority": "meeting_decision",
                "source_excerpt": "会议再次确认售前知识库", "reason": "会议确认既有项目",
            },
            "attention_proposal": {
                "category": "watch", "title": "售前知识库交付风险",
                "why_attention": "交付风险需要观察", "current_state": "等待交付结果",
                "ceo_action": "观察结果", "anchor_id": None,
                "assessment_basis": "current_observation", "material_trigger": "risk_escalation",
                "evidence": [{"source_ref": item.source.ref, "source_excerpt": "交付风险需要观察"}],
            },
        }],
    })
    signals_before = store.list_business_task_signals()

    result = apply_task_agent_decision(
        store, summary_input_id=1, work_item=item, decision=decision, record_run=False,
    )

    assert result.applied_decisions == ()
    assert result.projection_receipt is not None
    [readback] = result.projection_receipt.project_assessments
    assert readback.status == "rejected"
    assert readback.reason == "proposal has no applied Task decision"
    assert readback.anchor_id == anchor_id
    assert readback.task_ids == [seed.task_id]
    assert readback.attention_id is None
    assert readback.evidence[0].signal_id is None
    assert store.list_business_task_signals() == signals_before
    assert store.list_business_attention_items() == ()


def test_assessment_task_id_maps_matching_signal_without_decision_index(tmp_path):
    store = AutoReplyStore(tmp_path / "assessment-task-id-signal-readback.sqlite3")
    seed, anchor_id = _stored_project_task(store)
    item = _work_item().model_copy(update={"summary": "售前知识库交付补齐本轮来源。"})
    decision = TaskAgentDecision.model_validate({
        "project_assessments": [{
            "project_title": "售前知识库", "anchor_id": anchor_id,
            "outcome": "not_needed", "reason": "本轮只补来源，没有新增经营影响。",
            "assessment_basis": "current_observation", "decision_indexes": [],
            "task_ids": [seed.task_id],
            "evidence": [{"source_ref": item.source.ref, "source_excerpt": "补齐本轮来源"}],
        }],
        "task_decisions": [{
            "action": "update_task", "transition": "update_fields", "task_id": seed.task_id,
            "title": "售前知识库交付", "status": "waiting",
            "source_ref": item.source.ref, "source_excerpt": "补齐本轮来源",
        }],
    })

    result = apply_task_agent_decision(
        store, summary_input_id=1, work_item=item, decision=decision, record_run=False,
    )

    assert result.applied_decisions[0].task_id == seed.task_id
    assert result.projection_receipt is not None
    [readback] = result.projection_receipt.project_assessments
    assert readback.status == "recorded"
    assert readback.evidence[0].signal_id == result.applied_decisions[0].signal_id


def test_stored_assessment_rejects_attention_anchor_contradicting_canonical_project_before_writes(tmp_path):
    store = AutoReplyStore(tmp_path / "assessment-selector-contradiction.sqlite3")
    _first, first_anchor = _stored_project_task(store)
    _second, second_anchor = _stored_project_task(store, title="其他项目")
    base = _work_item()
    item = base.model_copy(update={
        "source": base.source.model_copy(update={"type": WorkItemSourceType.AI_MINUTES}),
        "context": base.context.model_copy(update={"source_conversation_kind": WorkItemSourceKind.MINUTES}),
        "summary": "会议决定启动售前知识库，但交付风险需要观察。",
    })
    decision = TaskAgentDecision.model_validate({
        "project_assessments": [{
            "project_title": "售前知识库", "project_decision_index": 0,
            "outcome": "needs_attention", "reason": "当前交付风险需要观察。",
            "assessment_basis": "current_observation", "decision_indexes": [0], "task_ids": [],
            "evidence": [{"source_ref": item.source.ref, "source_excerpt": "交付风险需要观察"}],
        }],
        "task_decisions": [{
            "action": "record_candidate", "transition": "none", "title": "完成交付",
            "source_ref": item.source.ref, "source_excerpt": "交付风险需要观察",
            "project_proposal": {
                "title": "售前知识库", "authority": "meeting_decision",
                "source_excerpt": "会议决定启动售前知识库", "reason": "会议明确立项",
            },
            "attention_proposal": {
                "category": "watch", "title": "交付风险", "why_attention": "风险待核实",
                "current_state": "等待交付", "ceo_action": "观察结果",
                "anchor_id": second_anchor, "assessment_basis": "current_observation",
                "material_trigger": "risk_escalation",
                "evidence": [{"source_ref": item.source.ref, "source_excerpt": "交付风险需要观察"}],
            },
        }],
    })
    tasks_before = store.list_business_tasks()
    signals_before = store.list_business_task_signals()

    with pytest.raises(ValueError, match="different canonical stored Project"):
        apply_task_agent_decision(
            store, summary_input_id=1, work_item=item, decision=decision, record_run=False,
        )

    assert first_anchor != second_anchor
    assert store.list_business_tasks() == tasks_before
    assert store.list_business_task_signals() == signals_before


def test_stored_assessment_accepts_project_proposal_and_attention_on_same_canonical_project(tmp_path):
    store = AutoReplyStore(tmp_path / "assessment-selector-canonical-control.sqlite3")
    _seed, anchor_id = _stored_project_task(store)
    base = _work_item()
    item = base.model_copy(update={
        "source": base.source.model_copy(update={"type": WorkItemSourceType.AI_MINUTES}),
        "context": base.context.model_copy(update={"source_conversation_kind": WorkItemSourceKind.MINUTES}),
        "summary": "会议决定启动售前知识库，但交付风险需要观察。",
    })
    decision = TaskAgentDecision.model_validate({
        "project_assessments": [{
            "project_title": "售前知识库", "project_decision_index": 0,
            "outcome": "needs_attention", "reason": "当前交付风险需要观察。",
            "assessment_basis": "current_observation", "decision_indexes": [0], "task_ids": [],
            "evidence": [{"source_ref": item.source.ref, "source_excerpt": "交付风险需要观察"}],
        }],
        "task_decisions": [{
            "action": "record_candidate", "transition": "none", "title": "完成交付",
            "source_ref": item.source.ref, "source_excerpt": "交付风险需要观察",
            "project_proposal": {
                "title": "售前知识库", "authority": "meeting_decision",
                "source_excerpt": "会议决定启动售前知识库", "reason": "会议明确立项",
            },
            "attention_proposal": {
                "category": "watch", "title": "交付风险", "why_attention": "风险待核实",
                "current_state": "等待交付", "ceo_action": "观察结果",
                "anchor_id": anchor_id, "assessment_basis": "current_observation",
                "material_trigger": "risk_escalation",
                "evidence": [{"source_ref": item.source.ref, "source_excerpt": "交付风险需要观察"}],
            },
        }],
    })

    result = apply_task_agent_decision(
        store, summary_input_id=1, work_item=item, decision=decision, record_run=False,
    )

    assert result.applied_decisions[0].anchor_id == anchor_id
    assert result.attention_proposals[0].anchor_id == anchor_id


def test_new_project_proposal_rejects_attention_anchor_for_different_registered_project_before_writes(tmp_path):
    store = AutoReplyStore(tmp_path / "assessment-new-selector-contradiction.sqlite3")
    _other, other_anchor = _stored_project_task(store, title="另一项目")
    base = _work_item()
    item = base.model_copy(update={
        "source": base.source.model_copy(update={"type": WorkItemSourceType.AI_MINUTES}),
        "context": base.context.model_copy(update={"source_conversation_kind": WorkItemSourceKind.MINUTES}),
        "summary": "会议决定启动新业务项目，但交付风险需要观察。",
    })
    decision = TaskAgentDecision.model_validate({
        "project_assessments": [{
            "project_title": "新业务项目", "project_decision_index": 0,
            "outcome": "needs_attention", "reason": "当前交付风险需要观察。",
            "assessment_basis": "current_observation", "decision_indexes": [0], "task_ids": [],
            "evidence": [{"source_ref": item.source.ref, "source_excerpt": "交付风险需要观察"}],
        }],
        "task_decisions": [{
            "action": "record_candidate", "transition": "none", "title": "完成新业务交付",
            "source_ref": item.source.ref, "source_excerpt": "交付风险需要观察",
            "project_proposal": {
                "title": "新业务项目", "authority": "meeting_decision",
                "source_excerpt": "会议决定启动新业务项目", "reason": "会议明确立项",
            },
            "attention_proposal": {
                "category": "watch", "title": "新业务交付风险", "why_attention": "风险待核实",
                "current_state": "等待交付", "ceo_action": "观察结果",
                "anchor_id": other_anchor, "assessment_basis": "current_observation",
                "material_trigger": "risk_escalation",
                "evidence": [{"source_ref": item.source.ref, "source_excerpt": "交付风险需要观察"}],
            },
        }],
    })
    tasks_before = store.list_business_tasks()
    signals_before = store.list_business_task_signals()
    projects_before = store.list_business_projects()

    with pytest.raises(ValueError, match="different canonical stored Project"):
        apply_task_agent_decision(
            store, summary_input_id=1, work_item=item, decision=decision, record_run=False,
        )

    assert store.list_business_tasks() == tasks_before
    assert store.list_business_task_signals() == signals_before
    assert store.list_business_projects() == projects_before


def test_new_project_attention_rejects_guessed_future_anchor_and_accepts_null_resolution(tmp_path):
    store = AutoReplyStore(tmp_path / "assessment-new-project-future-anchor.sqlite3")
    _first, _first_anchor = _stored_project_task(store)
    _second, _second_anchor = _stored_project_task(store, title="另一项目")
    guessed_future_anchor = 3
    with store.business_task_transaction() as db:
        assert store.get_business_anchor_in_transaction(
            anchor_id=guessed_future_anchor, _db=db,
        ) is None
    base = _work_item()
    item = base.model_copy(update={
        "source": base.source.model_copy(update={"type": WorkItemSourceType.AI_MINUTES}),
        "context": base.context.model_copy(update={"source_conversation_kind": WorkItemSourceKind.MINUTES}),
        "summary": "会议决定启动新业务项目，但交付风险需要观察。",
    })
    payload = {
        "project_assessments": [{
            "project_title": "新业务项目", "project_decision_index": 0,
            "outcome": "needs_attention", "reason": "当前交付风险需要观察。",
            "assessment_basis": "current_observation", "decision_indexes": [0], "task_ids": [],
            "evidence": [{"source_ref": item.source.ref, "source_excerpt": "交付风险需要观察"}],
        }],
        "task_decisions": [{
            "action": "record_candidate", "transition": "none", "title": "完成新业务交付",
            "source_ref": item.source.ref, "source_excerpt": "交付风险需要观察",
            "project_proposal": {
                "title": "新业务项目", "authority": "meeting_decision",
                "source_excerpt": "会议决定启动新业务项目", "reason": "会议明确立项",
            },
            "attention_proposal": {
                "category": "watch", "title": "新业务交付风险", "why_attention": "风险待核实",
                "current_state": "等待交付", "ceo_action": "观察结果",
                "anchor_id": guessed_future_anchor, "assessment_basis": "current_observation",
                "material_trigger": "risk_escalation",
                "evidence": [{"source_ref": item.source.ref, "source_excerpt": "交付风险需要观察"}],
            },
        }],
    }
    tasks_before = store.list_business_tasks()
    signals_before = store.list_business_task_signals()

    with pytest.raises(ValueError, match="registered active official Project"):
        apply_task_agent_decision(
            store, summary_input_id=1, work_item=item,
            decision=TaskAgentDecision.model_validate(payload), record_run=False,
        )

    assert store.list_business_tasks() == tasks_before
    assert store.list_business_task_signals() == signals_before
    payload["task_decisions"][0]["attention_proposal"]["anchor_id"] = None
    result = apply_task_agent_decision(
        store, summary_input_id=2, work_item=item,
        decision=TaskAgentDecision.model_validate(payload), record_run=False,
    )
    project = next(project for project in store.list_business_projects()
                   if project.title == "新业务项目")
    assert result.applied_decisions[0].anchor_id == project.canonical_anchor_id
    assert result.attention_proposals[0].anchor_id == project.canonical_anchor_id
    assert result.projection_receipt is not None
    assert result.projection_receipt.status == "completed"


def test_existing_attention_requires_assessment_to_cite_that_cards_original_proof(tmp_path):
    store = AutoReplyStore(tmp_path / "assessment-card-proof-binding.sqlite3")
    seed, anchor_id = _stored_project_task(store)
    attention_id = _stored_attention_card(store, seed=seed, anchor_id=anchor_id)
    other_signal_id = store.create_business_task_signal(
        source_type="seed", source_ref="seed:other-proof",
        evidence_text="另一条真实历史事实", dedupe_key="seed:other-proof",
    )
    store.link_business_task_evidence(
        task_id=seed.task_id, signal_id=other_signal_id, evidence_role="discovery",
    )
    item = _work_item()
    current_only = _stored_project_assessment(
        item, seed, anchor_id, outcome="needs_attention",
        reason="声称现有卡片已经表示该事实。", existing_attention_id=attention_id,
    )
    unrelated_history = _stored_project_assessment(
        item, seed, anchor_id, outcome="needs_attention",
        reason="引用同项目的另一条历史事实。", existing_attention_id=attention_id,
        assessment_basis="historical_comparison",
        evidence=[
            {"source_ref": item.source.ref, "source_excerpt": "补齐来源链接"},
            {"signal_id": other_signal_id, "source_ref": "seed:other-proof",
             "source_excerpt": "另一条真实历史事实"},
        ],
        task_ids=[seed.task_id],
    )

    for assessment in (current_only, unrelated_history):
        with pytest.raises(ValueError, match="must cite this card's stored original evidence"):
            apply_task_agent_decision(
                store, summary_input_id=1, work_item=item,
                decision=TaskAgentDecision.model_validate({
                    "project_assessments": [assessment], "task_decisions": [],
                }),
                record_run=False,
            )


def test_existing_attention_changed_current_words_with_actual_old_proof_is_idempotent(tmp_path):
    store = AutoReplyStore(tmp_path / "assessment-card-old-proof-repeat.sqlite3")
    seed, anchor_id = _stored_project_task(store)
    attention_id = _stored_attention_card(store, seed=seed, anchor_id=anchor_id)
    item = _work_item().model_copy(update={
        "summary": "这次使用不同措辞说明仍需观察，但没有新的 Task 字段。"
    })
    assessment = _stored_project_assessment(
        item, seed, anchor_id, outcome="needs_attention",
        reason="新消息与卡片保存的原始风险一起支持继续观察。",
        existing_attention_id=attention_id,
        assessment_basis="historical_comparison",
        evidence=[
            {"source_ref": item.source.ref, "source_excerpt": "不同措辞说明仍需观察"},
            {"signal_id": seed.signal_id, "source_ref": "seed:售前知识库",
             "source_excerpt": "售前知识库历史交付风险"},
        ],
    )
    events_before = store.list_business_attention_events(attention_id)

    result = apply_task_agent_decision(
        store, summary_input_id=1, work_item=item,
        decision=TaskAgentDecision.model_validate({
            "project_assessments": [assessment], "task_decisions": [],
        }),
        record_run=False,
    )

    assert result.task_ids == ()
    assert result.applied_decisions == ()
    assert store.list_business_attention_events(attention_id) == events_before
    assert len(store.list_business_attention_items()) == 1


def test_stored_assessment_membership_checks_do_not_materialize_full_task_evidence(
    tmp_path, monkeypatch,
):
    store = AutoReplyStore(tmp_path / "assessment-bounded-evidence-membership.sqlite3")
    seed, anchor_id = _stored_project_task(store)
    second_signal_id = store.create_business_task_signal(
        source_type="seed", source_ref="seed:second-card-proof",
        evidence_text="第二条历史交付事实", dedupe_key="seed:second-card-proof",
    )
    store.link_business_task_evidence(
        task_id=seed.task_id, signal_id=second_signal_id, evidence_role="discovery",
    )
    stored_evidence = [
        {
            "signal_id": seed.signal_id,
            "source_ref": "seed:售前知识库",
            "source_excerpt": "售前知识库历史交付风险",
            "source_time": "",
            "source_link": "",
        },
        {
            "signal_id": second_signal_id,
            "source_ref": "seed:second-card-proof",
            "source_excerpt": "第二条历史交付事实",
            "source_time": "",
            "source_link": "",
        },
    ]
    attention_id = BusinessAttentionProjection(store).upsert(AttentionProposal(
        stable_key=f"project:{anchor_id}",
        category=AttentionCategory.WATCH,
        title="售前知识库交付风险",
        business_area="",
        why_attention="两条历史事实仍需观察",
        current_state="等待新的交付结果",
        ceo_action="暂不介入，观察结果",
        anchor_id=anchor_id,
        task_ids=(seed.task_id,),
        evidence_signal_id=seed.signal_id,
        assessment_json=json.dumps({"evidence": stored_evidence}, ensure_ascii=False),
    ))
    item = _work_item()
    assessment = _stored_project_assessment(
        item, seed, anchor_id,
        outcome="needs_attention",
        reason="当前来源与两条卡片原始事实支持继续观察。",
        existing_attention_id=attention_id,
        assessment_basis="historical_comparison",
        evidence=[
            {"source_ref": item.source.ref, "source_excerpt": "补齐来源链接"},
            *(
                {
                    "signal_id": proof["signal_id"],
                    "source_ref": proof["source_ref"],
                    "source_excerpt": proof["source_excerpt"],
                }
                for proof in stored_evidence
            ),
        ],
    )

    def reject_full_history_materialization(*_args, **_kwargs):
        raise AssertionError("stored assessment validation must use bounded membership lookup")

    monkeypatch.setattr(
        store,
        "list_business_task_evidence_in_transaction",
        reject_full_history_materialization,
    )

    result = apply_task_agent_decision(
        store, summary_input_id=1, work_item=item,
        decision=TaskAgentDecision.model_validate({
            "project_assessments": [assessment], "task_decisions": [],
        }),
        record_run=False,
    )

    assert result.task_ids == ()
    assert result.applied_decisions == ()


def test_existing_attention_same_current_source_quote_can_cite_card_original_without_signal_id(tmp_path):
    store = AutoReplyStore(tmp_path / "assessment-card-current-repeat.sqlite3")
    seed, anchor_id = _stored_project_task(store)
    attention_id = _stored_attention_card(store, seed=seed, anchor_id=anchor_id)
    base = _work_item()
    item = base.model_copy(update={
        "source": base.source.model_copy(update={"ref": "seed:售前知识库"}),
        "summary": "售前知识库历史交付风险",
    })
    assessment = _stored_project_assessment(
        item, seed, anchor_id, outcome="needs_attention",
        reason="同一原始来源重放，现有卡片继续代表该事实。",
        existing_attention_id=attention_id,
        evidence=[{
            "source_ref": "seed:售前知识库",
            "source_excerpt": "售前知识库历史交付风险",
        }],
    )
    events_before = store.list_business_attention_events(attention_id)

    result = apply_task_agent_decision(
        store, summary_input_id=1, work_item=item,
        decision=TaskAgentDecision.model_validate({
            "project_assessments": [assessment], "task_decisions": [],
        }),
        record_run=False,
    )

    assert result.projection_receipt is not None
    [readback] = result.projection_receipt.project_assessments
    assert readback.status == "existing"
    assert readback.attention_id == attention_id
    assert readback.evidence[0].signal_id == seed.signal_id
    assert store.list_business_attention_events(attention_id) == events_before


def test_applied_mapping_does_not_claim_project_from_assessment_without_actual_link(tmp_path):
    store = AutoReplyStore(tmp_path / "assessment-truthful-actual-map.sqlite3")
    seed, anchor_id = _stored_project_task(store)
    item = _work_item()
    decision = TaskAgentDecision.model_validate({
        "project_assessments": [_stored_project_assessment(
            item, seed, anchor_id, decision_indexes=[0], task_ids=[],
        )],
        "task_decisions": [{
            "action": "record_candidate", "transition": "none", "title": "新建但未关联的 Task",
            "source_ref": item.source.ref, "source_excerpt": "补齐来源链接",
        }],
    })

    result = apply_task_agent_decision(
        store, summary_input_id=1, work_item=item, decision=decision, record_run=False,
    )

    assert result.applied_decisions[0].anchor_id is None
    assert store.list_business_task_anchor_links(task_id=result.task_ids[0]) == ()


def test_applied_mapping_uses_unique_actual_confirmed_project_without_support_index(tmp_path):
    store = AutoReplyStore(tmp_path / "assessment-map-unique-confirmed-project.sqlite3")
    seed, anchor_id = _stored_project_task(store)
    item = _work_item()
    assessment = _stored_project_assessment(
        item, seed, anchor_id, decision_indexes=[], task_ids=[seed.task_id],
    )
    decision = TaskAgentDecision.model_validate({
        "project_assessments": [assessment],
        "task_decisions": [{
            "action": "update_task", "transition": "update_fields", "task_id": seed.task_id,
            "title": "售前知识库交付", "status": "waiting",
            "source_ref": item.source.ref, "source_excerpt": "补齐来源链接",
        }],
    })

    result = apply_task_agent_decision(
        store, summary_input_id=1, work_item=item, decision=decision, record_run=False,
    )

    assert result.applied_decisions[0].anchor_id == anchor_id


def test_new_project_rejects_unrelated_existing_task_without_matching_supported_decision(tmp_path):
    store = AutoReplyStore(tmp_path / "assessment-new-project-unrelated-task.sqlite3")
    unrelated = TaskSemanticService(store).record_candidate(RecordCandidate(
        title="无关旧 Task",
        signal=SourceSignal(source_type="seed", source_ref="seed:unrelated-old",
                            evidence_text="无关旧 Task", dedupe_key="seed:unrelated-old"),
    ))
    base = _work_item()
    item = base.model_copy(update={
        "source": base.source.model_copy(update={"type": WorkItemSourceType.AI_MINUTES}),
        "context": base.context.model_copy(update={"source_conversation_kind": WorkItemSourceKind.MINUTES}),
        "summary": "会议决定启动全新项目，并完成新的交付。",
    })
    decision = TaskAgentDecision.model_validate({
        "project_assessments": [{
            "project_title": "全新项目", "project_decision_index": 0,
            "outcome": "not_needed", "reason": "当前只是正常立项。",
            "assessment_basis": "current_observation", "decision_indexes": [0],
            "task_ids": [unrelated.task_id],
            "evidence": [{"source_ref": item.source.ref, "source_excerpt": "启动全新项目"}],
        }],
        "task_decisions": [{
            "action": "record_candidate", "transition": "none", "title": "完成新的交付",
            "source_ref": item.source.ref, "source_excerpt": "完成新的交付",
            "project_proposal": {
                "title": "全新项目", "authority": "meeting_decision",
                "source_excerpt": "会议决定启动全新项目", "reason": "会议明确立项",
            },
        }],
    })
    tasks_before = store.list_business_tasks()

    with pytest.raises(ValueError, match="not supported by a matching current Project decision"):
        apply_task_agent_decision(
            store, summary_input_id=1, work_item=item, decision=decision, record_run=False,
        )

    assert store.list_business_tasks() == tasks_before
    assert store.list_business_projects() == []


def test_new_project_rejects_indexed_existing_task_without_matching_project_selector(tmp_path):
    store = AutoReplyStore(tmp_path / "assessment-new-project-indexed-unrelated.sqlite3")
    unrelated = TaskSemanticService(store).record_candidate(RecordCandidate(
        title="无关旧 Task",
        signal=SourceSignal(source_type="seed", source_ref="seed:indexed-unrelated",
                            evidence_text="无关旧 Task", dedupe_key="seed:indexed-unrelated"),
    ))
    base = _work_item()
    item = base.model_copy(update={
        "source": base.source.model_copy(update={"type": WorkItemSourceType.AI_MINUTES}),
        "context": base.context.model_copy(update={"source_conversation_kind": WorkItemSourceKind.MINUTES}),
        "summary": "会议决定启动全新项目，完成新的交付，并更新无关旧 Task。",
    })
    proposal = {
        "title": "全新项目", "authority": "meeting_decision",
        "source_excerpt": "会议决定启动全新项目", "reason": "会议明确立项",
    }
    decision = TaskAgentDecision.model_validate({
        "project_assessments": [{
            "project_title": "全新项目", "project_decision_index": 0,
            "outcome": "not_needed", "reason": "当前只是正常立项。",
            "assessment_basis": "current_observation", "decision_indexes": [0, 1],
            "task_ids": [],
            "evidence": [{"source_ref": item.source.ref, "source_excerpt": "启动全新项目"}],
        }],
        "task_decisions": [
            {
                "action": "record_candidate", "transition": "none", "title": "完成新的交付",
                "source_ref": item.source.ref, "source_excerpt": "完成新的交付",
                "project_proposal": proposal,
            },
            {
                "action": "update_task", "transition": "update_fields", "task_id": unrelated.task_id,
                "title": "无关旧 Task", "status": "waiting",
                "source_ref": item.source.ref, "source_excerpt": "更新无关旧 Task",
            },
        ],
    })
    tasks_before = store.list_business_tasks()
    signals_before = store.list_business_task_signals()

    with pytest.raises(ValueError, match="not supported by a matching current Project decision"):
        apply_task_agent_decision(
            store, summary_input_id=1, work_item=item, decision=decision, record_run=False,
        )

    assert store.list_business_tasks() == tasks_before
    assert store.list_business_task_signals() == signals_before


def test_new_project_accepts_existing_task_only_when_matching_current_decision_links_it(tmp_path):
    store = AutoReplyStore(tmp_path / "assessment-new-project-supported-task.sqlite3")
    existing = TaskSemanticService(store).record_candidate(RecordCandidate(
        title="既有交付",
        signal=SourceSignal(source_type="seed", source_ref="seed:existing-delivery",
                            evidence_text="既有交付", dedupe_key="seed:existing-delivery"),
    ))
    base = _work_item()
    item = base.model_copy(update={
        "source": base.source.model_copy(update={"type": WorkItemSourceType.AI_MINUTES}),
        "context": base.context.model_copy(update={"source_conversation_kind": WorkItemSourceKind.MINUTES}),
        "summary": "会议决定启动全新项目，完成新的交付，并更新既有交付。",
    })
    proposal = {
        "title": "全新项目", "authority": "meeting_decision",
        "source_excerpt": "会议决定启动全新项目", "reason": "会议明确立项",
    }
    decision = TaskAgentDecision.model_validate({
        "project_assessments": [{
            "project_title": "全新项目", "project_decision_index": 0,
            "outcome": "not_needed", "reason": "当前只是正常立项。",
            "assessment_basis": "current_observation", "decision_indexes": [0, 1],
            "task_ids": [existing.task_id],
            "evidence": [{"source_ref": item.source.ref, "source_excerpt": "启动全新项目"}],
        }],
        "task_decisions": [
            {
                "action": "record_candidate", "transition": "none", "title": "完成新的交付",
                "source_ref": item.source.ref, "source_excerpt": "完成新的交付",
                "project_proposal": proposal,
            },
            {
                "action": "update_task", "transition": "update_fields", "task_id": existing.task_id,
                "title": "既有交付", "status": "waiting",
                "source_ref": item.source.ref, "source_excerpt": "更新既有交付",
                "project_proposal": proposal,
            },
        ],
    })

    result = apply_task_agent_decision(
        store, summary_input_id=1, work_item=item, decision=decision, record_run=False,
    )

    project = store.list_business_projects()[0]
    assert all(entry.anchor_id == project.canonical_anchor_id for entry in result.applied_decisions)
    assert store.list_business_task_anchor_links(task_id=existing.task_id)[0].anchor_id == project.canonical_anchor_id
