from __future__ import annotations

from dataclasses import replace
import pytest

from app.store import AutoReplyStore
from app.task_attention_projection import AttentionProposal, BusinessAttentionProjection
from app.task_business_resolution import BusinessResolutionService
from app.project_context_service import ProjectContextService
from app.task_semantic_models import AttentionCategory, BusinessAnchorType
from app.task_semantic_service import (
    RecordFormalTask,
    SourceSignal,
    TaskSemanticService,
    UpdateBusinessTask,
)
from app.task_semantic_rules import FormalityEvidence
from app.task_semantic_models import BusinessTaskStatus, FormalTaskBasis


@pytest.fixture
def projection(tmp_path):
    return BusinessAttentionProjection(AutoReplyStore(tmp_path / "attention.sqlite3"))


def _task_with_anchor(projection: BusinessAttentionProjection, key: str) -> tuple[int, int, int]:
    task_id = TaskSemanticService(projection.store).record_formal_task(
        RecordFormalTask(
            title=f"推进 {key}",
            signal=SourceSignal(
                source_type="message", source_ref=key, evidence_text=f"王明负责推进 {key}",
                dedupe_key=f"task:{key}",
            ),
            formality=FormalityEvidence(
                basis=FormalTaskBasis.EXPLICIT_ASSIGNMENT,
                assigner_is_authorized=True,
                deliverable_is_explicit=True,
                owner_is_explicit=True,
            ),
            owner_name="王明",
            owner_evidence_json=f'{{"source_ref":"{key}","excerpt":"王明"}}',
        )
    ).task_id
    signal_id = projection.store.create_business_task_signal(
        source_type="registry", source_ref=f"anchor:{key}", evidence_text=f"{key} 是公司重点",
        dedupe_key=f"anchor:{key}",
    )
    resolver = BusinessResolutionService(projection.store)
    anchor_id = resolver.register_anchor(
        anchor_type=BusinessAnchorType.PROJECT,
        anchor_ref=f"project:{key}", title=f"交付 {key}",
    )
    resolver.register_official_project(anchor_id=anchor_id, registry_source=f"meeting:{key}")
    _link_project_signal(projection, anchor_id, signal_id)
    resolver.confirm_anchor_match(
        task_id=task_id, anchor_id=anchor_id, evidence_signal_id=signal_id,
        reason="已确认相关",
    )
    return task_id, anchor_id, signal_id


def _link_project_signal(projection, anchor_id, signal_id):
    with projection.store.business_task_transaction() as db:
        project_id = db.execute(
            "select id from business_projects where canonical_anchor_id=?", (anchor_id,)
        ).fetchone()[0]
        ProjectContextService(projection.store).apply(
            project_id=project_id, context=None, signal_ids=(signal_id,), db=db
        )


def _proposal(task_ids: tuple[int, ...], anchor_id: int, signal_id: int, *, category=AttentionCategory.WATCH):
    return AttentionProposal(
        stable_key=f"anchor:{anchor_id}:open",
        category=category,
        title="客户交付需关注",
        business_area="客户",
        why_attention="关键客户交付存在需要跟进的事项",
        current_state="负责人正在推进",
        ceo_action="关注推进节奏",
        anchor_id=anchor_id,
        task_ids=task_ids,
        evidence_signal_id=signal_id,
    )


def _project_without_tasks(projection, key):
    resolver = BusinessResolutionService(projection.store)
    anchor_id = resolver.register_anchor(
        anchor_type=BusinessAnchorType.PROJECT, anchor_ref=f"project:{key}", title=key
    )
    project_id = resolver.register_official_project(
        anchor_id=anchor_id, registry_source=f"meeting:{key}"
    )
    signal_id = projection.store.create_business_task_signal(
        source_type="message", source_ref=f"message:{key}",
        evidence_text="客户验收标准尚未确认，需要关注交付风险。", dedupe_key=f"risk:{key}"
    )
    with projection.store.business_task_transaction() as db:
        db.execute(
            "insert into business_project_evidence(project_id, signal_id) values (?, ?)",
            (project_id, signal_id),
        )
    return anchor_id, project_id, signal_id


