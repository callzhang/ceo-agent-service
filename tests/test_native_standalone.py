from app.native_standalone import native_run_ref, task_decision, meeting_decision, okr_envelope, _meeting_tool_events
from tests.support.session_search import seed_session_search_metadata
import json
import pytest

from app.run_entity_storage import create_runtime_attempt
from app.store import MEETING_LOGICAL_RUN_SQL,TASK_LOGICAL_RUN_SQL
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
    create_runtime_attempt(
        db, values={
            'parent_id': run_id,
            'workload_kind': kind,
            'workload_key': workload_key,
            'attempt_number': number,
            'route_name': 'test',
            'runtime_kind': 'codex_cli',
            'credential_mode': 'local_oauth',
            'model': 'test',
            'session_mode': 'fresh', 'source_session_id': '',
            'attempt_purpose': 'normal', 'validation_retry_policy_id': '',
            'validation_result_schema_id': '', 'transcript_reference': '',
            'session_id': session,
            'status': status,
            'transcript_start': start,
            'transcript_end': end,
            'lease_owner': 'test',
            'lease_expires_at': '2099-01-01 00:00:00',
            'failure_class': '', 'failure_code': '', 'failover_permitted': 0,
            'first_effect_started_at': '', 'result_schema_id': '',
            'started_at': '', 'finished_at': '', 'created_at': '', 'updated_at': '',
        },
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
    run = store.create_professional_run(job, session_id="native-meeting", status="no_action", transcript_start=1, transcript_end=3)
    with store._connect() as db:
        _attempt(db, run, str(run), "native-meeting", 1, 3, 1, kind="meeting")
        row = db.execute(
            f"select * from ({MEETING_LOGICAL_RUN_SQL}) where id=?", (run,)
        ).fetchone()
    assert not {"decision_json", "audit_summary", "audit_tool_events_json"} & set(row.keys())
    with store._connect() as db:
        loaded = meeting_decision(native_run_ref(db, run, "meeting_alignment_runs"))
    assert loaded.available
    assert json.loads(loaded.decision_json)["final_message"] == "current turn"
    assert loaded.audit_summary == current.audit_summary
    assert json.loads(loaded.audit_tool_events_json)[0]["command"] == "native evidence"

    native.write_text(_message(old.model_dump_json()) + "\n")
    with store._connect() as db:
        missing = meeting_decision(native_run_ref(db, run, "meeting_alignment_runs"))
    assert not missing.available
    assert missing.reason == "native_source_unavailable"
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


def test_meeting_current_run_range_is_read_without_attempt(tmp_path, monkeypatch):
    store = AutoReplyStore(tmp_path / "store.sqlite3")
    native = tmp_path / "meeting.jsonl"
    native.write_text(_message(summary_decision().model_dump_json()) + "\n")
    monkeypatch.setattr(native_trajectory, "find_codex_session_path", lambda *args, **kwargs: native)
    job = store.upsert_meeting_alignment_job(
        meeting_id="legacy", title="Legacy", source_json="{}",
        participants_json="[]", ended_at="2026-10-09", eligible_at="2026-10-09",
        status="no_action",
    )
    run = store.create_professional_run(job, session_id="native-meeting", status="no_action", transcript_start=0, transcript_end=1)
    with store._connect() as db:
        assert meeting_decision(native_run_ref(db, run, "meeting_alignment_runs")).available
    seed_session_search_metadata(store,
        session_id="native-meeting", source_type="meeting_alignment",
        source_id=str(run), title="Legacy Planning",
    )
    with store._connect() as db:
        _attempt(db, run, str(run), "native-meeting", 0, 1, 1,
                 kind="meeting", status="failed")
        _attempt(db, run, str(run), "native-meeting", 0, 1, 2,
                 kind="meeting", status="running")
    with store._connect() as db:
        assert native_run_ref(db, run, "meeting_alignment_runs") is None
    [search] = store.search_codex_sessions(fts_query="Planning")
    assert search.summary_text == ""
    assert search.native_reason == "native_reference_unavailable"


@pytest.mark.parametrize("meeting_native_only", [False, True])
def test_meeting_search_and_detail_share_execution_parent_not_audit_or_other_run(
    tmp_path, monkeypatch, meeting_native_only,
):
    from app.meeting_alignment_models import MeetingSource
    from app.native_standalone import native_run_ref

    store = AutoReplyStore(tmp_path / "native-owner.sqlite3")
    source = MeetingSource.model_validate({
        "meeting_id": "native-owner", "title": "Native planning", "status": "ended",
        "started_at": "2026-10-26T07:00:00+00:00", "ended_at": "2026-10-26T08:00:00+00:00",
        "participants": [{"name": "Alex", "user_id": "alex"}],
        "attendee_evidence": "calendar", "attendee_roster_complete": True,
        "current_user_id": "alex", "summary": "Native source", "transcript": [],
    })
    decision = summary_decision().model_copy(update={"final_message": "own execution result"})
    native = tmp_path / "execution.jsonl"
    native.write_text(json.dumps({"type": "response_item", "payload": {
        "type": "message", "role": "user", "content": [{
            "type": "input_text", "text": "完整会议来源 JSON：\n" + source.model_dump_json(),
        }],
    }}) + "\n" + _message(decision.model_dump_json()) + "\n")
    audit = tmp_path / "audit.jsonl"
    audit.write_text(_message('{"outcome":"approve"}') + "\n")
    paths = {"execution": native, "audit": audit}
    monkeypatch.setattr(native_trajectory, "find_codex_session_path", lambda session, **_kw: paths.get(session))
    owner = store.upsert_meeting_alignment_job(
        meeting_id=source.meeting_id, title=source.title, source_json="{}", participants_json="[]",
        ended_at=source.ended_at, eligible_at=source.ended_at, status="no_action",
    )
    other = store.upsert_meeting_alignment_job(
        meeting_id="other-owner", title="Other", source_json="{}", participants_json="[]",
        ended_at=source.ended_at, eligible_at=source.ended_at, status="no_action",
    )
    run_id = store.create_professional_run(owner, status="no_action", session_id="execution",
                                           transcript_start=0, transcript_end=2)
    other_run = store.create_professional_run(other, status="no_action")
    with store._connect() as db:
        _attempt(db, run_id, str(run_id), "execution", 0, 2, 1, kind="meeting")
        _attempt(db, other_run, str(run_id), "unrelated", 0, 2, 2, kind="meeting")
        _attempt(db, run_id, str(run_id), "audit", 0, 1, 1, kind="meeting_audit")
        ref = native_run_ref(db, run_id, "meeting_alignment_runs")
    assert (ref.session_id, ref.start, ref.end) == ("execution", 0, 2)
    seed_session_search_metadata(store,
        session_id="execution", source_type="meeting_alignment", source_id=str(run_id), title=source.title,
    )
    [found] = store.search_codex_sessions(fts_query="Native", meeting_native_only=meeting_native_only)
    assert found.source_id == str(run_id)
    assert "own execution result" in found.summary_text
    assert found.native_reason == ""


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
    run = store.create_professional_run(job, session_id="friday_thread:native-thread", status="no_action", transcript_start=0, transcript_end=0)
    with store._connect() as db:
        create_runtime_attempt(
            db, values={
                'parent_id': run,
                'workload_kind': 'meeting',
                'workload_key': str(run),
                'attempt_number': 1,
                'route_name': 'test',
                'runtime_kind': 'friday_runtime',
                'credential_mode': 'local_oauth',
                'model': 'test',
                'session_mode': 'fresh', 'source_session_id': '',
                'attempt_purpose': 'normal', 'validation_retry_policy_id': '',
                'validation_result_schema_id': '', 'transcript_reference': '',
                'session_id': 'friday_thread:native-thread',
                'status': 'completed',
                'transcript_start': 0, 'transcript_end': 0,
                'failure_class': '', 'failure_code': '', 'failover_permitted': 0,
                'lease_owner': '', 'lease_expires_at': '', 'first_effect_started_at': '',
                'result_schema_id': '', 'started_at': '', 'finished_at': '',
                'created_at': '', 'updated_at': '',
            },
        )
    with store._connect() as db:
        loaded = meeting_decision(native_run_ref(db, run, "meeting_alignment_runs"))
    assert loaded.available
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
    parent = store.enqueue_work_summary_input('local_file', 'native-task', '{}')
    run = store.create_professional_run(parent, session_id="native-task")
    with store._connect() as db:
        _attempt(db, run, str(run), "native-task", 0, 1, 1)
        _attempt(db, run, f"{run}:decision_repair.1", "native-task", 1, 2, 1)
        stored = db.execute(
            f"select * from ({TASK_LOGICAL_RUN_SQL}) where id=?", (run,)
        ).fetchone()
    assert not {"decision_json", "audit_summary", "memory_recall_used"} & set(stored.keys())
    with store._connect() as db:
        loaded = task_decision(native_run_ref(db, run, "task_agent_runs"))
    assert loaded.available is True
    assert json.loads(loaded.decision_json)["update_summary"] == "new decision"

    native.write_text(_message(first.model_dump_json()) + "\n")
    with store._connect() as db:
        missing = task_decision(native_run_ref(db, run, "task_agent_runs"))
    assert missing.available is False
    assert missing.decision_json == "{}"


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
    run = store.create_professional_run(input_id)
    with store._connect() as db:
        _attempt(db, run, str(run), "native-evaluation", 0, 1, 1)
        assert "decision_json" not in db.execute(f"select * from ({TASK_LOGICAL_RUN_SQL}) where id=?", (run,)).fetchone().keys()
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
    from test_okr_review_task_runtime import NOW, _envelope, _request, _store

    store = _store()
    envelope = _envelope([]).model_copy(update={
        "audit": _envelope([]).audit.model_copy(update={"summary": "native audit"}),
    })
    native = tmp_path / "okr.jsonl"
    native.write_text(_message(envelope.model_dump_json()) + "\n")
    monkeypatch.setattr(native_trajectory, "find_codex_session_path", lambda *args, **kwargs: native)
    task_id, _ = _request(store)
    run = store.create_professional_run(task_id, session_id="native-okr", transcript_start=0, transcript_end=1)
    store.record_okr_review_item(
        request_id=task_id, objective_title="O", objective_weight=1.0,
        kr_title="KR", kr_weight=1.0, item_json='{"adopted":"business"}',
    )
    attempt = store.claim_runtime_operation_attempt("structured", str(task_id), "codex_oauth", "codex_cli", "local_oauth", "test", owner=f"okr_review:{task_id}", lease_seconds=1800, now=NOW)
    store.mark_agent_runtime_attempt_running_once(attempt.id, owner=f"okr_review:{task_id}", now=NOW)
    store.complete_agent_runtime_attempt(attempt.id, "native-okr", "token", 0, 1, owner=f"okr_review:{task_id}", now=NOW)
    store.bind_okr_review_runtime_attempt(task_id=task_id, run_id=run, attempt_id=attempt.id)
    with store._connect() as db:
        assert db.execute("select item_json from okr_review_items where request_id=?", (task_id,)).fetchone()[0] == '{"adopted":"business"}'
    with store._connect() as db:
        ref = native_run_ref(db, run, "okr_review_runs")
    envelope_json, reason = okr_envelope(ref)
    assert reason == ""
    assert _meeting_tool_events(ref)[1] is True
    assert json.loads(envelope_json)["kind"] == "okr_review"
    assert json.loads(envelope_json)["audit"]["summary"] == "native audit"
    with store._connect() as db:
        db.execute("update runtime_attempts set status='failed' where id=?", (attempt.id,))
    with store._connect() as db:
        ref = native_run_ref(db, run, "okr_review_runs")
    envelope_json, reason = okr_envelope(ref)
    assert reason == "native_reference_unavailable"
    assert envelope_json == "{}"


def test_okr_runs_for_same_request_read_only_their_own_completed_attempt(tmp_path, monkeypatch):
    from test_okr_review_task_runtime import NOW, _request, _store

    store = _store()
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
    task_id, _ = _request(store)
    first = store.create_professional_run(task_id, session_id="first-session", transcript_start=0, transcript_end=1)
    second = store.create_professional_run(task_id, session_id="second-session", transcript_start=0, transcript_end=1)
    attempts = []
    for session, run in (("first-session", first), ("second-session", second)):
        attempt = store.claim_runtime_operation_attempt("structured", str(task_id), "codex_oauth", "codex_cli", "local_oauth", "test", owner=f"okr_review:{task_id}", lease_seconds=1800, now=NOW)
        store.mark_agent_runtime_attempt_running_once(attempt.id, owner=f"okr_review:{task_id}", now=NOW)
        store.complete_agent_runtime_attempt(attempt.id, session, "token", 0, 1, owner=f"okr_review:{task_id}", now=NOW)
        store.bind_okr_review_runtime_attempt(task_id=task_id, run_id=run, attempt_id=attempt.id)
        attempts.append(attempt.id)
    with store._connect() as db:
        assert json.loads(okr_envelope(native_run_ref(db, first, "okr_review_runs"))[0])["audit"]["summary"] == "first review"
    with store._connect() as db:
        assert json.loads(okr_envelope(native_run_ref(db, second, "okr_review_runs"))[0])["audit"]["summary"] == "second review"

    paths["first-session"] = None
    with store._connect() as db:
        envelope_json, reason = okr_envelope(native_run_ref(db, first, "okr_review_runs"))
    assert reason == "native_source_unavailable"
    assert envelope_json == "{}"
    with store._connect() as db:
        assert json.loads(okr_envelope(native_run_ref(db, second, "okr_review_runs"))[0])["audit"]["summary"] == "second review"


def test_okr_run_uses_completed_friday_provider_reference(tmp_path, monkeypatch):
    from app import friday_runtime_adapter
    from test_okr_review_task_runtime import NOW, _request, _store

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
    store = _store()
    task_id, _ = _request(store)
    run = store.create_professional_run(task_id, session_id="friday_thread:okr-thread", transcript_start=0, transcript_end=0)
    attempt = store.claim_runtime_operation_attempt("structured", str(task_id), "codex_oauth", "friday_runtime", "local_oauth", "test", owner=f"okr_review:{task_id}", lease_seconds=1800, now=NOW)
    store.mark_agent_runtime_attempt_running_once(attempt.id, owner=f"okr_review:{task_id}", now=NOW)
    store.complete_agent_runtime_attempt(attempt.id, "friday_thread:okr-thread", "token", 0, 0, owner=f"okr_review:{task_id}", now=NOW)
    store.bind_okr_review_runtime_attempt(task_id=task_id, run_id=run, attempt_id=attempt.id)
    with store._connect() as db:
        ref = native_run_ref(db, run, "okr_review_runs")
    envelope_json, reason = okr_envelope(ref)
    assert reason == ""
    assert json.loads(envelope_json)["audit"]["summary"] == "Friday review"
    assert _meeting_tool_events(ref) == ("[]", False, "native_tool_stream_unavailable")
