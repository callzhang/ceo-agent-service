import json
from enum import StrEnum
from typing import Iterable

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class AgentOutcome(StrEnum):
    COMPLETED = "completed"
    NO_ACTION = "no_action"
    NEEDS_HUMAN = "needs_human"
    FAILED = "failed"


class SideEffectState(StrEnum):
    NONE = "none"
    CONFIRMED = "confirmed"
    UNKNOWN = "unknown"


class AgentError(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    code: str = ""
    retryable: bool = False
    authorization_required: bool = False
    side_effect_state: SideEffectState = SideEffectState.NONE


class AgentResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    outcome: AgentOutcome
    summary: str = Field(min_length=1)
    error: AgentError = Field(default_factory=AgentError)


class EffectKind(StrEnum):
    READ_ONLY = "read_only"
    EFFECTFUL = "effectful"


class EffectEventStatus(StrEnum):
    STARTED = "started"
    COMPLETED = "completed"


class ToolEffectEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    event_id: str = Field(min_length=1)
    effect: EffectKind
    status: EffectEventStatus


class ExecutionReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    receipt_id: str = Field(min_length=1)
    operation_id: str = Field(min_length=1)
    completed: bool
    persisted: bool
    safe_to_confirm: bool


class ResultParseError(ValueError):
    pass


class InconsistentAgentResultError(ValueError):
    def __init__(self, evidence_state: SideEffectState) -> None:
        self.evidence_state = evidence_state
        super().__init__(
            "completed result with confirmed side effect has no completed "
            "effectful event or safe persisted receipt"
        )


def parse_agent_result(raw: str) -> AgentResult:
    payloads = _parse_jsonl_payloads(raw)
    for payload in reversed(payloads):
        candidate = _agent_message_candidate(payload)
        if candidate is None:
            continue
        try:
            normalized = _normalize_result_text(candidate)
            return AgentResult.model_validate_json(normalized)
        except (ResultParseError, ValidationError) as exc:
            raise ResultParseError(
                "latest agent result candidate is malformed or does not match the strict schema"
            ) from exc
    raise ResultParseError("no valid AgentResult JSON found in Codex JSONL")


def validate_completion_evidence(
    result: AgentResult,
    *,
    events: Iterable[ToolEffectEvent],
    receipts: Iterable[ExecutionReceipt] = (),
) -> SideEffectState:
    evidence_state = _completion_evidence_state(events=events, receipts=receipts)
    if (
        result.outcome is AgentOutcome.COMPLETED
        and result.error.side_effect_state is SideEffectState.CONFIRMED
        and evidence_state is not SideEffectState.CONFIRMED
    ):
        raise InconsistentAgentResultError(evidence_state)
    return evidence_state


def _completion_evidence_state(
    *,
    events: Iterable[ToolEffectEvent],
    receipts: Iterable[ExecutionReceipt],
) -> SideEffectState:
    event_list = tuple(events)
    receipt_list = tuple(receipts)
    effectful_started = {
        event.event_id
        for event in event_list
        if event.effect is EffectKind.EFFECTFUL
        and event.status is EffectEventStatus.STARTED
    }
    effectful_completed = {
        event.event_id
        for event in event_list
        if event.effect is EffectKind.EFFECTFUL
        and event.status is EffectEventStatus.COMPLETED
    }
    receipt_completed = {
        receipt.operation_id
        for receipt in receipt_list
        if receipt.completed and receipt.persisted and receipt.safe_to_confirm
    }
    completed_operations = effectful_completed | receipt_completed

    # Lifecycle evidence is set-based: duplicates and event order do not matter.
    # Every started effect must close under the same stable operation ID.
    if effectful_started - completed_operations:
        return SideEffectState.UNKNOWN
    if completed_operations:
        return SideEffectState.CONFIRMED
    return SideEffectState.NONE


def _parse_jsonl_payloads(raw: str) -> list[dict]:
    payloads = []
    for line in raw.splitlines():
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            payloads.append(payload)
    return payloads


def _agent_message_candidate(payload: dict) -> str | None:
    item = payload.get("item")
    if isinstance(item, dict) and item.get("type") == "agent_message":
        for key in ("text", "message"):
            candidate = item.get(key)
            if isinstance(candidate, str):
                return candidate
    last_agent_message = payload.get("last_agent_message")
    if isinstance(last_agent_message, str):
        return last_agent_message
    message = payload.get("message")
    payload_type = payload.get("type")
    if isinstance(message, str) and payload_type in (None, "agent_message", "task_complete"):
        return message
    return None


def _normalize_result_text(text: str) -> str:
    return _first_balanced_json_object(_strip_json_fence(text.strip()))


def _strip_json_fence(text: str) -> str:
    if not text.startswith("```"):
        return text
    content = text[3:]
    if content.startswith("json"):
        content = content[4:]
    content = content.lstrip("\r\n")
    if content.rstrip().endswith("```"):
        content = content.rstrip()[:-3]
    return content.strip()


def _first_balanced_json_object(text: str) -> str:
    start = text.find("{")
    if start < 0:
        raise ResultParseError("agent message does not contain a JSON object")
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        character = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    raise ResultParseError("agent message contains an unbalanced JSON object")
