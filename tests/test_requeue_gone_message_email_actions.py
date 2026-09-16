import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from app.email_store import EmailStore
from requeue_gone_message_email_actions import LEGACY_ERROR, requeue
from tests.test_email_store import (  # noqa: E402
    _classification,
    _fetchall,
    _persist_scan,
)
from app.email_store import EmailClassificationStatus


def _exhausted_action(store: EmailStore, database: Path, *, error: str) -> str:
    _persist_scan(store, _classification(status=EmailClassificationStatus.PROCESSED))
    action_id = _fetchall(database, "select action_id from email_actions")[0][
        "action_id"
    ]
    with store._connect() as db:
        db.execute(
            "update email_actions set status='failed', attempt_count=3, error=?, "
            "next_attempt_at='', finished_at='2026-09-16T07:28:28+00:00' "
            "where action_id=?",
            (error, action_id),
        )
    return action_id


def test_requeue_resets_only_the_gone_message_failures(tmp_path: Path):
    database = tmp_path / "requeue.sqlite3"
    store = EmailStore(database)
    action_id = _exhausted_action(store, database, error=LEGACY_ERROR)

    preview, remaining = requeue(store)
    assert preview == [action_id]
    assert remaining == 1
    with store._connect() as db:
        assert (
            db.execute(
                "select status from email_actions where action_id=?", (action_id,)
            ).fetchone()["status"]
            == "failed"
        )

    applied, remaining = requeue(store, dry_run=False)
    assert applied == [action_id]
    assert remaining == 0
    with store._connect() as db:
        row = db.execute(
            "select status, attempt_count, error, next_attempt_at "
            "from email_actions where action_id=?",
            (action_id,),
        ).fetchone()
    assert row["status"] == "pending"
    assert row["attempt_count"] == 0
    assert row["error"] == ""
    assert row["next_attempt_at"] == ""


def test_requeue_leaves_a_different_failure_alone(tmp_path: Path):
    database = tmp_path / "requeue-other.sqlite3"
    store = EmailStore(database)
    action_id = _exhausted_action(
        store, database, error="provider_apply_failed:TimeoutError"
    )

    assert requeue(store)[0] == []
    with store._connect() as db:
        assert (
            db.execute(
                "select status from email_actions where action_id=?", (action_id,)
            ).fetchone()["status"]
            == "failed"
        )
