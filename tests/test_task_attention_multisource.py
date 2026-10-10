"""Contract fixtures shared by the staged multisource attention implementation."""

import json
import importlib.util
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.task_models import TaskAgentDecision, WorkItem, WorkItemSourceType
from app.task_semantic_models import ProjectContext
from app.task_agent import apply_task_agent_decision
from app.store import AutoReplyStore
from app.task_agent import process_work_item
from app.task_attention_projection import BusinessAttentionProjection
from tests.support.task_native import task_native_records  # noqa: F401
from tests.support.task_native import read_task_fixture_decision, rewrite_task_fixture_decision


def evaluation_tool():
    path = Path(__file__).parents[1] / "scripts/replay_task_attention.py"
    assert path.exists(), "single-input native evaluation tool is missing"
    spec = importlib.util.spec_from_file_location("attention_evaluation", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_fixed_evaluation_cases_have_independent_inputs_and_expectations():
    tool = evaluation_tool()
    cases = tool.load_cases(
        Path(__file__).parent / "fixtures/task_attention_multisource.json"
    )
    assert [case["case_id"] for case in cases] == [
        "w39-project-risk",
        "meeting-new-risk",
        "chat-with-report-context",
        "newer-conflicting-chat",
        "risk-label-only",
        "routine-progress",
        "unconfirmed-project",
        "no-real-task",
        "same-project-two-actions",
    ]
    for case in cases:
        assert set(case) == {"case_id", "work_item", "existing_context", "expected"}
        WorkItem.model_validate(case["work_item"])
        assert "expected" not in case["work_item"]
        assert "expected" not in case["existing_context"]
        required_refs = case["expected"]["required_source_refs"]
        if case["expected"]["attention_projects"]:
            assert case["work_item"]["source"]["ref"] in required_refs
        else:
            assert required_refs == []
        if case["case_id"] in ("chat-with-report-context", "newer-conflicting-chat"):
            assert "eval:historical-report" in required_refs
        assert ("allowed_task_counts" in case["expected"]) is (
            case["case_id"] in ("w39-project-risk", "meeting-new-risk")
        )


def test_assessment_cases_are_versioned_source_facts_with_post_run_expectations(
    tmp_path,
):
    from app.task_agent import build_task_agent_prompt

    tool = evaluation_tool()
    cases = tool.load_cases(
        Path(__file__).parent / "fixtures/task_attention_project_assessments_v1.json"
    )
    assert [case["case_id"] for case in cases] == [
        "assessment-report-needs-attention",
        "assessment-meeting-needs-attention",
        "assessment-chat-needs-attention",
        "assessment-vague-risk-not-needed",
        "assessment-no-task-insufficient",
        "assessment-unconfirmed-project",
        "assessment-routine-not-needed",
        "assessment-two-tasks-one-project",
        "assessment-existing-card-idempotent",
    ]
    for case in cases:
        assert set(case) == {"case_id", "work_item", "existing_context", "expected"}
        item = WorkItem.model_validate(case["work_item"])
        case["expected"]["secret_label"] = "evaluation_only_secret_expectation"
        store = AutoReplyStore(tmp_path / f"{case['case_id']}.sqlite3")
        input_id = tool.seed_case(store, case)
        stored = store.get_work_summary_input(input_id)
        assert "evaluation_only_secret_expectation" not in stored.payload_json
        assert "evaluation_only_secret_expectation" not in build_task_agent_prompt(
            item, ""
        )
        if case["case_id"] == "assessment-existing-card-idempotent":
            (card,) = store.list_business_attention_items()
            (evidence,) = json.loads(card.assessment_json)["evidence"]
            signal = store.get_business_task_signal(evidence["signal_id"])
            assert signal is not None
            assert evidence["source_ref"] == signal.source_ref == item.source.ref
            assert tool.evidence_contains(
                signal.evidence_text, evidence["source_excerpt"]
            )


@pytest.mark.parametrize(
    "fixture,case_id",
    [
        (
            "task_attention_project_assessments_v1.json",
            "assessment-existing-card-idempotent",
        ),
        (
            "task_attention_card_members_v1.json",
            "assessment-existing-card-project-peer",
        ),
    ],
)
def test_existing_card_assessment_replay_is_idempotent_with_actual_ids(
    tmp_path, fixture, case_id
):
    from app.task_agent import TaskAgentRunner

    tool = evaluation_tool()
    cases = tool.load_cases(Path(__file__).parent / "fixtures" / fixture)
    case = next(item for item in cases if item["case_id"] == case_id)
    store = AutoReplyStore(tmp_path / "existing-card-idempotent.sqlite3")
    input_id = tool.seed_case(store, case)
    (card,) = store.list_business_attention_items()
    (member,) = store.list_business_attention_tasks(card.id)
    (proof,) = json.loads(card.assessment_json)["evidence"]

    def domain_snapshot():
        tables = (
            "business_tasks",
            "business_projects",
            "business_anchors",
            "business_source_documents",
            "business_project_context_revisions",
            "business_project_evidence",
            "business_attention_items",
            "business_attention_tasks",
            "business_attention_events",
            "business_task_events",
            "business_task_signals",
            "business_task_evidence",
        )
        with store._connect() as db:
            return {
                table: [
                    dict(row)
                    for row in db.execute(f"select * from {table} order by rowid")
                ]
                for table in tables
            }

    initial_domain = domain_snapshot()

    class Codex:
        def decide(self, **kwargs):
            assert "evaluation_only_secret_expectation" not in kwargs["prompt"]
            return TaskAgentDecision.model_validate(
                {
                    "project_decisions": [],
                    "project_assessments": [
                        {
                            "project_title": "星海交付",
                            "outcome": "needs_attention",
                            "reason": "当前来源重申客户拒绝验收并延后回款，与已保存关注事实相同。",
                            "assessment_basis": "historical_comparison",
                            "evidence": [
                                {
                                    "source_ref": proof["source_ref"],
                                    "source_excerpt": proof["source_excerpt"],
                                },
                                {
                                    "signal_id": proof["signal_id"],
                                    "source_ref": proof["source_ref"],
                                    "source_excerpt": proof["source_excerpt"],
                                },
                            ],
                            "anchor_id": card.anchor_id,
                            "existing_attention_id": card.id,
                            "task_ids": [member.task_id],
                        }
                    ],
                    "task_decisions": [],
                }
            )

    runner = tool.scoped_runner(TaskAgentRunner, Codex())
    first = tool.replay_input(
        store,
        runner,
        input_id,
        source_ref=case["work_item"]["source"]["ref"],
        expected=case["expected"],
    )
    after_first_domain = domain_snapshot()
    replay = tool.replay_input(
        store,
        runner,
        input_id,
        source_ref=case["work_item"]["source"]["ref"],
        expected=case["expected"],
    )
    assert first["passed"], {
        "failures": first["failures"],
        "projection": first.get("projection"),
        "run_status": first.get("run_status"),
        "execution_error": first.get("execution_error"),
    }
    assert replay["passed"], {
        "failures": replay["failures"],
        "projection": replay.get("projection"),
        "run_status": replay.get("run_status"),
        "execution_error": replay.get("execution_error"),
    }
    assert first["projection"]["project_assessments"][0]["status"] == "existing"
    assert replay["cards"][0]["id"] == first["cards"][0]["id"] == card.id
    assert replay["changes"]["tasks"]["created_ids"] == []
    assert replay["changes"]["projects"]["created_ids"] == []
    assert replay["changes"]["attention_events"]["created_ids"] == []
    # A current Project citation may create its independent observed source on
    # the first replay.  It must not rewrite the pre-existing Task, Project,
    # or card, and an identical replay is fully idempotent thereafter.
    for table in (
        "business_tasks",
        "business_projects",
        "business_anchors",
        "business_attention_items",
        "business_attention_tasks",
        "business_attention_events",
        "business_task_events",
        "business_task_evidence",
    ):
        assert after_first_domain[table] == initial_domain[table]
    assert domain_snapshot() == after_first_domain


def test_evaluation_replays_exact_input_without_claiming_pending_or_rewriting_runs(
    tmp_path,
):
    tool = evaluation_tool()
    store = AutoReplyStore(tmp_path / "single.sqlite3")
    item = report_item()
    other = store.enqueue_work_summary_input(
        "project_weekly_report", "other", item.model_dump_json()
    )
    target = store.enqueue_work_summary_input(
        item.source.type.value, item.source.ref, item.model_dump_json()
    )
    store.mark_work_summary_input_done(target)
    old_run = store.record_task_agent_run(summary_input_id=target, decision_json="{}")
    with store._connect() as db:
        old = dict(
            db.execute(
                "select * from task_agent_runs where id=?", (old_run,)
            ).fetchone()
        )

    class Codex:
        def decide(self, **kwargs):
            assert kwargs["session_scope_id"] == tool.EVALUATION_SCOPE
            assert "evaluation_only_secret_expectation" not in kwargs["prompt"]
            return TaskAgentDecision.model_validate(decision_payload())

    from app.task_agent import TaskAgentRunner

    runner = tool.scoped_runner(TaskAgentRunner, Codex())
    result = tool.replay_input(store, runner, target, source_ref=item.source.ref)
    assert result["input_status"] == "done"
    assert result["proposal_count"] == 1
    assert result["persisted_attention_count"] == 1
    assert result["evidence_valid"] is True
    replay = tool.replay_input(store, runner, target, source_ref=item.source.ref)
    assert replay["cards"][0]["id"] == result["cards"][0]["id"]
    assert replay["changes"]["tasks"]["created_ids"] == []
    assert replay["changes"]["projects"]["created_ids"] == []
    assert replay["changes"]["attention_events"]["created_ids"] == []
    assert store.get_work_summary_input(other).status == "pending"
    assert store.get_work_summary_input(target).attempts == 0
    with store._connect() as db:
        assert (
            dict(
                db.execute(
                    "select * from task_agent_runs where id=?", (old_run,)
                ).fetchone()
            )
            == old
        )
    with pytest.raises(ValueError, match="source_ref"):
        tool.replay_input(store, runner, target, source_ref="wrong")


def test_evaluation_metrics_use_persisted_cards_and_detect_invalid_evidence(tmp_path):
    tool = evaluation_tool()
    store = AutoReplyStore(tmp_path / "metrics.sqlite3")
    seed_report(store)
    before = tool.read_domain(store)
    expected = {
        "attention_projects": ["示例项目"],
        "project_titles": ["示例项目"],
        "task_count": 1,
        "minimum_evidence_sources": 1,
    }
    result = tool.readback(store, input_id=None, before=before, expected=expected)
    assert result["passed"]
    (card,) = store.list_business_attention_items()
    assessment = json.loads(card.assessment_json)
    assessment["evidence"][0]["source_link"] = "https://example.invalid/invented"
    with store._connect() as db:
        db.execute(
            "update business_attention_items set assessment_json=?",
            (json.dumps(assessment),),
        )
    assert (
        tool.readback(store, input_id=None, before=before, expected=expected)[
            "evidence_valid"
        ]
        is False
    )
    with store._connect() as db:
        db.execute(
            "update business_attention_items set assessment_json=?",
            (
                json.dumps(
                    {
                        "evidence": [
                            {
                                "signal_id": 999,
                                "source_ref": "missing",
                                "source_excerpt": "invented",
                            }
                        ]
                    }
                ),
            ),
        )
    result = tool.readback(store, input_id=None, before=before, expected=expected)
    assert result["evidence_valid"] is False
    assert "unverifiable_attention_evidence" in result["failures"]


@pytest.mark.parametrize(
    "allowed,passes", [([1, 2], True), ([2, 3], False), ([0], False)]
)
def test_evaluation_allows_only_explicit_reviewed_task_counts(
    tmp_path, allowed, passes
):
    tool = evaluation_tool()
    store = AutoReplyStore(tmp_path / "counts.sqlite3")
    seed_report(store)
    expected = {
        "attention_projects": ["示例项目"],
        "project_titles": ["示例项目"],
        "allowed_task_counts": allowed,
        "minimum_evidence_sources": 1,
    }
    result = tool.readback(
        store, input_id=None, before=tool.read_domain(store), expected=expected
    )
    assert result["passed"] is passes
    assert ("task_count_mismatch" in result["failures"]) is (not passes)


def test_evaluation_refuses_worker_database_before_open(tmp_path, monkeypatch):
    tool = evaluation_tool()
    monkeypatch.setenv("CEO_WORKER_DB", str(tmp_path / "worker.sqlite3"))
    with pytest.raises(ValueError, match="worker database"):
        tool.require_copy(tmp_path / "worker.sqlite3")


def test_fixed_cases_seed_only_existing_facts_and_never_send_expected_labels(tmp_path):
    tool = evaluation_tool()
    cases = tool.load_cases(
        Path(__file__).parent / "fixtures/task_attention_multisource.json"
    )
    for case in cases:
        store = AutoReplyStore(tmp_path / f"{case['case_id']}.sqlite3")
        input_id = tool.seed_case(store, case)
        domain = tool.read_domain(store)
        assert len(domain["tasks"]) == len(case["existing_context"]["tasks"])
        assert len(domain["projects"]) == len(case["existing_context"]["projects"])
        assert len(domain["attention"]) == len(case["existing_context"]["attention"])
        case["expected"]["secret_label"] = "evaluation_only_secret_expectation"

        class Codex:
            def decide(self, **kwargs):
                assert "evaluation_only_secret_expectation" not in kwargs["prompt"]
                return TaskAgentDecision.model_validate(
                    {
                        "project_decisions": [],
                        "project_assessments": [],
                        "task_decisions": [],
                        "update_summary": (
                            "本 fake 只验证评测期望未进入 Agent prompt，"
                            "不生成 Task 或 Project 业务判断。"
                        ),
                    }
                )

        from app.task_agent import TaskAgentRunner

        result = tool.replay_input(
            store,
            tool.scoped_runner(TaskAgentRunner, Codex()),
            input_id,
            source_ref=case["work_item"]["source"]["ref"],
        )
        assert result["run_status"] == "completed"


def test_evaluation_missing_run_cannot_pass_a_negative_case(tmp_path):
    tool = evaluation_tool()
    store = AutoReplyStore(tmp_path / "not-run.sqlite3")
    item = report_item()
    input_id = store.enqueue_work_summary_input(
        item.source.type.value, item.source.ref, item.model_dump_json()
    )
    result = tool.readback(
        store,
        input_id=input_id,
        before=tool.read_domain(store),
        expected={
            "attention_projects": [],
            "project_titles": [],
            "task_count": 0,
            "minimum_evidence_sources": 1,
        },
    )
    assert result["passed"] is False
    assert "task_agent_run_missing" in result["failures"]


def recorded_assessment_case(store):
    item = report_item()
    input_id = store.enqueue_work_summary_input(
        item.source.type.value, item.source.ref, item.model_dump_json()
    )
    before = evaluation_tool().read_domain(store)
    result = apply_task_agent_decision(
        store,
        summary_input_id=input_id,
        work_item=item,
        decision=TaskAgentDecision.model_validate(decision_payload()),
    )
    expected = {
        "project_decisions": [],
        "attention_projects": ["示例项目"],
        "project_titles": ["示例项目"],
        "task_count": 1,
        "minimum_evidence_sources": 1,
        "project_assessments": [
            {
                "project_title": "示例项目",
                "outcome": "needs_attention",
                "evidence": [
                    {"source_ref": item.source.ref, "source_excerpt": RISK_QUOTE}
                ],
                "application_status": "applied",
                "task_count": 1,
                "attention_required": True,
            }
        ],
    }
    return item, input_id, before, result, expected


def recorded_no_task_assessment_case(store):
    tool = evaluation_tool()
    cases = tool.load_cases(
        Path(__file__).parent / "fixtures/task_attention_project_assessments_v1.json"
    )
    case = next(
        item for item in cases if item["case_id"] == "assessment-no-task-insufficient"
    )
    input_id = tool.seed_case(store, case)
    item = WorkItem.model_validate(case["work_item"])
    before = tool.read_domain(store)
    (project,) = store.list_business_projects()
    expected_assessment = case["expected"]["project_assessments"][0]
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "project_assessments": [
                {
                    "project_title": expected_assessment["project_title"],
                    "outcome": "insufficient_evidence",
                    "reason": "当前只有满意度下降线索，没有真实 Task 或已核实的经营影响。",
                    "assessment_basis": "current_observation",
                    "evidence": expected_assessment["evidence"],
                    "anchor_id": project.canonical_anchor_id,
                }
            ],
            "task_decisions": [],
            "update_summary": "已记录证据不足判断，不创建 Task 或 Attention。",
        }
    )
    result = apply_task_agent_decision(
        store,
        summary_input_id=input_id,
        work_item=item,
        decision=decision,
    )
    return item, input_id, before, result, case["expected"]


