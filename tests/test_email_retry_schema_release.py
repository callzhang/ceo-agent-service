from pathlib import Path
import sqlite3

import pytest

from app.email_store import EmailStore
from app.email_schema_release import prepare_email_schema_release  # noqa: F401 - historical fixture import retained
from tests.test_email_action_reconcile import ACCOUNT
from tests.test_email_readback_retry import _seed_failed, _release
from app.email_classifier_contracts import EmailAction
from app.action_storage import email_action_projection


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
        retry_sql = email_action_projection("email_direct_retry_requests")
        original = db.execute(f"select * from ({retry_sql})").fetchone()
        physical = db.execute(
            "select id,record_type,legacy_key,payload_json from actions "
            "where record_type='email_direct_retry_requests'"
        ).fetchone()
        assert original is not None and physical is not None
        assert db.execute(
            "select json_extract(?,'$[0]'),json_extract(?,'$[1]')",
            (physical[2], physical[2]),
        ).fetchone() == original[:2]
        with pytest.raises(
            sqlite3.IntegrityError, match="email_direct_retry_request_immutable"
        ):
            db.execute(
                "insert or replace into actions(record_type,legacy_key,payload_json) "
                "values(?,?,json_set(?,'$.source_readback_digest',?,"
                "'$.requested_at',?))",
                (physical[1], physical[2], physical[3], "a" * 64,
                 "2026-10-12T07:00:00+00:00"),
            )
        assert db.execute(f"select * from ({retry_sql})").fetchone() == original
        assert db.execute(
            "select id,record_type,legacy_key,payload_json from actions "
            "where record_type='email_direct_retry_requests'"
        ).fetchone() == physical
        assert db.execute(
            "select count(*) from actions where record_type='email_direct_retry_requests'"
        ).fetchone()[0] == 1
