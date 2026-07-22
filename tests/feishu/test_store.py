import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.feishu.models import FeishuInboundMessage, FeishuReplyScope
from app.store import AutoReplyStore


def _store(tmp_path):
    return AutoReplyStore(tmp_path / "worker.sqlite3")


def _message(
    number: int = 1,
    *,
    event_id: str = "",
    message_id: str = "",
    chat_id: str = "oc_1",
    chat_type: str = "group",
    body_text: str = "hello",
    created_at: str = "",
    app_id: str = "cli_a",
):
    return FeishuInboundMessage(
        event_id=event_id or f"evt-{number}",
        app_id=app_id,
        message_id=message_id or f"om_{number}",
        chat_id=chat_id,
        chat_type=chat_type,
        chat_title="Test chat",
        thread_id="omt_root" if chat_type == "topic" else "",
        sender_open_id="ou_1",
        sender_name="Alex",
        message_type="text",
        mentioned_bot=chat_type != "p2p",
        body_text=body_text,
        event_create_time=(
            created_at or f"2026-07-22T10:00:{number:02d}+08:00"
        ),
        received_at=f"2026-07-22T10:01:{number:02d}+08:00",
    )


def _eligible(store, number: int = 1, **message_fields):
    return store.record_feishu_event(
        _message(number, **message_fields),
        eligibility_status="eligible",
        store_body=True,
    )


def _attempt(store, event):
    task = next(
        row
        for row in store.list_reply_tasks(channel="feishu")
        if row.id == event.reply_task_id
    )
    return store.record_reply_attempt(
        conversation_id=task.conversation_id,
        conversation_title=task.conversation_title,
        trigger_message_id=event.message_id,
        trigger_sender=event.sender_name or event.sender_open_id,
        trigger_text=event.body_text,
        action="send_reply",
        sensitivity_kind="general",
        draft_reply_text="reply",
        send_status="pending",
        channel="feishu",
    )


def test_schema_creates_all_feishu_tables_and_indexes(tmp_path):
    store = _store(tmp_path)
    with store._connect() as db:
        tables = {
            row["name"]
            for row in db.execute(
                "select name from sqlite_master where type='table'"
            ).fetchall()
        }
        delivery_columns = {
            row["name"]
            for row in db.execute("pragma table_info(feishu_deliveries)")
        }
        violations = db.execute("pragma foreign_key_check").fetchall()

    assert {
        "feishu_events",
        "feishu_reply_scopes",
        "feishu_deliveries",
    } <= tables
    assert {
        "idempotency_key",
        "available_at",
        "approved_at",
        "approved_by",
        "locked_at",
    } <= delivery_columns
    assert violations == []


def test_eligible_event_and_reply_task_are_recorded_idempotently(tmp_path):
    store = _store(tmp_path)

    first = _eligible(store)
    duplicate = _eligible(store)

    assert first.inserted is True
    assert first.reply_task_id > 0
    assert duplicate.inserted is False
    assert duplicate.id == first.id
    assert duplicate.reply_task_id == first.reply_task_id
    tasks = store.list_reply_tasks(channel="feishu")
    assert len(tasks) == 1
    assert tasks[0].conversation_id.startswith("feishu:")
    assert tasks[0].conversation_id.endswith(":oc_1")
    assert tasks[0].trigger_message_id == "om_1"
    assert tasks[0].trigger_text == "hello"
    assert store.count_reply_tasks(channel="dingtalk") == 0


def test_message_key_is_second_idempotency_boundary(tmp_path):
    store = _store(tmp_path)
    first = _eligible(store, event_id="evt-original", message_id="om_same")

    duplicate = _eligible(store, event_id="evt-redelivery", message_id="om_same")

    assert duplicate.id == first.id
    assert duplicate.event_id == "evt-original"
    assert store.count_reply_tasks(channel="feishu") == 1


def test_event_id_cannot_be_reused_for_different_message(tmp_path):
    store = _store(tmp_path)
    _eligible(store, event_id="evt-same", message_id="om_1")

    with pytest.raises(ValueError, match="event_id reused"):
        _eligible(store, 2, event_id="evt-same", message_id="om_2")