def recorded_two_project_assessment_case(store):
    tool = evaluation_tool()
    item = report_item()
    second_row = PROJECT_ROW.replace("示例项目", "另一项目")
    second_risk = "另一项目客户取消验收，回款延期导致本周供应商款项无法支付。"
    second_task_quote = TASK_QUOTE.replace("示例项目", "另一项目")
    source = json.loads(item.summary)
    source["markdown"] = source["markdown"].replace(
        PROJECT_ROW, f"{PROJECT_ROW}\n{second_row}"
    )
    source["markdown"] += f"\n{second_risk}\n{second_task_quote}"
    item = item.model_copy(update={"summary": json.dumps(source, ensure_ascii=False)})
    payload = decision_payload()
    second = json.loads(json.dumps(payload["task_decisions"][0], ensure_ascii=False))
    second.update(
        title="复核另一项目回款及供应商付款计划",
        source_excerpt=second_task_quote,
    )
    second["project"] = {"project_decision_index": 1}
    second["project_link_evidence"] = [
        {"source_ref": item.source.ref, "source_excerpt": second_task_quote}
    ]
    payload["task_decisions"].append(second)
    payload["project_decisions"].append(
        {
            "registration": {
                "title": "另一项目", "reason": "正式周报项目登记表明确列出",
                "authority": "project_weekly_report", "source_excerpt": second_row,
            },
            "evidence": [{"source_ref": item.source.ref, "source_excerpt": second_row}],
            "reason": "登记表明确列出正式项目。",
        }
    )
    payload["project_assessments"].append(
        {
            "project_title": "另一项目",
            "outcome": "needs_attention",
            "reason": "客户取消验收使回款延期，并已影响本周供应商付款。",
            "assessment_basis": "current_observation",
            "evidence": [
                {"source_ref": item.source.ref, "source_excerpt": second_risk}
            ],
            "project_decision_index": 1,
            "decision_indexes": [1],
            "attention_proposal": {
                "category": "watch", "title": "另一项目回款风险",
                "why_attention": "收入确认延迟与付款安排可能影响现金流",
                "current_state": "客户取消验收且回款延期",
                "ceo_action": "当前无需你处理；观察客户确认及付款安排是否恢复。",
                "assessment_basis": "current_observation", "material_trigger": "risk_escalation",
                "evidence": [{"source_ref": item.source.ref, "source_excerpt": second_risk}],
            },
        }
    )
    input_id = store.enqueue_work_summary_input(
        item.source.type.value, item.source.ref, item.model_dump_json()
    )
    before = tool.read_domain(store)
    apply_task_agent_decision(
        store,
        summary_input_id=input_id,
        work_item=item,
        decision=TaskAgentDecision.model_validate(payload),
    )
    expected = {
        "project_decisions": [],
        "attention_projects": ["示例项目", "另一项目"],
        "project_titles": ["示例项目", "另一项目"],
        "task_count": 2,
        "minimum_evidence_sources": 1,
        "project_assessments": [
            {
                "project_title": "示例项目",
                "outcome": "needs_attention",
                "evidence": [
                    {"source_ref": item.source.ref, "source_excerpt": RISK_QUOTE}
                ],
                "application_status": "applied",
                "task_count": 1,
                "attention_required": True,
            },
            {
                "project_title": "另一项目",
                "outcome": "needs_attention",
                "evidence": [
                    {"source_ref": item.source.ref, "source_excerpt": second_risk}
                ],
                "application_status": "applied",
                "task_count": 1,
                "attention_required": True,
            },
        ],
    }
    return input_id, before, expected


def read_latest_decision(store):
    with store._connect() as db:
        run_id = db.execute("select max(id) from task_agent_runs").fetchone()[0]
    return read_task_fixture_decision(store, run_id)


def rewrite_latest_run(store, *, decision=None, projection=None):
    with store._connect() as db:
        run = db.execute(
            "select * from task_agent_runs order by id desc limit 1"
        ).fetchone()
        if projection is not None:
            db.execute(
                "update task_agent_runs set projection_json=? where id=?",
                (json.dumps(projection, ensure_ascii=False), run["id"]),
            )
    if decision is not None:
        rewrite_task_fixture_decision(store, run["id"], decision)


def test_assessment_oracle_accepts_no_task_insufficient_receipt(tmp_path):
    tool = evaluation_tool()
    store = AutoReplyStore(tmp_path / "assessment-no-task-positive.sqlite3")
    _, input_id, before, applied, expected = recorded_no_task_assessment_case(store)

    assert applied.task_ids == ()
    assert applied.projection_receipt.project_assessments[0].task_ids == []
    result = tool.readback(store, input_id=input_id, before=before, expected=expected)
    assert result["passed"], result["failures"]


def test_assessment_oracle_rejects_unlinked_positive_receipt_signal_without_tasks(
    tmp_path,
):
    tool = evaluation_tool()
    store = AutoReplyStore(tmp_path / "assessment-no-task-unlinked-signal.sqlite3")
    item, input_id, before, _, expected = recorded_no_task_assessment_case(store)
    required = expected["project_assessments"][0]["evidence"][0]
    unlinked_signal_id = store.create_business_task_signal(
        source_type=item.source.type.value,
        source_ref=required["source_ref"],
        evidence_text=required["source_excerpt"],
        source_time=item.source.created_at,
        context_json="{}",
        dedupe_key="eval:no-task-unlinked-receipt-signal",
    )
    with store._connect() as db:
        projection = json.loads(
            db.execute("select projection_json from task_agent_runs").fetchone()[0]
        )
    receipt = projection["project_assessments"][0]
    assert receipt["task_ids"] == []
    assert receipt["attention_id"] is None
    receipt["evidence"].append(
        {
            "signal_id": unlinked_signal_id,
            "source_ref": required["source_ref"],
            "source_excerpt": required["source_excerpt"],
            "source_time": item.source.created_at,
            "source_link": "",
        }
    )
    rewrite_latest_run(store, projection=projection)

    result = tool.readback(store, input_id=input_id, before=before, expected=expected)

    assert not result["passed"]
    assert "project_assessment_receipt_mismatch" in result["failures"]


def test_assessment_oracle_observes_missing_raw_field_without_model_defaults(tmp_path):
    tool = evaluation_tool()
    store = AutoReplyStore(tmp_path / "assessment-missing.sqlite3")
    _, input_id, before, _, expected = recorded_assessment_case(store)
    raw = read_latest_decision(store)
    raw.pop("project_assessments")
    rewrite_latest_run(store, decision=raw)
    result = tool.readback(store, input_id=input_id, before=before, expected=expected)
    assert not result["passed"]
    assert "project_assessments_missing" in result["failures"]


def test_assessment_oracle_rejects_duplicate_receipt_assessment_index(tmp_path):
    tool = evaluation_tool()
    store = AutoReplyStore(tmp_path / "assessment-duplicate-receipt-index.sqlite3")
    _, input_id, before, _, expected = recorded_assessment_case(store)
    with store._connect() as db:
        projection = json.loads(
            db.execute("select projection_json from task_agent_runs").fetchone()[0]
        )
    duplicate = {
        **projection["project_assessments"][0],
        "status": "error",
        "task_ids": [],
        "attention_id": 99999,
    }
    projection["project_assessments"].append(duplicate)
    rewrite_latest_run(store, projection=projection)

    result = tool.readback(store, input_id=input_id, before=before, expected=expected)

    assert not result["passed"]
    assert "project_assessment_receipt_mismatch" in result["failures"]


def test_assessment_oracle_rejects_unmatched_extra_receipt_assessment(tmp_path):
    tool = evaluation_tool()
    store = AutoReplyStore(tmp_path / "assessment-unmatched-extra-receipt.sqlite3")
    _, input_id, before, _, expected = recorded_assessment_case(store)
    with store._connect() as db:
        projection = json.loads(
            db.execute("select projection_json from task_agent_runs").fetchone()[0]
        )
    projection["project_assessments"].append(
        {
            **projection["project_assessments"][0],
            "assessment_index": 99,
        }
    )
    rewrite_latest_run(store, projection=projection)

    result = tool.readback(store, input_id=input_id, before=before, expected=expected)

    assert not result["passed"]
    assert "project_assessment_receipt_mismatch" in result["failures"]


def test_assessment_oracle_matches_two_receipts_by_index_after_order_reversal(tmp_path):
    tool = evaluation_tool()
    store = AutoReplyStore(tmp_path / "assessment-reversed-receipts.sqlite3")
    input_id, before, expected = recorded_two_project_assessment_case(store)
    with store._connect() as db:
        projection = json.loads(
            db.execute("select projection_json from task_agent_runs").fetchone()[0]
        )
    assert [
        entry["assessment_index"] for entry in projection["project_assessments"]
    ] == [0, 1]
    projection["project_assessments"].reverse()
    rewrite_latest_run(store, projection=projection)

    result = tool.readback(store, input_id=input_id, before=before, expected=expected)

    assert result["passed"], result["failures"]


def test_assessment_oracle_rejects_null_reason_in_native_source(tmp_path):
    tool = evaluation_tool()
    store = AutoReplyStore(tmp_path / "assessment-null-reason.sqlite3")
    _, input_id, before, _, expected = recorded_assessment_case(store)
    decision = read_latest_decision(store)
    decision["project_assessments"][0]["reason"] = None
    rewrite_latest_run(store, decision=decision)

    result = tool.readback(store, input_id=input_id, before=before, expected=expected)

    assert not result["passed"]
    assert "task_agent_native_decision_unavailable" in result["failures"]


def test_assessment_oracle_rejects_blank_extra_current_citation(tmp_path):
    tool = evaluation_tool()
    store = AutoReplyStore(tmp_path / "assessment-blank-extra-citation.sqlite3")
    item, input_id, before, _, expected = recorded_assessment_case(store)
    with store._connect() as db:
        run = db.execute("select * from task_agent_runs").fetchone()
        projection = json.loads(run["projection_json"])
    decision = read_latest_decision(store)
    decision["project_assessments"][0]["evidence"].append(
        {
            "signal_id": None,
            "source_ref": item.source.ref,
            "source_excerpt": "",
        }
    )
    projection["project_assessments"][0]["evidence"].append(
        {
            "signal_id": None,
            "source_ref": item.source.ref,
            "source_excerpt": "",
            "source_time": item.source.created_at,
            "source_link": "",
        }
    )
    rewrite_latest_run(store, decision=decision, projection=projection)

    result = tool.readback(store, input_id=input_id, before=before, expected=expected)

    assert not result["passed"]
    assert "task_agent_native_decision_unavailable" in result["failures"]


@pytest.mark.parametrize(
    "fault,expected_failure",
    [
        ("wrong_project_title", "project_assessment_coverage_mismatch"),
        ("wrong_judgment", "project_assessment_outcome_mismatch"),
        ("unsupported_citation", "project_assessment_evidence_mismatch"),
        ("missing_negative_reason", "task_agent_native_decision_unavailable"),
    ],
)
def test_assessment_oracle_rejects_wrong_semantic_readback(
    tmp_path, fault, expected_failure
):
    tool = evaluation_tool()
    store = AutoReplyStore(tmp_path / f"assessment-{fault}.sqlite3")
    _, input_id, before, _, expected = recorded_assessment_case(store)
    decision = read_latest_decision(store)
    assessment = decision["project_assessments"][0]
    if fault == "wrong_project_title":
        assessment["project_title"] = "另一个未期望 Project"
    elif fault == "wrong_judgment":
        assessment["outcome"] = "not_needed"
        assessment["attention_proposal"] = None
    elif fault == "unsupported_citation":
        assessment["evidence"] = [
            {
                "signal_id": None,
                "source_ref": "report:unrelated",
                "source_excerpt": "未出现在来源中的句子。",
            }
        ]
    else:
        assessment["outcome"] = "not_needed"
        assessment["attention_proposal"] = None
        assessment["reason"] = "  "
        expected["project_assessments"][0]["outcome"] = "not_needed"
    rewrite_latest_run(store, decision=decision)
    result = tool.readback(store, input_id=input_id, before=before, expected=expected)
    assert not result["passed"]
    assert expected_failure in result["failures"]


def test_assessment_oracle_rejects_unlinked_raw_positive_signal_absent_from_receipt(
    tmp_path,
):
    tool = evaluation_tool()
    store = AutoReplyStore(tmp_path / "assessment-unlinked-raw-positive.sqlite3")
    _, input_id, before, _, expected = recorded_assessment_case(store)
    unrelated_signal_id = store.create_business_task_signal(
        source_type="project_weekly_report",
        source_ref="report:fixture",
        evidence_text=RISK_QUOTE,
        source_time="2026-09-30T12:00:00Z",
        context_json="{}",
        dedupe_key="eval:unlinked-raw-positive",
    )
    decision = read_latest_decision(store)
    decision["project_assessments"][0]["evidence"].append(
        {
            "signal_id": unrelated_signal_id,
            "source_ref": "report:fixture",
            "source_excerpt": RISK_QUOTE,
        }
    )
    rewrite_latest_run(store, decision=decision)

    result = tool.readback(store, input_id=input_id, before=before, expected=expected)

    assert not result["passed"]
    assert "project_assessment_evidence_mismatch" in result["failures"]


def test_assessment_oracle_accepts_linked_extra_positive_signal_in_raw_and_receipt(
    tmp_path,
):
    from app.project_context_service import ProjectContextService

    tool = evaluation_tool()
    store = AutoReplyStore(tmp_path / "assessment-linked-raw-positive.sqlite3")
    _, input_id, before, _, expected = recorded_assessment_case(store)
    with store._connect() as db:
        run = db.execute("select * from task_agent_runs").fetchone()
        projection = json.loads(run["projection_json"])
    decision = read_latest_decision(store)
    receipt = projection["project_assessments"][0]
    linked_signal_id = store.create_business_task_signal(
        source_type="project_weekly_report",
        source_ref="report:fixture",
        evidence_text=f"本周经营风险：{RISK_QUOTE}",
        source_time="2026-09-30T12:00:00Z",
        context_json="{}",
        dedupe_key="eval:linked-raw-positive",
    )
    store.link_business_task_evidence(
        task_id=receipt["task_ids"][0],
        signal_id=linked_signal_id,
        evidence_role="discovery",
    )
    project = next(
        project
        for project in store.list_business_projects()
        if project.canonical_anchor_id == receipt["anchor_id"]
    )
    with store.business_task_transaction() as db:
        ProjectContextService(store).apply(
            project_id=project.id,
            context=None,
            signal_ids=(linked_signal_id,),
            db=db,
        )
    decision["project_assessments"][0]["evidence"].append(
        {
            "signal_id": linked_signal_id,
            "source_ref": "report:fixture",
            "source_excerpt": f"本周经营风险：{RISK_QUOTE}",
        }
    )
    receipt["evidence"].append(
        {
            "signal_id": linked_signal_id,
            "source_ref": "report:fixture",
            "source_excerpt": f"本周经营风险：{RISK_QUOTE}",
            "source_time": "2026-09-30T12:00:00Z",
            "source_link": "",
        }
    )
    rewrite_latest_run(store, decision=decision, projection=projection)

    result = tool.readback(store, input_id=input_id, before=before, expected=expected)

    assert result["passed"], result["failures"]


@pytest.mark.parametrize(
    "fault",
    [
        "missing",
        "wrong_status",
        "false_existing",
        "forged_signal_id",
        "unlinked_real_signal",
        "unlinked_raw_and_receipt",
        "wrong_source_time",
        "wrong_source_link",
    ],
)
def test_assessment_oracle_requires_actual_application_receipt(tmp_path, fault):
    tool = evaluation_tool()
    store = AutoReplyStore(tmp_path / f"assessment-receipt-{fault}.sqlite3")
    _, input_id, before, applied, expected = recorded_assessment_case(store)
    with store._connect() as db:
        projection = json.loads(
            db.execute("select projection_json from task_agent_runs").fetchone()[0]
        )
    if fault == "missing":
        projection["project_assessments"] = []
    elif fault == "wrong_status":
        projection["project_assessments"][0]["status"] = "recorded"
    elif fault == "false_existing":
        projection["project_assessments"][0].update(
            status="existing",
            attention_id=applied.projection_receipt.project_assessments[0].attention_id
            + 999,
        )
        expected["project_assessments"][0]["application_status"] = "existing"
    elif fault == "forged_signal_id":
        projection["project_assessments"][0]["evidence"][0]["signal_id"] += 999
    elif fault in {"unlinked_real_signal", "unlinked_raw_and_receipt"}:
        original = projection["project_assessments"][0]["evidence"][0]
        unlinked = store.create_business_task_signal(
            source_type="project_weekly_report",
            source_ref=original["source_ref"],
            evidence_text=original["source_excerpt"],
            source_time=original["source_time"],
            context_json=json.dumps({"source_link": original["source_link"]}),
            dedupe_key="eval:unlinked-real-signal",
        )
        projection["project_assessments"][0]["evidence"][0]["signal_id"] = unlinked
        if fault == "unlinked_raw_and_receipt":
            decision = read_latest_decision(store)
            decision["project_assessments"][0]["evidence"][0]["signal_id"] = unlinked
            rewrite_latest_run(store, decision=decision)
    elif fault == "wrong_source_time":
        projection["project_assessments"][0]["evidence"][0]["source_time"] = (
            "2099-01-01T00:00:00Z"
        )
    else:
        projection["project_assessments"][0]["evidence"][0]["source_link"] = (
            "https://invalid.test/receipt"
        )
    rewrite_latest_run(store, projection=projection)
    result = tool.readback(store, input_id=input_id, before=before, expected=expected)
    assert not result["passed"]
    assert "project_assessment_receipt_mismatch" in result["failures"]


