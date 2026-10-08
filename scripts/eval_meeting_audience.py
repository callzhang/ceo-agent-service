#!/usr/bin/env python3
"""Paired native meeting-route decisions; synthetic facts and no tools/effects."""
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PROMPT = """
import json,sys
from app.meeting_alignment_models import MeetingSource
from app.meeting_alignment_agent import build_meeting_alignment_prompt
x=json.load(sys.stdin)
s=MeetingSource.model_validate(x['source'])
print(build_meeting_alignment_prompt(s,work_profile='',work_profile_source='synthetic',group_candidates=x['groups']))
"""
VALIDATE = """
import json,sys
from app.meeting_alignment_models import MeetingSource,MeetingAlignmentDecision
from app.meeting_alignment_agent import _validate_source_aware_target
x=json.load(sys.stdin)
d=MeetingAlignmentDecision.model_validate(x['result'])
_validate_source_aware_target(MeetingSource.model_validate(x['source']),d,group_candidates=x['groups'])
print(d.model_dump_json())
"""


def run_local(root, program, payload):
    result = subprocess.run([sys.executable, "-c", program], cwd=root,
        input=json.dumps(payload), text=True, capture_output=True, timeout=30,
        env={**os.environ, "PYTHONPATH": str(root), "PYTHONDONTWRITEBYTECODE": "1"})
    return result


def score(case, result):
    if not isinstance(result, dict):
        return False
    target = result.get("target") or {}
    private = result.get("sensitive_private_message")
    if result.get("action") != "send" or result.get("audience_scope") != "business":
        return False
    if any(target.get(k) != v for k, v in case["expected_target"].items()):
        return False
    expected_private = case["expected_private_user_id"]
    if expected_private is None:
        return private is None
    if not isinstance(private, dict) or private.get("target", {}).get("direct_user_id") != expected_private:
        return False
    marker = case["private_marker"]
    return marker not in result.get("final_message", "") and marker in private.get("message", "")


def main():
    from scripts.eval_consumer_audit_business import archive_ref, native_command, resolve_commit_ref, run_role

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-ref", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=ROOT / "evals/meeting_audience/v1.json")
    args = parser.parse_args()
    manifest_bytes = args.manifest.read_bytes()
    manifest = json.loads(manifest_bytes)
    report = {"manifest_sha256":sha256(manifest_bytes).hexdigest(),
        "harness_sha256":sha256(Path(__file__).read_bytes()).hexdigest(),
        "settings":manifest["settings"], "external_effects":"none; tools disabled; synthetic facts"}
    for label, ref in (("baseline", manifest["baseline_ref"]), ("candidate", args.candidate_ref)):
        ref = resolve_commit_ref(ref)
        rows = []
        with tempfile.TemporaryDirectory(prefix=f"meeting-route-{label}-") as raw:
            root = Path(raw)
            archive_ref(ref, root)
            for case in manifest["cases"]:
                source = {"meeting_id":case["id"], "title":"Pilot review", "status":"ended",
                    "started_at":"2026-10-01T10:00:00+00:00", "ended_at":"2026-10-01T10:20:00+00:00",
                    "participants":manifest["participants"], "attendee_evidence":"transcript",
                    "attendee_roster_complete":False, "creator":None, "current_user_id":"principal",
                    "summary":case["summary"], "transcript":[{"speaker_name":"Morgan","speaker_user_id":"manager","text":case["summary"]}]}
                payload = {"source":source,"groups":case["groups"]}
                built = run_local(root, PROMPT, payload)
                if built.returncode:
                    raise RuntimeError("meeting prompt construction failed: " + built.stderr[-1500:])
                prompt = ("Synthetic native evaluation. No tools, providers, writes or real identities are available. "
                    "The following scoped source and verified facts are complete; do not ask for tools. "
                    "Return the actual meeting decision wire JSON only.\nVerified facts:\n" + case["facts"] + "\n" + built.stdout)
                native = run_role(command=native_command(model=manifest["settings"]["model"],
                    effort=manifest["settings"]["reasoning_effort"],developer_instructions="",workdir=root),
                    prompt=prompt, timeout=manifest["settings"]["timeout_seconds_per_case"])
                validation = run_local(root, VALIDATE, {**payload,"result":native.get("result")})
                valid = native["ok"] and validation.returncode == 0
                rows.append({"id":case["id"], "prompt_sha256":sha256(prompt.encode()).hexdigest(),
                    "native":native, "validation_ok":valid, "validation_error":validation.stderr[-1500:] if not valid else "",
                    "passed":valid and score(case,native.get("result"))})
                print(f"{label} {case['id']}: {rows[-1]['passed']}", flush=True)
        report[label] = {"ref":ref,"cases":rows,"passed":sum(r["passed"] for r in rows)}
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n")
    return 0 if report["candidate"]["passed"] == len(manifest["cases"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
