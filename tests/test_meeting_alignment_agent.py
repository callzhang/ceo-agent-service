import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.meeting_alignment_agent import (
    MeetingAlignmentAgent,
    MeetingAlignmentCodexRunner,
    MeetingAlignmentTargetError,
    build_meeting_alignment_prompt,
    parse_meeting_alignment_decision,
)
from app.meeting_alignment_models import (
    MEETING_ALIGNMENT_CROSS_FIELD_RULES,
    MeetingSource,
)
from tests.test_meeting_alignment_models import (
    cross_field_rule_payloads,
    valid_send_decision,
)


class FakeRoutedMeetingExecution:
    def __init__(self, raw, *, session_id="meeting-session", transcript_end=9):
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


def test_meeting_runner_translates_exhausted_capacity_for_attempt_limit_retry():
    from app.agent_runtime_contracts import RuntimeFailureClass
    from app.agent_runtime_router import RoutedCodexExecutionError
    from app.external_retry import ExternalDependencyError

    class FailingRoutedExecution:
        def execute(self, **kwargs):
            raise RoutedCodexExecutionError(
                "runtime_execution_failed",
                failure_class=RuntimeFailureClass.CAPACITY,
                failure_code="codex_provider_unavailable",
                retryable_external_dependency=True,
            )

    runner = MeetingAlignmentCodexRunner(
        routed_execution=FailingRoutedExecution()
    )
    with pytest.raises(ExternalDependencyError) as raised:
        runner.decide(prompt="decide", run_id=9)
    assert raised.value.dependency == "codex"


def test_meeting_runner_keeps_auth_failure_terminal():
    from app.agent_runtime_contracts import RuntimeFailureClass
    from app.agent_runtime_router import RoutedCodexExecutionError

    class FailingRoutedExecution:
        def execute(self, **kwargs):
            raise RoutedCodexExecutionError(
                "runtime_execution_failed",
                failure_class=RuntimeFailureClass.AUTHENTICATION,
                failure_code="codex_login_required",
                retryable_external_dependency=False,
            )

    runner = MeetingAlignmentCodexRunner(
        routed_execution=FailingRoutedExecution()
    )
    with pytest.raises(RoutedCodexExecutionError) as raised:
        runner.decide(prompt="decide", run_id=9)
    assert raised.value.failure_code == "codex_login_required"


@pytest.fixture(autouse=True)
def _use_canonical_developer_prompt(monkeypatch):
    monkeypatch.setenv(
        "CEO_DEVELOPER_PROMPT_TEMPLATE_PATH",
        str(
            Path(__file__).resolve().parents[1]
            / "app"
            / "defaults"
            / "developer_prompt.md"
        ),
    )


def source(*, participant_count: int = 3) -> MeetingSource:
    participants = [
        {"name": "Derek", "user_id": "derek"},
        {"name": "Alex", "user_id": "alex"},
        {"name": "Mina", "user_id": "mina"},
    ][:participant_count]
    return MeetingSource.model_validate(
        {
            "meeting_id": "minutes-1",
            "title": "上线范围评审",
            "status": "ended",
            "started_at": "2026-07-14T10:00:00+08:00",
            "ended_at": "2026-07-14T11:00:00+08:00",
            "participants": participants,
            "attendee_evidence": "calendar",
            "attendee_roster_complete": True,
            "creator": participants[1] if participant_count > 2 else None,
            "current_user_id": "derek",
            "summary": "Alex 主张全量，Mina 主张灰度。",
            "transcript": [
                {
                    "speaker_name": "Alex",
                    "text": "我建议全量上线以验证收入。",
                },
                {
                    "speaker_name": "Mina",
                    "text": "我建议先灰度以控制故障面。",
                },
                {
                    "speaker_name": "Derek",
                    "text": "先定义可接受的故障面，再倒推范围。",
                },
            ],
            "source_url": "https://example.test/minutes-1",
        }
    )


def source_with_unresolved_one_to_one_counterpart() -> MeetingSource:
    payload = source(participant_count=2).model_dump(mode="json")
    payload["participants"][1].update(
        user_id="",
        open_dingtalk_id="open-alex-evidence",
    )
    return MeetingSource.model_validate(payload)


def summary_payload() -> dict:
    return {
        "action": "send",
        "audience_scope": "business",
        "trigger_reasons": ["meeting_summary"],
        "topics": [],
        "derek_viewpoint": None,
        "key_questions": [],
        "mention_names": [],
        "target": {
            "kind": "group",
            "conversation_id": "cid-summary",
            "direct_user_id": "",
            "title": "业务群",
            "candidates": [
                {
                    "conversation_id": "cid-summary",
                    "title": "业务群",
                    "evidence": ["承接本次会议主题"],
                }
            ],
        },
        "final_message": "会议总结｜已确认事项与下一步请以本次会议结论执行。",
        "audit_summary": "会议没有实质分歧，仍发送简短总结。",
        "confidence": 0.9,
    }


