"""Meeting's actual 11.2 declarations survive the paired 4B conversion."""

from contextlib import closing
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from tests.test_task_storage import fingerprint, source_database


def meeting_source(path):
    db = source_database(path)
    db.executescript("""
        create table meeting_memory_write_events (
            id integer primary key autoincrement,
            meeting_job_id integer not null unique,
            payload_json text not null,
            status text not null default 'pending',
            attempts integer not null default 0,
            available_at text not null default '',
            error text not null default '',
            memory_id text not null default '',
            created_at text not null default current_timestamp,
            updated_at text not null default current_timestamp,
            execution_generation text not null default '',
            lease_owner text not null default '',
            lease_expires_at text not null default '',
            started_at text not null default '',
            foreign key(meeting_job_id) references meeting_alignment_jobs(id)
        );
        create index idx_meeting_memory_write_events_due
            on meeting_memory_write_events(status,available_at,id);
        create table meeting_alignment_delivery_claims (
            job_id integer primary key,
            claim_token text not null,
            foreign key(job_id) references meeting_alignment_jobs(id) on delete cascade
        );
        update reply_tasks set id=4908;
        update reply_task_inputs set reply_task_id=4908;
        update receipt set reply_task_id=4908;
        update runs set task_id=4908 where record_type='agent_runs';
        update sqlite_sequence set seq=4908 where name='reply_tasks';
        insert into meeting_alignment_jobs(id,meeting_id,title,source_json,participants_json,ended_at,eligible_at,status,attempts,locked_at,available_at,error,decision_json,target_kind,target_id,target_title,mentions_json,final_message,send_result_json,created_at,updated_at,calendar_summary_status,calendar_summary_result_json)
        values(1,'meeting-one',x'00ff',' { "source": [2, 1] } ',' [ "one", "two" ] ','ended','eligible','ready_to_send',1.25,null,'available','original error',' { "adopted": true } ','group','public target','target title',' [ "user" ] ','adopted final message',' { "messageId": "receipt" } ','created','updated','failed',' { "calendar": "receipt" } '),
              (2,'meeting-two','','{}','[]','','','waiting',0,'lock','','','{}','','','','[]','','{}','created','','not_started','{}');
        insert into meeting_alignment_runs(id,job_id,codex_session_id,codex_transcript_start_line,codex_transcript_end_line,status,error,created_at,finished_at,updated_at)
        values(1,1,'exact-session',3,27,'source custom status','run error','run-created','',''),
              (2298,4908,'orphan-session',0,0,'retry','','orphan-created','','');
        insert into meeting_memory_write_events(meeting_job_id,payload_json,memory_id,execution_generation)
        values(1,' { "existing": "memory receipt" } ','memory-receipt','source-generation');
        insert into meeting_alignment_delivery_claims values(1,'existing-delivery-token'),(2,'second-token');
        update sqlite_sequence set seq=7000 where name='meeting_alignment_jobs';
        update sqlite_sequence set seq=8000 where name='meeting_alignment_runs';
    """)
    db.commit()
    return db


def forward(db):
    from app.task_storage import capture_task_schema, migrate_task_schema
    state = capture_task_schema(db)
    db.execute("begin immediate")
    migrate_task_schema(db)
    db.execute("update service_state set value='2026-10-11.3'")
    db.commit()
    return state


def inverse(db, state):
    from app.task_storage import restore_task_schema
    db.execute("begin immediate")
    restore_task_schema(db, state)
    db.execute("update service_state set value='2026-10-11.2'")
    db.commit()


