from __future__ import annotations

import json
import sqlite3
import time
from email import policy
from email.parser import BytesParser
from datetime import datetime, timezone
from hashlib import sha256
from importlib import import_module
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.agent_context import AgentTaskContext
from app.agent_contracts import DecisionOption, ProposedAction
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
    ephemeral_body_html,
    parse_rfc822_message,
)
from app.email_store import email_action_identity
from app.email_task_adapter import email_conversation_id
from app.email_task_adapter import (
    EmailClassificationTaskAdapter,
    EmailClassificationTaskInput,
)
from app.email_task_producer import EmailActionTaskProducer
from app.email_unsubscribe import (
    ConnectedMailboxOtp,
    EmailOtpChallenge,
    UnsubscribeAuthenticationEvidence,
    extract_unsubscribe_entries,
)
from app.email_unsubscribe_audit import EmailUnsubscribeAuditOperation
from app.store import AgentRole, AutoReplyStore
from app.email_store import EmailStore
from app.email_provider_folders import FolderRole, ProviderFolder
from app.email_classifier_runtime import (
    EmailClassifierRuntimeMode,
    OnlineClassificationResult,
    OnlineModelAcceptError,
    OnlineModelAcceptStage,
    OnlineModelDurableConflict,
    OnlineModelInput,
)
from app.email_classifier_scan import AgentScanContext, route_online_classification
from app.email_embedding_classifier import EmbeddingModelPrediction
from app.email_historical_classifier import (
    HistoricalActionResult,
    HistoricalClassificationCandidate,
    HistoricalClassificationState,
)
from app.email_provider_actions import ProviderActionResult
from app.email_store import StoredEmailLocator


def _module():
    return import_module("app.email_worker")


def test_connected_mailbox_otp_resolver_reads_only_the_bound_account_and_closes():
    opened_at = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)
    challenge = EmailOtpChallenge(
        recipient="derek@stardust.ai",
        site_domain="accounts.example.com",
        context_reference="otp-context:unsubscribe",
        opened_at=opened_at,
        expires_at=opened_at.replace(minute=10),
    )
    candidate = ConnectedMailboxOtp(
        recipient="derek@stardust.ai",
        sender_domain="accounts.example.com",
        context_reference="otp-context:unsubscribe",
        authenticated_sender_domain="accounts.example.com",
        authentication_reference="otp-auth:" + "a" * 64,
        received_at=opened_at.replace(minute=1),
        value="847201",
    )
    events = []

    class Source:
        def fetch_email_otp_candidates(self, supplied, *, limit):
            events.append(("fetch", supplied, limit))
            return (candidate,)

        def logout(self):
            events.append("logout")

    account = {
        "account_id": "account-primary",
        "email_address": "derek@stardust.ai",
    }
    resolver = _module()._build_connected_mailbox_otp_resolver(
        account,
        lambda supplied: events.append(("connect", supplied)) or Source(),
    )

    assert resolver(challenge) is candidate
    assert events == [
        ("connect", account),
        ("fetch", challenge, 8),
        "logout",
    ]
    wrong_mailbox = EmailOtpChallenge(
        recipient="other@stardust.ai",
        site_domain="accounts.example.com",
        context_reference="otp-context:unsubscribe",
        opened_at=opened_at,
        expires_at=opened_at.replace(minute=10),
    )
    assert resolver(wrong_mailbox) is None
    assert len(events) == 3


def test_production_email_source_factory_binds_the_configured_recipient(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []
    sentinel = object()

    monkeypatch.setattr(
        "app.email_connector_config.resolve_secret",
        lambda reference, environment: "secret",
    )
    monkeypatch.setattr(
        "app.email_imap_readonly.ImapReadonlyAdapter.connect",
        lambda host, username, password, **kwargs: calls.append(
            (host, username, password, kwargs)
        )
        or sentinel,
    )
    account = {
        "account_id": "account-primary",
        "email_address": "derek@stardust.ai",
        "imap_host": "imap.example.com",
        "imap_port": 993,
        "imap_username": "derek@stardust.ai",
        "imap_secret_reference": "env:MAIL_SECRET",
        "imap_tls": True,
    }

    source = _module()._build_email_source_factory(object())(account)

    assert source is sentinel
    assert calls == [
        (
            "imap.example.com",
            "derek@stardust.ai",
            "secret",
            {
                "port": 993,
                "account_id": "account-primary",
                "mailbox_address": "derek@stardust.ai",
            },
        )
    ]

def _accepted_model_prediction(category="work", important=True):
    return EmbeddingModelPrediction(
        category=category,
        category_probability=0.97,
        category_probabilities={category: 0.97},
        category_accepted=True,
        important=important,
        important_probability=0.96 if important else 0.04,
        head_ms=1.0,
    )


def test_model_primary_result_uses_existing_history_and_action_queue(tmp_path):
    store = EmailStore(tmp_path / "model-result.sqlite3")
    produced = []
    message = {
        "messageId": "<model-result@example.com>",
        "stableMessageIdentity": "stable-model-result",
        "accountId": "account-1",
        "folder": "INBOX",
        "uidValidity": 42,
        "uid": 7,
        "providerUnread": True,
        "from": {"email": "sender@example.com"},
        "subject": "Board contract",
        "textBody": "Please review the agreement.",
        "date": "2026-09-07T12:00:00+00:00",
    }

    persisted = _module().persist_model_primary_classification(
        store,
        SimpleNamespace(produce=lambda plan, raw: produced.append((plan, raw))),
        message=message,
        prediction=_accepted_model_prediction(),
        context=AgentScanContext(
            allowed_category_keys=("work", "junk"),
            category_descriptions={"work": {}, "junk": {}},
            folder_targets={"work": "Work"},
            config_version="config-v1",
        ),
        model_id="email-embedding-mlp-ready",
        model_text="exact current model text",
        unsubscribe_entries=(),
    )

    assert persisted.status == "accepted"
    assert persisted.persisted["classification_source"] == "model"
    assert persisted.persisted["model_id"] == "email-embedding-mlp-ready"
    assert persisted.persisted["predicted_category"] == "work"
    assert persisted.persisted["action_plan"]["actions"] == [
        "move",
        "flag_important",
    ]
    assert produced == []
    direct = store.claim_next_direct_action(
        claimed_at="2026-09-07T12:00:01+00:00",
        account_ids=("account-1",),
    )
    assert direct is not None
    assert direct.action_type is EmailAction.MOVE
    with sqlite3.connect(tmp_path / "model-result.sqlite3") as db:
        assert db.execute(
            "select count(*) from email_agent_classification_tasks"
        ).fetchone()[0] == 0


def _route_production_model_accept(
    store,
    producer,
    *,
    message,
    prediction,
    context,
    model_id="email-embedding-mlp-ready",
    unsubscribe_entries=(),
    agent_calls,
):
    return route_online_classification(
        mode=EmailClassifierRuntimeMode.MODEL_PRIMARY,
        current_input=OnlineModelInput("exact current text", "input-v3"),
        model_predict=lambda _value: OnlineClassificationResult(
            source="model", value=prediction
        ),
        enqueue_agent=lambda _value: agent_calls.append("agent") or "queued",
        accept_model=lambda accepted: _module().persist_model_primary_classification(
            store,
            producer,
            message=message,
            prediction=accepted,
            context=context,
            model_id=model_id,
            model_text="exact current text",
            unsubscribe_entries=unsubscribe_entries,
        ),
    )


def _model_accept_message():
    return {
        "messageId": "<accept-production@example.com>",
        "accountId": "account-1",
        "folder": "INBOX",
        "uidValidity": 42,
        "uid": 17,
        "providerUnread": True,
        "from": {"email": "sender@example.com"},
        "subject": "Production accept",
        "textBody": "Review this business message.",
        "date": "2026-09-07T12:00:00+00:00",
    }


def _assert_no_model_accept_rows(database):
    with sqlite3.connect(database) as db:
        assert db.execute("select count(*) from email_classifications").fetchone()[0] == 0
        assert db.execute("select count(*) from email_action_plans").fetchone()[0] == 0
        assert db.execute("select count(*) from email_actions").fetchone()[0] == 0


def test_model_accept_missing_folder_target_falls_back_before_commit_once(tmp_path):
    database = tmp_path / "accept-missing-folder.sqlite3"
    store = EmailStore(database)
    task_store = AutoReplyStore(database)
    agent_calls = []

    result = _route_production_model_accept(
        store,
        EmailActionTaskProducer(task_store, store),
        message=_model_accept_message(),
        prediction=_accepted_model_prediction(),
        context=AgentScanContext(
            allowed_category_keys=("work", "junk"),
            category_descriptions={"work": {}, "junk": {}},
            folder_targets={},
            config_version="config-v1",
        ),
        agent_calls=agent_calls,
    )

    assert result.source == "agent"
    assert agent_calls == ["agent"]
    _assert_no_model_accept_rows(database)
    assert task_store.list_reply_tasks(channel="email") == []


def test_model_accept_real_store_write_failure_falls_back_once_without_rows(
    tmp_path, monkeypatch
):
    database = tmp_path / "accept-db-failure.sqlite3"
    store = EmailStore(database)
    task_store = AutoReplyStore(database)
    agent_calls = []
    monkeypatch.setattr(
        store,
        "persist_scan_result",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            sqlite3.OperationalError("injected write failure before commit")
        ),
    )

    result = _route_production_model_accept(
        store,
        EmailActionTaskProducer(task_store, store),
        message=_model_accept_message(),
        prediction=_accepted_model_prediction(),
        context=AgentScanContext(
            allowed_category_keys=("work", "junk"),
            category_descriptions={"work": {}, "junk": {}},
            folder_targets={"work": "Work"},
            config_version="config-v1",
        ),
        agent_calls=agent_calls,
    )

    assert result.source == "agent"
    assert agent_calls == ["agent"]
    _assert_no_model_accept_rows(database)
    assert task_store.list_reply_tasks(channel="email") == []


def test_model_accept_partial_commit_repairs_action_and_task_without_agent(
    tmp_path, monkeypatch
):
    database = tmp_path / "accept-partial.sqlite3"
    store = EmailStore(database)
    task_store = AutoReplyStore(database)
    message = _model_accept_message() | {
        "textBody": "Unsubscribe at https://news.example.test/unsubscribe",
        "listUnsubscribe": "<https://news.example.test/unsubscribe>",
        "listUnsubscribePost": "",
    }
    entries = extract_unsubscribe_entries(
        list_unsubscribe="<https://news.example.test/unsubscribe>",
        list_unsubscribe_post="",
        body_text=str(message["textBody"]),
        body_html="",
    )
    context = AgentScanContext(
        allowed_category_keys=("work", "junk"),
        category_descriptions={"work": {}, "junk": {}},
        folder_targets={"work": "Work"},
        config_version="config-v1",
    )
    real_persist = store.persist_scan_result
    persist_calls = 0

    def crash_after_plan(*args, **kwargs):
        nonlocal persist_calls
        persisted = real_persist(*args, **kwargs)
        persist_calls += 1
        if persist_calls == 1:
            with sqlite3.connect(database) as db:
                db.execute("delete from email_actions")
            raise RuntimeError("crash after durable plan")
        return persisted

    monkeypatch.setattr(store, "persist_scan_result", crash_after_plan)
    real_producer = EmailActionTaskProducer(task_store, store)
    producer_calls = 0

    class CrashAfterTaskProducer:
        def produce(self, plan, raw):
            nonlocal producer_calls
            routes = real_producer.produce(plan, raw)
            producer_calls += 1
            if producer_calls == 1:
                raise RuntimeError("crash after durable task")
            return routes

    agent_calls = []
    with pytest.raises(OnlineModelAcceptError) as interrupted:
        _route_production_model_accept(
            store,
            CrashAfterTaskProducer(),
            message=message,
            prediction=_accepted_model_prediction("junk", important=False),
            context=context,
            unsubscribe_entries=entries,
            agent_calls=agent_calls,
        )
    assert interrupted.value.stage is OnlineModelAcceptStage.AFTER_DURABLE_COMMIT

    result = _route_production_model_accept(
        store,
        CrashAfterTaskProducer(),
        message=message,
        prediction=_accepted_model_prediction("junk", important=False),
        context=context,
        unsubscribe_entries=entries,
        agent_calls=agent_calls,
    )

    assert result.source == "model"
    assert result.accept_outcome.status == "already_committed"
    assert agent_calls == []
    with sqlite3.connect(database) as db:
        assert db.execute("select count(*) from email_classifications").fetchone()[0] == 1
        assert db.execute("select count(*) from email_action_plans").fetchone()[0] == 1
        assert db.execute("select count(*) from email_actions").fetchone()[0] == 1
    assert len(task_store.list_reply_tasks(channel="email")) == 1


def test_worker_startup_repairs_missing_unsubscribe_task_and_repeated_tick_is_idempotent(
    tmp_path,
):
    module = _module()
    database = tmp_path / "startup-model-task-repair.sqlite3"
    email_store = EmailStore(database)
    task_store = AutoReplyStore(database)
    message = _model_accept_message() | {
        "textBody": "Unsubscribe at https://news.example.test/unsubscribe",
        "listUnsubscribe": "<https://news.example.test/unsubscribe>",
        "listUnsubscribePost": "",
    }
    entries = extract_unsubscribe_entries(
        list_unsubscribe=str(message["listUnsubscribe"]),
        list_unsubscribe_post="",
        body_text=str(message["textBody"]),
        body_html="",
    )
    module.persist_model_primary_classification(
        email_store,
        SimpleNamespace(produce=lambda *_args: None),
        message=message,
        prediction=_accepted_model_prediction("junk", important=False),
        context=AgentScanContext(
            allowed_category_keys=("work", "junk"),
            category_descriptions={"work": {}, "junk": {}},
            folder_targets={"work": "Work"},
            config_version="config-v1",
        ),
        model_id="email-embedding-mlp-ready",
        model_text="exact current text",
        unsubscribe_entries=entries,
    )
    health = []

    def reconcile():
        return module.reconcile_missing_model_action_tasks(
            email_store,
            EmailActionTaskProducer(task_store, email_store),
            load_message=lambda _classification: message,
            record_health=lambda scope, payload: health.append((scope, payload)),
        )

    dependencies = _dependencies([], model=object())
    dependencies.email_store = email_store
    dependencies.task_store = task_store
    dependencies.reconcile_action_tasks_once = reconcile

    module.run_email_worker(
        SimpleNamespace(),
        dependencies=dependencies,
        thread_factory=lambda **_kwargs: SimpleNamespace(start=lambda: None),
        wait=lambda: None,
        output=StringIO(),
    )
    reconcile()

    assert len(task_store.list_reply_tasks(channel="email")) == 1
    assert email_store.claim_next_direct_action(
        claimed_at="2026-09-08T12:00:00+00:00",
        account_ids=("account-1",),
    ) is None
    assert health[-1] == (
        "component:email-model-action-reconciliation",
        {"status": "ready", "candidates": 0, "repaired": 0, "conflicts": 0},
    )


def test_model_action_reconciliation_skips_nonunsubscribe_and_reports_conflict(
    tmp_path,
):
    module = _module()
    database = tmp_path / "model-task-repair-conflict.sqlite3"
    email_store = EmailStore(database)
    task_store = AutoReplyStore(database)
    message = _model_accept_message()
    module.persist_model_primary_classification(
        email_store,
        SimpleNamespace(produce=lambda *_args: None),
        message=message,
        prediction=_accepted_model_prediction("work", important=False),
        context=AgentScanContext(
            allowed_category_keys=("work", "junk"),
            category_descriptions={"work": {}, "junk": {}},
            folder_targets={"work": "Work"},
            config_version="config-v1",
        ),
        model_id="email-embedding-mlp-ready",
        model_text="exact current text",
        unsubscribe_entries=(),
    )
    assert module.reconcile_missing_model_action_tasks(
        email_store,
        EmailActionTaskProducer(task_store, email_store),
        load_message=lambda _classification: message,
        record_health=lambda *_args: None,
    )["candidates"] == 0
    assert task_store.list_reply_tasks(channel="email") == []

    junk_message = message | {
        "messageId": "<repair-conflict@example.com>",
        "uid": 18,
        "textBody": "https://news.example.test/unsubscribe",
        "listUnsubscribe": "<https://news.example.test/unsubscribe>",
        "listUnsubscribePost": "",
    }
    entries = extract_unsubscribe_entries(
        list_unsubscribe=str(junk_message["listUnsubscribe"])
    )
    module.persist_model_primary_classification(
        email_store,
        SimpleNamespace(produce=lambda *_args: None),
        message=junk_message,
        prediction=_accepted_model_prediction("junk", important=False),
        context=AgentScanContext(
            allowed_category_keys=("work", "junk"),
            category_descriptions={"work": {}, "junk": {}},
            folder_targets={"work": "Work"},
            config_version="config-v1",
        ),
        model_id="email-embedding-mlp-ready",
        model_text="exact current text",
        unsubscribe_entries=entries,
    )
    health = []

    result = module.reconcile_missing_model_action_tasks(
        email_store,
        EmailActionTaskProducer(task_store, email_store),
        load_message=lambda _classification: None,
        record_health=lambda scope, payload: health.append((scope, payload)),
    )

    assert result == {"candidates": 1, "repaired": 0, "conflicts": 1}
    assert health == [
        (
            "component:email-model-action-reconciliation",
            {
                "status": "degraded",
                "candidates": 1,
                "repaired": 0,
                "conflicts": 1,
                "error_code": "model_action_reconciliation_conflict",
            },
        )
    ]
    assert task_store.list_reply_tasks(channel="email") == []


def test_training_maintenance_ticks_runtime_and_reconciliation_once():
    module = _module()
    calls = []

    result = module.run_model_training_maintenance(
        SimpleNamespace(tick=lambda: calls.append("runtime") or "training-result"),
        reconcile_action_tasks_once=lambda: calls.append("reconcile"),
    )

    assert result == "training-result"
    assert calls == ["runtime", "reconcile"]


def test_training_maintenance_repairs_tasks_even_when_runtime_tick_fails():
    module = _module()
    calls = []

    with pytest.raises(RuntimeError, match="training failed"):
        module.run_model_training_maintenance(
            SimpleNamespace(
                tick=lambda: (
                    calls.append("runtime")
                    or (_ for _ in ()).throw(RuntimeError("training failed"))
                )
            ),
            reconcile_action_tasks_once=lambda: calls.append("reconcile"),
        )

    assert calls == ["runtime", "reconcile"]


@pytest.mark.parametrize("conflict", ("model_id", "category", "important", "plan_version"))
def test_model_accept_stable_readback_conflict_fails_closed_without_agent(
    tmp_path, conflict
):
    database = tmp_path / f"accept-conflict-{conflict}.sqlite3"
    store = EmailStore(database)
    task_store = AutoReplyStore(database)
    message = _model_accept_message()
    context = AgentScanContext(
        allowed_category_keys=("work", "legal", "junk"),
        category_descriptions={"work": {}, "legal": {}, "junk": {}},
        folder_targets={"work": "Work", "legal": "Legal"},
        config_version="config-v1",
    )
    _module().persist_model_primary_classification(
        store,
        EmailActionTaskProducer(task_store, store),
        message=message,
        prediction=_accepted_model_prediction(),
        context=context,
        model_id="email-embedding-mlp-ready",
        model_text="exact current text",
        unsubscribe_entries=(),
    )
    if conflict == "plan_version":
        with sqlite3.connect(database) as db:
            db.execute(
                "update email_action_plans set action_plan_version=2"
            )
    prediction = _accepted_model_prediction(
        "legal" if conflict == "category" else "work",
        important=conflict != "important",
    )
    agent_calls = []

    with pytest.raises(OnlineModelDurableConflict):
        _route_production_model_accept(
            store,
            EmailActionTaskProducer(task_store, store),
            message=message,
            prediction=prediction,
            context=context,
            model_id=(
                "email-embedding-mlp-other"
                if conflict == "model_id"
                else "email-embedding-mlp-ready"
            ),
            agent_calls=agent_calls,
        )

    assert agent_calls == []
    with sqlite3.connect(database) as db:
        assert db.execute("select count(*) from email_classifications").fetchone()[0] == 1
        assert db.execute("select count(*) from email_action_plans").fetchone()[0] == 1
        assert db.execute("select count(*) from email_actions").fetchone()[0] == 2
    assert task_store.list_reply_tasks(channel="email") == []


def test_worker_bootstrap_uses_agent_primary_without_online_activation(tmp_path):
    settings = SimpleNamespace(
        db_path=tmp_path / "worker-runtime.sqlite3",
        workspace=tmp_path,
        dry_run=False,
    )

    runtime = _module().build_email_worker_dependencies(settings).load_active_model()

    assert runtime.mode is EmailClassifierRuntimeMode.AGENT_PRIMARY
    assert runtime.model_predict is None
    assert runtime._observability_store.path == settings.db_path


