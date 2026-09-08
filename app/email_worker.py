"""Independent email scan, Agent-task, and training worker."""

from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import partial
from hashlib import sha256
from pathlib import Path
from threading import Event, Lock, Thread
from typing import Any, TextIO

from app.email_category_config import VerifiedEmailFolderBinding
from app.email_provider_folders import FolderRole, ProviderFolder


SCAN_INTERVAL_SECONDS = 60
CONSUMER_POLL_INTERVAL_SECONDS = 10
TRAINING_INTERVAL_SECONDS = 60
MAX_HEALTH_TEXT_LENGTH = 160
DIRECT_ACTION_DRAIN_MAX_ACTIONS = 25
DIRECT_ACTION_DRAIN_MAX_SECONDS = 2.0


def _agent_classification_action_plan(
    *,
    classification_id: int,
    account_id: str,
    result: object,
    unsubscribe_selection: object | None = None,
    folder_targets: Mapping[str, str],
    config_version: str,
    created_at: datetime,
    classification_source: str = "agent",
    model_id: str = "email-classifier-agent:v1",
    preserve_read: bool = False,
):
    """Translate a validated Agent decision into Task 3/4 deterministic actions."""

    from app.email_classifier_contracts import (
        ACTION_DEPENDENCY_PARAMETER,
        EmailAction,
        EmailActionAuthorization,
        build_email_action_plan,
    )

    if getattr(result, "certainty") == "uncertain":
        return None
    category = str(getattr(result, "category"))
    actions: tuple[EmailAction, ...]
    parameters: dict[EmailAction, Mapping[str, object]]
    if category == "junk":
        if unsubscribe_selection is None:
            actions = (EmailAction.TRASH,)
            parameters = {}
        else:
            index = getattr(unsubscribe_selection, "unsubscribe_candidate_index")
            source = getattr(unsubscribe_selection, "unsubscribe_candidate_source")
            digest = getattr(unsubscribe_selection, "unsubscribe_candidate_digest")
            reference = getattr(
                unsubscribe_selection, "unsubscribe_candidate_reference"
            )
            if any(value is None for value in (index, source, digest, reference)):
                raise ValueError("junk unsubscribe selection is incomplete")
            from app.email_unsubscribe import UnsubscribeEntrySource

            try:
                selected_source = UnsubscribeEntrySource(str(source))
            except ValueError as exc:
                raise ValueError(
                    "junk unsubscribe selection is not executable HTTPS"
                ) from exc
            if not selected_source.value.endswith("_https"):
                raise ValueError("junk unsubscribe selection is not executable HTTPS")
            actions = (EmailAction.UNSUBSCRIBE, EmailAction.TRASH)
            parameters = {
                EmailAction.UNSUBSCRIBE: {
                    "candidate_index": index,
                    "candidate_source": source,
                    "candidate_digest": digest,
                    "candidate_reference": reference,
                }
            }
        if preserve_read:
            actions += (EmailAction.MARK_READ,)
            parameters[EmailAction.MARK_READ] = {
                ACTION_DEPENDENCY_PARAMETER: [EmailAction.TRASH.value]
            }
    else:
        target = str(folder_targets.get(category) or "").strip()
        if not target:
            raise ValueError("certain business category has no verified folder target")
        actions = (EmailAction.MOVE,)
        parameters = {EmailAction.MOVE: {"target_folder": target}}
        if getattr(result, "important") is True:
            actions += (EmailAction.FLAG_IMPORTANT,)
        if preserve_read:
            actions += (EmailAction.MARK_READ,)
            dependencies = [EmailAction.MOVE.value]
            if EmailAction.FLAG_IMPORTANT in actions:
                parameters[EmailAction.FLAG_IMPORTANT] = {
                    ACTION_DEPENDENCY_PARAMETER: [EmailAction.MOVE.value]
                }
                dependencies.append(EmailAction.FLAG_IMPORTANT.value)
            parameters[EmailAction.MARK_READ] = {
                ACTION_DEPENDENCY_PARAMETER: dependencies
            }
    action_authorizations = None
    if classification_source == "model":
        action_authorizations = tuple(
            EmailActionAuthorization(
                action_type=action,
                parameters=dict(sorted(parameters.get(action, {}).items())),
                authorization_source="model_eligibility",
                eligibility_evidence_reference=f"email-model-promotion:{model_id}",
                authorized=True,
                ineligible_reason="",
                source_model_id=model_id,
                config_version=config_version,
            )
            for action in actions
        )
    return build_email_action_plan(
        classification_id=classification_id,
        account_id=account_id,
        category=category,
        classification_source=classification_source,
        confidence=float(getattr(result, "confidence")),
        model_id=model_id,
        config_version=config_version,
        actions=actions,
        action_parameters=parameters,
        created_at=created_at,
        action_authorizations=action_authorizations,
    )


def persist_model_primary_classification(
    email_store: object,
    action_task_producer: object,
    *,
    message: Mapping[str, object],
    prediction: object,
    context: object,
    model_id: str,
    model_text: str,
    unsubscribe_entries: Sequence[object],
    preserve_read: bool = False,
) -> object:
    """Persist an accepted model result through the existing immutable action path."""

    from hashlib import sha256
    from types import SimpleNamespace

    from app.email_classifier_contracts import (
        EmailAttachmentMetadata,
        EmailClassification,
        EmailClassificationStatus,
    )
    from app.email_classifier_scan import _provider_locator, _stable_message_identity
    from app.email_classifier_runtime import (
        OnlineModelAcceptError,
        OnlineModelAcceptOutcome,
        OnlineModelAcceptStage,
        OnlineModelDurableConflict,
    )
    from app.email_unsubscribe import browser_unsubscribe_entries

    stable_identity = ""
    plan = None
    try:
        if getattr(prediction, "category_accepted", None) is not True:
            raise ValueError("only an accepted model prediction may be persisted")
        locator = _provider_locator(message)
        stable_identity = _stable_message_identity(message, locator)
        if locator.thread_id is None:
            locator = locator.model_copy(update={"thread_id": stable_identity})
        existing_before = email_store.get_classification_by_stable_identity(
            stable_identity
        )
        classification_id = (
            int.from_bytes(sha256(stable_identity.encode("utf-8")).digest()[:8], "big")
            & ((1 << 63) - 1)
            or 1
        )
        category = str(prediction.category)
        entries = browser_unsubscribe_entries(
            tuple(unsubscribe_entries), normalize_indexes=True
        )
        selection = None
        if category == "junk" and entries:
            entry = entries[0]
            selection = SimpleNamespace(
                unsubscribe_candidate_index=entry.index,
                unsubscribe_candidate_source=entry.source.value,
                unsubscribe_candidate_digest=entry.reference.removeprefix(
                    "unsubscribe-entry:"
                ),
                unsubscribe_candidate_reference=entry.reference,
            )
        result = SimpleNamespace(
            category=category,
            important=bool(prediction.important),
            certainty="certain",
            confidence=float(prediction.category_probability),
        )
        plan = _agent_classification_action_plan(
            classification_id=classification_id,
            account_id=locator.account_id,
            result=result,
            unsubscribe_selection=selection,
            folder_targets=context.folder_targets,
            config_version=context.config_version,
            created_at=datetime.now(timezone.utc),
            classification_source="model",
            model_id=model_id,
            preserve_read=preserve_read,
        )
        classification = EmailClassification(
            classification_id=classification_id,
            stable_message_identity=stable_identity,
            provider_locator=locator,
            category=category,
            confidence=float(prediction.category_probability),
            margin=_prediction_margin(prediction.category_probabilities),
            probabilities=dict(prediction.category_probabilities),
            model_id=model_id,
            config_version=context.config_version,
            status=EmailClassificationStatus.PROCESSED,
            classification_source="model",
            action_plan=plan,
        )
        if existing_before is not None:
            durable_plan = _validated_model_accept_readback(
                existing_before,
                expected_plan=plan,
                model_id=model_id,
                category=category,
            )
            try:
                persisted = email_store.repair_current_action_plan(durable_plan)
            except Exception as exc:
                raise OnlineModelDurableConflict(
                    "durable model ActionPlan storage conflicts with readback"
                ) from exc
            if durable_plan.agent_actions:
                action_task_producer.produce(durable_plan, message)
            return OnlineModelAcceptOutcome.already_committed(persisted)
        sender_value = message.get("from") or message.get("sender") or {}
        sender = (
            str(sender_value.get("email") or sender_value.get("name") or "")
            if isinstance(sender_value, Mapping)
            else str(sender_value)
        )
        persisted = email_store.persist_scan_result(
            classification,
            sender=sender,
            recipients=tuple(),
            subject=str(message.get("subject") or ""),
            normalized_text=model_text,
            attachment_metadata=tuple(
                EmailAttachmentMetadata.model_validate(item)
                for item in message.get("attachments") or ()
            ),
            received_at=str(message.get("date") or ""),
            model_text=model_text,
        )
        if plan is not None and plan.agent_actions:
            action_task_producer.produce(plan, message)
        return OnlineModelAcceptOutcome.accepted(persisted)
    except OnlineModelAcceptError:
        raise
    except Exception as exc:
        try:
            durable = (
                email_store.get_classification_by_stable_identity(stable_identity)
                if stable_identity
                else None
            )
        except Exception as readback_exc:
            has_durable = bool(
                stable_identity
                and email_store.has_stable_classification(stable_identity)
            )
            if has_durable:
                raise OnlineModelDurableConflict(
                    "durable model classification cannot be validated"
                ) from readback_exc
            durable = None
        if durable is not None:
            if plan is None:
                raise OnlineModelDurableConflict(
                    "durable model classification has no expected ActionPlan"
                ) from exc
            _validated_model_accept_readback(
                durable,
                expected_plan=plan,
                model_id=model_id,
                category=str(prediction.category),
            )
        stage = (
            OnlineModelAcceptStage.AFTER_DURABLE_COMMIT
            if durable is not None
            else OnlineModelAcceptStage.BEFORE_DURABLE_COMMIT
        )
        raise OnlineModelAcceptError(stage, str(exc)) from exc


def _validated_model_accept_readback(
    existing: Mapping[str, object],
    *,
    expected_plan: object,
    model_id: str,
    category: str,
):
    from app.email_classifier_contracts import EmailActionPlan
    from app.email_classifier_runtime import OnlineModelDurableConflict

    try:
        durable_plan = EmailActionPlan.model_validate_json(
            json.dumps(existing.get("action_plan"))
        )
    except (TypeError, ValueError) as exc:
        raise OnlineModelDurableConflict(
            "durable model ActionPlan is invalid"
        ) from exc
    if not isinstance(expected_plan, EmailActionPlan):
        raise TypeError("expected model ActionPlan is invalid")
    expected = expected_plan
    classification_matches = (
        existing.get("classification_source") == "model"
        and existing.get("model_id") == model_id
        and existing.get("predicted_category") == category
        and existing.get("config_version") == expected.config_version
        and existing.get("id") == expected.classification_id
        and existing.get("current_action_plan_id") == durable_plan.action_plan_id
    )
    plan_matches = all(
        getattr(durable_plan, field_name) == getattr(expected, field_name)
        for field_name in (
            "action_plan_version",
            "classification_id",
            "account_id",
            "category",
            "classification_source",
            "confidence",
            "model_id",
            "config_version",
            "actions",
            "action_parameters",
        )
    )
    if not classification_matches or not plan_matches:
        raise OnlineModelDurableConflict(
            "stable identity is bound to a different durable model decision"
        )
    return durable_plan


def _prediction_margin(probabilities: Mapping[str, float]) -> float:
    ordered = sorted((float(value) for value in probabilities.values()), reverse=True)
    if not ordered:
        return 0.0
    return ordered[0] - (ordered[1] if len(ordered) > 1 else 0.0)


def _historical_action_outcome_from_receipts(
    actions: Sequence[str],
    receipts: Sequence[Mapping[str, object]],
    *,
    is_read: bool,
) -> str:
    """Derive the operation result from the full plan and durable receipts."""

    from app.email_classifier_contracts import DIRECT_ACTIONS

    direct_action_names = {
        action.value for action in DIRECT_ACTIONS if action.value in actions
    }
    receipt_action_names = {
        str(receipt.get("action_type") or "") for receipt in receipts
    }
    if not direct_action_names.issubset(receipt_action_names):
        return "actions_queued"
    if "mark_read" in direct_action_names and not is_read:
        return "read_state_not_preserved"
    if "move" in direct_action_names:
        return (
            "moved_and_flagged"
            if "flag_important" in direct_action_names
            else "moved"
        )
    if "trash" in direct_action_names:
        return "trashed"
    if "mark_read" in direct_action_names:
        return "read_preserved"
    if "unsubscribe" in actions:
        return "unsubscribe_queued"
    return "no_action" if not actions else "actions_queued"


