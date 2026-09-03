"""Execute one accepted Email unsubscribe operation from a bound Audit run."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import inspect
import json
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from app.agent_contracts import ProposedAction
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
    browser_network_policy_for_entries,
    browser_unsubscribe_entries,
)
from app.store import AgentRole, AgentRun, AutoReplyStore, ReplyTask


AUDITED_LIFECYCLE_VERSION = "email_unsubscribe_audited_v2"
PAYLOAD_SCHEMA = "email_agent_action.v1"


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
        if not self._is_current_running_audit(
            task,
            audit_run,
            execution_generation,
        ):
            return self._failed("unsubscribe_audit_run_invalid")
        assert task is not None and audit_run is not None
        try:
            payload = _task_payload(task)
            identity = _validate_task_identity(task, payload)
            classification = self.email_store.get_classification(
                identity["classification_id"]
            )
            if classification is None:
                return self._failed("unsubscribe_classification_missing")
            plan = _current_action_plan(classification)
            _validate_current_plan(identity, classification, plan)
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
            action = ProposedAction.model_validate(accepted_action)
            effect = accepted_email_unsubscribe_effect(
                task,
                action,
                continuation=continuation,
            )
            entries = browser_unsubscribe_entries(tuple(
                _resolve_entries_with_authentication(
                    self.resolve_entries,
                    locator,
                    effect.entry_reference,
                    _authentication_from_payload(payload),
                    network_policy_reference=str(
                        identity["network_policy_reference"]
                    ),
                    network_policy_origin_references=tuple(
                        identity["network_policy_origin_references"]
                    ),
                )
            ))
            if not entries:
                return self._failed("unsubscribe_entry_changed")
            current_policy = browser_network_policy_for_entries(entries)
            if (
                current_policy.reference != effect.network_policy_reference
                or current_policy.origin_references
                != effect.network_policy_origin_references
            ):
                return self._failed("unsubscribe_network_policy_changed")
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
            return self._failed(f"unsubscribe_operation_rejected:{type(exc).__name__}")

    def _is_current_running_audit(
        self,
        task: ReplyTask | None,
        audit_run: AgentRun | None,
        execution_generation: object,
    ) -> bool:
        if (
            task is None
            or audit_run is None
            or not isinstance(execution_generation, str)
            or not execution_generation.strip()
            or task.status != "processing"
            or task.execution_generation != execution_generation
            or audit_run.reply_task_id != task.id
            or audit_run.execution_generation != execution_generation
            or audit_run.role is not AgentRole.AUDIT
            or audit_run.status != "running"
            or not audit_run.operation_id.strip()
            or audit_run.parent_agent_run_id is None
        ):
            return False
        parent = self.task_store.get_agent_run(audit_run.parent_agent_run_id)
        if (
            parent is None
            or parent.reply_task_id != task.id
            or parent.execution_generation != execution_generation
            or parent.role is not AgentRole.CONSUMER
            or parent.status != "completed"
            or parent.proposal_revision != audit_run.proposal_revision
        ):
            return False
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
        return (
            bool(current_consumers)
            and max(
                current_consumers,
                key=lambda run: (run.turn_attempt, run.id),
            ).id
            == parent.id
            and bool(current_audits)
            and max(
                current_audits,
                key=lambda run: (run.turn_attempt, run.id),
            ).id
            == audit_run.id
        )

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
                receipt = self.email_store.get_email_unsubscribe_receipt(
                    effect.action_identity
                )
                if receipt is None:
                    return self._failed("unsubscribe_receipt_missing")
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
                self.email_store.mark_email_unsubscribe_uncertain(
                    effect.action_identity,
                    owner=owner,
                )
            return result.model_dump(mode="json")
        if result.status != "done" or not result.receipt_id.strip():
            raise ValueError("unsubscribe operation result is not terminal")
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
        return result.model_dump(mode="json")

    @staticmethod
    def _failed(code: str, *, retryable: bool = False) -> dict[str, object]:
        return {
            "status": "failed",
            "summary": code,
            "error": AgentError(
                code=code,
                retryable=retryable,
            ).model_dump(mode="json"),
        }


def _task_payload(task: ReplyTask) -> dict[str, object]:
    try:
        payload = json.loads(task.trigger_message_json)
    except (TypeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("email task metadata is invalid") from exc
    if not isinstance(payload, dict):
        raise ValueError("email task metadata must be an object")
    return payload


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
        "network_policy_reference": _required_text(
            payload,
            "unsubscribe_network_policy_reference",
        ),
        "network_policy_origin_references": tuple(
            _required_texts(
                payload,
                "unsubscribe_network_policy_origin_references",
            )
        ),
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
            UnsubscribeOperation.from_mapping(item)
            for item in value["operations"]
        ),
        controls=tuple(
            UnsubscribeDiscoveredControl(**item) for item in value["controls"]
        ),
        network_policy_reference=str(value["network_policy_reference"]),
        network_policy_origin_references=tuple(
            str(item) for item in value["network_policy_origin_references"]
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
        and set(value) == {"source", "reference", "priority"}
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
        "network_policy_reference": effect.network_policy_reference,
        "network_policy_origin_references": effect.network_policy_origin_references,
    }


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
            summary="Unsubscribe requires one further audited operation.",
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
            summary=value.result_text or value.outcome.value,
            error=AgentError(
                code=value.error_code,
                retryable=value.disposition.retryable,
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
    *,
    network_policy_reference: str,
    network_policy_origin_references: tuple[str, ...],
) -> Sequence[UnsubscribeEntry]:
    try:
        parameters = inspect.signature(callback).parameters.values()
    except (TypeError, ValueError):
        parameters = ()
    accepts_kwargs = any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters
    )
    kwargs: dict[str, object] = {}
    optional = {
        "authentication": authentication,
        "network_policy_reference": network_policy_reference,
        "network_policy_origin_references": network_policy_origin_references,
    }
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
