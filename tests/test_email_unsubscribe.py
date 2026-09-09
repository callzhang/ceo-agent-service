from __future__ import annotations

import json
import inspect
from dataclasses import asdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from threading import Barrier
from threading import Thread, get_ident

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
from app.email_store import (
    EmailStore,
    EmailUnsubscribeClaimConflict,
    email_action_identity,
)
from app.email_browser_profile import (
    EmailBrowserProfile,
    EmailBrowserProfileError,
    EmailBrowserSessionManager,
    launch_persistent_email_context,
)
from app.store import ReplyTask
from app.email_unsubscribe import (
    BrowserNetworkPolicy,
    ConnectedMailboxOtp,
    EmailOtpChallenge,
    EmailUnsubscribeEffect,
    PlaywrightUnsubscribeBrowser,
    UnsubscribeAuthenticationEvidence,
    UnsubscribeBrowserError,
    UnsubscribeDisposition,
    UnsubscribeContinuationResult,
    UnsubscribeContinuationKind,
    UnsubscribeDiscoveredControl,
    UnsubscribeExecutionResult,
    UnsubscribeEntrySource,
    UnsubscribeExecutor,
    UnsubscribeObservation,
    UnsubscribeOperation,
    UnsubscribeOperationKind,
    UnsubscribeOutcome,
    UnsubscribePageDiscovery,
    UnsubscribePageState,
    UnsubscribeProviderAuthError,
    UnsubscribeTerminalReceipt,
    browser_unsubscribe_entries,
    disposition_for_unsubscribe_outcome,
    execute_unsubscribe_in_dedicated_profile,
    extract_unsubscribe_entries,
    normalize_unsubscribe_result_text,
    select_browser_unsubscribe_entry,
    select_connected_mailbox_otp,
    email_otp_context_reference,
    unsubscribe_entry_reference,
    _ChromiumIsolatedWorld,
    _AuditedControlBinding,
    _terminal_result,
    _validated_restored_audit_session,
)


def test_connected_mailbox_otp_requires_exact_recipient_site_context_and_window() -> None:
    opened_at = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)
    challenge = EmailOtpChallenge(
        recipient="derek@stardust.ai",
        site_domain="accounts.example.com",
        context_reference=email_otp_context_reference(
            "derek@stardust.ai", "accounts.example.com"
        ),
        opened_at=opened_at,
        expires_at=opened_at + timedelta(minutes=10),
    )
    matching = ConnectedMailboxOtp(
        recipient="derek@stardust.ai",
        sender_domain="accounts.example.com",
        context_reference=email_otp_context_reference(
            "derek@stardust.ai", "accounts.example.com"
        ),
        authenticated_sender_domain="accounts.example.com",
        authentication_reference="otp-auth:" + "a" * 64,
        received_at=opened_at + timedelta(minutes=1),
        value="847201",
    )
    rejected = (
        ConnectedMailboxOtp(
            recipient="other@stardust.ai",
            sender_domain="accounts.example.com",
            context_reference=email_otp_context_reference(
                "derek@stardust.ai", "accounts.example.com"
            ),
            authenticated_sender_domain="accounts.example.com",
            authentication_reference="otp-auth:" + "b" * 64,
            received_at=opened_at + timedelta(minutes=1),
            value="111111",
        ),
        ConnectedMailboxOtp(
            recipient="derek@stardust.ai",
            sender_domain="attacker.example.net",
            context_reference=email_otp_context_reference(
                "derek@stardust.ai", "accounts.example.com"
            ),
            authenticated_sender_domain="attacker.example.net",
            authentication_reference="otp-auth:" + "c" * 64,
            received_at=opened_at + timedelta(minutes=1),
            value="222222",
        ),
        ConnectedMailboxOtp(
            recipient="derek@stardust.ai",
            sender_domain="accounts.example.com",
            context_reference="otp-context:other",
            authenticated_sender_domain="accounts.example.com",
            authentication_reference="otp-auth:" + "d" * 64,
            received_at=opened_at + timedelta(minutes=1),
            value="333333",
        ),
        ConnectedMailboxOtp(
            recipient="derek@stardust.ai",
            sender_domain="accounts.example.com",
            context_reference=email_otp_context_reference(
                "derek@stardust.ai", "accounts.example.com"
            ),
            authenticated_sender_domain="accounts.example.com",
            authentication_reference="otp-auth:" + "e" * 64,
            received_at=opened_at - timedelta(seconds=1),
            value="444444",
        ),
    )

    selected = select_connected_mailbox_otp(challenge, (*rejected, matching))

    assert selected is matching
    assert selected.consume() == "847201"
    with pytest.raises(UnsubscribeProviderAuthError, match="already consumed"):
        selected.consume()
    assert "847201" not in repr(selected)
    assert "847201" not in json.dumps(selected.redacted, sort_keys=True)
    assert not hasattr(selected, "__dict__")
    with pytest.raises(TypeError):
        asdict(selected)


@pytest.mark.parametrize(
    "kind",
    (
        UnsubscribeContinuationKind.EMAIL_OTP,
        UnsubscribeContinuationKind.CAPTCHA_HANDOFF,
        UnsubscribeContinuationKind.CREDENTIAL_HANDOFF,
    ),
)
def test_authentication_continuation_controls_are_typed_and_secret_free(kind) -> None:
    control = UnsubscribeDiscoveredControl(
        reference=f"control-{kind.value}",
        kind=kind.value,
        intent="confirm",
    )

    assert control.continuation_kind is kind
    assert set(control.redacted) == {"reference", "kind", "intent"}


