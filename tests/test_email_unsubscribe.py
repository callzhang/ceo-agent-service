from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.agent_contracts import ProposedAction
from app.email_task_adapter import accepted_email_unsubscribe_effect
from app.email_unsubscribe import (
    EmailUnsubscribeEffect,
    UnsubscribeBrowserError,
    UnsubscribeDisposition,
    UnsubscribeEntrySource,
    UnsubscribeExecutor,
    UnsubscribeObservation,
    UnsubscribeOperation,
    UnsubscribeOperationKind,
    UnsubscribeOutcome,
    UnsubscribePageState,
    UnsubscribeProviderAuthError,
    UnsubscribeTerminalReceipt,
    disposition_for_unsubscribe_outcome,
    extract_unsubscribe_entries,
    select_browser_unsubscribe_entry,
    unsubscribe_entry_reference,
)
from app.store import ReplyTask


TOKEN_URL = "https://news.example.com/unsubscribe?token=private-token"


def _operations(*kinds: UnsubscribeOperationKind) -> tuple[UnsubscribeOperation, ...]:
    return tuple(
        UnsubscribeOperation(
            operation_reference=f"step-{index}",
            kind=kind,
            target_reference=(
                "entry"
                if kind is UnsubscribeOperationKind.OPEN_ENTRY
                else f"control-{index}"
            ),
        )
        for index, kind in enumerate(kinds, start=1)
    )


def _effect(
    operations: tuple[UnsubscribeOperation, ...] | None = None,
) -> EmailUnsubscribeEffect:
    return EmailUnsubscribeEffect(
        action_identity="email-action:unsubscribe:test",
        action_plan_id="email-plan:test",
        action_plan_version=3,
        classification_id=41,
        account_id="account-primary",
        stable_message_identity="account-primary:message:<mail-41@example.com>",
        thread_identity="thread-41",
        entry_reference=unsubscribe_entry_reference(TOKEN_URL),
        operations=operations or _operations(UnsubscribeOperationKind.OPEN_ENTRY),
    )


def _task() -> ReplyTask:
    payload = {
        "schema": "email_agent_action.v1",
        "action_type": "unsubscribe",
        "action_identity": "email-action:unsubscribe:test",
        "action_plan_id": "email-plan:test",
        "action_plan_version": 3,
        "classification_id": 41,
        "account_id": "account-primary",
        "stable_message_identity": "account-primary:message:<mail-41@example.com>",
        "thread_identity": "thread-41",
    }
    return ReplyTask(
        id=9,
        channel="email",
        conversation_id="email-thread:test",
        conversation_title="Email unsubscribe",
        single_chat=False,
        trigger_message_id=payload["action_identity"],
        trigger_create_time="2026-08-30T08:00:00+00:00",
        trigger_sender="sender@example.com",
        trigger_text="Immutable ActionPlan authorizes unsubscribe.",
        trigger_message_json=json.dumps(payload),
        status="pending",
        attempts=0,
        created_at="2026-08-30T08:00:00+00:00",
        updated_at="2026-08-30T08:00:00+00:00",
    )


def _accepted_action() -> ProposedAction:
    return ProposedAction.model_validate(
        {
            "description": "Unsubscribe the current sender",
            "capability": "email_browser",
            "operation": "unsubscribe",
            "target": {
                "action_identity": "email-action:unsubscribe:test",
                "account_id": "account-primary",
                "stable_message_identity": (
                    "account-primary:message:<mail-41@example.com>"
                ),
                "thread_identity": "thread-41",
                "entry_reference": "unsubscribe-entry:test",
            },
            "payload": {
                "operations": [
                    {
                        "operation_reference": "step-1",
                        "kind": "open_entry",
                        "target_reference": "entry",
                    },
                    {
                        "operation_reference": "step-2",
                        "kind": "click_confirmation",
                        "target_reference": "control-2",
                    },
                ]
            },
            "expected_verification": (
                "Read the terminal page or confirmation-mail receipt."
            ),
        }
    )


def test_unsubscribe_outcome_contract_is_exact() -> None:
    assert {item.name: item.value for item in UnsubscribeOutcome} == {
        "DONE": "done",
        "ALREADY_UNSUBSCRIBED": "already_unsubscribed",
        "SKIPPED_NO_RELIABLE_ENTRY": "skipped_no_reliable_entry",
        "SKIPPED_LOGIN_REQUIRED": "skipped_login_required",
        "SKIPPED_CAPTCHA": "skipped_captcha",
        "SKIPPED_PAYMENT": "skipped_payment",
        "FAILED_BROWSER": "failed_browser",
        "FAILED_PROVIDER_AUTH": "failed_provider_auth",
    }


