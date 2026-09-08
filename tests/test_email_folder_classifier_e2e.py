from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
import time
from typing import NoReturn

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agent_context import AgentTaskContext
from app.agent_contracts import (
    AuditAgentResult,
    ConsumerAgentResult,
    ProposedAction,
)
from app.agent_orchestrator import AgentOrchestrator
from app.agent_turn_runner import AgentTurnRunResult
from app.email_classifier_agent import AgentClassificationResult
from app.email_classifier_contracts import (
    EmailAction,
    EmailClassification,
    EmailClassificationStatus,
)
from app.email_classifier_runtime import (
    OnlineModelInput,
    PromotedEmailClassifierRuntime,
    EmailClassifierRuntimeMode,
    OnlineClassificationResult,
    OnlineModelAcceptOutcome,
    activate_online_model,
)
from app.email_classifier_training import train_frozen_embedding_candidate
from app.email_classifier_scan import route_online_classification
from app.email_embedding_client import (
    EmailEmbeddingClient,
    EmbeddingResult,
    EmbeddingTiming,
)
from app.email_embedding_cache import EmbeddingCache, EmbeddingCacheKey
from app.email_embedding_classifier import (
    CategoryDescription,
    DescriptionAwareEmailClassifier,
    DescriptionVectors,
)
from app.email_important import important_effective, normalize_important_signals
from app.email_imap_readonly import ImapReadonlyAdapter, parse_rfc822_message
from app.email_model_registry import (
    CandidateCompatibility,
    CandidateMaturityEvidence,
    HistoricalEligibility,
    HistoricalSystematicErrorState,
    EmailModelRegistry,
    assess_whole_model_readiness,
    build_embedding_model_id,
)
from app.email_provider_actions import (
    DeterministicEmailActionExecutor,
    ProviderMessageState,
    parse_imap_fetch_flags,
)
from app.email_provider_folders import FolderRole, ProviderFolder
from app.email_folder_truth import resolve_email_folder_truth
from app.email_store import EmailStore, StoredEmailAction, StoredEmailLocator
from app.email_task_producer import EmailActionTaskProducer
from app.email_training_snapshot import build_folder_training_snapshot
from app.email_training_observer import ProviderTrainingObservationJob
from app.email_category_config import VerifiedEmailFolderBinding
from app.email_unsubscribe import BrowserNetworkPolicy, extract_unsubscribe_entries
from app.email_worker import (
    _finalize_email_task,
    _run_next_direct_action,
    execute_historical_model_actions,
    persist_model_primary_classification,
    run_email_agent_task_loop,
)
from app.store import AgentRole, AutoReplyStore
from app.web_api.email import register_email_routes


def _restore_designated_provider_message(
    *,
    locator_hint: StoredEmailLocator,
    original_folder: str,
    original_flags: frozenset[str],
    locate,
    raw_move,
    read_flags,
    replace_flags,
) -> StoredEmailLocator:
    """Compensate a live-test mutation by stable identity, then prove exact state."""

    current_state = locate(locator_hint)
    if current_state.locator is None:
        raise AssertionError(
            "restoration locator unavailable; "
            f"stable_identity={locator_hint.stable_message_identity}; "
            f"current_locator={locator_hint!r}"
        )
    current = current_state.locator
    if current_state.folder != original_folder:
        try:
            raw_move(current, original_folder)
        except Exception:
            # MOVE may have succeeded before its locator/readback failed.  Never
            # retry the write from a stale UID: re-locate by stable Message-ID.
            pass
        current_state = locate(locator_hint)
        if current_state.locator is None or current_state.folder != original_folder:
            rendered = (
                current_state.locator
                if current_state.locator is not None
                else locator_hint
            )
            raise AssertionError(
                "folder restoration could not be proven; "
                f"stable_identity={locator_hint.stable_message_identity}; "
                f"current_locator={rendered!r}"
            )
        current = current_state.locator
    replace_flags(current, original_flags)
    final_state = locate(locator_hint)
    if final_state.locator is None:
        raise AssertionError(
            "final restoration locator unavailable; "
            f"stable_identity={locator_hint.stable_message_identity}; "
            f"current_locator={current!r}"
        )
    final_flags = read_flags(final_state.locator)
    if final_state.folder != original_folder or final_flags != original_flags:
        raise AssertionError(
            "provider readback did not prove exact restoration; "
            f"stable_identity={locator_hint.stable_message_identity}; "
            f"current_locator={final_state.locator!r}"
        )
    return final_state.locator


class _LifecycleProvider:
    def __init__(self) -> None:
        self.folder = "INBOX"
        self.uid = 7
        self.is_read = False
        self.important = False
        self.send_calls = 0
        self.reply_calls = 0
        self.audit_unsubscribe_calls = 0
        self.folder_changes = 0

    def list_folders(self):
        return (
            ProviderFolder("inbox", "INBOX", FolderRole.INBOX),
            ProviderFolder("legal", "Legal", FolderRole.CATEGORY),
            ProviderFolder("financing", "Financing", FolderRole.CATEGORY),
            ProviderFolder("trash", "Trash", FolderRole.TRASH),
        )

    def read_state(self, locator, *, action_type):
        del action_type
        return ProviderMessageState(
            revision=f"revision-{self.uid}-{self.folder}-{int(self.important)}",
            labels=frozenset(),
            is_read=self.is_read,
            archived=False,
            folder=self.folder,
            trashed=self.folder == "Trash",
            important_signal_names=(
                frozenset({"\\Flagged"}) if self.important else frozenset()
            ),
            required_important_signal_names=frozenset({"\\Flagged"}),
            locator=replace(locator, folder=self.folder, uid=self.uid),
        )

    def resolve_destination(self, locator, action_type, parameters):
        del locator
        if action_type is EmailAction.TRASH:
            return "Trash"
        return str(parameters["target_folder"])

    def apply(self, locator, action_type, parameters, *, observed_state):
        del observed_state
        if action_type in {EmailAction.MOVE, EmailAction.TRASH}:
            destination = (
                "Trash"
                if action_type is EmailAction.TRASH
                else str(parameters["target_folder"])
            )
            if destination != self.folder:
                self.folder_changes += 1
                self.folder = destination
                self.uid += 1
        elif action_type is EmailAction.FLAG_IMPORTANT:
            self.important = True
        elif action_type is EmailAction.MARK_READ:
            self.is_read = True
        return replace(locator, folder=self.folder, uid=self.uid)

    def close(self):
        return None

    def user_move(self, folder: str) -> None:
        self.folder = folder
        self.uid += 1
        self.folder_changes += 1

    def audited_unsubscribe(self, entry) -> None:
        assert entry.reference.startswith("unsubscribe-entry:")
        self.audit_unsubscribe_calls += 1

    def send(self, *_args, **_kwargs):
        self.send_calls += 1
        raise AssertionError("email send is disabled")

    def reply(self, *_args, **_kwargs):
        self.reply_calls += 1
        raise AssertionError("email reply is disabled")


def _action(
    action_type: EmailAction,
    locator: StoredEmailLocator,
    parameters: dict[str, object],
) -> StoredEmailAction:
    return StoredEmailAction(
        action_id=f"action-{action_type.value}-{locator.uid}",
        action_plan_id="plan-e2e",
        classification_id=1,
        account_id="account-1",
        action_type=action_type,
        parameters=parameters,
        config_version="config-e2e",
        locator=locator,
        attempt_number=1,
        claim_started_at="2026-09-08T08:00:00+00:00",
    )


