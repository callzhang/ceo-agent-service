import json
from copy import deepcopy

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic.fields import FieldInfo

from app.agent_contracts import (
    AuditExternalResult,
    AuditFeedback,
    AuditAgentResult,
    AuditReconciliation,
    AuditOutcome,
    ConsumerAgentResult,
    ConsumerOutcome,
    ConsumerProposal,
    DecisionOption,
)
from app.agent_result import (
    ResultParseError,
    SideEffectState,
    parse_typed_agent_result,
)


def _inline_schema_refs(schema: dict[str, object]) -> dict[str, object]:
    definitions = schema.get("$defs")
    if not isinstance(definitions, dict):
        return deepcopy(schema)

    def resolve(value: object, stack: frozenset[str] = frozenset()) -> object:
        if isinstance(value, list):
            return [resolve(item, stack) for item in value]
        if not isinstance(value, dict):
            return value
        reference = value.get("$ref")
        if isinstance(reference, str) and reference.startswith("#/$defs/"):
            name = reference.removeprefix("#/$defs/")
            if name in stack:
                return {}
            target = definitions.get(name)
            if isinstance(target, dict):
                return resolve(target, stack | {name})
        return {
            key: resolve(item, stack)
            for key, item in value.items()
            if key != "$defs"
        }

    resolved = resolve(schema)
    if not isinstance(resolved, dict):
        raise TypeError("content schema must resolve to an object")
    return resolved


def _content_schema(value_type: object, schema_id: str) -> dict[str, object]:
    schema = _inline_schema_refs(TypeAdapter(value_type).json_schema())
    schema["$id"] = f"urn:ceo-agent:{schema_id}"
    return schema


_PROPOSAL_CONTENT_SCHEMA = _content_schema(ConsumerProposal, "consumer-proposal")
_DECISION_OPTIONS_CONTENT_SCHEMA = _content_schema(
    list[DecisionOption],
    "consumer-decision-options",
)
_NEEDS_HUMAN_OPTIONS_CONTENT_SCHEMA = {
    **deepcopy(_DECISION_OPTIONS_CONTENT_SCHEMA),
    "minItems": 2,
    "maxItems": 4,
}
_EMPTY_ARRAY_CONTENT_SCHEMA: dict[str, object] = {
    "$id": "urn:ceo-agent:empty-array",
    "type": "array",
    "maxItems": 0,
}
_AUDIT_FEEDBACK_CONTENT_SCHEMA = _content_schema(AuditFeedback, "audit-feedback")
_AUDIT_EXTERNAL_RESULT_CONTENT_SCHEMA = _content_schema(
    AuditExternalResult,
    "audit-external-result",
)
_AUDIT_RECONCILIATION_CONTENT_SCHEMA = _content_schema(
    list[AuditReconciliation],
    "audit-reconciliation",
)


def _json_string_contract(content_schema: dict[str, object]) -> dict[str, object]:
    return {
        "type": "string",
        "minLength": 1,
        "contentMediaType": "application/json",
        "contentSchema": deepcopy(content_schema),
    }


def _json_string_field(
    *,
    description: str,
    content_schema: dict[str, object],
) -> FieldInfo:
    return Field(
        min_length=1,
        description=description,
        json_schema_extra={
            "contentMediaType": "application/json",
            "contentSchema": deepcopy(content_schema),
        },
    )


def _consumer_wire_json_schema(schema: dict[str, object]) -> None:
    null_value = {"type": "null"}
    empty_options = _json_string_contract(_EMPTY_ARRAY_CONTENT_SCHEMA)
    schema["anyOf"] = [
        {
            "properties": {
                "outcome": {"const": "proposal"},
                "proposal_json": _json_string_contract(_PROPOSAL_CONTENT_SCHEMA),
                "decision_options_json": empty_options,
            }
        },
        {
            "properties": {
                "outcome": {"const": "needs_human"},
                "proposal_json": null_value,
                "decision_options_json": _json_string_contract(
                    _NEEDS_HUMAN_OPTIONS_CONTENT_SCHEMA
                ),
            }
        },
        {
            "properties": {
                "outcome": {"enum": ["no_action", "failed"]},
                "proposal_json": null_value,
                "decision_options_json": empty_options,
            }
        },
    ]


