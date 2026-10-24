import hashlib
import json
from pathlib import Path


SKILL = Path("ci/shared-skills/ceo-calendar-invite/SKILL.md")


def test_calendar_skill_reuses_complete_conversion_result_without_round_trip():
    text = SKILL.read_text(encoding="utf-8")

    assert "UTC instant" in text
    assert "目标区当地时间" in text
    assert "所有 fold" in text
    assert "复用这些结果" in text
    assert "不要为同一 instant 再反向换算" in text
    assert "同一原生 session 续会话时" in text
    assert "`local_time`、`source_timezone`、`target_timezone` 三项完全相同" in text
    assert "UTC instant 精确匹配此前结果中的某个 choice" in text
    assert "更新 fold 选择" in text
    assert "本轮消息和日历状态仍须重新读取" in text
    assert "反向换算核对同一时刻" not in text
    assert "已知时区不证明对方空闲" in text


def test_calendar_skill_release_manifest_tracks_current_digest():
    content = SKILL.read_bytes()
    manifest = json.loads(
        Path("ci/consumer-system-contract-release.json").read_text(encoding="utf-8")
    )
    entry = next(
        item for item in manifest["files"]
        if item["source"] == "ci/shared-skills/ceo-calendar-invite/SKILL.md"
    )

    assert entry["new_sha256"] == hashlib.sha256(content).hexdigest()
