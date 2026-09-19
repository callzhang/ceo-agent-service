"""Write to the Memory connector directly, without a model turn.

Recording a delivered meeting conclusion calls one connector tool with
arguments the service already decided. Running that through an Agent turn cost
74-95 seconds each and made Memory depend on a model route: when the route
paused, conclusions stopped being recorded for reasons unrelated to Memory.

The connector authenticates with OAuth and issues no API keys, so the service
registers its own public client and keeps its own refresh token. It never reads
the credentials another tool holds: those expire on that tool's schedule and
are refreshed by it, so borrowing them builds something that breaks later for
reasons this service cannot see.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import secrets
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


MEMORY_CONNECTOR_URL = "https://memory.preseen.ai/mcp/"
MEMORY_CONNECTOR_ISSUER = "https://memory.preseen.ai"
MEMORY_CONNECTOR_SCOPE = "offline_access memory.read memory.write"
MEMORY_CONNECTOR_CLIENT_NAME = "ceo-agent-service"
MCP_PROTOCOL_VERSION = "2024-11-05"

# Refresh this far before the access token expires, so a write never races the
# expiry it was told about.
_ACCESS_TOKEN_REFRESH_MARGIN = timedelta(seconds=60)
_HTTP_TIMEOUT_SECONDS = 30
# The connector sits behind an edge that refuses Python's default agent string
# outright (Cloudflare 1010), so every request names this service.
_USER_AGENT = "ceo-agent-service/1.0"


class MemoryConnectorError(RuntimeError):
    """The connector could not be reached or refused the request."""


class MemoryConnectorNotAuthorized(MemoryConnectorError):
    """No usable credential; a person must authorize this service once."""


@dataclass(frozen=True)
class MemoryWriteReceipt:
    """What the connector recorded, read back from its own response."""

    episode_uuid: str
    processing_status: str
    duplicate: bool = False


@dataclass(frozen=True)
class MemoryConnectorCredential:
    """The service's own OAuth client and refresh token."""

    client_id: str
    refresh_token: str
    issuer: str = MEMORY_CONNECTOR_ISSUER
    url: str = MEMORY_CONNECTOR_URL

    def to_payload(self) -> dict[str, str]:
        return {
            "client_id": self.client_id,
            "refresh_token": self.refresh_token,
            "issuer": self.issuer,
            "url": self.url,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "MemoryConnectorCredential":
        client_id = str(payload.get("client_id") or "").strip()
        refresh_token = str(payload.get("refresh_token") or "").strip()
        if not client_id or not refresh_token:
            raise MemoryConnectorNotAuthorized(
                "the Memory connector credential is missing its client or refresh token"
            )
        return cls(
            client_id=client_id,
            refresh_token=refresh_token,
            issuer=str(payload.get("issuer") or MEMORY_CONNECTOR_ISSUER),
            url=str(payload.get("url") or MEMORY_CONNECTOR_URL),
        )


def credential_path() -> Path:
    """Where the service keeps its own connector credential."""
    configured = os.environ.get("CEO_MEMORY_CONNECTOR_CREDENTIAL", "").strip()
    if configured:
        return Path(configured).expanduser()
    return (
        Path.home()
        / "Library"
        / "Application Support"
        / "ceo-agent-service"
        / "memory-connector-credential.json"
    )


def load_credential(path: Path | None = None) -> MemoryConnectorCredential:
    target = path or credential_path()
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise MemoryConnectorNotAuthorized(
            f"no Memory connector credential at {target}; authorize the service once"
        ) from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise MemoryConnectorNotAuthorized(
            f"unreadable Memory connector credential at {target}: {exc}"
        ) from exc
    return MemoryConnectorCredential.from_payload(payload)


def save_credential(
    credential: MemoryConnectorCredential, path: Path | None = None
) -> Path:
    target = path or credential_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(credential.to_payload(), ensure_ascii=False), encoding="utf-8"
    )
    target.chmod(0o600)
    return target


def _post_json(url: str, payload: dict[str, Any], headers: dict[str, str]) -> Any:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "User-Agent": _USER_AGENT,
            **headers,
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=_HTTP_TIMEOUT_SECONDS) as response:
            return response.read().decode("utf-8"), dict(response.headers)
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise MemoryConnectorError(f"{url} returned {exc.code}: {detail}") from exc
    except (URLError, OSError) as exc:
        raise MemoryConnectorError(f"{url} unreachable: {exc}") from exc


