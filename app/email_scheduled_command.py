"""Single-pass Email discovery command owned by Agent Cron."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from app.email_classifier_scan import (
    AgentScanContext,
    scan_agent_classification_batch,
    scan_model_classification_batch,
)
from app.email_classifier_runtime import (
    EmailClassifierRuntimeMode,
    PromotedEmailClassifierRuntime,
)
from app.email_model_registry import EmailModelRegistry
from app.email_provider_folders import FolderRole
from app.email_store import EmailStore
from app.email_task_producer import (
    EmailActionTaskProducer,
    EmailClassificationTaskProducer,
)
from app.email_worker import (
    _build_email_source_factory,
    _close_email_source,
    _email_worker_health_recorder,
    persist_model_primary_classification,
    run_email_discovery_once,
)
from app.store import AutoReplyStore


HISTORY_EMBEDDING_TIMEOUT_SECONDS = 120.0


@dataclass(frozen=True)
class EmailDiscoveryBootstrap:
    """Only the resources needed to discover and enqueue new Email work."""

    email_store: EmailStore
    load_enabled_accounts: Callable[[], Sequence[Mapping[str, object]]]
    load_active_model: Callable[[], object]
    scan_account: Callable[[Mapping[str, object], object], object]
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
    action_task_producer = EmailActionTaskProducer(task_store, email_store)
    model_registry = EmailModelRegistry(
        Path(settings.db_path).parent / "email-models"
    )
    if source_factory is None:
        source_factory = _build_email_source_factory(settings)
    record_health = _email_worker_health_recorder(task_store)

    def load_enabled_accounts() -> tuple[Mapping[str, object], ...]:
        return tuple(
            account for account in email_store.list_accounts() if account["enabled"]
        )

    def load_active_model() -> PromotedEmailClassifierRuntime:
        return PromotedEmailClassifierRuntime(
            model_registry,
            observability_store=email_store,
            embedding_remote_timeout_seconds=HISTORY_EMBEDDING_TIMEOUT_SECONDS,
        )

    def scan_account(
        account: Mapping[str, object], current_model: object
    ) -> object:
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
            snapshot_reader = getattr(current_model, "snapshot", None)
            runtime_snapshot = (
                snapshot_reader() if callable(snapshot_reader) else current_model
            )
            model_is_primary = EmailClassifierRuntimeMode(runtime_snapshot.mode) is (
                EmailClassifierRuntimeMode.MODEL_PRIMARY
            )
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
                if not model_is_primary:
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
                            lookback_days=int(
                                account.get("agent_lookback_days") or 30
                            ),
                            include_read=account.get("scan_read_state") == "all",
                            online_runtime=None,
                            accept_model=None,
                        )
                    )
                    continue
                results.append(
                    scan_model_classification_batch(
                        source,
                        email_store,
                        context,
                        mailbox=folder_name,
                        folder_role=role,
                        configured_unclassified_source=(
                            role is FolderRole.UNBOUND and not is_bound
                        ),
                        lookback_days=int(account.get("model_lookback_days") or 365),
                        online_runtime=runtime_snapshot,
                        agent_task_adapter=classification_task_producer.adapter,
                        accept_model=(
                            lambda message, prediction, entries, model_text, model_id: (
                                persist_model_primary_classification(
                                    email_store,
                                    action_task_producer,
                                    message=message,
                                    prediction=prediction,
                                    context=context,
                                    model_id=model_id,
                                    model_text=model_text,
                                    unsubscribe_entries=entries,
                                    preserve_read=(
                                        message.get("providerUnread") is False
                                    ),
                                )
                            )
                        ),
                        enqueue_agent=lambda message, entries: (
                            classification_task_producer.produce(
                                message,
                                allowed_category_keys=context.allowed_category_keys,
                                category_descriptions=context.category_descriptions,
                                folder_targets=context.folder_targets,
                                config_version=context.config_version,
                                unsubscribe_candidates=entries,
                            )
                        ),
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
        load_active_model=load_active_model,
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
        active_model = bootstrap.load_active_model()
        try:
            metrics = run_email_discovery_once(
                accounts,
                active_model,
                scan_account=bootstrap.scan_account,
                record_health=bootstrap.record_health,
            )
        finally:
            close_model = getattr(active_model, "close", None)
            if callable(close_model):
                close_model()
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
