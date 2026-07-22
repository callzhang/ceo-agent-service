import asyncio
from concurrent.futures import ThreadPoolExecutor
import threading
from types import SimpleNamespace

import pytest

from app.dingtalk_models import CodexAction, CodexDecision
from app.feishu.consumer import FeishuReplyConsumer
from app.feishu.delivery import FeishuDeliverySender, delivery_idempotency_key
from app.feishu.listener import FeishuListenerHealth
from app.feishu.local_notifications import FeishuLocalNotificationWorker
from app.feishu.models import FeishuInboundMessage
from app.feishu import service
from app.feishu.service import component_names
from app.store import AutoReplyStore
from tests.feishu.fakes import FakeDeliveryClient, FakeRunner


def test_components_are_absent_when_disabled_or_unconfigured():
    assert component_names(enabled=False, configured=True, sender_enabled=True) == ()
    assert component_names(enabled=True, configured=False, sender_enabled=True) == ()


def test_receive_and_consumer_start_without_sender():
    assert component_names(
        enabled=True, configured=True, sender_enabled=False
    ) == ("feishu-listener", "feishu-consumer")


def test_sender_is_a_separate_explicit_component():
    assert component_names(
        enabled=True, configured=True, sender_enabled=True
    ) == ("feishu-listener", "feishu-consumer", "feishu-sender")


def test_decision_runner_is_hard_isolated_from_all_tools(tmp_path):
    runner = service.build_decision_runner(workspace=tmp_path)

    assert runner.tool_mode == "none"
    command = runner.runner.build_command("reply", None)
    assert "tools.enabled_tools=[]" in command
    assert "--ignore-user-config" in command
    assert "--dangerously-bypass-approvals-and-sandbox" not in command


def test_build_consumer_threads_configured_context_lookback(monkeypatch):
    monkeypatch.setenv("CEO_FEISHU_ENABLED", "0")
    monkeypatch.setenv("CEO_FEISHU_APP_ID", "cli_test")
    monkeypatch.setenv("CEO_FEISHU_EVENT_RETENTION_DAYS", "30")
    monkeypatch.setenv("CEO_FEISHU_CONTEXT_LOOKBACK_SECONDS", "321")
    runner = SimpleNamespace(tool_mode="none", timeout_seconds=0)

    consumer = service.build_consumer(SimpleNamespace(), runner)

    assert consumer.context_lookback_seconds == 321


def test_builders_thread_current_reply_mention_identity_policy(monkeypatch):
    monkeypatch.setenv("CEO_FEISHU_ENABLED", "1")
    monkeypatch.setenv("CEO_FEISHU_SENDER_ENABLED", "1")
    monkeypatch.setenv("CEO_FEISHU_REPLY_MENTION_SENDER", "1")
    monkeypatch.setenv("CEO_FEISHU_REPLY_MENTION_OPEN_IDS", "ou_1,ou_1")
    monkeypatch.setenv("CEO_FEISHU_APP_ID", "cli_test")
    monkeypatch.setenv("CEO_NOT_SEND_MESSAGE", "0")
    runner = SimpleNamespace(tool_mode="none", timeout_seconds=0)

    consumer = service.build_consumer(SimpleNamespace(), runner)
    sender = service.build_sender(SimpleNamespace(), SimpleNamespace())

    assert consumer.reply_mention_sender is True
    assert consumer.reply_mention_open_ids == ("ou_1",)
    delivery = SimpleNamespace(mention_open_ids=("ou_1",))
    assert sender._reply_mentions_currently_authorized(delivery) is True

    monkeypatch.setenv("CEO_FEISHU_REPLY_MENTION_OPEN_IDS", "")
    assert sender._reply_mentions_currently_authorized(delivery) is False


def test_runtime_health_returns_safe_listener_snapshot():
    class Listener:
        health = FeishuListenerHealth(status="ready", connected_at="now")

    service._register_listener(Listener())
    health = service.current_health()
    assert health.status == "ready"
    assert not hasattr(health, "app_secret")