def test_project_attention_does_not_require_a_task(projection):
    anchor_id, project_id, signal_id = _project_without_tasks(projection, "zero-task")
    proposal = _proposal((), anchor_id, signal_id)
    item_id = projection.upsert(proposal)
    assert projection.upsert(proposal) == item_id
    assert projection.store.get_business_attention_item(item_id).anchor_id == anchor_id
    assert projection.store.list_business_attention_tasks(item_id) == ()
    assert projection.store.list_business_tasks() == ()
    assert len(projection.store.list_business_project_evidence(project_id)) == 1
    assert len(projection.store.list_business_attention_events(item_id)) == 1


def test_zero_task_attention_resolves_only_with_its_project_evidence(projection):
    anchor_id, project_id, signal_id = _project_without_tasks(projection, "zero-resolution")
    item_id = projection.upsert(_proposal((), anchor_id, signal_id))
    other_anchor, _, unrelated_signal = _project_without_tasks(projection, "other-project")
    assert other_anchor != anchor_id
    with pytest.raises(ValueError, match="resolution evidence signal must be linked to the Project"):
        projection.resolve(item_id=item_id, resolution_signal_id=unrelated_signal, reason="已解决")
    resolution_signal = projection.store.create_business_task_signal(
        source_type="message", source_ref="resolved:zero", evidence_text="验收标准已经确认。",
        dedupe_key="resolved:zero"
    )
    with projection.store.business_task_transaction() as db:
        db.execute(
            "insert into business_project_evidence(project_id, signal_id) values (?, ?)",
            (project_id, resolution_signal),
        )
    projection.resolve(item_id=item_id, resolution_signal_id=resolution_signal, reason="验收风险已消除")
    projection.resolve(item_id=item_id, resolution_signal_id=resolution_signal, reason="验收风险已消除")
    assert projection.store.get_business_attention_item(item_id).status.value == "resolved"
    assert len(projection.store.list_business_attention_events(item_id)) == 2
    assert projection.store.list_business_tasks() == ()


def test_project_attention_rejects_task_only_evidence(projection):
    anchor_id, _, _ = _project_without_tasks(projection, "project-proof")
    task_id = projection.store.create_business_task(title="真实独立任务", stage="candidate")
    task_signal = projection.store.create_business_task_signal(
        source_type="message", source_ref="task-only", evidence_text="任务安排。", dedupe_key="task-only"
    )
    projection.store.link_business_task_evidence(task_id=task_id, signal_id=task_signal, evidence_role="discovery")
    with pytest.raises(ValueError, match="attention evidence signal must be linked to the Project"):
        projection.upsert(_proposal((), anchor_id, task_signal))
    assert projection.store.list_business_attention_items() == ()


@pytest.mark.parametrize("anchor_type", [BusinessAnchorType.PROJECT, BusinessAnchorType.CUSTOMER])
def test_zero_task_attention_rejects_unregistered_project_or_nonproject_anchor(projection, anchor_type):
    anchor_id = BusinessResolutionService(projection.store).register_anchor(
        anchor_type=anchor_type, anchor_ref=f"unregistered:{anchor_type.value}", title="只有锚点"
    )
    signal_id = projection.store.create_business_task_signal(
        source_type="message", source_ref="unregistered:source", evidence_text="存在风险。", dedupe_key="unregistered:source"
    )
    with pytest.raises(ValueError, match="requires an official Project"):
        projection.upsert(_proposal((), anchor_id, signal_id))
    assert projection.store.list_business_attention_items() == ()


def test_optional_attention_members_must_belong_to_the_actual_project(projection):
    _, first_anchor, first_signal = _task_with_anchor(projection, "first-project")
    other_task, other_anchor, _ = _task_with_anchor(projection, "second-project")
    assert first_anchor != other_anchor
    with pytest.raises(ValueError, match="confirmed for every underlying Task"):
        projection.upsert(_proposal((other_task,), first_anchor, first_signal))
    assert projection.store.list_business_attention_items() == ()


