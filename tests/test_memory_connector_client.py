import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import pytest
from mcp.client.auth import OAuthFlowError
from mcp.types import CallToolResult, TextContent

from app.memory_connector_auth import MemoryConnectorAuthorizationRequired
from app.memory_connector_client import (
    MemoryConnectorClient,
    MemoryConnectorProtocolError,
    MemoryConnectorRequestError,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class FakeAuth:
    def __init__(self, provider: object | None = None, error: Exception | None = None):
        self.provider = provider or object()
        self.error = error

    async def noninteractive_provider(self) -> object:
        if self.error:
            raise self.error
        return self.provider

    def interactive_provider(self, *, redirect_handler, callback_handler) -> object:
        assert redirect_handler is not None
        assert callback_handler is not None
        return self.provider


class FakeSession:
    def __init__(self, result: CallToolResult | Exception) -> None:
        self.result = result
        self.initialized = False
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def initialize(self) -> None:
        self.initialized = True

    async def call_tool(self, name: str, arguments: dict[str, object]) -> CallToolResult:
        self.calls.append((name, arguments))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def session_factory(session: FakeSession):
    @asynccontextmanager
    async def factory(provider: object):
        assert provider is not None
        yield session

    return factory


def tool_result(payload: object, *, is_error: bool = False) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(payload))],
        isError=is_error,
    )


@pytest.mark.anyio
async def test_memory_write_calls_initialized_mcp_session_with_exact_arguments() -> None:
    session = FakeSession(
        tool_result(
            {
                "ok": True,
                "episode_uuid": "episode-1",
                "processing_status": "queued",
            }
        )
    )
    client = MemoryConnectorClient(
        url="https://memory.example/mcp/",
        auth=FakeAuth(),
        session_factory=session_factory(session),
    )
    created_at = "2026-07-20T10:00:00+08:00"

    result = await client.memory_write(
        data="Durable decision.",
        type="text",
        created_at=created_at,
        source_description="ceo-agent-memory:abc",
    )

    assert session.initialized is True
    assert session.calls == [
        (
            "memory_write",
            {
                "data": "Durable decision.",
                "type": "text",
                "created_at": created_at,
                "source_description": "ceo-agent-memory:abc",
                "wait_for_processing": False,
            },
        )
    ]
    assert result.episode_uuid == "episode-1"
    assert result.processing_status == "queued"
    assert result.duplicate is False


@pytest.mark.anyio
async def test_duplicate_memory_write_is_idempotent_success_even_for_error_result() -> None:
    session = FakeSession(
        tool_result(
            {
                "ok": False,
                "status": "failed",
                "failure_kind": "duplicate_memory_write",
                "duplicate_of_episode_uuid": "episode-existing",
                "processing_status": "duplicate",
            },
            is_error=True,
        )
    )
    client = MemoryConnectorClient(
        url="https://memory.example/mcp/",
        auth=FakeAuth(),
        session_factory=session_factory(session),
    )

    result = await client.memory_write(
        data="Durable decision.",
        type="message",
        created_at="2026-07-20T02:00:00Z",
        source_description="ceo-agent-memory:abc",
    )

    assert result.episode_uuid == "episode-existing"
    assert result.duplicate is True


@pytest.mark.anyio
async def test_memory_write_rejects_invalid_input_before_transport() -> None:
    session = FakeSession(tool_result({"ok": True}))
    client = MemoryConnectorClient(
        url="https://memory.example/mcp/",
        auth=FakeAuth(),
        session_factory=session_factory(session),
    )

    with pytest.raises(ValueError):
        await client.memory_write(
            data="x",
            type="project_summary",
            created_at="not-a-date",
            source_description="source",
        )
    assert session.calls == []


@pytest.mark.anyio
async def test_protocol_errors_do_not_echo_payload_or_credentials() -> None:
    sensitive = "Bearer access-secret"
    session = FakeSession(
        CallToolResult(content=[TextContent(type="text", text=sensitive)], isError=True)
    )
    client = MemoryConnectorClient(
        url="https://memory.example/mcp/",
        auth=FakeAuth(),
        session_factory=session_factory(session),
    )

    with pytest.raises(MemoryConnectorProtocolError) as caught:
        await client.memory_write(
            data="private durable content",
            type="text",
            created_at=datetime.now(timezone.utc).isoformat(),
            source_description="private-source",
        )

    message = str(caught.value)
    assert "access-secret" not in message
    assert "private durable content" not in message
    assert "private-source" not in message


@pytest.mark.anyio
async def test_auth_required_is_reported_without_opening_interactive_flow() -> None:
    client = MemoryConnectorClient(
        url="https://memory.example/mcp/",
        auth=FakeAuth(error=MemoryConnectorAuthorizationRequired("authorization required")),
        session_factory=session_factory(FakeSession(tool_result({}))),
    )

    with pytest.raises(MemoryConnectorAuthorizationRequired):
        await client.memory_write(
            data="x",
            type="text",
            created_at="2026-07-20T02:00:00Z",
            source_description="source",
        )


@pytest.mark.anyio
async def test_readiness_check_only_initializes_noninteractive_session() -> None:
    session = FakeSession(tool_result({"ok": True}))
    client = MemoryConnectorClient(
        url="https://memory.example/mcp/",
        auth=FakeAuth(),
        session_factory=session_factory(session),
    )

    await client.ensure_ready()

    assert session.initialized is True
    assert session.calls == []


@pytest.mark.anyio
async def test_revoked_token_oauth_flow_is_normalized_as_auth_required() -> None:
    session = FakeSession(OAuthFlowError("missing redirect handler with access-secret"))
    client = MemoryConnectorClient(
        url="https://memory.example/mcp/",
        auth=FakeAuth(),
        session_factory=session_factory(session),
    )

    with pytest.raises(MemoryConnectorAuthorizationRequired) as caught:
        await client.memory_write(
            data="x",
            type="text",
            created_at="2026-07-20T02:00:00Z",
            source_description="source",
        )

    assert "access-secret" not in str(caught.value)


@pytest.mark.anyio
async def test_login_uses_interactive_provider_only_when_explicitly_called() -> None:
    session = FakeSession(tool_result({}))
    auth = FakeAuth()
    client = MemoryConnectorClient(
        url="https://memory.example/mcp/",
        auth=auth,
        session_factory=session_factory(session),
    )

    async def redirect_handler(_url: str) -> None:
        return None

    async def callback_handler() -> tuple[str, str | None]:
        return ("code", "state")

    await client.login(
        redirect_handler=redirect_handler,
        callback_handler=callback_handler,
    )

    assert session.initialized is True


@pytest.mark.anyio
async def test_transport_exception_is_redacted() -> None:
    session = FakeSession(RuntimeError("request failed with refresh-secret"))
    client = MemoryConnectorClient(
        url="https://memory.example/mcp/",
        auth=FakeAuth(),
        session_factory=session_factory(session),
    )

    with pytest.raises(MemoryConnectorRequestError) as caught:
        await client.memory_write(
            data="private data",
            type="text",
            created_at="2026-07-20T02:00:00Z",
            source_description="source",
        )

    assert "refresh-secret" not in str(caught.value)
    assert "RuntimeError" in str(caught.value)