def _delivery_store(tmp_path):
    store = AutoReplyStore(tmp_path / "feishu-runtime.sqlite3")
    event = store.record_feishu_event(
        FeishuInboundMessage(
            event_id="evt_1",
            app_id="cli_test",
            message_id="om_1",
            chat_id="oc_1",
            chat_type="group",
            chat_title="Group",
            sender_open_id="ou_1",
            sender_name="Alex",
            message_type="text",
            mentioned_bot=True,
            body_text="hi",
            event_create_time="2026-07-22T03:20:00+00:00",
            received_at="2026-07-22T03:20:01+00:00",
        ),
        eligibility_status="eligible",
        store_body=True,
    )
    task = next(
        row
        for row in store.list_reply_tasks(channel="feishu")
        if row.id == event.reply_task_id
    )
    attempt_id = store.record_reply_attempt(
        conversation_id=task.conversation_id,
        conversation_title=task.conversation_title,
        trigger_message_id=event.message_id,
        trigger_sender=event.sender_name,
        trigger_text=event.body_text,
        action="send_reply",
        sensitivity_kind="general",
        draft_reply_text="收到",
        send_status="pending",
        channel="feishu",
    )
    delivery = store.create_feishu_delivery(
        reply_task_id=event.reply_task_id,
        attempt_id=attempt_id,
        app_id="cli_test",
        chat_id="oc_1",
        reply_to_message_id="om_1",
        reply_in_thread=False,
        reply_text="收到",
        idempotency_key=delivery_idempotency_key(
            app_id="cli_test",
            reply_task_id=event.reply_task_id,
            trigger_message_id="om_1",
        ),
    )
    return store, delivery


def test_audit_approval_uses_active_runtime_client_without_second_ws(tmp_path):
    store, delivery = _delivery_store(tmp_path)
    client = FakeDeliveryClient()

    async def fetch_message_state(app_id, message_id):
        assert app_id == "cli_test" and message_id == "om_1"
        return SimpleNamespace(state="exists")

    client.fetch_message_state = fetch_message_state

    runtime = service.FeishuChannelRuntime(
        listener=SimpleNamespace(
            health=FeishuListenerHealth(status="ready"), client=client
        ),
        store=store,
        sender_factory=lambda st, cl: FeishuDeliverySender(
            st,
            cl,
            sender_enabled=True,
            live_send_allowed=True,
            send_mode="confirm",
        ),
    )
    loop = asyncio.new_event_loop()
    ready = threading.Event()

    def run_loop():
        asyncio.set_event_loop(loop)
        ready.set()
        loop.run_forever()

    thread = threading.Thread(target=run_loop)
    thread.start()
    ready.wait(2)
    runtime._loop = loop
    service._register_runtime(runtime)
    try:
        outcome = service.approve_delivery_on_runtime(
            store,
            delivery.id,
            expected_approval_hash=delivery.approval_hash,
            approved_by="operator",
            timeout=2,
        )
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(2)
        loop.close()
        runtime._loop = None
    assert outcome.status == "sent"
    assert len(client.deliveries) == 1


def test_audit_approval_fails_closed_without_active_runtime(tmp_path):
    store, delivery = _delivery_store(tmp_path)
    with service._RUNTIME_HEALTH_LOCK:
        service._CURRENT_RUNTIME = None
    with pytest.raises(RuntimeError, match="not active"):
        service.approve_delivery_on_runtime(
            store,
            delivery.id,
            expected_approval_hash=delivery.approval_hash,
            approved_by="operator",
            timeout=1,
        )


def test_runtime_approval_cannot_self_source_hash_from_delivery_id(tmp_path):
    store, delivery = _delivery_store(tmp_path)
    with pytest.raises(TypeError, match="expected_approval_hash"):
        service.approve_delivery_on_runtime(
            store,
            delivery.id,
            approved_by="operator",
            timeout=1,
        )


class _MediaRuntimeListener:
    enabled = True

    def __init__(self):
        self.config = SimpleNamespace(app_id="cli_test")
        self.client = SimpleNamespace(app_id="cli_test")
        self.health = FeishuListenerHealth(status="stopped")
        self.started = False
        self.stopped = False
        self.errors = []

    async def run(self):
        self.started = True
        self.health = FeishuListenerHealth(status="ready")
        while not self.stopped:
            await asyncio.sleep(0)

    async def wait_ready(self, timeout=30):
        del timeout
        while not self.started:
            await asyncio.sleep(0)

    async def stop(self):
        self.stopped = True

    def _record_error(self, kind, error=None):
        self.errors.append((kind, type(error).__name__ if error else "none"))


