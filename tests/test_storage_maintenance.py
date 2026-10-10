import hashlib
import json
import sqlite3

from app.storage_maintenance import compact_terminal_work_summary_inputs
from app.store import AutoReplyStore


def test_historical_work_input_compaction_is_idempotent_and_preserves_adopted_rows(tmp_path):
    store = AutoReplyStore(tmp_path / "legacy.sqlite3")
    project_id = store.create_work_project(title="Adopted project")
    update_id = store.create_work_update(
        project_id=project_id, source_type="local_file", source_ref="file#v1",
        summary="Adopted business result",
    )
    payload = json.dumps({"source": {"type": "local_file", "ref": "file#v1",
                                     "created_at": "2026-01-01T00:00:00Z"},
                          "summary": "old copied input body"})
    with sqlite3.connect(store.path) as db:
        terminal_id = db.execute(
            "insert into work_summary_inputs(source_type,source_ref,payload_json,status) "
            "values('local_file','file#v1',?,'done')", (payload,),
        ).lastrowid
        db.execute(
            "insert into task_agent_runs(summary_input_id,codex_session_id,status) "
            "values(?,'native-session','completed')", (terminal_id,),
        )
        skipped_id = db.execute(
            "insert into work_summary_inputs(source_type,source_ref,payload_json,status,attempts,error,created_at,updated_at) "
            "values('local_file','file#skipped',?,'skipped',2,'no task'," 
            "'2026-02-01 01:00:00','2026-02-02 02:00:00')", (payload,),
        ).lastrowid
    pending_id = store.enqueue_work_summary_input("local_file", "file#pending", payload)
    failed_id = store.enqueue_work_summary_input("local_file", "file#failed", payload)
    store.mark_work_summary_input_failed(failed_id, "retryable")

    first = compact_terminal_work_summary_inputs(store.path)
    second = compact_terminal_work_summary_inputs(store.path)
    assert first == {"rows_compacted": 2,
                     "logical_bytes_removed": 2 * (len(payload.encode("utf-8")) - 2)}
    assert second == {"rows_compacted": 0, "logical_bytes_removed": 0}
    terminal = store.get_work_summary_input(terminal_id)
    assert terminal.payload_json == "{}"
    assert terminal.source_created_at == "2026-01-01T00:00:00Z"
    assert terminal.body_sha256 == hashlib.sha256(payload.encode("utf-8")).hexdigest()
    assert terminal.body_bytes == len(payload.encode("utf-8"))
    skipped = store.get_work_summary_input(skipped_id)
    assert skipped.payload_json == "{}"
    assert (skipped.status, skipped.attempts, skipped.error,
            skipped.created_at, skipped.updated_at) == (
                "skipped", 2, "no task", "2026-02-01 01:00:00", "2026-02-02 02:00:00")
    assert store.get_work_summary_input(pending_id).payload_json == payload
    assert store.get_work_summary_input(failed_id).payload_json == payload
    with store._connect() as db:
        assert db.execute("select title from work_projects where id=?", (project_id,)).fetchone()[0] == "Adopted project"
        assert db.execute("select summary from work_updates where id=?", (update_id,)).fetchone()[0] == "Adopted business result"
        assert db.execute("select codex_session_id from task_agent_runs where summary_input_id=?", (terminal_id,)).fetchone()[0] == "native-session"
