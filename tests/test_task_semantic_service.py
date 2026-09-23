from __future__ import annotations

import sqlite3
from dataclasses import asdict, replace

import pytest

from app.store import AutoReplyStore
from app.task_semantic_models import (
    BusinessActorKind,
    BusinessTaskDateType,
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
    TaskDateInput,
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
        evidence_text="今天分配王明，周五前提交报价。",
        dedupe_key=dedupe_key,
        author_user_id="derek", author_name="Derek",
        author_kind=BusinessActorKind.HUMAN,
        context_json='{"owner_identity":{"user_id":"wangming","name":"王明"}}',
    )


def dated_assignment_signal(*, dedupe_key: str, phrase: str) -> SourceSignal:
    source = assignment_signal(dedupe_key=dedupe_key)
    return replace(source, evidence_text=f"{source.evidence_text} {phrase}")


def acceptance_signal(*, dedupe_key: str) -> SourceSignal:
    return SourceSignal(
        source_type="dingtalk_message", source_ref=dedupe_key,
        evidence_text="我接受提交报价。", dedupe_key=dedupe_key,
        author_user_id="wangming", author_name="王明",
        author_kind=BusinessActorKind.HUMAN,
    )


def owner_evidence_for(signal: SourceSignal) -> str:
    return f'{{"source_ref":"{signal.source_ref}","excerpt":"王明"}}'


def formal_signal(*, dedupe_key: str, basis: FormalTaskBasis) -> SourceSignal:
    if basis is FormalTaskBasis.EXPLICIT_COMMITMENT:
        return SourceSignal(
            source_type="dingtalk_message", source_ref=dedupe_key,
            evidence_text="王明承诺提交报价。", dedupe_key=dedupe_key,
            author_user_id="wangming", author_name="王明",
            author_kind=BusinessActorKind.HUMAN,
        )
    return assignment_signal(dedupe_key=dedupe_key)


def requested_date(value: str) -> tuple[TaskDateInput, ...]:
    return (TaskDateInput(
        date_type=BusinessTaskDateType.REQUESTED_DEADLINE_AT,
        value_at=value, raw_phrase=value,
        actor_kind=BusinessActorKind.HUMAN, actor_user_id="derek", actor_name="Derek",
    ),)


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
            owner_user_id="wangming",
            owner_name="王明",
            owner_evidence_json=f'{{"source_ref":"{dedupe_key}","excerpt":"王明"}}',
            signal=assignment_signal(dedupe_key=dedupe_key),
        )
    )


def semantic_state(service: TaskSemanticService):
    tasks = service.store.list_business_tasks()
    return (
        service.store.list_business_task_signals(),
        tasks,
        {task.id: service.store.list_business_task_evidence(task.id) for task in tasks},
        {task.id: service.store.list_business_task_date_evidence(task.id) for task in tasks},
        {task.id: service.events(task.id) for task in tasks},
    )


def test_formal_task_requires_source_backed_identified_owner(service):
    command = RecordFormalTask(
        title="提交报价",
        signal=assignment_signal(),
        formality=formality_evidence(FormalTaskBasis.EXPLICIT_ASSIGNMENT),
    )
    with pytest.raises(ValueError, match="owner"):
        service.record_formal_task(command)
    assert semantic_state(service)[0:2] == ((), ())


def test_explicit_commitment_requires_owner_authored_source(service):
    command = RecordFormalTask(
        title="提交报价",
        signal=SourceSignal(
            source_type="dingtalk_message",
            source_ref="message:other-person",
            evidence_text="李丽说王明会提交报价。",
            dedupe_key="message:other-person",
            author_user_id="lili",
            author_name="李丽",
            context_json='{"owner_identity":{"user_id":"wangming","name":"王明"}}',
        ),
        formality=formality_evidence(FormalTaskBasis.EXPLICIT_COMMITMENT),
        owner_user_id="wangming",
        owner_name="王明",
        owner_evidence_json='{"source_ref":"message:other-person","excerpt":"王明"}',
    )
    with pytest.raises(ValueError, match="owner.authored|owner actor"):
        service.record_formal_task(command)
    assert semantic_state(service)[0:2] == ((), ())


