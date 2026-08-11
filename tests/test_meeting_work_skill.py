from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SKILL_PATH = ROOT / "skills" / "ceo-meeting-work" / "SKILL.md"
DEFAULT_PROMPT_PATH = ROOT / "app" / "defaults" / "developer_prompt.md"


def _skill_text() -> str:
    return SKILL_PATH.read_text(encoding="utf-8")


def _skill_prose() -> str:
    return " ".join(_skill_text().split())


@pytest.mark.parametrize(
    ("source", "operation_skills"),
    [
        ("DingTalk meeting or AI Minutes", "`dingtalk-minutes`"),
        ("Lark meeting record", "`lark-vc` and `lark-minutes`"),
        ("DingTalk linked document or file", "`dingtalk-doc` or `dingtalk-drive`"),
        ("Lark linked document or file", "`lark-doc` or `lark-drive`"),
    ],
)
def test_meeting_work_skill_composes_platform_operation_skills(
    source: str,
    operation_skills: str,
):
    assert f"| {source} | {operation_skills} |" in _skill_text()


def test_meeting_work_skill_defines_evidence_driven_workflow():
    text = _skill_prose()

    for required in (
        "Load `ceo-meeting-work`",
        "Load the meeting operation Skill for the source platform before reading evidence",
        "Read the meeting identity and summary first",
        "Read tasks when ownership, delivery, or follow-up matters",
        "Read transcript only when speaker attribution, disagreement, ambiguity, or an unsupported summary requires it",
        "The agent decides what evidence is needed and performs the business judgment",
        "The service supplies references and exact read commands without interpreting meeting content",
        "Do not treat a silent meeting as an ordinary notification",
        "Use canonical `no_action` when the meeting creates no decision, task, clarification, or useful information delivery",
        "Ask for one specifically missing meeting material",
        "Do not ask for a generic meeting recap or for material the loaded operation Skill can read",
    ):
        assert required in text


def test_meeting_work_skill_places_each_mention_with_its_subject():
    text = _skill_prose()

    assert "Place every participant mention adjacent to that person's concrete task, question, decision, or information" in text
    assert "Never put a wall of participant mentions at the start" in text
    assert "Mention a participant once per relevant item" in text
    assert "Do not duplicate the same mention as a heading or preamble" in text


def test_canonical_prompt_delegates_meeting_policy_to_skill():
    text = DEFAULT_PROMPT_PATH.read_text(encoding="utf-8")

    assert "如果新消息或引用涉及“静默会”、AI 听记、会议纪要链接或会议材料" not in text
    assert "涉及专业业务流程时" in text
    assert "agent_cli.read_skill" in text
    assert "只输出合法 JSON" in text


def test_meeting_work_skill_has_no_command_catalog_or_output_schema():
    text = _skill_text()

    for forbidden in (
        "dws minutes get",
        "dws minutes transcription",
        "lark-cli",
        "system_actions",
        "dws_mail_reply",
        "ConsumerAgentResult",
        "AuditAgentResult",
        "re.compile",
    ):
        assert forbidden not in text
