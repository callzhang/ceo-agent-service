import sqlite3

import pytest

from app.store import AutoReplyStore
from app.run_storage import insert_run
from tests.run_schema_fixtures import current_store_with_migrated_core


def test_fresh_storage_has_one_core_run_table(tmp_path):
    store = AutoReplyStore(tmp_path / "runs.db")
    with store._connect() as db:
        tables = {
            r[0]
            for r in db.execute("select name from sqlite_master where type='table'")
        }
        assert "runs" in tables
        assert not tables & {
            "agent_runs",
            "agent_runtime_attempts",
            "agent_run_state_events",
            "agent_run_events",
        }
        columns = {r[1] for r in db.execute("pragma table_info(runs)")}
        assert not columns & {
            "final_result_json",
            "result_envelope_json",
            "tool_events_json",
        }


def test_forward_inverse_preserves_colliding_public_ids_and_new_receipt(tmp_path):
    from app.run_storage import (
        AGENT_RUN_SQL,
        RUNTIME_ATTEMPT_SQL,
        capture_run_schema,
        migrate_run_schema,
        restore_run_schema,
    )
    from tests.run_schema_fixtures import previous_run_schema_store

    path = tmp_path / "old.db"
    previous_run_schema_store(
        path,
        task_args=dict(
            conversation_id="cid",
            conversation_title="title",
            single_chat=False,
            trigger_message_id="msg",
            trigger_create_time="time",
            trigger_sender="sender",
            trigger_text="task",
            execution_generation="g",
        ),
    )
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript("""
        create table receipt(id integer primary key autoincrement,agent_run_id integer,body blob,foreign key(agent_run_id) references agent_runs(id));
        create index receipt_run on receipt(agent_run_id);
        insert into agent_runs(id,reply_task_id,execution_generation,role,status) values(1,1,'g','consumer','running');
        insert into agent_runtime_attempts(id,agent_run_id,workload_kind,workload_key,attempt_number,route_name,runtime_kind,credential_mode,model,status) values(1,1,'agent_run','1',1,'route','codex_cli','local_oauth','model','running');
        insert into agent_run_state_events values(1,1,'phase','{}','time');
        insert into receipt values(1,1,x'0010');
    """)
    state = capture_run_schema(db)
    db.execute("begin immediate")
    migrate_run_schema(db)
    assert db.execute(f"select id from ({AGENT_RUN_SQL})").fetchone()[0] == 1
    assert db.execute(f"select id from ({RUNTIME_ATTEMPT_SQL})").fetchone()[0] == 1
    assert len({r[0] for r in db.execute("select id from runs")}) == 3
    assert db.execute("pragma foreign_key_list(receipt)").fetchone()[2] == "runs"
    cursor = insert_run(
        db,
        "agent_runs",
        {
            "task_id": 1,
            "execution_generation": "g2",
            "role": "consumer",
            "status": "running",
        },
    )
    new_id = cursor.lastrowid
    assert new_id > 3
    db.execute("insert into receipt(agent_run_id,body) values(?,x'ff00')", (new_id,))
    db.commit()
    db.execute("begin immediate")
    restore_run_schema(db, state)
    db.commit()
    assert (
        db.execute("select id from agent_runs order by id").fetchall()[1][0] == new_id
    )
    assert (
        db.execute(
            "select hex(body) from receipt where agent_run_id=?", (new_id,)
        ).fetchone()[0]
        == "FF00"
    )
    assert (
        db.execute(
            "select final_result_json from agent_runs where id=?", (new_id,)
        ).fetchone()[0]
        == ""
    )
    assert db.execute("pragma foreign_key_list(receipt)").fetchone()[2] == "agent_runs"
    assert db.execute("pragma foreign_key_check").fetchall() == []


def test_retired_native_events_or_nonempty_caches_stop_migration(tmp_path):
    from app.run_storage import migrate_run_schema

    db = sqlite3.connect(tmp_path / "blocked.db")
    db.executescript(
        "create table agent_run_events(id integer,event_json text); insert into agent_run_events values(1,'native body');"
    )
    with pytest.raises(ValueError, match="agent_run_events"):
        migrate_run_schema(db)


def test_fresh_fk_children_retain_logical_parent_namespace(tmp_path):
    store = AutoReplyStore(tmp_path / "typed-children.db")
    with store._connect() as db:
        guards = {
            r[0]
            for r in db.execute("select name from sqlite_master where type='trigger'")
        }
        assert "trg_runs_child_agent_execution_receipts_agent_run_id_insert" in guards
        assert "trg_runs_child_review_candidates_consumer_run_id_update" in guards


def test_public_id_lookup_is_indexed(tmp_path):
    from app.run_storage import RUNTIME_ATTEMPT_SQL

    store = AutoReplyStore(tmp_path / "index.db")
    with store._connect() as db:
        plan = "\n".join(
            r[3]
            for r in db.execute(
                f"EXPLAIN QUERY PLAN select * from ({RUNTIME_ATTEMPT_SQL}) where id=?",
                (1,),
            )
        )
        assert "idx_runs_public_id" in plan


