import json
from time import perf_counter
from pathlib import Path

from ceo_agent_service.codex_history import (
    count_codex_session_lines,
    extract_codex_audit_events_from_session,
    find_codex_session_path,
    render_local_codex_session,
    refresh_codex_session_path_index,
)


def write_session(codex_home: Path, session_id: str) -> Path:
    session_path = (
        codex_home
        / "sessions"
        / "2026"
        / "05"
        / "14"
        / f"rollout-2026-05-14T12-00-00-{session_id}.jsonl"
    )
    session_path.parent.mkdir(parents=True)
    lines = [
        {
            "timestamp": "2026-05-14T12:00:00Z",
            "type": "session_meta",
            "payload": {
                "id": session_id,
                "cwd": "/Users/derek/Documents/memory",
                "originator": "codex exec",
                "cli_version": "0.1",
            },
        },
        {
            "timestamp": "2026-05-14T12:00:01Z",
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": "# AGENTS.md instructions for /Users/derek/Documents/memory",
                    }
                ],
            },
        },
        {
            "timestamp": "2026-05-14T12:00:01.500Z",
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "请判断候选人"}],
            },
        },
        {
            "timestamp": "2026-05-14T12:00:02Z",
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "exec_command",
                "arguments": json.dumps(
                    {"cmd": "rg -n 岗位 /Users/derek/Documents/memory/面试"},
                    ensure_ascii=False,
                ),
            },
        },
        {
            "timestamp": "2026-05-14T12:00:03Z",
            "type": "response_item",
            "payload": {
                "type": "function_call_output",
                "call_id": "call-1",
                "output": "Output:\n岗位画像.md:1:项目经理",
            },
        },
        {
            "timestamp": "2026-05-14T12:00:04Z",
            "type": "response_item",
            "payload": {
                "type": "reasoning",
                "summary": [{"type": "summary_text", "text": "先查岗位画像，再比较候选人经历。"}],
                "encrypted_content": "secret-hidden-reasoning",
            },
        },
        {
            "timestamp": "2026-05-14T12:00:04.500Z",
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {"total_tokens": 1000},
            },
        },
        {
            "timestamp": "2026-05-14T12:00:05Z",
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "需要先看简历和JD。"}],
            },
        },
    ]
    session_path.write_text(
        "\n".join(json.dumps(line, ensure_ascii=False) for line in lines),
        encoding="utf-8",
    )
    return session_path


def test_find_codex_session_path_uses_local_codex_home(tmp_path: Path):
    session_id = "019e2c00-test-session"
    session_path = write_session(tmp_path, session_id)

    assert find_codex_session_path(session_id, codex_home=tmp_path) == session_path


def test_find_codex_session_path_does_not_inspect_all_files_on_miss(
    tmp_path: Path, monkeypatch
):
    session_dir = tmp_path / "sessions" / "2026" / "05" / "14"
    session_dir.mkdir(parents=True)
    for index in range(5):
        (session_dir / f"unrelated-{index}.jsonl").write_text(
            json.dumps(
                {
                    "type": "session_meta",
                    "payload": {"id": f"session-{index}"},
                }
            ),
            encoding="utf-8",
        )

    def fail_if_file_is_opened(_path):
        raise AssertionError("request path must not inspect transcript contents")

    monkeypatch.setattr(
        "ceo_agent_service.codex_history._file_session_id",
        fail_if_file_is_opened,
    )

    assert find_codex_session_path("missing-session", codex_home=tmp_path) is None


def test_find_codex_session_path_miss_stays_under_one_second(tmp_path: Path):
    session_dir = tmp_path / "sessions" / "2026" / "05" / "14"
    session_dir.mkdir(parents=True)
    for index in range(1000):
        (session_dir / f"unrelated-{index}.jsonl").write_text(
            '{"type":"session_meta","payload":{"id":"unrelated"}}\n',
            encoding="utf-8",
        )

    started = perf_counter()
    path = find_codex_session_path("missing-session", codex_home=tmp_path)
    elapsed = perf_counter() - started

    assert path is None
    assert elapsed < 1


def test_find_codex_session_path_uses_refreshed_path_index(tmp_path: Path):
    session_id = "019e2c00-test-session"
    session_path = tmp_path / "sessions" / "legacy-name.jsonl"
    session_path.parent.mkdir(parents=True)
    session_path.write_text(
        json.dumps(
            {
                "type": "session_meta",
                "payload": {"id": session_id},
            }
        )
        + "\n"
        + json.dumps({"type": "response_item", "payload": {"type": "message"}}),
        encoding="utf-8",
    )

    refresh_codex_session_path_index(tmp_path)

    assert find_codex_session_path(session_id, codex_home=tmp_path) == session_path
    assert count_codex_session_lines(session_id, codex_home=tmp_path) == 2


def test_render_local_codex_session_renders_reasoning_summary_and_skips_system_events(tmp_path: Path):
    session_id = "019e2c00-test-session"
    session_path = write_session(tmp_path, session_id)

    rendered = render_local_codex_session(session_id, codex_home=tmp_path)

    assert rendered.path == session_path
    assert rendered.missing is False
    body = "\n".join(event.body for event in rendered.events)
    assert "请判断候选人" in body
    assert "AGENTS.md instructions" in body
    assert "rg -n 岗位" in body
    assert "岗位画像.md" in body
    assert "需要先看简历和JD" in body
    assert "先查岗位画像，再比较候选人经历。" in body
    assert "secret-hidden-reasoning" not in body
    assert "token_count" not in body
    assert all(not event.kind.startswith("event:") for event in rendered.events)
    expanded_by_kind = {event.kind: event.expanded for event in rendered.events}
    assert expanded_by_kind["system_context"] is False
    assert expanded_by_kind["user"] is True
    assert expanded_by_kind["assistant"] is True
    assert expanded_by_kind["tool_call"] is False
    assert expanded_by_kind["tool_output"] is False
    assert expanded_by_kind["reasoning"] is False


def test_extract_codex_audit_events_from_session_respects_line_range(tmp_path: Path):
    session_id = "019e2c00-test-session"
    write_session(tmp_path, session_id)

    events = extract_codex_audit_events_from_session(
        session_id,
        codex_home=tmp_path,
        start_line=2,
        end_line=5,
    )

    assert events == [
        {
            "event_type": "response_item",
            "tool": "exec_command",
            "command": "rg -n 岗位 /Users/derek/Documents/memory/面试",
            "path": "/Users/derek/Documents/memory/面试",
        },
        {
            "event_type": "response_item",
            "tool": "tool_output",
            "command": "call-1",
            "path": "岗位画像.md",
        },
    ]
