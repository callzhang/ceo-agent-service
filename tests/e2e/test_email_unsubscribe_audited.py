from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from threading import Thread

import pytest

from app.agent_orchestrator import AgentOrchestrator
from app.audit_agent import AuditAgentRunner
from app.consumer_agent import ConsumerAgentRunner
from app.email_browser_profile import (
    EmailBrowserProfile,
    launch_persistent_email_context,
)
from app.email_classifier_contracts import (
    EmailAction,
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
        email_store: EmailStore,
        task_id: int,
        task_payload: Mapping[str, object],
        audit_operation: EmailUnsubscribeAuditOperation,
    ) -> None:
        self.task_store = task_store
        self.email_store = email_store
        self.task_id = task_id
        self.task_payload = dict(task_payload)
        self.audit_operation = audit_operation
        self.consumer_proposals: list[dict[str, object]] = []
        self.audit_invocations: list[dict[str, object]] = []
        self.commands: list[list[str]] = []
        self.smtp_connections: list[object] = []
        self.attachment_reads: list[object] = []

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
            output = self._audit_output(prompt)
        else:
            output = self._consumer_output()
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

    def _consumer_output(self) -> dict[str, object]:
        continuation = self.email_store.get_email_unsubscribe_continuation(
            str(self.task_payload["action_identity"])
        )
        if continuation is None:
            operations = [
                {
                    "operation_reference": OPEN_OPERATION_REFERENCE,
                    "kind": UnsubscribeOperationKind.OPEN_ENTRY.value,
                    "target_reference": self.task_payload["unsubscribe_entries"][0][
                        "reference"
                    ],
                }
            ]
        else:
            accepted_prefix = list(continuation["operations"])
            [control] = continuation["controls"]
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
                "action_identity": self.task_payload["action_identity"],
                "account_id": self.task_payload["account_id"],
                "stable_message_identity": self.task_payload["stable_message_identity"],
                "thread_identity": self.task_payload["thread_identity"],
                "entry_reference": self.task_payload["unsubscribe_entries"][0][
                    "reference"
                ],
                "network_policy_reference": self.task_payload[
                    "unsubscribe_network_policy_reference"
                ],
                "network_policy_origin_references": self.task_payload[
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
        result = self.audit_operation.execute(
            task.id,
            task.execution_generation,
            audit_agent_run_id=audit_run.id,
            accepted_action=accepted_action,
        )
        self.audit_invocations.append(
            {
                "run_id": audit_run.id,
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
        attachments=(),
        list_unsubscribe=f"<{private_url}>",
        body_text="Manage subscription",
        unsubscribe_network_policy_reference=policy.reference,
        unsubscribe_network_policy_origin_references=policy.origin_references,
        unsubscribe_allow_loopback_for_tests=True,
    )
    return plan, task_input


def test_two_page_unsubscribe_runs_two_consumer_audit_rounds_and_finishes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "audited-email-e2e.sqlite3"
    email_store = EmailStore(database)
    task_store = AutoReplyStore(database)
    browser_launches: list[dict[str, object]] = []
    dedicated_profile = EmailBrowserProfile(tmp_path / "email-browser-runtime")
    main_chrome_profile = (
        Path.home() / "Library/Application Support/Google/Chrome"
    ).resolve()

    def launch_for_fixture(playwright, profile, *, headless=True, **kwargs):
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
        plan, task_input = _persist_confirmed_subscription(
            email_store,
            origin=origin,
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
        executor = _AuditedTurnExecutor(
            task_store=task_store,
            email_store=email_store,
            task_id=route.task.id,
            task_payload=payload,
            audit_operation=audit_operation,
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
    )
    assert [run.role for run in runs].count(AgentRole.CONSUMER) == 2
    assert [run.role for run in runs].count(AgentRole.AUDIT) == 2
    assert all(run.status == "completed" for run in runs)
    assert len(executor.audit_invocations) == 2

    first_operations = executor.consumer_proposals[0]["payload"]["operations"]
    second_operations = executor.consumer_proposals[1]["payload"]["operations"]
    assert [item["kind"] for item in first_operations] == ["open_entry"]
    assert [item["kind"] for item in second_operations] == [
        "open_entry",
        "submit_form",
    ]
    assert second_operations[:-1] == first_operations
    assert [
        len(invocation["accepted_action"]["payload"]["operations"])
        for invocation in executor.audit_invocations
    ] == [1, 2]

    steps = email_store.list_email_unsubscribe_steps(str(payload["action_identity"]))
    assert [step["operation"] for step in steps] == ["open_entry", "submit_form"]
    receipt = email_store.get_email_unsubscribe_receipt(str(payload["action_identity"]))
    assert receipt is not None
    assert receipt["result_text"] == "You have been unsubscribed"
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
    assert executor.smtp_connections == []
    assert executor.attachment_reads == []
    assert all(
        "execute_audited_email_unsubscribe" not in json.dumps(command)
        for command in executor.commands[0::2]
    )
    assert all(
        "execute_audited_email_unsubscribe" in json.dumps(command)
        for command in executor.commands[1::2]
    )
