from __future__ import annotations

import sqlite3
from dataclasses import asdict

import pytest

from app.store import AutoReplyStore
from app.task_semantic_models import (
    BusinessEvidenceRole,
    BusinessTaskStatus,
    CommitmentStatus,
    FormalTaskBasis,
)
from app.task_semantic_service import (
    ApplyAcceptance,
    MergeBusinessTasks,
    PromoteCandidate,
    RecordCandidate,
    RecordFormalTask,
    RecordTaskFromEvidence,
    SourceSignal,
    TaskSemanticService,
    UpdateBusinessTask,
)
from app.task_semantic_rules import FormalityEvidence, IdentityEvidence


@pytest.fixture
def service(tmp_path):
    return TaskSemanticService(AutoReplyStore(tmp_path / "semantic-service.sqlite3"))


def assignment_signal(*, dedupe_key: str = "message:assignment") -> SourceSignal:
    return SourceSignal(
        source_type="dingtalk_message",
        source_ref=dedupe_key,
        evidence_text="王明，周五前提交报价。",
        dedupe_key=dedupe_key,
    )


def formality_evidence(
    basis: FormalTaskBasis, *, authorized: bool = True, deliverable: bool = True, owner: bool = True
) -> FormalityEvidence:
    return FormalityEvidence(
        basis=basis,
        assigner_is_authorized=authorized,
        deliverable_is_explicit=deliverable,
        owner_is_explicit=owner,
    )


def merge_identity() -> IdentityEvidence:
    return IdentityEvidence(same_external_task_id=True)


def record_assignment(service: TaskSemanticService, *, dedupe_key: str = "message:assignment"):
    return service.record_formal_task(
        RecordFormalTask(
            title="提交报价",
            formality=formality_evidence(FormalTaskBasis.EXPLICIT_ASSIGNMENT),
            owner_name="王明",
            signal=assignment_signal(dedupe_key=dedupe_key),
        )
    )


def semantic_state(service: TaskSemanticService):
    tasks = service.store.list_business_tasks()
    return (
        service.store.list_business_task_signals(),
        tasks,
        {task.id: service.store.list_business_task_evidence(task.id) for task in tasks},
        {task.id: service.events(task.id) for task in tasks},
    )


def test_record_formal_task_commits_signal_task_evidence_and_initial_event_together(service):
    result = record_assignment(service)

    assert result.created is True
    assert result.signal_id > 0
    task = service.store.get_business_task(result.task_id)
    assert task is not None
    assert task.formal_basis is FormalTaskBasis.EXPLICIT_ASSIGNMENT
    assert task.commitment_status is CommitmentStatus.ASSIGNED_UNACCEPTED
    evidence = service.store.list_business_task_evidence(result.task_id)
    assert [(item.signal_id, item.evidence_role) for item in evidence] == [
        (result.signal_id, BusinessEvidenceRole.ASSIGNMENT)
    ]
    assert [row.event_type.value for row in service.events(result.task_id)] == ["created"]


def test_duplicate_signal_is_idempotent_and_does_not_duplicate_downstream_history(service):
    first = record_assignment(service)
    duplicate = record_assignment(service)

    assert duplicate == first.__class__(
        task_id=first.task_id, signal_id=first.signal_id, created=False
    )
    assert len(service.store.list_business_task_signals()) == 1
    assert len(service.store.list_business_tasks()) == 1
    assert len(service.store.list_business_task_evidence(first.task_id)) == 1
    assert [row.event_type.value for row in service.events(first.task_id)] == ["created"]


@pytest.mark.parametrize("record_kind", ["candidate", "formal"])
def test_record_task_reuses_independently_persisted_signal_and_replays(service, record_kind):
    signal = assignment_signal()
    signal_id = service.store.create_business_task_signal(**asdict(signal))
    original_signal = service.store.get_business_task_signal(signal_id)
    if record_kind == "candidate":
        command = RecordCandidate(title="提交报价", signal=signal)
        record = service.record_candidate
    else:
        command = RecordFormalTask(
            title="提交报价", signal=signal,
            formality=formality_evidence(FormalTaskBasis.EXPLICIT_ASSIGNMENT),
        )
        record = service.record_formal_task

    first = record(command)
    state_before_replay = semantic_state(service)
    replay = record(command)

    assert first.created is True
    assert first.signal_id == replay.signal_id == signal_id
    assert replay.task_id == first.task_id
    assert replay.created is False
    assert service.store.list_business_task_signals() == (original_signal,)
    assert len(service.store.list_business_tasks()) == 1
    assert len(service.store.list_business_task_evidence(first.task_id)) == 1
    assert [event.event_type.value for event in service.events(first.task_id)] == ["created"]
    assert semantic_state(service) == state_before_replay


