from contextlib import contextmanager

import pytest

from app.logical_run_store import LogicalRunStoreMixin
from app.task_entity_storage import create_task
from app.task_run_entity_assembly import task_run_entity_assembly_objects


class NativeStore(LogicalRunStoreMixin):
    def __init__(self, db):
        self.db = db

    @contextmanager
    def _connect(self):
        yield self.db

    @contextmanager
    def _agent_run_write_transaction(self, _owner):
        yield self.db, (None, None)


@pytest.fixture
def native_store():
    import sqlite3

    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    objects = task_run_entity_assembly_objects()
    for sql in objects.values():
        if sql.lstrip().lower().startswith("create table"):
            db.execute(sql)
    for sql in objects.values():
        if not sql.lstrip().lower().startswith("create table"):
            db.execute(sql)
    yield NativeStore(db)
    db.close()


def task(db, kind, status, *, meeting_id="meeting", conversation_id="c"):
    return create_task(db, values={
        "kind": kind, "status": status, "created_at": "created", "updated_at": "updated",
    }, parameters={
        "meeting_alignment": {"meeting_id": meeting_id},
        "okr_review": {"conversation_id": conversation_id, "conversation_title": "OKR",
                       "period_end": "e", "period_label": "p", "period_start": "s",
                       "trigger_message_id": "m", "trigger_sender": "u", "trigger_text": "review"},
    }.get(kind, {}))


def test_native_task_agent_run_claim_finish_and_projection(native_store):
    store = native_store
    parent = task(store.db, "work_summary", "processing")
    run_id = store.claim_professional_run(parent["id"])
    assert store.claim_professional_run(parent["id"]) == run_id
    run = store.get_professional_run(run_id, load_native=False)
    assert run["id"] == run_id
    assert run["task_id"] == parent["id"]
    assert run["role"] is None
    assert run["transcript_start"] is None and run["transcript_end"] is None
    store.finish_professional_run(run_id, status="completed", session_id="session")
    store.finish_professional_run(run_id, status="completed", session_id="session")
    with pytest.raises(ValueError, match="conflicting"):
        store.finish_professional_run(run_id, status="failed", session_id="session")
    with store._connect() as db:
        assert db.execute("select projection_json from run_projections where run_id=?", (run_id,)).fetchone()[0] == "{}"


def test_meeting_and_okr_native_runs_preserve_absent_lifecycle(native_store):
    store = native_store
    meeting = task(store.db, "meeting_alignment", "processing")
    meeting_run = store.claim_professional_run(meeting["id"])
    store.finish_professional_run(meeting_run, status="ready_to_send", session_id="meeting-session",
                                  transcript_start=3, transcript_end=8, error="")
    meeting_fact = store.get_professional_run(meeting_run, load_native=False)
    assert (meeting_fact["status"], meeting_fact["session_id"],
            meeting_fact["transcript_start"], meeting_fact["transcript_end"]) == (
                "ready_to_send", "meeting-session", 3, 8,
            )
    assert meeting_fact["error"] == ""
    assert meeting_fact["completed_at"] == meeting_fact["updated_at"]

    defaulted = task(store.db, "meeting_alignment", "processing", meeting_id="meeting-defaults")
    defaulted_run = store.claim_professional_run(defaulted["id"])
    store.finish_professional_run(defaulted_run, status="failed")
    defaulted_fact = store.get_professional_run(defaulted_run, load_native=False)
    assert (defaulted_fact["transcript_start"], defaulted_fact["transcript_end"]) == (0, 0)
    assert defaulted_fact["error"] == ""

    okr = task(store.db, "okr_review", "completed")
    okr_run = store.create_professional_run(
        okr["id"], session_id="okr-session", transcript_start=1, transcript_end=5,
    )
    okr_fact = store.get_professional_run(okr_run, load_native=False)
    assert (okr_fact["status"], okr_fact["error"], okr_fact["completed_at"],
            okr_fact["updated_at"]) == (None, None, None, None)
    with pytest.raises(ValueError, match="after its native result"):
        store.claim_professional_run(okr["id"])


