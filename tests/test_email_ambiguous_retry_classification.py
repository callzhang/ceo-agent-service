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
