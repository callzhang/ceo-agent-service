import hashlib
import json
from pathlib import Path


GENERIC_SKILL = Path("ci/shared-skills/dingtalk-oa-approval/SKILL.md")
PEOPLE_SKILL = Path("ci/shared-skills/stardust-oa-people-review/SKILL.md")


def test_return_receipts_alone_do_not_establish_effective_rounds() -> None:
    text = GENERIC_SKILL.read_text(encoding="utf-8")

    assert "回执只证明退回动作发生" in text
    assert "不证明该轮有效" in text
    assert "逐轮核验表" in text
    assert "退回后每条回复、附件或审批意见的来源时间" in text
    assert "该回应是否实质补正了同一缺口" in text
    assert "该缺口的累计链即结束" in text
    assert "若任一轮的回应、时间线或材料无法核实，不得推定该轮有效" in text


def test_people_review_requires_round_by_round_cure_check_before_threshold() -> None:
    text = PEOPLE_SKILL.read_text(encoding="utf-8")

    assert "use the generic OA Skill's per-return verification table" in text
    assert "Do not use the count of DWS `REDIRECT_PROCESS` records or service receipts" in text
    assert "If a response cures the gap, that unresolved-gap chain ends" in text
    assert "A lack of a new attachment is not proof that no substantive response was made" in text


def test_release_manifest_hashes_match_both_review_skills() -> None:
    manifest = json.loads(
        Path("ci/consumer-system-contract-release.json").read_text(encoding="utf-8")
    )
    entries = {entry["source"]: entry for entry in manifest["files"]}

    for path in (GENERIC_SKILL, PEOPLE_SKILL):
        source = path.as_posix()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert entries[source]["new_sha256"] == digest