@pytest.mark.parametrize("operation", ["promote", "update", "accept", "merge"])
def test_transition_reuses_independently_persisted_signal_and_replays(service, operation):
    source = (
        record_assignment(service)
        if operation == "accept"
        else service.record_candidate(
            RecordCandidate(title="提交报价", signal=assignment_signal())
        )
    )
    signal = assignment_signal(dedupe_key="message:transition")
    signal_id = service.store.create_business_task_signal(**asdict(signal))
    original_signal = service.store.get_business_task_signal(signal_id)
    if operation == "promote":
        command = PromoteCandidate(
            task_id=source.task_id, signal=signal,
            formality=formality_evidence(FormalTaskBasis.EXPLICIT_ASSIGNMENT),
        )
        transition = service.promote_candidate
    elif operation == "update":
        command = UpdateBusinessTask(
            task_id=source.task_id, signal=signal, deadline_at="2026-09-25T17:00:00Z",
        )
        transition = service.update_task
    elif operation == "accept":
        command = ApplyAcceptance(task_id=source.task_id, signal=signal)
        transition = service.apply_acceptance
    else:
        target = record_assignment(service, dedupe_key="message:target")
        command = MergeBusinessTasks(
            source_task_id=source.task_id, target_task_id=target.task_id, signal=signal,
            identity_evidence=merge_identity(),
        )
        transition = service.merge_same_deliverable

    first = transition(command)
    state_before_replay = semantic_state(service)

    assert first.signal_id == signal_id
    assert service.store.get_business_task_signal(signal_id) == original_signal
    assert len(service.events(source.task_id)) == 2
    assert transition(command) == first
    assert semantic_state(service) == state_before_replay


@pytest.mark.parametrize("operation", ["promote-candidate", "update-candidate", "update-formal"])
@pytest.mark.parametrize("check_insert_order", [False, True])
def test_fresh_transition_to_merged_source_rejects_without_any_mutation(
    service, operation, check_insert_order, monkeypatch
):
    if operation == "update-formal":
        source = record_assignment(service)
    else:
        source = service.record_candidate(
            RecordCandidate(title="提交报价", signal=assignment_signal())
        )
    target = record_assignment(service, dedupe_key="message:target")
    service.merge_same_deliverable(
        MergeBusinessTasks(
            source_task_id=source.task_id, target_task_id=target.task_id,
            signal=assignment_signal(dedupe_key="review:merge"),
            identity_evidence=merge_identity(),
        )
    )
    state_before = semantic_state(service)

    def unexpected_signal_insert(**_kwargs):
        pytest.fail("merged source must be rejected before signal persistence")

    if check_insert_order:
        monkeypatch.setattr(
            service.store, "create_business_task_signal_in_transaction", unexpected_signal_insert
        )
    with pytest.raises(ValueError, match="merged"):
        if operation == "promote-candidate":
            service.promote_candidate(
                PromoteCandidate(
                    task_id=source.task_id,
                    formality=formality_evidence(FormalTaskBasis.EXPLICIT_ASSIGNMENT),
                    signal=assignment_signal(dedupe_key="message:fresh-promotion"),
                )
            )
        else:
            service.update_task(
                UpdateBusinessTask(
                    task_id=source.task_id, deadline_at="2026-09-25T17:00:00Z",
                    signal=assignment_signal(dedupe_key="message:fresh-deadline"),
                )
            )

    assert semantic_state(service) == state_before


