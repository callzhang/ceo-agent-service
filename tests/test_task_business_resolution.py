from __future__ import annotations

import pytest

from app.store import AutoReplyStore
from app.task_business_resolution import BusinessResolutionService
from app.task_semantic_models import (
    BusinessActorKind,
    BusinessAnchorType,
    BusinessRelationStatus,
    BusinessRelationType,
    BusinessRelevance,
    BusinessTaskStatus,
    BusinessTaskDateType,
    FormalTaskBasis,
)
from app.task_semantic_service import RecordFormalTask, SourceSignal, TaskDateInput, TaskSemanticService
from app.task_semantic_rules import FormalityEvidence


@pytest.fixture
def resolver(tmp_path):
    store = AutoReplyStore(tmp_path / "business-resolution.sqlite3")
    return BusinessResolutionService(store)


def create_task(store: AutoReplyStore, key: str, *, owner: str = "王明") -> int:
    service = TaskSemanticService(store)
    return service.record_formal_task(
        RecordFormalTask(
            title=f"任务 {key}",
            signal=SourceSignal(
                source_type="message", source_ref=key,
                evidence_text=f"{owner} 负责 {key}，要求 2026-10-01 前完成",
                dedupe_key=f"signal:{key}",
                author_kind=BusinessActorKind.HUMAN, author_user_id="assigner",
            ),
            formality=FormalityEvidence(
                basis=FormalTaskBasis.EXPLICIT_ASSIGNMENT,
                assigner_is_authorized=True,
                deliverable_is_explicit=True,
                owner_is_explicit=True,
            ),
            owner_name=owner,
            owner_evidence_json=f'{{"source_ref":"{key}","excerpt":"{owner}"}}',
            date_facts=(TaskDateInput(
                date_type=BusinessTaskDateType.REQUESTED_DEADLINE_AT,
                value_at="2026-10-01T00:00:00+00:00", raw_phrase="2026-10-01",
                actor_kind=BusinessActorKind.HUMAN, actor_user_id="assigner",
            ),),
        )
    ).task_id


def evidence_signal(store: AutoReplyStore, key: str) -> int:
    return store.create_business_task_signal(
        source_type="registry", source_ref=key, evidence_text=f"Evidence {key}",
        dedupe_key=f"evidence:{key}",
    )


def test_cluster_keeps_member_task_truth_independent_and_never_creates_project(resolver):
    first = create_task(resolver.store, "one", owner="王明")
    second = create_task(resolver.store, "two", owner="李丽")
    resolver.store.create_business_task(
        title="第三项", stage="candidate", status=BusinessTaskStatus.WAITING,
        owner_name="陈晨", deadline_at="2026-11-01T00:00:00+00:00",
    )
    third = resolver.store.list_business_tasks()[-1].id
    before = {task.id: task for task in resolver.store.list_business_tasks()}

    cluster_id = resolver.create_cluster(title="美国客户成交", task_ids=[first, second, third])
    candidate_id = resolver.propose_project(
        cluster_id=cluster_id, title="美国客户成交", reason="持续多任务目标",
    )

    assert resolver.store.get_business_project_candidate(candidate_id) is not None
    assert resolver.store.list_business_projects() == []
    after = {task.id: task for task in resolver.store.list_business_tasks()}
    for task_id in (first, second, third):
        assert (after[task_id].owner_name, after[task_id].deadline_at, after[task_id].status) == (
            before[task_id].owner_name, before[task_id].deadline_at, before[task_id].status
        )


def test_only_registry_registration_or_explicit_evidence_confirmation_creates_project(resolver):
    task_id = create_task(resolver.store, "one")
    cluster_id = resolver.create_cluster(title="美国客户成交", task_ids=[task_id])
    candidate_id = resolver.propose_project(
        cluster_id=cluster_id, title="美国客户成交", reason="持续目标",
    )
    anchor_id = resolver.register_anchor(
        anchor_type=BusinessAnchorType.PROJECT, anchor_ref="registry:us-close", title="美国客户成交"
    )

    with pytest.raises(ValueError, match="evidence"):
        resolver.confirm_project_candidate(candidate_id=candidate_id, project_id=1)
    assert resolver.store.list_business_projects() == []

    project_id = resolver.register_official_project(anchor_id=anchor_id, registry_source="portfolio-registry")
    registered_project = resolver.store.get_business_project(project_id)
    assert registered_project.canonical_anchor_id == anchor_id
    assert registered_project.registry_source == "portfolio-registry"
    candidate_two = resolver.propose_project(
        cluster_id=cluster_id, title="美国客户成交第二阶段", reason="另一个候选",
    )
    assert resolver.confirm_project_candidate(
        candidate_id=candidate_two, project_id=project_id,
        evidence_signal_id=evidence_signal(resolver.store, "candidate-confirm"),
    ) == candidate_two


