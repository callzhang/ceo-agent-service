import json
from pathlib import Path

import httpx
import pytest
from mcp.shared.auth import (
    OAuthClientInformationFull,
    OAuthMetadata,
    OAuthToken,
    ProtectedResourceMetadata,
)

from app.memory_connector_auth import (
    KeyringOAuthStorage,
    MemoryConnectorAuthError,
    MemoryConnectorAuthManager,
    resolve_memory_connector_url,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class FakeKeyring:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.values.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.values[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        self.values.pop((service, username), None)


def test_resolve_url_prefers_explicit_then_env_then_codex_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        '[mcp_servers.memory_connector]\nurl = "https://config.example/mcp/"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("CEO_MEMORY_CONNECTOR_URL", "https://env.example/mcp/")

    assert resolve_memory_connector_url("https://explicit.example/mcp/", config_path=config) == (
        "https://explicit.example/mcp/"
    )
    assert resolve_memory_connector_url(config_path=config) == "https://env.example/mcp/"
    monkeypatch.delenv("CEO_MEMORY_CONNECTOR_URL")
    assert resolve_memory_connector_url(config_path=config) == "https://config.example/mcp/"


def test_resolve_url_does_not_read_codex_auth_fields(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        """
[mcp_servers.memory_connector]
url = "https://memory.example/mcp/"
bearer_token = "must-not-be-read"
[mcp_servers.memory_connector.http_headers]
Authorization = "Bearer must-not-be-read"
""",
        encoding="utf-8",
    )

    assert resolve_memory_connector_url(config_path=config) == "https://memory.example/mcp/"


@pytest.mark.parametrize(
    "url",
    [
        "https://memory.example/mcp/",
        "http://localhost:8765/mcp/",
        "http://127.0.0.1:8765/mcp/",
        "http://[::1]:8765/mcp/",
    ],
)
def test_resolve_url_allows_https_and_loopback_http(url: str) -> None:
    assert resolve_memory_connector_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "http://memory.example/mcp/",
        "http://192.168.1.8/mcp/",
        "http://10.0.0.2/mcp/",
    ],
)
def test_resolve_url_rejects_non_loopback_cleartext_before_auth(url: str) -> None:
    with pytest.raises(MemoryConnectorAuthError, match="HTTPS"):
        MemoryConnectorAuthManager(url=url)


@pytest.mark.anyio
async def test_keyring_storage_round_trips_tokens_client_and_refresh_metadata() -> None:
    keyring = FakeKeyring()
    storage = KeyringOAuthStorage("https://memory.example/mcp/", keyring_backend=keyring)
    token = OAuthToken(
        access_token="access-secret",
        refresh_token="refresh-secret",
        expires_in=3600,
        scope="memory.read memory.write offline_access",
    )
    client = OAuthClientInformationFull(
        client_id="client-id",
        client_secret="client-secret",
        redirect_uris=["http://127.0.0.1:8766/callback"],
        scope="memory.read memory.write offline_access",
    )

    await storage.set_tokens(token)
    await storage.set_client_info(client)

    assert await storage.get_tokens() == token
    assert await storage.get_client_info() == client
    assert storage.token_expiry_time is not None
    raw = " ".join(keyring.values.values())
    assert "access-secret" in raw
    assert json.loads(next(iter(keyring.values.values())))["schema_version"] == 1


@pytest.mark.anyio
async def test_keyring_storage_round_trips_oauth_server_discovery_metadata() -> None:
    storage = KeyringOAuthStorage(
        "https://memory.example/mcp/", keyring_backend=FakeKeyring()
    )
    protected = ProtectedResourceMetadata(
        resource="https://memory.example/mcp/",
        authorization_servers=["https://auth.example/"],
        scopes_supported=["memory.read", "memory.write", "offline_access"],
    )
    oauth = OAuthMetadata(
        issuer="https://auth.example/",
        authorization_endpoint="https://auth.example/oauth/authorize",
        token_endpoint="https://auth.example/oauth/token",
        registration_endpoint="https://auth.example/oauth/register",
        scopes_supported=["memory.read", "memory.write", "offline_access"],
    )

    await storage.set_server_metadata(
        protected_resource_metadata=protected,
        oauth_metadata=oauth,
        auth_server_url="https://auth.example/",
    )

    restored = await storage.get_server_metadata()
    assert restored.protected_resource_metadata == protected
    assert restored.oauth_metadata == oauth
    assert restored.auth_server_url == "https://auth.example/"


@pytest.mark.anyio
async def test_refresh_response_without_refresh_token_preserves_existing_refresh_token() -> None:
    storage = KeyringOAuthStorage(
        "https://memory.example/mcp/", keyring_backend=FakeKeyring()
    )
    await storage.set_tokens(
        OAuthToken(access_token="old-access", refresh_token="stable-refresh")
    )

    await storage.set_tokens(OAuthToken(access_token="new-access", expires_in=1800))

    refreshed = await storage.get_tokens()
    assert refreshed is not None
    assert refreshed.access_token == "new-access"
    assert refreshed.refresh_token == "stable-refresh"


@pytest.mark.anyio
async def test_status_is_noninteractive_and_redacted() -> None:
    keyring = FakeKeyring()
    storage = KeyringOAuthStorage("https://memory.example/mcp/", keyring_backend=keyring)
    manager = MemoryConnectorAuthManager(
        url="https://memory.example/mcp/", storage=storage
    )
    await storage.set_tokens(
        OAuthToken(
            access_token="access-secret",
            refresh_token="refresh-secret",
            expires_in=3600,
            scope="memory.read memory.write offline_access",
        )
    )

    status = await manager.status()

    assert status.ready is True
    assert status.authorization_required is False
    assert "memory.write" in status.scopes
    assert "secret" not in json.dumps(status.as_dict())


