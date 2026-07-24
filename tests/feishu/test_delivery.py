import asyncio
import sqlite3

import pytest

from app.feishu.client import FeishuSendResult
from app.feishu.delivery import (
    FeishuDeliverySender,
    delivery_idempotency_key,
    recover_orphaned_sending,
)
from app.feishu.models import FeishuInboundMessage
from app.store import AutoReplyStore
from tests.feishu.fakes import FakeDeliveryClient


def _seed(tmp_path):
    store = AutoReplyStore(tmp_path / "feishu.sqlite3")
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


def _sender(store, client, **kwargs):
    return FeishuDeliverySender(
        store,
        client,
        sender_enabled=True,
        live_send_allowed=True,
        **kwargs,
    )


def test_confirm_mode_does_not_claim_or_send(tmp_path):
    store, delivery = _seed(tmp_path)
    client = FakeDeliveryClient()
    sender = _sender(store, client, send_mode="confirm")
    assert asyncio.run(sender.process_once()) == 0
    assert client.deliveries == []
    assert store.get_feishu_delivery(delivery.id).status == "ready_to_send"


def test_confirm_mode_claims_only_durably_approved_delivery(tmp_path):
    store, delivery = _seed(tmp_path)
    store.approve_feishu_delivery(
        delivery.id,
        app_id="cli_test",
        approved_by="cli-operator",
    )
    client = FakeDeliveryClient()
    sender = _sender(store, client, send_mode="confirm")

    assert asyncio.run(sender.process_once()) == 1
    assert store.get_feishu_delivery(delivery.id).status == "sent"
    assert [row.id for row in client.deliveries] == [delivery.id]


def test_closed_global_gate_does_not_claim_even_in_auto(tmp_path):
    store, delivery = _seed(tmp_path)
    client = FakeDeliveryClient()
    sender = FeishuDeliverySender(
        store,
        client,
        sender_enabled=True,
        live_send_allowed=False,
        send_mode="auto",
    )
    assert asyncio.run(sender.process_once()) == 0
    assert store.get_feishu_delivery(delivery.id).status == "ready_to_send"


def test_explicit_approval_sends_and_records_message_id(tmp_path):
    store, delivery = _seed(tmp_path)
    client = FakeDeliveryClient(
        FeishuSendResult(True, message_id="om_reply", request_log_id="log-1")
    )
    outcome = asyncio.run(
        _sender(store, client, send_mode="confirm").approve_and_send(delivery.id)
    )
    saved = store.get_feishu_delivery(delivery.id)
    assert outcome.status == "sent"
    assert saved.status == "sent" and saved.feishu_message_id == "om_reply"
    assert saved.idempotency_key == delivery.idempotency_key
    assert saved.approved_at
    assert saved.approved_by == "local-audit-runtime"


def test_client_app_mismatch_fails_before_claim_or_send(tmp_path):
    store, delivery = _seed(tmp_path)
    client = FakeDeliveryClient(app_id="cli_other")
    sender = _sender(store, client, send_mode="confirm")

    with pytest.raises(PermissionError, match="App ID"):
        asyncio.run(sender.approve_and_send(delivery.id))

    saved = store.get_feishu_delivery(delivery.id)
    assert saved.status == "ready_to_send"
    assert saved.approved_at == ""
    assert client.deliveries == []


def test_legacy_zero_attempt_delivery_is_never_sent(tmp_path):
    store, delivery = _seed(tmp_path)
    with sqlite3.connect(store.path) as db:
        db.execute("pragma foreign_keys=off")
        db.execute(
            "update feishu_deliveries set attempt_id=0 where id=?",
            (delivery.id,),
        )
    client = FakeDeliveryClient()
    sender = _sender(store, client, send_mode="auto")

    assert asyncio.run(sender.process_once()) == 0

    assert client.deliveries == []
    saved = store.get_feishu_delivery(delivery.id)
    assert saved.status == "failed"
    assert saved.error_code == "legacy_identity_unverifiable"
    assert {
        row.event_type
        for row in store.list_feishu_audit_events(
            entity_type="delivery", entity_id=delivery.id
        )
    } >= {"created", "invalid_binding_quarantined"}


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("rate_limited", "retry"),
        ("not_connected", "retry"),
        ("permission_denied", "failed"),
        ("target_revoked", "failed"),
        ("format_error", "failed"),
        ("send_timeout", "send_unknown"),
        ("unknown", "send_unknown"),
    ],
)
def test_sdk_error_classification_is_fail_closed(tmp_path, code, expected):
    store, delivery = _seed(tmp_path)
    client = FakeDeliveryClient(FeishuSendResult(False, error_code=code))
    outcome = asyncio.run(
        _sender(store, client, send_mode="confirm").approve_and_send(delivery.id)
    )
    saved = store.get_feishu_delivery(delivery.id)
    assert outcome.status == expected
    assert saved.status == expected
    assert saved.idempotency_key == delivery.idempotency_key
    if expected == "retry":
        assert saved.available_at