def test_email_otp_is_consumed_only_inside_the_audited_browser_operation(caplog) -> None:
    fills = []
    clicks = []
    posts = []

    class Field:
        def count(self):
            return 1

        def fill(self, value, **_kwargs):
            fills.append(value)

    class Submitter:
        def count(self):
            return 1

        def click(self, **_kwargs):
            clicks.append("click")

    class Page:
        url = "https://accounts.example.com/unsubscribe"

        def locator(self, selector):
            return Field() if selector == "#email-code" else Submitter()

        def wait_for_load_state(self, *_args, **_kwargs):
            return None

        def set_content(self, *_args, **_kwargs):
            return None

    class Response:
        url = "https://accounts.example.com/unsubscribe"
        status = 200

        def body(self):
            return b"Unsubscribed"

        def text(self):
            return "Unsubscribed"

    class Request:
        def post(self, url, **kwargs):
            posts.append((url, kwargs))
            return Response()

    browser = object.__new__(PlaywrightUnsubscribeBrowser)
    browser.page = Page()
    browser._context = type("Context", (), {"request": Request()})()
    browser.timeout_ms = 500
    browser.connected_recipient = "derek@stardust.ai"
    browser._document_url = "https://accounts.example.com/unsubscribe"
    seen_challenges = []

    def resolve(challenge):
        seen_challenges.append(challenge)
        return ConnectedMailboxOtp(
            recipient=challenge.recipient,
            sender_domain=challenge.site_domain,
            context_reference=challenge.context_reference,
            authenticated_sender_domain=challenge.site_domain,
            authentication_reference="otp-auth:" + "f" * 64,
            received_at=challenge.expires_at,
            value="847201",
        )

    browser.email_otp_resolver = resolve
    browser._raise_if_blocked = lambda: None
    browser._validate_navigation_target = lambda value: value
    binding = _AuditedControlBinding(
        control=UnsubscribeDiscoveredControl(
            reference="control-email-otp",
            kind="email_otp",
            intent="confirm",
        ),
        target_url="https://accounts.example.com/unsubscribe",
        method="POST",
        enctype="application/x-www-form-urlencoded",
        successful_controls=(),
        field_selector="#email-code",
        submitter_selector="#verify-code",
        field_name="code",
        submitter_name="decision",
        submitter_value="verify",
        challenge_context_reference="otp-context:challenge-1",
        challenge_opened_at=datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc),
        challenge_expires_at=datetime(2026, 9, 8, 12, 10, tzinfo=timezone.utc),
    )

    browser._execute_email_otp_control(_effect(), binding)

    assert fills == [""]
    assert clicks == []
    assert posts[0][1]["data"] == "code=847201&decision=verify"
    assert seen_challenges[0].recipient == "derek@stardust.ai"
    assert seen_challenges[0].site_domain == "accounts.example.com"
    assert seen_challenges[0].opened_at == datetime(
        2026, 9, 8, 12, 0, tzinfo=timezone.utc
    )
    assert seen_challenges[0].expires_at == datetime(
        2026, 9, 8, 12, 10, tzinfo=timezone.utc
    )
    assert "847201" not in caplog.text
    assert "847201" not in repr(seen_challenges)


def test_authentication_binding_requires_explicit_email_delivery_proof_and_is_exact() -> None:
    opened_at = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)

    class World:
        def __init__(self, values):
            self.values = iter(values)

        def evaluate(self, _script):
            return next(self.values)

    def browser_for(*snapshots):
        browser = object.__new__(PlaywrightUnsubscribeBrowser)
        browser._trusted_world = World(snapshots)
        browser.connected_recipient = "derek@stardust.ai"
        browser.network_policy = type(
            "Policy",
            (),
            {
                "reference": "policy:test",
                "validate_url": lambda self, value: value,
            },
        )()
        browser._page_identity = lambda: "https://accounts.example.com/verify"
        browser._challenge_bindings = {}
        browser._clock = lambda: opened_at
        return browser

    exact = {
        "present": True,
        "captcha": False,
        "autocompleteTokens": ["one-time-code"],
        "deliveryMethod": "email",
        "deliveryRecipient": "derek@stardust.ai",
        "challengeIdentity": "dom:form-1/code-1",
        "fieldSelector": "#email-code",
        "submitterSelector": "#verify-code",
        "field": {
            "identity": "element:field-1",
            "tag": "input",
            "type": "text",
            "name": "code",
            "autocomplete": "one-time-code",
            "form": "element:form-1",
            "handlers": {"onchange": "", "oninput": ""},
        },
        "formAssociation": "element:form-1",
        "action": "/verify",
        "method": "POST",
        "enctype": "application/x-www-form-urlencoded",
        "target": "",
        "successfulControls": [
            {
                "identity": "element:challenge-1",
                "name": "challenge_id",
                "type": "hidden",
                "value": "challenge-42",
            }
        ],
        "submitter": {
            "identity": "element:submit-1",
            "label": "Verify",
            "tag": "button",
            "type": "submit",
            "name": "",
            "value": "",
            "handlers": {"onclick": ""},
        },
        "documentGeneration": "document:1",
    }
    first = browser_for(exact)._ordinary_controls()[0]
    mutated = {**exact, "submitterSelector": "#replacement-submit"}
    second = browser_for(mutated)._ordinary_controls()[0]
    same_selector_replacement = {
        **exact,
        "field": {**exact["field"], "identity": "element:field-2"},
    }
    replacement = browser_for(same_selector_replacement)._ordinary_controls()[0]
    changed_handler = {
        **exact,
        "submitter": {
            **exact["submitter"],
            "handlers": {"onclick": "sendElsewhere()"},
        },
    }
    handler_mutation = browser_for(changed_handler)._ordinary_controls()[0]
    ambiguous = {**exact, "deliveryMethod": "", "deliveryRecipient": ""}
    third = browser_for(ambiguous)._ordinary_controls()[0]

    assert first.control.kind == "email_otp"
    assert first.field_selector == "#email-code"
    assert first.submitter_selector == "#verify-code"
    assert first.challenge_opened_at == opened_at
    assert first.challenge_expires_at == opened_at + timedelta(minutes=10)
    assert first.control.reference != second.control.reference
    assert first.control.reference != replacement.control.reference
    assert first.control.reference != handler_mutation.control.reference
    assert third.control.kind == "credential_handoff"


def test_email_otp_resend_creates_a_new_challenge_generation_and_window() -> None:
    now = [datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)]
    snapshots = [
        {
            "present": True,
            "captcha": False,
            "autocompleteTokens": ["one-time-code"],
            "deliveryMethod": "email",
            "deliveryRecipient": "derek@stardust.ai",
            "challengeIdentity": "dom:form-1/code-1",
            "fieldSelector": "#code",
            "submitterSelector": "#verify",
            "field": {"identity": "element:field", "name": "code"},
            "formAssociation": "element:form",
            "action": "/verify",
            "method": "POST",
            "enctype": "application/x-www-form-urlencoded",
            "target": "",
            "successfulControls": [
                {
                    "identity": "element:challenge",
                    "name": "challenge_id",
                    "type": "hidden",
                    "value": "generation-1",
                }
            ],
            "submitter": {"identity": "element:submit", "label": "Verify"},
            "documentGeneration": "document:1",
        },
    ]

    class World:
        def evaluate(self, _script):
            return snapshots[0]

    browser = object.__new__(PlaywrightUnsubscribeBrowser)
    browser._trusted_world = World()
    browser.connected_recipient = "derek@stardust.ai"
    browser.network_policy = type(
        "Policy",
        (),
        {"reference": "policy:test", "validate_url": lambda self, value: value},
    )()
    browser._page_identity = lambda: "https://accounts.example.com/verify"
    browser._challenge_bindings = {}
    browser._clock = lambda: now[0]

    first = browser._ordinary_controls()[0]
    now[0] += timedelta(minutes=2)
    same = browser._ordinary_controls()[0]
    snapshots[0] = {
        **snapshots[0],
        "successfulControls": [
            {
                "identity": "element:challenge",
                "name": "challenge_id",
                "type": "hidden",
                "value": "generation-2",
            }
        ],
    }
    resent = browser._ordinary_controls()[0]

    assert same.challenge_opened_at == first.challenge_opened_at
    assert resent.challenge_opened_at == now[0]
    assert resent.challenge_context_reference == first.challenge_context_reference
    assert resent.control.reference != first.control.reference
    old_otp = ConnectedMailboxOtp(
        recipient="derek@stardust.ai",
        sender_domain="accounts.example.com",
        context_reference=first.challenge_context_reference,
        authenticated_sender_domain="accounts.example.com",
        authentication_reference="otp-auth:" + "9" * 64,
        received_at=first.challenge_opened_at + timedelta(minutes=1),
        value="847201",
    )
    assert (
        select_connected_mailbox_otp(
            EmailOtpChallenge(
                recipient="derek@stardust.ai",
                site_domain="accounts.example.com",
                context_reference=resent.challenge_context_reference,
                opened_at=resent.challenge_opened_at,
                expires_at=resent.challenge_expires_at,
            ),
            (old_otp,),
        )
        is None
    )


