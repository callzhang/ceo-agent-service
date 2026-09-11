import ast
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

import app.meeting_alignment_models as models
from app.meeting_alignment_agent import parse_meeting_alignment_decision
from app.meeting_alignment_models import (
    MeetingAlignmentDecision,
    MeetingAlignmentJob,
    MeetingAlignmentRun,
    MeetingSource,
    load_persisted_meeting_alignment_decision,
    render_meeting_alignment_cross_field_rules,
)


def valid_send_decision():
    return {
        "action": "send",
        "audience_scope": "business",
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


@pytest.mark.parametrize(
    ("target_kind", "expected_scope"),
    [("group", "business"), ("direct", "personal")],
)
def test_load_persisted_decision_backfills_scope_from_existing_target(
    target_kind, expected_scope
):
    payload = valid_send_decision()
    if target_kind == "direct":
        payload["target"] = {
            "kind": "direct",
            "conversation_id": "",
            "direct_user_id": "u-a",
            "title": "A",
            "candidates": [],
        }
    del payload["audience_scope"]

    decision, canonical_json = load_persisted_meeting_alignment_decision(
        json.dumps(payload)
    )

    assert decision.audience_scope == expected_scope
    assert json.loads(canonical_json)["audience_scope"] == expected_scope


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


def test_decision_normalizes_topic_trigger_pairing():
    payload = valid_send_decision()
    payload["topics"][0].update(
        state="aligned",
        conclusion="先小流量验证，再扩大范围。",
        alignment_reason="双方明确同意这个推进方式。",
    )
    payload["trigger_reasons"] = ["meeting_summary"]
    payload["key_questions"] = []

    decision = parse_meeting_alignment_decision(json.dumps(payload))

    assert "aligned_disagreement" in decision.trigger_reasons
    assert "unresolved_disagreement" not in decision.trigger_reasons


def test_decision_normalizes_derek_viewpoint_trigger_pairing():
    payload = valid_send_decision()
    payload["derek_viewpoint"] = valid_derek_viewpoint()
    payload["trigger_reasons"] = ["meeting_summary"]

    decision = parse_meeting_alignment_decision(json.dumps(payload))

    assert "derek_viewpoint" in decision.trigger_reasons

    payload = valid_send_decision()
    payload["trigger_reasons"] = ["meeting_summary", "derek_viewpoint"]
    payload["derek_viewpoint"] = None

    decision = parse_meeting_alignment_decision(json.dumps(payload))

    assert "derek_viewpoint" not in decision.trigger_reasons


def test_decision_normalizes_target_shape_from_kind():
    group_payload = valid_send_decision()
    group_payload["target"]["conversation_id"] = ""
    group_payload["target"]["direct_user_id"] = "stale-direct-user"

    group_decision = parse_meeting_alignment_decision(json.dumps(group_payload))

    assert group_decision.target is not None
    assert group_decision.target.conversation_id == "cid-1"
    assert group_decision.target.direct_user_id == ""

    direct_payload = valid_send_decision()
    direct_payload["audience_scope"] = "personal"
    direct_payload["trigger_reasons"] = ["meeting_summary"]
    direct_payload["topics"] = []
    direct_payload["key_questions"] = []
    direct_payload["target"] = {
        "kind": "direct",
        "conversation_id": "stale-group",
        "direct_user_id": "alex",
        "title": "Alex",
        "candidates": [
            {
                "conversation_id": "stale-group",
                "title": "项目群",
                "evidence": ["stale group target from a previous candidate"],
            }
        ],
    }

    direct_decision = parse_meeting_alignment_decision(json.dumps(direct_payload))

    assert direct_decision.target is not None
    assert direct_decision.target.conversation_id == ""
    assert direct_decision.target.candidates == []


def test_send_decision_requires_message_and_explicit_target():
    payload = valid_send_decision()
    payload["final_message"] = ""
    with pytest.raises(ValidationError):
        MeetingAlignmentDecision.model_validate(payload)


def test_business_summary_can_split_personnel_evaluation_into_private_message():
    payload = valid_send_decision()
    payload["sensitive_private_message"] = {
        "target": {
            "kind": "direct",
            "conversation_id": "",
            "direct_user_id": "u-mina",
            "title": "Mina",
            "candidates": [],
        },
        "message": "人员评价仅私下同步给本次招聘事项的 HR 负责人。",
        "reason": "包含对候选人与人事工作的评价，不应进入招聘业务群。",
        "recipient_evidence": ["Mina 是本次招聘事项的 HR 负责人"],
    }

    decision = MeetingAlignmentDecision.model_validate(payload)

    assert decision.target.kind == "group"
    assert decision.sensitive_private_message is not None
    assert decision.sensitive_private_message.target.direct_user_id == "u-mina"


def test_sensitive_private_message_requires_direct_stable_recipient_and_evidence():
    payload = valid_send_decision()
    payload["sensitive_private_message"] = {
        "target": payload["target"],
        "message": "敏感人员评价。",
        "reason": "不能群发。",
        "recipient_evidence": [],
    }

    with pytest.raises(ValidationError, match="sensitive private message"):
        MeetingAlignmentDecision.model_validate(payload)

    payload = valid_send_decision()
    payload["target"] = None
    with pytest.raises(ValidationError, match="explicit delivery target"):
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


def test_meeting_summary_rejects_no_action():
    payload = valid_send_decision()
    payload.update(
        action="no_action",
        trigger_reasons=[],
        topics=[],
        derek_viewpoint=None,
        key_questions=[],
        mention_names=[],
        target=None,
        final_message="",
    )
    with pytest.raises(ValidationError):
        MeetingAlignmentDecision.model_validate(payload)


def test_no_action_requires_empty_trigger_reasons():
    payload = valid_send_decision()
    payload.update(action="no_action", target=None, final_message="")
    with pytest.raises(ValidationError):
        MeetingAlignmentDecision.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        (
            "topics",
            [
                {
                    "title": "上线范围",
                    "state": "unresolved",
                    "views": [],
                    "conclusion": "",
                    "alignment_reason": "",
                }
            ],
        ),
        ("derek_viewpoint", valid_derek_viewpoint()),
        (
            "key_questions",
            [{"question": "是否上线？", "answer_owner_names": ["A"]}],
        ),
        ("mention_names", ["A"]),
    ],
)
def test_no_action_rejects_analysis_payload(field, value):
    payload = valid_send_decision()
    payload.update(
        action="no_action",
        trigger_reasons=[],
        topics=[],
        derek_viewpoint=None,
        key_questions=[],
        mention_names=[],
        target=None,
        final_message="",
    )
    payload[field] = value
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


