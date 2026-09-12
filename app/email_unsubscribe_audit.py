"""Execute one accepted Email unsubscribe operation from a bound Audit run."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from enum import Enum
import hashlib
import hmac
import inspect
import json
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from app.agent_contracts import ConsumerAgentResult, ConsumerOutcome, ProposedAction
from app.agent_result import AgentError
from app.email_classifier_contracts import (
    EmailAction,
    EmailActionPlan,
    EmailProviderLocator,
)
from app.email_store import EmailStore
from app.email_task_adapter import accepted_email_unsubscribe_effect
from app.email_unsubscribe import (
    EmailUnsubscribeContinuation,
    EmailUnsubscribeEffect,
    UnsubscribeAuthenticationEvidence,
    UnsubscribeContinuationResult,
    UnsubscribeDiscoveredControl,
    UnsubscribeEntry,
    UnsubscribeExecutionResult,
    UnsubscribeOperation,
    UnsubscribeOutcome,
    browser_unsubscribe_entries,
)
from app.store import AgentRole, AgentRun, AutoReplyStore, ReplyTask


AUDITED_LIFECYCLE_VERSION = "email_unsubscribe_audited_v2"
PAYLOAD_SCHEMA = "email_agent_action.v1"
AUDITED_UNSUBSCRIBE_TOOL = "execute_audited_email_unsubscribe"
UNSUBSCRIBE_TOOL = "unsubscribe_email"
# Transcripts written before the one-call tool still name the audited
# one, and those runs are read back by the same readers.
_UNSUBSCRIBE_TOOLS = frozenset({UNSUBSCRIBE_TOOL, AUDITED_UNSUBSCRIBE_TOOL})
AUDIT_BINDING_REJECTED_CODE = "unsubscribe_audit_run_invalid"
# A binding rejection is worth a fresh Audit turn, not an unbounded supply of
# them: the orchestrator turns any retryable Audit failure into another turn,
# so a call that can never bind would otherwise consume every turn the process
# allows. Two re-binding turns cover the ordering and visibility conditions a
# newer turn satisfies by construction.
MAX_AUDIT_BINDING_REJECTIONS = 2
_REJECTION_DETAIL_LIMIT = 200


class AuditedUnsubscribeTerminalState(str, Enum):
    HANDOFF = "handoff"
    NO_ACTION = "no_action"


# `disposition_for_unsubscribe_outcome` files every terminal skip the same way
# - "skipped", not retryable - because what the browser may do next is the same
# for all of them: nothing. This map keeps every terminal skip as a
# service-owned no-action result. A login, CAPTCHA or payment wall is a
# boundary the service must never cross, but it is not a request for Derek to
# choose an action: the Skill says to stop without further controls. A missing
# entry or an already unsubscribed address likewise leaves nothing for anyone
# to do. Escalation is reserved for a genuine high-risk/low-confidence or
# low-coverage decision, not a bounded browser outcome that is already fully
# evidenced.
_TERMINAL_SKIP_STATES: Mapping[UnsubscribeOutcome, AuditedUnsubscribeTerminalState] = {
    UnsubscribeOutcome.SKIPPED_LOGIN_REQUIRED: AuditedUnsubscribeTerminalState.NO_ACTION,
    UnsubscribeOutcome.SKIPPED_CAPTCHA: AuditedUnsubscribeTerminalState.NO_ACTION,
    UnsubscribeOutcome.SKIPPED_PAYMENT: AuditedUnsubscribeTerminalState.NO_ACTION,
    UnsubscribeOutcome.SKIPPED_NO_RELIABLE_ENTRY: (
        AuditedUnsubscribeTerminalState.NO_ACTION
    ),
    UnsubscribeOutcome.ALREADY_UNSUBSCRIBED: AuditedUnsubscribeTerminalState.NO_ACTION,
}


def audited_unsubscribe_skip_receipt(
    run: object,
) -> tuple[UnsubscribeOutcome, AuditedUnsubscribeTerminalState] | None:
    """Read the terminal state the audited tool receipt already carries.

    The service writes the receipt before the Audit model reports anything, so
    a terminal skip is projected from the persisted outcome instead of from the
    error code the model invented for an operation it may not complete. The
    newest succeeded call of the turn decides.

    A rejected later call on the same action is stepped over, because a
    terminal receipt cannot be undone and a repeat call on a terminal effect is
    exactly what that rejection is. A rejected later call on a different action
    is not: a receipt settles only its own action, while the caller replaces
    the whole task's state with it, so stepping over that rejection would erase
    the other action's failure. Only a call that names the action it targeted
    can be told apart this way; a transcript that recorded no arguments keeps
    the older, step-over reading.
    """

    rejected_identities: set[str] = set()
    for event in reversed(list(getattr(run, "tool_events", None) or ())):
        if not isinstance(event, Mapping):
            continue
        item = event.get("item")
        if (
            not isinstance(item, Mapping)
            or item.get("tool") not in _UNSUBSCRIBE_TOOLS
        ):
            continue
        structured = _tool_structured_content(item.get("result"))
        if structured is None:
            continue
        identity = _tool_action_identity(item.get("arguments"))
        if structured.get("status") != "done":
            if identity:
                rejected_identities.add(identity)
            continue
        try:
            outcome = UnsubscribeOutcome(structured.get("outcome"))
        except ValueError:
            return None
        state = _TERMINAL_SKIP_STATES.get(outcome)
        if state is None:
            return None
        if rejected_identities - {identity}:
            return None
        return (outcome, state)
    return None


def audited_unsubscribe_route_refusal(run: object) -> str:
    """Return why the route refused to place the call, or "" if it did place it.

    A runtime can decline to issue an MCP call at all: the provider's own
    safety review reads the planned call and rejects it, so the turn records
    the invocation with an error and no result. The service's audited tool
    never runs, nothing is attempted against the mail provider, and no receipt
    exists.

    The Audit model then has to report something, and what it reports is an
    error code of its own invention with `retryable` false, which reads exactly
    like a business decision to refuse the unsubscribe. It is not one. The
    same call on the same task succeeds on a route whose provider does place
    it, so this is a property of the route, not of the task, and closing the
    task on it throws away work no one declined to do.

    A later successful call on the same turn wins: the refusal was not final.
    """
    for event in reversed(list(getattr(run, "tool_events", None) or ())):
        if not isinstance(event, Mapping):
            continue
        item = event.get("item")
        if (
            not isinstance(item, Mapping)
            or item.get("tool") not in _UNSUBSCRIBE_TOOLS
        ):
            continue
        if _tool_structured_content(item.get("result")) is not None:
            return ""
        error = item.get("error")
        if isinstance(error, Mapping):
            message = error.get("message")
            if isinstance(message, str) and message.strip():
                return message.strip()
        if isinstance(error, str) and error.strip():
            return error.strip()
    return ""


def _tool_action_identity(arguments: object) -> str:
    """Name the action one recorded call targeted, or "" when unreadable."""

    if not isinstance(arguments, Mapping):
        return ""
    accepted_action = arguments.get("accepted_action")
    if not isinstance(accepted_action, Mapping):
        return ""
    identity = accepted_action.get("action_identity")
    return identity if isinstance(identity, str) else ""


def _tool_structured_content(result: object) -> Mapping[str, object] | None:
    """Read one MCP result's structured content in either recorded spelling.

    Runtimes record the same tool result under both spellings, so the readers
    that already project receipts out of a transcript accept both.
    """

    if not isinstance(result, Mapping):
        return None
    for key in ("structured_content", "structuredContent"):
        structured = result.get(key)
        if isinstance(structured, Mapping):
            return structured
    return None


class EmailUnsubscribeAuditOperationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    status: str
    outcome: str = ""
    receipt_id: str = ""
    evidence: str = ""
    result_text: str = ""
    observation_digest: str = ""
    started_at: str = ""
    completed_at: str = ""
    summary: str = Field(min_length=1)
    error: AgentError = AgentError()
    final_step: dict[str, object] | None = None
    continuation: dict[str, object] | None = None


class EmailUnsubscribeAuditOperation:
    """Validate Audit lineage and execute at most one accepted browser effect."""

    def __init__(
        self,
        *,
        task_store: AutoReplyStore,
        email_store: EmailStore,
        resolve_entries: Callable[
            [EmailProviderLocator, str], Sequence[UnsubscribeEntry]
        ],
        execute_effect: Callable[..., object],
        owner_id: str = "email-unsubscribe-audit",
    ) -> None:
        if task_store.path.resolve() != email_store.path.resolve():
            raise ValueError(
                "email task and classification stores must share one database"
            )
        if not owner_id.strip():
            raise ValueError("owner_id must be non-empty")
        self.task_store = task_store
        self.email_store = email_store
        self.resolve_entries = resolve_entries
        self.execute_effect = execute_effect
        self.owner_id = owner_id

    def execute(
        self,
        task_id: int,
        execution_generation: str,
        *,
        audit_agent_run_id: int,
        accepted_action: Mapping[str, object],
    ) -> dict[str, object]:
        task = (
            self.task_store.get_reply_task(task_id)
            if isinstance(task_id, int) and not isinstance(task_id, bool)
            else None
        )
        audit_run = (
            self.task_store.get_agent_run(audit_agent_run_id)
            if isinstance(audit_agent_run_id, int)
            and not isinstance(audit_agent_run_id, bool)
            else None
        )
        binding_failure = self._audit_binding_failure(
            task,
            audit_run,
            execution_generation,
        )
        if binding_failure:
            # A binding rejection used to end the task: one opaque code covered
            # every condition and was never retried. Name the condition and let
            # a bounded number of fresh Audit turns re-bind - the effect itself
            # stays fenced by the unsubscribe write claim and by the
            # one-run-per-turn key.
            return self._failed(
                AUDIT_BINDING_REJECTED_CODE,
                retryable=self._audit_binding_retry_available(task, audit_run),
                detail=binding_failure,
            )
        assert task is not None and audit_run is not None
        try:
            parent = self.task_store.get_agent_run(audit_run.parent_agent_run_id)
            assert parent is not None
            action = _bound_parent_consumer_action(parent, accepted_action)
            payload = _task_payload(task)
            identity = _validate_task_identity(task, payload)
            classification = self.email_store.get_classification(
                identity["classification_id"]
            )
            if classification is None:
                return self._failed("unsubscribe_classification_missing")
            plan = _current_action_plan(classification)
            _validate_current_plan(identity, classification, plan)
            terminal_effect = _terminal_expected_effect(task, action)
            terminal_operations = terminal_effect["operations"]
            if not isinstance(terminal_operations, list):
                raise ValueError("accepted unsubscribe proposal revision is invalid")
            terminal_snapshot = (
                self.email_store.get_email_unsubscribe_terminal_snapshot(
                    task.trigger_message_id,
                    task_id=task.id,
                    task_execution_generation=task.execution_generation,
                    expected_effect=terminal_effect,
                )
            )
            if terminal_snapshot is not None:
                return _terminal_snapshot_result(terminal_snapshot)
            locator = _locator_from_classification(classification, identity)
            continuation_value = self.email_store.get_email_unsubscribe_continuation(
                task.trigger_message_id
            )
            continuation = (
                None
                if continuation_value is None
                else _continuation_from_store(
                    continuation_value,
                    identity,
                    self.email_store.get_email_unsubscribe_claim(
                        task.trigger_message_id
                    ),
                )
            )
            effect = accepted_email_unsubscribe_effect(
                task,
                action,
                continuation=continuation,
            )
            entries = browser_unsubscribe_entries(
                tuple(
                    _resolve_entries_with_authentication(
                        self.resolve_entries,
                        locator,
                        effect.entry_reference,
                        _authentication_from_payload(payload),
                    )
                )
            )
            if not entries:
                return self._failed("unsubscribe_entry_changed")
            entry = next(
                (
                    candidate
                    for candidate in entries
                    if candidate.reference == effect.entry_reference
                ),
                None,
            )
            if entry is None:
                return self._failed("unsubscribe_entry_changed")
            return self._execute_bound_effect(
                task=task,
                audit_run=audit_run,
                effect=effect,
                entries=entries,
            )
        except Exception as exc:  # noqa: BLE001 - public tool fails closed
            return self._failed(
                f"unsubscribe_operation_rejected:{type(exc).__name__}",
                detail=_rejection_detail(exc),
            )

    def _audit_binding_retry_available(
        self,
        task: ReplyTask | None,
        audit_run: AgentRun | None,
    ) -> bool:
        """Say whether this generation may spend another re-binding turn.

        The budget is counted from the rejections the generation already
        recorded, so it survives the process boundary the tool runs behind. A
        call that resolves neither the task nor the Audit run cannot be
        attributed to a generation at all and gets no retry.
        """

        if task is not None:
            reply_task_id, generation = task.id, task.execution_generation
        elif audit_run is not None:
            reply_task_id = audit_run.reply_task_id
            generation = audit_run.execution_generation
        else:
            return False
        rejections = 0
        for run in self.task_store.list_agent_runs_for_task_generation(
            reply_task_id,
            generation,
        ):
            if run.role is not AgentRole.AUDIT or run.status != "failed":
                continue
            try:
                recorded = json.loads(run.structured_error_json or "{}")
            except ValueError:
                continue
            if (
                isinstance(recorded, Mapping)
                and recorded.get("code") == AUDIT_BINDING_REJECTED_CODE
            ):
                rejections += 1
        return rejections < MAX_AUDIT_BINDING_REJECTIONS

    def _audit_binding_failure(
        self,
        task: ReplyTask | None,
        audit_run: AgentRun | None,
        execution_generation: object,
    ) -> str:
        """Name the first unmet binding condition, or "" when the call is bound.

        Every condition keeps a fixed internal name. The name is the only thing
        the caller reports, so a live rejection is attributable without reading
        any task, run or page content back out of the database.
        """

        if task is None:
            return "task_missing"
        if audit_run is None:
            return "audit_run_missing"
        if (
            not isinstance(execution_generation, str)
            or not execution_generation.strip()
        ):
            return "execution_generation_invalid"
        if task.status != "processing":
            return "task_not_processing"
        if task.execution_generation != execution_generation:
            return "task_generation_mismatch"
        if audit_run.reply_task_id != task.id:
            return "audit_run_task_mismatch"
        if audit_run.execution_generation != execution_generation:
            return "audit_run_generation_mismatch"
        if audit_run.role is not AgentRole.AUDIT:
            return "audit_run_role_invalid"
        if audit_run.status != "running":
            return "audit_run_not_running"
        if not audit_run.operation_id.strip():
            return "audit_run_operation_id_missing"
        if audit_run.parent_agent_run_id is None:
            return "audit_run_parent_missing"
        parent = self.task_store.get_agent_run(audit_run.parent_agent_run_id)
        if parent is None:
            return "parent_missing"
        if parent.reply_task_id != task.id:
            return "parent_task_mismatch"
        if parent.execution_generation != execution_generation:
            return "parent_generation_mismatch"
        if parent.role is not AgentRole.CONSUMER:
            return "parent_role_invalid"
        if parent.status != "completed":
            return "parent_not_completed"
        if parent.proposal_revision != audit_run.proposal_revision:
            return "parent_revision_mismatch"
        runs = self.task_store.list_agent_runs_for_task_generation(
            task.id,
            execution_generation,
        )
        current_consumers = [
            run
            for run in runs
            if run.role is AgentRole.CONSUMER
            and run.status == "completed"
            and run.proposal_revision == audit_run.proposal_revision
        ]
        current_audits = [
            run
            for run in runs
            if run.role is AgentRole.AUDIT
            and run.status == "running"
            and run.proposal_revision == audit_run.proposal_revision
        ]
        if not current_consumers or max(
            current_consumers,
            key=lambda run: (run.turn_attempt, run.id),
        ).id != parent.id:
            return "superseded_consumer_turn"
        if not current_audits or max(
            current_audits,
            key=lambda run: (run.turn_attempt, run.id),
        ).id != audit_run.id:
            return "superseded_audit_turn"
        return ""

    def _execute_bound_effect(
        self,
        *,
        task: ReplyTask,
        audit_run: AgentRun,
        effect: EmailUnsubscribeEffect,
        entries: tuple[UnsubscribeEntry, ...],
    ) -> dict[str, object]:
        owner = {
            "owner_id": f"{self.owner_id}:{audit_run.id}",
            "generation": max(1, audit_run.turn_attempt + 1),
            "lease_token": f"unsubscribe-audit-lease:{uuid4().hex}",
        }
        claim = self.email_store.claim_email_unsubscribe_write(
            **_store_arguments(effect),
            owner=owner,
            task_id=task.id,
            task_execution_generation=task.execution_generation,
            task_lifecycle_version=AUDITED_LIFECYCLE_VERSION,
            task_action_type=EmailAction.UNSUBSCRIBE.value,
            audit_agent_run_id=audit_run.id,
        )
        if claim is None:
            return self._failed("unsubscribe_authorization_rejected")
        if not claim["acquired"]:
            if claim["status"] == "done":
                snapshot = self.email_store.get_email_unsubscribe_terminal_snapshot(
                    effect.action_identity,
                    effect.effect_digest,
                    task_id=task.id,
                    task_execution_generation=task.execution_generation,
                )
                if snapshot is None:
                    return self._failed("unsubscribe_receipt_missing")
                receipt = snapshot["receipt"]
                assert isinstance(receipt, Mapping)
                return {
                    "status": "done",
                    "outcome": receipt["outcome"],
                    "receipt_id": receipt["receipt_id"],
                    "evidence": receipt["evidence"],
                    "result_text": receipt["result_text"],
                    "observation_digest": receipt["observation_digest"],
                    "started_at": receipt["started_at"],
                    "completed_at": receipt["completed_at"],
                    "summary": "Unsubscribe already completed.",
                    "error": AgentError().model_dump(mode="json"),
                }
            return self._failed(
                "unsubscribe_operation_already_claimed",
                retryable=True,
            )
        self.email_store.advance_email_unsubscribe_phase(
            effect.action_identity,
            "navigating",
            owner=owner,
        )
        raw_result = _execute_one_effect(
            self.execute_effect,
            effect,
            entries,
            owner,
            executed_prefix_length=int(claim["executed_prefix_length"]),
        )
        result = _normalize_result(raw_result)
        if result.status == "awaiting_audit":
            durable = self.email_store.get_email_unsubscribe_claim(
                effect.action_identity
            )
            if (
                durable is None
                or durable["status"] != "awaiting_audit"
                or durable["audit_agent_run_id"] != audit_run.id
            ):
                return self._failed("unsubscribe_continuation_missing")
            return result.model_dump(mode="json")
        if result.status == "failed":
            current = self.email_store.get_email_unsubscribe_claim(
                effect.action_identity
            )
            if current is not None and current["status"] == "dispatching":
                self.email_store.release_email_unsubscribe_failed_operation(
                    effect.action_identity,
                    effect_digest=effect.effect_digest,
                    owner=owner,
                )
            return result.model_dump(mode="json")
        if result.status != "done" or not result.receipt_id.strip():
            raise ValueError("unsubscribe operation result is not terminal")
        snapshot = self.email_store.get_email_unsubscribe_terminal_snapshot(
            effect.action_identity,
            effect.effect_digest,
            task_id=task.id,
            task_execution_generation=task.execution_generation,
            expected_audit_agent_run_id=audit_run.id,
            expected_owner=owner,
        )
        if snapshot is not None:
            if not _matches_terminal_result(snapshot, result):
                return self._failed("unsubscribe_persisted_result_mismatch")
            return result.model_dump(mode="json")
        self.email_store.persist_email_unsubscribe_terminal(
            **_store_arguments(effect),
            outcome=result.outcome or "done",
            receipt_id=result.receipt_id,
            evidence=result.evidence or "terminal-result",
            result_text=result.result_text,
            observation_digest=result.observation_digest,
            started_at=result.started_at,
            completed_at=result.completed_at,
            final_step=result.final_step,
            claim_owner=owner,
        )
        snapshot = self.email_store.get_email_unsubscribe_terminal_snapshot(
            effect.action_identity,
            effect.effect_digest,
            task_id=task.id,
            task_execution_generation=task.execution_generation,
            expected_audit_agent_run_id=audit_run.id,
            expected_owner=owner,
        )
        if snapshot is None or not _matches_terminal_result(snapshot, result):
            return self._failed("unsubscribe_persisted_result_mismatch")
        return result.model_dump(mode="json")

    @staticmethod
    def _failed(
        code: str, *, retryable: bool = False, detail: str = ""
    ) -> dict[str, object]:
        return {
            "status": "failed",
            # The Audit model reads the summary; tell it why so the next turn
            # can correct the call instead of repeating it blind.
            "summary": f"{code}: {detail}" if detail else code,
            "error": AgentError(
                code=code,
                retryable=retryable,
            ).model_dump(mode="json"),
        }


def _rejection_detail(exc: Exception) -> str:
    """Say why a call was rejected without echoing what the call carried.

    Only this package's own rejection messages are reported: they name the
    field that failed and never its value. A validator built out of the
    proposal quotes the value it rejected - and a proposal carries the private
    unsubscribe URL - so anything else keeps its type name and nothing more.
    """

    if type(exc) is not ValueError:
        return ""
    detail = " ".join(str(exc).split())
    return detail if len(detail) <= _REJECTION_DETAIL_LIMIT else ""


def _bound_parent_consumer_action(
    parent: AgentRun,
    accepted_action: Mapping[str, object],
) -> ProposedAction:
    """Bind the Audit's acceptance to the Consumer's persisted proposal.

    The Audit identifies the action it accepts by ``action_identity``; the
    action that executes is always the Consumer's persisted proposal. The
    model therefore never has to reproduce the proposal byte for byte, and a
    drifted copy (missing field, truncated digest, whole proposal instead of
    one action) cannot change what runs.
    """
    try:
        parent_result = ConsumerAgentResult.model_validate_json(
            parent.final_result_json
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("unsubscribe Consumer proposal is invalid") from exc
    if (
        parent_result.outcome is not ConsumerOutcome.PROPOSAL
        or parent_result.proposal is None
        or len(parent_result.proposal.actions) != 1
    ):
        raise ValueError("unsubscribe Consumer proposal must contain one action")
    proposed = parent_result.proposal.actions[0]
    accepted_identity = _accepted_action_identity(accepted_action)
    if not hmac.compare_digest(
        hashlib.sha256(proposed.action_identity.encode("utf-8")).digest(),
        hashlib.sha256(accepted_identity.encode("utf-8")).digest(),
    ):
        raise ValueError(
            "accepted_action.action_identity does not match the Consumer "
            f"proposal (expected {proposed.action_identity})"
        )
    return proposed


def _accepted_action_identity(accepted_action: Mapping[str, object]) -> str:
    candidate: object = accepted_action.get("action_identity")
    if candidate is None:
        # Tolerate the whole proposal being passed instead of its one action.
        actions = accepted_action.get("actions")
        if isinstance(actions, list) and len(actions) == 1 and isinstance(
            actions[0], Mapping
        ):
            candidate = actions[0].get("action_identity")
    if not isinstance(candidate, str) or not candidate.strip():
        raise ValueError(
            "accepted_action must carry the action_identity of the accepted "
            "Consumer proposal action"
        )
    return candidate.strip()



def _task_payload(task: ReplyTask) -> dict[str, object]:
    try:
        payload = json.loads(task.trigger_message_json)
    except (TypeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("email task metadata is invalid") from exc
    if not isinstance(payload, dict):
        raise ValueError("email task metadata must be an object")
    return payload


def _terminal_expected_effect(
    task: ReplyTask,
    accepted_action: ProposedAction,
) -> dict[str, object]:
    """Validate a terminal proposal without rebuilding deleted continuation state."""

    payload = accepted_action.payload
    if set(payload) != {"operations"}:
        raise ValueError("accepted unsubscribe payload must contain exact operations")
    operations_value = payload.get("operations")
    if (
        not isinstance(operations_value, Sequence)
        or isinstance(operations_value, str | bytes | bytearray)
        or not operations_value
        or any(not isinstance(item, Mapping) for item in operations_value)
    ):
        raise ValueError("accepted unsubscribe operations must be a non-empty list")
    typed_operations = tuple(
        UnsubscribeOperation.from_mapping(item)
        for item in operations_value
        if isinstance(item, Mapping)
    )
    operation_mappings = [
        {
            "operation_reference": operation.operation_reference,
            "kind": operation.kind.value,
            "target_reference": operation.target_reference,
        }
        for operation in typed_operations
    ]
    initial = accepted_action.model_dump(mode="json")
    initial["payload"] = {"operations": [operation_mappings[0]]}
    initial_effect = accepted_email_unsubscribe_effect(
        task,
        ProposedAction.model_validate(initial),
    )
    return {
        "action_identity": initial_effect.action_identity,
        "action_plan_id": initial_effect.action_plan_id,
        "action_plan_version": initial_effect.action_plan_version,
        "classification_id": initial_effect.classification_id,
        "account_id": initial_effect.account_id,
        "stable_message_identity": initial_effect.stable_message_identity,
        "thread_identity": initial_effect.thread_identity,
        "entry_reference": initial_effect.entry_reference,
        "operations": operation_mappings,
    }


def _terminal_snapshot_result(
    snapshot: Mapping[str, object],
) -> dict[str, object]:
    receipt = snapshot.get("receipt")
    if not isinstance(receipt, Mapping):
        raise ValueError("unsubscribe terminal receipt is unavailable")
    return {
        "status": "done",
        "outcome": receipt["outcome"],
        "receipt_id": receipt["receipt_id"],
        "evidence": receipt["evidence"],
        "result_text": receipt["result_text"],
        "observation_digest": receipt["observation_digest"],
        "started_at": receipt["started_at"],
        "completed_at": receipt["completed_at"],
        "summary": "Unsubscribe already completed.",
        "error": AgentError().model_dump(mode="json"),
    }


def _validate_task_identity(
    task: ReplyTask,
    payload: Mapping[str, object],
) -> dict[str, object]:
    if (
        task.channel != "email"
        or payload.get("schema") != PAYLOAD_SCHEMA
        or payload.get("lifecycle_version") != AUDITED_LIFECYCLE_VERSION
        or payload.get("action_type") != EmailAction.UNSUBSCRIBE.value
        or task.trigger_message_id != payload.get("action_identity")
    ):
        raise ValueError("email unsubscribe task identity is invalid")
    return {
        "action_identity": _required_text(payload, "action_identity"),
        "action_plan_id": _required_text(payload, "action_plan_id"),
        "action_plan_version": _required_positive_int(
            payload,
            "action_plan_version",
        ),
        "classification_id": _required_positive_int(payload, "classification_id"),
        "account_id": _required_text(payload, "account_id"),
        "stable_message_identity": _required_text(
            payload,
            "stable_message_identity",
        ),
        "thread_identity": _required_text(payload, "thread_identity"),
        "entry_references": _projected_entry_references(payload),
    }


def _current_action_plan(classification: Mapping[str, object]) -> EmailActionPlan:
    raw_plan = classification.get("action_plan")
    try:
        return (
            raw_plan
            if isinstance(raw_plan, EmailActionPlan)
            else EmailActionPlan.model_validate_json(json.dumps(raw_plan))
        )
    except (TypeError, ValueError, RecursionError, OverflowError) as exc:
        raise ValueError("unsubscribe action plan is unavailable") from exc


def _validate_current_plan(
    identity: Mapping[str, object],
    classification: Mapping[str, object],
    plan: EmailActionPlan,
) -> None:
    if (
        classification.get("current_action_plan_id") != identity["action_plan_id"]
        or plan.action_plan_id != identity["action_plan_id"]
        or plan.action_plan_version != identity["action_plan_version"]
        or plan.classification_id != identity["classification_id"]
        or plan.account_id != identity["account_id"]
        or classification.get("account_id") != identity["account_id"]
        or classification.get("stable_message_identity")
        != identity["stable_message_identity"]
        or classification.get("thread_id") != identity["thread_identity"]
        or EmailAction.UNSUBSCRIBE not in plan.actions
    ):
        raise ValueError("email unsubscribe ActionPlan is not current")


def _locator_from_classification(
    classification: Mapping[str, object],
    identity: Mapping[str, object],
) -> EmailProviderLocator:
    locator = EmailProviderLocator(
        account_id=_required_text(classification, "account_id"),
        folder=_required_text(classification, "folder"),
        uidvalidity=_required_positive_int(classification, "uidvalidity"),
        uid=_required_positive_int(classification, "uid"),
        rfc_message_id=classification.get("rfc_message_id") or None,
        thread_id=classification.get("thread_id") or None,
    )
    if locator.stable_message_identity != identity["stable_message_identity"]:
        raise ValueError("email unsubscribe provider locator changed")
    return locator


def _continuation_from_store(
    value: Mapping[str, object],
    identity: Mapping[str, object],
    claim: Mapping[str, object] | None,
) -> EmailUnsubscribeContinuation:
    if claim is None or claim.get("status") != "awaiting_audit":
        raise ValueError("unsubscribe continuation claim is unavailable")
    for claim_field, identity_field in (
        ("action_identity", "action_identity"),
        ("action_plan_id", "action_plan_id"),
        ("action_plan_version", "action_plan_version"),
        ("classification_id", "classification_id"),
        ("account_id", "account_id"),
        ("stable_message_identity", "stable_message_identity"),
        ("thread_identity", "thread_identity"),
    ):
        if claim.get(claim_field) != identity.get(identity_field):
            raise ValueError("unsubscribe continuation identity changed")
    entry_reference = _required_text(claim, "entry_reference")
    entry_references = identity.get("entry_references")
    if (
        not isinstance(entry_references, tuple)
        or entry_reference not in entry_references
    ):
        raise ValueError("unsubscribe continuation entry changed")
    return EmailUnsubscribeContinuation(
        action_identity=str(identity["action_identity"]),
        action_plan_id=str(identity["action_plan_id"]),
        action_plan_version=int(identity["action_plan_version"]),
        classification_id=int(identity["classification_id"]),
        account_id=str(identity["account_id"]),
        stable_message_identity=str(identity["stable_message_identity"]),
        thread_identity=str(identity["thread_identity"]),
        entry_reference=entry_reference,
        effect_digest=str(value["effect_digest"]),
        previous_effect_digest=str(value["previous_effect_digest"]),
        executed_operations=tuple(
            UnsubscribeOperation.from_mapping(item) for item in value["operations"]
        ),
        controls=tuple(
            UnsubscribeDiscoveredControl(**item) for item in value["controls"]
        ),
    )


def _projected_entry_references(payload: Mapping[str, object]) -> tuple[str, ...]:
    entries = payload.get("unsubscribe_entries")
    if not isinstance(entries, list) or not entries:
        raise ValueError("email unsubscribe entry is unavailable")
    references = [
        value.get("reference")
        for value in entries
        if isinstance(value, Mapping)
        and set(value) == {"index", "source", "digest", "reference"}
        and isinstance(value.get("reference"), str)
        and str(value.get("reference")).strip()
    ]
    if len(references) != len(entries) or len(set(references)) != len(references):
        raise ValueError("email unsubscribe entry metadata is invalid")
    return tuple(str(reference) for reference in references)


def _store_arguments(effect: EmailUnsubscribeEffect) -> dict[str, object]:
    return {
        "action_identity": effect.action_identity,
        "effect_digest": effect.effect_digest,
        "action_plan_id": effect.action_plan_id,
        "action_plan_version": effect.action_plan_version,
        "classification_id": effect.classification_id,
        "account_id": effect.account_id,
        "stable_message_identity": effect.stable_message_identity,
        "thread_identity": effect.thread_identity,
        "entry_reference": effect.entry_reference,
        "operations": effect.operation_mappings,
        "previous_effect_digest": effect.previous_effect_digest,
    }


def _matches_terminal_result(
    snapshot: Mapping[str, object],
    result: EmailUnsubscribeAuditOperationResult,
) -> bool:
    receipt = snapshot.get("receipt")
    steps = snapshot.get("steps")
    if not isinstance(receipt, Mapping) or not isinstance(steps, list):
        return False
    expected = {
        "outcome": result.outcome or "done",
        "receipt_id": result.receipt_id,
        "evidence": result.evidence or "terminal-result",
        "result_text": result.result_text,
        "observation_digest": result.observation_digest,
        "started_at": result.started_at,
        "completed_at": result.completed_at,
    }
    if not all(receipt.get(key) == value for key, value in expected.items()):
        return False
    if result.final_step is None:
        return not steps
    if set(result.final_step) != {"sequence", "operation", "state", "reference"}:
        return False
    return bool(steps) and steps[-1] == result.final_step


def _execute_one_effect(
    callback: Callable[..., object],
    effect: EmailUnsubscribeEffect,
    entries: tuple[UnsubscribeEntry, ...],
    owner: Mapping[str, object],
    *,
    executed_prefix_length: int,
) -> object:
    try:
        parameters = inspect.signature(callback).parameters.values()
    except (TypeError, ValueError):
        parameters = ()
    accepts_kwargs = any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters
    )
    kwargs: dict[str, object] = {}
    if accepts_kwargs or any(parameter.name == "owner" for parameter in parameters):
        kwargs["owner"] = owner
    if accepts_kwargs or any(
        parameter.name == "executed_prefix_length" for parameter in parameters
    ):
        kwargs["executed_prefix_length"] = executed_prefix_length
    return callback(effect, entries, **kwargs)


def _normalize_result(value: object) -> EmailUnsubscribeAuditOperationResult:
    if isinstance(value, UnsubscribeContinuationResult):
        return EmailUnsubscribeAuditOperationResult(
            status="awaiting_audit",
            # The next operation is proposed by a new Consumer turn, not by
            # another call inside this one: a second call from this turn still
            # carries the persisted one-operation proposal and is rejected as a
            # non-append-only continuation.
            summary=(
                "Unsubscribe needs one further audited operation. End this turn "
                "without calling the tool again; the next operation comes from "
                "a new Consumer proposal built on this continuation."
            ),
            continuation=value.redacted["continuation"],
        )
    if isinstance(value, UnsubscribeExecutionResult):
        receipt = value.receipt
        return EmailUnsubscribeAuditOperationResult(
            status=(
                "done"
                if value.disposition.task_status in {"done", "skipped"}
                else "failed"
            ),
            outcome=value.outcome.value,
            receipt_id="" if receipt is None else receipt.receipt_id,
            evidence="" if receipt is None else receipt.evidence,
            result_text=value.result_text,
            observation_digest=value.observation_digest,
            started_at=value.started_at,
            completed_at=value.completed_at,
            # The summary is display and model facing: keep it the typed
            # outcome. Page text stays in the redacted result_text field.
            summary=value.outcome.value,
            error=AgentError(
                code=value.error_code,
                retryable=value.disposition.retryable,
                # The code is the coarse, cross-module contract; the category
                # names the internal condition it came from. AgentError already
                # reserves source_code for exactly that diagnostic, so a
                # generic browser failure stays attributable without composing
                # the two into one string other modules would stop matching.
                source_code=value.error_category,
            ),
            final_step=(
                None
                if not value.journal
                else {
                    "sequence": len(value.journal),
                    "operation": value.journal[-1].operation,
                    "state": value.journal[-1].state,
                    "reference": value.journal[-1].reference,
                }
            ),
        )
    if not isinstance(value, Mapping):
        raise ValueError("unsubscribe effect returned an invalid result")
    return EmailUnsubscribeAuditOperationResult.model_validate(dict(value))


def _resolve_entries_with_authentication(
    callback: Callable[..., Sequence[UnsubscribeEntry]],
    locator: EmailProviderLocator,
    entry_reference: str,
    authentication: UnsubscribeAuthenticationEvidence | None,
) -> Sequence[UnsubscribeEntry]:
    try:
        parameters = inspect.signature(callback).parameters.values()
    except (TypeError, ValueError):
        parameters = ()
    accepts_kwargs = any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters
    )
    kwargs: dict[str, object] = {}
    optional = {"authentication": authentication}
    names = {parameter.name for parameter in parameters}
    for name, value in optional.items():
        if accepts_kwargs or name in names:
            kwargs[name] = value
    return callback(locator, entry_reference, **kwargs)


def _authentication_from_payload(
    payload: Mapping[str, object],
) -> UnsubscribeAuthenticationEvidence | None:
    raw = payload.get("unsubscribe_authentication")
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ValueError("email unsubscribe authentication is invalid")
    verified = raw.get("one_click_verified")
    if not isinstance(verified, bool):
        raise ValueError("email unsubscribe authentication is invalid")
    return UnsubscribeAuthenticationEvidence(
        dkim_covers_list_unsubscribe=verified,
        dkim_covers_list_unsubscribe_post=verified,
        evidence_reference=_required_text(raw, "evidence_reference"),
    )


def _required_text(source: Mapping[str, object], field: str) -> str:
    value = source.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty text")
    return value.strip()


def _required_texts(source: Mapping[str, object], field: str) -> list[str]:
    values = source.get(field)
    if not isinstance(values, list) or not values:
        raise ValueError(f"{field} must be a non-empty list")
    return [_required_text({"value": value}, "value") for value in values]


def _required_positive_int(source: Mapping[str, object], field: str) -> int:
    value = source.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return value
