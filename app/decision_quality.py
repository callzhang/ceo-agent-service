"""Pure decision-quality classification for agent results and projections."""

from enum import StrEnum
import json
from math import isfinite
from numbers import Real
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class DecisionQuality(StrEnum):
    """The actionability classification for a result."""

    ASK_BACK = "ask_back"
    NEEDS_HUMAN = "needs_human"
    AUTONOMOUS = "autonomous"


class StoredNeedsHumanProjection(StrEnum):
    """Whether a persisted result is an actionable human rule decision."""

    NEEDS_HUMAN = "needs_human"
    INVALID = "invalid"


class DecisionRisk(StrEnum):
    """The estimated consequence of acting on an incorrect result."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


def _classification_for(
    *,
    risk: DecisionRisk,
    confidence: float,
    rule_coverage: float,
    information_completeness: float,
) -> DecisionQuality:
    if information_completeness < 0.5:
        return DecisionQuality.ASK_BACK
    if (risk is DecisionRisk.HIGH and confidence < 0.5) or rule_coverage < 0.5:
        return DecisionQuality.NEEDS_HUMAN
    return DecisionQuality.AUTONOMOUS


class _DecisionQualityMetrics(BaseModel):
    """Validated inputs used by the result model and its factory."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    risk: DecisionRisk
    confidence: float = Field(ge=0.0, le=1.0)
    rule_coverage: float = Field(ge=0.0, le=1.0)
    information_completeness: float = Field(ge=0.0, le=1.0)

    @field_validator("risk", mode="before")
    @classmethod
    def validate_risk(cls, value: Any) -> str:
        if not isinstance(value, str) or value not in {"low", "medium", "high"}:
            raise ValueError("risk must be low, medium, or high")
        return value

    @field_validator(
        "confidence", "rule_coverage", "information_completeness", mode="before"
    )
    @classmethod
    def validate_score(cls, value: Any) -> float:
        if isinstance(value, bool) or not isinstance(value, Real):
            raise ValueError("quality scores must be numbers between 0 and 1")
        value = float(value)
        if not isfinite(value):
            raise ValueError("quality scores must be finite")
        return value


class DecisionQualityResult(_DecisionQualityMetrics):
    """Validated quality inputs together with their deterministic classification."""

    classification: DecisionQuality

    @model_validator(mode="after")
    def validate_classification(self) -> "DecisionQualityResult":
        expected = _classification_for(
            risk=self.risk,
            confidence=self.confidence,
            rule_coverage=self.rule_coverage,
            information_completeness=self.information_completeness,
        )
        if self.classification is not expected:
            raise ValueError(
                "classification does not match decision quality thresholds"
            )
        return self


def classify_decision_quality(
    *,
    risk: DecisionRisk | str,
    confidence: float,
    rule_coverage: float,
    information_completeness: float,
) -> DecisionQualityResult:
    """Classify a result using the fixed information, risk, and coverage gates."""

    metrics = _DecisionQualityMetrics(
        risk=risk,
        confidence=confidence,
        rule_coverage=rule_coverage,
        information_completeness=information_completeness,
    )
    return DecisionQualityResult(
        **metrics.model_dump(),
        classification=_classification_for(**metrics.model_dump()),
    )


def classify_stored_needs_human_projection(
    result: object,
) -> StoredNeedsHumanProjection:
    """Accept only a complete, typed rule decision as a human projection."""

    return (
        StoredNeedsHumanProjection.NEEDS_HUMAN
        if parse_stored_needs_human_decision(result) is not None
        else StoredNeedsHumanProjection.INVALID
    )


def parse_stored_needs_human_decision(result: object):
    """Return a validated terminal human decision, never a partial JSON shape.

    Imports stay local because the typed result models use this module's quality
    classifier while they are being defined.
    """

    if isinstance(result, str):
        try:
            result = json.loads(result)
        except (TypeError, json.JSONDecodeError):
            return None
    if not isinstance(result, dict):
        return None
    from pydantic import ValidationError

    from app.agent_contracts import AuditAgentResult, ConsumerAgentResult

    if result.get("outcome") == "proposal":
        # A proposal that also asked Derek an independent question. Its action
        # was executed by Audit; the Attempt points at this run for the
        # question (Derek, 2026-09-23).
        try:
            proposal = ConsumerAgentResult.model_validate(result)
        except ValidationError:
            return None
        return proposal if proposal.escalates else None
    if result.get("outcome") != "needs_human":
        return None

    for model in (ConsumerAgentResult, AuditAgentResult):
        try:
            return model.model_validate(result)
        except ValidationError:
            continue
    return None
