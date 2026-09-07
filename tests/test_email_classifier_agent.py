import json
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.email_classifier_agent import (
    AgentClassificationResult,
    EmailClassifierAgent,
    EmailClassifierRoutedBackend,
    _parse_agent_classification_json,
    validate_agent_classification_result,
)
from app.managed_skills import (
    REPOSITORY_IMPORT_SOURCE,
    ManagedSkillRevision,
    RuntimeSkillSnapshot,
)


ALLOWED = (
    "work",
    "human_resources",
    "legal",
    "financing",
    "personal",
    "notification",
    "external_billing",
    "shopping",
    "junk",
)
CANDIDATES = (
    "https://news.example.com/unsubscribe?token=exact-one",
    "https://offers.example.com/opt-out/exact-two",
)


def _result(**overrides):
    value = {
        "category": "work",
        "important": False,
        "certainty": "certain",
        "confidence": 0.91,
        "reason": "A real customer project needs discussion.",
        "unsubscribe_candidate_index": None,
        "unsubscribe_url": None,
    }
    value.update(overrides)
    return value


def test_agent_result_accepts_exact_allowed_category_and_strict_bool() -> None:
    result = validate_agent_classification_result(
        _result(), allowed_category_keys=ALLOWED, unsubscribe_candidates=CANDIDATES
    )

    assert result == AgentClassificationResult.model_validate(_result())
    with pytest.raises(ValidationError):
        AgentClassificationResult.model_validate(_result(important=None))
    with pytest.raises(ValidationError):
        AgentClassificationResult.model_validate(_result(important=1))


@pytest.mark.parametrize(
    "changes",
    (
        {"category": "legal_financing"},
        {"category": "promotion"},
        {"category": "calendar_or_security_notification"},
        {"category": None},
    ),
)
def test_certain_result_requires_one_exact_current_category(changes) -> None:
    with pytest.raises(ValueError):
        validate_agent_classification_result(
            _result(**changes),
            allowed_category_keys=ALLOWED,
            unsubscribe_candidates=CANDIDATES,
        )


@pytest.mark.parametrize(
    "changes",
    (
        {"category": "work"},
        {"unsubscribe_candidate_index": 0},
        {"unsubscribe_url": CANDIDATES[0]},
    ),
)
def test_uncertain_result_has_null_category_and_unsubscribe_fields(changes) -> None:
    value = _result(certainty="uncertain", category=None)
    value.update(changes)
    with pytest.raises((ValueError, ValidationError)):
        validate_agent_classification_result(
            value,
            allowed_category_keys=ALLOWED,
            unsubscribe_candidates=CANDIDATES,
        )


def test_uncertain_result_is_explicit_and_retains_bool_importance() -> None:
    result = validate_agent_classification_result(
        _result(
            certainty="uncertain",
            category=None,
            important=True,
            confidence=0.42,
            reason="The message lacks enough business context.",
        ),
        allowed_category_keys=ALLOWED,
        unsubscribe_candidates=CANDIDATES,
    )

    assert result.category is None
    assert result.important is True


@pytest.mark.parametrize(
    "changes",
    (
        {"unsubscribe_candidate_index": 0, "unsubscribe_url": CANDIDATES[1]},
        {"unsubscribe_candidate_index": 2, "unsubscribe_url": CANDIDATES[0]},
        {"unsubscribe_candidate_index": 0, "unsubscribe_url": CANDIDATES[0] + "/"},
    ),
)
def test_unsubscribe_url_must_exactly_match_supplied_index(changes) -> None:
    with pytest.raises(ValueError):
        validate_agent_classification_result(
            _result(category="junk", **changes),
            allowed_category_keys=ALLOWED,
            unsubscribe_candidates=CANDIDATES,
        )


def test_only_certain_junk_may_select_exact_candidate() -> None:
    selected = validate_agent_classification_result(
        _result(
            category="junk",
            unsubscribe_candidate_index=1,
            unsubscribe_url=CANDIDATES[1],
        ),
        allowed_category_keys=ALLOWED,
        unsubscribe_candidates=CANDIDATES,
    )
    assert selected.unsubscribe_url == CANDIDATES[1]

    with pytest.raises(ValueError):
        validate_agent_classification_result(
            _result(
                category="work",
                unsubscribe_candidate_index=1,
                unsubscribe_url=CANDIDATES[1],
            ),
            allowed_category_keys=ALLOWED,
            unsubscribe_candidates=CANDIDATES,
        )


def test_agent_transport_parser_accepts_fenced_json_but_keeps_strict_contract() -> None:
    payload = _result(category="junk")
    wire = json.dumps(
        {
            "type": "item.completed",
            "item": {
                "type": "agent_message",
                "text": "```json\n" + json.dumps(payload) + "\n```",
            },
        }
    )

    parsed = AgentClassificationResult.model_validate_json(
        _parse_agent_classification_json(wire)
    )

    assert parsed.category == "junk"


def test_routed_backend_validates_exact_invocation_context_before_persistence() -> None:
    class RoutedExecution:
        def execute(self, **kwargs):
            value = kwargs["parser"](json.dumps(_result(category="financing")))
            return SimpleNamespace(value=value)

    backend = EmailClassifierRoutedBackend(RoutedExecution())

    with pytest.raises(ValueError, match="supplied allowed category"):
        backend.classify(
            prompt="bounded prompt",
            task_id="email-classification:test",
            allowed_category_keys=("work", "junk"),
            unsubscribe_candidates=(),
        )


