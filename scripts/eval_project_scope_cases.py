#!/usr/bin/env python3
"""Paired native Audit review of fixed project-margin interpretation cases.

The only prompt difference within a case is the exact project Skill body.
All subjects are fixed synthetic Consumer candidates and no provider is connected.
"""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import subprocess

from app.agent_wire_contracts import AuditAgentWireResult
from app.audit_rules import render_audit_rules
from app.consumer_agent import audit_developer_instructions
from app.store import AgentRole
from scripts import eval_consumer_audit_business as business

ROOT = business.ROOT
MANIFEST = ROOT / "evals/attempt_review_cases/project_scope.v1.json"


def digest(value: str | bytes) -> str:
    return sha256(value.encode("utf-8") if isinstance(value, str) else value).hexdigest()


def git_file(ref: str, path: str) -> str:
    return subprocess.run(
        ["git", "show", f"{ref}:{path}"], cwd=ROOT,
        capture_output=True, text=True, check=True, timeout=30,
    ).stdout


def fixed_consumer(case: dict, suffix: str) -> dict:
    identity = case["id"]
    instance = f"synthetic-PROJ-{suffix}"
    task = f"synthetic-TASK-{suffix}"
    source = f"synthetic-form-{suffix}"
    candidate = case["consumer"]
    return {
        "outcome": "proposal",
        "summary": candidate["summary"],
        "proposal": {
            "objective": "Return the synthetic application to clarify or correct the stated project review issue",
            "actions": [{
                "description": "Return for resubmission with the stated reason",
                "action_identity": f"synthetic-return-{identity}",
                "capability": "dingtalk-oa",
                "operation": "revert_task",
                "target": {
                    "process_instance_id": instance,
                    "task_id": task,
                    "target_activity_id": "sid-startevent",
                },
                "payload": {
                    "action": "REVERT_FOR_RESUBMIT",
                    "remark": candidate["remark"],
                },
                "effect": "external",
            }],
            "sourced_facts": [{"assertion": candidate["basis"], "references": [source]}],
            "authored_judgment": candidate["summary"],
        },
        "decision_options": [],
        "error": {"code": "", "retryable": False, "authorization_required": False,
                  "stage": "", "source": "", "source_code": "", "session_continuable": False},
        "risk": "medium",
        "confidence": 0.85,
        "rule_coverage": 1.0,
        "information_completeness": 0.9 if identity == "unknown_aggregate_scope" else 1.0,
        "needs_human_reason": None,
        "decision_basis": None,
        "requested_input": None,
        "stage_index": 0,
        "predecessor_review_id": None,
        "continue_after_execution": False,
        "source_bindings": [],
        "durable_memories": [],
    }


