"""One logical owner, with source identities and real invocation facts distinct."""
import sqlite3

import pytest

from app.store import AutoReplyStore


def test_claim_finish_uses_common_owner_without_manufacturing_invocation(tmp_path):
    store = AutoReplyStore(tmp_path / "logical.sqlite3")
    parent = store.enqueue_work_summary_input("local_file", "logical-one", "{}")
    [claimed] = store.claim_work_summary_inputs(1)
    assert claimed.id == parent
    run_id = store.claim_professional_run(parent)
    assert store.claim_professional_run(parent) == run_id
    with store._connect() as db:
        row = db.execute(
            "select id,task_id,parent_id,"
            "role,execution_generation,proposal_revision,turn_attempt,"
            "operation_id,lease_owner,lease_expires_at,started_at,"
            "transcript_start,transcript_end from runs where id=?", (run_id,)
        ).fetchone()
        assert tuple(row) == (run_id, parent, *([None] * 11))
        assert db.execute(
            "select source_type from run_provenance where run_id=?", (run_id,)
        ).fetchone()[0] == "task_agent_runs"
        assert db.execute("select count(*) from runtime_attempts").fetchone()[0] == 0
    store.finish_professional_run(run_id, status="completed",
                                  session_id="source-no-range", error="")
    run = store.get_professional_run(run_id)
    assert run["id"] == run_id
    assert run["status"] == "completed"
    assert run["native_available"] is False
    assert run["native_reason"] == "native_reference_unavailable"
    assert run["projection_json"] == "{}"
    store.finish_professional_run(run_id, status="completed",
                                  session_id="source-no-range", error="")
    with pytest.raises(ValueError, match="conflicting"):
        store.finish_professional_run(run_id, status="failed",
                                      session_id="source-no-range", error="changed")
    with store._connect() as db:
        assert db.execute(
            "select count(*) from actions where record_type in "
            "('review_candidates','candidate_reviews')"
        ).fetchone()[0] == 0


def test_okr_history_preserves_absent_lifecycle_without_an_attempt(tmp_path):
    store = AutoReplyStore(tmp_path / "okr.sqlite3")
    request = store.create_okr_review_request(
        conversation_id="c", conversation_title="OKR", trigger_message_id="m",
        trigger_sender="sender", trigger_sender_user_id="owner", trigger_text="review",
        period_label="period", period_start="start", period_end="end",
        okr_source_json="{}",
    )
    run_id = store.create_professional_run(
        request, session_id="historical-native-session",
        transcript_start=1, transcript_end=9,
    )
    with store._connect() as db:
        assert tuple(db.execute(
            "select status,error,completed_at,updated_at,role,execution_generation,"
            "proposal_revision,turn_attempt,operation_id,lease_owner,lease_expires_at,"
            "started_at,parent_id from runs where id=?", (run_id,)
        ).fetchone()) == (None,) * 13
        assert db.execute("select count(*) from runtime_attempts").fetchone()[0] == 0
    assert store.get_professional_run(run_id)["task_id"] == request


def test_retired_parallel_writers_are_absent_from_current_store():
    for source in ("task_agent", "meeting_alignment", "okr_review"):
        for operation in ("begin", "record", "finish"):
            assert not hasattr(AutoReplyStore, f"{operation}_{source}_run")
    assert not hasattr(AutoReplyStore, "get_task_agent_run")
    assert not hasattr(AutoReplyStore, "get_okr_review_run")


RECEIPT_PAYLOAD = sqlite3.connect(":memory:").execute(
    "select jsonb('[\"r\",\"o\",\"cli\",\"path\",\"d\",0,1,1,1,\"\",0]')"
).fetchone()[0]


