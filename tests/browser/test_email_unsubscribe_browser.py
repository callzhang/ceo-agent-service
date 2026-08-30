from __future__ import annotations

import json
import os

import pytest

from app.email_unsubscribe import (
    EmailUnsubscribeEffect,
    UnsubscribeExecutor,
    UnsubscribeObservation,
    UnsubscribeOperation,
    UnsubscribeOperationKind,
    UnsubscribeOutcome,
    UnsubscribePageState,
    UnsubscribeTerminalReceipt,
    extract_unsubscribe_entries,
    unsubscribe_entry_reference,
)


pytestmark = pytest.mark.skipif(
    os.environ.get("WORKBENCH_BROWSER_TESTS") != "1",
    reason="set WORKBENCH_BROWSER_TESTS=1 to run local browser fixtures",
)


PRIVATE_URL = "https://fixture.invalid/unsubscribe?token=fixture-private-token"


def _operation(index: int, kind: UnsubscribeOperationKind) -> UnsubscribeOperation:
    return UnsubscribeOperation(
        operation_reference=f"step-{index}",
        kind=kind,
        target_reference="entry" if index == 1 else f"control-{index}",
    )


class _LocalBrowserFixture:
    """Deterministic protocol fixture; it never opens a network connection."""

    def __init__(
        self,
        initial: UnsubscribeObservation,
        transitions: dict[str, UnsubscribeObservation],
        *,
        confirmation_receipt: UnsubscribeTerminalReceipt | None = None,
    ) -> None:
        self.initial = initial
        self.transitions = transitions
        self.confirmation_receipt = confirmation_receipt
        self.calls: list[str] = []

    def find_confirmation_receipt(self, effect: EmailUnsubscribeEffect):
        self.calls.append("receipt")
        return self.confirmation_receipt

    def inspect_current_state(
        self,
        effect: EmailUnsubscribeEffect,
        private_url: str,
    ) -> UnsubscribeObservation:
        assert private_url == PRIVATE_URL
        self.calls.append("inspect")
        return self.initial

    def execute_operation(
        self,
        effect: EmailUnsubscribeEffect,
        private_url: str,
        operation: UnsubscribeOperation,
    ) -> UnsubscribeObservation:
        assert private_url == PRIVATE_URL
        self.calls.append(operation.operation_reference)
        return self.transitions[operation.operation_reference]


def _receipt(evidence: str = "terminal_page") -> UnsubscribeTerminalReceipt:
    return UnsubscribeTerminalReceipt(
        receipt_id=f"fixture-receipt:{evidence}",
        evidence=evidence,
        entry_reference=unsubscribe_entry_reference(PRIVATE_URL),
    )


def _observation(
    state: UnsubscribePageState,
    *,
    next_step: str = "",
    evidence: str = "terminal_page",
) -> UnsubscribeObservation:
    return UnsubscribeObservation(
        state=state,
        state_reference=f"fixture-state:{state.value}",
        next_operation_reference=next_step,
        receipt=(
            _receipt(evidence)
            if state
            in {
                UnsubscribePageState.DONE,
                UnsubscribePageState.ALREADY_UNSUBSCRIBED,
                UnsubscribePageState.LOGIN_REQUIRED,
                UnsubscribePageState.CAPTCHA,
                UnsubscribePageState.PAYMENT,
            }
            else None
        ),
    )


def _run(
    fixture: _LocalBrowserFixture,
    operations: tuple[UnsubscribeOperation, ...],
):
    entry = extract_unsubscribe_entries(list_unsubscribe=f"<{PRIVATE_URL}>")[0]
    effect = EmailUnsubscribeEffect(
        action_identity="email-action:unsubscribe:fixture",
        action_plan_id="email-plan:fixture",
        action_plan_version=1,
        classification_id=1,
        account_id="fixture-account",
        stable_message_identity="fixture-message",
        thread_identity="fixture-thread",
        entry_reference=entry.reference,
        operations=operations,
    )
    result = UnsubscribeExecutor(fixture).execute(effect, (entry,))
    assert fixture.calls[:2] == ["receipt", "inspect"]
    assert "fixture-private-token" not in repr(result)
    assert "fixture-private-token" not in json.dumps(result.redacted, sort_keys=True)
    return result


