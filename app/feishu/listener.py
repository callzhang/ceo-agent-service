"""Async WebSocket lifecycle for the official Feishu Channel SDK."""
from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Callable

from app.feishu.client import FeishuClientConfig, build_channel


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class FeishuListenerHealth:
    status: str = "stopped"
    connected_at: str = ""
    last_event_at: str = ""
    last_reconnect_at: str = ""
    last_error_kind: str = ""
    events_seen: int = 0


class FeishuIngressListener:
    """Own exactly one SDK connection and hand messages to the fast producer."""

    def __init__(
        self,
        producer,
        config: FeishuClientConfig,
        *,
        enabled: bool = False,
        client_factory: Callable[..., Any] = build_channel,
    ):
        self.producer = producer
        self.config = config
        self.enabled = enabled
        self.client_factory = client_factory
        self.client = None
        self._health = FeishuListenerHealth(
            status="disabled" if not enabled else "stopped"
        )
        self._health_lock = threading.Lock()
        self._stop_event: asyncio.Event | None = None
        self._ready_event: asyncio.Event | None = None

    @property
    def health(self) -> FeishuListenerHealth:
        with self._health_lock:
            return self._health

    def _update(self, **changes) -> None:
        with self._health_lock:
            self._health = replace(self._health, **changes)

    def _record_error(self, kind: str, error: Any | None = None) -> None:
        """Write only a fixed code and exception class; never raw error text."""
        store = getattr(self.producer, "store", None)
        recorder = getattr(store, "record_error", None)
        if recorder is None:
            return
        error_kind = type(error).__name__ if error is not None else "none"
        try:
            recorder(None, None, kind, f"{kind}:{error_kind}")
        except Exception:
            # Observability failure must not crash the WebSocket callback.
            pass

    async def _on_message(self, sdk_message: Any) -> None:
        try:
            self.producer.ingest_sdk_message(sdk_message)
        except Exception as exc:
            # Never include exception text: SDK/persistence errors may contain a
            # credential or raw payload.  The class name is enough for doctor.
            self._update(
                status="degraded", last_error_kind=type(exc).__name__
            )
            self._record_error("feishu_ingress_callback_failed", exc)
            return
        health = self.health
        self._update(
            status="ready",
            last_event_at=_now(),
            events_seen=health.events_seen + 1,
            last_error_kind="",
        )

    async def _on_error(self, error: Any) -> None:
        self._update(status="degraded", last_error_kind=type(error).__name__)
        self._record_error("feishu_sdk_error", error)

    async def _on_reconnecting(self, *_args) -> None:
        self._update(status="reconnecting")
        self._record_error("feishu_sdk_reconnecting")

    async def _on_reconnected(self, *_args) -> None:
        self._update(status="ready", last_reconnect_at=_now(), last_error_kind="")

    async def wait_ready(self, timeout: float = 30) -> None:
        if self._ready_event is None:
            raise RuntimeError("Feishu listener has not started")
        await asyncio.wait_for(self._ready_event.wait(), timeout=timeout)

    async def run(self, *, ready_timeout: float = 30) -> None:
        if not self.enabled:
            self._update(status="disabled")
            return
        self._stop_event = asyncio.Event()
        self._ready_event = asyncio.Event()
        self._update(status="connecting", last_error_kind="")
        self.client = self.client_factory(
            self.config,
            on_message=self._on_message,
            on_error=self._on_error,
            on_reconnecting=self._on_reconnecting,
            on_reconnected=self._on_reconnected,
        )
        try:
            await self.client.connect_until_ready(timeout=ready_timeout)
            self._update(status="ready", connected_at=_now())
            self._ready_event.set()
            await self._stop_event.wait()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._update(status="failed", last_error_kind=type(exc).__name__)
            self._record_error("feishu_connect_failed", exc)
            raise
        finally:
            if self.client is not None:
                try:
                    await self.client.disconnect()
                except Exception as exc:
                    self._update(last_error_kind=type(exc).__name__)
                    self._record_error("feishu_disconnect_failed", exc)
            if self.health.status not in {"failed", "disabled"}:
                self._update(status="stopped")

    async def stop(self) -> None:
        if self._stop_event is not None:
            self._stop_event.set()


def run_listener(listener: FeishuIngressListener) -> None:
    """Synchronous service-thread entry point."""
    asyncio.run(listener.run())
