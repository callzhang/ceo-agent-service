import json
from time import perf_counter
from pathlib import Path

from app.codex_history import (
    count_codex_session_lines,
    extract_codex_assistant_messages_from_session,
    extract_codex_audit_events_from_session,
    extract_codex_mcp_tool_results_from_session,
    find_codex_session_path,
    normalize_stored_tool_events,
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
                "cwd": "/Users/principal/Documents/memory",
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
                        "text": "# AGENTS.md instructions for /Users/principal/Documents/memory",
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
                "call_id": "call-1",
                "arguments": json.dumps(
                    {"cmd": "rg -n 岗位 /Users/principal/Documents/memory/面试"},
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


def test_extract_codex_assistant_messages_from_session(tmp_path: Path):
    session_id = "assistant-result"
    session_path = write_session(tmp_path, session_id)
    records = json.loads(session_path.read_text(encoding="utf-8").splitlines()[-1])
    records["payload"] = {
        "type": "message",
        "role": "assistant",
        "content": [{"type": "output_text", "text": '{"outcome":"no_action"}'}],
    }
    session_path.write_text(
        session_path.read_text(encoding="utf-8")
        + "\n"
        + json.dumps(records)
        + "\n",
        encoding="utf-8",
    )

    extracted = extract_codex_assistant_messages_from_session(
        session_id, codex_home=tmp_path
    )

    extracted_records = [json.loads(line) for line in extracted.splitlines()]
    assert extracted_records[-1]["payload"]["role"] == "assistant"


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
        "app.codex_history._file_session_id",
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

    with session_path.open("a", encoding="utf-8") as session_file:
        session_file.write(
            "\n" + json.dumps({"type": "event_msg", "payload": {}}),
        )

    assert count_codex_session_lines(session_id, codex_home=tmp_path) == 3


def test_find_codex_session_path_reuses_index_until_the_index_changes(
    tmp_path: Path, monkeypatch
):
    session_id = "index-cache-session"
    session_path = write_session(tmp_path, session_id)
    refresh_codex_session_path_index(tmp_path)
    index_path = tmp_path / "session_path_index.jsonl"
    original_read_text = Path.read_text
    reads = 0

    def count_index_reads(path: Path, *args, **kwargs):
        nonlocal reads
        if path == index_path:
            reads += 1
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", count_index_reads)

    assert find_codex_session_path(session_id, codex_home=tmp_path) == session_path
    assert find_codex_session_path(session_id, codex_home=tmp_path) == session_path
    assert reads == 1

    # A new index revision must invalidate the cache, so a session created by
    # another process is discoverable without restarting the audit web.
    index_path.write_text(
        index_path.read_text(encoding="utf-8") + "\n", encoding="utf-8"
    )
    assert find_codex_session_path(session_id, codex_home=tmp_path) == session_path
    assert reads == 3


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


def test_render_local_codex_session_preserves_tool_identity_input_and_output(tmp_path: Path):
    session_id = "019e2c00-trace-session"
    write_session(tmp_path, session_id)

    rendered = render_local_codex_session(session_id, codex_home=tmp_path)

    tool_call, tool_output = [
        event for event in rendered.events if event.kind in {"tool_call", "tool_output"}
    ]
    assert tool_call.trace == {
        "call_id": "call-1",
        "name": "exec_command",
        "input": '{\n  "cmd": "rg -n 岗位 /Users/principal/Documents/memory/面试"\n}',
    }
    assert tool_output.trace == {
        "call_id": "call-1",
        "output": "Output:\n岗位画像.md:1:项目经理",
    }


def test_render_local_codex_session_renders_completed_mcp_event_as_one_trace(tmp_path: Path):
    session_id = "019e2c00-mcp-trace-session"
    session_path = write_session(tmp_path, session_id)
    completed_mcp_call = {
        "timestamp": "2026-05-14T12:00:06Z",
        "type": "event_msg",
        "payload": {
            "type": "item_completed",
            "item": {
                "type": "McpToolCall",
                "id": "mcp-1",
                "server": "codex_apps",
                "tool": "calendar.list_events",
                "arguments": {"calendar_id": "primary"},
                "result": {"content": [{"type": "text", "text": "[]"}]},
            },
        },
    }
    session_path.write_text(
        session_path.read_text(encoding="utf-8")
        + "\n"
        + json.dumps(completed_mcp_call, ensure_ascii=False),
        encoding="utf-8",
    )

    rendered = render_local_codex_session(session_id, codex_home=tmp_path)

    tool_event = rendered.events[-1]
    assert tool_event.kind == "tool"
    # The tool's real answer is the empty list at content[0].text; the
    # content/type envelope around it is not something to read.
    assert tool_event.trace == {
        "call_id": "mcp-1",
        "name": "codex_apps.calendar.list_events",
        "input": '{\n  "calendar_id": "primary"\n}',
        "output": "[]",
    }


def test_render_local_codex_session_uses_command_argv_for_trace_name_and_input(tmp_path: Path):
    session_id = "019e2c00-command-trace-session"
    session_path = write_session(tmp_path, session_id)
    completed_command = {
        "timestamp": "2026-05-14T12:00:06Z",
        "type": "event_msg",
        "payload": {
            "type": "item_completed",
            "item": {
                "type": "CommandExecution",
                "id": "command-1",
                "command": ["/bin/zsh", "-lc", "rg --files app"],
                "cwd": "file:///workspace",
                "aggregated_output": "app/codex_history.py",
            },
        },
    }
    session_path.write_text(
        session_path.read_text(encoding="utf-8")
        + "\n"
        + json.dumps(completed_command, ensure_ascii=False),
        encoding="utf-8",
    )

    rendered = render_local_codex_session(session_id, codex_home=tmp_path)

    tool_event = rendered.events[-1]
    assert tool_event.title == "Command: /bin/zsh -lc rg --files app"
    assert tool_event.trace == {
        "call_id": "command-1",
        "name": "/bin/zsh -lc rg --files app",
        "input": '{\n  "command": "/bin/zsh -lc rg --files app",\n  "cwd": "file:///workspace"\n}',
        "output": "app/codex_history.py",
    }


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
            "call_id": "call-1",
            "input": json.dumps(
                {"cmd": "rg -n 岗位 /Users/principal/Documents/memory/面试"},
                ensure_ascii=False,
                indent=2,
            ),
            "command": "rg -n 岗位 /Users/principal/Documents/memory/面试",
            "path": "/Users/principal/Documents/memory/面试",
        },
        {
            "event_type": "response_item",
            "tool": "tool_output",
            "call_id": "call-1",
            "output": "Output:\n岗位画像.md:1:项目经理",
            "path": "岗位画像.md",
        },
    ]


