"""Explicit retirement preserves old results without accepting current failures."""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import pytest

from app.audit_web import _reply_attempt_queue_snapshot, handle_rerun_attempt_post
from app.quality_gate import scan_hourly_quality
from app.store import AutoReplyStore
from app.web_api.attempts import build_attempt_detail


AUTHORITY = "docs/architecture.md:current-instance-decisions"
NOW = datetime.now(timezone.utc)


@pytest.fixture(autouse=True)
def isolated_native_home(tmp_path, monkeypatch):
    home = tmp_path / "native-home"
    home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(home))
    return home


def seed(store, *, native_home):
    assert native_home.resolve().is_relative_to(store.path.parent.resolve())
    store.enqueue_reply_task(
        conversation_id="synthetic", conversation_title="Synthetic",
        single_chat=False, trigger_message_id="question",
        trigger_create_time=NOW.isoformat(), trigger_sender="Synthetic",
        trigger_text="Synthetic rule question", execution_generation="old-contract",
    )
    fact = {"assertion": "Synthetic checked fact", "references": ["synthetic:1"]}
    result = {
        "outcome": "needs_human", "needs_human_reason": "Choose a reusable rule",
        "error": {"code": "", "retryable": False, "authorization_required": False},
        "decision_options": [
            {"key": key, "label": key, "instruction": key,
             "consequence": key, "applies_to": "task_class"}
            for key in ("allow", "stop")
        ],
        "decision_basis": {
            "verified_facts": [fact], "rule_evidence": [fact],
            "quality_explanation": "Synthetic explanation",
            "no_external_action_evidence": [fact], "conclusion": "Rule question",
        },
    }
    attempt_id = store.record_reply_attempt(
        conversation_id="synthetic", conversation_title="Synthetic",
        trigger_message_id="question", trigger_sender="Synthetic",
        trigger_text="Synthetic rule question", action="agent_run",
        sensitivity_kind="general", send_status="needs_human",
    )
    session_id = str(uuid4())
    native = native_home / "sessions" / NOW.strftime("%Y/%m/%d") / f"rollout-fixture-{session_id}.jsonl"
    native.parent.mkdir(parents=True, exist_ok=True)
    native.write_text("\n".join(json.dumps(record) for record in (
        {"type": "session_meta", "payload": {"id": session_id}},
        {"type": "response_item", "payload": {"type": "message", "role": "assistant", "content": [
            {"type": "output_text", "text": json.dumps(result)},
        ]}},
    )) + "\n")
    with store._connect() as db:
        task_id = db.execute("select id from reply_tasks").fetchone()[0]
        db.execute("update reply_tasks set status='done' where id=?", (task_id,))
        run_id = db.execute(
            "insert into agent_runs(reply_task_id, execution_generation, role, status, codex_session_id, "
            "transcript_start_line, transcript_end_line) "
            "values (?, 'old-contract', 'consumer', 'completed', ?, 1, 2)",
            (task_id, session_id),
        ).lastrowid
        db.execute("update reply_attempts set agent_run_id=? where id=?", (run_id, attempt_id))
    return attempt_id, task_id, run_id, result


def invalid_count(store):
    return sum(i.count for i in scan_hourly_quality(store.path).violations
               if i.code == "invalid_needs_human_result")


def native_path(store, run_id):
    from app.native_trajectory import find_codex_session_path

    with store._connect() as db:
        session = db.execute("select codex_session_id from agent_runs where id=?", (run_id,)).fetchone()[0]
    path = find_codex_session_path(session, codex_home=Path(os.environ["CODEX_HOME"]))
    assert path is not None and path.resolve().is_relative_to(Path(os.environ["CODEX_HOME"]).resolve())
    return path


def test_resolved_question_projects_consistently_and_cannot_rerun(tmp_path, isolated_native_home):
    store = AutoReplyStore(tmp_path / "state.sqlite3")
    attempt_id, task_id, run_id, result = seed(store, native_home=isolated_native_home)
    original_native = native_path(store, run_id).read_bytes()
    with store._connect() as db:
        db.execute("update reply_attempts set resolved_at=current_timestamp, resolution=? where id=?",
                   ("旧长期规则问题已退役；未作出业务决策，也未执行新动作。", attempt_id))
    _, detail = build_attempt_detail(store, attempt_id)
    assert detail["status"]["raw"] == "skipped"
    assert "退役" in detail["status"]["message"]
    assert detail["actions"]["can_rerun"] is False
    assert detail["actions"]["terminal"] is True
    assert handle_rerun_attempt_post(store, attempt_id)[0] == 409
    assert store.get_reply_task(task_id).status == "done"
    assert json.loads(store.get_agent_run(run_id).final_result_json) == result
    assert native_path(store, run_id).read_bytes() == original_native
    assert store.get_reply_attempt(attempt_id).send_status == "needs_human"
    items = store.list_operation_logs(source_tables=("reply_attempts",))
    assert items[0].status == "skipped"
    assert store.list_history_items(kinds=("reply",))[0].status == "skipped"
    with store._connect() as db:
        snapshot = _reply_attempt_queue_snapshot(db)
    assert snapshot["counts"].get("needs_human", 0) == 0
    assert invalid_count(store) == 0


