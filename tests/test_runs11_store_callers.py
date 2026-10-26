"""Current51 Store caller regressions; run against the cumulative copied app."""
import inspect
import sqlite3

from app.meeting_alignment import _index_meeting_codex_session
from app.runs11_definitions import RUN11_SOURCE_PROFILES
from app.store import AutoReplyStore


def _migrated_store(tmp_path):
    return AutoReplyStore(tmp_path / "store.sqlite3")

def test_metadata_aliases_update_and_title_search(tmp_path):
    store = _migrated_store(tmp_path)
    for session, title in (("alias-a", "alpha"), ("alias-b", "bravo")):
        store.upsert_codex_session_search_index(
            session_id=session, source_type="meeting_alignment",
            source_id="45", title=title,
        )
    projection = "codex_session_search_index"
    with store._connect() as db:
        before = [dict(row) for row in db.execute(
            f"select * from {projection} order by id")]
        assert [row["id"] for row in before] == [1, 2]
        assert [(row["session_id"], row["source_id"], row["title"])
                for row in before] == [
                    ("alias-a", "45", "alpha"), ("alias-b", "45", "bravo")]
        assert all(row["created_at"] and row["updated_at"] for row in before)
        assert db.execute("select count(*) from codex_session_search_index").fetchone()[0] == 2
        assert not db.execute("select 1 from sqlite_schema where name like 'codex_session_search_fts%'").fetchall()
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


def test_metadata_update_uses_unique_session_index(tmp_path):
    store = _migrated_store(tmp_path)
    store.upsert_codex_session_search_index(
        session_id="session", source_type="meeting_alignment",
        source_id="45", title="old",
    )
    statement = (
        "update codex_session_search_index set source_type=?,source_id=?,title=?,"
        "updated_at=current_timestamp where session_id=?"
    )
    source = inspect.getsource(AutoReplyStore.upsert_codex_session_search_index)
    assert "on conflict(session_id)" in source
    with store._connect() as db:
        details = [row[3] for row in db.execute(
            "explain query plan " + statement,
            ("meeting_alignment", "60", "new", "session"),
        )]
    assert any("USING INDEX sqlite_autoindex_codex_session_search_index_1" in item for item in details)
    assert not any("SCAN codex_session_search_index" in item for item in details)


def test_search_tied_timestamp_uses_native_public_id_order(tmp_path):
    store = _migrated_store(tmp_path)
    for session in ("one", "two"):
        store.upsert_codex_session_search_index(
            session_id=session, source_type="meeting_alignment",
            source_id="45", title="same",
        )
    with store._connect() as db:
        db.execute("update codex_session_search_index set updated_at='2000-01-01 00:00:00'")
        assert [row[0] for row in db.execute(
            "select id from codex_session_search_index order by updated_at desc,id asc")] == [1, 2]
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
        result = db.execute(
            "insert into setup_wizard_events(step_id,action_id,status,finished_at) "
            "values('rollback','test','running',case when ? = 'running' then '' else current_timestamp end)",
            ("running",),
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
