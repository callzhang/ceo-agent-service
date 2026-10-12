"""Phase 4B preserves the actual source records and their public identities."""

import json
from pathlib import Path
import sqlite3

import pytest

from app.store import AutoReplyStore


def test_fresh_task_container_replaces_all_4b_sources(tmp_path):
    store = AutoReplyStore(tmp_path / "tasks.db")
    with store._connect() as db:
        tables = {
            row[0]
            for row in db.execute("select name from sqlite_master where type='table'")
        }
        assert "tasks" in tables
        assert not tables.intersection(
            {
                "reply_tasks",
                "reply_task_inputs",
                "work_summary_inputs",
                "task_agent_runs",
                "meeting_alignment_jobs",
                "meeting_alignment_runs",
                "okr_review_requests",
                "okr_review_runs",
                "weekly_okr_analysis_jobs",
            }
        )
        assert {"okr_review_items", "runs"} <= tables


def source_database(path):
    """Independent source oracle: the captured deployed declarations, not the target."""
    from app.run_storage import create_run_schema, insert_run

    inventory = json.loads(
        (
            Path(__file__).parents[1]
            / "docs/superpowers/specs/2026-10-11-database-redesign-phase4-inventory.json"
        ).read_text()
    )
    db = sqlite3.connect(path)
    for table in (
        "reply_tasks",
        "reply_task_inputs",
        "work_summary_inputs",
        "task_agent_runs",
        "meeting_alignment_jobs",
        "meeting_alignment_runs",
        "okr_review_requests",
        "okr_review_runs",
        "weekly_okr_analysis_jobs",
        "okr_review_items",
    ):
        source = inventory["tables"][table]
        db.execute(source["sql"])
        for _, _, sql in source["indexes_and_triggers"]:
            if sql:
                db.execute(sql)
    create_run_schema(db)
    db.executescript("""
        create table service_state(key text primary key,value text,updated_at text);
        insert into service_state values('store_schema_version','2026-10-11.2','source-time');
        create table receipt(id integer primary key autoincrement,reply_task_id integer references reply_tasks(id),agent_run_id integer references runs(id),body blob);
        create index receipt_task on receipt(reply_task_id);
        create trigger receipt_touch after update of body on receipt begin select new.id; end;
        insert into reply_tasks(id,conversation_id,conversation_title,single_chat,trigger_message_id,trigger_create_time,trigger_sender,status,current_input_id,claimed_input_id,input_version,claimed_input_version,locked_at)
            values(1,'chat','Exact title',2,'current','original time','sender','processing',2,1,17,6,NULL);
        insert into reply_task_inputs(id,reply_task_id,channel,conversation_id,conversation_title,single_chat,trigger_message_id,trigger_create_time,trigger_sender,trigger_text,trigger_message_json,business_object_key,input_provenance_json,input_revision_key)
            values(1,1,'dingtalk','chat','Original title',0,'original','original time','sender','claimed body','{ "source": [1, 2] }','business','{ "source": "exact" }','first'),
                  (2,1,'dingtalk','chat','Exact title',2,'current','new time','sender','current body','{ "source": [2, 1] }','business','{}','second');
        insert into work_summary_inputs(id,source_type,source_ref,payload_json,status,available_at,body_sha256,body_bytes,body_compacted)
            values(1,'task','one',x'575349310000ff00017b7d','done','', 'exact digest',123,1),
                  (2,'task','two','{ "summary": "raw" }','pending','','',0,0);
        insert into task_agent_runs(id,summary_input_id,status,error,finished_at,updated_at,projection_json)
            values(1,1,'historical-status','retained error','','','{ "applied": [1, 2] }');
        insert into receipt(reply_task_id,body) values(1,x'ff00');
        update sqlite_sequence set seq=90 where name='reply_task_inputs';
        update sqlite_sequence set seq=80 where name='task_agent_runs';
    """)
    insert_run(
        db,
        "agent_runs",
        dict(
            id=1,
            legacy_key="1",
            task_id=1,
            execution_generation="original generation",
            role="consumer",
            status="completed",
            structured_error_json=' {"retained": true} ',
            completed_at="",
        ),
    )
    insert_run(
        db,
        "agent_runtime_attempts",
        dict(
            id=2,
            legacy_key="1",
            parent_id=1,
            workload_kind="agent_run",
            workload_key="1",
            attempt_number=1,
            route_name="source",
            runtime_kind="codex_cli",
            credential_mode="local_oauth",
            model="test",
            status="completed",
        ),
    )
    insert_run(
        db,
        "agent_runtime_attempts",
        dict(
            id=3,
            legacy_key="2",
            parent_id=None,
            workload_kind="task",
            workload_key="1:decision_repair.2",
            attempt_number=1,
            route_name="source",
            runtime_kind="codex_cli",
            credential_mode="local_oauth",
            model="test",
            status="completed",
        ),
    )
    insert_run(
        db,
        "agent_run_state_events",
        dict(
            id=4,
            legacy_key="1",
            parent_id=1,
            phase="exact phase",
            structured_error_json=" {} ",
        ),
    )
    db.execute("update receipt set agent_run_id=1")
    db.commit()
    return db


