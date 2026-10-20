from types import SimpleNamespace

from app.agent_runtime_contracts import CredentialMode, RuntimeKind, RuntimeRoute
from app.agent_turn_runner import AgentTurnProcess
from app.business_skills import BusinessSkillCatalogEntry
from app.consumer_agent import (
    consumer_selected_skill_source_fingerprints,
    consumer_selected_skill_session_contract_hash,
    consumer_task_session_contract_hash,
)
from app.store import AgentRole


def test_selected_skill_body_changes_consumer_contract(monkeypatch, tmp_path):
    skill = tmp_path / "people" / "SKILL.md"
    skill.parent.mkdir()
    skill.write_text("---\nname: people-review\ndescription: People\n---\nOld rule\n")
    entry = BusinessSkillCatalogEntry("people-review", skill, "People")
    monkeypatch.setattr(
        "app.consumer_agent.installed_runtime_skills",
        lambda *, names=None: (entry,) if "people-review" in (names or ()) else (),
    )

    first = consumer_selected_skill_source_fingerprints(("people-review",), ())
    first_contract = consumer_selected_skill_session_contract_hash(first)

    skill.write_text("---\nname: people-review\ndescription: People\n---\nNew rule\n")
    second = consumer_selected_skill_source_fingerprints(("people-review",), ())
    second_contract = consumer_selected_skill_session_contract_hash(second)

    assert first != second
    assert first_contract != second_contract


def test_oa_ocr_handoff_refreshes_only_oa_consumer_sessions():
    selected = "selected-skills:unchanged"
    assert consumer_task_session_contract_hash(selected, purpose_name="general") == selected
    assert consumer_task_session_contract_hash("", purpose_name="general") == ""
    oa_contract = consumer_task_session_contract_hash(selected, purpose_name="oa")
    assert oa_contract.startswith("oa-ocr-handoff:")
    assert oa_contract != selected
    assert oa_contract == consumer_task_session_contract_hash(selected, purpose_name="oa")


def test_frozen_skill_fingerprint_uses_round_snapshot_not_live_file(monkeypatch, tmp_path):
    skill = tmp_path / "people" / "SKILL.md"
    skill.parent.mkdir()
    skill.write_text("live newer content")
    entry = BusinessSkillCatalogEntry("people-review", skill, "People")
    monkeypatch.setattr(
        "app.consumer_agent.installed_runtime_skills",
        lambda *, names=None: (entry,) if "people-review" in (names or ()) else (),
    )
    frozen = (SimpleNamespace(name="people-review", content="round snapshot"),)

    first = consumer_selected_skill_source_fingerprints(("people-review",), frozen)
    skill.write_text("different live content")
    second = consumer_selected_skill_source_fingerprints(("people-review",), frozen)

    assert first == second


def test_consumer_route_session_requires_matching_skill_contract():
    task = SimpleNamespace(conversation_id="conversation-1")
    process = object.__new__(AgentTurnProcess)
    process.task = task
    calls = []

    def get_session(conversation_id, route_name, *, required_contract_hash=None):
        calls.append((conversation_id, route_name, required_contract_hash))
        return "session-current" if required_contract_hash == "selected-skills:current-contract" else None

    process.store = SimpleNamespace(get_conversation_runtime_session=get_session)
    route = RuntimeRoute(
        name="codex_oauth",
        runtime_kind=RuntimeKind.CODEX_CLI,
        credential_mode=CredentialMode.LOCAL_OAUTH,
        model="gpt-6.1-sol",
    )

    assert process._session_for_route(
        route,
        role=AgentRole.CONSUMER,
        requested_session_id="session-old",
        conversation_contract_hash="selected-skills:old-contract",
    ) is None
    assert process._session_for_route(
        route,
        role=AgentRole.CONSUMER,
        requested_session_id="session-old",
        conversation_contract_hash="selected-skills:current-contract",
    ) == "session-current"
    assert calls == [
        ("conversation-1", "codex_oauth", "selected-skills:old-contract"),
        ("conversation-1", "codex_oauth", "selected-skills:current-contract"),
    ]
