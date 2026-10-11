from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from app.project_context_service import ProjectContextService, context_changed
from app.store import AutoReplyStore
from app.task_business_resolution import BusinessResolutionService
from app.task_semantic_models import BusinessAnchorType, ProjectContext, TaskSuggestion


def citation(signal_id: int, *, excerpt: str = "张三负责交付") -> dict[str, object]:
    return {"signal_id": signal_id, "source_ref": "meeting:project-1", "source_excerpt": excerpt}


def context(signal_id: int, *, owner: str = "张三", roles: list[dict[str, object]] | None = None) -> ProjectContext:
    proof = [citation(signal_id, excerpt=f"{owner}负责交付")]
    return ProjectContext(
        goal="完成一期交付",
        scope="一期",
        overall_owner={"person_name": owner, "responsibility": "交付与验收", "evidence": proof},
        responsibilities=roles if roles is not None else [
            {"person_name": "李四", "responsibility": "材料准备", "evidence": proof},
            {"person_name": "王五", "responsibility": "客户沟通", "evidence": proof},
        ],
        facts=[],
    )


@pytest.fixture
def project_store(tmp_path):
    store = AutoReplyStore(tmp_path / "project-context.sqlite3")
    resolver = BusinessResolutionService(store)
    anchor_id = resolver.register_anchor(
        anchor_type=BusinessAnchorType.PROJECT,
        anchor_ref="registry:project-context",
        title="项目上下文",
    )
    project_id = resolver.register_official_project(
        anchor_id=anchor_id, registry_source="portfolio-registry"
    )
    first_signal = store.create_business_task_signal(
        source_type="meeting", source_ref="meeting:project-1",
        evidence_text="张三负责交付，李四负责材料，王五负责客户沟通。",
        dedupe_key="project-context:1",
    )
    second_signal = store.create_business_task_signal(
        source_type="meeting", source_ref="meeting:project-1",
        evidence_text="赵六负责交付，李四负责材料，王五负责客户沟通。",
        dedupe_key="project-context:2",
    )
    return store, project_id, first_signal, second_signal


def test_project_context_has_single_owner_roles_and_unknown_owner_fact():
    proof = [citation(1)]
    owner = {"person_name": "张三", "responsibility": "交付与验收", "evidence": proof}
    value = ProjectContext(
        goal="交付", scope="一期", overall_owner=owner,
        responsibilities=[{"person_name": "李四", "responsibility": "材料", "evidence": proof}],
        facts=[{"key": "owner-conflict", "text": "负责人尚有冲突", "evidence": proof}],
    )
    assert value.overall_owner is not None and value.overall_owner.person_name == "张三"
    assert ProjectContext(goal="交付", scope="一期", overall_owner=None, responsibilities=[], facts=value.facts).overall_owner is None
    with pytest.raises(ValidationError):
        ProjectContext(goal="交付", scope="一期", overall_owner=[owner, owner], responsibilities=[], facts=[])


def test_project_fact_requires_both_date_fields_or_neither():
    proof = [citation(1)]
    for date_type, date_value in (("target", ""), ("", "2026-10-10")):
        with pytest.raises(ValidationError):
            ProjectContext(goal="交付", scope="一期", overall_owner=None, responsibilities=[], facts=[
                {"key": "date", "text": "日期", "evidence": proof, "date_type": date_type, "date_value": date_value}
            ])


def test_task_suggestion_requires_responsibility_proof_only_for_named_person():
    proof = [citation(1)]
    TaskSuggestion(reason="适合负责材料", basis_evidence=proof)
    with pytest.raises(ValidationError):
        TaskSuggestion(reason="适合负责材料", suggested_owner_name="李四", basis_evidence=proof)
    assert TaskSuggestion(
        reason="适合负责材料", suggested_owner_name="李四",
        responsibility_evidence=proof, basis_evidence=proof,
    ).suggested_owner_name == "李四"


def test_context_service_persists_zero_task_project_history_and_sources(project_store):
    store, project_id, first_signal, second_signal = project_store
    service = ProjectContextService(store)
    with store.business_task_transaction() as db:
        first_revision = service.apply(project_id=project_id, context=context(first_signal), signal_ids=(first_signal,), db=db)
    with store.business_task_transaction() as db:
        second_revision = service.apply(project_id=project_id, context=context(second_signal, owner="赵六"), signal_ids=(second_signal,), db=db)

    current = store.get_business_project_context(project_id)
    assert current is not None and current.overall_owner is not None
    assert current.overall_owner.person_name == "赵六"
    assert [item.id for item in store.list_business_project_context_revisions(project_id)] == [second_revision, first_revision]
    assert {item.signal_id for item in store.list_business_project_evidence(project_id)} == {first_signal, second_signal}
    assert store.get_business_project(project_id).context == current
    assert BusinessResolutionService(store).register_source_project(
        title="项目上下文", registry_source="portfolio-registry"
    ).context == current


