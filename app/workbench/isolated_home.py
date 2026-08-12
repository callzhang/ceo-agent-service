"""Crash-recoverable ownership for temporary Codex homes."""

from __future__ import annotations

import errno
import fcntl
import hashlib
import json
import os
import re
import stat
import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterator

from app.codex_history import find_codex_session_path
from app.leak_check import assert_no_credentials


_ROOT_NAME = "ceo-agent-service-workbench-codex"
_MARKER_NAME = ".owner.json"
_LOCK_NAME = ".active"
_CONFIG_NAME = "config.toml"
_MARKER_VERSION = 1
_MAX_RECONCILE_ENTRIES = 256
_SAFE_ERROR = "Codex configuration could not be isolated safely"
_SYNC_STATE_DIRECTORY = ".workbench-session-sync"
_SYNC_LOCK_NAME = ".lock"
_SYNC_JOURNAL_NAME = "journal.json"
_SYNC_JOURNAL_VERSION = 1
_MAX_SYNC_JOURNAL_BYTES = 1024 * 1024
_MAX_SYNC_JOURNAL_ENTRIES = 4096
_MAX_SYNC_FILE_BYTES = 256 * 1024 * 1024
_MAX_JOURNAL_COMPONENT_BYTES = 255
_MAX_JOURNAL_PATH_BYTES = 4096
_SHA256_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_SYNC_ARTIFACT = re.compile(
    r"^\.workbench-sync-(?:stage|backup|restore)-[0-9a-f]{32}-[0-9]{8}$"
)
_COPIED_ROOT_FILES = frozenset(
    {
        "AGENTS.md",
        "auth.json",
        "installation_id",
        "memory_connector.env",
        "memory_connector.install.json",
        "memory_connector_hook.json",
        "session_index.jsonl",
        "session_path_index.jsonl",
    }
)
_COPIED_ROOT_DIRECTORIES = frozenset(
    {".agents", "mcp-oauth-locks", "plugins", "rules", "skills", "vendor_imports"}
)


def isolated_home_root() -> Path:
    return Path(tempfile.gettempdir()).resolve() / _ROOT_NAME


@dataclass
class IsolatedCodexHome:
    path: Path
    marker_token: str
    lock_fd: int
    source_home: Path
    root: Path

    def cleanup(self, *, sync_sessions: bool = True) -> None:
        if self.lock_fd < 0:
            return
        sync_error: BaseException | None = None
        try:
            if sync_sessions and self.path.exists():
                if (
                    _validated_marker_token(self.path, self.lock_fd)
                    != self.marker_token
                ):
                    raise ValueError(_SAFE_ERROR)
                _sync_sessions(self.path, self.source_home, lock_root=self.root)
        except BaseException as exc:
            sync_error = exc
        finally:
            try:
                remove_verified_isolated_home(
                    self.path,
                    self.marker_token,
                    self.lock_fd,
                    root=self.root,
                )
            finally:
                try:
                    fcntl.flock(self.lock_fd, fcntl.LOCK_UN)
                except OSError:
                    pass
                try:
                    os.close(self.lock_fd)
                except OSError:
                    pass
                self.lock_fd = -1
        if sync_error is not None:
            raise ValueError(_SAFE_ERROR) from sync_error


def create_isolated_codex_home(
    source_home: Path,
    sanitized_config: str,
    *,
    root: Path | None = None,
    provider_session_ref: str = "",
) -> IsolatedCodexHome:
    effective_root = _ensure_private_root(root or isolated_home_root())
    reconcile_isolated_codex_homes(root=effective_root)
    source = Path(source_home).absolute()
    _require_owned_directory(source)
    home_id = uuid.uuid4().hex
    marker_token = uuid.uuid4().hex
    child = effective_root / home_id
    child.mkdir(mode=0o700)
    child.chmod(0o700)
    lock_fd = -1
    ownership: IsolatedCodexHome | None = None
    try:
        root_fd = os.open(
            effective_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        )
        try:
            child_fd = os.open(
                home_id,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=root_fd,
            )
            try:
                marker = json.dumps(
                    {
                        "version": _MARKER_VERSION,
                        "uid": os.getuid(),
                        "home_id": home_id,
                        "token": marker_token,
                    },
                    separators=(",", ":"),
                ).encode("utf-8")
                _write_new_file(child_fd, _MARKER_NAME, marker, 0o600)
                lock_fd = os.open(
                    _LOCK_NAME,
                    os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=child_fd,
                )
                os.fchmod(lock_fd, 0o600)
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                _copy_codex_state(
                    source,
                    child,
                    child_fd,
                    provider_session_ref=provider_session_ref,
                )
                _write_new_file(
                    child_fd,
                    _CONFIG_NAME,
                    sanitized_config.encode("utf-8"),
                    0o600,
                )
            finally:
                os.close(child_fd)
        finally:
            os.close(root_fd)
        ownership = IsolatedCodexHome(
            path=child,
            marker_token=marker_token,
            lock_fd=lock_fd,
            source_home=source,
            root=effective_root,
        )
        return ownership
    except BaseException as exc:
        if lock_fd >= 0:
            try:
                remove_verified_isolated_home(
                    child,
                    marker_token,
                    lock_fd,
                    root=effective_root,
                )
            finally:
                os.close(lock_fd)
        else:
            _remove_uninitialized_child(child, effective_root)
        if isinstance(exc, ValueError):
            raise
        raise ValueError(_SAFE_ERROR) from exc


def reconcile_isolated_codex_homes(*, root: Path | None = None) -> int:
    effective_root = _ensure_private_root(root or isolated_home_root())
    removed = 0
    for index, entry in enumerate(os.scandir(effective_root)):
        if index >= _MAX_RECONCILE_ENTRIES:
            break
        path = Path(entry.path)
        if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
            continue
        if not _is_canonical_home_id(entry.name):
            continue
        try:
            _require_owned_directory(path)
            lock_fd = os.open(path / _LOCK_NAME, os.O_RDWR | os.O_NOFOLLOW)
        except (OSError, ValueError):
            continue
        try:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                continue
            marker_token = _validated_marker_token(path, lock_fd)
            if marker_token and remove_verified_isolated_home(
                path,
                marker_token,
                lock_fd,
                root=effective_root,
            ):
                removed += 1
        finally:
            try:
                os.close(lock_fd)
            except OSError:
                pass
    return removed


