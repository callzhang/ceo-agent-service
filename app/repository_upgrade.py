from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
import fcntl
import hashlib
import os
from pathlib import Path
import subprocess
from typing import Iterator, Literal, Protocol
from urllib.parse import quote_from_bytes

from pydantic import BaseModel, ConfigDict, Field


REPOSITORY_UPGRADE_STATE_KEY = "repository_upgrade_state:v1"
LOCK_FILENAME = "ceo-agent-upgrade.lock"
MAX_RELEASE_COMMITS = 20
MAX_RELEASE_SUBJECT_LENGTH = 200
DEFAULT_GIT_TIMEOUT_SECONDS = 30.0


class UpgradeStatus(StrEnum):
    IDLE = "idle"
    CHECKING = "checking"
    CURRENT = "current"
    UPDATE_AVAILABLE = "update_available"
    LOCAL_CHANGES = "local_changes"
    DIVERGED = "diverged"
    CHECK_FAILED = "check_failed"


class RepositoryDiagnostic(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command_category: str
    code: int | Literal["timeout", "os_error", "invalid_output"]
    reason: Literal[
        "git_command_failed",
        "git_command_timed_out",
        "git_command_unavailable",
        "git_output_invalid",
    ]


class RepositorySnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: UpgradeStatus = UpgradeStatus.IDLE
    checked_at: datetime
    local_commit: str = ""
    remote_commit: str = ""
    commits_behind: int = Field(default=0, ge=0)
    release_summary: list[str] = Field(default_factory=list)
    dirty_paths: list[str] = Field(default_factory=list)
    fingerprint: str = ""
    error: RepositoryDiagnostic | None = None


class RepositoryUpgradeOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: str
    fingerprint: str
    reserved_at: datetime


class RepositoryUpgradeState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    snapshot: RepositorySnapshot | None = None
    operation: RepositoryUpgradeOperation | None = None


class OperationReservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    acquired: bool
    idempotent: bool
    operation: RepositoryUpgradeOperation


class RepositoryUpgradeConflict(RuntimeError):
    pass


class ServiceStateStore(Protocol):
    def get_service_state(self, key: str) -> str | None: ...

    def set_service_state(self, key: str, value: str) -> None: ...


class GitCommandError(RuntimeError):
    def __init__(self, diagnostic: RepositoryDiagnostic) -> None:
        self.diagnostic = diagnostic
        super().__init__(
            f"{diagnostic.command_category}: {diagnostic.reason} ({diagnostic.code})"
        )


@dataclass(frozen=True)
class GitStatusRecord:
    code: str
    path: str
    original_path: str | None
    raw: bytes


class GitRepository:
    def __init__(
        self,
        root: Path,
        *,
        timeout_seconds: float = DEFAULT_GIT_TIMEOUT_SECONDS,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.root = root.resolve()
        self.timeout_seconds = timeout_seconds
        self._common_git_dir: Path | None = None

    def _run(
        self,
        args: list[str],
        *,
        category: str,
        accepted_returncodes: tuple[int, ...] = (0,),
    ) -> subprocess.CompletedProcess[bytes]:
        try:
            result = subprocess.run(
                ["git", *args],
                cwd=self.root,
                check=False,
                capture_output=True,
                timeout=self.timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            raise GitCommandError(
                RepositoryDiagnostic(
                    command_category=category,
                    code="timeout",
                    reason="git_command_timed_out",
                )
            ) from None
        except OSError:
            raise GitCommandError(
                RepositoryDiagnostic(
                    command_category=category,
                    code="os_error",
                    reason="git_command_unavailable",
                )
            ) from None
        if result.returncode not in accepted_returncodes:
            raise GitCommandError(
                RepositoryDiagnostic(
                    command_category=category,
                    code=result.returncode,
                    reason="git_command_failed",
                )
            )
        return result

    def _run_bytes(self, args: list[str], *, category: str) -> bytes:
        return self._run(args, category=category).stdout

    def _run_text(self, args: list[str], *, category: str) -> str:
        output = self._run_bytes(args, category=category)
        try:
            return output.decode("utf-8").strip()
        except UnicodeDecodeError:
            raise GitCommandError(
                RepositoryDiagnostic(
                    command_category=category,
                    code="invalid_output",
                    reason="git_output_invalid",
                )
            ) from None

    def fetch(self, remote: str) -> None:
        self._run(["fetch", "--prune", remote], category="fetch")

    def resolve_ref(self, ref: str) -> str:
        return self._run_text(
            ["rev-parse", "--verify", ref],
            category="resolve_ref",
        )

    def current_branch(self) -> str:
        return self._run_text(
            ["rev-parse", "--abbrev-ref", "HEAD"],
            category="current_branch",
        )

    def is_ancestor(self, ancestor: str, descendant: str) -> bool:
        result = self._run(
            ["merge-base", "--is-ancestor", ancestor, descendant],
            category="ancestry",
            accepted_returncodes=(0, 1),
        )
        return result.returncode == 0

    def commits_behind(self, local_ref: str, remote_ref: str) -> int:
        value = self._run_text(
            ["rev-list", "--count", f"{local_ref}..{remote_ref}"],
            category="commit_count",
        )
        try:
            return int(value)
        except ValueError:
            raise GitCommandError(
                RepositoryDiagnostic(
                    command_category="commit_count",
                    code="invalid_output",
                    reason="git_output_invalid",
                )
            ) from None

    def release_summary(self, local_ref: str, remote_ref: str) -> list[str]:
        output = self._run_bytes(
            [
                "log",
                f"--max-count={MAX_RELEASE_COMMITS}",
                "--format=%s",
                f"{local_ref}..{remote_ref}",
            ],
            category="release_summary",
        )
        return [
            subject.decode("utf-8", errors="replace")[:MAX_RELEASE_SUBJECT_LENGTH]
            for subject in output.splitlines()
            if subject
        ]

    def _status_bytes(self) -> bytes:
        return self._run_bytes(
            ["status", "--porcelain=v1", "-z", "--untracked-files=all"],
            category="status",
        )

    @staticmethod
    def _display_path(raw_path: bytes) -> str:
        try:
            return raw_path.decode("utf-8")
        except UnicodeDecodeError:
            return "bytes:" + quote_from_bytes(raw_path, safe="/-._~")

    def status_records(self) -> list[GitStatusRecord]:
        fields = self._status_bytes().split(b"\x00")
        records: list[GitStatusRecord] = []
        index = 0
        while index < len(fields) and fields[index]:
            field = fields[index]
            if len(field) < 4 or field[2:3] != b" ":
                raise GitCommandError(
                    RepositoryDiagnostic(
                        command_category="status",
                        code="invalid_output",
                        reason="git_output_invalid",
                    )
                )
            code = field[:2].decode("ascii")
            path_bytes = field[3:]
            raw = field + b"\x00"
            original_path: str | None = None
            if "R" in code or "C" in code:
                index += 1
                if index >= len(fields) or not fields[index]:
                    raise GitCommandError(
                        RepositoryDiagnostic(
                            command_category="status",
                            code="invalid_output",
                            reason="git_output_invalid",
                        )
                    )
                original_bytes = fields[index]
                raw += original_bytes + b"\x00"
                original_path = self._display_path(original_bytes)
            records.append(
                GitStatusRecord(
                    code=code,
                    path=self._display_path(path_bytes),
                    original_path=original_path,
                    raw=raw,
                )
            )
            index += 1
        return records

    @staticmethod
    def dirty_paths(records: list[GitStatusRecord]) -> list[str]:
        return sorted(
            {
                path
                for record in records
                for path in (record.path, record.original_path)
                if path is not None
            }
        )

    def fingerprint(
        self,
        branch: str,
        local_ref: str,
        remote_ref: str,
        records: list[GitStatusRecord] | None = None,
    ) -> str:
        status_bytes = (
            self._status_bytes()
            if records is None
            else b"".join(record.raw for record in records)
        )
        components = [
            self.current_branch().encode("utf-8"),
            branch.encode("utf-8"),
            self.resolve_ref(local_ref).encode("ascii"),
            self.resolve_ref(remote_ref).encode("ascii"),
            status_bytes,
        ]
        digest = hashlib.sha256()
        for component in components:
            digest.update(len(component).to_bytes(8, "big"))
            digest.update(component)
        return digest.hexdigest()

    @property
    def common_git_dir(self) -> Path:
        if self._common_git_dir is None:
            value = Path(
                self._run_text(
                    ["rev-parse", "--git-common-dir"],
                    category="repository_metadata",
                )
            )
            self._common_git_dir = (
                value if value.is_absolute() else (self.root / value).resolve()
            )
        return self._common_git_dir

    @property
    def lock_path(self) -> Path:
        return self.common_git_dir / LOCK_FILENAME

    @contextmanager
    def mutex(self) -> Iterator[None]:
        try:
            descriptor = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        except OSError:
            raise GitCommandError(
                RepositoryDiagnostic(
                    command_category="repository_mutex",
                    code="os_error",
                    reason="git_command_unavailable",
                )
            ) from None
        with os.fdopen(descriptor, "a+b") as lock_file:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            except OSError:
                raise GitCommandError(
                    RepositoryDiagnostic(
                        command_category="repository_mutex",
                        code="os_error",
                        reason="git_command_unavailable",
                    )
                ) from None
            yield


class RepositoryUpgradeService:
    def __init__(
        self,
        *,
        repository: GitRepository,
        store: ServiceStateStore,
        remote: str,
        branch: str,
    ) -> None:
        self.repository = repository
        self.store = store
        self.remote = remote
        self.branch = branch

    @property
    def local_ref(self) -> str:
        return f"refs/heads/{self.branch}"

    @property
    def remote_ref(self) -> str:
        return f"refs/remotes/{self.remote}/{self.branch}"

    def check(self) -> RepositorySnapshot:
        checked_at = datetime.now(timezone.utc)
        try:
            self._save_snapshot(
                RepositorySnapshot(status=UpgradeStatus.CHECKING, checked_at=checked_at)
            )
            self.repository.fetch(self.remote)
            local_commit = self.repository.resolve_ref(self.local_ref)
            remote_commit = self.repository.resolve_ref(self.remote_ref)
            records = self.repository.status_records()
            dirty_paths = self.repository.dirty_paths(records)
            commits_behind = self.repository.commits_behind(
                local_commit, remote_commit
            )
            fingerprint = self.repository.fingerprint(
                self.branch,
                local_commit,
                remote_commit,
                records,
            )
            if local_commit == remote_commit:
                status = UpgradeStatus.CURRENT
            elif self.repository.is_ancestor(local_commit, remote_commit):
                status = (
                    UpgradeStatus.LOCAL_CHANGES
                    if dirty_paths
                    else UpgradeStatus.UPDATE_AVAILABLE
                )
            else:
                status = UpgradeStatus.DIVERGED
            snapshot = RepositorySnapshot(
                status=status,
                checked_at=checked_at,
                local_commit=local_commit,
                remote_commit=remote_commit,
                commits_behind=commits_behind,
                release_summary=self.repository.release_summary(
                    local_commit, remote_commit
                ),
                dirty_paths=dirty_paths,
                fingerprint=fingerprint,
            )
        except GitCommandError as exc:
            snapshot = RepositorySnapshot(
                status=UpgradeStatus.CHECK_FAILED,
                checked_at=checked_at,
                error=exc.diagnostic,
            )
        try:
            self._save_snapshot(snapshot)
        except GitCommandError as exc:
            return RepositorySnapshot(
                status=UpgradeStatus.CHECK_FAILED,
                checked_at=checked_at,
                error=exc.diagnostic,
            )
        return snapshot

    def _load_state_unlocked(self) -> RepositoryUpgradeState:
        raw = self.store.get_service_state(REPOSITORY_UPGRADE_STATE_KEY)
        if raw is None:
            return RepositoryUpgradeState()
        return RepositoryUpgradeState.model_validate_json(raw)

    def _save_state_unlocked(self, state: RepositoryUpgradeState) -> None:
        self.store.set_service_state(
            REPOSITORY_UPGRADE_STATE_KEY,
            state.model_dump_json(),
        )

    def load_state(self) -> RepositoryUpgradeState:
        with self.repository.mutex():
            return self._load_state_unlocked()

    def save_state(self, state: RepositoryUpgradeState) -> None:
        with self.repository.mutex():
            self._save_state_unlocked(state)

    def reserve_operation(
        self,
        operation_id: str,
        fingerprint: str,
    ) -> OperationReservation:
        if not operation_id:
            raise ValueError("operation_id must not be empty")
        if not fingerprint:
            raise ValueError("fingerprint must not be empty")
        with self.repository.mutex():
            state = self._load_state_unlocked()
            existing = state.operation
            if existing is not None:
                if existing.operation_id != operation_id:
                    raise RepositoryUpgradeConflict(
                        f"repository upgrade operation {existing.operation_id!r} "
                        "is already reserved"
                    )
                if existing.fingerprint != fingerprint:
                    raise RepositoryUpgradeConflict(
                        f"repository upgrade operation {operation_id!r} has a "
                        "different fingerprint"
                    )
                return OperationReservation(
                    acquired=True,
                    idempotent=True,
                    operation=existing,
                )
            operation = RepositoryUpgradeOperation(
                operation_id=operation_id,
                fingerprint=fingerprint,
                reserved_at=datetime.now(timezone.utc),
            )
            self._save_state_unlocked(
                state.model_copy(update={"operation": operation})
            )
            return OperationReservation(
                acquired=True,
                idempotent=False,
                operation=operation,
            )

    def _save_snapshot(self, snapshot: RepositorySnapshot) -> None:
        with self.repository.mutex():
            state = self._load_state_unlocked()
            self._save_state_unlocked(state.model_copy(update={"snapshot": snapshot}))
