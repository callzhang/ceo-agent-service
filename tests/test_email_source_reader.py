"""Source location projection and provider read use real final SQLite storage."""

from types import SimpleNamespace
import sqlite3
import json

import pytest

from app.email_store import EmailStore
from app.email_canonical_storage import upsert_email_message
from tests.provider_state_fixtures import insert_observation
from tests.support.email_native_fixtures import _message
from tests.test_email_exact_source import source, LOCATOR


@pytest.fixture
def stored(tmp_path):
    store = EmailStore(tmp_path / "source.sqlite3")
    with store._connect() as db:
        db.execute("begin immediate")
        upsert_email_message(
            db,
            _message(
                LOCATOR.stable_message_identity,
                account_id="a",
                folder="INBOX",
                uidvalidity=42,
                uid=1,
                rfc_message_id=LOCATOR.rfc_message_id,
                normalized_text="stored body must not be passed to source factory",
            ),
        )
    return store


def test_location_is_metadata_only_and_works_without_classification(stored):
    location = stored.get_email_source_location("a", LOCATOR.stable_message_identity)
    assert location["folder"] == "INBOX" and location["uid"] == 1
    assert location["stable_message_identity"] == LOCATOR.stable_message_identity
    assert "normalized_text" not in location
    assert "stored body" not in repr(location)
    assert (
        stored.get_email_source_location("other", LOCATOR.stable_message_identity)
        is None
    )


def reader(store, adapter):
    from app.email_source_reader import EmailSourceReader

    public = SimpleNamespace(
        get_email_source_locations=store.get_email_source_locations,
        get_account=lambda account: {"account_id": account},
    )
    return EmailSourceReader(public, source_factory=lambda account: adapter)


def test_reader_fetches_exact_body_without_database_write(stored):
    adapter, session = source()
    before = stored.path.read_bytes()
    result = reader(stored, adapter).read("a", LOCATOR.stable_message_identity)
    assert result["textBody"] == "complete body"
    assert session.calls[-1] == ("logout",)
    assert stored.path.read_bytes() == before


@pytest.mark.parametrize("changed", [False, True])
def test_retired_original_reference_checks_complete_version_and_closes_source(stored, changed):
    from hashlib import sha256

    reference = {"kind": "provider", "representation": "provider_body",
                 "sha256": sha256(b"complete body").hexdigest(), "utf8_bytes": len(b"complete body"),
                 "locator": {"folder": "INBOX", "uidvalidity": 42, "uid": 1,
                             "rfc_message_id": LOCATOR.rfc_message_id}}
    with stored._connect() as db:
        db.execute("update emails set message_normalized_text='',message_source_reference_json=?",
                   (json.dumps(reference),))
    before = stored.path.read_bytes()
    adapter, session = source(body=b"changed original" if changed else b"complete body")
    if changed:
        with pytest.raises(ValueError, match="original"):
            reader(stored, adapter).read("a", LOCATOR.stable_message_identity)
    else:
        result = reader(stored, adapter).read("a", LOCATOR.stable_message_identity)
        assert result["textBody"] == "complete body"
    assert session.calls[-1] == ("logout",)
    assert stored.path.read_bytes() == before


def test_retired_native_original_is_verified_when_provider_message_is_read(stored, tmp_path, monkeypatch):
    from app import native_trajectory as native
    from app.email_body_source import locate_original

    path = tmp_path / "native.jsonl"
    path.write_text(json.dumps({"content": json.dumps({"body": "complete body"})}) + "\n")
    monkeypatch.setattr(native, "find_codex_session_path", lambda *a, **kw: path)
    reference = locate_original("complete body", attempts=[dict(runtime_kind="codex_cli",
        session_id="session", transcript_start=0, transcript_end=1)],
        read_message=lambda: pytest.fail("native must be read first"))
    with stored._connect() as db:
        db.execute("update emails set message_normalized_text='',message_source_reference_json=?",
                   (json.dumps(reference),))
    adapter, session = source()
    assert reader(stored, adapter).read("a", LOCATOR.stable_message_identity)["textBody"] == "complete body"
    assert session.calls[-1] == ("logout",)


