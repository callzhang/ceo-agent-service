from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SKILL_PATH = ROOT / "skills" / "ceo-calendar-invite" / "SKILL.md"
DEFAULT_PROMPT_PATH = ROOT / "app" / "defaults" / "developer_prompt.md"
RUNTIME_PROMPT_PATH = ROOT / "data" / "prompts" / "developer_prompt.md"


def _skill_text() -> str:
    return SKILL_PATH.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("case", "decision", "clarification"),
    [
        ("clear_value", "accept", "no"),
        ("worth_holding_but_uncertain", "tentative", "no"),
        ("no_principal_input_needed", "decline", "no"),
        ("missing_attendance_value", "clarify_inviter", "yes"),
        ("missing_description_but_clear_title", "accept", "no"),
        ("silent_meeting_with_material", "process_material", "no"),
        ("silent_meeting_without_material", "clarify_exact_material", "yes"),
    ],
)
def test_calendar_skill_defines_behavior_case(
    case: str,
    decision: str,
    clarification: str,
):
    row = f"| `{case}` | `{decision}` | `{clarification}` |"

    assert row in _skill_text()


def test_calendar_skill_defines_complete_read_and_decision_workflow():
    text = _skill_text()

    for required in (
        "Load `dingtalk-calendar` before every calendar read or write",
        "Load `dingtalk-chat` before a chat fallback",
        "title, time, organizer, attendees, description, comments, linked materials",
        "the principal's current response state",
        "conflicting accepted events",
        "A missing description alone is not a reason to clarify",
        "customer, product, personnel, or cross-team",
        "ask the verified inviter one concrete factual question",
        "prefer a calendar comment when the installed capability supports it",
        "A resolvable factual question is a Consumer proposal, never `needs_human`",
        "read and process every linked material",
        "ask for that exact material",
        "rereads the live event state",
        "already-applied exact response",
        "already-sent exact clarification",
        "Reuse confirmed facts",
    ):
        assert required in text


def test_developer_prompts_delegate_calendar_policy_to_business_skills():
    default_text = DEFAULT_PROMPT_PATH.read_text(encoding="utf-8")
    prompt_texts = [default_text]
    if RUNTIME_PROMPT_PATH.is_file():
        runtime_text = RUNTIME_PROMPT_PATH.read_text(encoding="utf-8")
        assert runtime_text == default_text
        prompt_texts.append(runtime_text)

    for text in prompt_texts:
        assert "<var: calendar_rules_path>" not in text
        assert "涉及专业业务流程时" in text
        assert "agent_cli.read_skill" in text
        assert "最具体适用的业务 Skill" in text
        assert "只输出合法 JSON" in text
        assert "user_response.mode 必须是" in text
