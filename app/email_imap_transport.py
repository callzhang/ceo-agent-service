"""Shared IMAP sessions with bounded socket reads and command duration."""

from __future__ import annotations

import imaplib
import socket
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

    def _start_command_deadline(self) -> None:
        with self._command_lock:
            if self._command_active:
                raise RuntimeError("IMAP commands cannot overlap on one session")
            self._command_active = True
            self._command_expired = False
            self._command_deadline = time.monotonic() + self._command_timeout
            timer = threading.Timer(self._command_timeout, self._expire_command)
            timer.daemon = True
            self._command_timer = timer
            timer.start()

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

    def _get_line(self) -> bytes:
        """Read one IMAP line with the remaining total deadline on each read.

        Closing an SSL socket from the timer thread does not reliably interrupt
        macOS ``SSLSocket`` buffered reads.  Applying the remaining time to the
        socket before each byte read makes the deadline effective even when a
        peer trickles a line slowly and the socket's ordinary idle timeout keeps
        getting reset.
        """
        line = bytearray()
        while not line.endswith(b"\n"):
            remaining = self._command_deadline - time.monotonic()
            if remaining <= 0:
                self._raise_command_timeout(
                    TimeoutError("IMAP line read exceeded its total deadline")
                )
            read_timeout = remaining
            if self._socket_timeout is not None:
                read_timeout = min(read_timeout, self._socket_timeout)
            session_socket = getattr(self, "sock", None)
            if session_socket is not None:
                session_socket.settimeout(read_timeout)
            try:
                chunk = self.file.read(1)
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
        raise TimeoutError(
            f"IMAP command exceeded its {self._command_timeout:g}s total deadline"
        ) from cause

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
            return super()._command_complete(name, tag)
        finally:
            if not self._initialization_deadline_active:
                self._finish_command()


class DeadlineIMAP4SSL(_CommandDeadlineMixin, imaplib.IMAP4_SSL):
    """TLS IMAP session with both idle-read and total-command deadlines."""


class _DeadlineIMAP4(_CommandDeadlineMixin, imaplib.IMAP4):
    """Plain IMAP variant used by socketpair regression tests."""
