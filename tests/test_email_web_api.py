from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import gc
import sqlite3
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

import app.email_store as email_store_module
from app.audit_web import create_audit_app
from app.email_classifier_contracts import (
    EmailAction,
    EmailAttachmentMetadata,
    EmailCategory,
    INITIAL_EMAIL_CATEGORY_KEYS,
    EmailClassification,
    EmailClassificationStatus,
    build_versioned_email_action_plan,
)
from app.email_category_config import VerifiedEmailFolderBinding
from app.email_provider_folders import FolderRole
from app.email_provider_folders import ProviderFolder
from app.email_worker import ProviderFolderBindingCoordinator
from app.email_classifier_model import CpuTfidfLogisticClassifier
from app.email_model_registry import (
    EmailModelMetadata,
    EmailModelRegistry,
    build_embedding_model_id,
    build_model_id,
)
from app.email_embedding_client import DEFAULT_EMBEDDING_MODEL_ID
from app.email_html_text import visible_email_text
from app.email_important import ImportantSignals
from app.email_classifier_runtime import EmailClassifierRuntimeMode
from app.email_training_snapshot import build_folder_training_snapshot
from app.email_store import (
    EmailFolderBindingConflict,
    EmailStore,
    email_action_identity,
    email_unsubscribe_effect_digest,
)
from app.email_task_adapter import email_conversation_id
from app.store import AgentRole, AutoReplyStore
from app.web_api.email import _project_legacy_model_inventory, register_email_routes


_CANONICAL_EMBEDDING_MODEL_FIRST = build_embedding_model_id(
    trained_at=datetime(2026, 9, 8, 8, 0, tzinfo=timezone.utc),
    artifact_sha256="a" * 64,
)
_CANONICAL_EMBEDDING_MODEL_SECOND = build_embedding_model_id(
    trained_at=datetime(2026, 9, 8, 8, 1, tzinfo=timezone.utc),
    artifact_sha256="b" * 64,
)


def _client(tmp_path: Path) -> TestClient:
    database = tmp_path / "worker.sqlite3"
    app = FastAPI()
    register_email_routes(app, lambda: EmailStore(database))
    return TestClient(app)


def test_email_large_classification_id_round_trips_as_text(tmp_path: Path):
    store = EmailStore(tmp_path / "large-id.sqlite3")
    identity = 8423079112545370123
    classification = EmailClassification.model_validate({
        "classification_id": identity,
        "stable_message_identity": "account-1:message-id:<large@example.com>",
        "provider_locator": {"account_id": "account-1", "folder": "INBOX", "uidvalidity": 1, "uid": 1, "rfc_message_id": "<large@example.com>", "thread_id": "large"},
        "category": EmailCategory.NOTIFICATION, "confidence": 0.167, "margin": 0.032,
        "probabilities": {"notification": 0.167}, "model_id": "test-model",
        "config_version": "test-v1", "status": EmailClassificationStatus.PENDING_FEEDBACK,
        "classification_source": "model", "action_plan": None,
    })
    store.persist_scan_result(classification, sender="sender@example.com", subject="Large ID", preview="Test", model_text="Test")
    app = FastAPI()
    register_email_routes(app, lambda: store)
    client = TestClient(app)
    listed = client.get("/api/console/email/classifications?status=pending_feedback").json()["items"][0]
    assert listed["id"] == str(identity)
    detail = client.get(f"/api/console/email/classifications/{listed['id']}")
    assert detail.json()["item"]["id"] == str(identity)
    response = client.post(f"/api/console/email/classifications/{listed['id']}/feedback", json={
        "category": "notification", "feedback_request_id": f"email-feedback:{listed['id']}", "expected_current_action_plan_id": None,
    })
    assert response.status_code == 200, response.text
    assert response.json()["item"]["id"] == str(identity)
    assert store.get_classification(identity)["classification_source"] == "user"


class _FakeSignalProvider:
    def __init__(self, names: set[str], *, unavailable: bool = False) -> None:
        self.names = names
        self.unavailable = unavailable
        self.calls: list[tuple[str, bool]] = []
        self.closed = False

    def set_important_signal(self, locator, signal, *, present):
        from app.email_provider_actions import ImapMessageUnavailable

        if self.unavailable:
            raise ImapMessageUnavailable("gone")
        self.calls.append((signal, present))
        (self.names.add if present else self.names.discard)(signal)
        return frozenset(self.names)

    def close(self):
        self.closed = True


def _signal_client(tmp_path: Path, provider: _FakeSignalProvider):
    store = EmailStore(tmp_path / "signals.sqlite3")
    store.create_account({
        "account_id": "account-1", "display_name": "Mail", "email_address": "me@example.test",
        "imap_host": "imap.example.test", "imap_port": 993, "imap_tls": True,
        "imap_username": "me@example.test", "imap_secret_reference": "keychain://x",
        "smtp_host": "", "smtp_port": 465, "smtp_tls": True, "smtp_username": "",
        "smtp_secret_reference": "", "enabled": True, "scan_folders": ["INBOX"],
        "scan_interval_seconds": 60,
    })
    identity = 4242
    store.persist_scan_result(
        EmailClassification.model_validate({
            "classification_id": identity,
            "stable_message_identity": "account-1:message-id:<signal@example.com>",
            "provider_locator": {"account_id": "account-1", "folder": "INBOX", "uidvalidity": 1, "uid": 5, "rfc_message_id": "<signal@example.com>", "thread_id": "signal"},
            "category": EmailCategory.NOTIFICATION, "confidence": 0.5, "margin": 0.1,
            "probabilities": {"notification": 0.5}, "model_id": "m", "config_version": "v1",
            "status": EmailClassificationStatus.PENDING_FEEDBACK,
            "classification_source": "model", "action_plan": None,
        }),
        sender="s@example.com", subject="Signal", preview="p", model_text="t",
    )
    app = FastAPI()
    register_email_routes(app, lambda: store, imap_provider_factory=lambda account: provider)
    return TestClient(app), store, identity


def test_star_and_flag_are_toggled_on_the_mailbox_and_read_back(tmp_path: Path):
    provider = _FakeSignalProvider(set())
    client, store, identity = _signal_client(tmp_path, provider)
    url = f"/api/console/email/classifications/{identity}/provider-signal"

    starred = client.post(url, json={"signal": "star", "value": True})
    both = client.post(url, json={"signal": "flag", "value": True})
    cleared = client.post(url, json={"signal": "star", "value": False})

    assert starred.json() == {"ok": True, "starred": True, "important_flag": False}
    assert both.json() == {"ok": True, "starred": True, "important_flag": True}
    assert cleared.json() == {"ok": True, "starred": False, "important_flag": True}
    assert provider.calls == [("\\Flagged", True), ("$Important", True), ("\\Flagged", False)]
    assert provider.closed


def test_a_signal_change_updates_the_observed_state_the_lists_read(tmp_path: Path):
    provider = _FakeSignalProvider(set())
    client, store, identity = _signal_client(tmp_path, provider)
    store.record_current_provider_observations(
        [{
            "account_id": "account-1",
            "stable_message_identity": "account-1:message-id:<signal@example.com>",
            "provider_folder_id": "INBOX", "provider_folder_name": "INBOX",
            "folder_role": "inbox", "bound_category_key": None,
            "folder_binding_status": "unbound",
            "important_signals": {"provider_important": False, "raw_signal_names": []},
        }],
        unavailable_folders=(), observed_at="2026-09-25T00:00:00+00:00",
    )

    client.post(f"/api/console/email/classifications/{identity}/provider-signal", json={"signal": "star", "value": True})

    state = store.get_provider_classification_state(identity)
    assert state["starred"] is True and state["important"] is True


def test_a_moved_message_is_reported_instead_of_pretending_to_toggle(tmp_path: Path):
    provider = _FakeSignalProvider(set(), unavailable=True)
    client, _store, identity = _signal_client(tmp_path, provider)

    response = client.post(f"/api/console/email/classifications/{identity}/provider-signal", json={"signal": "flag", "value": True})

    assert response.status_code == 409
    assert response.json()["code"] == "email_message_unavailable"
    assert provider.closed


def test_processing_progress_reports_exact_queue_counts_and_scan_cursors(tmp_path: Path):
    client, store, _identity = _signal_client(tmp_path, _FakeSignalProvider(set()))
    store.record_scan_cursor(account_id="account-1", folder="INBOX", uidvalidity=1, last_seen_uid=77)

    payload = client.get("/api/console/email/processing-progress").json()

    assert payload["ok"] is True
    assert payload["waiting_for_owner"] == 1
    assert payload["provider_actions"] == {"done": 0, "pending": 0, "processing": 0, "failed": 0, "skipped": 0}
    assert payload["classification_queue"] == {"pending": 0, "processing": 0}
    assert payload["unsubscribe_queue"] == {"pending": 0, "processing": 0}
    assert payload["throughput"] == {"window_minutes": 30, "finished": 0, "per_minute": 0.0, "median_seconds": None}
    assert [(scan["account"], scan["folder"], scan["last_seen_uid"], scan["last_error"]) for scan in payload["scans"]] == [("Mail", "INBOX", 77, "")]
    assert payload["scans"][0]["last_success_at"]


def test_email_unsubscribe_filter_returns_dedicated_task_rows(tmp_path: Path):
    store = EmailStore(tmp_path / "unsubscribe-filter.sqlite3")
    store.list_unsubscribe_classifications = lambda *, limit, offset: ([{
        "id": 47, "category": "junk", "sender": "newsletter@example.com",
        "subject": "Weekly newsletter", "status": "processed",
        "classification_source": "agent", "updated_at": "2026-09-14T12:00:00+00:00",
    }], 1)
    app = FastAPI()
    register_email_routes(app, lambda: store)

    response = TestClient(app).get(
        "/api/console/email/classifications?status=unsubscribe&page=1&page_size=20"
    )

    assert response.status_code == 200, response.text
    assert response.json()["meta"]["total"] == 1
    assert response.json()["items"] == [{
        "id": "47", "category": "junk", "sender": "newsletter@example.com",
        "subject": "Weekly newsletter", "status": "processed",
        "classification_source": "agent", "updated_at": "2026-09-14T12:00:00+00:00",
        "important": None,
        "provider_classification": {"state": "unavailable", "reason": "classification_missing"},
        "unsubscribe_state": None,
    }]


def test_email_unsubscribe_filter_reports_each_rows_newest_unsubscribe_state(tmp_path: Path):
    store = EmailStore(tmp_path / "unsubscribe-state.sqlite3")
    store.list_unsubscribe_classifications = lambda *, limit, offset: ([
        {"id": 47, "subject": "a"}, {"id": 48, "subject": "b"},
    ], 2)
    events = {
        47: [
            {"kind": "provider_action", "status": "done"},
            {"kind": "unsubscribe", "status": "done", "outcome": "skipped_login_required"},
            {"kind": "unsubscribe", "status": "done", "outcome": "done"},
        ],
        48: [{"kind": "unsubscribe", "status": "processing"}],
    }
    store.list_email_classification_observability = lambda classification_id, **_: events[classification_id]
    app = FastAPI()
    register_email_routes(app, lambda: store)

    response = TestClient(app).get(
        "/api/console/email/classifications?status=unsubscribe&page=1&page_size=20"
    )

    assert response.status_code == 200, response.text
    assert [item["unsubscribe_state"] for item in response.json()["items"]] == [
        {"status": "done", "outcome": "done"},
        {"status": "processing", "outcome": None},
    ]


class _ZeroTimeoutEmailStore(EmailStore):
    def _open_connection(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=0)
        db.execute("pragma busy_timeout = 0")
        db.execute("pragma foreign_keys = on")
        db.row_factory = sqlite3.Row
        return db


class _NonExecutingExecutor:
    def __init__(self, workspace: Path):
        self.workspace = workspace

    def recover(self):
        return 0

    def run_once(self):
        return []

    def stop(self, turn_id):
        del turn_id
        return None

    def confirm(self, confirmation_id):
        raise AssertionError(f"unexpected confirmation: {confirmation_id}")

    def cancel(self, confirmation_id):
        raise AssertionError(f"unexpected cancellation: {confirmation_id}")

    def close(self):
        return True


def _assert_email_endpoints_unavailable(client: TestClient) -> None:
    responses = (
        client.get("/api/console/email/classifications?status=invalid"),
        client.post("/api/console/email/classifications/999/feedback"),
        client.get("/api/console/email/classifications/999"),
        client.get("/api/console/email/config"),
        client.put("/api/console/email/config/invalid"),
        client.get("/api/console/email/accounts"),
        client.post("/api/console/email/accounts"),
        client.put("/api/console/email/accounts/missing"),
        client.post("/api/console/email/accounts/missing/test"),
    )
    expected = {
        "ok": False,
        "code": "email_store_unavailable",
        "message": "Email storage is unavailable",
        "details": {},
    }
    for response in responses:
        assert response.status_code == 503
        assert response.json() == expected


def _assert_audit_app_email_unavailable(database: Path, tmp_path: Path) -> None:
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "index.html").write_text("", encoding="utf-8")
    app = create_audit_app(
        database,
        workbench_asset_dir=assets,
        workbench_workspace=tmp_path,
        workbench_executor=_NonExecutingExecutor(tmp_path),
    )

    with TestClient(
        app,
        client=("127.0.0.1", 50000),
        headers={"Host": "127.0.0.1:8765"},
    ) as client:
        assert client.get("/healthz").json() == {"ok": True, "status": "ok"}
        tasks = client.get("/api/console/tasks?page=1&page_size=1")
        assert tasks.status_code == 200
        _assert_email_endpoints_unavailable(client)


def test_email_routes_initialize_and_reuse_one_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    database = tmp_path / "singleton.sqlite3"
    initialize_calls = 0
    factory_calls = 0
    original_initialize = EmailStore._initialize

    def counted_initialize(self: EmailStore) -> None:
        nonlocal initialize_calls
        initialize_calls += 1
        original_initialize(self)

    def factory() -> EmailStore:
        nonlocal factory_calls
        factory_calls += 1
        return EmailStore(database)

    monkeypatch.setattr(EmailStore, "_initialize", counted_initialize)
    app = FastAPI()
    register_email_routes(app, factory)

    with TestClient(app) as client:
        classifications = client.get(
            "/api/console/email/classifications?page=1&page_size=1"
        )
        configs = client.get("/api/console/email/config")
        update = client.put(
            "/api/console/email/config/work",
            json={
                "core_description": "Daily work operations.",
                "include": ["customer delivery"],
                "exclude": ["personal matters"],
                "threshold": 0.9,
                "enabled": True,
                "description_version": "work-description-v2",
                "config_version": "email-config-v1",
                "expected_current_version": next(
                    item["config_version"] for item in configs.json()["items"]
                    if item["category_key"] == "work"
                ),
            },
        )
        feedback = client.post(
            "/api/console/email/classifications/999/feedback",
            json={
                "category": "work",
                "feedback_request_id": "missing-classification-feedback",
                "expected_current_action_plan_id": None,
            },
        )

    assert classifications.status_code == 200
    assert configs.status_code == 200
    assert update.status_code == 200
    assert feedback.status_code == 404
    assert factory_calls == 1
    assert initialize_calls == 1


def test_email_learning_endpoint_exposes_current_state_without_private_paths(
    tmp_path: Path,
) -> None:
    database = tmp_path / "learning.sqlite3"
    registry = EmailModelRegistry(tmp_path / "models")
    service = SimpleNamespace(
        registry=registry,
        retrain_state_path=tmp_path / "models" / "retrain-state.json",
    )
    app = FastAPI()
    register_email_routes(
        app,
        lambda: EmailStore(database),
        email_learning_factory=lambda: service,
    )

    response = TestClient(app).get("/api/console/email/learning")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["learning"]["active_model_id"] is None
    assert payload["learning"]["pending_examples"] == 0
    assert payload["learning"]["models"] == []


def _stage_learning_evidence_model(
    registry: EmailModelRegistry,
    tmp_path: Path,
    *,
    trained_at: datetime,
    suffix: str,
    parent_model_id: str | None = None,
) -> str:
    source = tmp_path / f"learning-{suffix}.pkl"
    texts = [
        f"{category} {variant}"
        for category in INITIAL_EMAIL_CATEGORY_KEYS
        for variant in ("primary", "secondary")
    ]
    labels = [
        category
        for category in INITIAL_EMAIL_CATEGORY_KEYS
        for _variant in ("primary", "secondary")
    ]
    classifier = CpuTfidfLogisticClassifier(model_version="candidate").fit(
        texts,
        labels,
        enabled_category_keys=INITIAL_EMAIL_CATEGORY_KEYS,
    )
    classifier.save(source)
    digest = sha256(source.read_bytes()).hexdigest()
    model_id = build_model_id(trained_at=trained_at, artifact_sha256=digest)
    metrics = {
        category: {
            "precision": 0.96,
            "recall": 0.95,
            "f1": 0.955,
            "validation_sample_count": 20,
            "validation_positive_support": 20,
            "automatic_candidate_count": 20,
            "evaluated_threshold": 0.9,
            "configured_threshold": 0.9,
            "minimum_precision": 0.95,
            "minimum_validation_samples": 20,
            "auto_action_eligible": True,
            "eligibility_reason": "eligible",
        }
        for category in INITIAL_EMAIL_CATEGORY_KEYS
    }
    metadata = EmailModelMetadata(
        model_id=model_id,
        parent_model_id=parent_model_id,
        model_family="tfidf-logistic-regression",
        tokenizer_version="jieba-default-v1",
        feature_version=CpuTfidfLogisticClassifier.FEATURE_VERSION,
        training_dataset_version=f"feedback-sha256:{suffix}",
        trained_at=trained_at.isoformat(),
        training_started_at=(trained_at - timedelta(seconds=2)).isoformat(),
        training_finished_at=trained_at.isoformat(),
        sample_count=180,
        new_sample_count=18,
        category_counts={category: 20 for category in INITIAL_EMAIL_CATEGORY_KEYS},
        account_counts={"account-a": 180},
        validation_method="time-ordered-holdout",
        accuracy=0.96,
        per_category_metrics=metrics,
        prediction_latency_p50_ms=1.25,
        prediction_latency_p95_ms=3.5,
        artifact_sha256=digest,
        status="candidate",
        promotion_reason="candidate_validation_pending",
        failure_reason="",
    )
    registry.stage_candidate(
        source,
        metadata,
        parity_texts=tuple(texts),
        expected_labels=tuple(classifier.predict(text).label for text in texts),
    )
    return model_id