@pytest.mark.parametrize(
    "shape,passes",
    [
        ("prefix_and_extra", True),
        ("missing_required", False),
        ("forged_extra", False),
    ],
)
def test_assessment_oracle_requires_reviewed_proof_coverage_and_validates_extras(
    tmp_path, shape, passes
):
    tool = evaluation_tool()
    store = AutoReplyStore(tmp_path / f"assessment-proof-{shape}.sqlite3")
    item, input_id, before, _, expected = recorded_assessment_case(store)
    with store._connect() as db:
        run = db.execute("select * from task_agent_runs").fetchone()
        projection = json.loads(run["projection_json"])
    decision = read_latest_decision(store)
    if shape == "prefix_and_extra":
        evidence = [
            {
                "signal_id": None,
                "source_ref": item.source.ref,
                "source_excerpt": f"## 本周进展\n{RISK_QUOTE}",
            },
            {
                "signal_id": None,
                "source_ref": item.source.ref,
                "source_excerpt": PROJECT_ROW,
            },
        ]
    elif shape == "missing_required":
        evidence = [
            {
                "signal_id": None,
                "source_ref": item.source.ref,
                "source_excerpt": PROJECT_ROW,
            },
        ]
    else:
        evidence = [
            {
                "signal_id": None,
                "source_ref": item.source.ref,
                "source_excerpt": RISK_QUOTE,
            },
            {
                "signal_id": None,
                "source_ref": item.source.ref,
                "source_excerpt": "当期毛利已大幅恶化。",
            },
        ]
    decision["project_assessments"][0]["evidence"] = evidence
    projection["project_assessments"][0]["evidence"] = [
        {**entry, "source_time": item.source.created_at, "source_link": ""}
        for entry in evidence
    ]
    rewrite_latest_run(store, decision=decision, projection=projection)
    result = tool.readback(store, input_id=input_id, before=before, expected=expected)
    assert result["passed"] is passes
    assert ("project_assessment_evidence_mismatch" in result["failures"]) is (
        not passes
    )


def test_assessment_oracle_counts_unique_receipt_task_identities(tmp_path):
    tool = evaluation_tool()
    store = AutoReplyStore(tmp_path / "assessment-unique-receipt-tasks.sqlite3")
    item = report_item()
    second_quote = "核对示例项目供应商延期付款安排。"
    source = json.loads(item.summary)
    source["markdown"] += "\n" + second_quote
    item = item.model_copy(update={"summary": json.dumps(source, ensure_ascii=False)})
    payload = decision_payload()
    second = decision_payload()["task_decisions"][0]
    second.update(
        title="核对示例项目供应商延期付款安排",
        source_excerpt=second_quote,
    )
    payload["task_decisions"].append(second)
    payload["project_assessments"][0]["decision_indexes"] = [0, 1]

    class Codex:
        def decide(self, **kwargs):
            return TaskAgentDecision.model_validate(payload)

    from app.task_agent import TaskAgentRunner

    input_id = store.enqueue_work_summary_input(
        item.source.type.value, item.source.ref, item.model_dump_json()
    )
    before = tool.read_domain(store)
    tool.replay_input(
        store,
        tool.scoped_runner(TaskAgentRunner, Codex()),
        input_id,
        source_ref=item.source.ref,
    )
    with store._connect() as db:
        projection = json.loads(
            db.execute("select projection_json from task_agent_runs").fetchone()[0]
        )
    receipt = projection["project_assessments"][0]
    assert len(set(receipt["task_ids"])) == 2
    receipt["task_ids"] = [receipt["task_ids"][0], receipt["task_ids"][0]]
    rewrite_latest_run(store, projection=projection)
    expected = {
        "project_decisions": [],
        "attention_projects": ["示例项目"],
        "project_titles": ["示例项目"],
        "task_count": 2,
        "minimum_evidence_sources": 1,
        "required_project_member_counts": {"示例项目": 2},
        "project_assessments": [
            {
                "project_title": "示例项目",
                "outcome": "needs_attention",
                "evidence": [
                    {"source_ref": item.source.ref, "source_excerpt": RISK_QUOTE}
                ],
                "application_status": "applied",
                "task_count": 2,
                "attention_required": True,
            }
        ],
    }

    result = tool.readback(store, input_id=input_id, before=before, expected=expected)

    assert not result["passed"]
    assert "project_assessment_receipt_mismatch" in result["failures"]


def test_evaluation_new_risk_cannot_pass_with_only_historical_evidence(tmp_path):
    tool = evaluation_tool()
    store = AutoReplyStore(tmp_path / "historical-only.sqlite3")
    seed = seed_report(store)
    (original_card,) = store.list_business_attention_items()
    quote = "示例项目客户取消验收；复核回款计划并增加供应商付款协调。"
    item = WorkItem.model_validate(
        {
            "source": {
                "type": "ai_minutes",
                "ref": "meeting:current",
                "created_at": "2026-10-01T12:00:00Z",
            },
            "context": {"source_conversation_kind": "minutes"},
            "summary": quote,
        }
    )
    payload = decision_payload()
    row = payload["task_decisions"][0]
    row.update(
        action="update_task",
        transition="update_fields",
        task_id=seed.task_ids[0],
        source_ref=item.source.ref,
        source_excerpt=quote,
        description=quote,
    )
    payload["project_decisions"] = [{"anchor_id": original_card.anchor_id, "reason": "复用已登记 Project。", "evidence": [{"source_ref": item.source.ref, "source_excerpt": quote}]}]
    row["project"] = {"project_decision_index": 0}
    assessment = payload["project_assessments"][0]
    assessment["project_decision_index"] = 0
    assessment.pop("anchor_id", None)
    assessment["attention_proposal"]["evidence"][0]["signal_id"] = seed.attention_proposals[0].signal_id

    class Codex:
        def decide(self, **kwargs):
            assert "required_source_refs" not in kwargs["prompt"]
            return TaskAgentDecision.model_validate(payload)

    from app.task_agent import TaskAgentRunner

    input_id = store.enqueue_work_summary_input(
        item.source.type.value, item.source.ref, item.model_dump_json()
    )
    result = tool.replay_input(
        store,
        tool.scoped_runner(TaskAgentRunner, Codex()),
        input_id,
        source_ref=item.source.ref,
        expected={
            "attention_projects": ["示例项目"],
            "project_titles": ["示例项目"],
            "task_count": 1,
            "minimum_evidence_sources": 1,
            "reuse_attention": True,
            "required_source_refs": [item.source.ref],
        },
    )
    assert result["run_status"] == "failed"
    assert "requires current null-ID evidence" in result["execution_error"]
    assert store.list_business_attention_items()[0] == original_card
    assert result["passed"] is False


def test_evaluation_requires_source_on_each_target_project_card(tmp_path):
    tool = evaluation_tool()
    store = AutoReplyStore(tmp_path / "each-target.sqlite3")
    seed_report(store)
    second_item = report_item()
    second_item = second_item.model_copy(
        update={
            "source": second_item.source.model_copy(update={"ref": "report:other"}),
            "summary": second_item.summary.replace("示例项目", "另一项目"),
        }
    )
    second_payload = json.loads(
        json.dumps(decision_payload(), ensure_ascii=False)
        .replace("示例项目", "另一项目")
        .replace("report:fixture", "report:other")
    )
    apply_task_agent_decision(
        store,
        summary_input_id=2,
        work_item=second_item,
        decision=TaskAgentDecision.model_validate(second_payload),
        record_run=False,
    )
    result = tool.readback(
        store,
        input_id=None,
        before=tool.read_domain(store),
        expected={
            "attention_projects": ["示例项目", "另一项目"],
            "project_titles": ["示例项目", "另一项目"],
            "task_count": 2,
            "minimum_evidence_sources": 1,
            "required_source_refs": ["report:fixture"],
        },
    )
    assert result["evidence_valid"]
    assert result["passed"] is False
    assert "missing_required_source" in result["failures"]


@pytest.mark.parametrize("include_second_proposal", [False, True])
def test_evaluation_checks_both_same_project_actions_are_attention_members(
    tmp_path, include_second_proposal
):
    tool = evaluation_tool()
    store = AutoReplyStore(tmp_path / "project-members.sqlite3")
    item = report_item()
    second_quote = "核对示例项目供应商延期付款安排。"
    source = json.loads(item.summary)
    source["markdown"] += "\n" + second_quote
    item = item.model_copy(update={"summary": json.dumps(source, ensure_ascii=False)})
    payload = decision_payload()
    second = decision_payload()["task_decisions"][0]
    second.update(title="核对示例项目供应商延期付款安排", source_excerpt=second_quote)
    payload["task_decisions"].append(second)
    payload["project_assessments"][0]["decision_indexes"] = [0, 1]

    class Codex:
        def decide(self, **kwargs):
            assert "required_project_member_counts" not in kwargs["prompt"]
            return TaskAgentDecision.model_validate(payload)

    from app.task_agent import TaskAgentRunner

    input_id = store.enqueue_work_summary_input(
        item.source.type.value, item.source.ref, item.model_dump_json()
    )
    result = tool.replay_input(
        store,
        tool.scoped_runner(TaskAgentRunner, Codex()),
        input_id,
        source_ref=item.source.ref,
        expected={
            "attention_projects": ["示例项目"],
            "project_titles": ["示例项目"],
            "task_count": 2,
            "minimum_evidence_sources": 1,
            "required_source_refs": [item.source.ref],
            "required_project_member_counts": {"示例项目": 2},
        },
    )
    assert result["run_status"] == "completed", result.get("execution_error")
    assert result["projection"]["status"] == "completed"
    assert result["evidence_valid"]
    assert len(result["tasks"]) == 2
    assert len(result["cards"][0]["task_ids"]) == 2
    assert result["passed"] is True
    assert "missing_project_task_member" not in result["failures"]


@pytest.mark.parametrize(
    "status,outcome,recompute_error",
    [
        ("pending", "applied", ""),
        ("partial", "applied", ""),
        ("failed", "applied", ""),
        ("no_proposal", "applied", ""),
        ("completed", "rejected", ""),
        ("completed", "error", ""),
        ("completed", "applied", "member recompute failed"),
    ],
)
def test_evaluation_valid_old_card_cannot_mask_recorded_projection_failure(
    tmp_path, status, outcome, recompute_error
):
    from app.task_models import (
        TaskAttentionProjectionReceipt,
        TaskAttentionProjectionOutcome,
    )

    tool = evaluation_tool()
    store = AutoReplyStore(tmp_path / "failed-receipt.sqlite3")
    seed = seed_report(store)
    item = report_item()
    input_id = store.enqueue_work_summary_input(
        item.source.type.value, item.source.ref, item.model_dump_json()
    )
    run_id = store.record_task_agent_run(
        input_id, decision_json=json.dumps(decision_payload())
    )
    before = tool.read_domain(store)
    expected = {
        "attention_projects": ["示例项目"],
        "project_titles": ["示例项目"],
        "task_count": 1,
        "minimum_evidence_sources": 1,
        "required_source_refs": [item.source.ref],
    }
    # A current proposal without a persisted application receipt cannot pass;
    # this is distinct from historical cards with no current proposal.
    baseline = tool.readback(store, input_id=input_id, before=before, expected=expected)
    assert baseline["passed"] is False
    assert "projection_not_successful" in baseline["failures"]
    (card,) = store.list_business_attention_items()
    receipt = TaskAttentionProjectionReceipt(
        status=status,
        source_type=item.source.type.value,
        task_decision_count=1,
        project_link_count=1,
        registry_row_count=1,
        proposal_count=1,
        applied_count=1,
        outcomes=[
            TaskAttentionProjectionOutcome(
                task_id=seed.task_ids[0],
                anchor_id=card.anchor_id,
                attention_id=card.id,
                status=outcome,
                reason="persisted evaluation failure",
            )
        ],
        recompute_error=recompute_error,
    )
    store.record_task_agent_projection(run_id, receipt.model_dump_json())
    result = tool.readback(store, input_id=input_id, before=before, expected=expected)
    assert result["evidence_valid"]
    assert result["passed"] is False
    assert "projection_not_successful" in result["failures"]


PROJECT_ROW = "| 示例项目 | 回款复核 | 降低现金流风险 | 09-30 | 有风险 |"
RISK_QUOTE = "已交付收入因客户确认延迟，尚未进入当期确认，供应商付款需要协调。"
TASK_QUOTE = "复核示例项目回款及供应商付款计划。"


def report_item():
    return WorkItem.model_validate(
        {
            "source": {
                "type": "project_weekly_report",
                "ref": "report:fixture",
                "created_at": "2026-09-30T12:00:00Z",
            },
            "context": {"source_conversation_kind": "group"},
            "summary": json.dumps(
                {
                    "report": {"reporting_period": "2026-W39"},
                    "markdown": (
                        "## 手头项目\n| 项目名 | 负责内容 | 目标 | DDL | 状态 |\n"
                        "| --- | --- | --- | --- | --- |\n"
                        f"{PROJECT_ROW}\n## 本周进展\n{RISK_QUOTE}\n"
                        f"## 下周工作重点\n{TASK_QUOTE}"
                    ),
                },
                ensure_ascii=False,
            ),
        }
    )


def decision_payload():
    return {
        "project_decisions": [
            {
                "registration": {
                    "title": "示例项目",
                    "reason": "正式周报项目登记表明确列出",
                    "authority": "project_weekly_report",
                    "source_excerpt": PROJECT_ROW,
                },
                "evidence": [
                    {"source_ref": "report:fixture", "source_excerpt": PROJECT_ROW}
                ],
                "reason": "登记表明确列出正式项目。",
            }
        ],
        "project_assessments": [
            {
                "project_title": "示例项目",
                "outcome": "needs_attention",
                "reason": "客户确认延迟使已交付收入未能确认，并且已需协调供应商付款。",
                "assessment_basis": "current_observation",
                "evidence": [
                    {"source_ref": "report:fixture", "source_excerpt": RISK_QUOTE}
                ],
                "project_decision_index": 0,
                "decision_indexes": [0],
                "attention_proposal": {
                    "category": "watch",
                    "title": "示例项目回款风险",
                    "why_attention": "收入确认延迟与付款安排可能影响现金流",
                    "current_state": "收入确认延迟",
                    "ceo_action": "当前无需你处理；观察客户确认及付款安排是否恢复。",
                    "assessment_basis": "current_observation",
                    "material_trigger": "risk_escalation",
                    "evidence": [
                        {"source_ref": "report:fixture", "source_excerpt": RISK_QUOTE}
                    ],
                },
            }
        ],
        "task_decisions": [
            {
                "action": "record_candidate",
                "transition": "none",
                "source_ref": "report:fixture",
                "source_excerpt": TASK_QUOTE,
                "title": "复核示例项目回款及供应商付款计划",
                "missing_evidence": ["owner"],
                "project": {"project_decision_index": 0},
                "project_link_evidence": [
                    {"source_ref": "report:fixture", "source_excerpt": TASK_QUOTE}
                ],
            }
        ],
    }


def test_parse_separate_registry_task_and_attention_evidence_without_inventing_owner():
    item = report_item()
    parsed = TaskAgentDecision.model_validate(decision_payload()).task_decisions[0]
    report = json.loads(item.summary)["report"]
    assert report["reporting_period"] == "2026-W39"
    assert parsed.source_excerpt == TASK_QUOTE
    assert parsed.project.project_decision_index == 0
    project = TaskAgentDecision.model_validate(decision_payload()).project_decisions[0]
    assert project.registration.source_excerpt == PROJECT_ROW
    attention = TaskAgentDecision.model_validate(decision_payload()).project_assessments[0].attention_proposal
    assert attention.evidence[0].signal_id is None
    assert attention.evidence[0].source_ref == item.source.ref
    assert attention.evidence[0].source_excerpt == RISK_QUOTE
    assert RISK_QUOTE not in parsed.source_excerpt
    assert parsed.owner_name == ""
    assert parsed.owner_user_id == ""
    assert parsed.missing_evidence == ["owner"]