def test_topic_states_require_matching_disagreement_triggers():
    payload = valid_send_decision()
    payload["topics"].append(
        {
            "title": "回滚门槛",
            "state": "aligned",
            "views": [],
            "conclusion": "错误率超过 1% 时回滚。",
            "alignment_reason": "参会者已明确确认。",
        }
    )
    with pytest.raises(ValidationError):
        MeetingAlignmentDecision.model_validate(payload)

    payload = valid_send_decision()
    payload["trigger_reasons"] = ["aligned_disagreement"]
    payload["topics"][0].update(
        state="aligned",
        conclusion="错误率超过 1% 时回滚。",
        alignment_reason="参会者已明确确认。",
    )
    payload["topics"].append(
        {
            "title": "上线范围",
            "state": "unresolved",
            "views": [],
            "conclusion": "",
            "alignment_reason": "",
        }
    )
    with pytest.raises(ValidationError):
        MeetingAlignmentDecision.model_validate(payload)


@pytest.mark.parametrize("field", ["conclusion", "alignment_reason"])
def test_aligned_topic_requires_conclusion_and_alignment_reason(field):
    payload = valid_send_decision()
    payload["trigger_reasons"] = ["aligned_disagreement"]
    payload["topics"][0].update(
        state="aligned",
        conclusion="错误率超过 1% 时回滚。",
        alignment_reason="参会者已明确确认。",
    )
    payload["topics"][0][field] = "  "
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


def test_direct_target_accepts_resolved_user_id_and_requires_no_group_fields():
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
    assert MeetingAlignmentDecision.model_validate(payload).target.title == (
        "一对一会话"
    )


def test_unresolved_direct_target_requires_nonempty_counterpart_title():
    payload = valid_send_decision()
    payload["target"] = {
        "kind": "direct",
        "conversation_id": "",
        "direct_user_id": "",
        "title": "Alex",
        "candidates": [],
    }
    decision = MeetingAlignmentDecision.model_validate(payload)
    assert decision.target.direct_user_id == ""
    assert decision.target.title == "Alex"

    payload["target"]["title"] = "  "
    with pytest.raises(ValidationError, match="direct target requires title"):
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
            "attendee_evidence": "calendar",
            "attendee_roster_complete": True,
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