@pytest.mark.parametrize(
    "values",
    [
        {"session_mode": "resume", "source_session_id": "   "},
        {"session_mode": "fresh", "source_session_id": "old-session"},
        {"attempt_purpose": "result_validation_correction"},
        {"attempt_purpose": "normal", "validation_retry_policy_id": "policy"},
        {
            "workload_kind": "standalone",
            "workload_key": "standalone",
            "parent_id": None,
            "lease_owner": "",
            "lease_expires_at": "",
        },
    ],
)
def test_runtime_database_constraints_keep_existing_contract(tmp_path, values):
    from app.run_storage import create_run_schema

    db = sqlite3.connect(tmp_path / "constraints.db")
    db.execute("create table reply_tasks(id integer primary key)")
    create_run_schema(db)
    insert_run(
        db,
        "agent_runs",
        {
            "task_id": 1,
            "execution_generation": "generation",
            "role": "consumer",
            "status": "running",
        },
    )
    attempt = dict(
        record_type="agent_runtime_attempts",
        parent_id=1,
        workload_kind="agent_run",
        workload_key="1",
        attempt_number=1,
        route_name="route",
        runtime_kind="codex_cli",
        credential_mode="local_oauth",
        model="model",
        status="running",
    )
    attempt.update(values)
    with pytest.raises(sqlite3.IntegrityError):
        insert_run(
            db,
            "agent_runtime_attempts",
            {key: value for key, value in attempt.items() if key != "record_type"},
        )


def test_runtime_lineage_is_immutable_and_all_seven_source_triggers_remain(tmp_path):
    from app.run_storage import create_run_schema

    db = sqlite3.connect(tmp_path / "lineage.db")
    db.execute("create table reply_tasks(id integer primary key)")
    create_run_schema(db)
    insert_run(
        db,
        "agent_runs",
        {
            "task_id": 1,
            "execution_generation": "generation",
            "role": "consumer",
            "status": "running",
        },
    )
    insert_run(
        db,
        "agent_runtime_attempts",
        {
            "parent_id": 1,
            "workload_kind": "agent_run",
            "workload_key": "1",
            "attempt_number": 1,
            "route_name": "route",
            "runtime_kind": "codex_cli",
            "credential_mode": "local_oauth",
            "model": "model",
            "status": "running",
        },
    )
    assert (
        db.execute(
            "select count(*) from sqlite_master where type='trigger' and name like 'trg_runtime_attempt_%'"
        ).fetchone()[0]
        == 7
    )
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db.execute(
            "update runs set attempt_purpose='result_validation_correction',validation_retry_policy_id='policy',validation_result_schema_id='schema' where record_type='agent_runtime_attempts'"
        )
    with pytest.raises(sqlite3.IntegrityError, match="logical agent run"):
        insert_run(
            db, "agent_run_state_events", {"parent_id": 2, "phase": "wrong namespace"}
        )


def test_standalone_attempt_has_native_public_id_and_lease(tmp_path):
    from app.run_storage import RUNTIME_ATTEMPT_SQL, create_run_schema

    db = sqlite3.connect(tmp_path / "standalone.db")
    db.execute("create table reply_tasks(id integer primary key)")
    create_run_schema(db)
    cursor = insert_run(
        db,
        "agent_runtime_attempts",
        {
            "workload_kind": "meeting",
            "workload_key": "17",
            "attempt_number": 1,
            "route_name": "route",
            "runtime_kind": "codex_cli",
            "credential_mode": "local_oauth",
            "model": "model",
            "status": "running",
            "lease_owner": "owner",
            "lease_expires_at": "future",
        },
    )
    row = db.execute(RUNTIME_ATTEMPT_SQL).fetchone()
    assert row[0] == cursor.lastrowid
    assert row[1] is None


def test_migrated_attempt_public_lookup_and_mutation_use_legacy_namespace(tmp_path):
    from app.run_storage import RUNTIME_ATTEMPT_SQL, migrate_run_schema
    from tests.run_schema_fixtures import previous_run_schema_store

    path = tmp_path / "public-lookup.db"
    previous_run_schema_store(
        path,
        task_args=dict(
            conversation_id="cid",
            conversation_title="title",
            single_chat=False,
            trigger_message_id="msg",
            trigger_create_time="time",
            trigger_sender="sender",
            trigger_text="task",
            execution_generation="generation",
        ),
    )
    with sqlite3.connect(path) as db:
        db.execute(
            "insert into agent_runs(id,reply_task_id,execution_generation,role,status,lease_owner,lease_expires_at) values(7,1,'generation','consumer','running','owner','2099-01-01 00:00:00')"
        )
        db.execute(
            "insert into agent_runtime_attempts(id,agent_run_id,workload_kind,workload_key,attempt_number,route_name,runtime_kind,credential_mode,model,status) values(7,7,'agent_run','7',1,'route','codex_cli','local_oauth','model','starting')"
        )
        db.commit()
        db.execute("pragma foreign_keys=off")
        db.execute("begin immediate")
        migrate_run_schema(db)
        db.execute(
            "update service_state set value='2026-10-11.2' where key='store_schema_version'"
        )
    store = current_store_with_migrated_core(tmp_path, path)
    assert store.get_agent_runtime_attempt(7).id == 7
    assert store.mark_agent_runtime_attempt_running_once(7).status == "running"
    assert (
        store.fail_agent_runtime_attempt(7, "process", "provider_failure", True).status
        == "failed"
    )
    assert store.get_agent_run(7).status == "running"
    with store._connect() as db:
        physical_id = db.execute(
            "select id from runs where record_type='agent_runtime_attempts' and legacy_key='7'"
        ).fetchone()[0]
        assert physical_id > 7
        assert (
            db.execute(
                f"select status from ({RUNTIME_ATTEMPT_SQL}) where id=7"
            ).fetchone()[0]
            == "failed"
        )
        assert (
            db.execute(
                "select value from service_state where key='store_schema_version'"
            ).fetchone()[0]
            == "2026-10-12.1"
        )
    reopened = AutoReplyStore(store.path)
    assert reopened.get_agent_runtime_attempt(7).status == "failed"


