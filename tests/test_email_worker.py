from __future__ import annotations

import json
from importlib import import_module
from io import StringIO
from types import SimpleNamespace

import pytest

from app.agent_context import AgentTaskContext
from app.email_classifier_contracts import EmailAction
from app.email_store import email_action_identity


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