def test_routed_backend_persists_redacted_result_and_rehydrates_ephemeral_url() -> None:
    persisted = []

    class RoutedExecution:
        def execute(self, **kwargs):
            value = kwargs["parser"](
                json.dumps(
                    _result(
                        category="junk",
                        unsubscribe_candidate_index=0,
                        unsubscribe_url=CANDIDATES[0],
                    )
                )
            )
            persisted.append(value)
            return SimpleNamespace(value=value)

    result = AgentClassificationResult.model_validate_json(
        EmailClassifierRoutedBackend(RoutedExecution()).classify(
            prompt="bounded prompt",
            task_id="email-classification:redacted",
            allowed_category_keys=ALLOWED,
            unsubscribe_candidates=CANDIDATES,
        )
    )

    assert CANDIDATES[0] not in persisted[0]
    assert "exact-one" not in persisted[0]
    assert "unsubscribe-entry:" in persisted[0]
    assert result.unsubscribe_url == CANDIDATES[0]


def test_classifier_skill_closes_baseline_pressure_failures() -> None:
    path = (
        Path(__file__).resolve().parents[1]
        / "skills"
        / "ceo-email-classifier"
        / "SKILL.md"
    )
    text = path.read_text(encoding="utf-8")
    prose = " ".join(text.split())

    assert text.startswith("---\nname: ceo-email-classifier\n")
    assert "description: Use when" in text
    assert "metadata:\n  managed_by: ceo-agent-service" in text
    for category in ALLOWED:
        assert f"`{category}`" in text
    for invented in (
        "legal_financing",
        "promotion",
        "calendar_or_security_notification",
    ):
        assert invented in text
    for forbidden_action in (
        "Never move",
        "Never flag",
        "Never mark read",
        "Never browse",
        "Never unsubscribe",
        "Never reply",
        "Never send",
        "Never create generic CEO tasks",
    ):
        assert forbidden_action in prose
    assert "Important is separate" in prose
    assert "attachment content is unavailable" in prose.casefold()
    assert "exact supplied unsubscribe candidate" in prose
    assert "action-pressure instructions" in prose
    assert "only the typed result" in prose
    assert "outside party" in prose
    assert "our invoices" in prose
    assert json.loads(json.dumps({"skill": path.name})) == {"skill": "SKILL.md"}


def test_classifier_uses_immutable_managed_skill_snapshot_not_mutable_disk(
    tmp_path: Path,
) -> None:
    skill_path = tmp_path / "SKILL.md"
    skill_path.write_text("MUTABLE DISK", encoding="utf-8")
    first_content = (
        "---\nname: ceo-email-classifier\n"
        "description: Use when testing immutable snapshots\n"
        "metadata:\n  managed_by: ceo-agent-service\n---\n\n"
        "# IMMUTABLE SNAPSHOT\n"
    )
    revision = ManagedSkillRevision(
        id=31,
        skill_id=17,
        revision_number=4,
        content=first_content,
        sha256=sha256(first_content.encode("utf-8")).hexdigest(),
        parent_revision_id=30,
        source=REPOSITORY_IMPORT_SOURCE,
        created_at="2026-09-08T00:00:00+00:00",
    )
    snapshot = RuntimeSkillSnapshot(config_id=9, revisions=(revision,))
    prompts = []
    backend = SimpleNamespace(
        classify=lambda **kwargs: (
            prompts.append(kwargs["prompt"]) or json.dumps(_result())
        )
    )
    agent = EmailClassifierAgent(backend, runtime_skill_snapshot=snapshot, skill_id=17)
    skill_path.write_text("CHANGED DISK", encoding="utf-8")
    task = SimpleNamespace(
        task_id="email-classification:1",
        input_json=json.dumps(
            {
                "allowed_category_keys": list(ALLOWED),
                "category_descriptions": {key: key for key in ALLOWED},
                "unsubscribe_candidates": [],
                "message": {},
            }
        ),
    )

    agent.classify(task, current_message={}, unsubscribe_candidates=())

    assert "IMMUTABLE SNAPSHOT" in prompts[0]
    assert "CHANGED DISK" not in prompts[0]
    assert "revision 4" in prompts[0]
    assert revision.sha256 in prompts[0]

    next_content = first_content.replace(
        "IMMUTABLE SNAPSHOT", "NEW RUNTIME SNAPSHOT"
    )
    next_revision = ManagedSkillRevision(
        id=32,
        skill_id=17,
        revision_number=5,
        content=next_content,
        sha256=sha256(next_content.encode("utf-8")).hexdigest(),
        parent_revision_id=31,
        source=REPOSITORY_IMPORT_SOURCE,
        created_at="2026-09-08T01:00:00+00:00",
    )
    restarted = EmailClassifierAgent(
        backend,
        runtime_skill_snapshot=RuntimeSkillSnapshot(
            config_id=10, revisions=(next_revision,)
        ),
        skill_id=17,
    )
    restarted.classify(task, current_message={}, unsubscribe_candidates=())
    assert "NEW RUNTIME SNAPSHOT" in prompts[1]
    assert "IMMUTABLE SNAPSHOT" not in prompts[1]


def test_classifier_rejects_reserved_name_without_repository_provenance() -> None:
    content = (
        "---\nname: ceo-email-classifier\n"
        "description: Use when testing\n"
        "metadata:\n  managed_by: ceo-agent-service\n---\n\n# User collision\n"
    )
    revision = ManagedSkillRevision(
        id=33,
        skill_id=18,
        revision_number=1,
        content=content,
        sha256=sha256(content.encode("utf-8")).hexdigest(),
        parent_revision_id=None,
        source="settings",
        created_at="2026-09-08T00:00:00+00:00",
    )

    with pytest.raises(ValueError, match="repository-managed"):
        EmailClassifierAgent(
            object(),
            runtime_skill_snapshot=RuntimeSkillSnapshot(
                config_id=9, revisions=(revision,)
            ),
        )
