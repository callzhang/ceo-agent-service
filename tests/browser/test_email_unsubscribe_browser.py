from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
from threading import Thread
from urllib.parse import urlsplit

import pytest

from app.email_classifier_contracts import (
    EmailAction,
    EmailCategory,
    EmailClassification,
    EmailClassificationStatus,
    build_versioned_email_action_plan,
)
from app.email_store import EmailStore, email_action_identity
from app.email_unsubscribe import (
    EmailUnsubscribeEffect,
    PlaywrightUnsubscribeBrowser,
    UnsubscribeEntry,
    UnsubscribeEntrySource,
    UnsubscribeExecutor,
    UnsubscribeOperation,
    UnsubscribeOperationKind,
    UnsubscribeOutcome,
    unsubscribe_entry_reference,
)


pytestmark = pytest.mark.skipif(
    os.environ.get("WORKBENCH_BROWSER_TESTS") != "1",
    reason="set WORKBENCH_BROWSER_TESTS=1 to run local browser fixtures",
)

sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright


def _page(
    state: str,
    visible_text: str,
    *,
    next_step: str = "",
    receipt: str = "",
    evidence: str = "terminal_page",
    content: str = "",
) -> bytes:
    attributes = [
        f'data-unsubscribe-state="{state}"',
        f'data-state-reference="state-{state}"',
    ]
    if next_step:
        attributes.append(f'data-next-operation-reference="{next_step}"')
    if receipt:
        attributes.extend(
            (
                f'data-receipt-id="{receipt}"',
                f'data-evidence="{evidence}"',
            )
        )
    return (
        "<!doctype html><html><body>"
        f"<main {' '.join(attributes)}><h1>{visible_text}</h1>{content}</main>"
        "</body></html>"
    ).encode("utf-8")


class _FixtureHandler(BaseHTTPRequestHandler):
    requests: list[tuple[str, str]] = []

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
                        'data-operation-reference="control-2">'
                        '<button type="submit">Continue</button></form>'
                    ),
                )
            )
        elif path == "/final-click":
            self._send(
                _page(
                    "action_required",
                    "Confirm unsubscribe",
                    next_step="step-2",
                    content=(
                        '<a href="/terminal-click" '
                        'data-operation-reference="control-2">Confirm</a>'
                    ),
                )
            )
        elif path == "/terminal-click":
            self._send(
                _page(
                    "done",
                    "Final confirmation complete",
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
                    receipt="receipt-confirmation-mail",
                    evidence="confirmation_mail",
                )
            )
        else:
            self._send(b"not found", status=404)

    def do_POST(self) -> None:  # noqa: N802
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length:
            self.rfile.read(content_length)
        type(self).requests.append(("POST", self.path))
        path = urlsplit(self.path).path
        if path == "/two-step-second":
            self._send(
                _page(
                    "action_required",
                    "Final form confirmation",
                    next_step="step-3",
                    content=(
                        '<form method="post" action="/two-step-terminal" '
                        'data-operation-reference="control-3">'
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
        else:
            self._send(b"not found", status=404)


class _LoopbackHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False


@contextmanager
def _loopback_server():
    _FixtureHandler.requests = []
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


def _operations(*kinds: UnsubscribeOperationKind) -> tuple[UnsubscribeOperation, ...]:
    return tuple(
        UnsubscribeOperation(
            operation_reference=f"step-{index}",
            kind=kind,
            target_reference="entry" if index == 1 else f"control-{index}",
        )
        for index, kind in enumerate(kinds, start=1)
    )


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
    )
    return store, effect, entry


def _run(
    tmp_path: Path,
    chrome_browser,
    *,
    path: str,
    operations: tuple[UnsubscribeOperation, ...],
    confirmation_path: str = "",
):
    with _loopback_server() as origin:
        private_url = f"{origin}{path}?opaque=private-fixture-token"
        store, effect, entry = _setup(tmp_path, private_url, operations)
        context = chrome_browser.new_context()
        page = context.new_page()
        blocked: list[str] = []

        def guard(route, request) -> None:
            parsed = urlsplit(request.url)
            if parsed.scheme in {"http", "https"} and parsed.hostname not in {
                "127.0.0.1",
                "localhost",
                "::1",
            }:
                blocked.append(request.url)
                route.abort()
                return
            route.continue_()

        page.route("**/*", guard)
        browser = PlaywrightUnsubscribeBrowser(
            page,
            timeout_ms=3_000,
            allowed_hosts=frozenset({"127.0.0.1"}),
            confirmation_url_resolver=(
                (lambda _effect: f"{origin}{confirmation_path}")
                if confirmation_path
                else None
            ),
        )
        try:
            result = UnsubscribeExecutor(
                store,
                browser,
                owner={
                    "owner_id": "email-worker",
                    "generation": 41,
                    "lease_token": "browser-fixture-owner",
                },
            ).execute(effect, (entry,))
        finally:
            context.close()

        assert blocked == []
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
        ),
    )
    assert result.outcome is UnsubscribeOutcome.DONE
    assert [method for method, _ in requests] == ["GET", "POST", "POST"]


def test_final_confirmation_click_fixture(tmp_path: Path, chrome_browser) -> None:
    result, requests = _run(
        tmp_path,
        chrome_browser,
        path="/final-click",
        operations=_operations(
            UnsubscribeOperationKind.OPEN_ENTRY,
            UnsubscribeOperationKind.CLICK_CONFIRMATION,
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
        ),
        confirmation_path="/confirmation-receipt",
    )
    assert result.outcome is UnsubscribeOutcome.DONE
    assert result.receipt is not None
    assert result.receipt.evidence == "confirmation_mail"
    assert requests[-1] == ("GET", "/confirmation-receipt")
