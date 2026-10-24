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
import subprocess
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.action_storage import (  # noqa: E402
    SENT_REPLY_SQL,
    REPLY_ATTEMPT_COMMON_SQL,
)
from app.store import (  # noqa: E402
    CANDIDATE_REVIEW_SQL,
    EXTERNAL_ACTION_RESULT_SQL,
    REVIEW_CANDIDATE_SQL,
)


def _portable_value(value):
    """Encode converter metadata as strict JSON without losing SQLite facts."""
    if type(value) is tuple:
        return {"$task5_type": "tuple", "items": [_portable_value(item) for item in value]}
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"$task5_type": "bytes", "hex": bytes(value).hex()}
    if type(value) is float:
        return {"$task5_type": "float", "hex": value.hex()}
    if type(value) is dict:
        if any(type(key) is not str for key in value):
            raise TypeError("portable converter metadata requires string object keys")
        return {key: _portable_value(item) for key, item in value.items()}
    if type(value) is list:
        return [_portable_value(item) for item in value]
    if value is None or type(value) in (str, int, bool):
        return value
    raise TypeError(f"unsupported portable converter metadata value: {type(value).__name__}")


def _restore_portable_value(value):
    if type(value) is list:
        return [_restore_portable_value(item) for item in value]
    if type(value) is dict:
        tag = value.get("$task5_type")
        if tag == "tuple" and set(value) == {"$task5_type", "items"}:
            return tuple(_restore_portable_value(item) for item in value["items"])
        if tag == "bytes" and set(value) == {"$task5_type", "hex"}:
            return bytes.fromhex(value["hex"])
        if tag == "float" and set(value) == {"$task5_type", "hex"}:
            return float.fromhex(value["hex"])
        if tag is not None:
            raise ValueError("unknown or malformed Task 5 metadata type tag")
        return {key: _restore_portable_value(item) for key, item in value.items()}
    if value is None or type(value) in (str, int, bool):
        return value
    raise TypeError(f"unsupported Task 5 JSON value: {type(value).__name__}")


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
        self.inject_capacity_after_session_once = False
        self.commands = []

    def __call__(self, command, **kwargs):
        from app.process_runner import run_process_with_idle_timeout
        from app.leak_check import redact_credentials

        scoped = restricted_command(command, workspace=self.workspace, db_path=self.db_path)
        resume_index = scoped.index("resume") if "resume" in scoped else -1
        self.commands.append({
            "mode": "resume" if resume_index >= 0 else "exec",
            "session_id": scoped[-2] if resume_index >= 0 and len(scoped) > 2 else "",
        })
        if self.fail_first_start and not self.calls:
            scoped.insert(scoped.index("exec") + 1, "--copy-acceptance-invalid-start")
        role = "audit" if 'sandbox_mode="read-only"' in scoped else "consumer"
        print(json.dumps({"event": "native_start", "role": role}), flush=True)
        # Keep native authentication/home; no credential files or transcript copies.
        original_progress = kwargs.get("on_stdout_line")
        observed_lines = []
        observed_session = False

        def progress(line):
            nonlocal observed_session
            observed_lines.append(line)
            if original_progress is not None:
                original_progress(line)
            if self.inject_capacity_after_session_once:
                try:
                    event = json.loads(line)
                except (TypeError, ValueError):
                    event = {}
                if isinstance(event, dict) and (event.get("session_id") or event.get("thread_id")):
                    observed_session = True
        kwargs["on_stdout_line"] = progress
        kwargs["total_timeout_seconds"] = min(kwargs["total_timeout_seconds"], 300)
        kwargs["idle_timeout_seconds"] = min(kwargs["idle_timeout_seconds"], 120)
        result = run_process_with_idle_timeout(scoped, **kwargs)
        if self.inject_capacity_after_session_once and observed_session and result.returncode == 0:
            # Let the real CLI finish and persist its rollout before injecting
            # the capacity classifier input. This makes the subsequent native
            # resume exercise a genuinely resumable observed session.
            from app.process_runner import ProcessRunResult
            self.inject_capacity_after_session_once = False
            result = ProcessRunResult(
                1, result.stdout, "selected model is at capacity",
            )
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
    def __init__(self, store, conversation, *, ledger_path=None):
        self.store, self.conversation, self.calls = store, conversation, []
        self.ledger_path = Path(ledger_path) if ledger_path is not None else None
        self.verifications = []
        self.verification_results = []
        self.after_send = None

    def send_message(self, conversation_id, text, **target):
        assert conversation_id == self.conversation
        with self.store._connect() as db:
            decision = db.execute(f"select decision from ({CANDIDATE_REVIEW_SQL}) order by id desc limit 1").fetchone()[0]
        assert decision == "approve"
        if self.ledger_path is not None:
            ledger = json.loads(self.ledger_path.read_text()) if self.ledger_path.exists() else {"accepted_effects": []}
            if ledger["accepted_effects"]:
                raise AssertionError("durable fake provider rejects a duplicate accepted effect")
            effect = {"conversation_id": conversation_id, "text": text, "target": target,
                      "message_id": "native-copy-provider-receipt"}
            ledger["accepted_effects"].append(effect)
            temporary = self.ledger_path.with_suffix(self.ledger_path.suffix + ".tmp")
            temporary.write_text(json.dumps(ledger, ensure_ascii=False, sort_keys=True))
            with temporary.open("rb") as stream:
                os.fsync(stream.fileno())
            os.replace(temporary, self.ledger_path)
            directory_fd = os.open(self.ledger_path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        self.calls.append({"conversation_id": conversation_id, "text": text, "target": target})
        if self.after_send is not None:
            callback, self.after_send = self.after_send, None
            callback()
        return {"success": True, "result": {"openMessageId": "native-copy-provider-receipt", "openConversationId": conversation_id}}

    def verify_message_send_result(self, result):
        self.verifications.append(result)
        assert result["result"]["openMessageId"] == "native-copy-provider-receipt"
        verification = {"state": "sent", "message_id": "native-copy-provider-receipt"}
        self.verification_results.append(verification)
        return verification

    def reconcile_message_send(self, *args, **kwargs):
        return None



def _install_settlement_interrupt(store, task_id):
    with store._connect() as db:
        db.execute(f"create trigger native_copy_interrupt before update of status on tasks "
                   f"when old.id={task_id} and new.status='done' "
                   "begin select raise(abort,'native copy settlement interrupted'); end")

def original_rows_unchanged(source, candidate, *, natural_key_overrides=None,
                            ignored_value_columns=None,
                            allow_store_schema_marker_timestamp=False):
    # The target Email trigger names main explicitly, so SQLite cannot load a
    # second target copy as an ATTACHed schema. Compare rows on two read-only
    # connections instead; rowid (or WITHOUT ROWID primary key) bounds memory.
    with sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True) as baseline, \
            sqlite3.connect(candidate.resolve().as_uri() + "?mode=ro", uri=True) as db:
        def object_ddl(connection):
            return {(row[0], row[1], row[2]): row[3] for row in connection.execute(
                "select type,name,tbl_name,sql from sqlite_master "
                "where type in ('table','index','trigger') and name not like 'sqlite_%'")}

        source_ddl = object_ddl(baseline)
        candidate_ddl = object_ddl(db)
        if source_ddl != candidate_ddl:
            source_keys, candidate_keys = set(source_ddl), set(candidate_ddl)
            mismatch = sorted(source_keys ^ candidate_keys)
            mismatch.extend(sorted(key for key in source_keys & candidate_keys
                                   if source_ddl[key] != candidate_ddl[key]))
            raise AssertionError({"schema_object_mismatch": mismatch[:20],
                                  "mismatch_count": len(mismatch)})
        virtual = [r[0] for r in baseline.execute("select name from sqlite_master where type='table' and sql like 'CREATE VIRTUAL TABLE%'")]
        tables = [r[0] for r in baseline.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%'")
                  if not any(r[0] == v or r[0].startswith(v + "_") for v in virtual)]
        changed = {}
        for table in tables:
            quoted = '"' + table.replace('"', '""') + '"'
            # Compare persisted values only. Expanding `*` also evaluates
            # virtual generated projections, many of which parse the same
            # large JSON body repeatedly. hidden=0 are ordinary stored
            # columns; hidden=3 are STORED generated columns and remain part
            # of the durable row contract.
            stored = [row[1] for row in baseline.execute(
                f"pragma table_xinfo({quoted})") if row[6] in (0, 3)]
            if not stored:
                continue
            stored_projection = ",".join('"' + name.replace('"', '""') + '"'
                                         for name in stored)
            pk = [row[1] for row in sorted(
                baseline.execute(f"pragma table_info({quoted})"), key=lambda row: row[5]
            ) if row[5]]
            # Some acceptance comparisons use two independently produced
            # copies of the same current schema. In those cases a converter-
            # generated surrogate ID can differ while the declared unique
            # business key and every business value remain equal. Callers must
            # opt in per table; ordinary lifecycle checks remain PK-exact.
            key_columns = (natural_key_overrides or {}).get(table, pk or ["rowid"])
            keys = ",".join('"' + name.replace('"', '""') + '"' for name in key_columns)
            order = keys
            projection = keys + "," + stored_projection
            key_indexes = tuple(range(len(key_columns)))
            source_rows = baseline.execute(f"select {projection} from {quoted} order by {order}")
            candidate_rows = iter(db.execute(f"select {projection} from {quoted} order by {order}"))
            current = next(candidate_rows, None)
            count = 0
            for expected in source_rows:
                expected_key = tuple(expected[i] for i in key_indexes)
                while current is not None and tuple(current[i] for i in key_indexes) < expected_key:
                    current = next(candidate_rows, None)
                if current is None or tuple(current[i] for i in key_indexes) != expected_key:
                    count += 1
                    continue
                expected_values = tuple(expected)
                current_values = tuple(current)
                ignored = set()
                if table in (ignored_value_columns or {}):
                    # Callers may omit a converter-generated surrogate only
                    # while independently comparing on an explicit natural
                    # key, after validating the association identity itself.
                    ignored.update(len(key_columns) + stored.index(column)
                                   for column in ignored_value_columns[table]
                                   if column in stored)
                if (allow_store_schema_marker_timestamp and table == "state"
                        and expected_values != current_values):
                    # Publishing the same schema release refreshes only this
                    # service-state marker's bookkeeping timestamp. Its typed
                    # key and published version must still be identical.
                    payload_index = len(key_columns) + stored.index("payload_json")
                    type_index = len(key_columns) + stored.index("record_type")
                    legacy_index = len(key_columns) + stored.index("legacy_key")
                    if (expected_values[type_index] == current_values[type_index] == "service_state"
                            and expected_values[legacy_index] == current_values[legacy_index] == "120"):
                        try:
                            old_payload = json.loads(expected_values[payload_index])
                            new_payload = json.loads(current_values[payload_index])
                        except (TypeError, json.JSONDecodeError):
                            old_payload = new_payload = None
                        if (isinstance(old_payload, dict) and isinstance(new_payload, dict)
                                and old_payload.get("key") == new_payload.get("key") == "store_schema_version"
                                and old_payload.get("value") == new_payload.get("value")
                                and old_payload.get("updated_at") != new_payload.get("updated_at")):
                            expected_values = list(expected_values)
                            current_values = list(current_values)
                            expected_values[payload_index] = current_values[payload_index]
                            expected_values = tuple(expected_values)
                if any(expected_values[index] != current_values[index]
                       for index in range(len(expected_values)) if index not in ignored):
                    count += 1
                current = next(candidate_rows, None)
            if count:
                changed[table] = count
        fks = {tuple(r) for r in db.execute("pragma foreign_key_check")}
        original_fks = {tuple(r) for r in baseline.execute("pragma foreign_key_check")}
    assert not changed, changed
    assert fks == original_fks
    return len(tables)