def test_record_type_cannot_move_existing_fact_between_source_domains(tmp_path):
    from app.run_storage import create_run_schema

    db = sqlite3.connect(tmp_path / "immutable-type.db")
    db.execute("create table reply_tasks(id integer primary key)")
    create_run_schema(db)
    insert_run(
        db,
        "agent_runs",
        {
            "task_id": 1,
            "execution_generation": "g",
            "role": "consumer",
            "status": "running",
        },
    )
    with pytest.raises(sqlite3.IntegrityError, match="record type is immutable"):
        db.execute(
            "update runs set record_type='agent_run_state_events',parent_id=1,phase='state' where id=1"
        )


def test_logical_physical_id_always_matches_public_id(tmp_path):
    from app.run_storage import create_run_schema

    db = sqlite3.connect(tmp_path / "logical-key.db")
    db.execute("create table reply_tasks(id integer primary key)")
    create_run_schema(db)
    with pytest.raises(sqlite3.IntegrityError):
        insert_run(
            db,
            "agent_runs",
            {
                "id": 1,
                "legacy_key": "7",
                "task_id": 1,
                "execution_generation": "g",
                "role": "consumer",
                "status": "running",
            },
        )


@pytest.mark.parametrize(
    "cache", ["final_result_json", "result_envelope_json", "native_events"]
)
def test_nonempty_retired_source_is_rejected_without_schema_rows_or_marker_changes(
    tmp_path, cache
):
    from app.run_storage import capture_run_schema
    from tests.run_schema_fixtures import previous_run_schema_store

    path = tmp_path / "nonempty.db"
    previous_run_schema_store(
        path,
        task_args=dict(
            conversation_id="cid",
            conversation_title="title",
            single_chat=False,
            trigger_message_id="msg",
            trigger_create_time="time",
            trigger_sender="sender",
            trigger_text="task",
            execution_generation="generation",
        ),
    )
    with sqlite3.connect(path) as db:
        db.execute(
            "insert into agent_runs(id,reply_task_id,execution_generation,role,status) values(7,1,'generation','consumer','completed')"
        )
        if cache == "final_result_json":
            db.execute(
                'update agent_runs set final_result_json=\'{"result":"body"}\' where id=7'
            )
        elif cache == "result_envelope_json":
            db.execute(
                "insert into agent_runtime_attempts(id,agent_run_id,workload_kind,workload_key,attempt_number,route_name,runtime_kind,credential_mode,model,status,result_envelope_json) values(7,7,'agent_run','7',1,'route','codex_cli','local_oauth','model','completed','{\"result\":\"body\"}')"
            )
        else:
            db.execute(
                'insert into agent_run_events(agent_run_id,sequence,event_json) values(7,1,\'{"body":"native"}\')'
            )
        state = capture_run_schema(db)
        rows = {
            table: db.execute(f"select * from {table}").fetchall()
            for table in state["tables"]
        }
    from app.run_storage import migrate_run_schema

    with sqlite3.connect(path) as db:
        db.execute("pragma foreign_keys=off")
        db.execute("begin immediate")
        with pytest.raises(
            ValueError, match="retired empty placeholder|contains native events"
        ):
            migrate_run_schema(db)
        db.rollback()
    with sqlite3.connect(path) as db:
        assert capture_run_schema(db) == state
        assert {
            table: db.execute(f"select * from {table}").fetchall()
            for table in state["tables"]
        } == rows
        assert (
            db.execute(
                "select value from service_state where key='store_schema_version'"
            ).fetchone()[0]
            == "2026-10-11.1"
        )
        assert not db.execute(
            "select 1 from sqlite_master where name='runs'"
        ).fetchone()


def test_complete_source_objects_roundtrip_exactly(tmp_path):
    from app.run_storage import (
        capture_run_schema,
        migrate_run_schema,
        restore_run_schema,
    )
    from tests.run_schema_fixtures import previous_run_schema_store

    path = tmp_path / "objects.db"
    previous_run_schema_store(path)
    db = sqlite3.connect(path)
    state = capture_run_schema(db)
    db.execute("pragma foreign_keys=off")
    db.execute("begin immediate")
    migrate_run_schema(db)
    db.commit()
    db.execute("begin immediate")
    restore_run_schema(db, state)
    db.commit()
    assert capture_run_schema(db) == state