def remove_verified_isolated_home(
    path: Path,
    marker_token: str,
    lock_fd: int,
    *,
    root: Path | None = None,
) -> bool:
    effective_root = _ensure_private_root(root or isolated_home_root())
    child = Path(path).absolute()
    if child.parent != effective_root or not _is_canonical_home_id(child.name):
        return False
    try:
        child_stat = child.lstat()
    except FileNotFoundError:
        return False
    if (
        stat.S_ISLNK(child_stat.st_mode)
        or not stat.S_ISDIR(child_stat.st_mode)
        or child_stat.st_uid != os.getuid()
        or stat.S_IMODE(child_stat.st_mode) != 0o700
    ):
        return False
    if _validated_marker_token(child, lock_fd) != marker_token:
        return False

    root_fd = os.open(effective_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        child_fd = os.open(
            child.name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=root_fd,
        )
        try:
            opened = os.fstat(child_fd)
            if (opened.st_dev, opened.st_ino) != (child_stat.st_dev, child_stat.st_ino):
                return False
            _clear_directory_fd(child_fd)
        finally:
            os.close(child_fd)
        try:
            os.rmdir(child.name, dir_fd=root_fd)
        except FileNotFoundError:
            pass
        return True
    except FileNotFoundError:
        return False
    finally:
        os.close(root_fd)


def _ensure_private_root(root: Path) -> Path:
    candidate = Path(root).absolute()
    if candidate != candidate.resolve(strict=False):
        raise ValueError(_SAFE_ERROR)
    try:
        candidate.mkdir(mode=0o700)
        candidate.chmod(0o700)
    except FileExistsError:
        pass
    _require_owned_directory(candidate)
    if stat.S_IMODE(candidate.lstat().st_mode) != 0o700:
        raise ValueError(_SAFE_ERROR)
    return candidate


def _require_owned_directory(path: Path) -> None:
    metadata = path.lstat()
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
    ):
        raise ValueError(_SAFE_ERROR)


def _copy_codex_state(
    source: Path,
    destination: Path,
    destination_fd: int,
    *,
    provider_session_ref: str,
) -> None:
    for entry in os.scandir(source):
        if entry.is_symlink():
            continue
        is_auth_file = entry.name.startswith("auth_") and entry.name.endswith(".json")
        target = destination / entry.name
        if entry.name in _COPIED_ROOT_DIRECTORIES and entry.is_dir(
            follow_symlinks=False
        ):
            target.mkdir(mode=0o700)
            target.chmod(0o700)
            _copy_tree(Path(entry.path), target, source_root=source)
        elif (
            entry.name in _COPIED_ROOT_FILES or is_auth_file
        ) and entry.is_file(follow_symlinks=False):
            _copy_regular_file(Path(entry.path), target, destination_fd)
    if provider_session_ref:
        session_path = find_codex_session_path(
            provider_session_ref,
            codex_home=source,
        )
        if session_path is None:
            raise ValueError(_SAFE_ERROR)
        _copy_provider_session(source, destination, session_path)


def _copy_tree(source: Path, destination: Path, *, source_root: Path) -> None:
    destination_fd = os.open(
        destination, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    )
    try:
        for entry in os.scandir(source):
            if entry.is_symlink():
                _materialize_safe_symlink(
                    Path(entry.path),
                    destination / entry.name,
                    destination_fd,
                    source_root=source_root,
                )
                continue
            target = destination / entry.name
            if entry.is_dir(follow_symlinks=False):
                target.mkdir(mode=0o700)
                target.chmod(0o700)
                _copy_tree(Path(entry.path), target, source_root=source_root)
            elif entry.is_file(follow_symlinks=False):
                _copy_regular_file(Path(entry.path), target, destination_fd)
    finally:
        os.close(destination_fd)


def _materialize_safe_symlink(
    source: Path,
    destination: Path,
    destination_fd: int,
    *,
    source_root: Path,
) -> None:
    try:
        resolved = source.resolve(strict=True)
        resolved.relative_to(source_root)
    except (OSError, RuntimeError, ValueError):
        return
    metadata = resolved.lstat()
    if metadata.st_uid != os.getuid():
        return
    if stat.S_ISDIR(metadata.st_mode):
        destination.mkdir(mode=0o700)
        destination.chmod(0o700)
        _copy_tree(resolved, destination, source_root=source_root)
    elif stat.S_ISREG(metadata.st_mode):
        _copy_regular_file(resolved, destination, destination_fd)


def _copy_regular_file(source: Path, destination: Path, destination_fd: int) -> None:
    source_fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        initial_metadata = os.fstat(source_fd)
        if (
            not stat.S_ISREG(initial_metadata.st_mode)
            or initial_metadata.st_uid != os.getuid()
        ):
            raise ValueError(_SAFE_ERROR)
        target_fd = os.open(
            destination.name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=destination_fd,
        )
        try:
            while True:
                chunk = os.read(source_fd, 64 * 1024)
                if not chunk:
                    break
                _write_all(target_fd, chunk)
            final_metadata = os.fstat(source_fd)
            source_identity = (
                initial_metadata.st_dev,
                initial_metadata.st_ino,
                initial_metadata.st_size,
                initial_metadata.st_mtime_ns,
                initial_metadata.st_ctime_ns,
            )
            final_identity = (
                final_metadata.st_dev,
                final_metadata.st_ino,
                final_metadata.st_size,
                final_metadata.st_mtime_ns,
                final_metadata.st_ctime_ns,
            )
            if final_identity != source_identity:
                raise ValueError(_SAFE_ERROR)
            copied_mode = (
                0o700
                if stat.S_IMODE(initial_metadata.st_mode) & 0o111
                else 0o600
            )
            os.fchmod(target_fd, copied_mode)
            os.fsync(target_fd)
        finally:
            os.close(target_fd)
        os.fsync(destination_fd)
    finally:
        os.close(source_fd)


def _copy_provider_session(source_home: Path, isolated_home: Path, session: Path) -> None:
    try:
        relative = session.absolute().relative_to(source_home)
    except ValueError as exc:
        raise ValueError(_SAFE_ERROR) from exc
    if not relative.parts or relative.parts[0] not in {"sessions", "archived_sessions"}:
        raise ValueError(_SAFE_ERROR)
    if session.is_symlink() or not session.is_file():
        raise ValueError(_SAFE_ERROR)
    destination_parent = isolated_home
    for part in relative.parts[:-1]:
        destination_parent /= part
        if destination_parent.exists():
            _require_owned_directory(destination_parent)
        else:
            destination_parent.mkdir(mode=0o700)
            destination_parent.chmod(0o700)
    destination_fd = os.open(
        destination_parent,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
    )
    try:
        _copy_regular_file(session, destination_parent / relative.name, destination_fd)
    finally:
        os.close(destination_fd)


@dataclass(frozen=True)
class _FileIdentity:
    device: int
    inode: int
    size: int
    modified_ns: int
    changed_ns: int
    mode: int


@dataclass
class _SessionSyncFile:
    relative: Path
    source: Path
    destination: Path
    source_identity: _FileIdentity
    destination_identity: _FileIdentity | None
    stage_name: str = ""
    backup_name: str = ""
    backup_identity: _FileIdentity | None = None
    stage_identity: _FileIdentity | None = None
    stage_digest: str | None = None


