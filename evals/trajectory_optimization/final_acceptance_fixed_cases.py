"""Current-input revalidation of the original six anonymous acceptance cases.

This is not a reconstruction of the lost original Profile or an old/new A/B.
Only a local, read-only fake provider is connected. Never execute candidates.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime
from functools import partial
from hashlib import sha256
import json
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
from unittest.mock import patch
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MANIFEST = ROOT / "evals/task_scoped_prompts/v1.json"


def serve(snapshot: Path, trace: Path) -> None:
    from mcp.server.fastmcp import FastMCP
    from mcp.types import ToolAnnotations
    from app.action_contract_catalog import read_system_action_contract
    from app.calendar_timezone import convert_calendar_timezone

    server = FastMCP("agent_cli")
    read = ToolAnnotations(readOnlyHint=True, destructiveHint=False)

    def record(name, arguments, result):
        with trace.open("a") as stream:
            stream.write(json.dumps({"tool": name, "arguments": arguments,
                                     "result": result}, ensure_ascii=False) + "\n")
        return result

    def state():
        return json.loads(snapshot.read_text())

    @server.tool(annotations=read)
    def read_task_skill(name: str) -> dict:
        current = state()
        if name != current["case"]["skill_name"]:
            raise ValueError("Skill is not selected in this task; use installed read_skill for dependencies")
        item = current["skills"][name]
        return record("read_task_skill", {"name": name}, {"name": name, **item})

    @server.tool(annotations=read)
    def read_skill(name: str = "", path: str = "") -> dict:
        selected = name or Path(path).parent.name
        item = state()["skills"][selected]
        return record("read_skill", {"name": name, "path": path},
                      {"name": selected, **item})

    @server.tool(annotations=read)
    def read_dingtalk_messages(conversation_id: str, title: str = "",
                               single_chat: bool = False, limit: int = 50) -> dict:
        case = state()["case"]
        assert conversation_id == case["trigger"]["conversation_id"]
        return record("read_dingtalk_messages", {"conversation_id": conversation_id},
                      {"messages": case["phases"][state()["phase"]]["messages"]})

    @server.tool(annotations=read)
    def read_dingtalk_document(node_id: str) -> dict:
        doc = state()["case"]["phases"][state()["phase"]]["document"]
        assert node_id == doc["document_id"]
        return record("read_dingtalk_document", {"node_id": node_id},
                      {"success": True, "node_id": node_id,
                       "markdown": doc["content"], **doc})

    @server.tool(annotations=read)
    def read_dingtalk_calendar_event(event_id: str) -> dict:
        events = state()["case"]["phases"][state()["phase"]]["calendar_events"]
        event = next(e for e in events if e["event_id"] == event_id)
        return record("read_dingtalk_calendar_event", {"event_id": event_id}, {"event": event})

    @server.tool(annotations=read)
    def list_dingtalk_calendar_events(start: str, end: str) -> dict:
        lo, hi = datetime.fromisoformat(start), datetime.fromisoformat(end)
        assert lo.tzinfo and hi.tzinfo and lo < hi
        events = state()["case"]["phases"][state()["phase"]]["calendar_events"]
        selected = [e for e in events if datetime.fromisoformat(e["start_time"]) < hi
                    and datetime.fromisoformat(e["end_time"]) > lo]
        return record("list_dingtalk_calendar_events", {"start": start, "end": end},
                      {"events": selected})

    @server.tool(annotations=read)
    def convert_dingtalk_calendar_time(local_time: str, source_timezone: str,
                                      target_timezone: str) -> dict:
        result = convert_calendar_timezone(local_time, source_timezone, target_timezone)
        return record("convert_dingtalk_calendar_time", {
            "local_time": local_time, "source_timezone": source_timezone,
            "target_timezone": target_timezone}, result)

    @server.tool(annotations=read, name="read_system_action_contract")
    def contract(capability: str, operation: str | None = None) -> dict:
        return record("read_system_action_contract", {"capability": capability,
                      "operation": operation}, read_system_action_contract(capability, operation))

    server.run(transport="stdio")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", type=Path)
    parser.add_argument("--trace", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--case", action="append")
    parser.add_argument("--route", default="codex_oauth")
    parser.add_argument("--repeat-current-state", action="store_true",
                        help="Add one same-session reread of unchanged fake facts when case has no resume phase")
    parser.add_argument("--check-fixture", action="store_true")
    parser.add_argument("--continue-session", help="One explicit continue after a failed diagnostic; not a cold-start pass")
    parser.add_argument("--resume-correction", help="Resume an existing cross-midnight case; do not rerun cold")
    args = parser.parse_args()
    if args.serve:
        serve(args.serve, args.trace)
        return
    manifest = json.loads(MANIFEST.read_text())
    assert len(manifest["cases"]) == 6
    if args.check_fixture:
        assert all(c["criteria"]["required_tool_calls"] for c in manifest["cases"])
        assert len({c["id"] for c in manifest["cases"]}) == 6
        print("six original case facts and criteria available; sha256="
              + sha256(MANIFEST.read_bytes()).hexdigest())
        return
    if args.output is None:
        parser.error("--output is required for live anonymous checks")

    from app.agent_runtime_config import load_runtime_config
    from app.agent_wire_contracts import parse_consumer_agent_wire_result, parse_audit_agent_wire_result
    from app.audit_rules import render_audit_rules
    from app.config import read_env_file
    from app.consumer_agent import consumer_developer_sections_by_runtime, audit_developer_sections
    from app.codex_runtime_adapter import CodexRuntimeAdapter
    from app.leak_check import redact_credentials
    from app.native_agent_output import native_output_schema_path, parse_native_result
    from app.native_trajectory import find_codex_session_path
    from app.prompt_composition import render_prompt_sections
    from app.store import AgentRole
    from app.wechat.codex_safety import make_role_agent_command

    config = load_runtime_config(read_env_file(Path("/Users/derek/Services/ceo-agent-service/.env")))
    route = next(r for r in config.routes if r.name == args.route)
    report = {"scope": "Current six-case acceptance, not original A/B; fake reads only",
              "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT,
                                                       text=True).strip(),
              "case_manifest_sha256": sha256(MANIFEST.read_bytes()).hexdigest(),
              "harness_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
              "route": route.name, "model": route.model, "fixed_clock": manifest["fixed_runtime_time"],
              "results": []}
    names = {c["skill_name"] for c in manifest["cases"]} | {
        "dingtalk-calendar", "dingtalk-chat", "dingtalk-doc", "dingtalk-mail", "dingtalk-shared"}
    skills = {}
    for name in names:
        path = ROOT / "ci/shared-skills" / name / "SKILL.md"
        if not path.exists():
            path = Path.home() / ".agents/skills" / name / "SKILL.md"
        content = path.read_text()
        skills[name] = {"path": f"/synthetic/{name}/SKILL.md", "content": content,
                        "sha256": sha256(content.encode()).hexdigest()}
    report["skill_sha256"] = {k: v["sha256"] for k, v in skills.items()}
    with tempfile.TemporaryDirectory(prefix="ceo-g3-fixed-", dir="/tmp") as directory:
        root = Path(directory)
        for case in manifest["cases"]:
            if args.resume_correction and case["id"] != "calendar-cross-midnight":
                continue
            if args.case and case["id"] not in args.case:
                continue
            case = deepcopy(case)
            if args.repeat_current_state and "resume" not in case["phases"]:
                case["phases"]["resume"] = deepcopy(case["phases"]["cold"])
                case["criteria"]["resume"] = case["criteria"]["cold"]
            work = root / case["id"]
            work.mkdir()
            subprocess.run(["git", "init", "-q", str(work)], check=True)
            state, trace = work / "state.json", work / "trace.jsonl"
            adapter, sid = CodexRuntimeAdapter(work, config), args.resume_correction or args.continue_session
            for phase in (["correction"] if args.resume_correction else
                          ["continue"] if args.continue_session else case["phases"]):
                if phase != "cold" and not sid:
                    break
                source_phase = "cold" if phase in {"correction", "continue"} else phase
                state.write_text(json.dumps({"case": case, "phase": source_phase, "skills": skills}))
                role = case["role"]
                context = ("Anonymous fixed-case acceptance; only local fake read MCP tools. "
                           "No production facts, Work Profile, provider writes or external actions. "
                           "Current time: " + manifest["fixed_runtime_time"])
                if role == "consumer":
                    sections = consumer_developer_sections_by_runtime(
                        runtime_context=context, work_profile="")["codex_cli"]
                else:
                    sections = audit_developer_sections(
                        render_audit_rules(AgentRole.AUDIT, create_missing=False),
                        runtime_context=context, work_profile="")
                developer = render_prompt_sections(sections, placement="developer")
                task = (case["task"] + "\nCurrent time: " + manifest["fixed_runtime_time"]
                        + "\nSelected task Skill (read with read_task_skill): " + case["skill_name"]
                        + "\nOriginal trigger:\n" + json.dumps(case["trigger"], ensure_ascii=False))
                if phase == "resume":
                    task += "\nNew turn: reread current state and handle only remaining work."
                if phase == "correction":
                    task += ("\nReview feedback on the previous candidate: your initial calendar query "
                             "ended exactly at the next local midnight. It did not inspect the "
                             "adjacent day-boundary event required by this review. Verify that "
                             "boundary against current calendar source and return a replacement "
                             "candidate. No previous candidate was executed.")
                if phase == "continue":
                    task = "continue"
                digest = sha256(json.dumps(case["phases"][source_phase].get("candidate", {}),
                                          sort_keys=True).encode()).hexdigest()
                if role == "audit":
                    task += ("\nPrepared candidate:\n" + json.dumps(case["phases"][source_phase]["candidate"])
                             + "\nproposal_revision=0; candidate_digest=" + digest)
                with patch("app.codex_runtime_adapter.service_mcp_config_options", return_value=[]):
                    command = adapter.build_command(route, task, sid, None,
                        native_output_schema_path(role), True, "never", developer, False,
                        sandbox_mode="read-only", skip_git_repo_check=False,
                        project_doc_max_bytes=0, skill_names=())
                options = ["--ignore-user-config", "--ignore-rules", "-c",
                    "mcp_servers.agent_cli.command=" + json.dumps(sys.executable), "-c",
                    "mcp_servers.agent_cli.args=" + json.dumps([
                        str(Path(__file__).resolve()), "--serve", str(state), "--trace", str(trace)])]
                index = len(command) - (2 if sid else 1)
                command[index:index] = options
                with patch("app.wechat.codex_safety.inherited_native_server_names", return_value=()):
                    make_role_agent_command(command, role=role,
                                            task_workspace=str(work) if role == "consumer" else None)
                options = ["-c", "features.shell_tool=false", "-c", "features.unified_exec=false",
                           "-c", "sandbox_workspace_write.network_access=false"]
                index = len(command) - (2 if sid else 1)
                command[index:index] = options
                before = len(trace.read_text().splitlines()) if trace.exists() else 0
                row = {"case": case["id"], "phase": phase, "role": role,
                       "developer_sha256": sha256(developer.encode()).hexdigest(),
                       "task_sha256": sha256(task.encode()).hexdigest(),
                       "schema_sha256": sha256(native_output_schema_path(role).read_bytes()).hexdigest(),
                       "criteria": case["criteria"].get(source_phase, [])}
                print("START", case["id"], phase, flush=True)
                process = subprocess.Popen(command, cwd=work, env=adapter.build_env(route),
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, start_new_session=True)
                try:
                    output, stderr = process.communicate(task, timeout=180)
                except subprocess.TimeoutExpired:
                    import os
                    os.killpg(process.pid, signal.SIGTERM)
                    output, stderr = process.communicate(timeout=15)
                    row["timeout"] = True
                events = []
                for line in output.splitlines():
                    try:
                        events.append(json.loads(line))
                    except ValueError:
                        pass
                new_sid = next((e.get("thread_id") for e in events
                                if e.get("type") == "thread.started"), sid)
                row.update(exit_code=process.returncode, session_id=new_sid,
                           reused_session=phase in {"resume", "correction", "continue"} and new_sid == sid)
                row["tool_trace"] = [json.loads(line) for line in trace.read_text().splitlines()[before:]] if trace.exists() else []
                for call in row["tool_trace"]:
                    payload = call["result"]
                    if call["tool"] in {"read_task_skill", "read_skill"}:
                        call["result"] = {"name": payload["name"], "path": payload["path"],
                                          "sha256": payload["sha256"],
                                          "characters": len(payload["content"])}
                parser = parse_consumer_agent_wire_result if role == "consumer" else partial(
                    parse_audit_agent_wire_result, proposal_revision=0, candidate_digest=digest)
                try:
                    result = parse_native_result(output, role, parser=parser)
                    row["result"] = result.model_dump(mode="json")
                    row["canonical_valid"] = True
                except (ValueError, TypeError) as exc:
                    row.update(canonical_valid=False, error=redact_credentials(str(exc))[:500])
                if new_sid:
                    path = find_codex_session_path(new_sid)
                    if path:
                        lines = path.read_text().splitlines()
                        row["native_lines"] = len(lines)
                        row["native_sha256"] = sha256("\n".join(lines).encode()).hexdigest()
                        terminal = next((json.loads(line).get("payload", {}) for line in reversed(lines)
                                         if json.loads(line).get("payload", {}).get("type") == "task_complete"), {})
                        if terminal.get("error"):
                            row["native_terminal_error"] = redact_credentials(json.dumps(terminal["error"]))
                row["stderr"] = redact_credentials(stderr)[-500:]
                report["results"].append(row)
                args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
                print(case["id"], phase, "valid", row["canonical_valid"],
                      "calls", len(row["tool_trace"]), "sid", new_sid, flush=True)
                sid = new_sid if row["canonical_valid"] else None
    report["completed_at"] = datetime.now(ZoneInfo("America/Los_Angeles")).isoformat()
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
