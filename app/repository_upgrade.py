from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
import hashlib
import os
from pathlib import Path
import re
import subprocess
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field


REPOSITORY_UPGRADE_STATE_KEY = "repository_upgrade_state:v1"
LOCK_FILENAME = "ceo-agent-upgrade.lock"
MAX_ERROR_LENGTH = 500
MAX_RELEASE_COMMITS = 20
MAX_RELEASE_SUBJECT_LENGTH = 200


class UpgradeStatus(StrEnum):
    IDLE = "idle"
    CHECKING = "checking"
    CURRENT = "current"
    UPDATE_AVAILABLE = "update_available"
    LOCAL_CHANGES = "local_changes"
    DIVERGED = "diverged"
    CHECK_FAILED = "check_failed"


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
    error: str | None = None


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
    def __init__(self, args: list[str], returncode: int, stderr: str) -> None:
        self.git_args = args
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(
            f"git {' '.join(args)} failed with exit code {returncode}: {stderr.strip()}"
        )


@dataclass(frozen=True)
class GitStatusRecord:
    code: str
    path: str
    original_path: str | None
    raw: bytes


class GitRepository:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def _run_bytes(self, args: list[str]) -> bytes:
        try:
            result = subprocess.run(
                ["git", *args],
                cwd=self.root,
                check=False,
                capture_output=True,
            )
        except OSError as exc:
            raise GitCommandError(args, -1, str(exc)) from exc
        if result.returncode != 0:
            raise GitCommandError(
                args,
                result.returncode,
                result.stderr.decode("utf-8", errors="replace"),
            )
        return result.stdout

    def _run_text(self, args: list[str]) -> str:
        return self._run_bytes(args).decode("utf-8", errors="surrogateescape").strip()

    def fetch(self, remote: str) -> None:
        self._run_bytes(["fetch", "--prune", remote])

    def resolve_ref(self, ref: str) -> str:
        return self._run_text(["rev-parse", "--verify", ref])

    def current_branch(self) -> str:
        return self._run_text(["rev-parse", "--abbrev-ref", "HEAD"])

    def is_ancestor(self, ancestor: str, descendant: str) -> bool:
        try:
            result = subprocess.run(
                ["git", "merge-base", "--is-ancestor", ancestor, descendant],
                cwd=self.root,
                check=False,
                capture_output=True,
            )
        except OSError as exc:
            raise GitCommandError(["merge-base", "--is-ancestor"], -1, str(exc)) from exc
        if result.returncode == 0:
            return True
        if result.returncode == 1:
            return False
        raise GitCommandError(
            ["merge-base", "--is-ancestor", ancestor, descendant],
            result.returncode,
            result.stderr.decode("utf-8", errors="replace"),
        )

    def commits_behind(self, local_ref: str, remote_ref: str) -> int:
        return int(self._run_text(["rev-list", "--count", f"{local_ref}..{remote_ref}"]))

    def release_summary(self, local_ref: str, remote_ref: str) -> list[str]:
        output = self._run_bytes(
            [
                "log",
                f"--max-count={MAX_RELEASE_COMMITS}",
                "--format=%s",
                f"{local_ref}..{remote_ref}",
            ]
        )
        subjects = output.splitlines()
        return [
            subject.decode("utf-8", errors="replace")[:MAX_RELEASE_SUBJECT_LENGTH]
            for subject in subjects
            if subject
        ]

    def _status_bytes(self) -> bytes:
        return self._run_bytes(
            ["status", "--porcelain=v1", "-z", "--untracked-files=all"]
        )

    def status_records(self) -> list[GitStatusRecord]:
        fields = self._status_bytes().split(b"\x00")
        records: list[GitStatusRecord] = []
        index = 0
        while index < len(fields) and fields[index]:
            field = fields[index]
            if len(field) < 4 or field[2:3] != b" ":
                raise ValueError("malformed git porcelain status record")
            code = field[:2].decode("ascii")
            path_bytes = field[3:]
            raw = field + b"\x00"
            original_path: str | None = None
            if "R" in code or "C" in code:
                index += 1
                if index >= len(fields) or not fields[index]:
                    raise ValueError("incomplete git porcelain rename record")
                original_bytes = fields[index]
                raw += original_bytes + b"\x00"
                original_path = original_bytes.decode("utf-8", errors="surrogateescape")
            records.append(
                GitStatusRecord(
                    code=code,
                    path=path_bytes.decode("utf-8", errors="surrogateescape"),
                    original_path=original_path,
                    raw=raw,
                )
            )
            index += 1
        return records

    @staticmethod
    def dirty_paths(records: list[GitStatusRecord]) -> list[str]:
        paths = {
            path
            for record in records
            for path in (record.path, record.original_path)
            if path is not None
        }
        return sorted(paths)

    def visible_paths(self) -> list[str]:
        output = self._run_bytes(
            ["ls-files", "-z", "--cached", "--others", "--exclude-standard"]
        )
        return sorted(
            path.decode("utf-8", errors="surrogateescape")
            for path in output.split(b"\x00")
            if path
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
            self.current_branch().encode("utf-8", errors="surrogateescape"),
            branch.encode("utf-8", errors="surrogateescape"),
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
    def git_dir(self) -> Path:
        value = Path(self._run_text(["rev-parse", "--git-dir"]))
        return value if value.is_absolute() else (self.root / value).resolve()

    def remote_url(self, remote: str) -> str:
        return self._run_text(["remote", "get-url", remote])


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
        self._save_snapshot(
            RepositorySnapshot(status=UpgradeStatus.CHECKING, checked_at=checked_at)
        )
        try:
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
        except (GitCommandError, OSError, ValueError) as exc:
            snapshot = RepositorySnapshot(
                status=UpgradeStatus.CHECK_FAILED,
                checked_at=checked_at,
                error=self._redacted_error(exc),
            )
        self._save_snapshot(snapshot)
        return snapshot

    def load_state(self) -> RepositoryUpgradeState:
        raw = self.store.get_service_state(REPOSITORY_UPGRADE_STATE_KEY)
        if raw is None:
            return RepositoryUpgradeState()
        return RepositoryUpgradeState.model_validate_json(raw)

    def save_state(self, state: RepositoryUpgradeState) -> None:
        self.store.set_service_state(
            REPOSITORY_UPGRADE_STATE_KEY,
            state.model_dump_json(),
        )

    def reserve_operation(
        self,
        operation_id: str,
        fingerprint: str,
    ) -> OperationReservation:
        if not operation_id:
            raise ValueError("operation_id must not be empty")
        if not fingerprint:
            raise ValueError("fingerprint must not be empty")
        lock_path = self.repository.git_dir / LOCK_FILENAME
        operation = RepositoryUpgradeOperation(
            operation_id=operation_id,
            fingerprint=fingerprint,
            reserved_at=datetime.now(timezone.utc),
        )
        payload = operation.model_dump_json().encode("utf-8")
        try:
            descriptor = os.open(
                lock_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
        except FileExistsError:
            existing = self._read_lock(lock_path)
            if existing.operation_id != operation_id:
                raise RepositoryUpgradeConflict(
                    f"repository upgrade operation {existing.operation_id!r} is already reserved"
                )
            self._persist_operation(existing)
            return OperationReservation(
                acquired=True,
                idempotent=True,
                operation=existing,
            )
        try:
            with os.fdopen(descriptor, "wb") as lock_file:
                lock_file.write(payload)
                lock_file.flush()
                os.fsync(lock_file.fileno())
            self._persist_operation(operation)
        except Exception:
            lock_path.unlink(missing_ok=True)
            raise
        return OperationReservation(
            acquired=True,
            idempotent=False,
            operation=operation,
        )

    def _read_lock(self, lock_path: Path) -> RepositoryUpgradeOperation:
        try:
            return RepositoryUpgradeOperation.model_validate_json(
                lock_path.read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as exc:
            raise RepositoryUpgradeConflict(
                f"repository upgrade lock at {lock_path} is unreadable or malformed"
            ) from exc

    def _persist_operation(self, operation: RepositoryUpgradeOperation) -> None:
        state = self.load_state()
        self.save_state(state.model_copy(update={"operation": operation}))

    def _save_snapshot(self, snapshot: RepositorySnapshot) -> None:
        state = self.load_state()
        self.save_state(state.model_copy(update={"snapshot": snapshot}))

    def _redacted_error(self, exc: BaseException) -> str:
        message = str(exc)
        sensitive_values = [str(self.repository.root), str(Path.home())]
        try:
            sensitive_values.append(self.repository.remote_url(self.remote))
        except GitCommandError:
            pass
        for value in sorted(set(sensitive_values), key=len, reverse=True):
            if value:
                message = message.replace(value, "<redacted>")
        message = re.sub(r"(?i)(https?://)[^/@\s]+@", r"\1<redacted>@", message)
        return message[:MAX_ERROR_LENGTH]
