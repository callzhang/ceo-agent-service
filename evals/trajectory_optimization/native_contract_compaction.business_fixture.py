"""Readonly native role fixture using actual frozen Skill and contract readers."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.agent_cli import build_role_server


def build_fixture(
    root: Path, case: dict, phase: str, *, task_id: int = 1, log_label: str | None = None
):
    server = build_role_server(
        case["role"],
        task_id=task_id,
        db_path=root / "fixture.sqlite3",
        execution_generation="probe",
    )
    facts = case["phases"][phase]
    originals = {t.name: t.fn for t in server._tool_manager.list_tools()}

    def replace(name):
        def callback(**kwargs):
            if name in ("read_task_skill", "read_skill", "read_system_action_contract"):
                result = originals[name](**kwargs)
            elif name == "read_dingtalk_calendar_event":
                matches = [
                    e
                    for e in facts.get("calendar_events", [])
                    if e["event_id"] == kwargs["event_id"]
                ]
                if len(matches) != 1:
                    raise ValueError("fixture calendar identity unavailable")
                result = matches[0]
            elif name == "list_dingtalk_calendar_events":
                # Match provider calendar date queries to its explicit configured zone.
                start = datetime.fromisoformat(kwargs["start"])
                end = datetime.fromisoformat(kwargs["end"])
                zone = ZoneInfo(case["trigger"]["raw_payload"]["principal_timezone"])
                if start.tzinfo is None:
                    start = start.replace(tzinfo=zone)
                if end.tzinfo is None:
                    end = end.replace(tzinfo=zone)
                if end <= start:
                    raise ValueError("fixture calendar interval invalid")
                result = {
                    "events": [
                        e
                        for e in facts["calendar_events"]
                        if datetime.fromisoformat(e["start_time"]) < end
                        and datetime.fromisoformat(e["end_time"]) > start
                    ]
                }
            elif name == "read_dingtalk_messages":
                if kwargs["conversation_id"] != case["trigger"]["conversation_id"]:
                    raise ValueError("fixture conversation identity unavailable")
                result = {
                    "conversation_id": kwargs["conversation_id"],
                    "messages": facts["messages"],
                    "complete": True,
                }
            elif name in ("read_dingtalk_document", "read_dingtalk_document_full"):
                document = facts["document"]
                if kwargs["node_id"] != document["document_id"]:
                    raise ValueError("fixture document identity unavailable")
                result = document
            else:
                raise ValueError(
                    "operation not provided by this readonly fixture: " + name
                )
            with (root / ((log_label or phase) + ".calls.jsonl")).open("a") as f:
                f.write(
                    json.dumps(
                        {"name": name, "arguments": kwargs, "result": result},
                        ensure_ascii=False,
                    )
                    + "\n"
                )
            return result

        return callback

    for tool in server._tool_manager.list_tools():
        server._tool_manager._tools[tool.name] = tool.model_copy(
            update={"fn": replace(tool.name), "is_async": False}
        )
    return server
