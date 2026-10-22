"""Paired anonymous native classification check for classifier Skill catalog scope."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.business_skills import (  # noqa: E402
    codex_inlined_skill_config_override,
    codex_skill_config_override,
)
from app.email_classifier_agent import (  # noqa: E402
    AgentClassificationWireResult,
    bind_agent_classification_candidate,
    build_agent_classification_prompt,
)


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "evals/trajectory_optimization/email_classifier_category_table_pair.v1.json"
SKILL = ROOT / "ci/shared-skills/ceo-email-classifier/SKILL.md"
DEVELOPER = (
    "You are only an email classifier. Return exactly one strict "
    "AgentClassificationWireResult JSON object and perform no action. "
    "Select an unsubscribe candidate only by its supplied index; "
    "do not return or rewrite its URL."
)


def run(prompt: str, *, override: str, model: str) -> dict[str, object]:
    command = [
        "codex", "exec", "--ignore-user-config", "--ignore-rules", "--ephemeral",
        "--skip-git-repo-check", "-s", "read-only", "-m", model,
        "--disable", "hooks", "-c", "features.plugins=false",
        "-c", "features.apps=false", "-c", "features.multi_agent=false",
        "-c", "tools.enabled_tools=[]", "-c", 'web_search="disabled"',
        "-c", "model_reasoning_effort=\"high\"",
        "-c", "developer_instructions=" + json.dumps(DEVELOPER),
        "-c", "include_permissions_instructions=false",
        "-c", 'approval_policy="never"', "-c", "project_doc_max_bytes=0",
        "-c", override, "--json", "-",
    ]
    process = subprocess.run(
        command, cwd=ROOT, input=prompt, capture_output=True, text=True, timeout=240
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
        "prompt_sha256": sha256(prompt.encode()).hexdigest(),
        "skill_budget_warnings": sum(
            "Skill descriptions were shortened to fit the skills context budget."
            in json.dumps(event)
            for event in events
        ),
        "tool_calls": sum(
            event.get("item", {}).get("type")
            in {"mcp_tool_call", "function_call", "command_execution"}
            for event in events
        ),
    }
    try:
        wire = AgentClassificationWireResult.model_validate_json(messages[-1])
    except (IndexError, ValueError) as exc:
        result["validation_error"] = str(exc)[:200]
    else:
        result["wire"] = wire
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default="gpt-6-luna")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    fixture = json.loads(FIXTURE.read_text())["fixture"]
    skill = SKILL.read_text()
    baseline = codex_inlined_skill_config_override(("ceo-email-classifier",))
    candidate = codex_skill_config_override((), inlined_names=("ceo-email-classifier",))
    cases = fixture["cases"][: args.limit or None]
    results = []
    for case in cases:
        candidates = (
            [entry["url"] for entry in fixture["unsubscribe_candidates_for_junk"]]
            if case["expected_category"] == "junk" else []
        )
        payload = {
            "allowed_category_keys": list(fixture["category_descriptions"]),
            "category_descriptions": fixture["category_descriptions"],
            "message": {
                "sender": case["sender"], "subject": case["subject"],
                "text": case["text"],
            },
            "provider_unread": fixture["provider_unread"],
            "unsubscribe_candidates": candidates,
        }
        prompt = build_agent_classification_prompt(payload, skill_text=skill)
        for arm, override in (("baseline", baseline), ("candidate", candidate)):
            result = run(prompt, override=override, model=args.model)
            wire = result.pop("wire", None)
            if wire is not None:
                try:
                    bound = bind_agent_classification_candidate(
                        wire,
                        allowed_category_keys=payload["allowed_category_keys"],
                        unsubscribe_candidates=candidates,
                    )
                except ValueError as exc:
                    result["validation_error"] = str(exc)[:200]
                else:
                    result.update(
                        category=wire.category, important=wire.important,
                        certainty=wire.certainty,
                        candidate_index=wire.unsubscribe_candidate_index,
                        bound_candidate=(
                            bound.unsubscribe_url == candidates[wire.unsubscribe_candidate_index]
                            if wire.unsubscribe_candidate_index is not None
                            else bound.unsubscribe_url is None
                        ),
                    )
            result.update(case=case["expected_category"], arm=arm)
            results.append(result)
            print(json.dumps({
                "case": result["case"], "arm": arm,
                "valid": "validation_error" not in result,
                "category": result.get("category"),
            }), flush=True)
    pairs = [results[index:index + 2] for index in range(0, len(results), 2)]
    fields = ("category", "important", "certainty", "candidate_index")
    accepted = all(
        all(item["exit_code"] == 0 and item["tool_calls"] == 0
            and "validation_error" not in item and item.get("bound_candidate") is True
            and item.get("category") == item["case"] for item in pair)
        and all(pair[0][field] == pair[1][field] for field in fields)
        and pair[0]["prompt_sha256"] == pair[1]["prompt_sha256"]
        for pair in pairs
    )
    args.output.write_text(json.dumps({
        "model": args.model, "skill_sha256": sha256(skill.encode()).hexdigest(),
        "fixture": str(FIXTURE.relative_to(ROOT)), "case_count": len(cases),
        "accepted": accepted, "results": results,
    }, ensure_ascii=False, indent=2) + "\n")
    print("accepted", accepted, flush=True)


if __name__ == "__main__":
    main()
