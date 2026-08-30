from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
from threading import Thread
from urllib.parse import urlsplit

import pytest

from app.agent_contracts import ProposedAction
from app.email_classifier_contracts import (
    EmailAction,
    EmailCategory,
    EmailClassification,
    EmailClassificationStatus,
    build_versioned_email_action_plan,
)
from app.email_store import EmailStore, email_action_identity
from app.email_task_adapter import (
    EmailAgentTaskAdapter,
    EmailAgentTaskInput,
    EmailThreadMessage,
    accepted_email_unsubscribe_effect,
)
from app.email_unsubscribe import (
    BrowserNetworkPolicy,
    ConfirmationNavigationTarget,
    EmailUnsubscribeEffect,
    PlaywrightUnsubscribeBrowser,
    UnsubscribeEntry,
    UnsubscribeEntrySource,
    UnsubscribeContinuationResult,
    UnsubscribeExecutor,
    UnsubscribeOperation,
    UnsubscribeOperationKind,
    UnsubscribeOutcome,
    confirmation_target_reference,
    unsubscribe_entry_reference,
)
from app.store import AutoReplyStore


pytestmark = pytest.mark.skipif(
    os.environ.get("WORKBENCH_BROWSER_TESTS") != "1",
    reason="set WORKBENCH_BROWSER_TESTS=1 to run local browser fixtures",
)

sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright

_BROWSER_OWNER = {
    "owner_id": "email-worker",
    "generation": 41,
    "lease_token": "browser-fixture-owner",
}
_RESTART_OWNER = {
    "owner_id": "email-worker",
    "generation": 42,
    "lease_token": "browser-fixture-restart",
}


def _page(
    state: str,
    visible_text: str,
    *,
    next_step: str = "",
    receipt: str = "",
    evidence: str = "terminal_page",
    content: str = "",
) -> bytes:
    del state, next_step, receipt, evidence
    return (
        "<!doctype html><html><body>"
        f"<main><h1>{visible_text}</h1>{content}</main>"
        "</body></html>"
    ).encode("utf-8")


