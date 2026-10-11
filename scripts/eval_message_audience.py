#!/usr/bin/env python3
"""Paired native audience judgment with exact synthetic delivery expectations."""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import tempfile

from eval_consumer_audit_business import ROOT, archive_ref, resolve_commit_ref, run_suite


def delivery_matches(case: dict, result: dict | None) -> bool:
    if result is None or result.get("outcome") != case["expected_consumer"]:
        return False
    proposal = result.get("proposal") or {}
    actions = proposal.get("actions", [])
    option_actions = [
        action
        for option in result.get("decision_options", [])
        for action in (option.get("plan") or {}).get("actions", [])
    ]
    if option_actions:
        return False
    if case.get("require_factual_input") and (
        result.get("decision_options") or not result.get("requested_input")
    ):
        return False
    expected_error = case.get("expected_error")
    if expected_error is not None:
        error = result.get("error") or {}
        if any(error.get(key) != value for key, value in expected_error.items()):
            return False
    expected = case["expected_deliveries"]
    if len(actions) != len(expected):
        return False
    remaining = list(actions)
    for delivery in expected:
        matches = [
            action for action in remaining
            if action.get("capability") == "dingtalk-chat"
            and action.get("operation") == "send_message"
            and action.get("target") == delivery["target"]
            and action.get("payload", {}).get("content") == delivery["content"]
        ]
        if len(matches) != 1:
            return False
        remaining.remove(matches[0])
    return True


def audit_binding_matches(row: dict) -> bool:
    if not row["score"]["audit_applicable"]:
        return True
    subject = row.get("audit_subject") or row["consumer_contract"]
    result = row["audit_contract"].get("result") or {}
    return bool(subject.get("digest")) and (
        result.get("candidate_digest") == subject["digest"]
        and result.get("proposal_revision") == 0
    )


def score_deliveries(suite: dict, manifest: dict) -> None:
    if [row["id"] for row in suite["cases"]] != [case["id"] for case in manifest["cases"]]:
        raise ValueError("artifact cases do not match the frozen manifest")
    for case, row in zip(manifest["cases"], suite["cases"], strict=True):
        row["exact_delivery_passed"] = delivery_matches(case, row["consumer_contract"].get("result"))
        row["audit_binding_passed"] = audit_binding_matches(row)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--candidate-ref")
    mode.add_argument("--rescore", type=Path)
    parser.add_argument("--manifest", type=Path, default=ROOT / "evals/message_audience/v2.json")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest_path = args.manifest
    manifest = json.loads(manifest_path.read_text())
    if args.rescore:
        report = json.loads(args.rescore.read_text())
        if report["manifest_sha256"] != sha256(manifest_path.read_bytes()).hexdigest():
            raise ValueError("artifact manifest changed; cannot rescore different inputs")
        report["rescore_harness_sha256"] = sha256(Path(__file__).read_bytes()).hexdigest()
        report["rescore_source"] = str(args.rescore)
        for label in ("baseline", "candidate"):
            score_deliveries(report[label], manifest)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print("Rechecked original native outputs; no new provider turns.", flush=True)
        return _exit_status(report)
    baseline_ref = resolve_commit_ref(manifest["baseline_ref"])
    candidate_ref = resolve_commit_ref(args.candidate_ref)
    report = {
        "mode": "native_synthetic_audience_judgment",
        "manifest_sha256": sha256(manifest_path.read_bytes()).hexdigest(),
        "harness_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
        "settings": manifest["settings"],
        "external_effects": "none; synthetic inline facts, tools disabled",
    }
    for label, ref in (("baseline", baseline_ref), ("candidate", candidate_ref)):
        print(f"Running {label} {ref}", flush=True)
        with tempfile.TemporaryDirectory(prefix=f"audience-{label}-") as temporary:
            root = Path(temporary)
            archive_ref(ref, root)
            suite = run_suite(root, manifest, archived=True)
        suite["ref"] = ref
        score_deliveries(suite, manifest)
        report[label] = suite
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(f"{label}: {sum(row['exact_delivery_passed'] for row in suite['cases'])}/{len(suite['cases'])} exact deliveries", flush=True)
    print("Artifact retained for independent exact-output review; scores alone are not acceptance.", flush=True)
    return _exit_status(report)


def _exit_status(report: dict) -> int:
    return 0 if all(
        row["exact_delivery_passed"] and row["audit_binding_passed"] and
        (not row["score"]["audit_applicable"] or row["score"]["audit_ok"])
        for row in report["candidate"]["cases"]
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
