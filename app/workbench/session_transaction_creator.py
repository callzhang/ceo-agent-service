"""Single-purpose crash-surviving creator for session-sync transaction roots."""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import stat
import time
from collections.abc import Callable


_VERSION = 2
_MARKER_NAME = ".transaction.json"
_INTENT_PREFIX = ".transaction-creation-"
_SAFE_FAILURE = b'{"ok":false}'
_TRANSACTION_ID = re.compile(r"^[0-9a-f]{32}$")
_NONCE = re.compile(r"^[0-9a-f]{64}$")


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii")


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError("short write")
        view = view[written:]


def _intent_name(transaction_id: str) -> str:
    return f"{_INTENT_PREFIX}{transaction_id}.json"


def _intent_payload(transaction_id: str, nonce: str) -> bytes:
    return _canonical(
        {
            "creation_nonce": nonce,
            "transaction_id": transaction_id,
            "version": _VERSION,
        }
    )


def _validate_state_directory(state_fd: int) -> None:
    metadata = os.fstat(state_fd)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise ValueError("invalid state directory")


def _validate_intent(state_fd: int, transaction_id: str, nonce: str) -> None:
    fd = os.open(
        _intent_name(transaction_id),
        os.O_RDONLY | os.O_NOFOLLOW,
        dir_fd=state_fd,
    )
    try:
        metadata = os.fstat(fd)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_size > 512
        ):
            raise ValueError("invalid creation intent")
        data = os.read(fd, 513)
        if data != _intent_payload(transaction_id, nonce):
            raise ValueError("invalid creation intent")
    finally:
        os.close(fd)


def _write_marker(transaction_fd: int, transaction_id: str, nonce: str) -> None:
    marker_fd = os.open(
        _MARKER_NAME,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
        dir_fd=transaction_fd,
    )
    try:
        _write_all(
            marker_fd,
            _canonical(
                {
                    "creation_nonce": nonce,
                    "transaction_id": transaction_id,
                    "uid": os.getuid(),
                    "version": _VERSION,
                }
            ),
        )
        os.fchmod(marker_fd, 0o600)
        os.fsync(marker_fd)
    finally:
        os.close(marker_fd)


def _remove_own_failed_root(
    state_fd: int,
    name: str,
    transaction_fd: int,
    identity: tuple[int, int],
) -> None:
    try:
        path_metadata = os.stat(name, dir_fd=state_fd, follow_symlinks=False)
        opened_metadata = os.fstat(transaction_fd)
        if (
            not stat.S_ISDIR(path_metadata.st_mode)
            or (path_metadata.st_dev, path_metadata.st_ino) != identity
            or (opened_metadata.st_dev, opened_metadata.st_ino) != identity
        ):
            return
        for child in os.listdir(transaction_fd):
            if child != _MARKER_NAME:
                return
        try:
            os.unlink(_MARKER_NAME, dir_fd=transaction_fd)
        except FileNotFoundError:
            pass
        os.fsync(transaction_fd)
        os.rmdir(name, dir_fd=state_fd)
        os.fsync(state_fd)
    except OSError:
        return


def create_marked_transaction(
    state_fd: int,
    transaction_id: str,
    nonce: str,
    *,
    checkpoint_fd: int = -1,
    checkpoint_delay_seconds: float = 0.0,
    marker_writer: Callable[[int, str, str], None] | None = None,
) -> tuple[int, int]:
    """Create, durably mark, and return the owned directory identity."""

    if not _TRANSACTION_ID.fullmatch(transaction_id) or not _NONCE.fullmatch(nonce):
        raise ValueError("invalid transaction identity")
    _validate_state_directory(state_fd)
    _validate_intent(state_fd, transaction_id, nonce)
    name = f"tx-{transaction_id}"
    created = False
    transaction_fd = -1
    identity = (0, 0)
    try:
        os.mkdir(name, mode=0o700, dir_fd=state_fd)
        created = True
        transaction_fd = os.open(
            name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=state_fd,
        )
        metadata = os.fstat(transaction_fd)
        identity = (metadata.st_dev, metadata.st_ino)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o700
        ):
            raise ValueError("invalid transaction directory")
        if checkpoint_fd >= 0:
            _write_all(checkpoint_fd, b"created")
        if checkpoint_delay_seconds:
            time.sleep(min(max(checkpoint_delay_seconds, 0.0), 2.0))
        (marker_writer or _write_marker)(
            transaction_fd, transaction_id, nonce
        )
        os.fsync(transaction_fd)
        os.fsync(state_fd)
        return identity
    except BaseException:
        if created and transaction_fd >= 0:
            _remove_own_failed_root(
                state_fd, name, transaction_fd, identity
            )
        raise
    finally:
        if transaction_fd >= 0:
            os.close(transaction_fd)


def _remove_own_intent(state_fd: int, transaction_id: str, nonce: str) -> None:
    try:
        _validate_intent(state_fd, transaction_id, nonce)
        os.unlink(_intent_name(transaction_id), dir_fd=state_fd)
        os.fsync(state_fd)
    except (FileNotFoundError, OSError, ValueError):
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--state-fd", required=True, type=int)
    parser.add_argument("--ack-fd", required=True, type=int)
    parser.add_argument("--transaction-id", required=True)
    parser.add_argument("--nonce", required=True)
    parser.add_argument("--checkpoint-fd", type=int, default=-1)
    parser.add_argument("--checkpoint-delay-ms", type=int, default=0)
    args = parser.parse_args(argv)

    def cancel_creation(_signum: int, _frame: object) -> None:
        raise TimeoutError("transaction creation cancelled")

    signal.signal(signal.SIGTERM, cancel_creation)
    signal.signal(signal.SIGINT, cancel_creation)
    response = _SAFE_FAILURE
    try:
        device, inode = create_marked_transaction(
            args.state_fd,
            args.transaction_id,
            args.nonce,
            checkpoint_fd=args.checkpoint_fd,
            checkpoint_delay_seconds=max(args.checkpoint_delay_ms, 0) / 1000,
        )
        response = _canonical({"device": device, "inode": inode, "ok": True})
    except BaseException:
        pass
    finally:
        _remove_own_intent(args.state_fd, args.transaction_id, args.nonce)
    try:
        _write_all(args.ack_fd, response)
    except OSError:
        pass
    return 0 if response != _SAFE_FAILURE else 1


if __name__ == "__main__":
    raise SystemExit(main())