def test_meeting_runner_routes_persisted_run_fresh_with_exact_capabilities(tmp_path):
    routed = FakeRoutedMeetingExecution(
        json.dumps(summary_payload(), ensure_ascii=False)
    )
    runner = MeetingAlignmentCodexRunner(routed_execution=routed)

    decision = runner.decide(prompt="decide", run_id=52)

    call = routed.calls[0]
    assert call["workload_kind"] == "meeting"
    assert call["workload_key"] == "52"
    assert call["conversation_id"] is None
    assert call["required_capabilities"] == frozenset(
        {
            "structured_output",
            "local_schema_validation",
        }
    )
    instructions = call["command_factory"].developer_instructions
    assert "Use only reviewed read tools." not in instructions
    assert "does not reinterpret provider-specific commands or tools" in instructions
    assert decision.action == "send"
    assert runner.last_session_id == "meeting-session"


def derek_view_payload(*, historical_sources: list[str]) -> dict:
    return {
        "action": "send",
        "audience_scope": "business",
        "trigger_reasons": ["derek_viewpoint"],
        "topics": [],
        "derek_viewpoint": {
            "expressed_view": "先定义可接受的故障面，再倒推范围。",
            "meeting_evidence": ["Derek 在会议中明确说出该句"],
            "omitted_layer": "风险预算决定发布范围",
            "plain_explanation": (
                "先定最多能损失什么，再决定一次放多少量。"
            ),
            "analogy": "像先确定船能承受多大的浪，再决定航线。",
            "example": (
                "若最多允许 1% 用户受影响，就按监控和回滚能力定灰度量。"
            ),
            "historical_sources": historical_sources,
        },
        "key_questions": [],
        "mention_names": ["Alex", "Mina"],
        "target": {
            "kind": "group",
            "conversation_id": "cid-1",
            "direct_user_id": "",
            "title": "上线项目群",
            "candidates": [
                {
                    "conversation_id": "cid-1",
                    "title": "上线项目群",
                    "evidence": ["会前讨论了同一上线范围"],
                }
            ],
        },
        "final_message": (
            "Derek 的观点输出解读\n\n先定风险预算，再倒推上线范围。"
        ),
        "audit_summary": "Derek 的观点在后续讨论中没有被完整还原。",
        "confidence": 0.85,
    }


class FakeMeetingCodex:
    last_session_id = "meeting-session"
    last_transcript_start_line = 0
    last_transcript_end_line = 10
    last_audit_tool_events = []

    def __init__(self, payload: dict):
        self.payload = payload

    def decide(self, *, prompt: str):
        from app.meeting_alignment_models import MeetingAlignmentDecision

        return MeetingAlignmentDecision.model_validate(self.payload)


def send_payload_with_target(target) -> dict:
    payload = derek_view_payload(historical_sources=[])
    payload["target"] = target
    return payload


def test_prompt_contains_full_transcript_and_behavioral_contracts():
    prompt = build_meeting_alignment_prompt(
        source(),
        work_profile="重视端到端结果",
        work_profile_source="/configured/work_profile.md",
    )

    assert "我建议全量上线以验证收入" in prompt
    assert "每场会议均须发送一条总结" in prompt
    assert "不得返回 no_action" in prompt
    assert "后来明确对齐也仍然触发发布" in prompt
    assert "沉默不算对齐" in prompt
    assert "明确同意、承诺或复述一致" in prompt
    assert "aligned_disagreement" in prompt
    assert "unresolved_disagreement" in prompt
    assert "可以提出多个问题" in prompt
    assert "完成对齐所需的最小集合" in prompt
    assert "的观点输出解读" in prompt
    assert "只能解释" in prompt and "在会议中明确表达的观点" in prompt
    assert "不能用历史信息发明或替换" in prompt and "的立场" in prompt
    assert "能只靠会议证据解释时，historical_sources 必须为空数组" in prompt
    assert "必须逐字填写 `/configured/work_profile.md`" in prompt
    assert "不得改写、加标题或写成说明性文字" in prompt
    assert "业务群消息与敏感私聊消息" in prompt
    assert "人员评价、绩效、薪酬、晋升、去留、候选人结论" in prompt
    assert "不得出现在 final_message" in prompt
    assert "sensitive_private_message" in prompt
    assert "每场会议最多生成一条合并消息" not in prompt
    assert "群内所有人员都必须属于本次会议参会人" not in prompt
    assert "业务承接证据" in prompt
    assert "内容优先于参会人数" in prompt
    assert "audience_scope=business" in prompt
    assert "target.kind=group" in prompt
    assert "audience_scope=personal" in prompt
    assert "完整日历 1:1" in prompt
    assert "业务群发现失败时，使用日历中已确认的会议组织者" in prompt
    assert "不得按姓名模糊搜索目标" in prompt
    assert "只保留 audit_summary 与 confidence" not in prompt
    assert "真实 @" in prompt
    assert "放在对应的任务、问题或信息所在句子中" in prompt
    assert "禁止在消息开头集中列一排 @ 人员" in prompt


