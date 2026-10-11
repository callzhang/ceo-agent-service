import threading
from types import SimpleNamespace

import pytest

from app import cli
from app.cli import WorkerSettings
from app.store import AutoReplyStore
from app.dispatcher.service import ConsumerDispatcher


def test_dispatcher_failed_tick_does_not_report_recovery():
    dispatcher = ConsumerDispatcher.__new__(ConsumerDispatcher)
    dispatcher.wake_event = threading.Event()
    ticks = []
    dispatcher.tick_observer = ticks.append

    def fail_dispatch(*_args, **_kwargs):
        raise RuntimeError("dispatch failed")

    dispatcher.dispatch_available = fail_dispatch
    with pytest.raises(RuntimeError, match="dispatch failed"):
        dispatcher.run(stop_event=threading.Event())
    assert ticks == []


def test_dispatcher_start_clears_stale_health(monkeypatch, tmp_path):
    db_path = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(db_path)
    store.set_service_health_component(
        "agent-cron-dispatcher",
        state="degraded",
        status="failed",
        detail="database disk image is malformed",
        latest_error="database disk image is malformed",
        latest_error_at="2026-09-11T00:44:08+00:00",
    )

    class StopDispatcher(Exception):
        pass

    class FakeDispatcher:
        def __init__(self, **_kwargs):
            pass

        def run(self, *, stop_event):
            del stop_event

        def drain(self):
            raise StopDispatcher

    fake_runtime = SimpleNamespace(
        config=SimpleNamespace(), refresh_runtime_capabilities=None
    )
    monkeypatch.setattr(cli, "ConsumerDispatcher", FakeDispatcher)
    monkeypatch.setattr(cli, "_scheduled_task_option_service", lambda *_: object())
    monkeypatch.setattr(cli, "_create_service_worker", lambda *_: object())
    monkeypatch.setattr(cli, "_create_meeting_dws", lambda *_: object())
    monkeypatch.setattr(cli, "MeetingAlignmentCodexRunner", lambda **_: object())
    monkeypatch.setattr(cli, "TaskAgentCodexRunner", lambda **_: object())
    monkeypatch.setattr(cli, "TaskAgentRunner", lambda *_: object())
    monkeypatch.setattr(cli, "_build_okr_review_runner", lambda *_: object())
    monkeypatch.setattr(
        "app.agent_runtime_production.build_production_agent_runtime",
        lambda **_: fake_runtime,
    )
    monkeypatch.setattr(
        "app.agent_runtime_production.build_production_routed_codex_execution",
        lambda **_: object(),
    )

    with pytest.raises(StopDispatcher):
        cli.run_agent_cron_dispatcher_loop(
            WorkerSettings(db_path=db_path, dry_run=True),
            object(),
            wake_event=threading.Event(),
        )

    [component] = AutoReplyStore(db_path).list_service_health_components()
    assert component["component"] == "agent-cron-dispatcher"
    assert component["state"] == "healthy"
    assert component["status"] == "running"
    assert component["latest_error"] == ""
    assert component["latest_error_at"] == ""
    assert component["latest_tick_at"]
