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
import stat
from uuid import uuid4

from app.config import PRODUCTION_CHECKOUT_MESSAGE, service_root, worker_db_path
from app.repository_updater import (
    RepositoryUpdater,
    UpgradePreconditionError,
    UpgradeOperation,
    _default_restart,
    build_frontend,
    verify_imports,
    wait_for_health,
    wait_until_quiet,
)
from app.repository_upgrade import GitRepository

REMOTE = "origin"
BRANCH = "main"


#: Hooks that refuse a commit, merge commit or rebase in the production
#: checkout (Derek 2026-09-25, after a test sync was committed there and every
#: deploy stopped). A deploy only fast-forwards, which runs none of them.
GUARD_HOOKS = ("pre-commit", "pre-merge-commit", "pre-rebase")


def guard_hook_script(root: Path) -> str:
    message = PRODUCTION_CHECKOUT_MESSAGE.format(root=root)
    return f"#!/bin/sh\n# ceo-agent-service production checkout guard\necho '{message}' >&2\nexit 1\n"


def ensure_production_guards(root: Path) -> None:
    """Install the refusing hooks; each deploy puts back one that went missing."""
    hooks = Path(
        GitRepository(root)._run(["rev-parse", "--git-path", "hooks"], category="deploy_hooks_path")
        .stdout.decode().strip()
    )
    if not hooks.is_absolute():
        hooks = root / hooks
    hooks.mkdir(parents=True, exist_ok=True)
    script = guard_hook_script(root)
    for name in GUARD_HOOKS:
        hook = hooks / name
        if not hook.exists() or hook.read_text(encoding="utf-8") != script:
            hook.write_text(script, encoding="utf-8")
        hook.chmod(hook.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def deploy(root: Path, database_path: Path) -> str:
    from app.store import AutoReplyStore

    ensure_production_guards(root)
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
        # A commit made in the production checkout itself (2026-09-25: a test
        # sync committed there) leaves it diverged, and every later deploy
        # would stop here. Name the stray commits so they can be moved to
        # main; never reset them away automatically.
        stray = repository._run(
            ["log", "--format=%h %s", f"refs/remotes/{REMOTE}/{BRANCH}..refs/heads/{BRANCH}"],
            category="deploy_stray_commits",
        ).stdout.decode().strip()
        raise SystemExit(
            f"{root} has diverged from origin/{BRANCH}; nothing was deployed. "
            f"Commits only in production (move them to main, then reset production to origin/{BRANCH}):\n{stray}"
        )
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


def restart_only(database_path: Path, *, restart=_default_restart) -> str:
    """Restart the service on its current code, when only its settings changed.

    Some settings (an email account's, for one) are read when a worker starts,
    so a settings change needs a restart with no commit to deploy. Same rules
    as a deploy: wait until no work is in flight, restart through launchd,
    wait for health.
    """
    wait_until_quiet(database_path)
    restart()
    if not wait_for_health():
        raise SystemExit("restarted, but the service did not become healthy")
    return "restarted"


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m app.deploy")
    parser.add_argument("--root", type=Path, default=None, help="production checkout (default: CEO_SERVICE_ROOT)")
    parser.add_argument("--db", type=Path, default=None, help="service database (default: CEO_WORKER_DB)")
    parser.add_argument(
        "--restart",
        action="store_true",
        help="restart on the current code (a setting changed), instead of deploying a commit",
    )
    args = parser.parse_args()
    if args.restart:
        print(restart_only(args.db or worker_db_path()), flush=True)
        return 0
    print(deploy(args.root or service_root(), args.db or worker_db_path()), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