@pytest.mark.parametrize("operation", ["promote", "update"])
def test_transition_replay_still_succeeds_after_source_is_merged(service, operation):
    source = service.record_candidate(
        RecordCandidate(title="提交报价", signal=assignment_signal())
    )
    signal = assignment_signal(dedupe_key="message:transition")
    if operation == "promote":
        command = PromoteCandidate(
            task_id=source.task_id, signal=signal,
            formality=formality_evidence(FormalTaskBasis.EXPLICIT_ASSIGNMENT),
        )
        transition = service.promote_candidate
    else:
        command = UpdateBusinessTask(
            task_id=source.task_id, signal=signal, deadline_at="2026-09-25T17:00:00Z",
        )
        transition = service.update_task
    first = transition(command)
    target = record_assignment(service, dedupe_key="message:target")
    service.merge_same_deliverable(
        MergeBusinessTasks(
            source_task_id=source.task_id, target_task_id=target.task_id,
            signal=assignment_signal(dedupe_key="review:merge"),
            identity_evidence=merge_identity(),
        )
    )
    state_before = semantic_state(service)

    assert transition(command) == first
    assert semantic_state(service) == state_before


def test_acceptance_changes_the_same_assigned_task_and_appends_history(service):
    initial = record_assignment(service)

    result = service.apply_acceptance(
        ApplyAcceptance(
            task_id=initial.task_id,
            signal=SourceSignal(
                source_type="dingtalk_message",
                source_ref="message:acceptance",
                evidence_text="我接受，会在周五前完成。",
                dedupe_key="message:acceptance",
            ),
        )
    )

    assert result.task_id == initial.task_id
    assert result.created is False
    task = service.store.get_business_task(initial.task_id)
    assert task is not None
    assert task.commitment_status is CommitmentStatus.ACCEPTED
    assert [row.event_type.value for row in service.events(initial.task_id)] == [
        "created",
        "commitment_changed",
    ]


def test_event_insert_failure_rolls_back_signal_task_evidence_and_update(service, monkeypatch):
    initial = record_assignment(service)

    def fail_event(**_kwargs):
        raise sqlite3.IntegrityError("forced event failure")

    monkeypatch.setattr(service.store, "append_business_task_event", fail_event)
    with pytest.raises(sqlite3.IntegrityError, match="forced event failure"):
        service.apply_acceptance(
            ApplyAcceptance(
                task_id=initial.task_id,
                signal=SourceSignal(
                    source_type="dingtalk_message",
                    source_ref="message:rollback",
                    evidence_text="我接受。",
                    dedupe_key="message:rollback",
                ),
            )
        )

    task = service.store.get_business_task(initial.task_id)
    assert task is not None
    assert task.commitment_status is CommitmentStatus.ASSIGNED_UNACCEPTED
    assert service.store.get_business_task_signal(2) is None
    assert [row.event_type.value for row in service.events(initial.task_id)] == ["created"]


def test_merge_preserves_source_history_and_copies_all_evidence_to_target(service):
    source = record_assignment(service, dedupe_key="message:source")
    target = service.record_candidate(
        RecordCandidate(
            title="提交报价",
            signal=SourceSignal(
                source_type="dingtalk_message",
                source_ref="message:target",
                evidence_text="报价需要完成。",
                dedupe_key="message:target",
            ),
        )
    )
    source_events_before = service.events(source.task_id)
    source_evidence_before = service.store.list_business_task_evidence(source.task_id)

    result = service.merge_same_deliverable(
        MergeBusinessTasks(
            source_task_id=source.task_id,
            target_task_id=target.task_id,
            signal=SourceSignal(
                source_type="human_confirmation",
                source_ref="review:merge",
                evidence_text="两个任务指向同一份报价。",
                dedupe_key="review:merge",
            ),
            identity_evidence=merge_identity(),
        )
    )

    assert result.task_id == target.task_id
    source_task = service.store.get_business_task(source.task_id)
    target_task = service.store.get_business_task(target.task_id)
    assert source_task is not None and target_task is not None
    assert source_task.status is BusinessTaskStatus.MERGED
    assert source_task.merged_into_task_id == target.task_id
    assert target_task.status is BusinessTaskStatus.OPEN
    assert service.events(source.task_id)[: len(source_events_before)] == source_events_before
    assert {item.signal_id for item in service.store.list_business_task_evidence(target.task_id)} >= {
        item.signal_id for item in source_evidence_before
    }
    assert any(
        item.evidence_role is BusinessEvidenceRole.MERGE_IDENTITY
        for item in service.store.list_business_task_evidence(target.task_id)
    )
    assert service.events(source.task_id)[-1].event_type.value == "merged"
    assert service.events(target.task_id)[-1].event_type.value == "merged"