def test_prompt_keeps_each_participant_mention_adjacent_to_concrete_content():
    prompt = build_meeting_alignment_prompt(
        source(), work_profile="", work_profile_source="profile"
    )

    assert "每个真实 @ 都必须放在对应的任务、问题或信息所在句子中" in prompt
    assert "禁止在消息开头集中列一排 @ 人员" in prompt


def test_prompt_makes_business_content_group_first_even_for_one_to_one():
    prompt = build_meeting_alignment_prompt(
        source(participant_count=2), work_profile="", work_profile_source="profile"
    )
    assert "内容优先于参会人数" in prompt
    assert "客户、项目、产品、需求、交付、排期、测试、部署、客户沟通或跨团队行动" in prompt
    assert "audience_scope=business" in prompt
    assert "DWS 做群发现" in prompt
    assert "target.kind=group" in prompt
    assert "业务群发现失败时，使用日历中已确认的会议组织者" in prompt


def test_prompt_requires_a_summary_even_for_candidate_interviews():
    prompt = build_meeting_alignment_prompt(
        source(participant_count=2), work_profile="", work_profile_source="profile"
    )

    assert "每场会议都必须生成并发送一条会议总结" in prompt
    assert "action 只能是 send" in prompt
    assert "候选人结论" in prompt
    assert "敏感私聊" in prompt


def test_prompt_allows_personal_direct_only_for_complete_calendar_one_to_one():
    prompt = build_meeting_alignment_prompt(
        source(participant_count=2),
        work_profile="",
        work_profile_source="profile",
    )
    assert "audience_scope=personal" in prompt
    assert "attendee_evidence=calendar" in prompt
    assert "attendee_roster_complete=true" in prompt
    assert "恰好两名参会人" in prompt
    assert "target.kind=direct" in prompt


def test_agent_accepts_business_direct_fallback_to_calendar_organizer():
    target = {
        "kind": "direct",
        "conversation_id": "",
        "direct_user_id": "alex",
        "title": "Alex",
        "candidates": [],
    }
    decision = MeetingAlignmentAgent(
        FakeMeetingCodex(send_payload_with_target(target))
    ).decide(source())
    assert decision.target is not None
    assert decision.target.kind == "direct"
    assert decision.target.direct_user_id == "alex"


def test_target_error_preserves_the_generated_decision():
    target = {
        "kind": "direct",
        "conversation_id": "",
        "direct_user_id": "alex",
        "title": "Alex",
        "candidates": [],
    }
    agent = MeetingAlignmentAgent(
        FakeMeetingCodex(send_payload_with_target(target))
    )

    with pytest.raises(MeetingAlignmentTargetError) as raised:
        agent.decide(source(participant_count=2))

    assert raised.value.decision is not None
    assert raised.value.decision.action == "send"


def test_agent_rejects_business_direct_fallback_without_calendar_organizer():
    target = {
        "kind": "direct",
        "conversation_id": "",
        "direct_user_id": "alex",
        "title": "Alex",
        "candidates": [],
    }
    agent = MeetingAlignmentAgent(FakeMeetingCodex(send_payload_with_target(target)))
    with pytest.raises(MeetingAlignmentTargetError, match="calendar organizer"):
        agent.decide(source(participant_count=2))


def test_agent_accepts_business_group_target_for_incomplete_transcript_roster():
    target = {
        "kind": "group",
        "conversation_id": "cid-1",
        "direct_user_id": "",
        "title": "上线项目群",
        "candidates": [
            {
                "conversation_id": "cid-1",
                "title": "上线项目群",
                "evidence": ["承接上线范围的项目群"],
            }
        ],
    }
    transcript_source = source(participant_count=2).model_copy(
        update={"attendee_evidence": "transcript", "attendee_roster_complete": False}
    )
    decision = MeetingAlignmentAgent(
        FakeMeetingCodex(send_payload_with_target(target))
    ).decide(transcript_source)
    assert decision.target is not None
    assert decision.target.kind == "group"


def test_agent_rejects_sensitive_private_target_outside_meeting_roster():
    target = {
        "kind": "group",
        "conversation_id": "cid-1",
        "direct_user_id": "",
        "title": "产品招聘群",
        "candidates": [
            {
                "conversation_id": "cid-1",
                "title": "产品招聘群",
                "evidence": ["承接招聘安排的业务群"],
            }
        ],
    }
    payload = send_payload_with_target(target)
    payload["sensitive_private_message"] = {
        "target": {
            "kind": "direct",
            "conversation_id": "",
            "direct_user_id": "not-a-participant",
            "title": "非参会人员",
            "candidates": [],
        },
        "message": "人员评价仅私下同步。",
        "reason": "不得发送到招聘业务群。",
        "recipient_evidence": ["未经会议参会证据确认"],
    }

    with pytest.raises(
        MeetingAlignmentTargetError, match="one meeting participant"
    ):
        MeetingAlignmentAgent(FakeMeetingCodex(payload)).decide(source())


