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
        safe, reason = failover_is_safe(
            run=run,
            attempt=failed_attempt,
            failure=failure,
            has_confirmed_receipt=has_confirmed_receipt,
            recovery_phase=recovery_phase,
        )
        if not safe:
            return RuntimeRouteDecision(None, False, reason)

        now = _parse_timestamp(self._now())
        attempts = self._store.list_agent_runtime_attempts(run.id)
        attempted_routes = {attempt.route_name for attempt in attempts}

        for route in self._routes:
            if route.name in attempted_routes:
                if self._fresh_session_retry_is_permitted(
                    route=route,
                    failed_attempt=failed_attempt,
                    failure=failure,
                    attempts=attempts,
                ):
                    return RuntimeRouteDecision(route, True, "fresh_session_retry")
                continue
            if self._store.active_runtime_route_pause(route.name, now=now) is not None:
                continue
            if not self._snapshot_is_current_and_eligible(
                route=route,
                required_capabilities=required_capabilities,
                now=now,
            ):
                continue
            return RuntimeRouteDecision(route, False, "eligible_route")
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
            and bool(failed_attempt.source_session_id)
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
