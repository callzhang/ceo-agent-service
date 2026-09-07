from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
import json
import sqlite3
from threading import Barrier, Event, Thread

import pytest
from pydantic import ValidationError

from app.email_classifier_contracts import (
    EmailAction,
    EmailCategory,
    EmailCategoryKey,
    EmailClassification,
    EmailClassificationStatus,
    EmailProviderLocator,
    validate_email_category_key,
)
from app.email_classifier_training import CategoryEligibility, EmailActionEligibility
from app.email_pipeline import (
    EmailCategoryConfig,
    EmailClassificationDecision,
    EmailModelPrediction,
    apply_human_confirmation,
    decide_classification,
)
from app.email_store import EmailClassificationConflict, EmailStore


NOW = datetime(2026, 8, 30, 16, 0, tzinfo=timezone.utc)
MODEL_ID = "email-tfidf-lr-20260830T160000Z-1234567890abcdef"


def _legacy_store_category(category: str) -> EmailCategory:
    return EmailCategory(validate_email_category_key(category))


@pytest.mark.parametrize("category", ("work", "board_governance"))
def test_pipeline_category_contracts_store_exact_plain_strings(category: str):
    prediction = EmailModelPrediction(
        category=category,  # type: ignore[arg-type]
        confidence=0.9,
        margin=0.5,
        probabilities={category: 1.0},
        model_id=MODEL_ID,
    )
    config = EmailCategoryConfig(
        category=category,  # type: ignore[arg-type]
        description=category,
        threshold=0.8,
        actions=(),
        action_parameters={},
        enabled=True,
        config_version="email-config-v1",
    )
    decision = EmailClassificationDecision(
        category=category,  # type: ignore[arg-type]
        confidence=0.9,
        margin=0.5,
        probabilities={category: 1.0},
        model_id=MODEL_ID,
        config_version="email-config-v1",
        status=EmailClassificationStatus.PENDING_FEEDBACK,
        action_plan=None,
    )

    assert type(prediction.category) is str
    assert all(type(key) is str for key in prediction.probabilities)
    assert type(config.category) is str
    assert type(decision.category) is str
    assert all(type(key) is str for key in decision.probabilities)


def test_only_junk_category_may_authorize_unsubscribe() -> None:
    with pytest.raises(ValueError, match="only junk may authorize unsubscribe"):
        EmailCategoryConfig(
            category="notification",
            description="Notification",
            threshold=0.8,
            actions=(EmailAction.UNSUBSCRIBE,),
            action_parameters={},
            enabled=True,
            config_version="email-config-v1",
        )

    junk = EmailCategoryConfig(
        category="junk",
        description="Junk",
        threshold=0.8,
        actions=(EmailAction.UNSUBSCRIBE, EmailAction.TRASH),
        action_parameters={EmailAction.UNSUBSCRIBE: {}},
        enabled=True,
        config_version="email-config-v1",
    )

    assert junk.actions == (EmailAction.UNSUBSCRIBE, EmailAction.TRASH)


def test_human_confirmation_forwards_a_valid_custom_category_key():
    calls = []

    class RecordingStore:
        def apply_human_classification(self, classification_id, category, **kwargs):
            calls.append((classification_id, category, kwargs))
            return "recorded"

    result = apply_human_confirmation(
        RecordingStore(),  # type: ignore[arg-type]
        17,
        "board_governance",
        feedback_request_id="feedback-custom-category",
        expected_current_action_plan_id=None,
        now=NOW,
    )

    assert result == "recorded"
    assert calls[0][0:2] == (17, "board_governance")
    assert type(calls[0][1]) is str


def test_human_confirmation_forwards_initial_category_as_plain_string():
    calls = []

    class RecordingStore:
        def apply_human_classification(self, classification_id, category, **kwargs):
            calls.append((classification_id, category, kwargs))
            return "recorded"

    result = apply_human_confirmation(
        RecordingStore(),  # type: ignore[arg-type]
        18,
        "work",
        feedback_request_id="feedback-initial-string",
        expected_current_action_plan_id=None,
        now=NOW,
    )

    assert result == "recorded"
    assert calls[0][0:2] == (18, "work")
    assert type(calls[0][1]) is str


def test_classification_decision_rejects_an_invalid_category_key():
    with pytest.raises(ValueError):
        EmailClassificationDecision(
            category="Work",  # type: ignore[arg-type]
            confidence=0.9,
            margin=0.5,
            probabilities={"work": 0.9},
            model_id=MODEL_ID,
            config_version="email-config-v1",
            status=EmailClassificationStatus.PENDING_FEEDBACK,
            action_plan=None,
        )