def _historical_relocation_folder_verified(
    *,
    action_plan: Mapping[str, object],
    classification: Mapping[str, object],
    state: object,
) -> bool:
    """Verify relocation against the durable target and current provider state."""

    raw_actions = action_plan.get("actions")
    if not isinstance(raw_actions, list):
        return False
    actions = tuple(str(action) for action in raw_actions)
    if "move" not in actions and "trash" not in actions:
        return True

    persisted_folder = str(classification.get("folder") or "").strip()
    provider_folder = str(
        getattr(state, "provider_folder_name", None) or ""
    ).strip()
    if not persisted_folder or provider_folder != persisted_folder:
        return False

    if "move" in actions:
        raw_parameters = action_plan.get("action_parameters")
        if not isinstance(raw_parameters, Mapping):
            return False
        move_parameters = raw_parameters.get("move")
        if not isinstance(move_parameters, Mapping):
            return False
        target_folder = str(move_parameters.get("target_folder") or "").strip()
        return bool(target_folder) and persisted_folder == target_folder

    return getattr(state, "folder_role", None) is FolderRole.TRASH


def execute_historical_model_actions(
    email_store: object,
    direct_action_executor_factory: Callable[[str], object],
    action_task_producer: object,
    *,
    message: Mapping[str, object],
    prediction: object,
    context: object,
    model_id: str,
    model_text: str,
    unsubscribe_entries: Sequence[object],
    read_after: Callable[[], object],
    threshold: float | None = None,
) -> object:
    """Persist and synchronously read back a bounded historical action plan."""

    from app.email_historical_classifier import HistoricalActionResult

    persisted = persist_model_primary_classification(
        email_store,
        action_task_producer,
        message=message,
        prediction=prediction,
        context=context,
        model_id=model_id,
        model_text=model_text,
        unsubscribe_entries=unsubscribe_entries,
        preserve_read=True,
    )
    action_plan = persisted.persisted["action_plan"]
    actions = tuple(action_plan["actions"])
    action_ids = email_store.direct_action_ids_for_plan(
        action_plan["action_plan_id"]
    )
    email_store.begin_historical_operation(
        account_id=str(message["accountId"]),
        stable_message_identity=str(persisted.persisted["stable_message_identity"]),
        model_id=model_id,
        classification_id=int(persisted.persisted["id"]),
        action_plan_id=str(action_plan["action_plan_id"]),
        action_ids=action_ids,
        predicted_category=str(prediction.category),
        threshold=float(
            prediction.category_probability if threshold is None else threshold
        ),
        probability=float(prediction.category_probability),
        important=bool(prediction.important),
    )
    def current_read_state() -> bool:
        state = read_after()
        is_read = getattr(state, "is_read", None)
        if type(is_read) is not bool:
            raise TypeError("historical action readback must expose strict is_read")
        return is_read

    for action_id in action_ids:
        statuses = email_store.direct_action_statuses_for_plan(
            action_plan["action_plan_id"]
        )
        if statuses.get(action_id) == "done":
            continue
        result = _run_next_direct_action(
            email_store,
            direct_action_executor_factory,
            available_account_ids=lambda: (str(message["accountId"]),),
            action_id=action_id,
        )
        if result is None:
            return HistoricalActionResult(
                "provider_action_failed", is_read=current_read_state()
            )
        email_store.touch_historical_operation(
            model_id=model_id,
            stable_message_identity=str(persisted.persisted["stable_message_identity"]),
        )
        if getattr(result, "status", "") != "done":
            return HistoricalActionResult(
                "provider_action_failed", is_read=current_read_state()
            )
    state = read_after()
    is_read = getattr(state, "is_read", None)
    if type(is_read) is not bool:
        raise TypeError("historical action readback must expose strict is_read")
    classification = email_store.get_classification_by_stable_identity(
        str(persisted.persisted["stable_message_identity"])
    )
    if classification is None or not _historical_relocation_folder_verified(
        action_plan=action_plan,
        classification=classification,
        state=state,
    ):
        return HistoricalActionResult(
            "provider_folder_not_verified", is_read=is_read
        )
    receipts = email_store.direct_action_receipts_for_plan(
        action_plan["action_plan_id"]
    )
    outcome = _historical_action_outcome_from_receipts(
        actions, receipts, is_read=is_read
    )
    return HistoricalActionResult(outcome, is_read=is_read)


def run_manual_historical_batch(
    *,
    email_store: object,
    model_id: str,
    model: object,
    cache: object,
    historically_eligible: Mapping[str, bool],
    candidates: Sequence[object],
    read_state: Callable[[object], object],
    execute: Callable[[object, object], object],
) -> tuple[object, ...]:
    """Explicit staged/manual entrypoint; the worker component list never invokes it."""

    from app.email_historical_classifier import HistoricalClassifier

    def record_and_finish(outcome):
        if outcome.action_outcome in {
            "state_changed_before_execute",
            "provider_action_failed",
            "provider_folder_not_verified",
            "read_state_not_preserved",
        }:
            return None
        result = email_store.record_historical_classification_outcome(outcome)
        email_store.complete_historical_operation(outcome)
        return result

    return HistoricalClassifier(
        model_id=model_id,
        model=model,
        cache=cache,
        historically_eligible=historically_eligible,
        read_state=read_state,
        execute=execute,
        record=record_and_finish,
    ).run_batch(candidates)


def run_email_classification_task_once(
    adapter: object,
    agent: object,
    email_store: object,
    *,
    owner: str,
    provider_readback: Callable[
        [object, Mapping[str, object]], Mapping[str, object] | None
    ],
    action_task_producer: object,
) -> Mapping[str, object] | None:
    """Run one dedicated classifier task and persist its non-action decision."""

    from hashlib import sha256

    from app.email_classifier_contracts import (
        EmailAttachmentMetadata,
        EmailActionPlan,
        EmailClassification,
        EmailClassificationStatus,
        EmailProviderLocator,
    )

    task = adapter.claim_next(owner=owner)
    if task is None:
        return None
    try:
        payload = json.loads(task.input_json)
        canonical = email_store.get_classification_by_stable_identity(
            task.stable_message_identity
        )
        if canonical is not None:
            if not canonical.get("agent_result"):
                raise ValueError(
                    "stable canonical classification lacks Agent provenance"
                )
            from app.email_classifier_agent import DurableAgentClassificationResult

            stored_result = DurableAgentClassificationResult.model_validate(
                canonical["agent_result"]
            )
            outcome = {
                "decision_status": (
                    "processed"
                    if stored_result.certainty == "certain"
                    else "pending_feedback"
                ),
                "category": stored_result.category,
                "important": stored_result.important,
                "certainty": stored_result.certainty,
                "confidence": stored_result.confidence,
                "reason": stored_result.reason,
                "unsubscribe_candidate_index": (
                    stored_result.unsubscribe_candidate_index
                ),
                "unsubscribe_candidate_source": stored_result.unsubscribe_candidate_source,
                "unsubscribe_candidate_digest": stored_result.unsubscribe_candidate_digest,
                "unsubscribe_candidate_reference": stored_result.unsubscribe_candidate_reference,
                "classification_id": canonical["id"],
            }
            persisted_plan = canonical.get("action_plan")
            if persisted_plan is not None:
                plan = EmailActionPlan.model_validate_json(json.dumps(persisted_plan))
                if plan.agent_actions:
                    current_message = provider_readback(task, payload)
                    if current_message is None:
                        raise ValueError(
                            "persisted Agent action message is unavailable"
                        )
                    action_task_producer.produce(plan, current_message)
            adapter.complete(task, outcome)
            return outcome
        current_message = provider_readback(task, payload)
        locator_payload = payload.get("provider_locator")
        if not isinstance(locator_payload, Mapping):
            raise ValueError("classification task provider locator is invalid")
        if (
            current_message is None
            or current_message.get("stableMessageIdentity")
            != task.stable_message_identity
            or current_message.get("folder") != locator_payload.get("folder")
            or current_message.get("providerUnread") is not True
        ):
            outcome = {
                "decision_status": "skipped",
                "reason": "provider_message_no_longer_eligible",
            }
            adapter.complete(task, outcome)
            return outcome
        from app.email_imap_readonly import (
            ephemeral_body_html,
            ephemeral_unsubscribe_authentication,
        )
        from app.email_unsubscribe import (
            browser_unsubscribe_entries,
            extract_unsubscribe_entries,
        )
        from app.email_classifier_agent import durable_agent_classification_result

        body_text = str(
            current_message.get("markdownBody") or current_message.get("textBody") or ""
        )
        entries = browser_unsubscribe_entries(
            extract_unsubscribe_entries(
                list_unsubscribe=str(current_message.get("listUnsubscribe") or ""),
                list_unsubscribe_post=str(
                    current_message.get("listUnsubscribePost") or ""
                ),
                body_text=body_text,
                body_html=ephemeral_body_html(current_message),
                authentication_evidence=ephemeral_unsubscribe_authentication(
                    current_message
                ),
            ),
            normalize_indexes=True,
        )
        candidate_urls = tuple(entry.private_url for entry in entries)
        candidate_metadata = tuple(
            {
                "index": entry.index,
                "source": entry.source.value,
                "scheme": entry.scheme,
                "host": entry.host,
                "context": entry.context,
                "reference": entry.reference,
            }
            for entry in entries
        )
        prompt_message = dict(payload.get("message") or {})
        prompt_message["text"] = body_text
        prompt_message["subject"] = str(current_message.get("subject") or "")
        result = agent.classify(
            task,
            current_message=prompt_message,
            unsubscribe_candidates=candidate_urls,
            unsubscribe_candidate_metadata=candidate_metadata,
        )
        durable_result = durable_agent_classification_result(result, entries)
        outcome = {
            "decision_status": (
                "processed" if result.certainty == "certain" else "pending_feedback"
            ),
            "category": result.category,
            "important": result.important,
            "certainty": result.certainty,
            "confidence": result.confidence,
            "reason": durable_result.reason,
            "unsubscribe_candidate_index": result.unsubscribe_candidate_index,
            "unsubscribe_candidate_source": durable_result.unsubscribe_candidate_source,
            "unsubscribe_candidate_digest": durable_result.unsubscribe_candidate_digest,
            "unsubscribe_candidate_reference": durable_result.unsubscribe_candidate_reference,
        }
        locator = EmailProviderLocator.model_validate(payload["provider_locator"])
        classification_id = (
            int.from_bytes(
                sha256(task.stable_message_identity.encode("utf-8")).digest()[:8], "big"
            )
            & ((1 << 63) - 1)
            or 1
        )
        plan = _agent_classification_action_plan(
            classification_id=classification_id,
            account_id=locator.account_id,
            result=result,
            unsubscribe_selection=(
                durable_result
                if durable_result.unsubscribe_candidate_index is not None
                else None
            ),
            folder_targets=payload["folder_targets"],
            config_version=payload["config_version"],
            created_at=datetime.now(timezone.utc),
        )
        message = payload["message"]
        sender_value = message.get("sender") or {}
        sender = (
            str(sender_value.get("email") or sender_value.get("name") or "")
            if isinstance(sender_value, Mapping)
            else str(sender_value)
        )
        classification = EmailClassification(
            classification_id=classification_id,
            stable_message_identity=task.stable_message_identity,
            provider_locator=locator,
            category=result.category,
            confidence=result.confidence,
            margin=0.0,
            probabilities=(
                {result.category: result.confidence}
                if result.category is not None
                else {}
            ),
            model_id="email-classifier-agent:v1",
            config_version=payload["config_version"],
            status=(
                EmailClassificationStatus.PROCESSED
                if result.certainty == "certain"
                else EmailClassificationStatus.PENDING_FEEDBACK
            ),
            classification_source="agent",
            action_plan=plan,
        )
        email_store.persist_scan_result(
            classification,
            agent_result=durable_result,
            sender=sender,
            recipients=tuple(
                str(item.get("email") or item.get("name") or "")
                if isinstance(item, Mapping)
                else str(item)
                for item in (
                    *(message.get("to_recipients") or ()),
                    *(message.get("cc_recipients") or ()),
                )
            ),
            subject=str(message.get("subject") or ""),
            normalized_text=str(message.get("text") or ""),
            attachment_metadata=tuple(
                EmailAttachmentMetadata.model_validate(item)
                for item in message.get("attachments") or ()
            ),
            received_at=str(message.get("date") or ""),
            model_text=str(message.get("text") or "") or "__empty__",
        )
        if plan is not None and plan.agent_actions:
            action_task_producer.produce(plan, current_message)
        outcome = {**outcome, "classification_id": classification_id}
        adapter.complete(task, outcome)
        return outcome
    except Exception as exc:
        from pydantic import ValidationError
        from app.email_store import (
            EmailClassificationConflict,
            EmailClassificationIdentityCollision,
        )

        permanent = isinstance(
            exc,
            (
                json.JSONDecodeError,
                ValidationError,
                EmailClassificationConflict,
                EmailClassificationIdentityCollision,
            ),
        ) or (isinstance(exc, ValueError) and not isinstance(exc, ConnectionError))
        adapter.fail(
            task,
            error=f"{type(exc).__name__}:{exc}",
            retryable=not permanent,
        )
        raise


class EmailWorkerStartupError(RuntimeError):
    """The email worker could not construct its required runtime."""