def test_extracts_and_prioritizes_rfc_entries_without_rendering_private_urls() -> None:
    entries = extract_unsubscribe_entries(
        list_unsubscribe=(
            "<mailto:leave@example.com?subject=unsubscribe>, "
            f"<{TOKEN_URL}>"
        ),
        list_unsubscribe_post="List-Unsubscribe=One-Click",
        body_text=(
            "Manage your subscription: "
            "https://body.example.com/preferences/unsubscribe?id=body-token"
        ),
    )

    selected = select_browser_unsubscribe_entry(entries)

    assert selected is not None
    assert selected.source is UnsubscribeEntrySource.HEADER_ONE_CLICK_HTTPS
    assert selected.private_url == TOKEN_URL
    assert selected.reference.startswith("unsubscribe-entry:")
    assert "private-token" not in repr(entries)
    assert "body-token" not in repr(entries)
    assert "private-token" not in json.dumps(
        [entry.redacted for entry in entries],
        sort_keys=True,
    )


def test_extracts_only_explicit_body_unsubscribe_links() -> None:
    entries = extract_unsubscribe_entries(
        body_text=(
            "Product docs https://docs.example.com/start. "
            "退订：https://news.example.com/preferences/remove?id=private"
        ),
        body_html=(
            '<a href="https://news.example.com/preferences?id=html-private">'
            "Unsubscribe</a>"
        ),
    )

    assert {entry.source for entry in entries} == {
        UnsubscribeEntrySource.BODY_HTML_HTTPS,
        UnsubscribeEntrySource.BODY_TEXT_HTTPS,
    }
    assert all("docs.example.com" not in entry.private_url for entry in entries)
    assert "html-private" not in repr(entries)
    assert "id=private" not in repr(entries)


def test_accepted_unsubscribe_effect_binds_exact_audited_operations() -> None:
    effect = accepted_email_unsubscribe_effect(_task(), _accepted_action())

    assert effect == EmailUnsubscribeEffect(
        action_identity="email-action:unsubscribe:test",
        action_plan_id="email-plan:test",
        action_plan_version=3,
        classification_id=41,
        account_id="account-primary",
        stable_message_identity="account-primary:message:<mail-41@example.com>",
        thread_identity="thread-41",
        entry_reference="unsubscribe-entry:test",
        operations=_operations(
            UnsubscribeOperationKind.OPEN_ENTRY,
            UnsubscribeOperationKind.CLICK_CONFIRMATION,
        ),
    )


def test_accepted_unsubscribe_effect_rejects_url_or_wrong_authorization() -> None:
    action = _accepted_action().model_copy(deep=True)
    action.target["entry_reference"] = TOKEN_URL
    with pytest.raises(ValueError, match="accepted unsubscribe") as error:
        accepted_email_unsubscribe_effect(_task(), action)
    assert "private-token" not in str(error.value)

    wrong_task = _task().model_copy(update={"trigger_message_id": "wrong-plan-action"})
    with pytest.raises(ValueError, match="automatic email unsubscribe"):
        accepted_email_unsubscribe_effect(wrong_task, _accepted_action())


def test_accepted_unsubscribe_effect_rejects_private_url_in_any_proposal_field() -> None:
    action = _accepted_action().model_copy(
        update={"description": f"Open {TOKEN_URL}"},
        deep=True,
    )

    with pytest.raises(ValueError, match="accepted unsubscribe") as error:
        accepted_email_unsubscribe_effect(_task(), action)

    assert "private-token" not in str(error.value)


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        (UnsubscribeOutcome.DONE, UnsubscribeDisposition("done", False, False)),
        (
            UnsubscribeOutcome.ALREADY_UNSUBSCRIBED,
            UnsubscribeDisposition("done", False, False),
        ),
        (
            UnsubscribeOutcome.SKIPPED_NO_RELIABLE_ENTRY,
            UnsubscribeDisposition("skipped", False, False),
        ),
        (
            UnsubscribeOutcome.SKIPPED_LOGIN_REQUIRED,
            UnsubscribeDisposition("skipped", False, False),
        ),
        (
            UnsubscribeOutcome.SKIPPED_CAPTCHA,
            UnsubscribeDisposition("skipped", False, False),
        ),
        (
            UnsubscribeOutcome.SKIPPED_PAYMENT,
            UnsubscribeDisposition("skipped", False, False),
        ),
        (
            UnsubscribeOutcome.FAILED_BROWSER,
            UnsubscribeDisposition("failed", True, True),
        ),
        (
            UnsubscribeOutcome.FAILED_PROVIDER_AUTH,
            UnsubscribeDisposition("failed", True, True),
        ),
    ],
)
def test_maps_outcomes_to_existing_task_semantics(
    outcome: UnsubscribeOutcome,
    expected: UnsubscribeDisposition,
) -> None:
    assert disposition_for_unsubscribe_outcome(outcome) == expected


