#!/usr/bin/env python3
"""Replay the old A5 selection case with real GPT-6 on an already migrated copy.

Run prepare, select and finish in separate processes to exercise cold reopening.
Only the provider is fake. Native CLI authentication and home remain unchanged.
The caller owns the database snapshot, migration and optional pristine baseline.
"""

from pathlib import Path
import argparse
import importlib.util
import hashlib
import json
import os
import sqlite3
import sys
import subprocess
from datetime import datetime, timezone
from itertools import islice
from urllib.parse import urlencode
from uuid import uuid4

WT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WT))
ROOT = DB = META = WORKSPACE = BASELINE = None
harness = None


def initialize(args):
    global ROOT, DB, META, WORKSPACE, BASELINE, harness
    ROOT = Path(args.owned_temp_root)
    assert ROOT.is_absolute() and ROOT.is_dir() and ROOT.parent == Path("/tmp")
    assert ROOT.name.startswith("ceo-gpt6-db-release.")
    DB = Path(args.database)
    assert (
        DB.is_absolute()
        and DB.is_file()
        and DB.resolve().is_relative_to(ROOT.resolve())
    )
    BASELINE = Path(args.baseline) if args.baseline else None
    if BASELINE is not None:
        assert BASELINE.is_absolute() and BASELINE.is_file() and BASELINE != DB
    META = ROOT / "gpt6-native-result.json"
    WORKSPACE = ROOT / "gpt6-native-workspace"
    environment()
    spec = importlib.util.spec_from_file_location(
        "native_copy_harness", WT / "scripts/validate_native_copy_lifecycle.py"
    )
    harness = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(harness)


def schema_pair():
    from app.state_definitions import state_projection

    with sqlite3.connect(DB) as db:
        store = db.execute(
            f"select value from ({state_projection('service_state')}) "
            "where key='store_schema_version'"
        ).fetchone()[0]
        email = db.execute(
            "select max(version) from email_schema_migrations"
        ).fetchone()[0]
    assert (store, email) == ("2026-10-19.11", 53)
    return {"store": store, "email": email}


def native_model_records(path, start, end):
    """Read actual bounded native turn metadata without copying trajectory text."""
    records = []
    with path.open() as stream:
        for line_number, line in enumerate(islice(stream, start, end), start=start):
            entry = json.loads(line)
            if entry.get("type") == "turn_context":
                payload = entry["payload"]
                records.append(
                    {
                        "line": line_number,
                        "model": payload.get("model"),
                        "effort": payload.get("effort"),
                    }
                )
    assert records, "No native turn_context metadata in runtime attempt line bounds"
    assert all(
        r["model"] == "gpt-6-astra" and r["effort"] == "high" for r in records
    ), records
    return records


def environment():
    WORKSPACE.mkdir(exist_ok=True)
    manifest = ROOT / "gpt6-native-mcp.json"
    manifest.write_text('{"servers":{}}')
    os.environ.update(
        CEO_ENV_FILE=str(ROOT / "missing-native.env"),
        CEO_WORKSPACE=str(WORKSPACE),
        CEO_WORKER_DB=str(DB),
        CEO_SERVICE_MCP_CONFIG_PATH=str(manifest),
        CEO_FEEDBACK_SPIKE_VERCEL_BASE_URL="",
        CEO_CORPUS_DIR=str(ROOT / "no-corpus"),
        PYTHONDONTWRITEBYTECODE="1",
    )


def emit(report, stage, **values):
    report[stage] = values
    META.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(stage, json.dumps(values, ensure_ascii=False), flush=True)


def context_for(task, instruction, source_file):
    from app.agent_context import AgentTaskContext, MaterialReference

    return AgentTaskContext(
        task_id=task.id,
        channel=task.channel,
        conversation_id=task.conversation_id,
        conversation_title=task.conversation_title,
        single_chat=False,
        trigger_message_id=task.trigger_message_id,
        trigger_sender=task.trigger_sender,
        trigger_text=instruction,
        trigger_create_time=task.trigger_create_time,
        messages=(),
        materials=(
            MaterialReference(
                "local_file",
                str(source_file),
                task.trigger_message_id,
                ('read_task_artifact(name="case-input.json")',),
            ),
        ),
        prior_receipts=(),
        consumer_prompt=instruction,
        skill_protocol_override="Use only this test task local artifact and system action contract; no external source reads or writes.",
        skill_protocol_source="explicit_custom",
    )


