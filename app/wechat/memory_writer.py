"""Approved-only, claimed and audited writes to Friday Memory."""
from __future__ import annotations

import json
from pathlib import Path

from app.agent_runtime_production import build_production_routed_codex_execution
from app.agent_runtime_router import (
    ApprovedCodexCommandFactory,
    RoutedCodexExecution,
    RoutedCodexExecutionError,
    RoutedResultCodec,
)

WRITE_SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schemas" / "wechat_memory_write_result.schema.json"
MEMORY_ID_CODEC = RoutedResultCodec.text(schema_id="wechat_memory_write.id.v1")
MEMORY_WRITE_CAPABILITIES = frozenset(
    {"structured_output", "mcp:memory_connector:memory_write"}
)
class MemoryWriteOutcomeUnknown(RuntimeError):
    pass


class CodexMemoryWriteBackend:
    def __init__(self, workspace: Path, store, codex_bin: str = "codex",
                 routed_execution: RoutedCodexExecution | None = None,
                 timeout_seconds: int = 1200, idle_timeout_seconds: int = 900):
        self.routed_execution = routed_execution or build_production_routed_codex_execution(
            store=store,
            workspace=workspace,
            codex_bin=codex_bin,
            total_timeout_seconds=timeout_seconds,
            idle_timeout_seconds=idle_timeout_seconds,
        )

    def write(self, candidate_id: int, statement: str, *, source_time_start: str, source_time_end: str) -> str:
        prompt = (
            "必须且只能调用一次 memory_write。data 只传下面 final_statement；"
            "type 使用 text；created_at 使用 source_time_start（为空才使用 source_time_end）。"
            "不得调用任何其他工具。绝不传 user_id、graph_id、graph_ids 或聊天 evidence。"
            "调用后只输出 {\"status\":\"attempted\"}。\n"
            + json.dumps({"final_statement": statement,
                          "source_time_start": source_time_start,
                          "source_time_end": source_time_end}, ensure_ascii=False)
        )
        try:
            result = self.routed_execution.execute(
                workload_kind="memory",
                workload_key=f"wechat_memory_candidate:{candidate_id}",
                prompt=prompt,
                command_factory=ApprovedCodexCommandFactory.effectful_memory_write(
                    developer_instructions=(
                        "Call exactly one memory_connector.memory_write using the exact "
                        "approved statement and created_at. Do not call any other tool."
                    ),
                    output_schema_path=WRITE_SCHEMA_PATH,
                ),
                parser=lambda raw: self._memory_id_from_audit(
                    raw,
                    statement=statement,
                    expected_created_at=source_time_start or source_time_end,
                ),
                result_codec=MEMORY_ID_CODEC,
                required_capabilities=MEMORY_WRITE_CAPABILITIES,
            )
        except RoutedCodexExecutionError as exc:
            raise MemoryWriteOutcomeUnknown("memory write outcome unknown") from exc
        return result.value

    @staticmethod
    def _memory_id_from_audit(
        raw: str, *, statement: str, expected_created_at: str,
    ) -> str:
        from app.store import AutoReplyStore
        from app.wechat.codex_safety import (
            completed_mcp_tool_calls,
            completed_tool_events,
        )

        calls = completed_mcp_tool_calls(raw)
        memory_calls = [call for call in calls
                        if AutoReplyStore._is_memory_write_tool_name(
                            str(call.get("tool") or ""))]
        if (
            len(completed_tool_events(raw)) != 1
            or len(calls) != 1
            or len(memory_calls) != 1
        ):
            raise MemoryWriteOutcomeUnknown("memory write outcome unknown: expected one tool call")
        call = memory_calls[0]
        arguments = call.get("arguments")
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError as exc:
                raise MemoryWriteOutcomeUnknown(
                    "memory write outcome unknown: invalid arguments"
                ) from exc
        if (
            not isinstance(arguments, dict)
            or set(arguments) != {"data", "type", "created_at"}
            or arguments.get("data") != statement
            or arguments.get("type") != "text"
            or arguments.get("created_at") != expected_created_at
        ):
            raise MemoryWriteOutcomeUnknown(
                "memory write outcome unknown: unsafe arguments"
            )
        output = call.get("result")
        if output is None:
            raise MemoryWriteOutcomeUnknown("memory write outcome unknown: missing tool result")
        output_text = output if isinstance(output, str) else json.dumps(output, ensure_ascii=False)
        parsed = AutoReplyStore._parse_memory_write_output(output_text)
        if parsed.get("status") == "failed":
            raise RuntimeError(parsed.get("last_error") or "memory_write failed")
        stable_id = parsed.get("memory_episode_id", "").strip()
        if parsed.get("status") != "written" or not stable_id:
            raise MemoryWriteOutcomeUnknown(
                "memory write outcome unknown: no explicit successful tool result"
            )
        return stable_id


class WechatMemoryWriter:
    def __init__(self, store, memory_backend):
        self.store = store
        self.memory_backend = memory_backend

    def write(self, candidate_id: int) -> str:
        claim = self.store.claim_wechat_memory_candidate_write(candidate_id)
        if claim["outcome"] == "written":
            return claim["memory_id"]
        if claim["outcome"] == "writing":
            attempts = self.store.list_runtime_operation_attempts(
                "memory", f"wechat_memory_candidate:{candidate_id}"
            )
            if not attempts or attempts[-1].status != "completed":
                raise RuntimeError("memory write already in progress")
            row = self.store.get_wechat_memory_candidate(candidate_id)
            if row is None:
                raise ValueError("candidate not found")
            row["edited_statement"] = row["edited_statement"] or row["statement"]
        elif claim["outcome"] != "claimed":
            raise ValueError(claim["reason"])
        else:
            row = claim["candidate"]
        try:
            memory_id = self.memory_backend.write(
                candidate_id, row["edited_statement"], source_time_start=row["source_time_start"],
                source_time_end=row["source_time_end"],
            )
        except MemoryWriteOutcomeUnknown:
            self.store.finish_wechat_memory_candidate_write(
                candidate_id, status="unknown", error="memory write outcome unknown")
            raise
        except Exception as exc:
            if "outcome unknown" in str(exc).casefold():
                self.store.finish_wechat_memory_candidate_write(
                    candidate_id, status="unknown", error=str(exc))
            else:
                self.store.finish_wechat_memory_candidate_write(
                    candidate_id, status="failed", error=str(exc))
            raise
        self.store.finish_wechat_memory_candidate_write(
            candidate_id, status="written", memory_id=memory_id)
        return memory_id
