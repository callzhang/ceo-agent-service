# The route-loop closures below are passed only to `_finalized_step`, which
# invokes them synchronously and never stores them beyond the current attempt.
# ruff: noqa: B023

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeVar
from zoneinfo import ZoneInfo

from app.agent_effects import IDLE_TIMEOUT_SECONDS, TOTAL_TIMEOUT_SECONDS
from app.agent_runtime_config import AgentRuntimeConfig
from app.agent_runtime_contracts import (
    RuntimeCapabilitySnapshot,
    RuntimeFailure,
    RuntimeFailureClass,
    RuntimeKind,
    RuntimeRoute,
    runtime_route_surface_capabilities,
)
from app.codex_decision import extract_codex_session_id
from app.codex_failure import CODEX_PROVIDER_AUTH_FAILED
from app.codex_history import count_codex_session_lines
from app.codex_runtime_adapter import CodexRuntimeAdapter
from app.friday_runtime_adapter import (
    FridayExecutionResult,
    FridayRuntimeAdapter,
    FridayRuntimeError,
)
from app.leak_check import contains_credential, contains_local_runtime_leak
from app.process_runner import ProcessRunResult, run_process_with_idle_timeout
from app.store import (
    MAX_RUNTIME_RESULT_ENVELOPE_BYTES,
    AgentRun,
    AgentRuntimeAttempt,
    AgentRuntimeAttemptStartConflictError,
    AutoReplyStore,
    RuntimeAttemptSessionMode,
    RuntimeRoutePausedError,
)

ResultT = TypeVar("ResultT")
StepT = TypeVar("StepT")
ProcessExecutor = Callable[..., ProcessRunResult]
_ROUTED_RESULT_CODEC_SEAL = object()
_RESULT_VALIDATION_RETRY_SEAL = object()


_LOGGER = logging.getLogger(__name__)

class RoutedResultEnvelopeTooLarge(ValueError):
    """Raised when a durable result exceeds the reviewed byte budget."""


class RoutedResultValidationError(ValueError):
    """A typed business-result validation failure eligible for one correction."""

    def __init__(self, message: str, *, raw_output: str = "") -> None:
        self.raw_output = raw_output
        super().__init__(message)


