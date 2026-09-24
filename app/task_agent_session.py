"""Shared runtime session identity for Task extraction and lifecycle checks."""

import os
import threading
from uuid import uuid4

from app.store import AutoReplyStore

TASK_AGENT_SESSION_SCOPE_ID = "task-agent:work-tracking:v1"
TASK_AGENT_SESSION_RENEW_INTERVAL_SECONDS = 60.0


class TaskAgentSessionLeaseLost(RuntimeError):
    """The caller no longer owns the shared Task Agent runtime session."""


class TaskAgentSessionLease:
    def __init__(
        self,
        store: AutoReplyStore,
        *,
        owner: str,
        renew_interval_seconds: float = TASK_AGENT_SESSION_RENEW_INTERVAL_SECONDS,
    ) -> None:
        if renew_interval_seconds <= 0:
            raise ValueError("renew_interval_seconds must be positive")
        self.store = store
        self.owner = owner
        self.renew_interval_seconds = renew_interval_seconds
        self._stop_event = threading.Event()
        self._lost_event = threading.Event()
        self._renew_guard = threading.Lock()
        self._close_guard = threading.Lock()
        self._closed = False
        self._renew_thread = threading.Thread(
            target=self._renew_loop,
            name="task-agent-session-lease",
            daemon=True,
        )
        self._renew_thread.start()

    @classmethod
    def try_acquire(
        cls,
        store: AutoReplyStore,
        *,
        renew_interval_seconds: float = TASK_AGENT_SESSION_RENEW_INTERVAL_SECONDS,
    ) -> "TaskAgentSessionLease | None":
        owner = f"task-agent:{os.getpid()}:{uuid4().hex}"
        if not store.acquire_codex_session_lock(TASK_AGENT_SESSION_SCOPE_ID, owner):
            return None
        return cls(
            store,
            owner=owner,
            renew_interval_seconds=renew_interval_seconds,
        )

    def assert_owned(self) -> None:
        if self._lost_event.is_set():
            raise TaskAgentSessionLeaseLost(
                "Task Agent session lease is no longer owned by this worker"
            )
        with self._renew_guard:
            if self._lost_event.is_set():
                raise TaskAgentSessionLeaseLost(
                    "Task Agent session lease is no longer owned by this worker"
                )
            try:
                renewed = self.store.renew_codex_session_lock(
                    TASK_AGENT_SESSION_SCOPE_ID,
                    self.owner,
                )
            except Exception as exc:
                self._lost_event.set()
                raise TaskAgentSessionLeaseLost(
                    "Task Agent session lease renewal failed"
                ) from exc
            if not renewed:
                self._lost_event.set()
                raise TaskAgentSessionLeaseLost(
                    "Task Agent session lease is no longer owned by this worker"
                )

    def _renew_loop(self) -> None:
        while not self._stop_event.wait(self.renew_interval_seconds):
            with self._renew_guard:
                if self._stop_event.is_set():
                    return
                try:
                    renewed = self.store.renew_codex_session_lock(
                        TASK_AGENT_SESSION_SCOPE_ID,
                        self.owner,
                    )
                except Exception:
                    self._lost_event.set()
                    return
                if not renewed:
                    self._lost_event.set()
                    return

    def close(self) -> None:
        with self._close_guard:
            if self._closed:
                return
            self._closed = True
            self._stop_event.set()
            self._renew_thread.join()
            self.store.release_codex_session_lock(
                TASK_AGENT_SESSION_SCOPE_ID,
                self.owner,
            )

    def __enter__(self) -> "TaskAgentSessionLease":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
