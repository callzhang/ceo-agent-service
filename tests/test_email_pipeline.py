from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from threading import Barrier, Event, Thread

import pytest
from pydantic import ValidationError

from app.email_classifier_contracts import (
    EmailAction,
    EmailCategory,
    EmailClassification,
    EmailClassificationStatus,
    EmailProviderLocator,
)
from app.email_classifier_training import CategoryEligibility
from app.email_pipeline import (
    EmailCategoryConfig,
    EmailModelPrediction,
    apply_human_confirmation,
    decide_classification,
)
from app.email_store import EmailClassificationConflict, EmailStore


NOW = datetime(2026, 8, 30, 16, 0, tzinfo=timezone.utc)
MODEL_ID = "email-tfidf-lr-20260830T160000Z-1234567890abcdef"


def _prediction(*, confidence: float = 0.93) -> EmailModelPrediction:
    return EmailModelPrediction(
        category=EmailCategory.WORK,
        confidence=confidence,
        margin=0.41,
        probabilities={"work": confidence, "important": 1.0 - confidence},
        model_id=MODEL_ID,
    )


def _config(
    *,
    category: EmailCategory = EmailCategory.WORK,
    enabled: bool = True,
    threshold: float = 0.8,
    config_version: str = "email-config-v1",
    actions: tuple[EmailAction, ...] = (EmailAction.LABEL,),
) -> EmailCategoryConfig:
    parameters = (
        {EmailAction.LABEL: {"labels": [category.value]}}
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
        category=EmailCategory.WORK,
        configured_threshold=0.8,
        validated_precision=0.99 if eligible else 0.70,
        validation_sample_count=30,
        auto_action_eligible=eligible,
        reason="eligible" if eligible else "precision_gate_not_met",
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
    assert decision.action_plan.category is EmailCategory.WORK
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


def test_pending_confirmation_records_feedback_then_current_config_plan_without_task(
    tmp_path: Path,
):
    store = EmailStore(tmp_path / "email.sqlite3")
    pending = _persist_decision(store, _decision(confidence=0.79))
    store.upsert_config(
        category=EmailCategory.IMPORTANT,
        description="important",
        threshold=0.97,
        actions=(EmailAction.AUTO_REPLY, EmailAction.UNSUBSCRIBE),
        action_parameters={
            EmailAction.AUTO_REPLY: {"instruction": "reply briefly"},
        },
        enabled=True,
        config_version="important-v3",
    )

    application = apply_human_confirmation(
        store,
        pending["id"],
        EmailCategory.IMPORTANT,
        feedback_request_id="feedback-pending-confirmation",
        expected_current_action_plan_id=None,
        now=NOW,
    )

    assert application is not None
    confirmed = application.confirmed
    assert confirmed["status"] == "processed"
    assert confirmed["classification_source"] == "user"
    assert confirmed["action_plan"]["category"] == "important"
    assert confirmed["action_plan"]["config_version"] == "important-v3"
    assert confirmed["action_plan"]["model_id"] == MODEL_ID
    assert confirmed["action_plan"]["actions"] == ["auto_reply", "unsubscribe"]
    assert store.list_training_examples()[0]["label"] == "important"
    with sqlite3.connect(store.path) as db:
        table_names = {
            row[0]
            for row in db.execute(
                "select name from sqlite_master where type='table'"
            )
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
        old_action = db.execute(
            "select action_id from email_actions"
        ).fetchone()[0]
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
        category=EmailCategory.IMPORTANT,
        description="important",
        threshold=0.97,
        actions=(EmailAction.ARCHIVE, EmailAction.UNSUBSCRIBE),
        action_parameters={},
        enabled=True,
        config_version="important-v4",
    )

    application = apply_human_confirmation(
        store,
        processed["id"],
        EmailCategory.IMPORTANT,
        feedback_request_id="feedback-processed-correction",
        expected_current_action_plan_id=processed["current_action_plan_id"],
        now=NOW,
    )

    assert application is not None
    corrected = application.confirmed
    assert corrected["classification_source"] == "user"
    assert corrected["confirmed_category"] == "important"
    assert corrected["current_action_plan_id"] != processed["current_action_plan_id"]
    assert corrected["action_plan"]["action_plan_version"] == 2
    assert corrected["action_plan"]["model_id"] == MODEL_ID
    assert corrected["action_plan"]["config_version"] == "important-v4"
    assert store.list_training_examples()[0]["label"] == "important"

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
        (2, MODEL_ID, "important-v4"),
    ]
    assert {row["action_type"] for row in actions} == {"label", "archive"}
    assert next(row for row in actions if row["action_id"] == old_action)[
        "status"
    ] == "done"
    assert [(row["action_id"], row["provider_result_id"]) for row in attempts] == [
        (old_action, "provider-receipt-1")
    ]
    assert all(
        row["config_version"] in {"email-config-v1", "important-v4"}
        for row in actions
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
        EmailCategory.WORK,
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
        category=EmailCategory.IMPORTANT,
        description="important",
        threshold=0.97,
        actions=(EmailAction.ARCHIVE,),
        action_parameters={},
        enabled=True,
        config_version="important-v1",
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
            config_version='important-v2'
        where category='important'
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
                    EmailCategory.IMPORTANT,
                    feedback_request_id="feedback-config-lease",
                    expected_current_action_plan_id=processed[
                        "current_action_plan_id"
                    ],
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
    assert corrected["config_version"] == "important-v2"
    assert corrected["action_plan"]["config_version"] == "important-v2"
    assert corrected["action_plan"]["actions"] == ["move"]
    assert corrected["action_plan"]["action_parameters"] == {
        "move": {"target_folder": "Important"}
    }
    with sqlite3.connect(database) as db:
        committed_config_version = db.execute(
            "select config_version from email_category_configs where category='important'"
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
    assert committed_config_version == "important-v2"
    assert current_plan_and_action == ("important-v2", "important-v2")


def test_exact_feedback_replay_returns_original_result_without_new_history(
    tmp_path: Path,
):
    store = EmailStore(tmp_path / "email.sqlite3")
    pending = _persist_decision(store, _decision(confidence=0.79))

    first = apply_human_confirmation(
        store,
        pending["id"],
        EmailCategory.IMPORTANT,
        feedback_request_id="feedback-first-1",
        expected_current_action_plan_id=None,
        now=NOW,
    )
    replay = apply_human_confirmation(
        store,
        pending["id"],
        EmailCategory.IMPORTANT,
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
        assert db.execute("select count(*) from email_feedback_requests").fetchone()[0] == 1
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
        EmailCategory.WORK,
        feedback_request_id="feedback-original",
        expected_current_action_plan_id=None,
        now=NOW,
    )
    assert first is not None
    corrected = apply_human_confirmation(
        store,
        pending["id"],
        EmailCategory.IMPORTANT,
        feedback_request_id="feedback-correction",
        expected_current_action_plan_id=first.resulting_action_plan_id,
        now=NOW,
    )
    assert corrected is not None

    replay = apply_human_confirmation(
        store,
        pending["id"],
        EmailCategory.WORK,
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
        (0, EmailCategory.PERSONAL, None),
        (0, EmailCategory.IMPORTANT, "unexpected-plan"),
        (1, EmailCategory.IMPORTANT, None),
    ),
)
def test_feedback_request_id_reuse_with_different_intent_conflicts(
    tmp_path: Path,
    classification_offset: int,
    category: EmailCategory,
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
        EmailCategory.IMPORTANT,
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
        EmailCategory.WORK,
        feedback_request_id="feedback-first",
        expected_current_action_plan_id=None,
        now=NOW,
    )
    assert first is not None

    with pytest.raises(EmailClassificationConflict):
        apply_human_confirmation(
            store,
            pending["id"],
            EmailCategory.WORK,
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
        EmailCategory.WORK,
        feedback_request_id="feedback-first",
        expected_current_action_plan_id=None,
        now=NOW,
    )
    assert first is not None
    pointer = first.resulting_action_plan_id
    ready = Barrier(2)
    results = []

    def correct(request_id: str, category: EmailCategory):
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
                    ("feedback-correction-a", EmailCategory.IMPORTANT),
                    ("feedback-correction-b", EmailCategory.PERSONAL),
                ),
            )
        )

    applied = [result for result in results if not isinstance(result, Exception)]
    conflicts = [result for result in results if isinstance(result, Exception)]
    assert len(applied) == 1
    assert applied[0] is not None and applied[0].applied is True
    assert len(conflicts) == 1
    with sqlite3.connect(store.path) as db:
        assert db.execute("select count(*) from email_feedback_requests").fetchone()[0] == 2
        assert db.execute("select count(*) from email_action_plans").fetchone()[0] == 2
