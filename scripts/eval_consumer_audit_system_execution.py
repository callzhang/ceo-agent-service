#!/usr/bin/env python3
"""Compare one frozen synthetic contract manifest against baseline and candidate.

This does not call an Agent, model, or live provider. Business-judgment quality is
measured by the separate native model evaluation; these results only show whether
source code accepts the same 20 synthetic contract probes.
"""

from __future__ import annotations

import argparse
from contextlib import ExitStack
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
MANIFEST = ROOT / "evals" / "consumer_audit_system_execution" / "v1.json"
SCENARIO_SCRIPT = ROOT / "evals" / "consumer_audit_system_execution" / "scenarios.py"
SCENARIO_ENTRY = """
import importlib.util, pathlib, runpy, sys
package = pathlib.Path.cwd() / 'app'
spec = importlib.util.spec_from_file_location('app', package / '__init__.py', submodule_search_locations=[str(package)])
module = importlib.util.module_from_spec(spec)
sys.modules['app'] = module
spec.loader.exec_module(module)
runpy.run_path(sys.argv.pop(1), run_name='__main__')
"""


def _manifest() -> dict[str, object]:
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if data.get("version") != "consumer-audit-system-execution.v1" or len(data.get("cases", [])) != 20:
        raise ValueError("frozen v1 manifest must contain exactly 20 cases")
    settings = data.get("settings")
    if not isinstance(settings, dict) or settings.get("runtime_mode") != "persisted-contract-replay" or settings.get("concurrency") != 1 or settings.get("timeout_seconds") != 30 or settings.get("model") is not None or settings.get("thinking") is not None:
        raise ValueError("frozen v1 execution settings changed")
    from evals.consumer_audit_system_execution.scenarios import SCENARIOS
    ids = [case["id"] for case in data["cases"]]
    if len(ids) != len(set(ids)) or set(ids) != set(SCENARIOS):
        raise ValueError("manifest and executable scenario IDs differ")
    return data


def _snapshot(ref: str, destination: Path) -> None:
    archive = subprocess.run(
        ["git", "archive", "--format=tar", ref], cwd=ROOT,
        check=True, capture_output=True, timeout=120,
    ).stdout
    with tarfile.open(fileobj=BytesIO(archive), mode="r:") as bundle:
        bundle.extractall(destination, filter="data")


def _run_case(case_id: str, source_root: Path) -> dict[str, object]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(source_root)
    try:
        completed = subprocess.run(
            [sys.executable, "-c", SCENARIO_ENTRY, str(SCENARIO_SCRIPT), "--case", case_id],
            cwd=source_root, env=env, text=True, capture_output=True, timeout=30,
        )
    except subprocess.TimeoutExpired:
        return {"case_id": case_id, "ok": False, "error": "scenario timeout after 30 seconds"}
    try:
        result = json.loads(completed.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        return {"case_id": case_id, "ok": False,
                "error": f"scenario process failed without JSON result (exit {completed.returncode})"}
    if result.get("case_id") != case_id or result.get("ok") is not (completed.returncode == 0):
        return {"case_id": case_id, "ok": False, "error": "scenario result/exit mismatch"}
    return result


def compare(*, candidate_root: Path | None = None, candidate_ref: str | None = None,
            baseline_root: Path | None = None) -> dict[str, object]:
    if candidate_root is not None and candidate_ref is not None:
        raise ValueError("candidate_root and candidate_ref are mutually exclusive")
    manifest = _manifest()
    baseline_ref = str(manifest["baseline_ref"])
    resolved_candidate_ref = None
    if candidate_ref is not None:
        resolved_candidate_ref = subprocess.run(
            ["git", "rev-parse", "--verify", "--end-of-options", f"{candidate_ref}^{{commit}}"],
            cwd=ROOT, check=True, capture_output=True, text=True, timeout=30,
        ).stdout.strip()
    with ExitStack() as stack:
        if resolved_candidate_ref is not None:
            candidate_root = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="consumer-audit-candidate-")))
            _snapshot(resolved_candidate_ref, candidate_root)
        else:
            candidate_root = (candidate_root or ROOT).resolve()
        if baseline_root is None:
            baseline_root = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="consumer-audit-baseline-")))
            _snapshot(baseline_ref, baseline_root)
        else:
            baseline_root = baseline_root.resolve()
        cases = []
        for case in manifest["cases"]:
            case_id = case["id"]
            baseline = _run_case(case_id, baseline_root)
            candidate = _run_case(case_id, candidate_root)
            cases.append({"id": case_id, "expected": case["expected"],
                          "baseline": baseline, "candidate": candidate})
    return {
        "mode": "synthetic_contract_replay",
        "business_model_evaluation": False,
        "manifest": str(MANIFEST), "baseline_ref": baseline_ref,
        "manifest_sha256": sha256(MANIFEST.read_bytes()).hexdigest(),
        "harness_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
        "scenario_sha256": sha256(SCENARIO_SCRIPT.read_bytes()).hexdigest(),
        "candidate_ref": resolved_candidate_ref,
        "candidate_root": None if resolved_candidate_ref else str(candidate_root),
        "settings": manifest["settings"],
        "total": len(cases),
        "baseline_passed": sum(row["baseline"]["ok"] for row in cases),
        "candidate_passed": sum(row["candidate"]["ok"] for row in cases),
        "cases": cases,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    candidate_source = parser.add_mutually_exclusive_group()
    candidate_source.add_argument("--candidate-root", type=Path)
    candidate_source.add_argument("--candidate-ref")
    parser.add_argument("--baseline-root", type=Path,
                        help="Use an existing baseline checkout; default extracts the frozen Git ref")
    parser.add_argument("--output", type=Path, help="Write the full JSON comparison to this file")
    args = parser.parse_args(argv)
    report = compare(candidate_root=args.candidate_root, candidate_ref=args.candidate_ref,
                     baseline_root=args.baseline_root)
    encoded = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 0 if report["candidate_passed"] == report["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