class ProviderFolderBindingCoordinator:
    """Materialize and verify one exact provider folder per enabled account."""

    def __init__(
        self,
        provider_factory: Callable[[Mapping[str, object]], object],
        *,
        now: Callable[[], str] | None = None,
    ) -> None:
        self._provider_factory = provider_factory
        self._now = now or (
            lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
        )

    def create_and_verify_bindings(
        self,
        *,
        category_key: str,
        provider_folder_name: str,
        enabled_accounts: Sequence[Mapping[str, object]],
    ) -> tuple[VerifiedEmailFolderBinding, ...]:
        return tuple(
            self._materialize_account(
                account,
                category_key=category_key,
                provider_folder_name=provider_folder_name,
            )
            for account in enabled_accounts
        )

    def _materialize_account(
        self,
        account: Mapping[str, object],
        *,
        category_key: str,
        provider_folder_name: str,
    ) -> VerifiedEmailFolderBinding:
        account_id = str(account.get("account_id") or "")
        provider = None
        try:
            provider = self._provider_factory(account)
            folders = tuple(provider.list_folders())
            if category_key == "junk":
                matches = tuple(
                    folder for folder in folders if folder.role is FolderRole.TRASH
                )
                return self._binding_from_matches(
                    account_id,
                    category_key,
                    provider_folder_name,
                    matches,
                )
            matches = self._exact_matches(folders, provider_folder_name)
            if matches:
                return self._binding_from_matches(
                    account_id,
                    category_key,
                    provider_folder_name,
                    matches,
                )
            try:
                provider.create_folder_exact(provider_folder_name)
            except Exception:
                readback = tuple(provider.list_folders())
                readback_matches = self._exact_matches(readback, provider_folder_name)
                if not readback_matches:
                    return self._unresolved_binding(
                        account_id, provider_folder_name, binding_status="error"
                    )
                return self._binding_from_matches(
                    account_id,
                    category_key,
                    provider_folder_name,
                    readback_matches,
                )
            readback = tuple(provider.list_folders())
            return self._binding_from_matches(
                account_id,
                category_key,
                provider_folder_name,
                self._exact_matches(readback, provider_folder_name),
            )
        except Exception:
            return self._unresolved_binding(
                account_id, provider_folder_name, binding_status="error"
            )
        finally:
            close = getattr(provider, "close", None)
            if callable(close):
                close()

    @staticmethod
    def _exact_matches(
        folders: Sequence[ProviderFolder], display_name: str
    ) -> tuple[ProviderFolder, ...]:
        return tuple(
            folder for folder in folders if folder.display_name == display_name
        )

    def _binding_from_matches(
        self,
        account_id: str,
        category_key: str,
        requested_name: str,
        matches: Sequence[ProviderFolder],
    ) -> VerifiedEmailFolderBinding:
        if len(matches) == 1:
            folder = matches[0]
            allowed_roles = (
                {FolderRole.TRASH}
                if category_key == "junk"
                else {FolderRole.UNBOUND, FolderRole.CATEGORY}
            )
            if folder.role not in allowed_roles:
                return self._unresolved_binding(
                    account_id, requested_name, binding_status="error"
                )
            return VerifiedEmailFolderBinding(
                account_id=account_id,
                provider_folder_id=folder.provider_folder_id,
                provider_folder_name=folder.display_name,
                binding_status="active",
                last_verified_at=self._now(),
                provider_folder_role=folder.role,
            )
        return self._unresolved_binding(
            account_id,
            requested_name,
            binding_status=("ambiguous" if len(matches) > 1 else "missing"),
        )

    def _unresolved_binding(
        self,
        account_id: str,
        provider_folder_name: str,
        *,
        binding_status: str,
    ) -> VerifiedEmailFolderBinding:
        return VerifiedEmailFolderBinding(
            account_id=account_id,
            provider_folder_id="",
            provider_folder_name=provider_folder_name,
            binding_status=binding_status,
            last_verified_at=self._now(),
        )


def build_provider_folder_binding_coordinator(
    environment_factory: Callable[[], Mapping[str, str]],
) -> ProviderFolderBindingCoordinator:
    """Build the production IMAP-backed category folder coordinator."""

    from app.email_connector_config import resolve_secret
    from app.email_provider_actions import ImapDeterministicProvider

    def provider_factory(account: Mapping[str, object]) -> object:
        if not bool(account.get("imap_tls")):
            raise ConnectionError("email IMAP TLS is required")
        secret = resolve_secret(
            str(account.get("imap_secret_reference") or ""),
            environment_factory(),
        )
        if not secret:
            raise ConnectionError("email IMAP credential is unavailable")
        return ImapDeterministicProvider.connect(
            str(account["imap_host"]),
            str(account["imap_username"]),
            secret,
            port=int(account["imap_port"]),
            account_id=str(account["account_id"]),
        )

    return ProviderFolderBindingCoordinator(provider_factory)


@dataclass(frozen=True)
class EmailWorkerDependencies:
    load_enabled_accounts: Callable[[], Sequence[Mapping[str, object]]]
    load_active_model: Callable[[], object]
    scan_account: Callable[[Mapping[str, object], object], object]
    run_direct_actions_once: Callable[[], object]
    task_store: object
    orchestrator: object
    load_task_context: Callable[[object], object]
    finalize_task: Callable[[object, object], object]
    training_tick: Callable[[], object]
    record_health: Callable[[str, Mapping[str, object]], object]
    email_store: object | None = None
    run_classification_once: Callable[[], object] | None = None
    publish_provider_observation_change: Callable[[], object] | None = None
    training_observation_tick: Callable[[], object] | None = None
    run_historical_once: Callable[..., object] | None = None
    reconcile_action_tasks_once: Callable[[], object] | None = None
    reconcile_historical_operations_once: Callable[[], object] | None = None


@dataclass(frozen=True)
class EmailWorkerBootstrap:
    load_enabled_accounts: Callable[[], Sequence[Mapping[str, object]]]
    load_active_model: Callable[[], object]
    build_dependencies: Callable[
        [Sequence[Mapping[str, object]], object], EmailWorkerDependencies
    ]
    record_health: Callable[[str, Mapping[str, object]], object] | None = None
    task_store: object | None = None
    email_store: object | None = None


@dataclass(frozen=True)
class LegacyReconciliationResult:
    authoritative: bool
    unresolved_task_ids: tuple[int, ...] = ()
    task_count: int = 0


def _account_id(account: Mapping[str, object]) -> str:
    value = str(account.get("account_id") or "").strip()
    return value if value else "unknown"


def _safe_health_error(exc: Exception) -> dict[str, object]:
    return {
        "status": "failed",
        "error_code": "provider_runtime_error",
        "error_type": type(exc).__name__[:MAX_HEALTH_TEXT_LENGTH],
    }


def _health_error_code(value: object, *, fallback: str) -> str:
    candidate = str(value or "").strip().casefold()
    if not candidate or not candidate.replace("_", "").isalnum():
        return fallback
    return candidate[:MAX_HEALTH_TEXT_LENGTH]


def _is_disabled_email_auto_reply_task(task: object) -> bool:
    """Recognize persisted reply tasks that the current Email policy forbids."""

    try:
        payload = json.loads(str(getattr(task, "trigger_message_json", "")))
    except (TypeError, json.JSONDecodeError):
        return False
    return isinstance(payload, dict) and payload.get("action_type") == "auto_reply"


def _scan_result_error_code(result: object) -> str:
    outcomes = getattr(result, "accounts", ())
    for outcome in outcomes:
        account_error = getattr(outcome, "error_code", "")
        if account_error:
            return _health_error_code(account_error, fallback="scan_failed")
        for folder in getattr(outcome, "folders", ()):
            folder_error = getattr(folder, "error_code", "")
            if folder_error:
                return _health_error_code(folder_error, fallback="scan_failed")
    return ""


def _scan_result_change_summary(result: object) -> tuple[int, bool]:
    if isinstance(result, Sequence) and not isinstance(result, str | bytes):
        summaries = tuple(_scan_result_change_summary(item) for item in result)
        return (
            sum(persisted_count for persisted_count, _changed in summaries),
            any(changed for _persisted_count, changed in summaries),
        )
    persisted_count = int(
        result.get("persisted_count", 0)
        if isinstance(result, Mapping)
        else getattr(result, "persisted_count", 0)
    )
    reported_change = (
        result.get("provider_changed", False)
        if isinstance(result, Mapping)
        else getattr(result, "provider_changed", False)
    )
    if type(reported_change) is not bool:
        raise TypeError("provider_changed must be boolean")
    return persisted_count, persisted_count > 0 or reported_change


def _ignore_health(_scope: str, _payload: Mapping[str, object]) -> None:
    return None


def _drain_direct_actions(
    run_direct_actions_once: Callable[[], object],
    *,
    max_actions: int,
    time_budget_seconds: float,
    monotonic: Callable[[], float],
) -> tuple[object, ...]:
    if max_actions <= 0 or time_budget_seconds <= 0:
        raise ValueError("direct action drain bounds must be positive")
    started_at = monotonic()
    results: list[object] = []
    while len(results) < max_actions and monotonic() - started_at < time_budget_seconds:
        result = run_direct_actions_once()
        if result is None:
            break
        results.append(result)
    return tuple(results)


class EmailWorkerReadiness:
    """Publish process readiness only after every component heartbeats once."""

    def __init__(
        self,
        component_names: Sequence[str],
        *,
        record_health: Callable[[str, Mapping[str, object]], object],
        accounts: int,
    ) -> None:
        self._component_names = frozenset(component_names)
        self._record_health = record_health
        self._accounts = accounts
        self._ready: set[str] = set()
        self._published = False
        self._lock = Lock()

    def mark_ready(self, component_name: str) -> None:
        if component_name not in self._component_names:
            raise ValueError("unknown email worker component")
        with self._lock:
            self._ready.add(component_name)
            if self._published or self._ready != self._component_names:
                return
            self._published = True
        self._record_health(
            "process:email-worker",
            {
                "status": "ready",
                "accounts": self._accounts,
                "components": len(self._component_names),
            },
        )


def run_scan_and_direct_actions_loop(
    accounts: Sequence[Mapping[str, object]],
    active_model: object,
    *,
    scan_account: Callable[[Mapping[str, object], object], object],
    run_direct_actions_once: Callable[[], object],
    run_classification_once: Callable[[], object] | None = None,
    provider_observation_complete: Callable[[], object] | None = None,
    record_health: Callable[[str, Mapping[str, object]], object] = _ignore_health,
    component_ready: Callable[[str], object] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    max_cycles: int | None = None,
    direct_action_max_actions: int = DIRECT_ACTION_DRAIN_MAX_ACTIONS,
    direct_action_time_budget_seconds: float = DIRECT_ACTION_DRAIN_MAX_SECONDS,
    monotonic: Callable[[], float] = time.monotonic,
) -> None:
    cycles = 0
    while max_cycles is None or cycles < max_cycles:
        failures = 0
        provider_changed = False
        for account in accounts:
            account_id = _account_id(account)
            try:
                result = scan_account(account, active_model)
            except Exception as exc:  # noqa: BLE001 - isolate provider accounts
                failures += 1
                record_health(f"account:{account_id}", _safe_health_error(exc))
                continue
            error_code = _scan_result_error_code(result)
            if error_code:
                failures += 1
                record_health(
                    f"account:{account_id}",
                    {"status": "failed", "error_code": error_code},
                )
                continue
            persisted_count, scan_changed = _scan_result_change_summary(result)
            provider_changed = provider_changed or scan_changed
            record_health(
                f"account:{account_id}",
                {
                    "status": "ready",
                    "persisted_count": persisted_count,
                },
            )
        if run_classification_once is not None:
            try:
                _drain_direct_actions(
                    run_classification_once,
                    max_actions=direct_action_max_actions,
                    time_budget_seconds=direct_action_time_budget_seconds,
                    monotonic=monotonic,
                )
            except Exception as exc:  # noqa: BLE001 - keep scan cadence alive
                failures += 1
                record_health(
                    "component:email-classifier-agent", _safe_health_error(exc)
                )
        try:
            direct_results = _drain_direct_actions(
                run_direct_actions_once,
                max_actions=direct_action_max_actions,
                time_budget_seconds=direct_action_time_budget_seconds,
                monotonic=monotonic,
            )
            failed_actions = sum(
                getattr(result, "status", "") == "failed" for result in direct_results
            )
            if failed_actions:
                failures += failed_actions
                record_health(
                    "component:email-provider-actions",
                    {"status": "degraded", "error_code": "provider_action_failed"},
                )
            elif direct_results:
                provider_changed = True
        except Exception as exc:  # noqa: BLE001 - keep the scan cadence alive
            failures += 1
            record_health("component:email-provider-actions", _safe_health_error(exc))
        if (
            failures == 0
            and provider_changed
            and provider_observation_complete is not None
        ):
            try:
                provider_observation_complete()
            except Exception as exc:  # noqa: BLE001 - keep scan cadence alive
                failures += 1
                record_health(
                    "component:email-training-observation", _safe_health_error(exc)
                )
        record_health(
            "component:email-scan-actions",
            {"status": "ready" if failures == 0 else "degraded", "failures": failures},
        )
        if failures == 0 and component_ready is not None:
            component_ready("email-scan-actions")
        cycles += 1
        if max_cycles is None or cycles < max_cycles:
            interval = min(
                (
                    int(account.get("scan_interval_seconds") or SCAN_INTERVAL_SECONDS)
                    for account in accounts
                ),
                default=SCAN_INTERVAL_SECONDS,
            )
            sleep(max(interval, 1))


