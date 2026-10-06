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
            "applies_to": "task_class",
        },
        {
            "key": "B",
            "label": "Revise",
            "instruction": "Request a corrected candidate.",
            "consequence": "No candidate executes yet.",
            "applies_to": "task_class",
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


@pytest.mark.parametrize(
    ("operation", "target"),
    [
        ("approve", {"process_instance_id": "P-101", "task_id": "801"}),
        ("reject", {"process_instance_id": "P-101", "task_id": "801"}),
        ("revert_task", {"process_instance_id": "P-101", "task_id": "801", "target_activity_id": "activity-1"}),
        ("redirect_task", {"process_instance_id": "P-101", "task_id": "801", "to_actioner_id": "U-2"}),
        ("comment", {"process_instance_id": "P-101"}),
    ],
)
def test_registered_oa_action_target_identifiers_are_nonempty_strings(operation, target):
    action = {
        **_proposal()["actions"][0],
        "capability": "dingtalk-oa",
        "operation": operation,
        "target": target,
        "payload": {},
    }
    schema = ProposedAction.model_json_schema()
    validator = Draft202012Validator(schema)
    validator.validate(action)
    ProposedAction.model_validate(action)
    for field in target:
        for wrong in (801, "", "  "):
            invalid = {**action, "target": {**target, field: wrong}}
            with pytest.raises(JsonSchemaValidationError):
                validator.validate(invalid)
            with pytest.raises(ValidationError, match=field):
                ProposedAction.model_validate(invalid)
        missing_target = dict(target)
        missing_target.pop(field)
        invalid = {**action, "target": missing_target}
        with pytest.raises(JsonSchemaValidationError):
            validator.validate(invalid)
        with pytest.raises(ValidationError):
            ProposedAction.model_validate(invalid)


def test_consumer_wire_schema_rejects_numeric_oa_task_id_before_review():
    proposal = _proposal()
    proposal["actions"][0].update(
        capability="dingtalk-oa", operation="approve",
        target={"process_instance_id": "P-101", "task_id": "801"},
        payload={"remark": "Receipt and budget verified."},
    )
    payload = _consumer_wire_payload(outcome="proposal", proposal=proposal)
    validator = Draft202012Validator(ConsumerAgentWireResult.model_json_schema())
    validator.validate(payload)
    ConsumerAgentWireResult.model_validate(payload)
    proposal["actions"][0]["target"]["task_id"] = 801
    with pytest.raises(JsonSchemaValidationError):
        validator.validate(payload)
    with pytest.raises(ValidationError, match="task_id"):
        ConsumerAgentWireResult.model_validate(payload)


def test_non_oa_action_targets_keep_generic_json_identifier_types():
    action = {**_proposal()["actions"][0], "target": {"task_id": 801}}
    Draft202012Validator(ProposedAction.model_json_schema()).validate(action)
    assert ProposedAction.model_validate(action).target["task_id"] == 801


def test_document_create_requires_body_in_payload_not_description():
    action = {
        "description": "# Daily report\n" * 300,
        "action_identity": "report-doc",
        "capability": "dingtalk-doc",
        "operation": "create_document",
        "target": {"folder_id": "folder-1"},
        "payload": {"content_format": "markdown"},
    }
    with pytest.raises(ValidationError, match="payload.content"):
        ProposedAction.model_validate(action)

    action["description"] = "Create the daily report"
    action["payload"]["content"] = "# Daily report\n" * 300
    assert ProposedAction.model_validate(action).payload["content"].startswith("# Daily")


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
        assert schema["oneOf"]
        serialized = json.dumps(schema, ensure_ascii=False)
        for business_action in (
            "send_dingtalk_reply",
            "oa_approval",
            "send_mail",
            "edit_document",
        ):
            assert business_action not in serialized


@pytest.mark.parametrize("outcome", ["proposal", "no_action", "failed"])
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("needs_human_reason", "Ask Derek to decide."),
        ("decision_basis", _decision_basis()),
        ("requested_input", "Please supply the missing fact."),
        ("decision_options", [{
            "key": "stop", "label": "Stop", "instruction": "Stop this item.",
            "consequence": "The item is skipped.", "plan": None,
            "terminal_outcome": "skipped", "reason": "No action is appropriate.",
        }]),
    ],
)
def test_non_human_consumer_schemas_reject_human_decision_fields(outcome, field, value):
    proposal = _proposal() if outcome == "proposal" else None
    contract_payload = {
        "outcome": outcome,
        "summary": "The current outcome is complete.",
        "proposal": proposal,
        "decision_options": [],
        "error": {**_error(), "stage": "", "source": "", "source_code": "", "session_continuable": False},
        "risk": "low",
        "confidence": 1.0,
        "rule_coverage": 1.0,
        "information_completeness": 1.0,
    }
    wire_payload = _consumer_wire_payload(outcome=outcome, proposal=proposal)
    for schema, payload in (
        (ConsumerAgentResult.model_json_schema(), contract_payload),
        (ConsumerAgentWireResult.model_json_schema(), wire_payload),
    ):
        validator = Draft202012Validator(schema)
        validator.validate(payload)
        validator.validate({**payload, "needs_human_reason": None, "decision_basis": None, "requested_input": None})
        with pytest.raises(JsonSchemaValidationError):
            validator.validate({**payload, field: value})
    with pytest.raises(ValidationError):
        ConsumerAgentResult.model_validate({**contract_payload, field: value})
    with pytest.raises(ValidationError):
        ConsumerAgentWireResult.model_validate({**wire_payload, field: value})


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