def test_email_learning_endpoint_exposes_real_lifecycle_evidence(
    tmp_path: Path,
) -> None:
    database = tmp_path / "learning-evidence.sqlite3"
    registry = EmailModelRegistry(tmp_path / "models")
    started = datetime(2026, 9, 2, 8, 0, tzinfo=timezone.utc)
    previous = _stage_learning_evidence_model(
        registry, tmp_path, trained_at=started, suffix="previous"
    )
    registry.promote(previous, reason="initial_validation_passed")
    active = _stage_learning_evidence_model(
        registry,
        tmp_path,
        trained_at=started + timedelta(seconds=1),
        suffix="active",
        parent_model_id=previous,
    )
    registry.promote(active, reason="macro_f1_and_latency_passed")
    candidate = _stage_learning_evidence_model(
        registry,
        tmp_path,
        trained_at=started + timedelta(seconds=2),
        suffix="candidate",
        parent_model_id=active,
    )
    rejected = _stage_learning_evidence_model(
        registry,
        tmp_path,
        trained_at=started + timedelta(seconds=3),
        suffix="rejected",
        parent_model_id=active,
    )
    registry.reject(rejected, reason="subscription_precision_below_0.95")
    failed = _stage_learning_evidence_model(
        registry,
        tmp_path,
        trained_at=started + timedelta(seconds=4),
        suffix="failed",
        parent_model_id=active,
    )
    registry.mark_failed(failed, reason="artifact_reload_failed")
    service = SimpleNamespace(
        registry=registry,
        retrain_state_path=tmp_path / "models" / "retrain-state.json",
    )
    app = FastAPI()
    register_email_routes(
        app,
        lambda: EmailStore(database),
        email_learning_factory=lambda: service,
    )

    response = TestClient(app).get("/api/console/email/learning")

    assert response.status_code == 200
    models = response.json()["learning"]["models"]
    by_id = {model["model_id"]: model for model in models}
    assert by_id[active]["status"] == "active"
    assert by_id[active]["candidate_reason"] == "candidate_validation_pending"
    assert by_id[active]["promotion_reason"] == "macro_f1_and_latency_passed"
    assert by_id[previous]["status"] == "previous"
    assert by_id[previous]["promotion_reason"] == "initial_validation_passed"
    assert by_id[previous]["superseded_reason"] == "superseded"
    assert by_id[candidate]["status"] == "candidate"
    assert by_id[rejected]["rejection_reason"] == "subscription_precision_below_0.95"
    assert by_id[failed]["failure_reason"] == "artifact_reload_failed"
    assert [event["status"] for event in by_id[previous]["lifecycle"]] == [
        "candidate",
        "active",
        "previous",
    ]
    assert all(model["integrity_status"] == "verified" for model in models)
    assert response.json()["learning"]["registry_issues"] == []
    serialized = json.dumps(models, sort_keys=True)
    assert "/private/" not in serialized
    assert "artifact_path" not in serialized


def test_legacy_model_observability_redacts_every_reason_field() -> None:
    private = (
        "https://private.example/path?token=secret-token OTP 129944 "
        "private body phrase browser/profile/session-reference"
    )
    metadata = {
        "model_id": "email-tfidf-lr-safe",
        "status": "failed",
        "promotion_reason": private,
        "failure_reason": private,
        "per_category_metrics": {
            "legal": {
                "precision": 0.5,
                "recall": 0.5,
                "f1": 0.5,
                "eligibility_reason": private,
                "raw_embedding": [1.0, 2.0],
            }
        },
    }
    lifecycle = tuple(
        SimpleNamespace(
            event_id=f"event-{status}",
            model_id=metadata["model_id"],
            status=status,
            occurred_at="2026-09-08T00:00:00+00:00",
            reason=private,
        )
        for status in ("candidate", "active", "rejected", "failed", "previous")
    )
    entry = SimpleNamespace(
        metadata=SimpleNamespace(to_dict=lambda: metadata),
        status="failed",
        status_reason=private,
        lifecycle=lifecycle,
        integrity_status="corrupt",
        integrity_error=private,
    )

    serialized = json.dumps(_project_legacy_model_inventory(entry), sort_keys=True)

    for fragment in (
        "private.example",
        "secret-token",
        "129944",
        "private body phrase",
        "browser/profile/session-reference",
        "raw_embedding",
    ):
        assert fragment not in serialized
    assert '"status_reason": "unavailable"' in serialized
    assert '"integrity_error": "registry_integrity_error"' in serialized
    assert "metadata_path" not in serialized


def test_email_learning_keeps_healthy_models_visible_when_history_is_corrupt(
    tmp_path: Path,
) -> None:
    database = tmp_path / "learning-corruption.sqlite3"
    registry = EmailModelRegistry(tmp_path / "models")
    started = datetime(2026, 9, 2, 8, 0, tzinfo=timezone.utc)
    healthy = _stage_learning_evidence_model(
        registry, tmp_path, trained_at=started, suffix="healthy"
    )
    corrupt = _stage_learning_evidence_model(
        registry,
        tmp_path,
        trained_at=started + timedelta(seconds=1),
        suffix="corrupt",
    )
    registry.get_model(corrupt).artifact_path.write_bytes(b"corrupt")
    service = SimpleNamespace(
        registry=registry,
        retrain_state_path=tmp_path / "models" / "retrain-state.json",
    )
    app = FastAPI()
    register_email_routes(
        app,
        lambda: EmailStore(database),
        email_learning_factory=lambda: service,
    )

    response = TestClient(app).get("/api/console/email/learning")

    assert response.status_code == 200
    by_id = {
        model["model_id"]: model for model in response.json()["learning"]["models"]
    }
    assert by_id[healthy]["integrity_status"] == "verified"
    assert by_id[corrupt]["integrity_status"] == "corrupt"
    assert response.json()["learning"]["registry_issues"] == [
        {
            "model_id": "model-inventory-evidence",
            "integrity_status": "corrupt",
            "integrity_error": "artifact_digest_mismatch",
        }
    ]


@pytest.mark.parametrize(
    "mutation",
    [
        "unknown_model",
        "missing_metadata",
        "missing_artifact",
        "manifest_path_mismatch",
        "manifest_digest_mismatch",
        "artifact_digest_mismatch",
    ],
)
def test_email_learning_rejects_semantically_invalid_active_manifest(
    tmp_path: Path,
    mutation: str,
) -> None:
    database = tmp_path / f"learning-active-{mutation}.sqlite3"
    registry = EmailModelRegistry(tmp_path / "models")
    trained_at = datetime(2026, 9, 2, 8, 0, tzinfo=timezone.utc)
    model_id = _stage_learning_evidence_model(
        registry, tmp_path, trained_at=trained_at, suffix=mutation
    )
    registry.promote(model_id, reason="validated")
    record = registry.get_model(model_id)
    manifest_path = registry.root / "active.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if mutation == "unknown_model":
        manifest["model_id"] = "email-tfidf-lr-20260903T000000Z-deadbeef"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    elif mutation == "missing_metadata":
        record.metadata_path.unlink()
    elif mutation == "missing_artifact":
        record.artifact_path.unlink()
    elif mutation == "manifest_path_mismatch":
        manifest["artifact"] = "artifacts/email-tfidf-lr-20260903T000000Z-deadbeef.pkl"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    elif mutation == "manifest_digest_mismatch":
        manifest["artifact_sha256"] = "f" * 64
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    else:
        record.artifact_path.write_bytes(b"corrupt")
    service = SimpleNamespace(
        registry=registry,
        retrain_state_path=tmp_path / "models" / "retrain-state.json",
    )
    app = FastAPI()
    register_email_routes(
        app,
        lambda: EmailStore(database),
        email_learning_factory=lambda: service,
    )

    response = TestClient(app).get("/api/console/email/learning")

    assert response.status_code == 200
    learning = response.json()["learning"]
    assert learning["active_model_id"] is None
    assert {
        "model_id": "active-manifest",
        "integrity_status": "corrupt",
        "integrity_error": "active_manifest_invalid",
    } in learning["registry_issues"]


def test_email_learning_does_not_serialize_wrong_metadata_identity(
    tmp_path: Path,
) -> None:
    database = tmp_path / "learning-identity.sqlite3"
    registry = EmailModelRegistry(tmp_path / "models")
    trained_at = datetime(2026, 9, 2, 8, 0, tzinfo=timezone.utc)
    first = _stage_learning_evidence_model(
        registry, tmp_path, trained_at=trained_at, suffix="identity-first"
    )
    second = _stage_learning_evidence_model(
        registry,
        tmp_path,
        trained_at=trained_at + timedelta(seconds=1),
        suffix="identity-second",
    )
    registry.get_model(first).metadata_path.write_bytes(
        registry.get_model(second).metadata_path.read_bytes()
    )
    service = SimpleNamespace(
        registry=registry,
        retrain_state_path=tmp_path / "models" / "retrain-state.json",
    )
    app = FastAPI()
    register_email_routes(
        app,
        lambda: EmailStore(database),
        email_learning_factory=lambda: service,
    )

    response = TestClient(app).get("/api/console/email/learning")

    assert response.status_code == 200
    learning = response.json()["learning"]
    model_ids = [model["model_id"] for model in learning["models"]]
    assert model_ids == [second]
    assert len(model_ids) == len(set(model_ids))
    assert {
        "model_id": "model-inventory-evidence",
        "integrity_status": "corrupt",
        "integrity_error": "metadata_invalid",
    } in learning["registry_issues"]


@pytest.mark.parametrize(
    "corrupt_model_id",
    (
        "https://private.example/model?token=secret",
        "847201",
        "private full body phrase",
        "private-browser-session",
    ),
)
def test_email_learning_registry_issues_never_echo_corrupt_model_id(
    tmp_path: Path, corrupt_model_id: str
) -> None:
    class Registry:
        root = tmp_path / "models"

        def active_manifest(self):
            return None

        def active_model_id_unverified(self):
            return None

        def list_model_inventory(self):
            return [
                SimpleNamespace(
                    model_id=corrupt_model_id,
                    integrity_status="corrupt",
                    integrity_error="metadata_invalid",
                    metadata=None,
                )
            ]

        def list_staged_evidence(self):
            return []

        def list_staged_evidence_inventory(self):
            return []

    class LearningStore:
        def current_model_promotion_config(self):
            return EmailStore(tmp_path / "controls.sqlite3").current_model_promotion_config()

        def list_category_configs(self):
            return []

        def list_unincluded_training_examples(self):
            return []

        def list_configs(self):
            return []

        def latest_training_snapshot_state(self):
            return None

    app = FastAPI()
    register_email_routes(
        app,
        lambda: LearningStore(),
        email_learning_factory=lambda: SimpleNamespace(
            registry=Registry(),
            retrain_state_path=tmp_path / "missing-state.json",
        ),
    )

    response = TestClient(app).get("/api/console/email/learning")

    assert response.status_code == 200
    assert corrupt_model_id not in response.text
    assert response.json()["learning"]["registry_issues"] == [
        {
            "model_id": "model-inventory-evidence",
            "integrity_status": "corrupt",
            "integrity_error": "metadata_invalid",
        }
    ]


def test_email_classification_list_and_detail_expose_persisted_text(
    tmp_path: Path,
) -> None:
    database = tmp_path / "classification-attachment-metadata.sqlite3"
    store = EmailStore(database)
    classification = EmailClassification.model_validate(
        {
            "classification_id": 73,
            "stable_message_identity": "account-1:message-id:<safe@example.com>",
            "provider_locator": {
                "account_id": "account-1",
                "folder": "INBOX",
                "uidvalidity": 4,
                "uid": 73,
                "rfc_message_id": "<safe@example.com>",
                "thread_id": "thread-73",
            },
            "category": EmailCategory.WORK,
            "confidence": 0.7,
            "margin": 0.2,
                "probabilities": {"work": 0.7, "legal": 0.3},
            "model_id": "email-model-v73",
            "config_version": "email-config-v3",
            "status": EmailClassificationStatus.PENDING_FEEDBACK,
            "classification_source": "model",
            "action_plan": None,
        }
    )
    expected_metadata = [
        {
            "filename": "quarterly-brief.pdf",
            "mime_type": "application/pdf",
            "size_bytes": 4096,
            "inline": False,
        },
        {
            "filename": "logo.png",
            "mime_type": "image/png",
            "size_bytes": 512,
            "inline": True,
        },
    ]
    store.persist_scan_result(
        classification,
        sender="sender@example.com",
        recipients=("recipient@example.com",),
        subject="Quarterly brief",
        normalized_text="From: sender@example.com\nTo: recipient@example.com\nCc: copy@example.com\nSubject: Quarterly brief\n\n正文\n\nFrom: quoted@example.com\nSubject: 转发邮件\n\n引用内容",
        preview="Metadata only",
        attachment_metadata=tuple(
            EmailAttachmentMetadata.model_validate(item) for item in expected_metadata
        ),
        received_at="2026-09-02T08:00:00+00:00",
        model_text="__subject__quarterly brief",
    )
    app = FastAPI()
    register_email_routes(app, lambda: store)
    client = TestClient(app)

    listed = client.get("/api/console/email/classifications?status=pending_feedback")
    detailed = client.get("/api/console/email/classifications/73")

    assert listed.status_code == 200
    assert detailed.status_code == 200
    assert listed.json()["items"][0]["attachment_metadata"] == expected_metadata
    assert detailed.json()["item"]["attachment_metadata"] == expected_metadata
    assert detailed.json()["item"]["message_text"] == (
        "正文\n\nFrom: quoted@example.com\nSubject: 转发邮件\n\n引用内容"
    )
    assert listed.json()["items"][0]["message_text"] == (
        "正文\n\nFrom: quoted@example.com\nSubject: 转发邮件\n\n引用内容"
    )
    assert detailed.json()["item"]["cc"] == "copy@example.com"
    assert detailed.json()["item"]["recipients"] == ["recipient@example.com"]
    for response_item in (listed.json()["items"][0], detailed.json()["item"]):
        assert all(
            set(attachment) == {"filename", "mime_type", "size_bytes", "inline"}
            for attachment in response_item["attachment_metadata"]
        )


def test_email_classification_list_all_unifies_statuses_with_persisted_body(
    tmp_path: Path,
) -> None:
    store = EmailStore(tmp_path / "classification-all.sqlite3")
    for classification_id, status, category, source in (
        (101, EmailClassificationStatus.PENDING_FEEDBACK, EmailCategory.WORK, "model"),
        (102, EmailClassificationStatus.PROCESSED, EmailCategory.LEGAL, "model"),
    ):
        action_plan = (
            build_versioned_email_action_plan(
                action_plan_version=1,
                classification_id=classification_id,
                account_id="account-1",
                category=category,
                classification_source=source,
                confidence=0.7,
                model_id="email-model-v1",
                config_version="email-config-v1",
                actions=(EmailAction.MOVE,),
                action_parameters={EmailAction.MOVE: {"target_folder": "Legal"}},
                created_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
            )
            if status is EmailClassificationStatus.PROCESSED
            else None
        )
        classification = EmailClassification.model_validate(
            {
                "classification_id": classification_id,
                "stable_message_identity": f"account-1:message-id:<{classification_id}@example.com>",
                "provider_locator": {
                    "account_id": "account-1",
                    "folder": "INBOX",
                    "uidvalidity": 1,
                    "uid": classification_id,
                    "rfc_message_id": f"<{classification_id}@example.com>",
                    "thread_id": str(classification_id),
                },
                "category": category,
                "confidence": 0.7,
                "margin": 0.2,
                "probabilities": {category.value: 0.7},
                "model_id": "email-model-v1",
                "config_version": "email-config-v1",
                "status": status,
                "classification_source": source,
                "action_plan": action_plan,
            }
        )
        store.persist_scan_result(
            classification,
            sender="sender@example.com",
            subject=f"Message {classification_id}",
            normalized_text=f"正文 {classification_id}",
            preview=f"摘要 {classification_id}",
            model_text=f"模型文本 {classification_id}",
        )

    client = TestClient(FastAPI())
    app = client.app
    register_email_routes(app, lambda: store)
    response = client.get("/api/console/email/classifications?status=all&page_size=10")

    assert response.status_code == 200
    payload = response.json()
    assert payload["meta"]["total"] == 2
    assert {item["status"] for item in payload["items"]} == {
        "pending_feedback",
        "processed",
    }
    assert {item["category"] for item in payload["items"]} == {"work", "legal"}
    assert {item["classification_source"] for item in payload["items"]} == {"model"}
    assert all(isinstance(item["id"], str) for item in payload["items"])
    assert {item["message_text"] for item in payload["items"]} == {"正文 101", "正文 102"}

    for query, expected in [("正文 101", ["101"]), ("MESSAGE 102", ["102"]), ("SENDER@EXAMPLE", ["102", "101"]), ("%", []), ("' OR 1=1 --", [])]:
        found = client.get("/api/console/email/classifications", params={"status":"all", "q":query, "page_size":1}).json()
        assert found["meta"]["total"] == len(expected)
        assert [item["id"] for item in found["items"]] == expected[:1]
    found = client.get("/api/console/email/classifications", params={"status":"pending_feedback", "q":"正文 102"}).json()
    assert found["meta"]["total"] == 0
    found = client.get("/api/console/email/classifications", params={"status":"all", "q":"sender", "page_size":1,"page":2}).json()
    assert [item["id"] for item in found["items"]] == ["101"]
    assert found["meta"]["total"] == 2