def run(manifest_path: Path, output: Path) -> dict:
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    if manifest["version"] != "project-scope-audit.v1" or len(manifest["cases"]) != 3:
        raise ValueError("unexpected project scope case set")
    baseline = git_file(manifest["baseline_ref"], manifest["baseline_skill_path"])
    candidate = (ROOT / manifest["candidate_skill_path"]).read_text(encoding="utf-8")
    if digest(baseline) != manifest["baseline_skill_sha256"]:
        raise ValueError("baseline project Skill hash changed")
    if digest(candidate) != manifest["candidate_skill_sha256"]:
        raise ValueError("candidate project Skill hash changed")
    generic = git_file(
        manifest["baseline_ref"], "ci/shared-skills/dingtalk-oa-approval/SKILL.md"
    )
    # The shared extractor also renders Consumer configuration, which is not
    # involved in this fixed-subject Audit comparison.
    audit_developer = audit_developer_instructions(render_audit_rules(AgentRole.AUDIT))
    settings = manifest["settings"]
    command = business.native_command(
        model=settings["model"], effort=settings["reasoning_effort"],
        developer_instructions=audit_developer, workdir=ROOT,
    )
    audit_schema = AuditAgentWireResult.model_json_schema()
    results = []
    for index, case in enumerate(manifest["cases"]):
        subject = business.normalize_review_subject(ROOT, fixed_consumer(case, "ABC"[index]))
        if not subject["ok"]:
            raise ValueError(f"invalid fixed Consumer candidate: {case['id']}: {subject['error']}")
        for side, project_skill in (("baseline", baseline), ("candidate", candidate)):
            full_case = {
                "facts": f"Current time: {settings['current_time']}\n{case['facts']}",
                "skill_excerpt": (
                    "Exact frozen generic DingTalk OA Skill:\n" + generic
                    + "\nExact frozen project review Skill:\n" + project_skill
                ),
            }
            prompt = business._audit_prompt(
                full_case, subject["result"], subject["digest"], audit_schema,
            )
            raw = business.run_role(
                command=command, prompt=prompt,
                timeout=settings["timeout_seconds_per_case"],
            )
            parsed = business.normalize_role_result(
                ROOT, "audit", raw.get("result"), candidate_digest=subject["digest"])
            result = parsed.get("result") or {}
            bound = (result.get("candidate_digest") == subject["digest"]
                     and result.get("proposal_revision") == 0)
            item = {
                "case_id": case["id"], "side": side,
                "expected_audit": case["expected_audit"],
                "semantic_criterion": case["semantic_criterion"],
                "facts_sha256": digest(full_case["facts"]),
                "subject_digest": subject["digest"],
                "prompt_sha256": digest(prompt),
                "native": raw,
                "wire": parsed,
                "binding_correct": bound,
                "outcome_passed": bool(raw["ok"] and parsed["ok"] and bound
                                       and result.get("outcome") in case["expected_audit"]),
            }
            results.append(item)
            print(json.dumps({
                "case_id": case["id"], "side": side,
                "native_ok": raw["ok"], "wire_ok": parsed["ok"],
                "outcome": result.get("outcome"),
                "outcome_passed": item["outcome_passed"],
            }), flush=True)
            output.write_text(json.dumps({
                "version": manifest["version"],
                "manifest_sha256": digest(manifest_bytes),
                "harness_sha256": digest(Path(__file__).read_bytes()),
                "shared_harness_sha256": digest(Path(business.__file__).read_bytes()),
                "baseline_ref": manifest["baseline_ref"],
                "baseline_skill_sha256": digest(baseline),
                "candidate_skill_sha256": digest(candidate),
                "generic_skill_sha256": digest(generic),
                "audit_developer_sha256": digest(audit_developer),
                "audit_schema_sha256": digest(json.dumps(audit_schema, sort_keys=True)),
                "settings": settings,
                "results": results,
            }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"results": results}


def correct_wire(manifest_path: Path, previous_path: Path, output: Path) -> None:
    """Ask once per invalid main-case side for a digest-only wire correction."""
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    previous = json.loads(previous_path.read_text(encoding="utf-8"))
    if previous["manifest_sha256"] != digest(manifest_path.read_bytes()):
        raise ValueError("previous run used a different manifest")
    case = manifest["cases"][0]
    baseline = git_file(manifest["baseline_ref"], manifest["baseline_skill_path"])
    candidate = (ROOT / manifest["candidate_skill_path"]).read_text(encoding="utf-8")
    generic = git_file(
        manifest["baseline_ref"], "ci/shared-skills/dingtalk-oa-approval/SKILL.md"
    )
    audit_developer = audit_developer_instructions(render_audit_rules(AgentRole.AUDIT))
    audit_schema = AuditAgentWireResult.model_json_schema()
    settings = manifest["settings"]
    command = business.native_command(
        model=settings["model"], effort=settings["reasoning_effort"],
        developer_instructions=audit_developer, workdir=ROOT,
    )
    results = []
    for side, project_skill in (("baseline", baseline), ("candidate", candidate)):
        prior = next(
            item for item in previous["results"]
            if item["case_id"] == case["id"] and item["side"] == side
        )
        if prior["wire"]["ok"] or not prior["native"]["result"]:
            raise ValueError(f"no invalid native result to correct for {side}")
        subject = business.normalize_review_subject(ROOT, fixed_consumer(case, "A"))
        if subject["digest"] != prior["subject_digest"]:
            raise ValueError(f"fixed Consumer subject changed for {side}")
        full_case = {
            "facts": f"Current time: {settings['current_time']}\n{case['facts']}",
            "skill_excerpt": (
                "Exact frozen generic DingTalk OA Skill:\n" + generic
                + "\nExact frozen project review Skill:\n" + project_skill
            ),
        }
        original_prompt = business._audit_prompt(
            full_case, subject["result"], subject["digest"], audit_schema,
        )
        if digest(original_prompt) != prior["prompt_sha256"]:
            raise ValueError(f"original Audit prompt changed for {side}")
        correction = (
            original_prompt
            + "\n\nWire-format correction only. Your previous JSON was rejected because "
              "candidate_digest was not copied as the exact 64-character value above. "
              "Return the same complete JSON object, preserving all business judgment, "
              "summary, feedback, numbers, fields and values exactly. Change only "
              "candidate_digest to the exact value above; do not recompute it or use a tool. "
              "This is your only correction attempt.\nPrevious JSON:\n"
            + json.dumps(prior["native"]["result"], ensure_ascii=False, sort_keys=True)
        )
        raw = business.run_role(
            command=command, prompt=correction,
            timeout=settings["timeout_seconds_per_case"],
        )
        parsed = business.normalize_role_result(
            ROOT, "audit", raw.get("result"), candidate_digest=subject["digest"])
        after = raw.get("result") or {}
        before = prior["native"]["result"]
        before_without_digest = {k: v for k, v in before.items() if k != "candidate_digest"}
        after_without_digest = {k: v for k, v in after.items() if k != "candidate_digest"}
        only_digest_changed = before_without_digest == after_without_digest
        item = {
            "case_id": case["id"], "side": side,
            "original_prompt_sha256": prior["prompt_sha256"],
            "correction_prompt_sha256": digest(correction),
            "previous_native": prior["native"],
            "corrected_native": raw,
            "corrected_wire": parsed,
            "only_digest_changed": only_digest_changed,
            "digest_binding_correct": bool(
                parsed["ok"] and parsed["result"]["candidate_digest"] == subject["digest"]
            ),
        }
        results.append(item)
        output.write_text(json.dumps({
            "version": "project-scope-audit-wire-correction.v1",
            "manifest_sha256": digest(manifest_path.read_bytes()),
            "previous_run_sha256": digest(previous_path.read_bytes()),
            "harness_sha256": digest(Path(__file__).read_bytes()),
            "model": settings["model"],
            "reasoning_effort": settings["reasoning_effort"],
            "results": results,
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({
            "side": side, "wire_ok": parsed["ok"],
            "only_digest_changed": only_digest_changed,
            "digest_binding_correct": item["digest_binding_correct"],
        }), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--correct-wire-from", type=Path)
    args = parser.parse_args()
    if args.correct_wire_from is None:
        run(args.manifest, args.output)
    else:
        correct_wire(args.manifest, args.correct_wire_from, args.output)


if __name__ == "__main__":
    main()
