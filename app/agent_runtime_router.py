from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import TypeVar

from app.agent_effects import (
    IDLE_TIMEOUT_SECONDS,
    TOTAL_TIMEOUT_SECONDS,
    McpToolEffectRegistry,
)
from app.agent_result import EffectKind
from app.agent_runtime_config import AgentRuntimeConfig
from app.agent_runtime_contracts import (
    RuntimeCapabilitySnapshot,
    RuntimeFailure,
    RuntimeFailureClass,
    RuntimeRoute,
)
from app.codex_decision import extract_codex_session_id
from app.codex_history import count_codex_session_lines
from app.codex_runtime_adapter import CodexRuntimeAdapter
from app.native_cli_metadata import NativeCliMetadataClassifier
from app.process_runner import ProcessRunResult, run_process_with_idle_timeout
from app.store import (
    AgentRun,
    AgentRuntimeAttempt,
    AgentRuntimeAttemptStartConflictError,
    AutoReplyStore,
    RuntimeAttemptSessionMode,
)

ResultT = TypeVar("ResultT")
ProcessExecutor = Callable[..., ProcessRunResult]
_APPROVED_COMMAND_FACTORY_SEAL = object()


class ExecutionEffectMode(StrEnum):
    READ_ONLY = "read_only"
    EFFECTFUL = "effectful"


@dataclass(frozen=True, slots=True)
class _ApprovedExecutionPolicy:
    effect_mode: ExecutionEffectMode
    seal: object

    def __post_init__(self) -> None:
        if self.seal is not _APPROVED_COMMAND_FACTORY_SEAL:
            raise ValueError("execution policy was not issued by the approved factory")


class ApprovedCodexCommandFactory:
    """Build only the two reviewed Codex command policy shapes.

    The sealed policy is consumed internally by ``RoutedCodexExecution``. A
    caller cannot opt into failover by passing a boolean or an arbitrary policy
    object.
    """

    def __init__(
        self,
        *,
        effect_mode: ExecutionEffectMode,
        developer_instructions: str,
        output_schema_path: Path | None,
        use_output_schema: bool,
        image_paths: tuple[Path, ...],
        seal: object,
    ) -> None:
        if seal is not _APPROVED_COMMAND_FACTORY_SEAL:
            raise ValueError("approved command factories use named constructors")
        developer_instructions = developer_instructions.strip()
        if not developer_instructions:
            raise ValueError("developer_instructions must be non-empty")
        self._policy = _ApprovedExecutionPolicy(effect_mode, seal)
        self._developer_instructions = developer_instructions
        self._output_schema_path = output_schema_path
        self._use_output_schema = use_output_schema
        self._image_paths = image_paths

    @classmethod
    def read_only(
        cls,
        *,
        developer_instructions: str,
        output_schema_path: Path | None = None,
        use_output_schema: bool = False,
        image_paths: Sequence[Path] = (),
    ) -> ApprovedCodexCommandFactory:
        return cls(
            effect_mode=ExecutionEffectMode.READ_ONLY,
            developer_instructions=developer_instructions,
            output_schema_path=output_schema_path,
            use_output_schema=use_output_schema,
            image_paths=tuple(image_paths),
            seal=_APPROVED_COMMAND_FACTORY_SEAL,
        )

    @classmethod
    def effectful(
        cls,
        *,
        developer_instructions: str,
        output_schema_path: Path | None = None,
        use_output_schema: bool = False,
        image_paths: Sequence[Path] = (),
    ) -> ApprovedCodexCommandFactory:
        return cls(
            effect_mode=ExecutionEffectMode.EFFECTFUL,
            developer_instructions=developer_instructions,
            output_schema_path=output_schema_path,
            use_output_schema=use_output_schema,
            image_paths=tuple(image_paths),
            seal=_APPROVED_COMMAND_FACTORY_SEAL,
        )

    @property
    def _approved_policy(self) -> _ApprovedExecutionPolicy:
        return self._policy

    def build(
        self,
        *,
        adapter: CodexRuntimeAdapter,
        route: RuntimeRoute,
        prompt: str,
        session_id: str | None,
    ) -> tuple[list[str], dict[str, str]]:
        read_only = self._policy.effect_mode is ExecutionEffectMode.READ_ONLY
        command = adapter.build_command(
            route=route,
            prompt=prompt,
            session_id=session_id,
            image_paths=list(self._image_paths),
            output_schema_path=self._output_schema_path,
            use_output_schema=self._use_output_schema,
            approval_policy="never" if read_only else "untrusted",
            developer_instructions=self._developer_instructions,
            use_approval_bypass=not read_only,
        )
        return command, adapter.build_env(route)


