"""One decision for what happens after a runtime attempt fails.

Two loops execute runtime turns: Agent turns in app/agent_turn_runner.py and
every other workload in app/agent_runtime_router.py. Each used to decide on
its own when to pause a route, when to retry it and when to move on, so the
same provider failure was handled differently depending on which loop saw it.

Derek, 2026-09-17: one fallback path, not several. Both loops ask this module
what to do; the loop only carries it out, because the evidence it has to write
down for an attempt (sessions, transcripts, receipts) differs between them.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from app.agent_runtime_contracts import (
    RuntimeFailure,
    RuntimeFailureClass,
    RuntimeRoute,
)
from app.external_retry import retry_delay_seconds
from app.store import AgentRuntimeAttempt

# A provider that says it is full (429, model at capacity) is retried on the
# same route this many times, with growing waits, before the route is paused
# and the turn moves to the next runtime.
CAPACITY_RETRIES_ON_SAME_ROUTE = 3
CAPACITY_RETRY_BASE_DELAY_SECONDS = 10.0


class RouteDecision(Protocol):
    """The router's own decision shape, without importing the router."""

    route: RuntimeRoute | None
    fresh_session: bool
    reason: str


@dataclass(frozen=True, slots=True)
class FallbackPlan:
    """What the loop does next: pause, wait, and which route to run."""

    route: RuntimeRoute | None
    fresh_session: bool
    reason: str
    pause_route: bool
    retry_same_route: bool = False
    wait_seconds: float = 0.0


def is_provider_full(failure: RuntimeFailure) -> bool:
    """Whether the provider reported being full rather than unusable."""
    return (
        failure.failure_class is RuntimeFailureClass.CAPACITY
        and failure.retryable_on_same_route
    )


def is_session_writer_conflict(failure: RuntimeFailure) -> bool:
    return (
        failure.failure_class is RuntimeFailureClass.SESSION
        and failure.code == "codex_session_writer_conflict"
        and failure.retryable_on_same_route
    )


def consecutive_capacity_failures(
    attempts: Sequence[AgentRuntimeAttempt], route_name: str
) -> int:
    """Count the newest unbroken run of provider-full failures on one route."""
    count = 0
    for attempt in reversed(attempts):
        if (
            attempt.route_name != route_name
            or attempt.failure_class != RuntimeFailureClass.CAPACITY.value
        ):
            break
        count += 1
    return count


def consecutive_session_writer_conflicts(
    attempts: Sequence[AgentRuntimeAttempt], route_name: str
) -> int:
    count = 0
    for attempt in reversed(attempts):
        if (
            attempt.route_name != route_name
            or attempt.failure_code != "codex_session_writer_conflict"
        ):
            break
        count += 1
    return count


def plan_runtime_fallback(
    *,
    route: RuntimeRoute,
    failure: RuntimeFailure,
    attempts: Sequence[AgentRuntimeAttempt],
    select_next_route: Callable[[], RouteDecision],
    same_route_retry_permitted: bool = True,
) -> FallbackPlan:
    """Decide what follows one failed attempt on ``route``.

    ``attempts`` are this workload's attempts including the one that just
    failed, newest last, so a run of provider-full failures is visible.
    """
    if same_route_retry_permitted and is_provider_full(failure):
        failures = consecutive_capacity_failures(attempts, route.name)
        if 0 < failures <= CAPACITY_RETRIES_ON_SAME_ROUTE:
            # Waiting out a full provider keeps the route available to every
            # other workload; only a route that stays full is taken away.
            # A full provider does not invalidate the session, so the retry
            # resumes it; the Agent loop treats fresh_session as "clear an
            # incompatible session" and fails the run on any other failure.
            return FallbackPlan(
                route=route,
                fresh_session=False,
                reason="capacity_retry",
                pause_route=False,
                retry_same_route=True,
                wait_seconds=retry_delay_seconds(
                    CAPACITY_RETRY_BASE_DELAY_SECONDS, failures - 1
                ),
            )
    if same_route_retry_permitted and is_session_writer_conflict(failure):
        conflicts = consecutive_session_writer_conflicts(attempts, route.name)
        if 0 < conflicts <= CAPACITY_RETRIES_ON_SAME_ROUTE:
            return FallbackPlan(
                route=route,
                fresh_session=False,
                reason="session_writer_retry",
                pause_route=False,
                retry_same_route=True,
                wait_seconds=retry_delay_seconds(
                    CAPACITY_RETRY_BASE_DELAY_SECONDS, conflicts - 1
                ),
            )
    decision = select_next_route()
    return FallbackPlan(
        route=decision.route,
        fresh_session=decision.fresh_session,
        reason=decision.reason,
        pause_route=failure.route_pause_required,
    )
