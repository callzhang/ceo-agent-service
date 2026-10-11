import sqlite3

import pytest

from app.email_classifier_contracts import EmailAction, EmailClassificationStatus
from app.email_store import EmailPersistenceCorruption, EmailStore
from tests.test_email_store import _classification, _persist_scan


INDEX = "idx_email_classifications_account_locator"
UID_SQL = "select uid from email_classifications where account_id=? and folder=? and uidvalidity=?"


def business_rows(path):
    with sqlite3.connect(path) as db:
        return {table: db.execute(f"select * from {table} order by rowid").fetchall()
                for table in ("email_accounts", "email_messages", "email_classifications",
                              "email_action_plans", "email_actions", "email_scan_cursors")}


def legacy_v46(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    store = EmailStore(path)
    rows = (
        ("first", "INBOX", 42, EmailClassificationStatus.PROCESSED),
        ("duplicate", "INBOX", 42, EmailClassificationStatus.PROCESSED),
        ("pending", "INBOX", 42, EmailClassificationStatus.PENDING_FEEDBACK),
        ("archive", "Archive", 42, EmailClassificationStatus.PROCESSED),
        ("new-epoch", "INBOX", 43, EmailClassificationStatus.PROCESSED),
    )
    for index, (name, folder, validity, status) in enumerate(rows):
        item = _classification(status=status, message_id=name, folder=folder,
                               uidvalidity=validity, uid=7,
                               actions=(EmailAction.LABEL,))
        _persist_scan(store, item, expected_cursor_uidvalidity=42 if validity == 43 else None)
        with sqlite3.connect(path) as db:
            db.execute("update email_classifications set updated_at=? where id=?",
                       (f"2026-10-11T00:00:0{index}+00:00", item.classification_id))
    with sqlite3.connect(path) as db:
        db.execute(f"drop index if exists {INDEX}")
        db.execute("update email_schema_migrations set version=46 where version>46")
    return path, store


def test_uid_locator_query_uses_covering_folder_and_epoch_index(tmp_path):
    path, _ = legacy_v46(tmp_path)
    EmailStore(path)
    with sqlite3.connect(path) as db:
        plan = db.execute("explain query plan " + UID_SQL,
                          ("dingtalk-account", "INBOX", 42)).fetchall()
    details = "\n".join(row[3] for row in plan)
    assert f"USING COVERING INDEX {INDEX}" in details
    assert "account_id=? AND folder=? AND uidvalidity=?" in details


def test_locator_migration_preserves_business_rows_uid_metadata_and_existing_order(tmp_path):
    path, legacy = legacy_v46(tmp_path)
    groups = (("INBOX", 42), ("Archive", 42), ("INBOX", 43), ("Missing", 42))
    before = [(
        legacy.stable_classification_uids(account_id="dingtalk-account", folder=f, uidvalidity=v),
        legacy.classified_provider_uids(account_id="dingtalk-account", folder=f, uidvalidity=v),
    ) for f, v in groups]
    original_rows = business_rows(path)
    migrated = EmailStore(path)
    after = [(
        migrated.stable_classification_uids(account_id="dingtalk-account", folder=f, uidvalidity=v),
        migrated.classified_provider_uids(account_id="dingtalk-account", folder=f, uidvalidity=v),
    ) for f, v in groups]
    assert after == before
    assert business_rows(path) == original_rows
    with sqlite3.connect(path) as db:
        assert db.execute("select version from email_schema_migrations order by version").fetchall() == [(46,), (47,)]
        assert db.execute("pragma foreign_key_check").fetchall() == []
        indexed = db.execute(f"pragma index_xinfo({INDEX})").fetchall()
    assert [row[2] for row in indexed if row[5]] == [
        "account_id", "folder", "uidvalidity", "status", "updated_at", "uid",
    ]
    assert indexed[4][3] == 1
    EmailStore(path)
    with sqlite3.connect(path) as db:
        assert db.execute("select version from email_schema_migrations order by version").fetchall() == [(46,), (47,)]


def test_locator_migration_validation_failure_rolls_back_index_and_version(tmp_path, monkeypatch):
    path, _ = legacy_v46(tmp_path)
    original_rows = business_rows(path)
    validate = EmailStore._validate_durable_state

    def fail_after_migration(self, db):
        if db.execute("select max(version) from email_schema_migrations").fetchone()[0] == 47:
            raise RuntimeError("injected locator migration failure")
        return validate(self, db)

    monkeypatch.setattr(EmailStore, "_validate_durable_state", fail_after_migration)
    with pytest.raises(RuntimeError, match="injected locator migration failure"):
        EmailStore(path)
    assert business_rows(path) == original_rows
    with sqlite3.connect(path) as db:
        assert db.execute("select max(version) from email_schema_migrations").fetchone()[0] == 46
        assert db.execute("select name from sqlite_master where type='index' and name=?", (INDEX,)).fetchone() is None


def test_current_schema_missing_locator_index_is_not_silently_repaired(tmp_path):
    path = tmp_path / "current.sqlite3"
    EmailStore(path)
    with sqlite3.connect(path) as db:
        db.execute(f"drop index if exists {INDEX}")
    with pytest.raises(EmailPersistenceCorruption, match=INDEX):
        EmailStore(path, validate_rows=False)


@pytest.mark.parametrize("columns", [
    "account_id,folder,uidvalidity,status,updated_at asc,uid",
    "account_id,folder collate nocase,uidvalidity,status,updated_at desc,uid",
    "account_id,folder,uidvalidity,status desc,updated_at desc,uid",
])
def test_current_schema_rejects_wrong_locator_sort_and_collation(tmp_path, columns):
    path = tmp_path / "wrong-locator.sqlite3"
    EmailStore(path)
    with sqlite3.connect(path) as db:
        db.execute(f"drop index {INDEX}")
        db.execute(f"create index {INDEX} on email_classifications({columns})")
    with pytest.raises(EmailPersistenceCorruption, match=INDEX):
        EmailStore(path, validate_rows=False)
