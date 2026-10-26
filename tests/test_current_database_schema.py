"""Normal Store and Email constructors must select one final schema."""

import sqlite3

import pytest

from app.store import AutoReplyStore
from app.email_store import EmailStore
from app.current_schema_manifest import schema_is_current
from tests.support.current_inventory import CURRENT_TABLES


@pytest.mark.parametrize("first", ("store", "email"))
def test_blank_and_cold_constructors_select_final_graph(tmp_path, first):
    path = tmp_path / "final.sqlite3"
    if first == "email":
        EmailStore(path)
    store = AutoReplyStore(path)
    EmailStore(path)
    with sqlite3.connect(path) as db:
        assert schema_is_current(db)
        assert {row[0] for row in db.execute("select name from sqlite_schema where type='table' and name not glob 'sqlite_*'")} == CURRENT_TABLES
        before = db.execute("select count(*) from configs").fetchone()[0]
    signal_id = store.create_business_task_signal(source_type="message", source_ref="original:one",
        evidence_text="A transient observed source", dedupe_key="one")
    assert store.get_business_task_signal(signal_id).source.body_utf8_bytes == len("A transient observed source".encode())
    from app import store as module
    module._INITIALIZED_STORE_PATHS.discard(path.resolve())
    cold = AutoReplyStore(path)
    EmailStore(path)
    assert cold.get_business_task_signal(signal_id) == store.get_business_task_signal(signal_id)
    with sqlite3.connect(path) as db:
        assert schema_is_current(db)
        assert db.execute("select count(*) from configs").fetchone()[0] == before
        assert db.execute("pragma foreign_key_check").fetchall() == []


def test_known_auxiliary_damage_is_rejected(tmp_path):
    path = tmp_path / "damaged.sqlite3"
    AutoReplyStore(path)
    with sqlite3.connect(path) as db:
        index = db.execute(
            "select name from sqlite_schema where type='index' and name like 'idx_actions_%' order by name"
        ).fetchone()[0]
        db.execute(f'drop index "{index}"')
        before = tuple(db.iterdump())
    with pytest.raises(sqlite3.IntegrityError, match="malformed"):
        AutoReplyStore(path)
    with sqlite3.connect(path) as db:
        assert tuple(db.iterdump()) == before


@pytest.mark.parametrize("table", ("provenance", "business_source_documents", "business", "reply_task_deferral_receipts"))
def test_unversioned_current_or_retired_source_container_is_rejected_read_only(tmp_path, table):
    path = tmp_path / "collision.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute(f'create table "{table}"(id integer primary key, value text)')
        db.execute(f'insert into "{table}" values(1, ?)', ("existing fact",))
    before = path.read_bytes()
    with pytest.raises(sqlite3.IntegrityError, match="unsupported or malformed Store source"):
        AutoReplyStore(path)
    assert path.read_bytes() == before
    assert not path.with_name(path.name + "-wal").exists()
