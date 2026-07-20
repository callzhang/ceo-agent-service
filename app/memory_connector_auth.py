from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import time
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, AsyncGenerator, Awaitable, Callable
from urllib.parse import urlparse

import keyring
from mcp.client.auth import OAuthClientProvider
from mcp.shared.auth import (
    OAuthClientInformationFull,
    OAuthClientMetadata,
    OAuthMetadata,
    OAuthToken,
    ProtectedResourceMetadata,
)


MEMORY_CONNECTOR_SCOPES = "memory.read memory.write offline_access"
DEFAULT_REDIRECT_URI = "http://127.0.0.1:8766/callback"
KEYRING_SERVICE = "com.stardust.ceo-agent-service.memory-connector"


class MemoryConnectorAuthError(RuntimeError):
    pass


class MemoryConnectorAuthorizationRequired(MemoryConnectorAuthError):
    pass


def resolve_memory_connector_url(
    explicit_url: str | None = None,
    *,
    config_path: Path | None = None,
) -> str:
    candidate = str(explicit_url or "").strip()
    if not candidate:
        candidate = os.getenv("CEO_MEMORY_CONNECTOR_URL", "").strip()
    if not candidate:
        candidate = os.getenv("MEMORY_CONNECTOR_URL", "").strip()
    if not candidate:
        path = config_path or Path(
            os.getenv("CODEX_HOME", str(Path.home() / ".codex"))
        ) / "config.toml"
        try:
            payload = tomllib.loads(path.expanduser().read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, tomllib.TOMLDecodeError):
            payload = {}
        memory_config = (payload.get("mcp_servers") or {}).get(
            "memory_connector"
        ) or {}
        candidate = str(memory_config.get("url") or "").strip()
    if not candidate:
        raise MemoryConnectorAuthError("memory connector URL is not configured")
    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise MemoryConnectorAuthError("memory connector URL is invalid")
    if parsed.username or parsed.password:
        raise MemoryConnectorAuthError("memory connector URL must not contain credentials")
    if parsed.scheme == "http" and not _is_loopback_host(parsed.hostname):
        raise MemoryConnectorAuthError(
            "memory connector URL must use HTTPS unless it is loopback"
        )
    return candidate


def _is_loopback_host(hostname: str | None) -> bool:
    if not hostname:
        return False
    if hostname.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


@dataclass(frozen=True)
class OAuthServerMetadata:
    protected_resource_metadata: ProtectedResourceMetadata | None
    oauth_metadata: OAuthMetadata | None
    auth_server_url: str | None


