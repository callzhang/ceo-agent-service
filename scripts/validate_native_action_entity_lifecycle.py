#!/usr/bin/env python3
"""Run native Consumer/Audit lifecycle and current Action roundtrip on an owned copy."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.validate_native_copy_lifecycle import (  # noqa: E402
    InMemoryProvider,
    NativeExecutor,
    _file_sha256,
    _install_settlement_interrupt,
    _reserve_fixture_task_ids,
)


def _copy(source: Path, target: Path) -> None:
    if target.exists():
        raise FileExistsError(target)
    with sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True) as src, sqlite3.connect(target) as dst:
        src.execute("PRAGMA query_only=ON")
        src.backup(dst)


def _old_public_read_and_write(db_path: Path, old_root: Path, key: str, body: str,
                               conversation_id: str, trigger_message_id: str) -> dict:
    import subprocess
    script = db_path.parent / ("old-write-" + hashlib.sha256(key.encode()).hexdigest()[:10] + ".py")
    script.write_text(
        "from pathlib import Path\nimport app.store as store_module\nfrom app.store import AutoReplyStore\n"
        f"old_root=Path({str(old_root.resolve())!r}).resolve()\n"
        "module_path=Path(store_module.__file__).resolve()\n"
        "assert module_path.is_relative_to(old_root), f'old runtime module escaped pinned root: {module_path}'\n"
        "import hashlib\nmodule_sha256=hashlib.sha256(module_path.read_bytes()).hexdigest()\n"
        f"store=AutoReplyStore(Path({str(db_path)!r}))\n"
        f"sent=store.get_sent_reply({conversation_id!r},{trigger_message_id!r})\n"
        "assert sent is not None, 'pinned old runtime cannot read the new native Sent fact'\n"
        "sent_data=sent.model_dump()\n"
        f"item=store.prepare_outbound_postfix('dingtalk',{key!r},{body!r},'native Task6A2 acceptance')\n"
        "import json\nprint(json.dumps({'sent':sent_data,'prepared_body':item.final_body,'old_module_path':str(module_path),'old_module_sha256':module_sha256},ensure_ascii=False))\n"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(old_root)
    result = subprocess.run([sys.executable, str(script)], cwd=old_root, env=env,
                            capture_output=True, text=True, timeout=300)
    if result.returncode:
        from app.leak_check import redact_credentials
        raise RuntimeError("pinned old-code cold public write failed: " + redact_credentials(result.stderr)[-1200:])
    return json.loads(result.stdout.strip())


def _stable_inverse_facts(facts: dict, *, exclude_delivery_key: str | None = None) -> dict:
    normalized = {}
    for family, rows in facts.items():
        retained = []
        for row in rows:
            if exclude_delivery_key and row.get("delivery_key") == exclude_delivery_key:
                continue
            retained.append({key: value for key, value in row.items() if key != "_action_id"})
        normalized[family] = sorted(retained, key=lambda row: json.dumps(row, sort_keys=True, ensure_ascii=False))
    return normalized


def _assert_native_current_facts_equal(before: dict, after: dict, *, exclude_delivery_key: str) -> None:
    """Match source facts by typed business keys, allowing only newly allocated source IDs."""
    identities = {
        "external_action_results": ("external_action_key",),
        "candidate_action_attempts": ("execution_id", "action_index"),
        "outbound_postfixes": ("channel", "delivery_key"),
        "outbound_postfix_receipts": ("channel", "delivery_key"),
        "native_reply_dispatches": ("channel", "delivery_key"),
        "sent_replies": ("conversation_id", "trigger_message_id", "sent_at", "agent_run_id"),
        "sent_reply_observers": ("agent_run_id", "reply_task_id", "created_at"),
        "email_actions": ("action_id",),
        "email_action_attempts": ("action_id", "attempt_number"),
    }

    def index(family: str, rows: list[dict]) -> dict[tuple, dict]:
        key_fields = identities[family]
        indexed = {tuple(row.get(field) for field in key_fields): row for row in rows}
        if len(indexed) != len(rows):
            raise AssertionError(f"{family} contains duplicate typed business identities")
        return indexed

    before_sent = index("sent_replies", before["sent_replies"])
    after_sent = index("sent_replies", after["sent_replies"])
    sent_key_by_id = {}
    for key, row in after_sent.items():
        if row.get("id") is not None:
            if row["id"] in sent_key_by_id:
                raise AssertionError("Sent source IDs are not unique")
            sent_key_by_id[row["id"]] = key
    new_sent_keys = [key for key, row in before_sent.items() if row.get("id") is None]
    new_sent_by_run = {}
    for key in new_sent_keys:
        run_id = key[-1]
        if run_id in new_sent_by_run:
            raise AssertionError("new Sent facts do not have a unique run identity")
        new_sent_by_run[run_id] = key

    for family in identities:
        before_rows = [row for row in before[family]
                       if not (family == "outbound_postfixes" and row.get("delivery_key") == exclude_delivery_key)]
        after_rows = [row for row in after[family]
                      if not (family == "outbound_postfixes" and row.get("delivery_key") == exclude_delivery_key)]
        left, right = index(family, before_rows), index(family, after_rows)
        if set(left) != set(right):
            raise AssertionError(f"native current {family} typed business identities changed")
        for key in left:
            old, new = dict(left[key]), dict(right[key])
            old.pop("_action_id", None)
            new.pop("_action_id", None)
            if family in {"candidate_action_attempts", "sent_replies"}:
                old_id, new_id = old.pop("id", None), new.pop("id", None)
                if old_id is None:
                    if not isinstance(new_id, int):
                        raise AssertionError(f"new native {family} source identity was not allocated")
                elif old_id != new_id:
                    raise AssertionError(f"existing native {family} source identity changed")
            if family == "sent_reply_observers":
                old_ref, new_ref = old.pop("_legacy_identity", None), new.pop("_legacy_identity", None)
                if old_ref is None:
                    target = new_sent_by_run.get(key[0])
                    if target is None:
                        raise AssertionError("new observer lacks a matching newly created Sent identity")
                else:
                    target = sent_key_by_id.get(old_ref[0])
                    if target is None:
                        raise AssertionError("observer source Sent ID does not resolve to a typed Sent fact")
                if new_ref is None or sent_key_by_id.get(new_ref[0]) != target:
                    raise AssertionError("observer Sent reference changed typed identity")
            if old != new:
                raise AssertionError(f"native current {family} business fact values changed")


def _assert_old_cold_write_fact(facts: dict, delivery_key: str, prepared_body: str) -> None:
    cold_fact = next((row for row in facts.get("outbound_postfixes", [])
                      if row.get("delivery_key") == delivery_key), None)
    if cold_fact is None or cold_fact.get("final_body") != prepared_body:
        raise AssertionError("pinned old-code public cold-write fact was not carried forward")


def _assert_old_native_sent_read(sent: dict, body: str, provider_receipt_sha256: str,
                                 agent_run_id: int) -> None:
    if sent.get("reply_text") != body or sent.get("agent_run_id") != agent_run_id:
        raise AssertionError("pinned old runtime read a different native Sent body or run")
    actual_receipt_sha256 = hashlib.sha256((sent.get("send_result_json") or "").encode()).hexdigest()
    if actual_receipt_sha256 != provider_receipt_sha256:
        raise AssertionError("pinned old runtime read a different native provider receipt")


def _print_event(event: dict) -> None:
    print(json.dumps(event), flush=True)


def _audit_run_id(runs: list) -> int:
    audit_runs = [run for run in runs if run.role.value == "audit"]
    if len(audit_runs) != 1:
        raise AssertionError(f"expected one audit run, found {len(audit_runs)}")
    return audit_runs[0].id


def _case_fixture(key: str) -> dict:
    return {"verification_tag": "TASK6A2_" + uuid4().hex[:12],
            "records": [{"units": 23}, {"units": 27}, {"units": 50}],
            "synthetic_group": {"conversation_id": key,
                "membership_basis": "complete synthetic roster for this isolated test; identifiers are local fixture IDs, not provider accounts",
                "conversation_state": "This test group has no prior messages; the task trigger is the complete current discussion.",
                "members": [
                    {"stable_id": "synthetic-member-001", "name": "Derek", "title": "Principal",
                     "role": "principal", "responsibility": "requested the database-copy verification summary",
                     "fit": "recipient of the requested test summary"},
                    {"stable_id": "synthetic-member-002", "name": "Test recipient", "title": "Test group member",
                     "role": "team member", "responsibility": "receives the database-copy verification summary in this synthetic test group",
                     "fit": "recipient of the requested test summary"}]}}


def _acceptance_instruction() -> str:
    return (
        '副本验收任务：请调用 read_task_artifact(name="case-input.json") 读取当前任务材料，'
        '核对记录数量并计算 units 总和；调用 read_system_action_contract 读取 dingtalk-chat 的 '
        'send_group_message 契约。请提出一个 send_group_message 候选，正文包含 verification_tag、'
        '记录数和 units 总和，并注明这是数据库副本测试。case-input.json 同时包含此合成群的完整成员、角色和接收适配信息；'
        '该文件是本次隔离验收的群成员事实来源。可用系统动作注册表仅声明 send_group_message。'
        '此运行的 SystemExecutor 只连接到本地内存测试替身，'
        '没有真实 provider 客户端或网络路径；你不要调用任何直接发送工具。经 Consumer 与 Audit 批准后，'
        '系统会按正常执行路径调用该替身。先读取再计算，输出完整 Consumer 合同 JSON。'
    )


def _roundtrip(current_db: Path, case_dir: Path, old_root: Path, unique: str,
               conversation_id: str, trigger_message_id: str, expected_body: str,
               expected_agent_run_id: int) -> dict:
    from app.action_entity_schema_release import invert_action_entity_release
    from app.action_entity_transfer import _capture_inverse_facts
    from app.action_entity_schema_release import prepare_action_entity_release, publish_action_entity_release
    from tools.validate_action_entity_copy import _compare_source_databases

    source_old = case_dir / "source-old-schema.sqlite3"
    _copy(Path("/tmp/action-entity-task6-corrected.IH2XQu/copy/before-inverse.sqlite3"), source_old)
    before = case_dir / "pre-inverse-current.sqlite3"
    _copy(current_db, before)
    with sqlite3.connect(before) as db:
        facts_before = _capture_inverse_facts(db)["facts"]
        action_count = db.execute("select count(*) from actions").fetchone()[0]
        receipt_count = db.execute("select count(*) from action_receipts").fetchone()[0]
    native_sent = [row for row in facts_before["sent_replies"]
                   if row.get("conversation_id") == conversation_id
                   and row.get("trigger_message_id") == trigger_message_id
                   and row.get("agent_run_id") == expected_agent_run_id]
    if len(native_sent) != 1:
        raise AssertionError(f"expected one typed native Sent fact, found {len(native_sent)}")
    native_provider_receipts = [row for row in facts_before["external_action_results"]
                                if row.get("external_action_key") == native_sent[0].get("external_action_key")]
    if len(native_provider_receipts) != 1:
        raise AssertionError(f"expected one provider result linked to native Sent, found {len(native_provider_receipts)}")
    expected_provider_receipt_sha256 = hashlib.sha256(
        native_provider_receipts[0]["provider_result_json"].encode()).hexdigest()
    contract = prepare_action_entity_release(source_old)
    contract["database"] = str(current_db.resolve())
    invert_action_entity_release(current_db, contract)
    print(json.dumps({"event": "inverse", "case_dir": str(case_dir)}), file=sys.stderr, flush=True)
    cold_key = "task6a2-cold-" + unique
    print(json.dumps({"event": "oldcold_start", "case_dir": str(case_dir)}), file=sys.stderr, flush=True)
    old_read = _old_public_read_and_write(
        current_db, old_root, cold_key, "Pinned old runtime cold write " + unique,
        conversation_id, trigger_message_id)
    cold_body = old_read["prepared_body"]
    if Path(old_read["old_module_path"]).resolve() != (old_root / "app" / "store.py").resolve():
        raise AssertionError("pinned old runtime module proof resolved outside the exact Store source")
    _assert_old_native_sent_read(old_read["sent"], expected_body,
                                 expected_provider_receipt_sha256, expected_agent_run_id)
    old_module = (old_root / "app" / "store.py").resolve()
    old_module_sha256 = _file_sha256(old_module)
    if (Path(old_read["old_module_path"]).resolve() != old_module
            or old_read["old_module_sha256"] != old_module_sha256):
        raise AssertionError("pinned old runtime module path or SHA-256 differs from the pinned source")
    print(json.dumps({"event": "oldcold_end", "body_sha256": hashlib.sha256(cold_body.encode()).hexdigest()}),
          file=sys.stderr, flush=True)
    after_cold = case_dir / "after-old-cold.sqlite3"
    _copy(current_db, after_cold)
    publish_action_entity_release(current_db, contract)
    print(json.dumps({"event": "reforward", "case_dir": str(case_dir)}), file=sys.stderr, flush=True)
    inverse_check = case_dir / "reforward-inverse-check.sqlite3"
    _copy(current_db, inverse_check)
    inverse_contract = {**contract, "database": str(inverse_check.resolve())}
    invert_action_entity_release(inverse_check, inverse_contract)
    cohort = _compare_source_databases(after_cold, inverse_check)
    with sqlite3.connect(current_db) as db:
        facts_after = _capture_inverse_facts(db)["facts"]
        current_types = db.execute("select record_type,count(*) from actions group by record_type").fetchall()
    _assert_old_cold_write_fact(facts_after, cold_key, cold_body)
    _assert_native_current_facts_equal(facts_before, facts_after, exclude_delivery_key=cold_key)
    return {"current_inverse": "passed", "pinned_old_code_native_sent_read": "passed",
            "pinned_old_code_public_write": "passed", "reforward": "passed",
            "pinned_old_store_module_path": str(old_module),
            "pinned_old_store_module_sha256": old_module_sha256,
            "pinned_old_native_sent_body_sha256": hashlib.sha256(expected_body.encode()).hexdigest(),
            "pinned_old_native_provider_receipt_sha256": expected_provider_receipt_sha256,
            "pinned_old_native_sent_agent_run_id": expected_agent_run_id,
            "native_action_rows_before": action_count,
            "native_receipt_rows_before": receipt_count,
            "native_current_fact_families_before": {kind: len(rows) for kind, rows in facts_before.items()},
            "native_current_fact_families_after": {kind: len(rows) for kind, rows in facts_after.items()},
            "cold_write_body_sha256": hashlib.sha256(cold_body.encode()).hexdigest(),
            "same_cohort_inverse_comparison": cohort,
            "action_type_counts_after": dict(current_types),
            "native_current_fact_families_equal_after_cold_write": True,
            "pinned_old_code_read_message_id": old_read["sent"].get("reply_message_id"),
            "inverse_fact_families": sorted(facts_before)}


def run(current_copy: Path, root: Path, old_root: Path, case: str = "approve") -> dict:
    current_copy, root, old_root = current_copy.resolve(), root.resolve(), old_root.resolve()
    if not root.is_relative_to(Path("/tmp").resolve()) or root.exists():
        raise ValueError("--owned-temp-root must be a new directory under /tmp")
    if _file_sha256(current_copy) != "176ad9cbfb2d835933f72a714252467cbc54209297e7f26bc4a2f2c58363538b":
        raise ValueError("current copy SHA-256 does not match the reviewed A1 cohort")
    root.mkdir(parents=True)
    case_dir = root / case
    case_dir.mkdir()
    db_path = case_dir / "candidate.sqlite3"
    _copy(current_copy, db_path)
    before_hash = _file_sha256(current_copy)
    workspace = case_dir / "workspace"
    workspace.mkdir()
    manifest = case_dir / "service-mcp.json"
    manifest.write_text('{"servers":{}}')
    os.environ.update({"CEO_ENV_FILE": str(case_dir / "no-service.env"),
        "CEO_WORKSPACE": str(workspace), "CEO_WORKER_DB": str(db_path),
        "CEO_FEEDBACK_SPIKE_VERCEL_BASE_URL": "", "CEO_SERVICE_MCP_CONFIG_PATH": str(manifest),
        "CEO_CORPUS_DIR": str(case_dir / "no-corpus"), "CEO_AGENT_RUNTIME_ROUTES": "codex_oauth",
        "CEO_CODEX_MODEL": "gpt-6-luna", "CEO_CODEX_MODEL_REASONING_EFFORT": "high",
        "PYTHONDONTWRITEBYTECODE": "1"})
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
    from app.action_storage import CANDIDATE_REVIEW_SQL, REVIEW_CANDIDATE_SQL

    store = AutoReplyStore(db_path)
    reservation = _reserve_fixture_task_ids(db_path)
    key = "native-action-entity:" + str(uuid4())
    instruction = _acceptance_instruction()
    trigger = DingTalkMessage(open_conversation_id=key, open_message_id=key + ":trigger",
        conversation_title="Action Entity native copy", single_chat=False, sender_name="Derek",
        create_time="2026-10-21T00:00:00Z", content=instruction)
    task = store.ensure_reply_task(conversation_id=key, conversation_title=trigger.conversation_title,
        single_chat=False, trigger_message_id=trigger.open_message_id, trigger_sender=trigger.sender_name,
        trigger_create_time=trigger.create_time, trigger_text=instruction, trigger_message_json=trigger.model_dump_json())
    assert task.id == reservation + 1 and task.status == "pending"
    artifacts = _task_file_root(store.path, task.id, task.execution_generation, create=True)
    source_file = artifacts / "case-input.json"
    fixture = _case_fixture(key)
    source_file.write_text(json.dumps(fixture, ensure_ascii=False))
    source_file_sha256 = _file_sha256(source_file)
    task = store.claim_reply_task(task.id)
    assert task and task.status == "processing"
    context = AgentTaskContext(task_id=task.id, channel=task.channel, conversation_id=key,
        conversation_title=task.conversation_title, single_chat=False,
        trigger_message_id=task.trigger_message_id, trigger_sender=task.trigger_sender,
        trigger_text=instruction, trigger_create_time=task.trigger_create_time,
        messages=(), materials=(MaterialReference("local_file", str(source_file), task.trigger_message_id,
            ('read_task_artifact(name="case-input.json")',)),), prior_receipts=(),
        consumer_prompt=instruction,
        skill_protocol_override="Use only this local artifact and the declared dingtalk action contract. No external source reads.",
        skill_protocol_source="explicit_custom")
    if case == "revision":
        source_file.write_text(json.dumps({**fixture, "records": fixture["records"][:2]}))
    config = load_runtime_config({"CEO_AGENT_RUNTIME_ROUTES": "codex_oauth", "CEO_CODEX_MODEL": "gpt-6-luna"})
    native = NativeExecutor(workspace, store.path)
    if case == "revision":
        def restore_changed_source():
            nonlocal source_file_sha256
            source_file.write_text(json.dumps(fixture, ensure_ascii=False))
            source_file_sha256 = _file_sha256(source_file)
        native.after_consumer = restore_changed_source
    provider = InMemoryProvider(store, key)
    driver = AgentOrchestrator(store=store,
        consumer=ConsumerAgentRunner(store=store, workspace=workspace, runtime_config=config, executor=native,
            reasoning_effort="high", skill_protocol_override=context.skill_protocol_override,
            owner="task6a2-native-consumer"),
        audit=AuditAgentRunner(store=store, workspace=workspace, runtime_config=config, executor=native,
            reasoning_effort="high", skill_protocol_override=context.skill_protocol_override,
            owner="task6a2-native-audit"),
        system_executor=SystemExecutor(store, native_dingtalk_message_handlers(store, provider), owner="task6a2-native-system"))
    worker = DingTalkAutoReplyWorker(store, object(), object(), agent_orchestrator=driver)
    worker._queued_task_prompt_context_messages = lambda *a, **kw: ([], [])
    worker._build_agent_task_context = lambda **kw: context
    worker._sync_processing_reactions = lambda: None
    worker._dismiss_problem_notification = lambda task: None
    worker._notify_problem_attempt = lambda *a, **kw: None
    interrupted = False
    if case == "settlement_recovery":
        provider.after_send = lambda: _install_settlement_interrupt(store, task.id)
        try:
            worker.process_claimed_reply_task(task)
        except sqlite3.IntegrityError as exc:
            assert "native copy settlement interrupted" in str(exc)
            interrupted = True
        assert interrupted and store.get_reply_task(task.id).status == "processing"
        assert len(provider.calls) == 1
        with store._connect() as db:
            db.execute("drop trigger native_copy_interrupt")
        store = AutoReplyStore(db_path)
        driver = AgentOrchestrator(store=store, consumer=object(), audit=object(), system_executor=None)
        worker = DingTalkAutoReplyWorker(store, object(), object(), agent_orchestrator=driver)
        worker._queued_task_prompt_context_messages = lambda *a, **kw: ([], [])
        worker._build_agent_task_context = lambda **kw: context
        worker._sync_processing_reactions = lambda: None
        worker._dismiss_problem_notification = lambda task: None
        worker._notify_problem_attempt = lambda *a, **kw: None
        task = store.get_reply_task(task.id)
    print(json.dumps({"event": "nativecase_start", "case": case, "task_id": task.id}),
          file=sys.stderr, flush=True)
    completed = worker.process_claimed_reply_task(task)
    current = store.get_reply_task(task.id)
    runs = store.list_agent_runs_for_task_generation(task.id, task.execution_generation)
    run_rows = [{"id": r.id, "role": r.role.value, "status": r.status,
        "revision": r.proposal_revision, "parent": r.parent_agent_run_id,
        "session_id": r.codex_session_id, "start": r.transcript_start_line, "end": r.transcript_end_line,
        "native_exists": bool(r.codex_session_id and find_codex_session_path(r.codex_session_id)),
        "tool_events": len(r.tool_events)} for r in runs]
    from app.action_storage import EXTERNAL_ACTION_RESULT_SQL, SENT_REPLY_SQL
    with store._connect() as db:
        receipt_rows = db.execute(
            f"select * from ({EXTERNAL_ACTION_RESULT_SQL}) where business_object_key=?",
            (task.business_object_key,)).fetchall()
        sent_rows = db.execute(
            f"select * from ({SENT_REPLY_SQL}) where conversation_id=? and trigger_message_id=?",
            (key, task.trigger_message_id)).fetchall()
        reviews = []
        for row in db.execute(
            f"select r.id,r.decision,c.id candidate_id,c.proposal_revision,c.candidate_json "
            f"from ({CANDIDATE_REVIEW_SQL}) r join ({REVIEW_CANDIDATE_SQL}) c on c.id=r.candidate_id "
            "where c.task_id=? order by r.id", (task.id,)):
            candidate = json.loads(row["candidate_json"])
            proposal = candidate.get("proposal") or {}
            reviews.append({"review_id": row["id"], "decision": row["decision"],
                "candidate_id": row["candidate_id"], "revision": row["proposal_revision"],
                "outcome": candidate.get("outcome"),
                "action_count": len(proposal.get("actions", [])),
                "operations": [item.get("operation") for item in proposal.get("actions", [])]})
    effect_count = len(receipt_rows)
    verification_count = len(provider.verifications)
    body = provider.calls[0]["text"] if provider.calls else ""
    from app.native_trajectory import find_codex_session_path
    for row in run_rows:
        path = find_codex_session_path(row["session_id"]) if row["session_id"] else None
        row["trajectory_path"] = str(path) if path else None
        row["trajectory_sha256"] = _file_sha256(path) if path and path.is_file() else None
    report = {"case": case, "status": current.status, "completed": completed, "interrupted": interrupted,
        "task_id": task.id, "source_sha256": before_hash, "current_copy_sha256_unchanged": _file_sha256(current_copy) == before_hash,
        "model": "gpt-6-luna", "reasoning_effort": "high", "route": "codex_oauth", "cli_version": "0.163.0",
        "native_runs": run_rows, "provider_substitute_sends": len(provider.calls),
        "provider_substitute_verifications": verification_count,
        "provider_receipt_rows_for_task": effect_count, "sent_rows_for_task": len(sent_rows),
        "revision_reviews": reviews, "source_file_sha256": source_file_sha256,
        "output_body_sha256": hashlib.sha256(body.encode()).hexdigest() if body else None,
        "trajectory_home": "~/.codex/sessions", "passed": False, "status_detail": "native_case_result_recorded"}
    (case_dir / "native-result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    _print_event({"event": "native_case_result_recorded", "case": case,
        "task_id": task.id, "substitute_sends": len(provider.calls),
        "provider_receipt_rows": effect_count, "sent_rows": len(sent_rows),
        "run_count": len(run_rows)})
    assert completed and current.status == "done", report
    assert {r["role"] for r in run_rows} == {"consumer", "audit"}, report
    assert all(r["native_exists"] and r["end"] > r["start"] for r in run_rows if r["status"] == "completed"), report
    assert len(provider.calls) == 1, report
    assert effect_count == len(sent_rows) == 1, report
    assert verification_count >= 1, report
    assert sent_rows[0]["reply_text"] == body, report
    audit_run_id = _audit_run_id(runs)
    assert sent_rows[0]["agent_run_id"] == audit_run_id, report
    assert sent_rows[0]["external_action_key"] == receipt_rows[0]["external_action_key"], report
    provider_result = json.loads(receipt_rows[0]["provider_result_json"])
    assert provider_result == provider.verifications[0]["result"], report
    assert "TASK6A2_" in body and "100" in body and "3" in body, body
    if case == "revision":
        assert reviews[0]["decision"] in {"return", "reject"} and reviews[-1]["decision"] == "approve", report
        assert max(r["revision"] for r in run_rows) >= 1, report
    report["roundtrip"] = _roundtrip(
        db_path, case_dir, old_root, uuid4().hex[:12], key, task.trigger_message_id, body,
        audit_run_id)
    report["passed"] = True
    report["status_detail"] = "native_lifecycle_and_current_action_roundtrip_passed"
    (case_dir / "native-result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    _print_event({"event": "native_case_pass", "case": case, "task_id": task.id,
                  "sends": len(provider.calls), "runs": len(run_rows)})
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--current-copy", type=Path, required=True)
    parser.add_argument("--owned-temp-root", type=Path, required=True)
    parser.add_argument("--old-root", type=Path, required=True)
    parser.add_argument("--case", choices=("approve", "revision", "settlement_recovery"), default="approve")
    args = parser.parse_args()
    report = run(args.current_copy, args.owned_temp_root, args.old_root, args.case)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