def test_captcha_attempt_uses_only_the_audited_exact_control() -> None:
    selectors = []
    clicks = []

    class Control:
        def count(self):
            return 1

        def click(self, **_kwargs):
            clicks.append("click")

    class Page:
        def locator(self, selector):
            selectors.append(selector)
            return Control()

        def wait_for_timeout(self, _timeout):
            return None

    browser = object.__new__(PlaywrightUnsubscribeBrowser)
    browser.page = Page()
    browser.timeout_ms = 500
    browser._raise_if_blocked = lambda: None
    binding = _AuditedControlBinding(
        control=UnsubscribeDiscoveredControl(
            reference="control-captcha",
            kind="captcha_handoff",
            intent="confirm",
        ),
        target_url="https://accounts.example.com/challenge",
        method="GET",
        enctype="application/x-www-form-urlencoded",
        successful_controls=(),
        field_selector="iframe:nth-of-type(1) > div[role=checkbox]",
    )

    browser._attempt_rendered_challenge(binding)

    assert selectors == ["iframe:nth-of-type(1) > div[role=checkbox]"]
    assert clicks == ["click"]


def test_unsubscribe_result_text_is_redacted_bounded_and_digest_is_full_text() -> None:
    result, digest = normalize_unsubscribe_result_text(
        "退订成功\r\nhttps://news.example.com/unsubscribe?token=private-token "
        "/Users/derek/private-profile secret=super-secret\x00\n" + "你好" * 10_000
    )

    assert "private-token" not in result
    assert "super-secret" not in result
    assert "/Users/derek" not in result
    assert "\r" not in result
    assert "\n" in result
    assert len(result.encode("utf-8")) <= 16 * 1024

    short_result, short_digest = normalize_unsubscribe_result_text("退订成功\n")
    assert short_result == "退订成功\n"
    assert digest != short_digest


