"""HTTP adapter for executing one CEO Agent turn in Friday Runtime.

Friday owns provider selection and provider credentials.  This module only
transports a prompt through Friday's thread/turn/operation API and returns the
final Artifact message; it does not implement audit or effect policy.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from pydantic import SecretStr

from app.agent_runtime_config import AgentRuntimeConfig
from app.friday_runtime_contract import (
    FridayExecutionInput,
    FridayOperationStatus,
    FridayRuntimeContract,
    FridayRuntimeContractError,
)


class FridayRuntimeError(RuntimeError):
    """A Friday transport or execution result that cannot be returned."""

    def __init__(
        self,
        code: str,
        detail: str,
        *,
        retryable: bool,
        thread_id: str = "",
        turn_id: str = "",
        operation_id: str = "",
    ) -> None:
        self.code = code
        self.detail = detail
        self.retryable = retryable
        self.thread_id = thread_id
        self.turn_id = turn_id
        self.operation_id = operation_id
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True, slots=True)
class FridayExecutionResult:
    """The normalized final result of one Friday execution attempt."""

    text: str
    thread_id: str
    turn_id: str
    operation_id: str
    artifact: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class FridayHttpResponse:
    status_code: int
    payload: object


class FridayHttpTransport(Protocol):
    """Small injectable transport boundary used by the adapter and tests."""

    def request(
        self,
        method: str,
        path: str,
        *,
        headers: Mapping[str, str],
        body: Mapping[str, object] | None = None,
        timeout_seconds: float,
    ) -> FridayHttpResponse | tuple[int, object]: ...


class UrllibFridayHttpTransport:
    """Default synchronous JSON transport with no credential-bearing logging."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def request(
        self,
        method: str,
        path: str,
        *,
        headers: Mapping[str, str],
        body: Mapping[str, object] | None = None,
        timeout_seconds: float,
    ) -> FridayHttpResponse:
        encoded = None
        request_headers = {"Accept": "application/json", **headers}
        if body is not None:
            encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=encoded,
            headers=request_headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                raw = response.read()
                return FridayHttpResponse(
                    status_code=int(response.status),
                    payload=_decode_json(raw),
                )
        except urllib.error.HTTPError as exc:
            # Preserve the status for the adapter's stable auth/failure mapping
            # while limiting the body to a safe, bounded JSON detail.
            raw = exc.read(64 * 1024)
            try:
                payload = _decode_json(raw)
            except ValueError:
                payload = {"message": "Friday Runtime request failed"}
            return FridayHttpResponse(status_code=int(exc.code), payload=payload)


