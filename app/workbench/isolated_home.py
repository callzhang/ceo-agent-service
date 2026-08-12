"""Crash-recoverable ownership for temporary Codex homes."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import stat
import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from app.codex_history import find_codex_session_path


_ROOT_NAME = "ceo-agent-service-workbench-codex"
_MARKER_NAME = ".owner.json"
_LOCK_NAME = ".active"
_CONFIG_NAME = "config.toml"
_MARKER_VERSION = 1
_MAX_RECONCILE_ENTRIES = 256
_SAFE_ERROR = "Codex configuration could not be isolated safely"
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


@dataclass
class _SessionSyncPlan:
    isolated_sessions: Path
    destination_sessions: Path
    directories: tuple[Path, ...]
    directory_identities: dict[Path, tuple[int, int] | None]
    files: tuple[_SessionSyncFile, ...]


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
    with _session_sync_lock(source_home, lock_root):
        plan = _build_session_sync_plan(isolated_sessions, source_home / "sessions")
        _execute_session_sync(plan)


@contextmanager
def _session_sync_lock(source_home: Path, lock_root: Path) -> Iterator[None]:
    metadata = source_home.lstat()
    lock_key = hashlib.sha256(
        f"{metadata.st_dev}:{metadata.st_ino}".encode("ascii")
    ).hexdigest()
    lock_name = f".session-sync-{lock_key}.lock"
    root_fd = os.open(lock_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    lock_fd = -1
    try:
        lock_fd = os.open(
            lock_name,
            os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
            0o600,
            dir_fd=root_fd,
        )
        lock_metadata = os.fstat(lock_fd)
        if (
            not stat.S_ISREG(lock_metadata.st_mode)
            or lock_metadata.st_uid != os.getuid()
            or stat.S_IMODE(lock_metadata.st_mode) != 0o600
        ):
            raise ValueError(_SAFE_ERROR)
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        yield
    finally:
        if lock_fd >= 0:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(lock_fd)
        os.close(root_fd)


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
            case_keys.add(case_key)
            metadata = entry.stat(follow_symlinks=False)
            if metadata.st_uid != os.getuid() or stat.S_ISLNK(metadata.st_mode):
                raise ValueError(_SAFE_ERROR)
            path = Path(entry.path)
            if stat.S_ISDIR(metadata.st_mode):
                directories.append(relative)
                visit(path, relative)
            elif stat.S_ISREG(metadata.st_mode):
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


def _execute_session_sync(plan: _SessionSyncPlan) -> None:
    transaction_id = uuid.uuid4().hex
    created_directories: list[Path] = []
    committed: list[_SessionSyncFile] = []
    success = False
    try:
        _create_missing_session_directories(plan, created_directories)
        for index, entry in enumerate(plan.files):
            parent_fd = _open_verified_directory(
                entry.destination.parent,
                plan.directory_identities[entry.relative.parent],
            )
            try:
                _validate_destination(entry)
                entry.stage_name = (
                    f".workbench-sync-stage-{transaction_id}-{index:08d}"
                )
                _copy_validated_file_to_new(
                    entry.source,
                    entry.source_identity,
                    parent_fd,
                    entry.stage_name,
                    secure_mode=True,
                )
            finally:
                os.close(parent_fd)

        for index, entry in enumerate(plan.files):
            if entry.destination_identity is None:
                continue
            parent_fd = _open_verified_directory(
                entry.destination.parent,
                plan.directory_identities[entry.relative.parent],
            )
            try:
                _validate_destination(entry)
                entry.backup_name = (
                    f".workbench-sync-backup-{transaction_id}-{index:08d}"
                )
                _copy_validated_file_to_new(
                    entry.destination,
                    entry.destination_identity,
                    parent_fd,
                    entry.backup_name,
                    secure_mode=False,
                )
            finally:
                os.close(parent_fd)

        _fsync_sync_directories(plan)
        _validate_sync_plan_before_commit(plan)
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
                entry.stage_name = ""
                committed.append(entry)
                os.fsync(parent_fd)
            finally:
                os.close(parent_fd)
        success = True
    except BaseException:
        _rollback_session_sync(plan, committed)
        raise
    finally:
        _remove_sync_artifacts(plan)
        if not success:
            _remove_created_directories(created_directories)


def _create_missing_session_directories(
    plan: _SessionSyncPlan,
    created: list[Path],
) -> None:
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
        created.append(destination)


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


def _rollback_session_sync(
    plan: _SessionSyncPlan,
    committed: list[_SessionSyncFile],
) -> None:
    rollback_error: BaseException | None = None
    for entry in reversed(committed):
        try:
            parent_fd = _open_verified_directory(
                entry.destination.parent,
                plan.directory_identities[entry.relative.parent],
            )
            try:
                if entry.destination_identity is None:
                    os.unlink(entry.destination.name, dir_fd=parent_fd)
                else:
                    os.replace(
                        entry.backup_name,
                        entry.destination.name,
                        src_dir_fd=parent_fd,
                        dst_dir_fd=parent_fd,
                    )
                    entry.backup_name = ""
                os.fsync(parent_fd)
            finally:
                os.close(parent_fd)
        except BaseException as exc:
            rollback_error = rollback_error or exc
    if rollback_error is not None:
        raise ValueError(_SAFE_ERROR) from rollback_error


def _remove_sync_artifacts(plan: _SessionSyncPlan) -> None:
    touched: set[Path] = set()
    for entry in plan.files:
        parent_identity = plan.directory_identities.get(entry.relative.parent)
        if parent_identity is None:
            continue
        try:
            parent_fd = _open_verified_directory(entry.destination.parent, parent_identity)
        except (OSError, ValueError):
            continue
        try:
            for name in (entry.stage_name, entry.backup_name):
                if not name:
                    continue
                try:
                    os.unlink(name, dir_fd=parent_fd)
                except FileNotFoundError:
                    pass
            touched.add(entry.destination.parent)
        finally:
            os.close(parent_fd)
    for directory in touched:
        try:
            directory_fd = os.open(
                directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            )
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass


def _remove_created_directories(created: list[Path]) -> None:
    for directory in reversed(created):
        try:
            directory.rmdir()
        except OSError:
            pass


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
        if _identity_from_stat(initial) != expected or initial.st_uid != os.getuid():
            raise ValueError(_SAFE_ERROR)
        target_fd = os.open(
            destination_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=destination_fd,
        )
        while True:
            chunk = os.read(source_fd, 64 * 1024)
            if not chunk:
                break
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