def test_model_prediction_rejects_an_invalid_probability_category_key():
    with pytest.raises(ValueError):
        EmailModelPrediction(
            category="work",
            confidence=0.9,
            margin=0.5,
            probabilities={"Work": 0.9},
            model_id=MODEL_ID,
        )


def test_classification_decision_rejects_an_invalid_probability_category_key():
    with pytest.raises(ValueError):
        EmailClassificationDecision(
            category="work",
            confidence=0.9,
            margin=0.5,
            probabilities={"Work": 0.9},
            model_id=MODEL_ID,
            config_version="email-config-v1",
            status=EmailClassificationStatus.PENDING_FEEDBACK,
            action_plan=None,
        )


def _prediction(*, confidence: float = 0.93) -> EmailModelPrediction:
    return EmailModelPrediction(
        category="work",
        confidence=confidence,
        margin=0.41,
        probabilities={"work": confidence, "legal": 1.0 - confidence},
        model_id=MODEL_ID,
    )


def _config(
    *,
    category: EmailCategoryKey = "work",
    enabled: bool = True,
    threshold: float = 0.8,
    config_version: str = "email-config-v1",
    actions: tuple[EmailAction, ...] = (EmailAction.LABEL,),
) -> EmailCategoryConfig:
    parameters = (
        {EmailAction.LABEL: {"labels": [category]}}
        if EmailAction.LABEL in actions
        else {}
    )
    return EmailCategoryConfig(
        category=category,
        description="test category",
        threshold=threshold,
        actions=actions,
        action_parameters=parameters,
        enabled=enabled,
        config_version=config_version,
    )


def _eligibility(*, eligible: bool = True) -> CategoryEligibility:
    return CategoryEligibility(
        category="work",
        configured_threshold=0.8,
        validated_precision=0.99 if eligible else 0.70,
        validation_sample_count=30,
        auto_action_eligible=eligible,
        reason="eligible" if eligible else "precision_gate_not_met",
        source_model_id=MODEL_ID,
        action_eligibility={
            EmailAction.LABEL: EmailActionEligibility(
                action=EmailAction.LABEL,
                auto_action_eligible=eligible,
                reason="eligible" if eligible else "action_precision_gate_not_met",
                source_model_id=MODEL_ID,
                evidence_reference="email-model-eligibility:model-1:label",
            )
        },
    )


def _decision(
    *,
    confidence: float = 0.93,
    enabled: bool = True,
    eligible: bool = True,
):
    return decide_classification(
        _prediction(confidence=confidence),
        _config(enabled=enabled),
        _eligibility(eligible=eligible),
        classification_id=101,
        account_id="account-a",
        created_at=NOW,
    )


def _persist_decision(store: EmailStore, decision, *, classification_id: int = 101):
    rfc_message_id = f"<pipeline-{classification_id}@example.com>"
    classification = EmailClassification(
        classification_id=classification_id,
        stable_message_identity=f"account-a:message-id:{rfc_message_id}",
        provider_locator=EmailProviderLocator(
            account_id="account-a",
            folder="INBOX",
            uidvalidity=42,
            uid=7,
            rfc_message_id=rfc_message_id,
        ),
        category=decision.category,
        confidence=decision.confidence,
        margin=decision.margin,
        probabilities=dict(decision.probabilities),
        model_id=decision.model_id,
        config_version=decision.config_version,
        status=decision.status,
        classification_source="model",
        action_plan=decision.action_plan,
    )
    return store.persist_scan_result(
        classification,
        sender="sender@example.com",
        subject="pipeline test",
        model_text="__subject__pipeline test __body__body",
    )


def test_high_confidence_enabled_and_eligible_is_processed_with_immutable_plan():
    decision = _decision()

    assert decision.status is EmailClassificationStatus.PROCESSED
    assert decision.action_plan is not None
    assert decision.action_plan.action_plan_version == 1
    assert decision.action_plan.category == "work"
    assert decision.action_plan.actions == (EmailAction.LABEL,)
    assert decision.action_plan.model_id == MODEL_ID
    assert decision.action_plan.config_version == "email-config-v1"
    with pytest.raises(ValidationError):
        decision.action_plan.config_version = "mutated"


