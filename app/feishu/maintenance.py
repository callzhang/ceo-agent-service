"""Bounded, network-free maintenance for persisted Feishu channel data."""
from __future__ import annotations

import errno
import hashlib
import os
import re
import stat
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath

from app.feishu.media import (
    DEFAULT_MAX_RESOURCE_BYTES,
    DEFAULT_MEDIA_PROCESSING_GRACE_SECONDS,
    MEDIA_ROOT_PARTS,
    feishu_media_content_lock,
)


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_TEMP_MEDIA_RE = re.compile(
    r"^\.([0-9a-f]{64})\.([0-9a-f]{32})\.tmp$"
)
_STALE_DOWNLOAD_SECONDS = 5 * 60


class FeishuMediaPurgeError(RuntimeError):
    """A closed, path-free local media cleanup failure."""

    def __init__(self, code: str):
        super().__init__(f"Feishu media purge failed:{code}")
        self.code = code


@dataclass(frozen=True)
class FeishuMediaPurgeResult:
    cutoff: str
    purged_assets: int
    deleted_files: int
    failures: int
    batches: int
    more_may_remain: bool
    expired_keys: int = 0
    deleted_orphans: int = 0


@dataclass(frozen=True)
class FeishuMaintenanceResult:
    cutoff: str
    deleted_events: int
    batches: int
    more_may_remain: bool
    purged_assets: int = 0
    deleted_files: int = 0
    failures: int = 0
    media_failures: int = 0
    media_batches: int = 0
    media_cutoff: str = ""
    expired_media_keys: int = 0
    deleted_media_orphans: int = 0


def _require_safe_workspace(workspace: Path) -> Path:
    """Return an absolute directory after rejecting symlinks in every level."""
    root = workspace.absolute()
    if not root.is_absolute():  # pragma: no cover - Path.absolute is absolute
        raise FeishuMediaPurgeError("path_validation_failed")
    current = Path(root.anchor)
    for part in root.parts[1:]:
        current = current / part
        try:
            metadata = os.lstat(current)
        except OSError as exc:
            raise FeishuMediaPurgeError("filesystem_error") from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise FeishuMediaPurgeError("symlink_rejected")
    try:
        metadata = os.lstat(root)
    except OSError as exc:
        raise FeishuMediaPurgeError("filesystem_error") from exc
    if not stat.S_ISDIR(metadata.st_mode):
        raise FeishuMediaPurgeError("not_regular_file")
    return root