def test_committed_schema_allows_null_target_for_delivery_retry():
    schema_path = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "schemas"
        / "meeting_alignment_decision.schema.json"
    )
    target_schema = json.loads(schema_path.read_text())["properties"]["target"]
    assert {entry.get("type") for entry in target_schema["anyOf"]} >= {"null"}


def test_committed_schema_requires_explicit_sensitive_private_message():
    schema_path = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "schemas"
        / "meeting_alignment_decision.schema.json"
    )
    schema = json.loads(schema_path.read_text())

    assert "sensitive_private_message" in schema["required"]
    assert {entry.get("type") for entry in schema["properties"][
        "sensitive_private_message"
    ]["anyOf"]} >= {"null"}


def direct_target():
    return {
        "kind": "direct",
        "conversation_id": "",
        "direct_user_id": "u-a",
        "title": "A",
        "candidates": [],
    }


def aligned_topic():
    return {
        "title": "上线范围",
        "state": "aligned",
        "views": [{"speaker": "A", "view": "小流量", "reason": "控制风险"}],
        "conclusion": "先小流量再逐步放量。",
        "alignment_reason": "双方复述一致并承诺执行。",
    }


def sensitive_private_payload():
    return {
        "target": direct_target(),
        "message": "候选人结论仅同步给人员负责人。",
        "reason": "涉及候选人评价。",
        "recipient_evidence": ["DWS 实时身份：A 为人员负责人"],
    }


