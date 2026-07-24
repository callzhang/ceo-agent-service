"""Compose listener, Codex consumer, and sender without weakening boundaries."""
from __future__ import annotations

import asyncio
import threading
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Callable

from app.feishu.action_delivery import (
    FeishuMessageActionSender,
    recover_orphaned_message_actions,
)
from app.feishu.client import FeishuClientConfig
from app.feishu.consumer import FeishuReplyConsumer
from app.feishu.delivery import FeishuDeliverySender, recover_orphaned_sending
from app.feishu.listener import FeishuIngressListener
from app.feishu.listener import FeishuListenerHealth
from app.feishu.local_notifications import (
    FeishuLocalNotificationWorker,
    recover_orphaned_local_notifications,
)
from app.feishu.media import FeishuMediaResolver
from app.feishu.producer import FeishuReplyProducer
from app.feishu.rate_limit import SlidingWindowMutationBudget


LISTENER_COMPONENT = "feishu-listener"
CONSUMER_COMPONENT = "feishu-consumer"
SENDER_COMPONENT = "feishu-sender"
_OUTBOUND_DRAIN_LIMIT_PER_KIND = 10
_OUTBOUND_MUTATION_KIND: ContextVar[str] = ContextVar(
    "feishu_outbound_mutation_kind", default=""
)

_RUNTIME_HEALTH_LOCK = threading.Lock()
_CURRENT_LISTENER: FeishuIngressListener | None = None
_CURRENT_RUNTIME: "FeishuChannelRuntime | None" = None


def _other_outbound_kind(kind: str) -> str:
    return "action" if kind == "reply" else "reply"


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


def _configured_listener_app_id(listener) -> str:
    """Return a local configured identity without consulting network state."""
    candidates = []
    for owner in (
        getattr(listener, "config", None),
        getattr(listener, "producer", None),
    ):
        value = str(getattr(owner, "app_id", "") or "")
        if value:
            if (
                value != value.strip()
                or len(value) > 256
                or any(ord(character) < 32 for character in value)
            ):
                raise ValueError("Feishu listener configured App ID is invalid")
            candidates.append(value)
    if not candidates:
        raise ValueError("Feishu listener configured App ID is unavailable")
    if any(value != candidates[0] for value in candidates[1:]):
        raise PermissionError("Feishu listener configured App IDs do not match")
    return candidates[0]


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
        media_enabled=config.feishu_media_enabled(),
        media_max_assets=config.feishu_media_max_assets(),
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


def _media_workspace(store, workspace=None) -> Path:
    """Resolve media storage only from an explicit root or this store's DB."""
    if workspace is None:
        store_path = getattr(store, "path", None)
        if store_path is None:
            raise ValueError(
                "Feishu media workspace requires an explicit path or store DB"
            )
        candidate = Path(store_path).parent
    else:
        candidate = Path(workspace)
    if not candidate.exists() or not candidate.is_dir():
        raise ValueError("Feishu media workspace must be an existing directory")
    return candidate.resolve(strict=True)


