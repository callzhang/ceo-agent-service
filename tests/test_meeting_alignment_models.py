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
