from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from hashlib import sha256
from importlib import import_module
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.agent_context import AgentTaskContext
from app.agent_contracts import ProposedAction
from app.email_classifier_contracts import (
    EmailAction,
    EmailCategory,
    EmailClassification,
    EmailClassificationStatus,
    EmailProviderLocator,
    build_versioned_email_action_plan,
)
from app.email_imap_readonly import (
    attach_ephemeral_unsubscribe_authentication,
    parse_rfc822_message,
)
from app.email_store import email_action_identity
from app.email_task_adapter import email_conversation_id
from app.email_task_producer import EmailActionTaskProducer
from app.email_unsubscribe import (
    BrowserNetworkPolicy,
    UnsubscribeAuthenticationEvidence,
    extract_unsubscribe_entries,
)
from app.email_unsubscribe_audit import EmailUnsubscribeAuditOperation
from app.store import AgentRole, AutoReplyStore
from app.email_store import EmailStore


def _module():
    return import_module("app.email_worker")


def _dependencies(
    events,
    *,
    accounts=(
        {"account_id": "account-1", "enabled": True, "scan_interval_seconds": 60},
    ),
    model=object(),
):
    return SimpleNamespace(
        load_enabled_accounts=lambda: events.append("accounts") or accounts,
        load_active_model=lambda: events.append("model") or model,
        scan_account=lambda account, active_model: None,
        run_direct_actions_once=lambda: None,
        email_store=SimpleNamespace(
            list_nonterminal_legacy_unsubscribe_task_attempts=lambda: ()
        ),
        task_store=SimpleNamespace(claim_reply_tasks=lambda *args, **kwargs: []),
        orchestrator=SimpleNamespace(process=lambda *args, **kwargs: None),
        load_task_context=lambda task: None,
        finalize_task=lambda task, result: None,
        training_tick=lambda: None,
        record_health=lambda scope, payload: None,
    )


def _persist_email_worker_health(task_store):
    def record_health(scope, payload):
        task_store.set_service_state(
            f"email_worker_health:{scope}",
            json.dumps(dict(payload), sort_keys=True, separators=(",", ":")),
        )

    return record_health


def test_build_audited_email_unsubscribe_operation_wires_real_runtime_seams(
    tmp_path,
    monkeypatch,
):
    module = _module()
    private_url = "https://news.example.com/unsubscribe?token=runtime-only"
    entry = extract_unsubscribe_entries(list_unsubscribe=f"<{private_url}>")[0]
    source_events = []

    class Source:
        def fetch_uid_batch(self, folder, *, cursor_uidvalidity, last_seen_uid, limit):
            source_events.append(
                ("fetch", folder, cursor_uidvalidity, last_seen_uid, limit)
            )
            return SimpleNamespace(
                uidvalidity=42,
                messages=(
                    {
                        "uid": 7,
                        "stableMessageIdentity": (
                            "account-1:message-id:<mail-7@example.com>"
                        ),
                        "listUnsubscribe": f"<{private_url}>",
                        "listUnsubscribePost": "",
                        "textBody": "metadata-only test body",
                    },
                ),
            )

        def logout(self):
            source_events.append("logout")

    source = Source()
    monkeypatch.setattr(
        module,
        "_build_email_source_factory",
        lambda _settings: lambda _account: source,
    )
    execution_calls = []
    sentinel_result = object()

    def fake_execute(effect, entries, **kwargs):
        execution_calls.append((effect, entries, kwargs))
        return sentinel_result

    import app.email_unsubscribe as email_unsubscribe

    monkeypatch.setattr(
        email_unsubscribe,
        "execute_unsubscribe_in_dedicated_profile",
        fake_execute,
    )
    settings = SimpleNamespace(
        db_path=tmp_path / "email-worker.sqlite3",
        workspace=tmp_path,
    )

    operation = module.build_audited_email_unsubscribe_operation(settings)

    assert isinstance(operation, EmailUnsubscribeAuditOperation)
    assert isinstance(operation.task_store, AutoReplyStore)
    assert isinstance(operation.email_store, EmailStore)
    assert operation.task_store.path == Path(settings.db_path)
    assert operation.email_store.path == Path(settings.db_path)
    monkeypatch.setattr(
        operation.email_store,
        "get_account",
        lambda account_id: {"account_id": account_id},
    )
    locator = EmailProviderLocator(
        account_id="account-1",
        folder="INBOX",
        uidvalidity=42,
        uid=7,
        rfc_message_id="<mail-7@example.com>",
        thread_id="thread-7",
    )
    policy = BrowserNetworkPolicy(frozenset({"https://news.example.com"}))
    assert operation.resolve_entries(
        locator,
        entry.reference,
        network_policy_reference=policy.reference,
        network_policy_origin_references=policy.origin_references,
    ) == (entry,)
    assert source_events == [("fetch", "INBOX", 42, 6, 2), "logout"]
    with pytest.raises(ValueError, match="network policy changed"):
        operation.resolve_entries(
            locator,
            entry.reference,
            network_policy_reference="network-policy:stale",
            network_policy_origin_references=("network-origin:stale",),
        )
    assert execution_calls == []

    effect = SimpleNamespace(
        entry_reference=entry.reference,
        network_policy_reference=policy.reference,
        network_policy_origin_references=policy.origin_references,
    )
    owner = {"owner_id": "audit", "generation": 1, "lease_token": "lease"}
    assert (
        operation.execute_effect(
            effect,
            (entry,),
            owner=owner,
            executed_prefix_length=0,
        )
        is sentinel_result
    )
    assert execution_calls[0][0:2] == (effect, (entry,))
    assert execution_calls[0][2]["store"] is operation.email_store
    assert execution_calls[0][2]["owner"] is owner
    assert "automatic" not in execution_calls[0][2]
    assert execution_calls[0][2]["executed_prefix_length"] == 0


def test_audited_unsubscribe_resolves_html_only_provider_entry_in_memory(
    tmp_path,
    monkeypatch,
):
    module = _module()
    private_url = "https://news.example.com/unsubscribe?token=html-runtime-only"
    raw_message = (
        b"From: sender@example.com\r\n"
        b"To: derek@example.com\r\n"
        b"Subject: HTML unsubscribe\r\n"
        b"Message-ID: <mail-7@example.com>\r\n"
        b"Content-Type: text/html; charset=utf-8\r\n\r\n"
        + ('<a href="' + private_url + '">Unsubscribe</a>').encode()
    )
    from app.email_imap_readonly import ephemeral_body_html, parse_rfc822_message

    provider_message = parse_rfc822_message(
        raw_message,
        account_id="account-1",
        folder="INBOX",
        uidvalidity=42,
        uid=7,
    )
    entry = extract_unsubscribe_entries(
        body_html=ephemeral_body_html(provider_message)
    )[0]
    policy = BrowserNetworkPolicy(frozenset({"https://news.example.com"}))

    class Source:
        def fetch_uid_batch(self, folder, *, cursor_uidvalidity, last_seen_uid, limit):
            return SimpleNamespace(uidvalidity=42, messages=(provider_message,))

        def logout(self):
            return None

    monkeypatch.setattr(
        module,
        "_build_email_source_factory",
        lambda _settings: lambda _account: Source(),
    )
    settings = SimpleNamespace(
        db_path=tmp_path / "email-worker.sqlite3",
        workspace=tmp_path,
    )
    operation = module.build_audited_email_unsubscribe_operation(settings)
    monkeypatch.setattr(
        operation.email_store,
        "get_account",
        lambda account_id: {"account_id": account_id},
    )
    locator = EmailProviderLocator(
        account_id="account-1",
        folder="INBOX",
        uidvalidity=42,
        uid=7,
        rfc_message_id="<mail-7@example.com>",
        thread_id="thread-7",
    )

    assert operation.resolve_entries(
        locator,
        entry.reference,
        network_policy_reference=policy.reference,
        network_policy_origin_references=policy.origin_references,
    ) == (entry,)
    assert private_url not in repr(provider_message)


def test_run_audited_email_unsubscribe_forwards_exact_binding(
    tmp_path,
    monkeypatch,
):
    module = _module()
    calls = []
    accepted_action = {"operation": "unsubscribe", "target": {"id": "opaque"}}
    expected = {"status": "done", "summary": "fake result"}

    class Operation:
        def execute(
            self,
            task_id,
            execution_generation,
            *,
            audit_agent_run_id,
            accepted_action,
        ):
            calls.append(
                (
                    task_id,
                    execution_generation,
                    audit_agent_run_id,
                    accepted_action,
                )
            )
            return expected

    monkeypatch.setattr(
        module,
        "build_audited_email_unsubscribe_operation",
        lambda settings: calls.append(("settings", settings)) or Operation(),
    )

    result = module.run_audited_email_unsubscribe(
        tmp_path / "email-worker.sqlite3",
        17,
        "generation-17",
        audit_agent_run_id=29,
        accepted_action=accepted_action,
    )

    assert result is expected
    assert calls[0][0] == "settings"
    assert calls[0][1].db_path == tmp_path / "email-worker.sqlite3"
    assert calls[0][1].workspace == tmp_path
    assert calls[1] == (17, "generation-17", 29, accepted_action)


