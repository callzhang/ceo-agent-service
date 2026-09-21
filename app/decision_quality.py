"""Pure decision-quality classification for agent results and projections."""

from enum import StrEnum
import json
from math import isfinite
from numbers import Real
from typing import Any, Mapping

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

    if isinstance(result, str):
        try:
            result = json.loads(result)
        except (TypeError, json.JSONDecodeError):
            return StoredNeedsHumanProjection.INVALID
    if not isinstance(result, Mapping) or result.get("outcome") != "needs_human":
        return StoredNeedsHumanProjection.INVALID
    error = result.get("error")
    error_values = (
        result.get("error_retryable"),
        result.get("error_authorization_required"),
        error.get("retryable") if isinstance(error, Mapping) else None,
        error.get("authorization_required") if isinstance(error, Mapping) else None,
    )
    if any(value is True for value in error_values):
        return StoredNeedsHumanProjection.INVALID
    try:
        quality = classify_decision_quality(
            risk=result["risk"],
            confidence=result["confidence"],
            rule_coverage=result["rule_coverage"],
            information_completeness=result["information_completeness"],
        )
    except (KeyError, TypeError, ValueError):
        return StoredNeedsHumanProjection.INVALID
    if quality.classification is not DecisionQuality.NEEDS_HUMAN:
        return StoredNeedsHumanProjection.INVALID
    options = result.get("decision_options")
    if not isinstance(options, list) or not 2 <= len(options) <= 4:
        return StoredNeedsHumanProjection.INVALID
    keys: set[str] = set()
    for option in options:
        if not isinstance(option, Mapping):
            return StoredNeedsHumanProjection.INVALID
        if any(
            not isinstance(option.get(field), str) or not option[field].strip()
            for field in ("key", "label", "instruction", "consequence")
        ):
            return StoredNeedsHumanProjection.INVALID
        if option["key"] in keys:
            return StoredNeedsHumanProjection.INVALID
        keys.add(option["key"])
    return StoredNeedsHumanProjection.NEEDS_HUMAN
