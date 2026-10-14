#!/usr/bin/env python3
"""One-shot CLI diagnostic for the exact failed v2 candidate-A Consumer input."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import subprocess
from unittest.mock import patch

from app.agent_wire_contracts import ConsumerAgentWireResult
from app.consumer_agent import consumer_developer_instructions
from scripts import eval_consumer_audit_business as business
from scripts.eval_project_scope_consumer import MANIFEST, PROMPT_TEMPLATE, ROOT, git_file


def digest(value: str | bytes) -> str:
    return sha256(value.encode("utf-8") if isinstance(value, str) else value).hexdigest()


def redact(value: str) -> str:
    value = re.sub(r"(?i)\b(bearer|authorization|api[_-]?key|password|token)\b\s*[:=]\s*[^\s,;]+",
                   r"\1=[REDACTED]", value)
    value = re.sub(r"\bsk-[A-Za-z0-9_-]+\b", "[REDACTED-KEY]", value)
    return value


def run(output: Path) -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    previous_path = ROOT / "evals/attempt_review_cases/project_scope.consumer_native_results.v2.json"
    previous = json.loads(previous_path.read_text(encoding="utf-8"))
    case = manifest["cases"][0]
    prior = next(
        item for item in previous["results"]
        if item["case_id"] == case["id"] and item["side"] == "candidate"
    )
    if prior["native"]["exit_code"] != 1 or prior["native"]["result"] is not None:
        raise ValueError("the exact failed candidate-A record is absent")
    candidate = (ROOT / manifest["candidate_skill_path"]).read_text(encoding="utf-8")
    if digest(candidate) != manifest["candidate_skill_sha256"]:
        raise ValueError("candidate Skill has changed")
    generic = git_file(
        manifest["baseline_ref"], "ci/shared-skills/dingtalk-oa-approval/SKILL.md"
    )
    with patch.dict(os.environ, {"CEO_USER_PROMPT_TEMPLATE_PATH": str(PROMPT_TEMPLATE)}):
        developer = consumer_developer_instructions()
    if digest(developer) != previous["consumer_developer_sha256"]:
        raise ValueError("Consumer developer instructions have changed")
    schema = ConsumerAgentWireResult.model_json_schema()
    if digest(json.dumps(schema, sort_keys=True)) != previous["consumer_schema_sha256"]:
        raise ValueError("Consumer wire schema has changed")
    settings = manifest["settings"]
    prompt = business._consumer_prompt({
        "trigger": case["trigger"],
        "facts": f"Current time: {settings['current_time']}\n{case['facts']}",
        "skill_excerpt": (
            "Exact frozen generic DingTalk OA Skill:\n" + generic
            + "\nExact frozen project review Skill:\n" + candidate
        ),
    }, schema)
    if digest(prompt) != prior["prompt_sha256"]:
        raise ValueError("candidate-A prompt differs from the failed v2 input")
    command = business.native_command(
        model=settings["model"], effort=settings["reasoning_effort"],
        developer_instructions=developer, workdir=ROOT,
    )
    completed = subprocess.run(
        command, input=prompt, text=True, capture_output=True,
        timeout=settings["timeout_seconds_per_case"], check=False,
    )
    payload = {
        "version": "project-scope-candidate-a-cli-diagnostic.v1",
        "manifest_sha256": digest(MANIFEST.read_bytes()),
        "previous_run_sha256": digest(previous_path.read_bytes()),
        "candidate_prompt_sha256": digest(prompt),
        "consumer_developer_sha256": digest(developer),
        "model": settings["model"],
        "reasoning_effort": settings["reasoning_effort"],
        "exit_code": completed.returncode,
        "stdout": redact(completed.stdout),
        "stderr": redact(completed.stderr),
    }
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "exit_code": completed.returncode,
        "stdout_chars": len(completed.stdout),
        "stderr_chars": len(completed.stderr),
        "output": str(output),
    }), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.output)


if __name__ == "__main__":
    main()
