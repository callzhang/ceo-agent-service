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
