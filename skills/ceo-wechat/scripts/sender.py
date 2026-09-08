#!/usr/bin/env python3
"""Narrow, executable sender entrypoint owned by the ceo-wechat Skill.

Accessibility remains solely in CEO WeChat Sender.app; this script calls its
owner-only IPC client through the service CLI.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


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
    return subprocess.run(build_command(command), cwd=REPOSITORY_ROOT, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
