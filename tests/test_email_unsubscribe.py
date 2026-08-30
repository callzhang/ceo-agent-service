from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from threading import Barrier

import pytest

from app.agent_contracts import ProposedAction
from app.email_task_adapter import accepted_email_unsubscribe_effect
from app.email_classifier_contracts import (
    EmailAction,
    EmailCategory,
    EmailClassification,
    EmailClassificationStatus,
    build_versioned_email_action_plan,
)
from app.email_store import EmailStore, email_action_identity
from app.email_unsubscribe import (
    BrowserNetworkPolicy,
    EmailUnsubscribeEffect,
    UnsubscribeAuthenticationEvidence,
    UnsubscribeBrowserError,
    UnsubscribeDisposition,
    UnsubscribeContinuationResult,
    UnsubscribeDiscoveredControl,
    UnsubscribeExecutionResult,
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
RUNTIME_PLAN = build_versioned_email_action_plan(
    action_plan_version=1,
    classification_id=41,
    account_id="account-primary",
    category=EmailCategory.SUBSCRIPTION,
    classification_source="model",
    confidence=0.98,
    model_id="email-model:task11-test",
    config_version="email-config:task11-test",
    actions=(EmailAction.UNSUBSCRIBE,),
    action_parameters={},
    created_at=datetime(2026, 8, 30, 8, 0, tzinfo=timezone.utc),
)
ACTION_IDENTITY = email_action_identity(
    account_id="account-primary",
    stable_message_identity="account-primary:message-id:<mail-41@example.com>",
    action_type=EmailAction.UNSUBSCRIBE,
    action_plan_version=RUNTIME_PLAN.action_plan_version,
)
UNSUBSCRIBE_OWNER = {
    "owner_id": "email-worker",
    "generation": 31,
    "lease_token": "unsubscribe-unit-owner",
}
RESTART_OWNER = {
    "owner_id": "email-worker",
    "generation": 32,
    "lease_token": "unsubscribe-unit-restart",
}


def _operations(*kinds: UnsubscribeOperationKind) -> tuple[UnsubscribeOperation, ...]:
    return tuple(
        UnsubscribeOperation(
            operation_reference=f"step-{index}",
            kind=kind,
            target_reference=(
                unsubscribe_entry_reference(TOKEN_URL)
                if kind is UnsubscribeOperationKind.OPEN_ENTRY
                else f"control-{index}"
            ),
        )
        for index, kind in enumerate(kinds, start=1)
    )


def _effect(
    operations: tuple[UnsubscribeOperation, ...] | None = None,
    *,
    previous_effect_digest: str = "",
) -> EmailUnsubscribeEffect:
    return EmailUnsubscribeEffect(
        action_identity=ACTION_IDENTITY,
        action_plan_id=RUNTIME_PLAN.action_plan_id,
        action_plan_version=RUNTIME_PLAN.action_plan_version,
        classification_id=41,
        account_id="account-primary",
        stable_message_identity="account-primary:message-id:<mail-41@example.com>",
        thread_identity="thread-41",
        entry_reference=unsubscribe_entry_reference(TOKEN_URL),
        operations=operations or _operations(UnsubscribeOperationKind.OPEN_ENTRY),
        previous_effect_digest=previous_effect_digest,
        network_policy_reference="network-policy:test",
        network_policy_origin_references=("origin:test",),
    )


def _task() -> ReplyTask:
    payload = {
        "schema": "email_agent_action.v1",
        "action_type": "unsubscribe",
        "action_identity": ACTION_IDENTITY,
        "action_plan_id": RUNTIME_PLAN.action_plan_id,
        "action_plan_version": RUNTIME_PLAN.action_plan_version,
        "classification_id": 41,
        "account_id": "account-primary",
        "stable_message_identity": "account-primary:message-id:<mail-41@example.com>",
        "thread_identity": "thread-41",
        "unsubscribe_entries": [
            {
                "source": "header_https",
                "reference": unsubscribe_entry_reference(TOKEN_URL),
                "priority": 10,
            }
        ],
        "unsubscribe_authentication": None,
        "unsubscribe_network_policy_reference": "network-policy:test",
        "unsubscribe_network_policy_origin_references": ["origin:test"],
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
                "action_identity": ACTION_IDENTITY,
                "account_id": "account-primary",
                "stable_message_identity": (
                    "account-primary:message-id:<mail-41@example.com>"
                ),
                "thread_identity": "thread-41",
                "entry_reference": unsubscribe_entry_reference(TOKEN_URL),
                "network_policy_reference": "network-policy:test",
                "network_policy_origin_references": ["origin:test"],
            },
            "payload": {
                "operations": [
                    {
                        "operation_reference": "step-1",
                        "kind": "open_entry",
                        "target_reference": unsubscribe_entry_reference(TOKEN_URL),
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


def test_network_policy_is_exact_origin_and_rejects_private_dns_resolution() -> None:
    public = BrowserNetworkPolicy(
        allowed_origins=frozenset({"https://mail.example.com"}),
        resolver=lambda _host, _port: ("93.184.216.34",),
    )
    private = BrowserNetworkPolicy(
        allowed_origins=frozenset({"https://mail.example.com:443"}),
        resolver=lambda _host, _port: ("10.0.0.7",),
    )

    assert public.validate_url("https://mail.example.com/unsubscribe")
    with pytest.raises(UnsubscribeBrowserError, match="request rejected"):
        public.validate_url("https://mail.example.com:444/unsubscribe")
    with pytest.raises(UnsubscribeBrowserError, match="request rejected"):
        private.validate_url("https://mail.example.com/unsubscribe")


def test_extracts_and_prioritizes_rfc_entries_without_rendering_private_urls() -> None:
    entries = extract_unsubscribe_entries(
        list_unsubscribe=(
            "<mailto:leave@example.com?subject=unsubscribe>, "
            f"<{TOKEN_URL}>"
        ),
        list_unsubscribe_post="List-Unsubscribe=One-Click",
        authentication_evidence=UnsubscribeAuthenticationEvidence(
            dkim_covers_list_unsubscribe=True,
            dkim_covers_list_unsubscribe_post=True,
            evidence_reference="dkim-evidence:message-41",
        ),
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


def test_unverified_rfc_one_click_is_downgraded_to_ordinary_https() -> None:
    entries = extract_unsubscribe_entries(
        list_unsubscribe=f"<{TOKEN_URL}>",
        list_unsubscribe_post="List-Unsubscribe=One-Click",
    )

    assert len(entries) == 1
    assert entries[0].source is UnsubscribeEntrySource.HEADER_HTTPS
    assert entries[0].priority == 10


def test_rfc_one_click_requires_both_dkim_covered_headers() -> None:
    entries = extract_unsubscribe_entries(
        list_unsubscribe=f"<{TOKEN_URL}>",
        list_unsubscribe_post="List-Unsubscribe=One-Click",
        authentication_evidence=UnsubscribeAuthenticationEvidence(
            dkim_covers_list_unsubscribe=True,
            dkim_covers_list_unsubscribe_post=False,
            evidence_reference="dkim-evidence:message-41",
        ),
    )

    assert entries[0].source is UnsubscribeEntrySource.HEADER_HTTPS


def test_unverified_one_click_operation_is_rejected_by_task_projection() -> None:
    task = _task()
    action = _accepted_action().model_copy(deep=True)
    action.payload["operations"] = [
        {
            "operation_reference": "step-1",
            "kind": "post_one_click",
            "target_reference": unsubscribe_entry_reference(TOKEN_URL),
        }
    ]

    with pytest.raises(ValueError, match="proposal is invalid"):
        accepted_email_unsubscribe_effect(task, action)


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

    assert effect == _effect(
        _operations(UnsubscribeOperationKind.OPEN_ENTRY)
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
    ("field", "unsafe_value"),
    (
        (
            "description",
            "Open https://news.example.com/u/opaque-user-id?uid=42",
        ),
        ("expected_verification", "Open /u/opaque-user-id/42"),
        ("description", "Open https%3A%2F%2Fnews.example.com%2Fu%2F42"),
        ("description", "Open https%253A%252F%252Fnews.example.com%252Fu%252F42"),
    ),
)
def test_unsubscribe_proposal_rejects_all_url_like_text_outside_opaque_schema(
    field: str,
    unsafe_value: str,
) -> None:
    action = _accepted_action().model_copy(update={field: unsafe_value}, deep=True)

    with pytest.raises(
        ValueError, match="accepted unsubscribe proposal is not redacted"
    ) as error:
        accepted_email_unsubscribe_effect(_task(), action)

    assert "news.example.com" not in str(error.value)
    assert "opaque-user-id" not in str(error.value)


def test_unsubscribe_proposal_uses_positive_exact_target_schema() -> None:
    action = _accepted_action().model_copy(deep=True)
    action.target["debug_note"] = "harmless-looking-extra-field"

    with pytest.raises(ValueError, match="accepted unsubscribe proposal is invalid"):
        accepted_email_unsubscribe_effect(_task(), action)


def test_effect_digest_binds_plan_classification_entry_and_ordered_operations() -> None:
    effect = _effect(
        _operations(
            UnsubscribeOperationKind.OPEN_ENTRY,
            UnsubscribeOperationKind.CLICK_CONFIRMATION,
        )
    )
    changed_plan = EmailUnsubscribeEffect(
        **{
            **effect.__dict__,
            "action_plan_version": effect.action_plan_version + 1,
        }
    )
    changed_order = EmailUnsubscribeEffect(
        **{
            **effect.__dict__,
            "operations": tuple(reversed(effect.operations)),
        }
    )

    assert effect.effect_digest != changed_plan.effect_digest
    assert effect.effect_digest != changed_order.effect_digest
    assert len(effect.effect_digest) == 64


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


class _TerminateAfterExternalEffectBrowser(_ScriptedBrowser):
    def execute_operation(
        self,
        effect: EmailUnsubscribeEffect,
        private_url: str,
        operation: UnsubscribeOperation,
    ) -> UnsubscribeObservation:
        self.calls.append(operation.operation_reference)
        raise KeyboardInterrupt("simulated termination after provider effect")


class _ConcurrentWinnerBrowser(_ScriptedBrowser):
    def __init__(self, barrier: Barrier, effect: EmailUnsubscribeEffect) -> None:
        super().__init__([])
        self.barrier = barrier
        self.effect = effect

    def inspect_current_state(
        self,
        effect: EmailUnsubscribeEffect,
        private_url: str,
    ) -> UnsubscribeObservation:
        self.calls.append("inspect")
        self.barrier.wait(timeout=2)
        return UnsubscribeObservation(
            state=UnsubscribePageState.ACTION_REQUIRED,
            state_reference="state-ready",
            next_operation_reference="step-1",
        )

    def execute_operation(
        self,
        effect: EmailUnsubscribeEffect,
        private_url: str,
        operation: UnsubscribeOperation,
    ) -> UnsubscribeObservation:
        self.calls.append(operation.operation_reference)
        return UnsubscribeObservation(
            state=UnsubscribePageState.DONE,
            state_reference="state-done",
            receipt=_terminal_receipt(self.effect),
        )


def _entry():
    return extract_unsubscribe_entries(list_unsubscribe=f"<{TOKEN_URL}>")[0]


def _authorized_store(tmp_path: Path) -> EmailStore:
    store = EmailStore(tmp_path / "unsubscribe.sqlite3")
    store.create_account(
        {
            "account_id": "account-primary",
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
                "classification_id": 41,
                "stable_message_identity": (
                    "account-primary:message-id:<mail-41@example.com>"
                ),
                "provider_locator": {
                    "account_id": "account-primary",
                    "folder": "INBOX",
                    "uidvalidity": 42,
                    "uid": 41,
                    "rfc_message_id": "<mail-41@example.com>",
                    "thread_id": "thread-41",
                },
                "category": EmailCategory.SUBSCRIPTION,
                "confidence": 0.98,
                "margin": 0.4,
                "probabilities": {"subscription": 0.98},
                "model_id": RUNTIME_PLAN.model_id,
                "config_version": RUNTIME_PLAN.config_version,
                "status": EmailClassificationStatus.PROCESSED,
                "classification_source": "model",
                "action_plan": RUNTIME_PLAN,
            }
        ),
        sender="sender@example.com",
        subject="Newsletter",
        model_text="__subject__newsletter",
        received_at="2026-08-30T08:00:00+00:00",
    )
    return store


def _executor(tmp_path: Path, browser: _ScriptedBrowser) -> UnsubscribeExecutor:
    return UnsubscribeExecutor(
        _authorized_store(tmp_path),
        browser,
        owner=UNSUBSCRIBE_OWNER,
    )


def _terminal_receipt(
    effect: EmailUnsubscribeEffect | None = None,
) -> UnsubscribeTerminalReceipt:
    effect = effect or _effect()
    return UnsubscribeTerminalReceipt(
        receipt_id="provider-receipt:done-41",
        evidence="terminal_page",
        entry_reference=unsubscribe_entry_reference(TOKEN_URL),
        effect_digest=effect.effect_digest,
    )


def test_restart_reconciles_receipt_before_page_and_never_replays_operations(
    tmp_path: Path,
) -> None:
    browser = _ScriptedBrowser([], receipt=_terminal_receipt())

    result = _executor(tmp_path, browser).execute(_effect(), (_entry(),))

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
    tmp_path: Path,
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

    result = _executor(tmp_path, browser).execute(_effect(), (_entry(),))

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
    tmp_path: Path,
) -> None:
    browser = _ScriptedBrowser([], error=error)

    result = _executor(tmp_path, browser).execute(_effect(), (_entry(),))

    assert result.outcome is outcome
    assert result.error_code == code
    assert result.disposition == UnsubscribeDisposition("failed", True, True)
    assert "private-token" not in repr(result)
    assert "token=" not in json.dumps(result.redacted, sort_keys=True)


def test_no_reliable_browser_entry_is_skipped_without_calling_browser(
    tmp_path: Path,
) -> None:
    mailto_only = extract_unsubscribe_entries(
        list_unsubscribe="<mailto:leave@example.com?subject=unsubscribe>"
    )
    browser = _ScriptedBrowser([])

    result = _executor(tmp_path, browser).execute(_effect(), mailto_only)

    assert result.outcome is UnsubscribeOutcome.SKIPPED_NO_RELIABLE_ENTRY
    assert result.disposition == UnsubscribeDisposition("skipped", False, False)
    assert browser.calls == []


def test_executor_refuses_unreviewed_or_out_of_order_browser_operation(
    tmp_path: Path,
) -> None:
    browser = _ScriptedBrowser(
        [
            UnsubscribeObservation(
                state=UnsubscribePageState.ACTION_REQUIRED,
                state_reference="state-41",
                next_operation_reference="different-step",
            )
        ]
    )

    result = _executor(tmp_path, browser).execute(_effect(), (_entry(),))

    assert result.outcome is UnsubscribeOutcome.FAILED_BROWSER
    assert browser.calls == ["receipt", "inspect"]
    assert result.error_code == "email_unsubscribe_operation_mismatch"


def test_initial_effect_cannot_preapprove_a_later_control(tmp_path: Path) -> None:
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
                receipt=_terminal_receipt(_effect(operations)),
            ),
        ]
    )

    result = _executor(tmp_path, browser).execute(
        _effect(operations),
        (_entry(),),
    )

    assert result.outcome is UnsubscribeOutcome.FAILED_BROWSER
    assert result.error_code == "email_unsubscribe_authorization_stale"
    assert browser.calls == ["receipt", "inspect"]


