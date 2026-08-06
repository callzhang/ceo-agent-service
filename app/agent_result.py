import json
from enum import StrEnum
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pydantic.json_schema import SkipJsonSchema


def _strict_agent_error_json_schema(schema: dict[str, object]) -> None:
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return
    for property_schema in properties.values():
        if isinstance(property_schema, dict):
            property_schema.pop("default", None)
    schema["required"] = list(properties)


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
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        json_schema_extra=_strict_agent_error_json_schema,
    )

    code: str = ""
    retryable: bool = False
    authorization_required: bool = False
    side_effect_state: SkipJsonSchema[SideEffectState] = Field(
        default=SideEffectState.NONE,
        exclude=True,
    )


class OaActionReceipt(BaseModel):
    """A verified OA action that must remain attached to its approval history."""

    model_config = ConfigDict(extra="forbid", strict=True)

    process_instance_id: str = Field(min_length=1)
    task_id: str = ""
    action: str = Field(min_length=1)
    remark: str = ""
    result: dict[str, object] = Field(default_factory=dict)


class AgentResult(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        json_schema_extra={"required": ["outcome", "summary", "error"]},
    )

    outcome: AgentOutcome
    summary: str = Field(min_length=1)
    error: AgentError = Field(default_factory=AgentError)
    oa_action_receipt: OaActionReceipt | None = None


class EffectKind(StrEnum):
    READ_ONLY = "read_only"
    EFFECTFUL = "effectful"
    UNREVIEWED = "unreviewed"


class EffectEventStatus(StrEnum):
    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"


class ToolEffectEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    call_id: str = Field(min_length=1)
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


def _parse_jsonl_payloads(raw: str) -> list[dict]:
    payloads = []
    seen_json_record = False
    for line_number, line in enumerate(raw.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            if seen_json_record:
                raise ResultParseError(
                    f"Codex JSONL record is malformed at line {line_number}"
                ) from exc
            continue
        seen_json_record = True
        if isinstance(payload, dict):
            payloads.append(payload)
    return payloads


def _agent_message_candidate(payload: dict) -> str | None:
    response_item = payload.get("payload")
    if (
        payload.get("type") == "response_item"
        and isinstance(response_item, dict)
        and response_item.get("type") == "message"
        and response_item.get("role") == "assistant"
    ):
        content = response_item.get("content")
        if isinstance(content, list):
            for block in reversed(content):
                if (
                    isinstance(block, dict)
                    and block.get("type") == "output_text"
                    and isinstance(block.get("text"), str)
                ):
                    return block["text"]
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
