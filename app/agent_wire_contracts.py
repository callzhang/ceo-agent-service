from typing import Annotated, Literal

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
    AuditExternalResult,
    AuditFeedback,
    AuthorizationPlan,
    ConsumerAgentResult,
    ConsumerProposal,
    DecisionBasis,
    DecisionOption,
    DurableMemory,
    RiskLevel,
)
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
    # Required so every turn has to consider it; an empty list is an answer.
    durable_memories: list[DurableMemory]


class _ConsumerProposalWire(_ConsumerWireBase):
    outcome: Literal["proposal"]
    proposal: ConsumerProposal
    # Empty, or an independent question for Derek that the proposed action
    # does not settle: 2-4 options with needs_human_reason and decision_basis.
    decision_options: list[DecisionOption] = Field(
        default_factory=list, max_length=4
    )
    needs_human_reason: str | None = None
    decision_basis: DecisionBasis | None = None


class _ConsumerNeedsHumanWire(_ConsumerWireBase):
    outcome: Literal["needs_human"]
    proposal: None
    decision_options: list[DecisionOption] = Field(
        min_length=2,
        max_length=4,
        json_schema_extra={"uniqueItems": True},
    )
    needs_human_reason: str = Field(min_length=1)
    decision_basis: DecisionBasis
    authorization_plan: AuthorizationPlan | None = None


class _ConsumerNoActionWire(_ConsumerWireBase):
    outcome: Literal["no_action"]
    proposal: None
    decision_options: list[DecisionOption] = Field(
        default_factory=list, max_length=0
    )


class _ConsumerFailedWire(_ConsumerWireBase):
    outcome: Literal["failed"]
    proposal: None
    decision_options: list[DecisionOption] = Field(
        default_factory=list, max_length=0
    )


ConsumerWirePayload = Annotated[
    _ConsumerProposalWire
    | _ConsumerNeedsHumanWire
    | _ConsumerNoActionWire
    | _ConsumerFailedWire,
    Field(discriminator="outcome"),
]


class ConsumerAgentWireResult(RootModel[ConsumerWirePayload]):
    """Strict discriminated transport contract for Consumer Agent A."""

    @model_validator(mode="after")
    def validate_result_conversion(self) -> "ConsumerAgentWireResult":
        self.to_result()
        return self

    def to_result(self) -> ConsumerAgentResult:
        payload = self.root
        return ConsumerAgentResult.model_validate(
            {
                "outcome": payload.outcome,
                "summary": payload.summary,
                "proposal": payload.proposal,
                "decision_options": payload.decision_options,
                "risk": payload.risk,
                "confidence": payload.confidence,
                "rule_coverage": payload.rule_coverage,
                "information_completeness": payload.information_completeness,
                "error": payload.error_payload(),
                "needs_human_reason": getattr(payload, "needs_human_reason", None),
                "decision_basis": getattr(payload, "decision_basis", None),
                "authorization_plan": getattr(payload, "authorization_plan", None),
                "durable_memories": tuple(payload.durable_memories),
            }
        )


class _AuditWireBase(_WireBase):
    proposal_revision: int = Field(ge=0)


class _AuditExecutedWire(_AuditWireBase):
    outcome: Literal["executed"]
    feedback: None
    external_result: AuditExternalResult
    decision_options: list[DecisionOption] = Field(default_factory=list, max_length=0)


class _AuditFeedbackProvidedWire(_AuditWireBase):
    # ``revision_required`` is the legacy wire spelling.  Keep accepting it
    # at the transport boundary; AuditAgentResult normalizes it to the
    # canonical ``feedback_provided`` outcome.
    outcome: Literal["feedback_provided", "revision_required"]
    feedback: AuditFeedback
    external_result: None
    decision_options: list[DecisionOption] = Field(default_factory=list, max_length=0)


class _AuditNeedsHumanWire(_AuditWireBase):
    outcome: Literal["needs_human"]
    feedback: None
    external_result: None
    decision_options: list[DecisionOption] = Field(
        min_length=2,
        max_length=4,
        json_schema_extra={"uniqueItems": True},
    )
    needs_human_reason: str = Field(min_length=1)
    decision_basis: DecisionBasis
    authorization_plan: AuthorizationPlan | None = None


class _AuditDryRunWire(_AuditWireBase):
    outcome: Literal["dry_run"]
    feedback: None
    external_result: None
    decision_options: list[DecisionOption] = Field(default_factory=list, max_length=0)


class _AuditFailedWire(_AuditWireBase):
    outcome: Literal["failed"]
    feedback: None
    external_result: None
    decision_options: list[DecisionOption] = Field(default_factory=list, max_length=0)


AuditWirePayload = Annotated[
    _AuditExecutedWire
    | _AuditFeedbackProvidedWire
    | _AuditNeedsHumanWire
    | _AuditDryRunWire
    | _AuditFailedWire
    ,
    Field(discriminator="outcome"),
]


class AuditAgentWireResult(RootModel[AuditWirePayload]):
    """Strict discriminated transport contract for Audit Agent B."""

    @model_validator(mode="after")
    def validate_result_conversion(self) -> "AuditAgentWireResult":
        self.to_result()
        return self

    def to_result(self) -> AuditAgentResult:
        payload = self.root
        return AuditAgentResult.model_validate(
            {
                "outcome": payload.outcome,
                "summary": payload.summary,
                "proposal_revision": payload.proposal_revision,
                "feedback": payload.feedback,
                "external_result": payload.external_result,
                "decision_options": payload.decision_options,
                "risk": payload.risk,
                "confidence": payload.confidence,
                "rule_coverage": payload.rule_coverage,
                "information_completeness": payload.information_completeness,
                "error": payload.error_payload(),
                "needs_human_reason": getattr(payload, "needs_human_reason", None),
                "decision_basis": getattr(payload, "decision_basis", None),
                "authorization_plan": getattr(payload, "authorization_plan", None),
            }
        )


def parse_consumer_agent_wire_result(raw: str) -> ConsumerAgentResult:
    try:
        return parse_typed_agent_result(raw, ConsumerAgentWireResult).to_result()
    except ResultParseError:
        raise
    except (ValidationError, ValueError) as exc:
        raise ResultParseError(
            "consumer wire result does not match the strict schema"
        ) from exc


def parse_audit_agent_wire_result(raw: str) -> AuditAgentResult:
    try:
        return parse_typed_agent_result(raw, AuditAgentWireResult).to_result()
    except ResultParseError:
        raise
    except (ValidationError, ValueError) as exc:
        raise ResultParseError(
            "audit wire result does not match the strict schema"
        ) from exc
