"""Actual 45f 19.9 source, exact current-fact inverse and strict old cold open."""
from contextlib import closing
import io
from pathlib import Path
import sqlite3
import subprocess
import tarfile

import pytest

from app.logical_run_schema import source_row_projection
from app.result_schema_release import _markers
from app.store_schema_release import prepare_database_schema_release
from tests.test_shared_action_release import _old_process

ROOT = Path(__file__).resolve().parents[1]
SOURCE_HEAD = "45f3324be3c568b9f91709c25306e19d73a15087"


@pytest.fixture(scope="module")
def archived_source(tmp_path_factory):
    root = tmp_path_factory.mktemp("actual-store19-9")
    archive = subprocess.check_output(["git", "archive", SOURCE_HEAD, "app", "tests", "scripts"], cwd=ROOT)
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(root, filter="data")
    return root


def seed(root, path, variant="fresh_memory_fk"):
    _old_process(root, r'''
from pathlib import Path
import sys,sqlite3
from app.store import AutoReplyStore
from app.email_store import EmailStore
from app.run_storage import insert_run
from app.state_storage import update_state_rows
s=AutoReplyStore(Path(sys.argv[1]));EmailStore(s.path)
if sys.argv[2] == "actual_no_memory_fk":
 from app import action_storage as actions
 from app.email_canonical_storage import ACTION_OPTIONS
 with sqlite3.connect(s.path) as db:
  assert db.execute("select count(*) from actions").fetchone()[0] == 0
  objects=actions._current_objects(reply_variant="actual_no_memory_fk",
   inline_legacy_owner=True,physical_provenance=True,compact_payload=True,canonical_email=True,
   selection_references=True,result_references=True,execution_owner=True,sent_result_references=True,
   shared_lifecycle=True,specialized_claims=True,postfix_owner=True,**ACTION_OPTIONS)
  db.execute("drop table actions")
  for obj in sorted(objects,key=lambda o:o["type"]!="table"):db.execute(obj["sql"])
s.upsert_codex_session_search_index(session_id="source-metadata", source_type="meeting_alignment", source_id="7", title="Original index")
summary=s.enqueue_work_summary_input("local_file","c2-source","{}")
meeting=s.upsert_meeting_alignment_job(meeting_id="c2-meeting", title="Source",
 source_json="{}",participants_json="[]",ended_at="",eligible_at="",status="no_action")
request=s.create_okr_review_request(conversation_id="c",conversation_title="Source",
 trigger_message_id="m",trigger_sender="sender",trigger_sender_user_id="owner",
 trigger_text="review",period_label="period",period_start="",period_end="",okr_source_json="{}")
s.enqueue_reply_task(conversation_id="reply-c",conversation_title="Reply",single_chat=True,
 trigger_message_id="reply-m",trigger_create_time="",trigger_sender="sender",trigger_text="body",trigger_message_json="{}")
with sqlite3.connect(s.path) as db:
 db.execute("begin")
 update_state_rows(db,'service_state',{'updated_at':b'original-marker-clock'},
  where_sql='key=?',parameters=('store_schema_version',))
 parents={kind:db.execute("select id from tasks where record_type=? order by id limit 1",(kind,)).fetchone()[0]
  for kind in ("work_summary_inputs","meeting_alignment_jobs","okr_review_requests","reply_tasks")}
 insert_run(db,"agent_runs",dict(id=7,task_id=parents["reply_tasks"],role="consumer",
  execution_generation="source-generation"))
 insert_run(db,"task_agent_runs",dict(id=100,legacy_key=7,summary_input_id=summary,
  status="source-status",session_id="task-session",error=b"error\x00\xff",
  projection_json=' { "source_unique_receipt" : 1 } ',finished_at=b"finish\x00",
  updated_at="task-update",created_at="task-create"))
 insert_run(db,"meeting_alignment_runs",dict(id=101,legacy_key=7,task_id=parents["meeting_alignment_jobs"],
  status="ready_to_send",session_id="meeting-session",transcript_start=3,transcript_end=9,
  error="native failure text",finished_at="meeting-finish",updated_at="meeting-update",created_at="meeting-create"))
 insert_run(db,"okr_review_runs",dict(id=102,legacy_key=7,request_id=request,
  session_id="okr-session",transcript_start=2,transcript_end=7,created_at="okr-create"))
 insert_run(db,"task_agent_runs",dict(id=103,legacy_key=8,summary_input_id=summary,
  session_id="no-attempt",status="failed",error="unavailable"))
 insert_run(db,"meeting_alignment_runs",dict(id=104,legacy_key=8,task_id=parents["meeting_alignment_jobs"],
  status="no_action",session_id="legacy-only",transcript_start=2,transcript_end=7))
 db.execute("update sqlite_sequence set seq=9901 where name='tasks'")
 insert_run(db,"meeting_alignment_runs",dict(id=105,legacy_key=2298,task_id=9901,job_id=4908,
  status="retry",session_id="orphan-session",error="orphan"))
 for i,(kind,key,session) in enumerate((("task","7","task-session"),
  ("task","7:decision_repair.1","repair-session"),("task","7:deadline_backfill.1","todo-operation"),
  ("meeting","7","meeting-session"))):
  insert_run(db,"agent_runtime_attempts",dict(id=200+i,legacy_key=501+i,
   workload_kind=kind,workload_key=key,attempt_number=1,route_name="source-route",
   runtime_kind="codex_cli",credential_mode="local_oauth",model="source-model",
   status="completed",session_id=session,transcript_start=3,transcript_end=9))
 db.execute("update sqlite_sequence set seq=700 where name='runs'")
 db.execute("update sqlite_sequence set seq=699 where name='runs:published'")
''', path, variant)


