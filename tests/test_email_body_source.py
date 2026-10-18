"""Exact original references preserve recoverable Email bodies without copies."""

from hashlib import sha256
import json

import pytest

from app.email_canonical_storage import (
    capture_email_canonical_source, migrate_email_canonical_schema,
    restore_email_canonical_schema,
)
from tests.test_email_canonical_storage import current13 as current13, _db, _insert, _message, _classification


def test_email_original_reference_has_one_nullable_owner_column(tmp_path):
    from app.store import AutoReplyStore

    store = AutoReplyStore(tmp_path / "fresh.sqlite3")
    with store._connect() as db:
        fields = {row[1]: row for row in db.execute("pragma table_info(emails)")}
    assert "message_source_reference_json" in fields
    assert fields["message_source_reference_json"][3] == 0


def _retired_canonical(db):
    original = "完整原文🙂\nwith exact whitespace  "
    _insert(db, "email_messages", _message(normalized_text=original))
    _insert(db, "email_classifications", _classification(status="processed"))
    db.commit()
    metadata = capture_email_canonical_source(db)
    migrate_email_canonical_schema(db, metadata)
    reference = {"kind": "provider", "representation": "provider_body",
                 "sha256": sha256(original.encode()).hexdigest(), "utf8_bytes": len(original.encode()),
                 "locator": {"folder": " INBOX ", "uidvalidity": 10, "uid": 20,
                             "rfc_message_id": "<message>"}}
    db.execute("update emails set message_normalized_text='',message_source_reference_json=?",
               (json.dumps(reference),))
    db.commit()
    return original, reference, metadata


def test_inverse_requires_exact_original_without_rewinding_current_facts(tmp_path, current13):
    db = _db(tmp_path, current13)
    original, reference, metadata = _retired_canonical(db)
    db.execute("update emails set class_subject='current business metadata'")
    calls = []

    def read_body(row, ref):
        calls.append((row["stable_message_identity"], ref))
        return original

    restore_email_canonical_schema(db, metadata, read_body=read_body)
    assert calls == [("stable-1", reference)]
    assert db.execute("select normalized_text from email_messages").fetchone() == (original,)
    assert db.execute("select subject from email_classifications").fetchone() == ("current business metadata",)
    assert db.execute("pragma quick_check").fetchone() == ("ok",)
    db.close()


@pytest.mark.parametrize("original_available", [False, True])
def test_inverse_rejects_missing_or_changed_original_before_mutation(tmp_path, current13, original_available):
    db = _db(tmp_path, current13)
    _, _, metadata = _retired_canonical(db)
    before = tuple(db.iterdump())
    kwargs = {"read_body": lambda row, ref: "different original"} if original_available else {}
    with pytest.raises(ValueError, match="original"):
        restore_email_canonical_schema(db, metadata, **kwargs)
    assert tuple(db.iterdump()) == before
    db.close()


def _provider_message(body):
    return {"accountId": "account", "stableMessageIdentity": "stable-1", "folder": "Archive",
            "uidValidity": 11, "uid": 21, "messageId": "<message>",
            "from": {"email": "sender@example.test"}, "subject": "Subject", "textBody": body}


def test_native_original_is_proved_before_any_provider_read(tmp_path, monkeypatch):
    from app import native_trajectory as native
    from app.email_body_source import locate_original

    body = "完整原文🙂\n  exact whitespace"
    path = tmp_path / "native.jsonl"
    path.write_text(json.dumps({"content": json.dumps({"body": body}, ensure_ascii=False)}) + "\n")
    monkeypatch.setattr(native, "find_codex_session_path", lambda *a, **kw: path)

    def forbidden():
        raise AssertionError("native source must be checked first")

    reference = locate_original(body, attempts=[dict(runtime_kind="codex_cli", session_id="session",
                                transcript_start=0, transcript_end=1)], read_message=forbidden)
    assert reference["kind"] == "native"
    assert body not in json.dumps(reference, ensure_ascii=False)
    assert native.read_native_source_body(reference["native_provenance"]) == body


