from pathlib import Path
import json

from app.task_agent import build_task_agent_prompt
from app.task_models import WorkItem


def test_current_skill_owns_shared_guidance_once_and_task_keeps_unique_contract(
    monkeypatch,
):
    path = Path(__file__).parents[1] / "ci/shared-skills/ceo-work-tracking/SKILL.md"
    monkeypatch.setattr("app.task_agent.WORK_TRACKING_SKILL_PATH", path)
    work = WorkItem.model_validate(
        {
            "summary": "Original anonymous source",
            "source": {"type": "local_file", "ref": "synthetic-source"},
            "context": {"source_conversation_kind": "file"},
        }
    )
    prompt = build_task_agent_prompt(
        work, "EXACT_CURRENT_CONTEXT", current_time="2026-10-12T10:00:00Z"
    )
    full_prompt = build_task_agent_prompt(
        work, "EXACT_CURRENT_CONTEXT", current_time="2026-10-12T10:00:00Z",
        inline_skill=True,
    )
    assert path.read_text().strip() not in prompt
    assert str(path) in prompt
    assert path.read_text().strip() in full_prompt
    assert (
        full_prompt.count(
            "All source_excerpt values are exact contiguous quotes, preserving punctuation,"
        )
        == 1
    )
    assert "EXACT_CURRENT_CONTEXT" in prompt
    assert "2026-10-12T10:00:00Z" in prompt
    work_json = prompt.split("Current Work Item JSON:\n", 1)[1]
    delivered_work = json.JSONDecoder().raw_decode(work_json)[0]
    assert delivered_work["source"]["ref"] == "synthetic-source"
    # The installed Skill can omit these rules even when the CI Skill has them.
    task_only = full_prompt.replace(path.read_text().strip(), "")
    for rule in (
        "or neither only for an unresolved",
        "status and business_relevance may only change through update_fields",
        "When promoting a suggestion, omit the suggestion field",
        "Never include a skip decision",
        "An existing card already reflecting the same facts does not need a new proposal",
        "nearest blocking outcome",
        "A Task title must include the decisive unresolved Project outcome",
        "name the payment date/schedule in the title",
        "Actual owner_evidence uses",
    ):
        assert rule in task_only
