from contextlib import closing
import sqlite3

import pytest

from app.store_schema_release import prepare_store_schema_release
from app.task_storage import insert_task
from app.run_storage import insert_run
from tests.workbench_schema_fixtures import source_database, fingerprint, CHILDREN


def target_and_source(tmp_path):
    root = tmp_path / "target"
    (root / "app").mkdir(parents=True)
    (root / "app/store.py").write_text("STORE_SCHEMA_VERSION = '2026-10-12.1'\n")
    path = tmp_path / "source.db"
    source_database(path).close()
    return root, path


def migrate(root, path):
    from app.workbench_storage import migrate_workbench_schema

    with closing(sqlite3.connect(path, isolation_level=None)) as db:
        db.execute("begin immediate")
        migrate_workbench_schema(db)
        db.execute(
            "update service_state set value='2026-10-12.1',updated_at='new time' where key='store_schema_version'"
        )
        db.commit()


def test_release_inverse_keeps_new_facts_and_deleted_allocation_watermarks(tmp_path):
    root, path = target_and_source(tmp_path)
    release = prepare_store_schema_release(root, path)
    release.migrate = migrate
    release.publish()
    with closing(sqlite3.connect(path)) as db, db:
        parent = insert_task(
            db,
            "workbench_tasks",
            dict(legacy_key="new-parent", title="new title", runtime_kind="codex"),
        ).lastrowid
        turn = insert_task(
            db,
            "workbench_turns",
            dict(
                legacy_key="new-turn",
                parent_id=parent,
                client_request_id="new-request",
                task_sequence=1,
                user_text=b"\x00\xff",
                status="failed",
                error_detail="retained",
            ),
        ).lastrowid
        event = insert_run(
            db,
            "workbench_events",
            dict(
                task_id=turn,
                sequence=-8,
                event_type="unknown",
                payload_json=b"\x00\xff",
            ),
        ).lastrowid
        scheduled = insert_run(
            db,
            "scheduled_task_runs",
            dict(
                event_id="new-schedule",
                scheduled_task_id=7,
                trigger_kind="manual",
                scheduled_for="exact instant",
                execution_kind="reply_task",
                execution_id="01",
                result_summary=" exact result ",
            ),
        ).lastrowid
        core = insert_task(
            db,
            "work_summary_inputs",
            dict(source_type="new", source_ref="kept", payload_json=b"WSI1\x00\xff"),
        ).lastrowid
        run = insert_run(
            db,
            "task_agent_runs",
            dict(summary_input_id=core, projection_json=' {"receipt":1} '),
        ).lastrowid
        deleted_task = insert_task(
            db,
            "workbench_tasks",
            dict(legacy_key="deleted", title="deleted", runtime_kind="codex"),
        ).lastrowid
        db.execute("delete from tasks where id=?", (deleted_task,))
        deleted_run = insert_run(
            db,
            "workbench_events",
            dict(task_id=turn, sequence=19, event_type="deleted", payload_json="{}"),
        ).lastrowid
        db.execute("delete from runs where id=?", (deleted_run,))
        db.execute(
            "insert into workbench_artifacts values('new-artifact',?,'label','path','mime','time')",
            (turn,),
        )
        db.execute(
            "insert into workbench_attachments values('new-attachment',?,'request','name','mime',2,'sha','path','time')",
            (parent,),
        )
        db.execute(
            "update workbench_confirmations set result_json=? where id='confirmation'",
            (b"\x00\xff",),
        )
        child_rowids = {
            table: db.execute(f"select rowid,id from {table} order by rowid").fetchall()
            for table in CHILDREN
        }
    release.rollback()
    with closing(sqlite3.connect(path)) as db:
        for table in CHILDREN:
            assert (
                db.execute(f"select rowid,id from {table} order by rowid").fetchall()
                == child_rowids[table]
            )
        assert db.execute(
            "select task_id,user_text,error_detail,final_text from workbench_turns where id='new-turn'"
        ).fetchone() == ("new-parent", b"\x00\xff", "retained", "")
        assert db.execute(
            "select turn_id,sequence,payload_json from workbench_events where id=?",
            (event,),
        ).fetchone() == ("new-turn", -8, b"\x00\xff")
        assert db.execute(
            "select execution_id,result_summary from scheduled_task_runs where id=?",
            (scheduled,),
        ).fetchone() == ("01", " exact result ")
        assert db.execute(
            "select payload_json from tasks where id=?", (core,)
        ).fetchone() == (b"WSI1\x00\xff",)
        assert db.execute(
            "select projection_json from runs where id=?", (run,)
        ).fetchone() == (' {"receipt":1} ',)
        assert db.execute(
            "select turn_id from workbench_artifacts where id='new-artifact'"
        ).fetchone() == ("new-turn",)
        assert db.execute(
            "select task_id from workbench_attachments where id='new-attachment'"
        ).fetchone() == ("new-parent",)
        assert db.execute(
            "select result_json from workbench_confirmations where id='confirmation'"
        ).fetchone() == (b"\x00\xff",)
        assert (
            db.execute("select seq from sqlite_sequence where name='tasks'").fetchone()[
                0
            ]
            >= deleted_task
        )
        for table in ("runs", "scheduled_task_runs", "workbench_events"):
            assert (
                db.execute(
                    "select seq from sqlite_sequence where name=?", (table,)
                ).fetchone()[0]
                >= deleted_run
            )
        assert db.execute("pragma foreign_key_check").fetchall() == [
            ("runs", 190042, "tasks", 1)
        ]
        before = fingerprint(db)
    release.rollback()
    with closing(sqlite3.connect(path)) as db:
        assert fingerprint(db) == before
    release.publish()
    release.verify_loaded()


