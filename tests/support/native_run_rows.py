"""Insert native Run, RuntimeAttempt and schedule-occurrence rows for fixtures."""
from types import SimpleNamespace

_RUN_DEFAULTS = {
    "proposal_revision": 0, "turn_attempt": 0, "operation_id": "", "session_id": "",
    "transcript_start": 0, "transcript_end": 0, "structured_error_json": "", "error": "",
    "lease_owner": "", "lease_expires_at": "", "started_at": "", "completed_at": "",
    "updated_at": "",
}
_ATTEMPT_DEFAULTS = {
    "session_mode": "fresh", "source_session_id": "", "attempt_purpose": "normal",
    "validation_retry_policy_id": "", "validation_result_schema_id": "", "session_id": "",
    "transcript_reference": "", "transcript_start": 0, "transcript_end": 0,
    "failure_class": "", "failure_code": "", "failover_permitted": 0, "lease_owner": "",
    "lease_expires_at": "", "first_effect_started_at": "", "result_schema_id": "",
    "started_at": "", "finished_at": "", "created_at": "", "updated_at": "",
}


def insert_native_run(db, values):
    """Insert a consumer/audit Run; return its native `runs.id`."""
    from app.run_entity_storage import create_run

    return create_run(db, values={**_RUN_DEFAULTS, **values})["id"]


def insert_run_row(db, values):
    """Like `insert_native_run` but shaped like a cursor (`.lastrowid`)."""
    return SimpleNamespace(lastrowid=insert_native_run(db, values))


def insert_runtime_attempt_row(db, values):
    from app.run_entity_storage import create_runtime_attempt

    return create_runtime_attempt(db, values={**_ATTEMPT_DEFAULTS, **values})


def insert_scheduled_occurrence(db, values):
    from app.scheduled_occurrence_storage import insert_occurrence

    defaults = {
        "skip_or_error_reason": "", "occurrence_count": 1, "result_summary": "",
        "lease_owner": "", "created_at": "2026-09-10 12:00:00",
        "first_scheduled_for": values.get("scheduled_for", ""),
        "execution_kind": "", "execution_id": "",
    }
    return insert_occurrence(db, {**defaults, **values})


def find_run_for_turn(store, task_id, execution_generation, *, role, proposal_revision, turn_attempt):
    with store._connect() as db:
        row = db.execute(
            "select id from runs where task_id=? and execution_generation=? and role=? "
            "and proposal_revision=? and turn_attempt=?",
            (task_id, execution_generation, role.value, proposal_revision, turn_attempt),
        ).fetchone()
    return store.get_agent_run(int(row[0])) if row else None


def memory_events_for_attempt(store, attempt_id):
    from app.current_action_schema import current_reply_action_projection
    from app.store import MemoryWriteEvent

    with store._connect() as db:
        projection = current_reply_action_projection("memory_write_events")
        rows = db.execute(f"select * from ({projection}) where attempt_id=? order by id",
                          (attempt_id,)).fetchall()
    return [MemoryWriteEvent.model_validate(dict(row)) for row in rows]