@pytest.mark.parametrize(
    ("source_index", "target_index"),
    [(1, 2), (0, 2), (2, 0), (1, 1)],
    ids=["merge-target-as-source", "merged-source", "merged-target", "self-merge"],
)
def test_merge_rejects_chains_and_merged_endpoints_atomically(
    service, source_index, target_index
):
    tasks = [
        record_assignment(service, dedupe_key=f"message:task-{index}")
        for index in range(3)
    ]
    service.merge_same_deliverable(
        MergeBusinessTasks(
            source_task_id=tasks[0].task_id,
            target_task_id=tasks[1].task_id,
            signal=assignment_signal(dedupe_key="review:first-merge"),
            identity_evidence=merge_identity(),
        )
    )
    state_before = semantic_state(service)

    with pytest.raises(ValueError, match="merge"):
        service.merge_same_deliverable(
            MergeBusinessTasks(
                source_task_id=tasks[source_index].task_id,
                target_task_id=tasks[target_index].task_id,
                signal=assignment_signal(dedupe_key="review:rejected-merge"),
                identity_evidence=merge_identity(),
            )
        )

    assert semantic_state(service) == state_before


def test_merge_allows_multiple_sources_to_the_same_target(service):
    sources = [
        record_assignment(service, dedupe_key=f"message:source-{index}")
        for index in range(2)
    ]
    target = record_assignment(service, dedupe_key="message:target")

    for index, source in enumerate(sources):
        result = service.merge_same_deliverable(
            MergeBusinessTasks(
                source_task_id=source.task_id,
                target_task_id=target.task_id,
                signal=assignment_signal(dedupe_key=f"review:merge-{index}"),
                identity_evidence=merge_identity(),
            )
        )
        assert result.task_id == target.task_id
        merged = service.store.get_business_task(source.task_id)
        assert merged.status is BusinessTaskStatus.MERGED
        assert merged.merged_into_task_id == target.task_id

    assert service.store.get_business_task(target.task_id).status is BusinessTaskStatus.OPEN
    assert len(service.store.list_business_tasks()) == 3


def test_promotion_replay_preserves_original_result_state_and_history(service):
    candidate = service.record_candidate(
        RecordCandidate(title="提交报价", signal=assignment_signal())
    )
    command = PromoteCandidate(
        task_id=candidate.task_id,
        formality=formality_evidence(FormalTaskBasis.EXPLICIT_ASSIGNMENT),
        signal=assignment_signal(dedupe_key="message:promotion"),
    )
    first = service.promote_candidate(command)
    state_before = semantic_state(service)

    replay = service.promote_candidate(command)

    assert replay == first
    assert semantic_state(service) == state_before
    assert len(service.store.list_business_task_signals()) == 2
    assert len(service.store.list_business_tasks()) == 1
    assert len(service.store.list_business_task_evidence(first.task_id)) == 2
    assert [event.event_type.value for event in service.events(first.task_id)] == [
        "created", "promoted"
    ]


def test_merge_replay_preserves_original_result_state_and_history(service):
    target = record_assignment(service, dedupe_key="message:target")
    source = record_assignment(service, dedupe_key="message:source")
    command = MergeBusinessTasks(
        source_task_id=source.task_id,
        target_task_id=target.task_id,
        signal=assignment_signal(dedupe_key="review:merge"),
        identity_evidence=merge_identity(),
    )
    first = service.merge_same_deliverable(command)
    state_before = semantic_state(service)

    replay = service.merge_same_deliverable(command)

    assert replay == first
    assert semantic_state(service) == state_before
    assert len(service.store.list_business_task_signals()) == 3
    assert len(service.store.list_business_tasks()) == 2
    assert len(service.store.list_business_task_evidence(source.task_id)) == 2
    assert len(service.store.list_business_task_evidence(target.task_id)) == 3
    assert len(service.events(source.task_id)) == len(service.events(target.task_id)) == 2


