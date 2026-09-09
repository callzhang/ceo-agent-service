from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
from threading import Event, Lock
from types import SimpleNamespace

import pytest

from app.email_classifier_contracts import (
    EmailAction,
    EmailCategory,
    EmailClassificationStatus,
    INITIAL_EMAIL_CATEGORY_KEYS,
)
from app.email_classifier_model import CpuTfidfLogisticClassifier
from app.email_classifier_scan import (
    AgentScanContext,
    EmailScanConfig,
    scan_agent_classification_batch,
    should_enqueue_agent_classification,
    route_online_classification,
    scan_imap_accounts,
    scan_readonly_batch,
)
from app.email_classifier_runtime import (
    EmailClassifierRuntimeMode,
    OnlineClassificationResult,
    OnlineModelAcceptError,
    OnlineModelAcceptOutcome,
    OnlineModelAcceptStage,
    OnlineModelInput,
    RuntimeSnapshot,
)
from app.email_provider_folders import FolderRole
from app.email_classifier_training import CategoryEligibility, EmailActionEligibility
from app.email_imap_readonly import (
    ImapUidBatch,
    attach_ephemeral_unsubscribe_authentication,
    parse_rfc822_message,
)
from app.email_store import EmailStore
from app.email_task_producer import EmailClassificationTaskProducer
from app.email_unsubscribe import UnsubscribeAuthenticationEvidence
from app.email_worker import _scan_config


class FakeSource:
    def __init__(self, messages: list[dict[str, object]]):
        self.messages = messages
        self.account_id = str(messages[0]["accountId"])
        self.calls: list[tuple[str, int | None, int, int]] = []

    def fetch_uid_batch(
        self,
        mailbox: str = "INBOX",
        *,
        cursor_uidvalidity: int | None,
        last_seen_uid: int,
        limit: int = 50,
        unread_only: bool = False,
        excluded_uids: frozenset[int] = frozenset(),
    ) -> ImapUidBatch:
        uidvalidity = int(self.messages[0]["uidValidity"])
        self.calls.append((mailbox, cursor_uidvalidity, last_seen_uid, limit))
        minimum_uid = (
            0
            if unread_only
            else last_seen_uid
            if cursor_uidvalidity == uidvalidity
            else 0
        )
        return ImapUidBatch(
            account_id=str(self.messages[0]["accountId"]),
            folder=mailbox,
            uidvalidity=uidvalidity,
            previous_uidvalidity=cursor_uidvalidity,
            messages=[
                message
                for message in self.messages
                if int(message["uid"]) > minimum_uid
                and int(message["uid"]) not in excluded_uids
            ][:limit],
        )


@dataclass
class StaticPrediction:
    label: str
    probability: float
    margin: float = 0.1
    model_version: str = "static-model-v1"

    @property
    def probabilities(self) -> dict[str, float]:
        return {self.label: self.probability}


@dataclass
class StaticClassifier:
    prediction: StaticPrediction

    def predict_message(self, message):
        del message
        return self.prediction


def _category_eligibility(
    *,
    eligible: tuple[EmailCategory, ...] = (),
    threshold: float = 0.8,
    model_id: str = "static-model-v1",
) -> dict[str, CategoryEligibility]:
    return {
        category: CategoryEligibility(
            category=category,
            configured_threshold=threshold,
            validated_precision=0.99 if category in eligible else None,
            validation_sample_count=30 if category in eligible else 0,
            auto_action_eligible=category in eligible,
            reason=(
                "precision_and_sample_gate_met"
                if category in eligible
                else "insufficient_validation_samples"
            ),
            source_model_id=model_id,
            action_eligibility={
                action: EmailActionEligibility(
                    action=action,
                    auto_action_eligible=category in eligible,
                    reason=(
                        "action_precision_and_support_gate_met"
                        if category in eligible
                        else "action_precision_and_support_gate_not_met"
                    ),
                    source_model_id=model_id,
                    evidence_reference=(
                        f"email-model-eligibility:{model_id}:{action.value}"
                    ),
                )
                for action in EmailAction
                if action is not EmailAction.AUTO_REPLY
            },
        )
        for category in INITIAL_EMAIL_CATEGORY_KEYS
    }


def _message() -> dict[str, object]:
    return {
        "messageId": "<message-1@example.com>",
        "accountId": "dingtalk-account",
        "folder": "INBOX",
        "uidValidity": 42,
        "uid": 1,
        "from": {"email": "team@stardust.ai"},
        "subject": "project deadline",
        "textBody": "please confirm the work sprint",
    }


def test_online_scan_route_is_agent_primary_before_promotion():
    calls = []

    result = route_online_classification(
        mode=EmailClassifierRuntimeMode.AGENT_PRIMARY,
        current_input=OnlineModelInput("current", "input-v3"),
        model_predict=lambda _value: calls.append("model"),
        enqueue_agent=lambda value: calls.append(("agent", value)) or "queued",
        accept_model=lambda _value: calls.append("accepted"),
    )

    assert result.source == "agent"
    assert calls == [("agent", OnlineModelInput("current", "input-v3"))]


def test_online_scan_route_accepts_model_without_agent_and_falls_back_on_reject():
    calls = []
    current = OnlineModelInput("current", "input-v3")
    accepted = route_online_classification(
        mode=EmailClassifierRuntimeMode.MODEL_PRIMARY,
        current_input=current,
        model_predict=lambda _value: OnlineClassificationResult(
            source="model", value="legal"
        ),
        enqueue_agent=lambda _value: calls.append("agent"),
        accept_model=lambda value: (
            calls.append(("accepted", value))
            or OnlineModelAcceptOutcome.accepted({"id": 1})
        ),
    )
    rejected = route_online_classification(
        mode=EmailClassifierRuntimeMode.MODEL_PRIMARY,
        current_input=current,
        model_predict=lambda _value: OnlineClassificationResult(
            source="model", value=None, fallback_reason="model_rejected"
        ),
        enqueue_agent=lambda _value: calls.append("agent") or "queued",
        accept_model=lambda _value: calls.append("must-not-accept"),
    )

    assert accepted.source == "model"
    assert rejected.source == "agent"
    assert calls == [("accepted", "legal"), "agent"]


def test_online_scan_route_falls_back_once_when_accept_fails_before_commit():
    calls = []

    result = route_online_classification(
        mode=EmailClassifierRuntimeMode.MODEL_PRIMARY,
        current_input=OnlineModelInput("current", "input-v3"),
        model_predict=lambda _value: OnlineClassificationResult(
            source="model", value="work"
        ),
        enqueue_agent=lambda _value: calls.append("agent") or "queued",
        accept_model=lambda _value: (_ for _ in ()).throw(
            OnlineModelAcceptError(
                OnlineModelAcceptStage.BEFORE_DURABLE_COMMIT, "database unavailable"
            )
        ),
    )

    assert result.source == "agent"
    assert calls == ["agent"]