def _backup_database(source, destination):
    assert not destination.exists()
    with sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True) as original, \
            sqlite3.connect(destination) as copy:
        original.backup(copy)


def _file_sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _publish_email_pair(repo, candidate, *, recovery_source_path=None):
    from app.storage_payload import ACTION_RECORD_TYPES
    from app.state_definitions import state_projection
    from app.email_store import EMAIL_SCHEMA_VERSION, EmailStore
    from app.store import AutoReplyStore, STORE_SCHEMA_VERSION
    from app.store_schema_release import prepare_database_schema_release

    release = prepare_database_schema_release(repo, candidate, recovery_source_path=recovery_source_path)
    if release is not None:
        release.publish()
    AutoReplyStore(candidate)
    EmailStore(candidate)
    with sqlite3.connect(candidate) as db:
        store_version = db.execute(
            f"select value from ({state_projection('service_state')}) where key='store_schema_version'"
        ).fetchone()
        email_version = db.execute("select max(version) from email_schema_migrations").fetchone()
        tables = {row[0] for row in db.execute(
            "select name from sqlite_schema where type='table' and name not glob 'sqlite_*'"
        )}
        assert store_version == (STORE_SCHEMA_VERSION,)
        assert email_version == (EMAIL_SCHEMA_VERSION,)
        assert tables == {"tasks", "runs", "actions", "business", "emails", "provenance",
                          "configs", "provider_state", "schedules", "state", "email_schema_migrations"}
    return {"store": store_version[0], "email": email_version[0],
            "action_types": len(ACTION_RECORD_TYPES.names), "core_tables": 10,
            "source_body_table_present": False}