def run_email_agent_task_loop(
    task_store: object,
    orchestrator: object,
    *,
    load_task_context: Callable[[object], object],
    finalize_task: Callable[[object, object], object],
    record_health: Callable[[str, Mapping[str, object]], object] = _ignore_health,
    component_ready: Callable[[str], object] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    max_cycles: int | None = None,
) -> None:
    cycles = 0
    while max_cycles is None or cycles < max_cycles:
        failures = 0
        last_error_type = ""
        try:
            tasks = task_store.claim_reply_tasks(50, channel="email")
        except Exception as exc:  # noqa: BLE001 - keep the component alive
            tasks = ()
            failures += 1
            last_error_type = type(exc).__name__[:MAX_HEALTH_TEXT_LENGTH]
        for task in tasks:
            if _is_disabled_email_auto_reply_task(task):
                try:
                    task_store.fail_reply_task(
                        task.id,
                        "email_auto_reply_disabled",
                        expected_execution_generation=task.execution_generation,
                    )
                except Exception as exc:  # noqa: BLE001 - keep the component alive
                    failures += 1
                    last_error_type = type(exc).__name__[:MAX_HEALTH_TEXT_LENGTH]
                continue
            try:
                context = load_task_context(task)
                from app.task_lifecycle import validate_audited_email_task

                if not validate_audited_email_task(task, context):
                    raise RuntimeError("legacy_email_unsubscribe_lifecycle")
                result = orchestrator.process(
                    task,
                    context,
                    refresh_context=lambda task=task: load_task_context(task),
                )
                finalize_task(task, result)
            except Exception as exc:  # noqa: BLE001 - isolate one Email task
                failures += 1
                last_error_type = type(exc).__name__[:MAX_HEALTH_TEXT_LENGTH]
                try:
                    task_store.fail_reply_task(
                        task.id,
                        f"email_consumer_runtime_error:{last_error_type}",
                        expected_execution_generation=task.execution_generation,
                    )
                except Exception:  # noqa: BLE001 - heartbeat still reports failure
                    pass
        health: dict[str, object] = {
            "status": "ready" if failures == 0 else "degraded",
            "failures": failures,
        }
        if failures:
            health.update(
                {
                    "error_code": "consumer_runtime_error",
                    "error_type": last_error_type,
                }
            )
        record_health("component:email-agent-consumer", health)
        if failures == 0 and component_ready is not None:
            component_ready("email-agent-consumer")
        cycles += 1
        if max_cycles is None or cycles < max_cycles:
            sleep(CONSUMER_POLL_INTERVAL_SECONDS)


def run_training_scheduler_loop(
    training_tick: Callable[[], object],
    *,
    training_observation_tick: Callable[[], object] | None = None,
    record_health: Callable[[str, Mapping[str, object]], object],
    component_ready: Callable[[str], object] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    max_cycles: int | None = None,
) -> None:
    cycles = 0
    while max_cycles is None or cycles < max_cycles:
        if training_observation_tick is not None:
            try:
                training_observation_tick()
            except Exception as exc:  # noqa: BLE001 - keep the component alive
                record_health(
                    "component:email-training-observation",
                    _safe_health_error(exc),
                )
        try:
            training_tick()
        except Exception as exc:  # noqa: BLE001 - keep the component alive
            record_health(
                "component:email-training",
                {
                    "status": "degraded",
                    "failures": 1,
                    "error_code": "training_runtime_error",
                    "error_type": type(exc).__name__[:MAX_HEALTH_TEXT_LENGTH],
                },
            )
        else:
            record_health(
                "component:email-training",
                {"status": "ready", "failures": 0},
            )
            if component_ready is not None:
                component_ready("email-training")
        cycles += 1
        if max_cycles is None or cycles < max_cycles:
            sleep(TRAINING_INTERVAL_SECONDS)


def run_model_training_maintenance(
    active_model: object,
    *,
    reconcile_action_tasks_once: Callable[[], object],
) -> object:
    """Poll learning once, refresh runtime, then repair durable model tasks."""

    runtime_error: BaseException | None = None
    try:
        return active_model.tick()
    except BaseException as exc:
        runtime_error = exc
        raise
    finally:
        try:
            reconcile_action_tasks_once()
        except BaseException:
            if runtime_error is None:
                raise


def reconcile_missing_model_action_tasks(
    email_store: object,
    action_task_producer: object,
    *,
    load_message: Callable[[Mapping[str, object]], Mapping[str, object] | None],
    record_health: Callable[[str, Mapping[str, object]], object],
) -> dict[str, int]:
    """Repair durable model unsubscribe plans without reclassification."""

    from app.email_classifier_contracts import EmailActionPlan

    candidates = tuple(email_store.list_missing_unsubscribe_action_tasks())
    repaired = 0
    conflicts = 0
    for classification in candidates:
        try:
            message = load_message(classification)
            if not isinstance(message, Mapping):
                raise ValueError("model action repair message is unavailable")
            plan = EmailActionPlan.model_validate_json(
                json.dumps(classification["action_plan"])
            )
            action_task_producer.produce(plan, message)
            remaining = {
                int(item["id"])
                for item in email_store.list_missing_unsubscribe_action_tasks()
            }
            if int(classification["id"]) in remaining:
                raise RuntimeError("model action repair did not persist a task")
            repaired += 1
        except Exception:  # noqa: BLE001 - each durable conflict is isolated
            conflicts += 1
    payload: dict[str, object] = {
        "status": "ready" if conflicts == 0 else "degraded",
        "candidates": len(candidates),
        "repaired": repaired,
        "conflicts": conflicts,
    }
    if conflicts:
        payload["error_code"] = "model_action_reconciliation_conflict"
    record_health("component:email-model-action-reconciliation", payload)
    return {
        "candidates": len(candidates),
        "repaired": repaired,
        "conflicts": conflicts,
    }


def reconcile_historical_operations(
    email_store: object,
    direct_action_executor_factory: Callable[[str], object],
    *,
    read_after: Callable[[Mapping[str, object]], object],
) -> dict[str, int]:
    """Resume exact durable historical plans without source-folder enumeration."""

    from app.email_historical_classifier import HistoricalClassificationOutcome

    repaired = 0
    deferred = 0
    for operation in email_store.list_processing_historical_operations():
        classification = email_store.get_classification_by_stable_identity(
            operation["stable_message_identity"]
        )
        if (
            classification is None
            or classification.get("model_id") != operation["model_id"]
            or int(classification.get("id") or 0) != operation["classification_id"]
            or classification.get("current_action_plan_id")
            != operation["action_plan_id"]
        ):
            deferred += 1
            continue
        statuses = email_store.direct_action_statuses_for_plan(
            operation["action_plan_id"]
        )
        complete = True
        for action_id in operation["action_ids"]:
            if statuses.get(action_id) == "done":
                continue
            result = _run_next_direct_action(
                email_store,
                direct_action_executor_factory,
                available_account_ids=(operation["account_id"],),
                action_id=action_id,
            )
            if result is None or getattr(result, "status", "") != "done":
                complete = False
                break
            statuses[action_id] = "done"
            email_store.touch_historical_operation(
                model_id=operation["model_id"],
                stable_message_identity=operation["stable_message_identity"],
            )
        if not complete:
            deferred += 1
            continue
        classification = email_store.get_classification_by_stable_identity(
            operation["stable_message_identity"]
        )
        if classification is None:
            deferred += 1
            continue
        state = read_after(operation)
        if getattr(state, "is_read", None) is not True:
            deferred += 1
            continue
        action_plan = classification.get("action_plan")
        if not isinstance(action_plan, Mapping) or not isinstance(
            action_plan.get("actions"), list
        ):
            deferred += 1
            continue
        if not _historical_relocation_folder_verified(
            action_plan=action_plan,
            classification=classification,
            state=state,
        ):
            deferred += 1
            continue
        receipts = email_store.direct_action_receipts_for_plan(
            operation["action_plan_id"]
        )
        outcome_name = _historical_action_outcome_from_receipts(
            tuple(str(action) for action in action_plan["actions"]),
            receipts,
            is_read=True,
        )
        if outcome_name in {"actions_queued", "read_state_not_preserved"}:
            deferred += 1
            continue
        outcome = HistoricalClassificationOutcome(
            stable_message_identity=operation["stable_message_identity"],
            model_id=operation["model_id"],
            predicted_category=operation["predicted_category"],
            threshold=float(operation["threshold"]),
            probability=float(operation["probability"]),
            important=bool(operation["important"]),
            action_outcome=outcome_name,
        )
        email_store.record_historical_classification_outcome(outcome)
        email_store.complete_historical_operation(outcome)
        repaired += 1
    return {"repaired": repaired, "deferred": deferred}


def email_worker_components(
    dependencies: EmailWorkerDependencies | Any,
    *,
    accounts: Sequence[Mapping[str, object]],
    active_model: object,
    component_ready: Callable[[str], object] | None = None,
) -> tuple[tuple[str, partial], ...]:
    return (
        (
            "email-scan-actions",
            partial(
                run_scan_and_direct_actions_loop,
                accounts,
                active_model,
                scan_account=dependencies.scan_account,
                run_direct_actions_once=dependencies.run_direct_actions_once,
                run_classification_once=getattr(
                    dependencies, "run_classification_once", None
                ),
                record_health=dependencies.record_health,
                component_ready=component_ready,
                provider_observation_complete=getattr(
                    dependencies, "publish_provider_observation_change", None
                ),
            ),
        ),
        (
            "email-agent-consumer",
            partial(
                run_email_agent_task_loop,
                dependencies.task_store,
                dependencies.orchestrator,
                load_task_context=dependencies.load_task_context,
                finalize_task=dependencies.finalize_task,
                record_health=dependencies.record_health,
                component_ready=component_ready,
            ),
        ),
        (
            "email-training",
            partial(
                run_training_scheduler_loop,
                dependencies.training_tick,
                training_observation_tick=getattr(
                    dependencies, "training_observation_tick", None
                ),
                record_health=dependencies.record_health,
                component_ready=component_ready,
            ),
        ),
    )


def _scan_config(email_store: object, model_record: object | None):
    from app.email_classifier_contracts import (
        EmailAction,
        INITIAL_EMAIL_CATEGORY_KEYS,
        validate_email_category_key,
    )
    from app.email_classifier_scan import EmailScanConfig
    from app.email_classifier_training import (
        CategoryEligibility,
        assess_email_action_eligibility,
    )

    rows = email_store.list_configs()
    if len(rows) != len(INITIAL_EMAIL_CATEGORY_KEYS):
        return EmailScanConfig.cold_start(config_version="email-config-missing-v1")
    versions = {str(row["config_version"]) for row in rows}
    if len(versions) != 1:
        raise EmailWorkerStartupError("email category config versions are inconsistent")
    config_version = next(iter(versions))
    by_category = {validate_email_category_key(row["category"]): row for row in rows}
    if set(by_category) != set(INITIAL_EMAIL_CATEGORY_KEYS):
        return EmailScanConfig.cold_start(config_version="email-config-missing-v1")
    thresholds = {
        category: float(by_category[category]["threshold"])
        for category in INITIAL_EMAIL_CATEGORY_KEYS
    }
    metadata = getattr(model_record, "metadata", None)
    raw_source_model_id = getattr(metadata, "model_id", None)
    source_model_id = (
        raw_source_model_id.strip()
        if isinstance(raw_source_model_id, str) and raw_source_model_id.strip()
        else None
    )
    model_status = str(getattr(model_record, "status", ""))
    validation_method = str(getattr(metadata, "validation_method", ""))
    metrics_value = getattr(metadata, "per_category_metrics", {})
    metrics = metrics_value if isinstance(metrics_value, Mapping) else {}
    actions = {
        category: tuple(
            EmailAction(value) for value in by_category[category]["actions"]
        )
        for category in INITIAL_EMAIL_CATEGORY_KEYS
    }
    eligibility: dict[str, CategoryEligibility] = {}
    for category in INITIAL_EMAIL_CATEGORY_KEYS:
        metric_value = metrics.get(category)
        metric = metric_value if isinstance(metric_value, Mapping) else None
        if metric is None:
            action_eligibility = assess_email_action_eligibility(
                category=category,
                actions=actions[category],
                model_status=model_status,
                validation_method=validation_method,
                configured_threshold=thresholds[category],
                evaluated_threshold=None,
                validated_precision=None,
                validation_positive_support=0,
                metadata_auto_action_eligible=False,
                source_model_id=source_model_id,
                config_version=config_version,
            )
            eligibility[category] = CategoryEligibility(
                category=category,
                configured_threshold=thresholds[category],
                validated_precision=None,
                validation_sample_count=0,
                auto_action_eligible=False,
                reason=(
                    "model_not_active"
                    if model_record is not None and model_status != "active"
                    else "model_eligibility_missing"
                ),
                source_model_id=source_model_id,
                action_eligibility=action_eligibility,
            )
            continue
        trained_threshold = float(metric["configured_threshold"])
        evaluated_threshold = float(metric["evaluated_threshold"])
        threshold_matches = (
            trained_threshold == thresholds[category]
            and evaluated_threshold == thresholds[category]
        )
        action_eligibility = assess_email_action_eligibility(
            category=category,
            actions=actions[category],
            model_status=model_status,
            validation_method=validation_method,
            configured_threshold=thresholds[category],
            evaluated_threshold=(
                evaluated_threshold
                if trained_threshold == thresholds[category]
                else trained_threshold
            ),
            validated_precision=float(metric["precision"]),
            validation_positive_support=int(
                metric.get(
                    "validation_positive_support",
                    metric["validation_sample_count"],
                )
            ),
            metadata_auto_action_eligible=bool(metric["auto_action_eligible"]),
            source_model_id=source_model_id,
            config_version=config_version,
        )
        configured_action_eligible = (
            any(item.auto_action_eligible for item in action_eligibility.values())
            if actions[category]
            else (
                model_status == "active"
                and source_model_id is not None
                and validation_method == "time-ordered-holdout"
                and threshold_matches
                and bool(metric["auto_action_eligible"])
            )
        )
        eligibility[category] = CategoryEligibility(
            category=category,
            configured_threshold=thresholds[category],
            validated_precision=float(metric["precision"]),
            validation_sample_count=int(metric["validation_sample_count"]),
            auto_action_eligible=configured_action_eligible,
            reason=(
                str(metric["eligibility_reason"])
                if configured_action_eligible
                else next(
                    (
                        item.reason
                        for item in action_eligibility.values()
                        if not item.auto_action_eligible
                    ),
                    "model_eligibility_missing",
                )
            ),
            source_model_id=source_model_id,
            evaluated_threshold=evaluated_threshold,
            action_eligibility=action_eligibility,
        )
    return EmailScanConfig(
        config_version=config_version,
        thresholds=thresholds,
        actions=actions,
        category_eligibility=eligibility,
        action_parameters={
            category: {
                EmailAction(action): parameters
                for action, parameters in by_category[category][
                    "action_parameters"
                ].items()
            }
            for category in INITIAL_EMAIL_CATEGORY_KEYS
        },
        category_enabled={
            category: bool(by_category[category]["enabled"])
            for category in INITIAL_EMAIL_CATEGORY_KEYS
        },
    )