def test_native_body_reference_does_not_invent_to_or_cc_from_flat_recipients(stored, tmp_path, monkeypatch):
    from app import native_trajectory as native
    from app.email_body_source import locate_original
    from tests.test_email_exact_source import HEADERS

    path = tmp_path / "native.jsonl"
    path.write_text(json.dumps({"content": json.dumps({"body": "complete body"})}) + "\n")
    monkeypatch.setattr(native, "find_codex_session_path", lambda *a, **kw: path)
    reference = locate_original("complete body", attempts=[dict(runtime_kind="codex_cli",
        session_id="session", transcript_start=0, transcript_end=1)],
        read_message=lambda: pytest.fail("native must be read first"))
    with stored._connect() as db:
        db.execute("update emails set message_normalized_text='',message_source_reference_json=?,message_recipients_json=?",
                   (json.dumps(reference), json.dumps(["target@example.test", "copy@example.test"])))
    adapter, session = source(headers=HEADERS.replace(b"Subject:", b"Cc: copy@example.test\r\nSubject:"))
    result = reader(stored, adapter).read_view("a", LOCATOR.stable_message_identity)
    assert result == {"message_text": "complete body", "cc": "copy@example.test", "recipients": ["target@example.test"]}
    assert session.calls[-1] == ("logout",)


def observe(store, folder):
    with store._connect() as db:
        db.execute("begin immediate")
        insert_observation(
            db,
            {
                "account_id": "a",
                "stable_message_identity": LOCATOR.stable_message_identity,
                "state": "available",
                "provider_folder_id": folder,
                "provider_folder_name": folder,
                "observed_at": "current",
            },
        )


def test_observed_moved_folder_is_resolved_with_real_readonly_provider(stored):
    observe(stored, "Archive")
    adapter, session = source(search_result=b"7", uidvalidity=99)
    message = reader(stored, adapter).read("a", LOCATOR.stable_message_identity)
    assert (
        message["folder"] == "Archive"
        and message["uid"] == 7
        and message["uidValidity"] == 99
    )
    assert ("select", "Archive", True) in session.calls
    assert session.calls[-1] == ("logout",)
    assert (
        stored.get_email_source_location("a", LOCATOR.stable_message_identity)["uid"]
        == 1
    )


def test_ambiguous_moved_original_is_unavailable_and_session_is_closed(stored):
    observe(stored, "Archive")
    adapter, session = source(search_result=b"7 8", uidvalidity=99)
    with pytest.raises(LookupError, match="ambiguous"):
        reader(stored, adapter).read("a", LOCATOR.stable_message_identity)
    assert session.calls[-1] == ("logout",)


def test_current_location_change_during_provider_read_is_not_returned(stored):
    adapter, session = source()
    original = adapter.fetch_message

    def fetch(*args, **kwargs):
        with sqlite3.connect(stored.path, timeout=0) as db:
            db.execute("begin immediate")
            db.execute("update emails set uid=2 where account_id='a'")
        return original(*args, **kwargs)

    adapter.fetch_message = fetch
    with pytest.raises(LookupError, match="location changed"):
        reader(stored, adapter).read("a", LOCATOR.stable_message_identity)
    assert session.calls[-1] == ("logout",)


def test_task_context_reads_current_message_location_instead_of_stale_classification(
    stored,
):
    from app.email_source_reader import EmailSourceReader

    observe(stored, "Archive")
    adapter, session = source(search_result=b"7", uidvalidity=99)
    public = SimpleNamespace(
        get_email_source_locations=stored.get_email_source_locations,
        get_account=lambda account: {"account_id": account},
    )
    message = EmailSourceReader(public, source_factory=lambda account: adapter).read(
        "a", LOCATOR.stable_message_identity,
    )
    assert message["folder"] == "Archive" and message["uid"] == 7
    assert session.calls[-1] == ("logout",)


def test_source_view_renders_complete_body_and_provider_recipient_headers(stored):
    from tests.test_email_exact_source import HEADERS

    headers = HEADERS.replace(
        b"To: target@example.test", b"To: target@example.test\r\nCc: copy@example.test"
    )
    adapter, _ = source(headers=headers, body=b"Alpha\n\nBETA")
    view = reader(stored, adapter).read_view("a", LOCATOR.stable_message_identity)
    assert view == {
        "message_text": "Alpha\n\nBETA",
        "cc": "copy@example.test",
        "recipients": ["target@example.test"],
    }


def test_observed_same_folder_relocates_an_expired_uid_by_exact_message_id(stored):
    observe(stored, "INBOX")
    adapter, session = source(search_result=b"7")
    original = session.uid

    def uid(command, *args):
        if command == "FETCH" and args[0] == b"1":
            return "OK", [None]
        return original(command, *args)

    session.uid = uid
    message = reader(stored, adapter).read("a", LOCATOR.stable_message_identity)
    assert message["uid"] == 7 and message["folder"] == "INBOX"
    assert (
        "uid",
        "SEARCH",
        None,
        "HEADER",
        "Message-ID",
        "<source@example.test>",
    ) in session.calls
    assert (
        stored.get_email_source_location("a", LOCATOR.stable_message_identity)["uid"]
        == 1
    )
    assert session.calls[-1] == ("logout",)


