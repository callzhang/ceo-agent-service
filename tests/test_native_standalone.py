import json
import sqlite3

import pytest

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


def _attempt(db, run_id, workload_key, session, start, end, number, *, kind="task", status="completed"):
    db.execute(
        "insert into agent_runtime_attempts "
        "(workload_kind, workload_key, attempt_number, route_name, runtime_kind, "
        "credential_mode, model, session_id, status, transcript_start, transcript_end, "
        "lease_owner, lease_expires_at) "
        "values (?, ?, ?, 'test', 'codex_cli', 'local_oauth', 'test', ?, "
        "?, ?, ?, 'test', '2099-01-01 00:00:00')",
        (kind, workload_key, number, session, status, start, end),
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
        _attempt(db, run, str(run), "native-meeting", 1, 3, 1, kind="meeting")
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
    from app.native_standalone import NativeStandaloneRef, meeting_decision

    native = tmp_path / "meeting.jsonl"
    native.write_text("\n".join((
        json.dumps({"type": "event_msg", "payload": {"type": "task_started"}}),
        _message(summary_decision().model_dump_json()),
        json.dumps({"type": "event_msg", "payload": {"type": "task_started"}}),
        _message(summary_decision().model_dump_json()),
    )) + "\n")
    monkeypatch.setattr(native_trajectory, "find_codex_session_path", lambda *args, **kwargs: native)
    decision = meeting_decision(NativeStandaloneRef("codex_cli", "native-meeting", 0, 4))
    assert not decision.available
    assert decision.reason == "native_source_unavailable"


def test_meeting_legacy_complete_range_is_read_without_attempt(tmp_path, monkeypatch):
    store = AutoReplyStore(tmp_path / "store.sqlite3")
    native = tmp_path / "meeting.jsonl"
    native.write_text(_message(summary_decision().model_dump_json()) + "\n")
    monkeypatch.setattr(native_trajectory, "find_codex_session_path", lambda *args, **kwargs: native)
    job = store.upsert_meeting_alignment_job(
        meeting_id="legacy", title="Legacy", source_json="{}",
        participants_json="[]", ended_at="2026-10-09", eligible_at="2026-10-09",
        status="no_action",
    )
    run = store.record_meeting_alignment_run(
        job_id=job, codex_session_id="native-meeting",
        decision_json="{}", audit_summary="", status="no_action", error="",
        codex_transcript_start_line=0, codex_transcript_end_line=1,
    )
    assert store.get_meeting_alignment_run(run).native_available
    store.upsert_codex_session_search_index(
        session_id="native-meeting", source_type="meeting_alignment",
        source_id=str(run), title="Legacy Planning",
    )
    with store._connect() as db:
        _attempt(db, run, str(run), "native-meeting", 0, 1, 1,
                 kind="meeting", status="failed")
        _attempt(db, run, str(run), "native-meeting", 0, 1, 2,
                 kind="meeting", status="running")
    unavailable = store.get_meeting_alignment_run(run)
    assert not unavailable.native_available
    assert unavailable.native_reason == "native_reference_unavailable"
    [search] = store.search_codex_sessions(fts_query="Planning")
    assert search.summary_text == ""
    assert search.native_reason == "native_reference_unavailable"


def test_friday_original_artifact_read_uses_existing_adapter_and_reports_unavailable(tmp_path, monkeypatch):
    from app import friday_runtime_adapter
    from app.native_standalone import NativeStandaloneRef, meeting_decision

    calls = []

    class FakeAdapter:
        def __init__(self, config):
            calls.append(config)

        def read_final_artifact(self, thread_id):
            assert thread_id == "native-thread"
            return summary_decision().model_dump_json()

    monkeypatch.setattr(friday_runtime_adapter, "FridayRuntimeAdapter", FakeAdapter)
    ref = NativeStandaloneRef("friday_runtime", "friday_thread:native-thread", 0, 0)
    decision = meeting_decision(ref)
    assert decision.available
    assert decision.tool_events_available is False
    assert decision.tool_events_reason == "native_tool_stream_unavailable"
    assert decision.audit_tool_events_json == "[]"
    assert len(calls) == 1

    store = AutoReplyStore(tmp_path / "store.sqlite3")
    job = store.upsert_meeting_alignment_job(
        meeting_id="friday", title="Friday", source_json="{}",
        participants_json="[]", ended_at="2026-10-09", eligible_at="2026-10-09",
        status="no_action",
    )
    run = store.record_meeting_alignment_run(
        job_id=job, codex_session_id="friday_thread:native-thread",
        decision_json="{}", audit_summary="", status="no_action", error="",
        codex_transcript_start_line=0, codex_transcript_end_line=0,
    )
    with store._connect() as db:
        db.execute(
            "insert into agent_runtime_attempts "
            "(workload_kind, workload_key, attempt_number, route_name, runtime_kind, "
            "credential_mode, model, session_id, status) "
            "values ('meeting', ?, 1, 'test', 'friday_runtime', 'local_oauth', "
            "'test', 'friday_thread:native-thread', 'completed')",
            (str(run),),
        )
    loaded = store.get_meeting_alignment_run(run)
    assert loaded.native_available
    assert loaded.tool_events_available is False
    assert loaded.tool_events_reason == "native_tool_stream_unavailable"

    def unavailable(self, thread_id):
        raise RuntimeError("Artifact unavailable")

    monkeypatch.setattr(FakeAdapter, "read_final_artifact", unavailable)
    result = meeting_decision(ref)
    assert result.available is False
    assert result.reason == "native_source_unavailable"


def test_claude_meeting_reads_exact_decision_and_matching_native_input(tmp_path, monkeypatch):
    from app.meeting_alignment_models import MeetingSource
    from app.native_standalone import NativeStandaloneRef, meeting_decision, meeting_source

    source = MeetingSource.model_validate({
        "meeting_id": "matching", "title": "Planning", "status": "ended",
        "started_at": "2026-10-09T09:00:00+08:00",
        "ended_at": "2026-10-09T10:00:00+08:00",
        "participants": [], "attendee_evidence": "calendar",
        "attendee_roster_complete": True, "current_user_id": "derek",
        "summary": "Native input", "transcript": [],
    })
    native = tmp_path / "claude.jsonl"
    native.write_text("\n".join((
        json.dumps({"type": "user", "message": {"content": [{
            "type": "text", "text": source.model_dump_json(),
        }]}}),
        json.dumps({"type": "assistant", "message": {"content": [{
            "type": "tool_use", "id": "native-call", "name": "mcp__source__read",
            "input": {"query": "native input"},
        }]}}),
        json.dumps({"type": "user", "message": {"content": [{
            "type": "tool_result", "tool_use_id": "native-call",
            "content": [{"type": "text", "text": "native output"}],
        }]}}),
        json.dumps({"type": "assistant", "message": {"content": [{
            "type": "text", "text": summary_decision().model_dump_json(),
        }]}}),
    )) + "\n")
    monkeypatch.setattr(native_trajectory, "claude_session_path", lambda *args, **kwargs: native)
    ref = NativeStandaloneRef("claude_cli", "native-claude", 0, 4)
    decision = meeting_decision(ref)
    assert decision.available
    assert decision.tool_events_available
    assert decision.tool_events_reason == ""
    [tool] = json.loads(decision.audit_tool_events_json)
    assert tool["tool"] == "read"
    assert tool["call_id"] == "native-call"
    assert "native input" in tool["input"]
    assert "native output" in tool["output"]
    assert meeting_source(ref, meeting_id="matching") == source
    assert meeting_source(ref, meeting_id="different") is None


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


def test_task_evaluation_reads_original_native_decision_without_sql_copy(tmp_path, monkeypatch):
    from tests.test_task_project_centered_eval import _tool

    store = AutoReplyStore(tmp_path / "evaluation.sqlite3")
    tool = _tool()
    input_id = store.enqueue_work_summary_input("reply_attempt", "native-evaluation", "{}")
    decision = {
        "project_decisions": [], "task_decisions": [],
        "project_assessments": [], "update_summary": "Native evaluation decision",
    }
    native = tmp_path / "evaluation-native.jsonl"
    native.write_text(_message(json.dumps(decision)) + "\n")
    monkeypatch.setattr(native_trajectory, "find_codex_session_path", lambda *a, **k: native)
    run = store.record_task_agent_run(summary_input_id=input_id, decision_json=json.dumps(decision))
    with store._connect() as db:
        _attempt(db, run, str(run), "native-evaluation", 0, 1, 1)
        assert db.execute("select decision_json from task_agent_runs where id=?", (run,)).fetchone()[0] == "{}"
    result = tool.readback(store, input_id=input_id, before=tool.read_domain(store))
    assert result["project_decisions"] == []
    assert result["project_assessments"] == []
    decision.pop("project_assessments")
    native.write_text(_message(json.dumps(decision)) + "\n")
    missing_field = tool.readback(store, input_id=input_id, before=tool.read_domain(store))
    assert missing_field["project_assessments"] is None
    native.write_text("")
    unavailable = tool.readback(store, input_id=input_id, before=tool.read_domain(store))
    assert unavailable["passed"] is False
    assert "task_agent_native_decision_unavailable" in unavailable["failures"]


@pytest.mark.parametrize("invalid_field", [
    {"task_decisions": "invalid"},
    {"project_assessments": None},
])
def test_raw_task_decision_uses_runtime_valid_candidate_selection(monkeypatch, invalid_field):
    from app import native_standalone

    valid = {
        "task_decisions": [], "project_decisions": [],
        "project_assessments": [], "update_summary": "Accepted",
    }
    invalid = {**valid, **invalid_field}
    stream = _message(json.dumps(valid)) + "\n" + _message(json.dumps(invalid))
    monkeypatch.setattr(native_standalone, "_native_stream", lambda ref: stream)
    ref = native_standalone.NativeStandaloneRef("codex_cli", "synthetic", 0, 2)
    assert native_standalone.task_decision_value(ref) == valid
    assert "todo_changes" not in native_standalone.task_decision_value(ref)


def test_raw_task_decision_rejects_all_invalid_candidates(monkeypatch):
    from app import native_standalone

    stream = _message(json.dumps({"task_decisions": "invalid", "update_summary": "Invalid"}))
    monkeypatch.setattr(native_standalone, "_native_stream", lambda ref: stream)
    ref = native_standalone.NativeStandaloneRef("codex_cli", "synthetic", 0, 1)
    assert native_standalone.task_decision_value(ref) is None


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
        _attempt(db, run, "1", "native-okr", 0, 1, 1, kind="structured")
        assert db.execute(
            "select envelope_json, audit_summary, audit_tool_events_json "
            "from okr_review_runs where id=?", (run,)
        ).fetchone() == ("{}", "", "[]")
        assert db.execute("select item_json from okr_review_items").fetchone()[0] == '{"adopted":"business"}'
    loaded = store.get_okr_review_run(run)
    assert loaded["native_available"] is True
    assert loaded["tool_events_available"] is True
    assert json.loads(loaded["envelope_json"])["kind"] == "okr_review"
    assert loaded["audit_summary"] == "native audit"
    with store._connect() as db:
        db.execute(
            "update agent_runtime_attempts set status='failed' "
            "where workload_kind='structured' and workload_key='1'"
        )
    unavailable = store.get_okr_review_run(run)
    assert unavailable["native_available"] is False
    assert unavailable["native_reason"] == "native_reference_unavailable"
    assert unavailable["tool_events_available"] is False


def test_okr_runs_for_same_request_read_only_their_own_completed_attempt(tmp_path, monkeypatch):
    store = AutoReplyStore(tmp_path / "store.sqlite3")
    paths = {}
    for session, summary in (("first-session", "first review"),
                             ("second-session", "second review")):
        envelope = AgentEnvelope.model_validate({
            "kind": "okr_review",
            "user_response": {
                "mode": "no_reply", "text": "", "sensitivity_kind": "general",
            },
            "system_actions": [], "domain_payload": {},
            "audit": {"summary": summary, "documents": [], "confidence": 1.0},
        })
        path = tmp_path / f"{session}.jsonl"
        path.write_text(_message(envelope.model_dump_json()) + "\n")
        paths[session] = path
    monkeypatch.setattr(
        native_trajectory, "find_codex_session_path",
        lambda session, **kwargs: paths[session],
    )
    first = store.record_okr_review_run(
        request_id=1, codex_session_id="first-session",
        codex_transcript_start_line=0, codex_transcript_end_line=1,
        envelope_json="{}", audit_tool_events_json="[]", audit_summary="",
    )
    second = store.record_okr_review_run(
        request_id=1, codex_session_id="second-session",
        codex_transcript_start_line=0, codex_transcript_end_line=1,
        envelope_json="{}", audit_tool_events_json="[]", audit_summary="",
    )
    with store._connect() as db:
        _attempt(db, first, "1", "first-session", 0, 1, 1, kind="structured")
        _attempt(db, second, "1", "second-session", 0, 1, 2, kind="structured")
    assert store.get_okr_review_run(first)["audit_summary"] == "first review"
    assert store.get_okr_review_run(second)["audit_summary"] == "second review"

    paths["first-session"] = None
    missing = store.get_okr_review_run(first)
    assert missing["native_available"] is False
    assert missing["native_reason"] == "native_source_unavailable"
    assert missing["envelope_json"] == "{}"
    assert store.get_okr_review_run(second)["audit_summary"] == "second review"


def test_okr_run_uses_completed_friday_provider_reference(tmp_path, monkeypatch):
    from app import friday_runtime_adapter

    envelope = AgentEnvelope.model_validate({
        "kind": "okr_review",
        "user_response": {"mode": "no_reply", "text": "", "sensitivity_kind": "general"},
        "system_actions": [], "domain_payload": {},
        "audit": {"summary": "Friday review", "documents": [], "confidence": 1.0},
    })

    class FakeAdapter:
        def __init__(self, config):
            pass

        def read_final_artifact(self, thread_id):
            assert thread_id == "okr-thread"
            return envelope.model_dump_json()

    monkeypatch.setattr(friday_runtime_adapter, "FridayRuntimeAdapter", FakeAdapter)
    store = AutoReplyStore(tmp_path / "store.sqlite3")
    run = store.record_okr_review_run(
        request_id=1, codex_session_id="friday_thread:okr-thread",
        codex_transcript_start_line=0, codex_transcript_end_line=0,
        envelope_json="{}", audit_tool_events_json="[]", audit_summary="",
    )
    with store._connect() as db:
        db.execute(
            "insert into agent_runtime_attempts "
            "(workload_kind, workload_key, attempt_number, route_name, runtime_kind, "
            "credential_mode, model, session_id, status) "
            "values ('structured', '1', 1, 'test', 'friday_runtime', 'local_oauth', "
            "'test', 'friday_thread:okr-thread', 'completed')"
        )
    loaded = store.get_okr_review_run(run)
    assert loaded["native_available"] is True
    assert loaded["audit_summary"] == "Friday review"
    assert loaded["audit_tool_events_json"] == "[]"
    assert loaded["tool_events_available"] is False
    assert loaded["tool_events_reason"] == "native_tool_stream_unavailable"