def test_restart_reads_durable_terminal_receipt_without_browser_replay(
    tmp_path: Path,
) -> None:
    store = _authorized_store(tmp_path)
    first_browser = _ScriptedBrowser(
        [
            UnsubscribeObservation(
                state=UnsubscribePageState.ACTION_REQUIRED,
                state_reference="state-ready",
                next_operation_reference="step-1",
            ),
            UnsubscribeObservation(
                state=UnsubscribePageState.DONE,
                state_reference="state-done",
                receipt=_terminal_receipt(),
            ),
        ]
    )
    first = UnsubscribeExecutor(
        store,
        first_browser,
        owner=UNSUBSCRIBE_OWNER,
    ).execute(
        _effect(),
        (_entry(),),
    )
    restarted_browser = _ScriptedBrowser([])

    replay = UnsubscribeExecutor(
        EmailStore(store.path),
        restarted_browser,
        owner=RESTART_OWNER,
    ).execute(_effect(), (_entry(),))

    assert first.outcome is replay.outcome is UnsubscribeOutcome.DONE
    assert replay.receipt == first.receipt
    assert replay.journal == first.journal
    assert restarted_browser.calls == []


def test_same_owner_concurrent_executor_has_exactly_one_write_winner(
    tmp_path: Path,
) -> None:
    store = _authorized_store(tmp_path)
    effect = _effect()
    barrier = Barrier(2)
    browsers = [
        _ConcurrentWinnerBrowser(barrier, effect),
        _ConcurrentWinnerBrowser(barrier, effect),
    ]

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda browser: UnsubscribeExecutor(
                    store,
                    browser,
                    owner=UNSUBSCRIBE_OWNER,
                ).execute(effect, (_entry(),)),
                browsers,
            )
        )

    assert sum(browser.calls.count("step-1") for browser in browsers) == 1
    assert sorted(result.outcome.value for result in results) == [
        "done",
        "failed_browser",
    ]


