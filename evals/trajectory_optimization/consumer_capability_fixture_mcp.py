"""Synthetic read-only source and action-contract MCP for paired Consumer evals."""

from __future__ import annotations

import json
from pathlib import Path
import sys

from evals.runtime_context.fixture_mcp import response as source_response
from evals.runtime_context.fixture_mcp import tool_catalog as source_catalog


CONTRACT_TOOL = {
    "name": "read_system_action_contract",
    "description": "Read the declared system-action contract for an exact capability and operation.",
    "annotations": {
        "readOnlyHint": True, "destructiveHint": False,
        "idempotentHint": True, "openWorldHint": False,
    },
    "inputSchema": {
        "type": "object",
        "properties": {
            "capability": {"type": "string"},
            "operation": {"type": "string"},
        },
        "required": ["capability", "operation"],
        "additionalProperties": False,
    },
}


def serve(case: dict, trace: Path) -> None:
    from app.action_contract_catalog import read_system_action_contract

    for line in sys.stdin:
        request = None
        try:
            request = json.loads(line)
            request_id = request.get("id")
            method = request.get("method")
            if method == "notifications/initialized":
                continue
            if method == "initialize":
                result = {
                    "protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                    "serverInfo": {"name": "consumer-capability-synthetic-read", "version": "1"},
                }
            elif method == "tools/list":
                result = {"tools": [*source_catalog(case), CONTRACT_TOOL]}
            elif method == "tools/call":
                params = request.get("params") or {}
                name = params.get("name")
                arguments = params.get("arguments") or {}
                if name == "read_system_action_contract":
                    try:
                        payload = read_system_action_contract(
                            arguments.get("capability", ""),
                            arguments.get("operation", ""),
                        )
                        is_error = False
                    except ValueError:
                        payload = {"code": "contract_not_found"}
                        is_error = True
                else:
                    payload, is_error = source_response(case, name, arguments)
                with trace.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps({
                        "tool": name, "arguments": arguments, "result": payload,
                        "isError": is_error,
                    }, ensure_ascii=False, sort_keys=True) + "\n")
                result = {
                    "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
                    "structuredContent": payload, "isError": is_error,
                }
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
    selected["skill_content"] = {
        **manifest["skill_content"],
        "ceo-calendar-invite": "# Calendar invitation\nUse read-only calendar and message facts. Propose a message only after an exact action contract read.",
    }
    if selected["messages"][0]["content"] != selected["trigger"]:
        selected["messages"] = [
            {**selected["messages"][0], "content": selected["trigger"]},
            *selected["messages"][1:],
        ]
    serve(selected, Path(sys.argv[3]))
