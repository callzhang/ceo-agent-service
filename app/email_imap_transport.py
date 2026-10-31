"""Shared IMAP sessions with bounded socket reads and command duration."""

from __future__ import annotations

import imaplib
import socket
import threading
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
        super().__init__(*args, **kwargs)

    def _start_command_deadline(self) -> None:
        with self._command_lock:
            if self._command_active:
                raise RuntimeError("IMAP commands cannot overlap on one session")
            self._command_active = True
            self._command_expired = False
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
            return self._command_expired

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
        self._start_command_deadline()
        try:
            return super()._command(name, *args)
        except Exception as exc:
            if self._command_deadline_expired():
                self._raise_command_timeout(exc)
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
            self._finish_command()

    def _command_complete(self, name: str, tag: bytes) -> Any:
        try:
            return super()._command_complete(name, tag)
        finally:
            self._finish_command()


class DeadlineIMAP4SSL(_CommandDeadlineMixin, imaplib.IMAP4_SSL):
    """TLS IMAP session with both idle-read and total-command deadlines."""


class _DeadlineIMAP4(_CommandDeadlineMixin, imaplib.IMAP4):
    """Plain IMAP variant used by socketpair regression tests."""
