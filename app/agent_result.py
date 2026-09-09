import json
from enum import StrEnum
from typing import TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError


def _strict_agent_error_json_schema(schema: dict[str, object]) -> None:
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return
    for property_schema in properties.values():
        if isinstance(property_schema, dict):
            property_schema.pop("default", None)
    schema["required"] = list(properties)


class AgentError(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        json_schema_extra=_strict_agent_error_json_schema,
    )

    code: str = ""
    retryable: bool = False
    authorization_required: bool = False
    # Optional execution diagnostics.  These describe where a failure came
    # from without replacing the provider's original code with a generic
    # application error.
    stage: str = ""
    source: str = ""
    source_code: str = ""
    session_continuable: bool = False
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


ResultModelT = TypeVar("ResultModelT", bound=BaseModel)


def parse_typed_agent_result(
    raw: str,
    model_type: type[ResultModelT],
) -> ResultModelT:
    payloads = _primary_turn_payloads(_parse_jsonl_payloads(raw))
    schema_error: ValidationError | None = None
    for payload in reversed(payloads):
        candidate = _agent_message_candidate(payload)
        if candidate is None:
            continue
        try:
            normalized = _normalize_result_text(candidate)
            return model_type.model_validate_json(normalized)
        except ResultParseError:
            continue
        except ValidationError as exc:
            # Codex can emit more than one assistant message in a turn. A later
            # malformed candidate must not hide an earlier valid typed result.
            # A JSON object that exists but violates the typed contract is
            # remembered so the caller can report where it failed instead of
            # claiming that no result was returned at all.
            if schema_error is None and not _is_json_syntax_error(exc):
                schema_error = exc
            continue
    if schema_error is not None:
        raise ResultParseError(
            "typed result JSON failed schema validation"
        ) from schema_error
    raise ResultParseError("no valid typed result JSON found in Codex JSONL")


def _is_json_syntax_error(exc: ValidationError) -> bool:
    return all(error.get("type") == "json_invalid" for error in exc.errors())


def parse_agent_text_result(raw: str) -> str:
    """Return the final non-empty assistant message from a Codex JSONL turn."""
    payloads = _primary_turn_payloads(_parse_jsonl_payloads(raw))
    for payload in reversed(payloads):
        candidate = _agent_message_candidate(payload)
        if candidate is not None and candidate.strip():
            return candidate
    raise ResultParseError("no agent text result found in Codex JSONL")


def _primary_turn_payloads(payloads: list[dict]) -> list[dict]:
    start = next(
        (
            index
            for index, payload in enumerate(payloads)
            if payload.get("type") == "turn.started"
        ),
        None,
    )
    if start is None:
        return payloads
    end = next(
        (
            index + 1
            for index, payload in enumerate(payloads[start:], start=start)
            if payload.get("type") in {"turn.completed", "turn.failed"}
        ),
        len(payloads),
    )
    return payloads[start:end]


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
    cleaned = _strip_json_fence(text.strip())
    if '\"authored_judgment\":' in cleaned and not cleaned.rstrip().endswith("}"):
        cleaned += "}"
    repaired = _remove_top_level_stray_array_close(cleaned)
    candidate = _first_balanced_json_object(repaired)
    remainder = repaired[len(candidate) :].lstrip()
    if remainder.startswith(',"authored_judgment"'):
        # The legacy envelope omitted the outer closing brace, so the first
        # balanced object ends at the proposal object. Move the trailing
        # judgment into that proposal before closing both objects.
        try:
            candidate_payload = json.loads(candidate)
        except json.JSONDecodeError:
            candidate_payload = {}
        if isinstance(candidate_payload, dict) and "sourced_facts" in candidate_payload:
            candidate = candidate[:-1] + remainder
            if not remainder.endswith("}"):
                candidate += "}"
        else:
            trailing_root = remainder.endswith("}")
            authored = remainder[:-1] if trailing_root else remainder
            candidate = candidate[:-1] + authored + "}}"
    # Some Codex turns close the proposal object and then emit one redundant
    # array terminator before the next top-level field. Accept only that exact
    # wire-format defect; the repaired result still has to satisfy the strict
    # typed schema at the caller.
    return _remove_top_level_stray_array_close(candidate)


def _remove_top_level_stray_array_close(text: str) -> str:
    depth = 0
    in_string = False
    escaped = False
    result: list[str] = []
    for index, character in enumerate(text):
        if in_string:
            result.append(character)
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
            result.append(character)
            continue
        if character == "}":
            depth -= 1
            result.append(character)
            continue
        if character == "]" and depth == 1:
            previous = text[index - 1] if index else ""
            remainder = text[index + 1 :].lstrip()
            next_field = remainder[1:].lstrip() if remainder.startswith(",") else ""
            if previous == "}" and next_field.startswith(
                ('"error"', '"sourced_facts"')
            ):
                continue
        if character == "{":
            depth += 1
        result.append(character)
    return "".join(result)


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