@pytest.mark.parametrize("provider_shape", ("html_only", "one_click"))
def test_production_unsubscribe_task_reload_and_audit_preserve_opaque_bindings(
    tmp_path,
    monkeypatch,
    provider_shape,
):
    module = _module()
    db_path = tmp_path / f"production-{provider_shape}.sqlite3"
    account_id = "account-production"
    stable_identity = "account-production:message-id:<mail-41@example.com>"
    thread_identity = "thread-production-41"
    private_url = "https://news.example.com/unsubscribe?token=production-private-secret"
    plan = build_versioned_email_action_plan(
        action_plan_version=1,
        classification_id=41,
        account_id=account_id,
        category=EmailCategory.SUBSCRIPTION,
        classification_source="user",
        confidence=1.0,
        model_id="email-model:production-path",
        config_version="email-config:production-path",
        actions=(EmailAction.UNSUBSCRIBE,),
        action_parameters={},
        created_at=datetime(2026, 9, 2, 8, 0, tzinfo=timezone.utc),
    )
    if provider_shape == "html_only":
        unsubscribe_headers = b""
        body = f'<a href="{private_url}">Unsubscribe</a>'.encode()
    else:
        unsubscribe_headers = (
            f"List-Unsubscribe: <{private_url}>\r\n"
            "List-Unsubscribe-Post: List-Unsubscribe=One-Click\r\n"
        ).encode()
        body = b"Subscription message"
    provider_message = parse_rfc822_message(
        b"From: sender@example.com\r\n"
        b"To: derek@example.com\r\n"
        b"Subject: Production subscription\r\n"
        b"Date: Wed, 02 Sep 2026 08:00:00 +0000\r\n"
        b"Message-ID: <mail-41@example.com>\r\n"
        + unsubscribe_headers
        + b"Content-Type: text/html; charset=utf-8\r\n\r\n"
        + body,
        account_id=account_id,
        folder="INBOX",
        uidvalidity=42,
        uid=41,
    )
    provider_message["threadId"] = thread_identity
    if provider_shape == "one_click":
        attach_ephemeral_unsubscribe_authentication(
            provider_message,
            UnsubscribeAuthenticationEvidence(
                dkim_covers_list_unsubscribe=True,
                dkim_covers_list_unsubscribe_post=True,
                evidence_reference="dkim-evidence:production-message",
            ),
        )

    email_store = EmailStore(db_path)
    email_store.create_account(
        {
            "account_id": account_id,
            "display_name": "Production",
            "email_address": "derek@example.com",
            "imap_host": "imap.example.com",
            "imap_port": 993,
            "imap_tls": True,
            "imap_username": "derek@example.com",
            "imap_secret_reference": "keychain://production-imap",
            "smtp_host": "smtp.example.com",
            "smtp_port": 465,
            "smtp_tls": True,
            "smtp_username": "derek@example.com",
            "smtp_secret_reference": "keychain://production-smtp",
            "enabled": True,
            "scan_folders": ["INBOX"],
            "scan_interval_seconds": 60,
        }
    )
    email_store.upsert_classification(
        EmailClassification(
            classification_id=41,
            stable_message_identity=stable_identity,
            provider_locator=EmailProviderLocator(
                account_id=account_id,
                folder="INBOX",
                uidvalidity=42,
                uid=41,
                rfc_message_id="<mail-41@example.com>",
                thread_id=thread_identity,
            ),
            category=EmailCategory.SUBSCRIPTION,
            confidence=1.0,
            margin=1.0,
            probabilities={EmailCategory.SUBSCRIPTION: 1.0},
            model_id=plan.model_id,
            config_version=plan.config_version,
            status=EmailClassificationStatus.PROCESSED,
            classification_source="user",
            action_plan=plan,
        ),
        sender="sender@example.com",
        subject="Production subscription",
        model_text="__subject__newsletter",
        received_at="2026-09-02T08:00:00+00:00",
    )
    task_store = AutoReplyStore(db_path)
    [route] = EmailActionTaskProducer(task_store, email_store).produce(
        plan,
        provider_message,
    )
    payload = json.loads(route.task.trigger_message_json)
    policy = BrowserNetworkPolicy(frozenset({"https://news.example.com"}))
    assert payload["unsubscribe_network_policy_reference"] == policy.reference
    assert payload["unsubscribe_network_policy_origin_references"] == list(
        policy.origin_references
    )
    expected_authentication = (
        None
        if provider_shape == "html_only"
        else {
            "evidence_reference": "dkim-evidence:production-message",
            "one_click_verified": True,
        }
    )
    assert payload["unsubscribe_authentication"] == expected_authentication

    class Source:
        def fetch_uid_batch(self, *_args, **_kwargs):
            return SimpleNamespace(uidvalidity=42, messages=(provider_message,))

        def logout(self):
            return None

    def source_factory(_account):
        return Source()

    context = module._load_email_task_context(
        email_store,
        task_store,
        source_factory,
        route.task,
    )
    assert context.trigger_raw_payload == payload
    assert private_url not in repr(context)

    task = task_store.claim_reply_task(route.task.id)
    assert task is not None
    [projected_entry] = payload["unsubscribe_entries"]
    operation_kind = "post_one_click" if provider_shape == "one_click" else "open_entry"
    accepted_action = ProposedAction.model_validate(
        {
            "description": "Unsubscribe the current subscription",
            "capability": "email_browser",
            "operation": "unsubscribe",
            "target": {
                "action_identity": payload["action_identity"],
                "account_id": account_id,
                "stable_message_identity": stable_identity,
                "thread_identity": thread_identity,
                "entry_reference": projected_entry["reference"],
                "network_policy_reference": policy.reference,
                "network_policy_origin_references": list(policy.origin_references),
            },
            "payload": {
                "operations": [
                    {
                        "operation_reference": "operation:production-path",
                        "kind": operation_kind,
                        "target_reference": projected_entry["reference"],
                    }
                ]
            },
            "expected_verification": "Read terminal provider evidence",
        }
    ).model_dump(mode="json")
    consumer = task_store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=None,
        operation_id="",
        owner="consumer-owner",
    ).run
    consumer = task_store.complete_agent_run(
        consumer.id,
        {
            "outcome": "proposal",
            "summary": "Propose one audited unsubscribe operation.",
            "proposal": {
                "objective": "Unsubscribe the current subscription.",
                "actions": [accepted_action],
                "sourced_facts": [],
                "authored_judgment": "The ActionPlan authorizes unsubscribe.",
            },
            "decision_options": [],
            "error": {
                "code": "",
                "retryable": False,
                "authorization_required": False,
                "stage": "",
                "source": "",
                "source_code": "",
                "session_continuable": False,
            },
            "risk": "low",
            "confidence": 1.0,
        },
        owner="consumer-owner",
    )
    audit = task_store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=consumer.id,
        operation_id="audit-production-path",
        owner="audit-owner",
    ).run
    monkeypatch.setattr(
        module, "_build_email_source_factory", lambda _settings: source_factory
    )
    execution_calls = []

    def fake_dedicated_profile(effect, entries, **kwargs):
        execution_calls.append((effect, entries, kwargs))
        assert kwargs["network_policy"].reference == policy.reference
        assert kwargs["network_policy"].origin_references == policy.origin_references
        assert effect.network_policy_reference == policy.reference
        assert effect.network_policy_origin_references == policy.origin_references
        assert "automatic" not in kwargs
        return {
            "status": "done",
            "outcome": "done",
            "receipt_id": "provider-receipt:production-path",
            "evidence": "terminal-page",
            "result_text": "Unsubscribed",
            "observation_digest": sha256(b"Unsubscribed").hexdigest(),
            "started_at": "2026-09-02T08:00:01+00:00",
            "completed_at": "2026-09-02T08:00:02+00:00",
            "summary": "Unsubscribed",
            "final_step": {
                "sequence": 1,
                "operation": operation_kind,
                "state": "done",
                "reference": "provider-receipt:production-path",
            },
        }

    import app.email_unsubscribe as email_unsubscribe

    monkeypatch.setattr(
        email_unsubscribe,
        "execute_unsubscribe_in_dedicated_profile",
        fake_dedicated_profile,
    )
    operation = module.build_audited_email_unsubscribe_operation(
        SimpleNamespace(db_path=db_path, workspace=tmp_path)
    )
    result = operation.execute(
        task.id,
        task.execution_generation,
        audit_agent_run_id=audit.id,
        accepted_action=accepted_action,
    )

    assert result["status"] == "done", result
    assert len(execution_calls) == 1
    assert private_url not in route.task.trigger_message_json
    assert "production-private-secret" not in route.task.trigger_message_json
    assert private_url.encode() not in db_path.read_bytes()
    assert b"production-private-secret" not in db_path.read_bytes()


def test_startup_loads_enabled_accounts_and_active_model_before_ready_and_threads():
    module = _module()
    events = []
    output = StringIO()

    class FakeThread:
        def __init__(self, *, target, name, daemon):
            events.append(("create", name, daemon))
            self.name = name

        def start(self):
            events.append(("start", self.name))

    module.run_email_worker(
        SimpleNamespace(),
        dependencies=_dependencies(events),
        thread_factory=FakeThread,
        wait=lambda: events.append("wait"),
        output=output,
    )

    assert events[:2] == ["accounts", "model"]
    assert output.getvalue().strip() == (
        "email-worker starting accounts=1 components=3"
    )
    assert events[2:] == [
        ("create", "ceo-agent-email-scan-actions", True),
        ("start", "ceo-agent-email-scan-actions"),
        ("create", "ceo-agent-email-agent-consumer", True),
        ("start", "ceo-agent-email-agent-consumer"),
        ("create", "ceo-agent-email-training", True),
        ("start", "ceo-agent-email-training"),
        "wait",
    ]


@pytest.mark.parametrize("failure", ["empty_accounts", "model_failure"])
def test_startup_failure_does_not_report_ready_or_start_threads(failure):
    module = _module()
    output = StringIO()
    started = []
    events = []
    dependencies = _dependencies(
        events,
        accounts=()
        if failure == "empty_accounts"
        else ({"account_id": "account-1", "enabled": True},),
    )
    if failure == "model_failure":
        dependencies.load_active_model = lambda: (_ for _ in ()).throw(
            RuntimeError("active model missing")
        )

    module.run_email_worker(
        SimpleNamespace(),
        dependencies=dependencies,
        thread_factory=lambda **kwargs: started.append(kwargs),
        wait=lambda: None,
        output=output,
    )

    assert output.getvalue().startswith("email-worker waiting_configuration")
    assert started == []


@pytest.mark.parametrize("configuration", ["empty_accounts", "missing_model"])
def test_missing_configuration_reports_waiting_configuration_without_starting_threads(
    configuration,
):
    module = _module()
    health = []
    events = []
    dependencies = _dependencies(
        events,
        accounts=()
        if configuration == "empty_accounts"
        else ({"account_id": "account-1", "enabled": True},),
        model=None if configuration == "missing_model" else object(),
    )
    dependencies.record_health = lambda scope, payload: health.append((scope, payload))

    module.run_email_worker(
        SimpleNamespace(),
        dependencies=dependencies,
        thread_factory=lambda **_kwargs: pytest.fail("waiting worker must not start"),
        wait=lambda: None,
        output=StringIO(),
    )

    assert health == [
        (
            "process:email-worker",
            {"status": "waiting_configuration", "reason": configuration},
        )
    ]


@pytest.mark.parametrize("configuration", ("empty_accounts", "missing_model"))
def test_pending_legacy_unsubscribe_is_failed_before_configuration_wait(
    tmp_path,
    configuration,
):
    module = _module()
    health = []
    database = tmp_path / f"legacy-before-{configuration}.sqlite3"
    task_store = AutoReplyStore(database)
    email_store = EmailStore(database)
    legacy = task_store.ensure_reply_task(
        channel="email",
        conversation_id=f"legacy-before-{configuration}",
        conversation_title="Legacy unsubscribe",
        single_chat=False,
        trigger_message_id=f"legacy-before-{configuration}",
        trigger_create_time="2026-09-03T12:00:00+00:00",
        trigger_sender="newsletter@example.com",
        trigger_text="Legacy unsubscribe.",
        trigger_message_json=json.dumps(
            {
                "schema": "email_agent_action.v1",
                "action_type": "unsubscribe",
                "lifecycle_version": "email_unsubscribe_consumer_direct_v1",
            }
        ),
        execution_generation=f"legacy-generation-{configuration}",
    )
    dependencies = _dependencies(
        [],
        accounts=(
            ()
            if configuration == "empty_accounts"
            else ({"account_id": "account-1", "enabled": True},)
        ),
        model=None if configuration == "missing_model" else object(),
    )
    dependencies.email_store = email_store
    dependencies.task_store = task_store
    dependencies.record_health = lambda scope, payload: health.append((scope, payload))

    module.run_email_worker(
        SimpleNamespace(),
        dependencies=dependencies,
        thread_factory=lambda **_kwargs: pytest.fail("waiting worker must not start"),
        wait=lambda: None,
        output=StringIO(),
    )

    terminal = task_store.get_reply_task(legacy.id)
    assert terminal is not None
    assert terminal.status == "failed"
    assert terminal.error == "legacy_email_unsubscribe_lifecycle"
    assert terminal.locked_at is None
    assert terminal.available_at == ""
    assert health[0] == (
        "component:email-legacy-lifecycle",
        {
            "status": "degraded",
            "error_code": "legacy_email_unsubscribe_lifecycle",
            "task_count": 1,
            "isolated_count": 1,
            "superseded_count": 0,
            "unresolved_count": 0,
        },
    )
    assert health[1] == (
        "process:email-worker",
        {"status": "waiting_configuration", "reason": configuration},
    )


def test_email_worker_components_are_three_independent_daemon_threads():
    module = _module()
    events = []

    class FakeThread:
        def __init__(self, *, target, name, daemon):
            events.append(("created", name, daemon, target))
            self.name = name

        def start(self):
            events.append(("started", self.name))

    module.run_email_worker(
        SimpleNamespace(),
        dependencies=_dependencies(events),
        thread_factory=FakeThread,
        wait=lambda: None,
        output=StringIO(),
    )

    created = [event for event in events if event[0] == "created"]
    assert [event[1] for event in created] == [
        "ceo-agent-email-scan-actions",
        "ceo-agent-email-agent-consumer",
        "ceo-agent-email-training",
    ]
    assert all(event[2] is True for event in created)
    assert len({id(event[3]) for event in created}) == 3
    assert [event[1] for event in events if event[0] == "started"] == [
        "ceo-agent-email-scan-actions",
        "ceo-agent-email-agent-consumer",
        "ceo-agent-email-training",
    ]


def test_email_worker_components_use_exact_independent_loop_functions():
    module = _module()
    dependencies = _dependencies([])

    components = module.email_worker_components(
        dependencies,
        accounts=({"account_id": "account-1"},),
        active_model=object(),
    )

    assert tuple(name for name, _target in components) == (
        "email-scan-actions",
        "email-agent-consumer",
        "email-training",
    )
    assert tuple(target.func for _name, target in components) == (
        module.run_scan_and_direct_actions_loop,
        module.run_email_agent_task_loop,
        module.run_training_scheduler_loop,
    )


def test_scan_failure_isolated_per_account_and_health_is_bounded_and_sanitized():
    module = _module()
    sensitive = (
        "mail body contract.pdf CEO_EMAIL_MAIN_IMAP_SECRET "
        "https://mail.example.test/unsubscribe?token=private"
    )
    accounts = (
        {"account_id": "broken", "scan_interval_seconds": 60},
        {"account_id": "healthy", "scan_interval_seconds": 60},
    )
    scanned = []
    health = []

    def scan_account(account, active_model):
        scanned.append((account["account_id"], active_model))
        if account["account_id"] == "broken":
            raise RuntimeError(sensitive)
        return {"persisted_count": 2}

    module.run_scan_and_direct_actions_loop(
        accounts,
        object(),
        scan_account=scan_account,
        run_direct_actions_once=lambda: None,
        record_health=lambda scope, payload: health.append((scope, payload)),
        sleep=lambda _seconds: None,
        max_cycles=1,
    )

    assert [account_id for account_id, _model in scanned] == ["broken", "healthy"]
    assert [scope for scope, _payload in health] == [
        "account:broken",
        "account:healthy",
        "component:email-scan-actions",
    ]
    encoded = repr(health)
    assert len(encoded) < 2_000
    for forbidden in (
        "mail body",
        "contract.pdf",
        "CEO_EMAIL_MAIN_IMAP_SECRET",
        "https://",
        "private",
    ):
        assert forbidden not in encoded
    assert health[0][1]["error_code"] == "provider_runtime_error"


