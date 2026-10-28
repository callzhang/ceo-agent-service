"""Dashboard uses current persisted execution facts, without changing them."""
from tests.scheduled_claim_fixture import claim_scheduled_occurrence

from datetime import UTC, datetime
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.store import AutoReplyStore

NOW = datetime(2026, 10, 20, 17, 0, tzinfo=UTC)


def test_runtime_metadata_reads_native_runtime_attempts(tmp_path):
    from app.web_api.dashboard import _runtime_metadata
    store = AutoReplyStore(tmp_path / "runtime.db")
    task_id = seed(store, "native-runtime", status="processing", lease="2026-10-20 17:10:00")
    from app.run_entity_storage import create_run, create_runtime_attempt
    with store._connect() as db:
        now = "2026-10-20 16:58:00"
        run = create_run(db, values={"task_id": task_id, "execution_generation": "initial",
            "role": "consumer", "status": "running", "started_at": now, "updated_at": now})
        create_runtime_attempt(db, values={"parent_id": run["id"], "workload_kind": "agent_run",
            "workload_key": str(run["id"]), "attempt_number": 1, "route_name": "primary",
            "runtime_kind": "codex_cli", "credential_mode": "oauth", "model": "test-model",
            "session_mode": "fresh", "source_session_id": "", "attempt_purpose": "normal",
            "validation_retry_policy_id": "", "validation_result_schema_id": "", "session_id": "",
            "transcript_reference": "", "transcript_start": 0, "transcript_end": 0,
            "status": "running", "failure_class": "", "failure_code": "", "failover_permitted": 0,
            "lease_owner": "worker", "lease_expires_at": "2026-10-20 17:10:00",
            "first_effect_started_at": "", "result_schema_id": "", "started_at": now,
            "finished_at": "", "created_at": now, "updated_at": now})
        rows = _runtime_metadata(db, "2026-10-20T17:00:00+00:00")
    assert rows[task_id]["model"] == "test-model"


def seed(store, key, *, status="pending", attempts=0, lease=""):
    task = store.ensure_reply_task(
        conversation_id=key,
        conversation_title=f"Task {key}",
        single_chat=True,
        trigger_message_id=key,
        trigger_create_time="time",
        trigger_sender="Sender",
        trigger_text="input",
    )
    with store._connect() as db:
        db.execute(
            "update tasks set status=?,attempts=?,created_at=?,updated_at=?,available_at=?,locked_at=? where id=? and kind='reply'",
            (status, attempts, "2026-10-20 16:00:00", "2026-10-20 16:59:00",
             "2026-10-20 17:05:00" if attempts and status == "pending" else "",
             "2026-10-20 16:58:00" if status == "processing" else None, task.id),
        )
        if lease:
            db.execute(
                "update tasks set lease_owner=?,lease_expires_at=? where id=? and kind='reply'",
                ("worker", lease, task.id),
            )
    return task.id


def test_initial_retry_and_active_are_distinct_and_reads_do_not_write(tmp_path):
    from app.web_api.dashboard import read_dashboard_queues

    store = AutoReplyStore(tmp_path / "test.db")
    first = seed(store, "first")
    retry = seed(store, "retry", attempts=2)
    active = seed(
        store, "active", status="processing", attempts=3, lease="2026-10-20 17:10:00"
    )
    seed(store, "expired", status="processing", attempts=1, lease="2026-10-20 16:59:00")
    seed(store, "done", status="done", attempts=1)
    with store._connect() as db:
        before = db.total_changes
        result = read_dashboard_queues(db, now=NOW)
        assert db.total_changes == before
    assert [r["id"] for r in result["active"]] == [f"reply_tasks:{active}"]
    assert result["active"][0]["retry_number"] == 2
    waiting = {r["id"]: r for r in result["queued"]}
    assert set(waiting) == {f"reply_tasks:{first}", f"reply_tasks:{retry}"}
    assert waiting[f"reply_tasks:{first}"]["retry_number"] == 0
    assert waiting[f"reply_tasks:{retry}"]["retry_number"] == 2
    assert (
        waiting[f"reply_tasks:{retry}"]["available_at"] == "2026-10-20T17:05:00+00:00"
    )