def _unlink_content_addressed_media(
    workspace: Path,
    relative_path: str,
    expected_sha256: str,
    *,
    older_than_timestamp: float | None = None,
) -> bool:
    """Unlink one exact regular file using no-follow directory descriptors."""
    relative = PurePosixPath(relative_path)
    if relative.is_absolute() or not relative.parts:
        raise FeishuMediaPurgeError("path_validation_failed")
    root = _require_safe_workspace(workspace)
    directory_flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        directory_flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        directory_flags |= os.O_NOFOLLOW
    descriptor = -1
    try:
        descriptor = os.open(root, directory_flags)
        for part in relative.parts[:-1]:
            try:
                child = os.stat(part, dir_fd=descriptor, follow_symlinks=False)
            except FileNotFoundError:
                return False
            if stat.S_ISLNK(child.st_mode):
                raise FeishuMediaPurgeError("symlink_rejected")
            if not stat.S_ISDIR(child.st_mode):
                raise FeishuMediaPurgeError("not_regular_file")
            try:
                next_descriptor = os.open(
                    part, directory_flags, dir_fd=descriptor
                )
            except OSError as exc:
                if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                    raise FeishuMediaPurgeError("symlink_rejected") from exc
                if exc.errno == errno.ENOENT:
                    return False
                raise FeishuMediaPurgeError("filesystem_error") from exc
            os.close(descriptor)
            descriptor = next_descriptor

        leaf = relative.parts[-1]
        try:
            leaf_metadata = os.stat(
                leaf, dir_fd=descriptor, follow_symlinks=False
            )
        except FileNotFoundError:
            return False
        if stat.S_ISLNK(leaf_metadata.st_mode):
            raise FeishuMediaPurgeError("symlink_rejected")
        if not stat.S_ISREG(leaf_metadata.st_mode):
            raise FeishuMediaPurgeError("not_regular_file")
        file_flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            file_flags |= os.O_NOFOLLOW
        try:
            file_descriptor = os.open(leaf, file_flags, dir_fd=descriptor)
        except OSError as exc:
            if exc.errno == errno.ENOENT:
                return False
            if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                raise FeishuMediaPurgeError("symlink_rejected") from exc
            raise FeishuMediaPurgeError("filesystem_error") from exc
        try:
            opened = os.fstat(file_descriptor)
            if not stat.S_ISREG(opened.st_mode):
                raise FeishuMediaPurgeError("not_regular_file")
            if opened.st_size > DEFAULT_MAX_RESOURCE_BYTES:
                raise FeishuMediaPurgeError("file_size_exceeded")
            if (
                older_than_timestamp is not None
                and opened.st_mtime >= older_than_timestamp
            ):
                return False
            digest = hashlib.sha256()
            while True:
                chunk = os.read(file_descriptor, 1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
            if digest.hexdigest() != expected_sha256:
                raise FeishuMediaPurgeError("content_hash_mismatch")
            current = os.stat(leaf, dir_fd=descriptor, follow_symlinks=False)
            if (
                current.st_dev != opened.st_dev
                or current.st_ino != opened.st_ino
                or not stat.S_ISREG(current.st_mode)
            ):
                raise FeishuMediaPurgeError("filesystem_error")
        finally:
            os.close(file_descriptor)
        try:
            os.unlink(leaf, dir_fd=descriptor)
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise FeishuMediaPurgeError("filesystem_error") from exc
        try:
            os.fsync(descriptor)
        except OSError as exc:
            raise FeishuMediaPurgeError("filesystem_error") from exc
        return True
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _unlink_regular_media_entry(
    workspace: Path,
    relative_path: str,
    *,
    older_than_timestamp: float,
) -> bool:
    """Age-check and unlink one exact regular temp entry without following."""
    relative = PurePosixPath(relative_path)
    if relative.is_absolute() or not relative.parts:
        raise FeishuMediaPurgeError("path_validation_failed")
    root = _require_safe_workspace(workspace)
    directory_flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        directory_flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        directory_flags |= os.O_NOFOLLOW
    descriptor = -1
    try:
        descriptor = os.open(root, directory_flags)
        for part in relative.parts[:-1]:
            try:
                metadata = os.stat(part, dir_fd=descriptor, follow_symlinks=False)
            except FileNotFoundError:
                return False
            if stat.S_ISLNK(metadata.st_mode):
                raise FeishuMediaPurgeError("symlink_rejected")
            if not stat.S_ISDIR(metadata.st_mode):
                raise FeishuMediaPurgeError("not_regular_file")
            try:
                next_descriptor = os.open(part, directory_flags, dir_fd=descriptor)
            except OSError as exc:
                if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                    raise FeishuMediaPurgeError("symlink_rejected") from exc
                if exc.errno == errno.ENOENT:
                    return False
                raise FeishuMediaPurgeError("filesystem_error") from exc
            os.close(descriptor)
            descriptor = next_descriptor
        leaf = relative.parts[-1]
        file_flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            file_flags |= os.O_NOFOLLOW
        try:
            file_descriptor = os.open(leaf, file_flags, dir_fd=descriptor)
        except FileNotFoundError:
            return False
        except OSError as exc:
            if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                raise FeishuMediaPurgeError("symlink_rejected") from exc
            raise FeishuMediaPurgeError("filesystem_error") from exc
        try:
            opened = os.fstat(file_descriptor)
            if not stat.S_ISREG(opened.st_mode):
                raise FeishuMediaPurgeError("not_regular_file")
            if opened.st_mtime >= older_than_timestamp:
                return False
            current = os.stat(leaf, dir_fd=descriptor, follow_symlinks=False)
            if (
                current.st_dev != opened.st_dev
                or current.st_ino != opened.st_ino
                or not stat.S_ISREG(current.st_mode)
            ):
                raise FeishuMediaPurgeError("filesystem_error")
        finally:
            os.close(file_descriptor)
        try:
            os.unlink(leaf, dir_fd=descriptor)
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise FeishuMediaPurgeError("filesystem_error") from exc
        try:
            os.fsync(descriptor)
        except OSError as exc:
            raise FeishuMediaPurgeError("filesystem_error") from exc
        return True
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _media_root_if_present(workspace: Path) -> Path | None:
    current = _require_safe_workspace(workspace)
    for part in MEDIA_ROOT_PARTS:
        current = current / part
        try:
            metadata = os.lstat(current)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise FeishuMediaPurgeError("filesystem_error") from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise FeishuMediaPurgeError("symlink_rejected")
        if not stat.S_ISDIR(metadata.st_mode):
            raise FeishuMediaPurgeError("not_regular_file")
    return current


def _sweep_expired_feishu_media_orphans(
    store,
    *,
    workspace: Path,
    cutoff_timestamp: float,
    app_id: str,
    limit: int,
) -> tuple[int, int, bool]:
    """Delete only old, unreferenced digest files and abandoned temp files."""
    media_root = _media_root_if_present(workspace)
    if media_root is None:
        return 0, 0, False
    deleted = 0
    failures = 0
    inspected = 0
    inspection_limit = max(256, limit * 16)
    more_may_remain = False
    directory_flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        directory_flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        directory_flags |= os.O_NOFOLLOW

    for clear_app_id in store.list_feishu_media_app_ids(app_id=app_id):
        app_digest = hashlib.sha256(clear_app_id.encode("utf-8")).hexdigest()
        app_dir = media_root / app_digest
        try:
            app_descriptor = os.open(app_dir, directory_flags)
        except FileNotFoundError:
            continue
        except OSError:
            failures += 1
            continue
        try:
            with os.scandir(app_descriptor) as shards:
                for shard in shards:
                    inspected += 1
                    if inspected > inspection_limit:
                        more_may_remain = True
                        break
                    if (
                        len(shard.name) != 2
                        or any(char not in "0123456789abcdef" for char in shard.name)
                        or not shard.is_dir(follow_symlinks=False)
                    ):
                        continue
                    try:
                        shard_descriptor = os.open(
                            shard.name, directory_flags, dir_fd=app_descriptor
                        )
                    except OSError:
                        failures += 1
                        continue
                    try:
                        with os.scandir(shard_descriptor) as entries:
                            for entry in entries:
                                inspected += 1
                                if inspected > inspection_limit or deleted >= limit:
                                    more_may_remain = True
                                    break
                                try:
                                    metadata = entry.stat(follow_symlinks=False)
                                except OSError:
                                    failures += 1
                                    continue
                                if (
                                    not stat.S_ISREG(metadata.st_mode)
                                    or metadata.st_mtime >= cutoff_timestamp
                                ):
                                    continue
                                digest = ""
                                is_temp = False
                                if _SHA256_RE.fullmatch(entry.name):
                                    digest = entry.name
                                else:
                                    matched = _TEMP_MEDIA_RE.fullmatch(entry.name)
                                    if matched is not None:
                                        digest = matched.group(1)
                                        is_temp = True
                                if not digest or digest[:2] != shard.name:
                                    continue
                                relative = PurePosixPath(
                                    *MEDIA_ROOT_PARTS,
                                    app_digest,
                                    shard.name,
                                    entry.name,
                                ).as_posix()
                                try:
                                    with feishu_media_content_lock(
                                        workspace, clear_app_id, digest
                                    ):
                                        if is_temp:
                                            removed = _unlink_regular_media_entry(
                                                workspace,
                                                relative,
                                                older_than_timestamp=cutoff_timestamp,
                                            )
                                        else:
                                            if store.feishu_media_blob_is_referenced(
                                                app_id=clear_app_id,
                                                sha256=digest,
                                                relative_path=relative,
                                            ):
                                                continue
                                            removed = _unlink_content_addressed_media(
                                                workspace,
                                                relative,
                                                digest,
                                                older_than_timestamp=cutoff_timestamp,
                                            )
                                except (FeishuMediaPurgeError, OSError, ValueError):
                                    failures += 1
                                    continue
                                if removed:
                                    deleted += 1
                            if more_may_remain:
                                break
                    finally:
                        os.close(shard_descriptor)
                    if more_may_remain:
                        break
        finally:
            os.close(app_descriptor)
        if more_may_remain:
            break
    return deleted, failures, more_may_remain


def purge_expired_feishu_media(
    store,
    *,
    retention_days: int,
    app_id: str = "",
    workspace: Path | None = None,
    now: datetime | None = None,
    batch_limit: int = 100,
    max_batches: int = 20,
) -> FeishuMediaPurgeResult:
    """Mark and safely unlink expired media in bounded, recoverable batches."""
    if retention_days <= 0:
        raise ValueError("Feishu media retention_days must be positive")
    if batch_limit <= 0 or batch_limit > 1000 or max_batches <= 0:
        raise ValueError("Feishu media retention batch settings are invalid")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("Feishu media maintenance now must include timezone")
    cutoff = (
        current.astimezone(timezone.utc) - timedelta(days=retention_days)
    ).isoformat()
    root = Path(workspace) if workspace is not None else Path(store.path).parent
    root = _require_safe_workspace(root.absolute())
    purged_assets = 0
    deleted_files = 0
    failures = 0
    batches = 0
    last_marked = 0
    expired_keys = 0
    last_expired = 0
    downloading_stale_before = (
        current.astimezone(timezone.utc) - timedelta(seconds=_STALE_DOWNLOAD_SECONDS)
    ).isoformat()
    processing_stale_before = (
        current.astimezone(timezone.utc)
        - timedelta(seconds=DEFAULT_MEDIA_PROCESSING_GRACE_SECONDS)
    ).isoformat()
    for _ in range(max_batches):
        expired = store.expire_feishu_media_keys_before(
            cutoff,
            downloading_stale_before=downloading_stale_before,
            app_id=app_id,
            batch_limit=batch_limit,
        )
        last_expired = len(expired)
        expired_keys += last_expired
        if last_expired < batch_limit:
            break
    deleted_orphans, sweep_failures, sweep_more = (
        _sweep_expired_feishu_media_orphans(
            store,
            workspace=root,
            cutoff_timestamp=(
                current.astimezone(timezone.utc) - timedelta(days=retention_days)
            ).timestamp(),
            app_id=app_id,
            limit=batch_limit,
        )
    )
    failures += sweep_failures
    while batches < max_batches:
        marked = store.mark_feishu_media_purged_before(
            cutoff,
            app_id=app_id,
            batch_limit=batch_limit,
            processing_stale_before=processing_stale_before,
        )
        last_marked = len(marked)
        purged_assets += last_marked
        pending = store.list_feishu_media_pending_purge(
            app_id=app_id, limit=batch_limit
        )
        by_id = {asset.id: asset for asset in marked}
        for asset in pending:
            by_id.setdefault(asset.id, asset)
        candidates = list(by_id.values())[:batch_limit]
        if not candidates and not marked:
            break
        batches += 1
        made_progress = False
        for asset in candidates:
            try:
                with feishu_media_content_lock(
                    root, asset.app_id, asset.sha256
                ):
                    _, outcome = store.finalize_feishu_media_purge(
                        asset.id,
                        app_id=asset.app_id,
                        sha256=asset.sha256,
                        relative_path=asset.relative_path,
                        delete_file=lambda relative_path, digest, root=root: (
                            _unlink_content_addressed_media(
                                root, relative_path, digest
                            )
                        ),
                    )
            except FeishuMediaPurgeError as exc:
                failures += 1
                store.record_feishu_media_purge_failure(
                    asset.id, app_id=asset.app_id, error_code=exc.code
                )
                continue
            except (OSError, ValueError):
                failures += 1
                store.record_feishu_media_purge_failure(
                    asset.id,
                    app_id=asset.app_id,
                    error_code="path_validation_failed",
                )
                continue
            if outcome == "deleted":
                deleted_files += 1
                made_progress = True
            elif outcome in {"missing", "already_finalized", "shared_reference"}:
                made_progress = True
        if last_marked < batch_limit and not made_progress:
            break
    more_may_remain = bool(
        last_marked == batch_limit
        or last_expired == batch_limit
        or sweep_more
        or store.list_feishu_media_pending_purge(app_id=app_id, limit=1)
    )
    return FeishuMediaPurgeResult(
        cutoff=cutoff,
        purged_assets=purged_assets,
        deleted_files=deleted_files,
        failures=failures,
        batches=batches,
        more_may_remain=more_may_remain,
        expired_keys=expired_keys,
        deleted_orphans=deleted_orphans,
    )


def purge_expired_feishu_events(
    store,
    *,
    retention_days: int,
    app_id: str = "",
    now: datetime | None = None,
    batch_limit: int = 500,
    max_batches: int = 20,
    media_retention_days: int | None = None,
    media_workspace: Path | None = None,
    media_batch_limit: int = 100,
    media_max_batches: int = 20,
) -> FeishuMaintenanceResult:
    """Purge local media first, then remove eligible normalized event rows."""
    if retention_days <= 0:
        raise ValueError("Feishu retention_days must be positive")
    if batch_limit <= 0 or max_batches <= 0:
        raise ValueError("Feishu retention batch settings must be positive")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("Feishu maintenance now must include timezone")
    cutoff = (
        current.astimezone(timezone.utc) - timedelta(days=retention_days)
    ).isoformat()
    media_result: FeishuMediaPurgeResult | None = None
    if media_retention_days is not None:
        media_result = purge_expired_feishu_media(
            store,
            retention_days=media_retention_days,
            app_id=app_id,
            workspace=media_workspace,
            now=current,
            batch_limit=media_batch_limit,
            max_batches=media_max_batches,
        )
    total = 0
    batches = 0
    last_deleted = 0
    while batches < max_batches:
        last_deleted = store.purge_feishu_events_before(
            cutoff,
            app_id=app_id,
            batch_limit=batch_limit,
        )
        batches += 1
        total += last_deleted
        if last_deleted < batch_limit:
            break
    return FeishuMaintenanceResult(
        cutoff=cutoff,
        deleted_events=total,
        batches=batches,
        more_may_remain=(
            last_deleted == batch_limit
            or bool(media_result and media_result.more_may_remain)
        ),
        purged_assets=media_result.purged_assets if media_result else 0,
        deleted_files=media_result.deleted_files if media_result else 0,
        failures=media_result.failures if media_result else 0,
        media_failures=media_result.failures if media_result else 0,
        media_batches=media_result.batches if media_result else 0,
        media_cutoff=media_result.cutoff if media_result else "",
        expired_media_keys=media_result.expired_keys if media_result else 0,
        deleted_media_orphans=(
            media_result.deleted_orphans if media_result else 0
        ),
    )
