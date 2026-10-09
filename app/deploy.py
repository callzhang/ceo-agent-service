"""Deploy the latest pushed ``main`` to the production checkout.

Derek, 2026-09-25: the service runs from its own checkout (``~/Services`` by
default, ``CEO_SERVICE_ROOT`` to override) that nobody edits, so any session
may deploy: what goes live is always a complete, pushed commit. This runs the
repository updater the console's upgrade uses: wait until no work is in flight,
back up the database, fast-forward to ``origin/main``, build the console,
check the imports, restart and wait for health, rolling back on failure.

Usage: ``python -m app.deploy`` from any checkout. An explicitly requested
``--skip-quiet-wait`` bypasses only the in-flight-work wait; the service is
still stopped through the normal updater before backup or checkout changes.
Two sessions deploying at
once are serialized by the repository lock; the second finds the checkout
already moved and stops.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import plistlib
import stat
from uuid import uuid4

from app.config import PRODUCTION_CHECKOUT_MESSAGE, read_env_file, service_root, worker_db_path
from app.consumer_system_release import prepare_consumer_system_contracts
from app.prompt_template_release import prepare_prompt_templates
from app.deploy_maintenance import check_maintenance, prepare_maintenance, stop_for_maintenance
from app.repository_updater import (
    ExistingSchemaUpgradeStateStore,
    RepositoryUpdater,
    UpgradePreconditionError,
    UpgradeOperation,
    _default_start,
    _default_restart,
    _default_stop,
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


#: Source trees nobody should hand-edit between deploys (Derek 2026-09-28).
#: The guard hooks above stop a commit, and the "has local changes" check in
#: `deploy` below stops a stray edit from ever going live, but neither one
#: stops the edit from being *made* -- this catches that at write time. Kept
#: narrow on purpose: `data/`, `.env` and build output outside these trees
#: (`app/static/workbench`, `frontend/dist`, `frontend/node_modules`) are
#: genuinely written at runtime or by the build step below and must stay
#: writable. `app/static/workbench` itself sits inside `app/` and is
#: deliberately swept into the lock too -- nothing writes there except the
#: build step, which always runs inside the unlocked window.
PROTECTED_SOURCE_DIRS = ("app", "frontend/src", "tests")


def _chmod_tree(path: Path, *, writable: bool) -> None:
    if not path.exists():
        return
    for entry in path.rglob("*"):
        try:
            if entry.is_symlink():
                continue
            current = entry.stat().st_mode
            if writable:
                entry.chmod(current | stat.S_IWUSR)
            else:
                entry.chmod(current & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
        except OSError:
            # A file mid-write, or gone by the time we reach it, must not
            # abort a deploy; the next lock/unlock pass corrects it.
            continue
    top_mode = path.stat().st_mode
    if writable:
        path.chmod(top_mode | stat.S_IWUSR)
    else:
        path.chmod(top_mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))


def lock_source_tree(root: Path) -> None:
    for relative in PROTECTED_SOURCE_DIRS:
        _chmod_tree(root / relative, writable=False)


def unlock_source_tree(root: Path) -> None:
    for relative in PROTECTED_SOURCE_DIRS:
        _chmod_tree(root / relative, writable=True)


def _production_audit_rules_path(root: Path) -> Path:
    """Resolve the worker's Audit rules path from its own environment."""
    configured = read_env_file(root / ".env").get("CEO_AUDIT_RULES_TEMPLATE_PATH", "")
    plist = Path.home() / "Library/LaunchAgents/com.ceo-agent-service.main.plist"
    if plist.exists():
        launch_environment = plistlib.loads(plist.read_bytes()).get("EnvironmentVariables", {})
        configured = launch_environment.get("CEO_AUDIT_RULES_TEMPLATE_PATH", configured)
    path = Path(os.path.expandvars(configured)).expanduser() if configured else root / "data/prompts/audit_rules.md"
    return path if path.is_absolute() else root / path


def _production_prompt_template_paths(root: Path) -> tuple[Path, Path]:
    """Resolve Developer/User files using the installed worker environment."""
    environment = read_env_file(root / ".env")
    plist = Path.home() / "Library/LaunchAgents/com.ceo-agent-service.main.plist"
    if plist.exists():
        environment.update(plistlib.loads(plist.read_bytes()).get("EnvironmentVariables", {}))
    paths = []
    for name in ("developer_prompt", "user_prompt"):
        configured = environment.get(f"CEO_{name.upper()}_TEMPLATE_PATH", "")
        path = Path(os.path.expandvars(configured)).expanduser() if configured else root / f"data/prompts/{name}.md"
        paths.append(path if path.is_absolute() else root / path)
    return paths[0], paths[1]


