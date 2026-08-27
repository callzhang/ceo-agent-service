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
    ProposedAction,
)
from app.agent_result import parse_typed_agent_result
from app.agent_wire_contracts import (
    AuditAgentWireResult,
    ConsumerAgentWireResult,
)


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
                "capability": "agent_cli.dws",
                "operation": "chat message send",
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


def _decision_options() -> list[dict[str, str]]:
    return [
        {
            "key": "A",
            "label": "Proceed",
            "instruction": "Proceed with the verified candidate.",
            "consequence": "The accepted candidate can move to Audit.",
        },
        {
            "key": "B",
            "label": "Revise",
            "instruction": "Request a corrected candidate.",
            "consequence": "No candidate executes yet.",
        },
    ]


def test_proposed_action_does_not_require_deferred_structured_boundary_field():
    assert "external_boundary" not in ProposedAction.model_fields


def _consumer_wire_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "outcome": "no_action",
        "summary": "Nothing to do.",
        "proposal": None,
        "decision_options": [],
        "error_code": "",
        "error_retryable": False,
        "error_authorization_required": False,
    }
    payload.update(overrides)
    return payload


def _audit_wire_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "outcome": "failed",
        "summary": "The dependency failed.",
        "proposal_revision": 0,
        "side_effect_state": "none",
        "feedback": None,
        "external_result": None,
        "reconciliation": [],
        "decision_options": [],
        "error_code": "dependency_failed",
        "error_retryable": True,
        "error_authorization_required": False,
    }
    payload.update(overrides)
    return payload


def _validate_wire_schema(
    model: type[ConsumerAgentWireResult] | type[AuditAgentWireResult],
    payload: dict[str, object],
) -> None:
    schema = model.model_json_schema()
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(payload)


def _audit_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "outcome": "feedback_provided",
        "summary": "Candidate adds a management commitment.",
        "proposal_revision": 0,
        "side_effect_state": "none",
        "feedback": {
            "rule": "Do not publish a new commitment without authority.",
            "observation": "No source authorizes a recurring review promise.",
            "requested_revision": "Remove that promise and retain the final result.",
        },
        "external_result": None,
        "reconciliation": [],
        "decision_options": [],
        "error": _error(),
    }
    payload.update(overrides)
    return payload


@pytest.mark.parametrize(
    ("model", "payload"),
    (
        (
            ConsumerAgentWireResult,
            _consumer_wire_payload(
                outcome="proposal",
                proposal=_proposal(),
            ),
        ),
        (ConsumerAgentWireResult, _consumer_wire_payload(outcome="no_action")),
        (
            ConsumerAgentWireResult,
            _consumer_wire_payload(
                outcome="needs_human",
                decision_options=_decision_options(),
            ),
        ),
        (ConsumerAgentWireResult, _consumer_wire_payload(outcome="failed")),
        (
            AuditAgentWireResult,
            _audit_wire_payload(
                outcome="executed",
                side_effect_state="confirmed",
                external_result={
                    "operation_id": "op-1",
                    "verification_summary": "The effect was read back.",
                    "live_result_reference": {"receipt_id": "receipt-1"},
                },
            ),
        ),
        (
            AuditAgentWireResult,
            _audit_wire_payload(
                outcome="feedback_provided",
                feedback={
                    "rule": "Use verified Skill receipts.",
                    "observation": "A receipt is missing.",
                    "requested_revision": "Read the Skill and replace the candidate.",
                },
            ),
        ),
        (
            AuditAgentWireResult,
            _audit_wire_payload(
                outcome="needs_human",
                decision_options=_decision_options(),
            ),
        ),
        (
            AuditAgentWireResult,
            _audit_wire_payload(
                outcome="dry_run",
                error_code="dry_run_execution_suppressed",
                error_retryable=False,
            ),
        ),
        (AuditAgentWireResult, _audit_wire_payload(outcome="failed")),
    ),
)
def test_generated_wire_schema_acceptance_always_converts(model, payload):
    _validate_wire_schema(model, payload)
    assert model.model_validate(payload).to_result() is not None


@pytest.mark.parametrize(
    "model",
    (ConsumerAgentWireResult, AuditAgentWireResult),
)
def test_wire_schema_is_discriminated_and_contains_only_nested_fields(model):
    schema = model.model_json_schema()
    serialized = json.dumps(schema, ensure_ascii=False)

    assert schema["discriminator"]["propertyName"] == "outcome"
    assert schema["oneOf"]
    assert "contentSchema" not in serialized
    for legacy_field in (
        "proposal_json",
        "decision_options_json",
        "feedback_json",
        "external_result_json",
        "reconciliation_json",
    ):
        assert legacy_field not in serialized


