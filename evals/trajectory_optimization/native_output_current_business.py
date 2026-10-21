"""Paired anonymous Consumer business check for the current native schema."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile

from app.agent_wire_contracts import parse_consumer_agent_wire_result
from app.consumer_agent import consumer_developer_sections
from app.native_agent_output import native_output_schema_path, parse_native_result
from app.prompt_composition import render_prompt_sections
from evals.trajectory_optimization.consumer_capability_dedupe_native import (
    MANIFEST, ROOT, SERVER_MODULE, _case, _checks, _old_capability_text,
    _prompts,
)


MODEL = "gpt-6-luna"
CASE_MANIFEST = ROOT / "evals/trajectory_optimization/native_output_current_business.v1.json"


def _probe_case(manifest: dict, case_id: str) -> dict:
    specification = next(
        item for item in json.loads(CASE_MANIFEST.read_text(encoding="utf-8"))["cases"]
        if item["id"] == case_id
    )
    case = _case(manifest, specification["base_case"])
    case["id"] = case_id
    if "trigger" in specification:
        case["trigger"] = specification["trigger"]
        case["messages"][0]["content"] = case["trigger"]
    if "skill_names" in specification:
        case["skill_names"] = tuple(specification["skill_names"])
    if "skill_source" in specification:
        case["skill_source"] = specification["skill_source"]
    if "oa_instance_patch" in specification:
        case["oa_instance"] = {
            **case["oa_instance"], **specification["oa_instance_patch"],
        }
    return case


def _checks_for_probe(run: dict) -> dict[str, bool]:
    if run["case"] != "oa-return-explicit-target":
        return _checks(run)
    result = run.get("result") or {}
    oa_reads = [call for call in run.get("tool_trace", [])
                if call["tool"] == "read_dingtalk_oa"]
    actions = (result.get("proposal") or {}).get("actions") or []
    action = actions[0] if len(actions) == 1 else {}
    expected_target = {
        "process_instance_id": "OA-EXACT-204",
        "task_id": "OA-TASK-204",
        "target_activity_id": "ACT-RETURN-204",
    }
    payload = action.get("payload") or {}
    return {
        "valid_wire": bool(result),
        "native_completed": run["status"] == "completed",
        "exact_oa_read": len(oa_reads) == 1 and not oa_reads[0]["isError"]
            and oa_reads[0]["arguments"].get("process_instance_id") == "OA-EXACT-204",
        "return_not_approve": result.get("outcome") == "proposal"
            and action.get("capability") == "dingtalk-oa"
            and action.get("operation") == "revert_task",
        "source_bound_target": action.get("target") == expected_target,
        "source_bound_revert": payload.get("revert_action") == "REVERT_FOR_RESUBMIT",
        "missing_receipt_reason": "返程" in str(payload.get("remark", "")),
    }


def _run(case: dict, *, native: bool, manifest: dict, developer: str,
         task_prompt: str, include_trajectory: bool = False) -> dict:
    case_id = case["id"]
    purpose = "oa" if case.get("kind") == "oa" else "general"
    with tempfile.TemporaryDirectory(prefix="native-output-business-", dir="/tmp") as temporary:
        work = Path(temporary)
        case_manifest = work / "case.json"
        trace = work / "tool-trace.jsonl"
        skill_content = dict(manifest["skill_content"])
        if case.get("skill_source"):
            skill_content["ceo-message-triage"] = (
                ROOT / case["skill_source"]
            ).read_text(encoding="utf-8")
        if purpose == "oa":
            skill_content.update({
                "dingtalk-oa-approval": (
                    "# Synthetic OA review\nRead the exact instance. Use only its returned "
                    "task and target activity IDs for a return action."
                ),
                "stardust-oa-finance-review": (
                    "# Synthetic finance review\nMissing travel receipts require a "
                    "return for completion when the source gives an exact return target."
                ),
            })
        case_manifest.write_text(json.dumps({
            "cases": [case], "skill_content": skill_content,
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
                "-m", SERVER_MODULE, str(case_manifest), case_id, str(trace),
            ]),
            "-c", "mcp_servers.agent_cli.cwd=" + json.dumps(str(ROOT)),
            "-c", 'mcp_servers.agent_cli.default_tools_approval_mode="approve"',
            *(["--output-schema", str(native_output_schema_path(
                "consumer", purpose=purpose,
            ))]
              if native else []),
            "-",
        ]
        try:
            process = subprocess.run(
                command, input=task_prompt, capture_output=True, text=True,
                timeout=240, cwd=work,
            )
        except subprocess.TimeoutExpired:
            return {"case": case_id, "native": native, "status": "timeout"}
        calls = ([json.loads(line) for line in trace.read_text().splitlines()]
                 if trace.exists() else [])
        result = None
        validation_error = ""
        try:
            parsed = (
                parse_native_result(
                    process.stdout, "consumer", purpose=purpose,
                    parser=lambda raw: parse_consumer_agent_wire_result(
                        raw, purpose=purpose,
                    ),
                ) if native else parse_consumer_agent_wire_result(
                    process.stdout, purpose=purpose,
                )
            )
            result = parsed.model_dump(mode="json")
        except (ValueError, TypeError) as exc:
            validation_error = str(exc)[:350]
        run = {
            "case": case_id, "native": native,
            "status": "completed" if process.returncode == 0 else "cli_failed",
            "exit_code": process.returncode,
            "session_id": next((json.loads(line).get("thread_id", "")
                                for line in process.stdout.splitlines()
                                if '"thread.started"' in line), ""),
            "tool_trace": calls, "result": result,
            "validation_error": validation_error,
            "runtime_errors": [
                event.get("message") or event.get("error")
                for line in process.stdout.splitlines()
                if line.startswith("{")
                for event in [json.loads(line)]
                if event.get("type") in {"error", "turn.failed"}
            ][:3],
        }
        run["checks"] = _checks_for_probe(run)
        if include_trajectory:
            run["native_stdout"] = process.stdout
            run["native_stderr"] = process.stderr
            run["developer"] = developer
            run["task_prompt"] = task_prompt
        return run


def main() -> None:
    parser = argparse.ArgumentParser()
    case_specs = json.loads(CASE_MANIFEST.read_text(encoding="utf-8"))["cases"]
    parser.add_argument("--case", choices=[case["id"] for case in case_specs],
                        default="source-no-match")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--include-trajectory", action="store_true")
    args = parser.parse_args()
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    case = _probe_case(manifest, args.case)
    task_prompt, prompts = _prompts(case, _old_capability_text())
    developer = prompts["candidate"]
    if case.get("kind") == "oa":
        runtime = (
            "Anonymous read-only OA fixture. Available direct MCP reads: "
            "mcp__agent_cli__read_skill and mcp__agent_cli__read_dingtalk_oa. "
            "No provider write tools are declared; propose an action for later review."
        )
        developer = render_prompt_sections(
            consumer_developer_sections(
                runtime_context=runtime, work_profile="", purpose_name="oa",
            ), placement="developer", omit_empty=True,
        )
    runs = [_run(case, native=native, manifest=manifest,
                 developer=developer, task_prompt=task_prompt,
                 include_trajectory=args.include_trajectory)
            for native in (False, True)]
    args.output.write_text(json.dumps({
        "version": 1, "case_manifest": str(CASE_MANIFEST.relative_to(ROOT)),
        "base_manifest": str(MANIFEST.relative_to(ROOT)),
        "model": MODEL, "same_developer_and_task": True,
        "scope": "anonymous read-only fake MCP; no business provider or Work Profile",
        "runs": runs,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for run in runs:
        print(args.case, "native" if run["native"] else "free-form",
              run["status"], run.get("checks", {}), flush=True)


if __name__ == "__main__":
    main()