def test_sanitized_scan_result_error_is_not_reported_as_ready():
    module = _module()
    health = []
    scan_result = SimpleNamespace(
        accounts=(
            SimpleNamespace(
                account_id="account-1",
                error_code="connection_failed",
                folders=(),
            ),
        ),
        persisted_count=0,
    )

    module.run_scan_and_direct_actions_loop(
        ({"account_id": "account-1", "scan_interval_seconds": 60},),
        object(),
        scan_account=lambda _account, _model: scan_result,
        run_direct_actions_once=lambda: None,
        record_health=lambda scope, payload: health.append((scope, payload)),
        sleep=lambda _seconds: None,
        max_cycles=1,
    )

    assert health[0] == (
        "account:account-1",
        {"status": "failed", "error_code": "connection_failed"},
    )
    assert health[-1] == (
        "component:email-scan-actions",
        {"status": "degraded", "failures": 1},
    )


def test_email_agent_consumer_claims_only_email_channel_without_dingtalk_adapter(
    monkeypatch,
):
    module = _module()
    monkeypatch.setattr(
        "app.task_lifecycle.validate_audited_email_task",
        lambda _task, _context: True,
    )
    task = SimpleNamespace(id=41)
    calls = []

    class Store:
        def claim_reply_tasks(self, limit, *, channel):
            calls.append(("claim", limit, channel))
            return [task]

    context = object()
    result = object()

    def process(claimed, loaded, *, refresh_context):
        calls.append(("process", claimed, loaded))
        calls.append(("refresh", refresh_context()))
        return result

    orchestrator = SimpleNamespace(process=process)

    module.run_email_agent_task_loop(
        Store(),
        orchestrator,
        load_task_context=lambda claimed: calls.append(("context", claimed)) or context,
        finalize_task=lambda claimed, completed: calls.append(
            ("finalize", claimed, completed)
        ),
        sleep=lambda _seconds: None,
        max_cycles=1,
    )

    assert calls[0] == ("claim", 50, "email")
    assert calls[1] == ("context", task)
    assert calls[2] == ("process", task, context)
    assert calls[3] == ("context", task)
    assert calls[4] == ("refresh", context)
    assert calls[5] == ("finalize", task, result)


def test_email_agent_consumer_does_not_execute_legacy_auto_reply_task():
    module = _module()
    task = SimpleNamespace(
        id=42,
        execution_generation="generation-1",
        trigger_message_json=json.dumps(
            {
                "schema": "email_agent_action.v1",
                "action_type": "auto_reply",
            }
        ),
    )
    failures = []

    class Store:
        def claim_reply_tasks(self, limit, *, channel):
            assert (limit, channel) == (50, "email")
            return [task]

        def fail_reply_task(self, task_id, error, *, expected_execution_generation):
            failures.append((task_id, error, expected_execution_generation))

    module.run_email_agent_task_loop(
        Store(),
        SimpleNamespace(
            process=lambda *_args, **_kwargs: pytest.fail("reply executed")
        ),
        load_task_context=lambda _task: pytest.fail("reply context loaded"),
        finalize_task=lambda *_args: pytest.fail("reply finalized"),
        sleep=lambda _seconds: None,
        max_cycles=1,
    )

    assert failures == [(42, "email_auto_reply_disabled", "generation-1")]


def test_unsubscribe_task_uses_consumer_audit_orchestrator():
    module = _module()
    account_id = "account-1"
    stable_message_identity = "account-1:message-id:<mail@example.com>"
    thread_identity = "thread-1"
    action_identity = email_action_identity(
        account_id=account_id,
        stable_message_identity=stable_message_identity,
        action_type=EmailAction.UNSUBSCRIBE,
        action_plan_version=1,
    )
    entry = extract_unsubscribe_entries(
        list_unsubscribe="<https://news.example.com/unsubscribe?token=private>"
    )[0]
    policy = BrowserNetworkPolicy(frozenset({"https://news.example.com"}))
    payload = {
        "schema": "email_agent_action.v1",
        "lifecycle_version": "email_unsubscribe_audited_v2",
        "action_type": "unsubscribe",
        "action_identity": action_identity,
        "action_plan_id": "email-plan:1",
        "action_plan_version": 1,
        "classification_id": 1,
        "account_id": account_id,
        "stable_message_identity": stable_message_identity,
        "thread_identity": thread_identity,
        "category": "subscription",
        "classification_source": "user",
        "confidence": 1.0,
        "model_id": "email-model:test",
        "config_version": "email-config:test",
        "action_parameters": {},
        "unsubscribe_entries": [
            {
                "source": entry.source.value,
                "reference": entry.reference,
                "priority": entry.priority,
            }
        ],
        "unsubscribe_authentication": None,
        "unsubscribe_network_policy_reference": policy.reference,
        "unsubscribe_network_policy_origin_references": list(policy.origin_references),
    }
    task = SimpleNamespace(
        id=7,
        execution_generation="generation-1",
        channel="email",
        conversation_id=email_conversation_id(account_id, thread_identity),
        conversation_title="Email unsubscribe",
        trigger_message_id=action_identity,
        trigger_sender="sender@example.com",
        trigger_text="Immutable ActionPlan authorizes unsubscribe.",
        trigger_create_time="2026-08-30T08:00:00+00:00",
        trigger_message_json=json.dumps(payload),
    )
    context = AgentTaskContext(
        task_id=task.id,
        channel="email",
        conversation_id=task.conversation_id,
        conversation_title=task.conversation_title,
        single_chat=False,
        trigger_message_id=task.trigger_message_id,
        trigger_sender=task.trigger_sender,
        trigger_text=task.trigger_text,
        trigger_create_time=task.trigger_create_time,
        messages=(),
        materials=(),
        prior_receipts=(),
        trigger_raw_payload=payload,
    )
    calls = []

    class Store:
        def claim_reply_tasks(self, limit, *, channel):
            calls.append(("claim", limit, channel))
            return [task]

    def process(claimed, loaded, *, refresh_context):
        calls.append(("process", claimed, loaded, refresh_context()))
        return "audited-result"

    orchestrator = SimpleNamespace(process=process)
    module.run_email_agent_task_loop(
        Store(),
        orchestrator,
        load_task_context=lambda claimed: context,
        finalize_task=lambda claimed, result: calls.append(
            ("finalize", claimed, result)
        ),
        sleep=lambda _seconds: None,
        max_cycles=1,
    )

    assert calls[1] == ("process", task, context, context)
    assert calls[2] == ("finalize", task, "audited-result")


def test_default_dependency_builder_has_no_direct_unsubscribe_consumer(
    tmp_path, monkeypatch
):
    module = _module()
    sentinel = object()

    def direct_factory(_account_id):
        return None

    monkeypatch.setattr(
        module,
        "_build_agent_orchestrator",
        lambda *_args, **_kwargs: sentinel,
    )
    monkeypatch.setattr(
        module,
        "_build_imap_direct_action_executor_factory",
        lambda _store: direct_factory,
    )
    from app.email_model_registry import EmailModelRegistry

    monkeypatch.setattr(
        EmailModelRegistry,
        "get_model",
        lambda self, _model_id: SimpleNamespace(
            metadata=SimpleNamespace(per_category_metrics={})
        ),
    )
    monkeypatch.setattr(
        module,
        "_scan_config",
        lambda *_args, **_kwargs: SimpleNamespace(
            config_version="test",
            thresholds={},
            actions={},
            category_eligibility={},
            action_parameters={},
            category_enabled={},
        ),
    )

    settings = SimpleNamespace(
        db_path=tmp_path / "worker.sqlite3",
        workspace=tmp_path,
        dry_run=False,
    )
    bootstrap = module.build_email_worker_dependencies(settings)
    dependencies = bootstrap.build_dependencies(
        ({"account_id": "account-1", "enabled": True},),
        SimpleNamespace(
            loaded=SimpleNamespace(model_id="email-model:test", classifier=object()),
            tick=lambda: None,
        ),
    )

    assert dependencies.orchestrator is sentinel
    assert dependencies.run_direct_actions_once.args[1] is direct_factory
    assert not hasattr(dependencies, "unsubscribe_consumer")
    assert not hasattr(module, "_build_email_unsubscribe_consumer")
    assert not hasattr(module, "build_email_unsubscribe_operation")
    assert not hasattr(module, "run_email_unsubscribe_task")


