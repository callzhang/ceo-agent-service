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
from app.meeting_alignment_models import MeetingSource


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


def test_agent_rejects_business_direct_target_for_calendar_one_to_one():
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
    with pytest.raises(MeetingAlignmentTargetError, match="business.*group"):
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


def test_agent_rejects_null_target_for_multi_party_meeting():
    with pytest.raises(ValidationError, match="explicit delivery target"):
        MeetingAlignmentAgent(
            FakeMeetingCodex(send_payload_with_target(None))
        ).decide(source())


def test_parser_rejects_extra_fields():
    payload = summary_payload()
    payload["unexpected"] = True
    with pytest.raises(ValueError, match="No MeetingAlignmentDecision"):
        parse_meeting_alignment_decision(json.dumps(payload))


def test_parser_rejects_no_action_without_required_audience_scope():
    payload = summary_payload()
    payload["action"] = "no_action"
    del payload["audience_scope"]

    with pytest.raises(ValueError, match="No MeetingAlignmentDecision"):
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


def test_runner_treats_invalid_model_decision_as_retryable(tmp_path: Path):
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

    with pytest.raises(ValueError, match="MeetingAlignmentDecision"):
        runner.decide(prompt="decide", run_id=1)


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
