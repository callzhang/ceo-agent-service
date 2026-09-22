from enum import StrEnum
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)

from app.agent_result import AgentError
from app.decision_quality import DecisionQuality, classify_decision_quality


class RiskLevel(StrEnum):
    """Estimated consequence if the proposed result is acted on incorrectly."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


def _consumer_result_json_schema(schema: dict[str, object]) -> None:
    required = schema.setdefault("required", [])
    for field in ("risk", "confidence", "rule_coverage", "information_completeness"):
        if field not in required:
            required.append(field)
    schema["anyOf"] = [
        {
            "type": "object",
            "properties": {
                "outcome": {"const": "proposal"},
                "proposal": {"type": "object"},
                "decision_options": {"type": "array", "maxItems": 0},
            },
        },
        {
            "type": "object",
            "properties": {
                "outcome": {
                    "enum": ["no_action", "failed"],
                },
                "proposal": {"type": "null"},
                "decision_options": {"type": "array", "maxItems": 0},
            },
        },
        {
            "type": "object",
            "properties": {
                "outcome": {"const": "needs_human"},
                "proposal": {"type": "null"},
                "decision_options": {"type": "array", "minItems": 2, "maxItems": 4},
                "needs_human_reason": {"type": "string", "minLength": 1},
                "decision_basis": {"type": "object"},
            },
            "required": [
                "outcome",
                "proposal",
                "decision_options",
                "needs_human_reason",
                "decision_basis",
            ],
        },
    ]


def _audit_result_json_schema(schema: dict[str, object]) -> None:
    required = schema.setdefault("required", [])
    for field in ("risk", "confidence", "rule_coverage", "information_completeness"):
        if field not in required:
            required.append(field)
    null_value = {"type": "null"}
    schema["anyOf"] = [
        {
            "type": "object",
            "properties": {
                "outcome": {"const": "executed"},
                "feedback": null_value,
                "external_result": {"type": "object"},
                "decision_options": {"type": "array", "maxItems": 0},
            },
            "required": ["outcome", "feedback", "external_result"],
        },
        {
            "type": "object",
            "properties": {
                "outcome": {"const": "feedback_provided"},
                "feedback": {"type": "object"},
                "external_result": null_value,
                "decision_options": {"type": "array", "maxItems": 0},
            },
            "required": ["outcome", "feedback", "external_result"],
        },
        {
            "type": "object",
            "properties": {
                "outcome": {"const": "failed"},
                "feedback": null_value,
                "external_result": null_value,
                "decision_options": {"type": "array", "maxItems": 0},
            },
            "required": ["outcome", "feedback", "external_result"],
        },
        {
            "type": "object",
            "properties": {
                "outcome": {"const": "needs_human"},
                "feedback": null_value,
                "external_result": null_value,
                "decision_options": {"type": "array", "minItems": 2, "maxItems": 4},
                "needs_human_reason": {"type": "string", "minLength": 1},
                "decision_basis": {"type": "object"},
            },
            "required": [
                "outcome",
                "feedback",
                "external_result",
                "decision_options",
                "needs_human_reason",
                "decision_basis",
            ],
        },
        {
            "type": "object",
            "properties": {
                "outcome": {"const": "dry_run"},
                "feedback": null_value,
                "external_result": null_value,
                "decision_options": {"type": "array", "maxItems": 0},
            },
            "required": ["outcome", "feedback", "external_result"],
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
    action_identity: str = Field(min_length=1)
    capability: str = Field(min_length=1)
    operation: str = Field(min_length=1)
    target: dict[str, JsonValue] = Field(min_length=1)
    payload: dict[str, JsonValue]
    # Whether performing this action changes anything outside the service.
    # `capability` and `operation` cannot answer that: they are free text, and
    # one September month spelled chat four ways and wrote `no-op`,
    # `accept_already_confirmed` and `event get verification` as operations. The
    # proposer states it here instead, before anyone knows whether the effect
    # will happen, and the evidence gate asks for a provider receipt only for
    # actions that claim one. It defaults to `external` so an action that says
    # nothing is still held to evidence.
    effect: Literal["external", "none"] = Field(
        default="external",
        description=(
            "external when performing this action changes something outside "
            "the service and a provider must accept it; none when it changes "
            "nothing -- a verification, or a state already in place. An action "
            "that carries a message body is always external."
        ),
    )

    @model_validator(mode="after")
    def validate_dingtalk_message_target(self) -> "ProposedAction":
        if self.capability != "dingtalk-chat":
            return self
        if {"open_conversation_id", "reply_to_message_id"}.intersection(self.target):
            raise ValueError(
                "DingTalk proposal targets use conversation_id and message_id"
            )
        conversation_id = str(self.target.get("conversation_id") or "").strip()
        message_id = str(
            self.target.get("message_id") or self.target.get("source_message_id") or ""
        ).strip()
        recipient = str(
            self.target.get("open_dingtalk_id")
            or self.target.get("user_id")
            or self.target.get("recipient_open_dingtalk_id")
            or self.target.get("sender_open_dingtalk_id")
            or self.target.get("verified_participant_open_dingtalk_id")
            or ""
        ).strip()
        if (
            self.operation
            in {"send_to_group", "messages-send-to-group", "send_group_message"}
            and not conversation_id
        ):
            raise ValueError("DingTalk group target requires conversation_id")
        if self.operation in {"messages-reply", "message.reply", "reply"} and not (
            conversation_id and message_id
        ):
            raise ValueError(
                "DingTalk reply target requires conversation_id and message_id"
            )
        if (
            self.operation
            in {
                "send_direct_message",
                "send_message_to_source_conversation",
            }
            and not recipient
        ):
            raise ValueError("DingTalk direct target requires a stable recipient id")
        return self


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

    @model_validator(mode="after")
    def validate_action_identities(self) -> "ConsumerProposal":
        identities = [action.action_identity for action in self.actions]
        if len(identities) != len(set(identities)):
            raise ValueError("action_identity must be unique within a proposal")
        return self


class DecisionOption(BaseModel):
    """One actionable, mutually exclusive instruction for a real management choice."""

    model_config = ConfigDict(extra="forbid", strict=True)

    key: str = Field(min_length=1)
    label: str = Field(min_length=1)
    instruction: str = Field(min_length=1)
    consequence: str = Field(min_length=1)


class DecisionBasis(BaseModel):
    """The compact evidence chain a person needs to assess an escalation."""

    model_config = ConfigDict(extra="forbid", strict=True)

    verified_facts: tuple[ProposalFact, ...] = Field(min_length=1)
    rule_evidence: tuple[ProposalFact, ...] = Field(min_length=1)
    quality_explanation: str = Field(min_length=1)
    no_external_action_evidence: tuple[ProposalFact, ...] = Field(min_length=1)
    conclusion: str = Field(min_length=1)

    @field_validator(
        "verified_facts",
        "rule_evidence",
        "no_external_action_evidence",
        mode="before",
    )
    @classmethod
    def accept_json_arrays(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value


class AuthorizationPlan(BaseModel):
    """One bounded, externally effective action awaiting a human decision."""

    model_config = ConfigDict(extra="forbid", strict=True)

    summary: str = Field(min_length=1)
    primary_action: ProposedAction
    follow_up_actions: tuple[ProposedAction, ...] = ()
    side_effects: tuple[str, ...] = Field(min_length=1)
    will_not_do: tuple[str, ...] = Field(min_length=1)
    readback: tuple[str, ...] = Field(min_length=1)

    @field_validator(
        "follow_up_actions",
        "side_effects",
        "will_not_do",
        "readback",
        mode="before",
    )
    @classmethod
    def accept_json_arrays(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def validate_explicit_external_action(self) -> "AuthorizationPlan":
        if self.summary != self.primary_action.description:
            raise ValueError("authorization plan summary must name the primary action")
        if self.primary_action.effect != "external":
            raise ValueError("authorization plan primary action must be external")
        action_ids = [self.primary_action.action_identity] + [
            action.action_identity for action in self.follow_up_actions
        ]
        if len(action_ids) != len(set(action_ids)):
            raise ValueError("authorization plan action identities must be unique")
        return self


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
    risk: RiskLevel
    confidence: float = Field(ge=0.0, le=1.0)
    rule_coverage: float = Field(ge=0.0, le=1.0)
    information_completeness: float = Field(ge=0.0, le=1.0)
    needs_human_reason: str | None = None
    decision_basis: DecisionBasis | None = None
    authorization_plan: AuthorizationPlan | None = None

    @field_validator("outcome", mode="before")
    @classmethod
    def accept_json_outcome(cls, value: object) -> object:
        return ConsumerOutcome(value) if isinstance(value, str) else value

    @field_validator("risk", mode="before")
    @classmethod
    def accept_json_risk(cls, value: object) -> object:
        return RiskLevel(value) if isinstance(value, str) else value

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
            if not self.needs_human_reason:
                raise ValueError("needs_human_reason is required for needs_human")
            if self.decision_basis is None:
                raise ValueError("decision_basis is required for needs_human")
            if self.error.retryable:
                raise ValueError("needs_human cannot carry a retryable error")
            if self.error.authorization_required:
                if self.authorization_plan is None:
                    raise ValueError(
                        "authorization_plan is required for authorization needs_human"
                    )
                if self.risk is not RiskLevel.HIGH:
                    raise ValueError("authorization needs_human requires high risk")
                if self.information_completeness < 0.5 or self.rule_coverage < 0.5:
                    raise ValueError(
                        "authorization needs_human requires complete information and rule coverage"
                    )
            else:
                if self.error.code:
                    raise ValueError("needs_human cannot carry a non-authorization error")
                if self.authorization_plan is not None:
                    raise ValueError(
                        "authorization_plan requires authorization needs_human"
                    )
                quality = classify_decision_quality(
                    risk=self.risk.value,
                    confidence=self.confidence,
                    rule_coverage=self.rule_coverage,
                    information_completeness=self.information_completeness,
                )
                if quality.classification is not DecisionQuality.NEEDS_HUMAN:
                    raise ValueError(
                        "needs_human outcome must match decision quality classification"
                    )
        elif self.decision_options:
            raise ValueError("decision options are only valid for needs_human")
        elif any(
            value is not None
            for value in (
                self.needs_human_reason,
                self.decision_basis,
                self.authorization_plan,
            )
        ):
            raise ValueError("needs_human fields are only valid for needs_human")
        return self


class AuditOutcome(StrEnum):
    EXECUTED = "executed"
    FEEDBACK_PROVIDED = "feedback_provided"
    NEEDS_HUMAN = "needs_human"
    DRY_RUN = "dry_run"
    FAILED = "failed"


class AuditFeedback(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    rule: str = Field(min_length=1)
    observation: str = Field(min_length=1)
    requested_revision: str = Field(min_length=1)


class AuditExternalResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    operation_id: str = Field(min_length=1)
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
    feedback: AuditFeedback | None
    external_result: AuditExternalResult | None
    decision_options: tuple[DecisionOption, ...] = ()
    error: AgentError
    risk: RiskLevel
    confidence: float = Field(ge=0.0, le=1.0)
    rule_coverage: float = Field(ge=0.0, le=1.0)
    information_completeness: float = Field(ge=0.0, le=1.0)
    needs_human_reason: str | None = None
    decision_basis: DecisionBasis | None = None
    authorization_plan: AuthorizationPlan | None = None

    @field_validator("outcome", mode="before")
    @classmethod
    def accept_json_outcome(cls, value: object) -> object:
        if isinstance(value, str) and value == "revision_required":
            value = "feedback_provided"
        return AuditOutcome(value) if isinstance(value, str) else value

    @field_validator("risk", mode="before")
    @classmethod
    def accept_json_risk(cls, value: object) -> object:
        return RiskLevel(value) if isinstance(value, str) else value

    @field_validator("decision_options", mode="before")
    @classmethod
    def accept_json_arrays(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def validate_outcome_payload(self) -> "AuditAgentResult":
        if self.outcome is AuditOutcome.FEEDBACK_PROVIDED:
            if self.feedback is None or self.external_result is not None:
                raise ValueError("feedback_provided needs feedback and no result")
        elif self.feedback is not None:
            raise ValueError("feedback is only valid for feedback_provided")
        if self.outcome is AuditOutcome.EXECUTED:
            if self.external_result is None:
                raise ValueError("executed needs external result")
        elif self.external_result is not None:
            raise ValueError("external result is only valid for executed")
        if self.outcome is AuditOutcome.NEEDS_HUMAN:
            if not 2 <= len(self.decision_options) <= 4:
                raise ValueError("needs_human requires two to four decision options")
            keys = [option.key for option in self.decision_options]
            if len(keys) != len(set(keys)):
                raise ValueError("decision option keys must be unique")
            if not self.needs_human_reason:
                raise ValueError("needs_human_reason is required for needs_human")
            if self.decision_basis is None:
                raise ValueError("decision_basis is required for needs_human")
            if self.error.retryable:
                raise ValueError("needs_human cannot carry a retryable error")
            if self.error.authorization_required:
                if self.authorization_plan is None:
                    raise ValueError(
                        "authorization_plan is required for authorization needs_human"
                    )
                if self.risk is not RiskLevel.HIGH:
                    raise ValueError("authorization needs_human requires high risk")
                if self.information_completeness < 0.5 or self.rule_coverage < 0.5:
                    raise ValueError(
                        "authorization needs_human requires complete information and rule coverage"
                    )
            else:
                if self.error.code:
                    raise ValueError("needs_human cannot carry a non-authorization error")
                if self.authorization_plan is not None:
                    raise ValueError(
                        "authorization_plan requires authorization needs_human"
                    )
                quality = classify_decision_quality(
                    risk=self.risk.value,
                    confidence=self.confidence,
                    rule_coverage=self.rule_coverage,
                    information_completeness=self.information_completeness,
                )
                if quality.classification is not DecisionQuality.NEEDS_HUMAN:
                    raise ValueError(
                        "needs_human outcome must match decision quality classification"
                    )
        elif self.decision_options:
            raise ValueError("decision options are only valid for needs_human")
        elif any(
            value is not None
            for value in (
                self.needs_human_reason,
                self.decision_basis,
                self.authorization_plan,
            )
        ):
            raise ValueError("needs_human fields are only valid for needs_human")
        if self.outcome is AuditOutcome.DRY_RUN:
            if self.error.code != "dry_run_execution_suppressed":
                raise ValueError("dry_run requires dry_run_execution_suppressed")
            if self.error.retryable or self.error.authorization_required:
                raise ValueError(
                    "dry_run must not be retryable or require authorization"
                )
        return self
