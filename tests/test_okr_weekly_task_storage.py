"""Exact deployed OKR/weekly declarations, public identities and paired inverse."""

from contextlib import closing
import sqlite3

import pytest

from tests.test_task_schema_release import (
    _assert_source_rejected_before_mutation,
    _replace_source_table,
)
from tests.test_task_storage import fingerprint, source_database


def request_values(key="new"):
    return dict(
        conversation_id="chat", conversation_title="Exact title",
        trigger_message_id=key, trigger_sender="Sender", trigger_text="Source text",
        period_label="2026 Q3", period_start="2026-07-01", period_end="2026-09-30",
    )


def okr_weekly_source(path, *, meeting=False):
    from app.run_storage import insert_run
    from tests.test_meeting_task_storage import meeting_source

    db = meeting_source(path) if meeting else source_database(path)
    for key, status in ((1, "pending"), (6, "skipped"), (10, "source custom status")):
        values = request_values(str(key)) | dict(id=key, status=status)
        db.execute(
            f"insert into okr_review_requests({','.join(values)}) "
            f"values({','.join('?' for _ in values)})", tuple(values.values()),
        )
    db.executescript("""
        update okr_review_requests set conversation_title=x'00ff',
            trigger_text='raw input', okr_source_json=' { "source": [2, 1] } ',
            error='original request error', codex_session_id='request-session',
            created_at='request-created', updated_at='' where id=6;
        update okr_review_requests set okr_source_json=x'ff0001' where id=10;
        insert into okr_review_runs(id,request_id,codex_session_id,
            codex_transcript_start_line,codex_transcript_end_line,created_at)
        values(3,6,'native-one',4,12,'run-created'),
              (4,6,'native-two',13,27,''),
              (7,10,'native-three',0,0,'run-created'),
              (8,10,x'00ff',1.25,-2,'unparsed time');
        insert into okr_review_items(request_id,objective_title,kr_title,item_json)
        values(6,'adopted objective','adopted KR',' { "adopted": [2, 1] } '),
              (999999,'permitted missing request','KR',x'ff00');
        update sqlite_sequence set seq=24 where name='okr_review_requests';
        update sqlite_sequence set seq=22 where name='okr_review_runs';
        insert into weekly_okr_analysis_jobs(id,week_end,manager_user_id,
            source_digest,status,lease_owner,lease_expires_at,error,
            created_at,finished_at,updated_at)
        values(574,'2026-10-11','manager',
            'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
            'running','lease-owner','lease-until','retained error','created','','');
        update sqlite_sequence set seq=718 where name='weekly_okr_analysis_jobs';
        insert into service_state values
            ('weekly_okr_report:run_lease',' { "owner": "global owner" } ','lease-time'),
            ('weekly_okr_report:last_success_date','2026-10-04','success-time'),
            ('weekly_okr_report:last_attempt_at','2026-10-11T07:00:00','attempt-time');
    """)
    db.executemany(
        "insert into weekly_okr_analysis_jobs(id,week_end,manager_user_id,"
        "source_digest,status,error,finished_at) values(?,'2026-10-04','manager',"
        "?,'completed','superseded by a newer successful analysis',?)",
        [(key, f"historical-digest-{key}", f"finished-{key}") for key in range(1, 26)],
    )
    for physical, public, kind, key in (
        (5, "3", "structured", "6"),
        (6, "4", "weekly_okr", "2026-10-11:manager:" + "a" * 64 + ":574:generation"),
    ):
        insert_run(db, "agent_runtime_attempts", dict(
            id=physical, legacy_key=public, workload_kind=kind, workload_key=key,
            attempt_number=1, route_name="source", runtime_kind="codex_cli",
            credential_mode="local_oauth", model="test", status="completed",
            session_id=f"provider-native-{public}", transcript_start=7, transcript_end=31,
        ))
    db.commit()
    return db


def forward(db):
    from app.task_storage import capture_task_schema, migrate_task_schema

    state = capture_task_schema(db)
    db.execute("begin immediate")
    migrate_task_schema(db)
    db.execute("update service_state set value='2026-10-11.3' where key='store_schema_version'")
    db.commit()
    return state


