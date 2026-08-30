"""Independent email scan, Agent-task, and training worker."""

from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from threading import Event, Thread
from typing import Any, TextIO


SCAN_INTERVAL_SECONDS = 60
CONSUMER_POLL_INTERVAL_SECONDS = 10
TRAINING_INTERVAL_SECONDS = 60
MAX_HEALTH_TEXT_LENGTH = 160


class EmailWorkerStartupError(RuntimeError):
    """The email worker could not construct its required runtime."""


@dataclass(frozen=True)
class EmailWorkerDependencies:
    load_enabled_accounts: Callable[[], Sequence[Mapping[str, object]]]
    load_active_model: Callable[[], object]
    scan_account: Callable[[Mapping[str, object], object], object]
    run_direct_actions_once: Callable[[], object]
    task_store: object
    orchestrator: object
    load_task_context: Callable[[object], object]
    finalize_task: Callable[[object, object], object]
    training_tick: Callable[[], object]
    record_health: Callable[[str, Mapping[str, object]], object]


@dataclass(frozen=True)
class EmailWorkerBootstrap:
    load_enabled_accounts: Callable[[], Sequence[Mapping[str, object]]]
    load_active_model: Callable[[], object]
    build_dependencies: Callable[
        [Sequence[Mapping[str, object]], object], EmailWorkerDependencies
    ]


def _account_id(account: Mapping[str, object]) -> str:
    value = str(account.get("account_id") or "").strip()
    return value if value else "unknown"


def _safe_health_error(exc: Exception) -> dict[str, object]:
    return {
        "status": "failed",
        "error_code": "provider_runtime_error",
        "error_type": type(exc).__name__[:MAX_HEALTH_TEXT_LENGTH],
    }


def _health_error_code(value: object, *, fallback: str) -> str:
    candidate = str(value or "").strip().casefold()
    if not candidate or not candidate.replace("_", "").isalnum():
        return fallback
    return candidate[:MAX_HEALTH_TEXT_LENGTH]


def _scan_result_error_code(result: object) -> str:
    outcomes = getattr(result, "accounts", ())
    for outcome in outcomes:
        account_error = getattr(outcome, "error_code", "")
        if account_error:
            return _health_error_code(account_error, fallback="scan_failed")
        for folder in getattr(outcome, "folders", ()):
            folder_error = getattr(folder, "error_code", "")
            if folder_error:
                return _health_error_code(folder_error, fallback="scan_failed")
    return ""


def _ignore_health(_scope: str, _payload: Mapping[str, object]) -> None:
    return None


def run_scan_and_direct_actions_loop(
    accounts: Sequence[Mapping[str, object]],
    active_model: object,
    *,
    scan_account: Callable[[Mapping[str, object], object], object],
    run_direct_actions_once: Callable[[], object],
    record_health: Callable[[str, Mapping[str, object]], object] = _ignore_health,
    sleep: Callable[[float], None] = time.sleep,
    max_cycles: int | None = None,
) -> None:
    cycles = 0
    while max_cycles is None or cycles < max_cycles:
        failures = 0
        for account in accounts:
            account_id = _account_id(account)
            try:
                result = scan_account(account, active_model)
            except Exception as exc:  # noqa: BLE001 - isolate provider accounts
                failures += 1
                record_health(f"account:{account_id}", _safe_health_error(exc))
                continue
            error_code = _scan_result_error_code(result)
            if error_code:
                failures += 1
                record_health(
                    f"account:{account_id}",
                    {"status": "failed", "error_code": error_code},
                )
                continue
            record_health(
                f"account:{account_id}",
                {
                    "status": "ready",
                    "persisted_count": int(
                        getattr(result, "persisted_count", 0)
                        if not isinstance(result, Mapping)
                        else result.get("persisted_count", 0)
                    ),
                },
            )
        try:
            run_direct_actions_once()
        except Exception as exc:  # noqa: BLE001 - keep the scan cadence alive
            failures += 1
            record_health("component:email-direct-actions", _safe_health_error(exc))
        record_health(
            "component:email-scan-actions",
            {"status": "ready" if failures == 0 else "degraded", "failures": failures},
        )
        cycles += 1
        if max_cycles is None or cycles < max_cycles:
            interval = min(
                (
                    int(account.get("scan_interval_seconds") or SCAN_INTERVAL_SECONDS)
                    for account in accounts
                ),
                default=SCAN_INTERVAL_SECONDS,
            )
            sleep(max(interval, 1))


