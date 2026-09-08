from datetime import datetime, timezone
import sqlite3

from app.email_classifier_contracts import (
    EmailAction,
    EmailCategory,
    EmailClassificationStatus,
)
from app.email_classifier_training import CategoryEligibility
from app.email_pipeline import (
    EmailCategoryConfig,
    EmailModelPrediction,
    decide_classification,
)
from app.email_store import EmailStore
from app.email_classifier_runtime import StageLatencyRecorder


def test_runtime_observability_sink_failure_never_changes_classification_path():
    recorder = StageLatencyRecorder(
        sample_sink=lambda _sample: (_ for _ in ()).throw(OSError("db unavailable"))
    )

    recorder.record(
        outcome="success",
        cache_hit=True,
        runtime_warm=True,
        queue_ms=0.0,
        http_ms=0.0,
        embedding_ms=0.0,
        head_ms=1.0,
        total_ms=2.0,
    )

    assert recorder.summary()["warm_success_cache"]["sample_count"] == 1


def test_runtime_observability_persists_safe_bounded_samples_across_processes(
    tmp_path,
):
    database = tmp_path / "runtime-observability.sqlite3"
    writer = EmailStore(database)
    writer.record_classifier_runtime_sample(
        model_id="email-embedding-mlp-live",
        outcome="success",
        cache_hit=True,
        runtime_warm=True,
        queue_ms=1.0,
        http_ms=0.0,
        embedding_ms=0.0,
        head_ms=3.0,
        total_ms=7.0,
    )
    writer.record_classifier_runtime_fallback(
        model_id="email-embedding-mlp-live",
        fallback_code="model_rejected",
    )

    reader = EmailStore(database)
    observed = reader.classifier_runtime_observability(
        model_id="email-embedding-mlp-live"
    )

    assert observed["timing"]["warm_success_cache"]["sample_count"] == 1
    assert observed["timing"]["warm_success_cache"]["stages"]["total"] == {
        "p50": 7.0,
        "p95": 7.0,
        "p99": 7.0,
    }
    assert observed["fallback_counts"] == {"model_rejected": 1}
    with sqlite3.connect(database) as db:
        columns = {
            row[1]
            for row in db.execute(
                "pragma table_info(email_classifier_runtime_samples)"
            )
        }
    assert not {
        "message_text",
        "request_text",
        "embedding",
        "vector",
        "failure_reason",
    } & columns


def test_latest_snapshot_and_provider_truth_use_set_queries_and_lookup_index(
    tmp_path,
):
    store = EmailStore(tmp_path / "bounded-observability.sqlite3")
    statements: list[str] = []
    original_connect = store._connect

    def traced_connect():
        db = original_connect()
        db.set_trace_callback(statements.append)
        return db

    store._connect = traced_connect  # type: ignore[method-assign]
    assert store.latest_training_snapshot_state() is None
    select_statements = [
        statement for statement in statements if statement.lstrip().upper().startswith("SELECT")
    ]
    assert len(select_statements) <= 2

    with original_connect() as db:
        indexes = {
            row[1]
            for row in db.execute(
                "pragma index_list(email_provider_observations)"
            )
        }
        plan = db.execute(
            """
            explain query plan
            select state, category_key, important, provider_folder_id,
                   provider_folder_name, observed_at
            from email_provider_observations
            where account_id=? and stable_message_identity=?
            """,
            ("account-1", "stable-1"),
        ).fetchall()
    assert "idx_email_provider_observations_lookup" in indexes
    assert any(
        "USING INDEX" in str(row[3]).upper()
        for row in plan
    )
    assert not any(" over " in statement.lower() for statement in select_statements)
    assert not any(
        "email_training_snapshot_observations" in statement.lower()
        for statement in select_statements
    )


