"""Bounded, lease-owned execution and confirmation orchestration."""

from __future__ import annotations

import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.agent_cli import (
    ReviewedWriteAuthorization,
    execute_reviewed_write,
    review_write_authorization,
)
from app.history import safe_observability_error
from app.leak_check import assert_no_credentials
from app.native_cli_metadata import NativeCliMetadataClassifier
from app.workbench.models import (
    ConfirmationStatus,
    TurnStatus,
    WorkbenchConfirmation,
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
_SAFE_RUNTIME_FAILURE = "Runtime execution could not be completed safely."


@dataclass
class _RunState:
    turn_id: str
    task_id: str
    runtime: Any
    next_sequence: int
    handle: RuntimeHandle | None = None
    stop_dispatched: bool = False
    confirmation_created: bool = False
    lease_lost: bool = False
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
        classifier: NativeCliMetadataClassifier | None = None,
        write_runner=None,
    ) -> None:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        self.store = store
        self.runtimes = runtimes
        self.workspace = Path(workspace).resolve()
        self.lease_seconds = lease_seconds
        self.heartbeat_interval_seconds = heartbeat_interval_seconds or max(
            0.1, min(30.0, lease_seconds / 3)
        )
        self.owner = str(uuid4())
        self.classifier = classifier
        self.write_runner = write_runner
        self._pool = ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="workbench-executor"
        )
        self._states: dict[str, _RunState] = {}
        self._task_locks: dict[str, threading.Lock] = {}
        self._map_lock = threading.RLock()
        self._closed = False
        self.recover()

    def recover(self, *, now=None) -> int:
        ambiguous = self.store.reconcile_confirmed_without_result(now=now)
        return ambiguous + self.store.recover_expired_turns(now=now)

    def run_once(self, *, max_turns: int = _MAX_CLAIMS) -> list[str]:
        if max_turns < 1 or max_turns > _MAX_CLAIMS:
            raise ValueError("max_turns must be between 1 and 2")
        with self._map_lock:
            if self._closed:
                raise RuntimeError("workbench executor is closed")
        self.recover()
        claimed: list[WorkbenchTurn] = []
        for _ in range(max_turns):
            # Store timestamps have one-second precision; one extra second prevents
            # the persisted lease from becoming equal to "now" between heartbeats.
            turn = self.store.claim_next_turn(
                owner=self.owner, lease_seconds=self.lease_seconds + 1
            )
            if turn is None:
                break
            claimed.append(turn)
        futures = [self._pool.submit(self._execute_turn, turn) for turn in claimed]
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

    def confirm(self, confirmation_id: str) -> WorkbenchConfirmation:
        claimed = self.store.claim_confirmation_execution(confirmation_id)
        if claimed is None:
            existing = self.store.get_confirmation(confirmation_id)
            if existing is None:
                raise ValueError("workbench confirmation does not exist")
            if existing.status is ConfirmationStatus.CANCELLED:
                raise ValueError("confirmation has already been decided")
            return existing
        try:
            arguments = json.loads(claimed.arguments_json)
            if not isinstance(arguments, dict):
                raise ValueError("invalid confirmation arguments")
            argv = arguments.get("argv")
            action_index = arguments.get("action_index", 0)
            if not isinstance(argv, list) or not all(
                isinstance(item, str) for item in argv
            ):
                raise ValueError("invalid confirmation arguments")
            authorization: ReviewedWriteAuthorization = review_write_authorization(
                argv,
                authorization_id=claimed.id,
                action_index=action_index,
                classifier=self.classifier,
            )
            receipt = execute_reviewed_write(
                argv,
                authorization_id=claimed.id,
                action_index=action_index,
                authorization=authorization,
                classifier=self.classifier,
                process_runner=self.write_runner,
            )
            failed = isinstance(receipt.get("error"), dict)
            safe_receipt = self._safe_receipt(
                receipt, failed=failed, target_summary=claimed.target
            )
            return self.store.finish_confirmation_execution(
                claimed.id,
                status=(
                    ConfirmationStatus.FAILED if failed else ConfirmationStatus.EXECUTED
                ),
                result_json=safe_receipt,
                resume_context=self._receipt_resume_context(safe_receipt),
            )
        except Exception:
            safe_receipt = self._safe_receipt(
                {
                    "error": {
                        "code": "reviewed_write_failed",
                        "retryable": False,
                    }
                },
                failed=True,
                target_summary=claimed.target,
            )
            return self.store.finish_confirmation_execution(
                claimed.id,
                status=ConfirmationStatus.FAILED,
                result_json=safe_receipt,
                resume_context=self._receipt_resume_context(safe_receipt),
            )

    def cancel(self, confirmation_id: str) -> WorkbenchConfirmation:
        return self.store.cancel_confirmation_execution(
            confirmation_id,
            resume_context="The user cancelled the reviewed external action. Continue without executing it.",
        )

    def close(self) -> None:
        with self._map_lock:
            if self._closed:
                return
            self._closed = True
            states = tuple(self._states.values())
        for state in states:
            state.heartbeat_stop.set()
            self._stop_state_once(state)
        self._pool.shutdown(wait=False, cancel_futures=True)

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
            heartbeat = threading.Thread(
                target=self._heartbeat,
                args=(state,),
                name=f"workbench-heartbeat-{turn.id}",
                daemon=True,
            )
            heartbeat.start()
            try:
                prompt = turn.user_text
                if turn.resume_context:
                    prompt = f"{prompt}\n\nResume context: {turn.resume_context}"
                attachment_paths, image_paths = self._validated_inputs(turn.id, runtime)
                request = RuntimeRequest(
                    turn_id=turn.id,
                    workspace=self.workspace,
                    prompt=prompt,
                    provider_session_ref=task.provider_session_ref,
                    attachment_paths=attachment_paths,
                    image_paths=image_paths,
                )
                handle = runtime.start(
                    request, on_event=lambda event: self._on_event(state, event)
                )
                with state.lock:
                    state.handle = handle
                current = self.store.get_turn(turn.id)
                if (
                    state.lease_lost
                    or (current is not None and current.stop_requested)
                    or state.confirmation_created
                ):
                    self._stop_state_once(state)
                result = runtime.wait(handle)
                self._finish_runtime(state, result)
            except Exception:
                self._stop_state_once(state)
                self._fail_claimed(turn.id, "runtime_failure", state=state)
            finally:
                state.heartbeat_stop.set()
                heartbeat.join(timeout=max(1.0, self.heartbeat_interval_seconds * 2))
                with self._map_lock:
                    self._states.pop(turn.id, None)

    def _heartbeat(self, state: _RunState) -> None:
        while not state.heartbeat_stop.wait(self.heartbeat_interval_seconds):
            try:
                self.store.renew_turn_lease(
                    state.turn_id,
                    owner=self.owner,
                    lease_seconds=self.lease_seconds + 1,
                )
            except ValueError:
                current = self.store.get_turn(state.turn_id)
                if (
                    current is not None
                    and current.status is TurnStatus.WAITING_CONFIRMATION
                ):
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
            if state.lease_lost or state.confirmation_created:
                return
            current = self.store.get_turn(state.turn_id)
            if current is None or current.status is not TurnStatus.RUNNING:
                return
            if event.event_type in {"turn_completed", "turn_failed"}:
                return
            payload = event.payload_json_value()
            assert_no_credentials(payload)
            if event.event_type == "confirmation_required":
                try:
                    self._create_confirmation(state, payload)
                except ValueError:
                    current = self.store.get_turn(state.turn_id)
                    if current is not None and current.status is TurnStatus.STOPPED:
                        return
                    raise
                else:
                    state.confirmation_created = True
                return
            try:
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

    def _create_confirmation(self, state: _RunState, payload: dict[str, Any]) -> None:
        if (
            payload.get("kind") != "reviewed_cli"
            or payload.get("executed") is not False
        ):
            raise ValueError("invalid confirmation proposal")
        argv = payload.get("argv")
        if not isinstance(argv, list) or not all(
            isinstance(item, str) for item in argv
        ):
            raise ValueError("invalid confirmation proposal")
        review_write_authorization(
            argv,
            authorization_id=str(uuid4()),
            action_index=0,
            classifier=self.classifier,
        )
        self.store.create_confirmation(
            state.turn_id,
            action_kind="reviewed_cli",
            target=self._safe_display(payload.get("target")),
            summary=self._safe_display(payload.get("summary")),
            risk=self._safe_display(payload.get("risk")),
            arguments_json={"argv": argv, "action_index": 0},
            owner=self.owner,
        )

    def _finish_runtime(self, state: _RunState, result: RuntimeResult) -> None:
        if not isinstance(result, RuntimeResult):
            raise ValueError("malformed runtime result")
        assert_no_credentials(result.final_text)
        assert_no_credentials(result.provider_session_ref)
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
            error_detail=(_SAFE_RUNTIME_FAILURE if target is TurnStatus.FAILED else ""),
            event_payload={"status": target.value},
            owner=self.owner,
        )

    def _fail_claimed(
        self, turn_id: str, code: str, *, state: _RunState | None = None
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
                error_detail=("" if current.stop_requested else _SAFE_RUNTIME_FAILURE),
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

    @staticmethod
    def _safe_display(value: object) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("invalid confirmation proposal")
        assert_no_credentials(value)
        return safe_observability_error(value.strip(), limit=500)

    @staticmethod
    def _safe_receipt(
        receipt: dict[str, object], *, failed: bool, target_summary: str
    ) -> dict[str, object]:
        safe: dict[str, object] = {
            "status": "failed" if failed else "executed",
            "retryable": False,
            "target_summary": target_summary,
        }
        for key in ("operation_digest", "result_digest"):
            digest = receipt.get(key)
            if (
                isinstance(digest, str)
                and len(digest) == 64
                and all(character in "0123456789abcdef" for character in digest)
            ):
                safe[key] = digest
        error = receipt.get("error")
        if isinstance(error, dict):
            code = error.get("code")
            safe["code"] = (
                code
                if isinstance(code, str)
                and 0 < len(code) <= 120
                and all(character.isalnum() or character in "_-." for character in code)
                else "reviewed_write_failed"
            )
            safe["retryable"] = error.get("retryable") is True
        assert_no_credentials(safe)
        encoded = json.dumps(
            safe, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        safe["receipt_digest"] = hashlib.sha256(
            b"workbench-safe-receipt-v1\0" + encoded
        ).hexdigest()
        return safe

    @staticmethod
    def _receipt_resume_context(receipt: dict[str, object]) -> str:
        return "Reviewed action receipt: " + json.dumps(
            receipt,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
