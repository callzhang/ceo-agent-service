from contextlib import contextmanager
import hashlib
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.store import AutoReplyStore
from app.email_store import EmailStore
from app.task_run_entity_assembly import task_run_entity_assembly_objects


@contextmanager
def _native_stores(path: Path):
    db = sqlite3.connect(path)
    db.execute("pragma foreign_keys=on")
    objects = task_run_entity_assembly_objects()
    for sql in objects.values():
        if sql.lstrip().lower().startswith("create table"):
            db.execute(sql)
    for sql in objects.values():
        if not sql.lstrip().lower().startswith("create table"):
            db.execute(sql)
    db.close()
    oa = AutoReplyStore.__new__(AutoReplyStore)
    oa.path = path
    email = EmailStore.__new__(EmailStore)
    email.path = path
    def connect():
        @contextmanager
        def opened():
            connection = sqlite3.connect(path)
            connection.row_factory = sqlite3.Row
            try:
                with connection:
                    yield connection
            finally:
                connection.close()
        return opened()
    oa._connect = connect
    email._connect = connect
    email._now = lambda: "2026-10-23T12:00:00+00:00"
    yield oa, email


def _oa_event(store, **overrides):
    values = {
        "kind": "chat_reminder", "process_instance_id": "proc-1", "task_id": "task-5",
        "channel": "dingtalk", "conversation_id": "conv", "conversation_title": "Title",
        "trigger_message_id": "msg", "trigger_create_time": "2026-10-22T10:00:00+08:00",
        "trigger_sender": "sender", "trigger_text": "remind", "trigger_message_json": "{}",
        "oa_url": "https://example.test/task",
    }
    values.update(overrides)
    return store.record_oa_notification_event(**values)


def _candidate(identity, uid, text="body", *, account="a", folder="INBOX", uidvalidity=3):
    message = {
        "accountId": account, "folder": folder, "stableMessageIdentity": identity,
        "uidValidity": uidvalidity, "uid": uid, "subject": f"subject-{uid}",
        "sender": {"name": "Sender", "address": "s@example.test"},
        "to_recipients": [], "cc_recipients": [], "date": "2026-10-20T10:00:00Z",
        "text": text, "headers": {}, "attachments": [],
    }
    return SimpleNamespace(stable_message_identity=identity, normalized_text=text,
                           provider_message=message)


def _enqueue(store, candidates, *, account="a", folder="INBOX", model="m", validity=3, cursor=0):
    store.enqueue_historical_page(account_id=account, folder=folder, model_id=model,
                                  uidvalidity=validity, last_seen_uid=cursor, candidates=candidates)


def test_oa_direct_owner_first_write_wins_and_claim_attempt_cas(tmp_path):
    with _native_stores(tmp_path / "native.sqlite") as (store, _email):
        event_id = _oa_event(store)
        assert _oa_event(store, task_id="task-changed", trigger_text="changed") == event_id
        with store._connect() as db:
            row = dict(db.execute("select * from oa_notification_events where id=?", (event_id,)).fetchone())
        assert row["task_id"] == "task-5"
        assert row["trigger_text"] == "remind"
        assert row["status"] == "pending"
        assert store.claim_oa_reminder_target(event_id, 71)
        assert not store.claim_oa_reminder_target(event_id, 72)
        assert not store.mark_oa_reminder_sent(event_id, 72, 900)
        assert store.mark_oa_reminder_failed(event_id, 71, "temporary")
        assert store.claim_oa_reminder_target(event_id, 73)
        assert store.mark_oa_reminder_sent(event_id, 73, 901)
        assert not store.mark_oa_reminder_sent(event_id, 73, 901)
        with store._connect() as db:
            row = dict(db.execute("select * from oa_notification_events where id=?", (event_id,)).fetchone())
        assert (row["status"], row["claimed_attempt_id"], row["result_attempt_id"]) == ("sent", 73, 901)


def test_oa_adoption_matches_process_and_keeps_provider_task_text_opaque(tmp_path):
    with _native_stores(tmp_path / "native.sqlite") as (store, _email):
        first = _oa_event(store, task_id="", trigger_message_id="one")
        other = _oa_event(store, process_instance_id="other", task_id="", trigger_message_id="two")
        system = _oa_event(store, kind="system_notification", process_instance_id="proc-1",
                           task_id="", trigger_message_id="three")
        assert store.adopt_oa_notification_events("proc-1", "task-5") == 1
        pending = store.list_pending_oa_reminder_targets("proc-1", "task-5")
        assert [row["id"] for row in pending] == [first]
        assert pending[0]["task_id"] == "task-5"
        with store._connect() as db:
            assert db.execute("select status,requires_reply from oa_notification_events where id=?", (system,)).fetchone()[:] == ("ignored", 0)
            assert db.execute("select task_id from oa_notification_events where id=?", (other,)).fetchone()[0] == ""


