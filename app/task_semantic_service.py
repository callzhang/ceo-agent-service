"""Atomic, append-only commands for the Task-first semantic store.

This module deliberately has no API or Task Agent integration.  Its commands
are the only place that combine source observations, Task truth, evidence, and
history in one transaction. A previously collected signal can drive its first
transition; only an existing event makes it a replay. Replays resolve their
original result before inspecting mutable Task state. Fresh changes to a
merged source are rejected before signal persistence. Merges remain one hop: a Task
that already receives merged sources cannot itself become a merged source.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import sqlite3

from app.store import AutoReplyStore
from app.task_semantic_models import (
    BusinessEvidenceRole,
    BusinessRelevance,
    BusinessTask,
    BusinessTaskEventType,
    BusinessTaskStage,
    BusinessTaskStatus,
    CommitmentStatus,
    FormalTaskBasis,
)
from app.task_semantic_rules import (
    FormalityEvidence,
    IdentityEvidence,
    resolve_formality,
    resolve_identity,
)


@dataclass(frozen=True)
class SourceSignal:
    source_type: str
    source_ref: str
    evidence_text: str
    dedupe_key: str
    source_time: str = ""
    conversation_id: str = ""
    conversation_title: str = ""
    author_user_id: str = ""
    author_name: str = ""
    context_json: str = "{}"


@dataclass(frozen=True)
class RecordCandidate:
    title: str
    signal: SourceSignal
    description: str = ""
    owner_user_id: str = ""
    owner_name: str = ""
    deadline_at: str = ""
    missing_evidence_json: str = "[]"


@dataclass(frozen=True)
class RecordFormalTask:
    title: str
    signal: SourceSignal
    formality: FormalityEvidence
    description: str = ""
    owner_user_id: str = ""
    owner_name: str = ""
    owner_evidence_json: str = "{}"
    deadline_at: str = ""


@dataclass(frozen=True)
class RecordTaskFromEvidence:
    title: str
    signal: SourceSignal
    formality: FormalityEvidence
    description: str = ""
    owner_user_id: str = ""
    owner_name: str = ""
    owner_evidence_json: str = "{}"
    deadline_at: str = ""


@dataclass(frozen=True)
class PromoteCandidate:
    task_id: int
    signal: SourceSignal
    formality: FormalityEvidence
    reason: str = "Task now has formal evidence."


@dataclass(frozen=True)
class ApplyAcceptance:
    task_id: int
    signal: SourceSignal
    reason: str = "Task owner accepted the commitment."


@dataclass(frozen=True)
class UpdateBusinessTask:
    task_id: int
    signal: SourceSignal
    commitment_status: CommitmentStatus | None = None
    owner_user_id: str | None = None
    owner_name: str | None = None
    owner_evidence_json: str | None = None
    deadline_at: str | None = None
    status: BusinessTaskStatus | None = None
    business_relevance: BusinessRelevance | None = None
    reason: str = "Task state changed."


@dataclass(frozen=True)
class MergeBusinessTasks:
    source_task_id: int
    target_task_id: int
    signal: SourceSignal
    identity_evidence: IdentityEvidence
    reason: str = "Confirmed as the same deliverable."


@dataclass(frozen=True)
class TaskMutationResult:
    task_id: int
    signal_id: int
    created: bool


class TaskSemanticService:
    def __init__(self, store: AutoReplyStore) -> None:
        self.store = store

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _timestamp(now: datetime) -> str:
        return now.isoformat(timespec="seconds")

    @staticmethod
    def _snapshot(task: BusinessTask) -> str:
        return json.dumps(
            task.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )

    def _signal_id_or_create(self, *, signal: SourceSignal, db: sqlite3.Connection, now: datetime) -> int:
        existing = self.store.get_business_task_signal_by_dedupe_key(
            dedupe_key=signal.dedupe_key, _db=db
        )
        if existing is not None:
            return existing.id
        return self.store.create_business_task_signal_in_transaction(
            source_type=signal.source_type,
            source_ref=signal.source_ref,
            evidence_text=signal.evidence_text,
            dedupe_key=signal.dedupe_key,
            source_time=signal.source_time,
            conversation_id=signal.conversation_id,
            conversation_title=signal.conversation_title,
            author_user_id=signal.author_user_id,
            author_name=signal.author_name,
            context_json=signal.context_json,
            now=now,
            _db=db,
        )

    @staticmethod
    def _require_task(task: BusinessTask | None, task_id: int) -> BusinessTask:
        if task is None:
            raise ValueError(f"business task {task_id} does not exist")
        return task

    @staticmethod
    def _replay_result(*, signal: SourceSignal, db: sqlite3.Connection) -> TaskMutationResult | None:
        # Commands append the result Task's event last, including a merge's
        # target event. Evidence links can later be copied to another Task;
        # immutable event history preserves the original command result.
        row = db.execute(
            """
            select event.task_id, event.signal_id from business_task_events as event
            join business_task_signals as signal on signal.id=event.signal_id
            where signal.dedupe_key=? order by event.id desc limit 1
            """,
            (signal.dedupe_key,),
        ).fetchone()
        if row is None:
            return None
        return TaskMutationResult(
            task_id=int(row["task_id"]), signal_id=int(row["signal_id"]), created=False
        )

    @staticmethod
    def _formal_evidence_role(formal_basis: FormalTaskBasis) -> BusinessEvidenceRole:
        return (
            BusinessEvidenceRole.COMMITMENT
            if formal_basis
            in {FormalTaskBasis.EXPLICIT_COMMITMENT, FormalTaskBasis.EXTERNAL_TODO}
            else BusinessEvidenceRole.ASSIGNMENT
        )

    def _record_new_task(
        self,
        *,
        signal: SourceSignal,
        task_fields: dict[str, object],
        evidence_role: BusinessEvidenceRole,
        reason: str,
    ) -> TaskMutationResult:
        now = self._now()
        with self.store.business_task_transaction() as db:
            replay = self._replay_result(signal=signal, db=db)
            if replay is not None:
                return replay
            signal_id = self._signal_id_or_create(signal=signal, db=db, now=now)
            task_id = self.store.create_business_task_in_transaction(
                now=now, _db=db, **task_fields
            )
            self.store.link_business_task_evidence_in_transaction(
                task_id=task_id, signal_id=signal_id, evidence_role=evidence_role, _db=db
            )
            task = self._require_task(
                self.store.get_business_task_in_transaction(task_id=task_id, _db=db), task_id
            )
            self.store.append_business_task_event(
                task_id=task_id,
                event_type=BusinessTaskEventType.CREATED,
                signal_id=signal_id,
                before_json="{}",
                after_json=self._snapshot(task),
                reason=reason,
                _db=db,
            )
            return TaskMutationResult(task_id=task_id, signal_id=signal_id, created=True)

    def record_candidate(self, command: RecordCandidate) -> TaskMutationResult:
        return self._record_new_task(
            signal=command.signal,
            task_fields={
                "title": command.title,
                "description": command.description,
                "stage": BusinessTaskStage.CANDIDATE,
                "owner_user_id": command.owner_user_id,
                "owner_name": command.owner_name,
                "deadline_at": command.deadline_at,
                "missing_evidence_json": command.missing_evidence_json,
            },
            evidence_role=BusinessEvidenceRole.DISCOVERY,
            reason="Candidate task recorded from source evidence.",
        )

    def record_formal_task(self, command: RecordFormalTask) -> TaskMutationResult:
        if command.formality is None:
            raise ValueError("formal task requires structured formality evidence")
        resolution = resolve_formality(command.formality)
        if resolution.stage is not BusinessTaskStage.FORMAL or command.formality.basis is None:
            raise ValueError("formal task requires formal evidence")
        return self._record_new_task(
            signal=command.signal,
            task_fields={
                "title": command.title,
                "description": command.description,
                "stage": BusinessTaskStage.FORMAL,
                "formal_basis": command.formality.basis,
                "commitment_status": resolution.commitment_status,
                "owner_user_id": command.owner_user_id,
                "owner_name": command.owner_name,
                "owner_evidence_json": command.owner_evidence_json,
                "deadline_at": command.deadline_at,
                "missing_evidence_json": json.dumps(list(resolution.missing_evidence)),
            },
            evidence_role=self._formal_evidence_role(command.formality.basis),
            reason="Formal task recorded from source evidence.",
        )

    def record_task_from_evidence(self, command: RecordTaskFromEvidence) -> TaskMutationResult:
        resolution = resolve_formality(command.formality)
        missing_evidence_json = json.dumps(list(resolution.missing_evidence))
        if resolution.stage is BusinessTaskStage.CANDIDATE:
            return self.record_candidate(
                RecordCandidate(
                    title=command.title,
                    signal=command.signal,
                    description=command.description,
                    owner_user_id=command.owner_user_id,
                    owner_name=command.owner_name,
                    deadline_at=command.deadline_at,
                    missing_evidence_json=missing_evidence_json,
                )
            )
        if command.formality.basis is None:
            raise AssertionError("formal resolution requires a formal basis")
        return self.record_formal_task(
            RecordFormalTask(
                title=command.title,
                signal=command.signal,
                formality=command.formality,
                description=command.description,
                owner_user_id=command.owner_user_id,
                owner_name=command.owner_name,
                owner_evidence_json=command.owner_evidence_json,
                deadline_at=command.deadline_at,
            )
        )

    def promote_candidate(self, command: PromoteCandidate) -> TaskMutationResult:
        if command.formality is None:
            raise ValueError("candidate promotion requires structured formality evidence")
        resolution = resolve_formality(command.formality)
        if resolution.stage is not BusinessTaskStage.FORMAL or command.formality.basis is None:
            raise ValueError("candidate promotion requires formal evidence")
        now = self._now()
        with self.store.business_task_transaction() as db:
            replay = self._replay_result(signal=command.signal, db=db)
            if replay is not None:
                return replay
            task = self._require_task(
                self.store.get_business_task_in_transaction(task_id=command.task_id, _db=db),
                command.task_id,
            )
            if task.status is BusinessTaskStatus.MERGED:
                raise ValueError("cannot promote a merged task")
            if task.stage is not BusinessTaskStage.CANDIDATE:
                raise ValueError("only a candidate task can be promoted")
            signal_id = self._signal_id_or_create(signal=command.signal, db=db, now=now)
            after = task.model_copy(
                update={
                    "stage": BusinessTaskStage.FORMAL,
                    "formal_basis": command.formality.basis,
                    "commitment_status": resolution.commitment_status,
                    "missing_evidence_json": json.dumps(list(resolution.missing_evidence)),
                    "updated_at": self._timestamp(now),
                    "last_activity_at": self._timestamp(now),
                }
            )
            self.store.update_business_task_in_transaction(task=after, _db=db)
            self.store.link_business_task_evidence_in_transaction(
                task_id=task.id,
                signal_id=signal_id,
                evidence_role=self._formal_evidence_role(command.formality.basis),
                _db=db,
            )
            self.store.append_business_task_event(
                task_id=task.id,
                event_type=BusinessTaskEventType.PROMOTED,
                signal_id=signal_id,
                before_json=self._snapshot(task),
                after_json=self._snapshot(after),
                reason=command.reason,
                _db=db,
            )
            return TaskMutationResult(task_id=task.id, signal_id=signal_id, created=False)

    def apply_acceptance(self, command: ApplyAcceptance) -> TaskMutationResult:
        now = self._now()
        with self.store.business_task_transaction() as db:
            replay = self._replay_result(signal=command.signal, db=db)
            if replay is not None:
                return replay
            task = self._require_task(
                self.store.get_business_task_in_transaction(task_id=command.task_id, _db=db),
                command.task_id,
            )
            if task.status is BusinessTaskStatus.MERGED:
                raise ValueError("cannot accept a merged task")
            signal_id = self._signal_id_or_create(signal=command.signal, db=db, now=now)
            after = task.model_copy(
                update={
                    "commitment_status": CommitmentStatus.ACCEPTED,
                    "updated_at": self._timestamp(now),
                    "last_activity_at": self._timestamp(now),
                }
            )
            self.store.update_business_task_in_transaction(task=after, _db=db)
            self.store.link_business_task_evidence_in_transaction(
                task_id=task.id,
                signal_id=signal_id,
                evidence_role=BusinessEvidenceRole.ACCEPTANCE,
                _db=db,
            )
            self.store.append_business_task_event(
                task_id=task.id,
                event_type=BusinessTaskEventType.COMMITMENT_CHANGED,
                signal_id=signal_id,
                before_json=self._snapshot(task),
                after_json=self._snapshot(after),
                reason=command.reason,
                _db=db,
            )
            return TaskMutationResult(task_id=task.id, signal_id=signal_id, created=False)

    def update_task(self, command: UpdateBusinessTask) -> TaskMutationResult:
        now = self._now()
        fields = {
            "commitment_status": command.commitment_status,
            "owner_user_id": command.owner_user_id,
            "owner_name": command.owner_name,
            "owner_evidence_json": command.owner_evidence_json,
            "deadline_at": command.deadline_at,
            "status": command.status,
            "business_relevance": command.business_relevance,
        }
        changed = {name: value for name, value in fields.items() if value is not None}
        if not changed:
            raise ValueError("task update must change at least one field")
        event_type = self._update_event_type(changed)
        with self.store.business_task_transaction() as db:
            replay = self._replay_result(signal=command.signal, db=db)
            if replay is not None:
                return replay
            task = self._require_task(
                self.store.get_business_task_in_transaction(task_id=command.task_id, _db=db),
                command.task_id,
            )
            if task.status is BusinessTaskStatus.MERGED:
                raise ValueError("cannot update a merged task")
            signal_id = self._signal_id_or_create(signal=command.signal, db=db, now=now)
            after = task.model_copy(
                update={
                    **changed,
                    "updated_at": self._timestamp(now),
                    "last_activity_at": self._timestamp(now),
                }
            )
            self.store.update_business_task_in_transaction(task=after, _db=db)
            self.store.link_business_task_evidence_in_transaction(
                task_id=task.id,
                signal_id=signal_id,
                evidence_role=BusinessEvidenceRole.CORRECTION,
                _db=db,
            )
            self.store.append_business_task_event(
                task_id=task.id,
                event_type=event_type,
                signal_id=signal_id,
                before_json=self._snapshot(task),
                after_json=self._snapshot(after),
                reason=command.reason,
                _db=db,
            )
            return TaskMutationResult(task_id=task.id, signal_id=signal_id, created=False)

    @staticmethod
    def _update_event_type(changed: dict[str, object]) -> BusinessTaskEventType:
        if set(changed) == {"commitment_status"}:
            return BusinessTaskEventType.COMMITMENT_CHANGED
        if set(changed) <= {"owner_user_id", "owner_name", "owner_evidence_json"}:
            return BusinessTaskEventType.OWNER_CHANGED
        if set(changed) == {"deadline_at"}:
            return BusinessTaskEventType.DEADLINE_CHANGED
        if set(changed) == {"status"}:
            return BusinessTaskEventType.STATUS_CHANGED
        if set(changed) == {"business_relevance"}:
            return BusinessTaskEventType.RELEVANCE_CHANGED
        raise ValueError("each task update must describe one state transition")

    def merge_same_deliverable(self, command: MergeBusinessTasks) -> TaskMutationResult:
        if command.source_task_id == command.target_task_id:
            raise ValueError("a task cannot merge into itself")
        if (
            command.identity_evidence is None
            or resolve_identity(command.identity_evidence) != "merge"
        ):
            raise ValueError("identity evidence does not authorize a merge")
        now = self._now()
        with self.store.business_task_transaction() as db:
            replay = self._replay_result(signal=command.signal, db=db)
            if replay is not None:
                return replay
            source = self._require_task(
                self.store.get_business_task_in_transaction(task_id=command.source_task_id, _db=db),
                command.source_task_id,
            )
            target = self._require_task(
                self.store.get_business_task_in_transaction(task_id=command.target_task_id, _db=db),
                command.target_task_id,
            )
            if source.status is BusinessTaskStatus.MERGED or target.status is BusinessTaskStatus.MERGED:
                raise ValueError("merge chains and merged targets are not allowed")
            incoming_merge = db.execute(
                "select 1 from business_tasks where merged_into_task_id=? limit 1",
                (source.id,),
            ).fetchone()
            if incoming_merge is not None:
                raise ValueError("merge chains are not allowed")
            signal_id = self._signal_id_or_create(signal=command.signal, db=db, now=now)
            for evidence in self.store.list_business_task_evidence_in_transaction(
                task_id=source.id, _db=db
            ):
                self.store.link_business_task_evidence_in_transaction(
                    task_id=target.id,
                    signal_id=evidence.signal_id,
                    evidence_role=evidence.evidence_role,
                    _db=db,
                )
            self.store.link_business_task_evidence_in_transaction(
                task_id=target.id,
                signal_id=signal_id,
                evidence_role=BusinessEvidenceRole.MERGE_IDENTITY,
                _db=db,
            )
            self.store.link_business_task_evidence_in_transaction(
                task_id=source.id,
                signal_id=signal_id,
                evidence_role=BusinessEvidenceRole.MERGE_IDENTITY,
                _db=db,
            )
            after_source = source.model_copy(
                update={
                    "status": BusinessTaskStatus.MERGED,
                    "merged_into_task_id": target.id,
                    "updated_at": self._timestamp(now),
                    "last_activity_at": self._timestamp(now),
                }
            )
            self.store.update_business_task_in_transaction(task=after_source, _db=db)
            self.store.append_business_task_event(
                task_id=source.id,
                event_type=BusinessTaskEventType.MERGED,
                signal_id=signal_id,
                before_json=self._snapshot(source),
                after_json=self._snapshot(after_source),
                reason=command.reason,
                _db=db,
            )
            self.store.append_business_task_event(
                task_id=target.id,
                event_type=BusinessTaskEventType.MERGED,
                signal_id=signal_id,
                before_json=self._snapshot(target),
                after_json=json.dumps(
                    {"merged_source_task_id": source.id}, separators=(",", ":")
                ),
                reason=command.reason,
                _db=db,
            )
            return TaskMutationResult(task_id=target.id, signal_id=signal_id, created=False)

    def events(self, task_id: int):
        return self.store.list_business_task_events(task_id)