def cold(root, path):
    _old_process(root, """
from pathlib import Path
import sys
from app.store import AutoReplyStore
from app.email_store import EmailStore
from app.workbench.store import WorkbenchStore
p=Path(sys.argv[1]);AutoReplyStore(p);EmailStore(p);WorkbenchStore(p)
""", path)


def snapshot(path, *, owned):
    with closing(sqlite3.connect(path)) as db:
        projection = source_row_projection(owned=owned)
        columns = [column[0] for column in db.execute(projection).description]
        fields = ",".join(columns) + "," + ",".join(f"typeof({column})" for column in columns)
        return (
            tuple(db.execute(f"select {fields} from ({projection}) order by id")),
            tuple(db.execute("select rowid,name,seq from sqlite_sequence order by rowid")),
            tuple(sorted(db.execute("pragma foreign_key_check"))),
            tuple(
                (table, tuple(db.execute(
                    "select " + ",".join(f'"{r[1]}"' for r in db.execute(f'pragma table_info("{table}")'))
                    + f' from "{table}" order by rowid'
                )))
                for (table,) in db.execute("select name from sqlite_schema where type='table' "
                                          "and name not in ('runs','state','sqlite_sequence') order by name")
            ),
        )


def new_facts(path):
    from app.store import AutoReplyStore
    store = AutoReplyStore(path)
    summary = store.enqueue_work_summary_input("local_file", "c2-after", "{}")
    run = store.create_logical_run("task_agent_runs", summary, codex_session_id="after-task")
    meeting = store.upsert_meeting_alignment_job(
        meeting_id="after-meeting", title="After", source_json="{}", participants_json="[]",
        ended_at="", eligible_at="", status="no_action",
    )
    mr = store.create_logical_run("meeting_alignment_runs", meeting, status="no_action",
                                  codex_session_id="after-meeting", codex_transcript_end_line=9)
    request = store.create_okr_review_request(
        conversation_id="after", conversation_title="After", trigger_message_id="after",
        trigger_sender="sender", trigger_sender_user_id="owner", trigger_text="review",
        period_label="after", period_start="", period_end="", okr_source_json="{}",
    )
    okr = store.create_logical_run("okr_review_runs", request, codex_session_id="after-okr",
                                   codex_transcript_end_line=9)
    assert run > 700 and mr > run and okr > mr
    return run, mr, okr