class _ScriptedBrowser:
    def __init__(
        self,
        observations: list[UnsubscribeObservation],
        *,
        receipt: UnsubscribeTerminalReceipt | None = None,
        error: Exception | None = None,
    ) -> None:
        self.observations = observations
        self.receipt = receipt
        self.error = error
        self.calls: list[str] = []

    def find_confirmation_receipt(
        self,
        effect: EmailUnsubscribeEffect,
    ) -> UnsubscribeTerminalReceipt | None:
        self.calls.append("receipt")
        if isinstance(self.error, UnsubscribeProviderAuthError):
            raise self.error
        return self.receipt

    def inspect_current_state(
        self,
        effect: EmailUnsubscribeEffect,
        private_url: str,
    ) -> UnsubscribeObservation:
        self.calls.append("inspect")
        if isinstance(self.error, UnsubscribeBrowserError):
            raise self.error
        return self.observations.pop(0)

    def execute_operation(
        self,
        effect: EmailUnsubscribeEffect,
        private_url: str,
        operation: UnsubscribeOperation,
    ) -> UnsubscribeObservation:
        self.calls.append(operation.operation_reference)
        return self.observations.pop(0)


def _entry():
    return extract_unsubscribe_entries(list_unsubscribe=f"<{TOKEN_URL}>")[0]


def _terminal_receipt() -> UnsubscribeTerminalReceipt:
    return UnsubscribeTerminalReceipt(
        receipt_id="provider-receipt:done-41",
        evidence="terminal_page",
        entry_reference=unsubscribe_entry_reference(TOKEN_URL),
    )


def test_restart_reconciles_receipt_before_page_and_never_replays_operations() -> None:
    browser = _ScriptedBrowser([], receipt=_terminal_receipt())

    result = UnsubscribeExecutor(browser).execute(_effect(), (_entry(),))

    assert result.outcome is UnsubscribeOutcome.DONE
    assert result.receipt == _terminal_receipt()
    assert browser.calls == ["receipt"]
    serialized = json.dumps(result.redacted, sort_keys=True)
    assert "private-token" not in serialized
    assert TOKEN_URL not in serialized


@pytest.mark.parametrize(
    ("state", "outcome"),
    [
        (UnsubscribePageState.ALREADY_UNSUBSCRIBED, UnsubscribeOutcome.ALREADY_UNSUBSCRIBED),
        (UnsubscribePageState.LOGIN_REQUIRED, UnsubscribeOutcome.SKIPPED_LOGIN_REQUIRED),
        (UnsubscribePageState.CAPTCHA, UnsubscribeOutcome.SKIPPED_CAPTCHA),
        (UnsubscribePageState.PAYMENT, UnsubscribeOutcome.SKIPPED_PAYMENT),
    ],
)
def test_reconciled_business_terminal_states_do_not_execute(
    state: UnsubscribePageState,
    outcome: UnsubscribeOutcome,
) -> None:
    browser = _ScriptedBrowser(
        [
            UnsubscribeObservation(
                state=state,
                state_reference="state-41",
                receipt=_terminal_receipt(),
            )
        ]
    )

    result = UnsubscribeExecutor(browser).execute(_effect(), (_entry(),))

    assert result.outcome is outcome
    assert result.disposition.task_status in {"done", "skipped"}
    assert browser.calls == ["receipt", "inspect"]


@pytest.mark.parametrize(
    ("error", "outcome", "code"),
    [
        (
            UnsubscribeBrowserError("browser leaked " + TOKEN_URL),
            UnsubscribeOutcome.FAILED_BROWSER,
            "email_unsubscribe_browser_failed",
        ),
        (
            UnsubscribeProviderAuthError("auth token=private"),
            UnsubscribeOutcome.FAILED_PROVIDER_AUTH,
            "email_unsubscribe_provider_auth_failed",
        ),
    ],
)
def test_technical_failures_use_fixed_redacted_errors(
    error: Exception,
    outcome: UnsubscribeOutcome,
    code: str,
) -> None:
    browser = _ScriptedBrowser([], error=error)

    result = UnsubscribeExecutor(browser).execute(_effect(), (_entry(),))

    assert result.outcome is outcome
    assert result.error_code == code
    assert result.disposition == UnsubscribeDisposition("failed", True, True)
    assert "private-token" not in repr(result)
    assert "token=" not in json.dumps(result.redacted, sort_keys=True)


