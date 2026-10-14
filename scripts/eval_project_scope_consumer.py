#!/usr/bin/env python3
"""Native, tool-free Consumer comparison for three frozen project scope cases."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
from unittest.mock import patch

from app.agent_wire_contracts import ConsumerAgentWireResult
from app.consumer_agent import consumer_developer_instructions
from scripts import eval_consumer_audit_business as business

ROOT = business.ROOT
MANIFEST = ROOT / "evals/attempt_review_cases/project_scope.v2.json"
V1_MANIFEST = ROOT / "evals/attempt_review_cases/project_scope.v1.json"
PROMPT_TEMPLATE = ROOT / "app/defaults/user_prompt.md"


def digest(data: str | bytes) -> str:
    return sha256(data.encode("utf-8") if isinstance(data, str) else data).hexdigest()


def git_file(ref: str, path: str) -> str:
    return subprocess.run(
        ["git", "show", f"{ref}:{path}"], cwd=ROOT,
        capture_output=True, text=True, check=True, timeout=30,
    ).stdout


def run(manifest_path: Path, output: Path) -> None:
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    original = json.loads(V1_MANIFEST.read_text(encoding="utf-8"))
    if manifest["version"] != "project-scope-consumer.v2" or len(manifest["cases"]) != 3:
        raise ValueError("unexpected frozen Consumer case set")
    if [(case["id"], case["facts"]) for case in manifest["cases"]] != [
        (case["id"], case["facts"]) for case in original["cases"]
    ]:
        raise ValueError("Consumer case facts differ from the fixed Audit cases")
    baseline = git_file(manifest["baseline_ref"], manifest["baseline_skill_path"])
    candidate = (ROOT / manifest["candidate_skill_path"]).read_text(encoding="utf-8")
    if digest(baseline) != manifest["baseline_skill_sha256"]:
        raise ValueError("baseline Skill source hash changed")
    if digest(candidate) != manifest["candidate_skill_sha256"]:
        raise ValueError("candidate Skill source hash changed")
    generic = git_file(
        manifest["baseline_ref"], "ci/shared-skills/dingtalk-oa-approval/SKILL.md"
    )
    if PROMPT_TEMPLATE.read_text(encoding="utf-8").strip() != "{{task_context}}":
        raise ValueError("verified production Consumer prompt template changed")
    with patch.dict(os.environ, {"CEO_USER_PROMPT_TEMPLATE_PATH": str(PROMPT_TEMPLATE)}):
        developer = consumer_developer_instructions()
    schema = ConsumerAgentWireResult.model_json_schema()
    settings = manifest["settings"]
    command = business.native_command(
        model=settings["model"], effort=settings["reasoning_effort"],
        developer_instructions=developer, workdir=ROOT,
    )
    results = []
    for case in manifest["cases"]:
        for side, project_skill in (("baseline", baseline), ("candidate", candidate)):
            prompt = business._consumer_prompt({
                "trigger": case["trigger"],
                "facts": f"Current time: {settings['current_time']}\n{case['facts']}",
                "skill_excerpt": (
                    "Exact frozen generic DingTalk OA Skill:\n" + generic
                    + "\nExact frozen project review Skill:\n" + project_skill
                ),
            }, schema)
            raw = business.run_role(
                command=command, prompt=prompt,
                timeout=settings["timeout_seconds_per_case"],
            )
            parsed = business.normalize_role_result(ROOT, "consumer", raw.get("result"))
            result = parsed.get("result") or {}
            item = {
                "case_id": case["id"], "side": side,
                "semantic_criterion": case["semantic_criterion"],
                "case_facts_sha256": digest(case["facts"]),
                "prompt_sha256": digest(prompt),
                "native": raw,
                "wire": parsed,
                "wire_valid": bool(raw["ok"] and parsed["ok"]),
                "outcome": result.get("outcome"),
            }
            results.append(item)
            output.write_text(json.dumps({
                "version": manifest["version"],
                "manifest_sha256": digest(manifest_bytes),
                "v1_manifest_sha256": digest(V1_MANIFEST.read_bytes()),
                "harness_sha256": digest(Path(__file__).read_bytes()),
                "shared_harness_sha256": digest(Path(business.__file__).read_bytes()),
                "baseline_ref": manifest["baseline_ref"],
                "baseline_skill_sha256": digest(baseline),
                "candidate_skill_sha256": digest(candidate),
                "generic_skill_sha256": digest(generic),
                "production_user_prompt_template_sha256": digest(PROMPT_TEMPLATE.read_bytes()),
                "consumer_developer_sha256": digest(developer),
                "consumer_schema_sha256": digest(json.dumps(schema, sort_keys=True)),
                "settings": settings,
                "results": results,
            }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(json.dumps({
                "case_id": case["id"], "side": side,
                "native_ok": raw["ok"], "wire_ok": parsed["ok"],
                "outcome": result.get("outcome"),
            }), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.manifest, args.output)


if __name__ == "__main__":
    main()
