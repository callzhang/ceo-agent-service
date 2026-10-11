from __future__ import annotations

import builtins
from collections.abc import Mapping
from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import imaplib
import json
import os
from pathlib import Path
import smtplib
import socket
import sqlite3
from threading import Thread
from urllib.parse import urlsplit

import pytest

from app.email_worker import run_email_unsubscribe
from app.email_browser_profile import (
    EmailBrowserProfile,
    launch_persistent_email_context,
)
from app.email_classifier_contracts import (
    EmailAction,
    EmailAttachmentMetadata,
    EmailCategory,
    EmailClassification,
    EmailClassificationStatus,
    build_versioned_email_action_plan,
)
from app.email_store import EmailStore
from app.email_task_adapter import (
    EmailAgentTaskAdapter,
    EmailAgentTaskInput,
    EmailThreadMessage,
)
from app.email_unsubscribe import (
    PlaywrightUnsubscribeBrowser,
    extract_unsubscribe_entries,
    browser_unsubscribe_entries,
)
from app.email_unsubscribe_direct import (
    DirectEmailUnsubscribeOperation,
    run_unsubscribe_in_dedicated_profile,
)
from app.store import AutoReplyStore


ACCOUNT_ID = "account-a"
CLASSIFICATION_ID = 801
MESSAGE_IDENTITY = "account-a:message-id:<newsletter-801@example.com>"
THREAD_IDENTITY = "newsletter-thread-801"
OPEN_OPERATION_REFERENCE = (
    "unsubscribe-operation:" + sha256(b"audited-e2e-open").hexdigest()
)
CONFIRM_OPERATION_REFERENCE = (
    "unsubscribe-operation:" + sha256(b"audited-e2e-confirm").hexdigest()
)


class _LoopbackServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False


class _UnsubscribeHandler(BaseHTTPRequestHandler):
    requests: list[dict[str, str]] = []

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _write(
        self,
        body: str,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        encoded = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:  # noqa: N802
        # A browser fetches /favicon.ico on its own; it is the browser's
        # behaviour, not the service's, and recording it makes this assertion
        # depend on the Chromium build rather than on what the unsubscribe did.
        if self.path != "/favicon.ico":
            type(self).requests.append(
                {
                    "method": "GET",
                    "path": self.path,
                    "body": "",
                    "cookie": self.headers.get("Cookie", ""),
                }
            )
        self._write(
            """
            <!doctype html>
            <html><body><main>
              <h1>Manage subscription</h1>
              <form method="post" action="/confirm">
                <input type="hidden" name="segment" value="basic">
                <input name="scope" value="newsletter">
                <button type="submit" name="decision" value="unsubscribe">
                  Unsubscribe
                </button>
              </form>
            </main></body></html>
            """,
            headers={
                "Set-Cookie": ("audit_session=persistent-profile; Path=/; SameSite=Lax")
            },
        )

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8")
        type(self).requests.append(
            {
                "method": "POST",
                "path": self.path,
                "body": body,
                "cookie": self.headers.get("Cookie", ""),
            }
        )
        self._write(
            "<!doctype html><html><body><main>"
            "You have been unsubscribed"
            "</main></body></html>"
        )


@contextmanager
def _loopback_unsubscribe_server():
    _UnsubscribeHandler.requests = []
    server = _LoopbackServer(("127.0.0.1", 0), _UnsubscribeHandler)
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


def _persist_confirmed_subscription(
    email_store: EmailStore,
    *,
    origin: str,
    attachment: EmailAttachmentMetadata,
) -> tuple[object, EmailAgentTaskInput]:
    email_store.create_account(
        {
            "account_id": ACCOUNT_ID,
            "display_name": "Loopback account",
            "email_address": "derek@example.com",
            "imap_host": "imap.invalid",
            "imap_port": 993,
            "imap_tls": True,
            "imap_username": "derek@example.com",
            "imap_secret_reference": "keychain://unused-imap",
            "smtp_host": "smtp.invalid",
            "smtp_port": 465,
            "smtp_tls": True,
            "smtp_username": "derek@example.com",
            "smtp_secret_reference": "keychain://unused-smtp",
            "enabled": True,
            "scan_folders": ["INBOX"],
            "scan_interval_seconds": 60,
        }
    )
    plan = build_versioned_email_action_plan(
        action_plan_version=1,
        classification_id=CLASSIFICATION_ID,
        account_id=ACCOUNT_ID,
        category=EmailCategory.JUNK,
        classification_source="user",
        confidence=1.0,
        model_id="email-model:audited-e2e:v1",
        config_version="email-config:audited-e2e:v1",
        actions=(EmailAction.UNSUBSCRIBE,),
        action_parameters={},
        created_at=datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc),
    )
    email_store.upsert_classification(
        EmailClassification.model_validate(
            {
                "classification_id": CLASSIFICATION_ID,
                "stable_message_identity": MESSAGE_IDENTITY,
                "provider_locator": {
                    "account_id": ACCOUNT_ID,
                    "folder": "INBOX",
                    "uidvalidity": 81,
                    "uid": 801,
                    "rfc_message_id": "<newsletter-801@example.com>",
                    "thread_id": THREAD_IDENTITY,
                },
                "category": EmailCategory.JUNK,
                "confidence": 1.0,
                "margin": 1.0,
                "probabilities": {"junk": 1.0},
                "model_id": plan.model_id,
                "config_version": plan.config_version,
                "status": EmailClassificationStatus.PROCESSED,
                "classification_source": "user",
                "action_plan": plan,
            }
        ),
        sender="newsletter@example.com",
        subject="Weekly newsletter",
        model_text="__subject__weekly newsletter",
        received_at="2026-09-03T08:00:00+00:00",
    )
    private_url = f"{origin}/manage?token=loopback-private"
    task_input = EmailAgentTaskInput(
        stable_message_identity=MESSAGE_IDENTITY,
        thread_identity=THREAD_IDENTITY,
        subject="Weekly newsletter",
        trigger=EmailThreadMessage(
            message_id=MESSAGE_IDENTITY,
            sender="newsletter@example.com",
            text="Manage subscription",
            create_time="2026-09-03T08:00:00+00:00",
        ),
        attachments=(attachment,),
        list_unsubscribe=f"<{private_url}>",
        body_text="Manage subscription",
        unsubscribe_allow_loopback_for_tests=True,
    )
    return plan, task_input