class _MediaRuntimeStore:
    def __init__(self, path):
        self.path = path
        self.recoveries = []
        self.local_notification_recoveries = []
        self.attached = []

    def recover_stale_feishu_media_assets(self, **kwargs):
        self.recoveries.append(kwargs)
        return 0

    def list_feishu_events(self, *_args, **_kwargs):
        return []

    def list_feishu_media_assets(self, **_kwargs):
        return []

    def feishu_media_event_ready_for_enqueue(self, *_args, **_kwargs):
        return True

    def attach_feishu_event_reply_task(self, event_record_id):
        self.attached.append(event_record_id)

    def recover_stale_feishu_local_notifications(self, **kwargs):
        self.local_notification_recoveries.append(kwargs)
        return 0

    def claim_feishu_local_notifications(self, *_args, **_kwargs):
        return []


def test_runtime_drains_local_fallback_without_any_remote_send_feature(tmp_path):
    listener = _MediaRuntimeListener()
    listener.client = SimpleNamespace()
    store = _MediaRuntimeStore(tmp_path / "runtime.sqlite3")
    factory_calls = []

    class LocalWorker:
        async def process_once(self):
            listener.stopped = True
            return 0

    def local_factory(st, *, app_id):
        factory_calls.append((st, app_id))
        return LocalWorker()

    runtime = service.FeishuChannelRuntime(
        listener=listener,
        store=store,
        local_notification_factory=local_factory,
        sender_interval_seconds=0.001,
    )

    asyncio.run(runtime.run())

    assert factory_calls == [(store, "cli_test")]
    assert store.local_notification_recoveries == [
        {"app_id": "cli_test", "stale_after_seconds": 300, "now": None}
    ]


def _local_fallback_store(tmp_path):
    store = AutoReplyStore(tmp_path / "local-fallback.sqlite3")
    store.record_feishu_event(
        FeishuInboundMessage(
            event_id="evt_local_fallback",
            app_id="cli_test",
            message_id="om_local_fallback",
            chat_id="oc_local_fallback",
            chat_type="group",
            chat_title="Operations",
            sender_open_id="ou_requester",
            sender_name="Alex",
            message_type="text",
            mentioned_bot=True,
            body_text="needs a human",
            event_create_time="2026-07-22T03:20:00+00:00",
            received_at="2026-07-22T03:20:01+00:00",
        ),
        eligibility_status="eligible",
        store_body=True,
    )
    FeishuReplyConsumer(
        store,
        FakeRunner(CodexDecision(action=CodexAction.HANDOFF_TO_HUMAN)),
        app_id="cli_test",
    ).run_once(1)
    return store


class _UnavailableListener:
    enabled = True

    def __init__(self, *, fail=False):
        self.config = SimpleNamespace(app_id="cli_test")
        self.client = None
        self.fail = fail
        self.stopped = False
        self.run_calls = 0
        self.errors = []

    async def run(self):
        self.run_calls += 1
        if self.fail:
            await asyncio.sleep(0)
            raise RuntimeError("connect detail must stay private")
        while not self.stopped:
            await asyncio.sleep(0)

    async def wait_ready(self, timeout=30):
        await asyncio.wait_for(asyncio.Event().wait(), timeout=timeout)

    async def stop(self):
        self.stopped = True

    def _record_error(self, kind, error=None):
        self.errors.append((kind, type(error).__name__ if error else "none"))


@pytest.mark.parametrize("fail", [True, False])
def test_local_fallback_drains_during_connect_failure_or_never_ready(
    tmp_path, fail
):
    store = _local_fallback_store(tmp_path)
    listener = _UnavailableListener(fail=fail)
    calls = []
    runtime = service.FeishuChannelRuntime(
        listener=listener,
        store=store,
        listener_ready_timeout_seconds=0.02,
        sender_interval_seconds=0.001,
        local_notification_factory=lambda st, *, app_id: (
            FeishuLocalNotificationWorker(
                st,
                app_id=app_id,
                notifier=lambda **payload: calls.append(payload),
            )
        ),
    )

    with pytest.raises((RuntimeError, TimeoutError)) as exc_info:
        asyncio.run(runtime.run())

    assert listener.run_calls == 1
    if fail:
        assert type(exc_info.value) is RuntimeError
    assert len(calls) == 1
    [notification] = store.list_feishu_local_notifications(app_id="cli_test")
    assert notification.status == "sent"
    assert listener.stopped is True
    assert runtime._loop is None


