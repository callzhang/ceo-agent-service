"""The one-call unsubscribe path: open, operate, and bring back evidence."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.email_classifier_contracts import (
    EmailAction,
    EmailCategory,
    EmailClassification,
    EmailClassificationStatus,
    build_versioned_email_action_plan,
)
from app.email_store import EmailStore, email_action_identity
from app.email_task_adapter import email_conversation_id
from app.email_unsubscribe import (
    EmailUnsubscribeEffect,
    UnsubscribeBrowserError,
    UnsubscribeBrowserFailure,
    UnsubscribeDiscoveredControl,
    UnsubscribeObservation,
    UnsubscribeOperationKind,
    UnsubscribeOutcome,
    UnsubscribePageState,
    UnsubscribeTerminalReceipt,
    extract_unsubscribe_entries,
)
from app.email_unsubscribe_direct import (
    MAX_DIRECT_UNSUBSCRIBE_CONTROLS,
    DirectEmailUnsubscribeOperation,
    opening_operation,
    run_direct_unsubscribe,
)
from app.store import AutoReplyStore

PRIVATE_URL = "https://news.example.com/unsubscribe?token=private-token"
ACCOUNT_ID = "account-primary"
MESSAGE_IDENTITY = "account-primary:message-id:<mail-77@example.com>"
THREAD_IDENTITY = "thread-77"
CLASSIFICATION_ID = 77
PLAN = build_versioned_email_action_plan(
    action_plan_version=1,
    classification_id=CLASSIFICATION_ID,
    account_id=ACCOUNT_ID,
    category=EmailCategory.JUNK,
    classification_source="user",
    confidence=1.0,
    model_id="email-model:test-direct",
    config_version="email-config:test-direct",
    actions=(EmailAction.UNSUBSCRIBE,),
    action_parameters={},
    created_at=datetime(2026, 9, 11, 8, 0, tzinfo=timezone.utc),
)
ACTION_IDENTITY = email_action_identity(
    account_id=ACCOUNT_ID,
    stable_message_identity=MESSAGE_IDENTITY,
    action_type=EmailAction.UNSUBSCRIBE,
    action_plan_version=PLAN.action_plan_version,
)
ENTRY = extract_unsubscribe_entries(list_unsubscribe=f"<{PRIVATE_URL}>")[0]


def _effect() -> EmailUnsubscribeEffect:
    return EmailUnsubscribeEffect(
        action_identity=ACTION_IDENTITY,
        action_plan_id=PLAN.action_plan_id,
        action_plan_version=PLAN.action_plan_version,
        classification_id=CLASSIFICATION_ID,
        account_id=ACCOUNT_ID,
        stable_message_identity=MESSAGE_IDENTITY,
        thread_identity=THREAD_IDENTITY,
        entry_reference=ENTRY.reference,
        operations=(opening_operation(ENTRY, one_click_verified=False),),
    )


def _control(kind: str, intent: str, reference: str) -> UnsubscribeDiscoveredControl:
    return UnsubscribeDiscoveredControl(reference=reference, kind=kind, intent=intent)


def _action_required(
    *controls: UnsubscribeDiscoveredControl,
    text: str = "Are you sure?",
) -> UnsubscribeObservation:
    return UnsubscribeObservation(
        state=UnsubscribePageState.ACTION_REQUIRED,
        state_reference="state-action-required",
        controls=tuple(controls),
        visible_text=text,
    )


def _terminal(
    effect: EmailUnsubscribeEffect,
    state: UnsubscribePageState,
    text: str,
) -> UnsubscribeObservation:
    return UnsubscribeObservation(
        state=state,
        state_reference=f"state-{state.value}",
        receipt=UnsubscribeTerminalReceipt(
            receipt_id=f"unsubscribe-receipt:{effect.effect_digest[:24]}:{state.value}",
            evidence="terminal-page",
            entry_reference=effect.entry_reference,
            effect_digest=effect.effect_digest,
        ),
        visible_text=text,
    )


class ScriptedBrowser:
    """Return one prepared observation per operation, recording the calls."""

    def __init__(self, observations: list[object]) -> None:
        self.observations = list(observations)
        self.calls: list[tuple[str, str]] = []

    def execute_operation(self, effect, private_url, operation):
        del effect, private_url
        self.calls.append((operation.kind.value, operation.target_reference))
        if not self.observations:
            raise AssertionError("browser was driven past its script")
        nxt = self.observations.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


def test_a_page_that_is_already_terminal_needs_one_operation() -> None:
    effect = _effect()
    browser = ScriptedBrowser(
        [_terminal(effect, UnsubscribePageState.DONE, "You've unsubscribed.")]
    )

    result = run_direct_unsubscribe(browser, effect, ENTRY)

    assert result.outcome is UnsubscribeOutcome.DONE
    assert browser.calls == [("open_entry", ENTRY.reference)]
    assert "unsubscribed" in result.result_text
    assert result.receipt is not None


def test_a_confirmation_button_is_operated_without_another_agent_turn() -> None:
    effect = _effect()
    browser = ScriptedBrowser(
        [
            _action_required(_control("form", "unsubscribe", "control-confirm")),
            _terminal(effect, UnsubscribePageState.DONE, "You are unsubscribed."),
        ]
    )

    result = run_direct_unsubscribe(browser, effect, ENTRY)

    assert result.outcome is UnsubscribeOutcome.DONE
    assert browser.calls == [
        ("open_entry", ENTRY.reference),
        ("submit_form", "control-confirm"),
    ]
    # Both pages are in the journal, so the operation is reviewable end to end.
    assert [step.operation for step in result.journal] == ["open_entry", "submit_form"]


def test_the_control_that_says_what_it_does_is_preferred() -> None:
    effect = _effect()
    browser = ScriptedBrowser(
        [
            _action_required(
                _control("link", "continue", "control-cookie-banner"),
                _control("link", "unsubscribe", "control-real"),
            ),
            _terminal(effect, UnsubscribePageState.DONE, "Unsubscribed."),
        ]
    )

    run_direct_unsubscribe(browser, effect, ENTRY)

    assert browser.calls[1] == ("click_confirmation", "control-real")


def test_a_page_offering_nothing_operable_is_a_skip_that_keeps_its_text() -> None:
    effect = _effect()
    # A control this service does not drive: the page was read, and there is
    # still nothing here to click.
    browser = ScriptedBrowser(
        [
            _action_required(
                _control("confirmation_email", "confirm", "control-mail-me"),
                text="Thanks for reading.",
            )
        ]
    )

    result = run_direct_unsubscribe(browser, effect, ENTRY)

    assert result.outcome is UnsubscribeOutcome.SKIPPED_NO_RELIABLE_ENTRY
    assert result.disposition.retryable is False
    # A skip that records nothing cannot be reviewed later.
    assert "Thanks for reading." in result.result_text
    assert result.receipt is not None


def test_a_page_asking_for_a_person_skips_instead_of_clicking() -> None:
    effect = _effect()
    browser = ScriptedBrowser(
        [_action_required(_control("credential_handoff", "continue", "control-login"))]
    )

    result = run_direct_unsubscribe(browser, effect, ENTRY)

    assert result.outcome is UnsubscribeOutcome.SKIPPED_LOGIN_REQUIRED
    assert browser.calls == [("open_entry", ENTRY.reference)]


def test_a_captcha_is_a_terminal_skip() -> None:
    effect = _effect()
    browser = ScriptedBrowser(
        [_action_required(_control("captcha_handoff", "continue", "control-captcha"))]
    )

    result = run_direct_unsubscribe(browser, effect, ENTRY)

    assert result.outcome is UnsubscribeOutcome.SKIPPED_CAPTCHA


def test_a_page_that_never_settles_is_bounded() -> None:
    effect = _effect()
    # Every page offers one more fresh control, forever.
    observations = [
        _action_required(_control("link", "continue", f"control-{index}"))
        for index in range(MAX_DIRECT_UNSUBSCRIBE_CONTROLS + 5)
    ]
    browser = ScriptedBrowser(observations)

    result = run_direct_unsubscribe(browser, effect, ENTRY)

    assert result.outcome is UnsubscribeOutcome.SKIPPED_NO_RELIABLE_ENTRY
    assert len(browser.calls) == MAX_DIRECT_UNSUBSCRIBE_CONTROLS + 1


def test_the_same_control_is_never_operated_twice() -> None:
    effect = _effect()
    browser = ScriptedBrowser(
        [
            _action_required(_control("link", "unsubscribe", "control-loop")),
            _action_required(_control("link", "unsubscribe", "control-loop")),
        ]
    )

    result = run_direct_unsubscribe(browser, effect, ENTRY)

    assert result.outcome is UnsubscribeOutcome.SKIPPED_NO_RELIABLE_ENTRY
    assert browser.calls == [
        ("open_entry", ENTRY.reference),
        ("click_confirmation", "control-loop"),
    ]


def test_a_page_this_service_will_not_operate_is_not_retried() -> None:
    effect = _effect()
    browser = ScriptedBrowser(
        [
            UnsubscribeBrowserError(
                UnsubscribeBrowserFailure.PAGE_CONTROLS_UNMODELLED,
                "unsubscribe page state is unknown",
                observation={"text_preview": "Manage preferences in your account"},
            )
        ]
    )

    result = run_direct_unsubscribe(browser, effect, ENTRY)

    assert result.outcome is UnsubscribeOutcome.SKIPPED_NO_RELIABLE_ENTRY
    assert result.disposition.retryable is False


def test_a_browser_fault_stays_a_retryable_failure_with_its_observation() -> None:
    effect = _effect()
    browser = ScriptedBrowser(
        [
            UnsubscribeBrowserError(
                UnsubscribeBrowserFailure.OPERATION_TIMEOUT,
                "browser operation timed out",
            )
        ]
    )

    result = run_direct_unsubscribe(browser, effect, ENTRY)

    assert result.outcome is UnsubscribeOutcome.FAILED_BROWSER
    assert result.receipt is None
    assert result.error_code


def test_a_verified_one_click_header_posts_instead_of_opening_a_page() -> None:
    from dataclasses import replace

    from app.email_unsubscribe import UnsubscribeEntrySource

    entry = replace(ENTRY, source=UnsubscribeEntrySource.HEADER_ONE_CLICK_HTTPS)

    operation = opening_operation(entry, one_click_verified=True)

    assert operation.kind is UnsubscribeOperationKind.POST_ONE_CLICK
    assert (
        opening_operation(entry, one_click_verified=False).kind
        is UnsubscribeOperationKind.OPEN_ENTRY
    )


def _seed(path: Path) -> EmailStore:
    store = EmailStore(path)
    store.create_account(
        {
            "account_id": ACCOUNT_ID,
            "display_name": "Primary",
            "email_address": "derek@example.com",
            "imap_host": "imap.example.com",
            "imap_port": 993,
            "imap_tls": True,
            "imap_username": "derek@example.com",
            "imap_secret_reference": "keychain://imap-test",
            "smtp_host": "smtp.example.com",
            "smtp_port": 465,
            "smtp_tls": True,
            "smtp_username": "derek@example.com",
            "smtp_secret_reference": "keychain://smtp-test",
            "enabled": True,
            "scan_folders": ["INBOX"],
            "scan_interval_seconds": 60,
        }
    )
    store.upsert_classification(
        EmailClassification.model_validate(
            {
                "classification_id": CLASSIFICATION_ID,
                "stable_message_identity": MESSAGE_IDENTITY,
                "provider_locator": {
                    "account_id": ACCOUNT_ID,
                    "folder": "INBOX",
                    "uidvalidity": 42,
                    "uid": 77,
                    "rfc_message_id": "<mail-77@example.com>",
                    "thread_id": THREAD_IDENTITY,
                },
                "category": EmailCategory.JUNK,
                "confidence": 1.0,
                "margin": 1.0,
                "probabilities": {"junk": 1.0},
                "model_id": PLAN.model_id,
                "config_version": PLAN.config_version,
                "status": EmailClassificationStatus.PROCESSED,
                "classification_source": "user",
                "action_plan": PLAN,
            }
        ),
        sender="sender@example.com",
        subject="Newsletter",
        model_text="__subject__newsletter",
        received_at="2026-09-11T08:00:00+00:00",
    )
    return store


def _payload() -> dict[str, object]:
    return {
        "schema": "email_agent_action.v1",
        "lifecycle_version": "email_unsubscribe_audited_v2",
        "action_type": "unsubscribe",
        "action_identity": ACTION_IDENTITY,
        "action_plan_id": PLAN.action_plan_id,
        "action_plan_version": PLAN.action_plan_version,
        "classification_id": CLASSIFICATION_ID,
        "account_id": ACCOUNT_ID,
        "stable_message_identity": MESSAGE_IDENTITY,
        "thread_identity": THREAD_IDENTITY,
        "unsubscribe_entries": [
            {
                "index": ENTRY.index,
                "source": "header_https",
                "digest": ENTRY.reference.removeprefix("unsubscribe-entry:"),
                "reference": ENTRY.reference,
            }
        ],
        "unsubscribe_authentication": None,
    }


def _operation(tmp_path: Path, observations: list[object]):
    path = tmp_path / "direct-unsubscribe.sqlite3"
    email_store = _seed(path)
    task_store = AutoReplyStore(path)
    task = task_store.ensure_reply_task(
        channel="email",
        conversation_id=email_conversation_id(ACCOUNT_ID, THREAD_IDENTITY),
        conversation_title="Email unsubscribe",
        single_chat=False,
        trigger_message_id=ACTION_IDENTITY,
        trigger_create_time="2026-09-11T08:00:00+00:00",
        trigger_sender="sender@example.com",
        trigger_text="Immutable ActionPlan authorizes unsubscribe.",
        trigger_message_json=json.dumps(_payload(), sort_keys=True),
        execution_generation="generation-direct-1",
    )
    task = task_store.claim_reply_task(task.id)
    assert task is not None
    browser = ScriptedBrowser(observations)

    def resolve_entries(locator, expected_reference, *_args, **_kwargs):
        del locator, expected_reference
        return (ENTRY,)

    def run_effect(effect, entry, *, one_click_verified):
        return run_direct_unsubscribe(
            browser,
            effect,
            entry,
            one_click_verified=one_click_verified,
        )

    operation = DirectEmailUnsubscribeOperation(
        task_store=task_store,
        email_store=email_store,
        resolve_entries=resolve_entries,
        run_effect=run_effect,
    )
    return operation, task, email_store, browser


def test_one_call_unsubscribes_and_persists_the_receipt(tmp_path: Path) -> None:
    effect = _effect()
    operation, task, email_store, browser = _operation(
        tmp_path,
        [_terminal(effect, UnsubscribePageState.DONE, "You have unsubscribed.")],
    )

    result = operation.execute(task.id)

    assert result["status"] == "done"
    assert result["outcome"] == "done"
    assert "unsubscribed" in result["result_text"]
    receipt = email_store.get_email_unsubscribe_receipt(ACTION_IDENTITY)
    assert receipt is not None and receipt["outcome"] == "done"
    assert len(browser.calls) == 1


def test_a_second_call_returns_the_receipt_instead_of_unsubscribing_again(
    tmp_path: Path,
) -> None:
    effect = _effect()
    operation, task, _email_store, browser = _operation(
        tmp_path,
        [_terminal(effect, UnsubscribePageState.DONE, "You have unsubscribed.")],
    )
    first = operation.execute(task.id)

    second = operation.execute(task.id)

    assert second["status"] == "done"
    assert second["receipt_id"] == first["receipt_id"]
    # The browser was never opened a second time.
    assert len(browser.calls) == 1


def test_a_claim_left_by_the_audited_lifecycle_no_longer_blocks_the_receipt(
    tmp_path: Path,
) -> None:
    """The live failure this replaces: EmailUnsubscribeClaimConflict.

    Five tasks held an ``uncertain`` claim carrying the audited lifecycle's
    effect digest. Any receipt with a different digest -- which every direct
    run produces -- was refused, so the task could never reach a terminal
    state by any route.
    """

    effect = _effect()
    operation, task, email_store, _browser = _operation(
        tmp_path,
        [_terminal(effect, UnsubscribePageState.DONE, "You have unsubscribed.")],
    )
    stale = email_store.claim_email_unsubscribe_write(
        action_identity=ACTION_IDENTITY,
        effect_digest=_stale_effect().effect_digest,
        action_plan_id=PLAN.action_plan_id,
        action_plan_version=PLAN.action_plan_version,
        classification_id=CLASSIFICATION_ID,
        account_id=ACCOUNT_ID,
        stable_message_identity=MESSAGE_IDENTITY,
        thread_identity=THREAD_IDENTITY,
        entry_reference=ENTRY.reference,
        operations=_stale_effect().operation_mappings,
        owner={
            "owner_id": "audited-owner",
            "generation": 1,
            "lease_token": "audited-lease",
        },
    )
    assert stale is not None and stale["acquired"]

    result = operation.execute(task.id)

    assert result["status"] == "done"
    assert email_store.get_email_unsubscribe_receipt(ACTION_IDENTITY) is not None


def _stale_effect() -> EmailUnsubscribeEffect:
    from app.email_unsubscribe import UnsubscribeOperation

    return EmailUnsubscribeEffect(
        action_identity=ACTION_IDENTITY,
        action_plan_id=PLAN.action_plan_id,
        action_plan_version=PLAN.action_plan_version,
        classification_id=CLASSIFICATION_ID,
        account_id=ACCOUNT_ID,
        stable_message_identity=MESSAGE_IDENTITY,
        thread_identity=THREAD_IDENTITY,
        entry_reference=ENTRY.reference,
        operations=(
            UnsubscribeOperation(
                operation_reference="unsubscribe-operation:audited-open",
                kind=UnsubscribeOperationKind.OPEN_ENTRY,
                target_reference=ENTRY.reference,
            ),
        ),
    )


def test_a_browser_failure_writes_no_receipt(tmp_path: Path) -> None:
    operation, task, email_store, _browser = _operation(
        tmp_path,
        [
            UnsubscribeBrowserError(
                UnsubscribeBrowserFailure.OPERATION_TIMEOUT,
                "browser operation timed out",
            )
        ],
    )

    result = operation.execute(task.id)

    assert result["status"] == "failed"
    assert email_store.get_email_unsubscribe_receipt(ACTION_IDENTITY) is None


def test_a_missing_task_fails_closed(tmp_path: Path) -> None:
    operation, _task, _email_store, _browser = _operation(tmp_path, [])

    result = operation.execute(9_999_999)

    assert result["status"] == "failed"
    assert result["error"]["code"] == "unsubscribe_task_missing"


@pytest.mark.parametrize("task_id", [0, -1, True])
def test_an_invalid_task_id_fails_closed(tmp_path: Path, task_id: object) -> None:
    operation, _task, _email_store, _browser = _operation(tmp_path, [])

    result = operation.execute(task_id)

    assert result["status"] == "failed"