def _exercise_current_email(candidate):
    """Use current public Email APIs with one isolated in-memory provider result."""
    from datetime import datetime, timezone

    from app.email_classifier_contracts import (
        EmailAction, EmailCategory, EmailClassification, EmailClassificationStatus,
        build_email_action_plan,
    )
    from app.email_store import EmailStore, EMAIL_DIRECT_ACTION_LOCATOR_SQL
    from app.store import AutoReplyStore

    email = EmailStore(candidate)
    store = AutoReplyStore(candidate)
    unique = uuid4().hex
    account_id = "native-copy-" + unique
    classification_id = uuid4().int & ((1 << 63) - 1) or 1
    message_id = f"<native-copy-{unique}@example.test>"
    identity = f"{account_id}:message-id:{message_id}"
    now = datetime(2026, 10, 14, tzinfo=timezone.utc)
    plan = build_email_action_plan(
        classification_id=classification_id, account_id=account_id,
        category=EmailCategory.WORK, classification_source="model",
        confidence=0.98, model_id="native-copy:deterministic",
        config_version="native-copy:v1", actions=(EmailAction.MARK_READ,),
        action_parameters={EmailAction.MARK_READ: {}}, created_at=now,
    )
    classification = EmailClassification.model_validate({
        "classification_id": classification_id, "stable_message_identity": identity,
        "provider_locator": {"account_id": account_id, "folder": "INBOX",
                             "uidvalidity": 42, "uid": 41, "rfc_message_id": message_id,
                             "thread_id": "native-copy-thread-" + unique},
        "category": EmailCategory.WORK, "confidence": 0.98, "margin": 0.42,
        "probabilities": {"work": 0.98}, "model_id": "native-copy:deterministic",
        "config_version": "native-copy:v1", "status": EmailClassificationStatus.PROCESSED,
        "classification_source": "model", "action_plan": plan,
    })
    email.upsert_classification(
        classification, sender="sender@example.test", subject="Native copy source",
        model_text="Durable source text", received_at=now.isoformat(),
    )
    action_ids = email.direct_action_ids_for_plan(plan.action_plan_id)
    assert len(action_ids) == 1
    action_id = action_ids[0]
    context = email.list_email_context_thread(
        account_id=account_id, stable_message_identity=identity,
    )
    assert len(context) == 1 and context[0]["stable_message_identity"] == identity
    assert email.list_email_context_receipts(
        account_id=account_id, stable_message_identity=identity,
    ) == []
    claimed = email.claim_direct_action(action_id=action_id, claimed_at=now.isoformat())
    assert claimed is not None and claimed.action_id == action_id
    result = email.complete_direct_action_attempt(
        claimed, status="done", provider_operation="mark_read",
        provider_target=identity, provider_result_id="in-memory-" + unique,
        error="", finished_at="2026-10-14T00:00:01+00:00",
    )
    attempts = email.list_action_attempts(action_id)
    assert result["status"] == "done" and len(attempts) == 1
    assert attempts[0]["provider_result_id"] == "in-memory-" + unique
    with sqlite3.connect(candidate) as db:
        action_owner_id = db.execute(
            f"select action_id from ({EMAIL_DIRECT_ACTION_LOCATOR_SQL}) where action_id=?",
            (action_id,),
        ).fetchone()[0]
    history = [item for item in store.list_operation_logs(source_tables=("actions",))
               if item.source_id == action_owner_id]
    assert len(history) == 1 and history[0].id == f"action:{action_owner_id}"
    assert history[0].status == "done"
    reopened = EmailStore(candidate)
    assert reopened.list_action_attempts(action_id) == attempts
    assert len(reopened.list_email_context_thread(
        account_id=account_id, stable_message_identity=identity,
    )) == 1
    return {"context_messages": len(context), "prior_context_receipts": 0,
            "direct_attempts": len(attempts), "direct_status": "done",
            "provider_result_persisted": True, "history_locator_valid": True}