@pytest.mark.parametrize(
    ("attendee_evidence", "attendee_roster_complete"),
    [("transcript", True), ("calendar", False)],
)
def test_agent_rejects_personal_direct_without_complete_calendar_roster(
    attendee_evidence, attendee_roster_complete
):
    target = {
        "kind": "direct",
        "conversation_id": "",
        "direct_user_id": "alex",
        "title": "Alex",
        "candidates": [],
    }
    payload = send_payload_with_target(target)
    payload["audience_scope"] = "personal"
    incomplete_source = source(participant_count=2).model_copy(
        update={
            "attendee_evidence": attendee_evidence,
            "attendee_roster_complete": attendee_roster_complete,
        }
    )
    agent = MeetingAlignmentAgent(FakeMeetingCodex(payload))
    with pytest.raises(MeetingAlignmentTargetError, match="complete calendar"):
        agent.decide(incomplete_source)


def test_agent_accepts_personal_direct_for_complete_calendar_one_to_one():
    target = {
        "kind": "direct",
        "conversation_id": "",
        "direct_user_id": "alex",
        "title": "Alex",
        "candidates": [],
    }
    payload = send_payload_with_target(target)
    payload["audience_scope"] = "personal"
    decision = MeetingAlignmentAgent(
        FakeMeetingCodex(payload)
    ).decide(source(participant_count=2))
    assert decision.target is not None
    assert decision.target.direct_user_id == "alex"


def test_personal_direct_guidance_matches_the_source_aware_target_rule():
    """Emptying direct_user_id on a resolvable 1:1 is terminal, not a repairable schema miss.

    Pydantic has no rule about the top-level direct_user_id, so the decision
    validates, no correction turn fires, and the roster check then fails the job
    with kind=meeting_target. Guidance that pointed the model at "" therefore had
    to be dropped from every description the prompts and the schema file carry.
    """
    from app.meeting_alignment_models import MeetingAlignmentDecision

    target = {
        "kind": "direct",
        "conversation_id": "",
        "direct_user_id": "",
        "title": "Alex",
        "candidates": [],
    }
    payload = send_payload_with_target(target)
    payload["audience_scope"] = "personal"
    MeetingAlignmentDecision.model_validate(payload)

    agent = MeetingAlignmentAgent(FakeMeetingCodex(payload))
    with pytest.raises(
        MeetingAlignmentTargetError, match="must target the other participant"
    ):
        agent.decide(source(participant_count=2))

    schema = MeetingAlignmentDecision.model_json_schema()
    direct_user_id_description = schema["$defs"]["DeliveryTarget"]["properties"][
        "direct_user_id"
    ]["description"]
    assert "真实 user_id" in direct_user_id_description


def test_agent_rejects_null_target_for_multi_party_meeting():
    with pytest.raises(ValidationError, match="explicit delivery target"):
        MeetingAlignmentAgent(
            FakeMeetingCodex(send_payload_with_target(None))
        ).decide(source())


def test_parser_rejects_extra_fields():
    payload = summary_payload()
    payload["unexpected"] = True
    with pytest.raises(
        ValueError, match="unexpected: Extra inputs are not permitted"
    ):
        parse_meeting_alignment_decision(json.dumps(payload))


def test_parser_rejects_no_action_without_required_audience_scope():
    payload = summary_payload()
    payload["action"] = "no_action"
    del payload["audience_scope"]

    with pytest.raises(ValueError, match="audience_scope: Field required"):
        parse_meeting_alignment_decision(json.dumps(payload))


def test_runner_always_starts_fresh_and_uses_schema(tmp_path: Path):
    routed = FakeRoutedMeetingExecution(
        json.dumps(summary_payload(), ensure_ascii=False)
    )
    runner = MeetingAlignmentCodexRunner(routed_execution=routed)
    decision = runner.decide(prompt="decide", run_id=8)

    assert decision.action == "send"
    assert routed.calls[0]["conversation_id"] is None
    assert routed.calls[0]["workload_key"] == "8"
    assert routed.calls[0]["command_factory"].output_schema_path.name == (
        "meeting_alignment_decision.schema.json"
    )
    assert runner.last_transcript_start_line == 0