def test_terminated_claim_after_external_effect_is_reconciliation_only(
    tmp_path: Path,
) -> None:
    store = _authorized_store(tmp_path)
    effect = _effect()
    first_browser = _TerminateAfterExternalEffectBrowser(
        [
            UnsubscribeObservation(
                state=UnsubscribePageState.ACTION_REQUIRED,
                state_reference="state-ready",
                next_operation_reference="step-1",
            )
        ]
    )

    with pytest.raises(KeyboardInterrupt, match="simulated termination"):
        UnsubscribeExecutor(
            store,
            first_browser,
            owner=UNSUBSCRIBE_OWNER,
        ).execute(effect, (_entry(),))

    assert first_browser.calls == ["receipt", "inspect", "step-1"]
    assert store.get_email_unsubscribe_claim(effect.action_identity)["status"] == (
        "dispatching"
    )
    assert store.list_email_unsubscribe_steps(effect.action_identity) == []
    assert store.recover_terminated_email_unsubscribe_claims(
        owner=UNSUBSCRIBE_OWNER,
        termination_verifier=lambda owner: owner == UNSUBSCRIBE_OWNER,
        recovered_at="2026-08-30T11:00:00+00:00",
    ) == 1
    restarted_browser = _ScriptedBrowser(
        [
            UnsubscribeObservation(
                state=UnsubscribePageState.ACTION_REQUIRED,
                state_reference="state-not-opened",
                next_operation_reference="step-1",
            )
        ]
    )

    result = UnsubscribeExecutor(
        EmailStore(store.path),
        restarted_browser,
        owner=RESTART_OWNER,
    ).execute(effect, (_entry(),))

    assert result.outcome is UnsubscribeOutcome.FAILED_BROWSER
    assert result.error_code == "email_unsubscribe_outcome_unresolved"
    assert restarted_browser.calls == ["receipt", "inspect"]
    assert result.journal == ()
    recovered_claim = store.get_email_unsubscribe_claim(effect.action_identity)
    assert recovered_claim["status"] == "uncertain"
    assert recovered_claim["owner_generation"] == UNSUBSCRIBE_OWNER["generation"]