def test_explicit_confirmation_atomically_creates_official_project_and_is_idempotent(resolver):
    task_id = create_task(resolver.store, "explicit-confirmation")
    cluster_id = resolver.create_cluster(title="Explicit project", task_ids=[task_id])
    candidate_id = resolver.propose_project(
        cluster_id=cluster_id, title="Explicit project", reason="Needs explicit confirmation"
    )
    anchor_id = resolver.register_anchor(
        anchor_type=BusinessAnchorType.PROJECT,
        anchor_ref="confirmed:explicit-project",
        title="Explicit project",
    )
    signal_id = evidence_signal(resolver.store, "explicit-project-confirmation")
    assert resolver.store.list_business_projects() == []

    assert resolver.confirm_project_candidate(
        candidate_id=candidate_id,
        anchor_id=anchor_id,
        evidence_signal_id=signal_id,
    ) == candidate_id
    candidate = resolver.store.get_business_project_candidate(candidate_id)
    assert candidate is not None
    assert candidate.confirmation_signal_id == signal_id
    project = resolver.store.get_business_project(candidate.confirmed_project_id)
    assert project is not None
    assert project.canonical_anchor_id == anchor_id
    assert project.registry_source == f"explicit_confirmation:{signal_id}"

    assert resolver.confirm_project_candidate(
        candidate_id=candidate_id,
        anchor_id=anchor_id,
        evidence_signal_id=signal_id,
    ) == candidate_id
    assert len(resolver.store.list_business_projects()) == 1

    other_anchor_id = resolver.register_anchor(
        anchor_type=BusinessAnchorType.PROJECT,
        anchor_ref="confirmed:other-project",
        title="Other project",
    )
    with pytest.raises(ValueError, match="different project"):
        resolver.confirm_project_candidate(
            candidate_id=candidate_id,
            anchor_id=other_anchor_id,
            evidence_signal_id=signal_id,
        )
    assert len(resolver.store.list_business_projects()) == 1


def test_anchor_confirmation_derives_relevance_and_proposed_link_does_not(resolver):
    task_id = create_task(resolver.store, "one")
    anchor_id = resolver.register_anchor(
        anchor_type=BusinessAnchorType.CUSTOMER, anchor_ref="customer:us", title="美国客户"
    )
    proposed_signal = evidence_signal(resolver.store, "proposed")

    assert resolver.propose_anchor_match(
        task_id=task_id, anchor_id=anchor_id, evidence_signal_id=proposed_signal, reason="可能相关"
    ) == task_id
    assert resolver.store.get_business_task(task_id).business_relevance is BusinessRelevance.UNKNOWN

    confirmed_signal = evidence_signal(resolver.store, "confirmed")
    assert resolver.confirm_anchor_match(
        task_id=task_id, anchor_id=anchor_id, evidence_signal_id=confirmed_signal, reason="客户确认"
    ) == task_id
    assert resolver.store.get_business_task(task_id).business_relevance is BusinessRelevance.RELEVANT
    assert [event.event_type.value for event in resolver.store.list_business_task_events(task_id)][-1] == "relevance_changed"


def test_anchor_proposals_return_their_persisted_link_ids(resolver):
    task_id = create_task(resolver.store, "link-ids")
    first_anchor = resolver.register_anchor(
        anchor_type=BusinessAnchorType.CUSTOMER,
        anchor_ref="customer:first-link",
        title="First link",
    )
    second_anchor = resolver.register_anchor(
        anchor_type=BusinessAnchorType.PRODUCT,
        anchor_ref="product:second-link",
        title="Second link",
    )

    first_link_id = resolver.propose_anchor_match(
        task_id=task_id,
        anchor_id=first_anchor,
        evidence_signal_id=evidence_signal(resolver.store, "first-link"),
    )
    second_link_id = resolver.propose_anchor_match(
        task_id=task_id,
        anchor_id=second_anchor,
        evidence_signal_id=evidence_signal(resolver.store, "second-link"),
    )

    with resolver.store._connect() as db:
        rows = db.execute(
            "select id, anchor_id from business_task_anchor_links where task_id=? order by id",
            (task_id,),
        ).fetchall()
    assert first_link_id != second_link_id
    assert [(row["id"], row["anchor_id"]) for row in rows] == [
        (first_link_id, first_anchor),
        (second_link_id, second_anchor),
    ]


