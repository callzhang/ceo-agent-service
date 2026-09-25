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
    ConsumerProposal,
    ProposedAction,
    RiskLevel,
)
from app.agent_result import ResultParseError, parse_typed_agent_result
from app.agent_wire_contracts import (
    AuditAgentWireResult,
    ConsumerAgentWireResult,
    parse_consumer_agent_wire_result,
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
                "action_identity": "notify-effective-result",
                "capability": "agent_cli.dws",
                "operation": "chat message send",
                "target": {"conversation_reference": "cid-1"},
                "payload": {"text": "The published result is effective today."},
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


def _decision_basis() -> dict[str, object]:
    return {
        "verified_facts": [
            {
                "assertion": "The current OA task is still running.",
                "references": ["oa:task:103917272718"],
            }
        ],
        "rule_evidence": [
            {
                "assertion": "The applicable high-risk rule requires this specific authorization.",
                "references": ["skill:dingtalk-oa-approval#risk"],
            }
        ],
        "quality_explanation": "The material and rule are complete; authorization is separate from evidence completeness.",
        "no_external_action_evidence": [
            {
                "assertion": "No provider receipt exists for this OA action.",
                "references": ["attempt:9733"],
            }
        ],
        "conclusion": "Only this explicit action requires a human decision.",
    }


def _authorization_plan() -> dict[str, object]:
    primary_action = dict(_proposal()["actions"][0])
    primary_action["description"] = "Return the current OA task to its supervisor."
    primary_action["action_identity"] = "return-current-oa-task"
    primary_action["target"] = {
        "oa_process_instance_id": "process-9733",
        "oa_task_id": "task-9733",
    }
    return {
        "summary": "Return the current OA task to its supervisor.",
        "primary_action": primary_action,
        "follow_up_actions": [],
        "side_effects": ["The current OA task is returned to its supervisor."],
        "will_not_do": ["This authorization will not approve or reject the OA."],
        "readback": ["Read the OA history to verify the returned task."],
    }


def _explainable_needs_human_payload(
    model: type[ConsumerAgentResult] | type[AuditAgentResult],
    *,
    authorization_required: bool = False,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "outcome": "needs_human",
        "summary": "A high-risk decision is not covered by the current rule.",
        "decision_options": _decision_options(),
        "risk": "high",
        "confidence": 0.49,
        "rule_coverage": 1.0,
        "information_completeness": 1.0,
        "error": {
            **_error(),
            "code": "authorization_required" if authorization_required else "",
            "authorization_required": authorization_required,
        },
        "needs_human_reason": "A high-risk action needs a concrete decision that the current rule does not provide.",
        "decision_basis": _decision_basis(),
    }
    if model is ConsumerAgentResult:
        payload["proposal"] = None
    else:
        payload.update(
            proposal_revision=0,
            feedback=None,
            external_result=None,
        )
    if authorization_required:
        payload["authorization_plan"] = _authorization_plan()
    return payload


@pytest.mark.parametrize("model", (ConsumerAgentResult, AuditAgentResult))
def test_needs_human_requires_explainable_reason_and_basis(model):
    payload = _explainable_needs_human_payload(model)
    accepted = model.model_validate(payload)
    assert accepted.needs_human_reason
    assert accepted.decision_basis is not None

    for field in ("needs_human_reason", "decision_basis"):
        invalid = dict(payload)
        invalid.pop(field)
        with pytest.raises(ValidationError, match=field):
            model.model_validate(invalid)


@pytest.mark.parametrize("model", (ConsumerAgentResult, AuditAgentResult))
def test_authorization_plan_is_bounded_to_one_explicit_external_action(model):
    payload = _explainable_needs_human_payload(model, authorization_required=True)
    accepted = model.model_validate(payload)
    assert accepted.authorization_plan is not None
    assert accepted.authorization_plan.primary_action.effect == "external"

    generic = dict(payload)
    generic["authorization_plan"] = {
        **_authorization_plan(),
        "summary": "Re-evaluate and execute the current item.",
    }
    with pytest.raises(ValidationError, match="summary"):
        model.model_validate(generic)

    no_effect = dict(payload)
    no_effect["authorization_plan"] = {
        **_authorization_plan(),
        "primary_action": {
            **_authorization_plan()["primary_action"],
            "effect": "none",
        },
    }
    with pytest.raises(ValidationError, match="external"):
        model.model_validate(no_effect)


@pytest.mark.parametrize("model", (ConsumerAgentResult, AuditAgentResult))
def test_non_human_outcome_rejects_human_decision_fields(model):
    payload = (
        {
            "outcome": "no_action",
            "summary": "Nothing to do.",
            "proposal": None,
            "decision_options": [],
            "risk": "low",
            "confidence": 1.0,
            "rule_coverage": 1.0,
            "information_completeness": 1.0,
            "error": _error(),
        }
        if model is ConsumerAgentResult
        else _audit_payload(outcome="failed", feedback=None, external_result=None)
    )
    payload.update(
        needs_human_reason="A reason that is only valid for a human decision.",
        decision_basis=_decision_basis(),
    )
    with pytest.raises(ValidationError, match="needs_human"):
        model.model_validate(payload)


def test_proposed_action_does_not_require_deferred_structured_boundary_field():
    assert "external_boundary" not in ProposedAction.model_fields


def test_proposed_action_requires_stable_action_identity():
    action = dict(_proposal()["actions"][0])
    action.pop("action_identity")

    with pytest.raises(ValidationError, match="action_identity"):
        ProposedAction.model_validate(action)


def test_needs_human_follows_decision_quality_classification_for_all_task_types():
    consumer_payload = _explainable_needs_human_payload(ConsumerAgentResult)
    accepted = ConsumerAgentResult.model_validate(consumer_payload)
    assert accepted.risk is RiskLevel.HIGH
    assert accepted.confidence == 0.49

    low_coverage = ConsumerAgentResult.model_validate(
        {
            **consumer_payload,
            "risk": "low",
            "confidence": 1.0,
            "rule_coverage": 0.1,
        }
    )
    assert low_coverage.outcome is ConsumerOutcome.NEEDS_HUMAN

    audit_payload = _explainable_needs_human_payload(AuditAgentResult)
    audit = AuditAgentResult.model_validate(audit_payload)
    assert audit.risk is RiskLevel.HIGH
    assert audit.confidence == 0.49

    audit_low_coverage = AuditAgentResult.model_validate(
        {
            **audit_payload,
            "risk": "low",
            "confidence": 1.0,
            "rule_coverage": 0.1,
        }
    )
    assert audit_low_coverage.outcome is AuditOutcome.NEEDS_HUMAN


@pytest.mark.parametrize("model", (ConsumerAgentResult, AuditAgentResult))
@pytest.mark.parametrize(
    "field", ["risk", "confidence", "rule_coverage", "information_completeness"]
)
def test_domain_result_requires_all_decision_quality_fields(model, field):
    payload = (
        {
            "outcome": "no_action",
            "summary": "Nothing to do.",
            "proposal": None,
            "decision_options": [],
            "risk": "low",
            "confidence": 1.0,
            "rule_coverage": 1.0,
            "information_completeness": 1.0,
            "error": _error(),
        }
        if model is ConsumerAgentResult
        else _audit_payload(outcome="failed")
    )
    payload.pop(field)
    with pytest.raises(ValidationError):
        model.model_validate(payload)


def test_decision_quality_fields_are_readable_and_classify_needs_human():
    payload = _explainable_needs_human_payload(ConsumerAgentResult)
    result = ConsumerAgentResult.model_validate(payload)

    assert result.rule_coverage == 1.0
    assert result.information_completeness == 1.0


@pytest.mark.parametrize(
    ("risk", "confidence", "rule_coverage", "information_completeness"),
    [
        ("low", 1.0, 1.0, 0.1),
        ("high", 1.0, 1.0, 1.0),
    ],
)
def test_needs_human_rejects_non_needs_human_quality_classifications(
    risk, confidence, rule_coverage, information_completeness
):
    payload = {
        **_explainable_needs_human_payload(ConsumerAgentResult),
        "risk": risk,
        "confidence": confidence,
        "rule_coverage": rule_coverage,
        "information_completeness": information_completeness,
    }
    with pytest.raises(ValidationError, match="must match decision quality"):
        ConsumerAgentResult.model_validate(payload)


@pytest.mark.parametrize(
    "field",
    ["confidence", "rule_coverage", "information_completeness"],
)
def test_decision_quality_scores_reject_boolean_and_out_of_range_values(field):
    payload = {
        "outcome": "no_action",
        "summary": "Nothing to do.",
        "proposal": None,
        "decision_options": [],
        "risk": "low",
        "confidence": 1.0,
        "rule_coverage": 1.0,
        "information_completeness": 1.0,
        "error": _error(),
    }
    with pytest.raises(ValidationError):
        ConsumerAgentResult.model_validate({**payload, field: True})
    with pytest.raises(ValidationError):
        ConsumerAgentResult.model_validate({**payload, field: 1.1})


def test_failed_result_remains_failed_when_quality_is_low():
    payload = {
        "outcome": "failed",
        "summary": "The dependency failed.",
        "proposal": None,
        "decision_options": [],
        "risk": "high",
        "confidence": 0.1,
        "rule_coverage": 0.1,
        "information_completeness": 0.1,
        "error": _error(),
    }
    result = ConsumerAgentResult.model_validate(payload)
    assert result.outcome is ConsumerOutcome.FAILED


@pytest.mark.parametrize(
    "field",
    ["risk", "confidence", "rule_coverage", "information_completeness"],
)
def test_wire_result_requires_generic_risk_assessment_fields(field):
    payload = _consumer_wire_payload()
    payload.pop(field)
    with pytest.raises((ValidationError, JsonSchemaValidationError)):
        _validate_wire_schema(ConsumerAgentWireResult, payload)
    with pytest.raises(ValidationError):
        ConsumerAgentWireResult.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("confidence", True),
        ("rule_coverage", "complete"),
        ("information_completeness", False),
        ("rule_coverage", 1.1),
    ],
)
def test_wire_decision_quality_fields_reject_non_numeric_or_out_of_range_values(
    field, value
):
    payload = _consumer_wire_payload(**{field: value})
    with pytest.raises(ValidationError):
        ConsumerAgentWireResult.model_validate(payload)