def test_failed_coordinates_without_current_folder_fact_are_not_guessed(stored):
    adapter, session = source(search_result=b"7")
    original = session.uid

    def uid(command, *args):
        if command == "FETCH" and args[0] == b"1":
            return "OK", [None]
        return original(command, *args)

    session.uid = uid
    with pytest.raises(LookupError):
        reader(stored, adapter).read("a", LOCATOR.stable_message_identity)
    assert not any(call[:2] == ("uid", "SEARCH") for call in session.calls)


def second_message(stored):
    identity = "a:message-id:<second@example.test>"
    with stored._connect() as db:
        db.execute("begin immediate")
        upsert_email_message(
            db,
            _message(
                identity,
                account_id="a",
                folder="INBOX",
                uidvalidity=42,
                uid=2,
                rfc_message_id="<second@example.test>",
            ),
        )
    return identity


def batch_source(stored):
    from app.email_source_reader import EmailSourceReader
    from tests.test_email_exact_source import HEADERS

    adapter, session = source()
    original = session.uid

    def uid(command, *args):
        if command != "FETCH":
            return original(command, *args)
        records = []
        for requested_uid in args[0].split(b','):
            second = requested_uid == b"2"
            session.headers = (HEADERS.replace(b"source@example.test", b"second@example.test")
                               if second else HEADERS)
            session.section_payloads["1"] = b"second body" if second else b"complete body"
            status, data = original(command, requested_uid, args[1])
            records.extend(data)
        return status, records

    session.uid = uid
    factories = []
    metadata = []

    def locations(keys):
        metadata.append(tuple(keys))
        return stored.get_email_source_locations(keys)

    def factory(account):
        factories.append(account["account_id"])
        return adapter

    public = SimpleNamespace(
        get_email_source_locations=locations,
        get_account=lambda account: {"account_id": account},
    )
    return (
        EmailSourceReader(public, source_factory=factory),
        adapter,
        session,
        factories,
        metadata,
    )


def test_batch_metadata_is_account_scoped_without_body(stored):
    identity = second_message(stored)
    keys = [
        ("a", LOCATOR.stable_message_identity),
        ("a", identity),
        ("other", identity),
    ]
    locations = stored.get_email_source_locations(keys)
    assert set(locations) == set(keys[:2])
    assert all("normalized_text" not in item for item in locations.values())


def test_account_batch_uses_one_source_and_two_metadata_reads(stored):
    identity = second_message(stored)
    reads, adapter, session, factories, metadata = batch_source(stored)
    results = reads.read_many("a", [LOCATOR.stable_message_identity, identity])
    assert [item["textBody"] for item in results.values()] == [
        "complete body",
        "second body",
    ]
    assert factories == ["a"] and len(metadata) == 2
    assert session.calls.count(("logout",)) == 1


def test_empty_account_batch_does_not_open_a_source(stored):
    reads, adapter, session, factories, metadata = batch_source(stored)
    assert reads.read_many("a", []) == {}
    assert factories == metadata == session.calls == []


def test_batch_source_failure_closes_without_returning_partial_results(stored):
    identity = second_message(stored)
    reads, adapter, session, factories, metadata = batch_source(stored)
    original = adapter.fetch_messages

    def fetch(requests):
        result = original(requests)
        result[identity] = LookupError("second original unavailable")
        return result

    adapter.fetch_messages = fetch
    with pytest.raises(LookupError, match="second original unavailable"):
        reads.read_many("a", [LOCATOR.stable_message_identity, identity])
    assert factories == ["a"] and session.calls.count(("logout",)) == 1


def test_batch_rechecks_prior_rows_after_all_source_reads(stored):
    identity = second_message(stored)
    reads, adapter, session, factories, metadata = batch_source(stored)
    original = adapter.fetch_messages

    def fetch(requests):
        result = original(requests)
        with sqlite3.connect(stored.path, timeout=0) as db:
            db.execute("begin immediate")
            db.execute("update emails set uid=3 where stable_message_identity=?",
                       (LOCATOR.stable_message_identity,))
        return result

    adapter.fetch_messages = fetch
    with pytest.raises(LookupError, match="location changed"):
        reads.read_many("a", [LOCATOR.stable_message_identity, identity])
    assert session.calls.count(("logout",)) == 1