def test_raised_timeout_is_send_unknown_not_blind_retry(tmp_path):
    store, delivery = _seed(tmp_path)
    client = FakeDeliveryClient(error=TimeoutError("may contain request details"))
    outcome = asyncio.run(
        _sender(store, client, send_mode="confirm").approve_and_send(delivery.id)
    )
    assert outcome.status == "send_unknown"
    assert "request details" not in store.get_feishu_delivery(delivery.id).error


def test_sender_enforces_bounded_network_timeout(tmp_path):
    store, delivery = _seed(tmp_path)

    class SlowClient(FakeDeliveryClient):
        async def send_reply(self, delivery):
            self.deliveries.append(delivery)
            await asyncio.sleep(1)
            return self.result

    client = SlowClient()
    sender = _sender(
        store,
        client,
        send_mode="confirm",
        send_timeout_seconds=0.01,
        send_lease_stale_seconds=1,
    )

    outcome = asyncio.run(sender.approve_and_send(delivery.id))

    assert outcome.status == "send_unknown"
    assert store.get_feishu_delivery(delivery.id).status == "send_unknown"
    assert len(client.deliveries) == 1


def test_unknown_result_becomes_send_unknown_on_first_attempt(tmp_path):
    store, delivery = _seed(tmp_path)
    client = FakeDeliveryClient(FeishuSendResult(False, error_code="unknown"))
    outcome = asyncio.run(
        _sender(
            store, client, send_mode="confirm", max_attempts=1
        ).approve_and_send(delivery.id)
    )
    assert outcome.status == "send_unknown"


@pytest.mark.parametrize("code", ["rate_limited", "not_connected"])
def test_confirmed_non_delivery_is_failed_after_retry_budget(tmp_path, code):
    store, delivery = _seed(tmp_path)
    client = FakeDeliveryClient(FeishuSendResult(False, error_code=code))

    outcome = asyncio.run(
        _sender(
            store, client, send_mode="confirm", max_attempts=1
        ).approve_and_send(delivery.id)
    )

    assert outcome.status == "failed"
    assert store.get_feishu_delivery(delivery.id).status == "failed"


def test_local_rate_limit_defers_with_same_uuid(tmp_path):
    store, delivery = _seed(tmp_path)
    sender = _sender(
        store,
        FakeDeliveryClient(),
        send_mode="confirm",
        max_sends_per_minute=1,
        monotonic_clock=lambda: 100.0,
    )
    sender._sent_times.append(100.0)
    outcome = asyncio.run(sender.approve_and_send(delivery.id))
    saved = store.get_feishu_delivery(delivery.id)
    assert outcome.status == "retry"
    assert saved.idempotency_key == delivery.idempotency_key


def test_reject_never_calls_client(tmp_path):
    store, delivery = _seed(tmp_path)
    client = FakeDeliveryClient()
    sender = _sender(store, client, send_mode="confirm")
    sender.reject(delivery.id)
    assert store.get_feishu_delivery(delivery.id).status == "rejected"
    assert client.deliveries == []


def test_only_stale_orphaned_sending_becomes_send_unknown(tmp_path):
    store, delivery = _seed(tmp_path)
    claimed = store.claim_feishu_delivery(delivery.id)
    assert recover_orphaned_sending(store) == 0
    with sqlite3.connect(store.path) as db:
        db.execute(
            "update feishu_deliveries set locked_at=datetime('now', '-6 minutes') where id=?",
            (delivery.id,),
        )
    assert recover_orphaned_sending(store) == 1
    assert store.get_feishu_delivery(delivery.id).status == "send_unknown"

    client = FakeDeliveryClient()
    sender = _sender(store, client, send_mode="auto")
    with pytest.raises(ValueError, match="lease"):
        asyncio.run(sender.send_claimed(claimed))
    assert client.deliveries == []


def test_second_approval_cannot_send_already_sent_delivery(tmp_path):
    store, delivery = _seed(tmp_path)
    sender = _sender(store, FakeDeliveryClient(), send_mode="confirm")
    asyncio.run(sender.approve_and_send(delivery.id))
    with pytest.raises(ValueError):
        asyncio.run(sender.approve_and_send(delivery.id))
