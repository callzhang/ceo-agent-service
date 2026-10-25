"""Evaluation readback follows persisted Project evidence, not Task carriers."""

import importlib.util
import json
from pathlib import Path

import pytest

from app.action_business_definitions import business_action_projection
from app.action_business_storage import delete_business_action
from app.business_source_reader import (
    AvailableSource, SourceUnavailableReason, UnavailableSource,
)
from app.project_context_service import ProjectContextService
from app.store import AutoReplyStore, WORK_SUMMARY_INPUT_SQL
from app.task_attention_projection import AttentionProposal, BusinessAttentionProjection
from app.task_business_resolution import BusinessResolutionService
from app.task_semantic_models import AttentionCategory
from app.task_models import TaskAgentDecision
from app.task_source_documents import source_contains_quote
from tests.support.task_native import record_task_fixture_run, task_native_records  # noqa: F401


@pytest.fixture(autouse=True)
def synthetic_exact_source_reader(monkeypatch):
    """Expose only synthetic bodies emitted by this test, bound to their exact hash."""
    records = {}
    create_signal = AutoReplyStore.create_business_task_signal
    business_source_reader = AutoReplyStore.business_source_reader

    def capture_signal(store, **values):
        signal_id = create_signal(store, **values)
        fact = store.get_business_task_signal(signal_id)
        records.setdefault(store, {})[fact.source.identity_key] = (
            fact.source, values["evidence_text"],
        )
        store._synthetic_source_records = records[store]
        return signal_id

    class SyntheticReader:
        def __init__(self, store):
            self.store = store

        def resolve(self, binding, *, source_ref=None, quote=None):
            record = records.get(self.store, {}).get(binding.identity_key)
            if record is None:
                return UnavailableSource(binding, SourceUnavailableReason.SOURCE_MISSING)
            saved_binding, body = record
            if saved_binding != binding or not binding.matches_body(body):
                return UnavailableSource(binding, SourceUnavailableReason.BINDING_CONFLICT)
            if source_ref is not None and source_ref != binding.source_ref:
                return UnavailableSource(binding, SourceUnavailableReason.SOURCE_REF_MISMATCH)
            if quote is not None and not source_contains_quote(body, quote):
                return UnavailableSource(binding, SourceUnavailableReason.QUOTE_MISSING)
            return AvailableSource(binding, body)

    monkeypatch.setattr(AutoReplyStore, "create_business_task_signal", capture_signal)
    monkeypatch.setattr(
        AutoReplyStore, "business_source_reader",
        lambda store, **_: SyntheticReader(store),
    )

    # A small number of replay tests need the production request reader so they
    # prove that only explicitly prepared originals are available.
    return lambda: monkeypatch.setattr(
        AutoReplyStore, "business_source_reader", business_source_reader,
    )


def _delete_project_evidence(store, project_id):
    with store.business_task_transaction() as db:
        rows = db.execute(
            "select _source_rowid from (" + business_action_projection(
                "business_project_evidence", include_rowid=True,
            ) + ") where project_id=?", (project_id,),
        ).fetchall()
        for (source_rowid,) in rows:
            delete_business_action(db, "business_project_evidence", source_rowid)


_TASK_FIELDS = frozenset({"status"})


def _set_work_input_fields(db, task_id, values):
    for table, key, fields in (
        ("tasks", "id", {k: v for k, v in values.items() if k in _TASK_FIELDS}),
        ("work_summary_inputs", "task_id", {k: v for k, v in values.items() if k not in _TASK_FIELDS}),
    ):
        if not fields:
            continue
        cursor = db.execute(
            f"update {table} set " + ",".join(f"{name}=?" for name in fields)
            + f" where {key}=?", (*fields.values(), task_id),
        )
        assert cursor.rowcount == 1


NO_PROJECT_DECISION = json.dumps({
    "project_decisions": [], "task_decisions": [], "project_assessments": [],
    "update_summary": "No relevant Project in this fixture.",
})


