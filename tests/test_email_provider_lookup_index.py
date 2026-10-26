"""Current provider identity and folder query plans on the accepted target."""

import sqlite3

import pytest

from app.email_classifier_contracts import EmailClassificationStatus
from app.email_store import EmailStore
from tests.test_email_store import _classification, _persist_scan

POINT = "idx_provider_state_observation_identity"
GENERATION = "idx_provider_state_generation_identity"
FOLDER = "idx_provider_state_observation_folder"
POINT_SQL = (
    "select state, observed_at from provider_state "
    "where record_type='observation' and account_id=? and stable_message_identity=?"
)
FOLDER_SQL = (
    "select stable_message_identity from provider_state "
    "where record_type='observation' and account_id=? and provider_folder_id=?"
)


def index_names(db):
    return {row[1] for row in db.execute("pragma index_list(provider_state)")}


def seed_provider(path):
    store = EmailStore(path)
    _persist_scan(store, _classification(status=EmailClassificationStatus.PROCESSED))
    with sqlite3.connect(path) as db:
        db.execute(
            "insert into provider_state "
            "(record_type,account_id,stable_message_identity,state,provider_folder_id,observed_at,"
            "provider_folder_name,category_key,important,important_signals_json) "
            "values('observation',?,?,'available','INBOX',?,'INBOX',null,0,'[]')",
            ("dingtalk-account", "provider-example", "2026-10-11T00:00:00Z"),
        )
    return store


def test_new_schema_has_three_partial_provider_indexes(tmp_path):
    path = tmp_path / "fresh.sqlite3"
    EmailStore(path)
    with sqlite3.connect(path) as db:
        assert index_names(db) == {POINT, GENERATION, FOLDER}
        assert all(row[4] for row in db.execute("pragma index_list(provider_state)"))


def test_lookup_uses_partial_identity_and_folder_indexes(tmp_path):
    path = tmp_path / "query.sqlite3"
    seed_provider(path)
    with sqlite3.connect(path) as db:
        assert db.execute(POINT_SQL, ("dingtalk-account", "provider-example")).fetchall() == [
            ("available", "2026-10-11T00:00:00Z")]
        point_plan = " ".join(row[3] for row in db.execute(
            "explain query plan " + POINT_SQL, ("dingtalk-account", "provider-example")))
        assert POINT in point_plan
        folder_plan = " ".join(row[3] for row in db.execute(
            "explain query plan " + FOLDER_SQL, ("dingtalk-account", "INBOX")))
        assert FOLDER in folder_plan
        assert db.execute(FOLDER_SQL, ("dingtalk-account", "INBOX")).fetchall() == [
            ("provider-example",)]
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "insert into provider_state "
                "(record_type,account_id,stable_message_identity,state,provider_folder_id,observed_at,"
                "provider_folder_name,category_key,important,important_signals_json) "
                "select record_type,account_id,stable_message_identity,state,provider_folder_id,observed_at,"
                "provider_folder_name,category_key,important,important_signals_json "
                "from provider_state where record_type='observation' and stable_message_identity=?",
                ("provider-example",),
            )


def test_required_folder_index_is_not_silently_recreated(tmp_path):
    path = tmp_path / "broken.sqlite3"
    EmailStore(path)
    with sqlite3.connect(path) as db:
        db.execute(f"drop index {FOLDER}")
    with pytest.raises(sqlite3.IntegrityError, match="unsupported or malformed Store source schema"):
        EmailStore(path, validate_rows=False)
