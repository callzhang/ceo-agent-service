from __future__ import annotations

from enum import StrEnum
from typing import FrozenSet

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RuntimeKind(StrEnum):
    CODEX_CLI = "codex_cli"
    CLAUDE_CLI = "claude_cli"


class CredentialMode(StrEnum):
    LOCAL_OAUTH = "local_oauth"
    SERVICE_API = "service_api"


class RuntimeFailureClass(StrEnum):
    AUTHENTICATION = "authentication"
    CAPACITY = "capacity"
    TRANSPORT = "transport"
    CAPABILITY = "capability"
    SESSION = "session"
    RESULT = "result"
    PROCESS = "process"
    UNCLASSIFIED = "unclassified"


class RuntimeRoute(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    runtime_kind: RuntimeKind
    credential_mode: CredentialMode
    model: str

    @field_validator("name", "model")
    @classmethod
    def require_non_empty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value must be non-empty")
        return value


class RuntimeFailure(BaseModel):
    model_config = ConfigDict(frozen=True)

    failure_class: RuntimeFailureClass
    code: str
    detail: str
    retryable_on_same_route: bool = False
    failover_permitted: bool = False
    route_pause_required: bool = False


class RuntimeCapabilitySnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    route_name: str
    capabilities: FrozenSet[str] = Field(default_factory=frozenset)
    healthy: bool
    checked_at: str
    expires_at: str
    failure: RuntimeFailure | None = None


class RuntimeSelectionRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    required_capabilities: FrozenSet[str] = Field(default_factory=frozenset)
    side_effect_state: str = "none"
    effect_started_count: int = 0
    has_confirmed_receipt: bool = False
    recovery_phase: str = ""
