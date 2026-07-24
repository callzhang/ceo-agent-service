import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.feishu.actions import build_message_action
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
    thread_id: str | None = None,
    root_message_id: str = "",
    parent_message_id: str = "",
    normalized_summary: str = "",
):
    return FeishuInboundMessage(
        event_id=event_id or f"evt-{number}",
        app_id=app_id,
        message_id=message_id or f"om_{number}",
        chat_id=chat_id,
        chat_type=chat_type,
        chat_title="Test chat",
        thread_id=(
            thread_id
            if thread_id is not None
            else ("omt_root" if chat_type == "topic" else "")
        ),
        root_message_id=root_message_id,
        parent_message_id=parent_message_id,
        sender_open_id="ou_1",
        sender_name="Alex",
        message_type="text",
        mentioned_bot=chat_type != "p2p",
        body_text=body_text,
        normalized_summary=normalized_summary,
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


def _action_context(tmp_path):
    store = _store(tmp_path)
    event = _eligible(
        store,
        event_id="evt-action-store",
        message_id="om_action_trigger",
        chat_id="oc_actions",
    )
    attempt_id = _attempt(store, event)
    delivery = store.create_feishu_delivery(
        reply_task_id=event.reply_task_id,
        attempt_id=attempt_id,
        app_id="cli_a",
        chat_id="oc_actions",
        reply_to_message_id=event.message_id,
        reply_in_thread=False,
        reply_text="bot-owned action target",
    )
    claimed = store.claim_feishu_delivery(delivery.id, app_id="cli_a")
    store.transition_feishu_delivery(
        delivery.id,
        from_statuses=("sending",),
        to_status="sent",
        app_id="cli_a",
        expected_lease_token=claimed.lease_token,
        feishu_message_id="om_action_bot",
        message_ids=("om_action_bot",),
    )
    task = next(
        row
        for row in store.list_reply_tasks(channel="feishu")
        if row.id == event.reply_task_id
    )
    [receipt] = store.list_feishu_delivery_receipts(delivery_id=delivery.id)
    return store, event, task, attempt_id, receipt


def _create_store_action(store, event, task, attempt_id, receipt, *, kind):
    fields = {
        "target_message_id": "",
        "target_open_id": "",
        "payload": {},
    }
    if kind == "add_reaction":
        fields.update(
            target_message_id=event.message_id,
            payload={"emoji_type": "OK"},
        )
    elif kind == "recall_message":
        fields["target_message_id"] = receipt.message_id
    elif kind == "handoff_notify":
        fields.update(
            target_open_id="ou_action_owner",
            payload={"text": "A reviewed handoff"},
        )
    else:  # pragma: no cover - helper misuse
        raise AssertionError(kind)
    action = build_message_action(
        reply_task_id=task.id,
        attempt_id=attempt_id,
        app_id="cli_a",
        chat_id="oc_actions",
        action_key=f"store-test:{kind}",
        kind=kind,
        **fields,
    )
    return store.create_feishu_message_action(
        action,
        handoff_target_allowlist=("ou_action_owner",),
    )


def _unknown_store_action(
    tmp_path, *, kind="add_reaction", request_log_id="log-original"
):
    store, event, task, attempt_id, receipt = _action_context(tmp_path)
    action = _create_store_action(
        store, event, task, attempt_id, receipt, kind=kind
    )
    store.approve_feishu_message_action(
        action.id,
        app_id="cli_a",
        approved_by="initial-reviewer",
        expected_approval_hash=action.approval_hash,
    )
    claimed = store.claim_feishu_message_action(
        action.id,
        app_id="cli_a",
        kinds=(kind,),
        send_mode="confirm",
    )
    unknown = store.transition_feishu_message_action(
        action.id,
        from_statuses=("sending",),
        to_status="result_unknown",
        app_id="cli_a",
        expected_lease_token=claimed.lease_token,
        request_log_id=request_log_id,
        error_code="send_timeout",
        error="bounded timeout",
    )
    current_receipt = store.get_feishu_delivery_receipt(
        app_id="cli_a", message_id=receipt.message_id
    )
    return store, unknown, current_receipt


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


def test_event_id_is_audit_only_and_may_repeat_across_messages_and_apps(tmp_path):
    store = _store(tmp_path)
    first = _eligible(store, event_id="evt-same", message_id="om_1")
    second = _eligible(store, 2, event_id="evt-same", message_id="om_2")
    third = _eligible(
        store,
        3,
        event_id="evt-same",
        message_id="om_1",
        app_id="cli_b",
    )

    assert len({first.id, second.id, third.id}) == 3
    assert store.get_feishu_event_for_message("cli_a", "om_1").id == first.id
    assert store.get_feishu_event_for_message("cli_a", "om_2").id == second.id
    assert store.get_feishu_event_for_message("cli_b", "om_1").id == third.id
    context = store.list_feishu_context(
        "oc_1",
        app_id="cli_a",
        thread_id="",
        before_message_id="om_2",
    )
    assert [item.message_id for item in context] == ["om_1"]
    with pytest.raises(ValueError, match="ambiguous"):
        store.get_feishu_event("evt-same")


def test_rich_event_fields_round_trip_into_record_and_trigger_json(tmp_path):
    store = _store(tmp_path)
    event = store.record_feishu_event(
        _message(
            message_id="om_child",
            thread_id="thread-a",
            root_message_id="om_root",
            parent_message_id="om_parent",
            normalized_summary="[图片]",
        ),
        eligibility_status="eligible",
        store_body=True,
        normalization_version=1,
    )

    assert event.root_message_id == "om_root"
    assert event.parent_message_id == "om_parent"
    assert event.normalized_summary == "[图片]"
    assert event.normalization_version == 1
    assert event.content_truncated is False
    assert event.resource_truncated is False
    task = next(
        item
        for item in store.list_reply_tasks(channel="feishu")
        if item.id == event.reply_task_id
    )
    trigger = json.loads(task.trigger_message_json)
    assert trigger["root_message_id"] == "om_root"
    assert trigger["parent_message_id"] == "om_parent"
    assert trigger["normalized_summary"] == "[图片]"
    assert trigger["normalization_version"] == 1


def test_truncation_evidence_is_persisted_without_enqueuing(tmp_path):
    store = _store(tmp_path)
    event = store.record_feishu_event(
        _message(normalized_summary="incomplete"),
        eligibility_status="rejected",
        reject_reason="normalization_truncated",
        store_body=True,
        enqueue_eligible=False,
        content_truncated=True,
        resource_truncated=True,
    )

    assert event.normalized_summary == "incomplete"
    assert event.content_truncated is True
    assert event.resource_truncated is True
    assert event.reply_task_id == 0


def test_root_context_isolation_uses_message_id_boundary(tmp_path):
    store = _store(tmp_path)
    _eligible(store, 1, message_id="om_root_a", body_text="root-a")
    _eligible(
        store,
        2,
        message_id="om_child_a",
        body_text="child-a",
        root_message_id="om_root_a",
    )
    _eligible(store, 3, message_id="om_root_b", body_text="root-b")
    _eligible(
        store,
        4,
        message_id="om_child_b",
        body_text="child-b",
        root_message_id="om_root_b",
    )
    trigger = _eligible(
        store,
        5,
        message_id="om_trigger_a",
        body_text="trigger-a",
        root_message_id="om_root_a",
    )

    context = store.list_feishu_context(
        "oc_1",
        app_id="cli_a",
        thread_id="",
        root_message_id="om_root_a",
        before_message_id=trigger.message_id,
    )

    assert [item.message_id for item in context] == [
        "om_root_a",
        "om_child_a",
    ]


def test_as_of_context_enforces_temporal_lower_bound_and_scope(tmp_path):
    store = _store(tmp_path)
    _eligible(
        store,
        1,
        message_id="om_root_a",
        body_text="just-inside",
        created_at="2026-07-22T11:59:00.001+00:00",
    )
    _eligible(
        store,
        2,
        message_id="om_old_child_a",
        body_text="just-outside",
        root_message_id="om_root_a",
        created_at="2026-07-22T11:58:59.999+00:00",
    )
    _eligible(
        store,
        3,
        message_id="om_other_root",
        body_text="different-root",
        root_message_id="om_root_b",
        created_at="2026-07-22T11:59:30+00:00",
    )
    _eligible(
        store,
        4,
        app_id="cli_b",
        message_id="om_other_app",
        body_text="different-app",
        root_message_id="om_root_a",
        created_at="2026-07-22T11:59:30+00:00",
    )
    trigger = _eligible(
        store,
        5,
        message_id="om_trigger_a",
        body_text="trigger",
        root_message_id="om_root_a",
        created_at="2026-07-22T12:00:00+00:00",
    )

    context = store.list_feishu_context(
        "oc_1",
        app_id="cli_a",
        thread_id="",
        root_message_id="om_root_a",
        before_message_id=trigger.message_id,
        lookback_seconds=60,
    )

    assert [item.body_text for item in context] == ["just-inside"]


@pytest.mark.parametrize("lookback_seconds", [0, 30 * 86400 + 1])
def test_context_rejects_invalid_temporal_lookback(tmp_path, lookback_seconds):
    store = _store(tmp_path)
    with pytest.raises(ValueError, match="lookback_seconds"):
        store.list_feishu_context(
            "oc_1", lookback_seconds=lookback_seconds
        )


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
            expected_approval_hash=delivery.approval_hash,
        )

    approved = store.approve_feishu_delivery(
        delivery.id,
        app_id="cli_a",
        approved_by="operator",
        expected_approval_hash=delivery.approval_hash,
        now="2026-07-22T12:00:00+08:00",
    )
    [claimed] = store.claim_feishu_deliveries(
        10, app_id="cli_a", approved_only=True
    )

    assert approved.approved_at == "2026-07-22T12:00:00+08:00"
    assert approved.approved_by == "operator"
    assert claimed.id == delivery.id


