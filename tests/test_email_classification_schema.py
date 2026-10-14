"""The unselected classification parent matches a genuine 2773 fresh database."""

import json
from pathlib import Path
import sqlite3

from app.email_classification_schema import create_current_email_classifications


_ORACLE = json.loads(
    (Path(__file__).parent / "fixtures/email_current_schema_2773.json").read_text()
)
_CLASSIFICATION = next(
    item for item in _ORACLE["objects"] if item["name"] == "email_classifications"
)


def test_current_parent_matches_published_fresh_native_declaration() -> None:
    with sqlite3.connect(":memory:") as db:
        db.execute("pragma foreign_keys=on")
        create_current_email_classifications(db)
        assert db.execute(
            "select sql from sqlite_schema where name='email_classifications'"
        ).fetchone()[0] == _CLASSIFICATION["sql"]
        assert [list(row) for row in db.execute("pragma table_xinfo(email_classifications)")] == _CLASSIFICATION["columns"]
        assert [list(row) for row in db.execute("pragma foreign_key_list(email_classifications)")] == []
        assert len(_CLASSIFICATION["columns"]) == 30
        assert "action_plan_json" not in {row[1] for row in db.execute("pragma table_xinfo(email_classifications)")}
        create_current_email_classifications(db)
        assert db.execute("select count(*) from sqlite_schema where name='email_classifications'").fetchone() == (1,)


def test_current_parent_obeys_caller_transaction_rollback() -> None:
    with sqlite3.connect(":memory:") as db:
        db.execute("begin")
        create_current_email_classifications(db)
        db.execute("rollback")
        assert db.execute("select 1 from sqlite_schema where name='email_classifications'").fetchone() is None