@pytest.mark.parametrize(
    ("model", "payload"),
    (
        (
            ConsumerAgentWireResult,
            _consumer_wire_payload(outcome="proposal", proposal=None),
        ),
        (
            ConsumerAgentWireResult,
            _consumer_wire_payload(
                outcome="proposal",
                proposal=_proposal(),
                proposal_json=json.dumps(_proposal()),
            ),
        ),
        (
            ConsumerAgentWireResult,
            _consumer_wire_payload(
                outcome="proposal",
                proposal=json.dumps(_proposal()),
            ),
        ),
        (
            ConsumerAgentWireResult,
            _consumer_wire_payload(
                outcome="proposal",
                proposal=_proposal(),
                decision_options=_decision_options(),
            ),
        ),
        (
            ConsumerAgentWireResult,
            _consumer_wire_payload(outcome="needs_human"),
        ),
        (
            ConsumerAgentWireResult,
            _consumer_wire_payload(
                outcome="needs_human",
                decision_options=[{"key": "A"}],
            ),
        ),
        (
            ConsumerAgentWireResult,
            _consumer_wire_payload(outcome="no_action", proposal={}),
        ),
        (
            AuditAgentWireResult,
            _audit_wire_payload(
                outcome="executed",
                side_effect_state="none",
                external_result={},
            ),
        ),
        (
            AuditAgentWireResult,
            _audit_wire_payload(
                outcome="executed",
                side_effect_state="confirmed",
                external_result=None,
            ),
        ),
        (
            AuditAgentWireResult,
            _audit_wire_payload(
                outcome="executed",
                side_effect_state="confirmed",
                external_result=json.dumps(
                    {
                        "operation_id": "op-1",
                        "verification_summary": "Read back.",
                        "live_result_reference": {"id": "one"},
                    }
                ),
            ),
        ),
        (
            AuditAgentWireResult,
            _audit_wire_payload(outcome="feedback_provided", feedback=None),
        ),
        (
            AuditAgentWireResult,
            _audit_wire_payload(
                outcome="failed",
                side_effect_state="confirmed",
            ),
        ),
        (
            AuditAgentWireResult,
            _audit_wire_payload(
                outcome="needs_human",
                reconciliation=[{"action_index": 0}],
            ),
        ),
    ),
)
def test_invalid_wire_combinations_fail_generated_schema_and_local_model(
    model,
    payload,
):
    with pytest.raises(JsonSchemaValidationError):
        _validate_wire_schema(model, payload)
    with pytest.raises(ValidationError):
        model.model_validate(payload)


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


def test_proposed_action_rejects_empty_target():
    with pytest.raises(ValidationError):
        ProposedAction.model_validate(
            {
                "description": "Send",
                "capability": "agent_cli.dws",
                "operation": "chat message send",
                "target": {},
                "payload": {"text": "done"},
                "expected_verification": "Message exists",
            }
        )


def test_proposed_action_requires_operation_identity():
    action = _proposal()["actions"][0]
    assert isinstance(action, dict)
    action.pop("operation")

    with pytest.raises(ValidationError):
        ProposedAction.model_validate(action)


def test_proposed_action_requires_capability_identity():
    action = _proposal()["actions"][0]
    assert isinstance(action, dict)
    action.pop("capability")

    with pytest.raises(ValidationError):
        ProposedAction.model_validate(action)


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


def test_needs_human_requires_actionable_options_and_wire_preserves_them():
    options = [
        {
            "key": "A",
            "label": "同意当前方案",
            "instruction": "同意已核验的当前方案并发布。",
            "consequence": "会执行经过审计的外部动作。",
        },
        {
            "key": "B",
            "label": "要求补充材料",
            "instruction": "要求申请人补充缺失材料并发布。",
            "consequence": "当前外部动作不会执行。",
        },
    ]
    with pytest.raises(ValidationError, match="decision options"):
        ConsumerAgentResult.model_validate(
            {
                "outcome": "needs_human",
                "summary": "A management decision is required.",
                "proposal": None,
                "error": _error(),
            }
        )

    result = ConsumerAgentWireResult.model_validate(
        {
            "outcome": "needs_human",
            "summary": "A management decision is required.",
            "proposal": None,
            "decision_options": options,
            "error_code": "decision_required",
            "error_retryable": False,
            "error_authorization_required": False,
        }
    ).to_result()

    assert result.decision_options[0].instruction == options[0]["instruction"]


def test_audit_needs_human_requires_actionable_options_and_wire_preserves_them():
    options = _decision_options()
    with pytest.raises(ValidationError, match="decision options"):
        AuditAgentResult.model_validate(
            _audit_payload(
                outcome="needs_human",
                feedback=None,
            )
        )

    result = AuditAgentWireResult.model_validate(
        _audit_wire_payload(
            outcome="needs_human",
            decision_options=options,
            error_code="decision_required",
            error_retryable=False,
        )
    ).to_result()

    assert result.decision_options[0].instruction == options[0]["instruction"]


def test_audit_dry_run_is_non_effectful_and_cannot_be_a_human_decision():
    result = AuditAgentWireResult.model_validate(
        _audit_wire_payload(
            outcome="dry_run",
            error_code="dry_run_execution_suppressed",
            error_retryable=False,
        )
    ).to_result()

    assert result.outcome is AuditOutcome.DRY_RUN
    assert result.decision_options == ()

    with pytest.raises(ValidationError, match="dry_run requires"):
        AuditAgentResult.model_validate(
            _audit_payload(
                outcome="dry_run",
                feedback=None,
                error={
                    "code": "wrong_code",
                    "retryable": False,
                    "authorization_required": False,
                },
            )
        )


