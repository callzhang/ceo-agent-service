from contextlib import closing
import sqlite3

import pytest

from test_action_storage import source_database


def _source(path):
    db = source_database(path)
    db.execute("create table service_state (key text primary key,value text not null,updated_at text not null default current_timestamp)")
    db.execute("insert into service_state values('store_schema_version','2026-10-12.1','original marker time')")
    db.commit()
    db.close()


def _publish(root, path):
    from app.action_storage import migrate_action_schema

    with closing(sqlite3.connect(path, isolation_level=None)) as db:
        db.execute("begin immediate")
        migrate_action_schema(db)
        db.execute("update service_state set value='2026-10-13.1' where key='store_schema_version'")
        db.commit()


@pytest.mark.parametrize('statement', [
    'create view actions as select 1 as id',
    'create view review_candidates as select 1 as id',
    'create index actions on unrelated(value)',
    'create trigger candidate_reviews after insert on unrelated begin select 1; end',
    'create index idx_actions_public_id on unrelated(value)',
    'create trigger trg_actions_record_type_immutable after insert on unrelated begin select 1; end',
    'create view Actions as select 1 as id',
    'create index IDX_actions_PUBLIC_id on unrelated(value)',
])
def test_store_bootstrap_rejects_action_name_collisions_before_schema_changes(tmp_path, statement):
    from app.store import AutoReplyStore

    path = tmp_path / 'collision.db'
    with closing(sqlite3.connect(path)) as db:
        db.executescript("create table unrelated(value text);insert into unrelated values('retained original');")
        db.execute(statement)
        db.commit()
        before = db.execute('select type,name,tbl_name,sql from sqlite_master order by type,name').fetchall()
    with pytest.raises((sqlite3.IntegrityError, ValueError)):
        AutoReplyStore(path)
    with closing(sqlite3.connect(path)) as db:
        assert db.execute('select type,name,tbl_name,sql from sqlite_master order by type,name').fetchall() == before
        assert db.execute('select * from unrelated').fetchall() == [('retained original',)]