def rows(db, table):
    return db.execute(f'select * from "{table}" order by id').fetchall()


def fingerprint(db):
    return {
        "schema": db.execute(
            "select type,name,sql from sqlite_master where sql is not null order by type,name"
        ).fetchall(),
        "sequence": db.execute(
            "select name,seq from sqlite_sequence order by name"
        ).fetchall(),
        "version": db.execute("select * from service_state").fetchall(),
        "rows": {
            table: db.execute(f'select * from "{table}" order by rowid').fetchall()
            for (table,) in db.execute(
                "select name from sqlite_master where type='table' and name not like 'sqlite_%' order by name"
            ).fetchall()
        },
    }


def typed_store(tmp_path):
    """Exercise each DAO through normal fresh typed-schema bootstrap."""
    store = AutoReplyStore(tmp_path / "dao.db")
    with sqlite3.connect(store.path) as db:
        names = {
            row[0]
            for row in db.execute("select name from sqlite_master where type='table'")
        }
        assert "tasks" in names
        assert not names.intersection(
            {
                "reply_tasks",
                "reply_task_inputs",
                "work_summary_inputs",
                "task_agent_runs",
            }
        )
    return store


def test_work_summary_dao_preserves_bytes_claim_and_terminal_compaction(tmp_path):
    store = typed_store(tmp_path)
    body = '{ "source": {"created_at":"exact source time"}, "summary": "body" }'
    input_id = store.enqueue_work_summary_input("local_file", "source", body)
    assert store.get_work_summary_input(input_id).payload_json == body
    claimed = store.claim_work_summary_inputs(1)
    assert [item.id for item in claimed] == [input_id]
    assert claimed[0].attempts == 1
    store.enqueue_work_summary_input("local_file", "source", '{"ignored": true}')
    assert store.get_work_summary_input(input_id).payload_json == body
    store.mark_work_summary_input_done(input_id)
    settled = store.get_work_summary_input(input_id)
    assert settled.status == "done"
    assert settled.body_compacted == 1
    assert settled.payload_json == "{}"
    store.enqueue_work_summary_input("local_file", "source", body)
    assert store.get_work_summary_input(input_id).payload_json == "{}"


def test_exact_forward_inverse_preserves_overlaps_claim_raw_blob_and_source_ddl(
    tmp_path,
):
    from app.task_storage import (
        TASK_PROJECTIONS,
        capture_task_schema,
        migrate_task_schema,
        restore_task_schema,
    )
    from app.run_storage import TASK_AGENT_RUN_SQL

    db = source_database(tmp_path / "source.db")
    tables = (
        "reply_tasks",
        "reply_task_inputs",
        "work_summary_inputs",
        "task_agent_runs",
        "meeting_alignment_jobs",
        "meeting_alignment_runs",
        "okr_review_requests",
        "okr_review_runs",
        "weekly_okr_analysis_jobs",
        "okr_review_items",
        "receipt",
        "runs",
    )
    before_rows = {table: rows(db, table) for table in tables}
    before = fingerprint(db)
    state = capture_task_schema(db)
    db.execute("begin immediate")
    migrate_task_schema(db)
    for table, projection in TASK_PROJECTIONS.items():
        assert (
            db.execute(f"select * from ({projection}) order by id").fetchall()
            == before_rows[table]
        )
    assert (
        db.execute(f"select * from ({TASK_AGENT_RUN_SQL}) order by id").fetchall()
        == before_rows["task_agent_runs"]
    )
    physical = db.execute("select id,record_type,legacy_key from tasks").fetchall()
    assert len({row[0] for row in physical}) == 5
    assert sum(row[2] == "1" for row in physical) == 3
    parent = db.execute(
        "select id,current_input_id,claimed_input_id from tasks where record_type='reply_tasks'"
    ).fetchone()
    assert parent[1] != parent[2]
    assert {
        row[0]
        for row in db.execute(
            "select parent_id from tasks where record_type='reply_task_inputs'"
        )
    } == {parent[0]}
    assert db.execute(
        "select typeof(payload_json),hex(payload_json) from tasks where record_type='work_summary_inputs' and legacy_key='1'"
    ).fetchone() == ("blob", "575349310000FF00017B7D")
    assert db.execute("pragma foreign_key_check").fetchall() == []
    db.commit()
    db.execute("begin immediate")
    restore_task_schema(db, state)
    db.commit()
    assert fingerprint(db) == before
    assert {table: rows(db, table) for table in tables} == before_rows
    db.close()