def test_extract_codex_audit_events_from_modern_completed_mcp_call(tmp_path: Path):
    session_id = "019e2c00-modern-mcp"
    session_path = (
        tmp_path
        / "sessions"
        / "2026"
        / "09"
        / "01"
        / f"rollout-2026-09-01T22-00-00-{session_id}.jsonl"
    )
    session_path.parent.mkdir(parents=True)
    result = {
        "content": [{"type": "text", "text": "读取完成"}],
        "structuredContent": {"items": [{"title": "上线范围"}]},
        "isError": False,
    }
    session_path.write_text(
        "\n".join(
            json.dumps(line, ensure_ascii=False)
            for line in (
                {"type": "session_meta", "payload": {"id": session_id}},
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "item_completed",
                        "item": {
                            "type": "McpToolCall",
                            "id": "exec-modern-1",
                            "server": "memory_connector",
                            "tool": "memory_recall",
                            "arguments": {"query": "上线范围"},
                            "result": result,
                        },
                    },
                },
            )
        ),
        encoding="utf-8",
    )

    events = extract_codex_audit_events_from_session(
        session_id,
        codex_home=tmp_path,
    )

    # The tool's real return value is `structuredContent`; `content` and
    # `isError` are the MCP envelope around it, not the answer itself, and
    # showing the envelope meant reading escaped JSON-inside-JSON.
    assert events == [{
        "event_type": "event_msg",
        "tool": "memory_recall",
        "call_id": "exec-modern-1",
        "input": json.dumps({"query": "上线范围"}, ensure_ascii=False, indent=2),
        "output": json.dumps(result["structuredContent"], ensure_ascii=False, indent=2),
    }]