def deploy(
    root: Path, database_path: Path, *, publish_contracts: bool = False,
    publish_prompt_templates: bool = False,
    maintenance_tasks: tuple[int, ...] = (),
    skip_quiet_wait: bool = False,
) -> str:
    if publish_contracts and publish_prompt_templates:
        raise SystemExit("Consumer/Audit contracts and prompt templates require separate deployments")
    ensure_production_guards(root)
    repository = GitRepository(root)
    repository.fetch(REMOTE)
    current = repository.resolve_ref(f"refs/heads/{BRANCH}")
    target = repository.resolve_ref(f"refs/remotes/{REMOTE}/{BRANCH}")
    if current == target and not publish_contracts and not publish_prompt_templates:
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
    stopped = False

    def stop_once() -> None:
        nonlocal stopped
        if not stopped:
            _default_stop()
            stopped = True

    def start() -> None:
        nonlocal stopped
        _default_start()
        stopped = False

    def quiet() -> None:
        if skip_quiet_wait:
            return
        if maintenance_tasks:
            processes = check_maintenance(database_path, maintenance_tasks)
            processes = stop_for_maintenance(processes, stop_once)
            try:
                prepare_maintenance(database_path, maintenance_tasks, operation.operation_id, processes)
                wait_until_quiet(database_path)
            except Exception:
                start()
                raise
        else:
            wait_until_quiet(database_path)

    publication = None
    if publish_contracts:
        def publication():
            return prepare_consumer_system_contracts(
                root=root,
                database_path=database_path,
                operation_id=operation.operation_id,
                audit_rules_path=_production_audit_rules_path(root),
            )
    elif publish_prompt_templates:
        def publication():
            developer_path, user_path = _production_prompt_template_paths(root)
            return prepare_prompt_templates(
                root=root,
                database_path=database_path,
                operation_id=operation.operation_id,
                developer_prompt_path=developer_path,
                user_prompt_path=user_path,
            )

    updater = RepositoryUpdater(
        root,
        ExistingSchemaUpgradeStateStore(database_path),
        remote=REMOTE,
        branch=BRANCH,
        database_path=database_path,
        wait_for_quiet=quiet,
        stop=stop_once,
        restart=start,
        dependency_sync=lambda: build_frontend(root, changed),
        verification=lambda: verify_imports(root),
        health=wait_for_health,
        publication=publication,
    )
    # Unlocked for exactly the checkout + build + verify window; the source
    # tree is locked again in `finally` whether this succeeds, rolls back, or
    # raises, so a deploy that dies mid-way never leaves it writable.
    unlock_source_tree(root)
    try:
        result = updater.execute(operation)
    except UpgradePreconditionError as exc:
        moved = repository.resolve_ref(f"refs/heads/{BRANCH}")
        if moved != current:
            # Another session deployed while this one waited for the lock.
            return f"another deploy got there first; production is at {moved[:8]}"
        raise SystemExit(f"nothing was deployed: {exc}") from None
    finally:
        lock_source_tree(root)
    message = f"deployed {current[:8]} -> {result.installed_commit[:8]}"
    return f"{message}; {result.error}" if result.error else message


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
    parser.add_argument(
        "--skip-quiet-wait",
        action="store_true",
        help="deploy immediately without waiting for active work to drain; the updater still stops the service before backup and checkout changes",
    )
    parser.add_argument(
        "--publish-consumer-system-contracts",
        action="store_true",
        help="publish the reviewed Consumer/Audit Skill and Audit-rule files in the quiet deploy window",
    )
    parser.add_argument(
        "--publish-prompt-templates",
        action="store_true",
        help="migrate the approved Developer/User default files in the stopped deploy window",
    )
    parser.add_argument("--maintenance-task", type=int, action="append", default=[],
                        help="explicitly authorized recoverable reply task interrupted for maintenance; repeat per task")
    args = parser.parse_args()
    if args.restart and args.maintenance_task:
        parser.error("maintenance requires a commit deployment")
    if args.restart and args.publish_consumer_system_contracts:
        parser.error("--restart cannot publish Consumer/Audit contracts")
    if args.restart and args.publish_prompt_templates:
        parser.error("--restart cannot publish prompt templates")
    if args.publish_consumer_system_contracts and args.publish_prompt_templates:
        parser.error("Consumer/Audit contracts and prompt templates require separate deployments")
    if args.restart:
        print(restart_only(args.db or worker_db_path()), flush=True)
        return 0
    print(deploy(
        args.root or service_root(), args.db or worker_db_path(),
        publish_contracts=args.publish_consumer_system_contracts,
        publish_prompt_templates=args.publish_prompt_templates,
        maintenance_tasks=tuple(args.maintenance_task),
        skip_quiet_wait=args.skip_quiet_wait,
    ), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