def test_running_agent_metadata_and_current_generation(tmp_path):
    from app.web_api.dashboard import read_dashboard_queues
    from app.run_entity_storage import create_run, create_runtime_attempt

    store = AutoReplyStore(tmp_path / "test.db")
    task = seed(
        store, "agent", status="processing", attempts=1, lease="2026-10-20 17:10:00"
    )
    with store._connect() as db:
        generation = db.execute("select execution_generation from tasks where id=?", (task,)).fetchone()[0]
        now = "2026-10-20 16:58:00"
        run = create_run(db, values={"task_id": task, "execution_generation": generation,
            "role": "audit", "status": "running", "lease_owner": "worker",
            "lease_expires_at": "2026-10-20 17:10:00", "started_at": now, "updated_at": now})
        create_runtime_attempt(db, values={"parent_id": run["id"], "workload_kind": "agent_run",
            "workload_key": str(run["id"]), "attempt_number": 1, "route_name": "primary",
            "runtime_kind": "codex_cli", "credential_mode": "oauth", "model": "test-model",
            "session_mode": "fresh", "source_session_id": "", "attempt_purpose": "normal",
            "validation_retry_policy_id": "", "validation_result_schema_id": "", "session_id": "",
            "transcript_reference": "", "transcript_start": 0, "transcript_end": 0,
            "status": "running", "failure_class": "", "failure_code": "", "failover_permitted": 0,
            "lease_owner": "worker", "lease_expires_at": "2026-10-20 17:10:00",
            "first_effect_started_at": "", "result_schema_id": "", "started_at": now,
            "finished_at": "", "created_at": now, "updated_at": now})
        result = read_dashboard_queues(db, now=NOW)
        assert not read_dashboard_queues(db, now=NOW.replace(minute=11))["active"]
    row = result["active"][0]
    assert row["agent"] == "audit"
    assert row["model"] == "test-model"
    assert row["runtime_kind"] == "codex_cli"
    assert row["started_at"] == "2026-10-20T16:58:00+00:00"


def test_endpoint_pages_queue_without_exposing_payloads(tmp_path):
    from app.web_api.dashboard import register_dashboard_routes

    store = AutoReplyStore(tmp_path / "test.db")
    for i in range(3):
        seed(store, f"queued-{i}")
    app = FastAPI()
    register_dashboard_routes(app, store_factory=lambda: store)
    response = TestClient(app).get(
        "/api/console/dashboard/queues?state=queued&page=2&page_size=2"
    )
    assert response.status_code == 200
    data = response.json()
    assert data["meta"]["total"] == 3
    assert len(data["items"]) == 1
    assert data["counts"] == {"active": 0, "queued": 3}
    assert "trigger_text" not in data["items"][0]
    assert "payload_json" not in data["items"][0]


def test_superseded_business_object_is_not_in_queue(tmp_path):
    from app.web_api.dashboard import read_dashboard_queues
    store = AutoReplyStore(tmp_path / "test.db")
    old = seed(store, "old")
    new = seed(store, "new")
    with store._connect() as db:
        key = db.execute("select business_object_key from task_inputs where task_id=?", (new,)).fetchone()[0]
        db.execute("update task_inputs set business_object_key=? where task_id=?", (key, old))
        result = read_dashboard_queues(db, now=NOW)
    assert [r["id"] for r in result["queued"]] == [f"reply_tasks:{new}"]


def test_dashboard_route_is_registered_in_console_app(tmp_path):
    from tests.test_console_web_api import _client

    with _client(tmp_path, spa_enabled=True, asset=b"<html>console</html>") as client:
        response = client.get("/api/console/dashboard/queues?state=queued")
        assert response.status_code == 200
        assert response.json()["counts"] == {"active": 0, "queued": 0}


def test_email_classifier_uses_subject_and_its_live_model(tmp_path):
    from app.task_entity_storage import create_task
    from app.run_entity_storage import create_runtime_attempt
    from app.web_api.dashboard import read_dashboard_queues

    store = AutoReplyStore(tmp_path / "test.db")
    with store._connect() as db:
        now = "2026-10-20 16:58:00"
        task = create_task(db, values={"kind": "email_classification", "status": "processing",
            "created_at": now, "updated_at": now, "lease_owner": "classifier",
            "lease_expires_at": "2026-10-20 17:10:00"})
        db.execute("insert into email_classification_inputs(task_id,channel,stable_message_identity,input_json) values(?,?,?,?)",
                   (task["id"], "email", "opaque-message-id", '{"message":{"subject":"Customer proposal"}}'))
        create_runtime_attempt(db, values={"parent_id": None, "workload_kind": "email_classification",
            "workload_key": str(task["id"]), "attempt_number": 1, "route_name": "primary",
            "runtime_kind": "claude_cli", "credential_mode": "oauth", "model": "real-classifier-model",
            "session_mode": "fresh", "source_session_id": "", "attempt_purpose": "normal",
            "validation_retry_policy_id": "", "validation_result_schema_id": "", "session_id": "",
            "transcript_reference": "", "transcript_start": 0, "transcript_end": 0,
            "status": "running", "failure_class": "", "failure_code": "", "failover_permitted": 0,
            "lease_owner": "routed-classifier", "lease_expires_at": "2026-10-20 17:10:00",
            "first_effect_started_at": "", "result_schema_id": "", "started_at": now,
            "finished_at": "", "created_at": now, "updated_at": now})
        result = read_dashboard_queues(db, now=NOW)
    assert len(result["active"]) == 1
    row = result["active"][0]
    assert row["title"] == "Customer proposal"
    assert row["model"] == "real-classifier-model"


