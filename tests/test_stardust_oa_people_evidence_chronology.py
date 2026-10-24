from pathlib import Path
import hashlib
import json


PEOPLE_SKILL = Path("ci/shared-skills/stardust-oa-people-review/SKILL.md")
GENERIC_SKILL = Path("ci/shared-skills/dingtalk-oa-approval/SKILL.md")


def test_org_change_uses_timestamped_evidence_and_counts_only_valid_same_gap_returns() -> None:
    text = PEOPLE_SKILL.read_text(encoding="utf-8")

    assert "source-timestamp order" in text
    assert "using DWS operation records" in text
    assert "Only evidence submitted after a verified return" in text
    assert "three such effective returns" in text
    assert "At Derek's authorized review node" in text
    assert "same substantive gap unresolved" in text
    assert "reviewer-side permission, parsing, OCR, or client read failure" in text
    assert "gap cured by post-return evidence does not count" in text
    assert "concrete, verifiable substantive statements in approval comments as evidence" in text


def test_verified_receiving_supervisor_comment_cannot_be_rejected_for_lacking_attachment() -> None:
    text = PEOPLE_SKILL.read_text(encoding="utf-8")

    assert "verified receiving supervisor" in text
    assert "post-return statement" in text
    normalized = text.lower()
    assert (
        "do not count that fact as unresolved or demand a separate attachment or confirmation record "
        "solely because the statement is in an oa comment"
    ) in normalized
    assert "if a concrete material fact remains unsupported or contradicted, ask only for that fact" in normalized


def test_generic_repeat_return_rule_rejects_only_after_three_valid_same_gap_returns() -> None:
    text = GENERIC_SKILL.read_text(encoding="utf-8")
    assert "三次有效退回" in text
    assert "第四次审核" in text
    assert "同一缺口未解决" in text
    assert "客户端读取失败" in text
    assert "责任审批方补正的缺口" in text
    assert "不得仅凭退回次数拒绝" in text
    assert "审批人的再次同意不是自动补正" in text
    assert "按次数自动拒绝" not in text


def test_oa_skill_release_digests_match_the_reviewed_sources() -> None:
    manifest = json.loads(
        Path("ci/consumer-system-contract-release.json").read_text(encoding="utf-8")
    )
    entries = {item["source"]: item for item in manifest["files"]}
    for source in (GENERIC_SKILL, PEOPLE_SKILL):
        entry = entries[f"ci/shared-skills/{source.parent.name}/SKILL.md"]
        assert entry["new_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
