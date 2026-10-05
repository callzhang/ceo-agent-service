"""Fixed Project-centered oracle over persisted domain rows, not Agent prose."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from app.project_context_service import ProjectContextService
from app.store import AutoReplyStore
from app.task_attention_projection import AttentionProposal, BusinessAttentionProjection
from app.task_business_resolution import BusinessResolutionService
from app.task_semantic_models import AttentionCategory, ProjectContext, TaskSuggestion
from app.task_semantic_service import RecordTaskSuggestion, SourceSignal, TaskSemanticService
from app.task_models import TaskAgentDecision, WorkItem


def _tool():
    path = Path(__file__).parents[1] / "scripts/replay_task_attention.py"
    spec = importlib.util.spec_from_file_location("project_centered_evaluation", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def domain_snapshot(store: AutoReplyStore) -> dict[str, tuple[str, ...]]:
    return _tool().domain_snapshot(store)


def evaluate_persisted_case(
    store: AutoReplyStore, expected: dict[str, object], *,
    before: dict[str, tuple[str, ...]] | None = None,
) -> tuple[str, ...]:
    return _tool().read_project_centered_expectations(store, before, expected)


@pytest.fixture
def seeded_domain(tmp_path):
    def seed(*, owner: str = "张三", second_project: bool = True,
             attention: bool = True, suggestion: bool = True):
        store = AutoReplyStore(tmp_path / "project-centered.sqlite3")
        resolver = BusinessResolutionService(store)
        anchor = resolver.register_anchor(
            anchor_type="project", anchor_ref="eval:project:a", title="甲客户一期交付",
        )
        project_id = resolver.register_official_project(
            anchor_id=anchor, registry_source="meeting:project-start",
        )
        if second_project:
            second_anchor = resolver.register_anchor(
                anchor_type="project", anchor_ref="eval:project:b", title="乙客户采购协同",
            )
            resolver.register_official_project(
                anchor_id=second_anchor, registry_source="meeting:project-start",
            )
        duty_text = "甲客户一期交付由张三总负责交付验收，王五负责商务回款。"
        duty_id = store.create_business_task_signal(
            source_type="meeting", source_ref="meeting:project-start",
            evidence_text=duty_text, dedupe_key="eval:duty",
        )
        risk_text = "甲客户一期交付付款日期未确定，影响本期现金安排。"
        risk = SourceSignal(
            source_type="message", source_ref="chat:cash-risk",
            evidence_text=risk_text, dedupe_key="eval:risk",
        )
        risk_id = store.create_business_task_signal(**risk.__dict__)
        duty_citation = {"signal_id": duty_id, "source_ref": "meeting:project-start",
                         "source_excerpt": duty_text}
        risk_citation = {"signal_id": risk_id, "source_ref": "chat:cash-risk",
                         "source_excerpt": risk_text}
        context = ProjectContext(
            goal="完成一期交付并回款", scope="一期交付",
            overall_owner={"person_name": owner, "responsibility": "交付验收",
                           "evidence": [duty_citation]},
            responsibilities=[{"person_name": "王五", "responsibility": "商务回款",
                               "evidence": [duty_citation]}],
            facts=[{"key": "cash-risk", "text": "付款日期未确定，影响本期现金安排",
                    "evidence": [risk_citation]}],
        )
        with store.business_task_transaction() as db:
            ProjectContextService(store).apply(
                project_id=project_id, context=context,
                signal_ids=(duty_id, risk_id), db=db,
            )
        if attention:
            BusinessAttentionProjection(store).upsert(AttentionProposal(
                stable_key=f"project:{anchor}", category=AttentionCategory.WATCH,
                title="甲客户一期交付回款关注", business_area="交付回款",
                why_attention="付款时间影响现金安排", current_state="付款日期未确定",
                ceo_action="观察付款排期", anchor_id=anchor, task_ids=(),
                evidence_signal_id=risk_id,
                assessment_json=json.dumps({"evidence": [{**risk_citation,
                    "source_time": "", "source_link": ""}]}, ensure_ascii=False),
            ))
        task_id = None
        if suggestion:
            suggestion_value = TaskSuggestion(
                reason="按商务职责确认排期", suggested_owner_name="王五",
                responsibility_evidence=[duty_citation], basis_evidence=[risk_citation],
            )
            task_id = TaskSemanticService(store).record_suggestion(RecordTaskSuggestion(
                title="确认付款排期", description="核实客户付款时间",
                signal=risk, suggestion=suggestion_value, project_anchor_id=anchor,
            )).task_id
        return store, task_id
    return seed


EXPECTED = {
    "project_titles": ["甲客户一期交付", "乙客户采购协同"],
    "project_contexts": {"甲客户一期交付": {
        "overall_owner": "张三",
        "responsibilities": [{"person_name": "王五", "responsibility": "商务回款"}],
        "fact_text_contains": ["付款日期未确定"],
    }},
    "attention_projects": ["甲客户一期交付"],
    "zero_task_attention_projects": ["甲客户一期交付"],
    "task_count": 1,
    "task_expectations": [{
        "title_contains": "确认付款排期", "project_title": "甲客户一期交付",
        "origin": "agent_suggestion", "stage": "candidate", "owner_name": "",
        "commitment_status": "none", "suggested_owner_name": "王五",
    }],
    "minimum_evidence_sources": 1,
    "required_source_refs": [],
    "required_project_source_refs": ["meeting:project-start", "chat:cash-risk"],
    "max_outbound_intent_delta": 0,
}


@pytest.mark.parametrize(
    "fault,required_failure", [
        ("wrong_owner", "project_owner_mismatch"),
        ("suggestion_formal", "task_expectation_mismatch"),
        ("missing_second_project", "project_titles_mismatch"),
        ("zero_task_card_unsaved", "attention_projects_mismatch"),
        ("duplicate_suggestion", "task_count_mismatch"),
        ("outbound_intent", "outbound_intent_increase"),
    ],
)
def test_oracle_rejects_wrong_persisted_result(seeded_domain, fault, required_failure):
    options = {
        "owner": "赵六" if fault == "wrong_owner" else "张三",
        "second_project": fault != "missing_second_project",
        "attention": fault != "zero_task_card_unsaved",
    }
    store, task_id = seeded_domain(**options)
    before = domain_snapshot(store)
    if fault == "suggestion_formal":
        with store._connect() as db:
            db.execute("update business_tasks set stage='formal', formal_basis='meeting_action_item', "
                       "commitment_status='assigned_unaccepted', owner_name='王五' where id=?", (task_id,))
    if fault == "duplicate_suggestion":
        with store._connect() as db:
            db.execute("insert into business_tasks (title, description, stage, origin, suggestion_json) "
                       "select title, description, stage, origin, suggestion_json from business_tasks where id=?",
                       (task_id,))
    if fault == "outbound_intent":
        with store._connect() as db:
            db.execute("insert into business_task_todo_sync_outbox "
                       "(operation_key,business_task_id,operation) values (?,?,?)",
                       ("eval:unexpected-send", task_id, "create"))
    failures = evaluate_persisted_case(store, EXPECTED, before=before)
    assert required_failure in failures, failures


def test_oracle_accepts_correct_persisted_result(seeded_domain):
    store, _ = seeded_domain()
    assert evaluate_persisted_case(store, EXPECTED, before=domain_snapshot(store)) == ()


def test_task_title_oracle_accepts_reviewed_synonym_alternatives(seeded_domain):
    store, task_id = seeded_domain()
    with store._connect() as db:
        db.execute(
            "update business_tasks set title=? where id=?",
            ("确认甲客户一期交付付款安排及现金影响", task_id),
        )
    before = domain_snapshot(store)
    expected = json.loads(json.dumps(EXPECTED, ensure_ascii=False))
    expected["task_expectations"][0].pop("title_contains")
    expected["task_expectations"][0]["title_contains_any"] = ["付款时间", "付款安排"]

    failures = evaluate_persisted_case(store, expected, before=before)

    assert "task_expectation_mismatch" not in failures


def test_assessment_oracle_accepts_one_of_reviewed_source_citations():
    match = _tool()._assessment_evidence_matches
    expected = [
        {"source_ref": "minutes:1", "source_excerpt": "两个独立交付物"},
        {"source_ref": "minutes:1", "source_excerpt": "行动一：李四负责准备验收材料"},
    ]
    actual = [{
        "source_ref": "minutes:1",
        "source_excerpt": "会议确认这是两个独立交付物。",
    }]

    assert match(actual, required=[], alternatives=expected)
    assert not match(actual, required=[], alternatives=[{
        "source_ref": "minutes:1", "source_excerpt": "行动二：王五负责商务对账",
    }])


def test_oracle_rejects_two_official_projects_with_same_title(seeded_domain):
    store, _ = seeded_domain()
    with store._connect() as db:
        projects = {
            row["title"]: row["id"]
            for row in db.execute("select id,title from business_projects")
        }
        original_id = projects["甲客户一期交付"]
        duplicate_id = projects["乙客户采购协同"]
        db.execute(
            "insert into business_project_context_revisions "
            "(project_id,context_json,evidence_json) "
            "select ?,context_json,evidence_json from business_project_context_revisions "
            "where project_id=? order by id desc limit 1",
            (duplicate_id, original_id),
        )
        db.execute(
            "insert into business_project_evidence (project_id,signal_id) "
            "select ?,signal_id from business_project_evidence where project_id=?",
            (duplicate_id, original_id),
        )
        db.execute(
            "update business_projects set title=? where id=?",
            ("甲客户一期交付", duplicate_id),
        )
    expected = EXPECTED | {"project_titles": ["甲客户一期交付"]}
    failures = evaluate_persisted_case(store, expected, before=domain_snapshot(store))
    assert "project_titles_mismatch" in failures, failures
    assert "project_identity_ambiguous" in failures, failures


def test_readback_rejects_one_judgment_for_two_relevant_projects(seeded_domain):
    store, _ = seeded_domain(attention=False, suggestion=False)
    with store._connect() as db:
        projects = {
            row["title"]: row["canonical_anchor_id"]
            for row in db.execute("select title,canonical_anchor_id from business_projects")
        }
    assert set(projects) == {"甲客户一期交付", "乙客户采购协同"}
    current = WorkItem.model_validate({
        "source": {"type": "reply_attempt", "ref": "eval:both:chat",
                   "created_at": "2026-10-02T09:00:00Z"},
        "context": {"source_conversation_kind": "group"},
        "summary": "甲客户一期交付按计划推进；乙客户采购协同按计划推进。",
    })
    input_id = store.enqueue_work_summary_input(
        current.source.type.value, current.source.ref, current.model_dump_json()
    )
    citation = {
        "signal_id": None, "source_ref": "eval:both:chat",
        "source_excerpt": "甲客户一期交付按计划推进",
        "source_time": "2026-10-02T09:00:00Z", "source_link": "",
    }
    run_id = store.record_task_agent_run(input_id, decision_json=json.dumps({
        "project_decisions": [], "task_decisions": [],
        "project_assessments": [{
            "project_title": "甲客户一期交付", "outcome": "not_needed",
            "reason": "按计划推进", "evidence": [citation],
            "anchor_id": projects["甲客户一期交付"], "attention_proposal": None,
        }],
    }, ensure_ascii=False))
    store.record_task_agent_projection(run_id, json.dumps({
        "status": "no_proposal", "outcomes": [], "recompute_error": "",
        "source_type": "reply_attempt", "task_decision_count": 0,
        "project_link_count": 0, "proposal_count": 0,
        "project_assessments": [{
            "assessment_index": 0, "status": "recorded", "task_ids": [],
            "anchor_id": projects["甲客户一期交付"], "attention_id": None,
            "evidence": [citation],
        }],
    }, ensure_ascii=False))
    expected = {
        "project_titles": sorted(projects), "attention_projects": [],
        "task_count": 0, "minimum_evidence_sources": 1,
        "project_assessments": [{
            "project_title": title, "outcome": "not_needed",
            "evidence": [{"source_ref": "eval:both:chat", "source_excerpt": title}],
            "application_status": "recorded", "task_count": 0,
            "attention_required": False,
        } for title in sorted(projects)],
    }
    result = _tool().readback(
        store, input_id=input_id, before=_tool().read_domain(store), expected=expected
    )
    assert "project_assessment_coverage_mismatch" in result["failures"], result


def test_readback_accepts_only_allowed_assessment_task_counts(seeded_domain):
    store, task_id = seeded_domain(attention=False)
    with store._connect() as db:
        cursor = db.execute(
            "insert into business_tasks (title, description, stage, origin, suggestion_json) "
            "select '独立商务对账', description, stage, origin, suggestion_json "
            "from business_tasks where id=?", (task_id,)
        )
        peer_id = cursor.lastrowid
        db.execute(
            "insert into business_task_anchor_links "
            "(task_id,anchor_id,status,active,evidence_signal_id,reason) "
            "select ?,anchor_id,status,active,evidence_signal_id,reason "
            "from business_task_anchor_links where task_id=?", (peer_id, task_id)
        )
        anchor_id = db.execute(
            "select canonical_anchor_id from business_projects where title=?",
            ("甲客户一期交付",),
        ).fetchone()[0]
    current = WorkItem.model_validate({
        "source": {"type": "reply_attempt", "ref": "eval:optional-members",
                   "created_at": "2026-10-02T09:00:00Z"},
        "context": {"source_conversation_kind": "group"},
        "summary": "甲客户一期交付款项已到账，当前无需处理。",
    })
    input_id = store.enqueue_work_summary_input(
        current.source.type.value, current.source.ref, current.model_dump_json()
    )
    citation = {
        "signal_id": None, "source_ref": current.source.ref,
        "source_excerpt": "款项已到账", "source_time": current.source.created_at,
        "source_link": "",
    }
    expected = {
        "project_titles": ["甲客户一期交付", "乙客户采购协同"],
        "attention_projects": [], "task_count": 2, "minimum_evidence_sources": 1,
        "project_assessments": [{
            "project_title": "甲客户一期交付", "outcome": "not_needed",
            "evidence": [{"source_ref": current.source.ref, "source_excerpt": "款项已到账"}],
            "application_status": "recorded", "allowed_task_counts": [0, 1],
            "attention_required": False,
        }],
    }

    def readback_for(task_ids):
        run_id = store.record_task_agent_run(input_id, decision_json=json.dumps({
            "project_decisions": [], "task_decisions": [],
            "project_assessments": [{
                "project_title": "甲客户一期交付", "outcome": "not_needed",
                "reason": "款项已到账", "evidence": [citation],
                "anchor_id": anchor_id, "task_ids": task_ids,
                "attention_proposal": None,
            }],
        }, ensure_ascii=False))
        store.record_task_agent_projection(run_id, json.dumps({
            "status": "no_proposal", "outcomes": [], "recompute_error": "",
            "source_type": "reply_attempt", "task_decision_count": 0,
            "project_link_count": 0, "proposal_count": 0,
            "project_assessments": [{
                "assessment_index": 0, "status": "recorded", "task_ids": task_ids,
                "anchor_id": anchor_id, "attention_id": None,
                "evidence": [citation],
            }],
        }, ensure_ascii=False))
        return _tool().readback(
            store, input_id=input_id, before=_tool().read_domain(store),
            expected=expected,
        )

    assert "project_assessment_receipt_mismatch" not in readback_for([task_id])["failures"]
    assert "project_assessment_receipt_mismatch" in readback_for(
        [task_id, peer_id]
    )["failures"]


@pytest.mark.parametrize(
    "application_status,accepted",
    [("applied", True), ("existing", True), ("rejected", False), ("error", False)],
)
def test_readback_accepts_only_applied_or_existing_card_receipts(
    seeded_domain, application_status, accepted,
):
    store, _ = seeded_domain(attention=True, suggestion=False)
    with store._connect() as db:
        card = db.execute("select id,anchor_id from business_attention_items").fetchone()
        signal = db.execute(
            "select id from business_task_signals where source_ref=?", ("chat:cash-risk",)
        ).fetchone()
    current = WorkItem.model_validate({
        "source": {"type": "reply_attempt", "ref": "eval:card-still-open",
                   "created_at": "2026-10-02T09:00:00Z"},
        "context": {"source_conversation_kind": "group"},
        "summary": "甲客户一期交付付款日期仍未确定，影响本期现金安排。",
    })
    input_id = store.enqueue_work_summary_input(
        current.source.type.value, current.source.ref, current.model_dump_json()
    )
    current_citation = {
        "signal_id": None, "source_ref": current.source.ref,
        "source_excerpt": "付款日期仍未确定，影响本期现金安排",
        "source_time": current.source.created_at, "source_link": "",
    }
    original_citation = {
        "signal_id": signal["id"], "source_ref": "chat:cash-risk",
        "source_excerpt": "付款日期未确定，影响本期现金安排",
        "source_time": "", "source_link": "",
    }
    proposed = application_status == "applied"
    wire_evidence = [
        {key: citation[key] for key in ("signal_id", "source_ref", "source_excerpt")}
        for citation in (current_citation, original_citation)
    ]
    attention_proposal = {
        "assessment_basis": "historical_comparison", "category": "watch",
        "title": "甲客户一期交付回款关注", "why_attention": "付款日期仍不确定，影响现金安排",
        "current_state": "付款日期仍未确定", "ceo_action": "关注付款排期",
        "material_trigger": "material_change", "evidence": wire_evidence,
    }
    decision = TaskAgentDecision.model_validate({
        "project_decisions": [], "task_decisions": [],
        "project_assessments": [{
            "project_title": "甲客户一期交付", "outcome": "needs_attention",
            "reason": "付款时间仍不确定", "anchor_id": card["anchor_id"],
            "assessment_basis": "historical_comparison", "evidence": wire_evidence,
            "existing_attention_id": None if proposed else card["id"],
            "attention_proposal": attention_proposal if proposed else None,
        }],
        "update_summary": "付款风险仍存在",
    })
    run_id = store.record_task_agent_run(
        input_id, decision_json=decision.model_dump_json()
    )
    outcomes = ([{
        "assessment_index": 0, "anchor_id": card["anchor_id"],
        "attention_id": card["id"], "task_id": None, "status": "applied",
    }] if proposed else [])
    store.record_task_agent_projection(run_id, json.dumps({
        "status": "completed" if proposed else "no_proposal",
        "outcomes": outcomes, "recompute_error": "",
        "source_type": "reply_attempt", "task_decision_count": 0,
        "project_link_count": 0, "proposal_count": int(proposed),
        "project_assessments": [{
            "assessment_index": 0, "status": application_status,
            "task_ids": [], "anchor_id": card["anchor_id"],
            "attention_id": card["id"],
            "evidence": [current_citation, original_citation],
        }],
    }, ensure_ascii=False))
    expected = {
        "project_titles": ["甲客户一期交付", "乙客户采购协同"],
        "attention_projects": ["甲客户一期交付"],
        "task_count": 0, "minimum_evidence_sources": 1,
        "project_assessments": [{
            "project_title": "甲客户一期交付", "outcome": "needs_attention",
            "evidence": [{"source_ref": "chat:cash-risk",
                          "source_excerpt": "付款日期未确定"}],
            "allowed_application_statuses": ["applied", "existing"],
            "task_count": 0, "attention_required": True,
        }],
    }
    result = _tool().readback(
        store, input_id=input_id, before=_tool().read_domain(store), expected=expected
    )
    assert ("project_assessment_receipt_mismatch" not in result["failures"]) is accepted, result
    assert result["passed"] is accepted, result


def test_oracle_rejects_unrelated_peer_added_to_project_card(seeded_domain):
    store, task_id = seeded_domain()
    with store._connect() as db:
        card_id = db.execute("select id from business_attention_items").fetchone()[0]
        db.execute("insert into business_attention_tasks (attention_item_id,task_id) values (?,?)",
                   (card_id, task_id))
    expected = EXPECTED | {"attention_member_counts": {"甲客户一期交付": 0}}
    assert "attention_member_count_mismatch" in evaluate_persisted_case(
        store, expected, before=domain_snapshot(store)
    )


def test_oracle_rejects_wrong_project_peer_with_same_member_count(seeded_domain):
    store, task_id = seeded_domain()
    with store._connect() as db:
        cursor = db.execute(
            "insert into business_tasks (title, description, stage, origin, suggestion_json) "
            "select '完成独立商务对账', description, stage, origin, suggestion_json "
            "from business_tasks where id=?", (task_id,)
        )
        peer_id = cursor.lastrowid
        db.execute(
            "insert into business_task_anchor_links "
            "(task_id,anchor_id,status,active,evidence_signal_id,reason) "
            "select ?,anchor_id,status,active,evidence_signal_id,reason "
            "from business_task_anchor_links where task_id=?", (peer_id, task_id)
        )
        card_id = db.execute("select id from business_attention_items").fetchone()[0]
        db.execute(
            "insert into business_attention_tasks (attention_item_id,task_id) values (?,?)",
            (card_id, peer_id),
        )
    expected = EXPECTED | {
        "task_count": 2,
        "attention_member_counts": {"甲客户一期交付": 1},
        "attention_member_task_title_contains": {"甲客户一期交付": ["付款排期"]},
    }
    expected.pop("zero_task_attention_projects")
    assert "attention_member_task_mismatch" in evaluate_persisted_case(
        store, expected, before=domain_snapshot(store)
    )


def test_oracle_counts_distinct_signal_versions_for_same_ref(seeded_domain):
    store, _ = seeded_domain()
    expected = EXPECTED | {"minimum_signal_versions_for_ref": {"chat:cash-risk": 2}}
    store.create_business_task_signal(
        source_type="message", source_ref="chat:cash-risk",
        evidence_text="甲客户一期交付付款日期未确定，影响本期现金安排。",
        dedupe_key="eval:risk:second-signal-same-document",
    )
    with store._connect() as db:
        signals = db.execute(
            "select source_document_id from business_task_signals where source_ref=?",
            ("chat:cash-risk",),
        ).fetchall()
    assert len(signals) == 2
    assert len({signal[0] for signal in signals}) == 1
    assert "signal_version_count_mismatch" in evaluate_persisted_case(
        store, expected, before=domain_snapshot(store)
    )
    store.create_business_task_signal(
        source_type="message", source_ref="chat:cash-risk",
        source_time="2026-10-03T09:00:00Z",
        evidence_text="甲客户一期交付付款日期仍未确定。", dedupe_key="eval:risk:version2",
    )
    assert "signal_version_count_mismatch" not in evaluate_persisted_case(
        store, expected, before=domain_snapshot(store)
    )


def test_repeat_oracle_includes_new_source_signal_in_domain_snapshot(seeded_domain):
    store, _ = seeded_domain()
    before = domain_snapshot(store)
    store.create_business_task_signal(
        source_type="message", source_ref="chat:unexpected-new-version",
        evidence_text="一次重复处理却新增了 Signal。", dedupe_key="eval:unexpected-signal",
    )
    expected = EXPECTED | {"repeat_domain_unchanged": True}
    assert "repeat_domain_changed" in evaluate_persisted_case(store, expected, before=before)


def test_fixed_cases_are_raw_sources_with_offline_only_expectations():
    fixture = json.loads((Path(__file__).parent / "fixtures/task_project_centered_v4.json").read_text())
    evidence_contains = _tool().evidence_contains
    assert fixture["version"] == 4
    cases = fixture["cases"]
    by_id = {case["case_id"]: case for case in cases}
    assert len(cases) == len(by_id) == 19
    assert {
        "project-risk-without-task", "role-based-unnamed-suggestion",
        "role-based-settled-negative", "same-project-three-sources",
        "single-report-two-projects", "meeting-chat-no-report", "routine-progress",
        "ambiguous-risk", "unconfirmed-project", "independent-explicit-task",
        "unknown-overall-owner", "responsibility-change-conflict",
        "suggestion-promoted-same-id", "existing-task-update",
        "distinct-deliverables", "repeated-same-source", "same-ref-new-version",
        "peer-not-auto-member", "completed-task-risk-persists",
    } == set(by_id)
    for case in cases:
        assert case["source_inputs"] and case["work_item"] == case["source_inputs"][-1]
        assert case["existing_context"] == {"projects": [], "tasks": [], "attention": []}
        assert "project_assessments" in case["expected"], case["case_id"]
        expected_assessments = case["expected"]["project_assessments"]
        if case["case_id"] == "independent-explicit-task":
            assert expected_assessments == []
        elif case["case_id"] == "unconfirmed-project":
            assert [item["project_title"] for item in expected_assessments] == ["北区优化"]
        else:
            assert sorted(item["project_title"] for item in expected_assessments) == sorted(
                case["expected"]["project_titles"]
            )
            for assessment in expected_assessments:
                assert assessment["evidence"] or assessment.get("evidence_any")
            assert ("task_count" in assessment) != ("allowed_task_counts" in assessment)
            for citation in assessment["evidence"]:
                assert any(
                    source["source"]["ref"] == citation["source_ref"]
                    and evidence_contains(source["summary"], citation["source_excerpt"])
                    for source in case["source_inputs"]
                ), (case["case_id"], citation)
        assert all("expected" not in source for source in case["source_inputs"])
        assert [source["source"]["created_at"] for source in case["source_inputs"]] == sorted(
            source["source"]["created_at"] for source in case["source_inputs"]
        )
        for source in case["source_inputs"]:
            WorkItem.model_validate(source)
    assert (by_id["role-based-unnamed-suggestion"]["source_inputs"][0]["summary"]
            == by_id["role-based-settled-negative"]["source_inputs"][0]["summary"])
    assert by_id["repeated-same-source"]["source_inputs"][0] == by_id["repeated-same-source"]["source_inputs"][1]
    versions = by_id["same-ref-new-version"]["source_inputs"]
    assert versions[0]["source"]["ref"] == versions[1]["source"]["ref"]
    assert versions[0]["source"]["created_at"] != versions[1]["source"]["created_at"]
    assert versions[0]["summary"] != versions[1]["summary"]
