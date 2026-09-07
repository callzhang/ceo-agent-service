"""Claimed writes to Friday Memory using a typed provider result."""

from __future__ import annotations

import json
from pathlib import Path

from app.agent_runtime_production import build_production_routed_codex_execution
from app.agent_runtime_router import (
    CodexCommandFactory,
    RoutedCodexExecution,
    RoutedCodexExecutionError,
    RoutedResultCodec,
)
from app.codex_memory_write import (
    _failure_from_routed_error,
    _memory_write_typed_result_json,
    memory_result_from_typed_output,
)

WRITE_SCHEMA_PATH = (
    Path(__file__).resolve().parents[1]
    / "schemas"
    / "wechat_memory_write_result.schema.json"
)
MEMORY_ID_CODEC = RoutedResultCodec.text(schema_id="wechat_memory_write.result.v2")
MEMORY_WRITE_CAPABILITIES = frozenset(
    {"structured_output", "mcp:memory_connector:memory_write"}
)


class CodexMemoryWriteBackend:
    def __init__(
        self,
        workspace: Path,
        store,
        codex_bin: str = "codex",
        routed_execution: RoutedCodexExecution | None = None,
        timeout_seconds: int = 1200,
        idle_timeout_seconds: int = 900,
    ):
        self.routed_execution = (
            routed_execution
            or build_production_routed_codex_execution(
                store=store,
                workspace=workspace,
                codex_bin=codex_bin,
                total_timeout_seconds=timeout_seconds,
                idle_timeout_seconds=idle_timeout_seconds,
            )
        )

    def write(
        self,
        candidate_id: int,
        statement: str,
        *,
        source_time_start: str,
        source_time_end: str,
    ) -> str:
        created_at = source_time_start or source_time_end
        prompt = (
            "Use the available memory connector to persist the approved statement. "
            "Return the final typed result with status, memory_id, retryable, "
            "source_code, and detail. Preserve any provider error code and diagnostic "
            "in source_code and detail.\n"
            + json.dumps(
                {
                    "data": statement,
                    "type": "text",
                    "created_at": created_at,
                },
                ensure_ascii=False,
            )
        )
        try:
            result = self.routed_execution.execute(
                workload_kind="memory",
                workload_key=f"wechat_memory_candidate:{candidate_id}",
                prompt=prompt,
                command_factory=CodexCommandFactory.standard(
                    developer_instructions=(
                        "Persist the approved statement using the runtime capabilities. "
                        "Return success with the provider's stable memory identifier, "
                        "or failed with retryable, the original provider code, and a "
                        "bounded diagnostic."
                    ),
                    output_schema_path=WRITE_SCHEMA_PATH,
                ),
                parser=_memory_write_typed_result_json,
                result_codec=MEMORY_ID_CODEC,
                required_capabilities=MEMORY_WRITE_CAPABILITIES,
            )
        except RoutedCodexExecutionError as exc:
            raise _failure_from_routed_error(exc) from exc
        return self._memory_id_from_typed_output(result.value)

    @staticmethod
    def _memory_id_from_typed_output(raw: str) -> str:
        return memory_result_from_typed_output(raw).episode_uuid


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
                candidate_id,
                row["edited_statement"],
                source_time_start=row["source_time_start"],
                source_time_end=row["source_time_end"],
            )
        except Exception as exc:
            self.store.finish_wechat_memory_candidate_write(
                candidate_id, status="failed", error=str(exc)
            )
            raise
        self.store.finish_wechat_memory_candidate_write(
            candidate_id, status="written", memory_id=memory_id
        )
        return memory_id
