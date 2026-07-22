from __future__ import annotations

import pytest

from app.quality.monitor import run_quality_monitor_loop, run_quality_monitor_once
from app.store import AutoReplyStore


def test_monitor_persists_snapshot_and_heartbeat(tmp_path) -> None:
    store = AutoReplyStore(tmp_path / "service.sqlite3")
    store.record_component_health("producer", success=True)
    store.record_component_health("consumer", success=True)

    run_quality_monitor_once(store, required_components=("producer", "consumer"))

    assert store.list_quality_snapshots(limit=1)[0]["ready"] == 1
    health = {row["component"]: row for row in store.list_component_health()}
    assert health["quality-monitor"]["status"] == "ready"


def test_monitor_records_failure_and_continues_to_sleep(tmp_path, monkeypatch) -> None:
    store = AutoReplyStore(tmp_path / "service.sqlite3")
    sleeps = 0

    def fail_once(*args, **kwargs):
        raise LookupError("synthetic")

    def stop_after_iteration(seconds: int) -> None:
        nonlocal sleeps
        assert seconds == 7
        sleeps += 1
        if sleeps == 3:
            raise RuntimeError("stop")

    monkeypatch.setattr("app.quality.monitor.run_quality_monitor_once", fail_once)
    with pytest.raises(RuntimeError, match="stop"):
        run_quality_monitor_loop(store, interval_seconds=7, sleep=stop_after_iteration)

    health = {row["component"]: row for row in store.list_component_health()}
    assert health["quality-monitor"]["status"] == "failed"
    assert health["quality-monitor"]["last_error_kind"] == "LookupError"
