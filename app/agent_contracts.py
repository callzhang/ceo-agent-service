from enum import StrEnum

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)

from app.agent_result import AgentError, SideEffectState


def _consumer_result_json_schema(schema: dict[str, object]) -> None:
    schema["anyOf"] = [
        {
            "type": "object",
            "properties": {
                "outcome": {"const": "proposal"},
                "proposal": {"type": "object"},
            }
        },
        {
            "type": "object",
            "properties": {
                "outcome": {
                    "enum": ["no_action", "needs_human", "failed"],
                },
                "proposal": {"type": "null"},
            }
        },
    ]


def _audit_result_json_schema(schema: dict[str, object]) -> None:
    null_value = {"type": "null"}
    schema["anyOf"] = [
        {
            "type": "object",
            "properties": {
                "outcome": {"const": "executed"},
                "side_effect_state": {"const": "confirmed"},
                "feedback": null_value,
                "external_result": {"type": "object"},
            }
        },
        {
            "type": "object",
            "properties": {
                "outcome": {"const": "feedback_provided"},
                "side_effect_state": {"const": "none"},
                "feedback": {"type": "object"},
                "external_result": null_value,
            }
        },
        {
            "type": "object",
            "properties": {
                "outcome": {"const": "failed"},
                "side_effect_state": {"const": "none"},
                "feedback": null_value,
                "external_result": null_value,
            }
        },
        {
            "type": "object",
            "properties": {
                "outcome": {"const": "needs_human"},
                "side_effect_state": {"const": "none"},
                "feedback": null_value,
                "external_result": null_value,
            }
        },
        {
            "type": "object",
            "properties": {
                "outcome": {"const": "dry_run"},
                "side_effect_state": {"const": "none"},
                "feedback": null_value,
                "external_result": null_value,
            }
        },
    ]


class ConsumerOutcome(StrEnum):
    PROPOSAL = "proposal"
    NO_ACTION = "no_action"
    NEEDS_HUMAN = "needs_human"  # legacy wire name; semantically a policy gap
    FAILED = "failed"


class ProposedAction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    description: str = Field(min_length=1)
    capability: str = Field(min_length=1)
    operation: str = Field(min_length=1)
    target: dict[str, JsonValue] = Field(min_length=1)
    payload: dict[str, JsonValue]
    expected_verification: str = Field(min_length=1)