def test_production_scan_account_model_primary_closure_persists_without_agent(
    tmp_path, monkeypatch
):
    module = _module()
    message = {
        "messageId": "<production-model@example.com>",
        "accountId": "account-1",
        "folder": "INBOX",
        "uidValidity": 42,
        "uid": 7,
        "providerUnread": True,
        "from": {"email": "sender@example.com"},
        "subject": "Production model route",
        "textBody": "Please review the work item.",
        "date": "2026-09-08T12:00:00+00:00",
    }

    class Source:
        account_id = "account-1"

        def list_folders(self):
            return (ProviderFolder("inbox-id", "INBOX", FolderRole.INBOX),)

        def fetch_uid_batch(self, mailbox, **kwargs):
            assert mailbox == "INBOX"
            assert kwargs["unread_only"] is True
            return import_module("app.email_imap_readonly").ImapUidBatch(
                account_id=self.account_id,
                folder=mailbox,
                uidvalidity=42,
                previous_uidvalidity=kwargs["cursor_uidvalidity"],
                messages=(message,),
            )

        def logout(self):
            return None

    monkeypatch.setattr(
        module, "_build_email_source_factory", lambda _settings: lambda _account: Source()
    )
    monkeypatch.setattr(module, "_build_agent_orchestrator", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(
        "app.agent_runtime_production.build_production_routed_codex_execution",
        lambda **_kwargs: object(),
    )
    settings = SimpleNamespace(
        db_path=tmp_path / "production-model-scan.sqlite3",
        workspace=tmp_path,
        dry_run=False,
    )
    bootstrap = module.build_email_worker_dependencies(settings)
    store = bootstrap.email_store
    monkeypatch.setattr(
        store,
        "list_category_configs",
        lambda: [
            {
                "category_key": "work",
                "enabled": True,
                "core_description": "Business work.",
                "include": ["delivery"],
                "exclude": ["promotion"],
                "config_version": "config-v1",
            },
            {
                "category_key": "junk",
                "enabled": True,
                "core_description": "Unwanted mail.",
                "include": ["promotion"],
                "exclude": ["delivery"],
                "config_version": "config-v1",
            },
        ],
    )
    monkeypatch.setattr(
        store,
        "list_account_folder_bindings",
        lambda: [
            {
                "account_id": "account-1",
                "category_key": "work",
                "provider_folder_id": "work-id",
                "provider_folder_name": "Work",
                "binding_status": "active",
            }
        ],
    )
    runtime = SimpleNamespace(
        mode=EmailClassifierRuntimeMode.MODEL_PRIMARY,
        model_predict=lambda _input: import_module(
            "app.email_classifier_runtime"
        ).OnlineClassificationResult(source="model", value=_accepted_model_prediction()),
        input_schema_version="input-v3",
        model_id="email-embedding-mlp-ready",
    )
    dependencies = bootstrap.build_dependencies(
        ({"account_id": "account-1", "enabled": True, "scan_folders": ["INBOX"]},),
        runtime,
    )

    [result] = dependencies.scan_account(
        {"account_id": "account-1", "enabled": True, "scan_folders": ["INBOX"]},
        runtime,
    )

    assert result.persisted_count == 1
    persisted = store.get_classification_by_stable_identity(
        "account-1:message-id:<production-model@example.com>"
    )
    assert persisted is not None
    assert persisted["classification_source"] == "model"
    assert persisted["model_id"] == "email-embedding-mlp-ready"
    with sqlite3.connect(settings.db_path) as db:
        assert db.execute(
            "select normalized_text from email_messages"
        ).fetchone()[0] == message["textBody"]
        stored_text = db.execute(
            "select model_text from email_classifications"
        ).fetchone()[0]
        assert "sender@example.com" not in stored_text
        assert "__subject__" in stored_text
        assert db.execute(
            "select count(*) from email_agent_classification_tasks"
        ).fetchone()[0] == 0


def test_historical_actions_use_changed_locator_preserve_read_and_persist_attempts(
    tmp_path,
):
    store = EmailStore(tmp_path / "historical-actions.sqlite3")
    calls = []
    provider_state = {"is_read": True, "folder": "INBOX"}
    message = {
        "messageId": "<historical@example.com>",
        "stableMessageIdentity": "stable-historical",
        "accountId": "account-1",
        "folder": "INBOX",
        "uidValidity": 42,
        "uid": 7,
        "providerUnread": False,
        "from": {"email": "sender@example.com"},
        "subject": "Historical work",
        "textBody": "A completed business thread.",
        "date": "2026-09-01T12:00:00+00:00",
    }
    context = AgentScanContext(
        allowed_category_keys=("work", "junk"),
        category_descriptions={"work": {}, "junk": {}},
        folder_targets={"work": "Work"},
        config_version="config-v1",
    )
    old_message = {
        **message,
        "messageId": "<old-pending@example.com>",
        "stableMessageIdentity": "stable-old-pending",
        "uid": 6,
    }
    _module().persist_model_primary_classification(
        store,
        SimpleNamespace(produce=lambda *_args: None),
        message=old_message,
        prediction=_accepted_model_prediction(),
        context=context,
        model_id="email-embedding-mlp-ready",
        model_text="old pending text",
        unsubscribe_entries=(),
    )

    class Executor:
        def execute(self, action):
            calls.append(action)
            updated = None
            if action.action_type is EmailAction.MOVE:
                provider_state.update(is_read=False, folder="Work")
                updated = StoredEmailLocator(
                    account_id=action.account_id,
                    folder="Work",
                    uidvalidity=84,
                    uid=19,
                    rfc_message_id=action.locator.rfc_message_id,
                    thread_id=action.locator.thread_id,
                    stable_message_identity=action.locator.stable_message_identity,
                )
            elif action.action_type is EmailAction.MARK_READ:
                provider_state["is_read"] = True
            return ProviderActionResult(
                status="done",
                provider_operation=action.action_type.value,
                provider_target=action.locator.stable_message_identity,
                provider_result_id=f"receipt-{len(calls)}",
                updated_locator=updated,
            )

    outcome = _module().execute_historical_model_actions(
        store,
        lambda _account_id: Executor(),
        SimpleNamespace(produce=lambda *_args: pytest.fail("business move is direct")),
        message=message,
        prediction=_accepted_model_prediction(),
        context=context,
        model_id="email-embedding-mlp-ready",
        model_text="exact historical text",
        unsubscribe_entries=(),
        read_after=lambda: SimpleNamespace(
            is_read=provider_state["is_read"],
            provider_folder_name=provider_state["folder"],
        ),
    )

    assert outcome == HistoricalActionResult("moved_and_flagged", is_read=True)
    assert [item.action_type for item in calls] == [
        EmailAction.MOVE,
        EmailAction.FLAG_IMPORTANT,
        EmailAction.MARK_READ,
    ]
    assert calls[1].locator.folder == "Work"
    assert calls[1].locator.uidvalidity == 84
    assert calls[1].locator.uid == 19
    assert calls[2].locator.folder == "Work"
    assert provider_state == {"is_read": True, "folder": "Work"}
    assert all(
        item.locator.rfc_message_id == "<historical@example.com>" for item in calls
    )
    repaired = _module().reconcile_historical_operations(
        store,
        lambda _account_id: pytest.fail("completed effects must not repeat"),
        read_after=lambda _operation: SimpleNamespace(
            is_read=True, provider_folder_name="Work"
        ),
    )
    assert repaired == {"repaired": 1, "deferred": 0}
    assert store.list_processing_historical_operations() == []
    assert len(store.list_historical_classification_history()) == 1
    assert _module().reconcile_historical_operations(
        store,
        lambda _account_id: pytest.fail("terminal operation must not repeat"),
        read_after=lambda _operation: SimpleNamespace(
            is_read=True, provider_folder_name="Work"
        ),
    ) == {"repaired": 0, "deferred": 0}
    old_pending = store.claim_next_direct_action(
        claimed_at="2026-09-07T12:00:01+00:00", account_ids=("account-1",)
    )
    assert old_pending is not None
    assert old_pending.locator.rfc_message_id == "<old-pending@example.com>"


def test_global_workers_claim_historical_plan_in_order_and_terminalize_history(
    tmp_path,
):
    store = EmailStore(tmp_path / "historical-global-sequence.sqlite3")
    message = {
        "messageId": "<historical-global@example.com>",
        "stableMessageIdentity": "stable-historical-global",
        "accountId": "account-1",
        "folder": "INBOX",
        "uidValidity": 42,
        "uid": 27,
        "providerUnread": False,
        "from": {"email": "sender@example.com"},
        "subject": "Historical global sequencing",
        "textBody": "A completed business thread.",
        "date": "2026-09-01T12:00:00+00:00",
    }
    context = AgentScanContext(
        allowed_category_keys=("work", "junk"),
        category_descriptions={"work": {}, "junk": {}},
        folder_targets={"work": "Work"},
        config_version="config-v1",
    )
    persisted = _module().persist_model_primary_classification(
        store,
        SimpleNamespace(produce=lambda *_args: None),
        message=message,
        prediction=_accepted_model_prediction(),
        context=context,
        model_id="model-global-sequence",
        model_text="historical global sequence",
        unsubscribe_entries=(),
        preserve_read=True,
    )
    plan = persisted.persisted["action_plan"]
    action_ids = store.direct_action_ids_for_plan(plan["action_plan_id"])
    store.begin_historical_operation(
        account_id="account-1",
        stable_message_identity=persisted.persisted["stable_message_identity"],
        model_id="model-global-sequence",
        classification_id=persisted.persisted["id"],
        action_plan_id=plan["action_plan_id"],
        action_ids=action_ids,
        predicted_category="work",
        threshold=0.8,
        probability=0.97,
        important=True,
    )
    provider_state = {"folder": "INBOX", "is_read": True}
    effects = []
    effects_lock = __import__("threading").Lock()

    class Executor:
        def execute(self, action):
            with effects_lock:
                effects.append(action.action_type)
                updated = None
                if action.action_type is EmailAction.MOVE:
                    assert provider_state == {"folder": "INBOX", "is_read": True}
                    provider_state.update(folder="Work", is_read=False)
                    updated = StoredEmailLocator(
                        account_id=action.account_id,
                        folder="Work",
                        uidvalidity=84,
                        uid=127,
                        rfc_message_id=action.locator.rfc_message_id,
                        thread_id=action.locator.thread_id,
                        stable_message_identity=action.locator.stable_message_identity,
                    )
                elif action.action_type is EmailAction.FLAG_IMPORTANT:
                    assert provider_state["folder"] == "Work"
                    assert action.locator.folder == "Work"
                else:
                    assert action.action_type is EmailAction.MARK_READ
                    assert effects[-2] is EmailAction.FLAG_IMPORTANT
                    assert action.locator.folder == "Work"
                    provider_state["is_read"] = True
                return ProviderActionResult(
                    status="done",
                    provider_operation=action.action_type.value,
                    provider_target=action.locator.stable_message_identity,
                    provider_result_id=f"receipt-{len(effects)}",
                    updated_locator=updated,
                )

    def drain_global_worker() -> None:
        for _ in range(10):
            result = _module()._run_next_direct_action(
                store,
                lambda _account_id: Executor(),
                available_account_ids=("account-1",),
            )
            if result is None:
                time.sleep(0.002)

    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=3) as pool:
        tuple(pool.map(lambda _index: drain_global_worker(), range(3)))

    assert effects == [
        EmailAction.MOVE,
        EmailAction.FLAG_IMPORTANT,
        EmailAction.MARK_READ,
    ]
    assert provider_state == {"folder": "Work", "is_read": True}
    assert _module().reconcile_historical_operations(
        store,
        lambda _account_id: pytest.fail("global effects must not repeat"),
        read_after=lambda _operation: SimpleNamespace(
            is_read=True, provider_folder_name="Work"
        ),
    ) == {"repaired": 1, "deferred": 0}
    [history] = store.list_historical_classification_history()
    assert history["action_outcome"] == "moved_and_flagged"


@pytest.mark.parametrize("precompleted_count", (3, 2))
def test_historical_outcome_uses_all_durable_receipts_when_global_worker_won_race(
    tmp_path,
    precompleted_count,
):
    store = EmailStore(tmp_path / f"historical-receipts-{precompleted_count}.sqlite3")
    message = {
        "messageId": f"<historical-receipts-{precompleted_count}@example.com>",
        "stableMessageIdentity": f"stable-historical-receipts-{precompleted_count}",
        "accountId": "account-1",
        "folder": "INBOX",
        "uidValidity": 42,
        "uid": 30 + precompleted_count,
        "providerUnread": False,
        "from": {"email": "sender@example.com"},
        "subject": "Historical receipt race",
        "textBody": "A completed business thread.",
        "date": "2026-09-01T12:00:00+00:00",
    }
    context = AgentScanContext(
        allowed_category_keys=("work", "junk"),
        category_descriptions={"work": {}, "junk": {}},
        folder_targets={"work": "Work"},
        config_version="config-v1",
    )
    provider_state = {"folder": "INBOX", "is_read": True}
    effects = []

    class Executor:
        def execute(self, action):
            effects.append(action.action_type)
            updated = None
            if action.action_type is EmailAction.MOVE:
                provider_state.update(folder="Work", is_read=False)
                updated = StoredEmailLocator(
                    account_id=action.account_id,
                    folder="Work",
                    uidvalidity=84,
                    uid=130 + precompleted_count,
                    rfc_message_id=action.locator.rfc_message_id,
                    thread_id=action.locator.thread_id,
                    stable_message_identity=action.locator.stable_message_identity,
                )
            elif action.action_type is EmailAction.MARK_READ:
                provider_state["is_read"] = True
            return ProviderActionResult(
                status="done",
                provider_operation=action.action_type.value,
                provider_target=action.locator.stable_message_identity,
                provider_result_id=f"receipt-{len(effects)}",
                updated_locator=updated,
            )

    _module().persist_model_primary_classification(
        store,
        SimpleNamespace(produce=lambda *_args: None),
        message=message,
        prediction=_accepted_model_prediction(),
        context=context,
        model_id="model-receipt-race",
        model_text="historical receipt race",
        unsubscribe_entries=(),
        preserve_read=True,
    )
    for _ in range(precompleted_count):
        result = _module()._run_next_direct_action(
            store,
            lambda _account_id: Executor(),
            available_account_ids=("account-1",),
        )
        assert result is not None and result.status == "done"

    outcome = _module().execute_historical_model_actions(
        store,
        lambda _account_id: Executor(),
        SimpleNamespace(produce=lambda *_args: None),
        message=message,
        prediction=_accepted_model_prediction(),
        context=context,
        model_id="model-receipt-race",
        model_text="historical receipt race",
        unsubscribe_entries=(),
        read_after=lambda: SimpleNamespace(
            is_read=provider_state["is_read"],
            provider_folder_name=provider_state["folder"],
            folder_role=FolderRole.TRASH,
        ),
        threshold=0.8,
    )

    assert outcome == HistoricalActionResult("moved_and_flagged", is_read=True)
    assert effects == [
        EmailAction.MOVE,
        EmailAction.FLAG_IMPORTANT,
        EmailAction.MARK_READ,
    ]
    assert _module().reconcile_historical_operations(
        store,
        lambda _account_id: pytest.fail("durable effects must not repeat"),
        read_after=lambda _operation: SimpleNamespace(
            is_read=True, provider_folder_name="Work"
        ),
    ) == {"repaired": 1, "deferred": 0}
    assert len(store.list_historical_classification_history()) == 1
    assert _module().reconcile_historical_operations(
        store,
        lambda _account_id: pytest.fail("terminal history must not repeat"),
        read_after=lambda _operation: SimpleNamespace(
            is_read=True, provider_folder_name="Work"
        ),
    ) == {"repaired": 0, "deferred": 0}


def test_failed_historical_mark_read_stays_processing_and_reconcile_finishes_once(
    tmp_path,
):
    store = EmailStore(tmp_path / "historical-mark-read-retry.sqlite3")
    message = {
        "messageId": "<historical-retry@example.com>",
        "stableMessageIdentity": "stable-historical-retry",
        "accountId": "account-1",
        "folder": "INBOX",
        "uidValidity": 42,
        "uid": 8,
        "providerUnread": False,
        "from": {"email": "sender@example.com"},
        "subject": "Historical retry",
        "textBody": "A completed business thread.",
        "date": "2026-09-01T12:00:00+00:00",
    }
    context = AgentScanContext(
        allowed_category_keys=("work", "junk"),
        category_descriptions={"work": {}, "junk": {}},
        folder_targets={"work": "Work"},
        config_version="config-v1",
    )
    provider_state = {"is_read": True, "folder": "INBOX"}
    attempts = []
    successful_effects = []

    class Executor:
        def execute(self, action):
            attempts.append(action.action_type)
            updated = None
            if action.action_type is EmailAction.MOVE:
                provider_state.update(is_read=False, folder="Work")
                updated = StoredEmailLocator(
                    account_id=action.account_id,
                    folder="Work",
                    uidvalidity=84,
                    uid=20,
                    rfc_message_id=action.locator.rfc_message_id,
                    thread_id=action.locator.thread_id,
                    stable_message_identity=action.locator.stable_message_identity,
                )
            if action.action_type is EmailAction.MARK_READ and attempts.count(
                EmailAction.MARK_READ
            ) == 1:
                return ProviderActionResult(
                    status="failed",
                    provider_operation="mark_read",
                    provider_target=action.locator.stable_message_identity,
                    provider_result_id="",
                    error="temporary_mark_read_failure",
                )
            if action.action_type is EmailAction.MARK_READ:
                provider_state["is_read"] = True
            successful_effects.append(action.action_type)
            return ProviderActionResult(
                status="done",
                provider_operation=action.action_type.value,
                provider_target=action.locator.stable_message_identity,
                provider_result_id=f"receipt-{len(successful_effects)}",
                updated_locator=updated,
            )

    outcome = _module().execute_historical_model_actions(
        store,
        lambda _account_id: Executor(),
        SimpleNamespace(produce=lambda *_args: None),
        message=message,
        prediction=_accepted_model_prediction(),
        context=context,
        model_id="email-embedding-mlp-ready",
        model_text="exact historical retry text",
        unsubscribe_entries=(),
        read_after=lambda: SimpleNamespace(
            is_read=provider_state["is_read"],
            provider_folder_name=provider_state["folder"],
        ),
        threshold=0.8,
    )

    assert outcome == HistoricalActionResult("provider_action_failed", is_read=False)
    assert len(store.list_processing_historical_operations()) == 1
    assert store.list_historical_classification_history() == []
    with sqlite3.connect(store.path) as db:
        db.execute(
            "update email_actions set next_attempt_at='2020-01-01T00:00:00+00:00' "
            "where action_type='mark_read'"
        )
    assert _module().reconcile_historical_operations(
        store,
        lambda _account_id: Executor(),
        read_after=lambda _operation: SimpleNamespace(
            is_read=provider_state["is_read"],
            provider_folder_name=provider_state["folder"],
        ),
    ) == {"repaired": 1, "deferred": 0}
    assert successful_effects == [
        EmailAction.MOVE,
        EmailAction.FLAG_IMPORTANT,
        EmailAction.MARK_READ,
    ]
    assert attempts == [
        EmailAction.MOVE,
        EmailAction.FLAG_IMPORTANT,
        EmailAction.MARK_READ,
        EmailAction.MARK_READ,
    ]
    assert len(store.list_historical_classification_history()) == 1
    assert store.list_processing_historical_operations() == []


@pytest.mark.parametrize("crash_after_receipts", (1, 2, 3))
def test_worker_startup_recovers_exact_historical_actions_after_receipt_crash(
    tmp_path, monkeypatch, crash_after_receipts
):
    module = _module()
    database = tmp_path / f"historical-crash-{crash_after_receipts}.sqlite3"
    store = EmailStore(database)
    message = {
        "messageId": "<historical-crash@example.com>",
        "stableMessageIdentity": "stable-historical-crash",
        "accountId": "account-1",
        "folder": "INBOX",
        "uidValidity": 42,
        "uid": 9,
        "providerUnread": False,
        "from": {"email": "sender@example.com"},
        "subject": "Historical crash recovery",
        "textBody": "A completed business thread.",
        "date": "2026-09-01T12:00:00+00:00",
    }
    context = AgentScanContext(
        allowed_category_keys=("work", "junk"),
        category_descriptions={"work": {}, "junk": {}},
        folder_targets={"work": "Work"},
        config_version="config-v1",
    )
    provider_state = {"is_read": True, "folder": "INBOX"}
    successful_effects = []

    class ProcessCrash(BaseException):
        pass

    class Executor:
        def execute(self, action):
            updated = None
            if action.action_type is EmailAction.MOVE:
                provider_state.update(is_read=False, folder="Work")
                updated = StoredEmailLocator(
                    account_id=action.account_id,
                    folder="Work",
                    uidvalidity=84,
                    uid=21,
                    rfc_message_id=action.locator.rfc_message_id,
                    thread_id=action.locator.thread_id,
                    stable_message_identity=action.locator.stable_message_identity,
                )
            elif action.action_type is EmailAction.MARK_READ:
                provider_state["is_read"] = True
            successful_effects.append(action.action_type)
            return ProviderActionResult(
                status="done",
                provider_operation=action.action_type.value,
                provider_target=action.locator.stable_message_identity,
                provider_result_id=f"receipt-{len(successful_effects)}",
                updated_locator=updated,
            )

    original_run = module._run_next_direct_action
    durable_receipts = 0

    def crash_after_receipt(*args, **kwargs):
        nonlocal durable_receipts
        result = original_run(*args, **kwargs)
        if result is not None and result.status == "done":
            durable_receipts += 1
            if durable_receipts == crash_after_receipts:
                raise ProcessCrash()
        return result

    monkeypatch.setattr(module, "_run_next_direct_action", crash_after_receipt)
    with pytest.raises(ProcessCrash):
        module.execute_historical_model_actions(
            store,
            lambda _account_id: Executor(),
            SimpleNamespace(produce=lambda *_args: None),
            message=message,
            prediction=_accepted_model_prediction(),
            context=context,
            model_id="email-embedding-mlp-ready",
            model_text="exact historical crash text",
            unsubscribe_entries=(),
            read_after=lambda: SimpleNamespace(
                is_read=provider_state["is_read"],
                provider_folder_name=provider_state["folder"],
            ),
            threshold=0.8,
        )
    assert len(store.list_processing_historical_operations()) == 1
    assert store.list_historical_classification_history() == []

    monkeypatch.setattr(module, "_run_next_direct_action", original_run)
    reopened = EmailStore(database)
    dependencies = _dependencies(
        [], model=SimpleNamespace(close=lambda: None)
    )
    dependencies.email_store = reopened
    dependencies.reconcile_historical_operations_once = lambda: (
        module.reconcile_historical_operations(
            reopened,
            lambda _account_id: Executor(),
            read_after=lambda _operation: SimpleNamespace(
                is_read=provider_state["is_read"],
                provider_folder_name=provider_state["folder"],
            ),
        )
    )
    module.run_email_worker(
        SimpleNamespace(),
        dependencies=dependencies,
        thread_factory=lambda **_kwargs: SimpleNamespace(start=lambda: None),
        wait=lambda: None,
        output=StringIO(),
    )

    assert successful_effects == [
        EmailAction.MOVE,
        EmailAction.FLAG_IMPORTANT,
        EmailAction.MARK_READ,
    ]
    assert reopened.list_processing_historical_operations() == []
    assert len(reopened.list_historical_classification_history()) == 1