class FridayRuntimeAdapter:
    """Execute a prompt through Friday's HTTP thread and operation contract."""

    def __init__(
        self,
        config: AgentRuntimeConfig,
        *,
        transport: FridayHttpTransport | None = None,
        contract: FridayRuntimeContract | None = None,
        poll_interval_seconds: float = 0.25,
    ) -> None:
        if poll_interval_seconds < 0:
            raise ValueError("poll_interval_seconds must be non-negative")
        self.config = config
        self.contract = contract or FridayRuntimeContract.from_documented_api()
        self.transport = transport or UrllibFridayHttpTransport(
            config.friday_runtime_base_url
        )
        self.poll_interval_seconds = poll_interval_seconds

    def execute(
        self,
        prompt: str,
        *,
        project_id: str | None = None,
        conversation_id: str | None = None,
        model: str | None = None,
        timeout_seconds: float = 60.0,
        runtime_ticket: str | None = None,
        friday_session_token: str | None = None,
        auth_disabled: bool | None = None,
    ) -> FridayExecutionResult:
        """Create one thread, submit one turn, poll it, and read its Artifact.

        ``conversation_id`` and ``model`` are accepted for route-neutral
        callers. Friday currently owns thread continuity and model selection;
        this adapter therefore does not invent or override either value.
        """
        del conversation_id, model
        if isinstance(timeout_seconds, bool) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        effective_project = project_id.strip() if isinstance(project_id, str) else ""
        effective_auth_disabled = (
            self.config.friday_runtime_auth_disabled
            if auth_disabled is None
            else auth_disabled
        )
        credential = self._configured_credential()
        ticket = runtime_ticket
        session = friday_session_token
        if ticket is None and session is None and not effective_auth_disabled:
            if self.config.friday_runtime_auth_mode == "session_token":
                session = credential
            else:
                ticket = credential
        request_credential = ticket or session or credential
        try:
            execution = FridayExecutionInput(
                project_id=effective_project,
                prompt=prompt,
                runtime_ticket=ticket,
                friday_session_token=session,
                auth_disabled=effective_auth_disabled,
            )
        except FridayRuntimeContractError as exc:
            raise FridayRuntimeError(
                "friday_runtime_result_invalid", str(exc), retryable=False
            ) from exc
        try:
            headers = self.contract.authentication_headers(
                runtime_ticket=execution.runtime_ticket,
                friday_session_token=execution.friday_session_token,
                auth_disabled=execution.auth_disabled,
            )
        except FridayRuntimeContractError as exc:
            raise FridayRuntimeError(
                "friday_runtime_auth_failed", str(exc), retryable=False
            ) from exc
        deadline = time.monotonic() + timeout_seconds
        thread_id = ""
        turn_id = ""
        operation_id = ""
        try:
            thread_payload = self._request(
                "POST",
                self.contract.create_thread_path,
                headers=headers,
                body=self.contract.create_thread_payload(execution),
                deadline=deadline,
                credential=request_credential,
            )
            thread_data = self.contract.unwrap_success_envelope(thread_payload)
            thread_id = self.contract.thread_id_from_create_response(
                dict(thread_data)
            )
            turn_payload = self._request(
                "POST",
                self.contract.send_message_path(thread_id),
                headers=headers,
                body={
                    "message": {
                        "role": "user",
                        "intent": "send_message",
                        "text": execution.prompt,
                        "parts": [],
                    },
                    "execution": {"dispatch_mode": "background"},
                },
                deadline=deadline,
                credential=request_credential,
            )
            turn_data = self.contract.unwrap_success_envelope(turn_payload)
            operation = _mapping_value(turn_data, "operation")
            operation_id = _required_string(operation, "operation_id")
            turn_id = _turn_id_from_operation(operation, turn_data)
            self._poll_operation(
                operation_id, headers=headers, deadline=deadline, credential=request_credential
            )
            artifact_payload = self._request(
                "GET",
                f"{self.contract.artifacts_path()}?{urllib.parse.urlencode({'thread_id': thread_id})}",
                headers=headers,
                body=None,
                deadline=deadline,
                credential=request_credential,
            )
            artifact = self.contract.select_final_artifact(
                artifact_payload, thread_id=thread_id
            )
            text = _artifact_result_text(artifact)
            if not text:
                raise FridayRuntimeContractError("Friday response has empty final_message")
            return FridayExecutionResult(
                text=text,
                thread_id=thread_id,
                turn_id=turn_id,
                operation_id=operation_id,
                artifact=dict(artifact),
            )
        except FridayRuntimeError as exc:
            exc.thread_id = exc.thread_id or thread_id
            exc.turn_id = exc.turn_id or turn_id
            exc.operation_id = exc.operation_id or operation_id
            raise
        except FridayRuntimeContractError as exc:
            raise FridayRuntimeError(
                "friday_runtime_result_invalid", str(exc), retryable=False,
                thread_id=thread_id, turn_id=turn_id, operation_id=operation_id,
            ) from exc
        except (ValueError, TypeError, KeyError) as exc:
            raise FridayRuntimeError(
                "friday_runtime_result_invalid", "Friday response shape is invalid", retryable=False,
                thread_id=thread_id, turn_id=turn_id, operation_id=operation_id,
            ) from exc

    def _poll_operation(
        self,
        operation_id: str,
        *,
        headers: Mapping[str, str],
        deadline: float,
        credential: str | None = None,
    ) -> None:
        while True:
            payload = self._request(
                "GET",
                self.contract.operation_path(operation_id),
                headers=headers,
                body=None,
                deadline=deadline,
                credential=credential,
            )
            try:
                status = self.contract.operation_status(payload)
            except FridayRuntimeContractError as exc:
                raise FridayRuntimeError(
                    "friday_runtime_result_invalid", str(exc), retryable=False
                ) from exc
            if status == FridayOperationStatus.COMPLETED:
                return
            if self.contract.is_terminal_operation_status(status):
                detail = _operation_failure_detail(payload, credential=credential)
                raise FridayRuntimeError(
                    "friday_runtime_failed", detail, retryable=True
                )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise FridayRuntimeError(
                    "friday_runtime_unreachable", "Friday operation timed out", retryable=True
                )
            time.sleep(min(self.poll_interval_seconds, remaining))

    def _request(
        self,
        method: str,
        path: str,
        *,
        headers: Mapping[str, str],
        body: Mapping[str, object] | None,
        deadline: float,
        credential: str | None = None,
    ) -> Mapping[str, object]:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise FridayRuntimeError(
                "friday_runtime_unreachable", "Friday Runtime request timed out", retryable=True
            )
        try:
            response = self.transport.request(
                method,
                path,
                headers=headers,
                body=body,
                timeout_seconds=remaining,
            )
        except (TimeoutError, urllib.error.URLError, OSError) as exc:
            raise FridayRuntimeError(
                "friday_runtime_unreachable", "Friday Runtime is unreachable", retryable=True
            ) from exc
        status_code, payload = _response_parts(response)
        if status_code in {401, 403}:
            raise FridayRuntimeError(
                "friday_runtime_auth_failed", "Friday Runtime authentication failed", retryable=False
            )
        if not isinstance(payload, Mapping):
            raise FridayRuntimeError(
                "friday_runtime_result_invalid", "Friday response is not a JSON object", retryable=False
            )
        if status_code < 200 or status_code >= 300:
            detail = _safe_response_detail(payload, credential=credential)
            raise FridayRuntimeError("friday_runtime_failed", detail, retryable=True)
        return payload

    def _configured_credential(self) -> str | None:
        secret: SecretStr | None = self.config.secret_for("friday_runtime")
        return secret.get_secret_value() if secret else None


