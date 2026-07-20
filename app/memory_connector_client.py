from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, AsyncContextManager, Callable, Literal, Protocol

import httpx
from mcp import ClientSession
from mcp.client.auth import OAuthFlowError, OAuthTokenError
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, TextContent

from app.memory_connector_auth import (
    MemoryConnectorAuthManager,
    MemoryConnectorAuthorizationRequired,
    resolve_memory_connector_url,
)


class MemoryConnectorClientError(RuntimeError):
    pass


class MemoryConnectorProtocolError(MemoryConnectorClientError):
    pass


class MemoryConnectorRequestError(MemoryConnectorClientError):
    pass


@dataclass(frozen=True)
class MemoryWriteResult:
    episode_uuid: str
    processing_status: str
    duplicate: bool


class _Session(Protocol):
    async def initialize(self) -> Any: ...

    async def call_tool(
        self, name: str, arguments: dict[str, object]
    ) -> CallToolResult: ...


SessionFactory = Callable[[object], AsyncContextManager[_Session]]


class MemoryConnectorClient:
    def __init__(
        self,
        *,
        url: str | None = None,
        auth: MemoryConnectorAuthManager | None = None,
        session_factory: SessionFactory | None = None,
        request_timeout_seconds: float = 60.0,
    ) -> None:
        self.url = resolve_memory_connector_url(url)
        self.auth = auth or MemoryConnectorAuthManager(url=self.url)
        self._session_factory = session_factory or self._default_session
        self._request_timeout_seconds = request_timeout_seconds

    @asynccontextmanager
    async def _default_session(self, provider: object):
        timeout = httpx.Timeout(self._request_timeout_seconds)
        async with httpx.AsyncClient(auth=provider, timeout=timeout) as http_client:
            async with streamable_http_client(
                self.url, http_client=http_client
            ) as (read_stream, write_stream, _get_session_id):
                async with ClientSession(
                    read_stream,
                    write_stream,
                    read_timeout_seconds=timedelta(
                        seconds=self._request_timeout_seconds
                    ),
                ) as session:
                    yield session

    async def memory_write(
        self,
        *,
        data: str,
        type: Literal["text", "message"],
        created_at: str,
        source_description: str,
        wait_for_processing: bool = False,
    ) -> MemoryWriteResult:
        arguments = self._validate_memory_write(
            data=data,
            type=type,
            created_at=created_at,
            source_description=source_description,
            wait_for_processing=wait_for_processing,
        )
        provider = await self.auth.noninteractive_provider()
        try:
            async with self._session_factory(provider) as session:
                await session.initialize()
                result = await session.call_tool("memory_write", arguments)
        except MemoryConnectorAuthorizationRequired:
            raise
        except (OAuthFlowError, OAuthTokenError):
            raise MemoryConnectorAuthorizationRequired(
                "memory connector authorization required"
            ) from None
        except Exception as exc:
            raise MemoryConnectorRequestError(
                f"memory connector request failed ({exc.__class__.__name__})"
            ) from None
        return self._parse_memory_write_result(result)

    def memory_write_sync(self, **kwargs: Any) -> MemoryWriteResult:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.memory_write(**kwargs))
        raise RuntimeError(
            "memory_write_sync cannot run inside an active event loop"
        )

    async def login(
        self,
        *,
        redirect_handler,
        callback_handler,
    ) -> None:
        """Run OAuth only after an explicit caller-provided authorization action."""
        provider = self.auth.interactive_provider(
            redirect_handler=redirect_handler,
            callback_handler=callback_handler,
        )
        try:
            async with self._session_factory(provider) as session:
                await session.initialize()
        except Exception as exc:
            raise MemoryConnectorRequestError(
                f"memory connector login failed ({exc.__class__.__name__})"
            ) from None

    @staticmethod
    def _validate_memory_write(
        *,
        data: str,
        type: str,
        created_at: str,
        source_description: str,
        wait_for_processing: bool,
    ) -> dict[str, object]:
        if type not in {"text", "message"}:
            raise ValueError("memory type must be text or message")
        if not isinstance(data, str) or not data.strip():
            raise ValueError("memory data must be non-empty")
        if not isinstance(source_description, str) or not source_description.strip():
            raise ValueError("memory source_description must be non-empty")
        try:
            parsed_time = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        except (AttributeError, TypeError, ValueError):
            raise ValueError("memory created_at must be ISO-8601") from None
        if parsed_time.tzinfo is None:
            raise ValueError("memory created_at must include a timezone")
        if not isinstance(wait_for_processing, bool):
            raise ValueError("wait_for_processing must be boolean")
        return {
            "data": data,
            "type": type,
            "created_at": created_at,
            "source_description": source_description,
            "wait_for_processing": wait_for_processing,
        }

    @staticmethod
    def _parse_memory_write_result(result: CallToolResult) -> MemoryWriteResult:
        if len(result.content) != 1 or not isinstance(result.content[0], TextContent):
            raise MemoryConnectorProtocolError(
                "memory connector returned an invalid tool result"
            )
        try:
            payload = json.loads(result.content[0].text)
        except (TypeError, ValueError):
            raise MemoryConnectorProtocolError(
                "memory connector returned invalid JSON"
            ) from None
        if not isinstance(payload, dict):
            raise MemoryConnectorProtocolError(
                "memory connector returned an invalid payload"
            )
        duplicate = payload.get("failure_kind") == "duplicate_memory_write"
        episode_uuid = str(
            payload.get("episode_uuid")
            or payload.get("duplicate_of_episode_uuid")
            or payload.get("uuid")
            or ""
        ).strip()
        processing_status = str(payload.get("processing_status") or "").strip()
        if duplicate:
            if not episode_uuid:
                raise MemoryConnectorProtocolError(
                    "memory connector duplicate result lacks an episode ID"
                )
            return MemoryWriteResult(
                episode_uuid=episode_uuid,
                processing_status=processing_status or "duplicate",
                duplicate=True,
            )
        if result.isError or payload.get("ok") is not True:
            raise MemoryConnectorProtocolError("memory connector rejected memory_write")
        if not episode_uuid or not processing_status:
            raise MemoryConnectorProtocolError(
                "memory connector success result is incomplete"
            )
        return MemoryWriteResult(
            episode_uuid=episode_uuid,
            processing_status=processing_status,
            duplicate=False,
        )
