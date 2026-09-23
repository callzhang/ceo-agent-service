from __future__ import annotations

import pytest

from app.store import AutoReplyStore
from app.task_attention_projection import AttentionProposal, BusinessAttentionProjection
from app.task_business_resolution import BusinessResolutionService
from app.task_semantic_models import AttentionCategory, BusinessAnchorType
from app.task_semantic_service import RecordFormalTask, SourceSignal, TaskSemanticService
from app.task_semantic_rules import FormalityEvidence
from app.task_semantic_models import FormalTaskBasis


@pytest.fixture
def projection(tmp_path):
    return BusinessAttentionProjection(AutoReplyStore(tmp_path / "attention.sqlite3"))


def _task_with_anchor(projection: BusinessAttentionProjection, key: str) -> tuple[int, int, int]:
    task_id = TaskSemanticService(projection.store).record_formal_task(
        RecordFormalTask(
            title=f"推进 {key}",
            signal=SourceSignal(
                source_type="message", source_ref=key, evidence_text=f"负责人推进 {key}",
                dedupe_key=f"task:{key}",
            ),
            formality=FormalityEvidence(
                basis=FormalTaskBasis.EXPLICIT_ASSIGNMENT,
                assigner_is_authorized=True,
                deliverable_is_explicit=True,
                owner_is_explicit=True,
            ),
            owner_name="王明",
        )
    ).task_id
    signal_id = projection.store.create_business_task_signal(
        source_type="registry", source_ref=f"anchor:{key}", evidence_text=f"{key} 是公司重点",
        dedupe_key=f"anchor:{key}",
    )
    resolver = BusinessResolutionService(projection.store)
    anchor_id = resolver.register_anchor(
        anchor_type=BusinessAnchorType.CUSTOMER,
        anchor_ref=f"customer:{key}", title=f"客户 {key}",
    )
    resolver.confirm_anchor_match(
        task_id=task_id, anchor_id=anchor_id, evidence_signal_id=signal_id,
        reason="已确认相关",
    )
    return task_id, anchor_id, signal_id


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
    projection.resolve(item_id=item_id, resolution_signal_id=resolution_signal, reason="已解决")
    assert projection.store.get_business_attention_item(item_id).status.value == "resolved"


def test_nonrelevant_task_is_not_eligible(projection):
    task_id = projection.store.create_business_task(title="临时杂事", stage="candidate")
    signal_id = projection.store.create_business_task_signal(
        source_type="message", source_ref="incidental", evidence_text="临时杂事",
        dedupe_key="incidental",
    )
    anchor_id = BusinessResolutionService(projection.store).register_anchor(
        anchor_type=BusinessAnchorType.MATTER, anchor_ref="matter:incidental", title="临时事项"
    )
    with pytest.raises(ValueError, match="relevant"):
        projection.upsert(_proposal((task_id,), anchor_id, signal_id))


def test_recompute_is_idempotent_for_same_semantic_snapshot(projection):
    task_id, anchor_id, signal_id = _task_with_anchor(projection, "recompute")
    # Upsert establishes the same stable semantic input recompute derives.
    item_id = projection.upsert(_proposal((task_id,), anchor_id, signal_id))
    projection.recompute_for_tasks((task_id,))
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
