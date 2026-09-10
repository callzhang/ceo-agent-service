import json
from dataclasses import dataclass, field
from itertools import zip_longest
from pathlib import Path

from pydantic import ValidationError

from app.agent_result import agent_message_json_objects
from app.agent_envelope import AgentEnvelope
from app.agent_runtime_router import (
    CodexCommandFactory,
    RoutedCodexExecution,
    RoutedResultCodec,
    RoutedResultValidationError,
    RoutedResultValidationRetry,
)
from app.codex_decision import (
    extract_codex_audit_events,
    extract_codex_audit_events_from_session,
)
from app.codex_runner import _codex_home
from app.routed_result_privacy import audit_references_from_full_events

STRUCTURED_RUNTIME_CAPABILITIES = frozenset(
    {
        "structured_output",
        "local_schema_validation",
    }
)
STRUCTURED_RESULT_CODEC = RoutedResultCodec.text(
    schema_id="structured_agent.result.v1"
)
# Rendered from the model so the correction prompt cannot drift from the
# envelope contract (kinds, response modes, sensitivity kinds, action types).
AGENT_ENVELOPE_PROMPT_SCHEMA = json.dumps(
    AgentEnvelope.model_json_schema(), ensure_ascii=False, indent=2
)
AGENT_ENVELOPE_SCHEMA_PROBLEM_LIMIT = 8


class SkillLoadError(RuntimeError):
    pass


def load_skill_text(paths: list[Path]) -> str:
    sections: list[str] = []
    for path in paths:
        if not path.exists():
            raise SkillLoadError(f"missing skill file: {path}")
        if not path.is_file():
            raise SkillLoadError(f"skill path is not a file: {path}")
        sections.append(path.read_text(encoding="utf-8").strip())
    return "\n\n".join(section for section in sections if section)


@dataclass(frozen=True)
class AgentSpec:
    name: str
    schema_path: Path
    primary_skill_paths: list[Path] = field(default_factory=list)
    reply_visible_skill_paths: list[Path] = field(default_factory=list)
    developer_preamble: str = ""
    output_schema_path: Path | None = None

    def developer_instructions(self) -> str:
        if not self.schema_path.exists():
            raise SkillLoadError(f"missing schema file: {self.schema_path}")
        if self.output_schema_path is not None and not self.output_schema_path.exists():
            raise SkillLoadError(
                f"missing output schema file: {self.output_schema_path}"
            )
        skill_text = load_skill_text(
            [*self.primary_skill_paths, *self.reply_visible_skill_paths]
        )
        parts = [
            self.developer_preamble.strip(),
            f"# Agent spec\n\nname: {self.name}",
            skill_text,
        ]
        return "\n\n".join(part for part in parts if part)


@dataclass(frozen=True)
class StructuredAgentRun:
    envelope: AgentEnvelope
    codex_session_id: str
    transcript_start_line: int
    transcript_end_line: int
    audit_tool_events: list[dict[str, str]]


class StructuredCodexRunner:
    def __init__(
        self,
        *,
        routed_execution: RoutedCodexExecution,
        spec: AgentSpec,
    ):
        self.routed_execution = routed_execution
        self.spec = spec

    def run(
        self,
        request_id: int,
        conversation_id: str,
        conversation_title: str,
        single_chat: bool,
        prompt: str,
        *,
        owner: str,
    ) -> StructuredAgentRun:
        if request_id <= 0:
            raise ValueError("request_id must be positive")
        result = self.routed_execution.execute(
            workload_kind="structured",
            workload_key=str(request_id),
            prompt=prompt,
            command_factory=CodexCommandFactory.standard(
                developer_instructions=self.spec.developer_instructions(),
                output_schema_path=(
                    self.spec.output_schema_path or self.spec.schema_path
                ),
                use_output_schema=True,
            ),
            parser=_encode_structured_result,
            result_codec=STRUCTURED_RESULT_CODEC,
            conversation_id=conversation_id,
            required_capabilities=STRUCTURED_RUNTIME_CAPABILITIES,
            result_validation_retry=(
                RoutedResultValidationRetry.same_session_exactly_once(
                    correction_prompt=_agent_envelope_repair_prompt
                )
            ),
        )
        payload = json.loads(result.value)
        envelope = AgentEnvelope.model_validate(payload["envelope"])
        audit_tool_events = self._audit_tool_events(
            raw_events=payload["audit_tool_events"],
            session_id=result.session_id,
            start_line=result.transcript_start,
            end_line=result.transcript_end,
        )
        return StructuredAgentRun(
            envelope=envelope,
            codex_session_id=result.session_id,
            transcript_start_line=result.transcript_start,
            transcript_end_line=result.transcript_end,
            audit_tool_events=audit_tool_events,
        )

    @staticmethod
    def _audit_tool_events(
        *,
        raw_events: list[dict[str, str]],
        session_id: str,
        start_line: int,
        end_line: int,
    ) -> list[dict[str, str]]:
        session_events = []
        if session_id and end_line >= start_line:
            session_events = extract_codex_audit_events_from_session(
                session_id,
                codex_home=_codex_home(),
                start_line=start_line,
                end_line=end_line,
            )
        return session_events or raw_events


