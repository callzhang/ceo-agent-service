import json

import pytest

from app.minutes_recovery_agent import (
    build_recovery_prompt,
    completed_asr_receipt,
    read_completed_asr,
)


def event(
    result,
    *,
    job_id="job-1",
    server="ai_services",
    tool="get_audio_transcription",
    status="completed",
):
    return {
        "type": "item.completed",
        "item": {
            "type": "mcp_tool_call",
            "server": server,
            "tool": tool,
            "status": status,
            "arguments": {"job_id": job_id},
            "result": result,
        },
    }


ASR = {
    "status": "done",
    "text": "Discussion.",
    "segments": [{"text": "Discussion.", "start": 0, "end": 1}],
}


@pytest.mark.parametrize(
    "result",
    [
        {"structuredContent": ASR, "isError": False},
        {"content": [{"type": "text", "text": json.dumps(ASR)}], "isError": False},
        {"Ok": {"structuredContent": ASR, "isError": False}},
        json.dumps(ASR),
    ],
)
def test_completed_native_receipt_returns_asr(result, monkeypatch):
    monkeypatch.setattr(
        "app.minutes_recovery_agent._native_events", lambda *_: [event(result)]
    )
    assert read_completed_asr(None, "turn-1", "job-1") == ASR


@pytest.mark.parametrize(
    "events",
    [
        [],
        [event({"structuredContent": ASR}, job_id="other")],
        [event({"structuredContent": {"status": "running"}})],
        [event({"structuredContent": ASR}, server="other")],
        [event({"structuredContent": ASR}, status="failed")],
        [
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": json.dumps(ASR)},
            }
        ],
    ],
)
def test_absent_or_unrelated_native_receipt_is_not_asr(events, monkeypatch):
    monkeypatch.setattr("app.minutes_recovery_agent._native_events", lambda *_: events)
    assert read_completed_asr(None, "turn-1", "job-1") is None


def test_completed_receipt_matches_recorded_source_job(monkeypatch):
    monkeypatch.setattr(
        "app.minutes_recovery_agent._native_events",
        lambda *_: [event({"structuredContent": ASR})],
    )
    assert completed_asr_receipt(None, "turn-1", ["job-1"]) == {
        "job_id": "job-1",
        "result": ASR,
    }


def test_completed_receipt_selects_latest_completed_source_upload(monkeypatch):
    monkeypatch.setattr(
        "app.minutes_recovery_agent._native_events",
        lambda *_: [
            event({"structuredContent": ASR}),
            event({"structuredContent": ASR}, job_id="job-2"),
        ],
    )
    assert completed_asr_receipt(None, "turn-1", ["job-1", "job-2"]) == {
        "job_id": "job-2",
        "result": ASR,
    }
    assert completed_asr_receipt(None, "turn-1", ["unrelated"]) is None


def test_completed_receipt_ignores_running_and_failed_before_success(monkeypatch):
    monkeypatch.setattr(
        "app.minutes_recovery_agent._native_events",
        lambda *_: [
            event({"structuredContent": {"status": "running"}}),
            {**event({"structuredContent": ASR}), "type": "item.failed"},
            event({"structuredContent": ASR}),
        ],
    )
    assert completed_asr_receipt(None, "turn-1", ["job-1"]) == {
        "job_id": "job-1",
        "result": ASR,
    }


def test_completed_receipt_unknown_returns_none(monkeypatch):
    monkeypatch.setattr(
        "app.minutes_recovery_agent._native_events",
        lambda *_: [event({"structuredContent": {"status": "running"}})],
    )
    assert completed_asr_receipt(None, "turn-1", ["job-1"]) is None


def test_prompt_uses_original_job_and_prohibits_forged_asr(tmp_path):
    prompt = build_recovery_prompt(tmp_path / "work dir", tmp_path / "service root")
    assert "metadata.json" in prompt
    assert "minutes-asr-recovery.md" in prompt
    assert "summary" in prompt.lower()
    assert "create_audio_upload" not in prompt
    assert "work dir" in prompt and "service root" in prompt


def test_completed_claude_receipt_is_hydrated_from_native_turn(tmp_path, monkeypatch):
    from app import native_trajectory
    from app.workbench.store import WorkbenchStore
    from tests.support.native_run_rows import insert_runtime_attempt_row

    store = WorkbenchStore(tmp_path / "workbench.sqlite3")
    task = store.create_task(title="ASR", runtime_kind="codex")
    turn = store.create_turn(task.id, user_text="Recover", client_request_id="request")
    assert store.claim_next_turn(owner="worker") is not None
    records = [
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "call-1",
                        "name": "mcp__ai_services__get_audio_transcription",
                        "input": {"job_id": "job-1"},
                    }
                ]
            },
        },
        {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "call-1",
                        "content": [{"type": "text", "text": json.dumps(ASR)}],
                    }
                ]
            },
        },
    ]
    native_path = tmp_path / "claude.jsonl"
    native_path.write_text("\n".join(map(json.dumps, records)) + "\n")
    with store._connect() as db:
        insert_runtime_attempt_row(
            db,
            {
                "workload_kind": "workbench",
                "workload_key": turn.id,
                "attempt_number": 1,
                "route_name": "first",
                "runtime_kind": "codex_cli",
                "credential_mode": "oauth",
                "model": "model",
                "status": "superseded",
            },
        )
        insert_runtime_attempt_row(
            db,
            {
                "workload_kind": "workbench",
                "workload_key": turn.id,
                "attempt_number": 2,
                "route_name": "primary",
                "runtime_kind": "claude_cli",
                "credential_mode": "oauth",
                "model": "model",
                "status": "completed",
                "session_id": "session-id",
                "transcript_reference": "claude_session:session-id",
                "transcript_start": 0,
                "transcript_end": 2,
            },
        )
    monkeypatch.setattr(
        native_trajectory, "claude_session_path", lambda *_: native_path
    )
    store.complete_turn(turn.id, status="completed", owner="worker")
    assert read_completed_asr(WorkbenchStore(store.path), turn.id, "job-1") == ASR
    assert completed_asr_receipt(WorkbenchStore(store.path), turn.id, ["job-1"]) == {
        "job_id": "job-1",
        "result": ASR,
    }