@dataclass
class _SessionSyncPlan:
    isolated_sessions: Path
    destination_sessions: Path
    directories: tuple[Path, ...]
    directory_identities: dict[Path, tuple[int, int] | None]
    files: tuple[_SessionSyncFile, ...]


@dataclass(frozen=True)
class _SessionSyncState:
    source_home: Path
    state_fd: int


def _sync_sessions(
    isolated_home: Path,
    source_home: Path,
    *,
    lock_root: Path,
) -> None:
    isolated_sessions = isolated_home / "sessions"
    try:
        isolated_metadata = isolated_sessions.lstat()
    except FileNotFoundError:
        return
    if (
        stat.S_ISLNK(isolated_metadata.st_mode)
        or not stat.S_ISDIR(isolated_metadata.st_mode)
        or isolated_metadata.st_uid != os.getuid()
    ):
        raise ValueError(_SAFE_ERROR)
    _require_owned_directory(source_home)
    with _session_sync_lock(source_home, lock_root) as sync_state:
        plan = _build_session_sync_plan(isolated_sessions, source_home / "sessions")
        _execute_session_sync(plan, sync_state)


@contextmanager
def _session_sync_lock(
    source_home: Path,
    lock_root: Path,
) -> Iterator[_SessionSyncState]:
    del lock_root  # Retained for compatibility with existing internal callers.
    state_fd = _open_session_sync_state(source_home)
    lock_fd = -1
    try:
        lock_fd = os.open(
            _SYNC_LOCK_NAME,
            os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
            0o600,
            dir_fd=state_fd,
        )
        lock_metadata = os.fstat(lock_fd)
        if (
            not stat.S_ISREG(lock_metadata.st_mode)
            or lock_metadata.st_uid != os.getuid()
            or stat.S_IMODE(lock_metadata.st_mode) != 0o600
        ):
            raise ValueError(_SAFE_ERROR)
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        sync_state = _SessionSyncState(source_home, state_fd)
        _remove_orphan_journal_temps(sync_state)
        _recover_session_journal(sync_state)
        yield sync_state
    finally:
        if lock_fd >= 0:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(lock_fd)
        os.close(state_fd)


def _open_session_sync_state(source_home: Path) -> int:
    _require_owned_directory(source_home)
    source_fd = os.open(
        source_home, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    )
    try:
        try:
            os.mkdir(_SYNC_STATE_DIRECTORY, mode=0o700, dir_fd=source_fd)
        except FileExistsError:
            pass
        state_fd = os.open(
            _SYNC_STATE_DIRECTORY,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=source_fd,
        )
    finally:
        os.close(source_fd)
    metadata = os.fstat(state_fd)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        os.close(state_fd)
        raise ValueError(_SAFE_ERROR)
    return state_fd


def _build_session_sync_plan(
    isolated_sessions: Path,
    destination_sessions: Path,
) -> _SessionSyncPlan:
    directories, source_files = _enumerate_isolated_sessions(isolated_sessions)
    destination_identities: dict[Path, tuple[int, int] | None] = {}
    destination_root = destination_sessions.parent
    _require_owned_directory(destination_root)

    for relative in directories:
        destination = destination_sessions / relative
        parent = destination.parent
        parent_relative = relative.parent if relative != Path(".") else None
        parent_exists = relative == Path(".") or (
            parent_relative in destination_identities
            and destination_identities[parent_relative] is not None
        )
        if relative == Path("."):
            parent_exists = True
        if parent_exists:
            _reject_case_conflict(parent, destination.name)
        identity = _directory_identity_or_missing(destination)
        if identity is not None and not parent_exists and relative != Path("."):
            raise ValueError(_SAFE_ERROR)
        destination_identities[relative] = identity

    planned_files: list[_SessionSyncFile] = []
    for relative, source, source_identity in source_files:
        parent_relative = relative.parent
        destination = destination_sessions / relative
        if destination_identities.get(parent_relative) is not None:
            _reject_case_conflict(destination.parent, destination.name)
            destination_identity = _file_identity_or_missing(destination)
        else:
            destination_identity = None
        planned_files.append(
            _SessionSyncFile(
                relative=relative,
                source=source,
                destination=destination,
                source_identity=source_identity,
                destination_identity=destination_identity,
            )
        )
    return _SessionSyncPlan(
        isolated_sessions=isolated_sessions,
        destination_sessions=destination_sessions,
        directories=tuple(directories),
        directory_identities=destination_identities,
        files=tuple(planned_files),
    )


def _enumerate_isolated_sessions(
    root: Path,
) -> tuple[list[Path], list[tuple[Path, Path, _FileIdentity]]]:
    directories = [Path(".")]
    files: list[tuple[Path, Path, _FileIdentity]] = []
    case_keys: set[str] = set()

    def visit(directory: Path, relative_directory: Path) -> None:
        entries = sorted(
            os.scandir(directory),
            key=lambda entry: (entry.name.casefold(), entry.name),
        )
        for entry in entries:
            relative = relative_directory / entry.name
            if (
                not entry.name
                or entry.name in {".", ".."}
                or entry.name.startswith(".workbench-sync-")
                or relative.is_absolute()
                or ".." in relative.parts
            ):
                raise ValueError(_SAFE_ERROR)
            case_key = relative.as_posix().casefold()
            if case_key in case_keys:
                raise ValueError(_SAFE_ERROR)
            try:
                assert_no_credentials(relative.as_posix())
            except ValueError as exc:
                raise ValueError(_SAFE_ERROR) from exc
            case_keys.add(case_key)
            metadata = entry.stat(follow_symlinks=False)
            if metadata.st_uid != os.getuid() or stat.S_ISLNK(metadata.st_mode):
                raise ValueError(_SAFE_ERROR)
            path = Path(entry.path)
            if stat.S_ISDIR(metadata.st_mode):
                directories.append(relative)
                visit(path, relative)
            elif stat.S_ISREG(metadata.st_mode):
                if metadata.st_size > _MAX_SYNC_FILE_BYTES:
                    raise ValueError(_SAFE_ERROR)
                files.append((relative, path, _identity_from_stat(metadata)))
            else:
                raise ValueError(_SAFE_ERROR)

    visit(root, Path("."))
    directories.sort(
        key=lambda value: (
            len(value.parts),
            value.as_posix().casefold(),
            value.as_posix(),
        )
    )
    files.sort(
        key=lambda value: (
            value[0].as_posix().casefold(),
            value[0].as_posix(),
        )
    )
    return directories, files