def test_unapproved_event_does_not_store_body_or_enqueue(tmp_path):
    store = _store(tmp_path)
    sensitive = "confidential personnel note"

    event = store.record_feishu_event(
        _message(body_text=sensitive),
        eligibility_status="unapproved_scope",
        reject_reason="scope_pending",
        store_body=False,
    )

    assert event.body_text == ""
    assert event.reply_task_id == 0
    assert store.count_reply_tasks(channel="feishu") == 0
    with store._connect() as db:
        persisted = db.execute(
            "select body_text, reject_reason from feishu_events"
        ).fetchone()
    assert persisted["body_text"] == ""
    assert persisted["reject_reason"] == "scope_pending"


def test_receive_only_event_can_be_atomically_attached_later(tmp_path):
    store = _store(tmp_path)
    event = store.record_feishu_event(
        _message(),
        eligibility_status="eligible",
        store_body=True,
        enqueue_eligible=False,
    )
    assert event.reply_task_id == 0

    attached = store.attach_feishu_event_reply_task(event.id)
    attached_again = store.attach_feishu_event_reply_task(event.id)

    assert attached.reply_task_id > 0
    assert attached_again.reply_task_id == attached.reply_task_id
    assert store.count_reply_tasks(channel="feishu") == 1


def test_list_events_finds_only_eligible_receive_only_work(tmp_path):
    store = _store(tmp_path)
    receive_only = store.record_feishu_event(
        _message(1),
        eligibility_status="eligible",
        store_body=True,
        enqueue_eligible=False,
    )
    _eligible(store, 2)
    store.record_feishu_event(
        _message(3, body_text="not approved"),
        eligibility_status="unapproved_scope",
        reject_reason="scope_pending",
        store_body=False,
    )

    pending = store.list_feishu_events(
        "cli_a",
        eligibility_status="eligible",
        unqueued_only=True,
        limit=10,
    )

    assert [event.id for event in pending] == [receive_only.id]
    assert pending[0].body_text == "hello"


def test_context_is_only_eligible_stored_text_and_is_oldest_first(tmp_path):
    store = _store(tmp_path)
    for number in (1, 2, 3):
        _eligible(store, number, body_text=f"text-{number}")
    store.record_feishu_event(
        _message(4, body_text="must-not-appear"),
        eligibility_status="stale",
        reject_reason="too_old",
        store_body=False,
    )

    context = store.list_feishu_context("oc_1", limit=2, app_id="cli_a")

    assert [event.message_id for event in context] == ["om_2", "om_3"]
    assert [event.body_text for event in context] == ["text-2", "text-3"]


def test_scope_discovery_starts_pending_and_rediscovery_preserves_review(tmp_path):
    store = _store(tmp_path)
    discovered = FeishuReplyScope(
        app_id="cli_a",
        target_type="group",
        target_id="oc_1",
        display_name="Initial name",
        trigger_mode="mention_bot",
    )

    pending = store.upsert_feishu_reply_scope(discovered)
    verified = store.review_feishu_reply_scope(
        "cli_a",
        "group",
        "oc_1",
        approved=True,
        approved_by="local-owner",
        now="2026-07-22T11:00:00+08:00",
    )
    rediscovered = store.upsert_feishu_reply_scope(
        discovered.model_copy(
            update={
                "display_name": "Renamed",
                "last_seen_at": "2026-07-22T11:01:00+08:00",
            }
        )
    )

    assert pending.binding_status == "pending"
    assert pending.enabled is False
    assert verified.binding_status == "verified"
    assert verified.enabled is True
    assert rediscovered.binding_status == "verified"
    assert rediscovered.enabled is True
    assert rediscovered.display_name == "Renamed"
    assert store.list_feishu_reply_scopes(
        "cli_a", enabled_only=True
    ) == [rediscovered]


def test_scope_rejection_fails_closed(tmp_path):
    store = _store(tmp_path)
    store.upsert_feishu_reply_scope(
        FeishuReplyScope(
            app_id="cli_a",
            target_type="direct_sender",
            target_id="ou_1",
            trigger_mode="every_inbound_text",
        )
    )

    disabled = store.review_feishu_reply_scope(
        "cli_a",
        "direct_sender",
        "ou_1",
        approved=False,
        approved_by="local-owner",
    )

    assert disabled.binding_status == "disabled"
    assert disabled.enabled is False
    assert store.list_feishu_reply_scopes("cli_a", enabled_only=True) == []


