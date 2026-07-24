from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from app.codex_decision import _subprocess_failure_reason
from app.codex_runner import CodexRunner, _config_string
from app.memory_connector_auth import MemoryConnectorAuthorizationRequired
from app.memory_connector_client import MemoryWriteResult
from app.process_runner import run_process_with_idle_timeout
from app.store import AutoReplyStore
from app.wechat.codex_safety import completed_mcp_tool_calls, completed_tool_events

WRITE_SCHEMA_PATH = (
    Path(__file__).resolve().parent
    / "schemas"
    / "wechat_memory_write_result.schema.json"
)


class CodexMemoryWriteOutcomeUnknown(RuntimeError):
    pass


class CodexMcpMemoryClient:
    """Use native Codex MCP configuration for service-owned memory writes."""

    def __init__(
        self,
        *,
        workspace: Path,
        codex_bin: str = "codex",
        codex_config_path: Path | None = None,
        executor=None,
        timeout_seconds: int = 1200,
        idle_timeout_seconds: int = 900,
    ) -> None:
        self.runner = CodexRunner(workspace=workspace, codex_bin=codex_bin)
        self.codex_config_path = codex_config_path or Path.home() / ".codex" / "config.toml"
        self.executor = executor
        self.timeout_seconds = timeout_seconds
        self.idle_timeout_seconds = idle_timeout_seconds

    def ensure_ready_sync(self) -> None:
        return None

    def memory_write_sync(
        self,
        *,
        data: str,
        type: Literal["text", "message"],
        created_at: str,
        source_description: str,
        wait_for_processing: bool = False,
    ) -> MemoryWriteResult:
        return self._codex_memory_write(
            data=data,
            type=type,
            created_at=created_at,
        )

    def _codex_memory_write(
        self,
        *,
        data: str,
        type: Literal["text", "message"],
        created_at: str,
    ) -> MemoryWriteResult:
        self.ensure_ready_sync()
        prompt = (
            "必须且只能调用一次 memory_write MCP 工具。"
            "arguments 必须严格等于输入 JSON 中的 data、type、created_at 三个字段；"
            "不得传 user_id、graph_id、graph_ids、source_description、额外证据或任何其他字段。"
            "调用后只输出 {\"status\":\"attempted\"}。\n"
            + json.dumps(
                {"data": data, "type": type, "created_at": created_at},
                ensure_ascii=False,
            )
        )
        command = self.runner.build_command(
            prompt,
            None,
            output_schema_path=WRITE_SCHEMA_PATH,
            ignore_user_config=False,
        )
        _remove_config_option(command, "developer_instructions=")
        command[-1:-1] = [
            "-c",
            _config_string(
                "developer_instructions",
                (
                    "You are executing a service-owned Memory write. "
                    "Call exactly one memory_connector.memory_write tool with "
                    "the exact user-provided data, type, and created_at fields. "
                    "Do not call any other tool. Do not add user_id, graph_id, "
                    "graph_ids, source_description, evidence, or any extra field. "
                    'After the tool call, output exactly {"status":"attempted"}.'
                ),
            ),
            "-c",
            'mcp_servers.memory_connector.enabled_tools=["memory_write"]',
            "-c",
            'mcp_servers.memory_connector.disabled_tools=["memory_recall"]',
        ]
        last_error: CodexMemoryWriteOutcomeUnknown | None = None
        max_attempts = 1 if self.executor is not None else 3
        for _attempt in range(max_attempts):
            if self.executor is not None:
                raw = self.executor(command, prompt)
            else:
                completed = run_process_with_idle_timeout(
                    command,
                    prompt=prompt,
                    env=self.runner.build_env(),
                    total_timeout_seconds=self.timeout_seconds,
                    idle_timeout_seconds=self.idle_timeout_seconds,
                )
                if completed.timed_out:
                    raise CodexMemoryWriteOutcomeUnknown(
                        completed.timeout_reason or "memory write outcome unknown"
                    )
                if completed.returncode != 0:
                    reason = _subprocess_failure_reason(
                        completed.stderr,
                        completed.stdout,
                    )
                    if _looks_like_memory_authorization_error(reason):
                        raise MemoryConnectorAuthorizationRequired(reason)
                    raise CodexMemoryWriteOutcomeUnknown(
                        f"memory write outcome unknown: {reason}"
                    )
                raw = completed.stdout
            try:
                return self._memory_result_from_audit(
                    raw,
                    data=data,
                    type=type,
                    created_at=created_at,
                )
            except CodexMemoryWriteOutcomeUnknown as exc:
                last_error = exc
                continue
        if last_error is not None:
            raise last_error
        raise CodexMemoryWriteOutcomeUnknown("memory write outcome unknown")

    @staticmethod
    def _memory_result_from_audit(
        raw: str,
        *,
        data: str,
        type: str,
        created_at: str,
    ) -> MemoryWriteResult:
        calls = completed_mcp_tool_calls(raw)
        memory_calls = [
            call
            for call in calls
            if AutoReplyStore._is_memory_write_tool_name(str(call.get("tool") or ""))
        ]
        if (
            len(completed_tool_events(raw)) != 1
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
        parsed = AutoReplyStore._parse_memory_write_output(output_text)
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


def _remove_config_option(command: list[str], prefix: str) -> None:
    index = 0
    while index < len(command) - 1:
        if command[index] == "-c" and command[index + 1].startswith(prefix):
            del command[index : index + 2]
            continue
        index += 1