def test_external_todo_is_formal_without_owner_acceptance(service):
    result = service.record_formal_task(
        RecordFormalTask(
            title="提交报价",
            signal=SourceSignal(
                source_type="external_todo",
                source_ref="todo:18",
                evidence_text="待办负责人：王明；提交报价。",
                dedupe_key="todo:18",
                author_user_id="system",
                context_json='{"owner_identity":{"user_id":"wangming","name":"王明"}}',
            ),
            formality=formality_evidence(FormalTaskBasis.EXTERNAL_TODO),
            owner_user_id="wangming",
            owner_name="王明",
            owner_evidence_json='{"source_ref":"todo:18","excerpt":"王明"}',
        )
    )
    task = service.store.get_business_task(result.task_id)
    assert task is not None
    assert task.commitment_status is CommitmentStatus.ASSIGNED_UNACCEPTED


def test_acceptance_requires_owner_actor_explicit_content_and_unique_task_link(service):
    initial = service.record_formal_task(
        RecordFormalTask(
            title="提交报价",
            signal=assignment_signal(),
            formality=formality_evidence(FormalTaskBasis.EXPLICIT_ASSIGNMENT),
            owner_user_id="wangming",
            owner_name="王明",
            owner_evidence_json='{"source_ref":"message:assignment","excerpt":"王明"}',
        )
    )
    for author_user_id, explicit, reference, excerpt in (
        ("lili", True, initial.signal_id, "我接受提交报价"),
        ("wangming", False, initial.signal_id, "收到"),
        ("wangming", True, 999, "我接受提交报价"),
        ("wangming", True, initial.signal_id, "源文没有这句"),
    ):
        with pytest.raises(ValueError, match="acceptance|owner|linked"):
            service.apply_acceptance(
                ApplyAcceptance(
                    task_id=initial.task_id,
                    signal=SourceSignal(
                        source_type="dingtalk_message",
                        source_ref=f"reply:{author_user_id}:{explicit}:{reference}",
                        evidence_text="收到" if not explicit else "我接受提交报价",
                        dedupe_key=f"reply:{author_user_id}:{explicit}:{reference}",
                        author_user_id=author_user_id,
                        author_kind=BusinessActorKind.HUMAN,
                    ),
                    acceptance_is_explicit=explicit,
                    referenced_signal_id=reference,
                    acceptance_excerpt=excerpt,
                )
            )
    task = service.store.get_business_task(initial.task_id)
    assert task is not None
    assert task.commitment_status is CommitmentStatus.ASSIGNED_UNACCEPTED


def test_acceptance_rejects_source_link_shared_by_two_open_tasks(service):
    first = service.record_formal_task(
        RecordFormalTask(
            title="提交报价",
            signal=assignment_signal(),
            formality=formality_evidence(FormalTaskBasis.EXPLICIT_ASSIGNMENT),
            owner_user_id="wangming", owner_name="王明",
            owner_evidence_json='{"source_ref":"message:assignment","excerpt":"王明"}',
        )
    )
    second = service.record_formal_task(
        RecordFormalTask(
            title="复核报价",
            signal=assignment_signal(dedupe_key="message:second"),
            formality=formality_evidence(FormalTaskBasis.EXPLICIT_ASSIGNMENT),
            owner_user_id="wangming", owner_name="王明",
            owner_evidence_json='{"source_ref":"message:second","excerpt":"王明"}',
        )
    )
    service.store.link_business_task_evidence(
        task_id=second.task_id, signal_id=first.signal_id, evidence_role="assignment"
    )
    state_before = semantic_state(service)

    with pytest.raises(ValueError, match="unique|multiple"):
        service.apply_acceptance(
            ApplyAcceptance(
                task_id=first.task_id,
                signal=SourceSignal(
                    source_type="dingtalk_message", source_ref="reply:ambiguous",
                    evidence_text="我接受报价工作", dedupe_key="reply:ambiguous",
                    author_user_id="wangming", author_name="王明",
                    author_kind=BusinessActorKind.HUMAN,
                ),
                acceptance_is_explicit=True,
                referenced_signal_id=first.signal_id,
                acceptance_excerpt="我接受报价工作",
            )
        )
    assert semantic_state(service) == state_before