def test_context_service_does_not_add_revision_or_evidence_for_identical_input(project_store):
    store, project_id, signal_id, _ = project_store
    service = ProjectContextService(store)
    value = context(signal_id)
    with store.business_task_transaction() as db:
        assert service.apply(project_id=project_id, context=value, signal_ids=(signal_id,), db=db) is not None
    with store.business_task_transaction() as db:
        assert service.apply(project_id=project_id, context=value, signal_ids=(signal_id,), db=db) is None
    assert len(store.list_business_project_context_revisions(project_id)) == 1
    assert len(store.list_business_project_evidence(project_id)) == 1
    assert context_changed('{"goal":"x","scope":"y"}', '{"scope":"y","goal":"x"}') is False


def test_context_service_rejects_missing_or_unresolved_signal_without_writes(project_store):
    store, project_id, signal_id, _ = project_store
    service = ProjectContextService(store)
    unresolved = ProjectContext(goal="交付", scope="一期", overall_owner=None, responsibilities=[], facts=[
        {"key": "risk", "text": "尚未确认", "evidence": [{"source_ref": "meeting:missing", "source_excerpt": "尚未确认"}]}
    ])
    with pytest.raises(ValueError), store.business_task_transaction() as db:
        service.apply(project_id=project_id, context=unresolved, signal_ids=(signal_id,), db=db)
    with pytest.raises(ValueError), store.business_task_transaction() as db:
        service.apply(project_id=project_id, context=context(signal_id), signal_ids=(999999,), db=db)
    assert store.list_business_project_context_revisions(project_id) == ()
    assert store.list_business_project_evidence(project_id) == ()


def test_context_none_only_links_proof_and_explicit_empty_snapshot_clears_roles(project_store):
    store, project_id, first_signal, second_signal = project_store
    service = ProjectContextService(store)
    with store.business_task_transaction() as db:
        service.apply(project_id=project_id, context=context(first_signal), signal_ids=(first_signal,), db=db)
    with store.business_task_transaction() as db:
        assert service.apply(project_id=project_id, context=None, signal_ids=(second_signal,), db=db) is None
    retained = store.get_business_project_context(project_id)
    assert retained is not None and len(retained.responsibilities) == 2
    empty = ProjectContext(goal="完成一期交付", scope="一期", overall_owner=None, responsibilities=[], facts=[])
    with store.business_task_transaction() as db:
        assert service.apply(project_id=project_id, context=empty, signal_ids=(second_signal,), db=db) is not None
    cleared = store.get_business_project_context(project_id)
    assert cleared is not None and cleared.overall_owner is None and cleared.responsibilities == []


def test_context_service_accepts_quote_in_one_decoded_structured_source_string(project_store):
    store, project_id, _, _ = project_store
    signal_id = store.create_business_task_signal(
        source_type="report", source_ref="report:structured",
        evidence_text=json.dumps({"report": {"line": "张三负责\n交付"}}, ensure_ascii=True),
        dedupe_key="project-context:structured",
    )
    value = ProjectContext(goal="交付", scope="一期", overall_owner={
        "person_name": "张三", "responsibility": "交付",
        "evidence": [{"signal_id": signal_id, "source_ref": "report:structured", "source_excerpt": "张三负责\n交付"}],
    }, responsibilities=[], facts=[])
    with store.business_task_transaction() as db:
        assert ProjectContextService(store).apply(
            project_id=project_id, context=value, signal_ids=(signal_id,), db=db
        ) is not None


def test_context_service_rejection_leaves_no_partial_writes_inside_caller_transaction(project_store):
    store, project_id, signal_id, _ = project_store
    value = ProjectContext(goal="交付", scope="一期", overall_owner=None, responsibilities=[], facts=[{
        "key": "fabricated", "text": "不可拼接", "evidence": [{
            "signal_id": signal_id, "source_ref": "meeting:project-1",
            "source_excerpt": "张三负责交付并不存在",
        }],
    }])
    with store.business_task_transaction() as db:
        with pytest.raises(ValueError, match="faithfully"):
            ProjectContextService(store).apply(
                project_id=project_id, context=value, signal_ids=(signal_id,), db=db
            )
        assert db.execute(
            "select count(*) from business_project_evidence where project_id=?", (project_id,)
        ).fetchone()[0] == 0
        assert db.execute(
            "select count(*) from business_project_context_revisions where project_id=?", (project_id,)
        ).fetchone()[0] == 0


@pytest.mark.parametrize("source_type", ["memory_provenance", "session_provenance"])
def test_project_context_evidence_requires_observed_original_source(project_store, source_type):
    store, project_id, _, _ = project_store
    signal_id = store.create_business_task_signal(source_type=source_type, source_ref="quoted:project",
        evidence_text="张三负责交付。", dedupe_key=f"{source_type}:project")
    with store.business_task_transaction() as db:
        with pytest.raises(ValueError, match="observed"):
            ProjectContextService(store).apply(project_id=project_id, context=None, signal_ids=(signal_id,), db=db)
        assert db.execute("select count(*) from business_project_evidence").fetchone()[0] == 0
        assert db.execute("select count(*) from business_project_context_revisions").fetchone()[0] == 0
