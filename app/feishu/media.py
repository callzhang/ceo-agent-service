"""Fail-closed persistence for Feishu inbound message resources.

The Channel SDK is allowed to hand this module only resource bytes.  Remote
URLs are never accepted, file keys never leave the download boundary, and the
original remote file name is never used as a local path.  Successful payloads
are stored under a fixed, application-scoped, content-addressed directory in
the configured workspace.
"""
from __future__ import annotations

import asyncio
import fcntl
import hashlib
import inspect
import os
import stat
import unicodedata
from contextlib import contextmanager
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, TYPE_CHECKING
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from app.history import safe_observability_error

if TYPE_CHECKING:  # pragma: no cover - imports used only by type checkers
    from app.store import AutoReplyStore


DEFAULT_MAX_RESOURCE_BYTES = 20 * 1024 * 1024
DEFAULT_MAX_EVENT_BYTES = 32 * 1024 * 1024
DEFAULT_MAX_EVENT_RESOURCES = 8
DEFAULT_DOWNLOAD_TIMEOUT_SECONDS = 60.0
DEFAULT_MEDIA_PROCESSING_GRACE_SECONDS = 2 * 60 * 60
MEDIA_ROOT_PARTS = (".ceo-agent", "feishu-media")
MEDIA_LOCK_DIRECTORY = ".locks"
MEDIA_LOCK_POOL_SIZE = 256
MAX_IMAGE_DIMENSION = 16_384
MAX_IMAGE_PIXELS = 40_000_000

FeishuMediaStatus = Literal[
    "pending",
    "downloading",
    "ready",
    "rejected",
    "purged",
]


class FeishuMediaAsset(BaseModel):
    """One durable, app-bound inbound resource and its local resolution state."""

    model_config = ConfigDict(frozen=True)

    id: int
    event_record_id: int
    app_id: str
    message_id: str
    ordinal: int
    resource_type: str
    role: str = ""
    file_key: str = Field(default="", repr=False)
    file_key_sha256: str
    safe_name: str = ""
    duration_ms: int = 0
    status: FeishuMediaStatus = "pending"
    lease_token: str = ""
    relative_path: str = ""
    mime_type: str = ""
    size_bytes: int = 0
    sha256: str = ""
    error_code: str = ""
    error: str = ""
    locked_at: str = ""
    ready_at: str = ""
    purged_at: str = ""
    created_at: str = ""
    updated_at: str = ""


class FeishuMediaResolution(BaseModel):
    """Result of one claimed download, including the atomic enqueue signal."""

    model_config = ConfigDict(frozen=True)

    asset: FeishuMediaAsset
    event_ready_for_enqueue: bool = False


class FeishuMediaRejected(ValueError):
    """A deterministic, safe rejection carrying only a local error code."""

    def __init__(self, error_code: str, message: str = ""):
        super().__init__(message or error_code)
        self.error_code = error_code


def file_key_sha256(file_key: str) -> str:
    """Return the only file-key representation safe for logs and comparisons."""
    return hashlib.sha256(file_key.encode("utf-8")).hexdigest()


def safe_media_name(value: str, *, resource_type: str = "file") -> str:
    """Validate an untrusted display name without ever turning it into a path."""
    normalized = unicodedata.normalize("NFKC", str(value or "")).strip()
    if not normalized:
        return {
            "image": "image",
            "sticker": "sticker",
            "audio": "audio",
            "video": "video",
        }.get(resource_type, "attachment")
    if (
        normalized in {".", ".."}
        or Path(normalized).is_absolute()
        or "/" in normalized
        or "\\" in normalized
        or any(unicodedata.category(char) == "Cc" for char in normalized)
    ):
        raise FeishuMediaRejected("unsafe_file_name")
    if len(normalized) > 255 or len(normalized.encode("utf-8")) > 255:
        raise FeishuMediaRejected("file_name_too_long")
    return normalized


