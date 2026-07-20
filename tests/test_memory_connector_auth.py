import json
from pathlib import Path

import pytest
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from app.memory_connector_auth import (
    KeyringOAuthStorage,
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

    await manager.logout()

    assert await storage.get_tokens() is None
    assert await storage.get_client_info() is None
