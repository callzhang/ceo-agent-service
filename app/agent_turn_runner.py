from __future__ import annotations

import hashlib
import json
import logging
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Generic, TypeVar, cast

from pydantic import ValidationError

from app.agent_contracts import (
    AuditAgentResult,
    AuditOutcome,
    ConsumerAgentResult,
    ConsumerOutcome,
)
from app.agent_effects import (
    IDLE_TIMEOUT_SECONDS,
    LEASE_SECONDS,
    TOTAL_TIMEOUT_SECONDS,
    _is_signed_url,
)
from app.agent_result import ResultParseError
from app.agent_runtime_config import AgentRuntimeConfig, load_runtime_config
from app.agent_runtime_contracts import (
    CredentialMode,
    RuntimeFailureClass,
    RuntimeKind,
    RuntimeRoute,
)
from app.agent_runtime_router import AgentRuntimeRouter, route_unavailable_code
from app.claude_runtime_adapter import (
    ClaudeEventNormalizer,
    ClaudeRuntimeAdapter,
    ClaudeCommandPolicy,
    require_claude_session_id,
)
from app.codex_capacity import (
    CODEX_PROVIDER_CAPACITY_EXHAUSTED,
    codex_provider_failure_code,
    is_codex_capacity_exhausted,
)
from app.codex_failure import (
    CODEX_PROVIDER_AUTH_FAILED,
    CODEX_PROVIDER_OVERLOADED,
    CODEX_PROVIDER_UNAVAILABLE,
    classify_codex_process_failure,
)
from app.wechat.codex_safety import disable_automatic_review
from app.codex_history import (
    count_codex_session_lines,
    extract_codex_assistant_messages_from_session,
)
from app.codex_runner import _codex_home
from app.codex_runtime_adapter import CodexRuntimeAdapter
from app.friday_runtime_adapter import FridayRuntimeAdapter, FridayRuntimeError
from app.agent_runtime_router import _runtime_failure_from_friday_error
from app.config import feedback_spike_vercel_base_url
from app.feedback_spike import sanitize_configured_feedback_links
from app.leak_check import (
    contains_credential,
    contains_local_runtime_leak,
    is_sensitive_credential_name,
    redact_credentials,
    redact_forbidden_leak_markers,
)
from app.process_runner import ProcessRunResult, run_process_with_idle_timeout
from app.store import (
    AgentRole,
    AgentRun,
    AgentRuntimeAttempt,
    AgentRuntimeAttemptStartConflictError,
    AutoReplyStore,
    ReplyTask,
    RuntimeAttemptSessionMode,
)

_LOGGER = logging.getLogger(__name__)

ResultT = TypeVar("ResultT")
ProcessExecutor = Callable[..., ProcessRunResult]
CLAUDE_INPUT_MAX_BYTES = 1024 * 1024
_COMMON_RUNTIME_CAPABILITIES = frozenset(
    {"structured_output", "local_schema_validation"}
)
_RUNTIME_DOMAIN_RESULT_CODEC_VERSION = 1
_RUNTIME_DOMAIN_RESULT_CODEC_MAX_BYTES = 32 * 1024
_RUNTIME_RESULT_SUMMARY_MAX_CHARS = 2048


def _normalized_key(key: str) -> str:
    return "".join(character for character in key.casefold() if character.isalnum())


_RUNTIME_RESULT_FORBIDDEN_DOCUMENT_FIELDS = frozenset(
    {
        "documentbody",
        "documentcontent",
        "fulltext",
        "rawoutput",
        "rawresult",
        "responsebody",
        "stderr",
        "stdout",
        "transcript",
    }
)

class RuntimeRouteUnavailableError(RuntimeError):
    """No configured runtime route can serve this turn."""

    code = "runtime_execution_failed"

    def __init__(
        self,
        reason: str,
        *,
        ineligible_routes: tuple[tuple[str, str], ...] = (),
    ) -> None:
        self.reason = reason
        self.code = route_unavailable_code(ineligible_routes)
        super().__init__(self.code)


class _RecoveredCompletedRuntimeResult(RuntimeError):
    """Internal control flow for a validated durable provider result."""


class CompletedRuntimeResultBlockedError(ValueError):
    """A durable result cannot be trusted and must not trigger provider replay."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class RuntimeResultValidationError(ValueError):
    """A typed result contains data outside the durable runtime contract."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class _DecodedRuntimeDomainResult:
    result: ConsumerAgentResult | AuditAgentResult


def _bounded_runtime_result_text(value: str, *, field: str, limit: int) -> str:
    if not isinstance(value, str) or not value or len(value) > limit:
        raise ValueError(f"runtime_result_envelope_{field}_invalid")
    return value


def _project_runtime_external_reference(
    reference: dict[str, object],
) -> dict[str, object]:
    # This is an opaque provider result, not an application-defined evidence
    # schema. Generic size and secret checks run on the complete typed result.
    return dict(reference)


def _project_runtime_domain_result(
    result: ConsumerAgentResult | AuditAgentResult,
) -> dict[str, object]:
    summary = _bounded_runtime_result_text(
        result.summary,
        field="summary",
        limit=_RUNTIME_RESULT_SUMMARY_MAX_CHARS,
    )
    if isinstance(result, ConsumerAgentResult):
        # Consumer proposals are already strict typed business values. Project
        # fields explicitly so future model additions cannot silently enter the
        # durable recovery envelope.
        proposal = None
        if result.proposal is not None:
            proposal = {
                "objective": result.proposal.objective,
                "actions": [
                    {
                        "description": action.description,
                        "action_identity": action.action_identity,
                        "capability": action.capability,
                        "operation": action.operation,
                        "target": action.target,
                        "payload": action.payload,
                    }
                    for action in result.proposal.actions
                ],
                "sourced_facts": [
                    {
                        "assertion": fact.assertion,
                        "references": list(fact.references),
                    }
                    for fact in result.proposal.sourced_facts
                ],
                "authored_judgment": result.proposal.authored_judgment,
            }
        return {
            "outcome": result.outcome.value,
            "summary": summary,
            "proposal": proposal,
            "decision_options": [
                option.model_dump(mode="json") for option in result.decision_options
            ],
            "error": result.error.model_dump(mode="json"),
        }
    external_result = None
    if result.external_result is not None:
        external_result = {
            "operation_id": _bounded_runtime_result_text(
                result.external_result.operation_id,
                field="operation_id",
                limit=512,
            ),
            "live_result_reference": _project_runtime_external_reference(
                result.external_result.live_result_reference
            ),
        }
    return {
        "outcome": result.outcome.value,
        "summary": summary,
        "proposal_revision": result.proposal_revision,
        "feedback": (
            result.feedback.model_dump(mode="json")
            if result.feedback is not None
            else None
        ),
        "external_result": external_result,
        "decision_options": [
            option.model_dump(mode="json") for option in result.decision_options
        ],
        "error": result.error.model_dump(mode="json"),
    }


