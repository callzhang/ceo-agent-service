"""Read-only, paged presentation of a recorded native invocation.

No transcript copies are stored. Format differences live here, never in task
execution. Private reasoning blocks are not part of the visible trajectory.
"""

import json
from itertools import islice

from app.native_trajectory import claude_session_path, find_codex_session_path


def trajectory_url(attempt_id: int) -> str:
    return f"/runtime-attempts/{attempt_id}/trajectory"


def _text(value):
    return (
        value
        if isinstance(value, str)
        else json.dumps(value, ensure_ascii=False, indent=2)
    )


def _event(kind, body, timestamp="", title="", trace=None):
    return dict(
        kind=kind,
        body=_text(body),
        timestamp=timestamp,
        title=title or kind,
        **({"trace": trace} if trace is not None else {}),
    )


def _tool(kind, call_id, name, value, timestamp, status=""):
    key = "output" if kind == "tool_output" else "input"
    return _event(
        kind,
        value,
        timestamp,
        name,
        dict(
            call_id=call_id,
            name=name,
            **{key: _text(value)},
            **({"status": status} if status else {}),
        ),
    )


def _message_events(message, timestamp):
    role = message.get("role", "user")
    if role == "assistant" and message.get("channel") not in (
        None,
        "commentary",
        "final",
    ):
        return []
    content = message.get("content", [])
    if isinstance(content, str):
        return [_event(role, content, timestamp)]
    result = []
    for block in content if isinstance(content, list) else []:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind in ("text", "input_text", "output_text") and isinstance(
            block.get("text"), str
        ):
            result.append(_event(role, block["text"], timestamp))
        elif kind == "tool_use":
            result.append(
                _tool(
                    "tool_call",
                    block.get("id", ""),
                    block.get("name", ""),
                    block.get("input", {}),
                    timestamp,
                )
            )
        elif kind == "tool_result":
            result.append(
                _tool(
                    "tool_output",
                    block.get("tool_use_id", ""),
                    "",
                    block.get("content", ""),
                    timestamp,
                    "failed" if block.get("is_error") else "completed",
                )
            )
        elif kind in ("image", "input_image", "document"):
            result.append(
                _event(
                    "system_context", f"原生 {kind} 非文本输入", timestamp, "附件输入"
                )
            )
    return result


def _claude_events(record):
    kind, timestamp = record.get("type"), record.get("timestamp", "")
    if kind in ("user", "assistant") and isinstance(record.get("message"), dict):
        return _message_events({**record["message"], "role": kind}, timestamp)
    if kind == "result":
        return [
            _event(
                "assistant",
                record.get("structured_output")
                or record.get("result")
                or record.get("errors", []),
                timestamp,
                "运行结果",
            )
        ]
    if kind == "system" and record.get("subtype") in (
        "api_error",
        "local_command",
        "compact_boundary",
    ):
        return [_event("system_context", record, timestamp, record["subtype"])]
    return []


def _codex_events(record):
    timestamp, body = record.get("timestamp", ""), record.get("payload")
    if not isinstance(body, dict):
        return []
    if record.get("type") == "response_item":
        kind = body.get("type")
        if kind == "message":
            return _message_events(body, timestamp)
        if kind in ("function_call", "custom_tool_call"):
            return [
                _tool(
                    "tool_call",
                    body.get("call_id", ""),
                    body.get("name", ""),
                    body.get("arguments", "")
                    if kind == "function_call"
                    else body.get("input", ""),
                    timestamp,
                )
            ]
        if kind in ("function_call_output", "custom_tool_call_output"):
            return [
                _tool(
                    "tool_output",
                    body.get("call_id", ""),
                    "",
                    body.get("output", ""),
                    timestamp,
                )
            ]
    if record.get("type") == "event_msg":
        if body.get("type") in ("item_started", "item_completed") and isinstance(
            body.get("item"), dict
        ):
            item = body["item"]
            started = body["type"] == "item_started"
            if item.get("type") == "McpToolCall":
                name = ".".join(
                    str(item[key]) for key in ("server", "tool") if item.get(key)
                )
                return [
                    _tool(
                        "tool_call" if started else "tool_output",
                        item.get("id", ""),
                        name,
                        item.get("arguments", {})
                        if started
                        else item.get("result", ""),
                        timestamp,
                        "" if started else item.get("status", "completed"),
                    )
                ]
            if item.get("type") == "CommandExecution":
                return [
                    _tool(
                        "tool_call" if started else "tool_output",
                        item.get("id", ""),
                        "Command",
                        item.get("command", "")
                        if started
                        else item.get("aggregated_output", ""),
                        timestamp,
                        ""
                        if started
                        else "failed"
                        if item.get("exit_code")
                        else "completed",
                    )
                ]
            if not started and item.get("type") == "AgentMessage":
                return _message_events(
                    {
                        "role": "assistant",
                        "content": item.get("content") or item.get("text", ""),
                    },
                    timestamp,
                )
        if body.get("type") in (
            "task_started",
            "task_complete",
            "error",
            "turn_aborted",
        ):
            return [_event("system_context", body, timestamp, body["type"])]
    if record.get("type") == "session_meta":
        return [
            _event(
                "system_context",
                {
                    k: body[k]
                    for k in ("id", "cwd", "model", "cli_version")
                    if k in body
                },
                timestamp,
                "Session",
            )
        ]
    return []