@pytest.mark.anyio
async def test_status_and_provider_allow_oauth_token_with_scope_none() -> None:
    storage = KeyringOAuthStorage(
        "https://memory.example/mcp/", keyring_backend=FakeKeyring()
    )
    await storage.set_tokens(
        OAuthToken(access_token="access-secret", scope=None, expires_in=3600)
    )
    manager = MemoryConnectorAuthManager(
        url="https://memory.example/mcp/", storage=storage
    )

    status = await manager.status()
    provider = await manager.noninteractive_provider()

    assert status.ready is True
    assert status.scopes == ()
    assert provider is not None


@pytest.mark.anyio
async def test_noninteractive_provider_requires_authorization_without_token() -> None:
    manager = MemoryConnectorAuthManager(
        url="https://memory.example/mcp/",
        storage=KeyringOAuthStorage(
            "https://memory.example/mcp/", keyring_backend=FakeKeyring()
        ),
    )

    with pytest.raises(Exception, match="authorization required"):
        await manager.noninteractive_provider()


@pytest.mark.anyio
async def test_noninteractive_provider_restores_expiry_for_sdk_refresh() -> None:
    now = 1_000.0
    storage = KeyringOAuthStorage(
        "https://memory.example/mcp/",
        keyring_backend=FakeKeyring(),
        now=lambda: now,
    )
    await storage.set_tokens(
        OAuthToken(
            access_token="expired-access",
            refresh_token="refresh-secret",
            expires_in=60,
            scope="memory.write offline_access",
        )
    )
    await storage.set_client_info(
        OAuthClientInformationFull(
            client_id="client-id",
            redirect_uris=["http://127.0.0.1:8766/callback"],
        )
    )
    manager = MemoryConnectorAuthManager(
        url="https://memory.example/mcp/", storage=storage, now=lambda: 2_000.0
    )

    provider = await manager.noninteractive_provider()

    assert provider.context.token_expiry_time == 1_060.0


@pytest.mark.anyio
async def test_expired_token_refresh_uses_persisted_discovered_token_endpoint() -> None:
    storage = KeyringOAuthStorage(
        "https://memory.example/mcp/",
        keyring_backend=FakeKeyring(),
        now=lambda: 1_000.0,
    )
    await storage.set_tokens(
        OAuthToken(
            access_token="expired-access",
            refresh_token="refresh-secret",
            expires_in=60,
            scope="memory.write offline_access",
        )
    )
    await storage.set_client_info(
        OAuthClientInformationFull(
            client_id="client-id",
            redirect_uris=["http://127.0.0.1:8766/callback"],
            token_endpoint_auth_method="none",
        )
    )
    await storage.set_server_metadata(
        protected_resource_metadata=ProtectedResourceMetadata(
            resource="https://memory.example/mcp/",
            authorization_servers=["https://auth.example/"],
        ),
        oauth_metadata=OAuthMetadata(
            issuer="https://auth.example/",
            authorization_endpoint="https://auth.example/oauth/authorize",
            token_endpoint="https://auth.example/oauth/token",
        ),
        auth_server_url="https://auth.example/",
    )
    manager = MemoryConnectorAuthManager(
        url="https://memory.example/mcp/", storage=storage, now=lambda: 2_000.0
    )
    provider = await manager.noninteractive_provider()
    requested_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        if request.url == httpx.URL("https://auth.example/oauth/token"):
            assert b"grant_type=refresh_token" in request.content
            assert b"refresh_token=refresh-secret" in request.content
            return httpx.Response(
                200,
                json={
                    "access_token": "refreshed-access",
                    "token_type": "Bearer",
                    "expires_in": 3600,
                    "scope": "memory.write offline_access",
                },
            )
        assert request.url == httpx.URL("https://memory.example/mcp/")
        assert request.headers["Authorization"] == "Bearer refreshed-access"
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(
        auth=provider, transport=httpx.MockTransport(handler)
    ) as client:
        response = await client.get("https://memory.example/mcp/")

    assert response.status_code == 200
    assert requested_urls == [
        "https://auth.example/oauth/token",
        "https://memory.example/mcp/",
    ]
    assert "https://auth.example/token" not in requested_urls


@pytest.mark.anyio
async def test_logout_removes_only_service_owned_credentials() -> None:
    keyring = FakeKeyring()
    storage = KeyringOAuthStorage("https://memory.example/mcp/", keyring_backend=keyring)
    manager = MemoryConnectorAuthManager(
        url="https://memory.example/mcp/", storage=storage
    )
    await storage.set_tokens(OAuthToken(access_token="access-secret"))
    await storage.set_client_info(
        OAuthClientInformationFull(
            client_id="client-id",
            redirect_uris=["http://127.0.0.1:8766/callback"],
        )
    )
    await storage.set_server_metadata(
        protected_resource_metadata=None,
        oauth_metadata=OAuthMetadata(
            issuer="https://auth.example/",
            authorization_endpoint="https://auth.example/oauth/authorize",
            token_endpoint="https://auth.example/oauth/token",
        ),
        auth_server_url="https://auth.example/",
    )

    await manager.logout()

    assert await storage.get_tokens() is None
    assert await storage.get_client_info() is None
    assert (await storage.get_server_metadata()).oauth_metadata is None
