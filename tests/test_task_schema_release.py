from contextlib import closing
import sqlite3

import pytest

from app.store_schema_release import prepare_store_schema_release
from tests.test_task_storage import fingerprint, source_database


def target_and_source(tmp_path):
    root = tmp_path / "target"
    (root / "app").mkdir(parents=True)
    (root / "app/store.py").write_text("STORE_SCHEMA_VERSION = '2026-10-11.3'\n")
    path = tmp_path / "source.db"
    source_database(path).close()
    return root, path


def migrate(root, path):
    from app.task_storage import migrate_task_schema

    with closing(sqlite3.connect(path, isolation_level=None)) as db:
        db.execute("begin immediate")
        migrate_task_schema(db)
        db.execute(
            "update service_state set value='2026-10-11.3',updated_at='new' where key='store_schema_version'"
        )
        db.commit()


def test_explicit_11_2_release_keeps_new_records_and_deleted_allocations(tmp_path):
    from app.run_storage import insert_run
    from app.task_storage import insert_task

    root, path = target_and_source(tmp_path)
    release = prepare_store_schema_release(root, path)
    assert release is not None
    release.migrate = migrate
    release.publish()
    with closing(sqlite3.connect(path)) as db, db:
        created = insert_task(
            db,
            "work_summary_inputs",
            dict(source_type="new", source_ref="kept", payload_json=b"WSI1\x00\xff"),
        ).lastrowid
        deleted = insert_task(
            db,
            "work_summary_inputs",
            dict(source_type="new", source_ref="deleted", payload_json="{}"),
        ).lastrowid
        db.execute("delete from tasks where id=?", (deleted,))
        task_run = insert_run(
            db,
            "task_agent_runs",
            dict(
                summary_input_id=created,
                projection_json=' {"receipt": [2, 1]} ',
                status="completed",
                finished_at="",
            ),
        ).lastrowid
        deleted_run = insert_run(
            db, "task_agent_runs", dict(summary_input_id=created, status="failed")
        ).lastrowid
        db.execute("delete from runs where id=?", (deleted_run,))
    release.rollback()
    with closing(sqlite3.connect(path)) as db:
        assert db.execute(
            "select payload_json from work_summary_inputs where id=?", (created,)
        ).fetchone() == (b"WSI1\x00\xff",)
        assert db.execute(
            "select summary_input_id,projection_json,finished_at from task_agent_runs where id=?",
            (task_run,),
        ).fetchone() == (created, ' {"receipt": [2, 1]} ', "")
        for table in ("reply_tasks", "reply_task_inputs", "work_summary_inputs"):
            assert (
                db.execute(
                    "select seq from sqlite_sequence where name=?", (table,)
                ).fetchone()[0]
                >= deleted
            )
        assert (
            db.execute(
                "select seq from sqlite_sequence where name='task_agent_runs'"
            ).fetchone()[0]
            >= deleted_run
        )
        assert db.execute("pragma foreign_key_check").fetchall() == []
    release.rollback()


def test_inverse_failure_is_atomic(tmp_path, monkeypatch):
    import app.task_schema_release as module

    root, path = target_and_source(tmp_path)
    release = prepare_store_schema_release(root, path)
    release.migrate = migrate
    release.publish()
    with closing(sqlite3.connect(path)) as db:
        before = fingerprint(db)
    original = module.restore_task_schema

    def interrupted(db, state):
        original(db, state)
        raise RuntimeError("interrupted")

    monkeypatch.setattr(module, "restore_task_schema", interrupted)
    with pytest.raises(RuntimeError, match="interrupted"):
        release.rollback()
    with closing(sqlite3.connect(path)) as db:
        assert fingerprint(db) == before


@pytest.mark.parametrize(
    "alteration",
    [
        "alter table task_agent_runs add column unknown text",
        "update task_agent_runs set decision_json='not empty'",
        "create table tasks(id integer)",
        "update service_state set value='unknown'",
        "create table agent_runs(id integer)",
    ],
)
def test_malformed_source_is_rejected_without_changes(tmp_path, alteration):
    from app.task_storage import migrate_task_schema

    _, path = target_and_source(tmp_path)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute(alteration)
    with closing(sqlite3.connect(path)) as db:
        before = fingerprint(db)
        db.execute("begin immediate")
        with pytest.raises(ValueError):
            migrate_task_schema(db)
        db.rollback()
        assert fingerprint(db) == before