def test_uncertain_claim_without_journal_does_not_replay_from_blank_state(
    tmp_path: Path,
) -> None:
    store = _authorized_store(tmp_path)
    effect = _effect()
    claim = store.claim_email_unsubscribe_write(
        **UnsubscribeExecutor._store_arguments(effect),
        owner=UNSUBSCRIBE_OWNER,
    )
    assert claim is not None and claim["acquired"] is True
    assert store.list_email_unsubscribe_steps(effect.action_identity) == []
    assert store.recover_terminated_email_unsubscribe_claims(
        owner=UNSUBSCRIBE_OWNER,
        termination_verifier=lambda owner: owner == UNSUBSCRIBE_OWNER,
        recovered_at="2026-08-30T11:01:00+00:00",
    ) == 1
    restarted_browser = _ScriptedBrowser(
        [
            UnsubscribeObservation(
                state=UnsubscribePageState.ACTION_REQUIRED,
                state_reference="state-not-opened",
                next_operation_reference="step-1",
            )
        ]
    )

    result = UnsubscribeExecutor(
        EmailStore(store.path),
        restarted_browser,
        owner=RESTART_OWNER,
    ).execute(effect, (_entry(),))

    assert result.outcome is UnsubscribeOutcome.FAILED_BROWSER
    assert result.error_code == "email_unsubscribe_outcome_unresolved"
    assert result.journal == ()
    assert restarted_browser.calls == ["receipt", "inspect"]
    assert store.get_email_unsubscribe_claim(effect.action_identity)["status"] == (
        "uncertain"
    )


