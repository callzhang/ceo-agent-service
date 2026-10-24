"""Native records for Task domain fixtures, never production provider receipts."""
import json
from uuid import uuid4

import pytest

from app.run_entity_storage import create_runtime_attempt
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


def record_task_fixture_run(store, summary_input_id, *, decision_json, codex_session_id=""):
    run_id = store.create_professional_run(summary_input_id, session_id=codex_session_id)
    store._write_test_native_decision(run_id, decision_json)
    return run_id


def apply_task_fixture_decision(store, **kwargs):
    """Seed a real typed input for direct domain-unit fixtures, then its native output."""
    from app.task_agent import apply_task_agent_decision
    summary_id = kwargs["summary_input_id"]
    item = kwargs["work_item"]
    if kwargs.get("record_run", True) and kwargs.get("_db") is None:
        if store.get_work_summary_input(summary_id) is None:
            summary_id = store.enqueue_work_summary_input(
                item.source.type.value,
                item.source.ref + ":fixture:" + str(summary_id),
                item.model_dump_json(),
            )
            kwargs["summary_input_id"] = summary_id
    result = apply_task_agent_decision(store, **kwargs)
    if kwargs.get("record_run", True) and kwargs.get("_db") is None:
        from app.store import NATIVE_TASK_RUN_SQL
        with store._connect() as db:
            run = db.execute(f"select max(id) from ({NATIVE_TASK_RUN_SQL}) where summary_input_id=?", (summary_id,)).fetchone()[0]
        store._write_test_native_decision(run, kwargs["decision"].model_dump_json())
    return result


@pytest.fixture(autouse=True)
def task_native_records(tmp_path, monkeypatch):
    root = tmp_path / "task-native"
    root.mkdir()
    paths = {}
    monkeypatch.setattr(native_trajectory, "find_codex_session_path", lambda session_id, **_kwargs: paths.get(session_id))

    def capture(store, run_id, body):
        # Explicit fixture output only: a missing fixture result is not replaced
        # with an empty candidate or an invented completed invocation.
        with store._connect() as db:
            session = str(uuid4())
            path = root / f"{session}.jsonl"
            path.write_text(_message(json.loads(body)) + "\n")
            paths[session] = path
            create_runtime_attempt(db, values={
                "parent_id": run_id, "workload_kind": "task", "workload_key": str(run_id),
                "attempt_number": 1, "route_name": "fixture", "runtime_kind": "codex_cli",
                "credential_mode": "local_oauth", "model": "fixture", "session_mode": "fresh",
                "source_session_id": "", "attempt_purpose": "normal",
                "validation_retry_policy_id": "", "validation_result_schema_id": "",
                "transcript_reference": f"codex_session:{session}", "session_id": session,
                "status": "completed", "transcript_start": 0, "transcript_end": 1,
                "lease_owner": "", "lease_expires_at": "", "failure_class": "",
                "failure_code": "", "failover_permitted": 0, "first_effect_started_at": "",
                "result_schema_id": "", "started_at": "", "finished_at": "",
                "created_at": "", "updated_at": "",
            })
    monkeypatch.setattr(AutoReplyStore, "_write_test_native_decision", capture, raising=False)
    return capture