def test_agent_orchestrator_is_wired_with_email_continuation_driver(
    tmp_path, monkeypatch
):
    module = _module()
    runtime = SimpleNamespace(
        config=object(),
        router=object(),
        codex_adapter=object(),
        claude_adapter=object(),
        friday_adapter=object(),
        refresh_runtime_capabilities=lambda: None,
    )
    monkeypatch.setattr(
        "app.agent_runtime_production.build_production_agent_runtime",
        lambda **_kwargs: runtime,
    )
    monkeypatch.setattr(
        "app.consumer_agent.ConsumerAgentRunner",
        lambda **kwargs: SimpleNamespace(kwargs=kwargs),
    )
    monkeypatch.setattr(
        "app.audit_agent.AuditAgentRunner",
        lambda **kwargs: SimpleNamespace(kwargs=kwargs),
    )
    captured = {}

    def build_orchestrator(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(**kwargs)

    monkeypatch.setattr(
        "app.agent_orchestrator.AgentOrchestrator",
        build_orchestrator,
    )
    settings = SimpleNamespace(
        db_path=tmp_path / "worker.sqlite3",
        workspace=tmp_path,
        dry_run=False,
    )

    result = module._build_agent_orchestrator(
        settings,
        AutoReplyStore(settings.db_path),
    )

    from app.email_unsubscribe_continuation import (
        EmailUnsubscribeContinuationDriver,
    )

    assert result.domain_continuation is captured["domain_continuation"]
    assert isinstance(result.domain_continuation, EmailUnsubscribeContinuationDriver)
    assert result.domain_continuation.email_store.path == settings.db_path


def test_email_orchestrator_scopes_runtime_skill_snapshot_to_consumer(
    tmp_path, monkeypatch
):
    module = _module()
    runtime = SimpleNamespace(
        config=object(), router=object(), codex_adapter=object(),
        claude_adapter=object(), friday_adapter=object(),
        refresh_runtime_capabilities=lambda: None,
    )
    monkeypatch.setattr(
        "app.agent_runtime_production.build_production_agent_runtime",
        lambda **_kwargs: runtime,
    )
    captured = {}
    monkeypatch.setattr(
        "app.consumer_agent.ConsumerAgentRunner",
        lambda **kwargs: captured.setdefault("consumer", kwargs),
    )

    def build_audit(**kwargs):
        captured["audit"] = kwargs
        assert "runtime_skill_snapshot" not in kwargs
        return object()

    monkeypatch.setattr(
        "app.audit_agent.AuditAgentRunner", build_audit,
    )
    monkeypatch.setattr(
        "app.agent_orchestrator.AgentOrchestrator", lambda **kwargs: object(),
    )
    snapshot = object()
    settings = SimpleNamespace(db_path=tmp_path / "worker.sqlite3", workspace=tmp_path, dry_run=False)

    module._build_agent_orchestrator(
        settings, AutoReplyStore(settings.db_path), runtime_skill_snapshot=snapshot
    )

    assert captured["consumer"]["runtime_skill_snapshot"] is snapshot
    assert "runtime_skill_snapshot" not in captured["audit"]


def test_email_dependency_builder_resolves_one_snapshot_for_agent_orchestrator(
    tmp_path, monkeypatch
):
    module = _module()
    snapshot = object()
    calls = []
    monkeypatch.setattr(
        "app.managed_skills.resolve_pending_runtime_skills",
        lambda store, *, pid: calls.append((store, pid)) or snapshot,
    )
    captured = {}
    monkeypatch.setattr(
        module,
        "_build_agent_orchestrator",
        lambda _settings, _store, *, runtime_skill_snapshot: captured.setdefault(
            "snapshot", runtime_skill_snapshot
        ) or object(),
    )
    from app.email_model_registry import EmailModelRegistry

    monkeypatch.setattr(
        EmailModelRegistry,
        "get_model",
        lambda self, _model_id: SimpleNamespace(
            metadata=SimpleNamespace(per_category_metrics={})
        ),
    )
    monkeypatch.setattr(
        module,
        "_scan_config",
        lambda *_args, **_kwargs: SimpleNamespace(
            config_version="test", thresholds={}, actions={},
            category_eligibility={}, action_parameters={}, category_enabled={},
        ),
    )
    settings = SimpleNamespace(
        db_path=tmp_path / "worker.sqlite3", workspace=tmp_path, dry_run=False
    )

    bootstrap = module.build_email_worker_dependencies(settings)
    bootstrap.build_dependencies(
        ({"account_id": "account-1", "enabled": True},),
        SimpleNamespace(
            loaded=SimpleNamespace(model_id="email-model:test", classifier=object()),
            tick=lambda: None,
        ),
    )

    assert len(calls) == 1
    assert captured["snapshot"] is snapshot


def test_worker_startup_isolates_legacy_before_agent_claim_and_starts_components(
    tmp_path,
):
    module = _module()
    events = []
    health = []
    database = tmp_path / "legacy-worker-startup.sqlite3"
    task_store = AutoReplyStore(database)
    email_store = EmailStore(database)
    legacy = task_store.ensure_reply_task(
        channel="email",
        conversation_id="legacy-worker-startup",
        conversation_title="Legacy unsubscribe",
        single_chat=False,
        trigger_message_id="legacy-worker-startup",
        trigger_create_time="2026-09-03T12:00:00+00:00",
        trigger_sender="newsletter@example.com",
        trigger_text="Legacy unsubscribe.",
        trigger_message_json=json.dumps(
            {
                "schema": "email_agent_action.v1",
                "action_type": "unsubscribe",
                "lifecycle_version": "email_unsubscribe_consumer_direct_v1",
            }
        ),
        execution_generation="legacy-generation-17",
    )
    dependencies = _dependencies(events)
    dependencies.email_store = email_store
    dependencies.task_store = task_store
    dependencies.orchestrator = SimpleNamespace(
        process=lambda *_args, **_kwargs: pytest.fail(
            "legacy task must never reach orchestrator"
        )
    )

    class FakeThread:
        def __init__(self, *, target, name, daemon):
            self.name = name
            events.append(("thread", name, daemon, target))

        def start(self):
            events.append(("started", self.name))

    dependencies.record_health = lambda scope, payload: health.append((scope, payload))

    module.run_email_worker(
        SimpleNamespace(),
        dependencies=dependencies,
        thread_factory=FakeThread,
        wait=lambda: None,
        output=StringIO(),
    )

    terminal = task_store.get_reply_task(legacy.id)
    assert terminal is not None
    assert terminal.status == "failed"
    assert (
        "component:email-legacy-lifecycle",
        {
            "status": "degraded",
            "error_code": "legacy_email_unsubscribe_lifecycle",
            "task_count": 1,
            "isolated_count": 1,
            "superseded_count": 0,
            "unresolved_count": 0,
        },
    ) in health
    assert {event[1] for event in events if event[0] == "started"} == {
        "ceo-agent-email-scan-actions",
        "ceo-agent-email-agent-consumer",
        "ceo-agent-email-training",
    }
    module.run_email_agent_task_loop(
        task_store,
        dependencies.orchestrator,
        load_task_context=lambda _task: pytest.fail("legacy context loaded"),
        finalize_task=lambda *_args: pytest.fail("legacy task finalized"),
        sleep=lambda _seconds: None,
        max_cycles=1,
    )


def test_unfenced_legacy_blocks_agent_consumer_but_starts_scan_direct_and_training(
    tmp_path,
):
    module = _module()
    events = []
    health = []
    database = tmp_path / "unfenced-legacy-worker.sqlite3"
    real_task_store = AutoReplyStore(database)
    email_store = EmailStore(database)
    legacy = real_task_store.ensure_reply_task(
        channel="email",
        conversation_id="unfenced-legacy-worker",
        conversation_title="Unfenced legacy unsubscribe",
        single_chat=False,
        trigger_message_id="unfenced-legacy-worker",
        trigger_create_time="2026-09-03T12:00:00+00:00",
        trigger_sender="newsletter@example.com",
        trigger_text="Legacy unsubscribe.",
        trigger_message_json=json.dumps(
            {
                "schema": "email_agent_action.v1",
                "action_type": "unsubscribe",
                "lifecycle_version": "email_unsubscribe_consumer_direct_v1",
            }
        ),
        execution_generation="unfenced-generation",
    )

    class UnfenceableTaskStore:
        def __getattr__(self, name):
            return getattr(real_task_store, name)

        def terminalize_legacy_email_unsubscribe_task(
            self,
            task_id,
            *,
            expected_execution_generation,
            expected_status,
        ):
            assert (task_id, expected_execution_generation, expected_status) == (
                legacy.id,
                legacy.execution_generation,
                "pending",
            )
            return False

    dependencies = _dependencies(events)
    dependencies.email_store = email_store
    dependencies.task_store = UnfenceableTaskStore()
    dependencies.orchestrator = SimpleNamespace(
        process=lambda *_args, **_kwargs: pytest.fail(
            "unfenced legacy task must never reach orchestrator"
        )
    )
    dependencies.record_health = lambda scope, payload: health.append((scope, payload))

    class FakeThread:
        def __init__(self, *, target, name, daemon):
            self.name = name
            events.append(("thread", name, daemon, target))

        def start(self):
            events.append(("started", self.name))

    module.run_email_worker(
        SimpleNamespace(),
        dependencies=dependencies,
        thread_factory=FakeThread,
        wait=lambda: None,
        output=StringIO(),
    )

    unchanged = real_task_store.get_reply_task(legacy.id)
    assert unchanged is not None
    assert unchanged.status == "pending"
    assert {event[1] for event in events if event[0] == "started"} == {
        "ceo-agent-email-scan-actions",
        "ceo-agent-email-training",
    }
    assert (
        "component:email-legacy-lifecycle",
        {
            "status": "degraded",
            "error_code": "legacy_email_unsubscribe_lifecycle",
            "task_count": 1,
            "isolated_count": 0,
            "superseded_count": 0,
            "unresolved_count": 1,
        },
    ) in health
    assert (
        "component:email-agent-consumer",
        {
            "status": "degraded",
            "error_code": "legacy_email_unsubscribe_lifecycle",
            "unresolved_count": 1,
        },
    ) in health


def test_generation_replacement_after_inventory_is_not_failed_and_blocks_agent(
    tmp_path,
):
    module = _module()
    events = []
    health = []
    database = tmp_path / "legacy-generation-race.sqlite3"
    real_task_store = AutoReplyStore(database)
    real_email_store = EmailStore(database)
    legacy = real_task_store.ensure_reply_task(
        channel="email",
        conversation_id="legacy-generation-race",
        conversation_title="Legacy generation race",
        single_chat=False,
        trigger_message_id="legacy-generation-race",
        trigger_create_time="2026-09-03T12:00:00+00:00",
        trigger_sender="newsletter@example.com",
        trigger_text="Legacy unsubscribe.",
        trigger_message_json=json.dumps(
            {
                "schema": "email_agent_action.v1",
                "action_type": "unsubscribe",
                "lifecycle_version": "email_unsubscribe_consumer_direct_v1",
            }
        ),
        execution_generation="inventoried-generation",
    )

    class RotatingInventoryStore:
        rotated = False

        def __getattr__(self, name):
            return getattr(real_email_store, name)

        def _rotate(self):
            if self.rotated:
                return
            with sqlite3.connect(database) as db:
                db.execute(
                    "update reply_tasks set execution_generation=? where id=?",
                    ("replacement-generation", legacy.id),
                )
            self.rotated = True

        def list_nonterminal_legacy_unsubscribe_task_attempts(self):
            result = (
                real_email_store.list_nonterminal_legacy_unsubscribe_task_attempts()
            )
            self._rotate()
            return result

    terminalization_generations = []

    class RecordingTaskStore:
        def __getattr__(self, name):
            return getattr(real_task_store, name)

        def terminalize_legacy_email_unsubscribe_task(
            self,
            task_id,
            *,
            expected_execution_generation,
            expected_status,
        ):
            assert expected_status == "pending"
            terminalization_generations.append(expected_execution_generation)
            return real_task_store.terminalize_legacy_email_unsubscribe_task(
                task_id,
                expected_execution_generation=expected_execution_generation,
                expected_status=expected_status,
            )

    dependencies = _dependencies(events)
    dependencies.email_store = RotatingInventoryStore()
    dependencies.task_store = RecordingTaskStore()
    dependencies.record_health = lambda scope, payload: health.append((scope, payload))

    class FakeThread:
        def __init__(self, *, target, name, daemon):
            del target, daemon
            self.name = name

        def start(self):
            events.append(("started", self.name))

    module.run_email_worker(
        SimpleNamespace(),
        dependencies=dependencies,
        thread_factory=FakeThread,
        wait=lambda: None,
        output=StringIO(),
    )

    replacement = real_task_store.get_reply_task(legacy.id)
    assert replacement is not None
    assert replacement.execution_generation == "replacement-generation"
    assert replacement.status == "pending"
    assert terminalization_generations == ["inventoried-generation"]
    assert {event[1] for event in events if event[0] == "started"} == {
        "ceo-agent-email-scan-actions",
        "ceo-agent-email-training",
    }
    assert (
        "component:email-legacy-lifecycle",
        {
            "status": "degraded",
            "error_code": "legacy_email_unsubscribe_lifecycle",
            "task_count": 1,
            "isolated_count": 0,
            "superseded_count": 0,
            "unresolved_count": 1,
        },
    ) in health


def test_processing_replacement_after_pending_inventory_is_unresolved_and_blocks_agent(
    tmp_path,
):
    module = _module()
    events = []
    database = tmp_path / "legacy-status-race.sqlite3"
    real_task_store = AutoReplyStore(database)
    real_email_store = EmailStore(database)
    legacy = real_task_store.ensure_reply_task(
        channel="email",
        conversation_id="legacy-status-race",
        conversation_title="Legacy status race",
        single_chat=False,
        trigger_message_id="legacy-status-race",
        trigger_create_time="2026-09-03T12:00:00+00:00",
        trigger_sender="newsletter@example.com",
        trigger_text="Legacy unsubscribe.",
        trigger_message_json=json.dumps(
            {
                "schema": "email_agent_action.v1",
                "action_type": "unsubscribe",
                "lifecycle_version": "email_unsubscribe_consumer_direct_v1",
            }
        ),
        execution_generation="status-race-generation",
    )

    class AdvancingInventoryStore:
        advanced = False

        def __getattr__(self, name):
            return getattr(real_email_store, name)

        def list_nonterminal_legacy_unsubscribe_task_attempts(self):
            attempts = (
                real_email_store.list_nonterminal_legacy_unsubscribe_task_attempts()
            )
            if not self.advanced:
                claimed = real_task_store.claim_reply_task(legacy.id)
                assert claimed is not None
                assert claimed.status == "processing"
                self.advanced = True
            return attempts

    terminalization_bindings = []

    class RecordingTaskStore:
        def __getattr__(self, name):
            return getattr(real_task_store, name)

        def terminalize_legacy_email_unsubscribe_task(
            self,
            task_id,
            *,
            expected_execution_generation,
            expected_status,
        ):
            terminalization_bindings.append(
                (task_id, expected_execution_generation, expected_status)
            )
            return real_task_store.terminalize_legacy_email_unsubscribe_task(
                task_id,
                expected_execution_generation=expected_execution_generation,
                expected_status=expected_status,
            )

    dependencies = _dependencies(events)
    dependencies.email_store = AdvancingInventoryStore()
    dependencies.task_store = RecordingTaskStore()

    class FakeThread:
        def __init__(self, *, target, name, daemon):
            del target, daemon
            self.name = name

        def start(self):
            events.append(("started", self.name))

    module.run_email_worker(
        SimpleNamespace(),
        dependencies=dependencies,
        thread_factory=FakeThread,
        wait=lambda: None,
        output=StringIO(),
    )

    replacement = real_task_store.get_reply_task(legacy.id)
    assert replacement is not None
    assert replacement.execution_generation == legacy.execution_generation
    assert replacement.status == "processing"
    assert terminalization_bindings == [
        (legacy.id, legacy.execution_generation, "pending")
    ]
    assert {event[1] for event in events if event[0] == "started"} == {
        "ceo-agent-email-scan-actions",
        "ceo-agent-email-training",
    }


@pytest.mark.parametrize("first_terminalization", ("cas-false", "exception"))
def test_final_dependency_reconciliation_clears_bootstrap_unresolved_after_isolation(
    tmp_path,
    first_terminalization,
):
    module = _module()
    database = tmp_path / f"two-stage-{first_terminalization}.sqlite3"
    real_task_store = AutoReplyStore(database)
    email_store = EmailStore(database)
    legacy = real_task_store.ensure_reply_task(
        channel="email",
        conversation_id=f"two-stage-{first_terminalization}",
        conversation_title="Two-stage legacy reconciliation",
        single_chat=False,
        trigger_message_id=f"two-stage-{first_terminalization}",
        trigger_create_time="2026-09-03T12:00:00+00:00",
        trigger_sender="newsletter@example.com",
        trigger_text="Legacy unsubscribe.",
        trigger_message_json=json.dumps(
            {
                "schema": "email_agent_action.v1",
                "action_type": "unsubscribe",
                "lifecycle_version": "email_unsubscribe_consumer_direct_v1",
            }
        ),
        execution_generation="two-stage-generation",
    )
    terminalization_bindings = []

    class FirstPassTaskStore:
        def __getattr__(self, name):
            return getattr(real_task_store, name)

        def terminalize_legacy_email_unsubscribe_task(
            self,
            task_id,
            *,
            expected_execution_generation,
            expected_status,
        ):
            terminalization_bindings.append(
                (task_id, expected_execution_generation, expected_status)
            )
            if first_terminalization == "exception":
                raise RuntimeError("simulated bootstrap CAS failure")
            return False

    events = []
    health = []
    accounts = ({"account_id": "account-1", "enabled": True},)
    active_model = object()
    ready_dependencies = _dependencies(events)
    ready_dependencies.email_store = email_store
    ready_dependencies.task_store = real_task_store
    ready_dependencies.orchestrator = SimpleNamespace(
        process=lambda *_args, **_kwargs: pytest.fail(
            "isolated legacy task must never reach orchestrator"
        )
    )
    ready_dependencies.record_health = lambda scope, payload: health.append(
        (scope, payload)
    )
    bootstrap = SimpleNamespace(
        email_store=email_store,
        task_store=FirstPassTaskStore(),
        record_health=lambda scope, payload: health.append((scope, payload)),
        load_enabled_accounts=lambda: accounts,
        load_active_model=lambda: active_model,
        build_dependencies=lambda loaded_accounts, loaded_model: (
            ready_dependencies
            if (loaded_accounts, loaded_model) == (accounts, active_model)
            else pytest.fail("unexpected dependency build input")
        ),
    )

    class FakeThread:
        def __init__(self, *, target, name, daemon):
            del target, daemon
            self.name = name

        def start(self):
            events.append(("started", self.name))

    module.run_email_worker(
        SimpleNamespace(),
        dependencies=bootstrap,
        thread_factory=FakeThread,
        wait=lambda: None,
        output=StringIO(),
    )

    terminal = real_task_store.get_reply_task(legacy.id)
    assert terminal is not None
    assert terminal.status == "failed"
    assert terminal.error == "legacy_email_unsubscribe_lifecycle"
    assert terminalization_bindings == [
        (legacy.id, legacy.execution_generation, "pending")
    ]
    assert {event[1] for event in events if event[0] == "started"} == {
        "ceo-agent-email-scan-actions",
        "ceo-agent-email-agent-consumer",
        "ceo-agent-email-training",
    }
    assert not any(
        scope == "component:email-agent-consumer" for scope, _payload in health
    )
    module.run_email_agent_task_loop(
        real_task_store,
        ready_dependencies.orchestrator,
        load_task_context=lambda _task: pytest.fail("legacy context loaded"),
        finalize_task=lambda *_args: pytest.fail("legacy task finalized"),
        sleep=lambda _seconds: None,
        max_cycles=1,
    )


@pytest.mark.parametrize(
    "final_read_failure",
    ("missing-inventory", "inventory-exception", "recheck-exception"),
)
def test_final_dependencies_without_authoritative_read_preserve_bootstrap_unresolved(
    tmp_path,
    final_read_failure,
):
    module = _module()
    database = tmp_path / "two-stage-inventory-unavailable.sqlite3"
    real_task_store = AutoReplyStore(database)
    email_store = EmailStore(database)
    legacy = real_task_store.ensure_reply_task(
        channel="email",
        conversation_id="two-stage-inventory-unavailable",
        conversation_title="Two-stage unavailable inventory",
        single_chat=False,
        trigger_message_id="two-stage-inventory-unavailable",
        trigger_create_time="2026-09-03T12:00:00+00:00",
        trigger_sender="newsletter@example.com",
        trigger_text="Legacy unsubscribe.",
        trigger_message_json=json.dumps(
            {
                "schema": "email_agent_action.v1",
                "action_type": "unsubscribe",
                "lifecycle_version": "email_unsubscribe_consumer_direct_v1",
            }
        ),
        execution_generation="inventory-unavailable-generation",
    )

    class UnfenceableTaskStore:
        def __getattr__(self, name):
            return getattr(real_task_store, name)

        def terminalize_legacy_email_unsubscribe_task(self, *_args, **_kwargs):
            return False

    class InventoryFailureStore:
        def list_nonterminal_legacy_unsubscribe_task_attempts(self):
            raise RuntimeError("simulated final inventory failure")

    class RecheckFailureStore:
        def list_nonterminal_legacy_unsubscribe_task_attempts(self):
            return email_store.list_nonterminal_legacy_unsubscribe_task_attempts()

        def get_nonterminal_legacy_unsubscribe_task_attempt(self, _task_id):
            raise RuntimeError("simulated final recheck failure")

    events = []
    health = []
    accounts = ({"account_id": "account-1", "enabled": True},)
    active_model = object()
    ready_dependencies = _dependencies(events)
    ready_dependencies.email_store = {
        "missing-inventory": SimpleNamespace(),
        "inventory-exception": InventoryFailureStore(),
        "recheck-exception": RecheckFailureStore(),
    }[final_read_failure]
    ready_dependencies.task_store = (
        UnfenceableTaskStore()
        if final_read_failure == "recheck-exception"
        else real_task_store
    )
    ready_dependencies.record_health = lambda scope, payload: health.append(
        (scope, payload)
    )
    bootstrap = SimpleNamespace(
        email_store=email_store,
        task_store=UnfenceableTaskStore(),
        record_health=lambda scope, payload: health.append((scope, payload)),
        load_enabled_accounts=lambda: accounts,
        load_active_model=lambda: active_model,
        build_dependencies=lambda _accounts, _model: ready_dependencies,
    )

    class FakeThread:
        def __init__(self, *, target, name, daemon):
            del target, daemon
            self.name = name

        def start(self):
            events.append(("started", self.name))

    module.run_email_worker(
        SimpleNamespace(),
        dependencies=bootstrap,
        thread_factory=FakeThread,
        wait=lambda: None,
        output=StringIO(),
    )

    current = real_task_store.get_reply_task(legacy.id)
    assert current is not None
    assert current.status == "pending"
    assert {event[1] for event in events if event[0] == "started"} == {
        "ceo-agent-email-scan-actions",
        "ceo-agent-email-training",
    }
    assert (
        "component:email-agent-consumer",
        {
            "status": "degraded",
            "error_code": "legacy_email_unsubscribe_inventory_unavailable",
        },
    ) in health


@pytest.mark.parametrize(
    "scenario",
    (
        "pending-bootstrap-and-final-error",
        "bootstrap-isolates-then-final-error",
        "insert-after-empty-bootstrap-then-final-error",
        "empty-bootstrap-and-final-error",
    ),
)
def test_final_unknown_legacy_inventory_blocks_only_agent_consumer(
    tmp_path,
    scenario,
):
    module = _module()
    database = tmp_path / f"final-unknown-{scenario}.sqlite3"
    task_store = AutoReplyStore(database)
    email_store = EmailStore(database)
    accounts = ({"account_id": "account-1", "enabled": True},)
    active_model = object()
    events = []
    health = []
    created_tasks = []

    def create_legacy_task(suffix):
        task = task_store.ensure_reply_task(
            channel="email",
            conversation_id=f"final-unknown-{scenario}-{suffix}",
            conversation_title="Final unknown legacy inventory",
            single_chat=False,
            trigger_message_id=f"final-unknown-{scenario}-{suffix}",
            trigger_create_time="2026-09-03T12:00:00+00:00",
            trigger_sender="newsletter@example.com",
            trigger_text="Legacy unsubscribe.",
            trigger_message_json=json.dumps(
                {
                    "schema": "email_agent_action.v1",
                    "action_type": "unsubscribe",
                    "lifecycle_version": "email_unsubscribe_consumer_direct_v1",
                }
            ),
            execution_generation=f"final-unknown-{scenario}-{suffix}",
        )
        created_tasks.append(task)
        return task

    if scenario in {
        "pending-bootstrap-and-final-error",
        "bootstrap-isolates-then-final-error",
    }:
        create_legacy_task("initial")

    class InventoryUnavailableStore:
        def __getattr__(self, name):
            return getattr(email_store, name)

        def list_nonterminal_legacy_unsubscribe_task_attempts(self):
            raise RuntimeError("simulated legacy inventory failure")

    ready_dependencies = _dependencies(events)
    ready_dependencies.email_store = InventoryUnavailableStore()
    ready_dependencies.task_store = task_store
    ready_dependencies.orchestrator = SimpleNamespace(
        process=lambda *_args, **_kwargs: pytest.fail(
            "unknown legacy inventory must never reach orchestrator"
        )
    )
    ready_dependencies.record_health = lambda scope, payload: health.append(
        (scope, payload)
    )

    def build_dependencies(loaded_accounts, loaded_model):
        assert (loaded_accounts, loaded_model) == (accounts, active_model)
        if scenario == "insert-after-empty-bootstrap-then-final-error":
            create_legacy_task("inserted")
        return ready_dependencies

    bootstrap = SimpleNamespace(
        email_store=(
            InventoryUnavailableStore()
            if scenario
            in {
                "pending-bootstrap-and-final-error",
                "empty-bootstrap-and-final-error",
            }
            else email_store
        ),
        task_store=task_store,
        record_health=lambda scope, payload: health.append((scope, payload)),
        load_enabled_accounts=lambda: accounts,
        load_active_model=lambda: active_model,
        build_dependencies=build_dependencies,
    )

    class FakeThread:
        def __init__(self, *, target, name, daemon):
            del target, daemon
            self.name = name

        def start(self):
            events.append(("started", self.name))

    module.run_email_worker(
        SimpleNamespace(),
        dependencies=bootstrap,
        thread_factory=FakeThread,
        wait=lambda: None,
        output=StringIO(),
    )

    assert {event[1] for event in events if event[0] == "started"} == {
        "ceo-agent-email-scan-actions",
        "ceo-agent-email-training",
    }
    assert (
        "component:email-legacy-lifecycle",
        {
            "status": "degraded",
            "error_code": "legacy_email_unsubscribe_inventory_unavailable",
        },
    ) in health
    assert (
        "component:email-agent-consumer",
        {
            "status": "degraded",
            "error_code": "legacy_email_unsubscribe_inventory_unavailable",
        },
    ) in health
    if scenario == "bootstrap-isolates-then-final-error":
        assert task_store.get_reply_task(created_tasks[0].id).status == "failed"
    else:
        assert all(
            task_store.get_reply_task(task.id).status == "pending"
            for task in created_tasks
        )


@pytest.mark.parametrize("final_state", ("isolated", "empty"))
def test_final_authoritative_reconciliation_allows_agent_after_bootstrap_unknown(
    tmp_path,
    final_state,
):
    module = _module()
    database = tmp_path / f"final-authoritative-{final_state}.sqlite3"
    task_store = AutoReplyStore(database)
    email_store = EmailStore(database)
    accounts = ({"account_id": "account-1", "enabled": True},)
    active_model = object()
    events = []
    health = []
    legacy = None
    if final_state == "isolated":
        legacy = task_store.ensure_reply_task(
            channel="email",
            conversation_id="final-authoritative-isolated",
            conversation_title="Final authoritative isolation",
            single_chat=False,
            trigger_message_id="final-authoritative-isolated",
            trigger_create_time="2026-09-03T12:00:00+00:00",
            trigger_sender="newsletter@example.com",
            trigger_text="Legacy unsubscribe.",
            trigger_message_json=json.dumps(
                {
                    "schema": "email_agent_action.v1",
                    "action_type": "unsubscribe",
                    "lifecycle_version": "email_unsubscribe_consumer_direct_v1",
                }
            ),
            execution_generation="final-authoritative-isolated",
        )

    class InventoryUnavailableStore:
        def list_nonterminal_legacy_unsubscribe_task_attempts(self):
            raise RuntimeError("simulated bootstrap inventory failure")

    ready_dependencies = _dependencies(events)
    ready_dependencies.email_store = email_store
    ready_dependencies.task_store = task_store
    ready_dependencies.record_health = lambda scope, payload: health.append(
        (scope, payload)
    )
    bootstrap = SimpleNamespace(
        email_store=InventoryUnavailableStore(),
        task_store=task_store,
        record_health=lambda scope, payload: health.append((scope, payload)),
        load_enabled_accounts=lambda: accounts,
        load_active_model=lambda: active_model,
        build_dependencies=lambda loaded_accounts, loaded_model: (
            ready_dependencies
            if (loaded_accounts, loaded_model) == (accounts, active_model)
            else pytest.fail("unexpected dependency build input")
        ),
    )

    class FakeThread:
        def __init__(self, *, target, name, daemon):
            del target, daemon
            self.name = name

        def start(self):
            events.append(("started", self.name))

    module.run_email_worker(
        SimpleNamespace(),
        dependencies=bootstrap,
        thread_factory=FakeThread,
        wait=lambda: None,
        output=StringIO(),
    )

    assert {event[1] for event in events if event[0] == "started"} == {
        "ceo-agent-email-scan-actions",
        "ceo-agent-email-agent-consumer",
        "ceo-agent-email-training",
    }
    assert not any(
        scope == "component:email-agent-consumer" for scope, _payload in health
    )
    lifecycle_health = [
        payload
        for scope, payload in health
        if scope == "component:email-legacy-lifecycle"
    ]
    if final_state == "empty":
        assert lifecycle_health[-1] == {
            "status": "ready",
            "task_count": 0,
            "unresolved_count": 0,
        }
    else:
        assert lifecycle_health[-1] == {
            "status": "degraded",
            "error_code": "legacy_email_unsubscribe_lifecycle",
            "task_count": 1,
            "isolated_count": 1,
            "superseded_count": 0,
            "unresolved_count": 0,
        }
    if legacy is not None:
        terminal = task_store.get_reply_task(legacy.id)
        assert terminal is not None
        assert terminal.status == "failed"
        assert terminal.error == "legacy_email_unsubscribe_lifecycle"


def test_final_authoritative_empty_overwrites_bootstrap_unknown_health(
    tmp_path,
):
    module = _module()
    database = tmp_path / "final-empty-overwrites-bootstrap-unknown.sqlite3"
    task_store = AutoReplyStore(database)
    email_store = EmailStore(database)
    record_health = _persist_email_worker_health(task_store)
    accounts = ({"account_id": "account-1", "enabled": True},)
    active_model = object()
    events = []

    class InventoryUnavailableStore:
        def list_nonterminal_legacy_unsubscribe_task_attempts(self):
            raise RuntimeError("simulated bootstrap inventory failure")

    ready_dependencies = _dependencies(events)
    ready_dependencies.email_store = email_store
    ready_dependencies.task_store = task_store
    ready_dependencies.record_health = record_health
    bootstrap = SimpleNamespace(
        email_store=InventoryUnavailableStore(),
        task_store=task_store,
        record_health=record_health,
        load_enabled_accounts=lambda: accounts,
        load_active_model=lambda: active_model,
        build_dependencies=lambda loaded_accounts, loaded_model: (
            ready_dependencies
            if (loaded_accounts, loaded_model) == (accounts, active_model)
            else pytest.fail("unexpected dependency build input")
        ),
    )

    class FakeThread:
        def __init__(self, *, target, name, daemon):
            del target, daemon
            self.name = name

        def start(self):
            events.append(("started", self.name))

    module.run_email_worker(
        SimpleNamespace(),
        dependencies=bootstrap,
        thread_factory=FakeThread,
        wait=lambda: None,
        output=StringIO(),
    )

    assert {event[1] for event in events if event[0] == "started"} == {
        "ceo-agent-email-scan-actions",
        "ceo-agent-email-agent-consumer",
        "ceo-agent-email-training",
    }
    persisted = task_store.get_service_state(
        "email_worker_health:component:email-legacy-lifecycle"
    )
    assert persisted is not None
    assert json.loads(persisted) == {
        "status": "ready",
        "task_count": 0,
        "unresolved_count": 0,
    }


def test_authoritative_empty_overwrites_previous_process_degraded_health(
    tmp_path,
):
    module = _module()
    database = tmp_path / "authoritative-empty-overwrites-stale.sqlite3"
    task_store = AutoReplyStore(database)
    email_store = EmailStore(database)
    health_key = "email_worker_health:component:email-legacy-lifecycle"
    task_store.set_service_state(
        health_key,
        json.dumps(
            {
                "status": "degraded",
                "error_code": "legacy_email_unsubscribe_inventory_unavailable",
            }
        ),
    )
    events = []
    dependencies = _dependencies(events)
    dependencies.email_store = email_store
    dependencies.task_store = task_store
    dependencies.record_health = _persist_email_worker_health(task_store)

    class FakeThread:
        def __init__(self, *, target, name, daemon):
            del target, daemon
            self.name = name

        def start(self):
            events.append(("started", self.name))

    module.run_email_worker(
        SimpleNamespace(),
        dependencies=dependencies,
        thread_factory=FakeThread,
        wait=lambda: None,
        output=StringIO(),
    )

    assert {event[1] for event in events if event[0] == "started"} == {
        "ceo-agent-email-scan-actions",
        "ceo-agent-email-agent-consumer",
        "ceo-agent-email-training",
    }
    persisted = task_store.get_service_state(health_key)
    assert persisted is not None
    assert json.loads(persisted) == {
        "status": "ready",
        "task_count": 0,
        "unresolved_count": 0,
    }


def test_final_empty_inventory_clears_bootstrap_unresolved_after_safe_replacement(
    tmp_path,
):
    module = _module()
    database = tmp_path / "two-stage-safe-replacement.sqlite3"
    real_task_store = AutoReplyStore(database)
    email_store = EmailStore(database)
    legacy = real_task_store.ensure_reply_task(
        channel="email",
        conversation_id="two-stage-safe-replacement",
        conversation_title="Two-stage safe replacement",
        single_chat=False,
        trigger_message_id="two-stage-safe-replacement",
        trigger_create_time="2026-09-03T12:00:00+00:00",
        trigger_sender="newsletter@example.com",
        trigger_text="Legacy unsubscribe.",
        trigger_message_json=json.dumps(
            {
                "schema": "email_agent_action.v1",
                "action_type": "unsubscribe",
                "lifecycle_version": "email_unsubscribe_consumer_direct_v1",
            }
        ),
        execution_generation="safe-replacement-generation",
    )

    class FirstPassTaskStore:
        def __getattr__(self, name):
            return getattr(real_task_store, name)

        def terminalize_legacy_email_unsubscribe_task(self, *_args, **_kwargs):
            return False

    events = []
    health = []
    accounts = ({"account_id": "account-1", "enabled": True},)
    active_model = object()
    ready_dependencies = _dependencies(events)
    ready_dependencies.email_store = email_store
    ready_dependencies.task_store = real_task_store
    ready_dependencies.record_health = lambda scope, payload: health.append(
        (scope, payload)
    )

    def build_dependencies(_accounts, _model):
        payload = json.loads(legacy.trigger_message_json)
        payload["lifecycle_version"] = "email_unsubscribe_audited_v2"
        with sqlite3.connect(database) as db:
            db.execute(
                "update reply_tasks set trigger_message_json=? where id=?",
                (json.dumps(payload), legacy.id),
            )
        return ready_dependencies

    bootstrap = SimpleNamespace(
        email_store=email_store,
        task_store=FirstPassTaskStore(),
        record_health=lambda scope, payload: health.append((scope, payload)),
        load_enabled_accounts=lambda: accounts,
        load_active_model=lambda: active_model,
        build_dependencies=build_dependencies,
    )

    class FakeThread:
        def __init__(self, *, target, name, daemon):
            del target, daemon
            self.name = name

        def start(self):
            events.append(("started", self.name))

    module.run_email_worker(
        SimpleNamespace(),
        dependencies=bootstrap,
        thread_factory=FakeThread,
        wait=lambda: None,
        output=StringIO(),
    )

    replacement = real_task_store.get_reply_task(legacy.id)
    assert replacement is not None
    assert replacement.status == "pending"
    assert (
        json.loads(replacement.trigger_message_json)["lifecycle_version"]
        == "email_unsubscribe_audited_v2"
    )
    assert {event[1] for event in events if event[0] == "started"} == {
        "ceo-agent-email-scan-actions",
        "ceo-agent-email-agent-consumer",
        "ceo-agent-email-training",
    }
    assert not any(
        scope == "component:email-agent-consumer" for scope, _payload in health
    )


@pytest.mark.parametrize(
    "replacement",
    ("terminal", "audited-v2", "non-email", "malformed", "missing"),
)
@pytest.mark.parametrize("terminalization", ("cas-false", "exception"))
def test_safe_replacement_after_inventory_is_unchanged_and_does_not_block_agent(
    tmp_path,
    replacement,
    terminalization,
):
    module = _module()
    events = []
    health = []
    database = tmp_path / f"legacy-{replacement}-race.sqlite3"
    task_store = AutoReplyStore(database)
    real_email_store = EmailStore(database)
    legacy = task_store.ensure_reply_task(
        channel="email",
        conversation_id=f"legacy-{replacement}-race",
        conversation_title="Legacy replacement race",
        single_chat=False,
        trigger_message_id=f"legacy-{replacement}-race",
        trigger_create_time="2026-09-03T12:00:00+00:00",
        trigger_sender="newsletter@example.com",
        trigger_text="Legacy unsubscribe.",
        trigger_message_json=json.dumps(
            {
                "schema": "email_agent_action.v1",
                "action_type": "unsubscribe",
                "lifecycle_version": "email_unsubscribe_consumer_direct_v1",
            }
        ),
        execution_generation="inventoried-generation",
    )

    class ReplacingInventoryStore:
        replaced = False

        def __getattr__(self, name):
            return getattr(real_email_store, name)

        def _replace(self):
            if self.replaced:
                return
            with sqlite3.connect(database) as db:
                if replacement == "terminal":
                    db.execute(
                        "update reply_tasks set status='done' where id=?",
                        (legacy.id,),
                    )
                elif replacement == "audited-v2":
                    payload = json.loads(legacy.trigger_message_json)
                    payload["lifecycle_version"] = "email_unsubscribe_audited_v2"
                    db.execute(
                        "update reply_tasks set trigger_message_json=? where id=?",
                        (json.dumps(payload), legacy.id),
                    )
                elif replacement == "non-email":
                    db.execute(
                        "update reply_tasks set channel='dingtalk' where id=?",
                        (legacy.id,),
                    )
                elif replacement == "malformed":
                    db.execute(
                        "update reply_tasks set trigger_message_json='{malformed' "
                        "where id=?",
                        (legacy.id,),
                    )
                else:
                    db.execute("delete from reply_tasks where id=?", (legacy.id,))
            self.replaced = True

        def list_nonterminal_legacy_unsubscribe_task_attempts(self):
            result = (
                real_email_store.list_nonterminal_legacy_unsubscribe_task_attempts()
            )
            self._replace()
            return result

    class RaisingTaskStore:
        def __getattr__(self, name):
            return getattr(task_store, name)

        def terminalize_legacy_email_unsubscribe_task(self, *_args, **_kwargs):
            raise RuntimeError("simulated terminalization failure")

    dependencies = _dependencies(events)
    dependencies.email_store = ReplacingInventoryStore()
    dependencies.task_store = (
        RaisingTaskStore() if terminalization == "exception" else task_store
    )
    dependencies.record_health = lambda scope, payload: health.append((scope, payload))

    class FakeThread:
        def __init__(self, *, target, name, daemon):
            del target, daemon
            self.name = name

        def start(self):
            events.append(("started", self.name))

    module.run_email_worker(
        SimpleNamespace(),
        dependencies=dependencies,
        thread_factory=FakeThread,
        wait=lambda: None,
        output=StringIO(),
    )

    current = task_store.get_reply_task(legacy.id)
    if replacement == "missing":
        assert current is None
    else:
        assert current is not None
        assert current.execution_generation == legacy.execution_generation
    if replacement == "terminal" and current is not None:
        assert current.status == "done"
    elif replacement == "audited-v2" and current is not None:
        assert current.status == "pending"
        assert (
            json.loads(current.trigger_message_json)["lifecycle_version"]
            == "email_unsubscribe_audited_v2"
        )
    elif replacement == "non-email" and current is not None:
        assert current.channel == "dingtalk"
        assert current.status == "pending"
    elif replacement == "malformed" and current is not None:
        assert current.trigger_message_json == "{malformed"
        assert current.status == "pending"
    assert {event[1] for event in events if event[0] == "started"} == {
        "ceo-agent-email-scan-actions",
        "ceo-agent-email-agent-consumer",
        "ceo-agent-email-training",
    }
    assert (
        "component:email-legacy-lifecycle",
        {
            "status": "degraded",
            "error_code": "legacy_email_unsubscribe_lifecycle",
            "task_count": 1,
            "isolated_count": 0,
            "superseded_count": 1,
            "unresolved_count": 0,
        },
    ) in health
    assert not any(
        scope == "component:email-agent-consumer" for scope, _payload in health
    )


def test_direct_actions_are_not_claimed_without_a_provider_executor_factory():
    module = _module()

    class Store:
        def claim_next_direct_action(self, **_kwargs):
            pytest.fail("an unavailable provider must not consume a durable claim")

    assert module._run_next_direct_action(Store(), None) is None


def test_direct_action_executor_result_completes_the_exact_claim():
    module = _module()
    action = SimpleNamespace(account_id="account-1")
    completed = []
    result = SimpleNamespace(
        status="done",
        provider_operation="STORE \\Seen",
        provider_target="account-1:message-id:<message@example.com>",
        provider_result_id="revision-2",
        error="",
    )

    class Store:
        def claim_next_direct_action(self, *, claimed_at):
            assert claimed_at
            return action

        def complete_direct_action_attempt(self, claimed, **values):
            completed.append((claimed, values))

    class Executor:
        def execute(self, claimed):
            assert claimed is action
            return result

    observed = module._run_next_direct_action(
        Store(),
        lambda account_id: Executor() if account_id == "account-1" else None,
    )

    assert observed is result
    assert completed[0][0] is action
    assert completed[0][1]["status"] == "done"
    assert completed[0][1]["provider_result_id"] == "revision-2"
    assert completed[0][1]["finished_at"]


def test_direct_action_completion_forwards_provider_locator_update():
    module = _module()
    original = SimpleNamespace(
        stable_message_identity="account-1:message-id:<message@example.com>"
    )
    updated = SimpleNamespace(folder="Archive", uidvalidity=84, uid=19)
    action = SimpleNamespace(account_id="account-1", locator=original)
    completed = []
    result = SimpleNamespace(
        status="done",
        provider_operation="MOVE ARCHIVE",
        provider_target=original.stable_message_identity,
        provider_result_id="revision-archive",
        error="",
        updated_locator=updated,
    )

    class Store:
        def claim_next_direct_action(self, *, claimed_at):
            assert claimed_at
            return action

        def complete_direct_action_attempt(self, claimed, **values):
            completed.append((claimed, values))

    module._run_next_direct_action(Store(), lambda _account_id: SimpleNamespace(execute=lambda _action: result))

    assert completed[0][1]["updated_locator"] is updated


def test_production_direct_action_factory_uses_each_accounts_imap_secret_only(
    monkeypatch,
):
    module = _module()
    calls = []
    accounts = {
        "account-a": {
            "account_id": "account-a",
            "enabled": True,
            "imap_tls": True,
            "imap_host": "imap-a.example.com",
            "imap_port": 993,
            "imap_username": "a@example.com",
            "imap_secret_reference": "CEO_EMAIL_A_IMAP_SECRET",
            "smtp_secret_reference": "CEO_EMAIL_A_SMTP_SECRET",
        },
        "account-b": {
            "account_id": "account-b",
            "enabled": True,
            "imap_tls": True,
            "imap_host": "imap-b.example.com",
            "imap_port": 1993,
            "imap_username": "b@example.com",
            "imap_secret_reference": "CEO_EMAIL_B_IMAP_SECRET",
            "smtp_secret_reference": "CEO_EMAIL_B_SMTP_SECRET",
        },
    }

    class Store:
        def get_account(self, account_id):
            return accounts.get(account_id)

    monkeypatch.setenv("CEO_EMAIL_A_IMAP_SECRET", "imap-secret-a")
    monkeypatch.setenv("CEO_EMAIL_B_IMAP_SECRET", "imap-secret-b")
    monkeypatch.setenv("CEO_EMAIL_A_SMTP_SECRET", "must-not-be-read-a")
    monkeypatch.setenv("CEO_EMAIL_B_SMTP_SECRET", "must-not-be-read-b")
    monkeypatch.setattr(
        "app.email_provider_actions.ImapDeterministicProvider.connect",
        lambda host, username, password, **kwargs: calls.append(
            (host, username, password, kwargs)
        )
        or SimpleNamespace(),
    )

    factory = module._build_imap_direct_action_executor_factory(Store())
    executor_a = factory("account-a")
    executor_b = factory("account-b")
    readback_provider_a = executor_a._readback_provider_factory()

    assert executor_a.provider is not executor_b.provider
    assert readback_provider_a is not executor_a.provider
    assert calls == [
        (
            "imap-a.example.com",
            "a@example.com",
            "imap-secret-a",
            {"port": 993, "account_id": "account-a"},
        ),
        (
            "imap-b.example.com",
            "b@example.com",
            "imap-secret-b",
            {"port": 1993, "account_id": "account-b"},
        ),
        (
            "imap-a.example.com",
            "a@example.com",
            "imap-secret-a",
            {"port": 993, "account_id": "account-a"},
        ),
    ]
    assert all("must-not-be-read" not in repr(call) for call in calls)


def _seed_direct_move_action(database: Path):
    email_store = EmailStore(database)
    task_store = AutoReplyStore(database)
    plan = build_versioned_email_action_plan(
        action_plan_version=1,
        classification_id=901,
        account_id="account-direct",
        category=EmailCategory.WORK,
        classification_source="user",
        confidence=1.0,
        model_id="email-model:direct-action-test",
        config_version="email-config:direct-action-test",
        actions=(EmailAction.MOVE,),
        action_parameters={EmailAction.MOVE: {"target_folder": "Archive"}},
        created_at=datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc),
    )
    email_store.create_account(
        {
            "account_id": "account-direct",
            "display_name": "Direct action fixture",
            "email_address": "direct@example.com",
            "imap_host": "imap.invalid",
            "imap_port": 993,
            "imap_tls": True,
            "imap_username": "direct@example.com",
            "imap_secret_reference": "keychain://unused-direct-imap",
            "smtp_host": "smtp.invalid",
            "smtp_port": 465,
            "smtp_tls": True,
            "smtp_username": "direct@example.com",
            "smtp_secret_reference": "keychain://unused-direct-smtp",
            "enabled": True,
            "scan_folders": ["INBOX"],
            "scan_interval_seconds": 60,
        }
    )
    email_store.upsert_classification(
        EmailClassification(
            classification_id=901,
            stable_message_identity=(
                "account-direct:message-id:<direct-901@example.com>"
            ),
            provider_locator=EmailProviderLocator(
                account_id="account-direct",
                folder="INBOX",
                uidvalidity=9,
                uid=901,
                rfc_message_id="<direct-901@example.com>",
                thread_id="thread-direct-901",
            ),
            category=EmailCategory.WORK,
            confidence=1.0,
            margin=1.0,
            probabilities={EmailCategory.WORK: 1.0},
            model_id=plan.model_id,
            config_version=plan.config_version,
            status=EmailClassificationStatus.PROCESSED,
            classification_source="user",
            action_plan=plan,
        ),
        sender="sender@example.com",
        subject="Direct action fixture",
        model_text="__subject__direct action fixture",
        received_at="2026-09-03T08:00:00+00:00",
    )

    assert EmailActionTaskProducer(task_store, email_store).produce(plan, {}) == ()
    return email_store, task_store


