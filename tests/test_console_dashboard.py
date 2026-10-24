"""Dashboard uses current persisted execution facts, without changing them."""

from datetime import UTC, datetime
import sqlite3

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.store import AutoReplyStore
from app.task8_storage import task8_set_parts
from app.task_lifecycle_storage import encoded

NOW = datetime(2026, 10, 20, 17, 0, tzinfo=UTC)


def test_runtime_metadata_supports_deployed_runs_schema_without_summary_input_id():
    from app.web_api.dashboard import _runtime_metadata

    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(
        """
        create table runs (
            id integer primary key,
            record_type text,
            legacy_key text,
            task_id integer,
            parent_id integer,
            execution_generation text,
            role text,
            status text,
            lease_owner text,
            lease_expires_at text,
            started_at text,
            updated_at text,
            workload_kind text,
            workload_key text,
            runtime_kind text,
            model text
        );
        create table tasks (
            id integer primary key,
            record_type text,
            lifecycle_status text,
            "reply_tasks__execution_generation" text,
            lifecycle_lease_owner text,
            lifecycle_lease_expires_at text
        );
        insert into tasks values
            (9, 'meeting_alignment_jobs', 'processing', 'generation-1', 'worker', '2026-10-20T17:10:00+00:00');
        insert into runs values
            (1, 'meeting_alignment_runs', '55', 9, null, null, null, 'running', '', '',
             '2026-10-20T16:59:00+00:00', '2026-10-20T16:59:00+00:00',
             null, null, null, null),
            (2, 'agent_runtime_attempts', '2', null, null, null, 'consumer', 'running',
             'worker', '2026-10-20T17:10:00+00:00', '2026-10-20T16:59:00+00:00',
             '2026-10-20T16:59:00+00:00', 'meeting', '55', 'codex_cli', 'test-model');
        """
    )

    assert _runtime_metadata(db, "2026-10-20T17:00:00+00:00")[9]["model"] == "test-model"


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
    from app.run_storage import insert_run

    store = AutoReplyStore(tmp_path / "test.db")
    task = seed(
        store, "agent", status="processing", attempts=1, lease="2026-10-20 17:10:00"
    )
    with store._connect() as db:
        run = insert_run(
            db,
            "agent_runs",
            dict(
                task_id=task,
                execution_generation="initial",
                role="audit",
                status="running",
                lease_owner="worker",
                lease_expires_at="2026-10-20 17:10:00",
                started_at="2026-10-20 16:58:00",
            ),
        )
        insert_run(
            db,
            "agent_runtime_attempts",
            dict(
                parent_id=run.lastrowid,
                workload_kind="agent_run",
                workload_key=str(run.lastrowid),
                attempt_number=1,
                route_name="primary",
                runtime_kind="codex_cli",
                credential_mode="oauth",
                model="test-model",
                status="running",
                lease_owner="",
                lease_expires_at="",
                started_at="2026-10-20 16:58:00",
            ),
        )
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


def test_memory_write_retry_is_visible_and_terminal_history_is_not(tmp_path):
    from app.web_api.dashboard import read_dashboard_queues
    from app.action_storage import insert_task_delivery_action

    store = AutoReplyStore(tmp_path / "test.db")
    task = seed(store, "memory-parent", status="done", attempts=1)
    with store._connect() as db:
        insert_task_delivery_action(
            db,
            "task_memory_write_events",
            dict(
                reply_task_id=task,
                execution_generation="initial",
                consumer_run_id=0,
                status="pending",
                attempts=2,
                available_at="2026-10-20 17:05:00",
                error="connection timeout",
            ),
        )
        result = read_dashboard_queues(db, now=NOW)
    assert len(result["queued"]) == 1
    assert result["queued"][0]["source"] == "任务 Memory 写入"
    assert result["queued"][0]["retry_number"] == 2
    assert result["queued"][0]["error"] == "connection timeout"


def test_dashboard_route_is_registered_in_console_app(tmp_path):
    from tests.test_console_web_api import _client

    with _client(tmp_path, spa_enabled=True, asset=b"<html>console</html>") as client:
        response = client.get("/api/console/dashboard/queues?state=queued")
        assert response.status_code == 200
        assert response.json()["counts"] == {"active": 0, "queued": 0}