@pytest.mark.parametrize("representation", ["provider_body", "scan_normalized_message"])
def test_complete_provider_original_gets_body_free_exact_reference(representation):
    from app.email_body_source import locate_original, original_from_message
    from app.email_classifier_scan import _normalized_message_text

    message = _provider_message("Full original  \n尾部🙂")
    body = message["textBody"] if representation == "provider_body" else _normalized_message_text(message)
    reference = locate_original(body, attempts=[], read_message=lambda: message)
    assert reference["kind"] == "provider" and reference["representation"] == representation
    assert reference["locator"] == {"folder": "Archive", "uidvalidity": 11, "uid": 21,
                                    "rfc_message_id": "<message>"}
    assert body not in json.dumps(reference, ensure_ascii=False)
    assert original_from_message(message, reference) == body
    with pytest.raises(ValueError, match="original"):
        original_from_message(_provider_message("changed version"), reference)


def test_partial_or_unavailable_provider_original_is_not_a_source_reference():
    from app.email_body_source import locate_original

    assert locate_original("full original", attempts=[], read_message=lambda: _provider_message("full")) is None

    def missing():
        raise LookupError("exact source unavailable")

    assert locate_original("full original", attempts=[], read_message=missing) is None


def test_source_reader_programming_error_remains_visible():
    from app.email_body_source import locate_original

    def broken():
        raise RuntimeError("reader bug")

    with pytest.raises(RuntimeError, match="reader bug"):
        locate_original("full original", attempts=[], read_message=broken)


@pytest.mark.parametrize("backup_matches", [True, False])
def test_recovery_uses_exact_backup_and_reads_new_original_without_rewinding_rows(tmp_path, current13, backup_matches):
    import sqlite3
    from app.email_body_source import prepare_recovery_originals

    db = _db(tmp_path, current13)
    original, reference, metadata = _retired_canonical(db)
    backup = tmp_path / "source-backup.sqlite3"
    with sqlite3.connect(backup) as source:
        source.execute("create table email_messages(account_id text,stable_message_identity text,normalized_text text)")
        source.execute("insert into email_messages values(?,?,?)", ("account", "stable-1",
                       original if backup_matches else "older source version"))
    body2 = "new post-migration original"
    ref2 = reference | {"sha256": sha256(body2.encode()).hexdigest(), "utf8_bytes": len(body2.encode())}
    # This row did not exist at the backup's point in time.
    from app.email_canonical_storage import insert_email_message
    insert_email_message(db, _message("stable-new", normalized_text=""))
    db.execute("update emails set message_source_reference_json=? where stable_message_identity='stable-new'",
               (json.dumps(ref2),))
    db.commit()
    seen = []

    def read_message(account, identity):
        seen.append((account, identity))
        return _provider_message(original if identity == "stable-1" else body2) | {"stableMessageIdentity": identity}

    before = db.execute("select count(*) from emails").fetchone()[0]
    prepared = prepare_recovery_originals(tmp_path / "email.sqlite3", backup_path=backup,
                                          read_message=read_message)
    assert sorted(seen) == ([("account", "stable-new")] if backup_matches else
                            [("account", "stable-1"), ("account", "stable-new")])
    assert prepared == {("account", "stable-1"): original, ("account", "stable-new"): body2}
    assert db.execute("select count(*) from emails").fetchone()[0] == before
    assert db.execute("select sum(length(message_normalized_text)) from emails").fetchone() == (0,)
    restore_email_canonical_schema(db, metadata,
        read_body=lambda row, ref: prepared[(row["account_id"], row["stable_message_identity"])])
    assert db.execute("select normalized_text from email_messages where stable_message_identity='stable-new'").fetchone() == (body2,)
    assert db.execute("select count(*) from email_messages").fetchone() == (2,)
    db.close()


@pytest.fixture
def body_store(tmp_path):
    from app.email_store import EmailStore
    from app.email_canonical_storage import upsert_email_message, insert_email_classification

    store = EmailStore(tmp_path / "body-store.sqlite3")
    with store._connect() as db:
        db.execute("begin immediate")
        upsert_email_message(db, _message(normalized_text="complete body"))
        insert_email_classification(db, _classification(status="processed", legacy_processed_without_plan=1))
    return store