def _post_form(url: str, form: dict[str, str]) -> dict[str, Any]:
    request = Request(
        url,
        data=urlencode(form).encode("utf-8"),
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": _USER_AGENT,
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=_HTTP_TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        if exc.code in (400, 401):
            raise MemoryConnectorNotAuthorized(
                f"the connector refused the credential ({exc.code}): {detail}"
            ) from exc
        raise MemoryConnectorError(f"{url} returned {exc.code}: {detail}") from exc
    except (URLError, OSError) as exc:
        raise MemoryConnectorError(f"{url} unreachable: {exc}") from exc


def register_client(
    *, redirect_uri: str, issuer: str = MEMORY_CONNECTOR_ISSUER
) -> str:
    """Register this service as its own public OAuth client."""
    body, _ = _post_json(
        f"{issuer}/oauth/register",
        {
            "client_name": MEMORY_CONNECTOR_CLIENT_NAME,
            "redirect_uris": [redirect_uri],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
            "scope": MEMORY_CONNECTOR_SCOPE,
        },
        headers={},
    )
    client_id = str((json.loads(body) or {}).get("client_id") or "").strip()
    if not client_id:
        raise MemoryConnectorError("the connector registered no client id")
    return client_id


def authorization_url(
    *,
    client_id: str,
    redirect_uri: str,
    code_challenge: str,
    state: str,
    issuer: str = MEMORY_CONNECTOR_ISSUER,
) -> str:
    query = urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "scope": MEMORY_CONNECTOR_SCOPE,
            "resource": MEMORY_CONNECTOR_URL,
        }
    )
    return f"{issuer}/oauth/authorize?{query}"


def pkce_pair() -> tuple[str, str]:
    """Return one (verifier, challenge) pair for this authorization."""
    import base64
    import hashlib

    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).decode().rstrip("=")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return verifier, challenge


def exchange_code(
    *,
    client_id: str,
    code: str,
    code_verifier: str,
    redirect_uri: str,
    issuer: str = MEMORY_CONNECTOR_ISSUER,
) -> MemoryConnectorCredential:
    payload = _post_form(
        f"{issuer}/oauth/token",
        {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "code_verifier": code_verifier,
            "redirect_uri": redirect_uri,
            "resource": MEMORY_CONNECTOR_URL,
        },
    )
    refresh_token = str(payload.get("refresh_token") or "").strip()
    if not refresh_token:
        raise MemoryConnectorNotAuthorized(
            "the connector issued no refresh token; the authorization must request "
            "offline_access"
        )
    return MemoryConnectorCredential(client_id=client_id, refresh_token=refresh_token)


