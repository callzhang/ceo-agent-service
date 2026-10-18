"""Compare Audit interpretation of a fictional scheduled document return."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.agent_context import AgentTaskContext, AuditTurnContext
from app.agent_contracts import ConsumerAgentResult, ConsumerProposal


MODEL = "gpt-5.6-luna"
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "returned_readback_identified": {"type": "boolean"},
        "report_content_verified": {"type": "boolean"},
        "tool_call_id": {"type": "string"},
    },
    "required": [
        "returned_readback_identified",
        "report_content_verified",
        "tool_call_id",
    ],
}
RECEIPT = {
    "consumer_run_id": 43,
    "tool_call_id": "synthetic-call-1",
    "operation": "created",
    "document_node_id": "synthetic-node-1",
    "document_title": "Synthetic report",
    "readback_sha256": "b" * 64,
    "readback_bytes": 35,
}


def _context(receipts: tuple[dict[str, object], ...]) -> str:
    proposal = ConsumerProposal.model_validate({
        "objective": "Review fictional report notice",
        "actions": [{
            "description": "Send fictional report notice",
            "action_identity": "send-synthetic-report",
            "capability": "dingtalk-chat",
            "operation": "send_message",
            "target": {"conversation_id": "synthetic-group"},
            "payload": {"text": "The synthetic report was saved and all its figures have been verified."},
        }],
        "sourced_facts": [],
        "authored_judgment": "Consumer says the report was saved and checked.",
    })
    candidate = ConsumerAgentResult.model_validate({
        "outcome": "proposal",
        "summary": "Prepared a complete candidate.",
        "proposal": proposal.model_dump(mode="json"),
        "decision_options": [],
        "error": {"code": "", "retryable": False, "authorization_required": False},
        "risk": "low", "confidence": 1.0,
        "rule_coverage": 1.0, "information_completeness": 1.0,
    })
    task = AgentTaskContext(
        task_id=42, channel="scheduled", conversation_id="synthetic-group",
        conversation_title="Fictional report group", single_chat=False,
        trigger_message_id="synthetic-trigger", trigger_sender="Scheduler",
        trigger_text="Review a fictional scheduled report notice.",
        trigger_create_time="2026-07-28 14:15:00 +0800",
        messages=(), materials=(), prior_receipts=(),
    )
    rendered = AuditTurnContext(
        task=task, proposal_revision=0, operation_id="synthetic-op",
        candidate=candidate, candidate_digest="a" * 64,
        audit_rules="Assess the proposed notice against available evidence.",
        scheduled_work_receipts=receipts,
    ).render(current_time="2026-07-28 14:16:00 +0800")
    return (
        rendered + "\n\nDiagnostic only: output whether a service-recorded Consumer "
        "document readback was identified, whether the report content itself was "
        "verified, and the returned tool_call_id or empty string. Do not call "
        "tools or perform actions."
    )


def _run(prompt: str, *, directory: str, schema: Path) -> dict[str, object]:
    result = subprocess.run(
        ["codex", "exec", "--ignore-user-config", "--ignore-rules", "--ephemeral",
         "--skip-git-repo-check", "-C", directory, "-s", "read-only", "-m", MODEL,
         "--json", "--output-schema", str(schema), "-"],
        input=prompt, capture_output=True, text=True, timeout=240,
    )
    messages: list[str] = []
    tool_calls = 0
    for line in result.stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        item = event.get("item", {})
        if event.get("type") == "item.completed" and item.get("type") == "agent_message":
            messages.append(item.get("text", ""))
        if event.get("type") == "item.completed" and item.get("type") in {
            "mcp_tool_call", "command_execution",
        }:
            tool_calls += 1
    try:
        decision = json.loads(messages[-1]) if messages else None
    except json.JSONDecodeError:
        decision = None
    return {
        "exit_code": result.returncode,
        "tool_calls": tool_calls,
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "prompt_chars": len(prompt),
        "result": decision,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repetitions", type=int, default=3)
    args = parser.parse_args()
    if args.repetitions < 1:
        parser.error("repetitions must be positive")
    results = []
    with tempfile.TemporaryDirectory(prefix="scheduled-audit-receipt-", dir="/tmp") as directory:
        schema = Path(directory) / "schema.json"
        schema.write_text(json.dumps(SCHEMA))
        for name, receipts in (("empty", ()), ("returned", (RECEIPT,))):
            prompt = _context(receipts)
            for repetition in range(1, args.repetitions + 1):
                run = _run(prompt, directory=directory, schema=schema)
                run.update(case=name, repetition=repetition)
                results.append(run)
                print(name, repetition, run["result"], flush=True)
    args.output.write_text(json.dumps({
        "model": MODEL,
        "scope": "Fictional read-only diagnostic; no Work Profile, business MCP or action",
        "repetitions": args.repetitions,
        "results": results,
    }, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