def cross_field_rule_payloads() -> dict[str, dict]:
    """One decision payload per cross-field rule, each breaking exactly that rule."""
    payloads: dict[str, dict] = {}

    topic_without_conclusion = valid_send_decision()
    topic_without_conclusion["topics"][0]["state"] = "aligned"
    payloads[models.ALIGNED_TOPIC_RESULT_RULE.message] = topic_without_conclusion

    aligned_trigger_only = valid_send_decision()
    aligned_trigger_only["trigger_reasons"] = ["aligned_disagreement"]
    payloads[models.ALIGNED_TRIGGER_NEEDS_TOPIC_RULE.message] = aligned_trigger_only

    aligned_topic_without_trigger = valid_send_decision()
    aligned_topic_without_trigger["topics"] = [aligned_topic()]
    payloads[models.ALIGNED_TOPIC_NEEDS_TRIGGER_RULE.message] = (
        aligned_topic_without_trigger
    )

    unresolved_trigger_without_topic = valid_send_decision()
    unresolved_trigger_without_topic["topics"] = [aligned_topic()]
    unresolved_trigger_without_topic["trigger_reasons"] = [
        "aligned_disagreement",
        "unresolved_disagreement",
    ]
    payloads[models.UNRESOLVED_TRIGGER_NEEDS_TOPIC_RULE.message] = (
        unresolved_trigger_without_topic
    )

    unresolved_topic_without_trigger = valid_send_decision()
    unresolved_topic_without_trigger["trigger_reasons"] = ["meeting_summary"]
    payloads[models.UNRESOLVED_TOPIC_NEEDS_TRIGGER_RULE.message] = (
        unresolved_topic_without_trigger
    )

    unresolved_without_questions = valid_send_decision()
    unresolved_without_questions["key_questions"] = []
    payloads[models.UNRESOLVED_TRIGGER_NEEDS_QUESTIONS_RULE.message] = (
        unresolved_without_questions
    )

    lonely_derek_trigger = valid_send_decision()
    lonely_derek_trigger["trigger_reasons"] = [
        "unresolved_disagreement",
        "derek_viewpoint",
    ]
    payloads[models.DEREK_VIEWPOINT_PAIRING_RULE.message] = lonely_derek_trigger

    without_triggers = valid_send_decision()
    without_triggers["trigger_reasons"] = []
    without_triggers["topics"] = []
    without_triggers["key_questions"] = []
    payloads[models.SEND_TRIGGER_REASONS_RULE.message] = without_triggers

    without_final_message = valid_send_decision()
    without_final_message["final_message"] = "  "
    payloads[models.SEND_FINAL_MESSAGE_RULE.message] = without_final_message

    without_target = valid_send_decision()
    without_target["target"] = None
    payloads[models.SEND_TARGET_RULE.message] = without_target

    group_without_candidates = valid_send_decision()
    group_without_candidates["target"]["candidates"] = []
    payloads[models.GROUP_TARGET_FIELDS_RULE.message] = group_without_candidates

    group_off_first_candidate = valid_send_decision()
    group_off_first_candidate["target"]["candidates"][0]["conversation_id"] = "cid-2"
    payloads[models.GROUP_TARGET_FIRST_CANDIDATE_RULE.message] = (
        group_off_first_candidate
    )

    group_with_direct_user = valid_send_decision()
    group_with_direct_user["target"]["direct_user_id"] = "u-a"
    payloads[models.GROUP_TARGET_NO_DIRECT_USER_RULE.message] = group_with_direct_user

    direct_with_group_fields = valid_send_decision()
    direct_with_group_fields["audience_scope"] = "personal"
    direct_with_group_fields["target"] = direct_target() | {"conversation_id": "cid-1"}
    payloads[models.DIRECT_TARGET_NO_GROUP_FIELDS_RULE.message] = (
        direct_with_group_fields
    )

    direct_without_title = valid_send_decision()
    direct_without_title["audience_scope"] = "personal"
    direct_without_title["target"] = direct_target() | {"title": " "}
    payloads[models.DIRECT_TARGET_TITLE_RULE.message] = direct_without_title

    private_outside_business = valid_send_decision()
    private_outside_business["audience_scope"] = "personal"
    private_outside_business["target"] = direct_target()
    private_outside_business["sensitive_private_message"] = sensitive_private_payload()
    payloads[models.PRIVATE_MESSAGE_BUSINESS_SCOPE_RULE.message] = (
        private_outside_business
    )

    private_to_group = valid_send_decision()
    private_to_group["sensitive_private_message"] = sensitive_private_payload() | {
        "target": valid_send_decision()["target"]
    }
    payloads[models.PRIVATE_MESSAGE_DIRECT_TARGET_RULE.message] = private_to_group

    private_without_user_id = valid_send_decision()
    private_without_user_id["sensitive_private_message"] = (
        sensitive_private_payload()
        | {"target": direct_target() | {"direct_user_id": " "}}
    )
    payloads[models.PRIVATE_MESSAGE_USER_ID_RULE.message] = private_without_user_id

    private_without_evidence = valid_send_decision()
    private_without_evidence["sensitive_private_message"] = (
        sensitive_private_payload() | {"recipient_evidence": []}
    )
    payloads[models.PRIVATE_MESSAGE_EVIDENCE_RULE.message] = private_without_evidence

    return payloads


def aligned_send_decision():
    payload = valid_send_decision()
    payload["trigger_reasons"] = ["aligned_disagreement"]
    payload["topics"] = [aligned_topic()]
    payload["key_questions"] = []
    return payload


def direct_send_decision():
    payload = valid_send_decision()
    payload["audience_scope"] = "personal"
    payload["target"] = direct_target()
    return payload


def private_send_decision():
    payload = valid_send_decision()
    payload["sensitive_private_message"] = sensitive_private_payload()
    return payload