def test_batch_missing_location_does_not_open_a_source(stored):
    reads, adapter, session, factories, metadata = batch_source(stored)
    with pytest.raises(LookupError, match="location is unavailable"):
        reads.read_many("a", [LOCATOR.stable_message_identity, "missing"])
    assert len(metadata) == 1
    assert factories == session.calls == []


def test_batch_duplicate_identity_reads_the_original_once(stored):
    reads, adapter, session, factories, metadata = batch_source(stored)
    fetches = []
    original = adapter.fetch_message

    def fetch(locator, **kwargs):
        fetches.append(locator.uid)
        return original(locator, **kwargs)

    adapter.fetch_message = fetch
    messages = reads.read_many(
        "a", [LOCATOR.stable_message_identity, LOCATOR.stable_message_identity]
    )
    assert list(messages) == [LOCATOR.stable_message_identity]
    assert fetches == [1]
    assert factories == ["a"]
    assert session.calls.count(("logout",)) == 1


def test_batch_views_share_exact_source_lease_and_render_each_original(stored):
    identity = second_message(stored)
    reads, adapter, session, factories, metadata = batch_source(stored)
    views = reads.read_views("a", [LOCATOR.stable_message_identity, identity])
    assert [view["message_text"] for view in views.values()] == [
        "complete body",
        "second body",
    ]
    assert all(view["recipients"] == ["target@example.test"] for view in views.values())
    assert factories == ["a"] and len(metadata) == 2
    assert session.calls.count(("logout",)) == 1


def test_thread_discovery_reads_only_metadata_and_keeps_transitive_relations(stored, monkeypatch):
    from contextlib import contextmanager

    with stored._connect() as db:
        for identity, account, rfc, references, thread in [
            ("a:prior", "a", "<prior>", [], "prior-thread"),
            ("a:bridge", "a", "<bridge>", ["<prior>", LOCATOR.rfc_message_id], "bridge-thread"),
            ("a:unrelated", "a", "<unrelated>", [], "other-thread"),
            ("b:other", "b", LOCATOR.rfc_message_id, [], "prior-thread"),
        ]:
            upsert_email_message(db, _message(
                identity, account_id=account, uidvalidity=42, uid=2,
                rfc_message_id=rfc, references_json=json.dumps(references),
                thread_identity=thread, normalized_text="cached original",
            ))
    connect = stored._connect
    denied = []

    @contextmanager
    def metadata_connection(**kwargs):
        with connect(**kwargs) as db:
            def authorizer(action, table, column, database, trigger):
                if action == sqlite3.SQLITE_READ and table == "emails" and column in {
                    "message_normalized_text", "message_preview", "class_model_text", "class_preview",
                }:
                    denied.append(column)
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK
            db.set_authorizer(authorizer)
            yield db

    monkeypatch.setattr(stored, "_connect", metadata_connection)
    rows = stored.list_email_context_thread(account_id="a", stable_message_identity=LOCATOR.stable_message_identity)
    assert [row["stable_message_identity"] for row in rows] == [LOCATOR.stable_message_identity, "a:prior", "a:bridge"]
    assert all("normalized_text" not in row and "preview" not in row for row in rows)
    assert denied == []


def test_runtime_classification_getter_needs_no_original_or_model_input(stored, monkeypatch):
    from contextlib import contextmanager
    from app.email_canonical_storage import insert_email_classification, update_email_message
    from tests.support.email_native_fixtures import _classification

    with stored._connect() as db:
        insert_email_classification(db, _classification(
            LOCATOR.stable_message_identity, id=101, account_id="a", folder="INBOX",
            uidvalidity=42, uid=1, rfc_message_id=LOCATOR.rfc_message_id,
        ))
        message_id = db.execute("select message_id from emails where stable_message_identity=?", (LOCATOR.stable_message_identity,)).fetchone()[0]
        update_email_message(db, message_id, {"recipients_json": '["recipient@example.com"]'})
    original = stored._connect

    @contextmanager
    def metadata_connection(**kwargs):
        with original(**kwargs) as db:
            def authorizer(action, table, column, database, trigger):
                if action == sqlite3.SQLITE_READ and table == "emails" and column in {
                    "message_normalized_text", "class_model_text",
                }:
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK
            db.set_authorizer(authorizer)
            yield db

    monkeypatch.setattr(stored, "_connect", metadata_connection)
    item = stored.get_classification(101)
    assert item["stable_message_identity"] == LOCATOR.stable_message_identity
    assert item["recipients"] == ["recipient@example.com"]
    assert "message_text" not in item and "model_text" not in item and "cc" not in item
    assert stored.get_classification(999) is None