def worker_for(store, driver, context):
    from app.worker import DingTalkAutoReplyWorker

    worker = DingTalkAutoReplyWorker(
        store, object(), object(), agent_orchestrator=driver
    )
    worker._queued_task_prompt_context_messages = lambda *a, **kw: ([], [])
    worker._build_agent_task_context = lambda **kw: context
    worker._sync_processing_reactions = lambda: None
    worker._dismiss_problem_notification = lambda task: None
    worker._notify_problem_attempt = lambda *a, **kw: None
    return worker


def prepare():
    from app.store import AutoReplyStore
    from app.agent_cli import _task_file_root
    from app.agent_orchestrator import AgentOrchestrator
    from app.agent_runtime_config import load_runtime_config
    from app.audit_agent import AuditAgentRunner
    from app.consumer_agent import ConsumerAgentRunner
    from app.dingtalk_models import DingTalkMessage
    from app.system_action_handlers import native_dingtalk_message_handlers
    from app.system_executor import SystemExecutor

    assert not META.exists()
    report = {
        "case": "business-model-gpt6-selection-copy.v1",
        "database": str(DB),
        "baseline": str(BASELINE) if BASELINE else None,
        "baseline_sha256": harness._file_sha256(BASELINE) if BASELINE else None,
        "schema_pair": schema_pair(),
        "source_head": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=WT, text=True
        ).strip(),
        "runtime": {
            "routes": ["codex_oauth"],
            "model": "gpt-6-astra",
            "reasoning_effort": "high",
        },
        "validation_limit": "Real Store claim, Consumer/Audit, public human selection, SystemExecutor and Worker on migrated copy; only outbound provider is in memory. Existing postfix permitted. Source-count assertion is new coverage beyond the old tag/sum assertion.",
    }
    store = AutoReplyStore(DB)
    reservation = harness._reserve_fixture_task_ids(DB)

    key = "native-copy-selection:" + uuid4().hex
    instruction = (
        '这是隔离数据库副本中的真实CLI验收，材料均为合成测试数据。汇总只要求包含tag、条数和总和，允许系统既有(via agent)附注，不要求逐字正文，不要自行增加逐字相等约束。请用read_task_artifact(name="case-input.json")'
        "核对记录数量和units总和。材料明确没有约定本类离线验收的结果发布规则，请提交一项可复用的task_class规则选择："
        "execute表示以后本类验收通过后向当前来源测试群发送带verification_tag、条目数和总和的简短汇总；"
        "stop表示本类验收只保留本地结果、不发送通知。请输出真实的needs_human候选和完整依据，"
        "先通过read_system_action_contract读取dingtalk-chat的send_group_message契约，再给execute绑定可执行方案，"
        "给stop绑定skipped终态。人尚未选择，不要自行执行。系统只会用内存替身执行所选方案，绝不真实发送。"
        "所有规则仅适用本合成验收任务，不写用户偏好或Memory；不读真实消息、不访问网络、不调用其他工具。"
    )
    trigger = DingTalkMessage(
        open_conversation_id=key,
        open_message_id=key + ":trigger",
        conversation_title="Native selection copy acceptance",
        single_chat=False,
        sender_name="Derek",
        create_time=datetime.now(timezone.utc).isoformat(),
        content=instruction,
    )
    task = store.ensure_reply_task(
        conversation_id=key,
        conversation_title=trigger.conversation_title,
        single_chat=False,
        trigger_message_id=trigger.open_message_id,
        trigger_sender="Derek",
        trigger_create_time=trigger.create_time,
        trigger_text=instruction,
        trigger_message_json=trigger.model_dump_json(),
    )
    assert task.id == reservation + 1
    source_file = (
        _task_file_root(DB, task.id, task.execution_generation, create=True)
        / "case-input.json"
    )
    fixture = {
        "verification_tag": "NATIVE_SELECTION_9178ab3793",
        "records": [{"units": 23}, {"units": 27}, {"units": 50}],
        "task_class": "isolated_copy_task_lifecycle_format_notifications",
        "notification_policy": None,
        "message_format": "Include tag, count and sum; existing system (via agent) postfix is permitted; no verbatim equality requirement.",
        "policy_scope": "This synthetic acceptance only; never real user preferences or external delivery.",
    }
    source_file.write_text(json.dumps(fixture, ensure_ascii=False))
    fixture_sha256 = harness._file_sha256(source_file)
    task = store.claim_reply_task(task.id)
    context = context_for(task, instruction, source_file)
    config = load_runtime_config(
        {"CEO_AGENT_RUNTIME_ROUTES": "codex_oauth", "CEO_CODEX_MODEL": "gpt-6-astra"}
    )
    native = harness.NativeExecutor(WORKSPACE, DB)
    provider = harness.InMemoryProvider(store, key)
    driver = AgentOrchestrator(
        store=store,
        consumer=ConsumerAgentRunner(
            store=store,
            workspace=WORKSPACE,
            runtime_config=config,
            executor=native,
            skill_protocol_override=context.skill_protocol_override,
            reasoning_effort="high",
            owner="native-selection-consumer",
        ),
        audit=AuditAgentRunner(
            store=store,
            workspace=WORKSPACE,
            runtime_config=config,
            executor=native,
            skill_protocol_override=context.skill_protocol_override,
            reasoning_effort="high",
            owner="native-selection-audit",
        ),
        system_executor=SystemExecutor(
            store,
            native_dingtalk_message_handlers(store, provider),
            owner="native-selection-system",
        ),
    )
    worker_for(store, driver, context).process_claimed_reply_task(task)
    report.update(
        task_id=task.id,
        generation=task.execution_generation,
        conversation_id=key,
        instruction=instruction,
        source_file=str(source_file),
        verification_tag=fixture["verification_tag"],
        fixture_sha256=fixture_sha256,
    )
    complete_prepare_report(report, store, task, native.calls, len(provider.calls))


