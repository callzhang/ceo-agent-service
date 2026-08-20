from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.agent_runtime_contracts import (
    CredentialMode,
    RuntimeCapabilitySnapshot,
    RuntimeFailure,
    RuntimeFailureClass,
    RuntimeKind,
    RuntimeRoute,
)
from app.agent_runtime_router import AgentRuntimeRouter, failover_is_safe
from app.store import AgentRole, AutoReplyStore

NOW = datetime(2026, 8, 20, 10, 0, tzinfo=UTC)


def route(name: str) -> RuntimeRoute:
    return RuntimeRoute(
        name=name,
        runtime_kind=RuntimeKind.CODEX_CLI,
        credential_mode=(
            CredentialMode.SERVICE_API
            if name == "codex_api"
            else CredentialMode.LOCAL_OAUTH
        ),
        model="gpt-5.5",
    )


def snapshot(
    route_name: str,
    *,
    capabilities: frozenset[str] = frozenset({"structured_output"}),
    healthy: bool = True,
    checked_at: str = "2026-08-20 09:59:00",
    expires_at: str = "2026-08-20 10:05:00",
    failure: RuntimeFailure | None = None,
) -> RuntimeCapabilitySnapshot:
    return RuntimeCapabilitySnapshot(
        route_name=route_name,
        capabilities=capabilities,
        healthy=healthy,
        checked_at=checked_at,
        expires_at=expires_at,
        failure=failure,
    )


def failover_failure(code: str = "codex_login_required") -> RuntimeFailure:
    return RuntimeFailure(
        failure_class=RuntimeFailureClass.AUTHENTICATION,
        code=code,
        detail="provider failure",
        failover_permitted=True,
    )


def make_router(
    store: AutoReplyStore,
    *,
    routes: tuple[RuntimeRoute, ...] = (route("codex_oauth"), route("codex_api")),
    snapshots: dict[str, RuntimeCapabilitySnapshot] | None = None,
) -> AgentRuntimeRouter:
    return AgentRuntimeRouter(
        routes=routes,
        store=store,
        snapshots=(
            snapshots
            if snapshots is not None
            else {item.name: snapshot(item.name) for item in routes}
        ),
        now=lambda: NOW,
    )


@pytest.fixture
def store(tmp_path) -> AutoReplyStore:
    return AutoReplyStore(tmp_path / "agent-runtime-router.sqlite3")


@pytest.fixture
def running_attempt(store: AutoReplyStore):
    assert store.enqueue_reply_task(
        conversation_id="cid-router",
        conversation_title="Router",
        single_chat=False,
        trigger_message_id="msg-router",
        trigger_create_time="2026-08-20 09:00:00",
        trigger_sender="Derek",
        trigger_text="route this",
    )
    task = store.claim_reply_tasks(limit=1)[0]
    run = store.claim_agent_run(
        task.id,
        "initial",
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=None,
        operation_id=f"direct-agent:{task.id}:initial",
        owner="router-test",
    ).run
    attempt = store.claim_agent_runtime_attempt(
        run.id, "codex_oauth", "codex_cli", "local_oauth", "gpt-5.5"
    )
    return store.mark_agent_runtime_attempt_running(attempt.id)


@pytest.fixture
def router(store: AutoReplyStore) -> AgentRuntimeRouter:
    return make_router(store)


def next_route(
    router,
    store,
    attempt,
    *,
    failure=None,
    capabilities=frozenset({"structured_output"}),
    recovery_phase="",
    has_confirmed_receipt=False,
):
    return router.next_route(
        run=store.get_agent_run(attempt.agent_run_id),
        failed_attempt=store.get_agent_runtime_attempt(attempt.id),
        failure=failure or failover_failure(),
        required_capabilities=capabilities,
        recovery_phase=recovery_phase,
        has_confirmed_receipt=has_confirmed_receipt,
    )


def test_effect_start_blocks_failover(router, store, running_attempt):
    store.note_runtime_attempt_effect_started(running_attempt.id)

    decision = next_route(router, store, running_attempt)

    assert decision.route is None
    assert decision.reason == "effect_started"


def test_oauth_failure_selects_api_once(router, store, running_attempt):
    decision = next_route(router, store, running_attempt)

    assert decision.route.name == "codex_api"
    assert decision.fresh_session is False


@pytest.mark.parametrize(
    ("run_update", "attempt_update", "failure", "receipt", "recovery_phase", "reason"),
    [
        ({}, {}, failover_failure(), False, "reconciliation", "recovery_pinned"),
        ({}, {}, failover_failure(), True, "", "confirmed_receipt"),
        (
            {"side_effect_state": "unknown"},
            {},
            failover_failure(),
            False,
            "",
            "side_effect_state",
        ),
        (
            {"effect_started_count": 1},
            {},
            failover_failure(),
            False,
            "",
            "effect_started",
        ),
        (
            {},
            {"first_effect_started_at": "2026-08-20 09:59:00"},
            failover_failure(),
            False,
            "",
            "effect_started",
        ),
        (
            {},
            {},
            RuntimeFailure(
                failure_class=RuntimeFailureClass.PROCESS,
                code="process_failed",
                detail="safe",
                failover_permitted=False,
            ),
            False,
            "",
            "failure_not_eligible",
        ),
    ],
)
def test_failover_safety_uses_only_persisted_evidence(
    store,
    running_attempt,
    run_update,
    attempt_update,
    failure,
    receipt,
    recovery_phase,
    reason,
):
    run = store.get_agent_run(running_attempt.agent_run_id).model_copy(
        update=run_update
    )
    attempt = running_attempt.model_copy(update=attempt_update)

    safe, result_reason = failover_is_safe(
        run=run,
        attempt=attempt,
        failure=failure,
        has_confirmed_receipt=receipt,
        recovery_phase=recovery_phase,
    )

    assert (safe, result_reason) == (False, reason)