def _execute_session_sync(
    plan: _SessionSyncPlan,
    sync_state: _SessionSyncState,
) -> None:
    transaction_id = uuid.uuid4().hex
    for index, entry in enumerate(plan.files):
        entry.stage_name = f".workbench-sync-stage-{transaction_id}-{index:08d}"
        entry.backup_name = f".workbench-sync-backup-{transaction_id}-{index:08d}"
    created_relatives = tuple(
        relative
        for relative in plan.directories
        if plan.directory_identities[relative] is None
    )
    try:
        _create_missing_session_directories(plan)
        for entry in plan.files:
            parent_fd = _open_verified_directory(
                entry.destination.parent,
                plan.directory_identities[entry.relative.parent],
            )
            try:
                _validate_destination(entry)
                _copy_validated_file_to_new(
                    entry.source,
                    entry.source_identity,
                    parent_fd,
                    entry.stage_name,
                    secure_mode=True,
                )
                entry.stage_identity = _file_identity(
                    entry.destination.parent / entry.stage_name
                )
                entry.stage_digest = _stable_file_digest_at(
                    parent_fd,
                    entry.stage_name,
                    expected=entry.stage_identity,
                )
            finally:
                os.close(parent_fd)

        for entry in plan.files:
            if entry.destination_identity is None:
                continue
            parent_fd = _open_verified_directory(
                entry.destination.parent,
                plan.directory_identities[entry.relative.parent],
            )
            try:
                _validate_destination(entry)
                _copy_validated_file_to_new(
                    entry.destination,
                    entry.destination_identity,
                    parent_fd,
                    entry.backup_name,
                    secure_mode=False,
                )
                entry.backup_identity = _file_identity(
                    entry.destination.parent / entry.backup_name
                )
            finally:
                os.close(parent_fd)

        _fsync_sync_directories(plan)
        _validate_sync_plan_before_commit(plan)
        prepared_payload = _session_journal_payload(
            sync_state,
            plan,
            transaction_id=transaction_id,
            phase="prepared",
            created_directories=created_relatives,
        )
        _write_session_journal(sync_state, prepared_payload)
        for entry in plan.files:
            parent_fd = _open_verified_directory(
                entry.destination.parent,
                plan.directory_identities[entry.relative.parent],
            )
            try:
                _validate_source(entry)
                _validate_destination(entry)
                os.replace(
                    entry.stage_name,
                    entry.destination.name,
                    src_dir_fd=parent_fd,
                    dst_dir_fd=parent_fd,
                )
                os.fsync(parent_fd)
            finally:
                os.close(parent_fd)
        committed_payload = dict(prepared_payload)
        committed_payload["phase"] = "committed"
        _write_session_journal(sync_state, committed_payload)
    except BaseException:
        _recover_session_journal(sync_state)
        raise
    try:
        _cleanup_committed_session_sync(sync_state, committed_payload)
    except BaseException:
        # The committed journal is the durable cleanup obligation. A later lock
        # holder will finish cleanup without rolling back provider state.
        return


class _InvalidSessionJournal(ValueError):
    pass


def _session_journal_payload(
    sync_state: _SessionSyncState,
    plan: _SessionSyncPlan,
    *,
    transaction_id: str,
    phase: str,
    created_directories: tuple[Path, ...],
) -> dict[str, object]:
    source_metadata = sync_state.source_home.lstat()
    return {
        "version": _SYNC_JOURNAL_VERSION,
        "transaction_id": transaction_id,
        "phase": phase,
        "source_device": source_metadata.st_dev,
        "source_inode": source_metadata.st_ino,
        "created_directories": [
            relative.as_posix() for relative in created_directories
        ],
        "entries": [
            {
                "relative": entry.relative.as_posix(),
                "existed": entry.destination_identity is not None,
                "original_identity": _identity_payload(entry.destination_identity),
                "backup_identity": _identity_payload(entry.backup_identity),
                "stage_identity": _identity_payload(entry.stage_identity),
                "stage_digest": entry.stage_digest,
                "backup_name": entry.backup_name,
                "stage_name": entry.stage_name,
            }
            for entry in plan.files
        ],
    }


def _identity_payload(identity: _FileIdentity | None) -> dict[str, int] | None:
    if identity is None:
        return None
    return {
        "device": identity.device,
        "inode": identity.inode,
        "size": identity.size,
        "modified_ns": identity.modified_ns,
        "changed_ns": identity.changed_ns,
        "mode": identity.mode,
    }


def _write_session_journal(
    sync_state: _SessionSyncState,
    payload: dict[str, object],
) -> None:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(encoded) > _MAX_SYNC_JOURNAL_BYTES:
        raise ValueError(_SAFE_ERROR)
    temporary_name = f".journal.tmp-{uuid.uuid4().hex}"
    temporary_fd = -1
    try:
        temporary_fd = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=sync_state.state_fd,
        )
        _write_all(temporary_fd, encoded)
        os.fchmod(temporary_fd, 0o600)
        os.fsync(temporary_fd)
        os.close(temporary_fd)
        temporary_fd = -1
        os.replace(
            temporary_name,
            _SYNC_JOURNAL_NAME,
            src_dir_fd=sync_state.state_fd,
            dst_dir_fd=sync_state.state_fd,
        )
        os.fsync(sync_state.state_fd)
    finally:
        if temporary_fd >= 0:
            os.close(temporary_fd)
        try:
            os.unlink(temporary_name, dir_fd=sync_state.state_fd)
        except FileNotFoundError:
            pass


def _recover_session_journal(sync_state: _SessionSyncState) -> None:
    try:
        payload = _read_session_journal(sync_state)
    except FileNotFoundError:
        _remove_orphan_sync_artifacts(sync_state.source_home / "sessions")
        return
    except _InvalidSessionJournal as exc:
        _quarantine_session_journal(sync_state)
        raise ValueError(_SAFE_ERROR) from exc

    try:
        if payload["phase"] == "prepared":
            _recover_prepared_session_sync(sync_state, payload)
        else:
            _cleanup_committed_session_sync(sync_state, payload)
    except BaseException as exc:
        raise ValueError(_SAFE_ERROR) from exc