@dataclass(frozen=True, slots=True)
class RoutedCodexExecutionResult[ResultT]:
    value: ResultT
    route_name: str
    attempt_id: int
    session_id: str
    transcript_start: int
    transcript_end: int


class RoutedCodexExecutionError(RuntimeError):
    def __init__(self, code: str, reason: str = "") -> None:
        self.code = code
        self.reason = reason
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class RuntimeRouteDecision:
    """A bounded runtime-route decision with display-safe static reasons."""

    route: RuntimeRoute | None
    fresh_session: bool
    reason: str


def failover_is_safe(
    *,
    run: AgentRun,
    attempt: AgentRuntimeAttempt,
    failure: RuntimeFailure,
    has_confirmed_receipt: bool,
    recovery_phase: str,
) -> tuple[bool, str]:
    if recovery_phase:
        return False, "recovery_pinned"
    if has_confirmed_receipt:
        return False, "confirmed_receipt"
    if run.side_effect_state != "none":
        return False, "side_effect_state"
    if run.effect_started_count or attempt.first_effect_started_at:
        return False, "effect_started"
    if not failure.failover_permitted:
        return False, "failure_not_eligible"
    return True, "safe"


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
    ) -> RuntimeRoute | None:
        """Select an initial route from current evidence.

        The bootstrap exception preserves the pre-failover OAuth path only. It
        never asserts probe health, never applies to service credentials, and
        is disabled whenever an explicit OAuth snapshot exists.
        """
        return self.first_route_decision(
            required_capabilities=required_capabilities,
            allow_legacy_oauth_bootstrap=allow_legacy_oauth_bootstrap,
        ).route

    def first_route_decision(
        self,
        *,
        required_capabilities: frozenset[str],
        allow_legacy_oauth_bootstrap: bool = False,
    ) -> RuntimeRouteDecision:
        """Return the initial route plus a safe, persisted eligibility reason."""
        now = _parse_timestamp(self._now())
        ineligible: list[str] = []
        for route in self._routes:
            if self._store.active_runtime_route_pause(route.name, now=now) is not None:
                ineligible.append(f"{route.name}=paused")
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
                        missing = sorted(required_capabilities - snapshot.capabilities)
                        reason = "missing_capabilities:" + ",".join(missing)
            ineligible.append(f"{route.name}={reason}")
        return RuntimeRouteDecision(
            None,
            False,
            "no_eligible_route:" + ";".join(ineligible),
        )

    def next_route(
        self,
        *,
        run: AgentRun,
        failed_attempt: AgentRuntimeAttempt,
        failure: RuntimeFailure,
        required_capabilities: frozenset[str],
        recovery_phase: str,
        has_confirmed_receipt: bool = False,
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
        has_persisted_confirmed_receipt = any(
            receipt.completed and receipt.persisted and receipt.safe_to_confirm
            for receipt in self._store.list_agent_execution_receipts(persisted_run.id)
        )
        safe, reason = failover_is_safe(
            run=persisted_run,
            attempt=persisted_attempt,
            failure=failure,
            has_confirmed_receipt=(
                has_confirmed_receipt or has_persisted_confirmed_receipt
            ),
            recovery_phase=recovery_phase,
        )
        if not safe:
            return RuntimeRouteDecision(None, False, reason)

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
        read_only_policy_proven: bool,
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
        if not read_only_policy_proven:
            return RuntimeRouteDecision(None, False, "read_only_policy_unproven")
        if not failure.failover_permitted:
            return RuntimeRouteDecision(None, False, "failure_not_eligible")

        attempts = self._store.list_runtime_operation_attempts(
            workload_kind, workload_key
        )
        if any(attempt.first_effect_started_at for attempt in attempts):
            return RuntimeRouteDecision(None, False, "effect_started")
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
        return not any(
            attempt.route_name == "codex_api"
            and attempt.session_mode == RuntimeAttemptSessionMode.FRESH
            for attempt in attempts
        ) and (
            route.name == "codex_api"
            and failed_attempt.route_name == "codex_api"
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
        return required_capabilities.issubset(snapshot.capabilities)


class RoutedCodexExecution:
    """Execute one persisted generalized workload through bounded Codex routes."""

    def __init__(
        self,
        *,
        store: AutoReplyStore,
        config: AgentRuntimeConfig,
        router: AgentRuntimeRouter,
        adapter: CodexRuntimeAdapter,
        executor: ProcessExecutor = run_process_with_idle_timeout,
        session_id_parser: Callable[[str], str | None] = extract_codex_session_id,
        session_line_counter: Callable[[str], int] = count_codex_session_lines,
        session_effect_probe: Callable[[str, int, int], bool | None] | None = None,
        total_timeout_seconds: float = TOTAL_TIMEOUT_SECONDS,
        idle_timeout_seconds: float = IDLE_TIMEOUT_SECONDS,
        effect_registry: McpToolEffectRegistry | None = None,
        native_cli_classifier: NativeCliMetadataClassifier | None = None,
    ) -> None:
        self._store = store
        self._config = config
        self._router = router
        self._adapter = adapter
        self._executor = executor
        self._session_id_parser = session_id_parser
        self._session_line_counter = session_line_counter
        self._session_effect_probe = session_effect_probe or (
            lambda _session_id, _start, _end: None
        )
        self._total_timeout_seconds = total_timeout_seconds
        self._idle_timeout_seconds = idle_timeout_seconds
        self._effect_registry = effect_registry or McpToolEffectRegistry.default()
        self._native_cli_classifier = (
            native_cli_classifier or NativeCliMetadataClassifier()
        )

    def execute(
        self,
        *,
        workload_kind: str,
        workload_key: str,
        prompt: str,
        command_factory: ApprovedCodexCommandFactory,
        parser: Callable[[str], ResultT],
        conversation_id: str | None = None,
        required_capabilities: frozenset[str] = frozenset(),
    ) -> RoutedCodexExecutionResult[ResultT]:
        if type(command_factory) is not ApprovedCodexCommandFactory:
            raise ValueError("command_factory must be approved")
        policy = command_factory._approved_policy
        if policy.seal is not _APPROVED_COMMAND_FACTORY_SEAL:
            raise ValueError("command_factory policy is not approved")
        prompt = prompt.strip()
        if not prompt:
            raise ValueError("prompt must be non-empty")

        existing_attempts = self._store.list_runtime_operation_attempts(
            workload_kind, workload_key
        )
        if policy.effect_mode is ExecutionEffectMode.EFFECTFUL and existing_attempts:
            raise RoutedCodexExecutionError("runtime_effectful_replay_blocked")
        if existing_attempts:
            latest = existing_attempts[-1]
            if latest.status in {"starting", "running"}:
                raise RoutedCodexExecutionError("runtime_attempt_active")
            if latest.status == "completed":
                raise RoutedCodexExecutionError("runtime_operation_completed")
            if latest.status != "failed":
                raise RoutedCodexExecutionError("runtime_attempt_state_invalid")
            persisted_failure = RuntimeFailure(
                failure_class=RuntimeFailureClass(latest.failure_class),
                code=latest.failure_code,
                detail="persisted runtime failure",
                failover_permitted=latest.failover_permitted,
            )
            decision = self._router.next_operation_route(
                workload_kind=workload_kind,
                workload_key=workload_key,
                failed_attempt=latest,
                failure=persisted_failure,
                required_capabilities=required_capabilities,
                read_only_policy_proven=True,
            )
        else:
            decision = self._router.first_route_decision(
                required_capabilities=required_capabilities
            )
        if decision.route is None:
            raise RoutedCodexExecutionError(
                "runtime_route_unavailable", decision.reason
            )
        route = decision.route
        route_session_id = (
            None
            if decision.fresh_session
            else self._session_for_route(conversation_id, route.name)
        )
        active_attempt = self._claim_and_start(
            workload_kind, workload_key, route, route_session_id, policy.effect_mode
        )

        while True:
            transcript_start = (
                self._session_line_counter(route_session_id) if route_session_id else 0
            )
            line_count = 0
            effect_policy_violated = False

            def observe_stdout_line(line: str) -> None:
                nonlocal line_count, effect_policy_violated, active_attempt
                line_count += 1
                if _line_violates_read_only_policy(
                    line,
                    effect_registry=self._effect_registry,
                    native_cli_classifier=self._native_cli_classifier,
                ):
                    persisted = self._store.get_agent_runtime_attempt(active_attempt.id)
                    if persisted is not None and not persisted.first_effect_started_at:
                        active_attempt = (
                            self._store.note_runtime_attempt_effect_started(
                                active_attempt.id
                            )
                        )
                    if policy.effect_mode is ExecutionEffectMode.READ_ONLY:
                        effect_policy_violated = True

            command, env = command_factory.build(
                adapter=self._adapter,
                route=route,
                prompt=prompt,
                session_id=route_session_id,
            )
            try:
                process = self._executor(
                    command,
                    prompt=prompt,
                    env=env,
                    total_timeout_seconds=self._total_timeout_seconds,
                    idle_timeout_seconds=self._idle_timeout_seconds,
                    on_stdout_line=observe_stdout_line,
                )
            except Exception as exc:
                self._fail_unclassified(active_attempt)
                raise RoutedCodexExecutionError(
                    "runtime_executor_failed", "process_executor_failed"
                ) from exc

            observed_session_id = (
                self._session_id_parser(process.stdout) or route_session_id or ""
            )
            transcript_end = max(transcript_start + line_count, transcript_start)
            if observed_session_id:
                transcript_end = max(
                    transcript_end,
                    self._session_line_counter(observed_session_id),
                )
            transcript_reference = (
                f"codex_session:{observed_session_id}" if observed_session_id else ""
            )

            if process.returncode == 0 and not process.timed_out:
                if effect_policy_violated:
                    self._fail_policy_violation(
                        active_attempt,
                        observed_session_id,
                        transcript_reference,
                        transcript_start,
                        transcript_end,
                    )
                    raise RoutedCodexExecutionError("runtime_effect_policy_violation")
                try:
                    value = parser(process.stdout)
                except Exception as exc:
                    self._store.fail_agent_runtime_attempt(
                        active_attempt.id,
                        RuntimeFailureClass.RESULT.value,
                        "runtime_result_invalid",
                        False,
                        session_id=observed_session_id,
                        transcript_reference=transcript_reference,
                        transcript_start=transcript_start,
                        transcript_end=transcript_end,
                    )
                    raise RoutedCodexExecutionError("runtime_result_invalid") from exc
                completed = self._store.complete_agent_runtime_attempt(
                    active_attempt.id,
                    observed_session_id,
                    transcript_reference,
                    transcript_start,
                    transcript_end,
                )
                if conversation_id and observed_session_id:
                    self._store.upsert_conversation_runtime_session(
                        conversation_id, route.name, observed_session_id
                    )
                return RoutedCodexExecutionResult(
                    value=value,
                    route_name=route.name,
                    attempt_id=completed.id,
                    session_id=observed_session_id,
                    transcript_start=transcript_start,
                    transcript_end=transcript_end,
                )

            failure = self._adapter.classify_failure(
                process.stdout,
                process.stderr,
                process.returncode,
                timed_out=process.timed_out,
                timeout_kind=process.timeout_kind,
            )
            if (
                observed_session_id
                and policy.effect_mode is ExecutionEffectMode.READ_ONLY
                and self._probe_session_effect(
                    observed_session_id, transcript_start, transcript_end
                )
                is not False
            ):
                persisted = self._store.get_agent_runtime_attempt(active_attempt.id)
                if persisted is not None and not persisted.first_effect_started_at:
                    active_attempt = self._store.note_runtime_attempt_effect_started(
                        active_attempt.id
                    )
                effect_policy_violated = True
            failed_attempt = self._store.fail_agent_runtime_attempt(
                active_attempt.id,
                failure.failure_class.value,
                failure.code,
                failure.failover_permitted,
                session_id=observed_session_id,
                transcript_reference=transcript_reference,
                transcript_start=transcript_start,
                transcript_end=transcript_end,
            )
            if failure.route_pause_required:
                self._store.open_runtime_route_pause(
                    route.name,
                    failure.code,
                    datetime.now(UTC) + self._config.retry_delay,
                )
            if effect_policy_violated:
                raise RoutedCodexExecutionError("runtime_effect_policy_violation")
            if policy.effect_mode is ExecutionEffectMode.EFFECTFUL:
                raise RoutedCodexExecutionError("runtime_execution_failed")

            next_decision = self._router.next_operation_route(
                workload_kind=workload_kind,
                workload_key=workload_key,
                failed_attempt=failed_attempt,
                failure=failure,
                required_capabilities=required_capabilities,
                read_only_policy_proven=True,
            )
            if next_decision.route is None:
                raise RoutedCodexExecutionError(
                    "runtime_execution_failed", next_decision.reason
                )
            route = next_decision.route
            route_session_id = (
                None
                if next_decision.fresh_session
                else self._session_for_route(conversation_id, route.name)
            )
            successor = self._claim_and_start(
                workload_kind, workload_key, route, route_session_id, policy.effect_mode
            )
            self._store.mark_agent_runtime_attempt_superseded(failed_attempt.id)
            active_attempt = successor

    def _claim_and_start(
        self,
        workload_kind: str,
        workload_key: str,
        route: RuntimeRoute,
        session_id: str | None,
        effect_mode: ExecutionEffectMode,
    ) -> AgentRuntimeAttempt:
        attempt = self._store.claim_runtime_operation_attempt(
            workload_kind,
            workload_key,
            route.name,
            route.runtime_kind.value,
            route.credential_mode.value,
            route.model,
            session_mode=(
                RuntimeAttemptSessionMode.RESUME
                if session_id
                else RuntimeAttemptSessionMode.FRESH
            ),
            source_session_id=session_id or "",
        )
        try:
            running = self._store.mark_agent_runtime_attempt_running_once(attempt.id)
        except AgentRuntimeAttemptStartConflictError as exc:
            raise RoutedCodexExecutionError("runtime_attempt_active") from exc
        if effect_mode is ExecutionEffectMode.EFFECTFUL:
            return self._store.note_runtime_attempt_effect_started(running.id)
        return running

    def _session_for_route(
        self, conversation_id: str | None, route_name: str
    ) -> str | None:
        if not conversation_id:
            return None
        return self._store.get_conversation_runtime_session(conversation_id, route_name)

    def _probe_session_effect(
        self, session_id: str, transcript_start: int, transcript_end: int
    ) -> bool | None:
        try:
            return self._session_effect_probe(
                session_id, transcript_start, transcript_end
            )
        except (OSError, RuntimeError, TypeError, ValueError):
            return None

    def _fail_unclassified(self, attempt: AgentRuntimeAttempt) -> None:
        persisted = self._store.get_agent_runtime_attempt(attempt.id)
        if persisted is None or persisted.status not in {"starting", "running"}:
            return
        self._store.fail_agent_runtime_attempt(
            persisted.id,
            RuntimeFailureClass.PROCESS.value,
            "runtime_executor_failed",
            False,
        )

    def _fail_policy_violation(
        self,
        attempt: AgentRuntimeAttempt,
        session_id: str,
        transcript_reference: str,
        transcript_start: int,
        transcript_end: int,
    ) -> None:
        self._store.fail_agent_runtime_attempt(
            attempt.id,
            RuntimeFailureClass.CAPABILITY.value,
            "runtime_effect_policy_violation",
            False,
            session_id=session_id,
            transcript_reference=transcript_reference,
            transcript_start=transcript_start,
            transcript_end=transcript_end,
        )


def _line_violates_read_only_policy(
    line: str,
    *,
    effect_registry: McpToolEffectRegistry,
    native_cli_classifier: NativeCliMetadataClassifier,
) -> bool:
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return False
    if not isinstance(payload, dict) or payload.get("type") != "item.started":
        return False
    item = payload.get("item")
    if not isinstance(item, dict):
        return False
    metadata = item.get("metadata")
    if isinstance(metadata, dict) and metadata.get("effect") == "effectful":
        return True
    item_type = item.get("type")
    if item_type == "command_execution":
        command = native_cli_classifier.classify(item)
        return command is None or command.effect is not EffectKind.READ_ONLY
    if item_type == "mcp_tool_call":
        call = effect_registry.classify(item)
        return call is None or call.effect is not EffectKind.READ_ONLY
    return False


def _parse_timestamp(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        parsed = datetime.fromisoformat(value.strip())
    else:
        raise ValueError("timestamp must be a non-empty ISO value")
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
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