def test_delivery_preview_hash_cas_and_db_identity_trigger_block_tampering(
    tmp_path,
):
    store = _store(tmp_path)
    event = _eligible(store)
    delivery = store.create_feishu_delivery(
        reply_task_id=event.reply_task_id,
        attempt_id=_attempt(store, event),
        app_id="cli_a",
        chat_id="oc_1",
        reply_to_message_id="om_1",
        reply_in_thread=False,
        reply_text="immutable approved body",
    )

    with pytest.raises(ValueError, match="approval hash changed"):
        store.approve_feishu_delivery(
            delivery.id,
            app_id="cli_a",
            approved_by="operator",
            expected_approval_hash="0" * 64,
        )
    assert store.get_feishu_delivery(delivery.id).approved_at == ""

    with sqlite3.connect(store.path) as db, pytest.raises(
        sqlite3.IntegrityError, match="identity is immutable"
    ):
        db.execute(
            "update feishu_deliveries set reply_text='tampered' where id=?",
            (delivery.id,),
        )

    saved = store.get_feishu_delivery(delivery.id)
    assert saved.reply_text == "immutable approved body"
    assert saved.approval_hash == delivery.approval_hash


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


def _reopened_older_delivery_with_newer_unknown(store):
    events = [
        _eligible(store, number, chat_id="oc_unknown_order")
        for number in (1, 2)
    ]
    deliveries = [
        store.create_feishu_delivery(
            reply_task_id=event.reply_task_id,
            attempt_id=_attempt(store, event),
            app_id="cli_a",
            chat_id="oc_unknown_order",
            reply_to_message_id=event.message_id,
            reply_in_thread=False,
            reply_text=f"reply-{event.message_id}",
        )
        for event in events
    ]

    first_claim = store.claim_feishu_delivery(
        deliveries[0].id, app_id="cli_a"
    )
    store.mark_feishu_delivery_send_unknown(
        deliveries[0].id,
        error_code="send_timeout",
        error="older outcome unknown",
    )
    store.reconcile_feishu_delivery_unknown(
        deliveries[0].id,
        app_id="cli_a",
        outcome="not_sent",
        verified_by="operator",
        evidence_kind="message_lookup",
    )

    second_claim = store.claim_feishu_delivery(
        deliveries[1].id, app_id="cli_a"
    )
    assert first_claim is not None and second_claim is not None
    reopened = store.requeue_feishu_delivery_after_verification(
        deliveries[0].id,
        app_id="cli_a",
        verified_by="operator",
        evidence_kind="admin_audit",
    )
    store.mark_feishu_delivery_send_unknown(
        deliveries[1].id,
        error_code="send_timeout",
        error="newer outcome unknown",
    )
    assert reopened.status == "retry"
    return deliveries


