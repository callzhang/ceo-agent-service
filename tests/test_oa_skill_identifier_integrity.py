from pathlib import Path


def test_oa_skill_requires_exact_source_identifiers_for_read_tools():
    root = Path(__file__).resolve().parents[1]
    skill = (root / "ci/shared-skills/dingtalk-oa-approval/SKILL.md").read_text(encoding="utf-8")

    assert "参数必须使用待办/详情对象中对应 ID 字段的原始字符串值" in skill
    assert "逐字符核对 `process_instance_id`、`task_id` 与来源字段相同" in skill
    assert "不把它改写成材料缺失或业务不合格" in skill