def test_confirmed_not_relevant_stays_searchable_but_is_excluded_from_projection_input(resolver):
    task_id = create_task(resolver.store, "one")
    anchor_id = resolver.register_anchor(
        anchor_type=BusinessAnchorType.MATTER, anchor_ref="matter:incidental", title="临时杂事"
    )
    resolver.propose_anchor_match(
        task_id=task_id, anchor_id=anchor_id,
        evidence_signal_id=evidence_signal(resolver.store, "proposed"), reason="待判断"
    )
    resolver.confirm_anchor_match(
        task_id=task_id, anchor_id=anchor_id,
        evidence_signal_id=evidence_signal(resolver.store, "not-relevant"),
        reason="确认不属于公司主线", relevance=BusinessRelevance.NOT_RELEVANT,
    )

    assert resolver.store.get_business_task(task_id).business_relevance is BusinessRelevance.NOT_RELEVANT
    assert resolver.store.list_business_tasks(relevance=[BusinessRelevance.NOT_RELEVANT])[0].id == task_id
    assert task_id not in {task.id for task in resolver.store.list_business_tasks_for_projection()}


def test_projection_input_does_not_truncate_eligible_tasks_at_one_hundred(resolver):
    created_ids = [
        resolver.store.create_business_task(title=f"Candidate {index}", stage="candidate")
        for index in range(101)
    ]
    late_relevant_id = resolver.store.create_business_task(
        title="Late relevant task",
        stage="candidate",
        business_relevance=BusinessRelevance.RELEVANT,
    )

    projected_ids = {task.id for task in resolver.store.list_business_tasks_for_projection()}

    assert set(created_ids).issubset(projected_ids)
    assert late_relevant_id in projected_ids


def test_relation_is_evidence_backed_and_does_not_merge_tasks(resolver):
    first = create_task(resolver.store, "one")
    second = create_task(resolver.store, "two")
    relation_id = resolver.add_relation(
        from_task_id=first, to_task_id=second, relation_type=BusinessRelationType.SUPPORTS,
        evidence_signal_id=evidence_signal(resolver.store, "relation"),
        status=BusinessRelationStatus.CONFIRMED, reason="同一客户推进",
    )

    assert relation_id > 0
    assert resolver.store.get_business_task(first).status is not BusinessTaskStatus.MERGED
    assert resolver.store.get_business_task(second).status is not BusinessTaskStatus.MERGED


def test_relation_proposal_can_be_confirmed_and_replayed_without_changing_identity(resolver):
    first = create_task(resolver.store, "relation-first")
    second = create_task(resolver.store, "relation-second")
    proposed_signal = evidence_signal(resolver.store, "relation-proposed")
    confirmed_signal = evidence_signal(resolver.store, "relation-confirmed")

    relation_id = resolver.add_relation(
        from_task_id=first,
        to_task_id=second,
        relation_type=BusinessRelationType.RELATED_TO,
        evidence_signal_id=proposed_signal,
        status=BusinessRelationStatus.PROPOSED,
        reason="Possible shared work",
    )
    assert resolver.add_relation(
        from_task_id=first,
        to_task_id=second,
        relation_type=BusinessRelationType.RELATED_TO,
        evidence_signal_id=proposed_signal,
        status=BusinessRelationStatus.PROPOSED,
        reason="Possible shared work",
    ) == relation_id
    assert resolver.add_relation(
        from_task_id=first,
        to_task_id=second,
        relation_type=BusinessRelationType.RELATED_TO,
        evidence_signal_id=confirmed_signal,
        status=BusinessRelationStatus.CONFIRMED,
        reason="Confirmed shared work",
    ) == relation_id
    assert resolver.add_relation(
        from_task_id=first,
        to_task_id=second,
        relation_type=BusinessRelationType.RELATED_TO,
        evidence_signal_id=confirmed_signal,
        status=BusinessRelationStatus.CONFIRMED,
        reason="Confirmed shared work",
    ) == relation_id

    later_proposal = evidence_signal(resolver.store, "relation-late-proposal")
    assert resolver.add_relation(
        from_task_id=first,
        to_task_id=second,
        relation_type=BusinessRelationType.RELATED_TO,
        evidence_signal_id=later_proposal,
        status=BusinessRelationStatus.PROPOSED,
        reason="Late possible match",
    ) == relation_id
    with pytest.raises(ValueError, match="conflicting relation decision"):
        resolver.add_relation(
            from_task_id=first,
            to_task_id=second,
            relation_type=BusinessRelationType.RELATED_TO,
            evidence_signal_id=evidence_signal(resolver.store, "relation-rejected"),
            status=BusinessRelationStatus.REJECTED,
            reason="Conflicting rejection",
        )

    with resolver.store._connect() as db:
        row = db.execute(
            """select rowid, status, supporting_signal_id, reason
               from business_task_relations
               where from_task_id=? and to_task_id=? and relation_type=?""",
            (first, second, BusinessRelationType.RELATED_TO.value),
        ).fetchone()
    assert row is not None
    assert int(row["rowid"]) == relation_id
    assert row["status"] == BusinessRelationStatus.CONFIRMED.value
    assert row["supporting_signal_id"] == confirmed_signal
    assert row["reason"] == "Confirmed shared work"