TOKEN_URL = "https://news.example.com/unsubscribe?token=private-token"
RUNTIME_PLAN = build_versioned_email_action_plan(
    action_plan_version=1,
    classification_id=41,
    account_id="account-primary",
    category=EmailCategory.JUNK,
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


class _UnavailableIsolatedWorldSession:
    def __init__(self, *, stale_context: bool = False) -> None:
        self.calls: list[tuple[str, object | None]] = []
        self.stale_context = stale_context
        self.live_value_reads = 0
        self.persisted_values: list[str] = []

    def send(self, method: str, params: object | None = None) -> object:
        self.calls.append((method, params))
        if method == "Page.getFrameTree":
            return {"frameTree": {"frame": {"id": "main-frame"}}}
        if method == "Page.createIsolatedWorld":
            if not self.stale_context:
                raise RuntimeError("isolated world disabled by runtime")
            return {"executionContextId": 71}
        if method == "Runtime.callFunctionOn":
            raise RuntimeError("Cannot find context with specified id")
        raise AssertionError(f"unexpected CDP method: {method}")


@pytest.mark.parametrize("stale_context", (False, True))
def test_chromium_isolated_world_unavailable_or_stale_fails_closed_without_fallback(
    stale_context: bool,
) -> None:
    session = _UnavailableIsolatedWorldSession(stale_context=stale_context)
    world = _ChromiumIsolatedWorld(session)

    with pytest.raises(
        UnsubscribeBrowserError,
        match="^trusted browser execution unavailable$",
    ):
        world.evaluate("() => { throw new Error('must never run in main world'); }")

    assert [method for method, _ in session.calls] == (
        [
            "Page.getFrameTree",
            "Page.createIsolatedWorld",
            "Runtime.callFunctionOn",
        ]
        if stale_context
        else ["Page.getFrameTree", "Page.createIsolatedWorld"]
    )
    assert session.live_value_reads == 0
    assert session.persisted_values == []


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
                "index": 0,
                "source": "header_https",
                "digest": unsubscribe_entry_reference(TOKEN_URL).removeprefix(
                    "unsubscribe-entry:"
                ),
                "reference": unsubscribe_entry_reference(TOKEN_URL),
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
            "action_identity": ACTION_IDENTITY,
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


def test_network_policy_reference_binds_test_only_loopback_mode() -> None:
    origins = frozenset({"https://mail.example.com"})
    production = BrowserNetworkPolicy(
        allowed_origins=origins,
        resolver=lambda _host, _port: ("93.184.216.34",),
    )
    test_only = BrowserNetworkPolicy(
        allowed_origins=origins,
        allow_loopback_for_tests=True,
        resolver=lambda _host, _port: ("93.184.216.34",),
    )

    assert production.reference != test_only.reference


def test_extracts_candidates_in_offline_semantic_order_with_ephemeral_metadata(
    monkeypatch,
) -> None:
    def network_forbidden(*_args, **_kwargs):
        raise AssertionError("candidate discovery must not use the network")

    monkeypatch.setattr("socket.getaddrinfo", network_forbidden)
    ordinary_header = "https://header.example.com/unsubscribe?id=header-token"
    semantic_html = "https://html.example.com/preferences?id=html-token"
    nearby_text = "https://text.example.com/leave?id=text-token"
    entries = extract_unsubscribe_entries(
        list_unsubscribe=(
            "<mailto:leave@example.com?subject=unsubscribe>, "
            f"<{TOKEN_URL}>, <{ordinary_header}>"
        ),
        list_unsubscribe_post="List-Unsubscribe=One-Click",
        authentication_evidence=UnsubscribeAuthenticationEvidence(
            dkim_covers_list_unsubscribe=True,
            dkim_covers_list_unsubscribe_post=True,
            evidence_reference="dkim-evidence:message-41",
        ),
        body_html=f'<a href="{semantic_html}">取消订阅</a>',
        body_text=f"如不希望继续接收，请退订 {nearby_text}",
    )

    selected = select_browser_unsubscribe_entry(entries)

    assert selected is not None
    assert selected.source is UnsubscribeEntrySource.HEADER_ONE_CLICK_HTTPS
    assert selected.private_url == TOKEN_URL
    assert selected.reference.startswith("unsubscribe-entry:")
    assert [entry.source for entry in entries] == [
        UnsubscribeEntrySource.HEADER_ONE_CLICK_HTTPS,
        UnsubscribeEntrySource.HEADER_ONE_CLICK_HTTPS,
        UnsubscribeEntrySource.HEADER_MAILTO,
        UnsubscribeEntrySource.BODY_HTML_HTTPS,
        UnsubscribeEntrySource.BODY_TEXT_HTTPS,
    ]
    assert [entry.index for entry in entries] == list(range(5))
    assert entries[0].scheme == "https"
    assert entries[0].host == "news.example.com"
    assert entries[3].context == "取消订阅"
    assert len(entries[4].context.encode("utf-8")) <= 160
    assert set(entries[0].redacted) == {"index", "source", "digest", "reference"}
    assert "private-token" not in repr(entries)
    assert "html-token" not in repr(entries)
    assert "text-token" not in repr(entries)
    assert "private-token" not in json.dumps(
        [entry.redacted for entry in entries],
        sort_keys=True,
    )


def test_executable_candidate_indexes_stay_stable_after_exact_selection() -> None:
    entries = extract_unsubscribe_entries(
        list_unsubscribe="<mailto:leave@example.test?subject=unsubscribe>",
        body_html=(
            '<a href="https://first.example.test/unsubscribe?id=first">Unsubscribe</a>'
            '<a href="https://second.example.test/unsubscribe?id=second">Unsubscribe</a>'
        ),
    )

    executable = browser_unsubscribe_entries(entries, normalize_indexes=True)
    selected = browser_unsubscribe_entries((executable[1],))

    assert [entry.index for entry in executable] == [0, 1]
    assert selected[0].index == 1
    assert selected[0].reference == executable[1].reference


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


def test_plain_text_candidates_use_symmetric_cross_line_context_in_source_order() -> (
    None
):
    first = "https://first.example.com/preferences?id=first-private"
    second = "https://second.example.net/preferences?id=second-private"
    entries = extract_unsubscribe_entries(
        body_text=(f"{first}\n退订\n" + "普通说明" * 30 + f"\n{second}\nunsubscribe\n")
    )

    assert [entry.private_url for entry in entries] == [first, second]
    assert [entry.index for entry in entries] == [0, 1]
    assert "退订" in entries[0].context
    assert "unsubscribe" in entries[1].context.casefold()
    assert all(len(entry.context.encode("utf-8")) <= 160 for entry in entries)


def test_plain_text_context_never_leaks_an_adjacent_url_fragment() -> None:
    private_token = "adjacent-private-token-" * 8
    first = f"https://first.example.com/unsubscribe?token={private_token}"
    second = "https://second.example.net/unsubscribe?token=second-private"

    entries = extract_unsubscribe_entries(body_text=f"{first} {second}\n退订")

    assert [entry.private_url for entry in entries] == [first, second]
    assert "adjacent-private-token" not in repr(entries)
    assert "second-private" not in repr(entries)


def test_plain_text_distant_marker_does_not_bind_single_ordinary_url() -> None:
    ordinary_url = "https://news.example.com/preferences?id=distant-private"

    entries = extract_unsubscribe_entries(
        body_text="unsubscribe\n" + ("ordinary body text " * 800) + ordinary_url
    )

    assert entries == ()
    assert "distant-private" not in repr(entries)


def test_plain_text_distant_marker_does_not_bind_any_of_multiple_urls() -> None:
    first = "https://first.example.com/preferences?id=first-distant"
    second = "https://second.example.net/preferences?id=second-distant"

    entries = extract_unsubscribe_entries(
        body_text=("退订\n" + ("普通正文" * 3500) + f"\n{first}\n普通链接\n{second}")
    )

    assert entries == ()
    assert "first-distant" not in repr(entries)
    assert "second-distant" not in repr(entries)


def test_accepted_unsubscribe_effect_binds_exact_audited_operations() -> None:
    effect = accepted_email_unsubscribe_effect(_task(), _accepted_action())

    assert effect == _effect(_operations(UnsubscribeOperationKind.OPEN_ENTRY))


def test_accepted_unsubscribe_effect_rejects_url_or_wrong_authorization() -> None:
    action = _accepted_action().model_copy(deep=True)
    action.target["entry_reference"] = TOKEN_URL
    with pytest.raises(ValueError, match="accepted unsubscribe") as error:
        accepted_email_unsubscribe_effect(_task(), action)
    assert "private-token" not in str(error.value)

    wrong_task = _task().model_copy(update={"trigger_message_id": "wrong-plan-action"})
    with pytest.raises(ValueError, match="automatic email unsubscribe"):
        accepted_email_unsubscribe_effect(wrong_task, _accepted_action())


def test_accepted_unsubscribe_effect_rejects_private_url_in_any_proposal_field() -> (
    None
):
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
                "category": EmailCategory.JUNK,
                "confidence": 0.98,
                "margin": 0.4,
                "probabilities": {"junk": 0.98},
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


@pytest.mark.parametrize(
    ("visible_text", "truncated"),
    [
        ("", False),
        ("Unsubscribed", False),
        ("A" * (16 * 1024 + 777), True),
    ],
)
def test_terminal_result_carries_private_bounded_integrity_metadata(
    visible_text: str,
    truncated: bool,
) -> None:
    effect = _effect()
    result = _terminal_result(
        effect,
        UnsubscribeObservation(
            state=UnsubscribePageState.DONE,
            state_reference="state-done",
            receipt=_terminal_receipt(effect),
            visible_text=visible_text,
        ),
        [],
    )

    assert result is not None
    expected_digest = (
        sha256(result.result_text.encode("utf-8")).hexdigest()
        if result.result_text
        else ""
    )
    assert result.result_text_digest == expected_digest
    assert result.result_text_truncated is truncated
    assert "result_text_digest" not in result.redacted
    assert "result_text_truncated" not in result.redacted


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
        (
            UnsubscribePageState.ALREADY_UNSUBSCRIBED,
            UnsubscribeOutcome.ALREADY_UNSUBSCRIBED,
        ),
        (
            UnsubscribePageState.LOGIN_REQUIRED,
            UnsubscribeOutcome.SKIPPED_LOGIN_REQUIRED,
        ),
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
            UnsubscribeBrowserError("browser operation timed out"),
            UnsubscribeOutcome.FAILED_BROWSER,
            "email_unsubscribe_browser_timeout",
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


def test_network_policy_allows_only_same_google_provider_redirect() -> None:
    addresses = lambda _host, _port: ("142.250.72.14",)
    policy = BrowserNetworkPolicy(
        allowed_origins=frozenset({"https://workspace.google.com"}),
        resolver=addresses,
    )

    assert policy.validate_provider_redirect(
        "https://workspace.google.com/unsubscribe",
        "https://accounts.google.com/continue",
    ) == "https://accounts.google.com/continue"
    short_link_policy = BrowserNetworkPolicy(
        allowed_origins=frozenset({"https://c.gle"}),
        resolver=addresses,
    )
    assert short_link_policy.validate_provider_redirect(
        "https://c.gle/workspace-unsubscribe",
        "https://workspace.google.com/continue",
    ) == "https://workspace.google.com/continue"
    assert short_link_policy.validate_provider_resource(
        "https://c.gle/workspace-unsubscribe",
        "https://ssl.gstatic.com/account.css",
    ) == "https://ssl.gstatic.com/account.css"
    assert short_link_policy.validate_provider_resource(
        "https://c.gle/workspace-unsubscribe",
        "https://accounts.google.com/account.js",
    ) == "https://accounts.google.com/account.js"
    with pytest.raises(UnsubscribeBrowserError, match="network request rejected"):
        short_link_policy.validate_provider_resource(
            "https://c.gle/workspace-unsubscribe",
            "https://www.apple.com/account.css",
        )
    with pytest.raises(UnsubscribeBrowserError, match="network request rejected"):
        policy.validate_provider_redirect(
            "https://workspace.google.com/unsubscribe",
            "https://example.com/continue",
        )


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
    owners = [
        {**UNSUBSCRIBE_OWNER, "lease_token": "unsubscribe-unit-owner-1"},
        {**UNSUBSCRIBE_OWNER, "lease_token": "unsubscribe-unit-owner-2"},
    ]

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda pair: UnsubscribeExecutor(
                    store,
                    pair[0],
                    owner=pair[1],
                ).execute(effect, (_entry(),)),
                zip(browsers, owners),
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
    assert (
        store.recover_terminated_email_unsubscribe_claims(
            owner=UNSUBSCRIBE_OWNER,
            termination_verifier=lambda owner: owner == UNSUBSCRIBE_OWNER,
            recovered_at="2026-08-30T11:00:00+00:00",
        )
        == 1
    )
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
    assert (
        store.recover_terminated_email_unsubscribe_claims(
            owner=UNSUBSCRIBE_OWNER,
            termination_verifier=lambda owner: owner == UNSUBSCRIBE_OWNER,
            recovered_at="2026-08-30T11:01:00+00:00",
        )
        == 1
    )
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
    assert (
        store.recover_terminated_email_unsubscribe_claims(
            owner=UNSUBSCRIBE_OWNER,
            termination_verifier=lambda owner: owner == UNSUBSCRIBE_OWNER,
            recovered_at="2026-08-30T11:01:30+00:00",
        )
        == 1
    )
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
    assert (
        store.recover_terminated_email_unsubscribe_claims(
            owner=UNSUBSCRIBE_OWNER,
            termination_verifier=lambda owner: owner == UNSUBSCRIBE_OWNER,
            recovered_at="2026-08-30T11:02:00+00:00",
        )
        == 1
    )
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
        Path(__file__).resolve().parents[1] / "skills" / "ceo-mail-review" / "SKILL.md"
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
        "For `captcha_handoff`, attempt ordinary interaction with the rendered challenge",
        "Browser runtime and provider authentication failures are technical failures",
        "initial proposal contains exactly `OPEN_ENTRY`",
        "returns a typed continuation",
        "strict append-only extension of the persisted prefix",
        "Execute only the newly accepted operation and never replay the prefix",
        "The value may be consumed only once",
        "minimize Python references without claiming physical memory zeroization",
        "`RECONCILE_HANDOFF`",
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
    assert (
        store.get_email_unsubscribe_claim(ACTION_IDENTITY)["status"] == "awaiting_audit"
    )


def test_captcha_gets_one_audited_normal_attempt_then_requires_human(
    tmp_path: Path,
) -> None:
    initial = _effect()
    captcha = UnsubscribeDiscoveredControl(
        reference="control-captcha",
        kind="captcha_handoff",
        intent="confirm",
    )
    store = _authorized_store(tmp_path)
    first_browser = _ScriptedBrowser(
        [
            UnsubscribeObservation(
                state=UnsubscribePageState.ACTION_REQUIRED,
                state_reference="state-not-opened",
                next_operation_reference="step-1",
            ),
            UnsubscribeObservation(
                state=UnsubscribePageState.ACTION_REQUIRED,
                state_reference="state-captcha",
                controls=(captcha,),
            ),
        ]
    )
    first = UnsubscribeExecutor(store, first_browser, owner=UNSUBSCRIBE_OWNER).execute(
        initial,
        (_entry(),),
    )
    assert isinstance(first, UnsubscribeContinuationResult)
    assert first.continuation.requires_human is False

    attempted = _effect(
        initial.operations
        + (
            UnsubscribeOperation(
                operation_reference="step-captcha-attempt",
                kind=UnsubscribeOperationKind.CLICK_CONFIRMATION,
                target_reference=captcha.reference,
            ),
        ),
        previous_effect_digest=initial.effect_digest,
    )
    second_browser = _ScriptedBrowser(
        [
            UnsubscribeObservation(
                state=UnsubscribePageState.ACTION_REQUIRED,
                state_reference="state-captcha-still-present",
                controls=(captcha,),
            )
        ]
    )

    second = UnsubscribeExecutor(
        EmailStore(store.path),
        second_browser,
        owner=RESTART_OWNER,
    ).execute(attempted, (_entry(),))

    assert isinstance(second, UnsubscribeContinuationResult)
    assert second.continuation.requires_human is True
    assert second_browser.calls == ["receipt", "step-captcha-attempt"]


@pytest.mark.parametrize(
    "invalid_kind",
    (
        UnsubscribeOperationKind.FOLLOW_REDIRECT,
        UnsubscribeOperationKind.RECONCILE_HANDOFF,
    ),
)
def test_captcha_cannot_skip_its_initial_ordinary_attempt(
    tmp_path: Path,
    invalid_kind: UnsubscribeOperationKind,
) -> None:
    store = _authorized_store(tmp_path)
    initial = _effect()
    captcha = UnsubscribeDiscoveredControl(
        reference="control-captcha",
        kind="captcha_handoff",
        intent="confirm",
    )
    result = UnsubscribeExecutor(
        store,
        _ScriptedBrowser(
            [
                UnsubscribeObservation(
                    state=UnsubscribePageState.ACTION_REQUIRED,
                    state_reference="state-not-opened",
                    next_operation_reference="step-1",
                ),
                UnsubscribeObservation(
                    state=UnsubscribePageState.ACTION_REQUIRED,
                    state_reference="state-captcha",
                    controls=(captcha,),
                ),
            ]
        ),
        owner=UNSUBSCRIBE_OWNER,
    ).execute(initial, (_entry(),))
    assert isinstance(result, UnsubscribeContinuationResult)
    skipped = _effect(
        initial.operations
        + (
            UnsubscribeOperation(
                operation_reference="step-invalid-captcha-stage",
                kind=invalid_kind,
                target_reference=captcha.reference,
            ),
        ),
        previous_effect_digest=initial.effect_digest,
    )

    with pytest.raises(
        EmailUnsubscribeClaimConflict,
        match="control kind|continuation stage",
    ):
        EmailStore(store.path).claim_email_unsubscribe_write(
            **UnsubscribeExecutor._store_arguments(skipped),
            owner=RESTART_OWNER,
        )


def test_audited_normal_captcha_attempt_may_complete_without_handoff(
    tmp_path: Path,
) -> None:
    store = _authorized_store(tmp_path)
    initial = _effect()
    captcha = UnsubscribeDiscoveredControl(
        reference="control-captcha",
        kind="captcha_handoff",
        intent="confirm",
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
                state_reference="state-captcha",
                controls=(captcha,),
            ),
        ]
    )
    assert isinstance(
        UnsubscribeExecutor(store, first_browser, owner=UNSUBSCRIBE_OWNER).execute(
            initial,
            (_entry(),),
        ),
        UnsubscribeContinuationResult,
    )
    extension = _effect(
        initial.operations
        + (
            UnsubscribeOperation(
                operation_reference="step-captcha-attempt",
                kind=UnsubscribeOperationKind.CLICK_CONFIRMATION,
                target_reference=captcha.reference,
            ),
        ),
        previous_effect_digest=initial.effect_digest,
    )
    browser = _ScriptedBrowser(
        [
            UnsubscribeObservation(
                state=UnsubscribePageState.DONE,
                state_reference="state-passed",
                receipt=_terminal_receipt(extension),
            )
        ]
    )

    result = UnsubscribeExecutor(
        EmailStore(store.path),
        browser,
        owner=RESTART_OWNER,
    ).execute(extension, (_entry(),))

    assert isinstance(result, UnsubscribeExecutionResult)
    assert result.outcome is UnsubscribeOutcome.DONE
    assert browser.calls == ["receipt", "step-captcha-attempt"]


