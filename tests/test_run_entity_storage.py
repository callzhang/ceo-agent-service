import sqlite3

import pytest

from app.task_run_entity_assembly import task_run_entity_assembly_objects


@pytest.fixture
def db():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    objects = task_run_entity_assembly_objects()
    for sql in objects.values():
        if sql.lstrip().lower().startswith("create table"):
            connection.execute(sql)
    for sql in objects.values():
        if not sql.lstrip().lower().startswith("create table"):
            connection.execute(sql)
    yield connection
    connection.close()


def test_native_run_storage_api_exists_and_preserves_absent_facts(db):
    from app import run_entity_storage as storage

    assert {row[1] for row in db.execute("pragma table_info(runs)")} == {
        name for name, _type, _required, _default in __import__("app.run_entity_definitions", fromlist=["RUN_FIELDS"]).RUN_FIELDS
    }
    from app.task_entity_storage import create_task

    task = create_task(db, values={"kind": "work_summary", "status": "pending", "created_at": "now", "updated_at": "now"})
    run = storage.create_run(db, values={"task_id": task["id"], "status": "completed"})
    assert run["id"] > 0
    assert run["role"] is None
    assert run["proposal_revision"] is None
    assert run["created_at"]
    assert storage.get_run(db, run["id"]) == run
    assert storage.update_run_cas(db, run_id=run["id"], expected={"status": "completed"},
                                  changes={"session_id": "observed"})["session_id"] == "observed"
    assert storage.update_run_cas(db, run_id=run["id"], expected={"status": "pending"},
                                  changes={"error": "stale"}) is None
    with pytest.raises(ValueError, match="unknown Run fields"):
        storage.create_run(db, values={"task_id": task["id"], "role": "consumer", "bogus": 1})


def test_runtime_attempt_native_parent_observation_lineage_and_cas(db):
    from app import run_entity_storage as storage

    assert {row[1] for row in db.execute("pragma table_info(runtime_attempts)")} == {
        "id", "parent_id", "workload_kind", "workload_key", "attempt_number", "route_name",
        "runtime_kind", "credential_mode", "model", "session_mode", "source_session_id",
        "attempt_purpose", "validation_retry_policy_id", "validation_result_schema_id", "session_id",
        "transcript_reference", "transcript_start", "transcript_end", "status", "failure_class",
        "failure_code", "failover_permitted", "lease_owner", "lease_expires_at", "first_effect_started_at",
        "result_schema_id", "started_at", "finished_at", "created_at", "updated_at",
    }
    from app.task_entity_storage import create_task

    task = create_task(db, values={"kind": "reply", "status": "pending", "created_at": "now", "updated_at": "now"})
    run = storage.create_run(db, values={"task_id": task["id"], "status": "running"})
    attempt_values = {
        "parent_id": run["id"], "workload_kind": "agent_run", "workload_key": str(run["id"]),
        "attempt_number": 1, "route_name": "codex_oauth", "runtime_kind": "codex",
        "credential_mode": "oauth", "model": "gpt-6-luna", "session_mode": "fresh",
        "source_session_id": "", "attempt_purpose": "normal", "validation_retry_policy_id": "",
        "validation_result_schema_id": "", "session_id": "session-1", "transcript_reference": "trace-1",
        "transcript_start": 0, "transcript_end": 2, "status": "running", "failure_class": "",
        "failure_code": "", "failover_permitted": 1, "lease_owner": "worker",
        "lease_expires_at": "lease", "first_effect_started_at": "effect", "result_schema_id": "result",
        "started_at": "start", "finished_at": "", "created_at": "created", "updated_at": "updated",
    }
    attempt = storage.create_runtime_attempt(db, values=attempt_values)
    assert storage.get_runtime_attempt(db, attempt["id"]) == attempt
    updated = storage.update_runtime_attempt_cas(
        db, attempt_id=attempt["id"], expected={"status": "running"},
        changes={"session_id": "observed-session", "transcript_end": 9, "updated_at": "observed"},
    )
    assert updated["session_id"] == "observed-session"
    assert updated["transcript_end"] == 9
    for field in ("attempt_purpose", "validation_retry_policy_id", "validation_result_schema_id"):
        with pytest.raises(sqlite3.IntegrityError, match="correction lineage is immutable"):
            storage.update_runtime_attempt_cas(
                db, attempt_id=attempt["id"], expected={"status": "running"}, changes={field: "changed"}
            )
    resume = storage.create_runtime_attempt(db, values={
        **attempt_values, "attempt_number": 2, "route_name": "codex_resume", "session_mode": "resume",
        "source_session_id": "session-1", "session_id": "session-1-resumed",
        "attempt_purpose": "result_validation_correction", "validation_retry_policy_id": "policy-1",
        "validation_result_schema_id": "schema-1", "transcript_start": 2, "transcript_end": 3,
    })
    observed = storage.update_runtime_attempt_cas(
        db, attempt_id=resume["id"], expected={"status": "running"},
        changes={"session_id": "session-1-resumed-observed", "transcript_end": 8,
                 "status": "completed", "finished_at": "finished", "updated_at": "observed"},
    )
    assert observed["session_mode"] == "resume"
    assert observed["source_session_id"] == "session-1"
    assert observed["session_id"] == "session-1-resumed-observed"
    assert (observed["transcript_start"], observed["transcript_end"]) == (2, 8)
    for field, value in (("attempt_purpose", "result_validation_correction"),
                         ("validation_retry_policy_id", "policy-1"),
                         ("validation_result_schema_id", "schema-1")):
        assert observed[field] == value


