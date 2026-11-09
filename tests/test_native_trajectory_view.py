"""A recorded runtime attempt is the identity of its complete visible trace."""

import json
from types import SimpleNamespace

import pytest

from app.native_trajectory_view import read_attempt_trajectory


def attempt(**updates):
    values = dict(
        id=7,
        parent_id=9,
        runtime_kind="claude_cli",
        session_id="session",
        transcript_start=1,
        transcript_end=5,
        transcript_reference="",
        status="failed",
    )
    return SimpleNamespace(**(values | updates))


def write_records(path, records):
    path.write_text("".join(json.dumps(r) + "\n" for r in records))


def test_claude_trace_reads_exact_range_full_text_tools_and_error_without_thinking(
    tmp_path, monkeypatch
):
    path = tmp_path / "native.jsonl"
    text = "input-" + "x" * 22000
    write_records(
        path,
        [
            {"type": "user", "message": {"content": "outside-before"}},
            {
                "type": "user",
                "timestamp": "2026-11-08T00:00:00Z",
                "message": {"content": text},
            },
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "thinking", "thinking": "private-thought"},
                        {
                            "type": "tool_use",
                            "id": "call",
                            "name": "Bash",
                            "input": {"command": "pytest"},
                        },
                    ]
                },
            },
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "call",
                            "is_error": True,
                            "content": "exact-output",
                        }
                    ]
                },
            },
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "final-output"}]},
            },
            {"type": "user", "message": {"content": "outside-after"}},
        ],
    )
    monkeypatch.setattr(
        "app.native_trajectory_view.claude_session_path", lambda _: path
    )
    first = read_attempt_trajectory(attempt(), after=1, page_size=2)
    assert first["available"] and first["next_cursor"] == 3
    assert first["events"][0]["body"] == text
    second = read_attempt_trajectory(attempt(), after=3, page_size=2)
    assert not second["has_more"]
    result = first["events"] + second["events"]
    assert [e["kind"] for e in result] == [
        "user",
        "tool_call",
        "tool_output",
        "assistant",
    ]
    assert result[2]["trace"]["status"] == "failed"
    assert result[2]["trace"]["output"] == "exact-output"
    assert "private-thought" not in json.dumps(result)
    assert "outside-" not in json.dumps(result)


def test_codex_trace_has_no_500_event_or_20000_character_cutoff(tmp_path, monkeypatch):
    path = tmp_path / "native.jsonl"
    records = [
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "visible-" + str(n)}],
            },
        }
        for n in range(510)
    ]
    records += [
        {
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call",
                "name": "functions.exec",
                "call_id": "raw",
                "input": "raw-js-" + "x" * 22000,
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call_output",
                "call_id": "raw",
                "output": "tail-output",
            },
        },
    ]
    write_records(path, records)
    monkeypatch.setattr(
        "app.native_trajectory_view.find_codex_session_path", lambda _: path
    )
    ref = attempt(
        runtime_kind="codex_cli", transcript_start=0, transcript_end=len(records)
    )
    events = []
    cursor = 0
    while True:
        page = read_attempt_trajectory(ref, after=cursor, page_size=100)
        events.extend(page["events"])
        if not page["has_more"]:
            break
        cursor = page["next_cursor"]
    assert len(events) == 512
    assert events[-2]["trace"]["input"] == records[-2]["payload"]["input"]
    assert events[-1]["trace"]["output"] == "tail-output"


def test_missing_source_and_out_of_range_cursor_are_explicit(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.native_trajectory_view.claude_session_path", lambda _: None
    )
    assert not read_attempt_trajectory(attempt(), after=1)["available"]
    with pytest.raises(ValueError, match="cursor"):
        read_attempt_trajectory(attempt(), after=0)