def test_meeting_waits_until_eligible_and_delivery_wait_remains_queued(tmp_path):
    from app.task_entity_storage import create_task
    from app.web_api.dashboard import read_dashboard_queues

    store = AutoReplyStore(tmp_path / "test.db")
    with store._connect() as db:
        for key, phase in [
            ("101", "waiting"),
            ("102", "ready_to_send"),
            ("103", "waiting"),
        ]:
            create_task(db, values={"kind": "meeting_alignment", "status": phase,
                "available_at": "2026-10-20 17:45:00" if key == "103" else "",
                "created_at": "2026-10-20 16:30:00", "updated_at": "2026-10-20 16:30:00"},
                parameters={"meeting_id": "meeting-" + key, "title": "Meeting " + key,
                    "source_json": "{}", "participants_json": "[]", "ended_at": "2026-10-20 16:30:00",
                    "eligible_at": "2026-10-20 17:30:00" if key != "103" else ""})
        result = read_dashboard_queues(db, now=NOW)
    assert len(result["active"]) == 0
    assert len(result["queued"]) == 3
    assert result["queued"][2]["available_at"] == "2026-10-20T17:45:00+00:00"
    assert result["queued"][0]["available_at"] == "2026-10-20T17:30:00+00:00"
    assert result["queued"][1]["stage"] == "ready_to_send"


def test_active_direct_email_retry_counts_completed_attempts(tmp_path):
    from tests.test_email_store import _create_action_with_attempts
    from app.web_api.dashboard import read_dashboard_queues

    path = tmp_path / "test.db"
    store, action_id = _create_action_with_attempts(path, ("failed",))
    from app import shared_action_lifecycle

    with store._connect() as db:
        db.execute("BEGIN IMMEDIATE")
        from app.action_entity_storage import get_action
        action = get_action(db, int(action_id))
        assert action is not None
        claimed = shared_action_lifecycle.claim_direct_email_action(
            db, action_id=action_id, expected_status="failed",
            expected_started_at=action["started_at"], attempt_count=1,
            claimed_at="2026-10-20T17:00:00+00:00",
        )
        assert claimed is not None
    with store._connect() as db:
        result = read_dashboard_queues(db, now=NOW)
    rows = [r for r in result["active"] if r["id"] == f"email_actions:{action_id}"]
    assert len(rows) == 1
    assert rows[0]["retry_number"] == 1


def test_workbench_title_model_and_start_with_distinct_valid_owners(tmp_path):
    from app.workbench.store import WorkbenchStore
    from app.run_entity_storage import create_runtime_attempt
    from app.web_api.dashboard import read_dashboard_queues

    store = WorkbenchStore(tmp_path / "test.db")
    workbench = store.create_task(title="Investigate incident", runtime_kind="codex")
    turn = store.create_turn(workbench.id, user_text="start", client_request_id="opaque-request")
    with store._connect() as db:
        db.execute("update tasks set status='processing',lease_owner='executor-owner',lease_expires_at=?,locked_at=?,updated_at=? where id=?",
                   ("2026-10-20 17:10:00", "2026-10-20 16:58:00", "2026-10-20 16:58:00", int(turn.id)))
        now = "2026-10-20 16:58:00"
        create_runtime_attempt(db, values={"parent_id": None, "workload_kind": "workbench",
            "workload_key": str(turn.id), "attempt_number": 1, "route_name": "primary",
            "runtime_kind": "codex_cli", "credential_mode": "oauth", "model": "real-workbench-model",
            "session_mode": "fresh", "source_session_id": "", "attempt_purpose": "normal",
            "validation_retry_policy_id": "", "validation_result_schema_id": "", "session_id": "",
            "transcript_reference": "", "transcript_start": 0, "transcript_end": 0,
            "status": "running", "failure_class": "", "failure_code": "", "failover_permitted": 0,
            "lease_owner": "routed-owner", "lease_expires_at": "2026-10-20 17:10:00",
            "first_effect_started_at": "", "result_schema_id": "", "started_at": now,
            "finished_at": "", "created_at": now, "updated_at": now})
        rows = read_dashboard_queues(db, now=NOW)["active"]
    assert len(rows) == 1
    assert rows[0]["title"] == "Investigate incident"
    assert rows[0]["model"] == "real-workbench-model"
    assert rows[0]["started_at"] == "2026-10-20T16:58:00+00:00"


