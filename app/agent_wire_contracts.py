import json

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from app.agent_contracts import (
    AuditAgentResult,
    AuditOutcome,
    ConsumerAgentResult,
    ConsumerOutcome,
)
from app.agent_result import (
    AgentError,
    ResultParseError,
    SideEffectState,
    parse_typed_agent_result,
)


class ConsumerAgentWireResult(BaseModel):
    """Strict-output transport form; nested dynamic objects remain JSON strings."""

    model_config = ConfigDict(extra="forbid", strict=True)

    outcome: ConsumerOutcome
    summary: str = Field(min_length=1)
    proposal_json: str | None
    decision_options_json: str
    error_code: str
    error_retryable: bool
    error_authorization_required: bool

    @field_validator("outcome", mode="before")
    @classmethod
    def accept_json_outcome(cls, value: object) -> object:
        return ConsumerOutcome(value) if isinstance(value, str) else value

    @model_validator(mode="after")
    def validate_wire_payload(self) -> "ConsumerAgentWireResult":
        if (self.outcome is ConsumerOutcome.PROPOSAL) != (self.proposal_json is not None):
            raise ValueError("proposal_json is required only for proposal outcome")
        return self

    def to_result(self) -> ConsumerAgentResult:
        proposal = _json_object(self.proposal_json, "proposal_json") if self.proposal_json else None
        return ConsumerAgentResult.model_validate(
            {
                "outcome": self.outcome,
                "summary": self.summary,
                "proposal": proposal,
                "decision_options": _json_array(
                    self.decision_options_json, "decision_options_json"
                ),
                "error": _error_payload(self),
            }
        )


class AuditAgentWireResult(BaseModel):
    """Strict-output transport form for Audit B dynamic result fields."""

    model_config = ConfigDict(extra="forbid", strict=True)

    outcome: AuditOutcome
    summary: str = Field(min_length=1)
    proposal_revision: int = Field(ge=0)
    side_effect_state: SideEffectState
    feedback_json: str | None
    external_result_json: str | None
    reconciliation_json: str
    error_code: str
    error_retryable: bool
    error_authorization_required: bool

    @field_validator("outcome", mode="before")
    @classmethod
    def accept_json_outcome(cls, value: object) -> object:
        return AuditOutcome(value) if isinstance(value, str) else value

    @field_validator("side_effect_state", mode="before")
    @classmethod
    def accept_json_side_effect_state(cls, value: object) -> object:
        return SideEffectState(value) if isinstance(value, str) else value

    def to_result(self) -> AuditAgentResult:
        return AuditAgentResult.model_validate(
            {
                "outcome": self.outcome,
                "summary": self.summary,
                "proposal_revision": self.proposal_revision,
                "side_effect_state": self.side_effect_state,
                "feedback": _json_object(self.feedback_json, "feedback_json")
                if self.feedback_json
                else None,
                "external_result": _json_object(
                    self.external_result_json, "external_result_json"
                )
                if self.external_result_json
                else None,
                "reconciliation": _json_array(
                    self.reconciliation_json, "reconciliation_json"
                ),
                "error": _error_payload(self),
            }
        )


def _error_payload(value: ConsumerAgentWireResult | AuditAgentWireResult) -> dict[str, object]:
    return {
        "code": value.error_code,
        "retryable": value.error_retryable,
        "authorization_required": value.error_authorization_required,
    }


def _json_object(value: str | None, field: str) -> dict[str, object]:
    parsed = _json_value(value, field)
    if not isinstance(parsed, dict):
        raise ValueError(f"{field} must contain a JSON object")
    return parsed


def _json_array(value: str, field: str) -> list[object]:
    parsed = _json_value(value, field)
    if not isinstance(parsed, list):
        raise ValueError(f"{field} must contain a JSON array")
    return parsed


def _json_value(value: str | None, field: str) -> object:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a JSON string")
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{field} is not valid JSON") from exc


def parse_consumer_agent_wire_result(raw: str) -> ConsumerAgentResult:
    try:
        return parse_typed_agent_result(raw, ConsumerAgentWireResult).to_result()
    except ResultParseError:
        raise
    except (ValidationError, ValueError) as exc:
        raise ResultParseError("consumer wire result does not match the strict schema") from exc


def parse_audit_agent_wire_result(raw: str) -> AuditAgentResult:
    try:
        return parse_typed_agent_result(raw, AuditAgentWireResult).to_result()
    except ResultParseError:
        raise
    except (ValidationError, ValueError) as exc:
        raise ResultParseError("audit wire result does not match the strict schema") from exc
