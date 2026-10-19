"""Compare anonymous unsubscribe-page decisions with and without project docs."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.email_unsubscribe_page_judge import (
    _DEVELOPER_INSTRUCTIONS,
    build_prompt,
    parse_page_judgement,
)


MODEL = "gpt-5.6-luna"
CASES = (
    ("done_with_control", "Your subscription to Example updates has been canceled. You will receive no more updates.", ("Manage preferences",), "done"),
    ("action_required", "To stop Example alerts, select Unsubscribe below. Your subscription remains active until you do.", ("Unsubscribe",), "action_required"),
    ("already_off_with_login_nav", "Sign in | Help. You were already unsubscribed from Example alerts before opening this page.", ("Sign in",), "already_unsubscribed"),
)


def run(prompt: str, *, exclude_project_doc: bool, cwd: Path) -> dict[str, object]:
    command = [
        "codex", "exec", "--ignore-user-config", "--ignore-rules", "--ephemeral",
        "--skip-git-repo-check", "-s", "read-only", "-m", MODEL,
        "-c", "developer_instructions=" + json.dumps(_DEVELOPER_INSTRUCTIONS),
        "-c", "include_permissions_instructions=false",
        "-c", 'approval_policy="never"',
        *(["-c", "project_doc_max_bytes=0"] if exclude_project_doc else []),
        "--json", "-",
    ]
    result = subprocess.run(
        command, cwd=cwd, input=prompt, capture_output=True, text=True, timeout=240,
    )
    events = []
    for line in result.stdout.splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    messages = [
        event.get("item", {}).get("text", "")
        for event in events if event.get("item", {}).get("type") == "agent_message"
    ]
    raw = messages[-1] if messages else ""
    try:
        decision = parse_page_judgement(raw).model_dump(mode="json")
        validation_error = ""
    except ValueError as exc:
        decision = None
        validation_error = str(exc)[:250]
    return {
        "exit_code": result.returncode,
        "prompt_chars": len(prompt),
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "native_event_count": len(events),
        "tool_call_count": sum(
            event.get("item", {}).get("type") in {"mcp_tool_call", "function_call", "command_execution"}
            for event in events
        ),
        "decision": decision,
        "validation_error": validation_error,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repetitions", type=int, default=2)
    args = parser.parse_args()
    if args.repetitions < 1:
        parser.error("repetitions must be positive")
    cwd = Path(__file__).resolve().parents[2]
    results = []
    for name, page, controls, expected in CASES:
        prompt = build_prompt("example.test", page, controls)
        for repetition in range(1, args.repetitions + 1):
            for arm, exclude in (("baseline", False), ("candidate", True)):
                result = run(prompt, exclude_project_doc=exclude, cwd=cwd)
                decision = result["decision"]
                result.update(
                    case=name, repetition=repetition, arm=arm,
                    expected_state=expected,
                    state_matches=decision is not None and decision["state"] == expected,
                    evidence_cites_page=decision is not None and decision["evidence"] in page,
                )
                results.append(result)
                print(json.dumps({
                    "case": name, "repetition": repetition, "arm": arm,
                    "state": decision["state"] if decision else None,
                    "expected": expected, "tool_calls": result["tool_call_count"],
                }), flush=True)
    args.output.write_text(json.dumps({
        "model": MODEL,
        "scope": "Anonymous read-only Codex OAuth; no mailbox, business MCP or browser action",
        "case_count": len(CASES),
        "repetitions": args.repetitions,
        "results": results,
    }, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
