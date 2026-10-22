#!/usr/bin/env python3
"""Anonymous native Consumer comparison for conditional Daily Report Minutes reads."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
MODEL = "gpt-6-luna"
EFFORT = "medium"
REPORT_SKILL = ROOT / "ci/shared-skills/ceo-daily-report/SKILL.md"
MINUTES_SKILL = Path.home() / ".agents/skills/dingtalk-minutes/SKILL.md"
CHAT_SKILL = Path.home() / ".agents/skills/dingtalk-chat/SKILL.md"
WIKI_SKILL = Path.home() / ".agents/skills/dingtalk-wiki/SKILL.md"
DOC_SKILL = Path.home() / ".agents/skills/dingtalk-doc/SKILL.md"
WINDOW = {"start": "2026-10-16T07:00:00Z", "end": "2026-10-17T07:00:00Z"}
CONVERSATION_ID = "synthetic-daily-report-group"
MINUTES_ID = "synthetic-minutes-001"
SKILLS = {
    "ceo-daily-report": REPORT_SKILL,
    "dingtalk-minutes": MINUTES_SKILL,
    "dingtalk-chat": CHAT_SKILL,
    "dingtalk-wiki": WIKI_SKILL,
    "dingtalk-doc": DOC_SKILL,
}
TOOLS = [
    {"name": "read_task_skill", "description": "Read the exact selected task-frozen Skill.",
     "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}},
                     "required": ["name"], "additionalProperties": False}},
    {"name": "read_skill", "description": "Read an installed Skill by exact name.",
     "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}},
                     "required": ["name"], "additionalProperties": False}},
    {"name": "read_system_action_contract", "description": "Read a registered action contract.",
     "inputSchema": {"type": "object", "properties": {"capability": {"type": "string"},
        "operation": {"type": "string"}}, "required": ["capability", "operation"],
        "additionalProperties": False}},
    {"name": "consumer_artifact_write", "description": "Store the synthetic report draft in a task-local fixture.",
     "inputSchema": {"type": "object", "properties": {"name": {"type": "string"},
        "content": {"type": "string"}}, "required": ["name", "content"],
        "additionalProperties": False}},
    {"name": "consumer_document_write", "description": "Write the exact synthetic report artifact to a fake read-only document and return a fake readback receipt.",
     "inputSchema": {"type": "object", "properties": {"artifact_name": {"type": "string"},
        "title": {"type": "string"}, "doc_format": {"type": "string"}, "node_id": {"type": "string"},
        "parent_id": {"type": "string"}, "workspace_id": {"type": "string"},
        "expected_revision": {"type": ["integer", "null"]}}, "required": ["artifact_name", "title"],
        "additionalProperties": False}},
    {"name": "read_scheduled_context", "description": "Read fixed synthetic scheduled-run facts.",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "read_service_activity", "description": "Read fixed synthetic service facts for an explicit time window.",
     "inputSchema": {"type": "object", "properties": {"start": {"type": "string"},
        "end": {"type": "string"}, "sources": {"type": "array", "items": {"type": "string"}}},
        "required": ["start", "end"], "additionalProperties": False}},
    {"name": "consumer_run_skill_script", "description": "Run the selected Daily Report's deterministic script in the fixture workspace.",
     "inputSchema": {"type": "object", "properties": {"name": {"type": "string"},
        "script": {"type": "string"}, "arguments": {"type": "array", "items": {"type": "string"}}},
        "required": ["name", "script"], "additionalProperties": False}},
    {"name": "read_task_artifact", "description": "Read the fixture's document write receipt.",
     "inputSchema": {"type": "object", "properties": {"name": {"type": "string"},
        "offset": {"type": "integer"}, "max_chars": {"type": "integer"}},
        "required": ["name"], "additionalProperties": False}},
    {"name": "recent_dingtalk_conversations", "description": "List conversations active in the requested window.",
     "inputSchema": {"type": "object", "properties": {"start": {"type": "string"}, "end": {"type": "string"}},
        "required": ["start", "end"], "additionalProperties": False}},
    {"name": "read_dingtalk_messages_in_window", "description": "Read messages in a conversation and exact window.",
     "inputSchema": {"type": "object", "properties": {"conversation_id": {"type": "string"},
        "start": {"type": "string"}, "end": {"type": "string"}},
        "required": ["conversation_id", "start", "end"], "additionalProperties": False}},
    {"name": "list_dingtalk_minutes", "description": "Search synthetic AI Minutes by title or time window.",
     "inputSchema": {"type": "object", "properties": {"query": {"type": "string"},
        "start": {"type": "string"}, "end": {"type": "string"}, "scope": {"type": "string"}},
        "additionalProperties": False}},
    {"name": "read_dingtalk_minutes", "description": "Read one synthetic AI Minutes summary and transcript.",
     "inputSchema": {"type": "object", "properties": {"task_uuid": {"type": "string"}},
        "required": ["task_uuid"], "additionalProperties": False}},
]


def facts(case: str) -> dict:
    meetings = []
    if case == "meeting_detail_needed":
        meetings = [{
            "meeting_id": "synthetic-meeting-001", "title": "Synthetic launch review",
            "ended_at": "2026-10-16T18:00:00+08:00", "participants": ["Alex", "Mina"],
            "delivered_to": "Synthetic launch group",
            "follow_up_message": "今天讨论了发布安排。",
        }]
    return {"meetings": meetings, "tasks": [], "attention": [],
        "important_emails": [], "attempts": [], "waiting": []}


def reply(tool: str, args: dict, case: str, selected: list[str], frozen: dict[str, str],
          artifact_root: Path) -> tuple[dict, bool]:
    if tool == "read_task_skill":
        name = args.get("name", "")
        if name not in selected or name not in frozen:
            return {"error": "skill_not_selected"}, True
        return {"name": name, "content": frozen[name],
                "sha256": hashlib.sha256(frozen[name].encode()).hexdigest()}, False
    if tool == "read_skill":
        name = args.get("name", "")
        path = SKILLS.get(name)
        if path is None or not path.is_file():
            return {"error": "skill_not_found"}, True
        content = path.read_text(encoding="utf-8")
        return {"name": name, "content": content,
                "sha256": hashlib.sha256(content.encode()).hexdigest()}, False
    if tool == "read_system_action_contract":
        try:
            from app.action_contract_catalog import read_system_action_contract
            return read_system_action_contract(args["capability"], args.get("operation")), False
        except Exception as exc:
            return {"error": type(exc).__name__}, True
    if tool == "read_scheduled_context":
        return {"run_id": 1, "task_id": 1, "scheduled_for": "2026-10-17T07:00:00+00:00",
                "timezone_name": "Asia/Shanghai", "principal_user_id": "synthetic-principal",
                "history": []}, False
    if tool == "read_service_activity":
        if args.get("start") != WINDOW["start"] or args.get("end") != WINDOW["end"]:
            return {"error": "window_mismatch"}, True
        return facts(case), False
    if tool == "consumer_artifact_write":
        content = str(args.get("content", ""))
        path = artifact_root / args.get("name", "artifact.txt")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return {"name": args.get("name"), "sha256": hashlib.sha256(content.encode()).hexdigest(),
                "stored": True}, False
    if tool == "consumer_run_skill_script":
        name, script = args.get("name", ""), args.get("script", "")
        if name != "ceo-daily-report" or not str(script).endswith(("window.py", "destination.py")):
            return {"error": "script_not_allowed"}, True
        if str(script).endswith("window.py"):
            path = SKILLS["ceo-daily-report"].parent / script
            completed = subprocess.run(
                [sys.executable, str(path), *(args.get("arguments") or [])],
                cwd=artifact_root, capture_output=True, text=True, check=False, timeout=20,
            )
            return {"exit_code": completed.returncode, "stdout": completed.stdout,
                    "stderr": completed.stderr}, completed.returncode != 0
        else:
            result = {"workspace_id": "synthetic-workspace", "parent_id": "synthetic-folder",
                      "title": "CEO 每日总结 2026-10-17", "node_id": "", "folder_url": "https://synthetic.invalid/folder"}
        return {"exit_code": 0, "stdout": json.dumps(result, ensure_ascii=False), "stderr": ""}, False
    if tool == "read_task_artifact":
        path = artifact_root / args.get("name", "")
        if not path.is_file():
            return {"error": "artifact_not_found"}, True
        content = path.read_text(encoding="utf-8")
        offset = int(args.get("offset", 0))
        limit = int(args.get("max_chars", 20000))
        return {"name": path.name, "content": content[offset:offset + limit],
                "offset": offset, "total_chars": len(content),
                "next_offset": None if offset + limit >= len(content) else offset + limit}, False
    if tool == "consumer_document_write":
        name = "synthetic-readback.json"
        material = artifact_root / args.get("artifact_name", "")
        if not material.is_file() or not material.read_text(encoding="utf-8").strip():
            return {"error": "document_artifact_missing"}, True
        readback = artifact_root / name
        readback.write_text(json.dumps({"title": args.get("title"), "readback_matches": True}), encoding="utf-8")
        raw = readback.read_bytes()
        return {"operation": "created", "node_id": "synthetic-document", "title": args.get("title"),
                "readback": {"artifact_name": name, "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}}, False
    if tool == "recent_dingtalk_conversations":
        if args.get("start") != WINDOW["start"] or args.get("end") != WINDOW["end"]:
            return {"error": "window_mismatch"}, True
        return {"conversations": [{"conversation_id": CONVERSATION_ID,
                "title": "Synthetic launch group", "kind": "group"}], "complete": True}, False
    if tool == "read_dingtalk_messages_in_window":
        if args.get("start") != WINDOW["start"] or args.get("end") != WINDOW["end"]:
            return {"error": "conversation_or_window_mismatch"}, True
        if args.get("conversation_id") != CONVERSATION_ID:
            return {"error": "conversation_or_window_mismatch"}, True
        messages = [{"message_id": "synthetic-message-001", "sender": "Mina",
                     "content": "客户验收材料已准备好，Alex 今天会核对最后两项并在明早回报。"}]
        return {"messages": messages, "complete": True}, False
    if tool == "list_dingtalk_minutes":
        if case != "meeting_detail_needed":
            return {"minutes": [], "complete": True}, False
        return {"minutes": [{"taskUuid": MINUTES_ID, "title": "Synthetic launch review"}],
                "complete": True}, False
    if tool == "read_dingtalk_minutes":
        if case != "meeting_detail_needed" or args.get("task_uuid") != MINUTES_ID:
            return {"error": "minutes_target_mismatch"}, True
        return {"info": {"taskUuid": MINUTES_ID, "title": "Synthetic launch review"},
                "summary": {"content": "团队决定先做小范围灰度；Mina 负责客户验收，Alex 于明早核对最后两项。"},
                "transcript": {"data": [], "complete": True}}, False
    return {"error": "unknown_fixture_tool"}, True


def serve(args):
    selected = json.loads(args.selected)
    frozen = {name: Path(path).read_text(encoding="utf-8") for name, path in json.loads(args.frozen).items()}
    for line in sys.stdin:
        req = json.loads(line)
        method, request_id = req.get("method"), req.get("id")
        trace_entry = None
        if method == "notifications/initialized":
            continue
        if method == "initialize":
            result = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                      "serverInfo": {"name": "daily-report-synthetic-readonly", "version": "1"}}
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            params = req.get("params", {})
            payload, failed = reply(params.get("name", ""), params.get("arguments") or {},
                                    args.case, selected, frozen, Path(args.trace).parent)
            trace_entry = {"tool": params.get("name"),
                "arguments": params.get("arguments") or {}, "result": payload, "isError": failed}
            result = {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
                      "structuredContent": payload, "isError": failed}
        else:
            continue
        response = {"jsonrpc": "2.0", "id": request_id, "result": result}
        if trace_entry is not None:
            trace_entry["mcp_response_json_bytes"] = len(
                json.dumps(response, ensure_ascii=False).encode("utf-8")
            )
            with Path(args.trace).open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(trace_entry, ensure_ascii=False) + "\n")
        if request_id is not None:
            print(json.dumps(response, ensure_ascii=False), flush=True)


def selected_and_prompt(arm: str, case: str) -> tuple[list[str], dict[str, str], str]:
    from app.agent_cron.seeds import (
        _daily_report_prompt_before_conditional_minutes,
        daily_report_prompt,
    )
    from app.business_skills import installed_business_skill_catalog
    from app.prompt_composition import PromptConfiguration, compose_consumer_task_assembly
    from app.developer_prompt import render_developer_prompt_template

    selected = ["ceo-daily-report", "dingtalk-chat", "dingtalk-minutes", "dingtalk-wiki", "dingtalk-doc"]
    frozen = {name: SKILLS[name].read_text(encoding="utf-8") for name in selected}
    task_prompt = (
        daily_report_prompt()
        if arm == "candidate"
        else _daily_report_prompt_before_conditional_minutes()
    )
    catalog = installed_business_skill_catalog()
    config = PromptConfiguration(
        (ROOT / "app/defaults/developer_prompt.md").read_text(encoding="utf-8"),
        render_developer_prompt_template((ROOT / "app/defaults/developer_prompt.md").read_text(encoding="utf-8")),
        (ROOT / "app/defaults/user_prompt.md").read_text(encoding="utf-8"), "", "",
    )
    materials = tuple(SimpleNamespace(name=name, content=content) for name, content in frozen.items())
    task_context = (
        "Synthetic read-only scheduled report run. No production data or Work Profile. "
        f"Window UTC {WINDOW['start']} through {WINDOW['end']}."
    )
    task_input = compose_consumer_task_assembly(
        config,
        task_context=task_context,
        scheduled_prompt=task_prompt,
        skill_names=tuple(selected), frozen_skill_names=tuple(selected),
        frozen_skill_materials=materials, skill_catalog=catalog,
    ).text
    return selected, frozen, task_input


def _session_input_token_total(session_id: str | None) -> tuple[int | None, int | None, int]:
    if not session_id:
        return None, None, 0
    from app.native_trajectory import find_codex_session_path

    path = find_codex_session_path(session_id)
    if path is None:
        return None, None, 0
    total = cached_total = records = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") != "token_usage_record":
            continue
        usage = event.get("payload", {}).get("turn_token_usage")
        if isinstance(usage, dict):
            total += int(usage.get("input_tokens", 0))
            cached_total += int(usage.get("cached_input_tokens", 0))
            records += 1
    return total, cached_total, records


def run_one(arm: str, case: str) -> dict:
    from app.consumer_agent import consumer_developer_instructions
    from app.codex_runner import CodexRunner
    from app.prompt_composition import PromptConfiguration
    from app.developer_prompt import render_developer_prompt_template
    from app.wechat.codex_safety import ControlledCliConfig, make_role_agent_command
    from app.agent_wire_contracts import parse_consumer_agent_wire_result
    from app import wechat

    selected, frozen, prompt = selected_and_prompt(arm, case)
    dev_template = (ROOT / "app/defaults/developer_prompt.md").read_text(encoding="utf-8")
    user_template = (ROOT / "app/defaults/user_prompt.md").read_text(encoding="utf-8")
    config = PromptConfiguration(dev_template, render_developer_prompt_template(dev_template), user_template, "", "")
    developer = consumer_developer_instructions(
        runtime_context=("Only synthetic read-only fixture tools are available. No real business MCP, "
            "production data, Work Profile, shell or external action."),
        work_profile="", prompt_configuration=config,
    )
    frozen_paths = {name: str(SKILLS[name]) for name in selected}
    wechat.codex_safety.inherited_native_server_names = lambda: ()
    with tempfile.TemporaryDirectory(prefix="ceo-daily-minutes-eval-") as tmp:
        tmp_path = Path(tmp)
        trace = tmp_path / "calls.jsonl"
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        command = CodexRunner(workspace=workspace).build_command(
            prompt="", session_id=None,
            output_schema_path=ROOT / "app/schemas/consumer_agent_result.schema.json",
            use_output_schema=False, approval_policy="never", developer_instructions=developer,
            use_approval_bypass=False, ignore_user_config=True, model=MODEL,
            reasoning_effort=EFFORT, sandbox_mode="read-only",
        )
        controlled = ControlledCliConfig(
            sys.executable,
            (str(Path(__file__).resolve()), "--server", "--case", case, "--trace", str(trace),
             "--selected", json.dumps(selected), "--frozen", json.dumps(frozen_paths)),
            str(ROOT),
        )
        make_role_agent_command(command, role="consumer", task_workspace=str(workspace), controlled_cli=controlled)
        for feature in ("shell_tool", "unified_exec"):
            command[-1:-1] = ["-c", f"features.{feature}=false"]
        completed = subprocess.run(command, input=prompt, capture_output=True, text=True,
            timeout=240, check=False, cwd=ROOT)
        messages, errors, session_id = [], [], None
        for line in completed.stdout.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "thread.started":
                session_id = event.get("thread_id")
            if event.get("type") == "item.completed" and (event.get("item") or {}).get("type") == "agent_message":
                messages.append(event["item"].get("text", ""))
            if event.get("type") in ("error", "turn.failed"):
                errors.append(str(event.get("message") or event.get("error") or "")[:250])
        final = messages[-1] if messages else ""
        valid = False
        try:
            normalized = parse_consumer_agent_wire_result(json.dumps({"type": "item.completed", "item":
                {"type": "agent_message", "text": final}}, ensure_ascii=False)).model_dump(mode="json")
            valid = True
        except Exception as exc:
            normalized = None
            errors.append(type(exc).__name__ + ":" + str(exc)[:250])
        calls = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()] if trace.exists() else []
        for item in calls:
            if item["tool"] in {"read_task_skill", "read_skill"} and isinstance(item.get("result"), dict):
                body = item["result"].pop("content", "")
                item["result"]["content_chars"] = len(body)
        input_tokens, cached_input_tokens, input_token_records = _session_input_token_total(session_id)
        return {"arm": arm, "case": case, "exit_code": completed.returncode,
            "input_tokens_total": input_tokens, "cached_input_tokens_total": cached_input_tokens,
            "input_token_records": input_token_records,
            "session_id": session_id, "valid_wire": valid, "result": normalized,
            "tool_calls": calls, "errors": errors, "prompt_chars": len(prompt),
            "stderr_excerpt": completed.stderr[:1000].replace("\n", " ")}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", action="store_true")
    parser.add_argument("--case", choices=("no_meeting_group_update", "meeting_detail_needed"))
    parser.add_argument("--arm", choices=("baseline", "candidate"))
    parser.add_argument("--trace")
    parser.add_argument("--selected", default="[]")
    parser.add_argument("--frozen", default="{}")
    args = parser.parse_args()
    if args.server:
        serve(args)
        return
    if args.case or args.arm:
        if not args.case or not args.arm:
            parser.error("--case and --arm must be supplied together")
        results = [run_one(args.arm, args.case)]
    else:
        results = [run_one(arm, case)
            for case in ("no_meeting_group_update", "meeting_detail_needed")
            for arm in ("baseline", "candidate")]
    print(json.dumps({"model": MODEL, "reasoning_effort": EFFORT,
        "mode": "anonymous_native_codex_oauth_fake_readonly_mcp", "production_data": False,
        "work_profile": "omitted", "business_mcp": False, "external_actions": False,
        "results": results}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
