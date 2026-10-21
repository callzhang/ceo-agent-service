"""Current Consumer purpose and Audit contracts for native Codex output."""

import copy
import json

import pytest
from jsonschema import Draft202012Validator

from app.agent_result import ResultParseError
from app.agent_wire_contracts import AuditAgentWireResult, consumer_wire_model
from app.native_agent_output import (
    native_output_schema,
    native_output_schema_path,
    parse_native_output,
)


def _event(value):
    return json.dumps({"type": "item.completed", "item": {
        "type": "agent_message", "text": json.dumps(value, ensure_ascii=False),
    }}, ensure_ascii=False)


def _consumer(action="no_action", *, purpose="general", target=None, payload=None):
    wire = consumer_wire_model(purpose).model_validate({
        "action": action, "reason": "Current source checked", "target": target or {},
        "payload": payload or {}, "evidence_refs": ["source:1"],
        "summary": "Current source checked", "risk": "low", "confidence": 1.0,
        "rule_coverage": 1.0, "information_completeness": 1.0,
        "durable_memories": [],
    }).model_dump(mode="json")
    for name in ("target", "payload"):
        wire[name] = {"entries": [
            {"key": key, "value": value} for key, value in wire[name].items()
        ]}
    return wire


def test_native_consumer_schema_tracks_both_current_purposes():
    general = native_output_schema("consumer", purpose="general")
    oa = native_output_schema("consumer", purpose="oa")
    Draft202012Validator.check_schema(general)
    Draft202012Validator.check_schema(oa)
    assert set(oa["properties"]["action"]["enum"]) - set(
        general["properties"]["action"]["enum"]
    ) == {"approve", "reject", "return", "comment"}


@pytest.mark.parametrize("role,purpose", [
    ("consumer", "general"), ("consumer", "oa"), ("audit", "general"),
])
def test_committed_native_schema_matches_current_wire_model(role, purpose):
    assert json.loads(native_output_schema_path(role, purpose=purpose).read_text()) == (
        native_output_schema(role, purpose=purpose)
    )


def test_native_oa_return_restores_target_and_payload_with_original_values():
    target = {"process_instance_id": "p", "task_id": "001", "target_activity_id": "a"}
    payload = {"remark": "请补充材料", "revert_action": "REVERT_FOR_RESUBMIT"}
    native = _consumer("return", purpose="oa", target=target, payload=payload)
    assert Draft202012Validator(native_output_schema("consumer", purpose="oa")).is_valid(native)
    restored = json.loads(parse_native_output(_event(native), "consumer", purpose="oa"))
    result = consumer_wire_model("oa").model_validate(restored).to_result()
    assert result.proposal.actions[0].target == target
    assert result.proposal.actions[0].payload == payload
    with pytest.raises(ResultParseError):
        parse_native_output(_event(native), "consumer", purpose="general")


@pytest.mark.parametrize("action,target,payload", [
    ("approve", {"process_instance_id": "p", "task_id": "001"}, {"remark": "同意"}),
    ("reject", {"process_instance_id": "p", "task_id": "001"}, {"remark": "不同意"}),
    ("comment", {"process_instance_id": "p"}, {"content": "请核对"}),
])
def test_native_other_oa_actions_follow_current_business_contract(action, target, payload):
    native = _consumer(action, purpose="oa", target=target, payload=payload)
    assert Draft202012Validator(native_output_schema("consumer", purpose="oa")).is_valid(native)
    result = consumer_wire_model("oa").model_validate_json(
        parse_native_output(_event(native), "consumer", purpose="oa")
    ).to_result()
    assert result.purpose_output["action"] == action
    assert result.proposal.actions[0].target == target


def test_native_general_no_action_preserves_empty_business_objects():
    native = _consumer()
    assert Draft202012Validator(native_output_schema("consumer")).is_valid(native)
    result = consumer_wire_model("general").model_validate_json(
        parse_native_output(_event(native), "consumer")
    ).to_result()
    assert result.outcome.value == "no_action"
    assert result.proposal is None


@pytest.mark.parametrize("suffix", ["}", "\u200b"])
def test_native_output_rejects_trailing_non_json_bytes(suffix):
    native = _consumer()
    broken = json.dumps(native, ensure_ascii=False) + suffix
    event = json.dumps({"type": "item.completed", "item": {
        "type": "agent_message", "text": broken,
    }}, ensure_ascii=False)
    with pytest.raises(ResultParseError):
        parse_native_output(event, "consumer")


def test_native_dynamic_json_preserves_special_keys_nested_types_and_null():
    audit = AuditAgentWireResult.model_validate({
        "outcome": "return", "summary": "Check exact candidate field", "risk": "low",
        "error_code": None, "error_retryable": False,
        "error_authorization_required": False,
        "confidence": 1.0, "rule_coverage": 1.0,
        "information_completeness": 1.0, "evidence_refs": ["source:1"],
        "feedback": {"rule": "Candidate field", "observation": "Wrong value",
                     "requested_revision": "Correct value", "observation_basis": "candidate_fields",
                     "candidate_field_assertions": [{
                         "path": ["proposal", "actions", 0, "payload", "value"],
                         "value": {"entries": {"a/~b": [None, True, 1.5, 2]}},
                         "json_type": "object",
                     }]},
    }).model_dump(mode="json")
    value = audit["feedback"]["candidate_field_assertions"][0]["value"]
    audit["feedback"]["candidate_field_assertions"][0]["value"] = {
        "entries": [{"key": "entries", "value": {
            "entries": [{"key": "a/~b", "value": [None, True, 1.5, 2]}],
        }}],
    }
    assert Draft202012Validator(native_output_schema("audit")).is_valid(audit)
    restored = json.loads(parse_native_output(_event(audit), "audit"))
    assertion = restored["feedback"]["candidate_field_assertions"][0]
    assert assertion["value"] == value
    duplicate = copy.deepcopy(audit)
    duplicate_value = duplicate["feedback"]["candidate_field_assertions"][0]["value"]
    duplicate_value["entries"].append({"key": "entries", "value": False})
    with pytest.raises(ResultParseError, match="native result failed current wire contract"):
        parse_native_output(_event(duplicate), "audit")


def test_native_audit_keeps_required_feedback_validation():
    audit = AuditAgentWireResult.model_validate({
        "outcome": "return", "summary": "Correct destination", "risk": "low",
        "error_code": None, "error_retryable": False,
        "error_authorization_required": False,
        "confidence": 1.0, "rule_coverage": 1.0,
        "information_completeness": 1.0, "evidence_refs": ["source:1"],
        "feedback": {"rule": "Destination", "observation": "Wrong node",
                     "requested_revision": "Use the source node",
                     "observation_basis": "business", "candidate_field_assertions": []},
    }).model_dump(mode="json")
    assert Draft202012Validator(native_output_schema("audit")).is_valid(audit)
    assert AuditAgentWireResult.model_validate_json(
        parse_native_output(_event(audit), "audit")
    ).root.feedback.rule == "Destination"
    invalid = copy.deepcopy(audit)
    invalid["feedback"] = None
    with pytest.raises(ResultParseError):
        parse_native_output(_event(invalid), "audit")
    approved = copy.deepcopy(audit)
    approved["outcome"] = "approve"
    approved["feedback"] = None
    assert AuditAgentWireResult.model_validate_json(
        parse_native_output(_event(approved), "audit")
    ).root.outcome == "approve"
