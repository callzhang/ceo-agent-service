"""The single decision both runtime loops follow after an attempt fails."""

import pytest

from app.agent_runtime_contracts import (
    CredentialMode,
    RuntimeFailure,
    RuntimeFailureClass,
    RuntimeKind,
    RuntimeRoute,
)
from app.runtime_fallback import (
    CAPACITY_RETRIES_ON_SAME_ROUTE,
    FallbackPlan,
    plan_runtime_fallback,
)

ROUTE = RuntimeRoute(
    name="codex_api",
    runtime_kind=RuntimeKind.CODEX_CLI,
    credential_mode=CredentialMode.SERVICE_API,
    model="gpt-5-codex",
)
NEXT_ROUTE = RuntimeRoute(
    name="claude_oauth",
    runtime_kind=RuntimeKind.CLAUDE_CLI,
    credential_mode=CredentialMode.LOCAL_OAUTH,
    model="sonnet",
)


class Decision:
    def __init__(self, route, fresh_session=False, reason="eligible_route"):
        self.route = route
        self.fresh_session = fresh_session
        self.reason = reason


class Attempt:
    def __init__(self, route_name, failure_class, failure_code=""):
        self.route_name = route_name
        self.failure_class = failure_class
        self.failure_code = failure_code


def _failure(failure_class, *, same_route=False, pause=True):
    return RuntimeFailure(
        failure_class=failure_class,
        code="test_failure",
        detail="redacted",
        retryable_on_same_route=same_route,
        failover_permitted=True,
        route_pause_required=pause,
    )


def _full(count):
    return [Attempt("codex_api", RuntimeFailureClass.CAPACITY.value)] * count


def _plan(failure, attempts, next_route=NEXT_ROUTE, **kwargs):
    return plan_runtime_fallback(
        route=ROUTE,
        failure=failure,
        attempts=attempts,
        select_next_route=lambda: Decision(next_route),
        **kwargs,
    )


@pytest.mark.parametrize("failures", range(1, CAPACITY_RETRIES_ON_SAME_ROUTE + 1))
def test_a_full_provider_is_waited_out_on_the_same_route(failures):
    plan = _plan(_failure(RuntimeFailureClass.CAPACITY, same_route=True), _full(failures))

    assert plan.route is ROUTE
    assert plan.retry_same_route is True
    assert plan.pause_route is False
    assert plan.wait_seconds == 10.0 * 2 ** (failures - 1)


@pytest.mark.parametrize("failures", range(1, CAPACITY_RETRIES_ON_SAME_ROUTE + 1))
def test_a_same_route_capacity_retry_resumes_the_session(failures):
    """Production 2026-09-17: this plan said fresh_session=True. The Agent loop
    reads that as "clear an incompatible session", whose guard accepts only
    session_route_incompatible, so every Agent run failed on its first 429 with
    "fresh session retry lacks persisted resume evidence" - 27 runs that day,
    no wait and no failover. A full provider does not invalidate the session."""
    plan = _plan(_failure(RuntimeFailureClass.CAPACITY, same_route=True), _full(failures))

    assert plan.retry_same_route is True
    assert plan.fresh_session is False


def test_a_provider_still_full_after_the_retries_switches_runtime():
    plan = _plan(
        _failure(RuntimeFailureClass.CAPACITY, same_route=True),
        _full(CAPACITY_RETRIES_ON_SAME_ROUTE + 1),
    )

    assert plan.route is NEXT_ROUTE
    assert plan.retry_same_route is False
    assert plan.pause_route is True
    assert plan.wait_seconds == 0.0


def test_session_writer_conflict_waits_on_same_route_without_pausing_provider():
    failure = RuntimeFailure(
        failure_class=RuntimeFailureClass.SESSION,
        code="codex_session_writer_conflict",
        detail="A Codex session has another writer.",
        retryable_on_same_route=True,
    )
    plan = _plan(failure, [Attempt(ROUTE.name, RuntimeFailureClass.SESSION.value, failure.code)])

    assert plan.route is ROUTE
    assert plan.fresh_session is False
    assert plan.retry_same_route is True
    assert plan.pause_route is False
    assert plan.wait_seconds > 0