def _encode_structured_result(raw: str) -> str:
    try:
        envelope = parse_agent_envelope(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        raise RoutedResultValidationError(
            "invalid AgentEnvelope JSON", raw_output=raw
        ) from exc
    envelope_payload = envelope.model_dump(mode="json")
    envelope_payload["audit"]["documents"] = []
    return json.dumps(
        {
            "envelope": envelope_payload,
            "audit_tool_events": audit_references_from_full_events(
                extract_codex_audit_events(raw),
                limit=40,
            ),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def parse_agent_envelope(raw: str) -> AgentEnvelope:
    # Codex may print non-JSON lines (warnings, MCP start-up noise) around the
    # JSONL stream; only the JSON records carry the envelope.
    payloads: list[object] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            payloads.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    if not payloads:
        raise ValueError("no valid AgentEnvelope found")
    for payload in reversed(payloads):
        if isinstance(payload, dict):
            if "kind" in payload and "user_response" in payload:
                return AgentEnvelope.model_validate(payload)
            item = payload.get("item")
            item_text = _agent_message_text(item)
            if item_text is not None:
                return _parse_agent_envelope_text(item_text)
            message = payload.get("message")
            if isinstance(message, str) and "{" in message:
                return _parse_agent_envelope_text(message)
    raise ValueError("no valid AgentEnvelope found")


def _parse_agent_envelope_text(text: str) -> AgentEnvelope:
    """Parse the envelope out of one agent message, fences and prose included."""
    candidates = agent_message_json_objects(text)
    if not candidates:
        raise ValueError("agent message does not contain a JSON object")
    failure: Exception | None = None
    for payload in reversed(candidates):
        try:
            return _parse_agent_envelope_payload(payload)
        except (ValueError, ValidationError) as exc:
            failure = failure or exc
    assert failure is not None
    raise failure


def _agent_message_text(item: object) -> str | None:
    if not isinstance(item, dict):
        return None
    text = item.get("text")
    if isinstance(text, str):
        return text
    content = item.get("content")
    if not isinstance(content, list):
        return None
    for part in reversed(content):
        if isinstance(part, dict) and isinstance(part.get("text"), str):
            return part["text"]
    return None


def _parse_agent_envelope_payload(payload: object) -> AgentEnvelope:
    if not isinstance(payload, dict):
        raise ValueError("AgentEnvelope payload must be an object")
    return AgentEnvelope.model_validate(payload)


def _is_codex_session_refresh_error(message: str) -> bool:
    normalized = message.casefold()
    return (
        "failed to refresh token" in normalized
        or "your session has ended" in normalized
    )


def _agent_envelope_repair_prompt(raw: str) -> str:
    """Tell the model which schema rules its last envelope broke."""
    problems = _agent_envelope_schema_problems(raw)
    detail = (
        "\n".join(f"- {problem}" for problem in problems)
        if problems
        else "- the reply did not contain a valid AgentEnvelope JSON object"
    )
    return (
        "上一次输出不是合法 AgentEnvelope JSON。请基于同一个上下文重新输出合法 "
        "AgentEnvelope JSON，只输出 JSON，不要调用工具，不要发送消息，不要执行任何外部动作。\n\n"
        f"上一次输出的问题：\n{detail}\n\n"
        "AgentEnvelope Pydantic JSON schema:\n"
        f"{AGENT_ENVELOPE_PROMPT_SCHEMA}"
    )


def _agent_envelope_schema_problems(raw: str) -> list[str]:
    """List why the parser rejected the last candidate, by field path, never its text."""
    try:
        parse_agent_envelope(raw)
    except ValidationError as exc:
        # A made-up system action fails against every union member (one error
        # per member field); taking problems field by field in turns keeps the
        # other top-level fields inside the cap.
        by_field: dict[str, list[str]] = {}
        for error in exc.errors():
            field_name = str(error["loc"][0]) if error["loc"] else ""
            problem = _schema_problem(error["loc"], error["msg"])
            if problem not in by_field.setdefault(field_name, []):
                by_field[field_name].append(problem)
        in_turns = [
            problem
            for round_ in zip_longest(*by_field.values())
            for problem in round_
            if problem is not None
        ]
        return in_turns[:AGENT_ENVELOPE_SCHEMA_PROBLEM_LIMIT]
    except ValueError as exc:
        return [str(exc)]
    # The router builds this prompt after claiming the correction attempt, so
    # a raw that parses here must degrade to the fixed line, never raise.
    return []


def _schema_problem(loc: tuple[int | str, ...], message: str) -> str:
    path = ""
    for part in loc:
        if isinstance(part, int):
            path += "[]"
        else:
            path = f"{path}.{part}" if path else str(part)
    # Model-level validators report an empty location.
    return f"{path}: {message}" if path else message