def test_forward_interruption_is_atomic_after_target_creation(tmp_path):
    from app.task_storage import migrate_task_schema

    _, path = target_and_source(tmp_path)
    with closing(sqlite3.connect(path)) as db:
        before = fingerprint(db)
        calls = 0

        def interrupt(action, first, second, *_):
            nonlocal calls
            if action == sqlite3.SQLITE_DROP_TABLE and first == "runs":
                calls += 1
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        db.set_authorizer(interrupt)
        db.execute("begin immediate")
        with pytest.raises(sqlite3.DatabaseError):
            migrate_task_schema(db)
        db.rollback()
        db.set_authorizer(None)
        assert calls == 1
        assert fingerprint(db) == before


def test_same_named_columns_with_changed_null_contract_are_rejected_before_ddl(
    tmp_path,
):
    from app.task_storage import migrate_task_schema

    _, path = target_and_source(tmp_path)
    with closing(sqlite3.connect(path)) as db, db:
        source = db.execute(
            "select sql from sqlite_master where name='task_agent_runs'"
        ).fetchone()[0]
        data = db.execute("select * from task_agent_runs").fetchall()
        db.execute("drop table task_agent_runs")
        db.execute(
            source.replace(
                "summary_input_id integer not null", "summary_input_id integer"
            )
        )
        db.executemany(
            "insert into task_agent_runs values (?,?,?,?,?,?,?,?,?,?,?,?)", data
        )
    with closing(sqlite3.connect(path)) as db:
        before = fingerprint(db)
        db.execute("begin immediate")
        with pytest.raises(ValueError, match="source declaration"):
            migrate_task_schema(db)
        # Rejection occurs before target DDL, independent of caller rollback.
        assert fingerprint(db) == before
        db.rollback()


def _replace_source_table(db, table, ddl):
    """Change one source declaration without changing its persisted values."""
    columns = [row[1] for row in db.execute(f'pragma table_info("{table}")')]
    values = db.execute(f'select * from "{table}" order by id').fetchall()
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
        values,
    )
    for (statement,) in objects:
        db.execute(statement)
    if sequence is not None and "autoincrement" in ddl.lower():
        db.execute("update sqlite_sequence set seq=? where name=?", (*sequence, table))


def _assert_source_rejected_before_mutation(db):
    from app.task_storage import migrate_task_schema

    before = fingerprint(db)
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
        with pytest.raises(ValueError, match="source declaration"):
            migrate_task_schema(db)
        assert writes == []
        assert fingerprint(db) == before
    finally:
        db.set_authorizer(None)
        db.rollback()
    assert fingerprint(db) == before


@pytest.mark.parametrize(
    "table,before,after",
    [
        pytest.param("task_agent_runs", "projection_json text not null default '{}')", "projection_json text not null default '{}', check(summary_input_id>0))", id="additional-check"),
        pytest.param("task_agent_runs", "projection_json text not null default '{}')", "projection_json text not null default '{}', foreign key(summary_input_id) references work_summary_inputs(id))", id="additional-summary-fk"),
        pytest.param("task_agent_runs", "projection_json text not null default '{}')", "projection_json text not null default '{}', unique(summary_input_id))", id="additional-unique"),
        pytest.param("task_agent_runs", " primary key autoincrement", " primary key", id="removed-autoincrement"),
        pytest.param("reply_task_inputs", "references reply_tasks(id)", "references reply_tasks(id) on delete cascade", id="fk-delete-action"),
        pytest.param("reply_task_inputs", "references reply_tasks(id)", "references reply_tasks(id) on update cascade", id="fk-update-action"),
        pytest.param("reply_task_inputs", "references reply_tasks(id)", "references reply_tasks(id) deferrable initially deferred", id="fk-deferrability"),
        pytest.param("reply_task_inputs", "references reply_tasks(id)", "references work_summary_inputs(id)", id="different-fk-parent"),
        pytest.param("reply_tasks", "default 'pending'", "default 'PENDING'", id="string-default-case"),
    ],
)
def test_unmapped_source_constraints_are_rejected_before_mutation(
    tmp_path, table, before, after
):
    _, path = target_and_source(tmp_path)
    with closing(sqlite3.connect(path)) as db:
        source = db.execute(
            "select sql from sqlite_master where type='table' and name=?", (table,)
        ).fetchone()[0]
        assert before in source
        _replace_source_table(db, table, source.replace(before, after, 1))
        db.commit()
        _assert_source_rejected_before_mutation(db)