def test_online_scan_route_repairs_after_commit_without_agent():
    calls = []

    def accept(_value):
        calls.append("accept")
        if len(calls) == 1:
            raise OnlineModelAcceptError(
                OnlineModelAcceptStage.AFTER_DURABLE_COMMIT, "task queue interrupted"
            )
        return OnlineModelAcceptOutcome.already_committed({"id": 1})

    result = route_online_classification(
        mode=EmailClassifierRuntimeMode.MODEL_PRIMARY,
        current_input=OnlineModelInput("current", "input-v3"),
        model_predict=lambda _value: OnlineClassificationResult(
            source="model", value="work"
        ),
        enqueue_agent=lambda _value: calls.append("agent"),
        accept_model=accept,
    )

    assert result.source == "model"
    assert result.accept_outcome.status == "already_committed"
    assert calls == ["accept", "accept"]


def test_online_scan_route_never_allows_shadow_history_into_scan_loop():
    with pytest.raises(ValueError, match="scan loop"):
        route_online_classification(
            mode=EmailClassifierRuntimeMode.SHADOW_HISTORY,
            current_input=OnlineModelInput("current", "input-v3"),
            model_predict=lambda _value: None,
            enqueue_agent=lambda _value: None,
            accept_model=lambda _value: None,
        )


@pytest.mark.parametrize(
    ("unread", "role", "configured", "has_record", "expected"),
    (
        (True, FolderRole.INBOX, False, False, True),
        (True, FolderRole.UNBOUND, True, False, True),
        (False, FolderRole.INBOX, False, False, False),
        (True, FolderRole.CATEGORY, True, False, False),
        (True, FolderRole.JUNK, True, False, False),
        (True, FolderRole.TRASH, True, False, False),
        (True, FolderRole.SENT, True, False, False),
        (True, FolderRole.DRAFT, True, False, False),
        (True, FolderRole.UNBOUND, False, False, False),
        (True, FolderRole.INBOX, False, True, False),
    ),
)
def test_agent_scan_gate_is_exact(unread, role, configured, has_record, expected):
    assert (
        should_enqueue_agent_classification(
            provider_unread=unread,
            folder_role=role,
            configured_unclassified_source=configured,
            has_stable_record=has_record,
        )
        is expected
    )


def test_old_unread_mail_is_eligible_without_a_date_gate() -> None:
    message = _message() | {"date": "1999-01-01T00:00:00Z", "providerUnread": True}

    assert should_enqueue_agent_classification(
        provider_unread=message["providerUnread"],
        folder_role=FolderRole.INBOX,
        configured_unclassified_source=False,
        has_stable_record=False,
    )


def test_agent_scan_enqueues_once_without_invoking_local_model(tmp_path: Path) -> None:
    message = _message() | {"providerUnread": True, "date": "1999-01-01"}
    source = FakeSource([message])
    store = EmailStore(tmp_path / "agent-scan.sqlite3")
    calls = []
    producer = SimpleNamespace(
        adapter=SimpleNamespace(has_stable_record=lambda _identity: bool(calls)),
        produce=lambda value, **context: calls.append((value, context)),
    )
    scan_context = AgentScanContext(
        allowed_category_keys=("work", "junk"),
        category_descriptions={
            "work": {"core": "Business."},
            "junk": {"core": "Unwanted."},
        },
        folder_targets={"work": "Work"},
        config_version="config-v1",
    )

    first = scan_agent_classification_batch(
        source,
        store,
        producer,
        scan_context,
        folder_role=FolderRole.INBOX,
        configured_unclassified_source=False,
    )

    assert first.persisted_count == 1
    assert len(calls) == 1
    assert calls[0][0]["date"] == "1999-01-01"


def test_promoted_scan_persists_accepted_model_result_without_agent_task(tmp_path):
    message = _message() | {"providerUnread": True, "date": "2026-09-07"}
    source = FakeSource([message])
    store = EmailStore(tmp_path / "model-primary-scan.sqlite3")
    agent_calls = []
    accepted = []
    producer = SimpleNamespace(
        adapter=SimpleNamespace(has_stable_record=lambda _identity: False),
        produce=lambda *_args, **_kwargs: agent_calls.append("agent"),
    )
    runtime = SimpleNamespace(
        mode=EmailClassifierRuntimeMode.MODEL_PRIMARY,
        input_schema_version="input-v3",
        model_predict=lambda _value: OnlineClassificationResult(
            source="model", value="accepted-prediction"
        ),
    )

    result = scan_agent_classification_batch(
        source,
        store,
        producer,
        AgentScanContext(
            allowed_category_keys=("work", "junk"),
            category_descriptions={"work": {}, "junk": {}},
            folder_targets={"work": "Work"},
            config_version="config-v1",
        ),
        folder_role=FolderRole.INBOX,
        configured_unclassified_source=False,
        online_runtime=runtime,
        accept_model=lambda raw, prediction, _entries, model_text, _model_id: (
            accepted.append((raw, prediction, model_text))
            or OnlineModelAcceptOutcome.accepted({"id": 1})
        ),
    )

    assert result.persisted_count == 1
    assert agent_calls == []
    assert accepted[0][0] is message
    assert accepted[0][1] == "accepted-prediction"
    assert accepted[0][2]