def test_specific_claim_cannot_cross_newer_same_conversation_send_unknown(
    tmp_path,
):
    store = _store(tmp_path)
    older, newer = _reopened_older_delivery_with_newer_unknown(store)

    assert older.id < newer.id
    assert store.claim_feishu_delivery(older.id, app_id="cli_a") is None
    assert store.get_feishu_delivery(older.id).status == "retry"
    assert store.get_feishu_delivery(newer.id).status == "send_unknown"


def test_batch_claim_cannot_cross_newer_send_unknown_but_keeps_isolation(
    tmp_path,
):
    store = _store(tmp_path)
    older, newer = _reopened_older_delivery_with_newer_unknown(store)
    other_chat_event = _eligible(store, 3, chat_id="oc_independent")
    other_chat = store.create_feishu_delivery(
        reply_task_id=other_chat_event.reply_task_id,
        attempt_id=_attempt(store, other_chat_event),
        app_id="cli_a",
        chat_id="oc_independent",
        reply_to_message_id=other_chat_event.message_id,
        reply_in_thread=False,
        reply_text="independent chat",
    )
    other_app_event = _eligible(
        store,
        4,
        app_id="cli_b",
        chat_id="oc_unknown_order",
    )
    other_app = store.create_feishu_delivery(
        reply_task_id=other_app_event.reply_task_id,
        attempt_id=_attempt(store, other_app_event),
        app_id="cli_b",
        chat_id="oc_unknown_order",
        reply_to_message_id=other_app_event.message_id,
        reply_in_thread=False,
        reply_text="independent app",
    )

    claimed_a = store.claim_feishu_deliveries(10, app_id="cli_a")
    claimed_b = store.claim_feishu_deliveries(10, app_id="cli_b")

    assert [delivery.id for delivery in claimed_a] == [other_chat.id]
    assert [delivery.id for delivery in claimed_b] == [other_app.id]
    assert store.get_feishu_delivery(older.id).status == "retry"
    assert store.get_feishu_delivery(newer.id).status == "send_unknown"


