import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.meeting_alignment_models import (
    MeetingAlignmentDecision,
    MeetingAlignmentJob,
    MeetingAlignmentRun,
    MeetingSource,
)


def valid_send_decision():
    return {
        "action": "send",
        "trigger_reasons": ["unresolved_disagreement"],
        "topics": [
            {
                "title": "上线范围",
                "state": "unresolved",
                "views": [
                    {"speaker": "A", "view": "全量上线", "reason": "验证收入"},
                    {"speaker": "B", "view": "小流量", "reason": "控制风险"},
                ],
                "conclusion": "",
                "alignment_reason": "",
            }
        ],
        "derek_viewpoint": None,
        "key_questions": [
            {
                "question": "如果本周必须验证收入，最多接受多大故障面？",
                "answer_owner_names": ["A", "B"],
            }
        ],
        "mention_names": ["A", "B"],
        "target": {
            "kind": "group",
            "conversation_id": "cid-1",
            "direct_user_id": "",
            "title": "项目群",
            "candidates": [
                {
                    "conversation_id": "cid-1",
                    "title": "项目群",
                    "evidence": ["会前后讨论同一上线范围"],
                }
            ],
        },
        "final_message": "会后对齐｜上线评审\n\n目前尚未对齐…",
        "audit_summary": "发现一个未对齐的上线范围取舍。",
        "confidence": 0.86,
    }


def valid_job():
    return {
        "id": 1,
        "meeting_id": "minutes-1",
        "title": "上线评审",
        "source_json": "{}",
        "participants_json": "[]",
        "ended_at": "2026-07-14 02:00:00",
        "eligible_at": "2026-07-14 02:10:00",
        "status": "waiting",
        "attempts": 0,
        "locked_at": None,
        "available_at": "2026-07-14 02:10:00",
        "error": "",
        "decision_json": "{}",
        "target_kind": "",
        "target_id": "",
        "target_title": "",
        "mentions_json": "[]",
        "final_message": "",
        "send_result_json": "{}",
        "created_at": "2026-07-14 02:00:00",
        "updated_at": "2026-07-14 02:00:00",
    }


def valid_derek_viewpoint():
    return {
        "expressed_view": "先控制风险，再逐步放量。",
        "meeting_evidence": ["Derek 提出先验证故障恢复能力。"],
        "omitted_layer": "故障面与恢复能力的约束",
        "plain_explanation": "先确认出问题时能收回来，再扩大范围。",
        "analogy": "先试刹车，再上高速。",
        "example": "先开放 5% 流量并验证回滚。",
        "historical_sources": ["历史项目复盘"],
    }


def test_send_decision_requires_message_and_target():
    payload = valid_send_decision()
    payload["final_message"] = ""
    with pytest.raises(ValidationError):
        MeetingAlignmentDecision.model_validate(payload)

    payload = valid_send_decision()
    payload["target"] = None
    with pytest.raises(ValidationError):
        MeetingAlignmentDecision.model_validate(payload)


def test_no_action_rejects_delivery_payload():
    payload = valid_send_decision()
    payload.update(action="no_action", final_message="")
    with pytest.raises(ValidationError):
        MeetingAlignmentDecision.model_validate(payload)

    payload = valid_send_decision()
    payload.update(
        action="no_action",
        trigger_reasons=[],
        target=None,
        final_message="仍然发送一条消息",
    )
    with pytest.raises(ValidationError):
        MeetingAlignmentDecision.model_validate(payload)


def test_send_requires_trigger_reasons_and_first_ranked_group_target():
    payload = valid_send_decision()
    payload["trigger_reasons"] = []
    with pytest.raises(ValidationError):
        MeetingAlignmentDecision.model_validate(payload)

    payload = valid_send_decision()
    payload["target"]["conversation_id"] = "cid-2"
    with pytest.raises(ValidationError):
        MeetingAlignmentDecision.model_validate(payload)

    payload = valid_send_decision()
    payload["target"]["candidates"] = []
    with pytest.raises(ValidationError):
        MeetingAlignmentDecision.model_validate(payload)


def test_no_action_without_delivery_output_is_valid():
    payload = valid_send_decision()
    payload.update(
        action="no_action",
        trigger_reasons=[],
        target=None,
        final_message="  ",
    )
    decision = MeetingAlignmentDecision.model_validate(payload)
    assert decision.action == "no_action"


def test_no_action_requires_empty_trigger_reasons():
    payload = valid_send_decision()
    payload.update(action="no_action", target=None, final_message="")
    with pytest.raises(ValidationError):
        MeetingAlignmentDecision.model_validate(payload)


def test_disagreement_triggers_require_matching_topics_and_questions():
    payload = valid_send_decision()
    payload["trigger_reasons"] = ["aligned_disagreement"]
    with pytest.raises(ValidationError):
        MeetingAlignmentDecision.model_validate(payload)

    payload = valid_send_decision()
    payload["topics"][0]["state"] = "aligned"
    with pytest.raises(ValidationError):
        MeetingAlignmentDecision.model_validate(payload)

    payload = valid_send_decision()
    payload["key_questions"] = []
    with pytest.raises(ValidationError):
        MeetingAlignmentDecision.model_validate(payload)