@pytest.mark.parametrize("variant", ["fresh_memory_fk", "actual_no_memory_fk"])
def test_actual_source_collisions_orphan_and_current_fact_inverse_cold_reforward(archived_source, tmp_path, variant):
    path = tmp_path / "release.sqlite3"
    seed(archived_source, path, variant)
    before = snapshot(path, owned=False)
    assert before[2] == (("runs", 105, "tasks", 1),)
    release = prepare_database_schema_release(ROOT, path)
    assert type(release).__name__ == "LogicalRunSchemaRelease"
    release.publish()
    assert snapshot(path, owned=True) == before
    from app.store import AutoReplyStore
    store = AutoReplyStore(path)
    assert store.get_agent_run(100, load_events=False) is None
    assert store.get_agent_run(7, load_events=False).id == 7
    with store._connect() as db:
        assert [tuple(row) for row in db.execute(
            "select source_type,id,legacy_key from runs where record_type='agent_runs' "
            "and cast(legacy_key as integer)=7 order by id"
        )] == [(None, 7, "7"), ("task_agent_runs", 100, "7"),
               ("meeting_alignment_runs", 101, "7"), ("okr_review_runs", 102, "7")]
        assert tuple(db.execute(
            "select status,error,completed_at,updated_at,role from runs where id=102"
        ).fetchone()) == (None,) * 5
        assert tuple(db.execute("select task_id,job_id from runs where id=105").fetchone()) == (9901, 4908)
        assert not db.execute(
            "select 1 from runs where record_type in ('task_agent_runs','meeting_alignment_runs','okr_review_runs')"
        ).fetchone()
    new_facts(path)
    current = snapshot(path, owned=True)
    release.rollback()
    assert snapshot(path, owned=False) == current
    cold(archived_source, path)
    assert snapshot(path, owned=False) == current
    again = prepare_database_schema_release(ROOT, path)
    again.publish()
    again.finalize()
    assert snapshot(path, owned=True) == current


def test_failed_target_load_retains_new_facts_in_inverse(archived_source, tmp_path, monkeypatch):
    from app import store_schema_release
    path = tmp_path / "failed.sqlite3"
    seed(archived_source, path)
    with closing(sqlite3.connect(path)) as db:
        markers = _markers(db)
    release = prepare_database_schema_release(ROOT, path)
    current = None

    def fail(root, database):
        nonlocal current
        new_facts(database)
        current = snapshot(database, owned=True)
        raise RuntimeError("injected current loader failure")

    monkeypatch.setattr(store_schema_release, "_migrate_store", fail)
    with pytest.raises(RuntimeError, match="injected"):
        release.publish()
    release.rollback()
    release.rollback()
    assert snapshot(path, owned=False) == current
    with closing(sqlite3.connect(path)) as db:
        assert _markers(db) == markers
    cold(archived_source, path)


def test_unknown_source_declaration_refuses_before_write(archived_source, tmp_path):
    path = tmp_path / "unknown.sqlite3"
    seed(archived_source, path)
    with closing(sqlite3.connect(path)) as db:
        db.execute("create index unknown_logical_run_index on runs(session_id)")
        db.commit()
    before = path.read_bytes()
    with pytest.raises((ValueError, sqlite3.IntegrityError), match="declaration"):
        prepare_database_schema_release(ROOT, path)
    assert path.read_bytes() == before