def _run_next_direct_action(
    email_store: object,
    executor_factory: Callable[[str], object] | None,
    *,
    available_account_ids: Callable[[], Sequence[str]] | Sequence[str] | None = None,
    action_id: str | None = None,
) -> object | None:
    """Run one claimed action, or leave it pending when no provider exists."""

    if executor_factory is None:
        return None
    if callable(available_account_ids):
        available_account_ids = available_account_ids()
    if available_account_ids is not None and not tuple(available_account_ids):
        return None
    claimed_at = datetime.now(timezone.utc).isoformat()
    stale_before = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    recover = getattr(email_store, "recover_stale_processing_actions", None)
    if callable(recover):
        recover(stale_before=stale_before, recovered_at=claimed_at)
    if action_id is not None:
        action = email_store.claim_direct_action(
            action_id=action_id, claimed_at=claimed_at
        )
    else:
        claim_kwargs = {"claimed_at": claimed_at}
        if available_account_ids is not None:
            claim_kwargs["account_ids"] = available_account_ids
        action = email_store.claim_next_direct_action(**claim_kwargs)
    if action is None:
        return None

    from app.email_provider_actions import ProviderActionResult

    try:
        executor = executor_factory(action.account_id)
        if executor is None:
            raise LookupError("provider executor unavailable")
        result = executor.execute(action)
    except Exception as exc:  # noqa: BLE001 - every durable claim is terminalized
        result = ProviderActionResult(
            status="failed",
            provider_operation="provider_factory",
            provider_target=action.locator.stable_message_identity,
            provider_result_id="",
            error=f"provider_factory_failed:{type(exc).__name__}",
            retryable=True,
        )
    email_store.complete_direct_action_attempt(
        action,
        status=result.status,
        provider_operation=result.provider_operation,
        provider_target=result.provider_target,
        provider_result_id=result.provider_result_id,
        error=result.error,
        finished_at=datetime.now(timezone.utc).isoformat(),
        retryable=bool(getattr(result, "retryable", True)),
        updated_locator=getattr(result, "updated_locator", None),
    )
    return result


def _build_agent_orchestrator(
    settings: object,
    store: object,
    *,
    runtime_skill_snapshot=None,
):
    from app.agent_orchestrator import AgentOrchestrator
    from app.agent_runtime_production import build_production_agent_runtime
    from app.audit_agent import AuditAgentRunner
    from app.consumer_agent import ConsumerAgentRunner
    from app.email_store import EmailStore
    from app.email_unsubscribe_continuation import (
        EmailUnsubscribeContinuationDriver,
    )

    workspace = Path(settings.workspace)
    runtime = build_production_agent_runtime(store=store, workspace=workspace)
    shared = {
        "store": store,
        "workspace": workspace,
        "runtime_config": runtime.config,
        "runtime_router": runtime.router,
        "codex_adapter": runtime.codex_adapter,
        "claude_adapter": runtime.claude_adapter,
        "friday_adapter": runtime.friday_adapter,
        "refresh_runtime_capabilities": runtime.refresh_runtime_capabilities,
    }
    return AgentOrchestrator(
        store=store,
        consumer=ConsumerAgentRunner(
            **shared,
            runtime_skill_snapshot=runtime_skill_snapshot,
        ),
        audit=AuditAgentRunner(**shared, dry_run=bool(settings.dry_run)),
        domain_continuation=EmailUnsubscribeContinuationDriver(
            EmailStore(Path(settings.db_path))
        ),
    )


def _message_identity(message: Mapping[str, object]) -> str:
    return str(message.get("stableMessageIdentity") or message.get("id") or "").strip()


def _message_sender(message: Mapping[str, object]) -> str:
    sender = message.get("from")
    if isinstance(sender, Mapping):
        value = sender.get("email") or sender.get("name")
    else:
        value = sender
    return str(value or "unknown").strip() or "unknown"


def _message_text(message: Mapping[str, object]) -> str:
    return str(message.get("markdownBody") or message.get("textBody") or "")


def _close_email_source(source: object) -> None:
    close = getattr(source, "logout", None)
    if callable(close):
        close()


def _build_connected_mailbox_otp_resolver(
    account: Mapping[str, object],
    source_factory: Callable[[Mapping[str, object]], object],
):
    """Build an ephemeral OTP reader confined to one configured account."""

    from app.email_unsubscribe import (
        EmailOtpChallenge,
        select_connected_mailbox_otp,
    )

    recipient = str(account.get("email_address") or "").strip()

    def resolve(challenge: EmailOtpChallenge):
        if (
            not recipient
            or not isinstance(challenge, EmailOtpChallenge)
            or challenge.recipient.casefold() != recipient.casefold()
        ):
            return None
        source = source_factory(account)
        try:
            fetch = getattr(source, "fetch_email_otp_candidates", None)
            if not callable(fetch):
                return None
            candidates = fetch(challenge, limit=8)
            if not isinstance(candidates, Sequence) or isinstance(
                candidates, (str, bytes, bytearray)
            ):
                return None
            return select_connected_mailbox_otp(challenge, candidates)
        finally:
            _close_email_source(source)

    return resolve


def _missing_historical_state(folder_name: str):
    from app.email_historical_classifier import HistoricalClassificationState

    return HistoricalClassificationState(
        folder_role=FolderRole.UNBOUND,
        configured_unclassified_source=False,
        is_read=False,
        is_unclassified=False,
        provider_folder_name=folder_name or None,
    )


def _read_historical_provider_state(
    email_store: object,
    source_factory: Callable[[Mapping[str, object]], object],
    account: Mapping[str, object],
    *,
    folder_name: str,
    uidvalidity: int,
    uid: int,
    stable_message_identity: str,
):
    from app.email_classifier_scan import _provider_locator, _stable_message_identity
    from app.email_historical_classifier import HistoricalClassificationState

    source = source_factory(account)
    try:
        matches = tuple(
            folder
            for folder in source.list_folders()
            if folder.display_name == folder_name
        )
        if len(matches) != 1:
            return _missing_historical_state(folder_name)
        folder = matches[0]
        bindings = tuple(
            item
            for item in email_store.list_account_folder_bindings()
            if item["account_id"] == str(account["account_id"])
            and item["binding_status"] == "active"
        )
        is_bound = any(
            item["provider_folder_id"] == folder.provider_folder_id
            and item["category_key"] != "junk"
            for item in bindings
        )
        role = FolderRole.CATEGORY if is_bound else folder.role
        configured_source = (
            role is FolderRole.UNBOUND
            and folder_name in tuple(account.get("scan_folders") or ())
            and not is_bound
        )
        batch = source.fetch_uid_batch(
            folder_name,
            cursor_uidvalidity=uidvalidity,
            last_seen_uid=max(0, uid - 1),
            limit=2,
            unread_only=False,
        )
        if int(batch.uidvalidity) != uidvalidity:
            return _missing_historical_state(folder_name)
        message = next(
            (
                item
                for item in batch.messages
                if _stable_message_identity(item, _provider_locator(item))
                == stable_message_identity
            ),
            None,
        )
        if message is None:
            return _missing_historical_state(folder_name)
        is_unclassified = role is FolderRole.INBOX or configured_source
        return HistoricalClassificationState(
            folder_role=role,
            configured_unclassified_source=configured_source,
            is_read=message.get("providerUnread") is False,
            is_unclassified=is_unclassified,
            provider_folder_name=folder_name,
        )
    finally:
        _close_email_source(source)


def _reread_historical_candidate_state(
    email_store: object,
    source_factory: Callable[[Mapping[str, object]], object],
    account: Mapping[str, object],
    candidate: object,
):
    from app.email_classifier_scan import _provider_locator

    message = getattr(candidate, "provider_message", None)
    if not isinstance(message, Mapping):
        raise ValueError("historical candidate provider message is missing")
    locator = _provider_locator(message)
    return _read_historical_provider_state(
        email_store,
        source_factory,
        account,
        folder_name=locator.folder,
        uidvalidity=locator.uidvalidity,
        uid=locator.uid,
        stable_message_identity=str(candidate.stable_message_identity),
    )


def _reread_historical_candidate_message(
    source_factory: Callable[[Mapping[str, object]], object],
    account: Mapping[str, object],
    candidate: object,
) -> Mapping[str, object]:
    """Fetch the exact current provider record without trusting queued content."""

    from app.email_classifier_scan import _provider_locator, _stable_message_identity

    projection = getattr(candidate, "provider_message", None)
    if not isinstance(projection, Mapping):
        raise ValueError("historical candidate provider locator is missing")
    locator = _provider_locator(projection)
    source = source_factory(account)
    try:
        batch = source.fetch_uid_batch(
            locator.folder,
            cursor_uidvalidity=locator.uidvalidity,
            last_seen_uid=max(0, locator.uid - 1),
            limit=2,
            unread_only=False,
        )
        if int(batch.uidvalidity) != locator.uidvalidity:
            raise ValueError("historical candidate UIDVALIDITY changed")
        message = next(
            (
                item
                for item in batch.messages
                if _stable_message_identity(item, _provider_locator(item))
                == str(candidate.stable_message_identity)
            ),
            None,
        )
        if not isinstance(message, Mapping):
            raise ValueError("historical candidate is no longer at queued locator")
        return message
    finally:
        _close_email_source(source)


def _read_persisted_historical_state(
    email_store: object,
    source_factory: Callable[[Mapping[str, object]], object],
    account: Mapping[str, object],
    stable_message_identity: str,
):
    persisted = email_store.get_classification_by_stable_identity(
        stable_message_identity
    )
    if not isinstance(persisted, Mapping):
        return _missing_historical_state("")
    return _read_historical_provider_state(
        email_store,
        source_factory,
        account,
        folder_name=str(persisted["folder"]),
        uidvalidity=int(persisted["uidvalidity"]),
        uid=int(persisted["uid"]),
        stable_message_identity=stable_message_identity,
    )


def _load_email_task_input(
    email_store: object,
    source_factory: Callable[[Mapping[str, object]], object],
    task: object,
):
    from app.email_context_source import EmailContextSource

    try:
        return EmailContextSource(
            email_store,
            source_factory=source_factory,
        ).load_task_input(task)
    except ValueError as exc:
        raise EmailWorkerStartupError(str(exc)) from exc


def _load_email_task_context(
    email_store: object,
    task_store: object,
    source_factory: Callable[[Mapping[str, object]], object],
    task: object,
):
    from app.email_classifier_contracts import EmailActionPlan
    from app.email_task_adapter import EmailAgentTaskAdapter

    payload = json.loads(task.trigger_message_json)
    classification = email_store.get_classification(int(payload["classification_id"]))
    if classification is None or classification["action_plan"] is None:
        raise EmailWorkerStartupError("email task action plan is unavailable")
    task_input = _load_email_task_input(email_store, source_factory, task)
    routes = EmailAgentTaskAdapter(task_store, email_store).ensure_action_plan_tasks(
        EmailActionPlan.model_validate_json(json.dumps(classification["action_plan"])),
        task_input,
    )
    route = next(
        (candidate for candidate in routes if candidate.task.id == task.id), None
    )
    if route is None:
        raise EmailWorkerStartupError("email task route is unavailable")
    return route.context


