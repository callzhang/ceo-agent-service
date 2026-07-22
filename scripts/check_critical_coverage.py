from __future__ import annotations

import argparse
import xml.etree.ElementTree as ET
from pathlib import Path


CRITICAL_MODULES = (
    "leak_check.py",
    "permission.py",
    "universal_plan.py",
    "universal_executor.py",
    "wechat/codex_safety.py",
    "quality/models.py",
    "quality/evaluator.py",
    "quality/migrations.py",
)


def module_coverage(report: Path) -> dict[str, float]:
    root = ET.parse(report).getroot()
    return {
        str(node.attrib["filename"]): float(node.attrib["line-rate"]) * 100
        for node in root.findall(".//class")
        if "filename" in node.attrib and "line-rate" in node.attrib
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Enforce the line-coverage floor for every safety-critical module."
    )
    parser.add_argument("report", type=Path)
    parser.add_argument("--threshold", type=float, default=95.0)
    args = parser.parse_args(argv)

    if not args.report.is_file():
        parser.error(f"coverage report does not exist: {args.report}")
    coverage = module_coverage(args.report)
    failed = False
    for module in CRITICAL_MODULES:
        percent = coverage.get(module)
        if percent is None:
            failed = True
            print(f"MISSING {module}")
            continue
        status = "PASS" if percent + 1e-9 >= args.threshold else "FAIL"
        failed = failed or status == "FAIL"
        print(f"{status} {module}: {percent:.2f}%")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
