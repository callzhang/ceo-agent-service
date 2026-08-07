"""A test-only, idempotent destination for Audit Agent integration tests."""

from __future__ import annotations

import json
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SinkRecord:
    operation_id: str
    payload: dict[str, object]


class AuditSink:
    def __init__(self, path: Path) -> None:
        self.path = path
        with self._connect() as db:
            db.execute(
                """
                create table if not exists audit_sink (
                    operation_id text primary key,
                    payload_json text not null
                )
                """
            )

    def read_state(self, operation_id: str) -> SinkRecord | None:
        with self._connect() as db:
            row = db.execute(
                "select operation_id, payload_json from audit_sink where operation_id=?",
                (operation_id,),
            ).fetchone()
        if row is None:
            return None
        return SinkRecord(row[0], json.loads(row[1]))

    def write_state(self, operation_id: str, payload: dict[str, object]) -> SinkRecord:
        canonical_payload = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        with self._connect() as db:
            db.execute("begin immediate")
            row = db.execute(
                "select operation_id, payload_json from audit_sink where operation_id=?",
                (operation_id,),
            ).fetchone()
            if row is None:
                db.execute(
                    "insert into audit_sink(operation_id, payload_json) values (?, ?)",
                    (operation_id, canonical_payload),
                )
                db.commit()
                return SinkRecord(operation_id, dict(payload))
            db.commit()
        return SinkRecord(row[0], json.loads(row[1]))

    def row_count(self, operation_id: str) -> int:
        with self._connect() as db:
            return int(
                db.execute(
                    "select count(*) from audit_sink where operation_id=?",
                    (operation_id,),
                ).fetchone()[0]
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)


def _tool_result(record: SinkRecord | None) -> dict[str, object]:
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(
                    None if record is None else {"operation_id": record.operation_id, "payload": record.payload},
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            }
        ]
    }


def serve(path: Path) -> None:
    """Minimal stdio MCP server used only by the opt-in native Codex test."""
    sink = AuditSink(path)
    for raw in sys.stdin:
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
                    "serverInfo": {"name": "audit-sink", "version": "1"},
                }
            elif method == "tools/list":
                result = {
                    "tools": [
                        {
                            "name": "read_state",
                            "description": "Read one controlled sink row by operation ID.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {"operation_id": {"type": "string"}},
                                "required": ["operation_id"],
                            },
                        },
                        {
                            "name": "write_state",
                            "description": "Write one idempotent controlled sink row.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "operation_id": {"type": "string"},
                                    "payload": {"type": "object"},
                                },
                                "required": ["operation_id", "payload"],
                            },
                        },
                    ]
                }
            elif method == "tools/call":
                name = params.get("name")
                arguments = params.get("arguments") or {}
                operation_id = str(arguments.get("operation_id") or "")
                if not operation_id:
                    raise ValueError("operation_id is required")
                if name == "read_state":
                    result = _tool_result(sink.read_state(operation_id))
                elif name == "write_state":
                    payload = arguments.get("payload")
                    if not isinstance(payload, dict):
                        raise ValueError("payload must be an object")
                    result = _tool_result(sink.write_state(operation_id, payload))
                else:
                    raise ValueError("unknown tool")
            else:
                raise ValueError("unknown method")
            if request_id is not None:
                print(json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}), flush=True)
        except Exception as exc:
            if isinstance(locals().get("request"), dict) and request.get("id") is not None:
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
    serve(Path(sys.argv[1]))