def _read_session_journal(sync_state: _SessionSyncState) -> dict[str, object]:
    try:
        metadata = os.stat(
            _SYNC_JOURNAL_NAME,
            dir_fd=sync_state.state_fd,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        raise
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_size > _MAX_SYNC_JOURNAL_BYTES
    ):
        raise _InvalidSessionJournal()
    journal_fd = os.open(
        _SYNC_JOURNAL_NAME,
        os.O_RDONLY | os.O_NOFOLLOW,
        dir_fd=sync_state.state_fd,
    )
    try:
        opened = os.fstat(journal_fd)
        if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
            raise _InvalidSessionJournal()
        data = bytearray()
        while len(data) <= _MAX_SYNC_JOURNAL_BYTES:
            chunk = os.read(
                journal_fd,
                min(64 * 1024, _MAX_SYNC_JOURNAL_BYTES + 1 - len(data)),
            )
            if not chunk:
                break
            data.extend(chunk)
    finally:
        os.close(journal_fd)
    if len(data) > _MAX_SYNC_JOURNAL_BYTES:
        raise _InvalidSessionJournal()
    try:
        payload = json.loads(bytes(data).decode("utf-8"), object_pairs_hook=_unique_json_object)
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise _InvalidSessionJournal() from exc
    canonical = json.dumps(
        payload,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if canonical != bytes(data):
        raise _InvalidSessionJournal()
    return _validate_session_journal(sync_state, payload)


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _InvalidSessionJournal()
        result[key] = value
    return result


def _validate_session_journal(
    sync_state: _SessionSyncState,
    payload: object,
) -> dict[str, object]:
    expected_keys = {
        "version",
        "transaction_id",
        "phase",
        "source_device",
        "source_inode",
        "created_directories",
        "entries",
    }
    if not isinstance(payload, dict) or set(payload) != expected_keys:
        raise _InvalidSessionJournal()
    transaction_id = payload["transaction_id"]
    if (
        type(payload["version"]) is not int
        or payload["version"] != _SYNC_JOURNAL_VERSION
        or not isinstance(transaction_id, str)
        or not _is_canonical_home_id(transaction_id)
        or payload["phase"] not in {"prepared", "committed"}
    ):
        raise _InvalidSessionJournal()
    source_metadata = sync_state.source_home.lstat()
    if (
        type(payload["source_device"]) is not int
        or type(payload["source_inode"]) is not int
        or (payload["source_device"], payload["source_inode"])
        != (source_metadata.st_dev, source_metadata.st_ino)
    ):
        raise _InvalidSessionJournal()
    entries = payload["entries"]
    created = payload["created_directories"]
    if (
        not isinstance(entries, list)
        or len(entries) > _MAX_SYNC_JOURNAL_ENTRIES
        or not isinstance(created, list)
        or len(created) > _MAX_SYNC_JOURNAL_ENTRIES
    ):
        raise _InvalidSessionJournal()
    previous_key = ""
    case_keys: set[str] = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict) or set(entry) != {
            "relative",
            "existed",
            "original_identity",
            "backup_identity",
            "stage_identity",
            "stage_digest",
            "backup_name",
            "stage_name",
        }:
            raise _InvalidSessionJournal()
        relative = _validated_journal_relative(entry["relative"], allow_root=False)
        order_key = f"{relative.casefold()}\0{relative}"
        if order_key <= previous_key or relative.casefold() in case_keys:
            raise _InvalidSessionJournal()
        previous_key = order_key
        case_keys.add(relative.casefold())
        if type(entry["existed"]) is not bool:
            raise _InvalidSessionJournal()
        original = _identity_from_payload(entry["original_identity"])
        backup = _identity_from_payload(entry["backup_identity"])
        stage = _identity_from_payload(entry["stage_identity"])
        stage_digest = entry["stage_digest"]
        if (original is not None) != entry["existed"]:
            raise _InvalidSessionJournal()
        if (
            (backup is not None and stage is None)
            or (payload["phase"] == "committed" and stage is None)
            or (entry["existed"] and stage is not None and backup is None)
            or stage is None
            or not isinstance(stage_digest, str)
            or not _SHA256_DIGEST.fullmatch(stage_digest)
        ):
            raise _InvalidSessionJournal()
        expected_stage = f".workbench-sync-stage-{transaction_id}-{index:08d}"
        expected_backup = f".workbench-sync-backup-{transaction_id}-{index:08d}"
        if entry["stage_name"] != expected_stage or entry["backup_name"] != expected_backup:
            raise _InvalidSessionJournal()
        entry["original_identity"] = original
        entry["backup_identity"] = backup
        entry["stage_identity"] = stage
    created_keys: set[str] = set()
    validated_created: list[str] = []
    for relative_value in created:
        relative = _validated_journal_relative(relative_value, allow_root=True)
        if relative.casefold() in created_keys:
            raise _InvalidSessionJournal()
        created_keys.add(relative.casefold())
        validated_created.append(relative)
    expected_created = sorted(
        validated_created,
        key=lambda value: (
            len(PurePosixPath(value).parts),
            value.casefold(),
            value,
        ),
    )
    if validated_created != expected_created:
        raise _InvalidSessionJournal()
    return payload


def _identity_from_payload(payload: object) -> _FileIdentity | None:
    if payload is None:
        return None
    keys = {"device", "inode", "size", "modified_ns", "changed_ns", "mode"}
    if not isinstance(payload, dict) or set(payload) != keys:
        raise _InvalidSessionJournal()
    if any(type(payload[key]) is not int or payload[key] < 0 for key in keys):
        raise _InvalidSessionJournal()
    if not stat.S_ISREG(payload["mode"]):
        raise _InvalidSessionJournal()
    return _FileIdentity(
        device=payload["device"],
        inode=payload["inode"],
        size=payload["size"],
        modified_ns=payload["modified_ns"],
        changed_ns=payload["changed_ns"],
        mode=payload["mode"],
    )


def _validated_journal_relative(value: object, *, allow_root: bool) -> str:
    if not isinstance(value, str) or not value or "\0" in value or "\\" in value:
        raise _InvalidSessionJournal()
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise _InvalidSessionJournal() from exc
    if len(encoded) > _MAX_JOURNAL_PATH_BYTES:
        raise _InvalidSessionJournal()
    relative = PurePosixPath(value)
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or (value == "." and not allow_root)
        or (value != "." and relative.as_posix() != value)
        or any(
            not part
            or part in {".", ".."}
            or len(part.encode("utf-8")) > _MAX_JOURNAL_COMPONENT_BYTES
            for part in relative.parts
            if value != "."
        )
    ):
        raise _InvalidSessionJournal()
    return value