def test_email_otp_continuation_accepts_only_an_appended_submit_form(
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
            {
                "reference": "control-email-otp",
                "kind": "email_otp",
                "intent": "confirm",
            },
        ),
        observation_reference="state-email-otp",
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
                operation_reference="step-email-otp",
                kind=UnsubscribeOperationKind.SUBMIT_FORM,
                target_reference="control-email-otp",
            ),
        ),
        previous_effect_digest=initial.effect_digest,
    )

    claim = store.claim_email_unsubscribe_write(
        **UnsubscribeExecutor._store_arguments(extension),
        owner=RESTART_OWNER,
    )

    assert claim is not None and claim["acquired"] is True
    assert claim["executed_prefix_length"] == 1


def test_unsupported_credential_continuation_requires_human_without_secret(
    tmp_path: Path,
) -> None:
    credential = UnsubscribeDiscoveredControl(
        reference="control-credential",
        kind="credential_handoff",
        intent="confirm",
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
                state_reference="state-credential",
                controls=(credential,),
            ),
        ]
    )

    result = _executor(tmp_path, browser).execute(_effect(), (_entry(),))

    assert isinstance(result, UnsubscribeContinuationResult)
    assert result.continuation.requires_human is True
    serialized = json.dumps(result.redacted, sort_keys=True)
    assert "password" not in serialized
    assert "847201" not in serialized