def _finalize_email_task(store: object, task: object, result: object) -> None:
    status_map = {
        "executed": ("done", "completed"),
        "no_action": ("done", "skipped"),
        "needs_human": ("done", "needs_human"),
        "dry_run": ("done", "dry_run"),
        "failed_retryable": ("pending", "failed"),
        "unknown": ("pending", "failed"),
        "failed_terminal": ("failed", "failed"),
    }
    try:
        task_status, send_status = status_map[result.status]
    except KeyError as exc:
        raise ValueError("invalid email orchestration status") from exc
    run = store.get_agent_run(result.final_run_id)
    if run is None:
        raise RuntimeError("email orchestration final run was not persisted")
    error = str(result.error.code or "")
    store.finalize_orchestrated_reply_task(
        task_id=task.id,
        expected_execution_generation=task.execution_generation,
        run_id=run.id,
        task_status=task_status,
        task_error=error,
        available_at="",
        conversation_id=task.conversation_id,
        conversation_title=task.conversation_title,
        trigger_message_id=task.trigger_message_id,
        trigger_sender=task.trigger_sender,
        trigger_text=task.trigger_text,
        codex_reason=result.summary,
        codex_session_id=run.codex_session_id,
        codex_transcript_start_line=run.transcript_start_line,
        codex_transcript_end_line=run.transcript_end_line,
        audit_tool_events_json=json.dumps(run.tool_events, ensure_ascii=False),
        audit_summary=result.summary,
        send_status=send_status,
        send_error=error,
        channel="email",
    )


def build_email_worker_dependencies(
    settings: object,
    *,
    direct_action_executor_factory: Callable[[str], object] | None = None,
    training_snapshot_job_factory: Callable[..., object] | None = None,
) -> EmailWorkerBootstrap:
    from app.email_classifier_agent import (
        EmailClassifierAgent,
        EmailClassifierRoutedBackend,
    )
    from app.email_classifier_learning import EmailClassifierLearningService
    from app.email_description_optimizer import (
        DescriptionOptimizationOrchestrator,
        RoutedDescriptionOptimizerAgent,
    )
    from app.email_classifier_runtime import PromotedEmailClassifierRuntime
    from app.email_classifier_scan import (
        AgentScanContext,
        scan_agent_classification_batch,
    )
    from app.email_model_registry import EmailModelRegistry
    from app.email_store import EmailStore
    from app.email_training_snapshot import EmailTrainingSnapshotPublicationJob
    from app.email_task_producer import (
        EmailActionTaskProducer,
        EmailClassificationTaskProducer,
    )
    from app.agent_runtime_production import build_production_routed_codex_execution
    from app.store import AutoReplyStore

    if training_snapshot_job_factory is None:
        training_snapshot_job_factory = EmailTrainingSnapshotPublicationJob

    email_store = EmailStore(Path(settings.db_path))
    task_store = AutoReplyStore(Path(settings.db_path))
    from app.managed_skills import resolve_pending_runtime_skills

    runtime_skill_snapshot = resolve_pending_runtime_skills(task_store, pid=os.getpid())
    classification_task_producer = EmailClassificationTaskProducer(email_store)
    action_task_producer = EmailActionTaskProducer(task_store, email_store)
    classification_task_producer.adapter.recover_running_tasks()
    source_factory = _build_email_source_factory(settings)
    model_root = Path(settings.db_path).parent / "email-models"
    registry = EmailModelRegistry(model_root)
    description_optimizer = DescriptionOptimizationOrchestrator(
        store=email_store,
        registry=registry,
        agent=lambda payload: RoutedDescriptionOptimizerAgent(
            build_production_routed_codex_execution(
                store=task_store,
                workspace=Path(settings.workspace),
                total_timeout_seconds=900.0,
                idle_timeout_seconds=120.0,
            )
        )(payload),
    )
    learning = EmailClassifierLearningService(
        email_store,
        registry=registry,
        retrain_state_path=model_root / "retrain-state.json",
        description_optimizer=description_optimizer,
    )
    if direct_action_executor_factory is None:
        direct_action_executor_factory = _build_imap_direct_action_executor_factory(
            email_store
        )

    def load_enabled_accounts():
        return tuple(
            account for account in email_store.list_accounts() if account["enabled"]
        )

    def load_active_model():
        return PromotedEmailClassifierRuntime(
            registry,
            learning_service=learning,
            observability_store=email_store,
        )

    def record_health(scope: str, payload: Mapping[str, object]):
        task_store.set_service_state(
            f"email_worker_health:{scope}",
            json.dumps(dict(payload), sort_keys=True, separators=(",", ":")),
        )

    def load_scan_config(active_model: object):
        model_record = registry.get_model(active_model.loaded.model_id)
        config = _scan_config(email_store, model_record)
        metrics = model_record.metadata.per_category_metrics
        missing_config = config.config_version == "email-config-missing-v1"
        stale_threshold = any(
            item.reason == "threshold_changed_since_training"
            for item in config.category_eligibility.values()
        )
        missing_eligibility = not bool(metrics)
        if missing_config:
            payload = {
                "status": "degraded",
                "error_code": "category_config_missing",
            }
        elif missing_eligibility:
            payload = {
                "status": "degraded",
                "error_code": "model_eligibility_missing",
            }
        elif stale_threshold:
            payload = {
                "status": "degraded",
                "error_code": "model_eligibility_stale",
            }
        else:
            payload = {"status": "ready"}
        record_health("component:email-scan-config", payload)
        return config

    def build_dependencies(
        _accounts: Sequence[Mapping[str, object]],
        active_model: object,
    ) -> EmailWorkerDependencies:
        routed_classifier = EmailClassifierAgent(
            EmailClassifierRoutedBackend(
                build_production_routed_codex_execution(
                    store=task_store,
                    workspace=Path(settings.workspace),
                    total_timeout_seconds=900.0,
                    idle_timeout_seconds=120.0,
                )
            ),
            runtime_skill_snapshot=runtime_skill_snapshot,
            skill_name="ceo-email-classifier",
        )

        def read_current_classification_message(
            task: object, payload: Mapping[str, object]
        ) -> Mapping[str, object] | None:
            locator = payload.get("provider_locator")
            if not isinstance(locator, Mapping):
                raise ValueError("classification task provider locator is invalid")
            account_id = str(locator.get("account_id") or "")
            account = email_store.get_account(account_id)
            if not isinstance(account, Mapping):
                raise ValueError("classification task account is unavailable")
            folder = str(locator.get("folder") or "")
            configured = tuple(account.get("scan_folders") or ())
            bindings = tuple(
                item
                for item in email_store.list_account_folder_bindings()
                if item["account_id"] == account_id
                and item["binding_status"] == "active"
            )
            if folder.casefold() != "inbox" and (
                folder not in configured
                or any(item["provider_folder_name"] == folder for item in bindings)
            ):
                return None
            source = source_factory(account)
            try:
                uid = int(locator.get("uid") or 0)
                uidvalidity = int(locator.get("uidvalidity") or 0)
                batch = source.fetch_uid_batch(
                    folder,
                    cursor_uidvalidity=uidvalidity,
                    last_seen_uid=max(0, uid - 1),
                    limit=2,
                )
                if int(batch.uidvalidity) != uidvalidity:
                    return None
                return next(
                    (
                        message
                        for message in batch.messages
                        if _message_identity(message)
                        == str(getattr(task, "stable_message_identity"))
                    ),
                    None,
                )
            finally:
                _close_email_source(source)

        def scan_account(account: Mapping[str, object], current_model: object):
            configs = tuple(
                item for item in email_store.list_category_configs() if item["enabled"]
            )
            bindings = tuple(
                item
                for item in email_store.list_account_folder_bindings()
                if item["account_id"] == str(account["account_id"])
                and item["binding_status"] == "active"
            )
            context = AgentScanContext(
                allowed_category_keys=tuple(item["category_key"] for item in configs),
                category_descriptions={
                    item["category_key"]: {
                        "core": item["core_description"],
                        "include": item["include"],
                        "exclude": item["exclude"],
                    }
                    for item in configs
                },
                folder_targets={
                    item["category_key"]: item["provider_folder_name"]
                    for item in bindings
                    if item["category_key"] != "junk"
                },
                config_version="|".join(
                    sorted({str(item["config_version"]) for item in configs})
                ),
            )
            source = source_factory(account)
            try:
                inventory = tuple(source.list_folders())
                results = []
                for folder_name in account["scan_folders"]:
                    matches = tuple(
                        folder
                        for folder in inventory
                        if folder.display_name == folder_name
                    )
                    if len(matches) != 1:
                        continue
                    folder = matches[0]
                    is_bound = any(
                        binding["provider_folder_id"] == folder.provider_folder_id
                        and binding["category_key"] != "junk"
                        for binding in bindings
                    )
                    role = FolderRole.CATEGORY if is_bound else folder.role
                    results.append(
                        scan_agent_classification_batch(
                            source,
                            email_store,
                            classification_task_producer,
                            context,
                            mailbox=folder_name,
                            folder_role=role,
                            configured_unclassified_source=(
                                role is FolderRole.UNBOUND and not is_bound
                            ),
                            online_runtime=current_model,
                            accept_model=lambda message, prediction, entries, model_text, model_id: (
                                persist_model_primary_classification(
                                    email_store,
                                    action_task_producer,
                                    message=message,
                                    prediction=prediction,
                                    context=context,
                                    model_id=model_id,
                                    model_text=model_text,
                                    unsubscribe_entries=entries,
                                )
                            ),
                        )
                    )
                return tuple(results)
            finally:
                _close_email_source(source)

        def run_historical_once(
            model_id: str, *, account_id: str | None = None
        ) -> tuple[object, ...]:
            """Run one explicit cached historical batch; never scheduled by worker."""

            from app.email_embedding_cache import EmbeddingCache
            from app.email_embedding_classifier import DescriptionAwareEmailClassifier
            from app.email_historical_classifier import (
                MAX_HISTORICAL_BATCH_SIZE,
                HistoricalClassificationCandidate,
                enumerate_historical_page,
            )
            from app.email_embedding_cache import EmbeddingCacheKey

            evidence = registry.get_staged_evidence(model_id)
            artifact = registry.embedding_artifacts / f"{model_id}.artifact"
            model = DescriptionAwareEmailClassifier.load(artifact)
            historical = evidence.get("historical_eligibility")
            if not isinstance(historical, Mapping) or not isinstance(
                historical.get("categories"), Mapping
            ):
                raise ValueError("historical eligibility evidence is incomplete")
            category_evidence = historical["categories"]
            eligibility = {
                category: bool(category_evidence[category].get("eligible"))
                for category in model.enabled_categories
                if isinstance(category_evidence.get(category), Mapping)
            }
            if set(eligibility) != set(model.enabled_categories):
                raise ValueError("historical eligibility does not cover model")
            cache = EmbeddingCache(registry.root, dimension=model.dimension)
            selected_accounts = tuple(
                account
                for account in _accounts
                if account_id is None or str(account["account_id"]) == account_id
            )
            if account_id is not None and not selected_accounts:
                raise ValueError("historical account is not enabled")
            reconcile_historical_operations_once()
            outcomes: list[object] = []
            for account in selected_accounts:
                if len(outcomes) >= MAX_HISTORICAL_BATCH_SIZE:
                    break
                bindings = tuple(
                    item
                    for item in email_store.list_account_folder_bindings()
                    if item["account_id"] == str(account["account_id"])
                    and item["binding_status"] == "active"
                )
                configs = tuple(
                    item
                    for item in email_store.list_category_configs()
                    if item["enabled"]
                )
                context = AgentScanContext(
                    allowed_category_keys=tuple(
                        item["category_key"] for item in configs
                    ),
                    category_descriptions={
                        item["category_key"]: {
                            "core": item["core_description"],
                            "include": item["include"],
                            "exclude": item["exclude"],
                        }
                        for item in configs
                    },
                    folder_targets={
                        item["category_key"]: item["provider_folder_name"]
                        for item in bindings
                        if item["category_key"] != "junk"
                    },
                    config_version="|".join(
                        sorted({str(item["config_version"]) for item in configs})
                    ),
                )
                source = source_factory(account)
                try:
                    inventory = tuple(source.list_folders())
                    for folder_name in account["scan_folders"]:
                        if len(outcomes) >= MAX_HISTORICAL_BATCH_SIZE:
                            break
                        matches = tuple(
                            folder
                            for folder in inventory
                            if folder.display_name == folder_name
                        )
                        if len(matches) != 1:
                            continue
                        folder = matches[0]
                        is_bound = any(
                            binding["provider_folder_id"]
                            == folder.provider_folder_id
                            and binding["category_key"] != "junk"
                            for binding in bindings
                        )
                        role = FolderRole.CATEGORY if is_bound else folder.role
                        configured_source = role is FolderRole.UNBOUND and not is_bound
                        cursor = email_store.historical_traversal_cursor(
                            account_id=str(account["account_id"]),
                            folder=folder_name,
                            model_id=model_id,
                        )
                        page = enumerate_historical_page(
                            source,
                            mailbox=folder_name,
                            folder_role=role,
                            configured_unclassified_source=configured_source,
                            cursor_uidvalidity=cursor["uidvalidity"],
                            last_seen_uid=cursor["last_seen_uid"],
                        )
                        email_store.enqueue_historical_page(
                            account_id=str(account["account_id"]),
                            folder=folder_name,
                            model_id=model_id,
                            uidvalidity=page.uidvalidity,
                            last_seen_uid=page.last_seen_uid,
                            candidates=page.candidates,
                        )
                        queued = email_store.list_historical_candidates(
                            account_id=str(account["account_id"]),
                            folder=folder_name,
                            model_id=model_id,
                        )

                        def read_state(candidate):
                            return _reread_historical_candidate_state(
                                email_store,
                                source_factory,
                                account,
                                candidate,
                            )

                        def execute(candidate, prediction):
                            message = _reread_historical_candidate_message(
                                source_factory, account, candidate
                            )
                            from app.email_unsubscribe import extract_unsubscribe_entries
                            from app.email_imap_readonly import ephemeral_body_html

                            entries = extract_unsubscribe_entries(
                                list_unsubscribe=str(
                                    message.get("listUnsubscribe") or ""
                                ),
                                list_unsubscribe_post=str(
                                    message.get("listUnsubscribePost") or ""
                                ),
                                body_text=str(
                                    message.get("markdownBody")
                                    or message.get("textBody")
                                    or ""
                                ),
                                body_html=ephemeral_body_html(message),
                            )
                            return execute_historical_model_actions(
                                email_store,
                                direct_action_executor_factory,
                                action_task_producer,
                                message=message,
                                prediction=prediction,
                                context=context,
                                model_id=model_id,
                                model_text=candidate.normalized_text,
                                unsubscribe_entries=entries,
                                read_after=lambda: _read_persisted_historical_state(
                                    email_store,
                                    source_factory,
                                    account,
                                    candidate.stable_message_identity,
                                ),
                                threshold=float(
                                    model.category_thresholds[prediction.category]
                                ),
                            )

                        runnable = []
                        for queued_item in queued:
                            if len(runnable) >= (
                                MAX_HISTORICAL_BATCH_SIZE - len(outcomes)
                            ):
                                break
                            candidate = HistoricalClassificationCandidate(
                                stable_message_identity=queued_item[
                                    "stable_message_identity"
                                ],
                                normalized_text=queued_item["normalized_text"],
                                provider_message=queued_item["provider_message"],
                            )
                            current = read_state(candidate)
                            if not current.eligible:
                                reason = (
                                    "unread"
                                    if not current.is_read
                                    else "no_longer_unclassified"
                                )
                                email_store.set_historical_candidate_state(
                                    account_id=str(account["account_id"]),
                                    folder=folder_name,
                                    model_id=model_id,
                                    stable_message_identity=candidate.stable_message_identity,
                                    state=(
                                        "deferred" if reason == "unread" else "terminal"
                                    ),
                                    reason=reason,
                                )
                                continue
                            current_message = _reread_historical_candidate_message(
                                source_factory, account, candidate
                            )
                            from app.email_classifier_model import email_message_to_text

                            current_normalized_text = email_message_to_text(
                                current_message
                            )
                            if sha256(
                                current_normalized_text.encode("utf-8")
                            ).hexdigest() != queued_item["normalized_input_hash"]:
                                email_store.set_historical_candidate_state(
                                    account_id=str(account["account_id"]),
                                    folder=folder_name,
                                    model_id=model_id,
                                    stable_message_identity=(
                                        candidate.stable_message_identity
                                    ),
                                    state="deferred",
                                    reason="model_input_changed",
                                )
                                continue
                            candidate = HistoricalClassificationCandidate(
                                stable_message_identity=(
                                    candidate.stable_message_identity
                                ),
                                normalized_text=current_normalized_text,
                                provider_message=candidate.provider_message,
                            )
                            key = EmbeddingCacheKey.for_text(
                                normalized_text=candidate.normalized_text,
                                input_schema_version=str(model.input_schema_version),
                                embedding_model_id=str(model.embedding_model_id),
                                embedding_revision=str(model.embedding_revision),
                            )
                            if cache.get(key) is None:
                                email_store.set_historical_candidate_state(
                                    account_id=str(account["account_id"]),
                                    folder=folder_name,
                                    model_id=model_id,
                                    stable_message_identity=candidate.stable_message_identity,
                                    state="deferred",
                                    reason="exact_cache_miss",
                                )
                                continue
                            runnable.append(candidate)
                        batch_outcomes = run_manual_historical_batch(
                                email_store=email_store,
                                model_id=model_id,
                                model=model,
                                cache=cache,
                                historically_eligible=eligibility,
                                candidates=runnable,
                                read_state=read_state,
                                execute=execute,
                            )
                        outcomes.extend(batch_outcomes)
                        for outcome in batch_outcomes:
                            retryable = outcome.action_outcome in {
                                "state_changed_before_execute",
                                "provider_action_failed",
                                "provider_folder_not_verified",
                                "read_state_not_preserved",
                            }
                            email_store.set_historical_candidate_state(
                                account_id=str(account["account_id"]),
                                folder=folder_name,
                                model_id=model_id,
                                stable_message_identity=outcome.stable_message_identity,
                                state="deferred" if retryable else "terminal",
                                reason=outcome.action_outcome,
                            )
                finally:
                    _close_email_source(source)
            return tuple(outcomes)

        from app.email_training_observer import (
            ProviderTrainingChangeDetector,
            ProviderTrainingObservationJob,
            TrainingObservationCoordinator,
        )

        observation_job = ProviderTrainingObservationJob(
            state_path=registry.root / "provider-training-observations.json",
            source_factory=source_factory,
            email_store=email_store,
        )
        snapshot_job = training_snapshot_job_factory(
            store=email_store,
            learning_service=learning,
            observation_loader=observation_job.cached_observations,
            description_version_loader=lambda: _active_description_set_version(
                email_store
            ),
        )
        observation_coordinator = TrainingObservationCoordinator(
            request_state_path=registry.root / "provider-training-requests.json",
            job=observation_job,
            accounts_loader=lambda: _accounts,
            publish=snapshot_job.publish_observations,
            description_version_loader=lambda: _active_description_set_version(
                email_store
            ),
        )
        observation_coordinator.request_initialization()
        change_detector = ProviderTrainingChangeDetector(
            job=observation_job,
            accounts_loader=lambda: _accounts,
            request=observation_coordinator.request,
        )

        def training_observation_tick():
            change_detector.tick()
            return observation_coordinator.tick()

        def load_model_action_repair_message(
            classification: Mapping[str, object],
        ) -> Mapping[str, object] | None:
            account = email_store.get_account(str(classification["account_id"]))
            if not isinstance(account, Mapping):
                return None
            source = source_factory(account)
            try:
                uid = int(classification["uid"])
                uidvalidity = int(classification["uidvalidity"])
                batch = source.fetch_uid_batch(
                    str(classification["folder"]),
                    cursor_uidvalidity=uidvalidity,
                    last_seen_uid=max(0, uid - 1),
                    limit=2,
                )
                if int(batch.uidvalidity) != uidvalidity:
                    return None
                return next(
                    (
                        message
                        for message in batch.messages
                        if _message_identity(message)
                        == str(classification["stable_message_identity"])
                    ),
                    None,
                )
            finally:
                _close_email_source(source)

        def reconcile_action_tasks_once():
            return reconcile_missing_model_action_tasks(
                email_store,
                action_task_producer,
                load_message=load_model_action_repair_message,
                record_health=record_health,
            )

        def reconcile_historical_operations_once():
            by_id = {str(item["account_id"]): item for item in _accounts}

            def read_after(operation):
                account = by_id.get(str(operation["account_id"]))
                if account is None:
                    return _missing_historical_state("")
                return _read_persisted_historical_state(
                    email_store,
                    source_factory,
                    account,
                    str(operation["stable_message_identity"]),
                )

            return reconcile_historical_operations(
                email_store,
                direct_action_executor_factory,
                read_after=read_after,
            )

        def training_tick():
            return run_model_training_maintenance(
                active_model,
                reconcile_action_tasks_once=reconcile_action_tasks_once,
            )

        def publish_provider_observation_change():
            observation_coordinator.request()

        return EmailWorkerDependencies(
            load_enabled_accounts=load_enabled_accounts,
            load_active_model=load_active_model,
            scan_account=scan_account,
            run_direct_actions_once=partial(
                _run_next_direct_action,
                email_store,
                direct_action_executor_factory,
                available_account_ids=lambda: tuple(
                    str(account["account_id"])
                    for account in load_enabled_accounts()
                    if str(account.get("account_id") or "").strip()
                ),
            ),
            task_store=task_store,
            orchestrator=_build_agent_orchestrator(
                settings,
                task_store,
                runtime_skill_snapshot=runtime_skill_snapshot,
            ),
            load_task_context=partial(
                _load_email_task_context,
                email_store,
                task_store,
                source_factory,
            ),
            finalize_task=partial(_finalize_email_task, task_store),
            training_tick=training_tick,
            record_health=record_health,
            email_store=email_store,
            run_classification_once=partial(
                run_email_classification_task_once,
                classification_task_producer.adapter,
                routed_classifier,
                email_store,
                owner=f"email-classifier:{os.getpid()}",
                provider_readback=read_current_classification_message,
                action_task_producer=action_task_producer,
            ),
            publish_provider_observation_change=(publish_provider_observation_change),
            training_observation_tick=training_observation_tick,
            run_historical_once=run_historical_once,
            reconcile_action_tasks_once=reconcile_action_tasks_once,
            reconcile_historical_operations_once=(
                reconcile_historical_operations_once
            ),
        )

    return EmailWorkerBootstrap(
        load_enabled_accounts=load_enabled_accounts,
        load_active_model=load_active_model,
        build_dependencies=build_dependencies,
        record_health=record_health,
        task_store=task_store,
        email_store=email_store,
    )


