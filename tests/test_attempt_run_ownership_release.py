from contextlib import closing
import json
from pathlib import Path
import sqlite3

import pytest

from app.current_schema_manifest import schema_is_current, schema_objects
from app.state_storage import upsert_state
from app.store_schema_release import prepare_database_schema_release
from app.task8_storage import register_task8_functions


def source_database(path):
    catalog = json.loads((Path(__file__).parents[1] / "app/schema_fixtures/store29_1_email54_catalog.json").read_text())
    with closing(sqlite3.connect(path)) as db:
        register_task8_functions(db)
        for kind, _name, _table, sql in catalog:
            if kind == "table":
                db.execute(sql)
        for kind, _name, _table, sql in catalog:
            if kind != "table":
                db.execute(sql)
        db.execute("begin")
        upsert_state(db, "service_state", {"key": "store_schema_version", "value": "2026-10-29.1"}, {"value": "json_quote(excluded.value)"})
        db.execute("insert into email_schema_migrations values(54,'source-time')")
        db.commit()


def test_release_selector_migrates_exact_previous_schema_and_current_data_inverse(tmp_path):
    path = tmp_path / "source.sqlite3"
    source_database(path)
    with closing(sqlite3.connect(path)) as db:
        before = schema_objects(db)
    release = prepare_database_schema_release(Path(__file__).parents[1], path)
    assert release is not None
    release.publish()
    release.verify_loaded()
    with closing(sqlite3.connect(path)) as db:
        assert schema_is_current(db)
        assert list(db.execute("pragma foreign_key_check")) == []
    release.rollback()
    with closing(sqlite3.connect(path)) as db:
        assert schema_objects(db) == before
    again = prepare_database_schema_release(Path(__file__).parents[1], path)
    again.publish()
    again.finalize()


def source_attempt(db, public_id, run_id, session, start, end, reason, summary):
    # Pure frozen-shape encoding, never the normalized current writer.
    from app.action_storage import _ATTEMPT_DEFAULTS, _action_payload
    from app.current_action_schema import current_action_payloads
    data = _ATTEMPT_DEFAULTS | {
        "conversation_id": "c", "conversation_title": "t", "trigger_message_id": str(public_id),
        "trigger_sender": "sender", "trigger_text": "input", "action": "agent_run",
        "sensitivity_kind": "general", "agent_run_id": run_id, "codex_session_id": session,
        "codex_transcript_start_line": start, "codex_transcript_end_line": end,
        "codex_reason": reason, "audit_summary": summary, "send_status": "failed",
        "created_at": "source-created", "updated_at": "source-updated",
    }
    blob = _action_payload(db, "reply_attempts", data, layout=current_action_payloads().layouts["reply_attempts"])
    db.execute("insert into actions(_kind,legacy_key,payload_json) values(15,?,?)", (str(public_id), blob))
    return blob


def test_raw_facts_and_new_rows_survive_inverse(tmp_path):
    from app.action_storage import insert_action, update_action, REPLY_ATTEMPT_COMMON_SQL
    from app.attempt_run_ownership_release import prepare_attempt_run_ownership_release
    from app.run_entity_storage import create_run

    path = tmp_path / "facts.sqlite3"
    source_database(path)
    with closing(sqlite3.connect(path)) as db:
        db.row_factory = sqlite3.Row
        run = create_run(db, values={"status": "completed", "session_id": "native", "transcript_start": 2, "transcript_end": 9})
        originals = {
            1: source_attempt(db, 1, run["id"], "native", 2, 9, "same", "same"),
            2: source_attempt(db, 2, run["id"], "different", 4, 7, "", "full failure detail"),
            3: source_attempt(db, 3, None, "orphan", 1, 3, "cause", "different failure detail"),
        }
        db.execute("update sqlite_sequence set seq=50 where name='actions:reply_attempts'")
        db.commit()
        water = [tuple(row) for row in db.execute("select rowid,name,seq from sqlite_sequence order by rowid")]
    release = prepare_attempt_run_ownership_release(path)
    release.publish()
    with closing(sqlite3.connect(path)) as db:
        assert [tuple(row) for row in db.execute("select rowid,name,seq from sqlite_sequence order by rowid")] == water
        assert db.execute("select json_extract(payload_json,'$[22]') from actions where legacy_key='1'").fetchone()[0] is None
        assert db.execute("select attempt_codex_reason from actions where legacy_key='2'").fetchone()[0] == ""
        db.row_factory = sqlite3.Row
        new_id = insert_action(db, "reply_attempts", {
            "conversation_id": "c", "conversation_title": "t", "trigger_message_id": "new",
            "trigger_sender": "s", "trigger_text": "t", "action": "agent_run", "sensitivity_kind": "general",
            "send_status": "completed", "codex_reason": "old summary", "audit_summary": "old summary",
            "agent_run_id": run["id"], "codex_session_id": "native",
            "codex_transcript_start_line": 2, "codex_transcript_end_line": 9,
        }).lastrowid
        assert new_id == 51
        update_action(db, "reply_attempts", new_id, {"audit_summary": "new failure detail"})
        expected = tuple(db.execute(f"select codex_reason,audit_summary,codex_session_id,codex_transcript_start_line,codex_transcript_end_line from ({REPLY_ATTEMPT_COMMON_SQL}) where id=?", (new_id,)).fetchone())
        db.commit()
    release.rollback()
    with closing(sqlite3.connect(path)) as db:
        for public_id, blob in originals.items():
            assert db.execute("select payload_json from actions where _kind=15 and legacy_key=?", (str(public_id),)).fetchone()[0] == blob
        actual = db.execute("select attempt_codex_reason,attempt_audit_summary,attempt_codex_session_id,attempt_codex_transcript_start_line,attempt_codex_transcript_end_line from actions where _kind=15 and legacy_key=?", (str(new_id),)).fetchone()
        assert tuple(actual) == expected
        assert db.execute("select seq from sqlite_sequence where name='actions:reply_attempts'").fetchone()[0] == 51
        assert list(db.execute("pragma foreign_key_check")) == []
    again = prepare_attempt_run_ownership_release(path)
    again.publish()
    again.finalize()


def test_publish_failure_rolls_back_schema_payload_and_sequence(tmp_path, monkeypatch):
    from app import attempt_run_ownership_release as module
    path = tmp_path / "atomic.sqlite3"
    source_database(path)
    with closing(sqlite3.connect(path)) as db:
        before = schema_objects(db)
        water = list(db.execute("select rowid,name,seq from sqlite_sequence order by rowid"))
    release = module.prepare_attempt_run_ownership_release(path)

    def fail(*args):
        raise RuntimeError("interrupted publication")

    monkeypatch.setattr(module, "_write_marker", fail)
    with pytest.raises(RuntimeError, match="interrupted publication"):
        release.publish()
    with closing(sqlite3.connect(path)) as db:
        assert schema_objects(db) == before
        assert list(db.execute("select rowid,name,seq from sqlite_sequence order by rowid")) == water
    release.rollback()


def test_mixed_source_rejected_without_writes(tmp_path):
    from app.attempt_run_ownership_release import prepare_attempt_run_ownership_release
    path = tmp_path / "mixed.sqlite3"
    source_database(path)
    with closing(sqlite3.connect(path)) as db:
        db.execute("create index unknown_source_index on runs(status)")
        before = schema_objects(db)
    with pytest.raises(ValueError, match="Unsupported"):
        prepare_attempt_run_ownership_release(path)
    with closing(sqlite3.connect(path)) as db:
        assert schema_objects(db) == before
