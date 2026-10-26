import json
from app.run_entity_storage import get_run, get_run_provenance
from contextlib import contextmanager
from datetime import UTC, datetime
import sqlite3
from types import SimpleNamespace

import pytest

from app.agent_envelope import AgentEnvelope
from app.store import AutoReplyStore
from app.task8_storage import register_task8_functions
from app.task_run_entity_assembly import task_run_entity_assembly_objects
from app.task_entity_storage import get_task


NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)


class AssembledAutoReplyStore(AutoReplyStore):
    """AutoReplyStore methods against the unactivated, assembled native target."""

    def __init__(self, db):
        self.db = db

    @contextmanager
    def _connect(self):
        yield self.db
        if self.db.in_transaction:
            self.db.commit()

    @contextmanager
    def _immediate_write_transaction(self):
        self.db.execute("begin immediate")
        try:
            yield self.db
        except BaseException:
            self.db.rollback()
            raise
        else:
            self.db.commit()

    @contextmanager
    def _agent_run_write_transaction(self, now):
        value = NOW if now is None else (datetime.fromisoformat(now) if isinstance(now, str) else now)
        stamp = value.strftime("%Y-%m-%d %H:%M:%S")
        self.db.execute("begin immediate")
        try:
            yield self.db, (value, stamp)
        except BaseException:
            self.db.rollback()
            raise
        else:
            self.db.commit()


def _store():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("pragma foreign_keys=on")
    register_task8_functions(db)
    objects = task_run_entity_assembly_objects()
    for ddl in objects.values():
        if ddl.lstrip().lower().startswith("create table"):
            db.execute(ddl)
    for ddl in objects.values():
        if not ddl.lstrip().lower().startswith("create table"):
            db.execute(ddl)
    return AssembledAutoReplyStore(db)


def _request(store, conversation="c", message="m"):
    task_id = store.create_okr_review_request(
        conversation_id=conversation, conversation_title="OKR", trigger_message_id=message,
        trigger_sender="Derek", trigger_sender_user_id="u1", trigger_text="审查 OKR",
        period_label="2026 Q2", period_start="2026-04-01", period_end="2026-06-30",
        okr_source_json='{"processed":{"objectives":[],"okrRows":[]}}',
    )
    return task_id, store.claim_okr_review_requests(1)[0]


def _item():
    return {
        "objective_title": "O", "objective_weight": 1.0, "kr_title": "KR", "kr_weight": 0.5,
        "self_progress": "80%", "kr_progress_update": "完成验收。", "claim_text": "完成验收。",
        "claim_completion_time": "", "deadline": "", "claim_base_score": 60,
        "claim_discount_factor": 1.0, "claim_discount_reason": "无折扣。", "claim_score": 60,
        "verified_completion_time": "", "verified_base_score": 0,
        "verified_discount_factor": 1.0, "verified_discount_reason": "证据不足。",
        "verified_score": 0, "evidence_used": [], "evidence_gap": "缺少记录。",
        "review_comment": "需要补充证据。", "suggested_follow_up": "补充验收记录。",
    }


def _envelope(items):
    return AgentEnvelope.model_validate({
        "kind": "okr_review",
        "user_response": {"mode": "send_reply", "text": "审核完成", "sensitivity_kind": "internal_personnel"},
        "system_actions": [],
        "domain_payload": {"person_name": "Derek", "period_label": "2026 Q2", "summary": "已审核。", "items": items},
        "audit": {"summary": "审核完成。", "documents": [], "confidence": 0.8},
    })


class PersistedStructuredRunner:
    def __init__(self, store, envelope):
        self.store = store
        self.envelope = envelope
        self.attempt_ids = []
        self.calls = []

    def run(self, request_id, conversation_id, conversation_title, single_chat, prompt, *, owner):
        self.calls.append((conversation_id, conversation_title, single_chat, prompt, owner))
        attempt = self.store.claim_runtime_operation_attempt(
            "structured", str(request_id), "codex_oauth", "codex_cli", "local_oauth", "gpt-6-luna",
            owner=owner, lease_seconds=1800, now=NOW,
        )
        self.store.mark_agent_runtime_attempt_running_once(
            attempt.id, owner=owner, lease_seconds=1800, now=NOW,
        )
        completed = self.store.complete_agent_runtime_attempt(
            attempt.id, "structured-session", "trajectory-token", 2, 7,
            owner=owner, now=NOW,
        )
        self.attempt_ids.append(completed.id)
        return SimpleNamespace(
            envelope=self.envelope, attempt_id=completed.id, codex_session_id="structured-session",
            transcript_start_line=2, transcript_end_line=7, audit_tool_events=[],
        )


