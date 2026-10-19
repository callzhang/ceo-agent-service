"""Exact native schema/current-fact inverse on actual/fresh synthetic sources."""
from contextlib import closing
import importlib.util
import json
from pathlib import Path
import sqlite3

import pytest

from app.task_storage import create_task_schema, insert_task
from app.task12_storage import insert_task12, update_task12, task12_projection
from app.task8_storage import register_task8_functions, task8_insert_parts
from tests.test_tasks12_native_source import values, insert

ROOT = Path(__file__).resolve().parents[3]
SPEC = ROOT / "docs/superpowers/specs"
HERE = Path(__file__).parent
spec = importlib.util.spec_from_file_location("task20_release_candidate", HERE / "2026-10-16-task20-release-candidate.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)
ACTUAL = json.loads((SPEC / "2026-10-16-tasks12-native-source.json").read_text())
FRESH = json.loads((SPEC / "2026-10-16-tasks12-fresh-source.json").read_text())


def source(db, variant):
    create_task_schema(db, include_workbench=True)
    native = ACTUAL if variant == "actual" else FRESH
    tables = native["tables"] | {release.CHILD: ACTUAL["retained_coupled_schema_metadata"][release.CHILD]}
    for category in ("table", "index", "trigger"):
        for definition in tables.values():
            for obj in definition["objects"]:
                if obj["type"] == category and obj["sql"] is not None:
                    db.execute(obj["sql"])


def seed(db):
    insert_task(db, "reply_tasks", dict(id=-11, conversation_id="chat", conversation_title="synthetic", single_chat=0,
                trigger_message_id="message", trigger_create_time="clock", trigger_sender="synthetic"))
    insert_task(db, "workbench_tasks", dict(id=-12, legacy_key="public-task", title="synthetic", runtime_kind="codex"))
    insert_task(db, "workbench_turns", dict(id=-13, legacy_key="public-turn", parent_id=-12,
                client_request_id="request", task_sequence=1, user_text="synthetic", status="queued"))
    insert_task(db, "reply_task_inputs", dict(id=-14, parent_id=-11, channel="email", conversation_id="chat", conversation_title="synthetic", single_chat=0,
                trigger_message_id="input", trigger_create_time="clock", trigger_sender="synthetic", trigger_text="input", business_object_key="object"))
    db.execute("update tasks set current_input_id=-14,claimed_input_id=-14 where id=-11")
    insert_task(db, "work_summary_inputs", dict(id=2,source_type="local_file",source_ref="source",payload_json='{"business_leaf":true}'))
    insert_task(db, "meeting_alignment_jobs", dict(id=3,meeting_id="meeting"))
    insert_task(db, "okr_review_requests", dict(id=4,conversation_id="chat",conversation_title="title",trigger_message_id="okr",trigger_sender="sender",trigger_text="okr",period_label="quarter",period_start="start",period_end="end"))
    insert_task(db, "weekly_okr_analysis_jobs", dict(id=5,week_end="week",manager_user_id="manager",source_digest="digest"))
    insert(db,"feedback_processing_batches",batch_id="seed")
    for kind in release.TASK20_NEW_KINDS:
        insert(db,kind,supplied=values(kind),**({"id":17} if kind in release.TASK12_AUTOINCREMENT_KINDS else {"rowid":19}))
    db.execute("insert into feedback_iteration_decision_items(rowid,decision_id,feedback_key,round_id) values(29,17,'feedback-a',17)")
    # Exact scalar codec witnesses with SQLite source affinity applied natively.
    db.execute("update email_agent_classification_tasks set result_json=?,error=?", (b'{"blob":1}',b"native-blob"))
    db.execute("update okr_review_items set objective_weight=?", (1.2345678901234567,))
    release._set_sequence(db,"tasks",700)
    for i,kind in enumerate(release.TASK12_AUTOINCREMENT_KINDS):
        release._set_sequence(db,kind,100+i)
    db.commit()


def snapshot(db):
    return {
        "schema": {name: release._declaration(db,name) for name in release.SOURCE_TABLES},
        "rows": {name: [tuple(row) for row in db.execute("select rowid,* from main." + release._quote(name) + " order by rowid")] for name in release.SOURCE_TABLES},
        "sequence": {name: release._sequence(db,name) for name in release.SOURCE_COUNTERS},
    }


@pytest.fixture(params=("actual","fresh"))
def db(request):
    with closing(sqlite3.connect(":memory:")) as connection:
        source(connection,request.param)
        register_task8_functions(connection)
        yield connection


def test_exact_nonempty_raw82_typed_inverse_and_reforward(db):
    seed(db)
    before=snapshot(db)
    db.execute("begin immediate")
    metadata=release.capture_task20_source(db)
    assert set(metadata)=={"variant","tables","sequence_present"}
    counts=release.migrate_task20_schema(db,metadata)
    assert counts["tasks"]==8
    assert all(db.execute("select 1 from sqlite_schema where name=?",(kind,)).fetchone() is None for kind in release.TASK20_NEW_KINDS)
    assert not db.execute("pragma foreign_key_check").fetchall()
    db.commit()
    db.execute("begin immediate")
    release.restore_task20_schema(db,metadata)
    db.commit()
    assert snapshot(db)==before
    assert not db.execute("pragma foreign_key_check").fetchall()
    db.execute("begin immediate")
    second=release.capture_task20_source(db)
    assert second==metadata
    release.migrate_task20_schema(db,second)
    db.commit()
    assert db.execute("pragma quick_check").fetchone()[0]=="ok"


@pytest.mark.parametrize("water",(None,0,999))
def test_empty_source_sequence_presence_is_exact(db,water):
    release._set_sequence(db,"tasks",water)
    for kind in release.TASK12_AUTOINCREMENT_KINDS:
        release._set_sequence(db,kind,water)
    db.commit()
    before=snapshot(db)
    db.execute("begin")
    metadata=release.capture_task20_source(db)
    release.migrate_task20_schema(db,metadata)
    release.restore_task20_schema(db,metadata)
    db.commit()
    assert snapshot(db)==before


def test_current_target_writes_survive_inverse_and_native_reforward(db):
    seed(db)
    db.execute("begin")
    metadata=release.capture_task20_source(db)
    release.migrate_task20_schema(db,metadata)
    update_task12(db,"email_agent_classification_tasks",19,{"status":"done","input_json":"{}","result_json":'{"business_result":1}'})
    fresh=insert_task12(db,"wechat_memory_import_jobs",values("wechat_memory_import_jobs","current"))
    assert fresh.lastrowid==105
    parts=task8_insert_parts("work_summary_inputs",{"source_type":"reply_attempt","source_ref":"current","payload_json":'{"receipt":"retained"}'})
    physical=db.execute("insert into tasks("+parts.columns+") values("+parts.values+")",parts.parameters).lastrowid
    assert physical>700
    release.restore_task20_schema(db,metadata)
    assert db.execute("select status,input_json,result_json from email_agent_classification_tasks where rowid=19").fetchone()==("done","{}",'{"business_result":1}')
    assert db.execute("select source_ref,payload_json from tasks where id=?",(physical,)).fetchone()==("current",'{"receipt":"retained"}')
    assert db.execute("select id from wechat_memory_import_jobs where import_run_id='import-current'").fetchone()==(105,)
    assert release._sequence(db,"tasks")==physical
    # Real native predecessor factories/writers, with new source facts retained.
    insert(db,"wechat_memory_import_jobs",suffix="predecessor")
    assert release._sequence(db,"wechat_memory_import_jobs")==106
    current=snapshot(db)
    second=release.capture_task20_source(db)
    release.migrate_task20_schema(db,second)
    assert db.execute("select id from ("+task12_projection("wechat_memory_import_jobs")+") where import_run_id='import-predecessor'").fetchone()==(106,)
    release.restore_task20_schema(db,second)
    assert snapshot(db)==current


def test_component_obeys_caller_rollback_on_late_failure(db):
    seed(db)
    before=snapshot(db)
    db.execute("create table witness(value text)")
    db.commit()
    db.execute("begin")
    db.execute("insert into witness values('earlier caller work')")
    metadata=release.capture_task20_source(db)
    release.migrate_task20_schema(db,metadata)
    assert db.in_transaction
    db.rollback()
    assert snapshot(db)==before
    assert db.execute("select * from witness").fetchall()==[]
    assert db.execute("select name from temp.sqlite_schema").fetchall()==[]


def test_external_physical_foreign_key_and_declarations_stay_unchanged(db):
    seed(db)
    db.execute("create table external_witness(id integer primary key, task_id integer references tasks(id))")
    db.execute("insert into external_witness values(1,-11)")
    db.commit()
    declaration=release._declaration(db,"external_witness")
    db.execute("begin")
    metadata=release.capture_task20_source(db)
    release.migrate_task20_schema(db,metadata)
    assert release._declaration(db,"external_witness")==declaration
    assert db.execute("select e.id,t.record_type,t.legacy_key from external_witness e join tasks t on t.id=e.task_id").fetchall()==[(1,"reply_tasks","-11")]
    assert not db.execute("pragma foreign_key_check").fetchall()
    release.restore_task20_schema(db,metadata)
    assert release._declaration(db,"external_witness")==declaration
    assert not db.execute("pragma foreign_key_check").fetchall()


def test_declaration_mismatch_does_not_mutate_source(db):
    db.execute("begin")
    metadata=release.capture_task20_source(db)
    db.execute("create index unrelated_shape_change on oa_notification_events(status)")
    before=snapshot(db)
    with pytest.raises(ValueError,match="source declarations"):
        release.migrate_task20_schema(db,metadata)
    assert snapshot(db)==before


def test_requires_caller_fk_off_transaction(db):
    with pytest.raises(sqlite3.ProgrammingError,match="caller FK-off transaction"):
        release.capture_task20_source(db)
    db.execute("pragma foreign_keys=on")
    db.execute("begin")
    with pytest.raises(sqlite3.ProgrammingError,match="caller FK-off transaction"):
        release.capture_task20_source(db)


@pytest.mark.parametrize("name",release.SOURCE_COUNTERS)
@pytest.mark.parametrize("water",(None,0,2**63-1))
def test_independent_empty_allocator_states(db,name,water):
    release._set_sequence(db,name,water)
    db.commit()
    before=snapshot(db)
    db.execute("begin")
    metadata=release.capture_task20_source(db)
    release.migrate_task20_schema(db,metadata)
    release.restore_task20_schema(db,metadata)
    assert snapshot(db)==before


def test_old_absent_allocator_becomes_current_positive_after_new_write(db):
    db.execute("begin")
    metadata=release.capture_task20_source(db)
    assert metadata["sequence_present"]["tasks"] is False
    release.migrate_task20_schema(db,metadata)
    insert_task12(db,"wechat_memory_import_jobs",values("wechat_memory_import_jobs"))
    parts=task8_insert_parts("work_summary_inputs",{"source_type":"local_file","source_ref":"current","payload_json":"{}"})
    ident=db.execute("insert into tasks("+parts.columns+") values("+parts.values+")",parts.parameters).lastrowid
    assert ident==2
    release.restore_task20_schema(db,metadata)
    assert release._sequence(db,"tasks")==2
    assert release._sequence(db,"wechat_memory_import_jobs")==1
    assert db.execute("select source_ref from tasks where id=2").fetchone()==("current",)


def test_exhausted_shared_physical_stream_preserves_separate_native_auto(db):
    release._set_sequence(db,"tasks",2**63-1)
    db.commit()
    db.execute("begin")
    metadata=release.capture_task20_source(db)
    release.migrate_task20_schema(db,metadata)
    result=insert_task12(db,"wechat_memory_import_jobs",values("wechat_memory_import_jobs"))
    assert result.lastrowid==1
    release.restore_task20_schema(db,metadata)
    assert release._sequence(db,"tasks")==2**63-1
    assert db.execute("select id from wechat_memory_import_jobs").fetchall()==[(1,)]


def test_temporary_collision_and_later_native_error_remain_caller_owned(db):
    db.execute("create table witness(value text check(value<>'invalid'))")
    db.commit()
    db.execute("begin")
    db.execute("insert into witness values('earlier')")
    metadata=release.capture_task20_source(db)
    db.execute("create temp table task20_release_tasks(value text)")
    with pytest.raises(ValueError,match="TEMP relation or staging name collision"):
        release.migrate_task20_schema(db,metadata)
    assert db.execute("select * from witness").fetchall()==[("earlier",)]
    db.rollback()
    assert db.execute("select * from witness").fetchall()==[]
    db.execute("begin")
    db.execute("insert into witness values('earlier')")
    metadata=release.capture_task20_source(db)
    release.migrate_task20_schema(db,metadata)
    with pytest.raises(sqlite3.IntegrityError,match="CHECK constraint failed"):
        db.execute("insert into witness values('invalid')")
    assert db.in_transaction
    db.rollback()
    assert db.execute("select * from witness").fetchall()==[]
    assert not db.in_transaction
    db.execute("begin")
    assert release.capture_task20_source(db)==metadata
    assert not db.execute("select name from temp.sqlite_schema").fetchall()


@pytest.mark.parametrize("name",(*release.SOURCE_TABLES, *("task20_release_"+name for name in release.SOURCE_TABLES)))
@pytest.mark.parametrize("direction",("forward","inverse"))
def test_all_finite_temp_collisions_rejected_before_main_ddl(db,name,direction):
    seed(db)
    db.execute("begin")
    metadata=release.capture_task20_source(db)
    if direction=="inverse":
        release.migrate_task20_schema(db,metadata)
    db.commit()
    db.execute("create temp table "+release._quote(name.upper())+"(witness text)")
    db.execute("insert into temp."+release._quote(name.upper())+" values('untouched')")
    db.commit()
    schema=list(db.execute("select type,name,tbl_name,sql from main.sqlite_schema order by type,name"))
    sequence=list(db.execute("select name,seq from main.sqlite_sequence order by name"))
    rows=list(db.execute("select * from main.tasks order by id"))
    db.execute("begin")
    with pytest.raises(ValueError,match="TEMP relation or staging name collision"):
        if direction=="forward":
            release.migrate_task20_schema(db,metadata)
        else:
            release.restore_task20_schema(db,metadata)
    assert db.in_transaction
    assert list(db.execute("select type,name,tbl_name,sql from main.sqlite_schema order by type,name"))==schema
    assert list(db.execute("select name,seq from main.sqlite_sequence order by name"))==sequence
    assert list(db.execute("select * from main.tasks order by id"))==rows
    assert db.execute("select * from temp."+release._quote(name.upper())).fetchall()==[("untouched",)]


@pytest.mark.parametrize("conflicting_names",(False,True))
def test_unrelated_temp_auto_counter_does_not_redirect_main_migration(db,conflicting_names):
    seed(db)
    before=snapshot(db)
    db.execute("create temp table unrelated_scratch(id integer primary key autoincrement)")
    db.execute("insert into temp.unrelated_scratch default values")
    if conflicting_names:
        db.executemany("insert into temp.sqlite_sequence(name,seq) values(?,?)",
                       [("tasks",9000),("tasks:published",8000),
                        *(("tasks:"+kind,7000) for kind in release.TASK12_AUTOINCREMENT_KINDS)])
    db.commit()
    temp=list(db.execute("select name,seq from temp.sqlite_sequence order by name"))
    db.execute("begin")
    metadata=release.capture_task20_source(db)
    release.migrate_task20_schema(db,metadata)
    assert release._sequence(db,"tasks:published")==700
    assert db.execute("select min(id) from main.tasks where record_type not in ("+",".join(map(repr,release.TASK8_KINDS))+")").fetchone()[0]>700
    assert list(db.execute("select name,seq from temp.sqlite_sequence order by name"))==temp
    release.restore_task20_schema(db,metadata)
    assert snapshot(db)==before
    assert list(db.execute("select name,seq from temp.sqlite_sequence order by name"))==temp
