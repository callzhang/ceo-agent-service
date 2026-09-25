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
from enum import StrEnum
import json
import sqlite3
from contextlib import nullcontext

from app.store import AutoReplyStore
from app.task_semantic_models import (
    BusinessActorKind,
    BusinessEvidenceRole,
    BusinessRelevance,
    BusinessTask,
    BusinessTaskSignal,
    BusinessTaskEventType,
    BusinessTaskDateType,
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
    author_kind: BusinessActorKind = BusinessActorKind.UNKNOWN
    context_json: str = "{}"


@dataclass(frozen=True)
class TaskDateInput:
    date_type: BusinessTaskDateType
    value_at: str
    raw_phrase: str
    actor_kind: BusinessActorKind
    actor_user_id: str = ""
    actor_name: str = ""


class AcceptancePolarity(StrEnum):
    ACCEPTED = "accepted"
    DECLINED = "declined"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True)
class RecordCandidate:
    title: str
    signal: SourceSignal
    description: str = ""
    owner_user_id: str = ""
    owner_name: str = ""
    deadline_at: str = ""
    missing_evidence_json: str = "[]"
    date_facts: tuple[TaskDateInput, ...] = ()


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
    date_facts: tuple[TaskDateInput, ...] = ()


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
    date_facts: tuple[TaskDateInput, ...] = ()


@dataclass(frozen=True)
class PromoteCandidate:
    task_id: int
    signal: SourceSignal
    formality: FormalityEvidence
    owner_user_id: str | None = None
    owner_name: str | None = None
    owner_evidence_json: str | None = None
    reason: str = "任务已有正式依据。"
    date_facts: tuple[TaskDateInput, ...] = ()


@dataclass(frozen=True)
class ApplyAcceptance:
    task_id: int
    signal: SourceSignal
    acceptance_is_explicit: bool = False
    acceptance_polarity: AcceptancePolarity = AcceptancePolarity.AMBIGUOUS
    acceptance_excerpt: str = ""
    referenced_signal_id: int | None = None
    reason: str = "负责人接受了这项承诺。"
    date_facts: tuple[TaskDateInput, ...] = ()


@dataclass(frozen=True)
class UpdateBusinessTask:
    task_id: int
    signal: SourceSignal
    title: str | None = None
    description: str | None = None
    commitment_status: CommitmentStatus | None = None
    owner_user_id: str | None = None
    owner_name: str | None = None
    owner_evidence_json: str | None = None
    deadline_at: str | None = None
    status: BusinessTaskStatus | None = None
    business_relevance: BusinessRelevance | None = None
    reason: str = "任务状态发生变化。"
    date_facts: tuple[TaskDateInput, ...] = ()