def test_email_four_part_identity_refresh_preserves_retry_and_cursor_epoch(tmp_path):
    with _native_stores(tmp_path / "native.sqlite") as (_store, email):
        _enqueue(email, [_candidate("same", 4, "old")], cursor=9)
        email.set_historical_candidate_state(account_id="a", folder="INBOX", model_id="m",
                                            stable_message_identity="same", state="deferred", reason="rate")
        with email._connect() as db:
            before = dict(db.execute("select * from email_historical_candidates").fetchone())
        _enqueue(email, [_candidate("same", 8, "new")], cursor=7)
        with email._connect() as db:
            after = dict(db.execute("select * from email_historical_candidates").fetchone())
        assert after["normalized_text"] == hashlib.sha256(b"new").hexdigest()
        assert json.loads(after["provider_message_json"])["uid"] == 8
        assert (after["state"], after["reason"], after["attempted_at"], after["next_retry_at"]) == (
            before["state"], before["reason"], before["attempted_at"], before["next_retry_at"])
        assert email.historical_traversal_cursor(account_id="a", folder="INBOX", model_id="m") == {
            "uidvalidity": 3, "last_seen_uid": 9}
        _enqueue(email, [_candidate("same", 2, "epoch", uidvalidity=4)], validity=4, cursor=2)
        assert email.historical_traversal_cursor(account_id="a", folder="INBOX", model_id="m") == {
            "uidvalidity": 4, "last_seen_uid": 2}
        assert email.list_historical_candidates(account_id="a", folder="INBOX", model_id="other") == []
        assert email.list_historical_candidates(account_id="a", folder="Archive", model_id="m") == []


def test_email_page_bad_candidate_rolls_back_rows_and_cursor(tmp_path):
    with _native_stores(tmp_path / "native.sqlite") as (_store, email):
        good = _candidate("good", 1)
        bad = SimpleNamespace(stable_message_identity="bad", normalized_text="x", provider_message=None)
        with pytest.raises(ValueError, match="message is missing"):
            _enqueue(email, [good, bad], cursor=1)
        with email._connect() as db:
            assert db.execute("select count(*) from email_historical_candidates").fetchone()[0] == 0
        assert email.historical_traversal_cursor(account_id="a", folder="INBOX", model_id="m") == {
            "uidvalidity": None, "last_seen_uid": 0}


def test_email_deferred_quota_selection_and_read_is_idempotent(tmp_path):
    with _native_stores(tmp_path / "native.sqlite") as (_store, email):
        candidates = [_candidate(f"id-{n}", n) for n in range(1, 6)]
        _enqueue(email, candidates, cursor=5)
        email.set_historical_candidate_state(account_id="a", folder="INBOX", model_id="m",
                                            stable_message_identity="id-1", state="deferred", reason="r1")
        email.set_historical_candidate_state(account_id="a", folder="INBOX", model_id="m",
                                            stable_message_identity="id-2", state="deferred", reason="r2")
        email.set_historical_candidate_state(account_id="a", folder="INBOX", model_id="m",
                                            stable_message_identity="id-3", state="deferred", reason="r3")
        email._now = lambda: "2099-01-01T00:00:00+00:00"
        first = email.list_historical_candidates(account_id="a", folder="INBOX", model_id="m", limit=3)
        second = email.list_historical_candidates(account_id="a", folder="INBOX", model_id="m", limit=3)
        assert [row["stable_message_identity"] for row in first] == ["id-1", "id-2", "id-4"]
        assert second == first
        with pytest.raises(ValueError, match="positive"):
            email.list_historical_candidates(account_id="a", folder="INBOX", model_id="m", limit=True)
        with pytest.raises(ValueError, match="positive"):
            email.list_historical_candidates(account_id="a", folder="INBOX", model_id="m", limit=0)


