"""Native main-schema AUTOINCREMENT survives unrelated TEMP allocators."""
from contextlib import closing
import sqlite3

import pytest

from app.task20_definitions import create_task20_schema, initialize_task8_sequence
from app.task12_storage import insert_task12
from app.task8_storage import register_task8_functions, task8_insert_parts


@pytest.fixture(params=(False,True))
def db(request):
    with closing(sqlite3.connect(':memory:')) as connection:
        create_task20_schema(connection)
        register_task8_functions(connection)
        connection.execute('create temp table unrelated_scratch(id integer primary key autoincrement)')
        connection.execute('insert into temp.unrelated_scratch default values')
        if request.param:
            connection.executemany('insert into temp.sqlite_sequence(name,seq) values(?,?)',
                                  [('tasks',9000),('tasks:published',8000),('tasks:wechat_memory_import_jobs',7000)])
        connection.execute("insert into main.sqlite_sequence(name,seq) values('tasks',700)")
        connection.execute("insert into main.sqlite_sequence(name,seq) values('tasks:wechat_memory_import_jobs',99)")
        yield connection


def temp_snapshot(db):
    return list(db.execute('select name,seq from temp.sqlite_sequence order by name'))


def test_published_initializer_modifies_main_only(db):
    before=temp_snapshot(db)
    initialize_task8_sequence(db,700)
    assert db.execute("select seq from main.sqlite_sequence where name='tasks:published'").fetchall()==[(700,)]
    assert temp_snapshot(db)==before
    assert db.in_transaction


def test_deleted_high_main_physical_and_source_waters_are_read_from_main(db):
    before=temp_snapshot(db)
    result=insert_task12(db,'wechat_memory_import_jobs',{'import_run_id':'current','account_id':'synthetic'})
    assert (result.physical_id,result.lastrowid)==(701,100)
    assert db.execute("select seq from main.sqlite_sequence where name='tasks'").fetchone()==(701,)
    assert db.execute("select seq from main.sqlite_sequence where name='tasks:wechat_memory_import_jobs'").fetchone()==(100,)
    assert temp_snapshot(db)==before


def test_main_trigger_keeps_schema_local_resolution_without_qualification(db):
    before=temp_snapshot(db)
    initialize_task8_sequence(db,700)
    parts=task8_insert_parts('work_summary_inputs',{'source_type':'local_file','source_ref':'witness','payload_json':'{}'})
    result=db.execute('insert into main.tasks('+parts.columns+') values('+parts.values+')',parts.parameters)
    assert result.lastrowid==701
    assert db.execute("select seq from main.sqlite_sequence where name='tasks:published'").fetchone()==(701,)
    assert temp_snapshot(db)==before


def test_native_source_auto_control_uses_main_with_identical_temp_names(db):
    db.execute('create table native_control(id integer primary key autoincrement,value text)')
    db.execute("insert into main.sqlite_sequence(name,seq) values('native_control',99)")
    db.execute("insert into temp.sqlite_sequence(name,seq) values('native_control',9999)")
    before=temp_snapshot(db)
    assert db.execute("insert into main.native_control(value) values('current')").lastrowid==100
    result=insert_task12(db,'wechat_memory_import_jobs',{'import_run_id':'current','account_id':'synthetic'})
    assert result.lastrowid==100
    assert temp_snapshot(db)==before
