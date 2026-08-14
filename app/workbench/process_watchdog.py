"""Parent-liveness wrapper for one owned POSIX process group."""

from __future__ import annotations

import argparse
import os
import select
import signal
import time
import uuid


_TERM_GRACE_SECONDS = 2.0


def _kill_owned_group(pgid: int) -> None:
    try:
        os.killpg(pgid, signal.SIGTERM)
    except (PermissionError, ProcessLookupError):
        return
    deadline = time.monotonic() + _TERM_GRACE_SECONDS
    while time.monotonic() < deadline:
        try:
            os.killpg(pgid, 0)
        except (PermissionError, ProcessLookupError):
            return
        time.sleep(0.02)
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (PermissionError, ProcessLookupError):
        pass


def _monitor_parent(
    parent_fd: int,
    owned_pgid: int,
) -> None:
    # The watchdog observes this group continuously from creation. POSIX cannot
    # reuse the PGID while any member remains; once the group disappears we exit
    # and permanently relinquish permission to signal that numeric identifier.
    os.setsid()
    for fd in (0, 1, 2):
        try:
            os.close(fd)
        except OSError:
            pass
    try:
        while True:
            readable, _, _ = select.select([parent_fd], [], [], 0.25)
            if not readable:
                continue
            data = os.read(parent_fd, 1)
            if data == b"R":
                return
            if not data:
                _kill_owned_group(owned_pgid)
                return
    finally:
        try:
            os.close(parent_fd)
        except OSError:
            pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--parent-fd", type=int, required=True)
    parser.add_argument("--identity", required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if args.parent_fd < 0 or not args.command or args.command[0] != "--":
        return 2
    try:
        uuid.UUID(args.identity)
    except ValueError:
        return 2
    command = args.command[1:]
    if not command:
        return 2
    owned_pgid = os.getpid()
    if os.fork() == 0:
        try:
            _monitor_parent(
                args.parent_fd,
                owned_pgid,
            )
        finally:
            os._exit(0)
    os.close(args.parent_fd)
    os.execvpe(command[0], command, os.environ)
    return 127


if __name__ == "__main__":
    raise SystemExit(main())
