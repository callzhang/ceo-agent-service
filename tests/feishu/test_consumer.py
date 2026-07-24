import sqlite3

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
    store.record_feishu_event(
        trigger,
        eligibility_status="eligible",
        store_body=True,
    )


def _seed_second(store):
    store.record_feishu_event(
        _trigger().model_copy(
            update={
                "event_id": "evt_2",
                "message_id": "om_2",
                "chat_id": "oc_2",
                "thread_id": "",
            }
        ),
        eligibility_status="eligible",
        store_body=True,
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


def test_consumer_claims_and_finishes_one_task_at_a_time(store):
    _seed(store)
    _seed_second(store)
    tasks = store.list_reply_tasks(channel="feishu")
    first_id, second_id = sorted(task.id for task in tasks)
    consumer = FeishuReplyConsumer(store, FakeRunner())

    def finish(task):
        current = {row.id: row for row in store.list_reply_tasks(channel="feishu")}
        if task.id == first_id:
            assert current[second_id].status == "pending"
        assert store.complete_processing_reply_task(
            task.id, channel="feishu"
        )

    consumer.process = finish

    assert consumer.run_once(limit=2) == 2
    assert all(
        task.status == "done"
        for task in store.list_reply_tasks(channel="feishu")
    )


def test_standalone_consumer_reclaims_only_stale_feishu_task(store):
    _seed(store)
    [claimed] = store.claim_reply_tasks(1, channel="feishu")
    with sqlite3.connect(store.path) as db:
        db.execute(
            "update reply_tasks set locked_at=datetime('now', '-31 minutes') where id=?",
            (claimed.id,),
        )
    runner = FakeRunner(CodexDecision(action=CodexAction.NO_REPLY))

    assert FeishuReplyConsumer(store, runner).run_once(limit=1) == 1

    [task] = store.list_reply_tasks(channel="feishu")
    assert task.status == "done"
    assert task.attempts == 2
    assert len(runner.prompts) == 1


def test_consumer_does_not_reclaim_during_json_repair_window(store):
    _seed(store)
    [claimed] = store.claim_reply_tasks(1, channel="feishu")
    with sqlite3.connect(store.path) as db:
        db.execute(
            "update reply_tasks set locked_at=datetime('now', '-31 minutes') where id=?",
            (claimed.id,),
        )
    runner = FakeRunner(CodexDecision(action=CodexAction.NO_REPLY))
    runner.timeout_seconds = 1200

    assert FeishuReplyConsumer(store, runner).run_once(limit=1) == 0

    [task] = store.list_reply_tasks(channel="feishu")
    assert task.status == "processing"
    assert task.attempts == 1
    assert runner.prompts == []


def test_unexpected_task_failure_does_not_stop_later_task(store):
    _seed(store)
    _seed_second(store)
    first_id, second_id = sorted(
        task.id for task in store.list_reply_tasks(channel="feishu")
    )
    consumer = FeishuReplyConsumer(store, FakeRunner())

    def fail_first(task):
        if task.id == first_id:
            raise RuntimeError("raw secret must not be persisted")
        assert store.complete_processing_reply_task(
            task.id, channel="feishu"
        )

    consumer.process = fail_first

    assert consumer.run_once(limit=2) == 2
    tasks = {row.id: row for row in store.list_reply_tasks(channel="feishu")}
    assert tasks[first_id].status == "failed"
    assert tasks[first_id].error == "feishu_consumer_failed:RuntimeError"
    assert tasks[second_id].status == "done"


def test_atomic_finalize_rolls_back_attempt_if_delivery_insert_fails(store):
    _seed(store)
    with store._connect() as db:
        db.execute(
            """
            create trigger reject_test_delivery
            before insert on feishu_deliveries
            begin
                select raise(abort, 'injected delivery failure');
            end
            """
        )
    runner = FakeRunner(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="reply")
    )

    assert FeishuReplyConsumer(store, runner).run_once(limit=1) == 1

    [task] = store.list_reply_tasks(channel="feishu")
    assert task.status == "failed"
    assert task.error == "feishu_consumer_failed:IntegrityError"
    assert store.list_reply_attempts() == []
    assert store.list_feishu_deliveries() == []
    assert len(runner.prompts) == 1


def test_idempotency_key_is_stable_for_same_identity(store):
    from app.feishu.delivery import delivery_idempotency_key

    first = delivery_idempotency_key(
        app_id="cli_test", reply_task_id=1, trigger_message_id="om_1"
    )
    second = delivery_idempotency_key(
        app_id="cli_test", reply_task_id=1, trigger_message_id="om_1"
    )
    assert first == second