def test_runner_normalizes_mechanical_trigger_mismatch(tmp_path: Path):
    payload = derek_view_payload(historical_sources=[])
    payload["topics"] = [
        {
            "title": "发布范围",
            "state": "aligned",
            "views": [
                {"speaker": "Alex", "view": "全量", "reason": "收入"},
                {"speaker": "Mina", "view": "灰度", "reason": "风险"},
            ],
            "conclusion": "先 10% 后扩量",
            "alignment_reason": "双方明确同意并承诺执行",
        }
    ]
    runner = MeetingAlignmentCodexRunner(
        routed_execution=FakeRoutedMeetingExecution(
            json.dumps(payload, ensure_ascii=False)
        ),
    )

    decision = runner.decide(prompt="decide", run_id=1)

    assert "aligned_disagreement" in decision.trigger_reasons
    assert decision.topics[0].state == "aligned"


def test_runner_clears_prior_audit_metadata_before_executor_failure(tmp_path: Path):
    calls = 0

    def executor():
        nonlocal calls
        calls += 1
        if calls == 1:
            return "\n".join(
                [
                    json.dumps(
                        {"type": "thread.started", "thread_id": "session-old"}
                    ),
                    json.dumps(
                        {
                            "type": "item.completed",
                            "item": {
                                "type": "tool_call",
                                "tool_name": "dws",
                                "arguments": {"cmd": "dws chat search"},
                            },
                        }
                    ),
                    json.dumps(summary_payload(), ensure_ascii=False),
                ]
            )
        raise RuntimeError("executor failed")

    runner = MeetingAlignmentCodexRunner(
        routed_execution=FakeRoutedMeetingExecution(
            executor, session_id="session-old", transcript_end=17
        )
    )
    runner.decide(prompt="first", run_id=1)
    assert runner.last_session_id == "session-old"
    assert runner.last_transcript_end_line == 17
    assert runner.last_audit_tool_events

    with pytest.raises(RuntimeError, match="executor failed"):
        runner.decide(prompt="second", run_id=2)

    assert runner.last_session_id is None
    assert runner.last_transcript_start_line == 0
    assert runner.last_transcript_end_line == 0
    assert runner.last_audit_tool_events == []


def test_runner_accepts_historical_sources_from_typed_result(tmp_path: Path):
    payload = derek_view_payload(historical_sources=["历史上线案例"])

    def executor(command, prompt):
        return "\n".join(
            [
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {
                            "type": "tool_call",
                            "tool_name": "memory_recall",
                            "arguments": {"query": "历史上线案例"},
                        },
                    },
                    ensure_ascii=False,
                ),
                json.dumps(payload, ensure_ascii=False),
            ]
        )

    runner = MeetingAlignmentCodexRunner(
        routed_execution=FakeRoutedMeetingExecution(executor([], ""))
    )
    assert runner.decide(prompt="decide", run_id=1).action == "send"
    assert any(
        "memory_recall" in event.get("tool", "")
        for event in runner.last_audit_tool_events
    )


def test_runner_accepts_configured_profile_as_unqueried_history(tmp_path: Path):
    configured = "/configured/work_profile.md"
    runner = MeetingAlignmentCodexRunner(
        routed_execution=FakeRoutedMeetingExecution(
            json.dumps(
                derek_view_payload(historical_sources=[configured]),
                ensure_ascii=False,
            )
        ),
    )
    assert runner.decide(prompt="decide", run_id=1).action == "send"


def test_runner_does_not_require_tool_receipt_for_historical_sources(tmp_path: Path):
    runner = MeetingAlignmentCodexRunner(
        routed_execution=FakeRoutedMeetingExecution(
            json.dumps(
                derek_view_payload(historical_sources=["某个未核验案例"]),
                ensure_ascii=False,
            )
        ),
    )
    assert runner.decide(prompt="decide", run_id=1).action == "send"


def _fixture_cases() -> list[dict]:
    path = Path(__file__).parent / "fixtures" / "meeting_alignment_cases.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _source_for_case(case: dict) -> MeetingSource:
    return MeetingSource.model_validate(
        {
            **source().model_dump(mode="json"),
            "meeting_id": case["id"],
            "summary": case["summary"],
            "transcript": [
                {"speaker_name": speaker, "text": text}
                for speaker, text in case["transcript"]
            ],
        }
    )


