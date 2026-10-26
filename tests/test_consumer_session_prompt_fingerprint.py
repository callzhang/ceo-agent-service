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
