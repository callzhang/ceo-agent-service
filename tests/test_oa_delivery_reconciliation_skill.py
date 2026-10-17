from pathlib import Path


SKILL = Path(__file__).resolve().parents[1] / "ci/shared-skills/dingtalk-oa-approval/SKILL.md"


def test_complete_negative_readback_fails_old_delivery_without_authorizing_replay():
    text = SKILL.read_text(encoding="utf-8")
    rule = next(line for line in text.splitlines() if "发送回执缺失时先打回 Consumer 核验" in line)

    assert "零个匹配必须作为未送达/验证失败" in rule
    assert "不能把它当作允许重发的凭证" in rule
    assert "重新经过 Audit，不复用或重发旧候选" in rule
