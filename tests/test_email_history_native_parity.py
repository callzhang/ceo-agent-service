"""Six public Email history calls against genuine 83c1d7f0 captured behavior."""

from types import SimpleNamespace
import hashlib
import json
import sqlite3

import pytest

from app.action_email_history_storage import insert_email_history
from app.email_store import EmailStore
from app.store import AutoReplyStore


def exercise_email_history_public(email: EmailStore, prefix: str) -> dict:
    """Run deterministic local calls; also usable on a genuine native source."""
    times = iter(f"2026-10-17T12:00:{second:02d}+00:00" for second in range(40))
    original_now = email._now
    email._now = lambda: next(times)
    stable = "copy-only-history:" + prefix
    model = "copy-only-model:" + prefix
    outcome = SimpleNamespace(
        stable_message_identity=stable, model_id=model,
        predicted_category=None, threshold=None, probability=None,
        important=None, action_outcome="copy-only-recorded",
    )
    kwargs = dict(
        account_id="copy-only-account:" + prefix,
        stable_message_identity=stable, model_id=model,
        classification_id=-5, action_plan_id="copy-only-plan:" + prefix,
        action_ids=("copy-only-action:" + prefix,),
        predicted_category="copy-only-category", threshold=1.25,
        probability=-0.25, important=True,
    )
    try:
        first = email.record_historical_classification_outcome(outcome)
        repeated = email.record_historical_classification_outcome(outcome)
        history = [row for row in email.list_historical_classification_history()
                   if row["event_id"] == first["event_id"]]
        started = email.begin_historical_operation(**kwargs)
        start_repeat = email.begin_historical_operation(**kwargs)
        processing_before = [row for row in email.list_processing_historical_operations()
                             if row["operation_id"] == started["operation_id"]]
        email.touch_historical_operation(model_id=model, stable_message_identity=stable)
        touched = email.begin_historical_operation(**kwargs)
        email.complete_historical_operation(SimpleNamespace(
            stable_message_identity=stable, model_id=model,
            action_outcome="copy-only-completed",
        ))
        terminal = email.begin_historical_operation(**kwargs)
        email.touch_historical_operation(model_id=model, stable_message_identity=stable)
        terminal_after_touch = email.begin_historical_operation(**kwargs)
        pending = email.begin_historical_operation(
            **(kwargs | {"stable_message_identity": "copy-only-pending:" + prefix})
        )
        processing_after = [row for row in email.list_processing_historical_operations()
                            if row["operation_id"] == pending["operation_id"]]
    finally:
        email._now = original_now
    assert first == repeated
    assert started == start_repeat
    assert touched["created_at"] == started["created_at"]
    assert touched["updated_at"] != started["updated_at"]
    assert terminal["status"] == "terminal"
    assert terminal == terminal_after_touch
    assert len(processing_before) == 1
    assert processing_after == [pending]
    return dict(first=first, history=history, started=started,
                touched=touched, terminal=terminal, pending=pending,
                processing_before=processing_before,
                processing_after=processing_after)


def test_current_six_public_methods_match_genuine_native_shape(tmp_path):
    path = tmp_path / "public.sqlite"
    AutoReplyStore(path)
    email = EmailStore(path)
    observed = exercise_email_history_public(email, "s")
    assert hashlib.sha256(json.dumps(observed, sort_keys=True, default=list).encode()).hexdigest() == (
        "946c053572521acfb180741b5d160c3c137cfad01bf75589a7f06b5436807c9f"
    )
    assert observed["history"] == [observed["first"]]
    assert observed["first"]["created_at"] == "2026-10-17T12:00:00+00:00"
    assert observed["started"]["classification_id"] == -5
    assert observed["started"]["threshold"] == 1.25
    assert observed["started"]["probability"] == -0.25
    assert observed["started"]["important"] is True
    assert observed["started"]["action_ids"] == ("copy-only-action:s",)
    assert observed["terminal"]["action_outcome"] == "copy-only-completed"


def test_public_begin_preserves_native_insert_or_ignore_conflicts(tmp_path):
    path = tmp_path / "conflicts.sqlite"
    email = EmailStore(path)
    with sqlite3.connect(path) as db:
        insert_email_history(db, "email_historical_operations", {
            "operation_id": "different", "account_id": "account",
            "stable_message_identity": "same", "model_id": "model",
            "classification_id": 1, "action_plan_id": "plan",
            "action_ids_json": "[]", "predicted_category": "custom",
            "threshold": .5, "probability": .5, "important": 1,
            "status": "processing", "action_outcome": "",
            "created_at": "time", "updated_at": "time",
        })
    args = dict(account_id="account", stable_message_identity="same",
                model_id="model", classification_id=1, action_plan_id="plan",
                action_ids=("one",), predicted_category="custom",
                threshold=.5, probability=.5, important=True)
    # Genuine deployed 83c1d7f0 returned AssertionError for both cases:
    # its public INSERT OR IGNORE consumed the UNIQUE and CHECK failures, then
    # the derived operation ID had no row to read.
    with pytest.raises(AssertionError):
        email.begin_historical_operation(**args)
    with pytest.raises(AssertionError):
        email.begin_historical_operation(**(args | {
            "stable_message_identity": "other", "important": 2,
        }))