def test_verified_requeue_cannot_cross_other_same_conversation_send_unknown(
    tmp_path,
):
    store = _store(tmp_path)
    events = [
        _eligible(store, number, chat_id="oc_requeue_unknown")
        for number in (1, 2)
    ]
    deliveries = [
        store.create_feishu_delivery(
            reply_task_id=event.reply_task_id,
            attempt_id=_attempt(store, event),
            app_id="cli_a",
            chat_id="oc_requeue_unknown",
            reply_to_message_id=event.message_id,
            reply_in_thread=False,
            reply_text=f"reply-{event.message_id}",
        )
        for event in events
    ]
    first_claim = store.claim_feishu_delivery(deliveries[0].id, app_id="cli_a")
    assert first_claim is not None
    store.mark_feishu_delivery_send_unknown(
        deliveries[0].id,
        error_code="send_timeout",
        error="older outcome unknown",
    )
    failed = store.reconcile_feishu_delivery_unknown(
        deliveries[0].id,
        app_id="cli_a",
        outcome="not_sent",
        verified_by="operator",
        evidence_kind="message_lookup",
    )
    second_claim = store.claim_feishu_delivery(deliveries[1].id, app_id="cli_a")
    assert second_claim is not None
    store.mark_feishu_delivery_send_unknown(
        deliveries[1].id,
        error_code="send_timeout",
        error="newer outcome unknown",
    )

    with pytest.raises(ValueError, match="unresolved send"):
        store.requeue_feishu_delivery_after_verification(
            deliveries[0].id,
            app_id="cli_a",
            verified_by="operator",
            evidence_kind="admin_audit",
        )

    unchanged = store.get_feishu_delivery(deliveries[0].id)
    assert unchanged.status == "failed"
    assert unchanged.error_code == "verified_not_sent"
    assert unchanged.review_generation == failed.review_generation

    store.reconcile_feishu_delivery_unknown(
        deliveries[1].id,
        app_id="cli_a",
        outcome="not_sent",
        verified_by="operator",
        evidence_kind="message_lookup",
    )
    other_app_event = _eligible(
        store,
        3,
        app_id="cli_b",
        chat_id="oc_requeue_unknown",
    )
    other_app_delivery = store.create_feishu_delivery(
        reply_task_id=other_app_event.reply_task_id,
        attempt_id=_attempt(store, other_app_event),
        app_id="cli_b",
        chat_id="oc_requeue_unknown",
        reply_to_message_id=other_app_event.message_id,
        reply_in_thread=False,
        reply_text="other app unknown",
    )
    other_app_claim = store.claim_feishu_delivery(
        other_app_delivery.id, app_id="cli_b"
    )
    assert other_app_claim is not None
    store.mark_feishu_delivery_send_unknown(
        other_app_delivery.id,
        error_code="send_timeout",
        error="other app outcome unknown",
    )

    reopened = store.requeue_feishu_delivery_after_verification(
        deliveries[0].id,
        app_id="cli_a",
        verified_by="operator",
        evidence_kind="admin_audit",
    )
    assert reopened.status == "retry"


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