def _audit_wire_json_schema(schema: dict[str, object]) -> None:
    null_value = {"type": "null"}
    empty_reconciliation = _json_string_contract(_EMPTY_ARRAY_CONTENT_SCHEMA)
    schema["anyOf"] = [
        {
            "properties": {
                "outcome": {"const": "executed"},
                "side_effect_state": {"const": "confirmed"},
                "feedback_json": null_value,
                "external_result_json": _json_string_contract(
                    _AUDIT_EXTERNAL_RESULT_CONTENT_SCHEMA
                ),
                "reconciliation_json": empty_reconciliation,
            }
        },
        {
            "properties": {
                "outcome": {"const": "revision_required"},
                "side_effect_state": {"const": "none"},
                "feedback_json": _json_string_contract(
                    _AUDIT_FEEDBACK_CONTENT_SCHEMA
                ),
                "external_result_json": null_value,
                "reconciliation_json": empty_reconciliation,
            }
        },
        {
            "properties": {
                "outcome": {"const": "unknown"},
                "side_effect_state": {"const": "unknown"},
                "feedback_json": null_value,
                "external_result_json": null_value,
                "reconciliation_json": empty_reconciliation,
            }
        },
        {
            "properties": {
                "outcome": {"const": "reconciled"},
                "side_effect_state": {"const": "unknown"},
                "feedback_json": null_value,
                "external_result_json": null_value,
                "reconciliation_json": _json_string_contract(
                    _AUDIT_RECONCILIATION_CONTENT_SCHEMA
                ),
            }
        },
        {
            "properties": {
                "outcome": {"enum": ["needs_human", "failed"]},
                "side_effect_state": {"const": "none"},
                "feedback_json": null_value,
                "external_result_json": null_value,
                "reconciliation_json": empty_reconciliation,
            }
        },
    ]


class ConsumerAgentWireResult(BaseModel):
    """Strict-output transport form; nested dynamic objects remain JSON strings."""

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        json_schema_extra=_consumer_wire_json_schema,
    )

    outcome: ConsumerOutcome
    summary: str = Field(min_length=1)
    proposal_json: str | None = _json_string_field(
        description="JSON-encoded ConsumerProposal, or null outside proposal outcome.",
        content_schema=_PROPOSAL_CONTENT_SCHEMA,
    )
    decision_options_json: str = _json_string_field(
        description="JSON-encoded array of DecisionOption objects.",
        content_schema=_DECISION_OPTIONS_CONTENT_SCHEMA,
    )
    error_code: str
    error_retryable: bool
    error_authorization_required: bool

    @field_validator("outcome", mode="before")
    @classmethod
    def accept_json_outcome(cls, value: object) -> object:
        return ConsumerOutcome(value) if isinstance(value, str) else value

    @model_validator(mode="after")
    def validate_wire_payload(self) -> "ConsumerAgentWireResult":
        self.to_result()
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

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        json_schema_extra=_audit_wire_json_schema,
    )

    outcome: AuditOutcome
    summary: str = Field(min_length=1)
    proposal_revision: int = Field(ge=0)
    side_effect_state: SideEffectState
    feedback_json: str | None = _json_string_field(
        description="JSON-encoded AuditFeedback, or null outside revision_required.",
        content_schema=_AUDIT_FEEDBACK_CONTENT_SCHEMA,
    )
    external_result_json: str | None = _json_string_field(
        description="JSON-encoded AuditExternalResult, or null outside executed.",
        content_schema=_AUDIT_EXTERNAL_RESULT_CONTENT_SCHEMA,
    )
    reconciliation_json: str = _json_string_field(
        description="JSON-encoded array of AuditReconciliation entries.",
        content_schema=_AUDIT_RECONCILIATION_CONTENT_SCHEMA,
    )
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

    @model_validator(mode="after")
    def validate_wire_payload(self) -> "AuditAgentWireResult":
        self.to_result()
        return self

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
