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
    model_config = ConfigDict(extra="forbid")

    code: str = ""
    retryable: bool = False
    authorization_required: bool = False
    side_effect_state: SideEffectState = SideEffectState.NONE


class AgentResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

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
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1)
    effect: EffectKind
    status: EffectEventStatus


class ExecutionReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    receipt_id: str = Field(min_length=1)
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
    validation_error: ValidationError | None = None
    for payload in reversed(payloads):
        for candidate in _agent_message_candidates(payload):
            try:
                result_payload = json.loads(_normalize_result_text(candidate))
                return AgentResult.model_validate(result_payload)
            except (json.JSONDecodeError, ResultParseError):
                continue
            except ValidationError as exc:
                validation_error = exc
                continue
    if validation_error is not None:
        raise ResultParseError("agent result does not match the strict schema") from validation_error
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
    if any(
        event.effect is EffectKind.EFFECTFUL
        and event.status is EffectEventStatus.COMPLETED
        for event in event_list
    ) or any(
        receipt.completed and receipt.persisted and receipt.safe_to_confirm
        for receipt in receipt_list
    ):
        return SideEffectState.CONFIRMED
    if any(
        event.effect is EffectKind.EFFECTFUL
        and event.status is EffectEventStatus.STARTED
        for event in event_list
    ):
        return SideEffectState.UNKNOWN
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


def _agent_message_candidates(payload: dict) -> tuple[str, ...]:
    candidates = []
    item = payload.get("item")
    if isinstance(item, dict) and item.get("type") == "agent_message":
        for key in ("text", "message"):
            candidate = item.get(key)
            if isinstance(candidate, str):
                candidates.append(candidate)
    for key in ("message", "last_agent_message"):
        candidate = payload.get(key)
        if isinstance(candidate, str):
            candidates.append(candidate)
    return tuple(candidates)


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
