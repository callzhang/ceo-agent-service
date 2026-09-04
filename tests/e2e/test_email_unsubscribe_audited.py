from __future__ import annotations

import asyncio
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

import app.agent_cli as agent_cli
from app.agent_orchestrator import AgentOrchestrator
from app.audit_agent import AuditAgentRunner
from app.consumer_agent import ConsumerAgentRunner
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
    BrowserNetworkPolicy,
    UnsubscribeOperationKind,
    browser_network_policy_for_entries,
    browser_unsubscribe_entries,
    execute_unsubscribe_in_dedicated_profile,
    extract_unsubscribe_entries,
)
from app.email_unsubscribe_audit import EmailUnsubscribeAuditOperation
from app.email_unsubscribe_continuation import EmailUnsubscribeContinuationDriver
from app.email_worker import _finalize_email_task, run_email_agent_task_loop
from app.process_runner import ProcessRunResult
from app.store import AgentRole, AutoReplyStore


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


def _json_section(prompt: str, heading: str) -> object:
    start = prompt.index(heading) + len(heading)
    value, _end = json.JSONDecoder().raw_decode(prompt[start:].lstrip())
    return value


def _agent_message_record(payload: Mapping[str, object]) -> str:
    return json.dumps(
        {
            "type": "item.completed",
            "item": {
                "type": "agent_message",
                "text": json.dumps(dict(payload), sort_keys=True),
            },
        },
        sort_keys=True,
    )