def run_email_agent_task_loop(
    task_store: object,
    orchestrator: object,
    *,
    load_task_context: Callable[[object], object],
    finalize_task: Callable[[object, object], object],
    record_health: Callable[[str, Mapping[str, object]], object] = _ignore_health,
    sleep: Callable[[float], None] = time.sleep,
    max_cycles: int | None = None,
) -> None:
    cycles = 0
    while max_cycles is None or cycles < max_cycles:
        failures = 0
        last_error_type = ""
        try:
            tasks = task_store.claim_reply_tasks(50, channel="email")
        except Exception as exc:  # noqa: BLE001 - keep the component alive
            tasks = ()
            failures += 1
            last_error_type = type(exc).__name__[:MAX_HEALTH_TEXT_LENGTH]
        for task in tasks:
            try:
                context = load_task_context(task)
                result = orchestrator.process(
                    task,
                    context,
                    refresh_context=lambda task=task: load_task_context(task),
                )
                finalize_task(task, result)
            except Exception as exc:  # noqa: BLE001 - isolate one Email task
                failures += 1
                last_error_type = type(exc).__name__[:MAX_HEALTH_TEXT_LENGTH]
                try:
                    task_store.fail_reply_task(
                        task.id,
                        f"email_consumer_runtime_error:{last_error_type}",
                        expected_execution_generation=task.execution_generation,
                    )
                except Exception:  # noqa: BLE001 - heartbeat still reports failure
                    pass
        health: dict[str, object] = {
            "status": "ready" if failures == 0 else "degraded",
            "failures": failures,
        }
        if failures:
            health.update(
                {
                    "error_code": "consumer_runtime_error",
                    "error_type": last_error_type,
                }
            )
        record_health("component:email-agent-consumer", health)
        cycles += 1
        if max_cycles is None or cycles < max_cycles:
            sleep(CONSUMER_POLL_INTERVAL_SECONDS)


def run_training_scheduler_loop(
    training_tick: Callable[[], object],
    *,
    record_health: Callable[[str, Mapping[str, object]], object],
    sleep: Callable[[float], None] = time.sleep,
    max_cycles: int | None = None,
) -> None:
    cycles = 0
    while max_cycles is None or cycles < max_cycles:
        try:
            training_tick()
        except Exception as exc:  # noqa: BLE001 - keep the component alive
            record_health(
                "component:email-training",
                {
                    "status": "degraded",
                    "failures": 1,
                    "error_code": "training_runtime_error",
                    "error_type": type(exc).__name__[:MAX_HEALTH_TEXT_LENGTH],
                },
            )
        else:
            record_health(
                "component:email-training",
                {"status": "ready", "failures": 0},
            )
        cycles += 1
        if max_cycles is None or cycles < max_cycles:
            sleep(TRAINING_INTERVAL_SECONDS)


def email_worker_components(
    dependencies: EmailWorkerDependencies | Any,
    *,
    accounts: Sequence[Mapping[str, object]],
    active_model: object,
) -> tuple[tuple[str, partial], ...]:
    return (
        (
            "email-scan-actions",
            partial(
                run_scan_and_direct_actions_loop,
                accounts,
                active_model,
                scan_account=dependencies.scan_account,
                run_direct_actions_once=dependencies.run_direct_actions_once,
                record_health=dependencies.record_health,
            ),
        ),
        (
            "email-agent-consumer",
            partial(
                run_email_agent_task_loop,
                dependencies.task_store,
                dependencies.orchestrator,
                load_task_context=dependencies.load_task_context,
                finalize_task=dependencies.finalize_task,
                record_health=dependencies.record_health,
            ),
        ),
        (
            "email-training",
            partial(
                run_training_scheduler_loop,
                dependencies.training_tick,
                record_health=dependencies.record_health,
            ),
        ),
    )