def test_snapshot_online_and_benchmark_use_identical_canonical_input(tmp_path):
    import json
    from datetime import datetime, timezone
    from app.email_candidate_benchmark import _online_input
    from app.email_important import ImportantSignals
    from app.email_training_observer import _provider_observation
    from app.email_training_snapshot import build_folder_training_snapshot, MODEL_INPUT_SCHEMA_VERSION

    message = _message() | {
        "providerUnread": True, "stableMessageIdentity": "same-input",
        "importantSignals": ImportantSignals((), False),
        "toRecipients": [{"name": "Derek", "email": "d@example.test"}],
        "ccRecipients": [{"email": "cc@example.test"}],
        "textBody": "Current reply\n\n> Quoted historical reply\n> retain this context",
        "markdownBody": "Must use canonical textBody even if markdown differs",
        "inReplyTo": "<parent@example.test>", "references": ["<parent@example.test>"],
        "autoSubmitted": "auto-generated",
        "listUnsubscribe": "<https://example.test/unsubscribe?token=private>",
        "listUnsubscribePost": "List-Unsubscribe=One-Click",
        "attachments": [{"filename": "合同.pdf", "mime_type": "application/pdf",
                         "size_bytes": 1234, "inline": False, "content_id": "cid-1",
                         "disposition": "attachment"}],
    }
    observation = _provider_observation(
        account_id=message["accountId"],
        folder=SimpleNamespace(provider_folder_id="work", display_name="Work"),
        role=FolderRole.CATEGORY, binding={"category_key": "work", "binding_status": "active"},
        message=message, email_store=SimpleNamespace(has_stable_classification=lambda _: True),
    )
    snapshot = build_folder_training_snapshot(
        [observation], snapshot_id="same-input-snapshot", description_version="description-v1",
        observed_at=datetime(2026, 9, 8, tzinfo=timezone.utc), seed=17,
        proposed_splits={"same-input": "train"},
    )
    frozen = snapshot.observations[0]
    captured = []
    runtime = SimpleNamespace(
        mode=EmailClassifierRuntimeMode.MODEL_PRIMARY,
        input_schema_version=MODEL_INPUT_SCHEMA_VERSION,
        model_predict=lambda value: (captured.append(value) or OnlineClassificationResult(
            source="model", value="accepted-prediction")),
    )
    scan_agent_classification_batch(
        FakeSource([message]), EmailStore(tmp_path / "same-input.sqlite3"),
        SimpleNamespace(adapter=SimpleNamespace(has_stable_record=lambda _: False),
                        produce=lambda *a, **kw: pytest.fail("unexpected Agent fallback")),
        AgentScanContext(allowed_category_keys=("work", "junk"), category_descriptions={"work": {}, "junk": {}},
                         folder_targets={"work": "Work"}, config_version="config-v1"),
        folder_role=FolderRole.INBOX, configured_unclassified_source=False, online_runtime=runtime,
        accept_model=lambda *a: OnlineModelAcceptOutcome.accepted({"id": 1}),
    )
    assert captured[0].normalized_text == frozen.normalized_model_input
    benchmark_input = _online_input(frozen.to_dict(), MODEL_INPUT_SCHEMA_VERSION)
    assert benchmark_input == captured[0]
    from app.email_embedding_cache import EmbeddingCacheKey
    keys = [EmbeddingCacheKey.for_text(
        normalized_text=text, input_schema_version=MODEL_INPUT_SCHEMA_VERSION,
        embedding_model_id="jina-small", embedding_revision="same-revision",
    ) for text in (frozen.normalized_model_input, captured[0].normalized_text, benchmark_input.normalized_text)]
    assert keys[0] == keys[1] == keys[2]
    assert keys[0].normalized_input_hash == frozen.normalized_model_input_hash
    payload = json.loads(captured[0].normalized_text)
    assert "> Quoted historical reply" in payload["body"]
    assert payload["attachments"][0]["filename"] == "合同.pdf"
    assert payload["attachment_count"] == 1
    assert payload["headers"]["in-reply-to"] == "<parent@example.test>"
    assert payload["unsubscribe"]["one_click"] is True
    assert "token=private" not in captured[0].normalized_text


@pytest.mark.parametrize("malformed", [
    {"attachments": [{"filename": "bad.pdf", "size_bytes": "not-an-integer"}]},
    {"toRecipients": "not-an-address-list"},
    {"references": [123]},
])
def test_canonical_input_error_uses_agent_fallback_and_continues_batch(tmp_path, malformed):
    messages = [
        _message() | {"providerUnread": True, **malformed},
        _message() | {"providerUnread": True, "uid": 2, "messageId": "<second@example.test>"},
    ]
    agents, predictions, accepted = [], [], []
    runtime = SimpleNamespace(
        mode=EmailClassifierRuntimeMode.MODEL_PRIMARY,
        input_schema_version="email-folder-model-input-v2",
        model_predict=lambda value: (predictions.append(value) or OnlineClassificationResult(source="model", value="accepted")),
    )
    store = EmailStore(tmp_path / "input-error.sqlite3")
    result = scan_agent_classification_batch(
        FakeSource(messages), store,
        SimpleNamespace(adapter=SimpleNamespace(has_stable_record=lambda _: False),
                        produce=lambda message, **kw: agents.append(message)),
        AgentScanContext(allowed_category_keys=("work", "junk"), category_descriptions={"work": {}, "junk": {}},
                         folder_targets={"work": "Work"}, config_version="config-v1"),
        folder_role=FolderRole.INBOX, configured_unclassified_source=False, online_runtime=runtime,
        accept_model=lambda *args: (accepted.append(args) or OnlineModelAcceptOutcome.accepted({"id": 1})),
    )
    assert agents == [messages[0]]
    assert len(predictions) == len(accepted) == 1
    assert result.persisted_count == 2
    assert store.get_scan_cursor("dingtalk-account", "INBOX")["last_seen_uid"] == 2


def test_scan_binds_each_message_to_one_runtime_snapshot_during_refresh(tmp_path):
    first_prediction = object()
    second_prediction = object()
    snapshots = [
        RuntimeSnapshot.model_primary(
            predictor=lambda _value: OnlineClassificationResult(
                source="model", value=first_prediction
            ),
            model_id="model-first",
            input_schema_version="input-v3",
            compatibility={"embedding_revision": "r1"},
        ),
        RuntimeSnapshot.model_primary(
            predictor=lambda _value: OnlineClassificationResult(
                source="model", value=second_prediction
            ),
            model_id="model-second",
            input_schema_version="input-v3",
            compatibility={"embedding_revision": "r2"},
        ),
    ]
    snapshot_calls = []

    class RefreshingRuntime:
        @property
        def mode(self):
            pytest.fail("scan split-read mutable runtime mode")

        @property
        def model_predict(self):
            pytest.fail("scan split-read mutable predictor")

        @property
        def model_id(self):
            pytest.fail("scan split-read mutable model id")

        def snapshot(self):
            snapshot = snapshots[len(snapshot_calls)]
            snapshot_calls.append(snapshot)
            return snapshot

    messages = [
        _message() | {"uid": 1, "messageId": "<snapshot-1@example.com>", "providerUnread": True},
        _message() | {"uid": 2, "messageId": "<snapshot-2@example.com>", "providerUnread": True},
    ]
    accepted = []
    producer = SimpleNamespace(
        adapter=SimpleNamespace(has_stable_record=lambda _identity: False),
        produce=lambda *_args, **_kwargs: pytest.fail("model should bypass Agent"),
    )

    scan_agent_classification_batch(
        FakeSource(messages),
        EmailStore(tmp_path / "snapshot-scan.sqlite3"),
        producer,
        AgentScanContext(
            allowed_category_keys=("work", "junk"),
            category_descriptions={"work": {}, "junk": {}},
            folder_targets={"work": "Work"},
            config_version="config-v1",
        ),
        folder_role=FolderRole.INBOX,
        configured_unclassified_source=False,
        online_runtime=RefreshingRuntime(),
        accept_model=lambda _raw, prediction, _entries, _text, model_id: (
            accepted.append((prediction, model_id))
            or OnlineModelAcceptOutcome.accepted({"id": len(accepted)})
        ),
    )

    assert accepted == [
        (first_prediction, "model-first"),
        (second_prediction, "model-second"),
    ]
    assert snapshot_calls == snapshots