class _E2EConsumerRunner:
    def __init__(self, store, accepted_action):
        self.store = store
        self.accepted_action = accepted_action
        self.owner = "email-e2e-consumer"

    def run(
        self,
        task,
        _context,
        *,
        proposal_revision,
        parent_agent_run_id,
        feedback=None,
    ):
        assert feedback is None
        claim = self.store.claim_agent_run(
            task.id,
            task.execution_generation,
            role=AgentRole.CONSUMER,
            proposal_revision=proposal_revision,
            turn_attempt=self.store.next_agent_run_turn_attempt(
                task.id,
                task.execution_generation,
                role=AgentRole.CONSUMER,
                proposal_revision=proposal_revision,
            ),
            parent_agent_run_id=parent_agent_run_id,
            operation_id="",
            owner=self.owner,
        )
        result = ConsumerAgentResult.model_validate(
            {
                "outcome": "proposal",
                "summary": "Propose the authorized unsubscribe operation.",
                "proposal": {
                    "objective": "Unsubscribe the classified junk source.",
                    "actions": [self.accepted_action],
                    "sourced_facts": [],
                    "authored_judgment": (
                        "The immutable email ActionPlan authorizes unsubscribe."
                    ),
                },
                "decision_options": [],
                "error": {"code": "", "retryable": False},
                "risk": "low",
                "confidence": 1.0,
            }
        )
        completed = self.store.complete_agent_run(
            claim.run.id, result.model_dump(mode="json"), owner=self.owner
        )
        return AgentTurnRunResult(completed.id, result, 0, 1)


class _E2EAuditRunner:
    def __init__(self, store, execute):
        self.store = store
        self.execute = execute
        self.owner = "email-e2e-audit"

    def run(
        self,
        task,
        context,
        *,
        turn_attempt,
        parent_agent_run_id,
        frozen_delivery_retry=False,
    ):
        assert frozen_delivery_retry is False
        claim = self.store.claim_agent_run(
            task.id,
            task.execution_generation,
            role=AgentRole.AUDIT,
            proposal_revision=context.proposal_revision,
            turn_attempt=turn_attempt,
            parent_agent_run_id=parent_agent_run_id,
            operation_id=context.operation_id,
            owner=self.owner,
        )
        action = context.proposal.actions[0].model_dump(mode="json")
        receipt = self.execute(claim.run.id, action)
        result = AuditAgentResult.model_validate(
            {
                "outcome": "executed",
                "summary": "Audited unsubscribe completed.",
                "proposal_revision": context.proposal_revision,
                "feedback": None,
                "external_result": {
                    "operation_id": context.operation_id,
                    "verification_summary": "Terminal unsubscribe receipt persisted.",
                    "live_result_reference": {
                        "receipt_id": receipt["receipt_id"],
                        "status": receipt["status"],
                    },
                },
                "decision_options": [],
                "error": {"code": "", "retryable": False},
            }
        )
        completed = self.store.complete_agent_run(
            claim.run.id,
            result.model_dump(mode="json"),
            owner=self.owner,
            side_effect_state="confirmed",
        )
        return AgentTurnRunResult(completed.id, result, 0, 1)

    def recover(self, *_args, **_kwargs):
        raise AssertionError("unexpected audit recovery")

    def execute_recovery(self, *_args, **_kwargs):
        raise AssertionError("unexpected audit execution recovery")


def _maturity(model_id: str) -> CandidateMaturityEvidence:
    snapshot_number = 2 if model_id.endswith("v2") else 1
    eligible = HistoricalEligibility(
        0.97, 24 + snapshot_number, 11 + snapshot_number
    )
    return CandidateMaturityEvidence(
        model_id=model_id,
        source_snapshot_id=f"snapshot-{snapshot_number}",
        source_snapshot_digest=str(snapshot_number) * 64,
        source_snapshot_observed_at=(
            datetime(2026, 9, 8, tzinfo=timezone.utc)
            + timedelta(minutes=snapshot_number)
        ).isoformat(),
        folder_label_watermark=100 + snapshot_number,
        important_label_watermark=40 + snapshot_number,
        compatibility=CandidateCompatibility(
            enabled_categories=("legal", "financing", "junk"),
            description_version="descriptions-e2e",
            input_schema_version="email-model-input.v3",
            embedding_model_id="jina-small",
            embedding_revision="gpu4-r17",
            head_format="description-mlp-v1",
            parent_model_id=None,
        ),
        category_eligibility={
            "legal": eligible,
            "financing": eligible,
            "junk": eligible,
        },
        important_eligibility=eligible,
        unresolved_historical_systematic_error=False,
    )


