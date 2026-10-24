"""Current Email action History, Attention and publication readers."""

import sqlite3

from app.action_storage import insert_action, insert_email_action, update_email_action
from app.audit_web import _email_action_queue_snapshot, _queue_attention_rows, _queue_status_snapshots
from app.email_canonical_storage import (
    insert_email_classification, insert_email_message, update_email_classification,
)
from app.email_store import EmailStore
from app.email_publication_sources import current_email_actions_available
from app.store import AutoReplyStore
from app.web_api.dashboard import register_dashboard_routes
from app.web_api.registration import register_console_routes
from fastapi import FastAPI
from fastapi.testclient import TestClient

pytest_plugins = ("tests.test_email_action_plan_direct_dao",)


def _two_failed_actions(path):
    store = AutoReplyStore(path)
    EmailStore(path)
    with store._connect() as db:
        insert_email_message(db, dict(
            account_id="account", stable_message_identity="message", folder="INBOX",
            uidvalidity=1, uid=1, rfc_message_id="", in_reply_to="",
            references_json="[]", thread_identity="", sender="Sender",
            recipients_json="[]", subject="Subject", normalized_text="",
            preview="", attachment_metadata_json="[]", received_at="",
            created_at="", updated_at="",
        ))
        insert_email_classification(db, dict(
            id=11, account_id="account", folder="INBOX", uidvalidity=1, uid=1,
            stable_message_identity="message", confidence=0.5, margin=0.1,
            probabilities_json="{}", model_id="model", config_version="config",
            status="processed", classification_source="model",
            current_action_plan_id="plan", sender="Sender", subject="Subject",
        ))
        insert_email_action(db, "email_action_plans", dict(
            action_plan_id="plan", action_plan_version=1, classification_id=11,
            account_id="account", category="work", classification_source="model",
            confidence=0.5, model_id="model", config_version="config",
            actions_json='["archive","move"]', action_parameters_json="{}",
            created_at="2026-10-14T00:00:00+00:00",
        ))
        for rowid, action_type, stamp in (
            (-7, "archive", "2026-10-14T01:00:00+00:00"),
            (42, "move", "2026-10-14T02:00:00+00:00"),
        ):
            insert_email_action(db, "email_actions", dict(
                action_id=None, action_plan_id="plan", classification_id=11,
                account_id="account", action_type=action_type,
                parameters_json="{}", config_version="config", status="failed",
                attempt_count=0, error="provider_failed", created_at=stamp,
                updated_at=stamp,
            ), source_rowid=rowid)
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
        for attempt_number in range(1, 4):
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
    from app.audit_web import _email_action_queue_snapshot

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


def test_history_keeps_native_signed_rowid_and_nullable_public_id(tmp_path):
    store = _two_failed_actions(tmp_path / "history.sqlite3")
    total, page = store.list_operation_logs_with_count(
        limit=1, source_tables=("email_actions",),
    )
    assert total == 2
    assert [(item.id, item.source_id, item.status) for item in page] == [
        ("email-action:42", 42, "failed")
    ]
    total, page = store.list_operation_logs_with_count(
        limit=1, offset=1, statuses=("failed",),
        source_tables=("email_actions",),
    )
    assert total == 2
    assert [(item.id, item.source_id) for item in page] == [("email-action:-7", -7)]
    with store._connect() as db:
        update_email_action(db, "email_actions", 42, {"status": "done"})
    total, page = store.list_operation_logs_with_count(
        limit=10, statuses=("failed",), source_tables=("email_actions",),
    )
    assert total == 1
    assert [item.source_id for item in page] == [-7]


def test_attention_and_status_keep_nullable_failed_actions(tmp_path):
    store = _two_failed_actions(tmp_path / "attention.sqlite3")
    rows = [row for row in _queue_attention_rows(store) if row["category"] == "Email action"]
    assert len(rows) == 2
    assert [row["id"] for row in rows] == ["None", "None"]
    with store._connect() as db:
        assert current_email_actions_available(db)
        assert _email_action_queue_snapshot(db)["failed"] == 2


def test_store_only_does_not_advertise_email_history_or_queue(tmp_path):
    store = AutoReplyStore(tmp_path / "store-only.sqlite3")
    assert store.count_operation_logs(source_tables=("email_actions",)) == 0
    with store._connect() as db:
        assert not current_email_actions_available(db)
    assert not [row for row in _queue_attention_rows(store)
                if row["category"] == "Email action"]


