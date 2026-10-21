"""Paired anonymous Audit review of a source-bound OA return candidate."""

from __future__ import annotations

import argparse
from contextlib import nullcontext
from hashlib import sha256
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


def _prompts(case: dict, activity_id: str, *, revision: int = 0) -> tuple[str, str, str]:
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
        task=task, proposal_revision=revision,
        operation_id=f"synthetic-oa-audit-revision-{revision}",
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
    selected = "\n".join(
        f"- {name}: read_skill(path='/synthetic/{name}/SKILL.md')"
        for name in case["skill_names"]
    )
    return developer, (
        "## Selected Task Skills\n" + selected + "\n\n"
        + context.render(current_time=FIXED_TIME)
    ), digest


def _run(case: dict, variant: dict, *, native: bool,
         include_trajectory: bool, revision: int = 0,
         workdir: Path | None = None, resume_session_id: str = "") -> dict:
    developer, task_prompt, digest = _prompts(
        case, variant["activity_id"], revision=revision,
    )
    context = (nullcontext(workdir) if workdir is not None else
               tempfile.TemporaryDirectory(prefix="native-output-audit-", dir="/tmp"))
    with context as temporary:
        work = Path(temporary)
        trace = work / "tool-trace.jsonl"
        previous_trace_lines = len(trace.read_text().splitlines()) if trace.exists() else 0
        case_manifest = work / "case.json"
        skill_names = (
            "dingtalk-oa-approval", "stardust-oa-finance-review",
            "stardust-oa-attendance-travel-review",
        )
        skill_content = {
            name: (ROOT / "ci/shared-skills" / name / "SKILL.md").read_text(
                encoding="utf-8"
            ) for name in skill_names
        }
        case_manifest.write_text(json.dumps({
            "cases": [{**case, "skill_lookup_by_name": True}],
            "skill_content": skill_content,
        }, ensure_ascii=False), encoding="utf-8")
        command = [
            "codex", "exec", *(["resume"] if resume_session_id else []),
            "--ignore-user-config", "--ignore-rules",
            *([] if workdir is not None or resume_session_id else ["--ephemeral"]),
            "--skip-git-repo-check",
            *([] if resume_session_id else ["-C", str(work), "-s", "read-only"]),
            "-m", MODEL, "--json",
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
            *([resume_session_id] if resume_session_id else []), "-",
        ]
        try:
            process = subprocess.run(
                command, input=task_prompt, capture_output=True, text=True,
                timeout=240, cwd=work,
            )
        except subprocess.TimeoutExpired:
            return {"variant": variant["id"], "native": native,
                    "status": "timeout"}
        calls = ([json.loads(line) for line in trace.read_text().splitlines()[previous_trace_lines:]]
                 if trace.exists() else [])
        result = None
        validation_error = ""
        try:
            def parser(raw: str):
                return parse_audit_agent_wire_result(
                    raw, proposal_revision=revision, candidate_digest=digest,
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
        selected_skill_reads = {
            name: any(
                call["tool"] == "read_skill" and not call["isError"]
                and (call["arguments"].get("name") == name
                     or f"/{name}/SKILL.md" in str(call["arguments"].get("path", "")))
                for call in calls
            ) for name in case["skill_names"]
        }
        expected = variant["expected_audit"]
        run = {
            "variant": variant["id"], "native": native,
            "status": "completed" if process.returncode == 0 else "cli_failed",
            "session_id": next((json.loads(line).get("thread_id", "")
                                for line in process.stdout.splitlines()
                                if '"thread.started"' in line), ""),
            "revision": revision,
            "selected_skill_sha256": {
                name: sha256(skill_content[name].encode()).hexdigest()
                for name in case["skill_names"]
            },
            "read_selected_skills_this_turn": selected_skill_reads,
            "exit_code": process.returncode, "tool_trace": calls,
            "other_oa_read_count": len(oa_reads) - len(exact_reads),
            "result": result, "validation_error": validation_error,
            "checks": {
                "valid_wire": bool(result),
                "exact_source_read": len(exact_reads) == 1 and not exact_reads[0]["isError"],
                "selected_skills_available": all(selected_skill_reads.values()),
                "bound_candidate": bool(result) and result["candidate_digest"] == digest
                    and result["proposal_revision"] == revision,
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
    parser.add_argument("--continuation", action="store_true")
    args = parser.parse_args()
    base = json.loads(MANIFEST.read_text(encoding="utf-8"))
    case = _probe_case(base, "oa-return-explicit-target")
    specifications = json.loads(CASE_MANIFEST.read_text(encoding="utf-8"))
    variants = [variant for variant in specifications["audit_variants"]
                if not args.variant or variant["id"] in args.variant]
    if args.continuation:
        continuation = specifications["audit_continuation"]
        by_id = {variant["id"]: variant for variant in variants}
        cold_variant = by_id[continuation["cold_variant"]]
        next_variant = by_id[continuation["next_variant"]]
        assert _prompts(case, cold_variant["activity_id"])[0] == _prompts(
            case, next_variant["activity_id"],
            revision=continuation["next_revision"],
        )[0]
        runs = []
        for native in (False, True):
            with tempfile.TemporaryDirectory(prefix="native-output-audit-session-", dir="/tmp") as directory:
                work = Path(directory)
                cold = _run(case, cold_variant, native=native,
                            include_trajectory=args.include_trajectory, workdir=work)
                cold["phase"] = "cold"
                runs.append(cold)
                if not cold.get("session_id") or cold.get("result") is None:
                    continue
                resumed = _run(case, next_variant, native=native,
                               include_trajectory=args.include_trajectory,
                               revision=continuation["next_revision"], workdir=work,
                               resume_session_id=cold["session_id"])
                resumed["phase"] = "same_session"
                resumed["checks"]["reused_session"] = resumed["session_id"] == cold["session_id"]
                resumed["checks"]["selected_skills_available"] = (
                    resumed["checks"]["selected_skills_available"]
                    or (
                        cold["checks"]["selected_skills_available"]
                        and resumed["selected_skill_sha256"] == cold["selected_skill_sha256"]
                    )
                )
                runs.append(resumed)
    else:
        runs = [_run(case, variant, native=native,
                     include_trajectory=args.include_trajectory)
                for variant in variants for native in (False, True)]
    args.output.write_text(json.dumps({
        "version": 1, "case_manifest": str(CASE_MANIFEST.relative_to(ROOT)),
        "model": MODEL,
        "continuation": args.continuation,
        "scope": "anonymous read-only fake MCP; no business provider or Work Profile",
        "runs": runs,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for run in runs:
        print(run["variant"], run.get("phase", "cold"),
              "native" if run["native"] else "free-form",
              run["status"], run.get("checks", {}), flush=True)


if __name__ == "__main__":
    main()