def test_explicit_retirement_records_provenance_preserves_history_and_is_idempotent(tmp_path, isolated_native_home):
    from app.rule_question_retirement import retire_rule_question

    store = AutoReplyStore(tmp_path / "state.sqlite3")
    attempt_id, task_id, run_id, result = seed(store, native_home=isolated_native_home)
    original_native = native_path(store, run_id).read_bytes()
    assert invalid_count(store) == 1
    preview = retire_rule_question(store, attempt_id, authority=AUTHORITY)
    assert preview["applied"] is False
    assert invalid_count(store) == 1
    receipt = retire_rule_question(store, attempt_id, authority=AUTHORITY, apply=True)
    assert receipt["applied"] is True
    assert receipt["attempt_id"] == attempt_id
    assert receipt["task_id"] == task_id
    assert receipt["agent_run_id"] == run_id
    assert receipt["authority"] == AUTHORITY
    canonical = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    assert receipt["original_result_sha256"] == sha256(canonical.encode()).hexdigest()
    assert invalid_count(store) == 0
    assert retire_rule_question(store, attempt_id, authority=AUTHORITY, apply=True) == receipt
    assert store.get_reply_attempt(attempt_id).send_status == "needs_human"
    assert json.loads(store.get_agent_run(run_id).final_result_json) == result
    assert native_path(store, run_id).read_bytes() == original_native
    assert store.get_reply_task(task_id).status == "done"
    with store._connect() as db:
        assert db.execute("select count(*) from agent_runs").fetchone()[0] == 1
        assert db.execute("select final_result_json from agent_runs where id=?", (run_id,)).fetchone()[0] == ""
        for table in ("candidate_reviews", "candidate_selections", "candidate_executions",
                      "candidate_action_attempts", "external_action_results", "sent_replies"):
            assert db.execute(f"select count(*) from {table}").fetchone()[0] == 0
        assert json.loads(db.execute("select value from service_state where key=?",
                          (f"rule_question_retirement:{attempt_id}",)).fetchone()[0]) == receipt


@pytest.mark.parametrize("change", ["modern", "malformed", "generation", "active", "candidate", "newer", "retryable", "authorization_required"])
def test_retirement_refuses_current_or_unproven_questions(tmp_path, change, isolated_native_home):
    from app.rule_question_retirement import retire_rule_question

    store = AutoReplyStore(tmp_path / "state.sqlite3")
    attempt_id, task_id, run_id, result = seed(store, native_home=isolated_native_home)
    with store._connect() as db:
        if change == "modern":
            result["decision_options"][0]["applies_to"] = "current_instance"
        elif change == "malformed":
            result.pop("decision_basis")
        elif change in {"retryable", "authorization_required"}:
            result["error"][change] = True
        elif change == "generation":
            db.execute("update reply_tasks set execution_generation='new'")
        elif change == "active":
            db.execute("update reply_tasks set status='needs_human'")
        elif change == "candidate":
            db.execute("insert into review_candidates(task_id, execution_generation, consumer_run_id, "
                       "candidate_json, candidate_digest, stage_index, proposal_revision) values (?, 'old-contract', ?, '{}', 'digest',0,0)",
                       (task_id, run_id))
        elif change == "newer":
            db.execute("insert into reply_attempts(channel, conversation_id, conversation_title, "
                       "trigger_message_id, trigger_sender, trigger_text, action, sensitivity_kind, "
                       "codex_reason, send_status) values ('dingtalk','synthetic','Synthetic', "
                       "'question','Synthetic','new question','agent_run','general','new','needs_human')")
    if change in {"modern", "malformed", "retryable", "authorization_required"}:
        path = native_path(store, run_id)
        records = [json.loads(line) for line in path.read_text().splitlines()]
        records[1]["payload"]["content"][0]["text"] = json.dumps(result)
        path.write_text("\n".join(json.dumps(record) for record in records) + "\n")
    with pytest.raises(ValueError):
        retire_rule_question(store, attempt_id, authority=AUTHORITY, apply=True)
    assert store.get_reply_attempt(attempt_id).resolved_at == ""
    assert invalid_count(store) > 0


def test_cli_preview_does_not_initialize_or_migrate_the_database(tmp_path, isolated_native_home):
    store = AutoReplyStore(tmp_path / "state.sqlite3")
    attempt_id, _, run_id, _ = seed(store, native_home=isolated_native_home)
    original_native = native_path(store, run_id).read_bytes()
    with store._connect() as db:
        db.execute("drop table meeting_alignment_delivery_claims")
        before = list(db.execute("select sql from sqlite_master order by name"))
        before_data = "\n".join(db.iterdump())
    preview = subprocess.run([
        sys.executable, "-m", "app.rule_question_retirement", "--database", str(store.path),
        "--attempt-id", str(attempt_id), "--authority", AUTHORITY,
    ], text=True, capture_output=True, timeout=30)
    assert preview.returncode == 0, preview.stderr
    assert json.loads(preview.stdout)["applied"] is False
    with store._connect() as db:
        assert list(db.execute("select sql from sqlite_master order by name")) == before
        assert "\n".join(db.iterdump()) == before_data
    assert native_path(store, run_id).read_bytes() == original_native