def test_extract_codex_mcp_tool_results_from_session_reads_event_receipt(
    tmp_path: Path,
):
    session_id = "019e2c00-mcp-receipt"
    session_path = (
        tmp_path
        / "sessions"
        / "2026"
        / "08"
        / "11"
        / f"rollout-2026-08-11T04-00-00-{session_id}.jsonl"
    )
    session_path.parent.mkdir(parents=True)
    receipt = {
        "content": [{"type": "text", "text": "accepted"}],
        "structuredContent": {
            "operation": "chat message send",
            "operation_digest": "digest",
            "target_identifiers": {"group": "group-1"},
            "result_digest": "result-digest",
        },
        "isError": False,
    }
    session_path.write_text(
        "\n".join(
            json.dumps(line, ensure_ascii=False)
            for line in (
                {"type": "session_meta", "payload": {"id": session_id}},
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "mcp_tool_call_end",
                        "call_id": "call-1",
                        "invocation": {
                            "server": "agent_cli",
                            "tool": "execute_reviewed_write",
                            "arguments": {"argv": ["dws", "chat"]},
                        },
                        "result": {"Ok": receipt},
                    },
                },
            )
        ),
        encoding="utf-8",
    )

    events = extract_codex_mcp_tool_results_from_session(
        session_id, codex_home=tmp_path
    )

    assert events == [
        {
            "type": "item.completed",
            "item": {
                "type": "mcp_tool_call",
                "id": "call-1",
                "server": "agent_cli",
                "tool": "execute_reviewed_write",
                "arguments": {"argv": ["dws", "chat"]},
                "status": "completed",
                "result": receipt,
            },
        }
    ]


def test_extract_codex_audit_events_from_session_preserves_tool_search_call(
    tmp_path: Path,
):
    session_id = "019e2c00-tool-search"
    session_path = (
        tmp_path
        / "sessions"
        / "2026"
        / "07"
        / "06"
        / f"rollout-2026-07-06T14-00-00-{session_id}.jsonl"
    )
    session_path.parent.mkdir(parents=True)
    lines = [
        {
            "timestamp": "2026-07-06T14:00:00Z",
            "type": "session_meta",
            "payload": {"id": session_id},
        },
        {
            "timestamp": "2026-07-06T14:00:01Z",
            "type": "response_item",
            "payload": {
                "type": "tool_search_call",
                "call_id": "call-memory-discovery",
                "arguments": {
                    "query": "memory_connector memory_recall MCP semantic retrieval",
                    "limit": 5,
                },
            },
        },
    ]
    session_path.write_text(
        "\n".join(json.dumps(line, ensure_ascii=False) for line in lines),
        encoding="utf-8",
    )

    events = extract_codex_audit_events_from_session(
        session_id,
        codex_home=tmp_path,
    )

    assert events == [
        {
            "event_type": "response_item",
            "tool": "tool_search_call",
            "call_id": "call-memory-discovery",
            "input": json.dumps(
                {
                    "query": "memory_connector memory_recall MCP semantic retrieval",
                    "limit": 5,
                },
                ensure_ascii=False,
                indent=2,
            ),
        }
    ]


def test_extract_codex_audit_events_from_session_preserves_dws_material_read(
    tmp_path: Path,
):
    session_id = "019e2c00-dws-session"
    command = (
        "dws doc read --node https://alidocs.dingtalk.com/i/nodes/doc123 "
        "--format json"
    )
    session_path = (
        tmp_path
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
            "payload": {"id": session_id},
        },
        {
            "timestamp": "2026-05-14T12:00:01Z",
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "exec_command",
                "call_id": "call-dws-read",
                "arguments": json.dumps({"cmd": command}, ensure_ascii=False),
            },
        },
        {
            "timestamp": "2026-05-14T12:00:02Z",
            "type": "response_item",
            "payload": {
                "type": "function_call_output",
                "call_id": "call-dws-read",
                "output": "OpenAI 合作建议补充版\n建议先补齐材料。",
            },
        },
    ]
    session_path.write_text(
        "\n".join(json.dumps(line, ensure_ascii=False) for line in lines),
        encoding="utf-8",
    )

    events = extract_codex_audit_events_from_session(
        session_id,
        codex_home=tmp_path,
    )

    assert events == [
        {
            "event_type": "response_item",
            "tool": "exec_command",
            "call_id": "call-dws-read",
            "input": json.dumps({"cmd": command}, ensure_ascii=False, indent=2),
            "command": command,
        },
        {
            "event_type": "response_item",
            "tool": "tool_output",
            "call_id": "call-dws-read",
            "output": "OpenAI 合作建议补充版\n建议先补齐材料。",
        },
    ]