def test_uncertain_claim_missing_browser_state_stays_unresolved(tmp_path: Path) -> None:
    store = _authorized_store(tmp_path)
    effect = _effect()
    claim = store.claim_email_unsubscribe_write(
        **UnsubscribeExecutor._store_arguments(effect),
        owner=UNSUBSCRIBE_OWNER,
    )
    assert claim is not None and claim["acquired"] is True
    assert store.recover_terminated_email_unsubscribe_claims(
        owner=UNSUBSCRIBE_OWNER,
        termination_verifier=lambda owner: owner == UNSUBSCRIBE_OWNER,
        recovered_at="2026-08-30T11:01:30+00:00",
    ) == 1
    browser = _ScriptedBrowser(
        [],
        error=UnsubscribeBrowserError("recoverable browser state is missing"),
    )

    result = UnsubscribeExecutor(
        EmailStore(store.path),
        browser,
        owner=RESTART_OWNER,
    ).execute(effect, (_entry(),))

    assert result.outcome is UnsubscribeOutcome.FAILED_BROWSER
    assert result.error_code == "email_unsubscribe_outcome_unresolved"
    assert browser.calls == ["receipt", "inspect"]
    assert store.get_email_unsubscribe_claim(effect.action_identity)["status"] == (
        "uncertain"
    )


@pytest.mark.parametrize("terminal_source", ("receipt", "page"))
def test_uncertain_claim_matching_terminal_evidence_completes_without_write(
    terminal_source: str,
    tmp_path: Path,
) -> None:
    store = _authorized_store(tmp_path)
    effect = _effect()
    claim = store.claim_email_unsubscribe_write(
        **UnsubscribeExecutor._store_arguments(effect),
        owner=UNSUBSCRIBE_OWNER,
    )
    assert claim is not None and claim["acquired"] is True
    assert store.recover_terminated_email_unsubscribe_claims(
        owner=UNSUBSCRIBE_OWNER,
        termination_verifier=lambda owner: owner == UNSUBSCRIBE_OWNER,
        recovered_at="2026-08-30T11:02:00+00:00",
    ) == 1
    terminal_receipt = _terminal_receipt(effect)
    browser = (
        _ScriptedBrowser([], receipt=terminal_receipt)
        if terminal_source == "receipt"
        else _ScriptedBrowser(
            [
                UnsubscribeObservation(
                    state=UnsubscribePageState.DONE,
                    state_reference="state-done",
                    receipt=terminal_receipt,
                )
            ]
        )
    )

    result = UnsubscribeExecutor(
        EmailStore(store.path),
        browser,
        owner=RESTART_OWNER,
    ).execute(effect, (_entry(),))

    assert result.outcome is UnsubscribeOutcome.DONE
    assert result.receipt == terminal_receipt
    assert browser.calls == (
        ["receipt"] if terminal_source == "receipt" else ["receipt", "inspect"]
    )
    assert store.get_email_unsubscribe_claim(effect.action_identity)["status"] == (
        "done"
    )


def test_initial_never_claimed_effect_executes_from_blank_start(tmp_path: Path) -> None:
    effect = _effect()
    browser = _ScriptedBrowser(
        [
            UnsubscribeObservation(
                state=UnsubscribePageState.ACTION_REQUIRED,
                state_reference="state-not-opened",
                next_operation_reference="step-1",
            ),
            UnsubscribeObservation(
                state=UnsubscribePageState.DONE,
                state_reference="state-done",
                receipt=_terminal_receipt(effect),
            ),
        ]
    )

    result = _executor(tmp_path, browser).execute(effect, (_entry(),))

    assert result.outcome is UnsubscribeOutcome.DONE
    assert browser.calls == ["receipt", "inspect", "step-1"]


