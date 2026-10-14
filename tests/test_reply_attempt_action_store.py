from __future__ import annotations

import sqlite3
import json
from contextlib import contextmanager
from pathlib import Path

import pytest

from app.action_storage import (
    REPLY_SEQUENCE_NAMES,
    create_action_schema,
    insert_action,
    reply_action_projection,
    update_action,
    validated_reply_variant,
)
from app.codex_memory_write import run_codex_memory_write
from app.store import AutoReplyStore
from app import store as store_module
from app.action_reply_storage import capture_reply_source


class _Store:
    def __init__(self, path: Path) -> None:
        self.path = path

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        db.execute("pragma foreign_keys=on")
        try:
            with db:
                yield db
        finally:
            db.close()


class _Routed:
    def execute(self, **_kwargs):
        class Result:
            value = '{"status":"success","memory_id":"episode-19","retryable":false,"detail":""}'

        return Result()


def _target_store(tmp_path: Path, variant: str) -> tuple[_Store, int]:
    store = _Store(tmp_path / "target.sqlite3")
    with store._connect() as db:
        db.execute("create table tasks(id integer primary key,record_type text not null)")
        db.execute("create table runs(id integer primary key,record_type text not null)")
        create_action_schema(db, include_reply=True, reply_variant=variant)
        assert validated_reply_variant(db) == variant
        attempt_id = insert_action(db, "reply_attempts", {
            "conversation_id": "conversation", "conversation_title": "Title",
            "trigger_message_id": "message", "trigger_sender": "sender",
            "trigger_text": "text", "action": "no_reply",
            "sensitivity_kind": "general", "send_status": "skipped",
        }).lastrowid
        assert isinstance(attempt_id, int)
    return store, attempt_id


@pytest.mark.parametrize("variant", ("actual_no_memory_fk", "fresh_memory_fk"))
@pytest.mark.parametrize("status", ("pending", "failed", "written"))
def test_memory_completion_updates_only_eligible_target_event(
    tmp_path: Path, variant: str, status: str,
) -> None:
    store, attempt_id = _target_store(tmp_path, variant)
    with store._connect() as db:
        event_id = insert_action(db, "memory_write_events", {
            "attempt_id": attempt_id, "event_type": "memory_write",
            "status": status, "attempts": 4, "last_error": "old error",
            "updated_at": "2000-01-01 00:00:00",
        }).lastrowid
        assert isinstance(event_id, int)

    result = run_codex_memory_write(
        workspace=tmp_path, store=store, event_id=event_id,
        data="explicit input", type="text", created_at="2026-10-13",
        source_description="source", routed_execution=_Routed(),
    )

    assert result.episode_uuid == "episode-19"
    with store._connect() as db:
        rows = db.execute(
            "select status,attempts,last_error,memory_episode_id,updated_at "
            f"from ({reply_action_projection('memory_write_events', variant)}) "
            "where id=?", (event_id,),
        ).fetchall()
        assert len(rows) == 1
        assert tuple(rows[0][:4]) == ("written", 4, "", "episode-19")
        assert rows[0]["updated_at"] != "2000-01-01 00:00:00"
        assert db.execute("select seq from sqlite_sequence where name=?",
                          (REPLY_SEQUENCE_NAMES["memory_write_events"],)).fetchone()[0] == event_id


