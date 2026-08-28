from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
from typing import Callable, Protocol
from urllib.request import urlopen

from app.database_backup import create_database_backup
from app.repository_upgrade import GitRepository


UPGRADE_OPERATION_STATE_KEY = "repository_upgrade_operation:v1"


class UpgradePreconditionError(RuntimeError):
    """The repository changed or is not safe for an automatic fast-forward."""


class UpgradeFailed(RuntimeError):
    """The update was installed but verification or restart failed."""


class UpgradeStateStore(Protocol):
    def get_service_state(self, key: str) -> str | None: ...

    def set_service_state(self, key: str, value: str) -> None: ...


@dataclass(frozen=True)
class UpgradeOperation:
    operation_id: str
    expected_fingerprint: str
    original_commit: str
    target_commit: str
    branch_name: str = ""
    commit_message: str = ""


@dataclass(frozen=True)
class UpgradeResult:
    operation_id: str
    status: str
    installed_commit: str = ""
    backup_path: str = ""
    error: str = ""


def load_persisted_operation(
    store: UpgradeStateStore,
    operation_id: str,
) -> UpgradeOperation:
    raw = store.get_service_state(UPGRADE_OPERATION_STATE_KEY)
    if not raw:
        raise UpgradePreconditionError("repository upgrade operation is missing")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise UpgradePreconditionError("repository upgrade operation is invalid") from exc
    if payload.get("operation_id") != operation_id:
        raise UpgradePreconditionError("repository upgrade operation is not current")
    try:
        return UpgradeOperation(
            operation_id=operation_id,
            expected_fingerprint=str(payload.get("expected_fingerprint", "")),
            original_commit=str(payload["original_commit"]),
            target_commit=str(payload["target_commit"]),
            branch_name=str(payload.get("branch_name", "")),
            commit_message=str(payload.get("commit_message", "")),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise UpgradePreconditionError("repository upgrade operation is invalid") from exc


def persist_operation(store: UpgradeStateStore, operation: UpgradeOperation) -> None:
    payload = {
        "operation_id": operation.operation_id,
        "status": "preparing",
        "expected_fingerprint": operation.expected_fingerprint,
        "original_commit": operation.original_commit,
        "target_commit": operation.target_commit,
        "branch_name": operation.branch_name,
        "commit_message": operation.commit_message,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    store.set_service_state(
        UPGRADE_OPERATION_STATE_KEY,
        json.dumps(payload, ensure_ascii=False, sort_keys=True),
    )


def _default_restart() -> None:
    result = subprocess.run(
        ["launchctl", "kickstart", "-k", f"gui/{_uid()}/com.ceo-agent-service.main"],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise UpgradeFailed("launchd restart failed")


def _uid() -> int:
    import os

    return os.getuid()


def _default_health() -> bool:
    try:
        with urlopen("http://127.0.0.1:8765/", timeout=10) as response:
            return 200 <= response.status < 300
    except Exception:  # noqa: BLE001 - health is a boolean probe
        return False


class RepositoryUpdater:
    """Execute one verified, fast-forward-only repository upgrade.

    The caller must obtain the operation reservation through
    :class:`RepositoryUpgradeService` before launching this executor. The
    updater still rechecks every mutable precondition while holding the shared
    repository mutex, so a stale UI request cannot overwrite local work.
    """

    def __init__(
        self,
        repository_root: Path,
        store: UpgradeStateStore,
        *,
        remote: str = "origin",
        branch: str = "main",
        database_path: Path | None = None,
        dependency_sync: Callable[[], None] | None = None,
        verification: Callable[[], None] | None = None,
        restart: Callable[[], None] = _default_restart,
        health: Callable[[], bool] = _default_health,
    ) -> None:
        self.repository = GitRepository(repository_root)
        self.store = store
        self.remote = remote
        self.branch = branch
        self.database_path = database_path
        self.dependency_sync = dependency_sync or (lambda: None)
        self.verification = verification or (lambda: None)
        self.restart = restart
        self.health = health

    @property
    def target_ref(self) -> str:
        return f"refs/heads/{self.branch}"

    @property
    def remote_ref(self) -> str:
        return f"refs/remotes/{self.remote}/{self.branch}"

    def execute(self, operation: UpgradeOperation) -> UpgradeResult:
        with self.repository.mutex():
            self._persist(operation, "preparing")
            self.repository.fetch(self.remote)
            records = self._recheck(operation)
            backup_path = self._backup(operation)
            if records:
                self._preserve_local_changes(operation)
            self._persist(operation, "updating", backup_path=backup_path)
            try:
                self.repository._run(
                    ["merge", "--ff-only", self.remote_ref],
                    category="upgrade_merge",
                )
                self._persist(operation, "verifying", backup_path=backup_path)
                self.dependency_sync()
                self.verification()
                self._persist(operation, "restarting", backup_path=backup_path)
                self.restart()
                if not self.health():
                    raise UpgradeFailed("replacement service health check failed")
            except Exception as exc:
                rollback_status = "failed"
                try:
                    installed = self.repository.resolve_ref(self.target_ref)
                    if installed == operation.target_commit and not self.repository.status_records():
                        self.repository._run(
                            ["update-ref", self.target_ref, operation.original_commit],
                            category="upgrade_rollback",
                        )
                        self.repository._run(
                            ["reset", "--hard", operation.original_commit],
                            category="upgrade_rollback_checkout",
                        )
                        self.restart()
                        if not self.health():
                            raise UpgradeFailed("rollback health check failed")
                        rollback_status = "rolled_back"
                except Exception:
                    rollback_status = "needs_manual"
                self._persist(
                    operation,
                    rollback_status,
                    backup_path=backup_path,
                    error="upgrade verification or restart failed",
                )
                raise UpgradeFailed("upgrade verification or restart failed") from exc
            installed = self.repository.resolve_ref(self.target_ref)
            self._persist(
                operation,
                "succeeded",
                backup_path=backup_path,
                installed_commit=installed,
            )
            return UpgradeResult(
                operation_id=operation.operation_id,
                status="succeeded",
                installed_commit=installed,
                backup_path=str(backup_path) if backup_path else "",
            )

    def _recheck(self, operation: UpgradeOperation) -> list[object]:
        current = self.repository.resolve_ref(self.target_ref)
        remote = self.repository.resolve_ref(self.remote_ref)
        if current != operation.original_commit or remote != operation.target_commit:
            raise UpgradePreconditionError("repository revision changed")
        if not self.repository.is_ancestor(current, remote):
            raise UpgradePreconditionError("repository target is diverged")
        records = self.repository.status_records()
        if records and not operation.branch_name.strip():
            raise UpgradePreconditionError("repository fingerprint changed")
        fingerprint = self.repository.fingerprint(
            self.branch,
            current,
            remote,
            records,
        )
        if fingerprint != operation.expected_fingerprint:
            raise UpgradePreconditionError("repository fingerprint changed")
        return records

    def _preserve_local_changes(self, operation: UpgradeOperation) -> None:
        if not operation.commit_message.strip():
            raise UpgradePreconditionError("commit message is required for local changes")
        if not self._valid_preservation_branch(operation.branch_name):
            raise UpgradePreconditionError("preservation branch is invalid or exists")
        self.repository._run(
            ["switch", "-c", operation.branch_name],
            category="preservation_branch",
        )
        try:
            self.repository._run(["add", "--all"], category="preservation_stage")
            self.repository._run(
                ["commit", "-m", operation.commit_message],
                category="preservation_commit",
            )
            self.repository._run(
                ["switch", self.branch],
                category="preservation_return",
            )
        except Exception:
            # The branch and original changes remain available for manual repair;
            # never reset or delete operator work after a preservation failure.
            raise

    def _valid_preservation_branch(self, branch_name: str) -> bool:
        if not branch_name.strip() or branch_name == self.branch:
            return False
        valid = self.repository._run(
            ["check-ref-format", "--branch", branch_name],
            category="preservation_branch_validation",
            accepted_returncodes=(0, 1),
        )
        if valid.returncode != 0:
            return False
        for ref in (
            f"refs/heads/{branch_name}",
            f"refs/remotes/{self.remote}/{branch_name}",
        ):
            existing = self.repository._run(
                ["show-ref", "--verify", "--quiet", ref],
                category="preservation_branch_validation",
                accepted_returncodes=(0, 1),
            )
            if existing.returncode == 0:
                return False
        return True

    def _backup(self, operation: UpgradeOperation) -> Path | None:
        if self.database_path is None or not self.database_path.exists():
            return None
        destination = (
            self.database_path.parent
            / "backups"
            / f"{self.database_path.stem}-before-upgrade-{operation.operation_id}.sqlite3"
        )
        return create_database_backup(self.database_path, destination)

    def _persist(
        self,
        operation: UpgradeOperation,
        status: str,
        *,
        backup_path: Path | None = None,
        installed_commit: str = "",
        error: str = "",
    ) -> None:
        payload = {
            "operation_id": operation.operation_id,
            "status": status,
            "expected_fingerprint": operation.expected_fingerprint,
            "original_commit": operation.original_commit,
            "target_commit": operation.target_commit,
            "branch_name": operation.branch_name,
            "commit_message": operation.commit_message,
            "installed_commit": installed_commit,
            "backup_path": str(backup_path) if backup_path else "",
            "error": error,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self.store.set_service_state(
            UPGRADE_OPERATION_STATE_KEY,
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
        )


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="ceo-agent repository-updater")
    parser.add_argument("--operation-id", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--db", required=True)
    args = parser.parse_args()

    from app.store import AutoReplyStore

    store = AutoReplyStore(Path(args.db))
    operation = load_persisted_operation(store, args.operation_id)
    result = RepositoryUpdater(
        Path(args.repo),
        store,
        database_path=Path(args.db),
    ).execute(operation)
    print(json.dumps(result.__dict__, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