def complete_prepare_report(report, store, task, native_calls, provider_calls):
    """Read existing native runs and persist acceptance metadata; never run a model."""
    from app.action_storage import REPLY_ATTEMPT_COMMON_SQL
    from app.native_trajectory import find_codex_session_path, read_codex_events

    current = store.get_reply_task(task.id)
    reviewed = store.current_reviewed_candidate(task.id, task.execution_generation)
    runs = store.list_agent_runs_for_task_generation(task.id, task.execution_generation)
    with store._connect() as db:
        attempt = db.execute(
            f"select id,send_status from ({REPLY_ATTEMPT_COMMON_SQL}) "
            "where conversation_id=? order by id desc limit 1",
            (task.conversation_id,),
        ).fetchone()
    report["native_calls"] = native_calls
    emit(
        report,
        "prepared",
        task_status=current.status,
        provider_calls=provider_calls,
        candidate_id=reviewed["id"] if reviewed else None,
        review_id=reviewed["review_id"] if reviewed else None,
        candidate_digest=reviewed["candidate_digest"] if reviewed else None,
        attempt_id=attempt["id"] if attempt else None,
    )
    assert (
        current.status == "needs_human"
        and reviewed is not None
        and not reviewed["selection_id"]
    )
    assert provider_calls == 0 and attempt["send_status"] == "needs_human"
    from app.run_storage import RUNTIME_ATTEMPT_SQL

    native_records = []
    for run in runs:
        item = {
            "id": run.id,
            "role": run.role.value,
            "status": run.status,
            "attempts": [],
        }
        with store._connect() as db:
            actual_attempts = [
                dict(a)
                for a in db.execute(
                    f"select * from ({RUNTIME_ATTEMPT_SQL}) where agent_run_id=? order by id",
                    (run.id,),
                )
            ]
        assert actual_attempts
        for a in actual_attempts:
            path = find_codex_session_path(a["session_id"])
            assert (
                path is not None
                and path.is_file()
                and a["transcript_end"] > a["transcript_start"] >= 0
            )
            events = read_codex_events(
                a["session_id"],
                start_line=a["transcript_start"],
                end_line=a["transcript_end"],
            )
            assert events
            names = []
            commands = []
            for event in events:
                node = event.get("item", {})
                if node.get("type") == "command_execution":
                    commands.append(node.get("command"))
                if node.get("type") == "mcp_tool_call":
                    assert node.get("server") == "agent_cli" and node.get("tool") in {
                        "read_task_artifact",
                        "read_system_action_contract",
                    }
                    names.append(node["tool"])
            item["attempts"].append(
                dict(
                    attempt_id=a["id"],
                    session_id=a["session_id"],
                    start=a["transcript_start"],
                    end=a["transcript_end"],
                    status=a["status"],
                    tools=names,
                    command_count=len(commands),
                    event_count=len(events),
                    native_path=str(path),
                    actual_native_turns=native_model_records(
                        path, a["transcript_start"], a["transcript_end"]
                    ),
                )
            )
        native_records.append(item)
    assert any(
        "read_task_artifact" in a["tools"]
        for item in native_records
        for a in item["attempts"]
    )
    report["native_evidence"] = {"runs": native_records}
    assert_native_fixture_reads(report)
    emit(
        report,
        "native_evidence",
        runs=native_records,
        candidate_id=reviewed["id"],
        review_id=reviewed["review_id"],
        attempt_id=attempt["id"],
        original_tables_unchanged=harness.original_rows_unchanged(BASELINE, DB)
        if BASELINE
        else None,
    )