@pytest.mark.parametrize(
    ("enabled", "eligible"),
    ((False, True), (True, False)),
)
def test_high_confidence_disabled_or_model_ineligible_is_pending_without_plan(
    enabled: bool,
    eligible: bool,
):
    decision = _decision(enabled=enabled, eligible=eligible)

    assert decision.status is EmailClassificationStatus.PENDING_FEEDBACK
    assert decision.action_plan is None


def test_below_threshold_is_pending_without_plan():
    decision = _decision(confidence=0.79)

    assert decision.status is EmailClassificationStatus.PENDING_FEEDBACK
    assert decision.action_plan is None


def test_model_action_plan_freezes_authorized_and_ineligible_action_evidence(
    tmp_path: Path,
):
    config = EmailCategoryConfig(
        category="work",
        description="work",
        threshold=0.8,
        actions=(EmailAction.LABEL, EmailAction.TRASH),
        action_parameters={EmailAction.LABEL: {"labels": ["Work"]}},
        enabled=True,
        config_version="email-config:authorization-v1",
    )
    eligibility = CategoryEligibility(
        category="work",
        configured_threshold=0.8,
        validated_precision=0.96,
        validation_sample_count=30,
        auto_action_eligible=True,
        reason="label_only",
        source_model_id=MODEL_ID,
        action_eligibility={
            EmailAction.LABEL: EmailActionEligibility(
                action=EmailAction.LABEL,
                auto_action_eligible=True,
                reason="action_precision_and_support_gate_met",
                source_model_id=MODEL_ID,
                evidence_reference="email-model-eligibility:model-1:label",
            ),
            EmailAction.TRASH: EmailActionEligibility(
                action=EmailAction.TRASH,
                auto_action_eligible=False,
                reason="action_precision_gate_not_met",
                source_model_id=MODEL_ID,
                evidence_reference="email-model-eligibility:model-1:trash",
            ),
        },
    )

    decision = decide_classification(
        _prediction(),
        config,
        eligibility,
        classification_id=102,
        account_id="account-a",
        created_at=NOW,
    )

    assert decision.action_plan is not None
    assert decision.action_plan.actions == (EmailAction.LABEL,)
    records = {
        record.action_type: record
        for record in decision.action_plan.action_authorizations
    }
    assert records[EmailAction.LABEL].authorized is True
    assert records[EmailAction.LABEL].authorization_source == "model_eligibility"
    assert records[EmailAction.TRASH].authorized is False
    assert (
        records[EmailAction.TRASH].ineligible_reason == "action_precision_gate_not_met"
    )
    assert records[EmailAction.TRASH].parameters == {}
    store = EmailStore(tmp_path / "authorization-roundtrip.sqlite3")
    persisted = _persist_decision(store, decision, classification_id=102)
    assert (
        persisted["action_plan"]["action_authorizations"]
        == (decision.action_plan.model_dump(mode="json")["action_authorizations"])
    )
    with sqlite3.connect(store.path) as db:
        encoded_snapshot = db.execute(
            "select authorization_snapshot_json from email_action_plans"
        ).fetchone()[0]
    assert encoded_snapshot is not None
    assert (
        json.loads(encoded_snapshot)
        == persisted["action_plan"]["action_authorizations"]
    )


def test_prediction_and_eligibility_model_mismatch_fails_closed():
    eligibility = _eligibility()
    prediction = EmailModelPrediction(
        category="work",
        confidence=0.99,
        margin=0.90,
        probabilities={"work": 0.99},
        model_id="email-model:different-active-model",
    )

    decision = decide_classification(
        prediction,
        _config(),
        eligibility,
        classification_id=103,
        account_id="account-a",
        created_at=NOW,
    )

    assert decision.status is EmailClassificationStatus.PENDING_FEEDBACK
    assert decision.action_plan is None