def cross_field_rule_satisfying_payloads() -> dict[str, dict]:
    """One decision payload per cross-field rule, each built as its prose describes.

    Written from the `requirement` text alone, so a requirement that describes a
    shape the validators reject fails here instead of only misleading the model.
    """
    payloads: dict[str, dict] = {}

    # topics[].state=aligned needs both results; state=unresolved writes them empty.
    payloads[models.ALIGNED_TOPIC_RESULT_RULE.message] = aligned_send_decision()
    payloads[models.ALIGNED_TRIGGER_NEEDS_TOPIC_RULE.message] = aligned_send_decision()
    payloads[models.ALIGNED_TOPIC_NEEDS_TRIGGER_RULE.message] = aligned_send_decision()

    # The unresolved half of both biconditionals, plus its key_questions duty.
    payloads[models.UNRESOLVED_TRIGGER_NEEDS_TOPIC_RULE.message] = valid_send_decision()
    payloads[models.UNRESOLVED_TOPIC_NEEDS_TRIGGER_RULE.message] = valid_send_decision()
    payloads[models.UNRESOLVED_TRIGGER_NEEDS_QUESTIONS_RULE.message] = (
        valid_send_decision()
    )

    paired_derek_viewpoint = valid_send_decision()
    paired_derek_viewpoint["trigger_reasons"] = [
        "unresolved_disagreement",
        "derek_viewpoint",
    ]
    paired_derek_viewpoint["derek_viewpoint"] = valid_derek_viewpoint()
    payloads[models.DEREK_VIEWPOINT_PAIRING_RULE.message] = paired_derek_viewpoint

    payloads[models.SEND_TRIGGER_REASONS_RULE.message] = valid_send_decision()
    payloads[models.SEND_FINAL_MESSAGE_RULE.message] = valid_send_decision()
    payloads[models.SEND_TARGET_RULE.message] = valid_send_decision()

    # kind=group: conversation_id non-empty, candidates non-empty and led by it,
    # direct_user_id present as "".
    payloads[models.GROUP_TARGET_FIELDS_RULE.message] = valid_send_decision()
    payloads[models.GROUP_TARGET_FIRST_CANDIDATE_RULE.message] = valid_send_decision()
    payloads[models.GROUP_TARGET_NO_DIRECT_USER_RULE.message] = valid_send_decision()

    # kind=direct: the group keys stay present as ""/[] and title is non-empty,
    # while direct_user_id carries the counterpart's real id.
    payloads[models.DIRECT_TARGET_NO_GROUP_FIELDS_RULE.message] = (
        direct_send_decision()
    )
    payloads[models.DIRECT_TARGET_TITLE_RULE.message] = direct_send_decision()

    payloads[models.PRIVATE_MESSAGE_BUSINESS_SCOPE_RULE.message] = (
        private_send_decision()
    )
    payloads[models.PRIVATE_MESSAGE_DIRECT_TARGET_RULE.message] = (
        private_send_decision()
    )
    payloads[models.PRIVATE_MESSAGE_USER_ID_RULE.message] = private_send_decision()
    payloads[models.PRIVATE_MESSAGE_EVIDENCE_RULE.message] = private_send_decision()

    return payloads


def raised_validator_messages(payload: dict) -> set[str]:
    with pytest.raises(ValidationError) as raised:
        MeetingAlignmentDecision.model_validate(payload)
    return {
        error["msg"].removeprefix("Value error, ") for error in raised.value.errors()
    }


def test_every_cross_field_validator_has_a_declared_rule():
    raised: set[str] = set()
    for message, payload in cross_field_rule_payloads().items():
        messages = raised_validator_messages(payload)
        assert message in messages, message
        raised |= messages

    assert raised == {
        rule.message for rule in models.MEETING_ALIGNMENT_CROSS_FIELD_RULES
    }
    for rule in models.MEETING_ALIGNMENT_CROSS_FIELD_RULES:
        assert rule.message.strip()
        assert rule.requirement.strip()


@pytest.mark.parametrize(
    "rule",
    models.MEETING_ALIGNMENT_CROSS_FIELD_RULES,
    ids=[rule.message for rule in models.MEETING_ALIGNMENT_CROSS_FIELD_RULES],
)
def test_every_rule_requirement_describes_a_payload_that_validates(rule):
    """The requirement half is what the model acts on, so pin it to the validators.

    `test_cross_field_validators_raise_only_declared_rule_constants` ties the
    message half to the raise sites. Without this the requirement stays free
    prose and can quietly describe a shape the validators reject.
    """
    payload = cross_field_rule_satisfying_payloads()[rule.message]

    MeetingAlignmentDecision.model_validate(payload)


def test_every_rule_has_a_satisfying_payload():
    assert set(cross_field_rule_satisfying_payloads()) == {
        rule.message for rule in models.MEETING_ALIGNMENT_CROSS_FIELD_RULES
    }


def test_cross_field_validators_raise_only_declared_rule_constants():
    """A validator rule added without a CrossFieldRule entry never reaches the prompts."""
    module = ast.parse(
        (
            Path(__file__).parents[1] / "app" / "meeting_alignment_models.py"
        ).read_text()
    )
    declared = {rule.message for rule in models.MEETING_ALIGNMENT_CROSS_FIELD_RULES}
    raised_constants: list[str] = []
    for function in ast.walk(module):
        if not isinstance(function, ast.FunctionDef):
            continue
        if not any(
            "validator" in ast.unparse(decorator)
            for decorator in function.decorator_list
        ):
            continue
        for node in ast.walk(function):
            if not isinstance(node, ast.Raise):
                continue
            assert isinstance(node.exc, ast.Call) and node.exc.args, function.name
            argument = ast.unparse(node.exc.args[0])
            assert argument.endswith(".message"), argument
            raised_constants.append(argument.removesuffix(".message"))

    assert raised_constants
    for name in raised_constants:
        assert getattr(models, name).message in declared, name