def _completed_move_result():
    return SimpleNamespace(
        status="done",
        provider_operation="MOVE",
        provider_target="account-direct:message-id:<direct-901@example.com>",
        provider_result_id="provider-revision:901",
        error="",
        updated_locator=import_module("app.email_store").StoredEmailLocator(
            account_id="account-direct",
            folder="Archive",
            uidvalidity=10,
            uid=41,
            rfc_message_id="<direct-901@example.com>",
            thread_id="thread-direct-901",
            stable_message_identity=(
                "account-direct:message-id:<direct-901@example.com>"
            ),
        ),
    )


def test_direct_action_executes_without_email_task_consumer_or_audit_run(tmp_path):
    module = _module()
    database = tmp_path / "direct-action-no-agent.sqlite3"
    email_store, task_store = _seed_direct_move_action(database)

    result = _completed_move_result()

    class Executor:
        def execute(self, action):
            assert action.action_type is EmailAction.MOVE
            return result

    assert (
        module._run_next_direct_action(
            email_store,
            lambda account_id: (
                Executor() if account_id == "account-direct" else None
            ),
            available_account_ids=("account-direct",),
        )
        is result
    )
    assert task_store.list_reply_tasks(channel="email") == []
    with sqlite3.connect(database) as db:
        assert db.execute("select count(*) from agent_runs").fetchone()[0] == 0
        assert db.execute(
            "select folder, uidvalidity, uid from email_classifications where id=901"
        ).fetchone() == ("Archive", 10, 41)
        assert db.execute(
            """
            select folder, uidvalidity, uid from email_messages
            where stable_message_identity=?
            """,
            ("account-direct:message-id:<direct-901@example.com>",),
        ).fetchone() == ("Archive", 10, 41)