def test_upsert_creates_aggregated_attention_and_category_transition_preserves_id(projection):
    first, anchor_id, signal_id = _task_with_anchor(projection, "first")
    second, _, _ = _task_with_anchor(projection, "second")
    # The shared registered anchor is confirmed for the second independently open task.
    BusinessResolutionService(projection.store).confirm_anchor_match(
        task_id=second, anchor_id=anchor_id, evidence_signal_id=signal_id, reason="同一客户"
    )

    item_id = projection.upsert(_proposal((first, second), anchor_id, signal_id))
    assert {link.task_id for link in projection.store.list_business_attention_tasks(item_id)} == {first, second}
    assert projection.store.get_business_attention_item(item_id).category is AttentionCategory.WATCH

    transitioned = projection.upsert(
        _proposal((first, second), anchor_id, signal_id, category=AttentionCategory.DECISION)
    )
    assert transitioned == item_id
    assert projection.store.get_business_attention_item(item_id).category is AttentionCategory.DECISION
    assert [event.event_type.value for event in projection.store.list_business_attention_events(item_id)] == [
        "opened", "category_changed"
    ]


def test_assessment_change_creates_one_event_and_replay_is_idempotent(projection):
    task_id, anchor_id, signal_id = _task_with_anchor(projection, "assessment")
    initial = _proposal((task_id,), anchor_id, signal_id)
    attention_id = projection.upsert(initial)
    updated = replace(initial, assessment_json='{"inference":"new assessment"}')
    assert projection.upsert(updated) == attention_id
    assert projection.upsert(updated) == attention_id
    events = projection.store.list_business_attention_events(attention_id)
    assert len(events) == 2
    assert projection.store.get_business_attention_item(attention_id).assessment_json == updated.assessment_json


def test_resolution_requires_signal_and_reading_never_resolves(projection):
    task_id, anchor_id, signal_id = _task_with_anchor(projection, "read")
    item_id = projection.upsert(_proposal((task_id,), anchor_id, signal_id))

    projection.record_viewed(item_id=item_id, viewed_at="2026-09-22T12:00:00Z")
    assert projection.store.get_business_attention_item(item_id).status.value == "active"
    assert [event.event_type.value for event in projection.store.list_business_attention_events(item_id)] == ["opened"]
    with pytest.raises(ValueError, match="resolution evidence signal"):
        projection.resolve(item_id=item_id, resolution_signal_id=999_999, reason="已解决")

    resolution_signal = projection.store.create_business_task_signal(
        source_type="message", source_ref="resolved:read", evidence_text="问题已解决",
        dedupe_key="resolved:read",
    )
    projection.store.link_business_task_evidence(
        task_id=task_id, signal_id=resolution_signal, evidence_role="resolution"
    )
    _link_project_signal(projection, anchor_id, resolution_signal)
    projection.resolve(item_id=item_id, resolution_signal_id=resolution_signal, reason="已解决")
    assert projection.store.get_business_attention_item(item_id).status.value == "resolved"


def test_nonrelevant_task_is_not_eligible(projection):
    task_id = projection.store.create_business_task(title="临时杂事", stage="candidate")
    signal_id = projection.store.create_business_task_signal(
        source_type="message", source_ref="incidental", evidence_text="临时杂事",
        dedupe_key="incidental",
    )
    anchor_id, _, _ = _project_without_tasks(projection, "incidental-project")
    _link_project_signal(projection, anchor_id, signal_id)
    with pytest.raises(ValueError, match="relevant"):
        projection.upsert(_proposal((task_id,), anchor_id, signal_id))


def test_recompute_is_idempotent_for_same_semantic_snapshot(projection):
    task_id, anchor_id, signal_id = _task_with_anchor(projection, "recompute")
    # Upsert establishes the same stable semantic input recompute derives.
    item_id = projection.upsert(_proposal((task_id,), anchor_id, signal_id))
    before = (
        len(projection.store.list_business_attention_items()),
        len(projection.store.list_business_attention_tasks(item_id)),
        len(projection.store.list_business_attention_events(item_id)),
    )
    assert projection.recompute_for_tasks((task_id,)) == (item_id,)
    after = (
        len(projection.store.list_business_attention_items()),
        len(projection.store.list_business_attention_tasks(item_id)),
        len(projection.store.list_business_attention_events(item_id)),
    )
    assert after == before


def test_recompute_does_not_create_attention_for_routine_anchored_work(projection):
    task_id, _, _ = _task_with_anchor(projection, "routine")

    assert projection.recompute_for_tasks((task_id,)) == ()
    assert projection.store.list_business_attention_items() == ()


