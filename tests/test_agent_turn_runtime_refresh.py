from pathlib import Path

import pytest

from app.agent_runtime_production import build_production_agent_runtime
from app.agent_runtime_router import RuntimeRouteDecision
from app.agent_runtime_router import route_unavailable_code
from app.agent_turn_runner import (
    AgentTurnProcess,
    RuntimeRouteUnavailableError,
)
from app.agent_wire_contracts import parse_consumer_agent_wire_result
from app.store import AgentRole, AutoReplyStore


def _task_and_run(store: AutoReplyStore):
    store.enqueue_reply_task(
        conversation_id="cid-runtime-refresh",
        conversation_title="Runtime refresh",
        single_chat=False,
        trigger_message_id="msg-runtime-refresh",
        trigger_create_time="2026-08-21 18:00:00",
        trigger_sender="Derek",
        trigger_text="Verify the runtime boundary.",
        execution_generation="runtime-refresh-generation",
    )
    task = store.claim_reply_tasks(limit=1)[0]
    claim = store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=None,
        operation_id="",
        owner="consumer",
    )
    return task, claim.run


def test_turn_refreshes_capabilities_before_initial_route_decision(tmp_path):
    store = AutoReplyStore(tmp_path / "store.sqlite3")
    task, run = _task_and_run(store)
    calls: list[str] = []

    def refresh(*, force=False):
        calls.append(f"refresh:{force}")

    class Router:
        def first_route_decision(self, **_kwargs):
            calls.append("route")
            return RuntimeRouteDecision(None, False, "snapshot_expired")

    process = AgentTurnProcess(
        store=store,
        task=task,
        workspace=tmp_path,
        owner="consumer",
        runtime_router=Router(),
        refresh_runtime_capabilities=refresh,
    )

    with pytest.raises(RuntimeRouteUnavailableError, match="runtime_execution_failed"):
        process.execute(
            run=run,
            prompt="Read-only decision.",
            session_id=None,
            developer_instructions="Return the result schema.",
            configure_command=lambda _command: None,
            parse_result=parse_consumer_agent_wire_result,
            persist_conversation_session=False,
        )

    assert calls == ["refresh:False", "route", "refresh:True", "route"]


def test_production_runtime_retains_the_service_owned_refresh_callable(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("CEO_AGENT_RUNTIME_ROUTES", "codex_oauth")
    store = AutoReplyStore(tmp_path / "store.sqlite3")

    def refresh(*, force=False):
        return None

    runtime = build_production_agent_runtime(
        store=store,
        workspace=Path(tmp_path),
        refresh_runtime_capabilities=refresh,
    )

    assert runtime.refresh_runtime_capabilities is refresh


@pytest.mark.parametrize(
    ("ineligible_routes", "expected"),
    [
        ((), "runtime_execution_failed"),
        # Probe snapshots are not ready yet after a service start: defer.
        (
            (("codex_oauth", "snapshot_missing"), ("codex_api", "snapshot_missing")),
            "runtime_provider_unreachable",
        ),
        # A route paused for a transient provider failure recovers on its own.
        (
            (
                ("codex_oauth", "paused:codex_provider_overloaded"),
                ("codex_api", "snapshot_expired"),
            ),
            "runtime_provider_unreachable",
        ),
        # Only authentication pauses are an authentication outcome.
        (
            (
                ("codex_oauth", "paused:codex_login_required"),
                ("codex_api", "paused:codex_provider_auth_failed"),
            ),
            "runtime_provider_auth_failed",
        ),
        (
            (
                ("codex_oauth", "missing_capabilities:image_input"),
                ("codex_api", "surface_missing:image_input"),
            ),
            "runtime_capability_missing",
        ),
    ],
)
def test_route_unavailable_code_is_derived_from_typed_route_reasons(
    ineligible_routes, expected
):
    assert route_unavailable_code(ineligible_routes) == expected
    error = RuntimeRouteUnavailableError(
        "no_eligible_route", ineligible_routes=ineligible_routes
    )
    assert error.code == expected


def test_route_name_containing_auth_does_not_classify_as_authentication():
    error = RuntimeRouteUnavailableError(
        "no_eligible_route:codex_oauth=snapshot_missing",
        ineligible_routes=(("codex_oauth", "snapshot_missing"),),
    )

    assert error.code == "runtime_provider_unreachable"
