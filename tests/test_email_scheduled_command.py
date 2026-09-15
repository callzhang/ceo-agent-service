import json
from hashlib import sha256
from types import SimpleNamespace

import pytest

from app.agent_cron.commands import (
    SERVICE_COMMAND_OPTIONS,
    ServiceCommandConsumerContext,
    ServiceCommandRegistry,
)
from app.email_classifier_agent import EmailClassifierAgent
from app.email_classifier_scan import EmailScanResult
from app.email_imap_readonly import ImapUidBatch
from app.email_provider_folders import FolderRole, ProviderFolder
from app.email_scheduled_command import (
    EmailMessageCheckOnceCommand,
    build_email_discovery_dependencies,
)
from app.email_task_adapter import EmailClassificationTaskAdapter
from app.managed_skills import (
    REPOSITORY_IMPORT_SOURCE,
    ManagedSkillRevision,
    RuntimeSkillSnapshot,
)


def test_email_scheduled_command_runs_one_discovery_pass_without_loading_consumers_or_model() -> None:
    events: list[object] = []
    bootstrap = SimpleNamespace(
        load_enabled_accounts=lambda: ({"account_id": "account-1"},),
        scan_account=lambda account: (
            events.append(("scan", account["account_id"]))
            or EmailScanResult(1, 1, 0, 0)
        ),
        record_health=lambda scope, payload: events.append((scope, payload)),
        load_active_model=lambda: (_ for _ in ()).throw(
            AssertionError("Cron discovery must not load the promoted model")
        ),
        build_dependencies=lambda *_args: (_ for _ in ()).throw(
            AssertionError("Cron discovery must not build worker dependencies")
        ),
    )

    summary = EmailMessageCheckOnceCommand(
        SimpleNamespace(), dependency_builder=lambda _settings: bootstrap
    )()

    assert summary == "email-message-check-once accounts=1 discovered=1 failures=0"
    assert ("scan", "account-1") in events


def test_email_scheduled_command_without_accounts_is_an_idempotent_noop() -> None:
    bootstrap = SimpleNamespace(
        load_enabled_accounts=lambda: (),
        load_active_model=lambda: (_ for _ in ()).throw(
            AssertionError("an empty account set must not load a model")
        ),
    )

    summary = EmailMessageCheckOnceCommand(
        SimpleNamespace(), dependency_builder=lambda _settings: bootstrap
    )()

    assert summary == "email-message-check-once accounts=0 discovered=0 failures=0"


def test_email_scheduled_command_reports_dependency_construction_failure() -> None:
    def fail(_settings):
        raise RuntimeError("discovery bootstrap unavailable")

    with pytest.raises(RuntimeError, match="discovery bootstrap unavailable"):
        EmailMessageCheckOnceCommand(
            SimpleNamespace(), dependency_builder=fail
        )()