def _recover_prepared_session_sync(
    sync_state: _SessionSyncState,
    payload: dict[str, object],
) -> None:
    sessions = sync_state.source_home / "sessions"
    entries = payload["entries"]
    assert isinstance(entries, list)
    transaction_id = payload["transaction_id"]
    assert isinstance(transaction_id, str)
    external_writers = _prevalidate_prepared_recovery(sync_state, payload)
    for index, entry in reversed(list(enumerate(entries))):
        assert isinstance(entry, dict)
        relative = Path(entry["relative"])
        destination = sessions / relative
        parent_fd = _open_recovery_parent(sessions, relative.parent)
        if parent_fd is None:
            if entry["existed"]:
                raise ValueError(_SAFE_ERROR)
            continue
        try:
            destination_identity = _file_identity_at_or_missing(
                parent_fd, destination.name
            )
            if entry["existed"]:
                original = entry["original_identity"]
                backup_identity = entry["backup_identity"]
                stage_identity = entry["stage_identity"]
                assert isinstance(original, _FileIdentity)
                if backup_identity is None:
                    if destination_identity != original:
                        raise ValueError(_SAFE_ERROR)
                    continue
                assert isinstance(backup_identity, _FileIdentity)
                assert isinstance(stage_identity, _FileIdentity)
                if destination_identity == original:
                    continue
                installed = _destination_matches_stage(
                    parent_fd,
                    destination.name,
                    stage_identity,
                    entry["stage_digest"],
                )
                if installed is not True:
                    # An external writer won the path after preparation. Preserve
                    # that writer's result while rolling back component-owned paths.
                    external_writers.add(entry["relative"])
                    continue
                backup_name = entry["backup_name"]
                assert isinstance(backup_name, str)
                if _file_identity_at_or_missing(parent_fd, backup_name) != backup_identity:
                    raise ValueError(_SAFE_ERROR)
                restore_name = (
                    f".workbench-sync-restore-{transaction_id}-{index:08d}"
                )
                _unlink_owned_artifact(parent_fd, restore_name)
                _copy_validated_file_to_new(
                    destination.parent / backup_name,
                    backup_identity,
                    parent_fd,
                    restore_name,
                    secure_mode=False,
                )
                os.replace(
                    restore_name,
                    destination.name,
                    src_dir_fd=parent_fd,
                    dst_dir_fd=parent_fd,
                )
                os.fsync(parent_fd)
            elif destination_identity is not None:
                stage_identity = entry["stage_identity"]
                if not isinstance(stage_identity, _FileIdentity):
                    raise ValueError(_SAFE_ERROR)
                installed = _destination_matches_stage(
                    parent_fd,
                    destination.name,
                    stage_identity,
                    entry["stage_digest"],
                )
                if installed is not True:
                    external_writers.add(entry["relative"])
                    continue
                os.unlink(destination.name, dir_fd=parent_fd)
                os.fsync(parent_fd)
        finally:
            os.close(parent_fd)

    _remove_journal_artifacts(sync_state, payload, include_backups=False)
    _remove_journal_created_directories(sync_state, payload)
    _remove_session_journal(sync_state)
    try:
        _remove_journal_artifacts(sync_state, payload, include_backups=True)
    except OSError:
        # With the prepared journal durably removed, destinations are restored.
        # Remaining component-owned artifacts are reconciled on next acquisition.
        pass
    if external_writers:
        raise ValueError(_SAFE_ERROR)


def _prevalidate_prepared_recovery(
    sync_state: _SessionSyncState,
    payload: dict[str, object],
) -> set[str]:
    sessions = sync_state.source_home / "sessions"
    entries = payload["entries"]
    assert isinstance(entries, list)
    transaction_id = payload["transaction_id"]
    assert isinstance(transaction_id, str)
    external_writers: set[str] = set()
    for index, entry in enumerate(entries):
        assert isinstance(entry, dict)
        relative = Path(entry["relative"])
        parent_fd = _open_recovery_parent(sessions, relative.parent)
        if parent_fd is None:
            if entry["existed"]:
                raise ValueError(_SAFE_ERROR)
            continue
        try:
            destination_identity = _file_identity_at_or_missing(
                parent_fd, relative.name
            )
            stage_identity = entry["stage_identity"]
            if entry["existed"]:
                original = entry["original_identity"]
                backup_identity = entry["backup_identity"]
                assert isinstance(original, _FileIdentity)
                if backup_identity is None:
                    if destination_identity != original:
                        raise ValueError(_SAFE_ERROR)
                elif (
                    destination_identity != original
                    and isinstance(stage_identity, _FileIdentity)
                ):
                    installed = _destination_matches_stage(
                        parent_fd,
                        relative.name,
                        stage_identity,
                        entry["stage_digest"],
                    )
                    if installed is True:
                        backup_name = entry["backup_name"]
                        assert isinstance(backup_name, str)
                        if _file_identity_at_or_missing(
                            parent_fd, backup_name
                        ) != backup_identity:
                            raise ValueError(_SAFE_ERROR)
                    else:
                        external_writers.add(entry["relative"])
            elif destination_identity is not None:
                if not isinstance(stage_identity, _FileIdentity):
                    raise ValueError(_SAFE_ERROR)
                installed = _destination_matches_stage(
                    parent_fd,
                    relative.name,
                    stage_identity,
                    entry["stage_digest"],
                )
                if installed is not True:
                    external_writers.add(entry["relative"])
            _validate_owned_artifact_if_present(parent_fd, entry["stage_name"])
            _validate_owned_artifact_if_present(parent_fd, entry["backup_name"])
            _validate_owned_artifact_if_present(
                parent_fd,
                f".workbench-sync-restore-{transaction_id}-{index:08d}",
            )
        finally:
            os.close(parent_fd)
    created = payload["created_directories"]
    assert isinstance(created, list)
    for relative_value in created:
        destination = sessions / Path(relative_value)
        try:
            metadata = destination.lstat()
        except FileNotFoundError:
            continue
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.getuid()
        ):
            raise ValueError(_SAFE_ERROR)
    return external_writers


def _validate_owned_artifact_if_present(parent_fd: int, name: object) -> None:
    if not isinstance(name, str) or not _SYNC_ARTIFACT.fullmatch(name):
        raise ValueError(_SAFE_ERROR)
    try:
        metadata = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
    ):
        raise ValueError(_SAFE_ERROR)


def _cleanup_committed_session_sync(
    sync_state: _SessionSyncState,
    payload: dict[str, object],
) -> None:
    _remove_journal_artifacts(sync_state, payload, include_backups=True)
    _remove_session_journal(sync_state)


def _remove_journal_artifacts(
    sync_state: _SessionSyncState,
    payload: dict[str, object],
    *,
    include_backups: bool,
) -> None:
    sessions = sync_state.source_home / "sessions"
    entries = payload["entries"]
    assert isinstance(entries, list)
    for index, entry in enumerate(entries):
        assert isinstance(entry, dict)
        relative = Path(entry["relative"])
        parent_fd = _open_recovery_parent(sessions, relative.parent)
        if parent_fd is None:
            continue
        try:
            names = [entry["stage_name"]]
            if include_backups:
                names.append(entry["backup_name"])
            transaction_id = payload["transaction_id"]
            assert isinstance(transaction_id, str)
            names.append(
                f".workbench-sync-restore-{transaction_id}-{index:08d}"
            )
            for name in names:
                assert isinstance(name, str)
                _unlink_owned_artifact(parent_fd, name)
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)


def _unlink_owned_artifact(parent_fd: int, name: str) -> None:
    if not _SYNC_ARTIFACT.fullmatch(name):
        raise ValueError(_SAFE_ERROR)
    try:
        metadata = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
    ):
        raise ValueError(_SAFE_ERROR)
    os.unlink(name, dir_fd=parent_fd)


