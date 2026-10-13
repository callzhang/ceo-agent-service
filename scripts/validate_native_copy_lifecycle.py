#!/usr/bin/env python3
"""Real Codex Consumer/Audit on an owned SQLite copy, with in-memory delivery.

The caller creates source.sqlite3 using SQLite backup and supplies source-meta.json.
No fake model output, synthetic native transcript, credential copy or production
constructor is used. The invocation exposes only bound artifact/contract reads.
"""
from __future__ import annotations

from dataclasses import replace

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.action_storage import (
    CANDIDATE_REVIEW_SQL,
    EXTERNAL_ACTION_RESULT_SQL,
    REVIEW_CANDIDATE_SQL,
)


def restricted_command(command, *, workspace, db_path):
    """Narrow this test invocation's native tool exposure, without changing roles."""
    from app.wechat.codex_safety import inherited_native_server_names
    from app.codex_runner import _config_string

    args = list(command)
    assert str(db_path) in " ".join(args)
    assert not any("dangerously-bypass" in arg for arg in args)
    servers = set(inherited_native_server_names())
    for arg in args:
        if arg.startswith("mcp_servers."):
            servers.add(arg.split(".", 2)[1])
    additions = []
    for server in sorted(servers - {"agent_cli"}):
        additions.extend(["-c", f"mcp_servers.{server}.enabled=false"])
    additions.extend([
        "-c", 'mcp_servers.agent_cli.enabled_tools=["read_task_artifact","read_system_action_contract"]',
        "-c", _config_string("mcp_servers.agent_cli.env", {
            "CEO_ENV_FILE": str(workspace / "no-service.env"),
            "CEO_WORKSPACE": str(workspace),
            "CEO_WORKER_DB": str(db_path),
            "PYTHONDONTWRITEBYTECODE": "1",
        }),
    ])
    index = len(args) - 2 if "resume" in args else len(args) - 1
    args[index:index] = additions
    if "--skip-git-repo-check" not in args:
        args[index:index] = ["--skip-git-repo-check"]
    return args


class NativeExecutor:
    def __init__(self, workspace, db_path):
        self.workspace, self.db_path = workspace, db_path
        self.calls = []
        self.after_consumer = None
        self.fail_first_start = False

    def __call__(self, command, **kwargs):
        from app.process_runner import run_process_with_idle_timeout
        from app.leak_check import redact_credentials

        scoped = restricted_command(command, workspace=self.workspace, db_path=self.db_path)
        if self.fail_first_start and not self.calls:
            scoped.insert(scoped.index("exec") + 1, "--copy-acceptance-invalid-start")
        role = "audit" if 'sandbox_mode="read-only"' in scoped else "consumer"
        print(json.dumps({"event": "native_start", "role": role}), flush=True)
        # Keep native authentication/home; no credential files or transcript copies.
        original_progress = kwargs.get("on_stdout_line")
        def progress(line):
            if original_progress is not None:
                original_progress(line)
        kwargs["on_stdout_line"] = progress
        kwargs["total_timeout_seconds"] = min(kwargs["total_timeout_seconds"], 300)
        kwargs["idle_timeout_seconds"] = min(kwargs["idle_timeout_seconds"], 120)
        result = run_process_with_idle_timeout(scoped, **kwargs)
        record = {"role": role, "returncode": result.returncode, "timed_out": result.timed_out,
                  "prompt_sha256": hashlib.sha256(kwargs["prompt"].encode()).hexdigest()}
        if result.returncode:
            record["stderr"] = redact_credentials(result.stderr)[-1200:]
        self.calls.append(record)
        print(json.dumps({"event": "native_end", **record}), flush=True)
        if role == "consumer" and result.returncode == 0 and self.after_consumer:
            callback, self.after_consumer = self.after_consumer, None
            callback()
        return result


class InMemoryProvider:
    def __init__(self, store, conversation):
        self.store, self.conversation, self.calls = store, conversation, []
        self.verifications = []

    def send_message(self, conversation_id, text, **target):
        assert conversation_id == self.conversation
        with self.store._connect() as db:
            decision = db.execute(f"select decision from ({CANDIDATE_REVIEW_SQL}) order by id desc limit 1").fetchone()[0]
        assert decision == "approve"
        self.calls.append({"conversation_id": conversation_id, "text": text, "target": target})
        return {"success": True, "result": {"openMessageId": "native-copy-provider-receipt", "openConversationId": conversation_id}}

    def verify_message_send_result(self, result):
        self.verifications.append(result)
        assert result["result"]["openMessageId"] == "native-copy-provider-receipt"
        return {"state": "sent", "message_id": "native-copy-provider-receipt"}

    def reconcile_message_send(self, *args, **kwargs):
        return None


