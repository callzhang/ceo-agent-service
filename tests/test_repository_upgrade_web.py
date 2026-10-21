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
        self.reserved = False
        self.values = {}
        self.store = type(
            "Store",
            (),
            {
                "set_service_state": lambda _, key, value: self.values.__setitem__(
                    key, value
                ),
                "get_service_state": lambda _, key: self.values.get(key),
            },
        )()
        self.repository = type(
            "Repo", (), {"resolve_ref": lambda *_: "a" * 40, "root": "/tmp"}
        )()

    def reserve_operation(self, operation_id, fingerprint):
        duplicate = self.reserved
        self.reserved = True
        return type(
            "Reservation",
            (),
            {
                "idempotent": duplicate,
                "operation": type("Operation", (), {"operation_id": operation_id})(),
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


def test_dirty_start_requires_explicit_preservation_metadata():
    app = FastAPI()
    service = FakeService()
    service.snapshot = service.snapshot.model_copy(
        update={"status": UpgradeStatus.LOCAL_CHANGES, "dirty_paths": ("app/a.py",)}
    )
    register_repository_upgrade_routes(
        app,
        service_factory=lambda: service,
        updater_launcher=lambda _operation: 4242,
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/repository-upgrade/start",
            json={"operation_id": "op-1", "fingerprint": "fp-1"},
        )

    assert response.status_code == 422


def test_duplicate_start_does_not_launch_or_overwrite_progress_twice():
    app, service, launched = FastAPI(), FakeService(), []
    register_repository_upgrade_routes(
        app,
        service_factory=lambda: service,
        updater_launcher=lambda op: launched.append(op) or 4242,
    )
    with TestClient(app) as client:
        data = {"operation_id": "once", "fingerprint": "fp-1"}
        assert (
            client.post("/api/repository-upgrade/start", json=data).status_code == 202
        )
        assert (
            client.post("/api/repository-upgrade/start", json=data).status_code == 202
        )
        status = client.get("/api/repository-upgrade/status").json()
    assert len(launched) == 1
    assert status["progress"]["operation_id"] == "once"
    assert status["current_commit"] == "a" * 40


def test_notes_bind_to_reviewed_snapshot_and_return_ai_error_with_commits(monkeypatch):
    import app.repository_upgrade_web as web
    from app.repository_release_notes import ReleaseNotesError

    app, service = FastAPI(), FakeService()

    def notes(repository, store, local, target, summarize):
        assert local == "a" * 40 and target == "b" * 40
        raise ReleaseNotesError(
            commits=[{"sha": "b" * 40, "subject": "change", "body": "details"}]
        )

    monkeypatch.setattr(web, "release_notes", notes)
    register_repository_upgrade_routes(app, service_factory=lambda: service)
    with TestClient(app) as client:
        assert (
            client.post(
                "/api/repository-upgrade/notes", json={"fingerprint": "stale"}
            ).status_code
            == 409
        )
        response = client.post(
            "/api/repository-upgrade/notes", json={"fingerprint": "fp-1"}
        )
    assert response.status_code == 200
    assert response.json()["summary_error"]
    assert response.json()["commits"][0]["body"] == "details"


def test_read_only_state_does_not_wait_for_long_deployment_mutex(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from app.repository_upgrade import GitRepository, RepositoryUpgradeService
    from tests.test_repository_updater import fixture_repo, StateStore

    local, _ = fixture_repo(tmp_path)
    service = RepositoryUpgradeService(
        repository=GitRepository(local),
        store=StateStore(),
        remote="origin",
        branch="main",
    )
    with ThreadPoolExecutor(max_workers=1) as pool:
        with service.repository.mutex():
            future = pool.submit(service.load_state)
            assert future.result(timeout=1).snapshot is None