def _train_real_candidate_pair(tmp_path):
    store = EmailStore(tmp_path / "email.sqlite3")
    registry = EmailModelRegistry(tmp_path / "registry")
    observed_at = datetime(2026, 9, 8, tzinfo=timezone.utc)
    messages = []
    proposed_splits = {}
    for category, folder, axis in (
        ("legal", "Legal", (1.0, 0.02)),
        ("financing", "Financing", (0.02, 1.0)),
        ("junk", "Trash", (0.02, 0.02)),
    ):
        for index in range(60):
            identity = f"{category}-{index}"
            split = ("train", "validation", "test")[index % 3]
            proposed_splits[identity] = split
            messages.append(
                {
                    "account_id": "account-1",
                    "stable_message_identity": identity,
                    "provider_folder_id": category,
                    "provider_folder_name": folder,
                    "folder_role": "category",
                    "bound_category_key": category,
                    "folder_binding_status": "active",
                    "processed_by_email_service": True,
                    "important_signals": normalize_important_signals(
                        provider="imap",
                        raw_signal_names=("\\Flagged",) if index % 2 else (),
                    ),
                    "sender": {
                        "name": f"{category} sender {index}",
                        "email": f"{category}-{index}@example.test",
                    },
                    "to_recipients": [
                        {"name": "Derek", "email": "derek@example.test"}
                    ],
                    "cc_recipients": [],
                    "subject": f"{category} matter {index}",
                    "body": f"unique {category} evidence body {index}",
                    "headers": {"message-id": f"<{identity}@example.test>"},
                    "attachments": [],
                    "provider_thread_id": f"thread-{identity}",
                    "explicit_matter_group": f"matter-{identity}",
                    "source": "natural",
                    "received_at": observed_at.isoformat(),
                    "_axis": axis,
                }
            )
    snapshot = build_folder_training_snapshot(
        messages,
        snapshot_id="snapshot-real-e2e",
        description_version="descriptions-e2e",
        observed_at=observed_at,
        seed=20260905,
        proposed_splits=proposed_splits,
    )
    store.persist_training_snapshot(snapshot)
    later_observed_at = observed_at + timedelta(minutes=1)
    for category, folder in (
        ("legal", "Legal"),
        ("financing", "Financing"),
        ("junk", "Trash"),
    ):
        identity = f"{category}-later"
        proposed_splits[identity] = "test"
        messages.append(
            {
                "account_id": "account-1",
                "stable_message_identity": identity,
                "provider_folder_id": category,
                "provider_folder_name": folder,
                "folder_role": "category",
                "bound_category_key": category,
                "folder_binding_status": "active",
                "processed_by_email_service": True,
                "important_signals": normalize_important_signals(
                    provider="imap", raw_signal_names=("\\Flagged",)
                ),
                "sender": {
                    "name": f"{category} later sender",
                    "email": f"{identity}@example.test",
                },
                "to_recipients": [
                    {"name": "Derek", "email": "derek@example.test"}
                ],
                "cc_recipients": [],
                "subject": f"{category} later matter",
                "body": f"unique {category} later evidence body",
                "headers": {"message-id": f"<{identity}@example.test>"},
                "attachments": [],
                "provider_thread_id": f"thread-{identity}",
                "explicit_matter_group": f"matter-{identity}",
                "source": "natural",
                "received_at": later_observed_at.isoformat(),
            }
        )
    successor_snapshot = build_folder_training_snapshot(
        messages,
        snapshot_id="snapshot-real-e2e-successor",
        description_version="descriptions-e2e",
        observed_at=later_observed_at,
        seed=20260905,
        proposed_splits=proposed_splits,
    )
    stored = store.persist_training_snapshot(successor_snapshot)
    cache = EmbeddingCache(registry.root, dimension=4)
    for row in stored["observations"]:
        vector = np.array(
            (
                [1.0, 0.02, 0.02, 1.0 if row["important"] else -1.0]
                if row["category_key"] == "legal"
                else [0.02, 1.0, 0.02, 1.0 if row["important"] else -1.0]
                if row["category_key"] == "financing"
                else [0.02, 0.02, 1.0, 1.0 if row["important"] else -1.0]
            ),
            dtype=np.float32,
        )
        cache.put(
            EmbeddingCacheKey.for_text(
                normalized_text=row["normalized_model_input"],
                input_schema_version=stored["input_schema_version"],
                embedding_model_id="jina-small",
                embedding_revision="gpu4-r17",
            ),
            vector,
        )
    descriptions = {
        "legal": CategoryDescription(
            core="External legal rights and obligations.",
            include=("Contracts, compliance, and disputes.",),
            exclude=("Fundraising and investor relations.",),
            version="descriptions-e2e",
        ),
        "financing": CategoryDescription(
            core="Fundraising and investor relations.",
            include=("Investors, financing terms, and capital events.",),
            exclude=("Contracts, compliance, and disputes.",),
            version="descriptions-e2e",
        ),
        "junk": CategoryDescription(
            core="Unwanted bulk or deceptive mail.",
            include=("Unwanted promotions and spam.",),
            exclude=("Legitimate legal or financing work.",),
            version="descriptions-e2e",
        ),
    }
    description_vectors = {
        "External legal rights and obligations.": (1.0, 0.0, 0.0, 0.0),
        "Contracts, compliance, and disputes.": (1.0, 0.02, 0.0, 0.0),
        "Fundraising and investor relations.": (0.0, 1.0, 0.0, 0.0),
        "Investors, financing terms, and capital events.": (0.02, 1.0, 0.0, 0.0),
        "Unwanted bulk or deceptive mail.": (0.0, 0.0, 1.0, 0.0),
        "Unwanted promotions and spam.": (0.02, 0.02, 1.0, 0.0),
        "Legitimate legal or financing work.": (0.7, 0.7, 0.0, 0.0),
    }
    for text, values in description_vectors.items():
        for description in descriptions.values():
            if text not in (description.core, *description.include, *description.exclude):
                continue
            cache.put(
                EmbeddingCacheKey.for_description(
                    text=text,
                    description_version=description.version,
                    input_schema_version=stored["input_schema_version"],
                    embedding_model_id="jina-small",
                    embedding_revision="gpu4-r17",
                ),
                np.asarray(values, dtype=np.float32),
            )
    error_state = HistoricalSystematicErrorState(
        unresolved=False,
        source="integration-test",
        reason="historical review complete",
        updated_at=observed_at.isoformat(),
    )
    first = train_frozen_embedding_candidate(
        store=store,
        snapshot_id=snapshot.snapshot_id,
        registry=registry,
        cache=cache,
        descriptions=descriptions,
        embedding_model_id="jina-small",
        embedding_revision="gpu4-r17",
        parent_model_id=None,
        historical_systematic_error_state=error_state,
        trained_at=observed_at,
    )
    second = train_frozen_embedding_candidate(
        store=store,
        snapshot_id=successor_snapshot.snapshot_id,
        registry=registry,
        cache=cache,
        descriptions=descriptions,
        embedding_model_id="jina-small",
        embedding_revision="gpu4-r17",
        parent_model_id=None,
        historical_systematic_error_state=error_state,
        trained_at=later_observed_at,
    )
    activate_online_model(registry, second.model_id)
    return store, registry, cache, first, second, stored


def test_warmed_production_runtime_cached_path_includes_full_local_pipeline(tmp_path):
    from app.email_classifier_model import email_message_to_text

    store, registry, cache, _first, second, snapshot = _train_real_candidate_pair(
        tmp_path
    )
    message = {
        "sender": {"name": "Counsel", "email": "law@example.test"},
        "toRecipients": [{"name": "Derek", "email": "derek@example.test"}],
        "ccRecipients": [],
        "subject": "Legal contract review",
        "markdownBody": "External contract compliance obligation.",
        "attachments": [
            {"filename": "contract.pdf", "contentType": "application/pdf"}
        ],
    }
    normalized = email_message_to_text(message)
    cache.put(
        EmbeddingCacheKey.for_text(
            normalized_text=normalized,
            input_schema_version=snapshot["input_schema_version"],
            embedding_model_id="jina-small",
            embedding_revision="gpu4-r17",
        ),
        np.asarray([1.0, 0.02, 0.02, 1.0], dtype=np.float32),
    )

    class NoRemoteEmbedding:
        model_id = "jina-small"
        embedding_revision = "gpu4-r17"

        def embed(self, _texts, **_kwargs):
            raise AssertionError("warmed cached classification must not call GPU4")

    runtime = PromotedEmailClassifierRuntime(
        registry,
        embedding_client_factory=lambda _model: NoRemoteEmbedding(),
        cache_factory=lambda _model: cache,
        observability_store=store,
    )
    elapsed = []
    try:
        warm_snapshot = runtime.snapshot()
        warm_snapshot.predictor(
            OnlineModelInput(
                email_message_to_text(message),
                warm_snapshot.input_schema_version or "",
            )
        )
        for _ in range(100):
            started = time.perf_counter()
            runtime_snapshot = runtime.snapshot()
            result = runtime_snapshot.predictor(
                OnlineModelInput(
                    email_message_to_text(message),
                    runtime_snapshot.input_schema_version or "",
                )
            )
            elapsed.append((time.perf_counter() - started) * 1000)
            assert result.source == "model"
            assert result.value.category == "legal"
        summary = runtime.latency.summary()
    finally:
        runtime.close()

    assert summary["warm_success_cache"]["sample_count"] == 100
    assert store.classifier_runtime_observability(model_id=second.model_id)["timing"][
        "warm_success_cache"
    ]["sample_count"] == 100
    assert float(np.percentile(elapsed, 95)) < 100