class _FixtureHandler(BaseHTTPRequestHandler):
    requests: list[tuple[str, str]] = []
    request_details: list[dict[str, str]] = []
    blocked_origin = ""

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _send(self, body: bytes, *, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        type(self).requests.append(("GET", self.path))
        path = urlsplit(self.path).path
        if path == "/direct":
            self._send(
                _page(
                    "done",
                    "You are unsubscribed",
                    receipt="receipt-direct",
                )
            )
        elif path == "/malicious-redirect":
            self.send_response(302)
            self.send_header("Location", f"{type(self).blocked_origin}/effect")
            self.end_headers()
        elif path == "/malicious-iframe":
            self._send(
                _page(
                    "action_required",
                    "Choose unsubscribe",
                    content=f'<iframe src="{type(self).blocked_origin}/effect"></iframe>',
                )
            )
        elif path == "/malicious-fetch":
            self._send(
                _page(
                    "action_required",
                    "Choose unsubscribe",
                    content=(
                        "<script>fetch('"
                        f"{type(self).blocked_origin}/effect"
                        "')</script>"
                    ),
                )
            )
        elif path == "/malicious-image":
            self._send(
                _page(
                    "action_required",
                    "Choose unsubscribe",
                    content=f'<img src="{type(self).blocked_origin}/effect">',
                )
            )
        elif path == "/malicious-form":
            self._send(
                _page(
                    "action_required",
                    "Choose unsubscribe",
                    content=(
                        f'<form method="post" action="{type(self).blocked_origin}/effect">'
                        '<button type="submit">Unsubscribe</button></form>'
                    ),
                )
            )
        elif path == "/malicious-popup":
            self._send(
                _page(
                    "action_required",
                    "Choose unsubscribe",
                    content=(
                        f'<a target="_blank" href="{type(self).blocked_origin}/effect">'
                        "Confirm</a>"
                    ),
                )
            )
        elif path == "/malicious-download":
            self._send(
                _page(
                    "action_required",
                    "Choose unsubscribe",
                    content=(
                        f'<a download href="{type(self).blocked_origin}/effect">'
                        "Confirm</a>"
                    ),
                )
            )
        elif path == "/unknown":
            self._send(
                _page(
                    "action_required",
                    "Account preferences",
                    content='<a href="/terminal-click">Learn more</a>',
                )
            )
        elif path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/terminal-redirect")
            self.end_headers()
        elif path == "/terminal-redirect":
            self._send(
                _page(
                    "done",
                    "Redirect unsubscribe complete",
                    receipt="receipt-redirect",
                )
            )
        elif path == "/two-step":
            self._send(
                _page(
                    "action_required",
                    "Choose unsubscribe",
                    next_step="step-2",
                    content=(
                        '<form method="post" action="/two-step-second" '
                        '>'
                        '<button type="submit">Continue</button></form>'
                    ),
                )
            )
        elif path == "/mutable-link":
            self._send(
                _page(
                    "action_required",
                    "Confirm unsubscribe",
                    content=(
                        '<a href="/unsubscribe" '
                        'data-operation-reference="customerSegmentPlatinum42">'
                        "Confirm</a>"
                    ),
                )
            )
        elif path == "/mutable-form":
            self._send(
                _page(
                    "action_required",
                    "Confirm unsubscribe",
                    content=(
                        '<form method="post" action="/safe-form-terminal">'
                        '<input type="hidden" name="segment" value="basic">'
                        '<input name="scope" value="newsletter">'
                        '<button type="submit" name="decision" value="unsubscribe">'
                        "Unsubscribe</button></form>"
                    ),
                )
            )
        elif path == "/implicit-button-omitted":
            self._send(
                _page(
                    "action_required",
                    "Confirm unsubscribe",
                    content=(
                        '<form method="post" action="/implicit-terminal">'
                        '<input type="hidden" name="flow" value="omitted">'
                        '<button name="decision" value="unsubscribe">Unsubscribe</button>'
                        "</form>"
                    ),
                )
            )
        elif path == "/implicit-button-explicit":
            self._send(
                _page(
                    "action_required",
                    "Confirm unsubscribe",
                    content=(
                        '<form method="post" action="/implicit-terminal">'
                        '<input type="hidden" name="flow" value="button">'
                        '<button type="submit" name="decision" value="unsubscribe">'
                        "Unsubscribe</button></form>"
                    ),
                )
            )
        elif path == "/implicit-input":
            self._send(
                _page(
                    "action_required",
                    "Confirm unsubscribe",
                    content=(
                        '<form method="post" action="/implicit-terminal">'
                        '<input type="hidden" name="flow" value="input">'
                        '<input type="submit" name="decision" value="Unsubscribe">'
                        "</form>"
                    ),
                )
            )
        elif path == "/delete-profile":
            self._send(_page("done", "Profile deleted"))
        elif path == "/final-click":
            self._send(
                _page(
                    "action_required",
                    "Confirm unsubscribe",
                    next_step="step-2",
                    content=(
                        '<a href="/terminal-click" '
                        '>Confirm</a>'
                    ),
                )
            )
        elif path in {"/terminal-click", "/unsubscribe"}:
            self._send(
                _page(
                    "done",
                    "Unsubscribe confirmation complete",
                    receipt="receipt-click",
                )
            )
        elif path == "/already":
            self._send(
                _page(
                    "already_unsubscribed",
                    "Already unsubscribed",
                    receipt="receipt-already",
                )
            )
        elif path in {"/login", "/captcha", "/payment"}:
            state = {
                "/login": "login_required",
                "/captcha": "captcha",
                "/payment": "payment",
            }[path]
            self._send(
                _page(
                    state,
                    f"Business stop: {state}",
                    receipt=f"receipt-{state}",
                )
            )
        elif path == "/confirmation-email":
            self._send(
                _page(
                    "action_required",
                    "Check your confirmation email",
                    next_step="step-2",
                )
            )
        elif path == "/confirmation-receipt":
            self._send(
                _page(
                    "done",
                    "Confirmation email link completed",
                    content="<p>You are unsubscribed.</p>",
                    receipt="receipt-confirmation-mail",
                    evidence="confirmation_mail",
                )
            )
        else:
            self._send(b"not found", status=404)

    def do_POST(self) -> None:  # noqa: N802
        content_length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(content_length) if content_length else b""
        type(self).requests.append(("POST", self.path))
        type(self).request_details.append(
            {
                "body": body.decode("utf-8"),
                "cookie": self.headers.get("Cookie", ""),
                "content_type": self.headers.get("Content-Type", ""),
            }
        )
        path = urlsplit(self.path).path
        if path == "/two-step-second":
            self._send(
                _page(
                    "action_required",
                    "Final form confirmation",
                    next_step="step-3",
                    content=(
                        '<form method="post" action="/two-step-terminal" '
                        '>'
                        '<button type="submit">Unsubscribe</button></form>'
                    ),
                )
            )
        elif path == "/two-step-terminal":
            self._send(
                _page(
                    "done",
                    "Two step unsubscribe complete",
                    receipt="receipt-two-step",
                )
            )
        elif path in {"/safe-form-terminal", "/implicit-terminal"}:
            self._send(_page("done", "You are unsubscribed"))
        elif path == "/delete-profile":
            self._send(_page("done", "Profile deleted"))
        elif path == "/one-click":
            self._send(_page("done", "You are unsubscribed"))
        else:
            self._send(b"not found", status=404)


class _BlockedHandler(BaseHTTPRequestHandler):
    requests: list[tuple[str, str]] = []

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _record(self, method: str) -> None:
        type(self).requests.append((method, self.path))
        self.send_response(204)
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        self._record("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._record("POST")


class _LoopbackHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False


@contextmanager
def _loopback_server():
    _FixtureHandler.requests = []
    _FixtureHandler.request_details = []
    server = _LoopbackHTTPServer(("127.0.0.1", 0), _FixtureHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        shutdown = Thread(target=server.shutdown, daemon=True)
        shutdown.start()
        shutdown.join(timeout=5)
        server.server_close()
        thread.join(timeout=5)
        assert not shutdown.is_alive()
        assert not thread.is_alive()


@contextmanager
def _loopback_server_pair():
    _FixtureHandler.requests = []
    _FixtureHandler.request_details = []
    _BlockedHandler.requests = []
    blocked = _LoopbackHTTPServer(("127.0.0.1", 0), _BlockedHandler)
    blocked_thread = Thread(target=blocked.serve_forever, daemon=True)
    blocked_thread.start()
    blocked_origin = f"http://127.0.0.1:{blocked.server_port}"
    _FixtureHandler.blocked_origin = blocked_origin
    allowed = _LoopbackHTTPServer(("127.0.0.1", 0), _FixtureHandler)
    allowed_thread = Thread(target=allowed.serve_forever, daemon=True)
    allowed_thread.start()
    try:
        yield f"http://127.0.0.1:{allowed.server_port}", blocked_origin
    finally:
        for server, thread in (
            (allowed, allowed_thread),
            (blocked, blocked_thread),
        ):
            shutdown = Thread(target=server.shutdown, daemon=True)
            shutdown.start()
            shutdown.join(timeout=5)
            server.server_close()
            thread.join(timeout=5)
            assert not shutdown.is_alive()
            assert not thread.is_alive()


@pytest.fixture(scope="module")
def chrome_browser():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            channel="chrome",
            timeout=10_000,
        )
        yield browser
        browser.close()


def _operations(
    *kinds: UnsubscribeOperationKind,
    targets: tuple[str, ...] = (),
) -> tuple[UnsubscribeOperation, ...]:
    return tuple(
        UnsubscribeOperation(
            operation_reference=f"step-{index}",
            kind=kind,
            target_reference=(
                "entry" if index == 1 else targets[index - 2]
            ),
        )
        for index, kind in enumerate(kinds, start=1)
    )


def test_browser_requires_mandatory_exact_origin_policy(chrome_browser) -> None:
    context = chrome_browser.new_context()
    page = context.new_page()
    try:
        with pytest.raises(ValueError, match="network policy"):
            PlaywrightUnsubscribeBrowser(page, timeout_ms=500)
    finally:
        context.close()


def test_production_policy_rejects_private_and_loopback_origins() -> None:
    for origin in (
        "http://127.0.0.1:8080",
        "https://localhost:443",
        "https://10.0.0.4:443",
        "https://169.254.169.254:443",
        "https://metadata.google.internal:443",
    ):
        with pytest.raises(ValueError, match="network policy") as error:
            BrowserNetworkPolicy(allowed_origins=frozenset({origin}))
        assert origin not in str(error.value)


def _setup(
    tmp_path: Path,
    private_url: str,
    operations: tuple[UnsubscribeOperation, ...],
) -> tuple[EmailStore, EmailUnsubscribeEffect, UnsubscribeEntry]:
    plan = build_versioned_email_action_plan(
        action_plan_version=1,
        classification_id=701,
        account_id="fixture-account",
        category=EmailCategory.SUBSCRIPTION,
        classification_source="model",
        confidence=0.99,
        model_id="email-model:browser-fixture",
        config_version="email-config:browser-fixture",
        actions=(EmailAction.UNSUBSCRIBE,),
        action_parameters={},
        created_at=datetime(2026, 8, 30, 8, 0, tzinfo=timezone.utc),
    )
    stable_identity = "fixture-account:message-id:<browser-701@example.com>"
    store = EmailStore(tmp_path / "browser-unsubscribe.sqlite3")
    store.create_account(
        {
            "account_id": "fixture-account",
            "display_name": "Fixture",
            "email_address": "fixture@example.com",
            "imap_host": "imap.example.com",
            "imap_port": 993,
            "imap_tls": True,
            "imap_username": "fixture@example.com",
            "imap_secret_reference": "keychain://browser-imap",
            "smtp_host": "smtp.example.com",
            "smtp_port": 465,
            "smtp_tls": True,
            "smtp_username": "fixture@example.com",
            "smtp_secret_reference": "keychain://browser-smtp",
            "enabled": True,
            "scan_folders": ["INBOX"],
            "scan_interval_seconds": 60,
        }
    )
    store.upsert_classification(
        EmailClassification.model_validate(
            {
                "classification_id": 701,
                "stable_message_identity": stable_identity,
                "provider_locator": {
                    "account_id": "fixture-account",
                    "folder": "INBOX",
                    "uidvalidity": 77,
                    "uid": 701,
                    "rfc_message_id": "<browser-701@example.com>",
                    "thread_id": "fixture-thread",
                },
                "category": EmailCategory.SUBSCRIPTION,
                "confidence": 0.99,
                "margin": 0.5,
                "probabilities": {"subscription": 0.99},
                "model_id": plan.model_id,
                "config_version": plan.config_version,
                "status": EmailClassificationStatus.PROCESSED,
                "classification_source": "model",
                "action_plan": plan,
            }
        ),
        sender="newsletter@example.com",
        subject="Fixture newsletter",
        model_text="__subject__fixture newsletter",
        received_at="2026-08-30T08:00:00+00:00",
    )
    entry = UnsubscribeEntry(
        source=UnsubscribeEntrySource.BODY_HTML_HTTPS,
        reference=unsubscribe_entry_reference(private_url),
        private_url=private_url,
        priority=30,
    )
    parsed = urlsplit(private_url)
    origin = (
        f"{parsed.scheme}://{parsed.hostname}:"
        f"{parsed.port or (443 if parsed.scheme == 'https' else 80)}"
    )
    policy = BrowserNetworkPolicy(
        allowed_origins=frozenset({origin}),
        allow_loopback_for_tests=True,
    )
    effect = EmailUnsubscribeEffect(
        action_identity=email_action_identity(
            account_id="fixture-account",
            stable_message_identity=stable_identity,
            action_type=EmailAction.UNSUBSCRIBE,
            action_plan_version=1,
        ),
        action_plan_id=plan.action_plan_id,
        action_plan_version=1,
        classification_id=701,
        account_id="fixture-account",
        stable_message_identity=stable_identity,
        thread_identity="fixture-thread",
        entry_reference=entry.reference,
        operations=operations,
        network_policy_reference=policy.reference,
        network_policy_origin_references=policy.origin_references,
    )
    return store, effect, entry


def _run(
    tmp_path: Path,
    chrome_browser,
    *,
    path: str,
    operations: tuple[UnsubscribeOperation, ...],
    confirmation_path: str = "",
    confirmation_binding: str = "valid",
    recover_uncertain: bool = False,
):
    with _loopback_server() as origin:
        private_url = f"{origin}{path}?opaque=private-fixture-token"
        store, proposed_effect, entry = _setup(tmp_path, private_url, operations)
        effect = replace(
            proposed_effect,
            operations=(operations[0],),
            previous_effect_digest="",
        )
        owner = _BROWSER_OWNER
        if recover_uncertain:
            claim = store.claim_email_unsubscribe_write(
                **UnsubscribeExecutor._store_arguments(effect),
                owner=_BROWSER_OWNER,
            )
            assert claim is not None and claim["acquired"] is True
            assert store.recover_terminated_email_unsubscribe_claims(
                owner=_BROWSER_OWNER,
                termination_verifier=lambda candidate: candidate == _BROWSER_OWNER,
                recovered_at="2026-08-30T12:00:00+00:00",
            ) == 1
            owner = _RESTART_OWNER
        context = chrome_browser.new_context()
        page = context.new_page()
        confirmation_message_identity = (
            "wrong-confirmation-message"
            if confirmation_binding == "wrong_mail"
            else "fixture-confirmation-message"
        )
        confirmation_reference = (
            "confirmation-target:wrong"
            if confirmation_binding == "wrong_target"
            else confirmation_target_reference("fixture-confirmation-message")
        )
        browser = PlaywrightUnsubscribeBrowser(
            page,
            timeout_ms=3_000,
            network_policy=BrowserNetworkPolicy(
                allowed_origins=frozenset({origin}),
                allow_loopback_for_tests=True,
            ),
            confirmation_target_resolver=(
                (
                    lambda candidate: ConfirmationNavigationTarget(
                        private_url=f"{origin}{confirmation_path}",
                        target_reference=confirmation_reference,
                        confirmation_message_identity=confirmation_message_identity,
                        effect_digest=(
                            "0" * 64
                            if confirmation_binding == "wrong_effect"
                            else candidate.effect_digest
                        ),
                    )
                )
                if confirmation_path
                else None
            ),
        )
        try:
            result = UnsubscribeExecutor(
                store,
                browser,
                owner=owner,
            ).execute(effect, (entry,))
            for index, operation in enumerate(operations[1:], start=2):
                if not isinstance(result, UnsubscribeContinuationResult):
                    break
                expected_kind = {
                    UnsubscribeOperationKind.SUBMIT_FORM: "form",
                    UnsubscribeOperationKind.CLICK_CONFIRMATION: "link",
                    UnsubscribeOperationKind.CONFIRM_EMAIL: "confirmation_email",
                }.get(operation.kind)
                discovered = next(
                    (
                        control
                        for control in result.continuation.controls
                        if control.kind == expected_kind
                    ),
                    None,
                )
                if discovered is not None:
                    operation = replace(
                        operation,
                        target_reference=discovered.reference,
                    )
                effect = replace(
                    effect,
                    operations=effect.operations + (operation,),
                    previous_effect_digest=effect.effect_digest,
                )
                result = UnsubscribeExecutor(
                    store,
                    browser,
                    owner={
                        "owner_id": "email-worker",
                        "generation": 40 + index,
                        "lease_token": f"browser-fixture-step-{index}",
                    },
                ).execute(effect, (entry,))
        finally:
            context.close()

        if recover_uncertain:
            assert _FixtureHandler.requests == []
        else:
            assert _FixtureHandler.requests
        assert all(path.startswith("/") for _, path in _FixtureHandler.requests)
        serialized = json.dumps(result.redacted, sort_keys=True)
        assert "private-fixture-token" not in serialized
        assert private_url not in serialized
        return result, tuple(_FixtureHandler.requests)


def test_direct_success_fixture(tmp_path: Path, chrome_browser) -> None:
    result, requests = _run(
        tmp_path,
        chrome_browser,
        path="/direct",
        operations=_operations(UnsubscribeOperationKind.OPEN_ENTRY),
    )
    assert result.outcome is UnsubscribeOutcome.DONE
    assert requests[0][0] == "GET"


def _open_then_execute_discovered_control(
    tmp_path: Path,
    chrome_browser,
    *,
    path: str,
    operation_kind: UnsubscribeOperationKind,
    mutate_dom: str = "",
):
    with _loopback_server() as origin:
        private_url = f"{origin}{path}?opaque=private-fixture-token"
        initial_operation = _operations(UnsubscribeOperationKind.OPEN_ENTRY)[0]
        store, effect, entry = _setup(tmp_path, private_url, (initial_operation,))
        policy = BrowserNetworkPolicy(
            allowed_origins=frozenset({origin}),
            allow_loopback_for_tests=True,
        )
        context = chrome_browser.new_context()
        page = context.new_page()
        browser = PlaywrightUnsubscribeBrowser(
            page,
            timeout_ms=3_000,
            network_policy=policy,
        )
        try:
            first = UnsubscribeExecutor(
                store,
                browser,
                owner=_BROWSER_OWNER,
            ).execute(effect, (entry,))
            assert isinstance(first, UnsubscribeContinuationResult)
            assert len(first.continuation.controls) == 1
            durable = store.get_email_unsubscribe_continuation(effect.action_identity)
            if mutate_dom:
                page.evaluate(mutate_dom)
            extension = replace(
                effect,
                operations=effect.operations
                + (
                    UnsubscribeOperation(
                        operation_reference="step-2",
                        kind=operation_kind,
                        target_reference=first.continuation.controls[0].reference,
                    ),
                ),
                previous_effect_digest=effect.effect_digest,
            )
            result = UnsubscribeExecutor(
                store,
                browser,
                owner=_RESTART_OWNER,
            ).execute(extension, (entry,))
        finally:
            context.close()
        return (
            first,
            result,
            tuple(_FixtureHandler.requests),
            tuple(_FixtureHandler.request_details),
            durable,
        )


def test_page_control_reference_is_local_digest_and_never_persists_dom_attribute(
    tmp_path: Path,
    chrome_browser,
) -> None:
    first, _result, _requests, _details, durable = (
        _open_then_execute_discovered_control(
            tmp_path,
            chrome_browser,
            path="/mutable-link",
            operation_kind=UnsubscribeOperationKind.CLICK_CONFIRMATION,
        )
    )

    serialized = json.dumps(first.redacted, sort_keys=True) + repr(first) + repr(durable)
    assert "customerSegmentPlatinum42" not in serialized
    assert "Confirm unsubscribe" not in serialized
    assert first.continuation.controls[0].reference.startswith(
        "unsubscribe-control:"
    )


def test_mutated_audited_link_target_has_zero_unauthorized_requests(
    tmp_path: Path,
    chrome_browser,
) -> None:
    _first, result, requests, _details, _durable = (
        _open_then_execute_discovered_control(
            tmp_path,
            chrome_browser,
            path="/mutable-link",
            operation_kind=UnsubscribeOperationKind.CLICK_CONFIRMATION,
            mutate_dom=(
                "document.querySelector('a').setAttribute('href', '/delete-profile')"
            ),
        )
    )

    assert result.outcome is UnsubscribeOutcome.FAILED_BROWSER
    assert requests == (("GET", "/mutable-link?opaque=private-fixture-token"),)


@pytest.mark.parametrize(
    "mutation",
    (
        "document.querySelector('form').setAttribute('action', '/delete-profile')",
        "document.querySelector('input[type=hidden]').value = 'customerSegmentPlatinum42'",
    ),
)
def test_mutated_audited_form_semantics_have_zero_unauthorized_requests(
    tmp_path: Path,
    chrome_browser,
    mutation: str,
) -> None:
    _first, result, requests, _details, _durable = (
        _open_then_execute_discovered_control(
            tmp_path,
            chrome_browser,
            path="/mutable-form",
            operation_kind=UnsubscribeOperationKind.SUBMIT_FORM,
            mutate_dom=mutation,
        )
    )

    assert result.outcome is UnsubscribeOutcome.FAILED_BROWSER
    assert requests == (("GET", "/mutable-form?opaque=private-fixture-token"),)


@pytest.mark.parametrize(
    ("path", "expected_body"),
    (
        (
            "/mutable-form",
            "segment=basic&scope=newsletter&decision=unsubscribe",
        ),
        (
            "/implicit-button-omitted",
            "flow=omitted&decision=unsubscribe",
        ),
        (
            "/implicit-button-explicit",
            "flow=button&decision=unsubscribe",
        ),
        (
            "/implicit-input",
            "flow=input&decision=Unsubscribe",
        ),
    ),
)
def test_ordinary_form_executes_exact_audited_submitter(
    tmp_path: Path,
    chrome_browser,
    path: str,
    expected_body: str,
) -> None:
    _first, result, requests, details, _durable = (
        _open_then_execute_discovered_control(
            tmp_path,
            chrome_browser,
            path=path,
            operation_kind=UnsubscribeOperationKind.SUBMIT_FORM,
        )
    )

    assert result.outcome is UnsubscribeOutcome.DONE
    assert [method for method, _path in requests] == ["GET", "POST"]
    assert details[-1]["body"] == expected_body


def test_redirect_success_fixture(tmp_path: Path, chrome_browser) -> None:
    result, requests = _run(
        tmp_path,
        chrome_browser,
        path="/redirect",
        operations=_operations(UnsubscribeOperationKind.OPEN_ENTRY),
    )
    assert result.outcome is UnsubscribeOutcome.DONE
    assert [path for _, path in requests[:2]] == [
        "/redirect?opaque=private-fixture-token",
        "/terminal-redirect",
    ]


def test_two_step_form_fixture(tmp_path: Path, chrome_browser) -> None:
    result, requests = _run(
        tmp_path,
        chrome_browser,
        path="/two-step",
        operations=_operations(
            UnsubscribeOperationKind.OPEN_ENTRY,
            UnsubscribeOperationKind.SUBMIT_FORM,
            UnsubscribeOperationKind.SUBMIT_FORM,
            targets=("fixture-control-1", "fixture-control-2"),
        ),
    )
    assert result.outcome is UnsubscribeOutcome.DONE
    assert [method for method, _ in requests] == ["GET", "POST", "POST"]


def test_task9_to_incremental_audit_uses_only_discovered_opaque_controls(
    tmp_path: Path,
    chrome_browser,
) -> None:
    with _loopback_server() as origin:
        private_url = f"{origin}/two-step?opaque=private-fixture-token"
        policy = BrowserNetworkPolicy(
            allowed_origins=frozenset({origin}),
            allow_loopback_for_tests=True,
        )
        initial_operation = UnsubscribeOperation(
            operation_reference="step-1",
            kind=UnsubscribeOperationKind.OPEN_ENTRY,
            target_reference=unsubscribe_entry_reference(private_url),
        )
        email_store, _, entry = _setup(
            tmp_path,
            private_url,
            (initial_operation,),
        )
        task_store = AutoReplyStore(email_store.path)
        plan = build_versioned_email_action_plan(
            action_plan_version=1,
            classification_id=701,
            account_id="fixture-account",
            category=EmailCategory.SUBSCRIPTION,
            classification_source="model",
            confidence=0.99,
            model_id="email-model:browser-fixture",
            config_version="email-config:browser-fixture",
            actions=(EmailAction.UNSUBSCRIBE,),
            action_parameters={},
            created_at=datetime(2026, 8, 30, 8, 0, tzinfo=timezone.utc),
        )
        task_input = EmailAgentTaskInput(
            stable_message_identity=(
                "fixture-account:message-id:<browser-701@example.com>"
            ),
            thread_identity="fixture-thread",
            subject="Fixture newsletter",
            trigger=EmailThreadMessage(
                message_id=(
                    "fixture-account:message-id:<browser-701@example.com>"
                ),
                sender="newsletter@example.com",
                text="Newsletter body without attachment content.",
                create_time="2026-08-30T08:00:00+00:00",
            ),
            list_unsubscribe=f"<{private_url}>",
            unsubscribe_network_policy_reference=policy.reference,
            unsubscribe_network_policy_origin_references=policy.origin_references,
            unsubscribe_allow_loopback_for_tests=True,
        )
        route = EmailAgentTaskAdapter(
            task_store,
            email_store,
        ).ensure_action_plan_tasks(plan, task_input)[0]
        metadata = json.loads(route.task.trigger_message_json)

        def accepted(operations: tuple[UnsubscribeOperation, ...]) -> ProposedAction:
            return ProposedAction.model_validate(
                {
                    "description": "Execute one audited unsubscribe step",
                    "capability": "email_browser",
                    "operation": "unsubscribe",
                    "target": {
                        "action_identity": metadata["action_identity"],
                        "account_id": metadata["account_id"],
                        "stable_message_identity": metadata[
                            "stable_message_identity"
                        ],
                        "thread_identity": metadata["thread_identity"],
                        "entry_reference": metadata["unsubscribe_entries"][0][
                            "reference"
                        ],
                        "network_policy_reference": metadata[
                            "unsubscribe_network_policy_reference"
                        ],
                        "network_policy_origin_references": metadata[
                            "unsubscribe_network_policy_origin_references"
                        ],
                    },
                    "payload": {
                        "operations": [
                            {
                                "operation_reference": item.operation_reference,
                                "kind": item.kind.value,
                                "target_reference": item.target_reference,
                            }
                            for item in operations
                        ]
                    },
                    "expected_verification": "Read redacted provider state.",
                }
            )

        effect = accepted_email_unsubscribe_effect(
            route.task,
            accepted((initial_operation,)),
        )
        context = chrome_browser.new_context()
        browser = PlaywrightUnsubscribeBrowser(
            context.new_page(),
            timeout_ms=3_000,
            network_policy=policy,
        )
        try:
            first = UnsubscribeExecutor(
                email_store,
                browser,
                owner=_BROWSER_OWNER,
            ).execute(effect, (entry,))
            assert isinstance(first, UnsubscribeContinuationResult)
            assert [method for method, _ in _FixtureHandler.requests] == ["GET"]
            assert len(first.continuation.controls) == 1

            second_operation = UnsubscribeOperation(
                operation_reference="step-2",
                kind=UnsubscribeOperationKind.SUBMIT_FORM,
                target_reference=first.continuation.controls[0].reference,
            )
            second_effect = accepted_email_unsubscribe_effect(
                route.task,
                accepted(effect.operations + (second_operation,)),
                continuation=first.continuation,
            )
            second = UnsubscribeExecutor(
                email_store,
                browser,
                owner=_RESTART_OWNER,
            ).execute(second_effect, (entry,))
            assert isinstance(second, UnsubscribeContinuationResult)
            assert [method for method, _ in _FixtureHandler.requests] == ["GET", "POST"]

            third_operation = UnsubscribeOperation(
                operation_reference="step-3",
                kind=UnsubscribeOperationKind.SUBMIT_FORM,
                target_reference=second.continuation.controls[0].reference,
            )
            third_effect = accepted_email_unsubscribe_effect(
                route.task,
                accepted(second_effect.operations + (third_operation,)),
                continuation=second.continuation,
            )
            terminal = UnsubscribeExecutor(
                email_store,
                browser,
                owner={
                    "owner_id": "email-worker",
                    "generation": 43,
                    "lease_token": "browser-fixture-final",
                },
            ).execute(third_effect, (entry,))
        finally:
            context.close()

    assert terminal.outcome is UnsubscribeOutcome.DONE
    assert [method for method, _ in _FixtureHandler.requests] == ["GET", "POST", "POST"]
    serialized = json.dumps(
        {
            "first": first.redacted,
            "second": second.redacted,
            "terminal": terminal.redacted,
        },
        sort_keys=True,
    )
    assert private_url not in serialized
    assert "private-fixture-token" not in serialized


def test_read_only_discovery_returns_only_ordinary_opaque_controls(
    tmp_path: Path,
    chrome_browser,
) -> None:
    with _loopback_server() as origin:
        private_url = f"{origin}/two-step?opaque=private-fixture-token"
        _, effect, _ = _setup(
            tmp_path,
            private_url,
            _operations(UnsubscribeOperationKind.OPEN_ENTRY),
        )
        context = chrome_browser.new_context()
        browser = PlaywrightUnsubscribeBrowser(
            context.new_page(),
            timeout_ms=2_000,
            network_policy=BrowserNetworkPolicy(
                allowed_origins=frozenset({origin}),
                allow_loopback_for_tests=True,
            ),
        )
        try:
            browser.execute_operation(
                effect,
                private_url,
                effect.operations[0],
            )
            discovery = browser.discover_current_page(effect)
        finally:
            context.close()

    assert len(discovery.controls) == 1
    assert discovery.controls[0].reference.startswith("unsubscribe-control:")
    serialized = repr(discovery)
    assert "private-fixture-token" not in serialized
    assert origin not in serialized


def test_unknown_ordinary_page_fails_closed(
    tmp_path: Path,
    chrome_browser,
) -> None:
    result, _ = _run(
        tmp_path,
        chrome_browser,
        path="/unknown",
        operations=_operations(UnsubscribeOperationKind.OPEN_ENTRY),
    )

    assert result.outcome is UnsubscribeOutcome.FAILED_BROWSER


def test_final_confirmation_click_fixture(tmp_path: Path, chrome_browser) -> None:
    result, requests = _run(
        tmp_path,
        chrome_browser,
        path="/final-click",
        operations=_operations(
            UnsubscribeOperationKind.OPEN_ENTRY,
            UnsubscribeOperationKind.CLICK_CONFIRMATION,
            targets=("fixture-control-1",),
        ),
    )
    assert result.outcome is UnsubscribeOutcome.DONE
    assert requests[-1] == ("GET", "/terminal-click")


def test_already_unsubscribed_fixture(tmp_path: Path, chrome_browser) -> None:
    result, _ = _run(
        tmp_path,
        chrome_browser,
        path="/already",
        operations=_operations(UnsubscribeOperationKind.OPEN_ENTRY),
    )
    assert result.outcome is UnsubscribeOutcome.ALREADY_UNSUBSCRIBED


@pytest.mark.parametrize(
    ("path", "outcome"),
    [
        ("/login", UnsubscribeOutcome.SKIPPED_LOGIN_REQUIRED),
        ("/captcha", UnsubscribeOutcome.SKIPPED_CAPTCHA),
        ("/payment", UnsubscribeOutcome.SKIPPED_PAYMENT),
    ],
)
def test_unsupported_business_fixture(
    tmp_path: Path,
    chrome_browser,
    path: str,
    outcome: UnsubscribeOutcome,
) -> None:
    result, _ = _run(
        tmp_path,
        chrome_browser,
        path=path,
        operations=_operations(UnsubscribeOperationKind.OPEN_ENTRY),
    )
    assert result.outcome is outcome
    assert result.disposition.task_status == "skipped"
    assert result.disposition.attention_when_exhausted is False


def test_confirmation_email_fixture(tmp_path: Path, chrome_browser) -> None:
    result, requests = _run(
        tmp_path,
        chrome_browser,
        path="/confirmation-email",
        operations=_operations(
            UnsubscribeOperationKind.OPEN_ENTRY,
            UnsubscribeOperationKind.CONFIRM_EMAIL,
            targets=(
                confirmation_target_reference("fixture-confirmation-message"),
            ),
        ),
        confirmation_path="/confirmation-receipt",
    )
    assert result.outcome is UnsubscribeOutcome.DONE
    assert result.receipt is not None
    assert result.receipt.evidence.startswith("confirmation-mail:")
    assert requests[-1] == ("GET", "/confirmation-receipt")


@pytest.mark.parametrize(
    "binding",
    ("wrong_effect", "wrong_mail", "wrong_target"),
)
def test_confirmation_email_target_is_bound_to_effect_mail_and_control(
    tmp_path: Path,
    chrome_browser,
    binding: str,
) -> None:
    result, requests = _run(
        tmp_path,
        chrome_browser,
        path="/confirmation-email",
        operations=_operations(
            UnsubscribeOperationKind.OPEN_ENTRY,
            UnsubscribeOperationKind.CONFIRM_EMAIL,
            targets=(
                confirmation_target_reference("fixture-confirmation-message"),
            ),
        ),
        confirmation_path="/confirmation-receipt",
        confirmation_binding=binding,
    )

    assert result.outcome is UnsubscribeOutcome.FAILED_BROWSER
    assert all(path != "/confirmation-receipt" for _, path in requests)


def test_uncertain_claim_on_fresh_blank_page_never_navigates(
    tmp_path: Path,
    chrome_browser,
) -> None:
    result, requests = _run(
        tmp_path,
        chrome_browser,
        path="/direct",
        operations=_operations(UnsubscribeOperationKind.OPEN_ENTRY),
        recover_uncertain=True,
    )

    assert result.outcome is UnsubscribeOutcome.FAILED_BROWSER
    assert result.error_code == "email_unsubscribe_outcome_unresolved"
    assert requests == ()


@pytest.mark.parametrize(
    "path",
    (
        "/malicious-redirect",
        "/malicious-iframe",
        "/malicious-fetch",
        "/malicious-image",
    ),
)
def test_unapproved_redirect_and_subresources_are_blocked_before_request(
    tmp_path: Path,
    chrome_browser,
    path: str,
) -> None:
    with _loopback_server_pair() as (allowed_origin, _blocked_origin):
        private_url = f"{allowed_origin}{path}"
        store, effect, entry = _setup(
            tmp_path,
            private_url,
            _operations(UnsubscribeOperationKind.OPEN_ENTRY),
        )
        context = chrome_browser.new_context()
        browser = PlaywrightUnsubscribeBrowser(
            context.new_page(),
            timeout_ms=2_000,
            network_policy=BrowserNetworkPolicy(
                allowed_origins=frozenset({allowed_origin}),
                allow_loopback_for_tests=True,
            ),
        )
        try:
            result = UnsubscribeExecutor(
                store, browser, owner=_BROWSER_OWNER
            ).execute(effect, (entry,))
        finally:
            context.close()

    assert result.outcome is UnsubscribeOutcome.FAILED_BROWSER
    assert _BlockedHandler.requests == []


@pytest.mark.parametrize(
    "path",
    ("/malicious-form", "/malicious-popup", "/malicious-download"),
)
def test_unapproved_form_popup_and_download_have_zero_external_effect(
    tmp_path: Path,
    chrome_browser,
    path: str,
) -> None:
    with _loopback_server_pair() as (allowed_origin, _blocked_origin):
        private_url = f"{allowed_origin}{path}"
        operations = _operations(UnsubscribeOperationKind.OPEN_ENTRY)
        store, effect, entry = _setup(tmp_path, private_url, operations)
        context = chrome_browser.new_context(accept_downloads=False)
        browser = PlaywrightUnsubscribeBrowser(
            context.new_page(),
            timeout_ms=2_000,
            network_policy=BrowserNetworkPolicy(
                allowed_origins=frozenset({allowed_origin}),
                allow_loopback_for_tests=True,
            ),
        )
        try:
            result = UnsubscribeExecutor(
                store, browser, owner=_BROWSER_OWNER
            ).execute(effect, (entry,))
        finally:
            context.close()

    assert result.outcome is UnsubscribeOutcome.FAILED_BROWSER
    assert _BlockedHandler.requests == []


def test_verified_one_click_posts_exact_body_without_cookie(
    tmp_path: Path,
    chrome_browser,
) -> None:
    with _loopback_server() as origin:
        private_url = f"{origin}/one-click"
        store, effect, entry = _setup(
            tmp_path,
            private_url,
            _operations(UnsubscribeOperationKind.POST_ONE_CLICK),
        )
        context = chrome_browser.new_context()
        context.add_cookies(
            [
                {
                    "name": "session",
                    "value": "must-not-leak",
                    "url": origin,
                }
            ]
        )
        browser = PlaywrightUnsubscribeBrowser(
            context.new_page(),
            timeout_ms=2_000,
            network_policy=BrowserNetworkPolicy(
                allowed_origins=frozenset({origin}),
                allow_loopback_for_tests=True,
            ),
        )
        try:
            result = UnsubscribeExecutor(
                store, browser, owner=_BROWSER_OWNER
            ).execute(effect, (entry,))
        finally:
            context.close()

    assert result.outcome is UnsubscribeOutcome.DONE
    assert _FixtureHandler.requests == [("POST", "/one-click")]
    assert _FixtureHandler.request_details == [
        {
            "body": "List-Unsubscribe=One-Click",
            "cookie": "",
            "content_type": "application/x-www-form-urlencoded",
        }
    ]