def test_independent_okr_and_wechat_runtime_metadata(tmp_path):
    from app.task_entity_storage import create_task
    from app.run_entity_storage import create_runtime_attempt
    from app.web_api.dashboard import read_dashboard_queues

    store = AutoReplyStore(tmp_path / "test.db")
    with store._connect() as db:
        now = "2026-10-20 16:58:00"
        okr = create_task(db, values={"kind": "okr_review", "status": "processing",
            "created_at": now, "updated_at": now}, parameters={
                "conversation_id": "okr", "conversation_title": "Review OKR", "trigger_message_id": "msg",
                "trigger_sender": "owner", "trigger_text": "review", "period_label": "Q4",
                "period_start": "2026-10-01", "period_end": "2026-12-31"})
        imported = create_task(db, values={"kind": "wechat_import", "status": "processing",
            "created_at": now, "updated_at": now},
            parameters={"account_id": "wechat", "import_run_id": "import"})
        for workload, task in (("structured", okr), ("memory", imported)):
            create_runtime_attempt(db, values={"parent_id": None, "workload_kind": workload,
                "workload_key": str(task["id"]), "attempt_number": 1, "route_name": "primary",
                "runtime_kind": "codex_cli", "credential_mode": "oauth", "model": "independent-model",
                "session_mode": "fresh", "source_session_id": "", "attempt_purpose": "normal",
                "validation_retry_policy_id": "", "validation_result_schema_id": "", "session_id": "",
                "transcript_reference": "", "transcript_start": 0, "transcript_end": 0,
                "status": "running", "failure_class": "", "failure_code": "", "failover_permitted": 0,
                "lease_owner": "native-owner", "lease_expires_at": "2026-10-20 17:10:00",
                "first_effect_started_at": "", "result_schema_id": "", "started_at": now,
                "finished_at": "", "created_at": now, "updated_at": now})
        rows = read_dashboard_queues(db, now=NOW)["active"]
    assert len(rows) == 2
    assert all(row["model"] == "independent-model" for row in rows)
    assert (
        next(row for row in rows if row["source"] == "微信 Memory 导入")["retry_number"]
        is None
    )


def test_scheduled_command_shows_saved_name_and_first_claim_clock(tmp_path):
    from datetime import timedelta
    from app.state_storage import update_state
    from app.web_api.dashboard import read_dashboard_queues

    store = AutoReplyStore(tmp_path / "scheduled.db")
    task = store.create_scheduled_task(name="处理新的日历邀请", command="calendar-invites-once",
        cron_expression="10 * * * * *", timezone_name="UTC", enabled=True, now=NOW)
    run = store.create_scheduled_task_run(task.id, trigger_kind="scheduled", scheduled_for=NOW - timedelta(minutes=10),
        now=NOW - timedelta(minutes=10))
    claim_scheduled_occurrence(store, run.id, owner="worker", lease_seconds=600,
        now=NOW - timedelta(minutes=2))
    with store._connect() as db:
        db.execute("begin immediate")
        source_rowid = db.execute(
            "select cast(legacy_key as integer) from state where record_type='dispatcher_claim_leases' "
            "and adapter_name='scheduled' and source_id=?", (str(run.id),),
        ).fetchone()[0]
        update_state(db, "dispatcher_claim_leases", source_rowid, {
            "owner_pid": 123, "generation": 3, "created_at": "2026-10-20 16:58:00",
            "updated_at": "2026-10-20 16:59:55",
        })
        before = db.total_changes
        [row] = read_dashboard_queues(db, now=NOW)["active"]
        assert db.total_changes == before
    assert row["title"] == "处理新的日历邀请"
    assert row["agent"] == "调度器"
    assert row["runtime_kind"] == "内置命令"
    assert row["model"] == ""
    assert row["stage"] == "dispatching"
    assert row["started_at"] == "2026-10-20T16:58:00+00:00"
    assert row["updated_at"] == "2026-10-20T16:59:55+00:00"
    assert row["retry_number"] is None  # lease generation is not an Agent retry count
    assert row["detail_url"] == f"/scheduled-tasks?id={task.id}"


def test_unclaimed_scheduled_agent_dispatch_does_not_claim_to_be_running_a_model(tmp_path):
    from app.web_api.dashboard import read_dashboard_queues
    store = AutoReplyStore(tmp_path / "scheduled.db")
    task = store.create_scheduled_task(name="日报", prompt="生成日报", runtime_id="codex_oauth",
        runtime_options={"model":"configured-model"}, working_directory="/tmp",
        cron_expression="0 * * * * *", timezone_name="UTC", enabled=True, now=NOW)
    store.create_scheduled_task_run(task.id, trigger_kind="scheduled", scheduled_for=NOW, now=NOW)
    with store._connect() as db:
        [row] = read_dashboard_queues(db, now=NOW)["queued"]
    assert row["title"] == "日报"
    assert row["model"] == ""
    assert row["started_at"] == ""