def test_pending_confirmation_records_feedback_then_current_config_plan_without_task(
    tmp_path: Path,
):
    store = EmailStore(tmp_path / "email.sqlite3")
    pending = _persist_decision(store, _decision(confidence=0.79))
    store.upsert_config(
        category=_legacy_store_category("junk"),
        description="junk",
        threshold=0.97,
        actions=(EmailAction.UNSUBSCRIBE,),
        action_parameters={},
        enabled=True,
        config_version="junk-v3",
    )

    application = apply_human_confirmation(
        store,
        pending["id"],
        "junk",
        feedback_request_id="feedback-pending-confirmation",
        expected_current_action_plan_id=None,
        now=NOW,
    )

    assert application is not None
    confirmed = application.confirmed
    assert confirmed["status"] == "processed"
    assert confirmed["classification_source"] == "user"
    assert confirmed["action_plan"]["category"] == "junk"
    assert confirmed["action_plan"]["config_version"] == "junk-v3"
    assert confirmed["action_plan"]["model_id"] == MODEL_ID
    assert confirmed["action_plan"]["actions"] == ["unsubscribe"]
    assert EmailAction.AUTO_REPLY.value not in confirmed["action_plan"]["actions"]
    [authorization] = confirmed["action_plan"]["action_authorizations"]
    assert authorization["action_type"] == "unsubscribe"
    assert authorization["authorized"] is True
    assert authorization["authorization_source"] == "user_confirmation"
    assert authorization["source_model_id"] == MODEL_ID
    assert authorization["config_version"] == "junk-v3"
    assert store.list_training_examples()[0]["label"] == "junk"
    with sqlite3.connect(store.path) as db:
        table_names = {
            row[0]
            for row in db.execute("select name from sqlite_master where type='table'")
        }
        assert db.execute("select count(*) from email_actions").fetchone()[0] == 0
    assert "reply_tasks" not in table_names
    assert "agent_tasks" not in table_names


def test_processed_correction_appends_feedback_and_plan_without_replaying_history(
    tmp_path: Path,
):
    store = EmailStore(tmp_path / "email.sqlite3")
    processed = _persist_decision(store, _decision())
    with sqlite3.connect(store.path) as db:
        old_action = db.execute("select action_id from email_actions").fetchone()[0]
    store.append_action_attempt(
        action_id=old_action,
        attempt_number=1,
        status="done",
        provider_operation="add_label",
        provider_target="work",
        provider_result_id="provider-receipt-1",
        error="",
        started_at=NOW.isoformat(),
        finished_at=NOW.isoformat(),
    )
    store.upsert_config(
        category=_legacy_store_category("personal"),
        description="personal",
        threshold=0.97,
        actions=(EmailAction.ARCHIVE,),
        action_parameters={},
        enabled=True,
        config_version="personal-v4",
    )

    application = apply_human_confirmation(
        store,
        processed["id"],
        "personal",
        feedback_request_id="feedback-processed-correction",
        expected_current_action_plan_id=processed["current_action_plan_id"],
        now=NOW,
    )

    assert application is not None
    corrected = application.confirmed
    assert corrected["classification_source"] == "user"
    assert corrected["confirmed_category"] == "personal"
    assert corrected["current_action_plan_id"] != processed["current_action_plan_id"]
    assert corrected["action_plan"]["action_plan_version"] == 2
    assert corrected["action_plan"]["model_id"] == MODEL_ID
    assert corrected["action_plan"]["config_version"] == "personal-v4"
    assert store.list_training_examples()[0]["label"] == "personal"

    with sqlite3.connect(store.path) as db:
        db.row_factory = sqlite3.Row
        plans = db.execute(
            "select * from email_action_plans order by action_plan_version"
        ).fetchall()
        actions = db.execute(
            "select * from email_actions order by created_at, action_id"
        ).fetchall()
        attempts = db.execute(
            "select * from email_action_attempts order by id"
        ).fetchall()

    assert [
        (row["action_plan_version"], row["model_id"], row["config_version"])
        for row in plans
    ] == [
        (1, MODEL_ID, "email-config-v1"),
        (2, MODEL_ID, "personal-v4"),
    ]
    assert {row["action_type"] for row in actions} == {"label", "archive"}
    assert (
        next(row for row in actions if row["action_id"] == old_action)["status"]
        == "done"
    )
    assert [(row["action_id"], row["provider_result_id"]) for row in attempts] == [
        (old_action, "provider-receipt-1")
    ]
    assert all(
        row["config_version"] in {"email-config-v1", "personal-v4"} for row in actions
    )


def test_human_confirmation_uses_primary_key_lookup_not_classification_paging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    store = EmailStore(tmp_path / "email.sqlite3")
    pending = _persist_decision(store, _decision(confidence=0.79))

    def reject_paging(**_kwargs):
        raise AssertionError("human confirmation must not page through classifications")

    monkeypatch.setattr(store, "list_classifications", reject_paging)

    application = apply_human_confirmation(
        store,
        pending["id"],
        "work",
        feedback_request_id="feedback-primary-key-lookup",
        expected_current_action_plan_id=None,
        now=NOW,
    )

    assert application is not None
    confirmed = application.confirmed
    assert confirmed["status"] == "processed"


