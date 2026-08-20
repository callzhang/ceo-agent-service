from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from app.agent_runtime_contracts import (
    RuntimeCapabilitySnapshot,
    RuntimeFailure,
    RuntimeRoute,
)
from app.store import (
    AgentRun,
    AgentRuntimeAttempt,
    AutoReplyStore,
    RuntimeAttemptSessionMode,
)


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
        safe, reason = failover_is_safe(
            run=persisted_run,
            attempt=persisted_attempt,
            failure=failure,
            has_confirmed_receipt=has_confirmed_receipt,
            recovery_phase=recovery_phase,
        )
        if not safe:
            return RuntimeRouteDecision(None, False, reason)

        now = _parse_timestamp(self._now())
        attempted_routes = {attempt.route_name for attempt in attempts}

        for route in self._routes:
            fresh_session_retry = False
            if route.name in attempted_routes:
                fresh_session_retry = self._fresh_session_retry_is_permitted(
                    route=route,
                    failed_attempt=persisted_attempt,
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