def _scan_config(email_store: object, model_metadata: object | None):
    from app.email_classifier_contracts import EmailAction, EmailCategory
    from app.email_classifier_scan import EmailScanConfig
    from app.email_classifier_training import CategoryEligibility

    rows = email_store.list_configs()
    if len(rows) != len(EmailCategory):
        return EmailScanConfig.cold_start(config_version="email-config-missing-v1")
    versions = {str(row["config_version"]) for row in rows}
    if len(versions) != 1:
        raise EmailWorkerStartupError("email category config versions are inconsistent")
    by_category = {EmailCategory(str(row["category"])): row for row in rows}
    thresholds = {
        category: float(by_category[category]["threshold"])
        for category in EmailCategory
    }
    metrics_value = getattr(model_metadata, "per_category_metrics", {})
    metrics = metrics_value if isinstance(metrics_value, Mapping) else {}
    eligibility: dict[EmailCategory, CategoryEligibility] = {}
    for category in EmailCategory:
        metric_value = metrics.get(category.value)
        metric = metric_value if isinstance(metric_value, Mapping) else None
        if metric is None:
            eligibility[category] = CategoryEligibility(
                category=category,
                configured_threshold=thresholds[category],
                validated_precision=None,
                validation_sample_count=0,
                auto_action_eligible=False,
                reason="model_eligibility_missing",
            )
            continue
        trained_threshold = float(metric["configured_threshold"])
        threshold_matches = trained_threshold == thresholds[category]
        eligibility[category] = CategoryEligibility(
            category=category,
            configured_threshold=thresholds[category],
            validated_precision=float(metric["precision"]),
            validation_sample_count=int(metric["validation_sample_count"]),
            auto_action_eligible=(
                threshold_matches and bool(metric["auto_action_eligible"])
            ),
            reason=(
                str(metric["eligibility_reason"])
                if threshold_matches
                else "threshold_changed_since_training"
            ),
        )
    return EmailScanConfig(
        config_version=versions.pop(),
        thresholds=thresholds,
        actions={
            category: tuple(
                EmailAction(value) for value in by_category[category]["actions"]
            )
            for category in EmailCategory
        },
        category_eligibility=eligibility,
        action_parameters={
            category: {
                EmailAction(action): parameters
                for action, parameters in by_category[category][
                    "action_parameters"
                ].items()
            }
            for category in EmailCategory
        },
        category_enabled={
            category: bool(by_category[category]["enabled"])
            for category in EmailCategory
        },
    )


def _run_next_direct_action(
    email_store: object,
    executor_factory: Callable[[str], object] | None,
) -> object | None:
    """Run one claimed action, or leave it pending when no provider exists."""

    if executor_factory is None:
        return None
    claimed_at = datetime.now(timezone.utc).isoformat()
    action = email_store.claim_next_direct_action(claimed_at=claimed_at)
    if action is None:
        return None

    from app.email_provider_actions import ProviderActionResult

    try:
        executor = executor_factory(action.account_id)
        if executor is None:
            raise LookupError("provider executor unavailable")
        result = executor.execute(action)
    except Exception as exc:  # noqa: BLE001 - every durable claim is terminalized
        result = ProviderActionResult(
            status="failed",
            provider_operation="provider_factory",
            provider_target=action.locator.stable_message_identity,
            provider_result_id="",
            error=f"provider_factory_failed:{type(exc).__name__}",
        )
    email_store.complete_direct_action_attempt(
        action,
        status=result.status,
        provider_operation=result.provider_operation,
        provider_target=result.provider_target,
        provider_result_id=result.provider_result_id,
        error=result.error,
        finished_at=datetime.now(timezone.utc).isoformat(),
    )
    return result


def _build_agent_orchestrator(settings: object, store: object):
    from app.agent_orchestrator import AgentOrchestrator
    from app.agent_runtime_production import build_production_agent_runtime
    from app.audit_agent import AuditAgentRunner
    from app.consumer_agent import ConsumerAgentRunner

    workspace = Path(settings.workspace)
    runtime = build_production_agent_runtime(store=store, workspace=workspace)
    shared = {
        "store": store,
        "workspace": workspace,
        "runtime_config": runtime.config,
        "runtime_router": runtime.router,
        "codex_adapter": runtime.codex_adapter,
        "claude_adapter": runtime.claude_adapter,
        "friday_adapter": runtime.friday_adapter,
        "refresh_runtime_capabilities": runtime.refresh_runtime_capabilities,
    }
    return AgentOrchestrator(
        store=store,
        consumer=ConsumerAgentRunner(**shared),
        audit=AuditAgentRunner(**shared, dry_run=bool(settings.dry_run)),
    )


def _message_identity(message: Mapping[str, object]) -> str:
    return str(
        message.get("stableMessageIdentity") or message.get("id") or ""
    ).strip()


def _message_sender(message: Mapping[str, object]) -> str:
    sender = message.get("from")
    if isinstance(sender, Mapping):
        value = sender.get("email") or sender.get("name")
    else:
        value = sender
    return str(value or "unknown").strip() or "unknown"


def _message_text(message: Mapping[str, object]) -> str:
    return str(message.get("markdownBody") or message.get("textBody") or "")