def test_assignment_and_acceptance_keep_distinct_source_backed_date_facts(service):
    from app.task_semantic_models import BusinessActorKind, BusinessTaskDateType
    from app.task_semantic_service import TaskDateInput

    assignment = service.record_formal_task(
        RecordFormalTask(
            title="提交报价",
            signal=assignment_signal(),
            formality=formality_evidence(FormalTaskBasis.EXPLICIT_ASSIGNMENT),
            owner_user_id="wangming", owner_name="王明",
            owner_evidence_json='{"source_ref":"message:assignment","excerpt":"王明"}',
            date_facts=(
                TaskDateInput(
                    date_type=BusinessTaskDateType.ASSIGNED_AT,
                    value_at="2026-09-22T09:00:00+08:00", raw_phrase="今天分配",
                    actor_kind=BusinessActorKind.HUMAN, actor_user_id="derek", actor_name="Derek",
                ),
                TaskDateInput(
                    date_type=BusinessTaskDateType.REQUESTED_DEADLINE_AT,
                    value_at="2026-09-25", raw_phrase="周五前",
                    actor_kind=BusinessActorKind.HUMAN, actor_user_id="derek", actor_name="Derek",
                ),
            ),
        )
    )
    before = service.store.list_business_task_date_evidence(assignment.task_id)
    assert [fact.date_type for fact in before] == [
        BusinessTaskDateType.ASSIGNED_AT,
        BusinessTaskDateType.REQUESTED_DEADLINE_AT,
    ]
    assert all(fact.source_signal_id == assignment.signal_id for fact in before)
    assert all(fact.actor_user_id == "derek" for fact in before)
    assert service.store.get_business_task(assignment.task_id).created_at != before[0].value_at
    assert service.store.get_business_task(assignment.task_id).deadline_at == ""

    accepted = service.apply_acceptance(
        ApplyAcceptance(
            task_id=assignment.task_id,
            signal=SourceSignal(
                source_type="dingtalk_message", source_ref="reply:commit",
                evidence_text="提交报价我接受，周六提交。", dedupe_key="reply:commit",
                author_user_id="wangming", author_name="王明", author_kind=BusinessActorKind.HUMAN,
            ),
            acceptance_is_explicit=True,
            referenced_signal_id=assignment.signal_id,
            acceptance_excerpt="提交报价我接受",
            date_facts=(TaskDateInput(
                date_type=BusinessTaskDateType.COMMITTED_DEADLINE_AT,
                value_at="2026-09-26", raw_phrase="周六提交",
                actor_kind=BusinessActorKind.HUMAN, actor_user_id="wangming", actor_name="王明",
            ),),
        )
    )
    after = service.store.list_business_task_date_evidence(assignment.task_id)
    assert len(after) == 3
    assert after[-1].source_signal_id == accepted.signal_id
    assert after[-1].date_type is BusinessTaskDateType.COMMITTED_DEADLINE_AT
    assert after[-1].actor_user_id == "wangming"
    assert service.store.get_business_task(assignment.task_id).deadline_at == ""


def test_unparseable_estimate_stays_raw_and_generic_update_cannot_commit_deadline(service):
    from app.task_semantic_models import BusinessActorKind, BusinessTaskDateType
    from app.task_semantic_service import TaskDateInput

    task = service.record_candidate(
        RecordCandidate(
            title="评估报价周期", signal=dated_assignment_signal(
                dedupe_key="message:assignment", phrase="尽快，可能下周"
            ),
            date_facts=(TaskDateInput(
                date_type=BusinessTaskDateType.ESTIMATED_DEADLINE_AT,
                value_at="", raw_phrase="尽快，可能下周",
                actor_kind=BusinessActorKind.HUMAN, actor_user_id="derek", actor_name="Derek",
            ),),
        )
    )
    facts = service.store.list_business_task_date_evidence(task.task_id)
    assert facts[0].value_at == ""
    assert facts[0].raw_phrase == "尽快，可能下周"
    with pytest.raises(ValueError, match="committed.*deadline"):
        service.update_task(UpdateBusinessTask(
            task_id=task.task_id,
            signal=SourceSignal(
                source_type="dingtalk_message", source_ref="message:generic-date",
                evidence_text="王明周六提交报价。", dedupe_key="message:generic-date",
                author_kind=BusinessActorKind.HUMAN,
                author_user_id="wangming", author_name="王明",
            ),
            date_facts=(TaskDateInput(
                date_type=BusinessTaskDateType.COMMITTED_DEADLINE_AT,
                value_at="2026-09-26", raw_phrase="周六",
                actor_kind=BusinessActorKind.HUMAN, actor_user_id="wangming", actor_name="王明",
            ),),
        ))
    assert len(service.store.list_business_task_date_evidence(task.task_id)) == 1