def test_friday_exact_operation_and_all_events_not_just_final_artifact():
    seen = []

    def read(thread, operation):
        seen.append((thread, operation))
        return {
            "events": [
                {
                    "event_type": "message.created",
                    "payload": {"role": "user", "content": "native-input"},
                    "created_at": "now",
                },
                {
                    "event_type": "run.failed",
                    "payload": {"error": "provider failed"},
                    "created_at": "now",
                },
            ]
        }

    ref = attempt(
        runtime_kind="friday_runtime",
        session_id="friday_thread:thread-1",
        transcript_reference="friday_operation:op-1",
    )
    page = read_attempt_trajectory(ref, friday_reader=read)
    assert page["available"] and not page["has_more"]
    assert seen == [("thread-1", "op-1")]
    assert page["events"][0]["body"] == "native-input"
    assert "provider failed" in page["events"][1]["body"]


def test_independent_invocation_keeps_its_own_sibling_navigation(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.web_api.native_trajectory import register_native_trajectory_routes
    from app.audit_web import _is_spa_page_path

    ref = attempt(
        parent_id=None,
        workload_kind="workbench",
        workload_key="42",
        route_name="claude",
        model="test",
        created_at="",
        finished_at="",
    )
    store = SimpleNamespace(
        get_agent_runtime_attempt=lambda _: ref,
        list_runtime_operation_attempts=lambda kind, key: [ref],
    )
    monkeypatch.setattr(
        "app.native_trajectory_view.claude_session_path", lambda _: None
    )
    app = FastAPI()
    register_native_trajectory_routes(app, lambda: store)
    with TestClient(app) as client:
        result = client.get("/api/console/runtime-attempts/7/trajectory").json()["item"]
    assert result["runtime_attempts"][0]["id"] == 7
    assert _is_spa_page_path("/runtime-attempts/7/trajectory")


def test_codex_native_started_command_keeps_input_until_completion(
    tmp_path, monkeypatch
):
    path = tmp_path / "command.jsonl"
    write_records(
        path,
        [
            {
                "type": "event_msg",
                "payload": {
                    "type": "item_started",
                    "item": {
                        "type": "CommandExecution",
                        "id": "command",
                        "command": "pytest exact",
                    },
                },
            },
            {
                "type": "event_msg",
                "payload": {
                    "type": "item_completed",
                    "item": {
                        "type": "CommandExecution",
                        "id": "command",
                        "aggregated_output": "exit failure",
                        "exit_code": 1,
                    },
                },
            },
        ],
    )
    monkeypatch.setattr(
        "app.native_trajectory_view.find_codex_session_path", lambda _: path
    )
    events = read_attempt_trajectory(
        attempt(runtime_kind="codex_cli", transcript_start=0, transcript_end=2)
    )["events"]
    assert [event["kind"] for event in events] == ["tool_call", "tool_output"]
    assert events[0]["trace"]["input"] == "pytest exact"
    assert events[1]["trace"]["status"] == "failed"


def test_meeting_parent_lists_consumer_and_audit_native_calls(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.store import AutoReplyStore
    from app.web_api.native_trajectory import (
        run_trajectory_links,
        register_native_trajectory_routes,
    )

    store = AutoReplyStore(tmp_path / "meeting-trajectory.sqlite3")
    task = store.upsert_meeting_alignment_job(
        meeting_id="native-meeting",
        title="Meeting",
        source_json="{}",
        participants_json="[]",
        ended_at="2026-11-08T00:00:00",
        eligible_at="2026-11-08T00:01:00",
        status="processing",
    )
    run = store.claim_professional_run(task)
    calls = [
        store.claim_runtime_operation_attempt(
            kind, str(run), "claude", "claude_cli", "local_oauth", "sonnet"
        )
        for kind in ("meeting", "meeting_audit")
    ]
    assert {row["id"] for row in run_trajectory_links(store, run)} == {
        row.id for row in calls
    }
    app = FastAPI()
    register_native_trajectory_routes(app, lambda: store)
    with TestClient(app) as client:
        result = client.get(
            f"/api/console/runtime-attempts/{calls[0].id}/trajectory"
        ).json()["item"]
    assert {row["id"] for row in result["runtime_attempts"]} == {
        row.id for row in calls
    }
