"""Only the captured 11.1 core declarations have the approved paired inverse."""

from contextlib import closing
import sqlite3

import pytest

from app.run_storage import (
    capture_run_schema,
    migrate_run_schema,
    restore_run_schema,
)
from tests.test_run_schema_release import legacy_database


def _fingerprint(db):
    return {
        "schema": db.execute(
            "select type,name,tbl_name,sql from sqlite_master order by type,name"
        ).fetchall(),
        "rows": {
            table: db.execute(f'select * from "{table}" order by rowid').fetchall()
            for (table,) in db.execute(
                "select name from sqlite_master where type='table' and name not like 'sqlite_%' order by name"
            ).fetchall()
        },
        "sequences": db.execute(
            "select name,seq from sqlite_sequence order by name"
        ).fetchall(),
        "marker": db.execute("select * from service_state").fetchall(),
    }


def _replace_table(db, table, ddl):
    columns = [row[1] for row in db.execute(f'pragma table_info("{table}")')]
    rows = db.execute(f'select * from "{table}" order by id').fetchall()
    objects = db.execute(
        "select sql from sqlite_master where tbl_name=? and type<>'table' and sql is not null order by type,name",
        (table,),
    ).fetchall()
    sequence = db.execute(
        "select seq from sqlite_sequence where name=?", (table,)
    ).fetchone()
    db.execute(f'drop table "{table}"')
    db.execute(ddl)
    db.executemany(
        f'insert into "{table}" ({",".join(columns)}) values ({",".join("?" for _ in columns)})',
        rows,
    )
    for (statement,) in objects:
        db.execute(statement)
    if sequence is not None and "autoincrement" in ddl.lower():
        db.execute("update sqlite_sequence set seq=? where name=?", (*sequence, table))


def _assert_rejected_without_writes(db, match="unsupported 11.1 source"):
    before = _fingerprint(db)
    writes = []
    write_actions = {
        sqlite3.SQLITE_CREATE_INDEX,
        sqlite3.SQLITE_CREATE_TABLE,
        sqlite3.SQLITE_CREATE_TRIGGER,
        sqlite3.SQLITE_CREATE_VIEW,
        sqlite3.SQLITE_DELETE,
        sqlite3.SQLITE_DROP_INDEX,
        sqlite3.SQLITE_DROP_TABLE,
        sqlite3.SQLITE_DROP_TRIGGER,
        sqlite3.SQLITE_DROP_VIEW,
        sqlite3.SQLITE_INSERT,
        sqlite3.SQLITE_UPDATE,
        sqlite3.SQLITE_ALTER_TABLE,
    }

    def observe(action, first, second, *_):
        if action in write_actions:
            writes.append((action, first, second))
        return sqlite3.SQLITE_OK

    db.execute("begin immediate")
    db.set_authorizer(observe)
    try:
        with pytest.raises(ValueError, match=match):
            migrate_run_schema(db)
        assert writes == []
        # Compare before caller rollback, so rollback cannot hide mutation.
        assert _fingerprint(db) == before
    finally:
        db.set_authorizer(None)
        db.rollback()
    assert _fingerprint(db) == before


def test_check_that_breaks_new_failed_record_inverse_is_rejected(tmp_path):
    _, path = legacy_database(tmp_path)
    with closing(sqlite3.connect(path)) as db:
        source = db.execute(
            "select sql from sqlite_master where name='agent_runs'"
        ).fetchone()[0]
        _replace_table(db, "agent_runs", source[:-1] + ", check(status<>'failed'))")
        db.commit()
        with pytest.raises(sqlite3.IntegrityError, match="status<>'failed'"):
            db.execute(
                "insert into agent_runs(reply_task_id,execution_generation,role,status) values(1,'new-failed','consumer','failed')"
            )
        db.rollback()
        _assert_rejected_without_writes(db)


