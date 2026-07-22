import json
import sqlite3
from datetime import datetime, timezone

import pytest

from app.feishu.maintenance import purge_expired_feishu_events
from app.feishu.models import FeishuInboundMessage
from app.store import AutoReplyStore


def _message(
    number: int,
    *,
    app_id: str = "cli_a",
    chat_id: str = "oc_1",
    message_id: str = "",
    thread_id: str = "",
    event_time: str = "",
) -> FeishuInboundMessage:
    return FeishuInboundMessage(
        event_id=f"evt_{app_id}_{number}",
        app_id=app_id,
        message_id=message_id or f"om_{app_id}_{number}",
        chat_id=chat_id,
        chat_type="topic" if thread_id else "group",
        chat_title="Group",
        thread_id=thread_id,
        sender_open_id="ou_1",
        sender_name="Alex",
        message_type="text",
        mentioned_bot=True,
        body_text=f"text-{number}",
        event_create_time=event_time or f"2026-07-22T10:00:{number:02d}+00:00",
        received_at=f"2026-07-22T10:01:{number:02d}+00:00",
    )


def _eligible(store, number: int, **fields):
    return store.record_feishu_event(
        _message(number, **fields),
        eligibility_status="eligible",
        store_body=True,
    )


def _task(store, task_id: int):
    return next(row for row in store.list_reply_tasks(channel="feishu") if row.id == task_id)


def _attempt(store, event):
    task = _task(store, event.reply_task_id)
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


def _attempt_and_delivery(
    store, *, number: int = 1, app_id: str = "cli_a", chat_id: str = "oc_1"
):
    event = _eligible(store, number, app_id=app_id, chat_id=chat_id)
    task = _task(store, event.reply_task_id)
    attempt_id = store.record_reply_attempt(
        conversation_id=task.conversation_id,
        conversation_title=task.conversation_title,
        trigger_message_id=event.message_id,
        trigger_sender="Alex",
        trigger_text=event.body_text,
        action="send_reply",
        sensitivity_kind="general",
        draft_reply_text="reply",
        send_status="pending",
        channel="feishu",
    )
    delivery = store.create_feishu_delivery(
        reply_task_id=task.id,
        attempt_id=attempt_id,
        app_id=event.app_id,
        chat_id=event.chat_id,
        reply_to_message_id=event.message_id,
        reply_in_thread=False,
        reply_text="reply",
    )
    return event, task, attempt_id, delivery


def test_context_is_thread_scoped_as_of_trigger_and_excludes_future(tmp_path):
    store = AutoReplyStore(tmp_path / "db.sqlite3")
    _eligible(store, 1, thread_id="thread-a", event_time="1784714401000")
    _eligible(store, 2, thread_id="thread-b", event_time="1784714402000")
    _eligible(store, 3, thread_id="", event_time="1784714403000")
    # A future event arrives before the trigger and must still be excluded.
    _eligible(store, 4, thread_id="thread-a", event_time="1784714499000")
    trigger = _eligible(
        store, 5, thread_id="thread-a", event_time="1784714405000"
    )
    _eligible(store, 6, thread_id="thread-a", event_time="1784714404000")

    context = store.list_feishu_context(
        "oc_1",
        app_id="cli_a",
        thread_id="thread-a",
        before_message_id=trigger.message_id,
    )

    assert [row.message_id for row in context] == ["om_cli_a_1"]
    with pytest.raises(ValueError, match="boundary scope"):
        store.list_feishu_context(
            "oc_1",
            app_id="cli_a",
            thread_id="thread-b",
            before_message_id=trigger.message_id,
        )
    with pytest.raises(ValueError, match="requires app_id and thread_id"):
        store.list_feishu_context(
            "oc_1", before_message_id=trigger.message_id
        )