def test_latest_snapshot_projects_counts_and_provider_folder_truth(tmp_path):
    store = EmailStore(tmp_path / "observability.sqlite3")
    classification = {
        "classification_id": 91,
        "stable_message_identity": "account-1:message-id:<legal@example.com>",
        "provider_locator": {
            "account_id": "account-1",
            "folder": "INBOX",
            "uidvalidity": 1,
            "uid": 91,
            "rfc_message_id": "<legal@example.com>",
            "thread_id": "legal-thread",
        },
        "category": EmailCategory.LEGAL,
        "confidence": 1.0,
        "margin": 1.0,
        "probabilities": {"legal": 1.0},
        "model_id": "agent:cold-start",
        "config_version": "config-v1",
        "status": EmailClassificationStatus.PENDING_FEEDBACK,
        "classification_source": "model",
        "action_plan": None,
    }
    from app.email_classifier_contracts import EmailClassification

    store.upsert_classification(
        EmailClassification.model_validate(classification),
        sender="legal@example.com",
        subject="Contract",
        preview="Contract",
        model_text="contract",
    )
    with store._connect() as db:
        db.execute(
            """
            insert into email_training_snapshots (
                snapshot_id, snapshot_version, description_version,
                input_schema_version, seed, observed_at, snapshot_digest,
                manifest_json, frozen, created_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
            """,
            (
                "snapshot-observability",
                "email-folder-snapshot.v1",
                "description-set-v1",
                "email-model-input.v3",
                20260905,
                "2026-09-08T08:00:00+00:00",
                "a" * 64,
                "{}",
                "2026-09-08T08:00:00+00:00",
            ),
        )
        rows = (
            ("legal-1", "folder-legal", "Legal", "legal", "group-a"),
            ("legal-2", "folder-legal", "Legal", "legal", "group-b"),
            ("finance-1", "folder-financing", "Financing", "financing", "group-c"),
        )
        for identity, folder_id, folder_name, category, group in rows:
            stable_identity = (
                classification["stable_message_identity"]
                if identity == "legal-1"
                else f"account-1:message-id:<{identity}@example.com>"
            )
            db.execute(
                """
                insert into email_training_snapshot_observations (
                    snapshot_id, account_id, stable_message_identity,
                    provider_folder_id, provider_folder_name, category_key,
                    important, normalized_model_input,
                    normalized_model_input_hash, input_schema_version,
                    provider_thread_id, normalized_body_digest,
                    sender_template_signature, explicit_matter_group,
                    group_key, observed_at, source, split,
                    selected_for_training, ordered_record_digest
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, null, null,
                          ?, ?, 'natural', 'train', 1, ?)
                """,
                (
                    "snapshot-observability",
                    "account-1",
                    stable_identity,
                    folder_id,
                    folder_name,
                    category,
                    1 if identity == "legal-1" else 0,
                    identity,
                    "b" * 64,
                    "email-model-input.v3",
                    group,
                    "c" * 64,
                    (group + "0" * 64)[:64],
                    "2026-09-08T08:00:00+00:00",
                    "d" * 64,
                ),
            )
        db.execute(
            "update email_training_snapshots set frozen=1 "
            "where snapshot_id='snapshot-observability'"
        )

    statements: list[str] = []
    original_connect = store._connect

    def traced_connect():
        db = original_connect()
        db.set_trace_callback(statements.append)
        return db

    store._connect = traced_connect  # type: ignore[method-assign]
    state = store.latest_training_snapshot_state()
    store._connect = original_connect  # type: ignore[method-assign]
    store.record_current_provider_observations(
        [
            {
                "account_id": "account-1",
                "stable_message_identity": classification["stable_message_identity"],
                "provider_folder_id": "folder-legal",
                "provider_folder_name": "Legal",
                "folder_role": "category",
                "bound_category_key": "legal",
                "folder_binding_status": "active",
                "important_signals": {"provider_important": True},
            }
        ],
        unavailable_folders=(),
        observed_at="2026-09-08T08:01:00+00:00",
    )
    provider = store.get_provider_classification_state(91)

    assert state is not None
    assert state["snapshot_version"] == "email-folder-snapshot.v1"
    assert state["sample_count"] == 3
    assert state["group_count"] == 3
    assert state["category_sample_counts"] == {"financing": 1, "legal": 2}
    assert state["category_group_counts"] == {"financing": 1, "legal": 2}
    selects = [
        statement
        for statement in statements
        if statement.lstrip().upper().startswith("SELECT")
    ]
    assert len(selects) <= 5
    assert sum("from email_training_snapshots" in item.lower() for item in selects) == 1
    assert provider == {
        "state": "categorized",
        "category_key": "legal",
        "important": True,
        "provider_folder_id": "folder-legal",
        "provider_folder_name": "Legal",
        "observed_at": "2026-09-08T08:01:00+00:00",
    }