def _consumer_wire_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "outcome": "no_action",
        "summary": "Nothing to do.",
        "proposal": None,
        "decision_options": [],
        "error_code": "",
        "error_retryable": False,
        "error_authorization_required": False,
        "risk": "low",
        "confidence": 1.0,
        "rule_coverage": 1.0,
        "information_completeness": 1.0,
        "durable_memories": [],
    }
    payload.update(overrides)
    if payload["outcome"] == "needs_human" and "risk" not in overrides:
        payload.update(risk="high", confidence=0.1)
    if payload["outcome"] == "needs_human":
        payload.setdefault(
            "needs_human_reason",
            "A high-risk rule decision needs an explicit human choice.",
        )
        payload.setdefault("decision_basis", _decision_basis())
        if "error_code" not in overrides:
            payload["error_code"] = ""
        if "error_retryable" not in overrides:
            payload["error_retryable"] = False
    return payload


def _audit_wire_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "outcome": "failed",
        "summary": "The dependency failed.",
        "proposal_revision": 0,
        "feedback": None,
        "external_result": None,
        "decision_options": [],
        "error_code": "dependency_failed",
        "error_retryable": True,
        "error_authorization_required": False,
        "risk": "low",
        "confidence": 1.0,
        "rule_coverage": 1.0,
        "information_completeness": 1.0,
    }
    payload.update(overrides)
    if payload["outcome"] == "needs_human" and "risk" not in overrides:
        payload.update(risk="high", confidence=0.1)
    if payload["outcome"] == "needs_human":
        payload.setdefault(
            "needs_human_reason",
            "A high-risk rule decision needs an explicit human choice.",
        )
        payload.setdefault("decision_basis", _decision_basis())
        if "error_code" not in overrides:
            payload["error_code"] = ""
        if "error_retryable" not in overrides:
            payload["error_retryable"] = False
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
        "outcome": "revision_required",
        "summary": "Candidate adds a management commitment.",
        "proposal_revision": 0,
        "feedback": {
            "rule": "Do not publish a new commitment without authority.",
            "observation": "No source authorizes a recurring review promise.",
            "requested_revision": "Remove that promise and retain the final result.",
        },
        "external_result": None,
        "decision_options": [],
        "error": _error(),
        "risk": "low",
        "confidence": 1.0,
        "rule_coverage": 1.0,
        "information_completeness": 1.0,
    }
    payload.update(overrides)
    if payload["outcome"] == "needs_human":
        payload.setdefault("risk", "high")
        payload.setdefault("confidence", 0.1)
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
                external_result={
                    "operation_id": "op-1",
                    "live_result_reference": {"receipt_id": "receipt-1"},
                },
            ),
        ),
        (
            AuditAgentWireResult,
            _audit_wire_payload(
                outcome="revision_required",
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
    assert all(
        field in serialized
        for field in (
            "risk",
            "confidence",
            "rule_coverage",
            "information_completeness",
        )
    )
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
                        "live_result_reference": {"id": "one"},
                    }
                ),
            ),
        ),
        (
            AuditAgentWireResult,
            _audit_wire_payload(outcome="revision_required", feedback=None),
        ),
        (
            AuditAgentWireResult,
            _audit_wire_payload(outcome="unknown"),
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
                outcome="needs_human", reconciliation=[{"action_index": 0}]
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
            "risk": "low",
            "confidence": 1.0,
            "rule_coverage": 1.0,
            "information_completeness": 1.0,
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
                "sourced_facts": [{"assertion": "Unsupported fact", "references": []}],
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
                "decision_options": [],
                "risk": "high",
                "confidence": 0.1,
                "rule_coverage": 1.0,
                "information_completeness": 1.0,
                "error": _error(),
            }
        )

    result = ConsumerAgentWireResult.model_validate(
        {
            "outcome": "needs_human",
            "summary": "A management decision is required.",
            "proposal": None,
            "decision_options": options,
            "risk": "high",
            "confidence": 0.1,
            "rule_coverage": 1.0,
            "information_completeness": 1.0,
            "error_code": "",
            "error_retryable": False,
            "error_authorization_required": False,
            "needs_human_reason": "A high-risk rule decision needs an explicit human choice.",
            "decision_basis": _decision_basis(),
            "durable_memories": [],
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
            error_code="",
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


def test_audit_revision_feedback_is_concrete_and_non_effectful():
    result = AuditAgentResult.model_validate(_audit_payload())

    assert result.outcome is AuditOutcome.FEEDBACK_PROVIDED
    assert result.feedback is not None
    assert result.external_result is None


def test_audit_executed_requires_external_result():
    result = AuditAgentResult.model_validate(
        _audit_payload(
            outcome="executed",
            summary="Message sent and read back.",
            feedback=None,
            external_result={
                "operation_id": "op-1",
                "live_result_reference": {"message_id": "mid-1"},
            },
        )
    )

    assert result.outcome is AuditOutcome.EXECUTED
    assert result.external_result is not None
    assert result.external_result.operation_id == "op-1"


def test_audit_result_does_not_accept_reconciliation_application_field():
    with pytest.raises(ValidationError, match="reconciliation"):
        AuditAgentResult.model_validate(
            _audit_payload(
                outcome="failed",
                feedback=None,
                reconciliation=[
                    {
                        "action_index": 0,
                        "disposition": "ambiguous",
                        "read_result_digest": "digest-1",
                    }
                ],
            )
        )


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
def test_audit_result_rejects_reconciliation_application_field(
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
                "live_result_reference": {},
            }
        ),
        _audit_payload(
            outcome="executed",
            feedback=None,
            external_result=None,
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
        assert set(
            ("risk", "confidence", "rule_coverage", "information_completeness")
        ).issubset(schema["required"])
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
            ),
        ),
        (
            "audit_agent_result.schema.json",
            _audit_payload(
                outcome="needs_human",
                feedback=None,
            ),
        ),
    ],
)
def test_committed_schemas_reject_cross_field_mismatches(schema_name, payload):
    schema = json.loads((SCHEMA_DIR / schema_name).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)

    with pytest.raises(JsonSchemaValidationError):
        Draft202012Validator(schema).validate(payload)


