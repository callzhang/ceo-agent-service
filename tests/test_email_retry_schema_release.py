from pathlib import Path
import sqlite3

import pytest

from app.email_store import EmailStore
from app.email_schema_release import prepare_email_schema_release
from tests.test_email_action_reconcile import ACCOUNT
from tests.test_email_readback_retry import _seed_failed, _release
from app.email_classifier_contracts import EmailAction


def _schema50(path: Path):
    store = EmailStore(path)
    with sqlite3.connect(path) as db:
        db.execute("drop table email_direct_retry_requests")
        db.execute("delete from email_schema_migrations where version=51")
        db.execute("insert or ignore into email_schema_migrations values (50,'original50')")
    return store


def _target(tmp_path):
    root = tmp_path / "target"
    (root / "app").mkdir(parents=True)
    (root / "app" / "email_store.py").write_text("EMAIL_SCHEMA_VERSION = 51\n")
    return root


def test_schema51_empty_inverse_preserves_data_and_versions(tmp_path):
    path = tmp_path / "mail.sqlite3"
    _schema50(path)
    release = prepare_email_schema_release(_target(tmp_path), path)
    assert release is not None
    release.migrate = lambda _root, path: EmailStore(path)
    release.publish()
    release.finalize()
    release.rollback()
    release.rollback()
    with sqlite3.connect(path) as db:
        assert db.execute("select max(version) from email_schema_migrations").fetchone()[0] == 50
        assert db.execute("select 1 from sqlite_master where name='email_direct_retry_requests'").fetchone() is None
        assert db.execute("pragma integrity_check").fetchone()[0] == "ok"


def test_schema51_inverse_refuses_to_discard_request(tmp_path):
    path = tmp_path / "mail.sqlite3"
    _schema50(path)
    release = prepare_email_schema_release(_target(tmp_path), path)
    assert release is not None
    release.migrate = lambda _root, path: EmailStore(path)
    release.publish()
    store = EmailStore(path)
    action_id = _seed_failed(store, "protected", uid=7)
    failed = store.list_failed_direct_actions(account_id=ACCOUNT, action_types=(EmailAction.MOVE,), limit=1)[0]
    assert _release(store,
        failed, source_locator=failed.action.locator, source_revision="source-v1",
        requested_at="2026-10-12T06:00:00+00:00", apply=True,
    )
    with pytest.raises(ValueError, match="discard explicit retry"):
        release.rollback()
    with sqlite3.connect(path) as db:
        assert db.execute("select count(*) from email_direct_retry_requests where action_id=?", (action_id,)).fetchone()[0] == 1
        assert db.execute("pragma foreign_key_check(email_direct_retry_requests)").fetchall() == []
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            db.execute("delete from email_direct_retry_requests")


def test_retry_request_cannot_be_replaced(tmp_path):
    store = EmailStore(tmp_path / "mail.sqlite3")
    _seed_failed(store, "replace-proof", uid=7)
    failed = store.list_failed_direct_actions(
        account_id=ACCOUNT, action_types=(EmailAction.MOVE,), limit=1,
    )[0]
    assert _release(store,
        failed, source_locator=failed.action.locator, source_revision="source-v1",
        requested_at="2026-10-12T06:00:00+00:00", apply=True,
    )
    with sqlite3.connect(store.path) as db:
        original = db.execute("select * from email_direct_retry_requests").fetchone()
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            db.execute("insert or replace into email_direct_retry_requests values (?,?,?,?,?)",
                       (original[0], original[1], "a" * 64, original[3], "2026-10-12T07:00:00+00:00"))
        assert db.execute("select * from email_direct_retry_requests").fetchone() == original