@pytest.mark.parametrize("source", ["task_agent_runs", "meeting_alignment_runs", "okr_review_runs"])
def test_consumer_parent_contract_rejects_standalone_in_all_existing_refs(tmp_path, source):
    from app import run_entity_storage
    store = AutoReplyStore(tmp_path / "role-boundary.sqlite3")
    reply = store.enqueue_reply_task(conversation_id="c", conversation_title="C", single_chat=True,
        trigger_message_id="m", trigger_create_time="", trigger_sender="s", trigger_text="text")
    if source == "task_agent_runs":
        task = store.enqueue_work_summary_input("local_file", "parent-boundary", "{}")
    elif source == "meeting_alignment_runs":
        task = store.upsert_meeting_alignment_job(meeting_id="parent-boundary", title="Meeting",
            source_json="{}", participants_json="[]", ended_at="", eligible_at="", status="pending")
    else:
        task = store.create_okr_review_request(conversation_id="okr", conversation_title="OKR",
            trigger_message_id="okr", trigger_sender="s", trigger_sender_user_id="u", trigger_text="review",
            period_label="period", period_start="", period_end="", okr_source_json="{}")
    with store._connect() as db:
        consumer = run_entity_storage.create_run(db, values=dict(
            task_id=reply, execution_generation="g", role="consumer", status="completed",
            proposal_revision=0, turn_attempt=0, operation_id="consumer", started_at="now",
            completed_at="now", updated_at="now", structured_error_json="", error="",
            session_id="s", transcript_start=0, transcript_end=0,
            lease_owner="", lease_expires_at=""))["id"]
        audit = run_entity_storage.create_run(db, values=dict(
            task_id=reply, execution_generation="g", role="audit", operation_id="audit",
            status="completed"))["id"]
    standalone = store.create_professional_run(
        task, status="completed" if source != "okr_review_runs" else None)
    with store._connect() as db:
        for parent, allowed in ((standalone, False), (consumer, True), (audit, True)):
            db.execute("savepoint parent_ref")
            try:
                values = dict(task_id=reply, parent_id=parent, status="completed")
                if allowed:
                    run_entity_storage.create_run(db, values=values)
                else:
                    with pytest.raises(sqlite3.IntegrityError, match="Run parent must be logical"):
                        run_entity_storage.create_run(db, values=values)
            finally:
                db.execute("rollback to parent_ref")
                db.execute("release parent_ref")
        receipt = db.execute(
            "insert into actions(_kind,payload_json,agent_run_id) values(9,?,?)",
            (RECEIPT_PAYLOAD, audit),
        ).lastrowid
        db.execute("update actions set agent_run_id=? where id=?", (consumer, receipt))
        with pytest.raises(sqlite3.IntegrityError, match="action parent type mismatch"):
            db.execute("update actions set agent_run_id=? where id=?", (standalone, receipt))
        with pytest.raises(sqlite3.IntegrityError, match="action parent type mismatch"):
            db.execute("insert into actions(_kind,payload_json,agent_run_id) values(9,?,?)",
                       (RECEIPT_PAYLOAD, standalone))
    assert store.get_professional_run(standalone, load_native=False) is not None
    assert store.get_professional_run(consumer, load_native=False) is None
    assert store.get_agent_run(standalone, load_events=False) is None
    assert store.get_agent_run(consumer, load_events=False).id == consumer
    with pytest.raises(ValueError, match="does not exist"):
        store.complete_agent_run(standalone, {}, owner="fixture")
    with pytest.raises(ValueError, match="does not exist"):
        store.claim_agent_runtime_attempt(standalone, "route", "codex_cli", "local_oauth", "model")


def test_source_absent_fields_are_rejected_without_a_write(tmp_path):
    from app import logical_run_storage
    store = AutoReplyStore(tmp_path / "source-absent.sqlite3")
    summary = store.enqueue_work_summary_input("local_file", "absent-range", "{}")
    request = store.create_okr_review_request(
        conversation_id="c", conversation_title="OKR", trigger_message_id="m",
        trigger_sender="s", trigger_sender_user_id="u", trigger_text="review",
        period_label="period", period_start="", period_end="", okr_source_json="{}",
    )
    with store._connect() as db:
        for source, parent, fields in (
            ("task_agent_runs", summary, {"status": "completed", "codex_transcript_end_line": 9}),
            ("okr_review_runs", request, {"status": "completed"}),
            ("okr_review_runs", request, {"error": "not a stored source field"}),
        ):
            before = db.total_changes
            with pytest.raises(ValueError, match="absent from source"):
                logical_run_storage.create_logical_run(db, source, parent, **fields)
            assert db.total_changes == before