def test_email_cron_registry_chain_enqueues_then_uses_exact_scheduled_prompt_and_skill(
    tmp_path, monkeypatch
) -> None:
    message = {
        "messageId": "<cron-email@example.com>",
        "accountId": "account-1",
        "folder": "INBOX",
        "uidValidity": 42,
        "uid": 7,
        "providerUnread": True,
        "from": {"email": "sender@example.com"},
        "subject": "Cron classifier queue",
        "textBody": "Please classify this new work email.",
        "date": "2026-09-15T12:00:00+00:00",
    }

    class Source:
        account_id = "account-1"

        def list_folders(self):
            return (ProviderFolder("inbox-id", "INBOX", FolderRole.INBOX),)

        def fetch_uid_batch(self, mailbox, **kwargs):
            return ImapUidBatch(
                account_id=self.account_id,
                folder=mailbox,
                uidvalidity=42,
                previous_uidvalidity=kwargs["cursor_uidvalidity"],
                messages=(message,),
            )

        def logout(self):
            return None

    settings = SimpleNamespace(db_path=tmp_path / "email-cron.sqlite3")
    bootstrap = build_email_discovery_dependencies(
        settings, source_factory=lambda _account: Source()
    )
    bootstrap.email_store.create_account(
        {
            "account_id": "account-1",
            "display_name": "Work",
            "email_address": "derek@example.com",
            "imap_host": "imap.example.com",
            "imap_port": 993,
            "imap_tls": True,
            "imap_username": "derek@example.com",
            "imap_secret_reference": "keychain://cron-imap",
            "smtp_host": "smtp.example.com",
            "smtp_port": 465,
            "smtp_tls": True,
            "smtp_username": "derek@example.com",
            "smtp_secret_reference": "keychain://cron-smtp",
            "enabled": True,
            "scan_folders": ["INBOX"],
            "scan_interval_seconds": 86400,
        }
    )
    monkeypatch.setattr(
        bootstrap.email_store,
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
        bootstrap.email_store, "list_account_folder_bindings", lambda: []
    )
    context = ServiceCommandConsumerContext(
        scheduled_task_id=10,
        scheduled_task_run_id=101,
        prompt="自定义分类要求：使用 $ceo-email-classifier 判断真实新邮件。",
        skill_names=("ceo-email-classifier",),
        skill_protocol=(
            "## Managed Skill: ceo-email-classifier\n"
            "revision_id: 39\nsha256: selected\n\n"
            "# EXACT CRON EMAIL SKILL\nClassify only; never act."
        ),
    )
    implementations = {
        option.name: (lambda: "unused") for option in SERVICE_COMMAND_OPTIONS
    }
    implementations["email-message-check-once"] = EmailMessageCheckOnceCommand(
        settings, dependency_builder=lambda _settings: bootstrap
    )

    summary = ServiceCommandRegistry(implementations).run(
        "email-message-check-once", consumer_context=context
    )

    assert summary == "email-message-check-once accounts=1 discovered=1 failures=0"
    adapter = EmailClassificationTaskAdapter(bootstrap.email_store)
    task = adapter.claim_next(owner="test-classifier")
    assert task is not None
    assert json.loads(task.input_json)["scheduled_consumer"][
        "scheduled_task_run_id"
    ] == 101

    process_skill = (
        "---\nname: ceo-email-classifier\n"
        "description: Process fallback\n"
        "metadata:\n  managed_by: ceo-agent-service\n---\n\n"
        "# UNRELATED PROCESS SKILL MUST NOT LOAD\n"
    )
    revision = ManagedSkillRevision(
        id=41,
        skill_id=17,
        revision_number=5,
        content=process_skill,
        sha256=sha256(process_skill.encode("utf-8")).hexdigest(),
        parent_revision_id=40,
        source=REPOSITORY_IMPORT_SOURCE,
        created_at="2026-09-15T00:00:00+00:00",
    )
    prompts: list[str] = []
    backend = SimpleNamespace(
        classify=lambda **kwargs: (
            prompts.append(kwargs["prompt"])
            or json.dumps(
                {
                    "category": "work",
                    "important": False,
                    "certainty": "certain",
                    "confidence": 0.9,
                    "reason": "Work request.",
                    "unsubscribe_candidate_index": None,
                    "unsubscribe_url": None,
                }
            )
        )
    )
    EmailClassifierAgent(
        backend,
        runtime_skill_snapshot=RuntimeSkillSnapshot(
            config_id=9, revisions=(revision,)
        ),
    ).classify(task, current_message=message, unsubscribe_candidates=())

    [prompt] = prompts
    assert "自定义分类要求" in prompt
    assert "EXACT CRON EMAIL SKILL" in prompt
    assert "UNRELATED PROCESS SKILL MUST NOT LOAD" not in prompt


def test_discovery_source_close_failure_does_not_replace_scan_failure(
    tmp_path, monkeypatch
) -> None:
    class Source:
        def list_folders(self):
            raise RuntimeError("primary scan failure")

        def logout(self):
            raise RuntimeError("secondary close failure")

    bootstrap = build_email_discovery_dependencies(
        SimpleNamespace(db_path=tmp_path / "close.sqlite3"),
        source_factory=lambda _account: Source(),
    )
    monkeypatch.setattr(
        bootstrap.email_store,
        "list_category_configs",
        lambda: [
            {
                "category_key": "work",
                "enabled": True,
                "core_description": "Work.",
                "include": [],
                "exclude": [],
                "config_version": "config-v1",
            }
        ],
    )
    monkeypatch.setattr(
        bootstrap.email_store, "list_account_folder_bindings", lambda: []
    )

    with pytest.raises(RuntimeError, match="primary scan failure"):
        bootstrap.scan_account(
            {"account_id": "account-1", "scan_folders": ["INBOX"]}
        )
