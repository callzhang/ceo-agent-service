import json
import os
from typing import Any
from urllib import error, request


class MemoryConnectorError(RuntimeError):
    pass


def memory_connector_user_id() -> str:
    return os.getenv("MEMORY_CONNECTOR_USER_ID") or "derek"


def memory_connector_url() -> str:
    url = os.getenv("MEMORY_CONNECTOR_URL")
    if not url:
        raise MemoryConnectorError("MEMORY_CONNECTOR_URL is required")
    return url


def memory_connector_token() -> str:
    token = os.getenv("CONNECTOR_API_KEY")
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
        body = {
            "jsonrpc": "2.0",
            "id": 1,
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
        http_request = request.Request(
            self.url,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
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
        except error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            raise MemoryConnectorError(
                f"memory connector HTTP {exc.code}: {details}"
            ) from exc
        except error.URLError as exc:
            raise MemoryConnectorError(f"memory connector request failed: {exc}") from exc

        return _parse_memory_write_response(response_body)


def _parse_memory_write_response(response_body: str) -> dict[str, Any]:
    stripped = response_body.strip()
    if not stripped:
        return {}
    if _looks_like_sse(stripped):
        parsed_events = _parse_sse_json_events(stripped)
        if not parsed_events:
            return {}
        return _parse_json_rpc_payload(parsed_events[-1])
    try:
        return _parse_json_rpc_payload(json.loads(stripped))
    except json.JSONDecodeError as exc:
        raise MemoryConnectorError("memory connector returned invalid JSON") from exc


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
    error_payload = payload.get("error")
    if error_payload:
        if isinstance(error_payload, dict):
            message = error_payload.get("message") or json.dumps(error_payload)
        else:
            message = str(error_payload)
        raise MemoryConnectorError(f"memory connector tool error: {message}")

    result = payload.get("result")
    if isinstance(result, dict):
        if result.get("isError"):
            raise MemoryConnectorError(_tool_error_message(result))
        content_payload = _parse_content_payload(result)
        if content_payload is not None:
            return content_payload
        return result
    return payload


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