def _active_description_set_version(email_store: object) -> str:
    from app.email_description_optimizer import description_set_digest
    from app.email_embedding_classifier import CategoryDescription

    descriptions = {
        row["category_key"]: CategoryDescription(
            core=row["core_description"],
            include=tuple(row["include"]),
            exclude=tuple(row["exclude"]),
            version=row["description_version"],
        )
        for row in email_store.list_category_configs()
        if row["enabled"]
    }
    return (
        "description-set-sha256:" + description_set_digest(descriptions)
        if descriptions
        else "description-set-unavailable"
    )


def _build_imap_direct_action_executor_factory(email_store: object):
    from app.email_connector_config import resolve_secret
    from app.email_provider_actions import (
        DeterministicEmailActionExecutor,
        ImapDeterministicProvider,
    )

    def executor_factory(account_id: str):
        account = email_store.get_account(account_id)
        if not isinstance(account, Mapping) or not bool(account.get("enabled")):
            raise LookupError("email IMAP account is unavailable")
        if str(account.get("account_id") or "") != account_id:
            raise LookupError("email IMAP account identity mismatch")
        if not bool(account.get("imap_tls")):
            raise ConnectionError("email IMAP TLS is required")
        secret = resolve_secret(
            str(account.get("imap_secret_reference") or ""), os.environ
        )
        if not secret:
            raise ConnectionError("email IMAP credential is unavailable")

        def connect_provider():
            return ImapDeterministicProvider.connect(
                str(account["imap_host"]),
                str(account["imap_username"]),
                secret,
                port=int(account["imap_port"]),
                account_id=account_id,
            )

        return DeterministicEmailActionExecutor(
            connect_provider(),
            readback_provider_factory=connect_provider,
        )

    return executor_factory


def _build_email_source_factory(settings: object):
    from app.email_connector_config import resolve_secret
    from app.email_imap_readonly import ImapReadonlyAdapter

    def source_factory(account: Mapping[str, object]):
        if not bool(account["imap_tls"]):
            raise ConnectionError("email IMAP TLS is required")
        secret = resolve_secret(str(account["imap_secret_reference"]), os.environ)
        if not secret:
            raise ConnectionError("email IMAP credential is unavailable")
        return ImapReadonlyAdapter.connect(
            str(account["imap_host"]),
            str(account["imap_username"]),
            secret,
            port=int(account["imap_port"]),
            account_id=str(account["account_id"]),
            mailbox_address=str(account.get("email_address") or ""),
        )

    return source_factory


