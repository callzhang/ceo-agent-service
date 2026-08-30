from __future__ import annotations

import json
from importlib import import_module
from io import StringIO
from types import SimpleNamespace

import pytest


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
        "email-worker ready accounts=1 components=3"
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
        accounts=() if failure == "empty_accounts" else (
            {"account_id": "account-1", "enabled": True},
        ),
    )
    if failure == "model_failure":
        dependencies.load_active_model = lambda: (_ for _ in ()).throw(
            RuntimeError("active model missing")
        )

    with pytest.raises(module.EmailWorkerStartupError):
        module.run_email_worker(
            SimpleNamespace(),
            dependencies=dependencies,
            thread_factory=lambda **kwargs: started.append(kwargs),
            wait=lambda: None,
            output=output,
        )

    assert output.getvalue() == ""
    assert started == []


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
            "actions": ["mark_read"] if category is contracts.EmailCategory.WORK else [],
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
    assert output.getvalue().startswith("email-worker ready")


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
            {"status": "ready", "accounts": 1, "components": 3},
        )
    ]


def test_email_task_input_uses_thread_text_attachment_metadata_and_receipts():
    module = _module()
    stable_identity = "account-1:message-id:<current@example.com>"
    payload = {
        "account_id": "account-1",
        "stable_message_identity": stable_identity,
        "thread_identity": "thread-1",
        "classification_id": 17,
        "action_identity": "email-action:auto-reply-1",
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
