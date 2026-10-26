import sqlite3

import pytest

from app.store import AutoReplyStore
from app.run_storage import insert_run


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
                "select name from sqlite_master where type='table' and name not in ('sqlite_sequence','state')"
            )
        ]
        before_rows = {
            table: db.execute(f'select * from "{table}"').fetchall() for table in tables
        }
    store = AutoReplyStore(path)
    with sqlite3.connect(path) as db:
        assert {
            table: db.execute(f'select * from "{table}"').fetchall() for table in tables
        } == before_rows
    assert store._schema_is_current() is True


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
