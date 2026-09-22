from __future__ import annotations

import sqlite3

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
    RecordCandidate,
    RecordFormalTask,
    SourceSignal,
    TaskSemanticService,
)


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


def record_assignment(service: TaskSemanticService, *, dedupe_key: str = "message:assignment"):
    return service.record_formal_task(
        RecordFormalTask(
            title="提交报价",
            formal_basis=FormalTaskBasis.EXPLICIT_ASSIGNMENT,
            commitment_status=CommitmentStatus.ASSIGNED_UNACCEPTED,
            owner_name="王明",
            signal=assignment_signal(dedupe_key=dedupe_key),
        )
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
