#!/usr/bin/env python3
"""Replay the archived A5 selection task with real CLI and substitute delivery.

The caller supplies a closed, current production-derived copy and its baseline.
This harness never creates a provider client or changes production configuration.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.validate_native_copy_lifecycle import (  # noqa: E402
    InMemoryProvider, NativeExecutor, original_rows_unchanged, _reserve_fixture_task_ids,
)


def run(root: Path, *, database: Path, baseline: Path) -> dict:
    root, database, baseline = root.resolve(), database.resolve(), baseline.resolve()
    if database == baseline or root not in database.parents or root not in baseline.parents:
        raise ValueError("supply distinct database and baseline copies inside the owned root")
    case = json.loads((ROOT / "evals/business_model/a5-original-selection-case.json").read_text())
    instruction = case["instruction"]
    assert hashlib.sha256(instruction.encode()).hexdigest() == case["evidence"]["instruction_sha256"]
    workspace = root / "workspace"
    workspace.mkdir()
    manifest = root / "service-mcp.json"
    manifest.write_text('{"servers":{}}')
    os.environ.update({
        "CEO_ENV_FILE": str(root / "missing.env"), "CEO_WORKSPACE": str(workspace),
        "CEO_WORKER_DB": str(database), "CEO_SERVICE_MCP_CONFIG_PATH": str(manifest),
        "CEO_FEEDBACK_SPIKE_VERCEL_BASE_URL": "", "CEO_CORPUS_DIR": str(root / "no-corpus"),
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    from app.agent_cli import _task_file_root
    from app.agent_context import AgentTaskContext, MaterialReference
    from app.agent_orchestrator import AgentOrchestrator
    from app.agent_runtime_config import load_runtime_config
    from app.audit_agent import AuditAgentRunner
    from app.consumer_agent import ConsumerAgentRunner
    from app.dingtalk_models import DingTalkMessage
    from app.native_trajectory import find_codex_session_path
    from app.store import AutoReplyStore, CANDIDATE_REVIEW_SQL, REVIEW_CANDIDATE_SQL, EXTERNAL_ACTION_RESULT_SQL
    from app.system_action_handlers import native_dingtalk_message_handlers
    from app.system_executor import SystemExecutor
    from app.worker import DingTalkAutoReplyWorker

    store = AutoReplyStore(database)
    _reserve_fixture_task_ids(database)
    key = "a5-original-selection:" + uuid4().hex
    trigger = DingTalkMessage(
        open_conversation_id=key, open_message_id=key + ":trigger",
        conversation_title="Native selection copy acceptance", single_chat=False,
        sender_name="Derek", create_time="2026-10-19T00:00:00Z", content=instruction,
    )
    task = store.ensure_reply_task(
        conversation_id=key, conversation_title=trigger.conversation_title, single_chat=False,
        trigger_message_id=trigger.open_message_id, trigger_sender=trigger.sender_name,
        trigger_create_time=trigger.create_time, trigger_text=instruction,
        trigger_message_json=trigger.model_dump_json(),
    )
    artifacts = _task_file_root(database, task.id, task.execution_generation, create=True)
    source_file = artifacts / "case-input.json"
    source_file.write_text(json.dumps(case["material"]))
    assert hashlib.sha256(source_file.read_bytes()).hexdigest() == case["evidence"]["material_sha256"]
    task = store.claim_reply_task(task.id)
    assert task is not None
    context = AgentTaskContext(
        task_id=task.id, channel=task.channel, conversation_id=key,
        conversation_title=task.conversation_title, single_chat=False,
        trigger_message_id=task.trigger_message_id, trigger_sender=task.trigger_sender,
        trigger_text=instruction, trigger_create_time=task.trigger_create_time,
        messages=(), materials=(MaterialReference(
            "local_file", str(source_file), task.trigger_message_id,
            ('read_task_artifact(name="case-input.json")',),
        ),), prior_receipts=(), consumer_prompt=instruction,
        skill_protocol_override="Use only this test task local artifact and system action contract; no external source reads or writes.",
        skill_protocol_source="explicit_custom",
    )
    config = load_runtime_config({
        "CEO_AGENT_RUNTIME_ROUTES": "codex_oauth", "CEO_CODEX_MODEL": "gpt-6-luna",
        "CEO_CODEX_MODEL_REASONING_EFFORT": "high",
    })
    native = NativeExecutor(workspace, database)
    provider = InMemoryProvider(store, key)
    executor = SystemExecutor(store, native_dingtalk_message_handlers(store, provider), owner="a5-copy-system")
    driver = AgentOrchestrator(
        store=store,
        consumer=ConsumerAgentRunner(store=store, workspace=workspace, runtime_config=config,
            executor=native, skill_protocol_override=context.skill_protocol_override, owner="a5-copy-consumer"),
        audit=AuditAgentRunner(store=store, workspace=workspace, runtime_config=config,
            executor=native, skill_protocol_override=context.skill_protocol_override, owner="a5-copy-audit"),
        system_executor=executor,
    )
    worker = DingTalkAutoReplyWorker(store, object(), object(), agent_orchestrator=driver)
    worker._queued_task_prompt_context_messages = lambda *a, **kw: ([], [])
    worker._build_agent_task_context = lambda **kw: context
    worker._sync_processing_reactions = lambda: None
    worker._dismiss_problem_notification = lambda task: None
    worker._notify_problem_attempt = lambda *a, **kw: None
    report = {"case": case["case"], "original_evidence": case["evidence"], "task_id": task.id}
    worker.process_claimed_reply_task(task)
    current = store.get_reply_task(task.id)
    report.update(before_selection_status=current.status, before_selection_provider_calls=len(provider.calls))
    with store._connect() as db:
        candidates = [dict(r) for r in db.execute(
            f"select c.id,r.id as review_id,r.decision,c.proposal_revision from ({REVIEW_CANDIDATE_SQL}) c "
            f"join ({CANDIDATE_REVIEW_SQL}) r on r.candidate_id=c.id where c.task_id=? order by r.id", (task.id,),
        )]
    report["reviews"] = candidates
    # Write failure evidence before asserting; it must not disappear on a red run.
    (root / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    assert current.status == "needs_human", report
    assert not provider.calls and candidates[-1]["decision"] == "approve", report
    approved = candidates[-1]
    candidate = store.get_review_candidate(approved["id"])
    adopted = json.loads(candidate["candidate_json"])
    execute = next(option for option in adopted["decision_options"] if option["key"] == "execute")
    expected_body = execute["plan"]["actions"][0]["payload"]["content"]
    selection = store.select_candidate_option(approved["id"], approved["review_id"], "execute")
    store.wake_selected_candidate_execution(approved["id"], approved["review_id"])
    resumed = store.claim_reply_task(task.id)
    assert resumed is not None
    native_calls_before_selection = len(native.calls)
    assert worker.process_claimed_reply_task(resumed)
    current = store.get_reply_task(task.id)
    assert current.status == "done" and len(provider.calls) == len(provider.verifications) == 1
    assert len(native.calls) == native_calls_before_selection
    body = provider.calls[0]["text"]
    assert body == expected_body
    assert case["material"]["verification_tag"] in body and "100" in body and "3" in body
    runs = store.list_agent_runs_for_task_generation(task.id, task.execution_generation)
    report.update(
        status=current.status, selection_id=selection["id"],
        provider_calls=len(provider.calls), verification_calls=len(provider.verifications),
        delivered_body=body, delivered_body_equals_audited_option=True, native_invocations=native.calls,
        runs=[{"id": r.id, "role": r.role.value, "status": r.status,
               "revision": r.proposal_revision, "session_id": r.codex_session_id,
               "start": r.transcript_start_line, "end": r.transcript_end_line,
               "trajectory_available": bool(r.codex_session_id and find_codex_session_path(r.codex_session_id))}
              for r in runs],
    )
    with store._connect() as db:
        report["receipt_count"] = db.execute(
            f"select count(*) from ({EXTERNAL_ACTION_RESULT_SQL}) where business_object_key=?",
            (task.business_object_key,),
        ).fetchone()[0]
        report["foreign_keys"] = [tuple(r) for r in db.execute("pragma foreign_key_check")]
        assert db.execute("pragma quick_check").fetchone()[0] == "ok"
    assert report["receipt_count"] == 1
    reopened = AutoReplyStore(database)
    restored = AgentOrchestrator(store=reopened, consumer=object(), audit=object(), system_executor=None)
    assert restored.persisted_terminal_result(reopened.get_reply_task(task.id)).status == "executed"
    report["terminal_reopen_preserved"] = True
    report["original_tables_unchanged"] = original_rows_unchanged(baseline, database)
    report["passed"] = True
    (root / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({"passed": True, "task_id": task.id, "native_calls": len(native.calls), "fake_sends": 1}), flush=True)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    args = parser.parse_args()
    run(args.root, database=args.database, baseline=args.baseline)
