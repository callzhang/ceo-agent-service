import sqlite3
import json
import pytest

from app.email_historical_candidate_schema import (
    compact_candidate_locators,
    compact_projection,
    reconstruct_projection,
    restore_candidate_locators,
)
from app.email_schema_release import (
    apply_email_historical_candidate_transition,
    email_historical_candidate_release_selected,
    inverse_email_historical_candidate_transition,
)
from app.email_store import _historical_candidate_list_item


def test_deployment_quiet_sources_accept_exact_email53_but_runtime_does_not():
    from app.current_database_schema import create_current_database_schema
    from app.email_historical_candidate_schema import SOURCE_VERSION as EMAIL_RELEASE_SOURCE_VERSION
    from app.current_publication_sources import (
        current_email_actions_available,
        publication_deployment_task_sources,
        publication_dispatcher_source,
        publication_email_action_source,
        publication_meeting_sources,
        publication_run_sources,
        publication_scheduled_source,
        publication_task_sources,
    )
    from app.current_schema_manifest import create_current_email_migration_ledger, schema_is_current
    from app.state_storage import insert_state

    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    create_current_database_schema(db)
    create_current_email_migration_ledger(db)
    db.execute("begin")
    insert_state(db, "service_state", {
        "key": "store_schema_version", "value": "2026-10-23.1",
    })
    db.execute("insert into email_schema_migrations values(53,'source')")
    assert schema_is_current(db)

    with pytest.raises(ValueError, match="unsupported mixed Email publication version"):
        publication_run_sources(db)
    with pytest.raises(ValueError, match="unsupported mixed Email publication version"):
        current_email_actions_available(db)

    quiet_sources = [
        *publication_run_sources(db, release_source_email_version=EMAIL_RELEASE_SOURCE_VERSION),
        *publication_task_sources(db, release_source_email_version=EMAIL_RELEASE_SOURCE_VERSION),
        *publication_deployment_task_sources(db, release_source_email_version=EMAIL_RELEASE_SOURCE_VERSION),
        *publication_meeting_sources(db, release_source_email_version=EMAIL_RELEASE_SOURCE_VERSION),
        publication_scheduled_source(db, release_source_email_version=EMAIL_RELEASE_SOURCE_VERSION),
        publication_dispatcher_source(db, release_source_email_version=EMAIL_RELEASE_SOURCE_VERSION),
        publication_email_action_source(db, release_source_email_version=EMAIL_RELEASE_SOURCE_VERSION),
    ]
    for source in quiet_sources:
        db.execute(f"select count(*) from ({source})").fetchone()


def _db():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("""create table email_historical_candidates (
        account_id text, folder text, model_id text, stable_message_identity text,
        normalized_text text, provider_message_json text, uidvalidity integer, uid integer,
        state text, reason text, attempted_at text, next_retry_at text, updated_at text,
        primary key(account_id,folder,model_id,stable_message_identity))""")
    return db


def test_compact_and_inverse_preserve_mismatches_unknowns_and_business_fields():
    db = _db()
    payload = {
        "accountId": "acct", "folder": "INBOX",
        "stableMessageIdentity": "stable", "uidValidity": 17, "uid": 42,
        "messageId": "<id@example>", "threadId": "thread",
        "unknown": {"keep": [1, "x"]},
        "fallbackUidValidity": 9,
    }
    db.execute(
        "insert into email_historical_candidates values(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("acct", "INBOX", "model", "stable", "sha", json.dumps(payload),
         23, 42, "deferred", "reason", "attempt", "retry", "updated"),
    )
    before_business = tuple(db.execute(
        "select account_id,folder,model_id,stable_message_identity,normalized_text,"
        "uidvalidity,uid,state,reason,attempted_at,next_retry_at,updated_at "
        "from email_historical_candidates"
    ).fetchone())

    assert compact_candidate_locators(db) == 1
    row = db.execute("select * from email_historical_candidates").fetchone()
    compact = json.loads(row["provider_message_json"])
    assert set(compact) == {
        "messageId", "threadId", "unknown", "fallbackUidValidity", "uidValidity"
    }
    assert compact["uidValidity"] == 17  # differs from scalar fallback 23
    assert compact["messageId"] == "<id@example>"
    assert compact["threadId"] == "thread"
    assert reconstruct_projection(row)["uid"] == 42
    assert _historical_candidate_list_item(row)["provider_message"]["uid"] == 42
    assert tuple(db.execute(
        "select account_id,folder,model_id,stable_message_identity,normalized_text,"
        "uidvalidity,uid,state,reason,attempted_at,next_retry_at,updated_at "
        "from email_historical_candidates"
    ).fetchone()) == before_business

    assert restore_candidate_locators(db) == 1
    restored = json.loads(db.execute(
        "select provider_message_json from email_historical_candidates"
    ).fetchone()[0])
    assert restored == payload
    assert compact_candidate_locators(db) == 1
    compact_again = json.loads(db.execute(
        "select provider_message_json from email_historical_candidates"
    ).fetchone()[0])
    assert compact_again == compact