def test_concurrent_refresh_cannot_mix_predictor_and_model_id_for_one_message(
    tmp_path,
):
    prediction_started = Event()
    release_prediction = Event()
    state_lock = Lock()
    first_prediction = object()
    second_prediction = object()

    def first_predictor(_value):
        prediction_started.set()
        release_prediction.wait(1.0)
        return OnlineClassificationResult(source="model", value=first_prediction)

    snapshots = {
        "current": RuntimeSnapshot.model_primary(
            predictor=first_predictor,
            model_id="model-first",
            input_schema_version="input-v3",
            compatibility={"embedding_revision": "r1"},
        )
    }

    class Runtime:
        def snapshot(self):
            with state_lock:
                return snapshots["current"]

        def refresh(self):
            with state_lock:
                snapshots["current"] = RuntimeSnapshot.model_primary(
                    predictor=lambda _value: OnlineClassificationResult(
                        source="model", value=second_prediction
                    ),
                    model_id="model-second",
                    input_schema_version="input-v3",
                    compatibility={"embedding_revision": "r2"},
                )

    runtime = Runtime()
    accepted = []
    producer = SimpleNamespace(
        adapter=SimpleNamespace(has_stable_record=lambda _identity: False),
        produce=lambda *_args, **_kwargs: pytest.fail("model should bypass Agent"),
    )

    with ThreadPoolExecutor(max_workers=1) as pool:
        scanning = pool.submit(
            scan_agent_classification_batch,
            FakeSource([_message() | {"providerUnread": True}]),
            EmailStore(tmp_path / "concurrent-snapshot.sqlite3"),
            producer,
            AgentScanContext(
                allowed_category_keys=("work", "junk"),
                category_descriptions={"work": {}, "junk": {}},
                folder_targets={"work": "Work"},
                config_version="config-v1",
            ),
            folder_role=FolderRole.INBOX,
            configured_unclassified_source=False,
            online_runtime=runtime,
            accept_model=lambda _raw, prediction, _entries, _text, model_id: (
                accepted.append((prediction, model_id))
                or OnlineModelAcceptOutcome.accepted({"id": 1})
            ),
        )
        assert prediction_started.wait(0.5)
        runtime.refresh()
        release_prediction.set()
        scanning.result(timeout=1.0)

    assert accepted == [(first_prediction, "model-first")]


def test_promoted_scan_rejection_enqueues_agent_exactly_once(tmp_path):
    message = _message() | {"providerUnread": True, "date": "2026-09-07"}
    source = FakeSource([message])
    store = EmailStore(tmp_path / "model-fallback-scan.sqlite3")
    agent_calls = []
    producer = SimpleNamespace(
        adapter=SimpleNamespace(has_stable_record=lambda _identity: False),
        produce=lambda value, **context: agent_calls.append((value, context)),
    )
    runtime = SimpleNamespace(
        mode=EmailClassifierRuntimeMode.MODEL_PRIMARY,
        input_schema_version="input-v3",
        model_predict=lambda _value: OnlineClassificationResult(
            source="model", value=None, fallback_reason="model_rejected"
        ),
    )

    scan_agent_classification_batch(
        source,
        store,
        producer,
        AgentScanContext(
            allowed_category_keys=("work", "junk"),
            category_descriptions={"work": {}, "junk": {}},
            folder_targets={"work": "Work"},
            config_version="config-v1",
        ),
        folder_role=FolderRole.INBOX,
        configured_unclassified_source=False,
        online_runtime=runtime,
        accept_model=lambda *_args: pytest.fail("rejected model must not persist"),
    )

    assert len(agent_calls) == 1


def test_agent_scan_never_enqueues_read_mail() -> None:
    message = _message() | {"providerUnread": False}
    source = FakeSource([message])
    calls = []
    store = SimpleNamespace(
        get_scan_cursor=lambda *_args: None,
        record_scan_cursor=lambda **_kwargs: None,
    )
    producer = SimpleNamespace(
        adapter=SimpleNamespace(has_stable_record=lambda _identity: False),
        produce=lambda *_args, **_kwargs: calls.append("called"),
    )

    scan_agent_classification_batch(
        source,
        store,
        producer,
        AgentScanContext(
            allowed_category_keys=("work", "junk"),
            category_descriptions={"work": {}, "junk": {}},
            folder_targets={"work": "Work"},
            config_version="config-v1",
        ),
        folder_role=FolderRole.INBOX,
        configured_unclassified_source=False,
    )

    assert calls == []


def test_agent_scan_extracts_real_html_candidate_ephemerally_without_persistence(
    tmp_path: Path,
) -> None:
    private_url = "https://news.example.test/unsubscribe?token=scan-html-secret"
    message = parse_rfc822_message(
        (
            b"From: blast@example.test\r\nSubject: Offer\r\n"
            b"Message-ID: <scan-html@example.test>\r\n"
            b"Content-Type: text/html; charset=utf-8\r\n\r\n"
            + f'<a href="{private_url}">Unsubscribe</a>'.encode()
        ),
        account_id="dingtalk-account",
        folder="INBOX",
        uidvalidity=42,
        uid=87,
    )
    database = tmp_path / "html-scan.sqlite3"
    store = EmailStore(database)

    result = scan_agent_classification_batch(
        FakeSource([message]),
        store,
        EmailClassificationTaskProducer(store),
        AgentScanContext(
            allowed_category_keys=("work", "junk"),
            category_descriptions={"work": {}, "junk": {}},
            folder_targets={"work": "Work"},
            config_version="config-v1",
        ),
        folder_role=FolderRole.INBOX,
        configured_unclassified_source=False,
    )

    assert result.persisted_count == 1
    assert private_url.encode() not in database.read_bytes()
    with store._connect() as db:
        payload = db.execute(
            "select input_json from email_agent_classification_tasks"
        ).fetchone()[0]
    assert "body_html_https" in payload
    assert "unsubscribe-entry:" in payload
    assert "_ephemeralBodyHtml" not in payload


