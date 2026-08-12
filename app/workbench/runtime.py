"""Provider-neutral runtime contracts for workbench execution."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, Protocol


RuntimeEventType = Literal[
    "text_delta",
    "thinking_summary",
    "tool_started",
    "tool_completed",
    "file_changed",
    "artifact_created",
    "confirmation_required",
    "status_changed",
    "turn_completed",
    "turn_failed",
]
RuntimeResultStatus = Literal["completed", "stopped", "failed"]

_RUNTIME_EVENT_TYPES = frozenset(
    {
        "text_delta",
        "thinking_summary",
        "tool_started",
        "tool_completed",
        "file_changed",
        "artifact_created",
        "confirmation_required",
        "status_changed",
        "turn_completed",
        "turn_failed",
    }
)
_RUNTIME_RESULT_STATUSES = frozenset({"completed", "stopped", "failed"})


def _freeze_payload_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze_payload_value(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_payload_value(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze_payload_value(item) for item in value)
    return deepcopy(value)


def _freeze_payload(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType({key: _freeze_payload_value(value) for key, value in payload.items()})


@dataclass(frozen=True, slots=True)
class RuntimeCapabilities:
    session_resume: bool
    streamed_text: bool
    structured_tools: bool
    image_input: bool
    model_selection: bool
    mcp_configuration: bool
    stoppable: bool
    recoverable: bool


@dataclass(frozen=True, slots=True)
class RuntimeRequest:
    turn_id: str
    workspace: Path
    prompt: str
    provider_session_ref: str = ""
    model: str = ""
    attachment_paths: tuple[Path, ...] = ()
    image_paths: tuple[Path, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", Path(self.workspace))
        object.__setattr__(
            self,
            "attachment_paths",
            tuple(Path(path) for path in self.attachment_paths),
        )
        object.__setattr__(self, "image_paths", tuple(Path(path) for path in self.image_paths))


@dataclass(frozen=True, slots=True)
class RuntimeEvent:
    event_type: RuntimeEventType
    payload: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.event_type not in _RUNTIME_EVENT_TYPES:
            raise ValueError(f"unsupported runtime event type: {self.event_type}")
        if not isinstance(self.payload, Mapping):
            raise ValueError("runtime event payload must be a mapping")
        object.__setattr__(self, "payload", _freeze_payload(self.payload))


@dataclass(frozen=True, slots=True)
class RuntimeResult:
    status: RuntimeResultStatus
    final_text: str = ""
    provider_session_ref: str = ""
    error_code: str = ""
    error_detail: str = ""

    def __post_init__(self) -> None:
        if self.status not in _RUNTIME_RESULT_STATUSES:
            raise ValueError(f"unsupported runtime result status: {self.status}")


@dataclass(frozen=True, slots=True)
class RuntimeHandle:
    run_id: str
    _owner: object = field(repr=False, compare=False, hash=False)


class AgentRuntime(Protocol):
    kind: str

    def capabilities(self) -> RuntimeCapabilities: ...

    def start(
        self,
        request: RuntimeRequest,
        *,
        on_event: Callable[[RuntimeEvent], None],
    ) -> RuntimeHandle: ...

    def wait(self, handle: RuntimeHandle) -> RuntimeResult: ...

    def stop(self, handle: RuntimeHandle) -> None: ...


class RuntimeRegistry:
    def __init__(self, runtimes: Iterable[AgentRuntime] = ()) -> None:
        self._runtimes: dict[str, AgentRuntime] = {}
        for runtime in runtimes:
            self.register(runtime)

    def register(self, runtime: AgentRuntime) -> None:
        kind = runtime.kind
        if not isinstance(kind, str) or not kind.strip():
            raise ValueError("runtime kind must not be blank")
        if kind in self._runtimes:
            raise ValueError(f"duplicate runtime kind: {kind}")
        self._runtimes[kind] = runtime

    def get(self, kind: str) -> AgentRuntime:
        try:
            return self._runtimes[kind]
        except KeyError as exc:
            raise KeyError(f"unsupported runtime: {kind}") from exc

    def kinds(self) -> tuple[str, ...]:
        return tuple(self._runtimes)