def test_reported_problems_map_back_to_the_rule_that_fired():
    messages = [rule.message for rule in models.MEETING_ALIGNMENT_CROSS_FIELD_RULES]
    assert len(set(messages)) == len(messages)

    for rule in models.MEETING_ALIGNMENT_CROSS_FIELD_RULES:
        # No message may be a suffix of another, or the lookup would mismatch.
        assert not any(
            other != rule.message and other.endswith(rule.message)
            for other in messages
        ), rule.message
        assert (
            models.meeting_alignment_rule_for_message(f"Value error, {rule.message}")
            == rule
        )
        assert (
            models.meeting_alignment_rule_for_message(
                f"sensitive_private_message: Value error, {rule.message}"
            )
            == rule
        )

    assert models.meeting_alignment_rule_for_message("topics[].title: Field required") is None


def test_derived_schema_states_every_cross_field_rule():
    description = MeetingAlignmentDecision.model_json_schema()["description"]

    for rule in models.MEETING_ALIGNMENT_CROSS_FIELD_RULES:
        assert rule.message in description, rule.message
        assert rule.requirement in description, rule.message


def test_target_field_description_states_group_and_direct_combinations():
    description = MeetingAlignmentDecision.model_json_schema()["properties"]["target"][
        "description"
    ]

    for key in ("kind", "conversation_id", "direct_user_id", "title", "candidates"):
        assert key in description, key
    # The trap behind the live `target.direct_user_id: Field required` loop: the
    # unused side stays a required key and has to be emitted empty, not dropped.
    assert "省略" in description
    assert '""' in description
    assert "[]" in description


def test_sensitive_private_target_carries_no_schema_description():
    """A description here is rejected by the provider, so it must not come back.

    The field's type is a single model, so pydantic renders any description
    beside the `$ref`, and OpenAI's structured-output validator refuses the
    whole request: "$ref cannot have keywords {'description'}". Every meeting
    alignment turn then fails before the model reads a word of the prompt,
    which is what produced nine failed jobs on 2026-09-11.
    """
    target = MeetingAlignmentDecision.model_json_schema()["$defs"][
        "SensitivePrivateMessage"
    ]["properties"]["target"]

    assert "$ref" in target
    assert set(target) == {"$ref"}, target


def test_the_rules_still_teach_the_sensitive_private_target_shape():
    """What the removed description said has to survive somewhere the model reads.

    The trap it guarded is real: the unused side of the target stays a required
    key and has to be emitted empty rather than dropped, which is what the live
    `target.direct_user_id: Field required` loop came from. Both prompts print
    this block in full.
    """
    rules = render_meeting_alignment_cross_field_rules()

    assert "sensitive_private_message.target.kind 必须是 direct" in rules
    assert "direct_user_id 必须是非空的稳定 user_id" in rules
    direct_shape = next(
        line
        for line in rules.splitlines()
        if "direct target cannot contain group delivery fields" in line
    )
    for fragment in ("conversation_id", "candidates", "必填", '""', "[]"):
        assert fragment in direct_shape, fragment


def test_no_description_claims_the_two_direct_targets_contradict_each_other():
    """The two slots differ in what they allow, not in what direct_user_id means.

    `_validate_source_aware_target` requires the top-level direct target to carry
    the counterpart's user_id whenever the roster resolves one, so guidance that
    told the model to empty it there produced a terminal meeting_target failure
    with no correction turn.
    """
    schema = MeetingAlignmentDecision.model_json_schema()
    descriptions = [schema["description"]]
    descriptions.extend(
        prop["description"]
        for prop in schema["properties"].values()
        if "description" in prop
    )
    descriptions.extend(
        prop["description"]
        for definition in schema["$defs"].values()
        for prop in definition.get("properties", {}).values()
        if "description" in prop
    )

    for description in descriptions:
        assert "相反" not in description, description
