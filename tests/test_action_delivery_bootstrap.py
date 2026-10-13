"""Real Store bootstrap for the delivery action layout."""
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from app import action_storage as storage
from app.action_delivery_storage import restore_action_delivery_schema
from app.action_storage import action_schema_is_current
from app.store import AutoReplyStore
from tests.test_sent_delivery_source import SOURCE


def test_fresh_store_uses_delivery_actions_without_old_sent_tables(tmp_path):
    path = tmp_path / 'delivery.sqlite3'
    AutoReplyStore(path)
    with sqlite3.connect(path) as db:
        assert db.execute("select value from service_state where key='store_schema_version'").fetchone() == ('2026-10-13.4',)
        assert action_schema_is_current(db, include_delivery=True)
        assert not db.execute("select 1 from sqlite_master where name in ('sent_replies','sent_reply_observers')").fetchall()


@pytest.mark.parametrize('variant', tuple(SOURCE))
def test_cold_store_migrates_exact_published_source_layout(tmp_path, variant):
    path = tmp_path / 'source.sqlite3'
    AutoReplyStore(path)
    with sqlite3.connect(path) as db:
        db.execute('pragma foreign_keys=off')
        db.execute('begin immediate')
        state = {'actions':storage._current_objects(True, include_postfix=True),
                 'tables':SOURCE[variant],
                 'sequences':dict.fromkeys(storage.AUTOINCREMENT_TYPES),
                 'sent_sequence':None}
        restore_action_delivery_schema(db, state)
        db.execute("update service_state set value='2026-10-13.3' where key='store_schema_version'")
        db.execute("insert into sent_replies(id,conversation_id,trigger_message_id,reply_text,send_result_json,agent_run_id) values(101,'c','m','body',?,404)", (' raw non-JSON\0 ',))
        db.execute("insert into sent_replies(id,conversation_id,trigger_message_id,reply_text) values(501,'c','deleted','deleted')")
        db.execute('delete from sent_replies where id=501')
    subprocess.run([sys.executable, '-c',
                    'from pathlib import Path; from app.store import AutoReplyStore; '
                    'import sys; AutoReplyStore(Path(sys.argv[1]))', str(path)],
                   cwd=Path(__file__).parents[1], check=True, capture_output=True)
    with sqlite3.connect(path) as db:
        assert db.execute("select value from service_state where key='store_schema_version'").fetchone() == ('2026-10-13.4',)
        assert action_schema_is_current(db, include_delivery=True)
        assert db.execute('select id,send_result_json,agent_run_id from ('+storage.ACTION_PROJECTIONS['sent_replies']+')').fetchall() == [(101,' raw non-JSON\0 ',404)]
        assert db.execute('select seq from sqlite_sequence where name=?',(storage.DELIVERY_SEQUENCE_NAME,)).fetchone() == (501,)
        assert not db.execute("select 1 from sqlite_master where name in ('sent_replies','sent_reply_observers')").fetchall()


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
        assert action_schema_is_current(db, include_delivery=True)
        assert not db.execute("select 1 from sqlite_master where name in ('sent_replies','sent_reply_observers')").fetchall()