def test_email_classifier_uses_subject_and_its_live_model(tmp_path):
    from app.task12_storage import insert_task12
    from app.run_storage import insert_run
    from app.web_api.dashboard import read_dashboard_queues

    store = AutoReplyStore(tmp_path / "test.db")
    with store._connect() as db:
        insert_task12(
            db,
            "email_agent_classification_tasks",
            dict(
                task_id="classify-1",
                channel="email",
                stable_message_identity="opaque-message-id",
                status="running",
                owner="classifier",
                lease_expires_at="2026-10-20 17:10:00",
                attempt_count=1,
                input_json='{"message":{"subject":"Customer proposal"}}',
            ),
        )
        insert_run(
            db,
            "agent_runtime_attempts",
            dict(
                workload_kind="email_classification",
                workload_key="classify-1",
                attempt_number=1,
                route_name="primary",
                runtime_kind="claude_cli",
                credential_mode="oauth",
                model="real-classifier-model",
                status="running",
                lease_owner="routed-classifier",
                lease_expires_at="2026-10-20 17:10:00",
                started_at="2026-10-20 16:58:00",
            ),
        )
        result = read_dashboard_queues(db, now=NOW)
    assert len(result["active"]) == 1
    row = result["active"][0]
    assert row["title"] == "Customer proposal"
    assert row["model"] == "real-classifier-model"


def test_meeting_waits_until_eligible_and_delivery_wait_remains_queued(tmp_path):
    from app.task8_storage import task8_insert_parts
    from app.web_api.dashboard import read_dashboard_queues

    store = AutoReplyStore(tmp_path / "test.db")
    with store._connect() as db:
        for key, phase in [
            ("101", "waiting"),
            ("102", "ready_to_send"),
            ("103", "waiting"),
        ]:
            parts = task8_insert_parts(
                "meeting_alignment_jobs",
                dict(
                    legacy_key=key,
                    meeting_id="meeting-" + key,
                    title="Meeting " + key,
                    source_json="{}",
                    participants_json="[]",
                    ended_at="2026-10-20 16:30:00",
                    eligible_at="2026-10-20 17:30:00" if key != "103" else "",
                    available_at="2026-10-20 17:45:00" if key == "103" else "",
                    status=phase,
                    locked_at=None,
                ),
            )
            db.execute(
                f"insert into tasks({parts.columns}) values({parts.values})",
                parts.parameters,
            )
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


def test_active_memory_retry_keeps_completed_attempt_count(tmp_path):
    from app.web_api.dashboard import read_dashboard_queues
    from app.action_storage import insert_task_delivery_action

    store = AutoReplyStore(tmp_path / "test.db")
    task = seed(store, "memory-active", status="done", attempts=1)
    with store._connect() as db:
        insert_task_delivery_action(
            db,
            "task_memory_write_events",
            dict(
                reply_task_id=task,
                execution_generation="initial",
                consumer_run_id=0,
                status="processing",
                attempts=1,
                lease_owner="memory",
                lease_expires_at="2026-10-20 17:10:00",
                started_at="2026-10-20 16:58:00",
            ),
        )
        result = read_dashboard_queues(db, now=NOW)
    assert result["active"][0]["retry_number"] == 1