def test_exact_projection_orphan_reservation_and_full_inverse(tmp_path):
    from app import run_storage, task_storage
    with closing(meeting_source(tmp_path / "source.db")) as db:
        before = fingerprint(db)
        state = forward(db)
        assert not db.execute("select name from sqlite_master where type='table' and name in ('meeting_alignment_jobs','meeting_alignment_runs')").fetchall()
        for table, projection in (
            ("meeting_alignment_jobs", task_storage.MEETING_JOB_SQL),
            ("meeting_alignment_runs", run_storage.MEETING_RUN_SQL),
        ):
            assert db.execute(f"select * from ({projection}) order by id").fetchall() == before["rows"][table]
        parent = task_storage.task_physical_id(db, "meeting_alignment_jobs", 1)
        assert db.execute("select task_id,job_id from runs where record_type='meeting_alignment_runs' and legacy_key='1'").fetchone() == (parent, None)
        physical, raw = db.execute("select task_id,job_id from runs where record_type='meeting_alignment_runs' and legacy_key='2298'").fetchone()
        assert raw == 4908 and physical > db.execute("select max(id) from tasks").fetchone()[0]
        assert db.execute("select record_type from tasks where id=4908").fetchone() == ("reply_tasks",)
        assert not db.execute("select 1 from tasks where id=?", (physical,)).fetchone()
        assert db.execute("select seq from sqlite_sequence where name='tasks'").fetchone()[0] == physical
        assert len(db.execute("pragma foreign_key_check").fetchall()) == 1
        assert db.execute("select meeting_job_id,payload_json,memory_id from meeting_memory_write_events").fetchone() == (parent, ' { "existing": "memory receipt" } ', "memory-receipt")
        assert db.execute("select claim_token from meeting_alignment_delivery_claims where job_id=?", (parent,)).fetchone() == ("existing-delivery-token",)
        inverse(db, state)
        assert fingerprint(db) == before
        state = forward(db)
        inverse(db, state)
        assert fingerprint(db) == before


def test_defaults_nullable_scalars_and_no_process_cache_columns(tmp_path):
    from app.run_storage import insert_run, MEETING_RUN_SQL
    from app.task_storage import insert_task, MEETING_JOB_SQL
    with closing(meeting_source(tmp_path / "defaults.db")) as db:
        source_job = db.execute("insert into meeting_alignment_jobs(meeting_id) values('defaults')").lastrowid
        source_run = db.execute("insert into meeting_alignment_runs(job_id,status) values(?,'custom')", (source_job,)).lastrowid
        job_defaults = db.execute("select * from meeting_alignment_jobs where id=?", (source_job,)).fetchone()
        run_defaults = db.execute("select * from meeting_alignment_runs where id=?", (source_run,)).fetchone()
        db.execute("delete from meeting_alignment_runs where id=?", (source_run,))
        db.execute("delete from meeting_alignment_jobs where id=?", (source_job,))
        db.commit()
        forward(db)
        job = insert_task(db, "meeting_alignment_jobs", dict(meeting_id="defaults")).lastrowid
        run = insert_run(db, "meeting_alignment_runs", dict(task_id=job, status="custom")).lastrowid
        actual_job = db.execute(f"select * from ({MEETING_JOB_SQL}) where id=?", (job,)).fetchone()
        actual_run = db.execute(f"select * from ({MEETING_RUN_SQL}) where id=?", (run,)).fetchone()
        assert actual_job[1:19] == job_defaults[1:19]
        assert actual_job[21:] == job_defaults[21:]
        assert actual_run[2:10] == run_defaults[2:10]
        assert actual_run[11:] == ("", "")
        fields = {row[1] for row in db.execute("pragma table_info(runs)")}
        assert not fields.intersection({"decision_json", "audit_tool_events_json", "audit_summary"})
        assert db.execute("select locked_at from tasks where id=?", (job,)).fetchone() == (None,)
        with pytest.raises(sqlite3.IntegrityError):
            insert_task(db, "meeting_alignment_jobs", dict(meeting_id="null", source_json=None))
        with pytest.raises(sqlite3.IntegrityError):
            insert_run(db, "meeting_alignment_runs", dict(task_id=job))