def test_worker_startup_fails_closed_for_provider_effect_without_durable_receipt(
    tmp_path,
):
    module = _module()
    store = EmailStore(tmp_path / "historical-uncertain-effect.sqlite3")
    message = {
        "messageId": "<historical-uncertain@example.com>",
        "stableMessageIdentity": "stable-historical-uncertain",
        "accountId": "account-1",
        "folder": "INBOX",
        "uidValidity": 42,
        "uid": 10,
        "providerUnread": False,
        "from": {"email": "sender@example.com"},
        "subject": "Historical uncertain effect",
        "textBody": "A completed business thread.",
        "date": "2026-09-01T12:00:00+00:00",
    }
    provider_state = {"is_read": True, "folder": "INBOX"}
    effects = []

    class ProcessCrash(BaseException):
        pass

    class Executor:
        def execute(self, action):
            if action.action_type is EmailAction.MOVE:
                provider_state.update(is_read=False, folder="Work")
                effects.append(EmailAction.MOVE)
                return ProviderActionResult(
                    status="done",
                    provider_operation="move",
                    provider_target=action.locator.stable_message_identity,
                    provider_result_id="receipt-move",
                    updated_locator=StoredEmailLocator(
                        account_id=action.account_id,
                        folder="Work",
                        uidvalidity=84,
                        uid=22,
                        rfc_message_id=action.locator.rfc_message_id,
                        thread_id=action.locator.thread_id,
                        stable_message_identity=action.locator.stable_message_identity,
                    ),
                )
            if action.action_type is EmailAction.FLAG_IMPORTANT:
                effects.append(EmailAction.FLAG_IMPORTANT)
                return ProviderActionResult(
                    status="done",
                    provider_operation="flag_important",
                    provider_target=action.locator.stable_message_identity,
                    provider_result_id="receipt-flag",
                )
            effects.append(EmailAction.MARK_READ)
            provider_state["is_read"] = True
            raise ProcessCrash()

    with pytest.raises(ProcessCrash):
        module.execute_historical_model_actions(
            store,
            lambda _account_id: Executor(),
            SimpleNamespace(produce=lambda *_args: None),
            message=message,
            prediction=_accepted_model_prediction(),
            context=AgentScanContext(
                allowed_category_keys=("work", "junk"),
                category_descriptions={"work": {}, "junk": {}},
                folder_targets={"work": "Work"},
                config_version="config-v1",
            ),
            model_id="email-embedding-mlp-ready",
            model_text="exact historical uncertain text",
            unsubscribe_entries=(),
            read_after=lambda: SimpleNamespace(
                is_read=provider_state["is_read"],
                provider_folder_name=provider_state["folder"],
            ),
            threshold=0.8,
        )
    reopened = EmailStore(store.path)
    reconciliation = []
    dependencies = _dependencies([], model=SimpleNamespace(close=lambda: None))
    dependencies.email_store = reopened
    dependencies.reconcile_historical_operations_once = lambda: reconciliation.append(
        module.reconcile_historical_operations(
            reopened,
            lambda _account_id: Executor(),
            read_after=lambda _operation: SimpleNamespace(
                is_read=provider_state["is_read"],
                provider_folder_name=provider_state["folder"],
            ),
        )
    )
    module.run_email_worker(
        SimpleNamespace(),
        dependencies=dependencies,
        thread_factory=lambda **_kwargs: SimpleNamespace(start=lambda: None),
        wait=lambda: None,
        output=StringIO(),
    )

    assert reconciliation == [{"repaired": 0, "deferred": 1}]
    assert effects == [
        EmailAction.MOVE,
        EmailAction.FLAG_IMPORTANT,
        EmailAction.MARK_READ,
    ]
    assert len(reopened.list_processing_historical_operations()) == 1
    assert reopened.list_historical_classification_history() == []


def test_manual_historical_batch_persists_prediction_threshold_and_outcome(tmp_path):
    import numpy as np

    from app.email_embedding_cache import EmbeddingCacheKey

    store = EmailStore(tmp_path / "historical-history.sqlite3")
    candidate = HistoricalClassificationCandidate(
        stable_message_identity="stable-history",
        normalized_text="exact historical text",
    )
    key = EmbeddingCacheKey.for_text(
        normalized_text=candidate.normalized_text,
        input_schema_version="input-v3",
        embedding_model_id="jina",
        embedding_revision="r17",
    )
    model = SimpleNamespace(
        input_schema_version="input-v3",
        embedding_model_id="jina",
        embedding_revision="r17",
        category_thresholds={"work": 0.8},
        predict=lambda _vector: _accepted_model_prediction(important=False),
    )
    state = HistoricalClassificationState(
        folder_role=FolderRole.INBOX,
        configured_unclassified_source=False,
        is_read=True,
        is_unclassified=True,
    )

    outcomes = _module().run_manual_historical_batch(
        email_store=store,
        model_id="email-embedding-mlp-ready",
        model=model,
        cache=SimpleNamespace(
            get=lambda current_key: np.array([1.0], dtype=np.float32)
            if current_key == key
            else None
        ),
        historically_eligible={"work": True},
        candidates=(candidate,),
        read_state=lambda _candidate: state,
        execute=lambda *_args: HistoricalActionResult("moved", is_read=True),
    )

    history = store.list_historical_classification_history()
    assert len(outcomes) == len(history) == 1
    assert history[0]["model_id"] == "email-embedding-mlp-ready"
    assert history[0]["predicted_category"] == "work"
    assert history[0]["threshold"] == 0.8
    assert history[0]["action_outcome"] == "moved"


def test_historical_job_is_not_an_automatic_worker_component():
    dependencies = _dependencies([], accounts=({"account_id": "account-1"},))
    dependencies.run_historical_once = lambda: pytest.fail(
        "manual history must not enter scan loop"
    )

    components = _module().email_worker_components(
        dependencies,
        accounts=({"account_id": "account-1"},),
        active_model=object(),
    )

    assert [name for name, _target in components] == [
        "email-scan-actions",
        "email-agent-consumer",
        "email-training",
    ]


