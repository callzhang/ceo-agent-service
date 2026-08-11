from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SKILL_PATH = ROOT / "skills" / "ceo-personnel-communication" / "SKILL.md"
DEFAULT_PROMPT_PATH = ROOT / "app" / "defaults" / "developer_prompt.md"


def _skill_text() -> str:
    return SKILL_PATH.read_text(encoding="utf-8")


def _skill_prose() -> str:
    return " ".join(_skill_text().split())


@pytest.mark.parametrize(
    ("case", "handling"),
    [
        (
            "internal_performance_or_compensation",
            "Load `ceo-personnel-communication` and treat the employee as the subject.",
        ),
        (
            "hr_direct_chat_within_responsibility",
            "The recipient may receive the supported personnel information after HR responsibility and scope are established from live organization evidence.",
        ),
        (
            "non_hr_direct_chat_about_third_party",
            "Do not disclose unsupported sensitive details; provide only authorized supported facts or ask for the missing authorization or purpose.",
        ),
        (
            "external_candidate_evaluation",
            "Load both `ceo-personnel-communication` and `stardust-interview`.",
        ),
        (
            "personnel_oa",
            "Load both `ceo-personnel-communication` and `dingtalk-oa-approval`.",
        ),
        (
            "okr_review_or_scoring",
            "Load `dingtang-okr-review`; ordinary OKR discussion does not invoke that scoring workflow.",
        ),
        (
            "named_person_in_business_work",
            "Keep ownership, delivery, revenue, and project-risk facts as ordinary business facts unless the requested judgment is about the person's employment or personnel status.",
        ),
    ],
)
def test_personnel_skill_defines_composition_case(case: str, handling: str):
    assert f"- `{case}`: {handling}" in _skill_text()


def test_personnel_skill_separates_subject_recipient_and_authorized_audience():
    text = _skill_prose()

    for required in (
        "Identify the information subject",
        "Identify the intended recipient",
        "Determine the authorized audience from supplied context or live evidence",
        "Distinguish self-related information, internal personnel information, external candidate information, and ordinary business facts",
        "An explicit request is not required",
        "the agent decides whether the matter needs the principal's handling",
        "A person's name alone does not make a business fact personnel information",
        "Do not invent compensation, performance, promotion, employment, health, leave, or other sensitive facts",
    ):
        assert required in text


def test_personnel_skill_reuses_specialist_skills_without_copying_workflows():
    text = _skill_prose()

    assert "Load `stardust-interview` for candidate evaluation" in text
    assert "Load `dingtalk-oa-approval` for approval work" in text
    assert "Load `dingtang-okr-review` only for an actual OKR review or scoring task" in text
    assert "Do not reproduce or replace those specialist workflows here" in text
    for forbidden in (
        "dws oa approval",
        "queue_okr_review",
        "candidate_context_known",
        "personnel_subject_user_id",
        "re.compile",
        "sender_name ==",
    ):
        assert forbidden not in _skill_text()


def test_canonical_prompt_delegates_personnel_and_candidate_policy_to_skill():
    text = DEFAULT_PROMPT_PATH.read_text(encoding="utf-8")

    for removed in (
        "必须输出 user_response.sensitivity_kind",
        "internal_personnel 只用于具体个人的人事判断",
        "非 HR 单聊里如果对方询问第三方的人事敏感信息",
        "外部候选人问题必须输出 external_candidate",
        "回答外部候选人是否匹配、是否推进、是否降级评估前",
    ):
        assert removed not in text
    assert "涉及专业业务流程时" in text
    assert "agent_cli.read_skill" in text
    assert "没有列出的字段不要编造" in text
    assert "凭证" in text
    assert "只有明确需要 <var: principal> 处理时才回复" not in text

