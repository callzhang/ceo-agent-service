from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.agent_runtime_production import build_production_routed_codex_execution
from app.agent_runtime_router import (
    ApprovedCodexCommandFactory,
    RoutedCodexExecution,
    RoutedCodexExecutionError,
    RoutedResultCodec,
)
from app.store import AutoReplyStore
from app.wechat.codex_safety import completed_mcp_tool_calls, completed_tool_events

WRITE_SCHEMA_PATH = (
    Path(__file__).resolve().parent
    / "schemas"
    / "wechat_memory_write_result.schema.json"
)
MEMORY_WRITE_RESULT_CODEC = RoutedResultCodec.text(
    schema_id="memory_write.episode_uuid.v1"
)
MEMORY_WRITE_CAPABILITIES = frozenset(
    {"structured_output", "mcp:memory_connector:memory_write"}
)


@dataclass(frozen=True)
class MemoryWriteResult:
    episode_uuid: str
    processing_status: str
    duplicate: bool


class CodexMemoryWriteAuthorizationRequired(RuntimeError):
    pass


class CodexMemoryWriteOutcomeUnknown(RuntimeError):
    pass


class CodexMemoryWriteToolFailed(RuntimeError):
    pass


def run_codex_memory_write(
    *,
    workspace: Path,
    store: AutoReplyStore,
    event_id: int,
    data: str,
    type: Literal["text", "message"],
    created_at: str,
    source_description: str,
    codex_bin: str = "codex",
    routed_execution: RoutedCodexExecution | None = None,
    timeout_seconds: int = 1200,
    idle_timeout_seconds: int = 900,
) -> MemoryWriteResult:
    del source_description
    routed_execution = routed_execution or build_production_routed_codex_execution(
        store=store,
        workspace=workspace,
        codex_bin=codex_bin,
        total_timeout_seconds=timeout_seconds,
        idle_timeout_seconds=idle_timeout_seconds,
    )
    prompt = (
        "如果 memory_write 未直接可用，先调用 tool_search 查询并加载 "
        "memory_connector memory_write；tool_search 只能用于这次工具发现。"
        "随后必须且只能调用一次 memory_write MCP 工具。"
        "arguments 必须严格等于输入 JSON 中的 data、type、created_at 三个字段；"
        "不得传 user_id、graph_id、graph_ids、source_description、额外证据或任何其他字段。"
        "除 tool_search 和这一次 memory_write 外不得调用其他工具。"
        "只有 memory_write 调用完成后才能输出 {\"status\":\"attempted\"}。\n"
        + json.dumps(
            {"data": data, "type": type, "created_at": created_at},
            ensure_ascii=False,
        )
    )
    try:
        routed_result = routed_execution.execute(
            workload_kind="memory",
            workload_key=f"memory_write_event:{event_id}",
            prompt=prompt,
            command_factory=ApprovedCodexCommandFactory.effectful_memory_write(
                developer_instructions=(
                    "Execute exactly one memory_connector.memory_write with the exact "
                    "data, type, and created_at fields. Do not call any other tool or "
                    "add identity, graph, source, or evidence fields."
                ),
                output_schema_path=WRITE_SCHEMA_PATH,
            ),
            parser=lambda raw: memory_result_from_codex_audit(
                raw, data=data, type=type, created_at=created_at
            ).episode_uuid,
            result_codec=MEMORY_WRITE_RESULT_CODEC,
            required_capabilities=MEMORY_WRITE_CAPABILITIES,
        )
    except RoutedCodexExecutionError as exc:
        raise CodexMemoryWriteOutcomeUnknown("memory write outcome unknown") from exc
    result = MemoryWriteResult(
        episode_uuid=routed_result.value,
        processing_status="completed",
        duplicate=False,
    )
    with store._connect() as db:
        cursor = db.execute(
            """
            update memory_write_events
            set status='written', memory_episode_id=?, last_error='',
                updated_at=current_timestamp
            where id=? and status in ('pending', 'failed', 'written')
            """,
            (result.episode_uuid, event_id),
        )
        if cursor.rowcount != 1:
            raise ValueError("memory write event is not eligible")
    return result