def test_historical_effect_may_reconcile_but_cannot_issue_new_browser_write(
    tmp_path: Path,
) -> None:
    store = _authorized_store(tmp_path)
    corrected_plan = build_versioned_email_action_plan(
        action_plan_version=2,
        classification_id=41,
        account_id="account-primary",
        category=EmailCategory.WORK,
        classification_source="user",
        confidence=0.98,
        model_id=RUNTIME_PLAN.model_id,
        config_version="email-config:task11-corrected",
        actions=(EmailAction.LABEL,),
        action_parameters={EmailAction.LABEL: {"labels": ["work"]}},
        created_at=datetime(2026, 8, 30, 9, 0, tzinfo=timezone.utc),
    )
    store.append_action_plan_version(
        41,
        corrected_plan,
        confirmed_category=EmailCategory.WORK,
    )
    browser = _ScriptedBrowser(
        [
            UnsubscribeObservation(
                state=UnsubscribePageState.ACTION_REQUIRED,
                state_reference="state-ready",
                next_operation_reference="step-1",
            )
        ]
    )

    result = UnsubscribeExecutor(
        store,
        browser,
        owner=UNSUBSCRIBE_OWNER,
    ).execute(_effect(), (_entry(),))

    assert result.outcome is UnsubscribeOutcome.FAILED_BROWSER
    assert result.error_code == "email_unsubscribe_authorization_stale"
    assert browser.calls == ["receipt", "inspect"]


def test_terminal_readback_must_match_the_authorized_entry(tmp_path: Path) -> None:
    mismatched_receipt = UnsubscribeTerminalReceipt(
        receipt_id="provider-receipt:other-entry",
        evidence="terminal_page",
        entry_reference="unsubscribe-entry:other",
        effect_digest=_effect().effect_digest,
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

    result = _executor(tmp_path, browser).execute(_effect(), (_entry(),))

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
            effect_digest=_effect().effect_digest,
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
        "initial proposal contains exactly `OPEN_ENTRY`",
        "returns a typed continuation",
        "strict append-only extension of the persisted prefix",
        "Execute only the newly accepted operation and never replay the prefix",
    ):
        assert unsubscribe_rule in prose


def test_action_required_persists_typed_continuation_without_executing_control(
    tmp_path: Path,
) -> None:
    initial = _effect()
    discovered = UnsubscribeDiscoveredControl(
        reference="control-form",
        kind="form",
        intent="unsubscribe",
    )
    browser = _ScriptedBrowser(
        [
            UnsubscribeObservation(
                state=UnsubscribePageState.ACTION_REQUIRED,
                state_reference="state-not-opened",
                next_operation_reference="step-1",
            ),
            UnsubscribeObservation(
                state=UnsubscribePageState.ACTION_REQUIRED,
                state_reference="state-form",
                controls=(discovered,),
            ),
        ]
    )
    store = _authorized_store(tmp_path)

    result = UnsubscribeExecutor(store, browser, owner=UNSUBSCRIBE_OWNER).execute(
        initial,
        (_entry(),),
    )

    assert isinstance(result, UnsubscribeContinuationResult)
    assert browser.calls == ["receipt", "inspect", "step-1"]
    assert result.continuation.effect_digest == initial.effect_digest
    assert result.continuation.previous_effect_digest == ""
    assert result.continuation.executed_operations == initial.operations
    assert result.continuation.controls == (discovered,)
    assert result.continuation.network_policy_reference == "network-policy:test"
    assert result.continuation.network_policy_origin_references == ("origin:test",)
    durable = store.get_email_unsubscribe_continuation(ACTION_IDENTITY)
    assert durable is not None
    assert durable["controls"] == [
        {"reference": "control-form", "kind": "form", "intent": "unsubscribe"}
    ]
    assert store.get_email_unsubscribe_claim(ACTION_IDENTITY)["status"] == "awaiting_audit"