def test_audit_feedback_is_concrete_and_non_effectful():
    result = AuditAgentResult.model_validate(_audit_payload())

    assert result.outcome is AuditOutcome.FEEDBACK_PROVIDED
    assert result.feedback is not None
    assert result.external_result is None


def test_audit_contract_rejects_removed_legacy_outcomes():
    with pytest.raises(ValidationError):
        AuditAgentResult.model_validate(_audit_payload(outcome="revision_required"))
    with pytest.raises(ValidationError):
        AuditAgentWireResult.model_validate(
            _audit_wire_payload(outcome="revision_required")
        )


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
    "reconciliation",
    (
        [{"action_index": 0, "disposition": "present"}],
        [
            {
                "action_index": 0,
                "disposition": "present",
                "read_result_digest": "digest-1",
            },
            {
                "action_index": 0,
                "disposition": "absent",
                "read_result_digest": "digest-2",
            },
        ],
    ),
)
def test_audit_reconciliation_rejects_missing_digest_or_duplicate_action(
    reconciliation,
):
    with pytest.raises(ValidationError):
        AuditAgentResult.model_validate(
            _audit_payload(
                outcome="needs_human",
                feedback=None,
                reconciliation=reconciliation,
            )
        )


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
        assert schema["type"] == "object"
        assert all(branch["type"] == "object" for branch in schema["anyOf"])
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
        ("feedback_provided", "confirmed"),
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
            _audit_payload()["feedback"] if outcome == "feedback_provided" else None
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


def test_parse_typed_agent_result_ignores_later_hook_turn_result():
    business_result = {
        "outcome": "proposal",
        "summary": "Notify the applicant.",
        "proposal": _proposal(),
        "error": _error(),
    }
    hook_result = {
        "outcome": "no_action",
        "summary": "No durable memory update is needed.",
        "proposal": None,
        "error": _error(),
    }
    raw = "\n".join(
        json.dumps(event)
        for event in (
            {"type": "thread.started", "thread_id": "session-1"},
            {"type": "turn.started"},
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": json.dumps(business_result)},
            },
            {"type": "turn.completed"},
            {"type": "turn.started"},
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": json.dumps(hook_result)},
            },
            {"type": "turn.completed"},
        )
    )

    result = parse_typed_agent_result(raw, ConsumerAgentResult)

    assert result.outcome is ConsumerOutcome.PROPOSAL
    assert result.summary == "Notify the applicant."


def test_parse_typed_agent_result_skips_later_malformed_candidate():
    valid = {
        "outcome": "proposal",
        "summary": "Prepare the notice.",
        "proposal": _proposal(),
        "error": _error(),
    }
    raw = "\n".join(
        json.dumps(event)
        for event in (
            {"type": "turn.started", "thread_id": "session-1"},
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": json.dumps(valid)},
            },
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": "not typed JSON"},
            },
            {"type": "turn.completed"},
        )
    )

    result = parse_typed_agent_result(raw, ConsumerAgentResult)

    assert result.outcome is ConsumerOutcome.PROPOSAL


def test_consumer_wire_result_preserves_nested_proposal_fields():
    result = ConsumerAgentWireResult.model_validate(
        {
            "outcome": "proposal",
            "summary": "Prepare the notice.",
            "proposal": _proposal(),
            "decision_options": [],
            "error_code": "",
            "error_retryable": False,
            "error_authorization_required": False,
        }
    ).to_result()

    assert result.proposal is not None
    assert result.proposal.actions[0].target == {"conversation_reference": "cid-1"}


def test_audit_wire_result_preserves_nested_result_fields():
    result = AuditAgentWireResult.model_validate(
        {
            "outcome": "needs_human",
            "summary": "A decision is required.",
            "proposal_revision": 0,
            "side_effect_state": "none",
            "feedback": None,
            "external_result": None,
            "reconciliation": [],
            "decision_options": _decision_options(),
            "error_code": "decision_required",
            "error_retryable": False,
            "error_authorization_required": False,
        }
    ).to_result()

    assert result.outcome is AuditOutcome.NEEDS_HUMAN
    assert result.error.code == "decision_required"
    assert result.decision_options[0].key == "A"


def test_audit_wire_result_preserves_feedback_fields():
    result = AuditAgentWireResult.model_validate(
        {
            "outcome": "feedback_provided",
            "summary": "The command needs confirmation.",
            "proposal_revision": 0,
            "side_effect_state": "none",
            "feedback": {
                "rule": "DWS writes require --yes.",
                "observation": "The proposed argv omitted --yes.",
                "requested_revision": "Add --yes without changing the action.",
            },
            "external_result": None,
            "reconciliation": [],
            "error_code": "dws_write_missing_yes",
            "error_retryable": True,
            "error_authorization_required": False,
        }
    ).to_result()

    assert result.outcome is AuditOutcome.FEEDBACK_PROVIDED
    assert result.feedback is not None
    assert result.feedback.requested_revision == "Add --yes without changing the action."