def test_unmapped_sqlite_scalars_and_source_status_survive_without_normalization(
    tmp_path,
):
    from app.task_storage import (
        capture_task_schema,
        migrate_task_schema,
        restore_task_schema,
    )
    from app.run_storage import insert_run

    db = source_database(tmp_path / "scalars.db")
    db.execute(
        "update reply_tasks set conversation_title=x'ff00',single_chat=1.25,status='source-specific',locked_at=null"
    )
    db.execute(
        "update reply_task_inputs set trigger_message_json='  not parsed JSON  ',input_provenance_json=x'00ff'"
    )
    db.execute(
        "update task_agent_runs set summary_input_id=-321,status='custom historical state',projection_json=' { \"x\": [2,1] } '"
    )
    db.commit()
    before = {
        table: rows(db, table)
        for table in ("reply_tasks", "reply_task_inputs", "task_agent_runs")
    }
    state = capture_task_schema(db)
    db.execute("begin immediate")
    migrate_task_schema(db)
    insert_run(
        db,
        "task_agent_runs",
        dict(summary_input_id=-456, status="another custom state"),
    )
    restore_task_schema(db, state)
    db.commit()
    for table, values in before.items():
        assert rows(db, table)[: len(values)] == values
    assert db.execute(
        "select summary_input_id from task_agent_runs order by id desc limit 1"
    ).fetchone() == (-456,)
    db.close()


def test_source_typed_null_constraints_and_fk_namespace_are_retained(tmp_path):
    from app.task_storage import migrate_task_schema, insert_task
    from app.run_storage import insert_run

    db = source_database(tmp_path / "constraints.db")
    db.execute("begin immediate")
    migrate_task_schema(db)
    db.commit()
    db.execute("pragma foreign_keys=on")
    with pytest.raises(sqlite3.IntegrityError):
        insert_task(
            db,
            "work_summary_inputs",
            dict(source_type="test", source_ref="null", payload_json=None),
        )
    with pytest.raises(sqlite3.IntegrityError):
        insert_task(
            db,
            "work_summary_inputs",
            dict(
                source_type="test",
                source_ref="wrong-field",
                payload_json="{}",
                trigger_text="not a WSI source field",
            ),
        )
    input_physical = db.execute(
        "select id from tasks where record_type='reply_task_inputs' limit 1"
    ).fetchone()[0]
    with pytest.raises(sqlite3.IntegrityError, match="reply task"):
        insert_run(
            db,
            "agent_runs",
            dict(
                task_id=input_physical,
                execution_generation="bad-parent",
                role="consumer",
            ),
        )
    with pytest.raises(sqlite3.IntegrityError, match="reply task"):
        db.execute(
            "insert into receipt(reply_task_id,body) values(?,x'00')", (input_physical,)
        )
    with pytest.raises(sqlite3.IntegrityError):
        insert_run(
            db, "task_agent_runs", dict(summary_input_id=1, role="invented task role")
        )
    db.close()