class _AuditedTurnExecutor:
    """Script only Agent judgment; persist and execute through real boundaries."""

    def __init__(
        self,
        *,
        task_store: AutoReplyStore,
        task_id: int,
        task_payload: Mapping[str, object],
        expected_attachment_metadata: Mapping[str, object],
    ) -> None:
        self.task_store = task_store
        self.task_id = task_id
        self.task_payload = dict(task_payload)
        self.expected_attachment_metadata = dict(expected_attachment_metadata)
        self.consumer_proposals: list[dict[str, object]] = []
        self.audit_invocations: list[dict[str, object]] = []
        self.commands: list[list[str]] = []
        self.consumer_prompts: list[str] = []
        self.continuation_receipts: list[dict[str, object]] = []
        self.audit_failures: list[str] = []

    def __call__(
        self,
        command: list[str],
        *,
        prompt: str,
        on_stdout_line,
        **_kwargs: object,
    ) -> ProcessRunResult:
        self.commands.append(list(command))
        audit_turn = "Candidate revision\n" in prompt
        if audit_turn:
            try:
                output = self._audit_output(prompt)
            except Exception as exc:
                self.audit_failures.append(repr(exc))
                raise
        else:
            self.consumer_prompts.append(prompt)
            output = self._consumer_output(prompt)
        lines = [
            json.dumps(
                {
                    "type": "thread.started",
                    "thread_id": (
                        f"audited-email-audit-{len(self.audit_invocations)}"
                        if audit_turn
                        else "audited-email-consumer"
                    ),
                }
            ),
            _agent_message_record(output),
        ]
        stdout = "\n".join(lines)
        for line in lines:
            on_stdout_line(line)
        return ProcessRunResult(0, stdout, "")

    def _consumer_output(self, prompt: str) -> dict[str, object]:
        trigger = _json_section(prompt, "Original trigger\n")
        assert isinstance(trigger, dict)
        task_payload = trigger["raw_payload"]
        assert task_payload == self.task_payload
        assert isinstance(task_payload, dict)
        materials = _json_section(
            prompt,
            "Raw material references and exact read commands\n",
        )
        assert materials == [
            {
                "kind": "attachment_metadata",
                "reference": json.dumps(
                    self.expected_attachment_metadata,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                "source_message_id": MESSAGE_IDENTITY,
                "read_commands": [],
            }
        ]
        if not self.consumer_proposals:
            assert "Safe prior execution receipts\n" not in prompt
            operations = [
                {
                    "operation_reference": OPEN_OPERATION_REFERENCE,
                    "kind": UnsubscribeOperationKind.OPEN_ENTRY.value,
                    "target_reference": task_payload["unsubscribe_entries"][0][
                        "reference"
                    ],
                }
            ]
        else:
            receipts = _json_section(prompt, "Safe prior execution receipts\n")
            assert isinstance(receipts, list)
            [receipt] = [
                item
                for item in receipts
                if isinstance(item, dict)
                and item.get("operation") == "unsubscribe_continuation"
            ]
            assert receipt["completed"] is False
            assert str(receipt["receipt_id"]).startswith(
                "email-unsubscribe-continuation:"
            )
            continuation = json.loads(str(receipt["summary"]))
            self.continuation_receipts.append(continuation)
            accepted_prefix = list(continuation["accepted_operations"])
            [control] = continuation["controls"]
            assert continuation["instruction"] == (
                "Audit accepted the durable prefix; propose exactly one next "
                "operation from the listed opaque controls."
            )
            operations = accepted_prefix + [
                {
                    "operation_reference": CONFIRM_OPERATION_REFERENCE,
                    "kind": UnsubscribeOperationKind.SUBMIT_FORM.value,
                    "target_reference": control["reference"],
                }
            ]
        action = {
            "description": "Execute exactly one audited unsubscribe operation",
            "capability": "email_browser",
            "operation": "unsubscribe",
            "target": {
                "action_identity": task_payload["action_identity"],
                "account_id": task_payload["account_id"],
                "stable_message_identity": task_payload["stable_message_identity"],
                "thread_identity": task_payload["thread_identity"],
                "entry_reference": task_payload["unsubscribe_entries"][0][
                    "reference"
                ],
                "network_policy_reference": task_payload[
                    "unsubscribe_network_policy_reference"
                ],
                "network_policy_origin_references": task_payload[
                    "unsubscribe_network_policy_origin_references"
                ],
            },
            "payload": {"operations": operations},
            "expected_verification": "Read terminal provider evidence.",
        }
        self.consumer_proposals.append(action)
        return {
            "outcome": "proposal",
            "summary": "Prepared one bounded unsubscribe operation.",
            "proposal": {
                "objective": "Stop this confirmed newsletter subscription.",
                "actions": [action],
                "sourced_facts": [
                    {
                        "assertion": "The immutable ActionPlan authorizes unsubscribe.",
                        "references": [str(self.task_payload["action_plan_id"])],
                    }
                ],
                "authored_judgment": "One reversible provider step is justified.",
            },
            "decision_options": [],
            "risk": "low",
            "confidence": 1.0,
            "error_code": "",
            "error_retryable": False,
            "error_authorization_required": False,
        }

    def _audit_output(self, prompt: str) -> dict[str, object]:
        candidate = _json_section(prompt, "Candidate revision\n")
        assert isinstance(candidate, dict)
        proposal = candidate["proposal"]
        assert isinstance(proposal, dict)
        [accepted_action] = proposal["actions"]
        operation_id = str(candidate["operation_id"])
        running = [
            run
            for run in self.task_store.list_agent_runs_for_task_generation(
                self.task_id,
                self.task_store.get_reply_task(self.task_id).execution_generation,
            )
            if run.role is AgentRole.AUDIT and run.status == "running"
        ]
        audit_run = max(running, key=lambda run: run.id)
        assert f"audit_agent_run_id={audit_run.id}" in prompt
        task = self.task_store.get_reply_task(self.task_id)
        assert task is not None
        capability_arguments = json.loads(
            json.dumps(
                {
                    "task_id": task.id,
                    "execution_generation": task.execution_generation,
                    "audit_agent_run_id": audit_run.id,
                    "accepted_action": accepted_action,
                },
                sort_keys=True,
            )
        )
        mcp_result = asyncio.run(
            agent_cli.server.call_tool(
                "execute_audited_email_unsubscribe",
                capability_arguments,
            )
        )
        assert isinstance(mcp_result, tuple) and len(mcp_result) == 2
        mcp_content, result = mcp_result
        assert mcp_content
        assert isinstance(result, dict)
        self.audit_invocations.append(
            {
                "run_id": audit_run.id,
                "capability": "execute_audited_email_unsubscribe",
                "arguments": capability_arguments,
                "mcp_content": mcp_content,
                "accepted_action": accepted_action,
                "result": result,
            }
        )
        if result["status"] not in {"awaiting_audit", "done"}:
            raise AssertionError(json.dumps(result, sort_keys=True))
        return {
            "outcome": "executed",
            "summary": str(result["summary"]),
            "proposal_revision": int(candidate["proposal_revision"]),
            "feedback": None,
            "external_result": {
                "operation_id": operation_id,
                "verification_summary": str(result["summary"]),
                "live_result_reference": {
                    "audit_run_id": audit_run.id,
                    "receipt_id": str(result.get("receipt_id") or ""),
                    "status": str(result["status"]),
                },
            },
            "decision_options": [],
            "risk": "low",
            "confidence": 1.0,
            "error_code": "",
            "error_retryable": False,
            "error_authorization_required": False,
        }


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
        category=EmailCategory.SUBSCRIPTION,
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
                "category": EmailCategory.SUBSCRIPTION,
                "confidence": 1.0,
                "margin": 1.0,
                "probabilities": {"subscription": 1.0},
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
    entries = extract_unsubscribe_entries(
        list_unsubscribe=f"<{private_url}>",
        allow_loopback_for_tests=True,
    )
    policy = browser_network_policy_for_entries(
        entries,
        allow_loopback_for_tests=True,
    )
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
        unsubscribe_network_policy_reference=policy.reference,
        unsubscribe_network_policy_origin_references=policy.origin_references,
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


def test_two_page_unsubscribe_runs_two_consumer_audit_rounds_and_finishes(
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
            channel="chrome",
            **kwargs,
        )

    monkeypatch.setattr(
        "app.email_browser_profile.launch_persistent_email_context",
        launch_for_fixture,
    )
    monkeypatch.setattr(
        "app.email_unsubscribe_audit.browser_unsubscribe_entries",
        lambda entries: browser_unsubscribe_entries(
            entries,
            allow_loopback_for_tests=True,
        ),
    )
    monkeypatch.setattr(
        "app.email_unsubscribe_audit.browser_network_policy_for_entries",
        lambda entries: browser_network_policy_for_entries(
            entries,
            allow_loopback_for_tests=True,
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
        original_validate_url = BrowserNetworkPolicy.validate_url

        def guarded_validate_url(
            browser_policy: BrowserNetworkPolicy,
            value: str,
        ) -> str:
            parsed = urlsplit(value)
            destination = (
                parsed.hostname or "",
                parsed.port or (443 if parsed.scheme == "https" else 80),
            )
            if destination != allowed_browser_destination:
                blocked_browser_requests.append(value)
                raise AssertionError(
                    f"browser left exact fixture destination: {value}"
                )
            browser_requests.append(destination)
            return original_validate_url(browser_policy, value)

        monkeypatch.setattr(
            BrowserNetworkPolicy,
            "validate_url",
            guarded_validate_url,
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
        policy = browser_network_policy_for_entries(
            entries,
            allow_loopback_for_tests=True,
        )

        def execute_effect(
            effect,
            resolved_entries,
            *,
            owner,
            executed_prefix_length,
        ):
            return execute_unsubscribe_in_dedicated_profile(
                effect,
                tuple(resolved_entries),
                store=email_store,
                profile=dedicated_profile,
                network_policy=policy,
                owner=owner,
                executed_prefix_length=executed_prefix_length,
                timeout_ms=3_000,
            )

        audit_operation = EmailUnsubscribeAuditOperation(
            task_store=task_store,
            email_store=email_store,
            resolve_entries=lambda *_args, **_kwargs: entries,
            execute_effect=execute_effect,
            owner_id="audited-email-e2e",
        )
        operation_builds: list[Path] = []

        def build_fixture_operation(settings):
            received_path = Path(settings.db_path)
            operation_builds.append(received_path)
            assert received_path.resolve() == database.resolve()
            return audit_operation

        monkeypatch.setattr("app.config.worker_db_path", lambda: database)
        monkeypatch.setattr(
            "app.email_worker.build_audited_email_unsubscribe_operation",
            build_fixture_operation,
        )
        executor = _AuditedTurnExecutor(
            task_store=task_store,
            task_id=route.task.id,
            task_payload=payload,
            expected_attachment_metadata=attachment_metadata.model_dump(mode="json"),
        )
        orchestrator = AgentOrchestrator(
            store=task_store,
            consumer=ConsumerAgentRunner(
                store=task_store,
                workspace=tmp_path,
                executor=executor,
                owner="audited-email-e2e-consumer",
                codex_session_exists=lambda _session_id: True,
            ),
            audit=AuditAgentRunner(
                store=task_store,
                workspace=tmp_path,
                executor=executor,
                owner="audited-email-e2e-audit",
            ),
            domain_continuation=EmailUnsubscribeContinuationDriver(email_store),
        )

        def load_context(_task):
            [current] = adapter.ensure_action_plan_tasks(plan, task_input)
            return current.context

        run_email_agent_task_loop(
            task_store,
            orchestrator,
            load_task_context=load_context,
            finalize_task=lambda task, result: _finalize_email_task(
                task_store,
                task,
                result,
            ),
            sleep=lambda _seconds: None,
            max_cycles=1,
        )

    task = task_store.get_reply_task(route.task.id)
    assert task is not None
    assert task.channel == "email"
    assert json.loads(task.trigger_message_json)["lifecycle_version"] == (
        "email_unsubscribe_audited_v2"
    )
    runs = task_store.list_agent_runs_for_task_generation(
        task.id,
        task.execution_generation,
    )
    assert task.status == "done", (
        task.error,
        [
            (
                run.role.value,
                run.status,
                run.structured_error_json,
                run.final_result_json,
            )
            for run in runs
        ],
        executor.audit_invocations,
        executor.audit_failures,
        forbidden_io_calls,
        allowed_socket_calls,
        blocked_socket_calls,
        browser_requests,
        blocked_browser_requests,
        browser_launches,
        _UnsubscribeHandler.requests,
    )
    assert [run.role for run in runs].count(AgentRole.CONSUMER) == 2
    assert [run.role for run in runs].count(AgentRole.AUDIT) == 2
    assert all(run.status == "completed" for run in runs)
    assert len(executor.audit_invocations) == 2
    assert [
        (run.role.value, run.proposal_revision) for run in runs
    ] == [
        ("consumer", 0),
        ("audit", 0),
        ("consumer", 1),
        ("audit", 1),
    ]
    first_consumer, first_audit, second_consumer, second_audit = runs
    assert first_consumer.parent_agent_run_id is None
    assert first_audit.parent_agent_run_id == first_consumer.id
    assert second_consumer.parent_agent_run_id == first_audit.id
    assert second_audit.parent_agent_run_id == second_consumer.id
    assert all(run.reply_task_id == task.id for run in runs)
    assert all(run.execution_generation == task.execution_generation for run in runs)
    assert [invocation["run_id"] for invocation in executor.audit_invocations] == [
        first_audit.id,
        second_audit.id,
    ]
    assert [invocation["capability"] for invocation in executor.audit_invocations] == [
        "execute_audited_email_unsubscribe",
        "execute_audited_email_unsubscribe",
    ]
    assert operation_builds == [database, database]

    first_operations = executor.consumer_proposals[0]["payload"]["operations"]
    second_operations = executor.consumer_proposals[1]["payload"]["operations"]
    assert [item["kind"] for item in first_operations] == ["open_entry"]
    assert [item["kind"] for item in second_operations] == [
        "open_entry",
        "submit_form",
    ]
    assert second_operations[:-1] == first_operations
    assert len(executor.continuation_receipts) == 1
    assert [
        len(invocation["accepted_action"]["payload"]["operations"])
        for invocation in executor.audit_invocations
    ] == [1, 2]
    for index, invocation in enumerate(executor.audit_invocations):
        arguments = invocation["arguments"]
        assert arguments == {
            "task_id": task.id,
            "execution_generation": task.execution_generation,
            "audit_agent_run_id": (first_audit.id, second_audit.id)[index],
            "accepted_action": executor.consumer_proposals[index],
        }
        assert invocation["accepted_action"] == executor.consumer_proposals[index]

    steps = email_store.list_email_unsubscribe_steps(str(payload["action_identity"]))
    assert [step["operation"] for step in steps] == ["open_entry", "submit_form"]
    receipt = email_store.get_email_unsubscribe_receipt(str(payload["action_identity"]))
    assert receipt is not None
    assert receipt["result_text"] == "You have been unsubscribed"
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
    assert payload["unsubscribe_entries"][0]["reference"] == expected_binding[
        "entry_reference"
    ]
    expected_target_binding = {
        key: expected_binding[key]
        for key in (
            "action_identity",
            "account_id",
            "stable_message_identity",
            "thread_identity",
            "entry_reference",
        )
    } | {
        "network_policy_reference": payload[
            "unsubscribe_network_policy_reference"
        ],
        "network_policy_origin_references": payload[
            "unsubscribe_network_policy_origin_references"
        ],
    }
    assert all(
        proposal["target"] == expected_target_binding
        for proposal in executor.consumer_proposals
    )
    claim = email_store.get_email_unsubscribe_claim(str(payload["action_identity"]))
    assert claim is not None
    assert all(claim[key] == value for key, value in expected_binding.items())
    assert claim["audit_agent_run_id"] == second_audit.id
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
    assert len(effect_rows) == 2
    first_effect, second_effect = effect_rows
    assert [row["action_identity"] for row in effect_rows] == [
        expected_binding["action_identity"],
        expected_binding["action_identity"],
    ]
    assert [row["audit_agent_run_id"] for row in effect_rows] == [
        first_audit.id,
        second_audit.id,
    ]
    assert json.loads(first_effect["operations_json"]) == first_operations
    assert json.loads(second_effect["operations_json"]) == second_operations
    assert first_effect["previous_effect_digest"] == ""
    assert second_effect["previous_effect_digest"] == first_effect["effect_digest"]
    assert receipt["effect_digest"] == second_effect["effect_digest"]
    assert claim["effect_digest"] == second_effect["effect_digest"]
    assert executor.continuation_receipts[0]["previous_effect_digest"] == (
        first_effect["effect_digest"]
    )
    assert [step["action_identity"] for step in step_rows] == [
        expected_binding["action_identity"],
        expected_binding["action_identity"],
    ]
    assert [step["effect_digest"] for step in step_rows] == [
        first_effect["effect_digest"],
        second_effect["effect_digest"],
    ]
    assert all(
        row["network_policy_reference"]
        == payload["unsubscribe_network_policy_reference"]
        for row in effect_rows
    )
    assert all(
        json.loads(row["network_policy_origins_json"])
        == payload["unsubscribe_network_policy_origin_references"]
        for row in effect_rows
    )
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
    assert len(browser_launches) == 2
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
    serialized_prompts = "\n".join(executor.consumer_prompts)
    assert attachment_metadata.filename in serialized_prompts
    assert attachment_metadata.mime_type in serialized_prompts
    assert str(attachment_metadata.size_bytes) in serialized_prompts
    assert str(sentinel_path.resolve()) not in serialized_prompts
    assert sentinel_content.decode("utf-8") not in serialized_prompts
    assert all(
        "execute_audited_email_unsubscribe" not in json.dumps(command)
        for command in executor.commands[0::2]
    )
    assert all(
        "execute_audited_email_unsubscribe" in json.dumps(command)
        for command in executor.commands[1::2]
    )