# The actual raw shape persisted into reply_attempts.audit_tool_events_json
# for attempt 9129 -- the stdout stream of a real `codex exec --json` run,
# not the native rollout file format. Trimmed to the fields the flattener
# reads; unrelated bookkeeping fields are dropped.
REAL_STREAM_EVENTS = [
    {"provider": {"thread_id": "01a09446-9a75-71a0-978e-508174303693"}, "type": "thread.started"},
    {"item": {"id": "item_0", "type": "agent_message", "message": "warning banner"}, "type": "item.completed"},
    {"provider": {}, "type": "turn.started"},
    {
        "item": {
            "arguments": {},
            "error": None,
            "id": "item_2",
            "result": None,
            "server": "memory_connector",
            "status": "in_progress",
            "tool": "user_get",
            "type": "mcp_tool_call",
        },
        "type": "item.started",
    },
    {
        "item": {
            "arguments": {},
            "error": None,
            "id": "item_2",
            "result": {"content": [{"type": "text", "text": '{"ok": true}'}]},
            "server": "memory_connector",
            "status": "completed",
            "tool": "user_get",
            "type": "mcp_tool_call",
        },
        "type": "item.completed",
    },
    {
        "item": {
            "aggregated_output": "",
            "command": "/bin/zsh -lc \"sed -n '1,10p' SKILL.md\"",
            "cwd": "/Users/derek/.agents/skills/ceo-mail-review",
            "exit_code": None,
            "id": "item_4",
            "status": "in_progress",
            "type": "command_execution",
        },
        "type": "item.started",
    },
    {
        "item": {
            "aggregated_output": "---\nname: ceo-mail-review\n---",
            "command": "/bin/zsh -lc \"sed -n '1,10p' SKILL.md\"",
            "cwd": "/Users/derek/.agents/skills/ceo-mail-review",
            "exit_code": 0,
            "id": "item_4",
            "status": "completed",
            "type": "command_execution",
        },
        "type": "item.completed",
    },
    {
        "item": {
            "arguments": {"task_id": 383926},
            "error": None,
            "id": "item_5",
            "result": {"content": [{"type": "text", "text": '{"status": "done"}'}]},
            "server": "agent_cli",
            "status": "completed",
            "tool": "unsubscribe_email",
            "type": "mcp_tool_call",
        },
        "type": "item.completed",
    },
    {"provider": {}, "type": "turn.completed"},
]


def test_normalize_stored_tool_events_flattens_the_real_codex_json_stream_shape():
    """This is the exact shape that made every call render as an unnamed,
    argument-less, resultless "tool" row once the session transcript that
    would otherwise be read live had rotated off the machine -- 13 rows on
    attempt 9129, none of them readable.
    """
    flattened = normalize_stored_tool_events(REAL_STREAM_EVENTS)

    assert [event["tool"] for event in flattened] == [
        "user_get",
        "command_execution",
        "unsubscribe_email",
    ]
    user_get = flattened[0]
    assert user_get["call_id"] == "item_2"
    assert user_get["mcp_name"] == "memory_connector"
    # The tool's real answer is content[0].text, re-parsed - not the
    # content/type envelope wrapped around it.
    assert json.loads(user_get["output"]) == {"ok": True}

    command = flattened[1]
    assert command["command"] == "/bin/zsh -lc \"sed -n '1,10p' SKILL.md\""
    assert command["path"] == "/Users/derek/.agents/skills/ceo-mail-review"
    assert command["output"] == "---\nname: ceo-mail-review\n---"

    unsubscribe = flattened[2]
    assert unsubscribe["call_id"] == "item_5"
    assert json.loads(unsubscribe["input"]) == {"task_id": 383926}