@pytest.mark.parametrize("record_kind", ["candidate", "formal"])
def test_creation_replay_returns_original_task_after_evidence_is_copied_by_merge(
    service, record_kind
):
    target = record_assignment(service, dedupe_key="message:older-target")
    if record_kind == "candidate":
        command = RecordCandidate(title="提交报价", signal=assignment_signal())
        record = service.record_candidate
    else:
        command = RecordFormalTask(
            title="提交报价",
            formality=formality_evidence(FormalTaskBasis.EXPLICIT_ASSIGNMENT),
            signal=assignment_signal(),
        )
        record = service.record_formal_task
    first = record(command)
    service.merge_same_deliverable(
        MergeBusinessTasks(
            source_task_id=first.task_id,
            target_task_id=target.task_id,
            signal=assignment_signal(dedupe_key="review:merge"),
            identity_evidence=merge_identity(),
        )
    )
    state_before = semantic_state(service)

    replay = record(command)

    assert replay.task_id == first.task_id
    assert replay.signal_id == first.signal_id
    assert replay.created is False
    assert semantic_state(service) == state_before


def test_acceptance_replay_preserves_result_after_task_is_merged(service):
    task = record_assignment(service)
    command = ApplyAcceptance(
        task_id=task.task_id,
        signal=assignment_signal(dedupe_key="message:acceptance"),
    )
    first = service.apply_acceptance(command)
    target = record_assignment(service, dedupe_key="message:target")
    service.merge_same_deliverable(
        MergeBusinessTasks(
            source_task_id=task.task_id,
            target_task_id=target.task_id,
            signal=assignment_signal(dedupe_key="review:merge"),
            identity_evidence=merge_identity(),
        )
    )
    state_before = semantic_state(service)

    assert service.apply_acceptance(command) == first
    assert semantic_state(service) == state_before


def test_update_replay_preserves_result_after_a_later_state_change(service):
    task = record_assignment(service)
    command = UpdateBusinessTask(
        task_id=task.task_id,
        deadline_at="2026-09-25T17:00:00Z",
        signal=assignment_signal(dedupe_key="message:first-deadline"),
    )
    first = service.update_task(command)
    service.update_task(
        UpdateBusinessTask(
            task_id=task.task_id,
            deadline_at="2026-09-28T17:00:00Z",
            signal=assignment_signal(dedupe_key="message:later-deadline"),
        )
    )
    state_before = semantic_state(service)

    assert service.update_task(command) == first
    assert semantic_state(service) == state_before


@pytest.mark.parametrize(
    ("formal_basis", "evidence_role"),
    [
        (FormalTaskBasis.EXPLICIT_ASSIGNMENT, BusinessEvidenceRole.ASSIGNMENT),
        (FormalTaskBasis.EXPLICIT_COMMITMENT, BusinessEvidenceRole.COMMITMENT),
        (FormalTaskBasis.EXTERNAL_TODO, BusinessEvidenceRole.COMMITMENT),
        (FormalTaskBasis.MEETING_ACTION_ITEM, BusinessEvidenceRole.ASSIGNMENT),
    ],
)
def test_promotion_uses_the_same_formal_basis_evidence_role_as_creation(
    service, formal_basis, evidence_role
):
    recorded = service.record_formal_task(
        RecordFormalTask(
            title="提交报价",
            formality=formality_evidence(formal_basis),
            signal=assignment_signal(dedupe_key="message:formal"),
        )
    )
    candidate = service.record_candidate(
        RecordCandidate(title="提交报价", signal=assignment_signal())
    )
    promoted = service.promote_candidate(
        PromoteCandidate(
            task_id=candidate.task_id,
            formality=formality_evidence(formal_basis),
            signal=assignment_signal(dedupe_key="message:promotion"),
        )
    )

    assert [
        item.evidence_role for item in service.store.list_business_task_evidence(recorded.task_id)
    ] == [evidence_role]
    assert [
        item.evidence_role
        for item in service.store.list_business_task_evidence(promoted.task_id)
        if item.signal_id == promoted.signal_id
    ] == [evidence_role]