def test_okr_audit_summary_comes_from_native_envelope(native_store, monkeypatch):
    from app import native_standalone

    okr = task(native_store.db, "okr_review", "completed")
    run_id = native_store.create_professional_run(
        okr["id"], session_id="okr-session", transcript_start=1, transcript_end=5,
    )
    monkeypatch.setattr(native_standalone, "okr_envelope", lambda _ref: (
        '{"audit":{"summary":"reviewed"}}', "",
    ))
    monkeypatch.setattr(native_standalone, "_meeting_tool_events", lambda *_args: ("[]", False, "unavailable"))

    run = native_store.get_professional_run(run_id)

    assert run["audit_summary"] == "reviewed"


@pytest.mark.parametrize(("task_kind", "source_type", "workload_kind", "other_kind"), [
    ("meeting_alignment", "meeting_alignment_runs", "meeting", "meeting_audit"),
    ("work_summary", "task_agent_runs", "task", "meeting"),
])
def test_native_task_reader_selects_latest_completed_child_attempt(
    native_store, task_kind, source_type, workload_kind, other_kind,
):
    from app.native_standalone import native_run_ref

    store = native_store
    parent = task(store.db, task_kind, "processing")
    run_id = store.claim_professional_run(parent["id"])
    attempt = {
        "parent_id": run_id, "workload_kind": workload_kind, "workload_key": str(run_id),
        "attempt_number": 1, "route_name": "route-a", "runtime_kind": "codex_cli",
        "credential_mode": "oauth", "model": "gpt-6-luna", "session_mode": "fresh",
        "source_session_id": "", "attempt_purpose": "normal", "validation_retry_policy_id": "",
        "validation_result_schema_id": "", "session_id": "session-one", "transcript_reference": "trace-one",
        "transcript_start": 2, "transcript_end": 5, "status": "completed", "failure_class": "",
        "failure_code": "", "failover_permitted": 0, "lease_owner": "", "lease_expires_at": "",
        "first_effect_started_at": "", "result_schema_id": "", "started_at": "start",
        "finished_at": "finish", "created_at": "created", "updated_at": "updated",
    }
    from app.run_entity_storage import create_runtime_attempt

    first = create_runtime_attempt(store.db, values=attempt)
    create_runtime_attempt(store.db, values={
        **attempt, "attempt_number": 2, "route_name": "route-b",
        "session_id": "session-two", "transcript_reference": "trace-two",
        "transcript_start": 5, "transcript_end": 9,
    })
    create_runtime_attempt(store.db, values={
        **attempt, "workload_kind": other_kind, "session_id": "different-role",
    })
    ref = native_run_ref(store.db, run_id, source_type)
    assert (ref.session_id, ref.start, ref.end) == ("session-two", 5, 9)
    assert first["parent_id"] == run_id


def test_okr_reference_uses_native_task_key_without_cross_key_match(native_store):
    from app.native_standalone import native_run_ref
    from app.run_entity_storage import create_runtime_attempt

    store = native_store
    owner = task(store.db, "okr_review", "completed")
    other = task(store.db, "okr_review", "completed", conversation_id="other")
    run_id = store.create_professional_run(
        owner["id"], session_id="same-session", transcript_start=1, transcript_end=5,
    )
    attempt = {
        "parent_id": None, "workload_kind": "structured", "workload_key": str(other["id"]),
        "attempt_number": 1, "route_name": "route-a", "runtime_kind": "claude_cli",
        "credential_mode": "oauth", "model": "gpt-6-luna", "session_mode": "fresh",
        "source_session_id": "", "attempt_purpose": "normal", "validation_retry_policy_id": "",
        "validation_result_schema_id": "", "session_id": "same-session", "transcript_reference": "trace",
        "transcript_start": 1, "transcript_end": 5, "status": "completed", "failure_class": "",
        "failure_code": "", "failover_permitted": 0, "lease_owner": "", "lease_expires_at": "",
        "first_effect_started_at": "", "result_schema_id": "", "started_at": "start",
        "finished_at": "finish", "created_at": "created", "updated_at": "updated",
    }
    create_runtime_attempt(store.db, values=attempt)

    ref = native_run_ref(store.db, run_id, "okr_review_runs")
    assert (ref.kind, ref.session_id, ref.start, ref.end) == (
        "codex_cli", "same-session", 1, 5,
    )

    create_runtime_attempt(store.db, values={
        **attempt, "workload_key": str(owner["id"]), "attempt_number": 1,
        "transcript_start": 8, "transcript_end": 10,
    })
    assert native_run_ref(store.db, run_id, "okr_review_runs") is None