def test_agent_results_reject_removed_side_effect_state_field():
    with pytest.raises(ValidationError, match="side_effect_state"):
        AuditAgentResult.model_validate(_audit_payload(side_effect_state="none"))


def test_proposed_action_uses_runtime_result_instead_of_verification_plan():
    proposal = _proposal()

    parsed = ConsumerProposal.model_validate(proposal)
    assert parsed.actions[0].action_identity

    proposal["actions"][0]["expected_verification"] = "Read it back."
    with pytest.raises(ValidationError, match="expected_verification"):
        ConsumerProposal.model_validate(proposal)


def test_nested_removed_error_state_is_rejected_for_python_and_json_inputs():
    payload = {
        "outcome": "no_action",
        "summary": "Nothing to do.",
        "proposal": None,
        "error": {**_error(), "side_effect_state": "confirmed"},
    }

    with pytest.raises(ValidationError, match="side_effect_state"):
        ConsumerAgentResult.model_validate(payload)
    with pytest.raises(ValidationError, match="side_effect_state"):
        ConsumerAgentResult.model_validate_json(json.dumps(payload))


def test_parse_typed_agent_result_uses_current_codex_output_shape():
    payload = {
        "outcome": "proposal",
        "summary": "Prepare the notice.",
        "proposal": _proposal(),
        "risk": "low",
        "confidence": 1.0,
        "rule_coverage": 1.0,
        "information_completeness": 1.0,
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


def test_wire_result_accepts_null_error_code_as_no_error():
    payload = {
        "outcome": "proposal",
        "summary": "Prepare the notice.",
        "proposal": _proposal(),
        "decision_options": [],
        "error_code": None,
        "error_retryable": False,
        "error_authorization_required": False,
        "risk": "low",
        "confidence": 0.9,
        "rule_coverage": 1.0,
        "information_completeness": 1.0,
        "durable_memories": [],
    }
    raw = json.dumps(
        {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": json.dumps(payload)},
        }
    )

    result = parse_consumer_agent_wire_result(raw)

    assert result.outcome is ConsumerOutcome.PROPOSAL
    assert result.error.code == ""


def test_parse_typed_agent_result_reports_schema_violation_locations():
    payload = {
        "outcome": "proposal",
        "summary": "Prepare the notice.",
        "proposal": _proposal(),
        "error": {**_error(), "code": None},
    }
    raw = json.dumps(
        {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": json.dumps(payload)},
        }
    )

    with pytest.raises(ResultParseError, match="failed schema validation") as info:
        parse_typed_agent_result(raw, ConsumerAgentResult)

    assert isinstance(info.value.__cause__, ValidationError)
    assert any(
        "error" in error["loc"] and "code" in error["loc"]
        for error in info.value.__cause__.errors()
    )


def test_parse_typed_agent_result_still_reports_missing_when_no_object_exists():
    raw = json.dumps(
        {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": "I could not decide."},
        }
    )

    with pytest.raises(ResultParseError, match="no valid typed result JSON found"):
        parse_typed_agent_result(raw, ConsumerAgentResult)


def test_parse_typed_agent_result_ignores_later_hook_turn_result():
    business_result = {
        "outcome": "proposal",
        "summary": "Notify the applicant.",
        "proposal": _proposal(),
        "risk": "low",
        "confidence": 1.0,
        "rule_coverage": 1.0,
        "information_completeness": 1.0,
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
        "risk": "low",
        "confidence": 1.0,
        "rule_coverage": 1.0,
        "information_completeness": 1.0,
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


def test_parse_typed_agent_result_accepts_single_stray_array_close_after_proposal():
    payload = {
        "outcome": "proposal",
        "summary": "Prepare the notice.",
        "proposal": _proposal(),
        "error": _error(),
        "risk": "low",
        "confidence": 1.0,
        "rule_coverage": 1.0,
        "information_completeness": 1.0,
    }
    valid_json = json.dumps(payload)
    split_at = valid_json.rindex('}, "error"')
    malformed = (
        valid_json[:split_at]
        + '}], "error"'
        + valid_json[split_at + len('}, "error"') :]
    )
    raw = json.dumps(
        {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": malformed},
        }
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
            "risk": "low",
            "confidence": 1.0,
            "rule_coverage": 1.0,
            "information_completeness": 1.0,
            "error_code": "",
            "error_retryable": False,
            "error_authorization_required": False,
            "durable_memories": [],
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
            "feedback": None,
            "external_result": None,
            "decision_options": _decision_options(),
            "risk": "high",
            "confidence": 0.1,
            "rule_coverage": 1.0,
            "information_completeness": 1.0,
            "error_code": "",
            "error_retryable": False,
            "error_authorization_required": False,
            "needs_human_reason": "A high-risk rule decision needs an explicit human choice.",
            "decision_basis": _decision_basis(),
        }
    ).to_result()

    assert result.outcome is AuditOutcome.NEEDS_HUMAN
    assert result.error.code == ""
    assert result.decision_options[0].key == "A"


@pytest.mark.parametrize(
    "error_code",
    (
        "dependency_read_unavailable",
        "xiaoqing_interview_mcp_not_injected",
        "xiaoqing_interview_unavailable",
    ),
)
def test_audit_wire_result_normalizes_transient_dependency_failures_as_retryable(
    error_code: str,
):
    result = AuditAgentWireResult.model_validate(
        {
            "outcome": "failed",
            "summary": "The live dependency could not be read.",
            "proposal_revision": 0,
            "feedback": None,
            "external_result": None,
            "decision_options": [],
            "risk": "low",
            "confidence": 1.0,
            "rule_coverage": 1.0,
            "information_completeness": 1.0,
            "error_code": error_code,
            "error_retryable": False,
            "error_authorization_required": False,
        }
    ).to_result()

    assert result.error.retryable is True


def test_consumer_wire_result_normalizes_dependency_read_failure_as_retryable():
    result = ConsumerAgentWireResult.model_validate(
        _consumer_wire_payload(
            outcome="failed",
            error_code="dependency_read_unavailable",
            error_retryable=False,
        )
    ).to_result()

    assert result.error.retryable is True


def test_the_service_decides_what_an_unknown_reported_failure_means():
    result = AuditAgentWireResult.model_validate(
        {
            "outcome": "failed",
            "summary": "The business request is invalid.",
            "proposal_revision": 0,
            "feedback": None,
            "external_result": None,
            "decision_options": [],
            "risk": "low",
            "confidence": 1.0,
            "rule_coverage": 1.0,
            "information_completeness": 1.0,
            "error_code": "invalid_business_request",
            "error_retryable": False,
            "error_authorization_required": True,
        }
    ).to_result()

    # The turn's own flags are not read: a code the service does not know
    # takes the bounded retry, and the turn's wording is kept for diagnosis.
    assert result.error.code == "agent_reported_failure"
    assert result.error.retryable is True
    assert result.error.authorization_required is False
    assert result.error.source_code == "invalid_business_request"


def test_audit_wire_result_preserves_revision_feedback_fields():
    result = AuditAgentWireResult.model_validate(
        {
            "outcome": "revision_required",
            "summary": "The command needs confirmation.",
            "proposal_revision": 0,
            "feedback": {
                "rule": "DWS writes require --yes.",
                "observation": "The proposed argv omitted --yes.",
                "requested_revision": "Add --yes without changing the action.",
            },
            "external_result": None,
            "risk": "medium",
            "confidence": 0.8,
            "rule_coverage": 1.0,
            "information_completeness": 1.0,
            "error_code": "dws_write_missing_yes",
            "error_retryable": True,
            "error_authorization_required": False,
        }
    ).to_result()

    assert result.outcome is AuditOutcome.FEEDBACK_PROVIDED
    assert result.feedback is not None
    assert (
        result.feedback.requested_revision == "Add --yes without changing the action."
    )


def test_dingtalk_message_actions_require_canonical_target_fields():
    with pytest.raises(ValidationError, match="conversation_id and message_id"):
        ProposedAction.model_validate(
            {
                "description": "Reply",
                "action_identity": "reply-result",
                "capability": "dingtalk-chat",
                "operation": "messages-reply",
                "target": {
                    "open_conversation_id": "cid-1",
                    "reply_to_message_id": "message-1",
                },
                "payload": {"content": "done"},
            }
        )

    canonical = ProposedAction.model_validate(
        {
            "description": "Reply",
            "action_identity": "reply-result",
            "capability": "dingtalk-chat",
            "operation": "messages-reply",
            "target": {"conversation_id": "cid-1", "message_id": "message-1"},
            "payload": {"content": "done"},
        }
    )
    assert canonical.target["conversation_id"] == "cid-1"


def test_agent_message_json_objects_scans_fences_and_prose():
    from app.agent_result import agent_message_json_objects

    text = (
        'Draft {not json} first:\n```json\n{"a": 1}\n```\n'
        'then the final object: {"b": {"nested": [1, 2]}} done.'
    )

    assert agent_message_json_objects(text) == [{"a": 1}, {"b": {"nested": [1, 2]}}]
    assert agent_message_json_objects("no objects here") == []


@pytest.mark.parametrize(
    ("operation", "delivery"),
    [
        ("reply", "reply"),
        ("messages-reply", "reply"),
        ("reply_to_message", "reply"),
        ("dws chat +messages-reply", "reply"),
        ("reply_to_group_message", "reply"),
        ("send_group_message", "group"),
        ("messages-send-to-group", "group"),
        ("send_direct_message", "direct"),
        ("messages-send", "direct"),
    ],
)
def test_every_spelling_of_a_chat_operation_routes_the_same_way(
    operation: str, delivery: str
) -> None:
    from app.agent_contracts import dingtalk_chat_delivery

    assert dingtalk_chat_delivery(operation) == delivery


def test_a_group_reply_named_reply_to_message_is_executable() -> None:
    """Task 384694: Audit was refused six times on a valid reply.

    The Consumer called it `reply_to_message`; the executor knew three other
    spellings, found no recipient for a "direct" send, and reported
    `dingtalk_message_action_unsupported`.
    """
    from app.consumer_agent import structured_dingtalk_outgoing_text_key

    action = ProposedAction(
        action_identity="reply_to_msgExampleTriggerAAAAAAA==_settlement-policy-boundary",
        capability="dingtalk-chat",
        operation="reply_to_message",
        description="在星尘-财务管理群中回复触发消息",
        target={
            "conversation_id": "cidFinanceGroupExampleAAA==",
            "message_id": "msgExampleTriggerAAAAAAA==",
        },
        payload={"content": "先作为讨论稿，正式执行前再确认计算口径。"},
    )

    assert structured_dingtalk_outgoing_text_key(action) == "content"


def test_a_reply_by_any_name_still_needs_the_message_it_replies_to() -> None:
    with pytest.raises(ValidationError, match="reply target requires"):
        ProposedAction(
            action_identity="a",
            capability="dingtalk-chat",
            operation="reply_to_message",
            description="d",
            target={"conversation_id": "cid"},
            payload={"content": "x"},
        )


def test_a_proposal_can_also_ask_derek_an_independent_question():
    """Derek, 2026-09-23: act on the material gap, escalate the rule gap.

    384699 and 384514 each lost one half because a result could be a proposal
    or needs_human but not both.
    """
    from app.agent_wire_contracts import ConsumerAgentWireResult

    wire = _consumer_wire_payload(
        outcome="proposal",
        proposal=_proposal(),
        decision_options=_decision_options(),
        needs_human_reason="No written rule covers this signing authority.",
        decision_basis=_decision_basis(),
        rule_coverage=0.0,
    )

    result = ConsumerAgentWireResult.model_validate(wire).to_result()

    assert result.escalates
    assert result.proposal is not None


@pytest.mark.parametrize(
    "missing", ("decision_options", "needs_human_reason", "decision_basis")
)
def test_an_escalating_proposal_needs_the_whole_question(missing):
    payload = {
        "outcome": "proposal",
        "summary": "Comment for material and ask Derek.",
        "proposal": _proposal(),
        "decision_options": _decision_options(),
        "needs_human_reason": "No written rule covers this signing authority.",
        "decision_basis": _decision_basis(),
        "risk": "low",
        "confidence": 1.0,
        "rule_coverage": 0.0,
        "information_completeness": 1.0,
        "error": _error(),
    }
    payload[missing] = [] if missing == "decision_options" else None

    with pytest.raises(ValidationError, match="escalates"):
        ConsumerAgentResult.model_validate(payload)


def test_an_escalating_proposal_is_a_stored_human_decision():
    """The Attempt points at the Consumer run that asked the question."""
    from app.decision_quality import parse_stored_needs_human_decision

    escalating = {
        "outcome": "proposal",
        "summary": "Comment for material and ask Derek.",
        "proposal": _proposal(),
        "decision_options": _decision_options(),
        "needs_human_reason": "No written rule covers this signing authority.",
        "decision_basis": _decision_basis(),
        "risk": "low",
        "confidence": 1.0,
        "rule_coverage": 0.0,
        "information_completeness": 1.0,
        "error": _error(),
    }
    plain = {
        key: value
        for key, value in escalating.items()
        if key not in {"needs_human_reason", "decision_basis"}
    } | {"decision_options": []}

    assert parse_stored_needs_human_decision(json.dumps(escalating)) is not None
    assert parse_stored_needs_human_decision(json.dumps(plain)) is None