class MemoryConnectorClient:
    """One authenticated MCP client for the Memory connector."""

    def __init__(
        self,
        credential: MemoryConnectorCredential | None = None,
        *,
        now: Any = None,
        credential_path_override: Path | None = None,
    ) -> None:
        self._credential = credential or load_credential(credential_path_override)
        self._credential_path = credential_path_override
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._access_token = ""
        self._access_expires_at = datetime.min.replace(tzinfo=timezone.utc)
        self._session_id = ""

    def _fresh_access_token(self) -> str:
        if self._access_token and self._now() < (
            self._access_expires_at - _ACCESS_TOKEN_REFRESH_MARGIN
        ):
            return self._access_token
        payload = _post_form(
            f"{self._credential.issuer}/oauth/token",
            {
                "grant_type": "refresh_token",
                "refresh_token": self._credential.refresh_token,
                "client_id": self._credential.client_id,
                "resource": self._credential.url,
            },
        )
        access_token = str(payload.get("access_token") or "").strip()
        if not access_token:
            raise MemoryConnectorNotAuthorized("the connector issued no access token")
        self._access_token = access_token
        try:
            lifetime = int(payload.get("expires_in") or 3600)
        except (TypeError, ValueError):
            lifetime = 3600
        self._access_expires_at = self._now() + timedelta(seconds=lifetime)
        # A rotated refresh token replaces the stored one, or the next run
        # authenticates with a token the connector has already retired.
        rotated = str(payload.get("refresh_token") or "").strip()
        if rotated and rotated != self._credential.refresh_token:
            self._credential = MemoryConnectorCredential(
                client_id=self._credential.client_id,
                refresh_token=rotated,
                issuer=self._credential.issuer,
                url=self._credential.url,
            )
            save_credential(self._credential, self._credential_path)
        return self._access_token

    def _rpc(self, method: str, params: dict[str, Any], *, rpc_id: int) -> Any:
        headers = {
            "Authorization": f"Bearer {self._fresh_access_token()}",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": MCP_PROTOCOL_VERSION,
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        body, response_headers = _post_json(
            self._credential.url,
            {"jsonrpc": "2.0", "id": rpc_id, "method": method, "params": params},
            headers=headers,
        )
        session = response_headers.get("Mcp-Session-Id") or response_headers.get(
            "mcp-session-id"
        )
        if session:
            self._session_id = session
        return _decode_rpc(body, method)

    def _connect(self) -> None:
        if self._session_id:
            return
        self._rpc(
            "initialize",
            {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": MEMORY_CONNECTOR_CLIENT_NAME, "version": "1"},
            },
            rpc_id=1,
        )

    def write(
        self,
        *,
        data: str,
        created_at: str,
        source_description: str,
        thread_id: str = "",
        type: str = "text",
    ) -> MemoryWriteReceipt:
        """Persist one payload and return what the connector recorded."""
        self._connect()
        arguments: dict[str, Any] = {
            "data": data,
            "type": type,
            "created_at": created_at,
            "source_description": source_description,
        }
        if thread_id:
            arguments["thread_id"] = thread_id
        result = self._rpc(
            "tools/call",
            {"name": "memory_write", "arguments": arguments},
            rpc_id=int(time.time()) % 100000 + 2,
        )
        return _receipt_from_result(result)


def _decode_rpc(body: str, method: str) -> Any:
    """Read one JSON-RPC result from a JSON or SSE response."""
    text = body.strip()
    if text.startswith("event:") or text.startswith("data:"):
        for line in text.splitlines():
            if line.startswith("data:"):
                text = line[5:].strip()
                break
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise MemoryConnectorError(
            f"{method} returned a response that is not JSON: {text[:200]}"
        ) from exc
    if isinstance(payload, dict) and payload.get("error"):
        raise MemoryConnectorError(f"{method} failed: {payload['error']}")
    if not isinstance(payload, dict) or "result" not in payload:
        raise MemoryConnectorError(f"{method} returned no result: {text[:200]}")
    return payload["result"]


def _receipt_from_result(result: Any) -> MemoryWriteReceipt:
    """Read the connector's own words, not a hopeful default.

    A tool result carries its payload as content blocks, so an empty or
    unreadable one is reported rather than counted as a write.
    """
    if not isinstance(result, dict):
        raise MemoryConnectorError("memory_write returned no structured result")
    if result.get("isError"):
        raise MemoryConnectorError(f"memory_write reported an error: {result}")
    payload: Any = result.get("structuredContent")
    if not isinstance(payload, dict):
        for block in result.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "text":
                try:
                    payload = json.loads(str(block.get("text") or ""))
                except json.JSONDecodeError:
                    continue
                break
    if isinstance(payload, dict) and isinstance(payload.get("result"), str):
        try:
            payload = json.loads(payload["result"])
        except json.JSONDecodeError:
            pass
    if not isinstance(payload, dict):
        raise MemoryConnectorError("memory_write returned no readable payload")
    episode = str(payload.get("episode_uuid") or payload.get("uuid") or "").strip()
    if not episode:
        raise MemoryConnectorError(f"memory_write recorded no episode: {payload}")
    return MemoryWriteReceipt(
        episode_uuid=episode,
        processing_status=str(payload.get("processing_status") or "").strip(),
        duplicate=bool(payload.get("duplicate")),
    )


_SHARED_CLIENT: MemoryConnectorClient | None = None


def write_meeting_memory(
    *,
    data: str,
    type: str,
    created_at: str,
    source_description: str,
    client: MemoryConnectorClient | None = None,
) -> MemoryWriteReceipt:
    """Persist one payload, reusing one authenticated client per process.

    The access token and MCP session are worth keeping across writes: a pass
    drains a queue, and re-authenticating per item would put the cost back that
    calling the connector directly removes.
    """
    global _SHARED_CLIENT

    if client is not None:
        return client.write(
            data=data,
            type=type,
            created_at=created_at,
            source_description=source_description,
        )
    if _SHARED_CLIENT is None:
        _SHARED_CLIENT = MemoryConnectorClient()
    try:
        return _SHARED_CLIENT.write(
            data=data,
            type=type,
            created_at=created_at,
            source_description=source_description,
        )
    except MemoryConnectorNotAuthorized:
        # A credential replaced since this client was built is a real change,
        # not a permanent refusal: rebuild once and let the next write decide.
        _SHARED_CLIENT = None
        raise
