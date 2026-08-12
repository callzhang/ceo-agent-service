"""Read-only MCP fixture for opt-in Task 5 native Codex tests."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


def _result(payload: dict[str, object]) -> dict[str, object]:
    return {
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
        "structuredContent": payload,
        "isError": False,
    }


class ReadFixture:
    def __init__(self, config_path: Path, log_path: Path) -> None:
        config = json.loads(config_path.read_text(encoding="utf-8"))
        self.skill_paths = {str(Path(path).resolve()) for path in config["skill_paths"]}
        self.responses = {
            tuple(item["argv"]): str(item["stdout"])
            for item in config["operation_responses"]
        }
        self.log_path = log_path

    def read_skill(self, path_value: object) -> dict[str, object]:
        path = Path(str(path_value)).resolve()
        if str(path) not in self.skill_paths:
            raise ValueError("Skill path is not available in this fixture")
        content = path.read_text(encoding="utf-8")
        payload: dict[str, object] = {
            "name": path.parent.name,
            "path": str(path),
            "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "content": content,
        }
        self._log("read_skill", {"path": str(path)}, payload)
        return _result(payload)

    def execute_reviewed_read(self, argv_value: object) -> dict[str, object]:
        if not isinstance(argv_value, list) or not all(
            isinstance(item, str) for item in argv_value
        ):
            raise ValueError("argv must be an array of strings")
        argv = tuple(argv_value)
        if argv not in self.responses:
            raise ValueError("Exact reviewed read is not available in this fixture")
        stdout = self.responses[argv]
        payload = {
            "argv": list(argv),
            "stdout": stdout,
            "result_digest": hashlib.sha256(stdout.encode("utf-8")).hexdigest(),
        }
        self._log("execute_reviewed_read", {"argv": list(argv)}, payload)
        return _result(payload)

    def _log(
        self,
        tool: str,
        arguments: dict[str, object],
        result: dict[str, object],
    ) -> None:
        with self.log_path.open("a", encoding="utf-8") as stream:
            stream.write(
                json.dumps(
                    {"tool": tool, "arguments": arguments, "result": result},
                    ensure_ascii=False,
                    sort_keys=True,
                )
                + "\n"
            )


def serve(config_path: Path, log_path: Path) -> None:
    fixture = ReadFixture(config_path, log_path)
    for raw in sys.stdin:
        request: dict[str, object] | None = None
        try:
            request = json.loads(raw)
            request_id = request.get("id")
            method = request.get("method")
            params = request.get("params") or {}
            if method == "notifications/initialized":
                continue
            if method == "initialize":
                result: dict[str, object] = {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "task5-read-fixture", "version": "1"},
                }
            elif method == "tools/list":
                result = {
                    "tools": [
                        {
                            "name": "read_skill",
                            "description": "Read one exact available Skill with a receipt.",
                            "annotations": {
                                "readOnlyHint": True,
                                "destructiveHint": False,
                            },
                            "inputSchema": {
                                "type": "object",
                                "properties": {"path": {"type": "string"}},
                                "required": ["path"],
                            },
                        },
                        {
                            "name": "execute_reviewed_read",
                            "description": "Run one exact available read operation.",
                            "annotations": {
                                "readOnlyHint": True,
                                "destructiveHint": False,
                            },
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "argv": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                    }
                                },
                                "required": ["argv"],
                            },
                        },
                    ]
                }
            elif method == "tools/call":
                name = params.get("name")
                arguments = params.get("arguments") or {}
                if name == "read_skill":
                    result = fixture.read_skill(arguments.get("path"))
                elif name == "execute_reviewed_read":
                    result = fixture.execute_reviewed_read(arguments.get("argv"))
                else:
                    raise ValueError("unknown tool")
            else:
                raise ValueError("unknown method")
            if request_id is not None:
                print(
                    json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}),
                    flush=True,
                )
        except Exception as exc:
            if request is not None and request.get("id") is not None:
                print(
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": request["id"],
                            "error": {"code": -32602, "message": str(exc)},
                        }
                    ),
                    flush=True,
                )


if __name__ == "__main__":
    serve(Path(sys.argv[1]), Path(sys.argv[2]))