def test_delivery_create_claim_unknown_retry_and_sent_keep_one_uuid(tmp_path):
    store = _store(tmp_path)
    event = _eligible(store)
    attempt_id = _attempt(store, event)
    delivery = store.create_feishu_delivery(
        reply_task_id=event.reply_task_id,
        attempt_id=attempt_id,
        app_id="cli_a",
        chat_id="oc_1",
        reply_to_message_id="om_1",
        reply_in_thread=False,
        reply_text="first reply",
    )
    duplicate = store.create_feishu_delivery(
        reply_task_id=event.reply_task_id,
        attempt_id=attempt_id,
        app_id="cli_a",
        chat_id="oc_1",
        reply_to_message_id="om_1",
        reply_in_thread=False,
        reply_text="a later draft must not replace it",
        idempotency_key="different-key",
    )

    claimed = store.claim_feishu_delivery(
        delivery.id, now="2026-07-22T12:00:00+08:00"
    )
    assert claimed.status == "sending"
    assert claimed.attempts == 1
    assert store.claim_feishu_delivery(delivery.id) is None
    unknown = store.mark_feishu_delivery_send_unknown(
        delivery.id,
        error_code="send_timeout",
        error="result not confirmed",
        request_log_id="log-1",
    )
    verified_not_sent = store.reconcile_feishu_delivery_unknown(
        delivery.id,
        app_id="cli_a",
        outcome="not_sent",
        verified_by="operator",
        evidence_kind="message_lookup",
    )
    retry = store.requeue_feishu_delivery_after_verification(
        delivery.id,
        app_id="cli_a",
        verified_by="operator",
        evidence_kind="admin_audit",
        available_at="2026-07-22T12:05:00+08:00",
    )
    assert store.claim_feishu_delivery(
        delivery.id, now="2026-07-22T12:04:59+08:00"
    ) is None
    claimed_again = store.claim_feishu_delivery(
        delivery.id, now="2026-07-22T12:05:00+08:00"
    )
    sent = store.mark_feishu_delivery_sent(
        delivery.id,
        feishu_message_id="om_reply",
        request_log_id="log-2",
    )

    assert duplicate.id == delivery.id
    assert duplicate.reply_text == "first reply"
    assert duplicate.idempotency_key == delivery.idempotency_key
    assert unknown.idempotency_key == delivery.idempotency_key
    assert verified_not_sent.status == "failed"
    assert retry.idempotency_key == delivery.idempotency_key
    assert claimed_again.attempts == 2
    assert sent.status == "sent"
    assert sent.feishu_message_id == "om_reply"
    assert sent.idempotency_key == delivery.idempotency_key


def test_delivery_claim_is_single_winner_and_reject_is_terminal(tmp_path):
    store = _store(tmp_path)
    first = _eligible(store, 1)
    second = _eligible(store, 2)
    deliveries = [
        store.create_feishu_delivery(
            reply_task_id=event.reply_task_id,
            attempt_id=_attempt(store, event),
            app_id="cli_a",
            chat_id="oc_1",
            reply_to_message_id=event.message_id,
            reply_in_thread=False,
            reply_text=f"reply-{event.message_id}",
        )
        for event in (first, second)
    ]

    [claimed] = store.claim_feishu_deliveries(
        1, now="2026-07-22T12:00:00+08:00"
    )
    rejected = store.reject_feishu_delivery(
        deliveries[1].id, app_id="cli_a"
    )

    assert claimed.id == deliveries[0].id
    assert store.claim_feishu_delivery(claimed.id) is None
    assert rejected.status == "rejected"
    assert store.list_feishu_deliveries("ready_to_send") == []


def test_delivery_approval_is_audited_app_bound_and_claim_filterable(tmp_path):
    store = _store(tmp_path)
    event = _eligible(store)
    delivery = store.create_feishu_delivery(
        reply_task_id=event.reply_task_id,
        attempt_id=_attempt(store, event),
        app_id="cli_a",
        chat_id="oc_1",
        reply_to_message_id="om_1",
        reply_in_thread=False,
        reply_text="approved reply",
    )

    assert store.claim_feishu_deliveries(
        10, app_id="cli_a", approved_only=True
    ) == []
    with pytest.raises(PermissionError, match="App ID"):
        store.approve_feishu_delivery(
            delivery.id,
            app_id="cli_b",
            approved_by="operator",
        )

    approved = store.approve_feishu_delivery(
        delivery.id,
        app_id="cli_a",
        approved_by="operator",
        now="2026-07-22T12:00:00+08:00",
    )
    [claimed] = store.claim_feishu_deliveries(
        10, app_id="cli_a", approved_only=True
    )

    assert approved.approved_at == "2026-07-22T12:00:00+08:00"
    assert approved.approved_by == "operator"
    assert claimed.id == delivery.id