def _remove_journal_created_directories(
    sync_state: _SessionSyncState,
    payload: dict[str, object],
) -> None:
    created = payload["created_directories"]
    assert isinstance(created, list)
    sessions = sync_state.source_home / "sessions"
    relative_paths = [Path(value) for value in created]
    relative_paths.sort(key=lambda value: len(value.parts), reverse=True)
    for relative in relative_paths:
        destination = sessions / relative
        try:
            metadata = destination.lstat()
        except FileNotFoundError:
            continue
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.getuid()
        ):
            raise ValueError(_SAFE_ERROR)
        try:
            destination.rmdir()
        except OSError as exc:
            if exc.errno != errno.ENOTEMPTY:
                raise
    if sessions.exists():
        directory_fd = os.open(
            sessions, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        )
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)


def _open_recovery_parent(sessions: Path, relative_parent: Path) -> int | None:
    try:
        sessions_metadata = sessions.lstat()
    except FileNotFoundError:
        return None
    if (
        stat.S_ISLNK(sessions_metadata.st_mode)
        or not stat.S_ISDIR(sessions_metadata.st_mode)
        or sessions_metadata.st_uid != os.getuid()
    ):
        raise ValueError(_SAFE_ERROR)
    current = sessions
    for part in relative_parent.parts:
        if part in {"", "."}:
            continue
        current /= part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            return None
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.getuid()
        ):
            raise ValueError(_SAFE_ERROR)
    return os.open(current, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)


def _file_identity_at_or_missing(
    parent_fd: int,
    name: str,
) -> _FileIdentity | None:
    try:
        metadata = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
    ):
        raise ValueError(_SAFE_ERROR)
    return _identity_from_stat(metadata)


def _destination_matches_stage(
    parent_fd: int,
    name: str,
    staged: _FileIdentity,
    expected_digest: object,
) -> bool | None:
    if not isinstance(expected_digest, str) or not _SHA256_DIGEST.fullmatch(
        expected_digest
    ):
        raise ValueError(_SAFE_ERROR)
    try:
        current = _file_identity_at_or_missing(parent_fd, name)
    except ValueError:
        return None
    if current is None or (current.device, current.inode) != (
        staged.device,
        staged.inode,
    ):
        return False
    try:
        digest = _stable_file_digest_at(parent_fd, name, expected=current)
    except ValueError:
        return None
    return digest == expected_digest


def _stable_file_digest_at(
    parent_fd: int,
    name: str,
    *,
    expected: _FileIdentity,
) -> str:
    file_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_fd)
    try:
        initial = os.fstat(file_fd)
        initial_identity = _identity_from_stat(initial)
        if (
            not stat.S_ISREG(initial.st_mode)
            or initial.st_uid != os.getuid()
            or initial.st_size > _MAX_SYNC_FILE_BYTES
            or initial_identity != expected
        ):
            raise ValueError(_SAFE_ERROR)
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = os.read(file_fd, 64 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > _MAX_SYNC_FILE_BYTES:
                raise ValueError(_SAFE_ERROR)
            digest.update(chunk)
        final_identity = _identity_from_stat(os.fstat(file_fd))
        if final_identity != initial_identity or total != initial.st_size:
            raise ValueError(_SAFE_ERROR)
        return digest.hexdigest()
    finally:
        os.close(file_fd)


def _remove_session_journal(sync_state: _SessionSyncState) -> None:
    try:
        os.unlink(_SYNC_JOURNAL_NAME, dir_fd=sync_state.state_fd)
    except FileNotFoundError:
        return
    os.fsync(sync_state.state_fd)


def _quarantine_session_journal(sync_state: _SessionSyncState) -> None:
    quarantine = f"journal.invalid-{uuid.uuid4().hex}"
    try:
        os.replace(
            _SYNC_JOURNAL_NAME,
            quarantine,
            src_dir_fd=sync_state.state_fd,
            dst_dir_fd=sync_state.state_fd,
        )
        os.fsync(sync_state.state_fd)
    except FileNotFoundError:
        pass


def _remove_orphan_journal_temps(sync_state: _SessionSyncState) -> None:
    for name in os.listdir(sync_state.state_fd):
        if not re.fullmatch(r"\.journal\.tmp-[0-9a-f]{32}", name):
            continue
        try:
            metadata = os.stat(
                name, dir_fd=sync_state.state_fd, follow_symlinks=False
            )
        except FileNotFoundError:
            continue
        if (
            stat.S_ISREG(metadata.st_mode)
            and not stat.S_ISLNK(metadata.st_mode)
            and metadata.st_uid == os.getuid()
        ):
            os.unlink(name, dir_fd=sync_state.state_fd)
    os.fsync(sync_state.state_fd)


def _remove_orphan_sync_artifacts(sessions: Path) -> None:
    try:
        metadata = sessions.lstat()
    except FileNotFoundError:
        return
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
    ):
        raise ValueError(_SAFE_ERROR)
    for directory, directory_names, file_names in os.walk(sessions, followlinks=False):
        directory_path = Path(directory)
        directory_metadata = directory_path.lstat()
        if (
            stat.S_ISLNK(directory_metadata.st_mode)
            or not stat.S_ISDIR(directory_metadata.st_mode)
            or directory_metadata.st_uid != os.getuid()
        ):
            raise ValueError(_SAFE_ERROR)
        safe_directories: list[str] = []
        for name in sorted(directory_names):
            child_metadata = (directory_path / name).lstat()
            if stat.S_ISLNK(child_metadata.st_mode):
                continue
            if (
                not stat.S_ISDIR(child_metadata.st_mode)
                or child_metadata.st_uid != os.getuid()
            ):
                raise ValueError(_SAFE_ERROR)
            safe_directories.append(name)
        directory_names[:] = safe_directories
        for name in file_names:
            if not _SYNC_ARTIFACT.fullmatch(name):
                continue
            parent_fd = os.open(
                directory_path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            )
            try:
                _unlink_owned_artifact(parent_fd, name)
                os.fsync(parent_fd)
            finally:
                os.close(parent_fd)


def _create_missing_session_directories(plan: _SessionSyncPlan) -> None:
    for relative in plan.directories:
        if plan.directory_identities[relative] is not None:
            _validate_directory_identity(
                plan.destination_sessions / relative,
                plan.directory_identities[relative],
            )
            continue
        destination = plan.destination_sessions / relative
        parent_relative = relative.parent if relative != Path(".") else None
        expected_parent = (
            _directory_identity(plan.destination_sessions.parent)
            if parent_relative is None
            else plan.directory_identities[parent_relative]
        )
        _validate_directory_identity(destination.parent, expected_parent)
        _reject_case_conflict(destination.parent, destination.name)
        os.mkdir(destination, mode=0o700)
        destination.chmod(0o700)
        identity = _directory_identity(destination)
        plan.directory_identities[relative] = identity


