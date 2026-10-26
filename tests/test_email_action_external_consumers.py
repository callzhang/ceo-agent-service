"""Current Email action History, Attention and publication readers."""

from app.audit_web import _email_action_queue_snapshot, _queue_attention_rows, _queue_status_snapshots
from app.email_canonical_storage import (
    update_email_classification,
)
from app.email_store import DIRECT_ACTION_MAX_ATTEMPTS, EmailStore
from app.store import AutoReplyStore
from app.web_api.dashboard import register_dashboard_routes
from app.web_api.registration import register_console_routes
from fastapi import FastAPI
from fastapi.testclient import TestClient



def _two_failed_actions(path):
    """Two current processed classifications whose direct Actions failed for good."""
    from app.email_classifier_contracts import EmailAction, EmailClassificationStatus
    from tests.support.native_email_actions import fail_action, persist_classification

    store = AutoReplyStore(path)
    email = EmailStore(path)
    for classification_id, action in ((11, EmailAction.ARCHIVE), (12, EmailAction.FLAG_IMPORTANT)):
        plan_id = persist_classification(
            email, classification_id=classification_id,
            status=EmailClassificationStatus.PROCESSED, action=action, account_id="account",
        )
        fail_action(email, email.direct_action_ids_for_plan(plan_id)[0],
                    attempts=1, retryable_last=False)
    return store


def _current_direct_action_store(path):
    from app.email_classifier_contracts import EmailAction, EmailClassificationStatus
    from app.shared_action_lifecycle import record_direct_email_action_outcome
    from tests.test_email_store import _classification

    store = AutoReplyStore(path)
    email = EmailStore(path)
    ids = []
    for classification_id, action in (
        (301, EmailAction.ARCHIVE),
        (302, EmailAction.FLAG_IMPORTANT),
        (303, EmailAction.MARK_READ),
    ):
        classification = _classification(
            status=EmailClassificationStatus.PROCESSED,
            classification_id=classification_id,
            message_id=f"dashboard-{classification_id}",
            actions=(action,),
            action_parameters={},
        )
        email.persist_scan_result(classification, model_text="synthetic")
        ids.extend(email.direct_action_ids_for_plan(
            classification.action_plan.action_plan_id
        ))
    with email._connect() as db:
        record_direct_email_action_outcome(
            db, action_id=ids[1], expected_status="pending",
            expected_started_at=None, expected_attempt_count=0, attempt_number=1,
            status="failed", provider_operation="move", provider_target="INBOX/Archive",
            provider_result_id=None, error="provider_failed",
            started_at="2026-10-20T16:57:00+00:00",
            finished_at="2026-10-20T16:58:00+00:00",
            next_attempt_at="2026-10-20T16:59:00+00:00",
            updated_at="2026-10-20T16:58:00+00:00",
        )
        for attempt_number in range(1, DIRECT_ACTION_MAX_ATTEMPTS + 1):
            record_direct_email_action_outcome(
                db, action_id=ids[2],
                expected_status="pending" if attempt_number == 1 else "failed",
                expected_started_at=None, expected_attempt_count=attempt_number - 1,
                attempt_number=attempt_number, status="failed",
                provider_operation="mark_read", provider_target="INBOX/Message",
                provider_result_id=None, error="provider_failed",
                started_at=f"2026-10-20T16:5{attempt_number}:00+00:00",
                finished_at=f"2026-10-20T16:5{attempt_number}:30+00:00",
                next_attempt_at=f"2026-10-20T16:5{attempt_number + 1}:00+00:00",
                updated_at=f"2026-10-20T16:5{attempt_number}:30+00:00",
            )
    return store, tuple(ids)


def test_dashboard_queue_reads_current_actions_and_preserves_email_contract(tmp_path):

    store, action_ids = _current_direct_action_store(tmp_path / "dashboard.sqlite3")
    with store._connect() as db:
        snapshot = _email_action_queue_snapshot(db)
    assert snapshot["pending"] == 1
    assert snapshot["failed"] == 1
    assert snapshot["retryable"] == 1
    app = FastAPI()
    register_dashboard_routes(app, store_factory=lambda: store)

    response = TestClient(app).get("/api/console/dashboard/queues?state=queued")

    assert response.status_code == 200
    data = response.json()
    email_rows = [row for row in data["items"] if row["source"] == "邮件操作"]
    assert {row["id"] for row in email_rows} == {
        f"email_actions:{action_ids[0]}", f"email_actions:{action_ids[1]}",
    }
    assert {row["id"]: row["status"] for row in email_rows} == {
        f"email_actions:{action_ids[0]}": "pending",
        f"email_actions:{action_ids[1]}": "failed",
    }
    assert {row["id"]: row["title"] for row in email_rows} == {
        f"email_actions:{action_ids[0]}": "archive",
        f"email_actions:{action_ids[1]}": "flag_important",
    }
    assert all(row["detail_url"] == "" for row in email_rows)


def test_email_attention_id_matches_strict_worker_status_contract(tmp_path):
    from tests.test_console_status_response import _status_payload

    store, action_ids = _current_direct_action_store(tmp_path / "attention-current.sqlite3")
    rows = [row for row in _queue_attention_rows(store) if row["category"] == "Email action"]
    email_row = next(row for row in rows if row["summary"] == "INBOX/Message")

    assert email_row["id"] == str(action_ids[2])
    payload = _status_payload()
    payload["attention_rows"] = rows
    app = FastAPI()
    register_console_routes(
        app, lambda: store, status_payload_factory=lambda: payload,
        feedback_backlog_factory=lambda: [], attention_rows_factory=lambda: rows,
    )
    response = TestClient(app).get("/api/console/status")
    assert response.status_code == 200
    assert next(row for row in response.json()["item"]["attention_rows"]
                if row["category"] == "Email action")["id"] == str(action_ids[2])


