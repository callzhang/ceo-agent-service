from __future__ import annotations

from app.deployment_drain import QUIET_WAIT_SECONDS, deployment_drain
from app.task20_definitions import task20_projection
from app.state_publication_sources import publication_dispatcher_source
from app.business_legacy_field_schema_release import TARGET_STORE_VERSION, PREVIOUS_STORE_VERSIONS
from app.scheduled_config_storage import publication_scheduled_source

from contextlib import closing, nullcontext
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from typing import Callable, Protocol
from urllib.request import urlopen

from app.database_backup import (
    _backup_directory_lock,
    backup_is_complete,
    create_database_backup,
    prune_database_backups,
)
from app.repository_upgrade import GitRepository
from app.run_schema_release import publication_run_sources
from app.task_schema_release import publication_meeting_sources, publication_task_sources


UPGRADE_OPERATION_STATE_KEY = "repository_upgrade_operation:v1"
UPGRADE_STATE_WRITE_RETRY_ATTEMPTS = 3
UPGRADE_STATE_WRITE_RETRY_DELAY_SECONDS = 0.25
DEPLOY_BACKUP_MAX_AGE = timedelta(hours=24)
LAUNCHD_STOP_TIMEOUT_SECONDS = 10.0
LAUNCHD_STOP_POLL_SECONDS = 0.1


class UpgradePreconditionError(RuntimeError):
    """The repository changed or is not safe for an automatic fast-forward."""


class UpgradeFailed(RuntimeError):
    """The update was installed but verification or restart failed."""


class UpgradeStateStore(Protocol):
    def get_service_state(self, key: str) -> str | None: ...

    def set_service_state(self, key: str, value: str) -> None: ...


class ExistingSchemaUpgradeStateStore:
    """Persist deploy progress using the release's explicitly selected schema.

    This DAO never constructs an application store or migrates the database.
    It selects the committed release role at construction and before updater
    progress writes; explicit getters and setters never reselect a backend.
    """

    def __init__(self, database_path: Path) -> None:
        self.database_path = Path(database_path)
        self.bind_schema_source()
        self.bind_committed_schema()

    def bind_committed_schema(self) -> None:
        """Select the progress backend from one committed, read-only snapshot."""
        from app.action_email_schema_release import SOURCE_EMAIL_VERSION
        from app.email_metadata_schema_release import (
            SOURCE_EMAIL_VERSION as PREVIOUS_EMAIL_VERSION, TARGET_EMAIL_VERSION,
        )
        from app.business_legacy_field_schema_release import TARGET_STORE_VERSION, PREVIOUS_STORE_VERSIONS
        from app.state_definitions import state_projection

        if not self.database_path.exists():
            self.bind_schema_source()
            return
        with closing(sqlite3.connect(
            self.database_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=30
        )) as db:
            db.execute("begin")
            ledger = db.execute(
                "select 1 from main.sqlite_schema where type='table' "
                "and name='email_schema_migrations' collate nocase"
            ).fetchone()
            version = db.execute(
                "select max(version) from main.email_schema_migrations"
            ).fetchone()[0] if ledger else None
            if version is not None:
                if type(version) is not int or not (
                    0 < version <= SOURCE_EMAIL_VERSION or version in (PREVIOUS_EMAIL_VERSION, TARGET_EMAIL_VERSION)
                ):
                    raise ValueError("unsupported committed Email schema role")
                current = version in (PREVIOUS_EMAIL_VERSION, TARGET_EMAIL_VERSION)
            elif db.execute(
                "select 1 from main.sqlite_schema where name='state' collate nocase"
            ).fetchone():
                marker = db.execute(
                    f"select value from ({state_projection('service_state')}) "
                    "where key='store_schema_version'"
                ).fetchone()
                if marker is None or marker[0] not in (*PREVIOUS_STORE_VERSIONS, TARGET_STORE_VERSION):
                    raise ValueError("unsupported committed Store-only State schema role")
                current = True
            else:
                current = False
        if current:
            self.bind_schema_current()
        else:
            self.bind_schema_source()

    def bind_schema_source(self) -> None:
        self._state_schema_current = False

    def bind_schema_current(self) -> None:
        self._state_schema_current = True

    def get_service_state(self, key: str) -> str | None:
        from app.state_definitions import state_projection

        projection = ("(" + state_projection("service_state") + ")"
                      if self._state_schema_current else "service_state")
        with sqlite3.connect(
            f"file:{self.database_path}?mode=rw", uri=True, timeout=30
        ) as db:
            row = db.execute(
                f"select value from {projection} where key=?", (key,)
            ).fetchone()
            return None if row is None else str(row[0])

    def set_service_state(self, key: str, value: str) -> None:
        from app.state_storage import state_json_field, upsert_state

        for attempt in range(UPGRADE_STATE_WRITE_RETRY_ATTEMPTS):
            try:
                with sqlite3.connect(
                    f"file:{self.database_path}?mode=rw", uri=True, timeout=30
                ) as db:
                    db.execute("begin immediate")
                    if self._state_schema_current:
                        upsert_state(
                            db, "service_state", {"key": key, "value": value},
                            {"value": state_json_field("value"),
                             "updated_at": "json_quote(current_timestamp)"},
                        )
                    else:
                        db.execute(
                            """insert into service_state (key, value, updated_at)
                               values (?, ?, current_timestamp)
                               on conflict(key) do update set
                                 value=excluded.value, updated_at=current_timestamp""",
                            (key, value),
                        )
                return
            except sqlite3.OperationalError as exc:
                message = str(exc).casefold()
                is_lock_error = "locked" in message or "busy" in message
                if (
                    not is_lock_error
                    or attempt + 1 >= UPGRADE_STATE_WRITE_RETRY_ATTEMPTS
                ):
                    raise
                time.sleep(UPGRADE_STATE_WRITE_RETRY_DELAY_SECONDS * (attempt + 1))


