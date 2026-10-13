"""4C exact source declarations, raw identities, and inverse business facts."""

import sqlite3

import pytest

from app.store import AutoReplyStore
from app.task_storage import insert_task, task_physical_id
from app.run_storage import insert_run
from tests.workbench_schema_fixtures import (
    SOURCES,
    CHILDREN,
    SOURCE,
    source_database,
    rows,
    fingerprint,
)


def test_current_bootstrap_removes_only_approved_sources_and_caches(tmp_path):
    store = AutoReplyStore(tmp_path / "fresh.db")
    with store._connect() as db:
        assert (
            db.execute(
                "select value from service_state where key='store_schema_version'"
            ).fetchone()[0]
            == "2026-10-13.3"
        )
        tables = {
            r[0]
            for r in db.execute("select name from sqlite_master where type='table'")
        }
        assert not set(SOURCES) & tables
        assert set(CHILDREN) <= tables
        assert [
            r[1] for r in db.execute("pragma table_info(codex_session_search_index)")
        ] == [
            "id",
            "session_id",
            "source_type",
            "source_id",
            "title",
            "created_at",
            "updated_at",
        ]
    AutoReplyStore(store.path)


def test_text_public_ids_keep_leading_zero_empty_blob_and_null(tmp_path):
    store = AutoReplyStore(tmp_path / "fresh.db")
    with store._connect() as db:
        ids = ["01", "1", "uuid-A", "", b"\x00\xff", None, None]
        physical = [
            insert_task(
                db,
                "workbench_tasks",
                dict(legacy_key=key, title="title", runtime_kind="route"),
            ).lastrowid
            for key in ids
        ]
        assert [
            db.execute("select legacy_key from tasks where id=?", (key,)).fetchone()[0]
            for key in physical
        ] == ids
        for public, key in zip(ids[:-2], physical[:-2], strict=True):
            assert task_physical_id(db, "workbench_tasks", public) == key


def migrated(db):
    from app.workbench_storage import capture_workbench_schema, migrate_workbench_schema

    before = capture_workbench_schema(db)
    db.execute("begin immediate")
    migrate_workbench_schema(db)
    db.commit()
    return before


def test_forward_inverse_exact_rows_constraints_sequences_and_known_orphan(tmp_path):
    from app.workbench_storage import restore_workbench_schema, WORKBENCH_PROJECTIONS

    db = source_database(tmp_path / "source.db")
    before = fingerprint(db)
    saved = {
        t: rows(db, t)
        for t in (*SOURCES, *CHILDREN, "tasks", "runs", "codex_session_search_index")
    }
    state = migrated(db)
    for table in SOURCES:
        columns = [c[1] for c in SOURCE["tables"][table]["columns"]]
        actual = db.execute(
            "select "
            + ",".join(columns)
            + " from ("
            + WORKBENCH_PROJECTIONS[table]
            + ") order by id"
        ).fetchall()
        assert sorted(actual, key=repr) == sorted(saved[table], key=repr)
    for table in ("tasks", "runs"):
        old_columns = [c[1] for c in SOURCE["tables"][table]["columns"]]
        actual = db.execute(
            "select "
            + ",".join(old_columns)
            + f" from {table} where record_type not in (?,?,?,?) order by id",
            SOURCES,
        ).fetchall()
        assert actual == saved[table]
    assert db.execute("pragma foreign_key_check").fetchall() == [
        ("runs", 190042, "tasks", 1)
    ]
    assert db.execute(
        "select rowid from codex_session_search_fts where codex_session_search_fts match 'needle'"
    ).fetchall() == [(3,)]
    for table in CHILDREN:
        assert db.execute(f"select rowid from {table} order by rowid").fetchall() == [
            (row[0],) for row in before["source_rowids"][table]
        ]
        fk = db.execute(f"pragma foreign_key_list({table})").fetchone()
        assert (fk[2], fk[5], fk[6]) == ("tasks", "NO ACTION", "NO ACTION")
    db.execute("begin immediate")
    restore_workbench_schema(db, state)
    db.commit()
    assert fingerprint(db) == before


