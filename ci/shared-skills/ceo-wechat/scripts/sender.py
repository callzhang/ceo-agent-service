#!/usr/bin/env python3
"""Narrow, executable sender entrypoint owned by the ceo-wechat Skill.

Accessibility remains solely in CEO WeChat Sender.app; this script calls its
owner-only IPC client through the service CLI.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys


def repository_root() -> Path:
    """Where the service lives, which is not where this Skill lives.

    The Skill's source of truth is the shared Skills repository, so the path
    relative to this file no longer finds the service. `CEO_SERVICE_ROOT` is
    what the service's own launchd job already exports.
    """
    configured = os.environ.get("CEO_SERVICE_ROOT", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path.home() / "Projects" / "ceo-agent-service"


def build_command(arguments: list[str]) -> list[str]:
    return [sys.executable, "-m", "app.wechat.cli", *arguments]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="CEO WeChat Skill sender")
    subcommands = parser.add_subparsers(dest="operation", required=True)
    subcommands.add_parser("pending")
    for operation in ("approve", "reject"):
        action = subcommands.add_parser(operation)
        action.add_argument("--id", type=int, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    command = [args.operation]
    if args.operation in {"approve", "reject"}:
        command.extend(["--id", str(args.id)])
    return subprocess.run(build_command(command), cwd=repository_root(), check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