def test_recompute_preserves_category_and_resolved_state(projection):
    task_id, anchor_id, signal_id = _task_with_anchor(projection, "preserve")
    item_id = projection.upsert(
        _proposal((task_id,), anchor_id, signal_id, category=AttentionCategory.DECISION)
    )
    resolution_signal = projection.store.create_business_task_signal(
        source_type="message", source_ref="resolved:preserve", evidence_text="已解决",
        dedupe_key="resolved:preserve",
    )
    projection.store.link_business_task_evidence(
        task_id=task_id, signal_id=resolution_signal, evidence_role="resolution"
    )
    _link_project_signal(projection, anchor_id, resolution_signal)
    projection.resolve(item_id=item_id, resolution_signal_id=resolution_signal, reason="已解决")

    assert projection.recompute_for_tasks((task_id,)) == (item_id,)
    item = projection.store.get_business_attention_item(item_id)
    assert item.category is AttentionCategory.DECISION
    assert item.status.value == "resolved"


def test_upsert_synchronizes_membership_and_rejects_inactive_anchor(projection):
    first, anchor_id, signal_id = _task_with_anchor(projection, "membership-first")
    second, _, _ = _task_with_anchor(projection, "membership-second")
    resolver = BusinessResolutionService(projection.store)
    resolver.confirm_anchor_match(
        task_id=second, anchor_id=anchor_id, evidence_signal_id=signal_id, reason="同一客户"
    )
    item_id = projection.upsert(_proposal((first, second), anchor_id, signal_id))
    projection.upsert(_proposal((first,), anchor_id, signal_id))
    assert [link.task_id for link in projection.store.list_business_attention_tasks(item_id)] == [first]
    latest = projection.store.list_business_attention_events(item_id)[-1]
    assert '"task_ids":[%s,%s]' % (first, second) in latest.before_json
    assert '"task_ids":[%s]' % first in latest.after_json

    with projection.store.business_task_transaction() as db:
        db.execute("update business_anchors set active=0 where id=?", (anchor_id,))
    with pytest.raises(ValueError, match="active"):
        projection.upsert(_proposal((first,), anchor_id, signal_id))


@pytest.mark.parametrize("status", [BusinessTaskStatus.DONE, BusinessTaskStatus.CANCELLED])
def test_recompute_removes_terminal_tasks_from_attention_membership(projection, status):
    task_id, anchor_id, signal_id = _task_with_anchor(projection, f"terminal:{status.value}")
    item_id = projection.upsert(_proposal((task_id,), anchor_id, signal_id))
    TaskSemanticService(projection.store).update_task(
        UpdateBusinessTask(
            task_id=task_id,
            status=status,
            signal=SourceSignal(
                source_type="message", source_ref=f"terminal:{status.value}",
                evidence_text=f"任务已{status.value}", dedupe_key=f"terminal:{status.value}",
            ),
        )
    )

    assert projection.recompute_for_tasks((task_id,)) == (item_id,)
    assert projection.store.list_business_attention_tasks(item_id) == ()
    assert projection.store.get_business_attention_item(item_id).status.value == "active"


def test_resolution_can_use_historical_member_after_recompute_removes_it(projection):
    task_id, anchor_id, signal_id = _task_with_anchor(projection, "historical-resolution")
    item_id = projection.upsert(_proposal((task_id,), anchor_id, signal_id))
    TaskSemanticService(projection.store).update_task(
        UpdateBusinessTask(
            task_id=task_id,
            status=BusinessTaskStatus.DONE,
            signal=SourceSignal(
                source_type="message", source_ref="historical-done", evidence_text="任务完成",
                dedupe_key="historical-done",
            ),
        )
    )
    projection.recompute_for_tasks((task_id,))
    resolution_signal = projection.store.create_business_task_signal(
        source_type="message", source_ref="historical-resolution", evidence_text="关注事项解决",
        dedupe_key="historical-resolution",
    )
    projection.store.link_business_task_evidence(
        task_id=task_id, signal_id=resolution_signal, evidence_role="resolution"
    )

    _link_project_signal(projection, anchor_id, resolution_signal)
    projection.resolve(item_id=item_id, resolution_signal_id=resolution_signal, reason="已解决")
    resolved = projection.store.get_business_attention_item(item_id)
    event = projection.store.list_business_attention_events(item_id)[-1]
    assert resolved.status.value == "resolved"
    assert '"task_ids":[]' in event.before_json
    assert '"task_ids":[]' in event.after_json


