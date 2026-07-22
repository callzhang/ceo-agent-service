"""Compose listener, Codex consumer, and sender without weakening boundaries."""
from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from app.feishu.client import FeishuClientConfig
from app.feishu.consumer import FeishuReplyConsumer
from app.feishu.delivery import FeishuDeliverySender, recover_orphaned_sending
from app.feishu.listener import FeishuIngressListener
from app.feishu.listener import FeishuListenerHealth
from app.feishu.producer import FeishuReplyProducer


LISTENER_COMPONENT = "feishu-listener"
CONSUMER_COMPONENT = "feishu-consumer"
SENDER_COMPONENT = "feishu-sender"

_RUNTIME_HEALTH_LOCK = threading.Lock()
_CURRENT_LISTENER: FeishuIngressListener | None = None
_CURRENT_RUNTIME: "FeishuChannelRuntime | None" = None


def _register_listener(listener: FeishuIngressListener) -> None:
    global _CURRENT_LISTENER
    with _RUNTIME_HEALTH_LOCK:
        _CURRENT_LISTENER = listener


def _register_runtime(runtime: "FeishuChannelRuntime") -> None:
    global _CURRENT_RUNTIME
    with _RUNTIME_HEALTH_LOCK:
        _CURRENT_RUNTIME = runtime
    _register_listener(runtime.listener)


def current_health() -> FeishuListenerHealth | None:
    """Thread-safe audit-page snapshot; contains no credentials or raw events."""
    with _RUNTIME_HEALTH_LOCK:
        listener = _CURRENT_LISTENER
    return None if listener is None else listener.health


def runtime_health() -> FeishuListenerHealth | None:
    return current_health()


def component_names(
    *, enabled: bool, configured: bool, sender_enabled: bool
) -> tuple[str, ...]:
    if not enabled or not configured:
        return ()
    names = [LISTENER_COMPONENT, CONSUMER_COMPONENT]
    if sender_enabled:
        names.append(SENDER_COMPONENT)
    return tuple(names)


def build_client_config_from_env() -> FeishuClientConfig:
    """Read secrets only at explicit runtime construction, never at import."""
    from app import config

    return FeishuClientConfig(
        app_id=config.feishu_app_id(),
        app_secret=config.feishu_app_secret(),
        security_mode=config.feishu_security_mode(),
    )


def build_producer(store) -> FeishuReplyProducer:
    from app import config

    return FeishuReplyProducer(
        store,
        app_id=config.feishu_app_id(),
        stale_event_seconds=config.feishu_stale_event_seconds(),
    )


def build_listener(store, *, client_factory=None) -> FeishuIngressListener:
    from app import config
    from app.feishu.client import build_channel

    return FeishuIngressListener(
        build_producer(store),
        build_client_config_from_env(),
        enabled=config.feishu_enabled(),
        client_factory=client_factory or build_channel,
    )


def build_consumer(store, runner, *, leak_check=None) -> FeishuReplyConsumer:
    from app import config

    return FeishuReplyConsumer(
        store,
        runner,
        context_limit=config.feishu_context_limit(),
        leak_check=leak_check,
    )


def build_decision_runner(
    *,
    workspace,
    codex_bin: str = "codex",
    executor=None,
    timeout_seconds: int = 1200,
    idle_timeout_seconds: int = 900,
):
    """Build the only Codex policy accepted by the Feishu consumer."""
    from app.codex_decision import CodexDecisionRunner
    from app.codex_runner import CODEX_TOOL_MODE_NONE

    return CodexDecisionRunner(
        workspace=workspace,
        codex_bin=codex_bin,
        executor=executor,
        timeout_seconds=timeout_seconds,
        idle_timeout_seconds=idle_timeout_seconds,
        tool_mode=CODEX_TOOL_MODE_NONE,
    )


def build_sender(store, client) -> FeishuDeliverySender:
    from app import config

    return FeishuDeliverySender(
        store,
        client,
        sender_enabled=config.feishu_sender_enabled(),
        live_send_allowed=config.feishu_live_send_allowed(),
        send_mode=config.feishu_send_mode(),
        max_sends_per_minute=config.feishu_max_sends_per_minute(),
    )


