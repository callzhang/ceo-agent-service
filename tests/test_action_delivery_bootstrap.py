"""Real Store bootstrap for the delivery action layout."""
import sqlite3
import subprocess
import sys
from pathlib import Path


from app.action_storage import action_schema_is_current
from app.current_database_schema import FINAL_ACTION_OPTIONS
from app.state_definitions import state_projection
from app.store import AutoReplyStore


def test_fresh_store_uses_delivery_actions_without_old_sent_tables(tmp_path):
    path = tmp_path / 'delivery.sqlite3'
    AutoReplyStore(path)
    with sqlite3.connect(path) as db:
        assert db.execute(f"select value from ({state_projection('service_state')}) where key='store_schema_version'").fetchone() == ('2026-10-14.1',)
        assert action_schema_is_current(db, reply_variant="fresh_memory_fk", **FINAL_ACTION_OPTIONS)
        assert not db.execute("select 1 from sqlite_master where name in ('sent_replies','sent_reply_observers','reply_attempts','memory_write_events')").fetchall()


def test_delivery_auxiliary_repair_preserves_layout(tmp_path):
    path = tmp_path / 'repair.sqlite3'
    AutoReplyStore(path)
    with sqlite3.connect(path) as db:
        db.execute('drop index idx_actions_sent_history')
    subprocess.run([sys.executable, '-c',
                    'from pathlib import Path; from app.store import AutoReplyStore; '
                    'import sys; AutoReplyStore(Path(sys.argv[1]))', str(path)],
                   cwd=Path(__file__).parents[1], check=True, capture_output=True)
    with sqlite3.connect(path) as db:
        assert action_schema_is_current(db, reply_variant="fresh_memory_fk", **FINAL_ACTION_OPTIONS)
        assert not db.execute("select 1 from sqlite_master where name in ('sent_replies','sent_reply_observers')").fetchall()