def test_source_constraints_defaults_and_raw_values_survive(tmp_path):
    from app.run_storage import SCHEDULED_RUN_SQL, WORKBENCH_EVENT_SQL
    from app.task_storage import WORKBENCH_TURN_SQL

    db = source_database(tmp_path / "source.db")
    migrated(db)
    db.execute("pragma foreign_keys=on")
    parent = task_physical_id(db, "workbench_tasks", "01")
    turn = task_physical_id(db, "workbench_turns", "turn-A")
    made = insert_task(
        db,
        "workbench_turns",
        dict(
            legacy_key="new",
            parent_id=parent,
            client_request_id="new",
            task_sequence=2,
            user_text="body",
            status="queued",
        ),
    ).lastrowid
    assert db.execute(
        f"select final_text,error_detail,resume_context,lease_owner,started_at from ({WORKBENCH_TURN_SQL}) where id='new'"
    ).fetchone() == ("", "", "", "", "")
    # Source SQL accepts unusual raw values but rejects its actual enum/check/unique constraints.
    event = insert_run(
        db,
        "workbench_events",
        dict(
            task_id=turn,
            sequence=-7,
            event_type="unknown type",
            payload_json=b"\x00\xff",
        ),
    ).lastrowid
    assert db.execute(
        f"select sequence,event_type,payload_json from ({WORKBENCH_EVENT_SQL}) where id=?",
        (event,),
    ).fetchone() == (-7, "unknown type", b"\x00\xff")
    scheduled = insert_run(
        db,
        "scheduled_task_runs",
        dict(
            event_id="new event",
            scheduled_task_id=7,
            trigger_kind="manual",
            scheduled_for="instant",
            occurrence_count=-8,
        ),
    ).lastrowid
    assert db.execute(
        f"select occurrence_count,lease_expires_at,dispatched_at,snapshot_id from ({SCHEDULED_RUN_SQL}) where id=?",
        (scheduled,),
    ).fetchone() == (-8, None, None, None)
    for values in (
        {"sequence": None, "event_type": "x"},
        {"sequence": 9, "event_type": None},
        {"event_type": "x"},
        {"sequence": 9},
    ):
        with pytest.raises(sqlite3.IntegrityError):
            insert_run(
                db, "workbench_events", dict(task_id=turn, payload_json="{}", **values)
            )
    with pytest.raises(sqlite3.IntegrityError):
        insert_task(
            db,
            "workbench_turns",
            dict(
                legacy_key="duplicate active",
                parent_id=parent,
                client_request_id="active",
                task_sequence=3,
                user_text="",
                status="running",
            ),
        )
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("update tasks set task_sequence=0 where id=?", (made,))
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("update tasks set parent_id=10 where id=?", (made,))
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("update workbench_artifacts set turn_id=?", (parent,))
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("update runs set snapshot_id=999 where id=?", (scheduled,))
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("delete from tasks where id=?", (turn,))


@pytest.mark.parametrize(
    "alteration",
    [
        "alter table workbench_events add column extra text",
        "update workbench_turns set final_text='retained body'",
        "update codex_session_search_index set embedding_json='[1]'",
        "update service_state set value='unknown'",
        "create table reply_tasks(id integer)",
        "drop index idx_workbench_events_event_type",
    ],
)
def test_malformed_or_nonempty_source_rejected_before_mutation(tmp_path, alteration):
    from app.workbench_storage import migrate_workbench_schema

    db = source_database(tmp_path / "source.db")
    db.execute(alteration)
    db.commit()
    before = fingerprint(db)
    db.execute("begin immediate")
    with pytest.raises(ValueError):
        migrate_workbench_schema(db)
    assert fingerprint(db) == before
    db.rollback()


def test_forward_interruption_rolls_back_every_table(tmp_path):
    from app.workbench_storage import migrate_workbench_schema

    db = source_database(tmp_path / "source.db")
    before = fingerprint(db)
    db.set_authorizer(
        lambda action, a, b, *rest: (
            sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_DROP_TABLE and a == "workbench_turns"
            else sqlite3.SQLITE_OK
        )
    )
    db.execute("begin immediate")
    with pytest.raises(sqlite3.DatabaseError):
        migrate_workbench_schema(db)
    db.rollback()
    db.set_authorizer(None)
    assert fingerprint(db) == before


def fts_state(db):
    return {
        name: db.execute(f"select * from {name}").fetchall()
        for (name,) in db.execute(
            "select name from sqlite_master where type='table' and name like 'codex_session_search_fts_%' order by name"
        ).fetchall()
    }


@pytest.mark.parametrize("indexed", [False, True])
def test_inverse_restores_exact_fts_shadow_state(tmp_path, indexed):
    from app.workbench_storage import restore_workbench_schema

    db = source_database(tmp_path / "source.db")
    if not indexed:
        db.execute(
            "insert into codex_session_search_fts(codex_session_search_fts) values('delete-all')"
        )
    db.commit()
    before = fts_state(db)
    state = migrated(db)
    db.execute("begin immediate")
    restore_workbench_schema(db, state)
    db.commit()
    assert fts_state(db) == before
    expected = [(3,)] if indexed else []
    assert (
        db.execute(
            "select rowid from codex_session_search_fts where codex_session_search_fts match 'needle'"
        ).fetchall()
        == expected
    )


