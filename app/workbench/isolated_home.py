"""Crash-recoverable ownership for temporary Codex homes."""

from __future__ import annotations

import errno
import fcntl
import json
import os
import stat
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

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

    def cleanup(self) -> None:
        if self.lock_fd < 0:
            return
        sync_error: BaseException | None = None
        try:
            if self.path.exists():
                _sync_sessions(self.path, self.source_home)
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
        metadata = os.fstat(source_fd)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
            raise ValueError(_SAFE_ERROR)
        source_parent_fd = os.open(
            source.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        )
        try:
            try:
                os.link(
                    source.name,
                    destination.name,
                    src_dir_fd=source_parent_fd,
                    dst_dir_fd=destination_fd,
                    follow_symlinks=False,
                )
                return
            except OSError as exc:
                if exc.errno != errno.EXDEV:
                    raise
        finally:
            os.close(source_parent_fd)
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
            secure_mode = stat.S_IMODE(metadata.st_mode) & 0o700
            os.fchmod(target_fd, secure_mode or 0o600)
        finally:
            os.close(target_fd)
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


def _sync_sessions(isolated_home: Path, source_home: Path) -> None:
    isolated_sessions = isolated_home / "sessions"
    if not isolated_sessions.exists():
        return
    _require_owned_directory(isolated_sessions)
    source_sessions = source_home / "sessions"
    if source_sessions.exists():
        _require_owned_directory(source_sessions)
    else:
        source_sessions.mkdir(mode=0o700)
        source_sessions.chmod(0o700)
    _sync_tree(isolated_sessions, source_sessions)


def _sync_tree(source: Path, destination: Path) -> None:
    destination_fd = os.open(
        destination, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    )
    try:
        for entry in os.scandir(source):
            if entry.is_symlink():
                continue
            target = destination / entry.name
            if entry.is_dir(follow_symlinks=False):
                if target.exists():
                    _require_owned_directory(target)
                else:
                    target.mkdir(mode=0o700)
                    target.chmod(0o700)
                _sync_tree(Path(entry.path), target)
            elif entry.is_file(follow_symlinks=False):
                _replace_regular_file(Path(entry.path), target, destination_fd)
    finally:
        os.close(destination_fd)


def _replace_regular_file(source: Path, destination: Path, destination_fd: int) -> None:
    if destination.is_symlink():
        raise ValueError(_SAFE_ERROR)
    source_fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
    temporary_name = f".workbench-sync-{uuid.uuid4().hex}"
    temporary_fd = -1
    try:
        metadata = os.fstat(source_fd)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
            raise ValueError(_SAFE_ERROR)
        temporary_fd = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=destination_fd,
        )
        while True:
            chunk = os.read(source_fd, 64 * 1024)
            if not chunk:
                break
            _write_all(temporary_fd, chunk)
        secure_mode = stat.S_IMODE(metadata.st_mode) & 0o700
        os.fchmod(temporary_fd, secure_mode or 0o600)
        os.close(temporary_fd)
        temporary_fd = -1
        os.replace(
            temporary_name,
            destination.name,
            src_dir_fd=destination_fd,
            dst_dir_fd=destination_fd,
        )
    finally:
        os.close(source_fd)
        if temporary_fd >= 0:
            os.close(temporary_fd)
        try:
            os.unlink(temporary_name, dir_fd=destination_fd)
        except FileNotFoundError:
            pass


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
