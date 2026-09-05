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
    EmailClassification,
    EmailClassificationStatus,
    build_versioned_email_action_plan,
)
from app.email_classifier_model import CpuTfidfLogisticClassifier
from app.email_model_registry import (
    EmailModelMetadata,
    EmailModelRegistry,
    build_model_id,
)
from app.email_store import (
    EmailStore,
    email_action_identity,
    email_unsubscribe_effect_digest,
)
from app.email_task_adapter import email_conversation_id
from app.store import AgentRole, AutoReplyStore
from app.web_api.email import register_email_routes


def _client(tmp_path: Path) -> TestClient:
    database = tmp_path / "worker.sqlite3"
    app = FastAPI()
    register_email_routes(app, lambda: EmailStore(database))
    return TestClient(app)


class _ZeroTimeoutEmailStore(EmailStore):
    def _connect(self) -> sqlite3.Connection:
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
                "description": "Work",
                "threshold": 0.9,
                "actions": [],
                "enabled": True,
                "config_version": "email-config-v1",
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
        f"{category.value} {variant}"
        for category in EmailCategory
        for variant in ("primary", "secondary")
    ]
    labels = [
        category.value
        for category in EmailCategory
        for _variant in ("primary", "secondary")
    ]
    classifier = CpuTfidfLogisticClassifier(model_version="candidate").fit(
        texts,
        labels,
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
        for category in (item.value for item in EmailCategory)
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
        sample_count=160,
        new_sample_count=16,
        category_counts={category.value: 20 for category in EmailCategory},
        account_counts={"account-a": 160},
        validation_method="time-ordered-holdout",
        accuracy=0.96,
        macro_f1=0.955,
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
    assert by_id[previous]["superseded_reason"] == f"superseded_by:{active}"
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
            "model_id": corrupt,
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
        "model_id": first,
        "integrity_status": "corrupt",
        "integrity_error": "metadata_invalid",
    } in learning["registry_issues"]