def test_batch_claim_is_strictly_filtered_by_app_id(tmp_path):
    store = _store(tmp_path)
    first = _eligible(store, 1, chat_id="oc_a")
    second = _eligible(store, 2, chat_id="oc_b", app_id="cli_b")
    deliveries = [
        store.create_feishu_delivery(
            reply_task_id=event.reply_task_id,
            attempt_id=_attempt(store, event),
            app_id=app_id,
            chat_id=event.chat_id,
            reply_to_message_id=event.message_id,
            reply_in_thread=False,
            reply_text="reply",
        )
        for event, app_id in ((first, "cli_a"), (second, "cli_b"))
    ]

    claimed = store.claim_feishu_deliveries(10, app_id="cli_a")

    assert [row.id for row in claimed] == [deliveries[0].id]
    assert store.get_feishu_delivery(deliveries[1].id).status == "ready_to_send"


def test_batch_claims_at_most_one_delivery_per_chat(tmp_path):
    store = _store(tmp_path)
    first = _eligible(store, 1, chat_id="oc_same")
    second = _eligible(store, 2, chat_id="oc_same")
    other = _eligible(store, 3, chat_id="oc_other")
    deliveries = [
        store.create_feishu_delivery(
            reply_task_id=event.reply_task_id,
            attempt_id=_attempt(store, event),
            app_id="cli_a",
            chat_id=event.chat_id,
            reply_to_message_id=event.message_id,
            reply_in_thread=False,
            reply_text=f"reply-{event.message_id}",
        )
        for event in (first, second, other)
    ]

    claimed = store.claim_feishu_deliveries(
        10, now="2026-07-22T12:00:00+08:00"
    )

    assert [item.id for item in claimed] == [deliveries[0].id, deliveries[2].id]
    assert store.claim_feishu_delivery(deliveries[1].id) is None
    store.mark_feishu_delivery_failed(
        deliveries[0].id,
        error_code="target_revoked",
        error="first is done",
    )
    assert store.claim_feishu_delivery(deliveries[1].id) is not None


def test_later_delivery_cannot_overtake_delayed_retry_in_same_chat(tmp_path):
    store = _store(tmp_path)
    events = [
        _eligible(store, number, chat_id="oc_same") for number in (1, 2)
    ]
    deliveries = [
        store.create_feishu_delivery(
            reply_task_id=event.reply_task_id,
            attempt_id=_attempt(store, event),
            app_id="cli_a",
            chat_id="oc_same",
            reply_to_message_id=event.message_id,
            reply_in_thread=False,
            reply_text=f"reply-{event.message_id}",
        )
        for event in events
    ]
    store.claim_feishu_delivery(
        deliveries[0].id, now="2026-07-22T12:00:00+08:00"
    )
    store.mark_feishu_delivery_retry(
        deliveries[0].id,
        error_code="rate_limited",
        error="wait",
        available_at="2026-07-22T12:05:00+08:00",
    )

    assert store.claim_feishu_delivery(
        deliveries[1].id, now="2026-07-22T12:01:00+08:00"
    ) is None
    assert store.claim_feishu_deliveries(
        10, now="2026-07-22T12:01:00+08:00"
    ) == []
    [claimed] = store.claim_feishu_deliveries(
        10, now="2026-07-22T12:05:00+08:00"
    )
    assert claimed.id == deliveries[0].id


def test_concurrent_specific_claims_cannot_send_same_chat_in_parallel(tmp_path):
    store = _store(tmp_path)
    events = [
        _eligible(store, number, chat_id="oc_same") for number in (1, 2)
    ]
    deliveries = [
        store.create_feishu_delivery(
            reply_task_id=event.reply_task_id,
            attempt_id=_attempt(store, event),
            app_id="cli_a",
            chat_id="oc_same",
            reply_to_message_id=event.message_id,
            reply_in_thread=False,
            reply_text=f"reply-{event.message_id}",
        )
        for event in events
    ]
    barrier = threading.Barrier(2)

    def claim(delivery_id):
        worker_store = AutoReplyStore(store.path)
        barrier.wait()
        return worker_store.claim_feishu_delivery(delivery_id)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(claim, [item.id for item in deliveries]))

    winners = [result for result in results if result is not None]
    assert len(winners) == 1
    assert winners[0].id == deliveries[0].id
    assert winners[0].status == "sending"