def test_derek_viewpoint_trigger_and_payload_require_each_other():
    payload = valid_send_decision()
    payload["trigger_reasons"] = ["derek_viewpoint"]
    with pytest.raises(ValidationError):
        MeetingAlignmentDecision.model_validate(payload)

    payload = valid_send_decision()
    payload["derek_viewpoint"] = valid_derek_viewpoint()
    with pytest.raises(ValidationError):
        MeetingAlignmentDecision.model_validate(payload)


def test_combined_triggers_accept_all_required_evidence():
    payload = valid_send_decision()
    payload["trigger_reasons"] = [
        "aligned_disagreement",
        "unresolved_disagreement",
        "derek_viewpoint",
    ]
    payload["topics"].append(
        {
            "title": "回滚门槛",
            "state": "aligned",
            "views": [],
            "conclusion": "错误率超过 1% 时回滚。",
            "alignment_reason": "参会者已明确确认。",
        }
    )
    payload["derek_viewpoint"] = valid_derek_viewpoint()
    assert MeetingAlignmentDecision.model_validate(payload).action == "send"


def test_direct_target_requires_only_a_direct_user_id():
    payload = valid_send_decision()
    payload["target"] = {
        "kind": "direct",
        "conversation_id": "",
        "direct_user_id": "u-other",
        "title": "一对一会话",
        "candidates": [],
    }
    assert MeetingAlignmentDecision.model_validate(payload).target.direct_user_id == (
        "u-other"
    )

    payload["target"]["direct_user_id"] = ""
    with pytest.raises(ValidationError):
        MeetingAlignmentDecision.model_validate(payload)


def test_direct_target_rejects_group_fields():
    payload = valid_send_decision()
    payload["target"] = {
        "kind": "direct",
        "conversation_id": "cid-1",
        "direct_user_id": "u-other",
        "title": "一对一会话",
        "candidates": [
            {
                "conversation_id": "cid-1",
                "title": "项目群",
                "evidence": ["同一议题"],
            }
        ],
    }
    with pytest.raises(ValidationError):
        MeetingAlignmentDecision.model_validate(payload)


def test_group_target_rejects_direct_user_id():
    payload = valid_send_decision()
    payload["target"]["direct_user_id"] = "u-other"
    with pytest.raises(ValidationError):
        MeetingAlignmentDecision.model_validate(payload)


def test_contracts_forbid_unknown_fields_at_every_level():
    payload = valid_send_decision()
    payload["target"]["candidates"][0]["rank"] = 1
    with pytest.raises(ValidationError):
        MeetingAlignmentDecision.model_validate(payload)


def test_meeting_source_uses_the_fixed_source_shape():
    source = MeetingSource.model_validate(
        {
            "meeting_id": "minutes-1",
            "title": "上线评审",
            "status": "ended",
            "started_at": "2026-07-14 01:00:00",
            "ended_at": "2026-07-14 02:00:00",
            "participants": [
                {
                    "name": "Derek",
                    "user_id": "u-derek",
                    "open_dingtalk_id": "open-derek",
                }
            ],
            "current_user_id": "u-derek",
            "summary": "讨论上线范围。",
            "transcript": [
                {
                    "speaker_name": "Derek",
                    "speaker_user_id": "u-derek",
                    "timestamp": "00:10:00",
                    "text": "先控制风险。",
                }
            ],
            "source_url": "https://alidocs.dingtalk.com/minutes/minutes-1",
        }
    )
    assert source.participants[0].open_dingtalk_id == "open-derek"
    assert source.transcript[0].text == "先控制风险。"

    invalid = source.model_dump()
    invalid["status"] = "running"
    with pytest.raises(ValidationError):
        MeetingSource.model_validate(invalid)


@pytest.mark.parametrize(
    "status",
    [
        "waiting",
        "pending",
        "processing",
        "no_action",
        "ready_to_send",
        "sent",
        "retry",
        "failed",
    ],
)
def test_meeting_job_accepts_each_exact_queue_status(status):
    payload = valid_job()
    payload["status"] = status
    assert MeetingAlignmentJob.model_validate(payload).status == status


def test_meeting_job_rejects_status_outside_queue_contract():
    payload = valid_job()
    payload["status"] = "done"
    with pytest.raises(ValidationError):
        MeetingAlignmentJob.model_validate(payload)


def test_meeting_run_uses_the_fixed_persistence_shape():
    run = MeetingAlignmentRun.model_validate(
        {
            "id": 1,
            "job_id": 2,
            "codex_session_id": "session-1",
            "codex_transcript_start_line": 10,
            "codex_transcript_end_line": 20,
            "decision_json": "{}",
            "audit_tool_events_json": "[]",
            "audit_summary": "发现未解决分歧。",
            "status": "sent",
            "error": "",
            "created_at": "2026-07-14 02:20:00",
        }
    )
    assert run.codex_transcript_end_line == 20


def test_committed_schema_matches_the_decision_model():
    schema_path = (
        Path(__file__).parents[1]
        / "app"
        / "schemas"
        / "meeting_alignment_decision.schema.json"
    )
    committed_schema = json.loads(schema_path.read_text())
    assert committed_schema.pop("$schema") == (
        "https://json-schema.org/draft/2020-12/schema"
    )
    assert committed_schema == MeetingAlignmentDecision.model_json_schema()