def test_run_historical_once_uses_provider_rereads_cached_batch_and_durable_history(
    tmp_path, monkeypatch
):
    import numpy as np

    from app.email_classifier_model import email_message_to_text
    from app.email_embedding_cache import EmbeddingCache, EmbeddingCacheKey
    from app.email_embedding_classifier import DescriptionAwareEmailClassifier
    from app.email_imap_readonly import ImapUidBatch
    from app.email_model_registry import EmailModelRegistry

    module = _module()
    account = {
        "account_id": "account-1",
        "enabled": True,
        "scan_folders": ["INBOX", "Work"],
    }
    inbox_messages = [
        {
            "messageId": f"<history-{index}@example.com>",
            "accountId": "account-1",
            "folder": "INBOX",
            "uidValidity": 42,
            "uid": index + 1,
            "providerUnread": index < 50,
            "from": {"email": "sender@example.com"},
            "subject": f"Historical {index}",
            "textBody": "A completed business thread.",
            "date": "2026-09-01T12:00:00+00:00",
        }
        for index in range(52)
    ]
    business_message = {
        "messageId": "<existing-business@example.com>",
        "accountId": "account-1",
        "folder": "Work",
        "uidValidity": 84,
        "uid": 500,
        "providerUnread": False,
        "from": {"email": "sender@example.com"},
        "subject": "Already classified",
        "textBody": "Must remain untouched.",
        "date": "2026-09-01T12:00:00+00:00",
    }
    provider_messages = [*inbox_messages, business_message]
    fetches = []
    action_calls = []

    class Source:
        account_id = "account-1"

        def list_folders(self):
            return (
                ProviderFolder("inbox-id", "INBOX", FolderRole.INBOX),
                ProviderFolder("work-id", "Work", FolderRole.UNBOUND),
            )

        def fetch_uid_batch(self, mailbox, **kwargs):
            fetches.append((mailbox, dict(kwargs)))
            current = [
                message
                for message in provider_messages
                if message["folder"] == mailbox
                and int(message["uid"] or 0) > int(kwargs["last_seen_uid"])
                and (
                    not kwargs.get("unread_only", False)
                    or message["providerUnread"] is True
                )
            ]
            return ImapUidBatch(
                account_id=self.account_id,
                folder=mailbox,
                uidvalidity=42 if mailbox == "INBOX" else 84,
                previous_uidvalidity=kwargs["cursor_uidvalidity"],
                messages=tuple(current[: kwargs["limit"]]),
            )

        def logout(self):
            return None

    class Executor:
        def execute(self, action):
            action_calls.append(action)
            message = next(
                item
                for item in provider_messages
                if f"account-1:message-id:{item['messageId']}"
                == action.locator.stable_message_identity
            )
            updated = None
            if action.action_type is EmailAction.MOVE:
                assert message["providerUnread"] is False
                message["folder"] = "Work"
                message["uidValidity"] = 84
                message["uid"] = 100 + int(action.locator.uid)
                updated = StoredEmailLocator(
                    account_id="account-1",
                    folder="Work",
                    uidvalidity=84,
                    uid=int(message["uid"]),
                    rfc_message_id=str(message["messageId"]),
                    thread_id=None,
                    stable_message_identity=action.locator.stable_message_identity,
                )
            elif action.action_type is EmailAction.FLAG_IMPORTANT:
                assert action.locator.folder == "Work"
                assert action.locator.uidvalidity == 84
                assert message["providerUnread"] is False
            else:
                assert action.action_type is EmailAction.MARK_READ
                assert action.locator.folder == "Work"
                message["providerUnread"] = False
            return ProviderActionResult(
                status="done",
                provider_operation=action.action_type.value,
                provider_target=action.locator.stable_message_identity,
                provider_result_id=f"receipt-{len(action_calls)}",
                updated_locator=updated,
            )

    monkeypatch.setattr(
        module, "_build_email_source_factory", lambda _settings: lambda _account: Source()
    )
    monkeypatch.setattr(module, "_build_agent_orchestrator", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(
        "app.agent_runtime_production.build_production_routed_codex_execution",
        lambda **_kwargs: object(),
    )
    model = SimpleNamespace(
        enabled_categories=("work", "junk"),
        dimension=2,
        input_schema_version="input-v3",
        embedding_model_id="jina",
        embedding_revision="r17",
        category_thresholds={"work": 0.8, "junk": 0.9},
        predict=lambda _vector: EmbeddingModelPrediction(
            category="work",
            category_probability=0.97,
            category_probabilities={"work": 0.97, "junk": 0.03},
            category_accepted=True,
            important=True,
            important_probability=0.96,
            head_ms=1.0,
        ),
    )
    monkeypatch.setattr(
        DescriptionAwareEmailClassifier,
        "load",
        classmethod(lambda _cls, _path: model),
    )
    monkeypatch.setattr(
        EmailModelRegistry,
        "get_staged_evidence",
        lambda _self, model_id: {
            "model_id": model_id,
            "historical_eligibility": {
                "categories": {
                    "work": {"eligible": True},
                    "junk": {"eligible": False},
                }
            },
        },
    )
    settings = SimpleNamespace(
        db_path=tmp_path / "historical-production.sqlite3",
        workspace=tmp_path,
        dry_run=False,
    )
    bootstrap = module.build_email_worker_dependencies(
        settings,
        direct_action_executor_factory=lambda _account_id: Executor(),
    )
    store = bootstrap.email_store
    monkeypatch.setattr(
        store,
        "list_category_configs",
        lambda: [
            {
                "category_key": "work",
                "enabled": True,
                "core_description": "Business work.",
                "include": ["delivery"],
                "exclude": ["promotion"],
                "config_version": "config-v1",
            },
            {
                "category_key": "junk",
                "enabled": True,
                "core_description": "Unwanted mail.",
                "include": ["promotion"],
                "exclude": ["delivery"],
                "config_version": "config-v1",
            },
        ],
    )
    monkeypatch.setattr(
        store,
        "list_account_folder_bindings",
        lambda: [
            {
                "account_id": "account-1",
                "category_key": "work",
                "provider_folder_id": "work-id",
                "provider_folder_name": "Work",
                "binding_status": "active",
            }
        ],
    )
    cache = EmbeddingCache(tmp_path / "email-models", dimension=2)
    for message in (inbox_messages[50],):
        normalized_text = email_message_to_text(message)
        cache.put(
            EmbeddingCacheKey.for_text(
                normalized_text=normalized_text,
                input_schema_version="input-v3",
                embedding_model_id="jina",
                embedding_revision="r17",
            ),
            np.array([1.0, 0.0], dtype=np.float32),
        )
    dependencies = bootstrap.build_dependencies((account,), object())

    outcomes = dependencies.run_historical_once(
        "email-embedding-mlp-ready", account_id="account-1"
    )

    assert outcomes == ()
    assert action_calls == []
    assert fetches[0] == (
        "INBOX",
        {
            "cursor_uidvalidity": None,
            "last_seen_uid": 0,
            "limit": 50,
            "unread_only": False,
        },
    )
    assert not any(
        mailbox == "Work" and values["cursor_uidvalidity"] is None
        for mailbox, values in fetches
    )
    assert inbox_messages[0]["folder"] == "INBOX"
    assert inbox_messages[0]["providerUnread"] is True
    assert business_message["folder"] == "Work"
    assert store.get_classification_by_stable_identity(
        "account-1:message-id:<history-0@example.com>"
    ) is None
    assert store.get_classification_by_stable_identity(
        "account-1:message-id:<existing-business@example.com>"
    ) is None
    assert inbox_messages[50]["folder"] == "INBOX"
    durable = EmailStore(settings.db_path).list_historical_classification_history()
    assert durable == []

    after_restart = bootstrap.build_dependencies((account,), object())
    second = after_restart.run_historical_once(
        "email-embedding-mlp-ready", account_id="account-1"
    )
    assert [item.stable_message_identity for item in second] == [
        "account-1:message-id:<history-50@example.com>"
    ]
    assert len(action_calls) == 3
    assert inbox_messages[50]["folder"] == "Work"
    durable = EmailStore(settings.db_path).list_historical_classification_history()
    assert len(durable) == 1
    for message in (inbox_messages[50],):
        stable_identity = f"account-1:message-id:{message['messageId']}"
        persisted = store.get_classification_by_stable_identity(stable_identity)
        assert persisted is not None
        assert persisted["folder"] == "Work"
        assert persisted["uidvalidity"] == 84

    inbox_messages[0]["providerUnread"] = False
    normalized_text = email_message_to_text(inbox_messages[0])
    cache.put(
        EmbeddingCacheKey.for_text(
            normalized_text=normalized_text,
            input_schema_version="input-v3",
            embedding_model_id="jina",
            embedding_revision="r17",
        ),
        np.array([1.0, 0.0], dtype=np.float32),
    )
    with sqlite3.connect(settings.db_path) as db:
        db.execute(
            "update email_historical_candidates "
            "set next_retry_at='2020-01-01T00:00:00+00:00' "
            "where stable_message_identity=?",
            ("account-1:message-id:<history-0@example.com>",),
        )
    third = dependencies.run_historical_once(
        "email-embedding-mlp-ready", account_id="account-1"
    )
    assert [item.stable_message_identity for item in third] == [
        "account-1:message-id:<history-0@example.com>"
    ]
    assert fetches[-1][1]["last_seen_uid"] > 0


def test_historical_deferred_queue_is_fair_durable_and_not_a_busy_loop(tmp_path):
    database = tmp_path / "historical-fair-queue.sqlite3"
    store = EmailStore(database)
    candidates = tuple(
        HistoricalClassificationCandidate(
            stable_message_identity=f"stable-fair-{index:02d}",
            normalized_text=f"historical fair {index}",
            provider_message={
                "messageId": f"<fair-{index}@example.com>",
                "accountId": "account-1",
                "folder": "INBOX",
                "uidValidity": 42,
                "uid": index + 1,
                "providerUnread": index < 50,
                "subject": f"Fair {index}",
                "textBody": "body",
            },
        )
        for index in range(51)
    )
    store.enqueue_historical_page(
        account_id="account-1",
        folder="INBOX",
        model_id="model-fair",
        uidvalidity=42,
        last_seen_uid=51,
        candidates=candidates,
    )

    first_run = store.list_historical_candidates(
        account_id="account-1", folder="INBOX", model_id="model-fair", limit=50
    )
    assert [item["uid"] for item in first_run] == list(range(1, 51))
    for item in first_run:
        store.set_historical_candidate_state(
            account_id="account-1",
            folder="INBOX",
            model_id="model-fair",
            stable_message_identity=item["stable_message_identity"],
            state="deferred",
            reason="unread",
        )

    restarted = EmailStore(database)
    second_run = restarted.list_historical_candidates(
        account_id="account-1", folder="INBOX", model_id="model-fair", limit=50
    )
    assert [item["uid"] for item in second_run] == [51]
    restarted.set_historical_candidate_state(
        account_id="account-1",
        folder="INBOX",
        model_id="model-fair",
        stable_message_identity="stable-fair-50",
        state="terminal",
        reason="moved",
    )
    assert restarted.list_historical_candidates(
        account_id="account-1", folder="INBOX", model_id="model-fair", limit=50
    ) == []

    restarted._now = lambda: "2099-01-01T00:00:00+00:00"
    retry_run = restarted.list_historical_candidates(
        account_id="account-1", folder="INBOX", model_id="model-fair", limit=2
    )
    assert [item["uid"] for item in retry_run] == [1, 2]
    assert all(item["attempted_at"] for item in retry_run)
    assert all(item["next_retry_at"] for item in retry_run)


def test_historical_queue_persists_only_minimal_json_safe_projection(tmp_path):
    from app.email_imap_readonly import _normalized_message_record
    from app.email_important import ImportantSignals
    from app.email_classifier_model import email_message_to_text

    raw = (
        b"From: Private Sender <private@example.test>\r\n"
        b"To: derek@example.test\r\n"
        b"Subject: PRIVATE-SUBJECT-MARKER\r\n"
        b"Message-ID: <minimal-queue@example.test>\r\n"
        b"List-Unsubscribe: <https://private.example.test/u?token=PRIVATE-TOKEN>\r\n"
        b"\r\n"
    )
    parsed = BytesParser(policy=policy.default).parsebytes(raw)
    message = _normalized_message_record(
        parsed,
        body="PRIVATE-BODY-MARKER",
        body_html="<b>PRIVATE-HTML-MARKER</b>",
        attachments=[],
        account_id="account-1",
        folder="INBOX",
        uidvalidity=42,
        uid=9,
        important_signals=ImportantSignals(("$Important",), True),
        provider_unread=False,
    )
    candidate = HistoricalClassificationCandidate(
        stable_message_identity=str(message["stableMessageIdentity"]),
        normalized_text=email_message_to_text(message),
        provider_message=message,
    )
    database = tmp_path / "minimal-historical-queue.sqlite3"
    store = EmailStore(database)

    store.enqueue_historical_page(
        account_id="account-1",
        folder="INBOX",
        model_id="model-minimal",
        uidvalidity=42,
        last_seen_uid=9,
        candidates=(candidate,),
    )

    queued = store.list_historical_candidates(
        account_id="account-1", folder="INBOX", model_id="model-minimal"
    )
    assert queued[0]["provider_message"] == {
        "accountId": "account-1",
        "folder": "INBOX",
        "uidValidity": 42,
        "uid": 9,
        "messageId": "<minimal-queue@example.test>",
        "threadId": message["threadId"],
        "stableMessageIdentity": message["stableMessageIdentity"],
    }
    database_bytes = database.read_bytes()
    for forbidden in (
        b"PRIVATE-BODY-MARKER",
        b"PRIVATE-HTML-MARKER",
        b"PRIVATE-TOKEN",
        b"PRIVATE-SUBJECT-MARKER",
        b"$Important",
        b"private@example.test",
    ):
        assert forbidden not in database_bytes


def test_v29_historical_queue_migration_securely_redacts_legacy_provider_record(
    tmp_path,
):
    database = tmp_path / "historical-v29-redaction.sqlite3"
    store = EmailStore(database)
    candidate = HistoricalClassificationCandidate(
        stable_message_identity="stable-legacy-private",
        normalized_text="safe initial input",
        provider_message={
            "accountId": "account-1",
            "folder": "INBOX",
            "uidValidity": 42,
            "uid": 9,
            "messageId": "<legacy-private@example.test>",
            "threadId": "thread-legacy-private",
            "stableMessageIdentity": "stable-legacy-private",
        },
    )
    store.enqueue_historical_page(
        account_id="account-1",
        folder="INBOX",
        model_id="model-legacy",
        uidvalidity=42,
        last_seen_uid=9,
        candidates=(candidate,),
    )
    legacy = {
        **candidate.provider_message,
        "subject": "LEGACY-PRIVATE-SUBJECT",
        "textBody": "LEGACY-PRIVATE-BODY",
        "listUnsubscribe": "<https://private.test/u?token=LEGACY-PRIVATE-TOKEN>",
    }
    with sqlite3.connect(database) as db:
        db.execute("pragma secure_delete = on")
        db.execute("delete from email_schema_migrations")
        db.execute(
            "insert into email_schema_migrations(version, applied_at) "
            "values (29, '2026-09-08T00:00:00+00:00')"
        )
        db.execute(
            "update email_historical_candidates "
            "set normalized_text=?, provider_message_json=?",
            ("LEGACY-PRIVATE-BODY", json.dumps(legacy)),
        )

    migrated = EmailStore(database)
    [queued] = migrated.list_historical_candidates(
        account_id="account-1", folder="INBOX", model_id="model-legacy"
    )
    assert queued["normalized_input_hash"] == sha256(
        b"LEGACY-PRIVATE-BODY"
    ).hexdigest()
    database_bytes = database.read_bytes()
    assert b"LEGACY-PRIVATE-BODY" not in database_bytes
    assert b"LEGACY-PRIVATE-TOKEN" not in database_bytes
    assert b"LEGACY-PRIVATE-SUBJECT" not in database_bytes


def test_historical_unsubscribe_input_is_reread_from_provider_not_queue(tmp_path):
    from app.email_imap_readonly import ImapUidBatch

    store = EmailStore(tmp_path / "historical-provider-reread.sqlite3")
    private_url = "https://private.example.test/u?token=ONLY-PROVIDER-HAS-THIS"
    full_message = {
        "accountId": "account-1",
        "folder": "INBOX",
        "uidValidity": 42,
        "uid": 9,
        "messageId": "<provider-reread@example.test>",
        "threadId": "thread-reread",
        "stableMessageIdentity": (
            "account-1:message-id:<provider-reread@example.test>"
        ),
        "providerUnread": False,
        "subject": "Private",
        "textBody": "Private provider body",
        "listUnsubscribe": f"<{private_url}>",
        "listUnsubscribePost": "",
    }
    candidate = HistoricalClassificationCandidate(
        stable_message_identity=str(full_message["stableMessageIdentity"]),
        normalized_text="normalized private provider body",
        provider_message=full_message,
    )
    store.enqueue_historical_page(
        account_id="account-1",
        folder="INBOX",
        model_id="model-reread",
        uidvalidity=42,
        last_seen_uid=9,
        candidates=(candidate,),
    )
    [queued] = store.list_historical_candidates(
        account_id="account-1", folder="INBOX", model_id="model-reread"
    )
    queued_candidate = HistoricalClassificationCandidate(
        stable_message_identity=queued["stable_message_identity"],
        normalized_text=queued["normalized_text"],
        provider_message=queued["provider_message"],
    )
    reads = []

    class Source:
        def fetch_uid_batch(self, mailbox, **kwargs):
            reads.append((mailbox, kwargs))
            return ImapUidBatch(
                account_id="account-1",
                folder="INBOX",
                uidvalidity=42,
                previous_uidvalidity=42,
                messages=[full_message],
            )

        def logout(self):
            return None

    reread = _module()._reread_historical_candidate_message(
        lambda _account: Source(),
        {"account_id": "account-1"},
        queued_candidate,
    )
    entries = extract_unsubscribe_entries(
        list_unsubscribe=str(reread["listUnsubscribe"])
    )

    assert len(reads) == 1
    assert entries[0].private_url == private_url
    assert "listUnsubscribe" not in queued["provider_message"]


def test_due_deferred_candidates_receive_fixed_quota_despite_new_pending(tmp_path):
    database = tmp_path / "historical-deferred-quota.sqlite3"
    store = EmailStore(database)

    def enqueue(prefix: str, start_uid: int, count: int) -> None:
        store.enqueue_historical_page(
            account_id="account-1",
            folder="INBOX",
            model_id="model-fair",
            uidvalidity=42,
            last_seen_uid=start_uid + count,
            candidates=tuple(
                HistoricalClassificationCandidate(
                    stable_message_identity=f"{prefix}-{index}",
                    normalized_text=f"text {prefix} {index}",
                    provider_message={
                        "accountId": "account-1",
                        "folder": "INBOX",
                        "uidValidity": 42,
                        "uid": start_uid + index,
                        "messageId": f"<{prefix}-{index}@example.test>",
                        "threadId": f"thread-{prefix}-{index}",
                        "stableMessageIdentity": f"{prefix}-{index}",
                    },
                )
                for index in range(count)
            ),
        )

    enqueue("deferred", 1, 6)
    for index in range(6):
        store.set_historical_candidate_state(
            account_id="account-1",
            folder="INBOX",
            model_id="model-fair",
            stable_message_identity=f"deferred-{index}",
            state="deferred",
            reason="unread",
        )
    store._now = lambda: "2099-01-01T00:00:00+00:00"

    selected_deferred = []
    for round_index in range(3):
        enqueue(f"pending-{round_index}", 100 + round_index * 8, 8)
        batch = store.list_historical_candidates(
            account_id="account-1", folder="INBOX", model_id="model-fair", limit=8
        )
        due = [item for item in batch if item["state"] == "deferred"]
        assert len(due) >= 2
        selected_deferred.extend(item["stable_message_identity"] for item in due[:2])
        for item in batch:
            store.set_historical_candidate_state(
                account_id="account-1",
                folder="INBOX",
                model_id="model-fair",
                stable_message_identity=item["stable_message_identity"],
                state="terminal",
                reason="selected",
            )

    assert set(selected_deferred) == {f"deferred-{index}" for index in range(6)}


def test_agent_business_result_plans_move_then_optional_flag() -> None:
    from app.email_classifier_agent import AgentClassificationResult

    plan = _module()._agent_classification_action_plan(
        classification_id=71,
        account_id="account-1",
        result=AgentClassificationResult(
            category="work",
            important=True,
            certainty="certain",
            confidence=0.96,
            reason="Customer delivery decision.",
            unsubscribe_candidate_index=None,
            unsubscribe_url=None,
        ),
        folder_targets={"work": "Work"},
        config_version="config-v1",
        created_at=datetime(2026, 9, 8, tzinfo=timezone.utc),
    )

    assert plan is not None
    assert plan.actions == (EmailAction.MOVE, EmailAction.FLAG_IMPORTANT)
    assert plan.action_parameters[EmailAction.MOVE] == {"target_folder": "Work"}
    assert EmailAction.MARK_READ not in plan.actions
    assert not plan.agent_actions


def test_agent_junk_with_exact_candidate_plans_audited_unsubscribe_then_trash() -> None:
    from app.email_classifier_agent import (
        AgentClassificationResult,
        durable_agent_classification_result,
    )

    entries = extract_unsubscribe_entries(
        list_unsubscribe="<https://example.com/unsubscribe?id=exact>"
    )
    result = AgentClassificationResult(
        category="junk",
        important=False,
        certainty="certain",
        confidence=0.98,
        reason="Unrequested promotion with no retention value.",
        unsubscribe_candidate_index=0,
        unsubscribe_url="https://example.com/unsubscribe?id=exact",
    )

    plan = _module()._agent_classification_action_plan(
        classification_id=72,
        account_id="account-1",
        result=result,
        unsubscribe_selection=durable_agent_classification_result(result, entries),
        folder_targets={},
        config_version="config-v1",
        created_at=datetime(2026, 9, 8, tzinfo=timezone.utc),
    )

    assert plan is not None
    assert plan.actions == (EmailAction.UNSUBSCRIBE, EmailAction.TRASH)
    assert plan.action_parameters[EmailAction.UNSUBSCRIBE] == {
        "candidate_index": 0,
        "candidate_source": "header_https",
        "candidate_digest": entries[0].reference.removeprefix("unsubscribe-entry:"),
        "candidate_reference": entries[0].reference,
    }


def test_agent_junk_without_candidate_plans_direct_trash() -> None:
    from app.email_classifier_agent import AgentClassificationResult

    result = AgentClassificationResult(
        category="junk",
        important=False,
        certainty="certain",
        confidence=0.98,
        reason="Unwanted mail without a reliable unsubscribe entry.",
        unsubscribe_candidate_index=None,
        unsubscribe_url=None,
    )
    plan = _module()._agent_classification_action_plan(
        classification_id=74,
        account_id="account-1",
        result=result,
        unsubscribe_selection=None,
        folder_targets={},
        config_version="config-v1",
        created_at=datetime(2026, 9, 8, tzinfo=timezone.utc),
    )

    assert plan is not None
    assert plan.actions == (EmailAction.TRASH,)


def test_historical_junk_without_unsubscribe_trashes_then_restores_read() -> None:
    from app.email_classifier_agent import AgentClassificationResult

    plan = _module()._agent_classification_action_plan(
        classification_id=741,
        account_id="account-1",
        result=AgentClassificationResult(
            category="junk",
            important=False,
            certainty="certain",
            confidence=0.98,
            reason="Historical junk.",
            unsubscribe_candidate_index=None,
            unsubscribe_url=None,
        ),
        unsubscribe_selection=None,
        folder_targets={},
        config_version="config-v1",
        created_at=datetime(2026, 9, 8, tzinfo=timezone.utc),
        preserve_read=True,
    )

    assert plan is not None
    assert plan.actions == (EmailAction.TRASH, EmailAction.MARK_READ)


def test_historical_junk_with_unsubscribe_orders_task_trash_then_mark_read() -> None:
    from app.email_classifier_agent import (
        AgentClassificationResult,
        durable_agent_classification_result,
    )

    entries = extract_unsubscribe_entries(
        list_unsubscribe="<https://example.com/unsubscribe?id=historical>"
    )
    result = AgentClassificationResult(
        category="junk",
        important=False,
        certainty="certain",
        confidence=0.98,
        reason="Historical junk.",
        unsubscribe_candidate_index=0,
        unsubscribe_url=entries[0].private_url,
    )
    plan = _module()._agent_classification_action_plan(
        classification_id=742,
        account_id="account-1",
        result=result,
        unsubscribe_selection=durable_agent_classification_result(result, entries),
        folder_targets={},
        config_version="config-v1",
        created_at=datetime(2026, 9, 8, tzinfo=timezone.utc),
        preserve_read=True,
    )

    assert plan is not None
    assert plan.actions == (
        EmailAction.UNSUBSCRIBE,
        EmailAction.TRASH,
        EmailAction.MARK_READ,
    )


def _historical_junk_fixture():
    message = {
        "messageId": "<historical-junk@example.com>",
        "stableMessageIdentity": "stable-historical-junk",
        "accountId": "account-1",
        "folder": "INBOX",
        "uidValidity": 42,
        "uid": 88,
        "providerUnread": False,
        "from": {"email": "sender@example.com"},
        "subject": "Historical junk",
        "textBody": "Unwanted promotion.",
        "date": "2026-09-01T12:00:00+00:00",
    }
    context = AgentScanContext(
        allowed_category_keys=("work", "junk"),
        category_descriptions={"work": {}, "junk": {}},
        folder_targets={"work": "Work"},
        config_version="config-v1",
    )
    return message, context


def test_historical_junk_trash_changed_locator_then_mark_read_and_terminal(tmp_path):
    module = _module()
    store = EmailStore(tmp_path / "historical-junk-read.sqlite3")
    message, context = _historical_junk_fixture()
    provider_state = {"folder": "INBOX", "is_read": True}
    effects = []

    class Executor:
        def execute(self, action):
            effects.append((action.action_type, action.locator.folder))
            updated = None
            if action.action_type is EmailAction.TRASH:
                provider_state.update(folder="Trash", is_read=False)
                updated = StoredEmailLocator(
                    account_id=action.account_id,
                    folder="Trash",
                    uidvalidity=84,
                    uid=188,
                    rfc_message_id=action.locator.rfc_message_id,
                    thread_id=action.locator.thread_id,
                    stable_message_identity=action.locator.stable_message_identity,
                )
            else:
                assert action.action_type is EmailAction.MARK_READ
                provider_state["is_read"] = True
            return ProviderActionResult(
                status="done",
                provider_operation=action.action_type.value,
                provider_target=action.locator.stable_message_identity,
                provider_result_id=f"receipt-{len(effects)}",
                updated_locator=updated,
            )

    outcome = module.execute_historical_model_actions(
        store,
        lambda _account_id: Executor(),
        SimpleNamespace(produce=lambda *_args: None),
        message=message,
        prediction=_accepted_model_prediction(category="junk", important=False),
        context=context,
        model_id="model-junk",
        model_text="historical junk",
        unsubscribe_entries=(),
        read_after=lambda: SimpleNamespace(
            is_read=provider_state["is_read"],
            provider_folder_name=provider_state["folder"],
            folder_role=FolderRole.TRASH,
        ),
    )

    assert outcome == HistoricalActionResult("trashed", is_read=True)
    assert effects == [
        (EmailAction.TRASH, "INBOX"),
        (EmailAction.MARK_READ, "Trash"),
    ]
    assert module.reconcile_historical_operations(
        store,
        lambda _account_id: pytest.fail("completed junk effects must not repeat"),
        read_after=lambda _operation: SimpleNamespace(
            is_read=True,
            provider_folder_name="Trash",
            folder_role=FolderRole.TRASH,
        ),
    ) == {"repaired": 1, "deferred": 0}
    assert len(store.list_historical_classification_history()) == 1


def test_historical_junk_receipts_do_not_terminalize_until_provider_is_in_trash(
    tmp_path,
):
    module = _module()
    store = EmailStore(tmp_path / "historical-junk-folder-readback.sqlite3")
    message, context = _historical_junk_fixture()
    provider_observation = {
        "folder": "INBOX",
        "folder_role": FolderRole.INBOX,
        "is_read": True,
    }
    effects = []

    class Executor:
        def execute(self, action):
            effects.append(action.action_type)
            updated = None
            if action.action_type is EmailAction.TRASH:
                updated = StoredEmailLocator(
                    account_id=action.account_id,
                    folder="Trash",
                    uidvalidity=84,
                    uid=288,
                    rfc_message_id=action.locator.rfc_message_id,
                    thread_id=action.locator.thread_id,
                    stable_message_identity=action.locator.stable_message_identity,
                )
            return ProviderActionResult(
                status="done",
                provider_operation=action.action_type.value,
                provider_target=action.locator.stable_message_identity,
                provider_result_id=f"receipt-{len(effects)}",
                updated_locator=updated,
            )

    def readback():
        return SimpleNamespace(
            is_read=provider_observation["is_read"],
            provider_folder_name=provider_observation["folder"],
            folder_role=provider_observation["folder_role"],
        )

    outcome = module.execute_historical_model_actions(
        store,
        lambda _account_id: Executor(),
        SimpleNamespace(produce=lambda *_args: None),
        message=message,
        prediction=_accepted_model_prediction(category="junk", important=False),
        context=context,
        model_id="model-junk-folder-readback",
        model_text="historical junk folder readback",
        unsubscribe_entries=(),
        read_after=readback,
    )

    assert outcome == HistoricalActionResult(
        "provider_folder_not_verified", is_read=True
    )
    assert effects == [EmailAction.TRASH, EmailAction.MARK_READ]
    assert len(store.list_processing_historical_operations()) == 1
    assert store.list_historical_classification_history() == []
    assert module.reconcile_historical_operations(
        store,
        lambda _account_id: pytest.fail("durable effects must not repeat"),
        read_after=lambda _operation: readback(),
    ) == {"repaired": 0, "deferred": 1}

    provider_observation.update(folder="Trash", folder_role=FolderRole.TRASH)
    assert module.reconcile_historical_operations(
        store,
        lambda _account_id: pytest.fail("durable effects must not repeat"),
        read_after=lambda _operation: readback(),
    ) == {"repaired": 1, "deferred": 0}
    assert store.list_processing_historical_operations() == []
    [history] = store.list_historical_classification_history()
    assert history["action_outcome"] == "trashed"


def test_historical_unsubscribe_receipt_precedes_trash_and_mark_read(tmp_path):
    module = _module()
    store = EmailStore(tmp_path / "historical-junk-unsubscribe.sqlite3")
    message, context = _historical_junk_fixture()
    entry = extract_unsubscribe_entries(
        list_unsubscribe="<https://example.com/unsubscribe?id=historical>"
    )[0]
    produced = []
    effects = []
    provider_state = {"folder": "INBOX", "is_read": True}

    class Executor:
        def execute(self, action):
            effects.append(action.action_type)
            updated = None
            if action.action_type is EmailAction.TRASH:
                provider_state.update(folder="Trash", is_read=False)
                updated = StoredEmailLocator(
                    account_id=action.account_id,
                    folder="Trash",
                    uidvalidity=84,
                    uid=188,
                    rfc_message_id=action.locator.rfc_message_id,
                    thread_id=action.locator.thread_id,
                    stable_message_identity=action.locator.stable_message_identity,
                )
            else:
                assert action.action_type is EmailAction.MARK_READ
                provider_state["is_read"] = True
            return ProviderActionResult(
                status="done",
                provider_operation=action.action_type.value,
                provider_target=action.locator.stable_message_identity,
                provider_result_id=f"receipt-{len(effects)}",
                updated_locator=updated,
            )

    outcome = module.execute_historical_model_actions(
        store,
        lambda _account_id: Executor(),
        SimpleNamespace(produce=lambda plan, current: produced.append((plan, current))),
        message=message,
        prediction=_accepted_model_prediction(category="junk", important=False),
        context=context,
        model_id="model-junk-unsubscribe",
        model_text="historical junk unsubscribe",
        unsubscribe_entries=(entry,),
        read_after=lambda: SimpleNamespace(
            is_read=provider_state["is_read"],
            provider_folder_name=provider_state["folder"],
        ),
    )
    assert outcome.outcome == "provider_action_failed"
    assert len(produced) == 1
    assert effects == []

    classification = store.get_classification_by_stable_identity(
        "account-1:message-id:<historical-junk@example.com>"
    )
    assert classification is not None
    plan = classification["action_plan"]
    action_identity = email_action_identity(
        account_id="account-1",
        stable_message_identity=classification["stable_message_identity"],
        action_type=EmailAction.UNSUBSCRIBE,
        action_plan_version=plan["action_plan_version"],
    )
    with sqlite3.connect(store.path) as db:
        db.execute(
            """
            insert into email_unsubscribe_receipts (
                action_identity, effect_digest, action_plan_id,
                action_plan_version, classification_id, account_id,
                stable_message_identity, thread_identity, entry_reference,
                outcome, receipt_id, evidence, result_text,
                observation_digest, started_at, completed_at,
                result_text_truncated, result_text_digest, created_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, 'done', ?, 'done', '', '', '', '', 0, '', ?)
            """,
            (
                action_identity,
                "f" * 64,
                plan["action_plan_id"],
                plan["action_plan_version"],
                classification["id"],
                "account-1",
                classification["stable_message_identity"],
                classification["thread_id"],
                entry.reference,
                "receipt-unsubscribe",
                "2026-09-08T00:01:00+00:00",
            ),
        )

    assert module.reconcile_historical_operations(
        store,
        lambda _account_id: Executor(),
        read_after=lambda _operation: SimpleNamespace(
            is_read=provider_state["is_read"],
            provider_folder_name=provider_state["folder"],
            folder_role=FolderRole.TRASH,
        ),
    ) == {"repaired": 1, "deferred": 0}
    assert effects == [EmailAction.TRASH, EmailAction.MARK_READ]
    assert len(store.list_historical_classification_history()) == 1


def test_historical_junk_mark_read_failure_recovers_without_repeating_trash(tmp_path):
    module = _module()
    store = EmailStore(tmp_path / "historical-junk-retry.sqlite3")
    message, context = _historical_junk_fixture()
    provider_state = {"folder": "INBOX", "is_read": True}
    effects = []
    mark_attempts = 0

    class Executor:
        def execute(self, action):
            nonlocal mark_attempts
            updated = None
            if action.action_type is EmailAction.TRASH:
                effects.append(EmailAction.TRASH)
                provider_state.update(folder="Trash", is_read=False)
                updated = StoredEmailLocator(
                    account_id=action.account_id,
                    folder="Trash",
                    uidvalidity=84,
                    uid=188,
                    rfc_message_id=action.locator.rfc_message_id,
                    thread_id=action.locator.thread_id,
                    stable_message_identity=action.locator.stable_message_identity,
                )
            else:
                mark_attempts += 1
                if mark_attempts == 1:
                    return ProviderActionResult(
                        status="failed",
                        provider_operation="mark_read",
                        provider_target=action.locator.stable_message_identity,
                        provider_result_id="",
                        error="temporary",
                    )
                effects.append(EmailAction.MARK_READ)
                provider_state["is_read"] = True
            return ProviderActionResult(
                status="done",
                provider_operation=action.action_type.value,
                provider_target=action.locator.stable_message_identity,
                provider_result_id=f"receipt-{len(effects)}",
                updated_locator=updated,
            )

    outcome = module.execute_historical_model_actions(
        store,
        lambda _account_id: Executor(),
        SimpleNamespace(produce=lambda *_args: None),
        message=message,
        prediction=_accepted_model_prediction(category="junk", important=False),
        context=context,
        model_id="model-junk-retry",
        model_text="historical junk retry",
        unsubscribe_entries=(),
        read_after=lambda: SimpleNamespace(
            is_read=provider_state["is_read"],
            provider_folder_name=provider_state["folder"],
        ),
    )
    assert outcome.outcome == "provider_action_failed"
    assert len(store.list_processing_historical_operations()) == 1
    with sqlite3.connect(store.path) as db:
        db.execute(
            "update email_actions set next_attempt_at='2020-01-01T00:00:00+00:00' "
            "where action_type='mark_read'"
        )
    reopened = EmailStore(store.path)
    assert module.reconcile_historical_operations(
        reopened,
        lambda _account_id: Executor(),
        read_after=lambda _operation: SimpleNamespace(
            is_read=provider_state["is_read"],
            provider_folder_name="Trash",
            folder_role=FolderRole.TRASH,
        ),
    ) == {"repaired": 1, "deferred": 0}
    assert effects == [EmailAction.TRASH, EmailAction.MARK_READ]
    assert mark_attempts == 2
    assert len(reopened.list_historical_classification_history()) == 1


def test_agent_junk_mailto_selection_cannot_authorize_unsubscribe() -> None:
    from app.email_classifier_agent import (
        AgentClassificationResult,
        durable_agent_classification_result,
    )

    entries = extract_unsubscribe_entries(
        list_unsubscribe="<mailto:leave@example.test?subject=unsubscribe>"
    )
    result = AgentClassificationResult(
        category="junk",
        important=False,
        certainty="certain",
        confidence=0.98,
        reason="Unwanted promotion.",
        unsubscribe_candidate_index=0,
        unsubscribe_url=entries[0].private_url,
    )

    with pytest.raises(ValueError, match="executable HTTPS"):
        _module()._agent_classification_action_plan(
            classification_id=75,
            account_id="account-1",
            result=result,
            unsubscribe_selection=durable_agent_classification_result(result, entries),
            folder_targets={},
            config_version="config-v1",
            created_at=datetime(2026, 9, 8, tzinfo=timezone.utc),
        )


@pytest.mark.parametrize(
    ("outcome", "trash_allowed"),
    (
        (None, False),
        ("done", True),
        ("already_unsubscribed", True),
        ("skipped_no_reliable_entry", True),
        ("skipped_login_required", False),
        ("skipped_captcha", False),
        ("skipped_payment", False),
        ("failed_browser", False),
    ),
)
def test_dependent_trash_waits_for_safe_unsubscribe_terminal_evidence(
    tmp_path: Path,
    outcome: str | None,
    trash_allowed: bool,
) -> None:
    database = tmp_path / f"junk-dependent-{outcome or 'none'}.sqlite3"
    store = EmailStore(database)
    account_id = "account-junk"
    stable_identity = "account-junk:message-id:<junk-77@example.com>"
    entry = extract_unsubscribe_entries(
        list_unsubscribe="<https://news.example.com/unsubscribe?token=private>"
    )[0]
    selection = {
        "candidate_index": entry.index,
        "candidate_source": entry.source.value,
        "candidate_digest": entry.reference.removeprefix("unsubscribe-entry:"),
        "candidate_reference": entry.reference,
    }
    plan = build_versioned_email_action_plan(
        action_plan_version=1,
        classification_id=77,
        account_id=account_id,
        category="junk",
        classification_source="user",
        confidence=0.99,
        model_id="email-classifier-agent:v1",
        config_version="config-v1",
        actions=(EmailAction.UNSUBSCRIBE, EmailAction.TRASH),
        action_parameters={EmailAction.UNSUBSCRIBE: selection},
        created_at=datetime(2026, 9, 8, tzinfo=timezone.utc),
    )
    store.create_account(
        {
            "account_id": account_id,
            "display_name": "Junk",
            "email_address": "derek@example.com",
            "imap_host": "imap.example.com",
            "imap_port": 993,
            "imap_tls": True,
            "imap_username": "derek@example.com",
            "imap_secret_reference": "keychain://junk-imap",
            "smtp_host": "smtp.example.com",
            "smtp_port": 465,
            "smtp_tls": True,
            "smtp_username": "derek@example.com",
            "smtp_secret_reference": "keychain://junk-smtp",
            "enabled": True,
            "scan_folders": ["INBOX"],
            "scan_interval_seconds": 60,
        }
    )
    store.upsert_classification(
        EmailClassification(
            classification_id=77,
            stable_message_identity=stable_identity,
            provider_locator=EmailProviderLocator(
                account_id=account_id,
                folder="INBOX",
                uidvalidity=1,
                uid=77,
                rfc_message_id="<junk-77@example.com>",
                thread_id="thread-junk-77",
            ),
            category="junk",
            confidence=0.99,
            margin=1.0,
            probabilities={"junk": 0.99},
            model_id=plan.model_id,
            config_version=plan.config_version,
            status=EmailClassificationStatus.PROCESSED,
            classification_source="user",
            action_plan=plan,
        ),
        sender="sender@example.com",
        subject="Junk",
        model_text="junk",
        received_at="2026-09-08T00:00:00+00:00",
    )
    if outcome is not None:
        action_identity = email_action_identity(
            account_id=account_id,
            stable_message_identity=stable_identity,
            action_type=EmailAction.UNSUBSCRIBE,
            action_plan_version=1,
        )
        with sqlite3.connect(database) as db:
            db.execute(
                """
                insert into email_unsubscribe_receipts (
                    action_identity, effect_digest, action_plan_id,
                    action_plan_version, classification_id, account_id,
                    stable_message_identity, thread_identity, entry_reference,
                    outcome, receipt_id, evidence, result_text,
                    observation_digest, started_at, completed_at,
                    result_text_truncated, result_text_digest, created_at
                ) values (?, ?, ?, 1, 77, ?, ?, ?, ?, ?, ?, ?, '', '', '', '', 0, '', ?)
                """,
                (
                    action_identity,
                    "f" * 64,
                    plan.action_plan_id,
                    account_id,
                    stable_identity,
                    "thread-junk-77",
                    entry.reference,
                    outcome,
                    f"receipt:{outcome}",
                    "terminal evidence",
                    "2026-09-08T00:01:00+00:00",
                ),
            )

    claimed = store.claim_next_direct_action(claimed_at="2026-09-08T00:02:00+00:00")
    assert (claimed is not None) is trash_allowed
    if claimed is not None:
        assert claimed.action_type is EmailAction.TRASH


def test_agent_uncertain_result_has_no_action_plan() -> None:
    from app.email_classifier_agent import AgentClassificationResult

    plan = _module()._agent_classification_action_plan(
        classification_id=73,
        account_id="account-1",
        result=AgentClassificationResult(
            category=None,
            important=False,
            certainty="uncertain",
            confidence=0.41,
            reason="Insufficient context.",
            unsubscribe_candidate_index=None,
            unsubscribe_url=None,
        ),
        folder_targets={"work": "Work"},
        config_version="config-v1",
        created_at=datetime(2026, 9, 8, tzinfo=timezone.utc),
    )

    assert plan is None


def test_worker_runtime_scan_path_does_not_read_loaded_tfidf_classifier(
    monkeypatch, tmp_path
):
    module = _module()
    source = Path(module.__file__).read_text(encoding="utf-8")

    assert "current_model.loaded.classifier" not in source
    assert "scan_agent_classification_batch" in source
    assert "EmailClassificationTaskProducer" in source


def _classification_task_input():
    from app.email_task_adapter import EmailClassificationTaskInput

    return EmailClassificationTaskInput.from_message(
        {
            "accountId": "account-1",
            "folder": "INBOX",
            "uidValidity": 42,
            "uid": 81,
            "messageId": "<mail-81@example.com>",
            "threadId": "thread-81",
            "providerUnread": True,
            "from": {"email": "customer@example.com"},
            "toRecipients": [{"email": "derek@example.com"}],
            "subject": "Launch decision",
            "date": "2026-09-08T08:00:00+00:00",
            "textBody": "Please confirm the customer launch decision.",
        },
        allowed_category_keys=("work", "junk"),
        category_descriptions={
            "work": {"core": "Business."},
            "junk": {"core": "Unwanted."},
        },
        folder_targets={"work": "Work"},
        config_version="config-v1",
        unsubscribe_candidates=(),
    )


def _classification_provider_readback(task, payload):
    locator = payload["provider_locator"]
    message = payload["message"]
    return {
        "stableMessageIdentity": task.stable_message_identity,
        "accountId": locator["account_id"],
        "folder": locator["folder"],
        "uidValidity": locator["uidvalidity"],
        "uid": locator["uid"],
        "messageId": locator.get("rfc_message_id"),
        "threadId": locator.get("thread_id"),
        "providerUnread": True,
        "from": message.get("sender", {}),
        "toRecipients": message.get("to_recipients", []),
        "ccRecipients": message.get("cc_recipients", []),
        "subject": message.get("subject", ""),
        "date": message.get("date", ""),
        "textBody": message.get("text", ""),
        "markdownBody": message.get("text", ""),
        "listUnsubscribe": message.get("headers", {}).get("list_unsubscribe", ""),
        "listUnsubscribePost": message.get("headers", {}).get(
            "list_unsubscribe_post", ""
        ),
        "attachments": message.get("attachments", []),
    }


def _forbid_action_task_production():
    return SimpleNamespace(
        produce=lambda *_args, **_kwargs: pytest.fail(
            "unexpected Email Agent task production"
        )
    )


def test_classification_worker_persists_certain_decision_and_direct_plan(tmp_path):
    from app.email_classifier_agent import AgentClassificationResult
    from app.email_task_adapter import EmailClassificationTaskAdapter

    email_store = EmailStore(tmp_path / "classification-worker.sqlite3")
    adapter = EmailClassificationTaskAdapter(email_store)
    adapter.ensure_task(_classification_task_input())
    agent = SimpleNamespace(
        classify=lambda _task, **_kwargs: AgentClassificationResult(
            category="work",
            important=True,
            certainty="certain",
            confidence=0.96,
            reason="Customer project decision.",
            unsubscribe_candidate_index=None,
            unsubscribe_url=None,
        )
    )

    outcome = _module().run_email_classification_task_once(
        adapter,
        agent,
        email_store,
        owner="email-worker:test",
        provider_readback=_classification_provider_readback,
        action_task_producer=_forbid_action_task_production(),
    )

    assert outcome["decision_status"] == "processed"
    classification = email_store.get_classification(outcome["classification_id"])
    assert classification["classification_source"] == "agent"
    assert classification["model_id"] == "email-classifier-agent:v1"
    assert classification["agent_result"]["reason"] == "Customer project decision."
    assert classification["action_plan"]["classification_source"] == "agent"
    assert classification["action_plan"]["actions"] == ["move", "flag_important"]
    assert classification["status"] == "processed"


def test_classification_worker_persists_only_redacted_model_features(tmp_path):
    from app.email_classifier_agent import AgentClassificationResult
    from app.email_task_adapter import EmailClassificationTaskAdapter

    email_store = EmailStore(tmp_path / "classification-redaction.sqlite3")
    adapter = EmailClassificationTaskAdapter(email_store)
    adapter.ensure_task(_classification_task_input())
    agent = SimpleNamespace(
        classify=lambda _task, **_kwargs: AgentClassificationResult(
            category="work",
            important=False,
            certainty="certain",
            confidence=0.96,
            reason="Project coordination.",
        )
    )

    def readback(task, payload):
        message = _classification_provider_readback(task, payload)
        return message | {
            "textBody": "Contact private@example.com at https://private.example/path",
            "markdownBody": "Contact private@example.com at https://private.example/path",
        }

    outcome = _module().run_email_classification_task_once(
        adapter,
        agent,
        email_store,
        owner="email-worker:redaction",
        provider_readback=readback,
        action_task_producer=_forbid_action_task_production(),
    )

    with email_store._connect() as db:
        model_text = db.execute(
            "select model_text from email_classifications where id=?",
            (outcome["classification_id"],),
        ).fetchone()["model_text"]
    assert "private@example.com" not in model_text
    assert "https://private.example" not in model_text
    assert "EMAIL" in model_text
    assert "URL" in model_text


def test_classification_worker_persists_uncertain_feedback_without_actions(tmp_path):
    from app.email_classifier_agent import AgentClassificationResult
    from app.email_task_adapter import EmailClassificationTaskAdapter

    email_store = EmailStore(tmp_path / "classification-worker.sqlite3")
    adapter = EmailClassificationTaskAdapter(email_store)
    task = adapter.ensure_task(_classification_task_input())
    agent = SimpleNamespace(
        classify=lambda _task, **_kwargs: AgentClassificationResult(
            category=None,
            important=False,
            certainty="uncertain",
            confidence=0.43,
            reason="Insufficient business context.",
            unsubscribe_candidate_index=None,
            unsubscribe_url=None,
        )
    )

    outcome = _module().run_email_classification_task_once(
        adapter,
        agent,
        email_store,
        owner="email-worker:test",
        provider_readback=_classification_provider_readback,
        action_task_producer=_forbid_action_task_production(),
    )

    assert outcome == {
        "decision_status": "pending_feedback",
        "category": None,
        "important": False,
        "certainty": "uncertain",
        "confidence": 0.43,
        "reason": "Insufficient business context.",
        "unsubscribe_candidate_index": None,
        "unsubscribe_candidate_source": None,
        "unsubscribe_candidate_digest": None,
        "unsubscribe_candidate_reference": None,
        "classification_id": outcome["classification_id"],
    }
    rows, total = email_store.list_classifications(
        status=EmailClassificationStatus.PENDING_FEEDBACK,
        limit=20,
        offset=0,
    )
    assert total == 1
    assert rows[0]["category"] is None
    assert rows[0]["predicted_category"] is None
    assert rows[0]["classification_source"] == "agent"
    assert rows[0]["agent_result"] == {
        "category": None,
        "important": False,
        "certainty": "uncertain",
        "confidence": 0.43,
        "reason": "Insufficient business context.",
        "unsubscribe_candidate_index": None,
        "unsubscribe_candidate_source": None,
        "unsubscribe_candidate_digest": None,
        "unsubscribe_candidate_reference": None,
    }
    assert rows[0]["action_plan"] is None
    assert json.loads(adapter.get_task(task.task_id).result_json) == outcome
    confirmed = email_store.confirm_classification(
        rows[0]["id"],
        "work",
        feedback_request_id="feedback-agent-uncertain-81",
        expected_current_action_plan_id=None,
    )
    assert confirmed["category"] == "work"
    assert confirmed["classification_source"] == "user"


def test_classification_worker_retries_after_production_crash_and_deduplicates_task(
    tmp_path: Path,
) -> None:
    from app.email_classifier_agent import AgentClassificationResult
    from app.email_task_adapter import EmailClassificationTaskAdapter

    private_url = "https://news.example.test/unsubscribe?token=crash-after"
    message = parse_rfc822_message(
        (
            b"From: blast@example.test\r\nSubject: Offer\r\n"
            b"Message-ID: <crash-after@example.test>\r\n"
            b"Date: Sun, 07 Sep 2026 12:00:00 -0700\r\n"
            b"List-Unsubscribe: <"
            + private_url.encode()
            + b">\r\n\r\nUnwanted promotion"
        ),
        account_id="account-1",
        folder="INBOX",
        uidvalidity=42,
        uid=184,
    )
    task_input = EmailClassificationTaskInput.from_message(
        message,
        allowed_category_keys=("work", "junk"),
        category_descriptions={"work": "Business", "junk": "Unwanted"},
        folder_targets={"work": "Work"},
        config_version="config-v1",
        unsubscribe_candidates=extract_unsubscribe_entries(
            list_unsubscribe=f"<{private_url}>"
        ),
    )
    database = tmp_path / "classification-worker.sqlite3"
    email_store = EmailStore(database)
    task_store = AutoReplyStore(database)
    real_adapter = EmailClassificationTaskAdapter(email_store)
    task = real_adapter.ensure_task(task_input)
    calls = []
    agent = SimpleNamespace(
        classify=lambda _task, **_kwargs: (
            calls.append(_task.task_id)
            or AgentClassificationResult(
                category="junk",
                important=False,
                certainty="certain",
                confidence=0.99,
                reason="Unwanted promotion.",
                unsubscribe_candidate_index=0,
                unsubscribe_url=private_url,
            )
        )
    )
    producer = EmailActionTaskProducer(task_store, email_store)

    class CrashAfterCanonical:
        def __getattr__(self, name):
            return getattr(real_adapter, name)

        def complete(self, _task, _result):
            raise KeyboardInterrupt("crash after canonical persistence")

    with pytest.raises(KeyboardInterrupt, match="crash after canonical"):
        _module().run_email_classification_task_once(
            CrashAfterCanonical(),
            agent,
            email_store,
            owner="email-worker:first",
            provider_readback=lambda *_args: message,
            action_task_producer=producer,
        )

    [first_audited_task] = task_store.list_reply_tasks(channel="email")

    with email_store._connect() as db:
        db.execute(
            "update email_agent_classification_tasks set lease_expires_at='2000-01-01T00:00:00+00:00'"
        )
    assert real_adapter.recover_running_tasks() == 1
    outcome = _module().run_email_classification_task_once(
        real_adapter,
        agent,
        email_store,
        owner="email-worker:retry",
        provider_readback=lambda *_args: message,
        action_task_producer=producer,
    )

    assert len(calls) == 1
    [replayed_audited_task] = task_store.list_reply_tasks(channel="email")
    assert replayed_audited_task.id == first_audited_task.id
    assert outcome["classification_id"] > 0
    persisted = email_store.get_classification(outcome["classification_id"])
    assert persisted["agent_result"]["reason"] == "Unwanted promotion."
    assert persisted["action_plan"]["actions"] == ["unsubscribe", "trash"]
    assert outcome["certainty"] == "certain"
    assert json.loads(real_adapter.get_task(task.task_id).result_json) == outcome


@pytest.mark.parametrize(
    "current", (None, {"providerUnread": False}, {"folder": "Work"})
)
def test_classifier_provider_race_skips_before_agent_and_creates_no_actions(
    tmp_path: Path, current
) -> None:
    adapter = EmailClassificationTaskAdapter(EmailStore(tmp_path / "race.sqlite3"))
    task = adapter.ensure_task(_classification_task_input())
    calls = []
    base = _classification_provider_readback(task, json.loads(task.input_json))
    readback = None if current is None else base | current

    outcome = _module().run_email_classification_task_once(
        adapter,
        SimpleNamespace(classify=lambda *_args, **_kwargs: calls.append(1)),
        adapter.email_store,
        owner="email-worker:race",
        provider_readback=lambda *_args: readback,
        action_task_producer=_forbid_action_task_production(),
    )

    assert outcome == {
        "decision_status": "skipped",
        "reason": "provider_message_no_longer_eligible",
    }
    assert calls == []
    assert (
        adapter.email_store.get_classification_by_stable_identity(
            task.stable_message_identity
        )
        is None
    )
    assert adapter.get_task(task.task_id).status == "done"


def test_classification_worker_automatically_produces_one_audited_unsubscribe_task(
    tmp_path: Path,
) -> None:
    from app.email_classifier_agent import AgentClassificationResult

    private_url = "https://news.example.test/unsubscribe?token=private-html-token"
    message = parse_rfc822_message(
        (
            b"From: blast@example.test\r\nSubject: Offer\r\n"
            b"Message-ID: <html-classifier@example.test>\r\n"
            b"Date: Sun, 07 Sep 2026 12:00:00 -0700\r\n"
            b"Content-Type: text/html; charset=utf-8\r\n\r\n"
            + f'<a href="{private_url}">Unsubscribe</a>'.encode()
        ),
        account_id="account-1",
        folder="INBOX",
        uidvalidity=42,
        uid=182,
    )
    entries = extract_unsubscribe_entries(
        list_unsubscribe="",
        list_unsubscribe_post="",
        body_text=str(message["textBody"]),
        body_html=ephemeral_body_html(message),
    )
    task_input = EmailClassificationTaskInput.from_message(
        message,
        allowed_category_keys=("work", "junk"),
        category_descriptions={"work": "Business", "junk": "Unwanted"},
        folder_targets={"work": "Work"},
        config_version="config-v1",
        unsubscribe_candidates=entries,
    )
    database = tmp_path / "html.sqlite3"
    store = EmailStore(database)
    task_store = AutoReplyStore(database)
    adapter = EmailClassificationTaskAdapter(store)
    classifier_task = adapter.ensure_task(task_input)
    production_statuses: list[str] = []
    real_producer = EmailActionTaskProducer(task_store, store)

    class ObservedProducer:
        def produce(self, plan, current_message):
            production_statuses.append(adapter.get_task(classifier_task.task_id).status)
            return real_producer.produce(plan, current_message)

    seen = []
    agent = SimpleNamespace(
        classify=lambda _task, **kwargs: (
            seen.append(
                (
                    kwargs["unsubscribe_candidates"],
                    kwargs.get("unsubscribe_candidate_metadata"),
                )
            )
            or AgentClassificationResult(
                category="junk",
                important=False,
                certainty="certain",
                confidence=0.99,
                reason="Unwanted promotion.",
                unsubscribe_candidate_index=0,
                unsubscribe_url=private_url,
            )
        )
    )

    outcome = _module().run_email_classification_task_once(
        adapter,
        agent,
        store,
        owner="email-worker:html",
        provider_readback=lambda *_args: message,
        action_task_producer=ObservedProducer(),
    )

    assert seen == [
        (
            (private_url,),
            (
                {
                    "index": 0,
                    "source": "body_html_https",
                    "scheme": "https",
                    "host": "news.example.test",
                    "context": "Unsubscribe",
                    "reference": entries[0].reference,
                },
            ),
        )
    ]
    assert outcome["unsubscribe_candidate_reference"].startswith("unsubscribe-entry:")
    assert "unsubscribe_url" not in outcome
    [task] = task_store.list_reply_tasks(channel="email")
    assert production_statuses == ["running"]
    payload = json.loads(task.trigger_message_json)
    assert payload["category"] == "junk"
    assert payload["action_parameters"] == {
        "candidate_index": 0,
        "candidate_source": "body_html_https",
        "candidate_digest": outcome["unsubscribe_candidate_digest"],
        "candidate_reference": outcome["unsubscribe_candidate_reference"],
    }
    assert payload["unsubscribe_entries"] == [
        {
            "index": 0,
            "source": "body_html_https",
            "digest": outcome["unsubscribe_candidate_digest"],
            "reference": outcome["unsubscribe_candidate_reference"],
        }
    ]
    assert payload["lifecycle_version"] == "email_unsubscribe_audited_v2"
    assert private_url.encode() not in database.read_bytes()
    assert b"private-html-token" not in database.read_bytes()


def test_classification_worker_preserves_verified_one_click_candidate_end_to_end(
    tmp_path: Path,
) -> None:
    from app.email_classifier_agent import AgentClassificationResult

    private_url = "https://news.example.test/unsubscribe?token=one-click-private"
    message = parse_rfc822_message(
        (
            b"From: blast@example.test\r\nSubject: Offer\r\n"
            b"Message-ID: <one-click-classifier@example.test>\r\n"
            b"Date: Sun, 07 Sep 2026 12:00:00 -0700\r\n"
            b"List-Unsubscribe: <"
            + private_url.encode()
            + b">\r\nList-Unsubscribe-Post: List-Unsubscribe=One-Click\r\n\r\n"
            b"Unwanted promotion"
        ),
        account_id="account-1",
        folder="INBOX",
        uidvalidity=42,
        uid=185,
    )
    authentication = UnsubscribeAuthenticationEvidence(
        dkim_covers_list_unsubscribe=True,
        dkim_covers_list_unsubscribe_post=True,
        evidence_reference="dkim-evidence:one-click-classifier",
    )
    attach_ephemeral_unsubscribe_authentication(message, authentication)
    entries = extract_unsubscribe_entries(
        list_unsubscribe=str(message["listUnsubscribe"]),
        list_unsubscribe_post=str(message["listUnsubscribePost"]),
        authentication_evidence=authentication,
    )
    task_input = EmailClassificationTaskInput.from_message(
        message,
        allowed_category_keys=("work", "junk"),
        category_descriptions={"work": "Business", "junk": "Unwanted"},
        folder_targets={"work": "Work"},
        config_version="config-v1",
        unsubscribe_candidates=entries,
    )
    database = tmp_path / "one-click.sqlite3"
    email_store = EmailStore(database)
    task_store = AutoReplyStore(database)
    adapter = EmailClassificationTaskAdapter(email_store)
    adapter.ensure_task(task_input)
    seen_metadata: list[object] = []
    agent = SimpleNamespace(
        classify=lambda _task, **kwargs: (
            seen_metadata.append(kwargs["unsubscribe_candidate_metadata"])
            or AgentClassificationResult(
                category="junk",
                important=False,
                certainty="certain",
                confidence=0.99,
                reason="Unwanted promotion.",
                unsubscribe_candidate_index=0,
                unsubscribe_url=private_url,
            )
        )
    )

    outcome = _module().run_email_classification_task_once(
        adapter,
        agent,
        email_store,
        owner="email-worker:one-click",
        provider_readback=lambda *_args: message,
        action_task_producer=EmailActionTaskProducer(task_store, email_store),
    )

    assert seen_metadata[0][0]["source"] == "header_one_click_https"
    [task] = task_store.list_reply_tasks(channel="email")
    payload = json.loads(task.trigger_message_json)
    assert payload["lifecycle_version"] == "email_unsubscribe_audited_v2"
    assert payload["action_parameters"] == {
        "candidate_index": 0,
        "candidate_source": "header_one_click_https",
        "candidate_digest": outcome["unsubscribe_candidate_digest"],
        "candidate_reference": outcome["unsubscribe_candidate_reference"],
    }
    assert payload["unsubscribe_authentication"] == {
        "evidence_reference": authentication.evidence_reference,
        "one_click_verified": True,
    }


def test_classification_worker_treats_mailto_only_junk_as_direct_trash(
    tmp_path: Path,
) -> None:
    from app.email_classifier_agent import AgentClassificationResult

    private_mailto = "mailto:leave@example.test?subject=unsubscribe&token=private-mail"
    message = parse_rfc822_message(
        (
            b"From: blast@example.test\r\nSubject: Offer\r\n"
            b"Message-ID: <mailto-only@example.test>\r\n"
            b"Date: Sun, 07 Sep 2026 12:00:00 -0700\r\n"
            b"List-Unsubscribe: <"
            + private_mailto.encode()
            + b">\r\n\r\nUnwanted promotion"
        ),
        account_id="account-1",
        folder="INBOX",
        uidvalidity=42,
        uid=186,
    )
    discovered = extract_unsubscribe_entries(
        list_unsubscribe=str(message["listUnsubscribe"])
    )
    assert len(discovered) == 1
    assert discovered[0].scheme == "mailto"
    database = tmp_path / "mailto-only.sqlite3"
    email_store = EmailStore(database)
    task_store = AutoReplyStore(database)
    adapter = EmailClassificationTaskAdapter(email_store)
    adapter.ensure_task(
        EmailClassificationTaskInput.from_message(
            message,
            allowed_category_keys=("work", "junk"),
            category_descriptions={"work": "Business", "junk": "Unwanted"},
            folder_targets={"work": "Work"},
            config_version="config-v1",
            unsubscribe_candidates=discovered,
        )
    )
    seen: list[tuple[object, object]] = []
    agent = SimpleNamespace(
        classify=lambda _task, **kwargs: (
            seen.append(
                (
                    kwargs["unsubscribe_candidates"],
                    kwargs["unsubscribe_candidate_metadata"],
                )
            )
            or AgentClassificationResult(
                category="junk",
                important=False,
                certainty="certain",
                confidence=0.99,
                reason="Unwanted promotion.",
                unsubscribe_candidate_index=None,
                unsubscribe_url=None,
            )
        )
    )

    outcome = _module().run_email_classification_task_once(
        adapter,
        agent,
        email_store,
        owner="email-worker:mailto-only",
        provider_readback=lambda *_args: message,
        action_task_producer=EmailActionTaskProducer(task_store, email_store),
    )

    assert seen == [((), ())]
    classification = email_store.get_classification(outcome["classification_id"])
    assert classification["action_plan"]["actions"] == ["trash"]
    assert classification["action_plan"]["action_parameters"] == {}
    assert task_store.list_reply_tasks(channel="email") == []
    claimed = email_store.claim_next_direct_action(
        claimed_at="2026-09-08T00:02:00+00:00"
    )
    assert claimed is not None
    assert claimed.action_type is EmailAction.TRASH
    assert b"private-mail" not in database.read_bytes()


def test_classification_worker_exposes_only_deduplicated_https_from_mixed_mailto(
    tmp_path: Path,
) -> None:
    from app.email_classifier_agent import AgentClassificationResult

    private_mailto = "mailto:leave@example.test?subject=unsubscribe&token=private-mail"
    private_https = "https://news.example.test/unsubscribe?token=private-web"
    message = parse_rfc822_message(
        (
            b"From: blast@example.test\r\nSubject: Offer\r\n"
            b"Message-ID: <mailto-mixed@example.test>\r\n"
            b"Date: Sun, 07 Sep 2026 12:00:00 -0700\r\n"
            b"List-Unsubscribe: <"
            + private_mailto.encode()
            + b">, <"
            + private_mailto.encode()
            + b">\r\nContent-Type: text/html; charset=utf-8\r\n\r\n"
            + (
                f'<a href="{private_https}">Unsubscribe</a>'
                f'<a href="{private_https}">Unsubscribe again</a>'
            ).encode()
        ),
        account_id="account-1",
        folder="INBOX",
        uidvalidity=42,
        uid=187,
    )
    discovered = extract_unsubscribe_entries(
        list_unsubscribe=str(message["listUnsubscribe"]),
        body_html=ephemeral_body_html(message),
    )
    assert [entry.scheme for entry in discovered] == ["mailto", "https"]
    assert "private-mail" not in repr(discovered)
    assert "private-web" not in repr(discovered)
    database = tmp_path / "mailto-mixed.sqlite3"
    email_store = EmailStore(database)
    task_store = AutoReplyStore(database)
    adapter = EmailClassificationTaskAdapter(email_store)
    adapter.ensure_task(
        EmailClassificationTaskInput.from_message(
            message,
            allowed_category_keys=("work", "junk"),
            category_descriptions={"work": "Business", "junk": "Unwanted"},
            folder_targets={"work": "Work"},
            config_version="config-v1",
            unsubscribe_candidates=discovered,
        )
    )
    seen: list[tuple[object, object]] = []
    agent = SimpleNamespace(
        classify=lambda _task, **kwargs: (
            seen.append(
                (
                    kwargs["unsubscribe_candidates"],
                    kwargs["unsubscribe_candidate_metadata"],
                )
            )
            or AgentClassificationResult(
                category="junk",
                important=False,
                certainty="certain",
                confidence=0.99,
                reason="Unwanted promotion.",
                unsubscribe_candidate_index=0,
                unsubscribe_url=private_https,
            )
        )
    )

    outcome = _module().run_email_classification_task_once(
        adapter,
        agent,
        email_store,
        owner="email-worker:mailto-mixed",
        provider_readback=lambda *_args: message,
        action_task_producer=EmailActionTaskProducer(task_store, email_store),
    )

    assert seen == [
        (
            (private_https,),
            (
                {
                    "index": 0,
                    "source": "body_html_https",
                    "scheme": "https",
                    "host": "news.example.test",
                    "context": "Unsubscribe",
                    "reference": discovered[1].reference,
                },
            ),
        )
    ]
    [task] = task_store.list_reply_tasks(channel="email")
    payload = json.loads(task.trigger_message_json)
    assert payload["action_parameters"] == {
        "candidate_index": 0,
        "candidate_source": "body_html_https",
        "candidate_digest": outcome["unsubscribe_candidate_digest"],
        "candidate_reference": outcome["unsubscribe_candidate_reference"],
    }
    assert payload["unsubscribe_entries"] == [
        {
            "index": 0,
            "source": "body_html_https",
            "digest": outcome["unsubscribe_candidate_digest"],
            "reference": outcome["unsubscribe_candidate_reference"],
        }
    ]
    assert b"private-mail" not in database.read_bytes()
    assert b"private-web" not in database.read_bytes()


def test_classification_worker_recovers_crash_before_task_production_without_agent_recall(
    tmp_path: Path,
) -> None:
    from app.email_classifier_agent import AgentClassificationResult

    private_url = "https://news.example.test/unsubscribe?token=crash-before"
    message = parse_rfc822_message(
        (
            b"From: blast@example.test\r\nSubject: Offer\r\n"
            b"Message-ID: <crash-before@example.test>\r\n"
            b"Date: Sun, 07 Sep 2026 12:00:00 -0700\r\n"
            b"List-Unsubscribe: <"
            + private_url.encode()
            + b">\r\n\r\nUnwanted promotion"
        ),
        account_id="account-1",
        folder="INBOX",
        uidvalidity=42,
        uid=183,
    )
    entries = extract_unsubscribe_entries(list_unsubscribe=f"<{private_url}>")
    task_input = EmailClassificationTaskInput.from_message(
        message,
        allowed_category_keys=("work", "junk"),
        category_descriptions={"work": "Business", "junk": "Unwanted"},
        folder_targets={"work": "Work"},
        config_version="config-v1",
        unsubscribe_candidates=entries,
    )
    database = tmp_path / "crash-before.sqlite3"
    email_store = EmailStore(database)
    task_store = AutoReplyStore(database)
    adapter = EmailClassificationTaskAdapter(email_store)
    classifier_task = adapter.ensure_task(task_input)
    agent_calls: list[str] = []
    agent = SimpleNamespace(
        classify=lambda task, **_kwargs: (
            agent_calls.append(task.task_id)
            or AgentClassificationResult(
                category="junk",
                important=False,
                certainty="certain",
                confidence=0.99,
                reason="Unwanted promotion.",
                unsubscribe_candidate_index=0,
                unsubscribe_url=private_url,
            )
        )
    )

    class CrashBeforeProduction:
        def produce(self, _plan, _message):
            raise KeyboardInterrupt("crash before task production")

    with pytest.raises(KeyboardInterrupt, match="before task production"):
        _module().run_email_classification_task_once(
            adapter,
            agent,
            email_store,
            owner="email-worker:first",
            provider_readback=lambda *_args: message,
            action_task_producer=CrashBeforeProduction(),
        )

    assert task_store.list_reply_tasks(channel="email") == []
    with email_store._connect() as db:
        db.execute(
            "update email_agent_classification_tasks "
            "set lease_expires_at='2000-01-01T00:00:00+00:00'"
        )
    assert adapter.recover_running_tasks() == 1

    outcome = _module().run_email_classification_task_once(
        adapter,
        agent,
        email_store,
        owner="email-worker:retry",
        provider_readback=lambda *_args: message,
        action_task_producer=EmailActionTaskProducer(task_store, email_store),
    )

    assert len(agent_calls) == 1
    assert outcome["decision_status"] == "processed"
    [audited_task] = task_store.list_reply_tasks(channel="email")
    assert json.loads(audited_task.trigger_message_json)["lifecycle_version"] == (
        "email_unsubscribe_audited_v2"
    )
    assert adapter.get_task(classifier_task.task_id).status == "done"


@pytest.mark.parametrize(
    ("error", "expected_status"),
    (
        (ValueError("invalid Agent JSON"), "failed"),
        (ConnectionError("offline"), "pending"),
    ),
)
def test_classifier_distinguishes_permanent_and_transient_failures(
    tmp_path: Path, error: Exception, expected_status: str
) -> None:
    adapter = EmailClassificationTaskAdapter(EmailStore(tmp_path / "failure.sqlite3"))
    task = adapter.ensure_task(_classification_task_input())

    with pytest.raises(type(error)):
        _module().run_email_classification_task_once(
            adapter,
            SimpleNamespace(
                classify=lambda *_args, **_kwargs: (_ for _ in ()).throw(error)
            ),
            adapter.email_store,
            owner="email-worker:failure",
            provider_readback=_classification_provider_readback,
            action_task_producer=_forbid_action_task_production(),
        )

    assert adapter.get_task(task.task_id).status == expected_status


class _FolderProvider:
    def __init__(self, inventories: list[tuple[ProviderFolder, ...]]):
        self.inventories = list(inventories)
        self.created: list[str] = []

    def list_folders(self) -> tuple[ProviderFolder, ...]:
        if len(self.inventories) > 1:
            return self.inventories.pop(0)
        return self.inventories[0]

    def create_folder_exact(self, name: str) -> None:
        self.created.append(name)

    def close(self) -> None:
        pass


class _FailingFolderProvider(_FolderProvider):
    def list_folders(self) -> tuple[ProviderFolder, ...]:
        raise ConnectionError("provider inventory unavailable")


class _ConcurrentCreateProvider(_FolderProvider):
    def create_folder_exact(self, name: str) -> None:
        self.created.append(name)
        raise RuntimeError("another client created the folder")


def _folder_coordinator(provider: _FolderProvider):
    return _module().ProviderFolderBindingCoordinator(
        lambda _account: provider,
        now=lambda: "2026-09-08T10:00:00+00:00",
    )


def test_folder_materializer_binds_one_exact_existing_display_name() -> None:
    provider = _FolderProvider(
        [
            (
                ProviderFolder("work", "工作", FolderRole.UNBOUND),
                ProviderFolder("work-2", "工作项目", FolderRole.UNBOUND),
            )
        ]
    )

    bindings = _folder_coordinator(provider).create_and_verify_bindings(
        category_key="work",
        provider_folder_name="工作",
        enabled_accounts=({"account_id": "primary"},),
    )

    assert bindings[0].provider_folder_id == "work"
    assert bindings[0].binding_status == "active"
    assert provider.created == []


def test_folder_materializer_marks_duplicate_exact_names_ambiguous() -> None:
    provider = _FolderProvider(
        [
            (
                ProviderFolder("work-a", "工作", FolderRole.UNBOUND),
                ProviderFolder("work-b", "工作", FolderRole.UNBOUND),
            )
        ]
    )

    binding = _folder_coordinator(provider).create_and_verify_bindings(
        category_key="work",
        provider_folder_name="工作",
        enabled_accounts=({"account_id": "primary"},),
    )[0]

    assert binding.binding_status == "ambiguous"
    assert binding.provider_folder_id == ""
    assert provider.created == []


def test_folder_materializer_creates_exact_then_requires_one_exact_readback() -> None:
    provider = _FolderProvider(
        [
            (),
            (ProviderFolder("created-work", "工作", FolderRole.UNBOUND),),
        ]
    )

    binding = _folder_coordinator(provider).create_and_verify_bindings(
        category_key="work",
        provider_folder_name="工作",
        enabled_accounts=({"account_id": "primary"},),
    )[0]

    assert provider.created == ["工作"]
    assert binding.provider_folder_id == "created-work"
    assert binding.binding_status == "active"


def test_folder_materializer_marks_missing_when_created_folder_is_not_read_back() -> (
    None
):
    provider = _FolderProvider([(), ()])

    binding = _folder_coordinator(provider).create_and_verify_bindings(
        category_key="work",
        provider_folder_name="工作",
        enabled_accounts=({"account_id": "primary"},),
    )[0]

    assert provider.created == ["工作"]
    assert binding.binding_status == "missing"


def test_junk_materializer_binds_system_trash_and_never_creates_business_folder() -> (
    None
):
    provider = _FolderProvider(
        [
            (
                ProviderFolder("spam", "Spam", FolderRole.JUNK),
                ProviderFolder("trash", "Deleted", FolderRole.TRASH),
            )
        ]
    )

    binding = _folder_coordinator(provider).create_and_verify_bindings(
        category_key="junk",
        provider_folder_name="垃圾邮件",
        enabled_accounts=({"account_id": "primary"},),
    )[0]

    assert binding.provider_folder_id == "trash"
    assert binding.provider_folder_name == "Deleted"
    assert binding.provider_folder_role is FolderRole.TRASH
    assert binding.binding_status == "active"
    assert provider.created == []


def test_junk_materializer_marks_missing_without_creating_when_trash_is_absent() -> (
    None
):
    provider = _FolderProvider([(ProviderFolder("spam", "Spam", FolderRole.JUNK),)])

    binding = _folder_coordinator(provider).create_and_verify_bindings(
        category_key="junk",
        provider_folder_name="垃圾邮件",
        enabled_accounts=({"account_id": "primary"},),
    )[0]

    assert binding.binding_status == "missing"
    assert provider.created == []


def test_folder_materializer_records_provider_inventory_failure_as_error() -> None:
    provider = _FailingFolderProvider([()])

    binding = _folder_coordinator(provider).create_and_verify_bindings(
        category_key="work",
        provider_folder_name="工作",
        enabled_accounts=({"account_id": "primary"},),
    )[0]

    assert binding.binding_status == "error"
    assert binding.provider_folder_id == ""


def test_folder_materializer_recovers_from_concurrent_exact_create() -> None:
    provider = _ConcurrentCreateProvider(
        [(), (ProviderFolder("created-work", "工作", FolderRole.UNBOUND),)]
    )

    binding = _folder_coordinator(provider).create_and_verify_bindings(
        category_key="work",
        provider_folder_name="工作",
        enabled_accounts=({"account_id": "primary"},),
    )[0]

    assert provider.created == ["工作"]
    assert binding.binding_status == "active"
    assert binding.provider_folder_id == "created-work"


def test_real_imap_materializer_relists_after_create_no_and_binds_concurrent_folder() -> (
    None
):
    provider_module = import_module("app.email_provider_actions")

    class ConcurrentCreateSession:
        def __init__(self) -> None:
            self.list_calls = 0

        def list(self):
            self.list_calls += 1
            folders = [b'(\\Inbox) "/" "INBOX"']
            if self.list_calls == 2:
                folders.append(b'() "/" "Work"')
            return "OK", folders

        def create(self, mailbox: str):
            assert mailbox == "Work"
            return "NO", [b"already exists"]

        def logout(self):
            return "BYE", [b"logout"]

    session = ConcurrentCreateSession()
    provider = provider_module.ImapDeterministicProvider(session, account_id="primary")
    binding = _folder_coordinator(provider).create_and_verify_bindings(
        category_key="work",
        provider_folder_name="Work",
        enabled_accounts=({"account_id": "primary"},),
    )[0]

    assert session.list_calls == 2
    assert binding.binding_status == "active"
    assert binding.provider_folder_id == "Work"


@pytest.mark.parametrize(
    "system_role",
    (
        FolderRole.INBOX,
        FolderRole.JUNK,
        FolderRole.TRASH,
        FolderRole.SENT,
        FolderRole.DRAFT,
    ),
)
def test_business_folder_materializer_rejects_exact_system_folder_match(
    system_role: FolderRole,
) -> None:
    provider = _FolderProvider(
        [(ProviderFolder("system-folder", "工作", system_role),)]
    )

    binding = _folder_coordinator(provider).create_and_verify_bindings(
        category_key="work",
        provider_folder_name="工作",
        enabled_accounts=({"account_id": "primary"},),
    )[0]

    assert binding.binding_status == "error"
    assert binding.provider_folder_id == ""
    assert provider.created == []


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
        publish_provider_observation_change=lambda: None,
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
        lambda account_id: {
            "account_id": account_id,
            "email_address": "derek@stardust.ai",
        },
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
    ) == (entry,)
    assert source_events == [("fetch", "INBOX", 42, 6, 2), "logout"]
    assert execution_calls == []

    effect = SimpleNamespace(
        account_id="account-1",
        entry_reference=entry.reference,
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
    assert execution_calls[0][2]["connected_recipient"] == "derek@stardust.ai"
    assert callable(execution_calls[0][2]["email_otp_resolver"])
    assert execution_calls[0][2]["session_manager"] is operation.browser_session_manager
    assert callable(operation.open_user_handoff)
    assert "automatic" not in execution_calls[0][2]
    assert execution_calls[0][2]["executed_prefix_length"] == 0


def test_execution_resolves_selected_candidate_before_executing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.email_classifier_agent import (
        AgentClassificationResult,
        durable_agent_classification_result,
    )

    module = _module()
    selected_url = "https://selected.example.com/unsubscribe?token=selected"
    unrelated_url = "https://unrelated.example.net/unsubscribe?token=unrelated"
    provider_message = parse_rfc822_message(
        (
            b"From: sender@example.com\r\n"
            b"Message-ID: <multi-origin@example.com>\r\n"
            b"List-Unsubscribe: <"
            + selected_url.encode()
            + b">, <"
            + unrelated_url.encode()
            + b">\r\n\r\nBody"
        ),
        account_id="account-1",
        folder="INBOX",
        uidvalidity=42,
        uid=8,
    )
    entries = extract_unsubscribe_entries(
        list_unsubscribe=str(provider_message["listUnsubscribe"])
    )
    selected = entries[0]
    result = AgentClassificationResult(
        category="junk",
        important=False,
        certainty="certain",
        confidence=0.99,
        reason="Unwanted promotion.",
        unsubscribe_candidate_index=selected.index,
        unsubscribe_url=selected.private_url,
    )
    plan = module._agent_classification_action_plan(
        classification_id=809,
        account_id="account-1",
        result=result,
        unsubscribe_selection=durable_agent_classification_result(result, entries),
        folder_targets={},
        config_version="config-v1",
        created_at=datetime(2026, 9, 8, tzinfo=timezone.utc),
    )
    selection = plan.action_parameters[EmailAction.UNSUBSCRIBE]
    assert selection["candidate_digest"] == selected.reference.removeprefix(
        "unsubscribe-entry:"
    )
    execution_calls: list[tuple[object, tuple[object, ...]]] = []
    sentinel_result = object()

    def fake_execute(effect, resolved_entries, **_kwargs):
        execution_calls.append((effect, resolved_entries))
        return sentinel_result

    monkeypatch.setattr(
        "app.email_unsubscribe.execute_unsubscribe_in_dedicated_profile",
        fake_execute,
    )

    class Source:
        def fetch_uid_batch(self, *_args, **_kwargs):
            return SimpleNamespace(uidvalidity=42, messages=(provider_message,))

        def logout(self):
            return None

    monkeypatch.setattr(
        module,
        "_build_email_source_factory",
        lambda _settings: lambda _account: Source(),
    )
    operation = module.build_audited_email_unsubscribe_operation(
        SimpleNamespace(db_path=tmp_path / "multi-origin.sqlite3", workspace=tmp_path)
    )
    monkeypatch.setattr(
        operation.email_store,
        "get_account",
        lambda account_id: {
            "account_id": account_id,
            "email_address": "derek@stardust.ai",
        },
    )
    locator = EmailProviderLocator(
        account_id="account-1",
        folder="INBOX",
        uidvalidity=42,
        uid=8,
        rfc_message_id="<multi-origin@example.com>",
        thread_id="thread-multi-origin",
    )

    resolved = operation.resolve_entries(
        locator,
        str(selection["candidate_reference"]),
    )
    assert resolved == (selected,)
    effect = SimpleNamespace(
        account_id="account-1",
        entry_reference=selected.reference,
    )
    assert (
        operation.execute_effect(
            effect,
            resolved,
            owner={"owner_id": "audit", "generation": 1, "lease_token": "lease"},
            executed_prefix_length=0,
        )
        is sentinel_result
    )
    assert execution_calls == [(effect, (selected,))]


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
        category=EmailCategory.JUNK,
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
            category=EmailCategory.JUNK,
            confidence=1.0,
            margin=1.0,
            probabilities={EmailCategory.JUNK: 1.0},
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
            "action_identity": payload["action_identity"],
            "description": "Unsubscribe the current subscription",
            "capability": "email_browser",
            "operation": "unsubscribe",
            "target": {
                "action_identity": payload["action_identity"],
                "account_id": account_id,
                "stable_message_identity": stable_identity,
                "thread_identity": thread_identity,
                "entry_reference": projected_entry["reference"],
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
            # Required on ConsumerAgentResult since 310234e8; the Audit binding
            # re-reads this persisted result, so omitting them makes the whole
            # proposal unreadable rather than just skipping an assertion.
            "rule_coverage": 1.0,
            "information_completeness": 1.0,
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


def test_worker_closes_resident_online_batcher_when_lifecycle_returns():
    module = _module()
    events = []
    model = SimpleNamespace(close=lambda: events.append("model-close"))
    dependencies = _dependencies(events, model=model)

    module.run_email_worker(
        SimpleNamespace(),
        dependencies=dependencies,
        thread_factory=lambda **_kwargs: SimpleNamespace(start=lambda: None),
        wait=lambda: events.append("wait"),
        output=StringIO(),
    )

    assert events[-2:] == ["wait", "model-close"]


@pytest.mark.parametrize("close_raises", (False, True))
def test_worker_closes_online_batcher_once_on_error_without_masking_original(
    close_raises,
):
    module = _module()
    events = []

    def close():
        events.append("model-close")
        if close_raises:
            raise ValueError("close failed")

    dependencies = _dependencies(events, model=SimpleNamespace(close=close))

    with pytest.raises(RuntimeError, match="monitor failed"):
        module.run_email_worker(
            SimpleNamespace(),
            dependencies=dependencies,
            thread_factory=lambda **_kwargs: SimpleNamespace(start=lambda: None),
            wait=lambda: (_ for _ in ()).throw(RuntimeError("monitor failed")),
            output=StringIO(),
        )

    assert events.count("model-close") == 1


def test_worker_lifecycle_remains_compatible_with_model_without_close():
    module = _module()
    events = []

    module.run_email_worker(
        SimpleNamespace(),
        dependencies=_dependencies(events, model=object()),
        thread_factory=lambda **_kwargs: SimpleNamespace(start=lambda: None),
        wait=lambda: events.append("wait"),
        output=StringIO(),
    )

    assert events[-1] == "wait"


@pytest.mark.parametrize("close_raises", (False, True))
def test_worker_closes_model_once_when_startup_dependency_build_fails(close_raises):
    module = _module()
    events = []

    def close():
        events.append("model-close")
        if close_raises:
            raise ValueError("close failed")

    model = SimpleNamespace(close=close)
    bootstrap = SimpleNamespace(
        load_enabled_accounts=lambda: ({"account_id": "account-1"},),
        load_active_model=lambda: model,
        build_dependencies=lambda *_args: (_ for _ in ()).throw(
            RuntimeError("dependency build failed")
        ),
    )

    with pytest.raises(
        module.EmailWorkerStartupError, match="dependency construction failed"
    ) as failure:
        module.run_email_worker(
            SimpleNamespace(), dependencies=bootstrap, output=StringIO()
        )

    assert isinstance(failure.value.__cause__, RuntimeError)
    assert events == ["model-close"]


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
    assert components[0][1].keywords["provider_observation_complete"] is (
        dependencies.publish_provider_observation_change
    )


def test_successful_provider_scan_completion_publishes_training_observation_event():
    module = _module()
    events = []

    module.run_scan_and_direct_actions_loop(
        ({"account_id": "account-1", "scan_interval_seconds": 60},),
        object(),
        scan_account=lambda _account, _model: {"persisted_count": 1},
        run_direct_actions_once=lambda: None,
        provider_observation_complete=lambda: events.append("published"),
        sleep=lambda _seconds: None,
        max_cycles=1,
    )

    assert events == ["published"]


def test_successful_classifier_cycle_replaces_stale_failure_health() -> None:
    health = []

    _module().run_scan_and_direct_actions_loop(
        ({"account_id": "account-1", "scan_interval_seconds": 60},),
        object(),
        scan_account=lambda _account, _model: {"persisted_count": 0},
        run_direct_actions_once=lambda: None,
        run_classification_once=lambda: None,
        record_health=lambda scope, payload: health.append((scope, payload)),
        sleep=lambda _seconds: None,
        max_cycles=1,
    )

    assert (
        "component:email-classifier-agent",
        {"status": "ready", "failures": 0},
    ) in health


def test_idle_scan_without_provider_or_action_change_does_not_request_observation():
    module = _module()
    events = []

    module.run_scan_and_direct_actions_loop(
        ({"account_id": "account-1", "scan_interval_seconds": 60},),
        object(),
        scan_account=lambda _account, _model: {"persisted_count": 0},
        run_direct_actions_once=lambda: None,
        provider_observation_complete=lambda: events.append("requested"),
        sleep=lambda _seconds: None,
        max_cycles=1,
    )

    assert events == []


def test_production_folder_result_tuple_without_changes_does_not_request_observation():
    module = _module()
    from app.email_classifier_scan import EmailScanResult

    events = []

    module.run_scan_and_direct_actions_loop(
        ({"account_id": "account-1", "scan_interval_seconds": 60},),
        object(),
        scan_account=lambda _account, _model: (
            EmailScanResult(3, 0, 0, 0),
            EmailScanResult(2, 0, 0, 0),
        ),
        run_direct_actions_once=lambda: None,
        provider_observation_complete=lambda: events.append("requested"),
        sleep=lambda _seconds: None,
        max_cycles=1,
    )

    assert events == []


def test_production_folder_result_tuple_requests_once_when_any_folder_changes():
    module = _module()
    from app.email_classifier_scan import EmailScanResult

    events = []

    module.run_scan_and_direct_actions_loop(
        ({"account_id": "account-1", "scan_interval_seconds": 60},),
        object(),
        scan_account=lambda _account, _model: (
            EmailScanResult(3, 0, 0, 0),
            EmailScanResult(2, 1, 0, 0),
        ),
        run_direct_actions_once=lambda: None,
        provider_observation_complete=lambda: events.append("requested"),
        sleep=lambda _seconds: None,
        max_cycles=1,
    )

    assert events == ["requested"]


def test_explicit_provider_folder_or_important_change_requests_observation():
    module = _module()
    events = []

    module.run_scan_and_direct_actions_loop(
        ({"account_id": "account-1", "scan_interval_seconds": 60},),
        object(),
        scan_account=lambda _account, _model: {
            "persisted_count": 0,
            "provider_changed": True,
        },
        run_direct_actions_once=lambda: None,
        provider_observation_complete=lambda: events.append("requested"),
        sleep=lambda _seconds: None,
        max_cycles=1,
    )

    assert events == ["requested"]


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

    assert calls[0] == ("claim", module.EMAIL_TASK_CLAIM_BATCH, "email")
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
            assert (limit, channel) == (module.EMAIL_TASK_CLAIM_BATCH, "email")
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


def _unresolvable_selection_task(*, error: str = ""):
    return SimpleNamespace(
        id=77,
        attempts=1,
        error=error,
        execution_generation="generation-7",
        conversation_id="email-thread:abc",
        trigger_message_id="email-action:def",
        trigger_message_json=json.dumps(
            {"schema": "email_agent_action.v1", "action_type": "unsubscribe"}
        ),
    )


class _UnresolvableSelectionStore:
    def __init__(self, task):
        self.task = task
        self.deferred = []
        self.completed = []
        self.failed = []
        self.errors = []

    def claim_reply_tasks(self, limit, *, channel):
        return [self.task]

    def defer_reply_task(self, task_id, error, *, expected_execution_generation, available_at):
        self.deferred.append((task_id, error, available_at))

    def complete_reply_task(self, task_id, *, expected_execution_generation):
        self.completed.append((task_id, expected_execution_generation))

    def fail_reply_task(self, task_id, error, *, expected_execution_generation):
        self.failed.append((task_id, error))

    def record_error(self, conversation_id, trigger_message_id, kind, detail):
        self.errors.append((conversation_id, trigger_message_id, kind, detail))


def _raise_unresolvable_selection(_task):
    from app.email_unsubscribe import UnsubscribeSelectionUnresolvable

    raise UnsubscribeSelectionUnresolvable("unsubscribe candidate index changed")


def test_unresolvable_unsubscribe_selection_is_retried_before_it_is_believed():
    """An unreadable message looks the same from here as a link that is gone."""
    module = _module()
    task = _unresolvable_selection_task()
    store = _UnresolvableSelectionStore(task)

    module.run_email_agent_task_loop(
        store,
        SimpleNamespace(process=lambda *_a, **_k: pytest.fail("task executed")),
        load_task_context=_raise_unresolvable_selection,
        finalize_task=lambda *_a: pytest.fail("task finalized"),
        sleep=lambda _seconds: None,
        max_cycles=1,
    )

    assert store.completed == []
    assert store.failed == []
    assert len(store.deferred) == 1
    task_id, error, available_at = store.deferred[0]
    assert task_id == 77
    assert error.startswith(
        f"{module.UNRESOLVED_UNSUBSCRIBE_SELECTION_ERROR}:1:"
    )
    assert available_at


def test_unresolvable_unsubscribe_selection_ends_as_a_skip_not_a_failure():
    """The authorization names one entry; a rerun can only reach the same end.

    Leaving the task `failed` made the queue read as broken and invited reruns
    that could never succeed, which is how 94 email tasks accumulated on
    2026-09-10.
    """
    module = _module()
    task = _unresolvable_selection_task(
        error=(
            f"{module.UNRESOLVED_UNSUBSCRIBE_SELECTION_ERROR}:"
            f"{module.UNRESOLVED_UNSUBSCRIBE_RECHECKS - 1}:"
            "unsubscribe candidate index changed"
        )
    )
    store = _UnresolvableSelectionStore(task)

    module.run_email_agent_task_loop(
        store,
        SimpleNamespace(process=lambda *_a, **_k: pytest.fail("task executed")),
        load_task_context=_raise_unresolvable_selection,
        finalize_task=lambda *_a: pytest.fail("task finalized"),
        sleep=lambda _seconds: None,
        max_cycles=1,
    )

    assert store.deferred == []
    assert store.failed == []
    assert store.completed == [(77, "generation-7")]
    assert len(store.errors) == 1
    conversation_id, trigger_message_id, kind, detail = store.errors[0]
    assert (conversation_id, trigger_message_id) == (
        "email-thread:abc",
        "email-action:def",
    )
    assert kind == "email_unsubscribe_candidate_unresolvable"
    assert "unsubscribe candidate index changed" in detail


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
        "category": "junk",
        "classification_source": "user",
        "confidence": 1.0,
        "model_id": "email-model:test",
        "config_version": "email-config:test",
        "action_parameters": {},
        "unsubscribe_entries": [
            {
                "index": entry.index,
                "source": entry.source.value,
                "digest": entry.reference.removeprefix("unsubscribe-entry:"),
                "reference": entry.reference,
            }
        ],
        "unsubscribe_authentication": None,
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
    training_events = []

    class SnapshotJob:
        def __init__(self, **kwargs):
            training_events.append(("created", kwargs))

        def publish_observations(self, observations):
            training_events.append(("published", tuple(observations)))
            return "snapshot-published"

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
    provider_observations = ({"stable_message_identity": "provider-message-1"},)
    observation_events = []

    class ObservationJob:
        def __init__(self, **kwargs):
            observation_events.append(("job-created", kwargs))

        def cached_observations(self):
            return provider_observations

        def initialized(self):
            return False

    class ObservationCoordinator:
        def __init__(self, *, job, accounts_loader, publish, **kwargs):
            observation_events.append(("coordinator-created", kwargs))
            self.accounts_loader = accounts_loader
            self.publish = publish

        def request(self, _key=None):
            observation_events.append(("requested",))

        def request_initialization(self):
            observation_events.append(("initialization-requested",))

        def tick(self):
            observation_events.append(("provider-read", tuple(self.accounts_loader())))
            return self.publish(provider_observations)

    class ChangeDetector:
        def __init__(self, *, job, accounts_loader, request):
            observation_events.append(("detector-created", job))
            self.accounts_loader = accounts_loader
            self.request = request

        def tick(self):
            observation_events.append(
                ("fingerprint-read", tuple(self.accounts_loader()))
            )
            self.request("provider-change:test")
            return True

    monkeypatch.setattr(
        "app.email_training_observer.ProviderTrainingObservationJob",
        ObservationJob,
    )
    monkeypatch.setattr(
        "app.email_training_observer.TrainingObservationCoordinator",
        ObservationCoordinator,
    )
    monkeypatch.setattr(
        "app.email_training_observer.ProviderTrainingChangeDetector",
        ChangeDetector,
    )

    settings = SimpleNamespace(
        db_path=tmp_path / "worker.sqlite3",
        workspace=tmp_path,
        dry_run=False,
    )
    bootstrap = module.build_email_worker_dependencies(
        settings, training_snapshot_job_factory=SnapshotJob
    )
    dependencies = bootstrap.build_dependencies(
        ({"account_id": "account-1", "enabled": True},),
            SimpleNamespace(
                loaded=SimpleNamespace(model_id="email-model:test", classifier=object()),
                tick=lambda: SimpleNamespace(training_run=None),
            ),
    )

    assert dependencies.orchestrator is sentinel
    assert dependencies.run_direct_actions_once.args[1] is direct_factory
    assert isinstance(
        dependencies.run_classification_once.keywords["action_task_producer"],
        EmailActionTaskProducer,
    )
    assert not hasattr(dependencies, "unsubscribe_consumer")
    assert not hasattr(module, "_build_email_unsubscribe_consumer")
    assert not hasattr(module, "build_email_unsubscribe_operation")
    assert not hasattr(module, "run_email_unsubscribe_task")
    first_idle = dependencies.training_tick()
    second_idle = dependencies.training_tick()
    assert first_idle.training_run is None
    assert second_idle.training_run is None
    assert [event[0] for event in training_events] == ["created"]
    assert dependencies.publish_provider_observation_change() is None
    assert [event[0] for event in training_events] == ["created"]
    assert dependencies.training_observation_tick() == "snapshot-published"
    assert [event[0] for event in training_events] == ["created", "published"]
    assert training_events[1][1] == provider_observations
    assert isinstance(training_events[0][1]["store"], EmailStore)
    assert observation_events[0][1]["batch_size"] == 50
    assert [event[0] for event in observation_events] == [
        "job-created",
        "coordinator-created",
        "initialization-requested",
        "detector-created",
        "requested",
        "fingerprint-read",
        "requested",
        "provider-read",
    ]


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
        "app.audit_agent.AuditAgentRunner",
        build_audit,
    )
    monkeypatch.setattr(
        "app.agent_orchestrator.AgentOrchestrator",
        lambda **kwargs: object(),
    )
    classifier_skill = "---\nname: ceo-email-classifier\ndescription: Use when testing\nmetadata:\n  managed_by: ceo-agent-service\n---\n"
    snapshot = SimpleNamespace(
        revisions=(
            SimpleNamespace(
                skill_id=17,
                revision_number=1,
                sha256=sha256(classifier_skill.encode("utf-8")).hexdigest(),
                content=classifier_skill,
                source="repository:skills",
            ),
        )
    )
    settings = SimpleNamespace(
        db_path=tmp_path / "worker.sqlite3", workspace=tmp_path, dry_run=False
    )

    module._build_agent_orchestrator(
        settings, AutoReplyStore(settings.db_path), runtime_skill_snapshot=snapshot
    )

    assert captured["consumer"]["runtime_skill_snapshot"] is snapshot
    assert "runtime_skill_snapshot" not in captured["audit"]


def test_email_dependency_builder_resolves_one_snapshot_for_agent_orchestrator(
    tmp_path, monkeypatch
):
    module = _module()
    classifier_skill = "---\nname: ceo-email-classifier\ndescription: Use when testing\nmetadata:\n  managed_by: ceo-agent-service\n---\n"
    snapshot = SimpleNamespace(
        revisions=(
            SimpleNamespace(
                skill_id=17,
                revision_number=1,
                sha256=sha256(classifier_skill.encode("utf-8")).hexdigest(),
                content=classifier_skill,
                source="repository:skills",
            ),
        )
    )
    calls = []
    monkeypatch.setattr(
        "app.managed_skills.resolve_pending_runtime_skills",
        lambda store, *, pid: calls.append((store, pid)) or snapshot,
    )
    captured = {}
    monkeypatch.setattr(
        module,
        "_build_agent_orchestrator",
        lambda _settings, _store, *, runtime_skill_snapshot: (
            captured.setdefault("snapshot", runtime_skill_snapshot) or object()
        ),
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


def test_email_dependency_builder_applies_classifier_model_override_only(
    tmp_path, monkeypatch
):
    module = _module()
    classifier_skill = "---\nname: ceo-email-classifier\ndescription: Use when testing\nmetadata:\n  managed_by: ceo-agent-service\n---\n"
    snapshot = SimpleNamespace(
        revisions=(
            SimpleNamespace(
                skill_id=17,
                revision_number=1,
                sha256=sha256(classifier_skill.encode("utf-8")).hexdigest(),
                content=classifier_skill,
                source="repository:skills",
            ),
        )
    )
    monkeypatch.setenv("CEO_EMAIL_CLASSIFIER_MODEL", "gpt-5.6-luna")
    monkeypatch.setattr(
        "app.managed_skills.resolve_pending_runtime_skills",
        lambda _store, *, pid: snapshot,
    )
    routed_calls = []
    monkeypatch.setattr(
        "app.agent_runtime_production.build_production_routed_codex_execution",
        lambda **kwargs: routed_calls.append(kwargs) or object(),
    )
    description_agents = []

    class DescriptionOrchestrator:
        def __init__(self, *, agent, **_kwargs):
            description_agents.append(agent)

    monkeypatch.setattr(
        "app.email_description_optimizer.DescriptionOptimizationOrchestrator",
        DescriptionOrchestrator,
    )
    monkeypatch.setattr(
        "app.email_description_optimizer.RoutedDescriptionOptimizerAgent",
        lambda _execution: lambda payload: payload,
    )
    monkeypatch.setattr(
        module,
        "_build_agent_orchestrator",
        lambda *_args, **_kwargs: object(),
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
    assert description_agents[0]({"candidate": "test"}) == {"candidate": "test"}

    assert len(routed_calls) == 2
    assert routed_calls[0]["codex_oauth_model"] == "gpt-5.6-luna"
    assert "codex_oauth_model" not in routed_calls[1]


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

    module._run_next_direct_action(
        Store(), lambda _account_id: SimpleNamespace(execute=lambda _action: result)
    )

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
            "imap_move_mode": "copy_as_move",
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
        lambda host, username, password, **kwargs: (
            calls.append((host, username, password, kwargs)) or SimpleNamespace()
        ),
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
            {
                "port": 993,
                "account_id": "account-a",
                "move_mode": "copy_as_move",
            },
        ),
        (
            "imap-b.example.com",
            "b@example.com",
            "imap-secret-b",
            {"port": 1993, "account_id": "account-b", "move_mode": "move"},
        ),
        (
            "imap-a.example.com",
            "a@example.com",
            "imap-secret-a",
            {
                "port": 993,
                "account_id": "account-a",
                "move_mode": "copy_as_move",
            },
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
            lambda account_id: Executor() if account_id == "account-direct" else None,
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
        assert (
            db.execute(
                "select folder, uidvalidity, uid from email_classifications where id=901"
            ).fetchone()
            == expected_classification
        )
        assert (
            db.execute(
                """
            select folder, uidvalidity, uid from email_messages
            where stable_message_identity=?
            """,
                ("account-direct:message-id:<direct-901@example.com>",),
            ).fetchone()
            == expected_message
        )
        assert (
            db.execute("select count(*) from email_action_attempts").fetchone()[0] == 0
        )


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
        run_direct_actions_once=lambda: (
            calls.append("action") or SimpleNamespace(status="done")
        ),
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
        run_direct_actions_once=lambda: (
            calls.append("action") or SimpleNamespace(status="done")
        ),
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
    categories = contracts.INITIAL_EMAIL_CATEGORY_KEYS
    rows = [
        {
            "category": category,
            "description": category,
            "enabled": True,
            "threshold": 0.8,
            "actions": ["mark_read"]
            if category == contracts.EmailCategory.WORK.value
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
                category: {
                    "precision": 0.99,
                    "validation_sample_count": 35,
                    "validation_positive_support": 35,
                    "configured_threshold": 0.8,
                    "evaluated_threshold": 0.8,
                    "auto_action_eligible": category
                    == contracts.EmailCategory.WORK.value,
                    "eligibility_reason": (
                        "precision_and_sample_gate_met"
                        if category == contracts.EmailCategory.WORK.value
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


def test_scan_config_accepts_exact_current_nine_category_rows():
    module = _module()
    contracts = import_module("app.email_classifier_contracts")
    rows = [
        {
            "category": category,
            "description": category,
            "enabled": True,
            "threshold": 0.8,
            "actions": [],
            "action_parameters": {},
            "config_version": "config-current-nine-v1",
        }
        for category in contracts.INITIAL_EMAIL_CATEGORY_KEYS
    ]

    config = module._scan_config(SimpleNamespace(list_configs=lambda: rows), None)

    assert config.config_version == "config-current-nine-v1"
    assert config.config_version != "email-config-missing-v1"
    assert tuple(config.thresholds) == contracts.INITIAL_EMAIL_CATEGORY_KEYS
    assert tuple(config.actions) == contracts.INITIAL_EMAIL_CATEGORY_KEYS
    assert tuple(config.category_eligibility) == contracts.INITIAL_EMAIL_CATEGORY_KEYS
    assert tuple(config.action_parameters) == contracts.INITIAL_EMAIL_CATEGORY_KEYS
    assert tuple(config.category_enabled) == contracts.INITIAL_EMAIL_CATEGORY_KEYS
    assert all(
        type(category) is str
        for mapping in (
            config.thresholds,
            config.actions,
            config.category_eligibility,
            config.action_parameters,
            config.category_enabled,
        )
        for category in mapping
    )


@pytest.mark.parametrize("status", ["candidate", "rejected", "failed", "previous"])
def test_scan_config_non_active_model_record_is_never_action_eligible(status: str):
    module = _module()
    contracts = import_module("app.email_classifier_contracts")
    rows = [
        {
            "category": category,
            "description": category,
            "enabled": True,
            "threshold": 0.85,
            "actions": ["label"]
            if category == contracts.EmailCategory.WORK.value
            else [],
            "action_parameters": (
                {"label": {"labels": ["Work"]}}
                if category == contracts.EmailCategory.WORK.value
                else {}
            ),
            "config_version": "config-status-v1",
        }
        for category in contracts.INITIAL_EMAIL_CATEGORY_KEYS
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
            "category": category,
            "description": category,
            "enabled": True,
            "threshold": 0.8,
            "actions": ["mark_read"],
            "action_parameters": {},
            "config_version": "config-v4",
        }
        for category in contracts.INITIAL_EMAIL_CATEGORY_KEYS
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
            assert (limit, channel) == (module.EMAIL_TASK_CLAIM_BATCH, "email")
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


def test_deferred_orchestration_result_requeues_the_email_task():
    module = _module()
    task = SimpleNamespace(
        id=8,
        attempts=1,
        execution_generation="generation-8",
        conversation_id="conversation-8",
        conversation_title="Email unsubscribe",
        trigger_message_id="trigger-8",
        trigger_sender="sender@example.com",
        trigger_text="unsubscribe",
    )
    captured = {}

    class Store:
        def get_agent_run(self, run_id):
            raise AssertionError("a deferred result must not require a final run")

        def defer_reply_task(self, task_id, error, *, expected_execution_generation, available_at):
            captured.update(
                task_id=task_id,
                error=error,
                generation=expected_execution_generation,
                available_at=available_at,
            )

    result = SimpleNamespace(
        status="failed_retryable",
        final_run_id=0,
        summary="runtime not ready",
        error=SimpleNamespace(
            code="runtime_provider_unreachable", authorization_required=False
        ),
    )

    module._finalize_email_task(Store(), task, result)

    assert captured["task_id"] == 8
    assert captured["error"] == "runtime_provider_unreachable"
    assert captured["generation"] == "generation-8"
    assert captured["available_at"]


def test_bare_authorization_required_result_is_failed():
    module = _module()
    task = SimpleNamespace(
        id=7,
        execution_generation="generation-7",
        conversation_id="conversation-7",
        conversation_title="Email unsubscribe",
        trigger_message_id="trigger-7",
        trigger_sender="sender@example.com",
        trigger_text="unsubscribe",
    )
    run = SimpleNamespace(
        id=70,
        codex_session_id="",
        transcript_start_line=0,
        transcript_end_line=0,
        tool_events=[],
    )
    captured = {}

    class Store:
        def get_agent_run(self, run_id):
            assert run_id == 70
            return run

        def finalize_orchestrated_reply_task(self, **kwargs):
            captured.update(kwargs)

    result = SimpleNamespace(
        status="failed_terminal",
        final_run_id=70,
        summary="authorization_required",
        error=SimpleNamespace(code="authorization_required", authorization_required=True),
    )

    module._finalize_email_task(Store(), task, result)

    assert captured["task_status"] == "failed"
    assert captured["send_status"] == "failed"
    assert captured["send_error"] == "authorization_required"


def test_uncertain_unsubscribe_with_durable_step_becomes_needs_human():
    module = _module()
    task = SimpleNamespace(
        id=71,
        execution_generation="generation-71",
        channel="email",
        trigger_message_id="email-action:uncertain-71",
        conversation_id="email-thread:71",
        conversation_title="Email unsubscribe",
        trigger_sender="sender@example.com",
        trigger_text="Immutable ActionPlan authorizes unsubscribe.",
    )
    run = SimpleNamespace(
        id=710,
        codex_session_id="",
        transcript_start_line=0,
        transcript_end_line=0,
        tool_events=[],
    )
    captured = {}

    class Store:
        def get_email_unsubscribe_claim(self, action_identity):
            assert action_identity == task.trigger_message_id
            return {"status": "uncertain", "phase": "effect_uncertain"}

        def list_email_unsubscribe_steps(self, action_identity):
            assert action_identity == task.trigger_message_id
            return [{"sequence": 1, "operation": "open_entry", "state": "completed"}]

        def get_email_unsubscribe_receipt(self, action_identity):
            assert action_identity == task.trigger_message_id
            return None

        def get_agent_run(self, run_id):
            assert run_id == run.id
            return run

        def finalize_orchestrated_reply_task(self, **kwargs):
            captured.update(kwargs)

    result = SimpleNamespace(
        status="failed_terminal",
        final_run_id=run.id,
        summary="unsubscribe_operation_rejected:EmailUnsubscribeClaimConflict",
        error=SimpleNamespace(
            code="unsubscribe_operation_rejected:EmailUnsubscribeClaimConflict",
            authorization_required=False,
        ),
    )

    module._finalize_email_task(Store(), task, result)

    assert captured["task_status"] == "done"
    assert captured["send_status"] == "needs_human"
    assert captured["send_error"] == "email_unsubscribe_effect_uncertain"
    assert len(json.loads(captured["human_decision_options_json"])) == 2


def test_structured_authorization_result_keeps_needs_human_options():
    module = _module()
    task = SimpleNamespace(
        id=7,
        execution_generation="generation-7",
        conversation_id="conversation-7",
        conversation_title="Email unsubscribe",
        trigger_message_id="trigger-7",
        trigger_sender="sender@example.com",
        trigger_text="unsubscribe",
    )
    run = SimpleNamespace(
        id=70,
        codex_session_id="",
        transcript_start_line=0,
        transcript_end_line=0,
        tool_events=[],
    )
    captured = {}

    class Store:
        def get_agent_run(self, run_id):
            return run

        def finalize_orchestrated_reply_task(self, **kwargs):
            captured.update(kwargs)

    options = (
        DecisionOption(
            key="authorize",
            label="授权",
            instruction="允许继续处理。",
            consequence="服务继续执行当前操作。",
        ),
        DecisionOption(
            key="stop",
            label="停止",
            instruction="停止当前操作。",
            consequence="不再继续处理。",
        ),
    )
    result = SimpleNamespace(
        status="needs_human",
        final_run_id=70,
        summary="authorization_required",
        audit_result=SimpleNamespace(decision_options=options),
        error=SimpleNamespace(code="authorization_required", authorization_required=True),
    )

    module._finalize_email_task(Store(), task, result)

    assert captured["task_status"] == "done"
    assert captured["send_status"] == "needs_human"
    assert json.loads(captured["human_decision_options_json"]) == [
        option.model_dump(mode="json") for option in options
    ]


def test_domain_authorization_rejection_remains_failed():
    module = _module()
    task = SimpleNamespace(
        id=8,
        execution_generation="generation-8",
        conversation_id="conversation-8",
        conversation_title="Email unsubscribe",
        trigger_message_id="trigger-8",
        trigger_sender="sender@example.com",
        trigger_text="unsubscribe",
    )
    run = SimpleNamespace(
        id=80,
        codex_session_id="",
        transcript_start_line=0,
        transcript_end_line=0,
        tool_events=[],
    )
    captured = {}

    class Store:
        def get_agent_run(self, run_id):
            assert run_id == 80
            return run

        def finalize_orchestrated_reply_task(self, **kwargs):
            captured.update(kwargs)

    result = SimpleNamespace(
        status="failed_terminal",
        final_run_id=80,
        summary="email_unsubscribe_risk_rejected",
        error=SimpleNamespace(
            code="email_unsubscribe_risk_rejected",
            authorization_required=True,
        ),
    )

    module._finalize_email_task(Store(), task, result)

    assert captured["task_status"] == "failed"
    assert captured["send_status"] == "failed"
    assert captured["send_error"] == "email_unsubscribe_risk_rejected"


def test_email_orchestrator_can_refresh_runtime_capabilities(tmp_path):
    """Without this the worker has no snapshot for any route, ever.

    The capability registry is per process and a fresh child starts empty, so a
    worker that cannot refresh reports runtime_provider_unreachable on every
    poll for its whole life. Each email task then waits on routes that are
    healthy, and because that code is a route-pause wait rather than a failure
    it never surfaces as one.
    """
    from app.store import AutoReplyStore

    module = _module()
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    settings = SimpleNamespace(
        workspace=str(tmp_path),
        db_path=str(tmp_path / "worker.sqlite3"),
        dry_run=False,
    )

    orchestrator = module._build_agent_orchestrator(settings, store)

    for runner in (orchestrator.consumer, orchestrator.audit):
        assert callable(runner.refresh_runtime_capabilities)


def _route_refused_tool_event(message="This action was rejected due to unacceptable risk."):
    """The runtime declined to place the call, so there is no result at all."""
    return {
        "type": "item.completed",
        "item": {
            "type": "mcp_tool_call",
            "tool": "execute_audited_email_unsubscribe",
            "arguments": {"accepted_action": {"action_identity": "email-action:1"}},
            "error": {"message": message},
        },
    }


def _route_refusal_case(error: str = ""):
    module = _module()
    task = SimpleNamespace(
        id=9,
        attempts=1,
        error=error,
        execution_generation="generation-9",
        conversation_id="conversation-9",
        conversation_title="Email unsubscribe",
        trigger_message_id="trigger-9",
        trigger_sender="sender@example.com",
        trigger_text="unsubscribe",
    )
    run = SimpleNamespace(
        id=90,
        codex_session_id="",
        transcript_start_line=0,
        transcript_end_line=0,
        tool_events=[_route_refused_tool_event()],
    )
    captured = {}

    class Store:
        def get_agent_run(self, run_id):
            return run

        def finalize_orchestrated_reply_task(self, **kwargs):
            captured.update(kwargs)

        def defer_reply_task(self, task_id, error, **kwargs):
            captured["deferred"] = (task_id, error)

    result = SimpleNamespace(
        status="failed_terminal",
        final_run_id=90,
        summary="email_unsubscribe_risk_rejected",
        error=SimpleNamespace(
            code="email_unsubscribe_risk_rejected",
            authorization_required=True,
        ),
    )
    module._finalize_email_task(Store(), task, result)
    return module, captured


def test_route_refusing_the_call_is_retried_not_closed_as_a_decision():
    """Nothing decided anything: the audited tool never ran.

    The Audit model still has to report something, and what it reports reads
    like a refusal of the unsubscribe. The same call goes through on a route
    whose provider places it, so the task is retried rather than closed.
    """
    module, captured = _route_refusal_case()

    assert captured["deferred"] == (
        9,
        f"{module.ROUTE_REFUSED_UNSUBSCRIBE_ERROR}:1",
    )
    assert "task_status" not in captured


def test_every_route_refusing_the_call_remains_a_technical_failure():
    """Once retries are spent, the route failure is still not a decision."""
    module, captured = _route_refusal_case(
        error=(
            f"{_module().ROUTE_REFUSED_UNSUBSCRIBE_ERROR}:"
            f"{_module().ROUTE_REFUSED_UNSUBSCRIBE_RETRIES - 1}"
        )
    )

    assert captured["task_status"] == "failed"
    assert captured["send_status"] == "failed"
    assert captured["send_error"] == module.ROUTE_REFUSED_UNSUBSCRIBE_ERROR


def test_repeated_route_refusals_actually_reach_a_person():
    """The ladder has to advance, which a task.attempts counter cannot do.

    A deferral returns the attempt budget on purpose: claim adds one and
    defer_reply_task takes it away, so task.attempts is pinned across
    deferrals. A ladder keyed on it never escalates, and the task loops
    instead, which is how three route-refused tasks sat pending forever with
    458 and 105 agent runs in a single generation.
    """
    module = _module()
    error = ""
    seen = []
    for _ in range(module.ROUTE_REFUSED_UNSUBSCRIBE_RETRIES + 2):
        _module_again, captured = _route_refusal_case(error=error)
        if "deferred" in captured:
            error = captured["deferred"][1]
            seen.append(("deferred", error))
            continue
        seen.append(("finalized", captured.get("send_status")))
        break

    assert seen[-1] == ("finalized", "failed"), seen
    assert len(seen) == module.ROUTE_REFUSED_UNSUBSCRIBE_RETRIES, seen


def test_a_call_the_route_did_place_keeps_the_audited_outcome():
    """A refusal followed by a real result is not a refusal."""
    from app.email_unsubscribe_audit import audited_unsubscribe_route_refusal

    placed = {
        "type": "item.completed",
        "item": {
            "type": "mcp_tool_call",
            "tool": "execute_audited_email_unsubscribe",
            "arguments": {"accepted_action": {"action_identity": "email-action:1"}},
            "result": {"structured_content": {"status": "done", "outcome": "done"}},
        },
    }
    run = SimpleNamespace(tool_events=[_route_refused_tool_event(), placed])

    assert audited_unsubscribe_route_refusal(run) == ""


def _audited_unsubscribe_tool_event(outcome, *, status="done"):
    return {
        "type": "item.completed",
        "item": {
            "tool": "execute_audited_email_unsubscribe",
            "status": "completed",
            "result": {
                "structured_content": {
                    "status": status,
                    "outcome": outcome,
                    "receipt_id": f"unsubscribe-receipt:4d96610e8a5d070dbfbff1ed:{outcome}",
                    "evidence": "terminal-page",
                }
            },
        },
    }


def _finalize_audited_unsubscribe(module, result, tool_events, *, task_id=383232):
    task = SimpleNamespace(
        id=task_id,
        attempts=3,
        execution_generation="c529752524ec4378b919b499f6b2a200",
        conversation_id=f"conversation-{task_id}",
        conversation_title="Email unsubscribe",
        trigger_message_id=f"trigger-{task_id}",
        trigger_sender="sender@example.com",
        trigger_text="unsubscribe",
    )
    run = SimpleNamespace(
        id=result.final_run_id,
        codex_session_id="",
        transcript_start_line=0,
        transcript_end_line=0,
        tool_events=tool_events,
    )
    captured = {}

    class Store:
        def get_agent_run(self, run_id):
            assert run_id == result.final_run_id
            return run

        def finalize_orchestrated_reply_task(self, **kwargs):
            captured.update(kwargs)

        def defer_reply_task(self, *args, **kwargs):
            captured["deferred"] = True

    module._finalize_email_task(Store(), task, result)
    return captured


def test_audited_unsubscribe_login_skip_is_closed_as_no_action():
    module = _module()
    # Live task 383232: the audited tool returned a terminal skip receipt and
    # the Audit model reported failed/login_required for it anyway.
    result = SimpleNamespace(
        status="failed_terminal",
        final_run_id=11859,
        summary="login_required",
        error=SimpleNamespace(code="login_required", authorization_required=False),
    )

    captured = _finalize_audited_unsubscribe(
        module,
        result,
        [_audited_unsubscribe_tool_event("skipped_login_required")],
    )

    assert captured["task_status"] == "done"
    assert captured["send_status"] == "skipped"
    assert captured["send_error"] == ""
    assert captured["task_error"] == ""


def test_audited_unsubscribe_retryable_login_skip_is_not_deferred():
    module = _module()
    # Live task 383234, Audit run 16936: the same skip receipt after the model
    # called the terminal tool a second time. The domain rejection claimed an
    # authorization boundary under its own code, so the orchestrator deferred
    # it instead of ending the task.
    result = SimpleNamespace(
        status="failed_retryable",
        final_run_id=16936,
        summary="unsubscribe_operation_rejected:login_required",
        error=SimpleNamespace(
            code="unsubscribe_operation_rejected:login_required",
            authorization_required=True,
        ),
    )

    captured = _finalize_audited_unsubscribe(
        module,
        result,
        [
            _audited_unsubscribe_tool_event("skipped_login_required"),
            _audited_unsubscribe_tool_event("failed_browser", status="failed"),
        ],
        task_id=383234,
    )

    assert "deferred" not in captured
    assert captured["task_status"] == "done"
    assert captured["send_status"] == "skipped"
    assert captured["send_error"] == ""


@pytest.mark.parametrize(
    "outcome", ["skipped_no_reliable_entry", "already_unsubscribed"]
)
def test_audited_unsubscribe_no_work_skip_is_closed_as_no_action(outcome):
    module = _module()
    result = SimpleNamespace(
        status="failed_terminal",
        final_run_id=90,
        summary=outcome,
        error=SimpleNamespace(code="unsubscribe_entry_missing", authorization_required=False),
    )

    captured = _finalize_audited_unsubscribe(
        module,
        result,
        [_audited_unsubscribe_tool_event(outcome)],
    )

    assert captured["task_status"] == "done"
    assert captured["send_status"] == "skipped"
    assert captured["send_error"] == ""
    assert captured["task_error"] == ""


def test_audited_unsubscribe_skip_never_overrides_a_management_decision():
    module = _module()
    # Audit reported a real needs_human decision (a sensitive target, a budget
    # or approval boundary). The receipt says the browser step itself left
    # nothing to do, but the decision the person has to make is the result.
    result = SimpleNamespace(
        status="needs_human",
        final_run_id=92,
        summary="sender is a sensitive target",
        error=SimpleNamespace(
            code="email_unsubscribe_target_sensitive",
            authorization_required=False,
        ),
    )

    captured = _finalize_audited_unsubscribe(
        module,
        result,
        [_audited_unsubscribe_tool_event("already_unsubscribed")],
    )

    assert captured["task_status"] == "done"
    assert captured["send_status"] == "needs_human"
    assert captured["send_error"] == "email_unsubscribe_target_sensitive"


def test_audited_unsubscribe_skip_closes_a_bare_authorization_failure():
    module = _module()
    result = SimpleNamespace(
        status="failed_terminal",
        final_run_id=93,
        summary="authorization_required",
        error=SimpleNamespace(
            code="authorization_required",
            authorization_required=True,
        ),
    )

    captured = _finalize_audited_unsubscribe(
        module,
        result,
        [_audited_unsubscribe_tool_event("skipped_no_reliable_entry")],
    )

    assert captured["task_status"] == "done"
    assert captured["send_status"] == "skipped"
    assert captured["send_error"] == ""


def test_audited_unsubscribe_browser_failure_remains_failed():
    module = _module()
    result = SimpleNamespace(
        status="failed_terminal",
        final_run_id=91,
        summary="failed_browser",
        error=SimpleNamespace(code="unsubscribe_browser_unavailable", authorization_required=False),
    )

    captured = _finalize_audited_unsubscribe(
        module,
        result,
        [_audited_unsubscribe_tool_event("failed_browser", status="failed")],
    )

    assert captured["task_status"] == "failed"
    assert captured["send_status"] == "failed"
    assert captured["send_error"] == "unsubscribe_browser_unavailable"


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


def test_training_observation_success_replaces_stale_failure_health() -> None:
    calls = 0
    health = []

    def observation_tick():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ConnectionError("private provider detail")

    _module().run_training_scheduler_loop(
        lambda: None,
        training_observation_tick=observation_tick,
        record_health=lambda scope, payload: health.append((scope, payload)),
        sleep=lambda _seconds: None,
        max_cycles=2,
    )

    observations = [
        payload
        for scope, payload in health
        if scope == "component:email-training-observation"
    ]
    assert observations == [
        {
            "status": "failed",
            "error_code": "provider_runtime_error",
            "error_type": "ConnectionError",
        },
        {"status": "ready", "failures": 0},
    ]


def test_email_agent_consumer_requeues_stale_claims_before_claiming():
    module = _module()
    stale_email = SimpleNamespace(id=7, channel="email", execution_generation="gen-7")
    stale_dingtalk = SimpleNamespace(id=8, channel="dingtalk", execution_generation="gen-8")
    calls = []

    class Store:
        def list_stale_processing_reply_tasks(self, max_age_seconds, *, max_processing_seconds):
            calls.append(("stale", max_age_seconds, max_processing_seconds))
            return [stale_email, stale_dingtalk]

        def requeue_reply_task(self, task_id, error, *, expected_execution_generation):
            calls.append(("requeue", task_id, error, expected_execution_generation))

        def claim_reply_tasks(self, limit, *, channel):
            calls.append(("claim", limit, channel))
            return []

    module.run_email_agent_task_loop(
        Store(),
        SimpleNamespace(process=lambda *_args, **_kwargs: pytest.fail("no task")),
        load_task_context=lambda _task: pytest.fail("no task"),
        finalize_task=lambda *_args: pytest.fail("no task"),
        sleep=lambda _seconds: None,
        max_cycles=1,
    )

    assert calls == [
        ("stale", module.STALE_EMAIL_TASK_SECONDS, module.MAX_EMAIL_TASK_PROCESSING_SECONDS),
        ("requeue", 7, "stale_email_task_recovery", "gen-7"),
        ("claim", module.EMAIL_TASK_CLAIM_BATCH, "email"),
    ]


def test_email_agent_consumer_defers_transient_provider_failures_with_backoff():
    module = _module()
    task = SimpleNamespace(
        id=51,
        attempts=1,
        execution_generation="generation-51",
        trigger_message_json=json.dumps(
            {"schema": "email_agent_action.v1", "action_type": "unsubscribe"}
        ),
    )
    calls = []

    class Store:
        def claim_reply_tasks(self, limit, *, channel):
            return [task]

        def defer_reply_task(self, task_id, error, *, expected_execution_generation, available_at):
            calls.append(("defer", task_id, error, expected_execution_generation, bool(available_at)))

        def fail_reply_task(self, *args, **kwargs):
            calls.append(("fail", args))

    def load_task_context(_task):
        raise TimeoutError("_ssl.c:993: The handshake operation timed out")

    module.run_email_agent_task_loop(
        Store(),
        SimpleNamespace(process=lambda *_a, **_k: pytest.fail("no context")),
        load_task_context=load_task_context,
        finalize_task=lambda *_a: pytest.fail("no result"),
        sleep=lambda _seconds: None,
        max_cycles=1,
    )

    assert calls == [("defer", 51, "email_provider_transient:TimeoutError", "generation-51", True)]


def test_email_agent_consumer_fails_transient_provider_error_after_retry_budget():
    module = _module()
    task = SimpleNamespace(
        id=52,
        attempts=module.EMAIL_TASK_TRANSIENT_RETRY_ATTEMPTS,
        execution_generation="generation-52",
        trigger_message_json=json.dumps(
            {"schema": "email_agent_action.v1", "action_type": "unsubscribe"}
        ),
    )
    calls = []

    class Store:
        def claim_reply_tasks(self, limit, *, channel):
            return [task]

        def defer_reply_task(self, *args, **kwargs):
            calls.append(("defer", args))

        def fail_reply_task(self, task_id, error, *, expected_execution_generation):
            calls.append(("fail", task_id, error, expected_execution_generation))

    module.run_email_agent_task_loop(
        Store(),
        SimpleNamespace(process=lambda *_a, **_k: pytest.fail("no context")),
        load_task_context=lambda _task: (_ for _ in ()).throw(ConnectionResetError("reset")),
        finalize_task=lambda *_a: pytest.fail("no result"),
        sleep=lambda _seconds: None,
        max_cycles=1,
    )

    assert calls == [("fail", 52, "email_consumer_runtime_error:ConnectionResetError", "generation-52")]


def test_email_agent_consumer_leaves_superseded_task_to_its_new_generation():
    module = _module()
    from app.store import AgentRunLeaseLostError

    task = SimpleNamespace(
        id=53,
        attempts=0,
        execution_generation="generation-53",
        trigger_message_json=json.dumps(
            {"schema": "email_agent_action.v1", "action_type": "unsubscribe"}
        ),
    )
    calls = []

    class Store:
        def claim_reply_tasks(self, limit, *, channel):
            return [task]

        def defer_reply_task(self, *args, **kwargs):
            calls.append("defer")

        def fail_reply_task(self, *args, **kwargs):
            calls.append("fail")

    module.run_email_agent_task_loop(
        Store(),
        SimpleNamespace(process=lambda *_a, **_k: pytest.fail("no context")),
        load_task_context=lambda _task: (_ for _ in ()).throw(AgentRunLeaseLostError("reply task superseded: 53")),
        finalize_task=lambda *_a: pytest.fail("no result"),
        sleep=lambda _seconds: None,
        max_cycles=1,
    )

    assert calls == []


def test_recovery_releases_a_claim_whose_audit_run_died():
    """Nothing released these, so the action was blocked for good.

    A browser claim is taken by one Audit run and released by that same run's
    owner fence. When the run dies first, a later run fails the fence and every
    attempt on that action conflicts. The store has had the recovery method for
    exactly this and nothing called it: 18 claims sat stuck on 2026-09-11, each
    owned by an Audit run that had already failed.
    """
    module = _module()
    calls = []

    class EmailStore:
        def list_orphaned_email_unsubscribe_claim_owners(self):
            return [
                {
                    "action_identity": "email-action:abc",
                    "effect_digest": "d" * 64,
                    "owner": {
                        "owner_id": "email-unsubscribe-audit:18736",
                        "generation": 1,
                        "lease_token": "unsubscribe-audit-lease:t",
                    },
                }
            ]

        def release_orphaned_email_unsubscribe_claim(self, action_identity):
            # Durable browser state exists for this one, so it is not released.
            return False

        def recover_terminated_email_unsubscribe_claims(
            self, *, owner, termination_verifier, recovered_at
        ):
            calls.append((owner, termination_verifier(owner), recovered_at))
            return 1

    recovered = module._recover_orphaned_unsubscribe_claims(EmailStore(), object())

    assert recovered == 1
    assert len(calls) == 1
    owner, terminated, recovered_at = calls[0]
    assert owner["owner_id"] == "email-unsubscribe-audit:18736"
    # The run that owns the claim is already terminal; that is the termination
    # this fence needs proven, and it is read from the run, not a lease clock.
    assert terminated is True
    # The store requires a timezone-aware ISO-8601 stamp and rejects the naive
    # "%Y-%m-%d %H:%M:%S" the reply tables use. Getting that wrong made every
    # recovery raise, which the sweep then logged and swallowed, so the claims
    # stayed stuck and the queue looked unchanged.
    from datetime import datetime as _datetime

    parsed = _datetime.fromisoformat(recovered_at)
    assert parsed.tzinfo is not None and parsed.utcoffset() is not None


def test_claim_recovery_never_takes_the_consumer_loop_down():
    """Recovery runs beside the queue; a store that errors must not stop it."""
    module = _module()

    class Broken:
        def list_orphaned_email_unsubscribe_claim_owners(self):
            raise RuntimeError("store unavailable")

    assert module._recover_orphaned_unsubscribe_claims(Broken(), object()) == 0


def test_a_claim_that_wrote_nothing_is_released_outright():
    """`uncertain` has exactly one consumer and it cannot accept these.

    Moving an orphan to `uncertain` only looks like progress: the reviewed
    retry requires phase effect_uncertain and a specific error code, so 19
    claims recovered that way were still unreachable. When the browser
    persisted no step, receipt or continuation there is no completed action to
    protect, and the claim can simply go -- which is what the normal path does
    with the owner fence it can no longer present.
    """
    module = _module()
    released, recovered_calls = [], []

    class EmailStore:
        def list_orphaned_email_unsubscribe_claim_owners(self):
            return [
                {
                    "action_identity": "email-action:clean",
                    "effect_digest": "d" * 64,
                    "owner": {
                        "owner_id": "email-unsubscribe-audit:1",
                        "generation": 1,
                        "lease_token": "unsubscribe-audit-lease:t",
                    },
                }
            ]

        def release_orphaned_email_unsubscribe_claim(self, action_identity):
            released.append(action_identity)
            return True

        def recover_terminated_email_unsubscribe_claims(self, **kwargs):
            recovered_calls.append(kwargs)
            return 1

    recovered = module._recover_orphaned_unsubscribe_claims(EmailStore(), object())

    assert recovered == 1
    assert released == ["email-action:clean"]
    assert recovered_calls == [], "a released claim must not also be marked uncertain"
