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
    # The array itself carries no prompt otherwise -- only its item schema
    # (DurableMemory) describes what a well-formed entry looks like, which
    # never prompted the model to look back over the turn and ask whether one
    # applies. Every completed Consumer run has come back with durable_memories
    # entirely empty since the field shipped (Derek 2026-09-28: "参考 hook 的
    # prompt"), so this description is written the same way the memory-connector
    # Stop hook prompts an active check, not a passive field description.
    durable_memories: list[DurableMemory] = Field(
        description=(
            "Before you finish, check whether this turn confirmed anything "
            "durable: a person's preference, a decision that was made, a "
            "reusable rule or convention, an accepted project rule, a stable "
            "business fact, long-term context, or the explicit next step of "
            "unfinished work. If it did, list each one as its own item here. "
            "Skip temporary tasks, logs, code, one-off errors, unconfirmed "
            "guesses, sensitive original text, secrets or tokens, unauthorized "
            "document content, and anything already in Memory. An empty list "
            "is the right answer when nothing durable came up this turn -- "
            "but check every turn, don't default to empty."
        )
    )


class _ConsumerWire(_ConsumerWireBase):
    outcome: Literal["proposal", "needs_human", "no_action", "failed"]
    proposal: ConsumerProposal | None
    decision_options: list[DecisionOption] = Field(default_factory=list, max_length=4)
    requested_input: str | None = None
    needs_human_reason: str | None = None
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

    def to_result(self) -> ConsumerAgentResult:
        payload = self.root
        return ConsumerAgentResult.model_validate({
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
            "durable_memories": tuple(payload.durable_memories),
        })


class _AuditWire(_WireBase):
    outcome: Literal["approve", "return", "reject", "failed"]
    proposal_revision: int = Field(ge=0)
    candidate_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_refs: list[str] = Field(default_factory=list)
    feedback: AuditFeedback | None


class AuditAgentWireResult(RootModel[_AuditWire]):
    """Strict read-only transport contract for Audit Agent B."""

    @model_validator(mode="after")
    def validate_result_conversion(self) -> "AuditAgentWireResult":
        self.to_result()
        return self

    def to_result(self) -> AuditAgentResult:
        payload = self.root
        return AuditAgentResult.model_validate({
            "outcome": payload.outcome,
            "summary": payload.summary,
            "proposal_revision": payload.proposal_revision,
            "candidate_digest": payload.candidate_digest,
            "evidence_refs": payload.evidence_refs,
            "feedback": payload.feedback,
            "risk": payload.risk,
            "confidence": payload.confidence,
            "rule_coverage": payload.rule_coverage,
            "information_completeness": payload.information_completeness,
            "error": payload.error_payload(),
        })


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