def test_maintenance_reads_current_email_action_source(tmp_path):
    from app.email_publication_sources import publication_email_action_source

    path = tmp_path / "maintenance.sqlite3"
    _two_failed_actions(path)
    with sqlite3.connect(path) as db:
        source = publication_email_action_source(db)
        assert db.execute(f"select count(*) from ({source}) where status='failed'").fetchone() == (2,)


def test_complete_status_and_maintenance_paths_accept_current_target(tmp_path):
    from app.deploy_maintenance import check_maintenance
    from app.quality_gate import scan_hourly_quality

    path = tmp_path / "complete.sqlite3"
    store = _two_failed_actions(path)
    snapshots = _queue_status_snapshots(store)
    assert next(row for row in snapshots if row["name"] == "Email provider actions")["failed"] == 2
    assert check_maintenance(path, ()) is not None
    assert scan_hourly_quality(path).missing_sources == ()


def test_generic_publication_readers_accept_store_only_target(tmp_path):
    from app.action_postfix_schema_release import publication_postfix_sources
    from app.action_delivery_schema_release import publication_delivery_sources
    from app.action_receipt_schema_release import publication_receipt_sources
    from app.action_schema_release import publication_candidate_source
    from app.scheduled_config_storage import publication_scheduled_source
    from app.task_schema_release import publication_meeting_sources, publication_task_sources

    path = tmp_path / "store-only.sqlite3"
    AutoReplyStore(path)
    with sqlite3.connect(path) as db:
        assert db.execute(f"select count(*) from ({publication_task_sources(db)[0]})").fetchone() == (0,)
        assert db.execute(f"select count(*) from ({publication_meeting_sources(db)[0]})").fetchone() == (0,)
        assert db.execute(f"select count(*) from ({publication_candidate_source(db)})").fetchone() == (0,)
        assert db.execute(f"select count(*) from ({publication_scheduled_source(db)})").fetchone() == (0,)
        for sources in (publication_receipt_sources(db), publication_delivery_sources(db), publication_postfix_sources(db)):
            assert all(db.execute(f"select count(*) from ({sql})").fetchone() == (0,)
                       for sql in sources.values())


def test_publication_email_quiet_source_accepts_genuine_old_pair(converted_pair):
    from app.email_publication_sources import publication_email_action_source

    _converted, _classification, action_ids, original, _old_code = converted_pair
    with sqlite3.connect(original) as db:
        source = publication_email_action_source(db)
        assert db.execute(f"select count(*) from ({source})").fetchone() == (len(action_ids),)


def test_genuine_source_history_rowids_survive_real_pair_and_mixed_pagination(converted_pair):
    from pathlib import Path
    from app.store_schema_release import prepare_database_schema_release

    _converted, _classification, action_ids, source, _old_code = converted_pair
    with sqlite3.connect(source) as db:
        before = db.execute(
            "select rowid,action_id,status,updated_at from email_actions order by rowid"
        ).fetchall()
    release = prepare_database_schema_release(Path(__file__).resolve().parents[1], source)
    release.publish()
    store = AutoReplyStore(source)
    EmailStore(source)
    total, rows = store.list_operation_logs_with_count(
        limit=10, source_tables=("email_actions",),
    )
    assert total == len(before) == len(action_ids)
    assert {row.source_id for row in rows} == {row[0] for row in before}
    assert {row.id for row in rows} == {f"email-action:{row[0]}" for row in before}
    assert {row.status for row in rows} == {row[2] for row in before}
    assert all(row.context and row.summary for row in rows)

    store.record_reply_attempt(
        conversation_id="mixed", conversation_title="Mixed history",
        trigger_message_id="mixed-1", trigger_sender="sender",
        trigger_text="older family", action="agent_run",
        sensitivity_kind="general", send_status="skipped",
    )
    source_tables = ("email_actions", "reply_attempts")
    full_total, full = store.list_operation_logs_with_count(
        limit=20, source_tables=source_tables,
    )
    assert full_total == len(before) + 1
    assert {row.source_table for row in full} == set(source_tables)
    paged = []
    for offset in range(full_total):
        page_total, page = store.list_operation_logs_with_count(
            limit=1, offset=offset, source_tables=source_tables,
        )
        assert page_total == full_total
        paged.extend(page)
    assert [(row.source_table, row.source_id) for row in paged] == [
        (row.source_table, row.source_id) for row in full
    ]
    with store._connect() as db:
        update_email_action(db, "email_actions", before[0][0], {
            "status": "failed", "error": "provider_failed", "updated_at": "2026-10-14T04:00:00+00:00",
        })
    failed_total, failed = store.list_operation_logs_with_count(
        limit=10, statuses=("failed",), source_tables=source_tables,
    )
    assert failed_total == 1
    assert [(row.id, row.source_id, row.status) for row in failed] == [
        (f"email-action:{before[0][0]}", before[0][0], "failed")
    ]


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
    with email._connect() as db:
        rowid = db.execute(
            "select cast(legacy_key as integer) from actions "
            "where record_type='email_actions' and email_action_id=?", (action_id,)
        ).fetchone()[0]
        update_email_action(db, "email_actions", rowid, {
            "status": "failed", "next_attempt_at": "2026-10-14T12:00:00+00:00",
            "error": "provider_failed",
        })
        for number in range(1200):
            insert_action(db, "sent_replies", dict(
                conversation_id=f"unrelated-{number}",
                trigger_message_id=f"message-{number}", reply_text="sent",
            ))
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
    assert any("idx_actions_postfix_identity (record_type=?)" in step for step in plan), plan
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


