"""Compare source-backed Console reads with current real SQLite search."""

from tests.support.email_native_fixtures import mark_processed_with_plan

from tests.support.email_listing_oracle import StoredEmailListingOracle
from email.parser import Parser
import json
import sqlite3
from types import SimpleNamespace

import pytest

from app.email_store import EmailStore, _display_message_body
from app.email_classifier_contracts import EmailClassificationStatus
from app.email_canonical_storage import (
    upsert_email_message,
    insert_email_classification,
)
from app.store import AutoReplyStore
from tests.support.email_native_fixtures import _message, _classification


@pytest.fixture
def catalog(tmp_path):
    store = EmailStore(tmp_path / "catalog.sqlite3")
    bodies = [
        "Alpha\n\nBETA",
        "From: hidden@example.test\nSubject: Hidden\n\nActual body",
        "Content-Type: text/html; charset=utf-8\n\n<head><style>hiddenneedle</style></head><p>Hello <strong>world</strong>.</p>",
        "Hel<strong>lo</strong> world",
        "Straße and 100% literal",
        "MIME-Version: 1.0\nContent-Type: multipart/alternative; boundary=x\n\n--x\nContent-Type: text/plain\n\nnot visible\n--x--",
    ]
    views = {}
    with store._connect() as db:
        db.execute("begin immediate")
        for index, body in enumerate(bodies, 1):
            identity = f"account:message-id:<{index}@example.test>"
            upsert_email_message(
                db,
                _message(
                    identity,
                    folder="INBOX",
                    uid=index,
                    rfc_message_id=f"<{index}@example.test>",
                    normalized_text=body,
                    recipients_json='["target@example.test"]',
                ),
            )
            insert_email_classification(
                db,
                _classification(
                    identity,
                    id=index,
                    uid=index,
                    sender="Sender@Example.test",
                    subject=f"Message {index}",
                    category="work" if index % 2 else "legal",
                    classification_source="agent" if index % 2 else "model",
                    status="pending_feedback",
                    updated_at=f"2026-10-18 00:00:0{index}",
                ),
            )
            parsed = Parser().parsestr(body)
            views[identity] = {
                "message_text": _display_message_body(parsed),
                "cc": parsed.get("Cc", ""),
                "recipients": ["target@example.test"],
            }
    tasks = AutoReplyStore(store.path)
    tasks.ensure_reply_task(
        channel="email",
        conversation_id="email:test",
        conversation_title="unsubscribe",
        single_chat=False,
        trigger_message_id="unsubscribe:1",
        trigger_create_time="",
        trigger_sender="sender",
        trigger_text="unsubscribe",
        trigger_message_json=json.dumps(
            {"classification_id": 1, "action_type": "unsubscribe"}
        ),
    )
    return store, views


def view_reader(single):
    return SimpleNamespace(
        read_views=lambda account, identities: {
            identity: single(account, identity) for identity in identities
        }
    )


def reader(catalog, calls=None):
    from app.email_classification_reader import EmailClassificationReader

    store, views = catalog

    def read(account, identity):
        assert account == "account"
        if calls is not None:
            calls.append(identity)
        return views[identity]

    return EmailClassificationReader(store, message_reader=view_reader(read))


@pytest.mark.parametrize(
    "query",
    [
        "",
        "ALPHA",
        "lo wo",
        "hiddenneedle",
        "STRASSE",
        "%",
        "Message 2",
        "' OR 1=1 --",
        "sender@example",
        "not visible",
    ],
)
@pytest.mark.parametrize("offset", [0, 1])
def test_search_total_order_page_and_visible_body_match_existing_store(
    catalog, query, offset
):
    store, _ = catalog
    expected = StoredEmailListingOracle(store).list_classifications(status=None, limit=2, offset=offset, q=query)
    assert (
        reader(catalog).list_classifications(search_scope="full",
            status=None, limit=2, offset=offset, q=query
        )
        == expected
    )


