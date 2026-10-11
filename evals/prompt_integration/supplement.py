"""Four frozen matching-source cases; same native helpers and complete inputs."""

import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.eval_prompt_integration import (  # noqa: E402 - direct script
    ARTIFACT_ROOT,
    BASELINE,
    archive_ref,
    canonical,
    extract,
    fingerprint,
    normalize_role_result,
    resolve_commit_ref,
    run_native,
    save,
    screen,
    sizes,
)

MANIFEST = Path(__file__).with_name("supplement.v1.json")
CORPUS_SHA = "2433049a74ad6206af93adba5e488e10271eef95bc1cb3f8354fc174672f9658"


def load():
    value = json.loads(MANIFEST.read_text())
    if (
        value["version"] != "prompt-integration-matching-source.v1"
        or value["baseline_ref"] != BASELINE
    ):
        raise ValueError("supplement source changed")
    if len(value["cases"]) != 4 or fingerprint(value["cases"]) != CORPUS_SHA:
        raise ValueError("frozen matching-source cases changed")
    return value


def verify_rows(rows, manifest, arm):
    evidence = []
    for row in rows:
        case = next(c for c in manifest["cases"] if c["id"] == row["case_id"])
        if row["source_bindings"] != case["audit_subject"]["source_bindings"]:
            raise ValueError("source binding metadata differs")
        if row["role"] != "audit":
            continue
        rendered = json.loads(row["task"].split("Candidate revision\n")[-1])
        if (
            rendered["candidate"]["source_bindings"]
            != case["audit_subject"]["source_bindings"]
        ):
            raise ValueError("full source binding lost from actual Audit task")
        reference = "Candidate revision.candidate.source_bindings[0].value"
        exercised = reference in row["task"]
        if exercised != (arm == "candidate"):
            raise ValueError("source-reference path not exercised as expected")
        evidence.append(
            {
                "case_id": row["case_id"],
                "source_binding_sha256": fingerprint(row["source_bindings"]),
                "candidate_digest": row["candidate_digest"],
                "complete_sources_equal": True,
                "actual_task_reference_exercised": exercised,
            }
        )
    return evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", choices=("baseline", "candidate"), required=True)
    parser.add_argument("--candidate-ref")
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--baseline-report",
        type=Path,
        default=ARTIFACT_ROOT / "supplement-baseline-native.v1.json",
    )
    parser.add_argument("--profile-path", type=Path)
    parser.add_argument("--profile-sha256")
    args = parser.parse_args()
    if args.profile_path is not None and not args.profile_sha256:
        parser.error("private profile requires its frozen SHA")
    profile_metadata = {"condition": "identical-synthetic-short"}
    if args.profile_path is not None:
        raw_profile = args.profile_path.read_bytes()
        if sha256(raw_profile).hexdigest() != args.profile_sha256:
            raise ValueError("frozen private profile changed")
        profile_metadata = {
            "condition": "explicit-private-profile-pair",
            "path": str(args.profile_path),
            "sha256": args.profile_sha256,
            "bytes": len(raw_profile),
            "characters": len(raw_profile.decode("utf-8")),
            "body_in_git": False,
        }
    manifest = load()
    if args.arm == "candidate" and not args.candidate_ref:
        parser.error("immutable candidate ref required")
    ref = BASELINE if args.arm == "baseline" else resolve_commit_ref(args.candidate_ref)
    report = {
        "mode": "matching-source-supplement-size"
        if args.prepare_only
        else "matching-source-supplement-native",
        "supplement_distinct_from_v2": True,
        "refs": {args.arm: ref},
        "settings": {**manifest["settings"], "profile": profile_metadata["condition"]},
        "profile_condition": profile_metadata,
        "cases_sha256": CORPUS_SHA,
        "manifest_sha256": sha256(MANIFEST.read_bytes()).hexdigest(),
        "harness_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
        "completed": False,
        "arms": {},
    }
    with TemporaryDirectory(prefix="matching-source-" + args.arm + "-") as temporary:
        root = Path(temporary) / "source"
        root.mkdir()
        archive_ref(ref, root)
        rows = extract(root, manifest, args.arm, profile_path=args.profile_path)
        if (
            args.profile_path is not None
            and sha256(args.profile_path.read_bytes()).hexdigest()
            != args.profile_sha256
        ):
            raise ValueError("private profile changed during extraction")
        equality = verify_rows(rows, manifest, args.arm)
        if args.arm == "candidate":
            baseline = json.loads(args.baseline_report.read_text())
            if (
                baseline["cases_sha256"] != CORPUS_SHA
                or baseline["settings"] != report["settings"]
            ):
                raise ValueError("supplement comparison conditions differ")
            prior = baseline["arms"]["baseline"]["source_equality"]
            for item in equality:
                previous = next(p for p in prior if p["case_id"] == item["case_id"])
                if any(
                    previous[field] != item[field]
                    for field in ("source_binding_sha256", "candidate_digest")
                ):
                    raise ValueError(
                        "supplement source binding or digest changed between arms"
                    )
                item["baseline_candidate_bindings_and_digest_equal"] = True
        for row in rows:
            row.update(
                developer_sha256=sha256(row["developer"].encode()).hexdigest(),
                task_sha256=sha256(row["task"].encode()).hexdigest(),
            )
        report["arms"][args.arm] = {
            "inputs": rows,
            "size_summary": sizes(rows),
            "source_equality": equality,
            "results": [],
        }
        save(args.output, report)
        if not args.prepare_only:
            workdir = Path(temporary) / "native"
            workdir.mkdir()
            for row in rows:
                case = next(c for c in manifest["cases"] if c["id"] == row["case_id"])
                native = run_native(row, manifest["settings"], workdir)
                normalized = (
                    normalize_role_result(root, row["role"], native["result"])
                    if native["ok"]
                    else {
                        "ok": False,
                        "error": native["error"],
                        "result": None,
                        "digest": None,
                    }
                )
                evaluation = screen(case, row, native, normalized)
                report["arms"][args.arm]["results"].append(
                    {
                        "case_id": row["case_id"],
                        "role": row["role"],
                        "native": native,
                        "normalized": normalized,
                        "screen": evaluation,
                    }
                )
                save(args.output, report)
                print(
                    canonical(
                        {
                            "arm": args.arm,
                            "case_id": row["case_id"],
                            "role": row["role"],
                            "schema_valid": normalized["ok"],
                            "screen_passed": evaluation[
                                "contract_and_binding_screen_passed"
                            ],
                            "native_error": native["error"],
                        }
                    ),
                    flush=True,
                )
                if not native["ok"] and native["result"] is None:
                    report["stop_reason"] = native["error"]
                    save(args.output, report)
                    return 1
    report["completed"] = True
    save(args.output, report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
