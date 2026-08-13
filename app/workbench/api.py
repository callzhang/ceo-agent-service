"""Protected JSON and persisted event-stream routes for the local workbench."""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import os
import re
import stat
import threading
from collections.abc import Callable, Sequence
from dataclasses import asdict
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Literal
from uuid import UUID
from urllib.parse import quote, unquote, urlparse, urlsplit

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.leak_check import (
    contains_credential,
    contains_local_runtime_leak,
    is_sensitive_field_name,
)
from app.workbench.executor import WorkbenchExecutor
from app.workbench.models import (
    ConfirmationStatus,
    TurnStatus,
    WorkbenchArtifact,
    WorkbenchAttachment,
    WorkbenchConfirmation,
    WorkbenchEvent,
    WorkbenchTask,
    WorkbenchTurn,
)
from app.workbench.runtime import RuntimeRegistry
from app.workbench.store import WorkbenchConflictError, WorkbenchStore


_MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024
_MAX_ATTACHMENT_BASE64_LENGTH = ((_MAX_ATTACHMENT_BYTES + 2) // 3) * 4
_MAX_SMALL_JSON_BYTES = 128 * 1024
_MAX_ATTACHMENT_JSON_BYTES = _MAX_ATTACHMENT_BASE64_LENGTH + 16 * 1024
_TERMINAL_TURN_STATUSES = {
    TurnStatus.COMPLETED,
    TurnStatus.STOPPED,
    TurnStatus.FAILED,
}
_LOGGER = logging.getLogger(__name__)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _CreateTask(_StrictModel):
    title: str = Field(min_length=1, max_length=200)
    runtime_kind: str = Field(min_length=1, max_length=80)


class _RenameTask(_StrictModel):
    title: str = Field(min_length=1, max_length=200)


class _CreateTurn(_StrictModel):
    text: str = Field(min_length=1, max_length=100_000)
    client_request_id: str = Field(min_length=1, max_length=200)


class _AttachmentUpload(_StrictModel):
    filename: str = Field(min_length=1, max_length=255)
    media_type: str = Field(min_length=1, max_length=100)
    content_base64: str = Field(min_length=1, max_length=_MAX_ATTACHMENT_BASE64_LENGTH)

    @field_validator("filename")
    @classmethod
    def safe_filename(cls, value: str) -> str:
        if Path(value).name != value or any(ord(char) < 32 for char in value):
            raise ValueError("filename must be a safe basename")
        return value


class PublicTask(_StrictModel):
    id: str
    title: str
    runtime_kind: str
    archived_at: str
    state: str
    created_at: str
    updated_at: str


class PublicTurn(_StrictModel):
    id: str
    task_id: str
    client_request_id: str
    user_text: str
    status: TurnStatus
    stop_requested: bool
    final_text: str
    error_code: str
    error_detail: str
    started_at: str
    completed_at: str
    created_at: str
    updated_at: str


class PublicEvent(_StrictModel):
    id: int
    turn_id: str
    sequence: int
    event_type: str
    payload: dict[str, Any]
    created_at: str


class PublicArtifact(_StrictModel):
    id: str
    turn_id: str
    label: str
    media_type: str
    created_at: str
    download_url: str


class PublicConfirmation(_StrictModel):
    id: str
    turn_id: str
    action_kind: str
    target: str
    summary: str
    risk: str
    canonical_capability: str
    canonical_operation: str
    canonical_targets: list[str]
    status: ConfirmationStatus
    decision_requested: str
    decision_requested_at: str
    proposer_quiesced: bool
    created_at: str
    decided_at: str


class PublicTimeline(_StrictModel):
    task: PublicTask
    turns: list[PublicTurn]
    events: list[PublicEvent]
    attachments: list[WorkbenchAttachment]
    artifacts: list[PublicArtifact]
    confirmations: list[PublicConfirmation]


class RuntimeCapabilitiesResponse(_StrictModel):
    kind: str
    capabilities: dict[str, bool]


class CountSummary(_StrictModel):
    total: int
    active: int = 0
    archived: int = 0


class DurationSummary(_StrictModel):
    completed_count: int
    total_seconds: float
    average_seconds: float


class WorkbenchStatsResponse(_StrictModel):
    tasks: CountSummary
    turns: dict[str, int]
    confirmations: dict[str, int]
    events: dict[str, int]
    attachments: int
    artifacts: int
    duration: DurationSummary


class _Subscription:
    def __init__(self, broker: "EventBroker", turn_id: str) -> None:
        self._broker = broker
        self.turn_id = turn_id
        self.loop = asyncio.get_running_loop()
        self.event = asyncio.Event()
        self.closed = False

    async def wait(self, timeout: float) -> None:
        try:
            await asyncio.wait_for(self.event.wait(), timeout=timeout)
        except TimeoutError:
            return
        self.event.clear()

    def wake(self) -> None:
        if not self.closed:
            self.loop.call_soon_threadsafe(self.event.set)

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            self._broker._remove(self)


class EventBroker:
    """A bounded wakeup broker; SQLite remains the event authority."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: dict[str, set[_Subscription]] = {}

    def subscribe(self, turn_id: str) -> _Subscription:
        subscription = _Subscription(self, turn_id)
        with self._lock:
            self._subscribers.setdefault(turn_id, set()).add(subscription)
        return subscription

    def notify(self, turn_id: str) -> None:
        with self._lock:
            subscriptions = tuple(self._subscribers.get(turn_id, ()))
        for subscription in subscriptions:
            subscription.wake()

    def _remove(self, subscription: _Subscription) -> None:
        with self._lock:
            subscriptions = self._subscribers.get(subscription.turn_id)
            if subscriptions is None:
                return
            subscriptions.discard(subscription)
            if not subscriptions:
                self._subscribers.pop(subscription.turn_id, None)

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return sum(len(items) for items in self._subscribers.values())


class WorkbenchScheduler:
    def __init__(
        self, executor: WorkbenchExecutor, *, interval_seconds: float = 1.0
    ) -> None:
        if interval_seconds < 0.01 or interval_seconds > 60:
            raise ValueError("scheduler interval must be between 0.01 and 60 seconds")
        self._executor = executor
        self._interval_seconds = interval_seconds
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._lock = threading.Lock()
        self._close_complete: bool | None = None
        self._thread = threading.Thread(
            target=self._run,
            name="workbench-api-scheduler",
            daemon=True,
        )

    def start(self) -> None:
        with self._lock:
            if self._stop.is_set():
                raise RuntimeError("workbench scheduler is closed")
            if self._thread.is_alive():
                return
            self._thread.start()

    def wake(self) -> None:
        if not self._stop.is_set():
            self._wake.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._executor.run_once()
            except Exception:
                _LOGGER.warning(
                    "workbench scheduler cycle failed",
                    extra={"error_code": "executor_cycle_failed"},
                )
            self._wake.wait(self._interval_seconds)
            self._wake.clear()

    def stop(self) -> None:
        with self._lock:
            self._stop.set()
            self._wake.set()

    def join(self, *, timeout: float) -> bool:
        if timeout < 0:
            raise ValueError("scheduler join timeout must be nonnegative")
        with self._lock:
            started = self._thread.ident is not None
        if started and self._thread is not threading.current_thread():
            self._thread.join(timeout=timeout)
        complete = not self._thread.is_alive()
        with self._lock:
            self._close_complete = complete
        return complete

    def close(self, *, timeout: float = 1.0) -> bool:
        self.stop()
        return self.join(timeout=timeout)

    @property
    def is_alive(self) -> bool:
        return self._thread.is_alive()

    @property
    def close_complete(self) -> bool | None:
        with self._lock:
            return self._close_complete


def _uuid_text(value: UUID) -> str:
    return str(value)


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="Workbench resource not found")


_PUBLIC_ERROR_DETAILS = {
    "task_has_active_turn": "Tasks with active turns cannot be archived",
    "task_archived": "Archived tasks cannot accept new turns",
    "client_request_conflict": "Client request ID conflicts with an existing turn",
    "attachment_invalid": "Attachment data is invalid",
}


def _public_error(status_code: int, code: str) -> HTTPException:
    _LOGGER.info("workbench API request rejected", extra={"error_code": code})
    return HTTPException(
        status_code=status_code,
        detail=_PUBLIC_ERROR_DETAILS.get(
            code, "request conflicts with current resource state"
        ),
    )


async def _request_model(
    request: Request,
    model_type: type[_StrictModel],
    *,
    mutation_guard: Callable[[Request], None],
    max_bytes: int = _MAX_SMALL_JSON_BYTES,
) -> _StrictModel:
    mutation_guard(request)
    content_length = request.headers.get("content-length", "").strip()
    if content_length:
        try:
            if int(content_length) > max_bytes:
                raise HTTPException(status_code=413, detail="JSON request is too large")
        except ValueError as exc:
            raise HTTPException(
                status_code=400, detail="Invalid Content-Length"
            ) from exc
    body = await request.body()
    if len(body) > max_bytes:
        raise HTTPException(status_code=413, detail="JSON request is too large")
    try:
        return model_type.model_validate_json(body)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON request") from exc


def _public_task(store: WorkbenchStore, task: WorkbenchTask) -> PublicTask:
    turns = store.list_turns(task.id)
    state = turns[-1].status.value if turns else "idle"
    return PublicTask(
        id=task.id,
        title=task.title,
        runtime_kind=task.runtime_kind,
        archived_at=task.archived_at,
        state=state,
        created_at=task.created_at,
        updated_at=task.updated_at,
    )


def _public_turn(turn: WorkbenchTurn) -> PublicTurn:
    return PublicTurn.model_validate(turn.model_dump())


_PUBLIC_EVENT_FIELDS: dict[str, frozenset[str]] = {
    "text_delta": frozenset({"text"}),
    "thinking_summary": frozenset({"text", "summary"}),
    "tool_started": frozenset({"tool", "summary", "tool_call_id"}),
    "tool_completed": frozenset({"tool", "summary", "status", "tool_call_id"}),
    "file_changed": frozenset({"filename", "path", "change", "status"}),
    "artifact_created": frozenset(
        {"artifact_id", "label", "filename", "path", "media_type"}
    ),
    "confirmation_required": frozenset(
        {"action_kind", "confirmation_id", "target", "summary", "risk"}
    ),
    "status_changed": frozenset(
        {"status", "code", "confirmation_id", "confirmation_status"}
    ),
    "turn_completed": frozenset({"status"}),
    "turn_failed": frozenset({"status", "code", "confirmation_id"}),
}


_PATH_FIELD_NAMES = frozenset(
    {
        "path",
        "file",
        "filepath",
        "file_path",
        "filename",
        "file_name",
        "directory",
        "dir",
    }
)


def _is_path_field_name(key: str) -> bool:
    snake_case = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", key)
    tokens = re.split(r"[^a-z0-9]+", snake_case.casefold())
    return any(token in _PATH_FIELD_NAMES for token in tokens)


def _safe_path_display(value: str, workspace: Path) -> str:
    if value.casefold().startswith("file://"):
        value = urlparse(value).path
    candidate = Path(value)
    windows_candidate = PureWindowsPath(value)
    if windows_candidate.is_absolute():
        return windows_candidate.name
    if ".." in windows_candidate.parts:
        return windows_candidate.name
    if candidate.is_absolute():
        try:
            return candidate.resolve(strict=False).relative_to(workspace).as_posix()
        except ValueError:
            return candidate.name
    if ".." in candidate.parts:
        return candidate.name
    if "\\" in value:
        return windows_candidate.as_posix()
    return candidate.as_posix()


_WEB_URL_PATTERN = re.compile(r"https?://[^\s'\"`<>\[\]{}(),;|]+", re.IGNORECASE)
_LOCAL_PATH_BOUNDARY = r"(?:^|[\s=:'\"`\[\]{}(),;<>|])"
_LOCAL_PATH_END = r"[^ \t\r\n\f\v'\"`<>|]*"
_SAFE_PUBLIC_PATH_PREFIXES = ("/api", "/workbench-assets")


def _bounded_unquote(value: str) -> str | None:
    current = value
    for _ in range(3):
        try:
            decoded = unquote(current, errors="strict")
        except (UnicodeDecodeError, ValueError):
            return None
        if decoded == current:
            return decoded
        current = decoded
    return None


def _has_path_traversal_component(value: str) -> bool:
    return any(part in {".", ".."} for part in re.split(r"[/=&;:]+", value))


def _is_canonical_safe_public_url_path(value: str) -> bool:
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        return False
    if "\\" in value:
        return False
    decoded = _bounded_unquote(value)
    if decoded is None or decoded != value or "%" in value:
        return False
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    if parsed.scheme or parsed.netloc:
        return False
    path = parsed.path
    if not any(
        path.startswith(f"{prefix}/") for prefix in _SAFE_PUBLIC_PATH_PREFIXES
    ):
        return False
    if re.fullmatch(r"/[A-Za-z0-9._~/-]+", path) is None:
        return False
    if "//" in path or PurePosixPath(path).as_posix() != path:
        return False
    if any(part in {"", ".", ".."} for part in path.split("/")[1:]):
        return False
    return not (
        "/" in parsed.query
        or "/" in parsed.fragment
        or _has_path_traversal_component(parsed.query)
        or _has_path_traversal_component(parsed.fragment)
    )


def _redact_local_path_segment(value: str) -> str:
    local_path_pattern = re.compile(
        rf"(?P<prefix>{_LOCAL_PATH_BOUNDARY})(?P<path>"
        rf"/(?!/){_LOCAL_PATH_END}"
        rf"|[A-Za-z]:[\\/]{_LOCAL_PATH_END}"
        rf"|\\\\[^\\/\s]+[\\/]{_LOCAL_PATH_END}"
        rf")"
    )

    def replace(match: re.Match[str]) -> str:
        path = match.group("path")
        if path.startswith("/") and _is_canonical_safe_public_url_path(path):
            return match.group(0)
        return f"{match.group('prefix')}[local path]"

    return local_path_pattern.sub(replace, value)


def _redact_local_path_substrings(value: str) -> str:
    pieces: list[str] = []
    cursor = 0
    for match in _WEB_URL_PATTERN.finditer(value):
        pieces.append(_redact_local_path_segment(value[cursor : match.start()]))
        pieces.append(match.group(0))
        cursor = match.end()
    pieces.append(_redact_local_path_segment(value[cursor:]))
    return "".join(pieces)


def _contains_local_runtime_leak_outside_web_urls(value: str) -> bool:
    cursor = 0
    for match in _WEB_URL_PATTERN.finditer(value):
        if contains_local_runtime_leak(value[cursor : match.start()]):
            return True
        cursor = match.end()
    return contains_local_runtime_leak(value[cursor:])


def _safe_public_value(value: Any, *, key: str, workspace: Path) -> Any:
    if isinstance(value, dict):
        return {
            child_key: _safe_public_value(
                child_value, key=child_key, workspace=workspace
            )
            for child_key, child_value in value.items()
            if not is_sensitive_field_name(child_key)
        }
    if isinstance(value, list):
        return [
            _safe_public_value(item, key=key, workspace=workspace) for item in value
        ]
    if isinstance(value, str):
        if _is_path_field_name(key):
            candidate = Path(value)
            if candidate.is_absolute() or PureWindowsPath(value).is_absolute():
                return _safe_path_display(value, workspace)
        if contains_credential(value):
            return "[redacted]"
        safe_value = _redact_local_path_substrings(value)
        if _contains_local_runtime_leak_outside_web_urls(safe_value):
            return "[redacted]"
        if _is_path_field_name(key):
            return _safe_path_display(safe_value, workspace)
        return safe_value
    return value


def _public_event(event: WorkbenchEvent, workspace: Path) -> PublicEvent:
    allowed = _PUBLIC_EVENT_FIELDS.get(event.event_type, frozenset())
    payload = {
        key: _safe_public_value(value, key=key, workspace=workspace)
        for key, value in event.payload.items()
        if key in allowed and not is_sensitive_field_name(key)
    }
    return PublicEvent(
        id=event.id,
        turn_id=event.turn_id,
        sequence=event.sequence,
        event_type=event.event_type,
        payload=payload,
        created_at=event.created_at,
    )


def _public_artifact(task_id: str, artifact: WorkbenchArtifact) -> PublicArtifact:
    return PublicArtifact(
        id=artifact.id,
        turn_id=artifact.turn_id,
        label=artifact.label,
        media_type=artifact.media_type,
        created_at=artifact.created_at,
        download_url=(
            f"/api/workbench/tasks/{task_id}/turns/{artifact.turn_id}"
            f"/artifacts/{artifact.id}/download"
        ),
    )


def _public_confirmation(
    store: WorkbenchStore, confirmation: WorkbenchConfirmation
) -> PublicConfirmation:
    try:
        canonical_targets = json.loads(confirmation.canonical_targets_json)
    except json.JSONDecodeError:
        canonical_targets = []
    if not isinstance(canonical_targets, list) or not all(
        isinstance(item, str) for item in canonical_targets
    ):
        canonical_targets = []
    return PublicConfirmation(
        id=confirmation.id,
        turn_id=confirmation.turn_id,
        action_kind=confirmation.action_kind,
        target=confirmation.target,
        summary=confirmation.summary,
        risk=confirmation.risk,
        canonical_capability=confirmation.canonical_capability,
        canonical_operation=confirmation.canonical_operation,
        canonical_targets=canonical_targets,
        status=confirmation.status,
        decision_requested=confirmation.decision_requested,
        decision_requested_at=confirmation.decision_requested_at,
        proposer_quiesced=bool(store.confirmation_is_quiesced(confirmation.id)),
        created_at=confirmation.created_at,
        decided_at=confirmation.decided_at,
    )


def _owned_turn(store: WorkbenchStore, task_id: str, turn_id: str) -> WorkbenchTurn:
    task = store.get_task(task_id)
    turn = store.get_turn(turn_id)
    if task is None or turn is None or turn.task_id != task_id:
        raise _not_found()
    return turn


def _owned_confirmation(
    store: WorkbenchStore, task_id: str, turn_id: str, confirmation_id: str
) -> WorkbenchConfirmation:
    _owned_turn(store, task_id, turn_id)
    confirmation = store.get_confirmation(confirmation_id)
    if confirmation is None or confirmation.turn_id != turn_id:
        raise _not_found()
    return confirmation


def _media_type_permitted(media_type: str) -> bool:
    normalized = media_type.casefold()
    if ";" in normalized or any(ord(char) < 33 for char in normalized):
        return False
    return normalized.startswith(("text/", "image/")) or normalized in {
        "application/json",
        "application/pdf",
        "application/zip",
    }


def _decode_attachment(payload: _AttachmentUpload) -> bytes:
    if not _media_type_permitted(payload.media_type):
        raise ValueError("attachment media type is not permitted")
    try:
        content = base64.b64decode(payload.content_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("content_base64 must be strict Base64") from exc
    if len(content) > _MAX_ATTACHMENT_BYTES:
        raise ValueError("attachment exceeds 20 MiB")
    return content


def encode_sse(event: PublicEvent) -> bytes:
    data = json.dumps(
        event.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )
    return f"id: {event.id}\nevent: {event.event_type}\ndata: {data}\n\n".encode()


def _parse_event_cursor(request: Request) -> int:
    raw = request.headers.get("last-event-id")
    if raw is None:
        raw = request.query_params.get("after", "0")
    try:
        cursor = int(raw)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Invalid event cursor") from exc
    if cursor < 0 or str(cursor) != str(raw).strip():
        raise HTTPException(status_code=400, detail="Invalid event cursor")
    return cursor


@dataclass(frozen=True, slots=True)
class _OpenedArtifact:
    fd: int
    size: int
    basename: str


def _open_artifact_fd(path: str, roots: Sequence[Path]) -> _OpenedArtifact:
    candidate = Path(path)
    if not candidate.is_absolute() or ".." in candidate.parts:
        raise _not_found()
    matching_root = next(
        (root for root in roots if root in candidate.parents),
        None,
    )
    if matching_root is None:
        raise _not_found()
    parts = candidate.relative_to(matching_root).parts
    if not parts:
        raise _not_found()
    directory_fd = None
    artifact_fd = None
    try:
        directory_fd = os.open(
            matching_root,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
        )
        for component in parts[:-1]:
            child_fd = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=directory_fd,
            )
            os.close(directory_fd)
            directory_fd = child_fd
        artifact_fd = os.open(
            parts[-1],
            os.O_RDONLY | os.O_NOFOLLOW,
            dir_fd=directory_fd,
        )
        metadata = os.fstat(artifact_fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise _not_found()
        result = _OpenedArtifact(
            fd=artifact_fd,
            size=metadata.st_size,
            basename=parts[-1],
        )
        artifact_fd = None
        return result
    except (OSError, HTTPException) as exc:
        if isinstance(exc, HTTPException):
            raise
        raise _not_found() from exc
    finally:
        if artifact_fd is not None:
            os.close(artifact_fd)
        if directory_fd is not None:
            os.close(directory_fd)


def _artifact_chunks(fd: int, *, chunk_size: int = 64 * 1024):
    try:
        while True:
            chunk = os.read(fd, chunk_size)
            if not chunk:
                return
            yield chunk
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


def register_workbench_routes(
    app: FastAPI,
    store: WorkbenchStore,
    executor: WorkbenchExecutor,
    runtime_registry: RuntimeRegistry,
    asset_dir: Path,
    *,
    mutation_guard: Callable[[Request], None],
    stream_guard: Callable[[Request], None] | None = None,
    artifact_roots: Sequence[Path] = (),
    sse_poll_seconds: float = 1.0,
    sse_keepalive_seconds: float = 15.0,
    scheduler_interval_seconds: float = 1.0,
) -> WorkbenchScheduler:
    del asset_dir
    if sse_poll_seconds <= 0 or sse_keepalive_seconds < 15:
        raise ValueError("invalid SSE timing")
    broker = EventBroker()
    scheduler = WorkbenchScheduler(
        executor, interval_seconds=scheduler_interval_seconds
    )
    roots = tuple(
        dict.fromkeys(
            Path(root).resolve()
            for root in (
                executor.workspace,
                store.path.parent / "workbench" / "outputs",
                *artifact_roots,
            )
        )
    )
    app.state.workbench_event_broker = broker
    app.state.workbench_scheduler = scheduler

    @app.get("/api/workbench/tasks", response_model=list[PublicTask])
    def list_tasks(
        include_archived: bool = False,
        archived: Literal["active", "archived", "all"] | None = None,
    ) -> list[PublicTask]:
        if archived == "archived":
            tasks = [
                task
                for task in store.list_tasks(include_archived=True)
                if task.archived_at
            ]
        else:
            tasks = store.list_tasks(
                include_archived=(
                    archived == "all" if archived is not None else include_archived
                )
            )
        return [_public_task(store, task) for task in tasks]

    @app.post("/api/workbench/tasks", response_model=PublicTask, status_code=201)
    async def create_task(request: Request) -> PublicTask:
        payload = await _request_model(
            request, _CreateTask, mutation_guard=mutation_guard
        )
        assert isinstance(payload, _CreateTask)
        if payload.runtime_kind not in runtime_registry.kinds():
            raise HTTPException(status_code=400, detail="Unsupported runtime kind")
        try:
            return _public_task(
                store,
                store.create_task(
                    title=payload.title, runtime_kind=payload.runtime_kind
                ),
            )
        except ValueError as exc:
            raise _public_error(400, "unknown") from exc

    @app.get("/api/workbench/tasks/{task_id}", response_model=PublicTask)
    def get_task(task_id: UUID) -> PublicTask:
        task = store.get_task(_uuid_text(task_id))
        if task is None:
            raise _not_found()
        return _public_task(store, task)

    @app.patch("/api/workbench/tasks/{task_id}", response_model=PublicTask)
    async def rename_task(task_id: UUID, request: Request) -> PublicTask:
        payload = await _request_model(
            request, _RenameTask, mutation_guard=mutation_guard
        )
        assert isinstance(payload, _RenameTask)
        if store.get_task(_uuid_text(task_id)) is None:
            raise _not_found()
        try:
            return _public_task(
                store, store.rename_task(_uuid_text(task_id), title=payload.title)
            )
        except ValueError as exc:
            raise _public_error(400, "unknown") from exc

    @app.post("/api/workbench/tasks/{task_id}/archive", response_model=PublicTask)
    async def archive_task(task_id: UUID, request: Request) -> PublicTask:
        await _request_model(request, _StrictModel, mutation_guard=mutation_guard)
        if store.get_task(_uuid_text(task_id)) is None:
            raise _not_found()
        try:
            return _public_task(store, store.archive_task(_uuid_text(task_id)))
        except WorkbenchConflictError as exc:
            raise _public_error(409, exc.code) from exc
        except ValueError as exc:
            raise _public_error(409, "unknown") from exc

    @app.get(
        "/api/workbench/tasks/{task_id}/attachments",
        response_model=list[WorkbenchAttachment],
    )
    def list_attachments(task_id: UUID) -> list[WorkbenchAttachment]:
        try:
            return store.list_attachments(_uuid_text(task_id))
        except ValueError as exc:
            raise _not_found() from exc

    @app.post(
        "/api/workbench/tasks/{task_id}/attachments",
        response_model=WorkbenchAttachment,
        status_code=201,
    )
    async def upload_attachment(task_id: UUID, request: Request) -> WorkbenchAttachment:
        payload = await _request_model(
            request,
            _AttachmentUpload,
            mutation_guard=mutation_guard,
            max_bytes=_MAX_ATTACHMENT_JSON_BYTES,
        )
        assert isinstance(payload, _AttachmentUpload)
        if store.get_task(_uuid_text(task_id)) is None:
            raise _not_found()
        try:
            content = _decode_attachment(payload)
            return store.save_attachment(
                _uuid_text(task_id),
                filename=payload.filename,
                media_type=payload.media_type,
                content=content,
            )
        except ValueError as exc:
            raise _public_error(400, "attachment_invalid") from exc

    @app.post(
        "/api/workbench/tasks/{task_id}/turns",
        response_model=PublicTurn,
        status_code=201,
    )
    async def create_turn(task_id: UUID, request: Request) -> PublicTurn:
        payload = await _request_model(
            request, _CreateTurn, mutation_guard=mutation_guard
        )
        assert isinstance(payload, _CreateTurn)
        task_id_text = _uuid_text(task_id)
        if store.get_task(task_id_text) is None:
            raise _not_found()
        try:
            turn = store.create_turn(
                task_id_text,
                user_text=payload.text,
                client_request_id=payload.client_request_id,
            )
            broker.notify(turn.id)
            scheduler.wake()
            return _public_turn(turn)
        except WorkbenchConflictError as exc:
            raise _public_error(409, exc.code) from exc
        except ValueError as exc:
            raise _public_error(409, "unknown") from exc

    @app.get(
        "/api/workbench/tasks/{task_id}/turns/{turn_id}",
        response_model=PublicTurn,
    )
    def get_nested_turn(task_id: UUID, turn_id: UUID) -> PublicTurn:
        return _public_turn(
            _owned_turn(store, _uuid_text(task_id), _uuid_text(turn_id))
        )

    @app.get("/api/workbench/turns/{turn_id}", response_model=PublicTurn)
    def get_turn(turn_id: UUID) -> PublicTurn:
        turn = store.get_turn(_uuid_text(turn_id))
        if turn is None:
            raise _not_found()
        return _public_turn(turn)

    @app.post(
        "/api/workbench/tasks/{task_id}/turns/{turn_id}/stop",
        response_model=PublicTurn,
    )
    async def stop_turn(task_id: UUID, turn_id: UUID, request: Request) -> PublicTurn:
        await _request_model(request, _StrictModel, mutation_guard=mutation_guard)
        turn_id_text = _uuid_text(turn_id)
        _owned_turn(store, _uuid_text(task_id), turn_id_text)
        try:
            result = executor.stop(turn_id_text)
            broker.notify(turn_id_text)
            return _public_turn(result)
        except ValueError as exc:
            raise _public_error(409, "unknown") from exc

    @app.get("/api/workbench/turns/{turn_id}/events", response_model=list[PublicEvent])
    def replay_events(
        turn_id: UUID,
        after: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=1000),
    ) -> list[PublicEvent]:
        try:
            return [
                _public_event(event, executor.workspace)
                for event in store.events_after(
                    _uuid_text(turn_id), after_id=after, limit=limit
                )
            ]
        except ValueError as exc:
            raise _not_found() from exc

    @app.get("/api/workbench/tasks/{task_id}/timeline", response_model=PublicTimeline)
    def timeline(task_id: UUID) -> PublicTimeline:
        task_id_text = _uuid_text(task_id)
        task = store.get_task(task_id_text)
        if task is None:
            raise _not_found()
        turns = store.list_turns(task_id_text)
        events = [
            event for turn in turns for event in store.events_after(turn.id, limit=1000)
        ]
        return PublicTimeline(
            task=_public_task(store, task),
            turns=[_public_turn(turn) for turn in turns],
            events=[_public_event(event, executor.workspace) for event in events],
            attachments=store.list_attachments(task_id_text),
            artifacts=[
                _public_artifact(task_id_text, artifact)
                for artifact in store.list_artifacts(task_id_text)
            ],
            confirmations=[
                _public_confirmation(store, confirmation)
                for confirmation in store.list_confirmations(task_id_text)
            ],
        )

    @app.post(
        "/api/workbench/tasks/{task_id}/turns/{turn_id}/confirmations/{confirmation_id}/confirm",
        response_model=PublicConfirmation,
    )
    async def confirm(
        task_id: UUID, turn_id: UUID, confirmation_id: UUID, request: Request
    ) -> PublicConfirmation:
        await _request_model(request, _StrictModel, mutation_guard=mutation_guard)
        confirmation_id_text = _uuid_text(confirmation_id)
        _owned_confirmation(
            store, _uuid_text(task_id), _uuid_text(turn_id), confirmation_id_text
        )
        try:
            result = executor.confirm(confirmation_id_text)
            broker.notify(_uuid_text(turn_id))
            scheduler.wake()
            return _public_confirmation(store, result)
        except ValueError as exc:
            raise _public_error(409, "unknown") from exc

    @app.post(
        "/api/workbench/tasks/{task_id}/turns/{turn_id}/confirmations/{confirmation_id}/cancel",
        response_model=PublicConfirmation,
    )
    async def cancel(
        task_id: UUID, turn_id: UUID, confirmation_id: UUID, request: Request
    ) -> PublicConfirmation:
        await _request_model(request, _StrictModel, mutation_guard=mutation_guard)
        confirmation_id_text = _uuid_text(confirmation_id)
        _owned_confirmation(
            store, _uuid_text(task_id), _uuid_text(turn_id), confirmation_id_text
        )
        try:
            result = executor.cancel(confirmation_id_text)
            broker.notify(_uuid_text(turn_id))
            scheduler.wake()
            return _public_confirmation(store, result)
        except ValueError as exc:
            raise _public_error(409, "unknown") from exc

    @app.get(
        "/api/workbench/runtimes", response_model=list[RuntimeCapabilitiesResponse]
    )
    def runtime_capabilities() -> list[RuntimeCapabilitiesResponse]:
        return [
            RuntimeCapabilitiesResponse(
                kind=kind,
                capabilities=asdict(runtime_registry.get(kind).capabilities()),
            )
            for kind in runtime_registry.kinds()
        ]

    @app.get("/api/workbench/stats", response_model=WorkbenchStatsResponse)
    def stats_response() -> WorkbenchStatsResponse:
        return WorkbenchStatsResponse.model_validate(store.workbench_stats())

    @app.get(
        "/api/workbench/tasks/{task_id}/turns/{turn_id}/artifacts/{artifact_id}/download"
    )
    def download_artifact(
        task_id: UUID, turn_id: UUID, artifact_id: UUID
    ) -> StreamingResponse:
        task_id_text = _uuid_text(task_id)
        turn_id_text = _uuid_text(turn_id)
        _owned_turn(store, task_id_text, turn_id_text)
        artifact = store.get_artifact(_uuid_text(artifact_id))
        if artifact is None or artifact.turn_id != turn_id_text:
            raise _not_found()
        opened = _open_artifact_fd(artifact.path, roots)
        filename = Path(artifact.label).name.strip() or opened.basename
        if any(ord(char) < 32 for char in filename):
            filename = opened.basename
        disposition = f"attachment; filename*=UTF-8''{quote(filename, safe='')}"
        media_type = (
            artifact.media_type
            if _media_type_permitted(artifact.media_type)
            else "application/octet-stream"
        )
        try:
            return StreamingResponse(
                _artifact_chunks(opened.fd),
                media_type=media_type,
                headers={
                    "Content-Disposition": disposition,
                    "Content-Length": str(opened.size),
                    "X-Content-Type-Options": "nosniff",
                },
            )
        except Exception:
            os.close(opened.fd)
            raise

    @app.get("/api/workbench/turns/{turn_id}/events/stream")
    async def event_stream(turn_id: UUID, request: Request) -> StreamingResponse:
        if stream_guard is not None:
            stream_guard(request)
        turn_id_text = _uuid_text(turn_id)
        if store.get_turn(turn_id_text) is None:
            raise _not_found()
        cursor = _parse_event_cursor(request)

        async def generate():
            nonlocal cursor
            loop = asyncio.get_running_loop()
            last_activity = loop.time()
            subscription: _Subscription | None = None
            try:
                initial = store.events_after(turn_id_text, cursor, limit=1000)
                for event in initial:
                    public = _public_event(event, executor.workspace)
                    yield encode_sse(public)
                    cursor = event.id
                    last_activity = loop.time()
                subscription = broker.subscribe(turn_id_text)
                while True:
                    persisted = store.events_after(turn_id_text, cursor, limit=1000)
                    for event in persisted:
                        public = _public_event(event, executor.workspace)
                        yield encode_sse(public)
                        cursor = event.id
                        last_activity = loop.time()
                    turn = store.get_turn(turn_id_text)
                    if turn is None:
                        return
                    if turn.status in _TERMINAL_TURN_STATUSES:
                        post_terminal = store.events_after(
                            turn_id_text, cursor, limit=1000
                        )
                        for event in post_terminal:
                            public = _public_event(event, executor.workspace)
                            yield encode_sse(public)
                            cursor = event.id
                            last_activity = loop.time()
                        if post_terminal:
                            continue
                        return
                    elapsed = loop.time() - last_activity
                    if elapsed >= sse_keepalive_seconds:
                        yield b": keepalive\n\n"
                        last_activity = loop.time()
                    await subscription.wait(
                        min(
                            sse_poll_seconds,
                            max(
                                0.001,
                                sse_keepalive_seconds - (loop.time() - last_activity),
                            ),
                        )
                    )
            finally:
                if subscription is not None:
                    subscription.close()

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Content-Security-Policy": "default-src 'none'",
            },
        )

    return scheduler