@dataclass
class FeishuChannelRuntime:
    """One event loop shared by the single WS listener and optional sender.

    A separate sender connection would consume events from the same application
    unpredictably.  This runtime therefore keeps send calls on the listener's
    already-connected SDK channel while the Codex consumer stays in its own
    blocking service thread.
    """

    listener: FeishuIngressListener
    store: object
    sender_enabled: bool = False
    sender_interval_seconds: float = 2.0
    sender_factory: Callable[[object, object], FeishuDeliverySender] = build_sender
    _loop: asyncio.AbstractEventLoop | None = field(
        default=None, init=False, repr=False
    )

    async def run(self) -> None:
        if not self.listener.enabled:
            return
        if self.sender_interval_seconds <= 0:
            raise ValueError("sender_interval_seconds must be positive")
        self._loop = asyncio.get_running_loop()
        listener_task = asyncio.create_task(self.listener.run())
        try:
            # Let listener.run create its loop-bound readiness event first.
            await asyncio.sleep(0)
            ready_task = asyncio.create_task(self.listener.wait_ready(timeout=30))
            done, _ = await asyncio.wait(
                {listener_task, ready_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if listener_task in done:
                ready_task.cancel()
                await asyncio.gather(ready_task, return_exceptions=True)
                await listener_task
                return
            await ready_task
            if not self.sender_enabled:
                await listener_task
                return
            client_app_id = str(
                getattr(self.listener.client, "app_id", "") or ""
            ).strip()
            if not client_app_id:
                raise RuntimeError("Feishu runtime client App ID is unavailable")
            recover_orphaned_sending(self.store, app_id=client_app_id)
            sender = self.sender_factory(self.store, self.listener.client)
            while not listener_task.done():
                await sender.process_once()
                await asyncio.sleep(self.sender_interval_seconds)
            await listener_task
        finally:
            await self.listener.stop()
            if not listener_task.done():
                listener_task.cancel()
            await asyncio.gather(listener_task, return_exceptions=True)
            self._loop = None


def build_runtime(store, *, client_factory=None) -> FeishuChannelRuntime:
    from app import config

    runtime = FeishuChannelRuntime(
        listener=build_listener(store, client_factory=client_factory),
        store=store,
        sender_enabled=config.feishu_sender_enabled(),
    )
    _register_runtime(runtime)
    return runtime


def run_channel_runtime(runtime: FeishuChannelRuntime) -> None:
    asyncio.run(runtime.run())


def approve_delivery_on_runtime(
    store, delivery_id: int, *, timeout: float = 60
):
    """Approve through the single active listener loop; never open another WS.

    This remains an in-process helper for callers that already own the runtime.
    The CLI only writes durable approval state and never creates a short-lived
    second connection.
    """
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    with _RUNTIME_HEALTH_LOCK:
        runtime = _CURRENT_RUNTIME
    if runtime is None or runtime._loop is None or runtime._loop.is_closed():
        raise RuntimeError("Feishu runtime is not active")
    if runtime.listener.health.status != "ready" or runtime.listener.client is None:
        raise RuntimeError("Feishu runtime is not ready")
    try:
        calling_loop = asyncio.get_running_loop()
    except RuntimeError:
        calling_loop = None
    if calling_loop is runtime._loop:
        raise RuntimeError("use async sender from inside the Feishu runtime")

    async def _approve():
        sender = runtime.sender_factory(store, runtime.listener.client)
        return await sender.approve_and_send(delivery_id)

    future = asyncio.run_coroutine_threadsafe(_approve(), runtime._loop)
    try:
        return future.result(timeout=timeout)
    except TimeoutError:
        future.cancel()
        raise TimeoutError("Feishu runtime approval timed out") from None


def run_consume_once(store, runner, *, limit: int = 50, leak_check=None) -> int:
    return build_consumer(store, runner, leak_check=leak_check).run_once(limit)


def run_consumer_loop(
    store,
    runner,
    *,
    interval_seconds: float = 2.0,
    limit: int = 50,
    stop: Callable[[], bool] | None = None,
) -> None:
    """Blocking service-thread loop; contains no send client."""
    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be positive")
    should_stop = stop or (lambda: False)
    consumer = build_consumer(store, runner)
    while not should_stop():
        consumer.run_once(limit)
        time.sleep(interval_seconds)
