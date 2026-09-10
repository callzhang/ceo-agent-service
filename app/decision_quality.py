"""Pure decision-quality classification for agent results."""

from enum import StrEnum
from math import isfinite
from numbers import Real
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class DecisionQuality(StrEnum):
    """The actionability classification for a result."""

    ASK_BACK = "ask_back"
    NEEDS_HUMAN = "needs_human"
    AUTONOMOUS = "autonomous"


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
