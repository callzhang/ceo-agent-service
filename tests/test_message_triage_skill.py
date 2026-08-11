from pathlib import Path

from app.consumer_agent import CORE_DYNAMIC_SKILL_BODY

import pytest


ROOT = Path(__file__).resolve().parents[1]
SKILL_PATH = ROOT / "skills" / "ceo-message-triage" / "SKILL.md"
DEFAULT_PROMPT_PATH = ROOT / "app" / "defaults" / "developer_prompt.md"


def _skill_text() -> str:
    return SKILL_PATH.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("case", "handling"),
    [
        (
            "direct_decision_request",
            "Use canonical `proposal` with a grounded response or authorized action.",
        ),
        (
            "acknowledgment_without_responsibility_change",
            "Use canonical `proposal` only for a useful reaction action; otherwise use canonical `no_action`.",
        ),
        (
            "broadcast_without_principal_action",
            "Use canonical `no_action`.",
        ),
        (
            "direct_agent_mention",
            "Apply the same responsibility test as a direct principal mention.",
        ),
        (
            "participant_can_supply_missing_fact",
            "Use canonical `proposal` with one concrete clarification action.",
        ),
        (
            "newer_context_completed_matter",
            "Use canonical `no_action`.",
        ),
    ],
)
def test_message_triage_skill_defines_behavior_case(case: str, handling: str):
    assert f"- `{case}`: {handling}" in _skill_text()


def test_message_triage_skill_uses_only_canonical_consumer_outcomes():
    text = _skill_text()

    assert "`reaction_or_no_action`" not in text
    assert "`clarification_proposal`" not in text
    assert "A reaction or clarification is an action inside a canonical `proposal`" in text
    assert "never a workflow-specific outcome" in text


def test_message_triage_skill_defines_complete_judgment_workflow():
    text = _skill_text()

    for required in (
        "Load `dingtalk-chat` before reading conversation context",
        "direct mention of the configured agent identity",
        "same as a direct mention of the principal",
        "requires a decision, commitment, explanation, correction, or next step",
        "does not change responsibility, delivery, timing, permission, cost, or approval",
        "reaction action in a canonical `proposal` only when it adds useful acknowledgment",
        "A broadcast mention alone does not create principal responsibility",
        "one concrete factual question to that participant in a canonical `proposal`",
        "not an A/B selection and not `needs_human`",
        "newer context shows completion, supersession, or a sufficient response",
        "Do not invent recipients, accounts, identifiers, responsibilities, or targets",
        "Do not create a follow-up that the message did not request",
    ):
        assert required in text


def test_canonical_prompt_delegates_message_triage_judgment_to_skill():
    text = DEFAULT_PROMPT_PATH.read_text(encoding="utf-8")

    assert "单聊里如果对方只是表示感谢、确认收到、认可或客气收口" not in text
    assert "群聊里的 @所有人、全员通知、流程提醒" not in text
    assert "有些消息不需要正式文字回复，但适合轻量表达态度" not in text
    assert CORE_DYNAMIC_SKILL_BODY in text
    assert "agent_cli.read_skill" in text
    assert "2. [output_contracts] Output Contracts:" in text


def test_message_triage_skill_has_no_command_catalog_or_python_router():
    text = _skill_text()

    for forbidden in (
        "dws chat message send",
        "dws chat message list",
        "--group",
        "--user",
        "re.compile",
        "sender_name ==",
    ):
        assert forbidden not in text

    assert "An `@all` broadcast with no principal action" in text
    assert "Reuse confirmed facts" in text
