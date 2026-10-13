"""Readonly native role fixture using actual frozen Skill and contract readers."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.agent_cli import build_role_server
from app.dws_client import DwsCalendarEvent


def calendar_event(fact):
    return DwsCalendarEvent(
        event_id=fact["event_id"], title=fact["title"],
        start_time=fact["start_time"], end_time=fact["end_time"],
        description=fact["description"], organizer=fact["organizer"]["user_id"],
        self_response_status=fact["principal_response_status"],
    ).model_dump(mode="json")

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
                result = {"event": calendar_event(matches[0])}
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
                        calendar_event(e)
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
                if kwargs["node_id"] != document["node_id"]:
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


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--case", required=True)
    parser.add_argument("--phase", required=True)
    parser.add_argument("--log-label")
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    case = next(case for case in manifest["cases"] if case["id"] == args.case)
    build_fixture(args.root, case, args.phase, log_label=args.log_label).run(
        transport="stdio"
    )