def test_published_main_history_matches_target_for_mixed_failed_and_pages(
    converted_pair, tmp_path
):
    """The immediate published predecessor is the old-side History oracle."""
    import io
    import json
    import os
    from pathlib import Path
    import subprocess
    import sys
    import tarfile

    from app.store_schema_release import prepare_database_schema_release

    _converted, _classification, _action_ids, source, _old_code = converted_pair
    with sqlite3.connect(source) as db:
        failed_action_id = db.execute(
            "select action_id from email_actions where rowid=-7"
        ).fetchone()[0]
        db.execute(
            "insert into email_action_attempts (action_id,attempt_number,status,"
            "provider_operation,provider_target,provider_result_id,error,started_at,finished_at) "
            "values (?,1,'failed','READ','message','','source-failure','start','finish')",
            (failed_action_id,),
        )
        db.execute(
            "update email_actions set status='failed', attempt_count=1, "
            "provider_operation='READ', provider_target='message', "
            "started_at='start', finished_at='finish', error='source-failure' "
            "where rowid=-7"
        )
    published = tmp_path / "published-main-5e52cafc"
    published.mkdir()
    archive = subprocess.check_output(
        ["git", "archive", "5e52cafc8f209986868454d25ea5ba77a6be782c", "app"],
        cwd=Path(__file__).resolve().parents[1],
    )
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(published, filter="data")
    code = """
import json,sys
from pathlib import Path
from app.store import AutoReplyStore
from app.email_store import EmailStore
p=Path(sys.argv[1]); store=AutoReplyStore(p); EmailStore(p)
store.record_reply_attempt(conversation_id='mixed',conversation_title='Mixed',
    trigger_message_id='reply-1',trigger_sender='sender',trigger_text='old16',
    action='agent_run',sensitivity_kind='general',send_status='skipped')
def snap(statuses=None):
    sources=('email_actions','reply_attempts')
    total,all_rows=store.list_operation_logs_with_count(limit=20,statuses=statuses,
        source_tables=sources)
    pages=[]
    for offset in range(total):
        count,rows=store.list_operation_logs_with_count(limit=1,offset=offset,
            statuses=statuses,source_tables=sources)
        pages.append((count,[(r.id,r.source_table,r.source_id,r.status,r.context,
                            r.summary) for r in rows]))
    return total,[(r.id,r.source_table,r.source_id,r.status,r.context,r.summary)
                  for r in all_rows],pages
print(json.dumps({'all':snap(),'failed':snap(('failed',))},ensure_ascii=False))
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(source)], cwd=published,
        env={**os.environ, "PYTHONPATH": str(published)},
        text=True, capture_output=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    expected = json.loads(result.stdout.splitlines()[-1])

    release = prepare_database_schema_release(Path(__file__).resolve().parents[1], source)
    release.publish()
    store = AutoReplyStore(source)
    EmailStore(source)

    def target_snap(statuses=None):
        sources = ("email_actions", "reply_attempts")
        total, all_rows = store.list_operation_logs_with_count(
            limit=20, statuses=statuses, source_tables=sources,
        )
        pages = []
        for offset in range(total):
            count, rows = store.list_operation_logs_with_count(
                limit=1, offset=offset, statuses=statuses,
                source_tables=sources,
            )
            pages.append((count, [
                (r.id, r.source_table, r.source_id, r.status, r.context, r.summary)
                for r in rows
            ]))
        return total, [
            (r.id, r.source_table, r.source_id, r.status, r.context, r.summary)
            for r in all_rows
        ], pages

    actual = json.loads(json.dumps({
        "all": target_snap(), "failed": target_snap(("failed",)),
    }, ensure_ascii=False))
    assert all(len(page[1]) == 1 for page in actual["all"][2])
    assert actual == expected
