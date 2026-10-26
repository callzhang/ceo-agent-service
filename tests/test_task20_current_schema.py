"""Current declaration factories, native repair modes and retained FK semantics."""
from contextlib import closing
import sqlite3

import pytest

from app import task20_current_schema as current
from app.runs11_current_schema import create_current_run11_schema
from app.task_storage import create_task_schema
from app.workbench_storage import create_workbench_auxiliary_schema
from app.task8_storage import register_task8_functions
from tests.test_task20_definitions import insert as seed_task


@pytest.fixture
def db():
    with closing(sqlite3.connect(':memory:')) as connection:
        register_task8_functions(connection)
        create_current_run11_schema(connection)
        current.create_current_task20_schema(connection)
        current.create_current_feedback_child_schema(connection)
        current.create_current_workbench_auxiliary_schema(connection)
        yield connection


def test_repeat_factories_preserve_current_main_and_unrelated_temp_counters(db):
    db.execute("update main.sqlite_sequence set seq=650 where name='tasks:published'")
    db.execute("insert into main.sqlite_sequence(name,seq) values('tasks',700)")
    db.execute('create temp table unrelated_scratch(id integer primary key autoincrement)')
    db.execute("insert into temp.sqlite_sequence(name,seq) values('tasks:published',9000)")
    main=list(db.execute('select name,seq from main.sqlite_sequence order by name'))
    temp=list(db.execute('select name,seq from temp.sqlite_sequence order by name'))
    schema=list(db.execute('select type,name,tbl_name,sql from main.sqlite_schema order by type,name'))
    current.create_current_task20_schema(db)
    current.create_current_feedback_child_schema(db)
    current.create_current_workbench_auxiliary_schema(db)
    assert db.in_transaction
    assert list(db.execute('select name,seq from main.sqlite_sequence order by name'))==main
    assert list(db.execute('select name,seq from temp.sqlite_sequence order by name'))==temp
    assert list(db.execute('select type,name,tbl_name,sql from main.sqlite_schema order by type,name'))==schema


@pytest.mark.parametrize('kind,name',[(obj['type'],obj['name']) for obj in current._current_objects()['tasks'] if obj['type']!='table'])
def test_missing_container_objects_keep_explicit_repair_mode(db,kind,name):
    db.execute('drop '+kind+' "'+name+'"')
    assert not current.task20_schema_is_current(db)
    assert current.task20_schema_is_current(db,allow_missing_objects=True)
    current.create_current_task20_schema(db)
    assert current.task20_schema_is_current(db)


def test_missing_table_and_wrong_present_object_are_not_missing_auxiliaries(db):
    db.execute('drop index idx_task20_published_1')
    db.execute('create index idx_task20_published_1 on tasks(id)')
    assert not current.task20_schema_is_current(db,allow_missing_objects=True)
    db.execute('drop table tasks')
    assert not current.task20_schema_is_current(db,allow_missing_objects=True)


@pytest.mark.parametrize('kind',current.TASK20_NEW_KINDS)
def test_retired_native_name_is_rejected_even_in_repair_mode(db,kind):
    db.execute('create table "'+kind+'"(synthetic_witness)')
    assert not current.task20_schema_is_current(db)
    assert not current.task20_schema_is_current(db,allow_missing_objects=True)


def test_original_wide_schema_stays_a_source_oracle():
    with closing(sqlite3.connect(':memory:')) as native:
        create_task_schema(native,include_workbench=True)
        create_workbench_auxiliary_schema(native)
        assert not current.task20_schema_is_current(native)
        assert not current.task20_schema_is_current(native,allow_missing_objects=True)
        assert native.execute("select count(*) from pragma_table_xinfo('tasks') where hidden=0").fetchone()[0]==82


def test_existing_physical_reply_parent_guard_and_null_behavior_stay_native(db):
    db.execute('create table physical_witness(value integer references tasks(id))')
    seed_task(db,'reply_tasks',physical_id=1,legacy_key='1')
    seed_task(db,'workbench_tasks',physical_id=2,legacy_key='task-two')
    current.install_current_task_child_guards(db)
    db.execute('insert into physical_witness values(1)')
    db.execute('insert into physical_witness values(null)')
    with pytest.raises(sqlite3.IntegrityError,match='task parent must be a reply task'):
        db.execute('insert into physical_witness values(2)')
    assert db.execute('select * from physical_witness').fetchall()==[(1,),(None,)]


def test_feedback_child_missing_table_and_auxiliary_repair_modes(db):
    db.execute('drop trigger trg_feedback_iteration_decision_items_immutable_delete')
    assert not current.task20_feedback_child_is_current(db)
    assert current.task20_feedback_child_is_current(db,allow_missing_objects=True)
    current.create_current_feedback_child_schema(db)
    assert current.task20_feedback_child_is_current(db)
    db.execute('drop table feedback_iteration_decision_items')
    assert not current.task20_feedback_child_is_current(db)
    assert current.task20_feedback_child_is_current(db,allow_missing_objects=True)


def test_workbench_current_runs11_and_missing_index_repair(db):
    assert current.task20_workbench_schema_is_current(db)
    db.execute('drop index idx_runs11_metadata_source')
    assert not current.task20_workbench_schema_is_current(db)
    assert current.task20_workbench_schema_is_current(db,allow_missing_objects=True)
    create_current_run11_schema(db)
    assert current.task20_workbench_schema_is_current(db)
    assert not db.execute("select 1 from sqlite_schema where name='codex_session_search_index'").fetchone()
    db.execute('create table codex_session_search_index(id integer)')
    assert not current.task20_workbench_schema_is_current(db,allow_missing_objects=True)