def test_projection_compaction_requires_exact_json_types_and_same_row_values():
    payload = {
        "accountId": "acct", "folder": "INBOX", "stableMessageIdentity": "stable",
        "uidValidity": True, "uid": "42", "messageId": None, "threadId": "t",
    }
    compact = compact_projection(
        payload, account_id="acct", folder="INBOX", stable_message_identity="stable",
        uidvalidity=17, uid=42,
    )
    assert compact == {"uidValidity": True, "uid": "42", "messageId": None, "threadId": "t"}


def test_current_store_email_release_selector_requires_exact_53_to_54_pair():
    select = email_historical_candidate_release_selected
    assert select(store_marker="2026-10-23.1", current_store_marker="2026-10-23.1",
                  email_version=53, target_email_version=54)
    assert not select(store_marker="2026-10-23.1", current_store_marker="2026-10-23.1",
                      email_version=52, target_email_version=54)
    assert not select(store_marker="older", current_store_marker="2026-10-23.1",
                      email_version=53, target_email_version=54)
    assert not select(store_marker="2026-10-23.1", current_store_marker="2026-10-23.1",
                      email_version=53, target_email_version=53)


def test_release_transition_and_inverse_preserve_ledger_and_reforward():
    db = _db()
    db.execute("create table email_schema_migrations(version integer primary key, applied_at text)")
    original_ledger = tuple((version, f"stamp-{version}") for version in range(1, 54))
    db.executemany("insert into email_schema_migrations values(?,?)", original_ledger)
    db.execute(
        "insert into email_historical_candidates values(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("acct", "INBOX", "model", "stable", "sha",
         json.dumps({"accountId": "acct", "folder": "INBOX",
                     "stableMessageIdentity": "stable", "uidValidity": 7, "uid": 9,
                     "messageId": "id", "threadId": None}),
         7, 9, "pending", "", "", "", "updated"),
    )
    apply_email_historical_candidate_transition(db, original_ledger, "stamp-54")
    assert tuple(tuple(row) for row in db.execute("select * from email_schema_migrations order by version"))[-1] == (54, "stamp-54")
    inverse_email_historical_candidate_transition(db, original_ledger)
    assert tuple(tuple(row) for row in db.execute("select version,applied_at from email_schema_migrations order by version")) == original_ledger
    restored = json.loads(db.execute(
        "select provider_message_json from email_historical_candidates"
    ).fetchone()[0])
    assert restored["accountId"] == "acct" and restored["uid"] == 9
    apply_email_historical_candidate_transition(db, original_ledger, "stamp-54-again")
    assert tuple(tuple(row) for row in db.execute("select version,applied_at from email_schema_migrations order by version"))[-1] == (54, "stamp-54-again")


def test_release_fails_closed_when_inverse_locator_was_already_absent():
    db = _db()
    db.execute("create table email_schema_migrations(version integer primary key, applied_at text)")
    ledger = ((53, "stamp-53"),)
    db.execute("insert into email_schema_migrations values(53,'stamp-53')")
    db.execute(
        "insert into email_historical_candidates values(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("acct", "INBOX", "model", "stable", "sha",
         json.dumps({"accountId": "acct", "folder": "INBOX",
                     "stableMessageIdentity": "stable", "uidValidity": 7,
                     "messageId": "id", "threadId": None}),
         7, 9, "pending", "", "", "", "updated"),
    )
    before = db.execute("select provider_message_json from email_historical_candidates").fetchone()[0]
    try:
        apply_email_historical_candidate_transition(db, ledger, "stamp-54")
    except ValueError as exc:
        assert "missing uid" in str(exc)
    else:
        raise AssertionError("missing inverse locator must be rejected")
    assert db.execute("select provider_message_json from email_historical_candidates").fetchone()[0] == before
    assert tuple(tuple(row) for row in db.execute("select version,applied_at from email_schema_migrations")) == ledger
