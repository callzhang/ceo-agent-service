"""Paired read-only native comparison for the Consumer capability paragraph."""

from __future__ import annotations

import argparse
import ast
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
import tempfile


from app.agent_context import AgentContextMessage, AgentTaskContext
from app.agent_wire_contracts import parse_consumer_agent_wire_result
from app.consumer_agent import (
    TASK_STORAGE_INSTRUCTIONS, consumer_developer_sections,
)
from app.prompt_composition import render_prompt_sections


ROOT = Path(__file__).resolve().parents[2]
BASE = "465533b572a7396deabe33bb6894a87a9908eab8"
MODEL = "gpt-5.6-luna"
CASES = (
    "candidate-windows-unknown-zone",
    "calendar-auth-failure",
    "prior-risk-refusal",
    "draft-only",
)
MANIFEST = ROOT / "evals/runtime_context/cases.v1.json"
SERVER_MODULE = "evals.trajectory_optimization.consumer_capability_fixture_mcp"
FIXED_TIME = "2026-10-22T15:00:00+00:00"


def _old_capability_text() -> str:
    source = subprocess.check_output(
        ["git", "show", f"{BASE}:app/consumer_agent.py"],
        cwd=ROOT, text=True,
    )
    tree = ast.parse(source)
    assignment = next(
        node for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "AGENT_CAPABILITY_INSTRUCTIONS"
                for target in node.targets)
    )
    literals = [
        node.value for node in ast.walk(assignment.value)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]
    body = max(literals, key=len)
    if len(body) < 3_000 or "dependency_read_unavailable" not in body:
        raise RuntimeError("baseline capability text could not be recovered")
    return body.strip() + "\n\n" + TASK_STORAGE_INSTRUCTIONS


def _case(manifest: dict, case_id: str) -> dict:
    cases = {item["id"]: item for item in manifest["cases"]}
    selected = dict(cases[case_id])
    if "inherits" in selected:
        selected = {**cases[selected.pop("inherits")], **selected, "id": case_id}
    return selected


def _prompts(case: dict, old_text: str) -> tuple[str, dict[str, str]]:
    names = ("ceo-message-triage", "dingtalk-calendar", "dingtalk-chat")
    skill_entries = "\n".join(
        f"- {name}: use read_skill(path='/synthetic/{name}/SKILL.md') when applicable."
        for name in names
    )
    runtime = (
        "Synthetic read-only Agent turn. Current time: " + FIXED_TIME + ". "
        "Available direct MCP reads: mcp__agent_cli__read_skill, "
        "mcp__agent_cli__read_dingtalk_messages, "
        "mcp__agent_cli__list_dingtalk_calendar_events, "
        "mcp__agent_cli__read_system_action_contract. "
        "No provider write tools are declared; propose controlled messages for later review."
    )
    sections = consumer_developer_sections(runtime_context=runtime, work_profile="")
    old_sections = tuple(
        replace(section, text="## Capability Instructions\n" + old_text)
        if section.name == "能力边界" else section
        for section in sections
    )
    assert len(sections) == len(old_sections)
    assert [s.text for s in sections if s.name != "能力边界"] == [
        s.text for s in old_sections if s.name != "能力边界"
    ]
    task = AgentTaskContext(
        task_id=42, channel="dingtalk",
        conversation_id=case["conversation_id"],
        conversation_title="模拟项目群", single_chat=False,
        trigger_message_id=case["trigger_message_id"],
        trigger_sender="协调者", trigger_text=case["trigger"],
        trigger_create_time="2026-10-22T14:30:00+00:00",
        messages=tuple(
            AgentContextMessage(
                message_id=item["open_message_id"], sender=item["sender_name"],
                text=item["content"], create_time=item["create_time"],
            ) for item in case["messages"][1:]
        ),
        materials=(), prior_receipts=(),
    )
    additional = ""
    if case.get("prior_error"):
        additional = "\n\nPrior provider result for this exact intended action:\n" + json.dumps(
            case["prior_error"], ensure_ascii=False
        )
    task_prompt = (
        "## Selected Task Skills\n" + skill_entries + "\n\n" +
        task.render(current_time=FIXED_TIME) + additional
    )
    return task_prompt, {
        "baseline": render_prompt_sections(old_sections, placement="developer", omit_empty=True),
        "candidate": render_prompt_sections(sections, placement="developer", omit_empty=True),
    }


