"""Run after applying the companion unselected patch to a copied app tree.

Example: PYTHONPATH=/tmp/owned-copy pytest -q this-file.py
The copied app is required because current constructors still select Runs8.
"""
import inspect
import sqlite3

from app.meeting_alignment import _index_meeting_codex_session
from app.runs11_definitions import RUN11_SOURCE_PROFILES, run11_projection
from app.runs11_schema_release import capture_run11_source, migrate_run11_schema
from app.runs11_storage import insert_run11_fact
from app.store import AutoReplyStore


def _migrated_store(tmp_path):
    store = AutoReplyStore(tmp_path / "store.sqlite3")
    # Store's current54 constructor does not own Email's source table.
    with store._connect() as db:
        declarations = RUN11_SOURCE_PROFILES["fresh"]["email_classifier_runtime_samples"]
        for _, _, sql in sorted(declarations, key=lambda row: row[0] != "table"):
            db.execute(sql)
    with store._connect() as db:
        db.execute("pragma foreign_keys=off")
        db.execute("begin")
        metadata = capture_run11_source(db, mapped=True)
        assert migrate_run11_schema(db, metadata) == {
            "runs": 0, "codex_session_search_index": 0,
            "email_classifier_runtime_samples": 0, "setup_wizard_events": 0,
        }
        db.commit()
    return store


def test_metadata_aliases_update_and_title_search(tmp_path):
    store = _migrated_store(tmp_path)
    for session, title in (("alias-a", "alpha"), ("alias-b", "bravo")):
        store.upsert_codex_session_search_index(
            session_id=session, source_type="meeting_alignment",
            source_id="45", title=title,
        )
    projection = "(" + run11_projection("codex_session_search_index") + ")"
    with store._connect() as db:
        before = [dict(row) for row in db.execute(
            f"select * from {projection} order by id")]
        assert [row["id"] for row in before] == [1, 2]
        assert [(row["session_id"], row["source_id"], row["title"])
                for row in before] == [
                    ("alias-a", "45", "alpha"), ("alias-b", "45", "bravo")]
        assert all(row["created_at"] and row["updated_at"] for row in before)
        assert db.execute("select count(*) from runs where record_type='codex_session_search_index'").fetchone()[0] == 2
        assert not db.execute("select 1 from sqlite_schema where name like 'codex_session_search_%'").fetchall()
    store.upsert_codex_session_search_index(
        session_id="alias-a", source_type="meeting_alignment",
        source_id="60", title="charlie",
    )
    with store._connect() as db:
        after = [dict(row) for row in db.execute(f"select * from {projection} order by id")]
    assert len(after) == 2
    assert after[0]["id"] == before[0]["id"]
    assert after[0]["session_id"] == before[0]["session_id"]
    assert after[0]["created_at"] == before[0]["created_at"]
    assert (after[0]["source_id"], after[0]["title"]) == ("60", "charlie")
    assert (after[1]["source_id"], after[1]["title"]) == ("45", "bravo")
    found = store.search_codex_sessions(fts_query="charlie", limit=5)
    assert [(item.session_id, item.native_reason) for item in found] == [
        ("alias-a", "native_reference_unavailable")]
    assert store.search_codex_sessions(fts_query="alpha") == []
    assert "embedding_client" not in inspect.signature(_index_meeting_codex_session).parameters


def test_metadata_update_uses_existing_public_id_index(tmp_path):
    store = _migrated_store(tmp_path)
    store.upsert_codex_session_search_index(
        session_id="session", source_type="meeting_alignment",
        source_id="45", title="old",
    )
    statement = (
        "update runs set source_type=?,source_id=?,title=?,updated_at=current_timestamp "
        "where record_type='codex_session_search_index' "
        "and cast(legacy_key as integer)=?"
    )
    source = inspect.getsource(AutoReplyStore.upsert_codex_session_search_index)
    assert "and cast(legacy_key as integer)=?" in source
    with store._connect() as db:
        details = [row[3] for row in db.execute(
            "explain query plan " + statement,
            ("meeting_alignment", "60", "new", 1),
        )]
    assert any("SEARCH runs USING INDEX idx_runs_public_id" in item for item in details)
    assert not any("SCAN runs" in item for item in details)