def _deterministic_payload(case: dict) -> dict:
    if case["expected_action"] == "send_summary":
        return summary_payload()
    state = case.get("expected_state", "unresolved")
    triggers = [
        "aligned_disagreement" if state == "aligned" else "unresolved_disagreement"
    ]
    viewpoint = None
    if case.get("expected_trigger") == "derek_viewpoint":
        triggers.append("derek_viewpoint")
        viewpoint = derek_view_payload(historical_sources=[])["derek_viewpoint"]
    topic = {
        "title": "上线范围",
        "state": state,
        "views": [
            {"speaker": "Alex", "view": "全量", "reason": "验证收入"},
            {"speaker": "Mina", "view": "灰度", "reason": "控制风险"},
        ],
        "conclusion": "先 10% 后扩量" if state == "aligned" else "",
        "alignment_reason": (
            "双方明确同意并复述执行方案" if state == "aligned" else ""
        ),
    }
    questions = []
    if state == "unresolved":
        count = case.get("expected_question_count", 1)
        questions = [
            {
                "question": (
                    f"取舍问题 {index + 1}："
                    "选择收益时最多接受什么代价？"
                ),
                "answer_owner_names": ["Alex", "Mina"],
            }
            for index in range(count)
        ]
    return {
        "action": "send",
        "audience_scope": "business",
        "trigger_reasons": triggers,
        "topics": [topic],
        "derek_viewpoint": viewpoint,
        "key_questions": questions,
        "mention_names": ["Alex", "Mina"],
        "target": (
            None
            if case.get("expected_target") is None
            and "expected_target" in case
            else {
                "kind": "group",
                "conversation_id": "cid-best",
                "direct_user_id": "",
                "title": "上线项目群",
                "candidates": [
                    {
                        "conversation_id": "cid-best",
                        "title": "上线项目群",
                        "evidence": ["会议标题和近期讨论匹配"],
                    }
                ],
            }
        ),
        "final_message": (
            "Derek 的观点输出解读\n\n合并后的单条消息。"
            if viewpoint is not None
            else "会后对齐\n\n合并后的单条消息。"
        ),
        "audit_summary": f"语义夹具 {case['id']} 的确定性结果。",
        "confidence": 0.9,
    }


@pytest.mark.parametrize("case", _fixture_cases(), ids=lambda case: case["id"])
def test_semantic_fixtures_with_deterministic_executor(tmp_path: Path, case: dict):
    payload = _deterministic_payload(case)
    runner = MeetingAlignmentCodexRunner(
        routed_execution=FakeRoutedMeetingExecution(
            json.dumps(payload, ensure_ascii=False)
        ),
    )
    decision = runner.decide(
        prompt=build_meeting_alignment_prompt(
            _source_for_case(case), work_profile="", work_profile_source="profile"
        ),
        run_id=1,
    )

    fixture_id = case["id"]
    expected_action = (
        "send" if case["expected_action"] == "send_summary" else case["expected_action"]
    )
    assert decision.action == expected_action, fixture_id
    if expected_state := case.get("expected_state"):
        assert any(
            topic.state == expected_state for topic in decision.topics
        ), fixture_id
    if expected_count := case.get("expected_question_count"):
        assert len(decision.key_questions) == expected_count, fixture_id
    if expected_trigger := case.get("expected_trigger"):
        assert expected_trigger in decision.trigger_reasons, fixture_id
    if forbidden_trigger := case.get("forbidden_trigger"):
        assert forbidden_trigger not in decision.trigger_reasons, fixture_id
    if "expected_target" in case:
        assert decision.target == case["expected_target"], fixture_id


def test_parser_finds_decision_embedded_in_prose_and_fences():
    payload = summary_payload()
    text = (
        "I reviewed the transcript. {not json}\n\n```json\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
        + "\n```\nDone."
    )
    raw = json.dumps(
        {"type": "item.completed", "item": {"type": "agent_message", "text": text}}
    )

    assert parse_meeting_alignment_decision(raw) == parse_meeting_alignment_decision(
        json.dumps(payload)
    )
    assert parse_meeting_alignment_decision(text) == parse_meeting_alignment_decision(
        json.dumps(payload)
    )


def test_meeting_repair_prompt_names_schema_errors_of_last_candidate():
    from app.meeting_alignment_agent import _meeting_alignment_repair_prompt

    bad = {"action": "send", "trigger_reasons": ["aligned_disagreement"], "topics": [{"type": "aligned"}]}
    raw = json.dumps(
        {"type": "item.completed", "item": {"type": "agent_message", "text": "```json\n" + json.dumps(bad) + "\n```"}}
    )

    prompt = _meeting_alignment_repair_prompt(raw)

    assert "MeetingAlignmentDecision" in prompt
    assert "topics[].title: Field required" in prompt
    assert "上一次输出的问题" in prompt


def test_meeting_repair_prompt_reports_missing_decision():
    from app.meeting_alignment_agent import _meeting_alignment_repair_prompt

    prompt = _meeting_alignment_repair_prompt("I could not decide.")

    assert "did not contain a MeetingAlignmentDecision" in prompt


def invalid_shape_payload() -> dict:
    # The shape MiniMax produced on the failing runs: topic and target
    # objects invented by the model, top-level fields missing.
    return {
        "action": "send",
        "audience_scope": "business",
        "trigger_reasons": ["aligned_disagreement"],
        "topics": [{"topic": "发布范围"}, {"topic": "排期"}],
        "key_questions": [],
        "mention_names": [],
        "target": {
            "kind": "group",
            "conversation_id": "cid-1",
            "direct_user_id": "",
            "title": "上线项目群",
        },
        "final_message": "会议总结",
    }