def build_consumer(
    store,
    runner,
    *,
    leak_check=None,
    media_workspace=None,
) -> FeishuReplyConsumer:
    from app import config

    media_enabled = config.feishu_media_enabled()
    return FeishuReplyConsumer(
        store,
        runner,
        app_id=config.feishu_app_id(),
        context_limit=config.feishu_context_limit(),
        context_lookback_seconds=config.feishu_context_lookback_seconds(),
        leak_check=leak_check,
        media_enabled=media_enabled,
        media_workspace=(
            _media_workspace(store, media_workspace)
            if media_enabled
            else None
        ),
        media_max_assets=config.feishu_media_max_assets(),
        media_max_bytes=config.feishu_media_max_bytes(),
        reaction_enabled=config.feishu_reaction_enabled(),
        handoff_enabled=config.feishu_handoff_enabled(),
        handoff_open_ids=config.feishu_handoff_open_ids(),
        reply_mention_sender=config.feishu_reply_mention_sender_enabled(),
        reply_mention_open_ids=config.feishu_reply_mention_open_ids(),
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


class _ObservedMutationBudget(SlidingWindowMutationBudget):
    """Expose only admission counts needed to retain a denied fair turn."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._observation_lock = threading.Lock()
        self._observations = {
            "reply": [0, 0],
            "action": [0, 0],
        }

    def try_acquire(self) -> bool:
        admitted = super().try_acquire()
        kind = _OUTBOUND_MUTATION_KIND.get()
        if kind in self._observations:
            with self._observation_lock:
                observation = self._observations[kind]
                observation[0] += 1
                observation[1] += int(admitted)
        return admitted

    def observation(self, kind: str) -> tuple[int, int]:
        with self._observation_lock:
            attempts, admissions = self._observations[kind]
        return attempts, admissions


def _build_mutation_budget(
    store,
    *,
    app_id: str,
    max_mutations_per_minute: int,
    wall_clock: Callable[[], float] = time.time,
    monotonic_clock: Callable[[], float] = time.monotonic,
) -> SlidingWindowMutationBudget:
    """Build an App-scoped budget that survives service restarts."""

    durable_method = getattr(store, "try_acquire_feishu_mutation_slot", None)
    durable_acquire = None
    if callable(durable_method) and app_id:
        durable_acquire = lambda: durable_method(
            app_id=app_id,
            max_mutations_per_minute=max_mutations_per_minute,
            now_epoch_ms=int(wall_clock() * 1000),
        )
    return _ObservedMutationBudget(
        max_mutations_per_minute,
        monotonic_clock=monotonic_clock,
        durable_acquire=durable_acquire,
    )


def build_sender(
    store,
    client,
    *,
    mutation_budget: SlidingWindowMutationBudget | None = None,
) -> FeishuDeliverySender:
    from app import config

    max_mutations_per_minute = config.feishu_max_sends_per_minute()
    mutation_budget = mutation_budget or _build_mutation_budget(
        store,
        app_id=config.feishu_app_id(),
        max_mutations_per_minute=max_mutations_per_minute,
    )
    return FeishuDeliverySender(
        store,
        client,
        sender_enabled=config.feishu_sender_enabled(),
        live_send_allowed=config.feishu_live_send_allowed(),
        send_mode=config.feishu_send_mode(),
        max_sends_per_minute=max_mutations_per_minute,
        reply_mention_sender_enabled=(
            config.feishu_reply_mention_sender_enabled
        ),
        reply_mention_open_ids=config.feishu_reply_mention_open_ids,
        mutation_budget=mutation_budget,
    )


def build_action_sender(
    store,
    client,
    *,
    mutation_budget: SlidingWindowMutationBudget | None = None,
) -> FeishuMessageActionSender:
    """Build the gated action drainer around the listener's existing client."""
    from app import config

    max_mutations_per_minute = config.feishu_max_sends_per_minute()
    mutation_budget = mutation_budget or _build_mutation_budget(
        store,
        app_id=config.feishu_app_id(),
        max_mutations_per_minute=max_mutations_per_minute,
    )
    return FeishuMessageActionSender(
        store,
        client,
        sender_enabled=config.feishu_sender_enabled(),
        live_send_allowed=config.feishu_live_send_allowed(),
        reactions_enabled=config.feishu_reaction_enabled(),
        recalls_enabled=config.feishu_recall_enabled(),
        handoff_enabled=config.feishu_handoff_enabled(),
        handoff_target_allowlist=config.feishu_handoff_open_ids(),
        send_mode=config.feishu_send_mode(),
        max_actions_per_minute=max_mutations_per_minute,
        mutation_budget=mutation_budget,
    )


def build_local_notification_worker(
    store, *, app_id: str
) -> FeishuLocalNotificationWorker:
    """Build the offline-only drainer for durable handoff fallbacks."""
    return FeishuLocalNotificationWorker(store, app_id=app_id)


def build_media_resolver(
    store,
    client,
    *,
    workspace,
    max_resource_bytes: int,
    max_event_bytes: int,
    max_event_resources: int,
) -> FeishuMediaResolver:
    return FeishuMediaResolver(
        store=store,
        client=client,
        workspace=_media_workspace(store, workspace),
        max_resource_bytes=max_resource_bytes,
        max_event_bytes=max_event_bytes,
        max_event_resources=max_event_resources,
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
    reaction_enabled: bool = False
    recall_enabled: bool = False
    handoff_enabled: bool = False
    media_enabled: bool = False
    media_workspace: Path | None = None
    media_max_assets: int = 8
    media_max_bytes: int = 20 * 1024 * 1024
    media_event_max_bytes: int = 32 * 1024 * 1024
    media_stale_seconds: int = 5 * 60
    local_notification_stale_seconds: int = 5 * 60
    listener_ready_timeout_seconds: float = 30.0
    sender_interval_seconds: float = 2.0
    sender_factory: Callable[[object, object], FeishuDeliverySender] = build_sender
    action_sender_factory: Callable[
        [object, object], FeishuMessageActionSender
    ] = build_action_sender
    media_factory: Callable[..., FeishuMediaResolver] = build_media_resolver
    local_notification_factory: Callable[..., FeishuLocalNotificationWorker] = (
        build_local_notification_worker
    )
    _loop: asyncio.AbstractEventLoop | None = field(
        default=None, init=False, repr=False
    )
    _outbound_next_kind: str = field(default="reply", init=False, repr=False)
    _outbound_mutation_budget: _ObservedMutationBudget | None = field(
        default=None, init=False, repr=False
    )

    def _record_media_error(self, kind: str, error=None) -> None:
        recorder = getattr(self.listener, "_record_error", None)
        if callable(recorder):
            recorder(kind, error)

    def _record_action_error(self, kind: str, error=None) -> None:
        recorder = getattr(self.listener, "_record_error", None)
        if callable(recorder):
            recorder(kind, error)

    def _record_sender_error(self, kind: str, error=None) -> None:
        recorder = getattr(self.listener, "_record_error", None)
        if callable(recorder):
            recorder(kind, error)

    def _record_local_notification_error(self, kind: str, error=None) -> None:
        recorder = getattr(self.listener, "_record_error", None)
        if callable(recorder):
            recorder(kind, error)

    def _outbound_budget_observation(self, kind: str) -> tuple[int, int]:
        budget = self._outbound_mutation_budget
        if budget is None:
            return 0, 0
        return budget.observation(kind)

    async def _process_outbound_one(self, kind: str, worker) -> tuple[int, int, int]:
        before_attempts, before_admissions = self._outbound_budget_observation(kind)
        token = _OUTBOUND_MUTATION_KIND.set(kind)
        processed = 0
        error = None
        try:
            processed = await worker.process_once(1)
        except Exception as exc:
            error = exc
        finally:
            _OUTBOUND_MUTATION_KIND.reset(token)
        after_attempts, after_admissions = self._outbound_budget_observation(kind)
        if error is not None:
            if kind == "reply":
                self._record_sender_error("feishu_sender_process_failed", error)
            else:
                self._record_action_error("feishu_action_process_failed", error)
            processed = 0
        return (
            int(processed or 0),
            after_attempts - before_attempts,
            after_admissions - before_admissions,
        )

    async def _drain_outbound_once(self, sender, action_sender) -> None:
        """Drain bounded one-row turns and retain the next denied quota turn."""
        workers = {
            kind: worker
            for kind, worker in (("reply", sender), ("action", action_sender))
            if worker is not None
        }
        if not workers:
            return
        start = (
            self._outbound_next_kind
            if self._outbound_next_kind in workers
            else next(iter(workers))
        )
        # Alternate ordinary drain starts too; a quota denial below overrides
        # this with the category that owns the next fair admission.
        self._outbound_next_kind = (
            _other_outbound_kind(start)
            if _other_outbound_kind(start) in workers
            else start
        )
        active = set(workers)
        attempts_by_kind = {kind: 0 for kind in workers}
        turn = start
        while active:
            active = {
                kind
                for kind in active
                if attempts_by_kind[kind] < _OUTBOUND_DRAIN_LIMIT_PER_KIND
            }
            if not active:
                return
            if turn not in active:
                turn = (
                    _other_outbound_kind(turn)
                    if _other_outbound_kind(turn) in active
                    else next(iter(active))
                )
            attempts_by_kind[turn] += 1
            processed, budget_attempts, admissions = await self._process_outbound_one(
                turn, workers[turn]
            )
            if processed <= 0:
                active.discard(turn)
            denied = budget_attempts > admissions
            if denied:
                # No admission means this category keeps its turn until quota
                # reopens. A partial/multi-chunk admission has already had its
                # turn, so the peer owns the next window.
                self._outbound_next_kind = (
                    _other_outbound_kind(turn)
                    if admissions > 0 and _other_outbound_kind(turn) in workers
                    else turn
                )
                return
            turn = (
                _other_outbound_kind(turn)
                if _other_outbound_kind(turn) in active
                else turn
            )

    def _attach_recovered_media_tasks(self, app_id: str) -> None:
        """Close the crash window between terminal media and task attach."""
        events = self.store.list_feishu_events(
            app_id,
            eligibility_status="eligible",
            unqueued_only=True,
            limit=100,
        )
        for event in events:
            try:
                assets = self.store.list_feishu_media_assets(
                    event_record_id=event.id,
                    app_id=event.app_id,
                    message_id=event.message_id,
                    limit=self.media_max_assets + 1,
                )
                if not assets or len(assets) > self.media_max_assets:
                    continue
                if self.store.feishu_media_event_ready_for_enqueue(
                    event.id,
                    app_id=event.app_id,
                    message_id=event.message_id,
                ):
                    self.store.attach_feishu_event_reply_task(event.id)
            except Exception as exc:
                self._record_media_error(
                    "feishu_media_recovery_event_failed", exc
                )

    async def _resolve_media_once(self, resolver: FeishuMediaResolver) -> None:
        try:
            resolutions = await resolver.resolve_pending(
                limit=self.media_max_assets
            )
        except Exception as exc:
            # A persistence/SDK failure outside the resolver's per-asset
            # rejection path must degrade media only, never the WS runtime.
            self._record_media_error("feishu_media_resolve_failed", exc)
            return
        for resolution in resolutions:
            if not resolution.event_ready_for_enqueue:
                continue
            try:
                asset = resolution.asset
                if asset.app_id != resolver.app_id:
                    raise PermissionError(
                        "Feishu media resolution App ID mismatch"
                    )
                if self.store.feishu_media_event_ready_for_enqueue(
                    asset.event_record_id,
                    app_id=asset.app_id,
                    message_id=asset.message_id,
                ):
                    self.store.attach_feishu_event_reply_task(
                        asset.event_record_id
                    )
            except Exception as exc:
                self._record_media_error("feishu_media_attach_failed", exc)
        try:
            # Also covers an asset rejected during insertion (there was no
            # resolver transition to emit a signal) and retries a prior crash
            # or transient attach failure.  The store re-check is atomic.
            self._attach_recovered_media_tasks(resolver.app_id)
        except Exception as exc:
            self._record_media_error("feishu_media_attach_failed", exc)

    async def _run_local_notification_loop(
        self,
        worker: FeishuLocalNotificationWorker,
        *,
        first_pass: asyncio.Event,
    ) -> None:
        first = True
        while True:
            try:
                await worker.process_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._record_local_notification_error(
                    "feishu_local_notification_process_failed", exc
                )
            finally:
                if first:
                    first = False
                    first_pass.set()
            await asyncio.sleep(self.sender_interval_seconds)

    async def run(self) -> None:
        if not self.listener.enabled:
            return
        if (
            self.sender_interval_seconds <= 0
            or self.listener_ready_timeout_seconds <= 0
            or self.local_notification_stale_seconds <= 0
        ):
            raise ValueError("Feishu runtime intervals must be positive")
        if self.media_enabled and not 1 <= self.media_max_assets <= 8:
            raise ValueError("media_max_assets must be between 1 and 8")
        configured_app_id = _configured_listener_app_id(self.listener)
        try:
            recover_orphaned_local_notifications(
                self.store,
                app_id=configured_app_id,
                stale_after_seconds=self.local_notification_stale_seconds,
            )
        except Exception as exc:
            # A local notification is already durable. Recovery failure must
            # not interrupt either this local loop or inbound messages.
            self._record_local_notification_error(
                "feishu_local_notification_recovery_failed", exc
            )
        local_notification_worker = self.local_notification_factory(
            self.store, app_id=configured_app_id
        )
        self._loop = asyncio.get_running_loop()
        local_first_pass = asyncio.Event()
        local_notification_task = asyncio.create_task(
            self._run_local_notification_loop(
                local_notification_worker, first_pass=local_first_pass
            )
        )
        listener_task = asyncio.create_task(self.listener.run())
        ready_task: asyncio.Task | None = None
        try:
            # Let listener.run create its loop-bound readiness event first.
            await asyncio.sleep(0)
            ready_task = asyncio.create_task(
                self.listener.wait_ready(
                    timeout=self.listener_ready_timeout_seconds
                )
            )
            done, _ = await asyncio.wait(
                {listener_task, ready_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if listener_task in done:
                ready_task.cancel()
                await asyncio.gather(ready_task, return_exceptions=True)
                # Even an immediate connect failure cannot overtake the first
                # bounded pass over an already-durable local fallback.
                await local_first_pass.wait()
                await listener_task
                return
            await ready_task
            actions_enabled = bool(
                self.reaction_enabled
                or self.recall_enabled
                or self.handoff_enabled
            )
            connected_app_id = str(
                getattr(self.listener.client, "app_id", "") or ""
            ).strip()
            if connected_app_id and connected_app_id != configured_app_id:
                raise PermissionError(
                    "Feishu connected App ID does not match local configuration"
                )

            sender = None
            if self.sender_enabled:
                recover_orphaned_sending(
                    self.store, app_id=configured_app_id
                )
                sender = self.sender_factory(self.store, self.listener.client)

            action_sender = None
            if actions_enabled:
                try:
                    recover_orphaned_message_actions(
                        self.store,
                        app_id=configured_app_id,
                    )
                except Exception as exc:
                    # Action-outbox recovery failure must not tear down the
                    # receive channel. Pending rows remain locally durable.
                    self._record_action_error(
                        "feishu_action_recovery_failed", exc
                    )
                # Deliberately reuse listener.client.  Constructing another
                # Channel SDK client would create a second competing WS.
                action_sender = self.action_sender_factory(
                    self.store, self.listener.client
                )

            resolver = None
            if self.media_enabled:
                workspace = _media_workspace(self.store, self.media_workspace)
                try:
                    self.store.recover_stale_feishu_media_assets(
                        app_id=configured_app_id,
                        stale_after_seconds=self.media_stale_seconds,
                    )
                    self._attach_recovered_media_tasks(configured_app_id)
                except Exception as exc:
                    self._record_media_error("feishu_media_recovery_failed", exc)
                resolver = self.media_factory(
                    self.store,
                    self.listener.client,
                    workspace=workspace,
                    max_resource_bytes=self.media_max_bytes,
                    max_event_bytes=self.media_event_max_bytes,
                    max_event_resources=self.media_max_assets,
                )

            while not listener_task.done():
                if resolver is not None:
                    await self._resolve_media_once(resolver)
                await self._drain_outbound_once(sender, action_sender)
                await asyncio.sleep(self.sender_interval_seconds)
            await listener_task
        finally:
            await self.listener.stop()
            if ready_task is not None and not ready_task.done():
                ready_task.cancel()
            if not listener_task.done():
                listener_task.cancel()
            if not local_notification_task.done():
                local_notification_task.cancel()
            await asyncio.gather(
                *(
                    task
                    for task in (
                        ready_task,
                        listener_task,
                        local_notification_task,
                    )
                    if task is not None
                ),
                return_exceptions=True,
            )
            self._loop = None


def build_runtime(
    store, *, client_factory=None, media_workspace=None
) -> FeishuChannelRuntime:
    from app import config

    media_enabled = config.feishu_media_enabled()
    mutation_budget = _build_mutation_budget(
        store,
        app_id=config.feishu_app_id(),
        max_mutations_per_minute=config.feishu_max_sends_per_minute(),
    )
    runtime = FeishuChannelRuntime(
        listener=build_listener(store, client_factory=client_factory),
        store=store,
        sender_enabled=config.feishu_sender_enabled(),
        reaction_enabled=config.feishu_reaction_enabled(),
        recall_enabled=config.feishu_recall_enabled(),
        handoff_enabled=config.feishu_handoff_enabled(),
        media_enabled=media_enabled,
        media_workspace=(
            _media_workspace(store, media_workspace)
            if media_enabled
            else None
        ),
        media_max_assets=config.feishu_media_max_assets(),
        media_max_bytes=config.feishu_media_max_bytes(),
        media_event_max_bytes=config.feishu_media_event_max_bytes(),
        sender_factory=partial(
            build_sender, mutation_budget=mutation_budget
        ),
        action_sender_factory=partial(
            build_action_sender, mutation_budget=mutation_budget
        ),
    )
    if isinstance(mutation_budget, _ObservedMutationBudget):
        runtime._outbound_mutation_budget = mutation_budget
    _register_runtime(runtime)
    return runtime


def run_channel_runtime(runtime: FeishuChannelRuntime) -> None:
    asyncio.run(runtime.run())


def approve_delivery_on_runtime(
    store,
    delivery_id: int,
    *,
    expected_approval_hash: str,
    approved_by: str,
    timeout: float = 60,
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
        return await sender.approve_and_send(
            delivery_id,
            expected_approval_hash=expected_approval_hash,
            approved_by=approved_by,
        )

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
