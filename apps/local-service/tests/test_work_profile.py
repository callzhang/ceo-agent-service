from pathlib import Path

from ceo_agent_service.work_profile import (
    EvidenceRecord,
    WorkProfile,
    WorkProfileRule,
    evidence_id,
    safe_excerpt,
)


def test_evidence_id_is_stable_for_same_source():
    first = evidence_id("dingtalk", "message-1", "材料不足先追问")
    second = evidence_id("dingtalk", "message-1", "材料不足先追问")

    assert first == second
    assert first.startswith("ev_")


def test_safe_excerpt_collapses_whitespace_and_limits_length():
    excerpt = safe_excerpt("第一行\n\n第二行 " * 20, limit=30)

    assert "\n" not in excerpt
    assert len(excerpt) <= 31
    assert excerpt.endswith("…")


def test_work_profile_rule_requires_evidence_ids():
    rule = WorkProfileRule(
        id="rule_materials_before_decision",
        title="材料不足不拍板",
        category="decision",
        scenarios=["approval"],
        trigger="缺少正文、预算、责任人或附件",
        do="先追问缺失材料",
        dont="不要给批准或拒绝结论",
        confidence="high",
        evidence_ids=["ev_abc"],
    )

    assert rule.evidence_ids == ["ev_abc"]


def test_work_profile_serializes_rules():
    evidence = EvidenceRecord(
        id="ev_abc",
        source_type="dingtalk",
        title="审批沟通",
        timestamp="2026-05-26T10:00:00",
        location="cid-1/msg-1",
        scenario="approval",
        evidence_strength="behavior_high",
        sensitivity="approval",
        excerpt="材料不足，先补齐附件。",
        usable_for_profile=True,
    )
    profile = WorkProfile(
        title="Derek Work Profile",
        summary="工作判断 profile",
        rules=[
            WorkProfileRule(
                id="rule_materials_before_decision",
                title="材料不足不拍板",
                category="decision",
                scenarios=["approval"],
                trigger="缺少正文、预算、责任人或附件",
                do="先追问缺失材料",
                dont="不要给批准或拒绝结论",
                confidence="high",
                evidence_ids=[evidence.id],
            )
        ],
    )

    assert profile.model_dump()["rules"][0]["id"] == "rule_materials_before_decision"