def test_no_reliable_browser_entry_is_skipped_without_calling_browser() -> None:
    mailto_only = extract_unsubscribe_entries(
        list_unsubscribe="<mailto:leave@example.com?subject=unsubscribe>"
    )
    browser = _ScriptedBrowser([])

    result = UnsubscribeExecutor(browser).execute(_effect(), mailto_only)

    assert result.outcome is UnsubscribeOutcome.SKIPPED_NO_RELIABLE_ENTRY
    assert result.disposition == UnsubscribeDisposition("skipped", False, False)
    assert browser.calls == []


def test_executor_refuses_unreviewed_or_out_of_order_browser_operation() -> None:
    browser = _ScriptedBrowser(
        [
            UnsubscribeObservation(
                state=UnsubscribePageState.ACTION_REQUIRED,
                state_reference="state-41",
                next_operation_reference="different-step",
            )
        ]
    )

    result = UnsubscribeExecutor(browser).execute(_effect(), (_entry(),))

    assert result.outcome is UnsubscribeOutcome.FAILED_BROWSER
    assert browser.calls == ["receipt", "inspect"]
    assert result.error_code == "email_unsubscribe_operation_mismatch"


def test_restart_resumes_at_the_current_reviewed_operation() -> None:
    operations = _operations(
        UnsubscribeOperationKind.OPEN_ENTRY,
        UnsubscribeOperationKind.CLICK_CONFIRMATION,
    )
    browser = _ScriptedBrowser(
        [
            UnsubscribeObservation(
                state=UnsubscribePageState.ACTION_REQUIRED,
                state_reference="state-after-open",
                next_operation_reference="step-2",
            ),
            UnsubscribeObservation(
                state=UnsubscribePageState.DONE,
                state_reference="state-done",
                receipt=_terminal_receipt(),
            ),
        ]
    )

    result = UnsubscribeExecutor(browser).execute(
        _effect(operations),
        (_entry(),),
    )

    assert result.outcome is UnsubscribeOutcome.DONE
    assert browser.calls == ["receipt", "inspect", "step-2"]


def test_terminal_readback_must_match_the_authorized_entry() -> None:
    mismatched_receipt = UnsubscribeTerminalReceipt(
        receipt_id="provider-receipt:other-entry",
        evidence="terminal_page",
        entry_reference="unsubscribe-entry:other",
    )
    browser = _ScriptedBrowser(
        [
            UnsubscribeObservation(
                state=UnsubscribePageState.ACTION_REQUIRED,
                state_reference="state-ready",
                next_operation_reference="step-1",
            ),
            UnsubscribeObservation(
                state=UnsubscribePageState.DONE,
                state_reference="state-done",
                receipt=mismatched_receipt,
            ),
        ]
    )

    result = UnsubscribeExecutor(browser).execute(_effect(), (_entry(),))

    assert result.outcome is UnsubscribeOutcome.FAILED_BROWSER
    assert result.receipt is None
    assert result.error_code == "email_unsubscribe_receipt_mismatch"


@pytest.mark.parametrize(
    "unsafe_reference",
    (
        "token:private-value",
        "password=private-value",
        "https://news.example.com/unsubscribe",
    ),
)
def test_redacted_receipt_rejects_credentials_and_urls(
    unsafe_reference: str,
) -> None:
    with pytest.raises(ValueError) as error:
        UnsubscribeTerminalReceipt(
            receipt_id=unsafe_reference,
            evidence="terminal_page",
            entry_reference=unsubscribe_entry_reference(TOKEN_URL),
        )

    assert "private-value" not in str(error.value)


def test_mail_review_skill_keeps_review_boundaries_and_adds_unsubscribe_rules() -> None:
    skill = (
        Path(__file__).resolve().parents[1]
        / "skills"
        / "ceo-mail-review"
        / "SKILL.md"
    ).read_text(encoding="utf-8")
    prose = " ".join(skill.split())

    for existing in (
        "Resolve the principal's mailbox and the complete original message or thread",
        "Inspect every linked material needed for the requested judgment",
        "Every reply requires explicit reply authorization",
        "ask one concrete question naming the specifically missing mail or linked material",
    ):
        assert existing in prose
    for unsubscribe_rule in (
        "does not require per-message confirmation",
        "propose the exact ordered browser operations",
        "Audit Agent B must review those exact operations before any external write",
        "reconcile the current page, provider state, safe prior receipt, and confirmation mail before another write",
        "Never place a full unsubscribe URL or query token in the proposal, step journal, History, status, or error",
        "Login, CAPTCHA, and payment requirements are skipped business outcomes",
        "Browser runtime and provider authentication failures are technical failures",
    ):
        assert unsubscribe_rule in prose