def memory_result_from_codex_audit(
    raw: str,
    *,
    data: str,
    type: str,
    created_at: str,
) -> MemoryWriteResult:
    effectful_tool_events = [
        event
        for event in completed_tool_events(raw)
        if event.get("type") != "tool_search_call"
    ]
    calls = completed_mcp_tool_calls(raw)
    memory_calls = [
        call
        for call in calls
        if AutoReplyStore._is_memory_write_tool_name(str(call.get("tool") or ""))
    ]
    if (
        len(effectful_tool_events) != 1
        or len(calls) != 1
        or len(memory_calls) != 1
    ):
        raise CodexMemoryWriteOutcomeUnknown(
            "memory write outcome unknown: expected one memory_write tool call"
        )
    call = memory_calls[0]
    arguments = call.get("arguments")
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError as exc:
            raise CodexMemoryWriteOutcomeUnknown(
                "memory write outcome unknown: invalid arguments"
            ) from exc
    if (
        not isinstance(arguments, dict)
        or set(arguments) != {"data", "type", "created_at"}
        or arguments.get("data") != data
        or arguments.get("type") != type
        or arguments.get("created_at") != created_at
    ):
        raise CodexMemoryWriteOutcomeUnknown(
            "memory write outcome unknown: unsafe arguments"
        )
    output = call.get("result")
    if output is None:
        raise CodexMemoryWriteOutcomeUnknown(
            "memory write outcome unknown: missing tool result"
        )
    output_text = (
        output if isinstance(output, str) else json.dumps(output, ensure_ascii=False)
    )
    normalized_output = _normalize_memory_tool_output(output_text)
    explicit_failure = _explicit_memory_tool_failure(normalized_output)
    if explicit_failure:
        if _looks_like_memory_authorization_error(normalized_output):
            raise CodexMemoryWriteAuthorizationRequired(explicit_failure)
        raise CodexMemoryWriteToolFailed(explicit_failure)
    parsed = AutoReplyStore._parse_memory_write_output(normalized_output)
    if parsed.get("status") == "failed":
        raise RuntimeError(parsed.get("last_error") or "memory_write failed")
    episode_uuid = str(parsed.get("memory_episode_id") or "").strip()
    if parsed.get("status") != "written" or not episode_uuid:
        raise CodexMemoryWriteOutcomeUnknown(
            "memory write outcome unknown: no explicit successful tool result"
        )
    return MemoryWriteResult(
        episode_uuid=episode_uuid,
        processing_status="completed",
        duplicate=False,
    )


def _normalize_memory_tool_output(output: str) -> str:
    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        return output
    if isinstance(payload, list):
        return json.dumps({"content": payload}, ensure_ascii=False)
    return output


def _explicit_memory_tool_failure(output: str) -> str:
    payload = AutoReplyStore._load_memory_json(output)
    texts: list[str] = []
    if isinstance(payload, dict) and isinstance(payload.get("content"), list):
        texts.extend(
            str(item.get("text") or "")
            for item in payload["content"]
            if isinstance(item, dict)
        )
    texts.append(output)
    normalized = " ".join(texts).casefold()
    if "error executing tool memory_write:" not in normalized:
        return ""
    if any(
        marker in normalized
        for marker in (
            "couldn't connect",
            "connection refused",
            "failed to establish connection",
            "database unavailable",
        )
    ):
        return "memory backend unavailable"
    return "memory write tool failed"


def _looks_like_memory_authorization_error(reason: str) -> bool:
    normalized = reason.casefold()
    return any(
        marker in normalized
        for marker in (
            "authorization",
            "unauthorized",
            "missing bearer",
            "without a bearer",
            "oauth",
            "login",
        )
    )