def test_resolved_membership_change_stays_resolved_and_is_not_reopened(projection):
    first, anchor_id, signal_id = _task_with_anchor(projection, "resolved-first")
    second, _, _ = _task_with_anchor(projection, "resolved-second")
    resolver = BusinessResolutionService(projection.store)
    resolver.confirm_anchor_match(task_id=second, anchor_id=anchor_id, evidence_signal_id=signal_id, reason="同一客户")
    item_id = projection.upsert(_proposal((first,), anchor_id, signal_id))
    resolution_signal = projection.store.create_business_task_signal(
        source_type="message", source_ref="resolved-membership", evidence_text="已解决",
        dedupe_key="resolved-membership",
    )
    projection.store.link_business_task_evidence(task_id=first, signal_id=resolution_signal, evidence_role="resolution")
    _link_project_signal(projection, anchor_id, resolution_signal)
    projection.resolve(item_id=item_id, resolution_signal_id=resolution_signal, reason="已解决")

    projection.upsert(_proposal((first, second), anchor_id, signal_id))
    item = projection.store.get_business_attention_item(item_id)
    assert item.status.value == "resolved"
    assert projection.store.list_business_attention_events(item_id)[-1].event_type.value == "updated"


def test_resolution_snapshot_includes_current_membership(projection):
    first, anchor_id, signal_id = _task_with_anchor(projection, "snapshot-first")
    second, _, _ = _task_with_anchor(projection, "snapshot-second")
    resolver = BusinessResolutionService(projection.store)
    resolver.confirm_anchor_match(task_id=second, anchor_id=anchor_id, evidence_signal_id=signal_id, reason="同一客户")
    item_id = projection.upsert(_proposal((first, second), anchor_id, signal_id))
    resolution_signal = projection.store.create_business_task_signal(
        source_type="message", source_ref="snapshot-resolution", evidence_text="已解决",
        dedupe_key="snapshot-resolution",
    )
    projection.store.link_business_task_evidence(task_id=first, signal_id=resolution_signal, evidence_role="resolution")
    _link_project_signal(projection, anchor_id, resolution_signal)
    projection.resolve(item_id=item_id, resolution_signal_id=resolution_signal, reason="已解决")
    event = projection.store.list_business_attention_events(item_id)[-1]
    expected = '"task_ids":[%s,%s]' % (first, second)
    assert expected in event.before_json
    assert expected in event.after_json


def test_recompute_readds_desired_task_when_eligibility_returns(projection, monkeypatch):
    task_id, anchor_id, signal_id = _task_with_anchor(projection, "returns")
    item_id = projection.upsert(_proposal((task_id,), anchor_id, signal_id))
    service = TaskSemanticService(projection.store)
    service.update_task(
        UpdateBusinessTask(
            task_id=task_id, status=BusinessTaskStatus.DONE,
            signal=SourceSignal(source_type="message", source_ref="returns:done", evidence_text="完成", dedupe_key="returns:done"),
        )
    )
    monkeypatch.setattr(projection, "_timestamp", lambda: "2026-09-22T12:00:00+00:00")
    projection.recompute_for_tasks((task_id,))
    assert projection.store.list_business_attention_tasks(item_id) == ()
    assert projection.store.get_business_attention_item(item_id).updated_at == "2026-09-22T12:00:00+00:00"

    service.update_task(
        UpdateBusinessTask(
            task_id=task_id, status=BusinessTaskStatus.OPEN,
            signal=SourceSignal(source_type="message", source_ref="returns:open", evidence_text="重新打开", dedupe_key="returns:open"),
        )
    )
    monkeypatch.setattr(projection, "_timestamp", lambda: "2026-09-22T12:01:00+00:00")
    assert projection.recompute_for_tasks((task_id,)) == (item_id,)
    assert [link.task_id for link in projection.store.list_business_attention_tasks(item_id)] == [task_id]
    assert projection.store.get_business_attention_item(item_id).updated_at == "2026-09-22T12:01:00+00:00"


