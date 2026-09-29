#!/usr/bin/env python3
"""Narrow, executable reader entrypoint owned by the ceo-wechat Skill.

It delegates to the service's stable Reader IPC client. It never accesses the
WeChat database itself, so App Data permission stays with CEO WeChat Reader.app.
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
    parser = argparse.ArgumentParser(description="CEO WeChat Skill reader")
    subcommands = parser.add_subparsers(dest="operation", required=True)
    subcommands.add_parser("status")
    produce_once = subcommands.add_parser("produce-once")
    produce_once.add_argument("--db", required=True)
    read_recent = subcommands.add_parser("read-recent")
    read_recent.add_argument("--target-id", required=True)
    read_recent.add_argument("--type", choices=["direct", "group"], default="direct")
    read_recent.add_argument("--limit", type=int, default=100)
    read_recent.add_argument("--include-text", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    command = [args.operation]
    if args.operation == "produce-once":
        command.extend(["--db", args.db])
    if args.operation == "read-recent":
        command.extend(["--target-id", args.target_id, "--type", args.type, "--limit", str(args.limit)])
        if args.include_text:
            command.append("--include-text")
    return subprocess.run(build_command(command), cwd=repository_root(), check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