class KeyringOAuthStorage:
    """MCP OAuth storage isolated from Codex-owned credentials."""

    def __init__(
        self,
        url: str,
        *,
        keyring_backend: Any = keyring,
        now: Callable[[], float] = time.time,
    ) -> None:
        url_hash = hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
        self._service = f"{KEYRING_SERVICE}.{url_hash}"
        self._keyring = keyring_backend
        self._now = now

    def _get_json(self, name: str) -> dict[str, Any] | None:
        try:
            raw = self._keyring.get_password(self._service, name)
        except Exception as exc:
            raise MemoryConnectorAuthError(
                f"memory connector keychain read failed ({type(exc).__name__})"
            ) from None
        if not raw:
            return None
        try:
            value = json.loads(raw)
        except (TypeError, ValueError):
            raise MemoryConnectorAuthError(
                "memory connector keychain data is invalid"
            ) from None
        if not isinstance(value, dict) or value.get("schema_version") != 1:
            raise MemoryConnectorAuthError(
                "memory connector keychain data is invalid"
            )
        return value

    def _set_json(self, name: str, value: dict[str, Any]) -> None:
        try:
            self._keyring.set_password(
                self._service,
                name,
                json.dumps(value, ensure_ascii=True, separators=(",", ":")),
            )
        except Exception as exc:
            raise MemoryConnectorAuthError(
                f"memory connector keychain write failed ({type(exc).__name__})"
            ) from None

    async def get_tokens(self) -> OAuthToken | None:
        value = self._get_json("oauth-token")
        if value is None:
            return None
        try:
            return OAuthToken.model_validate(value["token"])
        except Exception:
            raise MemoryConnectorAuthError(
                "memory connector keychain token is invalid"
            ) from None

    async def set_tokens(self, tokens: OAuthToken) -> None:
        existing = await self.get_tokens()
        if not tokens.refresh_token and existing and existing.refresh_token:
            tokens = tokens.model_copy(
                update={"refresh_token": existing.refresh_token}
            )
        now = self._now()
        expires_at = now + tokens.expires_in if tokens.expires_in else None
        self._set_json(
            "oauth-token",
            {
                "schema_version": 1,
                "stored_at": now,
                "expires_at": expires_at,
                "token": tokens.model_dump(mode="json"),
            },
        )

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        value = self._get_json("oauth-client")
        if value is None:
            return None
        try:
            return OAuthClientInformationFull.model_validate(value["client"])
        except Exception:
            raise MemoryConnectorAuthError(
                "memory connector keychain client registration is invalid"
            ) from None

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        self._set_json(
            "oauth-client",
            {
                "schema_version": 1,
                "client": client_info.model_dump(mode="json"),
            },
        )

    async def get_server_metadata(self) -> OAuthServerMetadata:
        value = self._get_json("oauth-server-metadata")
        if value is None:
            return OAuthServerMetadata(None, None, None)
        try:
            protected = (
                ProtectedResourceMetadata.model_validate(
                    value["protected_resource_metadata"]
                )
                if value.get("protected_resource_metadata") is not None
                else None
            )
            oauth = (
                OAuthMetadata.model_validate(value["oauth_metadata"])
                if value.get("oauth_metadata") is not None
                else None
            )
            auth_server_url = str(value.get("auth_server_url") or "").strip() or None
        except Exception:
            raise MemoryConnectorAuthError(
                "memory connector keychain server metadata is invalid"
            ) from None
        return OAuthServerMetadata(protected, oauth, auth_server_url)

    async def set_server_metadata(
        self,
        *,
        protected_resource_metadata: ProtectedResourceMetadata | None,
        oauth_metadata: OAuthMetadata | None,
        auth_server_url: str | None,
    ) -> None:
        self._set_json(
            "oauth-server-metadata",
            {
                "schema_version": 1,
                "protected_resource_metadata": (
                    protected_resource_metadata.model_dump(mode="json")
                    if protected_resource_metadata is not None
                    else None
                ),
                "oauth_metadata": (
                    oauth_metadata.model_dump(mode="json")
                    if oauth_metadata is not None
                    else None
                ),
                "auth_server_url": auth_server_url,
            },
        )

    @property
    def token_expiry_time(self) -> float | None:
        value = self._get_json("oauth-token")
        if value is None or value.get("expires_at") is None:
            return None
        try:
            return float(value["expires_at"])
        except (TypeError, ValueError):
            raise MemoryConnectorAuthError(
                "memory connector keychain token expiry is invalid"
            ) from None

    async def clear(self) -> None:
        for name in ("oauth-token", "oauth-client", "oauth-server-metadata"):
            try:
                self._keyring.delete_password(self._service, name)
            except Exception:
                # Keyring backends differ on missing-password behavior.
                if self._keyring.get_password(self._service, name) is not None:
                    raise MemoryConnectorAuthError(
                        "memory connector keychain delete failed"
                    ) from None