class ProposalFact(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    assertion: str = Field(min_length=1)
    references: tuple[str, ...] = Field(min_length=1)

    @field_validator("references", mode="before")
    @classmethod
    def accept_json_array(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value


class ConsumerProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    objective: str = Field(min_length=1)
    actions: tuple[ProposedAction, ...] = Field(min_length=1)
    sourced_facts: tuple[ProposalFact, ...]
    authored_judgment: str

    @field_validator("actions", "sourced_facts", mode="before")
    @classmethod
    def accept_json_arrays(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value


class DecisionOption(BaseModel):
    """One actionable, mutually exclusive instruction for a real management choice."""

    model_config = ConfigDict(extra="forbid", strict=True)

    key: str = Field(min_length=1)
    label: str = Field(min_length=1)
    instruction: str = Field(min_length=1)
    consequence: str = Field(min_length=1)


class ConsumerAgentResult(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        json_schema_extra=_consumer_result_json_schema,
    )

    outcome: ConsumerOutcome
    summary: str = Field(min_length=1)
    proposal: ConsumerProposal | None
    decision_options: tuple[DecisionOption, ...] = ()
    error: AgentError

    @field_validator("outcome", mode="before")
    @classmethod
    def accept_json_outcome(cls, value: object) -> object:
        return ConsumerOutcome(value) if isinstance(value, str) else value

    @field_validator("decision_options", mode="before")
    @classmethod
    def accept_json_decision_options(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def validate_payload(self) -> "ConsumerAgentResult":
        if (self.outcome is ConsumerOutcome.PROPOSAL) != (self.proposal is not None):
            raise ValueError("proposal is required only for proposal outcome")
        if self.outcome is ConsumerOutcome.NEEDS_HUMAN:
            if not 2 <= len(self.decision_options) <= 4:
                raise ValueError("needs_human requires two to four decision options")
            keys = [option.key for option in self.decision_options]
            if len(keys) != len(set(keys)):
                raise ValueError("decision option keys must be unique")
        elif self.decision_options:
            raise ValueError("decision options are only valid for needs_human")
        return self


class AuditOutcome(StrEnum):
    EXECUTED = "executed"
    FEEDBACK_PROVIDED = "feedback_provided"
    NEEDS_HUMAN = "needs_human"
    DRY_RUN = "dry_run"
    FAILED = "failed"


class ReconciliationDisposition(StrEnum):
    PRESENT = "present"
    ABSENT = "absent"
    SUPERSEDED = "superseded"
    AMBIGUOUS = "ambiguous"


class AuditReconciliation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    action_index: int = Field(ge=0)
    disposition: ReconciliationDisposition
    read_result_digest: str = Field(min_length=1)

    @field_validator("disposition", mode="before")
    @classmethod
    def accept_json_disposition(cls, value: object) -> object:
        return ReconciliationDisposition(value) if isinstance(value, str) else value


class AuditFeedback(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    rule: str = Field(min_length=1)
    observation: str = Field(min_length=1)
    requested_revision: str = Field(min_length=1)


class AuditExternalResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    operation_id: str = Field(min_length=1)
    verification_summary: str = Field(min_length=1)
    live_result_reference: dict[str, JsonValue]


class AuditAgentResult(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        json_schema_extra=_audit_result_json_schema,
    )

    outcome: AuditOutcome
    summary: str = Field(min_length=1)
    proposal_revision: int = Field(ge=0)
    side_effect_state: SideEffectState
    feedback: AuditFeedback | None
    external_result: AuditExternalResult | None
    reconciliation: tuple[AuditReconciliation, ...] = ()
    decision_options: tuple[DecisionOption, ...] = ()
    error: AgentError

    @field_validator("outcome", mode="before")
    @classmethod
    def accept_json_outcome(cls, value: object) -> object:
        return AuditOutcome(value) if isinstance(value, str) else value

    @field_validator("side_effect_state", mode="before")
    @classmethod
    def accept_json_side_effect_state(cls, value: object) -> object:
        return SideEffectState(value) if isinstance(value, str) else value

    @field_validator("reconciliation", "decision_options", mode="before")
    @classmethod
    def accept_json_arrays(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def validate_outcome_payload(self) -> "AuditAgentResult":
        indexes = [entry.action_index for entry in self.reconciliation]
        if len(indexes) != len(set(indexes)):
            raise ValueError("reconciliation action indexes must be unique")
        if self.outcome is AuditOutcome.FEEDBACK_PROVIDED:
            if self.feedback is None or self.external_result is not None:
                raise ValueError("feedback_provided needs feedback and no result")
        elif self.feedback is not None:
            raise ValueError("feedback is only valid for feedback_provided")
        if self.outcome is AuditOutcome.EXECUTED:
            if (
                self.external_result is None
                or self.side_effect_state is not SideEffectState.CONFIRMED
            ):
                raise ValueError("executed needs confirmed external result")
        elif self.external_result is not None:
            raise ValueError("external result is only valid for executed")
        if self.outcome is not AuditOutcome.EXECUTED:
            if self.side_effect_state is not SideEffectState.NONE:
                raise ValueError("non-executed result cannot claim a side effect")
        if self.reconciliation:
            raise ValueError("reconciliation entries are no longer part of the application result")
        if self.outcome is AuditOutcome.NEEDS_HUMAN:
            if not 2 <= len(self.decision_options) <= 4:
                raise ValueError("needs_human requires two to four decision options")
            keys = [option.key for option in self.decision_options]
            if len(keys) != len(set(keys)):
                raise ValueError("decision option keys must be unique")
        elif self.decision_options:
            raise ValueError("decision options are only valid for needs_human")
        if self.outcome is AuditOutcome.DRY_RUN:
            if self.error.code != "dry_run_execution_suppressed":
                raise ValueError("dry_run requires dry_run_execution_suppressed")
            if self.error.retryable or self.error.authorization_required:
                raise ValueError("dry_run must not be retryable or require authorization")
        return self
