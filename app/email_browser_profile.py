"""Owner-only persistent profile and cross-process lock for email browsing."""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
import time
from collections.abc import Iterator, Mapping


class EmailBrowserProfileError(ValueError):
    """The configured browser profile is unsafe or unavailable."""


class EmailBrowserProfile:
    """Validate and serialize use of a dedicated persistent browser profile."""

    def __init__(self, runtime_root: str | Path, *, name: str = "email-browser-profile") -> None:
        root = Path(runtime_root).expanduser()
        if not root.is_absolute():
            raise EmailBrowserProfileError("browser runtime root must be absolute")
        if not name or name in {".", ".."} or Path(name).name != name:
            raise EmailBrowserProfileError("browser profile name must be one directory")
        if root.exists() and root.is_symlink():
            raise EmailBrowserProfileError("browser runtime root must not be a symlink")
        self.runtime_root = root.resolve()
        profile_candidate = self.runtime_root / name
        if profile_candidate.exists() and profile_candidate.is_symlink():
            raise EmailBrowserProfileError("browser profile must not be a symlink")
        self.profile_dir = profile_candidate.resolve()
        self.lock_path = self.runtime_root / f".{name}.lock"
        self._validate_location()

    def _validate_location(self) -> None:
        if self.profile_dir == self.runtime_root:
            raise EmailBrowserProfileError("browser profile must be below runtime root")
        try:
            self.profile_dir.relative_to(self.runtime_root)
        except ValueError as exc:
            raise EmailBrowserProfileError(
                "browser profile must be below runtime root"
            ) from exc
        known_roots = {
            Path.home() / "Library/Application Support/Google/Chrome",
            Path.home() / "Library/Application Support/Chromium",
            Path.home() / "AppData/Local/Google/Chrome/User Data",
            Path.home() / "AppData/Local/Chromium/User Data",
        }
        for known_root in known_roots:
            known_root = known_root.resolve()
            if self.profile_dir == known_root:
                raise EmailBrowserProfileError("main browser profile is not allowed")
            try:
                self.profile_dir.relative_to(known_root)
            except ValueError:
                continue
            raise EmailBrowserProfileError("main browser profile is not allowed")

        if self.lock_path.exists() and self.lock_path.is_symlink():
            raise EmailBrowserProfileError("browser profile lock must not be a symlink")

    def prepare(self) -> Path:
        self._validate_location()
        self.runtime_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.runtime_root, 0o700)
        if self.profile_dir.exists() and not self.profile_dir.is_dir():
            raise EmailBrowserProfileError("browser profile path is not a directory")
        self.profile_dir.mkdir(mode=0o700, exist_ok=True)
        os.chmod(self.profile_dir, 0o700)
        if self.lock_path.exists() and not self.lock_path.is_file():
            raise EmailBrowserProfileError("browser profile lock is not a file")
        if not self.lock_path.exists():
            self.lock_path.touch(mode=0o600, exist_ok=False)
        os.chmod(self.lock_path, 0o600)
        return self.profile_dir

    def _audit_session_path(self, action_identity: str) -> Path:
        if not isinstance(action_identity, str) or not action_identity.strip():
            raise EmailBrowserProfileError(
                "browser audit session identity is invalid"
            )
        self.prepare()
        session_dir = self.profile_dir / "audit-sessions"
        if session_dir.exists() and session_dir.is_symlink():
            raise EmailBrowserProfileError(
                "browser audit session directory must not be a symlink"
            )
        session_dir.mkdir(mode=0o700, exist_ok=True)
        os.chmod(session_dir, 0o700)
        filename = sha256(action_identity.encode("utf-8")).hexdigest() + ".json"
        target = session_dir / filename
        if target.exists() and target.is_symlink():
            raise EmailBrowserProfileError(
                "browser audit session must not be a symlink"
            )
        return target

    def save_audit_session(
        self,
        action_identity: str,
        payload: Mapping[str, object],
    ) -> None:
        """Atomically retain private browser state only inside this profile."""

        try:
            encoded = json.dumps(
                dict(payload),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError, RecursionError) as exc:
            raise EmailBrowserProfileError(
                "browser audit session is invalid"
            ) from exc
        if len(encoded) > 2 * 1024 * 1024:
            raise EmailBrowserProfileError("browser audit session is too large")
        target = self._audit_session_path(action_identity)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".audit-session-",
            dir=target.parent,
        )
        temporary = Path(temporary_name)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                descriptor = -1
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
            os.chmod(target, 0o600)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if temporary.exists():
                temporary.unlink()

    def load_audit_session(
        self,
        action_identity: str,
    ) -> dict[str, object] | None:
        target = self._audit_session_path(action_identity)
        if not target.exists():
            return None
        try:
            if target.stat().st_size > 2 * 1024 * 1024:
                raise EmailBrowserProfileError(
                    "browser audit session is too large"
                )
            value = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as exc:
            raise EmailBrowserProfileError(
                "browser audit session is invalid"
            ) from exc
        if not isinstance(value, dict):
            raise EmailBrowserProfileError("browser audit session is invalid")
        return value

    def clear_audit_session(self, action_identity: str) -> None:
        target = self._audit_session_path(action_identity)
        if target.exists():
            target.unlink()

    @contextmanager
    def lock(self, *, timeout_seconds: float = 30.0) -> Iterator[Path]:
        if timeout_seconds < 0:
            raise ValueError("timeout_seconds must be non-negative")
        self.prepare()
        handle = self.lock_path.open("r+")
        deadline = time.monotonic() + timeout_seconds
        try:
            while True:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise EmailBrowserProfileError("browser profile is busy") from None
                    time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
            yield self.profile_dir
        finally:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()


def launch_persistent_email_context(
    playwright: object,
    profile: EmailBrowserProfile,
    *,
    headless: bool = True,
    **kwargs: object,
) -> object:
    """Launch Chromium only through the validated dedicated profile."""

    if headless is not True:
        raise EmailBrowserProfileError("email browser must be headless")
    profile_path = profile.prepare()
    chromium = getattr(playwright, "chromium", None)
    launch = getattr(chromium, "launch_persistent_context", None)
    if not callable(launch):
        raise EmailBrowserProfileError("Chromium persistent context is unavailable")
    return launch(
        user_data_dir=str(profile_path),
        headless=True,
        accept_downloads=False,
        **kwargs,
    )