def test_normalize_stored_tool_events_keeps_the_completed_pair_not_the_started_one():
    events = normalize_stored_tool_events(REAL_STREAM_EVENTS)
    user_get = next(event for event in events if event["tool"] == "user_get")
    # The in_progress "item.started" record for the same id carried no result;
    # only the completed pairing does, and there must be exactly one row.
    assert "output" in user_get
    assert sum(1 for event in events if event["call_id"] == "item_2") == 1


def test_normalize_stored_tool_events_is_a_no_op_on_already_flat_events():
    already_flat = [
        {"tool": "unsubscribe_email", "call_id": "call-1", "input": "{}", "output": "done"}
    ]
    assert normalize_stored_tool_events(already_flat) == already_flat


def test_normalize_stored_tool_events_returns_empty_for_a_stream_with_no_calls():
    envelope_only = [
        {"provider": {}, "type": "thread.started"},
        {"item": {"id": "item_0", "type": "agent_message", "message": "hi"}, "type": "item.completed"},
        {"provider": {}, "type": "turn.completed"},
    ]
    assert normalize_stored_tool_events(envelope_only) == []


def test_normalize_stored_tool_events_unwraps_the_mcp_result_envelope():
    """A stored call's result is JSON-encoded twice: once for the real
    value, once more wrapping it in content[].text. Reading raw \\n and \\"
    escapes instead of the value itself was the whole complaint - fixed by
    unwrapping before the call ever reaches storage-fallback rendering.
    """
    events = [
        {
            "item": {
                "id": "item_1",
                "type": "mcp_tool_call",
                "tool": "get_interview_context",
                "server": "xiaoqing_interview",
                "arguments": {"interview_id": "int-1"},
                "status": "completed",
                "result": {
                    "content": [{"type": "text", "text": json.dumps({"status": "old"})}],
                    "structured_content": {"status": "ok", "candidate_name": "孙英双"},
                },
                "error": None,
            },
            "type": "item.completed",
        }
    ]

    [flattened] = normalize_stored_tool_events(events)

    assert json.loads(flattened["output"]) == {"status": "ok", "candidate_name": "孙英双"}


def test_normalize_stored_tool_events_shrinks_a_large_result_instead_of_slicing_the_text():
    """Real interview-context payloads run 40-50k characters once unwrapped -
    still over the 20k body cap. Cutting the encoded text at a fixed offset
    (the old behaviour) sliced through the middle of a string and handed
    back invalid JSON the page could not read at all. Shrinking the value
    itself keeps the result parseable no matter how large the source is.
    """
    from app.codex_history import MAX_EVENT_BODY_CHARS

    long_note = "候选人技术背景与项目经验详细说明" * 200  # well over the per-string cap
    interviews = [
        {"interview_id": f"int-{index}", "round": f"{index}面", "notes": long_note}
        for index in range(40)
    ]
    events = [
        {
            "item": {
                "id": "item_1",
                "type": "mcp_tool_call",
                "tool": "get_interview_context",
                "server": "xiaoqing_interview",
                "arguments": {},
                "status": "completed",
                "result": {"structured_content": {"interviews": interviews}},
                "error": None,
            },
            "type": "item.completed",
        }
    ]

    [flattened] = normalize_stored_tool_events(events)

    assert len(flattened["output"]) <= MAX_EVENT_BODY_CHARS
    parsed = json.loads(flattened["output"])  # must still be valid JSON
    assert len(parsed["interviews"]) < len(interviews)
    assert any("未显示" in str(item) for item in parsed["interviews"])


def test_normalize_stored_tool_events_falls_back_to_the_old_cut_only_as_a_last_resort():
    """A single string too long to fit even after shrinking (well past any
    realistic tool result) must still return something bounded rather than
    raise - the historical behaviour, now reached only in this edge case.
    """
    from app.codex_history import MAX_EVENT_BODY_CHARS

    events = [
        {
            "item": {
                "id": "item_1",
                "type": "mcp_tool_call",
                "tool": "dump_everything",
                "server": "x",
                "arguments": {},
                "status": "completed",
                "result": {"structured_content": "a" * (MAX_EVENT_BODY_CHARS * 2)},
                "error": None,
            },
            "type": "item.completed",
        }
    ]

    [flattened] = normalize_stored_tool_events(events)

    assert len(flattened["output"]) <= MAX_EVENT_BODY_CHARS + len("\n...[truncated]")
    assert flattened["output"].endswith("...[truncated]")