def test_report_risk_outside_action_projects_one_stable_card_with_verified_assessment(
    tmp_path,
):
    store = AutoReplyStore(tmp_path / "risk.sqlite3")
    decision = TaskAgentDecision.model_validate(decision_payload())
    result = apply_task_agent_decision(
        store,
        summary_input_id=1,
        work_item=report_item(),
        decision=decision,
        record_run=False,
    )
    (card,) = store.list_business_attention_items()
    (project,) = store.list_business_projects()
    assert card.stable_key == f"project:{project.canonical_anchor_id}"
    assert "当前无需你处理" in card.ceo_action
    assessment = json.loads(card.assessment_json)
    assert (
        assessment["inference"]
        == decision.project_assessments[0].attention_proposal.why_attention
    )
    assert assessment["evidence"] == [
        {
            "signal_id": result.attention_proposals[0].signal_id,
            "source_ref": "report:fixture",
            "source_excerpt": RISK_QUOTE,
            "source_time": "2026-09-30T12:00:00Z",
            "source_link": "",
        }
    ]
    receipt = result.projection_receipt
    assert (
        receipt.status,
        receipt.proposal_count,
        receipt.applied_count,
        receipt.project_link_count,
        receipt.registry_row_count,
    ) == ("completed", 1, 1, 1, 1)
    replay = apply_task_agent_decision(
        store,
        summary_input_id=2,
        work_item=report_item(),
        decision=decision,
        record_run=False,
    )
    assert replay.task_ids == result.task_ids
    assert store.list_business_projects() == [project]
    assert store.list_business_attention_items() == (card,)
    assert len(store.list_business_attention_events(card.id)) == 1


def seed_report(store):
    return apply_task_agent_decision(
        store,
        summary_input_id=1,
        work_item=report_item(),
        decision=TaskAgentDecision.model_validate(decision_payload()),
        record_run=False,
    )


def update_risk(
    store, seed, *, source_type="reply_attempt", evidence=None, related_ids=None
):
    quote = "客户确认进一步延迟，付款协调仍在推进。"
    item = WorkItem.model_validate(
        {
            "source": {
                "type": source_type,
                "ref": f"{source_type}:new",
                "created_at": "2026-10-01T15:00:00Z",
            },
            "context": {"source_conversation_kind": "group"},
            "summary": quote,
        }
    )
    proposal = decision_payload()["project_assessments"][0]["attention_proposal"]
    citations = evidence or [{"source_ref": item.source.ref, "source_excerpt": quote}]
    historical = any(entry.get("signal_id") is not None for entry in citations)
    if historical and not any(entry.get("signal_id") is None for entry in citations):
        citations = [
            {"source_ref": item.source.ref, "source_excerpt": quote},
            *citations,
        ]
    proposal.update(
        assessment_basis="historical_comparison"
        if historical
        else "current_observation",
        evidence=citations,
    )
    assessment_citations = [{"source_ref": item.source.ref, "source_excerpt": quote}]
    if historical:
        assessment_citations.append(
            {
                "signal_id": seed.attention_proposals[0].signal_id,
                "source_ref": "report:fixture",
                "source_excerpt": RISK_QUOTE,
            }
        )
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "project_assessments": [
                {
                    "project_title": "示例项目",
                    "outcome": "needs_attention",
                    "reason": "客户确认继续延迟，付款协调仍未完成。",
                    "assessment_basis": "historical_comparison"
                    if historical
                    else "current_observation",
                    "evidence": assessment_citations,
                    "anchor_id": seed.attention_proposals[0].anchor_id,
                    "decision_indexes": [0],
                    "task_ids": related_ids or [seed.task_ids[0]],
                    "attention_proposal": proposal,
                }
            ],
            "task_decisions": [
                {
                    "action": "update_task",
                    "transition": "update_fields",
                    "task_id": seed.task_ids[0],
                    "source_ref": item.source.ref,
                        "source_excerpt": quote,
                        "description": quote,
                        "title": store.get_business_task(seed.task_ids[0]).title,
                }
            ],
        }
    )
    return item, decision


@pytest.mark.parametrize(
    "fault",
    [
        "missing",
        "unlinked",
        "crossproject",
        "forged",
        "wrong_ref",
        "session_provenance",
        "memory_provenance",
    ],
)
def test_invalid_historical_citation_rejects_whole_proposal(tmp_path, fault):
    store = AutoReplyStore(tmp_path / f"{fault}.sqlite3")
    seed = seed_report(store)
    if fault == "missing":
        signal_id = 99999
    elif fault in {"forged", "wrong_ref"}:
        signal_id = seed.attention_proposals[0].signal_id
    else:
        signal_id = store.create_business_task_signal(
            source_type=fault,
            source_ref="report:fixture",
            evidence_text=RISK_QUOTE,
            dedupe_key=fault,
        )
        if fault in {"session_provenance", "memory_provenance"}:
            store.link_business_task_evidence(
                task_id=seed.task_ids[0], signal_id=signal_id, evidence_role="discovery"
            )
        if fault == "crossproject":
            other = seed_report_other_project(store)
            store.link_business_task_evidence(
                task_id=other.task_ids[0],
                signal_id=signal_id,
                evidence_role="discovery",
            )
    evidence = [
        {
            "signal_id": signal_id,
            "source_ref": "report:forged-ref"
            if fault == "wrong_ref"
            else "report:fixture",
            "source_excerpt": "不存在的风险原文" if fault == "forged" else RISK_QUOTE,
        }
    ]
    item, decision = update_risk(store, seed, evidence=evidence)
    if fault in {"unlinked", "crossproject"}:
        # Historical Project proof is checked against its immutable source,
        # not a legacy Task-evidence carrier or another Task's membership.
        result = apply_task_agent_decision(
            store, summary_input_id=2, work_item=item, decision=decision, record_run=False
        )
        assert result.projection_receipt.status == "completed"
        return
    with pytest.raises(ValueError):
        apply_task_agent_decision(
            store, summary_input_id=2, work_item=item, decision=decision, record_run=False
        )
    assert len(store.list_business_attention_events(1)) == 1


def seed_report_other_project(store):
    item = report_item()
    payload = decision_payload()
    item = item.model_copy(
        update={
            "summary": item.summary.replace("示例项目", "另一个项目"),
            "source": item.source.model_copy(update={"ref": "report:other"}),
        }
    )
    payload["task_decisions"][0]["source_ref"] = "report:other"
    row = payload["task_decisions"][0]
    row["title"] = row["title"].replace("示例项目", "另一个项目")
    row["source_excerpt"] = row["source_excerpt"].replace("示例项目", "另一个项目")
    row["project_link_evidence"][0]["source_ref"] = "report:other"
    row["project_link_evidence"][0]["source_excerpt"] = row["source_excerpt"]
    payload["project_decisions"][0]["registration"].update(
        title="另一个项目", source_excerpt=PROJECT_ROW.replace("示例项目", "另一个项目")
    )
    payload["project_assessments"][0]["attention_proposal"]["evidence"][0][
        "source_ref"
    ] = "report:other"
    payload["project_decisions"][0]["evidence"][0].update(
        source_ref="report:other",
        source_excerpt=PROJECT_ROW.replace("示例项目", "另一个项目"),
    )
    assessment = payload["project_assessments"][0]
    assessment["project_title"] = "另一个项目"
    assessment["evidence"][0].update(
        source_ref="report:other", source_excerpt=RISK_QUOTE
    )
    return apply_task_agent_decision(
        store,
        summary_input_id=3,
        work_item=item,
        decision=TaskAgentDecision.model_validate(payload),
        record_run=False,
    )


def test_crossproject_related_task_rejects_proposal(tmp_path):
    store = AutoReplyStore(tmp_path / "cross-related.sqlite3")
    seed, other = seed_report(store), seed_report_other_project(store)
    item, decision = update_risk(store, seed, related_ids=[other.task_ids[0]])
    with pytest.raises(ValueError, match="supporting Task is not confirmed"):
        apply_task_agent_decision(
            store, summary_input_id=2, work_item=item, decision=decision, record_run=False
        )


@pytest.mark.parametrize("distinct", [False, True])
def test_same_project_multiple_proposals_fold_only_identical_content(
    tmp_path, distinct
):
    store = AutoReplyStore(tmp_path / "multiple.sqlite3")
    payload = decision_payload()
    second = decision_payload()["task_decisions"][0]
    second["title"] = "复核付款协调行动"
    proposal = payload["project_assessments"][0]["attention_proposal"]
    if distinct:
        proposal["category"] = "decision"
    else:
        proposal["evidence"].append(dict(proposal["evidence"][0]))
    payload["task_decisions"].append(second)
    payload["project_assessments"][0]["decision_indexes"] = [0, 1]
    result = apply_task_agent_decision(
        store,
        summary_input_id=1,
        work_item=report_item(),
        decision=TaskAgentDecision.model_validate(payload),
        record_run=False,
    )
    receipt = result.projection_receipt
    assert receipt.proposal_count == 1
    assert receipt.status == "completed"
    assert receipt.applied_count == 1
    # A Project has one assessment-owned attention proposal.  Task count does
    # not create a second competing proposal.
    if True:
        (card,) = store.list_business_attention_items()
        assert {
            link.task_id for link in store.list_business_attention_tasks(card.id)
        } == set(result.task_ids)
        evidence = json.loads(card.assessment_json)["evidence"]
        assert evidence == [
            {
                "signal_id": entry.signal_id,
                "source_ref": "report:fixture",
                "source_excerpt": RISK_QUOTE,
                "source_time": "2026-09-30T12:00:00Z",
                "source_link": "",
            }
            for entry in result.attention_proposals
        ]
        assert len({entry["signal_id"] for entry in evidence}) == 1
        assert card.evidence_signal_id == result.attention_proposals[0].signal_id
        assert len(store.list_business_attention_events(card.id)) == 1
        replay = apply_task_agent_decision(
            store,
            summary_input_id=2,
            work_item=report_item(),
            decision=TaskAgentDecision.model_validate(payload),
            record_run=False,
        )
        assert replay.projection_receipt.applied_count == 1
        assert (
            store.get_business_attention_item(card.id).assessment_json
            == card.assessment_json
        )
        assert len(store.list_business_attention_events(card.id)) == 1


@pytest.mark.parametrize("failure", ["upsert", "receipt", "recompute"])
def test_projection_failure_preserves_committed_task_input_and_run(
    tmp_path, monkeypatch, failure
):
    store = AutoReplyStore(tmp_path / f"commit-{failure}.sqlite3")
    item = report_item()
    input_id = store.enqueue_work_summary_input(
        item.source.type.value, item.source.ref, item.model_dump_json()
    )
    (work_input,) = store.claim_work_summary_inputs(limit=1)

    class Runner:
        codex = type("Codex", (), {"last_session_id": ""})()

        def decide(self, *args, **kwargs):
            return TaskAgentDecision.model_validate(decision_payload())

    def fail(*args, **kwargs):
        raise RuntimeError(f"{failure} unavailable")

    if failure == "receipt":
        original = store.record_task_agent_projection

        def final_fail(run_id, projection_json, **kwargs):
            if json.loads(projection_json)["status"] != "pending":
                fail()
            return original(run_id, projection_json, **kwargs)

        monkeypatch.setattr(store, "record_task_agent_projection", final_fail)
    else:
        monkeypatch.setattr(
            BusinessAttentionProjection,
            "upsert" if failure == "upsert" else "recompute_for_tasks",
            fail,
        )
    process_work_item(store, Runner(), work_input)
    assert store.get_work_summary_input(input_id).status.value == "done"
    assert len(store.list_business_tasks()) == 1
    with store._connect() as db:
        (run,) = db.execute("select * from task_agent_runs").fetchall()
    assert run["status"] == "completed"
    receipt = json.loads(run["projection_json"])
    assert (
        receipt["status"]
        == {"upsert": "failed", "receipt": "pending", "recompute": "partial"}[failure]
    )
    if failure == "recompute":
        assert receipt["recompute_error"] == "recompute unavailable"


def test_registry_count_without_project_or_attention_proposal(tmp_path):
    store = AutoReplyStore(tmp_path / "counts.sqlite3")
    payload = decision_payload()
    payload["project_decisions"] = []
    payload["task_decisions"][0].pop("project")
    payload["task_decisions"][0].pop("project_link_evidence")
    payload["project_assessments"] = [
        {
            "project_title": "示例项目",
            "outcome": "insufficient_evidence",
            "reason": "来源提及该 Project，但此解析计数专测未提供正式登记提案或关注提案。",
            "assessment_basis": "current_observation",
            "evidence": [
                {"source_ref": "report:fixture", "source_excerpt": PROJECT_ROW}
            ],
            "decision_indexes": [],
        }
    ]
    result = apply_task_agent_decision(
        store,
        summary_input_id=1,
        work_item=report_item(),
        decision=TaskAgentDecision.model_validate(payload),
        record_run=False,
    )
    assert result.projection_receipt.status == "no_proposal"
    assert result.projection_receipt.registry_row_count == 1
    assert result.projection_receipt.project_link_count == 0


@pytest.mark.parametrize("source_type", ["ai_minutes", "reply_attempt"])
def test_new_source_risk_reuses_project_card_and_preserves_report_fields(
    tmp_path, source_type
):
    store = AutoReplyStore(tmp_path / f"new-{source_type}.sqlite3")
    seed = seed_report(store)
    (project,) = store.list_business_projects()
    (old_card,) = store.list_business_attention_items()
    item, decision = update_risk(store, seed, source_type=source_type)
    if source_type == "reply_attempt":
        historical = {
            "signal_id": seed.attention_proposals[0].signal_id,
            "source_ref": "report:fixture",
            "source_excerpt": RISK_QUOTE,
        }
        proposal = decision.project_assessments[0].attention_proposal
        proposal.evidence.append(type(proposal.evidence[0]).model_validate(historical))
        proposal.assessment_basis = "historical_comparison"
    result = apply_task_agent_decision(
        store, summary_input_id=2, work_item=item, decision=decision, record_run=False
    )
    assert result.projection_receipt.status == "completed"
    assert result.projection_receipt.registry_row_count is None
    assert result.projection_receipt.project_link_count == 0
    (card,) = store.list_business_attention_items()
    assert card.id == old_card.id
    assert store.list_business_projects() == [project]
    assert store.get_business_task(seed.task_ids[0]).description == item.summary
    evidence = json.loads(card.assessment_json)["evidence"]
    assert json.loads(card.assessment_json)["assessment_basis"] == (
        "historical_comparison"
        if source_type == "reply_attempt"
        else "current_observation"
    )
    assert evidence[0]["source_time"] == "2026-10-01T15:00:00Z"
    if source_type == "reply_attempt":
        assert evidence[1]["source_time"] == "2026-09-30T12:00:00Z"
        assert evidence[1]["signal_id"] != evidence[0]["signal_id"]


def test_later_proposal_preserves_open_sibling_and_completion_only_removes_member(
    tmp_path,
):
    store = AutoReplyStore(tmp_path / "siblings.sqlite3")
    payload = decision_payload()
    second = decision_payload()["task_decisions"][0]
    second["title"] = "复核付款协调行动"
    payload["task_decisions"].append(second)
    payload["project_assessments"][0]["decision_indexes"] = [0, 1]
    seed = apply_task_agent_decision(
        store,
        summary_input_id=1,
        work_item=report_item(),
        decision=TaskAgentDecision.model_validate(payload),
        record_run=False,
    )
    (card,) = store.list_business_attention_items()
    item, decision = update_risk(store, seed)
    apply_task_agent_decision(
        store, summary_input_id=2, work_item=item, decision=decision, record_run=False
    )
    assert {
        link.task_id for link in store.list_business_attention_tasks(card.id)
    } == set(seed.task_ids)
    completion_item = item.model_copy(
        update={
            "summary": "回款复核已经完成。",
            "source": item.source.model_copy(
                update={"ref": "chat:completed", "created_at": "2026-10-02T16:00:00Z"}
            ),
        }
    )
    completion = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "project_assessments": [
                {
                    "project_title": "示例项目",
                    "outcome": "not_needed",
                    "reason": "当前来源明确说明回款复核已完成，此轮不需新增关注提案。",
                    "assessment_basis": "current_observation",
                    "evidence": [
                        {
                            "source_ref": completion_item.source.ref,
                            "source_excerpt": completion_item.summary,
                        }
                    ],
                    "anchor_id": seed.attention_proposals[0].anchor_id,
                    "decision_indexes": [0],
                    "task_ids": [seed.task_ids[0]],
                }
            ],
            "task_decisions": [
                {
                    "action": "update_task",
                    "transition": "update_fields",
                    "task_id": seed.task_ids[0],
                    "title": store.get_business_task(seed.task_ids[0]).title,
                    "source_ref": completion_item.source.ref,
                    "source_excerpt": completion_item.summary,
                    "status": "done",
                }
            ],
        }
    )
    apply_task_agent_decision(
        store,
        summary_input_id=3,
        work_item=completion_item,
        decision=completion,
        record_run=False,
    )
    assert {link.task_id for link in store.list_business_attention_tasks(card.id)} == {
        seed.task_ids[1]
    }
    assert store.get_business_attention_item(card.id).status.value == "active"


