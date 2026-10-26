import json

import pytest

from app.prompt_preview import historical_prompt_preview
from app.store import AutoReplyStore
from tests.test_prompt_preview import task_and_run


SID = "00000000-0000-7000-8000-000000000001"


def message(role, text, kind):
    return {"type": "response_item", "payload": {"type": "message", "role": role,
        "content": [{"type": "input_text", "text": text}],
        "internal_chat_message_metadata_passthrough": {"content_item_kinds": [kind]}}}


def native_fixture(tmp_path, monkeypatch, *, resume=False, kind="codex_cli"):
    store = AutoReplyStore(tmp_path / "preview.sqlite3")
    task, run = task_and_run(store)
    attempt = store.claim_agent_runtime_attempt(run.id, "saved-route", kind, "local_oauth", "saved-model")
    first = [{"type": "event_msg", "payload": {"type": "task_started"}},
        message("developer", "Original developer", "generic.developer_instructions"),
        message("user", "Native AGENTS excluded", "agents_md.instructions"),
        message("user", "Original Task 中文", "user.text"),
        {"type": "event_msg", "payload": {"type": "task_complete"}}]
    records = first + ([{"type": "event_msg", "payload": {"type": "task_started"}},
        message("user", "Resumed Task", "user.text"),
        {"type": "event_msg", "payload": {"type": "task_complete"}}] if resume else [])
    path = tmp_path / "native.jsonl"
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records)+"\n")
    monkeypatch.setattr("app.native_trajectory.find_codex_session_path", lambda *a, **k: path)
    monkeypatch.setattr("app.native_trajectory.claude_session_path", lambda *a, **k: path)
    with store._connect() as db:
        db.execute("update runtime_attempts set session_id=?,status='completed',transcript_start=?,transcript_end=? where id=?",
                   (SID, len(first) if resume else 0, len(records), attempt.id))
    return store, task, run, attempt, path


@pytest.mark.parametrize("resume", [False, True])
def test_historical_input_reads_exact_native_attempt_without_current_templates(tmp_path, monkeypatch, resume):
    store, task, run, attempt, path = native_fixture(tmp_path, monkeypatch, resume=resume)
    monkeypatch.setattr("app.prompt_preview.load_prompt_configuration", lambda **k: pytest.fail("history read current config"))
    result = historical_prompt_preview(store, run_id=run.id)
    assert result["status"] == "available"
    assert result["developer_instructions"] == "Original developer"
    assert result["task_prompt"] == ("Resumed Task" if resume else "Original Task 中文")
    assert "Native AGENTS" not in result["submitted_input"]
    assert result["runtime_attempt_id"] == attempt.id
    assert result["attempts"][0]["submission_state"] == "invoked"
    assert result["route_name"] == "saved-route" and result["model"] == "saved-model"
    assert result["configuration_fingerprints"]["developer_template"] is None
    if resume:
        assert "session" in result["reason"]
    assert store.get_reply_task(task.id).status == task.status


def test_cross_turn_reference_does_not_present_combined_input_as_one_attempt(tmp_path, monkeypatch):
    store, task, run, attempt, path = native_fixture(tmp_path, monkeypatch, resume=True)
    with store._connect() as db:
        db.execute("update runtime_attempts set transcript_start=0 where id=?", (attempt.id,))
    assert historical_prompt_preview(store, run_id=run.id)["status"] == "unavailable"


def test_missing_native_input_is_not_rebuilt_from_trigger_or_current_config(tmp_path, monkeypatch):
    store, task, run, attempt, path = native_fixture(tmp_path, monkeypatch)
    path.unlink()
    result = historical_prompt_preview(store, run_id=run.id)
    assert result["status"] == "unavailable"
    assert result["task_prompt"] == "" and result["developer_instructions"] == ""


def test_native_tool_result_text_cannot_become_task_input(tmp_path, monkeypatch):
    store, task, run, attempt, path = native_fixture(tmp_path, monkeypatch)
    records = [json.loads(line) for line in path.read_text().splitlines()]
    records[3] = {"type": "response_item", "payload": {"type": "function_call_output", "output": "not a Task"}}
    path.write_text("\n".join(json.dumps(r) for r in records)+"\n")
    assert historical_prompt_preview(store, run_id=run.id)["status"] == "unavailable"