def test_explicit_proposal_removal_prevents_readding_task(projection):
    first, anchor_id, signal_id = _task_with_anchor(projection, "removal-first")
    second, _, _ = _task_with_anchor(projection, "removal-second")
    resolver = BusinessResolutionService(projection.store)
    resolver.confirm_anchor_match(task_id=second, anchor_id=anchor_id, evidence_signal_id=signal_id, reason="同一客户")
    item_id = projection.upsert(_proposal((first, second), anchor_id, signal_id))
    projection.upsert(_proposal((first,), anchor_id, signal_id))

    assert projection.recompute_for_tasks((second,)) == ()
    assert [link.task_id for link in projection.store.list_business_attention_tasks(item_id)] == [first]


def test_proposal_task_order_is_canonical_and_does_not_append_event(projection):
    first, anchor_id, signal_id = _task_with_anchor(projection, "ordered-first")
    second, _, _ = _task_with_anchor(projection, "ordered-second")
    BusinessResolutionService(projection.store).confirm_anchor_match(
        task_id=second, anchor_id=anchor_id, evidence_signal_id=signal_id, reason="同一客户"
    )
    item_id = projection.upsert(_proposal((first, second), anchor_id, signal_id))
    before = projection.store.list_business_attention_events(item_id)

    assert projection.upsert(_proposal((second, first), anchor_id, signal_id)) == item_id
    assert projection.store.list_business_attention_events(item_id) == before


def test_initial_terminal_proposal_retains_resolution_lineage(projection):
    task_id, anchor_id, signal_id = _task_with_anchor(projection, "initial-terminal")
    TaskSemanticService(projection.store).update_task(
        UpdateBusinessTask(
            task_id=task_id, status=BusinessTaskStatus.DONE,
            signal=SourceSignal(
                source_type="message", source_ref="initial-terminal:done", evidence_text="完成",
                dedupe_key="initial-terminal:done",
            ),
        )
    )
    item_id = projection.upsert(_proposal((task_id,), anchor_id, signal_id))
    assert projection.store.list_business_attention_tasks(item_id) == ()
    resolution_signal = projection.store.create_business_task_signal(
        source_type="message", source_ref="initial-terminal:resolution", evidence_text="已解决",
        dedupe_key="initial-terminal:resolution",
    )
    projection.store.link_business_task_evidence(
        task_id=task_id, signal_id=resolution_signal, evidence_role="resolution"
    )

    _link_project_signal(projection, anchor_id, resolution_signal)
    projection.resolve(item_id=item_id, resolution_signal_id=resolution_signal, reason="已解决")
    assert projection.store.get_business_attention_item(item_id).status.value == "resolved"


def test_resolution_uses_historical_proposal_membership_after_explicit_removal(projection):
    first, anchor_id, signal_id = _task_with_anchor(projection, "historical-proposal-first")
    second, _, _ = _task_with_anchor(projection, "historical-proposal-second")
    resolver = BusinessResolutionService(projection.store)
    resolver.confirm_anchor_match(task_id=second, anchor_id=anchor_id, evidence_signal_id=signal_id, reason="同一客户")
    service = TaskSemanticService(projection.store)
    for task_id, key in ((first, "first"), (second, "second")):
        service.update_task(
            UpdateBusinessTask(
                task_id=task_id, status=BusinessTaskStatus.DONE,
                signal=SourceSignal(source_type="message", source_ref=f"historical:{key}", evidence_text="完成", dedupe_key=f"historical:{key}"),
            )
        )
    item_id = projection.upsert(_proposal((first, second), anchor_id, signal_id))
    projection.upsert(_proposal((second,), anchor_id, signal_id))
    resolution_signal = projection.store.create_business_task_signal(
        source_type="message", source_ref="historical:resolution", evidence_text="已解决",
        dedupe_key="historical:resolution",
    )
    projection.store.link_business_task_evidence(
        task_id=first, signal_id=resolution_signal, evidence_role="resolution"
    )

    _link_project_signal(projection, anchor_id, resolution_signal)
    projection.resolve(item_id=item_id, resolution_signal_id=resolution_signal, reason="历史证据解决")
    assert projection.store.get_business_attention_item(item_id).status.value == "resolved"
    membership_event = projection.store.list_business_attention_events(item_id)[1]
    assert '"proposal_task_ids":[%s,%s]' % (first, second) in membership_event.before_json
    assert '"proposal_task_ids":[%s]' % second in membership_event.after_json
