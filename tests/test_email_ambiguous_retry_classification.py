from __future__ import annotations

from importlib import import_module
from types import SimpleNamespace

import pytest

from app.email_classifier_contracts import EmailAction


def _move_action():
    return SimpleNamespace(
        action_type=EmailAction.MOVE,
        parameters={"target_folder": "Archive"},
        locator=SimpleNamespace(
            stable_message_identity="account-1:message-id:<message@example.com>",
        ),
    )


def test_ambiguous_message_lookup_is_nonretryable_and_never_writes():
    module = import_module("app.email_provider_actions")

    class Provider:
        def read_state(self, locator, *, action_type):
            raise module.ImapMessageLookupAmbiguous(
                "message lookup was ambiguous"
            )

        def apply(self, *args, **kwargs):
            pytest.fail("an ambiguous message lookup must not mutate the mailbox")

    result = module.DeterministicEmailActionExecutor(Provider()).execute(
        _move_action()
    )

    assert result.status == "failed"
    assert result.error == (
        "provider_read_failed:imap.uid_lookup_ambiguous:"
        "message lookup was ambiguous"
    )
    assert result.retryable is False


def test_transient_network_read_failure_remains_retryable():
    module = import_module("app.email_provider_actions")

    class Provider:
        def read_state(self, locator, *, action_type):
            raise TimeoutError("provider read timed out")

        def apply(self, *args, **kwargs):
            pytest.fail("a failed read must not issue a mailbox mutation")

    result = module.DeterministicEmailActionExecutor(Provider()).execute(
        _move_action()
    )

    assert result.status == "failed"
    assert result.retryable is True


def test_exhausted_ambiguous_move_is_not_claimed_for_another_retry(tmp_path):
    from app.email_classifier_contracts import (
        EmailCategory,
        EmailClassificationStatus,
    )
    from app.email_store import (
        EMAIL_DIRECT_ACTION_LOCATOR_SQL,
        EmailStore,
        _failed_action_retriable_sql,
    )
    from tests.test_email_store import _classification, _persist_scan

    store = EmailStore(tmp_path / "ambiguous-action.sqlite3")
    classification = _classification(
        status=EmailClassificationStatus.PROCESSED,
        message_id="ambiguous-exhaustion",
        category=EmailCategory.WORK,
        actions=(EmailAction.MOVE,),
        action_parameters={EmailAction.MOVE: {"target_folder": "Archive"}},
    )
    _persist_scan(store, classification)

    for attempt_number in range(1, 5):
        finished_at = f"2026-10-{attempt_number:02d}T12:00:00+00:00"
        action = store.claim_next_direct_action(claimed_at=finished_at)
        assert action is not None
        assert action.attempt_number == attempt_number
        store.complete_direct_action_attempt(
            action,
            status="failed",
            provider_operation="READ",
            provider_target=action.locator.stable_message_identity,
            provider_result_id="",
            error=(
                "provider_read_failed:imap.uid_lookup_ambiguous:"
                "message lookup was ambiguous"
            ),
            finished_at=finished_at,
            retryable=True,
        )

    next_attempt = store.claim_next_direct_action(
        claimed_at="2026-11-01T12:00:00+00:00"
    )

    assert next_attempt is None
    action_state = store.list_mailbox_action_states(
        [classification.classification_id]
    )[classification.classification_id][0]
    assert action_state["retriable"] is False
    with store._connect() as db:
        retriable_count = db.execute(
            f"select count(*) from ({EMAIL_DIRECT_ACTION_LOCATOR_SQL}) action "
            f"where action.action_id=? and {_failed_action_retriable_sql('action')}",
            (action.action_id,),
        ).fetchone()[0]
    assert retriable_count == 0
