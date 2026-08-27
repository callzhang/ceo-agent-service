import hashlib
import json
from pathlib import Path

import app.skill_feedback as skill_feedback
import app.agent_skill_usage as agent_skill_usage


def test_apply_skill_feedback_update_is_idempotent_and_returns_new_receipt(
    tmp_path: Path, monkeypatch
):
    skill_dir = tmp_path / "dingtalk-calendar"
    skill_dir.mkdir()
    skill_path = skill_dir / "SKILL.md"
    skill_path.write_text("# Calendar\n", encoding="utf-8")
    monkeypatch.setattr(agent_skill_usage, "AGENT_SKILL_ROOTS", (tmp_path,))
    events = [
        {
            "type": "item.completed",
            "item": {
                "type": "mcp_tool_call",
                "tool": "read_skill",
                "status": "completed",
                "metadata": {"skill_path": str(skill_path)},
            },
        }
    ]
    # A real read_skill receipt includes the path and name; the updater only
    # needs the reviewed path and revalidates it against the authorized root.
    events[0]["item"]["metadata"].update({"skill_name": "dingtalk-calendar"})

    receipts = skill_feedback.apply_skill_feedback_update(
        events_json=json.dumps(events),
        feedback="出差期间只处理本次日程，不改变后续重复系列",
        source_attempt_id=7211,
    )
    first_content = skill_path.read_text(encoding="utf-8")
    receipts_again = skill_feedback.apply_skill_feedback_update(
        events_json=json.dumps(events),
        feedback="出差期间只处理本次日程，不改变后续重复系列",
        source_attempt_id=7211,
    )

    assert len(receipts) == 1
    assert receipts == receipts_again
    assert first_content.count("Attempt #7211") == 1
    assert receipts[0].sha256 == hashlib.sha256(first_content.encode()).hexdigest()
