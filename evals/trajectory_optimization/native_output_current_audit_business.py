"""Paired anonymous Audit review of a source-bound OA return candidate."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile

from app.agent_context import AgentTaskContext, AuditTurnContext
from app.agent_contracts import ConsumerAgentResult
from app.agent_wire_contracts import parse_audit_agent_wire_result
from app.consumer_agent import audit_developer_instructions
from app.native_agent_output import native_output_schema_path, parse_native_result
from app.reviewed_candidates import candidate_digest
from evals.trajectory_optimization.consumer_capability_dedupe_native import (
    FIXED_TIME, MANIFEST, ROOT, SERVER_MODULE,
)
from evals.trajectory_optimization.native_output_current_business import (
    CASE_MANIFEST, MODEL, _probe_case,
)


def _candidate(activity_id: str) -> ConsumerAgentResult:
    return ConsumerAgentResult.model_validate({
        "outcome": "proposal",
        "summary": "OA-EXACT-204 缺少返程交通票据，建议退回补充。",
        "proposal": {
            "objective": "请申请人补充返程交通票据。",
            "actions": [{
                "description": "退回 OA-EXACT-204 补充票据",
                "action_identity": "synthetic-oa-return-204",
                "capability": "dingtalk-oa", "operation": "revert_task",
                "target": {
                    "process_instance_id": "OA-EXACT-204",
                    "task_id": "OA-TASK-204",
                    "target_activity_id": activity_id,
                },
                "payload": {
                    "remark": "缺少返程交通票据，请补充后重新提交。",
                    "revert_action": "REVERT_FOR_RESUBMIT",
                },
                "effect": "external",
            }],
            "sourced_facts": [],
            "authored_judgment": "票据不完整，无法确认报销总额。",
        },
        "decision_options": [],
        "error": {"code": "", "retryable": False,
                  "authorization_required": False},
        "risk": "medium", "confidence": 0.9,
        "rule_coverage": 1.0, "information_completeness": 1.0,
        "durable_memories": [],
    })


def _prompts(case: dict, activity_id: str) -> tuple[str, str, str]:
    candidate = _candidate(activity_id)
    source = case["messages"][0]
    task = AgentTaskContext(
        task_id=204, channel="dingtalk", conversation_id=case["conversation_id"],
        conversation_title=source["conversation_title"], single_chat=False,
        trigger_message_id=source["open_message_id"],
        trigger_sender=source["sender_name"], trigger_text=case["trigger"],
        trigger_create_time=source["create_time"],
        messages=(), materials=(), prior_receipts=(),
        required_proposal_action={"capability": "dingtalk-oa"},
        skill_names=tuple(case["skill_names"]),
    )
    digest = candidate_digest(candidate)
    context = AuditTurnContext(
        task=task, proposal_revision=0, operation_id="synthetic-oa-audit",
        candidate=candidate, candidate_digest=digest,
        audit_rules="Review the exact current OA source, selected Skills and action contract; judge the complete candidate.",
    )
    developer = audit_developer_instructions(
        context.audit_rules,
        runtime_context=(
            "Anonymous read-only OA Audit fixture. Available direct MCP reads: "
            "mcp__agent_cli__read_skill, mcp__agent_cli__read_dingtalk_oa, "
            "mcp__agent_cli__read_system_action_contract. No write tools."
        ),
        work_profile="", purpose_name="oa",
    )
    return developer, context.render(current_time=FIXED_TIME), digest


def _run(case: dict, variant: dict, *, native: bool,
         include_trajectory: bool) -> dict:
    developer, task_prompt, digest = _prompts(case, variant["activity_id"])
    with tempfile.TemporaryDirectory(prefix="native-output-audit-", dir="/tmp") as temporary:
        work = Path(temporary)
        trace = work / "tool-trace.jsonl"
        case_manifest = work / "case.json"
        case_manifest.write_text(json.dumps({
            "cases": [case],
            "skill_content": {
                "dingtalk-oa-approval": (
                    "# Synthetic OA review\nUse only the current instance's exact "
                    "task and target activity IDs for a return action."
                ),
                "stardust-oa-finance-review": (
                    "# Synthetic finance review\nMissing travel receipts require a "
                    "return for completion when the source gives an exact return target."
                ),
            },
        }, ensure_ascii=False), encoding="utf-8")
        command = [
            "codex", "exec", "--ignore-user-config", "--ignore-rules",
            "--ephemeral", "--skip-git-repo-check", "-C", str(work),
            "-s", "read-only", "-m", MODEL, "--json",
            "--disable", "shell_tool", "--disable", "apps",
            "--disable", "plugins", "--disable", "hooks",
            "--disable", "multi_agent",
            "-c", 'model_reasoning_effort="low"',
            "-c", 'sandbox_mode="read-only"',
            "-c", "project_doc_max_bytes=0",
            "-c", 'web_search="disabled"',
            "-c", "developer_instructions=" + json.dumps(developer, ensure_ascii=False),
            "-c", "mcp_servers.agent_cli.command=" + json.dumps(sys.executable),
            "-c", "mcp_servers.agent_cli.args=" + json.dumps([
                "-m", SERVER_MODULE, str(case_manifest), case["id"], str(trace),
            ]),
            "-c", "mcp_servers.agent_cli.cwd=" + json.dumps(str(ROOT)),
            "-c", 'mcp_servers.agent_cli.default_tools_approval_mode="approve"',
            *(["--output-schema", str(native_output_schema_path("audit"))]
              if native else []),
            "-",
        ]
        try:
            process = subprocess.run(
                command, input=task_prompt, capture_output=True, text=True,
                timeout=240, cwd=work,
            )
        except subprocess.TimeoutExpired:
            return {"variant": variant["id"], "native": native,
                    "status": "timeout"}
        calls = ([json.loads(line) for line in trace.read_text().splitlines()]
                 if trace.exists() else [])
        result = None
        validation_error = ""
        try:
            def parser(raw: str):
                return parse_audit_agent_wire_result(
                    raw, proposal_revision=0, candidate_digest=digest,
                )

            parsed = (parse_native_result(
                process.stdout, "audit", parser=parser,
            ) if native else parser(process.stdout))
            result = parsed.model_dump(mode="json")
        except (ValueError, TypeError) as exc:
            validation_error = str(exc)[:350]
        oa_reads = [call for call in calls if call["tool"] == "read_dingtalk_oa"]
        exact_reads = [call for call in oa_reads
                       if call["arguments"].get("process_instance_id") == "OA-EXACT-204"]
        expected = variant["expected_audit"]
        run = {
            "variant": variant["id"], "native": native,
            "status": "completed" if process.returncode == 0 else "cli_failed",
            "exit_code": process.returncode, "tool_trace": calls,
            "other_oa_read_count": len(oa_reads) - len(exact_reads),
            "result": result, "validation_error": validation_error,
            "checks": {
                "valid_wire": bool(result),
                "exact_source_read": len(exact_reads) == 1 and not exact_reads[0]["isError"],
                "bound_candidate": bool(result) and result["candidate_digest"] == digest
                    and result["proposal_revision"] == 0,
                "review_outcome": bool(result) and result["outcome"] == expected,
                "feedback_when_returned": bool(result) and (
                    (expected == "return" and result["feedback"] is not None)
                    or (expected == "approve" and result["feedback"] is None)
                ),
            },
        }
        if include_trajectory:
            run["native_stdout"] = process.stdout
            run["native_stderr"] = process.stderr
            run["developer"] = developer
            run["task_prompt"] = task_prompt
        return run


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--variant", choices=("supported-return", "wrong-activity"),
                        action="append")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--include-trajectory", action="store_true")
    args = parser.parse_args()
    base = json.loads(MANIFEST.read_text(encoding="utf-8"))
    case = _probe_case(base, "oa-return-explicit-target")
    specifications = json.loads(CASE_MANIFEST.read_text(encoding="utf-8"))
    variants = [variant for variant in specifications["audit_variants"]
                if not args.variant or variant["id"] in args.variant]
    runs = [_run(case, variant, native=native,
                 include_trajectory=args.include_trajectory)
            for variant in variants for native in (False, True)]
    args.output.write_text(json.dumps({
        "version": 1, "case_manifest": str(CASE_MANIFEST.relative_to(ROOT)),
        "model": MODEL,
        "scope": "anonymous read-only fake MCP; no business provider or Work Profile",
        "runs": runs,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for run in runs:
        print(run["variant"], "native" if run["native"] else "free-form",
              run["status"], run.get("checks", {}), flush=True)


if __name__ == "__main__":
    main()
