"""Authorize one Consumer-direct unsubscribe operation from a queued Email task.

The public operation boundary deliberately accepts only a task identity and its
current execution generation. All provider coordinates and private entry URLs
are loaded or resolved by the service after the durable authorization fence.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import inspect
import json
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from app.agent_result import AgentError
from app.email_classifier_contracts import (
    EmailAction,
    EmailActionPlan,
    EmailProviderLocator,
)
from app.email_store import EmailStore
from app.email_unsubscribe import (
    EmailUnsubscribeEffect,
    UnsubscribeAuthenticationEvidence,
    UnsubscribeEntry,
    UnsubscribeOperation,
    UnsubscribeOperationKind,
)
from app.store import AutoReplyStore, ReplyTask


DIRECT_LIFECYCLE_VERSION = "email_unsubscribe_consumer_direct_v1"
PAYLOAD_SCHEMA = "email_agent_action.v1"


class EmailUnsubscribeTaskOperationResult(BaseModel):
    """Small result contract passed back to the Consumer-direct runner."""

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


class EmailUnsubscribeTaskOperation:
    """Run a task-bound unsubscribe effect behind one atomic authorization fence.

    ``resolve_entries`` is the provider adapter seam. It receives the exact
    persisted locator and returns runtime-only entries, including any private
    URL needed by the browser. The callback is invoked only after the task and
    current ActionPlan have been claimed atomically.
    """

    def __init__(
        self,
        *,
        task_store: AutoReplyStore,
        email_store: EmailStore,
        resolve_entries: Callable[
            [EmailProviderLocator, str], Sequence[UnsubscribeEntry]
        ],
        execute_effect: Callable[..., Mapping[str, object]],
        owner_id: str = "email-unsubscribe-consumer",
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

    def execute(self, task_id: int, execution_generation: str) -> dict[str, object]:
        """Execute only the task identified by ``task_id`` and generation."""

        task = (
            self.task_store.get_reply_task(task_id)
            if isinstance(task_id, int) and not isinstance(task_id, bool)
            else None
        )
        if task is None:
            return self._failed("unsubscribe_task_not_found")
        if (
            not isinstance(execution_generation, str)
            or not execution_generation.strip()
        ):
            return self._failed("unsubscribe_execution_generation_invalid")
        if (
            task.status != "processing"
            or task.execution_generation != execution_generation
        ):
            return self._failed("unsubscribe_task_claim_stale")
        try:
            payload = _task_payload(task)
            identity = _validate_task_identity(task, payload)
            classification = self.email_store.get_classification(
                identity["classification_id"]
            )
            if classification is None:
                return self._failed("unsubscribe_classification_missing")
            raw_plan = classification.get("action_plan")
            try:
                plan = (
                    raw_plan
                    if isinstance(raw_plan, EmailActionPlan)
                    else EmailActionPlan.model_validate_json(json.dumps(raw_plan))
                )
            except (TypeError, ValueError):
                return self._failed("unsubscribe_action_plan_missing")
            _validate_current_plan(identity, classification, plan)
            locator = _locator_from_classification(classification, identity)
            entry_reference, operation_kind = _select_entry(payload)
            effect = EmailUnsubscribeEffect(
                action_identity=identity["action_identity"],
                action_plan_id=plan.action_plan_id,
                action_plan_version=plan.action_plan_version,
                classification_id=plan.classification_id,
                account_id=plan.account_id,
                stable_message_identity=identity["stable_message_identity"],
                thread_identity=identity["thread_identity"],
                entry_reference=entry_reference,
                operations=(
                    UnsubscribeOperation(
                        operation_reference="unsubscribe-entry-operation",
                        kind=operation_kind,
                        target_reference=entry_reference,
                    ),
                ),
                network_policy_reference=_required_text(
                    payload, "unsubscribe_network_policy_reference"
                ),
                network_policy_origin_references=tuple(
                    _required_texts(
                        payload, "unsubscribe_network_policy_origin_references"
                    )
                ),
            )
            owner = {
                "owner_id": self.owner_id,
                "generation": max(1, int(task.attempts)),
                "lease_token": f"unsubscribe-lease:{uuid4().hex}",
            }
            claim = self.email_store.claim_email_unsubscribe_write(
                **_store_arguments(effect),
                owner=owner,
                task_id=task.id,
                task_execution_generation=execution_generation,
                task_lifecycle_version=DIRECT_LIFECYCLE_VERSION,
                task_action_type=EmailAction.UNSUBSCRIBE.value,
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
                        "receipt_id": receipt["receipt_id"],
                        "summary": "Unsubscribe already completed.",
                        "result_text": receipt["result_text"],
                        "observation_digest": receipt["observation_digest"],
                        "started_at": receipt["started_at"],
                        "completed_at": receipt["completed_at"],
                        "error": AgentError().model_dump(mode="json"),
                    }
                return self._failed(
                    "unsubscribe_operation_already_claimed", retryable=True
                )

            self.email_store.advance_email_unsubscribe_phase(
                effect.action_identity,
                "navigating",
                owner=owner,
            )

            entries = tuple(
                _resolve_entries_with_authentication(
                    self.resolve_entries,
                    locator,
                    entry_reference,
                    _authentication_from_payload(payload),
                )
            )
            entry = next(
                (
                    candidate
                    for candidate in entries
                    if candidate.reference == entry_reference
                ),
                None,
            )
            if entry is None:
                self.email_store.mark_email_unsubscribe_uncertain(
                    effect.action_identity,
                    owner=owner,
                )
                return self._failed("unsubscribe_entry_changed")
            raw_result = _execute_effect_with_owner(
                self.execute_effect,
                effect,
                entry,
                owner,
                automatic_continuation=lambda **kwargs: self.email_store.continue_email_unsubscribe_consumer_direct(
                    **kwargs,
                    task_id=task.id,
                    task_execution_generation=execution_generation,
                    task_lifecycle_version=DIRECT_LIFECYCLE_VERSION,
                    task_action_type=EmailAction.UNSUBSCRIBE.value,
                ),
            )
            result = EmailUnsubscribeTaskOperationResult.model_validate(raw_result)
            if result.status == "failed":
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
        except Exception as exc:  # noqa: BLE001 - public boundary is fail-closed
            return self._failed(
                f"unsubscribe_operation_rejected:{type(exc).__name__}"
            )

    @staticmethod
    def _failed(code: str, *, retryable: bool = False) -> dict[str, object]:
        return {
            "status": "failed",
            "summary": code,
            "error": AgentError(
                code=code, retryable=retryable
            ).model_dump(mode="json"),
        }


def _task_payload(task: ReplyTask) -> dict[str, object]:
    try:
        payload = json.loads(task.trigger_message_json)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("email task metadata is invalid") from exc
    if not isinstance(payload, dict):
        raise ValueError("email task metadata must be an object")
    return payload


def _validate_task_identity(
    task: ReplyTask, payload: Mapping[str, object]
) -> dict[str, object]:
    if (
        task.channel != "email"
        or task.trigger_message_id != payload.get("action_identity")
        or payload.get("schema") != PAYLOAD_SCHEMA
        or payload.get("lifecycle_version") != DIRECT_LIFECYCLE_VERSION
        or payload.get("action_type") != EmailAction.UNSUBSCRIBE.value
    ):
        raise ValueError("email unsubscribe task identity is invalid")
    identity = {
        "action_identity": _required_text(payload, "action_identity"),
        "action_plan_id": _required_text(payload, "action_plan_id"),
        "action_plan_version": _required_positive_int(
            payload, "action_plan_version"
        ),
        "classification_id": _required_positive_int(payload, "classification_id"),
        "account_id": _required_text(payload, "account_id"),
        "stable_message_identity": _required_text(
            payload, "stable_message_identity"
        ),
        "thread_identity": _required_text(payload, "thread_identity"),
    }
    if identity["action_identity"] != task.trigger_message_id:
        raise ValueError("email unsubscribe action identity is invalid")
    return identity


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
    classification: Mapping[str, object], identity: Mapping[str, object]
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


def _select_entry(
    payload: Mapping[str, object],
) -> tuple[str, UnsubscribeOperationKind]:
    entries = payload.get("unsubscribe_entries")
    if not isinstance(entries, list) or not entries:
        raise ValueError("email unsubscribe entry is unavailable")
    candidates: list[tuple[int, str, str]] = []
    for value in entries:
        if not isinstance(value, Mapping) or set(value) != {
            "source",
            "reference",
            "priority",
        }:
            raise ValueError("email unsubscribe entry metadata is invalid")
        source = _required_text(value, "source")
        reference = _required_text(value, "reference")
        priority = _required_non_negative_int(value, "priority")
        if source == "header_one_click_https":
            auth = payload.get("unsubscribe_authentication")
            if (
                not isinstance(auth, Mapping)
                or auth.get("one_click_verified") is not True
            ):
                raise ValueError("email unsubscribe one-click evidence is invalid")
            kind = UnsubscribeOperationKind.POST_ONE_CLICK
        elif source in {
            "header_https",
            "body_html_https",
            "body_text_https",
        }:
            kind = UnsubscribeOperationKind.OPEN_ENTRY
        else:
            continue
        candidates.append((priority, reference, kind.value))
    if not candidates:
        raise ValueError("email unsubscribe has no reliable entry")
    _, reference, kind = min(candidates)
    return reference, UnsubscribeOperationKind(kind)


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


def _required_non_negative_int(source: Mapping[str, object], field: str) -> int:
    value = source.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def _execute_effect_with_owner(
    callback: Callable[..., Mapping[str, object]],
    effect: EmailUnsubscribeEffect,
    entry: UnsubscribeEntry,
    owner: Mapping[str, object],
    automatic_continuation: Callable[..., Mapping[str, object]],
) -> Mapping[str, object]:
    """Pass the authorization lease to production effects, keeping test seams."""

    try:
        parameters = inspect.signature(callback).parameters.values()
    except (TypeError, ValueError):
        parameters = ()
    accepts_owner = any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        or parameter.name == "owner"
        for parameter in parameters
    )
    accepts_continuation = any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        or parameter.name == "automatic_continuation"
        for parameter in parameters
    )
    kwargs: dict[str, object] = {}
    if accepts_owner:
        kwargs["owner"] = owner
    if accepts_continuation:
        kwargs["automatic_continuation"] = automatic_continuation
    if kwargs:
        return callback(effect, entry, **kwargs)
    return callback(effect, entry)


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
    accepts_authentication = any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        or parameter.name == "authentication"
        for parameter in parameters
    )
    if accepts_authentication:
        return callback(
            locator,
            entry_reference,
            authentication=authentication,
        )
    return callback(locator, entry_reference)


def _authentication_from_payload(
    payload: Mapping[str, object],
) -> UnsubscribeAuthenticationEvidence | None:
    raw = payload.get("unsubscribe_authentication")
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ValueError("email unsubscribe authentication is invalid")
    evidence_reference = _required_text(raw, "evidence_reference")
    verified = raw.get("one_click_verified")
    if not isinstance(verified, bool):
        raise ValueError("email unsubscribe authentication is invalid")
    return UnsubscribeAuthenticationEvidence(
        dkim_covers_list_unsubscribe=verified,
        dkim_covers_list_unsubscribe_post=verified,
        evidence_reference=evidence_reference,
    )