def test_accepted_continuation_executes_only_new_operation_and_never_prefix(
    tmp_path: Path,
) -> None:
    store = _authorized_store(tmp_path)
    initial = _effect()
    discovered = UnsubscribeDiscoveredControl(
        reference="control-form",
        kind="form",
        intent="unsubscribe",
    )
    first_browser = _ScriptedBrowser(
        [
            UnsubscribeObservation(
                state=UnsubscribePageState.ACTION_REQUIRED,
                state_reference="state-not-opened",
                next_operation_reference="step-1",
            ),
            UnsubscribeObservation(
                state=UnsubscribePageState.ACTION_REQUIRED,
                state_reference="state-form",
                controls=(discovered,),
            ),
        ]
    )
    first = UnsubscribeExecutor(store, first_browser, owner=UNSUBSCRIBE_OWNER).execute(
        initial,
        (_entry(),),
    )
    assert isinstance(first, UnsubscribeContinuationResult)
    extension = _effect(
        initial.operations
        + (
            UnsubscribeOperation(
                operation_reference="step-2",
                kind=UnsubscribeOperationKind.SUBMIT_FORM,
                target_reference=discovered.reference,
            ),
        ),
        previous_effect_digest=initial.effect_digest,
    )
    terminal = UnsubscribeTerminalReceipt(
        receipt_id="provider-receipt:continued",
        evidence="terminal_page",
        entry_reference=extension.entry_reference,
        effect_digest=extension.effect_digest,
    )
    second_browser = _ScriptedBrowser(
        [
            UnsubscribeObservation(
                state=UnsubscribePageState.DONE,
                state_reference="state-done",
                receipt=terminal,
            )
        ]
    )

    result = UnsubscribeExecutor(
        EmailStore(store.path),
        second_browser,
        owner=RESTART_OWNER,
    ).execute(extension, (_entry(),))

    assert isinstance(result, UnsubscribeExecutionResult)
    assert result.outcome is UnsubscribeOutcome.DONE
    assert second_browser.calls == ["receipt", "step-2"]
    assert store.get_email_unsubscribe_claim(ACTION_IDENTITY)["status"] == "done"


def test_awaiting_audit_matching_terminal_receipt_completes_without_new_write(
    tmp_path: Path,
) -> None:
    store = _authorized_store(tmp_path)
    effect = _effect()
    first_browser = _ScriptedBrowser(
        [
            UnsubscribeObservation(
                state=UnsubscribePageState.ACTION_REQUIRED,
                state_reference="state-not-opened",
                next_operation_reference="step-1",
            ),
            UnsubscribeObservation(
                state=UnsubscribePageState.ACTION_REQUIRED,
                state_reference="state-form",
                controls=(
                    UnsubscribeDiscoveredControl(
                        reference="control-form",
                        kind="form",
                        intent="unsubscribe",
                    ),
                ),
            ),
        ]
    )
    assert isinstance(
        UnsubscribeExecutor(store, first_browser, owner=UNSUBSCRIBE_OWNER).execute(
            effect,
            (_entry(),),
        ),
        UnsubscribeContinuationResult,
    )
    receipt_browser = _ScriptedBrowser([], receipt=_terminal_receipt(effect))

    result = UnsubscribeExecutor(
        EmailStore(store.path),
        receipt_browser,
        owner=RESTART_OWNER,
    ).execute(effect, (_entry(),))

    assert isinstance(result, UnsubscribeExecutionResult)
    assert result.outcome is UnsubscribeOutcome.DONE
    assert receipt_browser.calls == ["receipt"]
    assert store.get_email_unsubscribe_claim(ACTION_IDENTITY)["status"] == "done"


@pytest.mark.parametrize(
    ("mutator", "expected_error"),
    [
        (lambda effect: _effect(effect.operations[1:], previous_effect_digest=effect.effect_digest), "prefix"),
        (
            lambda effect: EmailUnsubscribeEffect(
                **{
                    **effect.__dict__,
                    "previous_effect_digest": effect.effect_digest,
                    "network_policy_reference": "network-policy:changed",
                    "operations": effect.operations
                    + (
                        UnsubscribeOperation(
                            operation_reference="step-2",
                            kind=UnsubscribeOperationKind.SUBMIT_FORM,
                            target_reference="control-form",
                        ),
                    ),
                }
            ),
            "policy",
        ),
        (
            lambda effect: _effect(
                effect.operations
                + (
                    UnsubscribeOperation(
                        operation_reference="step-2",
                        kind=UnsubscribeOperationKind.CLICK_CONFIRMATION,
                        target_reference="control-unknown",
                    ),
                ),
                previous_effect_digest=effect.effect_digest,
            ),
            "control",
        ),
        (
            lambda effect: _effect(
                effect.operations
                + (
                    UnsubscribeOperation(
                        operation_reference="step-2",
                        kind=UnsubscribeOperationKind.CLICK_CONFIRMATION,
                        target_reference="control-form",
                    ),
                ),
                previous_effect_digest=effect.effect_digest,
            ),
            "kind",
        ),
        (
            lambda effect: EmailUnsubscribeEffect(
                **{
                    **effect.__dict__,
                    "entry_reference": "unsubscribe-entry:other",
                    "previous_effect_digest": effect.effect_digest,
                    "operations": effect.operations
                    + (
                        UnsubscribeOperation(
                            operation_reference="step-2",
                            kind=UnsubscribeOperationKind.SUBMIT_FORM,
                            target_reference="control-form",
                        ),
                    ),
                }
            ),
            "different effect",
        ),
    ],
)
def test_store_rejects_tampered_continuation_extensions(
    tmp_path: Path,
    mutator,
    expected_error: str,
) -> None:
    store = _authorized_store(tmp_path)
    initial = _effect()
    claim = store.claim_email_unsubscribe_write(
        **UnsubscribeExecutor._store_arguments(initial),
        owner=UNSUBSCRIBE_OWNER,
    )
    assert claim is not None and claim["acquired"] is True
    store.persist_email_unsubscribe_continuation(
        **UnsubscribeExecutor._store_arguments(initial),
        controls=(
            {"reference": "control-form", "kind": "form", "intent": "unsubscribe"},
        ),
        observation_reference="state-form",
        final_step={
            "sequence": 1,
            "operation": "open_entry",
            "state": "action_required",
            "reference": "step-1",
        },
        owner=UNSUBSCRIBE_OWNER,
    )

    with pytest.raises(Exception, match=expected_error):
        store.claim_email_unsubscribe_write(
            **UnsubscribeExecutor._store_arguments(mutator(initial)),
            owner=RESTART_OWNER,
        )


