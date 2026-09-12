"""Narrow durable continuation gate for audited Email unsubscribe tasks."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from typing import Protocol

from app.agent_context import AgentTaskContext, PriorReceipt
from app.agent_contracts import AuditAgentResult, AuditOutcome
from app.email_store import EmailPersistenceCorruption, EmailStore
from app.email_task_adapter import (
    email_unsubscribe_continuation_receipt,
    validate_unsubscribe_entry_operation_semantics,
    validated_email_unsubscribe_continuation,
)
from app.email_unsubscribe import (
    MAX_EMAIL_UNSUBSCRIBE_CONTINUATION_OPERATIONS,
    EmailUnsubscribeEffect,
    UnsubscribeOperation,
)
from app.store import AgentRole, AgentRun, ReplyTask
from app.task_lifecycle import validate_audited_email_task


class DomainContinuationDriver(Protocol):
    def load_snapshot(self, task: ReplyTask) -> object: ...

    def continuation_state(
        self,
        task: ReplyTask,
        *,
        audit_run: AgentRun,
        audit_result: AuditAgentResult,
        snapshot: object | None = None,
    ) -> DomainContinuationDecision: ...

    def consumption_state(
        self,
        task: ReplyTask,
        *,
        parent_audit_run: AgentRun,
        parent_audit_result: AuditAgentResult,
        child_audit_run: AgentRun,
        child_audit_result: AuditAgentResult | None,
        snapshot: object | None = None,
    ) -> DomainContinuationConsumptionDecision: ...


class DomainContinuationState(StrEnum):
    TERMINAL = "terminal"
    CONTINUE = "continue"
    LIMIT_REACHED = "limit_reached"
    INVALID = "invalid"
    UNAVAILABLE = "unavailable"


class DomainContinuationConsumptionState(StrEnum):
    CONSUMED = "consumed"
    NOT_CONSUMED = "not_consumed"
    INVALID = "invalid"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class DomainContinuationDecision:
    state: DomainContinuationState
    required_receipt_id: str = ""
    required_receipt_binding: str = ""


@dataclass(frozen=True)
class DomainContinuationConsumptionDecision:
    state: DomainContinuationConsumptionState


class _SnapshotState(StrEnum):
    VALID = "valid"
    TERMINAL = "terminal"
    INVALID = "invalid"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class _EmailUnsubscribeSnapshot:
    state: _SnapshotState
    payload: dict[str, object] | None = None
    claim: dict[str, object] | None = None
    continuation: dict[str, object] | None = None
    effects_by_digest: dict[str, dict[str, object]] | None = None
    effects_by_audit_id: dict[int, dict[str, object]] | None = None


class EmailUnsubscribeContinuationDriver:
    """Continue only the exact Audit run backed by current durable state."""

    def __init__(self, email_store: EmailStore) -> None:
        self.email_store = email_store

    def audit_run_has_execution_evidence(
        self, task: ReplyTask, *, audit_run_id: int
    ) -> bool:
        """Return whether the unsubscribe tool ran and left a receipt.

        The receipt is the evidence. Only the service writes one, and it
        writes one only after the browser reached a terminal page, so an
        `executed` result with no receipt is a claim the durable record does
        not support. Tasks outside the unsubscribe lifecycle have no such
        evidence contract.

        The audited lifecycle additionally required the receipt's claim or
        effect to carry this Audit run's id, which made a receipt written by
        an earlier turn look like no evidence at all. One unsubscribe needs
        one receipt, not one per turn.
        """
        del audit_run_id
        if task.channel != "email" or _validated_unsubscribe_task_payload(task) is None:
            return True
        try:
            snapshot = self.email_store.get_email_unsubscribe_state_snapshot(
                task.trigger_message_id
            )
        except Exception:
            return False
        return isinstance(snapshot, dict) and isinstance(
            snapshot.get("receipt"), dict
        )

    def load_snapshot(self, task: ReplyTask) -> _EmailUnsubscribeSnapshot:
        """Load and validate one immutable read view for one derivation pass."""

        if task.channel != "email":
            return _EmailUnsubscribeSnapshot(_SnapshotState.TERMINAL)
        payload = _validated_unsubscribe_task_payload(task)
        if payload is None:
            try:
                raw = json.loads(task.trigger_message_json)
            except (json.JSONDecodeError, TypeError):
                return _EmailUnsubscribeSnapshot(_SnapshotState.INVALID)
            if isinstance(raw, dict) and (
                raw.get("action_type") != "unsubscribe"
                or raw.get("lifecycle_version")
                != "email_unsubscribe_audited_v2"
            ):
                return _EmailUnsubscribeSnapshot(_SnapshotState.TERMINAL)
            return _EmailUnsubscribeSnapshot(_SnapshotState.INVALID)
        try:
            raw_snapshot = self.email_store.get_email_unsubscribe_state_snapshot(
                task.trigger_message_id
            )
        except Exception as exc:
            return _EmailUnsubscribeSnapshot(_snapshot_failure_state(exc))
        if isinstance(raw_snapshot, dict) and isinstance(
            raw_snapshot.get("receipt"), dict
        ):
            # One unsubscribe, one receipt, and the action is over. Nothing
            # below this line can change that, and the lineage it validates
            # belongs to a lifecycle that no longer runs.
            return _EmailUnsubscribeSnapshot(_SnapshotState.TERMINAL)
        try:
            return _validated_snapshot(payload, raw_snapshot)
        except (EmailPersistenceCorruption, KeyError, TypeError, ValueError):
            return _EmailUnsubscribeSnapshot(_SnapshotState.INVALID)
        except Exception:
            # Programming defects and unknown exception types must never be
            # presented as transient persistence availability failures.
            return _EmailUnsubscribeSnapshot(_SnapshotState.INVALID)

    def continuation_state(
        self,
        task: ReplyTask,
        *,
        audit_run: AgentRun,
        audit_result: AuditAgentResult,
        snapshot: object | None = None,
    ) -> DomainContinuationDecision:
        current = snapshot if snapshot is not None else self.load_snapshot(task)
        if not isinstance(current, _EmailUnsubscribeSnapshot):
            return DomainContinuationDecision(DomainContinuationState.INVALID)
        if current.state is _SnapshotState.TERMINAL:
            return DomainContinuationDecision(DomainContinuationState.TERMINAL)
        if current.state is _SnapshotState.UNAVAILABLE:
            return DomainContinuationDecision(DomainContinuationState.UNAVAILABLE)
        if current.state is not _SnapshotState.VALID:
            return DomainContinuationDecision(DomainContinuationState.INVALID)
        if (
            audit_result.outcome is not AuditOutcome.EXECUTED
            or audit_run.role is not AgentRole.AUDIT
            or audit_run.status != "completed"
            or audit_run.reply_task_id != task.id
            or audit_run.execution_generation != task.execution_generation
            or audit_run.parent_agent_run_id is None
            or audit_result.proposal_revision != audit_run.proposal_revision
            or audit_result.external_result is None
            or audit_result.external_result.operation_id != audit_run.operation_id
        ):
            return DomainContinuationDecision(DomainContinuationState.INVALID)
        assert current.payload is not None and current.claim is not None
        payload = current.payload
        claim = current.claim
        continuation = current.continuation
        effects = current.effects_by_digest or {}
        if claim.get("status") == "done":
            if continuation is not None:
                return DomainContinuationDecision(DomainContinuationState.INVALID)
            effect = effects.get(str(claim.get("effect_digest") or ""))
            if not _valid_terminal_effect(
                payload,
                claim,
                effect,
                audit_run=audit_run,
            ):
                return DomainContinuationDecision(DomainContinuationState.INVALID)
            return DomainContinuationDecision(DomainContinuationState.TERMINAL)
        if (
            not isinstance(claim, dict)
            or not isinstance(continuation, dict)
            or claim.get("status") != "awaiting_audit"
            or claim.get("audit_agent_run_id") != audit_run.id
        ):
            return DomainContinuationDecision(DomainContinuationState.INVALID)
        try:
            typed = validated_email_unsubscribe_continuation(
                payload,
                claim,
                continuation,
            )
        except Exception:
            return DomainContinuationDecision(DomainContinuationState.INVALID)
        if (
            len(typed.executed_operations)
            >= MAX_EMAIL_UNSUBSCRIBE_CONTINUATION_OPERATIONS
        ):
            return DomainContinuationDecision(
                DomainContinuationState.LIMIT_REACHED
            )
        receipt = email_unsubscribe_continuation_receipt(typed)
        return DomainContinuationDecision(
            DomainContinuationState.CONTINUE,
            required_receipt_id=receipt.receipt_id,
            required_receipt_binding=domain_continuation_receipt_binding(receipt),
        )

    def consumption_state(
        self,
        task: ReplyTask,
        *,
        parent_audit_run: AgentRun,
        parent_audit_result: AuditAgentResult,
        child_audit_run: AgentRun,
        child_audit_result: AuditAgentResult | None,
        snapshot: object | None = None,
    ) -> DomainContinuationConsumptionDecision:
        """Prove that a completed child Audit durably consumed the parent fence."""

        if (
            task.channel != "email"
            or parent_audit_result.outcome is not AuditOutcome.EXECUTED
            or parent_audit_run.role is not AgentRole.AUDIT
            or parent_audit_run.status != "completed"
            or parent_audit_run.reply_task_id != task.id
            or parent_audit_run.execution_generation != task.execution_generation
            or parent_audit_run.parent_agent_run_id is None
            or parent_audit_result.proposal_revision
            != parent_audit_run.proposal_revision
            or parent_audit_result.external_result is None
            or parent_audit_result.external_result.operation_id
            != parent_audit_run.operation_id
            or child_audit_run.status != "completed"
            or child_audit_result is None
            or child_audit_result.outcome is not AuditOutcome.EXECUTED
            or child_audit_run.role is not AgentRole.AUDIT
            or child_audit_run.reply_task_id != task.id
            or child_audit_run.execution_generation != task.execution_generation
            or child_audit_run.proposal_revision
            != parent_audit_run.proposal_revision + 1
            or child_audit_result.proposal_revision
            != child_audit_run.proposal_revision
            or child_audit_result.external_result is None
            or child_audit_result.external_result.operation_id
            != child_audit_run.operation_id
        ):
            return DomainContinuationConsumptionDecision(
                DomainContinuationConsumptionState.NOT_CONSUMED
            )
        current = snapshot if snapshot is not None else self.load_snapshot(task)
        if not isinstance(current, _EmailUnsubscribeSnapshot):
            return DomainContinuationConsumptionDecision(
                DomainContinuationConsumptionState.INVALID
            )
        if current.state is _SnapshotState.UNAVAILABLE:
            return DomainContinuationConsumptionDecision(
                DomainContinuationConsumptionState.UNAVAILABLE
            )
        if current.state is not _SnapshotState.VALID:
            return DomainContinuationConsumptionDecision(
                DomainContinuationConsumptionState.INVALID
            )
        effects_by_audit_id = current.effects_by_audit_id or {}
        effects_by_digest = current.effects_by_digest or {}
        child_effect = effects_by_audit_id.get(child_audit_run.id)
        if child_effect is None:
            return DomainContinuationConsumptionDecision(
                DomainContinuationConsumptionState.NOT_CONSUMED
            )
        parent_digest = str(child_effect.get("previous_effect_digest") or "")
        parent_effect = effects_by_digest.get(parent_digest)
        state = (
            DomainContinuationConsumptionState.CONSUMED
            if parent_effect is not None
            and parent_effect.get("audit_agent_run_id") == parent_audit_run.id
            else DomainContinuationConsumptionState.NOT_CONSUMED
        )
        return DomainContinuationConsumptionDecision(state)


def _snapshot_failure_state(exc: Exception) -> _SnapshotState:
    if isinstance(exc, EmailPersistenceCorruption):
        return _SnapshotState.INVALID
    if not isinstance(exc, sqlite3.OperationalError):
        return _SnapshotState.INVALID
    code = getattr(exc, "sqlite_errorcode", None)
    if isinstance(code, int) and code & 0xFF in {
        sqlite3.SQLITE_BUSY,
        sqlite3.SQLITE_LOCKED,
        sqlite3.SQLITE_IOERR,
    }:
        return _SnapshotState.UNAVAILABLE
    message = str(exc).lower()
    if any(
        marker in message
        for marker in (
            "database is locked",
            "database is busy",
            "disk i/o error",
            "unable to open database file",
        )
    ):
        return _SnapshotState.UNAVAILABLE
    return _SnapshotState.INVALID


def _validated_snapshot(
    payload: dict[str, object],
    raw_snapshot: object,
) -> _EmailUnsubscribeSnapshot:
    if not isinstance(raw_snapshot, dict):
        raise EmailPersistenceCorruption("unsubscribe snapshot has the wrong type")
    claim = raw_snapshot.get("claim")
    continuation = raw_snapshot.get("continuation")
    effects_value = raw_snapshot.get("effects")
    if not _valid_lineage_claim(payload, claim) or not isinstance(
        effects_value, (list, tuple)
    ):
        raise EmailPersistenceCorruption("unsubscribe snapshot claim is invalid")
    assert isinstance(claim, dict)
    if (
        not effects_value
        or len(effects_value) > MAX_EMAIL_UNSUBSCRIBE_CONTINUATION_OPERATIONS
    ):
        raise EmailPersistenceCorruption(
            "unsubscribe snapshot exceeds durable continuation operation limit"
        )
    effects_by_digest: dict[str, dict[str, object]] = {}
    effects_by_audit_id: dict[int, dict[str, object]] = {}
    for effect_value in effects_value:
        if not isinstance(effect_value, dict):
            raise EmailPersistenceCorruption("unsubscribe effect has the wrong type")
        effect_digest = effect_value.get("effect_digest")
        if not isinstance(effect_digest, str) or effect_digest in effects_by_digest:
            raise EmailPersistenceCorruption("unsubscribe effect digest is duplicated")
        typed = _validated_lineage_effect(
            payload,
            claim,
            effect_value,
            expected_digest=effect_digest,
        )
        if typed is None:
            raise EmailPersistenceCorruption("unsubscribe effect identity is invalid")
        audit_run_id = effect_value.get("audit_agent_run_id")
        assert isinstance(audit_run_id, int) and not isinstance(audit_run_id, bool)
        if audit_run_id in effects_by_audit_id:
            raise EmailPersistenceCorruption("unsubscribe Audit binding is duplicated")
        effects_by_digest[effect_digest] = dict(effect_value)
        effects_by_audit_id[audit_run_id] = dict(effect_value)

    head_digest = claim.get("effect_digest")
    head = effects_by_digest.get(str(head_digest or ""))
    if (
        head is None
        or head.get("operations") != claim.get("operations")
        or head.get("audit_agent_run_id") != claim.get("audit_agent_run_id")
    ):
        raise EmailPersistenceCorruption("unsubscribe claim head is invalid")

    seen: set[str] = set()
    current = head
    while True:
        current_digest = str(current["effect_digest"])
        if current_digest in seen:
            raise EmailPersistenceCorruption("unsubscribe effect lineage cycles")
        seen.add(current_digest)
        previous_digest = str(current.get("previous_effect_digest") or "")
        if not previous_digest:
            break
        previous = effects_by_digest.get(previous_digest)
        if previous is None:
            raise EmailPersistenceCorruption("unsubscribe effect lineage is incomplete")
        current_operations = current.get("operations")
        previous_operations = previous.get("operations")
        if (
            not isinstance(current_operations, list)
            or not isinstance(previous_operations, list)
            or len(current_operations) != len(previous_operations) + 1
            or current_operations[:-1] != previous_operations
        ):
            raise EmailPersistenceCorruption(
                "unsubscribe effect lineage is not append-only"
            )
        current = previous
    root_operations = current.get("operations")
    if (
        current.get("previous_effect_digest") != ""
        or not isinstance(root_operations, list)
        or len(root_operations) != 1
    ):
        raise EmailPersistenceCorruption("unsubscribe root effect is invalid")
    try:
        root_operation = UnsubscribeOperation.from_mapping(root_operations[0])
    except (TypeError, ValueError) as exc:
        raise EmailPersistenceCorruption(
            "unsubscribe root operation is invalid"
        ) from exc
    if root_operation.kind.value not in {"open_entry", "post_one_click"}:
        raise EmailPersistenceCorruption("unsubscribe root operation is invalid")
    try:
        validate_unsubscribe_entry_operation_semantics(
            payload,
            entry_reference=str(claim["entry_reference"]),
            operation=root_operation,
        )
    except (TypeError, ValueError) as exc:
        raise EmailPersistenceCorruption(
            "unsubscribe root operation is invalid"
        ) from exc
    if len(seen) != len(effects_by_digest):
        raise EmailPersistenceCorruption("unsubscribe effect lineage is disconnected")
    if len(seen) != len(claim["operations"]):
        raise EmailPersistenceCorruption(
            "unsubscribe effect lineage length does not match operation prefix"
        )
    if len(claim["operations"]) > MAX_EMAIL_UNSUBSCRIBE_CONTINUATION_OPERATIONS:
        raise EmailPersistenceCorruption(
            "unsubscribe claim exceeds durable continuation operation limit"
        )
    if claim.get("status") == "awaiting_audit":
        if (
            not isinstance(continuation, dict)
            or continuation.get("effect_digest") != head_digest
        ):
            raise EmailPersistenceCorruption(
                "unsubscribe continuation does not match current head"
            )
    elif continuation is not None:
        raise EmailPersistenceCorruption(
            "terminal unsubscribe claim has an unexpected continuation"
        )
    return _EmailUnsubscribeSnapshot(
        _SnapshotState.VALID,
        payload=dict(payload),
        claim=dict(claim),
        continuation=(dict(continuation) if isinstance(continuation, dict) else None),
        effects_by_digest=effects_by_digest,
        effects_by_audit_id=effects_by_audit_id,
    )


def _validated_unsubscribe_task_payload(
    task: ReplyTask,
) -> dict[str, object] | None:
    try:
        payload = json.loads(task.trigger_message_json)
    except (json.JSONDecodeError, TypeError):
        return None
    if (
        not isinstance(payload, dict)
        or payload.get("action_type") != "unsubscribe"
        or payload.get("lifecycle_version") != "email_unsubscribe_audited_v2"
    ):
        return None
    try:
        context = AgentTaskContext(
            task_id=task.id,
            channel=task.channel,
            conversation_id=task.conversation_id,
            conversation_title=task.conversation_title,
            single_chat=task.single_chat,
            trigger_message_id=task.trigger_message_id,
            trigger_sender=task.trigger_sender,
            trigger_text=task.trigger_text,
            trigger_create_time=task.trigger_create_time,
            messages=(),
            materials=(),
            prior_receipts=(),
            trigger_raw_payload=payload,
        )
        if not validate_audited_email_task(task, context):
            return None
    except (TypeError, ValueError):
        return None
    return payload


def _valid_lineage_claim(
    payload: dict[str, object],
    claim: object,
) -> bool:
    identity_fields = (
        "action_identity",
        "action_plan_id",
        "action_plan_version",
        "classification_id",
        "account_id",
        "stable_message_identity",
        "thread_identity",
    )
    if (
        not isinstance(claim, dict)
        or claim.get("status") not in {"awaiting_audit", "done"}
        or claim.get("status") == "done"
        and claim.get("phase") != "terminal"
        or any(claim.get(name) != payload.get(name) for name in identity_fields)
        or not isinstance(claim.get("audit_agent_run_id"), int)
        or isinstance(claim.get("audit_agent_run_id"), bool)
        or int(claim["audit_agent_run_id"]) <= 0
        or not isinstance(claim.get("effect_digest"), str)
        or not isinstance(claim.get("operations"), list)
        or not claim["operations"]
    ):
        return False
    entries = payload.get("unsubscribe_entries")
    entry_reference = claim.get("entry_reference")
    return (
        isinstance(entries, list)
        and isinstance(entry_reference, str)
        and any(
            isinstance(item, dict) and item.get("reference") == entry_reference
            for item in entries
        )
    )


def _validated_lineage_effect(
    payload: dict[str, object],
    claim: dict[str, object],
    effect: object,
    *,
    expected_digest: str,
) -> EmailUnsubscribeEffect | None:
    if (
        not isinstance(effect, dict)
        or effect.get("action_identity") != payload.get("action_identity")
        or effect.get("effect_digest") != expected_digest
        or not isinstance(effect.get("operations"), list)
        or not effect["operations"]
        or not isinstance(effect.get("audit_agent_run_id"), int)
        or isinstance(effect.get("audit_agent_run_id"), bool)
        or int(effect["audit_agent_run_id"]) <= 0
    ):
        return None
    operations_value = effect["operations"]
    try:
        operations = tuple(
            UnsubscribeOperation.from_mapping(item)
            for item in operations_value
            if isinstance(item, dict)
        )
        if len(operations) != len(operations_value):
            return None
        typed = EmailUnsubscribeEffect(
            action_identity=str(payload["action_identity"]),
            action_plan_id=str(payload["action_plan_id"]),
            action_plan_version=int(payload["action_plan_version"]),
            classification_id=int(payload["classification_id"]),
            account_id=str(payload["account_id"]),
            stable_message_identity=str(payload["stable_message_identity"]),
            thread_identity=str(payload["thread_identity"]),
            entry_reference=str(claim["entry_reference"]),
            operations=operations,
            previous_effect_digest=str(effect.get("previous_effect_digest") or ""),
        )
    except (KeyError, TypeError, ValueError):
        return None
    return typed if typed.effect_digest == expected_digest else None


def _valid_terminal_effect(
    payload: dict[str, object],
    claim: dict[str, object],
    effect: object,
    *,
    audit_run: AgentRun,
) -> bool:
    """Validate terminal state from immutable identity and its durable effect."""

    identity_fields = (
        "action_identity",
        "action_plan_id",
        "action_plan_version",
        "classification_id",
        "account_id",
        "stable_message_identity",
        "thread_identity",
    )
    if (
        not isinstance(effect, dict)
        or claim.get("status") != "done"
        or claim.get("phase") != "terminal"
        or claim.get("audit_agent_run_id") != audit_run.id
        or effect.get("audit_agent_run_id") != audit_run.id
        or any(claim.get(name) != payload.get(name) for name in identity_fields)
        or effect.get("action_identity") != payload.get("action_identity")
        or effect.get("effect_digest") != claim.get("effect_digest")
        or effect.get("operations") != claim.get("operations")
    ):
        return False
    entries = payload.get("unsubscribe_entries")
    entry_reference = claim.get("entry_reference")
    operations_value = claim.get("operations")
    if (
        not isinstance(entries, list)
        or not isinstance(entry_reference, str)
        or not any(
            isinstance(item, dict) and item.get("reference") == entry_reference
            for item in entries
        )
        or not isinstance(operations_value, list)
        or not operations_value
    ):
        return False
    try:
        operations = tuple(
            UnsubscribeOperation.from_mapping(item)
            for item in operations_value
            if isinstance(item, dict)
        )
        if len(operations) != len(operations_value):
            return False
        typed_effect = EmailUnsubscribeEffect(
            action_identity=str(payload["action_identity"]),
            action_plan_id=str(payload["action_plan_id"]),
            action_plan_version=int(payload["action_plan_version"]),
            classification_id=int(payload["classification_id"]),
            account_id=str(payload["account_id"]),
            stable_message_identity=str(payload["stable_message_identity"]),
            thread_identity=str(payload["thread_identity"]),
            entry_reference=entry_reference,
            operations=operations,
            previous_effect_digest=str(effect.get("previous_effect_digest") or ""),
        )
    except (KeyError, TypeError, ValueError):
        return False
    return typed_effect.effect_digest == claim.get("effect_digest")


def domain_continuation_receipt_binding(receipt: PriorReceipt) -> str:
    """Bind every projected receipt field without exposing its content."""

    if not isinstance(receipt, PriorReceipt):
        raise TypeError("domain continuation receipt has the wrong type")
    canonical = json.dumps(
        {
            "completed": receipt.completed,
            "operation": receipt.operation,
            "receipt_id": receipt.receipt_id,
            "summary": receipt.summary,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "domain-continuation-receipt:" + sha256(
        canonical.encode("utf-8")
    ).hexdigest()