@pytest.mark.parametrize("variant", ("actual_no_memory_fk", "fresh_memory_fk"))
@pytest.mark.parametrize("missing", (False, True))
def test_memory_completion_rejects_ineligible_or_deleted_event_without_recreation(
    tmp_path: Path, variant: str, missing: bool,
) -> None:
    store, attempt_id = _target_store(tmp_path, variant)
    with store._connect() as db:
        event_id = insert_action(db, "memory_write_events", {
            "attempt_id": attempt_id, "event_type": "memory_write",
            "status": "pending", "attempts": 2,
        }).lastrowid
        assert isinstance(event_id, int)
        if missing:
            db.execute("delete from actions where record_type='memory_write_events' and legacy_key=?",
                       (str(event_id),))
        else:
            update_action(db, "memory_write_events", event_id, {"status": "skipped"})

    with pytest.raises(ValueError, match="not eligible"):
        run_codex_memory_write(
            workspace=tmp_path, store=store, event_id=event_id,
            data="explicit input", type="text", created_at="2026-10-13",
            source_description="source", routed_execution=_Routed(),
        )
    with store._connect() as db:
        rows = db.execute(
            f"select status,attempts from ({reply_action_projection('memory_write_events', variant)}) where id=?",
            (event_id,),
        ).fetchall()
        assert [tuple(row) for row in rows] == ([] if missing else [("skipped", 2)])


def test_store_extracts_paired_repeated_memory_calls_and_rebuilds_selected_attempt(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "store.sqlite3")
    output = json.dumps({"ok": True, "episode_uuid": "episode-pair",
                         "processing_status": "completed"})
    events = [
        {"tool": "memory_write", "call_id": "one", "input": "first"},
        {"tool": "tool_output", "call_id": "one", "output": output},
        {"tool": "memory_write", "call_id": "two", "input": "second"},
    ]
    attempt_id = store.record_reply_attempt(
        conversation_id="cid", conversation_title="Title", trigger_message_id="msg",
        trigger_sender="sender", trigger_text="text", action="send_reply",
        sensitivity_kind="general", audit_tool_events_json=json.dumps(events),
    )
    other_id = store.record_reply_attempt(
        conversation_id="other", conversation_title="Other", trigger_message_id="other",
        trigger_sender="sender", trigger_text="text", action="send_reply",
        sensitivity_kind="general", audit_tool_events_json=json.dumps(events[:2]),
    )
    prior = store.list_memory_write_events_for_attempt(attempt_id)
    assert [(e.event_type, e.status, e.memory_episode_id, e.payload_json) for e in prior] == [
        ("memory_write", "written", "episode-pair", "{}"),
        ("memory_write_2", "pending", "", "{}"),
    ]
    other_event_id = store.list_memory_write_events_for_attempt(other_id)[0].id
    with store._connect() as db:
        store._record_memory_write_events_in_connection(db, attempt_id, "[]")
    assert store.list_memory_write_events_for_attempt(attempt_id) == []
    assert store.list_memory_write_events_for_attempt(other_id)[0].id == other_event_id
    with store._connect() as db:
        store._record_memory_write_events_in_connection(db, attempt_id, json.dumps(events[:2]))
    rebuilt = store.list_memory_write_events_for_attempt(attempt_id)
    assert len(rebuilt) == 1 and rebuilt[0].id > prior[-1].id


@pytest.mark.parametrize("raw", ("invalid-json", "{}", "[]"))
def test_store_memory_rebuild_deletes_previous_rows_for_empty_or_malformed_input(
    tmp_path: Path, raw: str,
) -> None:
    store = AutoReplyStore(tmp_path / "store.sqlite3")
    attempt_id = store.record_reply_attempt(
        conversation_id="cid", conversation_title="Title", trigger_message_id="msg",
        trigger_sender="sender", trigger_text="text", action="send_reply",
        sensitivity_kind="general",
        audit_tool_events_json='[{"tool":"memory_write","call_id":"one"}]',
    )
    assert len(store.list_memory_write_events_for_attempt(attempt_id)) == 1
    with store._connect() as db:
        store._record_memory_write_events_in_connection(db, attempt_id, raw)
    assert store.list_memory_write_events_for_attempt(attempt_id) == []


_SOURCE = json.loads((Path(__file__).parents[1] / "docs/superpowers/specs/"
    "2026-10-13-database-redesign-phase5e-reply-source.json").read_text())


