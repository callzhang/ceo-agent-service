from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SKILL_PATH = ROOT / "skills" / "ceo-document-review" / "SKILL.md"
DEFAULT_PROMPT_PATH = ROOT / "app" / "defaults" / "developer_prompt.md"


def _skill_text() -> str:
    return SKILL_PATH.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("material", "operation_guidance", "read_rule"),
    [
        ("DingTalk document", "`dingtalk-doc`", "read current content"),
        ("DingTalk AI table", "`dingtalk-aitable`", "never use document read"),
        (
            "ordinary file",
            "`matching drive Skill`",
            "use the supplied exact command",
        ),
        ("image", "none for attached input", "inspect the image before conclusions"),
        ("Lark document", "`lark-doc`", "read current content"),
        ("Lark table", "`lark-base`", "read current table data"),
        ("Lark file", "`lark-drive`", "use the supplied exact command"),
    ],
)
def test_document_review_skill_defines_material_read_path(
    material: str,
    operation_guidance: str,
    read_rule: str,
):
    assert (
        f"| {material} | {operation_guidance} | {read_rule} |" in _skill_text()
    )


def test_document_review_skill_defines_complete_review_workflow():
    text = _skill_text()

    for required in (
        "Load the operation Skill that matches the actual material type",
        "The agent chooses and performs every content read",
        "The service exposes references and exact read commands but does not interpret business content",
        "Reread the current material when the sender says it changed",
        "Do not reuse a conclusion from an older version",
        "Review readable material directly",
        "Do not ask the sender to paste content that the agent can read",
        "explicit dependency failure",
        "Do not infer or invent the missing content",
        "Deliver comments in the material when the loaded operation Skill supports comments",
        "otherwise deliver the review in the source conversation",
    ):
        assert required in text


def test_document_review_skill_uses_executable_image_inspection_guidance():
    text = _skill_text()

    assert "`matching file or image Skill`" not in text
    assert "image content already present in the current Agent input" in text
    assert "exact supplied local material reference or operation" in text
    assert "image-generation" in text
    assert "image-reading Skill" in text


def test_canonical_prompt_delegates_document_review_judgment_to_skill():
    text = DEFAULT_PROMPT_PATH.read_text(encoding="utf-8")

    assert "如果新消息要求 comments、审核、定稿或确认" not in text
    assert "如果新消息明确表示前一次依据的材料已经被修改" not in text
    assert "处理文档时，如果是钉钉文档可以用评论功能" not in text
    assert "普通钉钉文件不同于钉钉在线文档" not in text
    assert "most specific applicable business Skill" in text
    assert "agent_cli.read_skill" in text
    assert "2. [output_contracts] Output Contracts:" in text


def test_document_review_skill_has_no_command_catalog_or_python_router():
    text = _skill_text()

    for forbidden in (
        "dws doc info",
        "dws doc read",
        "dws aitable",
        "lark-cli",
        "extension=adoc",
        "extension=able",
        "re.compile",
    ):
        assert forbidden not in text