class PersistedRoutedExecution:
    """Substitute route executor that persists the actual attempt lifecycle."""

    def __init__(self, store, envelope):
        self.store = store
        self.envelope = envelope
        self.attempt_ids = []
        self.calls = []

    def execute(self, **kwargs):
        from app.agent_runtime_router import RoutedCodexExecutionResult

        self.calls.append(kwargs)
        attempt = self.store.claim_runtime_operation_attempt(
            "structured", kwargs["workload_key"], "codex_oauth", "codex_cli", "local_oauth", "gpt-6-luna",
            owner=f"okr_review:{kwargs['workload_key']}", lease_seconds=1800, now=NOW,
        )
        self.store.mark_agent_runtime_attempt_running_once(
            attempt.id, owner=f"okr_review:{kwargs['workload_key']}", lease_seconds=1800, now=NOW,
        )
        completed = self.store.complete_agent_runtime_attempt(
            attempt.id, "structured-session", "trajectory-token", 2, 7,
            owner=f"okr_review:{kwargs['workload_key']}", now=NOW,
        )
        self.attempt_ids.append(completed.id)
        raw = self.envelope.model_dump_json()
        encoded = kwargs["parser"](raw)
        value = kwargs["result_codec"].decode(kwargs["result_codec"].encode(encoded))
        return RoutedCodexExecutionResult(
            value=value, route_name="codex_oauth", attempt_id=completed.id,
            session_id="structured-session", transcript_start=2, transcript_end=7,
        )


def test_native_okr_request_dedupe_claim_failed_reopen_and_done():
    store = _store()
    task_id, claimed = _request(store)
    assert claimed.id == task_id and claimed.status == "processing"
    task = get_task(store.db, task_id)
    assert (task["attempts"], task["available_at"], task["locked_at"],
            task["lease_owner"], task["lease_expires_at"], task["generation"]) == (
                None, None, None, None, None, None,
            )
    assert store.create_okr_review_request(
        conversation_id="c", conversation_title="changed", trigger_message_id="m",
        trigger_sender="changed", trigger_sender_user_id="", trigger_text="changed",
        period_label="changed", period_start="", period_end="", okr_source_json="changed",
    ) == task_id
    store.mark_okr_review_request_failed(task_id, "failure")
    assert store.create_okr_review_request(
        conversation_id="c", conversation_title="changed", trigger_message_id="m",
        trigger_sender="changed", trigger_sender_user_id="", trigger_text="changed",
        period_label="changed", period_start="", period_end="", okr_source_json="reopened",
    ) == task_id
    reopened = store.get_okr_review_request(task_id)
    assert (reopened.status, reopened.error, reopened.codex_session_id, reopened.okr_source_json) == (
        "pending", "", "", "reopened",
    )
    claimed = store.claim_okr_review_requests(1)[0]
    store.mark_okr_review_request_done(task_id, codex_session_id="session")
    assert store.get_okr_review_request(task_id).status == "done"
    assert get_task(store.db, task_id)["parameters"]["codex_session_id"] == "session"


def test_native_okr_stale_processing_recovery_returns_pre_reset_task():
    store = _store()
    task_id, claimed = _request(store)
    store.db.execute("update tasks set updated_at='2000-01-01 00:00:00' where id=?", (task_id,))
    store.db.commit()
    recovered = store.reset_recoverable_okr_review_requests(processing_max_age_seconds=60)
    assert len(recovered) == 1
    assert (recovered[0].id, recovered[0].status) == (task_id, "processing")
    after = store.get_okr_review_request(task_id)
    assert (after.status, after.error, after.codex_session_id) == ("pending", "", "")