@pytest.mark.parametrize(
    "table,before,after",
    [
        pytest.param(
            "agent_runs",
            "foreign key(parent_agent_run_id) references agent_runs(id)",
            "foreign key(parent_agent_run_id) references agent_runs(id), unique(operation_id)",
            id="additional-unique",
        ),
        pytest.param(
            "agent_runs",
            "foreign key(parent_agent_run_id) references agent_runs(id)",
            "foreign key(parent_agent_run_id) references agent_runs(id), foreign key(operation_id) references external_operation(id)",
            id="additional-fk",
        ),
        pytest.param(
            "agent_runs",
            "default 'pending'",
            "default 'PENDING'",
            id="string-default-case",
        ),
        pytest.param(
            "agent_runs",
            "primary key autoincrement",
            "primary key",
            id="removed-autoincrement",
        ),
        pytest.param(
            "agent_runs",
            "reply_task_id integer not null",
            "reply_task_id integer",
            id="changed-null-contract",
        ),
        pytest.param(
            "agent_runtime_attempts",
            "references agent_runs(id)",
            "references agent_runs(id) on delete cascade",
            id="fk-delete-action",
        ),
        pytest.param(
            "agent_runtime_attempts",
            "references agent_runs(id)",
            "references agent_runs(id) on update cascade",
            id="fk-update-action",
        ),
        pytest.param(
            "agent_runtime_attempts",
            "references agent_runs(id)",
            "references agent_runs(id) deferrable initially deferred",
            id="fk-deferrability",
        ),
        pytest.param(
            "agent_run_state_events",
            "references agent_runs(id)",
            "references reply_tasks(id)",
            id="different-fk-parent",
        ),
        pytest.param(
            "agent_run_events",
            "default 'direct'",
            "default 'DIRECT'",
            id="empty-native-event-table-contract",
        ),
    ],
)
def test_changed_table_contract_is_rejected_before_mutation(
    tmp_path, table, before, after
):
    _, path = legacy_database(tmp_path)
    with closing(sqlite3.connect(path)) as db:
        source = db.execute(
            "select sql from sqlite_master where type='table' and name=?", (table,)
        ).fetchone()[0]
        assert before in source
        _replace_table(db, table, source.replace(before, after, 1))
        db.commit()
        _assert_rejected_without_writes(db)


@pytest.mark.parametrize(
    "name,before,after",
    [
        pytest.param(
            "idx_runtime_attempt_active_route",
            "status in ('starting', 'running')",
            "status in ('starting', 'RUNNING')",
            id="partial-string-literal-case",
        ),
        pytest.param(
            "idx_runtime_attempt_active_route",
            "CREATE UNIQUE INDEX",
            "CREATE INDEX",
            id="index-uniqueness",
        ),
        pytest.param(
            "idx_agent_runs_status",
            "(status, updated_at)",
            "(upper(status), updated_at)",
            id="index-expression",
        ),
        pytest.param(
            "idx_agent_runs_status",
            "(status, updated_at)",
            "(updated_at, status)",
            id="index-column-order",
        ),
        pytest.param(
            "idx_agent_runs_status",
            "(status, updated_at)",
            "(status, updated_at desc)",
            id="index-sort-order",
        ),
        pytest.param(
            "idx_agent_run_events_run_sequence",
            "(agent_run_id, sequence)",
            "(sequence, agent_run_id)",
            id="empty-native-event-index",
        ),
    ],
)
def test_changed_index_contract_is_rejected_before_mutation(
    tmp_path, name, before, after
):
    _, path = legacy_database(tmp_path)
    with closing(sqlite3.connect(path)) as db:
        source = db.execute(
            "select sql from sqlite_master where type='index' and name=?", (name,)
        ).fetchone()[0]
        assert before in source
        db.execute(f'drop index "{name}"')
        db.execute(source.replace(before, after, 1))
        db.commit()
        _assert_rejected_without_writes(db)


@pytest.mark.parametrize(
    "name,before,after",
    [
        pytest.param(
            "trg_runtime_attempt_lineage_immutable",
            "new.attempt_purpose<>old.attempt_purpose",
            "new.attempt_purpose=old.attempt_purpose",
            id="trigger-condition",
        ),
        pytest.param(
            "trg_runtime_attempt_lineage_immutable",
            "raise(abort, 'runtime attempt correction lineage is immutable')",
            "raise(ignore)",
            id="trigger-action",
        ),
        pytest.param(
            "trg_runtime_attempt_generalized_lease_insert",
            "new.status in ('starting', 'running')",
            "new.status in ('starting', 'RUNNING')",
            id="trigger-literal-case",
        ),
    ],
)
def test_changed_trigger_contract_is_rejected_before_mutation(
    tmp_path, name, before, after
):
    _, path = legacy_database(tmp_path)
    with closing(sqlite3.connect(path)) as db:
        source = db.execute(
            "select sql from sqlite_master where type='trigger' and name=?", (name,)
        ).fetchone()[0]
        assert before in source
        db.execute(f'drop trigger "{name}"')
        db.execute(source.replace(before, after, 1))
        db.commit()
        _assert_rejected_without_writes(db)


