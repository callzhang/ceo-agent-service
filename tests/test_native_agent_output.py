import copy
import json

import pytest
from jsonschema import Draft202012Validator

from app.agent_result import ResultParseError
from app.native_agent_output import native_output_schema, parse_native_output
from app.agent_wire_contracts import ConsumerAgentWireResult, AuditAgentWireResult


def stream(payload):
    return json.dumps(
        {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": json.dumps(payload)},
        }
    )


def consumer():
    return dict(
        summary="fixture",
        error_code=None,
        error_retryable=False,
        error_authorization_required=False,
        risk="low",
        confidence=1.0,
        rule_coverage=1.0,
        information_completeness=1.0,
        durable_memories=[],
        outcome="proposal",
        proposal=dict(
            objective="fixture",
            actions=[
                dict(
                    description="fixture",
                    action_identity="fixture",
                    capability="fixture",
                    operation="fixture",
                    target={
                        "entries": [
                            {"key": "entries", "value": "literal"},
                            {
                                "key": "nested",
                                "value": {
                                    "entries": [
                                        {"key": "bool", "value": True},
                                        {
                                            "key": "values",
                                            "value": [1, 1.5, None, False],
                                        },
                                    ]
                                },
                            },
                        ]
                    },
                    payload={"entries": []},
                    effect="none",
                )
            ],
            sourced_facts=[],
            authored_judgment="fixture",
        ),
        decision_options=[],
        requested_input=None,
        needs_human_reason=None,
        decision_basis=None,
        stage_index=0,
        predecessor_review_id=None,
        continue_after_execution=False,
    )


@pytest.mark.parametrize("role", ["consumer", "audit"])
def test_native_schema_is_object_required_closed_and_has_no_unsupported_composition(
    role,
):
    schema = native_output_schema(role)
    assert schema["type"] == "object"

    def walk(node):
        if not isinstance(node, dict):
            return
        assert not (
            {"oneOf", "allOf", "if", "then", "else", "not", "default"} & node.keys()
        )
        if node.get("type") == "object":
            assert node["additionalProperties"] is False
            assert node["required"] == list(node["properties"])
        for key in ("properties", "$defs"):
            for child in node.get(key, {}).values():
                walk(child)
        for child in node.get("anyOf", []):
            walk(child)
        if "items" in node:
            walk(node["items"])

    walk(schema)
    Draft202012Validator.check_schema(schema)


def test_native_map_decode_preserves_literal_key_nested_values_and_business_payload():
    payload = consumer()
    assert Draft202012Validator(native_output_schema("consumer")).is_valid(payload)
    decoded = parse_native_output(stream(payload), "consumer")
    result = ConsumerAgentWireResult.model_validate_json(decoded)
    action = result.root.proposal.actions[0]
    assert action.target == {
        "entries": "literal",
        "nested": {"bool": True, "values": [1, 1.5, None, False]},
    }
    assert type(action.target["nested"]["values"][0]) is int
    assert type(action.target["nested"]["values"][1]) is float
    assert action.payload == {}


@pytest.mark.parametrize(
    "mutation", ["duplicates", "confidence", "outcome", "oa_target", "extra_map_field"]
)
def test_invalid_native_or_business_values_remain_rejected(mutation):
    payload = consumer()
    action = payload["proposal"]["actions"][0]
    if mutation == "duplicates":
        action["target"]["entries"].append({"key": "entries", "value": False})
    if mutation == "confidence":
        payload["confidence"] = 1.5
    if mutation == "outcome":
        payload["outcome"] = "no_action"
    if mutation == "oa_target":
        action.update(capability="dingtalk-oa", operation="approve")
    if mutation == "extra_map_field":
        action["target"]["other"] = 1
    with pytest.raises(ResultParseError):
        parse_native_output(stream(payload), "consumer")


def test_audit_native_results_keep_digest_and_feedback_contract():
    payload = dict(
        summary="fixture",
        error_code=None,
        error_retryable=False,
        error_authorization_required=False,
        risk="low",
        confidence=1.0,
        rule_coverage=1.0,
        information_completeness=1.0,
        outcome="approve",
        proposal_revision=1,
        candidate_digest="0" * 64,
        evidence_refs=[],
        feedback=None,
    )
    assert Draft202012Validator(native_output_schema("audit")).is_valid(payload)
    assert (
        AuditAgentWireResult.model_validate_json(
            parse_native_output(stream(payload), "audit")
        ).root.candidate_digest
        == "0" * 64
    )
    bad = copy.deepcopy(payload)
    bad["outcome"] = "return"
    with pytest.raises(ResultParseError):
        parse_native_output(stream(bad), "audit")


