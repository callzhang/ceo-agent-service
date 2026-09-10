"""Pure decision-quality classification for agent results."""

from enum import StrEnum
from math import isfinite
from numbers import Real
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


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


class DecisionQualityResult(BaseModel):
    """Validated quality inputs together with their deterministic classification."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    risk: DecisionRisk
    confidence: float = Field(ge=0.0, le=1.0)
    rule_coverage: float = Field(ge=0.0, le=1.0)
    information_completeness: float = Field(ge=0.0, le=1.0)
    classification: DecisionQuality

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


def classify_decision_quality(
    *,
    risk: DecisionRisk | str,
    confidence: float,
    rule_coverage: float,
    information_completeness: float,
) -> DecisionQualityResult:
    """Classify a result using the fixed information, risk, and coverage gates."""

    validated = DecisionQualityResult(
        risk=risk,
        confidence=confidence,
        rule_coverage=rule_coverage,
        information_completeness=information_completeness,
        classification=DecisionQuality.AUTONOMOUS,
    )

    if validated.information_completeness < 0.5:
        classification = DecisionQuality.ASK_BACK
    elif (
        validated.risk is DecisionRisk.HIGH and validated.confidence < 0.5
    ) or validated.rule_coverage < 0.5:
        classification = DecisionQuality.NEEDS_HUMAN
    else:
        classification = DecisionQuality.AUTONOMOUS

    return validated.model_copy(update={"classification": classification})