def test_terminal_body_retirement_changes_only_original_and_inline_reference(body_store):
    from types import SimpleNamespace

    store = body_store
    with store._connect() as db:
        before = dict(db.execute("select * from emails").fetchone())
    reads = []

    def read(account, identity):
        reads.append((account, identity))
        return _provider_message("complete body")

    assert store.compact_email_message_body(101, source_reader=SimpleNamespace(read=read))
    with store._connect() as db:
        after = dict(db.execute("select * from emails").fetchone())
    assert reads == [("account", "stable-1")]
    assert after["message_normalized_text"] == ""
    ref = json.loads(after["message_source_reference_json"])
    assert ref["sha256"] == sha256(b"complete body").hexdigest()
    assert after | {key: before[key] for key in ("message_normalized_text", "message_source_reference_json")} == before
    assert not store.compact_email_message_body(101, source_reader=SimpleNamespace(read=read))
    assert len(reads) == 1


def test_pending_feedback_original_is_not_retired(body_store):
    from types import SimpleNamespace

    with body_store._connect() as db:
        db.execute("update emails set class_status='pending_feedback'")
    assert not body_store.compact_email_message_body(101,
        source_reader=SimpleNamespace(read=lambda *args: pytest.fail("active input must remain")))


def test_retirement_source_version_or_status_change_is_not_overwritten(body_store):
    from types import SimpleNamespace

    def read(account, identity):
        with body_store._connect() as db:
            db.execute("update emails set class_status='pending_feedback',message_normalized_text='new input'")
        return _provider_message("complete body")

    assert not body_store.compact_email_message_body(101, source_reader=SimpleNamespace(read=read))
    with body_store._connect() as db:
        row = db.execute("select message_normalized_text,message_source_reference_json from emails").fetchone()
    assert tuple(row) == ("new input", None)


def test_missing_original_does_not_retire_terminal_body(body_store):
    from types import SimpleNamespace

    def missing(*args):
        raise LookupError("source missing")

    assert not body_store.compact_email_message_body(101, source_reader=SimpleNamespace(read=missing))
    with body_store._connect() as db:
        assert tuple(db.execute("select message_normalized_text,message_source_reference_json from emails").fetchone()) == ("complete body", None)


@pytest.mark.parametrize("status", ["pending", "running", "failed"])
def test_unfinished_classification_task_keeps_original(body_store, status):
    from types import SimpleNamespace
    from app.task12_storage import insert_task12

    with body_store._connect() as db:
        db.execute("begin immediate")
        insert_task12(db, "email_agent_classification_tasks", {
            "task_id": "classification-1", "channel": "email", "stable_message_identity": "stable-1",
            "status": status, "input_json": "{}",
        })
    assert not body_store.compact_email_message_body(101,
        source_reader=SimpleNamespace(read=lambda *args: pytest.fail("unfinished task needs input")))


def test_pending_reply_task_keeps_original(body_store):
    from types import SimpleNamespace
    from app.store import AutoReplyStore

    tasks = AutoReplyStore(body_store.path)
    tasks.ensure_reply_task(channel="email", conversation_id="email:account:stable-1",
        conversation_title="Email task", single_chat=False, trigger_message_id="reply-1",
        trigger_create_time="", trigger_sender="sender", trigger_text="reply",
        trigger_message_json=json.dumps({"classification_id": 101}))
    assert not body_store.compact_email_message_body(101,
        source_reader=SimpleNamespace(read=lambda *args: pytest.fail("pending action task needs input")))


@pytest.mark.parametrize("kind,status", [
    ("email_actions", "pending"), ("email_actions", "failed"),
    ("email_reply_dispatch_claims", "uncertain"), ("email_unsubscribe_claims", "awaiting_audit"),
])
def test_unfinished_mailbox_or_delivery_operation_keeps_original(body_store, kind, status):
    from types import SimpleNamespace
    from app.action_storage import insert_email_action
    from tests.test_email_action_storage import _cases

    cases = _cases()
    with body_store._connect() as db:
        db.execute("begin immediate")
        insert_email_action(db, "email_action_plans", cases["email_action_plans"] | {"classification_id": 101})
        db.execute("update emails set class_current_action_plan_id='plan',class_legacy_processed_without_plan=0")
        insert_email_action(db, kind, cases[kind] | {"classification_id": 101, "status": status})
    assert not body_store.compact_email_message_body(101,
        source_reader=SimpleNamespace(read=lambda *args: pytest.fail("unfinished operation needs input")))
