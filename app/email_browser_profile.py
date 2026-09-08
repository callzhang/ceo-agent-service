"""Owner-only persistent profile and cross-process lock for email browsing."""

from __future__ import annotations

from contextlib import contextmanager
from concurrent.futures import Future
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path
import queue
import secrets
import tempfile
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any


class EmailBrowserProfileError(ValueError):
    """The configured browser profile is unsafe or unavailable."""


@dataclass
class _LiveEmailBrowserSession:
    reference: str
    action_identity: str
    effect_digest: str
    context: object
    page: object
    adapter: object
    cleanup: Callable[[], None]
    profile_lease: Any
    expires_at: float
    expiry_timer: threading.Timer | None = None


class _BrowserOwnerThread:
    """Serialize every sync-Playwright operation onto its creating thread."""

    def __init__(self) -> None:
        self._commands: queue.Queue[tuple[Callable[[], object], Future[object]]] = (
            queue.Queue()
        )
        self._thread = threading.Thread(
            target=self._run,
            name="ceo-email-browser-owner",
            daemon=True,
        )
        self._thread.start()

    def _run(self) -> None:
        while True:
            callback, future = self._commands.get()
            if not future.set_running_or_notify_cancel():
                continue
            try:
                future.set_result(callback())
            except BaseException as exc:
                future.set_exception(exc)

    def call(self, callback: Callable[[], object]) -> object:
        future: Future[object] = Future()
        self._commands.put((callback, future))
        return future.result()


class _ThreadBoundBrowserAdapter:
    """Proxy browser adapter methods and assignments to the owner thread."""

    def __init__(self, manager: "EmailBrowserSessionManager", action_identity: str):
        object.__setattr__(self, "_manager", manager)
        object.__setattr__(self, "_action_identity", action_identity)

    def __getattr__(self, name: str) -> object:
        if name.startswith("_"):
            raise AttributeError(name)

        def invoke(*args: object, **kwargs: object) -> object:
            return self._manager._call_adapter(
                self._action_identity,
                name,
                args,
                kwargs,
            )

        return invoke

    def __setattr__(self, name: str, value: object) -> None:
        if name.startswith("_"):
            object.__setattr__(self, name, value)
            return
        self._manager._set_adapter_attribute(self._action_identity, name, value)