@dataclass(frozen=True)
class MergeBusinessTasks:
    source_task_id: int
    target_task_id: int
    signal: SourceSignal
    identity_evidence: IdentityEvidence
    reason: str = "确认为同一交付物。"


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
            self._require_same_signal(signal=signal, persisted=existing)
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
            author_kind=signal.author_kind,
            context_json=signal.context_json,
            now=now,
            _db=db,
        )

    @staticmethod
    def _require_same_signal(*, signal: SourceSignal, persisted: BusinessTaskSignal) -> None:
        fields = (
            "source_type", "source_ref", "source_time", "conversation_id",
            "conversation_title", "author_user_id", "author_name", "author_kind",
            "evidence_text", "context_json", "dedupe_key",
        )
        if any(getattr(signal, field) != getattr(persisted, field) for field in fields):
            raise ValueError("signal dedupe key source payload mismatch")

    @staticmethod
    def _require_task(task: BusinessTask | None, task_id: int) -> BusinessTask:
        if task is None:
            raise ValueError(f"business task {task_id} does not exist")
        return task

    def _replay_result(self, *, signal: SourceSignal, db: sqlite3.Connection) -> TaskMutationResult | None:
        persisted = self.store.get_business_task_signal_by_dedupe_key(
            dedupe_key=signal.dedupe_key, _db=db
        )
        if persisted is not None:
            self._require_same_signal(signal=signal, persisted=persisted)
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
            if formal_basis is FormalTaskBasis.EXPLICIT_COMMITMENT
            else BusinessEvidenceRole.ASSIGNMENT
        )

    @staticmethod
    def _owner_is_persisted(
        *, owner_user_id: str, owner_name: str, owner_evidence_json: str
    ) -> bool:
        try:
            owner_evidence = json.loads(owner_evidence_json)
        except json.JSONDecodeError as exc:
            raise ValueError("owner evidence must be JSON") from exc
        if not isinstance(owner_evidence, dict):
            raise ValueError("owner evidence must be an object")
        return bool(owner_user_id.strip() or owner_name.strip()) and bool(owner_evidence)

    @staticmethod
    def _require_source_backed_owner(
        *, signal: SourceSignal | BusinessTaskSignal, owner_user_id: str, owner_name: str,
        owner_evidence_json: str,
    ) -> None:
        if not (owner_user_id.strip() or owner_name.strip()):
            raise ValueError("formal task requires an identified owner")
        try:
            evidence = json.loads(owner_evidence_json)
        except json.JSONDecodeError as exc:
            raise ValueError("owner evidence must be JSON") from exc
        if not isinstance(evidence, dict):
            raise ValueError("owner evidence must be an object")
        excerpt = evidence.get("excerpt")
        if evidence.get("source_ref") != signal.source_ref or not isinstance(excerpt, str):
            raise ValueError("owner evidence must cite the source")
        if not excerpt.strip() or excerpt not in signal.evidence_text:
            raise ValueError("owner evidence excerpt must occur in the source")
        if owner_name and owner_name not in excerpt:
            raise ValueError("owner identity must appear in its source evidence excerpt")
        if owner_user_id:
            context = json.loads(signal.context_json)
            mapped = context.get("owner_identity", {})
            if not isinstance(mapped, dict):
                mapped = {}
            author_matches = (
                signal.author_kind is BusinessActorKind.HUMAN
                and signal.author_user_id == owner_user_id
                and (not owner_name or signal.author_name == owner_name)
            )
            context_matches = (
                mapped.get("user_id") == owner_user_id
                and (not owner_name or mapped.get("name") == owner_name)
            )
            text_id_only = not owner_name and owner_user_id in excerpt
            if not text_id_only and not author_matches and not context_matches:
                raise ValueError("owner ID requires source identity mapping")
        elif not owner_name or owner_name not in excerpt:
            raise ValueError("owner identity must appear in its source evidence excerpt")

    @staticmethod
    def _require_owner_authored_commitment(*, signal: SourceSignal, owner_user_id: str) -> None:
        if (
            not owner_user_id.strip()
            or signal.author_kind is not BusinessActorKind.HUMAN
            or signal.author_user_id != owner_user_id
        ):
            raise ValueError("explicit commitment requires an owner-authored source")

    @staticmethod
    def _validate_date_facts(
        date_facts: tuple[TaskDateInput, ...], *, signal: SourceSignal,
        may_commit: bool, owner_user_id: str = ""
    ) -> None:
        for fact in date_facts:
            source_timestamp = (
                fact.date_type is BusinessTaskDateType.ASSIGNED_AT
                and bool(signal.source_time)
                and fact.raw_phrase == signal.source_time
                and fact.value_at == signal.source_time
            )
            if not fact.raw_phrase.strip() or (
                fact.raw_phrase not in signal.evidence_text and not source_timestamp
            ):
                raise ValueError("date phrase must occur in its source")
            agent_authored_date = (
                fact.date_type in {
                    BusinessTaskDateType.NEXT_CHECK_AT,
                    BusinessTaskDateType.ESTIMATED_DEADLINE_AT,
                }
                and fact.actor_kind is BusinessActorKind.AGENT
                and bool(fact.actor_user_id.strip())
            )
            if not (source_timestamp or agent_authored_date) and (
                fact.actor_kind is not signal.author_kind
                or fact.actor_user_id != signal.author_user_id
                or fact.actor_name != signal.author_name
            ):
                raise ValueError("date actor must match its source actor")
            if fact.date_type is BusinessTaskDateType.COMMITTED_DEADLINE_AT:
                if not may_commit:
                    raise ValueError("committed deadline requires owner acceptance")
                if (
                    fact.actor_kind is not BusinessActorKind.HUMAN
                    or not owner_user_id.strip()
                    or fact.actor_user_id != owner_user_id
                ):
                    raise ValueError("committed deadline requires the owner actor")

    def _record_date_facts(
        self, *, task_id: int, signal_id: int, date_facts: tuple[TaskDateInput, ...],
        db: sqlite3.Connection,
    ) -> None:
        for fact in date_facts:
            self.store.create_business_task_date_evidence_in_transaction(
                task_id=task_id,
                source_signal_id=signal_id,
                date_type=fact.date_type,
                value_at=fact.value_at,
                raw_phrase=fact.raw_phrase,
                actor_kind=fact.actor_kind,
                actor_user_id=fact.actor_user_id,
                actor_name=fact.actor_name,
                _db=db,
            )

    @staticmethod
    def _reconcile_missing_owner(*, missing_evidence_json: str, owner_is_persisted: bool) -> str:
        missing_evidence = json.loads(missing_evidence_json)
        if not isinstance(missing_evidence, list) or not all(
            isinstance(item, str) for item in missing_evidence
        ):
            raise ValueError("missing evidence must be a JSON string list")
        remaining = [item for item in missing_evidence if item != "owner"]
        if not owner_is_persisted:
            remaining.append("owner")
        return json.dumps(list(dict.fromkeys(remaining)), separators=(",", ":"))

    def _record_new_task(
        self,
        *,
        signal: SourceSignal,
        task_fields: dict[str, object],
        evidence_role: BusinessEvidenceRole,
        reason: str,
        date_facts: tuple[TaskDateInput, ...] = (),
        _db: sqlite3.Connection | None = None,
    ) -> TaskMutationResult:
        now = self._now()
        transaction = (
            nullcontext(_db)
            if _db is not None
            else self.store.business_task_transaction()
        )
        with transaction as db:
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
            self._record_date_facts(
                task_id=task_id, signal_id=signal_id, date_facts=date_facts, db=db
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

    def record_candidate(
        self, command: RecordCandidate, *, _db: sqlite3.Connection | None = None
    ) -> TaskMutationResult:
        if command.deadline_at:
            raise ValueError("untyped deadline is legacy; use date_facts")
        self._validate_date_facts(command.date_facts, signal=command.signal, may_commit=False)
        return self._record_new_task(
            signal=command.signal,
            task_fields={
                "title": command.title,
                "description": command.description,
                "stage": BusinessTaskStage.CANDIDATE,
                "owner_user_id": command.owner_user_id,
                "owner_name": command.owner_name,
                "missing_evidence_json": command.missing_evidence_json,
            },
            evidence_role=BusinessEvidenceRole.DISCOVERY,
            reason="根据来源证据记录为候选任务。",
            date_facts=command.date_facts,
            _db=_db,
        )

    def record_formal_task(
        self, command: RecordFormalTask, *, _db: sqlite3.Connection | None = None
    ) -> TaskMutationResult:
        if command.deadline_at:
            raise ValueError("untyped deadline is legacy; use date_facts")
        if command.formality is None:
            raise ValueError("formal task requires structured formality evidence")
        resolution = resolve_formality(command.formality)
        if resolution.stage is not BusinessTaskStage.FORMAL or command.formality.basis is None:
            raise ValueError("formal task requires formal evidence")
        self._require_source_backed_owner(
            signal=command.signal,
            owner_user_id=command.owner_user_id,
            owner_name=command.owner_name,
            owner_evidence_json=command.owner_evidence_json,
        )
        if command.formality.basis is FormalTaskBasis.EXPLICIT_COMMITMENT:
            self._require_owner_authored_commitment(
                signal=command.signal, owner_user_id=command.owner_user_id
            )
        self._validate_date_facts(
            command.date_facts,
            signal=command.signal,
            may_commit=command.formality.basis is FormalTaskBasis.EXPLICIT_COMMITMENT,
            owner_user_id=command.owner_user_id,
        )
        owner_is_persisted = self._owner_is_persisted(
            owner_user_id=command.owner_user_id,
            owner_name=command.owner_name,
            owner_evidence_json=command.owner_evidence_json,
        )
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
                "missing_evidence_json": self._reconcile_missing_owner(
                    missing_evidence_json="[]", owner_is_persisted=owner_is_persisted
                ),
            },
            evidence_role=self._formal_evidence_role(command.formality.basis),
            reason="根据来源证据记录为正式任务。",
            date_facts=command.date_facts,
            _db=_db,
        )

    def record_task_from_evidence(
        self, command: RecordTaskFromEvidence, *, _db: sqlite3.Connection | None = None
    ) -> TaskMutationResult:
        if command.deadline_at:
            raise ValueError("untyped deadline is legacy; use date_facts")
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
                    missing_evidence_json=missing_evidence_json,
                    date_facts=command.date_facts,
                ), _db=_db
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
                date_facts=command.date_facts,
            ), _db=_db
        )

    def promote_candidate(
        self, command: PromoteCandidate, *, _db: sqlite3.Connection | None = None
    ) -> TaskMutationResult:
        if command.formality is None:
            raise ValueError("candidate promotion requires structured formality evidence")
        resolution = resolve_formality(command.formality)
        if resolution.stage is not BusinessTaskStage.FORMAL or command.formality.basis is None:
            raise ValueError("candidate promotion requires formal evidence")
        now = self._now()
        transaction = (
            nullcontext(_db)
            if _db is not None
            else self.store.business_task_transaction()
        )
        with transaction as db:
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
            owner_user_id = (
                task.owner_user_id
                if command.owner_user_id is None
                else command.owner_user_id
            )
            owner_name = task.owner_name if command.owner_name is None else command.owner_name
            owner_evidence_json = (
                task.owner_evidence_json
                if command.owner_evidence_json is None
                else command.owner_evidence_json
            )
            owner_identity_changed = (
                command.owner_user_id is not None
                and command.owner_user_id != task.owner_user_id
            ) or (command.owner_name is not None and command.owner_name != task.owner_name)
            if owner_identity_changed and (
                command.owner_evidence_json is None
                or not self._owner_is_persisted(
                    owner_user_id=owner_user_id,
                    owner_name=owner_name,
                    owner_evidence_json=command.owner_evidence_json,
                )
            ):
                raise ValueError("changed owner requires new owner evidence")
            owner_is_persisted = self._owner_is_persisted(
                owner_user_id=owner_user_id,
                owner_name=owner_name,
                owner_evidence_json=owner_evidence_json,
            )
            cited_source_ref = json.loads(owner_evidence_json).get("source_ref")
            owner_source = command.signal
            if cited_source_ref != command.signal.source_ref:
                owner_source = self.store.get_business_task_signal_for_task_source_ref_in_transaction(
                    task_id=task.id, source_ref=cited_source_ref or "", _db=db
                )
                if owner_source is None:
                    raise ValueError("owner evidence must cite a linked source")
            self._require_source_backed_owner(
                signal=owner_source,
                owner_user_id=owner_user_id,
                owner_name=owner_name,
                owner_evidence_json=owner_evidence_json,
            )
            if command.formality.basis is FormalTaskBasis.EXPLICIT_COMMITMENT:
                self._require_owner_authored_commitment(
                    signal=command.signal, owner_user_id=owner_user_id
                )
            self._validate_date_facts(
                command.date_facts,
                signal=command.signal,
                may_commit=command.formality.basis is FormalTaskBasis.EXPLICIT_COMMITMENT,
                owner_user_id=owner_user_id,
            )
            signal_id = self._signal_id_or_create(signal=command.signal, db=db, now=now)
            after = task.model_copy(
                update={
                    "stage": BusinessTaskStage.FORMAL,
                    "formal_basis": command.formality.basis,
                    "commitment_status": resolution.commitment_status,
                    "owner_user_id": owner_user_id,
                    "owner_name": owner_name,
                    "owner_evidence_json": owner_evidence_json,
                    "missing_evidence_json": self._reconcile_missing_owner(
                        missing_evidence_json=task.missing_evidence_json,
                        owner_is_persisted=owner_is_persisted,
                    ),
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
            self._record_date_facts(
                task_id=task.id, signal_id=signal_id, date_facts=command.date_facts, db=db
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

    def apply_acceptance(
        self, command: ApplyAcceptance, *, _db: sqlite3.Connection | None = None
    ) -> TaskMutationResult:
        now = self._now()
        transaction = (
            nullcontext(_db)
            if _db is not None
            else self.store.business_task_transaction()
        )
        with transaction as db:
            replay = self._replay_result(signal=command.signal, db=db)
            if replay is not None:
                return replay
            task = self._require_task(
                self.store.get_business_task_in_transaction(task_id=command.task_id, _db=db),
                command.task_id,
            )
            if task.status is BusinessTaskStatus.MERGED:
                raise ValueError("cannot accept a merged task")
            if (
                task.stage is not BusinessTaskStage.FORMAL
                or task.commitment_status is not CommitmentStatus.ASSIGNED_UNACCEPTED
            ):
                raise ValueError("acceptance requires a formal assigned task")
            if not command.acceptance_is_explicit:
                raise ValueError("acceptance requires explicit owner commitment evidence")
            if AcceptancePolarity(command.acceptance_polarity) is not AcceptancePolarity.ACCEPTED:
                raise ValueError("acceptance evidence polarity must be accepted")
            if (
                not command.acceptance_excerpt.strip()
                or command.acceptance_excerpt not in command.signal.evidence_text
            ):
                raise ValueError("acceptance excerpt must occur in the owner-authored source")
            if (
                not task.owner_user_id.strip()
                or command.signal.author_kind is not BusinessActorKind.HUMAN
                or command.signal.author_user_id != task.owner_user_id
            ):
                raise ValueError("acceptance requires the identified owner actor")
            if command.referenced_signal_id is None or not any(
                item.signal_id == command.referenced_signal_id
                for item in self.store.list_business_task_evidence_in_transaction(
                    task_id=task.id, _db=db
                )
            ):
                raise ValueError("acceptance must be linked to this existing task")
            matching_task_ids = self.store.list_unmerged_formal_business_task_ids_for_signal_in_transaction(
                signal_id=command.referenced_signal_id, _db=db
            )
            if matching_task_ids != (task.id,):
                raise ValueError("acceptance source must uniquely link to one formal task")
            referenced_signal = self.store.get_business_task_signal_in_transaction(
                signal_id=command.referenced_signal_id, _db=db
            )
            source_context = json.loads(command.signal.context_json)
            if (
                referenced_signal is None
                or source_context.get("reply_to_source_ref") != referenced_signal.source_ref
            ):
                raise ValueError("acceptance reply must reference the assigned task source")
            if command.signal.conversation_id != referenced_signal.conversation_id:
                raise ValueError("acceptance reply must share the assigned task conversation")
            source_task_ids = self.store.list_unmerged_formal_business_task_ids_for_source_in_transaction(
                source_type=referenced_signal.source_type,
                source_ref=referenced_signal.source_ref,
                conversation_id=referenced_signal.conversation_id,
                _db=db,
            )
            if source_task_ids != (task.id,):
                raise ValueError("acceptance reply source must uniquely identify one formal task")
            self._validate_date_facts(
                command.date_facts, signal=command.signal,
                may_commit=True, owner_user_id=task.owner_user_id
            )
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
            self._record_date_facts(
                task_id=task.id, signal_id=signal_id, date_facts=command.date_facts, db=db
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

    def update_task(
        self, command: UpdateBusinessTask, *, _db: sqlite3.Connection | None = None
    ) -> TaskMutationResult:
        if command.commitment_status is not None:
            raise ValueError("commitment transitions require dedicated acceptance commands")
        if command.deadline_at is not None:
            raise ValueError("untyped deadline is legacy; use date_facts")
        self._validate_date_facts(command.date_facts, signal=command.signal, may_commit=False)
        now = self._now()
        fields = {
            "title": command.title,
            "description": command.description,
            "commitment_status": command.commitment_status,
            "owner_user_id": command.owner_user_id,
            "owner_name": command.owner_name,
            "owner_evidence_json": command.owner_evidence_json,
            "status": command.status,
            "business_relevance": command.business_relevance,
        }
        transaction = (
            nullcontext(_db)
            if _db is not None
            else self.store.business_task_transaction()
        )
        with transaction as db:
            replay = self._replay_result(signal=command.signal, db=db)
            if replay is not None:
                return replay
            task = self._require_task(
                self.store.get_business_task_in_transaction(task_id=command.task_id, _db=db),
                command.task_id,
            )
            if task.status is BusinessTaskStatus.MERGED:
                raise ValueError("cannot update a merged task")
            changed = {name: value for name, value in fields.items() if value is not None}
            changed = {
                name: value for name, value in changed.items()
                if name not in {"title", "description"}
                or getattr(task, name) != value
            }
            if not changed and not command.date_facts:
                raise ValueError("task update must change at least one field")
            event_type = (
                self._update_event_type(changed)
                if changed else BusinessTaskEventType.DATE_EVIDENCE_RECORDED
            )
            if set(changed) & {"owner_user_id", "owner_name", "owner_evidence_json"}:
                self._require_source_backed_owner(
                    signal=command.signal,
                    owner_user_id=changed.get("owner_user_id", task.owner_user_id),
                    owner_name=changed.get("owner_name", task.owner_name),
                    owner_evidence_json=changed.get(
                        "owner_evidence_json", task.owner_evidence_json
                    ),
                )
            next_owner_id = changed.get("owner_user_id", task.owner_user_id)
            if task.owner_user_id and next_owner_id:
                owner_identity_changed = next_owner_id != task.owner_user_id
            else:
                owner_identity_changed = (
                    next_owner_id != task.owner_user_id
                    or changed.get("owner_name", task.owner_name) != task.owner_name
                )
            acceptance_reset = (
                owner_identity_changed
                and task.commitment_status is CommitmentStatus.ACCEPTED
            )
            signal_id = self._signal_id_or_create(signal=command.signal, db=db, now=now)
            after = task.model_copy(
                update={
                    **changed,
                    **({"commitment_status": CommitmentStatus.ASSIGNED_UNACCEPTED}
                       if acceptance_reset else {}),
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
            self._record_date_facts(
                task_id=task.id, signal_id=signal_id, date_facts=command.date_facts, db=db
            )
            self.store.append_business_task_event(
                task_id=task.id,
                event_type=event_type,
                signal_id=signal_id,
                before_json=self._snapshot(task),
                after_json=self._snapshot(after),
                reason=(
                    f"{command.reason} 改派后，原负责人的接受已作废。"
                    if acceptance_reset else command.reason
                ),
                _db=db,
            )
            return TaskMutationResult(task_id=task.id, signal_id=signal_id, created=False)

    @staticmethod
    def _update_event_type(changed: dict[str, object]) -> BusinessTaskEventType:
        if set(changed) == {"commitment_status"}:
            return BusinessTaskEventType.COMMITMENT_CHANGED
        if set(changed) <= {"owner_user_id", "owner_name", "owner_evidence_json"}:
            return BusinessTaskEventType.OWNER_CHANGED
        if set(changed) == {"status"}:
            return BusinessTaskEventType.STATUS_CHANGED
        if set(changed) == {"business_relevance"}:
            return BusinessTaskEventType.RELEVANCE_CHANGED
        if set(changed) <= {"title", "description"}:
            return BusinessTaskEventType.DETAILS_CHANGED
        if set(changed) & {"title", "description"}:
            return BusinessTaskEventType.FIELDS_CHANGED
        raise ValueError("each task update must describe one state transition")

    def merge_same_deliverable(
        self, command: MergeBusinessTasks, *, _db: sqlite3.Connection | None = None
    ) -> TaskMutationResult:
        if command.source_task_id == command.target_task_id:
            raise ValueError("a task cannot merge into itself")
        if (
            command.identity_evidence is None
            or resolve_identity(command.identity_evidence) != "merge"
        ):
            raise ValueError("identity evidence does not authorize a merge")
        now = self._now()
        transaction = (
            nullcontext(_db)
            if _db is not None
            else self.store.business_task_transaction()
        )
        with transaction as db:
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
            self.store.copy_business_task_date_evidence_in_transaction(
                source_task_id=source.id, target_task_id=target.id, _db=db
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