def test_partial_migration_exception_rolls_back_all_source_objects_and_rows(
    tmp_path, monkeypatch
):
    import app.run_storage as storage
    from tests.run_schema_fixtures import previous_run_schema_store

    path = tmp_path / "atomic.db"
    previous_run_schema_store(path)
    with sqlite3.connect(path) as db:
        db.execute(
            "create table receipt(id integer primary key autoincrement,agent_run_id integer references agent_runs(id),body blob)"
        )
        state = storage.capture_run_schema(db)

    def fail_child(*args):
        raise sqlite3.IntegrityError("injected child rebuild failure")

    monkeypatch.setattr(storage, "_rebuild_child", fail_child)
    with sqlite3.connect(path) as db:
        db.execute("pragma foreign_keys=off")
        db.execute("begin immediate")
        with pytest.raises(sqlite3.IntegrityError, match="injected child rebuild failure"):
            storage.migrate_run_schema(db)
        db.rollback()
    with sqlite3.connect(path) as db:
        assert storage.capture_run_schema(db) == state
        assert (
            db.execute(
                "select value from service_state where key='store_schema_version'"
            ).fetchone()[0]
            == "2026-10-11.1"
        )
        assert not db.execute(
            "select 1 from sqlite_master where name='runs'"
        ).fetchone()


def test_state_events_have_no_unrecorded_time_lease_or_session_facts(tmp_path):
    from app.run_storage import create_run_schema

    db = sqlite3.connect(tmp_path / "state-fields.db")
    db.execute("create table reply_tasks(id integer primary key)")
    create_run_schema(db)
    insert_run(
        db,
        "agent_run_state_events",
        {
            "parent_id": 1,
            "phase": "service_restart",
            "structured_error_json": "{}",
            "created_at": "2026-10-11 10:20:30",
        },
    )
    row = db.execute(
        "select updated_at,started_at,completed_at,finished_at,lease_owner,lease_expires_at,session_id,session_mode,transcript_start,transcript_end,status,attempt_purpose,proposal_revision,turn_attempt,operation_id from runs"
    ).fetchone()
    assert row == (None,) * 15


@pytest.mark.parametrize("source_high_sequence", [1, 2000])
@pytest.mark.parametrize("provider_rows", [0, 3])
@pytest.mark.parametrize("allocate_and_delete", [False, True])
def test_inverse_preserves_deleted_replacement_allocation_without_changing_baseline_sequences(
    tmp_path, source_high_sequence, provider_rows, allocate_and_delete
):
    from app.run_storage import (
        capture_run_schema,
        migrate_run_schema,
        restore_run_schema,
    )
    from tests.run_schema_fixtures import previous_run_schema_store

    path = tmp_path / "allocator.db"
    previous_run_schema_store(
        path,
        task_args=dict(
            conversation_id="cid",
            conversation_title="title",
            single_chat=False,
            trigger_message_id="msg",
            trigger_create_time="time",
            trigger_sender="sender",
            trigger_text="task",
            execution_generation="source",
        ),
    )
    db = sqlite3.connect(path)
    db.execute(
        "insert into agent_runs(id,reply_task_id,execution_generation,role,status) values(1,1,'source','consumer','completed')"
    )
    for number in range(1, provider_rows + 1):
        db.execute(
            "insert into agent_runtime_attempts(id,agent_run_id,workload_kind,workload_key,attempt_number,route_name,runtime_kind,credential_mode,model,status) values(?,1,'agent_run','1',?,'route','codex_cli','local_oauth','model','completed')",
            (number, number),
        )
        db.execute(
            "insert into agent_run_state_events(id,agent_run_id,phase,structured_error_json) values(?,1,'source','{}')",
            (number,),
        )
    db.execute(
        "update sqlite_sequence set seq=? where name='agent_runs'",
        (source_high_sequence,),
    )
    db.commit()
    state = capture_run_schema(db)
    source_sequences = db.execute(
        "select name,seq from sqlite_sequence order by name"
    ).fetchall()
    db.execute("pragma foreign_keys=off")
    db.execute("begin immediate")
    report = migrate_run_schema(db)
    db.execute(
        "update service_state set value='2026-10-11.2' where key='store_schema_version'"
    )
    db.commit()
    baseline = report["high_water"]
    assert baseline == max(source_high_sequence, provider_rows) + provider_rows * 2
    if provider_rows:
        assert (
            min(
                row[0]
                for row in db.execute(
                    "select id from runs where record_type='agent_runtime_attempts'"
                )
            )
            > source_high_sequence
        )
    if allocate_and_delete:
        db.execute("begin immediate")
        allocated = insert_run(
            db,
            "agent_runs",
            {
                "task_id": 1,
                "execution_generation": "replacement",
                "role": "consumer",
                "status": "pending",
            },
        ).lastrowid
        assert allocated > baseline
        db.execute("delete from runs where id=?", (allocated,))
        db.commit()
        assert not db.execute("select 1 from runs where id=?", (allocated,)).fetchone()
    db.execute("begin immediate")
    restore_run_schema(db, state)
    db.commit()
    restored = capture_run_schema(db)
    assert {
        table: (data["objects"], data["columns"])
        for table, data in restored["tables"].items()
    } == {
        table: (data["objects"], data["columns"])
        for table, data in state["tables"].items()
    }
    if allocate_and_delete:
        for table in ("agent_runs", "agent_runtime_attempts", "agent_run_state_events"):
            assert (
                db.execute(
                    "select seq from sqlite_sequence where name=?", (table,)
                ).fetchone()[0]
                >= allocated
            )
        next_id = db.execute(
            "insert into agent_runs(reply_task_id,execution_generation,role,status) values(1,'next','consumer','pending')"
        ).lastrowid
        assert next_id > allocated
    else:
        assert (
            db.execute("select name,seq from sqlite_sequence order by name").fetchall()
            == source_sequences
        )
        assert restored == state