def test_historical_run_and_action_factories_stay_exact(archived_source):
    code = """
import hashlib,json
from app.run_storage import _declared_run_objects
from app.runs11_schema_release import _target_objects
result=[_declared_run_objects(False),_declared_run_objects(True),
 _declared_run_objects(True,True),_target_objects()]
from app.action_storage import _current_objects
from app.prepared_action_schema import prepared_action_objects
result += [_current_objects(), prepared_action_objects('fresh_memory_fk'),
           prepared_action_objects('actual_no_memory_fk')]
print(json.dumps([hashlib.sha256(json.dumps(x,sort_keys=True).encode()).hexdigest() for x in result]))
"""
    assert _old_process(archived_source, code) == _old_process(ROOT, code)


def test_inverse_requires_original_meeting_reservation_before_write(archived_source, tmp_path):
    from app.logical_run_schema import restore_logical_runs
    path = tmp_path / "lost-reservation.sqlite3"
    seed(archived_source, path)
    prepare_database_schema_release(ROOT, path).publish()
    with closing(sqlite3.connect(path)) as db:
        db.execute("update sqlite_sequence set seq=9900 where name='tasks'")
        db.commit()
        db.execute("begin immediate")
        changed = db.total_changes
        with pytest.raises(ValueError, match="allocator is not reserved"):
            restore_logical_runs(db, {})
        assert db.total_changes == changed
        db.rollback()


def test_source_unrepresentable_typed_parent_refuses_before_write(archived_source, tmp_path):
    from app.logical_run_schema import migrate_logical_runs
    path = tmp_path / "bad-parent.sqlite3"
    seed(archived_source, path)
    with closing(sqlite3.connect(path)) as db:
        db.execute("update runs set summary_input_id=? where id=100", (b"1",))
        db.commit()
        db.execute("begin immediate")
        changed = db.total_changes
        with pytest.raises(ValueError, match="exact SQL value/type"):
            migrate_logical_runs(db)
        assert db.total_changes == changed
        db.rollback()


def test_actual_source_dao_and_operation_lease_differential(archived_source, tmp_path):
    import inspect
    from tests import logical_run_caller_fixture as calls
    source, target = tmp_path / "source-dao.sqlite3", tmp_path / "target-dao.sqlite3"
    seed(archived_source, source)
    with closing(sqlite3.connect(source)) as db, closing(sqlite3.connect(target)) as other:
        db.backup(other)
    release = prepare_database_schema_release(ROOT, target)
    release.publish()
    def execute(root, path, owned):
        return _old_process(root, inspect.getsource(calls)
            + f"\nimport json,sys\nprint(json.dumps(exercise(sys.argv[1],{owned})))", path)
    expected = execute(archived_source, source, False)
    assert execute(ROOT, target, True) == expected
    # Native clocks and original SQLite storage classes are compared as facts,
    # not normalized JSON from reader DTOs.
    assert snapshot(target, owned=True)[:3] == snapshot(source, owned=False)[:3]
    release.rollback()
    cold(archived_source, target)
    assert snapshot(target, owned=False)[:3] == snapshot(source, owned=False)[:3]


@pytest.mark.parametrize("owned", [False, True])
def test_external_view_of_retired_column_refuses_before_write(archived_source, tmp_path, owned):
    from app.logical_run_schema import migrate_logical_runs, restore_logical_runs
    path = tmp_path / "dependent-view.sqlite3"
    seed(archived_source, path)
    if owned:
        prepare_database_schema_release(ROOT, path).publish()
    with closing(sqlite3.connect(path)) as db:
        # This view is outside the owned table and would become invalid after
        # moving the corresponding source/target discriminator contract.
        column = "summary_input_id" if not owned else "does_not_exist"
        db.execute(f"create view unknown_run_consumer as select {column} from runs")
        db.commit()
        db.execute("begin immediate")
        before = db.total_changes
        with pytest.raises(sqlite3.OperationalError, match="no such column"):
            restore_logical_runs(db, {}) if owned else migrate_logical_runs(db)
        assert db.total_changes == before
        db.rollback()