def test_email_classification_list_and_detail_expose_only_attachment_metadata(
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
            "probabilities": {"work": 0.7, "important": 0.3},
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
        normalized_text="__subject__quarterly brief",
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
    for response_item in (listed.json()["items"][0], detailed.json()["item"]):
        assert all(
            set(attachment) == {"filename", "mime_type", "size_bytes", "inline"}
            for attachment in response_item["attachment_metadata"]
        )


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

    app = FastAPI()
    register_email_routes(app, lambda: DetailStore())

    response = TestClient(app).get("/api/console/email/classifications/41")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["item"] == classification
    assert payload["observability"] == [
        {
            "kind": "unsubscribe",
            "operation": "unsubscribe",
            "status": "done",
            "receipt_id": "receipt-41",
            "result_text": "退订成功",
        }
    ]
    assert payload["meta"]["snapshot_at"]

    missing = TestClient(app).get("/api/console/email/classifications/42")
    assert missing.status_code == 404
    assert missing.json()["code"] == "not_found"


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
        category=EmailCategory.SUBSCRIPTION,
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
                "category": EmailCategory.SUBSCRIPTION,
                "confidence": 1.0,
                "margin": 1.0,
                "probabilities": {"subscription": 1.0},
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
    operations = (
        {
            "operation_reference": "step-observability-1",
            "kind": "open_entry",
            "target_reference": "unsubscribe-entry:observability-1",
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
        "entry_reference": "unsubscribe-entry:observability-1",
        "operations": operations,
        "network_policy_reference": "network-policy:observability-1",
        "network_policy_origin_references": ("network-origin:observability-1",),
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
        consumer=consumer,
        audit=audit,
        receipt=receipt,
        unrelated_run=unrelated_run,
        private_markers=private_markers,
    )


def _audited_observability_event(fixture: SimpleNamespace) -> dict[str, object]:
    response = fixture.client.get(
        f"/api/console/email/classifications/{fixture.classification_id}"
    )
    assert response.status_code == 200
    return response.json()["observability"][0]


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
        "status": "done",
        "receipt_id": "provider-receipt:observability-41",
        "result_text": "You have been unsubscribed",
        "evidence": "terminal-page:unsubscribed",
        "observation_digest": fixture.receipt["observation_digest"],
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
    original_connect = EmailStore._connect

    class CorruptOnDeleteRow:
        def __init__(self, row: sqlite3.Row):
            self._row = row

        def __getitem__(self, key: object):
            if key == "on_delete":
                return 7
            return self._row[key]

    def corrupting_connect(self: EmailStore) -> sqlite3.Connection:
        db = original_connect(self)

        def row_factory(cursor: sqlite3.Cursor, values: tuple[object, ...]):
            row = sqlite3.Row(cursor, values)
            if "on_delete" in row.keys():
                return CorruptOnDeleteRow(row)
            return row

        db.row_factory = row_factory
        return db

    monkeypatch.setattr(EmailStore, "_connect", corrupting_connect)

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
        ("work", ["label"], {"label": {"labels": ["work"]}}),
        (
            "billing",
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
    with _client(tmp_path) as client:
        response = client.put(
            f"/api/console/email/config/{category}",
            json={
                "description": "Configured category",
                "threshold": 0.95,
                "actions": actions,
                "action_parameters": action_parameters,
                "enabled": True,
                "config_version": "email-config-v1",
            },
        )

    assert response.status_code == 200
    assert response.json()["item"]["action_parameters"] == action_parameters


def test_email_config_api_rejects_auto_reply_even_with_valid_instruction(
    tmp_path: Path,
):
    with _client(tmp_path) as client:
        response = client.put(
            "/api/console/email/config/important",
            json={
                "description": "No outbound replies",
                "threshold": 0.95,
                "actions": ["auto_reply"],
                "action_parameters": {
                    "auto_reply": {"instruction": "Acknowledge receipt"}
                },
                "enabled": True,
                "config_version": "email-config-v1",
            },
        )

    assert response.status_code == 400
    assert response.json()["detail"] == (
        "auto_reply is disabled; email worker cannot send replies"
    )


def test_email_config_api_allows_unsubscribe_only_for_subscription(tmp_path: Path):
    payload = {
        "description": "Wrong unsubscribe category",
        "threshold": 0.95,
        "actions": ["unsubscribe"],
        "enabled": True,
        "config_version": "email-config-v1",
    }
    with _client(tmp_path) as client:
        rejected = client.put("/api/console/email/config/important", json=payload)
        accepted = client.put("/api/console/email/config/subscription", json=payload)

    assert rejected.status_code == 400
    assert rejected.json()["detail"] == (
        "unsubscribe can only be configured for subscription"
    )
    assert accepted.status_code == 200


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
        "scan_interval_seconds": 60,
    }
    with TestClient(app) as client:
        created = client.post("/api/console/email/accounts", json=payload)
        listed = client.get("/api/console/email/accounts")
        updated = client.put(
            "/api/console/email/accounts/work_mail",
            json={**payload, "imap_secret": "", "scan_interval_seconds": 120},
        )

    assert created.status_code == 201
    assert created.json()["restart_required"] is True
    assert listed.json()["items"][0]["imap_secret_configured"] is True
    assert updated.status_code == 200
    assert updated.json()["item"]["scan_interval_seconds"] == 120
    for response in (created, listed, updated):
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
        "description": "Invalid category config",
        "threshold": 0.95,
        "actions": actions,
        "enabled": True,
        "config_version": "email-config-v1",
    }
    if action_parameters is not None:
        payload["action_parameters"] = action_parameters
    with _client(tmp_path) as client:
        response = client.put(
            "/api/console/email/config/work",
            json=payload,
        )

    assert response.status_code == 400


def test_email_config_api_retains_no_parameter_archive_behavior(tmp_path: Path):
    with _client(tmp_path) as client:
        response = client.put(
            "/api/console/email/config/subscription",
            json={
                "description": "Archive subscription",
                "threshold": 0.98,
                "actions": ["archive"],
                "enabled": True,
                "config_version": "email-config-v1",
            },
        )

    assert response.status_code == 200
    assert response.json()["item"]["actions"] == ["archive"]
    assert response.json()["item"]["action_parameters"] == {}