def test_quote_cannot_join_decoded_json_leaves():
    from app.task_agent import source_contains_quote

    raw = json.dumps({"parts": ["收入确认延迟", "供应商付款受影响"]}, ensure_ascii=True)
    assert source_contains_quote(raw, "收入确认延迟")
    assert not source_contains_quote(raw, "收入确认延迟供应商付款受影响")
    assert not source_contains_quote(raw, " ")


def test_skipped_noop_proposal_is_counted_and_diagnosed(tmp_path):
    store = AutoReplyStore(tmp_path / "noop.sqlite3")
    seed = seed_report(store)
    item, decision = update_risk(store, seed)
    first = apply_task_agent_decision(
        store, summary_input_id=2, work_item=item, decision=decision, record_run=False
    )
    replay = apply_task_agent_decision(
        store, summary_input_id=3, work_item=item, decision=decision, record_run=False
    )
    assert first.projection_receipt.status == "completed"
    assert replay.projection_receipt.task_decision_count == 1
    assert replay.projection_receipt.proposal_count == 1
    # Replay of an existing signal remains a valid replay; a new source without
    # actual field changes is skipped by the Task domain instead.
    newer = item.model_copy(
        update={"source": item.source.model_copy(update={"ref": "chat:no-change"})}
    )
    row = decision.task_decisions[0].model_copy(update={"source_ref": newer.source.ref})
    assessment = decision.project_assessments[0].model_copy(
        update={
            "evidence": [
                decision.project_assessments[0]
                .evidence[0]
                .model_copy(
                    update={
                        "source_ref": newer.source.ref,
                    }
                )
            ],
        }
    )
    proposal = assessment.attention_proposal.model_copy(
        update={
            "evidence": [
                assessment.attention_proposal.evidence[0].model_copy(
                    update={"source_ref": newer.source.ref}
                )
            ]
        }
    )
    assessment = assessment.model_copy(update={"attention_proposal": proposal})
    skipped = apply_task_agent_decision(
        store,
        summary_input_id=4,
        work_item=newer,
        decision=decision.model_copy(
            update={
                "project_decisions": [],
                "project_assessments": [assessment],
                "task_decisions": [row],
            }
        ),
        record_run=False,
    )
    assert skipped.projection_receipt.status == "completed"
    assert skipped.projection_receipt.proposal_count == 1
    assert skipped.projection_receipt.outcomes[0].status == "applied"


def test_raw_skip_proposal_does_not_invent_a_task_id(tmp_path):
    store, item, seed, anchor_id, _, new_task = existing_project_link_case(tmp_path)
    valid = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "project_assessments": [
                existing_project_assessment(
                    item=item, seed=seed, anchor_id=anchor_id, decision_indexes=[0]
                )
            ],
            "task_decisions": [new_task],
        }
    )
    result = apply_task_agent_decision(
        store,
        summary_input_id=2,
        work_item=item,
        decision=valid.model_copy(
            update={
                "project_decisions": [],
                "task_decisions": [
                    valid.task_decisions[0].model_copy(
                        update={
                            "action": "skip",
                            "skip_reason": "No actionable Task",
                        }
                    )
                ],
            }
        ),
        record_run=False,
    )
    assert result.projection_receipt.proposal_count == 1
    assert result.projection_receipt.status == "completed"
    assert result.projection_receipt.outcomes[0].task_id is None


def test_folded_group_checks_each_current_signal_not_only_first(tmp_path):
    from app.task_agent import AppliedTaskAttention, _project_task_attention
    from app.task_models import TaskAttentionProjectionReceipt

    store = AutoReplyStore(tmp_path / "folded-provenance.sqlite3")
    seed = seed_report(store)
    applied = seed.attention_proposals[0]
    cited = store.create_business_task_signal(
        source_type="session_provenance",
        source_ref="report:fixture",
        evidence_text=RISK_QUOTE,
        dedupe_key="cited",
    )
    store.link_business_task_evidence(
        task_id=applied.task_ids[0], signal_id=cited, evidence_role="discovery"
    )
    receipt = _project_task_attention(
        store,
        (
            applied,
                AppliedTaskAttention(
                    applied.assessment_index,
                    applied.assessment,
                    applied.task_ids,
                    cited,
                    applied.anchor_id,
                ),
            ),
            (applied.task_ids[0],),
        receipt=TaskAttentionProjectionReceipt(
            status="pending",
            source_type="project_weekly_report",
            task_decision_count=2,
            project_link_count=0,
            proposal_count=2,
        ),
    )
    assert receipt.status == "partial"
    assert any(outcome.status == "rejected" for outcome in receipt.outcomes)


def test_report_plain_text_with_no_registry_has_zero_rows(tmp_path):
    store = AutoReplyStore(tmp_path / "plain.sqlite3")
    item = report_item().model_copy(update={"summary": TASK_QUOTE})
    result = apply_task_agent_decision(
        store,
        summary_input_id=1,
        work_item=item,
        decision=TaskAgentDecision.model_validate(
            {
                "project_decisions": [],
                "project_assessments": [],
                "task_decisions": [],
                "update_summary": "纯文本中没有正式 Project 登记区域或 Project 线索。",
            }
        ),
        record_run=False,
    )
    assert result.projection_receipt.registry_row_count == 0
    assert result.projection_receipt.status == "no_proposal"


def test_registered_project_without_attention_still_counts_applied_link(tmp_path):
    store = AutoReplyStore(tmp_path / "project-only.sqlite3")
    payload = decision_payload()
    payload["project_assessments"][0].update(
        outcome="insufficient_evidence",
        reason="当前夹具只验证 Project 登记与 Task 链接，候选 Task 缺少负责人，未提交关注提案。",
        attention_proposal=None,
    )
    result = apply_task_agent_decision(
        store,
        summary_input_id=1,
        work_item=report_item(),
        decision=TaskAgentDecision.model_validate(payload),
        record_run=False,
    )
    assert result.projection_receipt.project_link_count == 1
    assert (
        result.projection_receipt.proposal_count
        == result.projection_receipt.applied_count
        == 0
    )
    assert result.projection_receipt.status == "no_proposal"


def test_recorded_direct_apply_saves_receipt_on_actual_run(tmp_path):
    store = AutoReplyStore(tmp_path / "direct-run.sqlite3")
    result = apply_task_agent_decision(
        store,
        summary_input_id=8,
        work_item=report_item(),
        decision=TaskAgentDecision.model_validate(decision_payload()),
    )
    with store._connect() as db:
        (run,) = db.execute("select * from task_agent_runs").fetchall()
    assert run["summary_input_id"] == 8
    assert run["status"] == "completed"
    assert json.loads(run["projection_json"]) == result.projection_receipt.model_dump()


@pytest.mark.parametrize(
    "project_header", ["项目名", "项目名称", "业务项目", "工作流", "项目/方向"]
)
def test_registry_row_count_excludes_repeated_table_separators(
    tmp_path, project_header
):
    store = AutoReplyStore(tmp_path / "registry-count.sqlite3")
    item = report_item()
    payload = json.loads(item.summary)
    payload["markdown"] = payload["markdown"].replace(
        PROJECT_ROW,
        PROJECT_ROW + f"\n| {project_header} | 负责内容 | 目标 | DDL | 状态 |\n"
        "| :--- | ---: | --- | --- | --- |\n"
        + PROJECT_ROW.replace("示例项目", "第二个项目"),
    )
    item = item.model_copy(update={"summary": json.dumps(payload, ensure_ascii=False)})
    second_row = PROJECT_ROW.replace("示例项目", "第二个项目")
    result = apply_task_agent_decision(
        store,
        summary_input_id=1,
        work_item=item,
        decision=TaskAgentDecision.model_validate(
            {
                "project_decisions": [],
                "project_assessments": [
                    {
                        "project_title": title,
                        "outcome": "insufficient_evidence",
                        "reason": "此专测只核对登记表行计数，未提供对应真实 Task 或具体经营影响。",
                        "assessment_basis": "current_observation",
                        "evidence": [
                            {"source_ref": item.source.ref, "source_excerpt": quote}
                        ],
                    }
                    for title, quote in (
                        ("示例项目", PROJECT_ROW),
                        ("第二个项目", second_row),
                    )
                ],
                "task_decisions": [],
            }
        ),
        record_run=False,
    )
    assert result.projection_receipt.registry_row_count == 2


@pytest.mark.parametrize(
    "project_header", ["项目名称", "业务项目", "工作流", "项目/方向"]
)
def test_repeated_registry_header_cannot_register_project(tmp_path, project_header):
    store = AutoReplyStore(tmp_path / "header-project.sqlite3")
    item = report_item()
    source = json.loads(item.summary)
    header = f"| {project_header} | 负责内容 | 目标 | DDL | 状态 |"
    source["markdown"] = source["markdown"].replace(
        PROJECT_ROW, PROJECT_ROW + "\n" + header + "\n| --- | --- | --- | --- | --- |"
    )
    item = item.model_copy(update={"summary": json.dumps(source, ensure_ascii=False)})
    payload = decision_payload()
    payload["project_decisions"][0]["registration"].update(
        title=project_header, source_excerpt=header
    )
    payload["project_assessments"][0]["project_title"] = project_header
    with pytest.raises(ValueError, match="cited report registry row"):
        apply_task_agent_decision(
            store,
            summary_input_id=1,
            work_item=item,
            decision=TaskAgentDecision.model_validate(payload),
            record_run=False,
        )
    assert store.list_business_tasks() == ()
    assert store.list_business_projects() == []
    assert store.list_business_attention_items() == ()


def test_project_link_count_includes_confirmed_candidate_cluster_members(tmp_path):
    from app.task_business_resolution import BusinessResolutionService

    store = AutoReplyStore(tmp_path / "cluster-link-count.sqlite3")
    item = report_item()
    first = decision_payload()["task_decisions"][0]
    first.pop("project")
    first.pop("project_link_evidence")
    second = {**first, "title": "复核付款协调行动"}
    seed = apply_task_agent_decision(
        store,
        summary_input_id=1,
        work_item=item,
        decision=TaskAgentDecision.model_validate(
            {
                "project_decisions": [],
                "project_assessments": [
                    {
                        "project_title": "示例项目",
                        "outcome": "insufficient_evidence",
                        "reason": "两个候选行动只提供 Task 线索，尚未建立正式 Project 链接或关注证据。",
                        "assessment_basis": "current_observation",
                        "evidence": [
                            {
                                "source_ref": item.source.ref,
                                "source_excerpt": PROJECT_ROW,
                            }
                        ],
                        "decision_indexes": [0, 1],
                    }
                ],
                "task_decisions": [first, second],
            }
        ),
        record_run=False,
    )
    resolution = BusinessResolutionService(store)
    cluster_id = resolution.create_cluster(
        title="示例项目", task_ids=list(seed.task_ids)
    )
    resolution.propose_project(
        cluster_id=cluster_id, title="示例项目", reason="已有项目候选"
    )
    row = decision_payload()["task_decisions"][0]
    row.update(
        action="update_task",
        transition="update_fields",
        task_id=seed.task_ids[0],
        description="新来源补充回款复核细节",
        cluster_proposal={
            "cluster_id": cluster_id,
            "task_ids": list(seed.task_ids),
            "reason": "同一聚类",
        },
    )
    row["project"] = {"project_decision_index": 0}
    row["project_link_evidence"] = [
        {"source_ref": item.source.ref, "source_excerpt": TASK_QUOTE}
    ]
    project_only = decision_payload()["project_assessments"][0]
    project_only.update(
        outcome="insufficient_evidence",
        reason="当前仅确认 Project 登记与聚类链接，未提交关注提案。",
        decision_indexes=[0],
        attention_proposal=None,
        project_decision_index=0,
    )
    result = apply_task_agent_decision(
        store,
        summary_input_id=2,
        work_item=item,
        decision=TaskAgentDecision.model_validate(
            {
                "project_decisions": [decision_payload()["project_decisions"][0]],
                "project_assessments": [project_only],
                "task_decisions": [row],
            }
        ),
        record_run=False,
    )
    (project,) = store.list_business_projects()
    expected = {(task_id, project.canonical_anchor_id) for task_id in seed.task_ids}
    with store._connect() as db:
        actual = {
            tuple(link)
            for link in db.execute(
                "select task_id, anchor_id from business_task_anchor_links where status='confirmed' and active=1"
            )
        }
    assert actual == expected
    assert set(result.project_links) == expected
    assert result.projection_receipt.project_link_count == 2
    assert result.projection_receipt.proposal_count == 0


def test_conflicting_chat_risk_does_not_replace_report_registry_summary(
    tmp_path, monkeypatch
):
    from app.web_api.tasks import business_project_detail
    from app.project_context_service import ProjectContextService

    store = AutoReplyStore(tmp_path / "conflicting-chat.sqlite3")
    clock = {"now": "2026-10-07 10:10:30"}
    open_connection = store._open_connection

    def open_connection_with_test_clock():
        connection = open_connection()
        connection.create_function(
            "current_timestamp", 0, lambda: clock["now"]
        )
        return connection

    monkeypatch.setattr(store, "_open_connection", open_connection_with_test_clock)
    seed = seed_report(store)
    (project,) = store.list_business_projects()
    context = ProjectContext.model_validate(
        {
            "goal": "降低现金流风险",
            "scope": "回款复核",
            "overall_owner": None,
            "responsibilities": [],
            "facts": [
                {
                    "key": "current_status",
                    "text": "有风险",
                    "evidence": [
                        {
                            "signal_id": seed.current_signal_id,
                            "source_ref": "report:fixture",
                            "source_excerpt": PROJECT_ROW,
                        }
                    ],
                }
            ],
        }
    )
    with store.business_task_transaction() as db:
        ProjectContextService(store).apply(
            project_id=project.id,
            context=context,
            signal_ids=(seed.current_signal_id,),
            db=db,
        )
    before = business_project_detail(store, project.id).summary
    assert before.goal == "降低现金流风险"
    assert before.current_status == "有风险"
    assert before.updated_at == "2026-10-07 10:10:30"
    clock["now"] = "2026-10-07 10:10:31"
    item, decision = update_risk(store, seed)
    summary = item.summary + " 聊天提出目标改为扩张销售、状态恢复正常、DDL 改为 12-31。"
    item = item.model_copy(update={"summary": summary})
    row = decision.task_decisions[0].model_copy(update={"description": summary})
    result = apply_task_agent_decision(
        store,
        summary_input_id=2,
        work_item=item,
        decision=decision.model_copy(
            update={"project_decisions": [], "task_decisions": [row]}
        ),
        record_run=False,
    )
    assert result.projection_receipt.status == "completed"
    after = business_project_detail(store, project.id).summary
    assert after.model_dump(exclude={"updated_at"}) == before.model_dump(
        exclude={"updated_at"}
    )
    assert after.updated_at == "2026-10-07 10:10:31"
    (card,) = store.list_business_attention_items()
    assert (
        json.loads(card.assessment_json)["evidence"][0]["source_ref"] == item.source.ref
    )


def test_upsert_value_error_is_an_application_error_not_a_quote_rejection(
    tmp_path, monkeypatch
):
    store = AutoReplyStore(tmp_path / "upsert-value-error.sqlite3")

    def fail(*args, **kwargs):
        raise ValueError("card write rejected")

    monkeypatch.setattr(BusinessAttentionProjection, "upsert", fail)
    result = seed_report(store)
    assert result.projection_receipt.status == "failed"
    assert result.projection_receipt.outcomes[0].status == "rejected"
    assert result.projection_receipt.outcomes[0].reason == "card write rejected"
    assert len(store.list_business_tasks()) == 1


def test_new_task_project_selector_requires_a_current_project_decision():
    payload = decision_payload()
    del payload["task_decisions"][0]["project"]
    payload["project_assessments"][0]["decision_indexes"] = [0]
    with pytest.raises(
        ValidationError, match="project_link_evidence requires a Project selector"
    ):
        TaskAgentDecision.model_validate(payload)


def test_assessment_resolves_a_current_project_decision():
    payload = decision_payload()
    parsed = TaskAgentDecision.model_validate(payload).project_assessments[0]
    assert parsed.project_decision_index == 0


@pytest.mark.parametrize(
    "registration_excerpt",
    [
        PROJECT_ROW,
        PROJECT_ROW.lstrip("| "),
        "回款复核 | 降低现金流风险 | 09-30 | 有风险 |",
        "\n" + PROJECT_ROW,
    ],
)
def test_apply_independent_registry_proof_registers_project_and_reuses_task(
    tmp_path, registration_excerpt
):
    store = AutoReplyStore(tmp_path / "registry.sqlite3")
    payload = decision_payload()
    payload["project_decisions"][0]["registration"]["source_excerpt"] = (
        registration_excerpt
    )
    decision = TaskAgentDecision.model_validate(payload)
    result = apply_task_agent_decision(
        store,
        summary_input_id=1,
        work_item=report_item(),
        decision=decision,
        record_run=False,
    )
    (project,) = store.list_business_projects()
    assert project.title == "示例项目"
    task = store.get_business_task(result.task_ids[0])
    assert task.stage.value == "candidate"
    assert task.business_relevance.value == "relevant"
    assert [
        link.anchor_id
        for link in store.list_business_task_anchor_links(task_id=task.id)
    ] == [project.canonical_anchor_id]
    (applied,) = result.attention_proposals
    assert applied.anchor_id == project.canonical_anchor_id
    assert applied.task_ids == (task.id,)
    assert applied.assessment == decision.project_assessments[0]
    replay = apply_task_agent_decision(
        store,
        summary_input_id=2,
        work_item=report_item(),
        decision=decision,
        record_run=False,
    )
    assert replay.task_ids == result.task_ids
    assert store.list_business_projects() == [project]
    assert replay.attention_proposals[0].anchor_id == applied.anchor_id


