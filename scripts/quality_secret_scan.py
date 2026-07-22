from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def tracked_files() -> list[Path]:
    completed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        check=True,
        capture_output=True,
    )
    excluded = {Path("uv.lock"), Path(".secrets.baseline")}
    return [
        path
        for raw in completed.stdout.split(b"\0")
        if raw and (path := Path(raw.decode())) not in excluded
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, default=Path(".secrets.baseline"))
    args = parser.parse_args()
    if not args.baseline.exists():
        print(json.dumps({"status": "configuration_error", "reason": "baseline_missing"}))
        return 2
    completed = subprocess.run(
        [
            str(Path(sys.executable).with_name("detect-secrets-hook")),
            "--baseline",
            str(args.baseline),
            *map(str, tracked_files()),
        ],
        text=True,
    )
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
