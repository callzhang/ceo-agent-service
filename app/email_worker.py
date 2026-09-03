"""Independent email scan, Agent-task, and training worker."""

from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import partial
from pathlib import Path
from threading import Event, Lock, Thread
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
    email_store: object | None = None


@dataclass(frozen=True)
class EmailWorkerBootstrap:
    load_enabled_accounts: Callable[[], Sequence[Mapping[str, object]]]
    load_active_model: Callable[[], object]
    build_dependencies: Callable[
        [Sequence[Mapping[str, object]], object], EmailWorkerDependencies
    ]
    record_health: Callable[[str, Mapping[str, object]], object] | None = None
    task_store: object | None = None
    email_store: object | None = None


@dataclass(frozen=True)
class LegacyReconciliationResult:
    authoritative: bool
    unresolved_task_ids: tuple[int, ...] = ()
    task_count: int = 0


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


def _is_disabled_email_auto_reply_task(task: object) -> bool:
    """Recognize persisted reply tasks that the current Email policy forbids."""

    try:
        payload = json.loads(str(getattr(task, "trigger_message_json", "")))
    except (TypeError, json.JSONDecodeError):
        return False
    return isinstance(payload, dict) and payload.get("action_type") == "auto_reply"


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


class EmailWorkerReadiness:
    """Publish process readiness only after every component heartbeats once."""

    def __init__(
        self,
        component_names: Sequence[str],
        *,
        record_health: Callable[[str, Mapping[str, object]], object],
        accounts: int,
    ) -> None:
        self._component_names = frozenset(component_names)
        self._record_health = record_health
        self._accounts = accounts
        self._ready: set[str] = set()
        self._published = False
        self._lock = Lock()

    def mark_ready(self, component_name: str) -> None:
        if component_name not in self._component_names:
            raise ValueError("unknown email worker component")
        with self._lock:
            self._ready.add(component_name)
            if self._published or self._ready != self._component_names:
                return
            self._published = True
        self._record_health(
            "process:email-worker",
            {
                "status": "ready",
                "accounts": self._accounts,
                "components": len(self._component_names),
            },
        )


def run_scan_and_direct_actions_loop(
    accounts: Sequence[Mapping[str, object]],
    active_model: object,
    *,
    scan_account: Callable[[Mapping[str, object], object], object],
    run_direct_actions_once: Callable[[], object],
    record_health: Callable[[str, Mapping[str, object]], object] = _ignore_health,
    component_ready: Callable[[str], object] | None = None,
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
            direct_result = run_direct_actions_once()
            if getattr(direct_result, "status", "") == "failed":
                failures += 1
                record_health(
                    "component:email-provider-actions",
                    {"status": "degraded", "error_code": "provider_action_failed"},
                )
        except Exception as exc:  # noqa: BLE001 - keep the scan cadence alive
            failures += 1
            record_health("component:email-provider-actions", _safe_health_error(exc))
        record_health(
            "component:email-scan-actions",
            {"status": "ready" if failures == 0 else "degraded", "failures": failures},
        )
        if failures == 0 and component_ready is not None:
            component_ready("email-scan-actions")
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
    component_ready: Callable[[str], object] | None = None,
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
            if _is_disabled_email_auto_reply_task(task):
                try:
                    task_store.fail_reply_task(
                        task.id,
                        "email_auto_reply_disabled",
                        expected_execution_generation=task.execution_generation,
                    )
                except Exception as exc:  # noqa: BLE001 - keep the component alive
                    failures += 1
                    last_error_type = type(exc).__name__[:MAX_HEALTH_TEXT_LENGTH]
                continue
            try:
                context = load_task_context(task)
                from app.task_lifecycle import validate_audited_email_task

                if not validate_audited_email_task(task, context):
                    raise RuntimeError("legacy_email_unsubscribe_lifecycle")
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
        if failures == 0 and component_ready is not None:
            component_ready("email-agent-consumer")
        cycles += 1
        if max_cycles is None or cycles < max_cycles:
            sleep(CONSUMER_POLL_INTERVAL_SECONDS)


def run_training_scheduler_loop(
    training_tick: Callable[[], object],
    *,
    record_health: Callable[[str, Mapping[str, object]], object],
    component_ready: Callable[[str], object] | None = None,
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
            if component_ready is not None:
                component_ready("email-training")
        cycles += 1
        if max_cycles is None or cycles < max_cycles:
            sleep(TRAINING_INTERVAL_SECONDS)


def email_worker_components(
    dependencies: EmailWorkerDependencies | Any,
    *,
    accounts: Sequence[Mapping[str, object]],
    active_model: object,
    component_ready: Callable[[str], object] | None = None,
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
                component_ready=component_ready,
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
                component_ready=component_ready,
            ),
        ),
        (
            "email-training",
            partial(
                run_training_scheduler_loop,
                dependencies.training_tick,
                record_health=dependencies.record_health,
                component_ready=component_ready,
            ),
        ),
    )


