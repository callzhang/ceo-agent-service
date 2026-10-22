import json

import pytest
from jsonschema import Draft202012Validator

from app.agent_result import ResultParseError
from app.agent_wire_contracts import parse_audit_agent_wire_result
from app.native_agent_output import (
    native_output_schema,
    native_output_schema_path,
    parse_native_output,
    parse_native_result,
)


def _event(value):
    return json.dumps({"type": "item.completed", "item": {
        "type": "agent_message", "text": json.dumps(value, ensure_ascii=False),
    }}, ensure_ascii=False)


def _audit(outcome="approve"):
    feedback = None if outcome in {"approve", "failed"} else {
        "rule": "Source evidence is insufficient",
        "observation": "The cited source does not support the claim",
        "requested_revision": "Remove the unsupported claim",
        "observation_basis": "candidate_fields",
        "candidate_field_assertions": [{
            "path": ["payload", "actions", 0, "payload", "content"],
            "value": {"unsafe": True},
            "json_type": "object",
        }],
    }
    return {
        "outcome": outcome,
        "evidence_refs": ["source:1"],
        "feedback": feedback,
        "summary": "Checked the candidate against current source evidence",
        "error_code": None,
        "error_retryable": False,
        "error_authorization_required": False,
        "risk": "low",
        "confidence": 0.95,
        "rule_coverage": 1.0,
        "information_completeness": 1.0,
    }


def _encode_dynamic(value):
    if isinstance(value, dict):
        return {"entries": [{"key": key, "value": _encode_dynamic(item)}
                            for key, item in value.items()]}
    if isinstance(value, list):
        return [_encode_dynamic(item) for item in value]
    return value


def _native_audit(outcome="approve"):
    result = _audit(outcome)
    result["decision"] = {
        "outcome": result.pop("outcome"),
        "feedback": result.pop("feedback"),
    }
    feedback = result["decision"]["feedback"]
    if feedback:
        feedback["candidate_field_assertions"][0]["value"] = _encode_dynamic(
            feedback["candidate_field_assertions"][0]["value"]
        )
    return result


def test_audit_native_schema_is_structurally_valid_and_matches_snapshot():
    schema = native_output_schema("audit")
    Draft202012Validator.check_schema(schema)
    assert json.loads(native_output_schema_path("audit").read_text()) == schema
    assert "oneOf" not in schema and "anyOf" not in schema
    assert set(schema["properties"]) == (set(_audit("approve")) - {"outcome", "feedback"}) | {"decision"}


@pytest.mark.parametrize("outcome", ["approve", "return", "reject", "failed"])
def test_audit_native_schema_restores_and_uses_candidate_bound_parser(outcome):
    native = _native_audit(outcome)
    assert Draft202012Validator(native_output_schema("audit")).is_valid(native)
    parsed = parse_native_result(
        _event(native), "audit",
        parser=lambda raw: parse_audit_agent_wire_result(
            raw, proposal_revision=7, candidate_digest="a" * 64,
        ),
    )
    assert parsed.proposal_revision == 7
    assert parsed.candidate_digest == "a" * 64
    assert parsed.outcome.value == outcome


def test_audit_native_schema_preserves_dynamic_candidate_field_values():
    native = _native_audit("return")
    assert Draft202012Validator(native_output_schema("audit")).is_valid(native)
    restored = json.loads(parse_native_output(_event(native), "audit"))
    assertion = restored["feedback"]["candidate_field_assertions"][0]
    assert assertion["value"] == {"unsafe": True}


def test_audit_native_result_still_rejects_missing_return_feedback():
    native = _native_audit("return")
    native["decision"]["feedback"] = None
    assert not Draft202012Validator(native_output_schema("audit")).is_valid(native)
    with pytest.raises(ResultParseError):
        parse_native_output(_event(native), "audit")


def test_audit_native_result_rejects_extra_fields_and_malformed_json():
    native = _native_audit()
    native["unexpected"] = True
    assert not Draft202012Validator(native_output_schema("audit")).is_valid(native)
    with pytest.raises(ResultParseError):
        parse_native_output(_event(native), "audit")
    contaminated = _native_audit()
    contaminated["summ\u200bary"] = contaminated.pop("summary")
    assert not Draft202012Validator(native_output_schema("audit")).is_valid(contaminated)
    with pytest.raises(ResultParseError):
        parse_native_output(_event(contaminated), "audit")
    with pytest.raises(ResultParseError):
        parse_native_output(_event(_native_audit())[:-2] + "}", "audit")
