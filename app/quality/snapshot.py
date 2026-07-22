from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

from app.quality.migrations import schema_version
from app.quality.models import ComponentHealth, ComponentStatus, QualitySnapshot
from app.store import AutoReplyStore

DEFAULT_REQUIRED_COMPONENTS = (
    "network",
    "dws",
    "producer",
    "consumer",
    "meeting-producer",
    "meeting-consumer",
    "task-maintenance",
    "quality-monitor",
)


def _positive_seconds(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


def current_commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        marker = Path(".ceo-release.json")
        if marker.is_file():
            try:
                payload = json.loads(marker.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return "unknown"
            commit = payload.get("commit")
            return str(commit) if isinstance(commit, str) and commit else "unknown"
        return "unknown"


def build_quality_snapshot(
    store: AutoReplyStore,
    *,
    commit: str | None = None,
    pid: int | None = None,
    now: datetime | None = None,
    required_components: tuple[str, ...] = DEFAULT_REQUIRED_COMPONENTS,
    poll_seconds: int = 60,
) -> QualitySnapshot:
    observed_at = now or datetime.now(timezone.utc)
    rows = {row["component"]: row for row in store.list_component_health()}
    components: list[ComponentHealth] = []
    for component in required_components:
        row = rows.get(component)
        if row is None:
            components.append(ComponentHealth(component=component, status="missing"))
            continue
        raw_status = str(row["status"])
        status: ComponentStatus = (
            cast(ComponentStatus, raw_status)
            if raw_status in {"ready", "degraded", "failed", "stale", "missing"}
            else "failed"
        )
        last_success = str(row["last_success_at"] or "")
        if last_success:
            parsed = datetime.fromisoformat(last_success.replace("Z", "+00:00"))
            if (observed_at - parsed).total_seconds() > poll_seconds * 3:
                status = "stale"
        components.append(
            ComponentHealth(
                component=component,
                status=status,
                last_success_at=last_success,
                last_failure_at=str(row["last_failure_at"] or ""),
                consecutive_failures=int(str(row["consecutive_failures"] or 0)),
            )
        )
    backlog = store.quality_backlog_counts()
    oldest_queue_age = store.oldest_quality_queue_age_seconds()
    task_slo_seconds = _positive_seconds("CEO_QUALITY_TASK_SLO_SECONDS", 30 * 60)
    processing_limit_seconds = (
        2 * _positive_seconds("CEO_QUALITY_PROCESSING_TIMEOUT_SECONDS", 20 * 60)
        + 5 * 60
    )
    overdue = oldest_queue_age > task_slo_seconds
    processing_overdue = (
        backlog["processing"] > 0 and oldest_queue_age > processing_limit_seconds
    )
    ready = all(component.status == "ready" for component in components)
    ready = (
        ready
        and backlog["failed"] == 0
        and backlog["unknown"] == 0
        and not overdue
        and not processing_overdue
    )
    if backlog["failed"] or backlog["unknown"]:
        slo_status = "fail"
    elif overdue or processing_overdue:
        slo_status = "fail"
    elif not ready or backlog["processing"]:
        slo_status = "warn"
    else:
        slo_status = "pass"
    with store._connect() as connection:
        version = schema_version(connection)
    return QualitySnapshot(
        commit=commit or current_commit(),
        pid=pid if pid is not None else os.getpid(),
        schema_version=version,
        generated_at=observed_at.isoformat(),
        components=tuple(components),
        backlog=backlog,
        oldest_queue_age_seconds=oldest_queue_age,
        failed_actions=backlog["failed_actions"],
        unknown_actions=backlog["unknown_actions"],
        slo_status=slo_status,
        ready=ready,
    )
