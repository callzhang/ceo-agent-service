from pathlib import Path

import pytest

from app.agent_runtime_production import build_production_agent_runtime
from app.agent_runtime_router import RuntimeRouteDecision
from app.agent_turn_runner import AgentTurnProcess, RuntimeRouteUnavailableError
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

    with pytest.raises(RuntimeRouteUnavailableError, match="runtime_route_unavailable"):
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
