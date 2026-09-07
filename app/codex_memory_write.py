from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from app.agent_runtime_production import build_production_routed_codex_execution
from app.agent_runtime_router import (
    CodexCommandFactory,
    RoutedCodexExecution,
    RoutedCodexExecutionError,
    RoutedResultCodec,
)
from app.codex_decision import _decision_text_candidates, _iter_json_payloads
from app.store import AutoReplyStore

WRITE_SCHEMA_PATH = (
    Path(__file__).resolve().parent
    / "schemas"
    / "wechat_memory_write_result.schema.json"
)
MEMORY_WRITE_RESULT_CODEC = RoutedResultCodec.text(schema_id="memory_write.result.v2")
MEMORY_WRITE_CAPABILITIES = frozenset(
    {"structured_output", "mcp:memory_connector:memory_write"}
)


@dataclass(frozen=True)
class MemoryWriteResult:
    episode_uuid: str
    processing_status: str
    duplicate: bool


class MemoryWriteTypedResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["success", "failed"]
    memory_id: str
    retryable: bool
    source_code: str
    detail: str

    @model_validator(mode="after")
    def validate_status_fields(self) -> Self:
        if self.status == "success":
            if not self.memory_id.strip():
                raise ValueError("successful memory write requires memory_id")
            if self.retryable or self.source_code.strip() or self.detail.strip():
                raise ValueError("successful memory write cannot contain failure fields")
        elif self.memory_id.strip():
            raise ValueError("failed memory write cannot contain memory_id")
        return self


class CodexMemoryWriteFailed(RuntimeError):
    def __init__(self, detail: str, *, source_code: str, retryable: bool) -> None:
        self.source_code = source_code
        self.retryable = retryable
        super().__init__(detail)


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
        "Use the available memory connector to persist the following input. "
        "Return the final typed result with status, memory_id, retryable, "
        "source_code, and detail. Preserve any provider error code and diagnostic "
        "in source_code and detail.\n"
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
            command_factory=CodexCommandFactory.standard(
                developer_instructions=(
                    "Persist the requested memory using the runtime capabilities. "
                    "Return success with the provider's stable memory identifier, or "
                    "failed with retryable, the original provider code, and a bounded "
                    "diagnostic."
                ),
                output_schema_path=WRITE_SCHEMA_PATH,
            ),
            parser=_memory_write_typed_result_json,
            result_codec=MEMORY_WRITE_RESULT_CODEC,
            required_capabilities=MEMORY_WRITE_CAPABILITIES,
        )
    except RoutedCodexExecutionError as exc:
        raise _failure_from_routed_error(exc) from exc
    result = memory_result_from_typed_output(routed_result.value)
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


def memory_result_from_typed_output(raw: str) -> MemoryWriteResult:
    typed = parse_memory_write_typed_result(raw)
    if typed.status == "failed":
        raise CodexMemoryWriteFailed(
            typed.detail.strip() or typed.source_code.strip() or "memory write failed",
            source_code=typed.source_code.strip(),
            retryable=typed.retryable,
        )
    return MemoryWriteResult(
        episode_uuid=typed.memory_id.strip(),
        processing_status="completed",
        duplicate=False,
    )


def parse_memory_write_typed_result(raw: str) -> MemoryWriteTypedResult:
    candidates: list[Any] = []
    for payload in _iter_json_payloads(raw):
        candidates.append(payload)
        if isinstance(payload, dict):
            for text in _decision_text_candidates(payload):
                candidates.extend(_iter_json_payloads(text))
    for candidate in reversed(candidates):
        try:
            return MemoryWriteTypedResult.model_validate(candidate)
        except ValidationError:
            continue
    raise ValueError("memory write returned no valid typed result")


def _memory_write_typed_result_json(raw: str) -> str:
    return parse_memory_write_typed_result(raw).model_dump_json()


def _failure_from_routed_error(exc: RoutedCodexExecutionError) -> CodexMemoryWriteFailed:
    source_code = (exc.failure_code or exc.code or "runtime_execution_failed").strip()
    detail = (exc.reason or source_code).strip()
    return CodexMemoryWriteFailed(
        detail,
        source_code=source_code,
        retryable=bool(exc.retryable_external_dependency),
    )
