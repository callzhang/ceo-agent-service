"""Bounded, lease-owned Workbench execution."""

from __future__ import annotations

import re
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.store import _utc_store_time
from app.workbench.models import (
    TurnStatus,
    WorkbenchTurn,
)
from app.workbench.runtime import (
    RuntimeEvent,
    RuntimeHandle,
    RuntimeRegistry,
    RuntimeRequest,
    RuntimeResult,
)
from app.workbench.store import WorkbenchStore


_MAX_CLAIMS = 2
_RECOVERY_SQLITE_LOCK_RETRY_ATTEMPTS = 3
_RECOVERY_SQLITE_LOCK_RETRY_DELAY_SECONDS = 0.1
_RUNTIME_FAILURE = "Runtime execution could not be completed."
_KNOWN_RUNTIME_FAILURE_DETAILS = {
    "provider_output_limit": (
        "Codex provider output exceeded the 16 MiB Workbench safety limit."
    ),
    "provider_timeout": "Codex provider exceeded the Workbench execution timeout.",
    "provider_process_failed": "Codex provider process exited unsuccessfully.",
    "provider_turn_failed": "Codex provider reported that the turn failed.",
    "incomplete_provider_output": (
        "Codex provider exited without a terminal completion event."
    ),
    "missing_provider_session": (
        "Codex provider completed without returning a session identifier."
    ),
    "invalid_provider_output": "Codex provider returned invalid JSONL output.",
    "runtime_unavailable": "The requested runtime is not available in this service.",
}


def _public_runtime_failure_detail(error_code: str) -> str:
    return _KNOWN_RUNTIME_FAILURE_DETAILS.get(error_code, _RUNTIME_FAILURE)


@dataclass
class _RunState:
    turn_id: str
    task_id: str
    runtime: Any
    next_sequence: int
    handle: RuntimeHandle | None = None
    stop_dispatched: bool = False
    lease_lost: bool = False
    shutdown_requested: bool = False
    quiesced: threading.Event = field(default_factory=threading.Event)
    heartbeat_stop: threading.Event = field(default_factory=threading.Event)
    lock: threading.RLock = field(default_factory=threading.RLock)