def test_same_missing_public_parent_groups_and_new_ids_skip_reserved_holes(tmp_path):
    from app.run_storage import insert_run
    from app.task_storage import insert_task
    with closing(meeting_source(tmp_path / "orphans.db")) as db:
        db.execute("insert into meeting_alignment_runs(id,job_id,status) values(2299,4908,'failed'),(2300,7,'running')")
        db.commit()
        forward(db)
        links = db.execute("select job_id,task_id from runs where record_type='meeting_alignment_runs' and job_id is not null order by job_id,cast(legacy_key as integer)").fetchall()
        assert links[0][0] == 7 and links[1][0] == links[2][0] == 4908
        assert links[0][1] < links[1][1] == links[2][1]
        new = insert_task(db, "meeting_alignment_jobs", dict(meeting_id="next")).lastrowid
        assert new > max(row[1] for row in links)
        db.execute("update runs set status='running' where record_type='meeting_alignment_runs' and legacy_key='2298'")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("update runs set status='running' where record_type='meeting_alignment_runs' and legacy_key='2299'")
        with pytest.raises(sqlite3.IntegrityError):
            insert_run(db, "meeting_alignment_runs", dict(task_id=links[0][1], job_id=7, status="running"))


def test_real_parent_namespace_raw_orphan_contract_and_child_fk_actions(tmp_path):
    from app.run_storage import insert_run
    from app.task_storage import insert_task, task_physical_id, validate_meeting_run_references
    with closing(meeting_source(tmp_path / "relations.db")) as db:
        forward(db)
        db.execute("pragma foreign_keys=on")
        parent = task_physical_id(db, "meeting_alignment_jobs", 1)
        with pytest.raises(sqlite3.IntegrityError, match="meeting"):
            insert_run(db, "meeting_alignment_runs", dict(task_id=4908, status="running"))
        with pytest.raises(sqlite3.IntegrityError, match="meeting"):
            insert_run(db, "meeting_alignment_runs", dict(task_id=parent, job_id=1, status="running"))
        with pytest.raises(sqlite3.IntegrityError, match="reply task"):
            insert_run(db, "agent_runs", dict(task_id=parent, execution_generation="bad", role="consumer"))
        for sql in (
            "insert into meeting_alignment_delivery_claims values(4908,'wrong')",
            "insert into meeting_memory_write_events(meeting_job_id,payload_json) values(4908,'{}')",
        ):
            with pytest.raises(sqlite3.IntegrityError, match="meeting"):
                db.execute(sql)
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("delete from tasks where id=?", (parent,))
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("insert into meeting_memory_write_events(meeting_job_id,payload_json) values(?,'{}')", (parent,))
        second = task_physical_id(db, "meeting_alignment_jobs", 2)
        db.execute("delete from tasks where id=?", (second,))
        assert not db.execute("select 1 from meeting_alignment_delivery_claims where job_id=?", (second,)).fetchone()
        with pytest.raises(sqlite3.IntegrityError):
            insert_task(db, "meeting_alignment_jobs", dict(meeting_id="meeting-one"))
        validate_meeting_run_references(db)
        assert len(db.execute("pragma foreign_key_check").fetchall()) == 1


@pytest.mark.parametrize("column,value", [("decision_json", '{"valuable":true}'), ("audit_tool_events_json", '["valuable"]'), ("audit_summary", "valuable diagnostic")])
def test_nonempty_retired_cache_rejected_before_any_write(tmp_path, column, value):
    from app.task_storage import migrate_task_schema
    with closing(meeting_source(tmp_path / "cache.db")) as db:
        db.execute(f"update meeting_alignment_runs set {column}=? where id=1", (value,))
        db.commit()
        before = fingerprint(db)
        db.execute("begin immediate")
        with pytest.raises(ValueError, match="empty retired cache"):
            migrate_task_schema(db)
        assert fingerprint(db) == before
        db.rollback()