def _sniff_mime(data: bytes, resource_type: str) -> str:
    """Recognize a deliberately small set of safe, verifiable media formats."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff") and data.endswith(b"\xff\xd9"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        # GIF is frequently animated and requires a full decoder to establish
        # a safe frame/pixel budget.  Fail closed instead of handing it to a
        # downstream image decoder.
        raise FeishuMediaRejected("animated_image_unsupported")
    if (
        len(data) >= 12
        and data[:4] == b"RIFF"
        and data[8:12] == b"WEBP"
    ):
        return "image/webp"
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "audio/wav"
    if data.startswith(b"ID3") or (
        len(data) >= 2 and data[0] == 0xFF and data[1] & 0xE0 == 0xE0
    ):
        return "audio/mpeg"
    if data.startswith(b"OggS"):
        return "audio/ogg" if resource_type == "audio" else "video/ogg"
    if len(data) >= 12 and data[4:8] == b"ftyp":
        return "audio/mp4" if resource_type == "audio" else "video/mp4"
    if data.startswith(b"\x1aE\xdf\xa3"):
        return "video/webm"
    if data and b"\x00" not in data:
        try:
            decoded = data.decode("utf-8")
        except UnicodeDecodeError:
            pass
        else:
            disallowed = sum(
                1
                for char in decoded
                if unicodedata.category(char) == "Cc"
                and char not in "\n\r\t"
            )
            if disallowed == 0:
                return "text/plain"
    raise FeishuMediaRejected("unsupported_media_type")


def _normalize_declared_mime(value: str) -> str:
    mime = str(value or "").split(";", 1)[0].strip().lower()
    return {
        "image/jpg": "image/jpeg",
        "audio/x-wav": "audio/wav",
    }.get(mime, mime)


def verified_media_mime(
    data: bytes,
    *,
    resource_type: str,
    declared_mime: str = "",
) -> str:
    """Verify magic bytes, declared MIME, and the resource-kind allowlist."""
    sniffed = _sniff_mime(data, resource_type)
    declared = _normalize_declared_mime(declared_mime)
    if declared and declared != "application/octet-stream" and declared != sniffed:
        raise FeishuMediaRejected("mime_mismatch")
    allowed: dict[str, frozenset[str]] = {
        "image": frozenset({"image/png", "image/jpeg", "image/webp"}),
        "sticker": frozenset({"image/png", "image/jpeg", "image/webp"}),
        "audio": frozenset(
            {"audio/mpeg", "audio/wav", "audio/ogg", "audio/mp4"}
        ),
        "video": frozenset(
            {"video/mp4", "video/webm", "video/ogg"}
        ),
        "file": frozenset(
            {
                "application/pdf",
                "text/plain",
                "image/png",
                "image/jpeg",
                "image/webp",
            }
        ),
    }
    if resource_type not in allowed or sniffed not in allowed[resource_type]:
        raise FeishuMediaRejected("resource_type_mismatch")
    if sniffed.startswith("image/"):
        _validate_image_structure(data, sniffed)
    return sniffed


def _validate_image_dimensions(width: int, height: int) -> None:
    if (
        width <= 0
        or height <= 0
        or width > MAX_IMAGE_DIMENSION
        or height > MAX_IMAGE_DIMENSION
        or width * height > MAX_IMAGE_PIXELS
    ):
        raise FeishuMediaRejected("image_dimensions_exceeded")


def _validate_png_structure(data: bytes) -> None:
    offset = 8
    saw_header = False
    saw_end = False
    while offset + 12 <= len(data):
        chunk_size = int.from_bytes(data[offset : offset + 4], "big")
        chunk_type = data[offset + 4 : offset + 8]
        chunk_end = offset + 12 + chunk_size
        if chunk_end > len(data):
            raise FeishuMediaRejected("invalid_image_structure")
        payload = data[offset + 8 : offset + 8 + chunk_size]
        if not saw_header:
            if chunk_type != b"IHDR" or chunk_size != 13:
                raise FeishuMediaRejected("invalid_image_structure")
            _validate_image_dimensions(
                int.from_bytes(payload[0:4], "big"),
                int.from_bytes(payload[4:8], "big"),
            )
            saw_header = True
        elif chunk_type == b"IHDR":
            raise FeishuMediaRejected("invalid_image_structure")
        if chunk_type in {b"acTL", b"fcTL", b"fdAT"}:
            raise FeishuMediaRejected("animated_image_unsupported")
        offset = chunk_end
        if chunk_type == b"IEND":
            if chunk_size != 0:
                raise FeishuMediaRejected("invalid_image_structure")
            saw_end = True
            break
    if not saw_header or not saw_end:
        raise FeishuMediaRejected("invalid_image_structure")


def _validate_jpeg_structure(data: bytes) -> None:
    offset = 2
    saw_dimensions = False
    start_of_frame = frozenset(
        {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
    )
    standalone = frozenset({0x01, *range(0xD0, 0xDA)})
    while offset < len(data) - 1:
        if data[offset] != 0xFF:
            # Entropy-coded data starts after SOS.  A valid SOF must already
            # have established a bounded decoded image size.
            break
        while offset < len(data) and data[offset] == 0xFF:
            offset += 1
        if offset >= len(data):
            break
        marker = data[offset]
        offset += 1
        if marker in standalone:
            continue
        if marker == 0xD9:
            break
        if offset + 2 > len(data):
            raise FeishuMediaRejected("invalid_image_structure")
        segment_size = int.from_bytes(data[offset : offset + 2], "big")
        if segment_size < 2 or offset + segment_size > len(data):
            raise FeishuMediaRejected("invalid_image_structure")
        if marker in start_of_frame:
            if segment_size < 7:
                raise FeishuMediaRejected("invalid_image_structure")
            _validate_image_dimensions(
                int.from_bytes(data[offset + 3 : offset + 5], "big"),
                int.from_bytes(data[offset + 5 : offset + 7], "big"),
            )
            saw_dimensions = True
        offset += segment_size
        if marker == 0xDA:
            break
    if not saw_dimensions:
        raise FeishuMediaRejected("invalid_image_structure")


def _validate_webp_structure(data: bytes) -> None:
    if len(data) < 20:
        raise FeishuMediaRejected("invalid_image_structure")
    declared_size = int.from_bytes(data[4:8], "little") + 8
    if declared_size > len(data):
        raise FeishuMediaRejected("invalid_image_structure")
    offset = 12
    dimensions: tuple[int, int] | None = None
    while offset + 8 <= declared_size:
        chunk_type = data[offset : offset + 4]
        chunk_size = int.from_bytes(data[offset + 4 : offset + 8], "little")
        payload_start = offset + 8
        payload_end = payload_start + chunk_size
        if payload_end > declared_size:
            raise FeishuMediaRejected("invalid_image_structure")
        payload = data[payload_start:payload_end]
        if chunk_type in {b"ANIM", b"ANMF"}:
            raise FeishuMediaRejected("animated_image_unsupported")
        if chunk_type == b"VP8X":
            if chunk_size != 10:
                raise FeishuMediaRejected("invalid_image_structure")
            if payload[0] & 0x02:
                raise FeishuMediaRejected("animated_image_unsupported")
            dimensions = (
                1 + int.from_bytes(payload[4:7], "little"),
                1 + int.from_bytes(payload[7:10], "little"),
            )
        elif chunk_type == b"VP8 ":
            if chunk_size < 10 or payload[3:6] != b"\x9d\x01\x2a":
                raise FeishuMediaRejected("invalid_image_structure")
            dimensions = (
                int.from_bytes(payload[6:8], "little") & 0x3FFF,
                int.from_bytes(payload[8:10], "little") & 0x3FFF,
            )
        elif chunk_type == b"VP8L":
            if chunk_size < 5 or payload[0] != 0x2F:
                raise FeishuMediaRejected("invalid_image_structure")
            dimensions = (
                1 + payload[1] + ((payload[2] & 0x3F) << 8),
                1
                + (payload[2] >> 6)
                + (payload[3] << 2)
                + ((payload[4] & 0x0F) << 10),
            )
        if dimensions is not None:
            # Validate every declaration immediately.  In extended WebP the
            # VP8X canvas is authoritative and must not be overwritten by a
            # later, smaller frame header to bypass the decoded-pixel budget.
            _validate_image_dimensions(*dimensions)
        offset = payload_end + (chunk_size & 1)
    if dimensions is None:
        raise FeishuMediaRejected("invalid_image_structure")


def _validate_image_structure(data: bytes, mime_type: str) -> None:
    if mime_type == "image/png":
        _validate_png_structure(data)
    elif mime_type == "image/jpeg":
        _validate_jpeg_structure(data)
    elif mime_type == "image/webp":
        _validate_webp_structure(data)
    else:  # pragma: no cover - enforced by the caller's allowlist
        raise FeishuMediaRejected("unsupported_media_type")


def _unpack_download(value: Any) -> tuple[bytes, str]:
    """Accept bytes or a small typed SDK facade result, never paths or URLs."""
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value), ""
    if isinstance(value, tuple) and len(value) == 2:
        data, mime_type = value
    else:
        data = getattr(value, "data", None)
        if data is None:
            data = getattr(value, "content", None)
        mime_type = getattr(value, "mime_type", "")
    if not isinstance(data, (bytes, bytearray, memoryview)):
        # A URL/string/path is intentionally not dereferenced here.
        raise FeishuMediaRejected("invalid_download_result")
    return bytes(data), str(mime_type or "")


def _assert_secure_directory(path: Path, *, create: bool = True) -> None:
    """Create/check a private real directory without following leaf symlinks."""
    if create:
        try:
            path.mkdir(mode=0o700, parents=False, exist_ok=True)
        except FileExistsError:
            pass
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise FeishuMediaRejected("unsafe_media_directory")
    path.chmod(0o700)


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISDIR(metadata.st_mode):
            raise FeishuMediaRejected("unsafe_media_directory")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _prepare_media_root(workspace: Path) -> Path:
    root = Path(workspace)
    if not root.exists() or not root.is_dir():
        raise FeishuMediaRejected("unsafe_media_directory")
    if stat.S_ISLNK(root.lstat().st_mode):
        raise FeishuMediaRejected("unsafe_media_directory")
    current = root
    for part in MEDIA_ROOT_PARTS:
        current = current / part
        _assert_secure_directory(current)
    return current


@contextmanager
def feishu_media_content_lock(
    workspace: Path, app_id: str, sha256: str
) -> Iterator[None]:
    """Serialize content publication/deletion across service processes."""
    cleaned_app_id = str(app_id or "").strip()
    digest = str(sha256 or "").strip().lower()
    if not cleaned_app_id:
        raise FeishuMediaRejected("invalid_media_lock_identity")
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise FeishuMediaRejected("invalid_media_lock_identity")
    media_root = _prepare_media_root(Path(workspace))
    locks = media_root / MEDIA_LOCK_DIRECTORY
    _assert_secure_directory(locks)
    app_digest = hashlib.sha256(cleaned_app_id.encode("utf-8")).hexdigest()
    app_locks = locks / app_digest
    _assert_secure_directory(app_locks)
    # A fixed 256-slot pool prevents untrusted unique content digests from
    # creating an unbounded number of lock inodes.  Every digest maps
    # deterministically to one slot; collisions only reduce concurrency.
    slot = int(digest[:2], 16) % MEDIA_LOCK_POOL_SIZE
    lock_path = app_locks / f"{slot:02x}"
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as exc:
        raise FeishuMediaRejected("unsafe_media_lock") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise FeishuMediaRejected("unsafe_media_lock")
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


@dataclass(frozen=True)
class _StoredContent:
    relative_path: str
    created: bool


class FeishuMediaResolver:
    """Resolve claimed inbound resources through one connected Feishu client."""

    def __init__(
        self,
        *,
        store: "AutoReplyStore",
        client: Any,
        workspace: Path,
        max_resource_bytes: int = DEFAULT_MAX_RESOURCE_BYTES,
        max_event_bytes: int = DEFAULT_MAX_EVENT_BYTES,
        max_event_resources: int = DEFAULT_MAX_EVENT_RESOURCES,
        download_timeout_seconds: float = DEFAULT_DOWNLOAD_TIMEOUT_SECONDS,
    ):
        if max_resource_bytes <= 0 or max_event_bytes <= 0:
            raise ValueError("Feishu media byte limits must be positive")
        if max_resource_bytes > max_event_bytes:
            raise ValueError("resource byte limit cannot exceed event byte limit")
        if (
            max_event_resources <= 0
            or max_event_resources > DEFAULT_MAX_EVENT_RESOURCES
        ):
            raise ValueError(
                "Feishu event resource limit must be between 1 and 8"
            )
        if download_timeout_seconds <= 0:
            raise ValueError("Feishu media download timeout must be positive")
        client_app_id = str(getattr(client, "app_id", "") or "").strip()
        if not client_app_id:
            raise ValueError("Feishu media client App ID is required")

        raw_workspace = Path(workspace)
        if not raw_workspace.exists() or not raw_workspace.is_dir():
            raise ValueError("Feishu media workspace must be an existing directory")
        self.workspace = raw_workspace.resolve(strict=True)
        self.store = store
        self.client = client
        self.app_id = client_app_id
        self.max_resource_bytes = max_resource_bytes
        self.max_event_bytes = max_event_bytes
        self.max_event_resources = max_event_resources
        self.download_timeout_seconds = download_timeout_seconds
        self.media_root = self.workspace.joinpath(*MEDIA_ROOT_PARTS)

    def _prepare_root(self) -> None:
        _prepare_media_root(self.workspace)

    def _store_content(self, data: bytes, sha256: str) -> _StoredContent:
        self._prepare_root()
        app_digest = hashlib.sha256(self.app_id.encode("utf-8")).hexdigest()
        app_dir = self.media_root / app_digest
        shard_dir = app_dir / sha256[:2]
        _assert_secure_directory(app_dir)
        _assert_secure_directory(shard_dir)
        destination = shard_dir / sha256
        relative = destination.relative_to(self.workspace).as_posix()
        temp = shard_dir / f".{sha256}.{uuid4().hex}.tmp"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(temp, flags, 0o600)
        created = False
        shard_fd = -1
        destination_fd = -1
        try:
            with os.fdopen(fd, "wb", closefd=True) as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            directory_flags = os.O_RDONLY
            if hasattr(os, "O_DIRECTORY"):
                directory_flags |= os.O_DIRECTORY
            if hasattr(os, "O_NOFOLLOW"):
                directory_flags |= os.O_NOFOLLOW
            shard_fd = os.open(shard_dir, directory_flags)
            try:
                os.link(
                    temp,
                    sha256,
                    dst_dir_fd=shard_fd,
                    follow_symlinks=False,
                )
                created = True
                # The directory entry must be durable before SQLite can
                # commit a ready reference to it.
                _fsync_directory(shard_dir)
            except FileExistsError:
                pass

            destination_flags = os.O_RDONLY
            if hasattr(os, "O_NOFOLLOW"):
                destination_flags |= os.O_NOFOLLOW
            try:
                destination_fd = os.open(
                    sha256, destination_flags, dir_fd=shard_fd
                )
            except OSError as exc:
                raise FeishuMediaRejected("unsafe_media_destination") from exc
            info = os.fstat(destination_fd)
            if not stat.S_ISREG(info.st_mode):
                raise FeishuMediaRejected("unsafe_media_destination")
            if info.st_size != len(data):
                raise FeishuMediaRejected("content_address_collision")
            hasher = hashlib.sha256()
            while True:
                chunk = os.read(destination_fd, 1024 * 1024)
                if not chunk:
                    break
                hasher.update(chunk)
            if hasher.hexdigest() != sha256:
                raise FeishuMediaRejected("content_address_collision")
            os.fchmod(destination_fd, 0o600)
        finally:
            if destination_fd >= 0:
                os.close(destination_fd)
            if shard_fd >= 0:
                os.close(shard_fd)
            try:
                temp.unlink()
            except FileNotFoundError:
                pass
            else:
                _fsync_directory(shard_dir)
        return _StoredContent(relative_path=relative, created=created)

    def _remove_new_content(
        self, stored: _StoredContent | None, *, sha256: str
    ) -> None:
        if stored is None or not stored.created:
            return
        # A prior ready row can legitimately reference a missing digest.  If
        # this resolution repaired that file but its own CAS rejected/failed,
        # preserve the repaired shared content instead of deleting it again.
        if self.store.feishu_media_blob_is_referenced(
            app_id=self.app_id,
            sha256=sha256,
            relative_path=stored.relative_path,
        ):
            return
        candidate = self.workspace / stored.relative_path
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            return
        if stat.S_ISREG(info.st_mode) and not stat.S_ISLNK(info.st_mode):
            candidate.unlink()
            _fsync_directory(candidate.parent)

    async def _download(self, asset: FeishuMediaAsset) -> tuple[bytes, str]:
        if asset.resource_type == "sticker":
            # The official API does not expose sticker payload download.  A
            # sticker file key must never be reinterpreted as an image key.
            raise FeishuMediaRejected("sticker_download_unsupported")
        method = getattr(self.client, "download_inbound_resource", None)
        if method is None or not callable(method):
            raise FeishuMediaRejected("download_unavailable")
        result = method(
            app_id=asset.app_id,
            message_id=asset.message_id,
            file_key=asset.file_key,
            resource_type=asset.resource_type,
            max_bytes=self.max_resource_bytes + 1,
        )
        if inspect.isawaitable(result):
            result = await asyncio.wait_for(
                result, timeout=self.download_timeout_seconds
            )
        return _unpack_download(result)

    async def resolve_claimed(
        self, asset: FeishuMediaAsset
    ) -> FeishuMediaResolution:
        """Download and terminally resolve exactly the lease-owned asset."""
        if asset.app_id != self.app_id:
            raise PermissionError("Feishu media App ID does not match client")
        if asset.status != "downloading" or not asset.lease_token:
            raise ValueError("Feishu media asset is not actively claimed")

        stored: _StoredContent | None = None
        try:
            data, declared_mime = await self._download(asset)
            if not data:
                raise FeishuMediaRejected("empty_download")
            if len(data) > self.max_resource_bytes:
                raise FeishuMediaRejected("resource_too_large")
            mime_type = verified_media_mime(
                data,
                resource_type=asset.resource_type,
                declared_mime=declared_mime,
            )
            digest = hashlib.sha256(data).hexdigest()
            with feishu_media_content_lock(
                self.workspace, asset.app_id, digest
            ):
                stored = self._store_content(data, digest)
                try:
                    resolved, enqueue = self.store.mark_feishu_media_ready(
                        asset.id,
                        event_record_id=asset.event_record_id,
                        app_id=asset.app_id,
                        message_id=asset.message_id,
                        file_key=asset.file_key,
                        resource_type=asset.resource_type,
                        lease_token=asset.lease_token,
                        relative_path=stored.relative_path,
                        mime_type=mime_type,
                        size_bytes=len(data),
                        sha256=digest,
                        max_event_bytes=self.max_event_bytes,
                    )
                except Exception:
                    self._remove_new_content(stored, sha256=digest)
                    raise
                if resolved.status != "ready":
                    self._remove_new_content(stored, sha256=digest)
            return FeishuMediaResolution(
                asset=resolved,
                event_ready_for_enqueue=enqueue,
            )
        except (asyncio.TimeoutError, TimeoutError):
            error_code = "download_timeout"
            safe_error = error_code
        except FeishuMediaRejected as exc:
            error_code = exc.error_code
            safe_error = error_code
        except Exception as exc:  # fail closed without persisting secrets/paths
            error_code = "download_failed"
            safe_error = safe_observability_error(type(exc).__name__)[:128]
        rejected, enqueue = self.store.mark_feishu_media_rejected(
            asset.id,
            event_record_id=asset.event_record_id,
            app_id=asset.app_id,
            message_id=asset.message_id,
            file_key=asset.file_key,
            resource_type=asset.resource_type,
            lease_token=asset.lease_token,
            error_code=error_code,
            error=safe_error,
        )
        return FeishuMediaResolution(
            asset=rejected,
            event_ready_for_enqueue=enqueue,
        )

    async def resolve_pending(self, *, limit: int = 8) -> list[FeishuMediaResolution]:
        """Claim and resolve a bounded batch for the connected application."""
        if limit <= 0:
            return []
        if limit > self.max_event_resources:
            raise ValueError("Feishu media resolve limit is too large")
        results: list[FeishuMediaResolution] = []
        for _ in range(limit):
            asset = self.store.claim_feishu_media_asset(self.app_id)
            if asset is None:
                break
            results.append(await self.resolve_claimed(asset))
        return results
