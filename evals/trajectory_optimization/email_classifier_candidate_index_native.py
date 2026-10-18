"""Anonymous, tool-free native check of the classifier's index-only wire result."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import tempfile

from app.business_skills import codex_skill_config_override
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
URL = (
    "https://example.invalid/unsubscribe?campaign=synthetic-october"
    "&recipient=fictional-opaque-8f67a2d05d9e4c8fbabb41d8c7593e24"
    "&token=5fe1c639a0bd4d12b7736407f859cab1"
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repetitions", type=int, default=2)
    args = parser.parse_args()
    if args.repetitions < 1:
        parser.error("repetitions must be positive")

    root = Path(__file__).resolve().parents[2]
    skill = (root / "ci/shared-skills/ceo-email-classifier/SKILL.md").read_text()
    payload = {
        "allowed_category_keys": ["junk", "work", "notification"],
        "category_descriptions": {
            "junk": "Unwanted irrelevant no-retention mail",
            "work": "Customer project discussion",
            "notification": "System notice",
        },
        "message": {
            "sender": "promo@example.invalid",
            "subject": "Unsolicited unrelated promotion",
            "text": (
                "This unsolicited promotion advertises unrelated products. "
                "It has no retention value and is not a customer or project message. "
            ) * 130,
        },
        "provider_unread": True,
        "unsubscribe_candidates": [URL],
    }
    prompt = build_agent_classification_prompt(payload, skill_text=skill)
    results = []
    with tempfile.TemporaryDirectory(prefix="classifier-candidate-", dir="/tmp") as directory:
        command = [
            "codex", "exec", "--ignore-user-config", "--ignore-rules", "--ephemeral",
            "--skip-git-repo-check", "-C", directory, "-s", "read-only",
            "-m", MODEL, "--disable", "hooks",
            "-c", "developer_instructions=" + json.dumps(DEVELOPER),
            "-c", "include_permissions_instructions=false",
            "-c", codex_skill_config_override(()),
            "-c", "features.plugins=false", "-c", "features.apps=false",
            "-c", "tools.enabled_tools=[]", "-c", 'web_search="disabled"',
            "--json", "-",
        ]
        for repetition in range(1, args.repetitions + 1):
            run = subprocess.run(
                command, input=prompt, capture_output=True, text=True, timeout=240
            )
            events = []
            for line in run.stdout.splitlines():
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
            messages = [
                event.get("item", {}).get("text", "")
                for event in events
                if event.get("item", {}).get("type") == "agent_message"
            ]
            result = {
                "repetition": repetition,
                "exit_code": run.returncode,
                "tool_calls": sum(
                    event.get("item", {}).get("type") in {"mcp_tool_call", "function_call"}
                    for event in events
                ),
            }
            try:
                wire = AgentClassificationWireResult.model_validate_json(messages[-1])
                bound = bind_agent_classification_candidate(
                    wire,
                    allowed_category_keys=payload["allowed_category_keys"],
                    unsubscribe_candidates=payload["unsubscribe_candidates"],
                )
                result.update(
                    category=wire.category,
                    certainty=wire.certainty,
                    candidate_index=wire.unsubscribe_candidate_index,
                    wire_has_url="unsubscribe_url" in wire.model_dump(),
                    bound_exactly=bound.unsubscribe_url == URL,
                )
            except (IndexError, ValueError) as exc:
                result["validation_error"] = str(exc)[:300]
            results.append(result)

    report = {
        "case": "anonymous long unwanted promotion with one opaque candidate",
        "model": MODEL,
        "transport": "Codex OAuth read-only; no business MCP or tools; no output schema",
        "skill_sha256": sha256(skill.encode()).hexdigest(),
        "prompt_chars": len(prompt),
        "prompt_sha256": sha256(prompt.encode()).hexdigest(),
        "results": results,
        "accepted": all(
            result.get("exit_code") == 0
            and result.get("tool_calls") == 0
            and result.get("category") == "junk"
            and result.get("certainty") == "certain"
            and result.get("candidate_index") == 0
            and result.get("wire_has_url") is False
            and result.get("bound_exactly") is True
            for result in results
        ),
    }
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
