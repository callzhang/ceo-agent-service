"""Real Store bootstrap for the delivery action layout."""
import sqlite3


from app.action_storage import action_schema_is_current, create_action_schema
from app.historical_action_options import FINAL_ACTION_OPTIONS
from app.current_schema_manifest import schema_is_current
from app.state_definitions import state_projection
from app.store import AutoReplyStore, STORE_SCHEMA_VERSION


def _source_action_options():
    return {
        key: value for key, value in FINAL_ACTION_OPTIONS.items()
        if key != "action_entity"
    }


def test_fresh_store_uses_delivery_actions_without_old_sent_tables(tmp_path):
    path = tmp_path / 'delivery.sqlite3'
    AutoReplyStore(path)
    with sqlite3.connect(path) as db:
        assert db.execute(
            f"select value from ({state_projection('service_state')}) "
            "where key='store_schema_version'"
        ).fetchone() == (STORE_SCHEMA_VERSION,)
        assert schema_is_current(db)
        assert not db.execute("select 1 from sqlite_master where name in ('sent_replies','sent_reply_observers','reply_attempts','memory_write_events')").fetchall()


def test_delivery_auxiliary_repair_preserves_layout(tmp_path):
    path = tmp_path / 'repair.sqlite3'
    source_options = _source_action_options()
    with sqlite3.connect(path) as db:
        create_action_schema(db, reply_variant="fresh_memory_fk", **source_options)
        db.commit()
    with sqlite3.connect(path) as db:
        db.execute('drop index idx_actions_td_wechat_status')
        db.commit()
    with sqlite3.connect(path) as db:
        create_action_schema(db, reply_variant="fresh_memory_fk", **source_options)
        assert action_schema_is_current(
            db, reply_variant="fresh_memory_fk", **source_options
        )
        assert db.execute(
            "select 1 from sqlite_master where type='index' "
            "and name='idx_actions_td_wechat_status'"
        ).fetchone() == (1,)
        assert not db.execute(
            "select 1 from sqlite_master where name in "
            "('sent_replies','sent_reply_observers')"
        ).fetchall()