def test_processed_correction_reads_config_after_acquiring_write_lease(
    tmp_path: Path,
):
    database = tmp_path / "email.sqlite3"
    setup_store = EmailStore(database)
    processed = _persist_decision(setup_store, _decision())
    setup_store.upsert_config(
        category=_legacy_store_category("personal"),
        description="personal",
        threshold=0.97,
        actions=(EmailAction.ARCHIVE,),
        action_parameters={},
        enabled=True,
        config_version="personal-v1",
    )

    correction_begin_attempted = Event()

    class SignalingConnection(sqlite3.Connection):
        def execute(self, sql, parameters=(), /):
            if " ".join(sql.lower().split()) == "begin immediate":
                correction_begin_attempted.set()
            return super().execute(sql, parameters)

    class SignalingStore(EmailStore):
        def _connect(self):
            db = sqlite3.connect(
                self.path,
                timeout=30,
                factory=SignalingConnection,
            )
            db.execute("pragma busy_timeout = 30000")
            db.execute("pragma foreign_keys = on")
            db.row_factory = sqlite3.Row
            return db

    correction_store = SignalingStore(database)
    correction_begin_attempted.clear()
    writer = sqlite3.connect(database, timeout=30)
    writer.execute("begin immediate")
    writer.execute(
        """
        update email_category_configs
        set actions_json='["move"]',
            action_parameters_json='{"move":{"target_folder":"Important"}}',
            config_version='personal-v2'
        where category_key='personal'
        """
    )
    results = []
    failures = []

    def correct():
        try:
            results.append(
                apply_human_confirmation(
                    correction_store,
                    processed["id"],
                    "personal",
                    feedback_request_id="feedback-config-lease",
                    expected_current_action_plan_id=processed["current_action_plan_id"],
                    now=NOW,
                )
            )
        except BaseException as exc:
            failures.append(exc)

    thread = Thread(target=correct)
    thread.start()
    assert correction_begin_attempted.wait(timeout=2)
    writer.commit()
    writer.close()
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert failures == []
    assert len(results) == 1
    application = results[0]
    assert application is not None
    corrected = application.confirmed
    assert corrected["config_version"] == "personal-v2"
    assert corrected["action_plan"]["config_version"] == "personal-v2"
    assert corrected["action_plan"]["actions"] == ["move"]
    assert corrected["action_plan"]["action_parameters"] == {
        "move": {"target_folder": "Important"}
    }
    with sqlite3.connect(database) as db:
        committed_config_version = db.execute(
            "select config_version from email_category_configs "
            "where category_key='personal'"
        ).fetchone()[0]
        current_plan_and_action = db.execute(
            """
            select p.config_version, a.config_version
            from email_classifications c
            join email_action_plans p on p.action_plan_id=c.current_action_plan_id
            join email_actions a on a.action_plan_id=p.action_plan_id
            where c.id=? and a.action_type='move'
            """,
            (processed["id"],),
        ).fetchone()
    assert committed_config_version == "personal-v2"
    assert current_plan_and_action == ("personal-v2", "personal-v2")


def test_exact_feedback_replay_returns_original_result_without_new_history(
    tmp_path: Path,
):
    store = EmailStore(tmp_path / "email.sqlite3")
    pending = _persist_decision(store, _decision(confidence=0.79))

    first = apply_human_confirmation(
        store,
        pending["id"],
        "personal",
        feedback_request_id="feedback-first-1",
        expected_current_action_plan_id=None,
        now=NOW,
    )
    replay = apply_human_confirmation(
        store,
        pending["id"],
        "personal",
        feedback_request_id="feedback-first-1",
        expected_current_action_plan_id=None,
        now=NOW,
    )

    assert first is not None
    assert replay is not None
    assert first.applied is True
    assert first.replayed is False
    assert replay.applied is False
    assert replay.replayed is True
    assert replay.feedback_request_id == "feedback-first-1"
    assert replay.confirmed == first.confirmed
    with sqlite3.connect(store.path) as db:
        assert (
            db.execute("select count(*) from email_feedback_requests").fetchone()[0]
            == 1
        )
        assert db.execute("select count(*) from email_action_plans").fetchone()[0] == 1
        assert db.execute("select count(*) from email_actions").fetchone()[0] == 0