def test_current_provider_truth_changes_without_freezing_another_snapshot(tmp_path):
    store = EmailStore(tmp_path / "current-provider-truth.sqlite3")
    from app.email_classifier_contracts import EmailClassification

    classification = EmailClassification.model_validate(
        {
            "classification_id": 92,
            "stable_message_identity": "account-1:message-id:<move@example.com>",
            "provider_locator": {
                "account_id": "account-1",
                "folder": "Legal",
                "uidvalidity": 1,
                "uid": 92,
                "rfc_message_id": "<move@example.com>",
                "thread_id": None,
            },
            "category": EmailCategory.LEGAL,
            "confidence": 1.0,
            "margin": 1.0,
            "probabilities": {"legal": 1.0},
            "model_id": "agent:cold-start",
            "config_version": "config-v1",
            "status": EmailClassificationStatus.PENDING_FEEDBACK,
            "classification_source": "model",
            "action_plan": None,
        }
    )
    store.upsert_classification(
        classification,
        sender="legal@example.com",
        subject="Legal",
        preview="Legal",
        model_text="legal",
    )
    base = {
        "account_id": "account-1",
        "stable_message_identity": classification.stable_message_identity,
        "folder_role": "category",
        "folder_binding_status": "active",
        "important_signals": {"provider_important": False},
    }
    store.record_current_provider_observations(
        [
            {
                **base,
                "provider_folder_id": "folder-legal",
                "provider_folder_name": "Legal",
                "bound_category_key": "legal",
            }
        ],
        unavailable_folders=(),
        observed_at="2026-09-08T08:00:00+00:00",
    )
    store.record_current_provider_observations(
        [
            {
                **base,
                "provider_folder_id": "folder-financing",
                "provider_folder_name": "Financing",
                "bound_category_key": "financing",
            }
        ],
        unavailable_folders=(),
        observed_at="2026-09-08T08:05:00+00:00",
    )

    assert store.get_provider_classification_state(92) == {
        "state": "categorized",
        "category_key": "financing",
        "important": False,
        "provider_folder_id": "folder-financing",
        "provider_folder_name": "Financing",
        "observed_at": "2026-09-08T08:05:00+00:00",
    }
    store.record_current_provider_observations(
        [
            {
                **base,
                "provider_folder_id": "sent",
                "provider_folder_name": "Sent",
                "folder_role": "sent",
                "bound_category_key": None,
                "folder_binding_status": "unbound",
            }
        ],
        unavailable_folders=(),
        authoritative_folders=("account-1:folder-financing", "account-1:sent"),
        active_account_ids=("account-1",),
        observed_at="2026-09-08T08:06:00+00:00",
    )
    assert store.get_provider_classification_state(92) == {
        "state": "excluded",
        "reason": "provider_truth_excluded",
        "observed_at": "2026-09-08T08:06:00+00:00",
    }


