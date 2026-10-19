"""Public Workbench identities remain separate from typed storage IDs."""

from tests.test_email_legacy_schema import published_source as published_source

import json
import sqlite3

import pytest
from pathlib import Path

from app.run_storage import insert_run
from app.task_storage import task_physical_id
from app.workbench import native_events
from app.workbench.store import WorkbenchStore


def test_public_event_cursor_and_tool_pair_use_legacy_key_with_reused_other_type(
    tmp_path: Path, monkeypatch,
):
    store = WorkbenchStore(tmp_path / "worker.sqlite3")
    task = store.create_task(title="Public cursor", runtime_kind="codex")
    turn = store.create_turn(task.id, user_text="Inspect", client_request_id="cursor")
    assert store.claim_next_turn(owner="worker") is not None
    started = {"tool_call_id": "call", "type": "CommandExecution", "command": "pwd", "status": "running"}
    completed = {**started, "status": "completed", "aggregated_output": "native output"}
    with store._connect() as db:
        # The migrated source cursor is 7, while its physical run row is 1.
        assert db.execute("select id from runs where record_type='workbench_events'").fetchone()[0] == 1
        db.execute(
            "update runs set legacy_key='7',event_type='tool_started',payload_json='{}' "
            "where record_type='workbench_events' and id=1"
        )
        insert_run(db, "agent_runtime_attempts", {
            "id": 7, "legacy_key": "7", "workload_kind": "workbench",
            "workload_key": turn.id, "attempt_number": 1, "route_name": "primary",
            "runtime_kind": "codex_cli", "credential_mode": "oauth", "model": "model",
            "status": "completed", "transcript_reference": "other typed record",
        })
    native_events.remember_live_payload(store.path, turn.id, 7, "tool_started", started)
    event = store.append_event(
        turn.id, sequence=2, event_type="tool_completed", payload=completed, owner="worker",
    )
    assert event.id == 8
    assert [item.id for item in store.events_after(turn.id, after_id=6, limit=1)] == [7]
    assert [item.id for item in store.events_after(turn.id, after_id=7, limit=1)] == [8]
    with store._connect() as db:
        assert json.loads(db.execute(
            "select payload_json from runs where record_type='workbench_events' and id=1"
        ).fetchone()[0]) == {"native_ordinal": 1}
        assert db.execute(
            "select transcript_reference from runs where record_type='agent_runtime_attempts' and legacy_key='7'"
        ).fetchone()[0] == "other typed record"
        assert db.execute(
            "select task_id from runs where record_type='workbench_events' and legacy_key='8'"
        ).fetchone()[0] == task_physical_id(db, "workbench_turns", turn.id)
        assert not db.execute(
            "select 1 from sqlite_master where name in ('workbench_tasks','workbench_turns','workbench_events')"
        ).fetchall()
        assert "final_text" not in {row["name"] for row in db.execute("pragma table_info(tasks)")}
    native_events.forget_live_turn(store.path, turn.id)
    monkeypatch.setattr(native_events, "_native_events_for_turn", lambda *_: ([(started, completed)], [], [], []))
    first, _ = store.event_stream_snapshot(turn.id, after_id=6, limit=1)
    second, _ = store.event_stream_snapshot(turn.id, after_id=7, limit=1)
    assert first[0].payload["command"] == "pwd"
    assert second[0].payload["output"] == "native output"


def test_current_workload_check_uses_public_text_turn_key(tmp_path: Path):
    store = WorkbenchStore(tmp_path / "worker.sqlite3")
    task = store.create_task(title="Current workload", runtime_kind="codex")
    turn = store.create_turn(task.id, user_text="Inspect", client_request_id="workload")
    with store._connect() as db:
        assert not store._runtime_operation_parent_exists(db, "workbench", turn.id)
    assert store.claim_next_turn(owner="worker") is not None
    with store._connect() as db:
        assert store._runtime_operation_parent_exists(db, "workbench", turn.id)
        assert not store._runtime_operation_parent_exists(db, "workbench", "missing-text-key")
    store.request_stop(turn.id)
    with store._connect() as db:
        assert not store._runtime_operation_parent_exists(db, "workbench", turn.id)


@pytest.mark.parametrize("source", ["current", "source13"])
def test_incomplete_workbench_declaration_is_rejected_without_mutation(tmp_path: Path, source, published_source):
    from app.store import _INITIALIZED_STORE_PATHS
    from tests.workbench_schema_fixtures import full_source_database

    path = tmp_path / "incomplete.sqlite3"
    if source == "current":
        store = WorkbenchStore(path)
        store.create_task(title="Retained task", runtime_kind="codex")
        with sqlite3.connect(path) as db:
            db.execute("alter table workbench_attachments drop column content_sha256")
    else:
        # Keep the malformed source13 intact: do not run the valid-source
        # fixture finalizer, which itself performs a source13 migration.
        db = full_source_database(path, path.with_suffix(".unrelated-schema.sqlite3"), published_source)
        with db:
            db.execute("insert into workbench_tasks(id,title,runtime_kind) values('parent','Retained task','codex')")
            db.execute("""insert into workbench_turns(id,task_id,client_request_id,task_sequence,user_text,status)
                values('turn','parent','request',1,'retained input','completed')""")
            db.execute("alter table workbench_turns drop column resume_context")
        db.close()
    # Match the native runtime source journal mode before recording refusal evidence.
    with sqlite3.connect(path) as db:
        db.execute("pragma journal_mode=WAL")
    _INITIALIZED_STORE_PATHS.discard(path.resolve())
    with sqlite3.connect(path) as db:
        before = tuple(db.iterdump())
    before_bytes = path.read_bytes()
    with pytest.raises((ValueError, sqlite3.IntegrityError)):
        WorkbenchStore(path)
    with sqlite3.connect(path) as db:
        assert tuple(db.iterdump()) == before
    assert path.read_bytes() == before_bytes
