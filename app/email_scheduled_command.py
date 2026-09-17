"""Single-pass Email discovery command owned by Agent Cron."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from app.email_classifier_scan import AgentScanContext, scan_agent_classification_batch
from app.email_provider_folders import FolderRole
from app.email_store import EmailStore
from app.email_task_producer import EmailClassificationTaskProducer
from app.email_worker import (
    _build_email_source_factory,
    _close_email_source,
    _email_worker_health_recorder,
    run_email_discovery_once,
)
from app.store import AutoReplyStore


@dataclass(frozen=True)
class EmailDiscoveryBootstrap:
    """Only the resources needed to discover and enqueue new Email work."""

    email_store: EmailStore
    load_enabled_accounts: Callable[[], Sequence[Mapping[str, object]]]
    scan_account: Callable[[Mapping[str, object]], object]
    record_health: Callable[[str, Mapping[str, object]], object]


def build_email_discovery_dependencies(
    settings: object,
    *,
    source_factory: Callable[[Mapping[str, object]], object] | None = None,
) -> EmailDiscoveryBootstrap:
    """Build discovery without Agent runtimes, consumers, delivery, or training."""

    email_store = EmailStore(Path(settings.db_path))
    task_store = AutoReplyStore(Path(settings.db_path))
    classification_task_producer = EmailClassificationTaskProducer(email_store)
    if source_factory is None:
        source_factory = _build_email_source_factory(settings)
    record_health = _email_worker_health_recorder(task_store)

    def load_enabled_accounts() -> tuple[Mapping[str, object], ...]:
        return tuple(
            account for account in email_store.list_accounts() if account["enabled"]
        )

    def scan_account(account: Mapping[str, object]) -> object:
        configs = tuple(
            item for item in email_store.list_category_configs() if item["enabled"]
        )
        bindings = tuple(
            item
            for item in email_store.list_account_folder_bindings()
            if item["account_id"] == str(account["account_id"])
            and item["binding_status"] == "active"
        )
        context = AgentScanContext(
            allowed_category_keys=tuple(item["category_key"] for item in configs),
            category_descriptions={
                item["category_key"]: {
                    "core": item["core_description"],
                    "include": item["include"],
                    "exclude": item["exclude"],
                }
                for item in configs
            },
            folder_targets={
                item["category_key"]: item["provider_folder_name"]
                for item in bindings
                if item["category_key"] != "junk"
            },
            config_version="|".join(
                sorted({str(item["config_version"]) for item in configs})
            ),
        )
        source = source_factory(account)
        active_error: BaseException | None = None
        try:
            inventory = tuple(source.list_folders())
            results = []
            for folder_name in account["scan_folders"]:
                matches = tuple(
                    folder for folder in inventory if folder.display_name == folder_name
                )
                if len(matches) != 1:
                    continue
                folder = matches[0]
                is_bound = any(
                    binding["provider_folder_id"] == folder.provider_folder_id
                    and binding["category_key"] != "junk"
                    for binding in bindings
                )
                role = FolderRole.CATEGORY if is_bound else folder.role
                results.append(
                    scan_agent_classification_batch(
                        source,
                        email_store,
                        classification_task_producer,
                        context,
                        mailbox=folder_name,
                        folder_role=role,
                        configured_unclassified_source=(
                            role is FolderRole.UNBOUND and not is_bound
                        ),
                        lookback_days=int(account.get("scan_lookback_days") or 30),
                        include_read=account.get("scan_read_state") == "all",
                        # Cron discovery always queues the exact scheduled
                        # classifier task. Promoted-model acceptance remains
                        # available to explicit non-Cron scan callers.
                        online_runtime=None,
                        accept_model=None,
                    )
                )
            return tuple(results)
        except BaseException as exc:
            active_error = exc
            raise
        finally:
            try:
                _close_email_source(source)
            except BaseException:
                if active_error is None:
                    raise

    return EmailDiscoveryBootstrap(
        email_store=email_store,
        load_enabled_accounts=load_enabled_accounts,
        scan_account=scan_account,
        record_health=record_health,
    )


@dataclass(frozen=True)
class EmailMessageCheckOnceCommand:
    settings: object
    dependency_builder: Callable[
        [object], EmailDiscoveryBootstrap
    ] = build_email_discovery_dependencies

    def __call__(self) -> str:
        """Read each enabled account once and leave all consumers asynchronous."""

        bootstrap = self.dependency_builder(self.settings)
        accounts = tuple(bootstrap.load_enabled_accounts())
        if not accounts:
            return "email-message-check-once accounts=0 discovered=0 failures=0"
        metrics = run_email_discovery_once(
            accounts,
            None,
            scan_account=lambda account, _unused_model: bootstrap.scan_account(account),
            record_health=bootstrap.record_health,
        )
        if metrics["failures"]:
            raise RuntimeError(
                "email discovery failed for "
                f"{metrics['failures']} account operation(s)"
            )
        return (
            "email-message-check-once "
            f"accounts={metrics['accounts']} "
            f"discovered={metrics['discovered']} "
            f"failures={metrics['failures']}"
        )
