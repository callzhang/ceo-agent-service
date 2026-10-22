"""Stop admitting execution claims while a deployment drains current work.

The shared lock spans claim commit, never task execution. Existing tasks may
renew and finish, and intake may persist pending work, under an exclusive drain.
Closing the descriptor releases admission even when the deploy process exits.
"""

from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import time

QUIET_WAIT_SECONDS = 30 * 60
LOCK_POLL_SECONDS = 0.1


class DeploymentDrainTimeout(TimeoutError):
    """Admission could not drain before deployment; the service was not stopped."""


def _exclusive_until(lock, deadline: float):
    while True:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise DeploymentDrainTimeout(
                    "execution claim transactions did not drain; nothing was changed"
                ) from None
            time.sleep(min(LOCK_POLL_SECONDS, remaining))


def _open_lock(database_path: Path, name: str):
    path = Path(database_path).resolve()
    return os.fdopen(
        os.open(
            path.with_name(path.name + f".deployment-{name}.lock"),
            os.O_CREAT | os.O_RDWR,
            0o600,
        ),
        "a+b",
    )


@contextmanager
def deployment_drain(
    database_path: Path, *, timeout_seconds: float = QUIET_WAIT_SECONDS
):
    deadline = time.monotonic() + timeout_seconds
    # Close the short admission turnstile before waiting for already-admitted
    # claim transactions. New shared holders cannot starve the deployer.
    with _open_lock(database_path, "admission") as gate:
        _exclusive_until(gate, deadline)
        with _open_lock(database_path, "drain") as lock:
            _exclusive_until(lock, deadline)
            yield


@contextmanager
def claim_connection(store, *, immediate: bool = False):
    """Yield None during a drain; otherwise hold admission through DB commit."""
    with _open_lock(store.path, "drain") as lock:
        with _open_lock(store.path, "admission") as gate:
            try:
                fcntl.flock(gate.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                yield None
                return
            fcntl.flock(lock.fileno(), fcntl.LOCK_SH)
        connection = store._immediate_write_transaction if immediate else store._connect
        with connection() as db:
            yield db