@pytest.mark.parametrize("marker", ["2026-10-10.2", "2026-10-11.1", None])
def test_unsupported_or_malformed_store_source_is_rejected_before_bootstrap(
    tmp_path, marker
):
    from tests.run_schema_fixtures import previous_run_schema_store

    path = tmp_path / "unsupported.db"
    previous_run_schema_store(
        path,
        task_args=dict(
            conversation_id="cid",
            conversation_title="title",
            single_chat=False,
            trigger_message_id="msg",
            trigger_create_time="time",
            trigger_sender="sender",
            trigger_text="task",
            execution_generation="source",
        ),
    )
    with sqlite3.connect(path) as db:
        db.execute(
            "insert into agent_runs(id,reply_task_id,execution_generation,role,status) values(1,1,'source','consumer','completed')"
        )
        if marker is None:
            db.execute("delete from service_state where key='store_schema_version'")
        else:
            db.execute(
                "update service_state set value=? where key='store_schema_version'",
                (marker,),
            )
        before_schema = db.execute(
            "select type,name,tbl_name,sql from sqlite_master order by type,name"
        ).fetchall()
        tables = [
            row[0]
            for row in db.execute("select name from sqlite_master where type='table'")
        ]
        before_rows = {
            table: db.execute(f'select * from "{table}"').fetchall() for table in tables
        }
    with pytest.raises(sqlite3.Error):
        AutoReplyStore(path)
    with sqlite3.connect(path) as db:
        assert (
            db.execute(
                "select type,name,tbl_name,sql from sqlite_master order by type,name"
            ).fetchall()
            == before_schema
        )
        assert {
            table: db.execute(f'select * from "{table}"').fetchall() for table in tables
        } == before_rows
        assert not db.execute(
            "select 1 from sqlite_master where name='runs'"
        ).fetchone()


def test_preceding_marker_with_malformed_core_columns_fails_without_mutation(tmp_path):
    from tests.run_schema_fixtures import previous_run_schema_store

    path = tmp_path / "malformed.db"
    previous_run_schema_store(path)
    with sqlite3.connect(path) as db:
        db.execute("alter table agent_runs drop column operation_id")
        before_schema = db.execute(
            "select type,name,tbl_name,sql from sqlite_master order by type,name"
        ).fetchall()
        tables = [
            row[0]
            for row in db.execute("select name from sqlite_master where type='table'")
        ]
        before_rows = {
            table: db.execute(f'select * from "{table}"').fetchall() for table in tables
        }
    from app.run_storage import migrate_run_schema

    with sqlite3.connect(path) as db:
        db.execute("pragma foreign_keys=off")
        db.execute("begin immediate")
        with pytest.raises(ValueError, match="unsupported 11.1 source schema"):
            migrate_run_schema(db)
        db.rollback()
    with sqlite3.connect(path) as db:
        assert (
            db.execute(
                "select type,name,tbl_name,sql from sqlite_master order by type,name"
            ).fetchall()
            == before_schema
        )
        assert {
            table: db.execute(f'select * from "{table}"').fetchall() for table in tables
        } == before_rows


def test_fresh_store_bootstrap_on_email_only_database_keeps_email_schema_and_rows(
    tmp_path,
):
    from app.email_store import EmailStore

    path = tmp_path / "email-only.db"
    EmailStore(path)
    with sqlite3.connect(path) as db:
        tables = [
            row[0]
            for row in db.execute(
                "select name from sqlite_master where type='table' and name<>'sqlite_sequence'"
            )
        ]
        before_rows = {
            table: db.execute(f'select * from "{table}"').fetchall() for table in tables
        }
    AutoReplyStore(path)
    with sqlite3.connect(path) as db:
        assert {
            table: db.execute(f'select * from "{table}"').fetchall() for table in tables
        } == before_rows
        assert (
            db.execute(
                "select value from service_state where key='store_schema_version'"
            ).fetchone()[0]
            == "2026-10-12.1"
        )


def test_logical_public_lookup_keeps_original_integer_primary_key_plan(tmp_path):
    from app.run_storage import AGENT_RUN_SQL

    store = AutoReplyStore(tmp_path / "logical-lookup.db")
    with store._connect() as db:
        plan = "\n".join(
            row[3]
            for row in db.execute(
                f"EXPLAIN QUERY PLAN select * from ({AGENT_RUN_SQL}) where id=?", (1,)
            )
        )
        assert "INTEGER PRIMARY KEY" in plan
        assert "SCAN" not in plan


