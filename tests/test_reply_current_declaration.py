"""The current database declaration is checked without changing the file."""
from contextlib import closing
import sqlite3

import pytest

from app.current_schema_manifest import current_objects, require_current_pair, schema_objects
from app.store import AutoReplyStore


def test_current_declaration_is_exact_and_read_only(tmp_path):
    path = tmp_path / "current.sqlite3"
    AutoReplyStore(path)
    before = path.read_bytes()
    require_current_pair(path)
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        assert schema_objects(db) == current_objects(email_ledger=False)
    assert path.read_bytes() == before


@pytest.mark.parametrize("change", ("missing_index", "changed_index", "extra_index", "extra_trigger", "native_table"))
def test_current_declaration_rejects_incomplete_or_mixed_shapes(tmp_path, change):
    path = tmp_path / "current.sqlite3"
    AutoReplyStore(path)
    with sqlite3.connect(path) as db:
        index = "idx_actions_business_attention_events_lookup"
        if change == "missing_index":
            db.execute(f"drop index {index}")
        elif change == "changed_index":
            db.execute(f"drop index {index}")
            db.execute(f"create index {index} on actions(record_type)")
        elif change == "extra_index":
            db.execute("create index unexpected_action_index on actions(record_type)")
        elif change == "extra_trigger":
            db.execute("create trigger unexpected_action_trigger after insert on actions begin select 1; end")
        else:
            db.execute("create table reply_attempts(id integer)")
    before = path.read_bytes()
    with pytest.raises(sqlite3.IntegrityError, match="unsupported or malformed"):
        require_current_pair(path)
    assert path.read_bytes() == before