@pytest.mark.parametrize("value,json_type", [
    ({"entries": [{"key": "entries", "value": [None, True, 1.5]},
                   {"key": "a/~b", "value": {"entries": [{"key": "", "value": "原值"}]}}]}, "object"),
    (None, "null"),
    ("000123", "string"),
])
def test_native_audit_field_assertions_restore_values_and_keep_bound_validation(value, json_type):
    from app.audit_agent import validate_audit_candidate_fields

    payload = {
        "summary": "Verify exact candidate fields", "outcome": "return",
        "proposal_revision": 0, "candidate_digest": "0" * 64,
        "error_code": None, "error_retryable": False,
        "error_authorization_required": False,
        "risk": "low", "confidence": 1.0, "rule_coverage": 1.0,
        "information_completeness": 1.0, "evidence_refs": [],
        "feedback": {
            "rule": "Current field evidence", "observation": "Review exact value",
            "requested_revision": "Review current business facts",
            "observation_basis": "candidate_fields",
            "candidate_field_assertions": [{
                "path": ["proposal", "actions", 0, "payload", "value"],
                "value": value, "json_type": json_type,
            }],
        },
    }
    assert Draft202012Validator(native_output_schema("audit")).is_valid(payload)
    result = AuditAgentWireResult.model_validate_json(
        parse_native_output(stream(payload), "audit")
    ).to_result()
    assertion = result.feedback.candidate_field_assertions[0]
    expected = (
        {"entries": [None, True, 1.5], "a/~b": {"": "原值"}}
        if json_type == "object" else value
    )
    assert assertion.value == expected
    assert assertion.path == ("proposal", "actions", 0, "payload", "value")
    candidate_wire = json.loads(parse_native_output(stream(consumer()), "consumer"))
    candidate_wire["proposal"]["actions"][0]["payload"] = {"value": expected}
    candidate = ConsumerAgentWireResult.model_validate(candidate_wire).to_result()
    validate_audit_candidate_fields(result, candidate)
    candidate.proposal.actions[0].payload["value"] = "different"
    with pytest.raises(ResultParseError, match="contradicts the bound candidate"):
        validate_audit_candidate_fields(result, candidate)
    missing = copy.deepcopy(payload)
    missing["feedback"]["candidate_field_assertions"] = []
    assert Draft202012Validator(native_output_schema("audit")).is_valid(missing)
    with pytest.raises(ResultParseError):
        parse_native_output(stream(missing), "audit")


@pytest.mark.parametrize("role", ["consumer", "audit"])
def test_committed_native_schema_matches_current_projection(role):
    from app.native_agent_output import native_output_schema_path
    assert json.loads(native_output_schema_path(role).read_text()) == native_output_schema(role)


def test_later_invalid_native_candidate_does_not_hide_earlier_valid_result():
    valid = consumer()
    invalid = copy.deepcopy(valid)
    invalid["confidence"] = 2.0
    decoded = parse_native_output(stream(valid) + "\n" + stream(invalid), "consumer")
    assert json.loads(decoded)["confidence"] == 1.0


def test_native_generation_orders_existing_evidence_before_actions_and_summary():
    schema = native_output_schema("consumer")
    assert list(schema["properties"])[-1] == "summary"
    assert list(schema["$defs"]["ConsumerProposal"]["properties"]) == [
        "objective", "sourced_facts", "authored_judgment", "actions"
    ]


@pytest.mark.parametrize("identifier", [104265682397, True, {"entries": []}, [], "  "])
def test_native_decode_preserves_current_message_identifier_validation(identifier):
    payload = consumer()
    action = payload["proposal"]["actions"][0]
    action.update(capability="dingtalk-chat", operation="send_direct_message", effect="external")
    action["target"] = {"entries": [{"key": "user_id", "value": "verified-user"},
                                      {"key": "open_dingtalk_id", "value": identifier}]}
    action["payload"] = {"entries": [{"key": "content", "value": "Exact notice."}]}
    # Native structural grammar is a shape constraint. The full canonical
    # business validator rejects wrong field types after lossless restoration.
    assert Draft202012Validator(native_output_schema("consumer")).is_valid(payload)
    with pytest.raises(ResultParseError):
        parse_native_output(stream(payload), "consumer")


def test_native_decode_keeps_leading_zero_message_identifier():
    payload = consumer()
    action = payload["proposal"]["actions"][0]
    action.update(capability="dingtalk-chat", operation="send_direct_message", effect="external")
    action["target"] = {"entries": [{"key": "open_dingtalk_id", "value": "000104265682397"}]}
    action["payload"] = {"entries": [{"key": "content", "value": "Exact notice."}]}
    result = ConsumerAgentWireResult.model_validate_json(parse_native_output(stream(payload), "consumer"))
    assert result.root.proposal.actions[0].target["open_dingtalk_id"] == "000104265682397"