def test_feedback_replay_after_later_correction_returns_original_result(
    tmp_path: Path,
):
    store = EmailStore(tmp_path / "email.sqlite3")
    pending = _persist_decision(store, _decision(confidence=0.79))
    first = apply_human_confirmation(
        store,
        pending["id"],
        "work",
        feedback_request_id="feedback-original",
        expected_current_action_plan_id=None,
        now=NOW,
    )
    assert first is not None
    corrected = apply_human_confirmation(
        store,
        pending["id"],
        "personal",
        feedback_request_id="feedback-correction",
        expected_current_action_plan_id=first.resulting_action_plan_id,
        now=NOW,
    )
    assert corrected is not None

    replay = apply_human_confirmation(
        store,
        pending["id"],
        "work",
        feedback_request_id="feedback-original",
        expected_current_action_plan_id=None,
        now=NOW,
    )

    assert replay is not None
    assert replay.replayed is True
    assert replay.confirmed == first.confirmed
    assert replay.resulting_action_plan_id != corrected.resulting_action_plan_id


@pytest.mark.parametrize(
    ("classification_offset", "category", "expected_pointer"),
    (
        (0, "personal", None),
        (0, "notification", "unexpected-plan"),
        (1, "notification", None),
    ),
)
def test_feedback_request_id_reuse_with_different_intent_conflicts(
    tmp_path: Path,
    classification_offset: int,
    category: EmailCategoryKey,
    expected_pointer: str | None,
):
    store = EmailStore(tmp_path / "email.sqlite3")
    first_row = _persist_decision(store, _decision(confidence=0.79))
    second_row = _persist_decision(
        store,
        _decision(confidence=0.79),
        classification_id=102,
    )
    first = apply_human_confirmation(
        store,
        first_row["id"],
        "notification",
        feedback_request_id="feedback-stable-id",
        expected_current_action_plan_id=None,
        now=NOW,
    )
    assert first is not None
    target = second_row if classification_offset else first_row

    with pytest.raises(EmailClassificationConflict):
        apply_human_confirmation(
            store,
            target["id"],
            category,
            feedback_request_id="feedback-stable-id",
            expected_current_action_plan_id=expected_pointer,
            now=NOW,
        )


def test_unknown_request_against_processed_row_requires_current_pointer(
    tmp_path: Path,
):
    store = EmailStore(tmp_path / "email.sqlite3")
    pending = _persist_decision(store, _decision(confidence=0.79))
    first = apply_human_confirmation(
        store,
        pending["id"],
        "work",
        feedback_request_id="feedback-first",
        expected_current_action_plan_id=None,
        now=NOW,
    )
    assert first is not None

    with pytest.raises(EmailClassificationConflict):
        apply_human_confirmation(
            store,
            pending["id"],
            "work",
            feedback_request_id="feedback-unknown",
            expected_current_action_plan_id=None,
            now=NOW,
        )


def test_concurrent_different_requests_from_same_plan_pointer_allow_one_correction(
    tmp_path: Path,
):
    store = EmailStore(tmp_path / "email.sqlite3")
    pending = _persist_decision(store, _decision(confidence=0.79))
    first = apply_human_confirmation(
        store,
        pending["id"],
        "work",
        feedback_request_id="feedback-first",
        expected_current_action_plan_id=None,
        now=NOW,
    )
    assert first is not None
    pointer = first.resulting_action_plan_id
    ready = Barrier(2)
    results = []

    def correct(request_id: str, category: EmailCategoryKey):
        ready.wait()
        try:
            return apply_human_confirmation(
                EmailStore(store.path),
                pending["id"],
                category,
                feedback_request_id=request_id,
                expected_current_action_plan_id=pointer,
                now=NOW,
            )
        except EmailClassificationConflict as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda args: correct(*args),
                (
                    ("feedback-correction-a", "personal"),
                    ("feedback-correction-b", "notification"),
                ),
            )
        )

    applied = [result for result in results if not isinstance(result, Exception)]
    conflicts = [result for result in results if isinstance(result, Exception)]
    assert len(applied) == 1
    assert applied[0] is not None and applied[0].applied is True
    assert len(conflicts) == 1
    with sqlite3.connect(store.path) as db:
        assert (
            db.execute("select count(*) from email_feedback_requests").fetchone()[0]
            == 2
        )
        assert db.execute("select count(*) from email_action_plans").fetchone()[0] == 2