@pytest.mark.parametrize(
    "scanner_raced_table",
    ("email_classifications", "email_messages"),
)
def test_direct_action_locator_completion_rolls_back_on_scanner_race(
    tmp_path,
    scanner_raced_table: str,
) -> None:
    module = _module()
    store_module = import_module("app.email_store")
    database = tmp_path / f"direct-action-{scanner_raced_table}-race.sqlite3"
    email_store, _task_store = _seed_direct_move_action(database)
    result = _completed_move_result()

    class Executor:
        def execute(self, action):
            with sqlite3.connect(database) as db:
                if scanner_raced_table == "email_classifications":
                    db.execute(
                        """
                        update email_classifications
                        set folder='Scanner', uidvalidity=77, uid=707
                        where id=901
                        """
                    )
                else:
                    db.execute(
                        """
                        update email_messages
                        set folder='Scanner', uidvalidity=77, uid=707
                        where stable_message_identity=?
                        """,
                        (action.locator.stable_message_identity,),
                    )
            return result

    with pytest.raises(store_module.EmailActionAttemptConflict):
        module._run_next_direct_action(
            email_store,
            lambda _account_id: Executor(),
            available_account_ids=("account-direct",),
        )

    expected_classification = (
        ("Scanner", 77, 707)
        if scanner_raced_table == "email_classifications"
        else ("INBOX", 9, 901)
    )
    expected_message = (
        ("Scanner", 77, 707)
        if scanner_raced_table == "email_messages"
        else ("INBOX", 9, 901)
    )
    with sqlite3.connect(database) as db:
        assert db.execute(
            "select folder, uidvalidity, uid from email_classifications where id=901"
        ).fetchone() == expected_classification
        assert db.execute(
            """
            select folder, uidvalidity, uid from email_messages
            where stable_message_identity=?
            """,
            ("account-direct:message-id:<direct-901@example.com>",),
        ).fetchone() == expected_message
        assert db.execute("select count(*) from email_action_attempts").fetchone()[0] == 0