@pytest.mark.parametrize(("kind", "key", "status"), [
    ("meeting", "owner", "completed"),
    ("structured", "other", "completed"),
    ("structured", "owner", "failed"),
])
def test_okr_parent_edge_mismatch_cannot_fall_back_to_run_trace(
    native_store, kind, key, status,
):
    from app.native_standalone import native_run_ref
    from app.run_entity_storage import create_runtime_attempt

    store = native_store
    owner = task(store.db, "okr_review", "completed")
    run_id = store.create_professional_run(
        owner["id"], session_id="run-trace", transcript_start=1, transcript_end=5,
    )
    create_runtime_attempt(store.db, values={
        "parent_id": run_id, "workload_kind": kind,
        "workload_key": str(owner["id"] if key == "owner" else owner["id"] + 1),
        "attempt_number": 1, "route_name": "route-a", "runtime_kind": "codex_cli",
        "credential_mode": "oauth", "model": "gpt-6-luna", "session_mode": "fresh",
        "source_session_id": "", "attempt_purpose": "normal", "validation_retry_policy_id": "",
        "validation_result_schema_id": "", "session_id": "attempt-trace", "transcript_reference": "trace",
        "transcript_start": 7, "transcript_end": 9, "status": status, "failure_class": "",
        "failure_code": "", "failover_permitted": 0, "lease_owner": "", "lease_expires_at": "",
        "first_effect_started_at": "", "result_schema_id": "", "started_at": "start",
        "finished_at": "finish", "created_at": "created", "updated_at": "updated",
    })
    assert native_run_ref(store.db, run_id, "okr_review_runs") is None


def test_okr_parent_edge_ambiguity_is_unavailable(native_store):
    from app.native_standalone import native_run_ref
    from app.run_entity_storage import create_runtime_attempt

    store = native_store
    owner = task(store.db, "okr_review", "completed")
    run_id = store.create_professional_run(
        owner["id"], session_id="run-trace", transcript_start=1, transcript_end=5,
    )
    base = {
        "parent_id": run_id, "workload_kind": "structured", "workload_key": str(owner["id"]),
        "attempt_number": 1, "route_name": "route-a", "runtime_kind": "codex_cli",
        "credential_mode": "oauth", "model": "gpt-6-luna", "session_mode": "fresh",
        "source_session_id": "", "attempt_purpose": "normal", "validation_retry_policy_id": "",
        "validation_result_schema_id": "", "session_id": "attempt-trace", "transcript_reference": "trace",
        "transcript_start": 7, "transcript_end": 9, "status": "completed", "failure_class": "",
        "failure_code": "", "failover_permitted": 0, "lease_owner": "", "lease_expires_at": "",
        "first_effect_started_at": "", "result_schema_id": "", "started_at": "start",
        "finished_at": "finish", "created_at": "created", "updated_at": "updated",
    }
    create_runtime_attempt(store.db, values=base)
    create_runtime_attempt(store.db, values={**base, "attempt_number": 2})
    assert native_run_ref(store.db, run_id, "okr_review_runs") is None