def _reject_runtime_document_fields(value: object) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if _normalized_key(str(key)) in _RUNTIME_RESULT_FORBIDDEN_DOCUMENT_FIELDS:
                raise ValueError("runtime_result_envelope_document_field_invalid")
            _reject_runtime_document_fields(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            _reject_runtime_document_fields(nested)


def _encode_runtime_domain_result(
    *,
    schema_id: str,
    role: AgentRole,
    result: ConsumerAgentResult | AuditAgentResult,
    result_reference_run_id: int | None = None,
) -> str:
    envelope = {
        "schema_id": schema_id,
        "version": _RUNTIME_DOMAIN_RESULT_CODEC_VERSION,
        "role": role.value,
    }
    if result_reference_run_id is None:
        projected_result = _project_runtime_domain_result(result)
        _reject_runtime_document_fields(projected_result)
        envelope["result"] = projected_result
    else:
        if (
            role is not AgentRole.CONSUMER
            or result.outcome is ConsumerOutcome.FAILED
            or type(result_reference_run_id) is not int
            or result_reference_run_id <= 0
        ):
            raise ValueError("runtime_result_reference_invalid")
        domain_text = json.dumps(
            result.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        envelope["result_ref"] = {
            "agent_run_id": result_reference_run_id,
            "result_sha256": hashlib.sha256(domain_text.encode("utf-8")).hexdigest(),
        }
    if _contains_sensitive_value(envelope):
        raise ValueError("runtime_result_envelope_contains_sensitive_value")
    encoded = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
    try:
        encoded_size = len(encoded.encode("utf-8"))
    except (UnicodeError, MemoryError) as exc:
        raise ValueError("runtime_result_envelope_invalid_utf8") from exc
    if encoded_size > _RUNTIME_DOMAIN_RESULT_CODEC_MAX_BYTES:
        raise ValueError("runtime_result_envelope_too_large")
    if contains_local_runtime_leak(encoded):
        raise ValueError("runtime_result_envelope_contains_local_path")
    return encoded


def _decode_runtime_domain_result(
    encoded: str,
    *,
    schema_id: str,
    role: AgentRole,
    referenced_agent_run_id: int | None = None,
    referenced_result_json: str = "",
) -> _DecodedRuntimeDomainResult:
    try:
        if len(encoded.encode("utf-8")) > _RUNTIME_DOMAIN_RESULT_CODEC_MAX_BYTES:
            raise ValueError("runtime_result_envelope_too_large")
        envelope = json.loads(encoded)
    except (json.JSONDecodeError, UnicodeError, MemoryError) as exc:
        raise ValueError("runtime_result_envelope_invalid") from exc
    expected_keys = {
        "schema_id",
        "version",
        "role",
        "result",
    }
    reference_keys = expected_keys - {"result"} | {"result_ref"}
    if (
        not isinstance(envelope, dict)
        or frozenset(envelope)
        not in {frozenset(expected_keys), frozenset(reference_keys)}
        or envelope.get("schema_id") != schema_id
        or type(envelope.get("version")) is not int
        or envelope.get("version") != _RUNTIME_DOMAIN_RESULT_CODEC_VERSION
        or envelope.get("role") != role.value
        or _contains_sensitive_value(envelope)
        or contains_local_runtime_leak(encoded)
    ):
        raise ValueError("runtime_result_envelope_invalid")
    result_value = envelope.get("result")
    if "result_ref" in envelope:
        reference = envelope.get("result_ref")
        if (
            role is not AgentRole.CONSUMER
            or not isinstance(reference, dict)
            or set(reference) != {"agent_run_id", "result_sha256"}
            or type(reference.get("agent_run_id")) is not int
            or reference["agent_run_id"] <= 0
            or reference["agent_run_id"] != referenced_agent_run_id
            or not isinstance(reference.get("result_sha256"), str)
            or len(reference["result_sha256"]) != 64
            or not referenced_result_json
            or hashlib.sha256(referenced_result_json.encode("utf-8")).hexdigest()
            != reference["result_sha256"]
        ):
            raise ValueError("runtime_result_envelope_invalid")
        try:
            result_value = json.loads(referenced_result_json)
        except json.JSONDecodeError as exc:
            raise ValueError("runtime_result_envelope_invalid") from exc
    if not isinstance(result_value, dict):
        raise ValueError("runtime_result_envelope_invalid")
    model = ConsumerAgentResult if role is AgentRole.CONSUMER else AuditAgentResult
    try:
        result = model.model_validate(result_value)
        if "result" in envelope:
            projected_result = _project_runtime_domain_result(result)
            _reject_runtime_document_fields(projected_result)
            if projected_result != envelope["result"]:
                raise ValueError("runtime_result_envelope_projection_mismatch")
        elif result.outcome is ConsumerOutcome.FAILED:
            raise ValueError("runtime_result_reference_invalid")
        return _DecodedRuntimeDomainResult(result=result)
    except (ValidationError, ValueError) as exc:
        raise ValueError("runtime_result_envelope_invalid") from exc


def _required_runtime_capabilities(
    *,
    run: AgentRun,
    expected_actions: tuple[dict[str, object], ...],
    explicit_capabilities: frozenset[str] = frozenset(),
) -> frozenset[str]:
    del run, expected_actions, explicit_capabilities
    # Route selection is infrastructure-only. Business capabilities, command
    # names, tools, and Skill availability are resolved inside the selected
    # Agent runtime and must not become application routing gates.
    return _COMMON_RUNTIME_CAPABILITIES


@dataclass(frozen=True)
class AgentTurnRunResult(Generic[ResultT]):
    run_id: int
    result: ResultT
    transcript_start_line: int
    transcript_end_line: int


def _process_failure_code(process: ProcessRunResult) -> str:
    code = classify_codex_process_failure(process.stdout, process.stderr)
    if code == CODEX_PROVIDER_AUTH_FAILED:
        return f"{code}: native Codex CLI authentication is unavailable"
    if code == CODEX_PROVIDER_UNAVAILABLE:
        provider_code = codex_provider_failure_code(
            f"{process.stdout}\n{process.stderr}"
        )
        if provider_code == CODEX_PROVIDER_CAPACITY_EXHAUSTED:
            return provider_code
    if is_codex_capacity_exhausted(f"{process.stdout}\n{process.stderr}"):
        return CODEX_PROVIDER_CAPACITY_EXHAUSTED
    return code


def _agent_process_error_code(exc: Exception) -> str:
    code = str(exc).strip()
    explicit_code = getattr(exc, "code", "")
    if isinstance(explicit_code, str) and explicit_code.startswith("runtime_"):
        return explicit_code
    if code.startswith(CODEX_PROVIDER_AUTH_FAILED):
        return code
    if code in {
        CODEX_PROVIDER_UNAVAILABLE,
        CODEX_PROVIDER_CAPACITY_EXHAUSTED,
        CODEX_PROVIDER_OVERLOADED,
    }:
        return code
    if isinstance(exc, ResultParseError):
        if code == "no valid typed result JSON found in Codex JSONL":
            return "codex_result_missing"
        return "codex_result_invalid"
    return "codex_process_failed"


def _runtime_failure_detail(exc: Exception) -> str:
    """Persist a bounded, redacted explanation alongside the failure code.

    Routed execution errors may wrap the concrete parser/validation exception
    as ``__cause__`` and expose a more useful ``reason`` attribute. Preserve
    those details so the run record is actionable instead of only repeating
    its top-level error code.
    """
    parts: list[str] = []
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen and len(parts) < 8:
        seen.add(id(current))
        for attribute in ("reason", "detail"):
            value = getattr(current, attribute, "")
            if isinstance(value, str):
                value = " ".join(value.split())
                if value and value not in parts:
                    parts.append(value)
        value = " ".join(str(current).split())
        if value and value not in parts:
            parts.append(value)
        current = current.__cause__ or current.__context__

    detail = " | ".join(parts)
    if not detail:
        return ""
    detail = redact_credentials(detail)
    detail = redact_forbidden_leak_markers(detail)
    return detail[:1000]


RESULT_INVALID_ERROR_CODE = "codex_result_invalid"
RESULT_MISSING_ERROR_CODE = "codex_result_missing"


def result_correction_prompt(
    store: AutoReplyStore,
    task: ReplyTask,
    *,
    role: AgentRole,
    proposal_revision: int,
) -> str:
    """Return the correction block for a role retry after an unusable result.

    The retry re-enters the same typed contract, so the model is told what was
    wrong with its previous result (no JSON object at all, or the fields that
    failed validation) instead of receiving the identical prompt again and
    repeating the same wire defect.
    """
    failed_runs = [
        run
        for run in store.list_agent_runs_for_task_generation(
            task.id,
            task.execution_generation,
        )
        if run.role is role
        and run.proposal_revision == proposal_revision
        and run.status == "failed"
    ]
    if not failed_runs:
        return ""
    latest = max(failed_runs, key=lambda run: (run.turn_attempt, run.id))
    try:
        error = json.loads(latest.structured_error_json or "{}")
    except json.JSONDecodeError:
        return ""
    if not isinstance(error, dict):
        return ""
    code = error.get("code")
    if code == RESULT_MISSING_ERROR_CODE:
        problem = "上一轮没有返回任何 JSON 对象，只有说明文字"
    elif code == RESULT_INVALID_ERROR_CODE:
        locations = str(error.get("detail") or "").strip() or "result"
        problem = f"上一轮返回的结果未通过 wire schema 校验：{locations}"
    else:
        return ""
    return (
        "\n\n## Result Correction\n"
        f"{problem}。请只返回一个修正后的、严格匹配 schema 的 JSON 对象，"
        "不要重新开始新的业务判断。"
    )


def _result_parse_error_detail(exc: ResultParseError) -> str:
    """Keep validation locations, never the model output that failed validation."""
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, ValidationError):
            fields = []
            for error in current.errors():
                location = (
                    ".".join(str(part) for part in error.get("loc", ())) or "result"
                )
                kind = str(error.get("type") or "validation_error")
                fields.append(f"{location}: {kind}")
            if fields:
                return "; ".join(fields[:8])
        current = current.__cause__ or current.__context__
    return str(exc)[:240]


def _is_terminal_codex_auth_failure(code: str) -> bool:
    return code.startswith(CODEX_PROVIDER_AUTH_FAILED)


def _claude_input_contract(*, prompt: str, developer_instructions: str) -> str:
    payload = (
        "<developer-instructions>\n"
        f"{developer_instructions}\n"
        "</developer-instructions>\n"
        "<task>\n"
        f"{prompt}\n"
        "</task>"
    )
    if len(payload.encode("utf-8")) > CLAUDE_INPUT_MAX_BYTES:
        raise RuntimeRouteUnavailableError("claude_input_contract_too_large")
    return payload


def _execution_mode_environment(
    values: Mapping[str, str] | None,
) -> dict[str, str]:
    normalized = dict(values or {})
    allowed = {"CEO_DRY_RUN", "CEO_NOT_SEND_MESSAGE"}
    if set(normalized) - allowed or any(
        value not in {"0", "1"} for value in normalized.values()
    ):
        raise ValueError("execution mode environment is invalid")
    return normalized


class AgentTurnProcess(Generic[ResultT]):
    def _claude_provider_policy(self) -> ClaudeCommandPolicy:
        """Use the provider default; application Audit does not review tools."""
        return ClaudeCommandPolicy.normal()

    def __init__(
        self,
        *,
        store: AutoReplyStore,
        task: ReplyTask,
        workspace: Path,
        owner: str,
        executor: ProcessExecutor | None = None,
        codex_bin: str = "codex",
        runtime_config: AgentRuntimeConfig | None = None,
        runtime_router: AgentRuntimeRouter | None = None,
        codex_adapter: CodexRuntimeAdapter | None = None,
        claude_adapter: ClaudeRuntimeAdapter | None = None,
        friday_adapter: FridayRuntimeAdapter | None = None,
        refresh_runtime_capabilities: Callable[[], object] | None = None,
        forced_runtime_route: RuntimeRoute | None = None,
        reasoning_effort: str = "",
        execution_mode_environment: Mapping[str, str] | None = None,
    ) -> None:
        self.store = store
        self.task = task
        self.owner = owner
        self.runtime_config = runtime_config or load_runtime_config(os.environ)
        self.codex_adapter = codex_adapter or CodexRuntimeAdapter(
            workspace, self.runtime_config, codex_bin=codex_bin
        )
        self.workspace = workspace
        self.claude_adapter = claude_adapter
        self.friday_adapter = friday_adapter
        self._allow_legacy_oauth_bootstrap = runtime_router is None
        self.runtime_router = runtime_router or AgentRuntimeRouter(
            routes=self.runtime_config.routes,
            store=store,
            snapshots={},
        )
        self.executor = executor or run_process_with_idle_timeout
        self.refresh_runtime_capabilities = refresh_runtime_capabilities
        self.forced_runtime_route = forced_runtime_route
        self.reasoning_effort = reasoning_effort
        self.execution_mode_environment = _execution_mode_environment(
            execution_mode_environment
        )

    def execute(
        self,
        *,
        run: AgentRun,
        prompt: str,
        session_id: str | None,
        developer_instructions: str,
        configure_command: Callable[[list[str]], None],
        parse_result: Callable[[str], ResultT],
        persist_conversation_session: bool,
        prepare_result: Callable[[ResultT], ResultT] | None = None,
        expected_actions: tuple[dict[str, object], ...] = (),
        on_progress: Callable[[], None] | None = None,
        image_paths: list[Path] | None = None,
        required_capabilities: frozenset[str] = frozenset(),
        conversation_contract_hash: str = "",
        force_new_session: bool = False,
    ) -> AgentTurnRunResult[ResultT]:
        line_count = 0
        saw_json = False
        primary_turn_started = False
        primary_turn_closed = False
        observed_session_id = ""
        active_attempt: AgentRuntimeAttempt | None = None
        active_route: RuntimeRoute | None = None
        session_transcript_end = 0
        claude_normalizer: ClaudeEventNormalizer | None = None
        pending_claude_session_id = ""
        recovered_completed_attempt = False
        transcript_start = run.transcript_start_line

        def persist_effect_event(
            payload: dict[str, object],
            *,
            from_session_replay: bool = False,
            from_claude_normalizer: bool = False,
        ) -> None:
            """Append provider events; runtime owns command permissions."""
            del from_session_replay, from_claude_normalizer
            # Provider events are evidence, not an application policy input.
            # Preserve the provider payload and let the selected Skill/runtime
            # own command, tool, receipt, and readback semantics.
            event = _persist_provider_event(payload)
            if event is not None:
                self.store.append_agent_run_event(run.id, event, owner=self.owner)

        def persist_payload(
            payload: dict[str, object],
            *,
            trusted_claude_session_id: str = "",
            from_claude_normalizer: bool = False,
        ) -> None:
            nonlocal observed_session_id, active_attempt
            nonlocal primary_turn_started, primary_turn_closed
            payload_type = payload.get("type")
            if primary_turn_closed:
                return
            if payload_type == "turn.started":
                primary_turn_started = True
            new_session = trusted_claude_session_id or _session_id(payload)
            if new_session:
                observed_session_id = new_session
                is_claude = (
                    active_route is not None
                    and active_route.runtime_kind is RuntimeKind.CLAUDE_CLI
                )
                if active_attempt is not None and not is_claude:
                    active_attempt = self.store.set_agent_runtime_attempt_session(
                        active_attempt.id, new_session
                    )
                if active_route is not None and active_route.name == "codex_oauth":
                    self.store.set_agent_run_session(
                        run.id,
                        new_session,
                        owner=self.owner,
                        transcript_start_line=run.transcript_start_line,
                        allow_consumer_session_handoff=(run.role is AgentRole.CONSUMER),
                    )
                if (
                    not is_claude
                    and run.role is AgentRole.CONSUMER
                    and active_route is not None
                ):
                    self.store.upsert_conversation_runtime_session(
                        self.task.conversation_id,
                        active_route.name,
                        new_session,
                        conversation_contract_hash,
                    )
                if not is_claude and (
                    persist_conversation_session
                    and run.role is AgentRole.CONSUMER
                    and active_route is not None
                    and active_route.name == "codex_oauth"
                ):
                    self.store.upsert_conversation(
                        self.task.conversation_id,
                        self.task.conversation_title,
                        self.task.single_chat,
                        new_session,
                    )
            persist_effect_event(
                payload,
                from_claude_normalizer=from_claude_normalizer,
            )
            if primary_turn_started and payload_type in {
                "turn.completed",
                "turn.failed",
            }:
                primary_turn_closed = True

        def persist_line(line: str) -> None:
            nonlocal line_count, saw_json
            if not line.strip():
                return
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                if saw_json:
                    raise RuntimeError("codex_stream_invalid") from exc
                return
            saw_json = True
            if not isinstance(payload, dict):
                raise RuntimeError("codex_stream_invalid")
            line_count += 1
            self.store.renew_agent_run_lease(
                run.id, owner=self.owner, lease_seconds=LEASE_SECONDS
            )
            if on_progress is not None:
                on_progress()
            if (
                active_route is not None
                and active_route.runtime_kind is RuntimeKind.CLAUDE_CLI
            ):
                if claude_normalizer is None:
                    raise RuntimeError("claude_event_normalizer_missing")
                normalized_events = claude_normalizer.normalize_events(payload)
                for event in normalized_events:
                    persist_payload(
                        event,
                        trusted_claude_session_id=(
                            claude_normalizer.session_id or ""
                            if event.get("type") == "turn.started"
                            else ""
                        ),
                        from_claude_normalizer=True,
                    )
                return
            persist_payload(payload)

        def stabilize_and_replay_session(
            session_for_receipts: str, *, session_start: int
        ) -> int:
            del session_start
            return count_codex_session_lines(
                session_for_receipts, codex_home=_codex_home()
            )

        def parse_claude_result(raw: str) -> ResultT:
            return parse_result(
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {"type": "agent_message", "text": raw},
                    },
                    separators=(",", ":"),
                )
            )

        required_capabilities = _required_runtime_capabilities(
            run=run,
            expected_actions=expected_actions,
            explicit_capabilities=required_capabilities,
        )
        execution_contract = {
            "version": 1,
            "role": run.role.value,
            "operation_id": run.operation_id,
            "conversation_contract_hash": conversation_contract_hash,
            "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "developer_instructions_sha256": hashlib.sha256(
                developer_instructions.encode("utf-8")
            ).hexdigest(),
            "required_capabilities": sorted(required_capabilities),
            "expected_actions_sha256": hashlib.sha256(
                json.dumps(
                    expected_actions,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest(),
        }
        execution_contract_digest = hashlib.sha256(
            json.dumps(
                execution_contract, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()
        runtime_result_schema_id = hashlib.sha256(
            ("agent_turn_claude_result_v1\0" + execution_contract_digest).encode(
                "utf-8"
            )
        ).hexdigest()

        try:
            runtime_attempts = self.store.list_agent_runtime_attempts(run.id)
            completed_attempt = next(
                (
                    attempt
                    for attempt in reversed(runtime_attempts)
                    if attempt.status == "completed"
                    and attempt.result_schema_id == runtime_result_schema_id
                    and attempt.result_envelope_json
                ),
                None,
            )
            if (
                completed_attempt is None
                and run.role is AgentRole.CONSUMER
                and run.status == "completed"
                and any(
                    attempt.status == "completed"
                    and attempt.result_envelope_json
                    and attempt.runtime_kind == RuntimeKind.CLAUDE_CLI.value
                    for attempt in runtime_attempts
                )
            ):
                raise CompletedRuntimeResultBlockedError(
                    "completed_runtime_result_contract_mismatch"
                )
            if completed_attempt is not None:
                route = next(
                    (
                        candidate
                        for candidate in self.runtime_config.routes
                        if candidate.name == completed_attempt.route_name
                        and candidate.runtime_kind is RuntimeKind.CLAUDE_CLI
                    ),
                    None,
                )
                if route is None:
                    raise RuntimeError("completed runtime result route mismatch")
                try:
                    decoded = _decode_runtime_domain_result(
                        completed_attempt.result_envelope_json,
                        schema_id=runtime_result_schema_id,
                        role=run.role,
                        referenced_agent_run_id=run.id,
                        referenced_result_json=run.final_result_json,
                    )
                    result = cast(ResultT, decoded.result)
                except ValueError as exc:
                    raise CompletedRuntimeResultBlockedError(
                        "completed_runtime_result_invalid"
                    ) from exc
                if prepare_result is not None:
                    result = prepare_result(result)
                    _validate_runtime_reference_domain_result(
                        cast(ConsumerAgentResult | AuditAgentResult, result),
                        allow_configured_feedback_links=True,
                    )
                else:
                    if _contains_sensitive_value(result.model_dump(mode="json")):
                        raise ValueError("agent_result_contains_sensitive_value")
                    if (
                        run.role is AgentRole.CONSUMER
                        and result.outcome is not ConsumerOutcome.FAILED
                    ):
                        _validate_runtime_reference_domain_result(result)
                active_attempt = completed_attempt
                observed_session_id = completed_attempt.session_id
                pending_claude_session_id = completed_attempt.session_id
                attempt_transcript_start = completed_attempt.transcript_start
                attempt_line_start = 0
                line_count = (
                    completed_attempt.transcript_end
                    - completed_attempt.transcript_start
                )
                session_transcript_end = completed_attempt.transcript_end
                recovered_completed_attempt = True
                raise _RecoveredCompletedRuntimeResult
            if self.forced_runtime_route is not None:
                route = self.forced_runtime_route
            elif self.refresh_runtime_capabilities is not None:
                self.refresh_runtime_capabilities(force=False)
            attempted_routes = frozenset()
            configured_route_names = frozenset(
                candidate.name for candidate in self.runtime_config.routes
            )
            excluded_routes = (
                attempted_routes
                if attempted_routes and configured_route_names - attempted_routes
                else frozenset()
            )
            if self.forced_runtime_route is None:
                decision = self.runtime_router.first_route_decision(
                    required_capabilities=required_capabilities,
                    allow_legacy_oauth_bootstrap=self._allow_legacy_oauth_bootstrap,
                    excluded_routes=excluded_routes,
                )
                route = decision.route
            else:
                decision = None
            if route is None and self.forced_runtime_route is None:
                if self.refresh_runtime_capabilities is not None:
                    self.refresh_runtime_capabilities(force=True)
                    decision = self.runtime_router.first_route_decision(
                        required_capabilities=required_capabilities,
                        allow_legacy_oauth_bootstrap=(
                            self._allow_legacy_oauth_bootstrap
                        ),
                        excluded_routes=excluded_routes,
                    )
                    route = decision.route
            if route is None:
                assert decision is not None
                unavailable = RuntimeRouteUnavailableError(
                    decision.reason,
                    ineligible_routes=decision.ineligible_routes,
                )
                self._fail_running(run, unavailable.code, detail=decision.reason)
                raise unavailable
            route_session_id = self._session_for_route(
                route,
                role=run.role,
                requested_session_id=session_id,
                conversation_contract_hash=conversation_contract_hash,
                force_new_session=force_new_session,
            )
            attempt_is_preclaimed = False
            while True:
                saw_json = False
                primary_turn_started = False
                primary_turn_closed = False
                observed_session_id = ""
                attempt_transcript_reference = ""
                friday_failure = None
                active_route = route
                route_uses_codex_history = route.runtime_kind is RuntimeKind.CODEX_CLI
                executor_prompt = (
                    _claude_input_contract(
                        prompt=prompt,
                        developer_instructions=developer_instructions,
                    )
                    if route.runtime_kind is RuntimeKind.CLAUDE_CLI
                    else prompt
                )
                attempt_line_start = line_count
                if not attempt_is_preclaimed:
                    active_attempt = self._claim_and_start_attempt(
                        run,
                        route,
                        route_session_id,
                    )
                attempt_transcript_start = (
                    count_codex_session_lines(
                        route_session_id, codex_home=_codex_home()
                    )
                    if route_session_id and route_uses_codex_history
                    else 0
                )
                attempt_is_preclaimed = False
                if route.runtime_kind is RuntimeKind.CLAUDE_CLI:
                    claude_adapter = self._claude_adapter()
                    command = claude_adapter.build_command(
                        route=route,
                        session_id=route_session_id,
                        max_turns=1,
                    )
                    claude_normalizer = claude_adapter.new_event_normalizer(
                        expected_session_id=route_session_id,
                        command=command,
                    )
                    command_env = claude_adapter.build_env(route, command=command)
                elif route.runtime_kind is RuntimeKind.FRIDAY_RUNTIME:
                    if self.friday_adapter is None:
                        self.friday_adapter = FridayRuntimeAdapter(self.runtime_config)
                    claude_adapter = None
                    claude_normalizer = None
                    command = []
                    command_env = None
                else:
                    claude_adapter = None
                    claude_normalizer = None
                    command = self.codex_adapter.build_command(
                        route=route,
                        prompt=prompt,
                        session_id=route_session_id,
                        image_paths=image_paths,
                        output_schema_path=None,
                        use_output_schema=False,
                        approval_policy="on-failure",
                        developer_instructions=developer_instructions,
                        use_approval_bypass=True,
                        reasoning_effort=self.reasoning_effort or None,
                    )
                    configure_command(command)
                    if route.credential_mode is CredentialMode.SERVICE_API:
                        # The automatic reviewer model only exists on the
                        # OpenAI-hosted route; a service-API provider rejects it.
                        disable_automatic_review(command)
                    command_env = self.codex_adapter.build_env(route)
                if command_env is not None:
                    command_env.update(self.execution_mode_environment)
                try:
                    if route.runtime_kind is RuntimeKind.FRIDAY_RUNTIME:
                        friday_result = self.friday_adapter.execute(
                            prompt,
                            project_id=self.runtime_config.friday_runtime_project_id,
                            conversation_id=self.task.conversation_id,
                            model=route.model,
                            timeout_seconds=TOTAL_TIMEOUT_SECONDS,
                        )
                        observed_session_id = f"friday_thread:{friday_result.thread_id}"
                        attempt_transcript_reference = (
                            f"friday_operation:{friday_result.operation_id}"
                        )
                        active_attempt = self.store.set_agent_runtime_attempt_session(
                            active_attempt.id,
                            observed_session_id,
                            attempt_transcript_reference,
                        )
                        process = ProcessRunResult(
                            0,
                            json.dumps(
                                {
                                    "type": "item.completed",
                                    "item": {
                                        "type": "agent_message",
                                        "text": friday_result.text,
                                    },
                                }
                            ),
                            "",
                        )
                    else:
                        process = self.executor(
                            command,
                            prompt=executor_prompt,
                            env=command_env,
                            total_timeout_seconds=TOTAL_TIMEOUT_SECONDS,
                            idle_timeout_seconds=IDLE_TIMEOUT_SECONDS,
                            on_stdout_line=persist_line,
                        )
                except FridayRuntimeError as exc:
                    friday_failure = _runtime_failure_from_friday_error(exc)
                    if exc.thread_id:
                        observed_session_id = f"friday_thread:{exc.thread_id}"
                    if exc.operation_id:
                        attempt_transcript_reference = (
                            f"friday_operation:{exc.operation_id}"
                        )
                    if observed_session_id:
                        active_attempt = self.store.set_agent_runtime_attempt_session(
                            active_attempt.id,
                            observed_session_id,
                            attempt_transcript_reference,
                        )
                    process = ProcessRunResult(1, "", exc.detail)
                except Exception:
                    if claude_adapter is not None:
                        claude_adapter.finish_invocation(command)
                    self._fail_runtime_attempt_unclassified(active_attempt)
                    raise
                if process.returncode == 0 and not process.timed_out:
                    if claude_adapter is not None:
                        assert claude_normalizer is not None
                        claude_normalizer.finalize()
                        proof = claude_normalizer.terminal_proof()
                        result = claude_adapter.parse_final_result(
                            normalizer=claude_normalizer,
                            proof=proof,
                            parser=parse_claude_result,
                        )
                        trusted_session_id = claude_normalizer.session_id
                        if not trusted_session_id:
                            raise RuntimeError("claude_session_evidence_missing")
                        observed_session_id = trusted_session_id
                        pending_claude_session_id = trusted_session_id
                    else:
                        try:
                            result = parse_result(process.stdout)
                        except ResultParseError:
                            session_id_for_result = (
                                observed_session_id or route_session_id
                            )
                            if (
                                not session_id_for_result
                                or not route_uses_codex_history
                            ):
                                raise
                            session_result = (
                                extract_codex_assistant_messages_from_session(
                                    session_id_for_result,
                                    codex_home=_codex_home(),
                                    start_line=attempt_transcript_start,
                                )
                            )
                            if not session_result:
                                raise
                            result = parse_result(session_result)
                    break
                if claude_adapter is not None:
                    claude_adapter.finish_invocation(command)
                if friday_failure is not None:
                    failure = friday_failure
                else:
                    failure_adapter = claude_adapter or self.codex_adapter
                    failure = failure_adapter.classify_failure(
                        process.stdout,
                        process.stderr,
                        process.returncode,
                        timed_out=process.timed_out,
                        timeout_kind=process.timeout_kind,
                    )
                failed_session_id = observed_session_id or route_session_id or ""
                failed_transcript_end = max(
                    attempt_transcript_start + (line_count - attempt_line_start),
                    attempt_transcript_start,
                )
                if failed_session_id and route_uses_codex_history:
                    try:
                        failed_transcript_end = max(
                            failed_transcript_end,
                            stabilize_and_replay_session(
                                failed_session_id,
                                session_start=attempt_transcript_start,
                            ),
                        )
                    except Exception:
                        pass
                failed_attempt = self.store.fail_agent_runtime_attempt(
                    active_attempt.id,
                    failure.failure_class.value,
                    failure.code,
                    failure.failover_permitted,
                    session_id=failed_session_id,
                    transcript_reference=attempt_transcript_reference,
                    transcript_start=attempt_transcript_start,
                    transcript_end=failed_transcript_end,
                )
                if failure.route_pause_required:
                    self.store.open_runtime_route_pause(
                        route.name,
                        failure.code,
                        datetime.now(timezone.utc) + self.runtime_config.retry_delay,
                    )
                persisted = self.store.get_agent_run(run.id)
                assert persisted is not None
                self.store.renew_agent_run_lease(
                    run.id,
                    owner=self.owner,
                    lease_seconds=LEASE_SECONDS,
                )
                if self.forced_runtime_route is not None:
                    self._raise_for_process_failure(process, run=run)
                    raise AssertionError("unreachable forced runtime failure")
                decision = self.runtime_router.next_route(
                    run=persisted,
                    failed_attempt=failed_attempt,
                    failure=failure,
                    required_capabilities=required_capabilities,
                )
                if decision.route is None:
                    self._raise_for_process_failure(process, run=run)
                    raise AssertionError("unreachable process failure")
                route = decision.route
                if decision.fresh_session:
                    self._clear_incompatible_route_session_for_fresh_retry(
                        run=run,
                        route=route,
                        failed_attempt=failed_attempt,
                    )
                route_session_id = (
                    None
                    if decision.fresh_session
                    else self._session_for_route(
                        route,
                        role=run.role,
                        requested_session_id=session_id,
                        conversation_contract_hash=conversation_contract_hash,
                    )
                )
                successor = self._claim_and_start_attempt(
                    run,
                    route,
                    route_session_id,
                )
                self.store.mark_agent_runtime_attempt_superseded(failed_attempt.id)
                active_attempt = successor
                # The successor is durably claimed and process-start fenced while
                # this worker still owns the Agent run lease. The next iteration
                # must execute that exact row rather than claiming it again.
                attempt_is_preclaimed = True
            session_for_receipts = (
                observed_session_id or route_session_id or run.codex_session_id
            )
            if session_for_receipts and route.runtime_kind is RuntimeKind.CODEX_CLI:
                session_start = attempt_transcript_start
                session_transcript_end = stabilize_and_replay_session(
                    session_for_receipts,
                    session_start=session_start,
                )
            # Prepared results may legitimately contain the service's signed
            # feedback callbacks from an earlier revision. Validate those only
            # after preparation, where the exact configured pair is sanitized.
            if prepare_result is None and _contains_sensitive_value(
                result.model_dump(mode="json")
            ):
                raise ValueError("agent_result_contains_sensitive_value")
            if (
                route.runtime_kind is RuntimeKind.CLAUDE_CLI
                and run.role is AgentRole.CONSUMER
                and result.outcome is not ConsumerOutcome.FAILED
            ):
                _validate_runtime_reference_domain_result(result)
            if prepare_result is not None:
                result = prepare_result(result)
                _validate_runtime_reference_domain_result(
                    cast(ConsumerAgentResult | AuditAgentResult, result),
                    allow_configured_feedback_links=True,
                )
            persisted_attempt = (
                self.store.get_agent_runtime_attempt(active_attempt.id)
                if active_attempt is not None
                else None
            )
            if (
                persisted_attempt is not None
                and persisted_attempt.status == "running"
                and route.runtime_kind
                in {
                    RuntimeKind.CODEX_CLI,
                    RuntimeKind.FRIDAY_RUNTIME,
                }
            ):
                self.store.complete_agent_runtime_attempt(
                    persisted_attempt.id,
                    observed_session_id,
                    attempt_transcript_reference,
                    attempt_transcript_start,
                    max(
                        attempt_transcript_start + (line_count - attempt_line_start),
                        session_transcript_end,
                    ),
                )
        except _RecoveredCompletedRuntimeResult:
            pass
        except CompletedRuntimeResultBlockedError:
            raise
        except RuntimeRouteUnavailableError as exc:
            self._fail_running(
                run,
                exc.code,
                detail=exc.reason,
                stage="connect",
                source="runtime",
                source_code=exc.reason,
            )
            raise
        except ResultParseError as exc:
            self._fail_runtime_attempt_unclassified(active_attempt)
            parse_error_code = _agent_process_error_code(exc)
            self._fail_running(
                run,
                parse_error_code,
                detail=_result_parse_error_detail(exc),
                stage="result",
                source="codex",
                session_continuable=True,
            )
            raise
        except Exception as exc:
            self._fail_runtime_attempt_unclassified(active_attempt)
            provider_recovery = _agent_process_error_code(exc)
            code = provider_recovery
            self._fail_running(
                run,
                code,
                detail=_runtime_failure_detail(exc),
                stage="execution",
                source="codex",
                source_code=code,
                session_continuable=True,
            )
            if provider_recovery in {
                CODEX_PROVIDER_UNAVAILABLE,
                CODEX_PROVIDER_CAPACITY_EXHAUSTED,
                CODEX_PROVIDER_OVERLOADED,
            }:
                raise RuntimeError(code) from exc
            raise
        transcript_end = max(transcript_start + line_count, session_transcript_end)
        outcome = getattr(result, "outcome")
        persisted = self.store.get_agent_run(run.id)
        assert persisted is not None
        if run.role is AgentRole.AUDIT:
            result = self._bind_audit_result(run, result)
        claude_business_failure = outcome in {
            ConsumerOutcome.FAILED,
            AuditOutcome.FAILED,
        }
        if (
            route.runtime_kind is RuntimeKind.CLAUDE_CLI
            and not recovered_completed_attempt
            and claude_business_failure
        ):
            if active_attempt is None:
                raise AgentRuntimeAttemptStartConflictError(
                    "Claude runtime attempt is missing at business failure"
                )
            self.store.fail_agent_runtime_attempt(
                active_attempt.id,
                RuntimeFailureClass.RESULT.value,
                "runtime_business_result_failed",
                False,
            )
        elif (
            route.runtime_kind is RuntimeKind.CLAUDE_CLI
            and not recovered_completed_attempt
        ):
            if not pending_claude_session_id:
                raise RuntimeError("claude_session_evidence_missing")
            persisted_attempt = (
                self.store.get_agent_runtime_attempt(active_attempt.id)
                if active_attempt is not None
                else None
            )
            if persisted_attempt is None or persisted_attempt.status != "running":
                raise AgentRuntimeAttemptStartConflictError(
                    "Claude runtime attempt is not running at result commit"
                )
            domain_result = cast(
                ConsumerAgentResult | AuditAgentResult, result
            ).model_dump(mode="json")
            durable_consumer_result = (
                run.role is AgentRole.CONSUMER and outcome is not ConsumerOutcome.FAILED
            )
            self.store.complete_agent_runtime_attempt(
                persisted_attempt.id,
                pending_claude_session_id,
                "",
                attempt_transcript_start,
                attempt_transcript_start + (line_count - attempt_line_start),
                owner=self.owner,
                result_schema_id=runtime_result_schema_id,
                result_envelope_json=_encode_runtime_domain_result(
                    schema_id=runtime_result_schema_id,
                    role=run.role,
                    result=cast(ConsumerAgentResult | AuditAgentResult, result),
                    result_reference_run_id=(
                        run.id if durable_consumer_result else None
                    ),
                ),
                conversation_id=(
                    self.task.conversation_id if run.role is AgentRole.CONSUMER else ""
                ),
                route_name=route.name,
                conversation_contract_hash=conversation_contract_hash,
                agent_run_final_result=(
                    domain_result if durable_consumer_result else None
                ),
                agent_run_transcript_end=(
                    transcript_end if durable_consumer_result else None
                ),
            )
        if outcome in {ConsumerOutcome.FAILED, AuditOutcome.FAILED}:
            self.store.fail_agent_run(
                run.id,
                getattr(result, "error").model_dump(mode="json"),
                owner=self.owner,
                transcript_end_line=transcript_end,
            )
        else:
            self.store.complete_agent_run(
                run.id,
                result.model_dump(mode="json"),
                owner=self.owner,
                transcript_end_line=transcript_end,
            )
        completed = self.store.get_agent_run(run.id)
        assert completed is not None
        return AgentTurnRunResult(
            run_id=run.id,
            result=result,
            transcript_start_line=transcript_start,
            transcript_end_line=completed.transcript_end_line,
        )

    def _session_for_route(
        self,
        route: RuntimeRoute,
        *,
        role: AgentRole,
        requested_session_id: str | None,
        conversation_contract_hash: str = "",
        force_new_session: bool = False,
    ) -> str | None:
        if force_new_session and route.name != "codex_api":
            return None
        if role is AgentRole.AUDIT:
            # Audit retries are new immutable runs, but they continue the same
            # runtime conversation when that route owns the recorded session.
            # The proposal and operation identity stay fixed; opening a fresh
            # session would discard the failed turn's context and can repeat
            # feedback or an external action.
            return requested_session_id if route.name == "codex_oauth" else None
        persisted = self.store.get_conversation_runtime_session(
            self.task.conversation_id,
            route.name,
            required_contract_hash=conversation_contract_hash,
        )
        if persisted is not None and route.runtime_kind is RuntimeKind.CLAUDE_CLI:
            require_claude_session_id(persisted)
        if route.name == "codex_oauth":
            return (
                requested_session_id
                if requested_session_id and requested_session_id == persisted
                else persisted
            )
        return persisted

    def _claude_adapter(self) -> ClaudeRuntimeAdapter:
        if self.claude_adapter is None:
            self.claude_adapter = ClaudeRuntimeAdapter(
                workspace=self.workspace,
                config=self.runtime_config,
            )
        return self.claude_adapter

    def _clear_incompatible_route_session_for_fresh_retry(
        self,
        *,
        run: AgentRun,
        route: RuntimeRoute,
        failed_attempt: AgentRuntimeAttempt,
    ) -> None:
        persisted_attempt = self.store.get_agent_runtime_attempt(failed_attempt.id)
        if (
            run.role is not AgentRole.CONSUMER
            or persisted_attempt is None
            or persisted_attempt != failed_attempt
            or persisted_attempt.agent_run_id != run.id
            or persisted_attempt.route_name != route.name
            or persisted_attempt.status != "failed"
            or persisted_attempt.session_mode != RuntimeAttemptSessionMode.RESUME
            or persisted_attempt.failure_class != RuntimeFailureClass.SESSION.value
            or persisted_attempt.failure_code != "session_route_incompatible"
            or not persisted_attempt.source_session_id
        ):
            raise ValueError("fresh session retry lacks persisted resume evidence")
        self.store.clear_conversation_runtime_session_if_matches(
            self.task.conversation_id,
            route.name,
            persisted_attempt.source_session_id,
        )

    def _claim_and_start_attempt(
        self,
        run: AgentRun,
        route: RuntimeRoute,
        source_session_id: str | None,
    ) -> AgentRuntimeAttempt:
        attempt = self.store.claim_agent_runtime_attempt(
            run.id,
            route.name,
            route.runtime_kind.value,
            route.credential_mode.value,
            route.model,
            session_mode=(
                RuntimeAttemptSessionMode.RESUME
                if source_session_id
                else RuntimeAttemptSessionMode.FRESH
            ),
            source_session_id=source_session_id or "",
        )
        return self.store.mark_agent_runtime_attempt_running_once(attempt.id)

    def _fail_runtime_attempt_unclassified(
        self,
        attempt: AgentRuntimeAttempt | None,
    ) -> None:
        if attempt is None:
            return
        persisted = self.store.get_agent_runtime_attempt(attempt.id)
        if persisted is None or persisted.status not in {"starting", "running"}:
            return
        self.store.fail_agent_runtime_attempt(
            attempt.id,
            RuntimeFailureClass.UNCLASSIFIED.value,
            "runtime_unclassified",
            False,
        )

    def _bind_audit_result(
        self,
        run: AgentRun,
        result: ResultT,
    ) -> ResultT:
        """Bind the service-owned ``proposal_revision`` onto the Audit result.

        The Audit run row defines the reviewed revision; the model only echoes
        it (same rule as ``operation_id`` in ``audit_agent._parse_evidenced_result``).
        A differing echo is logged and overwritten, never a run failure.
        """
        echoed = getattr(result, "proposal_revision")
        if echoed == run.proposal_revision:
            return result
        _LOGGER.warning(
            "audit result proposal_revision bound from run: task=%s run=%s "
            "run_revision=%s echoed_revision=%s",
            self.task.id,
            run.id,
            run.proposal_revision,
            echoed,
        )
        return cast(
            ResultT,
            result.model_copy(update={"proposal_revision": run.proposal_revision}),
        )

    def _raise_for_process_failure(
        self, process: ProcessRunResult, *, run: AgentRun
    ) -> None:
        if process.timed_out:
            raise RuntimeError("codex_process_timeout")
        if process.returncode != 0:
            failure_code = _process_failure_code(process)
            persisted = self.store.get_agent_run(run.id)
            if (
                failure_code == "codex_process_failed"
                and run.role is AgentRole.CONSUMER
                and persisted is not None
                and not persisted.tool_events
                and _stream_has_no_agent_result(process.stdout)
            ):
                raise ResultParseError(
                    "no valid typed result JSON found in Codex JSONL"
                )
            raise RuntimeError(failure_code)

    def _fail_running(
        self,
        run: AgentRun,
        code: str,
        *,
        detail: str = "",
        stage: str = "",
        source: str = "",
        source_code: str = "",
        session_continuable: bool = False,
    ) -> None:
        persisted = self.store.get_agent_run(run.id)
        if persisted is not None and persisted.status == "running":
            terminal_auth_failure = _is_terminal_codex_auth_failure(code)
            self.store.fail_agent_run(
                run.id,
                {
                    "code": code,
                    "retryable": not terminal_auth_failure,
                    "authorization_required": False,
                    **({"detail": detail} if detail else {}),
                    **({"stage": stage} if stage else {}),
                    **({"source": source} if source else {}),
                    **({"source_code": source_code} if source_code else {}),
                    "session_continuable": session_continuable,
                },
                owner=self.owner,
            )


def _stream_has_no_agent_result(raw: str) -> bool:
    saw_json = False
    for line in raw.splitlines():
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        saw_json = True
        response_item = payload.get("payload")
        if (
            payload.get("type") == "response_item"
            and isinstance(response_item, dict)
            and response_item.get("type") == "message"
            and response_item.get("role") == "assistant"
        ):
            return False
        item = payload.get("item")
        if isinstance(item, dict) and item.get("type") == "agent_message":
            return False
        if isinstance(payload.get("last_agent_message"), str):
            return False
        if isinstance(payload.get("message"), str) and payload.get("type") in (
            None,
            "agent_message",
            "task_complete",
        ):
            return False
    return saw_json


def _persist_provider_event(payload: dict[str, object]) -> dict[str, object] | None:
    """Normalize only the provider envelope needed for durable evidence.

    Tool names, command strings, receipt fields, and effect classifications are
    intentionally opaque to the application.  A provider may expose the
    event body as ``item`` or ``payload``; both forms are retained verbatim.
    """
    event_type = payload.get("type")
    if not isinstance(event_type, str) or not event_type.strip():
        return None
    item = payload.get("item")
    if not isinstance(item, dict):
        item = payload.get("payload")
    event: dict[str, object] = {"type": event_type}
    if isinstance(item, dict):
        event["item"] = dict(item)
    else:
        # Keep scalar provider metadata without inventing an application
        # classification. This is useful for diagnosing transport failures.
        event["provider"] = {
            key: value
            for key, value in payload.items()
            if key != "type" and isinstance(value, (str, int, float, bool))
        }
    return event


def _session_id(payload: dict[str, object]) -> str:
    if payload.get("type") not in {"thread.started", "thread_started"}:
        return ""
    for key in ("thread_id", "session_id"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _json_digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _contains_sensitive_value(value: object, *, depth: int = 0) -> bool:
    if depth > 12:
        return True
    if isinstance(value, dict):
        if _contains_sensitive_argv(value):
            return True
        return any(
            is_sensitive_credential_name(str(key))
            or _contains_sensitive_value(item, depth=depth + 1)
            for key, item in value.items()
        )
    if isinstance(value, list | tuple):
        return any(_contains_sensitive_value(item, depth=depth + 1) for item in value)
    if not isinstance(value, str):
        return False
    if _is_signed_url(value) or contains_credential(value):
        return True
    stripped = value.lstrip()
    if not stripped.startswith(("{", "[")) or len(value) > 64 * 1024:
        return False
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return False
    return _contains_sensitive_value(decoded, depth=depth + 1)


def _contains_local_runtime_value(value: object, *, depth: int = 0) -> bool:
    if depth > 12:
        return True
    if isinstance(value, dict):
        return any(
            contains_local_runtime_leak(str(key))
            or _contains_local_runtime_value(item, depth=depth + 1)
            for key, item in value.items()
        )
    if isinstance(value, list | tuple):
        return any(
            _contains_local_runtime_value(item, depth=depth + 1) for item in value
        )
    if not isinstance(value, str):
        return False
    if contains_local_runtime_leak(value):
        return True
    stripped = value.lstrip()
    if not stripped.startswith(("{", "[")) or len(value) > 64 * 1024:
        return False
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return False
    return _contains_local_runtime_value(decoded, depth=depth + 1)


_RUNTIME_REFERENCE_TEXT_LIMITS = {
    "assertion": 2048,
    "authoredjudgment": 2048,
    "capability": 512,
    "consequence": 2048,
    "description": 2048,
    "expectedverification": 2048,
    "instruction": 2048,
    "key": 128,
    "label": 512,
    "objective": 2048,
    "operation": 512,
    "reason": 2048,
    "reference": 512,
    "summary": _RUNTIME_RESULT_SUMMARY_MAX_CHARS,
}


def _validate_runtime_reference_text_bounds(value: object, *, depth: int = 0) -> None:
    if depth > 12:
        raise ValueError("runtime_result_reference_depth_invalid")
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = _normalized_key(str(key))
            limit = _RUNTIME_REFERENCE_TEXT_LIMITS.get(normalized)
            if limit is not None and isinstance(item, str) and len(item) > limit:
                raise ValueError("runtime_result_reference_text_too_large")
            _validate_runtime_reference_text_bounds(item, depth=depth + 1)
    elif isinstance(value, list | tuple):
        for item in value:
            _validate_runtime_reference_text_bounds(item, depth=depth + 1)


def _validate_runtime_reference_domain_result(
    result: ConsumerAgentResult | AuditAgentResult,
    *,
    allow_configured_feedback_links: bool = False,
) -> None:
    domain_result = _project_runtime_domain_result(result)
    if allow_configured_feedback_links:
        domain_result = cast(
            dict[str, object],
            sanitize_configured_feedback_links(
                domain_result,
                vercel_base_url=feedback_spike_vercel_base_url(),
            ),
        )
    _redact_local_runtime_values(domain_result)
    # Local paths can be accidentally echoed while describing source material.
    # Redact the serialized domain fields before enforcing the result boundary.
    _validate_runtime_reference_text_bounds(domain_result)
    sensitive_projection = domain_result
    if _contains_sensitive_value(sensitive_projection):
        raise ValueError("agent_result_contains_sensitive_value")
    if _contains_local_runtime_value(domain_result):
        raise RuntimeResultValidationError("runtime_result_contains_local_runtime_leak")
    encoded = json.dumps(
        domain_result,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    if len(encoded.encode("utf-8")) > _RUNTIME_DOMAIN_RESULT_CODEC_MAX_BYTES:
        raise ValueError("runtime_result_reference_too_large")


def _redact_local_runtime_values(value: object) -> None:
    if isinstance(value, dict):
        for key, item in list(value.items()):
            if isinstance(item, str):
                value[key] = redact_forbidden_leak_markers(item)
            else:
                _redact_local_runtime_values(item)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            if isinstance(item, str):
                value[index] = redact_forbidden_leak_markers(item)
            else:
                _redact_local_runtime_values(item)


def _contains_sensitive_argv(value: dict[object, object]) -> bool:
    argv = value.get("argv")
    if not isinstance(argv, list) or not all(isinstance(item, str) for item in argv):
        return False
    for token in argv:
        if not token.startswith("--"):
            continue
        flag = token[2:].partition("=")[0]
        if is_sensitive_credential_name(flag):
            return True
    return False
