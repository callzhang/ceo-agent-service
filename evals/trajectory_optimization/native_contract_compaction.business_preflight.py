"""Create disposable bound tasks and verify fixture readers before inference."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.config import load_env_file
from app.agent_runtime_config import load_runtime_config
from app.agent_cron.context import ScheduledAgentContext
from app.agent_cron.commands import ServiceCommandSkillMaterial
from app.store import AutoReplyStore

base = Path(__file__).resolve().parent
manifest = json.loads(
    Path(sys.argv[2]).read_text()
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
input_spec = importlib.util.spec_from_file_location(
    "business_inputs", base / "native_contract_compaction.business_inputs.py"
)
inputs = importlib.util.module_from_spec(input_spec)
input_spec.loader.exec_module(inputs)
records = []
for case in manifest["cases"]:
    work = root / case["id"]
    work.mkdir()
    skill = manifest["skills"][case["skill_name"]]
    body = Path(skill["path"]).read_text()
    assert hashlib.sha256(body.encode()).hexdigest() == skill["sha256"]
    trigger = case["trigger"]
    context = inputs.task_context(case)
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
        mail = case["phases"]["cold"]["mail"]
        (work / "mail.json").write_text(json.dumps(mail))
        native_cli = shutil.which("dws")
        assert native_cli is not None
        (work / "native-dws-path.txt").write_text(native_cli)
        fixture_path = base / "native_contract_compaction.business_mail_fixture.py"

        def mail_read(*argv):
            completed = subprocess.run(
                [sys.executable, str(fixture_path), str(work), *argv],
                capture_output=True,
                text=True,
                check=True,
            )
            return json.loads(completed.stdout)

        assert mail_read("mail", "mailbox", "list", "--format", "json")["result"][
            "emailAccounts"
        ] == mail["emailAccounts"]
        email = mail["emailAccounts"][0]["email"]
        assert mail_read(
            "mail", "+thread", "--email", email,
            "--id", mail["conversation"]["id"], "--format", "json",
        )["data"]["value"] == mail["conversation"]
        for message in mail["messages"]:
            assert mail_read(
                "mail", "+message", "--email", email,
                "--id", message["id"], "--format", "json",
            )["data"]["value"] == message
        assert mail_read(
            "mail", "message", "verify", "--email", email,
            "--internet-message-id", mail["sent_receipt"]["message"]["internetMessageId"],
            "--format", "json",
        )["result"] == mail["sent_receipt"]
        for argv in (
            ["mail", "+thread", "--email", "wrong@example.test", "--id", "mail-thread-1", "--format", "json"],
            ["mail", "+message", "--email", email, "--id", "wrong-message", "--format", "json"],
            ["mail", "message", "send"],
        ):
            failed = subprocess.run(
                [sys.executable, str(fixture_path), str(work), *argv],
                capture_output=True,
            )
            assert failed.returncode != 0
        checks += [
            "chat is locator only; exact original mailbox/thread/messages and sent receipt read",
            "wrong mailbox, wrong message identity and write leaf rejected by eval fixture",
        ]
    else:
        document = case["phases"]["cold"]["document"]
        assert (
            read("read_dingtalk_document", node_id=document["node_id"]) == document
        )
        checks += ["exact document identity and revision"]
    candidate_receipt = None
    if case["role"] == "audit":
        from app.audit_rules import render_audit_rules
        from app.store import AgentRole
        from app.reviewed_candidates import candidate_digest

        audit = inputs.audit_context(
            case, audit_rules=render_audit_rules(AgentRole.AUDIT, create_missing=False)
        )
        assert audit.candidate_digest == candidate_digest(audit.candidate)
        rendered = audit.render(current_time=manifest["fixed_runtime_time"])
        assert audit.candidate_digest in rendered
        action = audit.candidate.proposal.actions[0]
        assert action.target["message_id"] == trigger["message_id"]
        assert action.payload["content"] == case["phases"]["cold"]["candidate"]["reply_body"]
        candidate_receipt = {
            "candidate_digest": audit.candidate_digest,
            "proposal_revision": audit.proposal_revision,
            "canonical_valid": True,
            "context_sha256": hashlib.sha256(rendered.encode()).hexdigest(),
            "source_bound_reply": True,
        }
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
            "audit_candidate": candidate_receipt,
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
            "mail_original_fixture_complete": True,
            "model_calls": 0,
        }
    )
)