def _close_email_source(source: object) -> None:
    close = getattr(source, "logout", None)
    if callable(close):
        close()


def _load_email_task_input(
    email_store: object,
    source_factory: Callable[[Mapping[str, object]], object],
    task: object,
):
    from app.agent_context import PriorReceipt
    from app.email_classifier_contracts import EmailAttachmentMetadata
    from app.email_task_adapter import EmailAgentTaskInput, EmailThreadMessage

    payload = json.loads(task.trigger_message_json)
    classification_id = int(payload["classification_id"])
    classification = email_store.get_classification(classification_id)
    if classification is None:
        raise EmailWorkerStartupError("email task classification is unavailable")
    account_id = str(payload["account_id"])
    stable_identity = str(payload["stable_message_identity"])
    thread_identity = str(payload["thread_identity"])
    if (
        classification["account_id"] != account_id
        or classification["stable_message_identity"] != stable_identity
        or str(classification["thread_id"]) != thread_identity
    ):
        raise EmailWorkerStartupError("email task identity does not match its source")
    account = email_store.get_account(account_id)
    if account is None:
        raise EmailWorkerStartupError("email task account is unavailable")

    source = source_factory(account)
    try:
        trigger_uid = int(classification["uid"])
        batch = source.fetch_uid_batch(
            str(classification["folder"]),
            cursor_uidvalidity=int(classification["uidvalidity"]),
            last_seen_uid=max(0, trigger_uid - 50),
            limit=50,
        )
        if int(batch.uidvalidity) != int(classification["uidvalidity"]):
            raise EmailWorkerStartupError("email task provider generation changed")
        messages = tuple(batch.messages)
    finally:
        _close_email_source(source)

    trigger_message = next(
        (message for message in messages if _message_identity(message) == stable_identity),
        None,
    )
    if trigger_message is None:
        raise EmailWorkerStartupError("email task source message is unavailable")
    trigger_time = str(
        trigger_message.get("date")
        or classification["received_at"]
        or getattr(task, "trigger_create_time", "")
    )
    trigger = EmailThreadMessage(
        message_id=stable_identity,
        sender=_message_sender(trigger_message),
        text=_message_text(trigger_message),
        create_time=trigger_time,
    )
    thread_messages = tuple(
        EmailThreadMessage(
            message_id=_message_identity(message),
            sender=_message_sender(message),
            text=_message_text(message),
            create_time=str(message.get("date") or trigger_time),
        )
        for message in messages
        if _message_identity(message)
        and _message_identity(message) != stable_identity
        and str(message.get("threadId") or "") == thread_identity
    )
    attachments_value = trigger_message.get("attachments") or ()
    if not isinstance(attachments_value, Sequence) or isinstance(
        attachments_value, str | bytes
    ):
        raise EmailWorkerStartupError("email attachment metadata is invalid")
    attachments = tuple(
        EmailAttachmentMetadata.model_validate(item) for item in attachments_value
    )

    action_identity = str(
        payload.get("action_identity") or getattr(task, "trigger_message_id", "")
    )
    prior_receipts: list[PriorReceipt] = []
    reply_receipt = email_store.get_email_reply_receipt(action_identity)
    if reply_receipt is not None:
        prior_receipts.append(
            PriorReceipt(
                receipt_id=str(reply_receipt["provider_result_id"]),
                operation=str(reply_receipt["provider_operation"]),
                summary=str(reply_receipt["display_excerpt"]),
                completed=True,
            )
        )
    unsubscribe_receipt = email_store.get_email_unsubscribe_receipt(action_identity)
    if unsubscribe_receipt is not None:
        prior_receipts.append(
            PriorReceipt(
                receipt_id=str(unsubscribe_receipt["receipt_id"]),
                operation="unsubscribe_readback",
                summary=(
                    "Automatic email unsubscribe completed with outcome "
                    f"{unsubscribe_receipt['outcome']}."
                ),
                completed=True,
            )
        )
    return EmailAgentTaskInput(
        stable_message_identity=stable_identity,
        thread_identity=thread_identity,
        subject=str(trigger_message.get("subject") or classification["subject"]),
        trigger=trigger,
        thread_messages=thread_messages,
        attachments=attachments,
        prior_receipts=tuple(prior_receipts),
        list_unsubscribe=str(trigger_message.get("listUnsubscribe") or ""),
        list_unsubscribe_post=str(trigger_message.get("listUnsubscribePost") or ""),
        body_text=_message_text(trigger_message),
        body_html="",
    )


