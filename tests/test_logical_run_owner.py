"""One logical owner, with source identities and real invocation facts distinct."""
import pytest

from app.store import AutoReplyStore


def test_claim_finish_uses_common_owner_without_manufacturing_invocation(tmp_path):
    store = AutoReplyStore(tmp_path / "logical.sqlite3")
    parent = store.enqueue_work_summary_input("local_file", "logical-one", "{}")
    [claimed] = store.claim_work_summary_inputs(1)
    assert claimed.id == parent
    run_id = store.claim_logical_run("task_agent_runs", parent)
    assert store.claim_logical_run("task_agent_runs", parent) == run_id
    with store._connect() as db:
        row = db.execute(
            "select id,record_type,legacy_key,source_type,task_id,parent_id,"
            "role,execution_generation,proposal_revision,turn_attempt,"
            "operation_id,lease_owner,lease_expires_at,started_at,"
            "transcript_start,transcript_end from runs where id=?", (run_id,)
        ).fetchone()
        task = db.execute(
            "select id from tasks where record_type='work_summary_inputs' "
            "and cast(legacy_key as integer)=?", (parent,)
        ).fetchone()[0]
        assert tuple(row) == (
            run_id, "agent_runs", str(run_id), "task_agent_runs", task,
            *([None] * 11),
        )
        assert not db.execute(
            "select 1 from runs where record_type='agent_runtime_attempts'"
        ).fetchone()
    store.finish_logical_run("task_agent_runs", run_id, status="completed",
                             codex_session_id="source-no-range", error="")
    run = store.get_logical_run("task_agent_runs", run_id)
    assert run["common_run_id"] == run_id
    assert run["status"] == "completed"
    assert run["native_available"] is False
    assert run["native_reason"] == "native_reference_unavailable"
    assert run["projection_json"] == "{}"
    store.finish_logical_run("task_agent_runs", run_id, status="completed",
                             codex_session_id="source-no-range", error="")
    with pytest.raises(ValueError, match="conflicting"):
        store.finish_logical_run("task_agent_runs", run_id, status="failed",
                                 codex_session_id="source-no-range", error="changed")
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
    run_id = store.create_logical_run(
        "okr_review_runs", request, codex_session_id="historical-native-session",
        codex_transcript_start_line=1, codex_transcript_end_line=9,
    )
    with store._connect() as db:
        assert tuple(db.execute(
            "select status,error,completed_at,updated_at,role,execution_generation,"
            "proposal_revision,turn_attempt,operation_id,lease_owner,lease_expires_at,"
            "started_at,parent_id from runs where id=?", (run_id,)
        ).fetchone()) == (None,) * 13
        assert db.execute(
            "select count(*) from runs where record_type='agent_runtime_attempts'"
        ).fetchone()[0] == 0
    assert store.get_logical_run("okr_review_runs", run_id)["request_id"] == request


def test_retired_parallel_writers_are_absent_from_current_store():
    for source in ("task_agent", "meeting_alignment", "okr_review"):
        for operation in ("begin", "record", "finish", "get"):
            assert not hasattr(AutoReplyStore, f"{operation}_{source}_run")


@pytest.mark.parametrize("source", ["task_agent_runs", "meeting_alignment_runs", "okr_review_runs"])
def test_consumer_parent_contract_rejects_standalone_in_all_existing_refs(tmp_path, source):
    import sqlite3
    from app.action_storage import action_physical_id, insert_action
    from app.shared_action_lifecycle import persist_plan
    from app.run_storage import insert_run
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
        reply_id = db.execute("select id from tasks where record_type='reply_tasks' and cast(legacy_key as integer)=?", (reply,)).fetchone()[0]
        consumer = insert_run(db, "agent_runs", dict(task_id=reply_id, execution_generation="g", role="consumer")).lastrowid
        audit = insert_run(db, "agent_runs", dict(task_id=reply_id, execution_generation="g", role="audit", operation_id="audit")).lastrowid
    standalone = store.create_logical_run(source, task, status="completed" if source != "okr_review_runs" else None)
    with store._connect() as db:
        db.execute("update runs set legacy_key=? where id=?", (str(consumer), standalone))
        for parent, allowed in ((standalone, False), (consumer, True), (audit, True)):
            db.execute("savepoint parent_ref")
            try:
                if allowed:
                    insert_run(db, "agent_run_state_events", dict(parent_id=parent, phase="source", structured_error_json=""))
                else:
                    with pytest.raises(sqlite3.IntegrityError, match="logical agent run"):
                        insert_run(db, "agent_run_state_events", dict(parent_id=parent, phase="source", structured_error_json=""))
            finally:
                db.execute("rollback to parent_ref")
                db.execute("release parent_ref")
        candidate = persist_plan(db, "reviewed", dict(task_id=reply_id,
            execution_generation="g", consumer_run_id=consumer, stage_index=0, proposal_revision=0,
            candidate_digest="digest", candidate_json="{}"))
        review = insert_action(db, "candidate_reviews", dict(candidate_id=candidate.lastrowid,
            audit_run_id=audit, decision="approve", candidate_digest="digest", result_json="{}"))
        result = insert_action(db, "external_action_results", dict(external_action_key="source-receipt",
            business_object_key="b", action_identity="i", operation="o", target_identifiers_json="{}",
            provider_result_json="{}", result_digest="d", first_agent_run_id=consumer))
        receipt = insert_action(db, "agent_execution_receipts", dict(agent_run_id=audit,
            receipt_id="r", operation_id="o", cli="cli", command_path="path", command_digest="d",
            exit_code=0, completed=1, persisted=1, safe_to_confirm=1, effect_counted=0))
        for action, kind, column in (
            (candidate, "review_candidates", "consumer_run_id"),
            (review, "candidate_reviews", "audit_run_id"),
            (result, "external_action_results", "first_agent_run_id"),
            (receipt, "agent_execution_receipts", "agent_run_id"),
        ):
            physical = action_physical_id(db, kind, action.lastrowid)
            with pytest.raises(sqlite3.IntegrityError, match="action parent type mismatch"):
                db.execute(f"update actions set {column}=? where id=?", (standalone, physical))
            columns = [row[1] for row in db.execute("pragma table_xinfo(actions)") if row[6] == 0]
            values = list(db.execute("select " + ",".join(columns) + " from actions where id=?", (physical,)).fetchone())
            values[columns.index(column)] = standalone
            values[columns.index("id")] = None
            with pytest.raises(sqlite3.IntegrityError, match="action parent type mismatch"):
                db.execute("insert into actions(" + ",".join(columns) + ") values("
                           + ",".join("?" for _ in values) + ")", values)
    assert store.get_logical_run(source, consumer, load_native=False) is not None
    wrong_namespace = "meeting_alignment_runs" if source == "task_agent_runs" else "task_agent_runs"
    assert store.get_logical_run(wrong_namespace, consumer, load_native=False) is None
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