def test_parse_typed_agent_result_reports_unclosed_json_as_invalid():
    raw = json.dumps(
        {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": '{"outcome":"executed"'},
        }
    )
    with pytest.raises(ResultParseError, match="invalid JSON") as info:
        parse_typed_agent_result(raw, AuditAgentResult)
    assert "unbalanced" in str(info.value.__cause__)


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


def test_consumer_wire_result_normalizes_dependency_read_failure_as_retryable():
    result = ConsumerAgentWireResult.model_validate(
        _consumer_wire_payload(
            outcome="failed",
            error_code="dependency_read_unavailable",
            error_retryable=False,
        )
    ).to_result()

    assert result.error.retryable is True


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


def test_a_direct_message_to_a_user_id_is_executable() -> None:
    """The CEO daily report messages Derek by user id (Derek 2026-09-24)."""
    from app.consumer_agent import structured_dingtalk_outgoing_text_key

    action = ProposedAction(
        action_identity="ceo_daily_report_notice_2026-09-25",
        capability="dingtalk-chat",
        operation="send_direct_message",
        description="把日报要点和链接单聊发给磊哥",
        target={"user_id": "derek-user"},
        payload={"content": "CEO 每日总结 2026-09-25 已发布"},
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


def test_current_instance_option_requires_exactly_one_branch():
    from app.agent_contracts import DecisionOption

    common = dict(key="go", label="Proceed", instruction="Send notice", consequence="Recipient gets notice")
    assert DecisionOption(**common, plan=_proposal()).plan is not None
    with pytest.raises(ValidationError):
        DecisionOption(**common)
    with pytest.raises(ValidationError):
        DecisionOption(**common, plan=_proposal(), terminal_outcome="skipped", reason="Stop")
    with pytest.raises(ValidationError):
        DecisionOption(**common, terminal_outcome="skipped", reason="")
    with pytest.raises(ValidationError):
        DecisionOption(**common, applies_to="task_class", plan=_proposal())


def test_live_consumer_wire_rejects_old_authorization_plan():
    payload = {
        "outcome": "needs_human", "summary": "Need source fact", "proposal": None,
        "decision_options": [], "requested_input": "Provide invoice number",
        "needs_human_reason": "Only Derek has it", "decision_basis": _decision_basis(),
        "risk": "low", "confidence": 1.0, "rule_coverage": 1.0,
        "information_completeness": 1.0, "error_code": "",
        "error_retryable": False, "error_authorization_required": False,
        "durable_memories": [], "authorization_plan": _authorization_plan(),
    }
    with pytest.raises(ValidationError, match="authorization_plan"):
        ConsumerAgentWireResult.model_validate(payload)


def test_open_ended_human_input_has_no_false_options_or_quality_gate():
    payload = {
        "outcome": "needs_human", "summary": "Need an unknown invoice number",
        "proposal": None, "decision_options": [],
        "requested_input": "What is the invoice number?",
        "needs_human_reason": "Only Derek has the receipt",
        "decision_basis": _decision_basis(),
        "risk": "low", "confidence": 1.0, "rule_coverage": 1.0,
        "information_completeness": 1.0, "error": _error(),
    }
    assert ConsumerAgentResult.model_validate(payload).requested_input
    with pytest.raises(ValidationError):
        ConsumerAgentResult.model_validate({**payload, "error": {**_error(), "code": "tool_failed"}})
    with pytest.raises(ValidationError):
        ConsumerAgentResult.model_validate({**payload, "proposal": _proposal()})


def test_stage_requires_predecessor_and_immediate_plan_excludes_question():
    base = {
        "outcome": "proposal", "summary": "Notify", "proposal": _proposal(),
        "risk": "low", "confidence": 1.0, "rule_coverage": 1.0,
        "information_completeness": 1.0, "error": _error(),
    }
    assert ConsumerAgentResult.model_validate({**base, "continue_after_execution": True}).stage_index == 0
    with pytest.raises(ValidationError):
        ConsumerAgentResult.model_validate({**base, "stage_index": 1})
    assert ConsumerAgentResult.model_validate({**base, "stage_index": 1, "predecessor_review_id": 3}).stage_index == 1
    with pytest.raises(ValidationError):
        ConsumerAgentResult.model_validate({**base, "decision_options": [] , "requested_input": "Choose"})


def test_audit_review_only_and_system_execution_result():
    from app.agent_contracts import SystemExecutionResult

    base = {
        "outcome": "approve", "summary": "Complete candidate is sound",
        "proposal_revision": 0, "candidate_digest": "a" * 64,
        "evidence_refs": ["message:1"], "feedback": None,
        "risk": "low", "confidence": 1.0, "rule_coverage": 1.0,
        "information_completeness": 1.0, "error": _error(),
    }
    assert AuditAgentResult.model_validate(base).outcome is AuditOutcome.APPROVE
    with pytest.raises(ValidationError):
        AuditAgentResult.model_validate({**base, "external_result": {"operation_id": "x", "live_result_reference": {}}})
    with pytest.raises(ValidationError):
        AuditAgentResult.model_validate({**base, "outcome": "return"})
    with pytest.raises(ValidationError):
        AuditAgentResult.model_validate({**base, "outcome": "executed"})
    execution = SystemExecutionResult(outcome="executed", summary="Sent", external_result={"operation_id": "x", "live_result_reference": {}}, completed_action_keys=("send",))
    assert execution.completed_action_keys == ("send",)


@pytest.mark.parametrize("old_outcome", ["executed", "feedback_provided", "needs_human", "dry_run", "revision_required"])
def test_live_audit_wire_rejects_old_execution_outcomes(old_outcome):
    payload = {
        "outcome": old_outcome, "summary": "Old result", "proposal_revision": 0,
        "candidate_digest": "a" * 64, "feedback": None,
        "risk": "low", "confidence": 1.0, "rule_coverage": 1.0,
        "information_completeness": 1.0, "error_code": "",
        "error_retryable": False, "error_authorization_required": False,
    }
    with pytest.raises(ValidationError):
        AuditAgentWireResult.model_validate(payload)


@pytest.mark.parametrize("outcome", ["return", "reject"])
def test_review_feedback_required_on_return_and_reject(outcome):
    payload = {
        "outcome": outcome, "summary": "Revise candidate", "proposal_revision": 0,
        "candidate_digest": "a" * 64, "evidence_refs": ["message:1"],
        "feedback": {"rule": "Audience must match source", "observation": "Wrong recipient", "requested_revision": "Correct recipient"},
        "risk": "low", "confidence": 1.0, "rule_coverage": 1.0,
        "information_completeness": 1.0, "error_code": "",
        "error_retryable": False, "error_authorization_required": False,
    }
    assert AuditAgentWireResult.model_validate(payload).to_result().outcome.value == outcome
    with pytest.raises(ValidationError):
        AuditAgentWireResult.model_validate({**payload, "feedback": None})


def test_pinned_consumer_schema_rejects_unbound_human_question_and_option():
    from app.agent_result import AgentError

    schema = json.loads((SCHEMA_DIR / "consumer_agent_result.schema.json").read_text())
    base = {
        "outcome": "needs_human", "summary": "Need decision", "proposal": None,
        "decision_options": [], "needs_human_reason": "Missing current fact",
        "decision_basis": _decision_basis(), "risk": "low", "confidence": 1.0,
        "rule_coverage": 1.0, "information_completeness": 1.0,
        "error": AgentError.model_validate(_error()).model_dump(mode="json"),
    }
    with pytest.raises(JsonSchemaValidationError):
        Draft202012Validator(schema).validate(base)
    valid = {**base, "requested_input": "What is the invoice number?"}
    Draft202012Validator(schema).validate(valid)
    invalid_option = {**base, "decision_options": [{
        "key": "stop", "label": "Stop", "instruction": "Stop now",
        "consequence": "No action", "terminal_outcome": "skipped",
    }]}
    with pytest.raises(JsonSchemaValidationError):
        Draft202012Validator(schema).validate(invalid_option)