def _assessment(*, proposal=False, anchor_id=1, evidence=None):
    evidence = evidence or [{"signal_id": None, "source_ref": "message:readback",
                             "source_excerpt": "验收日期未确定，影响本期回款", "source_time": "", "source_link": ""}]
    current = [{"signal_id": None, "source_ref": item["source_ref"],
                "source_excerpt": item["source_excerpt"]} for item in evidence]
    result = {
        "project_title": "交付项目", "anchor_id": anchor_id,
        "outcome": "needs_attention" if proposal else "not_needed",
        "reason": "验收排期影响回款" if proposal else "已有人推进，当前不需关注",
        "assessment_basis": "current_observation", "evidence": current,
        "attention_proposal": None,
    }
    if proposal:
        result["attention_proposal"] = {
            "assessment_basis": "current_observation", "category": "watch",
            "title": "交付项目", "why_attention": "本期回款有风险",
            "current_state": "验收日期未确定", "ceo_action": "关注验收排期",
            "material_trigger": "threatened_commitment", "evidence": current,
        }
    TaskAgentDecision.model_validate({"project_decisions": [], "task_decisions": [],
                                     "project_assessments": [result]})
    return result


def _tool():
    path = Path(__file__).parents[1] / "scripts/replay_task_attention.py"
    spec = importlib.util.spec_from_file_location("project_evaluation", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _project(store):
    resolution = BusinessResolutionService(store)
    anchor_id = resolution.register_anchor(
        anchor_type="project", anchor_ref="project:readback", title="交付项目",
    )
    project_id = resolution.register_official_project(
        anchor_id=anchor_id, registry_source="meeting:readback",
    )
    signal_id = store.create_business_task_signal(
        source_type="message", source_ref="message:readback",
        evidence_text="验收日期未确定，影响本期回款。", dedupe_key="readback:source",
    )
    with store.business_task_transaction() as db:
        ProjectContextService(store).apply(
            project_id=project_id, context=None, signal_ids=(signal_id,), db=db,
        )
    citation = {
        "signal_id": signal_id, "source_ref": "message:readback",
        "source_excerpt": "验收日期未确定，影响本期回款", "source_time": "",
        "source_link": "",
    }
    return project_id, anchor_id, signal_id, citation


@pytest.mark.parametrize("remove_project_proof,primary_missing", [(False, False), (True, False), (False, True)])
def test_readback_zero_task_attention_requires_actual_project_proof(tmp_path, remove_project_proof, primary_missing):
    store = AutoReplyStore(tmp_path / "project.sqlite3")
    project_id, anchor_id, signal_id, citation = _project(store)
    BusinessAttentionProjection(store).upsert(AttentionProposal(
        stable_key=f"project:{anchor_id}", category=AttentionCategory.WATCH,
        title="交付项目", business_area="交付", why_attention="本期回款有风险",
        current_state="验收日期未确定", ceo_action="关注验收排期",
        anchor_id=anchor_id, task_ids=(), evidence_signal_id=signal_id,
        assessment_json=json.dumps({"evidence": [citation]}),
    ))
    if remove_project_proof:
        _delete_project_evidence(store, project_id)
    if primary_missing:
        other_id = store.create_business_task_signal(source_type="message", source_ref="m:other", evidence_text="同项目另一资料。", dedupe_key="other")
        with store.business_task_transaction() as db:
            ProjectContextService(store).apply(project_id=project_id, context=None, signal_ids=(other_id,), db=db)
            card = store.get_business_attention_item_in_transaction(
                item_id=store.list_business_attention_items()[0].id, _db=db,
            )
            store.update_business_attention_item_in_transaction(
                item=card.model_copy(update={"evidence_signal_id": other_id}), _db=db,
            )
    tool = _tool()
    result = tool.readback(store, input_id=None, before=tool.read_domain(store))
    assert result["cards"][0]["task_ids"] == []
    assert result["evidence_valid"] == (not remove_project_proof and not primary_missing)
    assert result["passed"] == (not remove_project_proof and not primary_missing)


def test_readback_requires_original_source_body_for_attention_evidence(tmp_path):
    store = AutoReplyStore(tmp_path / "missing-original.sqlite3")
    _, anchor_id, signal_id, citation = _project(store)
    BusinessAttentionProjection(store).upsert(AttentionProposal(
        stable_key=f"project:{anchor_id}", category=AttentionCategory.WATCH,
        title="交付项目", business_area="交付", why_attention="本期回款有风险",
        current_state="验收日期未确定", ceo_action="关注验收排期",
        anchor_id=anchor_id, task_ids=(), evidence_signal_id=signal_id,
        assessment_json=json.dumps({"evidence": [citation]}),
    ))
    fact = store.get_business_task_signal(signal_id)
    store._synthetic_source_records.pop(fact.source.identity_key)
    tool = _tool()
    result = tool.readback(store, input_id=None, before=tool.read_domain(store))
    assert result["evidence_valid"] is False
    assert "unverifiable_attention_evidence" in result["failures"]


def test_domain_snapshot_includes_project_evidence_relations(tmp_path):
    store = AutoReplyStore(tmp_path / "snapshot-actions.sqlite3")
    project_id, _, _, _ = _project(store)
    tool = _tool()
    before = tool.domain_snapshot(store)

    _delete_project_evidence(store, project_id)

    after = tool.domain_snapshot(store)
    assert "business_project_evidence" in before
    assert before["business_project_evidence"]
    assert after["business_project_evidence"] == ()
    assert before != after


@pytest.mark.parametrize("save_receipt", [False, True])
def test_readback_counts_project_owned_proposal_and_rejects_no_proposal_receipt(tmp_path, save_receipt):
    store = AutoReplyStore(tmp_path / "proposal.sqlite3")
    input_id = store.enqueue_work_summary_input("reply_attempt", "m:proposal", "{}")
    run_id = record_task_fixture_run(store, input_id, decision_json=json.dumps({"project_decisions": [], "task_decisions": [], "project_assessments": [_assessment(proposal=True)]}))
    if save_receipt:
        store.record_task_agent_projection(run_id, json.dumps({
            "status": "no_proposal", "outcomes": [], "recompute_error": "",
            "source_type": "reply_attempt", "task_decision_count": 0,
            "project_link_count": 0, "proposal_count": 0,
        }))
    tool = _tool()
    result = tool.readback(store, input_id=input_id, before=tool.read_domain(store))
    assert result["proposal_count"] == 1
    assert "projection_not_successful" in result["failures"]


@pytest.mark.parametrize("remove_project_proof", [False, True])
def test_readback_not_needed_receipt_uses_project_proof_without_task_or_card(tmp_path, remove_project_proof):
    store = AutoReplyStore(tmp_path / "receipt.sqlite3")
    project_id, anchor_id, _, citation = _project(store)
    input_id = store.enqueue_work_summary_input("reply_attempt", "m:receipt", json.dumps({
        "source": {"ref": citation["source_ref"]},
        "summary": citation["source_excerpt"],
    }))
    assessment = _assessment(anchor_id=anchor_id, evidence=[citation])
    assessment["assessment_basis"] = "historical_comparison"
    assessment["evidence"].append({key: citation[key] for key in
                                   ("signal_id", "source_ref", "source_excerpt")})
    TaskAgentDecision.model_validate({"project_decisions": [], "task_decisions": [],
                                     "project_assessments": [assessment]})
    run_id = record_task_fixture_run(store, input_id, decision_json=json.dumps({
        "project_decisions": [], "task_decisions": [], "project_assessments": [assessment]}))
    receipt = {
        "assessment_index": 0, "status": "recorded", "task_ids": [],
        "anchor_id": anchor_id, "attention_id": None, "evidence": [citation],
    }
    store.record_task_agent_projection(run_id, json.dumps({
        "status": "no_proposal", "outcomes": [], "recompute_error": "",
        "source_type": "reply_attempt", "task_decision_count": 0,
        "project_link_count": 0, "proposal_count": 0,
        "project_assessments": [receipt],
    }))
    expected = {
        "attention_projects": [], "project_titles": ["交付项目"], "task_count": 0,
        "minimum_evidence_sources": 1, "project_assessments": [{
            "project_title": "交付项目", "outcome": "not_needed", "evidence": [citation],
            "application_status": "recorded", "task_count": 0,
            "attention_required": False,
        }],
    }
    tool = _tool()
    if remove_project_proof:
        _delete_project_evidence(store, project_id)
    result = tool.readback(store, input_id=input_id, before=tool.read_domain(store), expected=expected)
    assert result["passed"] is not remove_project_proof, result["failures"]


@pytest.mark.parametrize("outcomes", [[], [{"assessment_index": 0, "anchor_id": 1, "attention_id": 999,
                                          "task_id": None, "status": "applied"}]])
def test_completed_receipt_does_not_prove_a_missing_attention_result(tmp_path, outcomes):
    store = AutoReplyStore(tmp_path / "missing-card.sqlite3")
    input_id = store.enqueue_work_summary_input("reply_attempt", "m:missing-card", "{}")
    run_id = record_task_fixture_run(store, input_id, decision_json=json.dumps({"project_decisions": [], "task_decisions": [], "project_assessments": [_assessment(proposal=True)]}))
    store.record_task_agent_projection(run_id, json.dumps({
        "status": "completed", "proposal_count": 1, "applied_count": len(outcomes),
        "outcomes": outcomes, "recompute_error": "", "project_assessments": [],
        "source_type": "reply_attempt", "task_decision_count": 0, "project_link_count": 0,
    }))
    tool = _tool()
    result = tool.readback(store, input_id=input_id, before=tool.read_domain(store))
    assert "projection_not_successful" in result["failures"]


def test_current_negative_assessment_requires_a_saved_receipt_without_oracle_labels(tmp_path):
    store = AutoReplyStore(tmp_path / "missing-assessment.sqlite3")
    input_id = store.enqueue_work_summary_input("reply_attempt", "m:no-receipt", "{}")
    record_task_fixture_run(store, input_id, decision_json=json.dumps({"project_decisions": [], "task_decisions": [], "project_assessments": [_assessment(proposal=False)]}))
    tool = _tool()
    result = tool.readback(store, input_id=input_id, before=tool.read_domain(store))
    assert "project_assessment_receipt_mismatch" in result["failures"]


def test_seed_independent_project_context_and_old_card_with_no_task(tmp_path):
    store = AutoReplyStore(tmp_path / "seed-project.sqlite3")
    case = {
        "existing_context": {"projects": [{
            "ref": "project:seed", "title": "交付项目", "registry_source": "meeting:seed",
            "evidence": [{"source_type": "meeting", "source_ref": "m:seed",
                          "evidence_text": "张三总负责交付；付款日期未定。", "dedupe_key": "seed"}],
            "context": {"goal": "完成交付", "scope": "一期", "overall_owner": {
                "person_name": "张三", "responsibility": "交付", "evidence": [{
                    "signal_id": None, "source_ref": "m:seed", "source_excerpt": "张三总负责交付",
                }],
            }, "responsibilities": [], "facts": []},
        }], "tasks": [], "attention": [{
            "project_title": "交付项目", "task_titles": [], "title": "回款关注",
            "why_attention": "影响现金安排", "current_state": "付款日期未定", "ceo_action": "观察",
            "assessment": {"evidence_source_ref": "m:seed", "source_ref": "m:seed",
                           "source_excerpt": "付款日期未定", "source_time": "", "source_link": "",
                           "assessment_basis": "current_observation", "material_trigger": "material_change"},
        }]},
        "work_item": {"source": {"type": "reply_attempt", "ref": "m:now"}, "context": {"source_conversation_kind": "group"}, "summary": "付款仍未确定。"},
    }
    tool = _tool()
    input_id = tool.seed_case(store, case)
    project = store.list_business_projects()[0]
    assert store.get_business_project_context(project.id).overall_owner.person_name == "张三"
    assert store.list_business_tasks() == ()
    [card] = store.list_business_attention_items()
    assert store.list_business_attention_tasks(card.id) == ()
    assert tool.readback(store, input_id=None, before=tool.read_domain(store))["evidence_valid"]
    assert store.get_work_summary_input(input_id).source_ref == "m:now"


def test_seeded_task_source_body_is_prepared_from_exact_input(tmp_path, synthetic_exact_source_reader):
    from app.business_source_reader import AvailableSource, PreparedSources

    synthetic_exact_source_reader()
    store = AutoReplyStore(tmp_path / "seed-task-source.sqlite3")
    tool = _tool()
    body = "客户确认一期验收日期仍待排定。"
    case = {
        "existing_context": {
            "projects": [],
            "tasks": [{
                "title": "确认一期验收日期", "description": "跟进客户排期。",
                "signal": {
                    "source_type": "message", "source_ref": "m:seed-task",
                    "evidence_text": body, "dedupe_key": "seed-task-source",
                },
            }],
            "attention": [],
        },
        "work_item": {
            "source": {"type": "reply_attempt", "ref": "m:seed-task-current"},
            "context": {"source_conversation_kind": "group"},
            "summary": "读取新一期的当前输入。",
        },
    }
    prepared = PreparedSources(store.business_source_reader())

    tool.seed_case(store, case, prepared_sources=prepared)

    [fact] = prepared.facts.values()
    reading = prepared.reading_for(fact)
    assert isinstance(reading, AvailableSource)
    assert reading.body == body
    # The persisted Signal has no locator; a new reader cannot reconstruct it.
    assert not isinstance(store.business_source_reader().resolve(fact.source), AvailableSource)


@pytest.mark.parametrize("missing_capabilities", [False, True])
def test_case_replay_enqueues_each_source_version_only_when_its_turn_begins(
    tmp_path, monkeypatch, missing_capabilities, synthetic_exact_source_reader,
):
    from app.task_models import TaskAgentDecision

    store = AutoReplyStore(tmp_path / "sequence.sqlite3")
    synthetic_exact_source_reader()
    first = {"source": {"type": "ai_minutes", "ref": "m:same", "created_at": "2026-10-01T10:00:00Z"},
             "context": {"source_conversation_kind": "group"}, "summary": "会议确定交付项目，目标完成一期验收，目前按计划推进。"}
    second = {"source": {"type": "ai_minutes", "ref": "m:same", "created_at": "2026-10-02T10:00:00Z"},
              "context": {"source_conversation_kind": "group"}, "summary": "会议确定交付项目，目标完成一期验收，材料已经收齐。"}
    unrelated_id = store.enqueue_work_summary_input("ai_minutes", "m:unrelated", "{}")
    seen = []

    class Runner:
        codex = None

        def decide(self, item, *args, **kwargs):
            seen.append(item.summary)
            projects = store.list_business_projects()
            identity = {"anchor_id": projects[0].canonical_anchor_id} if projects else {"registration": {
                    "title": "交付项目", "authority": "meeting_decision", "source_excerpt": "会议确定交付项目", "reason": "会议明确项目目标",
            }}
            quote = {"source_ref": item.source.ref, "source_excerpt": item.summary}
            decision = TaskAgentDecision.model_validate({
                "project_decisions": [{**identity, "context": {"goal": "完成一期验收", "scope": "一期",
                    "overall_owner": None, "responsibilities": [], "facts": [{"key": "进展", "text": item.summary, "evidence": [quote]}]},
                    "evidence": [quote], "reason": "保存来源中的项目进展"}],
                "task_decisions": [], "project_assessments": [{"project_decision_index": 0, "project_title": "交付项目",
                    "outcome": "not_needed", "reason": "当前按计划推进", "assessment_basis": "current_observation", "evidence": [quote]}],
                "update_summary": "保存明确项目进展，没有新增任务",
            })
            with store._connect() as db:
                [run_id] = [row[0] for row in db.execute(
                    "select r.id from runs r join run_provenance p on p.run_id=r.id "
                    "where p.source_type='task_agent_runs' and r.status='running'")]
            store._write_test_native_decision(run_id, decision.model_dump_json())
            return decision

    case = {"source_inputs": [first, second], "work_item": second,
            "existing_context": {"projects": [], "tasks": [], "attention": []},
            "expected": {"project_titles": ["交付项目"], "attention_projects": [], "task_count": 0,
                         "minimum_evidence_sources": 1, "minimum_signal_versions_for_ref": {"m:same": 2}}}
    tool = _tool()
    if missing_capabilities:
        monkeypatch.setattr(tool, "comparison_capabilities", lambda _: {"missing_tables": ["provenance"]})
    result = tool.replay_case(store, Runner(), case)
    assert seen == [first["summary"], second["summary"]], [(step.get("execution_error"), step["failures"]) for step in result["source_steps"]]
    assert result["passed"] is (not missing_capabilities), result["failures"]
    assert "source_sequence_incomplete" not in result["failures"]
    if missing_capabilities:
        assert "project_centered_storage_missing" in result["failures"]
    assert len(result["source_steps"]) == 2
    with store._connect() as db:
        assert unrelated_id not in {step["input_id"] for step in result["source_steps"]}
        assert db.execute(
            f"select payload_json,status from ({WORK_SUMMARY_INPUT_SQL}) where source_ref='m:unrelated'"
        ).fetchone()[:] == ("{}", "pending")
    assert [step["source_time"] for step in result["source_steps"]] == [first["source"]["created_at"], second["source"]["created_at"]]
    assert len(store.list_business_project_context_revisions(store.list_business_projects()[0].id)) == 2


def test_scoped_runner_records_delivered_metrics_and_ranges_without_source_body():
    class Base:
        def __init__(self, codex):
            self.codex = codex

        def decide(self, *args, **kwargs):
            assert kwargs["session_scope_id"] == _tool().EVALUATION_SCOPE
            return "decision"

    runner = _tool().scoped_runner(Base, None)
    context = {"source_metrics": {"signal_count": 3, "document_count": 1, "unique_body_chars": 9000, "visible_body_chars": 2048},
               "source_documents": [{"document_id": 7, "full_length": 9000, "truncated": True,
                                     "visible_ranges": [{"start": 0, "end": 2048, "text": "private-original-text"}],
                                     "decoded_excerpts": []}]}
    assert runner.decide(None, json.dumps(context) + "\nCorrection after rejected decision") == "decision"
    [delivery] = runner.context_deliveries
    assert delivery["source_metrics"] == context["source_metrics"]
    assert delivery["source_documents"][0]["visible_ranges"] == [{"start": 0, "end": 2048}]
    assert "private-original-text" not in json.dumps(delivery)

    runner.decide(None, "{}")
    assert runner.context_deliveries[-1]["source_metrics"] is None  # old absent metrics are not reconstructed


def test_scoped_runner_records_a_source_left_out_of_the_input():
    class Base:
        def __init__(self, codex):
            self.codex = codex

        def decide(self, *args, **kwargs):
            return "decision"

    runner = _tool().scoped_runner(Base, None)
    context = {"source_documents": [
        {"document_id": 8, "source_in_input": False, "body_utf8_bytes": 9000,
         "saved_quotations": ["private-original-text"]},
    ]}

    assert runner.decide(None, json.dumps(context)) == "decision"
    [delivery] = runner.context_deliveries
    assert delivery["source_documents"] == [
        {"document_id": 8, "source_in_input": False,
         "visible_ranges": [], "decoded_excerpts": []},
    ]
    assert "private-original-text" not in json.dumps(delivery)


def test_baseline_missing_project_storage_is_an_explicit_comparison_failure(tmp_path):
    path = tmp_path / "missing-provenance.sqlite3"
    store = AutoReplyStore(path)
    input_id = store.enqueue_work_summary_input("reply_attempt", "m:missing-provenance", "{}")
    projection = json.dumps({
        "status": "no_proposal", "outcomes": [], "recompute_error": "",
        "source_type": "reply_attempt", "task_decision_count": 0,
        "project_link_count": 0, "proposal_count": 0,
    })
    run_ids = [record_task_fixture_run(store, input_id, decision_json=NO_PROJECT_DECISION) for _ in range(2)]
    latest_run_id = max(run_ids)
    for run_id in run_ids:
        store.record_task_agent_projection(run_id, projection)
    tool = _tool()
    before_domain = tool.read_domain(store)
    with store._connect() as db:
        db.execute("drop table provenance")
    before = path.read_bytes()
    result = tool.readback(store, input_id=input_id, before=before_domain)
    assert result["run_id"] == latest_run_id
    assert "project_centered_storage_missing" in result["failures"]
    assert result["comparison_capabilities"]["missing_tables"] == ["provenance"]
    assert not result["passed"]
    assert path.read_bytes() == before


@pytest.mark.parametrize("stored_as_blob", [False, True])
def test_replay_exact_cas_accepts_actual_plain_or_compressed_storage_and_keeps_codec(
    tmp_path, stored_as_blob
):
    from app.task_models import WorkItem
    from app.work_summary_payload import (
        decode_work_summary_payload,
        encode_work_summary_payload,
    )

    store = AutoReplyStore(tmp_path / "codec-replay.sqlite3")
    item = WorkItem.model_validate(
        {
            "source": {"type": "ai_minutes", "ref": "m:codec-replay"},
            "context": {"source_conversation_kind": "group"},
            "summary": "Storage reader fixture. " * 200,
        }
    )
    payload = item.model_dump_json()
    encoded = encode_work_summary_payload(payload)
    assert isinstance(encoded, bytes)
    input_id = store.enqueue_work_summary_input(
        item.source.type.value, item.source.ref, payload
    )
    with store._connect() as db:
        _set_work_input_fields(db, input_id, {"payload_json": encoded if stored_as_blob else payload})
    unrelated_id = store.enqueue_work_summary_input("ai_minutes", "m:unrelated", "{}")
    assert unrelated_id != input_id
    observed = []

    class Runner:
        codex = None

        def decide(self, current, *args, **kwargs):
            with store._connect() as db:
                raw, status = db.execute(
                    f"select payload_json,status from ({WORK_SUMMARY_INPUT_SQL}) where id=?",
                    (input_id,),
                ).fetchone()
            assert isinstance(raw, bytes)
            assert decode_work_summary_payload(raw) == payload
            assert status == "processing"
            observed.append(current.summary)
            return TaskAgentDecision(
                project_decisions=[],
                task_decisions=[],
                project_assessments=[],
                update_summary="This storage fixture contains no business Project.",
            )

    result = _tool().replay_input(
        store, Runner(), input_id, source_ref=item.source.ref, source_payload=payload
    )
    assert "execution_error" not in result, result.get("execution_error")
    assert observed == [item.summary]
    with store._connect() as db:
        assert db.execute(
            f"select source_ref,payload_json,status from ({WORK_SUMMARY_INPUT_SQL}) where id=?",
            (unrelated_id,),
        ).fetchone()[:] == ("m:unrelated", "{}", "pending")


@pytest.mark.parametrize("field,value", [
    ("source_type", "reply_attempt"), ("source_ref", "m:changed"),
    ("status", "failed"), ("body_compacted", 1),
    ("body_sha256", "changed-digest"), ("body_bytes", 999),
    ("payload_json", "{}"),
])
def test_replay_exact_cas_rejects_each_changed_source_field(tmp_path, monkeypatch, field, value):
    from app.task_agent_session import TaskAgentSessionLease
    from app.task_models import WorkItem

    store = AutoReplyStore(tmp_path / "cas-replay.sqlite3")
    item = WorkItem.model_validate({
        "source": {"type": "ai_minutes", "ref": "m:cas"},
        "context": {"source_conversation_kind": "group"},
        "summary": "Exact source version for local CAS validation.",
    })
    payload = item.model_dump_json()
    input_id = store.enqueue_work_summary_input(item.source.type.value, item.source.ref, payload)
    input_row = f"select * from ({WORK_SUMMARY_INPUT_SQL}) where id=?"
    acquire = TaskAgentSessionLease.try_acquire
    snapshots = []

    def change_after_snapshot(current_store):
        lease = acquire(current_store)
        assert lease is not None
        with store._connect() as db:
            _set_work_input_fields(db, input_id, {field: value})
            snapshots.append(tuple(db.execute(input_row, (input_id,)).fetchone()))
        return lease

    monkeypatch.setattr(TaskAgentSessionLease, "try_acquire", staticmethod(change_after_snapshot))

    class Runner:
        def decide(self, *args, **kwargs):
            pytest.fail("CAS failure must occur before runner execution")

    with pytest.raises(ValueError, match="fixed source version changed before replay"):
        _tool().replay_input(store, Runner(), input_id, source_ref=item.source.ref, source_payload=payload)
    with store._connect() as db:
        assert tuple(db.execute(input_row, (input_id,)).fetchone()) == snapshots[0]
    lease = acquire(store)
    assert lease is not None
    lease.close()


def test_cli_selects_code_root_before_importing_application(tmp_path):
    import os
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[1]
    foreign = tmp_path / "foreign"
    (foreign / "app").mkdir(parents=True)
    (foreign / "app" / "__init__.py").write_text(
        "raise RuntimeError('foreign application imported')\n", encoding="utf-8",
    )
    database = tmp_path / "unopened.sqlite3"
    result = subprocess.run(
        [sys.executable, str(root / "scripts/replay_task_attention.py"),
         "--code-root", str(root), "--db", str(database), "--input-id", "1", "--help"],
        cwd=tmp_path, env=os.environ | {"PYTHONPATH": str(foreign)},
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout
    assert not database.exists()