@pytest.mark.parametrize(
    ("kind", "outcome", "remote_id", "expected_status"),
    (
        ("add_reaction", "applied", "omr_verified", "sent"),
        ("add_reaction", "not_applied", "", "failed"),
        ("recall_message", "applied", "", "sent"),
        ("recall_message", "not_applied", "", "failed"),
        ("handoff_notify", "applied", "om_handoff_verified", "sent"),
        ("handoff_notify", "not_applied", "", "failed"),
    ),
)
def test_message_action_unknown_reconciliation_matrix_is_atomic(
    tmp_path, kind, outcome, remote_id, expected_status
):
    store, action, receipt = _unknown_store_action(tmp_path, kind=kind)

    saved = store.reconcile_feishu_message_action_unknown(
        action.id,
        app_id="cli_a",
        outcome=outcome,
        verified_by="final-state-reviewer",
        evidence_kind="message_lookup",
        remote_id=remote_id,
    )

    assert saved.status == expected_status
    assert saved.remote_id == remote_id
    assert saved.request_log_id == "log-original"
    assert saved.lease_token == ""
    if outcome == "not_applied":
        assert saved.error_code == "verified_not_applied"
        assert saved.error == "manual_verification_confirmed_not_applied"
    else:
        assert saved.error_code == ""
        assert saved.error == ""
    current_receipt = store.get_feishu_delivery_receipt(
        app_id="cli_a", message_id=receipt.message_id
    )
    if kind == "recall_message" and outcome == "applied":
        assert current_receipt.status == "recalled"
        assert current_receipt.recall_action_id == action.id
    elif kind == "recall_message":
        assert current_receipt.status == "active"
        assert current_receipt.recall_action_id == 0
    else:
        assert current_receipt.status == "active"
        assert current_receipt.recall_action_id == 0
    [audit] = [
        event
        for event in store.list_feishu_audit_events(
            entity_type="message_action", entity_id=action.id
        )
        if event.event_type.startswith("unknown_verified_")
    ]
    assert audit.event_type == f"unknown_verified_{outcome}"
    assert audit.previous_state == "result_unknown"
    assert audit.new_state == expected_status
    assert audit.actor == "final-state-reviewer"
    assert audit.detail == "evidence_kind=message_lookup"