def test_filters_and_unsubscribe_eligibility_use_existing_business_rules(catalog):
    store, _ = catalog
    for filters in (
        {"category": "legal"},
        {"source": "agent"},
        {"action_status": "none"},
    ):
        assert reader(catalog).list_classifications(search_scope="full",
            status=None, limit=3, offset=0, q="world", **filters
        ) == StoredEmailListingOracle(store).list_classifications(
            status=None, limit=3, offset=0, q="world", **filters
        )
    assert reader(catalog).list_unsubscribe_classifications(search_scope="full",
        limit=1, offset=0, q="alpha"
    ) == StoredEmailListingOracle(store).list_unsubscribe_classifications(limit=1, offset=0, q="alpha")


def test_source_detail_keeps_business_result_and_recipients(catalog):
    store, _ = catalog
    assert reader(catalog).get_classification(1) == StoredEmailListingOracle(store).get_classification(1)
    assert reader(catalog).get_classification(999) is None


def test_metadata_candidate_projection_never_returns_model_or_readable_input(catalog):
    store, _ = catalog
    rows, total = store.classification_source_candidates(status=None)
    assert total == 6 and len(rows) == 6
    assert all(
        not {"model_text", "normalized_text", "message_text"} & row.keys()
        for row in rows
    )


def test_source_missing_does_not_become_empty_body_or_zero_total(catalog):
    from app.email_classification_reader import EmailClassificationReader

    store, _ = catalog

    def unavailable(*args):
        raise LookupError("exact original unavailable")

    with pytest.raises(LookupError, match="original unavailable"):
        EmailClassificationReader(
            store, message_reader=view_reader(unavailable)
        ).list_classifications(search_scope="full", status=None, limit=2, offset=0, q="body")


def test_metadata_search_fetches_only_page_bodies(catalog):
    calls = []
    rows, total = reader(catalog, calls).list_classifications(search_scope="full",
        status=None, limit=1, offset=2, q="sender"
    )
    assert total == 6 and [row["id"] for row in rows] == [4]
    assert len(calls) == 1


def test_source_io_has_no_write_lock_and_candidate_change_is_explicit(catalog):
    from app.email_classification_reader import EmailClassificationReader

    store, views = catalog
    changed = False

    def read(account, identity):
        nonlocal changed
        if not changed:
            with sqlite3.connect(store.path, timeout=0) as db:
                db.execute("begin immediate")
                db.execute(
                    "update emails set message_subject='changed' where classification_id=1"
                )
            changed = True
        return views[identity]

    with pytest.raises(LookupError, match="changed"):
        EmailClassificationReader(
            store, message_reader=view_reader(read)
        ).list_classifications(search_scope="full", status=None, limit=2, offset=0, q="body")


def test_explicit_status_filter_is_preserved(catalog):
    store, _ = catalog
    with store._connect() as db:
        mark_processed_with_plan(db, 1)
    options = dict(
        status=EmailClassificationStatus.PROCESSED, limit=2, offset=0, q="alpha"
    )
    assert reader(catalog).list_classifications(search_scope="full",
        **options
    ) == StoredEmailListingOracle(store).list_classifications(**options)


def test_detail_and_page_use_actual_exact_source_view_without_database_body(catalog):
    from types import SimpleNamespace
    from app.email_source_reader import EmailSourceReader
    from app.email_imap_readonly import ImapReadonlyAdapter
    from tests.test_email_exact_source import HEADERS, STRUCTURE
    from tests.test_email_imap_readonly import FakeImapSession
    from app.email_classification_reader import EmailClassificationReader

    store, _ = catalog
    sessions = []

    def factory(account):
        session = FakeImapSession(
            headers=HEADERS.replace(b"source@example.test", b"1@example.test"),
            bodystructure=STRUCTURE,
            section_payloads={"1": b"Alpha\n\nBETA"},
            uidvalidity=10,
        )
        sessions.append(session)
        return ImapReadonlyAdapter(session, account_id="account")

    public = SimpleNamespace(
        get_email_source_locations=store.get_email_source_locations,
        get_account=lambda account: {"account_id": account},
    )
    accurate = EmailSourceReader(public, source_factory=factory)
    views = EmailClassificationReader(store, message_reader=accurate)
    assert views.get_classification(1) == StoredEmailListingOracle(store).get_classification(1)
    assert views.list_classifications(search_scope="full",
        status=None, limit=1, offset=5
    ) == StoredEmailListingOracle(store).list_classifications(status=None, limit=1, offset=5)
    assert len(sessions) == 2 and all(
        session.calls[-1] == ("logout",) for session in sessions
    )