def build_audited_email_unsubscribe_operation(settings: object) -> object:
    """Build the only executable Email unsubscribe operation."""

    from app.email_browser_profile import (
        EmailBrowserProfile,
        email_browser_session_manager,
    )
    from app.email_classifier_contracts import EmailProviderLocator
    from app.email_store import EmailStore
    from app.email_unsubscribe import (
        EmailUnsubscribeEffect,
        UnsubscribeAuthenticationEvidence,
        browser_network_policy_for_entries,
        browser_unsubscribe_entries,
        execute_unsubscribe_in_dedicated_profile,
        extract_unsubscribe_entries,
    )
    from app.email_unsubscribe_audit import EmailUnsubscribeAuditOperation
    from app.email_imap_readonly import ephemeral_body_html
    from app.store import AutoReplyStore

    email_store = EmailStore(Path(settings.db_path))
    task_store = AutoReplyStore(Path(settings.db_path))
    source_factory = _build_email_source_factory(settings)
    browser_profile = EmailBrowserProfile(
        Path(settings.db_path).parent / "email-browser-runtime"
    )
    browser_session_manager = email_browser_session_manager(browser_profile)

    def resolve_entries(
        locator: EmailProviderLocator,
        expected_reference: str,
        authentication: UnsubscribeAuthenticationEvidence | None = None,
        network_policy_reference: str = "",
        network_policy_origin_references: tuple[str, ...] = (),
    ):
        account = email_store.get_account(locator.account_id)
        if not isinstance(account, Mapping):
            raise ValueError("email unsubscribe account is unavailable")
        source = source_factory(account)
        try:
            batch = source.fetch_uid_batch(
                locator.folder,
                cursor_uidvalidity=locator.uidvalidity,
                last_seen_uid=max(0, locator.uid - 1),
                limit=2,
            )
            if batch.uidvalidity != locator.uidvalidity:
                raise ValueError("email unsubscribe provider generation changed")
            message = next(
                (
                    item
                    for item in batch.messages
                    if _message_identity(item) == locator.stable_message_identity
                    and int(item.get("uid") or 0) == locator.uid
                ),
                None,
            )
            if not isinstance(message, Mapping):
                raise ValueError("email unsubscribe source message is unavailable")
            entries = extract_unsubscribe_entries(
                list_unsubscribe=str(message.get("listUnsubscribe") or ""),
                list_unsubscribe_post=str(message.get("listUnsubscribePost") or ""),
                body_text=str(
                    message.get("textBody") or message.get("markdownBody") or ""
                ),
                body_html=ephemeral_body_html(message),
                authentication_evidence=authentication,
            )
            entries = tuple(
                entry
                for entry in browser_unsubscribe_entries(
                    entries,
                    normalize_indexes=True,
                )
                if entry.reference == expected_reference
            )
            if len(entries) != 1:
                raise ValueError("email unsubscribe entry changed")
            policy = browser_network_policy_for_entries(entries)
            if (
                policy.reference != network_policy_reference
                or policy.origin_references != network_policy_origin_references
            ):
                raise ValueError("email unsubscribe network policy changed")
            return entries
        finally:
            _close_email_source(source)

    def execute_effect(
        effect: EmailUnsubscribeEffect,
        entries: tuple[object, ...],
        *,
        owner: Mapping[str, object],
        executed_prefix_length: int,
    ):
        if not entries:
            raise ValueError("audited unsubscribe executes exactly one effect")
        if not any(
            getattr(entry, "reference", None) == effect.entry_reference
            for entry in entries
        ):
            raise ValueError("email unsubscribe entry changed")
        account = email_store.get_account(effect.account_id)
        if not isinstance(account, Mapping):
            raise ValueError("email unsubscribe account is unavailable")
        connected_recipient = str(account.get("email_address") or "").strip()
        if not connected_recipient:
            raise ValueError("email unsubscribe recipient is unavailable")
        policy = browser_network_policy_for_entries(entries)
        if (
            policy.reference != effect.network_policy_reference
            or policy.origin_references != effect.network_policy_origin_references
        ):
            raise ValueError("email unsubscribe network policy changed")
        return execute_unsubscribe_in_dedicated_profile(
            effect,
            entries,
            store=email_store,
            profile=browser_profile,
            network_policy=policy,
            owner=owner,
            executed_prefix_length=executed_prefix_length,
            connected_recipient=connected_recipient,
            email_otp_resolver=_build_connected_mailbox_otp_resolver(
                account,
                source_factory,
            ),
            session_manager=browser_session_manager,
        )

    operation = EmailUnsubscribeAuditOperation(
        task_store=task_store,
        email_store=email_store,
        resolve_entries=resolve_entries,
        execute_effect=execute_effect,
    )
    operation.browser_session_manager = browser_session_manager
    operation.open_user_handoff = browser_session_manager.handoff_action
    return operation


def run_audited_email_unsubscribe(
    db_path: str | Path,
    task_id: int,
    execution_generation: str,
    *,
    audit_agent_run_id: int,
    accepted_action: Mapping[str, object],
) -> dict[str, object]:
    """Execute one accepted unsubscribe action through its bound Audit run."""

    from types import SimpleNamespace

    operation = build_audited_email_unsubscribe_operation(
        SimpleNamespace(
            db_path=Path(db_path),
            workspace=Path(db_path).parent,
        )
    )
    return operation.execute(
        task_id,
        execution_generation,
        audit_agent_run_id=audit_agent_run_id,
        accepted_action=accepted_action,
    )


def _validate_email_worker_dependencies(dependencies: object) -> None:
    callable_fields = (
        "scan_account",
        "run_direct_actions_once",
        "load_task_context",
        "finalize_task",
        "training_tick",
        "record_health",
    )
    if any(not callable(getattr(dependencies, name, None)) for name in callable_fields):
        raise EmailWorkerStartupError("email worker loop dependency is unavailable")
    if not callable(getattr(dependencies.task_store, "claim_reply_tasks", None)):
        raise EmailWorkerStartupError("email task store dependency is unavailable")
    if not callable(getattr(dependencies.orchestrator, "process", None)):
        raise EmailWorkerStartupError("email Agent orchestrator is unavailable")


def _wait_for_email_components(threads: Sequence[object]) -> None:
    while True:
        dead = [
            thread
            for thread in threads
            if callable(getattr(thread, "is_alive", None)) and not thread.is_alive()
        ]
        if dead:
            raise RuntimeError("email worker component exited unexpectedly")
        Event().wait(1.0)


def _report_waiting_configuration(
    bootstrap: object,
    *,
    reason: str,
    output: TextIO,
) -> None:
    record_health = getattr(bootstrap, "record_health", None)
    if callable(record_health):
        record_health(
            "process:email-worker",
            {"status": "waiting_configuration", "reason": reason},
        )
    print(
        f"email-worker waiting_configuration reason={reason}",
        file=output,
        flush=True,
    )


def _wait_for_configuration(wait: Callable[[], object] | None) -> None:
    if wait is not None:
        wait()
        return
    Event().wait()


def _legacy_inventory_unavailable(
    dependencies: object,
    *,
    unresolved_task_ids: Sequence[int] = (),
) -> LegacyReconciliationResult:
    record_health = getattr(dependencies, "record_health", None)
    if callable(record_health):
        record_health(
            "component:email-legacy-lifecycle",
            {
                "status": "degraded",
                "error_code": "legacy_email_unsubscribe_inventory_unavailable",
            },
        )
    return LegacyReconciliationResult(
        authoritative=False,
        unresolved_task_ids=tuple(unresolved_task_ids),
    )


def _fail_nonterminal_legacy_unsubscribe_tasks(
    dependencies: object,
) -> LegacyReconciliationResult:
    """Fence inventoried legacy attempts and preserve inventory authority."""

    email_store = getattr(dependencies, "email_store", None)
    inventory = getattr(
        email_store,
        "list_nonterminal_legacy_unsubscribe_task_attempts",
        None,
    )
    if not callable(inventory):
        return _legacy_inventory_unavailable(dependencies)
    try:
        attempts = tuple(inventory())
    except Exception:  # noqa: BLE001 - preserve prior conservative fencing
        return _legacy_inventory_unavailable(dependencies)
    if not attempts:
        return LegacyReconciliationResult(authoritative=True)
    isolated: list[int] = []
    superseded: list[int] = []
    unresolved: list[int] = []
    terminalize = getattr(
        dependencies.task_store,
        "terminalize_legacy_email_unsubscribe_task",
        None,
    )
    read_current = getattr(
        email_store,
        "get_nonterminal_legacy_unsubscribe_task_attempt",
        None,
    )
    for index, attempt in enumerate(attempts):
        task_id = attempt.task_id
        try:
            fenced = bool(
                callable(terminalize)
                and terminalize(
                    task_id,
                    expected_execution_generation=attempt.execution_generation,
                    expected_status=attempt.status,
                )
            )
        except Exception:  # noqa: BLE001 - isolate each unsafe legacy attempt
            fenced = False
        if fenced:
            isolated.append(task_id)
            continue
        if not callable(read_current):
            remaining_ids = tuple(item.task_id for item in attempts[index + 1 :])
            return _legacy_inventory_unavailable(
                dependencies,
                unresolved_task_ids=(*unresolved, task_id, *remaining_ids),
            )
        try:
            current = read_current(task_id)
        except Exception:  # noqa: BLE001 - an unreadable replacement is unsafe
            remaining_ids = tuple(item.task_id for item in attempts[index + 1 :])
            return _legacy_inventory_unavailable(
                dependencies,
                unresolved_task_ids=(*unresolved, task_id, *remaining_ids),
            )
        (unresolved if current is not None else superseded).append(task_id)
    dependencies.record_health(
        "component:email-legacy-lifecycle",
        {
            "status": "degraded",
            "error_code": "legacy_email_unsubscribe_lifecycle",
            "task_count": len(attempts),
            "isolated_count": len(isolated),
            "superseded_count": len(superseded),
            "unresolved_count": len(unresolved),
        },
    )
    return LegacyReconciliationResult(
        authoritative=True,
        unresolved_task_ids=tuple(unresolved),
        task_count=len(attempts),
    )


def run_email_worker(
    settings: object,
    *,
    dependencies: EmailWorkerDependencies | Any | None = None,
    dependency_builder: Callable[[object], object] = (build_email_worker_dependencies),
    thread_factory: Callable[..., Thread] = Thread,
    wait: Callable[[], object] | None = None,
    output: TextIO = sys.stdout,
) -> None:
    active_model: object | None = None
    try:
        bootstrap = dependencies or dependency_builder(settings)
        final_reconciliation = _fail_nonterminal_legacy_unsubscribe_tasks(bootstrap)
        accounts = tuple(bootstrap.load_enabled_accounts())
        if not accounts:
            _report_waiting_configuration(
                bootstrap,
                reason="empty_accounts",
                output=output,
            )
            _wait_for_configuration(wait)
            return
        try:
            active_model = bootstrap.load_active_model()
        except Exception:
            _report_waiting_configuration(
                bootstrap,
                reason="missing_model",
                output=output,
            )
            _wait_for_configuration(wait)
            return
        if active_model is None:
            _report_waiting_configuration(
                bootstrap,
                reason="missing_model",
                output=output,
            )
            _wait_for_configuration(wait)
            return
        build_dependencies = getattr(bootstrap, "build_dependencies", None)
        dependencies = (
            build_dependencies(accounts, active_model)
            if callable(build_dependencies)
            else bootstrap
        )
        _validate_email_worker_dependencies(dependencies)
        reconcile_action_tasks_once = getattr(
            dependencies, "reconcile_action_tasks_once", None
        )
        if callable(reconcile_action_tasks_once):
            reconcile_action_tasks_once()
        reconcile_historical_once = getattr(
            dependencies, "reconcile_historical_operations_once", None
        )
        if callable(reconcile_historical_once):
            reconcile_historical_once()
        if dependencies is not bootstrap:
            final_reconciliation = _fail_nonterminal_legacy_unsubscribe_tasks(
                dependencies
            )
        unresolved_legacy_task_ids = set(final_reconciliation.unresolved_task_ids)
        agent_consumer_allowed = (
            final_reconciliation.authoritative and not unresolved_legacy_task_ids
        )
        if final_reconciliation.authoritative and final_reconciliation.task_count == 0:
            dependencies.record_health(
                "component:email-legacy-lifecycle",
                {
                    "status": "ready",
                    "task_count": 0,
                    "unresolved_count": 0,
                },
            )
        all_component_names = (
            "email-scan-actions",
            "email-agent-consumer",
            "email-training",
        )
        component_names = tuple(
            name
            for name in all_component_names
            if not (not agent_consumer_allowed and name == "email-agent-consumer")
        )
        readiness = EmailWorkerReadiness(
            component_names,
            record_health=dependencies.record_health,
            accounts=len(accounts),
        )
        components = email_worker_components(
            dependencies,
            accounts=accounts,
            active_model=active_model,
            component_ready=readiness.mark_ready,
        )
        if not agent_consumer_allowed:
            components = tuple(
                component
                for component in components
                if component[0] != "email-agent-consumer"
            )
            if final_reconciliation.authoritative:
                dependencies.record_health(
                    "component:email-agent-consumer",
                    {
                        "status": "degraded",
                        "error_code": "legacy_email_unsubscribe_lifecycle",
                        "unresolved_count": len(unresolved_legacy_task_ids),
                    },
                )
            else:
                dependencies.record_health(
                    "component:email-agent-consumer",
                    {
                        "status": "degraded",
                        "error_code": (
                            "legacy_email_unsubscribe_inventory_unavailable"
                        ),
                    },
                )
    except EmailWorkerStartupError as exc:
        _close_active_model(active_model, active_error=exc)
        raise
    except Exception as exc:
        startup_error = EmailWorkerStartupError(
            f"email worker dependency construction failed: {type(exc).__name__}"
        )
        _close_active_model(active_model, active_error=startup_error)
        raise startup_error from exc

    dependencies.record_health(
        "process:email-worker",
        {
            "status": "starting",
            "accounts": len(accounts),
            "components": len(components),
        },
    )
    print(
        f"email-worker starting accounts={len(accounts)} components={len(components)}",
        file=output,
        flush=True,
    )
    lifecycle_error: BaseException | None = None
    try:
        threads = []
        for name, target in components:
            thread = thread_factory(
                target=target,
                name=f"ceo-agent-{name}",
                daemon=True,
            )
            threads.append(thread)
            thread.start()
        if wait is not None:
            wait()
        else:
            _wait_for_email_components(threads)
    except BaseException as exc:
        lifecycle_error = exc
        raise
    finally:
        _close_active_model(active_model, active_error=lifecycle_error)


def _close_active_model(
    active_model: object | None, *, active_error: BaseException | None
) -> None:
    close_active_model = getattr(active_model, "close", None)
    if not callable(close_active_model):
        return
    try:
        close_active_model()
    except BaseException:
        if active_error is None:
            raise
