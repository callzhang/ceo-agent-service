from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    ValidationError,
    field_validator,
    model_validator,
)

from app.agent_result import (
    ResultParseError,
    agent_message_json_objects,
    parse_typed_agent_result,
)
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
# The health probe deliberately makes no MCP calls, so MCP tool availability
# cannot be a route-selection requirement. The typed write result below is the
# actual connector verification and carries retryable provider failures.
MEMORY_WRITE_CAPABILITIES = frozenset({"structured_output"})


@dataclass(frozen=True)
class MemoryWriteResult:
    episode_uuid: str
    processing_status: str
    duplicate: bool


class MemoryWriteTypedResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["success", "failed"]
    memory_id: str | None = None
    retryable: bool
    source_code: str | None = None
    detail: str

    @field_validator("detail", mode="before")
    @classmethod
    def normalize_structured_success_detail(cls, value: object) -> object:
        """Keep a successful provider receipt independent of its optional detail.

        ``memory_id`` and ``status`` determine whether the write completed. The
        model is instructed to emit a string detail, but current Codex sessions
        can carry the connector's structured success receipt in that optional
        field. Do not retain that raw receipt in the runtime result; it is not
        needed to confirm a successful Memory write.
        """
        if value is None:
            return ""
        if isinstance(value, (dict, list)):
            return "provider returned structured success detail"
        return value

    @model_validator(mode="after")
    def validate_status_fields(self) -> Self:
        if self.status == "success":
            if not str(self.memory_id or "").strip():
                raise ValueError("successful memory write requires memory_id")
            if self.retryable:
                raise ValueError("successful memory write cannot be retryable")
        elif str(self.memory_id or "").strip():
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
    result = execute_codex_memory_write(
        workspace=workspace,
        store=store,
        workload_key=f"memory_write_event:{event_id}",
        data=data,
        type=type,
        created_at=created_at,
        source_description=source_description,
        codex_bin=codex_bin,
        routed_execution=routed_execution,
        timeout_seconds=timeout_seconds,
        idle_timeout_seconds=idle_timeout_seconds,
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


def execute_codex_memory_write(
    *,
    workspace: Path,
    store: AutoReplyStore,
    workload_key: str,
    data: str,
    type: Literal["text", "message"],
    created_at: str,
    source_description: str,
    codex_bin: str = "codex",
    routed_execution: RoutedCodexExecution | None = None,
    timeout_seconds: int = 1200,
    idle_timeout_seconds: int = 900,
) -> MemoryWriteResult:
    """Persist one stable Memory payload through the routed runtime."""
    source_description = source_description.strip()
    if not source_description:
        raise ValueError("memory write source_description is required")
    routed_execution = routed_execution or build_production_routed_codex_execution(
        store=store,
        workspace=workspace,
        codex_bin=codex_bin,
        total_timeout_seconds=timeout_seconds,
        idle_timeout_seconds=idle_timeout_seconds,
    )
    source_description_literal = json.dumps(source_description, ensure_ascii=False)
    prompt = (
        "Use the available memory connector to persist the following input. "
        "Call memory_write exactly once with source_description set to the exact "
        f"literal {source_description_literal}. "
        "This field is the visible Memory title; do not replace it with an ID, "
        "a source label, or inferred provenance. "
        "Return the final typed result with status, memory_id, retryable, "
        "source_code, and detail. Preserve any provider error code and diagnostic "
        "in source_code and detail. detail must always be a JSON string; for a "
        "successful write use an empty string or a short plain-text confirmation, "
        "never the provider result object.\n"
        + json.dumps(
            {"data": data, "type": type, "created_at": created_at},
            ensure_ascii=False,
        )
    )
    try:
        routed_result = routed_execution.execute(
            workload_kind="memory",
            workload_key=workload_key,
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
    return memory_result_from_typed_output(routed_result.value)


def memory_result_from_typed_output(raw: str) -> MemoryWriteResult:
    typed = parse_memory_write_typed_result(raw)
    if typed.status == "failed":
        source_code = str(typed.source_code or "").strip()
        detail = str(typed.detail or "").strip()
        raise CodexMemoryWriteFailed(
            detail or source_code or "memory write failed",
            source_code=source_code or "memory_write_failed",
            retryable=typed.retryable,
        )
    return MemoryWriteResult(
        episode_uuid=str(typed.memory_id).strip(),
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
                candidates.extend(agent_message_json_objects(text))
    for candidate in reversed(candidates):
        try:
            return MemoryWriteTypedResult.model_validate(candidate)
        except ValidationError:
            continue
    try:
        return parse_typed_agent_result(raw, MemoryWriteTypedResult)
    except ResultParseError:
        pass
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
