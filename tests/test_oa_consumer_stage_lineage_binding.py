import json

from app.agent_wire_contracts import consumer_wire_model
from app.consumer_agent import _parse_consumer_result


def _wire_result(*, stage_index, predecessor_review_id):
    return consumer_wire_model().model_validate({
        "action": "no_action",
        "reason": "Nothing needs doing in this review stage.",
        "target": {},
        "payload": {},
        "evidence_refs": [],
        "summary": "The current stage needs no external action.",
        "error_code": None,
        "error_retryable": False,
        "error_authorization_required": False,
        "risk": "low",
        "confidence": 1.0,
        "rule_coverage": 1.0,
        "information_completeness": 1.0,
        "decision_options": [],
        "requested_input": None,
        "needs_human_reason": None,
        "decision_basis": None,
        "stage_index": stage_index,
        "predecessor_review_id": predecessor_review_id,
        "continue_after_execution": False,
        "durable_memories": [],
    }).model_dump(mode="json")


def _codex_output(result):
    return "\n".join((
        json.dumps({"type": "thread.started", "thread_id": "lineage-test"}),
        json.dumps({"type": "item.completed", "item": {
            "type": "agent_message", "text": json.dumps(result),
        }}),
    ))


def test_consumer_parser_binds_same_stage_after_audit_return():
    # The model accidentally echoes the current return review as the predecessor
    # and increments the stage. The service context says this is still stage 1,
    # whose only predecessor is the already-verified stage-0 review.
    raw = _codex_output(_wire_result(stage_index=2, predecessor_review_id=108))

    result = _parse_consumer_result(
        raw,
        stage_index=1,
        predecessor_review_id=73,
    )

    assert result.stage_index == 1
    assert result.predecessor_review_id == 73
    assert result.outcome == "no_action"


def test_consumer_parser_binds_first_stage_without_predecessor():
    raw = _codex_output(_wire_result(stage_index=8, predecessor_review_id=900))

    result = _parse_consumer_result(
        raw,
        stage_index=0,
        predecessor_review_id=None,
    )

    assert result.stage_index == 0
    assert result.predecessor_review_id is None