def test_typed_children_failure_projection_and_caller_rollback(db):
    from app import run_entity_storage as storage
    from app.task_entity_storage import create_task

    task = create_task(db, values={"kind": "work_summary", "status": "pending", "created_at": "now", "updated_at": "now"})
    db.commit()
    sequences_before = [tuple(row) for row in db.execute("select name,seq from sqlite_sequence order by name")]
    run = storage.create_run(db, values={"task_id": task["id"], "status": "failed"})
    storage.set_run_provenance(db, run_id=run["id"], source_type="task_agent_runs")
    storage.set_run_projection(db, run_id=run["id"], projection_json='{"summary":"brief"}')
    assert storage.get_run_projection(db, run["id"]) == '{"summary":"brief"}'
    storage.append_run_failure(db, run_id=run["id"], phase="execute", structured_error_json='{"code":"x"}', created_at="now")
    attempt_values = {
        "parent_id": run["id"], "workload_kind": "agent_run", "workload_key": str(run["id"]),
        "attempt_number": 1, "route_name": "route", "runtime_kind": "codex", "credential_mode": "oauth",
        "model": "model", "session_mode": "fresh", "source_session_id": "", "attempt_purpose": "normal",
        "validation_retry_policy_id": "", "validation_result_schema_id": "", "session_id": "session",
        "transcript_reference": "trace", "transcript_start": 0, "transcript_end": 1, "status": "completed",
        "failure_class": "", "failure_code": "", "failover_permitted": 0, "lease_owner": "",
        "lease_expires_at": "", "first_effect_started_at": "", "result_schema_id": "",
        "started_at": "start", "finished_at": "finish", "created_at": "now", "updated_at": "now",
    }
    attempt = storage.create_runtime_attempt(db, values=attempt_values)
    counts_before = {
        table: db.execute(f"select count(*) from {table}").fetchone()[0]
        for table in ("runs", "runtime_attempts", "run_provenance", "run_projections", "run_failures")
    }
    assert counts_before == {"runs": 1, "runtime_attempts": 1, "run_provenance": 1,
                             "run_projections": 1, "run_failures": 1}
    assert storage.get_runtime_attempt(db, attempt["id"]) == attempt
    db.rollback()
    counts_after = {
        table: db.execute(f"select count(*) from {table}").fetchone()[0]
        for table in ("runs", "runtime_attempts", "run_provenance", "run_projections", "run_failures")
    }
    sequences_after = [tuple(row) for row in db.execute("select name,seq from sqlite_sequence order by name")]
    assert counts_after == {table: 0 for table in counts_before}
    assert sequences_after == sequences_before


def test_all_task_kinds_accept_uninvented_run_facts_and_professional_provenance(db):
    from app import run_entity_storage as storage
    from app.task_entity_storage import create_task

    kinds = (
        "reply", "work_summary", "meeting_alignment", "okr_review", "okr_analysis",
        "workbench_turn", "email_classification", "feedback_processing", "wechat_import",
    )
    parameters = {
        "reply": {}, "work_summary": {}, "meeting_alignment": {"meeting_id": "m"},
        "okr_review": {"conversation_id": "c", "conversation_title": "title", "period_end": "e",
                       "period_label": "p", "period_start": "s", "trigger_message_id": "m",
                       "trigger_sender": "u", "trigger_text": "text"},
        "okr_analysis": {"manager_user_id": "u", "source_digest": "d", "week_end": "e"},
        "workbench_turn": {}, "email_classification": {}, "feedback_processing": {},
        "wechat_import": {"account_id": "a", "import_run_id": "r"},
    }
    runs = {}
    for kind in kinds:
        task = create_task(db, values={
            "kind": kind, "status": "pending", "created_at": "now", "updated_at": "now",
        }, parameters=parameters[kind])
        run = storage.create_run(db, values={"task_id": task["id"], "status": "completed"})
        assert run["role"] is None
        assert run["proposal_revision"] is None
        runs[kind] = run
    work_source = storage.set_run_provenance(
        db, run_id=runs["work_summary"]["id"], source_type="task_agent_runs",
    )
    meeting_source = storage.set_run_provenance(
        db, run_id=runs["meeting_alignment"]["id"], source_type="meeting_alignment_runs",
    )
    assert work_source["job_id"] is None
    assert meeting_source["job_id"] is None
    with pytest.raises(sqlite3.IntegrityError, match="Run source and Task kind mismatch"):
        storage.set_run_provenance(
            db, run_id=runs["reply"]["id"], source_type="task_agent_runs",
        )