def _reserve_fixture_task_ids(candidate):
    """Reserve candidate-only Task identities before native fixture allocation."""
    with sqlite3.connect(candidate) as db:
        db.execute("begin immediate")
        current = db.execute(
            "select seq from sqlite_sequence where name='tasks'"
        ).fetchone()
        maximum = db.execute("select coalesce(max(id), 0) from tasks").fetchone()[0]
        prior_maximum = max(maximum, current[0] if current else 0)
        assert prior_maximum < 2**53 - 2**20, \
            "native copy Task allocator is no longer exactly representable"
        # Keep fixture identities above ordinary tasks and exactly representable
        # by native tools/JSON consumers using JavaScript numbers.
        reservation = max(2**52, prior_maximum + 1) + (uuid4().int & ((1 << 20) - 1))
        if current is None:
            db.execute("insert into sqlite_sequence(name,seq) values('tasks',?)", (reservation,))
        else:
            db.execute("update sqlite_sequence set seq=? where name='tasks'", (reservation,))
    return reservation


def run(root, case="approve", *, current_target=None, current_baseline=None):
    root = root.resolve()
    assert root.name.startswith("ceo-native-copy.")
    source = root / "source.sqlite3"
    if current_target is None:
        metadata = json.loads((root / "source-meta.json").read_text())
        assert _file_sha256(source) == metadata["sha256"]
        candidate_path = root / "candidate.sqlite3"
        assert not candidate_path.exists()
        shutil.copyfile(source, candidate_path)
        published_pair = _publish_email_pair(ROOT, candidate_path, recovery_source_path=source)
        target_baseline = root / "pre-native-target.sqlite3"
        _backup_database(candidate_path, target_baseline)
        target_baseline_hash = _file_sha256(target_baseline)
        assert _file_sha256(source) == metadata["sha256"]
    else:
        candidate_path = Path(current_target).resolve()
        assert candidate_path.is_file()
        metadata = {"sha256": _file_sha256(candidate_path), "schema": "current-task-run-copy"}
        published_pair = {"store": "already-published", "source_body_table_present": False}
        target_baseline = Path(current_baseline).resolve() if current_baseline else None
        assert target_baseline is None or target_baseline.is_file()
        target_baseline_hash = _file_sha256(target_baseline) if target_baseline else ""
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
    task_id_reservation = _reserve_fixture_task_ids(candidate_path)
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
    assert task.id == task_id_reservation + 1
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
    os.environ["CEO_CODEX_MODEL"] = "gpt-6-luna"
    os.environ["CEO_CODEX_MODEL_REASONING_EFFORT"] = "high"
    config = load_runtime_config({"CEO_AGENT_RUNTIME_ROUTES": "codex_oauth", "CEO_CODEX_MODEL": "gpt-6-luna",
                                 "CEO_CODEX_MODEL_REASONING_EFFORT": "high"})
    native = NativeExecutor(workspace, store.path)
    if case == "revision":
        native.after_consumer = lambda: source_file.write_text(json.dumps(fixture))
    if case == "technical_retry":
        native.fail_first_start = True
    if case == "capacity_retry_resume":
        native.inject_capacity_after_session_once = True
    ledger_path = root / "durable-fake-provider-ledger.json" if case == "settlement_recovery" else None
    provider = InMemoryProvider(store, key, ledger_path=ledger_path)
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
        # Install only after native Consumer/Audit source reads have finished.
        # Current cold constructors correctly reject an unknown test trigger.
        provider.after_send = lambda: _install_settlement_interrupt(store, task.id)
        try:
            worker.process_claimed_reply_task(task)
        except sqlite3.IntegrityError as exc:
            assert "native copy settlement interrupted" in str(exc)
            interrupted = True
        assert interrupted and store.get_reply_task(task.id).status == "processing"
        assert len(provider.calls) == 1
        with store._connect() as db:
            assert db.execute(f"select count(*) from ({EXTERNAL_ACTION_RESULT_SQL}) where business_object_key=?", (task.business_object_key,)).fetchone()[0] == 1
            assert db.execute(f"select count(*) from ({REPLY_ATTEMPT_COMMON_SQL}) where conversation_id=?", (key,)).fetchone()[0] == 0
            db.execute("drop trigger native_copy_interrupt")
        recovery_output = root / "settlement-recovery-child.json"
        child = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--settlement-recovery-child",
             "--current-target", str(candidate_path), "--fake-ledger", str(ledger_path),
             "--task-id", str(task.id), "--recovery-output", str(recovery_output)],
            cwd=ROOT, text=True, capture_output=True, timeout=300,
        )
        if child.returncode:
            raise RuntimeError(child.stderr or child.stdout or "independent settlement recovery failed")
        settlement_recovery = json.loads(recovery_output.read_text())
        if not settlement_recovery.get("completed") or settlement_recovery.get("provider_send_calls") != 0:
            raise AssertionError({"independent_recovery": settlement_recovery, "child": child.stdout})
        ledger = json.loads(ledger_path.read_text())
        if len(ledger.get("accepted_effects", [])) != 1:
            raise AssertionError({"durable_fake_provider_ledger": ledger})
        store = AutoReplyStore(candidate_path)
        task = store.get_reply_task(task.id)
        completed = True
    else:
        settlement_recovery = None
        completed = worker.process_claimed_reply_task(task)
    runs = store.list_agent_runs_for_task_generation(task.id, task.execution_generation)
    current = store.get_reply_task(task.id)
    from app.task_artifacts import cleanup_terminal_task_artifacts
    cleanup_terminal_task_artifacts(store, task.id)
    report = {"case": case, "interrupted": interrupted, "settlement_recovery": settlement_recovery,
        "durable_fake_provider_ledger": str(ledger_path) if ledger_path else None, "source": metadata,
        "published_pair": published_pair, "native_baseline_phase": "post_email_publication",
        "task_id": task.id, "task_id_allocator_reservation": task_id_reservation, "conversation_id": key, "status": current.status,
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
                          "session_mode": getattr(a, "session_mode", ""),
                          "source_session_id": getattr(a, "source_session_id", ""),
                          "failure_class": getattr(a, "failure_class", ""),
                          "failure_code": getattr(a, "failure_code", ""),
                          "route_name": getattr(a, "route_name", ""),
                          "model": getattr(a, "model", ""),
                          "start": a.transcript_start, "end": a.transcript_end} for a in attempts]})
    if case == "capacity_retry_resume":
        for run_report in report["runs"]:
            for attempt in run_report["attempts"]:
                attempt["status"] = getattr(attempt["status"], "value", attempt["status"])
        capacity = [
            (run_report, attempt)
            for run_report in report["runs"] for attempt in run_report["attempts"]
            if attempt["failure_class"] == "capacity"
            and attempt["status"] in {"failed", "superseded"}
        ]
        if len(capacity) != 1 or not capacity[0][1]["session_id"]:
            raise AssertionError({"expected_one_observed_capacity_attempt": capacity,
                                  "runs": report["runs"]})
        failed_run, failed_attempt = capacity[0]
        session_id = failed_attempt["session_id"]
        resumed = [
            attempt for attempt in failed_run["attempts"]
            if attempt["session_mode"] == "resume"
            and attempt["source_session_id"] == session_id
        ]
        resume_commands = [
            command for command in native.commands
            if command["mode"] == "resume" and command["session_id"] == session_id
        ]
        if not resumed or not resume_commands:
            raise AssertionError({"observed_session_id": session_id,
                                  "resumed_attempts": resumed,
                                  "native_commands": native.commands})
        report["capacity_retry_resume_contract"] = {
            "injected_classifier_input": "recognized capacity error after native CLI persisted an observed session",
            "run_id": failed_run["id"], "failed_attempt_id": failed_attempt["id"],
            "observed_session_id": session_id,
            "resumed_attempt_ids": [attempt["id"] for attempt in resumed],
            "same_run": True, "resume_command_exec_observed": True,
        }
    report["sanitized_native_commands"] = native.commands
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
        report["sent_replies"] = db.execute(f"select count(*) from ({SENT_REPLY_SQL}) where conversation_id=?", (key,)).fetchone()[0]
        report["business_attempts"] = db.execute(f"select count(*) from ({REPLY_ATTEMPT_COMMON_SQL}) where conversation_id=?", (key,)).fetchone()[0]
        assert "final_result_json" not in {r[1] for r in db.execute("pragma table_info(runs)")}
        assert db.execute("pragma quick_check").fetchall()[0][0] == "ok"
    assert report["receipts"] == report["sent_replies"] == expected_dispatch
    assert report["business_attempts"] == 1
    reopened = AutoReplyStore(store.path)
    recovered = AgentOrchestrator(store=reopened, consumer=object(), audit=object(), system_executor=None)
    assert recovered.persisted_terminal_result(reopened.get_reply_task(task.id)).status == ("no_action" if case == "no_action" else "executed")
    report["reopen_no_new_invocation"] = len(native.calls)
    report["email_direct"] = _exercise_current_email(candidate_path)
    if target_baseline is not None:
        report["original_tables_unchanged"] = original_rows_unchanged(target_baseline, candidate_path)
        assert _file_sha256(target_baseline) == target_baseline_hash
    else:
        report["original_tables_unchanged"] = "not compared; current-target caller must provide post-forward baseline"
    if target_baseline is None:
        report["pre_native_target_baseline"] = "not captured; caller owns unchanged-row acceptance"
    else:
        assert _file_sha256(source) == metadata["sha256"]
        assert _file_sha256(target_baseline) == target_baseline_hash
    report["passed"] = True
    (root / "native-result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({"event": "acceptance_pass", "task_id": task.id, "native_invocations": len(native.calls),
                      "provider_calls": len(provider.calls), "original_tables_unchanged": report["original_tables_unchanged"]}), flush=True)
    return report