@pytest.mark.parametrize(
    "statement",
    [
        pytest.param(
            "create index additional_core_index on agent_runs(operation_id)",
            id="unknown-index",
        ),
        pytest.param("drop index idx_agent_run_events_run_scope", id="missing-index"),
        pytest.param(
            "create trigger additional_core_trigger before update of status on agent_runs when new.status='failed' begin select raise(abort,'extra restriction'); end",
            id="unknown-core-trigger",
        ),
        pytest.param(
            "create trigger additional_native_event_trigger before insert on agent_run_events begin select raise(ignore); end",
            id="unknown-empty-native-event-trigger",
        ),
        pytest.param(
            "drop trigger trg_runtime_attempt_lineage_insert", id="missing-trigger"
        ),
        pytest.param("drop table agent_run_events", id="missing-native-event-table"),
        pytest.param("drop table agent_run_state_events", id="partial-source"),
    ],
)
def test_unknown_or_missing_core_object_is_rejected_without_mutation(
    tmp_path, statement
):
    _, path = legacy_database(tmp_path)
    with closing(sqlite3.connect(path)) as db:
        db.execute(statement)
        db.commit()
        _assert_rejected_without_writes(db)


def test_empty_sources_still_require_the_exact_table_contract(tmp_path):
    _, path = legacy_database(tmp_path)
    with closing(sqlite3.connect(path)) as db:
        for table in (
            "receipt",
            "agent_run_state_events",
            "agent_runtime_attempts",
            "agent_runs",
        ):
            db.execute(f'delete from "{table}"')
        source = db.execute(
            "select sql from sqlite_master where name='agent_runs'"
        ).fetchone()[0]
        _replace_table(db, "agent_runs", source[:-1] + ", check(status<>'failed'))")
        db.commit()
        _assert_rejected_without_writes(db)


@pytest.mark.parametrize(
    "statement,match",
    [
        (
            "update agent_runs set final_result_json='retained native result'",
            "retired empty placeholder",
        ),
        (
            "update agent_runtime_attempts set result_envelope_json='retained native result'",
            "retired empty placeholder",
        ),
        (
            "insert into agent_run_events(agent_run_id,sequence,event_json) values(1,1,'retained native event')",
            "contains native events",
        ),
    ],
)
def test_existing_nonempty_source_reasons_precede_declaration_rejection(
    tmp_path, statement, match
):
    _, path = legacy_database(tmp_path)
    with closing(sqlite3.connect(path)) as db:
        db.execute(statement)
        db.execute("create index unsupported_core_index on agent_runs(operation_id)")
        db.commit()
        _assert_rejected_without_writes(db, match)


def test_extra_receipt_child_declarations_roundtrip_exactly(tmp_path):
    _, path = legacy_database(tmp_path)
    with closing(sqlite3.connect(path)) as db:
        db.executescript("""
            create table extra_receipt(
                id integer primary key autoincrement,
                agent_run_id integer not null references agent_runs(id)
                    on delete restrict deferrable initially deferred,
                body blob not null default x'ff00' check(length(body)>0),
                tag text not null default 'PENDING',
                unique(agent_run_id,tag)
            );
            create index extra_receipt_by_run on extra_receipt(agent_run_id,id desc);
            create trigger extra_receipt_touch after update of body on extra_receipt
                when new.body<>old.body begin select new.id; end;
            insert into extra_receipt(agent_run_id,body) values(1,x'ff0001');
            update sqlite_sequence set seq=100 where name='extra_receipt';
        """)
        before = _fingerprint(db)
        source = capture_run_schema(db)
        db.execute("begin immediate")
        migrate_run_schema(db)
        assert (
            db.execute("pragma foreign_key_list(extra_receipt)").fetchone()[2] == "runs"
        )
        restore_run_schema(db, source)
        db.commit()
        assert _fingerprint(db) == before
        assert db.execute("pragma foreign_key_check").fetchall() == []