@pytest.mark.parametrize(
    "change",
    [
        {"title": "另一项目"},
        {"source_excerpt": TASK_QUOTE},
        {"source_excerpt": "| 虚构项目 | 无来源 |"},
        {"authority": "management_weekly_report"},
        {
            "title": "回款复核",
            "source_excerpt": "回款复核 | 降低现金流风险 | 09-30 | 有风险 |",
        },
    ],
)
def test_invalid_registry_proof_rolls_back_domain_transaction(tmp_path, change):
    store = AutoReplyStore(tmp_path / "invalid-registry.sqlite3")
    payload = decision_payload()
    payload["project_decisions"][0]["registration"].update(change)
    payload["project_assessments"][0]["project_title"] = payload["project_decisions"][0][
        "registration"
    ]["title"]
    with pytest.raises(ValueError, match="cited report registry row"):
        apply_task_agent_decision(
            store,
            summary_input_id=1,
            work_item=report_item(),
            decision=TaskAgentDecision.model_validate(payload),
            record_run=False,
        )
    assert store.list_business_projects() == []
    with store._connect() as db:
        assert db.execute("select count(*) from business_tasks").fetchone()[0] == 0
        assert (
            db.execute("select count(*) from business_task_signals").fetchone()[0] == 0
        )


@pytest.mark.parametrize("quote_from_following_row", [False, True])
def test_registry_prose_cannot_register_a_project(tmp_path, quote_from_following_row):
    store = AutoReplyStore(tmp_path / "registry-prose.sqlite3")
    prose = "项目负责人统一更新登记信息。"
    second_row = PROJECT_ROW.replace("示例项目", "真实第二项目")
    work_item = report_item()
    source = json.loads(work_item.summary)
    source["markdown"] = source["markdown"].replace(
        PROJECT_ROW,
        PROJECT_ROW + "\n" + prose + "\n" + second_row,
    )
    work_item = work_item.model_copy(
        update={"summary": json.dumps(source, ensure_ascii=False)}
    )
    payload = decision_payload()
    payload["project_decisions"][0]["registration"].update(
        title=prose,
        source_excerpt="\n" + second_row if quote_from_following_row else prose,
    )
    payload["project_assessments"][0]["project_title"] = prose
    with pytest.raises(ValueError, match="cited report registry row"):
        apply_task_agent_decision(
            store,
            summary_input_id=1,
            work_item=work_item,
            decision=TaskAgentDecision.model_validate(payload),
            record_run=False,
        )
    assert store.list_business_projects() == []
    with store._connect() as db:
        assert db.execute("select count(*) from business_tasks").fetchone()[0] == 0


@pytest.mark.parametrize(
    "meeting_source,quote",
    [
        (False, TASK_QUOTE),
        (True, "会议决定启动未出现在来源的项目"),
    ],
)
def test_meeting_registration_requires_meeting_provenance_and_exact_quote(
    tmp_path, meeting_source, quote
):
    store = AutoReplyStore(tmp_path / "invalid-meeting.sqlite3")
    item = report_item()
    item = item.model_copy(
        update={
            "source": item.source.model_copy(
                update={
                    "type": WorkItemSourceType.AI_MINUTES
                    if meeting_source
                    else WorkItemSourceType.REPLY_ATTEMPT
                }
            ),
        }
    )
    payload = decision_payload()
    payload["project_decisions"][0]["registration"].update(
        authority="meeting_decision", source_excerpt=quote
    )
    with pytest.raises(ValueError, match="meeting Project registration"):
        apply_task_agent_decision(
            store,
            summary_input_id=1,
            work_item=item,
            decision=TaskAgentDecision.model_validate(payload),
            record_run=False,
        )
    assert store.list_business_projects() == []


@pytest.mark.parametrize("new_project", [False, True])
def test_each_decision_keeps_its_own_applied_project_anchor(tmp_path, new_project):
    store = AutoReplyStore(tmp_path / "separate-anchors.sqlite3")
    payload = decision_payload()
    second = decision_payload()["task_decisions"][0]
    second["title"] = "另一项回款复核"
    item = report_item()
    second_risk = "另一项目客户取消验收，回款延期导致本周供应商款项无法支付。"
    if new_project:
        second_row = PROJECT_ROW.replace("示例项目", "另一项目")
        second["project"] = {"project_decision_index": 1}
        second_quote = TASK_QUOTE.replace("示例项目", "另一项目")
        second["source_excerpt"] = second_quote
        second["project_link_evidence"] = [
            {"source_ref": item.source.ref, "source_excerpt": second_quote}
        ]
        source = json.loads(item.summary)
        source["markdown"] = source["markdown"].replace(
            PROJECT_ROW, f"{PROJECT_ROW}\n{second_row}"
        )
        source["markdown"] += f"\n{second_risk}\n{second_quote}"
        item = item.model_copy(
            update={"summary": json.dumps(source, ensure_ascii=False)}
        )
        payload["project_decisions"].append(
            {
                "registration": {
                    "title": "另一项目",
                    "reason": "正式周报登记表列出该项目。",
                    "authority": "project_weekly_report",
                    "source_excerpt": second_row,
                },
                "evidence": [
                    {"source_ref": item.source.ref, "source_excerpt": second_row}
                ],
                "reason": "登记表明确列出另一项目。",
            }
        )
        payload["project_assessments"].append(
            {
                "project_title": "另一项目",
                "outcome": "needs_attention",
                "reason": "客户取消验收使回款延期，并已影响本周供应商付款。",
                "assessment_basis": "current_observation",
                "evidence": [
                    {"source_ref": item.source.ref, "source_excerpt": second_risk}
                ],
                "project_decision_index": 1,
                "decision_indexes": [1],
                "attention_proposal": {
                    "category": "watch",
                    "title": "另一项目验收与回款风险",
                    "why_attention": "验收推迟影响回款。",
                    "current_state": second_risk,
                    "ceo_action": "观察付款安排。",
                    "assessment_basis": "current_observation",
                    "material_trigger": "risk_escalation",
                    "evidence": [
                        {"source_ref": item.source.ref, "source_excerpt": second_risk}
                    ],
                },
            }
        )
    else:
        seed = seed_report_other_project(store)
        other_project = next(
            project
            for project in store.list_business_projects()
            if project.title == "另一个项目"
        )
        second.update(
            action="update_task", transition="update_fields", task_id=seed.task_ids[0]
        )
        second["project"] = {"anchor_id": other_project.canonical_anchor_id}
        second["project_link_evidence"] = [
            {"source_ref": item.source.ref, "source_excerpt": TASK_QUOTE}
        ]
        source = json.loads(item.summary)
        source["markdown"] += f"\n{second_risk}"
        item = item.model_copy(
            update={"summary": json.dumps(source, ensure_ascii=False)}
        )
        payload["project_assessments"].append(
            {
                "project_title": "另一个项目",
                "outcome": "needs_attention",
                "reason": "客户取消验收使回款延期，并已影响本周供应商付款。",
                "assessment_basis": "current_observation",
                "evidence": [
                    {"source_ref": item.source.ref, "source_excerpt": second_risk}
                ],
                "anchor_id": other_project.canonical_anchor_id,
                "decision_indexes": [1],
                "task_ids": [seed.task_ids[0]],
                "attention_proposal": {
                    "category": "watch",
                    "title": "另一个项目验收与回款风险",
                    "why_attention": "验收推迟影响回款。",
                    "current_state": second_risk,
                        "ceo_action": "观察付款安排。",
                        "assessment_basis": "current_observation",
                        "material_trigger": "risk_escalation",
                        "evidence": [
                        {"source_ref": item.source.ref, "source_excerpt": second_risk}
                    ],
                },
            }
        )
    payload["task_decisions"].append(second)
    result = apply_task_agent_decision(
        store,
        summary_input_id=1,
        work_item=item,
        decision=TaskAgentDecision.model_validate(payload),
        record_run=False,
    )
    first, second = result.attention_proposals
    assert first.anchor_id == next(
        project.canonical_anchor_id
        for project in store.list_business_projects()
        if project.title == "示例项目"
    )
    expected_second_anchor = next(
        project.canonical_anchor_id
        for project in store.list_business_projects()
        if project.title in ({"另一项目"} if new_project else {"另一个项目"})
    )
    assert second.anchor_id == expected_second_anchor
    assert second.anchor_id != first.anchor_id


def test_registry_task_excerpt_without_project_proposal_does_not_register(tmp_path):
    store = AutoReplyStore(tmp_path / "no-proposal.sqlite3")
    payload = decision_payload()
    row = payload["task_decisions"][0]
    row["source_excerpt"] = PROJECT_ROW
    payload["project_decisions"] = []
    row.pop("project")
    row.pop("project_link_evidence")
    payload["project_assessments"] = [
        {
            "project_title": "示例项目",
            "outcome": "insufficient_evidence",
            "reason": "登记表行被当作 Task 引文，但没有显式 Project 登记提案，不能由服务端隐式登记。",
            "assessment_basis": "current_observation",
            "evidence": [
                {"source_ref": "report:fixture", "source_excerpt": PROJECT_ROW}
            ],
            "decision_indexes": [],
        }
    ]
    apply_task_agent_decision(
        store,
        summary_input_id=1,
        work_item=report_item(),
        decision=TaskAgentDecision.model_validate(payload),
        record_run=False,
    )
    assert store.list_business_projects() == []


def test_project_selector_requires_exactly_one_current_identity():
    payload = decision_payload()
    payload["task_decisions"][0]["project"] = {}
    with pytest.raises(ValidationError):
        TaskAgentDecision.model_validate(payload)


def test_project_selector_cannot_reference_another_missing_decision():
    payload = decision_payload()
    payload["task_decisions"][0]["project"] = {"project_decision_index": 1}
    with pytest.raises(ValidationError):
        TaskAgentDecision.model_validate(payload)


def test_existing_anchor_selector_does_not_require_new_registration():
    payload = decision_payload()
    decision = payload["task_decisions"][0]
    payload["project_decisions"] = [{"anchor_id": 8, "reason": "已登记 Project。", "evidence": [{"source_ref": "report:fixture", "source_excerpt": PROJECT_ROW}]}]
    decision["project"] = {"anchor_id": 8}
    payload["project_assessments"][0].update(
        project_decision_index=None,
        anchor_id=8,
        assessment_basis="current_observation",
        decision_indexes=[],
        task_ids=[],
    )
    parsed = TaskAgentDecision.model_validate(payload).task_decisions[0]
    assert parsed.project.anchor_id == 8


@pytest.mark.parametrize(
    "field,value",
    [
        ("evidence", []),
        ("evidence", None),
        ("anchor_id", 0),
        ("anchor_id", "8"),
        ("anchor_id", True),
        ("related_task_ids", [0]),
        ("related_task_ids", [-1]),
        ("related_task_ids", ["3"]),
        ("related_task_ids", [True]),
    ],
)
def test_attention_rejects_invalid_evidence_and_ids(field, value):
    payload = decision_payload()
    payload["project_assessments"][0]["attention_proposal"][field] = value
    with pytest.raises(ValidationError) as error:
        TaskAgentDecision.model_validate(payload)
    assert any(field in e["loc"] for e in error.value.errors())


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_ref", ""),
        ("source_ref", " \t\n"),
        ("source_excerpt", ""),
        ("source_excerpt", " \t\n"),
        ("signal_id", 0),
        ("signal_id", -1),
        ("signal_id", "1"),
        ("signal_id", True),
    ],
)
def test_attention_evidence_rejects_blank_provenance_and_invalid_signal_ids(
    field, value
):
    payload = decision_payload()
    payload["project_assessments"][0]["attention_proposal"]["evidence"][0][field] = value
    with pytest.raises(ValidationError) as error:
        TaskAgentDecision.model_validate(payload)
    assert any(e["loc"][-1] == field for e in error.value.errors())


@pytest.mark.parametrize("value", ["", " \t\n", None])
def test_project_registration_excerpt_is_required_and_nonblank(value):
    payload = decision_payload()
    payload["project_decisions"][0]["registration"]["source_excerpt"] = value
    with pytest.raises(ValidationError) as error:
        TaskAgentDecision.model_validate(payload)
    assert any(e["loc"][-1] == "source_excerpt" for e in error.value.errors())


def test_project_registration_excerpt_cannot_be_omitted():
    payload = decision_payload()
    del payload["project_decisions"][0]["registration"]["source_excerpt"]
    with pytest.raises(ValidationError) as error:
        TaskAgentDecision.model_validate(payload)
    assert any(e["loc"][-1] == "source_excerpt" for e in error.value.errors())


def test_attention_evidence_cannot_be_omitted():
    payload = decision_payload()
    del payload["project_assessments"][0]["attention_proposal"]["evidence"]
    with pytest.raises(ValidationError) as error:
        TaskAgentDecision.model_validate(payload)
    assert any(e["loc"][-1] == "evidence" for e in error.value.errors())


def test_old_trigger_evidence_field_is_rejected():
    payload = decision_payload()
    payload["project_assessments"][0]["attention_proposal"]["trigger_evidence"] = RISK_QUOTE
    with pytest.raises(ValidationError) as error:
        TaskAgentDecision.model_validate(payload)
    assert any(
        e["loc"][-1] == "trigger_evidence" and e["type"] == "extra_forbidden"
        for e in error.value.errors()
    )


def existing_project_link_case(tmp_path, source_type="ai_minutes"):
    from app.project_context_service import ProjectContextService
    from app.task_business_resolution import BusinessResolutionService
    from app.task_semantic_service import (
        RecordCandidate,
        SourceSignal,
        TaskSemanticService,
    )
    from app.task_attention_projection import AttentionProposal

    store = AutoReplyStore(tmp_path / "existing-project-link.sqlite3")
    seed = TaskSemanticService(store).record_candidate(
        RecordCandidate(
            title="复核示例交付项目验收计划",
            description="复核已登记项目的验收计划。",
            signal=SourceSignal(
                source_type="project_weekly_report",
                source_ref="report:existing",
                evidence_text="复核示例交付项目验收计划。验收延期可能影响回款。",
                dedupe_key="report:existing",
            ),
        )
    )
    resolution = BusinessResolutionService(store)
    anchor_id = resolution.register_anchor(
        anchor_type="project", anchor_ref="project:existing", title="示例交付项目"
    )
    project_id = resolution.register_official_project(
        anchor_id=anchor_id, registry_source="report:existing"
    )
    resolution.confirm_anchor_match(
        task_id=seed.task_id, anchor_id=anchor_id, evidence_signal_id=seed.signal_id
    )
    with store.business_task_transaction() as db:
        ProjectContextService(store).apply(
            project_id=project_id, context=None, signal_ids=(seed.signal_id,), db=db
        )
    card_id = BusinessAttentionProjection(store).upsert(
        AttentionProposal(
            stable_key=f"project:{anchor_id}",
            category="watch",
            title="原验收风险",
            business_area="示例交付项目",
            why_attention="验收延期影响回款",
            current_state="验收延期",
            ceo_action="当前无需处理",
            anchor_id=anchor_id,
            task_ids=(seed.task_id,),
            evidence_signal_id=seed.signal_id,
        )
    )
    action = "复核示例交付项目供应商延期付款安排。"
    risk = "示例交付项目客户取消验收，回款延期导致供应商本周款项无法支付。"
    item = WorkItem.model_validate(
        {
            "source": {
                "type": source_type,
                "ref": "source:project-risk",
                "created_at": "2026-10-02T12:00:00Z",
            },
            "context": {
                "source_conversation_kind": "minutes"
                if source_type == "ai_minutes"
                else "group"
            },
            "summary": f"{risk}\n复核示例交付项目验收计划，补充客户取消验收后的调整。\n{action}",
        }
    )
    new_task = {
        "action": "record_candidate",
        "transition": "none",
        "title": action[:-1],
        "source_ref": item.source.ref,
        "source_excerpt": action,
        "description": action,
        "project": {"anchor_id": anchor_id},
        "project_link_evidence": [
            {"source_ref": item.source.ref, "source_excerpt": action}
        ],
    }
    return store, item, seed, anchor_id, card_id, new_task


