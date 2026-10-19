from tests.test_email_legacy_schema import published_source as published_source
import sqlite3

import pytest

from app.store import AutoReplyStore, _INITIALIZED_STORE_PATHS
from app.workbench_storage import migrate_workbench_schema
from app.task20_current_schema import task20_workbench_schema_is_current
from tests.test_sent_delivery_source import SOURCE as SENT_SOURCE
from tests.workbench_schema_fixtures import full_source_database, fingerprint, SOURCE


def test_source13_auxiliary_copy_preserves_fresh_business_declarations(
    tmp_path, published_source,
):
    from app.action_reply_storage import _fingerprint
    from app.business_schema_release import (
        CORE_SOURCE_PARENTS, PARENT_HASHES, SOURCE_CHILD_FULL_HASHES, SOURCE_PARENTS,
        SERVICE_BUGFIX_SOURCE, SERVICE_BUGFIX_SOURCE_FINGERPRINT,
        capture_business_source,
    )

    with full_source_database(
        tmp_path / "source.db", tmp_path / "auxiliary.db", published_source,
    ) as db:
        captured = capture_business_source(db)
        assert captured["variant"] == "fresh"
        assert len(captured["parents"]) == 9
        assert set(captured["parents"]) == set(SOURCE_PARENTS.values())
        assert len(captured["children"]) == 15
        assert tuple(_fingerprint(captured["parents"][name]) for name in CORE_SOURCE_PARENTS.values()) == PARENT_HASHES["fresh"]
        assert _fingerprint(captured["parents"][SERVICE_BUGFIX_SOURCE]) == SERVICE_BUGFIX_SOURCE_FINGERPRINT
        assert _fingerprint(captured["children"]) == SOURCE_CHILD_FULL_HASHES["fresh"]


def test_nonempty_canceled_confirmation_source_refuses_before_mutation(
    tmp_path, published_source, monkeypatch,
):
    path = tmp_path / "source.db"
    db = full_source_database(path, tmp_path / "auxiliary.db", published_source)
    db.execute('begin immediate')
    migrate_workbench_schema(db)
    from app.action_storage import migrate_action_schema
    migrate_action_schema(db)
    from app.action_receipt_storage import migrate_action_receipt_schema
    migrate_action_receipt_schema(db)
    from app.action_postfix_storage import migrate_action_postfix_schema
    migrate_action_postfix_schema(db)
    for definition in SENT_SOURCE["fresh_candidate"].values():
        db.execute(next(obj["sql"] for obj in definition["objects"] if obj["type"] == "table"))
    for definition in SENT_SOURCE["fresh_candidate"].values():
        for obj in definition["objects"]:
            if obj["type"] != "table":
                db.execute(obj["sql"])
    db.execute("update service_state set value='2026-10-13.3' where key='store_schema_version'")
    db.commit()
    db.close()
    from tests.workbench_caller_fixtures import advance_source17_to_current_store
    import app.store_schema_release as schema_release

    prepare = schema_release.prepare_database_schema_release
    observed = {"called": False}

    def fingerprinting_prepare(root, target_path, **kwargs):
        with sqlite3.connect(target_path) as db:
            before = fingerprint(db)
            assert db.execute(
                "select count(*) from workbench_confirmations"
            ).fetchone()[0] == 1
        try:
            return prepare(root, target_path, **kwargs)
        finally:
            with sqlite3.connect(target_path) as db:
                assert fingerprint(db) == before
            observed["called"] = True

    monkeypatch.setattr(
        schema_release, "prepare_database_schema_release", fingerprinting_prepare
    )
    with pytest.raises(
        ValueError,
        match="canceled confirmation source is nonempty; explicit disposition required",
    ):
        advance_source17_to_current_store(path, published_source)
    assert observed["called"]