def test_public_point_natural_and_queue_ranges_use_source_indexes(tmp_path):
    from app.task_storage import TASK_PROJECTIONS, migrate_task_schema
    from app.run_storage import TASK_AGENT_RUN_SQL

    db = source_database(tmp_path / "plans.db")
    db.execute("begin immediate")
    migrate_task_schema(db)
    queries = [
        (
            f"select * from ({TASK_PROJECTIONS['reply_task_inputs']}) where id=?",
            (1,),
            "idx_tasks_public_id",
        ),
        (
            f"select * from ({TASK_PROJECTIONS['reply_task_inputs']}) where reply_task_id=? order by id",
            (1,),
            "idx_reply_task_inputs_task",
        ),
        (
            f"select * from ({TASK_PROJECTIONS['work_summary_inputs']}) where source_type=? and source_ref=?",
            ("task", "one"),
            "idx_work_summary_inputs_identity",
        ),
        (
            f"select id from ({TASK_PROJECTIONS['work_summary_inputs']}) where status='pending' order by id limit 1",
            (),
            "idx_work_summary_inputs_status",
        ),
        (
            f"select * from ({TASK_AGENT_RUN_SQL}) where summary_input_id=? order by id",
            (1,),
            "idx_task_agent_runs_input",
        ),
    ]
    for sql, args, index in queries:
        plan = "\n".join(
            row[3] for row in db.execute("explain query plan " + sql, args)
        )
        assert index in plan, plan
        assert "USE TEMP B-TREE" not in plan, plan
    db.close()


def test_valid_typed_core_can_repair_missing_declared_objects_without_row_changes(tmp_path):
    from app.run_storage import create_run_schema, run_schema_is_current
    from app.task_storage import create_task_schema, migrate_task_schema, task_schema_is_current

    db = source_database(tmp_path / "repair.db")
    db.execute("begin immediate")
    migrate_task_schema(db)
    before = {table: rows(db, table) for table in ("tasks", "runs", "receipt")}
    db.execute("drop index idx_reply_task_inputs_task")
    db.execute("drop trigger trg_tasks_current_input_id_update")
    db.execute("drop index idx_task_agent_runs_active_input")
    assert not task_schema_is_current(db)
    assert not run_schema_is_current(db, include_task_runs=True)
    assert task_schema_is_current(db, allow_missing_objects=True)
    assert run_schema_is_current(db, include_task_runs=True, allow_missing_objects=True)
    create_task_schema(db)
    create_run_schema(db, include_task_runs=True)
    assert task_schema_is_current(db)
    assert run_schema_is_current(db, include_task_runs=True)
    assert {table: rows(db, table) for table in before} == before
    db.close()


def test_claim_remains_old_input_after_new_current_and_terminal_compacts_both(tmp_path):
    store = AutoReplyStore(tmp_path / "queue.db")
    args = dict(
        conversation_id="chat",
        conversation_title="Title",
        single_chat=False,
        trigger_message_id="first",
        trigger_create_time="time",
        trigger_sender="sender",
        trigger_text="first body",
    )
    task_id = store.ensure_reply_task(**args).id
    claimed = store.claim_reply_task(task_id)
    current = store.get_reply_task(task_id)
    store.enqueue_reply_task(
        **(
            args
            | {
                "trigger_message_id": "second",
                "trigger_text": "second body",
                "business_object_key": current.business_object_key,
            }
        )
    )
    assert store.get_reply_task(task_id).trigger_text == "second body"
    assert (
        store.get_reply_task_for_execution(
            task_id, claimed.execution_generation
        ).trigger_text
        == "first body"
    )
    with store._connect() as db:
        assert (
            db.execute(
                "select count(*) from tasks where record_type='reply_task_inputs'"
            ).fetchone()[0]
            == 2
        )
    store.complete_reply_task(
        task_id, expected_execution_generation=claimed.execution_generation
    )
    # A new input arrived during execution; its original requeue semantics remain.
    task = store.get_reply_task(task_id)
    assert task.status == "pending"
    inputs_before = store.list_reply_task_inputs(task_id)
    assert [item["trigger_text"] for item in inputs_before] == ["first body", "second body"]
    current_claim = store.claim_reply_task(task_id)
    assert current_claim.trigger_text == "second body"
    assert current_claim.execution_generation != claimed.execution_generation
    store.complete_reply_task(
        task_id, expected_execution_generation=current_claim.execution_generation
    )
    assert store.get_reply_task(task_id).status == "done"
    inputs_after = store.list_reply_task_inputs(task_id)
    assert [item["id"] for item in inputs_after] == [item["id"] for item in inputs_before]
    assert [item["trigger_message_id"] for item in inputs_after] == ["first", "second"]
    assert all(item["input_compacted"] == 1 and item["trigger_text"] == "" for item in inputs_after)