def existing_project_assessment(
    *, item, seed, anchor_id, decision_indexes, outcome="needs_attention"
):
    return {
        "project_title": "示例交付项目",
        "outcome": outcome,
        "reason": (
            "客户取消验收且回款延期，已影响本周供应商付款安排。"
            if outcome == "needs_attention"
            else "此路径只核对当前 Task 与已登记 Project 的链接，没有提交新关注提案。"
        ),
        "assessment_basis": "current_observation",
        "evidence": [
            {
                "source_ref": item.source.ref,
                "source_excerpt": item.summary.splitlines()[0],
            }
        ],
        "anchor_id": anchor_id,
        "decision_indexes": decision_indexes,
        "task_ids": [seed.task_id],
        "attention_proposal": {
            key: value for key, value in {
                "category": "watch",
                "title": "验收取消与付款风险",
                "why_attention": "验收取消及回款延期影响付款",
                "current_state": item.summary.splitlines()[0],
                "ceo_action": "当前无需你处理；观察延期付款安排",
                "assessment_basis": "current_observation",
                "material_trigger": "risk_escalation",
                "evidence": [
                    {"source_ref": item.source.ref, "source_excerpt": item.summary.splitlines()[0]}
                ],
            }.items()
        } if outcome == "needs_attention" else None,
    }


@pytest.mark.parametrize("source_type", ["ai_minutes", "reply_attempt"])
def test_current_source_links_new_task_to_existing_project_and_updates_one_card(
    tmp_path, source_type
):
    from app.task_agent import TaskAgentRunner

    store, item, seed, anchor_id, old_card_id, new_task = existing_project_link_case(
        tmp_path, source_type
    )
    registered_projects = store.list_business_projects()
    payload = {
        "project_decisions": [],
        "project_assessments": [
            existing_project_assessment(
                item=item, seed=seed, anchor_id=anchor_id, decision_indexes=[0, 1]
            )
        ],
        "task_decisions": [
            {
                "action": "update_task",
                "transition": "update_fields",
                "task_id": seed.task_id,
                "title": "复核示例交付项目验收计划",
                "description": "客户取消验收后复核计划并补充调整方案。",
                "source_ref": item.source.ref,
                "source_excerpt": "复核示例交付项目验收计划，补充客户取消验收后的调整。",
                "project": {"anchor_id": anchor_id},
            },
            new_task,
        ],
    }

    class Codex:
        def decide(self, **kwargs):
            return TaskAgentDecision.model_validate(payload)

    input_id = store.enqueue_work_summary_input(
        item.source.type.value, item.source.ref, item.model_dump_json()
    )
    process_work_item(
        store, TaskAgentRunner(Codex()), store.claim_work_summary_inputs(limit=1)[0]
    )
    (card,) = store.list_business_attention_items()
    assert card.id == old_card_id
    tasks = store.list_business_tasks()
    assert len(tasks) == 2
    assert all(
        task.stage.value == "candidate" and task.business_relevance.value == "relevant"
        for task in tasks
    )
    assert {link.task_id for link in store.list_business_attention_tasks(card.id)} == {
        task.id for task in tasks
    }
    assert all(
        quote["source_ref"] == item.source.ref
        for quote in json.loads(card.assessment_json)["evidence"]
    )
    assert store.list_business_projects() == registered_projects
    with store._connect() as db:
        receipt = json.loads(
            db.execute(
                "select projection_json from task_agent_runs where summary_input_id=?",
                (input_id,),
            ).fetchone()[0]
        )
        link = db.execute(
            "select * from business_task_anchor_links where task_id<>?", (seed.task_id,)
        ).fetchone()
    assert receipt["status"] == "completed"
    assert receipt["project_link_count"] == 2
    assert link["status"] == "confirmed" and link["anchor_id"] == anchor_id
    replay = apply_task_agent_decision(
        store,
        summary_input_id=input_id,
        work_item=item,
        decision=TaskAgentDecision.model_validate(
            {
                "project_decisions": [],
                "project_assessments": [
                    existing_project_assessment(
                        item=item, seed=seed, anchor_id=anchor_id, decision_indexes=[0]
                    )
                ],
                "task_decisions": [new_task],
            }
        ),
        record_run=False,
    )
    assert replay.projection_receipt.status == "completed"
    assert store.list_business_tasks() == tasks
    assert store.list_business_attention_items()[0].id == old_card_id


def test_new_task_relation_and_compound_project_link_use_actual_current_id(tmp_path):
    from app.task_agent import TaskAgentRunner

    store, item, seed, anchor_id, old_card_id, new_task = existing_project_link_case(
        tmp_path
    )
    existing = store.get_business_task(seed.task_id)
    compound = "复核示例交付项目验收计划，并补充验收取消后的供应商延期付款安排。"
    item = item.model_copy(
        update={
            "summary": f"{item.summary.splitlines()[0]}\n{compound}"
        }
    )
    new_task.update(
        title="补充供应商延期付款安排",
        source_excerpt="补充验收取消后的供应商延期付款安排",
        description="补充验收取消后的供应商延期付款安排",
    )
    new_task["project_link_evidence"][0]["source_excerpt"] = compound
    new_task["relation_proposals"] = [
        {
            "related_task_id": seed.task_id,
            "direction": "current_to_related",
            "relation_type": "supports",
            "reason": "付款安排支持既有验收交付",
        }
    ]
    old_update = {
        "action": "update_task",
        "transition": "update_fields",
        "task_id": seed.task_id,
        "title": existing.title,
        "description": existing.description,
        "source_ref": item.source.ref,
        "source_excerpt": "复核示例交付项目验收计划",
    }
    assessment = existing_project_assessment(
        item=item, seed=seed, anchor_id=anchor_id, decision_indexes=[0, 1]
    )
    assessment["task_ids"] = [seed.task_id]
    payload = {
        "project_decisions": [],
        "project_assessments": [assessment],
        "task_decisions": [old_update, new_task],
    }

    class Codex:
        def decide(self, **kwargs):
            return TaskAgentDecision.model_validate(payload)

    input_id = store.enqueue_work_summary_input(
        item.source.type.value, item.source.ref, item.model_dump_json()
    )
    process_work_item(
        store, TaskAgentRunner(Codex()), store.claim_work_summary_inputs(limit=1)[0]
    )
    tasks = store.list_business_tasks()
    assert len(tasks) == 2
    assert store.get_business_task(seed.task_id) == existing
    current = next(task for task in tasks if task.id != seed.task_id)
    (relation,) = store.list_business_task_relations(task_id=current.id)
    assert (relation.from_task_id, relation.to_task_id) == (current.id, seed.task_id)
    assert relation.status.value == "proposed"
    (card,) = store.list_business_attention_items()
    assert card.id == old_card_id and card.anchor_id == anchor_id
    assert {link.task_id for link in store.list_business_attention_tasks(card.id)} == {
        seed.task_id,
        current.id,
    }
    with store._connect() as db:
        receipt = json.loads(
            db.execute(
                "select projection_json from task_agent_runs where summary_input_id=?",
                (input_id,),
            ).fetchone()[0]
        )
    assert receipt["status"] == "completed"


@pytest.mark.parametrize(
    "failure",
    [
        "missing",
        "unofficial",
        "inactive",
        "cross_project",
        "wrong_quote",
        "wrong_task_quote",
        "source_ref",
    ],
)
def test_existing_project_link_rejects_unproven_target_or_current_action(
    tmp_path, failure
):
    from app.task_business_resolution import BusinessResolutionService

    store, item, seed, anchor_id, card_id, payload = existing_project_link_case(
        tmp_path
    )
    resolution = BusinessResolutionService(store)
    target_anchor = anchor_id
    assessment_title = "示例交付项目"
    assessment_task_ids: list[int] = []
    if failure == "missing":
        target_anchor = 99999
    elif failure in {"unofficial", "cross_project"}:
        other = resolution.register_anchor(
            anchor_type="project", anchor_ref="project:other", title="其他交付项目"
        )
        if failure == "cross_project":
            resolution.register_official_project(
                anchor_id=other, registry_source="report:other"
            )
            # The current selector is a real, official Project.  The negative
            # is that the assessed existing Task is confirmed to the first
            # Project, not that the citation text is fabricated.
            assessment_title = "其他交付项目"
            assessment_task_ids = [seed.task_id]
        target_anchor = other
    elif failure == "inactive":
        with store._connect() as db:
            db.execute("update business_anchors set active=0 where id=?", (anchor_id,))
    elif failure == "wrong_quote":
        payload["project_link_evidence"][0]["source_excerpt"] = (
            "示例交付项目不存在于当前来源的关联句。"
        )
    elif failure == "wrong_task_quote":
        payload["project_link_evidence"][0]["source_excerpt"] = item.summary.splitlines()[
            0
        ]
    else:
        payload["source_ref"] = "other:source"
    payload["project"] = {"anchor_id": target_anchor}
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "project_assessments": [
                {
                    "project_title": assessment_title,
                    "outcome": "needs_attention",
                    "reason": "保留被测 Project 链接的当前来源风险。",
                    "assessment_basis": "current_observation",
                    "evidence": [
                        {
                            "source_ref": item.source.ref,
                            "source_excerpt": item.summary.splitlines()[0],
                        }
                    ],
                    "anchor_id": target_anchor,
                    "task_ids": assessment_task_ids,
                    "attention_proposal": existing_project_assessment(
                        item=item, seed=seed, anchor_id=anchor_id, decision_indexes=[0]
                    )["attention_proposal"],
                }
            ],
            "task_decisions": [payload],
        }
    )
    expected_error = (
        "task decision source_ref must match the Work Item source"
        if failure == "source_ref"
        else "official Project"
        if failure in {"missing", "unofficial", "inactive"}
        else "supporting Task is not confirmed to the assessed Project"
        if failure == "cross_project"
        else "current Project quote is absent"
    )
    if failure == "wrong_task_quote":
        result = apply_task_agent_decision(
            store, summary_input_id=1, work_item=item, decision=decision, record_run=False
        )
        # A Project citation and the Task's original source need not be the
        # same sentence.  Both are authentic current-source citations here.
        assert result.project_links == ((2, anchor_id),)
        return
    with pytest.raises(ValueError, match=expected_error):
        apply_task_agent_decision(
            store,
            summary_input_id=1,
            work_item=item,
            decision=decision,
            record_run=False,
        )
    assert len(store.list_business_tasks()) == 1
    assert len(store.list_business_task_signals()) == 1


def test_new_evidence_updates_existing_project_attention_without_task_field_change(
    tmp_path,
):
    store, item, seed, anchor_id, card_id, new_task = existing_project_link_case(
        tmp_path
    )
    task = store.get_business_task(seed.task_id)
    payload = {
        **new_task,
        "action": "update_task",
        "transition": "update_fields",
        "task_id": seed.task_id,
        "title": task.title,
        "description": task.description,
    }
    # This path updates Project attention from new evidence; it deliberately
    # does not re-submit a Task→Project selector or alter the confirmed link.
    payload.pop("project")
    payload.pop("project_link_evidence")
    follow_up_signal_id = store.create_business_task_signal(
        source_type="reply_attempt",
        source_ref="source:pending-follow-up",
        evidence_text="待确认验收结果。",
        dedupe_key="source:pending-follow-up",
        conversation_id="group:delivery",
    )
    store.link_business_task_evidence(
        task_id=seed.task_id,
        signal_id=follow_up_signal_id,
        evidence_role="discovery",
    )
    store.create_business_task_follow_up(
        business_task_id=seed.task_id,
        source_signal_id=follow_up_signal_id,
        target_conversation_id="group:delivery",
        target_kind="group",
        question_text="请确认验收结果。",
        scheduled_at="2026-10-10T12:00:00Z",
        owner_user_id="owner:seed",
        owner_name="负责人",
        dedupe_key="follow-up:seed",
    )
    with store._connect() as db:
        before = tuple(
            db.execute(
                "select evidence_signal_id,reason from business_task_anchor_links"
            ).fetchone()
        )
        followups_before = [
            dict(row) for row in db.execute("select * from business_task_follow_ups")
        ]
        todo_before = [
            dict(row)
            for row in db.execute("select * from business_task_todo_sync_outbox")
        ]
    events_before = store.list_business_task_events(seed.task_id)
    signals_before = store.list_business_task_signals()
    result = apply_task_agent_decision(
        store,
        summary_input_id=1,
        work_item=item,
        decision=TaskAgentDecision.model_validate(
            {
                "project_decisions": [],
                "project_assessments": [
                    existing_project_assessment(
                        item=item, seed=seed, anchor_id=anchor_id, decision_indexes=[0]
                    )
                ],
                "task_decisions": [payload],
            }
        ),
        record_run=False,
    )
    assert result.task_ids == ()
    assert result.project_links == ()
    assert result.projection_receipt.status == "completed"
    assert result.projection_receipt.outcomes[0].status == "applied"
    [assessment] = result.projection_receipt.project_assessments
    assert assessment.status == "applied"
    assert assessment.anchor_id == anchor_id
    assert assessment.task_ids == [seed.task_id]
    assert assessment.attention_id == card_id
    assert result.applied_decisions == ()
    assert len(store.list_business_task_signals()) == len(signals_before) + 1
    signal = store.get_business_task_signal(assessment.evidence[0].signal_id)
    assert signal.source_ref == item.source.ref
    assert assessment.evidence[0].source_excerpt in signal.evidence_text
    assert store.get_business_task(seed.task_id) == task
    assert store.list_business_task_events(seed.task_id) == events_before
    with store._connect() as db:
        assert (
            tuple(
                db.execute(
                    "select evidence_signal_id,reason from business_task_anchor_links"
                ).fetchone()
            )
            == before
        )
        assert [
            dict(row) for row in db.execute("select * from business_task_follow_ups")
        ] == followups_before
        assert [
            dict(row)
            for row in db.execute("select * from business_task_todo_sync_outbox")
        ] == todo_before
    assert store.get_business_attention_item(card_id).title == "验收取消与付款风险"
    assert store.list_business_attention_tasks(card_id)[0].task_id == seed.task_id
    assert (
        json.loads(store.get_business_attention_item(card_id).assessment_json)[
            "evidence"
        ][0]["signal_id"]
        == signal.id
    )
    events_after = store.list_business_attention_events(card_id)
    evidence_after = store.list_business_task_evidence(seed.task_id)

    replay = apply_task_agent_decision(
        store,
        summary_input_id=1,
        work_item=item,
        decision=TaskAgentDecision.model_validate(
            {
                "project_decisions": [],
                "project_assessments": [
                    existing_project_assessment(
                        item=item, seed=seed, anchor_id=anchor_id, decision_indexes=[0]
                    )
                ],
                "task_decisions": [payload],
            }
        ),
        record_run=False,
    )
    assert replay.projection_receipt.status == "completed"
    assert replay.applied_decisions == ()
    without_redundant_link = dict(payload)
    replay_without_link = apply_task_agent_decision(
        store,
        summary_input_id=1,
        work_item=item,
        decision=TaskAgentDecision.model_validate(
            {
                "project_decisions": [],
                "project_assessments": [
                    existing_project_assessment(
                        item=item, seed=seed, anchor_id=anchor_id, decision_indexes=[0]
                    )
                ],
                "task_decisions": [without_redundant_link],
            }
        ),
        record_run=False,
    )
    assert replay_without_link.applied_decisions == ()
    assert store.list_business_task_signals() == (*signals_before, signal)
    assert store.list_business_task_evidence(seed.task_id) == evidence_after
    assert store.list_business_attention_events(card_id) == events_after
    assert store.list_business_attention_tasks(card_id)[0].task_id == seed.task_id
    assert store.get_business_task(seed.task_id) == task
    assert store.list_business_task_events(seed.task_id) == events_before
    with store._connect() as db:
        assert [
            dict(row) for row in db.execute("select * from business_task_follow_ups")
        ] == followups_before
        assert [
            dict(row)
            for row in db.execute("select * from business_task_todo_sync_outbox")
        ] == todo_before


