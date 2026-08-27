from __future__ import annotations

import hashlib
import json
import os
import time
import unicodedata
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import RLock
from typing import Generic, TypeVar, cast

from markdown_it import MarkdownIt
from pydantic import ValidationError

from app.agent_contracts import (
    AuditAgentResult,
    AuditExternalResult,
    AuditOutcome,
    AuditReconciliation,
    ConsumerAgentResult,
    ConsumerOutcome,
    ReconciliationDisposition,
)
from app.agent_effects import (
    IDLE_TIMEOUT_SECONDS,
    LEASE_SECONDS,
    TOTAL_TIMEOUT_SECONDS,
    McpToolEffectRegistry,
    _controlled_cli_receipt,
    _is_sensitive_key,
    _is_signed_url,
    _mcp_call_completed,
    _normalized_key,
)
from app.agent_result import (
    AgentError,
    EffectKind,
    ResultParseError,
    SideEffectState,
)
from app.agent_runtime_config import AgentRuntimeConfig, load_runtime_config
from app.agent_runtime_contracts import (
    RuntimeFailureClass,
    RuntimeKind,
    RuntimeRoute,
)
from app.agent_runtime_router import AgentRuntimeRouter
from app.agent_skill_usage import (
    LoadedSkillReceipt,
    loaded_skill_receipts,
    normalized_read_skill_metadata,
)
from app.claude_runtime_adapter import (
    ClaudeCommandPolicy,
    ClaudeEventNormalizer,
    ClaudeRuntimeAdapter,
    require_claude_session_id,
)
from app.codex_capacity import (
    CODEX_PROVIDER_CAPACITY_EXHAUSTED,
    codex_provider_failure_code,
    is_codex_capacity_exhausted,
)
from app.codex_failure import (
    CODEX_PROVIDER_AUTH_FAILED,
    CODEX_PROVIDER_UNAVAILABLE,
    classify_codex_process_failure,
)
from app.codex_history import (
    count_codex_session_lines,
    extract_codex_mcp_tool_results_from_session,
)
from app.codex_runner import _codex_home
from app.codex_runtime_adapter import CodexRuntimeAdapter
from app.config import feedback_spike_vercel_base_url
from app.feedback_spike import sanitize_configured_feedback_links
from app.leak_check import contains_credential, contains_local_runtime_leak
from app.native_cli_metadata import (
    AgentReadOnlyViolationError,
    NativeCliMetadataClassifier,
    dingtalk_message_text,
    describe_native_command,
    native_command_argv,
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

ResultT = TypeVar("ResultT")
ProcessExecutor = Callable[..., ProcessRunResult]
UNKNOWN_RECONCILIATION_RETRY_BASE_SECONDS = 60
UNKNOWN_RECONCILIATION_RETRY_MAX_SECONDS = 15 * 60
CLAUDE_INPUT_MAX_BYTES = 1024 * 1024
_COMMON_RUNTIME_CAPABILITIES = frozenset(
    {"structured_output", "local_schema_validation"}
)
_CONSUMER_RUNTIME_CAPABILITIES = frozenset(
    {"consumer_read_only_enforcement", "reviewed_read_tools"}
)
_AUDIT_RUNTIME_CAPABILITIES = frozenset(
    {"audit_effect_visibility", "reviewed_read_tools", "reviewed_write_tools"}
)
_RECONCILIATION_RUNTIME_CAPABILITIES = frozenset(
    {"consumer_read_only_enforcement", "reconciliation_read_only"}
)
_RUNTIME_DOMAIN_RESULT_CODEC_VERSION = 1
_RUNTIME_RESULT_EVIDENCE_VERSION = 1
_RUNTIME_DOMAIN_RESULT_CODEC_MAX_BYTES = 32 * 1024
_RUNTIME_RESULT_SUMMARY_MAX_CHARS = 2048
_RUNTIME_RESULT_REFERENCE_KEYS = frozenset(
    {
        "action",
        "conversation_id",
        "evidence",
        "id",
        "message_id",
        "operation_id",
        "process_instance_id",
        "receipt_id",
        "recovery_action_indexes",
        "remark",
        "send_status",
        "status",
        "task_id",
    }
)
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
    """No configured route has current evidence for this exact turn."""

    code = "runtime_route_unavailable"

    def __init__(self, reason: str) -> None:
        self.reason = reason
        if "missing_capabilities:" in reason or "surface_missing:" in reason:
            self.code = "runtime_capability_missing"
        super().__init__(self.code)


class _RecoveredCompletedRuntimeResult(RuntimeError):
    """Internal control flow for a validated durable provider result."""


class CompletedRuntimeResultBlockedError(ValueError):
    """A durable result cannot be trusted and must not trigger provider replay."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class _DecodedRuntimeDomainResult:
    result: ConsumerAgentResult | AuditAgentResult
    evidence: dict[str, object]


def _runtime_evidence_digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _runtime_authorizations_digest(
    recovery_authorizations: dict[str, int],
) -> str:
    canonical: list[tuple[str, int]] = []
    for authorization_id, action_index in recovery_authorizations.items():
        if (
            not isinstance(authorization_id, str)
            or not authorization_id
            or type(action_index) is not int
            or action_index < 0
        ):
            raise ValueError("runtime_recovery_authorization_invalid")
        canonical.append((authorization_id, action_index))
    return _runtime_evidence_digest(sorted(canonical))


def _runtime_result_evidence(
    *,
    run: AgentRun,
    event_start: int,
    receipts: list[object],
    recovery_started_actions: set[int],
    completed_before_recovery: set[int],
    recovery_authorizations: dict[str, int] | None = None,
) -> dict[str, object]:
    recovery_authorizations = recovery_authorizations or {}
    event_end = len(run.tool_events)
    if event_start < 0 or event_start > event_end:
        raise ValueError("runtime_result_evidence_event_bounds_invalid")
    receipt_projection = [
        {
            "receipt_id": str(getattr(receipt, "receipt_id", "")),
            "operation_id": str(getattr(receipt, "operation_id", "")),
            "cli": str(getattr(receipt, "cli", "")),
            "command_path": str(getattr(receipt, "command_path", "")),
            "command_digest": str(getattr(receipt, "command_digest", "")),
            "exit_code": int(getattr(receipt, "exit_code", -1)),
            "completed": bool(getattr(receipt, "completed", False)),
            "persisted": bool(getattr(receipt, "persisted", False)),
            "safe_to_confirm": bool(getattr(receipt, "safe_to_confirm", False)),
            "effect_counted": bool(getattr(receipt, "effect_counted", False)),
        }
        for receipt in receipts
    ]
    return {
        "version": _RUNTIME_RESULT_EVIDENCE_VERSION,
        "event_start": event_start,
        "event_end": event_end,
        "events_sha256": _runtime_evidence_digest(
            run.tool_events[event_start:event_end]
        ),
        "receipts_sha256": _runtime_evidence_digest(receipt_projection),
        "recovery_started_actions": sorted(recovery_started_actions),
        "completed_before_recovery": sorted(completed_before_recovery),
        "recovery_authorizations_sha256": _runtime_authorizations_digest(
            recovery_authorizations
        ),
    }


def _validate_runtime_result_evidence_shape(
    evidence: object,
) -> dict[str, object]:
    if not isinstance(evidence, dict) or set(evidence) != {
        "version",
        "event_start",
        "event_end",
        "events_sha256",
        "receipts_sha256",
        "recovery_started_actions",
        "completed_before_recovery",
        "recovery_authorizations_sha256",
    }:
        raise ValueError("runtime_result_evidence_invalid")
    if (
        type(evidence.get("version")) is not int
        or evidence["version"] != _RUNTIME_RESULT_EVIDENCE_VERSION
        or type(evidence.get("event_start")) is not int
        or type(evidence.get("event_end")) is not int
        or evidence["event_start"] < 0
        or evidence["event_end"] < evidence["event_start"]
        or not isinstance(evidence.get("events_sha256"), str)
        or len(evidence["events_sha256"]) != 64
        or not isinstance(evidence.get("receipts_sha256"), str)
        or len(evidence["receipts_sha256"]) != 64
        or not isinstance(evidence.get("recovery_authorizations_sha256"), str)
        or len(evidence["recovery_authorizations_sha256"]) != 64
    ):
        raise ValueError("runtime_result_evidence_invalid")
    for key in ("recovery_started_actions", "completed_before_recovery"):
        indexes = evidence.get(key)
        if (
            not isinstance(indexes, list)
            or any(type(index) is not int or index < 0 for index in indexes)
            or indexes != sorted(set(indexes))
        ):
            raise ValueError("runtime_result_evidence_invalid")
    return evidence


def _bounded_runtime_result_text(value: str, *, field: str, limit: int) -> str:
    if not isinstance(value, str) or not value or len(value) > limit:
        raise ValueError(f"runtime_result_envelope_{field}_invalid")
    return value


def _project_runtime_external_reference(
    reference: dict[str, object],
) -> dict[str, object]:
    if not set(reference).issubset(_RUNTIME_RESULT_REFERENCE_KEYS):
        raise ValueError("runtime_result_envelope_external_reference_invalid")
    projected: dict[str, object] = {}
    for key, value in reference.items():
        if key == "recovery_action_indexes":
            if (
                not isinstance(value, list)
                or any(type(index) is not int or index < 0 for index in value)
                or len(value) > 128
            ):
                raise ValueError("runtime_result_envelope_external_reference_invalid")
            projected[key] = list(value)
            continue
        if not isinstance(value, (str, int, bool)) or isinstance(value, float):
            raise ValueError("runtime_result_envelope_external_reference_invalid")
        if isinstance(value, str) and (
            not value
            or len(value) > 512
            or "://" in value
            or contains_local_runtime_leak(value)
        ):
            raise ValueError("runtime_result_envelope_external_reference_invalid")
        projected[key] = value
    return projected


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
                        "capability": action.capability,
                        "operation": action.operation,
                        "target": action.target,
                        "payload": action.payload,
                        "expected_verification": action.expected_verification,
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
            "verification_summary": _bounded_runtime_result_text(
                result.external_result.verification_summary,
                field="verification_summary",
                limit=2048,
            ),
            "live_result_reference": _project_runtime_external_reference(
                result.external_result.live_result_reference
            ),
        }
    return {
        "outcome": result.outcome.value,
        "summary": summary,
        "proposal_revision": result.proposal_revision,
        "side_effect_state": result.side_effect_state.value,
        "feedback": (
            result.feedback.model_dump(mode="json")
            if result.feedback is not None
            else None
        ),
        "external_result": external_result,
        "reconciliation": [
            entry.model_dump(mode="json") for entry in result.reconciliation
        ],
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
    recovery_phase: str,
    result: ConsumerAgentResult | AuditAgentResult,
    evidence: dict[str, object] | None = None,
    result_reference_run_id: int | None = None,
) -> str:
    if evidence is None:
        empty_digest = _runtime_evidence_digest([])
        evidence = {
            "version": _RUNTIME_RESULT_EVIDENCE_VERSION,
            "event_start": 0,
            "event_end": 0,
            "events_sha256": empty_digest,
            "receipts_sha256": empty_digest,
            "recovery_started_actions": [],
            "completed_before_recovery": [],
            "recovery_authorizations_sha256": _runtime_authorizations_digest({}),
        }
    evidence = _validate_runtime_result_evidence_shape(evidence)
    envelope = {
        "schema_id": schema_id,
        "version": _RUNTIME_DOMAIN_RESULT_CODEC_VERSION,
        "role": role.value,
        "recovery_phase": recovery_phase,
        "evidence": evidence,
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
    recovery_phase: str,
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
        "recovery_phase",
        "result",
        "evidence",
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
        or envelope.get("recovery_phase") != recovery_phase
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
        evidence = _validate_runtime_result_evidence_shape(envelope["evidence"])
        return _DecodedRuntimeDomainResult(result=result, evidence=evidence)
    except (ValidationError, ValueError) as exc:
        raise ValueError("runtime_result_envelope_invalid") from exc


def unknown_reconciliation_retry_at(
    attempts: int, *, now: datetime | None = None
) -> str:
    delay_seconds = min(
        UNKNOWN_RECONCILIATION_RETRY_BASE_SECONDS * (2 ** max(attempts - 1, 0)),
        UNKNOWN_RECONCILIATION_RETRY_MAX_SECONDS,
    )
    current = now or datetime.now(timezone.utc)
    return (
        current.astimezone(timezone.utc) + timedelta(seconds=delay_seconds)
    ).strftime("%Y-%m-%d %H:%M:%S")


def _required_runtime_capabilities(
    *,
    run: AgentRun,
    recovery_phase: str,
    expected_effect_actions: tuple[dict[str, object], ...],
    explicit_capabilities: frozenset[str] = frozenset(),
) -> frozenset[str]:
    required = set(_COMMON_RUNTIME_CAPABILITIES)
    if run.role is AgentRole.CONSUMER:
        required.update(_CONSUMER_RUNTIME_CAPABILITIES)
    elif recovery_phase == "reconcile":
        required.update(_RECONCILIATION_RUNTIME_CAPABILITIES)
        required.add("reviewed_read_tools")
    else:
        required.update(_AUDIT_RUNTIME_CAPABILITIES)
    if recovery_phase != "reconcile":
        for action in expected_effect_actions:
            capability = action.get("capability")
            # Consumer capabilities are descriptive business labels until the
            # Audit layer canonicalizes a reviewed native command to an
            # ``agent_cli.*`` or ``native_cli.*`` execution surface.  A raw
            # label such as ``dingtalk_chat`` must not make route selection
            # fail before Audit can reject or revise the proposal.
            if (
                isinstance(capability, str)
                and capability.strip()
                and capability.strip().startswith(("agent_cli.", "native_cli."))
            ):
                required.add(capability.strip())
    required.update(
        capability.strip()
        for capability in explicit_capabilities
        # Skill availability is governed by agent_cli.read_skill and then
        # rechecked from exact receipts before an Audit effect starts. It is
        # not a route-health capability, so a static surface must never block
        # a currently installed Codex Skill before that validation runs.
        if capability.strip() and not capability.startswith("reviewed_skill:")
    )
    return frozenset(required)


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
    if code.startswith(CODEX_PROVIDER_AUTH_FAILED):
        return code
    if code in {CODEX_PROVIDER_UNAVAILABLE, CODEX_PROVIDER_CAPACITY_EXHAUSTED}:
        return code
    if isinstance(exc, ResultParseError):
        if code == "no valid typed result JSON found in Codex JSONL":
            return "codex_result_missing"
        return "codex_result_invalid"
    return "codex_process_failed"


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


class AgentTurnProcess(Generic[ResultT]):
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
        mcp_effect_registry: McpToolEffectRegistry | None = None,
        native_cli_classifier: NativeCliMetadataClassifier | None = None,
        refresh_runtime_capabilities: Callable[[], object] | None = None,
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
        self._allow_legacy_oauth_bootstrap = runtime_router is None
        self.runtime_router = runtime_router or AgentRuntimeRouter(
            routes=self.runtime_config.routes,
            store=store,
            snapshots={},
        )
        self.executor = executor or run_process_with_idle_timeout
        self.effects = mcp_effect_registry or McpToolEffectRegistry.default()
        self.native_cli = native_cli_classifier or NativeCliMetadataClassifier()
        self.refresh_runtime_capabilities = refresh_runtime_capabilities

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
        expected_effect_actions: tuple[dict[str, object], ...] = (),
        on_progress: Callable[[], None] | None = None,
        recovery_phase: str = "",
        authorized_recovery_actions: frozenset[int] = frozenset(),
        recovery_authorizations: dict[str, int] | None = None,
        allow_effectful_tools: bool = False,
        image_paths: list[Path] | None = None,
        required_skill_receipts: tuple[LoadedSkillReceipt, ...] = (),
        required_capabilities: frozenset[str] = frozenset(),
        conversation_contract_hash: str = "",
    ) -> AgentTurnRunResult[ResultT]:
        if recovery_phase not in {"", "reconcile", "execute"}:
            raise ValueError("invalid recovery phase")
        recover_unknown = bool(recovery_phase)
        recovery_authorizations = recovery_authorizations or {}
        line_count = 0
        saw_json = False
        primary_turn_started = False
        primary_turn_closed = False
        recovery_started_actions: set[int] = set()
        effect_action_counts = [0] * len(expected_effect_actions)
        effect_action_by_call_id: dict[str, int] = {}
        suppressed_session_replay_call_ids: set[str] = set()
        observed_session_id = ""
        active_attempt: AgentRuntimeAttempt | None = None
        active_route: RuntimeRoute | None = None
        replayed_effect_evidence = False
        session_transcript_end = 0
        claude_normalizer: ClaudeEventNormalizer | None = None
        pending_claude_session_id = ""
        recovered_completed_attempt = False
        turn_event_start = len(run.tool_events)
        recovery_event_start = turn_event_start
        completed_before_recovery = (
            _action_completion_accounting(
                [],
                self.store.list_agent_execution_receipts(run.id),
                expected_effect_actions,
                operation_id=run.operation_id,
                registry=self.effects,
            )[0]
            if recover_unknown
            else set()
        )
        transcript_start = (
            run.transcript_end_line if recover_unknown else run.transcript_start_line
        )
        effect_fence_lock = RLock()

        def authorize_claude_effect_dispatch(request: dict[str, object]) -> bool:
            if active_attempt is None or active_route is None:
                return False
            required_request_keys = {
                "dispatch_id",
                "reviewed_server",
                "reviewed_tool",
                "capability",
                "operation",
                "operation_digest",
                "arguments_digest",
                "target_identifiers",
            }
            if (
                set(request) != required_request_keys
                or active_route.runtime_kind is not RuntimeKind.CLAUDE_CLI
                or run.role is not AgentRole.AUDIT
                or recovery_phase
                or not isinstance(request.get("dispatch_id"), str)
                or not request["dispatch_id"].startswith("claude-dispatch:")
            ):
                return False
            with effect_fence_lock:
                candidates = [
                    index
                    for index, action in enumerate(expected_effect_actions)
                    if request.get("reviewed_server") == action.get("reviewed_server")
                    and request.get("reviewed_tool") == action.get("reviewed_tool")
                    and _metadata_matches_action(request, action)
                ]
                if not candidates:
                    return False
                action_index = min(candidates, key=effect_action_counts.__getitem__)
                metadata = {
                    "effect": EffectKind.EFFECTFUL.value,
                    **request,
                    "operation_id": run.operation_id,
                    "action_index": action_index,
                }
                dispatch_id = str(metadata.pop("dispatch_id"))
                event = {
                    "type": "item.started",
                    "item": {
                        "type": "mcp_tool_call",
                        "id": dispatch_id,
                        "status": "in_progress",
                        "metadata": metadata,
                    },
                }
                claim = self.store.authorize_claude_effect_dispatch(
                    run_id=run.id,
                    attempt_id=active_attempt.id,
                    owner=self.owner,
                    event=event,
                    expected_action=expected_effect_actions[action_index],
                    required_skill_receipts=tuple(
                        (receipt.name, receipt.path, receipt.sha256)
                        for receipt in required_skill_receipts
                    ),
                )
                if claim.dispatch_acquired:
                    effect_action_counts[action_index] += 1
                    effect_action_by_call_id[dispatch_id] = action_index
                return claim.dispatch_acquired

        def persist_effect_event(
            payload: dict[str, object],
            *,
            from_session_replay: bool = False,
            from_claude_normalizer: bool = False,
        ) -> None:
            nonlocal line_count, saw_json
            nonlocal primary_turn_started, primary_turn_closed
            # Provider capabilities are not reimplemented at the application
            # layer. Keep the event for observability, but let the runtime
            # decide whether a command/tool is permitted.
            read_only = False
            event = (
                _trusted_claude_effect_event(payload, read_only=read_only)
                if from_claude_normalizer
                else self._normalized_effect_event(
                    payload,
                    read_only=read_only,
                    operation_id=run.operation_id,
                    require_recovery_authorization=recovery_phase == "execute",
                    expected_message_text_digests=frozenset(
                        digest
                        for action in expected_effect_actions
                        if isinstance(digest := action.get("message_text_digest"), str)
                    ),
                    expected_message_rendered_text_digests=frozenset(
                        digest
                        for action in expected_effect_actions
                        if isinstance(
                            digest := action.get("message_rendered_text_digest"),
                            str,
                        )
                    ),
                    message_operation_started_at=run.started_at,
                )
            )
            if event is None:
                return
            item = event.get("item")
            metadata = item.get("metadata") if isinstance(item, dict) else None
            effect = metadata.get("effect") if isinstance(metadata, dict) else None
            call_id = (
                str(item.get("id") or item.get("call_id") or "")
                if isinstance(item, dict)
                else ""
            )
            if from_claude_normalizer and effect == EffectKind.EFFECTFUL.value:
                persisted = self.store.get_agent_run(run.id)
                persisted_start = (
                    _matching_effect_metadata(
                        persisted.tool_events if persisted is not None else [],
                        event_type="item.started",
                        operation_id=run.operation_id,
                        action_index=effect_action_by_call_id.get(call_id, -1),
                        expected_action=(
                            expected_effect_actions[
                                effect_action_by_call_id.get(call_id, -1)
                            ]
                            if effect_action_by_call_id.get(call_id, -1) >= 0
                            else {}
                        ),
                    )
                    if call_id
                    else None
                )
                if persisted_start is None:
                    raise AgentReadOnlyViolationError(
                        "claude_effect_fence_evidence_missing"
                    )
                if event.get("type") == "item.started":
                    return
                assert isinstance(metadata, dict)
                metadata.update(
                    {
                        key: value
                        for key, value in persisted_start.items()
                        if key not in {"result_digest"}
                    }
                )
            if (
                event.get("type") == "item.started"
                and effect == EffectKind.EFFECTFUL.value
                and active_attempt is not None
            ):
                self.store.note_runtime_attempt_effect_started(active_attempt.id)
            if (
                event.get("type") == "item.started"
                and effect == EffectKind.EFFECTFUL.value
                and required_skill_receipts
                and recovery_phase != "reconcile"
            ):
                current_run = self.store.get_agent_run(run.id)
                persisted_events = (
                    current_run.tool_events[turn_event_start:] if current_run else ()
                )
                observed = {
                    (receipt.name, receipt.path, receipt.sha256)
                    for receipt in loaded_skill_receipts(persisted_events)
                }
                required = {
                    (receipt.name, receipt.path, receipt.sha256)
                    for receipt in required_skill_receipts
                }
                if not required.issubset(observed):
                    raise AgentReadOnlyViolationError("audit_skill_reread_missing")
            if effect == EffectKind.EFFECTFUL.value and isinstance(metadata, dict):
                if event.get("type") == "item.started":
                    authorization_id = metadata.get("authorization_id")
                    if recovery_phase == "execute":
                        action_index = recovery_authorizations.get(
                            str(authorization_id or "")
                        )
                        if (
                            action_index is None
                            or action_index >= len(expected_effect_actions)
                            or not _metadata_matches_action(
                                metadata, expected_effect_actions[action_index]
                            )
                        ):
                            action_index = None
                    else:
                        candidates = [
                            index
                            for index, action in enumerate(expected_effect_actions)
                            if _metadata_matches_action(metadata, action)
                        ]
                        action_index = (
                            min(candidates, key=effect_action_counts.__getitem__)
                            if candidates
                            else None
                        )
                    if action_index is not None:
                        # The local Codex session can contain the same completed
                        # controlled call already observed in the live JSON stream,
                        # but with a different call ID. Replaying it would create a
                        # second lifecycle in our ledger and falsely make one action
                        # look like two executions. Keep session-only recovery, but
                        # never replay an action that live streaming already covered.
                        if from_session_replay and effect_action_counts[action_index]:
                            suppressed_session_replay_call_ids.add(call_id)
                            return
                        metadata["action_index"] = action_index
                        effect_action_counts[action_index] += 1
                        effect_action_by_call_id[call_id] = action_index
                else:
                    if (
                        from_session_replay
                        and call_id in suppressed_session_replay_call_ids
                    ):
                        return
                    action_index = effect_action_by_call_id.get(call_id)
                    if action_index is not None:
                        metadata["action_index"] = action_index
            if (
                recovery_phase == "execute"
                and event.get("type") == "item.started"
                and effect == EffectKind.EFFECTFUL.value
            ):
                action_index = metadata.get("action_index")
                if (
                    action_index is None
                    or action_index not in authorized_recovery_actions
                    or action_index in completed_before_recovery
                    or action_index in recovery_started_actions
                ):
                    raise RuntimeError("audit_recovery_action_not_authorized")
                recovery_started_actions.add(action_index)
            if recover_unknown:
                self.store.append_unknown_agent_run_event(
                    run.id, event, owner=self.owner
                )
            else:
                self.store.append_agent_run_event(run.id, event, owner=self.owner)
            self._record_direct_send_receipt(event, payload, run=run)

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
                if recover_unknown:
                    # Reconciliation and the narrowly authorized follow-up write
                    # run as fresh sessions. Preserve the original session on the
                    # AgentRun as immutable execution history rather than replacing
                    # it with a recovery session identifier.
                    pass
                elif active_route is not None and active_route.name == "codex_oauth":
                    self.store.set_agent_run_session(
                        run.id,
                        new_session,
                        owner=self.owner,
                        transcript_start_line=run.transcript_start_line,
                        allow_consumer_session_handoff=(run.role is AgentRole.CONSUMER),
                    )
                if not is_claude and (
                    run.role is AgentRole.CONSUMER
                    and not recover_unknown
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
                    and not recover_unknown
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
            if recover_unknown:
                self.store.renew_agent_run_lease(
                    run.id,
                    owner=self.owner,
                    lease_seconds=LEASE_SECONDS,
                    expected_status="unknown",
                )
            else:
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
            session_for_receipts: str,
            *,
            session_start: int,
        ) -> int:
            """Stabilize local evidence and replay it through normal validation."""
            nonlocal active_attempt, replayed_effect_evidence
            previous_count = -1
            for _ in range(4):
                session_end = count_codex_session_lines(
                    session_for_receipts, codex_home=_codex_home()
                )
                if session_end == previous_count:
                    break
                previous_count = session_end
                time.sleep(0.05)
            stable_end = max(previous_count, 0)
            persisted_for_replay = self.store.get_agent_run(run.id)
            persisted_completed_call_ids = {
                str(item.get("id") or item.get("call_id") or "")
                for event in (
                    persisted_for_replay.tool_events
                    if persisted_for_replay is not None
                    else ()
                )
                if event.get("type") == "item.completed"
                for item in [event.get("item")]
                if isinstance(item, dict)
            }
            persisted_read_counts = Counter(
                (
                    str(metadata.get("reviewed_server") or ""),
                    str(metadata.get("reviewed_tool") or ""),
                    str(metadata.get("arguments_digest") or ""),
                )
                for event in (
                    persisted_for_replay.tool_events
                    if persisted_for_replay is not None
                    else ()
                )
                if event.get("type") == "item.completed"
                for item in [event.get("item")]
                if isinstance(item, dict)
                for metadata in [item.get("metadata")]
                if isinstance(metadata, dict)
                and metadata.get("effect") == EffectKind.READ_ONLY.value
            )
            for completed_payload in extract_codex_mcp_tool_results_from_session(
                session_for_receipts,
                codex_home=_codex_home(),
                start_line=session_start,
                end_line=stable_end,
            ):
                completed_item = completed_payload.get("item")
                call_id = (
                    str(completed_item.get("id") or "")
                    if isinstance(completed_item, dict)
                    else ""
                )
                if not call_id or call_id in persisted_completed_call_ids:
                    continue
                call = self.effects.classify(completed_item)
                if call is None:
                    continue
                if call.server == "agent_cli" and (
                    run.role is AgentRole.CONSUMER
                    or call.effect is EffectKind.READ_ONLY
                ):
                    continue
                if (
                    recovery_phase != "reconcile"
                    and run.role is not AgentRole.CONSUMER
                    and call.effect is EffectKind.READ_ONLY
                ):
                    continue
                if call.effect is EffectKind.EFFECTFUL:
                    replayed_effect_evidence = True
                try:
                    normalized_completion = self._normalized_effect_event(
                        completed_payload,
                        read_only=(recovery_phase == "reconcile"),
                        operation_id=run.operation_id,
                        require_recovery_authorization=(recovery_phase == "execute"),
                        expected_message_text_digests=frozenset(
                            digest
                            for action in expected_effect_actions
                            if isinstance(
                                digest := action.get("message_text_digest"), str
                            )
                        ),
                        expected_message_rendered_text_digests=frozenset(
                            digest
                            for action in expected_effect_actions
                            if isinstance(
                                digest := action.get("message_rendered_text_digest"),
                                str,
                            )
                        ),
                        message_operation_started_at=run.started_at,
                    )
                except AgentReadOnlyViolationError as exc:
                    if str(exc) != "agent_cli_receipt_invalid":
                        raise
                    continue
                if call.effect is EffectKind.READ_ONLY:
                    normalized_item = (
                        normalized_completion.get("item")
                        if isinstance(normalized_completion, dict)
                        else None
                    )
                    normalized_metadata = (
                        normalized_item.get("metadata")
                        if isinstance(normalized_item, dict)
                        else None
                    )
                    if isinstance(normalized_metadata, dict):
                        read_key = (
                            str(normalized_metadata.get("reviewed_server") or ""),
                            str(normalized_metadata.get("reviewed_tool") or ""),
                            str(normalized_metadata.get("arguments_digest") or ""),
                        )
                        if persisted_read_counts[read_key] > 0:
                            persisted_read_counts[read_key] -= 1
                            continue
                start_payload = {
                    "type": "item.started",
                    "item": {
                        key: value
                        for key, value in completed_item.items()
                        if key not in {"status", "result"}
                    }
                    | {"status": "in_progress"},
                }
                persist_effect_event(start_payload, from_session_replay=True)
                persist_effect_event(completed_payload, from_session_replay=True)
            return stable_end

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
            recovery_phase=recovery_phase,
            expected_effect_actions=expected_effect_actions,
            explicit_capabilities=required_capabilities,
        )
        execution_contract = {
            "version": 1,
            "role": run.role.value,
            "recovery_phase": recovery_phase,
            "operation_id": run.operation_id,
            "conversation_contract_hash": conversation_contract_hash,
            "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "developer_instructions_sha256": hashlib.sha256(
                developer_instructions.encode("utf-8")
            ).hexdigest(),
            "required_capabilities": sorted(required_capabilities),
            "expected_actions_sha256": hashlib.sha256(
                json.dumps(
                    expected_effect_actions,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest(),
            "reviewed_skills": sorted(
                (receipt.name, receipt.sha256) for receipt in required_skill_receipts
            ),
            "recovery_authorizations_sha256": _runtime_authorizations_digest(
                recovery_authorizations
            ),
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
            if (
                completed_attempt is None
                and (run.effect_receipt_count or run.effect_unreviewed_count)
                and any(
                    attempt.status == "completed"
                    and attempt.result_envelope_json
                    and attempt.runtime_kind == RuntimeKind.CLAUDE_CLI.value
                    for attempt in runtime_attempts
                )
            ):
                mismatch_code = "completed_runtime_result_contract_mismatch"
                if recover_unknown:
                    self._defer_unknown(run, mismatch_code)
                else:
                    self.store.mark_agent_run_unknown(
                        run.id,
                        {"code": mismatch_code, "retryable": True},
                        owner=self.owner,
                    )
                raise CompletedRuntimeResultBlockedError(mismatch_code)
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
                        recovery_phase=recovery_phase,
                        referenced_agent_run_id=run.id,
                        referenced_result_json=run.final_result_json,
                    )
                    evidence = decoded.evidence
                    evidence_started = set(
                        cast(list[int], evidence["recovery_started_actions"])
                    )
                    evidence_completed_before = set(
                        cast(list[int], evidence["completed_before_recovery"])
                    )
                    persisted_for_evidence = self.store.get_agent_run(run.id)
                    if persisted_for_evidence is None:
                        raise ValueError("runtime_result_evidence_parent_missing")
                    current_evidence = _runtime_result_evidence(
                        run=persisted_for_evidence,
                        event_start=cast(int, evidence["event_start"]),
                        receipts=self.store.list_agent_execution_receipts(run.id),
                        recovery_started_actions=evidence_started,
                        completed_before_recovery=evidence_completed_before,
                        recovery_authorizations=recovery_authorizations,
                    )
                    if current_evidence != evidence:
                        raise ValueError("runtime_result_evidence_mismatch")
                    result = cast(ResultT, decoded.result)
                    turn_event_start = cast(int, evidence["event_start"])
                    recovery_event_start = turn_event_start
                    recovery_started_actions = evidence_started
                    completed_before_recovery = evidence_completed_before
                except ValueError as exc:
                    if recover_unknown:
                        self._defer_unknown(
                            run, "completed_runtime_result_envelope_invalid"
                        )
                    elif (
                        run.role is AgentRole.CONSUMER
                        and run.effect_receipt_count == 0
                        and run.effect_unreviewed_count == 0
                    ):
                        self.store.block_consumer_agent_run_for_completed_result_recovery(
                            run.id,
                            owner=self.owner,
                        )
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
                turn_event_start = 0
                recovered_completed_attempt = True
                raise _RecoveredCompletedRuntimeResult
            if self.refresh_runtime_capabilities is not None:
                self.refresh_runtime_capabilities()
            attempted_routes = frozenset(
                attempt.route_name
                for attempt in runtime_attempts
                if attempt.status in {"starting", "running", "failed", "superseded", "completed"}
            ) if recovery_phase else frozenset()
            configured_route_names = frozenset(
                candidate.name for candidate in self.runtime_config.routes
            )
            excluded_routes = (
                attempted_routes
                if attempted_routes and configured_route_names - attempted_routes
                else frozenset()
            )
            decision = self.runtime_router.first_route_decision(
                required_capabilities=required_capabilities,
                allow_legacy_oauth_bootstrap=self._allow_legacy_oauth_bootstrap,
                excluded_routes=excluded_routes,
            )
            route = decision.route
            if route is None:
                unavailable = RuntimeRouteUnavailableError(decision.reason)
                if not recover_unknown:
                    self._fail_running(
                        run,
                        unavailable.code,
                        detail=decision.reason,
                    )
                raise unavailable
            self._validate_route_workload_boundary(
                route, run=run, recovery_phase=recovery_phase
            )
            route_session_id = self._session_for_route(
                route,
                role=run.role,
                requested_session_id=session_id,
                recovery_phase=recovery_phase,
                conversation_contract_hash=conversation_contract_hash,
            )
            attempt_is_preclaimed = False
            while True:
                saw_json = False
                primary_turn_started = False
                primary_turn_closed = False
                observed_session_id = ""
                replayed_effect_evidence = False
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
                        recovery_phase=recovery_phase,
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
                        policy=self._claude_read_only_policy(),
                    )
                    claude_normalizer = claude_adapter.new_event_normalizer(
                        expected_session_id=route_session_id,
                        command=command,
                    )
                    command_env = claude_adapter.build_env(route, command=command)
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
                        approval_policy=(
                            "on-failure" if allow_effectful_tools else "never"
                        ),
                        developer_instructions=developer_instructions,
                        use_approval_bypass=allow_effectful_tools,
                    )
                    configure_command(command)
                    command_env = self.codex_adapter.build_env(route)
                try:
                    process = self.executor(
                        command,
                        prompt=executor_prompt,
                        env=command_env,
                        total_timeout_seconds=TOTAL_TIMEOUT_SECONDS,
                        idle_timeout_seconds=IDLE_TIMEOUT_SECONDS,
                        on_stdout_line=persist_line,
                    )
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
                        result = parse_result(process.stdout)
                    break
                if claude_adapter is not None:
                    claude_adapter.finish_invocation(command)
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
                evidence_uncertain = (
                    recovery_phase != "reconcile"
                    and bool(expected_effect_actions)
                    and not failed_session_id
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
                        evidence_uncertain = True
                if evidence_uncertain and active_attempt is not None:
                    active_attempt = self.store.note_runtime_attempt_effect_started(
                        active_attempt.id
                    )
                failed_attempt = self.store.fail_agent_runtime_attempt(
                    active_attempt.id,
                    failure.failure_class.value,
                    failure.code,
                    failure.failover_permitted,
                    session_id=failed_session_id,
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
                if evidence_uncertain or replayed_effect_evidence:
                    code = "runtime_failed_route_effect_requires_reconciliation"
                    if recover_unknown:
                        self._defer_unknown(run, code)
                    else:
                        self.store.mark_agent_run_unknown(
                            run.id,
                            {"code": code, "retryable": True},
                            owner=self.owner,
                        )
                    self._raise_for_process_failure(process, run=run)
                    raise AssertionError("unreachable process failure")
                fallback = _recovery_execution_result_from_receipts(
                    run=run,
                    recovery_phase=recovery_phase,
                    persisted=persisted,
                    expected_effect_actions=expected_effect_actions,
                    recovery_started_actions=recovery_started_actions,
                    authorized_recovery_actions=authorized_recovery_actions,
                    registry=self.effects,
                    store=self.store,
                )
                if fallback is not None:
                    result = cast(ResultT, fallback)
                    break
                self.store.renew_agent_run_lease(
                    run.id,
                    owner=self.owner,
                    lease_seconds=LEASE_SECONDS,
                    expected_status="unknown" if recover_unknown else "running",
                )
                decision = self.runtime_router.next_route(
                    run=persisted,
                    failed_attempt=failed_attempt,
                    failure=failure,
                    required_capabilities=required_capabilities,
                    recovery_phase=recovery_phase,
                )
                if decision.route is None:
                    self._raise_for_process_failure(process, run=run)
                    raise AssertionError("unreachable process failure")
                route = decision.route
                self._validate_route_workload_boundary(
                    route, run=run, recovery_phase=recovery_phase
                )
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
                        recovery_phase=recovery_phase,
                        conversation_contract_hash=conversation_contract_hash,
                    )
                )
                successor = self._claim_and_start_attempt(
                    run,
                    route,
                    route_session_id,
                    recovery_phase=recovery_phase,
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
            if _contains_sensitive_value(result.model_dump(mode="json")):
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
                and route.runtime_kind is RuntimeKind.CODEX_CLI
            ):
                self.store.complete_agent_runtime_attempt(
                    persisted_attempt.id,
                    observed_session_id,
                    "",
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
            if not recover_unknown:
                self._fail_running(run, exc.code, detail=exc.reason)
            raise
        except ResultParseError as exc:
            self._fail_runtime_attempt_unclassified(active_attempt)
            fallback = _recovery_execution_result_from_receipts(
                run=run,
                recovery_phase=recovery_phase,
                persisted=self.store.get_agent_run(run.id),
                expected_effect_actions=expected_effect_actions,
                recovery_started_actions=recovery_started_actions,
                authorized_recovery_actions=authorized_recovery_actions,
                registry=self.effects,
                store=self.store,
            )
            if fallback is None:
                parse_error_code = _agent_process_error_code(exc)
                if recover_unknown:
                    self._defer_unknown(run, parse_error_code)
                else:
                    self._fail_running(
                        run,
                        parse_error_code,
                        detail=_result_parse_error_detail(exc),
                    )
                raise
            result = cast(ResultT, fallback)
        except AgentReadOnlyViolationError as exc:
            self._fail_runtime_attempt_unclassified(active_attempt)
            code = str(exc).strip() or "agent_read_only_violation"
            if recover_unknown:
                self._defer_unknown(run, code)
            else:
                self._fail_running(run, code)
            raise
        except Exception as exc:
            self._fail_runtime_attempt_unclassified(active_attempt)
            provider_recovery = _agent_process_error_code(exc)
            code = (
                provider_recovery
                if provider_recovery != "codex_process_failed"
                else (
                    str(exc)
                    if str(exc).startswith("audit_recovery_")
                    else "codex_process_failed"
                )
            )
            if recover_unknown:
                self._defer_unknown(run, code)
            else:
                self._fail_running(run, code)
            if provider_recovery in {
                CODEX_PROVIDER_UNAVAILABLE,
                CODEX_PROVIDER_CAPACITY_EXHAUSTED,
            }:
                raise RuntimeError(code) from exc
            raise
        transcript_end = (
            run.transcript_end_line
            if recover_unknown
            else max(transcript_start + line_count, session_transcript_end)
        )
        outcome = getattr(result, "outcome")
        side_effect_state = getattr(result, "side_effect_state", SideEffectState.NONE)
        persisted = self.store.get_agent_run(run.id)
        assert persisted is not None
        if run.role is AgentRole.AUDIT and recovery_phase == "reconcile":
            try:
                reconciliation = self._validate_audit_reconciliation_result(
                    run,
                    result,
                    persisted,
                    expected_effect_actions=expected_effect_actions,
                    recovery_event_start=recovery_event_start,
                    completed_before_recovery=completed_before_recovery,
                )
            except RuntimeError as exc:
                if str(exc) not in {
                    "audit_reconciliation_result_invalid",
                    "audit_reconciliation_evidence_mismatch",
                    "audit_recovery_evidence_missing",
                }:
                    raise
                # A model that explicitly claims reconciliation must provide a
                # structured disposition for every unresolved action. Durable
                # reads may repair an invalid result, but may not turn an empty
                # reconciliation claim into implicit confirmation.
                if getattr(
                    result, "outcome"
                ) is AuditOutcome.RECONCILED and not getattr(result, "reconciliation"):
                    raise
                fallback = _conservative_reconciliation_result_from_readbacks(
                    run=run,
                    persisted=persisted,
                    expected_effect_actions=expected_effect_actions,
                    recovery_event_start=recovery_event_start,
                    completed_before_recovery=completed_before_recovery,
                    registry=self.effects,
                )
                if fallback is None:
                    raise exc
                result = cast(ResultT, fallback)
                reconciliation = self._validate_audit_reconciliation_result(
                    run,
                    result,
                    persisted,
                    expected_effect_actions=expected_effect_actions,
                    recovery_event_start=recovery_event_start,
                    completed_before_recovery=completed_before_recovery,
                )
            result = result.model_copy(
                update={
                    "reconciliation": tuple(
                        reconciliation[index] for index in sorted(reconciliation)
                    )
                }
            )
        elif run.role is AgentRole.AUDIT and recovery_phase == "execute":
            self._validate_audit_recovery_execution_result(
                run,
                result,
                persisted,
                expected_effect_actions=expected_effect_actions,
                recovery_started_actions=recovery_started_actions,
                authorized_recovery_actions=authorized_recovery_actions,
            )
        elif run.role is AgentRole.AUDIT:
            self._validate_audit_result(
                run,
                result,
                persisted,
                expected_effect_actions=expected_effect_actions,
                required_skill_receipts=(
                    () if recovery_phase == "reconcile" else required_skill_receipts
                ),
                turn_event_start=turn_event_start,
            )
        claude_business_failure = outcome in {
            ConsumerOutcome.FAILED,
            AuditOutcome.FAILED,
            AuditOutcome.UNKNOWN,
        } or (recovery_phase == "execute" and outcome is not AuditOutcome.EXECUTED)
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
                    recovery_phase=recovery_phase,
                    result=cast(ConsumerAgentResult | AuditAgentResult, result),
                    result_reference_run_id=(
                        run.id if durable_consumer_result else None
                    ),
                    evidence=_runtime_result_evidence(
                        run=cast(
                            AgentRun,
                            self.store.get_agent_run(run.id),
                        ),
                        event_start=(
                            recovery_event_start
                            if recover_unknown
                            else turn_event_start
                        ),
                        receipts=self.store.list_agent_execution_receipts(run.id),
                        recovery_started_actions=recovery_started_actions,
                        completed_before_recovery=completed_before_recovery,
                        recovery_authorizations=recovery_authorizations,
                    ),
                ),
                conversation_id=(
                    self.task.conversation_id
                    if run.role is AgentRole.CONSUMER and not recover_unknown
                    else ""
                ),
                route_name=route.name,
                conversation_contract_hash=conversation_contract_hash,
                agent_run_final_result=(
                    domain_result if durable_consumer_result else None
                ),
                agent_run_final_side_effect_state=(
                    side_effect_state.value if durable_consumer_result else "none"
                ),
                agent_run_transcript_end=(
                    transcript_end if durable_consumer_result else None
                ),
            )
        if recovery_phase == "reconcile":
            self.store.persist_unknown_agent_run_result(
                run.id,
                result.model_dump(mode="json"),
                owner=self.owner,
                transcript_end_line=transcript_end,
            )
        elif recovery_phase == "execute":
            if outcome is AuditOutcome.EXECUTED:
                self.store.complete_agent_run(
                    run.id,
                    result.model_dump(mode="json"),
                    owner=self.owner,
                    side_effect_state=SideEffectState.CONFIRMED.value,
                    transcript_end_line=transcript_end,
                    expected_status="unknown",
                )
            else:
                self._defer_unknown(
                    run,
                    getattr(result, "error").code or "audit_recovery_incomplete",
                )
        elif outcome in {ConsumerOutcome.FAILED, AuditOutcome.FAILED}:
            self.store.fail_agent_run(
                run.id,
                getattr(result, "error").model_dump(mode="json"),
                owner=self.owner,
                side_effect_state=side_effect_state.value,
                transcript_end_line=transcript_end,
            )
        elif outcome is AuditOutcome.UNKNOWN:
            self.store.mark_agent_run_unknown(
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
                side_effect_state=side_effect_state.value,
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
        recovery_phase: str,
        conversation_contract_hash: str,
    ) -> str | None:
        if recovery_phase:
            return None
        if role is AgentRole.AUDIT:
            return None
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
                effect_registry=self.effects,
                native_cli_classifier=self.native_cli,
            )
        return self.claude_adapter

    def _claude_read_only_policy(self) -> ClaudeCommandPolicy:
        reviewed = self.effects.reviewed_read_tools()
        exact_tools = tuple(
            sorted(
                f"mcp__{server}__{tool}"
                for server in ("agent_cli", "memory_connector")
                for tool in reviewed.get(server, ())
            )
        )
        if not exact_tools:
            raise RuntimeRouteUnavailableError("claude_reviewed_read_surface_missing")
        return ClaudeCommandPolicy.reviewed(
            mcp_tools=exact_tools,
            allow_native_cli=True,
        )

    @staticmethod
    def _validate_route_workload_boundary(
        route: RuntimeRoute,
        *,
        run: AgentRun,
        recovery_phase: str,
    ) -> None:
        if (
            route.runtime_kind is RuntimeKind.CLAUDE_CLI
            and run.role is AgentRole.AUDIT
            and recovery_phase != "reconcile"
        ):
            raise RuntimeRouteUnavailableError("claude_effectful_audit_ineligible")

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
        *,
        recovery_phase: str,
    ) -> AgentRuntimeAttempt:
        if recovery_phase:
            if source_session_id:
                raise ValueError("unknown recovery cannot resume a provider session")
            start_claim = self.store.claim_unknown_recovery_agent_runtime_attempt(
                run.id,
                route.name,
                route.runtime_kind.value,
                route.credential_mode.value,
                route.model,
                owner=self.owner,
            )
            if not start_claim.start_acquired:
                raise AgentRuntimeAttemptStartConflictError(
                    "unknown recovery runtime attempt was not atomically started"
                )
            return start_claim.attempt
        else:
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

    def _normalized_effect_event(
        self,
        payload: dict[str, object],
        *,
        read_only: bool,
        operation_id: str,
        require_recovery_authorization: bool = False,
        expected_message_text_digests: frozenset[str] = frozenset(),
        expected_message_rendered_text_digests: frozenset[str] = frozenset(),
        message_operation_started_at: str = "",
    ) -> dict[str, object] | None:
        if payload.get("type") not in {"item.started", "item.completed", "item.failed"}:
            return None
        item = payload.get("item")
        if not isinstance(item, dict):
            return None
        if item.get("type") == "command_execution":
            command = self.native_cli.classify(item)
            if command is None:
                return {
                    "type": str(payload["type"]),
                    "item": {
                        "type": "command_execution",
                        "id": str(item.get("id") or item.get("call_id") or ""),
                        "status": {"item.started": "in_progress", "item.completed": "completed", "item.failed": "failed"}[str(payload["type"])],
                        "metadata": {"effect": EffectKind.EFFECTFUL.value, "operation": "provider.command"},
                    },
                }
            status = {
                "item.started": "in_progress",
                "item.completed": "completed",
                "item.failed": "failed",
            }[str(payload["type"])]
            return {
                "type": str(payload["type"]),
                "item": {
                    "type": "command_execution",
                    "id": str(item.get("id") or item.get("call_id") or ""),
                    "status": status,
                    "metadata": {
                        "effect": EffectKind.READ_ONLY.value,
                        "capability": f"native_cli.{command.cli}",
                        "operation": command.command_path,
                        "operation_digest": command.command_digest,
                        "target_identifiers": command.target_identifiers,
                        "arguments_digest": _json_digest(
                            {"command": item.get("command")}
                        ),
                        "native_cli": command.cli,
                    },
                },
            }
        if item.get("type") != "mcp_tool_call":
            return None
        call = self.effects.classify(item)
        if call is None:
            return {
                "type": str(payload["type"]),
                "item": {
                    "type": "mcp_tool_call",
                    "id": str(item.get("id") or item.get("call_id") or ""),
                    "status": {"item.started": "in_progress", "item.completed": "completed", "item.failed": "failed"}[str(payload["type"])],
                    "metadata": {"effect": EffectKind.EFFECTFUL.value, "operation": "provider.tool"},
                },
            }
        operation = call.operation
        capability = call.server
        operation_digest = call.operation_digest
        target_identifiers = call.target_identifiers
        native_cli = ""
        authorization_id = ""
        validated_receipt: dict[str, object] | None = None
        skill_metadata: dict[str, str] | None = None
        controlled_receipt_failed = False
        if call.server == "agent_cli" and call.tool in {
            "execute_reviewed_read",
            "execute_reviewed_write",
        }:
            arguments = item.get("arguments")
            argv = arguments.get("argv") if isinstance(arguments, dict) else None
            if isinstance(arguments, dict):
                candidate_authorization = arguments.get("authorization_id")
                if isinstance(candidate_authorization, str):
                    authorization_id = candidate_authorization
            descriptor = describe_native_command(
                {"type": "command_execution", "argv": argv}
            )
            if descriptor is None:
                if read_only and call.tool == "execute_reviewed_read":
                    if payload.get("type") != "item.completed":
                        return None
                    failure_code = _agent_cli_tool_error(item.get("result"))
                    if failure_code:
                        return _failed_agent_cli_read_event(item, failure_code)
                raise AgentReadOnlyViolationError("agent_cli_command_invalid")
            capability = f"agent_cli.{descriptor.cli}"
            operation = descriptor.command_path
            operation_digest = descriptor.command_digest
            target_identifiers = descriptor.target_identifiers
            native_cli = descriptor.cli
            if payload.get("type") == "item.completed":
                receipt = _agent_cli_receipt(
                    item.get("result"),
                    allow_error=True,
                )
                if receipt is None:
                    failure_code = _agent_cli_tool_error(item.get("result"))
                    if (
                        read_only
                        and call.tool == "execute_reviewed_read"
                        and failure_code
                    ):
                        return _failed_agent_cli_read_event(item, failure_code)
                    raise AgentReadOnlyViolationError(
                        failure_code or "agent_cli_receipt_missing"
                    )
                if receipt.get("operation") != descriptor.command_path:
                    raise AgentReadOnlyViolationError(
                        "agent_cli_receipt_operation_mismatch"
                    )
                if receipt.get("operation_digest") != operation_digest:
                    raise AgentReadOnlyViolationError(
                        "agent_cli_receipt_digest_mismatch"
                    )
                if receipt.get("target_identifiers") != target_identifiers:
                    raise AgentReadOnlyViolationError(
                        "agent_cli_receipt_target_mismatch"
                    )
                if (
                    require_recovery_authorization
                    and call.effect is EffectKind.EFFECTFUL
                    and (
                        not authorization_id
                        or receipt.get("authorization_id") != authorization_id
                    )
                ):
                    raise AgentReadOnlyViolationError(
                        "agent_cli_receipt_authorization_mismatch"
                    )
                validated_receipt = receipt
                controlled_receipt_failed = (
                    "error" in receipt or item.get("status") != "completed"
                )
        elif call.server == "agent_cli" and call.tool == "read_skill":
            if payload.get("type") == "item.completed":
                skill_metadata = normalized_read_skill_metadata(
                    item.get("arguments"), item.get("result")
                )
                controlled_receipt_failed = (
                    skill_metadata is None or item.get("status") != "completed"
                )
        event_type = str(payload["type"])
        if controlled_receipt_failed:
            event_type = "item.failed"
        elif (
            event_type == "item.completed"
            and validated_receipt is None
            and skill_metadata is None
            and not _mcp_call_completed(payload)
        ):
            event_type = "item.failed"
        status = {
            "item.started": "in_progress",
            "item.completed": "completed",
            "item.failed": "failed",
        }[event_type]
        metadata: dict[str, object] = {
            "effect": call.effect.value,
            "capability": capability,
            "operation": operation,
            "reviewed_server": call.server,
            "reviewed_tool": call.tool,
            "operation_digest": operation_digest,
            "target_identifiers": target_identifiers,
            "arguments_digest": _json_digest(
                {"argv": argv} if native_cli else item.get("arguments")
            ),
        }
        if call.effect is EffectKind.EFFECTFUL:
            metadata["operation_id"] = operation_id
        if call.server == "agent_cli" and call.tool == "read_skill":
            requested_skill_path = _requested_skill_path(item.get("arguments"))
            if requested_skill_path:
                metadata["requested_skill_path"] = requested_skill_path
        if controlled_receipt_failed and validated_receipt is not None:
            receipt_error = validated_receipt.get("error")
            if isinstance(receipt_error, dict):
                failure_code = receipt_error.get("code")
                if isinstance(failure_code, str) and failure_code:
                    metadata["failure_code"] = failure_code
                failure_retryable = receipt_error.get("retryable")
                if isinstance(failure_retryable, bool):
                    metadata["failure_retryable"] = failure_retryable
                failure_gate_state = receipt_error.get("gate_state")
                if isinstance(failure_gate_state, str) and failure_gate_state:
                    metadata["failure_gate_state"] = failure_gate_state
                failure_detail = receipt_error.get("detail")
                if isinstance(failure_detail, str) and failure_detail:
                    metadata["failure_detail"] = failure_detail
        if controlled_receipt_failed and call.tool == "read_skill":
            metadata["failure_code"] = "agent_cli_skill_receipt_invalid"
        if event_type == "item.completed" and skill_metadata is not None:
            metadata.update(skill_metadata)
        if event_type == "item.completed":
            result_digest = (
                validated_receipt.get("result_digest")
                if validated_receipt is not None
                else _json_digest(item.get("result"))
            )
            if isinstance(result_digest, str) and result_digest:
                metadata["result_digest"] = result_digest
            result_identifiers = self.effects.result_identifiers(
                server=call.server,
                tool=call.tool,
                operation=operation,
                result=(
                    validated_receipt
                    if validated_receipt is not None
                    else item.get("result")
                ),
            )
            if result_identifiers:
                metadata["result_identifiers"] = result_identifiers
            if validated_receipt is not None:
                metadata.update(
                    _dingtalk_message_readback_proof(
                        validated_receipt,
                        native_cli=native_cli,
                        operation=operation,
                        expected_message_text_digests=(expected_message_text_digests),
                        expected_message_rendered_text_digests=(
                            expected_message_rendered_text_digests
                        ),
                        operation_started_at=message_operation_started_at,
                    )
                )
        elif controlled_receipt_failed and validated_receipt is not None:
            result_digest = validated_receipt.get("result_digest")
            if isinstance(result_digest, str) and result_digest:
                metadata["result_digest"] = result_digest
        if native_cli:
            metadata["native_cli"] = native_cli
        if authorization_id:
            metadata["authorization_id"] = authorization_id
        return {
            "type": event_type,
            "item": {
                "type": "mcp_tool_call",
                "id": str(item.get("id") or item.get("call_id") or ""),
                "server": call.server,
                "tool": call.tool,
                "status": status,
                "metadata": metadata,
            },
        }

    def _record_direct_send_receipt(
        self,
        event: dict[str, object],
        payload: dict[str, object],
        *,
        run: AgentRun,
    ) -> None:
        """Persist the service delivery fact for a completed reviewed chat send."""
        if event.get("type") != "item.completed":
            return
        event_item = event.get("item")
        metadata = event_item.get("metadata") if isinstance(event_item, dict) else None
        raw_item = payload.get("item")
        arguments = raw_item.get("arguments") if isinstance(raw_item, dict) else None
        argv = native_command_argv(
            {"type": "command_execution", "argv": arguments.get("argv")}
            if isinstance(arguments, dict)
            else {}
        )
        if not isinstance(
            metadata, dict
        ) or not self._is_recordable_dingtalk_chat_delivery(metadata, argv):
            return
        reply_text = _dingtalk_message_text(argv)
        if not reply_text or self.store.has_sent_reply_for_trigger(
            self.task.conversation_id, self.task.trigger_message_id
        ):
            return
        self.store.record_sent_reply(
            self.task.conversation_id,
            self.task.trigger_message_id,
            reply_text,
            send_result_json=json.dumps(
                {
                    "agent_run_id": run.id,
                    "operation_id": run.operation_id,
                    "operation_digest": metadata.get("operation_digest", ""),
                    "result_digest": metadata.get("result_digest", ""),
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        )

    def _is_recordable_dingtalk_chat_delivery(
        self,
        metadata: dict[str, object],
        argv: tuple[str, ...] | None,
    ) -> bool:
        if _is_dingtalk_chat_send_argv(metadata, argv):
            return True
        recipient = _command_option_value(argv, "--to")
        return (
            metadata.get("operation") == "chat +dm"
            and bool(recipient)
            and bool(_dingtalk_message_text(argv))
        )

    def _require_direct_send_receipt(
        self,
        run: AgentRun,
        expected_effect_actions: tuple[dict[str, object], ...],
    ) -> None:
        if not any(
            _is_expected_dingtalk_chat_send(action)
            for action in expected_effect_actions
        ):
            return
        if self.store.has_sent_reply_for_trigger(
            self.task.conversation_id, self.task.trigger_message_id
        ):
            return
        self.store.mark_agent_run_unknown(
            run.id,
            {"code": "audit_delivery_ledger_missing", "retryable": True},
            owner=self.owner,
        )
        raise RuntimeError("audit_delivery_ledger_missing")

    def _validate_audit_result(
        self,
        run: AgentRun,
        result: ResultT,
        persisted: AgentRun,
        *,
        expected_effect_actions: tuple[dict[str, object], ...],
        required_skill_receipts: tuple[LoadedSkillReceipt, ...],
        turn_event_start: int,
    ) -> None:
        outcome = getattr(result, "outcome")
        turn_events = persisted.tool_events[turn_event_start:]
        observed_receipts = loaded_skill_receipts(turn_events)
        observed_by_identity = {
            (receipt.name, receipt.path): receipt.sha256
            for receipt in observed_receipts
        }
        missing_receipts = tuple(
            receipt
            for receipt in required_skill_receipts
            if (receipt.name, receipt.path) not in observed_by_identity
        )
        mismatched_receipts = tuple(
            receipt
            for receipt in required_skill_receipts
            if observed_by_identity.get((receipt.name, receipt.path))
            not in {None, receipt.sha256}
        )
        attempted_paths = _attempted_skill_paths(turn_events)
        unreadable_receipts = tuple(
            receipt for receipt in missing_receipts if receipt.path in attempted_paths
        )
        absent_receipts = tuple(
            receipt
            for receipt in missing_receipts
            if receipt.path not in attempted_paths
        )
        if absent_receipts or (
            unreadable_receipts and outcome is not AuditOutcome.REVISION_REQUIRED
        ):
            self._fail_running(run, "audit_skill_reread_missing")
            raise AgentReadOnlyViolationError("audit_skill_reread_missing")
        if mismatched_receipts and outcome is not AuditOutcome.REVISION_REQUIRED:
            self._fail_running(run, "audit_skill_reread_mismatch")
            raise AgentReadOnlyViolationError("audit_skill_reread_mismatch")
        if getattr(result, "proposal_revision") != run.proposal_revision:
            self._fail_running(run, "audit_proposal_revision_mismatch")
            raise RuntimeError("audit_proposal_revision_mismatch")
        if getattr(result, "reconciliation", ()):
            self._fail_running(run, "audit_reconciliation_unexpected")
            raise RuntimeError("audit_reconciliation_unexpected")
        if outcome is AuditOutcome.EXECUTED:
            external_result = getattr(result, "external_result")
            if external_result.operation_id != run.operation_id:
                self._fail_running(run, "audit_operation_mismatch")
                raise RuntimeError("audit_operation_mismatch")
            completed, all_effects_closed = _action_completion_accounting(
                persisted.tool_events,
                self.store.list_agent_execution_receipts(run.id),
                expected_effect_actions,
                operation_id=run.operation_id,
                registry=self.effects,
            )
            if (
                completed == set(range(len(expected_effect_actions)))
                and all_effects_closed
            ):
                if not _actions_have_required_readbacks(
                    persisted.tool_events,
                    expected_effect_actions,
                    self.effects,
                ):
                    self.store.mark_agent_run_unknown(
                        run.id,
                        {"code": "audit_external_readback_missing", "retryable": True},
                        owner=self.owner,
                    )
                    raise RuntimeError("audit_external_readback_missing")
                self._require_direct_send_receipt(run, expected_effect_actions)
                return
            code = (
                "audit_execution_evidence_missing"
                if persisted.side_effect_state == SideEffectState.NONE.value
                else "audit_execution_evidence_mismatch"
            )
            if persisted.side_effect_state != SideEffectState.NONE.value:
                self.store.mark_agent_run_unknown(
                    run.id,
                    {"code": code, "retryable": True},
                    owner=self.owner,
                )
            else:
                self.store.fail_agent_run(
                    run.id,
                    {"code": code, "retryable": True},
                    owner=self.owner,
                )
            raise RuntimeError(code)
        if (
            outcome is not AuditOutcome.UNKNOWN
            and persisted.side_effect_state != SideEffectState.NONE.value
        ):
            self.store.mark_agent_run_unknown(
                run.id,
                {"code": "audit_effect_without_executed_result", "retryable": True},
                owner=self.owner,
            )
            raise RuntimeError("audit_effect_without_executed_result")

    def _validate_audit_reconciliation_result(
        self,
        run: AgentRun,
        result: ResultT,
        persisted: AgentRun,
        *,
        expected_effect_actions: tuple[dict[str, object], ...],
        recovery_event_start: int,
        completed_before_recovery: set[int],
    ) -> dict[int, AuditReconciliation]:
        outcome = getattr(result, "outcome")
        if getattr(result, "proposal_revision") != run.proposal_revision:
            raise RuntimeError("audit_proposal_revision_mismatch")
        if outcome is not AuditOutcome.RECONCILED:
            raise RuntimeError("audit_reconciliation_result_invalid")
        reconciliation = _validated_reconciliation(
            getattr(result, "reconciliation"),
            persisted.tool_events,
            expected_effect_actions,
            event_start=recovery_event_start,
            registry=self.effects,
        )
        # A completed tool lifecycle event is not enough to suppress recovery.
        # This run is explicitly ``unknown`` because the original write has no
        # durable receipt. Only a receipt that existed before the recovery turn
        # can prove the action was already reconciled; a receipt produced during
        # this turn must not suppress the readback required for that same turn.
        completed_by_events = completed_before_recovery
        required = {
            index
            for index, action in enumerate(expected_effect_actions)
            if index not in completed_by_events
            and _action_has_readback(action, self.effects)
        }
        if set(reconciliation) != required:
            raise RuntimeError("audit_recovery_evidence_missing")
        present = {
            index
            for index, entry in reconciliation.items()
            if entry.disposition is ReconciliationDisposition.PRESENT
        }
        receipts = self.store.list_agent_execution_receipts(run.id)
        for action_index in sorted(present - completed_by_events):
            action = expected_effect_actions[action_index]
            metadata = _matching_effect_metadata(
                persisted.tool_events,
                action,
                operation_id=run.operation_id,
                event_type="item.started",
                action_index=action_index,
            )
            read_result_digest = reconciliation[action_index].read_result_digest
            if metadata is None or not read_result_digest:
                continue
            persisted_index = metadata.get("action_index")
            if persisted_index is None:
                if not self.store.bind_legacy_unknown_effect_action(
                    run.id,
                    action_index=action_index,
                    operation_id=run.operation_id,
                    expected_identity=action,
                    owner=self.owner,
                ):
                    continue
                metadata["action_index"] = action_index
            command_digest = metadata.get("operation_digest")
            if command_digest != action.get("operation_digest"):
                continue
            receipt_operation_id = _action_receipt_operation_id(
                run.operation_id, action, action_index
            )
            if not any(
                getattr(receipt, "operation_id", "") == receipt_operation_id
                for receipt in receipts
            ):
                self.store.record_agent_execution_receipt(
                    run.id,
                    receipt_id=(
                        f"reconciliation:{receipt_operation_id}:{read_result_digest}"
                    ),
                    operation_id=receipt_operation_id,
                    cli=str(metadata.get("native_cli") or metadata.get("capability")),
                    command_path=str(metadata.get("operation")),
                    command_digest=str(command_digest),
                    exit_code=0,
                    owner=self.owner,
                    expected_status="unknown",
                )
            self.store.confirm_agent_execution_receipt(
                run.id,
                receipt_operation_id,
                owner=self.owner,
            )
        receipt_completed = _action_completion_accounting(
            persisted.tool_events,
            self.store.list_agent_execution_receipts(run.id),
            expected_effect_actions,
            operation_id=run.operation_id,
            registry=self.effects,
        )[0]
        for action_index, action in enumerate(expected_effect_actions):
            if (
                action_index in receipt_completed
                and action_index not in completed_by_events
                and not _action_has_readback(action, self.effects)
            ):
                self.store.bind_legacy_unknown_effect_action(
                    run.id,
                    action_index=action_index,
                    operation_id=run.operation_id,
                    expected_identity=action,
                    owner=self.owner,
                )
                self.store.confirm_agent_execution_receipt(
                    run.id,
                    _action_receipt_operation_id(
                        run.operation_id, action, action_index
                    ),
                    owner=self.owner,
                )
        return reconciliation

    def _validate_audit_recovery_execution_result(
        self,
        run: AgentRun,
        result: ResultT,
        persisted: AgentRun,
        *,
        expected_effect_actions: tuple[dict[str, object], ...],
        recovery_started_actions: set[int],
        authorized_recovery_actions: frozenset[int],
    ) -> None:
        if getattr(result, "proposal_revision") != run.proposal_revision:
            raise RuntimeError("audit_proposal_revision_mismatch")
        if getattr(result, "outcome") is not AuditOutcome.EXECUTED:
            raise RuntimeError("audit_recovery_result_invalid")
        if getattr(result, "reconciliation"):
            raise RuntimeError("audit_reconciliation_unexpected")
        external_result = getattr(result, "external_result")
        if external_result.operation_id != run.operation_id:
            raise RuntimeError("audit_operation_mismatch")
        if recovery_started_actions != set(authorized_recovery_actions):
            raise RuntimeError("audit_recovery_effect_unresolved")
        completed, all_effects_closed = _action_completion_accounting(
            persisted.tool_events,
            self.store.list_agent_execution_receipts(run.id),
            expected_effect_actions,
            operation_id=run.operation_id,
            registry=self.effects,
        )
        if (
            completed != set(range(len(expected_effect_actions)))
            or not all_effects_closed
        ):
            raise RuntimeError("audit_execution_evidence_missing")
        if not _actions_have_required_readbacks(
            persisted.tool_events,
            expected_effect_actions,
            self.effects,
        ):
            raise RuntimeError("audit_external_readback_missing")
        self._require_direct_send_receipt(run, expected_effect_actions)

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

    def _fail_running(self, run: AgentRun, code: str, *, detail: str = "") -> None:
        persisted = self.store.get_agent_run(run.id)
        if persisted is not None and persisted.status == "running":
            if persisted.side_effect_state == SideEffectState.NONE.value:
                terminal_auth_failure = _is_terminal_codex_auth_failure(code)
                self.store.fail_agent_run(
                    run.id,
                    {
                        "code": code,
                        "retryable": not terminal_auth_failure,
                        "authorization_required": False,
                        **({"detail": detail} if detail else {}),
                    },
                    owner=self.owner,
                )
            elif failure := _closed_effect_failure(persisted, fallback_code=code):
                self.store.fail_agent_run(
                    run.id,
                    {
                        **failure,
                        **({"detail": detail} if detail else {}),
                    },
                    owner=self.owner,
                    side_effect_state=SideEffectState.CONFIRMED.value,
                )
            else:
                self.store.mark_agent_run_unknown(
                    run.id,
                    {"code": code, "retryable": True},
                    owner=self.owner,
                )

    def _defer_unknown(self, run: AgentRun, code: str) -> None:
        persisted = self.store.get_agent_run(run.id)
        if persisted is None or persisted.lease_owner != self.owner:
            return
        error = {
            "code": code,
            "retryable": True,
        }
        if persisted.status == "running":
            self.store.fail_agent_run(run.id, error, owner=self.owner)


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


def _is_dingtalk_chat_send(metadata: dict[str, object]) -> bool:
    target = metadata.get("target_identifiers")
    return (
        metadata.get("effect") == EffectKind.EFFECTFUL.value
        and metadata.get("capability") == "agent_cli.dws"
        and isinstance(target, dict)
        and any(
            isinstance(target.get(key), str) and target[key]
            for key in (
                "group",
                "user",
                "open-dingtalk-id",
                "conversation-id",
                "conversation",
            )
        )
    )


def _is_dingtalk_chat_send_argv(
    metadata: dict[str, object],
    argv: tuple[str, ...] | None,
) -> bool:
    operation = metadata.get("operation")
    return (
        _is_dingtalk_chat_send(metadata)
        and argv is not None
        and len(argv) >= 3
        and argv[0] == "dws"
        and isinstance(operation, str)
        and operation.startswith("chat ")
        and bool(_dingtalk_message_text(argv))
    )


def _is_expected_dingtalk_chat_send(action: dict[str, object]) -> bool:
    target = action.get("readback_target_identifiers")
    if not isinstance(target, dict):
        target = action.get("target_identifiers")
    return (
        action.get("capability") == "agent_cli.dws"
        and str(action.get("operation") or "").startswith("chat ")
        and isinstance(action.get("message_text_digest"), str)
        and isinstance(target, dict)
        and any(
            isinstance(target.get(key), str) and target[key]
            for key in (
                "group",
                "user",
                "open-dingtalk-id",
                "conversation-id",
                "conversation",
            )
        )
    )


def _command_option_value(
    argv: tuple[str, ...] | None,
    option: str,
) -> str:
    if argv is None:
        return ""
    try:
        index = argv.index(option)
    except ValueError:
        return ""
    if index + 1 >= len(argv):
        return ""
    value = argv[index + 1]
    return value if value and not value.startswith("--") else ""


def _dingtalk_message_text(argv: tuple[str, ...] | None) -> str:
    return dingtalk_message_text(argv)


def _closed_effect_failure(
    run: AgentRun,
    *,
    fallback_code: str,
) -> dict[str, object] | None:
    if (
        run.effect_started_count <= 0
        or run.effect_failed_count <= 0
        or run.effect_unreviewed_count
        or run.effect_started_count
        > run.effect_completed_count
        + run.effect_failed_count
        + run.effect_receipt_count
    ):
        return None
    for event in reversed(run.tool_events):
        if event.get("type") != "item.failed":
            continue
        item = event.get("item")
        metadata = item.get("metadata") if isinstance(item, dict) else None
        if not isinstance(metadata, dict) or metadata.get("effect") != "effectful":
            continue
        failure_code = metadata.get("failure_code")
        return {
            "code": (
                failure_code
                if isinstance(failure_code, str) and failure_code
                else fallback_code
            ),
            "retryable": bool(metadata.get("failure_retryable", False)),
            "authorization_required": False,
        }
    return None


def _session_id(payload: dict[str, object]) -> str:
    if payload.get("type") not in {"thread.started", "thread_started"}:
        return ""
    for key in ("thread_id", "session_id"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _agent_cli_receipt(
    value: object, *, allow_error: bool = False
) -> dict[str, object] | None:
    if isinstance(value, str):
        try:
            encoded_size = len(value.encode("utf-8"))
        except (UnicodeError, MemoryError):
            return None
        if encoded_size > 64 * 1024:
            return None
        encoded = value.strip()
        if not encoded.startswith(("{", "[")):
            marker = "\nOutput:\n"
            if marker not in encoded:
                return None
            _timing, encoded = encoded.rsplit(marker, 1)
            encoded = encoded.strip()
        try:
            decoded = json.loads(encoded)
        except (json.JSONDecodeError, ValueError, RecursionError, MemoryError):
            return None
        return _agent_cli_receipt(decoded, allow_error=allow_error)
    if isinstance(value, list):
        for block in value:
            if not isinstance(block, dict) or block.get("type") != "text":
                continue
            receipt = _agent_cli_receipt(block.get("text"), allow_error=allow_error)
            if receipt is not None:
                return receipt
        return None
    receipt = _controlled_cli_receipt(value)
    if receipt is not None or not isinstance(value, dict):
        return receipt
    candidates: list[object] = [
        value.get("structuredContent"),
        value.get("structured_content"),
    ]
    content = value.get("content")
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "text":
                continue
            text = block.get("text")
            if not isinstance(text, str) or len(text.encode("utf-8")) > 64 * 1024:
                continue
            try:
                candidates.append(json.loads(text))
            except json.JSONDecodeError:
                continue
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        receipt = _controlled_cli_receipt(candidate)
        if receipt is not None and (
            allow_error or not isinstance(candidate.get("error"), dict)
        ):
            return receipt
        if allow_error and all(
            isinstance(candidate.get(key), expected)
            for key, expected in (
                ("result_digest", str),
                ("operation_digest", str),
                ("operation", str),
                ("target_identifiers", dict),
                ("error", dict),
            )
        ):
            return candidate
    return None


def _agent_cli_tool_error(value: object) -> str:
    if isinstance(value, str):
        if len(value.encode("utf-8")) > 64 * 1024:
            return ""
        text = value.strip()
        marker = "\nOutput:\n"
        if not text.startswith(("{", "[")) and marker in text:
            _timing, text = text.rsplit(marker, 1)
            text = text.strip()
        if text.startswith(("{", "[")):
            try:
                return _agent_cli_tool_error(json.loads(text))
            except (json.JSONDecodeError, ValueError, RecursionError, MemoryError):
                return ""
        prefix = "Error executing tool "
        if not text.startswith(prefix) or ": " not in text:
            return ""
        code = text.rsplit(": ", 1)[-1].strip()
        return code if code.replace("_", "").isalnum() else ""
    if isinstance(value, list):
        for block in value:
            if not isinstance(block, dict) or block.get("type") != "text":
                continue
            code = _agent_cli_tool_error(block.get("text"))
            if code:
                return code
    if isinstance(value, dict):
        for key in ("structuredContent", "structured_content"):
            code = _agent_cli_tool_error(value.get(key))
            if code:
                return code
        content = value.get("content")
        if isinstance(content, list):
            return _agent_cli_tool_error(content)
    return ""


def _failed_agent_cli_read_event(
    item: dict[str, object], failure_code: str
) -> dict[str, object]:
    arguments = item.get("arguments")
    argv = arguments.get("argv") if isinstance(arguments, dict) else None
    return {
        "type": "item.failed",
        "item": {
            "type": "mcp_tool_call",
            "id": str(item.get("id") or item.get("call_id") or ""),
            "server": "agent_cli",
            "tool": "execute_reviewed_read",
            "status": "failed",
            "metadata": {
                "effect": EffectKind.READ_ONLY.value,
                "capability": "agent_cli",
                "operation": "",
                "reviewed_server": "agent_cli",
                "reviewed_tool": "execute_reviewed_read",
                "operation_digest": "",
                "target_identifiers": {},
                "arguments_digest": _json_digest({"argv": argv}),
                "failure_code": failure_code,
            },
        },
    }


def _matching_effect_metadata(
    events: list[dict[str, object]],
    expected_action: dict[str, object],
    *,
    operation_id: str,
    event_type: str,
    action_index: int,
) -> dict[str, object] | None:
    for event in events:
        if event.get("type") != event_type:
            continue
        item = event.get("item")
        metadata = item.get("metadata") if isinstance(item, dict) else None
        if not isinstance(metadata, dict):
            continue
        if (
            metadata.get("effect") == EffectKind.EFFECTFUL.value
            and metadata.get("operation_id") == operation_id
            and metadata.get("action_index") in {None, action_index}
            and _metadata_matches_action(metadata, expected_action)
        ):
            return metadata
    return None


def _event_metadata(event: dict[str, object]) -> dict[str, object] | None:
    item = event.get("item")
    metadata = item.get("metadata") if isinstance(item, dict) else None
    return metadata if isinstance(metadata, dict) else None


def _metadata_matches_action(
    metadata: dict[str, object],
    action: dict[str, object],
) -> bool:
    if action.get("operation_contract_valid") is False:
        return False
    identity_matches = all(
        metadata.get(key) == action.get(key)
        for key in (
            "capability",
            "arguments_digest",
            "target_identifiers",
        )
    )
    expected_command_digest = action.get("operation_digest")
    if expected_command_digest is not None:
        return (
            identity_matches
            and metadata.get("operation_digest") == expected_command_digest
        )
    return identity_matches and metadata.get("operation") == action.get("operation")


def _recovery_execution_result_from_receipts(
    *,
    run: AgentRun,
    recovery_phase: str,
    persisted: AgentRun | None,
    expected_effect_actions: tuple[dict[str, object], ...],
    recovery_started_actions: set[int],
    authorized_recovery_actions: frozenset[int],
    registry: McpToolEffectRegistry,
    store: AutoReplyStore,
) -> AuditAgentResult | None:
    """Finish a recovery only when the controlled write receipts are complete."""
    if (
        recovery_phase != "execute"
        or run.role is not AgentRole.AUDIT
        or persisted is None
        or not authorized_recovery_actions
        or recovery_started_actions != set(authorized_recovery_actions)
    ):
        return None
    completed, all_effects_closed = _action_completion_accounting(
        persisted.tool_events,
        store.list_agent_execution_receipts(run.id),
        expected_effect_actions,
        operation_id=run.operation_id,
        registry=registry,
    )
    if completed != set(authorized_recovery_actions) or not all_effects_closed:
        return None
    return AuditAgentResult(
        outcome=AuditOutcome.EXECUTED,
        summary="Authorized recovery actions completed with controlled receipts.",
        proposal_revision=run.proposal_revision,
        side_effect_state=SideEffectState.CONFIRMED,
        feedback=None,
        external_result=AuditExternalResult(
            operation_id=run.operation_id,
            verification_summary="Controlled recovery receipts completed.",
            live_result_reference={
                "recovery_action_indexes": sorted(authorized_recovery_actions),
                "evidence": "controlled_receipts",
            },
        ),
        reconciliation=(),
        error=AgentError(),
    )


def _conservative_reconciliation_result_from_readbacks(
    *,
    run: AgentRun,
    persisted: AgentRun,
    expected_effect_actions: tuple[dict[str, object], ...],
    recovery_event_start: int,
    completed_before_recovery: set[int],
    registry: McpToolEffectRegistry,
) -> AuditAgentResult | None:
    """Preserve unknown state when durable target-matched reads outlive bad model JSON."""
    entries: list[AuditReconciliation] = []
    for action_index, action in enumerate(expected_effect_actions):
        if action_index in completed_before_recovery or not _action_has_readback(
            action, registry
        ):
            continue
        digest = _matching_read_digest(
            persisted.tool_events,
            action,
            event_start=recovery_event_start,
            registry=registry,
        )
        if not digest:
            return None
        entries.append(
            AuditReconciliation(
                action_index=action_index,
                disposition=ReconciliationDisposition.AMBIGUOUS,
                read_result_digest=digest,
            )
        )
    if not entries:
        return None
    return AuditAgentResult(
        outcome=AuditOutcome.RECONCILED,
        summary=(
            "Controlled target-matched live readbacks completed, but the recovery "
            "result could not safely classify the prior external action."
        ),
        proposal_revision=run.proposal_revision,
        side_effect_state=SideEffectState.UNKNOWN,
        feedback=None,
        external_result=None,
        reconciliation=tuple(entries),
        error=AgentError(),
    )


def _read_matches_action(
    read: dict[str, object],
    action: dict[str, object],
    registry: McpToolEffectRegistry,
) -> bool:
    read_server = str(read.get("reviewed_server") or "")
    read_tool = str(read.get("reviewed_tool") or "")
    write_server = str(action.get("reviewed_server") or "")
    write_tool = str(action.get("reviewed_tool") or "")
    if not registry.can_readback(
        read_server=read_server,
        read_tool=read_tool,
        write_server=write_server,
        write_tool=write_tool,
    ):
        return False
    if not registry.readback_operations_match(
        read_server=read_server,
        read_tool=read_tool,
        write_server=write_server,
        write_tool=write_tool,
        read_operation=str(read.get("operation") or ""),
        write_operation=str(action.get("operation") or ""),
    ):
        return False
    read_target = read.get("target_identifiers")
    action_target = action.get("readback_target_identifiers")
    if not isinstance(action_target, dict):
        action_target = action.get("target_identifiers")
    if not isinstance(read_target, dict) or not isinstance(action_target, dict):
        return False
    if not registry.readback_targets_match(
        read_server=read_server,
        read_tool=read_tool,
        write_server=write_server,
        write_tool=write_tool,
        read_targets=read_target,
        write_targets=action_target,
        read_operation=str(read.get("operation") or ""),
        write_operation=str(action.get("operation") or ""),
    ):
        return False
    read_result_identifiers = read.get("result_identifiers")
    write_result_identifiers = action.get("result_identifiers")
    return registry.readback_identities_match(
        read_server=read_server,
        read_tool=read_tool,
        write_server=write_server,
        write_tool=write_tool,
        read_operation=str(read.get("operation") or ""),
        write_operation=str(action.get("operation") or ""),
        read_targets=read_target,
        read_result_identifiers=(
            read_result_identifiers if isinstance(read_result_identifiers, dict) else {}
        ),
        write_result_identifiers=(
            write_result_identifiers
            if isinstance(write_result_identifiers, dict)
            else {}
        ),
    )


def _action_has_readback(
    action: dict[str, object],
    registry: McpToolEffectRegistry,
) -> bool:
    return registry.has_readback_for(
        write_server=str(action.get("reviewed_server") or ""),
        write_tool=str(action.get("reviewed_tool") or ""),
        write_operation=str(action.get("operation") or ""),
    )


def _matching_read_digest(
    events: list[dict[str, object]],
    action: dict[str, object],
    *,
    event_start: int = 0,
    after_index: int = -1,
    registry: McpToolEffectRegistry,
) -> str:
    readback_action = _readback_action_metadata(action, action)
    for index, event in enumerate(events):
        if (
            index < event_start
            or index <= after_index
            or event.get("type") != "item.completed"
        ):
            continue
        metadata = _event_metadata(event)
        if metadata is None or metadata.get("effect") != EffectKind.READ_ONLY.value:
            continue
        digest = metadata.get("result_digest")
        if (
            _read_matches_action(metadata, readback_action, registry)
            and _message_readback_matches_action(metadata, readback_action)
            and isinstance(digest, str)
        ):
            return digest
    return ""


def _actions_have_required_readbacks(
    events: list[dict[str, object]],
    actions: tuple[dict[str, object], ...],
    registry: McpToolEffectRegistry,
) -> bool:
    for action_index, action in enumerate(actions):
        if not _action_has_readback(action, registry):
            continue
        writes = [
            (index, metadata)
            for index, event in enumerate(events)
            if event.get("type") == "item.completed"
            and (metadata := _event_metadata(event)) is not None
            and metadata.get("effect") == EffectKind.EFFECTFUL.value
            and metadata.get("action_index") in {None, action_index}
            and _metadata_matches_action(metadata, action)
        ]
        if not writes:
            if registry.readback_requires_completed_write(
                write_server=str(action.get("reviewed_server") or ""),
                write_tool=str(action.get("reviewed_tool") or ""),
                write_operation=str(action.get("operation") or ""),
            ):
                return False
            writes = [(-1, action)]
        if any(
            not _matching_read_digest(
                events,
                _readback_action_metadata(write_metadata, action),
                after_index=write_index,
                registry=registry,
            )
            for write_index, write_metadata in writes
        ):
            return False
    return True


def _readback_action_metadata(
    write_metadata: dict[str, object],
    expected_action: dict[str, object],
) -> dict[str, object]:
    """Keep the actual write receipt, with a reviewed recipient for name-resolved DMs."""
    targets = expected_action.get("readback_target_identifiers")
    if not isinstance(targets, dict):
        return write_metadata
    return {**write_metadata, "target_identifiers": targets}


def _message_readback_matches_action(
    read_metadata: dict[str, object],
    action: dict[str, object],
) -> bool:
    """Require body evidence for message writes; target-only reads are unsafe."""
    if not _is_expected_dingtalk_chat_send(action):
        return True
    if read_metadata.get("message_readback_window_matches") is not True:
        return False
    expected_raw = action.get("message_text_digest")
    observed_raw = read_metadata.get("message_text_digests")
    if (
        isinstance(expected_raw, str)
        and isinstance(observed_raw, list)
        and expected_raw in observed_raw
    ):
        return True
    expected_rendered = action.get("message_rendered_text_digest")
    observed_rendered = read_metadata.get("message_rendered_text_digests")
    return (
        isinstance(expected_rendered, str)
        and isinstance(observed_rendered, list)
        and expected_rendered in observed_rendered
    )


def _validated_reconciliation(
    entries: tuple[AuditReconciliation, ...],
    events: list[dict[str, object]],
    actions: tuple[dict[str, object], ...],
    *,
    event_start: int,
    registry: McpToolEffectRegistry,
) -> dict[int, AuditReconciliation]:
    validated: dict[int, AuditReconciliation] = {}
    started_metadata_by_action: dict[int, dict[str, object]] = {}
    started_without_index: list[dict[str, object]] = []
    for event in events:
        if event.get("type") != "item.started":
            continue
        metadata = _event_metadata(event)
        if metadata is None or metadata.get("effect") != EffectKind.EFFECTFUL.value:
            continue
        action_index = metadata.get("action_index")
        if isinstance(action_index, int) and 0 <= action_index < len(actions):
            started_metadata_by_action.setdefault(action_index, metadata)
        else:
            started_without_index.append(metadata)
    for action_index, metadata in enumerate(started_without_index):
        if action_index >= len(actions):
            break
        started_metadata_by_action.setdefault(action_index, metadata)
    for entry in entries:
        action_index = entry.action_index
        if action_index >= len(actions):
            raise RuntimeError("audit_reconciliation_action_mismatch")
        matching_reads: list[dict[str, object]] = []
        for index, event in enumerate(events):
            if index < event_start or (
                event.get("type") != "item.completed"
                and not (
                    event.get("type") == "item.failed"
                    and entry.disposition is ReconciliationDisposition.AMBIGUOUS
                )
            ):
                continue
            metadata = _event_metadata(event)
            if (
                metadata is None
                or metadata.get("effect") != EffectKind.READ_ONLY.value
                or not isinstance(metadata.get("result_digest"), str)
            ):
                continue
            write_metadata = _latest_matching_write_metadata(
                events,
                actions[action_index],
                before_index=index,
            )
            if write_metadata is None:
                write_metadata = started_metadata_by_action.get(action_index)
            if write_metadata is None and entry.disposition is ReconciliationDisposition.ABSENT:
                expected_targets = actions[action_index].get("target_identifiers")
                if isinstance(expected_targets, dict):
                    for candidate in started_metadata_by_action.values():
                        candidate_targets = candidate.get("target_identifiers")
                        if (
                            isinstance(candidate_targets, dict)
                            and candidate_targets
                            and all(
                                expected_targets.get(key) == value
                                for key, value in candidate_targets.items()
                            )
                        ):
                            write_metadata = candidate
                            break
            if _read_matches_action(
                metadata,
                write_metadata or actions[action_index],
                registry,
            ):
                matching_reads.append(metadata)
        if not matching_reads:
            raise RuntimeError("audit_reconciliation_evidence_mismatch")
        cited_read = next(
            (
                metadata
                for metadata in matching_reads
                if metadata.get("result_digest") == entry.read_result_digest
            ),
            None,
        )
        if cited_read is None:
            raise RuntimeError("audit_reconciliation_evidence_mismatch")
        expected_text_digest = actions[action_index].get("message_text_digest")
        expected_rendered_text_digest = actions[action_index].get(
            "message_rendered_text_digest"
        )
        same_message_identity_count = sum(
            candidate.get("message_text_digest") == expected_text_digest
            and candidate.get("target_identifiers")
            == actions[action_index].get("target_identifiers")
            for candidate in actions
        )
        if (
            _is_expected_dingtalk_chat_send(actions[action_index])
            and isinstance(expected_text_digest, str)
            and same_message_identity_count == 1
        ):
            observed_text_digests = cited_read.get("message_text_digests")
            observed_rendered_text_digests = cited_read.get(
                "message_rendered_text_digests"
            )
            window_matches = cited_read.get("message_readback_window_matches") is True
            if (
                window_matches
                and isinstance(observed_text_digests, list)
                and expected_text_digest in observed_text_digests
            ) or (
                window_matches
                and isinstance(expected_rendered_text_digest, str)
                and isinstance(observed_rendered_text_digests, list)
                and expected_rendered_text_digest in observed_rendered_text_digests
            ):
                disposition = ReconciliationDisposition.PRESENT
            elif (
                window_matches
                and entry.disposition is ReconciliationDisposition.ABSENT
                and cited_read.get("message_readback_complete") is True
            ):
                disposition = ReconciliationDisposition.ABSENT
            else:
                disposition = ReconciliationDisposition.AMBIGUOUS
            entry = entry.model_copy(update={"disposition": disposition})
        validated[action_index] = entry
    return validated


def _latest_matching_write_metadata(
    events: list[dict[str, object]],
    action: dict[str, object],
    *,
    before_index: int,
) -> dict[str, object] | None:
    for index in range(before_index - 1, -1, -1):
        event = events[index]
        if event.get("type") != "item.completed":
            continue
        metadata = _event_metadata(event)
        if (
            metadata is not None
            and metadata.get("effect") == EffectKind.EFFECTFUL.value
            and _metadata_matches_action(metadata, action)
        ):
            return metadata
    return None


def _action_receipt_operation_id(
    operation_id: str,
    action: dict[str, object],
    action_index: int,
) -> str:
    return json.dumps(
        {
            "proposal_operation_id": operation_id,
            "action_index": action_index,
            "capability": action.get("capability", ""),
            "operation": action.get("operation", ""),
            "operation_digest": action.get("operation_digest", ""),
            "arguments_digest": action.get("arguments_digest", ""),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _action_completion_accounting(
    events: list[dict[str, object]],
    receipts: list[object],
    actions: tuple[dict[str, object], ...],
    *,
    operation_id: str,
    registry: McpToolEffectRegistry,
) -> tuple[set[int], bool]:
    starts: list[dict[str, object]] = []
    starts_per_action = [0] * len(actions)
    successes = [0] * len(actions)
    durable_completion_fingerprints: list[set[tuple[str, str, str, str]]] = [
        set() for _ in actions
    ]
    for event_index, event in enumerate(events):
        metadata = _event_metadata(event)
        if (
            metadata is None
            or metadata.get("effect") != EffectKind.EFFECTFUL.value
            or metadata.get("operation_id") != operation_id
        ):
            continue
        item = event.get("item")
        call_id = (
            str(item.get("id") or item.get("call_id") or "")
            if isinstance(item, dict)
            else ""
        )
        if event.get("type") == "item.started":
            candidates = [
                index
                for index, action in enumerate(actions)
                if _metadata_matches_action(metadata, action)
            ]
            persisted_index = metadata.get("action_index")
            action_index = (
                int(persisted_index)
                if isinstance(persisted_index, int) and persisted_index in candidates
                else min(candidates, key=starts_per_action.__getitem__)
                if candidates
                else None
            )
            if action_index is not None:
                action = actions[action_index]
                for prior in starts:
                    if (
                        not prior["closed"]
                        and prior["action_index"] == action_index
                        and _matching_read_digest(
                            events[:event_index],
                            action,
                            after_index=int(prior["event_index"]),
                            registry=registry,
                        )
                    ):
                        prior["closed"] = True
                starts_per_action[action_index] += 1
            starts.append(
                {
                    "call_id": call_id,
                    "action_index": action_index,
                    "event_index": event_index,
                    "closed": False,
                }
            )
            continue
        if event.get("type") not in {"item.completed", "item.failed"}:
            continue
        closure_action_index = metadata.get("action_index")
        start = next(
            (
                candidate
                for candidate in reversed(starts)
                if not candidate["closed"]
                and candidate["call_id"] == call_id
                and (
                    not isinstance(closure_action_index, int)
                    or candidate["action_index"] == closure_action_index
                )
                and (
                    candidate["action_index"] is None
                    or _metadata_matches_action(
                        metadata, actions[int(candidate["action_index"])]
                    )
                )
            ),
            None,
        )
        if start is None:
            continue
        start["closed"] = True
        action_index = start["action_index"]
        if event.get("type") == "item.completed" and action_index is not None:
            authorization_id = metadata.get("authorization_id")
            result_digest = metadata.get("result_digest")
            operation_digest = metadata.get("operation_digest")
            arguments_digest = metadata.get("arguments_digest")
            fingerprint = (
                authorization_id,
                operation_digest,
                arguments_digest,
                result_digest,
            )
            if (
                all(isinstance(value, str) and value for value in fingerprint)
                and fingerprint
                not in durable_completion_fingerprints[int(action_index)]
            ):
                durable_completion_fingerprints[int(action_index)].add(
                    cast(tuple[str, str, str, str], fingerprint)
                )
                successes[int(action_index)] += 1
            elif not all(isinstance(value, str) and value for value in fingerprint):
                successes[int(action_index)] += 1

    # An interrupted write may be followed by a later, independently recorded
    # successful write and its target-matched readback. The readback closes the
    # interrupted lifecycle as well; otherwise recovery keeps retrying an
    # already verified effect forever.
    for start in starts:
        action_index = start["action_index"]
        if (
            not start["closed"]
            and not any(
                other is not start and other["call_id"] == start["call_id"]
                for other in starts
            )
            and action_index is not None
            and _matching_read_digest(
                events,
                actions[int(action_index)],
                after_index=int(start["event_index"]),
                registry=registry,
            )
        ):
            start["closed"] = True

    used_receipts: set[int] = set()
    for action_index, action in enumerate(actions):
        expected_receipt_id = _action_receipt_operation_id(
            operation_id, action, action_index
        )
        expected_cli = str(action.get("capability") or "").rsplit(".", 1)[-1]
        receipt_index = next(
            (
                index
                for index, receipt in enumerate(receipts)
                if index not in used_receipts
                and getattr(receipt, "operation_id", "") == expected_receipt_id
                and getattr(receipt, "completed", False)
                and getattr(receipt, "persisted", False)
                and getattr(receipt, "safe_to_confirm", False)
                and getattr(receipt, "cli", "") == expected_cli
                and getattr(receipt, "command_path", "") == action.get("operation")
                and getattr(receipt, "command_digest", "")
                == action.get("operation_digest")
            ),
            None,
        )
        if receipt_index is None:
            continue
        used_receipts.add(receipt_index)
        open_start = next(
            (
                start
                for start in starts
                if not start["closed"] and start["action_index"] == action_index
            ),
            None,
        )
        if open_start is not None:
            open_start["closed"] = True
        # The durable receipt and completed event are two persistence views of
        # the same authorized write, not two external effects.
        if successes[action_index] == 0:
            successes[action_index] += 1

    # Multiple distinct completion fingerprints for one proposal action are
    # possible duplicate external writes and must prevent single-effect proof.
    completed = {
        index for index, success_count in enumerate(successes) if success_count == 1
    }
    return completed, all(bool(start["closed"]) for start in starts) and all(
        success_count <= 1 for success_count in successes
    )


def _json_digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _message_text_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


_MARKDOWN_RENDERER = MarkdownIt("commonmark", {"html": False})


def _message_rendered_text_digest(text: str) -> str:
    """Digest visible Markdown content, ignoring transport-only formatting."""
    visible: list[str] = []
    for token in _MARKDOWN_RENDERER.parse(text):
        children = token.children or ()
        for child in children:
            if child.type in {"text", "code_inline", "image"}:
                visible.append(child.content)
            elif child.type in {"softbreak", "hardbreak"}:
                visible.append(" ")
        if token.type in {"code_block", "fence"}:
            visible.append(token.content)
    normalized = unicodedata.normalize("NFC", "".join(visible))
    compact = " ".join(normalized.split())
    return _message_text_digest(compact)


def _dingtalk_message_readback_proof(
    receipt: dict[str, object],
    *,
    native_cli: str,
    operation: str,
    expected_message_text_digests: frozenset[str],
    operation_started_at: str,
    expected_message_rendered_text_digests: frozenset[str] = frozenset(),
) -> dict[str, object]:
    """Reduce a scoped DingTalk history read to privacy-safe content evidence."""
    if native_cli != "dws" or not operation.startswith("chat "):
        return {}
    stdout = receipt.get("stdout")
    if not isinstance(stdout, str) or not stdout:
        return {}
    try:
        payload = json.loads(stdout)
    except (json.JSONDecodeError, ValueError, RecursionError, MemoryError):
        return {}
    if not isinstance(payload, dict):
        return {}
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return {}
    matched: set[str] = set()
    rendered_matched: set[str] = set()
    for message in messages:
        if not isinstance(message, dict):
            continue
        text = message.get("text")
        message_id = message.get("messageId")
        conversation_id = message.get("conversationId")
        if (
            not isinstance(text, str)
            or not isinstance(message_id, str)
            or not message_id
            or not isinstance(conversation_id, str)
            or not conversation_id
        ):
            continue
        digest = _message_text_digest(text)
        if digest in expected_message_text_digests:
            matched.add(digest)
        rendered_digest = _message_rendered_text_digest(text)
        if rendered_digest in expected_message_rendered_text_digests:
            rendered_matched.add(rendered_digest)
    complete = (
        payload.get("complete") is True
        and payload.get("hasMore") is False
        and payload.get("paginationKnown") is True
        and payload.get("failures") == []
    )
    return {
        "message_readback_complete": complete,
        "message_readback_window_matches": _message_readback_window_matches(
            payload,
            operation_started_at=operation_started_at,
        ),
        "message_text_digests": sorted(matched),
        "message_rendered_text_digests": sorted(rendered_matched),
    }


def _message_readback_window_matches(
    payload: dict[str, object],
    *,
    operation_started_at: str,
) -> bool:
    query_range = payload.get("queryRange")
    if not isinstance(query_range, dict):
        return False
    start = _parse_reconciliation_timestamp(query_range.get("startTime"))
    end = _parse_reconciliation_timestamp(query_range.get("endTime"))
    operation_started = _parse_reconciliation_timestamp(operation_started_at)
    if start is None or end is None or operation_started is None:
        return False
    return start <= operation_started < end and timedelta(0) < end - start <= timedelta(
        hours=2
    )


def _parse_reconciliation_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _trusted_claude_effect_event(
    payload: dict[str, object], *, read_only: bool
) -> dict[str, object] | None:
    event_type = payload.get("type")
    if event_type not in {"item.started", "item.completed", "item.failed"}:
        return None
    item = payload.get("item")
    if isinstance(item, dict) and item.get("type") == "agent_message":
        return None
    if not isinstance(item, dict) or item.get("type") not in {
        "mcp_tool_call",
        "command_execution",
    }:
        raise AgentReadOnlyViolationError("claude_normalized_event_invalid")
    expected_status = {
        "item.started": "in_progress",
        "item.completed": "completed",
        "item.failed": "failed",
    }[str(event_type)]
    if item.get("status") != expected_status:
        raise AgentReadOnlyViolationError("claude_normalized_event_invalid")
    metadata = item.get("metadata")
    if not isinstance(metadata, dict) or not set(metadata).issubset(
        {
            "effect",
            "capability",
            "reviewed_server",
            "reviewed_tool",
            "operation",
            "operation_digest",
            "arguments_digest",
            "target_identifiers",
            "native_cli",
            "result_digest",
        }
    ):
        raise AgentReadOnlyViolationError("claude_normalized_event_invalid")
    effect = metadata.get("effect")
    if effect not in {EffectKind.READ_ONLY.value, EffectKind.EFFECTFUL.value}:
        raise AgentReadOnlyViolationError("claude_normalized_event_invalid")
    if read_only and effect != EffectKind.READ_ONLY.value:
        raise AgentReadOnlyViolationError("agent_write_forbidden")
    for key in ("capability", "operation", "operation_digest"):
        if not isinstance(metadata.get(key), str) or not metadata[key]:
            raise AgentReadOnlyViolationError("claude_normalized_event_invalid")
    arguments_digest = metadata.get("arguments_digest")
    if (
        not isinstance(arguments_digest, str)
        or len(arguments_digest) != 64
        or any(character not in "0123456789abcdef" for character in arguments_digest)
    ):
        raise AgentReadOnlyViolationError("claude_normalized_event_invalid")
    if not isinstance(metadata.get("target_identifiers"), dict):
        raise AgentReadOnlyViolationError("claude_normalized_event_invalid")
    result_digest = metadata.get("result_digest")
    if event_type == "item.completed" and (
        not isinstance(result_digest, str)
        or len(result_digest) != 64
        or any(character not in "0123456789abcdef" for character in result_digest)
    ):
        raise AgentReadOnlyViolationError("claude_normalized_event_invalid")
    normalized_item = {
        "type": item["type"],
        "id": str(item.get("id") or ""),
        "status": expected_status,
        "metadata": dict(metadata),
    }
    if item["type"] == "mcp_tool_call":
        server = item.get("server")
        tool = item.get("tool")
        if (
            not isinstance(server, str)
            or not server
            or not isinstance(tool, str)
            or not tool
        ):
            raise AgentReadOnlyViolationError("claude_normalized_event_invalid")
        normalized_item.update({"server": server, "tool": tool})
    return {"type": event_type, "item": normalized_item}


def _requested_skill_path(arguments: object) -> str:
    if not isinstance(arguments, dict) or set(arguments) != {"path"}:
        return ""
    path = arguments.get("path")
    if not isinstance(path, str) or not path or not Path(path).is_absolute():
        return ""
    try:
        return str(Path(path).resolve(strict=False))
    except (OSError, RuntimeError):
        return ""


def _attempted_skill_paths(
    events: tuple[dict[str, object], ...] | list[dict[str, object]],
) -> frozenset[str]:
    paths: set[str] = set()
    for event in events:
        if event.get("type") != "item.failed":
            continue
        item = event.get("item")
        metadata = item.get("metadata") if isinstance(item, dict) else None
        if not isinstance(metadata, dict):
            continue
        if (
            metadata.get("reviewed_server") != "agent_cli"
            or metadata.get("reviewed_tool") != "read_skill"
        ):
            continue
        path = metadata.get("requested_skill_path")
        if isinstance(path, str) and path:
            paths.add(path)
    return frozenset(paths)


def _contains_sensitive_value(value: object, *, depth: int = 0) -> bool:
    if depth > 12:
        return True
    if isinstance(value, dict):
        if _contains_sensitive_argv(value):
            return True
        return any(
            _is_sensitive_key(_normalized_key(str(key)))
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
    domain_result = result.model_dump(mode="json")
    _project_runtime_domain_result(result)
    _validate_runtime_reference_text_bounds(domain_result)
    sensitive_projection = domain_result
    if allow_configured_feedback_links:
        sensitive_projection = cast(
            dict[str, object],
            sanitize_configured_feedback_links(
                domain_result,
                vercel_base_url=feedback_spike_vercel_base_url(),
            ),
        )
    if _contains_sensitive_value(sensitive_projection):
        raise ValueError("agent_result_contains_sensitive_value")
    if _contains_local_runtime_value(domain_result):
        raise AgentReadOnlyViolationError("runtime_result_contains_local_runtime_leak")
    encoded = json.dumps(
        domain_result,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    if len(encoded.encode("utf-8")) > _RUNTIME_DOMAIN_RESULT_CODEC_MAX_BYTES:
        raise ValueError("runtime_result_reference_too_large")


def _contains_sensitive_argv(value: dict[object, object]) -> bool:
    argv = value.get("argv")
    if not isinstance(argv, list) or not all(isinstance(item, str) for item in argv):
        return False
    for token in argv:
        if not token.startswith("--"):
            continue
        flag = token[2:].partition("=")[0]
        if _is_sensitive_key(_normalized_key(flag)):
            return True
    return False
