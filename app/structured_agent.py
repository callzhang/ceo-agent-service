import json
from dataclasses import dataclass, field
from pathlib import Path

from app.agent_envelope import AgentEnvelope
from app.agent_runtime_router import (
    ApprovedCodexCommandFactory,
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
        "consumer_read_only_enforcement",
        "reviewed_read_tools",
    }
)
STRUCTURED_RESULT_CODEC = RoutedResultCodec.text(
    schema_id="structured_agent.result.v1"
)


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
        allow_side_effects: bool = False,
    ) -> StructuredAgentRun:
        if request_id <= 0:
            raise ValueError("request_id must be positive")
        if allow_side_effects:
            raise ValueError("structured routed execution is read-only")
        result = self.routed_execution.execute(
            workload_kind="structured",
            workload_key=str(request_id),
            prompt=prompt,
            command_factory=ApprovedCodexCommandFactory.read_only_structured(
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
    payloads = [json.loads(line) for line in raw.splitlines() if line.strip()]
    for payload in reversed(payloads):
        if isinstance(payload, dict):
            shorthand = _no_reply_shorthand_envelope(payload)
            if shorthand is not None:
                return shorthand
            if "kind" in payload and "user_response" in payload:
                return AgentEnvelope.model_validate(payload)
            item = payload.get("item")
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                return _parse_agent_envelope_payload(json.loads(item["text"]))
            message = payload.get("message")
            if isinstance(message, str) and message.strip().startswith("{"):
                return _parse_agent_envelope_payload(json.loads(message))
    raise ValueError("no valid AgentEnvelope found")


def _no_reply_shorthand_envelope(payload: dict) -> AgentEnvelope | None:
    """Accept only the action-free no-reply shorthand emitted by some Codex turns."""
    if set(payload) - {"mode", "audit_summary"}:
        return None
    if payload.get("mode") != "no_reply":
        return None
    summary = payload.get("audit_summary", "Agent selected no reply.")
    if not isinstance(summary, str) or not summary.strip():
        return None
    return AgentEnvelope.model_validate(
        {
            "kind": "no_action",
            "user_response": {
                "mode": "no_reply",
                "text": "",
                "sensitivity_kind": "general",
            },
            "system_actions": [],
            "domain_payload": {},
            "audit": {
                "summary": summary.strip(),
                "documents": [],
                "confidence": 0.5,
            },
        }
    )


def _parse_agent_envelope_payload(payload: object) -> AgentEnvelope:
    if not isinstance(payload, dict):
        raise ValueError("AgentEnvelope payload must be an object")
    shorthand = _no_reply_shorthand_envelope(payload)
    if shorthand is not None:
        return shorthand
    if "kind" in payload and "user_response" in payload:
        return AgentEnvelope.model_validate(_normalize_agent_envelope_payload(payload))
    if payload.get("kind") == "okr_review" and isinstance(payload.get("result"), dict):
        request_id = payload.get("request_id")
        if not isinstance(request_id, int):
            raise ValueError("legacy okr_review payload requires integer request_id")
        return AgentEnvelope.model_validate(
            {
                "kind": "okr_review",
                "user_response": {
                    "mode": "send_reply",
                    "text": "OKR review completed.",
                    "sensitivity_kind": "internal_personnel",
                },
                "system_actions": [
                    {"type": "persist_okr_review", "request_id": request_id}
                ],
                "domain_payload": payload["result"],
                "audit": {
                    "summary": str(
                        payload.get("audit_summary")
                        or payload["result"].get("summary")
                        or "OKR review completed."
                    ),
                    "documents": [],
                    "confidence": 0.7,
                },
            }
        )
    return AgentEnvelope.model_validate(payload)


def _normalize_agent_envelope_payload(payload: dict) -> dict:
    if payload.get("kind") != "okr_review":
        return payload
    audit = payload.get("audit")
    if not isinstance(audit, dict):
        return payload
    if all(key in audit for key in ("summary", "documents", "confidence")):
        return payload
    domain_payload = payload.get("domain_payload")
    domain_summary = (
        domain_payload.get("summary")
        if isinstance(domain_payload, dict)
        and isinstance(domain_payload.get("summary"), str)
        else ""
    )
    summary = audit.get("summary") or audit.get("method") or domain_summary
    if not isinstance(summary, str) or not summary.strip():
        summary = "OKR review completed."
    return {
        **payload,
        "audit": {
            "summary": summary.strip(),
            "documents": [],
            "confidence": 0.7,
        },
    }


def _is_codex_session_refresh_error(message: str) -> bool:
    normalized = message.casefold()
    return (
        "failed to refresh token" in normalized
        or "your session has ended" in normalized
    )


def _agent_envelope_repair_prompt(raw: str) -> str:
    excerpt = raw.strip()
    if len(excerpt) > 4000:
        excerpt = excerpt[:4000] + "\n...[truncated]"
    return (
        "上一次输出不是合法 AgentEnvelope JSON。请基于同一个上下文重新输出合法 "
        "AgentEnvelope JSON，只输出 JSON，不要调用工具，不要发送消息，不要执行任何外部动作。\n\n"
        "上一次输出摘录：\n"
        f"{excerpt}"
    )
