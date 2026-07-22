import json

import pytest

from app.dingtalk_models import (
    CalendarResponseStatus,
    CodexAction,
    CodexDecision,
)
from app.feishu.consumer import FeishuReplyConsumer
from app.feishu.models import FeishuInboundMessage
from app.store import AutoReplyStore
from tests.feishu.fakes import FakeRunner


@pytest.fixture
def store(tmp_path):
    return AutoReplyStore(tmp_path / "feishu.sqlite3")


def _trigger():
    return FeishuInboundMessage(
        event_id="evt_1",
        app_id="cli_test",
        message_id="om_1",
        chat_id="oc_1",
        chat_type="group",
        chat_title="Test Group",
        thread_id="omt_thread",
        sender_open_id="ou_1",
        sender_name="Alex",
        message_type="text",
        mentioned_bot=True,
        body_text="下午可以给结论吗？",
        event_create_time="2026-07-22T03:20:00+00:00",
        received_at="2026-07-22T03:20:01+00:00",
    )


def _seed(store):
    trigger = _trigger()
    store.enqueue_reply_task(
        channel="feishu",
        conversation_id=trigger.chat_id,
        conversation_title=trigger.chat_title,
        single_chat=False,
        trigger_message_id=trigger.message_id,
        trigger_create_time=trigger.event_create_time,
        trigger_sender=trigger.sender_name,
        trigger_text=trigger.body_text,
        trigger_message_json=trigger.model_dump_json(),
    )


def test_send_reply_creates_one_ready_delivery_and_never_sends(store):
    _seed(store)
    runner = FakeRunner(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            reply_text="可以，下午给你结论。",
            reason="明确回复",
        )
    )
    consumer = FeishuReplyConsumer(store, runner)
    assert not hasattr(consumer, "sender") and not hasattr(consumer, "client")
    assert consumer.run_once(limit=1) == 1
    delivery = store.get_feishu_delivery_for_task(1)
    assert delivery.status == "ready_to_send"
    assert delivery.reply_to_message_id == "om_1"
    assert delivery.reply_in_thread is True
    assert delivery.idempotency_key
    assert store.list_reply_tasks(channel="feishu")[0].status == "done"


def test_no_reply_completes_without_delivery(store):
    _seed(store)
    runner = FakeRunner(CodexDecision(action=CodexAction.NO_REPLY))
    assert FeishuReplyConsumer(store, runner).run_once(1) == 1
    assert store.get_feishu_delivery_for_task(1) is None


@pytest.mark.parametrize(
    "decision",
    [
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            reply_text="x",
            system_actions=[{"type": "send_dingtalk_reply"}],
        ),
        CodexDecision(
            action=CodexAction.SEND_REPLY, reply_text="x", ding_self=True
        ),
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            reply_text="x",
            calendar_response_status=CalendarResponseStatus.ACCEPTED,
        ),
    ],
)
def test_all_external_side_effects_are_rejected(store, decision):
    _seed(store)
    FeishuReplyConsumer(store, FakeRunner(decision)).run_once(1)
    assert store.get_feishu_delivery_for_task(1) is None
    assert store.list_reply_tasks(channel="feishu")[0].error == "external_system_actions_rejected"


def test_leak_failure_is_fail_closed(store):
    _seed(store)
    runner = FakeRunner(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="secret=abcd")
    )
    FeishuReplyConsumer(store, runner).run_once(1)
    assert store.get_feishu_delivery_for_task(1) is None
    assert store.list_reply_tasks(channel="feishu")[0].error == "reply_failed_leak_check"
    attempts = store.list_reply_attempts()
    assert attempts[0].draft_reply_text == "[redacted unsafe draft]"
    assert "secret=abcd" not in attempts[0].draft_reply_text


def test_runner_failure_does_not_create_attempt_or_delivery(store):
    _seed(store)
    FeishuReplyConsumer(
        store, FakeRunner(error=RuntimeError("contains raw payload"))
    ).run_once(1)
    task = store.list_reply_tasks(channel="feishu")[0]
    assert task.error == "feishu_decision_failed:RuntimeError"
    assert store.get_feishu_delivery_for_task(1) is None


def test_consumer_rejects_runner_without_hard_tool_isolation(store):
    class UnsafeRunner:
        def decide(self, prompt, session_id):
            raise AssertionError("must not be reached")

    with pytest.raises(ValueError, match="tool isolation"):
        FeishuReplyConsumer(store, UnsafeRunner())


def test_idempotency_key_is_stable_for_same_identity(store):
    from app.feishu.delivery import delivery_idempotency_key

    first = delivery_idempotency_key(
        app_id="cli_test", reply_task_id=1, trigger_message_id="om_1"
    )
    second = delivery_idempotency_key(
        app_id="cli_test", reply_task_id=1, trigger_message_id="om_1"
    )
    assert first == second