def test_agent_scan_preserves_verified_one_click_candidate_metadata(
    tmp_path: Path,
) -> None:
    private_url = "https://news.example.test/unsubscribe?token=scan-one-click"
    message = parse_rfc822_message(
        (
            b"From: blast@example.test\r\nSubject: Offer\r\n"
            b"Message-ID: <scan-one-click@example.test>\r\n"
            b"List-Unsubscribe: <"
            + private_url.encode()
            + b">\r\nList-Unsubscribe-Post: List-Unsubscribe=One-Click\r\n\r\n"
            b"Unwanted promotion"
        ),
        account_id="dingtalk-account",
        folder="INBOX",
        uidvalidity=42,
        uid=88,
    )
    attach_ephemeral_unsubscribe_authentication(
        message,
        UnsubscribeAuthenticationEvidence(
            dkim_covers_list_unsubscribe=True,
            dkim_covers_list_unsubscribe_post=True,
            evidence_reference="dkim-evidence:scan-one-click",
        ),
    )
    store = EmailStore(tmp_path / "one-click-scan.sqlite3")

    scan_agent_classification_batch(
        FakeSource([message]),
        store,
        EmailClassificationTaskProducer(store),
        AgentScanContext(
            allowed_category_keys=("work", "junk"),
            category_descriptions={"work": {}, "junk": {}},
            folder_targets={"work": "Work"},
            config_version="config-v1",
        ),
        folder_role=FolderRole.INBOX,
        configured_unclassified_source=False,
    )

    with store._connect() as db:
        payload = db.execute(
            "select input_json from email_agent_classification_tasks"
        ).fetchone()[0]
    assert '"source":"header_one_click_https"' in payload
    assert private_url not in payload


def test_agent_scan_revisits_old_unread_uids_after_cursor_advance(
    tmp_path: Path,
) -> None:
    message = _message() | {"providerUnread": True, "uid": 1}
    source = FakeSource([message])
    store = EmailStore(tmp_path / "email.sqlite3")
    store.record_scan_cursor(
        account_id="dingtalk-account",
        folder="INBOX",
        uidvalidity=42,
        last_seen_uid=99,
    )
    calls = []
    producer = SimpleNamespace(
        adapter=SimpleNamespace(has_stable_record=lambda _identity: False),
        produce=lambda *_args, **_kwargs: calls.append("called"),
    )

    result = scan_agent_classification_batch(
        source,
        store,
        producer,
        AgentScanContext(
            allowed_category_keys=("work", "junk"),
            category_descriptions={"work": {}, "junk": {}},
            folder_targets={"work": "Work"},
            config_version="config-v1",
        ),
        folder_role=FolderRole.INBOX,
        configured_unclassified_source=False,
    )

    assert result.persisted_count == 1
    assert calls == ["called"]


def test_agent_scan_excludes_stable_unread_records_before_provider_limit() -> None:
    messages = [
        _message() | {"providerUnread": True, "uid": 1},
        _message()
        | {
            "providerUnread": True,
            "uid": 2,
            "messageId": "<message-2@example.com>",
        },
    ]
    source = FakeSource(messages)
    calls = []
    producer = SimpleNamespace(
        adapter=SimpleNamespace(
            stable_provider_uids=lambda **_kwargs: frozenset({1}),
            has_stable_record=lambda identity: identity.endswith(
                ":<message-1@example.com>"
            ),
        ),
        produce=lambda message, **_kwargs: calls.append(message["uid"]),
    )

    result = scan_agent_classification_batch(
        source,
        SimpleNamespace(
            get_scan_cursor=lambda *_args: {
                "uidvalidity": 42,
                "last_seen_uid": 1,
            },
            record_scan_cursor=lambda **_kwargs: None,
        ),
        producer,
        AgentScanContext(
            allowed_category_keys=("work", "junk"),
            category_descriptions={"work": {}, "junk": {}},
            folder_targets={"work": "Work"},
            config_version="config-v1",
        ),
        folder_role=FolderRole.INBOX,
        configured_unclassified_source=False,
        limit=1,
    )

    assert result.persisted_count == 1
    assert calls == [2]


@pytest.mark.parametrize(
    "status",
    (
        EmailClassificationStatus.PENDING_FEEDBACK,
        EmailClassificationStatus.PROCESSED,
    ),
)
def test_agent_scan_skips_legacy_canonical_only_classification(
    tmp_path: Path, status: EmailClassificationStatus
) -> None:
    from datetime import datetime, timezone

    from app.email_classifier_contracts import (
        EmailClassification,
        EmailProviderLocator,
        build_email_action_plan,
    )

    message = _message() | {"providerUnread": True}
    source = FakeSource([message])
    calls = []
    store = EmailStore(tmp_path / f"canonical-{status.value}.sqlite3")
    locator = EmailProviderLocator(
        account_id="dingtalk-account",
        folder="INBOX",
        uidvalidity=42,
        uid=1,
        rfc_message_id="<message-1@example.com>",
    )
    plan = None
    if status is EmailClassificationStatus.PROCESSED:
        plan = build_email_action_plan(
            classification_id=71,
            account_id="dingtalk-account",
            category="work",
            classification_source="model",
            confidence=0.81,
            model_id="legacy-model:v1",
            config_version="legacy-config:v1",
            actions=(),
            action_parameters={},
            created_at=datetime(2026, 9, 7, tzinfo=timezone.utc),
        )
    store.persist_scan_result(
        EmailClassification(
            classification_id=71,
            stable_message_identity=locator.stable_message_identity,
            provider_locator=locator,
            category="work",
            confidence=0.81,
            margin=0.2,
            probabilities={"work": 0.81},
            model_id="legacy-model:v1",
            config_version="legacy-config:v1",
            status=status,
            classification_source="model",
            action_plan=plan,
        ),
        model_text="__subject__legacy canonical",
    )
    producer = SimpleNamespace(
        adapter=SimpleNamespace(has_stable_record=lambda _identity: False),
        produce=lambda *_args, **_kwargs: calls.append(status.value),
    )

    result = scan_agent_classification_batch(
        source,
        store,
        producer,
        AgentScanContext(
            allowed_category_keys=("work", "junk"),
            category_descriptions={"work": {}, "junk": {}},
            folder_targets={"work": "Work"},
            config_version="config-v1",
        ),
        folder_role=FolderRole.INBOX,
        configured_unclassified_source=False,
    )

    assert result.persisted_count == 0
    assert calls == []


def _training_messages() -> tuple[list[dict[str, object]], list[str]]:
    return (
        [
            {
                "from": {"email": "billing@example.com"},
                "subject": "发票 invoice",
                "textBody": "付款记录",
            },
            {
                "from": {"email": "team@stardust.ai"},
                "subject": "项目 project",
                "textBody": "本周工作安排",
            },
            {
                "from": {"email": "ads@example.com"},
                "subject": "marketing promotion",
                "textBody": "special offer",
            },
            {
                "from": {"email": "finance@example.com"},
                "subject": "receipt receipt",
                "textBody": "payment invoice",
            },
            {
                "from": {"email": "engineering@stardust.ai"},
                "subject": "work sprint",
                "textBody": "project deadline",
            },
            {
                "from": {"email": "news@example.com"},
                "subject": "newsletter",
                "textBody": "promotion offer",
            },
        ],
        ["external_billing", "work", "junk", "external_billing", "work", "junk"],
    )


