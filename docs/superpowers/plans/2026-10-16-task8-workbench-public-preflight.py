#!/usr/bin/env python3
"""Bounded real-public-method comparison, synthetic fresh source only.

Run with the repository's fixed Python; --output writes concise metadata only.
Owned temporary code trees/databases are deleted even on failure. Target fixture
replaces EMPTY Tasks only; this is deliberately not a migration/constructor test.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from uuid import UUID

CLOCK = "2040-01-01 00:00:00"
LATER = "2040-01-01 00:00:11"
PYTHON = Path("/Users/derek/Projects/ceo-agent-service/.worktrees/task-first-tasks/.venv/bin/python")
PREFIX = "2026-10-16-task8-"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def encoded(value):
    if hasattr(value, "model_dump"):
        return encoded(value.model_dump(mode="json"))
    if isinstance(value, bytes):
        return {"blob_hex": value.hex()}
    if isinstance(value, dict):
        return {str(k): encoded(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [encoded(v) for v in value]
    return value


def worker(args):
    sys.path.insert(0, str(args.code))
    import app.workbench.store as wb
    from app.store import AutoReplyStore

    if args.worker == "bootstrap":
        store = wb.WorkbenchStore(args.db)
        with store._connect() as db:
            assert db.execute("select count(*) from tasks").fetchone()[0] == 0
            result = {
                "tasks": 0,
                "sqlite_schema_table_entries": db.execute("select count(*) from sqlite_schema where type='table'").fetchone()[0],
                "tasks_ddl_sha256": hashlib.sha256(db.execute("select sql from sqlite_schema where name='tasks'").fetchone()[0].encode()).hexdigest(),
                "fk_findings": [list(r) for r in db.execute("pragma foreign_key_check")],
            }
        args.result.write_text(json.dumps(result))
        return
    if args.worker == "prepare":
        from app.task20_definitions import create_task20_schema, initialize_task8_sequence
        with sqlite3.connect(args.db) as db:
            assert db.execute("select count(*) from tasks").fetchone()[0] == 0
            db.execute("pragma foreign_keys=off")
            db.execute("begin immediate")
            db.execute("drop table tasks")
            create_task20_schema(db)
            initialize_task8_sequence(db, 0)
        args.result.write_text(json.dumps({"empty_tasks_replaced": True, "published_water": 0}))
        return

    original_open = AutoReplyStore._open_connection

    def fixture_open(self):
        db = original_open(self)
        db.create_function("current_timestamp", 0, lambda: CLOCK)
        if args.target:
            from app.task8_storage import register_task8_functions
            register_task8_functions(db)
        return db

    AutoReplyStore._open_connection = fixture_open
    if args.target:
        # Only after template installation; class initialization and attachment
        # reconciliation still execute normally. Cold selection remains unproved.
        AutoReplyStore._ensure_initialized = lambda self: None
    counter = iter(range(1, 10000))
    wb.uuid4 = lambda: UUID(int=next(counter))
    store = wb.WorkbenchStore(args.db)
    if args.worker == "dependency":
        task = store.create_task(title="dependency control", runtime_kind="codex")
        turn = store.create_turn(task.id, user_text="populate live cache", client_request_id="dependency-control")
        claimed = store.claim_next_turn(owner="control", execution_run_id="control-run", now=CLOCK)
        assert claimed.id == turn.id
        try:
            event = store.append_event(turn.id, sequence=2, event_type="text_delta", payload={"text": "control-live-text"}, owner="control", now=CLOCK)
            outcome = {"return": encoded(event)}
        except sqlite3.OperationalError as exc:
            outcome = {"error": {"type": type(exc).__name__, "message": str(exc)}}
        # A failed cache cleanup must remain a visible method failure. Read raw
        # committed facts directly because retrying Workbench._connect would
        # encounter the still-populated cache again in this negative control.
        with original_open(store) as db:
            event_count = db.execute("select count(*) from runs where record_type='workbench_events'").fetchone()[0]
        from app.workbench import native_events
        cache_entries = len(native_events._live_turn_events.get((str(args.db.resolve()), turn.id), set()))
        args.result.write_text(json.dumps({"method": "WorkbenchStore.append_event", "input": {"sequence": 2, "event_type": "text_delta", "payload": {"text": "control-live-text"}, "owner": "control", "now": CLOCK}, "outcome": outcome, "committed_workbench_event_count": event_count, "live_cache_entries": cache_entries}))
        return
    records = []
    trace = []
    public_calls = set()

    def facts():
        with store._connect() as db:
            tables = {}
            for name, query in (("workbench_tasks", wb.WORKBENCH_TASK_SQL), ("workbench_turns", wb.WORKBENCH_TURN_SQL)):
                rows = db.execute(f"select * from ({query}) order by id").fetchall()
                tables[name] = [[(k, encoded(r[k]), type(r[k]).__name__) for k in r.keys()] for r in rows]
            tables["physical_tasks"] = [list(r) for r in db.execute("select id,record_type,legacy_key,parent_id from tasks order by id")]
            for name in ("runs", "workbench_confirmations", "workbench_artifacts", "workbench_attachments"):
                tables[name] = [encoded(dict(r)) for r in db.execute(f"select * from {name} order by id")]
            seq_name = "tasks:published" if args.target else "tasks"
            seq = db.execute("select seq from sqlite_sequence where name=?", (seq_name,)).fetchone()
            # Fresh source has no sequence row; target fixture initializes explicit zero.
            # Compare the declared effective allocation water, retain setup separately.
            tables["tasks_source_water"] = 0 if seq is None else seq[0]
            tables["fk_findings"] = [list(r) for r in db.execute("pragma foreign_key_check")]
            # Existing twelve-kind and auxiliary schemas remain intentionally native;
            # their task-target FK translation is outside this empty fixture.
            return tables

    expected_errors = {
        "blank_title", "blank_runtime", "request_conflict", "active_conflict",
        "archive_active", "wrong_renew_owner", "bad_sequence", "archived_new_turn",
        "bad_list_limit", "missing_rename", "old_owner_append",
        "confirmation_wrong_owner", "expired_renew",
    }

    def call(label, method, *pos, **kw):
        public_calls.add(method)
        trace.clear()
        # Preserve original connection setup, capture SQLite's actual expanded SQL.
        previous = AutoReplyStore._open_connection

        def traced_open(self):
            db = previous(self)
            db.set_trace_callback(trace.append)
            return db

        AutoReplyStore._open_connection = traced_open
        try:
            result = getattr(store, method)(*pos, **kw)
            outcome = {"return": encoded(result)}
        except Exception as exc:
            result = None
            outcome = {"error": {"type": type(exc).__name__, "message": str(exc), "code": getattr(exc, "code", None)}}
        finally:
            AutoReplyStore._open_connection = previous
        transactions = [s.strip().upper() for s in trace if s.strip().upper() in {"BEGIN", "BEGIN IMMEDIATE", "COMMIT", "ROLLBACK"}]
        path_evidence = {
            "terminal_cache_probe": any("and status in ('completed','stopped','failed','waiting_confirmation')" in stmt for stmt in trace),
            "agent_hydration_status_query": any(stmt.lstrip().lower().startswith("select id,status from (") for stmt in trace),
        }
        records.append({"label": label, "method": method, "outcome": outcome, "transactions": transactions, "native_event_paths": path_evidence, "facts": facts()})
        if ("error" in outcome) != (label in expected_errors):
            raise AssertionError(f"unexpected public outcome for {label}: {outcome}")
        return result

    def section(label, fn):
        try:
            fn()
        except Exception as exc:
            records.append({"label": label + ":fixture_abort", "error": {"type": type(exc).__name__, "message": str(exc)}})

    def basics():
        call("blank_title", "create_task", title=" ", runtime_kind="codex")
        call("blank_runtime", "create_task", title="x", runtime_kind=" ")
        call("idle_claim", "claim_next_turn", owner="owner", now=CLOCK)
        task = call("new_task", "create_task", title=" 新任务 ", runtime_kind="codex")
        call("idle_summary", "get_task_summary", task.id)
        call("empty_timeline", "timeline_snapshot", task.id)
        turn = call("queued", "create_turn", task.id, user_text=" 同一个公共方法验证 ", client_request_id="basic-request")
        call("replay", "create_turn", task.id, user_text="同一个公共方法验证", client_request_id="basic-request")
        call("request_conflict", "create_turn", task.id, user_text="different", client_request_id="basic-request")
        call("active_conflict", "create_turn", task.id, user_text="other", client_request_id="other-request")
        call("archive_active", "archive_task", task.id, now=CLOCK)
        assert call("running", "claim_next_turn", owner="owner", execution_run_id="run-basic", lease_seconds=10, now=CLOCK).id == turn.id
        call("wrong_renew_owner", "renew_turn_lease", turn.id, owner="other", now=CLOCK)
        call("session", "set_provider_session", turn.id, " session-synthetic ", owner="owner", now=CLOCK)
        call("renew", "renew_turn_lease", turn.id, owner="owner", lease_seconds=10, now=CLOCK)
        call("text_live", "append_event", turn.id, sequence=2, event_type="text_delta", payload={"text": "live synthetic text", "native_ordinal": 1}, owner="owner", now=CLOCK)
        live = call("events_live_cache", "events_after", turn.id)
        assert live[1].payload["text"] == "live synthetic text"
        assert all(records[-1]["native_event_paths"].values())
        call("snapshot_live_cache", "event_stream_snapshot", turn.id)
        call("bad_sequence", "append_event", turn.id, sequence=8, event_type="text_delta", payload={"text": "bad"}, owner="owner", now=CLOCK)
        call("complete", "complete_turn", turn.id, status="completed", final_text="ephemeral synthetic answer", owner="owner", now=CLOCK)
        terminal = call("events_terminal_hydration", "events_after", turn.id)
        assert terminal[1].payload == {"text": "原生 Agent 记录不可用。"}
        assert records[-1]["native_event_paths"]["agent_hydration_status_query"]
        call("turn_native_missing", "get_turn", turn.id)
        call("rename", "rename_task", task.id, title=" Unicode 标题 ")
        call("summary_terminal", "get_task_summary", task.id)
        call("timeline_terminal", "timeline_snapshot", task.id, turn_limit=1, event_limit=2)
        second = call("second_turn", "create_turn", task.id, user_text="second", client_request_id="second-request")
        call("queued_stop", "request_stop", second.id, now=CLOCK)
        call("repeat_stop", "request_stop", second.id, now=CLOCK)
        call("latest_state", "get_task_summary", task.id)
        call("timeline_page", "timeline_snapshot", task.id, turn_limit=1, event_limit=1, before_sequence=2, event_before=3)
        call("archive", "archive_task", task.id, now=CLOCK)
        call("archive_replay_turn", "create_turn", task.id, user_text="second", client_request_id="second-request")
        call("archived_new_turn", "create_turn", task.id, user_text="third", client_request_id="third-request")
        call("archived_lists", "list_tasks_with_state", include_archived=True)
        call("archived_only", "list_tasks_with_state", archived_only=True)
        call("live_lists", "list_tasks")
        call("bad_list_limit", "list_tasks_with_state", limit=0)
        call("cursor_boundary", "list_tasks_with_state", include_archived=True, cursor=(CLOCK, task.id))
        call("missing_rename", "rename_task", str(UUID(int=9999)), title="missing")
        call("stats", "workbench_stats")

    def recovery():
        task = call("recovery_task", "create_task", title="recovery", runtime_kind="codex")
        turn = call("recovery_turn", "create_turn", task.id, user_text="recover", client_request_id="recovery-request")
        assert call("recovery_claim", "claim_next_turn", owner="old", execution_run_id="run-old", lease_seconds=10, now=CLOCK).id == turn.id
        call("recover_before_expiry", "recover_expired_turns", now=CLOCK)
        call("expired_renew", "renew_turn_lease", turn.id, owner="old", now="2040-01-01 00:00:10")
        assert call("recover_at_expiry", "recover_expired_turns", now="2040-01-01 00:00:10") == 1
        call("old_owner_append", "append_event", turn.id, sequence=2, event_type="text_delta", payload={"text": "stale"}, owner="old", now=LATER)
        assert call("reclaim", "claim_next_turn", owner="new", execution_run_id="run-new", lease_seconds=10, now=LATER).id == turn.id
        call("running_stop", "request_stop", turn.id, now=LATER)
        call("recover_stopped", "recover_expired_turns", now="2040-01-01 00:00:21")
        call("recover_repeat", "recover_expired_turns", now="2040-01-01 00:00:21")
        call("stopped_timeline", "timeline_snapshot", task.id)

    def confirmation(cancel):
        key = "cancel" if cancel else "failed"
        task = call(key + "_task", "create_task", title=key, runtime_kind="codex")
        turn = call(key + "_turn", "create_turn", task.id, user_text=key, client_request_id=key + "-request")
        assert call(key + "_claim", "claim_next_turn", owner=key, execution_run_id="run-" + key, now=CLOCK).id == turn.id
        conf = call(key + "_propose", "create_confirmation", turn.id, action_kind="synthetic", target="local-only", summary="synthetic receipt", risk="test", arguments_json={"synthetic": True}, owner=key, now=CLOCK)
        if cancel:
            call("cancel_intent", "cancel_confirmation_execution", conf.id, resume_context="cancelled", now=CLOCK)
        call(key + "_quiesced", "mark_confirmation_proposer_quiesced", turn.id, owner=key, proposer_run_id="run-" + key, now=CLOCK)
        call(key + "_quiesced_repeat", "mark_confirmation_proposer_quiesced", turn.id, owner=key, proposer_run_id="run-" + key, now=CLOCK)
        if cancel:
            call("cancel_finish", "cancel_confirmation_execution", conf.id, resume_context="cancelled", now=CLOCK)
            call("cancel_replay", "cancel_confirmation_execution", conf.id, resume_context="cancelled", now=CLOCK)
        else:
            call("confirmation_claim", "claim_confirmation_execution", conf.id, owner="exec", now=CLOCK)
            call("confirmation_wrong_owner", "finish_confirmation_execution", conf.id, owner="wrong", status="failed", result_json={"error": "synthetic"}, resume_context="failure", now=CLOCK)
            call("confirmation_failed", "finish_confirmation_execution", conf.id, owner="exec", status="failed", result_json={"error": "synthetic"}, resume_context="failure", now=CLOCK)
            call("confirmation_failed_replay", "finish_confirmation_execution", conf.id, owner="exec", status="failed", result_json={"error": "synthetic"}, resume_context="failure", now=CLOCK)
        call(key + "_list_confirmations", "list_confirmations", task.id)
        call(key + "_timeline", "timeline_snapshot", task.id)
        call(key + "_resume_claim", "claim_next_turn", owner=key, execution_run_id="resumed-" + key, now=CLOCK)
        call(key + "_resume_context", "resume_context_for_executor", turn.id, owner=key, now=CLOCK)
        call(key + "_stop", "complete_turn", turn.id, status="stopped", owner=key, now=CLOCK)

    section("basics", basics)
    section("recovery", recovery)
    section("confirmation_cancel", lambda: confirmation(True))
    section("confirmation_failed", lambda: confirmation(False))
    labels = [record["label"] for record in records]
    assert len(labels) == len(set(labels)), "worker checkpoint labels must be unique"
    args.result.write_text(json.dumps({"records": records, "public_methods": sorted(public_calls)}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument("--output", type=Path)
    parser.add_argument("--worker", choices=("bootstrap", "prepare", "scenario", "dependency"))
    parser.add_argument("--code", type=Path)
    parser.add_argument("--db", type=Path)
    parser.add_argument("--result", type=Path)
    parser.add_argument("--target", action="store_true")
    args = parser.parse_args()
    if args.worker:
        worker(args)
        return
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    script = Path(__file__).resolve()
    patches = [args.repo / "docs/superpowers/plans" / (PREFIX + k + "-caller-proposal.patch") for k in ("store", "workbench")]
    receipt = {"scope": "synthetic fresh original full Store+Workbench source; empty Tasks replacement only", "constructor_selection": "unproved: target _ensure_initialized bypassed after explicit template preparation", "patches": {p.name: digest(p) for p in patches}, "source_files": {f: digest(args.repo / f) for f in ("app/store.py", "app/workbench/store.py", "app/workbench/native_events.py", "app/task20_definitions.py", "app/task8_storage.py")}, "clock": CLOCK, "uuid_stream": "UUID integers 1..9999; Workbench module only", "water_normalization": "source absent sqlite_sequence row and target explicit zero both have effective allocation water zero; setup retained", "network_provider_native_cli": "not invoked"}
    with tempfile.TemporaryDirectory(prefix="task8-workbench-public-", dir="/tmp") as temp:
        root = Path(temp)
        code_source, code_target = root / "source", root / "target"
        for code in (code_source, code_target):
            shutil.copytree(args.repo / "app", code / "app", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        source_db, target_db = code_source / "fixture.sqlite3", code_target / "fixture.sqlite3"

        def run(stage, code, db, target=False):
            result = root / (stage + ("-target" if target else "-source") + ".json")
            cmd = [str(PYTHON), "-B", str(script), "--worker", stage, "--code", str(code), "--db", str(db), "--result", str(result)]
            if target:
                cmd.append("--target")
            completed = subprocess.run(cmd, cwd=code, env=env, capture_output=True, text=True, timeout=180)
            if completed.returncode:
                raise RuntimeError(f"{stage} target={target}: {completed.stderr[-5000:]}")
            return json.loads(result.read_text())

        receipt["bootstrap"] = run("bootstrap", code_source, source_db)
        assert receipt["bootstrap"]["tasks"] == 0
        with sqlite3.connect(source_db) as src, sqlite3.connect(target_db) as dst:
            src.backup(dst)
        for patch in patches:
            subprocess.run(["patch", "--batch", "--fuzz=0", "-p1", "-i", str(patch)], cwd=code_target, check=True, capture_output=True, text=True)
        code_negative = root / "target-without-native-companion"
        shutil.copytree(code_target / "app", code_negative / "app")
        source_control_db = code_source / "dependency-control.sqlite3"
        negative_db = code_negative / "dependency-control.sqlite3"
        for clone in (source_control_db, negative_db):
            with sqlite3.connect(source_db) as src, sqlite3.connect(clone) as dst:
                src.backup(dst)
        native = code_target / "app/workbench/native_events.py"
        before = "from app.task_storage import WORKBENCH_TURN_SQL, task_id_expression"
        text = native.read_text()
        assert text.count(before) == 1
        native.write_text(text.replace(before, "from app.task_storage import task_id_expression\nfrom app.task20_definitions import task20_projection\nWORKBENCH_TURN_SQL = task20_projection('workbench_turns')"))
        receipt["copied_source_files"] = {f: digest(code_source / f) for f in receipt["source_files"]}
        assert receipt["copied_source_files"] == receipt["source_files"], "input files changed while fixture copied"
        receipt["companion"] = "native_events imports task20_projection('workbench_turns'); task_id_expression retained"
        receipt["preparation"] = run("prepare", code_target, target_db, True)
        run("prepare", code_negative, negative_db, True)
        dependency_source = run("dependency", code_source, source_control_db)
        dependency_target = run("dependency", code_negative, negative_db, True)
        receipt["negative_control"] = {"mutation": "omit only native_events companion projection substitution", "source": dependency_source, "target_without_companion": dependency_target}
        assert "return" in dependency_source["outcome"], "source dependency control must succeed"
        assert dependency_source["committed_workbench_event_count"] == 2
        assert dependency_source["live_cache_entries"] == dependency_target["live_cache_entries"] == 1
        assert dependency_target["outcome"].get("error") == {"type": "OperationalError", "message": "no such column: t.client_request_id"}
        assert dependency_target["committed_workbench_event_count"] == 1, "native cleanup error must roll back event insert"
        source = run("scenario", code_source, source_db)
        target = run("scenario", code_target, target_db, True)
        assert source["public_methods"] == target["public_methods"], "method coverage differs"
        assert len(source["records"]) == len(target["records"]) == 81, "expected fixed 81 checkpoints per worker"
        receipt["coverage_assertions"] = {"unique_worker_labels": True, "same_method_set": True, "fixed_checkpoints_per_worker": 81}
        sr = {r["label"]: r for r in source["records"]}
        tr = {r["label"]: r for r in target["records"]}
        differences = []
        for label in sorted(sr.keys() | tr.keys()):
            left, right = sr.get(label), tr.get(label)
            if left != right:
                fields = sorted(k for k in (left or {}).keys() | (right or {}).keys() if (left or {}).get(k) != (right or {}).get(k))
                differences.append({"label": label, "different_fields": fields, "source_outcome": (left or {}).get("outcome", (left or {}).get("error")), "target_outcome": (right or {}).get("outcome", (right or {}).get("error"))})
        receipt.update({"source_calls": len(sr), "target_calls": len(tr), "public_methods": source["public_methods"], "equal_checkpoints": sum(sr.get(k) == tr.get(k) for k in sr.keys() | tr.keys()), "differences": differences, "source_errors": {k: r["outcome"]["error"] for k, r in sr.items() if "error" in r.get("outcome", {})}, "fixture_aborts": [r for r in source["records"] + target["records"] if r["label"].endswith(":fixture_abort")]})
        # Retain bounded decisive successful outputs and logical/physical evidence;
        # no database/code copy or complete row-body history is persisted.
        receipt["comparison_digests"] = {name: hashlib.sha256(json.dumps(data["records"], ensure_ascii=False, sort_keys=True).encode()).hexdigest() for name, data in (("source", source), ("target", target))}
        receipt["native_event_paths"] = {k: sr[k]["native_event_paths"] for k in ("text_live", "events_live_cache", "events_terminal_hydration", "complete")}
        receipt["observations"] = {k: {"source": sr.get(k, {}).get("outcome"), "target": tr.get(k, {}).get("outcome")} for k in ("events_live_cache", "events_terminal_hydration", "running", "reclaim", "recover_stopped", "cancel_finish", "confirmation_failed", "timeline_page")}
        receipt["raw_final_tasks"] = {"source": source["records"][-1].get("facts", {}).get("physical_tasks"), "target": target["records"][-1].get("facts", {}).get("physical_tasks")}
        receipt["final_fk_findings"] = {"source": source["records"][-1].get("facts", {}).get("fk_findings"), "target": target["records"][-1].get("facts", {}).get("fk_findings")}
    receipt["owned_temp_removed"] = not root.exists()
    text = json.dumps(receipt, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(text)
    print(json.dumps({k: receipt[k] for k in ("source_calls", "target_calls", "equal_checkpoints", "differences", "fixture_aborts", "owned_temp_removed")}, ensure_ascii=False))
    if receipt["differences"] or receipt["fixture_aborts"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