def test_direct_action_does_not_claim_an_unavailable_account():
    module = _module()
    calls = []

    class Store:
        def claim_next_direct_action(self, **kwargs):
            calls.append(kwargs)
            pytest.fail("unavailable account must not be claimed")

    assert (
        module._run_next_direct_action(
            Store(),
            lambda _account_id: None,
            available_account_ids=(),
        )
        is None
    )
    assert calls == []


def test_direct_action_loop_recovers_stale_claim_before_claiming_next_action():
    module = _module()
    calls = []

    class Store:
        def recover_stale_processing_actions(self, **kwargs):
            calls.append(("recover", kwargs))
            return 1

        def claim_next_direct_action(self, **kwargs):
            calls.append(("claim", kwargs))
            return None

    assert (
        module._run_next_direct_action(
            Store(),
            lambda _account_id: object(),
            available_account_ids=("account-1",),
        )
        is None
    )
    assert [name for name, _kwargs in calls] == ["recover", "claim"]


def test_failed_direct_action_degrades_provider_component_health():
    module = _module()
    health = []
    failed = type(
        "FailedProviderResult",
        (),
        {
            "status": "failed",
            "error": "provider_apply_failed:TimeoutError",
        },
    )()

    module.run_scan_and_direct_actions_loop(
        ({"account_id": "account-1", "scan_interval_seconds": 60},),
        object(),
        scan_account=lambda _account, _model: {"persisted_count": 1},
        run_direct_actions_once=lambda: failed,
        record_health=lambda scope, payload: health.append((scope, payload)),
        sleep=lambda _seconds: None,
        max_cycles=1,
    )

    assert (
        "component:email-provider-actions",
        {
            "status": "degraded",
            "error_code": "provider_action_failed",
        },
    ) in health


def test_direct_action_drain_processes_multiple_actions_but_stops_at_count_bound():
    module = _module()
    calls = []

    module.run_scan_and_direct_actions_loop(
        ({"account_id": "account-1", "scan_interval_seconds": 60},),
        object(),
        scan_account=lambda _account, _model: {"persisted_count": 0},
        run_direct_actions_once=lambda: calls.append("action")
        or SimpleNamespace(status="done"),
        sleep=lambda _seconds: None,
        max_cycles=1,
        direct_action_max_actions=3,
        direct_action_time_budget_seconds=60.0,
        monotonic=lambda: 0.0,
    )

    assert calls == ["action", "action", "action"]


def test_direct_action_drain_stops_on_empty_queue_without_busy_loop():
    module = _module()
    calls = []

    module.run_scan_and_direct_actions_loop(
        ({"account_id": "account-1", "scan_interval_seconds": 60},),
        object(),
        scan_account=lambda _account, _model: {"persisted_count": 0},
        run_direct_actions_once=lambda: calls.append("empty") or None,
        sleep=lambda _seconds: None,
        max_cycles=1,
        direct_action_max_actions=100,
        direct_action_time_budget_seconds=60.0,
        monotonic=lambda: 0.0,
    )

    assert calls == ["empty"]


def test_direct_action_drain_stops_at_time_bound():
    module = _module()
    calls = []
    clock = iter((0.0, 0.0, 0.6))

    module.run_scan_and_direct_actions_loop(
        ({"account_id": "account-1", "scan_interval_seconds": 60},),
        object(),
        scan_account=lambda _account, _model: {"persisted_count": 0},
        run_direct_actions_once=lambda: calls.append("action")
        or SimpleNamespace(status="done"),
        sleep=lambda _seconds: None,
        max_cycles=1,
        direct_action_max_actions=100,
        direct_action_time_budget_seconds=0.5,
        monotonic=lambda: next(clock),
    )

    assert calls == ["action"]