def test_metadata_upsert_changes_only_title_tokens_and_preserves_metadata(tmp_path):
    store = AutoReplyStore(tmp_path / "current.db")
    store.upsert_codex_session_search_index(
        session_id="session",
        source_type="workbench",
        source_id="uuid",
        title="original word",
        summary_text="must not persist",
        fts_text="retired body",
        embedding=[1],
        embedding_model="ignored",
    )
    with store._connect() as db:
        before = tuple(
            db.execute("select * from codex_session_search_index").fetchone()
        )
        assert db.execute(
            "select rowid from codex_session_search_fts where codex_session_search_fts match 'original'"
        ).fetchall()
    store.upsert_codex_session_search_index(
        session_id="session",
        source_type="meeting_alignment",
        source_id="7",
        title="replacement title",
    )
    with store._connect() as db:
        after = tuple(db.execute("select * from codex_session_search_index").fetchone())
        assert (after[0], after[1], after[5]) == (before[0], before[1], before[5])
        assert after[2:5] == ("meeting_alignment", "7", "replacement title")
        assert not db.execute(
            "select rowid from codex_session_search_fts where codex_session_search_fts match 'original OR retired'"
        ).fetchall()
        assert [
            tuple(r)
            for r in db.execute(
                "select rowid from codex_session_search_fts where codex_session_search_fts match 'replacement'"
            )
        ] == [(before[0],)]


def test_native_compaction_uses_current_typed_events_and_title_cache(tmp_path):
    from app.run_storage import insert_run
    from app.storage_maintenance import compact_native_duplicates
    from app.task_storage import insert_task

    path = tmp_path / "current.db"
    store = AutoReplyStore(path)
    with sqlite3.connect(path) as db:
        parent = insert_task(
            db,
            "workbench_tasks",
            dict(legacy_key="parent", title="task", runtime_kind="codex"),
        ).lastrowid
        turn = insert_task(
            db,
            "workbench_turns",
            dict(
                legacy_key="turn",
                parent_id=parent,
                client_request_id="request",
                task_sequence=1,
                user_text="keep input",
                status="completed",
                error_detail="old diagnostic",
                execution_run_id="native-session",
            ),
        ).lastrowid
        native_event = insert_run(
            db,
            "workbench_events",
            dict(
                task_id=turn,
                sequence=0,
                event_type="text_delta",
                payload_json='{"native_ordinal":3,"text":"old body"}',
            ),
        ).lastrowid
        unknown_event = insert_run(
            db,
            "workbench_events",
            dict(
                task_id=turn,
                sequence=-1,
                event_type="unknown",
                payload_json=b"\x00\xff",
            ),
        ).lastrowid
    store.upsert_codex_session_search_index(
        session_id="native-session",
        source_type="workbench",
        source_id="turn",
        title="searchneedle",
    )
    result = compact_native_duplicates(path)
    assert result["payload_fields_cleared"] == 2
    with sqlite3.connect(path) as db:
        assert db.execute(
            "select legacy_key,parent_id,user_text,status,error_detail,execution_run_id from tasks where id=?",
            (turn,),
        ).fetchone() == (
            "turn",
            parent,
            "keep input",
            "completed",
            "",
            "native-session",
        )
        assert db.execute(
            "select task_id,sequence,event_type,payload_json from runs where id=?",
            (native_event,),
        ).fetchone() == (turn, 0, "text_delta", '{"native_ordinal":3}')
        assert db.execute(
            "select task_id,sequence,event_type,payload_json from runs where id=?",
            (unknown_event,),
        ).fetchone() == (turn, -1, "unknown", b"\x00\xff")
        assert db.execute(
            "select session_id,source_type,source_id,title from codex_session_search_index"
        ).fetchone() == ("native-session", "workbench", "turn", "searchneedle")
        assert db.execute(
            "select rowid from codex_session_search_fts where codex_session_search_fts match 'searchneedle'"
        ).fetchall() == [(1,)]
    assert compact_native_duplicates(path)["payload_fields_cleared"] == 0


@pytest.mark.parametrize("table", ["tasks", "runs"])
@pytest.mark.parametrize("kind", ["index", "trigger"])
def test_source_extra_core_objects_refuse_before_mutation(tmp_path, table, kind):
    from app.workbench_storage import migrate_workbench_schema

    db = source_database(tmp_path / "source.db")
    declaration = (
        f"create index unknown_source_rule on {table}(id)"
        if kind == "index"
        else f"create trigger unknown_source_rule before delete on {table} begin select raise(abort,'retained source rule'); end"
    )
    db.execute(declaration)
    db.commit()
    before = fingerprint(db), fts_state(db)
    db.execute("begin immediate")
    with pytest.raises(ValueError, match="source13"):
        migrate_workbench_schema(db)
    assert (fingerprint(db), fts_state(db)) == before
    db.rollback()
