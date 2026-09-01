from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sqlite3
from threading import Thread
from urllib.request import urlopen

import pytest

from app.agent_result import AgentError
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
)
from app.email_unsubscribe import (
    BrowserNetworkPolicy,
    EmailUnsubscribeEffect,
    UnsubscribeEntry,
    UnsubscribeEntrySource,
    UnsubscribeObservation,
    UnsubscribeOperationKind,
    UnsubscribeOutcome,
    UnsubscribePageState,
    UnsubscribeTerminalReceipt,
    UnsubscribeExecutor,
    unsubscribe_entry_reference,
)
from app.email_unsubscribe_consumer import (
    EmailUnsubscribeConsumerAgentRunner,
    parse_email_unsubscribe_agent_turn_result,
)
from app.email_unsubscribe_operation import EmailUnsubscribeTaskOperation
from app.store import AutoReplyStore


pytestmark = pytest.mark.skipif(
    os.environ.get("WORKBENCH_BROWSER_TESTS") != "1",
    reason="set WORKBENCH_BROWSER_TESTS=1 to run loopback Email E2E",
)


class _LoopbackHandler(BaseHTTPRequestHandler):
    requests: list[str] = []

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802
        type(self).requests.append(self.path)
        body = b"You have been successfully unsubscribed."
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class _LoopbackServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False