def test_cpu_model_feeds_readonly_scan_and_persists_only_classification(tmp_path: Path):
    training_messages, labels = _training_messages()
    classifier = CpuTfidfLogisticClassifier(model_version="model-integration-test")
    classifier.fit_messages(
        training_messages,
        labels,
        enabled_category_keys=tuple(sorted(set(labels))),
    )

    messages = [
        {
            "messageId": "message-1",
            "accountId": "dingtalk-account",
            "folder": "INBOX",
            "uidValidity": 42,
            "uid": 1,
            "from": {"email": "team@stardust.ai"},
            "subject": "project deadline",
            "textBody": "please confirm the work sprint",
        },
        {
            "messageId": "message-2",
            "accountId": "dingtalk-account",
            "folder": "INBOX",
            "uidValidity": 42,
            "uid": 2,
            "from": {"email": "ads@example.com"},
            "subject": "special offer",
            "textBody": "marketing promotion",
        },
    ]
    source = FakeSource(messages)
    store = EmailStore(tmp_path / "email.sqlite3")
    config = EmailScanConfig(
        config_version="scan-model-test-v1",
        thresholds={category: 0.0 for category in INITIAL_EMAIL_CATEGORY_KEYS},
        actions={},
        category_eligibility=_category_eligibility(
            eligible=tuple(
                EmailCategory(category) for category in INITIAL_EMAIL_CATEGORY_KEYS
            ),
            threshold=0.0,
            model_id="model-integration-test",
        ),
    )

    result = scan_readonly_batch(source, classifier, store, config, limit=2)

    assert source.calls == [("INBOX", None, 0, 2)]
    assert result.fetched_count == result.persisted_count == 2
    assert result.pending_feedback_count == 0
    assert result.processed_count == 2
    rows, total = store.list_classifications(
        status=EmailClassificationStatus.PROCESSED, limit=10, offset=0
    )
    assert total == 2
    assert {row["category"] for row in rows} == {"work", "junk"}
    assert all(row["model_id"] == "model-integration-test" for row in rows)
    assert all("https://" not in row["preview"] for row in rows)
    assert store.get_scan_cursor("dingtalk-account", "INBOX") == {
        "account_id": "dingtalk-account",
        "folder": "INBOX",
        "uidvalidity": 42,
        "last_seen_uid": 2,
        "last_success_at": store.get_scan_cursor("dingtalk-account", "INBOX")[
            "last_success_at"
        ],
        "last_error": "",
    }


def test_scan_produces_agent_actions_only_after_plan_is_persisted(tmp_path: Path):
    message = _message()
    source = FakeSource([message])
    store = EmailStore(tmp_path / "email.sqlite3")
    config = EmailScanConfig(
        config_version="scan-task-production-v1",
        thresholds={category: 0.0 for category in INITIAL_EMAIL_CATEGORY_KEYS},
        actions={EmailCategory.JUNK: (EmailAction.UNSUBSCRIBE,)},
        category_eligibility=_category_eligibility(
            eligible=(EmailCategory.JUNK,),
            threshold=0.0,
        ),
    )
    callbacks: list[tuple[object, dict[str, object]]] = []

    result = scan_readonly_batch(
        source,
        StaticClassifier(StaticPrediction("junk", 0.99)),
        store,
        config,
        task_producer=lambda classification, raw_message: callbacks.append(
            (classification, dict(raw_message))
        ),
    )

    assert result.processed_count == 1
    assert len(callbacks) == 1
    classification, callback_message = callbacks[0]
    assert classification.action_plan is not None
    assert classification.action_plan.agent_actions == (EmailAction.UNSUBSCRIBE,)
    assert callback_message["messageId"] == message["messageId"]
    persisted = store.get_classification(classification.classification_id)
    assert persisted is not None
    assert (
        persisted["current_action_plan_id"] == classification.action_plan.action_plan_id
    )


def test_task_producer_failure_does_not_advance_cursor_and_retries_idempotently(
    tmp_path: Path,
):
    message = _message()
    source = FakeSource([message])
    store = EmailStore(tmp_path / "email.sqlite3")
    config = EmailScanConfig(
        config_version="scan-task-retry-v1",
        thresholds={category: 0.0 for category in INITIAL_EMAIL_CATEGORY_KEYS},
        actions={EmailCategory.JUNK: (EmailAction.UNSUBSCRIBE,)},
        category_eligibility=_category_eligibility(
            eligible=(EmailCategory.JUNK,),
            threshold=0.0,
        ),
    )
    attempts = 0

    def produce(classification, raw_message):
        nonlocal attempts
        attempts += 1
        persisted = store.get_classification(classification.classification_id)
        assert persisted is not None
        assert raw_message["messageId"] == message["messageId"]
        if attempts == 1:
            raise RuntimeError("injected task producer failure")

    with pytest.raises(RuntimeError, match="injected task producer failure"):
        scan_readonly_batch(
            source,
            StaticClassifier(StaticPrediction("junk", 0.99)),
            store,
            config,
            task_producer=produce,
        )

    assert store.get_scan_cursor("dingtalk-account", "INBOX") is None
    first = store.list_classifications(
        status=EmailClassificationStatus.PROCESSED,
        limit=10,
        offset=0,
    )[0]
    assert len(first) == 1

    result = scan_readonly_batch(
        source,
        StaticClassifier(StaticPrediction("junk", 0.99)),
        store,
        config,
        task_producer=produce,
    )

    assert attempts == 2
    assert result.persisted_count == 1
    assert (
        store.get_scan_cursor("dingtalk-account", "INBOX")["last_seen_uid"]
        == message["uid"]
    )


def test_scan_config_rejects_auto_reply_when_outbound_reply_is_disabled():
    with pytest.raises(ValueError, match="auto_reply is disabled"):
        EmailScanConfig(
            config_version="scan-no-reply-v1",
            thresholds={category: 0.95 for category in INITIAL_EMAIL_CATEGORY_KEYS},
            actions={EmailCategory.WORK: (EmailAction.AUTO_REPLY,)},
            action_parameters={
                EmailCategory.WORK: {
                    EmailAction.AUTO_REPLY: {"instruction": "Acknowledge the email."}
                }
            },
        )