def test_session_writer_conflict_stops_same_route_retry_at_ceiling():
    failure = RuntimeFailure(
        failure_class=RuntimeFailureClass.SESSION,
        code="codex_session_writer_conflict",
        detail="A Codex session has another writer.",
        retryable_on_same_route=True,
    )
    attempts = [
        Attempt(ROUTE.name, RuntimeFailureClass.SESSION.value, failure.code)
        for _ in range(CAPACITY_RETRIES_ON_SAME_ROUTE + 1)
    ]

    plan = _plan(failure, attempts)

    assert plan.retry_same_route is False


def test_context_overflow_retries_once_on_same_route_with_fresh_session():
    failure = RuntimeFailure(
        failure_class=RuntimeFailureClass.SESSION,
        code="codex_context_window_exceeded",
        detail="Codex compaction exceeded the model context window.",
        retryable_on_same_route=True,
        failover_permitted=True,
    )

    plan = _plan(
        failure,
        [Attempt(ROUTE.name, RuntimeFailureClass.SESSION.value, failure.code)],
    )

    assert plan.route is ROUTE
    assert plan.fresh_session is True
    assert plan.retry_same_route is True
    assert plan.pause_route is False


def test_repeated_context_overflow_uses_normal_route_fallback():
    failure = RuntimeFailure(
        failure_class=RuntimeFailureClass.SESSION,
        code="codex_context_window_exceeded",
        detail="Codex compaction exceeded the model context window.",
        retryable_on_same_route=True,
        failover_permitted=True,
    )
    attempts = [
        Attempt(ROUTE.name, RuntimeFailureClass.SESSION.value, failure.code),
        Attempt(ROUTE.name, RuntimeFailureClass.SESSION.value, failure.code),
    ]

    plan = _plan(failure, attempts)

    assert plan.route is NEXT_ROUTE
    assert plan.pause_route is False


def test_an_older_run_of_capacity_failures_does_not_count():
    """A success or another failure in between starts the count over."""
    attempts = _full(3) + [Attempt("codex_api", RuntimeFailureClass.TRANSPORT.value)]

    plan = _plan(_failure(RuntimeFailureClass.CAPACITY, same_route=True), attempts)

    assert plan.route is NEXT_ROUTE


def test_another_route_s_capacity_failures_do_not_count():
    attempts = [Attempt("codex_oauth", RuntimeFailureClass.CAPACITY.value)] * 4 + _full(1)

    plan = _plan(_failure(RuntimeFailureClass.CAPACITY, same_route=True), attempts)

    assert plan.retry_same_route is True


def test_a_capacity_failure_the_route_cannot_retry_switches_at_once():
    """An exhausted plan does not refill while a turn waits."""
    plan = _plan(_failure(RuntimeFailureClass.CAPACITY), _full(1))

    assert plan.route is NEXT_ROUTE
    assert plan.pause_route is True


@pytest.mark.parametrize(
    "failure_class",
    [RuntimeFailureClass.AUTHENTICATION, RuntimeFailureClass.TRANSPORT],
)
def test_any_other_failure_takes_the_next_route(failure_class):
    plan = _plan(_failure(failure_class, same_route=True), [])

    assert plan.route is NEXT_ROUTE
    assert plan.retry_same_route is False


def test_a_failure_that_does_not_pause_the_route_leaves_it_open():
    plan = _plan(_failure(RuntimeFailureClass.PROCESS, pause=False), [])

    assert plan.pause_route is False


def test_no_eligible_route_is_reported_as_it_is():
    plan = _plan(_failure(RuntimeFailureClass.PROCESS), [], next_route=None)

    assert plan == FallbackPlan(
        route=None, fresh_session=False, reason="eligible_route", pause_route=True
    )


def test_a_turn_that_may_not_retry_its_route_switches_on_a_full_provider():
    plan = _plan(
        _failure(RuntimeFailureClass.CAPACITY, same_route=True),
        _full(1),
        same_route_retry_permitted=False,
    )

    assert plan.route is NEXT_ROUTE