def _validate_sync_plan_before_commit(plan: _SessionSyncPlan) -> None:
    for relative in plan.directories:
        _validate_directory_identity(
            plan.destination_sessions / relative,
            plan.directory_identities[relative],
        )
    for entry in plan.files:
        _validate_source(entry)
        _validate_destination(entry)


def _fsync_sync_directories(plan: _SessionSyncPlan) -> None:
    for relative in plan.directories:
        directory_fd = _open_verified_directory(
            plan.destination_sessions / relative,
            plan.directory_identities[relative],
        )
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)


def _copy_validated_file_to_new(
    source: Path,
    expected: _FileIdentity,
    destination_fd: int,
    destination_name: str,
    *,
    secure_mode: bool,
) -> None:
    source_fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
    target_fd = -1
    try:
        initial = os.fstat(source_fd)
        if (
            _identity_from_stat(initial) != expected
            or initial.st_uid != os.getuid()
            or initial.st_size > _MAX_SYNC_FILE_BYTES
        ):
            raise ValueError(_SAFE_ERROR)
        target_fd = os.open(
            destination_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=destination_fd,
        )
        total = 0
        while True:
            chunk = os.read(source_fd, 64 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > _MAX_SYNC_FILE_BYTES:
                raise ValueError(_SAFE_ERROR)
            _write_all(target_fd, chunk)
        if _identity_from_stat(os.fstat(source_fd)) != expected:
            raise ValueError(_SAFE_ERROR)
        mode = stat.S_IMODE(expected.mode)
        if secure_mode:
            mode &= 0o700
            mode = mode or 0o600
        os.fchmod(target_fd, mode)
        os.fsync(target_fd)
    finally:
        if target_fd >= 0:
            os.close(target_fd)
        os.close(source_fd)


def _validate_source(entry: _SessionSyncFile) -> None:
    if _file_identity(entry.source) != entry.source_identity:
        raise ValueError(_SAFE_ERROR)


def _validate_destination(entry: _SessionSyncFile) -> None:
    _reject_case_conflict(entry.destination.parent, entry.destination.name)
    if _file_identity_or_missing(entry.destination) != entry.destination_identity:
        raise ValueError(_SAFE_ERROR)


def _reject_case_conflict(parent: Path, name: str) -> None:
    try:
        entries = os.listdir(parent)
    except FileNotFoundError:
        return
    folded = name.casefold()
    if any(existing.casefold() == folded and existing != name for existing in entries):
        raise ValueError(_SAFE_ERROR)


def _file_identity(path: Path) -> _FileIdentity:
    metadata = path.lstat()
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
    ):
        raise ValueError(_SAFE_ERROR)
    return _identity_from_stat(metadata)


def _file_identity_or_missing(path: Path) -> _FileIdentity | None:
    try:
        return _file_identity(path)
    except FileNotFoundError:
        return None


def _identity_from_stat(metadata: os.stat_result) -> _FileIdentity:
    return _FileIdentity(
        device=metadata.st_dev,
        inode=metadata.st_ino,
        size=metadata.st_size,
        modified_ns=metadata.st_mtime_ns,
        changed_ns=metadata.st_ctime_ns,
        mode=metadata.st_mode,
    )


def _directory_identity(path: Path) -> tuple[int, int]:
    metadata = path.lstat()
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
    ):
        raise ValueError(_SAFE_ERROR)
    return metadata.st_dev, metadata.st_ino


def _directory_identity_or_missing(path: Path) -> tuple[int, int] | None:
    try:
        return _directory_identity(path)
    except FileNotFoundError:
        return None


def _validate_directory_identity(
    path: Path,
    expected: tuple[int, int] | None,
) -> None:
    if expected is None or _directory_identity(path) != expected:
        raise ValueError(_SAFE_ERROR)


def _open_verified_directory(path: Path, expected: tuple[int, int] | None) -> int:
    _validate_directory_identity(path, expected)
    directory_fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    opened = os.fstat(directory_fd)
    if expected != (opened.st_dev, opened.st_ino) or opened.st_uid != os.getuid():
        os.close(directory_fd)
        raise ValueError(_SAFE_ERROR)
    return directory_fd


def _write_new_file(directory_fd: int, name: str, data: bytes, mode: int) -> None:
    fd = os.open(
        name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        mode,
        dir_fd=directory_fd,
    )
    try:
        _write_all(fd, data)
        os.fchmod(fd, mode)
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_all(fd: int, data: bytes) -> None:
    offset = 0
    while offset < len(data):
        offset += os.write(fd, data[offset:])


def _validated_marker_token(path: Path, lock_fd: int) -> str:
    try:
        lock_stat = os.fstat(lock_fd)
        path_lock_stat = (path / _LOCK_NAME).lstat()
        if (
            not stat.S_ISREG(lock_stat.st_mode)
            or lock_stat.st_uid != os.getuid()
            or stat.S_IMODE(lock_stat.st_mode) != 0o600
            or (lock_stat.st_dev, lock_stat.st_ino)
            != (path_lock_stat.st_dev, path_lock_stat.st_ino)
        ):
            return ""
        marker_fd = os.open(path / _MARKER_NAME, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            marker_stat = os.fstat(marker_fd)
            if (
                not stat.S_ISREG(marker_stat.st_mode)
                or marker_stat.st_uid != os.getuid()
                or stat.S_IMODE(marker_stat.st_mode) != 0o600
                or marker_stat.st_size > 512
            ):
                return ""
            marker_data = bytearray()
            while len(marker_data) <= 512:
                chunk = os.read(marker_fd, 513 - len(marker_data))
                if not chunk:
                    break
                marker_data.extend(chunk)
            payload = json.loads(bytes(marker_data).decode("utf-8"))
        finally:
            os.close(marker_fd)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        return ""
    if not isinstance(payload, dict):
        return ""
    token = payload.get("token")
    if (
        payload.get("version") != _MARKER_VERSION
        or payload.get("uid") != os.getuid()
        or payload.get("home_id") != path.name
        or not isinstance(token, str)
        or not _is_canonical_home_id(token)
    ):
        return ""
    return token


def _clear_directory_fd(directory_fd: int) -> None:
    for name in os.listdir(directory_fd):
        metadata = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if stat.S_ISDIR(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode):
            child_fd = os.open(
                name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=directory_fd,
            )
            try:
                _clear_directory_fd(child_fd)
            finally:
                os.close(child_fd)
            os.rmdir(name, dir_fd=directory_fd)
        else:
            os.unlink(name, dir_fd=directory_fd)


def _remove_uninitialized_child(child: Path, root: Path) -> None:
    try:
        if child.parent == root and _is_canonical_home_id(child.name):
            child.rmdir()
    except OSError:
        pass


def _is_canonical_home_id(value: str) -> bool:
    try:
        return uuid.UUID(value).hex == value
    except (AttributeError, ValueError):
        return False