def agent_message_jsonl(payload: dict) -> str:
    return json.dumps(
        {
            "type": "item.completed",
            "item": {
                "type": "agent_message",
                "text": json.dumps(payload, ensure_ascii=False),
            },
        },
        ensure_ascii=False,
    )


def test_parser_marks_missing_decision_as_validation_failure():
    from app.agent_runtime_router import RoutedResultValidationError

    raw = agent_message_jsonl(invalid_shape_payload())

    with pytest.raises(RoutedResultValidationError) as raised:
        parse_meeting_alignment_decision(raw)

    assert raised.value.raw_output == raw
    assert "topics[].title: Field required" in str(raised.value)
    assert "上线项目群" not in str(raised.value)

    with pytest.raises(RoutedResultValidationError) as raised:
        parse_meeting_alignment_decision("I could not decide.")

    assert raised.value.raw_output == "I could not decide."
    assert str(raised.value) == "No MeetingAlignmentDecision JSON found"


def test_meeting_repair_prompt_collapses_topic_indices_and_keeps_top_level_errors():
    from app.meeting_alignment_agent import _meeting_alignment_repair_prompt

    prompt = _meeting_alignment_repair_prompt(
        agent_message_jsonl(invalid_shape_payload())
    )
    problems = prompt.split("上一次输出的问题：\n", 1)[1].split("\n\n", 1)[0]

    assert "- topics[].title: Field required" in problems
    assert "topics.0" not in problems and "topics.1" not in problems
    assert "- derek_viewpoint: Field required" in problems
    assert "- target.candidates: Field required" in problems
    assert "- audit_summary: Field required" in problems
    assert "- confidence: Field required" in problems


def embedded_prompt_schema():
    """The derived schema as the prompts embed it: everything but the root rules.

    Both prompts print the rendered rule block just above the schema, so the copy
    inside the schema's root description would be the same instruction twice.
    """
    from app.meeting_alignment_models import MeetingAlignmentDecision

    schema = MeetingAlignmentDecision.model_json_schema()
    schema.pop("description")
    return schema


def test_meeting_repair_prompt_includes_derived_schema():
    from app.meeting_alignment_agent import _meeting_alignment_repair_prompt

    prompt = _meeting_alignment_repair_prompt("I could not decide.")
    prompt_schema = json.loads(
        prompt.split("MeetingAlignmentDecision Pydantic JSON schema:\n", 1)[1]
    )

    assert prompt_schema == embedded_prompt_schema()


def test_prompt_embeds_decision_schema():
    prompt = build_meeting_alignment_prompt(
        source(),
        work_profile="重视端到端结果",
        work_profile_source="/configured/work_profile.md",
    )
    prompt_schema, _ = json.JSONDecoder().raw_decode(
        prompt.split("MeetingAlignmentDecision Pydantic JSON schema:\n", 1)[1]
    )

    assert prompt_schema == embedded_prompt_schema()
    assert "严格遵守下方 schema" in prompt


def test_prompts_state_the_cross_field_rules_exactly_once():
    """Two copies of one instruction are their own drift risk, and cost ~2.2KB."""
    from app.meeting_alignment_agent import _meeting_alignment_repair_prompt
    from app.meeting_alignment_models import (
        MeetingAlignmentDecision,
        render_meeting_alignment_cross_field_rules,
    )

    block = render_meeting_alignment_cross_field_rules()
    prompts = (
        build_meeting_alignment_prompt(
            source(),
            work_profile="重视端到端结果",
            work_profile_source="/configured/work_profile.md",
        ),
        _meeting_alignment_repair_prompt("I could not decide."),
    )

    for prompt in prompts:
        assert prompt.count(block) == 1
    # The routes that honor --output-schema read the block from the schema file,
    # so it has to stay on the model even though the prompts drop it.
    assert MeetingAlignmentDecision.model_json_schema()["description"] == block