def _scan_config(email_store: object, model_record: object | None):
    from app.email_classifier_contracts import EmailAction, EmailCategory
    from app.email_classifier_scan import EmailScanConfig
    from app.email_classifier_training import (
        CategoryEligibility,
        assess_email_action_eligibility,
    )

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
    metadata = getattr(model_record, "metadata", None)
    model_status = str(getattr(model_record, "status", ""))
    validation_method = str(getattr(metadata, "validation_method", ""))
    metrics_value = getattr(metadata, "per_category_metrics", {})
    metrics = metrics_value if isinstance(metrics_value, Mapping) else {}
    actions = {
        category: tuple(
            EmailAction(value) for value in by_category[category]["actions"]
        )
        for category in EmailCategory
    }
    eligibility: dict[EmailCategory, CategoryEligibility] = {}
    for category in EmailCategory:
        metric_value = metrics.get(category.value)
        metric = metric_value if isinstance(metric_value, Mapping) else None
        if metric is None:
            action_eligibility = assess_email_action_eligibility(
                category=category,
                actions=actions[category],
                model_status=model_status,
                validation_method=validation_method,
                configured_threshold=thresholds[category],
                evaluated_threshold=None,
                validated_precision=None,
                validation_positive_support=0,
                metadata_auto_action_eligible=False,
            )
            eligibility[category] = CategoryEligibility(
                category=category,
                configured_threshold=thresholds[category],
                validated_precision=None,
                validation_sample_count=0,
                auto_action_eligible=False,
                reason=(
                    "model_not_active"
                    if model_record is not None and model_status != "active"
                    else "model_eligibility_missing"
                ),
                action_eligibility=action_eligibility,
            )
            continue
        trained_threshold = float(metric["configured_threshold"])
        evaluated_threshold = float(metric["evaluated_threshold"])
        threshold_matches = (
            trained_threshold == thresholds[category]
            and evaluated_threshold == thresholds[category]
        )
        action_eligibility = assess_email_action_eligibility(
            category=category,
            actions=actions[category],
            model_status=model_status,
            validation_method=validation_method,
            configured_threshold=thresholds[category],
            evaluated_threshold=(
                evaluated_threshold if trained_threshold == thresholds[category]
                else trained_threshold
            ),
            validated_precision=float(metric["precision"]),
            validation_positive_support=int(
                metric.get(
                    "validation_positive_support",
                    metric["validation_sample_count"],
                )
            ),
            metadata_auto_action_eligible=bool(metric["auto_action_eligible"]),
        )
        configured_action_eligible = (
            any(item.auto_action_eligible for item in action_eligibility.values())
            if actions[category]
            else (
                model_status == "active"
                and validation_method == "time-ordered-holdout"
                and threshold_matches
                and bool(metric["auto_action_eligible"])
            )
        )
        eligibility[category] = CategoryEligibility(
            category=category,
            configured_threshold=thresholds[category],
            validated_precision=float(metric["precision"]),
            validation_sample_count=int(metric["validation_sample_count"]),
            auto_action_eligible=configured_action_eligible,
            reason=(
                str(metric["eligibility_reason"])
                if configured_action_eligible
                else next(
                    (
                        item.reason
                        for item in action_eligibility.values()
                        if not item.auto_action_eligible
                    ),
                    "model_eligibility_missing",
                )
            ),
            evaluated_threshold=evaluated_threshold,
            action_eligibility=action_eligibility,
        )
    return EmailScanConfig(
        config_version=versions.pop(),
        thresholds=thresholds,
        actions=actions,
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
    *,
    available_account_ids: Callable[[], Sequence[str]] | Sequence[str] | None = None,
) -> object | None:
    """Run one claimed action, or leave it pending when no provider exists."""

    if executor_factory is None:
        return None
    if callable(available_account_ids):
        available_account_ids = available_account_ids()
    if available_account_ids is not None and not tuple(available_account_ids):
        return None
    claimed_at = datetime.now(timezone.utc).isoformat()
    stale_before = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    recover = getattr(email_store, "recover_stale_processing_actions", None)
    if callable(recover):
        recover(stale_before=stale_before, recovered_at=claimed_at)
    claim_kwargs = {"claimed_at": claimed_at}
    if available_account_ids is not None:
        claim_kwargs["account_ids"] = available_account_ids
    action = email_store.claim_next_direct_action(**claim_kwargs)
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
            retryable=True,
        )
    email_store.complete_direct_action_attempt(
        action,
        status=result.status,
        provider_operation=result.provider_operation,
        provider_target=result.provider_target,
        provider_result_id=result.provider_result_id,
        error=result.error,
        finished_at=datetime.now(timezone.utc).isoformat(),
        retryable=bool(getattr(result, "retryable", True)),
    )
    return result


