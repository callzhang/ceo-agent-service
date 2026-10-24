from copy import deepcopy
import json
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
    ValidationError,
    model_validator,
    field_validator,
)

from app.agent_contracts import (
    AuditAgentResult,
    AuditFeedback,
    CandidateFieldAssertion,
    ConsumerAgentResult,
    DINGTALK_MESSAGE_IDENTIFIER_FIELDS,
    DecisionBasis,
    DecisionOption,
    RiskLevel,
    _consumer_result_json_schema,
)
from app.consumer_purpose import ConsumerPurposeOutput, _purpose_schema
from app.agent_reported_error import agent_error_payload
from app.agent_result import ResultParseError, parse_typed_agent_result


class _WireBase(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    summary: str = Field(min_length=1)
    # JSON's native "no error" value is null; the application contract carries
    # the empty string, so both spellings map to the same AgentError.
    error_code: str | None
    error_retryable: bool
    error_authorization_required: bool
    risk: RiskLevel
    confidence: float = Field(ge=0.0, le=1.0)
    rule_coverage: float = Field(ge=0.0, le=1.0)
    information_completeness: float = Field(ge=0.0, le=1.0)

    @field_validator("risk", mode="before")
    @classmethod
    def accept_json_risk(cls, value: object) -> object:
        return RiskLevel(value) if isinstance(value, str) else value

    def error_payload(self) -> dict[str, object]:
        # The service decides what a reported code means; error_retryable and
        # error_authorization_required are not read. See agent_reported_error.
        return agent_error_payload(
            self.error_code, failed=getattr(self, "outcome", "") == "failed"
        )


class _ConsumerWireBase(_WireBase):
    error_code: str | None = None
    error_retryable: bool = False
    error_authorization_required: bool = False


class _ConsumerWire(_ConsumerWireBase, ConsumerPurposeOutput):
    @model_validator(mode="after")
    def validate_retry_error_fields(self):
        if self.action == "retry" and (
            self.error_code not in (None, "") or self.error_retryable or self.error_authorization_required
        ):
            raise ValueError("purpose retry is a waiting action; technical error fields must be empty")
        return self

    @property
    def outcome(self):
        if self.action in {"retry", "needs_human", "no_action", "failed"}:
            return self.action
        return "proposal"

    @property
    def proposal(self):
        return self.to_proposal()

    decision_options: list[DecisionOption] = Field(default_factory=list, max_length=4)
    requested_input: str | None = None
    needs_human_reason: str | None = Field(default=None, max_length=100)
    decision_basis: DecisionBasis | None = None
    stage_index: int = Field(default=0, ge=0)
    predecessor_review_id: int | None = Field(default=None, ge=1)
    continue_after_execution: bool = False


class ConsumerAgentWireResult(RootModel[_ConsumerWire]):
    """Strict transport contract for Consumer Agent A."""

    @model_validator(mode="after")
    def validate_result_conversion(self) -> "ConsumerAgentWireResult":
        self.to_result()
        return self

    @classmethod
    def model_json_schema(cls, **kwargs):
        schema = super().model_json_schema(**kwargs)
        wire = schema["$defs"][cls.model_fields["root"].annotation.__name__]
        _purpose_schema(wire, cls.model_fields["root"].annotation)
        constraints = {}
        _consumer_result_json_schema(constraints)
        branches = constraints["oneOf"]
        for branch in branches:
            properties = branch["properties"]
            outcome = properties.pop("outcome")
            properties.pop("proposal", None)
            if outcome.get("const") == "proposal":
                outcome = {"enum": [name for name in wire["properties"]["action"]["enum"]
                    if name not in {"retry", "no_action", "failed", "needs_human"}]}
            properties["action"] = outcome
        wire["allOf"].append({"oneOf": branches})
        wire["allOf"].append({
            "if": {"properties": {"action": {"const": "retry"}}, "required": ["action"]},
            "then": {"properties": {
                "error_code": {"enum": [None, ""]},
                "error_retryable": {"const": False},
                "error_authorization_required": {"const": False},
            }},
        })
        schema["$defs"].update(wire.pop("$defs", {}))
        return schema

    def to_result(self) -> ConsumerAgentResult:
        payload = self.root
        result = ConsumerAgentResult.model_validate({
            "outcome": payload.outcome,
            "summary": payload.summary,
            "proposal": payload.proposal,
            "decision_options": payload.decision_options,
            "requested_input": payload.requested_input,
            "needs_human_reason": payload.needs_human_reason,
            "decision_basis": payload.decision_basis,
            "stage_index": payload.stage_index,
            "predecessor_review_id": payload.predecessor_review_id,
            "continue_after_execution": payload.continue_after_execution,
            "risk": payload.risk,
            "confidence": payload.confidence,
            "rule_coverage": payload.rule_coverage,
            "information_completeness": payload.information_completeness,
            "error": payload.error_payload(),
            "purpose_output": {key: getattr(payload, key) for key in
                ("action", "reason", "target", "payload", "evidence_refs") } | {"purpose": payload.purpose},
        })
        plans = (result.proposal, *(option.plan for option in result.decision_options))
        for plan in plans:
            if plan is None:
                continue
            for action in plan.actions:
                if action.capability != "dingtalk-chat":
                    continue
                for field in DINGTALK_MESSAGE_IDENTIFIER_FIELDS:
                    value = action.target.get(field)
                    if value is not None and (not isinstance(value, str) or not value.strip()):
                        raise ValueError(f"dingtalk-chat target.{field} must be a nonempty string or null")
        return result


class _OAConsumerWire(_ConsumerWire):
    purpose = "oa"


class OAConsumerAgentWireResult(ConsumerAgentWireResult):
    root: _OAConsumerWire


def consumer_wire_model(purpose="general"):
    if purpose == "oa":
        return OAConsumerAgentWireResult
    if purpose == "general":
        return ConsumerAgentWireResult
    raise ValueError(f"unknown Consumer purpose: {purpose}")


def claude_consumer_output_schema(purpose="general") -> dict[str, object]:
    """Native Claude subset; the same purpose parser enforces every branch."""
    schema = deepcopy(consumer_wire_model(purpose).model_json_schema())
    definitions = schema.pop("$defs")
    wire = definitions[schema.pop("$ref").rsplit("/", 1)[1]]
    native = {**wire, "$defs": definitions}
    def project(value):
        if isinstance(value, dict):
            for key in ("oneOf", "allOf", "if", "then", "else"):
                value.pop(key, None)
            for child in value.values():
                project(child)
        elif isinstance(value, list):
            for child in value:
                project(child)
    project(native)
    return native


class _AuditFeedbackWire(AuditFeedback):
    observation_basis: Literal["business", "candidate_fields"] = Field(
        description=AuditFeedback.model_fields["observation_basis"].description)
    candidate_field_assertions: tuple[CandidateFieldAssertion, ...] = Field(
        description=AuditFeedback.model_fields["candidate_field_assertions"].description)


class _AuditWire(_WireBase):
    outcome: Literal["approve", "return", "reject", "failed"]
    evidence_refs: list[str] = Field(default_factory=list)
    feedback: _AuditFeedbackWire | None = None


class AuditAgentWireResult(RootModel[_AuditWire]):
    """Strict read-only transport contract for Audit Agent B."""

    @model_validator(mode="after")
    def validate_result_conversion(self) -> "AuditAgentWireResult":
        self.to_result(proposal_revision=0, candidate_digest="0" * 64)
        return self

    def to_result(self, *, proposal_revision: int, candidate_digest: str) -> AuditAgentResult:
        payload = self.root
        return AuditAgentResult.model_validate({
            "outcome": payload.outcome,
            "summary": payload.summary,
            "proposal_revision": proposal_revision,
            "candidate_digest": candidate_digest,
            "evidence_refs": payload.evidence_refs,
            "feedback": payload.feedback,
            "risk": payload.risk,
            "confidence": payload.confidence,
            "rule_coverage": payload.rule_coverage,
            "information_completeness": payload.information_completeness,
            "error": payload.error_payload(),
        })


def parse_consumer_agent_wire_result(
    raw: str,
    *,
    purpose="general",
    stage_index: int | None = None,
    predecessor_review_id: int | None = None,
) -> ConsumerAgentResult:
    try:
        transform_candidate = None
        if stage_index is not None:
            def bind_stage_lineage(candidate: str) -> str:
                payload = json.loads(candidate)
                payload["stage_index"] = stage_index
                payload["predecessor_review_id"] = predecessor_review_id
                return json.dumps(payload)

            transform_candidate = bind_stage_lineage
        return parse_typed_agent_result(
            raw,
            consumer_wire_model(purpose),
            transform_candidate=transform_candidate,
        ).to_result()
    except ResultParseError:
        raise
    except (ValidationError, ValueError) as exc:
        raise ResultParseError("consumer wire result does not match the strict schema") from exc


def parse_audit_agent_wire_result(
    raw: str, *, proposal_revision: int, candidate_digest: str,
) -> AuditAgentResult:
    try:
        return parse_typed_agent_result(raw, AuditAgentWireResult).to_result(
            proposal_revision=proposal_revision,
            candidate_digest=candidate_digest,
        )
    except ResultParseError:
        raise
    except (ValidationError, ValueError) as exc:
        raise ResultParseError("audit wire result does not match the strict schema") from exc
