"""Keep the worker and audit web isolated under one launchd service."""

import argparse
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path


POLL_INTERVAL_SECONDS = 0.2
SHUTDOWN_GRACE_SECONDS = 1.0
CHILD_RESTART_DELAY_SECONDS = 1.0


def build_child_command(command: str, args: argparse.Namespace) -> list[str]:
    return [
        sys.executable,
        "-m",
        "app.cli",
        command,
        "--host",
        args.host,
        "--port",
        str(args.port),
        "--db",
        str(args.db),
        "--workspace",
        str(args.workspace),
        "--corpus-dir",
        str(args.corpus_dir),
    ]


def stop_children(
    children: Sequence[subprocess.Popen],
    *,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> None:
    running = [child for child in children if child.poll() is None]
    for child in running:
        child.terminate()

    deadline = monotonic() + SHUTDOWN_GRACE_SECONDS
    while running and monotonic() < deadline:
        sleep(POLL_INTERVAL_SECONDS)
        running = [child for child in running if child.poll() is None]

    for child in running:
        child.kill()
    for child in children:
        child.wait()


def run_supervisor(
    worker_command: Sequence[str],
    audit_web_command: Sequence[str],
    *,
    popen: Callable[[Sequence[str]], subprocess.Popen] = subprocess.Popen,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> int:
    shutdown_requested = False

    def request_shutdown(_signum: int, _frame: object) -> None:
        nonlocal shutdown_requested
        shutdown_requested = True

    handled_signals = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
    previous_handlers = {
        current_signal: signal.signal(current_signal, request_shutdown)
        for current_signal in handled_signals
    }
    commands = {
        "worker": worker_command,
        "audit-web": audit_web_command,
    }
    children: dict[str, subprocess.Popen] = {}

    def start_child(name: str) -> None:
        while not shutdown_requested:
            try:
                children[name] = popen(commands[name])
                return
            except OSError:
                sleep(CHILD_RESTART_DELAY_SECONDS)

    try:
        for name in commands:
            start_child(name)
        while not shutdown_requested:
            for name, child in tuple(children.items()):
                returncode = child.poll()
                if returncode is not None:
                    child.wait()
                    start_child(name)
                    break
            sleep(POLL_INTERVAL_SECONDS)
        return 0
    finally:
        stop_children(tuple(children.values()), sleep=sleep, monotonic=monotonic)
        for current_signal, previous_handler in previous_handlers.items():
            signal.signal(current_signal, previous_handler)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ceo-agent-service-supervisor")
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--corpus-dir", type=Path, required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return run_supervisor(
        build_child_command("service", args),
        build_child_command("audit-web", args),
    )


if __name__ == "__main__":
    raise SystemExit(main())