def test_listener_total_disable_never_starts_local_fallback(tmp_path):
    store = _local_fallback_store(tmp_path)
    listener = _UnavailableListener()
    listener.enabled = False
    factory_calls = []
    runtime = service.FeishuChannelRuntime(
        listener=listener,
        store=store,
        local_notification_factory=lambda *_args, **_kwargs: factory_calls.append(
            True
        ),
    )

    asyncio.run(runtime.run())

    assert factory_calls == []
    [notification] = store.list_feishu_local_notifications(app_id="cli_test")
    assert notification.status == "pending"


def test_connected_app_id_must_match_local_configuration(tmp_path):
    listener = _MediaRuntimeListener()
    listener.client = SimpleNamespace(app_id="cli_other")
    store = _MediaRuntimeStore(tmp_path / "runtime.sqlite3")

    with pytest.raises(PermissionError, match="does not match"):
        asyncio.run(
            service.FeishuChannelRuntime(
                listener=listener,
                store=store,
                sender_interval_seconds=0.001,
            ).run()
        )


def test_runtime_cancellation_stops_independent_local_loop(tmp_path):
    async def scenario():
        listener = _MediaRuntimeListener()
        store = _MediaRuntimeStore(tmp_path / "runtime.sqlite3")
        second_pass = asyncio.Event()

        class BlockingWorker:
            def __init__(self):
                self.calls = 0
                self.cancelled = False

            async def process_once(self):
                self.calls += 1
                if self.calls == 1:
                    return 0
                second_pass.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    self.cancelled = True
                    raise

        worker = BlockingWorker()
        runtime = service.FeishuChannelRuntime(
            listener=listener,
            store=store,
            sender_interval_seconds=0.001,
            local_notification_factory=lambda *_args, **_kwargs: worker,
        )
        task = asyncio.create_task(runtime.run())
        await asyncio.wait_for(second_pass.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return listener, runtime, worker

    listener, runtime, worker = asyncio.run(scenario())

    assert worker.cancelled is True
    assert listener.stopped is True
    assert runtime._loop is None


def test_media_runtime_uses_listener_client_recovers_and_attaches(tmp_path):
    listener = _MediaRuntimeListener()
    store = _MediaRuntimeStore(tmp_path / "runtime.sqlite3")
    factory_calls = []

    class Resolver:
        app_id = "cli_test"

        async def resolve_pending(self, *, limit):
            assert limit == 8
            listener.stopped = True
            return [
                SimpleNamespace(
                    event_ready_for_enqueue=True,
                    asset=SimpleNamespace(
                        event_record_id=42,
                        app_id="cli_test",
                        message_id="om_media",
                    ),
                )
            ]

    def media_factory(st, client, **kwargs):
        factory_calls.append((st, client, kwargs))
        return Resolver()

    runtime = service.FeishuChannelRuntime(
        listener=listener,
        store=store,
        media_enabled=True,
        media_workspace=tmp_path,
        media_factory=media_factory,
        sender_interval_seconds=0.001,
    )

    asyncio.run(runtime.run())

    assert factory_calls[0][0] is store
    assert factory_calls[0][1] is listener.client
    assert factory_calls[0][2]["workspace"] == tmp_path.resolve()
    assert store.recoveries == [
        {"app_id": "cli_test", "stale_after_seconds": 300}
    ]
    assert store.attached == [42]


def test_media_download_failure_degrades_only_media_loop(tmp_path):
    listener = _MediaRuntimeListener()
    store = _MediaRuntimeStore(tmp_path / "runtime.sqlite3")

    class FlakyResolver:
        app_id = "cli_test"

        def __init__(self):
            self.calls = 0

        async def resolve_pending(self, *, limit):
            del limit
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("file_key=must-not-be-recorded")
            listener.stopped = True
            return []

    resolver = FlakyResolver()
    runtime = service.FeishuChannelRuntime(
        listener=listener,
        store=store,
        media_enabled=True,
        media_workspace=tmp_path,
        media_factory=lambda *_args, **_kwargs: resolver,
        sender_interval_seconds=0.001,
    )

    asyncio.run(runtime.run())

    assert resolver.calls == 2
    assert ("feishu_media_resolve_failed", "RuntimeError") in listener.errors
    assert "must-not-be-recorded" not in repr(listener.errors)


def test_media_runtime_rejects_limit_above_normalized_contract(tmp_path):
    runtime = service.FeishuChannelRuntime(
        listener=_MediaRuntimeListener(),
        store=_MediaRuntimeStore(tmp_path / "runtime.sqlite3"),
        media_enabled=True,
        media_workspace=tmp_path,
        media_max_assets=9,
    )
    with pytest.raises(ValueError, match="between 1 and 8"):
        asyncio.run(runtime.run())


def test_action_runtime_reuses_listener_client_without_second_connection(
    tmp_path, monkeypatch
):
    listener = _MediaRuntimeListener()
    store = _MediaRuntimeStore(tmp_path / "runtime.sqlite3")
    recoveries = []
    factory_calls = []

    monkeypatch.setattr(
        service,
        "recover_orphaned_message_actions",
        lambda st, **kwargs: recoveries.append((st, kwargs)) or 0,
    )

    class ActionSender:
        async def process_once(self, limit=10):
            assert limit == 1
            listener.stopped = True
            return 0

    def action_sender_factory(st, client):
        factory_calls.append((st, client))
        return ActionSender()

    runtime = service.FeishuChannelRuntime(
        listener=listener,
        store=store,
        reaction_enabled=True,
        action_sender_factory=action_sender_factory,
        sender_interval_seconds=0.001,
    )

    asyncio.run(runtime.run())

    assert factory_calls == [(store, listener.client)]
    assert recoveries == [(store, {"app_id": "cli_test"})]
    assert listener.started is True


def test_action_sender_builder_preserves_independent_default_off_gates(
    monkeypatch,
):
    monkeypatch.setenv("CEO_FEISHU_ENABLED", "1")
    monkeypatch.setenv("CEO_FEISHU_SENDER_ENABLED", "1")
    monkeypatch.setenv("CEO_NOT_SEND_MESSAGE", "0")
    monkeypatch.setenv("CEO_FEISHU_REACTION_ENABLED", "1")
    monkeypatch.setenv("CEO_FEISHU_RECALL_ENABLED", "0")
    monkeypatch.setenv("CEO_FEISHU_HANDOFF_ENABLED", "1")
    monkeypatch.setenv(
        "CEO_FEISHU_HANDOFF_OPEN_IDS", "ou_owner,ou_backup,ou_owner"
    )

    sender = service.build_action_sender(
        SimpleNamespace(), SimpleNamespace(app_id="cli_test")
    )

    assert sender.outbound_gate_open is True
    assert sender.enabled_kinds == ("add_reaction", "handoff_notify")
    assert sender.handoff_target_allowlist == frozenset(
        {"ou_owner", "ou_backup"}
    )


def test_runtime_reply_and_action_factories_share_durable_bidirectional_budget(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("CEO_FEISHU_MAX_SENDS_PER_MINUTE", "1")
    monkeypatch.setenv("CEO_FEISHU_MEDIA_ENABLED", "0")
    monkeypatch.setenv("CEO_FEISHU_APP_ID", "cli_test")
    monkeypatch.setattr(
        service,
        "build_listener",
        lambda *_args, **_kwargs: SimpleNamespace(),
    )
    client = SimpleNamespace(app_id="cli_test")
    store = AutoReplyStore(tmp_path / "runtime-budget.sqlite3")

    reply_first_runtime = service.build_runtime(store)
    reply_sender = reply_first_runtime.sender_factory(
        store, client
    )
    action_sender = reply_first_runtime.action_sender_factory(
        store, client
    )

    assert reply_sender.mutation_budget is action_sender.mutation_budget
    assert reply_sender._rate_slot() is True
    assert action_sender._rate_slot() is False

    action_first_runtime = service.build_runtime(store)
    reply_sender = action_first_runtime.sender_factory(
        store, client
    )
    action_sender = action_first_runtime.action_sender_factory(
        store, client
    )

    assert action_sender._rate_slot() is False
    assert reply_sender._rate_slot() is False


def test_durable_mutation_budget_expires_and_is_atomic_across_instances(tmp_path):
    store = AutoReplyStore(tmp_path / "durable-budget.sqlite3")
    current = [100.0]

    def budget(app_id="cli_test", limit=2):
        return service._build_mutation_budget(
            store,
            app_id=app_id,
            max_mutations_per_minute=limit,
            wall_clock=lambda: current[0],
        )

    first = budget()
    assert first.try_acquire() is True
    assert first.try_acquire() is True
    assert budget().try_acquire() is False
    assert budget(app_id="cli_other").try_acquire() is True

    current[0] += 60
    assert budget().try_acquire() is True

    current[0] += 60
    attempts = [budget(limit=5) for _ in range(20)]
    with ThreadPoolExecutor(max_workers=20) as pool:
        acquired = list(pool.map(lambda item: item.try_acquire(), attempts))
    assert sum(acquired) == 5


@pytest.mark.parametrize(
    ("quota", "expected_kinds"),
    [
        (1, ["reply", "action", "reply", "action"]),
        (
            3,
            [
                "reply",
                "action",
                "reply",
                "action",
                "reply",
                "action",
                "reply",
                "action",
                "reply",
                "action",
                "reply",
                "action",
            ],
        ),
    ],
)
def test_outbound_drain_is_fair_across_durable_quota_windows(
    tmp_path, quota, expected_kinds
):
    store = AutoReplyStore(tmp_path / f"fair-budget-{quota}.sqlite3")
    current = [0.0]
    budget = service._build_mutation_budget(
        store,
        app_id="cli_test",
        max_mutations_per_minute=quota,
        wall_clock=lambda: current[0],
        monotonic_clock=lambda: current[0],
    )
    mutations = []

    class BackloggedWorker:
        def __init__(self, kind):
            self.kind = kind
            self.calls = 0

        async def process_once(self, limit=10):
            assert limit == 1
            self.calls += 1
            if budget.try_acquire():
                mutations.append((self.kind, current[0]))
            # The real senders count a claimed row even when the shared budget
            # moves it to rate-limited retry.
            return 1

    reply = BackloggedWorker("reply")
    action = BackloggedWorker("action")
    runtime = service.FeishuChannelRuntime(
        listener=SimpleNamespace(),
        store=store,
    )
    runtime._outbound_mutation_budget = budget

    async def drain_windows():
        for _ in range(4):
            await runtime._drain_outbound_once(reply, action)
            current[0] += 60

    asyncio.run(drain_windows())

    assert [kind for kind, _ in mutations] == expected_kinds
    assert {kind for kind, _ in mutations} == {"reply", "action"}


def test_outbound_drain_keeps_other_class_moving_after_peer_failure(tmp_path):
    store = AutoReplyStore(tmp_path / "fair-budget-failure.sqlite3")
    current = [0.0]
    budget = service._build_mutation_budget(
        store,
        app_id="cli_test",
        max_mutations_per_minute=3,
        wall_clock=lambda: current[0],
        monotonic_clock=lambda: current[0],
    )
    listener = _MediaRuntimeListener()
    mutations = []

    class FailingReplyWorker:
        calls = 0

        async def process_once(self, limit=10):
            assert limit == 1
            self.calls += 1
            raise RuntimeError("reply detail must stay private")

    class BackloggedActionWorker:
        async def process_once(self, limit=10):
            assert limit == 1
            if budget.try_acquire():
                mutations.append("action")
            return 1

    reply = FailingReplyWorker()
    runtime = service.FeishuChannelRuntime(listener=listener, store=store)
    runtime._outbound_mutation_budget = budget

    asyncio.run(
        runtime._drain_outbound_once(reply, BackloggedActionWorker())
    )

    assert reply.calls == 1
    assert mutations == ["action", "action", "action"]
    assert ("feishu_sender_process_failed", "RuntimeError") in listener.errors
    assert "reply detail must stay private" not in repr(listener.errors)


def test_action_sender_failure_degrades_only_action_loop(tmp_path, monkeypatch):
    listener = _MediaRuntimeListener()
    store = _MediaRuntimeStore(tmp_path / "runtime.sqlite3")
    monkeypatch.setattr(
        service, "recover_orphaned_message_actions", lambda *_args, **_kwargs: 0
    )

    class FlakyActionSender:
        def __init__(self):
            self.calls = 0

        async def process_once(self, limit=10):
            assert limit == 1
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("remote or secret detail")
            listener.stopped = True
            return 0

    sender = FlakyActionSender()
    runtime = service.FeishuChannelRuntime(
        listener=listener,
        store=store,
        handoff_enabled=True,
        action_sender_factory=lambda *_args: sender,
        sender_interval_seconds=0.001,
    )

    asyncio.run(runtime.run())

    assert sender.calls == 2
    assert ("feishu_action_process_failed", "RuntimeError") in listener.errors
    assert "remote or secret detail" not in repr(listener.errors)
