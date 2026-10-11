"""Native records for Task domain fixtures, never production provider receipts."""
import json
import inspect
from uuid import uuid4

import pytest

from app.native_standalone import latest_task_ref
from app import native_trajectory
from app.store import AutoReplyStore
from app.task_agent import _task_decision_candidates


def read_task_fixture_decision(store, run_id):
    with store._connect() as db:
        ref = latest_task_ref(db, run_id)
    assert ref is not None
    path = native_trajectory.find_codex_session_path(ref.session_id)
    assert path is not None
    stream = native_trajectory.read_native_result_stream(
        ref.kind, ref.session_id, ref.start, ref.end,
    )
    candidates = _task_decision_candidates(stream)
    assert candidates
    return candidates[-1]


def rewrite_task_fixture_decision(store, run_id, decision):
    with store._connect() as db:
        ref = latest_task_ref(db, run_id)
    assert ref is not None
    path = native_trajectory.find_codex_session_path(ref.session_id)
    assert path is not None
    assert (ref.start, ref.end) == (0, 1)
    path.write_text(_message(decision) + "\n", encoding="utf-8")


def _message(decision):
    return json.dumps({"type": "response_item", "payload": {
        "type": "message", "role": "assistant",
        "content": [{"type": "output_text", "text": json.dumps(decision)}],
    }})


@pytest.fixture(autouse=True)
def task_native_records(tmp_path, monkeypatch):
    root = tmp_path / "task-native"
    root.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(root))
    paths = {}

    def native_path(session_id, **_kwargs):
        return paths.get(session_id)

    monkeypatch.setattr(native_trajectory, "find_codex_session_path", native_path)
    original = AutoReplyStore.record_task_agent_run
    original_finish = AutoReplyStore.finish_task_agent_run

    def capture(store, run_id, body, borrowed_db=None):
        # Historical domain fixtures are already terminal; seed their native
        # reference rather than reopening the parent through a runtime claim.
        with store._optional_connection(borrowed_db) as db:
            if latest_task_ref(db, run_id) is not None:
                return
            assert db.execute(
                "select decision_json from task_agent_runs where id=?", (run_id,),
            ).fetchone()[0] == "{}"
            session = str(uuid4())
            path = root / f"{session}.jsonl"
            path.write_text(_message(json.loads(body)) + "\n")
            paths[session] = path
            db.execute(
                "insert into agent_runtime_attempts "
                "(workload_kind,workload_key,attempt_number,route_name,runtime_kind,"
                "credential_mode,model,session_id,status,transcript_start,transcript_end) "
                "values ('task',?,1,'fixture','codex_cli','local_oauth','fixture',?,"
                "'completed',0,1)",
                (str(run_id), session),
            )

    def record(store, *args, **kwargs):
        bound = inspect.signature(original).bind(store, *args, **kwargs)
        bound.apply_defaults()
        run_id = original(store, *args, **kwargs)
        capture(store, run_id, bound.arguments["decision_json"])
        return run_id

    def finish(store, *args, **kwargs):
        bound = inspect.signature(original_finish).bind(store, *args, **kwargs)
        bound.apply_defaults()
        result = original_finish(store, *args, **kwargs)
        if bound.arguments["status"] == "completed":
            capture(store, bound.arguments["run_id"], bound.arguments["decision_json"], bound.arguments["_db"])
        return result

    monkeypatch.setattr(AutoReplyStore, "record_task_agent_run", record)
    monkeypatch.setattr(AutoReplyStore, "finish_task_agent_run", finish)