class UpgradePublication(Protocol):
    """External release assets changed while the service is stopped."""

    def publish(self) -> None: ...

    def verify_loaded(self) -> None: ...

    def rollback(self) -> None: ...

    def finalize(self) -> None: ...


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
    target = f"gui/{_uid()}/com.ceo-agent-service.main"
    result = subprocess.run(
        ["launchctl", "kickstart", "-k", target],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        state = subprocess.run(
            ["launchctl", "print", target],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if state.returncode != 0 and "could not find service" in (
            state.stdout + state.stderr
        ).lower():
            # A failed deployment can leave the service booted out before a
            # replacement was installed. In that case kickstart cannot find
            # the label; bootstrap the existing production plist instead.
            _default_start()
            return
        raise UpgradeFailed("launchd restart failed")


def _default_stop() -> None:
    target = f"gui/{_uid()}/com.ceo-agent-service.main"
    known_processes = _launchd_service_processes(target)
    result = subprocess.run(
        ["launchctl", "bootout", target],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        if not _launchd_service_is_absent(target):
            raise UpgradeFailed("launchd stop failed")
    deadline = time.monotonic() + LAUNCHD_STOP_TIMEOUT_SECONDS
    while True:
        if _launchd_service_is_absent(target) and _known_processes_exited(known_processes):
            return
        if time.monotonic() >= deadline:
            raise UpgradeFailed("launchd service processes did not finish stopping")
        time.sleep(LAUNCHD_STOP_POLL_SECONDS)


def _launchd_service_processes(target: str) -> tuple[int, ...]:
    """Capture the supervisor and its direct children before bootout reparents them."""
    state = subprocess.run(
        ["launchctl", "print", target],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if state.returncode != 0:
        return ()
    pid = next(
        (
            int(value)
            for line in state.stdout.splitlines()
            if (value := line.strip().removeprefix("pid = ")).isdigit()
            and line.strip().startswith("pid = ")
        ),
        None,
    )
    if pid is None:
        return ()
    processes = subprocess.run(
        ["ps", "-axo", "pid=,ppid="],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if processes.returncode != 0:
        raise UpgradeFailed("could not list launchd service children before stop")
    children = {
        int(columns[0])
        for line in processes.stdout.splitlines()
        if len(columns := line.split()) == 2
        and all(column.isdigit() for column in columns)
        and int(columns[1]) == pid
    }
    return tuple(sorted({pid, *children}))


def _known_processes_exited(process_ids: tuple[int, ...]) -> bool:
    if not process_ids:
        return True
    state = subprocess.run(
        ["ps", "-p", ",".join(map(str, process_ids)), "-o", "pid="],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if state.returncode not in (0, 1) or state.stderr.strip():
        raise UpgradeFailed("could not verify launchd service process exit")
    return not state.stdout.strip()


def _launchd_service_is_absent(target: str) -> bool:
    state = subprocess.run(
        ["launchctl", "print", target],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return state.returncode != 0 and "could not find service" in (
        state.stdout + state.stderr
    ).lower()


def _default_start() -> None:
    plist = Path.home() / "Library" / "LaunchAgents" / "com.ceo-agent-service.main.plist"
    result = subprocess.run(
        ["launchctl", "bootstrap", f"gui/{_uid()}", str(plist)],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise UpgradeFailed("launchd bootstrap failed")


def _uid() -> int:
    import os

    return os.getuid()


def _default_health() -> bool:
    try:
        with urlopen("http://127.0.0.1:8765/", timeout=10) as response:
            return 200 <= response.status < 300
    except Exception:  # noqa: BLE001 - health is a boolean probe
        return False


#: A restart re-reads a multi-gigabyte database before the console listens;
#: under load that took eight minutes on 2026-09-25, so one ten-second probe
#: would roll back a healthy upgrade.
HEALTH_WAIT_SECONDS = 15 * 60
POLL_SECONDS = 5.0


def wait_for_health(
    probe: Callable[[], bool] = _default_health,
    *,
    timeout_seconds: float = HEALTH_WAIT_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> bool:
    deadline = clock() + timeout_seconds
    while True:
        if probe():
            return True
        if clock() >= deadline:
            return False
        sleep(POLL_SECONDS)


def in_flight_work(database_path: Path) -> int:
    """Count work a restart would cut off: running turns and claimed items."""
    with closing(sqlite3.connect(
        f"file:{database_path}?mode=ro", uri=True, timeout=30
    )) as db:
        agent_source, runtime_source = publication_run_sources(db)
        reply_source, work_source = publication_task_sources(db)
        scheduled_source = publication_scheduled_source(db)
        meeting_jobs, meeting_runs = publication_meeting_sources(db)
        return int(db.execute(
            f"select (select count(*) from ({runtime_source}) as runtime "
            "        where runtime.status not in ('completed','failed','superseded') "
            "        and ((runtime.lease_owner<>'' "
            "          and julianday(runtime.lease_expires_at)>julianday('now')) "
            "          or not exists ("
            f"            select 1 from ({agent_source}) as parent "
            f"            join ({reply_source}) as current on current.id=parent.reply_task_id "
            "            where parent.id=runtime.agent_run_id "
            "              and parent.execution_generation<>current.execution_generation)))"
            f" + (select count(*) from ({reply_source}) as task "
            "    where task.status='processing' and (task.channel='email' or "
            f"      exists (select 1 from ({agent_source}) as runs "
            "        where runs.reply_task_id=task.id "
            "          and runs.status in ('starting','running')) "
            f"      or exists (select 1 from ({publication_dispatcher_source(db, current_version=TARGET_STORE_VERSION, previous_versions=PREVIOUS_STORE_VERSIONS)}) as claim "
            "        where claim.adapter_name='reply' "
            "          and claim.source_id=cast(task.id as text) "
            "          and claim.claim_active "
            "          and julianday(claim.lease_expires_at)>julianday('now'))))"
            f" + (select count(*) from ({work_source}) where status='processing')"
            f" + (select count(*) from ({meeting_jobs}) as jobs "
            "    where (jobs.status='processing' and ("
            f"      exists (select 1 from ({meeting_runs}) as runs "
            "        where runs.job_id=jobs.id and runs.status='running') "
            f"      or exists (select 1 from ({publication_dispatcher_source(db, current_version=TARGET_STORE_VERSION, previous_versions=PREVIOUS_STORE_VERSIONS)}) as claim "
            "        where claim.adapter_name='meeting' "
            "          and claim.source_id=cast(jobs.id as text) "
            "          and claim.claim_active "
            "          and julianday(claim.lease_expires_at)>julianday('now')))) "
            "    or (jobs.status='ready_to_send' "
            "      and coalesce(jobs.locked_at,'')<>''))"
            f" + (select count(*) from ({publication_dispatcher_source(db, current_version=TARGET_STORE_VERSION, previous_versions=PREVIOUS_STORE_VERSIONS)})"
            "    where claim_active"
            "    and julianday(lease_expires_at)>julianday('now'))"
            f" + (select count(*) from ({task20_projection('email_agent_classification_tasks')}) "
            "    where status='running' and owner<>'' "
            "    and julianday(lease_expires_at)>julianday('now'))"
            f" + (select count(*) from ({task20_projection('workbench_turns')}) "
            "    where status='running' and lease_owner<>'' "
            "    and julianday(lease_expires_at)>julianday('now'))"
            f" + (select count(*) from ({task20_projection('weekly_okr_analysis_jobs')}) "
            "    where status='running' and lease_owner<>'' "
            "    and julianday(lease_expires_at)>julianday('now'))"
            " + (select count(*) from actions where record_type='email_actions' "
            "    and email_status='processing')"
            " + (select count(*) from actions where record_type='meeting_memory_write_events' "
            "    and td_status='processing' and td_lease_owner<>'' "
            "    and julianday(td_lease_expires_at)>julianday('now'))"
            f" + (select count(*) from ({scheduled_source}) where dispatch_status='pending'"
            "    and lease_owner<>'' and lease_expires_at>strftime('%Y-%m-%dT%H:%M:%S+00:00','now'))"
        ).fetchone()[0])


def wait_until_quiet(
    database_path: Path,
    *,
    timeout_seconds: float = QUIET_WAIT_SECONDS,
    count: Callable[[Path], int] = in_flight_work,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    """Wait for no in-flight work before the code changes; never restart over it.

    A restart kills the running Agent turn, and a supervisor child restarted
    later would load the new code beside the old ones.
    """
    deadline = clock() + timeout_seconds
    while count(database_path) > 0:
        if clock() >= deadline:
            raise UpgradePreconditionError("service did not become idle; nothing was changed")
        sleep(POLL_SECONDS)


def _built_stamp(repository_root: Path) -> Path:
    return repository_root / "app" / "static" / "workbench" / ".built-from"


def _frontend_tree(repository_root: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD:frontend"], cwd=repository_root, check=True,
        capture_output=True, text=True, timeout=60,
    ).stdout.strip()


def frontend_needs_build(repository_root: Path) -> bool:
    """The console is stale when it was not built from the checkout's current `frontend/`.

    This compares the build stamp with the tree, not with this deploy's diff: a
    deploy that stopped after advancing the checkout (or another session's deploy
    that ran in between) leaves no frontend change in the next diff, and the
    console would keep serving the old build. 2026-09-25: exactly that happened to
    the Tasks candidate button.
    """
    stamp = _built_stamp(repository_root)
    built = repository_root / "app" / "static" / "workbench" / "index.html"
    return not built.exists() or not stamp.exists() or stamp.read_text(encoding="utf-8").strip() != _frontend_tree(repository_root)


def build_frontend(repository_root: Path, changed_paths: list[str]) -> None:
    """Build the console the checkout serves; the build output is not tracked."""
    if not frontend_needs_build(repository_root):
        return
    frontend = repository_root / "frontend"
    if not (frontend / "node_modules").exists() or "frontend/package-lock.json" in changed_paths:
        subprocess.run(["npm", "ci"], cwd=frontend, check=True, timeout=900)
    subprocess.run(["npm", "run", "build"], cwd=frontend, check=True, timeout=900)
    _built_stamp(repository_root).write_text(_frontend_tree(repository_root) + "\n", encoding="utf-8")


def verify_imports(repository_root: Path) -> None:
    subprocess.run(
        [sys.executable, "-c",
         "import app.cli, app.audit_web, app.service_supervisor, app.email_worker"],
        cwd=repository_root, check=True, timeout=300,
        env={**os.environ, "PYTHONPATH": str(repository_root)},
    )


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
        stop: Callable[[], None] = lambda: None,
        health: Callable[[], bool] = _default_health,
        wait_for_quiet: Callable[[], None] = lambda: None,
        publication: Callable[[], UpgradePublication] | None = None,
        database_publication: Callable[[Path | None], UpgradePublication | None] | None = None,
        pre_checkout_cleanup: Callable[[], None] | None = None,
        pin_target_commit: bool = False,
    ) -> None:
        self.repository = GitRepository(repository_root)
        self.store = store
        self.remote = remote
        self.branch = branch
        self.database_path = database_path
        self.dependency_sync = dependency_sync or (lambda: None)
        self.verification = verification or (lambda: None)
        self.restart = restart
        self.stop = stop
        self.health = health
        self.wait_for_quiet = wait_for_quiet
        self.publication = publication
        self.database_publication = database_publication
        self.pre_checkout_cleanup = pre_checkout_cleanup
        self.pin_target_commit = pin_target_commit

    @property
    def target_ref(self) -> str:
        return f"refs/heads/{self.branch}"

    @property
    def remote_ref(self) -> str:
        return f"refs/remotes/{self.remote}/{self.branch}"

    def execute(
        self, operation: UpgradeOperation, *, already_locked: bool = False
    ) -> UpgradeResult:
        lock = nullcontext() if already_locked else self.repository.mutex()
        drain = deployment_drain(self.database_path) if self.database_path is not None else nullcontext()
        with lock, drain:
            self.repository.fetch(self.remote)
            records = self._recheck(operation)
            self.wait_for_quiet()
            self.stop()
            backup_path = None
            published: UpgradePublication | None = None
            database_release: UpgradePublication | None = None
            replacement_started = False
            try:
                backup_path = self._backup(operation)
                self._persist(operation, "updating", backup_path=backup_path)
                if self.pre_checkout_cleanup is not None:
                    self.pre_checkout_cleanup()
                    records = self.repository.status_records()
                if records:
                    self._preserve_local_changes(operation)
                merge_ref = operation.target_commit if self.pin_target_commit else self.remote_ref
                self.repository._run(
                    ["merge", "--ff-only", merge_ref],
                    category="upgrade_merge",
                )
                if self.database_publication is not None:
                    database_release = self.database_publication(backup_path)
                self._persist(operation, "verifying", backup_path=backup_path)
                self.dependency_sync()
                if database_release is not None:
                    database_release.publish()
                self.verification()
                if self.publication is not None:
                    # Hold the restoration snapshot before the first asset
                    # changes so every partial failure uses this same receipt.
                    published = self.publication()
                    published.publish()
                self._persist(operation, "restarting", backup_path=backup_path)
                replacement_started = True
                self.restart()
                if not self.health():
                    raise UpgradeFailed("replacement service health check failed")
                if database_release is not None:
                    database_release.verify_loaded()
                if published is not None:
                    published.verify_loaded()
            except Exception as exc:
                rollback_status = "failed"
                try:
                    if replacement_started:
                        self.stop()
                    if published is not None:
                        published.rollback()
                    if database_release is not None:
                        database_release.rollback()
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
                if rollback_status == "failed":
                    installed = self.repository.resolve_ref(self.target_ref)
                    if (
                        not replacement_started
                        and installed == operation.original_commit
                        and not self.repository.status_records()
                    ):
                        try:
                            self.restart()
                            if not self.health():
                                raise UpgradeFailed(
                                    "original service did not become healthy after deploy failure"
                                )
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
            finalize_error = ""
            for release in (database_release, published):
                if release is None:
                    continue
                try:
                    release.finalize()
                except Exception as exc:
                    # The replacement passed health and loaded-file readback.
                    # Receipt cleanup failure must not turn a healthy service
                    # into an apparent rollback or leave progress at restarting.
                    finalize_error = f"publication receipt finalization failed: {exc}"
            installed = self.repository.resolve_ref(self.target_ref)
            self._persist(
                operation,
                "succeeded",
                backup_path=backup_path,
                installed_commit=installed,
                error=finalize_error,
            )
            return UpgradeResult(
                operation_id=operation.operation_id,
                status="succeeded",
                installed_commit=installed,
                backup_path=str(backup_path) if backup_path else "",
                error=finalize_error,
            )

    def execute_locked(self, operation: UpgradeOperation) -> UpgradeResult:
        """Execute while the caller holds this repository's deploy mutex."""
        return self.execute(operation, already_locked=True)

    def _recheck(self, operation: UpgradeOperation) -> list[object]:
        current = self.repository.resolve_ref(self.target_ref)
        remote = self.repository.resolve_ref(self.remote_ref)
        if current != operation.original_commit:
            raise UpgradePreconditionError("repository revision changed")
        if self.pin_target_commit:
            if not self.repository.is_ancestor(operation.target_commit, remote):
                raise UpgradePreconditionError("target commit is not in origin/main")
            selected_target = operation.target_commit
        else:
            if remote != operation.target_commit:
                raise UpgradePreconditionError("repository revision changed")
            selected_target = remote
        if not self.repository.is_ancestor(current, selected_target):
            raise UpgradePreconditionError("repository target is diverged")
        records = self.repository.status_records()
        if records and not operation.branch_name.strip():
            raise UpgradePreconditionError("repository fingerprint changed")
        fingerprint = self.repository.fingerprint(
            self.branch,
            current,
            selected_target,
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
        backup_dir = self.database_path.parent / "backups"
        now = datetime.now(timezone.utc).timestamp()
        oldest_allowed = now - DEPLOY_BACKUP_MAX_AGE.total_seconds()
        with _backup_directory_lock(backup_dir):
            recent = [
                path
                for path in backup_dir.glob(f"{self.database_path.stem}-*.sqlite3")
                if oldest_allowed <= path.stat().st_mtime <= now
                and backup_is_complete(path)
            ]
            if recent:
                return max(recent, key=lambda path: path.stat().st_mtime)
        destination = (
            backup_dir
            / f"{self.database_path.stem}-before-upgrade-{operation.operation_id}.sqlite3"
        )
        backup_path = create_database_backup(self.database_path, destination)
        prune_database_backups(
            destination.parent,
            today=datetime.now(timezone.utc).date(),
            keep_path=backup_path,
        )
        return backup_path

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
        if isinstance(self.store, ExistingSchemaUpgradeStateStore):
            self.store.bind_committed_schema()
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

    from app.store_schema_release import prepare_database_schema_release

    store = ExistingSchemaUpgradeStateStore(Path(args.db))
    operation = load_persisted_operation(store, args.operation_id)
    repository_root = Path(args.repo)
    repository = GitRepository(repository_root)

    def changed_paths() -> list[str]:
        output = repository._run(
            ["diff", "--name-only", operation.original_commit, operation.target_commit],
            category="upgrade_changed_paths",
        ).stdout
        text = output.decode() if isinstance(output, bytes) else str(output)
        return [line.strip() for line in text.splitlines() if line.strip()]

    result = RepositoryUpdater(
        repository_root,
        store,
        database_path=Path(args.db),
        wait_for_quiet=lambda: wait_until_quiet(Path(args.db)),
        stop=_default_stop,
        restart=_default_start,
        database_publication=lambda backup: prepare_database_schema_release(repository_root, Path(args.db), recovery_source_path=backup),
        dependency_sync=lambda: build_frontend(repository_root, changed_paths()),
        verification=lambda: verify_imports(repository_root),
        health=lambda: wait_for_health(),
    ).execute(operation)
    print(json.dumps(result.__dict__, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