def test_okr_parentless_history_requires_exact_task_session_range(native_store):
    from app.native_standalone import native_run_ref
    from app.run_entity_storage import create_runtime_attempt

    store = native_store
    owner = task(store.db, "okr_review", "completed")
    other = task(store.db, "okr_review", "completed", conversation_id="other")
    run_id = store.create_professional_run(
        owner["id"], session_id="history-session", transcript_start=2, transcript_end=5,
    )
    base = {
        "parent_id": None, "workload_kind": "structured", "workload_key": str(owner["id"]),
        "attempt_number": 1, "route_name": "route-a", "runtime_kind": "claude_cli",
        "credential_mode": "oauth", "model": "gpt-6-luna", "session_mode": "fresh",
        "source_session_id": "", "attempt_purpose": "normal", "validation_retry_policy_id": "",
        "validation_result_schema_id": "", "session_id": "history-session", "transcript_reference": "trace",
        "transcript_start": 2, "transcript_end": 5, "status": "completed", "failure_class": "",
        "failure_code": "", "failover_permitted": 0, "lease_owner": "", "lease_expires_at": "",
        "first_effect_started_at": "", "result_schema_id": "", "started_at": "start",
        "finished_at": "finish", "created_at": "created", "updated_at": "updated",
    }
    create_runtime_attempt(store.db, values={**base, "workload_key": str(other["id"])})
    fallback = native_run_ref(store.db, run_id, "okr_review_runs")
    assert (fallback.kind, fallback.session_id, fallback.start, fallback.end) == (
        "codex_cli", "history-session", 2, 5,
    )
    create_runtime_attempt(store.db, values={**base, "attempt_number": 2})
    exact = native_run_ref(store.db, run_id, "okr_review_runs")
    assert (exact.kind, exact.session_id, exact.start, exact.end) == (
        "claude_cli", "history-session", 2, 5,
    )

    unrelated_range_run = store.create_professional_run(
        owner["id"], session_id="other-range", transcript_start=8, transcript_end=9,
    )
    assert native_run_ref(store.db, unrelated_range_run, "okr_review_runs") is None


def test_okr_attempt_bound_to_another_run_cannot_be_reused_as_history(native_store):
    from app.native_standalone import native_run_ref
    from app.run_entity_storage import create_runtime_attempt

    store = native_store
    owner = task(store.db, "okr_review", "completed")
    first = store.create_professional_run(
        owner["id"], session_id="same-trace", transcript_start=1, transcript_end=4,
    )
    second = store.create_professional_run(
        owner["id"], session_id="same-trace", transcript_start=1, transcript_end=4,
    )
    create_runtime_attempt(store.db, values={
        "parent_id": second, "workload_kind": "structured", "workload_key": str(owner["id"]),
        "attempt_number": 1, "route_name": "route-a", "runtime_kind": "codex_cli",
        "credential_mode": "oauth", "model": "gpt-6-luna", "session_mode": "fresh",
        "source_session_id": "", "attempt_purpose": "normal", "validation_retry_policy_id": "",
        "validation_result_schema_id": "", "session_id": "same-trace", "transcript_reference": "trace",
        "transcript_start": 1, "transcript_end": 4, "status": "completed", "failure_class": "",
        "failure_code": "", "failover_permitted": 0, "lease_owner": "", "lease_expires_at": "",
        "first_effect_started_at": "", "result_schema_id": "", "started_at": "start",
        "finished_at": "finish", "created_at": "created", "updated_at": "updated",
    })
    assert native_run_ref(store.db, first, "okr_review_runs") is None


def test_okr_orphan_run_does_not_invent_task_attempt_key(native_store):
    from app.native_standalone import native_run_ref
    from app.run_entity_storage import set_run_provenance

    owner = task(native_store.db, "okr_review", "completed")
    run_id = native_store.create_professional_run(
        owner["id"], session_id="orphan-trace", transcript_start=1, transcript_end=3,
    )
    with native_store._connect() as db:
        set_run_provenance(db, run_id=run_id, source_type="okr_review_runs")
        db.execute("drop trigger trg_runs_task_kind_update")
        db.execute("update runs set task_id=null where id=?", (run_id,))
    ref = native_run_ref(native_store.db, run_id, "okr_review_runs")
    assert (ref.session_id, ref.start, ref.end) == ("orphan-trace", 1, 3)


def test_historical_meeting_orphan_keeps_provenance_without_a_task(native_store):
    from app.run_entity_storage import create_run, set_run_provenance

    with native_store._connect() as db:
        run = create_run(db, values={"task_id": None, "status": "retry", "created_at": "created"})
        set_run_provenance(
            db, run_id=run["id"], source_type="meeting_alignment_runs", job_id=4908,
        )
    hydrated = native_store.get_professional_run(run["id"], load_native=False)
    assert hydrated["task_id"] is None
    assert hydrated["job_id"] == 4908