def test_resolution_commands_require_persisted_evidence(resolver):
    first = create_task(resolver.store, "one")
    second = create_task(resolver.store, "two")
    anchor_id = resolver.register_anchor(
        anchor_type=BusinessAnchorType.CUSTOMER,
        anchor_ref="customer:evidence",
        title="Evidence customer",
    )

    with pytest.raises(ValueError, match="evidence signal"):
        resolver.add_relation(
            from_task_id=first,
            to_task_id=second,
            relation_type=BusinessRelationType.RELATED_TO,
            evidence_signal_id=999_999,
        )
    with pytest.raises(ValueError, match="evidence signal"):
        resolver.propose_anchor_match(
            task_id=first,
            anchor_id=anchor_id,
            evidence_signal_id=999_999,
        )


def test_relevance_is_recalculated_from_all_confirmed_active_links(resolver):
    task_id = create_task(resolver.store, "one")
    active_anchor_id = resolver.register_anchor(
        anchor_type=BusinessAnchorType.CUSTOMER,
        anchor_ref="customer:active",
        title="Active customer",
    )
    incidental_anchor_id = resolver.register_anchor(
        anchor_type=BusinessAnchorType.MATTER,
        anchor_ref="matter:incidental-two",
        title="Incidental matter",
    )
    resolver.confirm_anchor_match(
        task_id=task_id,
        anchor_id=active_anchor_id,
        evidence_signal_id=evidence_signal(resolver.store, "active-confirmation"),
    )

    resolver.confirm_anchor_match(
        task_id=task_id,
        anchor_id=incidental_anchor_id,
        evidence_signal_id=evidence_signal(resolver.store, "incidental-confirmation"),
        relevance=BusinessRelevance.NOT_RELEVANT,
    )

    assert resolver.store.get_business_task(task_id).business_relevance is BusinessRelevance.RELEVANT


def test_confirm_anchor_match_rolls_back_link_relevance_and_event_together(
    resolver, monkeypatch
):
    task_id = create_task(resolver.store, "one")
    anchor_id = resolver.register_anchor(
        anchor_type=BusinessAnchorType.CUSTOMER,
        anchor_ref="customer:rollback",
        title="Rollback customer",
    )
    signal_id = evidence_signal(resolver.store, "rollback")
    events_before = resolver.store.list_business_task_events(task_id)

    def fail_event(**_kwargs):
        raise RuntimeError("event write failed")

    monkeypatch.setattr(resolver.store, "append_business_task_event", fail_event)
    with pytest.raises(RuntimeError, match="event write failed"):
        resolver.confirm_anchor_match(
            task_id=task_id,
            anchor_id=anchor_id,
            evidence_signal_id=signal_id,
        )

    assert resolver.store.get_business_task(task_id).business_relevance is BusinessRelevance.UNKNOWN
    assert resolver.store.list_business_task_events(task_id) == events_before
    with resolver.store._connect() as db:
        assert db.execute(
            "select count(*) from business_task_anchor_links where task_id=? and anchor_id=?",
            (task_id, anchor_id),
        ).fetchone()[0] == 0


def test_proposal_cannot_downgrade_a_confirmed_anchor_decision(resolver):
    task_id = create_task(resolver.store, "one")
    anchor_id = resolver.register_anchor(
        anchor_type=BusinessAnchorType.CUSTOMER,
        anchor_ref="customer:confirmed",
        title="Confirmed customer",
    )
    resolver.confirm_anchor_match(
        task_id=task_id,
        anchor_id=anchor_id,
        evidence_signal_id=evidence_signal(resolver.store, "confirmed-first"),
    )

    with pytest.raises(ValueError, match="confirmed anchor match"):
        resolver.propose_anchor_match(
            task_id=task_id,
            anchor_id=anchor_id,
            evidence_signal_id=evidence_signal(resolver.store, "late-proposal"),
        )

    assert resolver.store.get_business_task(task_id).business_relevance is BusinessRelevance.RELEVANT