def test_semantic_commands_reject_untyped_deadline_writes(service):
    with pytest.raises(ValueError, match="untyped.*deadline"):
        service.record_candidate(RecordCandidate(
            title="报价", signal=assignment_signal(), deadline_at="2026-09-25"
        ))
    with pytest.raises(ValueError, match="untyped.*deadline"):
        service.record_formal_task(RecordFormalTask(
            title="报价", signal=assignment_signal(),
            formality=formality_evidence(FormalTaskBasis.EXPLICIT_ASSIGNMENT),
            owner_user_id="wangming", owner_name="王明",
            owner_evidence_json='{"source_ref":"message:assignment","excerpt":"王明"}',
            deadline_at="2026-09-25",
        ))
    task = service.record_candidate(RecordCandidate(title="报价", signal=assignment_signal()))
    with pytest.raises(ValueError, match="untyped.*deadline"):
        service.update_task(UpdateBusinessTask(
            task_id=task.task_id,
            signal=assignment_signal(dedupe_key="message:deadline"),
            deadline_at="2026-09-25",
        ))
    assert service.store.get_business_task(task.task_id).deadline_at == ""


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
            owner_user_id="wangming", owner_name="王明",
            owner_evidence_json='{"source_ref":"message:assignment","excerpt":"王明"}',
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
    signal = (
        acceptance_signal(dedupe_key="message:transition")
        if operation == "accept" else dated_assignment_signal(
            dedupe_key="message:transition", phrase="2026-09-25T17:00:00Z"
        )
    )
    signal_id = service.store.create_business_task_signal(**asdict(signal))
    original_signal = service.store.get_business_task_signal(signal_id)
    if operation == "promote":
        command = PromoteCandidate(
            task_id=source.task_id, signal=signal,
            formality=formality_evidence(FormalTaskBasis.EXPLICIT_ASSIGNMENT),
            owner_user_id="wangming", owner_name="王明",
            owner_evidence_json=owner_evidence_for(signal),
        )
        transition = service.promote_candidate
    elif operation == "update":
        command = UpdateBusinessTask(
            task_id=source.task_id, signal=signal,
            date_facts=requested_date("2026-09-25T17:00:00Z"),
        )
        transition = service.update_task
    elif operation == "accept":
        command = ApplyAcceptance(
            task_id=source.task_id, signal=signal,
            acceptance_is_explicit=True, acceptance_excerpt="我接受提交报价",
            referenced_signal_id=source.signal_id,
        )
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
                    owner_user_id="wangming", owner_name="王明",
                    owner_evidence_json='{"source_ref":"message:fresh-promotion","excerpt":"王明"}',
                )
            )
        else:
            service.update_task(
                UpdateBusinessTask(
                    task_id=source.task_id,
                    date_facts=requested_date("2026-09-25T17:00:00Z"),
                    signal=dated_assignment_signal(
                        dedupe_key="message:fresh-deadline", phrase="2026-09-25T17:00:00Z"
                    ),
                )
            )

    assert semantic_state(service) == state_before


