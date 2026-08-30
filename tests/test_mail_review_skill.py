from pathlib import Path

from app.consumer_agent import CORE_DYNAMIC_SKILL_BODY

import pytest


ROOT = Path(__file__).resolve().parents[1]
SKILL_PATH = ROOT / "skills" / "ceo-mail-review" / "SKILL.md"
DEFAULT_PROMPT_PATH = ROOT / "app" / "defaults" / "developer_prompt.md"


def _skill_text() -> str:
    return SKILL_PATH.read_text(encoding="utf-8")


def _skill_prose() -> str:
    return " ".join(_skill_text().split())


@pytest.mark.parametrize(
    ("source", "operation_skill"),
    [
        ("DingTalk mail", "`dingtalk-mail`"),
        ("Lark mail", "`lark-mail`"),
        ("DingTalk linked material", "`dingtalk-doc`, `dingtalk-aitable`, or `dingtalk-drive`"),
        ("Lark linked material", "`lark-doc`, `lark-base`, or `lark-drive`"),
    ],
)
def test_mail_review_skill_composes_platform_operation_skills(
    source: str,
    operation_skill: str,
):
    assert f"| {source} | {operation_skill} |" in _skill_text()


def test_mail_review_skill_defines_complete_review_workflow():
    text = _skill_prose()

    for required in (
        "Load `ceo-mail-review`",
        "Treat a truncated card or quoted preview only as a locator",
        "Resolve the principal's mailbox and the complete original message or thread",
        "Do not ask the sender to paste content that the loaded mail Skill can read",
        "Inspect every linked material needed for the requested judgment",
        "Check the current thread, sent state, and safe prior receipts before proposing a reply",
        "Do not propose or execute a duplicate reply",
        "Every reply requires explicit reply authorization",
        "For a DingTalk or Lark review, the current request must explicitly authorize replying",
        "For `channel=email`, the current immutable ActionPlan is the authorization",
        "Review-only, summarize-only, or approval-only requests do not authorize a mail reply",
        "The agent performs the business judgment",
        "The service supplies references and exact commands without interpreting mail or linked content",
    ):
        assert required in text


def test_mail_review_skill_uses_only_specific_missing_material_questions():
    text = _skill_prose()

    assert "ask one concrete question naming the specifically missing mail or linked material" in text
    assert "explain why it is needed for the requested judgment" in text
    assert "Do not ask for a generic resend" in text
    assert "Do not infer or invent unread content" in text


def test_mail_review_skill_separates_linked_materials_from_email_attachments():
    text = _skill_prose()

    assert "A linked material is not an email attachment" in text
    assert "DingTalk or Lark interactive mail review" in text
    assert "Do not open or inspect linked content for a `channel=email` task" in text
    assert "Do not open or inspect attachment content" in text
    assert "attachment metadata only" in text
    assert "Task 11 unsubscribe browser execution" in text


def test_canonical_prompt_delegates_mail_policy_to_skill():
    text = DEFAULT_PROMPT_PATH.read_text(encoding="utf-8")

    assert "如果已读完原邮件和依赖材料、当前消息明确授权回复邮件" not in text
    assert "决策 agent 不得直接发送邮件" not in text
    assert CORE_DYNAMIC_SKILL_BODY in text
    assert "independently selects and reads every applicable" in text
    assert "2. [output_contracts] Output Contracts:" in text


def test_mail_review_skill_has_no_command_catalog_or_output_schema():
    text = _skill_text()

    for forbidden in (
        "dws mail mailbox",
        "dws mail message",
        "lark-cli",
        "system_actions",
        "dws_mail_reply",
        "ConsumerAgentResult",
        "AuditAgentResult",
        "re.compile",
    ):
        assert forbidden not in text
