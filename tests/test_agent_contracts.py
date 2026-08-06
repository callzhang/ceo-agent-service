import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError
from pydantic import ValidationError

from app.agent_contracts import (
    AuditAgentResult,
    AuditOutcome,
    ConsumerAgentResult,
    ConsumerOutcome,
)
from app.agent_result import parse_typed_agent_result


SCHEMA_DIR = Path(__file__).parents[1] / "app" / "schemas"


def _error() -> dict[str, object]:
    return {
        "code": "",
        "retryable": False,
        "authorization_required": False,
    }


def _proposal() -> dict[str, object]:
    return {
        "objective": "Notify the verified recipient",
        "actions": [
            {
                "description": "Send one private message",
                "target": {"conversation_reference": "cid-1"},
                "payload": {"text": "The published result is effective today."},
                "expected_verification": "Read the sent message by operation id",
            }
        ],
        "sourced_facts": [
            {
                "assertion": "The result is effective today.",
                "references": ["message:trigger"],
            }
        ],
        "authored_judgment": "Use a factual private notice.",
    }


def _audit_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "outcome": "revision_required",
        "summary": "Candidate adds a management commitment.",
        "proposal_revision": 0,
        "side_effect_state": "none",
        "feedback": {
            "rule": "Do not publish a new commitment without authority.",
            "observation": "No source authorizes a recurring review promise.",
            "requested_revision": "Remove that promise and retain the final result.",
        },
        "external_result": None,
        "error": _error(),
    }
    payload.update(overrides)
    return payload


def test_consumer_proposal_keeps_facts_and_judgment_separate():
    result = ConsumerAgentResult.model_validate(
        {
            "outcome": "proposal",
            "summary": "Prepare the factual notice.",
            "proposal": _proposal(),
            "error": _error(),
        }
    )

    assert result.outcome is ConsumerOutcome.PROPOSAL
    assert result.proposal is not None
    assert result.proposal.sourced_facts[0].references == ("message:trigger",)
    assert result.proposal.authored_judgment == "Use a factual private notice."


@pytest.mark.parametrize(
    "payload",
    [
        {
            "outcome": "proposal",
            "summary": "Missing proposal.",
            "proposal": None,
            "error": _error(),
        },
        {
            "outcome": "no_action",
            "summary": "No action.",
            "proposal": _proposal(),
            "error": _error(),
        },
        {
            "outcome": "proposal",
            "summary": "Missing source reference.",
            "proposal": {
                **_proposal(),
                "sourced_facts": [
                    {"assertion": "Unsupported fact", "references": []}
                ],
            },
            "error": _error(),
        },
    ],
)
def test_consumer_contract_rejects_incomplete_or_mismatched_proposals(payload):
    with pytest.raises(ValidationError):
        ConsumerAgentResult.model_validate(payload)


def test_audit_revision_feedback_is_concrete_and_non_effectful():
    result = AuditAgentResult.model_validate(_audit_payload())

    assert result.outcome is AuditOutcome.REVISION_REQUIRED
    assert result.feedback is not None
    assert result.external_result is None


def test_audit_executed_requires_confirmed_external_result():
    result = AuditAgentResult.model_validate(
        _audit_payload(
            outcome="executed",
            summary="Message sent and read back.",
            side_effect_state="confirmed",
            feedback=None,
            external_result={
                "operation_id": "op-1",
                "verification_summary": "Sent message is visible by operation id.",
                "live_result_reference": {"message_id": "mid-1"},
            },
        )
    )

    assert result.outcome is AuditOutcome.EXECUTED
    assert result.external_result is not None
    assert result.external_result.operation_id == "op-1"


@pytest.mark.parametrize(
    "payload",
    [
        _audit_payload(feedback=None),
        _audit_payload(
            external_result={
                "operation_id": "op-1",
                "verification_summary": "unexpected",
                "live_result_reference": {},
            }
        ),
        _audit_payload(
            outcome="executed",
            feedback=None,
            side_effect_state="none",
            external_result={
                "operation_id": "op-1",
                "verification_summary": "not confirmed",
                "live_result_reference": {},
            },
        ),
        _audit_payload(
            outcome="unknown",
            feedback=None,
            side_effect_state="none",
        ),
        _audit_payload(
            outcome="failed",
            feedback=None,
            side_effect_state="confirmed",
        ),
    ],
)
def test_audit_contract_rejects_inconsistent_outcome_payloads(payload):
    with pytest.raises(ValidationError):
        AuditAgentResult.model_validate(payload)


def test_contract_schemas_match_models_and_do_not_enumerate_business_actions():
    expected = {
        "consumer_agent_result.schema.json": ConsumerAgentResult.model_json_schema(),
        "audit_agent_result.schema.json": AuditAgentResult.model_json_schema(),
    }

    for filename, schema in expected.items():
        committed = json.loads((SCHEMA_DIR / filename).read_text(encoding="utf-8"))
        assert committed == schema
        serialized = json.dumps(schema, ensure_ascii=False)
        for business_action in (
            "send_dingtalk_reply",
            "oa_approval",
            "send_mail",
            "edit_document",
        ):
            assert business_action not in serialized


@pytest.mark.parametrize(
    ("schema_name", "payload"),
    [
        (
            "consumer_agent_result.schema.json",
            {
                "outcome": "proposal",
                "summary": "Missing proposal.",
                "proposal": None,
                "error": _error(),
            },
        ),
        (
            "audit_agent_result.schema.json",
            _audit_payload(
                outcome="executed",
                feedback=None,
                side_effect_state="none",
            ),
        ),
        (
            "audit_agent_result.schema.json",
            _audit_payload(
                outcome="needs_human",
                feedback=None,
                side_effect_state="confirmed",
            ),
        ),
    ],
)
def test_committed_schemas_reject_cross_field_mismatches(schema_name, payload):
    schema = json.loads((SCHEMA_DIR / schema_name).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)

    with pytest.raises(JsonSchemaValidationError):
        Draft202012Validator(schema).validate(payload)


@pytest.mark.parametrize(
    ("outcome", "side_effect_state"),
    [
        ("revision_required", "confirmed"),
        ("failed", "unknown"),
        ("needs_human", "confirmed"),
        ("needs_human", "unknown"),
    ],
)
def test_nonexecuted_audit_outcomes_cannot_claim_side_effects(
    outcome,
    side_effect_state,
):
    payload = _audit_payload(
        outcome=outcome,
        feedback=(
            _audit_payload()["feedback"] if outcome == "revision_required" else None
        ),
        side_effect_state=side_effect_state,
    )

    with pytest.raises(ValidationError):
        AuditAgentResult.model_validate(payload)


def test_nested_legacy_error_state_is_consistent_for_python_and_json_inputs():
    payload = {
        "outcome": "no_action",
        "summary": "Nothing to do.",
        "proposal": None,
        "error": {**_error(), "side_effect_state": "confirmed"},
    }

    python_result = ConsumerAgentResult.model_validate(payload)
    json_result = ConsumerAgentResult.model_validate_json(json.dumps(payload))

    assert python_result.error.side_effect_state.value == "confirmed"
    assert json_result.error.side_effect_state.value == "confirmed"


def test_parse_typed_agent_result_uses_current_codex_output_shape():
    payload = {
        "outcome": "proposal",
        "summary": "Prepare the notice.",
        "proposal": _proposal(),
        "error": _error(),
    }
    raw = json.dumps(
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": json.dumps(payload)}],
            },
        }
    )

    result = parse_typed_agent_result(raw, ConsumerAgentResult)

    assert result.outcome is ConsumerOutcome.PROPOSAL