class WorkbenchExecutor:
    def __init__(
        self,
        store: WorkbenchStore,
        runtimes: RuntimeRegistry,
        *,
        workspace: Path,
        lease_seconds: int = 300,
        heartbeat_interval_seconds: float | None = None,
        artifact_roots: tuple[Path, ...] = (),
    ) -> None:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        self.store = store
        self.runtimes = runtimes
        self.workspace = Path(workspace).resolve()
        self.artifact_roots = tuple(
            dict.fromkeys(
                (
                    self.workspace,
                    (store.path.parent / "workbench" / "outputs").resolve(),
                    *(Path(root).resolve() for root in artifact_roots),
                )
            )
        )
        self.lease_seconds = lease_seconds
        self.heartbeat_interval_seconds = heartbeat_interval_seconds or max(
            0.1, min(30.0, lease_seconds / 3)
        )
        self.owner = str(uuid4())
        self._pool = ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="workbench-executor"
        )
        self._states: dict[str, _RunState] = {}
        self._task_locks: dict[str, threading.Lock] = {}
        self._map_lock = threading.RLock()
        self._schedule_lock = threading.Lock()
        self._reserved_turns = 0
        self._reserved_turn_ids: set[str] = set()
        self._closed = False

    def recover(self, *, now=None) -> int:
        recovery_now, _ = _utc_store_time(now)
        return self._retry_transient_sqlite_lock(
            lambda: self.store.recover_expired_turns(now=recovery_now)
        )

    @staticmethod
    def _retry_transient_sqlite_lock(operation):
        for attempt in range(_RECOVERY_SQLITE_LOCK_RETRY_ATTEMPTS):
            try:
                return operation()
            except sqlite3.OperationalError as exc:
                message = str(exc).casefold()
                if (
                    "database is locked" not in message
                    and "database is busy" not in message
                ) or attempt + 1 >= _RECOVERY_SQLITE_LOCK_RETRY_ATTEMPTS:
                    raise
                time.sleep(_RECOVERY_SQLITE_LOCK_RETRY_DELAY_SECONDS)
        raise RuntimeError("SQLite recovery retry loop exhausted")

    def run_once(self, *, max_turns: int = _MAX_CLAIMS) -> list[str]:
        if max_turns < 1 or max_turns > _MAX_CLAIMS:
            raise ValueError("max_turns must be between 1 and 2")
        with self._map_lock:
            if self._closed:
                raise RuntimeError("workbench executor is closed")
        claimed: list[WorkbenchTurn] = []
        futures = []
        with self._schedule_lock:
            capacity = min(max_turns, _MAX_CLAIMS - self._reserved_turns)
            for _ in range(capacity):
                execution_run_id = str(uuid4())
                turn = self.store.claim_next_turn(
                    owner=self.owner,
                    execution_run_id=execution_run_id,
                    lease_seconds=self.lease_seconds + 1,
                )
                if turn is None:
                    break
                self._reserved_turns += 1
                self._reserved_turn_ids.add(turn.id)
                claimed.append(turn)
                try:
                    futures.append(self._pool.submit(self._execute_turn_reserved, turn))
                except Exception as exc:
                    self._reserved_turns -= 1
                    self._reserved_turn_ids.discard(turn.id)
                    self._fail_claimed(
                        turn.id,
                        "executor_submit_failed",
                        detail=f"{type(exc).__name__}: {exc}",
                    )
        for future in futures:
            future.result()
        return [turn.id for turn in claimed]

    def stop(self, turn_id: str) -> WorkbenchTurn:
        turn = self.store.request_stop(turn_id)
        with self._map_lock:
            state = self._states.get(turn_id)
        if state is not None:
            self._stop_state_once(state)
        return self.store.get_turn(turn_id) or turn

    def close(self) -> bool:
        with self._map_lock:
            if self._closed:
                active = bool(self._states)
                with self._schedule_lock:
                    return self._reserved_turns == 0 and not active
            self._closed = True
            states = tuple(self._states.values())
        with self._schedule_lock:
            reserved_turn_ids = tuple(self._reserved_turn_ids)
        for turn_id in reserved_turn_ids:
            try:
                self.store.request_stop(turn_id)
            except ValueError:
                pass
        for state in states:
            state.heartbeat_stop.set()
            state.shutdown_requested = True
            try:
                self.store.request_stop(state.turn_id)
            except ValueError:
                pass
            self._stop_state_once(state)
        self._pool.shutdown(wait=False, cancel_futures=True)
        deadline = time.monotonic() + 0.5
        for state in states:
            state.quiesced.wait(timeout=max(0.0, deadline - time.monotonic()))
        with self._map_lock:
            active = bool(self._states)
        with self._schedule_lock:
            return self._reserved_turns == 0 and not active

    def _execute_turn(self, turn: WorkbenchTurn) -> None:
        task = self.store.get_task(turn.task_id)
        if task is None:
            self._fail_claimed(turn.id, "workbench_task_missing")
            return
        with self._map_lock:
            task_lock = self._task_locks.setdefault(turn.task_id, threading.Lock())
        with task_lock:
            try:
                runtime = self.runtimes.get(task.runtime_kind)
            except KeyError:
                self._fail_claimed(turn.id, "runtime_unavailable")
                return
            events = self.store.events_after(turn.id)
            state = _RunState(
                turn_id=turn.id,
                task_id=turn.task_id,
                runtime=runtime,
                next_sequence=(events[-1].sequence + 1 if events else 1),
            )
            with self._map_lock:
                self._states[turn.id] = state
                closed_before_start = self._closed
            if closed_before_start:
                state.shutdown_requested = True
                self.store.request_stop(turn.id)
                state.quiesced.set()
                with self._map_lock:
                    self._states.pop(turn.id, None)
                return
            heartbeat = threading.Thread(
                target=self._heartbeat,
                args=(state,),
                name=f"workbench-heartbeat-{turn.id}",
                daemon=True,
            )
            heartbeat.start()
            try:
                prompt = turn.user_text
                resume_context = self.store.resume_context_for_executor(
                    turn.id, owner=self.owner
                )
                if resume_context:
                    prompt = f"{prompt}\n\nResume context: {resume_context}"
                attachment_paths, image_paths = self._validated_inputs(turn.id, runtime)
                request = RuntimeRequest(
                    turn_id=turn.id,
                    workspace=self.workspace,
                    prompt=prompt,
                    conversation_id=turn.task_id,
                    provider_session_ref=task.provider_session_ref,
                    attachment_paths=attachment_paths,
                    image_paths=image_paths,
                )
                handle = runtime.start(
                    request, on_event=lambda event: self._on_event(state, event)
                )
                with state.lock:
                    state.handle = handle
                with self._map_lock:
                    closed = self._closed
                current = self.store.get_turn(turn.id)
                if (
                    closed
                    or state.shutdown_requested
                    or state.lease_lost
                    or (current is not None and current.stop_requested)
                ):
                    self._stop_state_once(state)
                result = runtime.wait(handle)
                self._finish_runtime(state, result)
            except Exception:
                self._stop_state_once(state)
                self._fail_claimed(
                    turn.id,
                    "runtime_failure",
                    state=state,
                )
            finally:
                state.heartbeat_stop.set()
                heartbeat.join(timeout=max(1.0, self.heartbeat_interval_seconds * 2))
                if heartbeat.is_alive():
                    state.lease_lost = True
                with self._map_lock:
                    self._states.pop(turn.id, None)
                state.quiesced.set()

    def _execute_turn_reserved(self, turn: WorkbenchTurn) -> None:
        try:
            with self.store.reuse_connection():
                self._execute_turn(turn)
        finally:
            with self._schedule_lock:
                self._reserved_turns -= 1
                self._reserved_turn_ids.discard(turn.id)

    def _heartbeat(self, state: _RunState) -> None:
        while not state.heartbeat_stop.wait(self.heartbeat_interval_seconds):
            try:
                self.store.renew_turn_lease(
                    state.turn_id,
                    owner=self.owner,
                    lease_seconds=self.lease_seconds + 1,
                )
            except Exception:
                try:
                    current = self.store.get_turn(state.turn_id)
                except Exception:
                    state.lease_lost = True
                    self._stop_state_once(state)
                    return
                if current is not None and current.status in {
                    TurnStatus.COMPLETED,
                    TurnStatus.STOPPED,
                    TurnStatus.FAILED,
                }:
                    return
                state.lease_lost = True
                self._stop_state_once(state)
                return

    def _on_event(self, state: _RunState, event: RuntimeEvent) -> None:
        if not isinstance(event, RuntimeEvent):
            raise ValueError("malformed runtime event")
        with state.lock:
            if state.lease_lost:
                return
            current = self.store.get_turn(state.turn_id)
            if current is None or current.status is not TurnStatus.RUNNING:
                return
            if event.event_type in {"turn_completed", "turn_failed"}:
                return
            payload = event.payload_json_value()
            try:
                if event.event_type == "artifact_created":
                    self._append_artifact_event(state, payload)
                else:
                    self.store.append_event(
                        state.turn_id,
                        sequence=state.next_sequence,
                        event_type=event.event_type,
                        payload=payload,
                        owner=self.owner,
                    )
            except ValueError:
                current = self.store.get_turn(state.turn_id)
                if current is not None and current.status is TurnStatus.STOPPED:
                    return
                raise
            state.next_sequence += 1

    def _append_artifact_event(self, state: _RunState, payload: dict[str, Any]) -> None:
        if set(payload) != {"label", "path", "media_type"} or not all(
            isinstance(payload.get(key), str) for key in payload
        ):
            raise ValueError("invalid artifact event")
        candidate = Path(payload["path"])
        if not candidate.is_absolute() or ".." in candidate.parts:
            raise ValueError("invalid artifact path")
        token = r"[!#$&^_.+\-A-Za-z0-9]+"
        media_type = payload["media_type"]
        if (
            len(media_type) > 100
            or not media_type.isascii()
            or re.fullmatch(rf"{token}/{token}", media_type) is None
        ):
            raise ValueError("invalid artifact media type")
        try:
            root = next(
                (root for root in self.artifact_roots if root in candidate.parents),
                None,
            )
            if root is None:
                raise ValueError("invalid artifact path")
            relative = candidate.relative_to(root)
            current = root
            for component in relative.parts:
                current = current / component
                if current.is_symlink():
                    raise ValueError("invalid artifact path")
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(root)
        except (OSError, ValueError) as exc:
            raise ValueError("invalid artifact path") from exc
        if not resolved.is_file():
            raise ValueError("invalid artifact path")
        self.store.append_artifact_event(
            state.turn_id,
            sequence=state.next_sequence,
            label=payload["label"],
            path=str(resolved),
            media_type=payload["media_type"],
            owner=self.owner,
        )

    def _finish_runtime(self, state: _RunState, result: RuntimeResult) -> None:
        if not isinstance(result, RuntimeResult):
            raise ValueError("malformed runtime result")
        current = self.store.get_turn(state.turn_id)
        if (
            current is None
            or state.lease_lost
            or current.status is not TurnStatus.RUNNING
        ):
            return
        if current.stop_requested or result.status == "stopped":
            target = TurnStatus.STOPPED
        elif result.status == "completed":
            target = TurnStatus.COMPLETED
        else:
            target = TurnStatus.FAILED
        if result.provider_session_ref:
            self.store.set_provider_session(
                state.turn_id, result.provider_session_ref, owner=self.owner
            )
        self.store.complete_turn(
            state.turn_id,
            status=target,
            final_text=(result.final_text if target is TurnStatus.COMPLETED else ""),
            error_code=(
                result.error_code or "runtime_failure"
                if target is TurnStatus.FAILED
                else ""
            ),
            error_detail=(
                _public_runtime_failure_detail(result.error_code or "runtime_failure")
                if target is TurnStatus.FAILED
                else ""
            ),
            event_payload={"status": target.value},
            owner=self.owner,
        )

    def _fail_claimed(
        self,
        turn_id: str,
        code: str,
        *,
        state: _RunState | None = None,
    ) -> None:
        if state is not None and state.lease_lost:
            return
        current = self.store.get_turn(turn_id)
        if current is None or current.status is not TurnStatus.RUNNING:
            return
        try:
            self.store.complete_turn(
                turn_id,
                status=(
                    TurnStatus.STOPPED if current.stop_requested else TurnStatus.FAILED
                ),
                error_code=("" if current.stop_requested else code),
                error_detail=(
                    ""
                    if current.stop_requested
                    else _public_runtime_failure_detail(code)
                ),
                owner=self.owner,
            )
        except ValueError:
            if state is not None:
                state.lease_lost = True

    def _stop_state_once(self, state: _RunState) -> None:
        with state.lock:
            if state.stop_dispatched or state.handle is None:
                return
            state.stop_dispatched = True
            handle = state.handle
        try:
            state.runtime.stop(handle)
        except Exception:
            pass

    def _validated_inputs(
        self, turn_id: str, runtime: Any
    ) -> tuple[tuple[Path, ...], tuple[Path, ...]]:
        attachments: list[Path] = []
        images: list[Path] = []
        supports_images = runtime.capabilities().image_input
        for path, media_type in self.store.attachment_paths_for_executor(
            turn_id, owner=self.owner
        ):
            try:
                path.lstat()
                resolved = path.resolve(strict=True)
            except (OSError, RuntimeError) as exc:
                raise ValueError("invalid workbench attachment") from exc
            if path.is_symlink() or not path.is_file() or resolved != path.absolute():
                raise ValueError("invalid workbench attachment")
            attachment_root = self.store.path.parent / "workbench" / "attachments"
            if not resolved.is_relative_to(attachment_root.resolve()):
                raise ValueError("invalid workbench attachment")
            if media_type.startswith("image/") and supports_images:
                images.append(resolved)
            else:
                attachments.append(resolved)
        return tuple(attachments), tuple(images)