class EmailBrowserSessionManager:
    """Keep one real isolated browser page alive across Audit continuations."""

    def __init__(
        self,
        profile: "EmailBrowserProfile",
        *,
        clock: Callable[[], float] = time.monotonic,
        lease_ttl_seconds: float = 15 * 60,
    ) -> None:
        if lease_ttl_seconds <= 0:
            raise ValueError("email browser lease TTL must be positive")
        self.profile = profile
        self._clock = clock
        self._lease_ttl_seconds = lease_ttl_seconds
        self._owner = _BrowserOwnerThread()
        self._sessions: dict[str, _LiveEmailBrowserSession] = {}
        self._references: dict[str, str] = {}
        self._lock = threading.RLock()

    def _close_session(self, session: _LiveEmailBrowserSession) -> None:
        def close() -> object:
            try:
                session.cleanup()
            finally:
                session.profile_lease.__exit__(None, None, None)
            return None

        self._owner.call(close)

    def _expire_locked(self) -> set[str]:
        now = self._clock()
        expired = {
            identity
            for identity, session in self._sessions.items()
            if session.expires_at <= now
        }
        sessions = [self._sessions.pop(identity) for identity in expired]
        for session in sessions:
            if session.expiry_timer is not None:
                session.expiry_timer.cancel()
            self._references.pop(session.reference, None)
            self._close_session(session)
            try:
                self.profile.clear_audit_session(session.action_identity)
            except (EmailBrowserProfileError, OSError):
                pass
        return expired

    def expire_stale(self) -> int:
        """Release expired browser/context/profile leases immediately."""

        with self._lock:
            return len(self._expire_locked())

    def _session(self, action_identity: str) -> _LiveEmailBrowserSession:
        expired = self._expire_locked()
        session = self._sessions.get(action_identity)
        if session is None:
            if action_identity in expired:
                raise EmailBrowserProfileError("email browser session expired")
            raise EmailBrowserProfileError("email browser session is unavailable")
        return session

    def _call_adapter(
        self,
        action_identity: str,
        name: str,
        args: tuple[object, ...],
        kwargs: Mapping[str, object],
    ) -> object:
        with self._lock:
            session = self._session(action_identity)
            return self._owner.call(
                lambda: getattr(session.adapter, name)(*args, **kwargs)
            )

    def _set_adapter_attribute(
        self, action_identity: str, name: str, value: object
    ) -> None:
        with self._lock:
            session = self._session(action_identity)
            self._owner.call(lambda: setattr(session.adapter, name, value))

    def start(
        self,
        *,
        action_identity: str,
        effect_digest: str,
        open_session: Callable[
            [], tuple[object, object, object, Callable[[], None]]
        ],
    ) -> tuple[str, object]:
        with self._lock:
            self._expire_locked()
            if action_identity in self._sessions:
                raise EmailBrowserProfileError("email browser session already exists")

            def open_owned() -> object:
                lease = self.profile.lock()
                lease.__enter__()
                try:
                    context, page, adapter, cleanup = open_session()
                    if not callable(cleanup):
                        raise EmailBrowserProfileError(
                            "email browser cleanup is invalid"
                        )
                    return context, page, adapter, cleanup, lease
                except Exception:
                    lease.__exit__(None, None, None)
                    raise

            context, page, adapter, cleanup, lease = self._owner.call(open_owned)
            reference = "email-browser-session:" + secrets.token_hex(32)
            session = _LiveEmailBrowserSession(
                reference=reference,
                action_identity=action_identity,
                effect_digest=effect_digest,
                context=context,
                page=page,
                adapter=adapter,
                cleanup=cleanup,
                profile_lease=lease,
                expires_at=self._clock() + self._lease_ttl_seconds,
            )
            timer = threading.Timer(self._lease_ttl_seconds, self.expire_stale)
            timer.daemon = True
            session.expiry_timer = timer
            self._sessions[action_identity] = session
            self._references[reference] = action_identity
            timer.start()
            return reference, _ThreadBoundBrowserAdapter(self, action_identity)

    def resume(
        self,
        *,
        session_reference: str,
        action_identity: str,
        previous_effect_digest: str,
    ) -> object:
        with self._lock:
            if self._references.get(session_reference) != action_identity:
                raise EmailBrowserProfileError("email browser session binding changed")
            session = self._session(action_identity)
            if (
                session is None
                or session.reference != session_reference
                or session.effect_digest != previous_effect_digest
            ):
                raise EmailBrowserProfileError("email browser session binding changed")
            return _ThreadBoundBrowserAdapter(self, action_identity)

    def retain(self, action_identity: str, *, effect_digest: str) -> None:
        with self._lock:
            session = self._session(action_identity)
            session.effect_digest = effect_digest

    def handoff_action(
        self,
        action_identity: str,
        interaction: Callable[[object], object],
    ) -> object:
        """Expose only the live isolated page to an authenticated UI boundary."""

        if not callable(interaction):
            raise TypeError("browser handoff interaction must be callable")
        payload = self.profile.load_audit_session(action_identity)
        reference = payload.get("session_reference") if payload else None
        with self._lock:
            session = self._session(action_identity)
            if (
                not isinstance(reference, str)
                or session is None
                or session.reference != reference
            ):
                raise EmailBrowserProfileError("email browser session is unavailable")
            def interact() -> object:
                result = interaction(session.page)
                mark = getattr(session.adapter, "mark_external_interaction", None)
                if callable(mark):
                    mark()
                return result

            return self._owner.call(interact)

    def close_action(self, action_identity: str) -> None:
        with self._lock:
            self._expire_locked()
            session = self._sessions.pop(action_identity, None)
            if session is None:
                return
            if session.expiry_timer is not None:
                session.expiry_timer.cancel()
            self._references.pop(session.reference, None)
        self._close_session(session)


_SESSION_MANAGERS: dict[str, EmailBrowserSessionManager] = {}
_SESSION_MANAGERS_LOCK = threading.Lock()


def email_browser_session_manager(
    profile: "EmailBrowserProfile",
) -> EmailBrowserSessionManager:
    """Return the process-wide manager for one dedicated Email profile."""

    key = str(profile.profile_dir)
    with _SESSION_MANAGERS_LOCK:
        manager = _SESSION_MANAGERS.get(key)
        if manager is None:
            manager = EmailBrowserSessionManager(profile)
            _SESSION_MANAGERS[key] = manager
        return manager


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