def test_concurrent_continuation_claim_has_exactly_one_fresh_winner(
    tmp_path: Path,
) -> None:
    store = _authorized_store(tmp_path)
    initial = _effect()
    assert store.claim_email_unsubscribe_write(
        **UnsubscribeExecutor._store_arguments(initial),
        owner=UNSUBSCRIBE_OWNER,
    )["acquired"]
    store.persist_email_unsubscribe_continuation(
        **UnsubscribeExecutor._store_arguments(initial),
        controls=(
            {"reference": "control-form", "kind": "form", "intent": "unsubscribe"},
        ),
        observation_reference="state-form",
        final_step={
            "sequence": 1,
            "operation": "open_entry",
            "state": "action_required",
            "reference": "step-1",
        },
        owner=UNSUBSCRIBE_OWNER,
    )
    extension = _effect(
        initial.operations
        + (
            UnsubscribeOperation(
                operation_reference="step-2",
                kind=UnsubscribeOperationKind.SUBMIT_FORM,
                target_reference="control-form",
            ),
        ),
        previous_effect_digest=initial.effect_digest,
    )
    owners = (
        RESTART_OWNER,
        {
            "owner_id": "email-worker",
            "generation": 33,
            "lease_token": "unsubscribe-unit-concurrent",
        },
    )

    def claim(owner: dict[str, object]) -> bool:
        try:
            result = store.claim_email_unsubscribe_write(
                **UnsubscribeExecutor._store_arguments(extension),
                owner=owner,
            )
        except Exception:
            return False
        return bool(result and result["acquired"])

    with ThreadPoolExecutor(max_workers=2) as pool:
        acquired = list(pool.map(claim, owners))

    assert acquired.count(True) == 1
    durable = store.get_email_unsubscribe_claim(ACTION_IDENTITY)
    assert durable is not None and durable["status"] == "dispatching"
    assert durable["effect_digest"] == extension.effect_digest


def test_adapter_rejects_precomputed_initial_dom_operations() -> None:
    action = _accepted_action().model_copy(
        update={
            "payload": {
                "operations": [
                    *_accepted_action().payload["operations"],
                    {
                        "operation_reference": "step-2",
                        "kind": "submit_form",
                        "target_reference": "control-guessed",
                    },
                ]
            }
        }
    )

    with pytest.raises(ValueError, match="invalid"):
        accepted_email_unsubscribe_effect(_task(), action)


def test_stale_plan_cannot_claim_a_persisted_continuation(tmp_path: Path) -> None:
    store = _authorized_store(tmp_path)
    initial = _effect()
    assert store.claim_email_unsubscribe_write(
        **UnsubscribeExecutor._store_arguments(initial),
        owner=UNSUBSCRIBE_OWNER,
    )["acquired"]
    store.persist_email_unsubscribe_continuation(
        **UnsubscribeExecutor._store_arguments(initial),
        controls=(
            {"reference": "control-form", "kind": "form", "intent": "unsubscribe"},
        ),
        observation_reference="state-form",
        final_step={
            "sequence": 1,
            "operation": "open_entry",
            "state": "action_required",
            "reference": "step-1",
        },
        owner=UNSUBSCRIBE_OWNER,
    )
    corrected = build_versioned_email_action_plan(
        action_plan_version=2,
        classification_id=41,
        account_id="account-primary",
        category=EmailCategory.SUBSCRIPTION,
        classification_source="user",
        confidence=1.0,
        model_id=RUNTIME_PLAN.model_id,
        config_version=RUNTIME_PLAN.config_version,
        actions=(EmailAction.UNSUBSCRIBE,),
        action_parameters={},
        created_at=datetime(2026, 8, 30, 9, 0, tzinfo=timezone.utc),
    )
    store.append_action_plan_version(
        41,
        corrected,
        confirmed_category=EmailCategory.SUBSCRIPTION,
    )
    extension = _effect(
        initial.operations
        + (
            UnsubscribeOperation(
                operation_reference="step-2",
                kind=UnsubscribeOperationKind.SUBMIT_FORM,
                target_reference="control-form",
            ),
        ),
        previous_effect_digest=initial.effect_digest,
    )

    assert store.claim_email_unsubscribe_write(
        **UnsubscribeExecutor._store_arguments(extension),
        owner=RESTART_OWNER,
    ) is None
    assert store.get_email_unsubscribe_claim(ACTION_IDENTITY)["status"] == (
        "awaiting_audit"
    )