def _forbidden_entry_point(
    calls: list[str],
    name: str,
):
    def blocked(*_args: object, **_kwargs: object):
        calls.append(name)
        raise AssertionError(f"forbidden E2E entry point invoked: {name}")

    return blocked


def _install_external_io_fences(
    monkeypatch: pytest.MonkeyPatch,
    *,
    sentinel_path: Path,
    allowed_socket_destination: tuple[str, int],
) -> tuple[list[str], list[tuple[str, int]], list[tuple[str, int]]]:
    forbidden_calls: list[str] = []
    allowed_socket_calls: list[tuple[str, int]] = []
    blocked_socket_calls: list[tuple[str, int]] = []

    monkeypatch.setattr(
        "app.email_imap_readonly.ImapReadonlyAdapter.connect",
        _forbidden_entry_point(forbidden_calls, "ImapReadonlyAdapter.connect"),
    )
    monkeypatch.setattr(
        imaplib,
        "IMAP4",
        _forbidden_entry_point(forbidden_calls, "imaplib.IMAP4"),
    )
    monkeypatch.setattr(
        imaplib,
        "IMAP4_SSL",
        _forbidden_entry_point(forbidden_calls, "imaplib.IMAP4_SSL"),
    )
    monkeypatch.setattr(
        smtplib,
        "SMTP",
        _forbidden_entry_point(forbidden_calls, "smtplib.SMTP"),
    )
    monkeypatch.setattr(
        smtplib,
        "SMTP_SSL",
        _forbidden_entry_point(forbidden_calls, "smtplib.SMTP_SSL"),
    )

    resolved_sentinel = sentinel_path.resolve()

    def is_sentinel(value: object) -> bool:
        if isinstance(value, int):
            return False
        try:
            return Path(value).resolve() == resolved_sentinel
        except (OSError, TypeError, ValueError):
            return False

    original_open = builtins.open
    original_os_open = os.open
    original_path_open = Path.open
    original_read_bytes = Path.read_bytes
    original_read_text = Path.read_text

    def guarded_open(file, *args, **kwargs):
        if is_sentinel(file):
            return _forbidden_entry_point(
                forbidden_calls,
                "attachment builtins.open",
            )()
        return original_open(file, *args, **kwargs)

    def guarded_os_open(path, *args, **kwargs):
        if is_sentinel(path):
            return _forbidden_entry_point(
                forbidden_calls,
                "attachment os.open",
            )()
        return original_os_open(path, *args, **kwargs)

    def guarded_path_open(path: Path, *args, **kwargs):
        if path.resolve() == resolved_sentinel:
            return _forbidden_entry_point(
                forbidden_calls,
                "attachment Path.open",
            )()
        return original_path_open(path, *args, **kwargs)

    def guarded_read_bytes(path: Path) -> bytes:
        if path.resolve() == resolved_sentinel:
            return _forbidden_entry_point(
                forbidden_calls,
                "attachment Path.read_bytes",
            )()
        return original_read_bytes(path)

    def guarded_read_text(path: Path, *args, **kwargs) -> str:
        if path.resolve() == resolved_sentinel:
            return _forbidden_entry_point(
                forbidden_calls,
                "attachment Path.read_text",
            )()
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guarded_open)
    monkeypatch.setattr(os, "open", guarded_os_open)
    monkeypatch.setattr(Path, "open", guarded_path_open)
    monkeypatch.setattr(Path, "read_bytes", guarded_read_bytes)
    monkeypatch.setattr(Path, "read_text", guarded_read_text)

    original_connect = socket.socket.connect
    original_create_connection = socket.create_connection

    def validate_destination(address: object) -> tuple[str, int] | None:
        if not isinstance(address, tuple) or len(address) < 2:
            return None
        destination = (str(address[0]), int(address[1]))
        if destination != allowed_socket_destination:
            blocked_socket_calls.append(destination)
            raise AssertionError(
                f"non-fixture network egress attempted: {destination!r}"
            )
        return destination

    def guarded_connect(sock: socket.socket, address: object):
        destination = validate_destination(address)
        if destination is not None:
            allowed_socket_calls.append(destination)
        return original_connect(sock, address)

    def guarded_create_connection(address, *args, **kwargs):
        validate_destination(address)
        return original_create_connection(address, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket, "create_connection", guarded_create_connection)
    return forbidden_calls, allowed_socket_calls, blocked_socket_calls