@pytest.mark.parametrize(
    "name,before,after",
    [
        pytest.param("idx_task_agent_runs_active_input", "status='running'", "status='RUNNING'", id="partial-literal-case"),
        pytest.param("idx_task_agent_runs_active_input", "CREATE UNIQUE INDEX", "CREATE INDEX", id="index-uniqueness"),
        pytest.param("idx_task_agent_runs_input", "(summary_input_id, id)", "(abs(summary_input_id), id)", id="index-expression"),
        pytest.param("idx_task_agent_runs_input", "(summary_input_id, id)", "(id, summary_input_id)", id="index-column-order"),
        pytest.param("idx_work_summary_inputs_updated", "updated_at desc", "updated_at asc", id="index-sort-order"),
    ],
)
def test_changed_source_indexes_are_rejected_before_mutation(
    tmp_path, name, before, after
):
    _, path = target_and_source(tmp_path)
    with closing(sqlite3.connect(path)) as db:
        source = db.execute(
            "select sql from sqlite_master where type='index' and name=?", (name,)
        ).fetchone()[0]
        assert before in source
        db.execute(f'drop index "{name}"')
        db.execute(source.replace(before, after, 1))
        db.commit()
        _assert_source_rejected_before_mutation(db)


@pytest.mark.parametrize(
    "statement",
    [
        pytest.param("create index additional_source_index on task_agent_runs(status)", id="unknown-index"),
        pytest.param("drop index idx_task_agent_runs_input", id="missing-index"),
        pytest.param("create trigger source_status_constraint before update of status on task_agent_runs when new.status='running' begin select raise(abort,'constraint'); end", id="unknown-trigger"),
        pytest.param("create trigger source_status_constraint before update of status on task_agent_runs when new.status='RUNNING' begin select raise(abort,'constraint'); end", id="changed-trigger-condition"),
        pytest.param("create trigger source_status_constraint before update of status on task_agent_runs when new.status='running' begin select raise(ignore); end", id="changed-trigger-action"),
    ],
)
def test_unknown_source_objects_are_rejected_before_mutation(tmp_path, statement):
    _, path = target_and_source(tmp_path)
    with closing(sqlite3.connect(path)) as db:
        db.execute(statement)
        db.commit()
        _assert_source_rejected_before_mutation(db)


# Frozen from app/store.py at 0c9aad4b: the actual fresh 11.2 Task template.
# It differs from the captured deployed source; current bootstrap must not be
# used as an oracle for a retired schema. No Git checkout is needed by this test.
FRESH_11_2_TASK_DDL = """create table task_agent_runs (
                    id integer primary key autoincrement,
                    summary_input_id integer not null,
                    codex_session_id text not null default '',
                    decision_json text not null default '{}',
                    projection_json text not null default '{}',
                    audit_summary text not null default '',
                    memory_recall_used integer not null default 0,
                    status text not null default 'completed'
                        check(status in ('running', 'completed', 'failed')),
                    error text not null default '',
                    created_at text not null default current_timestamp,
                    finished_at text not null default '',
                    updated_at text not null default current_timestamp
                )"""


def test_actual_fresh_11_2_task_template_is_not_a_supported_source(tmp_path):
    ddl = FRESH_11_2_TASK_DDL
    with closing(sqlite3.connect(":memory:")) as template:
        template.execute(ddl)
        assert "check(status in ('running', 'completed', 'failed'))" in ddl
        assert template.execute("pragma foreign_key_list(task_agent_runs)").fetchall() == []
        assert "updated_at text not null default current_timestamp" in ddl
    _, path = target_and_source(tmp_path)
    with closing(sqlite3.connect(path)) as db:
        db.execute("update task_agent_runs set status='completed'")
        _replace_source_table(db, "task_agent_runs", ddl)
        db.commit()
        _assert_source_rejected_before_mutation(db)