@pytest.mark.parametrize("operation", ["promote", "update"])
def test_transition_replay_still_succeeds_after_source_is_merged(service, operation):
    source = service.record_candidate(
        RecordCandidate(title="提交报价", signal=assignment_signal())
    )
    signal = assignment_signal(dedupe_key="message:transition")
    if operation == "update":
        signal = dated_assignment_signal(
            dedupe_key="message:transition", phrase="2026-09-25T17:00:00Z"
        )
    if operation == "promote":
        command = PromoteCandidate(
            task_id=source.task_id, signal=signal,
            formality=formality_evidence(FormalTaskBasis.EXPLICIT_ASSIGNMENT),
            owner_user_id="wangming", owner_name="王明",
            owner_evidence_json=owner_evidence_for(signal),
        )
        transition = service.promote_candidate
    else:
        command = UpdateBusinessTask(
            task_id=source.task_id, signal=signal,
            date_facts=requested_date("2026-09-25T17:00:00Z"),
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
                evidence_text="我接受提交报价，会在周五前完成。",
                dedupe_key="message:acceptance",
                author_user_id="wangming", author_name="王明",
                author_kind=BusinessActorKind.HUMAN,
            ),
            acceptance_is_explicit=True,
            acceptance_excerpt="我接受提交报价",
            referenced_signal_id=initial.signal_id,
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
                    evidence_text="我接受提交报价，周六提交。",
                    dedupe_key="message:rollback",
                    author_user_id="wangming", author_name="王明",
                    author_kind=BusinessActorKind.HUMAN,
                ),
                acceptance_is_explicit=True,
                acceptance_excerpt="我接受提交报价",
                referenced_signal_id=initial.signal_id,
                date_facts=(TaskDateInput(
                    date_type=BusinessTaskDateType.COMMITTED_DEADLINE_AT,
                    value_at="2026-09-26", raw_phrase="周六提交",
                    actor_kind=BusinessActorKind.HUMAN, actor_user_id="wangming", actor_name="王明",
                ),),
            )
        )

    task = service.store.get_business_task(initial.task_id)
    assert task is not None
    assert task.commitment_status is CommitmentStatus.ASSIGNED_UNACCEPTED
    assert service.store.get_business_task_signal(2) is None
    assert service.store.list_business_task_date_evidence(initial.task_id) == ()
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
        owner_user_id="wangming", owner_name="王明",
        owner_evidence_json='{"source_ref":"message:promotion","excerpt":"王明"}',
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
            owner_user_id="wangming", owner_name="王明",
            owner_evidence_json='{"source_ref":"message:assignment","excerpt":"王明"}',
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
        signal=acceptance_signal(dedupe_key="message:acceptance"),
        acceptance_is_explicit=True,
        acceptance_excerpt="我接受提交报价",
        referenced_signal_id=task.signal_id,
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
        date_facts=requested_date("2026-09-25T17:00:00Z"),
        signal=dated_assignment_signal(
            dedupe_key="message:first-deadline", phrase="2026-09-25T17:00:00Z"
        ),
    )
    first = service.update_task(command)
    service.update_task(
        UpdateBusinessTask(
            task_id=task.task_id,
            date_facts=requested_date("2026-09-28T17:00:00Z"),
            signal=dated_assignment_signal(
                dedupe_key="message:later-deadline", phrase="2026-09-28T17:00:00Z"
            ),
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
        (FormalTaskBasis.EXTERNAL_TODO, BusinessEvidenceRole.ASSIGNMENT),
        (FormalTaskBasis.MEETING_ACTION_ITEM, BusinessEvidenceRole.ASSIGNMENT),
    ],
)
def test_promotion_uses_the_same_formal_basis_evidence_role_as_creation(
    service, formal_basis, evidence_role
):
    creation_signal = formal_signal(dedupe_key="message:formal", basis=formal_basis)
    recorded = service.record_formal_task(
        RecordFormalTask(
            title="提交报价",
            formality=formality_evidence(formal_basis),
            signal=creation_signal,
            owner_user_id="wangming", owner_name="王明",
            owner_evidence_json=owner_evidence_for(creation_signal),
        )
    )
    candidate = service.record_candidate(
        RecordCandidate(title="提交报价", signal=assignment_signal())
    )
    promotion_signal = formal_signal(dedupe_key="message:promotion", basis=formal_basis)
    promoted = service.promote_candidate(
        PromoteCandidate(
            task_id=candidate.task_id,
            formality=formality_evidence(formal_basis),
            signal=promotion_signal,
            owner_user_id="wangming", owner_name="王明",
            owner_evidence_json=owner_evidence_for(promotion_signal),
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


def test_service_records_candidate_and_rejects_ownerless_formal_action(service):
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
    with pytest.raises(ValueError, match="owner"):
        service.record_task_from_evidence(
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
    assert candidate_task is not None
    assert candidate_task.stage.value == "candidate"
    assert candidate_task.commitment_status is CommitmentStatus.NONE
    assert len(service.store.list_business_tasks()) == 1


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


def test_direct_meeting_action_promotion_rejects_missing_owner(service):
    candidate = service.record_candidate(
        RecordCandidate(title="提交报价", signal=assignment_signal())
    )

    with pytest.raises(ValueError, match="owner"):
        service.promote_candidate(
            PromoteCandidate(
                task_id=candidate.task_id,
                formality=formality_evidence(FormalTaskBasis.MEETING_ACTION_ITEM, owner=False),
                signal=assignment_signal(dedupe_key="message:ownerless-meeting-action"),
            )
        )

    task = service.store.get_business_task(candidate.task_id)
    assert task is not None
    assert task.stage.value == "candidate"
    assert task.commitment_status is CommitmentStatus.NONE


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


def test_promotion_preserves_unrelated_missing_evidence_and_resolves_owner(service):
    candidate = service.record_candidate(
        RecordCandidate(
            title="提交报价",
            signal=assignment_signal(),
            missing_evidence_json='["approval","owner"]',
        )
    )

    service.promote_candidate(
        PromoteCandidate(
            task_id=candidate.task_id,
            signal=assignment_signal(dedupe_key="message:owner-resolved"),
            formality=formality_evidence(FormalTaskBasis.EXPLICIT_ASSIGNMENT),
            owner_user_id="wangming", owner_name="王明",
            owner_evidence_json='{"source_ref":"message:owner-resolved","excerpt":"王明"}',
        )
    )

    task = service.store.get_business_task(candidate.task_id)
    assert task is not None
    assert task.owner_name == "王明"
    assert task.missing_evidence_json == '["approval"]'


def test_promotion_without_persisted_owner_stays_candidate(service):
    candidate = service.record_candidate(
        RecordCandidate(
            title="提交报价",
            signal=assignment_signal(),
            missing_evidence_json='["approval"]',
        )
    )

    with pytest.raises(ValueError, match="owner"):
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
    assert task.missing_evidence_json == '["approval"]'
    assert task.stage.value == "candidate"


def test_promotion_rejects_changed_owner_identity_without_new_matching_evidence(service):
    candidate = service.record_candidate(
        RecordCandidate(
            title="提交报价",
            signal=assignment_signal(),
            owner_name="Alice",
            missing_evidence_json='["owner"]',
        )
    )
    service.update_task(
        UpdateBusinessTask(
            task_id=candidate.task_id,
            signal=SourceSignal(
                source_type="dingtalk_message", source_ref="message:alice-evidence",
                evidence_text="Alice 确认负责提交报价。", dedupe_key="message:alice-evidence",
                author_user_id="alice", author_name="Alice",
                author_kind=BusinessActorKind.HUMAN,
            ),
            owner_evidence_json='{"source_ref":"message:alice-evidence","excerpt":"Alice"}',
        )
    )
    state_before = semantic_state(service)

    with pytest.raises(ValueError, match="changed owner requires new owner evidence"):
        service.promote_candidate(
            PromoteCandidate(
                task_id=candidate.task_id,
                signal=assignment_signal(dedupe_key="message:bob-promotion"),
                formality=formality_evidence(FormalTaskBasis.EXPLICIT_ASSIGNMENT),
                owner_name="Bob",
            )
        )

    assert semantic_state(service) == state_before


def test_promotion_keeps_unchanged_owner_and_existing_evidence(service):
    candidate = service.record_candidate(
        RecordCandidate(title="提交报价", signal=assignment_signal(), owner_name="Alice")
    )
    service.update_task(
        UpdateBusinessTask(
            task_id=candidate.task_id,
            signal=SourceSignal(
                source_type="dingtalk_message", source_ref="message:alice-evidence",
                evidence_text="Alice 确认负责提交报价。", dedupe_key="message:alice-evidence",
                author_user_id="alice", author_name="Alice",
                author_kind=BusinessActorKind.HUMAN,
            ),
            owner_evidence_json='{"source_ref":"message:alice-evidence","excerpt":"Alice"}',
        )
    )

    service.promote_candidate(
        PromoteCandidate(
            task_id=candidate.task_id,
            signal=SourceSignal(
                source_type="dingtalk_message", source_ref="message:alice-promotion",
                evidence_text="继续执行 Alice 的报价任务。", dedupe_key="message:alice-promotion",
            ),
            formality=formality_evidence(FormalTaskBasis.EXPLICIT_ASSIGNMENT),
            owner_name="Alice",
        )
    )

    task = service.store.get_business_task(candidate.task_id)
    assert task is not None
    assert task.owner_name == "Alice"
    assert task.owner_evidence_json == '{"source_ref":"message:alice-evidence","excerpt":"Alice"}'
    assert task.missing_evidence_json == "[]"


def test_reused_dedupe_key_cannot_change_persisted_source_identity(service):
    source = assignment_signal(dedupe_key="message:canonical")
    service.store.create_business_task_signal(**asdict(source))
    forged = SourceSignal(
        source_type=source.source_type, source_ref=source.source_ref,
        evidence_text="王明承诺提交报价。", dedupe_key=source.dedupe_key,
        author_kind=BusinessActorKind.HUMAN, author_user_id="wangming", author_name="王明",
    )
    with pytest.raises(ValueError, match="dedupe|source.*mismatch"):
        service.record_formal_task(RecordFormalTask(
            title="提交报价", signal=forged,
            formality=formality_evidence(FormalTaskBasis.EXPLICIT_COMMITMENT),
            owner_user_id="wangming", owner_name="王明",
            owner_evidence_json=owner_evidence_for(forged),
        ))
    assert service.store.list_business_tasks() == ()


def test_owner_id_requires_source_identity_mapping(service):
    source = SourceSignal(
        source_type="dingtalk_message", source_ref="message:owner-mismatch",
        evidence_text="王明负责提交报价。", dedupe_key="message:owner-mismatch",
    )
    with pytest.raises(ValueError, match="owner.*identity|owner.*ID"):
        service.record_formal_task(RecordFormalTask(
            title="提交报价", signal=source,
            formality=formality_evidence(FormalTaskBasis.EXPLICIT_ASSIGNMENT),
            owner_user_id="lili", owner_name="王明",
            owner_evidence_json=owner_evidence_for(source),
        ))
    assert service.store.list_business_tasks() == ()


def test_cooccurring_name_and_unrelated_user_id_are_not_a_mapping(service):
    source = SourceSignal(
        source_type="dingtalk_message", source_ref="message:two-people",
        evidence_text="李丽 lili 转告王明负责提交报价。", dedupe_key="message:two-people",
    )
    with pytest.raises(ValueError, match="owner ID requires source identity mapping"):
        service.record_formal_task(RecordFormalTask(
            title="提交报价", signal=source,
            formality=formality_evidence(FormalTaskBasis.EXPLICIT_ASSIGNMENT),
            owner_user_id="lili", owner_name="王明",
            owner_evidence_json=(
                '{"source_ref":"message:two-people",'
                '"excerpt":"李丽 lili 转告王明负责提交报价。"}'
            ),
        ))


def test_name_only_source_owner_stays_unaccepted_until_identity_resolved(service):
    source = SourceSignal(
        source_type="dingtalk_message", source_ref="message:name-only",
        evidence_text="王明负责提交报价。", dedupe_key="message:name-only",
    )
    task = service.record_formal_task(RecordFormalTask(
        title="提交报价", signal=source,
        formality=formality_evidence(FormalTaskBasis.EXPLICIT_ASSIGNMENT),
        owner_name="王明", owner_evidence_json=owner_evidence_for(source),
    ))
    assert service.store.get_business_task(task.task_id).owner_user_id == ""
    with pytest.raises(ValueError, match="identified owner actor"):
        service.apply_acceptance(ApplyAcceptance(
            task_id=task.task_id,
            signal=acceptance_signal(dedupe_key="reply:name-only"),
            acceptance_is_explicit=True,
            acceptance_excerpt="我接受提交报价",
            referenced_signal_id=task.signal_id,
        ))


def test_generic_owner_update_requires_new_source_evidence(service):
    task = record_assignment(service)
    before = semantic_state(service)
    with pytest.raises(ValueError, match="owner.*evidence|owner.*source"):
        service.update_task(UpdateBusinessTask(
            task_id=task.task_id,
            signal=SourceSignal(
                source_type="dingtalk_message", source_ref="message:unrelated-owner",
                evidence_text="报价仍需讨论。", dedupe_key="message:unrelated-owner",
            ),
            owner_user_id="lili", owner_name="李丽",
            owner_evidence_json='{"source_ref":"message:unrelated-owner","excerpt":"李丽"}',
        ))
    assert semantic_state(service) == before
    with pytest.raises(ValueError, match="identified owner actor"):
        service.apply_acceptance(ApplyAcceptance(
            task_id=task.task_id,
            signal=SourceSignal(
                source_type="dingtalk_message", source_ref="reply:injected-owner",
                evidence_text="我接受提交报价。", dedupe_key="reply:injected-owner",
                author_kind=BusinessActorKind.HUMAN,
                author_user_id="lili", author_name="李丽",
            ),
            acceptance_is_explicit=True,
            acceptance_excerpt="我接受提交报价",
            referenced_signal_id=task.signal_id,
        ))
    assert service.store.get_business_task(task.task_id).owner_user_id == "wangming"


def test_preexisting_unknown_signal_cannot_be_recast_as_owner_acceptance(service):
    task = record_assignment(service)
    service.store.create_business_task_signal(
        source_type="dingtalk_message", source_ref="reply:unknown",
        evidence_text="我接受提交报价。", dedupe_key="reply:unknown",
        author_kind=BusinessActorKind.UNKNOWN,
    )
    before = semantic_state(service)
    with pytest.raises(ValueError, match="dedupe|source.*mismatch"):
        service.apply_acceptance(ApplyAcceptance(
            task_id=task.task_id,
            signal=SourceSignal(
                source_type="dingtalk_message", source_ref="reply:unknown",
                evidence_text="我接受提交报价。", dedupe_key="reply:unknown",
                author_kind=BusinessActorKind.HUMAN,
                author_user_id="wangming", author_name="王明",
            ),
            acceptance_is_explicit=True,
            acceptance_excerpt="我接受提交报价",
            referenced_signal_id=task.signal_id,
        ))
    assert semantic_state(service) == before


@pytest.mark.parametrize("raw_phrase,actor_user_id", [
    ("下周五", "derek"),
    ("周五前", "lili"),
])
def test_date_fact_must_quote_source_and_its_actor(service, raw_phrase, actor_user_id):
    before = semantic_state(service)
    with pytest.raises(ValueError, match="date.*source|date.*actor"):
        service.record_candidate(RecordCandidate(
            title="提交报价",
            signal=SourceSignal(
                source_type="dingtalk_message", source_ref="message:date-proof",
                evidence_text="王明周五前提交报价。", dedupe_key="message:date-proof",
                author_kind=BusinessActorKind.HUMAN, author_user_id="derek",
            ),
            date_facts=(TaskDateInput(
                date_type=BusinessTaskDateType.REQUESTED_DEADLINE_AT,
                value_at="2026-09-25", raw_phrase=raw_phrase,
                actor_kind=BusinessActorKind.HUMAN, actor_user_id=actor_user_id,
            ),),
        ))
    assert semantic_state(service) == before


def test_agent_next_check_can_be_derived_from_quoted_human_source(service):
    result = service.record_candidate(RecordCandidate(
        title="提交报价", signal=assignment_signal(dedupe_key="message:next-check"),
        date_facts=(TaskDateInput(
            date_type=BusinessTaskDateType.NEXT_CHECK_AT,
            value_at="2026-09-25", raw_phrase="周五前",
            actor_kind=BusinessActorKind.AGENT, actor_user_id="ceo-agent-service",
        ),),
    ))
    date = service.store.list_business_task_date_evidence(result.task_id)[0]
    assert date.date_type is BusinessTaskDateType.NEXT_CHECK_AT
    assert date.actor_kind is BusinessActorKind.AGENT
    assert date.actor_user_id == "ceo-agent-service"


def test_acceptance_excerpt_must_name_the_target_deliverable(service):
    task = record_assignment(service)
    before = semantic_state(service)
    with pytest.raises(ValueError, match="acceptance.*task|acceptance.*deliverable"):
        service.apply_acceptance(ApplyAcceptance(
            task_id=task.task_id,
            signal=SourceSignal(
                source_type="dingtalk_message", source_ref="reply:other-task",
                evidence_text="我接受复核合同。", dedupe_key="reply:other-task",
                author_kind=BusinessActorKind.HUMAN,
                author_user_id="wangming", author_name="王明",
            ),
            acceptance_is_explicit=True,
            acceptance_excerpt="我接受复核合同",
            referenced_signal_id=task.signal_id,
        ))
    assert semantic_state(service) == before