def test_component_chain_folder_classifier_lifecycle_uses_sequential_fallback_and_no_reply():
    provider = _LifecycleProvider()
    locator = StoredEmailLocator(
        account_id="account-1",
        folder="INBOX",
        uidvalidity=1,
        uid=7,
        rfc_message_id="<legal-e2e@example.com>",
        thread_id="legal-e2e",
        stable_message_identity="account-1:message-id:<legal-e2e@example.com>",
    )

    agent_result = AgentClassificationResult(
        category="legal",
        important=True,
        certainty="certain",
        confidence=0.98,
        reason="Material contract obligation",
    )
    assert provider.is_read is False
    assert important_effective(
        category=agent_result.category,
        provider_signals=normalize_important_signals(
            provider="imap", raw_signal_names=()
        ),
        model_important=agent_result.important,
    )

    moved = DeterministicEmailActionExecutor(provider).execute(
        _action(EmailAction.MOVE, locator, {"target_folder": "Legal"})
    )
    assert moved.status == "done"
    assert moved.updated_locator is not None
    flagged = DeterministicEmailActionExecutor(provider).execute(
        _action(EmailAction.FLAG_IMPORTANT, moved.updated_locator, {})
    )
    assert flagged.status == "done"
    legal_truth = resolve_email_folder_truth(
        account_id="account-1",
        current_provider_folder_id="legal",
        provider_folders=provider.list_folders(),
        bindings=(
            {
                "account_id": "account-1",
                "provider_folder_id": "legal",
                "category_key": "legal",
                "binding_status": "active",
            },
        ),
    )
    assert legal_truth.category_key == "legal"
    assert provider.is_read is False

    provider.user_move("Financing")
    financing_truth = resolve_email_folder_truth(
        account_id="account-1",
        current_provider_folder_id="financing",
        provider_folders=provider.list_folders(),
        bindings=(
            {
                "account_id": "account-1",
                "provider_folder_id": "financing",
                "category_key": "financing",
                "binding_status": "active",
            },
        ),
    )
    assert financing_truth.category_key == "financing"

    for index in range(50):
        provider.user_move("Legal" if index % 2 else "Financing")
    assert provider.folder_changes >= 50
    readiness = assess_whole_model_readiness(
        (_maturity("email-embedding-mlp-v1"), _maturity("email-embedding-mlp-v2"))
    )
    assert readiness.ready is True
    assert readiness.passing_model_ids == (
        "email-embedding-mlp-v1",
        "email-embedding-mlp-v2",
    )

    historical = DeterministicEmailActionExecutor(provider).execute(
        _action(
            EmailAction.MOVE,
            replace(locator, folder=provider.folder, uid=provider.uid),
            {"target_folder": "Legal"},
        )
    )
    assert historical.status == "done"
    assert provider.folder == "Legal"
    assert provider.is_read is False

    calls: list[str] = []

    def accepted_model(_current):
        calls.append("model")
        return OnlineClassificationResult(source="model", value="legal")

    active = route_online_classification(
        mode=EmailClassifierRuntimeMode.MODEL_PRIMARY,
        current_input="new unread mail",
        model_predict=accepted_model,
        enqueue_agent=lambda _current: calls.append("agent") or "agent-result",
        accept_model=lambda value: OnlineModelAcceptOutcome.accepted(
            {"category": value}
        ),
    )
    assert active.source == "model"
    assert calls == ["model"]

    def timed_out_model(_current):
        calls.append("model-timeout")
        raise TimeoutError("embedding deadline")

    fallback = route_online_classification(
        mode=EmailClassifierRuntimeMode.MODEL_PRIMARY,
        current_input="another unread mail",
        model_predict=timed_out_model,
        enqueue_agent=lambda _current: calls.append("agent-fallback")
        or "agent-result",
        accept_model=lambda _value: pytest.fail("timed-out model must not be accepted"),
    )
    assert fallback.source == "agent"
    assert calls[-2:] == ["model-timeout", "agent-fallback"]

    entries = extract_unsubscribe_entries(
        body_html=(
            '<a href="https://newsletter.example/unsubscribe?id=e2e">'
            "unsubscribe</a>"
        )
    )
    assert len(entries) == 1
    provider.audited_unsubscribe(entries[0])
    trashed = DeterministicEmailActionExecutor(provider).execute(
        _action(
            EmailAction.TRASH,
            replace(locator, folder=provider.folder, uid=provider.uid),
            {},
        )
    )
    assert trashed.status == "done"
    assert provider.folder == "Trash"
    assert provider.audit_unsubscribe_calls == 1
    assert provider.send_calls == provider.reply_calls == 0