def _settlement_recovery_child(target: Path, ledger_path: Path, task_id: int, output: Path) -> dict:
    from app.agent_orchestrator import AgentOrchestrator
    from app.store import AutoReplyStore
    from app.system_action_handlers import native_dingtalk_message_handlers
    from app.system_executor import SystemExecutor
    from app.worker import DingTalkAutoReplyWorker

    store = AutoReplyStore(target)
    task = store.get_reply_task(task_id)
    if task is None or task.status != "processing":
        raise AssertionError("independent recovery did not find the interrupted processing Task")
    provider = InMemoryProvider(store, task.conversation_id, ledger_path=ledger_path)
    orchestrator = AgentOrchestrator(
        store=store, consumer=object(), audit=object(),
        system_executor=SystemExecutor(store, native_dingtalk_message_handlers(store, provider),
                                       owner="native-copy-system-recovery"),
    )
    worker = DingTalkAutoReplyWorker(store, object(), object(), agent_orchestrator=orchestrator)
    worker._queued_task_prompt_context_messages = lambda *a, **kw: ([], [])
    worker._build_agent_task_context = lambda **kw: None
    worker._sync_processing_reactions = lambda: None
    worker._dismiss_problem_notification = lambda task: None
    worker._notify_problem_attempt = lambda *a, **kw: None
    completed = worker.process_claimed_reply_task(task)
    recovered = store.get_reply_task(task_id)
    ledger = json.loads(ledger_path.read_text())
    accepted = ledger.get("accepted_effects", [])
    if len(accepted) != 1 or provider.calls:
        raise AssertionError("independent recovery attempted a duplicate fake-provider send")
    result = {"completed": completed, "task_status": recovered.status,
              "provider_send_calls": len(provider.calls),
              "durable_accepted_effect_count": len(accepted),
              "receipt_message_id": accepted[0]["message_id"]}
    if not completed or recovered.status != "done":
        raise AssertionError(result)
    output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return result


