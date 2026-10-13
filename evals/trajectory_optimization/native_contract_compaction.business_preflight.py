"""Create disposable bound tasks and verify fixture readers before inference."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.config import load_env_file
from app.agent_runtime_config import load_runtime_config
from app.agent_context import AgentTaskContext
from app.agent_cron.context import ScheduledAgentContext
from app.agent_cron.commands import ServiceCommandSkillMaterial
from app.store import AutoReplyStore

base = Path(__file__).resolve().parent
manifest = json.loads(
    (base / "native_contract_compaction.business_cases.v3.json").read_text()
)
root = Path(sys.argv[1]).resolve()
assert root.is_dir() and not list(root.iterdir()), (
    "Use a new empty task-owned temporary directory"
)
load_env_file(Path("/Users/derek/Services/ceo-agent-service/.env"))
route = next(
    r
    for r in load_runtime_config(os.environ).routes
    if r.name == manifest["configured_route"]
)
assert route.model == manifest["model"]
spec = importlib.util.spec_from_file_location(
    "fixture", base / "native_contract_compaction.business_fixture.py"
)
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
records = []
for case in manifest["cases"]:
    work = root / case["id"]
    work.mkdir()
    skill = manifest["skills"][case["skill_name"]]
    body = Path(skill["path"]).read_text()
    assert hashlib.sha256(body.encode()).hexdigest() == skill["sha256"]
    trigger = case["trigger"]
    context = AgentTaskContext(
        task_id=1,
        channel=trigger["channel"],
        conversation_id=trigger["conversation_id"],
        conversation_title=trigger["conversation_title"],
        single_chat=False,
        trigger_message_id=trigger["message_id"],
        trigger_sender=trigger["sender"],
        trigger_text=trigger["text"],
        trigger_create_time=trigger["create_time"],
        messages=(),
        materials=(),
        prior_receipts=(),
        trigger_raw_payload=trigger.get("raw_payload", {}),
        skill_names=(case["skill_name"],),
    )
    frozen = ScheduledAgentContext(
        context=context,
        route=route,
        workspace=work,
        reasoning_effort="medium",
        skill_protocol="",
        skill_names=(case["skill_name"],),
        skill_materials=(ServiceCommandSkillMaterial(case["skill_name"], body),),
        skill_protocol_source="generated_discovery",
    )
    store = AutoReplyStore(work / "fixture.sqlite3")
    store.enqueue_reply_task(
        conversation_id=trigger["conversation_id"],
        conversation_title=trigger["conversation_title"],
        single_chat=False,
        trigger_message_id=trigger["message_id"],
        trigger_create_time=trigger["create_time"],
        trigger_sender=trigger["sender"],
        trigger_text=trigger["text"],
        execution_generation="probe",
        trigger_message_json=frozen.to_execution_json(),
    )
    task = store.claim_reply_tasks(limit=1)[0]
    assert task.id == 1
    server = fixture.build_fixture(work, case, "cold", log_label="preflight")

    def read(tool_name, **arguments):
        return server._tool_manager.get_tool(tool_name).fn(**arguments)

    material = read("read_task_skill", name=case["skill_name"])
    assert material["content"] == body and material["sha256"] == skill["sha256"]
    checks = ["actual frozen task Skill read matches complete original bytes"]
    operation_reads = []
    for name in case["criteria"]["required_operation_skills"]:
        operation = manifest["operation_skills"][name]
        live_body = Path(operation["path"]).read_text()
        assert hashlib.sha256(live_body.encode()).hexdigest() == operation["sha256"]
        loaded = read("read_skill", name=name)
        assert loaded["content"] == live_body
        operation_reads.append({"name": name, "sha256": operation["sha256"]})
    if case["domain"] == "calendar":
        first = case["phases"]["cold"]["calendar_events"][0]
        assert read("read_dingtalk_calendar_event", event_id=first["event_id"]) == first
        window = read(
            "list_dingtalk_calendar_events",
            start="2026-10-16T00:00:00-07:00",
            end="2026-10-18T00:00:00-07:00",
        )
        assert len(window["events"]) == 2
        assert (
            read(
                "list_dingtalk_calendar_events",
                start="2026-10-15T00:00:00-07:00",
                end="2026-10-16T00:00:00-07:00",
            )["events"]
            == []
        )
        checks += [
            "exact event identity",
            "timezone-aware windows filter actual event timestamps",
        ]
    elif case["domain"] == "mail":
        assert (
            read("read_dingtalk_messages", conversation_id=trigger["conversation_id"])[
                "messages"
            ]
            == case["phases"]["cold"]["messages"]
        )
        checks += ["chat locator at exact conversation; original mail evidence incomplete"]
    else:
        document = case["phases"]["cold"]["document"]
        assert (
            read("read_dingtalk_document", node_id=document["document_id"]) == document
        )
        checks += ["exact document identity and revision"]
    records.append(
        {
            "case": case["id"],
            "role": case["role"],
            "task_id": task.id,
            "generation": task.execution_generation,
            "tool_count": len(server._tool_manager.list_tools()),
            "skill_sha256": skill["sha256"],
            "operation_skill_reads": operation_reads,
            "inference_ready": case["inference_ready"],
            "missing_evidence": case.get("missing_evidence", []),
            "checks": checks,
        }
    )
(root / "preflight.json").write_text(
    json.dumps(records, ensure_ascii=False, indent=2) + "\n"
)
print(
    json.dumps(
        {
            "cases": len(records),
            "skill_reads_exact": True,
            "source_reads_exact": True,
            "mail_business_evidence_complete": False,
            "model_calls": 0,
        }
    )
)