def _load_email_task_context(
    email_store: object,
    task_store: object,
    source_factory: Callable[[Mapping[str, object]], object],
    task: object,
):
    from app.email_classifier_contracts import EmailActionPlan
    from app.email_task_adapter import EmailAgentTaskAdapter

    payload = json.loads(task.trigger_message_json)
    classification = email_store.get_classification(int(payload["classification_id"]))
    if classification is None or classification["action_plan"] is None:
        raise EmailWorkerStartupError("email task action plan is unavailable")
    task_input = _load_email_task_input(email_store, source_factory, task)
    routes = EmailAgentTaskAdapter(task_store, email_store).ensure_action_plan_tasks(
        EmailActionPlan.model_validate(classification["action_plan"]),
        task_input,
    )
    route = next((candidate for candidate in routes if candidate.task.id == task.id), None)
    if route is None:
        raise EmailWorkerStartupError("email task route is unavailable")
    return route.context


def _finalize_email_task(store: object, task: object, result: object) -> None:
    status_map = {
        "executed": ("done", "completed"),
        "no_action": ("done", "skipped"),
        "needs_human": ("done", "needs_human"),
        "dry_run": ("done", "dry_run"),
        "failed_retryable": ("pending", "failed"),
        "unknown": ("pending", "failed"),
        "failed_terminal": ("failed", "failed"),
    }
    try:
        task_status, send_status = status_map[result.status]
    except KeyError as exc:
        raise ValueError("invalid email orchestration status") from exc
    run = store.get_agent_run(result.final_run_id)
    if run is None:
        raise RuntimeError("email orchestration final run was not persisted")
    error = str(result.error.code or "")
    store.finalize_orchestrated_reply_task(
        task_id=task.id,
        expected_execution_generation=task.execution_generation,
        run_id=run.id,
        task_status=task_status,
        task_error=error,
        available_at="",
        conversation_id=task.conversation_id,
        conversation_title=task.conversation_title,
        trigger_message_id=task.trigger_message_id,
        trigger_sender=task.trigger_sender,
        trigger_text=task.trigger_text,
        codex_reason=result.summary,
        codex_session_id=run.codex_session_id,
        codex_transcript_start_line=run.transcript_start_line,
        codex_transcript_end_line=run.transcript_end_line,
        audit_tool_events_json=json.dumps(run.tool_events, ensure_ascii=False),
        audit_summary=result.summary,
        send_status=send_status,
        send_error=error,
        channel="email",
    )


