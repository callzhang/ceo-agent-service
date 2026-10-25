"""Selected Email history storage and reversible source facts."""

import json
import hashlib
import sqlite3

from app.action_email_history_definitions import HISTORY_KINDS
from app.action_email_history_storage import insert_email_history
from app.store import AutoReplyStore
from app.email_store import EmailStore


def test_default_current15_action_oracles_remain_exact():
    from pathlib import Path
    from app.action_storage import _current_objects
    capsule = json.loads((Path(__file__).parents[1] / "docs/superpowers/specs"
                          / "2026-10-17-email-history-current15-source.json").read_text())
    for variant, expected in capsule["pre_edit_action_oracles"].items():
        actual = _current_objects(reply_variant=variant,
                                  **capsule["pre_edit_action_options"])
        assert hashlib.sha256(json.dumps(actual, sort_keys=True,
                                         separators=(",", ":")).encode()).hexdigest() == expected


def test_fresh_current_email_history_is_in_actions(tmp_path):
    path = tmp_path / "current.sqlite"
    AutoReplyStore(path)
    EmailStore(path)
    with sqlite3.connect(path) as db:
        tables = {name for (name,) in db.execute(
            "select name from main.sqlite_schema where type='table' and name not like 'sqlite_%'"
        )}
        assert "email_historical_classification_history" not in tables
        assert "email_historical_operations" not in tables
        columns = {row[1] for row in db.execute("pragma main.table_xinfo(actions)")}
        assert "email_history_event_id" in columns
        assert "email_operation_operation_id" in columns


def test_history_rowid_max_allocation_and_caller_rollback(tmp_path):
    path = tmp_path / "rowid.sqlite"
    EmailStore(path)
    values = dict(event_id="new", stable_message_identity="stable", model_id="model",
                  predicted_category=None, threshold=None, probability=None,
                  important=None, action_outcome="done", created_at="time")
    with sqlite3.connect(path) as db:
        db.execute("begin")
        from app import action_storage as actions
        db.execute("insert into actions(_kind,legacy_key,payload_json) values(?,?,?)", (
            actions.ACTION_RECORD_TYPES.code(HISTORY_KINDS[0]), str(2**63 - 1),
            actions._action_payload(db, HISTORY_KINDS[0], values | {"event_id": "max"}),
        ))
        db.execute("savepoint caller")
        assert insert_email_history(db, HISTORY_KINDS[0], values) == 1
        source_rowid = db.execute("select cast(legacy_key as integer) from actions "
                                  "where record_type=? and email_history_event_id='new'",
                                  (HISTORY_KINDS[0],)).fetchone()[0]
        assert 1 <= source_rowid < 2**63 - 1
        db.execute("rollback to caller")
        db.execute("release caller")
        assert db.execute("select count(*) from actions where record_type=?",
                          (HISTORY_KINDS[0],)).fetchone()[0] == 1
        db.rollback()
