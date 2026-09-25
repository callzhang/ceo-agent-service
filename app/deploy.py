"""Deploy the latest pushed ``main`` to the production checkout.

Derek, 2026-09-25: the service runs from its own checkout (``~/Services`` by
default, ``CEO_SERVICE_ROOT`` to override) that nobody edits, so any session
may deploy: what goes live is always a complete, pushed commit. This runs the
repository updater the console's upgrade uses: wait until no work is in flight,
back up the database, fast-forward to ``origin/main``, build the console,
check the imports, restart and wait for health, rolling back on failure.

Usage: ``python -m app.deploy`` from any checkout. Two sessions deploying at
once are serialized by the repository lock; the second finds the checkout
already moved and stops.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from uuid import uuid4

from app.config import service_root, worker_db_path
from app.repository_updater import (
    RepositoryUpdater,
    UpgradePreconditionError,
    UpgradeOperation,
    build_frontend,
    verify_imports,
    wait_for_health,
    wait_until_quiet,
)
from app.repository_upgrade import GitRepository

REMOTE = "origin"
BRANCH = "main"


def deploy(root: Path, database_path: Path) -> str:
    from app.store import AutoReplyStore

    repository = GitRepository(root)
    repository.fetch(REMOTE)
    current = repository.resolve_ref(f"refs/heads/{BRANCH}")
    target = repository.resolve_ref(f"refs/remotes/{REMOTE}/{BRANCH}")
    if current == target:
        return f"already current at {current[:8]}"
    records = repository.status_records()
    if records:
        # Nobody edits the production checkout; a change there is the fault to
        # look at, not something to preserve and deploy over.
        raise SystemExit(f"{root} has local changes; nothing was deployed")
    if not repository.is_ancestor(current, target):
        raise SystemExit(f"{root} is not behind origin/{BRANCH}; nothing was deployed")
    operation = UpgradeOperation(
        operation_id=f"deploy-{uuid4()}",
        expected_fingerprint=repository.fingerprint(BRANCH, current, target, records),
        original_commit=current,
        target_commit=target,
    )
    changed = repository._run(
        ["diff", "--name-only", current, target], category="deploy_changed_paths"
    ).stdout.decode().split()
    updater = RepositoryUpdater(
        root,
        AutoReplyStore(database_path),
        remote=REMOTE,
        branch=BRANCH,
        database_path=database_path,
        wait_for_quiet=lambda: wait_until_quiet(database_path),
        dependency_sync=lambda: build_frontend(root, changed),
        verification=lambda: verify_imports(root),
        health=wait_for_health,
    )
    try:
        result = updater.execute(operation)
    except UpgradePreconditionError as exc:
        moved = repository.resolve_ref(f"refs/heads/{BRANCH}")
        if moved != current:
            # Another session deployed while this one waited for the lock.
            return f"another deploy got there first; production is at {moved[:8]}"
        raise SystemExit(f"nothing was deployed: {exc}") from None
    return f"deployed {current[:8]} -> {result.installed_commit[:8]}"


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m app.deploy")
    parser.add_argument("--root", type=Path, default=None, help="production checkout (default: CEO_SERVICE_ROOT)")
    parser.add_argument("--db", type=Path, default=None, help="service database (default: CEO_WORKER_DB)")
    args = parser.parse_args()
    print(deploy(args.root or service_root(), args.db or worker_db_path()), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
