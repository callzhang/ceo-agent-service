from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.repository_upgrade import RepositorySnapshot, UpgradeStatus
from app.repository_upgrade_web import (
    render_repository_upgrade_mount,
    register_repository_upgrade_routes,
)


class FakeService:
    def __init__(self):
        self.snapshot = RepositorySnapshot(
            status=UpgradeStatus.UPDATE_AVAILABLE,
            checked_at=datetime.now(timezone.utc),
            local_commit="a" * 40,
            remote_commit="b" * 40,
            commits_behind=2,
            release_summary=["new feature"],
            fingerprint="fp-1",
        )
        self.checked = 0
        self.store = type("Store", (), {"set_service_state": lambda *_args: None})()

    def reserve_operation(self, operation_id, fingerprint):
        return type(
            "Reservation",
            (),
            {
                "operation": type(
                    "Operation", (), {"operation_id": operation_id}
                )()
            },
        )()

    def load_state(self):
        return type("State", (), {"snapshot": self.snapshot, "operation": None})()

    def check(self):
        self.checked += 1
        return self.snapshot


def test_upgrade_mount_polls_banner_without_page_reload():
    html = render_repository_upgrade_mount()

    assert 'id="repository-upgrade-banner"' in html
    assert 'fetch("/api/repository-upgrade/status"' in html
    assert "window.location.reload" not in html


def test_status_and_check_routes_use_service_factory():
    app = FastAPI()
    service = FakeService()
    register_repository_upgrade_routes(app, service_factory=lambda: service)

    with TestClient(app) as client:
        status = client.get("/api/repository-upgrade/status")
        checked = client.post("/api/repository-upgrade/check")

    assert status.status_code == 200
    assert status.json()["snapshot"]["status"] == "update_available"
    assert checked.status_code == 200
    assert service.checked == 1


def test_start_route_requires_current_fingerprint_and_launches_once():
    app = FastAPI()
    service = FakeService()
    launched = []
    register_repository_upgrade_routes(
        app,
        service_factory=lambda: service,
        updater_launcher=lambda operation: launched.append(operation) or 4242,
    )

    with TestClient(app) as client:
        stale = client.post(
            "/api/repository-upgrade/start",
            json={"operation_id": "op-1", "fingerprint": "stale"},
        )
        accepted = client.post(
            "/api/repository-upgrade/start",
            json={"operation_id": "op-1", "fingerprint": "fp-1"},
        )

    assert stale.status_code == 409
    assert accepted.status_code == 202
    assert accepted.json()["pid"] == 4242
    assert launched[0].target_commit == "b" * 40