def original_rows_unchanged(source, candidate):
    with sqlite3.connect(candidate) as db:
        db.execute("attach database ? as baseline", (str(source),))
        virtual = [r[0] for r in db.execute("select name from baseline.sqlite_master where type='table' and sql like 'CREATE VIRTUAL TABLE%'")]
        tables = [r[0] for r in db.execute("select name from baseline.sqlite_master where type='table' and name not like 'sqlite_%'")
                  if not any(r[0] == v or r[0].startswith(v + "_") for v in virtual)]
        changed = {}
        for table in tables:
            quoted = '"' + table.replace('"', '""') + '"'
            count = db.execute(f"select count(*) from (select * from baseline.{quoted} except select * from main.{quoted})").fetchone()[0]
            if count:
                changed[table] = count
        fks = {tuple(r) for r in db.execute("pragma foreign_key_check")}
    with sqlite3.connect(source) as db:
        original_fks = {tuple(r) for r in db.execute("pragma foreign_key_check")}
    assert not changed, changed
    assert fks == original_fks
    return len(tables)


def run(root, case="approve"):
    root = root.resolve()
    assert root.parent == Path("/private/tmp") and root.name.startswith("ceo-native-copy.")
    source = root / "source.sqlite3"
    metadata = json.loads((root / "source-meta.json").read_text())
    assert hashlib.sha256(source.read_bytes()).hexdigest() == metadata["sha256"]
    candidate_path = root / "candidate.sqlite3"
    assert not candidate_path.exists()
    shutil.copyfile(source, candidate_path)
    workspace = root / "workspace"
    workspace.mkdir()
    # This test needs only the real task-bound local MCP, injected by role builders.
    manifest = root / "service-mcp.json"
    manifest.write_text('{"servers":{}}')
    os.environ.update({"CEO_ENV_FILE": str(root / "missing.env"), "CEO_WORKSPACE": str(workspace),
        "CEO_WORKER_DB": str(candidate_path), "CEO_FEEDBACK_SPIKE_VERCEL_BASE_URL": "",
        "CEO_SERVICE_MCP_CONFIG_PATH": str(manifest),
        "CEO_CORPUS_DIR": str(root / "no-corpus"), "PYTHONDONTWRITEBYTECODE": "1"})
    from app.agent_cli import _task_file_root
    from app.agent_context import AgentTaskContext, MaterialReference
    from app.agent_orchestrator import AgentOrchestrator
    from app.agent_runtime_config import load_runtime_config
    from app.audit_agent import AuditAgentRunner
    from app.consumer_agent import ConsumerAgentRunner
    from app.dingtalk_models import DingTalkMessage
    from app.store import AutoReplyStore
    from app.system_action_handlers import native_dingtalk_message_handlers
    from app.system_executor import SystemExecutor
    from app.worker import DingTalkAutoReplyWorker
    from app.native_trajectory import find_codex_session_path

    store = AutoReplyStore(candidate_path)
    key = "native-copy-acceptance:" + str(uuid4())
    instruction = (
        "这是数据库副本中的真实 Codex CLI 验收任务，数据均为测试数据。请使用 "
        "read_task_artifact(name=\"case-input.json\") 读取本任务材料，核对条目数量并计算 units 总和。"
        "请通过 read_system_action_contract 读取 dingtalk-chat 的 send_group_message 契约。"
        "形成一项 send_group_message 候选，在当前来源群回复验收汇总，正文包含材料中的 verification_tag、"
        "条目数和 units 总和，并准确说明这是测试结果。实际发送仅由系统的内存替身执行。"
        "不要自己执行发送，不读真实消息、不调用网络或其他系统，不需要读取 Skill 或 Memory。"
        "先读取和计算，再输出完整的 Consumer 合同 JSON；不要伪造读取结果。"
    )
    trigger = DingTalkMessage(open_conversation_id=key, open_message_id=key + ":trigger",
        conversation_title="Native copy acceptance", single_chat=False, sender_name="Derek",
        create_time="2026-10-12T00:00:00Z", content=instruction)
    task = store.ensure_reply_task(conversation_id=key, conversation_title=trigger.conversation_title,
        single_chat=False, trigger_message_id=trigger.open_message_id, trigger_sender=trigger.sender_name,
        trigger_create_time=trigger.create_time, trigger_text=instruction, trigger_message_json=trigger.model_dump_json())
    assert task.status == "pending"
    artifacts = _task_file_root(store.path, task.id, task.execution_generation, create=True)
    source_file = artifacts / "case-input.json"
    fixture = {"verification_tag": "NATIVE_COPY_" + uuid4().hex[:12], "records": [{"units": 23}, {"units": 27}, {"units": 50}]}
    source_file.write_text(json.dumps(fixture, ensure_ascii=False))
    task = store.claim_reply_task(task.id)
    assert task is not None and task.status == "processing"
    context = AgentTaskContext(task_id=task.id, channel=task.channel, conversation_id=key,
        conversation_title=task.conversation_title, single_chat=False,
        trigger_message_id=task.trigger_message_id, trigger_sender=task.trigger_sender,
        trigger_text=instruction, trigger_create_time=task.trigger_create_time,
        messages=(), materials=(MaterialReference("local_file", str(source_file), task.trigger_message_id,
            ('read_task_artifact(name="case-input.json")',)),), prior_receipts=(),
        consumer_prompt=instruction, skill_protocol_override="Use only this task's local artifact and system action contract; no external source reads.",
        skill_protocol_source="explicit_custom")
    if case == "revision":
        # The current source changes after R0, not the returned model JSON.
        source_file.write_text(json.dumps({**fixture, "records": fixture["records"][:2]}))
    if case == "no_action":
        instruction = (
            '这是副本验收任务。请用 read_task_artifact(name="case-input.json") 读取材料，'
            '计算条目数及 units 总和，将 verification_tag 和结果写入 summary。'
            '此任务只需要核对数据，无需通知、发送或任何外部动作；请输出 no_action 合同。'
        )
        context = replace(context, trigger_text=instruction, consumer_prompt=instruction)
    config = load_runtime_config({"CEO_AGENT_RUNTIME_ROUTES": "codex_oauth", "CEO_CODEX_MODEL": "gpt-5.6-luna"})
    native = NativeExecutor(workspace, store.path)
    if case == "revision":
        native.after_consumer = lambda: source_file.write_text(json.dumps(fixture))
    if case == "technical_retry":
        native.fail_first_start = True
    provider = InMemoryProvider(store, key)
    driver = AgentOrchestrator(store=store,
        consumer=ConsumerAgentRunner(store=store, workspace=workspace, runtime_config=config, executor=native,
            skill_protocol_override=context.skill_protocol_override, owner="native-copy-consumer"),
        audit=AuditAgentRunner(store=store, workspace=workspace, runtime_config=config, executor=native,
            skill_protocol_override=context.skill_protocol_override, owner="native-copy-audit"),
        system_executor=SystemExecutor(store, native_dingtalk_message_handlers(store, provider), owner="native-copy-system"))
    worker = DingTalkAutoReplyWorker(store, object(), object(), agent_orchestrator=driver)
    worker._queued_task_prompt_context_messages = lambda *a, **kw: ([], [])
    worker._build_agent_task_context = lambda **kw: context
    worker._sync_processing_reactions = lambda: None
    worker._dismiss_problem_notification = lambda task: None
    worker._notify_problem_attempt = lambda *a, **kw: None
    interrupted = False
    if case == "settlement_recovery":
        with store._connect() as db:
            db.execute(f"create trigger native_copy_interrupt before update of status on tasks when old.id={task.id} and new.status='done' begin select raise(abort,'native copy settlement interrupted'); end")
        try:
            worker.process_claimed_reply_task(task)
        except sqlite3.IntegrityError as exc:
            assert "native copy settlement interrupted" in str(exc)
            interrupted = True
        assert interrupted and store.get_reply_task(task.id).status == "processing"
        assert len(provider.calls) == 1
        with store._connect() as db:
            assert db.execute(f"select count(*) from ({EXTERNAL_ACTION_RESULT_SQL}) where business_object_key=?", (task.business_object_key,)).fetchone()[0] == 1
            assert db.execute("select count(*) from reply_attempts where conversation_id=?", (key,)).fetchone()[0] == 0
            db.execute("drop trigger native_copy_interrupt")
        store = AutoReplyStore(candidate_path)
        driver = AgentOrchestrator(store=store, consumer=object(), audit=object(), system_executor=None)
        worker = DingTalkAutoReplyWorker(store, object(), object(), agent_orchestrator=driver)
        worker._queued_task_prompt_context_messages = lambda *a, **kw: ([], [])
        worker._build_agent_task_context = lambda **kw: context
        worker._sync_processing_reactions = lambda: None
        worker._dismiss_problem_notification = lambda task: None
        worker._notify_problem_attempt = lambda *a, **kw: None
        task = store.get_reply_task(task.id)
    completed = worker.process_claimed_reply_task(task)
    runs = store.list_agent_runs_for_task_generation(task.id, task.execution_generation)
    current = store.get_reply_task(task.id)
    report = {"case": case, "interrupted": interrupted, "source": metadata, "task_id": task.id, "conversation_id": key, "status": current.status,
        "completed": completed, "native_invocations": native.calls,
        "provider_calls": len(provider.calls), "verification_calls": len(provider.verifications), "runs": []}
    for run in runs:
        attempts = store.list_agent_runtime_attempts(run.id)
        path = find_codex_session_path(run.codex_session_id) if run.codex_session_id else None
        report["runs"].append({"id": run.id, "role": run.role.value, "status": run.status,
            "revision": run.proposal_revision, "parent_id": run.parent_agent_run_id,
            "session_id": run.codex_session_id, "start": run.transcript_start_line, "end": run.transcript_end_line,
            "native_exists": bool(path and path.is_file()), "tool_events": len(run.tool_events),
            "error": run.structured_error_json,
            "attempts": [{"id": a.id, "status": a.status, "session_id": a.session_id,
                          "start": a.transcript_start, "end": a.transcript_end} for a in attempts]})
    (root / "native-result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    assert completed and current.status == "done", report
    expected_dispatch = 0 if case == "no_action" else 1
    assert len(provider.calls) == expected_dispatch
    if expected_dispatch:
        assert provider.verifications
        body = provider.calls[0]["text"]
        assert fixture["verification_tag"] in body and "100" in body and "3" in body, body
    if case == "revision":
        assert max(x["revision"] for x in report["runs"]) >= 1, report
        with store._connect() as db:
            report["reviews"] = [dict(x) for x in db.execute(f"select r.id,r.decision,c.proposal_revision,r.candidate_digest from ({CANDIDATE_REVIEW_SQL}) r join ({REVIEW_CANDIDATE_SQL}) c on c.id=r.candidate_id where c.task_id=? and c.execution_generation=? order by r.id", (task.id, task.execution_generation))]
        assert report["reviews"][0]["decision"] in {"return", "reject"}, report
        assert report["reviews"][-1]["decision"] == "approve", report
    if case == "technical_retry":
        assert native.calls[0]["returncode"] != 0
        assert any(x["status"] == "failed" for x in report["runs"]), report
    assert current.input_compacted and current.input_provenance_json
    assert {r["role"] for r in report["runs"]} == {"consumer", "audit"}
    assert all(r["native_exists"] and r["end"] > r["start"] for r in report["runs"] if r["status"] == "completed")
    with store._connect() as db:
        report["receipts"] = db.execute(f"select count(*) from ({EXTERNAL_ACTION_RESULT_SQL}) where business_object_key=?", (task.business_object_key,)).fetchone()[0]
        report["sent_replies"] = db.execute("select count(*) from sent_replies where conversation_id=?", (key,)).fetchone()[0]
        report["business_attempts"] = db.execute("select count(*) from reply_attempts where conversation_id=?", (key,)).fetchone()[0]
        assert "final_result_json" not in {r[1] for r in db.execute("pragma table_info(runs)")}
        assert db.execute("pragma quick_check").fetchall()[0][0] == "ok"
    assert report["receipts"] == report["sent_replies"] == expected_dispatch
    assert report["business_attempts"] == 1
    reopened = AutoReplyStore(store.path)
    recovered = AgentOrchestrator(store=reopened, consumer=object(), audit=object(), system_executor=None)
    assert recovered.persisted_terminal_result(reopened.get_reply_task(task.id)).status == ("no_action" if case == "no_action" else "executed")
    report["reopen_no_new_invocation"] = len(native.calls)
    report["original_tables_unchanged"] = original_rows_unchanged(source, candidate_path)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == metadata["sha256"]
    report["passed"] = True
    (root / "native-result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({"event": "acceptance_pass", "task_id": task.id, "native_invocations": len(native.calls),
                      "provider_calls": len(provider.calls), "original_tables_unchanged": report["original_tables_unchanged"]}), flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owned-temp-root", type=Path, required=True)
    parser.add_argument("--case", choices=("approve", "revision", "no_action", "technical_retry", "settlement_recovery"), default="approve")
    args = parser.parse_args()
    run(args.owned_temp_root, args.case)


if __name__ == "__main__":
    main()
