# Use the real Consumer/Store pipeline and only replace native CLI execution.
from hashlib import sha256
import pytest
from pathlib import Path

from app.consumer_agent import ConsumerAgentRunner
from app.native_agent_output import native_output_schema_path
from tests.test_consumer_agent import (
    CapturingExecutor, _consumer_runtime_dependencies, _result_jsonl,
    context as context, native_executor_records as native_executor_records,
    store as store, task as task,
)


from app.agent_turn_runner import (
    AgentTurnProcess,
    _consumer_session_contract_hash,
)
from app.agent_runtime_contracts import CredentialMode, RuntimeKind, RuntimeRoute
from app.store import AgentRole, AutoReplyStore


def test_consumer_session_reuse_requires_the_same_developer_contract(tmp_path):
    store = AutoReplyStore(tmp_path / "sessions.sqlite3")
    route = RuntimeRoute(
        name="codex_oauth",
        runtime_kind=RuntimeKind.CODEX_CLI,
        credential_mode=CredentialMode.LOCAL_OAUTH,
        model="gpt-6-luna",
    )
    base_contract = "selected-skills:verified-skill-set"
    old_developer = "The previous Consumer instructions."
    current_developer = "The updated Consumer instructions."
    store.upsert_conversation_runtime_session(
        "conversation-1",
        route.name,
        "old-session",
        _consumer_session_contract_hash(base_contract, old_developer),
    )
    process = AgentTurnProcess.__new__(AgentTurnProcess)
    process.store = store
    process.task = type("Task", (), {"conversation_id": "conversation-1"})()

    reused = AgentTurnProcess._session_for_route(
        process,
        route,
        role=AgentRole.CONSUMER,
        requested_session_id="old-session",
        conversation_contract_hash=_consumer_session_contract_hash(
            base_contract, old_developer
        ),
    )
    stale = AgentTurnProcess._session_for_route(
        process,
        route,
        role=AgentRole.CONSUMER,
        requested_session_id="old-session",
        conversation_contract_hash=_consumer_session_contract_hash(
            base_contract, current_developer
        ),
    )

    assert reused == "old-session"
    assert stale is None


@pytest.mark.parametrize("contract_change", [None, "mismatch", "missing"])
def test_consumer_reuses_only_matching_conversation_route_session(store, task, context, contract_change):
    config, router, adapter = _consumer_runtime_dependencies(store, routes="codex_oauth")
    current_schema = {
        "$native_output_schema_sha256": sha256(
            native_output_schema_path("consumer", purpose="general").read_bytes()
        ).hexdigest()
    }
    first = CapturingExecutor(_result_jsonl(session="session-a"))
    ConsumerAgentRunner(
        store=store, workspace=Path("/workspace"), executor=first,
        runtime_config=config, runtime_router=router, codex_adapter=adapter,
        codex_session_exists=lambda _: True,
        codex_session_wire_schema=lambda _: current_schema,
    ).run(task, context, proposal_revision=0, parent_agent_run_id=None)
    assert store.get_conversation_runtime_session(task.conversation_id, "codex_oauth") == "session-a"

    saved_hash = store.get_conversation_runtime_session_contract_hash(task.conversation_id, "codex_oauth")
    if contract_change is not None:
        store.upsert_conversation_runtime_session(
            task.conversation_id, "codex_oauth", "session-a",
            saved_hash + "-changed" if contract_change == "mismatch" else "",
        )
    expected_session = "session-a" if contract_change is None else "session-b"
    second = CapturingExecutor(_result_jsonl(session=expected_session))
    ConsumerAgentRunner(
        store=store, workspace=Path("/workspace"), executor=second,
        runtime_config=config, runtime_router=router, codex_adapter=adapter,
        codex_session_exists=lambda _: True,
        codex_session_wire_schema=lambda _: current_schema,
    ).run(task, context, proposal_revision=0, parent_agent_run_id=None)

    if contract_change is None:
        assert second.commands[0][3:5] == ["exec", "resume"]
        assert second.commands[0][-2:] == ["session-a", "-"]
    else:
        assert "resume" not in second.commands[0]
    assert store.get_conversation_runtime_session(task.conversation_id, "codex_oauth") == expected_session
    assert store.get_conversation_runtime_session_contract_hash(task.conversation_id, "codex_oauth") == saved_hash