def assert_native_fixture_reads(report):
    """Bind the synthetic input to its native read result after normal cleanup."""
    from app.native_trajectory import read_codex_events

    reads = []
    for run in report["native_evidence"]["runs"]:
        for attempt in run["attempts"]:
            events = read_codex_events(
                attempt["session_id"],
                start_line=attempt["start"],
                end_line=attempt["end"],
            )
            for event in events:
                item = event.get("item", {})
                if (
                    item.get("type") != "mcp_tool_call"
                    or item.get("server") != "agent_cli"
                    or item.get("tool") != "read_task_artifact"
                ):
                    continue
                assert item["arguments"]["name"] == Path(report["source_file"]).name
                result = item["result"]
                assert not result["isError"]
                body = result["structuredContent"]
                assert body["name"] == Path(report["source_file"]).name
                assert body["offset"] == 0 and body["next_offset"] is None
                assert len(body["content"]) == body["total_chars"]
                digest = hashlib.sha256(body["content"].encode()).hexdigest()
                assert digest == body["sha256"] == report["fixture_sha256"]
                reads.append(
                    {
                        "session_id": attempt["session_id"],
                        "start": attempt["start"],
                        "end": attempt["end"],
                        "tool_call_id": item["id"],
                        "name": body["name"],
                        "sha256": digest,
                    }
                )
    assert reads
    report["fixture_native_reads"] = reads


def finish():
    from app.store import AutoReplyStore
    from app.agent_orchestrator import AgentOrchestrator
    from app.system_action_handlers import native_dingtalk_message_handlers
    from app.system_executor import SystemExecutor

    report = json.loads(META.read_text())
    assert report["database"] == str(DB)
    assert report["baseline"] == (str(BASELINE) if BASELINE else None)
    assert schema_pair() == report["schema_pair"]
    store = AutoReplyStore(DB)
    task = store.get_reply_task(report["task_id"])
    reviewed = store.current_reviewed_candidate(task.id, task.execution_generation)
    assert (
        task.status == "pending"
        and reviewed["selection_id"] is not None
        and reviewed["option_key"] == "execute"
    ), "Run --phase select first"
    task = store.claim_reply_task(task.id)
    provider = harness.InMemoryProvider(store, task.conversation_id)
    driver = AgentOrchestrator(
        store=store,
        consumer=object(),
        audit=object(),
        system_executor=SystemExecutor(
            store,
            native_dingtalk_message_handlers(store, provider),
            owner="native-selection-system",
        ),
    )
    context = context_for(task, report["instruction"], Path(report["source_file"]))
    worker_for(store, driver, context).process_claimed_reply_task(task)
    current = store.get_reply_task(task.id)
    assert (
        current.status == "done"
        and len(provider.calls) == 1
        and len(provider.verifications) == 1
    )
    text = provider.calls[0]["text"]
    assert report["verification_tag"] in text and "100" in text
    # New count coverage, explicitly separate from the retained old-case assertion.
    assert "3" in text.replace(report["verification_tag"], "")
    execution = store.get_candidate_execution(reviewed["id"])
    attempts = store.list_candidate_action_attempts(execution["id"])
    assert (
        execution["status"] == "done"
        and len(attempts) == 1
        and attempts[0]["status"] == "verified"
    )
    receipt = store.get_candidate_external_action(attempts[0]["external_action_key"])
    assert receipt is not None
    assert attempts[0]["result_json"] == receipt["provider_result_json"]
    sent = store.get_sent_reply(task.conversation_id, task.trigger_message_id)
    assert sent is not None and sent.send_result_json == receipt["provider_result_json"]
    with store._connect() as sent_db:
        sent_raw = sent_db.execute(
            "select send_result_json,external_action_key from actions where record_type='sent_replies' and cast(legacy_key as integer)=?",
            (sent.id,),
        ).fetchone()
        assert (
            sent_raw is not None
            and sent_raw[0] is None
            and sent_raw[1] == attempts[0]["external_action_key"]
        )

    with store._connect() as connection:
        raw = connection.execute(
            "select result_json,status from actions where record_type='candidate_action_attempts' and cast(legacy_key as integer)=?",
            (attempts[0]["id"],),
        ).fetchone()
        assert raw["result_json"] is None and raw["status"] == "verified"
    with store._connect() as db:
        raw_alias = db.execute(
            "select id,candidate_id,payload_json=jsonb('[]') from actions where record_type='candidate_execution_alias' and cast(legacy_key as integer)=?",
            (execution["id"],),
        ).fetchone()
        assert raw_alias is not None and raw_alias[2] == 1
        raw_plan = db.execute(
            "select execution_public_id,execution_status from actions where id=?",
            (raw_alias[1],),
        ).fetchone()
        assert raw_plan[0] == execution["id"] and raw_plan[1] == "done"
        assert (
            db.execute(
                "select count(*) from actions where record_type='candidate_executions'"
            ).fetchone()[0]
            == 0
        )
    reopened = AutoReplyStore(DB)
    assert (
        reopened.current_reviewed_candidate(task.id, task.execution_generation)[
            "selection_id"
        ]
        == reviewed["selection_id"]
    )
    assert (
        reopened.list_human_decision_evidence(task.id)[0]["selected_branch"]["key"]
        == "execute"
    )
    assert len(
        store.list_agent_runs_for_task_generation(task.id, task.execution_generation)
    ) == len(report["native_evidence"]["runs"])
    assert_native_fixture_reads(report)
    assert schema_pair() == report["schema_pair"]
    if BASELINE is not None:
        assert harness._file_sha256(BASELINE) == report["baseline_sha256"]
    emit(
        report,
        "completed",
        task_status=current.status,
        execution_id=execution["id"],
        provider_calls=1,
        provider_verifications=1,
        verified_attempts=1,
        external_receipts=1,
        sent_result_is_reference=True,
        sent_public_result_exact=True,
        no_additional_native_turn=True,
        original_tables_unchanged=harness.original_rows_unchanged(BASELINE, DB)
        if BASELINE
        else None,
        old_tag_sum_assertion=True,
        new_source_count_assertion=True,
        synthetic_sent_text=text,
        synthetic_sent_sha256=hashlib.sha256(text.encode()).hexdigest(),
    )