def test_service_records_rule_derived_candidate_and_formal_task_states(service):
    candidate = service.record_task_from_evidence(
        RecordTaskFromEvidence(
            title="探索报价范围",
            signal=assignment_signal(dedupe_key="message:candidate"),
            formality=FormalityEvidence(
                basis=None,
                assigner_is_authorized=False,
                deliverable_is_explicit=False,
                owner_is_explicit=False,
            ),
        )
    )
    formal = service.record_task_from_evidence(
        RecordTaskFromEvidence(
            title="提交报价",
            signal=assignment_signal(dedupe_key="message:meeting-action"),
            formality=FormalityEvidence(
                basis=FormalTaskBasis.MEETING_ACTION_ITEM,
                assigner_is_authorized=False,
                deliverable_is_explicit=True,
                owner_is_explicit=False,
            ),
        )
    )

    candidate_task = service.store.get_business_task(candidate.task_id)
    formal_task = service.store.get_business_task(formal.task_id)
    assert candidate_task is not None and formal_task is not None
    assert candidate_task.stage.value == "candidate"
    assert candidate_task.commitment_status is CommitmentStatus.NONE
    assert formal_task.stage.value == "formal"
    assert formal_task.commitment_status is CommitmentStatus.ASSIGNED_UNACCEPTED
    assert formal_task.missing_evidence_json == '["owner"]'


def test_service_rejects_rule_insufficient_assignment_before_persisting(service):
    with pytest.raises(ValueError, match="cannot promote an unauthorized assignment"):
        service.record_task_from_evidence(
            RecordTaskFromEvidence(
                title="提交报价",
                signal=assignment_signal(dedupe_key="message:untrusted-assignment"),
                formality=FormalityEvidence(
                    basis=FormalTaskBasis.EXPLICIT_ASSIGNMENT,
                    assigner_is_authorized=False,
                    deliverable_is_explicit=True,
                    owner_is_explicit=True,
                ),
            )
        )

    assert service.store.list_business_task_signals() == ()
    assert service.store.list_business_tasks() == ()


def test_service_refuses_link_level_identity_evidence_for_merge(service):
    target = record_assignment(service, dedupe_key="message:target")
    source = record_assignment(service, dedupe_key="message:source")

    with pytest.raises(ValueError, match="does not authorize a merge"):
        service.merge_same_deliverable(
            MergeBusinessTasks(
                source_task_id=source.task_id,
                target_task_id=target.task_id,
                signal=assignment_signal(dedupe_key="message:shared-goal"),
                identity_evidence=IdentityEvidence(same_context=True),
            )
        )

    assert service.store.get_business_task(source.task_id).status is BusinessTaskStatus.OPEN
    assert service.store.get_business_task(target.task_id).status is BusinessTaskStatus.OPEN


def test_completing_one_member_does_not_change_an_independent_sibling(service):
    first = record_assignment(service, dedupe_key="message:cluster-member-a")
    second = record_assignment(service, dedupe_key="message:cluster-member-b")

    service.update_task(
        UpdateBusinessTask(
            task_id=first.task_id,
            signal=assignment_signal(dedupe_key="message:complete-a"),
            status=BusinessTaskStatus.DONE,
        )
    )

    assert service.store.get_business_task(first.task_id).status is BusinessTaskStatus.DONE
    assert service.store.get_business_task(second.task_id).status is BusinessTaskStatus.OPEN


def test_merge_without_structured_identity_evidence_rejects_before_signal_persistence(service):
    target = record_assignment(service, dedupe_key="message:target")
    source = record_assignment(service, dedupe_key="message:source")
    state_before = semantic_state(service)

    with pytest.raises(ValueError, match="identity evidence"):
        service.merge_same_deliverable(
            MergeBusinessTasks(
                source_task_id=source.task_id,
                target_task_id=target.task_id,
                signal=assignment_signal(dedupe_key="message:unproven-merge"),
                identity_evidence=None,
            )
        )

    assert semantic_state(service) == state_before


def test_direct_formal_creation_rejects_unauthorized_assignment_before_persisting(service):
    with pytest.raises(ValueError, match="unauthorized assignment"):
        service.record_formal_task(
            RecordFormalTask(
                title="提交报价",
                formality=formality_evidence(FormalTaskBasis.EXPLICIT_ASSIGNMENT, authorized=False),
                signal=assignment_signal(dedupe_key="message:direct-untrusted"),
            )
        )

    assert service.store.list_business_task_signals() == ()
    assert service.store.list_business_tasks() == ()


