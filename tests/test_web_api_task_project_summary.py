from app.project_context_service import ProjectContextService
from app.store import AutoReplyStore
from app.task_business_resolution import BusinessResolutionService
from app.task_semantic_models import FormalTaskBasis, ProjectContext, SourceCitation, TaskSuggestion
from app.task_semantic_rules import FormalityEvidence
from app.task_semantic_service import PromoteCandidate, RecordTaskSuggestion, SourceSignal, TaskSemanticService
from app.web_api.tasks import business_project_detail, business_project_list_response, business_task_detail
import pytest


def _project(store, title="交付项目", name="张三"):
    resolver = BusinessResolutionService(store)
    anchor_id = resolver.register_anchor(anchor_type="project", anchor_ref=f"project:{title}", title=title)
    project_id = resolver.register_official_project(anchor_id=anchor_id, registry_source=f"meeting:{title}")
    text = f"{name}总负责交付与验收，王五负责商务回款。验收排期未确定。"
    signal_id = store.create_business_task_signal(source_type="meeting", source_ref=f"meeting:{title}", evidence_text=text, dedupe_key=f"meeting:{title}")
    citation = SourceCitation(signal_id=signal_id, source_ref=f"meeting:{title}", source_excerpt=text)
    context = ProjectContext(goal="完成交付并回款", scope="一期", overall_owner={
        "person_name": name, "responsibility": "交付与验收", "evidence": [citation],
    }, responsibilities=[{"person_name": "王五", "responsibility": "商务回款", "evidence": [citation]}], facts=[{
        "key": "验收", "text": "验收排期未确定", "evidence": [citation],
    }])
    with store.business_task_transaction() as db:
        ProjectContextService(store).apply(project_id=project_id, context=context, signal_ids=(signal_id,), db=db)
    return project_id, anchor_id, citation, context


def test_zero_task_project_api_reads_saved_accountability_and_facts(tmp_path):
    store = AutoReplyStore(tmp_path / "project.sqlite3")
    project_id, _, citation, context = _project(store)
    before = store.list_business_project_context_revisions(project_id)
    detail = business_project_detail(store, project_id)
    summary = detail.summary
    assert summary.overall_owner == "张三"
    assert summary.overall_responsibility == summary.responsible_content == "交付与验收"
    assert summary.goal == context.goal
    assert summary.current_status == "验收排期未确定"
    assert summary.attention_reason == ""
    assert summary.confirmed_task_count == summary.open_task_count == summary.done_task_count == 0
    assert detail.context == context
    assert detail.responsibilities == context.responsibilities
    assert detail.confirmed_tasks == detail.suggestions == []
    assert [signal.id for signal in detail.evidence_signals] == [citation.signal_id]
    assert detail.context_revisions == list(before)
    assert store.list_business_project_context_revisions(project_id) == before
    result = business_project_list_response(store, page=1, page_size=20)
    assert result.items[0].overall_owner == "张三"


def test_unknown_project_context_does_not_borrow_task_owner(tmp_path):
    store = AutoReplyStore(tmp_path / "unknown.sqlite3")
    resolver = BusinessResolutionService(store)
    anchor = resolver.register_anchor(anchor_type="project", anchor_ref="unknown", title="待明确项目")
    project_id = resolver.register_official_project(anchor_id=anchor, registry_source="meeting:unknown")
    task_id = store.create_business_task(title="执行事项", stage="formal", formal_basis="explicit_assignment", owner_name="执行者")
    signal = store.create_business_task_signal(source_type="project_weekly_report", source_ref="report:old", evidence_text="报告说执行者负责某项任务。", dedupe_key="old-report")
    resolver.confirm_anchor_match(task_id=task_id, anchor_id=anchor, evidence_signal_id=signal)
    detail = business_project_detail(store, project_id)
    assert detail.context is None
    assert detail.summary.overall_owner == detail.summary.overall_responsibility == ""
    assert detail.summary.goal == detail.summary.current_status == ""
    assert detail.context_revisions == detail.evidence_signals == []
    assert detail.confirmed_tasks[0].owner == "执行者"
    assert store.get_business_project_context(project_id) is None


def test_project_suggestion_is_separate_from_actual_owner_and_task_counts(tmp_path):
    store = AutoReplyStore(tmp_path / "suggestion.sqlite3")
    project_id, anchor, citation, _ = _project(store)
    suggestion = TaskSuggestion(reason="按商务分工核实回款", suggested_owner_name="王五", responsibility_evidence=[citation], basis_evidence=[citation])
    result = TaskSemanticService(store).record_suggestion(RecordTaskSuggestion(
        title="确认回款日期", description="核实排期", project_anchor_id=anchor,
        signal=SourceSignal(source_type="message", source_ref="m:risk", evidence_text="付款日期未确定。", dedupe_key="risk"), suggestion=suggestion,
    ))
    detail = business_project_detail(store, project_id)
    assert detail.summary.overall_owner == "张三"
    assert detail.summary.confirmed_task_count == detail.summary.open_task_count == 0
    assert detail.confirmed_tasks == []
    [proposed] = detail.suggestions
    assert proposed.id == result.task_id
    assert proposed.origin == "agent_suggestion" and proposed.stage == "candidate"
    assert proposed.suggested_owner == "王五" and proposed.suggestion_reason == suggestion.reason
    assert proposed.owner == proposed.deadline_at == proposed.deadline_type == ""
    assert proposed.commitment_status == "none"
    assert business_task_detail(store, result.task_id).suggestion == suggestion
    with store._connect() as db:
        assert db.execute("select count(*) from business_task_todo_sync_outbox").fetchone()[0] == 0


