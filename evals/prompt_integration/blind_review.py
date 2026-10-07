"""Build an opaque paired semantic-review packet from completed native reports."""

import argparse
from hashlib import sha256
import json
from pathlib import Path
import random


def build_packet(baseline, candidate, manifest):
    for report in (baseline, candidate):
        if not report["completed"]:
            raise ValueError("partial model evidence cannot form final review packet")
        if (
            report["cases_sha256"] != baseline["cases_sha256"]
            or report["settings"] != baseline["settings"]
        ):
            raise ValueError("comparison conditions differ")
    baseline_rows = baseline["arms"]["baseline"]["results"]
    candidate_rows = candidate["arms"]["candidate"]["results"]
    if len(baseline_rows) != 40 or len(candidate_rows) != 40:
        raise ValueError("full20 cases×2roles required for independent review")
    rng = random.Random(20261006)
    packet = {
        "cases_sha256": baseline["cases_sha256"],
        "settings": baseline["settings"],
        "review_contract": "Score facts, useful deliverable, continuation, applicable timezone and independent Audit from0wrong/1partial/2complete. Never award quality from size alone; inspect all disagreements. Expected outcome screening labels are not truth.",
        "cases": [],
    }
    key = {}
    for case in manifest["cases"]:
        entry = {
            "case_id": case["id"],
            "category": case["category"],
            "context": case["context"],
            "feedback": case["feedback"],
            "proposal_revision": case["proposal_revision"],
            "fixed_audit_subject": case["audit_subject"],
            "semantic_rubric": case["semantic_rubric"],
            "roles": {},
        }
        for role in ("consumer", "audit"):
            paired = []
            for arm, rows in (
                ("baseline", baseline_rows),
                ("candidate", candidate_rows),
            ):
                row = next(
                    r for r in rows if r["case_id"] == case["id"] and r["role"] == role
                )
                opaque = sha256(
                    (case["id"] + role + arm + baseline["cases_sha256"]).encode()
                ).hexdigest()[:16]
                key[opaque] = {"case_id": case["id"], "role": role, "arm": arm}
                paired.append(
                    {
                        "output_id": opaque,
                        "strict_schema_valid": row["normalized"]["ok"],
                        "exact_wire_output": row["native"]["raw"],
                        "normalized_result": row["normalized"]["result"],
                        "scores": {
                            "facts": None,
                            "usefulness": None,
                            "continuation": None,
                            "timezone_if_applicable": None,
                            "audit_if_applicable": None,
                        },
                        "review_notes": None,
                    }
                )
            rng.shuffle(paired)
            entry["roles"][role] = paired
        packet["cases"].append(entry)
    return packet, key


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--key", type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads((Path(__file__).parent / "cases.v2.json").read_text())
    packet, key = build_packet(
        json.loads(args.baseline.read_text()),
        json.loads(args.candidate.read_text()),
        manifest,
    )
    args.output.write_text(json.dumps(packet, ensure_ascii=False, indent=2) + "\n")
    args.key.write_text(json.dumps(key, ensure_ascii=False, indent=2) + "\n")
    print(f"Wrote40 opaque role pairs to{args.output};mapping in{args.key}")
