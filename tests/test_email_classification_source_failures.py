from types import SimpleNamespace

import pytest

from app.email_account_connector import ConnectorPriority
from app.email_worker import _build_email_connector_registry, _build_registry_source_factory
from app.provider_state_storage import insert_observation, delete_observation
from tests.test_email_body_source import body_store as body_store
from tests.test_email_model_input_lifecycle import _model_fixture, _reader
from tests.test_email_exact_source import source, LOCATOR


@pytest.mark.parametrize("inline", [False, True])
def test_unavailable_historical_source_does_not_block_usable_owner_examples(body_store, inline):
    store = body_store
    message, text = _model_fixture(store)
    if not inline:
        assert store.compact_email_inputs(101, source_reader=_reader(message))
    before_stamp = store.owner_labelled_stamp()
    with store._connect() as db:
        row = db.execute("select account_id,stable_message_identity from emails").fetchone()
        db.execute("begin immediate")
        insert_observation(db, {
            "account_id": row["account_id"], "stable_message_identity": row["stable_message_identity"],
            "state": "unavailable", "observed_at": "current",
        })

    def missing_original(_account, _identities):
        raise LookupError("email source UID is unavailable")

    examples, unavailable_stamp = store.owner_labelled_examples(
        source_reader=SimpleNamespace(read_many=missing_original)
    )
    assert [item["text"] for item in examples] == ([text] if inline else [])
    with store._connect() as db:
        assert db.execute("select class_classification_source,class_confirmed_category from emails").fetchone()[:] == ("user", "work")
        db.execute("begin immediate")
        delete_observation(db, row["account_id"], row["stable_message_identity"])
        insert_observation(db, {
            "account_id": row["account_id"], "stable_message_identity": row["stable_message_identity"],
            "state": "available", "provider_folder_id": "INBOX", "provider_folder_name": "INBOX",
            "observed_at": "restored",
        })
    restored, restored_stamp = store.owner_labelled_examples(source_reader=_reader(message))
    assert [item["text"] for item in restored] == [text]
    assert restored_stamp == before_stamp
    assert (unavailable_stamp == restored_stamp) is inline


def test_shared_connector_preserves_readonly_timeout_for_slow_mail(monkeypatch):
    _, session = source()
    original_uid = session.uid

    def uid(*args):
        if session.socket_timeout < 25:
            raise TimeoutError("simulated provider takes 25 seconds")
        return original_uid(*args)

    session.uid = uid

    def connect(_host, _username, _password, *, timeout=20.0, **_kwargs):
        session.socket_timeout = timeout
        return SimpleNamespace(session=session)

    monkeypatch.setattr("app.email_provider_actions.ImapDeterministicProvider.connect", connect)
    account = {
        "account_id": "a", "enabled": True, "imap_tls": True,
        "imap_host": "imap.example.test", "imap_port": 993,
        "imap_username": "a@example.test", "imap_secret_reference": "CEO_EMAIL_A_IMAP_SECRET",
    }
    registry = _build_email_connector_registry(
        SimpleNamespace(get_account=lambda _account: account),
        environment_factory=lambda: {"CEO_EMAIL_A_IMAP_SECRET": "test-only"},
    )
    source_factory = _build_registry_source_factory(registry, ConnectorPriority.HIGH)
    current = source_factory(account)
    try:
        batch = current.fetch_uid_batch(cursor_uidvalidity=42, last_seen_uid=0)
        assert batch.messages[0]["textBody"] == "complete body"
    finally:
        current.logout()


@pytest.mark.parametrize("mode", ["single", "scan", "batch"])
def test_bodystructure_literal_filename_keeps_full_body_and_uid_binding(mode):
    adapter, session = source()
    original_uid = session.uid
    filename = "报告（修订版）.pdf"
    literal = filename.encode("utf-8")
    prefix = (
        b'1 (UID 1 BODYSTRUCTURE (("text" "plain" ("charset" "UTF-8") '
        b'NIL NIL "7bit" 13 1 NIL NIL NIL)'
        b'("application" "octet-stream" NIL NIL NIL "base64" 42 NIL '
        b'("attachment" ("filename" {' + str(len(literal)).encode() + b'}'
    )

    def uid(command, *args):
        if command == "FETCH" and args[-1] == "(BODYSTRUCTURE)":
            return "OK", [(prefix, literal), b')) NIL) "mixed" ("boundary" "x") NIL NIL))']
        return original_uid(command, *args)

    session.uid = uid
    if mode == "single":
        message = adapter.fetch_message(LOCATOR, stable_message_identity=LOCATOR.stable_message_identity)
    elif mode == "scan":
        message = adapter.fetch_uid_batch(cursor_uidvalidity=42, last_seen_uid=0).messages[0]
    else:
        message = adapter.fetch_messages([(LOCATOR, LOCATOR.stable_message_identity)])[LOCATOR.stable_message_identity]
    assert message["textBody"] == "complete body"
    assert message["attachments"][0]["filename"] == filename
    assert message["stableMessageIdentity"] == LOCATOR.stable_message_identity


@pytest.mark.parametrize("bound_uid", [True, False])
def test_uid_after_literal_is_bound_only_by_response_metadata(bound_uid):
    adapter, session = source(body=b"UID")
    original_uid = session.uid

    def uid(command, *args):
        if command == "FETCH" and args[-1] == "(BODY.PEEK[1])":
            return "OK", [(b"1 (BODY[1] {3}", b"UID"), b" UID 1)" if bound_uid else b" 1)"]
        return original_uid(command, *args)

    session.uid = uid
    if bound_uid:
        assert adapter.fetch_message(LOCATOR, stable_message_identity=LOCATOR.stable_message_identity)["textBody"] == "UID"
    else:
        with pytest.raises(LookupError, match="UID"):
            adapter.fetch_message(LOCATOR, stable_message_identity=LOCATOR.stable_message_identity)