def test_runner_correction_turn_uses_repair_prompt_with_field_errors(tmp_path):
    from app.agent_runtime_config import load_runtime_config
    from app.agent_runtime_contracts import RuntimeCapabilitySnapshot
    from app.agent_runtime_router import RoutedCodexExecution
    from app.meeting_alignment_agent import MEETING_RUNTIME_CAPABILITIES
    from app.process_runner import ProcessRunResult
    from app.store import AutoReplyStore
    from tests.test_routed_codex_execution import NOW, FakeAdapter, make_router

    store = AutoReplyStore(tmp_path / "meeting-correction.sqlite3")
    config = load_runtime_config(
        {
            "CEO_AGENT_RUNTIME_ROUTES": "codex_oauth,codex_api",
            "CEO_CODEX_API_KEY": "configured-secret",
        }
    )
    store.upsert_meeting_alignment_job(
        meeting_id="minutes-1",
        title="上线范围评审",
        source_json="{}",
        participants_json="[]",
        ended_at=NOW.isoformat(),
        eligible_at=NOW.isoformat(),
        status="pending",
    )
    [job] = store.claim_meeting_alignment_jobs(limit=1, now=NOW.isoformat())
    run_id = store.begin_meeting_alignment_run(job.id)
    snapshots = {
        route.name: RuntimeCapabilitySnapshot(
            route_name=route.name,
            capabilities=MEETING_RUNTIME_CAPABILITIES,
            healthy=True,
            checked_at="2026-08-20T09:59:00+00:00",
            expires_at="2026-08-20T10:05:00+00:00",
        )
        for route in config.routes
    }
    prompts = []

    def executor(command, **kwargs):
        prompts.append(kwargs["prompt"])
        payload = invalid_shape_payload() if len(prompts) == 1 else summary_payload()
        return ProcessRunResult(
            0,
            "\n".join(
                [
                    json.dumps(
                        {"type": "thread.started", "thread_id": "meeting-session-1"}
                    ),
                    agent_message_jsonl(payload),
                ]
            ),
            "",
        )

    adapter = FakeAdapter()
    runner = MeetingAlignmentCodexRunner(
        routed_execution=RoutedCodexExecution(
            store=store,
            config=config,
            router=make_router(store, config, snapshots=snapshots),
            adapter=adapter,
            executor=executor,
            session_line_counter=lambda _session_id: 2,
        )
    )

    decision = runner.decide(prompt="decide", run_id=run_id)

    assert decision.action == "send"
    assert runner.last_session_id == "meeting-session-1"
    assert prompts[0] == "decide"
    assert "topics[].title: Field required" in prompts[1]
    assert "derek_viewpoint: Field required" in prompts[1]
    assert "MeetingAlignmentDecision Pydantic JSON schema:" in prompts[1]
    assert adapter.commands == [
        ("codex_oauth", None, "on-failure", False),
        ("codex_oauth", "meeting-session-1", "on-failure", False),
    ]
    attempts = store.list_runtime_operation_attempts("meeting", str(run_id))
    assert [attempt.attempt_purpose for attempt in attempts] == [
        "normal",
        "result_validation_correction",
    ]
    assert [attempt.status for attempt in attempts] == ["superseded", "completed"]
    assert attempts[0].failure_code == "runtime_result_validation_failed"
    assert [attempt.session_mode for attempt in attempts] == ["fresh", "resume"]


@pytest.mark.parametrize(
    "rule",
    MEETING_ALIGNMENT_CROSS_FIELD_RULES,
    ids=[rule.message for rule in MEETING_ALIGNMENT_CROSS_FIELD_RULES],
)
def test_repair_prompt_names_the_required_combination_for_each_validator_message(rule):
    from app.meeting_alignment_agent import _meeting_alignment_repair_prompt

    payload = cross_field_rule_payloads()[rule.message]

    prompt = _meeting_alignment_repair_prompt(agent_message_jsonl(payload))
    problems = prompt.split("上一次输出的问题：\n", 1)[1].split("\n\n", 1)[0]

    assert rule.message in problems
    assert f"（需要：{rule.requirement}）" in problems


def test_repair_prompt_lists_every_cross_field_rule_even_when_one_fired():
    """Live job 2770: one rule is reported, the next two are the ones it then broke."""
    from app.meeting_alignment_agent import _meeting_alignment_repair_prompt
    from app.meeting_alignment_models import (
        ALIGNED_TOPIC_NEEDS_TRIGGER_RULE,
        DIRECT_TARGET_NO_GROUP_FIELDS_RULE,
    )

    payload = valid_send_decision()
    payload["audience_scope"] = "personal"
    payload["target"] = {"kind": "direct", "title": "张三"}

    prompt = _meeting_alignment_repair_prompt(agent_message_jsonl(payload))
    problems = prompt.split("上一次输出的问题：\n", 1)[1].split("\n\n", 1)[0]

    assert "- target.direct_user_id: Field required" in problems
    for unreported in (
        DIRECT_TARGET_NO_GROUP_FIELDS_RULE,
        ALIGNED_TOPIC_NEEDS_TRIGGER_RULE,
    ):
        assert unreported.requirement not in problems
        assert unreported.message in prompt
        assert unreported.requirement in prompt


def test_main_prompt_states_every_cross_field_rule():
    prompt = build_meeting_alignment_prompt(
        source(),
        work_profile="重视端到端结果",
        work_profile_source="/configured/work_profile.md",
    )

    for rule in MEETING_ALIGNMENT_CROSS_FIELD_RULES:
        assert rule.message in prompt, rule.message
        assert rule.requirement in prompt, rule.message
