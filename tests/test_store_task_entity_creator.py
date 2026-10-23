from contextvars import ContextVar
import sqlite3

import pytest

from app.store import (
    AutoReplyStore,
    EmailReplyTaskAuthorizationConflict,
    ReplyTaskIdentityConflict,
    ReplyTaskSpec,
)
from app import task_entity_storage
from app.task8_storage import register_task8_functions
from app.task_run_entity_assembly import task_run_entity_assembly_objects


def _target_store(path):
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys=ON")
    register_task8_functions(connection)
    for ddl in task_run_entity_assembly_objects().values():
        if ddl.lstrip().lower().startswith("create table"):
            connection.execute(ddl)
    for ddl in task_run_entity_assembly_objects().values():
        if not ddl.lstrip().lower().startswith("create table"):
            connection.execute(ddl)
    connection.close()

    store = AutoReplyStore.__new__(AutoReplyStore)
    store.path = path
    store.busy_timeout_seconds = 5
    store.busy_timeout_milliseconds = 5000
    store._read_snapshot_connection = ContextVar(
        f"creator_read_snapshot_{id(store)}", default=None,
    )
    store._reused_connection = ContextVar(
        f"creator_reused_connection_{id(store)}", default=None,
    )
    return store


def _spec(message_id, *, business_key="", channel="dingtalk", **overrides):
    values = {
        "channel": channel,
        "conversation_id": "conversation",
        "conversation_title": "Conversation",
        "single_chat": False,
        "trigger_message_id": message_id,
        "trigger_create_time": "2026-10-22T12:00:00Z",
        "trigger_sender": "sender",
        "trigger_text": f"message {message_id}",
        "trigger_message_json": f'{{"id":"{message_id}"}}',
        "business_object_key": business_key,
    }
    values.update(overrides)
    return ReplyTaskSpec(**values)


def _creator_kwargs(spec):
    values = spec.model_dump()
    values.pop("error")
    return values


def test_reply_creator_uses_native_owners_and_returns_same_task_for_message_dedupe(tmp_path):
    store = _target_store(tmp_path / "target.sqlite3")

    assert store.enqueue_reply_task(**_creator_kwargs(_spec("enqueued-message"))) is True
    first = store.ensure_reply_task(**_creator_kwargs(_spec(
        "message-1", force_new_decision=True, manual_rerun_attempt_id=17,
    )))
    duplicate = store.ensure_reply_task(**_creator_kwargs(_spec("message-1", trigger_text="changed")))
    inserted = store.enqueue_reply_task(**_creator_kwargs(_spec("message-1")))

    assert duplicate.id == first.id
    assert duplicate.trigger_text == "message message-1"
    assert duplicate.force_new_decision is True
    assert duplicate.manual_rerun_attempt_id == 17
    assert inserted is False
    with store._connect() as db:
        assert db.execute("select kind from tasks where id=?", (first.id,)).fetchone()[0] == "reply"
        parameters = task_entity_storage.get_task(db, first.id)["parameters"]
        assert set(parameters) == {
            "force_new_decision", "manual_rerun_attempt_id",
            "manual_rerun_revision_key", "recovery_code",
        }
        assert parameters["manual_rerun_attempt_id"] == 17
        assert db.execute("select count(*) from task_inputs where task_id=?", (first.id,)).fetchone()[0] == 1
        assert db.execute("select count(*) from task_business_keys where task_id=?", (first.id,)).fetchone()[0] == 1


def test_business_key_reuses_task_and_advances_current_without_moving_claimed_input(tmp_path):
    store = _target_store(tmp_path / "target.sqlite3")
    first = store.ensure_reply_task(**_creator_kwargs(_spec("message-1", business_key="business-1")))
    with store._immediate_write_transaction() as db:
        task_entity_storage.associate_business_key(
            db, task_id=first.id, business_key="business-alias",
        )
        task_entity_storage.bind_claimed_input(
            db, task_id=first.id, input_id=first.current_input_id,
            claimed_input_version=first.input_version,
        )
        task_entity_storage.update_task_cas(
            db, task_id=first.id,
            expected={"status": "pending"},
            changes={"status": "processing"},
        )

    second = store.ensure_reply_task(**_creator_kwargs(_spec("message-2", business_key="business-1")))

    assert second.id == first.id
    assert second.trigger_message_id == "message-2"
    assert second.input_version == first.input_version + 1
    assert second.claimed_input_id == first.current_input_id
    assert second.current_input_id != second.claimed_input_id
    assert second.execution_generation == first.execution_generation
    assert second.status == "processing"
    with store._connect() as db:
        assert db.execute("select count(*) from task_inputs where task_id=?", (first.id,)).fetchone()[0] == 2
        assert [row[0] for row in db.execute("select business_key from task_business_keys where task_id=? order by id", (first.id,))] == ["business-1", "business-alias"]


def test_new_message_resets_terminal_generation_and_retry_state(tmp_path):
    store = _target_store(tmp_path / "target.sqlite3")
    first = store.ensure_reply_task(**_creator_kwargs(_spec("message-1", business_key="business-1")))
    with store._immediate_write_transaction() as db:
        task_entity_storage.update_task_cas(
            db,
            task_id=first.id,
            expected={"status": "pending"},
            changes={
                "status": "failed",
                "attempts": 3,
                "error": "previous failure",
                "locked_at": "old lock",
                "available_at": "old availability",
            },
        )

    revised = store.ensure_reply_task(**_creator_kwargs(_spec("message-2", business_key="business-1")))

    assert revised.id == first.id
    assert revised.status == "pending"
    assert revised.attempts == 0
    assert revised.error == ""
    assert revised.locked_at is None
    assert revised.available_at == ""
    assert revised.execution_generation != first.execution_generation
    assert revised.input_version == first.input_version + 1


