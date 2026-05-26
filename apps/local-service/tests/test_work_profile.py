from pathlib import Path

from ceo_agent_service.corpus import CorpusRecord, write_records
from ceo_agent_service.work_profile import (
    EvidenceRecord,
    WorkProfile,
    WorkProfileRule,
    collect_dingtalk_kb_evidence,
    collect_existing_corpus_evidence,
    collect_local_doc_evidence,
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


def test_collect_existing_corpus_evidence_reads_style_corpus(tmp_path: Path):
    csv_path = tmp_path / "corpus" / "derek_style_corpus.csv"
    write_records(
        csv_path,
        [
            CorpusRecord(
                source_type="dingtalk",
                source_title="客户合作群",
                timestamp="2026-05-26T10:00:00",
                context="客户问是否能今天给最终方案",
                derek_reply="先别承诺最终版，先把客户目标和交付边界收敛清楚。",
                message_id="msg-1",
                conversation_id="cid-1",
                speaker_name="Derek",
                metadata_json="{}",
            )
        ],
    )

    records = collect_existing_corpus_evidence(csv_path)

    assert len(records) == 1
    assert records[0].source_type == "dingtalk"
    assert records[0].evidence_strength == "behavior_high"
    assert "先别承诺最终版" in records[0].excerpt


def test_collect_local_doc_evidence_prefers_thinking_and_strategy_dirs(tmp_path: Path):
    workspace = tmp_path / "memory"
    thinking = workspace / "Thinking"
    strategy = workspace / "management" / "strategy"
    ignored = workspace / ".smart-env"
    thinking.mkdir(parents=True)
    strategy.mkdir(parents=True)
    ignored.mkdir(parents=True)
    (thinking / "CEO 如何使用agent提效.md").write_text("先把问题拆成目标、证据、下一步。", encoding="utf-8")
    (strategy / "Q2 strategy.md").write_text("战略判断先看客户价值和交付闭环。", encoding="utf-8")
    (ignored / "cache.md").write_text("不应该进入 profile。", encoding="utf-8")

    records = collect_local_doc_evidence(workspace)

    assert {record.title for record in records} == {
        "CEO 如何使用agent提效.md",
        "Q2 strategy.md",
    }
    assert all(record.evidence_strength == "authored_high" for record in records)


def test_collect_local_doc_evidence_skips_nested_ignored_dirs(tmp_path: Path):
    workspace = tmp_path / "memory"
    visible = workspace / "management"
    ignored = visible / ".smart-env"
    visible.mkdir(parents=True)
    ignored.mkdir(parents=True)
    (visible / "operating.md").write_text("先把项目节奏和责任边界说清楚。", encoding="utf-8")
    (ignored / "cache.md").write_text("不应该进入 profile。", encoding="utf-8")

    records = collect_local_doc_evidence(workspace)

    assert {record.title for record in records} == {"operating.md"}


def test_collect_local_doc_evidence_deduplicates_overlapping_management_roots(tmp_path: Path):
    workspace = tmp_path / "memory"
    strategy = workspace / "management" / "strategy"
    strategy.mkdir(parents=True)
    (strategy / "Q2 strategy.md").write_text("战略判断先看客户价值和交付闭环。", encoding="utf-8")

    records = collect_local_doc_evidence(workspace)

    assert [record.location for record in records] == ["management/strategy/Q2 strategy.md"]


def test_collect_local_doc_evidence_classifies_sensitive_local_docs(tmp_path: Path):
    workspace = tmp_path / "memory"
    personnel = workspace / "management" / "staff management"
    customer = workspace / "business"
    personnel.mkdir(parents=True)
    customer.mkdir(parents=True)
    (personnel / "绩效.md").write_text("员工绩效需要结合目标和过程反馈。", encoding="utf-8")
    (customer / "customer.md").write_text("客户合作先看商务价值和交付边界。", encoding="utf-8")

    records = collect_local_doc_evidence(workspace)

    sensitivities = {record.title: record.sensitivity for record in records}
    assert sensitivities == {
        "绩效.md": "internal_personnel",
        "customer.md": "customer",
    }


class FakeDwsForKnowledgeBase:
    def __init__(self):
        self.read_nodes = []

    def list_doc_nodes(self, workspace_id=None, folder_id=None, page_token=""):
        return {
            "result": {
                "nodes": [
                    {
                        "nodeId": "doc-1",
                        "name": "战略判断.md",
                        "nodeType": "file",
                        "contentType": "ALIDOC",
                        "extension": "adoc",
                    }
                ],
                "nextToken": None,
            }
        }

    def doc_info(self, node):
        return {"result": {"nodeId": node, "name": "战略判断.md", "creatorName": "Derek"}}

    def read_doc(self, node):
        self.read_nodes.append(node)
        return {"result": {"markdown": "判断客户合作先看目标、边界和交付闭环。"}}


def test_collect_dingtalk_kb_evidence_reads_online_docs_to_cache(tmp_path: Path):
    dws = FakeDwsForKnowledgeBase()

    records = collect_dingtalk_kb_evidence(
        dws=dws,
        cache_dir=tmp_path / "cache",
        workspace_id="space-1",
    )

    assert dws.read_nodes == ["doc-1"]
    assert len(records) == 1
    assert records[0].source_type == "dingtalk_kb_live"
    assert records[0].evidence_strength == "kb_live_doc"
    assert "客户合作" in records[0].excerpt
    assert (tmp_path / "cache" / "doc-1.md").exists()