def test_scan_config_uses_active_model_category_eligibility():
    module = _module()
    contracts = import_module("app.email_classifier_contracts")
    categories = tuple(contracts.EmailCategory)
    rows = [
        {
            "category": category.value,
            "description": category.value,
            "enabled": True,
            "threshold": 0.8,
            "actions": ["mark_read"]
            if category is contracts.EmailCategory.WORK
            else [],
            "action_parameters": {},
            "config_version": "config-v3",
        }
        for category in categories
    ]
    model_record = SimpleNamespace(
        status="active",
        metadata=SimpleNamespace(
            model_id="email-model:worker-test",
            validation_method="time-ordered-holdout",
            per_category_metrics={
                category.value: {
                    "precision": 0.99,
                    "validation_sample_count": 35,
                    "validation_positive_support": 35,
                    "configured_threshold": 0.8,
                    "evaluated_threshold": 0.8,
                    "auto_action_eligible": category is contracts.EmailCategory.WORK,
                    "eligibility_reason": (
                        "precision_and_sample_gate_met"
                        if category is contracts.EmailCategory.WORK
                        else "precision_gate_not_met"
                    ),
                }
                for category in categories
            },
        ),
    )

    config = module._scan_config(
        SimpleNamespace(list_configs=lambda: rows),
        model_record,
    )

    work = config.category_eligibility[contracts.EmailCategory.WORK]
    assert work.auto_action_eligible is True
    assert (
        work.action_eligibility[contracts.EmailAction.MARK_READ].auto_action_eligible
        is True
    )
    assert work.validated_precision == 0.99
    assert work.validation_sample_count == 35


@pytest.mark.parametrize("status", ["candidate", "rejected", "failed", "previous"])
def test_scan_config_non_active_model_record_is_never_action_eligible(status: str):
    module = _module()
    contracts = import_module("app.email_classifier_contracts")
    rows = [
        {
            "category": category.value,
            "description": category.value,
            "enabled": True,
            "threshold": 0.85,
            "actions": ["label"] if category is contracts.EmailCategory.WORK else [],
            "action_parameters": (
                {"label": {"labels": ["Work"]}}
                if category is contracts.EmailCategory.WORK
                else {}
            ),
            "config_version": "config-status-v1",
        }
        for category in contracts.EmailCategory
    ]
    record = SimpleNamespace(
        status=status,
        metadata=SimpleNamespace(
            model_id="email-model:worker-test",
            validation_method="time-ordered-holdout",
            per_category_metrics={
                contracts.EmailCategory.WORK.value: {
                    "precision": 1.0,
                    "validation_sample_count": 100,
                    "validation_positive_support": 100,
                    "configured_threshold": 0.85,
                    "evaluated_threshold": 0.85,
                    "auto_action_eligible": True,
                    "eligibility_reason": "precision_and_sample_gate_met",
                }
            },
        ),
    )

    config = module._scan_config(SimpleNamespace(list_configs=lambda: rows), record)
    work = config.category_eligibility[contracts.EmailCategory.WORK]

    assert work.auto_action_eligible is False
    assert (
        work.action_eligibility[contracts.EmailAction.LABEL].auto_action_eligible
        is False
    )
    assert (
        work.action_eligibility[contracts.EmailAction.LABEL].reason
        == "model_not_active"
    )


def test_scan_config_without_model_eligibility_stays_pending_feedback():
    module = _module()
    contracts = import_module("app.email_classifier_contracts")
    rows = [
        {
            "category": category.value,
            "description": category.value,
            "enabled": True,
            "threshold": 0.8,
            "actions": ["mark_read"],
            "action_parameters": {},
            "config_version": "config-v4",
        }
        for category in contracts.EmailCategory
    ]

    config = module._scan_config(
        SimpleNamespace(list_configs=lambda: rows),
        None,
    )

    assert all(
        eligibility.auto_action_eligible is False
        for eligibility in config.category_eligibility.values()
    )
    assert {
        eligibility.reason for eligibility in config.category_eligibility.values()
    } == {"model_eligibility_missing"}


def test_startup_builds_real_loop_dependencies_only_after_accounts_and_model():
    module = _module()
    events = []
    output = StringIO()
    accounts = ({"account_id": "account-1", "enabled": True},)
    model = object()
    ready_dependencies = _dependencies([])
    bootstrap = SimpleNamespace(
        load_enabled_accounts=lambda: events.append("accounts") or accounts,
        load_active_model=lambda: events.append("model") or model,
        build_dependencies=lambda loaded_accounts, loaded_model: (
            events.append(("dependencies", loaded_accounts, loaded_model))
            or ready_dependencies
        ),
    )

    class FakeThread:
        def __init__(self, *, target, name, daemon):
            events.append(("thread", name, daemon))

        def start(self):
            events.append("start")

    module.run_email_worker(
        SimpleNamespace(),
        dependencies=bootstrap,
        thread_factory=FakeThread,
        wait=lambda: None,
        output=output,
    )

    assert events[:3] == [
        "accounts",
        "model",
        ("dependencies", accounts, model),
    ]
    assert output.getvalue().startswith("email-worker starting")


def test_startup_records_process_heartbeat_only_after_dependencies_are_ready():
    module = _module()
    health = []
    dependencies = _dependencies([])
    dependencies.record_health = lambda scope, payload: health.append((scope, payload))

    module.run_email_worker(
        SimpleNamespace(),
        dependencies=dependencies,
        thread_factory=lambda **_kwargs: SimpleNamespace(start=lambda: None),
        wait=lambda: None,
        output=StringIO(),
    )

    assert health == [
        (
            "component:email-legacy-lifecycle",
            {"status": "ready", "task_count": 0, "unresolved_count": 0},
        ),
        (
            "process:email-worker",
            {"status": "starting", "accounts": 1, "components": 3},
        ),
    ]


def test_readiness_barrier_reports_ready_only_after_all_component_heartbeats():
    module = _module()
    health = []
    barrier = module.EmailWorkerReadiness(
        ("email-scan-actions", "email-agent-consumer", "email-training"),
        record_health=lambda scope, payload: health.append((scope, payload)),
        accounts=1,
    )

    barrier.mark_ready("email-scan-actions")
    barrier.mark_ready("email-training")
    assert [scope for scope, _payload in health] == []

    barrier.mark_ready("email-agent-consumer")
    assert health == [
        (
            "process:email-worker",
            {"status": "ready", "accounts": 1, "components": 3},
        )
    ]


def test_default_worker_wait_detects_an_unexpected_component_exit():
    module = _module()

    class DeadThread:
        def is_alive(self):
            return False

    with pytest.raises(RuntimeError, match="component exited unexpectedly"):
        module._wait_for_email_components((DeadThread(),))


def test_email_task_input_uses_thread_text_attachment_metadata_and_receipts():
    module = _module()
    stable_identity = "account-1:message-id:<current@example.com>"
    payload = {
        "schema": "email_agent_action.v1",
        "account_id": "account-1",
        "stable_message_identity": stable_identity,
        "thread_identity": "thread-1",
        "classification_id": 17,
        "action_identity": "email-action:auto-reply-1",
        "action_plan_id": "email-plan:17:v1",
        "action_plan_version": 1,
        "model_id": "email-model:test",
        "config_version": "email-config:test",
    }
    task = SimpleNamespace(trigger_message_json=json.dumps(payload))
    classification = {
        "id": 17,
        "account_id": "account-1",
        "folder": "INBOX",
        "uidvalidity": 42,
        "uid": 9,
        "thread_id": "thread-1",
        "stable_message_identity": stable_identity,
        "subject": "Current subject",
        "current_action_plan_id": "email-plan:17:v1",
        "action_plan": {
            "action_plan_id": "email-plan:17:v1",
            "action_plan_version": 1,
            "model_id": "email-model:test",
            "config_version": "email-config:test",
        },
    }
    email_store = SimpleNamespace(
        get_classification=lambda classification_id: (
            classification if classification_id == 17 else None
        ),
        get_account=lambda account_id: (
            {"account_id": account_id} if account_id == "account-1" else None
        ),
        get_email_reply_receipt=lambda action_identity: {
            "provider_result_id": "sent-17",
            "provider_operation": "sent_readback",
            "display_excerpt": "Automatic email reply verified in Sent.",
        },
        get_email_unsubscribe_receipt=lambda _action_identity: None,
        list_email_context_thread=lambda **_kwargs: [
            {
                "stable_message_identity": "account-1:message-id:<prior@example.com>",
                "thread_identity": "thread-1",
                "sender": "prior@example.com",
                "subject": "Prior subject",
                "normalized_text": "Prior thread text",
                "attachment_metadata": [],
                "received_at": "2026-08-30T08:00:00+00:00",
            },
            {
                "stable_message_identity": stable_identity,
                "thread_identity": "thread-1",
                "sender": "sender@example.com",
                "subject": "Current subject",
                "normalized_text": "Current message text",
                "attachment_metadata": [
                    {
                        "filename": "contract.pdf",
                        "mime_type": "application/pdf",
                        "size_bytes": 1234,
                        "inline": False,
                    }
                ],
                "received_at": "2026-08-30T09:00:00+00:00",
            },
        ],
        list_email_context_receipts=lambda **_kwargs: [
            {
                "receipt_id": "sent-17",
                "operation": "sent_readback",
                "summary": "Automatic email reply verified in Sent.",
                "completed": True,
            }
        ],
    )
    source = SimpleNamespace(
        fetch_uid_batch=lambda *args, **kwargs: SimpleNamespace(
            uidvalidity=42,
            messages=[
                {
                    "stableMessageIdentity": "account-1:message-id:<prior@example.com>",
                    "threadId": "thread-1",
                    "from": {"email": "prior@example.com"},
                    "date": "2026-08-30T08:00:00+00:00",
                    "textBody": "Prior thread text",
                    "attachments": [],
                    "uid": 8,
                },
                {
                    "stableMessageIdentity": stable_identity,
                    "threadId": "thread-1",
                    "from": {"email": "sender@example.com"},
                    "date": "2026-08-30T09:00:00+00:00",
                    "subject": "Current subject",
                    "textBody": "Current message text",
                    "listUnsubscribe": "",
                    "listUnsubscribePost": "",
                    "attachments": [
                        {
                            "filename": "contract.pdf",
                            "mime_type": "application/pdf",
                            "size_bytes": 1234,
                            "inline": False,
                        }
                    ],
                    "uid": 9,
                },
            ],
        ),
        logout=lambda: None,
    )

    task_input = module._load_email_task_input(
        email_store,
        lambda account: source,
        task,
    )

    assert task_input.trigger.text == "Current message text"
    assert [message.text for message in task_input.thread_messages] == [
        "Prior thread text"
    ]
    assert task_input.attachments[0].filename == "contract.pdf"
    assert task_input.attachments[0].mime_type == "application/pdf"
    assert not hasattr(task_input.attachments[0], "content")
    assert task_input.prior_receipts[0].receipt_id == "sent-17"
    assert task_input.prior_receipts[0].operation == "sent_readback"


def test_consumer_task_failure_is_sanitized_isolated_and_heartbeated(monkeypatch):
    module = _module()
    monkeypatch.setattr(
        "app.task_lifecycle.validate_audited_email_task",
        lambda _task, _context: True,
    )
    first = SimpleNamespace(id=1, execution_generation="gen-1")
    second = SimpleNamespace(id=2, execution_generation="gen-2")
    failures = []
    finalized = []
    health = []

    class Store:
        def claim_reply_tasks(self, limit, *, channel):
            assert (limit, channel) == (50, "email")
            return [first, second]

        def fail_reply_task(self, task_id, error, *, expected_execution_generation):
            failures.append((task_id, error, expected_execution_generation))

    def process(task, context, *, refresh_context):
        if task is first:
            raise RuntimeError(
                "mail body contract.pdf SECRET https://mail.example.test/token"
            )
        assert refresh_context() == f"context-{task.id}"
        return f"result-{task.id}"

    module.run_email_agent_task_loop(
        Store(),
        SimpleNamespace(process=process),
        load_task_context=lambda task: f"context-{task.id}",
        finalize_task=lambda task, result: finalized.append((task.id, result)),
        record_health=lambda scope, payload: health.append((scope, payload)),
        sleep=lambda _seconds: None,
        max_cycles=1,
    )

    assert failures == [(1, "email_consumer_runtime_error:RuntimeError", "gen-1")]
    assert finalized == [(2, "result-2")]
    assert health[-1] == (
        "component:email-agent-consumer",
        {
            "status": "degraded",
            "failures": 1,
            "error_code": "consumer_runtime_error",
            "error_type": "RuntimeError",
        },
    )
    assert "mail body" not in repr(health)
    assert "contract.pdf" not in repr(health)
    assert "SECRET" not in repr(health)
    assert "https://" not in repr(health)


def test_training_failure_is_sanitized_isolated_and_heartbeated():
    module = _module()
    calls = 0
    health = []

    def training_tick():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError(
                "mail body contract.pdf SECRET https://mail.example.test/token"
            )

    module.run_training_scheduler_loop(
        training_tick,
        record_health=lambda scope, payload: health.append((scope, payload)),
        sleep=lambda _seconds: None,
        max_cycles=2,
    )

    assert calls == 2
    assert health == [
        (
            "component:email-training",
            {
                "status": "degraded",
                "failures": 1,
                "error_code": "training_runtime_error",
                "error_type": "RuntimeError",
            },
        ),
        ("component:email-training", {"status": "ready", "failures": 0}),
    ]
    assert "mail body" not in repr(health)
    assert "contract.pdf" not in repr(health)
    assert "SECRET" not in repr(health)
    assert "https://" not in repr(health)
