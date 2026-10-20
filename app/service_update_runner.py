"""Detached web upgrade process using the same formal deploy path as operators."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from app.deploy import deploy
from app.repository_upgrade import GitRepository, RepositoryUpgradeService
from app.repository_updater import (
    ExistingSchemaUpgradeStateStore,
    load_persisted_operation,
    UPGRADE_OPERATION_STATE_KEY,
)


def record_result(store, operation, status, *, installed_commit="", error=""):
    raw = store.get_service_state(UPGRADE_OPERATION_STATE_KEY)
    payload = json.loads(raw) if raw else {}
    if payload.get("operation_id") == operation.operation_id:
        payload.update(
            status=status,
            installed_commit=installed_commit,
            error=error,
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
        store.set_service_state(
            UPGRADE_OPERATION_STATE_KEY, json.dumps(payload, ensure_ascii=False)
        )


def run_update(operation_id: str, root: Path, database_path: Path):
    store = ExistingSchemaUpgradeStateStore(database_path)
    operation = None
    service = RepositoryUpgradeService(
        repository=GitRepository(root), store=store, remote="origin", branch="main"
    )
    try:
        operation = load_persisted_operation(store, operation_id)
        message = deploy(
            root,
            database_path,
            target_commit=operation.target_commit,
            operation_id=operation_id,
        )
        installed = service.repository.resolve_ref("HEAD")
        if installed != operation.target_commit:
            raise RuntimeError("生产版本已改变，请重新检查更新")
        progress = json.loads(
            store.get_service_state(UPGRADE_OPERATION_STATE_KEY) or "{}"
        )
        if (
            progress.get("operation_id") == operation_id
            and progress.get("status") != "succeeded"
        ):
            record_result(store, operation, "succeeded", installed_commit=installed)
        return message
    except BaseException as exc:
        progress = json.loads(
            store.get_service_state(UPGRADE_OPERATION_STATE_KEY) or "{}"
        )
        if operation is not None and progress.get("status") not in (
            "rolled_back",
            "needs_manual",
        ):
            record_result(
                store, operation, "failed", error=str(exc) or type(exc).__name__
            )
        raise
    finally:
        service.release_operation(operation_id)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--operation-id", required=True)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--db", type=Path, required=True)
    args = parser.parse_args()
    print(run_update(args.operation_id, args.repo, args.db), flush=True)


if __name__ == "__main__":
    main()