def test_reply_task_identity_is_namespaced_by_app(tmp_path):
    store = AutoReplyStore(tmp_path / "db.sqlite3")
    first = _eligible(store, 1, app_id="cli_a", message_id="om_same")
    second = _eligible(store, 2, app_id="cli_b", message_id="om_same")

    first_task = _task(store, first.reply_task_id)
    second_task = _task(store, second.reply_task_id)
    assert first_task.id != second_task.id
    assert first_task.conversation_id != second_task.conversation_id
    assert first_task.conversation_id.endswith(":oc_1")
    with pytest.raises(PermissionError, match="App ID"):
        store.create_feishu_delivery(
            reply_task_id=first_task.id,
            attempt_id=_attempt(store, first),
            app_id="cli_b",
            chat_id="oc_1",
            reply_to_message_id="om_same",
            reply_in_thread=False,
            reply_text="wrong app",
        )


def test_retention_is_bounded_and_preserves_unresolved_evidence(tmp_path):
    store = AutoReplyStore(tmp_path / "db.sqlite3")
    completed = _eligible(store, 1)
    pending = _eligible(store, 2)
    uncertain = _eligible(store, 3)
    uncertain_delivery = store.create_feishu_delivery(
        reply_task_id=uncertain.reply_task_id,
        attempt_id=_attempt(store, uncertain),
        app_id=uncertain.app_id,
        chat_id=uncertain.chat_id,
        reply_to_message_id=uncertain.message_id,
        reply_in_thread=False,
        reply_text="reply",
    )
    store.claim_feishu_delivery(uncertain_delivery.id)
    store.mark_feishu_delivery_send_unknown(
        uncertain_delivery.id,
        error_code="send_timeout",
        error="unknown",
    )
    rejected = store.record_feishu_event(
        _message(4),
        eligibility_status="rejected",
        reject_reason="scope_pending",
        store_body=False,
    )
    store.complete_reply_task(completed.reply_task_id)
    with store._connect() as db:
        db.execute(
            "update feishu_events set created_at='2026-01-01 00:00:00'"
        )

    first = purge_expired_feishu_events(
        store,
        retention_days=30,
        now=datetime(2026, 7, 22, tzinfo=timezone.utc),
        batch_limit=1,
        max_batches=1,
    )
    assert first.deleted_events == 1
    assert first.more_may_remain is True
    second = purge_expired_feishu_events(
        store,
        retention_days=30,
        now=datetime(2026, 7, 22, tzinfo=timezone.utc),
        batch_limit=10,
        max_batches=2,
    )
    assert second.deleted_events == 1
    assert store.get_feishu_event(completed.id) is None
    assert store.get_feishu_event(rejected.id) is None
    assert store.get_feishu_event(pending.id) is not None
    assert store.get_feishu_event(uncertain.id) is not None

    audit = store.list_feishu_audit_events(entity_type="retention")
    assert sum("deleted=" in row.detail for row in audit) == 2
    with store._connect() as db:
        audit_id = audit[0].id
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            db.execute(
                "update feishu_audit_events set event_type='changed' where id=?",
                (audit_id,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            db.execute("delete from feishu_audit_events where id=?", (audit_id,))


def test_delivery_attempt_sync_and_verified_unknown_recovery(tmp_path):
    store = AutoReplyStore(tmp_path / "db.sqlite3")
    _, _, attempt_id, delivery = _attempt_and_delivery(store)
    [recorded] = store.list_feishu_audit_events(
        entity_type="reply_attempt", entity_id=attempt_id
    )
    assert recorded.event_type == "attempt_recorded"
    assert recorded.new_state == "pending"
    store.approve_feishu_delivery(
        delivery.id,
        app_id="cli_a",
        approved_by="operator",
        expected_approval_hash=delivery.approval_hash,
    )
    assert store.get_reply_attempt(attempt_id).send_status == "pending"

    store.claim_feishu_delivery(delivery.id, approved_only=True)
    assert store.get_reply_attempt(attempt_id).send_status == "processing"
    store.mark_feishu_delivery_send_unknown(
        delivery.id,
        error_code="send_timeout",
        error="uncertain",
    )
    assert store.get_reply_attempt(attempt_id).send_status == "send_unknown"
    with pytest.raises(ValueError):
        store.mark_feishu_delivery_retry(
            delivery.id, error_code="send_timeout", error="unsafe"
        )
    with pytest.raises(ValueError, match="exact verified"):
        store.transition_feishu_delivery(
            delivery.id,
            from_statuses=("send_unknown",),
            to_status="failed",
            audit_event_type="unknown_verified_sent",
        )

    failed = store.reconcile_feishu_delivery_unknown(
        delivery.id,
        app_id="cli_a",
        outcome="not-sent",
        verified_by="operator",
        evidence_kind="message_lookup",
    )
    assert failed.status == "failed"
    assert store.get_reply_attempt(attempt_id).send_status == "failed"
    requeued = store.requeue_feishu_delivery_after_verification(
        delivery.id,
        app_id="cli_a",
        verified_by="operator",
        evidence_kind="admin_audit",
    )
    assert requeued.status == "retry"
    assert requeued.approved_at == ""
    assert store.get_reply_attempt(attempt_id).send_status == "processing"
    assert {
        "created",
        "approved",
        "claimed",
        "send_unknown",
        "unknown_verified_not_sent",
        "requeued_after_verification",
    } <= {
        row.event_type
        for row in store.list_feishu_audit_events(
            entity_type="delivery", entity_id=delivery.id
        )
    }


def test_delivery_schema_migration_invalidates_stale_approval_snapshot(tmp_path):
    store = AutoReplyStore(tmp_path / "db.sqlite3")
    _, _, _, delivery = _attempt_and_delivery(store)
    store.approve_feishu_delivery(
        delivery.id,
        app_id=delivery.app_id,
        approved_by="legacy-reviewer",
        expected_approval_hash=delivery.approval_hash,
    )
    with sqlite3.connect(store.path) as db:
        db.execute("drop trigger feishu_deliveries_identity_immutable")
        db.execute(
            """
            update feishu_deliveries
            set expected_chunks=2, approval_hash=?
            where id=?
            """,
            ("0" * 64, delivery.id),
        )

    store._initialize()

    migrated = store.get_feishu_delivery(delivery.id)
    assert migrated.expected_chunks == 1
    assert migrated.approval_hash == delivery.approval_hash
    assert migrated.approved_at == ""
    assert migrated.approved_by == ""
    assert any(
        row.event_type == "approval_snapshot_migrated"
        and row.actor == "schema-migration"
        and row.detail == "approval_invalidated=1"
        for row in store.list_feishu_audit_events(
            entity_type="delivery", entity_id=delivery.id
        )
    )


def test_feishu_attempt_requires_a_durable_reply_task(tmp_path):
    store = AutoReplyStore(tmp_path / "db.sqlite3")

    with pytest.raises(ValueError, match="durable reply task"):
        store.record_reply_attempt(
            conversation_id="feishu:missing:oc_1",
            conversation_title="Missing",
            trigger_message_id="om_missing",
            trigger_sender="Alex",
            trigger_text="hello",
            action="send_reply",
            sensitivity_kind="general",
            send_status="pending",
            channel="feishu",
        )

    assert store.list_feishu_audit_events(entity_type="reply_attempt") == []


def test_missing_attempt_is_quarantined_without_a_send_claim(tmp_path):
    store = AutoReplyStore(tmp_path / "db.sqlite3")
    _, _, attempt_id, delivery = _attempt_and_delivery(store)
    # Simulate an old/corrupted database with FK checks disabled.  New
    # databases enforce the attempt FK and ordinary store connections cannot
    # create this state.
    with sqlite3.connect(store.path) as db:
        db.execute("pragma foreign_keys=off")
        db.execute("delete from reply_attempts where id=?", (attempt_id,))

    assert store.claim_feishu_delivery(delivery.id) is None

    saved = store.get_feishu_delivery(delivery.id)
    assert saved.status == "failed"
    assert saved.error_code == "legacy_identity_unverifiable"
    events = store.list_feishu_audit_events(
        entity_type="delivery", entity_id=delivery.id
    )
    assert [row.event_type for row in events] == [
        "invalid_binding_quarantined",
        "created",
    ]


def test_invalid_batch_candidate_does_not_block_another_chat(tmp_path):
    store = AutoReplyStore(tmp_path / "db.sqlite3")
    _, _, bad_attempt_id, bad = _attempt_and_delivery(
        store, number=1, chat_id="oc_bad"
    )
    _, _, _, good = _attempt_and_delivery(store, number=2, chat_id="oc_good")
    with sqlite3.connect(store.path) as db:
        db.execute("pragma foreign_keys=off")
        db.execute("delete from reply_attempts where id=?", (bad_attempt_id,))

    claimed = store.claim_feishu_deliveries(50)

    assert [row.id for row in claimed] == [good.id]
    assert store.get_feishu_delivery(bad.id).status == "failed"
    assert store.get_feishu_delivery(good.id).status == "sending"


def test_target_misbinding_migration_quarantines_attempt_and_delivery(tmp_path):
    path = tmp_path / "db.sqlite3"
    store = AutoReplyStore(path)
    _, _, attempt_id, delivery = _attempt_and_delivery(store)
    with sqlite3.connect(path) as db:
        db.execute("drop trigger feishu_deliveries_identity_immutable")
        db.execute(
            "update feishu_deliveries set reply_to_message_id='om_wrong' where id=?",
            (delivery.id,),
        )

    # Re-run initialization to model a fresh process.  Store initialization is
    # intentionally memoized per path inside one test process.
    store._initialize()
    migrated = store

    saved = migrated.get_feishu_delivery(delivery.id)
    assert saved.status == "failed"
    assert saved.error_code == "legacy_identity_unverifiable"
    assert migrated.get_reply_attempt(attempt_id).send_status == "failed"
    assert {
        row.event_type
        for row in migrated.list_feishu_audit_events(
            entity_type="delivery", entity_id=delivery.id
        )
    } >= {"created", "invalid_binding_quarantined"}


def test_terminal_target_misbinding_preserves_fact_and_is_idempotent(tmp_path):
    path = tmp_path / "db.sqlite3"
    store = AutoReplyStore(path)
    _, _, attempt_id, delivery = _attempt_and_delivery(store)
    with sqlite3.connect(path) as db:
        db.execute("drop trigger feishu_deliveries_identity_immutable")
        db.execute(
            """
            update feishu_deliveries
            set status='sent', feishu_message_id='om_reply', attempts=1,
                reply_to_message_id='om_wrong'
            where id=?
            """,
            (delivery.id,),
        )

    store._initialize()

    saved = store.get_feishu_delivery(delivery.id)
    assert saved.status == "sent"
    assert saved.feishu_message_id == "om_reply"
    assert saved.error_code == "legacy_identity_unverifiable"
    assert store.get_reply_attempt(attempt_id).send_status == "sent"

    with sqlite3.connect(path) as db:
        db.execute(
            "update feishu_deliveries set updated_at='2000-01-01 00:00:00' where id=?",
            (delivery.id,),
        )
        db.execute(
            "update reply_attempts set updated_at='2000-01-01 00:00:00' where id=?",
            (attempt_id,),
        )
    store._initialize()
    with store._connect() as db:
        delivery_updated_at = db.execute(
            "select updated_at from feishu_deliveries where id=?", (delivery.id,)
        ).fetchone()["updated_at"]
        attempt_updated_at = db.execute(
            "select updated_at from reply_attempts where id=?", (attempt_id,)
        ).fetchone()["updated_at"]
    assert delivery_updated_at == "2000-01-01 00:00:00"
    assert attempt_updated_at == "2000-01-01 00:00:00"


def test_valid_terminal_legacy_delivery_syncs_attempt_once(tmp_path):
    path = tmp_path / "db.sqlite3"
    store = AutoReplyStore(path)
    _, _, attempt_id, delivery = _attempt_and_delivery(store)
    with sqlite3.connect(path) as db:
        db.execute(
            """
            update feishu_deliveries
            set status='sent', feishu_message_id='om_reply', attempts=1
            where id=?
            """,
            (delivery.id,),
        )

    store._initialize()

    assert store.get_reply_attempt(attempt_id).send_status == "sent"
    with sqlite3.connect(path) as db:
        db.execute(
            "update reply_attempts set updated_at='2000-01-01 00:00:00' where id=?",
            (attempt_id,),
        )
    store._initialize()
    with store._connect() as db:
        updated_at = db.execute(
            "select updated_at from reply_attempts where id=?", (attempt_id,)
        ).fetchone()["updated_at"]
    assert updated_at == "2000-01-01 00:00:00"


def test_legacy_cross_task_attempt_is_rebound_and_synced(tmp_path):
    path = tmp_path / "db.sqlite3"
    store = AutoReplyStore(path)
    _, _, first_attempt, first = _attempt_and_delivery(
        store, number=1, chat_id="oc_1"
    )
    _, _, second_attempt, _ = _attempt_and_delivery(
        store, number=2, chat_id="oc_2"
    )
    with sqlite3.connect(path) as db:
        db.execute("drop trigger feishu_deliveries_identity_immutable")
        db.execute(
            """
            update feishu_deliveries
            set attempt_id=?, status='sent', feishu_message_id='om_reply',
                attempts=1
            where id=?
            """,
            (second_attempt, first.id),
        )

    store._initialize()

    rebound = store.get_feishu_delivery(first.id)
    assert rebound.attempt_id not in {first_attempt, second_attempt}
    assert rebound.status == "sent"
    assert store.get_reply_attempt(rebound.attempt_id).send_status == "sent"
    assert {
        row.event_type
        for row in store.list_feishu_audit_events(
            entity_type="reply_attempt", entity_id=rebound.attempt_id
        )
    } == {"legacy_attempt_rebound"}


def test_consumer_crash_windows_recover_without_second_model_run(tmp_path):
    store = AutoReplyStore(tmp_path / "db.sqlite3")
    event = _eligible(store, 1)
    [claimed] = store.claim_reply_tasks(1, channel="feishu")
    attempt_id = store.record_reply_attempt(
        conversation_id=claimed.conversation_id,
        conversation_title=claimed.conversation_title,
        trigger_message_id=event.message_id,
        trigger_sender="Alex",
        trigger_text=event.body_text,
        action="no_reply",
        sensitivity_kind="general",
        send_status="pending",
        channel="feishu",
    )

    assert store.recover_feishu_reply_task(claimed.id, app_id="cli_a") is True
    assert store.reply_task_is_done(claimed.id)
    assert store.get_reply_attempt(attempt_id).send_status == "skipped"

    second_event = _eligible(store, 2, chat_id="oc_2")
    [second_task] = store.claim_reply_tasks(1, channel="feishu")
    second_attempt = store.record_reply_attempt(
        conversation_id=second_task.conversation_id,
        conversation_title=second_task.conversation_title,
        trigger_message_id=second_event.message_id,
        trigger_sender="Alex",
        trigger_text=second_event.body_text,
        action="send_reply",
        sensitivity_kind="general",
        send_status="pending",
        channel="feishu",
    )
    delivery = store.create_feishu_delivery(
        reply_task_id=second_task.id,
        attempt_id=second_attempt,
        app_id="cli_a",
        chat_id="oc_2",
        reply_to_message_id=second_event.message_id,
        reply_in_thread=False,
        reply_text="reply",
    )
    assert store.recover_feishu_reply_task(second_task.id, app_id="cli_a") is True
    assert store.reply_task_is_done(second_task.id)
    assert store.get_feishu_delivery(delivery.id).id == delivery.id


def test_stale_task_owner_cannot_finalize_new_owner_claim(tmp_path):
    store = AutoReplyStore(tmp_path / "db.sqlite3")
    _eligible(store, 1)
    [old_owner] = store.claim_reply_tasks(1, channel="feishu")
    with sqlite3.connect(store.path) as db:
        db.execute(
            "update reply_tasks set locked_at=datetime('now', '-2 minutes') where id=?",
            (old_owner.id,),
        )
    assert store.reset_stale_processing_reply_tasks(
        60, channel="feishu"
    ) == 1
    [new_owner] = store.claim_reply_tasks(1, channel="feishu")
    assert old_owner.lease_token != new_owner.lease_token

    with pytest.raises(ValueError, match="lease"):
        store.finalize_feishu_reply_task(
            old_owner.id,
            app_id="cli_a",
            lease_token=old_owner.lease_token,
            action="no_reply",
            sensitivity_kind="general",
            task_status="done",
            send_status="skipped",
        )

    store.finalize_feishu_reply_task(
        new_owner.id,
        app_id="cli_a",
        lease_token=new_owner.lease_token,
        action="no_reply",
        sensitivity_kind="general",
        task_status="done",
        send_status="skipped",
    )
    assert store.reply_task_is_done(new_owner.id)
    assert len(store.list_reply_attempts()) == 1


@pytest.mark.parametrize("column_exists", [False, True])
def test_event_time_migration_resumes_partial_backfill(
    tmp_path, column_exists
):
    path = tmp_path / f"legacy-{column_exists}.sqlite3"
    connection = sqlite3.connect(path)
    extra_column = (
        "event_create_time_ms integer not null default 0,"
        if column_exists
        else ""
    )
    connection.execute(
        """
        create table reply_tasks (
            id integer primary key autoincrement,
            channel text not null default 'dingtalk',
            conversation_id text not null,
            conversation_title text not null,
            single_chat integer not null,
            trigger_message_id text not null,
            trigger_create_time text not null,
            trigger_sender text not null,
            trigger_text text not null,
            trigger_message_json text not null default '{}',
            execution_generation text not null default 'initial',
            status text not null default 'pending',
            attempts integer not null default 0,
            locked_at text,
            error text not null default '',
            created_at text not null default current_timestamp,
            updated_at text not null default current_timestamp,
            unique(conversation_id, trigger_message_id)
        )
        """
    )
    connection.execute(
        """
        insert into reply_tasks (
            id, conversation_id, conversation_title, single_chat,
            trigger_message_id, trigger_create_time, trigger_sender,
            trigger_text, execution_generation
        ) values (
            7, 'oc', 'Legacy', 0, 'om',
            '2026-07-22T10:00:05+00:00', 'Alex', 'hello', 'generation-7'
        )
        """
    )
    connection.execute(
        """
        update reply_tasks
        set channel='feishu', trigger_message_json=?
        where id=7
        """,
        (
            json.dumps(
                {
                    "event_id": "evt",
                    "app_id": "cli_a",
                    "message_id": "om",
                    "chat_id": "oc",
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
        ),
    )
    connection.execute(
        """
        create table feishu_deliveries (
            id integer primary key autoincrement,
            reply_task_id integer not null unique,
            attempt_id integer not null default 0,
            app_id text not null,
            chat_id text not null,
            reply_to_message_id text not null,
            reply_in_thread integer not null default 0,
            reply_text text not null,
            idempotency_key text not null unique,
            status text not null default 'ready_to_send',
            feishu_message_id text not null default '',
            request_log_id text not null default '',
            attempts integer not null default 0,
            approved_at text not null default '',
            approved_by text not null default '',
            locked_at text not null default '',
            available_at text not null default '',
            error_code text not null default '',
            error text not null default '',
            created_at text not null default current_timestamp,
            updated_at text not null default current_timestamp,
            foreign key(reply_task_id) references reply_tasks(id)
        )
        """
    )
    connection.execute(
        f"""
        create table feishu_events (
            id integer primary key autoincrement,
            event_id text not null unique,
            app_id text not null,
            message_id text not null,
            chat_id text not null,
            chat_type text not null,
            chat_title text not null default '',
            thread_id text not null default '',
            reply_to_message_id text not null default '',
            sender_open_id text not null,
            sender_type text not null default 'user',
            sender_name text not null default '',
            message_type text not null,
            mentioned_bot integer not null default 0,
            body_text text not null default '',
            event_create_time text not null,
            {extra_column}
            received_at text not null default current_timestamp,
            eligibility_status text not null,
            reject_reason text not null default '',
            reply_task_id integer,
            created_at text not null default current_timestamp,
            unique(app_id, message_id),
            foreign key(reply_task_id) references reply_tasks(id)
        )
        """
    )
    connection.execute(
        """
        insert into feishu_deliveries (
            reply_task_id, app_id, chat_id, reply_to_message_id,
            reply_text, idempotency_key
        ) values (7, 'cli_a', 'oc', 'om', 'legacy reply', 'legacy-key')
        """
    )
    connection.execute(
        """
        insert into feishu_events (
            event_id, app_id, message_id, chat_id, chat_type,
            sender_open_id, message_type, body_text, event_create_time,
            eligibility_status, reply_task_id
        ) values ('evt', 'cli_a', 'om', 'oc', 'group', 'ou', 'text',
                  'hello', '1784714405000', 'eligible', 7)
        """
    )
    connection.commit()
    connection.close()

    store = AutoReplyStore(path)
    with store._connect() as db:
        event_row = db.execute(
            """
            select event_create_time_ms, reply_task_id
            from feishu_events where event_id='evt'
            """
        ).fetchone()
        task_row = db.execute(
            """
            select execution_generation from reply_tasks where id=7
            """
        ).fetchone()
        violations = db.execute("pragma foreign_key_check").fetchall()
        delivery_row = db.execute(
            """
            select attempt_id, status, payload_sha256,
                   expected_chunks, approval_hash
            from feishu_deliveries
            where reply_task_id=7
            """
        ).fetchone()
        receipt_columns = {
            row["name"]
            for row in db.execute(
                "pragma table_info(feishu_delivery_receipts)"
            ).fetchall()
        }
        delivery_triggers = {
            row["name"]
            for row in db.execute(
                """
                select name from sqlite_master
                where type='trigger' and tbl_name='feishu_deliveries'
                """
            ).fetchall()
        }
        event_unique_columns = {
            tuple(
                item["name"]
                for item in db.execute(
                    f"pragma index_info('{index['name']}')"
                ).fetchall()
            )
            for index in db.execute("pragma index_list(feishu_events)").fetchall()
            if index["unique"]
        }
        media_fk_targets = {
            row["table"]
            for row in db.execute(
                "pragma foreign_key_list(feishu_media_assets)"
            ).fetchall()
        }
        event_columns = {
            row["name"]
            for row in db.execute("pragma table_info(feishu_events)").fetchall()
        }
    assert event_row["event_create_time_ms"] == 1784714405000
    assert event_row["reply_task_id"] == 7
    assert task_row["execution_generation"] == "generation-7"
    assert delivery_row["attempt_id"] > 0
    assert delivery_row["status"] == "ready_to_send"
    assert delivery_row["expected_chunks"] == 1
    assert len(delivery_row["payload_sha256"]) == 64
    assert len(delivery_row["approval_hash"]) == 64
    assert "request_log_id" in receipt_columns
    assert "feishu_deliveries_identity_immutable" in delivery_triggers
    assert ("event_id",) not in event_unique_columns
    assert ("app_id", "message_id") in event_unique_columns
    assert "feishu_events" in media_fk_targets
    assert {
        "root_message_id",
        "parent_message_id",
        "normalized_summary",
        "normalization_version",
        "content_truncated",
        "resource_truncated",
    } <= event_columns
    attempt = store.get_reply_attempt(delivery_row["attempt_id"])
    assert attempt.channel == "feishu"
    assert attempt.send_status == "pending"
    assert violations == []