def _build_agent_orchestrator(settings: object, store: object):
    from app.agent_orchestrator import AgentOrchestrator
    from app.agent_runtime_production import build_production_agent_runtime
    from app.audit_agent import AuditAgentRunner
    from app.consumer_agent import ConsumerAgentRunner
    from app.email_store import EmailStore
    from app.email_unsubscribe_continuation import (
        EmailUnsubscribeContinuationDriver,
    )

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
        domain_continuation=EmailUnsubscribeContinuationDriver(
            EmailStore(Path(settings.db_path))
        ),
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
    from app.email_context_source import EmailContextSource

    try:
        return EmailContextSource(
            email_store,
            source_factory=source_factory,
        ).load_task_input(task)
    except ValueError as exc:
        raise EmailWorkerStartupError(str(exc)) from exc


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
        EmailActionPlan.model_validate_json(
            json.dumps(classification["action_plan"])
        ),
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
    from app.email_model_registry import EmailModelRegistry
    from app.email_store import EmailStore
    from app.email_task_producer import EmailActionTaskProducer
    from app.store import AutoReplyStore

    email_store = EmailStore(Path(settings.db_path))
    task_store = AutoReplyStore(Path(settings.db_path))
    task_producer = EmailActionTaskProducer(task_store, email_store)
    source_factory = _build_email_source_factory(settings)
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

    def record_health(scope: str, payload: Mapping[str, object]):
        task_store.set_service_state(
            f"email_worker_health:{scope}",
            json.dumps(dict(payload), sort_keys=True, separators=(",", ":")),
        )

    def load_scan_config(active_model: object):
        model_record = registry.get_model(active_model.loaded.model_id)
        config = _scan_config(email_store, model_record)
        metrics = model_record.metadata.per_category_metrics
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
                task_producer=task_producer.produce,
            )

        return EmailWorkerDependencies(
            load_enabled_accounts=load_enabled_accounts,
            load_active_model=load_active_model,
            scan_account=scan_account,
            run_direct_actions_once=partial(
                _run_next_direct_action,
                email_store,
                direct_action_executor_factory,
                available_account_ids=lambda: tuple(
                    str(account["account_id"])
                    for account in load_enabled_accounts()
                    if str(account.get("account_id") or "").strip()
                ),
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
            email_store=email_store,
        )

    return EmailWorkerBootstrap(
        load_enabled_accounts=load_enabled_accounts,
        load_active_model=load_active_model,
        build_dependencies=build_dependencies,
        record_health=record_health,
        task_store=task_store,
        email_store=email_store,
    )


def _build_email_source_factory(settings: object):
    from app.email_connector_config import resolve_secret
    from app.email_imap_readonly import ImapReadonlyAdapter

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

    return source_factory


def build_audited_email_unsubscribe_operation(settings: object) -> object:
    """Build the only executable Email unsubscribe operation."""

    from app.email_browser_profile import EmailBrowserProfile
    from app.email_classifier_contracts import EmailProviderLocator
    from app.email_store import EmailStore
    from app.email_unsubscribe import (
        EmailUnsubscribeEffect,
        UnsubscribeAuthenticationEvidence,
        browser_network_policy_for_entries,
        browser_unsubscribe_entries,
        execute_unsubscribe_in_dedicated_profile,
        extract_unsubscribe_entries,
    )
    from app.email_unsubscribe_audit import EmailUnsubscribeAuditOperation
    from app.email_imap_readonly import ephemeral_body_html
    from app.store import AutoReplyStore

    email_store = EmailStore(Path(settings.db_path))
    task_store = AutoReplyStore(Path(settings.db_path))
    source_factory = _build_email_source_factory(settings)

    def resolve_entries(
        locator: EmailProviderLocator,
        expected_reference: str,
        authentication: UnsubscribeAuthenticationEvidence | None = None,
        network_policy_reference: str = "",
        network_policy_origin_references: tuple[str, ...] = (),
    ):
        account = email_store.get_account(locator.account_id)
        if not isinstance(account, Mapping):
            raise ValueError("email unsubscribe account is unavailable")
        source = source_factory(account)
        try:
            batch = source.fetch_uid_batch(
                locator.folder,
                cursor_uidvalidity=locator.uidvalidity,
                last_seen_uid=max(0, locator.uid - 1),
                limit=2,
            )
            if batch.uidvalidity != locator.uidvalidity:
                raise ValueError("email unsubscribe provider generation changed")
            message = next(
                (
                    item
                    for item in batch.messages
                    if _message_identity(item) == locator.stable_message_identity
                    and int(item.get("uid") or 0) == locator.uid
                ),
                None,
            )
            if not isinstance(message, Mapping):
                raise ValueError("email unsubscribe source message is unavailable")
            entries = extract_unsubscribe_entries(
                list_unsubscribe=str(message.get("listUnsubscribe") or ""),
                list_unsubscribe_post=str(
                    message.get("listUnsubscribePost") or ""
                ),
                body_text=str(
                    message.get("textBody") or message.get("markdownBody") or ""
                ),
                body_html=ephemeral_body_html(message),
                authentication_evidence=authentication,
            )
            entries = browser_unsubscribe_entries(entries)
            policy = browser_network_policy_for_entries(entries)
            if (
                policy.reference != network_policy_reference
                or policy.origin_references != network_policy_origin_references
            ):
                raise ValueError("email unsubscribe network policy changed")
            if not any(
                entry.reference == expected_reference for entry in entries
            ):
                raise ValueError("email unsubscribe entry changed")
            return entries
        finally:
            _close_email_source(source)

    def execute_effect(
        effect: EmailUnsubscribeEffect,
        entries: tuple[object, ...],
        *,
        owner: Mapping[str, object],
        executed_prefix_length: int,
    ):
        if not entries:
            raise ValueError("audited unsubscribe executes exactly one effect")
        if not any(
            getattr(entry, "reference", None) == effect.entry_reference
            for entry in entries
        ):
            raise ValueError("email unsubscribe entry changed")
        profile = EmailBrowserProfile(
            Path(settings.db_path).parent / "email-browser-runtime"
        )
        policy = browser_network_policy_for_entries(entries)
        if (
            policy.reference != effect.network_policy_reference
            or policy.origin_references
            != effect.network_policy_origin_references
        ):
            raise ValueError("email unsubscribe network policy changed")
        return execute_unsubscribe_in_dedicated_profile(
            effect,
            entries,
            store=email_store,
            profile=profile,
            network_policy=policy,
            owner=owner,
            executed_prefix_length=executed_prefix_length,
        )

    return EmailUnsubscribeAuditOperation(
        task_store=task_store,
        email_store=email_store,
        resolve_entries=resolve_entries,
        execute_effect=execute_effect,
    )


def run_audited_email_unsubscribe(
    db_path: str | Path,
    task_id: int,
    execution_generation: str,
    *,
    audit_agent_run_id: int,
    accepted_action: Mapping[str, object],
) -> dict[str, object]:
    """Execute one accepted unsubscribe action through its bound Audit run."""

    from types import SimpleNamespace

    operation = build_audited_email_unsubscribe_operation(
        SimpleNamespace(
            db_path=Path(db_path),
            workspace=Path(db_path).parent,
        )
    )
    return operation.execute(
        task_id,
        execution_generation,
        audit_agent_run_id=audit_agent_run_id,
        accepted_action=accepted_action,
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


def _wait_for_email_components(threads: Sequence[object]) -> None:
    while True:
        dead = [
            thread
            for thread in threads
            if callable(getattr(thread, "is_alive", None)) and not thread.is_alive()
        ]
        if dead:
            raise RuntimeError("email worker component exited unexpectedly")
        Event().wait(1.0)


def _report_waiting_configuration(
    bootstrap: object,
    *,
    reason: str,
    output: TextIO,
) -> None:
    record_health = getattr(bootstrap, "record_health", None)
    if callable(record_health):
        record_health(
            "process:email-worker",
            {"status": "waiting_configuration", "reason": reason},
        )
    print(
        f"email-worker waiting_configuration reason={reason}",
        file=output,
        flush=True,
    )


def _wait_for_configuration(wait: Callable[[], object] | None) -> None:
    if wait is not None:
        wait()
        return
    Event().wait()


def _legacy_inventory_unavailable(
    dependencies: object,
    *,
    unresolved_task_ids: Sequence[int] = (),
) -> LegacyReconciliationResult:
    record_health = getattr(dependencies, "record_health", None)
    if callable(record_health):
        record_health(
            "component:email-legacy-lifecycle",
            {
                "status": "degraded",
                "error_code": "legacy_email_unsubscribe_inventory_unavailable",
            },
        )
    return LegacyReconciliationResult(
        authoritative=False,
        unresolved_task_ids=tuple(unresolved_task_ids),
    )


def _fail_nonterminal_legacy_unsubscribe_tasks(
    dependencies: object,
) -> LegacyReconciliationResult:
    """Fence inventoried legacy attempts and preserve inventory authority."""

    email_store = getattr(dependencies, "email_store", None)
    inventory = getattr(
        email_store,
        "list_nonterminal_legacy_unsubscribe_task_attempts",
        None,
    )
    if not callable(inventory):
        return _legacy_inventory_unavailable(dependencies)
    try:
        attempts = tuple(inventory())
    except Exception:  # noqa: BLE001 - preserve prior conservative fencing
        return _legacy_inventory_unavailable(dependencies)
    if not attempts:
        return LegacyReconciliationResult(authoritative=True)
    isolated: list[int] = []
    superseded: list[int] = []
    unresolved: list[int] = []
    terminalize = getattr(
        dependencies.task_store,
        "terminalize_legacy_email_unsubscribe_task",
        None,
    )
    read_current = getattr(
        email_store,
        "get_nonterminal_legacy_unsubscribe_task_attempt",
        None,
    )
    for index, attempt in enumerate(attempts):
        task_id = attempt.task_id
        try:
            fenced = bool(
                callable(terminalize)
                and terminalize(
                    task_id,
                    expected_execution_generation=attempt.execution_generation,
                    expected_status=attempt.status,
                )
            )
        except Exception:  # noqa: BLE001 - isolate each unsafe legacy attempt
            fenced = False
        if fenced:
            isolated.append(task_id)
            continue
        if not callable(read_current):
            remaining_ids = tuple(item.task_id for item in attempts[index + 1 :])
            return _legacy_inventory_unavailable(
                dependencies,
                unresolved_task_ids=(*unresolved, task_id, *remaining_ids),
            )
        try:
            current = read_current(task_id)
        except Exception:  # noqa: BLE001 - an unreadable replacement is unsafe
            remaining_ids = tuple(item.task_id for item in attempts[index + 1 :])
            return _legacy_inventory_unavailable(
                dependencies,
                unresolved_task_ids=(*unresolved, task_id, *remaining_ids),
            )
        (unresolved if current is not None else superseded).append(task_id)
    dependencies.record_health(
        "component:email-legacy-lifecycle",
        {
            "status": "degraded",
            "error_code": "legacy_email_unsubscribe_lifecycle",
            "task_count": len(attempts),
            "isolated_count": len(isolated),
            "superseded_count": len(superseded),
            "unresolved_count": len(unresolved),
        },
    )
    return LegacyReconciliationResult(
        authoritative=True,
        unresolved_task_ids=tuple(unresolved),
        task_count=len(attempts),
    )


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
        final_reconciliation = _fail_nonterminal_legacy_unsubscribe_tasks(bootstrap)
        accounts = tuple(bootstrap.load_enabled_accounts())
        if not accounts:
            _report_waiting_configuration(
                bootstrap,
                reason="empty_accounts",
                output=output,
            )
            _wait_for_configuration(wait)
            return
        try:
            active_model = bootstrap.load_active_model()
        except Exception:
            _report_waiting_configuration(
                bootstrap,
                reason="missing_model",
                output=output,
            )
            _wait_for_configuration(wait)
            return
        if active_model is None:
            _report_waiting_configuration(
                bootstrap,
                reason="missing_model",
                output=output,
            )
            _wait_for_configuration(wait)
            return
        build_dependencies = getattr(bootstrap, "build_dependencies", None)
        dependencies = (
            build_dependencies(accounts, active_model)
            if callable(build_dependencies)
            else bootstrap
        )
        _validate_email_worker_dependencies(dependencies)
        if dependencies is not bootstrap:
            final_reconciliation = _fail_nonterminal_legacy_unsubscribe_tasks(
                dependencies
            )
        unresolved_legacy_task_ids = set(
            final_reconciliation.unresolved_task_ids
        )
        agent_consumer_allowed = (
            final_reconciliation.authoritative
            and not unresolved_legacy_task_ids
        )
        if (
            final_reconciliation.authoritative
            and final_reconciliation.task_count == 0
        ):
            dependencies.record_health(
                "component:email-legacy-lifecycle",
                {
                    "status": "ready",
                    "task_count": 0,
                    "unresolved_count": 0,
                },
            )
        all_component_names = (
            "email-scan-actions",
            "email-agent-consumer",
            "email-training",
        )
        component_names = tuple(
            name
            for name in all_component_names
            if not (
                not agent_consumer_allowed and name == "email-agent-consumer"
            )
        )
        readiness = EmailWorkerReadiness(
            component_names,
            record_health=dependencies.record_health,
            accounts=len(accounts),
        )
        components = email_worker_components(
            dependencies,
            accounts=accounts,
            active_model=active_model,
            component_ready=readiness.mark_ready,
        )
        if not agent_consumer_allowed:
            components = tuple(
                component
                for component in components
                if component[0] != "email-agent-consumer"
            )
            if final_reconciliation.authoritative:
                dependencies.record_health(
                    "component:email-agent-consumer",
                    {
                        "status": "degraded",
                        "error_code": "legacy_email_unsubscribe_lifecycle",
                        "unresolved_count": len(unresolved_legacy_task_ids),
                    },
                )
            else:
                dependencies.record_health(
                    "component:email-agent-consumer",
                    {
                        "status": "degraded",
                        "error_code": (
                            "legacy_email_unsubscribe_inventory_unavailable"
                        ),
                    },
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
            "status": "starting",
            "accounts": len(accounts),
            "components": len(components),
        },
    )
    print(
        f"email-worker starting accounts={len(accounts)} components={len(components)}",
        file=output,
        flush=True,
    )
    threads = []
    for name, target in components:
        thread = thread_factory(
            target=target,
            name=f"ceo-agent-{name}",
            daemon=True,
        )
        threads.append(thread)
        thread.start()
    if wait is not None:
        wait()
    else:
        _wait_for_email_components(threads)