def test_direct_success_fixture() -> None:
    operations = (_operation(1, UnsubscribeOperationKind.OPEN_ENTRY),)
    fixture = _LocalBrowserFixture(
        _observation(UnsubscribePageState.ACTION_REQUIRED, next_step="step-1"),
        {"step-1": _observation(UnsubscribePageState.DONE)},
    )

    result = _run(fixture, operations)

    assert result.outcome is UnsubscribeOutcome.DONE


def test_redirect_success_fixture() -> None:
    operations = (
        _operation(1, UnsubscribeOperationKind.OPEN_ENTRY),
        _operation(2, UnsubscribeOperationKind.FOLLOW_REDIRECT),
    )
    fixture = _LocalBrowserFixture(
        _observation(UnsubscribePageState.ACTION_REQUIRED, next_step="step-1"),
        {
            "step-1": _observation(
                UnsubscribePageState.ACTION_REQUIRED,
                next_step="step-2",
            ),
            "step-2": _observation(UnsubscribePageState.DONE),
        },
    )

    assert _run(fixture, operations).outcome is UnsubscribeOutcome.DONE


def test_two_step_form_fixture() -> None:
    operations = (
        _operation(1, UnsubscribeOperationKind.OPEN_ENTRY),
        _operation(2, UnsubscribeOperationKind.SUBMIT_FORM),
        _operation(3, UnsubscribeOperationKind.SUBMIT_FORM),
    )
    fixture = _LocalBrowserFixture(
        _observation(UnsubscribePageState.ACTION_REQUIRED, next_step="step-1"),
        {
            "step-1": _observation(
                UnsubscribePageState.ACTION_REQUIRED,
                next_step="step-2",
            ),
            "step-2": _observation(
                UnsubscribePageState.ACTION_REQUIRED,
                next_step="step-3",
            ),
            "step-3": _observation(UnsubscribePageState.DONE),
        },
    )

    assert _run(fixture, operations).outcome is UnsubscribeOutcome.DONE


def test_final_confirmation_click_fixture() -> None:
    operations = (
        _operation(1, UnsubscribeOperationKind.OPEN_ENTRY),
        _operation(2, UnsubscribeOperationKind.CLICK_CONFIRMATION),
    )
    fixture = _LocalBrowserFixture(
        _observation(UnsubscribePageState.ACTION_REQUIRED, next_step="step-1"),
        {
            "step-1": _observation(
                UnsubscribePageState.ACTION_REQUIRED,
                next_step="step-2",
            ),
            "step-2": _observation(UnsubscribePageState.DONE),
        },
    )

    assert _run(fixture, operations).outcome is UnsubscribeOutcome.DONE


def test_already_unsubscribed_fixture() -> None:
    fixture = _LocalBrowserFixture(
        _observation(UnsubscribePageState.ALREADY_UNSUBSCRIBED),
        {},
    )

    assert _run(fixture, ()).outcome is UnsubscribeOutcome.ALREADY_UNSUBSCRIBED


@pytest.mark.parametrize(
    ("state", "outcome"),
    [
        (UnsubscribePageState.LOGIN_REQUIRED, UnsubscribeOutcome.SKIPPED_LOGIN_REQUIRED),
        (UnsubscribePageState.CAPTCHA, UnsubscribeOutcome.SKIPPED_CAPTCHA),
        (UnsubscribePageState.PAYMENT, UnsubscribeOutcome.SKIPPED_PAYMENT),
    ],
)
def test_unsupported_business_fixture(
    state: UnsubscribePageState,
    outcome: UnsubscribeOutcome,
) -> None:
    fixture = _LocalBrowserFixture(_observation(state), {})

    result = _run(fixture, ())

    assert result.outcome is outcome
    assert result.disposition.task_status == "skipped"
    assert result.disposition.attention_when_exhausted is False


def test_confirmation_email_fixture() -> None:
    operations = (
        _operation(1, UnsubscribeOperationKind.OPEN_ENTRY),
        _operation(2, UnsubscribeOperationKind.CONFIRM_EMAIL),
    )
    fixture = _LocalBrowserFixture(
        _observation(UnsubscribePageState.ACTION_REQUIRED, next_step="step-1"),
        {
            "step-1": _observation(
                UnsubscribePageState.ACTION_REQUIRED,
                next_step="step-2",
            ),
            "step-2": _observation(
                UnsubscribePageState.DONE,
                evidence="confirmation_mail",
            ),
        },
    )

    result = _run(fixture, operations)

    assert result.outcome is UnsubscribeOutcome.DONE
    assert result.receipt is not None
    assert result.receipt.evidence == "confirmation_mail"