def test_search_uses_bounded_account_batches_without_single_source_reads(catalog):
    from types import SimpleNamespace
    from app.email_classification_reader import EmailClassificationReader

    store, original_views = catalog
    batches = []

    def batch(account, identities):
        assert account == "account"
        assert len(identities) <= 50
        batches.append(list(identities))
        return {identity: original_views[identity] for identity in identities}

    def single(*args):
        raise AssertionError("Console should use bounded account source batches")

    view = EmailClassificationReader(
        store, message_reader=SimpleNamespace(read_view=single, read_views=batch)
    )
    options = dict(status=None, q="world", limit=2, offset=0)
    assert view.list_classifications(search_scope="full", **options) == StoredEmailListingOracle(store).list_classifications(**options)
    assert len(batches) == 1 and sum(map(len, batches)) == 6


def test_source_listing_works_without_retired_store_original_interfaces(catalog):
    store, _ = catalog
    assert not hasattr(store, "list_classifications")
    assert not hasattr(store, "list_unsubscribe_classifications")
    rows, total = reader(catalog).list_classifications(search_scope="full", status=None, limit=1, offset=0, q="alpha")
    assert total == 1 and rows[0]["message_text"] == "Alpha\n\nBETA"


def test_stable_identity_lookup_does_not_read_original_or_model_input(catalog, monkeypatch):
    store, _ = catalog
    identity = "account:message-id:<1@example.test>"
    expected = store.get_classification_by_stable_identity(identity)
    original_open = store._open_connection
    forbidden_reads = []

    def metadata_connection(*args, **kwargs):
        db = original_open(*args, **kwargs)

        def authorize(operation, table, column, database, trigger):
            if operation == sqlite3.SQLITE_READ and table == "emails" and column in {
                "class_model_text", "message_normalized_text",
            }:
                forbidden_reads.append(column)
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        db.set_authorizer(authorize)
        return db

    monkeypatch.setattr(store, "_open_connection", metadata_connection)
    assert store.get_classification_by_stable_identity(identity) == expected
    assert store.has_stable_classification(identity)
    assert store.get_classification_by_stable_identity("missing") is None
    assert forbidden_reads == []


@pytest.mark.parametrize('limit', [1, 2, 25])
def test_full_source_scan_batch_is_independent_of_page_size(limit):
    from app.email_classification_reader import EmailClassificationReader

    candidates = [
        {'id': 503 - rank, 'account_id': 'account',
         'stable_message_identity': str(rank), 'sender': 'sender@example.test',
         'subject': 'message'}
        for rank in range(503)
    ]
    reads = []
    matching_ranks = {3, 57, 499, 502}

    def read_views(account, identities):
        assert account == 'account'
        reads.append(tuple(identities))
        return {
            identity: {'message_text': 'needle' if int(identity) in matching_ranks else 'ordinary body'}
            for identity in identities
        }

    store = SimpleNamespace(classification_source_candidates=lambda **kwargs: (candidates, 503))
    view = EmailClassificationReader(store, message_reader=SimpleNamespace(read_views=read_views))
    rows, total = view.list_classifications(search_scope="full", status=None, q='needle', limit=limit, offset=1)
    assert total == 4
    assert [row['id'] for row in rows] == [446, 4, 1][:limit]
    assert all(row['message_text'] == 'needle' for row in rows)
    assert sum(map(len, reads)) == 503
    # Native exact IMAP reads already use at most50 messages per batch.
    # A small results page must not multiply source leases beyond that bound.
    assert len(reads) <= 11
    assert max(map(len, reads)) <= 50
