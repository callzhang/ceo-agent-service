import sqlite3

import pytest

from app.current_database_schema import create_current_database_schema
from app.task8_storage import register_task8_functions


@pytest.fixture
def db():
    connection = sqlite3.connect(":memory:")
    register_task8_functions(connection)
    create_current_database_schema(connection)
    # Reproduce the captured historical edge without removing current guards.
    connection.execute(
        "insert into runs(id,task_id,status,error,created_at) values(17,999,'retry','interrupted','old')"
    )
    connection.execute(
        "insert into run_provenance(run_id,source_type,job_id) values(17,'meeting_alignment_runs',42)"
    )
    connection.commit()
    connection.execute("pragma foreign_keys=on")
    yield connection
    connection.close()


def repair(db, **overrides):
    from scripts.repair_orphan_meeting_run import detach_missing_task

    return detach_missing_task(
        db, **{"run_id": 17, "missing_task_id": 999, "source_job_id": 42, **overrides}
    )


def test_detach_keeps_run_source_identity_schema_and_all_other_values(db):
    before = db.execute("select * from runs where id=17").fetchone()
    provenance = db.execute("select * from run_provenance").fetchall()
    schema = db.execute("select * from sqlite_schema order by name").fetchall()
    assert db.execute("pragma foreign_key_check").fetchall() == [("runs", 17, "tasks", 1)]
    with db:
        assert repair(db)
    after = db.execute("select * from runs where id=17").fetchone()
    assert after == (before[0], None, *before[2:])
    assert db.execute("select * from run_provenance").fetchall() == provenance
    assert db.execute("select * from sqlite_schema order by name").fetchall() == schema
    assert db.execute("select count(*) from tasks").fetchone()[0] == 0
    assert db.execute("pragma foreign_key_check").fetchall() == []
    with db:
        assert not repair(db)


def test_repair_transaction_can_roll_back_exact_original_edge(db):
    before = db.execute("select * from runs").fetchall()
    db.execute("begin immediate")
    assert repair(db)
    assert db.execute("pragma foreign_key_check").fetchall() == []
    db.rollback()
    assert db.execute("select * from runs").fetchall() == before
    assert db.execute("pragma foreign_key_check").fetchall() == [("runs", 17, "tasks", 1)]


@pytest.mark.parametrize("overrides", [{"run_id": 18}, {"missing_task_id": 998}, {"source_job_id": 43}])
def test_wrong_identity_rejects_without_changes(db, overrides):
    before = db.execute("select * from runs").fetchall()
    with pytest.raises(ValueError):
        repair(db, **overrides)
    assert db.execute("select * from runs").fetchall() == before


def test_existing_task_cannot_be_detached(db):
    db.execute(
        "insert into tasks(id,kind,status,parameters_json,created_at,updated_at) "
        "values(999,'meeting_alignment','sent',jsonb('{\"meeting_id\":\"fixture\"}'),'old','old')"
    )
    with pytest.raises(ValueError, match="Task exists"):
        repair(db)
    assert db.execute("select task_id from runs where id=17").fetchone() == (999,)


def test_running_run_cannot_be_changed(db):
    db.execute("update runs set status='running' where id=17")
    with pytest.raises(ValueError, match="active"):
        repair(db)


def test_active_runtime_attempt_cannot_be_detached(db):
    values = {
        "parent_id": 17, "workload_kind": "meeting", "workload_key": "17",
        "attempt_number": 1, "route_name": "codex_oauth", "runtime_kind": "codex",
        "credential_mode": "oauth", "model": "gpt-6-luna", "session_mode": "fresh",
        "source_session_id": "", "attempt_purpose": "normal", "validation_retry_policy_id": "",
        "validation_result_schema_id": "", "session_id": "session", "transcript_reference": "",
        "transcript_start": 0, "transcript_end": 0, "status": "running", "failure_class": "",
        "failure_code": "", "failover_permitted": 0, "lease_owner": "worker",
        "lease_expires_at": "lease", "first_effect_started_at": "", "result_schema_id": "",
        "started_at": "start", "finished_at": "", "created_at": "created", "updated_at": "updated",
    }
    db.execute(
        f"insert into runtime_attempts ({','.join(values)}) values ({','.join('?' for _ in values)})",
        tuple(values.values()),
    )
    with pytest.raises(ValueError, match="active"):
        repair(db)