def test_search_tied_timestamp_uses_native_public_id_order(tmp_path):
    store = _migrated_store(tmp_path)
    source = sqlite3.connect(":memory:")
    for _, _, sql in sorted(RUN11_SOURCE_PROFILES["fresh"]["codex_session_search_index"],
                            key=lambda row: row[0] != "table"):
        if "codex_session_search_fts" not in sql:
            source.execute(sql)
    for session in ("one", "two"):
        source.execute(
            "insert into codex_session_search_index(session_id,source_type,source_id,title) "
            "values (?,'meeting_alignment','45','same')", (session,))
        store.upsert_codex_session_search_index(
            session_id=session, source_type="meeting_alignment",
            source_id="45", title="same",
        )
    projection = "(" + run11_projection("codex_session_search_index") + ")"
    source.execute("update codex_session_search_index set updated_at='2000-01-01 00:00:00'")
    assert source.execute(
        "select id,session_id,title,updated_at from codex_session_search_index order by id"
    ).fetchall() == [
        (1, "one", "same", "2000-01-01 00:00:00"),
        (2, "two", "same", "2000-01-01 00:00:00"),
    ]
    with store._connect() as db:
        db.execute("update runs set updated_at='2000-01-01 00:00:00' "
                   "where record_type='codex_session_search_index'")
        assert [row[0] for row in db.execute(
            f"select id from {projection} order by updated_at desc,id asc")] == [1, 2]
    source.close()
    # Equal-score Python sort is stable; the explicit metadata input order wins.
    assert [row.session_id for row in store.search_codex_sessions(
        fts_query="same", limit=2)] == ["one", "two"]


def test_setup_public_values_filters_and_rollback(tmp_path):
    store = _migrated_store(tmp_path)
    payloads = (
        dict(step_id="mcp", action_id="start", status="running",
             evidence_json="not-json", stdout_excerpt="out\x00", stderr_excerpt="err"),
        dict(step_id="mcp", action_id="finish", status="custom", summary="done",
             evidence_json='{"a":1}'),
        dict(step_id="other", action_id="finish", status="done"),
    )
    assert [store.record_setup_wizard_event(**row) for row in payloads] == [1, 2, 3]
    all_rows = store.list_setup_wizard_events(limit=9)
    assert [row["id"] for row in all_rows] == [3, 2, 1]
    assert [row["id"] for row in store.list_setup_wizard_events("mcp", limit=1)] == [2]
    assert [row["id"] for row in store.list_setup_wizard_events(
        action_id="finish", limit=9)] == [3, 2]
    assert [row["id"] for row in store.list_setup_wizard_events(
        "mcp", action_id="finish", limit=9)] == [2]
    running = all_rows[2]
    assert running["status"] == "running" and running["finished_at"] == ""
    assert running["started_at"] and running["evidence_json"] == "not-json"
    assert running["stdout_excerpt"] == "out\x00" and running["stderr_excerpt"] == "err"
    assert all_rows[1]["status"] == "custom" and all_rows[1]["finished_at"]
    with store._connect() as db:
        db.execute("begin")
        result = insert_run11_fact(db, "setup_wizard_events", {
            "step_id": "rollback", "action_id": "test", "status": "running"},
            insert_sql={"finished_at": "case when ? = 'running' then '' else current_timestamp end"},
            insert_parameters=("running",),
        )
        assert result.lastrowid == 4
        db.rollback()
    assert store.list_setup_wizard_events("rollback") == []


def test_native_fresh_setup_case_matches_target(tmp_path):
    store = _migrated_store(tmp_path)
    source = sqlite3.connect(":memory:")
    try:
        for _, _, sql in sorted(RUN11_SOURCE_PROFILES["fresh"]["setup_wizard_events"],
                                key=lambda row: row[0] != "table"):
            source.execute(sql)
        for status in ("running", "done", "other"):
            source.execute(
                "insert into setup_wizard_events(step_id,action_id,status,finished_at) "
                "values ('x','y',?,case when ?='running' then '' else current_timestamp end)",
                (status, status),
            )
            store.record_setup_wizard_event(step_id="x", action_id="y", status=status)
        native = source.execute(
            "select id,step_id,action_id,status,summary,evidence_json,stdout_excerpt,"
            "stderr_excerpt,finished_at from setup_wizard_events order by id").fetchall()
        target = [row for row in reversed(store.list_setup_wizard_events("x"))]
        fields = ("id", "step_id", "action_id", "status", "summary", "evidence_json",
                  "stdout_excerpt", "stderr_excerpt")
        assert [tuple(row[field] for field in fields) for row in target] == [
            row[:-1] for row in native]
        assert [bool(row["finished_at"]) for row in target] == [
            bool(row[-1]) for row in native]
    finally:
        source.close()