def test_direct_email_worker_unsubscribes_two_page_flow_and_preserves_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "audited-email-e2e.sqlite3"
    sentinel_content = b"attachment-content-must-never-cross-email-agent-boundary"
    sentinel_path = tmp_path / "attachment-content-never-read.bin"
    sentinel_path.write_bytes(sentinel_content)
    attachment_metadata = EmailAttachmentMetadata(
        filename=sentinel_path.name,
        mime_type="application/octet-stream",
        size_bytes=len(sentinel_content),
        inline=False,
    )
    email_store = EmailStore(database)
    task_store = AutoReplyStore(database)
    browser_launches: list[dict[str, object]] = []
    browser_requests: list[tuple[str, int]] = []
    blocked_browser_requests: list[str] = []
    allowed_browser_destination: tuple[str, int] | None = None
    dedicated_profile = EmailBrowserProfile(tmp_path / "email-browser-runtime")
    main_chrome_profile = (
        Path.home() / "Library/Application Support/Google/Chrome"
    ).resolve()

    def launch_for_fixture(playwright, profile, *, headless=True, **kwargs):
        assert allowed_browser_destination is not None
        browser_launches.append(
            {
                "headless": headless,
                "profile_path": profile.profile_dir,
            }
        )
        return launch_persistent_email_context(
            playwright,
            profile,
            headless=headless,
            **kwargs,
        )

    monkeypatch.setattr(
        "app.email_browser_profile.launch_persistent_email_context",
        launch_for_fixture,
    )
    monkeypatch.setattr(
        "app.email_unsubscribe.browser_unsubscribe_entries",
        lambda entries, **kwargs: browser_unsubscribe_entries(
            entries,
            allow_loopback_for_tests=True,
            **{key: value for key, value in kwargs.items() if key != "allow_loopback_for_tests"},
        ),
    )

    with _loopback_unsubscribe_server() as origin:
        parsed_origin = urlsplit(origin)
        allowed_browser_destination = (
            parsed_origin.hostname or "",
            parsed_origin.port or 80,
        )
        (
            forbidden_io_calls,
            allowed_socket_calls,
            blocked_socket_calls,
        ) = _install_external_io_fences(
            monkeypatch,
            sentinel_path=sentinel_path,
            allowed_socket_destination=allowed_browser_destination,
        )
        original_validate_target = PlaywrightUnsubscribeBrowser._validate_navigation_target

        def guarded_validate_target(
            browser: PlaywrightUnsubscribeBrowser,
            value: str,
        ) -> str:
            parsed = urlsplit(value)
            destination = (
                parsed.hostname or "",
                parsed.port or (443 if parsed.scheme == "https" else 80),
            )
            if destination != allowed_browser_destination:
                blocked_browser_requests.append(value)
                raise AssertionError(f"browser left exact fixture destination: {value}")
            browser_requests.append(destination)
            return original_validate_target(browser, value)

        monkeypatch.setattr(
            PlaywrightUnsubscribeBrowser,
            "_validate_navigation_target",
            guarded_validate_target,
        )
        plan, task_input = _persist_confirmed_subscription(
            email_store,
            origin=origin,
            attachment=attachment_metadata,
        )
        adapter = EmailAgentTaskAdapter(task_store, email_store)
        [route] = adapter.ensure_action_plan_tasks(plan, task_input)
        payload = json.loads(route.task.trigger_message_json)
        entries = extract_unsubscribe_entries(
            list_unsubscribe=task_input.list_unsubscribe,
            allow_loopback_for_tests=True,
        )

        def run_effect(effect, entry, *, one_click_verified, executed=None):
            return run_unsubscribe_in_dedicated_profile(
                effect,
                entry,
                profile=dedicated_profile,
                one_click_verified=one_click_verified,
                timeout_ms=3_000,
                executed=executed,
            )

        unsubscribe_operation = DirectEmailUnsubscribeOperation(
            task_store=task_store,
            email_store=email_store,
            resolve_entries=lambda *_args, **_kwargs: entries,
            run_effect=run_effect,
        )
        operation_builds: list[Path] = []

        def build_fixture_operation(settings):
            received_path = Path(settings.db_path)
            operation_builds.append(received_path)
            assert received_path.resolve() == database.resolve()
            return unsubscribe_operation

        monkeypatch.setattr("app.config.worker_db_path", lambda: database)
        monkeypatch.setattr(
            "app.email_worker.build_direct_email_unsubscribe_operation",
            build_fixture_operation,
        )
        result = run_email_unsubscribe(database, route.task.id)
        assert result["status"] == "done", result

    task = task_store.get_reply_task(route.task.id)
    assert task is not None
    assert task.channel == "email"
    assert json.loads(task.trigger_message_json)["lifecycle_version"] == (
        "email_unsubscribe_audited_v2"
    )
    assert operation_builds == [database]

    steps = email_store.list_email_unsubscribe_steps(str(payload["action_identity"]))
    assert [step["operation"] for step in steps] == ["open_entry", "submit_form"]
    receipt = email_store.get_email_unsubscribe_receipt(str(payload["action_identity"]))
    assert receipt is not None
    assert receipt["result_text"] == "You have been unsubscribed"
    # A real headless browser navigated this exact private URL through the
    # The service email worker's actual lifecycle (DirectEmailUnsubscribeOperation,
    # not the legacy audited executor) - this is the coverage that would have
    # caught entry_url being wired into the wrong `_persist`.
    assert receipt["entry_url"] == f"{origin}/manage?token=loopback-private"
    assert sha256(receipt["entry_url"].encode("utf-8")).hexdigest() == (
        receipt["entry_reference"].removeprefix("unsubscribe-entry:")
    )
    expected_binding = {
        "action_identity": payload["action_identity"],
        "action_plan_id": plan.action_plan_id,
        "action_plan_version": plan.action_plan_version,
        "classification_id": plan.classification_id,
        "account_id": ACCOUNT_ID,
        "stable_message_identity": MESSAGE_IDENTITY,
        "thread_identity": THREAD_IDENTITY,
        "entry_reference": payload["unsubscribe_entries"][0]["reference"],
    }
    assert payload["action_type"] == EmailAction.UNSUBSCRIBE.value
    assert all(
        payload[key] == value
        for key, value in expected_binding.items()
        if key != "entry_reference"
    )
    assert (
        payload["unsubscribe_entries"][0]["reference"]
        == expected_binding["entry_reference"]
    )
    claim = email_store.get_email_unsubscribe_claim(str(payload["action_identity"]))
    assert claim is not None
    assert all(claim[key] == value for key, value in expected_binding.items())
    assert all(receipt[key] == value for key, value in expected_binding.items())

    with sqlite3.connect(database) as db:
        db.row_factory = sqlite3.Row
        action_plan_row = db.execute(
            "select * from email_action_plans where action_plan_id=?",
            (plan.action_plan_id,),
        ).fetchone()
        account_row = db.execute(
            "select * from email_accounts where account_id=?",
            (ACCOUNT_ID,),
        ).fetchone()
        message_row = db.execute(
            "select * from email_messages where stable_message_identity=?",
            (MESSAGE_IDENTITY,),
        ).fetchone()
        effect_rows = db.execute(
            "select * from email_unsubscribe_effects "
            "where action_identity=? order by rowid",
            (payload["action_identity"],),
        ).fetchall()
        step_rows = db.execute(
            "select * from email_unsubscribe_steps "
            "where action_identity=? order by sequence",
            (payload["action_identity"],),
        ).fetchall()
    assert action_plan_row is not None
    assert account_row is not None
    assert message_row is not None
    assert json.loads(action_plan_row["actions_json"]) == [
        EmailAction.UNSUBSCRIBE.value
    ]
    assert action_plan_row["action_plan_version"] == plan.action_plan_version
    assert action_plan_row["classification_id"] == plan.classification_id
    assert action_plan_row["account_id"] == ACCOUNT_ID
    assert account_row["account_id"] == ACCOUNT_ID
    assert message_row["account_id"] == ACCOUNT_ID
    assert message_row["stable_message_identity"] == MESSAGE_IDENTITY
    assert message_row["thread_identity"] == THREAD_IDENTITY
    # One call, one effect: no digest chain to keep append-only.
    assert len(effect_rows) == 1
    [only_effect] = effect_rows
    assert only_effect["action_identity"] == expected_binding["action_identity"]
    assert only_effect["previous_effect_digest"] == ""
    assert receipt["effect_digest"] == only_effect["effect_digest"]
    assert claim["effect_digest"] == only_effect["effect_digest"]
    # Both pages are in the durable journal even though one call wrote them.
    assert [step["action_identity"] for step in step_rows] == [
        expected_binding["action_identity"],
        expected_binding["action_identity"],
    ]
    assert [step["effect_digest"] for step in step_rows] == [
        only_effect["effect_digest"],
        only_effect["effect_digest"],
    ]
    assert _UnsubscribeHandler.requests == [
        {
            "method": "GET",
            "path": "/manage?token=loopback-private",
            "body": "",
            "cookie": "",
        },
        {
            "method": "POST",
            "path": "/confirm",
            "body": "segment=basic&scope=newsletter&decision=unsubscribe",
            "cookie": "audit_session=persistent-profile",
        },
    ]
    assert len(browser_launches) == 1
    assert all(launch["headless"] is True for launch in browser_launches)
    assert all(
        launch["profile_path"] == dedicated_profile.profile_dir
        for launch in browser_launches
    )
    assert dedicated_profile.profile_dir != main_chrome_profile
    assert forbidden_io_calls == []
    assert blocked_socket_calls == []
    assert set(allowed_socket_calls) <= {allowed_browser_destination}
    assert blocked_browser_requests == []
    assert browser_requests
    assert set(browser_requests) == {allowed_browser_destination}