def test_real_store_training_registry_runtime_and_historical_action_integration(
    tmp_path, monkeypatch
):
    store, registry, cache, first, second, snapshot = _train_real_candidate_pair(
        tmp_path
    )
    assert first.maturity.passing is True
    assert second.maturity.passing is True
    assert store.latest_training_snapshot_state()["folder_label_watermark"] == 183
    assert first.maturity.source_snapshot_id != second.maturity.source_snapshot_id
    assert first.maturity.source_snapshot_digest != second.maturity.source_snapshot_digest

    class BoundaryEmbeddingClient:
        model_id = "jina-small"
        embedding_revision = "gpu4-r17"

        def embed(self, texts, **_kwargs):
            vectors = np.stack(
                [
                    np.asarray(
                        [1.0, 0.02, 0.02, 1.0]
                        if "legal" in text
                        else [0.02, 0.02, 1.0, 1.0]
                        if "junk" in text
                        else [0.02, 1.0, 0.02, 1.0],
                        dtype=np.float32,
                    )
                    for text in texts
                ]
            )
            return EmbeddingResult(
                vectors=vectors,
                timing=EmbeddingTiming(0.0, 1.0, 1.0, 0.0, 1.0),
            )

    runtime = PromotedEmailClassifierRuntime(
        registry,
        embedding_client_factory=lambda _model: BoundaryEmbeddingClient(),
        cache_factory=lambda _model: cache,
        observability_store=store,
    )
    try:
        runtime_snapshot = runtime.snapshot()
        assert runtime_snapshot.mode is EmailClassifierRuntimeMode.MODEL_PRIMARY
        prediction_result = runtime_snapshot.predictor(
            OnlineModelInput(
                "legal contract and compliance obligation",
                snapshot["input_schema_version"],
            )
        )
    finally:
        runtime.close()
    prediction = prediction_result.value
    assert prediction_result.source == "model"
    assert prediction.category == "legal"
    assert prediction.category_accepted is True
    assert store.classifier_runtime_observability(model_id=second.model_id)["timing"][
        "all"
    ]["sample_count"] == 1

    provider = _LifecycleProvider()
    provider.is_read = True
    message = {
        "accountId": "account-1",
        "folder": "INBOX",
        "uidValidity": 1,
        "uid": 7,
        "messageId": "<historical-legal@example.test>",
        "threadId": "historical-legal",
        "stableMessageIdentity": (
            "account-1:message-id:<historical-legal@example.test>"
        ),
        "providerUnread": False,
        "sender": {"name": "Counsel", "email": "law@example.test"},
        "toRecipients": [{"name": "Derek", "email": "derek@example.test"}],
        "ccRecipients": [],
        "subject": "legal contract",
        "markdownBody": "legal compliance obligation",
        "attachments": [],
    }

    class NoReplyTaskProducer:
        def produce(self, *_args, **_kwargs):
            raise AssertionError("historical category move must not create email task")

    context = type(
        "Context",
        (),
        {
            "folder_targets": {"legal": "Legal", "financing": "Financing"},
            "config_version": "config-e2e",
        },
    )()
    action = execute_historical_model_actions(
        store,
        lambda _account_id: DeterministicEmailActionExecutor(provider),
        NoReplyTaskProducer(),
        message=message,
        prediction=prediction,
        context=context,
        model_id=second.model_id,
        model_text="legal contract and compliance obligation",
        unsubscribe_entries=(),
        read_after=lambda: type(
            "HistoricalReadback",
            (),
            {
                "is_read": provider.is_read,
                "provider_folder_name": provider.folder,
                "folder_role": (
                    FolderRole.TRASH
                    if provider.folder == "Trash"
                    else FolderRole.CATEGORY
                ),
            },
        )(),
    )
    assert action.outcome == "moved_and_flagged"
    assert action.is_read is True
    assert provider.folder == "Legal"
    persisted = store.get_classification_by_stable_identity(
        message["stableMessageIdentity"]
    )
    assert persisted is not None
    assert persisted["classification_source"] == "model"
    assert persisted["action_plan"]["actions"][0] == "move"
    assert store.direct_action_statuses_for_plan(
        persisted["action_plan"]["action_plan_id"]
    )

    runtime = PromotedEmailClassifierRuntime(
        registry,
        embedding_client_factory=lambda _model: BoundaryEmbeddingClient(),
        cache_factory=lambda _model: cache,
    )
    junk_training_text = next(
        str(row["normalized_model_input"])
        for row in snapshot["observations"]
        if row["category_key"] == "junk"
    )
    try:
        junk_result = runtime.snapshot().predictor(
            OnlineModelInput(
                junk_training_text,
                snapshot["input_schema_version"],
            )
        )
    finally:
        runtime.close()
    assert junk_result.value.category == "junk"
    private_url = "https://news.example.test/unsubscribe?token=private-e2e"
    provider_message = parse_rfc822_message(
        (
            "From: spam@example.test\r\n"
            "To: derek@example.test\r\n"
            "Subject: unwanted promotion\r\n"
            "Date: Tue, 08 Sep 2026 08:00:00 +0000\r\n"
            "Message-ID: <junk-e2e@example.test>\r\n"
            "Content-Type: text/html; charset=utf-8\r\n\r\n"
            f'<a href="{private_url}">unsubscribe</a>'
        ).encode(),
        account_id="account-1",
        folder="INBOX",
        uidvalidity=1,
        uid=99,
    )
    provider_message["threadId"] = "junk-e2e"
    entries = extract_unsubscribe_entries(
        body_html=f'<a href="{private_url}">unsubscribe</a>'
    )
    store.create_account(
        {
            "account_id": "account-1",
            "display_name": "Integration",
            "email_address": "derek@example.test",
            "imap_host": "imap.example.test",
            "imap_port": 993,
            "imap_tls": True,
            "imap_username": "derek@example.test",
            "imap_secret_reference": "keychain://integration-imap",
            "smtp_host": "",
            "smtp_port": 465,
            "smtp_tls": True,
            "smtp_username": "",
            "smtp_secret_reference": "",
            "enabled": True,
            "scan_folders": ["INBOX"],
            "scan_interval_seconds": 60,
        }
    )
    task_store = AutoReplyStore(store.path)
    producer = EmailActionTaskProducer(task_store, store)
    accepted = persist_model_primary_classification(
        store,
        producer,
        message=provider_message,
        prediction=junk_result.value,
        context=context,
        model_id=second.model_id,
        model_text="__subject__junk promotion",
        unsubscribe_entries=entries,
    )
    assert accepted.persisted["classification_source"] == "model"
    [queued_task] = task_store.list_reply_tasks(channel="email")
    task = queued_task
    payload = json.loads(task.trigger_message_json)
    [projected_entry] = payload["unsubscribe_entries"]
    policy = BrowserNetworkPolicy(frozenset({"https://news.example.test"}))
    accepted_action = ProposedAction.model_validate(
        {
            "description": "Unsubscribe the classified junk source",
            "capability": "email_browser",
            "operation": "unsubscribe",
            "target": {
                "action_identity": payload["action_identity"],
                "account_id": "account-1",
                "stable_message_identity": provider_message[
                    "stableMessageIdentity"
                ],
                "thread_identity": "junk-e2e",
                "entry_reference": projected_entry["reference"],
                "network_policy_reference": policy.reference,
                "network_policy_origin_references": list(policy.origin_references),
            },
            "payload": {
                "operations": [
                    {
                        "operation_reference": "operation:integration-e2e",
                        "kind": "open_entry",
                        "target_reference": projected_entry["reference"],
                    }
                ]
            },
            "expected_verification": "Read terminal unsubscribe evidence",
        }
    ).model_dump(mode="json")
    class Source:
        def fetch_uid_batch(self, *_args, **_kwargs):
            return type("Batch", (), {"uidvalidity": 1, "messages": (provider_message,)})()

        def logout(self):
            return None

    import app.email_unsubscribe as unsubscribe_module
    import app.email_worker as worker_module

    monkeypatch.setattr(
        worker_module,
        "_build_email_source_factory",
        lambda _settings: lambda _account: Source(),
    )
    browser_calls = []

    def fake_browser(effect, selected_entries, **_kwargs):
        browser_calls.append((effect, selected_entries))
        return {
            "status": "done",
            "outcome": "done",
            "receipt_id": "provider-receipt:integration-e2e",
            "evidence": "terminal-page",
            "result_text": "Unsubscribed",
            "observation_digest": sha256(b"Unsubscribed").hexdigest(),
            "started_at": "2026-09-08T08:00:01+00:00",
            "completed_at": "2026-09-08T08:00:02+00:00",
            "summary": "Unsubscribed",
            "final_step": {
                "sequence": 1,
                "operation": "open_entry",
                "state": "done",
                "reference": "provider-receipt:integration-e2e",
            },
        }

    monkeypatch.setattr(
        unsubscribe_module,
        "execute_unsubscribe_in_dedicated_profile",
        fake_browser,
    )
    operation = worker_module.build_audited_email_unsubscribe_operation(
        type("Settings", (), {"db_path": store.path, "workspace": tmp_path})()
    )

    def execute_unsubscribe(audit_run_id, action):
        return operation.execute(
            task.id,
            task.execution_generation,
            audit_agent_run_id=audit_run_id,
            accepted_action=action,
        )

    orchestrator = AgentOrchestrator(
        store=task_store,
        consumer=_E2EConsumerRunner(task_store, accepted_action),
        audit=_E2EAuditRunner(task_store, execute_unsubscribe),
    )

    def load_context(claimed):
        claimed_payload = json.loads(claimed.trigger_message_json)
        return AgentTaskContext(
            task_id=claimed.id,
            channel=claimed.channel,
            conversation_id=claimed.conversation_id,
            conversation_title=claimed.conversation_title,
            single_chat=False,
            trigger_message_id=claimed.trigger_message_id,
            trigger_sender=claimed.trigger_sender,
            trigger_text=claimed.trigger_text,
            trigger_create_time=claimed.trigger_create_time,
            messages=(),
            materials=(),
            prior_receipts=(),
            trigger_raw_payload=claimed_payload,
        )

    run_email_agent_task_loop(
        task_store,
        orchestrator,
        load_task_context=load_context,
        finalize_task=lambda claimed, result: _finalize_email_task(
            task_store, claimed, result
        ),
        sleep=lambda _seconds: None,
        max_cycles=1,
    )
    terminal_task = task_store.get_reply_task(task.id)
    assert terminal_task is not None
    assert terminal_task.status == "done"
    lineage = task_store.list_agent_runs_for_task_generation(
        task.id, task.execution_generation
    )
    assert [run.role for run in lineage] == [AgentRole.CONSUMER, AgentRole.AUDIT]
    assert lineage[1].parent_agent_run_id == lineage[0].id
    unsubscribe_receipt = store.get_email_unsubscribe_receipt(
        payload["action_identity"]
    )
    assert unsubscribe_receipt is not None
    assert unsubscribe_receipt["receipt_id"] == "provider-receipt:integration-e2e"
    assert len(browser_calls) == 1
    trashed = _run_next_direct_action(
        store,
        lambda _account_id: DeterministicEmailActionExecutor(provider),
        available_account_ids=("account-1",),
    )
    assert trashed is not None
    assert trashed.status == "done"
    assert provider.folder == "Trash"
    store.record_current_provider_observations(
        [
            {
                "account_id": "account-1",
                "stable_message_identity": provider_message[
                    "stableMessageIdentity"
                ],
                "provider_folder_id": "trash",
                "provider_folder_name": "Trash",
                "folder_role": "trash",
                "bound_category_key": None,
                "folder_binding_status": "unbound",
                "important_signals": {"provider_important": False},
            }
        ],
        unavailable_folders=(),
        observed_at="2026-09-08T08:00:03+00:00",
    )
    assert store.get_provider_classification_state(
        int(accepted.persisted["id"])
    )["state"] == "junk"
    assert private_url not in task.trigger_message_json
    assert provider.send_calls == provider.reply_calls == 0


