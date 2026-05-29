import json
from typing import Any
from urllib import error, request

from ceo_agent_service.codex_runner import (
    MEMORY_CONNECTOR_API_KEY_ENV,
    MEMORY_CONNECTOR_URL_ENV,
    MEMORY_CONNECTOR_USER_ID_ENV,
    _memory_connector_env,
)

MCP_PROTOCOL_VERSION = "2025-03-26"


class MemoryConnectorError(RuntimeError):
    pass


def memory_connector_user_id() -> str:
    return _memory_connector_env().get(MEMORY_CONNECTOR_USER_ID_ENV, "derek")


def memory_connector_url() -> str:
    url = _memory_connector_env().get(MEMORY_CONNECTOR_URL_ENV)
    if not url:
        raise MemoryConnectorError("MEMORY_CONNECTOR_URL is required")
    return url


def memory_connector_token() -> str:
    token = _memory_connector_env().get(MEMORY_CONNECTOR_API_KEY_ENV)
    if not token:
        raise MemoryConnectorError("CONNECTOR_API_KEY is required")
    return token


def extract_memory_episode_id(payload: dict[str, Any]) -> str:
    for key in ("episode_uuid", "episode_id", "source_episode_uuid"):
        value = payload.get(key)
        if isinstance(value, str):
            return value
    result = payload.get("result")
    if isinstance(result, dict):
        episode_uuids = result.get("episode_uuids")
        if isinstance(episode_uuids, list) and episode_uuids:
            first = episode_uuids[0]
            if isinstance(first, str):
                return first
    return ""


class MemoryConnectorClient:
    def __init__(
        self,
        url: str | None = None,
        token: str | None = None,
        user_id: str | None = None,
    ):
        self.url = url or memory_connector_url()
        self.token = token or memory_connector_token()
        self.user_id = user_id or memory_connector_user_id()

    def memory_write(
        self,
        *,
        data: str,
        type: str,
        created_at: str,
        user_id: str,
        source_description: str,
        source_metadata: dict[str, Any],
        provenance_metadata: dict[str, Any],
    ) -> dict[str, Any]:
        initialize_payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {
                    "name": "ceo-agent-service-memory-flush",
                    "version": "1.0.0",
                },
            },
        }
        initialize_body, session_id = self._post_mcp_json(initialize_payload)
        if initialize_body is None:
            raise MemoryConnectorError("memory connector initialize returned no body")
        _raise_json_rpc_error(initialize_body)

        initialized_payload = {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {},
        }
        self._post_mcp_json(initialized_payload, session_id=session_id)

        call_payload = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "memory_write",
                "arguments": {
                    "data": data,
                    "type": type,
                    "created_at": created_at,
                    "user_id": user_id,
                    "source_description": source_description,
                    "source_metadata": source_metadata,
                    "provenance_metadata": provenance_metadata,
                    "wait_for_processing": False,
                },
            },
        }
        call_body, _ = self._post_mcp_json(call_payload, session_id=session_id)
        if call_body is None:
            return {}
        return _parse_json_rpc_payload(call_body)

    def _post_mcp_json(
        self, payload: dict[str, Any], session_id: str | None = None
    ) -> tuple[dict[str, Any] | None, str | None]:
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "x-memory-user-id": self.user_id,
        }
        if session_id:
            headers["mcp-session-id"] = session_id
        http_request = request.Request(
            self.url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with request.urlopen(http_request, timeout=30) as response:
                status = getattr(response, "status", 200)
                response_body = response.read().decode("utf-8")
                if status >= 400:
                    raise MemoryConnectorError(
                        f"memory connector HTTP {status}: {response_body}"
                    )
                response_session_id = _response_session_id(response.headers)
        except error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            raise MemoryConnectorError(
                f"memory connector HTTP {exc.code}: {details}"
            ) from exc
        except error.URLError as exc:
            raise MemoryConnectorError(f"memory connector request failed: {exc}") from exc

        return _parse_mcp_response(response_body), response_session_id


def _response_session_id(headers: Any) -> str | None:
    for name in ("mcp-session-id", "Mcp-Session-Id", "Mcp-session-id"):
        value = headers.get(name)
        if value:
            return str(value)
    return None


def _parse_mcp_response(response_body: str) -> dict[str, Any] | None:
    stripped = response_body.strip()
    if not stripped:
        return None
    if _looks_like_sse(stripped):
        parsed_events = _parse_sse_json_events(stripped)
        if not parsed_events:
            return None
        return parsed_events[-1]
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise MemoryConnectorError("memory connector returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise MemoryConnectorError("memory connector response must be a JSON object")
    return payload


def _looks_like_sse(response_body: str) -> bool:
    return any(line.startswith("data:") for line in response_body.splitlines())


def _parse_sse_json_events(response_body: str) -> list[dict[str, Any]]:
    events = []
    data_lines: list[str] = []
    for line in response_body.splitlines():
        if line.startswith("data:"):
            data = line.removeprefix("data:").strip()
            if data == "[DONE]":
                data_lines = []
                continue
            data_lines.append(data)
        elif line == "" and data_lines:
            events.append(_load_sse_json("\n".join(data_lines)))
            data_lines = []
    if data_lines:
        events.append(_load_sse_json("\n".join(data_lines)))
    return events


def _load_sse_json(data: str) -> dict[str, Any]:
    try:
        payload = json.loads(data)
    except json.JSONDecodeError as exc:
        raise MemoryConnectorError("memory connector returned invalid SSE JSON") from exc
    if not isinstance(payload, dict):
        raise MemoryConnectorError("memory connector SSE event must be a JSON object")
    return payload


def _parse_json_rpc_payload(payload: dict[str, Any]) -> dict[str, Any]:
    _raise_json_rpc_error(payload)

    result = payload.get("result")
    if isinstance(result, dict):
        if result.get("isError"):
            raise MemoryConnectorError(_tool_error_message(result))
        content_payload = _parse_content_payload(result)
        if content_payload is not None:
            return content_payload
        return result
    return payload


def _raise_json_rpc_error(payload: dict[str, Any]) -> None:
    error_payload = payload.get("error")
    if not error_payload:
        return
    if isinstance(error_payload, dict):
        message = error_payload.get("message") or json.dumps(error_payload)
    else:
        message = str(error_payload)
    raise MemoryConnectorError(f"memory connector tool error: {message}")


def _parse_content_payload(result: dict[str, Any]) -> dict[str, Any] | None:
    content = result.get("content")
    if not isinstance(content, list):
        return None
    for item in content:
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        if not isinstance(text, str):
            continue
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return {"text": text}
        if isinstance(parsed, dict):
            return parsed
        return {"value": parsed}
    return None


def _tool_error_message(result: dict[str, Any]) -> str:
    content = result.get("content")
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                return item["text"]
    return json.dumps(result)