def test_email_classification_display_projects_residual_html_without_links(
    tmp_path: Path,
) -> None:
    store = EmailStore(tmp_path / "residual-html-display.sqlite3")
    classification = EmailClassification.model_validate(
        {
            "classification_id": 71,
            "stable_message_identity": "account-1:message-id:<display-71@example.com>",
            "provider_locator": {
                "account_id": "account-1", "folder": "INBOX",
                "uidvalidity": 1, "uid": 71,
                "rfc_message_id": "<display-71@example.com>", "thread_id": "display-71",
            },
            "category": EmailCategory.NOTIFICATION,
            "confidence": 0.7,
            "margin": 0.2,
            "probabilities": {"notification": 0.7},
            "model_id": "email-model-display-v1",
            "config_version": "email-config-display-v1",
            "status": EmailClassificationStatus.PENDING_FEEDBACK,
            "classification_source": "model",
            "action_plan": None,
        }
    )
    store.persist_scan_result(
        classification,
        sender="newsletter@example.com",
        subject="Display body",
        normalized_text=(
            "Content-Type: text/plain; charset=utf-8\n\n"
            '<p>First paragraph</p><p>Second paragraph</p>'
            '<a href="[UNSUBSCRIBE_CANDIDATE:1]">Unsubscribe</a>'
        ),
        preview="Display body",
        model_text="display body",
    )
    app = FastAPI()
    register_email_routes(app, lambda: store)

    response = TestClient(app).get("/api/console/email/classifications/71")

    assert response.status_code == 200
    assert response.json()["item"]["message_text"] == (
        "First paragraph\n\nSecond paragraph\n\nUnsubscribe"
    )
    assert "UNSUBSCRIBE_CANDIDATE" not in response.text
    assert "href=" not in response.text


def test_email_training_preview_returns_unique_frozen_selection_without_writes() -> None:
    class PreviewStore:
        writes: list[object] = []

        def latest_training_snapshot_state(self):
            return {
                "snapshot_id": "snapshot-preview-1",
                "snapshot_sha": "a" * 64,
                "snapshot_version": "email-folder-snapshot.v1",
                "description_version": "description-set-sha256:" + "b" * 64,
            }

        def get_training_snapshot(self, snapshot_id: str):
            assert snapshot_id == "snapshot-preview-1"
            return {
                "observations": [
                    {
                        "account_id": "account-preview",
                        "stable_message_identity": "message-1",
                        "provider_thread_id": "thread-1",
                        "normalized_model_input": '{"body":"Legal one"}',
                        "category_key": "legal",
                    },
                    {
                        "account_id": "account-preview",
                        "stable_message_identity": "message-2",
                        "provider_thread_id": "thread-2",
                        "normalized_model_input": '{"body":"Legal two"}',
                        "category_key": "legal",
                    },
                ]
            }

        def list_selected_training_records(self):
            return [
                {
                    "source": "user_feedback",
                    "account_id": "account-preview",
                    "stable_message_identity": "message-1",
                    "provider_thread_id": "thread-1",
                    "normalized_model_input": '{"body":"Legal one"}',
                    "category_key": "legal",
                },
                {
                    "source": "agent_auto_label",
                    "account_id": "account-preview",
                    "stable_message_identity": "message-2",
                    "provider_thread_id": "thread-2",
                    "normalized_model_input": '{"body":"Legal two"}',
                    "category_key": "legal",
                },
            ]

    store = PreviewStore()
    app = FastAPI()
    register_email_routes(app, lambda: store)

    response = TestClient(app).post(
        "/api/console/email/training/preview",
        json={
            "sources": ["folder_snapshot", "user_feedback", "agent_auto_label"],
            "categories": ["legal"],
        },
    )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "ok": True,
        "preview": {
            "unique_sample_count": 2,
            "snapshot_id": "snapshot-preview-1",
            "snapshot_digest": "a" * 64,
            "snapshot_version": "email-folder-snapshot.v1",
            "description_version": "description-set-sha256:" + "b" * 64,
            "training_ready": False,
            "training_blockers": ["legal:validation", "legal:test"],
        },
    }
    assert store.writes == []


def test_email_training_preview_allows_empty_valid_source_selection_without_mutation(
    tmp_path: Path,
) -> None:
    database = tmp_path / "preview-empty.sqlite3"
    store = EmailStore(database)
    snapshot = build_folder_training_snapshot(
        [
            {
                "account_id": "account-preview",
                "stable_message_identity": "account-preview:message-id:<preview@test>",
                "provider_folder_id": "folder-legal",
                "provider_folder_name": "Legal",
                "folder_role": "category",
                "bound_category_key": "legal",
                "folder_binding_status": "active",
                "processed_by_email_service": True,
                "important_signals": ImportantSignals((), False),
                "sender": {"name": "Sender", "email": "sender@example.test"},
                "to_recipients": [],
                "cc_recipients": [],
                "subject": "Preview only",
                "body": "Frozen source sample",
                "headers": {"message-id": "<preview@test>"},
                "attachments": [],
                "provider_thread_id": "thread-preview",
                "explicit_matter_group": None,
                "source": "natural",
                "received_at": "2026-09-07T12:00:00+00:00",
            }
        ],
        snapshot_id="snapshot-preview-empty",
        description_version="description-v3",
        observed_at=datetime(2026, 9, 7, 18, 0, tzinfo=timezone.utc),
        seed=17,
    )
    store.persist_training_snapshot(snapshot)
    snapshot_state = store.latest_training_snapshot_state()
    assert snapshot_state is not None
    with sqlite3.connect(database) as connection:
        before = tuple(connection.iterdump())
    app = FastAPI()
    register_email_routes(app, lambda: store)

    response = TestClient(app).post(
        "/api/console/email/training/preview",
        json={"sources": ["user_feedback"], "categories": ["legal"]},
    )

    assert response.status_code == 200, response.text
    assert response.json()["preview"] == {
        "unique_sample_count": 0,
        "snapshot_id": "snapshot-preview-empty",
        "snapshot_digest": snapshot.snapshot_digest,
        "snapshot_version": snapshot.snapshot_version,
        "description_version": snapshot_state["description_version"],
        "training_ready": False,
        "training_blockers": ["legal:train", "legal:validation", "legal:test"],
    }
    with sqlite3.connect(database) as connection:
        after = tuple(connection.iterdump())
    assert after == before


@pytest.mark.parametrize(
    "body",
    [
        {"sources": ["unknown"], "categories": ["legal"]},
        {"sources": ["folder_snapshot"], "categories": ["unknown"]},
        {"sources": ["folder_snapshot"], "categories": ["legal"]},
        {"sources": ["folder_snapshot"], "categories": ["legal"], "model_families": ["embedding-mlp"]},
    ],
)
def test_email_training_preview_rejects_invalid_selection_without_writes(body) -> None:
    class MissingSnapshotStore:
        writes: list[object] = []

        def latest_training_snapshot_state(self):
            return None

    store = MissingSnapshotStore()
    app = FastAPI()
    register_email_routes(app, lambda: store)

    response = TestClient(app).post("/api/console/email/training/preview", json=body)

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_training_selection"
    assert store.writes == []