@pytest.mark.parametrize(
    "kind,name",
    [
        ("index", "idx_runs_public_id"),
        ("trigger", "trg_runs_public_key"),
        ("trigger", "trg_runs_record_type_immutable"),
    ],
)
def test_current_core_manifest_detects_and_repairs_missing_objects_without_changing_rows(
    tmp_path, kind, name
):
    from app.store import _INITIALIZED_STORE_PATHS

    path = tmp_path / "missing-core-object.db"
    store = AutoReplyStore(path)
    store.enqueue_reply_task(
        conversation_id="cid",
        conversation_title="title",
        single_chat=False,
        trigger_message_id="msg",
        trigger_create_time="time",
        trigger_sender="sender",
        trigger_text="task",
        execution_generation="generation",
    )
    with store._connect() as db:
        insert_run(
            db,
            "agent_runs",
            dict(
                task_id=1,
                execution_generation="generation",
                role="consumer",
                status="completed",
            ),
        )
        before = db.execute("select * from runs").fetchall()
        db.execute(f"drop {kind} {name}")
    assert store._schema_is_current() is False
    _INITIALIZED_STORE_PATHS.discard(path.resolve())
    reopened = AutoReplyStore(path)
    assert reopened._schema_is_current() is True
    with reopened._connect() as db:
        assert [tuple(row) for row in db.execute("select * from runs")] == [
            tuple(row) for row in before
        ]
        assert (
            db.execute(
                "select 1 from sqlite_master where type=? and name=?", (kind, name)
            ).fetchone()
            is not None
        )


def test_current_layout_with_changed_core_definition_is_not_repaired_as_fresh(tmp_path):
    from app.store import _INITIALIZED_STORE_PATHS

    path = tmp_path / "changed-core-definition.db"
    store = AutoReplyStore(path)
    with store._connect() as db:
        db.execute("drop index idx_runs_public_id")
        db.execute("create index idx_runs_public_id on runs(status)")
        before = db.execute(
            "select type,name,tbl_name,sql from sqlite_master order by type,name"
        ).fetchall()
    assert store._schema_is_current() is False
    _INITIALIZED_STORE_PATHS.discard(path.resolve())
    with pytest.raises(
        sqlite3.IntegrityError, match="unsupported or malformed Store source"
    ):
        AutoReplyStore(path)
    with sqlite3.connect(path) as db:
        assert db.execute(
            "select type,name,tbl_name,sql from sqlite_master order by type,name"
        ).fetchall() == [tuple(row) for row in before]


@pytest.mark.parametrize("mixed_runs_ddl", ["current", "unknown"])
def test_preceding_source_with_runs_table_is_rejected_without_adopting_mixed_layout(
    tmp_path, mixed_runs_ddl
):
    from app.run_storage import RUN_TABLE_DDL
    from tests.run_schema_fixtures import previous_run_schema_store

    path = tmp_path / "mixed-layout.db"
    previous_run_schema_store(path)
    with sqlite3.connect(path) as db:
        db.execute(
            RUN_TABLE_DDL
            if mixed_runs_ddl == "current"
            else "create table runs(id integer)"
        )
        before_schema = db.execute(
            "select type,name,tbl_name,sql from sqlite_master order by type,name"
        ).fetchall()
        tables = [
            row[0]
            for row in db.execute("select name from sqlite_master where type='table'")
        ]
        before_rows = {
            table: db.execute(f'select * from "{table}"').fetchall() for table in tables
        }
    from app.run_storage import migrate_run_schema

    with sqlite3.connect(path) as db:
        db.execute("pragma foreign_keys=off")
        db.execute("begin immediate")
        with pytest.raises(
            ValueError, match="unsupported 11.1 source schema: runs already exists"
        ):
            migrate_run_schema(db)
        db.rollback()
    with sqlite3.connect(path) as db:
        assert (
            db.execute(
                "select type,name,tbl_name,sql from sqlite_master order by type,name"
            ).fetchall()
            == before_schema
        )
        assert {
            table: db.execute(f'select * from "{table}"').fetchall() for table in tables
        } == before_rows