def test_user_completed_credential_handoff_resumes_without_replaying_prefix(
    tmp_path: Path,
) -> None:
    store = _authorized_store(tmp_path)
    initial = _effect()
    credential = UnsubscribeDiscoveredControl(
        reference="control-credential",
        kind="credential_handoff",
        intent="confirm",
    )
    first = UnsubscribeExecutor(
        store,
        _ScriptedBrowser(
            [
                UnsubscribeObservation(
                    state=UnsubscribePageState.ACTION_REQUIRED,
                    state_reference="state-not-opened",
                    next_operation_reference="step-1",
                ),
                UnsubscribeObservation(
                    state=UnsubscribePageState.ACTION_REQUIRED,
                    state_reference="state-credential",
                    controls=(credential,),
                ),
            ]
        ),
        owner=UNSUBSCRIBE_OWNER,
    ).execute(initial, (_entry(),))
    assert isinstance(first, UnsubscribeContinuationResult)

    resumed = _effect(
        initial.operations
        + (
            UnsubscribeOperation(
                operation_reference="step-resume",
                kind=UnsubscribeOperationKind.RECONCILE_HANDOFF,
                target_reference=credential.reference,
            ),
        ),
        previous_effect_digest=initial.effect_digest,
    )
    receipt = _terminal_receipt(resumed)
    browser = _ScriptedBrowser(
        [
            UnsubscribeObservation(
                state=UnsubscribePageState.DONE,
                state_reference="state-done-after-user",
                receipt=receipt,
            )
        ]
    )

    result = UnsubscribeExecutor(
        EmailStore(store.path),
        browser,
        owner=RESTART_OWNER,
    ).execute(resumed, (_entry(),))

    assert isinstance(result, UnsubscribeExecutionResult)
    assert result.outcome is UnsubscribeOutcome.DONE
    assert browser.calls == ["receipt", "step-resume"]


def test_unsubscribe_executor_has_no_automatic_continuation_api(
    tmp_path: Path,
) -> None:
    constructor = inspect.signature(UnsubscribeExecutor).parameters
    execution = inspect.signature(UnsubscribeExecutor.execute).parameters
    dedicated = inspect.signature(
        __import__(
            "app.email_unsubscribe",
            fromlist=["execute_unsubscribe_in_dedicated_profile"],
        ).execute_unsubscribe_in_dedicated_profile
    ).parameters

    assert "automatic_continuation" not in constructor
    assert "automatic" not in execution
    assert "_automatic_depth" not in execution
    assert "automatic" not in dedicated
    assert "automatic_continuation" not in dedicated
    assert not hasattr(
        EmailStore(tmp_path / "worker.sqlite3"),
        "continue_email_unsubscribe_consumer_direct",
    )


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
        (
            lambda effect: _effect(
                effect.operations[1:], previous_effect_digest=effect.effect_digest
            ),
            "prefix",
        ),
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
        category=EmailCategory.JUNK,
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
        confirmed_category=EmailCategory.JUNK,
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

    assert (
        store.claim_email_unsubscribe_write(
            **UnsubscribeExecutor._store_arguments(extension),
            owner=RESTART_OWNER,
        )
        is None
    )
    assert store.get_email_unsubscribe_claim(ACTION_IDENTITY)["status"] == (
        "awaiting_audit"
    )


def test_email_browser_profile_is_owner_only_and_serializes_access(
    tmp_path: Path,
) -> None:
    profile = EmailBrowserProfile(tmp_path / "runtime")

    with profile.lock(timeout_seconds=0) as profile_path:
        assert profile_path == tmp_path / "runtime" / "email-browser-profile"
        assert profile_path.stat().st_mode & 0o777 == 0o700
        assert profile.runtime_root.stat().st_mode & 0o777 == 0o700
        with pytest.raises(EmailBrowserProfileError, match="busy"):
            with profile.lock(timeout_seconds=0):
                pass


