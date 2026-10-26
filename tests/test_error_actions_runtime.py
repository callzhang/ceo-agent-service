"""Current Errors share Actions while retaining native public behavior."""

import sqlite3

import pytest

from app.configs_storage import insert_config
from app.email_store import EmailStore
from app.store import AutoReplyStore
from app.action_error_definitions import error_projection
from app.action_error_storage import SOURCE_TABLE_SQL


def test_current_store_error_roundtrip_uses_actions(tmp_path):
    store = AutoReplyStore(tmp_path / "store.db")
    store.record_error("conversation", "message", "worker", "detail")
    with sqlite3.connect(store.path) as db:
        assert db.execute("select 1 from sqlite_schema where name='errors'").fetchone() is None
        row = db.execute("select legacy_key from actions where record_type='errors'").fetchone()
        assert row == ("1",)
    assert store.count_errors() == 1
    assert store.list_errors()[0].id == 1
    assert store.resolve_errors([1], resolution="recovered") == 1
    assert store.get_error(1).resolution == "recovered"


@pytest.mark.parametrize("first", ("store", "email"))
def test_fresh_constructors_and_nonempty_config_reopen_without_native_errors(tmp_path, first):
    path = tmp_path / "paired.db"
    if first == "store":
        store = AutoReplyStore(path)
        EmailStore(path)
    else:
        EmailStore(path)
        store = AutoReplyStore(path)
    with store._immediate_write_transaction() as db:
        insert_config(db, "scheduled_task_config_versions", {"snapshot_json": "{}"})
    store.record_error("cid", "mid", "worker", "failed")
    AutoReplyStore(path)
    EmailStore(path)
    with sqlite3.connect(path) as db:
        assert db.execute("select name from sqlite_schema where name='errors'").fetchone() is None
        assert db.execute("select count(*) from actions where record_type='errors'").fetchone()[0] == 1
    assert store.get_error(1).detail == "failed"


def test_public_error_id_point_range_and_recent_reads_use_bounded_index(tmp_path):
    store = AutoReplyStore(tmp_path / "store.db")
    with sqlite3.connect(":memory:") as native:
        native.execute(SOURCE_TABLE_SQL[1])
        native.executemany(
            "insert into errors(id,kind,detail) values(?, 'test', 'detail')",
            ((i,) for i in range(1, 3001)),
        )
        for _ in range(3000):
            store.record_error(None, None, "test", "detail")
        with store._connect() as db:
            cases = (
                ("where id=?", "where id=?", (2799,)),
                ("where id>? order by id limit 20",
                 "where id>? order by id limit 20", (2970,)),
                ("order by id desc limit 20", "order by id desc limit 20", ()),
            )
            for target_suffix, source_suffix, args in cases:
                target = f"select id from ({error_projection()}) {target_suffix}"
                source = f"select id from errors {source_suffix}"
                assert [tuple(row) for row in db.execute(target, args)] == native.execute(source, args).fetchall()
                plan = [step[3] for step in db.execute("explain query plan " + target, args)]
                assert any("idx_actions_public_id" in step for step in plan), plan
                assert not any("USE TEMP B-TREE" in step for step in plan), plan
                instructions = [0]

                def budget():
                    instructions[0] += 100
                    return instructions[0] > 2000

                db.set_progress_handler(budget, 100)
                try:
                    assert [tuple(row) for row in db.execute(target, args)] == native.execute(source, args).fetchall()
                finally:
                    db.set_progress_handler(None, 0)