@pytest.mark.parametrize(
    ("kind", "outcome", "remote_id", "app_id", "evidence", "error_type", "match"),
    (
        (
            "add_reaction",
            "applied",
            "omr_verified",
            "other_app",
            "message_lookup",
            PermissionError,
            "App ID",
        ),
        (
            "add_reaction",
            "applied",
            "omr_verified",
            "cli_a",
            "free_form_note",
            ValueError,
            "evidence kind",
        ),
        (
            "add_reaction",
            "applied",
            "",
            "cli_a",
            "message_lookup",
            ValueError,
            "reaction ID",
        ),
        (
            "recall_message",
            "applied",
            "om_action_bot",
            "cli_a",
            "message_lookup",
            ValueError,
            "must not copy",
        ),
        (
            "handoff_notify",
            "not_applied",
            "om_handoff",
            "cli_a",
            "message_lookup",
            ValueError,
            "must not include",
        ),
        (
            "handoff_notify",
            "unknown",
            "",
            "cli_a",
            "message_lookup",
            ValueError,
            "outcome",
        ),
    ),
)
def test_message_action_reconciliation_rejects_wrong_identity_or_evidence(
    tmp_path,
    kind,
    outcome,
    remote_id,
    app_id,
    evidence,
    error_type,
    match,
):
    store, action, receipt = _unknown_store_action(tmp_path, kind=kind)
    before_audits = store.list_feishu_audit_events(
        entity_type="message_action", entity_id=action.id
    )

    with pytest.raises(error_type, match=match):
        store.reconcile_feishu_message_action_unknown(
            action.id,
            app_id=app_id,
            outcome=outcome,
            verified_by="reviewer",
            evidence_kind=evidence,
            remote_id=remote_id,
        )

    current = store.get_feishu_message_action(action.id)
    assert current.status == "result_unknown"
    assert current.remote_id == ""
    assert store.get_feishu_delivery_receipt(
        app_id="cli_a", message_id=receipt.message_id
    ) == receipt
    assert store.list_feishu_audit_events(
        entity_type="message_action", entity_id=action.id
    ) == before_audits


def test_message_action_reconciliation_request_log_is_fill_only(tmp_path):
    store, action, _ = _unknown_store_action(tmp_path)

    with pytest.raises(ValueError, match="request log ID conflicts"):
        store.reconcile_feishu_message_action_unknown(
            action.id,
            app_id="cli_a",
            outcome="applied",
            verified_by="reviewer",
            evidence_kind="admin_audit",
            remote_id="omr_verified",
            request_log_id="log-replacement",
        )
    unchanged = store.get_feishu_message_action(action.id)
    assert unchanged.status == "result_unknown"
    assert unchanged.request_log_id == "log-original"


def test_message_action_reconciliation_can_fill_missing_request_log(tmp_path):
    store, action, _ = _unknown_store_action(tmp_path, request_log_id="")

    saved = store.reconcile_feishu_message_action_unknown(
        action.id,
        app_id="cli_a",
        outcome="applied",
        verified_by="reviewer",
        evidence_kind="admin_audit",
        remote_id="omr_verified",
        request_log_id="log-filled-once",
    )

    assert saved.request_log_id == "log-filled-once"


def test_message_action_reconciliation_requires_exact_unknown_state(tmp_path):
    store, action, _ = _unknown_store_action(tmp_path)
    store.reconcile_feishu_message_action_unknown(
        action.id,
        app_id="cli_a",
        outcome="applied",
        verified_by="reviewer",
        evidence_kind="feishu_ui",
        remote_id="omr_verified",
    )

    with pytest.raises(ValueError, match="requires result_unknown"):
        store.reconcile_feishu_message_action_unknown(
            action.id,
            app_id="cli_a",
            outcome="applied",
            verified_by="reviewer",
            evidence_kind="feishu_ui",
            remote_id="omr_verified",
        )
    with pytest.raises(ValueError, match="invalid Feishu message action transition"):
        store.transition_feishu_message_action(
            action.id,
            from_statuses=("result_unknown",),
            to_status="sent",
            app_id="cli_a",
            remote_id="omr_bypass",
        )
    audits = [
        event
        for event in store.list_feishu_audit_events(
            entity_type="message_action", entity_id=action.id
        )
        if event.event_type == "unknown_verified_applied"
    ]
    assert len(audits) == 1