@dataclass(frozen=True, slots=True, init=False)
class RoutedResultValidationRetry:
    """Sealed policy permitting exactly one typed-result correction turn."""

    correction_instructions: str
    correction_prompt: Callable[[str], str] | None
    resume_same_session: bool
    _seal: object

    def __init__(
        self,
        *,
        correction_instructions: str,
        correction_prompt: Callable[[str], str] | None = None,
        resume_same_session: bool = False,
        seal: object,
    ) -> None:
        if seal is not _RESULT_VALIDATION_RETRY_SEAL:
            raise ValueError("result validation retry policies use named constructors")
        correction_instructions = correction_instructions.strip()
        if not correction_instructions:
            raise ValueError("correction_instructions must be non-empty")
        if contains_credential(correction_instructions) or contains_local_runtime_leak(
            correction_instructions
        ):
            raise ValueError("correction instructions contain sensitive runtime data")
        object.__setattr__(self, "correction_instructions", correction_instructions)
        object.__setattr__(self, "correction_prompt", correction_prompt)
        object.__setattr__(self, "resume_same_session", resume_same_session)
        object.__setattr__(self, "_seal", seal)

    @classmethod
    def exactly_once(
        cls, *, correction_instructions: str
    ) -> RoutedResultValidationRetry:
        return cls(
            correction_instructions=correction_instructions,
            resume_same_session=True,
            seal=_RESULT_VALIDATION_RETRY_SEAL,
        )

    @classmethod
    def same_session_exactly_once(
        cls, *, correction_prompt: Callable[[str], str]
    ) -> RoutedResultValidationRetry:
        if not callable(correction_prompt):
            raise ValueError("correction_prompt must be callable")
        return cls(
            correction_instructions="Resume the same session and correct the result.",
            correction_prompt=correction_prompt,
            resume_same_session=True,
            seal=_RESULT_VALIDATION_RETRY_SEAL,
        )

    def corrected_prompt(
        self, original_prompt: str, failure: RoutedResultValidationError
    ) -> str:
        if self.correction_prompt is not None:
            prompt = self.correction_prompt(failure.raw_output).strip()
            if not prompt:
                raise ValueError("correction prompt must be non-empty")
            return prompt
        detail = " ".join(str(failure).split())[:1000]
        if (
            not detail
            or contains_credential(detail)
            or contains_local_runtime_leak(detail)
        ):
            detail = "the prior result did not satisfy the required validation"
        return (
            f"{original_prompt}\n\n"
            f"上一轮输出未通过结构化校验：{detail}。"
            f"{self.correction_instructions}"
        )

    @property
    def policy_id(self) -> str:
        payload = json.dumps(
            {
                "contract": "result_validation_retry.v1",
                "correction_instructions": self.correction_instructions,
                "resume_same_session": self.resume_same_session,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        return f"result_validation_retry.v1:{digest}"


BACKGROUND_AGENT_RUNTIME_BOUNDARY = """
This is a background Agent turn. Use the capabilities and execution space
available to the runtime and return one valid structured result. The service
consumes that result and does not reinterpret provider-specific commands or tools.
""".strip()


@dataclass(frozen=True, slots=True)
class CodexCommandFactory:
    """Build a normal Codex command and leave tool review to the runtime."""

    developer_instructions: str
    output_schema_path: Path | None = None
    use_output_schema: bool = False
    image_paths: tuple[Path, ...] = ()

    def __post_init__(self) -> None:
        if not self.developer_instructions.strip():
            raise ValueError("developer_instructions must be non-empty")

    @classmethod
    def standard(
        cls,
        *,
        developer_instructions: str,
        output_schema_path: Path | None = None,
        use_output_schema: bool = False,
        image_paths: Sequence[Path] = (),
    ) -> "CodexCommandFactory":
        return cls(
            developer_instructions=developer_instructions,
            output_schema_path=output_schema_path,
            use_output_schema=use_output_schema,
            image_paths=tuple(image_paths),
        )

    def build(
        self,
        *,
        adapter: CodexRuntimeAdapter,
        route: RuntimeRoute,
        prompt: str,
        session_id: str | None,
        skip_git_repo_check: bool = False,
    ) -> tuple[list[str], dict[str, str]]:
        build_options = dict(
            route=route,
            prompt=prompt,
            session_id=session_id,
            image_paths=list(self.image_paths),
            output_schema_path=self.output_schema_path,
            use_output_schema=self.use_output_schema,
            approval_policy="on-failure",
            developer_instructions=self.developer_instructions,
            use_approval_bypass=False,
            sandbox_mode=None,
        )
        if skip_git_repo_check:
            build_options["skip_git_repo_check"] = True
        command = adapter.build_command(**build_options)
        return command, adapter.build_env(route)


@dataclass(frozen=True, slots=True, init=False)
class RoutedResultCodec[ResultT]:
    """A sealed, versioned codec for durable generalized-operation results."""

    schema_id: str
    _kind: str
    _allow_evidence_source_refs: bool
    _seal: object

    def __init__(
        self,
        *,
        schema_id: str,
        kind: str,
        allow_evidence_source_refs: bool = False,
        seal: object,
    ) -> None:
        if seal is not _ROUTED_RESULT_CODEC_SEAL:
            raise ValueError("result codecs use named constructors")
        schema_id = schema_id.strip()
        if not schema_id or not all(
            part.replace("-", "").replace("_", "").isalnum()
            for part in schema_id.split(".")
        ):
            raise ValueError("schema_id must be a versioned identifier")
        object.__setattr__(self, "schema_id", schema_id)
        object.__setattr__(self, "_kind", kind)
        object.__setattr__(
            self,
            "_allow_evidence_source_refs",
            allow_evidence_source_refs,
        )
        object.__setattr__(self, "_seal", seal)

    @classmethod
    def integer(cls, *, schema_id: str) -> RoutedResultCodec[int]:
        return cls(schema_id=schema_id, kind="integer", seal=_ROUTED_RESULT_CODEC_SEAL)

    @classmethod
    def text(
        cls,
        *,
        schema_id: str,
        allow_evidence_source_refs: bool = False,
    ) -> RoutedResultCodec[str]:
        return cls(
            schema_id=schema_id,
            kind="text",
            allow_evidence_source_refs=allow_evidence_source_refs,
            seal=_ROUTED_RESULT_CODEC_SEAL,
        )

    def encode(self, value: ResultT) -> str:
        self._validate(value)
        encoded = json.dumps(
            {"schema_id": self.schema_id, "value": value},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        if len(encoded.encode("utf-8")) > MAX_RUNTIME_RESULT_ENVELOPE_BYTES:
            raise RoutedResultEnvelopeTooLarge("result envelope exceeds size limit")
        if contains_credential(encoded) or self._contains_runtime_leak(value):
            raise ValueError("result envelope contains sensitive runtime data")
        return encoded

    def decode(self, encoded: str) -> ResultT:
        if len(encoded.encode("utf-8")) > MAX_RUNTIME_RESULT_ENVELOPE_BYTES:
            raise RoutedResultEnvelopeTooLarge(
                "persisted result envelope exceeds size limit"
            )
        try:
            envelope = json.loads(encoded)
        except (json.JSONDecodeError, TypeError) as exc:
            raise ValueError("invalid persisted result envelope") from exc
        if (
            not isinstance(envelope, dict)
            or set(envelope) != {"schema_id", "value"}
            or envelope["schema_id"] != self.schema_id
        ):
            raise ValueError("persisted result schema mismatch")
        value = envelope["value"]
        self._validate(value)
        if contains_credential(encoded) or self._contains_runtime_leak(value):
            raise ValueError("persisted result envelope contains sensitive runtime data")
        return value

    def _contains_runtime_leak(self, value: object) -> bool:
        if not self._allow_evidence_source_refs:
            return contains_local_runtime_leak(json.dumps(value, ensure_ascii=False))
        return _contains_local_runtime_leak_outside_evidence_refs(value)

    def _validate(self, value: object) -> None:
        valid = type(value) is int if self._kind == "integer" else type(value) is str
        if not valid:
            raise ValueError(f"result does not match {self._kind} codec")


_EVIDENCE_SOURCE_REF_KEYS = frozenset({"source", "source_ref"})


def _contains_local_runtime_leak_outside_evidence_refs(value: object) -> bool:
    """Allow local paths only in explicit evidence source reference fields."""
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return contains_local_runtime_leak(value)
        return _contains_local_runtime_leak_outside_evidence_refs(parsed)
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).casefold() in _EVIDENCE_SOURCE_REF_KEYS:
                continue
            if _contains_local_runtime_leak_outside_evidence_refs(item):
                return True
        return False
    if isinstance(value, list):
        return any(
            _contains_local_runtime_leak_outside_evidence_refs(item)
            for item in value
        )
    return False


@dataclass(frozen=True, slots=True)
class RoutedCodexExecutionResult[ResultT]:
    value: ResultT
    route_name: str
    attempt_id: int
    session_id: str
    transcript_start: int
    transcript_end: int


class RoutedCodexExecutionError(RuntimeError):
    def __init__(
        self,
        code: str,
        reason: str = "",
        *,
        failure_class: RuntimeFailureClass | None = None,
        failure_code: str = "",
        retryable_external_dependency: bool = False,
        runtime_unavailable: bool = False,
    ) -> None:
        self.code = code
        self.reason = reason
        self.failure_class = failure_class
        self.failure_code = failure_code
        self.retryable_external_dependency = retryable_external_dependency
        # True when no route was entered because every route is paused or
        # unprobed: the workload never consumed a runtime attempt, so callers
        # wait for the routes instead of spending their own retry budget.
        self.runtime_unavailable = runtime_unavailable
        super().__init__(code)


class RoutedCodexExecutionCancelled(RoutedCodexExecutionError):
    def __init__(self) -> None:
        super().__init__(
            "runtime_cancelled",
            "the caller requested cancellation",
            failure_class=RuntimeFailureClass.PROCESS,
            failure_code="runtime_cancelled",
        )


def _runtime_failure_from_friday_error(error: FridayRuntimeError) -> RuntimeFailure:
    """Map Friday's transport result into the shared route-failover contract."""

    if error.code == "friday_runtime_auth_failed":
        failure_class = RuntimeFailureClass.AUTHENTICATION
    elif error.code == "friday_runtime_result_invalid":
        failure_class = RuntimeFailureClass.RESULT
    elif error.code == "friday_runtime_unreachable":
        failure_class = RuntimeFailureClass.TRANSPORT
    else:
        failure_class = RuntimeFailureClass.PROCESS
    return RuntimeFailure(
        failure_class=failure_class,
        code=error.code,
        detail=error.detail,
        retryable_on_same_route=error.retryable,
        # Authentication means this route cannot serve the current turn; the
        # router may continue to a configured route, but the same route is not
        # retried until its credentials are refreshed.
        failover_permitted=error.retryable
        or error.code == "friday_runtime_auth_failed",
    )


_ROUTE_AUTHENTICATION_FAILURE_CODES = frozenset(
    {
        "codex_login_required",
        CODEX_PROVIDER_AUTH_FAILED,
        "claude_authentication_failed",
        "friday_runtime_auth_failed",
    }
)
# A route without a current healthy probe snapshot, or one paused for a
# non-authentication failure, becomes eligible again on its own; the work is
# deferred rather than failed.
_TRANSIENT_ROUTE_REASONS = frozenset(
    {"snapshot_missing", "snapshot_expired", "snapshot_invalid", "snapshot_unhealthy"}
)


def route_unavailable_code(ineligible_routes: tuple[tuple[str, str], ...]) -> str:
    """Classify a ``no_eligible_route`` decision from its typed per-route reasons."""

    reasons = [reason for _, reason in ineligible_routes]
    if not reasons:
        return "runtime_execution_failed"
    capability = [
        reason
        for reason in reasons
        if reason.startswith(("missing_capabilities:", "surface_missing:"))
    ]
    paused = [reason.removeprefix("paused:") for reason in reasons if reason.startswith("paused:")]
    authentication = [code for code in paused if code in _ROUTE_AUTHENTICATION_FAILURE_CODES]
    transient = [
        reason
        for reason in reasons
        if reason in _TRANSIENT_ROUTE_REASONS
        or (
            reason.startswith("paused:")
            and reason.removeprefix("paused:") not in _ROUTE_AUTHENTICATION_FAILURE_CODES
        )
    ]
    if len(capability) == len(reasons):
        return "runtime_capability_missing"
    if transient:
        return "runtime_provider_unreachable"
    if authentication:
        return "runtime_provider_auth_failed"
    return "runtime_execution_failed"


def _is_retryable_external_runtime_failure(failure: RuntimeFailure) -> bool:
    """Return whether an exhausted provider failure belongs in caller backoff."""

    return failure.failure_class in {
        RuntimeFailureClass.CAPACITY,
        RuntimeFailureClass.TRANSPORT,
    }


def _agent_run_workload_id(workload_kind: str, workload_key: str) -> int | None:
    if workload_kind != "agent_run":
        return None
    if not workload_key.isdecimal() or int(workload_key) <= 0:
        raise ValueError("agent_run workload key must be a persisted ID")
    return int(workload_key)


class RoutedCodexPolicyAbort(RuntimeError):
    """Abort the child process immediately after fail-closed policy evidence."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class RuntimeRouteDecision:
    """A bounded runtime-route decision with display-safe static reasons."""

    route: RuntimeRoute | None
    fresh_session: bool
    reason: str
    # Per-route static reasons behind a ``no_eligible_route`` decision, so the
    # caller can classify the outcome without parsing the display string.
    ineligible_routes: tuple[tuple[str, str], ...] = ()


class AgentRuntimeRouter:
    """Select one untried, healthy route without starting or mutating work."""

    def __init__(
        self,
        *,
        routes: Sequence[RuntimeRoute],
        store: AutoReplyStore,
        snapshots: Mapping[str, RuntimeCapabilitySnapshot],
        now: Callable[[], datetime | str] | None = None,
    ) -> None:
        self._routes = tuple(routes)
        self._store = store
        self._snapshots = snapshots
        self._now = now or (lambda: datetime.now(UTC))

    def first_eligible_route(
        self,
        *,
        required_capabilities: frozenset[str],
        allow_legacy_oauth_bootstrap: bool = False,
        excluded_routes: frozenset[str] = frozenset(),
    ) -> RuntimeRoute | None:
        """Select an initial route from current evidence.

        The bootstrap exception preserves the pre-failover OAuth path only. It
        never asserts probe health, never applies to service credentials, and
        is disabled whenever an explicit OAuth snapshot exists.
        """
        return self.first_route_decision(
            required_capabilities=required_capabilities,
            allow_legacy_oauth_bootstrap=allow_legacy_oauth_bootstrap,
            excluded_routes=excluded_routes,
        ).route

    def first_route_decision(
        self,
        *,
        required_capabilities: frozenset[str],
        allow_legacy_oauth_bootstrap: bool = False,
        excluded_routes: frozenset[str] = frozenset(),
    ) -> RuntimeRouteDecision:
        """Return the initial route plus a safe, persisted eligibility reason."""
        now = _parse_timestamp(self._now())
        ineligible: list[tuple[str, str]] = []
        for route in self._routes:
            if route.name in excluded_routes:
                ineligible.append((route.name, "already_attempted"))
                continue
            pause_code = self._store.active_runtime_route_pause(route.name, now=now)
            if pause_code is not None:
                ineligible.append((route.name, f"paused:{pause_code}"))
                continue
            if self._snapshot_is_current_and_eligible(
                route=route,
                required_capabilities=required_capabilities,
                now=now,
            ):
                return RuntimeRouteDecision(route, False, "eligible_route")
            if (
                allow_legacy_oauth_bootstrap
                and route.name == "codex_oauth"
                and route.name not in self._snapshots
            ):
                return RuntimeRouteDecision(route, False, "legacy_oauth_bootstrap")
            snapshot = self._snapshots.get(route.name)
            if snapshot is None:
                reason = "snapshot_missing"
            elif snapshot.route_name != route.name:
                reason = "snapshot_invalid"
            elif not snapshot.healthy or snapshot.failure is not None:
                reason = "snapshot_unhealthy"
            else:
                try:
                    checked_at = _parse_timestamp(snapshot.checked_at)
                    expires_at = _parse_timestamp(snapshot.expires_at)
                except (TypeError, ValueError):
                    reason = "snapshot_invalid"
                else:
                    if checked_at > now:
                        reason = "snapshot_invalid"
                    elif expires_at <= now:
                        reason = "snapshot_expired"
                    else:
                        missing_probe, missing_surface = self._missing_capabilities(
                            route=route,
                            snapshot=snapshot,
                            required_capabilities=required_capabilities,
                        )
                        if missing_probe:
                            reason = "missing_capabilities:" + ",".join(missing_probe)
                        else:
                            reason = "surface_missing:" + ",".join(missing_surface)
            ineligible.append((route.name, reason))
        return RuntimeRouteDecision(
            None,
            False,
            "no_eligible_route:"
            + ";".join(f"{name}={reason}" for name, reason in ineligible),
            ineligible_routes=tuple(ineligible),
        )

    def next_route(
        self,
        *,
        run: AgentRun,
        failed_attempt: AgentRuntimeAttempt,
        failure: RuntimeFailure,
        required_capabilities: frozenset[str],
    ) -> RuntimeRouteDecision:
        persisted_run = self._store.get_agent_run(run.id)
        if persisted_run is None:
            return RuntimeRouteDecision(None, False, "run_not_found")
        if not _run_identity_matches(run, persisted_run):
            return RuntimeRouteDecision(None, False, "run_identity_mismatch")
        if persisted_run.status != "running":
            return RuntimeRouteDecision(None, False, "run_not_eligible")

        persisted_attempt = self._store.get_agent_runtime_attempt(failed_attempt.id)
        if (
            failed_attempt.agent_run_id != persisted_run.id
            or persisted_attempt is None
            or persisted_attempt.agent_run_id != persisted_run.id
            or persisted_attempt != failed_attempt
        ):
            return RuntimeRouteDecision(None, False, "attempt_run_mismatch")
        if persisted_attempt.status != "failed":
            return RuntimeRouteDecision(None, False, "attempt_not_failed")
        if not _failure_matches_persisted_attempt(failure, persisted_attempt):
            return RuntimeRouteDecision(None, False, "failure_mismatch")

        attempts = self._store.list_agent_runtime_attempts(persisted_run.id)
        if not failure.failover_permitted:
            return RuntimeRouteDecision(None, False, "failure_not_eligible")

        now = _parse_timestamp(self._now())
        attempted_routes = {attempt.route_name for attempt in attempts}
        return self._next_eligible_decision(
            attempted_routes=attempted_routes,
            failed_attempt=persisted_attempt,
            failure=failure,
            attempts=attempts,
            required_capabilities=required_capabilities,
            now=now,
        )

    def next_operation_route(
        self,
        *,
        workload_kind: str,
        workload_key: str,
        failed_attempt: AgentRuntimeAttempt,
        failure: RuntimeFailure,
        required_capabilities: frozenset[str],
    ) -> RuntimeRouteDecision:
        """Select a bounded fallback for one persisted non-Agent operation."""
        if not self._store.runtime_operation_parent_is_runnable(
            workload_kind, workload_key
        ):
            return RuntimeRouteDecision(None, False, "operation_not_runnable")
        persisted_attempt = self._store.get_agent_runtime_attempt(failed_attempt.id)
        if (
            failed_attempt.agent_run_id is not None
            or failed_attempt.workload_kind != workload_kind
            or failed_attempt.workload_key != workload_key
            or persisted_attempt is None
            or persisted_attempt != failed_attempt
            or persisted_attempt.agent_run_id is not None
            or persisted_attempt.workload_kind != workload_kind
            or persisted_attempt.workload_key != workload_key
        ):
            return RuntimeRouteDecision(None, False, "attempt_workload_mismatch")
        if persisted_attempt.status != "failed":
            return RuntimeRouteDecision(None, False, "attempt_not_failed")
        if not _failure_matches_persisted_attempt(failure, persisted_attempt):
            return RuntimeRouteDecision(None, False, "failure_mismatch")
        if not failure.failover_permitted:
            return RuntimeRouteDecision(None, False, "failure_not_eligible")

        attempts = self._store.list_runtime_operation_attempts(
            workload_kind, workload_key
        )
        now = _parse_timestamp(self._now())
        attempted_routes = {attempt.route_name for attempt in attempts}
        return self._next_eligible_decision(
            attempted_routes=attempted_routes,
            failed_attempt=persisted_attempt,
            failure=failure,
            attempts=attempts,
            required_capabilities=required_capabilities,
            now=now,
        )

    def _next_eligible_decision(
        self,
        *,
        attempted_routes: set[str],
        failed_attempt: AgentRuntimeAttempt,
        failure: RuntimeFailure,
        attempts: Sequence[AgentRuntimeAttempt],
        required_capabilities: frozenset[str],
        now: datetime,
    ) -> RuntimeRouteDecision:
        """Apply the shared pause, capability, and bounded-route selector."""
        for route in self._routes:
            fresh_session_retry = False
            if route.name in attempted_routes:
                fresh_session_retry = self._fresh_session_retry_is_permitted(
                    route=route,
                    failed_attempt=failed_attempt,
                    failure=failure,
                    attempts=attempts,
                )
                if not fresh_session_retry:
                    continue
            if self._store.active_runtime_route_pause(route.name, now=now) is not None:
                continue
            if not self._snapshot_is_current_and_eligible(
                route=route,
                required_capabilities=required_capabilities,
                now=now,
            ):
                continue
            return RuntimeRouteDecision(
                route,
                fresh_session_retry,
                "fresh_session_retry" if fresh_session_retry else "eligible_route",
            )
        return RuntimeRouteDecision(None, False, "no_eligible_route")

    @staticmethod
    def _fresh_session_retry_is_permitted(
        *,
        route: RuntimeRoute,
        failed_attempt: AgentRuntimeAttempt,
        failure: RuntimeFailure,
        attempts: Sequence[AgentRuntimeAttempt],
    ) -> bool:
        fresh_attempt_count = sum(
            attempt.route_name == route.name
            and attempt.session_mode == RuntimeAttemptSessionMode.FRESH
            for attempt in attempts
        )
        if (
            route.name in {"codex_api", "claude_api"}
            and failed_attempt.route_name == route.name
            and failed_attempt.session_mode == RuntimeAttemptSessionMode.FRESH
            and failure.retryable_on_same_route
            and fresh_attempt_count < 2
        ):
            # A transient transport/capacity failure can leave a fresh API
            # process without a resumable session. Permit one bounded fresh
            # retry on the same healthy route before surfacing the failure.
            return True
        return not any(
            attempt.route_name == route.name
            and attempt.session_mode == RuntimeAttemptSessionMode.FRESH
            for attempt in attempts
        ) and (
            route.name in {"codex_api", "claude_api"}
            and failed_attempt.route_name == route.name
            and failed_attempt.session_mode == RuntimeAttemptSessionMode.RESUME
            and bool(failed_attempt.source_session_id.strip())
            and failed_attempt.failure_class == RuntimeFailureClass.SESSION.value
            and failure.failure_class == RuntimeFailureClass.SESSION
            and failure.code == "session_route_incompatible"
        )

    def _snapshot_is_current_and_eligible(
        self,
        *,
        route: RuntimeRoute,
        required_capabilities: frozenset[str],
        now: datetime,
    ) -> bool:
        snapshot = self._snapshots.get(route.name)
        if snapshot is None or snapshot.route_name != route.name:
            return False
        if not snapshot.healthy or snapshot.failure is not None:
            return False
        try:
            expires_at = _parse_timestamp(snapshot.expires_at)
            checked_at = _parse_timestamp(snapshot.checked_at)
        except (TypeError, ValueError):
            return False
        if checked_at > now or expires_at <= now:
            return False
        missing_probe, missing_surface = self._missing_capabilities(
            route=route,
            snapshot=snapshot,
            required_capabilities=required_capabilities,
        )
        return not missing_probe and not missing_surface

    def _missing_capabilities(
        self,
        *,
        route: RuntimeRoute,
        snapshot: RuntimeCapabilitySnapshot,
        required_capabilities: frozenset[str],
    ) -> tuple[list[str], list[str]]:
        unresolved = required_capabilities - (
            snapshot.capabilities | runtime_route_surface_capabilities(route)
        )
        return sorted(unresolved), []


class RoutedCodexExecution:
    """Execute one persisted generalized workload through bounded Codex routes."""

    def __init__(
        self,
        *,
        store: AutoReplyStore,
        config: AgentRuntimeConfig,
        router: AgentRuntimeRouter,
        adapter: CodexRuntimeAdapter,
        friday_adapter: FridayRuntimeAdapter | None = None,
        executor: ProcessExecutor = run_process_with_idle_timeout,
        session_id_parser: Callable[[str], str | None] = extract_codex_session_id,
        session_line_counter: Callable[[str], int] = count_codex_session_lines,
        total_timeout_seconds: float = TOTAL_TIMEOUT_SECONDS,
        idle_timeout_seconds: float = IDLE_TIMEOUT_SECONDS,
        owner: str | None = None,
        lease_seconds: int | None = None,
        allow_legacy_oauth_bootstrap: bool = False,
        refresh_runtime_capabilities: Callable[[], object] | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._config = config
        self._router = router
        self._adapter = adapter
        self._friday_adapter = friday_adapter
        self._executor = executor
        self._session_id_parser = session_id_parser
        self._session_line_counter = session_line_counter
        self._total_timeout_seconds = total_timeout_seconds
        self._idle_timeout_seconds = idle_timeout_seconds
        self._owner = (owner or f"routed-codex-{uuid.uuid4().hex}").strip()
        if not self._owner:
            raise ValueError("owner must be non-empty")
        if lease_seconds is not None and lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        configured_lease = (
            int(total_timeout_seconds) + int(idle_timeout_seconds) + 300
            if lease_seconds is None
            else lease_seconds
        )
        self._lease_seconds = max(configured_lease, int(total_timeout_seconds) + 60)
        self._allow_legacy_oauth_bootstrap = bool(allow_legacy_oauth_bootstrap)
        self._refresh_runtime_capabilities = refresh_runtime_capabilities
        self._now = now or (lambda: datetime.now(UTC))

    def execute(
        self,
        *,
        workload_kind: str,
        workload_key: str,
        prompt: str,
        command_factory: CodexCommandFactory,
        parser: Callable[[str], ResultT],
        result_codec: RoutedResultCodec[ResultT],
        conversation_id: str | None = None,
        required_capabilities: frozenset[str] = frozenset(),
        result_validation_retry: RoutedResultValidationRetry | None = None,
        on_stdout_line: Callable[[str], None] | None = None,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> RoutedCodexExecutionResult[ResultT]:
        if cancel_requested is not None and cancel_requested():
            raise RoutedCodexExecutionCancelled()
        if self._refresh_runtime_capabilities is not None:
            self._refresh_runtime_capabilities(force=False)
        if not isinstance(command_factory, CodexCommandFactory):
            raise ValueError("command_factory is invalid")
        if (
            type(result_codec) is not RoutedResultCodec
            or result_codec._seal is not _ROUTED_RESULT_CODEC_SEAL
        ):
            raise ValueError("result_codec is invalid")
        if result_validation_retry is not None and (
            type(result_validation_retry) is not RoutedResultValidationRetry
            or result_validation_retry._seal is not _RESULT_VALIDATION_RETRY_SEAL
        ):
            raise ValueError("result_validation_retry is invalid")
        prompt = prompt.strip()
        if not prompt:
            raise ValueError("prompt must be non-empty")
        original_prompt = prompt
        result_validation_retries_used = 0
        forced_retry_session_id: str | None = None
        terminal_failure: RuntimeFailure | None = None
        next_attempt_purpose = "normal"
        next_validation_retry_policy_id = ""
        next_validation_result_schema_id = ""

        agent_run_id = _agent_run_workload_id(workload_kind, workload_key)

        if agent_run_id is None:
            self._store.recover_expired_runtime_operation_attempt(
                workload_kind, workload_key, now=self._now()
            )
        existing_attempts = self._runtime_attempts(
            workload_kind, workload_key, agent_run_id=agent_run_id
        )
        if existing_attempts:
            latest = existing_attempts[-1]
            if latest.status in {"starting", "running"}:
                raise RoutedCodexExecutionError("runtime_attempt_active")
            if latest.status == "completed":
                try:
                    value = result_codec.decode(latest.result_envelope_json)
                except RoutedResultEnvelopeTooLarge as exc:
                    raise RoutedCodexExecutionError("runtime_result_invalid") from exc
                except ValueError as exc:
                    raise RoutedCodexExecutionError(
                        "runtime_result_schema_mismatch"
                    ) from exc
                return RoutedCodexExecutionResult(
                    value=value,
                    route_name=latest.route_name,
                    attempt_id=latest.id,
                    session_id=latest.session_id,
                    transcript_start=latest.transcript_start,
                    transcript_end=latest.transcript_end,
                )
            if latest.status != "failed":
                raise RoutedCodexExecutionError("runtime_attempt_state_invalid")
            if latest.attempt_purpose == "result_validation_correction":
                raise RoutedCodexExecutionError(
                    "runtime_result_validation_retry_consumed",
                    failure_class=RuntimeFailureClass.RESULT,
                    failure_code="runtime_result_validation_retry_consumed",
                )
            validation_failures = sum(
                attempt.failure_code == "runtime_result_validation_failed"
                for attempt in existing_attempts
            )
            can_resume_validation_retry = (
                result_validation_retry is not None
                and latest.failure_code == "runtime_result_validation_failed"
                and validation_failures == 1
                and (
                    not result_validation_retry.resume_same_session
                    or bool(latest.session_id)
                )
            )
            if can_resume_validation_retry:
                eligible = self._router.first_route_decision(
                    required_capabilities=required_capabilities
                )
                if (
                    eligible.route is not None
                    and eligible.route.name == latest.route_name
                ):
                    decision = RuntimeRouteDecision(
                        route=eligible.route,
                        fresh_session=not result_validation_retry.resume_same_session,
                        reason="persisted_result_validation_retry",
                    )
                    if result_validation_retry.resume_same_session:
                        forced_retry_session_id = latest.session_id
                    prompt = result_validation_retry.corrected_prompt(
                        original_prompt,
                        RoutedResultValidationError(
                            "the prior persisted result did not satisfy validation"
                        ),
                    )
                    result_validation_retries_used = 1
                    next_attempt_purpose = "result_validation_correction"
                    next_validation_retry_policy_id = result_validation_retry.policy_id
                    next_validation_result_schema_id = result_codec.schema_id
                else:
                    decision = RuntimeRouteDecision(
                        route=None,
                        fresh_session=True,
                        reason="persisted_result_validation_route_unavailable",
                    )
            else:
                persisted_failure = RuntimeFailure(
                    failure_class=RuntimeFailureClass(latest.failure_class),
                    code=latest.failure_code,
                    detail="persisted runtime failure",
                    retryable_on_same_route=latest.failure_class
                    in {
                        RuntimeFailureClass.CAPACITY.value,
                        RuntimeFailureClass.TRANSPORT.value,
                    },
                    failover_permitted=latest.failover_permitted,
                )
                terminal_failure = persisted_failure
                decision = self._next_route_after_failure(
                    workload_kind=workload_kind,
                    workload_key=workload_key,
                    agent_run_id=agent_run_id,
                    failed_attempt=latest,
                    failure=persisted_failure,
                    required_capabilities=required_capabilities,
                )
        else:
            decision = self._router.first_route_decision(
                required_capabilities=required_capabilities,
                allow_legacy_oauth_bootstrap=self._allow_legacy_oauth_bootstrap,
            )
        if decision.route is None and self._refresh_runtime_capabilities is not None:
            # A route may recover before its unhealthy snapshot expires. Re-probe
            # once at the no-route boundary so a transient outage does not become
            # a task-level route failure for the whole retry window.
            self._refresh_runtime_capabilities(force=True)
            decision = self._router.first_route_decision(
                required_capabilities=required_capabilities,
                allow_legacy_oauth_bootstrap=self._allow_legacy_oauth_bootstrap,
            )
        if decision.route is None:
            # Every route is merely paused or unprobed: the runtime is not
            # ready rather than broken, so callers defer the work.
            runtime_unavailable = (
                terminal_failure is None
                and route_unavailable_code(decision.ineligible_routes)
                == "runtime_provider_unreachable"
            )
            raise RoutedCodexExecutionError(
                "runtime_execution_failed",
                decision.reason,
                failure_class=(
                    terminal_failure.failure_class if terminal_failure else None
                ),
                failure_code=terminal_failure.code if terminal_failure else "",
                retryable_external_dependency=(
                    _is_retryable_external_runtime_failure(terminal_failure)
                    if terminal_failure
                    else runtime_unavailable
                ),
                runtime_unavailable=runtime_unavailable,
            )
        route = decision.route
        if (
            route.runtime_kind is RuntimeKind.FRIDAY_RUNTIME
            and self._friday_adapter is None
        ):
            raise RoutedCodexExecutionError(
                "friday_runtime_unavailable",
                "Friday Runtime adapter is not configured",
                failure_class=RuntimeFailureClass.PROCESS,
                failure_code="friday_runtime_unavailable",
                retryable_external_dependency=True,
            )
        route_session_id = (
            forced_retry_session_id
            if forced_retry_session_id is not None
            else (
                None
                if decision.fresh_session
                else self._session_for_route(conversation_id, route.name)
            )
        )
        active_attempt = self._claim_and_start(
            workload_kind,
            workload_key,
            route,
            route_session_id,
            attempt_purpose=next_attempt_purpose,
            validation_retry_policy_id=next_validation_retry_policy_id,
            validation_result_schema_id=next_validation_result_schema_id,
        )
        if existing_attempts and existing_attempts[-1].status == "failed":
            previous = existing_attempts[-1]
            self._finalized_step(
                active_attempt,
                stage="attempt_supersede",
                evidence=lambda: (
                    route_session_id or "",
                    f"codex_session:{route_session_id}" if route_session_id else "",
                    0,
                    0,
                ),
                action=lambda: self._store.mark_agent_runtime_attempt_superseded(
                    previous.id
                ),
            )

        while True:
            transcript_start = 0
            transcript_end = 0
            line_count = 0
            observed_session_id = route_session_id or ""
            transcript_reference = ""
            # Friday's operation identifier is the durable execution evidence;
            # unlike Codex, it has no stdout session transcript to reference.
            if route.runtime_kind is not RuntimeKind.FRIDAY_RUNTIME:
                transcript_reference = (
                    f"codex_session:{observed_session_id}" if observed_session_id else ""
                )

            def current_evidence() -> tuple[str, str, int, int]:
                return (
                    observed_session_id,
                    transcript_reference,
                    transcript_start,
                    max(
                        transcript_start + line_count,
                        transcript_start,
                        transcript_end,
                    ),
                )

            if route_session_id:
                transcript_start = self._finalized_step(
                    active_attempt,
                    stage="transcript_evidence",
                    evidence=current_evidence,
                    action=lambda: self._session_line_counter(route_session_id),
                )

            def observe_stdout_line(line: str) -> None:
                nonlocal line_count, active_attempt
                nonlocal observed_session_id, transcript_reference
                line_count += 1
                streamed_session_id = self._session_id_parser(line)
                if streamed_session_id:
                    if (
                        observed_session_id
                        and streamed_session_id != observed_session_id
                    ):
                        raise RoutedCodexPolicyAbort("runtime_session_conflict")
                    observed_session_id = streamed_session_id
                    transcript_reference = f"codex_session:{streamed_session_id}"
                    active_attempt = self._store.set_agent_runtime_attempt_session(
                        active_attempt.id,
                        streamed_session_id,
                        transcript_reference,
                        owner=self._owner,
                        now=self._now(),
                    )
                if on_stdout_line is not None:
                    on_stdout_line(line)
            active_attempt = self._finalized_step(
                active_attempt,
                stage="lease_renewal",
                evidence=current_evidence,
                action=lambda: self._renew_attempt_parent_lease(active_attempt),
            )
            active_attempt = self._finalized_step(
                active_attempt,
                stage="lease_renewal",
                evidence=current_evidence,
                action=lambda: self._renew_attempt_parent_lease(active_attempt),
            )
            friday_result: FridayExecutionResult | None = None
            if route.runtime_kind is RuntimeKind.FRIDAY_RUNTIME:
                try:
                    friday_result = self._friday_adapter.execute(
                        prompt,
                        project_id=self._config.friday_runtime_project_id,
                        conversation_id=conversation_id,
                        model=route.model,
                        timeout_seconds=self._total_timeout_seconds,
                    )
                    observed_session_id = f"friday_thread:{friday_result.thread_id}"
                    transcript_reference = (
                        f"friday_operation:{friday_result.operation_id}"
                    )
                    active_attempt = self._store.set_agent_runtime_attempt_session(
                        active_attempt.id,
                        observed_session_id,
                        transcript_reference,
                        owner=self._owner,
                        now=self._now(),
                    )
                    process = ProcessRunResult(0, friday_result.text, "")
                except FridayRuntimeError as exc:
                    if exc.thread_id:
                        observed_session_id = f"friday_thread:{exc.thread_id}"
                    if exc.operation_id:
                        transcript_reference = f"friday_operation:{exc.operation_id}"
                    failure = _runtime_failure_from_friday_error(exc)
                    failed_attempt = self._finalized_step(
                        active_attempt,
                        stage="attempt_failure",
                        evidence=current_evidence,
                        action=lambda: self._store.fail_agent_runtime_attempt(
                            active_attempt.id,
                            failure.failure_class.value,
                            failure.code,
                            failure.failover_permitted,
                            session_id=observed_session_id,
                            transcript_reference=transcript_reference,
                            transcript_start=transcript_start,
                            transcript_end=transcript_end,
                            owner=self._owner,
                            now=self._now(),
                        ),
                    )
                    next_decision = self._next_route_after_failure(
                        workload_kind=workload_kind,
                        workload_key=workload_key,
                        agent_run_id=agent_run_id,
                        failed_attempt=failed_attempt,
                        failure=failure,
                        required_capabilities=required_capabilities,
                    )
                    if next_decision.route is None:
                        raise RoutedCodexExecutionError(
                            "runtime_execution_failed",
                            next_decision.reason,
                            failure_class=failure.failure_class,
                            failure_code=failure.code,
                            retryable_external_dependency=(
                                _is_retryable_external_runtime_failure(failure)
                            ),
                        ) from exc
                    route = next_decision.route
                    route_session_id = (
                        None
                        if next_decision.fresh_session
                        else self._session_for_route(conversation_id, route.name)
                    )
                    successor = self._claim_and_start(
                        workload_kind, workload_key, route, route_session_id
                    )
                    self._finalized_step(
                        successor,
                        stage="attempt_supersede",
                        evidence=lambda: (
                            route_session_id or "",
                            (f"codex_session:{route_session_id}" if route_session_id else ""),
                            0,
                            0,
                        ),
                        action=lambda: self._store.mark_agent_runtime_attempt_superseded(
                            failed_attempt.id
                        ),
                    )
                    active_attempt = successor
                    continue
                except Exception as exc:
                    self._terminalize_active_attempt(
                        active_attempt,
                        failure_class=RuntimeFailureClass.PROCESS,
                        failure_code="friday_runtime_failed",
                        session_id=observed_session_id,
                        transcript_reference=transcript_reference,
                        transcript_start=transcript_start,
                        transcript_end=transcript_end,
                    )
                    raise RoutedCodexExecutionError(
                        "friday_runtime_failed", "adapter_execution"
                    ) from exc
            else:
                command, env = self._finalized_step(
                    active_attempt,
                    stage="command_build",
                    evidence=current_evidence,
                    action=lambda: command_factory.build(
                        adapter=self._adapter,
                        route=route,
                        prompt=prompt,
                        session_id=route_session_id,
                    ),
                )
                process = self._finalized_step(
                    active_attempt,
                    stage="process_execution",
                    evidence=current_evidence,
                    action=lambda: self._executor(
                        command,
                        prompt=prompt,
                        env=env,
                        total_timeout_seconds=self._total_timeout_seconds,
                        idle_timeout_seconds=self._idle_timeout_seconds,
                        on_stdout_line=observe_stdout_line,
                    ),
                )

            if cancel_requested is not None and cancel_requested():
                self._terminalize_active_attempt(
                    active_attempt,
                    failure_class=RuntimeFailureClass.PROCESS,
                    failure_code="runtime_cancelled",
                    session_id=observed_session_id,
                    transcript_reference=transcript_reference,
                    transcript_start=transcript_start,
                    transcript_end=max(transcript_start + line_count, transcript_start),
                )
                raise RoutedCodexExecutionCancelled()

            buffered_session_id = self._finalized_step(
                active_attempt,
                stage="transcript_evidence",
                evidence=current_evidence,
                action=lambda: self._session_id_parser(process.stdout),
            )
            if (
                buffered_session_id
                and observed_session_id
                and buffered_session_id != observed_session_id
            ):

                def abort_conflicting_session() -> None:
                    raise RoutedCodexPolicyAbort("runtime_session_conflict")

                self._finalized_step(
                    active_attempt,
                    stage="transcript_evidence",
                    evidence=current_evidence,
                    action=abort_conflicting_session,
                )
            observed_session_id = buffered_session_id or observed_session_id
            transcript_end = max(transcript_start + line_count, transcript_start)
            if observed_session_id:
                transcript_end = max(
                    transcript_end,
                    self._finalized_step(
                        active_attempt,
                        stage="transcript_evidence",
                        evidence=current_evidence,
                        action=lambda: self._session_line_counter(observed_session_id),
                    ),
                )
            if route.runtime_kind is not RuntimeKind.FRIDAY_RUNTIME:
                transcript_reference = (
                    f"codex_session:{observed_session_id}" if observed_session_id else ""
                )

            if process.returncode == 0 and not process.timed_out:
                try:
                    value = parser(process.stdout)
                except RoutedResultValidationError as exc:
                    # The attempt row keeps only the failure code; the reason
                    # (never the raw output) goes to the service log.
                    _LOGGER.warning(
                        "result validation failed for %s/%s attempt %s on %s: %s",
                        workload_kind,
                        workload_key,
                        active_attempt.id,
                        route.name,
                        exc,
                    )
                    can_retry_validation = (
                        result_validation_retry is not None
                        and result_validation_retries_used == 0
                        and (
                            not result_validation_retry.resume_same_session
                            or bool(observed_session_id)
                        )
                    )
                    self._terminalize_active_attempt(
                        active_attempt,
                        failure_class=RuntimeFailureClass.RESULT,
                        failure_code="runtime_result_validation_failed",
                        session_id=observed_session_id,
                        transcript_reference=transcript_reference,
                        transcript_start=transcript_start,
                        transcript_end=transcript_end,
                    )
                    failed_validation_attempt = self._store.get_agent_runtime_attempt(
                        active_attempt.id
                    )
                    if not can_retry_validation or failed_validation_attempt is None:
                        raise RoutedCodexExecutionError(
                            "runtime_result_validation_failed",
                            failure_class=RuntimeFailureClass.RESULT,
                            failure_code="runtime_result_validation_failed",
                        ) from exc
                    successor_session_id = (
                        observed_session_id
                        if result_validation_retry.resume_same_session
                        else None
                    )
                    successor = self._claim_and_start(
                        workload_kind,
                        workload_key,
                        route,
                        successor_session_id,
                        attempt_purpose="result_validation_correction",
                        validation_retry_policy_id=result_validation_retry.policy_id,
                        validation_result_schema_id=result_codec.schema_id,
                    )
                    self._finalized_step(
                        successor,
                        stage="attempt_supersede",
                        evidence=lambda: (
                            successor_session_id or "",
                            (
                                f"codex_session:{successor_session_id}"
                                if successor_session_id
                                else ""
                            ),
                            0,
                            0,
                        ),
                        action=lambda: (
                            self._store.mark_agent_runtime_attempt_superseded(
                                failed_validation_attempt.id
                            )
                        ),
                    )
                    active_attempt = successor
                    route_session_id = successor_session_id
                    prompt = result_validation_retry.corrected_prompt(
                        original_prompt, exc
                    )
                    result_validation_retries_used = 1
                    continue
                except Exception as exc:  # noqa: BLE001
                    self._terminalize_active_attempt(
                        active_attempt,
                        failure_class=RuntimeFailureClass.RESULT,
                        failure_code="runtime_result_invalid",
                        session_id=observed_session_id,
                        transcript_reference=transcript_reference,
                        transcript_start=transcript_start,
                        transcript_end=transcript_end,
                    )
                    raise RoutedCodexExecutionError(
                        "runtime_result_invalid", "result_parse"
                    ) from exc
                result_envelope = self._finalized_step(
                    active_attempt,
                    stage="result_persistence",
                    evidence=current_evidence,
                    action=lambda: result_codec.encode(value),
                )
                completed = self._finalized_step(
                    active_attempt,
                    stage="attempt_completion",
                    evidence=current_evidence,
                    action=lambda: self._store.complete_agent_runtime_attempt(
                        active_attempt.id,
                        observed_session_id,
                        transcript_reference,
                        transcript_start,
                        transcript_end,
                        owner=self._owner,
                        result_schema_id=result_codec.schema_id,
                        result_envelope_json=result_envelope,
                        conversation_id=conversation_id or "",
                        route_name=route.name,
                        now=self._now(),
                    ),
                )
                return RoutedCodexExecutionResult(
                    value=value,
                    route_name=route.name,
                    attempt_id=completed.id,
                    session_id=observed_session_id,
                    transcript_start=transcript_start,
                    transcript_end=transcript_end,
                )

            failure = self._finalized_step(
                active_attempt,
                stage="failure_classification",
                evidence=current_evidence,
                action=lambda: self._adapter.classify_failure(
                    process.stdout,
                    process.stderr,
                    process.returncode,
                    timed_out=process.timed_out,
                    timeout_kind=process.timeout_kind,
                ),
            )
            if failure.route_pause_required:
                self._finalized_step(
                    active_attempt,
                    stage="route_pause",
                    evidence=current_evidence,
                    action=lambda: self._store.open_runtime_route_pause(
                        route.name,
                        failure.code,
                        self._now() + self._config.retry_delay,
                    ),
                )
            failed_attempt = self._finalized_step(
                active_attempt,
                stage="attempt_failure",
                evidence=current_evidence,
                action=lambda: self._store.fail_agent_runtime_attempt(
                    active_attempt.id,
                    failure.failure_class.value,
                    failure.code,
                    failure.failover_permitted,
                    session_id=observed_session_id,
                    transcript_reference=transcript_reference,
                    transcript_start=transcript_start,
                    transcript_end=transcript_end,
                    owner=self._owner,
                    now=self._now(),
                ),
            )
            if result_validation_retries_used > 0:
                raise RoutedCodexExecutionError(
                    "runtime_execution_failed",
                    failure_class=failure.failure_class,
                    failure_code=failure.code,
                    retryable_external_dependency=(
                        _is_retryable_external_runtime_failure(failure)
                    ),
                )

            next_decision = self._next_route_after_failure(
                workload_kind=workload_kind,
                workload_key=workload_key,
                agent_run_id=agent_run_id,
                failed_attempt=failed_attempt,
                failure=failure,
                required_capabilities=required_capabilities,
            )
            if next_decision.route is None:
                raise RoutedCodexExecutionError(
                    "runtime_execution_failed",
                    next_decision.reason,
                    failure_class=failure.failure_class,
                    failure_code=failure.code,
                    retryable_external_dependency=(
                        _is_retryable_external_runtime_failure(failure)
                    ),
                )
            route = next_decision.route
            route_session_id = (
                None
                if next_decision.fresh_session
                else self._session_for_route(conversation_id, route.name)
            )
            successor = self._claim_and_start(
                workload_kind, workload_key, route, route_session_id
            )
            self._finalized_step(
                successor,
                stage="attempt_supersede",
                evidence=lambda: (
                    route_session_id or "",
                    (f"codex_session:{route_session_id}" if route_session_id else ""),
                    0,
                    0,
                ),
                action=lambda: self._store.mark_agent_runtime_attempt_superseded(
                    failed_attempt.id
                ),
            )
            active_attempt = successor

    def _claim_and_start(
        self,
        workload_kind: str,
        workload_key: str,
        route: RuntimeRoute,
        session_id: str | None,
        *,
        attempt_purpose: str = "normal",
        validation_retry_policy_id: str = "",
        validation_result_schema_id: str = "",
    ) -> AgentRuntimeAttempt:
        try:
            session_mode = (
                RuntimeAttemptSessionMode.RESUME
                if session_id
                else RuntimeAttemptSessionMode.FRESH
            )
            agent_run_id = _agent_run_workload_id(workload_kind, workload_key)
            if agent_run_id is not None:
                attempt = self._store.claim_agent_runtime_attempt(
                    agent_run_id,
                    route.name,
                    route.runtime_kind.value,
                    route.credential_mode.value,
                    route.model,
                    session_mode=session_mode,
                    source_session_id=session_id or "",
                    attempt_purpose=attempt_purpose,
                    validation_retry_policy_id=validation_retry_policy_id,
                    validation_result_schema_id=validation_result_schema_id,
                )
            else:
                attempt = self._store.claim_runtime_operation_attempt(
                    workload_kind,
                    workload_key,
                    route.name,
                    route.runtime_kind.value,
                    route.credential_mode.value,
                    route.model,
                    session_mode=session_mode,
                    source_session_id=session_id or "",
                    attempt_purpose=attempt_purpose,
                    validation_retry_policy_id=validation_retry_policy_id,
                    validation_result_schema_id=validation_result_schema_id,
                    owner=self._owner,
                    lease_seconds=self._lease_seconds,
                    now=self._now(),
                )
        except RuntimeRoutePausedError as exc:
            raise RoutedCodexExecutionError(
                "runtime_execution_failed",
                f"{route.name}_paused",
            ) from exc
        try:
            running = self._store.mark_agent_runtime_attempt_running_once(
                attempt.id,
                owner=self._owner,
                lease_seconds=self._lease_seconds,
                # The application does not infer or gate provider side effects.
                # Agent/Skill execution owns that contract; this flag is kept
                # false so legacy effect counters cannot veto a retry.
                effectful=False,
                now=self._now(),
            )
        except AgentRuntimeAttemptStartConflictError as exc:
            raise RoutedCodexExecutionError("runtime_attempt_active") from exc
        return running

    def _runtime_attempts(
        self,
        workload_kind: str,
        workload_key: str,
        *,
        agent_run_id: int | None,
    ) -> list[AgentRuntimeAttempt]:
        if agent_run_id is not None:
            return self._store.list_agent_runtime_attempts(agent_run_id)
        return self._store.list_runtime_operation_attempts(workload_kind, workload_key)

    def _renew_attempt_parent_lease(
        self, attempt: AgentRuntimeAttempt
    ) -> AgentRuntimeAttempt:
        if attempt.agent_run_id is None:
            return self._store.renew_runtime_operation_attempt_lease(
                attempt.id,
                owner=self._owner,
                lease_seconds=self._lease_seconds,
                now=self._now(),
            )
        run = self._store.get_agent_run(attempt.agent_run_id)
        if run is None or not run.lease_owner:
            raise ValueError("agent run lease evidence is missing")
        self._store.renew_agent_run_lease(
            run.id,
            owner=run.lease_owner,
            lease_seconds=self._lease_seconds,
            now=self._now(),
        )
        persisted = self._store.get_agent_runtime_attempt(attempt.id)
        if persisted is None:
            raise ValueError("agent runtime attempt is missing")
        return persisted

    def _next_route_after_failure(
        self,
        *,
        workload_kind: str,
        workload_key: str,
        agent_run_id: int | None,
        failed_attempt: AgentRuntimeAttempt,
        failure: RuntimeFailure,
        required_capabilities: frozenset[str],
    ) -> RuntimeRouteDecision:
        if agent_run_id is None:
            return self._router.next_operation_route(
                workload_kind=workload_kind,
                workload_key=workload_key,
                failed_attempt=failed_attempt,
                failure=failure,
                required_capabilities=required_capabilities,
            )
        run = self._store.get_agent_run(agent_run_id)
        if run is None:
            return RuntimeRouteDecision(None, False, "run_not_found")
        return self._router.next_route(
            run=run,
            failed_attempt=failed_attempt,
            failure=failure,
            required_capabilities=required_capabilities,
        )

    def _session_for_route(
        self, conversation_id: str | None, route_name: str
    ) -> str | None:
        if not conversation_id:
            return None
        return self._store.get_conversation_runtime_session(conversation_id, route_name)

    def _finalized_step(
        self,
        attempt: AgentRuntimeAttempt,
        *,
        stage: str,
        evidence: Callable[[], tuple[str, str, int, int]],
        action: Callable[[], StepT],
    ) -> StepT:
        try:
            return action()
        except RoutedCodexPolicyAbort as exc:
            session_id, reference, start, end = evidence()
            self._terminalize_active_attempt(
                attempt,
                failure_class=(
                    RuntimeFailureClass.SESSION
                    if exc.code == "runtime_session_conflict"
                    else RuntimeFailureClass.CAPABILITY
                ),
                failure_code=exc.code,
                session_id=session_id,
                transcript_reference=reference,
                transcript_start=start,
                transcript_end=end,
            )
            raise RoutedCodexExecutionError(exc.code) from exc
        except Exception as exc:
            session_id, reference, start, end = evidence()
            failure_code = {
                "process_execution": "runtime_executor_failed",
                "result_parse": "runtime_result_invalid",
            }.get(stage, f"runtime_{stage}_failed")
            self._terminalize_active_attempt(
                attempt,
                failure_class=(
                    RuntimeFailureClass.RESULT
                    if stage in {"result_parse", "result_persistence"}
                    else RuntimeFailureClass.PROCESS
                ),
                failure_code=failure_code,
                session_id=session_id,
                transcript_reference=reference,
                transcript_start=start,
                transcript_end=end,
            )
            error_code = {
                "process_execution": "runtime_executor_failed",
                "result_parse": "runtime_result_invalid",
                "result_persistence": "runtime_result_invalid",
            }.get(stage, "runtime_post_start_failed")
            raise RoutedCodexExecutionError(error_code, stage) from exc

    def _terminalize_active_attempt(
        self,
        attempt: AgentRuntimeAttempt,
        *,
        failure_class: RuntimeFailureClass,
        failure_code: str,
        session_id: str,
        transcript_reference: str,
        transcript_start: int,
        transcript_end: int,
    ) -> None:
        persisted = self._store.get_agent_runtime_attempt(attempt.id)
        if persisted is None or persisted.status not in {"starting", "running"}:
            return
        self._store.fail_agent_runtime_attempt(
            persisted.id,
            failure_class.value,
            failure_code,
            False,
            session_id=session_id or persisted.session_id,
            transcript_reference=(
                transcript_reference or persisted.transcript_reference
            ),
            transcript_start=max(transcript_start, 0),
            transcript_end=max(transcript_end, transcript_start, 0),
            owner=self._owner,
            now=self._now(),
        )


_DEFAULT_NAIVE_TIME_ZONE = ZoneInfo("Asia/Shanghai")


def _parse_timestamp(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        parsed = datetime.fromisoformat(value.strip())
    else:
        raise ValueError("timestamp must be a non-empty ISO value")
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=_DEFAULT_NAIVE_TIME_ZONE).astimezone(UTC)
    return parsed.astimezone(UTC)


def _run_identity_matches(caller: AgentRun, persisted: AgentRun) -> bool:
    """Compare the immutable turn identity, but deliberately not mutable safety state."""
    return (
        caller.id,
        caller.reply_task_id,
        caller.execution_generation,
        caller.role,
        caller.proposal_revision,
        caller.turn_attempt,
        caller.parent_agent_run_id,
        caller.operation_id,
    ) == (
        persisted.id,
        persisted.reply_task_id,
        persisted.execution_generation,
        persisted.role,
        persisted.proposal_revision,
        persisted.turn_attempt,
        persisted.parent_agent_run_id,
        persisted.operation_id,
    )


def _failure_matches_persisted_attempt(
    failure: RuntimeFailure, attempt: AgentRuntimeAttempt
) -> bool:
    """Accept only failure fields recorded in the attempt ledger.

    The attempt ledger intentionally persists failure class, code, and failover
    permission. RuntimeFailure's retry and pause hints are not persisted and do
    not affect route selection at this layer.
    """
    return (
        failure.failure_class.value == attempt.failure_class
        and failure.code == attempt.failure_code
        and failure.failover_permitted == attempt.failover_permitted
    )