@pytest.mark.parametrize(
    "name,kind,literal",
    [
        ("idx_task20_active_turn", "index", "'queued'"),
        ("idx_scheduled_task_runs_scheduled_instant", "index", "'scheduled'"),
        ("trg_runs_workbench_turn_insert", "trigger", "'workbench_turns'"),
        (
            "trg_task20_parent_workbench_artifacts_insert",
            "trigger",
            "'workbench_turns'",
        ),
    ],
)
@pytest.mark.parametrize("allow_missing", [False, True])
def test_current_manifest_preserves_actual_literals(
    tmp_path, name, kind, literal, allow_missing
):
    store = AutoReplyStore(tmp_path / "current.db")
    with sqlite3.connect(store.path) as db:
        sql = db.execute(
            "select sql from sqlite_master where name=?", (name,)
        ).fetchone()[0]
        db.execute(f"drop {kind} {name}")
        db.execute(sql.replace(literal, literal.upper(), 1))
        assert not task20_workbench_schema_is_current(
            db, allow_missing_objects=allow_missing, include_feedback=True
        )
    _INITIALIZED_STORE_PATHS.discard(store.path.resolve())
    with sqlite3.connect(store.path) as db:
        before = fingerprint(db)
    with pytest.raises(sqlite3.IntegrityError, match="malformed"):
        AutoReplyStore(store.path)
    with sqlite3.connect(store.path) as db:
        assert fingerprint(db) == before


def test_missing_current_auxiliary_index_repairs_without_recreating_retired_tables(
    tmp_path,
):
    store = AutoReplyStore(tmp_path / "current.db")
    with sqlite3.connect(store.path) as db:
        db.execute("drop index idx_workbench_events_event_type")
        db.execute("drop trigger trg_task20_parent_workbench_artifacts_insert")
        assert not task20_workbench_schema_is_current(db, include_feedback=True)
        assert task20_workbench_schema_is_current(
            db, allow_missing_objects=True, include_feedback=True
        )
    _INITIALIZED_STORE_PATHS.discard(store.path.resolve())
    store = AutoReplyStore(store.path)
    with store._connect() as db:
        assert task20_workbench_schema_is_current(db, include_feedback=True)
        assert not db.execute(
            "select 1 from sqlite_master where name in ('workbench_tasks','workbench_turns','workbench_events','scheduled_task_runs')"
        ).fetchone()


def test_current_marker_with_mixed_source_is_rejected_without_mutation(tmp_path):
    store = AutoReplyStore(tmp_path / "mixed.db")
    with sqlite3.connect(store.path) as db:
        db.execute("create table workbench_turns(id text primary key)")
        before = fingerprint(db)
    _INITIALIZED_STORE_PATHS.discard(store.path.resolve())
    with pytest.raises(sqlite3.IntegrityError, match="unsupported or malformed"):
        AutoReplyStore(store.path)
    with sqlite3.connect(store.path) as db:
        assert fingerprint(db) == before


@pytest.mark.parametrize("marker", [None, "unknown"])
@pytest.mark.parametrize(
    "table,insert_sql",
    [
        (
            "scheduled_task_runs",
            "insert into scheduled_task_runs(event_id,scheduled_task_id,trigger_kind,scheduled_for) values('kept',7,'manual','instant')",
        ),
        (
            "workbench_tasks",
            "insert into workbench_tasks(id,title,runtime_kind) values('01','kept','codex')",
        ),
        (
            "workbench_turns",
            "insert into workbench_turns(id,task_id,client_request_id,task_sequence,user_text,status) values('01','parent','kept',1,x'00ff','completed')",
        ),
        (
            "workbench_events",
            "insert into workbench_events(turn_id,sequence,event_type,payload_json) values('turn',0,'unknown',x'00ff')",
        ),
    ],
    ids=[
        "scheduled_task_runs",
        "workbench_tasks",
        "workbench_turns",
        "workbench_events",
    ],
)
def test_partial_retired_source_without_known_marker_refuses_before_bootstrap(
    tmp_path, table, insert_sql, marker
):
    path = tmp_path / "partial.db"
    with sqlite3.connect(path) as db:
        declarations = SOURCE["tables"][table]["objects"]
        for kind, _, sql in declarations:
            if kind == "table":
                db.execute(sql)
        for kind, _, sql in declarations:
            if kind != "table" and sql:
                db.execute(sql)
        db.execute(insert_sql)
        if marker is not None:
            db.execute(
                "create table service_state(key text primary key,value text,updated_at text)"
            )
            db.execute(
                "insert into service_state values('store_schema_version',?,'kept time')",
                (marker,),
            )
        before = fingerprint(db)
    expected_diagnostic = "unsupported or malformed"
    with pytest.raises(sqlite3.IntegrityError, match=expected_diagnostic):
        AutoReplyStore(path)
    with sqlite3.connect(path) as db:
        assert fingerprint(db) == before