def test_runtime_attempt_ordinals_routes_and_standalone_parent_contract(db):
    from app import run_entity_storage as storage
    from app.task_entity_storage import create_task

    task = create_task(db, values={
        "kind": "reply", "status": "pending", "created_at": "now", "updated_at": "now",
    })
    run = storage.create_run(db, values={"task_id": task["id"], "status": "running"})
    base = {
        "parent_id": run["id"], "workload_kind": "agent_run", "workload_key": str(run["id"]),
        "attempt_number": 1, "route_name": "route-a", "runtime_kind": "codex",
        "credential_mode": "oauth", "model": "model", "session_mode": "fresh",
        "source_session_id": "", "attempt_purpose": "normal", "validation_retry_policy_id": "",
        "validation_result_schema_id": "", "session_id": "s", "transcript_reference": "t",
        "transcript_start": 0, "transcript_end": 1, "status": "completed", "failure_class": "",
        "failure_code": "", "failover_permitted": 0, "lease_owner": "", "lease_expires_at": "",
        "first_effect_started_at": "", "result_schema_id": "", "started_at": "start",
        "finished_at": "end", "created_at": "now", "updated_at": "now",
    }
    first = storage.create_runtime_attempt(db, values=base)
    second = storage.create_runtime_attempt(db, values={**base, "attempt_number": 2, "route_name": "route-b"})
    assert (first["attempt_number"], second["attempt_number"]) == (1, 2)
    with pytest.raises(sqlite3.IntegrityError):
        storage.create_runtime_attempt(db, values={**base, "route_name": "route-c"})
    storage.create_runtime_attempt(db, values={
        **base, "attempt_number": 3, "route_name": "active-route", "status": "running",
        "lease_owner": "worker", "lease_expires_at": "lease",
    })
    with pytest.raises(sqlite3.IntegrityError):
        storage.create_runtime_attempt(db, values={
            **base, "attempt_number": 4, "route_name": "active-route", "status": "running",
            "lease_owner": "worker", "lease_expires_at": "lease",
        })
    standalone = storage.create_runtime_attempt(db, values={
        **base, "parent_id": None, "workload_kind": "structured_workload", "workload_key": "work-1",
        "attempt_number": 1, "route_name": "route-a",
    })
    assert standalone["parent_id"] is None


def test_projection_getter_preserves_source_sqlite_storage_class_and_value(db):
    from app import run_entity_storage as storage
    from app.logical_run_definitions import logical_run_objects
    from app.task_entity_storage import create_task

    source = sqlite3.connect(":memory:")
    source.execute(logical_run_objects()["runs"])
    source.execute(
        "insert into runs(id,record_type,legacy_key,task_id,source_type,status,error,projection_json,"
        "completed_at,session_id,updated_at) values(1,'agent_runs','1',7,'task_agent_runs','completed',"
        "'',?,?, '', 'now')", (sqlite3.Binary(b'{"adopted":1}'), "now"),
    )
    source_projection, source_type = source.execute(
        "select projection_json,typeof(projection_json) from runs where id=1"
    ).fetchone()

    task = create_task(db, values={
        "kind": "work_summary", "status": "pending", "created_at": "now", "updated_at": "now",
    })
    run = storage.create_run(db, values={"task_id": task["id"], "status": "completed"})
    storage.set_run_provenance(db, run_id=run["id"], source_type="task_agent_runs")
    storage.set_run_projection(db, run_id=run["id"], projection_json=source_projection)
    native_projection, native_type = db.execute(
        "select projection_json,typeof(projection_json) from run_projections where run_id=?", (run["id"],)
    ).fetchone()
    assert (native_projection, native_type) == (source_projection, source_type)
    assert storage.get_run_projection(db, run["id"]) == source_projection
    assert storage.get_run_projection(db, run["id"]) == b'{"adopted":1}'

    text_run = storage.create_run(db, values={"task_id": task["id"], "status": "completed"})
    text_projection = '{"summary":"brief"}'
    storage.set_run_projection(db, run_id=text_run["id"], projection_json=text_projection)
    assert storage.get_run_projection(db, text_run["id"]) == text_projection
    source.close()