def _run(case_id: str, arm: str, developer: str, task: str, work: Path) -> dict:
    trace = work / "tool-trace.jsonl"
    server_args = ["-m", SERVER_MODULE, str(MANIFEST), case_id, str(trace)]
    command = [
        "codex", "exec", "--ignore-user-config", "--ignore-rules", "--ephemeral",
        "--skip-git-repo-check", "-C", str(work), "-s", "read-only",
        "-m", MODEL, "--json",
        "--disable", "shell_tool", "--disable", "apps", "--disable", "plugins",
        "--disable", "hooks", "--disable", "multi_agent",
        "-c", "model_reasoning_effort=\"medium\"",
        "-c", "project_doc_max_bytes=0",
        "-c", "web_search=\"disabled\"",
        "-c", "developer_instructions=" + json.dumps(developer, ensure_ascii=False),
        "-c", "mcp_servers.agent_cli.command=" + json.dumps(sys.executable),
        "-c", "mcp_servers.agent_cli.args=" + json.dumps(server_args),
        "-c", "mcp_servers.agent_cli.cwd=" + json.dumps(str(ROOT)),
        "-c", 'mcp_servers.agent_cli.default_tools_approval_mode="approve"',
        "-",
    ]
    try:
        process = subprocess.run(
            command, input=task, capture_output=True, text=True, timeout=240,
        )
    except subprocess.TimeoutExpired:
        return {"case": case_id, "arm": arm, "status": "timeout"}
    events = []
    for line in process.stdout.splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    calls = [json.loads(line) for line in trace.read_text().splitlines()] if trace.exists() else []
    result = None
    validation_error = ""
    try:
        parsed = parse_consumer_agent_wire_result(process.stdout)
        result = parsed.model_dump(mode="json")
    except (ValueError, TypeError) as exc:
        validation_error = str(exc)[:350]
    return {
        "case": case_id, "arm": arm, "status": "completed" if process.returncode == 0 else "cli_failed",
        "exit_code": process.returncode,
        "developer_sha256": sha256(developer.encode()).hexdigest(),
        "developer_chars": len(developer), "task_sha256": sha256(task.encode()).hexdigest(),
        "task_chars": len(task),
        "tool_calls": [{"tool": call["tool"], "isError": call["isError"]} for call in calls],
        "result": result, "validation_error": validation_error,
        "runtime_errors": [
            event.get("message") or event.get("error")
            for event in events if event.get("type") in {"error", "turn.failed"}
        ][:3],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case", choices=CASES, action="append")
    args = parser.parse_args()
    manifest = json.loads(MANIFEST.read_text())
    old_text = _old_capability_text()
    results = []
    for case_id in args.case or CASES:
        case = _case(manifest, case_id)
        task, prompts = _prompts(case, old_text)
        assert len(prompts["baseline"]) - len(prompts["candidate"]) >= 1_000
        for arm in ("baseline", "candidate"):
            with tempfile.TemporaryDirectory(prefix="consumer-capability-native-", dir="/tmp") as directory:
                result = _run(case_id, arm, prompts[arm], task, Path(directory))
            results.append(result)
            print(case_id, arm, result["status"],
                  "valid=" + str(result.get("result") is not None), flush=True)
    args.output.write_text(json.dumps({
        "baseline_ref": BASE,
        "model": MODEL,
        "manifest_sha256": sha256(MANIFEST.read_bytes()).hexdigest(),
        "scope": "Synthetic read-only direct MCP; no production facts or writes",
        "results": results,
    }, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