def test_live_restoration_algorithm_relocates_after_ambiguous_move_failure():
    locator = StoredEmailLocator(
        account_id="account-1",
        folder="INBOX",
        uidvalidity=1,
        uid=7,
        rfc_message_id="<restore@example.test>",
        thread_id=None,
        stable_message_identity="account-1:message-id:<restore@example.test>",
    )
    state = {
        "folder": "Test",
        "uid": 9,
        "flags": frozenset({"\\Seen", "custom-keyword", "$Important"}),
        "move_calls": 0,
    }

    def locate(_hint):
        current = replace(locator, folder=state["folder"], uid=state["uid"])
        return ProviderMessageState(
            revision=f"revision-{state['uid']}",
            labels=frozenset(
                flag for flag in state["flags"] if not str(flag).startswith("\\")
            ),
            is_read="\\Seen" in state["flags"],
            archived=False,
            folder=state["folder"],
            trashed=False,
            important_signal_names=frozenset(
                flag
                for flag in state["flags"]
                if flag in {"\\Flagged", "$Important"}
            ),
            required_important_signal_names=frozenset({"\\Flagged"}),
            locator=current,
        )

    def ambiguous_raw_move(_current, folder):
        state["move_calls"] += 1
        state["folder"] = folder
        state["uid"] += 1
        raise TimeoutError("readback failed after MOVE succeeded")

    restored = _restore_designated_provider_message(
        locator_hint=locator,
        original_folder="INBOX",
        original_flags=frozenset({"\\Flagged", "original-keyword"}),
        locate=locate,
        raw_move=ambiguous_raw_move,
        read_flags=lambda _current: state["flags"],
        replace_flags=lambda _current, flags: state.__setitem__("flags", flags),
    )

    assert state["move_calls"] == 1
    assert restored.folder == "INBOX"
    assert restored.uid == 10
    assert state["flags"] == frozenset({"\\Flagged", "original-keyword"})