def _provider_parent_lookup_store(tmp_path, *, standalone=False):
    from app.run_storage import migrate_run_schema
    from tests.run_schema_fixtures import previous_run_schema_store

    path = tmp_path / "provider-parent-plan.db"
    previous_run_schema_store(
        path,
        task_args=dict(
            conversation_id="cid",
            conversation_title="title",
            single_chat=False,
            trigger_message_id="msg",
            trigger_create_time="time",
            trigger_sender="sender",
            trigger_text="task",
            execution_generation="source",
        ),
    )
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executemany(
        "insert into agent_runs(id,reply_task_id,execution_generation,role,turn_attempt,status) values(?,1,'source','consumer',?,'completed')",
        [(1, 0), (2, 1), (3, 2)],
    )
    insert_sql = """insert into agent_runtime_attempts(id,agent_run_id,workload_kind,workload_key,attempt_number,route_name,runtime_kind,credential_mode,model,status,session_id,transcript_reference,transcript_start,transcript_end,started_at,finished_at) values(?,?,'agent_run',?,?,?,'codex_cli','local_oauth',?,'completed',?,?,?,?,?,?)"""
    source_attempts = [
        (
            7,
            1,
            "1",
            1,
            "route-A",
            "model-A",
            "synthetic-A",
            "ref-A",
            1,
            11,
            "start-A",
            "end-A",
        ),
        (
            9000,
            1,
            "1",
            2,
            "route-B",
            "model-B",
            "synthetic-B",
            "ref-B",
            12,
            22,
            "start-B",
            "end-B",
        ),
        *(
            (
                number,
                2,
                "2",
                number - 99,
                "unrelated-route",
                "unrelated-model",
                "unrelated-session",
                "unrelated-ref",
                0,
                0,
                "unrelated-start",
                "unrelated-end",
            )
            for number in range(100, 5100)
        ),
    ]

    if standalone:
        insert_sql = insert_sql.replace("'agent_run'", "'workbench'")
        source_attempts = [
            (
                row[0],
                None,
                "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
                if row[1] == 1
                else f"00000000-0000-4000-8000-{row[0]:012d}",
                row[3] if row[1] == 1 else 1,
                *row[4:],
            )
            for row in source_attempts
        ]
    db.executemany(insert_sql, source_attempts)
    expected = {}
    for parent in (1, 3):
        expected[parent] = {
            "events": [
                tuple(row)
                for row in db.execute(
                    "select runtime_kind,session_id,transcript_reference,transcript_start,transcript_end,started_at,finished_at from agent_runtime_attempts where agent_run_id=? and session_id<>'' order by id",
                    (parent,),
                )
            ],
            "result": db.execute(
                "select runtime_kind,session_id,transcript_start,transcript_end from agent_runtime_attempts where agent_run_id=? and status='completed' order by id desc limit 1",
                (parent,),
            ).fetchone(),
            "route": db.execute(
                "select route_name,model from agent_runtime_attempts where agent_run_id=? order by id desc limit 1",
                (parent,),
            ).fetchone(),
        }
        expected[parent]["result"] = (
            tuple(expected[parent]["result"])
            if expected[parent]["result"] is not None
            else None
        )
        expected[parent]["route"] = (
            tuple(expected[parent]["route"])
            if expected[parent]["route"] is not None
            else ("", "")
        )
    if standalone:
        from app.native_standalone import ref_from_attempt, legacy_codex_ref

        expected["standalone"] = {}
        expected["standalone_exact"] = {}
        for key in (
            "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        ):
            row = db.execute(
                "select status,runtime_kind,session_id,transcript_start,transcript_end from agent_runtime_attempts where workload_kind='workbench' and workload_key=? order by (status='completed') desc,id desc limit 1",
                (key,),
            ).fetchone()
            expected["standalone"][key] = ref_from_attempt(row)
            exact = db.execute(
                "select runtime_kind,session_id,transcript_start,transcript_end from agent_runtime_attempts where workload_kind='workbench' and workload_key=? and status='completed' and session_id='synthetic-B' and transcript_start=12 and transcript_end=22 order by id desc limit 1",
                (key,),
            ).fetchone()
            expected["standalone_exact"][key] = (
                ref_from_attempt(exact)
                if exact is not None
                else legacy_codex_ref(
                    {
                        "codex_session_id": "synthetic-B",
                        "codex_transcript_start_line": 12,
                        "codex_transcript_end_line": 22,
                    }
                )
            )

    db.commit()
    db.execute("pragma foreign_keys=off")
    db.execute("begin immediate")
    migrate_run_schema(db)
    db.execute(
        "update service_state set value='2026-10-11.2' where key='store_schema_version'"
    )
    db.commit()
    assert (
        db.execute(
            "select id from runs where record_type='agent_runtime_attempts' and legacy_key='7'"
        ).fetchone()[0]
        != 7
    )
    db.close()
    return current_store_with_migrated_core(tmp_path, path), expected


@pytest.mark.parametrize("parent", [1, 3])
def test_provider_native_snapshot_seeks_parent_and_preserves_source_public_order(
    tmp_path, parent
):
    from app.run_storage import AGENT_RUN_SQL, RUNTIME_ATTEMPT_SQL

    store, expected = _provider_parent_lookup_store(tmp_path)
    with store._connect() as db:
        row = db.execute(
            f"select * from ({AGENT_RUN_SQL}) where id=?", (parent,)
        ).fetchone()
        progress = []
        db.set_progress_handler(lambda: progress.append(1) or 0, 100)
        try:
            run, events, result = store._agent_run_native_snapshot(row, db=db)
        finally:
            db.set_progress_handler(None, 0)
        assert run.id == parent
        assert [tuple(event) for event in events] == expected[parent]["events"]
        assert (tuple(result) if result is not None else None) == expected[parent][
            "result"
        ]
        assert len(progress) * 100 < 2000
        plans = [
            row[3]
            for row in db.execute(
                f"explain query plan select runtime_kind,session_id,transcript_reference,transcript_start,transcript_end,started_at,finished_at from ({RUNTIME_ATTEMPT_SQL}) where agent_run_id=? and session_id<>'' order by id",
                (parent,),
            )
        ]
        assert any("SEARCH" in detail and "parent_id=?" in detail for detail in plans)
        assert not any("SCAN" in detail for detail in plans)


@pytest.mark.parametrize("parent", [1, 3])
def test_latest_provider_route_seeks_parent_and_matches_source_latest_id(
    tmp_path, parent, monkeypatch
):
    from app.run_storage import RUNTIME_ATTEMPT_SQL

    store, expected = _provider_parent_lookup_store(tmp_path)
    open_connection = store._open_connection
    progress = []

    def measured_connection():
        db = open_connection()
        db.set_progress_handler(lambda: progress.append(1) or 0, 100)
        return db

    monkeypatch.setattr(store, "_open_connection", measured_connection)
    assert store.latest_runtime_route_for_agent_run(parent) == expected[parent]["route"]
    assert len(progress) * 100 < 2000
    with store._connect() as db:
        plans = [
            row[3]
            for row in db.execute(
                f"explain query plan select route_name,model from ({RUNTIME_ATTEMPT_SQL}) where agent_run_id=? order by id desc limit 1",
                (parent,),
            )
        ]
        assert any("SEARCH" in detail and "parent_id=?" in detail for detail in plans)
        assert not any("SCAN" in detail for detail in plans)


def test_provider_batch_parent_lookup_uses_scoped_range_in_public_id_order(tmp_path):
    from app.run_storage import RUNTIME_ATTEMPT_SQL

    store, expected = _provider_parent_lookup_store(tmp_path)
    with store._connect() as db:
        query = f"select agent_run_id,runtime_kind,session_id,transcript_reference,transcript_start,transcript_end,started_at,finished_at from ({RUNTIME_ATTEMPT_SQL}) where session_id<>'' and agent_run_id in (?,?) order by agent_run_id,id"
        progress = []
        db.set_progress_handler(lambda: progress.append(1) or 0, 100)
        try:
            rows = db.execute(query, (1, 3)).fetchall()
        finally:
            db.set_progress_handler(None, 0)
        assert [tuple(row) for row in rows] == [
            (1, *event) for event in expected[1]["events"]
        ]
        assert len(progress) * 100 < 2000
        plans = [row[3] for row in db.execute("explain query plan " + query, (1, 3))]
        assert any("SEARCH" in detail and "parent_id=?" in detail for detail in plans)
        assert not any("SCAN" in detail for detail in plans)


@pytest.mark.parametrize(
    "key",
    ["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"],
)
def test_exact_workbench_native_ref_uses_workload_scope_and_matches_source(
    tmp_path, key
):
    from app.native_standalone import latest_standalone_ref, exact_standalone_ref
    from app.run_storage import RUNTIME_ATTEMPT_SQL

    store, expected = _provider_parent_lookup_store(tmp_path, standalone=True)
    with store._connect() as db:
        for operation, expected_ref in (
            (
                lambda: latest_standalone_ref(
                    db, workload_kind="workbench", workload_key=key
                ),
                expected["standalone"][key],
            ),
            (
                lambda: exact_standalone_ref(
                    db,
                    workload_kind="workbench",
                    workload_key=key,
                    row={
                        "codex_session_id": "synthetic-B",
                        "codex_transcript_start_line": 12,
                        "codex_transcript_end_line": 22,
                    },
                ),
                expected["standalone_exact"][key],
            ),
        ):
            progress = []
            db.set_progress_handler(lambda: progress.append(1) or 0, 100)
            try:
                actual = operation()
            finally:
                db.set_progress_handler(None, 0)
            assert actual == expected_ref
            assert len(progress) * 100 < 2000
        plans = [
            row[3]
            for row in db.execute(
                f"explain query plan select runtime_kind,session_id,transcript_start,transcript_end from ({RUNTIME_ATTEMPT_SQL}) where workload_kind=? and workload_key=? and status='completed' and session_id=? and transcript_start=? and transcript_end=? order by id desc limit 1",
                ("workbench", key, "synthetic-B", 12, 22),
            )
        ]
        assert any(
            "SEARCH" in detail
            and "workload_kind=?" in detail
            and "workload_key=?" in detail
            for detail in plans
        )
        assert not any("SCAN" in detail for detail in plans)


def test_numeric_scoped_public_identity_is_canonical_and_has_one_unique_index(tmp_path):
    from app.run_storage import create_run_schema

    db = sqlite3.connect(tmp_path / "numeric-public.db")
    db.execute("create table reply_tasks(id integer primary key)")
    create_run_schema(db)
    insert_run(
        db,
        "agent_runs",
        dict(
            id=1,
            task_id=1,
            execution_generation="g",
            role="consumer",
            status="completed",
        ),
    )
    provider = dict(
        id=7,
        legacy_key="0007",
        parent_id=1,
        workload_kind="agent_run",
        workload_key="1",
        attempt_number=1,
        route_name="route",
        runtime_kind="codex_cli",
        credential_mode="local_oauth",
        model="model",
        status="completed",
    )
    insert_run(db, "agent_runtime_attempts", provider)
    assert db.execute("select legacy_key from runs where id=7").fetchone()[0] == "7"
    assert provider["legacy_key"] == "0007"
    with pytest.raises(sqlite3.IntegrityError):
        insert_run(
            db,
            "agent_runtime_attempts",
            provider | dict(id=8, legacy_key="7", attempt_number=2),
        )
    insert_run(
        db,
        "agent_run_state_events",
        dict(
            id=8, legacy_key="7", parent_id=1, phase="state", structured_error_json="{}"
        ),
    )
    assert (
        db.execute(
            "select count(*) from runs where cast(legacy_key as integer)=7"
        ).fetchone()[0]
        == 2
    )
    indexes = db.execute("pragma index_list(runs)").fetchall()
    assert not any(row[1].startswith("sqlite_autoindex_runs_") for row in indexes)
    assert any(row[1] == "idx_runs_public_id" and row[2] == 1 for row in indexes)
