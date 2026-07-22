from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

QualitySuite = Literal["protocol", "safety", "semantic"]
QualityStatus = Literal["passed", "failed", "configuration_error", "infrastructure_error"]
ComponentStatus = Literal["ready", "degraded", "failed", "stale", "missing"]

_SENSITIVE_KEY = re.compile(
    r"(?:^|_)(?:message_body|message_text|person_name|real_name|user_id|open_id|path|token|secret|credential)(?:$|_)",
    re.IGNORECASE,
)
_SENSITIVE_TEXT = re.compile(
    r"(?:/Users/|/home/|Bearer\s+[A-Za-z0-9._~+/=-]+|(?:api[_-]?key|token|secret)\s*[:=])",
    re.IGNORECASE,
)


def _assert_redacted(value: Any, *, location: str = "payload") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if _SENSITIVE_KEY.search(str(key)):
                raise ValueError(f"{location} contains a sensitive field: {key}")
            _assert_redacted(item, location=f"{location}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _assert_redacted(item, location=f"{location}[{index}]")
    elif isinstance(value, str) and _SENSITIVE_TEXT.search(value):
        raise ValueError(f"{location} contains non-redacted text")


class QualityCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, pattern=r"^[a-z0-9][a-z0-9_.-]*$")
    suite: QualitySuite
    input_context: dict[str, Any]
    dependency_state: dict[str, Literal["ready", "degraded", "unavailable"]] = Field(
        default_factory=dict
    )
    recorded_output: dict[str, Any]
    allowed_actions: tuple[str, ...] = ()
    forbidden_actions: tuple[str, ...] = ()
    target_binding: dict[str, str] = Field(default_factory=dict)
    required_evidence: tuple[str, ...] = ()
    forbidden_text: tuple[str, ...] = ()
    replay_invariants: tuple[
        Literal["unique_idempotency_key", "single_terminal_state"], ...
    ] = ()
    expected_semantics: dict[str, Any] = Field(default_factory=dict)
    critical: bool = False

    @model_validator(mode="after")
    def validate_redaction(self) -> QualityCase:
        _assert_redacted(self.input_context, location="input_context")
        _assert_redacted(self.recorded_output, location="recorded_output")
        return self


class ComponentHealth(BaseModel):
    model_config = ConfigDict(frozen=True)

    component: str
    status: ComponentStatus
    last_success_at: str = ""
    last_failure_at: str = ""
    consecutive_failures: int = 0


class QualitySnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    commit: str
    pid: int
    schema_version: int
    generated_at: str = ""
    components: tuple[ComponentHealth, ...]
    backlog: dict[str, int]
    oldest_queue_age_seconds: int
    failed_actions: int
    unknown_actions: int
    slo_status: Literal["pass", "warn", "fail"]
    ready: bool


class QualityFailure(BaseModel):
    model_config = ConfigDict(frozen=True)

    case_id: str
    rule: str
    detail: str


class EvaluationResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    suite: QualitySuite
    status: QualityStatus
    total: int
    passed: int
    failed: int
    score: float
    failures: tuple[QualityFailure, ...] = ()
