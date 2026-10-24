from contextlib import contextmanager
import hashlib
import json
import sqlite3
from pathlib import Path
from threading import current_thread
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.store import AutoReplyStore
from app.email_store import EmailStore, _historical_candidate_list_item
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


def _existing_oa_store(path: Path):
    store = AutoReplyStore.__new__(AutoReplyStore)
    store.path = path
    @contextmanager
    def connect():
        connection = sqlite3.connect(path, timeout=5)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()
    store._connect = connect
    return store


def _existing_email_store(path: Path, now="2026-10-23T12:00:00+00:00"):
    store = EmailStore.__new__(EmailStore)
    store.path = path
    @contextmanager
    def connect():
        connection = sqlite3.connect(path, timeout=5)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()
    store._connect = connect
    store._now = lambda: now
    return store


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


def test_oa_independent_connections_contend_for_one_claim(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from threading import Condition
    path = tmp_path / "oa-native-compete.sqlite"
    with _native_stores(path) as (first, _email):
        event_id = _oa_event(first, trigger_message_id="contended")
        second = _existing_oa_store(path)
        condition = Condition()
        begin_attempts = []

        def instrument(store):
            original_connect = store._connect

            class TracedConnection:
                def __init__(self, connection):
                    self.connection = connection

                def __getattr__(self, name):
                    return getattr(self.connection, name)

                def execute(self, statement, *parameters):
                    if statement.strip().casefold() == "begin immediate":
                        with condition:
                            begin_attempts.append(current_thread().name)
                            condition.notify_all()
                    return self.connection.execute(statement, *parameters)

            @contextmanager
            def traced_connect():
                with original_connect() as db:
                    yield TracedConnection(db)

            monkeypatch.setattr(store, "_connect", traced_connect)

        instrument(first)
        instrument(second)
        blocker = sqlite3.connect(path, timeout=5, isolation_level=None)
        blocker.execute("begin immediate")
        blocker_held = True
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [
                    pool.submit(first.claim_oa_reminder_target, event_id, 801),
                    pool.submit(second.claim_oa_reminder_target, event_id, 802),
                ]
                with condition:
                    both_attempted = condition.wait_for(lambda: len(begin_attempts) == 2, timeout=5)
                if both_attempted:
                    assert len(set(begin_attempts)) == 2
                    assert not any(future.done() for future in futures)
                blocker.execute("rollback")
                blocker.close()
                blocker_held = False
        finally:
            if blocker_held:
                blocker.execute("rollback")
                blocker.close()

        assert both_attempted, f"both independent claims must reach BEGIN IMMEDIATE: {begin_attempts}"
        assert sorted(future.result() for future in futures) == [False, True]
        with first._connect() as db:
            row = db.execute(
                "select status,claimed_attempt_id from oa_notification_events where id=?", (event_id,)
            ).fetchone()
        assert tuple(row) in {("sending", 801), ("sending", 802)}


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
        assert "uid" not in json.loads(after["provider_message_json"])
        assert _historical_candidate_list_item(after)["provider_message"]["uid"] == 8
        assert (after["state"], after["reason"], after["attempted_at"], after["next_retry_at"]) == (
            before["state"], before["reason"], before["attempted_at"], before["next_retry_at"])
        assert email.historical_traversal_cursor(account_id="a", folder="INBOX", model_id="m") == {
            "uidvalidity": 3, "last_seen_uid": 9}
        _enqueue(email, [_candidate("same", 2, "epoch", uidvalidity=4)], validity=4, cursor=2)
        assert email.historical_traversal_cursor(account_id="a", folder="INBOX", model_id="m") == {
            "uidvalidity": 4, "last_seen_uid": 2}
        assert email.list_historical_candidates(account_id="a", folder="INBOX", model_id="other") == []
        assert email.list_historical_candidates(account_id="a", folder="Archive", model_id="m") == []


def test_email_all_four_identity_dimensions_isolate_updates_and_lists(tmp_path):
    path = tmp_path / "email-four-part-identity.sqlite"
    with _native_stores(path) as (_store, email):
        records = [
            ("a", "INBOX", "m", "same", 1),
            ("b", "INBOX", "m", "same", 2),
            ("a", "Archive", "m", "same", 3),
            ("a", "INBOX", "other-model", "same", 4),
            ("a", "INBOX", "m", "other-identity", 5),
        ]
        for account, folder, model, identity, uid in records:
            _enqueue(
                email,
                [_candidate(identity, uid, account=account, folder=folder)],
                account=account,
                folder=folder,
                model=model,
                cursor=uid,
            )

        for account, folder, model, identity, reason in (
            ("b", "INBOX", "m", "same", "account-only"),
            ("a", "Archive", "m", "same", "folder-only"),
            ("a", "INBOX", "other-model", "same", "model-only"),
            ("a", "INBOX", "m", "other-identity", "identity-only"),
        ):
            email.set_historical_candidate_state(
                account_id=account,
                folder=folder,
                model_id=model,
                stable_message_identity=identity,
                state="deferred",
                reason=reason,
            )

        email._now = lambda: "2099-01-01T00:00:00+00:00"
        with email._connect() as db:
            assert db.execute("select count(*) from email_historical_candidates").fetchone()[0] == 5
        assert [row["stable_message_identity"] for row in email.list_historical_candidates(
            account_id="b", folder="INBOX", model_id="m"
        )] == ["same"]
        assert [row["stable_message_identity"] for row in email.list_historical_candidates(
            account_id="a", folder="Archive", model_id="m"
        )] == ["same"]
        assert [row["stable_message_identity"] for row in email.list_historical_candidates(
            account_id="a", folder="INBOX", model_id="other-model"
        )] == ["same"]
        base = email.list_historical_candidates(account_id="a", folder="INBOX", model_id="m")
        assert [(row["stable_message_identity"], row["reason"]) for row in base] == [
            ("other-identity", "identity-only"), ("same", ""),
        ]
        email.set_historical_candidate_state(
            account_id="b", folder="INBOX", model_id="m", stable_message_identity="same",
            state="terminal", reason="account-only-terminal",
        )
        assert email.list_historical_candidates(account_id="b", folder="INBOX", model_id="m") == []
        base = email.list_historical_candidates(account_id="a", folder="INBOX", model_id="m")
        assert [(row["stable_message_identity"], row["state"]) for row in base] == [
            ("other-identity", "deferred"), ("same", "pending"),
        ]


def test_email_deferred_retry_survives_reopen_and_missing_identity_errors(tmp_path):
    path = tmp_path / "email-deferred-reopen.sqlite"
    with _native_stores(path) as (_store, email):
        _enqueue(email, [_candidate("retry-me", 1)], cursor=1)
        email.set_historical_candidate_state(
            account_id="a", folder="INBOX", model_id="m", stable_message_identity="retry-me",
            state="deferred", reason="provider-quota",
        )
        with email._connect() as db:
            saved = dict(db.execute("select * from email_historical_candidates").fetchone())

    before_due = _existing_email_store(path, now="2026-10-23T12:00:00+00:00")
    assert before_due.list_historical_candidates(account_id="a", folder="INBOX", model_id="m") == []
    restarted = _existing_email_store(path, now="2099-01-01T00:00:00+00:00")
    [retry] = restarted.list_historical_candidates(account_id="a", folder="INBOX", model_id="m")
    assert (retry["state"], retry["reason"], retry["attempted_at"], retry["next_retry_at"]) == (
        "deferred", "provider-quota", saved["attempted_at"], saved["next_retry_at"]
    )
    with pytest.raises(ValueError, match="historical queued candidate is missing"):
        restarted.set_historical_candidate_state(
            account_id="a", folder="INBOX", model_id="m", stable_message_identity="absent",
            state="terminal", reason="missing",
        )


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