def test_domain_leaf_facts_survive_current_inverse_and_reforward():
    from app.storage_payload import TASK_RECORD_TYPES
    from app.task_entity_schema import capture_task_entities, migrate_task_entities, restore_task_entities
    from app.task_lifecycle_storage import source_insert
    from app.state_storage import create_state_schema
    from app.task20_definitions import task20_projection
    from tests.test_task20_definitions import payload
    from tests.test_task_entity_schema import _source

    db = _source()
    db.row_factory = sqlite3.Row
    create_state_schema(db)
    for kind, physical_id, updates in (
        ("oa_notification_events", 11001, {
            "event_kind": "chat_reminder", "requires_reply": 1,
            "process_instance_id": "proc-portable", "task_id": "task-provider-5",
            "channel": "dingtalk", "conversation_id": "conv-portable",
            "conversation_title": "Title", "trigger_message_id": "msg-portable",
            "status": "pending", "trigger_text": "remind", "trigger_message_json": "{\"provider\":true}",
        }),
        ("email_historical_candidates", 11002, {
            "account_id": "account-portable", "folder": "INBOX", "model_id": "model-portable",
            "stable_message_identity": "identity-portable", "normalized_text": "hash-before",
            "provider_message_json": "{\"uid\":7}", "uidvalidity": 3, "uid": 7,
            "state": "deferred", "reason": "rate", "attempted_at": "attempted",
            "next_retry_at": "retry", "updated_at": "updated",
        }),
    ):
        values = payload(db, kind)
        values.update(updates)
        columns, expressions, parameters = source_insert(kind, values, {})
        db.execute(
            'insert into tasks("_kind","id","legacy_key","parent_id",'
            + ",".join('"' + column + '"' for column in columns)
            + ") values(" + ",".join((str(TASK_RECORD_TYPES.code(kind)), "?", "?", "?", *expressions)) + ")",
            (physical_id, str(physical_id), None, *parameters),
        )
    db.commit()
    db.execute("pragma foreign_keys=off")
    db.execute("begin immediate")
    source = capture_task_entities(db)
    forward = migrate_task_entities(db, source)
    db.commit()

    @contextmanager
    def same_db():
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise

    oa = AutoReplyStore.__new__(AutoReplyStore)
    oa.path = "portable-test"
    oa._connect = same_db
    # The real Store transaction wrapper owns the BEGIN IMMEDIATE boundary.
    event_id = oa.record_oa_notification_event(
        kind="chat_reminder", process_instance_id="proc-portable", task_id="task-provider-5",
        channel="dingtalk", conversation_id="conv-portable", conversation_title="Title",
        trigger_message_id="msg-portable", trigger_create_time="", trigger_sender="",
        trigger_text="remind", trigger_message_json='{"provider":true}', oa_url="",
    )
    assert event_id == 11001
    assert oa.claim_oa_reminder_target(event_id, 701)
    assert oa.mark_oa_reminder_sent(event_id, 701, 991)
    email = EmailStore.__new__(EmailStore)
    email.path = "portable-test"
    email._connect = same_db
    email._now = lambda: "2099-01-01T00:00:00+00:00"
    _enqueue(email, [_candidate("identity-portable", 8, "new", account="account-portable")], account="account-portable",
             model="model-portable", validity=3, cursor=8)
    native_before = tuple(tuple(row) for row in db.execute(
        "select * from oa_notification_events where id=11001")) + tuple(tuple(row) for row in db.execute(
        "select * from email_historical_candidates where account_id='account-portable'"))

    db.execute("pragma foreign_keys=off")
    db.execute("begin immediate")
    inverse = restore_task_entities(db, source, forward)
    oa_source = tuple(db.execute(
        "select id,task_id,status,claimed_attempt_id,result_attempt_id,trigger_text "
        f"from ({task20_projection('oa_notification_events')}) where id=11001"
    ).fetchone())
    email_source = tuple(db.execute(
        "select account_id,folder,model_id,stable_message_identity,normalized_text,state,reason,"
        "attempted_at,next_retry_at,uid from (" + task20_projection("email_historical_candidates") + ") "
        "where account_id='account-portable'"
    ).fetchone())
    assert oa_source == (11001, "task-provider-5", "sent", 701, 991, "remind")
    assert email_source == ("account-portable", "INBOX", "model-portable", "identity-portable",
                            hashlib.sha256(b"new").hexdigest(), "deferred", "rate", "attempted", "retry", 8)
    portable_source_context = json.loads(json.dumps(inverse["source_context"]))
    portable_identity = json.loads(json.dumps(inverse["inverse_identity"]))
    db.commit()
    db.execute("begin immediate")
    second_source = capture_task_entities(db)
    migrate_task_entities(db, second_source, source_context=portable_source_context,
                          inverse_identity=portable_identity)
    db.commit()
    native_after = tuple(tuple(row) for row in db.execute(
        "select * from oa_notification_events where id=11001")) + tuple(tuple(row) for row in db.execute(
        "select * from email_historical_candidates where account_id='account-portable'"))
    assert native_after == native_before
    db.close()