def test_repeated_readonly_scan_is_idempotent_and_preserves_feedback(tmp_path: Path):
    training_messages, labels = _training_messages()
    classifier = CpuTfidfLogisticClassifier(model_version="model-idempotence-test")
    classifier.fit_messages(
        training_messages,
        labels,
        enabled_category_keys=tuple(sorted(set(labels))),
    )
    messages = [
        {
            "messageId": "message-1",
            "accountId": "dingtalk-account",
            "folder": "INBOX",
            "uidValidity": 42,
            "uid": 1,
            "from": {"email": "team@stardust.ai"},
            "subject": "project deadline",
            "textBody": "please confirm the work sprint",
        },
        {
            "messageId": "message-2",
            "accountId": "dingtalk-account",
            "folder": "INBOX",
            "uidValidity": 42,
            "uid": 2,
            "from": {"email": "ads@example.com"},
            "subject": "special offer",
            "textBody": "marketing promotion",
        },
    ]
    source = FakeSource(messages)
    store = EmailStore(tmp_path / "email.sqlite3")
    config = EmailScanConfig.cold_start(config_version="idempotence-v1")

    first = scan_readonly_batch(source, classifier, store, config, limit=2)
    pending, pending_total = store.list_classifications(
        status=EmailClassificationStatus.PENDING_FEEDBACK, limit=10, offset=0
    )
    assert first.pending_feedback_count == 2
    assert pending_total == 2
    confirmed = store.confirm_classification(
        pending[0]["id"],
        EmailCategory.LEGAL,
        feedback_request_id="scan-reset-feedback",
        expected_current_action_plan_id=None,
    )
    assert confirmed is not None

    for index, message in enumerate(source.messages, start=1):
        message["uidValidity"] = 84
        message["uid"] = index
    second = scan_readonly_batch(source, classifier, store, config, limit=2)

    processed, processed_total = store.list_classifications(
        status=EmailClassificationStatus.PROCESSED, limit=10, offset=0
    )
    pending_after, pending_after_total = store.list_classifications(
        status=EmailClassificationStatus.PENDING_FEEDBACK, limit=10, offset=0
    )
    assert second.persisted_count == 2
    assert processed_total == 1
    assert pending_after_total == 1
    assert processed[0]["category"] == "legal"
    assert processed[0]["classification_source"] == "user"
    assert (
        pending_after[0]["stable_message_identity"]
        != processed[0]["stable_message_identity"]
    )
    assert len(store.list_training_examples()) == 1


def test_classification_failure_does_not_persist_message_or_advance_cursor(
    tmp_path: Path,
):
    class FailingClassifier:
        def predict_message(self, message):
            del message
            raise RuntimeError("model unavailable")

    store = EmailStore(tmp_path / "email.sqlite3")

    with pytest.raises(RuntimeError, match="model unavailable"):
        scan_readonly_batch(
            FakeSource([_message()]),
            FailingClassifier(),
            store,
            EmailScanConfig.cold_start(),
        )

    assert store.get_scan_cursor("dingtalk-account", "INBOX") is None
    with sqlite3.connect(tmp_path / "email.sqlite3") as db:
        assert db.execute("select count(*) from email_messages").fetchone()[0] == 0
        assert (
            db.execute("select count(*) from email_classifications").fetchone()[0] == 0
        )


@pytest.mark.parametrize(
    "failure_kind",
    ("value_error", "runtime_error", "persistence_runtime_error"),
)
def test_multi_account_scan_isolates_ordinary_folder_failures(
    tmp_path: Path,
    failure_kind: str,
):
    class FolderSource:
        def __init__(self, account_id: str):
            self.account_id = account_id

        def fetch_uid_batch(
            self,
            folder: str,
            *,
            cursor_uidvalidity: int | None,
            last_seen_uid: int,
            limit: int,
        ) -> ImapUidBatch:
            del cursor_uidvalidity, last_seen_uid, limit
            if folder == "bad" and failure_kind == "value_error":
                raise ValueError("malformed provider payload SECRET")
            return ImapUidBatch(
                account_id=self.account_id,
                folder=folder,
                uidvalidity=42,
                previous_uidvalidity=None,
                messages=[
                    {
                        "messageId": f"<{self.account_id}-{folder}@example.com>",
                        "accountId": self.account_id,
                        "folder": folder,
                        "uidValidity": 42,
                        "uid": 1,
                        "from": {"email": "sender@example.com"},
                        "subject": f"{failure_kind}-{folder}",
                        "textBody": "body",
                    }
                ],
            )

        def logout(self) -> None:
            return None

    class FolderClassifier:
        def predict_message(self, message):
            if message["folder"] == "bad" and failure_kind == "runtime_error":
                raise RuntimeError("classifier failure SECRET")
            return StaticPrediction("work", 0.61)

    class FolderStore(EmailStore):
        def persist_scan_result(self, classification, **kwargs):
            if (
                classification.provider_locator.folder == "bad"
                and failure_kind == "persistence_runtime_error"
            ):
                raise RuntimeError("persistence operation failure SECRET")
            return super().persist_scan_result(classification, **kwargs)

    store = FolderStore(tmp_path / "isolated.sqlite3")
    result = scan_imap_accounts(
        [
            {
                "account_id": "account-a",
                "enabled": True,
                "scan_folders": ("bad", "good"),
            },
            {"account_id": "account-b", "enabled": True, "scan_folders": ("good",)},
        ],
        lambda account: FolderSource(str(account["account_id"])),
        FolderClassifier(),
        store,
        EmailScanConfig.cold_start(),
    )

    assert [
        (
            account.account_id,
            [(folder.folder, folder.error_code) for folder in account.folders],
        )
        for account in result.accounts
    ] == [
        ("account-a", [("bad", "scan_failed"), ("good", "")]),
        ("account-b", [("good", "")]),
    ]
    assert "SECRET" not in repr(result)
    assert store.get_scan_cursor("account-a", "bad") is None
    assert store.get_scan_cursor("account-a", "good")["last_seen_uid"] == 1
    assert store.get_scan_cursor("account-b", "good")["last_seen_uid"] == 1


def test_high_confidence_is_pending_when_category_lacks_validation_samples(
    tmp_path: Path,
):
    eligibility = _category_eligibility()
    eligibility[EmailCategory.WORK] = CategoryEligibility(
        category=EmailCategory.WORK,
        configured_threshold=0.8,
        validated_precision=0.99,
        validation_sample_count=2,
        auto_action_eligible=False,
        reason="sample_gate_not_met",
    )
    config = EmailScanConfig(
        config_version="eligibility-samples-v1",
        thresholds={category: 0.8 for category in INITIAL_EMAIL_CATEGORY_KEYS},
        actions={EmailCategory.WORK: (EmailAction.LABEL,)},
        category_eligibility=eligibility,
        action_parameters={
            EmailCategory.WORK: {
                EmailAction.LABEL: {"labels": ["work"]},
            }
        },
    )
    store = EmailStore(tmp_path / "email.sqlite3")

    result = scan_readonly_batch(
        FakeSource([_message()]),
        StaticClassifier(StaticPrediction("work", 0.99)),
        store,
        config,
    )

    assert result.processed_count == 0
    assert result.pending_feedback_count == 1
    rows, total = store.list_classifications(
        status=EmailClassificationStatus.PENDING_FEEDBACK,
        limit=10,
        offset=0,
    )
    assert total == 1
    assert rows[0]["action_plan"] is None