def test_exact_all_columns_raw_values_public_references_and_untouched_items(tmp_path):
    from app.run_storage import OKR_RUN_SQL
    from app.task_storage import OKR_REQUEST_SQL, WEEKLY_JOB_SQL, restore_task_schema

    with closing(okr_weekly_source(tmp_path / "roundtrip.db", meeting=True)) as db:
        before = fingerprint(db)
        state = forward(db)
        assert len(state["tables"]) == 13  # nine sources, runs and three declared children
        assert "okr_review_items" not in state["children"]
        for table, projection in (
            ("okr_review_requests", OKR_REQUEST_SQL),
            ("okr_review_runs", OKR_RUN_SQL),
            ("weekly_okr_analysis_jobs", WEEKLY_JOB_SQL),
        ):
            assert db.execute(f"select * from ({projection}) order by id").fetchall() == before["rows"][table]
            assert not db.execute("select 1 from sqlite_master where name=?", (table,)).fetchone()
        assert db.execute("select request_id,count(*) from runs where record_type='okr_review_runs' group by request_id").fetchall() == [(6, 2), (10, 2)]
        assert db.execute("select count(*) from tasks where record_type='weekly_okr_analysis_jobs' and status='completed' and error<>'' and finished_at<>''").fetchone() == (25,)
        assert db.execute("select * from okr_review_items order by id").fetchall() == before["rows"]["okr_review_items"]
        assert db.execute("pragma foreign_key_list(okr_review_items)").fetchall() == []
        original_run_columns = ','.join(state["tables"]["runs"]["columns"])
        assert db.execute(f"select {original_run_columns} from runs where record_type='agent_runtime_attempts' order by id").fetchall() == [
            row for row in before["rows"]["runs"] if row[1] == "agent_runtime_attempts"
        ]
        assert db.execute("select * from service_state where key<>'store_schema_version' order by key").fetchall() == sorted(
            row for row in before["version"] if row[0] != "store_schema_version"
        )
        # Meeting's known absent parent is the only FK finding in the combined migration.
        assert len(db.execute("pragma foreign_key_check").fetchall()) == 1
        db.execute("begin immediate")
        restore_task_schema(db, state)
        db.execute("update service_state set value='2026-10-11.2' where key='store_schema_version'")
        db.commit()
        assert fingerprint(db) == before


@pytest.mark.parametrize("record_type", ["okr_review_requests", "okr_review_runs", "weekly_okr_analysis_jobs"])
def test_exact_source_defaults_and_irrelevant_fields_stay_null(tmp_path, record_type):
    from app.run_storage import OKR_RUN_SQL, insert_run
    from app.task_storage import OKR_REQUEST_SQL, WEEKLY_JOB_SQL, insert_task

    values = {
        "okr_review_requests": request_values(),
        "okr_review_runs": dict(request_id=987654321),
        "weekly_okr_analysis_jobs": dict(week_end="week", manager_user_id="manager", source_digest="digest"),
    }[record_type]
    projection = {"okr_review_requests": OKR_REQUEST_SQL, "okr_review_runs": OKR_RUN_SQL,
                  "weekly_okr_analysis_jobs": WEEKLY_JOB_SQL}[record_type]
    with closing(okr_weekly_source(tmp_path / "defaults.db")) as db:
        fields = [row[1] for row in db.execute(f"pragma table_info({record_type})")]
        source_id = db.execute(
            f"insert into {record_type}({','.join(values)}) values({','.join('?' for _ in values)})",
            tuple(values.values()),
        ).lastrowid
        source = dict(zip(fields, db.execute(f"select * from {record_type} where id=?", (source_id,)).fetchone(), strict=True))
        db.execute(f"delete from {record_type} where id=?", (source_id,))
        db.commit()
        forward(db)
        earliest = db.execute("select current_timestamp").fetchone()[0]
        insert = insert_run if record_type == "okr_review_runs" else insert_task
        physical = insert(db, record_type, values).lastrowid
        actual = dict(zip(fields, db.execute(f"select * from ({projection}) where id=?", (physical,)).fetchone(), strict=True))
        latest = db.execute("select current_timestamp").fetchone()[0]
        for field in fields:
            if field == "id":
                continue
            if field in {"created_at", "updated_at"}:
                assert earliest <= actual[field] <= latest
            else:
                assert actual[field] == source[field]
        physical_table = "runs" if record_type == "okr_review_runs" else "tasks"
        column_map = {"codex_session_id": "session_id", "codex_transcript_start_line": "transcript_start", "codex_transcript_end_line": "transcript_end"} if physical_table == "runs" else {}
        applicable = {column_map.get(field, field) for field in fields} | {"record_type", "legacy_key"}
        all_fields = [row[1] for row in db.execute(f"pragma table_info({physical_table})")]
        raw = dict(zip(all_fields, db.execute(f"select * from {physical_table} where id=?", (physical,)).fetchone(), strict=True))
        assert all(value is None for name, value in raw.items() if name not in applicable)
        if physical_table == "runs":
            assert not {"envelope_json", "audit_tool_events_json", "audit_summary"}.intersection(all_fields)
            assert not any(row[3] == "request_id" for row in db.execute("pragma foreign_key_list(runs)"))