@pytest.mark.live
def test_live_mailbox_move_flag_truth_and_restore_is_opt_in(tmp_path):
    if os.getenv("CEO_LIVE_EMAIL_FOLDER_CLASSIFIER_E2E") != "1":
        pytest.skip("set CEO_LIVE_EMAIL_FOLDER_CLASSIFIER_E2E=1 for mailbox writes")
    required = {
        name: os.environ.get(name, "").strip()
        for name in (
            "CEO_LIVE_EMAIL_IMAP_HOST",
            "CEO_LIVE_EMAIL_IMAP_USERNAME",
            "CEO_LIVE_EMAIL_IMAP_PASSWORD",
            "CEO_LIVE_EMAIL_ACCOUNT_ID",
            "CEO_LIVE_EMAIL_MESSAGE_UID",
            "CEO_LIVE_EMAIL_MESSAGE_UIDVALIDITY",
            "CEO_LIVE_EMAIL_MESSAGE_ID",
            "CEO_LIVE_EMAIL_LOCATOR_FOLDER",
            "CEO_LIVE_EMAIL_TEST_FOLDER",
        )
    }
    missing = sorted(name for name, value in required.items() if not value)
    if missing:
        pytest.skip("missing live mailbox configuration: " + ", ".join(missing))

    from app.email_provider_actions import ImapDeterministicProvider

    def connect():
        return ImapDeterministicProvider.connect(
            required["CEO_LIVE_EMAIL_IMAP_HOST"],
            required["CEO_LIVE_EMAIL_IMAP_USERNAME"],
            required["CEO_LIVE_EMAIL_IMAP_PASSWORD"],
            port=int(os.getenv("CEO_LIVE_EMAIL_IMAP_PORT") or "993"),
            account_id=required["CEO_LIVE_EMAIL_ACCOUNT_ID"],
        )

    def locate(hint):
        provider = connect()
        try:
            return provider.read_state(hint, action_type=EmailAction.FLAG_IMPORTANT)
        finally:
            provider.close()

    def read_complete_flags(hint):
        provider = connect()
        try:
            state = provider.read_state(
                hint, action_type=EmailAction.FLAG_IMPORTANT
            )
            assert state.locator is not None
            provider._select(state.locator.folder, readonly=True)
            status, data = provider.session.uid(
                "FETCH", str(state.locator.uid), "(FLAGS)"
            )
            assert status == "OK"
            # \Recent is session state and cannot be restored with STORE.  All
            # persisted system flags, keywords, and provider labels are frozen.
            return frozenset(
                flag
                for flag in parse_imap_fetch_flags(data)
                if flag.casefold() != "\\recent"
            )
        finally:
            provider.close()

    def replace_complete_flags(hint, wanted):
        provider = connect()
        try:
            state = provider.read_state(
                hint, action_type=EmailAction.FLAG_IMPORTANT
            )
            assert state.locator is not None
            actual = state.locator
            current = read_complete_flags(actual)
            provider._select(actual.folder, readonly=False)
            remove = tuple(sorted(current - wanted))
            add = tuple(sorted(wanted - current))
            if remove:
                status, _ = provider.session.uid(
                    "STORE",
                    str(actual.uid),
                    "-FLAGS.SILENT",
                    "(" + " ".join(remove) + ")",
                )
                assert status == "OK"
            if add:
                status, _ = provider.session.uid(
                    "STORE",
                    str(actual.uid),
                    "+FLAGS.SILENT",
                    "(" + " ".join(add) + ")",
                )
                assert status == "OK"
        finally:
            provider.close()

    def raw_provider_move(hint, target_folder):
        provider = connect()
        try:
            state = provider.read_state(hint, action_type=EmailAction.MOVE)
            assert state.locator is not None
            actual = state.locator
            assert target_folder in {item.display_name for item in provider.list_folders()}
            provider._select(actual.folder, readonly=False)
            status, _ = provider.session.uid(
                "MOVE", str(actual.uid), target_folder
            )
            assert status == "OK"
        finally:
            provider.close()

    locator_hint = StoredEmailLocator(
        account_id=required["CEO_LIVE_EMAIL_ACCOUNT_ID"],
        folder=required["CEO_LIVE_EMAIL_LOCATOR_FOLDER"],
        uidvalidity=int(required["CEO_LIVE_EMAIL_MESSAGE_UIDVALIDITY"]),
        uid=int(required["CEO_LIVE_EMAIL_MESSAGE_UID"]),
        rfc_message_id=required["CEO_LIVE_EMAIL_MESSAGE_ID"],
        thread_id=None,
        stable_message_identity=(
            required["CEO_LIVE_EMAIL_ACCOUNT_ID"]
            + ":message-id:"
            + required["CEO_LIVE_EMAIL_MESSAGE_ID"]
        ),
    )
    frozen = locate(locator_hint)
    assert frozen.locator is not None
    assert frozen.locator.rfc_message_id == required["CEO_LIVE_EMAIL_MESSAGE_ID"]
    original_locator = frozen.locator
    original_folder = frozen.folder
    original_flags = read_complete_flags(original_locator)
    current = original_locator

    def restoration_failure(
        message: str, state_locator: StoredEmailLocator | None
    ) -> NoReturn:
        rendered = "unavailable" if state_locator is None else repr(state_locator)
        raise AssertionError(
            f"{message}; stable_identity={original_locator.stable_message_identity}; "
            f"message_id={original_locator.rfc_message_id}; current_locator={rendered}"
        )

    try:
        # This deliberately bypasses the service executor to simulate a user
        # moving the designated message in the provider UI.
        raw_provider_move(current, required["CEO_LIVE_EMAIL_TEST_FOLDER"])
        moved_state = locate(locator_hint)
        assert moved_state.locator is not None
        current = moved_state.locator
        assert moved_state.folder == required["CEO_LIVE_EMAIL_TEST_FOLDER"]
        flagged = DeterministicEmailActionExecutor(
            connect(), readback_provider_factory=connect
        ).execute(_action(EmailAction.FLAG_IMPORTANT, current, {}))
        assert flagged.status == "done"
        current = flagged.updated_locator or current
        state = locate(locator_hint)
        assert state.folder == required["CEO_LIVE_EMAIL_TEST_FOLDER"]
        assert "\\Flagged" in state.important_signal_names

        store = EmailStore(tmp_path / "live-provider-observation.sqlite3")
        account_id = required["CEO_LIVE_EMAIL_ACCOUNT_ID"]
        store.create_account(
            {
                "account_id": account_id,
                "display_name": "Live reversible mailbox",
                "email_address": required["CEO_LIVE_EMAIL_IMAP_USERNAME"],
                "imap_host": required["CEO_LIVE_EMAIL_IMAP_HOST"],
                "imap_port": int(os.getenv("CEO_LIVE_EMAIL_IMAP_PORT") or "993"),
                "imap_tls": True,
                "imap_username": required["CEO_LIVE_EMAIL_IMAP_USERNAME"],
                "imap_secret_reference": "live-test-environment",
                "smtp_host": "",
                "smtp_port": 465,
                "smtp_tls": True,
                "smtp_username": "",
                "smtp_secret_reference": "",
                "enabled": True,
                "scan_folders": ["INBOX"],
                "scan_interval_seconds": 60,
            }
        )
        inventory_provider = connect()
        try:
            inventory = inventory_provider.list_folders()
        finally:
            inventory_provider.close()
        target = next(
            item
            for item in inventory
            if item.display_name == required["CEO_LIVE_EMAIL_TEST_FOLDER"]
        )
        store.upsert_verified_folder_binding(
            "legal",
            VerifiedEmailFolderBinding(
                account_id=account_id,
                provider_folder_id=target.provider_folder_id,
                provider_folder_name=target.display_name,
                binding_status="active",
                last_verified_at=datetime.now(timezone.utc).isoformat(),
                provider_folder_role=target.role,
            ),
        )
        classification = EmailClassification.model_validate(
            {
                "classification_id": 1,
                "stable_message_identity": original_locator.stable_message_identity,
                "provider_locator": {
                    "account_id": account_id,
                    "folder": current.folder,
                    "uidvalidity": current.uidvalidity,
                    "uid": current.uid,
                    "rfc_message_id": current.rfc_message_id,
                    "thread_id": current.thread_id,
                },
                "category": "legal",
                "confidence": 1.0,
                "margin": 1.0,
                "probabilities": {"legal": 1.0},
                "model_id": "agent:cold-start",
                "config_version": "live-test-v1",
                "status": EmailClassificationStatus.PENDING_FEEDBACK,
                "classification_source": "model",
                "action_plan": None,
            }
        )
        store.upsert_classification(
            classification,
            sender="live@example.test",
            subject="Designated reversible provider observation",
            preview="Provider folder truth live test",
            model_text="Provider folder truth live test",
        )

        def readonly_source(_account):
            return ImapReadonlyAdapter.connect(
                required["CEO_LIVE_EMAIL_IMAP_HOST"],
                required["CEO_LIVE_EMAIL_IMAP_USERNAME"],
                required["CEO_LIVE_EMAIL_IMAP_PASSWORD"],
                port=int(os.getenv("CEO_LIVE_EMAIL_IMAP_PORT") or "993"),
                account_id=account_id,
                mailbox_address=required["CEO_LIVE_EMAIL_IMAP_USERNAME"],
            )

        observer = ProviderTrainingObservationJob(
            state_path=tmp_path / "live-provider-observer.json",
            source_factory=readonly_source,
            email_store=store,
            batch_size=200,
        )
        result = observer.run_once(({"account_id": account_id},))
        for _ in range(99):
            if not result.more_available:
                break
            result = observer.run_once(({"account_id": account_id},))
        else:
            raise AssertionError("live provider observation did not converge")
        provider_truth = store.get_provider_classification_state(1)
        assert provider_truth["category_key"] == "legal"
        assert provider_truth["provider_folder_name"] == target.display_name
        app = FastAPI()
        register_email_routes(app, lambda: store)
        detail = TestClient(app).get("/api/console/email/classifications/1")
        assert detail.status_code == 200
        assert detail.json()["provider_classification"]["category_key"] == "legal"

        observed_at = datetime.now(timezone.utc)
        snapshot = build_folder_training_snapshot(
            result.observations,
            snapshot_id="live-provider-move",
            description_version="live-test",
            observed_at=observed_at,
            seed=20260905,
        )
        store.persist_training_snapshot(snapshot)
        observed = next(
            item
            for item in snapshot.observations
            if item.stable_message_identity == original_locator.stable_message_identity
        )
        assert observed.category_key == "legal"
        assert observed.provider_folder_name == target.display_name
    finally:
        try:
            _restore_designated_provider_message(
                locator_hint=original_locator,
                original_folder=original_folder,
                original_flags=original_flags,
                locate=locate,
                raw_move=raw_provider_move,
                read_flags=read_complete_flags,
                replace_flags=replace_complete_flags,
            )
        except Exception as exc:
            restoration_failure(
                f"live restoration failed: {type(exc).__name__}", current
            )


