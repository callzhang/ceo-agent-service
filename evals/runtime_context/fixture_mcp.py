"""Read-only synthetic role MCP for the runtime-context native evaluation.

This process has no provider imports or write tools. Its only file write is the
per-run trace inside the evaluation's temporary directory.
"""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import sys
from datetime import datetime


TOOLS = (
    ("read_skill", "Read an installed Agent skill or its referenced Markdown safely.",
     {"path": {"type": "string"}}, ["path"]),
    ("list_dingtalk_calendar_events", "Read the principal's events over an explicit time window.",
     {"start": {"type": "string"}, "end": {"type": "string"}}, ["start", "end"]),
    ("read_dingtalk_messages", "Read recent messages of an identified DingTalk conversation.",
     {"conversation_id": {"type": "string"}, "title": {"type": "string", "default": ""},
      "single_chat": {"type": "boolean", "default": False},
      "limit": {"type": "integer", "default": 50}}, ["conversation_id"]),
)


def tool_catalog(case: dict) -> list[dict]:
    return [
        {"name": name, "description": description,
         "annotations": {"readOnlyHint": True, "destructiveHint": False,
                         "idempotentHint": True, "openWorldHint": False},
         "inputSchema": {"type": "object", "properties": properties,
                         "required": required, "additionalProperties": False}}
        for name, description, properties, required in TOOLS
        if name not in case.get("absent_tools", [])
    ]


def response(case: dict, name: str, arguments: dict) -> tuple[dict, bool]:
    if name in case.get("absent_tools", []) or name not in {item[0] for item in TOOLS}:
        return {"code": "tool_not_declared", "tool": name}, True
    if name == "read_skill":
        path = str(arguments.get("path") or "")
        parts = Path(path).parts
        skill_name = parts[-2] if len(parts) >= 2 and parts[-1] == "SKILL.md" else ""
        if skill_name not in case["skill_content"]:
            return {"code": "skill_path_unavailable", "path": path}, True
        content = case["skill_content"][skill_name]
        return {"name": skill_name, "path": path,
                "sha256": sha256(content.encode()).hexdigest(), "content": content}, False
    if name == "read_dingtalk_messages":
        if arguments.get("conversation_id") != case["conversation_id"]:
            return {"code": "conversation_not_found"}, True
        return {"messages": case["messages"]}, False
    if not arguments.get("start") or not arguments.get("end"):
        return {"code": "explicit_time_window_required"}, True
    if case.get("calendar_error"):
        return case["calendar_error"], True
    try:
        start = datetime.fromisoformat(arguments["start"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(arguments["end"].replace("Z", "+00:00"))
        if start.tzinfo is None or end.tzinfo is None or start >= end:
            raise ValueError
    except (ValueError, TypeError):
        return {"code": "calendar_window_invalid"}, True
    events = [event for event in case["events"]
              if datetime.fromisoformat(event["start_time"]) < end
              and datetime.fromisoformat(event["end_time"]) > start]
    return {"events": events}, False


def serve(case: dict, trace_path: Path) -> None:
    for line in sys.stdin:
        request = None
        try:
            request = json.loads(line)
            request_id = request.get("id")
            method = request.get("method")
            if method == "notifications/initialized":
                continue
            if method == "initialize":
                result = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                          "serverInfo": {"name": "runtime-context-synthetic-read", "version": "1"}}
            elif method == "tools/list":
                result = {"tools": tool_catalog(case)}
            elif method == "tools/call":
                params = request.get("params") or {}
                name = params.get("name")
                arguments = params.get("arguments") or {}
                payload, is_error = response(case, name, arguments)
                trace = {"tool": name, "arguments": arguments, "result": payload,
                         "isError": is_error}
                with trace_path.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(trace, ensure_ascii=False, sort_keys=True) + "\n")
                result = {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
                          "structuredContent": payload, "isError": is_error}
            else:
                raise ValueError("unsupported_method")
            if request_id is not None:
                print(json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result},
                                 ensure_ascii=False), flush=True)
        except Exception as exc:
            if request is not None and request.get("id") is not None:
                print(json.dumps({"jsonrpc": "2.0", "id": request["id"],
                                  "error": {"code": -32602, "message": str(exc)}}), flush=True)


if __name__ == "__main__":
    manifest = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    cases = {case["id"]: case for case in manifest["cases"]}
    selected = dict(cases[sys.argv[2]])
    if "inherits" in selected:
        selected = {**cases[selected.pop("inherits")], **selected}
    if selected["messages"][0]["content"] != selected["trigger"]:
        selected["messages"] = [
            {**selected["messages"][0], "content": selected["trigger"]},
            *selected["messages"][1:],
        ]
    selected["skill_content"] = manifest["skill_content"]
    serve(selected, Path(sys.argv[3]))
