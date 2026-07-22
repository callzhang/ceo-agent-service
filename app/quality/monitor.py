from __future__ import annotations

from collections.abc import Callable

from app.quality.incidents import reconcile_snapshot_incidents
from app.quality.snapshot import DEFAULT_REQUIRED_COMPONENTS, build_quality_snapshot
from app.store import AutoReplyStore


def run_quality_monitor_once(
    store: AutoReplyStore,
    *,
    required_components: tuple[str, ...] = DEFAULT_REQUIRED_COMPONENTS,
) -> None:
    snapshot = build_quality_snapshot(
        store, required_components=required_components
    )
    store.record_quality_snapshot(snapshot)
    reconcile_snapshot_incidents(store, snapshot)
    store.record_component_health("quality-monitor", success=True)


def run_quality_monitor_loop(
    store: AutoReplyStore,
    *,
    interval_seconds: int = 60,
    required_components: tuple[str, ...] = DEFAULT_REQUIRED_COMPONENTS,
    sleep: Callable[[int], None],
) -> None:
    while True:
        try:
            run_quality_monitor_once(
                store, required_components=required_components
            )
        except Exception as exc:
            store.record_component_health(
                "quality-monitor",
                success=False,
                error_kind=type(exc).__name__,
            )
        sleep(interval_seconds)
