import json
import pytest

from app.repository_upgrade import GitRepository, RepositoryUpgradeService
from app.repository_updater import (
    persist_operation,
    UpgradeOperation,
    UPGRADE_OPERATION_STATE_KEY,
)
from tests.test_repository_updater import fixture_repo, StateStore, git
from app.service_update_runner import run_update


@pytest.mark.parametrize("fail", [False, True])
def test_web_runner_uses_formal_pinned_deploy_and_releases_reservation(
    tmp_path, monkeypatch, fail
):
    import app.service_update_runner as runner

    local, _ = fixture_repo(tmp_path)
    store = StateStore()
    service = RepositoryUpgradeService(
        repository=GitRepository(local), store=store, remote="origin", branch="main"
    )
    snapshot = service.check()
    service.reserve_operation("web-op", snapshot.fingerprint)
    persist_operation(
        store,
        UpgradeOperation(
            "web-op",
            snapshot.fingerprint,
            snapshot.local_commit,
            snapshot.remote_commit,
        ),
    )
    monkeypatch.setattr(runner, "ExistingSchemaUpgradeStateStore", lambda _: store)

    def deploy(root, db, **kwargs):
        assert root == local and kwargs == {
            "target_commit": snapshot.remote_commit,
            "operation_id": "web-op",
        }
        if fail:
            raise SystemExit("service did not become idle")
        git(local, "merge", "--ff-only", snapshot.remote_commit)
        return "deployed"

    monkeypatch.setattr(runner, "deploy", deploy)
    if fail:
        with pytest.raises(SystemExit):
            run_update("web-op", local, tmp_path / "db")
    else:
        run_update("web-op", local, tmp_path / "db")
    assert service.load_state().operation is None
    progress = json.loads(store.get_service_state(UPGRADE_OPERATION_STATE_KEY))
    assert progress["status"] == ("failed" if fail else "succeeded")
    assert progress["operation_id"] == "web-op"
    if not fail:
        assert progress["installed_commit"] == snapshot.remote_commit


def test_existing_success_warning_is_preserved(tmp_path, monkeypatch):
    import app.service_update_runner as runner

    local, _ = fixture_repo(tmp_path)
    store = StateStore()
    service = RepositoryUpgradeService(
        repository=GitRepository(local), store=store, remote="origin", branch="main"
    )
    snapshot = service.check()
    service.reserve_operation("warning", snapshot.fingerprint)
    operation = UpgradeOperation(
        "warning", snapshot.fingerprint, snapshot.local_commit, snapshot.remote_commit
    )
    persist_operation(store, operation)
    monkeypatch.setattr(runner, "ExistingSchemaUpgradeStateStore", lambda _: store)

    def deploy(*args, **kwargs):
        git(local, "merge", "--ff-only", snapshot.remote_commit)
        runner.record_result(
            store,
            operation,
            "succeeded",
            installed_commit=snapshot.remote_commit,
            error="receipt cleanup warning",
        )
        return "deployed"

    monkeypatch.setattr(runner, "deploy", deploy)
    run_update("warning", local, tmp_path / "db")
    assert (
        json.loads(store.get_service_state(UPGRADE_OPERATION_STATE_KEY))["error"]
        == "receipt cleanup warning"
    )


def test_lost_progress_during_startup_releases_only_own_reservation(
    tmp_path, monkeypatch
):
    import app.service_update_runner as runner

    local, _ = fixture_repo(tmp_path)
    store = StateStore()
    service = RepositoryUpgradeService(
        repository=GitRepository(local), store=store, remote="origin", branch="main"
    )
    snapshot = service.check()
    service.reserve_operation("web-race", snapshot.fingerprint)
    other = UpgradeOperation(
        "cli-other", "cli", snapshot.local_commit, snapshot.remote_commit
    )
    persist_operation(store, other)
    before = store.get_service_state(UPGRADE_OPERATION_STATE_KEY)
    monkeypatch.setattr(runner, "ExistingSchemaUpgradeStateStore", lambda _: store)
    with pytest.raises(Exception, match="not current"):
        run_update("web-race", local, tmp_path / "db")
    assert service.load_state().operation is None
    assert store.get_service_state(UPGRADE_OPERATION_STATE_KEY) == before