def test_delivery_cannot_be_created_for_dingtalk_task(tmp_path):
    store = _store(tmp_path)
    assert store.enqueue_reply_task(
        conversation_id="oc_1",
        conversation_title="Wrong channel",
        single_chat=False,
        trigger_message_id="om_1",
        trigger_create_time="2026-07-22T10:00:00+08:00",
        trigger_sender="Alex",
        trigger_text="hello",
        channel="dingtalk",
    )
    task = store.get_reply_task_for_message("oc_1", "om_1")

    with pytest.raises(ValueError, match="channel=feishu"):
        store.create_feishu_delivery(
            reply_task_id=task.id,
            attempt_id=1,
            app_id="cli_a",
            chat_id="oc_1",
            reply_to_message_id="om_1",
            reply_in_thread=False,
            reply_text="must not send",
        )


def test_sent_transition_requires_remote_message_id(tmp_path):
    store = _store(tmp_path)
    event = _eligible(store)
    delivery = store.create_feishu_delivery(
        reply_task_id=event.reply_task_id,
        attempt_id=_attempt(store, event),
        app_id="cli_a",
        chat_id="oc_1",
        reply_to_message_id="om_1",
        reply_in_thread=False,
        reply_text="reply",
    )
    store.claim_feishu_delivery(delivery.id)

    with pytest.raises(ValueError, match="requires feishu_message_id"):
        store.mark_feishu_delivery_sent(delivery.id, feishu_message_id="")
    assert store.get_feishu_delivery(delivery.id).status == "sending"


def test_uncertain_send_cannot_be_misclassified_for_retry_or_failure(tmp_path):
    store = _store(tmp_path)
    event = _eligible(store)
    delivery = store.create_feishu_delivery(
        reply_task_id=event.reply_task_id,
        attempt_id=_attempt(store, event),
        app_id="cli_a",
        chat_id="oc_1",
        reply_to_message_id=event.message_id,
        reply_in_thread=False,
        reply_text="reply",
    )
    store.claim_feishu_delivery(delivery.id)

    with pytest.raises(ValueError, match="confirmed retryable"):
        store.mark_feishu_delivery_retry(
            delivery.id,
            error_code="send_timeout",
            error="outcome unknown",
        )
    with pytest.raises(ValueError, match="must enter send_unknown"):
        store.mark_feishu_delivery_failed(
            delivery.id,
            error_code="send_timeout",
            error="outcome unknown",
        )

    unknown = store.mark_feishu_delivery_send_unknown(
        delivery.id,
        error_code="send_timeout",
        error="outcome unknown",
    )
    assert unknown.status == "send_unknown"


def test_unique_idempotency_key_cannot_cross_reply_tasks(tmp_path):
    store = _store(tmp_path)
    first = _eligible(store, 1)
    second = _eligible(store, 2)
    stable_key = "stable-key"
    store.create_feishu_delivery(
        reply_task_id=first.reply_task_id,
        attempt_id=_attempt(store, first),
        app_id="cli_a",
        chat_id="oc_1",
        reply_to_message_id="om_1",
        reply_in_thread=False,
        reply_text="one",
        idempotency_key=stable_key,
    )

    with pytest.raises(sqlite3.IntegrityError):
        store.create_feishu_delivery(
            reply_task_id=second.reply_task_id,
            attempt_id=_attempt(store, second),
            app_id="cli_a",
            chat_id="oc_1",
            reply_to_message_id="om_2",
            reply_in_thread=False,
            reply_text="two",
            idempotency_key=stable_key,
        )


def test_delivery_requires_a_durable_reply_attempt(tmp_path):
    store = _store(tmp_path)
    event = _eligible(store)

    with pytest.raises(ValueError, match="durable reply attempt"):
        store.create_feishu_delivery(
            reply_task_id=event.reply_task_id,
            app_id="cli_a",
            chat_id="oc_1",
            reply_to_message_id=event.message_id,
            reply_in_thread=False,
            reply_text="must not send",
        )
