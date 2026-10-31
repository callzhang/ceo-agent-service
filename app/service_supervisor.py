"""Keep the worker and audit web isolated under one launchd service."""

import argparse
import os
import select
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path


POLL_INTERVAL_SECONDS = 0.2
SHUTDOWN_GRACE_SECONDS = 1.0
CHILD_RESTART_DELAY_SECONDS = 1.0
SERVICE_STARTUP_READY_ENV = "CEO_SERVICE_STARTUP_READY_FD"


def build_child_command(command: str, args: argparse.Namespace) -> list[str]:
    child_command = [
        sys.executable,
        "-m",
        "app.cli",
        command,
        "--db",
        str(args.db),
        "--workspace",
        str(args.workspace),
        "--corpus-dir",
        str(args.corpus_dir),
    ]
    if command == "audit-web":
        child_command[4:4] = ["--host", args.host, "--port", str(args.port)]
    return child_command


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
    email_worker_command: Sequence[str],
    *,
    popen: Callable[..., subprocess.Popen] = subprocess.Popen,
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
        "email-worker": email_worker_command,
    }
    children: dict[str, subprocess.Popen] = {}
    service_ready_fds: dict[str, int] = {}
    def wait_for_startup_signal(child: subprocess.Popen, ready_fd: int) -> bool:
        try:
            while not shutdown_requested and child.poll() is None:
                readable, _, _ = select.select([ready_fd], [], [], POLL_INTERVAL_SECONDS)
                if not readable:
                    continue
                return os.read(ready_fd, 5) == b"ready"
            return False
        finally:
            os.close(ready_fd)

    def spawn_service() -> None:
        while not shutdown_requested:
            ready_read_fd, ready_write_fd = os.pipe()
            child_env = os.environ.copy()
            child_env[SERVICE_STARTUP_READY_ENV] = str(ready_write_fd)
            try:
                child = popen(
                    commands["worker"],
                    pass_fds=(ready_write_fd,),
                    env=child_env,
                )
            except OSError:
                os.close(ready_read_fd)
                os.close(ready_write_fd)
                sleep(CHILD_RESTART_DELAY_SECONDS)
                continue
            finally:
                try:
                    os.close(ready_write_fd)
                except OSError:
                    pass
            children["worker"] = child
            service_ready_fds["worker"] = ready_read_fd
            return

    def wait_for_service_startup() -> None:
        while not shutdown_requested:
            child = children["worker"]
            ready_fd = service_ready_fds["worker"]
            if wait_for_startup_signal(child, ready_fd):
                service_ready_fds.pop("worker")
                return
            service_ready_fds.pop("worker")
            stop_children((child,), sleep=sleep, monotonic=monotonic)
            children.pop("worker")
            if not shutdown_requested:
                print(
                    "service exited before startup recovery was ready; restarting",
                    flush=True,
                )
                sleep(CHILD_RESTART_DELAY_SECONDS)
                spawn_service()

    def start_service() -> None:
        spawn_service()
        wait_for_service_startup()

    def start_child(name: str) -> None:
        if name == "worker":
            start_service()
            return
        while not shutdown_requested:
            try:
                children[name] = popen(commands[name])
                return
            except OSError:
                sleep(CHILD_RESTART_DELAY_SECONDS)

    try:
        spawn_service()
        start_child("audit-web")
        wait_for_service_startup()
        if not shutdown_requested:
            start_child("email-worker")
        while not shutdown_requested:
            for name, child in tuple(children.items()):
                returncode = child.poll()
                if returncode is not None:
                    child.wait()
                    print(
                        f"child {name} exited returncode={returncode}; restarting",
                        flush=True,
                    )
                    if name == "worker":
                        email_worker = children.pop("email-worker", None)
                        if email_worker is not None:
                            stop_children((email_worker,), sleep=sleep, monotonic=monotonic)
                        start_child("worker")
                        if not shutdown_requested:
                            start_child("email-worker")
                    else:
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


def build_env_migration_command() -> list[str]:
    return [sys.executable, "-m", "app.agent_runtime_migration"]


def main() -> int:
    args = build_parser().parse_args()
    # Settings written by an older release are rewritten before any child
    # reads `.env`. It runs as its own process: importing app.config here
    # would copy the file into this process's environment, and every child
    # restarted later would inherit those stale values as authoritative.
    subprocess.run(build_env_migration_command(), check=True)
    return run_supervisor(
        build_child_command("service", args),
        build_child_command("audit-web", args),
        build_child_command("email-worker", args),
    )


if __name__ == "__main__":
    raise SystemExit(main())
