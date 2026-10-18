from pathlib import Path


SKILL = Path(__file__).resolve().parents[1] / "ci/shared-skills/dingtalk-oa-approval/SKILL.md"


def test_oa_pending_list_uses_verified_page_size_and_complete_pagination() -> None:
    text = SKILL.read_text(encoding="utf-8")

    assert "dws oa approval list-pending --page 1 --limit 20 --format json" in text
    assert "--create-time-to \"<YYYY-MM-DD>\" --page 1 --limit 20" in text
    assert "每页最多稳定接受 20 条" in text
    assert "hasMore=false" in text
    assert "中间页失败时不能把已读页称为完整待办列表" in text