@pytest.mark.parametrize("failure", ["dependencies", "verification", "restart", "health", "loaded"])
def test_task_publication_failure_inverses_before_old_start_and_keeps_replacement_writes(tmp_path, failure):
    import json

    from app.repository_updater import RepositoryUpdater, UpgradeFailed
    from app.run_storage import insert_run
    from app.task_storage import insert_task
    from tests.test_repository_updater_publication import _State, _repo

    checkout, operation = _repo(tmp_path)
    root, path = target_and_source(tmp_path)
    with closing(sqlite3.connect(path)) as db:
        source = fingerprint(db)
    publication = prepare_store_schema_release(root, path)
    publication.migrate = migrate
    events, inserted = [], {}
    starts = 0
    state = _State()
    verify, inverse = publication.verify_loaded, publication.rollback

    def verify_loaded():
        if starts and failure == "loaded":
            raise RuntimeError("loaded failure")
        verify()

    def rollback():
        events.append("inverse")
        inverse()

    publication.verify_loaded = verify_loaded
    publication.rollback = rollback

    def start():
        nonlocal starts
        starts += 1
        events.append("start")
        with closing(sqlite3.connect(path)) as db, db:
            version = db.execute("select value from service_state where key='store_schema_version'").fetchone()[0]
            if starts == 1 and failure not in ("dependencies", "verification"):
                assert version == "2026-10-11.3"
                physical = insert_task(db, "work_summary_inputs", dict(source_type="replacement", source_ref="kept", payload_json=b"WSI1\x00\xff")).lastrowid
                inserted["work"] = db.execute("select cast(legacy_key as integer) from tasks where id=?", (physical,)).fetchone()[0]
                run = insert_run(db, "task_agent_runs", dict(summary_input_id=inserted["work"], status="completed", projection_json=' {"replacement": true} ')).lastrowid
                inserted["run"] = db.execute("select cast(legacy_key as integer) from runs where id=?", (run,)).fetchone()[0]
                inserted["receipt"] = db.execute("insert into receipt(reply_task_id,body) values(1,x'ff00')").lastrowid
                inserted["receipt_row"] = tuple(db.execute("select * from receipt where id=?", (inserted["receipt"],)).fetchone())
            else:
                assert version == "2026-10-11.2"
                assert fingerprint(db)["schema"] == source["schema"]
        if starts == 1 and failure == "restart":
            raise RuntimeError("partial replacement start")

    def fail_at(stage):
        events.append(stage)
        if stage == failure:
            raise RuntimeError(stage + " failure")

    updater = RepositoryUpdater(
        checkout, state, database_path=path,
        stop=lambda: events.append("stop"), restart=start,
        health=lambda: failure != "health" or starts > 1,
        database_publication=lambda _backup: publication,
        dependency_sync=lambda: fail_at("dependencies"),
        verification=lambda: fail_at("verification"),
    )
    with pytest.raises(UpgradeFailed):
        updater.execute(operation)
    assert events[-1] == "start"
    assert events.index("inverse") < len(events) - 1
    assert (checkout / "version").read_text() == "old\n"
    assert json.loads(state.values["repository_upgrade_operation:v1"])["status"] == "rolled_back"
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("pragma quick_check").fetchone()[0] == "ok"
        assert db.execute("pragma foreign_key_check").fetchall() == []
        if inserted:
            assert events.count("stop") == 2
            assert events.index("inverse") > events.index("stop", 1)
            assert db.execute("select payload_json from work_summary_inputs where id=?", (inserted["work"],)).fetchone() == (b"WSI1\x00\xff",)
            assert db.execute("select summary_input_id,projection_json from task_agent_runs where id=?", (inserted["run"],)).fetchone() == (inserted["work"], ' {"replacement": true} ')
            assert db.execute("select * from receipt where id=?", (inserted["receipt"],)).fetchone() == inserted["receipt_row"]