def test_direct_email_history_locator_uses_current_action_owner_id(tmp_path):
    from app.email_store import EMAIL_DIRECT_ACTION_LOCATOR_SQL

    store, action_ids = _current_direct_action_store(tmp_path / "history-current.sqlite3")

    with store._connect() as db:
        rows = db.execute(
            f"select action_id from ({EMAIL_DIRECT_ACTION_LOCATOR_SQL}) order by action_id"
        ).fetchall()
    logs = store.list_operation_logs(source_tables=("actions",))

    assert [row[0] for row in rows] == sorted(action_ids)
    assert {row.source_id for row in logs}.issuperset(action_ids)
    assert all(row.id == f"action:{row.source_id}" for row in logs)


def test_history_lists_failed_email_actions_by_native_id_and_pages_them(tmp_path):
    store = _two_failed_actions(tmp_path / "history.sqlite3")
    total, everything = store.list_operation_logs_with_count(
        limit=10, source_tables=("actions",), statuses=("failed",),
    )
    assert total == 2
    assert all(item.id == f"action:{item.source_id}" for item in everything)
    assert all(item.status == "failed" for item in everything)
    total, page = store.list_operation_logs_with_count(
        limit=1, offset=1, statuses=("failed",), source_tables=("actions",),
    )
    assert total == 2
    assert [item.source_id for item in page] == [everything[1].source_id]


def test_complete_status_and_maintenance_paths_accept_current_target(tmp_path):
    from app.deploy_maintenance import check_maintenance
    from app.quality_gate import scan_hourly_quality

    path = tmp_path / "complete.sqlite3"
    store = _two_failed_actions(path)
    snapshots = _queue_status_snapshots(store)
    assert next(row for row in snapshots if row["name"] == "Email provider actions")["failed"] == 2
    assert check_maintenance(path, ()) is not None
    assert scan_hourly_quality(path).missing_sources == ()


def test_current_plan_poll_and_retry_window_on_target(tmp_path, monkeypatch):
    from app.email_classifier_contracts import EmailAction, EmailClassificationStatus
    from tests.test_email_store import _classification

    path = tmp_path / "claim.sqlite3"
    email = EmailStore(path)
    classification = _classification(
        status=EmailClassificationStatus.PROCESSED, classification_id=301,
        actions=(EmailAction.ARCHIVE,), action_parameters={},
    )
    email.persist_scan_result(classification, model_text="synthetic")
    action_id = email.direct_action_ids_for_plan(classification.action_plan.action_plan_id)[0]
    from app.shared_action_lifecycle import record_direct_email_action_outcome

    with email._connect() as db:
        record_direct_email_action_outcome(
            db, action_id=action_id, expected_status="pending",
            expected_started_at=None, expected_attempt_count=0, attempt_number=1,
            status="failed", provider_operation="archive", provider_target="INBOX",
            provider_result_id=None, error="provider_failed",
            started_at="2026-10-14T11:00:00+00:00", finished_at="2026-10-14T11:01:00+00:00",
            next_attempt_at="2026-10-14T12:00:00+00:00", updated_at="2026-10-14T11:01:00+00:00",
        )
    store = AutoReplyStore(path)
    for number in range(1200):
        store.record_sent_reply(f"unrelated-{number}", f"message-{number}", "sent")
    statements = []
    original_open = email._open_connection

    def traced_connection(*, foreign_keys=True):
        db = original_open(foreign_keys=foreign_keys)
        db.set_trace_callback(statements.append)
        return db

    monkeypatch.setattr(email, "_open_connection", traced_connection)
    assert email.claim_next_direct_action(claimed_at="2026-10-14T11:59:00+00:00") is None
    poll = next(sql for sql in statements if "select distinct a.classification_id" in sql)
    with email._connect() as db:
        plan = [row[3] for row in db.execute("explain query plan " + poll)]
        steps = 0

        def count_steps():
            nonlocal steps
            steps += 1000
            return steps > 300_000

        db.set_progress_handler(count_steps, 1000)
        try:
            assert len(db.execute(poll).fetchall()) == 1
        finally:
            db.set_progress_handler(None, 0)
    assert any("idx_actions_email_public_key (record_type=?)" in step for step in plan), plan
    assert any("sqlite_autoindex_emails_1 (classification_id=?)" in step for step in plan), plan
    assert not any(step.startswith("SCAN c") for step in plan), plan
    assert steps < 300_000
    claimed = email.claim_next_direct_action(claimed_at="2026-10-14T12:01:00+00:00")
    assert claimed is not None and claimed.action_id == action_id

    other = _classification(
        status=EmailClassificationStatus.PROCESSED, classification_id=302,
        message_id="retired", actions=(EmailAction.ARCHIVE,), action_parameters={},
    )
    email.persist_scan_result(other, model_text="synthetic")
    with email._connect() as db:
        assert update_email_classification(db, 302, {"current_action_plan_id": None}) == 1
    assert email.claim_next_direct_action(claimed_at="2026-10-14T12:01:00+00:00") is None
