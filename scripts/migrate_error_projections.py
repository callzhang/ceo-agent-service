#!/usr/bin/env python3
"""Migrate legacy meeting error projections; history rows are untouched."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.error_code_migration import migrate_legacy_meeting_projections
from app.store import AutoReplyStore


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--apply", action="store_true", help="write projections")
    args = parser.parse_args()
    result = migrate_legacy_meeting_projections(
        AutoReplyStore(args.db), dry_run=not args.apply
    )
    mode = "applied" if args.apply else "preview"
    print(f"{mode}: scanned={result.scanned} changed={result.changed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