@pytest.mark.parametrize("kind", ["default", "fk", "status_check", "index_literal"])
def test_actual_source_declarations_are_required_before_any_write(tmp_path, kind):
    from app.task_storage import migrate_task_schema
    with closing(meeting_source(tmp_path / "declaration.db")) as db:
        name = "idx_meeting_alignment_runs_active_job" if kind == "index_literal" else "meeting_alignment_runs"
        sql = db.execute("select sql from sqlite_master where name=?", (name,)).fetchone()[0]
        replacement = {
            "default": ("updated_at text not null default ''", "updated_at text not null default current_timestamp"),
            "fk": ("references meeting_alignment_jobs(id)", "references meeting_alignment_jobs(id) on delete cascade"),
            "status_check": ("status text not null,", "status text not null check(status in ('running','retry','failed')),"),
            "index_literal": ("status='running'", "status='RUNNING'"),
        }[kind]
        assert replacement[0] in sql
        db.execute("pragma writable_schema=on")
        db.execute("update sqlite_master set sql=? where name=?", (sql.replace(*replacement), name))
        db.execute("pragma writable_schema=off")
        db.commit()
        before = fingerprint(db)
        db.execute("begin immediate")
        with pytest.raises(ValueError, match="source declaration"):
            migrate_task_schema(db)
        assert fingerprint(db) == before
        db.rollback()


def test_release_normalizes_only_original_orphan_and_inverse_keeps_new_facts(tmp_path):
    from app.run_storage import insert_run
    from app.task_storage import insert_task
    from app.task_schema_release import prepare_task_schema_release
    path = tmp_path / "release.db"
    meeting_source(path).close()
    release = prepare_task_schema_release(tmp_path, path, ("2026-10-11.2", "source-time"))
    def migrate(_root, path):
        with closing(sqlite3.connect(path)) as db:
            forward(db)
    release.migrate = migrate
    release.publish()
    with closing(sqlite3.connect(path)) as db, db:
        job = insert_task(db, "meeting_alignment_jobs", dict(meeting_id="replacement", status="sent", final_message="new adopted message", send_result_json=' {"messageId": "new receipt"} ', calendar_summary_status="updated", calendar_summary_result_json=' {"calendar": "new receipt"} ')).lastrowid
        run = insert_run(db, "meeting_alignment_runs", dict(task_id=job, status="sent", session_id="replacement-session", transcript_start=4, transcript_end=21)).lastrowid
        db.execute("insert into meeting_memory_write_events(meeting_job_id,payload_json,memory_id,execution_generation) values(?,'{}','new-memory','replacement-generation')", (job,))
        db.execute("insert into meeting_alignment_delivery_claims values(?,'replacement-token')", (job,))
        removed_run = insert_run(db, "meeting_alignment_runs", dict(task_id=job, status="failed")).lastrowid
        db.execute("delete from runs where id=?", (removed_run,))
        removed_job = insert_task(db, "meeting_alignment_jobs", dict(meeting_id="deleted allocation")).lastrowid
        db.execute("delete from tasks where id=?", (removed_job,))
    release.verify_loaded()
    release.rollback()
    release.rollback()
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("select final_message,send_result_json,calendar_summary_status,calendar_summary_result_json from meeting_alignment_jobs where id=?", (job,)).fetchone() == ("new adopted message", ' {"messageId": "new receipt"} ', "updated", ' {"calendar": "new receipt"} ')
        assert db.execute("select job_id,codex_session_id,codex_transcript_start_line,codex_transcript_end_line from meeting_alignment_runs where id=?", (run,)).fetchone() == (job, "replacement-session", 4, 21)
        assert db.execute("select memory_id from meeting_memory_write_events where meeting_job_id=?", (job,)).fetchone() == ("new-memory",)
        assert db.execute("select claim_token from meeting_alignment_delivery_claims where job_id=?", (job,)).fetchone() == ("replacement-token",)
        assert db.execute("pragma foreign_key_check").fetchall() == [("meeting_alignment_runs", 2298, "meeting_alignment_jobs", 0)]
        assert db.execute("select job_id from meeting_alignment_runs where id=2298").fetchone() == (4908,)
        assert db.execute("insert into meeting_alignment_jobs(meeting_id) values('after rollback')").lastrowid > removed_job
        assert db.execute("insert into meeting_alignment_runs(job_id,status) values(?,'new')", (job,)).lastrowid > removed_run
        db.commit()
        forward(db)
        release.verify_loaded()


