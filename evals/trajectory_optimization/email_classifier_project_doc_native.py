"""Compare anonymous classifier decisions with and without native project docs."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.email_classifier_agent import (
    AgentClassificationWireResult,
    bind_agent_classification_candidate,
    build_agent_classification_prompt,
)


MODEL = "gpt-5.6-luna"
DEVELOPER = (
    "You are only an email classifier. Return exactly one strict "
    "AgentClassificationWireResult JSON object and perform no action. "
    "Select an unsubscribe candidate only by its supplied index; "
    "do not return or rewrite its URL."
)
CASES_PATH = Path(__file__).with_name("email_classifier_skill_once.native.cases.v1.json")
SKILL_PATH = Path(__file__).resolve().parents[2] / "ci/shared-skills/ceo-email-classifier/SKILL.md"
CANDIDATE = "https://example.invalid/unsubscribe?token=fictional-candidate-1"


def run(prompt: str, *, exclude_project_doc: bool, root: Path) -> dict[str, object]:
    command = [
        "codex", "exec", "--ignore-user-config", "--ignore-rules", "--ephemeral",
        "--skip-git-repo-check", "-s", "read-only", "-m", MODEL,
        "--disable", "hooks", "-c", "features.plugins=false",
        "-c", "features.apps=false", "-c", "tools.enabled_tools=[]",
        "-c", 'web_search="disabled"',
        "-c", "developer_instructions=" + json.dumps(DEVELOPER),
        "-c", "include_permissions_instructions=false",
        "-c", 'approval_policy="never"',
        *(["-c", "project_doc_max_bytes=0"] if exclude_project_doc else []),
        "--json", "-",
    ]
    process = subprocess.run(
        command, cwd=root, input=prompt, capture_output=True, text=True, timeout=240
    )
    events = []
    for line in process.stdout.splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    messages = [
        event.get("item", {}).get("text", "")
        for event in events if event.get("item", {}).get("type") == "agent_message"
    ]
    result: dict[str, object] = {
        "exit_code": process.returncode,
        "prompt_chars": len(prompt),
        "prompt_sha256": sha256(prompt.encode()).hexdigest(),
        "tool_calls": sum(
            event.get("item", {}).get("type") in {"mcp_tool_call", "function_call", "command_execution"}
            for event in events
        ),
    }
    try:
        result["wire"] = AgentClassificationWireResult.model_validate_json(messages[-1])
    except (IndexError, ValueError) as exc:
        result["validation_error"] = str(exc)[:300]
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repetitions", type=int, default=2)
    args = parser.parse_args()
    if args.repetitions < 1:
        parser.error("repetitions must be positive")
    root = Path(__file__).resolve().parents[2]
    skill = SKILL_PATH.read_text()
    cases = json.loads(CASES_PATH.read_text())
    cases.append({
        "name": "junk_with_candidate",
        "message": {
            "sender": "promo@example.invalid", "subject": "Unwanted campaign",
            "text": "Unsolicited product promotion with no retention value. " * 40,
        },
        "expected": "junk", "unsubscribe_candidates": [CANDIDATE],
    })
    results = []
    for case in cases:
        candidates = case.get("unsubscribe_candidates", [])
        payload = {
            "allowed_category_keys": ["work", "junk", "notification"],
            "category_descriptions": {
                "work": "Customer project discussion",
                "junk": "Unwanted irrelevant no-retention mail",
                "notification": "System notice",
            },
            "message": case["message"],
            "provider_unread": True,
            "unsubscribe_candidates": candidates,
        }
        prompt = build_agent_classification_prompt(payload, skill_text=skill)
        for repetition in range(1, args.repetitions + 1):
            for arm, exclude in (("baseline", False), ("candidate", True)):
                result = run(prompt, exclude_project_doc=exclude, root=root)
                wire = result.pop("wire", None)
                if wire is not None:
                    bound = bind_agent_classification_candidate(
                        wire, allowed_category_keys=payload["allowed_category_keys"],
                        unsubscribe_candidates=candidates,
                    )
                    result.update(
                        category=wire.category, certainty=wire.certainty,
                        important=wire.important,
                        candidate_index=wire.unsubscribe_candidate_index,
                        bound_exactly=(bound.unsubscribe_url == CANDIDATE)
                        if candidates else bound.unsubscribe_url is None,
                    )
                result.update(
                    case=case["name"], repetition=repetition, arm=arm,
                    expected_category=case["expected"],
                    passed=(result["exit_code"] == 0 and result["tool_calls"] == 0
                            and result.get("category") == case["expected"]
                            and result.get("certainty") == "certain"
                            and result.get("bound_exactly") is True
                            and (not candidates or result.get("candidate_index") == 0)),
                )
                results.append(result)
                print(json.dumps({key: result[key] for key in ("case", "repetition", "arm", "passed")}), flush=True)
    args.output.write_text(json.dumps({
        "model": MODEL,
        "scope": "Anonymous read-only Codex OAuth; no mailbox, business MCP or external action",
        "skill_sha256": sha256(skill.encode()).hexdigest(),
        "case_count": len(cases), "repetitions": args.repetitions,
        "accepted": all(item["passed"] for item in results),
        "results": results,
    }, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
