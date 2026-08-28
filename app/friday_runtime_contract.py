"""Frozen HTTP contract for the Friday Runtime integration.

This module deliberately describes Friday's public transport contract only. It
does not contain an HTTP client or provider-specific behavior; those belong to
the adapter built in the next implementation task.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum


class FridayOperationStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    ABANDONED = "abandoned"


class FridayRuntimeContractError(ValueError):
    """The Friday response or request does not satisfy the frozen contract."""


@dataclass(frozen=True, slots=True)
class FridayExecutionInput:
    """Required inputs mapped deterministically to a Friday thread request."""

    project_id: str
    prompt: str
    runtime_ticket: str | None = None
    friday_session_token: str | None = None
    auth_disabled: bool = False

    def __post_init__(self) -> None:
        if not self.project_id.strip():
            raise FridayRuntimeContractError("project_id is required")
        if not self.prompt.strip():
            raise FridayRuntimeContractError("prompt is required")
        if not self.auth_disabled and not (
            (self.runtime_ticket and self.runtime_ticket.strip())
            or (self.friday_session_token and self.friday_session_token.strip())
        ):
            raise FridayRuntimeContractError(
                "Friday auth requires runtime_ticket or friday_session_token"
            )


@dataclass(frozen=True, slots=True)
class FridayRuntimeContract:
    """Paths and normalized response names used by the CEO Agent adapter."""

    create_thread_path: str = "/v1/threads"
    final_artifact_field: str = "artifact"
    artifact_items_field: str = "items"
    operation_field: str = "operation"
    project_id_field: str = "project_id"
    auth_headers_supported: tuple[str, ...] = (
        "Authorization: Bearer <RuntimeTicket>",
        "X-Friday-Session-Token: <token>",
    )

    @classmethod
    def from_documented_api(cls) -> "FridayRuntimeContract":
        """Return the contract verified against Friday Runtime 939232c."""

        return cls()

    def send_message_path(self, thread_id: str) -> str:
        """Return the endpoint that creates a user turn in a thread."""

        return f"/v1/threads/{thread_id}/turns"

    def run_turn_path(self, turn_id: str) -> str:
        """Return the endpoint that executes a previously created turn."""

        return f"/v1/turns/{turn_id}/runs"

    def operation_path(self, operation_id: str) -> str:
        """Return the endpoint used to poll an asynchronous operation."""

        return f"/v1/operations/{operation_id}"

    def artifacts_path(self) -> str:
        """Return the artifact collection endpoint.

        The adapter adds ``thread_id`` as a query parameter. Friday returns
        the collection in ``data.items`` because the API envelope middleware
        wraps successful JSON responses.
        """

        return "/v1/artifacts"

    def authentication_headers(
        self,
        *,
        runtime_ticket: str | None = None,
        friday_session_token: str | None = None,
        auth_disabled: bool = False,
    ) -> dict[str, str]:
        """Build one supported auth mode, or explicitly use auth-disabled mode."""

        if auth_disabled:
            if runtime_ticket or friday_session_token:
                raise FridayRuntimeContractError(
                    "auth_disabled cannot include authentication credentials"
                )
            return {}
        runtime_ticket = runtime_ticket.strip() if runtime_ticket else None
        friday_session_token = (
            friday_session_token.strip() if friday_session_token else None
        )
        if runtime_ticket and friday_session_token:
            raise FridayRuntimeContractError("choose one Friday authentication mode")
        if runtime_ticket:
            return {"Authorization": f"Bearer {runtime_ticket}"}
        if friday_session_token:
            return {"X-Friday-Session-Token": friday_session_token}
        raise FridayRuntimeContractError(
            "Friday auth requires runtime_ticket or friday_session_token"
        )

    def create_thread_payload(
        self, execution: FridayExecutionInput, *, title: str = "CEO Agent task"
    ) -> dict[str, object]:
        """Map execution input to the deterministic minimum create-thread body."""

        return {
            "project_id": execution.project_id,
            "title": title,
            "dispatch_mode": "wait",
        }

    def unwrap_success_envelope(
        self, payload: Mapping[str, object]
    ) -> Mapping[str, object]:
        """Return ``data`` from Friday's successful response envelope."""

        if payload.get("result") != "success":
            raise FridayRuntimeContractError("Friday response is not successful")
        data = payload.get("data")
        if not isinstance(data, Mapping):
            raise FridayRuntimeContractError("Friday response has no data object")
        return data

    def operation_status(self, payload: Mapping[str, object]) -> FridayOperationStatus:
        data = self.unwrap_success_envelope(payload)
        operation = data.get(self.operation_field)
        if not isinstance(operation, Mapping):
            raise FridayRuntimeContractError("Friday response has no operation")
        try:
            return FridayOperationStatus(str(operation.get("status") or ""))
        except ValueError as exc:
            raise FridayRuntimeContractError(
                "Friday response has invalid operation status"
            ) from exc

    def is_terminal_operation_status(self, status: FridayOperationStatus) -> bool:
        return status in {
            FridayOperationStatus.COMPLETED,
            FridayOperationStatus.FAILED,
            FridayOperationStatus.CANCELLED,
            FridayOperationStatus.ABANDONED,
        }

    def select_final_artifact(
        self, payload: Mapping[str, object], *, thread_id: str
    ) -> Mapping[str, object]:
        """Select a matching Artifact whose final message is nonempty."""

        data = self.unwrap_success_envelope(payload)
        items = data.get(self.artifact_items_field)
        if not isinstance(items, list):
            raise FridayRuntimeContractError("Friday response has no artifact items")
        for item in items:
            if not isinstance(item, Mapping) or item.get("thread_id") != thread_id:
                continue
            if any(
                isinstance(item.get(key), (Mapping, list))
                or (isinstance(item.get(key), str) and item.get(key).strip())
                for key in ("output_payload", "structured", "result", "final_message")
            ):
                return item
        raise FridayRuntimeContractError(
            "Friday response has no matching artifact with final_message"
        )

    def thread_id_from_create_response(self, payload: dict[str, object]) -> str:
        """Extract a thread id from a normalized ``data.thread`` response."""

        thread = payload.get("thread")
        if not isinstance(thread, dict):
            raise ValueError("Friday create-thread response has no thread")
        thread_id = str(thread.get("thread_id") or "").strip()
        if not thread_id:
            raise ValueError("Friday create-thread response has no thread_id")
        return thread_id