def test_fresh_current_and_missing_index_repair_preserve_adopted_meeting_values(tmp_path):
    from app.store import AutoReplyStore
    from app.run_storage import insert_run
    from app.task_storage import insert_task

    path = tmp_path / "fresh.db"
    def reopen():
        # Exercise a genuine new process, without the existing process's
        # initialized-path cache or mocking Store schema checks.
        subprocess.run(
            [sys.executable, "-c",
             "from app.store import AutoReplyStore; from pathlib import Path; import sys; AutoReplyStore(Path(sys.argv[1]))",
             str(path)],
            cwd=Path(__file__).resolve().parents[1],
            check=True, capture_output=True,
        )

    store = AutoReplyStore(path)
    with closing(sqlite3.connect(path)) as db, db:
        job = insert_task(db, "meeting_alignment_jobs", dict(
            meeting_id="fresh", decision_json=' { "adopted": true } ',
            final_message="exact adopted message", send_result_json=' { "id": 2 } ',
            calendar_summary_status="failed", calendar_summary_result_json="not JSON",
        )).lastrowid
        insert_run(db, "meeting_alignment_runs", dict(
            task_id=job, status="custom source status", updated_at="", finished_at="",
        ))
    reopen()
    with closing(sqlite3.connect(path)) as db, db:
        before = {
            table: db.execute(f'select * from "{table}" order by rowid').fetchall()
            for table in ("tasks", "runs", "meeting_memory_write_events",
                          "meeting_alignment_delivery_claims")
        }
        db.execute("drop index idx_meeting_alignment_runs_active_job")
    reopen()
    with closing(sqlite3.connect(path)) as db:
        assert {
            table: db.execute(f'select * from "{table}" order by rowid').fetchall()
            for table in before
        } == before
        assert not db.execute(
            "select 1 from sqlite_master where name in "
            "('meeting_alignment_jobs','meeting_alignment_runs')"
        ).fetchone()
    assert store._schema_is_current()


@pytest.mark.parametrize("alteration", ["orphan_parent", "additional_child_fk"])
def test_publication_refuses_changed_or_additional_orphan_semantics(tmp_path, alteration):
    from app.task_schema_release import prepare_task_schema_release

    path = tmp_path / "invalid-target.db"
    meeting_source(path).close()
    release = prepare_task_schema_release(
        tmp_path, path, ("2026-10-11.2", "source-time")
    )
    with closing(sqlite3.connect(path)) as db:
        forward(db)
        if alteration == "orphan_parent":
            db.execute(
                "update runs set job_id=7 where "
                "record_type='meeting_alignment_runs' and legacy_key='2298'"
            )
        else:
            db.execute(
                "insert into meeting_memory_write_events(meeting_job_id,payload_json) "
                "values(999999,'{}')"
            )
        db.commit()
        before = fingerprint(db)
    with pytest.raises(ValueError, match="integrity/FK mismatch"):
        release.verify_loaded()
    with pytest.raises(ValueError, match="integrity/FK mismatch"):
        release.rollback()
    with closing(sqlite3.connect(path)) as db:
        assert fingerprint(db) == before


@pytest.mark.parametrize("allow_missing", [False, True])
@pytest.mark.parametrize("object_name", [
    "idx_meeting_alignment_jobs_identity", "idx_meeting_alignment_runs_active_job",
    "trg_runs_meeting_task_insert",
])
def test_meeting_current_manifest_keeps_literal_identity(tmp_path, object_name, allow_missing):
    from app.run_storage import run_schema_is_current
    from app.task_storage import task_schema_is_current

    with closing(meeting_source(tmp_path / "literal.db")) as db:
        forward(db)
        kind, sql = db.execute(
            "select type,sql from sqlite_master where name=?", (object_name,)
        ).fetchone()
        db.execute(f'drop {kind} "{object_name}"')
        db.execute(sql.replace("'meeting_alignment_jobs'", "'MEETING_ALIGNMENT_JOBS'")
                   .replace("'meeting_alignment_runs'", "'MEETING_ALIGNMENT_RUNS'"))
        check = task_schema_is_current if "jobs" in object_name else run_schema_is_current
        args = {} if check is task_schema_is_current else {"include_task_runs": True}
        assert not check(db, allow_missing_objects=allow_missing, **args)
