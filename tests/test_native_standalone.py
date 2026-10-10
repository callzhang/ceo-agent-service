import json
import sqlite3

from app import native_trajectory
from app.agent_envelope import AgentEnvelope
from app.store import AutoReplyStore
from app.task_agent import TaskAgentDecision
from tests.test_meeting_alignment import summary_decision


def _message(text):
    return json.dumps({"type": "response_item", "payload": {
        "type": "message", "role": "assistant",
        "content": [{"type": "output_text", "text": text}],
    }}, ensure_ascii=False)


def _attempt(db, run_id, workload_key, session, start, end, number):
    db.execute(
        "insert into agent_runtime_attempts "
        "(workload_kind, workload_key, attempt_number, route_name, runtime_kind, "
        "credential_mode, model, session_id, status, transcript_start, transcript_end) "
        "values ('task', ?, ?, 'test', 'codex_cli', 'local_oauth', 'test', ?, "
        "'completed', ?, ?)",
        (workload_key, number, session, start, end),
    )


def test_meeting_run_hydrates_only_exact_native_range_and_reports_unavailable(tmp_path, monkeypatch):
    store = AutoReplyStore(tmp_path / "store.sqlite3")
    native = tmp_path / "meeting.jsonl"
    old = summary_decision().model_copy(update={"final_message": "old turn"})
    current = summary_decision().model_copy(update={"final_message": "current turn"})
    native.write_text("\n".join((
        _message(old.model_dump_json()),
        json.dumps({"type": "event_msg", "payload": {
            "type": "item_completed", "item": {
                "type": "CommandExecution", "id": "tool-1",
                "command": "native evidence", "aggregated_output": "native output",
            },
        }}),
        _message(current.model_dump_json()),
        _message(old.model_dump_json()),
    )) + "\n")
    monkeypatch.setattr(native_trajectory, "find_codex_session_path", lambda *args, **kwargs: native)
    job = store.upsert_meeting_alignment_job(
        meeting_id="meeting", title="Meeting", source_json="{}",
        participants_json="[]", ended_at="2026-10-09", eligible_at="2026-10-09",
        status="no_action",
    )
    run = store.record_meeting_alignment_run(
        job_id=job, codex_session_id="native-meeting",
        decision_json=old.model_dump_json(), audit_summary="private audit",
        status="sent", error="", codex_transcript_start_line=1,
        codex_transcript_end_line=3, audit_tool_events_json='[{"private":"event"}]',
    )
    with store._connect() as db:
        row = db.execute(
            "select decision_json, audit_summary, audit_tool_events_json "
            "from meeting_alignment_runs where id=?", (run,)
        ).fetchone()
    assert tuple(row) == ("{}", "", "[]")
    loaded = store.get_meeting_alignment_run(run)
    assert loaded.native_available
    assert json.loads(loaded.decision_json)["final_message"] == "current turn"
    assert loaded.audit_summary == current.audit_summary
    assert json.loads(loaded.audit_tool_events_json)[0]["command"] == "native evidence"

    native.write_text(_message(old.model_dump_json()) + "\n")
    missing = store.get_meeting_alignment_run(run)
    assert not missing.native_available
    assert missing.native_reason == "native_source_unavailable"
    assert missing.decision_json == "{}"


def test_meeting_multiturn_reference_is_unavailable(tmp_path, monkeypatch):
    from app.native_standalone import meeting_decision

    native = tmp_path / "meeting.jsonl"
    native.write_text("\n".join((
        json.dumps({"type": "event_msg", "payload": {"type": "task_started"}}),
        _message(summary_decision().model_dump_json()),
        json.dumps({"type": "event_msg", "payload": {"type": "task_started"}}),
        _message(summary_decision().model_dump_json()),
    )) + "\n")
    monkeypatch.setattr(native_trajectory, "find_codex_session_path", lambda *args, **kwargs: native)
    decision = meeting_decision("native-meeting", 0, 4)
    assert not decision.available
    assert decision.reason == "native_source_unavailable"


def test_task_run_uses_latest_completed_exact_attempt_without_old_fallback(tmp_path, monkeypatch):
    store = AutoReplyStore(tmp_path / "store.sqlite3")
    native = tmp_path / "task.jsonl"
    first = TaskAgentDecision.model_validate({
        "project_decisions": [], "task_decisions": [], "project_assessments": [],
        "update_summary": "old decision",
    })
    current = first.model_copy(update={"update_summary": "new decision"})
    native.write_text("\n".join((
        _message(first.model_dump_json()),
        _message(current.model_dump_json()),
    )) + "\n")
    monkeypatch.setattr(native_trajectory, "find_codex_session_path", lambda *args, **kwargs: native)
    run = store.record_task_agent_run(
        summary_input_id=1, codex_session_id="native-task",
        decision_json=first.model_dump_json(), audit_summary="private audit",
        memory_recall_used=True,
    )
    with store._connect() as db:
        _attempt(db, run, str(run), "native-task", 0, 1, 1)
        _attempt(db, run, f"{run}:decision_repair.1", "native-task", 1, 2, 1)
        stored = db.execute(
            "select decision_json, audit_summary, memory_recall_used "
            "from task_agent_runs where id=?", (run,)
        ).fetchone()
    assert tuple(stored) == ("{}", "", 0)
    loaded = store.get_task_agent_run(run)
    assert loaded["native_available"] is True
    assert json.loads(loaded["decision_json"])["update_summary"] == "new decision"

    native.write_text(_message(first.model_dump_json()) + "\n")
    missing = store.get_task_agent_run(run)
    assert missing["native_available"] is False
    assert missing["decision_json"] == "{}"


def test_okr_run_keeps_business_items_and_reads_native_envelope(tmp_path, monkeypatch):
    store = AutoReplyStore(tmp_path / "store.sqlite3")
    envelope = AgentEnvelope.model_validate({
        "kind": "okr_review",
        "user_response": {"mode": "no_reply", "text": "", "sensitivity_kind": "general"},
        "system_actions": [], "domain_payload": {},
        "audit": {"summary": "native audit", "documents": [], "confidence": 1.0},
    })
    native = tmp_path / "okr.jsonl"
    native.write_text(_message(envelope.model_dump_json()) + "\n")
    monkeypatch.setattr(native_trajectory, "find_codex_session_path", lambda *args, **kwargs: native)
    run = store.record_okr_review_run(
        request_id=1, codex_session_id="native-okr",
        codex_transcript_start_line=0, codex_transcript_end_line=1,
        envelope_json='{"private":"copy"}', audit_tool_events_json='[{"private":"tool"}]',
        audit_summary="private audit",
    )
    store.record_okr_review_item(
        request_id=1, objective_title="O", objective_weight=1.0,
        kr_title="KR", kr_weight=1.0, item_json='{"adopted":"business"}',
    )
    with sqlite3.connect(store.path) as db:
        assert db.execute(
            "select envelope_json, audit_summary, audit_tool_events_json "
            "from okr_review_runs where id=?", (run,)
        ).fetchone() == ("{}", "", "[]")
        assert db.execute("select item_json from okr_review_items").fetchone()[0] == '{"adopted":"business"}'
    loaded = store.get_okr_review_run(run)
    assert loaded["native_available"] is True
    assert json.loads(loaded["envelope_json"])["kind"] == "okr_review"
    assert loaded["audit_summary"] == "native audit"
