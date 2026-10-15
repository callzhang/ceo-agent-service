import sqlite3

from tests.email_schema_fixtures import restore_action_plan_snapshot_column

import pytest

from app.email_classifier_contracts import EmailClassificationStatus
from app.email_store import EmailPersistenceCorruption, EmailStore
from tests.test_email_store import _classification, _persist_scan

LOOKUP = "idx_email_provider_observations_lookup"
FOLDER = "idx_email_provider_observations_folder"
POINT_SQL = ("select state, observed_at from email_provider_observations "
             "where account_id=? and stable_message_identity=?")
FOLDER_SQL = ("select stable_message_identity from email_provider_observations "
              "where account_id=? and provider_folder_id=?")


def index_names(db):
    return {row[1] for row in db.execute("pragma index_list(email_provider_observations)")}


def seed_provider(path):
    store = EmailStore(path)
    _persist_scan(store, _classification(status=EmailClassificationStatus.PROCESSED))
    with sqlite3.connect(path) as db:
        db.execute(
            "insert into email_provider_observations "
            "(account_id,stable_message_identity,state,provider_folder_id,"
            "provider_folder_name,category_key,important,important_signals_json,observed_at) "
            "values (?,?, 'available', 'INBOX', 'INBOX', null,0,'[]',?)",
            ("dingtalk-account", "provider-example", "2026-10-11T00:00:00Z"),
        )
    return store


def snapshot(path):
    with sqlite3.connect(path) as db:
        columns = [row[1] for row in db.execute("pragma table_info(email_classifications)")
                   if row[1] != "action_plan_json"]
        return {table: db.execute("select " + (",".join(columns) if table == "email_classifications" else "*") + f" from {table} order by rowid").fetchall()
                for table in ("email_accounts", "email_messages", "email_classifications",
                              "email_action_plans", "email_actions", "email_scan_cursors",
                              "email_provider_observations")}


def legacy_v47(path):
    seed_provider(path)
    with sqlite3.connect(path) as db:
        db.execute("drop table email_direct_retry_requests")
        restore_action_plan_snapshot_column(db)
        db.execute(
            f"create index if not exists {LOOKUP} on email_provider_observations"
            "(account_id,stable_message_identity,observed_at desc)"
        )
        db.execute("delete from email_schema_migrations where version>=47")
        db.execute("insert into email_schema_migrations(version,applied_at) values (47,?)",
                   ("2026-10-11T00:00:00Z",))
    return path


def test_new_schema_omits_lookup_but_keeps_folder_and_pk(tmp_path):
    path = tmp_path / "fresh.sqlite3"
    EmailStore(path)
    with sqlite3.connect(path) as db:
        indexes = index_names(db)
        assert LOOKUP not in indexes
        assert FOLDER in indexes
        assert any(row[3] == "pk" for row in db.execute(
            "pragma index_list(email_provider_observations)"))


def test_lookup_uses_pk_and_folder_query_keeps_its_index(tmp_path):
    path = tmp_path / "query.sqlite3"
    seed_provider(path)
    with sqlite3.connect(path) as db:
        assert db.execute(POINT_SQL, ("dingtalk-account", "provider-example")).fetchall() == [
            ("available", "2026-10-11T00:00:00Z")]
        point_plan = " ".join(row[3] for row in db.execute(
            "explain query plan " + POINT_SQL, ("dingtalk-account", "provider-example")))
        assert "sqlite_autoindex_email_provider_observations" in point_plan
        folder_plan = " ".join(row[3] for row in db.execute(
            "explain query plan " + FOLDER_SQL, ("dingtalk-account", "INBOX")))
        assert FOLDER in folder_plan
        assert db.execute(FOLDER_SQL, ("dingtalk-account", "INBOX")).fetchall() == [
            ("provider-example",)]
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("insert into email_provider_observations select * "
                       "from email_provider_observations where stable_message_identity=?",
                       ("provider-example",))


def test_required_folder_index_is_not_silently_recreated(tmp_path):
    path = tmp_path / "broken.sqlite3"
    EmailStore(path)
    with sqlite3.connect(path) as db:
        db.execute(f"drop index {FOLDER}")
    with pytest.raises(EmailPersistenceCorruption, match=FOLDER):
        EmailStore(path, validate_rows=False)