@dataclass(frozen=True)
class MemoryConnectorAuthStatus:
    configured: bool
    ready: bool
    authorization_required: bool
    can_refresh: bool
    scopes: tuple[str, ...]
    expires_at: float | None
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class PersistentOAuthClientProvider(OAuthClientProvider):
    """OAuth provider that makes SDK discovery state survive process restarts."""

    async def _initialize(self) -> None:
        await super()._initialize()
        storage = self.context.storage
        if not isinstance(storage, KeyringOAuthStorage):
            return
        metadata = await storage.get_server_metadata()
        self.context.protected_resource_metadata = metadata.protected_resource_metadata
        self.context.oauth_metadata = metadata.oauth_metadata
        self.context.auth_server_url = metadata.auth_server_url

    async def _persist_server_metadata(self) -> None:
        storage = self.context.storage
        if not isinstance(storage, KeyringOAuthStorage):
            return
        if not any(
            (
                self.context.protected_resource_metadata,
                self.context.oauth_metadata,
                self.context.auth_server_url,
            )
        ):
            return
        await storage.set_server_metadata(
            protected_resource_metadata=self.context.protected_resource_metadata,
            oauth_metadata=self.context.oauth_metadata,
            auth_server_url=self.context.auth_server_url,
        )

    async def async_auth_flow(
        self, request: Any
    ) -> AsyncGenerator[Any, Any]:
        flow = super().async_auth_flow(request)
        try:
            outbound = await flow.__anext__()
            while True:
                response = yield outbound
                outbound = await flow.asend(response)
        except StopAsyncIteration:
            return
        finally:
            await self._persist_server_metadata()


class MemoryConnectorAuthManager:
    def __init__(
        self,
        *,
        url: str | None = None,
        storage: KeyringOAuthStorage | None = None,
        config_path: Path | None = None,
        now: Callable[[], float] = time.time,
    ) -> None:
        self.url = resolve_memory_connector_url(url, config_path=config_path)
        self.storage = storage or KeyringOAuthStorage(self.url, now=now)
        self._now = now

    async def status(self) -> MemoryConnectorAuthStatus:
        tokens = await self.storage.get_tokens()
        client = await self.storage.get_client_info()
        expiry = self.storage.token_expiry_time
        scopes = tuple(sorted(set(((tokens.scope or "") if tokens else "").split())))
        expired = expiry is not None and self._now() >= expiry
        can_refresh = bool(
            tokens and tokens.refresh_token and client and client.client_id
        )
        scope_denied = bool(scopes) and "memory.write" not in scopes
        ready = bool(tokens and tokens.access_token and not scope_denied)
        if expired and not can_refresh:
            ready = False
        if scope_denied:
            reason = "memory.write scope is missing"
        elif not tokens or not tokens.access_token:
            reason = "authorization required"
        elif expired and not can_refresh:
            reason = "authorization expired and cannot be refreshed"
        else:
            reason = "ready"
        return MemoryConnectorAuthStatus(
            configured=True,
            ready=ready,
            authorization_required=not ready,
            can_refresh=can_refresh,
            scopes=scopes,
            expires_at=expiry,
            reason=reason,
        )

    def _provider(
        self,
        *,
        redirect_handler: Callable[[str], Awaitable[None]] | None,
        callback_handler: Callable[[], Awaitable[tuple[str, str | None]]] | None,
    ) -> OAuthClientProvider:
        provider = PersistentOAuthClientProvider(
            self.url,
            OAuthClientMetadata(
                client_name="CEO Agent Service",
                redirect_uris=[DEFAULT_REDIRECT_URI],
                grant_types=["authorization_code", "refresh_token"],
                response_types=["code"],
                scope=MEMORY_CONNECTOR_SCOPES,
            ),
            self.storage,
            redirect_handler=redirect_handler,
            callback_handler=callback_handler,
            timeout=900.0,
        )
        provider.context.token_expiry_time = self.storage.token_expiry_time
        return provider

    async def noninteractive_provider(self) -> OAuthClientProvider:
        status = await self.status()
        if not status.ready:
            raise MemoryConnectorAuthorizationRequired(
                f"memory connector authorization required: {status.reason}"
            )
        return self._provider(redirect_handler=None, callback_handler=None)

    def interactive_provider(
        self,
        *,
        redirect_handler: Callable[[str], Awaitable[None]],
        callback_handler: Callable[[], Awaitable[tuple[str, str | None]]],
    ) -> OAuthClientProvider:
        return self._provider(
            redirect_handler=redirect_handler,
            callback_handler=callback_handler,
        )

    async def logout(self) -> None:
        await self.storage.clear()