def test_unknown_attempt_is_not_silently_replaced_by_latest(tmp_path, monkeypatch):
    store, task, run, attempt, path = native_fixture(tmp_path, monkeypatch)
    with pytest.raises(LookupError):
        historical_prompt_preview(store, run_id=run.id, runtime_attempt_id=attempt.id+100)


@pytest.mark.parametrize("as_string", [False, True])
def test_claude_history_reads_original_combined_service_message(tmp_path, monkeypatch, as_string):
    from app.claude_runtime_adapter import claude_input_contract
    store, task, run, attempt, path = native_fixture(tmp_path, monkeypatch, kind="claude_cli")
    text = claude_input_contract(prompt="Saved Claude Task", developer_instructions="Saved Claude Developer")
    records = [{"type": "user", "sessionId": SID,
                "message": {"role": "user", "content": text if as_string else [{"type": "text", "text": text}]}}]
    path.write_text(json.dumps(records[0])+"\n")
    with store._connect() as db:
        db.execute("update runtime_attempts set transcript_end=1 where id=?", (attempt.id,))
    result = historical_prompt_preview(store, run_id=run.id)
    assert result["status"] == "available"
    assert result["submitted_input"] == text
    assert result["developer_instructions"] == "Saved Claude Developer"
    assert result["task_prompt"] == "Saved Claude Task"


def test_bound_current_preview_keeps_actual_native_task_and_current_developer(tmp_path, monkeypatch):
    from app.prompt_preview import current_prompt_preview
    from app.agent_runtime_config import load_runtime_config
    store, task, run, attempt, path = native_fixture(tmp_path, monkeypatch, resume=True)
    monkeypatch.setenv("CEO_WORK_PROFILE_PATH", str(tmp_path / "missing-profile"))
    result = current_prompt_preview(store, role="consumer", config=load_runtime_config({}), task_id=task.id)
    assert result["status"] == "available"
    assert result["task_prompt"] == "Resumed Task"
    assert "Original developer" not in result["developer_instructions"]
    assert result["task_source_run_id"] == run.id
    assert result["task_source_configuration_fingerprints"]["developer_template"] is None


@pytest.mark.parametrize("selected", [False, True])
def test_missing_latest_attempt_does_not_show_earlier_available_input(tmp_path, monkeypatch, selected):
    store, task, run, first, path = native_fixture(tmp_path, monkeypatch)
    later = store.claim_agent_runtime_attempt(run.id, "later-route", "codex_cli", "local_oauth", "later-model")
    other_sid = "00000000-0000-7000-8000-000000000002"
    with store._connect() as db:
        db.execute("update runtime_attempts set session_id=?,status='completed',transcript_end=5 where id=?", (other_sid, later.id))
    monkeypatch.setattr("app.native_trajectory.find_codex_session_path", lambda sid, **k: path if sid == SID else None)
    result = historical_prompt_preview(store, run_id=run.id, **({"runtime_attempt_id":later.id} if selected else {}))
    assert result["status"] == "unavailable"
    assert result["runtime_attempt_id"] == later.id
    assert result["route_name"] == "later-route" and result["model"] == "later-model"
    assert result["submission_state"] is None
    assert result["task_prompt"] == ""
    assert [x["runtime_attempt_id"] for x in result["attempts"]] == [first.id, later.id]
    assert result["attempts"][-1]["input_status"] == "unavailable"


def test_bound_current_missing_latest_attempt_does_not_use_earlier_task(tmp_path, monkeypatch):
    from app.prompt_preview import current_prompt_preview
    from app.agent_runtime_config import load_runtime_config
    store, task, run, first, path = native_fixture(tmp_path, monkeypatch)
    later = store.claim_agent_runtime_attempt(run.id, "later-route", "codex_cli", "local_oauth", "later-model")
    other_sid = "00000000-0000-7000-8000-000000000002"
    with store._connect() as db:
        db.execute("update runtime_attempts set session_id=?,status='completed',transcript_end=5 where id=?", (other_sid, later.id))
    monkeypatch.setattr("app.native_trajectory.find_codex_session_path", lambda sid, **k: path if sid == SID else None)
    result = current_prompt_preview(store, role="consumer", config=load_runtime_config({}), task_id=task.id)
    assert result["status"] == "unavailable"
    assert result["task_prompt"] == ""
    assert "Original Task" not in result["submitted_input"]