def build_email_worker_dependencies(
    settings: object,
    *,
    direct_action_executor_factory: Callable[[str], object] | None = None,
) -> EmailWorkerBootstrap:
    from app.email_classifier_learning import EmailClassifierLearningService
    from app.email_classifier_runtime import EmailClassifierRuntime
    from app.email_classifier_scan import scan_imap_accounts
    from app.email_connector_config import resolve_secret
    from app.email_imap_readonly import ImapReadonlyAdapter
    from app.email_model_registry import EmailModelRegistry
    from app.email_store import EmailStore
    from app.store import AutoReplyStore

    email_store = EmailStore(Path(settings.db_path))
    task_store = AutoReplyStore(Path(settings.db_path))
    model_root = Path(settings.db_path).parent / "email-models"
    registry = EmailModelRegistry(model_root)
    learning = EmailClassifierLearningService(
        email_store,
        registry=registry,
        retrain_state_path=model_root / "retrain-state.json",
    )
    def load_enabled_accounts():
        return tuple(account for account in email_store.list_accounts() if account["enabled"])

    def load_active_model():
        return EmailClassifierRuntime(
            registry,
            learning_service=learning,
        )

    def source_factory(account: Mapping[str, object]):
        if not bool(account["imap_tls"]):
            raise ConnectionError("email IMAP TLS is required")
        secret = resolve_secret(str(account["imap_secret_reference"]), os.environ)
        if not secret:
            raise ConnectionError("email IMAP credential is unavailable")
        return ImapReadonlyAdapter.connect(
            str(account["imap_host"]),
            str(account["imap_username"]),
            secret,
            port=int(account["imap_port"]),
            account_id=str(account["account_id"]),
        )

    def record_health(scope: str, payload: Mapping[str, object]):
        task_store.set_service_state(
            f"email_worker_health:{scope}",
            json.dumps(dict(payload), sort_keys=True, separators=(",", ":")),
        )

    def load_scan_config(active_model: object):
        metadata = registry.get_model(active_model.loaded.model_id).metadata
        config = _scan_config(email_store, metadata)
        metrics = metadata.per_category_metrics
        missing_config = config.config_version == "email-config-missing-v1"
        stale_threshold = any(
            item.reason == "threshold_changed_since_training"
            for item in config.category_eligibility.values()
        )
        missing_eligibility = not bool(metrics)
        if missing_config:
            payload = {
                "status": "degraded",
                "error_code": "category_config_missing",
            }
        elif missing_eligibility:
            payload = {
                "status": "degraded",
                "error_code": "model_eligibility_missing",
            }
        elif stale_threshold:
            payload = {
                "status": "degraded",
                "error_code": "model_eligibility_stale",
            }
        else:
            payload = {"status": "ready"}
        record_health("component:email-scan-config", payload)
        return config

    def build_dependencies(
        _accounts: Sequence[Mapping[str, object]],
        active_model: object,
    ) -> EmailWorkerDependencies:
        load_scan_config(active_model)

        def scan_account(account: Mapping[str, object], current_model: object):
            return scan_imap_accounts(
                (account,),
                source_factory,
                current_model.loaded.classifier,
                email_store,
                load_scan_config(current_model),
            )

        return EmailWorkerDependencies(
            load_enabled_accounts=load_enabled_accounts,
            load_active_model=load_active_model,
            scan_account=scan_account,
            run_direct_actions_once=partial(
                _run_next_direct_action,
                email_store,
                direct_action_executor_factory,
            ),
            task_store=task_store,
            orchestrator=_build_agent_orchestrator(settings, task_store),
            load_task_context=partial(
                _load_email_task_context,
                email_store,
                task_store,
                source_factory,
            ),
            finalize_task=partial(_finalize_email_task, task_store),
            training_tick=active_model.tick,
            record_health=record_health,
        )

    return EmailWorkerBootstrap(
        load_enabled_accounts=load_enabled_accounts,
        load_active_model=load_active_model,
        build_dependencies=build_dependencies,
    )


def _validate_email_worker_dependencies(dependencies: object) -> None:
    callable_fields = (
        "scan_account",
        "run_direct_actions_once",
        "load_task_context",
        "finalize_task",
        "training_tick",
        "record_health",
    )
    if any(not callable(getattr(dependencies, name, None)) for name in callable_fields):
        raise EmailWorkerStartupError("email worker loop dependency is unavailable")
    if not callable(getattr(dependencies.task_store, "claim_reply_tasks", None)):
        raise EmailWorkerStartupError("email task store dependency is unavailable")
    if not callable(getattr(dependencies.orchestrator, "process", None)):
        raise EmailWorkerStartupError("email Agent orchestrator is unavailable")


def run_email_worker(
    settings: object,
    *,
    dependencies: EmailWorkerDependencies | Any | None = None,
    dependency_builder: Callable[[object], object] = (
        build_email_worker_dependencies
    ),
    thread_factory: Callable[..., Thread] = Thread,
    wait: Callable[[], object] | None = None,
    output: TextIO = sys.stdout,
) -> None:
    try:
        bootstrap = dependencies or dependency_builder(settings)
        accounts = tuple(bootstrap.load_enabled_accounts())
        if not accounts:
            print("email-worker idle accounts=0", file=output, flush=True)
            (wait or Event().wait)()
            return
        active_model = bootstrap.load_active_model()
        if active_model is None:
            raise EmailWorkerStartupError("no active email classifier")
        build_dependencies = getattr(bootstrap, "build_dependencies", None)
        dependencies = (
            build_dependencies(accounts, active_model)
            if callable(build_dependencies)
            else bootstrap
        )
        _validate_email_worker_dependencies(dependencies)
        components = email_worker_components(
            dependencies,
            accounts=accounts,
            active_model=active_model,
        )
    except EmailWorkerStartupError:
        raise
    except Exception as exc:
        raise EmailWorkerStartupError(
            f"email worker dependency construction failed: {type(exc).__name__}"
        ) from exc

    dependencies.record_health(
        "process:email-worker",
        {
            "status": "ready",
            "accounts": len(accounts),
            "components": len(components),
        },
    )
    print(
        f"email-worker ready accounts={len(accounts)} components={len(components)}",
        file=output,
        flush=True,
    )
    for name, target in components:
        thread_factory(
            target=target,
            name=f"ceo-agent-{name}",
            daemon=True,
        ).start()
    (wait or Event().wait)()