@pytest.mark.parametrize("record_type,field", [
    *[("okr_review_requests", field) for field in (
        "conversation_id", "conversation_title", "trigger_message_id", "trigger_sender",
        "trigger_sender_user_id", "trigger_text", "period_label", "period_start",
        "period_end", "okr_source_json", "status", "error", "codex_session_id", "created_at", "updated_at",
    )],
    *[("okr_review_runs", field) for field in ("request_id", "session_id", "transcript_start", "transcript_end", "created_at")],
    *[("weekly_okr_analysis_jobs", field) for field in ("week_end", "manager_user_id", "source_digest", "status", "lease_owner", "lease_expires_at", "error", "created_at", "finished_at", "updated_at")],
])
def test_every_retained_source_not_null_is_enforced(tmp_path, record_type, field):
    from app.run_storage import insert_run
    from app.task_storage import insert_task

    with closing(source_database(tmp_path / "null.db")) as db:
        forward(db)
        values = {"okr_review_requests": request_values(), "okr_review_runs": dict(request_id=-1),
                  "weekly_okr_analysis_jobs": dict(week_end="week", manager_user_id="manager", source_digest="digest")}[record_type]
        insert = insert_run if record_type == "okr_review_runs" else insert_task
        with pytest.raises(sqlite3.IntegrityError):
            insert(db, record_type, values | {field: None})


def test_source_checks_natural_keys_and_missing_public_request_remain_exact(tmp_path):
    from app.run_storage import insert_run
    from app.task_storage import insert_task

    with closing(okr_weekly_source(tmp_path / "constraints.db")) as db:
        forward(db)
        db.execute("pragma foreign_keys=on")
        for status in ("skipped", "a source-specific status"):
            insert_task(db, "okr_review_requests", request_values(status) | dict(status=status))
        with pytest.raises(sqlite3.IntegrityError):
            insert_task(db, "okr_review_requests", request_values("6"))
        for status in ("running", "completed", "failed"):
            insert_task(db, "weekly_okr_analysis_jobs", dict(week_end="week", manager_user_id="manager", source_digest=status, status=status))
        for values in (dict(source_digest="running"), dict(source_digest="bad", status="skipped")):
            with pytest.raises(sqlite3.IntegrityError):
                insert_task(db, "weekly_okr_analysis_jobs", dict(week_end="week", manager_user_id="manager") | values)
        for _ in range(2):
            insert_run(db, "okr_review_runs", dict(request_id=-17.25, session_id="native-public"))
        assert db.execute("select distinct request_id,task_id from runs where record_type='okr_review_runs' and request_id<0").fetchall() == [(-17.25, None)]
        for name in ("status", "error", "finished_at", "updated_at", "task_id", "job_id", "summary_input_id", "workload_key"):
            with pytest.raises(sqlite3.IntegrityError):
                insert_run(db, "okr_review_runs", dict(request_id=6) | {name: 1})
        with pytest.raises(sqlite3.IntegrityError):
            insert_task(db, "okr_review_requests", request_values("bad-field") | dict(lease_owner="not in the source"))
        assert db.execute("pragma foreign_key_check").fetchall() == []


@pytest.mark.parametrize("column,value", [("envelope_json", '{"nonempty":true}'), ("audit_tool_events_json", " [ ] "), ("audit_summary", "native process copied")])
def test_nonempty_okr_process_cache_rejected_before_any_write(tmp_path, column, value):
    from app.task_storage import migrate_task_schema

    with closing(okr_weekly_source(tmp_path / "cache.db")) as db:
        db.execute(f"update okr_review_runs set {column}=? where id=3", (value,))
        db.commit()
        before = fingerprint(db)
        db.execute("begin immediate")
        writes = []
        db.set_trace_callback(writes.append)
        with pytest.raises(ValueError, match="empty retired cache"):
            migrate_task_schema(db)
        db.set_trace_callback(None)
        assert not any(sql.lstrip().lower().startswith(("create", "drop", "alter", "insert", "update", "delete")) for sql in writes)
        assert fingerprint(db) == before
        db.rollback()