def test_recall_reconciliation_requires_receipt_action_cas(tmp_path):
    store, action, receipt = _unknown_store_action(
        tmp_path, kind="recall_message"
    )
    with store._connect() as db:
        db.execute(
            """
            update feishu_delivery_receipts set recall_action_id=0
            where id=?
            """,
            (receipt.id,),
        )

    with pytest.raises(ValueError, match="does not match unknown action"):
        store.reconcile_feishu_message_action_unknown(
            action.id,
            app_id="cli_a",
            outcome="applied",
            verified_by="reviewer",
            evidence_kind="message_lookup",
        )

    assert store.get_feishu_message_action(action.id).status == "result_unknown"
    current_receipt = store.get_feishu_delivery_receipt(
        app_id="cli_a", message_id=receipt.message_id
    )
    assert current_receipt.status == "recall_unknown"
    assert current_receipt.recall_action_id == 0


def test_recall_reconciliation_rolls_back_action_and_receipt_when_audit_fails(
    tmp_path,
):
    store, action, receipt = _unknown_store_action(
        tmp_path, kind="recall_message"
    )
    with store._connect() as db:
        db.execute(
            """
            create trigger fail_store_action_reconciliation_audit
            before insert on feishu_audit_events
            when new.event_type='unknown_verified_applied'
            begin
                select raise(abort, 'forced action audit failure');
            end
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="forced action audit failure"):
        store.reconcile_feishu_message_action_unknown(
            action.id,
            app_id="cli_a",
            outcome="applied",
            verified_by="reviewer",
            evidence_kind="feishu_ui",
        )

    current = store.get_feishu_message_action(action.id)
    current_receipt = store.get_feishu_delivery_receipt(
        app_id="cli_a", message_id=receipt.message_id
    )
    assert current.status == "result_unknown"
    assert current.error_code == "send_timeout"
    assert current_receipt.status == "recall_unknown"
    assert current_receipt.recall_action_id == action.id
    assert not any(
        event.event_type == "unknown_verified_applied"
        for event in store.list_feishu_audit_events(
            entity_type="message_action", entity_id=action.id
        )
    )


def test_concurrent_message_action_reconciliation_has_one_winner(tmp_path):
    store, action, _ = _unknown_store_action(tmp_path)
    barrier = threading.Barrier(2)

    def reconcile():
        worker = AutoReplyStore(store.path)
        barrier.wait()
        try:
            return worker.reconcile_feishu_message_action_unknown(
                action.id,
                app_id="cli_a",
                outcome="applied",
                verified_by="reviewer",
                evidence_kind="message_lookup",
                remote_id="omr_verified",
            )
        except ValueError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: reconcile(), range(2)))

    assert sum(not isinstance(result, Exception) for result in results) == 1
    assert sum(isinstance(result, ValueError) for result in results) == 1
    assert store.get_feishu_message_action(action.id).status == "sent"
    assert len(
        [
            event
            for event in store.list_feishu_audit_events(
                entity_type="message_action", entity_id=action.id
            )
            if event.event_type == "unknown_verified_applied"
        ]
    ) == 1


def test_verified_not_applied_action_requeue_is_separate_and_clears_approval(
    tmp_path,
):
    store, action, _ = _unknown_store_action(tmp_path)
    failed = store.reconcile_feishu_message_action_unknown(
        action.id,
        app_id="cli_a",
        outcome="not_applied",
        verified_by="first-reviewer",
        evidence_kind="message_lookup",
    )
    assert failed.approved_at

    retry = store.requeue_feishu_message_action_after_verification(
        action.id,
        app_id="cli_a",
        verified_by="second-reviewer",
        evidence_kind="admin_audit",
        available_at="2026-07-22T12:05:00+08:00",
    )

    assert retry.status == "retry"
    assert retry.approved_at == ""
    assert retry.approved_by == ""
    assert retry.available_at == "2026-07-22T04:05:00+00:00"
    assert retry.error_code == ""
    assert retry.error == ""
    assert retry.request_log_id == "log-original"
    [audit] = [
        event
        for event in store.list_feishu_audit_events(
            entity_type="message_action", entity_id=action.id
        )
        if event.event_type == "requeued_after_verification"
    ]
    assert audit.actor == "second-reviewer"
    assert audit.detail == "evidence_kind=admin_audit"


def test_message_action_requeue_rejects_unverified_or_unsafe_state(tmp_path):
    store, action, _ = _unknown_store_action(tmp_path)
    store.reconcile_feishu_message_action_unknown(
        action.id,
        app_id="cli_a",
        outcome="not_applied",
        verified_by="reviewer",
        evidence_kind="message_lookup",
    )

    with pytest.raises(ValueError, match="evidence kind"):
        store.requeue_feishu_message_action_after_verification(
            action.id,
            app_id="cli_a",
            verified_by="reviewer",
            evidence_kind="operator_note",
        )
    with pytest.raises(ValueError, match="requires timezone"):
        store.requeue_feishu_message_action_after_verification(
            action.id,
            app_id="cli_a",
            verified_by="reviewer",
            evidence_kind="feishu_ui",
            available_at="2026-07-22T12:05:00",
        )
    with pytest.raises(PermissionError, match="App ID"):
        store.requeue_feishu_message_action_after_verification(
            action.id,
            app_id="other_app",
            verified_by="reviewer",
            evidence_kind="feishu_ui",
        )
    assert store.get_feishu_message_action(action.id).status == "failed"


def test_recall_action_requeue_requires_restored_active_receipt(tmp_path):
    store, action, receipt = _unknown_store_action(
        tmp_path, kind="recall_message"
    )
    store.reconcile_feishu_message_action_unknown(
        action.id,
        app_id="cli_a",
        outcome="not_applied",
        verified_by="reviewer",
        evidence_kind="message_lookup",
    )
    with store._connect() as db:
        db.execute(
            """
            update feishu_delivery_receipts
            set status='recalled', recall_action_id=? where id=?
            """,
            (action.id, receipt.id),
        )

    with pytest.raises(PermissionError, match="active terminal receipt"):
        store.requeue_feishu_message_action_after_verification(
            action.id,
            app_id="cli_a",
            verified_by="reviewer",
            evidence_kind="admin_audit",
        )
    assert store.get_feishu_message_action(action.id).status == "failed"


def test_local_action_reject_closes_inactive_target_without_remote_gate(tmp_path):
    store, event, task, attempt_id, receipt = _action_context(tmp_path)
    action = _create_store_action(
        store,
        event,
        task,
        attempt_id,
        receipt,
        kind="recall_message",
    )
    store.approve_feishu_message_action(
        action.id,
        app_id="cli_a",
        approved_by="first-reviewer",
        expected_approval_hash=action.approval_hash,
    )
    with store._connect() as db:
        db.execute(
            """
            update feishu_delivery_receipts
            set status='recalled', recall_action_id=999 where id=?
            """,
            (receipt.id,),
        )

    rejected = store.reject_feishu_message_action(
        action.id,
        app_id="cli_a",
        rejected_by="incident-reviewer",
    )

    assert rejected.status == "rejected"
    assert rejected.error_code == "rejected"
    assert rejected.approved_at
    [audit] = [
        event
        for event in store.list_feishu_audit_events(
            entity_type="message_action", entity_id=action.id
        )
        if event.event_type == "rejected"
    ]
    assert audit.actor == "incident-reviewer"
