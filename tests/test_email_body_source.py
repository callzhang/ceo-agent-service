"""Exact original references preserve recoverable Email bodies without copies."""

from hashlib import sha256
import json

import pytest

from tests.support.email_native_fixtures import _message, _classification, mark_processed_with_plan


def test_email_original_reference_has_one_nullable_owner_column(tmp_path):
    from app.store import AutoReplyStore

    store = AutoReplyStore(tmp_path / "fresh.sqlite3")
    with store._connect() as db:
        fields = {row[1]: row for row in db.execute("pragma table_info(emails)")}
    assert "message_source_reference_json" in fields
    assert fields["message_source_reference_json"][3] == 0


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


@pytest.fixture
def body_store(tmp_path):
    from app.email_store import EmailStore
    from app.email_canonical_storage import upsert_email_message, insert_email_classification

    store = EmailStore(tmp_path / "body-store.sqlite3")
    with store._connect() as db:
        db.execute("begin immediate")
        upsert_email_message(db, _message(normalized_text="complete body"))
        insert_email_classification(db, _classification())
        mark_processed_with_plan(db, 101)
    return store


def test_terminal_body_retirement_preserves_facts_and_one_bounded_preview(body_store):
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
    assert after["message_preview"] == "complete body"
    assert after | {key: before[key] for key in (
        "message_normalized_text", "message_source_reference_json", "message_preview"
    )} == before
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

    with body_store._connect() as db:
        db.execute("begin immediate")
        task_id = db.execute(
            "insert into tasks(kind,status,parameters_json,created_at,updated_at) "
            "values('email_classification',?,jsonb('{}'),'2026-10-25','2026-10-25')",
            (status,),
        ).lastrowid
        db.execute(
            "insert into email_classification_inputs"
            "(task_id,stable_message_identity,input_json) values(?,?,?)",
            (task_id, "stable-1", "{}"),
        )
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


@pytest.mark.parametrize("classification_id", [101, 999])
@pytest.mark.parametrize("status", ["done", "pending", "processing"])
def test_compacted_task_keeps_its_body_retirement_dependency(body_store, classification_id, status):
    from types import SimpleNamespace
    from app.store import AutoReplyStore

    tasks = AutoReplyStore(body_store.path)
    task = tasks.ensure_reply_task(
        channel="email", conversation_id="email:completed", conversation_title="Email task",
        single_chat=False, trigger_message_id="completed", trigger_create_time="",
        trigger_sender="sender", trigger_text="reply",
        trigger_message_json=json.dumps({"classification_id": classification_id}),
    )
    assert [item.id for item in tasks.claim_reply_tasks(1, channel="email")] == [task.id]
    tasks.complete_reply_task(task.id, expected_execution_generation=task.execution_generation)
    with tasks._connect() as db:
        compacted = db.execute(
            "select input_compacted,trigger_message_json from task_inputs where task_id=?",
            (task.id,),
        ).fetchone()
        assert tuple(compacted) == (1, "")
    if status != "done":
        from app.task_entity_storage import update_task_cas

        with tasks._connect() as db:
            update_task_cas(db, task_id=task.id, expected={"status": "done"},
                            changes={"status": status})
    should_compact = status == "done" or classification_id != 101

    def read(*args):
        assert should_compact, "unfinished task still requires the original"
        return _provider_message("complete body")
    assert body_store.compact_email_message_body(
        101, source_reader=SimpleNamespace(read=read),
    ) is should_compact


def _processed_store_with_operation(tmp_path, kind):
    """A processed message whose only open work is one operation of ``kind``."""
    from app.email_classifier_contracts import EmailAction, EmailClassificationStatus
    from app.email_store import EmailStore
    from tests.test_email_store import _classification, _persist_scan

    store = EmailStore(tmp_path / "operation.sqlite3")
    action = {
        "reply_dispatch": EmailAction.AUTO_REPLY,
        "unsubscribe_claim": EmailAction.UNSUBSCRIBE,
    }.get(kind, EmailAction.MOVE)
    parameters = (
        {EmailAction.AUTO_REPLY: {"instruction": "Acknowledge receipt."}}
        if action is EmailAction.AUTO_REPLY
        else {EmailAction.UNSUBSCRIBE: {}}
        if action is EmailAction.UNSUBSCRIBE
        else {EmailAction.MOVE: {"target_folder": "Projects"}}
    )
    classification = _classification(
        status=EmailClassificationStatus.PROCESSED, message_id="operation",
        actions=(action,), action_parameters=parameters, thread_id="thread-unsubscribe-41" if kind == "unsubscribe_claim" else "thread-operation",
    )
    _persist_scan(store, classification, normalized_text="complete body")
    message = _provider_message("complete body") | {
        "accountId": "dingtalk-account",
        "stableMessageIdentity": classification.stable_message_identity,
        "folder": "INBOX", "uidValidity": 42, "uid": classification.classification_id,
        "messageId": "<operation@example.com>",
    }
    return store, classification, message


def _finish(store, classification):
    (action_id,) = store.direct_action_ids_for_plan(classification.action_plan.action_plan_id)
    claimed = store.claim_direct_action(action_id=action_id, claimed_at="2026-10-25T10:00:00+00:00")
    store.complete_direct_action_attempt(
        claimed, status="done", provider_operation="MOVE",
        provider_target=claimed.locator.stable_message_identity,
        provider_result_id="receipt-1", error="", finished_at="2026-10-25T10:00:05+00:00",
    )


@pytest.mark.parametrize("kind", ["direct_pending", "direct_failed", "reply_dispatch", "unsubscribe_claim"])
def test_unfinished_mailbox_or_delivery_operation_keeps_original(tmp_path, kind):
    from types import SimpleNamespace

    store, classification, message = _processed_store_with_operation(tmp_path, kind)
    reader = SimpleNamespace(read=lambda *args: message)
    if kind == "direct_failed":
        from tests.support.native_email_actions import fail_action

        (action_id,) = store.direct_action_ids_for_plan(classification.action_plan.action_plan_id)
        fail_action(store, action_id, attempts=1, retryable_last=True)
    elif kind == "reply_dispatch":
        from tests.test_email_action_reply_dao import _reply_arguments
        from tests.test_email_store import _task2_account_values

        store.create_account(_task2_account_values("dingtalk-account"))
        args, owner = _reply_arguments(store, classification)
        store.claim_email_reply_dispatch(**args, owner=owner)
    elif kind == "unsubscribe_claim":
        from tests.test_email_action_unsubscribe_dao import _authorization
        from tests.test_email_store import _task2_account_values

        store.create_account(_task2_account_values("dingtalk-account"))
        authorization, owner = _authorization(classification)
        assert store.claim_email_unsubscribe_write(**authorization, owner=owner)["acquired"]
    assert not store.compact_email_message_body(
        classification.classification_id,
        source_reader=SimpleNamespace(read=lambda *a: pytest.fail("unfinished operation needs input")),
    )
    if kind == "direct_pending":
        # Control: the same message is retired as soon as its action is done.
        _finish(store, classification)
        assert store.compact_email_message_body(classification.classification_id, source_reader=reader)