def test_two_projects_api_never_mix_accountability(tmp_path):
    store = AutoReplyStore(tmp_path / "separate.sqlite3")
    first, _, _, _ = _project(store, title="一期", name="张三")
    second, _, _, _ = _project(store, title="二期", name="李四")
    assert business_project_detail(store, first).summary.overall_owner == "张三"
    assert business_project_detail(store, second).summary.overall_owner == "李四"
    assert business_project_detail(store, second).evidence_signals[0].source_ref == "meeting:二期"


def test_human_promoted_suggestion_is_an_actual_task_without_losing_origin(tmp_path):
    store = AutoReplyStore(tmp_path / "promoted.sqlite3")
    project_id, anchor, citation, _ = _project(store)
    suggestion = TaskSuggestion(reason="按分工建议核实排期", suggested_owner_name="王五",
                                responsibility_evidence=[citation], basis_evidence=[citation])
    service = TaskSemanticService(store)
    result = service.record_suggestion(RecordTaskSuggestion(
        title="确认回款日期", project_anchor_id=anchor, suggestion=suggestion,
        signal=SourceSignal(source_type="message", source_ref="m:suggestion", evidence_text="排期未定。", dedupe_key="suggestion"),
    ))
    service.promote_candidate(PromoteCandidate(
        task_id=result.task_id,
        signal=SourceSignal(source_type="message", source_ref="m:assignment", evidence_text="李四负责确认回款日期。", dedupe_key="assignment"),
        formality=FormalityEvidence(basis=FormalTaskBasis.EXPLICIT_ASSIGNMENT,
                                   assigner_is_authorized=True, deliverable_is_explicit=True, owner_is_explicit=True),
        owner_name="李四", owner_evidence_json='{"source_ref":"m:assignment","excerpt":"李四负责确认回款日期"}',
    ))
    before = store.get_business_task(result.task_id)
    detail = business_project_detail(store, project_id)
    assert detail.summary.confirmed_task_count == detail.summary.open_task_count == 1
    assert detail.suggestions == []
    [actual] = detail.confirmed_tasks
    assert actual.id == result.task_id and actual.stage == "formal"
    assert actual.origin == "agent_suggestion" and actual.owner == "李四"
    assert actual.suggested_owner == actual.suggestion_reason == ""
    task_detail = business_task_detail(store, result.task_id)
    assert task_detail.summary == actual
    assert task_detail.suggestion == suggestion  # immutable history, not a current assignment
    assert store.get_business_task(result.task_id) == before


def test_project_history_is_bounded_and_current_proof_remains_visible(tmp_path):
    store = AutoReplyStore(tmp_path / "history.sqlite3")
    project_id, _, citation, context = _project(store)
    for number in range(25):
        updated = context.model_copy(update={"scope": f"一期版本{number}"})
        with store.business_task_transaction() as db:
            ProjectContextService(store).apply(project_id=project_id, context=updated, signal_ids=(citation.signal_id,), db=db)
    detail = business_project_detail(store, project_id)
    assert len(detail.context_revisions) == 20
    assert detail.context_revision_meta.total == 26
    assert detail.context_revision_meta.has_more
    assert detail.context.scope == "一期版本24"
    assert [signal.id for signal in detail.evidence_signals] == [citation.signal_id]


@pytest.mark.parametrize("date_type", ["requested_deadline_at", "committed_deadline_at", "external_deadline_at"])
def test_task_summary_labels_only_matching_actual_deadline_evidence(tmp_path, date_type):
    store = AutoReplyStore(tmp_path / "deadline.sqlite3")
    task_id = store.create_business_task(title="交付", stage="candidate", deadline_at="2026-10-09")
    assert business_task_detail(store, task_id).summary.deadline_type == ""
    signal_id = store.create_business_task_signal(source_type="message", source_ref="m:date", evidence_text="日期为2026-10-09。", dedupe_key="date")
    with store.business_task_transaction() as db:
        store.create_business_task_date_evidence_in_transaction(task_id=task_id, source_signal_id=signal_id,
            date_type=date_type, value_at="2026-10-09", raw_phrase="2026-10-09", actor_kind="human", _db=db)
    summary = business_task_detail(store, task_id).summary
    assert summary.deadline_at == "2026-10-09" and summary.deadline_type == date_type
    assert store.get_business_task(task_id).deadline_at == "2026-10-09"