def test_high_confidence_is_pending_when_category_is_disabled(tmp_path: Path):
    config = EmailScanConfig(
        config_version="disabled-category-v1",
        thresholds={category: 0.8 for category in INITIAL_EMAIL_CATEGORY_KEYS},
        actions={EmailCategory.WORK: (EmailAction.LABEL,)},
        category_eligibility=_category_eligibility(eligible=(EmailCategory.WORK,)),
        action_parameters={
            EmailCategory.WORK: {
                EmailAction.LABEL: {"labels": ["work"]},
            }
        },
        category_enabled={
            category: category != EmailCategory.WORK.value
            for category in INITIAL_EMAIL_CATEGORY_KEYS
        },
    )
    store = EmailStore(tmp_path / "email.sqlite3")

    result = scan_readonly_batch(
        FakeSource([_message()]),
        StaticClassifier(StaticPrediction("work", 0.99)),
        store,
        config,
    )

    assert result.processed_count == 0
    assert result.pending_feedback_count == 1
    rows, total = store.list_classifications(
        status=EmailClassificationStatus.PENDING_FEEDBACK,
        limit=10,
        offset=0,
    )
    assert total == 1
    assert rows[0]["action_plan"] is None


def test_high_confidence_eligible_category_creates_action_plan(tmp_path: Path):
    config = EmailScanConfig(
        config_version="eligibility-approved-v1",
        thresholds={category: 0.8 for category in INITIAL_EMAIL_CATEGORY_KEYS},
        actions={EmailCategory.WORK: (EmailAction.LABEL,)},
        category_eligibility=_category_eligibility(eligible=(EmailCategory.WORK,)),
        action_parameters={
            EmailCategory.WORK: {
                EmailAction.LABEL: {"labels": ["work"]},
            }
        },
    )
    store = EmailStore(tmp_path / "email.sqlite3")

    result = scan_readonly_batch(
        FakeSource([_message()]),
        StaticClassifier(StaticPrediction("work", 0.99)),
        store,
        config,
    )

    assert result.processed_count == 1
    assert result.pending_feedback_count == 0
    rows, total = store.list_classifications(
        status=EmailClassificationStatus.PROCESSED,
        limit=10,
        offset=0,
    )
    assert total == 1
    assert rows[0]["action_plan"]["actions"] == ["label"]


def test_model_action_plan_contains_only_independently_eligible_actions(
    tmp_path: Path,
):
    rows = [
        {
            "category": category,
            "description": category,
            "enabled": True,
            "threshold": 0.85,
            "actions": (
                [EmailAction.LABEL.value, EmailAction.TRASH.value]
                if category == EmailCategory.WORK.value
                else []
            ),
            "action_parameters": (
                {EmailAction.LABEL.value: {"labels": ["Work"]}}
                if category == EmailCategory.WORK.value
                else {}
            ),
            "config_version": "per-action-v1",
        }
        for category in INITIAL_EMAIL_CATEGORY_KEYS
    ]
    record = type(
        "ActiveModelRecord",
        (),
        {
            "status": "active",
            "metadata": type(
                "Metadata",
                (),
                {
                    "model_id": "static-model-v1",
                    "validation_method": "time-ordered-holdout",
                    "per_category_metrics": {
                        EmailCategory.WORK.value: {
                            "precision": 0.96,
                            "validation_sample_count": 30,
                            "validation_positive_support": 30,
                            "configured_threshold": 0.85,
                            "evaluated_threshold": 0.85,
                            "auto_action_eligible": True,
                            "eligibility_reason": "precision_and_sample_gate_met",
                        }
                    },
                },
            )(),
        },
    )()
    config = _scan_config(
        type("Store", (), {"list_configs": lambda self: rows})(), record
    )
    store = EmailStore(tmp_path / "email.sqlite3")

    result = scan_readonly_batch(
        FakeSource([_message()]),
        StaticClassifier(StaticPrediction("work", 0.99)),
        store,
        config,
    )

    assert result.processed_count == 1
    classifications, total = store.list_classifications(
        status=EmailClassificationStatus.PROCESSED,
        limit=10,
        offset=0,
    )
    assert total == 1
    assert classifications[0]["action_plan"]["actions"] == ["label"]


def test_processed_model_rescan_preserves_original_authorization_snapshot(
    tmp_path: Path,
):
    classifier = StaticClassifier(StaticPrediction("work", 0.91))
    source = FakeSource([_message()])
    store = EmailStore(tmp_path / "email.sqlite3")
    config = EmailScanConfig(
        config_version="plan-identity-v1",
        thresholds={category: 0.8 for category in INITIAL_EMAIL_CATEGORY_KEYS},
        actions={EmailCategory.WORK: (EmailAction.LABEL,)},
        category_eligibility=_category_eligibility(eligible=(EmailCategory.WORK,)),
        action_parameters={
            EmailCategory.WORK: {
                EmailAction.LABEL: {"labels": ["work"]},
            }
        },
    )

    scan_readonly_batch(source, classifier, store, config)
    first_rows, _ = store.list_classifications(
        status=EmailClassificationStatus.PROCESSED,
        limit=10,
        offset=0,
    )
    first_plan = first_rows[0]["action_plan"]

    classifier.prediction = StaticPrediction(
        "work", 0.99, model_version="static-model-v2"
    )
    source.messages[0]["uidValidity"] = 84
    source.messages[0]["uid"] = 2
    scan_readonly_batch(source, classifier, store, config)
    second_rows, _ = store.list_classifications(
        status=EmailClassificationStatus.PROCESSED,
        limit=10,
        offset=0,
    )
    second_plan = second_rows[0]["action_plan"]

    assert first_plan["confidence"] == 0.91
    assert second_plan["confidence"] == 0.91
    assert first_plan["model_id"] == "static-model-v1"
    assert second_plan["model_id"] == "static-model-v1"
    assert first_plan["action_plan_version"] == 1
    assert second_plan["action_plan_version"] == 1
    assert first_plan["action_plan_id"] == second_plan["action_plan_id"]
    with sqlite3.connect(tmp_path / "email.sqlite3") as db:
        plan_history = db.execute(
            """
            select action_plan_id, action_plan_version
            from email_action_plans
            order by action_plan_version
            """
        ).fetchall()
        current_action_plan_id = db.execute(
            "select current_action_plan_id from email_classifications"
        ).fetchone()[0]
    assert plan_history == [(first_plan["action_plan_id"], 1)]
    assert current_action_plan_id == first_plan["action_plan_id"]
