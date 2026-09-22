from __future__ import annotations

from pathlib import Path

from app.managed_skills import capture_runtime_skill_edits
from app.store import AutoReplyStore


def _runtime_skill(root: Path, name: str) -> None:
    path = root / name / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nname: {name}\ndescription: Runtime-only test Skill.\n---\n\n# {name}\n",
        encoding="utf-8",
    )


def test_capture_versions_both_oa_operation_skills_from_runtime_files(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "skills.sqlite3")
    root = tmp_path / "skills"
    _runtime_skill(root, "dingtalk-oa-approval")
    _runtime_skill(root, "stardust-oa-finance-review")

    captured = capture_runtime_skill_edits(store, skills_root=root)

    assert [item.name for item in captured] == [
        "dingtalk-oa-approval",
        "stardust-oa-finance-review",
    ]
    assert [item.revision_number for item in captured] == [1, 1]
    assert capture_runtime_skill_edits(store, skills_root=root) == ()