@pytest.mark.live
def test_live_cached_and_gpu_latency_budgets_are_opt_in(tmp_path):
    if os.getenv("CEO_LIVE_EMAIL_EMBEDDING_E2E") != "1":
        pytest.skip("set CEO_LIVE_EMAIL_EMBEDDING_E2E=1 for GPU4")
    client = EmailEmbeddingClient.from_environment(
        embedding_revision=os.getenv("CEO_EMAIL_EMBEDDING_REVISION") or "live",
        dimension=int(os.getenv("CEO_EMAIL_EMBEDDING_DIMENSION") or "1024"),
    )
    descriptions = {
        "legal": CategoryDescription(
            core="External legal rights and obligations.",
            include=("Contracts and compliance.",),
            exclude=("Fundraising and investor relations.",),
            version="live-description-v1",
        ),
        "financing": CategoryDescription(
            core="Fundraising and investor relations.",
            include=("Investors and financing terms.",),
            exclude=("Contracts and compliance.",),
            version="live-description-v1",
        ),
    }
    description_texts = tuple(
        dict.fromkeys(
            text
            for description in descriptions.values()
            for text in (description.core, *description.include, *description.exclude)
        )
    )
    description_result = client.embed(description_texts)
    by_text = dict(zip(description_texts, description_result.vectors, strict=True))
    vectors = {
        category: DescriptionVectors(
            core=by_text[description.core],
            include=np.stack([by_text[item] for item in description.include]),
            exclude=np.stack([by_text[item] for item in description.exclude]),
        )
        for category, description in descriptions.items()
    }
    training_texts = (
        "legal contract compliance obligation",
        "legal dispute and regulatory obligation",
        "investor financing term sheet",
        "fundraising capital investor update",
    )
    training = client.embed(training_texts)
    matrix = np.concatenate((training.vectors, training.vectors), axis=0)
    classifier = DescriptionAwareEmailClassifier(
        enabled_categories=("legal", "financing"),
        descriptions=descriptions,
        description_vectors=vectors,
        dimension=client.dimension,
        input_schema_version="email-folder-model-input-v2",
        embedding_model_id=client.model_id,
        embedding_revision=client.embedding_revision,
        category_thresholds={"legal": 0.0, "financing": 0.0},
        important_threshold=0.0,
    ).fit(
        matrix,
        ("legal", "legal", "financing", "financing") * 2,
        (False, True, False, True) * 2,
    )
    source = tmp_path / "live-classifier.artifact"
    classifier.save(source)
    digest = sha256(source.read_bytes()).hexdigest()
    registry = EmailModelRegistry(tmp_path / "live-registry")
    compatibility = {
        "enabled_categories": ["legal", "financing"],
        "description_version": "live-description-v1",
        "input_schema_version": classifier.input_schema_version,
        "embedding_model_id": classifier.embedding_model_id,
        "embedding_revision": classifier.embedding_revision,
        "head_format": "description-mlp-v1",
        "parent_model_id": None,
    }
    model_ids = []
    for seconds in (0, 1):
        model_id = build_embedding_model_id(
            trained_at=datetime(2026, 9, 8, tzinfo=timezone.utc)
            + timedelta(seconds=seconds),
            artifact_sha256=digest,
        )
        evidence = {
            "model_id": model_id,
            "source_snapshot_id": f"live-snapshot-{seconds + 1}",
            "source_snapshot_digest": str(seconds + 1) * 64,
            "source_snapshot_observed_at": (
                datetime(2026, 9, 8, tzinfo=timezone.utc)
                + timedelta(seconds=seconds)
            ).isoformat(),
            "folder_label_watermark": 100 + seconds,
            "important_label_watermark": 40 + seconds,
            "trained_at": (
                datetime(2026, 9, 8, tzinfo=timezone.utc)
                + timedelta(seconds=seconds)
            ).isoformat(),
            "compatibility": compatibility,
            "metrics": {
                "categories": {
                    category: {
                        "accepted_precision": 1.0,
                        "accepted_hits": 20 + seconds,
                        "independent_groups": 10 + seconds,
                    }
                    for category in ("legal", "financing")
                },
                "important": {
                    "accepted_precision": 1.0,
                    "accepted_hits": 20 + seconds,
                    "independent_groups": 10 + seconds,
                },
            },
            "hashes": {"artifact_sha256": digest},
            "unresolved_historical_systematic_error": False,
        }
        registry.stage_embedding_candidate(model_id, source, evidence)
        model_ids.append(model_id)
    activate_online_model(registry, model_ids[-1])
    cache = EmbeddingCache(registry.root, dimension=client.dimension)
    runtime = PromotedEmailClassifierRuntime(
        registry,
        embedding_client_factory=lambda _model: client,
        embedding_client_owned=False,
        cache_factory=lambda _model: cache,
    )
    message = {
        "sender": {"name": "Counsel", "email": "law@example.test"},
        "toRecipients": [{"name": "Derek", "email": "derek@example.test"}],
        "ccRecipients": [],
        "subject": "Legal contract review",
        "markdownBody": "External contract compliance obligation.",
        "attachments": [{"filename": "contract.pdf", "contentType": "application/pdf"}],
    }
    from app.email_classifier_model import email_message_to_text

    def classify_message(current_message):
        normalized = email_message_to_text(current_message)
        snapshot = runtime.snapshot()
        assert snapshot.predictor is not None
        return snapshot.predictor(
            OnlineModelInput(normalized, snapshot.input_schema_version or "")
        )

    try:
        first = classify_message(message)
        assert first.source == "model"
        cached_ms = []
        for _ in range(100):
            started = time.perf_counter()
            result = classify_message(message)
            cached_ms.append((time.perf_counter() - started) * 1000)
            assert result.source == "model"
        remote_ms = []
        for index in range(10):
            current = dict(message)
            current["markdownBody"] = (
                f"External contract compliance obligation {index}."
            )
            started = time.perf_counter()
            result = classify_message(current)
            remote_ms.append((time.perf_counter() - started) * 1000)
            assert result.source == "model"
        summary = runtime.latency.summary()
    finally:
        runtime.close()
    assert summary["warm_success_cache"]["sample_count"] == 100
    assert summary["warm_success_remote"]["sample_count"] == 10
    assert float(np.percentile(cached_ms, 95)) < 100
    assert summary["warm_success_remote"]["stages"]["total"]["p95"] < 500
    assert float(np.percentile(remote_ms, 95)) < 500
