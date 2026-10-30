import pytest

from app import cli
from app.cli import WorkerSettings


class StopLoop(Exception):
    pass


class FakeStore:
    def __init__(self, _path):
        self.errors = []
        self.health_updates = []
        self.resolutions = []

    def record_error(self, *args):
        self.errors.append(args)

    def resolve_unresolved_errors_by_kind(self, kind, *, resolution):
        self.resolutions.append((kind, resolution))
        return 1

    def set_service_health_component(self, component, **kwargs):
        self.health_updates.append((component, kwargs))


def _stop_after_one_cycle(monkeypatch):
    monkeypatch.setattr(cli, "_create_meeting_dws", lambda _settings: object())

    def stop(_seconds):
        raise StopLoop

    return stop


def test_successful_cycle_refreshes_health_and_clears_stale_error(
    monkeypatch, tmp_path
):
    store = FakeStore(tmp_path / "worker.sqlite3")
    monkeypatch.setattr(cli, "AutoReplyStore", lambda _path: store)
    monkeypatch.setattr(cli, "deliver_ready_meeting_alignment_jobs", lambda *_a, **_k: [])

    with pytest.raises(StopLoop):
        cli.run_meeting_delivery_loop(
            WorkerSettings(db_path=tmp_path / "worker.sqlite3"),
            sleep=_stop_after_one_cycle(monkeypatch),
            network_ready=lambda: True,
        )

    assert store.resolutions == [
        (
            "meeting_alignment_delivery",
            "recovered by later successful meeting delivery cycle",
        )
    ]
    assert len(store.health_updates) == 1
    component, health = store.health_updates[0]
    assert component == "meeting-delivery"
    assert health["state"] == "healthy"
    assert health["status"] == "running"
    assert health["detail"] == ""
    assert health["latest_tick_at"]
    assert health["latest_error"] == ""
    assert health["latest_error_at"] == ""


def test_failed_cycle_does_not_clear_component_health_error(monkeypatch, tmp_path):
    store = FakeStore(tmp_path / "worker.sqlite3")
    monkeypatch.setattr(cli, "AutoReplyStore", lambda _path: store)

    def fail_delivery(*_args, **_kwargs):
        raise RuntimeError("delivery cycle failed")

    monkeypatch.setattr(cli, "deliver_ready_meeting_alignment_jobs", fail_delivery)

    with pytest.raises(StopLoop):
        cli.run_meeting_delivery_loop(
            WorkerSettings(db_path=tmp_path / "worker.sqlite3"),
            sleep=_stop_after_one_cycle(monkeypatch),
            network_ready=lambda: True,
        )

    assert store.errors == [
        ("", "", "meeting_alignment_delivery", "delivery cycle failed")
    ]
    assert store.health_updates == []
    assert store.resolutions == []
