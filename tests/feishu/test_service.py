import asyncio
import threading
from types import SimpleNamespace

import pytest

from app.feishu.delivery import FeishuDeliverySender, delivery_idempotency_key
from app.feishu.listener import FeishuListenerHealth
from app.feishu.models import FeishuInboundMessage
from app.feishu import service
from app.feishu.service import component_names
from app.store import AutoReplyStore
from tests.feishu.fakes import FakeDeliveryClient


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
            store, delivery.id, timeout=2
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
        service.approve_delivery_on_runtime(store, delivery.id, timeout=1)