def _full_source18(path: Path, source_variant: str) -> None:
    AutoReplyStore(path)
    with sqlite3.connect(path) as db:
        db.execute("pragma foreign_keys=off")
        db.execute("begin immediate")
        db.execute("drop table actions")
        create_action_schema(db, include_delivery=True)
        for table in ("reply_attempts", "memory_write_events"):
            declaration = _SOURCE[source_variant]["tables"][table]
            db.execute(declaration["sql"])
            for index in reversed(declaration["indexes"]):
                if index["sql"]:
                    db.execute(index["sql"])
            for trigger in declaration["triggers"]:
                db.execute(trigger["sql"])
        db.execute("update service_state set value='2026-10-13.4' "
                   "where key='store_schema_version'")
        db.execute("create table email_schema_migrations("
                   "version integer primary key,applied_at text not null)")
        db.execute("insert into email_schema_migrations values(51,'source')")
        db.execute("insert into reply_attempts(id,conversation_id,conversation_title,"
                   "trigger_message_id,trigger_sender,trigger_text,action,"
                   "sensitivity_kind,send_status) values(19,'cid','Title','msg',"
                   "'sender','text','send_reply','general','pending')")
        db.execute("insert into memory_write_events(id,attempt_id,event_type,payload_json) "
                   "values(23,19,'memory_write','{\"tool\":\"memory_write\"}')")
        assert capture_reply_source(db)["variant"] == source_variant
    store_module._INITIALIZED_STORE_PATHS.discard(path.resolve())


@pytest.mark.parametrize("source_variant,target_variant", (
    ("actual", "actual_no_memory_fk"),
    ("fresh_candidate17", "fresh_memory_fk"),
))
def test_store_migrates_exact_source18_and_repairs_only_its_canonical_variant(
    tmp_path: Path, source_variant: str, target_variant: str,
) -> None:
    path = tmp_path / "source18.sqlite3"
    _full_source18(path, source_variant)
    store = AutoReplyStore(path)
    assert store.get_reply_attempt(19).trigger_text == "text"
    assert [(e.id, e.payload_json) for e in store.list_memory_write_events_for_attempt(19)] == [
        (23, "{}")]
    with store._connect() as db:
        assert validated_reply_variant(db) == target_variant
        assert db.execute("select value from service_state where key='store_schema_version'").fetchone()[0] == "2026-10-13.5"
        assert not db.execute("select 1 from sqlite_schema where name in "
            "('reply_attempts','memory_write_events')").fetchone()
        db.execute("drop index idx_actions_reply_created")
    store_module._INITIALIZED_STORE_PATHS.discard(path.resolve())
    AutoReplyStore(path)
    with store._connect() as db:
        assert validated_reply_variant(db) == target_variant
        assert db.execute("select 1 from sqlite_schema where name='idx_actions_reply_created'").fetchone()
    store_module._INITIALIZED_STORE_PATHS.discard(path.resolve())
    AutoReplyStore(path)


@pytest.mark.parametrize("unsupported_shape", ("extra_index", "retired_tables"))
def test_current_store_rejects_unknown_reply_action_declaration_without_mutation(
    tmp_path: Path, unsupported_shape: str,
) -> None:
    path = tmp_path / "unknown.sqlite3"
    AutoReplyStore(path)
    with sqlite3.connect(path) as db:
        if unsupported_shape == "extra_index":
            db.execute("create index unreviewed_attempt_index on actions(attempt_send_status)")
        else:
            for table in ("reply_attempts", "memory_write_events"):
                db.execute(_SOURCE["actual"]["tables"][table]["sql"])
        before = tuple(db.iterdump())
    store_module._INITIALIZED_STORE_PATHS.discard(path.resolve())
    with pytest.raises(sqlite3.IntegrityError, match="unsupported or malformed"):
        AutoReplyStore(path)
    with sqlite3.connect(path) as db:
        assert tuple(db.iterdump()) == before
