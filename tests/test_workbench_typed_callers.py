"""Public Workbench identities remain separate from typed storage IDs."""

from tests.test_email_legacy_schema import published_source as published_source

import sqlite3

import pytest
from pathlib import Path

from app.workbench.store import WorkbenchStore


def test_current_workload_check_uses_native_turn_task_id(tmp_path: Path):
    store = WorkbenchStore(tmp_path / "worker.sqlite3")
    task = store.create_task(title="Current workload", runtime_kind="codex")
    turn = store.create_turn(task.id, user_text="Inspect", client_request_id="workload")
    with store._connect() as db:
        assert not store._runtime_operation_parent_exists(db, "workbench", turn.id)
    assert store.claim_next_turn(owner="worker") is not None
    with store._connect() as db:
        assert store._runtime_operation_parent_exists(db, "workbench", turn.id)
        assert not store._runtime_operation_parent_exists(db, "workbench", "999999")
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
