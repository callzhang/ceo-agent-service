"""The real paired release publishes result references and rolls back current facts."""
import sqlite3
from pathlib import Path

import pytest

from app.email_store import EmailStore
from app.state_definitions import state_projection
from app.state_storage import update_state_rows
from app.store import AutoReplyStore
from app.store_schema_release import prepare_database_schema_release

ROOT = Path(__file__).resolve().parents[1]


def _markers(db):
    return (tuple(db.execute(f"select value,updated_at from ({state_projection('service_state')}) where key='store_schema_version'").fetchone()),
            tuple(tuple(r) for r in db.execute('select version,applied_at from email_schema_migrations order by version')))


def test_fresh_target_is_noop_and_mixed_marker_refuses(tmp_path):
    path = tmp_path/'fresh.sqlite3'
    AutoReplyStore(path)
    EmailStore(path)
    assert prepare_database_schema_release(ROOT,path) is None
    with sqlite3.connect(path) as db:
        db.execute('begin immediate')
        update_state_rows(db,'service_state',{'value':'2026-10-19.1'},where_sql="key='store_schema_version'")
    with pytest.raises((ValueError,sqlite3.IntegrityError)):
        prepare_database_schema_release(ROOT,path)


