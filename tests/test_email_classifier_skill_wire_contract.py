import json
from pathlib import Path

from app.email_classifier_agent import AgentClassificationWireResult


def test_managed_classifier_skill_example_matches_index_only_wire_contract():
    skill_path = Path("ci/shared-skills/ceo-email-classifier/SKILL.md")
    text = skill_path.read_text(encoding="utf-8")
    example = text.split("```json\n", 1)[1].split("\n```", 1)[0]
    result = json.loads(example)

    assert set(result) == set(AgentClassificationWireResult.model_fields)
    assert "unsubscribe_url" not in text
    assert "the service binds the selected index to the exact URL" in " ".join(
        text.split()
    )