def test_append_rejects_full_key_byte_conflict_and_different_owner(tmp_path):
    store = _target_store(tmp_path / "target.sqlite3")
    first = store.ensure_reply_task(**_creator_kwargs(_spec("owner-1", business_key="owner-key-1")))
    second = store.ensure_reply_task(**_creator_kwargs(_spec("owner-2", business_key="owner-key-2")))
    snapshot = {
        "channel": "dingtalk",
        "conversation_id": "revision-conversation",
        "conversation_title": "Revision",
        "single_chat": 0,
        "trigger_message_id": "revision-message",
        "trigger_create_time": "time",
        "trigger_sender": "sender",
        "trigger_text": "original bytes",
        "trigger_message_json": '{"raw":"original"}',
        "oa_url": "",
        "business_object_key": "revision-key",
    }
    with store._immediate_write_transaction() as db:
        input_id = store._append_reply_input_snapshot(
            db, first.id, snapshot, revision_key="revision-1",
        )
        assert input_id > 0
        with pytest.raises(ReplyTaskIdentityConflict, match="different bytes"):
            store._append_reply_input_snapshot(
                db, first.id, {**snapshot, "trigger_text": "changed bytes"},
                revision_key="revision-1",
            )
        with pytest.raises(ReplyTaskIdentityConflict, match="another task"):
            store._append_reply_input_snapshot(
                db, second.id, snapshot, revision_key="revision-1",
            )


def test_creator_keeps_distinct_channels_separate_and_oa_provisional_key_refines(tmp_path):
    store = _target_store(tmp_path / "target.sqlite3")
    first = store.ensure_reply_task(**_creator_kwargs(_spec("same-message", channel="dingtalk")))
    other_channel = store.ensure_reply_task(**_creator_kwargs(_spec("same-message", channel="wechat")))
    provisional = store.ensure_reply_task(**_creator_kwargs(_spec(
        "oa-first", business_key="oa:process-1:",
    )))
    refined = store.ensure_reply_task(**_creator_kwargs(_spec(
        "oa-second", business_key="oa:process-1:node-1",
    )))

    assert first.id != other_channel.id
    distinct_key = store.ensure_reply_task(**_creator_kwargs(_spec(
        "different-message", business_key="different-business", channel="dingtalk",
    )))
    assert distinct_key.id != first.id
    assert refined.id == provisional.id
    with store._connect() as db:
        assert [row[0] for row in db.execute(
            "select business_key from task_business_keys where task_id=? order by id",
            (provisional.id,),
        )] == ["oa:process-1:node-1"]
        assert db.execute("select count(*) from task_inputs where task_id=?", (provisional.id,)).fetchone()[0] == 2


def test_batch_creator_rolls_back_task_input_and_key_on_immutable_identity_conflict(tmp_path):
    store = _target_store(tmp_path / "target.sqlite3")
    conflicting = (
        _spec("one", business_key="batch-key"),
        _spec("one", business_key="batch-key", trigger_text="different bytes"),
    )

    with pytest.raises(ReplyTaskIdentityConflict, match="different immutable input"):
        store.ensure_reply_tasks(conflicting)

    with store._connect() as db:
        assert db.execute("select count(*) from tasks where kind='reply'").fetchone()[0] == 0
        assert db.execute("select count(*) from task_inputs").fetchone()[0] == 0
        assert db.execute("select count(*) from task_business_keys").fetchone()[0] == 0


def test_authorized_email_creator_checks_current_persisted_plan_on_target_owners(tmp_path):
    from app.email_canonical_storage import insert_email_classification, upsert_email_message
    from tests.test_email_canonical_storage import _classification, _message

    store = _target_store(tmp_path / "target.sqlite3")
    identity = "account:message-id:<creator@example.test>"
    with store._immediate_write_transaction() as db:
        upsert_email_message(db, _message(
            identity, uid=21, thread_identity="email-thread",
        ))
        classification_id = insert_email_classification(db, _classification(
            identity, id=101, uid=21, status="processed",
            current_action_plan_id="plan-current",
        ))

    result = store.ensure_authorized_email_reply_tasks(
        classification_id=classification_id,
        account_id="account",
        stable_message_identity=identity,
        thread_identity="email-thread",
        action_plan_id="plan-current",
        task_specs=(
            _spec("email-trigger-1", channel="email", conversation_id="email-thread"),
            _spec("email-trigger-2", channel="email", conversation_id="email-thread"),
        ),
    )

    assert len(result) == 2
    assert result[0].channel == "email"
    assert result[0].conversation_id == "email-thread"
    with pytest.raises(EmailReplyTaskAuthorizationConflict, match="current persisted authorization"):
        store.ensure_authorized_email_reply_tasks(
            classification_id=classification_id,
            account_id="account",
            stable_message_identity=identity,
            thread_identity="email-thread",
            action_plan_id="stale-plan",
            task_specs=(_spec("must-not-insert", channel="email", conversation_id="email-thread"),),
        )
    with store._connect() as db:
        assert db.execute("select count(*) from tasks where kind='reply'").fetchone()[0] == 2