def test_workbench_title_model_and_start_with_distinct_valid_owners(tmp_path):
    from app.task8_storage import task8_insert_parts
    from app.run_storage import insert_run
    from app.web_api.dashboard import read_dashboard_queues

    store = AutoReplyStore(tmp_path / "test.db")
    with store._connect() as db:
        parent = task8_insert_parts(
            "workbench_tasks",
            dict(
                legacy_key="wb-task", title="Investigate incident", runtime_kind="codex"
            ),
        )
        parent_id = db.execute(
            f"insert into tasks({parent.columns}) values({parent.values})",
            parent.parameters,
        ).lastrowid
        turn = task8_insert_parts(
            "workbench_turns",
            dict(
                legacy_key="wb-turn",
                parent_id=parent_id,
                client_request_id="opaque-request",
                task_sequence=1,
                user_text="start",
                status="running",
                lease_owner="executor-owner",
                lease_expires_at="2026-10-20 17:10:00",
                started_at="2026-10-20 16:58:00",
            ),
        )
        db.execute(
            f"insert into tasks({turn.columns}) values({turn.values})", turn.parameters
        )
        insert_run(
            db,
            "agent_runtime_attempts",
            dict(
                workload_kind="workbench",
                workload_key="wb-turn",
                attempt_number=1,
                route_name="primary",
                runtime_kind="codex_cli",
                credential_mode="oauth",
                model="real-workbench-model",
                status="running",
                lease_owner="routed-owner",
                lease_expires_at="2026-10-20 17:10:00",
                started_at="2026-10-20 16:58:00",
            ),
        )
        rows = read_dashboard_queues(db, now=NOW)["active"]
    assert len(rows) == 1
    assert rows[0]["title"] == "Investigate incident"
    assert rows[0]["model"] == "real-workbench-model"
    assert rows[0]["started_at"] == "2026-10-20T16:58:00+00:00"


def test_independent_okr_and_wechat_runtime_metadata(tmp_path):
    from app.task12_storage import insert_task12
    from app.run_storage import insert_run
    from app.web_api.dashboard import read_dashboard_queues

    store = AutoReplyStore(tmp_path / "test.db")
    with store._connect() as db:
        from app.task8_storage import task8_insert_parts

        parts = task8_insert_parts(
            "okr_review_requests",
            dict(
                legacy_key="901",
                conversation_id="okr",
                conversation_title="Review OKR",
                trigger_message_id="msg",
                trigger_sender="owner",
                trigger_text="review",
                period_label="Q4",
                period_start="2026-10-01",
                period_end="2026-12-31",
                status="processing",
            ),
        )
        db.execute(
            f"insert into tasks({parts.columns}) values({parts.values})",
            parts.parameters,
        )
        imported = insert_task12(
            db,
            "wechat_memory_import_jobs",
            dict(import_run_id="import", account_id="wechat", status="running"),
        )
        for workload, key in (
            ("structured", "901"),
            ("memory", f"wechat_memory_import_job:{imported.lastrowid}"),
        ):
            insert_run(
                db,
                "agent_runtime_attempts",
                dict(
                    workload_kind=workload,
                    workload_key=key,
                    attempt_number=1,
                    route_name="primary",
                    runtime_kind="codex_cli",
                    credential_mode="oauth",
                    model="independent-model",
                    status="running",
                    lease_owner="native-owner",
                    lease_expires_at="2026-10-20 17:10:00",
                    started_at="2026-10-20 16:58:00",
                ),
            )
        rows = read_dashboard_queues(db, now=NOW)["active"]
    assert len(rows) == 2
    assert all(row["model"] == "independent-model" for row in rows)
    assert (
        next(row for row in rows if row["source"] == "微信 Memory 导入")["retry_number"]
        is None
    )


def test_scheduled_command_shows_saved_name_and_first_claim_clock(tmp_path):
    from datetime import timedelta
    from app.state_storage import insert_state
    from app.web_api.dashboard import read_dashboard_queues

    store = AutoReplyStore(tmp_path / "scheduled.db")
    task = store.create_scheduled_task(name="处理新的日历邀请", command="calendar-invites-once",
        cron_expression="10 * * * * *", timezone_name="UTC", enabled=True, now=NOW)
    run = store.create_scheduled_task_run(task.id, trigger_kind="scheduled", scheduled_for=NOW - timedelta(minutes=10),
        now=NOW - timedelta(minutes=10))
    store.claim_scheduled_task_run(run.id, owner="worker", lease_seconds=600,
        now=NOW - timedelta(minutes=2))
    with store._connect() as db:
        db.execute("begin immediate")
        insert_state(db, "dispatcher_claim_leases", dict(adapter_name="scheduled", source_id=str(run.id),
            owner="worker", owner_pid=123, generation=3, lease_expires_at="2026-10-20T17:08:00+00:00",
            created_at="2026-10-20 16:58:00", updated_at="2026-10-20 16:59:55", terminal_at="", last_error=""))
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
