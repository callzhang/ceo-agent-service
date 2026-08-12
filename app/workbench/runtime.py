"""Provider-neutral runtime contracts for workbench execution."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from math import isfinite
from pathlib import Path
from threading import RLock
from typing import Any, Literal, Protocol
from weakref import WeakKeyDictionary


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


@dataclass(frozen=True, slots=True, eq=False)
class _FrozenJsonMapping(Mapping[str, Any]):
    """A tuple-backed immutable mapping for provider-neutral event payloads."""

    _items: tuple[tuple[str, Any], ...]
    __hash__ = None

    def __getitem__(self, key: str) -> Any:
        for item_key, value in self._items:
            if item_key == key:
                return value
        raise KeyError(key)

    def __iter__(self):
        return (key for key, _ in self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Mapping):
            return NotImplemented
        return dict(self.items()) == dict(other.items())


def _freeze_json_value(value: Any, active_container_ids: set[int] | None = None) -> Any:
    if value is None or type(value) in (bool, int, str):
        return value
    if type(value) is float:
        if not isfinite(value):
            raise ValueError("runtime event payload must contain only finite JSON-compatible values")
        return value
    if active_container_ids is None:
        active_container_ids = set()
    if isinstance(value, Mapping):
        container_id = id(value)
        if container_id in active_container_ids:
            raise ValueError("runtime event payload must not contain a cyclic container")
        active_container_ids.add(container_id)
        try:
            frozen_items: list[tuple[str, Any]] = []
            for key, item in value.items():
                if type(key) is not str:
                    raise ValueError(
                        "runtime event payload must use string JSON-compatible mapping keys"
                    )
                frozen_items.append((key, _freeze_json_value(item, active_container_ids)))
            return _FrozenJsonMapping(tuple(frozen_items))
        finally:
            active_container_ids.remove(container_id)
    if type(value) in (list, tuple):
        container_id = id(value)
        if container_id in active_container_ids:
            raise ValueError("runtime event payload must not contain a cyclic container")
        active_container_ids.add(container_id)
        try:
            return tuple(_freeze_json_value(item, active_container_ids) for item in value)
        finally:
            active_container_ids.remove(container_id)
    raise ValueError("runtime event payload must contain only JSON-compatible values")


def _json_projection(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _json_projection(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_json_projection(item) for item in value]
    return value


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


@dataclass(frozen=True, slots=True, eq=False)
class RuntimeEvent:
    event_type: RuntimeEventType
    payload: Mapping[str, Any] = field(default_factory=dict)
    __hash__ = None

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, RuntimeEvent):
            return NotImplemented
        return self.event_type == other.event_type and self.payload == other.payload

    def __post_init__(self) -> None:
        if self.event_type not in _RUNTIME_EVENT_TYPES:
            raise ValueError(f"unsupported runtime event type: {self.event_type}")
        if not isinstance(self.payload, Mapping):
            raise ValueError("runtime event payload must be a mapping")
        frozen_payload = _freeze_json_value(self.payload)
        object.__setattr__(self, "payload", frozen_payload)

    def payload_json_value(self) -> dict[str, Any]:
        """Return a fresh plain JSON value suitable for persistence or API encoding."""
        return _json_projection(self.payload)


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


@dataclass(frozen=True, slots=True, eq=False, weakref_slot=True, init=False)
class RuntimeHandle:
    run_id: str

    @classmethod
    def create(cls, *, run_id: str, owner: object) -> RuntimeHandle:
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("runtime run id must not be blank")
        if owner is None:
            raise ValueError("runtime owner must not be None")
        handle = object.__new__(cls)
        object.__setattr__(handle, "run_id", run_id)
        with _RUNTIME_OWNER_LOCK:
            _RUNTIME_OWNERS[handle] = owner
        return handle

    def __init__(self, run_id: str) -> None:
        raise TypeError("use RuntimeHandle.create")


_RUNTIME_OWNER_LOCK = RLock()
_RUNTIME_OWNERS: WeakKeyDictionary[RuntimeHandle, object] = WeakKeyDictionary()


def _runtime_owner(handle: RuntimeHandle) -> object:
    """Return the process owner held privately for an active runtime handle."""
    with _RUNTIME_OWNER_LOCK:
        try:
            return _RUNTIME_OWNERS[handle]
        except KeyError as exc:
            raise ValueError("runtime handle owner is unavailable") from exc


def _release_runtime_owner(handle: RuntimeHandle) -> object:
    """Consume the private process owner when an adapter reaches a terminal state."""
    with _RUNTIME_OWNER_LOCK:
        try:
            return _RUNTIME_OWNERS.pop(handle)
        except KeyError as exc:
            raise ValueError("runtime handle owner is unavailable") from exc


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
        if kind != kind.strip():
            raise ValueError("runtime kind must be canonical")
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
