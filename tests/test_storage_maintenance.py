from tests.support.legacy_todo_facts import seed_legacy_work_project, seed_legacy_work_update
import hashlib
import json


from app.storage_maintenance import (
    compact_done_email_classification_inputs,
    compact_settled_reply_inputs,
    compact_terminal_work_summary_inputs,
)
from app.store import AutoReplyStore
from app.email_store import EmailStore
from app.email_task_adapter import EmailClassificationTaskAdapter, EmailClassificationTaskInput


def test_historical_work_input_compaction_is_idempotent_and_preserves_adopted_rows(tmp_path):
    store = AutoReplyStore(tmp_path / "legacy.sqlite3")
    project_id = seed_legacy_work_project(store, title="Adopted project")
    update_id = seed_legacy_work_update(store,
        project_id=project_id, source_type="local_file", source_ref="file#v1",
        summary="Adopted business result",
    )
    payload = json.dumps({"source": {"type": "local_file", "ref": "file#v1",
                                     "created_at": "2026-01-01T00:00:00Z"},
                          "summary": "old copied input body"})
    terminal_id = store.enqueue_work_summary_input("local_file", "file#v1", payload)
    skipped_id = store.enqueue_work_summary_input("local_file", "file#skipped", payload)
    with store._connect() as db:
        db.execute("begin immediate")
        for input_id, values in (
            (terminal_id, {"status": "done"}),
            (skipped_id, {
                "status": "skipped", "attempts": 2, "error": "no task",
                "created_at": "2026-02-01 01:00:00",
                "updated_at": "2026-02-02 02:00:00",
            }),
        ):
            db.execute(
                "update tasks set " + ",".join(f"{column}=?" for column in values)
                + " where kind='work_summary' and id=?",
                (*values.values(), input_id),
            )
    native_run_id = store.create_professional_run(
        terminal_id, session_id="native-session", status="completed",
    )
    pending_id = store.enqueue_work_summary_input("local_file", "file#pending", payload)
    failed_id = store.enqueue_work_summary_input("local_file", "file#failed", payload)
    store.mark_work_summary_input_failed(failed_id, "retryable")

    first = compact_terminal_work_summary_inputs(store.path)
    second = compact_terminal_work_summary_inputs(store.path)
    assert first == {"rows_compacted": 2,
                     "logical_bytes_removed": 2 * (len(payload.encode("utf-8")) - 2)}
    assert second == {"rows_compacted": 0, "logical_bytes_removed": 0}
    terminal = store.get_work_summary_input(terminal_id)
    assert terminal.payload_json == "{}"
    assert terminal.source_created_at == "2026-01-01T00:00:00Z"
    assert terminal.body_sha256 == hashlib.sha256(payload.encode("utf-8")).hexdigest()
    assert terminal.body_bytes == len(payload.encode("utf-8"))
    skipped = store.get_work_summary_input(skipped_id)
    assert skipped.payload_json == "{}"
    assert (skipped.status, skipped.attempts, skipped.error,
            skipped.created_at, skipped.updated_at) == (
                "skipped", 2, "no task", "2026-02-01 01:00:00", "2026-02-02 02:00:00")
    assert store.get_work_summary_input(pending_id).payload_json == payload
    assert store.get_work_summary_input(failed_id).payload_json == payload
    with store._connect() as db:
        assert db.execute("select title from business where record_type='work_project' "
                          "and cast(legacy_key as integer)=?", (project_id,)).fetchone()[0] == "Adopted project"
        assert db.execute("select sync_summary from actions where record_type='work_updates' "
                          "and cast(legacy_key as integer)=?", (update_id,)).fetchone()[0] == "Adopted business result"
    assert store.get_professional_run(native_run_id, load_native=False)["session_id"] == "native-session"


def test_historical_reply_input_compaction_preserves_unsettled_rows(tmp_path):
    store = AutoReplyStore(tmp_path / "reply-maintenance.sqlite3")
    ids = {}
    for status in ("done", "skipped", "pending", "failed", "needs_human"):
        task = store.ensure_reply_task(
            conversation_id=f"cid-{status}", conversation_title="Source",
            single_chat=True, trigger_message_id=f"msg-{status}",
            trigger_create_time="2026-10-10 10:00:00", trigger_sender="Sender",
            trigger_text=f"original {status} " + "body " * 300,
            trigger_message_json=json.dumps({"content": f"original {status} " + "body " * 300}),
        )
        ids[status] = task.id
        with store._connect() as db:
            db.execute("update tasks set status=? where kind='reply' and id=?", (status, task.id))
    first = compact_settled_reply_inputs(store.path)
    second = compact_settled_reply_inputs(store.path)
    assert first["tasks_compacted"] == 2
    assert first["inputs_compacted"] == 2
    assert first["logical_bytes_removed"] > 0
    assert second == {"tasks_compacted": 0, "inputs_compacted": 0,
                      "attempts_cleared": 0, "logical_bytes_removed": 0}
    for status, task_id in ids.items():
        task = store.get_reply_task(task_id)
        assert task is not None and task.status == status
        if status in {"done", "skipped"}:
            assert task.input_compacted and task.trigger_text == ""
        else:
            assert not task.input_compacted and task.trigger_text == f"original {status} " + "body " * 300


def test_historical_done_email_classification_compaction_is_idempotent(tmp_path):
    database = tmp_path / "email-legacy.sqlite3"
    store = EmailStore(database)
    adapter = EmailClassificationTaskAdapter(store)
    tasks = {}
    for uid, status in enumerate(("done", "pending", "running", "failed"), start=100):
        task_input = EmailClassificationTaskInput.from_message(
            {"accountId": "account-primary", "folder": "INBOX",
             "uidValidity": 42, "uid": uid, "providerUnread": True,
             "messageId": f"<mail-{uid}@example.com>",
             "textBody": "original email body " * 500},
            allowed_category_keys=("work",),
            category_descriptions={"work": {"core": "Business."}},
            folder_targets={"work": "Work"}, config_version="config-v1",
            unsubscribe_candidates=(),
        )
        task = adapter.ensure_task(task_input)
        tasks[status] = task
        with store._connect() as db:
            db.execute(
                "update tasks set status=?, lease_owner=?, lease_expires_at=? where id=?",
                (status, "worker" if status == "running" else "",
                 "2099-01-01T00:00:00+00:00" if status == "running" else "",
                 task.task_id),
            )
    first = compact_done_email_classification_inputs(database)
    second = compact_done_email_classification_inputs(database)
    assert first["tasks_compacted"] == 1
    assert first["logical_bytes_removed"] > 0
    assert second == {"tasks_compacted": 0, "logical_bytes_removed": 0}
    assert json.loads(adapter.get_task(tasks["done"].task_id).input_json)["input_compacted"] is True
    for status in ("pending", "running", "failed"):
        assert adapter.get_task(tasks[status].task_id).input_json == tasks[status].input_json
    assert adapter.stable_provider_uids(
        account_id="account-primary", folder="INBOX", uidvalidity=42
    ) == frozenset({100, 101, 102})
    EmailStore(database)  # Full durable-row validation accepts historical compaction.
