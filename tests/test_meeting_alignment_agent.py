import json
from pathlib import Path

import pytest

from app.meeting_alignment_agent import (
    MeetingAlignmentCodexRunner,
    build_meeting_alignment_prompt,
    parse_meeting_alignment_decision,
)
from app.meeting_alignment_models import MeetingSource


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


def no_action_payload() -> dict:
    return {
        "action": "no_action",
        "trigger_reasons": [],
        "topics": [],
        "derek_viewpoint": None,
        "key_questions": [],
        "mention_names": [],
        "target": None,
        "final_message": "",
        "audit_summary": (
            "没有实质观点分歧或 Derek 观点输出解读需求。"
        ),
        "confidence": 0.9,
    }


def derek_view_payload(*, historical_sources: list[str]) -> dict:
    return {
        "action": "send",
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


def test_prompt_contains_full_transcript_and_behavioral_contracts():
    prompt = build_meeting_alignment_prompt(
        source(),
        work_profile="重视端到端结果",
        work_profile_source="/configured/work_profile.md",
    )

    assert "我建议全量上线以验证收入" in prompt
    assert "沉默不算对齐" in prompt
    assert "明确同意、承诺或复述一致" in prompt
    assert "可以提出多个问题" in prompt
    assert "完成对齐所需的最小集合" in prompt
    assert "Derek 的观点输出解读" in prompt
    assert "只能解释 Derek 在会议中明确表达的观点" in prompt
    assert "不能用历史信息发明或替换 Derek 的立场" in prompt
    assert "每场会议最多生成一条合并消息" in prompt
    assert "候选列表第 1 个" in prompt
    assert "关联较弱也不能降级为私聊" in prompt
    assert "真实 @" in prompt


def test_one_to_one_prompt_requires_direct_other_participant():
    prompt = build_meeting_alignment_prompt(
        source(participant_count=2), work_profile="", work_profile_source="profile"
    )
    assert "这是 1:1 会议" in prompt
    assert "direct_user_id=alex" in prompt
    assert "禁止搜索或选择群" in prompt


def test_parser_rejects_extra_fields():
    payload = no_action_payload()
    payload["unexpected"] = True
    with pytest.raises(ValueError, match="No MeetingAlignmentDecision"):
        parse_meeting_alignment_decision(json.dumps(payload))


def test_runner_always_starts_fresh_and_uses_schema(tmp_path: Path):
    captured = {}

    def executor(command, prompt):
        captured["command"] = command
        captured["prompt"] = prompt
        return json.dumps(no_action_payload(), ensure_ascii=False)

    runner = MeetingAlignmentCodexRunner(workspace=tmp_path, executor=executor)
    decision = runner.decide(prompt="decide")

    assert decision.action == "no_action"
    assert "resume" not in captured["command"]
    assert "meeting_alignment_decision.schema.json" in " ".join(captured["command"])
    assert runner.last_transcript_start_line == 0


def test_runner_accepts_historical_sources_with_memory_recall_audit(tmp_path: Path):
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

    runner = MeetingAlignmentCodexRunner(workspace=tmp_path, executor=executor)
    assert runner.decide(prompt="decide").action == "send"
    assert any(
        "memory_recall" in event.get("tool", "")
        for event in runner.last_audit_tool_events
    )


def test_runner_accepts_configured_profile_as_unqueried_history(tmp_path: Path):
    configured = "/configured/work_profile.md"
    runner = MeetingAlignmentCodexRunner(
        workspace=tmp_path,
        executor=lambda command, prompt: json.dumps(
            derek_view_payload(historical_sources=[configured]), ensure_ascii=False
        ),
        work_profile_source=configured,
    )
    assert runner.decide(prompt="decide").action == "send"


def test_runner_rejects_unaudited_historical_sources(tmp_path: Path):
    runner = MeetingAlignmentCodexRunner(
        workspace=tmp_path,
        executor=lambda command, prompt: json.dumps(
            derek_view_payload(historical_sources=["某个未核验案例"]),
            ensure_ascii=False,
        ),
        work_profile_source="/configured/work_profile.md",
    )
    with pytest.raises(ValueError, match="historical_sources require"):
        runner.decide(prompt="decide")


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
    if case["expected_action"] == "no_action":
        return no_action_payload()
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
        "trigger_reasons": triggers,
        "topics": [topic],
        "derek_viewpoint": viewpoint,
        "key_questions": questions,
        "mention_names": ["Alex", "Mina"],
        "target": {
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
        },
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
        workspace=tmp_path,
        executor=lambda command, prompt: json.dumps(payload, ensure_ascii=False),
    )
    decision = runner.decide(
        prompt=build_meeting_alignment_prompt(
            _source_for_case(case), work_profile="", work_profile_source="profile"
        )
    )

    fixture_id = case["id"]
    assert decision.action == case["expected_action"], fixture_id
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