@contextmanager
def _loopback_site():
    _LoopbackHandler.requests = []
    server = _LoopbackServer(("127.0.0.1", 0), _LoopbackHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


class _LoopbackBrowser:
    def find_confirmation_receipt(self, _effect):
        return None

    def inspect_current_state(self, effect, _private_url):
        return UnsubscribeObservation(
            state=UnsubscribePageState.ACTION_REQUIRED,
            state_reference="loopback-state-ready",
            next_operation_reference=effect.operations[0].operation_reference,
        )

    def execute_operation(self, effect, private_url, operation):
        assert operation.kind is UnsubscribeOperationKind.OPEN_ENTRY
        with urlopen(private_url, timeout=5) as response:  # noqa: S310 - loopback fixture
            visible_text = response.read().decode("utf-8")
        return UnsubscribeObservation(
            state=UnsubscribePageState.DONE,
            state_reference="loopback-state-done",
            receipt=UnsubscribeTerminalReceipt(
                receipt_id="unsubscribe-receipt:loopback-e2e",
                evidence="loopback-terminal-page",
                entry_reference=effect.entry_reference,
                effect_digest=effect.effect_digest,
            ),
            visible_text=visible_text,
        )


def _create_store(tmp_path: Path, origin: str):
    store = EmailStore(tmp_path / "email-e2e.sqlite3")
    store.create_account(
        {
            "account_id": "e2e-account",
            "display_name": "E2E",
            "email_address": "e2e@example.com",
            "imap_host": "imap.example.com",
            "imap_port": 993,
            "imap_tls": True,
            "imap_username": "e2e@example.com",
            "imap_secret_reference": "keychain://e2e-imap",
            "smtp_host": "smtp.example.com",
            "smtp_port": 465,
            "smtp_tls": True,
            "smtp_username": "e2e@example.com",
            "smtp_secret_reference": "keychain://e2e-smtp",
            "enabled": True,
            "scan_folders": ["INBOX"],
            "scan_interval_seconds": 60,
        }
    )
    stable_identity = "e2e-account:message-id:<newsletter@example.com>"
    plan = build_versioned_email_action_plan(
        action_plan_version=1,
        classification_id=9001,
        account_id="e2e-account",
        category=EmailCategory.SUBSCRIPTION,
        classification_source="user",
        confidence=1.0,
        model_id="email-model:e2e",
        config_version="email-config:e2e",
        actions=(EmailAction.UNSUBSCRIBE,),
        action_parameters={},
        created_at=datetime(2026, 8, 30, 8, 0, tzinfo=timezone.utc),
    )
    store.upsert_classification(
        EmailClassification.model_validate(
            {
                "classification_id": 9001,
                "stable_message_identity": stable_identity,
                "provider_locator": {
                    "account_id": "e2e-account",
                    "folder": "INBOX",
                    "uidvalidity": 77,
                    "uid": 9001,
                    "rfc_message_id": "<newsletter@example.com>",
                    "thread_id": "e2e-thread",
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
        received_at="2026-08-30T08:00:00+00:00",
    )
    policy = BrowserNetworkPolicy(
        allowed_origins=frozenset({origin}),
        allow_loopback_for_tests=True,
    )
    task_input = EmailAgentTaskInput(
        stable_message_identity=stable_identity,
        thread_identity="e2e-thread",
        subject="Weekly newsletter",
        trigger=EmailThreadMessage(
            message_id=stable_identity,
            sender="newsletter@example.com",
            text="This is an unwanted newsletter. Unsubscribe me.",
            create_time="2026-08-30T08:00:00+00:00",
        ),
        list_unsubscribe=f"<{origin}/unsubscribe>",
        body_text="Manage your subscription or unsubscribe.",
        unsubscribe_network_policy_reference=policy.reference,
        unsubscribe_network_policy_origin_references=policy.origin_references,
        unsubscribe_allow_loopback_for_tests=True,
    )
    route = EmailAgentTaskAdapter(
        AutoReplyStore(store.path), store
    ).ensure_action_plan_tasks(plan, task_input)[0]
    task = AutoReplyStore(store.path).claim_reply_task(route.task.id)
    assert task is not None
    return store, plan, task, route.context, task_input


def test_user_confirmed_classification_reaches_consumer_direct_terminal_receipt_without_audit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    import app.email_unsubscribe_consumer as consumer_module
    from app.email_worker import _finalize_email_task

    with _loopback_site() as origin:
        store, plan, task, context, task_input = _create_store(tmp_path, origin)
        entry = UnsubscribeEntry(
            source=UnsubscribeEntrySource.HEADER_HTTPS,
            reference=unsubscribe_entry_reference(f"{origin}/unsubscribe"),
            private_url=f"{origin}/unsubscribe",
            priority=10,
        )

        def resolve_entries(_locator, _expected_reference, _authentication=None):
            return (entry,)

        def execute_effect(
            effect: EmailUnsubscribeEffect,
            resolved_entry: UnsubscribeEntry,
            *,
            owner,
            automatic_continuation,
        ):
            result = UnsubscribeExecutor(
                store,
                _LoopbackBrowser(),
                owner=owner,
                automatic_continuation=automatic_continuation,
            ).execute(effect, (resolved_entry,), automatic=True)
            assert result.outcome is UnsubscribeOutcome.DONE
            assert result.receipt is not None
            return {
                "status": "done",
                "outcome": result.outcome.value,
                "receipt_id": result.receipt.receipt_id,
                "evidence": result.receipt.evidence,
                "result_text": result.result_text,
                "observation_digest": result.observation_digest,
                "summary": result.result_text,
                "error": AgentError().model_dump(mode="json"),
            }

        operation = EmailUnsubscribeTaskOperation(
            task_store=AutoReplyStore(store.path),
            email_store=store,
            resolve_entries=resolve_entries,
            execute_effect=execute_effect,
        )

        class FakeProcess:
            def __class_getitem__(cls, _item):
                return cls

            def __init__(self, **_kwargs):
                pass

            def execute(self, **kwargs):
                raw = operation.execute(task.id, task.execution_generation)
                receipt_id = raw["receipt_id"]
                response = {
                    "outcome": "executed",
                    "summary": "Consumer invoked the task-bound operation.",
                    "receipt_id": receipt_id,
                    "error": {},
                }
                parsed = parse_email_unsubscribe_agent_turn_result(
                    json.dumps(
                        {
                            "type": "response_item",
                            "payload": {
                                "type": "message",
                                "role": "assistant",
                                "content": [
                                    {"type": "output_text", "text": json.dumps(response)}
                                ],
                            },
                        }
                    )
                )
                return consumer_module.AgentTurnRunResult(
                    run_id=kwargs["run"].id,
                    result=parsed,
                    transcript_start_line=0,
                    transcript_end_line=1,
                )

        monkeypatch.setattr(consumer_module, "AgentTurnProcess", FakeProcess)
        runner = EmailUnsubscribeConsumerAgentRunner(
            store=AutoReplyStore(store.path),
            workspace=tmp_path,
            receipt_loader=store.get_email_unsubscribe_receipt,
        )

        result = runner.run(task, context)
        _finalize_email_task(AutoReplyStore(store.path), task, result)

        final_task = AutoReplyStore(store.path).get_reply_task(task.id)
        assert final_task is not None
        assert final_task.status == "done"
        assert result.outcome == "executed"
        assert result.receipt_id == "unsubscribe-receipt:loopback-e2e"
        assert _LoopbackHandler.requests == ["/unsubscribe"]
        receipt = store.get_email_unsubscribe_receipt(
            email_action_identity(
                account_id=plan.account_id,
                stable_message_identity=task_input.stable_message_identity,
                action_type=EmailAction.UNSUBSCRIBE,
                action_plan_version=plan.action_plan_version,
            )
        )
        assert receipt is not None
        assert receipt["result_text"] == (
            "You have been successfully unsubscribed."
        )
        with sqlite3.connect(store.path) as db:
            send_status = db.execute(
                "select send_status from reply_attempts "
                "where channel='email' and conversation_id=? "
                "and trigger_message_id=? order by id desc limit 1",
                (task.conversation_id, task.trigger_message_id),
            ).fetchone()[0]
            audit_runs = db.execute(
                "select count(*) from agent_runs where reply_task_id=? and role='audit'",
                (task.id,),
            ).fetchone()[0]
            consumer_runs = db.execute(
                "select count(*) from agent_runs where reply_task_id=? and role='consumer'",
                (task.id,),
            ).fetchone()[0]
        assert send_status == "completed"
        assert audit_runs == 0
        assert consumer_runs == 1