def test_email_browser_profile_rejects_symlink_and_main_browser_locations(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    target = tmp_path / "outside"
    target.mkdir()
    (runtime / "email-browser-profile").symlink_to(target, target_is_directory=True)
    with pytest.raises(EmailBrowserProfileError, match="symlink"):
        EmailBrowserProfile(runtime)

    with pytest.raises(EmailBrowserProfileError, match="main browser"):
        EmailBrowserProfile(
            Path.home() / "Library/Application Support/Google/Chrome",
            name="email-browser-profile",
        )


def test_persistent_email_browser_launch_is_always_headless_and_dedicated(
    tmp_path: Path,
) -> None:
    calls: list[dict[str, object]] = []

    class Chromium:
        def launch_persistent_context(self, **kwargs):
            calls.append(kwargs)
            return "context"

    class Playwright:
        chromium = Chromium()

    profile = EmailBrowserProfile(tmp_path / "runtime")
    assert launch_persistent_email_context(Playwright(), profile) == "context"
    assert calls == [
        {
            "user_data_dir": str(tmp_path / "runtime" / "email-browser-profile"),
            "headless": True,
            "accept_downloads": False,
        }
    ]
    with pytest.raises(EmailBrowserProfileError, match="headless"):
        launch_persistent_email_context(Playwright(), profile, headless=False)


def test_email_browser_audit_session_is_owner_only_atomic_and_action_scoped(
    tmp_path: Path,
) -> None:
    profile = EmailBrowserProfile(tmp_path / "runtime")
    private_url = "https://news.example.com/unsubscribe?token=session-private"
    payload = {
        "version": 2,
        "session_reference": "email-browser-session:" + "9" * 64,
        "action_identity": ACTION_IDENTITY,
        "effect_digest": "a" * 64,
        "entry_reference": unsubscribe_entry_reference(private_url),
        "control_references": ["unsubscribe-control:" + "b" * 64],
        "network_policy_reference": "network-policy:test",
        "network_policy_origin_references": ["origin:test"],
    }

    profile.save_audit_session(ACTION_IDENTITY, payload)

    assert profile.load_audit_session(ACTION_IDENTITY) == payload
    session_files = tuple(profile.profile_dir.glob("audit-sessions/*.json"))
    assert len(session_files) == 1
    assert ACTION_IDENTITY not in session_files[0].name
    assert session_files[0].stat().st_mode & 0o777 == 0o600
    assert session_files[0].parent.stat().st_mode & 0o777 == 0o700
    assert private_url.encode() not in profile.lock_path.read_bytes()
    assert not tuple(profile.runtime_root.glob("*.json"))


def test_live_browser_session_resumes_the_same_page_and_supports_user_handoff(
    tmp_path: Path,
) -> None:
    profile = EmailBrowserProfile(tmp_path / "runtime")
    manager = EmailBrowserSessionManager(profile)
    events = []
    caller_thread = get_ident()

    class Page:
        completed = False

    class Adapter:
        def owner_thread(self):
            return get_ident()

    page = Page()
    adapter = Adapter()
    reference, started = manager.start(
        action_identity=ACTION_IDENTITY,
        effect_digest="a" * 64,
        open_session=lambda: (
            object(),
            page,
            adapter,
            lambda: events.append("closed"),
        ),
    )
    profile.save_audit_session(
        ACTION_IDENTITY,
        {"version": 2, "session_reference": reference},
    )

    resumed = manager.resume(
        session_reference=reference,
        action_identity=ACTION_IDENTITY,
        previous_effect_digest="a" * 64,
    )
    handoff_result: list[object] = []

    def invoke_handoff() -> None:
        handoff_result.append(
            manager.handoff_action(
                ACTION_IDENTITY,
                lambda live_page: (
                    setattr(live_page, "completed", True),
                    get_ident(),
                )[1],
            )
        )

    handoff_thread = Thread(target=invoke_handoff)
    handoff_thread.start()
    handoff_thread.join(timeout=2)

    assert not handoff_thread.is_alive()
    assert started.owner_thread() != caller_thread
    assert resumed.owner_thread() == started.owner_thread()
    assert handoff_result == [started.owner_thread()]
    assert page.completed is True
    assert reference.startswith("email-browser-session:")
    assert TOKEN_URL not in reference
    manager.close_action(ACTION_IDENTITY)
    assert events == ["closed"]


def test_email_browser_session_lease_expires_releases_profile_and_allows_restart(
    tmp_path: Path,
) -> None:
    now = [100.0]
    profile = EmailBrowserProfile(tmp_path / "runtime")
    manager = EmailBrowserSessionManager(
        profile,
        clock=lambda: now[0],
        lease_ttl_seconds=30,
    )
    events: list[str] = []

    def open_session(label: str):
        return (
            object(),
            object(),
            object(),
            lambda: events.append(label),
        )

    reference, _ = manager.start(
        action_identity=ACTION_IDENTITY,
        effect_digest="a" * 64,
        open_session=lambda: open_session("expired"),
    )
    now[0] = 131.0

    with pytest.raises(EmailBrowserProfileError, match="expired"):
        manager.resume(
            session_reference=reference,
            action_identity=ACTION_IDENTITY,
            previous_effect_digest="a" * 64,
        )

    assert events == ["expired"]
    new_reference, _ = manager.start(
        action_identity="email-action:replacement",
        effect_digest="b" * 64,
        open_session=lambda: open_session("replacement"),
    )
    assert new_reference != reference
    manager.close_action("email-action:replacement")
    assert events == ["expired", "replacement"]


def test_dedicated_profile_continuation_reuses_live_browser_without_replaying_prefix(
    tmp_path: Path,
) -> None:
    profile = EmailBrowserProfile(tmp_path / "runtime")
    store = _authorized_store(tmp_path)
    control = UnsubscribeDiscoveredControl(
        reference="control-credential",
        kind="credential_handoff",
        intent="confirm",
    )

    class LiveBrowser(_ScriptedBrowser):
        connected_recipient = ""
        email_otp_resolver = None

        def capture_audit_session(self, effect, *, session_reference):
            return {
                "version": 2,
                "session_reference": session_reference,
                "action_identity": effect.action_identity,
                "effect_digest": effect.effect_digest,
                "entry_reference": effect.entry_reference,
                "control_references": [control.reference],
                "network_policy_reference": effect.network_policy_reference,
                "network_policy_origin_references": list(
                    effect.network_policy_origin_references
                ),
            }

        def validate_restored_audit_session(self, *_args, **_kwargs):
            self.calls.append("validate-live-session")

    browser = LiveBrowser(
        [
            UnsubscribeObservation(
                state=UnsubscribePageState.ACTION_REQUIRED,
                state_reference="state-not-opened",
                next_operation_reference="step-1",
            ),
            UnsubscribeObservation(
                state=UnsubscribePageState.ACTION_REQUIRED,
                state_reference="state-credential",
                controls=(control,),
            ),
        ]
    )

    class Manager:
        reference = "email-browser-session:" + "7" * 64
        digest = ""
        closed = False

        def start(self, *, action_identity, effect_digest, open_session):
            del action_identity, open_session
            self.digest = effect_digest
            return self.reference, browser

        def retain(self, action_identity, *, effect_digest):
            del action_identity
            self.digest = effect_digest

        def resume(self, *, session_reference, action_identity, previous_effect_digest):
            del action_identity
            assert session_reference == self.reference
            assert previous_effect_digest == self.digest
            return browser

        def close_action(self, action_identity):
            del action_identity
            self.closed = True

    manager = Manager()
    first = execute_unsubscribe_in_dedicated_profile(
        _effect(),
        (_entry(),),
        store=store,
        profile=profile,
        network_policy=BrowserNetworkPolicy(
            frozenset({"https://news.example.com"})
        ),
        owner=UNSUBSCRIBE_OWNER,
        session_manager=manager,
    )
    assert isinstance(first, UnsubscribeContinuationResult), (
        first.outcome,
        first.error_code,
        first.journal,
    )

    resumed_effect = _effect(
        _effect().operations
        + (
            UnsubscribeOperation(
                operation_reference="step-reconcile",
                kind=UnsubscribeOperationKind.RECONCILE_HANDOFF,
                target_reference=control.reference,
            ),
        ),
        previous_effect_digest=_effect().effect_digest,
    )
    browser.observations.append(
        UnsubscribeObservation(
            state=UnsubscribePageState.DONE,
            state_reference="state-user-completed",
            receipt=_terminal_receipt(resumed_effect),
        )
    )
    claimed = EmailStore(store.path).claim_email_unsubscribe_write(
        **UnsubscribeExecutor._store_arguments(resumed_effect),
        owner=RESTART_OWNER,
    )
    assert claimed is not None and claimed["acquired"] is True
    second = execute_unsubscribe_in_dedicated_profile(
        resumed_effect,
        (_entry(),),
        store=EmailStore(store.path),
        profile=profile,
        network_policy=BrowserNetworkPolicy(
            frozenset({"https://news.example.com"})
        ),
        owner=RESTART_OWNER,
        executed_prefix_length=1,
        session_manager=manager,
    )

    assert isinstance(second, UnsubscribeExecutionResult)
    assert second.outcome is UnsubscribeOutcome.DONE
    assert browser.calls == [
        "receipt",
        "inspect",
        "step-1",
        "validate-live-session",
        "receipt",
        "step-reconcile",
    ]
    assert manager.closed is True


@pytest.mark.parametrize(
    "tamper",
    (
        "effect_digest",
        "entry_reference",
        "control_reference",
        "network_policy",
    ),
)
def test_restored_audit_session_is_bound_to_exact_effect_and_appended_control(
    tamper: str,
) -> None:
    initial = _effect()
    control_reference = "unsubscribe-control:" + "b" * 64
    extension = _effect(
        initial.operations
        + (
            UnsubscribeOperation(
                operation_reference="step-2",
                kind=UnsubscribeOperationKind.SUBMIT_FORM,
                target_reference=control_reference,
            ),
        ),
        previous_effect_digest=initial.effect_digest,
    )
    payload = {
        "version": 2,
        "session_reference": "email-browser-session:" + "9" * 64,
        "action_identity": extension.action_identity,
        "effect_digest": initial.effect_digest,
        "entry_reference": extension.entry_reference,
        "control_references": [control_reference],
        "network_policy_reference": extension.network_policy_reference,
        "network_policy_origin_references": list(
            extension.network_policy_origin_references
        ),
    }
    if tamper == "effect_digest":
        payload["effect_digest"] = "0" * 64
    elif tamper == "entry_reference":
        payload["entry_reference"] = "unsubscribe-entry:wrong"
    elif tamper == "control_reference":
        payload["control_references"] = ["unsubscribe-control:" + "c" * 64]
    else:
        payload["network_policy_reference"] = "network-policy:wrong"

    with pytest.raises(UnsubscribeBrowserError, match="browser session") as error:
        _validated_restored_audit_session(
            payload,
            extension,
            executed_prefix_length=1,
        )

    assert "private-token" not in str(error.value)


def test_restored_credential_handoff_accepts_only_readback_resume_operation() -> None:
    initial = _effect()
    control_reference = "auth-control:" + "d" * 64
    extension = _effect(
        initial.operations
        + (
            UnsubscribeOperation(
                operation_reference="step-resume",
                kind=UnsubscribeOperationKind.RECONCILE_HANDOFF,
                target_reference=control_reference,
            ),
        ),
        previous_effect_digest=initial.effect_digest,
    )
    payload = {
        "version": 2,
        "session_reference": "email-browser-session:" + "9" * 64,
        "action_identity": extension.action_identity,
        "effect_digest": initial.effect_digest,
        "entry_reference": extension.entry_reference,
        "control_references": [control_reference],
        "network_policy_reference": extension.network_policy_reference,
        "network_policy_origin_references": list(
            extension.network_policy_origin_references
        ),
    }

    restored = _validated_restored_audit_session(
        payload,
        extension,
        executed_prefix_length=1,
    )

    assert restored.control_references == (control_reference,)
    assert "password" not in repr(restored)


def test_user_handoff_reconciliation_allows_the_live_page_to_reach_terminal_state() -> None:
    initial = _effect()
    control_reference = "auth-control:" + "d" * 64
    extension = _effect(
        initial.operations
        + (
            UnsubscribeOperation(
                operation_reference="step-reconcile",
                kind=UnsubscribeOperationKind.RECONCILE_HANDOFF,
                target_reference=control_reference,
            ),
        ),
        previous_effect_digest=initial.effect_digest,
    )
    session = _validated_restored_audit_session(
        {
            "version": 2,
            "session_reference": "email-browser-session:" + "9" * 64,
            "action_identity": extension.action_identity,
            "effect_digest": initial.effect_digest,
            "entry_reference": extension.entry_reference,
            "control_references": [control_reference],
            "network_policy_reference": extension.network_policy_reference,
            "network_policy_origin_references": list(
                extension.network_policy_origin_references
            ),
        },
        extension,
        executed_prefix_length=1,
    )
    browser = object.__new__(PlaywrightUnsubscribeBrowser)
    browser.discover_current_page = lambda effect: UnsubscribePageDiscovery(
        state=UnsubscribePageState.DONE,
        state_reference="state-user-completed",
        controls=(),
    )

    browser.validate_restored_audit_session(
        extension,
        session,
        executed_prefix_length=1,
    )