def test_inverse_failure_rolls_back_and_stale_inverse_refuses_other_version(
    tmp_path, monkeypatch
):
    import app.workbench_schema_release as module

    root, path = target_and_source(tmp_path)
    release = prepare_store_schema_release(root, path)
    release.migrate = migrate
    release.publish()
    with closing(sqlite3.connect(path)) as db:
        before = fingerprint(db)
    original = module.restore_workbench_schema

    def interrupted(db, state):
        original(db, state)
        raise RuntimeError("interrupted")

    monkeypatch.setattr(module, "restore_workbench_schema", interrupted)
    with pytest.raises(RuntimeError, match="interrupted"):
        release.rollback()
    with closing(sqlite3.connect(path)) as db, db:
        assert fingerprint(db) == before
        db.execute(
            "update service_state set value='future' where key='store_schema_version'"
        )
        db.commit()
        altered = fingerprint(db)
    with pytest.raises(ValueError, match="version"):
        release.rollback()
    with closing(sqlite3.connect(path)) as db:
        assert fingerprint(db) == altered


def test_fts_inverse_keeps_mixed_original_index_state_and_new_metadata(tmp_path):
    root, path = target_and_source(tmp_path)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute(
            "insert into codex_session_search_index(id,session_id,title) values(4,'unindexed','untouchedempty'),(5,'updated-unindexed','staleempty'),(6,'updated-indexed','staleindexed'),(7,'deleted-indexed','deletedword'),(8,'deleted-unindexed','deletedempty')"
        )
        db.execute(
            "insert into codex_session_search_fts(rowid,title) values(6,'staleindexed'),(7,'deletedword')"
        )
    release = prepare_store_schema_release(root, path)
    release.migrate = migrate
    release.publish()
    from app.store import AutoReplyStore

    store = object.__new__(AutoReplyStore)
    # Use the real DAO body with a normal connection to this intentionally scoped source.
    from contextlib import contextmanager

    @contextmanager
    def connection():
        with closing(sqlite3.connect(path)) as db, db:
            db.row_factory = sqlite3.Row
            yield db

    store._connect = connection
    for session, title in [
        ("updated-unindexed", "fresh formerlyempty"),
        ("updated-indexed", "fresh formerlyindexed"),
        ("new-session", "brandnew"),
    ]:
        store.upsert_codex_session_search_index(
            session_id=session, source_type="test", source_id=session, title=title
        )
    with closing(sqlite3.connect(path)) as db, db:
        for identity in (7, 8):
            oldtitle = db.execute(
                "select title from codex_session_search_index where id=?", (identity,)
            ).fetchone()[0]
            db.execute(
                "insert into codex_session_search_fts(codex_session_search_fts,rowid,title) values('delete',?,?)",
                (identity, oldtitle),
            )
            db.execute("delete from codex_session_search_index where id=?", (identity,))
    release.rollback()
    with closing(sqlite3.connect(path)) as db:

        def match(term):
            return db.execute(
                "select rowid from codex_session_search_fts where codex_session_search_fts match ? order by rowid",
                (term,),
            ).fetchall()

        assert match("needle") == [(3,)]
        assert match("untouchedempty") == []
        assert match("fresh") == [(5,), (6,)]
        assert match("brandnew")
        assert match("staleempty OR staleindexed OR deletedword OR deletedempty") == []
        assert db.execute(
            "select source_type,source_id from codex_session_search_index where session_id='new-session'"
        ).fetchone() == ("test", "new-session")
        assert db.execute(
            "select count(*) from codex_session_search_index where summary_text<>'' or fts_text<>'' or embedding_json<>''"
        ).fetchone() == (0,)
        db.execute(
            "insert into codex_session_search_fts(codex_session_search_fts,rank) values('integrity-check',0)"
        )