def test_router_uses_configured_route_order(store, running_attempt):
    ordered = (route("codex_api"), route("codex_oauth"), route("third"))
    router = make_router(store, routes=ordered)

    decision = next_route(router, store, running_attempt)

    assert decision.route == ordered[0]


def test_attempted_routes_are_never_selected_again(router, store, running_attempt):
    store.fail_agent_runtime_attempt(
        running_attempt.id, "authentication", "codex_login_required", True
    )
    api = store.claim_agent_runtime_attempt(
        running_attempt.agent_run_id, "codex_api", "codex_cli", "service_api", "gpt-5.5"
    )

    decision = next_route(router, store, api)

    assert decision.route is None
    assert decision.reason == "no_eligible_route"


def test_active_route_pause_skips_only_that_route(store, running_attempt):
    routes = (route("codex_oauth"), route("codex_api"), route("third"))
    store.open_runtime_route_pause(
        "codex_api", "provider_unavailable", retry_at="2026-08-20 10:30:00"
    )
    router = make_router(store, routes=routes)

    decision = next_route(router, store, running_attempt)

    assert decision.route.name == "third"


def test_expired_route_pause_does_not_block_route(store, running_attempt):
    store.open_runtime_route_pause(
        "codex_api", "provider_unavailable", retry_at="2026-08-20 09:59:00"
    )
    router = make_router(store)

    decision = next_route(router, store, running_attempt)

    assert decision.route.name == "codex_api"


@pytest.mark.parametrize(
    "snapshots",
    [
        {},
        {"codex_api": snapshot("codex_api", healthy=False, failure=failover_failure())},
        {"codex_api": snapshot("codex_api", healthy=False)},
        {"codex_api": snapshot("codex_api", expires_at="2026-08-20T09:59:00Z")},
        {"codex_api": snapshot("codex_api", expires_at="not-a-timestamp")},
        {"codex_api": snapshot("codex_api", checked_at="not-a-timestamp")},
        {"codex_api": snapshot("codex_api", checked_at="2026-08-20 10:01:00")},
        {"codex_api": snapshot("different-route")},
        {"codex_api": snapshot("codex_api", failure=failover_failure())},
    ],
)
def test_missing_or_invalid_capability_snapshots_fail_closed(
    store, running_attempt, snapshots
):
    router = make_router(store, snapshots=snapshots)

    decision = next_route(router, store, running_attempt)

    assert decision.route is None
    assert decision.reason == "no_eligible_route"


def test_required_capabilities_must_all_be_proven(store, running_attempt):
    router = make_router(
        store,
        snapshots={
            "codex_api": snapshot(
                "codex_api", capabilities=frozenset({"structured_output", "read_only"})
            )
        },
    )

    selected = next_route(
        router,
        store,
        running_attempt,
        capabilities=frozenset({"structured_output", "read_only"}),
    )
    rejected = next_route(
        router,
        store,
        running_attempt,
        capabilities=frozenset({"structured_output", "effect_visibility"}),
    )

    assert selected.route.name == "codex_api"
    assert rejected.route is None
    assert rejected.reason == "no_eligible_route"


def test_session_route_incompatible_fails_closed_without_a_persisted_resume_marker(
    store, running_attempt
):
    store.fail_agent_runtime_attempt(
        running_attempt.id, "authentication", "codex_login_required", True
    )
    api = store.claim_agent_runtime_attempt(
        running_attempt.agent_run_id, "codex_api", "codex_cli", "service_api", "gpt-5.5"
    )
    router = make_router(store)

    decision = next_route(
        router, store, api, failure=failover_failure("session_route_incompatible")
    )

    assert decision.route is None
    assert decision.fresh_session is False
    assert decision.reason == "needs_context"


def test_second_codex_api_attempt_cannot_unlock_a_fresh_retry(store, running_attempt):
    store.fail_agent_runtime_attempt(
        running_attempt.id, "authentication", "codex_login_required", True
    )
    first_api = store.claim_agent_runtime_attempt(
        running_attempt.agent_run_id, "codex_api", "codex_cli", "service_api", "gpt-5.5"
    )
    store.fail_agent_runtime_attempt(
        first_api.id, "session", "session_route_incompatible", True
    )
    second_api = store.claim_agent_runtime_attempt(
        running_attempt.agent_run_id, "codex_api", "codex_cli", "service_api", "gpt-5.5"
    )
    router = make_router(store)

    decision = next_route(
        router,
        store,
        second_api,
        failure=failover_failure("session_route_incompatible"),
    )

    assert decision.route is None
    assert decision.fresh_session is False
    assert decision.reason == "needs_context"


def test_no_route_available_has_a_deterministic_safe_reason(store, running_attempt):
    router = make_router(
        store,
        snapshots={
            "codex_api": snapshot(
                "codex_api", healthy=False, failure=failover_failure()
            )
        },
    )

    decision = next_route(router, store, running_attempt)

    assert decision.route is None
    assert decision.fresh_session is False
    assert decision.reason == "no_eligible_route"


def test_route_decision_is_frozen_and_does_not_expose_failure_detail(
    router, store, running_attempt
):
    decision = next_route(
        router,
        store,
        running_attempt,
        failure=RuntimeFailure(
            failure_class=RuntimeFailureClass.AUTHENTICATION,
            code="codex_login_required",
            detail="API key secret-value must not appear",
            failover_permitted=True,
        ),
    )

    with pytest.raises((ValidationError, TypeError, AttributeError)):
        decision.reason = "API key secret-value"
    assert "secret-value" not in repr(decision)
