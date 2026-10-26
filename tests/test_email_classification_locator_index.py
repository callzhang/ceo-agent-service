import sqlite3

from tests.email_schema_fixtures import restore_action_plan_snapshot_column


from app.email_classifier_contracts import EmailAction, EmailClassificationStatus
from app.email_store import EmailStore
from tests.test_email_store import _classification, _persist_scan


INDEX = "idx_email_classifications_account_locator"
UID_SQL = "select uid from email_classifications where account_id=? and folder=? and uidvalidity=?"


def business_rows(path):
    with sqlite3.connect(path) as db:
        columns = [row[1] for row in db.execute("pragma table_info(email_classifications)")
                   if row[1] != "action_plan_json"]
        return {table: db.execute("select " + (",".join(columns) if table == "email_classifications" else "*") + f" from {table} order by rowid").fetchall()
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
        db.execute("drop table email_direct_retry_requests")
        restore_action_plan_snapshot_column(db)
        db.execute(f"drop index if exists {INDEX}")
        db.execute("delete from email_schema_migrations where version>46")
        db.execute("insert or ignore into email_schema_migrations values(46, 'legacy-fixture')")
    return path, store