def test_direct_candidate_promotion_rejects_implicit_deliverable_before_persisting(service):
    candidate = service.record_candidate(
        RecordCandidate(title="报价", signal=assignment_signal())
    )
    state_before = semantic_state(service)

    with pytest.raises(ValueError, match="implicit deliverable"):
        service.promote_candidate(
            PromoteCandidate(
                task_id=candidate.task_id,
                formality=formality_evidence(FormalTaskBasis.EXPLICIT_ASSIGNMENT, deliverable=False),
                signal=assignment_signal(dedupe_key="message:implicit-promotion"),
            )
        )

    assert semantic_state(service) == state_before


def test_direct_meeting_action_promotion_derives_commitment_and_missing_owner(service):
    candidate = service.record_candidate(
        RecordCandidate(title="提交报价", signal=assignment_signal())
    )

    service.promote_candidate(
        PromoteCandidate(
            task_id=candidate.task_id,
            formality=formality_evidence(FormalTaskBasis.MEETING_ACTION_ITEM, owner=False),
            signal=assignment_signal(dedupe_key="message:ownerless-meeting-action"),
        )
    )

    task = service.store.get_business_task(candidate.task_id)
    assert task is not None
    assert task.commitment_status is CommitmentStatus.ASSIGNED_UNACCEPTED
    assert task.missing_evidence_json == '["owner"]'


@pytest.mark.parametrize("operation", ["acceptance", "generic_update"])
def test_candidate_cannot_receive_commitment_transition_and_preserves_state(service, operation):
    candidate = service.record_candidate(
        RecordCandidate(title="提交报价", signal=assignment_signal())
    )
    state_before = semantic_state(service)
    signal = assignment_signal(dedupe_key=f"message:invalid-{operation}")

    with pytest.raises(ValueError, match="formal assigned task|dedicated acceptance"):
        if operation == "acceptance":
            service.apply_acceptance(ApplyAcceptance(task_id=candidate.task_id, signal=signal))
        else:
            service.update_task(
                UpdateBusinessTask(
                    task_id=candidate.task_id,
                    signal=signal,
                    commitment_status=CommitmentStatus.ACCEPTED,
                )
            )

    assert semantic_state(service) == state_before


def test_generic_update_rejects_invalid_formal_commitment_transition_without_mutation(service):
    assigned = record_assignment(service)
    state_before = semantic_state(service)

    with pytest.raises(ValueError, match="dedicated acceptance"):
        service.update_task(
            UpdateBusinessTask(
                task_id=assigned.task_id,
                signal=assignment_signal(dedupe_key="message:generic-accept"),
                commitment_status=CommitmentStatus.ACCEPTED,
            )
        )

    assert semantic_state(service) == state_before


def test_promotion_preserves_unrelated_missing_evidence_and_resolves_only_owner(service):
    candidate = service.record_candidate(
        RecordCandidate(
            title="提交报价",
            signal=assignment_signal(),
            missing_evidence_json='["deadline","owner"]',
        )
    )

    service.promote_candidate(
        PromoteCandidate(
            task_id=candidate.task_id,
            signal=assignment_signal(dedupe_key="message:owner-resolved"),
            formality=formality_evidence(FormalTaskBasis.EXPLICIT_ASSIGNMENT),
            owner_name="王明",
            owner_evidence_json='{"signal_id": 2}',
        )
    )

    task = service.store.get_business_task(candidate.task_id)
    assert task is not None
    assert task.owner_name == "王明"
    assert task.missing_evidence_json == '["deadline"]'


def test_promotion_without_persisted_owner_does_not_false_resolve_owner(service):
    candidate = service.record_candidate(
        RecordCandidate(
            title="提交报价",
            signal=assignment_signal(),
            missing_evidence_json='["deadline"]',
        )
    )

    service.promote_candidate(
        PromoteCandidate(
            task_id=candidate.task_id,
            signal=assignment_signal(dedupe_key="message:missing-owner"),
            formality=formality_evidence(FormalTaskBasis.EXPLICIT_ASSIGNMENT),
        )
    )

    task = service.store.get_business_task(candidate.task_id)
    assert task is not None
    assert task.owner_name == ""
    assert task.owner_evidence_json == "{}"
    assert task.missing_evidence_json == '["deadline","owner"]'