def test_actual_native_okr_task_run_and_returned_attempt_bind_after_validation(tmp_path, monkeypatch):
    from app import native_standalone
    from app.okr_review import process_okr_review_request
    from app.run_entity_storage import get_runtime_attempt
    from app.structured_agent import AgentSpec, StructuredCodexRunner

    store = _store()
    from app.run_entity_storage import create_run

    create_run(store.db, values={"created_at": "unrelated"})
    store.db.commit()
    task_id, request = _request(store)
    schema = tmp_path / "schema.json"
    skill = tmp_path / "skill.md"
    schema.write_text("{}", encoding="utf-8")
    skill.write_text("Return the supplied review result.", encoding="utf-8")
    routed = PersistedRoutedExecution(store, _envelope([_item()]))
    runner = StructuredCodexRunner(
        routed_execution=routed, spec=AgentSpec("okr_review", schema, [skill]),
    )
    monkeypatch.setattr("app.structured_agent.extract_codex_audit_events_from_session", lambda *a, **k: [])
    reply = process_okr_review_request(store=store, runner=runner, request=request, single_chat=True)
    attempt_id = routed.attempt_ids[0]
    run_ids = [row[0] for row in store.db.execute("select id from runs where task_id=?", (task_id,))]
    assert len(run_ids) == 1
    run = get_run(store.db, run_ids[0])
    attempt = get_runtime_attempt(store.db, attempt_id)
    assert run["id"] != task_id
    assert run["task_id"] == task_id
    assert (get_run_provenance(store.db, run["id"])["source_type"], run["role"], run["execution_generation"], run["status"], run["error"]) == (
        "okr_review_runs", None, None, None, None,
    )
    assert attempt["parent_id"] == run["id"]
    assert (attempt["workload_kind"], attempt["workload_key"], attempt["attempt_number"]) == (
        "structured", str(task_id), 1,
    )
    assert store.get_okr_review_request(task_id).status == "done"
    item = store.db.execute("select * from okr_review_items where request_id=?", (task_id,)).fetchone()
    assert (item["objective_title"], item["objective_weight"], item["kr_title"], item["kr_weight"]) == (
        "O", 1.0, "KR", 0.5,
    )
    assert "员工主张分" in reply
    observed = []
    monkeypatch.setattr(native_standalone, "okr_envelope", lambda ref: (
        observed.append(ref) or '{"audit":{"summary":"from actual attempt"}}', "",
    ))
    envelope, reason = native_standalone.okr_envelope(native_standalone.native_run_ref(store.db, run["id"], "okr_review_runs"))
    assert reason == ""
    assert json.loads(envelope)["audit"]["summary"] == "from actual attempt"
    assert (observed[0].session_id, observed[0].start, observed[0].end) == (
        "structured-session", 2, 7,
    )


def test_invalid_okr_payload_keeps_actual_attempt_but_creates_no_run_or_item():
    from app.okr_review import process_okr_review_request
    from pydantic import ValidationError

    store = _store()
    task_id, request = _request(store)
    runner = PersistedStructuredRunner(store, _envelope([]))
    with pytest.raises(ValidationError):
        process_okr_review_request(store=store, runner=runner, request=request, single_chat=True)
    assert len(runner.attempt_ids) == 1
    attempt = store.db.execute("select status,parent_id from runtime_attempts where id=?", runner.attempt_ids).fetchone()
    assert tuple(attempt) == ("completed", None)
    assert store.db.execute("select count(*) from runs").fetchone()[0] == 0
    assert store.db.execute("select count(*) from okr_review_items").fetchone()[0] == 0
    assert store.get_okr_review_request(task_id).status == "processing"


def test_exact_attempt_binder_rejects_wrong_task_or_run_owner():
    store = _store()
    task_id, _request_dto = _request(store)
    other_id, _ = _request(store, conversation="other")
    attempt = store.claim_runtime_operation_attempt(
        "structured", str(task_id), "codex_oauth", "codex_cli", "local_oauth", "gpt-6-luna",
        owner=f"okr_review:{task_id}", lease_seconds=1800, now=NOW,
    )
    store.mark_agent_runtime_attempt_running_once(attempt.id, owner=f"okr_review:{task_id}", now=NOW)
    store.complete_agent_runtime_attempt(attempt.id, "session", "trace", 1, 2,
                                         owner=f"okr_review:{task_id}", now=NOW)
    store.create_professional_run(task_id, session_id="session", transcript_start=1, transcript_end=2)
    wrong_run = store.create_professional_run(other_id, session_id="session", transcript_start=1, transcript_end=2)
    with pytest.raises(ValueError, match="owner Run"):
        store.bind_okr_review_runtime_attempt(task_id=task_id, run_id=wrong_run, attempt_id=attempt.id)
    with pytest.raises(ValueError, match="does not match"):
        store.bind_okr_review_runtime_attempt(task_id=other_id, run_id=wrong_run, attempt_id=attempt.id)


def test_okr_run_reader_keeps_native_run_id_when_task_parent_is_missing():
    from app.run_entity_storage import get_run

    store = _store()
    task_id, _request_dto = _request(store)
    run_id = store.create_professional_run(task_id, session_id="orphan-session", transcript_start=1, transcript_end=2)
    store.db.execute("pragma foreign_keys=off")
    store.db.execute("delete from tasks where id=?", (task_id,))
    store.db.commit()
    run = get_run(store.db, run_id)
    assert run is not None
    assert run["id"] == run_id
    assert run["task_id"] == task_id
    assert get_run_provenance(store.db, run_id)["source_type"] == "okr_review_runs"
    assert get_run(store.db, run_id)["task_id"] == task_id
