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


def _tool(name: str, properties: dict, required: list[str] | None = None) -> dict:
    return {
        "name": name, "description": "Synthetic task-bound provider operation.",
        "annotations": {"readOnlyHint": name not in {
            "consumer_artifact_write", "consumer_document_write",
        }, "destructiveHint": False, "idempotentHint": True,
            "openWorldHint": False},
        "inputSchema": {"type": "object", "properties": properties,
                        "required": required or [], "additionalProperties": False},
    }


OA_TOOL = _tool("read_dingtalk_oa", {"process_instance_id": {"type": "string"}},
                ["process_instance_id"])
SCHEDULED_TOOLS = (
    _tool("read_scheduled_context", {}),
    _tool("consumer_artifact_write", {
        "name": {"type": "string"}, "content": {"type": "string"},
    }, ["name", "content"]),
    _tool("read_task_artifact", {"name": {"type": "string"}}, ["name"]),
    _tool("consumer_document_write", {
        "artifact_name": {"type": "string"}, "title": {"type": "string"},
        "doc_format": {"type": "string"}, "node_id": {"type": "string"},
        "expected_revision": {"type": "integer"},
    }, ["artifact_name", "title", "node_id"]),
)


def serve(case: dict, trace: Path) -> None:
    from app.action_contract_catalog import read_system_action_contract

    artifacts: dict[str, str] = {}
    document_receipt: dict | None = None

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
                special = (
                    [OA_TOOL] if case.get("kind") == "oa" else
                    list(SCHEDULED_TOOLS) if case.get("kind") == "scheduled" else []
                )
                source_tools = source_catalog(case)
                if case.get("skill_lookup_by_name"):
                    for tool in source_tools:
                        if tool["name"] == "read_skill":
                            tool["inputSchema"]["properties"].update({
                                "name": {"type": "string"},
                                "query": {"type": "string"},
                            })
                            tool["inputSchema"]["required"] = []
                result = {"tools": [*source_tools, CONTRACT_TOOL, *special]}
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
                elif name == "read_dingtalk_oa" and case.get("kind") == "oa":
                    exact = arguments.get("process_instance_id") == case["oa_instance_id"]
                    payload = (case["oa_instance"] if exact else
                               {"code": "process_instance_not_found"})
                    is_error = not exact
                elif name == "read_scheduled_context" and case.get("kind") == "scheduled":
                    payload, is_error = case["scheduled_context"], False
                elif name == "consumer_artifact_write" and case.get("kind") == "scheduled":
                    artifact_name = str(arguments.get("name") or "")
                    content = str(arguments.get("content") or "")
                    if artifact_name and content:
                        artifacts[artifact_name] = content
                        payload, is_error = {"name": artifact_name, "bytes": len(content.encode())}, False
                    else:
                        payload, is_error = {"code": "artifact_invalid"}, True
                elif name == "consumer_document_write" and case.get("kind") == "scheduled":
                    artifact_name = str(arguments.get("artifact_name") or "")
                    exact_target = arguments.get("node_id") == case["document_node_id"]
                    if artifact_name in artifacts and exact_target:
                        document_receipt = {
                            "node_id": case["document_node_id"],
                            "revision": 8,
                            "readback_artifact_name": "document-readback.md",
                        }
                        artifacts["document-readback.md"] = artifacts[artifact_name]
                        payload, is_error = document_receipt, False
                    else:
                        payload, is_error = {"code": "document_target_or_artifact_invalid"}, True
                elif name == "read_task_artifact" and case.get("kind") == "scheduled":
                    artifact_name = str(arguments.get("name") or "")
                    if artifact_name in artifacts:
                        payload, is_error = {"name": artifact_name,
                                             "content": artifacts[artifact_name]}, False
                    else:
                        payload, is_error = {"code": "artifact_not_found"}, True
                elif name == "read_skill" and case.get("skill_lookup_by_name"):
                    skill_name = arguments.get("name")
                    query = arguments.get("query")
                    if skill_name and not arguments.get("path") and not query:
                        payload, is_error = source_response(
                            case, name, {"path": f"/synthetic/{skill_name}/SKILL.md"},
                        )
                    elif query is not None and not skill_name and not arguments.get("path"):
                        terms = str(query).casefold().split()
                        payload = {"catalog": [
                            {"name": item, "description": "Synthetic installed Skill"}
                            for item in case["skill_content"]
                            if terms and all(term in item.casefold() for term in terms)
                        ]}
                        is_error = not terms
                    elif not arguments:
                        payload = {"catalog": [
                            {"name": item, "description": "Synthetic installed Skill"}
                            for item in case["skill_content"]
                        ]}
                        is_error = False
                    else:
                        payload, is_error = source_response(case, name, arguments)
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