def _unavailable(message):
    return dict(
        available=False, message=message, events=[], has_more=False, next_cursor=None
    )


def read_attempt_trajectory(attempt, *, after=None, page_size=100, friday_reader=None):
    """Read one page of the invocation's exact recorded range, without clipping bodies."""
    if attempt.runtime_kind == "friday_runtime":
        return _friday_page(
            attempt, after=after, page_size=page_size, reader=friday_reader
        )
    start, end = attempt.transcript_start, attempt.transcript_end
    cursor = start if after is None else after
    if cursor < start or cursor > end:
        raise ValueError("cursor outside recorded native range")
    if not attempt.session_id or end <= start:
        return _unavailable("本次调用未保存完整的原生会话范围。")
    readers = {
        "codex_cli": (find_codex_session_path, _codex_events),
        "claude_cli": (claude_session_path, _claude_events),
    }
    if attempt.runtime_kind not in readers:
        return _unavailable("运行时未提供原生轨迹读取器。")
    locate, render = readers[attempt.runtime_kind]
    path = locate(attempt.session_id)
    if path is None:
        return _unavailable("原生会话文件已清理或当前不可读取。")
    stop = min(end, cursor + page_size)
    try:
        with path.open(encoding="utf-8") as stream:
            records = list(islice(stream, cursor, stop))
        if len(records) != stop - cursor:
            return _unavailable("原生会话文件缺少已记录范围内的行。")
        decoded = [json.loads(line) for line in records]
        if any(not isinstance(record, dict) for record in decoded):
            return _unavailable("原生记录不是 JSON 对象。")
        events = [event for record in decoded for event in render(record)]
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return _unavailable(f"原生会话读取失败：{type(exc).__name__}")
    return dict(
        available=True,
        message="",
        events=events,
        has_more=stop < end,
        next_cursor=stop if stop < end else None,
        range_start=start,
        range_end=end,
        read_through=stop,
    )


def _friday_page(attempt, *, after, page_size, reader):
    from app.friday_runtime_adapter import FridayRuntimeError

    if not attempt.session_id or not attempt.transcript_reference:
        return _unavailable("本次调用未保存 Friday thread / operation 身份。")
    if reader is None:
        import os
        from app.agent_runtime_config import load_runtime_config
        from app.friday_runtime_adapter import FridayRuntimeAdapter

        reader = FridayRuntimeAdapter(load_runtime_config(os.environ)).read_trajectory
    try:
        data = reader(
            attempt.session_id.removeprefix("friday_thread:"),
            attempt.transcript_reference.removeprefix("friday_operation:"),
        )
    except (FridayRuntimeError, ValueError) as exc:
        return _unavailable(str(exc))
    records = data["events"]
    cursor = 0 if after is None else after
    if cursor < 0 or cursor > len(records):
        raise ValueError("cursor outside recorded Friday range")
    stop = min(len(records), cursor + page_size)
    events = []
    for record in records[cursor:stop]:
        kind, body, timestamp = (
            record["event_type"],
            record.get("payload", {}),
            record.get("created_at", ""),
        )
        if kind == "message.created":
            events.append(
                _event(
                    body.get("role", "user"),
                    body.get("content", ""),
                    timestamp,
                    "Message",
                )
            )
        elif kind.startswith("tool."):
            events.append(
                _tool(
                    "tool_call" if kind == "tool.started" else "tool_output",
                    body.get("tool_call_id", ""),
                    body.get("tool_name", ""),
                    body,
                    timestamp,
                    "failed"
                    if kind == "tool.failed"
                    else ""
                    if kind == "tool.started"
                    else "completed",
                )
            )
        elif kind == "trace.event":
            events.append(
                _event(
                    "system_context",
                    "原生内部 trace 事件（私有模型过程不展示）",
                    timestamp,
                    kind,
                )
            )
        else:
            events.append(_event("system_context", body, timestamp, kind))
    return dict(
        available=True,
        message="",
        events=events,
        has_more=stop < len(records),
        next_cursor=stop if stop < len(records) else None,
        range_start=0,
        range_end=len(records),
        read_through=stop,
    )