def _response_parts(
    response: FridayHttpResponse | tuple[int, object] | Mapping[str, object]
) -> tuple[int, object]:
    if isinstance(response, FridayHttpResponse):
        return response.status_code, response.payload
    if isinstance(response, tuple) and len(response) == 2:
        return int(response[0]), response[1]
    # A fake/in-process transport may return an already decoded successful
    # envelope instead of an HTTP response wrapper.
    if isinstance(response, Mapping):
        return 200, response
    raise TypeError("Friday transport returned an invalid response")


def _decode_json(raw: bytes) -> object:
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Friday response is not valid JSON") from exc


def _mapping_value(mapping: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = mapping.get(key)
    if not isinstance(value, Mapping):
        raise FridayRuntimeContractError(f"Friday response has no {key}")
    return value


def _required_string(mapping: Mapping[str, object], key: str) -> str:
    value = str(mapping.get(key) or "").strip()
    if not value:
        raise FridayRuntimeContractError(f"Friday response has no {key}")
    return value


def _turn_id_from_operation(
    operation: Mapping[str, object], data: Mapping[str, object]
) -> str:
    request_payload = operation.get("request_payload")
    if isinstance(request_payload, Mapping):
        turn_id = str(request_payload.get("turn_id") or "").strip()
        if turn_id:
            return turn_id
    turn = data.get("turn")
    if isinstance(turn, Mapping):
        turn_id = str(turn.get("turn_id") or "").strip()
        if turn_id:
            return turn_id
    raise FridayRuntimeContractError("Friday response has no turn_id")


def _operation_failure_detail(
    payload: Mapping[str, object], *, credential: str | None = None
) -> str:
    try:
        data = payload.get("data")
        operation = data.get("operation") if isinstance(data, Mapping) else None
        if isinstance(operation, Mapping):
            detail = str(operation.get("last_error") or operation.get("phase") or "").strip()
            if detail:
                return _sanitize_error_detail(detail, credential=credential)
    except Exception:  # pragma: no cover - defensive serialization boundary
        pass
    return "Friday operation failed"


def _safe_response_detail(
    payload: Mapping[str, object], *, credential: str | None = None
) -> str:
    for key in ("message", "detail", "error"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return _sanitize_error_detail(value, credential=credential)
    return "Friday Runtime request failed"


def _artifact_result_text(artifact: Mapping[str, object]) -> str:
    """Prefer Friday's typed output over a concise display message."""

    for key in ("output_payload", "structured", "result"):
        value = artifact.get(key)
        if isinstance(value, (Mapping, list)):
            return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        if isinstance(value, str) and value.strip():
            return value.strip()
    value = artifact.get("final_message")
    return str(value or "").strip()


def _sanitize_error_detail(value: str, *, credential: str | None = None) -> str:
    """Keep provider diagnostics while preventing credential persistence."""

    detail = value.strip()
    if credential:
        detail = detail.replace(credential, "[redacted]")
    lowered = detail.casefold()
    if any(
        marker in lowered
        for marker in ("authorization", "bearer ", "api_key", "token=", "secret=")
    ):
        return "Friday Runtime request failed"
    return detail[:500]