def test_email_training_preview_rejects_malformed_body() -> None:
    app = FastAPI()
    register_email_routes(app, lambda: object())

    response = TestClient(app).post(
        "/api/console/email/training/preview",
        content=b"{not-json",
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_training_selection"


def test_display_text_removes_truncated_internal_unsubscribe_markup() -> None:
    displayed = visible_email_text(
        "OpenAI © 2015–2026 < a href=\"[UNSUBSCRIBE_CANDIDATE:1]\" "
        "target=\"_blank\" style=\"display:inline-bloc"
    )

    assert displayed == "OpenAI © 2015–2026"


def test_display_text_preserves_content_after_complete_internal_markup() -> None:
    displayed = visible_email_text(
        "Intro < a href=\"[UNSUBSCRIBE_CANDIDATE:1]\"> trailing real content"
    )

    assert displayed == (
        "Intro < a href=\"[UNSUBSCRIBE_CANDIDATE:1]\"> trailing real content"
    )


def test_display_text_preserves_ordinary_angle_bracket_text() -> None:
    displayed = visible_email_text(
        "Reply to <support@example.com> before 10:00 < 12:00."
    )

    assert displayed == "Reply to <support@example.com> before 10:00 < 12:00."


def test_email_classification_detail_projects_observability(tmp_path: Path) -> None:
    classification = {
        "id": 41,
        "account_id": "account-1",
        "stable_message_identity": "message-1",
        "status": "processed",
        "category": "subscription",
    }

    class DetailStore:
        def get_classification(self, classification_id: int):
            return classification if classification_id == 41 else None

        def list_email_classification_observability(self, classification_id: int):
            assert classification_id == 41
            return [
                {
                    "kind": "unsubscribe",
                    "operation": "unsubscribe",
                    "status": "done",
                    "receipt_id": "receipt-41",
                    "result_text": "退订成功",
                }
            ]

        def get_provider_classification_state(self, classification_id: int):
            assert classification_id == 41
            return {
                "state": "categorized",
                "category_key": "legal",
                "important": True,
                "provider_folder_id": "folder-legal",
                "provider_folder_name": "Legal",
                "observed_at": "2026-09-08T08:00:00+00:00",
            }

        def get_email_unsubscribe_entry_url(self, classification_id: int):
            assert classification_id == 41
            return None

    app = FastAPI()
    register_email_routes(app, lambda: DetailStore())

    response = TestClient(app).get("/api/console/email/classifications/41")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["item"] == {**classification, "id": str(classification["id"])}
    assert payload["unsubscribe_entry"] == {
        "available": False,
        "reason": "entry_unavailable",
    }
    assert payload["observability"] == [
        {
            "kind": "unsubscribe",
            "operation": "unsubscribe",
            "status": "done",
            "receipt_id": "receipt-41",
            "result_text": "退订成功",
        }
    ]
    assert payload["provider_classification"] == {
        "state": "categorized",
        "category_key": "legal",
        "important": True,
        "provider_folder_id": "folder-legal",
        "provider_folder_name": "Legal",
        "observed_at": "2026-09-08T08:00:00+00:00",
    }
    assert payload["meta"]["snapshot_at"]

    missing = TestClient(app).get("/api/console/email/classifications/42")
    assert missing.status_code == 404
    assert missing.json()["code"] == "not_found"


def test_email_folder_bindings_endpoint_is_read_only_and_provider_aware() -> None:
    class BindingStore:
        def list_category_configs(self):
            return [
                {
                    "category_key": "legal",
                    "display_name": "法务",
                    "enabled": True,
                    "description_version": "legal-v3",
                }
            ]

        def list_account_folder_bindings(self):
            return [
                {
                    "account_id": "account-1",
                    "category_key": "legal",
                    "provider_folder_id": "folder-legal",
                    "provider_folder_name": "Legal",
                    "binding_status": "active",
                    "last_verified_at": "2026-09-08T08:00:00+00:00",
                },
                {
                    "account_id": "account-2",
                    "category_key": "legal",
                    "provider_folder_id": "folder-legal-2",
                    "provider_folder_name": "Legal",
                    "binding_status": "unavailable",
                    "last_verified_at": "2026-09-08T08:01:00+00:00",
                },
            ]

    app = FastAPI()
    register_email_routes(app, lambda: BindingStore())

    response = TestClient(app).get("/api/console/email/folder-bindings")

    assert response.status_code == 200
    assert response.json()["items"] == [
        {
            "category_key": "legal",
            "display_name": "法务",
            "enabled": True,
            "description_version": "legal-v3",
            "accounts": [
                {
                    "account_id": "account-1",
                    "provider_folder_id": "folder-legal",
                    "provider_folder_name": "Legal",
                    "binding_status": "active",
                    "last_verified_at": "2026-09-08T08:00:00+00:00",
                },
                {
                    "account_id": "account-2",
                    "provider_folder_id": "folder-legal-2",
                    "provider_folder_name": "Legal",
                    "binding_status": "unavailable",
                    "last_verified_at": "2026-09-08T08:01:00+00:00",
                },
            ],
        }
    ]


def test_email_learning_and_model_version_expose_safe_modern_evidence(tmp_path) -> None:
    secret_markers = {
        "unsubscribe_url": "https://private.example/unsubscribe?token=secret",
        "otp": "847201",
        "raw_embedding": [0.1, 0.2],
        "body": "private full body",
        "browser_session": "private-browser-session",
    }
    evidence = {
        "model_id": _CANONICAL_EMBEDDING_MODEL_SECOND,
        "source_snapshot_id": "email-folder-snapshot-20260908T080100.000000Z-aaaaaaaaaaaa",
        "source_snapshot_digest": "a" * 64,
        "source_snapshot_observed_at": "2026-09-08T08:01:00+00:00",
        "folder_label_watermark": 81,
        "important_label_watermark": 41,
        "status": "candidate",
        "trained_at": "2026-09-08T08:02:00+00:00",
        "compatibility": {
            "enabled_categories": ["legal", "financing"],
            "description_version": "description-set-sha256:" + "d" * 64,
            "input_schema_version": "email-folder-model-input-v2",
            "embedding_model_id": DEFAULT_EMBEDDING_MODEL_ID,
            "embedding_revision": "release/2026-09-08+gpu4",
            "head_format": "description-mlp-v1",
            "parent_model_id": _CANONICAL_EMBEDDING_MODEL_FIRST,
            "private_browser_data": "nested-private-browser",
        },
        "split_counts": {
            "train": 240,
            "validation": 40,
            "test": 40,
            "test_evaluations": 1,
            "important": {"train": 240, "validation": 40, "test": 40},
            "private_browser_data": "nested-split-private",
        },
        "metrics": {
            "categories": {
                "legal": {
                    "accepted_precision": 0.97,
                    "accepted_hits": 25,
                    "independent_groups": 12,
                },
                "financing": {
                    "accepted_precision": 0.96,
                    "accepted_hits": 23,
                    "independent_groups": 11,
                },
            },
            "important": {
                "accepted_precision": 0.98,
                "accepted_hits": 24,
                "independent_groups": 12,
            },
        },
        "historical_eligibility": {
            "categories": {
                "legal": {
                    "precision": 0.97,
                    "accepted_hits": 25,
                    "independent_groups": 12,
                    "eligible": True,
                },
                "financing": {
                    "precision": 0.96,
                    "accepted_hits": 23,
                    "independent_groups": 11,
                    "eligible": True,
                },
            },
            "important": {
                "precision": 0.98,
                "accepted_hits": 24,
                "independent_groups": 12,
                "eligible": True,
            },
            "raw_embedding": [9.0, 8.0],
        },
        "unresolved_historical_systematic_error": False,
        "whole_model_readiness": {
            "ready": True,
            "passing_model_ids": [
                _CANONICAL_EMBEDDING_MODEL_FIRST,
                _CANONICAL_EMBEDDING_MODEL_SECOND,
            ],
            "reason": (
                "https://private.example/status?token=reason-secret "
                "OTP 129944 body phrase browser/profile/reference"
            ),
            "otp": "129944",
        },
        "latency_ms": {"p50": 8.0, "p95": 42.0, "p99": 70.0},
        "hashes": {"snapshot_sha256": "a" * 64, "artifact_sha256": "b" * 64},
        "failure_reason": (
            "https://private.example/failure?token=reason-secret "
            "OTP 847201 body phrase browser/profile/reference"
        ),
        **secret_markers,
    }

    class Registry:
        root = tmp_path / "models"
        embedding_artifacts = root / "embedding-artifacts"
        staged_evidence = root / "staged-evidence"

        def active_manifest(self):
            return SimpleNamespace(model_id=_CANONICAL_EMBEDDING_MODEL_SECOND)

        def active_model_id_unverified(self):
            return _CANONICAL_EMBEDDING_MODEL_SECOND

        def list_model_inventory(self):
            return []

        def list_staged_evidence(self):
            first = json.loads(json.dumps(evidence))
            first["model_id"] = _CANONICAL_EMBEDDING_MODEL_FIRST
            first["source_snapshot_id"] = (
                "email-folder-snapshot-20260908T080000.000000Z-cccccccccccc"
            )
            first["source_snapshot_digest"] = "c" * 64
            first["source_snapshot_observed_at"] = "2026-09-08T08:00:00+00:00"
            first["folder_label_watermark"] = 80
            first["important_label_watermark"] = 40
            for metrics in first["metrics"]["categories"].values():
                metrics["accepted_hits"] -= 1
                metrics["independent_groups"] -= 1
            first["metrics"]["important"]["accepted_hits"] -= 1
            first["metrics"]["important"]["independent_groups"] -= 1
            first["whole_model_readiness"] = {
                "ready": False,
                "passing_model_ids": [],
                "reason": "two_consecutive_candidates_required",
            }
            return [first, evidence]

        def get_staged_evidence(self, model_id):
            if model_id != evidence["model_id"]:
                raise ValueError("unknown")
            return evidence

        def list_staged_evidence_inventory(self):
            return [{"model_id": row["model_id"], "evidence": row, "integrity_status": "readable"}
                    for row in self.list_staged_evidence()]

    class LearningStore:
        def current_model_promotion_config(self):
            return EmailStore(tmp_path / "controls.sqlite3").current_model_promotion_config()

        def list_category_configs(self):
            return []

        def list_unincluded_training_examples(self):
            return []

        def list_configs(self):
            return []

        def latest_training_snapshot_state(self):
            return {
                "snapshot_id": "snapshot-2",
                "snapshot_version": "email-folder-snapshot.v1",
                "description_version": "description-set-sha256:abc",
                "sample_count": 320,
                "group_count": 80,
                "category_sample_counts": {"legal": 40, "financing": 35},
                "category_group_counts": {"legal": 15, "financing": 14},
            }

        def classifier_runtime_observability(self, *, model_id):
            assert model_id == _CANONICAL_EMBEDDING_MODEL_SECOND
            return {
                "timing": runtime.latency.summary(),
                "fallback_counts": {
                    **runtime.latency.fallback_counts(),
                    "token_secret_browser_profile_129944": 2,
                },
            }

    runtime = SimpleNamespace(
        mode=EmailClassifierRuntimeMode.MODEL_PRIMARY,
        latency=SimpleNamespace(
            summary=lambda: {
                "warm_success_cache": {
                    "sample_count": 30,
                    "stages": {"total": {"p50": 5.0, "p95": 11.0, "p99": 18.0}},
                },
                "warm_success_remote": {
                    "sample_count": 30,
                    "stages": {"total": {"p50": 80.0, "p95": 340.0, "p99": 410.0}},
                },
                "slo_status": "compliant",
                "raw_embedding": [7.0, 6.0],
            },
            fallback_counts=lambda: {"embedding_timeout": 3},
        ),
    )
    registry = Registry()
    registry.embedding_artifacts.mkdir(parents=True)
    registry.staged_evidence.mkdir(parents=True)
    artifact = b"safe-test-artifact"
    artifact_digest = sha256(artifact).hexdigest()
    (registry.embedding_artifacts / f"{_CANONICAL_EMBEDDING_MODEL_SECOND}.artifact").write_bytes(
        artifact
    )
    evidence["hashes"]["artifact_sha256"] = artifact_digest
    (registry.staged_evidence / f"{_CANONICAL_EMBEDDING_MODEL_SECOND}.json").write_text(
        json.dumps(evidence), encoding="utf-8"
    )
    from app.email_classifier_runtime import switch_online_model
    registry._locked = EmailModelRegistry(registry.root)._locked
    switch_online_model(
        registry, mode="model_primary", model_id=_CANONICAL_EMBEDDING_MODEL_SECOND,
        expected_mode="agent_primary", expected_model_id=None, request_id="fixture-activation",
        actor="test", validate_promotion=lambda: "gate-v1",
        classifier_loader=lambda _: SimpleNamespace(
            **{key: evidence["compatibility"][key] for key in (
                "enabled_categories", "input_schema_version", "embedding_model_id", "embedding_revision",
            )}
        ),
    )
    service = SimpleNamespace(
        registry=registry,
        retrain_state_path=Path("/nonexistent/retrain-state.json"),
    )
    app = FastAPI()
    register_email_routes(
        app,
        lambda: LearningStore(),
        email_learning_factory=lambda: service,
    )
    client = TestClient(app)

    learning = client.get("/api/console/email/learning")
    detail = client.get(
        f"/api/console/email/model-versions/{_CANONICAL_EMBEDDING_MODEL_SECOND}"
    )

    assert learning.status_code == 200
    payload = learning.json()["learning"]
    assert payload["active_mode"] == "model_primary"
    assert payload["active_model_id"] == _CANONICAL_EMBEDDING_MODEL_SECOND
    assert payload["training_snapshot"]["sample_count"] == 320
    assert payload["historical_eligibility"]["categories"]["legal"]["eligible"] is True
    assert payload["promotion_evidence"]["passing_model_ids"] == [
        _CANONICAL_EMBEDDING_MODEL_FIRST,
        _CANONICAL_EMBEDDING_MODEL_SECOND,
    ]
    assert payload["runtime_timing"]["warm_success_cache"]["stages"]["total"]["p95"] == 11.0
    assert payload["fallback_counts"] == {
        "embedding_timeout": 3,
        "runtime_failure": 2,
    }
    assert detail.status_code == 200
    model = detail.json()["model"]
    assert model["model_id"] == _CANONICAL_EMBEDDING_MODEL_SECOND
    assert model["training_snapshot_id"] == (
        "email-folder-snapshot-20260908T080100.000000Z-aaaaaaaaaaaa"
    )
    assert model["training_snapshot_digest"] == "a" * 64
    assert model["historical_eligibility"]["categories"]["legal"]["eligible"] is True
    serialized = json.dumps({"learning": learning.json(), "detail": detail.json()})
    for private in secret_markers.values():
        assert json.dumps(private) not in serialized
    assert "unsubscribe_url" not in serialized
    assert "raw_embedding" not in serialized
    assert "browser_session" not in serialized
    assert "nested-private-browser" not in serialized
    assert "nested-split-private" not in serialized
    assert "129944" not in serialized
    assert "[9.0, 8.0]" not in serialized
    assert "[7.0, 6.0]" not in serialized
    assert "reason-secret" not in serialized
    assert "body phrase" not in serialized
    assert "token_secret_browser_profile_129944" not in serialized


@pytest.mark.parametrize(
    "contents",
    (
        "{malformed-json",
        json.dumps({"model_id": "email-embedding-mlp-corrupt"}),
        json.dumps({"model_id": 42, "status": ["wrong"]}),
    ),
)
def test_email_model_version_distinguishes_missing_from_corrupt_evidence(
    tmp_path: Path, contents: str
) -> None:
    registry = EmailModelRegistry(tmp_path / "models")
    model_id = "email-embedding-mlp-corrupt"
    (registry.staged_evidence / f"{model_id}.json").write_text(
        contents, encoding="utf-8"
    )
    service = SimpleNamespace(
        registry=registry,
        retrain_state_path=tmp_path / "models" / "retrain-state.json",
    )
    app = FastAPI()
    register_email_routes(
        app,
        lambda: EmailStore(tmp_path / "email.sqlite3"),
        email_learning_factory=lambda: service,
    )
    client = TestClient(app)

    corrupt = client.get(f"/api/console/email/model-versions/{model_id}")
    missing = client.get(
        "/api/console/email/model-versions/email-embedding-mlp-missing"
    )
    traversal = client.get(
        "/api/console/email/model-versions/email-embedding-mlp-%2E%2E%2Fsecret"
    )
    backslash_traversal = client.get(
        "/api/console/email/model-versions/email-embedding-mlp-%5Csecret"
    )

    assert corrupt.status_code == 409
    assert corrupt.json()["code"] == "email_model_integrity_error"
    assert "malformed" not in corrupt.text
    assert missing.status_code == 404
    assert traversal.status_code in {400, 404}
    assert "secret" not in traversal.text
    assert backslash_traversal.status_code == 400
    assert backslash_traversal.json()["code"] == "invalid_email_model_id"
    assert "secret" not in backslash_traversal.text


def test_model_training_metrics_preserve_missing_values_and_comparability():
    from app.web_api.email import _project_staged_model_evidence
    evidence = _valid_model_detail_evidence()
    old = _project_staged_model_evidence(evidence)
    evidence["compatibility"]["input_schema_version"] = "email-folder-model-input-v3"
    current_schema = _project_staged_model_evidence(evidence)
    assert current_schema["compatibility"]["input_schema_version"] == "email-folder-model-input-v3"
    evidence["compatibility"]["input_schema_version"] = "email-folder-model-input-v2"
    assert old["metrics"]["micro_f1"] is None
    assert old["end_to_end_latency_ms"] is None
    evidence["metrics"]["categories"]["legal"].update(precision=.97, recall=.95, f1=.96, support=25)
    evidence["metrics"].update(accuracy=.97)
    evidence["evaluation"] = {"protocol": "email-folder-heldout-v1", "test_digest": "c" * 64}
    current = _project_staged_model_evidence(evidence)
    assert current["metrics"]["micro_f1"] == .95
    assert current["metrics"]["categories"]["legal"]["support"] == 25
    assert current["evaluation"]["comparability_key"]
    assert current["head_timing_percentiles_ms"]["p95"] == 20
    assert current["end_to_end_latency_ms"] is None


def _training_metadata():
    return {
        "started_at": "2026-09-08T08:00:00+00:00",
        "completed_at": "2026-09-08T08:01:00+00:00", "duration_ms": 60000.,
        "sample_count": 80, "category_sample_count": 70, "account_count": 2,
        "group_count": 35,
    }


def _training_parameters():
    return {"alpha": .7, "beta": .3, "category_thresholds": {"legal": .95},
            "important_threshold": .96, "head_format": "description-mlp-v1",
            "hidden_layer_sizes": [8], "solver": "lbfgs", "regularization_alpha": .001,
            "max_iter": 1000, "random_seed": 20260905}


def test_training_metadata_and_parameters_projection_is_explicit_and_private_rows_never_escape():
    from app.web_api.email import _project_staged_model_evidence
    from test_email_promotion_gate import measured_latency
    evidence = _valid_model_detail_evidence()
    missing = _project_staged_model_evidence(evidence)
    assert missing["training"] is None
    assert missing["parameters"] is None
    evidence.update(training={**_training_metadata(), "raw_rows": ["private-training-row"]},
                    parameters={**_training_parameters(), "api_key": "private-key"},
                    end_to_end_latency_ms={**measured_latency(), "raw_rows": ["private-timing-row"]},
                    classification_conflicts=["private-conflict-row"])
    projected = _project_staged_model_evidence(evidence)
    assert projected["training"] == _training_metadata()
    assert projected["parameters"] == _training_parameters()
    assert projected["end_to_end_latency_ms"] == measured_latency()
    assert "private-" not in json.dumps(projected)


@pytest.mark.parametrize("container,field,value", [
    ("training", "duration_ms", float("inf")), ("training", "sample_count", True),
    ("training", "account_count", -1), ("training", "started_at", "private-time"),
    ("training", "completed_at", "2026-09-08T07:00:00+00:00"),
    ("parameters", "solver", "private-path"), ("parameters", "hidden_layer_sizes", "private-layers"),
    ("parameters", "category_thresholds", []), ("parameters", "max_iter", True),
    ("parameters", "alpha", float("nan")),
])
def test_training_projection_rejects_invalid_metadata(container, field, value):
    from app.web_api.email import _project_staged_model_evidence
    evidence = _valid_model_detail_evidence()
    evidence.update(training=_training_metadata(), parameters=_training_parameters())
    evidence[container][field] = value
    with pytest.raises(ValueError):
        _project_staged_model_evidence(evidence)


@pytest.mark.parametrize("snapshot_id", (
    "email-folder-snapshot-20260908T080000.000000Z-abcdef123456",
    "email-selected-training-20260915T204910.123456Z-84b3983b9a28",
))
def test_staged_evidence_accepts_every_training_snapshot_source(snapshot_id):
    from app.web_api.email import _project_staged_model_evidence
    evidence = _valid_model_detail_evidence()
    evidence.update(
        training=_training_metadata(),
        parameters=_training_parameters(),
        source_snapshot_id=snapshot_id,
    )
    projected = _project_staged_model_evidence(evidence)
    assert projected["training_snapshot_id"] == snapshot_id


def _valid_model_detail_evidence() -> dict[str, object]:
    metric = {
        "accepted_precision": 0.97,
        "accepted_hits": 25,
        "independent_groups": 12,
    }
    historical = {
        "precision": 0.97,
        "accepted_hits": 25,
        "independent_groups": 12,
        "eligible": True,
    }
    return {
        "model_id": _CANONICAL_EMBEDDING_MODEL_SECOND,
        "source_snapshot_id": "email-folder-snapshot-20260908T080000.000000Z-abcdef123456",
        "source_snapshot_digest": "a" * 64,
        "source_snapshot_observed_at": "2026-09-08T08:00:00+00:00",
        "folder_label_watermark": 25,
        "important_label_watermark": 20,
        "status": "candidate",
        "trained_at": "2026-09-08T08:01:00+00:00",
        "compatibility": {
            "enabled_categories": ["legal"],
            "description_version": "description-set-sha256:" + "d" * 64,
            "input_schema_version": "email-folder-model-input-v2",
            "embedding_model_id": "jina-small",
            "embedding_revision": "gpu4-r17",
            "head_format": "description-mlp-v1",
            "parent_model_id": _CANONICAL_EMBEDDING_MODEL_FIRST,
        },
        "split_counts": {
            "train": 30,
            "validation": 20,
            "test": 20,
            "test_evaluations": 1,
            "important": {"train": 30, "validation": 20, "test": 20},
        },
        "metrics": {"categories": {"legal": metric}, "important": metric},
        "historical_eligibility": {
            "categories": {"legal": historical},
            "important": historical,
        },
        "unresolved_historical_systematic_error": False,
        "whole_model_readiness": {
            "ready": True,
            "passing_model_ids": [
                _CANONICAL_EMBEDDING_MODEL_FIRST,
                _CANONICAL_EMBEDDING_MODEL_SECOND,
            ],
            "reason": "two_consecutive_compatible_candidates_passed",
        },
        "latency_ms": {"p50": 10.0, "p95": 20.0, "p99": 30.0},
        "hashes": {"snapshot_sha256": "a" * 64, "artifact_sha256": "b" * 64},
        "failure_reason": "",
    }


def test_model_detail_accepts_canonical_production_model_and_opaque_external_refs(
    tmp_path: Path,
) -> None:
    evidence = _valid_model_detail_evidence()
    artifact_digest = "e" * 64
    model_id = build_embedding_model_id(
        trained_at=datetime(2026, 9, 8, 8, 1, tzinfo=timezone.utc),
        artifact_sha256=artifact_digest,
    )
    evidence["model_id"] = model_id
    evidence["compatibility"]["parent_model_id"] = None  # type: ignore[index]
    evidence["whole_model_readiness"]["passing_model_ids"] = [model_id]  # type: ignore[index]
    evidence["compatibility"]["embedding_model_id"] = DEFAULT_EMBEDDING_MODEL_ID  # type: ignore[index]
    evidence["compatibility"]["embedding_revision"] = "release/2026-09-08+gpu4"  # type: ignore[index]

    class Registry:
        root = tmp_path / "models"
        staged_evidence = root / "staged-evidence"

        def get_staged_evidence(self, requested_model_id):
            assert requested_model_id == model_id
            return evidence

    registry = Registry()
    registry.staged_evidence.mkdir(parents=True)
    (registry.staged_evidence / f"{model_id}.json").write_text("{}")
    app = FastAPI()
    register_email_routes(
        app,
        lambda: EmailStore(tmp_path / "email.sqlite3"),
        email_learning_factory=lambda: SimpleNamespace(registry=registry),
    )

    response = TestClient(app).get(f"/api/console/email/model-versions/{model_id}")

    assert response.status_code == 200
    compatibility = response.json()["model"]["compatibility"]
    assert compatibility["embedding_model_reference"] == "configured-external-model"
    assert compatibility["embedding_revision_reference"] == (
        "configured-external-revision"
    )
    assert DEFAULT_EMBEDDING_MODEL_ID not in response.text
    assert "release/2026-09-08+gpu4" not in response.text


def test_external_evidence_references_are_fixed_and_not_otp_derived(tmp_path: Path) -> None:
    def project(model_value: str, revision_value: str) -> dict[str, object]:
        evidence = _valid_model_detail_evidence()
        evidence["compatibility"]["embedding_model_id"] = model_value  # type: ignore[index]
        evidence["compatibility"]["embedding_revision"] = revision_value  # type: ignore[index]
        model_id = str(evidence["model_id"])

        class Registry:
            root = tmp_path / model_value
            staged_evidence = root / "staged-evidence"

            def get_staged_evidence(self, _requested_model_id):
                return evidence

        registry = Registry()
        registry.staged_evidence.mkdir(parents=True)
        (registry.staged_evidence / f"{model_id}.json").write_text("{}")
        app = FastAPI()
        register_email_routes(
            app,
            lambda: EmailStore(tmp_path / f"{model_value}.sqlite3"),
            email_learning_factory=lambda: SimpleNamespace(registry=registry),
        )
        response = TestClient(app).get(
            f"/api/console/email/model-versions/{model_id}"
        )
        assert response.status_code == 200
        assert model_value not in response.text
        assert revision_value not in response.text
        return response.json()["model"]["compatibility"]

    first = project("847201", "129944")
    second = project("731908", "650217")

    assert first["embedding_model_reference"] == second["embedding_model_reference"]
    assert first["embedding_revision_reference"] == second[
        "embedding_revision_reference"
    ]
    assert first["embedding_model_reference"] == "configured-external-model"
    assert first["embedding_revision_reference"] == "configured-external-revision"


@pytest.mark.parametrize(
    "path",
    (
        ("model_id",),
        ("source_snapshot_id",),
        ("source_snapshot_observed_at",),
        ("trained_at",),
        ("status",),
        ("compatibility", "description_version"),
        ("compatibility", "input_schema_version"),
        ("compatibility", "embedding_model_id"),
        ("compatibility", "embedding_revision"),
        ("compatibility", "head_format"),
        ("compatibility", "parent_model_id"),
        ("compatibility", "enabled_categories"),
        ("whole_model_readiness", "passing_model_ids"),
    ),
)
def test_model_detail_rejects_or_redacts_private_values_in_projected_strings(
    tmp_path: Path, path: tuple[str, ...]
) -> None:
    private = (
        "https://private.example/unsubscribe?token=secret OTP 847201 "
        "full body browser/profile/session-reference"
    )
    evidence = _valid_model_detail_evidence()
    target = evidence
    for key in path[:-1]:
        target = target[key]  # type: ignore[index,assignment]
    target[path[-1]] = [private] if path[-1].endswith("categories") or path[-1].endswith("ids") else private  # type: ignore[index]
    model_id = _CANONICAL_EMBEDDING_MODEL_SECOND

    class Registry:
        root = tmp_path / "models"
        staged_evidence = root / "staged-evidence"

        def get_staged_evidence(self, requested_model_id):
            assert requested_model_id == model_id
            return evidence

    registry = Registry()
    registry.staged_evidence.mkdir(parents=True)
    (registry.staged_evidence / f"{model_id}.json").write_text("{}")
    app = FastAPI()
    register_email_routes(
        app,
        lambda: EmailStore(tmp_path / "email.sqlite3"),
        email_learning_factory=lambda: SimpleNamespace(registry=registry),
    )

    response = TestClient(app).get(f"/api/console/email/model-versions/{model_id}")

    if path in {
        ("compatibility", "embedding_model_id"),
        ("compatibility", "embedding_revision"),
    }:
        assert response.status_code == 200
    else:
        assert response.status_code == 409
        assert response.json()["code"] == "email_model_integrity_error"
    assert "private.example" not in response.text
    assert "847201" not in response.text
    assert "full body" not in response.text
    assert "browser/profile" not in response.text


@pytest.mark.parametrize("private", ("847201", "private-browser-session", "token_abcdEFGH1234567890"))
@pytest.mark.parametrize(
    "path",
    (
        ("model_id",),
        ("source_snapshot_id",),
        ("compatibility", "description_version"),
        ("compatibility", "input_schema_version"),
        ("compatibility", "embedding_model_id"),
        ("compatibility", "embedding_revision"),
        ("compatibility", "head_format"),
        ("compatibility", "parent_model_id"),
        ("compatibility", "enabled_categories"),
        ("whole_model_readiness", "passing_model_ids"),
    ),
)
def test_model_detail_rejects_or_redacts_valid_syntax_secret_identifiers(
    tmp_path: Path, path: tuple[str, ...], private: str
) -> None:
    evidence = _valid_model_detail_evidence()
    target = evidence
    for key in path[:-1]:
        target = target[key]  # type: ignore[index,assignment]
    target[path[-1]] = [private] if path[-1].endswith("categories") or path[-1].endswith("ids") else private  # type: ignore[index]
    requested = _CANONICAL_EMBEDDING_MODEL_SECOND

    class Registry:
        root = tmp_path / "models"
        staged_evidence = root / "staged-evidence"

        def get_staged_evidence(self, _model_id):
            return evidence

    registry = Registry()
    registry.staged_evidence.mkdir(parents=True)
    (registry.staged_evidence / f"{requested}.json").write_text("{}")
    app = FastAPI()
    register_email_routes(
        app,
        lambda: EmailStore(tmp_path / "email.sqlite3"),
        email_learning_factory=lambda: SimpleNamespace(registry=registry),
    )
    response = TestClient(app).get(f"/api/console/email/model-versions/{requested}")

    if path in {
        ("compatibility", "embedding_model_id"),
        ("compatibility", "embedding_revision"),
    }:
        assert response.status_code == 200
    else:
        assert response.status_code == 409
    assert private not in response.text


@pytest.mark.parametrize(
    "mutation",
    (
        "missing_category_metrics",
        "extreme_numeric_value",
        "wrong_nested_structure",
    ),
)
def test_model_detail_rejects_malformed_nested_evidence(
    tmp_path: Path, mutation: str
) -> None:
    evidence = _valid_model_detail_evidence()
    if mutation == "missing_category_metrics":
        del evidence["metrics"]["categories"]["legal"]  # type: ignore[index]
    elif mutation == "extreme_numeric_value":
        evidence["latency_ms"]["p95"] = float("inf")  # type: ignore[index]
    else:
        evidence["historical_eligibility"] = ["wrong"]
    model_id = str(evidence["model_id"])

    class Registry:
        root = tmp_path / "models"
        staged_evidence = root / "staged-evidence"

        def get_staged_evidence(self, _model_id):
            return evidence

    registry = Registry()
    registry.staged_evidence.mkdir(parents=True)
    (registry.staged_evidence / f"{model_id}.json").write_text("{}")
    app = FastAPI()
    register_email_routes(
        app,
        lambda: EmailStore(tmp_path / "email.sqlite3"),
        email_learning_factory=lambda: SimpleNamespace(registry=registry),
    )

    response = TestClient(app).get(f"/api/console/email/model-versions/{model_id}")

    assert response.status_code == 409
    assert response.json()["code"] == "email_model_integrity_error"


def _audited_email_detail_fixture(
    tmp_path: Path,
) -> SimpleNamespace:
    database = tmp_path / "audited-email-detail.sqlite3"
    email_store = EmailStore(database)
    task_store = AutoReplyStore(database)
    account_id = "account-observability"
    stable_message_identity = (
        "account-observability:message-id:<newsletter-41@example.com>"
    )
    thread_identity = "thread-observability-41"
    classification_id = 41
    plan = build_versioned_email_action_plan(
        action_plan_version=1,
        classification_id=classification_id,
        account_id=account_id,
        category=EmailCategory.NOTIFICATION,
        classification_source="user",
        confidence=1.0,
        model_id="email-model:observability-v1",
        config_version="email-config:observability-v1",
        actions=(EmailAction.UNSUBSCRIBE,),
        action_parameters={},
        created_at=datetime(2026, 9, 2, 8, 0, tzinfo=timezone.utc),
    )
    email_store.create_account(
        {
            "account_id": account_id,
            "display_name": "Observability",
            "email_address": "derek@example.com",
            "imap_host": "imap.example.com",
            "imap_port": 993,
            "imap_tls": True,
            "imap_username": "derek@example.com",
            "imap_secret_reference": "keychain://imap-observability",
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
    email_store.upsert_classification(
        EmailClassification.model_validate(
            {
                "classification_id": classification_id,
                "stable_message_identity": stable_message_identity,
                "provider_locator": {
                    "account_id": account_id,
                    "folder": "INBOX",
                    "uidvalidity": 42,
                    "uid": 41,
                    "rfc_message_id": "<newsletter-41@example.com>",
                    "thread_id": thread_identity,
                },
                "category": EmailCategory.NOTIFICATION,
                "confidence": 1.0,
                "margin": 1.0,
                "probabilities": {"notification": 1.0},
                "model_id": plan.model_id,
                "config_version": plan.config_version,
                "status": EmailClassificationStatus.PROCESSED,
                "classification_source": "user",
                "action_plan": plan,
            }
        ),
        sender="newsletter@example.com",
        subject="Newsletter",
        preview="Weekly update",
        model_text="__subject__newsletter",
        received_at="2026-09-02T08:00:00+00:00",
    )
    action_identity = email_action_identity(
        account_id=account_id,
        stable_message_identity=stable_message_identity,
        action_type=EmailAction.UNSUBSCRIBE,
        action_plan_version=plan.action_plan_version,
    )
    private_markers = {
        "private_url": "https://news.example.com/unsubscribe?token=secret-query",
        "provider_locator": {"folder": "INBOX", "uid": 41},
        "browser_profile_path": "/private/email-browser-profile",
        "cookie": "session=secret-cookie",
        "credential": "secret-credential",
        "query_token": "secret-query",
        "raw_tool_transcript": "private transcript",
    }
    task_payload = {
        "schema": "email_agent_action.v1",
        "lifecycle_version": "email_unsubscribe_audited_v2",
        "action_type": "unsubscribe",
        "action_identity": action_identity,
        "action_plan_id": plan.action_plan_id,
        "action_plan_version": plan.action_plan_version,
        "classification_id": classification_id,
        "account_id": account_id,
        "stable_message_identity": stable_message_identity,
        "thread_identity": thread_identity,
        **private_markers,
    }
    task = task_store.ensure_reply_task(
        channel="email",
        conversation_id=email_conversation_id(account_id, thread_identity),
        conversation_title="Email unsubscribe",
        single_chat=False,
        trigger_message_id=action_identity,
        trigger_create_time="2026-09-02T08:00:00+00:00",
        trigger_sender="newsletter@example.com",
        trigger_text="Immutable ActionPlan authorizes unsubscribe.",
        trigger_message_json=json.dumps(task_payload, sort_keys=True),
        execution_generation="generation-observability-1",
    )
    task = task_store.claim_reply_task(task.id)
    assert task is not None
    attempt_id = task_store.record_reply_attempt(
        conversation_id=task.conversation_id,
        conversation_title=task.conversation_title,
        trigger_message_id=action_identity,
        trigger_sender="newsletter@example.com",
        trigger_text="Immutable ActionPlan authorizes unsubscribe.",
        action="unsubscribe",
        sensitivity_kind="email",
        send_status="done",
        channel="email",
    )
    consumer = task_store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=None,
        operation_id="",
        owner="consumer-observability-owner",
    ).run
    consumer = task_store.complete_agent_run(
        consumer.id,
        {"outcome": "proposal"},
        owner="consumer-observability-owner",
    )
    audit = task_store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=consumer.id,
        operation_id="audit-observability-1",
        owner="audit-observability-owner",
    ).run
    entry_url = "https://news.example.com/unsubscribe?token=fixture-entry"
    entry_reference = "unsubscribe-entry:" + sha256(
        entry_url.encode("utf-8")
    ).hexdigest()
    operations = (
        {
            "operation_reference": "step-observability-1",
            "kind": "open_entry",
            "target_reference": entry_reference,
        },
    )
    binding = {
        "action_identity": action_identity,
        "action_plan_id": plan.action_plan_id,
        "action_plan_version": plan.action_plan_version,
        "classification_id": classification_id,
        "account_id": account_id,
        "stable_message_identity": stable_message_identity,
        "thread_identity": thread_identity,
        "entry_reference": entry_reference,
        "operations": operations,
    }
    effect_digest = email_unsubscribe_effect_digest(**binding)
    claim_owner = {
        "owner_id": "email-audit-worker",
        "generation": 1,
        "lease_token": "unsubscribe-observability-lease",
    }
    claim = email_store.claim_email_unsubscribe_write(
        **binding,
        effect_digest=effect_digest,
        owner=claim_owner,
        task_id=task.id,
        task_execution_generation=task.execution_generation,
        task_lifecycle_version="email_unsubscribe_audited_v2",
        task_action_type="unsubscribe",
        audit_agent_run_id=audit.id,
    )
    assert claim is not None and claim["acquired"] is True
    receipt = email_store.persist_email_unsubscribe_terminal(
        **binding,
        effect_digest=effect_digest,
        entry_url=entry_url,
        outcome="done",
        receipt_id="provider-receipt:observability-41",
        evidence="terminal-page:unsubscribed",
        result_text="You have been unsubscribed",
        started_at="2026-09-02T08:00:01+00:00",
        completed_at="2026-09-02T08:00:02+00:00",
        final_step={
            "sequence": 1,
            "operation": "open_entry",
            "state": "done",
            "reference": "provider-receipt:observability-41",
        },
        claim_owner=claim_owner,
    )
    audit = task_store.complete_agent_run(
        audit.id,
        {"outcome": "executed"},
        owner="audit-observability-owner",
    )
    task_store.complete_reply_task(
        task.id,
        expected_execution_generation=task.execution_generation,
    )

    # A task from another channel may share the same trigger identity; it must
    # never contribute run lineage to the Email unsubscribe projection.
    unrelated_task = task_store.ensure_reply_task(
        channel="dingtalk",
        conversation_id=email_conversation_id(account_id, thread_identity),
        conversation_title="Unrelated task",
        single_chat=False,
        trigger_message_id=action_identity,
        trigger_create_time="2026-09-02T08:00:03+00:00",
        trigger_sender="someone@example.com",
        trigger_text="Unrelated",
        trigger_message_json="{}",
        execution_generation="generation-unrelated-1",
    )
    unrelated_task = task_store.claim_reply_task(unrelated_task.id)
    assert unrelated_task is not None
    unrelated_run = task_store.claim_agent_run(
        unrelated_task.id,
        unrelated_task.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=None,
        operation_id="",
        owner="unrelated-owner",
    ).run
    unrelated_attempt_id = task_store.record_reply_attempt(
        conversation_id=task.conversation_id,
        conversation_title="Unrelated attempt",
        trigger_message_id="unrelated-trigger",
        trigger_sender="someone@example.com",
        trigger_text="Unrelated",
        action="none",
        sensitivity_kind="email",
        send_status="done",
        channel="email",
    )

    app = FastAPI()
    register_email_routes(app, lambda: email_store)
    return SimpleNamespace(
        database=database,
        store=email_store,
        task_store=task_store,
        client=TestClient(app),
        classification_id=classification_id,
        account_id=account_id,
        stable_message_identity=stable_message_identity,
        thread_identity=thread_identity,
        plan=plan,
        action_identity=action_identity,
        effect_digest=effect_digest,
        task_payload=task_payload,
        task=task,
        attempt_id=attempt_id,
        consumer=consumer,
        audit=audit,
        receipt=receipt,
        unrelated_run=unrelated_run,
        unrelated_attempt_id=unrelated_attempt_id,
        entry_reference=entry_reference,
        entry_url=entry_url,
        private_markers=private_markers,
    )


def _audited_observability_event(fixture: SimpleNamespace) -> dict[str, object]:
    response = fixture.client.get(
        f"/api/console/email/classifications/{fixture.classification_id}"
    )
    assert response.status_code == 200
    return response.json()["observability"][0]


def test_unsubscribe_search_uses_same_predicate_for_rows_and_count(tmp_path: Path):
    fixture = _audited_email_detail_fixture(tmp_path)
    endpoint = "/api/console/email/classifications"
    found = fixture.client.get(endpoint, params={"status":"unsubscribe","q":"NEWSLETTER"}).json()
    assert found["meta"]["total"] == 1
    assert [row["id"] for row in found["items"]] == [str(fixture.classification_id)]
    absent = fixture.client.get(endpoint, params={"status":"unsubscribe","q":"no-such-message"}).json()
    assert absent["meta"]["total"] == 0
    assert absent["items"] == []


def _assert_no_audited_lineage(event: dict[str, object]) -> None:
    assert event["consumer_run_ids"] == []
    assert event["audit_run_ids"] == []
    assert "task_id" not in event
    assert "task_status" not in event
    assert "lifecycle_version" not in event


def test_email_detail_projects_only_redacted_audited_unsubscribe_lineage(
    tmp_path: Path,
) -> None:
    fixture = _audited_email_detail_fixture(tmp_path)
    event = _audited_observability_event(fixture)

    assert event == {
        "kind": "unsubscribe",
        "operation": "unsubscribe",
        "lifecycle_version": "email_unsubscribe_audited_v2",
        "task_id": fixture.task.id,
        "task_status": "done",
        "consumer_run_ids": [fixture.consumer.id],
        "audit_run_ids": [fixture.audit.id],
        "attempt_ids": [fixture.attempt_id],
        "status": "done",
        "outcome": "done",
        "receipt_id": "provider-receipt:observability-41",
        "result_text": "You have been unsubscribed",
        "evidence": "terminal-page:unsubscribed",
        "observation_digest": fixture.receipt["observation_digest"],
        "created_at": fixture.receipt["created_at"],
        "completed_at": "2026-09-02T08:00:02+00:00",
        "steps": [
            {
                "sequence": 1,
                "operation": "open_entry",
                "state": "done",
                "reference": "provider-receipt:observability-41",
            }
        ],
    }
    assert "result_text_truncated" not in event
    assert "result_text_digest" not in event
    serialized = json.dumps(event, sort_keys=True)
    assert fixture.unrelated_run.id not in event["consumer_run_ids"]
    assert fixture.unrelated_attempt_id not in event["attempt_ids"]
    assert fixture.entry_url not in serialized
    assert all(marker not in serialized for marker in fixture.private_markers)
    assert all(
        str(value) not in serialized for value in fixture.private_markers.values()
    )

    with sqlite3.connect(fixture.database) as db:
        db.execute(
            "update reply_tasks set channel='legacy-email' where id=?",
            (fixture.task.id,),
        )
    legacy_event = _audited_observability_event(fixture)

    _assert_no_audited_lineage(legacy_event)
    assert fixture.unrelated_run.id not in legacy_event["consumer_run_ids"]


def test_email_detail_marks_terminal_no_reliable_entry_as_non_success(
    tmp_path: Path,
) -> None:
    fixture = _audited_email_detail_fixture(tmp_path)
    with sqlite3.connect(fixture.database) as db:
        db.execute(
            "update email_unsubscribe_receipts set outcome='skipped_no_reliable_entry', "
            "entry_url='' where action_identity=?",
            (fixture.action_identity,),
        )

    response = fixture.client.get(
        f"/api/console/email/classifications/{fixture.classification_id}"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["unsubscribe_entry"] == {
        "available": False,
        "reason": "entry_unavailable",
    }
    event = payload["observability"][0]
    assert event["status"] == "done"
    assert event["outcome"] == "skipped_no_reliable_entry"
    assert event["outcome"] != "done"
    assert event["created_at"]
    assert event["completed_at"] == "2026-09-02T08:00:02+00:00"
    assert fixture.entry_url not in response.text


def test_email_unsubscribe_entry_url_requires_explicit_verified_receipt(
    tmp_path: Path,
) -> None:
    fixture = _audited_email_detail_fixture(tmp_path)

    response = fixture.client.get(
        f"/api/console/email/classifications/{fixture.classification_id}/unsubscribe-entry"
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {"ok": True, "entry_url": fixture.entry_url}

    with sqlite3.connect(fixture.database) as db:
        db.execute(
            "update email_unsubscribe_receipts set entry_url='' where action_identity=?",
            (fixture.action_identity,),
        )

    unavailable = fixture.client.get(
        f"/api/console/email/classifications/{fixture.classification_id}/unsubscribe-entry"
    )

    assert unavailable.status_code == 404
    assert unavailable.json()["code"] == "unsubscribe_entry_unavailable"
    assert fixture.entry_url not in unavailable.text
    assert "fixture-entry" not in unavailable.text


def test_a_direct_receipt_without_agent_runs_still_shows_its_unsubscribe_address(
    tmp_path: Path,
) -> None:
    fixture = _audited_email_detail_fixture(tmp_path)
    with sqlite3.connect(fixture.database) as db:
        db.execute(
            "update email_unsubscribe_effects set audit_agent_run_id=null "
            "where action_identity=?",
            (fixture.action_identity,),
        )
        db.execute("delete from agent_runs")

    detail = fixture.client.get(
        f"/api/console/email/classifications/{fixture.classification_id}"
    ).json()
    entry = fixture.client.get(
        f"/api/console/email/classifications/{fixture.classification_id}/unsubscribe-entry"
    )

    assert detail["unsubscribe_entry"] == {"available": True, "reason": None}
    assert entry.json() == {"ok": True, "entry_url": fixture.entry_url}


def test_email_detail_projects_verified_attempt_for_legacy_receipt_without_audit_run(
    tmp_path: Path,
) -> None:
    fixture = _audited_email_detail_fixture(tmp_path)
    with sqlite3.connect(fixture.database) as db:
        db.execute(
            "update email_unsubscribe_effects set audit_agent_run_id=null "
            "where action_identity=?",
            (fixture.action_identity,),
        )

    event = _audited_observability_event(fixture)

    _assert_no_audited_lineage(event)
    assert event["attempt_ids"] == [fixture.attempt_id]


def test_email_detail_projects_in_flight_unsubscribe_before_terminal_receipt(
    tmp_path: Path,
) -> None:
    fixture = _audited_email_detail_fixture(tmp_path)
    with sqlite3.connect(fixture.database) as db:
        db.execute("delete from email_unsubscribe_steps")
        db.execute("delete from email_unsubscribe_receipts")
        db.execute("delete from email_unsubscribe_continuations")
        db.execute("delete from email_unsubscribe_effects")
        db.execute("delete from email_unsubscribe_claims")
        db.execute(
            "update reply_tasks set status='processing' where id=?",
            (fixture.task.id,),
        )
        db.execute(
            "update agent_runs set status='running', completed_at='' where id=?",
            (fixture.audit.id,),
        )

    response = fixture.client.get(
        f"/api/console/email/classifications/{fixture.classification_id}"
    )

    assert response.status_code == 200
    assert response.json()["observability"] == [
        {
            "kind": "unsubscribe",
            "operation": "unsubscribe",
            "lifecycle_version": "email_unsubscribe_audited_v2",
            "task_id": fixture.task.id,
            "task_status": "processing",
            "consumer_run_ids": [fixture.consumer.id],
            "audit_run_ids": [fixture.audit.id],
            "status": "processing",
        }
    ]
    serialized = json.dumps(response.json()["observability"], sort_keys=True)
    assert all(marker not in serialized for marker in fixture.private_markers)
    assert all(
        str(value) not in serialized for value in fixture.private_markers.values()
    )


def test_email_detail_projects_safe_unsubscribe_continuation_state(
    tmp_path: Path,
) -> None:
    fixture = _audited_email_detail_fixture(tmp_path)
    with sqlite3.connect(fixture.database) as db:
        db.execute("delete from email_unsubscribe_steps")
        db.execute("delete from email_unsubscribe_receipts")
        db.execute(
            "update email_unsubscribe_claims set status='dispatching', "
            "phase='navigating' where action_identity=?",
            (fixture.action_identity,),
        )
        db.execute(
            "update reply_tasks set status='processing' where id=?",
            (fixture.task.id,),
        )
        db.execute(
            "update agent_runs set status='running', completed_at='' where id=?",
            (fixture.audit.id,),
        )
    fixture.store.persist_email_unsubscribe_continuation(
        action_identity=fixture.action_identity,
        effect_digest=fixture.effect_digest,
        action_plan_id=fixture.plan.action_plan_id,
        action_plan_version=fixture.plan.action_plan_version,
        classification_id=fixture.classification_id,
        account_id=fixture.account_id,
        stable_message_identity=fixture.stable_message_identity,
        thread_identity=fixture.thread_identity,
        entry_reference=fixture.entry_reference,
        operations=(
            {
                "operation_reference": "step-observability-1",
                "kind": "open_entry",
                "target_reference": fixture.entry_reference,
            },
        ),
        controls=(
            {
                "reference": "private-browser-control",
                "kind": "captcha_handoff",
                "intent": "confirm",
            },
        ),
        observation_reference="private-browser-observation",
        final_step={
            "sequence": 1,
            "operation": "open_entry",
            "state": "action_required",
            "reference": "private-browser-step",
        },
        owner={
            "owner_id": "email-audit-worker",
            "generation": 1,
            "lease_token": "unsubscribe-observability-lease",
        },
    )

    response = fixture.client.get(
        f"/api/console/email/classifications/{fixture.classification_id}"
    )

    assert response.status_code == 200
    event = response.json()["observability"][0]
    assert event["continuation"] == {
        "state": "awaiting_audit",
        "control_kinds": ["captcha_handoff"],
        "operation_count": 1,
        "requires_human": True,
    }
    serialized = json.dumps(event, sort_keys=True)
    assert "private-browser-control" not in serialized
    assert "private-browser-observation" not in serialized
    assert "private-browser-step" not in serialized


@pytest.mark.parametrize(
    "mismatch",
    (
        "account",
        "conversation",
        "plan",
        "classification",
        "generation",
        "effect",
        "audit_run",
    ),
)
def test_email_detail_omits_lineage_when_exact_audited_chain_conflicts(
    tmp_path: Path,
    mismatch: str,
) -> None:
    fixture = _audited_email_detail_fixture(tmp_path)
    with sqlite3.connect(fixture.database) as db:
        if mismatch in {"account", "plan", "classification"}:
            payload = dict(fixture.task_payload)
            if mismatch == "account":
                payload["account_id"] = "wrong-account"
            elif mismatch == "plan":
                payload["action_plan_id"] = "email-plan:wrong"
            else:
                payload["classification_id"] = fixture.classification_id + 1
            db.execute(
                "update reply_tasks set trigger_message_json=? where id=?",
                (json.dumps(payload, sort_keys=True), fixture.task.id),
            )
        elif mismatch == "conversation":
            db.execute(
                "update reply_tasks set conversation_id='wrong-conversation' where id=?",
                (fixture.task.id,),
            )
        elif mismatch == "generation":
            db.execute(
                "update agent_runs set execution_generation='wrong-generation' "
                "where id=?",
                (fixture.audit.id,),
            )
        elif mismatch == "effect":
            db.execute(
                "update email_unsubscribe_receipts set effect_digest=? "
                "where action_identity=?",
                ("f" * 64, fixture.action_identity),
            )
        else:
            db.execute(
                "update email_unsubscribe_effects set audit_agent_run_id=? "
                "where action_identity=? and effect_digest=?",
                (
                    fixture.consumer.id,
                    fixture.action_identity,
                    fixture.effect_digest,
                ),
            )

    _assert_no_audited_lineage(_audited_observability_event(fixture))


def test_email_detail_preserves_all_valid_continuation_rounds_in_lineage(
    tmp_path: Path,
) -> None:
    fixture = _audited_email_detail_fixture(tmp_path)
    revised_consumer = fixture.task_store.claim_agent_run(
        fixture.task.id,
        fixture.task.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=1,
        turn_attempt=0,
        parent_agent_run_id=fixture.audit.id,
        operation_id="",
        owner="consumer-observability-revision-owner",
    ).run
    revised_consumer = fixture.task_store.complete_agent_run(
        revised_consumer.id,
        {"outcome": "revised-proposal"},
        owner="consumer-observability-revision-owner",
    )
    final_audit = fixture.task_store.claim_agent_run(
        fixture.task.id,
        fixture.task.execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=1,
        turn_attempt=0,
        parent_agent_run_id=revised_consumer.id,
        operation_id="audit-observability-2",
        owner="audit-observability-revision-owner",
    ).run
    final_audit = fixture.task_store.complete_agent_run(
        final_audit.id,
        {"outcome": "executed"},
        owner="audit-observability-revision-owner",
    )
    with sqlite3.connect(fixture.database) as db:
        db.execute(
            "update email_unsubscribe_effects set audit_agent_run_id=? "
            "where action_identity=? and effect_digest=?",
            (final_audit.id, fixture.action_identity, fixture.effect_digest),
        )

    event = _audited_observability_event(fixture)

    assert event["consumer_run_ids"] == [fixture.consumer.id, revised_consumer.id]
    assert event["audit_run_ids"] == [fixture.audit.id, final_audit.id]


def test_future_email_schema_isolated_from_non_email_routes(tmp_path: Path):
    database = tmp_path / "corrupt.sqlite3"
    EmailStore(database)
    with sqlite3.connect(database) as db:
        db.execute(
            "update email_schema_migrations set version=?",
            (email_store_module.EMAIL_SCHEMA_VERSION + 1,),
        )
    factory_calls = 0

    def factory() -> EmailStore:
        nonlocal factory_calls
        factory_calls += 1
        return EmailStore(database)

    app = FastAPI()

    @app.get("/healthz")
    def healthz():
        return {"ok": True, "status": "ok"}

    @app.get("/api/non-email")
    def non_email():
        return {"ok": True}

    register_email_routes(app, factory)

    availability = app.state.email_store_availability
    assert availability.store is None
    assert availability.diagnostic == "email_persistence_corruption"
    assert str(database) not in availability.diagnostic
    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/api/non-email").status_code == 200
        _assert_email_endpoints_unavailable(client)
        _assert_email_endpoints_unavailable(client)
    assert factory_calls == 1


@pytest.mark.parametrize(
    "error_type",
    (
        sqlite3.ProgrammingError,
        sqlite3.NotSupportedError,
        TypeError,
        ValueError,
        SystemExit,
        KeyboardInterrupt,
    ),
)
def test_email_route_registration_does_not_hide_programming_or_control_flow_errors(
    error_type: type[BaseException],
):
    app = FastAPI()

    def factory():
        raise error_type("sentinel")

    with pytest.raises(error_type, match="sentinel"):
        register_email_routes(app, factory)


def test_locked_missing_schema_stays_unavailable_without_request_retry(
    tmp_path: Path,
):
    database = tmp_path / "locked-migration.sqlite3"
    factory_calls = 0
    writer = sqlite3.connect(database, timeout=0)
    try:
        writer.execute("begin immediate")

        def factory() -> EmailStore:
            nonlocal factory_calls
            factory_calls += 1
            return _ZeroTimeoutEmailStore(database)

        app = FastAPI()
        register_email_routes(app, factory)
        availability = app.state.email_store_availability
        assert availability.store is None
        assert availability.diagnostic == "sqlite_operational_error"
        writer.rollback()
        with TestClient(app) as client:
            _assert_email_endpoints_unavailable(client)
            _assert_email_endpoints_unavailable(client)
    finally:
        writer.close()

    assert factory_calls == 1
    with sqlite3.connect(database) as db:
        migration_table = db.execute(
            """
            select 1 from sqlite_master
            where type='table' and name='email_schema_migrations'
            """
        ).fetchone()
    assert migration_table is None


def test_audit_app_starts_when_email_schema_is_unavailable(tmp_path: Path):
    database = tmp_path / "audit-app.sqlite3"
    AutoReplyStore(database)
    EmailStore(database)
    with sqlite3.connect(database) as db:
        db.execute(
            "update email_schema_migrations set version=?",
            (email_store_module.EMAIL_SCHEMA_VERSION + 1,),
        )
    _assert_audit_app_email_unavailable(database, tmp_path)


def test_audit_app_starts_when_current_email_schema_is_missing_a_column(
    tmp_path: Path,
):
    database = tmp_path / "audit-app-missing-email-column.sqlite3"
    AutoReplyStore(database)
    EmailStore(database)
    gc.collect()
    with sqlite3.connect(database) as db:
        db.execute("alter table email_messages drop column normalized_text")

    _assert_audit_app_email_unavailable(database, tmp_path)


def test_audit_app_isolates_non_text_email_schema_identifier(tmp_path: Path):
    database = tmp_path / "audit-app-invalid-email-schema-name.sqlite3"
    AutoReplyStore(database)
    EmailStore(database)
    gc.collect()
    with sqlite3.connect(database) as db:
        schema_version = db.execute("pragma schema_version").fetchone()[0]
        db.execute("pragma writable_schema = on")
        db.execute(
            "update sqlite_master set name=? "
            "where type='table' and name='email_messages'",
            (sqlite3.Binary(b"email_messages"),),
        )
        db.execute(f"pragma schema_version = {schema_version + 1}")
        db.commit()
        db.execute("pragma writable_schema = off")

    _assert_audit_app_email_unavailable(database, tmp_path)


def test_audit_app_isolates_non_text_foreign_key_on_delete_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    database = tmp_path / "audit-app-invalid-email-fk-on-delete.sqlite3"
    AutoReplyStore(database)
    EmailStore(database)
    original_open_connection = EmailStore._open_connection

    class CorruptOnDeleteRow:
        def __init__(self, row: sqlite3.Row):
            self._row = row

        def __getitem__(self, key: object):
            if key == "on_delete":
                return 7
            return self._row[key]

    def corrupting_open_connection(self: EmailStore) -> sqlite3.Connection:
        db = original_open_connection(self)

        def row_factory(cursor: sqlite3.Cursor, values: tuple[object, ...]):
            row = sqlite3.Row(cursor, values)
            if "on_delete" in row.keys():
                return CorruptOnDeleteRow(row)
            return row

        db.row_factory = row_factory
        return db

    monkeypatch.setattr(EmailStore, "_open_connection", corrupting_open_connection)

    _assert_audit_app_email_unavailable(database, tmp_path)


def test_audit_app_isolates_weakened_current_email_schema_declarations(
    tmp_path: Path,
):
    database = tmp_path / "audit-app-weakened-email-schema.sqlite3"
    AutoReplyStore(database)
    EmailStore(database)
    gc.collect()
    with sqlite3.connect(database) as db:
        db.executescript(
            """
            alter table email_scan_cursors rename to old_email_scan_cursors;
            create table email_scan_cursors (
                account_id text not null,
                folder text not null,
                uidvalidity text,
                last_seen_uid text,
                last_success_at text,
                last_error text,
                primary key (account_id, folder)
            );
            drop table old_email_scan_cursors;
            """
        )

    _assert_audit_app_email_unavailable(database, tmp_path)


def test_audit_app_starts_when_email_json_contains_invalid_utf8_blob(
    tmp_path: Path,
):
    database = tmp_path / "audit-app-invalid-email-json.sqlite3"
    AutoReplyStore(database)
    EmailStore(database)
    gc.collect()
    with sqlite3.connect(database) as db:
        db.execute("pragma ignore_check_constraints = on")
        db.execute(
            """
            insert into email_accounts (
                account_id, display_name, email_address, imap_host, imap_port,
                imap_tls, imap_username, imap_secret_reference, smtp_host,
                smtp_port, smtp_tls, smtp_username, smtp_secret_reference,
                enabled, scan_folders_json, scan_interval_seconds, created_at,
                updated_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "invalid-json-account",
                "Invalid JSON",
                "redacted@example.com",
                "imap.example.com",
                993,
                1,
                "redacted@example.com",
                "IMAP_SECRET_REFERENCE",
                "smtp.example.com",
                465,
                1,
                "redacted@example.com",
                "SMTP_SECRET_REFERENCE",
                1,
                sqlite3.Binary(b"\xff"),
                60,
                "2026-08-29T16:00:00+00:00",
                "2026-08-29T16:00:00+00:00",
            ),
        )

    _assert_audit_app_email_unavailable(database, tmp_path)


def test_paginated_get_does_not_compete_with_scanner_write_transaction(
    tmp_path: Path,
):
    database = tmp_path / "concurrent-scanner.sqlite3"
    EmailStore(database)
    factory_calls = 0

    def factory() -> EmailStore:
        nonlocal factory_calls
        factory_calls += 1
        return _ZeroTimeoutEmailStore(database)

    writer = sqlite3.connect(database, timeout=0)
    try:
        writer.execute("pragma journal_mode = wal")
        writer.execute("begin immediate")
        writer.execute(
            """
            insert into email_scan_cursors (
                account_id, folder, uidvalidity, last_seen_uid,
                last_success_at, last_error
            ) values (?, ?, ?, ?, ?, ?)
            """,
            ("dingtalk-account", "INBOX", 42, 7, "", ""),
        )
        app = FastAPI()

        @app.get("/healthz")
        def healthz():
            return {"ok": True}

        @app.get("/api/non-email")
        def non_email():
            return {"ok": True}

        register_email_routes(app, factory)
        with TestClient(app) as client:
            health = client.get("/healthz")
            non_email_response = client.get("/api/non-email")
            response = client.get(
                "/api/console/email/classifications?page=1&page_size=1"
            )
        assert health.status_code == 200
        assert non_email_response.status_code == 200
        assert response.status_code == 200
        assert response.json()["meta"]["total"] == 0
        writer.commit()
    finally:
        writer.close()

    assert factory_calls == 1
    assert EmailStore(database).get_scan_cursor("dingtalk-account", "INBOX") == {
        "account_id": "dingtalk-account",
        "folder": "INBOX",
        "uidvalidity": 42,
        "last_seen_uid": 7,
        "last_success_at": "",
        "last_error": "",
    }


@pytest.mark.parametrize(
    ("category", "actions", "action_parameters"),
    (
        ("configured_work", ["label"], {"label": {"labels": ["work"]}}),
        (
            "configured_billing",
            ["move"],
            {"move": {"target_folder": "Archive/Billing"}},
        ),
    ),
)
def test_email_config_api_persists_valid_action_parameters(
    tmp_path: Path,
    category: str,
    actions: list[str],
    action_parameters: dict[str, dict[str, object]],
):
    coordinator = _VerifiedFolderCoordinator()
    app = FastAPI()
    register_email_routes(
        app,
        lambda: EmailStore(tmp_path / "action-config.sqlite3"),
        folder_binding_coordinator=coordinator,
    )
    payload = {
        **_dynamic_category_payload(category),
        "actions": actions,
        "action_parameters": action_parameters,
    }
    with TestClient(app) as client:
        response = client.post(
            "/api/console/email/config",
            json=payload,
        )

    assert response.status_code == 201
    assert response.json()["item"]["action_parameters"] == action_parameters


def test_email_config_api_rejects_auto_reply_even_with_valid_instruction(
    tmp_path: Path,
):
    coordinator = _VerifiedFolderCoordinator()
    app = FastAPI()
    register_email_routes(
        app,
        lambda: EmailStore(tmp_path / "auto-reply-config.sqlite3"),
        folder_binding_coordinator=coordinator,
    )
    payload = {
        **_dynamic_category_payload("auto_reply_category"),
        "actions": ["auto_reply"],
        "action_parameters": {
            "auto_reply": {"instruction": "Acknowledge receipt"}
        },
    }
    with TestClient(app) as client:
        response = client.post(
            "/api/console/email/config",
            json=payload,
        )

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_email_category"
    assert coordinator.calls == []


def test_email_config_api_rejects_reserved_unsubscribe_categories(tmp_path: Path):
    payload = {
        **_dynamic_category_payload("important"),
        "actions": ["unsubscribe"],
    }
    coordinator = _VerifiedFolderCoordinator()
    app = FastAPI()
    register_email_routes(
        app,
        lambda: EmailStore(tmp_path / "reserved-config.sqlite3"),
        folder_binding_coordinator=coordinator,
    )
    with TestClient(app) as client:
        rejected = client.post("/api/console/email/config", json=payload)
        reserved = client.post(
            "/api/console/email/config",
            json={**payload, "category_key": "subscription"},
        )

    assert rejected.status_code == 400
    assert rejected.json()["code"] == "invalid_email_category"
    assert reserved.status_code == 400
    assert reserved.json()["code"] == "invalid_email_category"


def test_email_account_api_accepts_nontechnical_payload_without_secret_reference(
    tmp_path: Path,
):
    database = tmp_path / "accounts.sqlite3"
    env_file = tmp_path / ".env"
    app = FastAPI()
    register_email_routes(
        app,
        lambda: EmailStore(database),
        email_env_path=env_file,
    )
    payload = {
        "account_id": "work_mail",
        "display_name": "Work Mail",
        "email_address": "work@example.test",
        "imap_host": "imap.example.test",
        "imap_port": 993,
        "imap_tls": True,
        "imap_username": "work@example.test",
        "imap_secret": "known-imap-secret",
        "enabled": True,
        "scan_folders": ["INBOX"],
    }
    with TestClient(app) as client:
        created = client.post("/api/console/email/accounts", json=payload)
        listed = client.get("/api/console/email/accounts")
        updated = client.put(
            "/api/console/email/accounts/work_mail",
            json={**payload, "imap_secret": ""},
        )
        rejected_cadence = client.put(
            "/api/console/email/accounts/work_mail",
            json={**payload, "scan_interval_seconds": 120},
        )

    assert created.status_code == 201
    assert created.json()["restart_required"] is True
    assert listed.json()["items"][0]["imap_secret_configured"] is True
    assert updated.status_code == 200
    assert rejected_cadence.status_code == 400
    assert EmailStore(database).get_account("work_mail")["scan_interval_seconds"] == 60
    for response in (created, listed, updated):
        assert "scan_interval_seconds" not in response.json().get("item", response.json().get("items", [{}])[0])
        assert "known-imap-secret" not in response.text
        assert "imap_secret_reference" not in response.text


@pytest.mark.parametrize(
    ("actions", "action_parameters"),
    (
        (["label"], None),
        (["move"], {"move": {"target_folder": " "}}),
        (["auto_reply"], {"auto_reply": {"instruction": ""}}),
        (["archive"], {"archive": {"folder": "Archive"}}),
        (["trash"], {"trash": {"permanent_delete": True}}),
        (["label"], {"unknown": {"labels": ["work"]}}),
    ),
)
def test_email_config_api_returns_controlled_4xx_for_invalid_action_parameters(
    tmp_path: Path,
    actions: list[str],
    action_parameters: dict[str, dict[str, object]] | None,
):
    payload = {
        **_dynamic_category_payload("invalid_action_config"),
        "actions": actions,
    }
    if action_parameters is not None:
        payload["action_parameters"] = action_parameters
    coordinator = _VerifiedFolderCoordinator()
    app = FastAPI()
    register_email_routes(
        app,
        lambda: EmailStore(tmp_path / "invalid-action-config.sqlite3"),
        folder_binding_coordinator=coordinator,
    )
    with TestClient(app) as client:
        response = client.post("/api/console/email/config", json=payload)

    assert response.status_code == 400


def test_email_config_api_retains_no_parameter_archive_behavior(tmp_path: Path):
    coordinator = _VerifiedFolderCoordinator()
    app = FastAPI()
    register_email_routes(
        app,
        lambda: EmailStore(tmp_path / "archive-config.sqlite3"),
        folder_binding_coordinator=coordinator,
    )
    payload = {
        **_dynamic_category_payload("archive_notification"),
        "actions": ["archive"],
    }
    with TestClient(app) as client:
        response = client.post(
            "/api/console/email/config",
            json=payload,
        )

    assert response.status_code == 201
    assert response.json()["item"]["actions"] == ["archive"]
    assert response.json()["item"]["action_parameters"] == {}


def _task2_api_account(account_id: str = "primary") -> dict[str, object]:
    return {
        "account_id": account_id,
        "display_name": account_id,
        "email_address": f"{account_id}@example.com",
        "imap_host": "imap.example.com",
        "imap_port": 993,
        "imap_tls": True,
        "imap_username": f"{account_id}@example.com",
        "imap_secret_reference": f"keychain://{account_id}",
        "smtp_host": "",
        "smtp_port": 465,
        "smtp_tls": True,
        "smtp_username": "",
        "smtp_secret_reference": "",
        "enabled": True,
        "scan_folders": ["INBOX"],
        "scan_interval_seconds": 60,
    }


def _dynamic_category_payload(category_key: str = "partner_updates") -> dict[str, object]:
    return {
        "category_key": category_key,
        "display_name": "合作伙伴",
        "provider_folder_name": "合作伙伴",
        "core_description": "Material partner relationship updates.",
        "include": ["partner discussions"],
        "exclude": ["unsolicited partnership promotion"],
        "threshold": 0.91,
        "actions": [],
        "action_parameters": {},
        "enabled": True,
        "description_version": "partner-description-v1",
        "config_version": "partner-config-v1",
    }


class _VerifiedFolderCoordinator:
    def __init__(self, *, folder_id: str = "folder-partners"):
        self.folder_id = folder_id
        self.calls: list[dict[str, object]] = []

    def create_and_verify_bindings(
        self,
        *,
        category_key: str,
        provider_folder_name: str,
        enabled_accounts: list[dict[str, object]],
    ) -> tuple[VerifiedEmailFolderBinding, ...]:
        self.calls.append(
            {
                "category_key": category_key,
                "provider_folder_name": provider_folder_name,
                "account_ids": [account["account_id"] for account in enabled_accounts],
            }
        )
        return tuple(
            VerifiedEmailFolderBinding(
                account_id=str(account["account_id"]),
                provider_folder_id=self.folder_id,
                provider_folder_name=provider_folder_name,
                binding_status="active",
                last_verified_at="2026-09-07T13:00:00+00:00",
            )
            for account in enabled_accounts
        )


class _CoordinatorFailure:
    def __init__(self, failure: Exception):
        self.failure = failure

    def create_and_verify_bindings(self, **_kwargs):
        raise self.failure


class _NegativeJunkCoordinator:
    def __init__(self, status: str):
        self.status = status

    def create_and_verify_bindings(self, **_kwargs):
        return (
            VerifiedEmailFolderBinding(
                account_id="primary",
                provider_folder_id="",
                provider_folder_name="垃圾邮件",
                binding_status=self.status,
                last_verified_at="2026-09-08T11:05:00+00:00",
            ),
        )


class _StaticFolderCoordinator:
    def __init__(self, bindings: tuple[VerifiedEmailFolderBinding, ...]):
        self.bindings = bindings

    def create_and_verify_bindings(self, **_kwargs):
        return self.bindings


class _CreationInventoryProvider:
    def __init__(self, status: str):
        self.status = status
        self.calls = 0

    def list_folders(self):
        self.calls += 1
        if self.status == "error":
            raise ConnectionError("inventory unavailable")
        if self.status == "ambiguous":
            return (
                ProviderFolder("one", "合作伙伴", FolderRole.UNBOUND),
                ProviderFolder("two", "合作伙伴", FolderRole.UNBOUND),
            )
        return ()

    def create_folder_exact(self, _name: str) -> None:
        pass

    def close(self) -> None:
        pass


class _IteratorFolderCoordinator(_VerifiedFolderCoordinator):
    def create_and_verify_bindings(self, **kwargs):
        return iter(super().create_and_verify_bindings(**kwargs))


def test_email_config_get_returns_structured_descriptions_and_bindings(tmp_path: Path):
    with _client(tmp_path) as client:
        response = client.get("/api/console/email/config")

    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 9
    work = next(item for item in items if item["category_key"] == "work")
    assert work["display_name"] == "工作"
    assert work["core_description"]
    assert work["include"]
    assert work["exclude"]
    assert work["bindings"] == []
    assert "category" not in work
    assert "description" not in work


def test_email_config_post_builds_real_folder_coordinator_by_default(
    tmp_path: Path,
    monkeypatch,
):
    coordinator = _VerifiedFolderCoordinator()
    built_with: list[object] = []

    def build(environment_factory):
        built_with.append(environment_factory)
        return coordinator

    monkeypatch.setattr(
        "app.email_worker.build_provider_folder_binding_coordinator", build
    )
    store = EmailStore(tmp_path / "default-folder-coordinator.sqlite3")
    store.create_account(_task2_api_account())
    app = FastAPI()
    register_email_routes(app, lambda: store)

    response = TestClient(app).post(
        "/api/console/email/config",
        json=_dynamic_category_payload(),
    )

    assert response.status_code == 201
    assert len(built_with) == 1
    assert coordinator.calls[0]["account_ids"] == ["primary"]


def test_email_config_post_maps_coordinator_binding_conflict_to_409(tmp_path: Path):
    app = FastAPI()
    register_email_routes(
        app,
        lambda: EmailStore(tmp_path / "coordinator-conflict-api.sqlite3"),
        folder_binding_coordinator=_CoordinatorFailure(
            EmailFolderBindingConflict("provider folder conflict")
        ),
    )

    response = TestClient(app).post(
        "/api/console/email/config",
        json=_dynamic_category_payload(),
    )

    assert response.status_code == 409
    assert response.json()["code"] == "email_folder_binding_conflict"


def test_email_config_post_rejects_non_sequence_coordinator_result(tmp_path: Path):
    app = FastAPI()
    register_email_routes(
        app,
        lambda: EmailStore(tmp_path / "coordinator-result-api.sqlite3"),
        folder_binding_coordinator=_IteratorFolderCoordinator(),
    )

    response = TestClient(app).post(
        "/api/console/email/config",
        json=_dynamic_category_payload(),
    )

    assert response.status_code == 503
    assert response.json()["code"] == "email_folder_binding_unavailable"


def test_email_config_post_uses_coordinator_readback_and_rejects_client_bindings(
    tmp_path: Path,
):
    database = tmp_path / "dynamic-category-api.sqlite3"
    store = EmailStore(database)
    store.create_account(_task2_api_account())
    coordinator = _VerifiedFolderCoordinator()
    app = FastAPI()
    register_email_routes(
        app,
        lambda: store,
        folder_binding_coordinator=coordinator,
    )
    with TestClient(app) as client:
        created = client.post(
            "/api/console/email/config",
            json=_dynamic_category_payload(),
        )
        rejected = client.post(
            "/api/console/email/config",
            json={
                **_dynamic_category_payload("raw_claim"),
                "bindings": [
                    {
                        "account_id": "primary",
                        "provider_folder_id": "client-claimed-id",
                        "binding_status": "active",
                    }
                ],
            },
        )

    assert created.status_code == 201
    assert created.json()["item"]["category_key"] == "partner_updates"
    assert created.json()["item"]["enabled"] is True
    assert created.json()["item"]["bindings"][0]["provider_folder_id"] == (
        "folder-partners"
    )
    assert coordinator.calls == [
        {
            "category_key": "partner_updates",
            "provider_folder_name": "合作伙伴",
            "account_ids": ["primary"],
        }
    ]
    assert rejected.status_code == 400
    assert rejected.json()["code"] == "invalid_email_category"
    assert store.get_category_config("raw_claim") is None


@pytest.mark.parametrize("provider_status", ("error", "missing", "ambiguous"))
def test_email_config_post_rejects_nonactive_production_folder_materialization(
    tmp_path: Path,
    provider_status: str,
) -> None:
    store = EmailStore(tmp_path / f"create-{provider_status}-binding.sqlite3")
    store.create_account(_task2_api_account())
    providers: list[_CreationInventoryProvider] = []

    def provider_factory(_account):
        provider = _CreationInventoryProvider(provider_status)
        providers.append(provider)
        return provider

    coordinator = ProviderFolderBindingCoordinator(provider_factory)
    app = FastAPI()
    register_email_routes(
        app,
        lambda: store,
        folder_binding_coordinator=coordinator,
    )

    response = TestClient(app).post(
        "/api/console/email/config",
        json=_dynamic_category_payload(),
    )

    assert response.status_code == 503
    assert response.json()["code"] == "email_folder_binding_unavailable"
    assert store.get_category_config("partner_updates") is None
    assert len(providers) == 1


@pytest.mark.parametrize(
    "payload",
    (
        _dynamic_category_payload("important"),
        {**_dynamic_category_payload(), "core_description": " "},
        {**_dynamic_category_payload(), "include": []},
        {**_dynamic_category_payload(), "exclude": [""]},
    ),
)
def test_email_config_post_returns_category_error_for_invalid_semantics(
    tmp_path: Path,
    payload: dict[str, object],
):
    coordinator = _VerifiedFolderCoordinator()
    app = FastAPI()
    register_email_routes(
        app,
        lambda: EmailStore(tmp_path / "invalid-category-api.sqlite3"),
        folder_binding_coordinator=coordinator,
    )

    response = TestClient(app).post("/api/console/email/config", json=payload)

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_email_category"
    assert coordinator.calls == []


def test_email_config_post_returns_conflict_for_active_provider_folder_collision(
    tmp_path: Path,
):
    database = tmp_path / "category-conflict-api.sqlite3"
    store = EmailStore(database)
    store.create_account(_task2_api_account())
    coordinator = _VerifiedFolderCoordinator(folder_id="shared-provider-folder")
    app = FastAPI()
    register_email_routes(
        app,
        lambda: store,
        folder_binding_coordinator=coordinator,
    )
    with TestClient(app) as client:
        first = client.post(
            "/api/console/email/config",
            json=_dynamic_category_payload("partners"),
        )
        duplicate = client.post(
            "/api/console/email/config",
            json=_dynamic_category_payload("partners"),
        )
        second = client.post(
            "/api/console/email/config",
            json=_dynamic_category_payload("vendors"),
        )

    assert first.status_code == 201
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "email_folder_binding_conflict"
    assert second.status_code == 409
    assert second.json()["code"] == "email_folder_binding_conflict"
    assert len(coordinator.calls) == 2
    assert store.get_category_config("vendors") is None


def test_email_config_put_updates_structured_descriptions_and_versions(tmp_path: Path):
    with _client(tmp_path) as client:
        current = next(item for item in client.get("/api/console/email/config").json()["items"]
                       if item["category_key"] == "work")
        response = client.put(
            "/api/console/email/config/work",
            json={
                "core_description": "Material daily company operations.",
                "include": ["customer delivery"],
                "exclude": ["personal matters"],
                "threshold": 0.93,
                "enabled": True,
                "description_version": "work-description-v2",
                "config_version": "work-config-v2",
                "expected_current_version": current["config_version"],
            },
        )

    assert response.status_code == 200
    item = response.json()["item"]
    assert item["category_key"] == "work"
    assert item["core_description"] == "Material daily company operations."
    assert item["include"] == ["customer delivery"]
    assert item["exclude"] == ["personal matters"]
    assert item["threshold"] == 0.93
    assert item["enabled"] is True
    assert item["description_version"] == "work-description-v2"
    assert item["config_version"] == "work-config-v2"
    assert item["bindings"] == []


def test_email_config_put_refreshes_provider_folder_bindings(tmp_path: Path):
    store = EmailStore(tmp_path / "update-folder-bindings.sqlite3")
    store.create_account(_task2_api_account())
    coordinator = _VerifiedFolderCoordinator(folder_id="work-folder")
    app = FastAPI()
    register_email_routes(
        app,
        lambda: store,
        folder_binding_coordinator=coordinator,
    )

    response = TestClient(app).put(
        "/api/console/email/config/work",
        json={
            "core_description": "Material daily company operations.",
            "include": ["customer delivery"],
            "exclude": ["personal matters"],
            "threshold": 0.93,
            "enabled": True,
            "description_version": "work-description-v2",
            "config_version": "work-config-v2",
            "expected_current_version": store.get_category_config("work")["config_version"],
        },
    )

    assert response.status_code == 200
    assert response.json()["item"]["enabled"] is True
    assert response.json()["item"]["bindings"][0]["provider_folder_id"] == (
        "work-folder"
    )
    assert coordinator.calls == [
        {
            "category_key": "work",
            "provider_folder_name": "工作",
            "account_ids": ["primary"],
        }
    ]


@pytest.mark.parametrize("negative_status", ("missing", "error"))
def test_email_config_put_pauses_junk_writes_after_negative_trash_readback(
    tmp_path: Path,
    negative_status: str,
) -> None:
    store = EmailStore(tmp_path / f"junk-{negative_status}-api.sqlite3")
    store.create_account(_task2_api_account())
    store.upsert_verified_folder_binding(
        "junk",
        VerifiedEmailFolderBinding(
            account_id="primary",
            provider_folder_id="provider-trash",
            provider_folder_name="Deleted",
            binding_status="active",
            last_verified_at="2026-09-08T11:00:00+00:00",
            provider_folder_role=FolderRole.TRASH,
        ),
    )
    junk = store.get_category_config("junk")
    enabled = store.update_category_descriptions(
        "junk",
        core_description=junk["core_description"],
        include=junk["include"],
        exclude=junk["exclude"],
        threshold=junk["threshold"],
        enabled=True,
        description_version=junk["description_version"],
        config_version="junk-enabled-before-negative-readback",
    )
    assert enabled is not None and enabled["enabled"] is True
    app = FastAPI()
    register_email_routes(
        app,
        lambda: store,
        folder_binding_coordinator=_NegativeJunkCoordinator(negative_status),
    )

    response = TestClient(app).put(
        "/api/console/email/config/junk",
        json={
            "core_description": junk["core_description"],
            "include": junk["include"],
            "exclude": junk["exclude"],
            "threshold": junk["threshold"],
            "enabled": True,
            "description_version": junk["description_version"],
            "config_version": f"junk-{negative_status}-readback",
            "expected_current_version": enabled["config_version"],
        },
    )

    assert response.status_code == 200
    item = response.json()["item"]
    assert item["enabled"] is False
    assert item["bindings"][0]["provider_folder_id"] == ""
    assert item["bindings"][0]["binding_status"] == negative_status


def test_email_config_put_maps_second_account_binding_conflict_and_rolls_back(
    tmp_path: Path,
) -> None:
    store = EmailStore(tmp_path / "atomic-refresh-api.sqlite3")
    store.create_account(_task2_api_account("primary"))
    store.create_account(_task2_api_account("secondary"))
    for account_id in ("primary", "secondary"):
        store.upsert_verified_folder_binding(
            "work",
            VerifiedEmailFolderBinding(
                account_id=account_id,
                provider_folder_id=f"old-work-{account_id}",
                provider_folder_name="工作",
                binding_status="active",
                last_verified_at="2026-09-08T12:00:00+00:00",
            ),
        )
    work = store.get_category_config("work")
    store.update_category_descriptions(
        "work",
        core_description=work["core_description"],
        include=work["include"],
        exclude=work["exclude"],
        threshold=work["threshold"],
        enabled=True,
        description_version=work["description_version"],
        config_version="work-before-api-conflict",
    )
    store.upsert_verified_folder_binding(
        "legal",
        VerifiedEmailFolderBinding(
            account_id="secondary",
            provider_folder_id="secondary-conflict",
            provider_folder_name="法务",
            binding_status="active",
            last_verified_at="2026-09-08T12:00:00+00:00",
        ),
    )
    before_config = store.get_category_config("work")
    before_bindings = store.list_account_folder_bindings("work")
    coordinator = _StaticFolderCoordinator(
        (
            VerifiedEmailFolderBinding(
                account_id="primary",
                provider_folder_id="new-work-primary",
                provider_folder_name="工作",
                binding_status="active",
                last_verified_at="2026-09-08T12:05:00+00:00",
            ),
            VerifiedEmailFolderBinding(
                account_id="secondary",
                provider_folder_id="secondary-conflict",
                provider_folder_name="工作",
                binding_status="active",
                last_verified_at="2026-09-08T12:05:00+00:00",
            ),
        )
    )
    app = FastAPI()
    register_email_routes(
        app,
        lambda: store,
        folder_binding_coordinator=coordinator,
    )

    response = TestClient(app, raise_server_exceptions=False).put(
        "/api/console/email/config/work",
        json={
            "core_description": "Changed description must roll back.",
            "include": ["changed include"],
            "exclude": ["changed exclude"],
            "threshold": 0.81,
            "enabled": False,
            "description_version": "work-description-conflict",
            "config_version": "work-config-conflict",
            "expected_current_version": before_config["config_version"],
        },
    )

    assert response.status_code == 409
    assert response.json()["code"] == "email_folder_binding_conflict"
    assert store.get_category_config("work") == before_config
    assert store.list_account_folder_bindings("work") == before_bindings


def test_retired_category_feedback_is_refused_at_the_boundary(tmp_path: Path):
    """A retired category must not reach the confirmation as a 500.

    An older model still predicts `important`, so the console can post it for
    a mail that model classified. `EmailCategory` still carries the retired
    keys, so reading the payload through it accepted a category the write path
    goes on to refuse -- deep enough that the refusal escaped unhandled and the
    console showed "请求失败，请稍后重试" for a request no retry can fix.
    """
    store = EmailStore(tmp_path / "retired-category.sqlite3")
    identity = 165239721836324343
    classification = EmailClassification.model_validate({
        "classification_id": identity,
        "stable_message_identity": "account-1:message-id:<retired@example.com>",
        "provider_locator": {"account_id": "account-1", "folder": "INBOX", "uidvalidity": 1, "uid": 1, "rfc_message_id": "<retired@example.com>", "thread_id": "retired"},
        "category": EmailCategory.NOTIFICATION, "confidence": 0.136, "margin": 0.0003,
        "probabilities": {"notification": 0.136}, "model_id": "email-tfidf-lr-20260905T214932Z-53379cdd",
        "config_version": "test-v1", "status": EmailClassificationStatus.PENDING_FEEDBACK,
        "classification_source": "model", "action_plan": None,
    })
    store.persist_scan_result(classification, sender="collections@example.com", subject="Overdue", preview="Overdue", model_text="Overdue")
    app = FastAPI()
    register_email_routes(app, lambda: store)
    client = TestClient(app)

    response = client.post(
        f"/api/console/email/classifications/{identity}/feedback",
        json={
            "category": "important",
            "feedback_request_id": f"email-feedback:{identity}",
            "expected_current_action_plan_id": None,
        },
    )

    assert response.status_code == 400, response.text
    assert "retired" in response.json()["detail"]
    # The mail is untouched, so it can still be confirmed as a live category.
    assert store.get_classification(identity)["classification_source"] == "model"
    accepted = client.post(
        f"/api/console/email/classifications/{identity}/feedback",
        json={
            "category": "external_billing",
            "feedback_request_id": f"email-feedback:{identity}:external_billing",
            "expected_current_action_plan_id": None,
        },
    )
    assert accepted.status_code == 200, accepted.text
    assert store.get_classification(identity)["category"] == "external_billing"


def _verified_binding(
    account_id: str,
    category_key: str,
    *,
    status: str = "active",
) -> VerifiedEmailFolderBinding:
    role = FolderRole.TRASH if category_key == "junk" else FolderRole.UNBOUND
    return VerifiedEmailFolderBinding(
        account_id=account_id,
        provider_folder_id=f"{category_key}-folder" if status == "active" else "",
        provider_folder_name=f"{category_key}-folder",
        binding_status=status,
        last_verified_at="2026-09-17T01:00:00+00:00",
        provider_folder_role=role if status == "active" else FolderRole.UNBOUND,
    )


def _running_mailbox(database: Path, account_id: str) -> EmailStore:
    """One mailbox with every category verified and enabled, as a live install is."""

    store = EmailStore(database)
    store.create_account(
        {
            "account_id": account_id,
            "display_name": account_id,
            "email_address": f"{account_id}@example.test",
            "imap_host": "imap.example.test",
            "imap_port": 993,
            "imap_tls": True,
            "imap_username": f"{account_id}@example.test",
            "imap_secret_reference": f"keychain://{account_id}",
            "imap_move_mode": "move",
            "smtp_host": "",
            "smtp_port": 587,
            "smtp_tls": True,
            "smtp_username": "",
            "smtp_secret_reference": "",
            "enabled": True,
            "scan_folders": ["INBOX"],
            "scan_interval_seconds": 60,
        }
    )
    for category_key in INITIAL_EMAIL_CATEGORY_KEYS:
        store.upsert_verified_folder_binding(
            category_key, _verified_binding(account_id, category_key)
        )
        config = store.get_category_config(category_key)
        store.update_category_descriptions(
            category_key,
            core_description=config["core_description"],
            include=config["include"],
            exclude=config["exclude"],
            threshold=config["threshold"],
            enabled=True,
            description_version=config["description_version"],
            config_version=f"{category_key}-enabled-v1",
        )
    return store


class _CoordinatorStub:
    """Return what a provider readback would prove for one account."""

    def __init__(self, *, failing_categories: frozenset[str] = frozenset()):
        self.failing_categories = failing_categories

    def create_and_verify_bindings(
        self, *, category_key: str, provider_folder_name: str, enabled_accounts
    ):
        del provider_folder_name
        status = "error" if category_key in self.failing_categories else "active"
        return tuple(
            _verified_binding(
                str(account["account_id"]), category_key, status=status
            )
            for account in enabled_accounts
        )


def _new_account_payload(account_id: str) -> dict[str, object]:
    return {
        "account_id": account_id,
        "display_name": account_id,
        "email_address": f"{account_id}@example.test",
        "imap_host": "imap.example.test",
        "imap_port": 993,
        "imap_tls": True,
        "imap_username": f"{account_id}@example.test",
        "imap_secret": "second-mailbox-secret",
        "enabled": True,
        "scan_folders": ["INBOX"],
    }


def test_adding_a_second_mailbox_keeps_the_first_one_classifying(tmp_path: Path):
    """The incident: adding a mailbox switched every category off for all of them."""

    database = tmp_path / "second-mailbox.sqlite3"
    _running_mailbox(database, "primary")
    app = FastAPI()
    register_email_routes(
        app,
        lambda: EmailStore(database),
        email_env_path=tmp_path / ".env",
        folder_binding_coordinator=_CoordinatorStub(),
    )

    with TestClient(app) as client:
        created = client.post(
            "/api/console/email/accounts", json=_new_account_payload("second")
        )

    assert created.status_code == 201
    assert created.json()["item"]["unverified_categories"] == []
    assert created.json()["message"] == "Email account configuration saved"
    store = EmailStore(database)
    assert [
        row["category_key"]
        for row in store.list_category_configs()
        if not row["enabled"]
    ] == []


def test_a_mailbox_whose_folder_fails_pauses_only_that_category_and_says_so(
    tmp_path: Path,
):
    database = tmp_path / "unverified-folder.sqlite3"
    _running_mailbox(database, "primary")
    app = FastAPI()
    register_email_routes(
        app,
        lambda: EmailStore(database),
        email_env_path=tmp_path / ".env",
        folder_binding_coordinator=_CoordinatorStub(
            failing_categories=frozenset({"junk"})
        ),
    )

    with TestClient(app) as client:
        created = client.post(
            "/api/console/email/accounts", json=_new_account_payload("second")
        )

    assert created.status_code == 201
    assert created.json()["item"]["unverified_categories"] == ["junk"]
    assert "junk" in created.json()["message"]
    store = EmailStore(database)
    disabled = [
        row["category_key"]
        for row in store.list_category_configs()
        if not row["enabled"]
    ]
    assert disabled == ["junk"]


def test_account_api_saves_independent_agent_and_model_windows(
    tmp_path: Path,
):
    database = tmp_path / "scan-window.sqlite3"
    app = FastAPI()
    register_email_routes(
        app,
        lambda: EmailStore(database),
        email_env_path=tmp_path / ".env",
        folder_binding_coordinator=_CoordinatorStub(),
    )
    payload = _new_account_payload("windowed") | {
        "enabled": False,
        "agent_lookback_days": 90,
        "model_lookback_days": 365,
        "scan_read_state": "all",
    }

    with TestClient(app) as client:
        created = client.post("/api/console/email/accounts", json=payload)
        too_long = client.put(
            "/api/console/email/accounts/windowed",
            json=payload | {"agent_lookback_days": 366},
        )
        model_too_long = client.put(
            "/api/console/email/accounts/windowed",
            json=payload | {"model_lookback_days": 3651},
        )
        unknown_state = client.put(
            "/api/console/email/accounts/windowed",
            json=payload | {"scan_read_state": "flagged"},
        )

    assert created.status_code == 201
    assert created.json()["item"]["agent_lookback_days"] == 90
    assert created.json()["item"]["model_lookback_days"] == 365
    assert "scan_lookback_days" not in created.json()["item"]
    assert created.json()["item"]["scan_read_state"] == "all"
    assert too_long.status_code == 400
    assert model_too_long.status_code == 400
    assert unknown_state.status_code == 400
    stored = EmailStore(database).get_account("windowed")
    assert (
        stored["agent_lookback_days"],
        stored["model_lookback_days"],
        stored["scan_read_state"],
    ) == (90, 365, "all")


def test_learning_reports_the_active_run_only_while_it_is_still_running(tmp_path: Path):
    """The retrain state keeps a finished run's id; the console must not."""

    from app.email_classifier_retrain import RetrainState, save_retrain_state

    database = tmp_path / "active-run.sqlite3"
    registry = EmailModelRegistry(tmp_path / "models")
    state_path = tmp_path / "models" / "retrain-state.json"
    service = SimpleNamespace(
        registry=registry,
        retrain_state_path=state_path,
        controller=SimpleNamespace(registry=registry),
    )
    app = FastAPI()
    register_email_routes(app, lambda: EmailStore(database), email_learning_factory=lambda: service)

    def write_run(run_id: str, status: str, model_ids: list[str]) -> None:
        (registry.runs / f"{run_id}.json").write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "status": status,
                    "started_at": "2026-09-17T08:00:00+00:00",
                    "finished_at": "",
                    "reason": "",
                    "model_ids": model_ids,
                    "training_selection": {"model_families": ["embedding-mlp"]},
                }
            ),
            encoding="utf-8",
        )

    write_run("run-finished", "succeeded", ["email-embedding-mlp-done"])
    save_retrain_state(state_path, RetrainState().with_active_run("run-finished"))
    with TestClient(app) as client:
        stale = client.get("/api/console/email/learning").json()["learning"]

    write_run("run-live", "running", [])
    save_retrain_state(state_path, RetrainState().with_active_run("run-live"))
    with TestClient(app) as client:
        live = client.get("/api/console/email/learning").json()["learning"]

    assert stale["active_run_id"] is None
    assert live["active_run_id"] == "run-live"
    running_rows = [row for row in live["training_runs_without_model"] if row["run_id"] == "run-live"]
    assert running_rows and running_rows[0]["status"] == "running"
    assert running_rows[0]["model_families"] == ["embedding-mlp"]


def test_action_throughput_is_the_whole_action_over_the_recent_window(tmp_path: Path):
    from datetime import datetime, timedelta, timezone

    from app.email_store import EmailStore

    store = EmailStore(tmp_path / "throughput.sqlite3")
    now = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)

    def action(index: int, *, status: str, started: datetime, seconds: int) -> None:
        with store._connect() as db:
            db.execute("pragma foreign_keys=off")
            db.execute(
                """insert into email_actions (
                       action_id, action_plan_id, classification_id, account_id, action_type,
                       parameters_json, config_version, status, attempt_count, started_at,
                       finished_at, next_attempt_at, provider_operation, provider_target,
                       provider_result_id, error, created_at, updated_at
                   ) values (?, ?, 1, 'a', 'move', '{}', 'c', ?, 0, ?, ?, '', '', '', '', '', ?, ?)""",
                (
                    f"action-{index}", f"plan-{index}", status, started.isoformat(),
                    (started + timedelta(seconds=seconds)).isoformat(),
                    started.isoformat(), started.isoformat(),
                ),
            )

    for index, seconds in enumerate((4, 8, 12)):
        action(index, status="done", started=now - timedelta(minutes=5 + index), seconds=seconds)
    action(9, status="skipped", started=now - timedelta(minutes=2), seconds=2)
    # Too old to count, and one that never finished.
    action(10, status="done", started=now - timedelta(hours=3), seconds=30)
    action(11, status="failed", started=now - timedelta(minutes=1), seconds=5)

    speed = store._provider_action_throughput(now=now)

    assert speed["finished"] == 4
    assert speed["per_minute"] == 0.1
    assert speed["median_seconds"] == 8.0