def select():
    from app.audit_web import handle_needs_human_decision_post
    from app.store import AutoReplyStore

    report = json.loads(META.read_text())
    assert report["database"] == str(DB)
    assert report["baseline"] == (str(BASELINE) if BASELINE else None)
    assert schema_pair() == report["schema_pair"]
    store = AutoReplyStore(DB)
    task = store.get_reply_task(report["task_id"])
    reviewed = store.current_reviewed_candidate(task.id, report["generation"])
    saved = report["prepared"]
    assert task.status == "needs_human" and reviewed["selection_id"] is None
    assert (reviewed["id"], reviewed["review_id"], reviewed["candidate_digest"]) == (
        saved["candidate_id"],
        saved["review_id"],
        saved["candidate_digest"],
    )
    body = urlencode(
        {
            "kind": "select",
            "candidate_id": saved["candidate_id"],
            "review_id": saved["review_id"],
            "candidate_digest": saved["candidate_digest"],
            "option_key": "execute",
        }
    ).encode()
    status, headers, _ = handle_needs_human_decision_post(
        store, saved["attempt_id"], body
    )
    assert status == 303
    reopened = AutoReplyStore(DB)
    current = reopened.current_reviewed_candidate(task.id, report["generation"])
    assert reopened.get_reply_task(task.id).status == "pending"
    assert current["selection_id"] is not None and current["option_key"] == "execute"
    emit(
        report,
        "selected",
        http_status=status,
        selection_id=current["selection_id"],
        candidate_id=current["id"],
        review_id=current["review_id"],
        candidate_digest=current["candidate_digest"],
        option_key=current["option_key"],
        location=headers["Location"],
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--phase", required=True, choices=("prepare", "select", "finish")
    )
    parser.add_argument("--owned-temp-root", required=True)
    parser.add_argument(
        "--database", required=True, help="Existing Store19.11/Email53 copy"
    )
    parser.add_argument(
        "--baseline", help="Optional pristine migrated copy; never created here"
    )
    args = parser.parse_args()
    initialize(args)
    {"prepare": prepare, "select": select, "finish": finish}[args.phase]()
