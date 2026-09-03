from __future__ import annotations

import json
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
        task_store=SimpleNamespace(claim_reply_tasks=lambda *args, **kwargs: []),
        orchestrator=SimpleNamespace(process=lambda *args, **kwargs: None),
        load_task_context=lambda task: None,
        finalize_task=lambda task, result: None,
        training_tick=lambda: None,
        record_health=lambda scope, payload: None,
    )


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
            automatic=False,
            executed_prefix_length=0,
        )
        is sentinel_result
    )
    assert execution_calls[0][0:2] == (effect, (entry,))
    assert execution_calls[0][2]["store"] is operation.email_store
    assert execution_calls[0][2]["owner"] is owner
    assert execution_calls[0][2]["automatic"] is False
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
    private_url = (
        "https://news.example.com/unsubscribe?token=production-private-secret"
    )
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
        {"outcome": "proposal"},
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
    [projected_entry] = payload["unsubscribe_entries"]
    operation_kind = (
        "post_one_click" if provider_shape == "one_click" else "open_entry"
    )
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
                "network_policy_origin_references": list(
                    policy.origin_references
                ),
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
    monkeypatch.setattr(module, "_build_email_source_factory", lambda _settings: source_factory)
    execution_calls = []

    def fake_dedicated_profile(effect, entries, **kwargs):
        execution_calls.append((effect, entries, kwargs))
        assert kwargs["network_policy"].reference == policy.reference
        assert kwargs["network_policy"].origin_references == policy.origin_references
        assert effect.network_policy_reference == policy.reference
        assert effect.network_policy_origin_references == policy.origin_references
        assert kwargs["automatic"] is False
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


def test_email_agent_consumer_claims_only_email_channel_without_dingtalk_adapter():
    module = _module()
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
        SimpleNamespace(process=lambda *_args, **_kwargs: pytest.fail("reply executed")),
        load_task_context=lambda _task: pytest.fail("reply context loaded"),
        finalize_task=lambda *_args: pytest.fail("reply finalized"),
        sleep=lambda _seconds: None,
        max_cycles=1,
    )

    assert failures == [(42, "email_auto_reply_disabled", "generation-1")]


def test_unsubscribe_task_uses_direct_consumer_without_orchestrator():
    module = _module()
    action_identity = email_action_identity(
        account_id="account-1",
        stable_message_identity="account-1:message-id:<mail@example.com>",
        action_type=EmailAction.UNSUBSCRIBE,
        action_plan_version=1,
    )
    payload = {
        "schema": "email_agent_action.v1",
        "lifecycle_version": "email_unsubscribe_consumer_direct_v1",
        "action_type": "unsubscribe",
        "action_identity": action_identity,
        "action_plan_id": "email-plan:1",
        "action_plan_version": 1,
        "classification_id": 1,
        "account_id": "account-1",
        "stable_message_identity": "account-1:message-id:<mail@example.com>",
        "thread_identity": "thread-1",
        "category": "subscription",
        "classification_source": "user",
        "confidence": 1.0,
        "model_id": "email-model:test",
        "config_version": "email-config:test",
        "action_parameters": {},
    }
    task = SimpleNamespace(
        id=7,
        execution_generation="generation-1",
        channel="email",
        conversation_id="email-thread:1",
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

    class DirectConsumer:
        def run(self, claimed, loaded):
            calls.append(("direct", claimed, loaded))
            return "direct-result"

    orchestrator = SimpleNamespace(
        process=lambda *_args, **_kwargs: pytest.fail("Audit orchestrator was called")
    )
    module.run_email_agent_task_loop(
        Store(),
        orchestrator,
        unsubscribe_consumer=DirectConsumer(),
        load_task_context=lambda claimed: context,
        finalize_task=lambda claimed, result: calls.append(
            ("finalize", claimed, result)
        ),
        sleep=lambda _seconds: None,
        max_cycles=1,
    )

    assert calls[1] == ("direct", task, context)
    assert calls[2] == ("finalize", task, "direct-result")


def test_default_dependency_builder_wires_a_real_unsubscribe_consumer(
    tmp_path, monkeypatch
):
    module = _module()
    sentinel = object()
    captured = []

    monkeypatch.setattr(
        module,
        "_build_email_unsubscribe_consumer",
        lambda *args, **kwargs: captured.append((args, kwargs))
        or (sentinel, object()),
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

    assert dependencies.unsubscribe_consumer is sentinel
    assert captured


def test_unsubscribe_result_finalizes_without_agent_or_audit_run():
    module = _module()
    from app.agent_result import AgentError
    from app.email_unsubscribe_consumer import EmailUnsubscribeConsumerResult

    task = SimpleNamespace(
        id=7,
        execution_generation="generation-1",
        conversation_id="email-thread:1",
        conversation_title="Email unsubscribe",
        trigger_message_id="email-action:unsubscribe-1",
        trigger_sender="sender@example.com",
        trigger_text="Immutable ActionPlan authorizes unsubscribe.",
    )
    calls = []

    class Store:
        def finalize_reply_task_without_run(self, **kwargs):
            calls.append(kwargs)

    result = EmailUnsubscribeConsumerResult(
        outcome="no_action",
        summary="Already unsubscribed.",
        error=AgentError(),
    )

    module._finalize_email_task(Store(), task, result)

    assert calls == [
        {
            "task_id": 7,
            "expected_execution_generation": "generation-1",
            "task_status": "done",
            "task_error": "",
            "available_at": "",
            "conversation_id": "email-thread:1",
            "conversation_title": "Email unsubscribe",
            "trigger_message_id": "email-action:unsubscribe-1",
            "trigger_sender": "sender@example.com",
            "trigger_text": "Immutable ActionPlan authorizes unsubscribe.",
            "codex_reason": "Already unsubscribed.",
            "audit_summary": "",
            "send_status": "skipped",
            "send_error": "",
            "channel": "email",
        }
    ]


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


def test_direct_action_does_not_claim_an_unavailable_account():
    module = _module()
    calls = []

    class Store:
        def claim_next_direct_action(self, **kwargs):
            calls.append(kwargs)
            pytest.fail("unavailable account must not be claimed")

    assert module._run_next_direct_action(
        Store(),
        lambda _account_id: None,
        available_account_ids=(),
    ) is None
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

    assert module._run_next_direct_action(
        Store(),
        lambda _account_id: object(),
        available_account_ids=("account-1",),
    ) is None
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

    assert ("component:email-provider-actions", {
        "status": "degraded",
        "error_code": "provider_action_failed",
    }) in health


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
    metadata = SimpleNamespace(
        per_category_metrics={
            category.value: {
                "precision": 0.99,
                "validation_sample_count": 35,
                "configured_threshold": 0.8,
                "auto_action_eligible": category is contracts.EmailCategory.WORK,
                "eligibility_reason": (
                    "precision_and_sample_gate_met"
                    if category is contracts.EmailCategory.WORK
                    else "precision_gate_not_met"
                ),
            }
            for category in categories
        }
    )

    config = module._scan_config(
        SimpleNamespace(list_configs=lambda: rows),
        metadata,
    )

    work = config.category_eligibility[contracts.EmailCategory.WORK]
    assert work.auto_action_eligible is True
    assert work.validated_precision == 0.99
    assert work.validation_sample_count == 35


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
            "process:email-worker",
            {"status": "starting", "accounts": 1, "components": 3},
        )
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


def test_consumer_task_failure_is_sanitized_isolated_and_heartbeated():
    module = _module()
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