@pytest.mark.parametrize("table,old,new", [
    ("okr_review_requests", "default 'pending'", "default 'PENDING'"),
    ("okr_review_requests", "status text not null default 'pending'", "status text not null default 'pending' check(status in ('pending','skipped','source custom status'))"),
    ("okr_review_requests", "trigger_text text not null", "trigger_text text"),
    ("okr_review_runs", "request_id integer not null", "request_id integer not null references okr_review_requests(id)"),
    ("okr_review_runs", "request_id integer not null", "request_id integer not null unique"),
    ("weekly_okr_analysis_jobs", "default 'running'", "default 'completed'"),
    ("weekly_okr_analysis_jobs", "check(status in ('running', 'completed', 'failed'))", "check(status in ('running', 'completed', 'failed', 'skipped'))"),
    ("weekly_okr_analysis_jobs", "finished_at text not null", "finished_at text"),
])
def test_changed_source_declarations_are_rejected_before_any_write(tmp_path, table, old, new):
    with closing(source_database(tmp_path / "declarations.db")) as db:
        ddl = db.execute("select sql from sqlite_master where name=?", (table,)).fetchone()[0]
        assert old in ddl
        _replace_source_table(db, table, ddl.replace(old, new, 1))
        db.commit()
        _assert_source_rejected_before_mutation(db)


@pytest.mark.parametrize("statement", [
    "create index invented_okr_parent_index on okr_review_runs(request_id)",
    "alter table okr_review_runs add column status text",
    "drop index idx_okr_review_requests_status",
    "create trigger changed_okr_insert after insert on okr_review_runs begin select new.id; end",
    "drop index idx_weekly_okr_analysis_jobs_identity",
])
def test_new_or_missing_source_fields_and_objects_reject_atomically(tmp_path, statement):
    from app.task_storage import migrate_task_schema

    with closing(okr_weekly_source(tmp_path / "extra.db")) as db:
        db.execute(statement)
        db.commit()
        before = fingerprint(db)
        db.execute("begin immediate")
        with pytest.raises(ValueError, match="source (schema|declaration)"):
            migrate_task_schema(db)
        assert fingerprint(db) == before
        db.rollback()


def test_forward_failure_after_domain_conversion_rolls_back_every_source(tmp_path):
    from app.task_storage import migrate_task_schema

    with closing(okr_weekly_source(tmp_path / "interrupted.db", meeting=True)) as db:
        before = fingerprint(db)
        denied = []

        def interrupt(action, first, _second, *_):
            if action == sqlite3.SQLITE_DROP_TABLE and first == "okr_review_runs":
                denied.append(first)
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        db.execute("begin immediate")
        db.set_authorizer(interrupt)
        with pytest.raises(sqlite3.DatabaseError):
            migrate_task_schema(db)
        db.set_authorizer(None)
        assert denied == ["okr_review_runs"]
        db.rollback()
        assert fingerprint(db) == before


def test_original_point_natural_and_queue_indexes_remain_usable(tmp_path):
    from app.run_storage import OKR_RUN_SQL
    from app.task_storage import OKR_REQUEST_SQL, WEEKLY_JOB_SQL

    with closing(okr_weekly_source(tmp_path / "plans.db")) as db:
        forward(db)
        for sql, args, index in (
            (f"select * from ({OKR_REQUEST_SQL}) where id=?", (6,), "idx_tasks_public_id"),
            (f"select * from ({OKR_REQUEST_SQL}) where conversation_id=? and trigger_message_id=?", ("chat", "6"), "idx_okr_review_requests_identity"),
            (f"select id from ({OKR_REQUEST_SQL}) where status='pending' order by id", (), "idx_okr_review_requests_status"),
            (f"select * from ({WEEKLY_JOB_SQL}) where week_end=? and manager_user_id=? and source_digest=?", ("2026-10-11", "manager", "a" * 64), "idx_weekly_okr_analysis_jobs_identity"),
            (f"select * from ({OKR_RUN_SQL}) where id=?", (3,), "idx_runs_public_id"),
        ):
            plan = '\n'.join(row[3] for row in db.execute("explain query plan " + sql, args))
            assert index in plan
            assert "USE TEMP B-TREE" not in plan


@pytest.mark.parametrize("name", ["idx_okr_review_requests_identity", "idx_okr_review_requests_status", "idx_weekly_okr_analysis_jobs_identity"])
@pytest.mark.parametrize("allow_missing", [False, True])
def test_current_manifest_rejects_altered_okr_weekly_literal(tmp_path, name, allow_missing):
    from app.task_storage import task_schema_is_current

    with closing(okr_weekly_source(tmp_path / "manifest.db")) as db:
        forward(db)
        sql = db.execute("select sql from sqlite_master where name=?", (name,)).fetchone()[0]
        db.execute(f"drop index {name}")
        db.execute(sql.replace("'okr_review_requests'", "'OKR_REVIEW_REQUESTS'").replace("'weekly_okr_analysis_jobs'", "'WEEKLY_OKR_ANALYSIS_JOBS'"))
        assert not task_schema_is_current(db, allow_missing_objects=allow_missing)