def test_distinct_current_risk_revises_existing_card_without_task_event(tmp_path):
    store, item, seed, anchor_id, card_id, new_task = existing_project_link_case(
        tmp_path
    )
    task_before = store.get_business_task(seed.task_id)
    events_before = store.list_business_task_events(seed.task_id)
    risk = "示例交付项目第二批验收推迟，新的回款节点也可能延后。"
    item = item.model_copy(
        update={
            "source": item.source.model_copy(update={"ref": "source:second-risk"}),
            "summary": f"{risk}\n复核示例交付项目验收计划。",
        }
    )
    proposal = dict(
        existing_project_assessment(
            item=item, seed=seed, anchor_id=anchor_id, decision_indexes=[0]
        )["attention_proposal"]
    )
    proposal.update(
        title="第二批验收与回款风险",
        why_attention="第二批验收推迟影响新的回款节点",
        current_state=risk,
        evidence=[{"source_ref": item.source.ref, "source_excerpt": risk}],
    )
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "project_assessments": [
                {
                    **existing_project_assessment(
                        item=item, seed=seed, anchor_id=anchor_id, decision_indexes=[0]
                    ),
                    "attention_proposal": proposal,
                }
            ],
            "task_decisions": [
                {
                    "action": "update_task",
                    "transition": "update_fields",
                    "task_id": seed.task_id,
                    "title": task_before.title,
                    "description": task_before.description,
                    "source_ref": item.source.ref,
                    "source_excerpt": "复核示例交付项目验收计划",
                }
            ],
        }
    )

    result = apply_task_agent_decision(
        store, summary_input_id=2, work_item=item, decision=decision, record_run=False
    )

    assert result.projection_receipt.status == "completed"
    [assessment] = result.projection_receipt.project_assessments
    assert (
        assessment.status,
        assessment.anchor_id,
        assessment.task_ids,
        assessment.attention_id,
    ) == ("applied", anchor_id, [seed.task_id], card_id)
    assert result.applied_decisions == ()
    card = store.get_business_attention_item(card_id)
    assert card.title == "第二批验收与回款风险"
    assert (
        json.loads(card.assessment_json)["evidence"][0]["source_ref"] == item.source.ref
    )
    assert store.get_business_task(seed.task_id) == task_before
    assert store.list_business_task_events(seed.task_id) == events_before


def test_field_update_then_identical_attention_replay_preserves_evidence_roles(
    tmp_path,
):
    store, item, seed, anchor_id, card_id, new_task = existing_project_link_case(
        tmp_path
    )
    payload = {
        **new_task,
        "action": "update_task",
        "transition": "update_fields",
        "task_id": seed.task_id,
        "title": "复核示例交付项目验收计划",
        "description": "客户取消验收后复核交付和付款安排。",
    }
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "project_assessments": [
                existing_project_assessment(
                    item=item, seed=seed, anchor_id=anchor_id, decision_indexes=[0]
                )
            ],
            "task_decisions": [payload],
        }
    )
    apply_task_agent_decision(
        store, summary_input_id=2, work_item=item, decision=decision, record_run=False
    )
    evidence_before = store.list_business_task_evidence(seed.task_id)
    events_before = store.list_business_task_events(seed.task_id)
    attention_events_before = store.list_business_attention_events(card_id)
    card_before = store.get_business_attention_item(card_id)
    signals_before = store.list_business_task_signals()

    apply_task_agent_decision(
        store, summary_input_id=2, work_item=item, decision=decision, record_run=False
    )

    assert store.list_business_task_signals() == signals_before
    assert store.list_business_task_evidence(seed.task_id) == evidence_before
    assert store.list_business_task_events(seed.task_id) == events_before
    assert store.list_business_attention_events(card_id) == attention_events_before
    assert store.get_business_attention_item(card_id) == card_before


def test_cited_memory_ref_does_not_replace_current_attention_source(tmp_path):
    store, current, seed, anchor_id, card_id, new_task = existing_project_link_case(
        tmp_path
    )
    earlier = current.model_copy(
        update={
            "source": current.source.model_copy(
                update={"ref": "source:earlier-envelope"}
            ),
            "summary": "本轮引用了另一份历史来源。",
        }
    )
    cited = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "project_assessments": [
                {
                    "project_title": "示例交付项目",
                    "anchor_id": anchor_id,
                    "outcome": "not_needed",
                    "reason": "这份来源只补充验收背景。",
                    "assessment_basis": "current_observation",
                    "decision_indexes": [0],
                    "task_ids": [seed.task_id],
                    "evidence": [
                        {
                            "source_ref": earlier.source.ref,
                            "source_excerpt": "本轮引用了另一份历史来源",
                        }
                    ],
                }
            ],
            "task_decisions": [
                {
                    "action": "update_task",
                    "transition": "update_fields",
                    "task_id": seed.task_id,
                    "title": "复核示例交付项目验收计划",
                    "description": "历史来源补充了验收背景。",
                    "evidence_origin": "memory",
                    "source_ref": current.source.ref,
                    "source_excerpt": "历史访谈提及验收背景。",
                    "source_description": "历史访谈纪要中的验收段落",
                }
            ],
        }
    )
    apply_task_agent_decision(
        store, summary_input_id=1, work_item=earlier, decision=cited, record_run=False
    )
    provenance = next(
        signal
        for signal in store.list_business_task_signals()
        if signal.source_type == "memory_provenance"
    )
    stored_task = store.get_business_task(seed.task_id)
    payload = {
        **new_task,
        "action": "update_task",
        "transition": "update_fields",
        "task_id": seed.task_id,
        "title": stored_task.title,
        "description": stored_task.description,
    }
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "project_assessments": [
                existing_project_assessment(
                    item=current, seed=seed, anchor_id=anchor_id, decision_indexes=[0]
                )
            ],
            "task_decisions": [payload],
        }
    )

    first = apply_task_agent_decision(
        store,
        summary_input_id=2,
        work_item=current,
        decision=decision,
        record_run=False,
    )
    assert first.projection_receipt.status == "completed"
    observed_id = first.applied_decisions[0].signal_id
    assert observed_id != provenance.id
    assert store.get_business_task_signal(provenance.id) == provenance
    assert (
        store.get_business_task_signal(observed_id).source_type
        == current.source.type.value
    )
    signals_after = store.list_business_task_signals()
    evidence_after = store.list_business_task_evidence(seed.task_id)
    events_after = store.list_business_task_events(seed.task_id)
    attention_events_after = store.list_business_attention_events(card_id)

    replay = apply_task_agent_decision(
        store,
        summary_input_id=2,
        work_item=current,
        decision=decision,
        record_run=False,
    )
    assert replay.applied_decisions[0].signal_id == observed_id
    assert store.get_business_task_signal(provenance.id) == provenance
    assert store.list_business_task_signals() == signals_after
    assert store.list_business_task_evidence(seed.task_id) == evidence_after
    assert store.list_business_task_events(seed.task_id) == events_after
    assert store.list_business_attention_events(card_id) == attention_events_after

    changed_payload = current.model_copy(
        update={
            "summary": current.summary + "\n同一来源编号却出现不同正文。",
        }
    )
    # Task-effect evidence and Project assessment evidence have separate
    # provenance.  An unrelated appended source line does not rewrite either.
    apply_task_agent_decision(
        store,
        summary_input_id=3,
        work_item=changed_payload,
        decision=decision,
        record_run=False,
    )
    assert store.get_business_task_signal(provenance.id) == provenance
    # The changed immutable source has its own Project proof signal; it does
    # not replace the older memory provenance or Task-effect evidence.
    assert len(store.list_business_task_signals()) == len(signals_after) + 1
    assert len(store.list_business_task_evidence(seed.task_id)) == len(evidence_after) + 1


def test_invalid_evidence_only_attention_quote_does_not_save_signal(tmp_path):
    store, item, seed, anchor_id, card_id, new_task = existing_project_link_case(
        tmp_path
    )
    task = store.get_business_task(seed.task_id)
    before_signals = store.list_business_task_signals()
    before_evidence = store.list_business_task_evidence(seed.task_id)
    proposal = dict(
        existing_project_assessment(
            item=item, seed=seed, anchor_id=anchor_id, decision_indexes=[0]
        )["attention_proposal"]
    )
    proposal["evidence"] = [
        {"source_ref": item.source.ref, "source_excerpt": "来源中不存在的风险原文"}
    ]
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "project_assessments": [
                {
                    **existing_project_assessment(
                        item=item, seed=seed, anchor_id=anchor_id, decision_indexes=[0]
                    ),
                    "attention_proposal": proposal,
                }
            ],
            "task_decisions": [
                {
                    "action": "update_task",
                    "transition": "update_fields",
                    "task_id": seed.task_id,
                    "title": task.title,
                    "description": task.description,
                    "source_ref": item.source.ref,
                    "source_excerpt": "复核示例交付项目验收计划",
                }
            ],
        }
    )

    with pytest.raises(ValueError, match="current Project quote is absent"):
        apply_task_agent_decision(
            store, summary_input_id=2, work_item=item, decision=decision, record_run=False
        )
    assert store.list_business_task_signals() == before_signals
    assert store.list_business_task_evidence(seed.task_id) == before_evidence
    assert store.get_business_attention_item(card_id).title == "原验收风险"


def test_invalid_historical_attention_quote_does_not_save_current_proof(tmp_path):
    store, item, seed, anchor_id, card_id, new_task = existing_project_link_case(
        tmp_path
    )
    task = store.get_business_task(seed.task_id)
    signals_before = store.list_business_task_signals()
    evidence_before = store.list_business_task_evidence(seed.task_id)
    card_before = store.get_business_attention_item(card_id)
    events_before = store.list_business_attention_events(card_id)
    proposal = dict(
        existing_project_assessment(
            item=item, seed=seed, anchor_id=anchor_id, decision_indexes=[0]
        )["attention_proposal"]
    )
    proposal["assessment_basis"] = "historical_comparison"
    proposal["evidence"] = [
        *proposal["evidence"],
        {
            "signal_id": seed.signal_id,
            "source_ref": "report:existing",
            "source_excerpt": "原始来源中不存在的历史风险",
        },
    ]
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "project_assessments": [
                {
                        **existing_project_assessment(
                            item=item, seed=seed, anchor_id=anchor_id, decision_indexes=[0]
                        ),
                        "assessment_basis": "historical_comparison",
                        "evidence": [
                            {
                                "source_ref": item.source.ref,
                                "source_excerpt": item.summary.splitlines()[0],
                            },
                            {
                                "signal_id": seed.signal_id,
                                "source_ref": "report:existing",
                                "source_excerpt": "原始来源中不存在的历史风险",
                            },
                        ],
                        "attention_proposal": proposal,
                }
            ],
            "task_decisions": [
                {
                    "action": "update_task",
                    "transition": "update_fields",
                    "task_id": seed.task_id,
                    "title": task.title,
                    "description": task.description,
                    "source_ref": item.source.ref,
                    "source_excerpt": "复核示例交付项目验收计划",
                }
            ],
        }
    )

    with pytest.raises(ValueError, match="historical Project quote"):
        apply_task_agent_decision(
            store,
            summary_input_id=2,
            work_item=item,
            decision=decision,
            record_run=False,
        )
    assert store.list_business_task_signals() == signals_before
    assert store.list_business_task_evidence(seed.task_id) == evidence_before
    assert store.get_business_attention_item(card_id) == card_before
    assert store.list_business_attention_events(card_id) == events_before


def test_reconfirmed_relevance_with_attention_keeps_task_and_follow_up_unchanged(
    tmp_path,
):
    store, item, seed, anchor_id, card_id, new_task = existing_project_link_case(
        tmp_path
    )
    task = store.get_business_task(seed.task_id)
    assert task.business_relevance.value == "relevant"
    follow_up_signal_id = store.create_business_task_signal(
        source_type="reply_attempt",
        source_ref="source:pending-relevance-follow-up",
        evidence_text="待确认验收结果。",
        dedupe_key="source:pending-relevance-follow-up",
        conversation_id="group:delivery",
    )
    store.link_business_task_evidence(
        task_id=seed.task_id,
        signal_id=follow_up_signal_id,
        evidence_role="discovery",
    )
    store.create_business_task_follow_up(
        business_task_id=seed.task_id,
        source_signal_id=follow_up_signal_id,
        target_conversation_id="group:delivery",
        target_kind="group",
        question_text="请确认验收结果。",
        scheduled_at="2026-10-10T12:00:00Z",
        owner_user_id="owner:seed",
        owner_name="负责人",
        dedupe_key="follow-up:relevance",
    )
    events_before = store.list_business_task_events(seed.task_id)
    with store._connect() as db:
        followups_before = [
            dict(row) for row in db.execute("select * from business_task_follow_ups")
        ]
        todo_before = [
            dict(row)
            for row in db.execute("select * from business_task_todo_sync_outbox")
        ]
    payload = {
        **new_task,
        "action": "update_task",
        "transition": "update_fields",
        "task_id": seed.task_id,
        "title": task.title,
        "description": task.description,
    }
    result = apply_task_agent_decision(
        store,
        summary_input_id=2,
        work_item=item,
        decision=TaskAgentDecision.model_validate(
            {
                "project_decisions": [],
                "project_assessments": [
                    existing_project_assessment(
                        item=item, seed=seed, anchor_id=anchor_id, decision_indexes=[0]
                    )
                ],
                "task_decisions": [payload],
            }
        ),
        record_run=False,
    )

    assert result.projection_receipt.status == "completed"
    assert result.projection_receipt.project_assessments[0].attention_id == card_id
    assert store.get_business_task(seed.task_id) == task
    assert store.list_business_task_events(seed.task_id) == events_before
    with store._connect() as db:
        assert [
            dict(row) for row in db.execute("select * from business_task_follow_ups")
        ] == followups_before
        assert [
            dict(row)
            for row in db.execute("select * from business_task_todo_sync_outbox")
        ] == todo_before


def test_reconfirmed_relevance_without_attention_keeps_ordinary_task_evidence_effect(
    tmp_path,
):
    store, item, seed, anchor_id, _card_id, _new_task = existing_project_link_case(
        tmp_path
    )
    events_before = store.list_business_task_events(seed.task_id)
    assessment = existing_project_assessment(
                            item=item,
                            seed=seed,
                            anchor_id=anchor_id,
                            decision_indexes=[],
        outcome="not_needed",
    )
    result = apply_task_agent_decision(
        store,
        summary_input_id=2,
        work_item=item,
        decision=TaskAgentDecision.model_validate(
            {
                "project_decisions": [],
                "project_assessments": [assessment],
                "task_decisions": [
                    {
                        "action": "update_task",
                        "transition": "update_fields",
                        "task_id": seed.task_id,
                        "title": "复核示例交付项目验收计划",
                        "business_relevance": "relevant",
                        "source_ref": item.source.ref,
                        "source_excerpt": "复核示例交付项目验收计划",
                    }
                ],
            }
        ),
        record_run=False,
    )

    assert result.attention_proposals == ()
    assert len(store.list_business_task_events(seed.task_id)) == len(events_before) + 1


def test_existing_unconfirmed_task_can_confirm_project_link_with_real_source_update(
    tmp_path,
):
    store, item, seed, anchor_id, card_id, new_task = existing_project_link_case(
        tmp_path
    )
    quote = item.summary.splitlines()[1]
    with store._connect() as db:
        db.execute(
            "update business_task_anchor_links set status='proposed' where task_id=?",
            (seed.task_id,),
        )
        db.execute(
            "update business_tasks set business_relevance='unknown' where id=?",
            (seed.task_id,),
        )
    payload = {
        **new_task,
        "action": "update_task",
        "transition": "update_fields",
        "task_id": seed.task_id,
        "title": "复核示例交付项目验收计划",
        "description": "客户取消验收后调整验收计划。",
        "source_excerpt": quote,
        "project": {"anchor_id": anchor_id},
        "project_link_evidence": [
            {"source_ref": item.source.ref, "source_excerpt": quote}
        ],
    }
    result = apply_task_agent_decision(
        store,
        summary_input_id=1,
        work_item=item,
        decision=TaskAgentDecision.model_validate(
            {
                "project_decisions": [],
                "project_assessments": [
                    existing_project_assessment(
                        item=item, seed=seed, anchor_id=anchor_id, decision_indexes=[0]
                    )
                ],
                "task_decisions": [payload],
            }
        ),
        record_run=False,
    )
    assert result.project_links == ((seed.task_id, anchor_id),)
    assert result.projection_receipt.status == "completed"
    assert store.get_business_task(seed.task_id).business_relevance.value == "relevant"
    with store._connect() as db:
        assert (
            db.execute(
                "select status from business_task_anchor_links where task_id=?",
                (seed.task_id,),
            ).fetchone()[0]
            == "confirmed"
        )


def test_uncertain_anchor_match_remains_proposed_without_deriving_relevance(tmp_path):
    store, item, seed, anchor_id, card_id, payload = existing_project_link_case(
        tmp_path
    )
    payload.pop("project")
    payload.pop("project_link_evidence")
    payload["anchor_match_proposals"] = [
        {"anchor_id": anchor_id, "reason": "尚待确认的相关性"}
    ]
    result = apply_task_agent_decision(
        store,
        summary_input_id=1,
        work_item=item,
        decision=TaskAgentDecision.model_validate(
            {
                "project_decisions": [],
                "project_assessments": [
                    existing_project_assessment(
                        item=item,
                        seed=seed,
                        anchor_id=anchor_id,
                        decision_indexes=[],
                        outcome="insufficient_evidence",
                    )
                ],
                "task_decisions": [payload],
            }
        ),
        record_run=False,
    )
    new_id = result.task_ids[0]
    assert result.project_links == ()
    assert store.get_business_task(new_id).business_relevance.value == "unknown"
    with store._connect() as db:
        assert (
            db.execute(
                "select status from business_task_anchor_links where task_id=?",
                (new_id,),
            ).fetchone()[0]
            == "proposed"
        )
    assert {link.task_id for link in store.list_business_attention_tasks(card_id)} == {
        seed.task_id
    }
