"""Shared IMAP sessions with bounded socket reads and command duration."""

from __future__ import annotations

from contextlib import contextmanager
import imaplib
import select
import socket
import ssl
import threading
import time
from typing import Any

IMAP_READ_TIMEOUT_SECONDS = 90.0
IMAP_COMMAND_TIMEOUT_SECONDS = 90.0


class _CommandDeadlineMixin:
    """Bound one IMAP command even when the peer keeps trickling bytes."""

    def __init__(self, *args: Any, command_timeout: float, **kwargs: Any) -> None:
        if command_timeout <= 0:
            raise ValueError("command_timeout must be positive")
        self._command_timeout = command_timeout
        self._command_lock = threading.Lock()
        self._command_timer: threading.Timer | None = None
        self._command_active = False
        self._command_expired = False
        self._command_deadline = 0.0
        self._operation_deadline: float | None = None
        self._active_deadline_scope = "command"
        self._active_deadline_seconds = command_timeout
        self._socket_timeout = kwargs.get("timeout")
        self._initialization_deadline_active = False
        self._start_command_deadline()
        self._initialization_deadline_active = True
        try:
            super().__init__(*args, **kwargs)
        except Exception as exc:
            expired = self._command_deadline_expired()
            self._initialization_deadline_active = False
            if expired and not (
                isinstance(exc, TimeoutError) and "total deadline" in str(exc)
            ):
                self._raise_command_timeout(exc)
            self._finish_command()
            raise
        else:
            self._initialization_deadline_active = False
            if self._command_deadline_expired():
                self._raise_command_timeout(
                    TimeoutError("IMAP initialization exceeded its total deadline")
                )
            self._finish_command()

    def open(self, *args: Any, **kwargs: Any) -> Any:
        result = super().open(*args, **kwargs)
        if self._initialization_deadline_active and self._command_deadline_expired():
            session_socket = getattr(self, "sock", None)
            if session_socket is not None:
                try:
                    session_socket.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
        return result

    @contextmanager
    def operation_deadline(self, timeout: float):
        """Bound a sequence of IMAP commands by one shared deadline."""
        if timeout <= 0:
            raise ValueError("operation timeout must be positive")
        with self._command_lock:
            if self._command_active:
                raise RuntimeError("cannot start an operation during an IMAP command")
            previous_deadline = self._operation_deadline
            candidate = time.monotonic() + timeout
            self._operation_deadline = (
                min(previous_deadline, candidate)
                if previous_deadline is not None
                else candidate
            )
        try:
            yield
        finally:
            with self._command_lock:
                if self._command_active:
                    raise RuntimeError("IMAP operation ended during an active command")
                active_deadline = self._operation_deadline
                self._operation_deadline = previous_deadline
            if active_deadline is not None and time.monotonic() >= active_deadline:
                self._active_deadline_scope = "operation"
                self._active_deadline_seconds = timeout
                self._raise_command_timeout(
                    TimeoutError("IMAP operation exceeded its total deadline")
                )

    def _start_command_deadline(self) -> None:
        with self._command_lock:
            if self._command_active:
                raise RuntimeError("IMAP commands cannot overlap on one session")
            now = time.monotonic()
            timeout = self._command_timeout
            self._active_deadline_scope = "command"
            if self._operation_deadline is not None:
                remaining = self._operation_deadline - now
                if remaining <= 0:
                    self._active_deadline_scope = "operation"
                    self._active_deadline_seconds = 0.0
                    expired_before_command = True
                else:
                    expired_before_command = False
                    if remaining < timeout:
                        timeout = remaining
                        self._active_deadline_scope = "operation"
            else:
                expired_before_command = False
            if not expired_before_command:
                self._active_deadline_seconds = timeout
                self._command_deadline = now + timeout
                self._command_active = True
                self._command_expired = False
                timer = threading.Timer(timeout, self._expire_command)
                timer.daemon = True
                self._command_timer = timer
                timer.start()
        if expired_before_command:
            self._raise_command_timeout(
                TimeoutError("IMAP operation exceeded its total deadline")
            )

    def _expire_command(self) -> None:
        with self._command_lock:
            if not self._command_active:
                return
            self._command_expired = True
            session_socket = getattr(self, "sock", None)
        if session_socket is None:
            return
        try:
            session_socket.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def _command_deadline_expired(self) -> bool:
        with self._command_lock:
            return self._command_expired or time.monotonic() >= self._command_deadline

    def _receive(self, size: int) -> bytes:
        """Receive without entering a blocking TLS read outside the deadline."""
        session_socket = getattr(self, "sock", None)
        if session_socket is None:
            return self.file.read(size)
        while True:
            remaining = self._command_deadline - time.monotonic()
            if remaining <= 0:
                self._raise_command_timeout(
                    TimeoutError("IMAP receive exceeded its total deadline")
                )
            wait_timeout = remaining
            if self._socket_timeout is not None:
                wait_timeout = min(wait_timeout, self._socket_timeout)
            try:
                session_socket.setblocking(False)
                try:
                    return session_socket.recv(size)
                except (BlockingIOError, ssl.SSLWantReadError):
                    pending = getattr(session_socket, "pending", None)
                    if callable(pending) and pending() > 0:
                        continue
                    readable, _, _ = select.select(
                        [session_socket], [], [], wait_timeout
                    )
                    if readable:
                        continue
                except ssl.SSLWantWriteError:
                    _, writable, _ = select.select(
                        [], [session_socket], [], wait_timeout
                    )
                    if writable:
                        continue
            finally:
                try:
                    session_socket.settimeout(self._socket_timeout)
                except OSError:
                    pass
            if self._command_deadline_expired():
                self._raise_command_timeout(
                    TimeoutError("IMAP receive exceeded its total deadline")
                )
            raise TimeoutError("IMAP socket idle read timed out")

    def _get_line(self) -> bytes:
        """Read one IMAP line with the remaining total deadline on each read.

        Closing an SSL socket from the timer thread does not reliably interrupt
        macOS ``SSLSocket`` buffered reads.  Read directly from the TLS socket,
        applying the remaining time before each receive, so a buffered file
        object cannot retain a blocking read after its deadline.
        """
        line = bytearray()
        while not line.endswith(b"\n"):
            remaining = self._command_deadline - time.monotonic()
            if remaining <= 0:
                self._raise_command_timeout(
                    TimeoutError("IMAP line read exceeded its total deadline")
                )
            session_socket = getattr(self, "sock", None)
            try:
                chunk = (
                    self._receive(1)
                    if session_socket is not None
                    else self.file.read(1)
                )
            except TimeoutError as exc:
                if self._command_deadline_expired():
                    self._raise_command_timeout(exc)
                raise
            if not chunk:
                raise self.abort("socket error: EOF")
            line.extend(chunk)
            if len(line) > imaplib._MAXLINE:
                raise self.error(f"got more than {imaplib._MAXLINE} bytes")
        if not line.endswith(b"\r\n"):
            raise self.abort(f"socket error: unterminated line: {bytes(line)!r}")
        result = bytes(line[:-2])
        if __debug__:
            if self.debug >= 4:
                self._mesg("< %r" % result)
            else:
                self._log("< %r" % result)
        return result

    def read(self, size: int) -> bytes:
        """Read an IMAP literal without allowing trickled bytes to extend it."""
        if size <= 0:
            return b""
        data = bytearray()
        while len(data) < size:
            remaining = self._command_deadline - time.monotonic()
            if remaining <= 0:
                self._raise_command_timeout(
                    TimeoutError("IMAP literal read exceeded its total deadline")
                )
            session_socket = getattr(self, "sock", None)
            try:
                read_size = min(size - len(data), imaplib._SAFE_BUF_SIZE)
                chunk = (
                    self._receive(read_size)
                    if session_socket is not None
                    else self.file.read1(read_size)
                )
            except TimeoutError as exc:
                if self._command_deadline_expired():
                    self._raise_command_timeout(exc)
                raise
            if not chunk:
                break
            data.extend(chunk)
        return bytes(data)

    def _finish_command(self) -> None:
        with self._command_lock:
            self._command_active = False
            timer = self._command_timer
            self._command_timer = None
        if timer is not None:
            timer.cancel()

    def _raise_command_timeout(self, cause: Exception) -> None:
        self._finish_command()
        try:
            self.file.close()
        except Exception:
            pass
        if self._active_deadline_scope == "operation":
            message = (
                "IMAP operation exceeded its "
                f"{self._active_deadline_seconds:g}s total deadline"
            )
        else:
            message = (
                "IMAP command exceeded its "
                f"{self._active_deadline_seconds:g}s total deadline"
            )
        raise TimeoutError(message) from cause

    def _command(self, name: str, *args: Any) -> bytes:
        initializing = self._initialization_deadline_active
        if not initializing:
            self._start_command_deadline()
        try:
            return super()._command(name, *args)
        except Exception as exc:
            if self._command_deadline_expired():
                self._raise_command_timeout(exc)
            if not initializing:
                self._finish_command()
            raise

    def _get_tagged_response(self, tag: bytes, expect_bye: bool = False) -> Any:
        try:
            return super()._get_tagged_response(tag, expect_bye=expect_bye)
        except Exception as exc:
            if self._command_deadline_expired():
                self._raise_command_timeout(exc)
            raise
        finally:
            if not self._initialization_deadline_active:
                self._finish_command()

    def _command_complete(self, name: str, tag: bytes) -> Any:
        try:
            result = super()._command_complete(name, tag)
            if self._command_deadline_expired():
                self._raise_command_timeout(
                    TimeoutError("IMAP command ended after its total deadline")
                )
            return result
        finally:
            if not self._initialization_deadline_active:
                self._finish_command()


class DeadlineIMAP4SSL(_CommandDeadlineMixin, imaplib.IMAP4_SSL):
    """TLS IMAP session with both idle-read and total-command deadlines."""


class _DeadlineIMAP4(_CommandDeadlineMixin, imaplib.IMAP4):
    """Plain IMAP variant used by socketpair regression tests."""