def _audit_existing_capacity_task(target: Path, root: Path, task_id: int) -> dict:
    """Recheck persisted real CLI evidence after a report-only assertion repair."""
    from app.agent_orchestrator import AgentOrchestrator
    from app.store import AutoReplyStore
    from app.native_trajectory import find_codex_session_path

    store = AutoReplyStore(target)
    task = store.get_reply_task(task_id)
    if task is None or task.status != "done":
        raise AssertionError("persisted native capacity acceptance Task is not done")
    runs = store.list_agent_runs_for_task_generation(task.id, task.execution_generation)
    report = {"case": "capacity_retry_resume", "source": {"sha256": _file_sha256(target),
        "schema": "current-task-run-copy"}, "task_id": task.id, "status": task.status,
        "completed": True, "native_invocations": "reconstructed from durable RuntimeAttempt records",
        "provider_calls": "fake provider effect receipt persisted in the copied database",
        "runs": []}
    for run in runs:
        attempts = store.list_agent_runtime_attempts(run.id)
        path = find_codex_session_path(run.codex_session_id) if run.codex_session_id else None
        report["runs"].append({"id": run.id, "role": run.role.value, "status": run.status,
            "revision": run.proposal_revision, "parent_id": run.parent_agent_run_id,
            "session_id": run.codex_session_id, "start": run.transcript_start_line,
            "end": run.transcript_end_line, "native_exists": bool(path and path.is_file()),
            "tool_events": len(run.tool_events), "error": run.structured_error_json,
            "attempts": [{"id": attempt.id, "status": getattr(attempt.status, "value", attempt.status),
                "session_id": attempt.session_id, "session_mode": getattr(attempt.session_mode, "value", attempt.session_mode),
                "source_session_id": attempt.source_session_id, "failure_class": attempt.failure_class,
                "failure_code": attempt.failure_code, "route_name": attempt.route_name,
                "model": attempt.model, "start": attempt.transcript_start, "end": attempt.transcript_end}
                for attempt in attempts]})
    consumer = [row for row in report["runs"] if row["role"] == "consumer" and row["status"] == "completed"]
    audit = [row for row in report["runs"] if row["role"] == "audit" and row["status"] == "completed"]
    capacity = [(run, attempt) for run in report["runs"] for attempt in run["attempts"]
                if attempt["failure_class"] == "capacity"
                and attempt["status"] in {"failed", "superseded"}]
    if len(consumer) != 1 or len(audit) != 1 or len(capacity) != 1:
        raise AssertionError({"consumer": consumer, "audit": audit, "capacity": capacity})
    capacity_run, capacity_attempt = capacity[0]
    resumed = [attempt for attempt in capacity_run["attempts"]
               if attempt["session_mode"] == "resume"
               and attempt["source_session_id"] == capacity_attempt["session_id"]
               and attempt["status"] == "completed"]
    if (not resumed or not capacity_attempt["session_id"]
            or consumer[0]["id"] != capacity_run["id"]
            or not all(item["native_exists"] and item["end"] > item["start"] for item in consumer + audit)):
        raise AssertionError({"capacity_run": capacity_run, "resumed": resumed,
                              "consumer": consumer, "audit": audit})
    report["capacity_retry_resume_contract"] = {
        "injected_classifier_input": "synthetic capacity classification after the actual native CLI persisted its session",
        "run_id": capacity_run["id"], "failed_attempt_id": capacity_attempt["id"],
        "observed_session_id": capacity_attempt["session_id"],
        "resumed_attempt_ids": [attempt["id"] for attempt in resumed],
        "same_run": True, "resume_attempt_completed": True,
        "report_source": "re-audit of persisted native CLI transcript/RuntimeAttempt evidence; no CLI rerun",
    }
    with store._connect() as db:
        report["receipts"] = db.execute(
            f"select count(*) from ({EXTERNAL_ACTION_RESULT_SQL}) where business_object_key=?",
            (task.business_object_key,),).fetchone()[0]
        report["sent_replies"] = db.execute(
            f"select count(*) from ({SENT_REPLY_SQL}) where conversation_id=?",
            (task.conversation_id,),).fetchone()[0]
        report["business_attempts"] = db.execute(
            f"select count(*) from ({REPLY_ATTEMPT_COMMON_SQL}) where conversation_id=?",
            (task.conversation_id,),).fetchone()[0]
        report["reviews"] = [dict(row) for row in db.execute(
            f"select r.id,r.decision,c.proposal_revision,r.candidate_digest "
            f"from ({CANDIDATE_REVIEW_SQL}) r join ({REVIEW_CANDIDATE_SQL}) c on c.id=r.candidate_id "
            "where c.task_id=? and c.execution_generation=? order by r.id",
            (task.id, task.execution_generation))]
        if db.execute("pragma quick_check").fetchone()[0] != "ok":
            raise AssertionError("current target quick_check failed after native acceptance")
    if report["receipts"] != report["sent_replies"] or report["receipts"] != 1 or report["business_attempts"] != 1:
        raise AssertionError(report)
    if not report["reviews"] or report["reviews"][-1]["decision"] != "approve":
        raise AssertionError(report["reviews"])
    terminal = AgentOrchestrator(store=store, consumer=object(), audit=object(), system_executor=None)
    if terminal.persisted_terminal_result(task).status != "executed":
        raise AssertionError("terminal accepted action did not reopen without another invocation")
    report["email_direct"] = _exercise_current_email(target)
    report["original_tables_unchanged"] = "not compared; no separate post-forward target baseline was retained"
    report["passed"] = True
    result_path = root / "native-result.json"
    result_path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owned-temp-root", type=Path)
    parser.add_argument("--case", choices=("approve", "revision", "no_action", "technical_retry", "capacity_retry_resume", "settlement_recovery"), default="approve")
    parser.add_argument("--current-target", type=Path)
    parser.add_argument("--current-baseline", type=Path)
    parser.add_argument("--settlement-recovery-child", action="store_true")
    parser.add_argument("--fake-ledger", type=Path)
    parser.add_argument("--task-id", type=int)
    parser.add_argument("--recovery-output", type=Path)
    parser.add_argument("--audit-existing-capacity-task-id", type=int)
    args = parser.parse_args()
    from tests.support.memory_limit import MemoryLimit
    MemoryLimit(3 * 1024**3).start()
    if args.settlement_recovery_child:
        if not all((args.current_target, args.fake_ledger, args.task_id, args.recovery_output)):
            parser.error("settlement recovery child requires target, fake ledger, task id and output")
        _settlement_recovery_child(args.current_target, args.fake_ledger,
                                   args.task_id, args.recovery_output)
        return
    if args.owned_temp_root is None:
        parser.error("--owned-temp-root is required outside settlement recovery child mode")
    if args.audit_existing_capacity_task_id is not None:
        if args.current_target is None:
            parser.error("--current-target is required for existing capacity evidence audit")
        print(json.dumps(_audit_existing_capacity_task(
            args.current_target, args.owned_temp_root, args.audit_existing_capacity_task_id),
            ensure_ascii=False, indent=2))
        return
    run(args.owned_temp_root, args.case, current_target=args.current_target,
        current_baseline=args.current_baseline)


if __name__ == "__main__":
    main()