def test_current_provider_truth_reconciles_disappearance_and_preserves_unavailable(tmp_path):
    store = EmailStore(tmp_path / "provider-reconciliation.sqlite3")
    from app.email_classifier_contracts import EmailClassification

    classification = EmailClassification.model_validate(
        {
            "classification_id": 93,
            "stable_message_identity": "account-1:message-id:<gone@example.com>",
            "provider_locator": {
                "account_id": "account-1", "folder": "Legal", "uidvalidity": 1,
                "uid": 93, "rfc_message_id": "<gone@example.com>", "thread_id": None,
            },
            "category": "legal", "confidence": 1.0, "margin": 1.0,
            "probabilities": {"legal": 1.0}, "model_id": "agent:cold-start",
            "config_version": "config-v1",
            "status": EmailClassificationStatus.PENDING_FEEDBACK,
            "classification_source": "model", "action_plan": None,
        }
    )
    store.upsert_classification(
        classification, sender="legal@example.com", subject="Legal",
        preview="Legal", model_text="legal",
    )
    observation = {
        "account_id": "account-1",
        "stable_message_identity": classification.stable_message_identity,
        "provider_folder_id": "folder-legal", "provider_folder_name": "Legal",
        "folder_role": "category", "bound_category_key": "legal",
        "folder_binding_status": "active",
        "important_signals": {"provider_important": False},
    }
    store.record_current_provider_observations(
        [observation], unavailable_folders=(),
        authoritative_folders=("account-1:folder-legal",),
        active_account_ids=("account-1",), observed_at="2026-09-08T08:00:00+00:00",
    )
    store.record_current_provider_observations(
        [], unavailable_folders=("account-1:folder-legal",),
        authoritative_folders=(), active_account_ids=("account-1",),
        observed_at="2026-09-08T08:01:00+00:00",
    )
    assert store.get_provider_classification_state(93)["state"] == "unavailable"

    store.record_current_provider_observations(
        [observation], unavailable_folders=(),
        authoritative_folders=("account-1:folder-legal",),
        active_account_ids=("account-1",), observed_at="2026-09-08T08:02:00+00:00",
    )
    store.record_current_provider_observations(
        [], unavailable_folders=(), authoritative_folders=("account-1:folder-legal",),
        active_account_ids=("account-1",), observed_at="2026-09-08T08:03:00+00:00",
    )
    assert store.get_provider_classification_state(93) == {
        "state": "unavailable", "reason": "provider_truth_unavailable",
        "observed_at": "2026-09-08T08:03:00+00:00",
    }
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.web_api.email import register_email_routes

    app = FastAPI()
    register_email_routes(app, lambda: store)
    detail = TestClient(app).get("/api/console/email/classifications/93")
    assert detail.status_code == 200
    assert detail.json()["provider_classification"] == {
        "state": "unavailable",
        "reason": "provider_truth_unavailable",
        "observed_at": "2026-09-08T08:03:00+00:00",
    }

    store.record_current_provider_observations(
        [observation], unavailable_folders=(),
        authoritative_folders=("account-1:folder-legal",),
        active_account_ids=("account-1",), observed_at="2026-09-08T08:04:00+00:00",
    )
    store.record_current_provider_observations(
        [], unavailable_folders=(), authoritative_folders=(), active_account_ids=(),
        observed_at="2026-09-08T08:05:00+00:00",
    )
    assert store.get_provider_classification_state(93)["state"] == "unavailable"


def test_model_only_prediction_without_action_eligibility_stays_pending_feedback():
    prediction = EmailModelPrediction(
        category=EmailCategory.WORK,
        confidence=0.99,
        margin=0.80,
        probabilities={"work": 0.99, "legal": 0.01},
        model_id="email-model:candidate",
    )
    category_config = EmailCategoryConfig(
        category=EmailCategory.WORK,
        description="Work",
        threshold=0.95,
        actions=(EmailAction.LABEL,),
        action_parameters={EmailAction.LABEL: {"labels": ["Work"]}},
        enabled=True,
        config_version="email-config:v1",
    )
    eligibility = CategoryEligibility(
        category=EmailCategory.WORK,
        configured_threshold=0.95,
        validated_precision=0.99,
        validation_sample_count=30,
        auto_action_eligible=False,
        reason="model_not_active",
    )

    model_only_prediction = decide_classification(
        prediction,
        category_config,
        eligibility,
        classification_id=1,
        account_id="account-1",
        created_at=datetime(2026, 8, 30, tzinfo=timezone.utc),
    )

    assert model_only_prediction.status == EmailClassificationStatus.PENDING_FEEDBACK
    assert model_only_prediction.status.value == "pending_feedback"
    assert model_only_prediction.action_plan is None
