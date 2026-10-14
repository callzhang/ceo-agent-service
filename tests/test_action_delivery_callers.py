"""Real Store18 sent callers preserve the delivery and feedback contracts."""

import sqlite3
import threading
import time
from contextlib import contextmanager

import pytest

from app.action_storage import SENT_REPLY_SQL, SENT_REPLY_OBSERVER_SQL, insert_action, update_action
from app.action_delivery_storage import ACTUAL_SOURCE
from app.store import AutoReplyStore
from tests.test_agent_turn_store import _claim_consumer, _task
from tests.test_store import _claim_audit_run, _enqueue_universal_reply_task


def test_sent_crud_preserves_raw_fields_public_ids_and_feedback_reads(tmp_path):
    store = AutoReplyStore(tmp_path / "delivery.sqlite3")
    store.record_sent_reply("conversation", "first", "Original", send_result_json=' {"raw": 1} ',
                            recall_key="key", feedback_token="token-a")
    first = store.get_sent_reply("conversation", "first")
    assert first is not None
    assert first.id == 1
    assert first.send_result_json == ' {"raw": 1} '
    assert (first.recall_key, first.recalled_at, first.agent_run_id) == ("key", None, 0)
    assert store.has_sent_reply_for_trigger("conversation", "first")
    assert store.list_sent_replies_after(0) == [first]
    assert store.list_sent_replies_with_feedback_tokens()[0].id == first.id
    assert store.list_sent_replies_waiting_for_feedback_events()[0].id == first.id

    store.update_sent_reply_recall(first.id, recall_status="recalled", recall_error="")
    recalled = store.get_sent_reply("conversation", "first")
    assert recalled is not None and recalled.recalled_at is not None
    with store._connect() as db:
        persisted_recalled_at = db.execute(
            f"select recalled_at from ({SENT_REPLY_SQL}) where id=?",
            (first.id,),
        ).fetchone()[0]
        assert recalled.recalled_at == persisted_recalled_at
        assert db.execute(f"select count(*) from ({SENT_REPLY_SQL})").fetchone()[0] == 1
        assert db.execute("select 1 from sqlite_master where name='sent_replies'").fetchone() is None
    store.update_sent_reply_recall(first.id, recall_status="failed", recall_error="provider")
    assert store.get_sent_reply("conversation", "first").recalled_at == recalled.recalled_at
    assert store.count_sent_replies() == 1
    assert store.max_sent_reply_id() == first.id


def test_duplicate_delivery_keeps_first_fact_records_new_observer_and_burns_id(tmp_path):
    store = AutoReplyStore(tmp_path / "observers.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    task = store.get_reply_task(task_id)
    assert task is not None
    first_run = _claim_audit_run(store, task_id, task.execution_generation, owner="first").run
    first = store.record_agent_message_delivery(
        agent_run_id=first_run.id, external_action_key="shared-send",
        conversation_id="original", trigger_message_id="m1", reply_text="Delivered",
        provider_result={"receipt": "first"},
    )
    store.enqueue_reply_task(
        conversation_id="second", conversation_title="Second", single_chat=False,
        trigger_message_id="m2", trigger_create_time="2026-10-13 00:00:00",
        trigger_sender="Derek", trigger_text="Second",
    )
    second_task = store.claim_reply_tasks(limit=1)[0]
    second_run = _claim_audit_run(store, second_task.id, second_task.execution_generation,
                                  owner="second").run
    duplicate = store.record_agent_message_delivery(
        agent_run_id=second_run.id, external_action_key="shared-send",
        conversation_id="different", trigger_message_id="m2", reply_text="Delivered",
        provider_result={"receipt": "later"},
    )
    assert duplicate == first
    with store._connect() as db:
        rows = db.execute(f"select * from ({SENT_REPLY_OBSERVER_SQL}) order by agent_run_id").fetchall()
        assert [(row["sent_reply_id"], row["agent_run_id"], row["reply_task_id"])
                for row in rows] == [(first.id, first_run.id, task_id),
                                    (first.id, second_run.id, second_task.id)]
        assert db.execute("select seq from sqlite_sequence where name='actions:sent_replies'").fetchone()[0] == 2
        assert db.execute("select 1 from sqlite_master where name='sent_reply_observers'").fetchone() is None
    store.record_sent_reply("next", "m3", "Later")
    assert store.get_sent_reply("next", "m3").id == 3
    assert store.max_sent_reply_id() == 3


def test_completed_delivery_conflict_rolls_back_sent_and_receipt(tmp_path):
    store = AutoReplyStore(tmp_path / "completed.sqlite3")
    task = _task(store)
    run = _claim_consumer(store, task).run
    store.complete_agent_run(run.id, {"outcome": "proposal"}, owner="consumer")
    values = dict(agent_run_id=run.id, external_action_key="completed-key",
                  business_object_key=task.business_object_key, action_identity="reply",
                  operation="message.send", target_identifiers={"conversation_id": task.conversation_id},
                  conversation_id=task.conversation_id, trigger_message_id=task.trigger_message_id,
                  reply_text="Delivered", provider_result={"receipt": "original"})
    sent = store.record_completed_agent_message_delivery(**values)
    assert sent.agent_run_id == run.id
    assert store.get_sent_reply(task.conversation_id, task.trigger_message_id) == sent
    with pytest.raises(ValueError, match="conflicting message delivery projection"):
        store.record_completed_agent_message_delivery(**(values | {"reply_text": "Different"}))
    with store._connect() as db:
        assert db.execute(f"select count(*) from ({SENT_REPLY_SQL})").fetchone()[0] == 1
        assert db.execute(f"select count(*) from ({SENT_REPLY_OBSERVER_SQL})").fetchone()[0] == 1
        assert db.execute("select seq from sqlite_sequence where name='actions:sent_replies'").fetchone()[0] == sent.id
        assert not db.execute("pragma foreign_key_check").fetchall()


def test_feedback_pressure_and_attempt_mapping_read_sent_projection(tmp_path):
    store = AutoReplyStore(tmp_path / "feedback.sqlite3")
    store.record_sent_reply("conversation", "old", "Old", feedback_token="old-token")
    store.record_sent_reply("conversation", "new", "New", feedback_token="new-token")
    with store._connect() as db:
        update_action(db, "sent_replies", 1, {"sent_at": "2026-05-30 12:00:00"})
        update_action(db, "sent_replies", 2, {"sent_at": "2026-06-02 12:00:00"})
    store.upsert_feedback_event(key="feedback", feedback_token="old-token", rating="up",
                                received_at="2026-06-01 12:00:00")
    stats = store.feedback_pressure_stats("conversation", now_utc="2026-06-12 12:00:00")
    assert stats.unanswered_since_last_feedback == 1
    assert stats.unanswered_older_than_7_days == 1
    assert store.list_sent_replies_waiting_for_feedback_events()[0].feedback_token == "new-token"
    assert store.list_sent_replies_with_feedback_tokens_for_conversation("conversation", limit=1)[0].id == 2


def test_recall_timestamp_is_taken_after_writer_lock_like_native_source(tmp_path):
    store = AutoReplyStore(tmp_path / "typed.sqlite3")
    store.record_sent_reply("conversation", "message", "Delivered")
    native_path = tmp_path / "native.sqlite3"
    with sqlite3.connect(native_path) as db:
        native_decl = next(obj["sql"] for obj in ACTUAL_SOURCE["sent_replies"]["objects"]
                           if obj["name"] == "sent_replies")
        db.execute(native_decl)
        db.execute("insert into sent_replies(id,conversation_id,trigger_message_id,reply_text) "
                   "values(1,'conversation','message','Delivered')")

    typed_lock = sqlite3.connect(store.path, timeout=10)
    native_lock = sqlite3.connect(native_path, timeout=10)
    typed_lock.execute("begin immediate")
    native_lock.execute("begin immediate")
    started = [threading.Event(), threading.Event()]
    failures = []

    def typed_update():
        started[0].set()
        try:
            store.update_sent_reply_recall(1, recall_status="recalled", recall_error="")
        except BaseException as exc:
            failures.append(exc)

    def native_update():
        started[1].set()
        try:
            with sqlite3.connect(native_path, timeout=10) as db:
                db.execute("update sent_replies set recalled_at=current_timestamp where id=1")
        except BaseException as exc:
            failures.append(exc)

    workers = [threading.Thread(target=typed_update), threading.Thread(target=native_update)]
    try:
        for worker in workers:
            worker.start()
        assert all(event.wait(2) for event in started)
        time.sleep(2.2)
        release_stamp = typed_lock.execute("select current_timestamp").fetchone()[0]
    finally:
        typed_lock.commit()
        native_lock.commit()
        typed_lock.close()
        native_lock.close()
        for worker in workers:
            worker.join(timeout=10)
    assert not failures
    assert all(not worker.is_alive() for worker in workers)
    typed_stamp = store.get_sent_reply("conversation", "message").recalled_at
    with sqlite3.connect(native_path) as db:
        native_stamp = db.execute("select recalled_at from sent_replies where id=1").fetchone()[0]
    assert native_stamp >= release_stamp
    assert typed_stamp >= release_stamp


def test_sent_external_key_caller_seeks_existing_partial_index(tmp_path, monkeypatch):
    store = AutoReplyStore(tmp_path / "key-plan.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    task = store.get_reply_task(task_id)
    assert task is not None
    run = _claim_audit_run(store, task_id, task.execution_generation, owner="plan").run
    original_connect = store._connect
    statements = []

    @contextmanager
    def traced_connect():
        with original_connect() as db:
            db.set_trace_callback(statements.append)
            yield db

    monkeypatch.setattr(store, "_connect", traced_connect)
    store.record_agent_message_delivery(
        agent_run_id=run.id, external_action_key="plan-key",
        conversation_id="conversation", trigger_message_id="message",
        reply_text="Delivered", provider_result={"receipt": "accepted"},
    )
    lookups = [sql for sql in statements if sql.startswith("select * from (select ")
               and "external_action_key='plan-key'" in sql]
    assert len(lookups) == 1
    with sqlite3.connect(store.path) as db:
        plan = [row[3] for row in db.execute("explain query plan " + lookups[0])]
    assert any("SEARCH a USING INDEX idx_actions_sent_external_key (external_action_key=?)" in step
               for step in plan)


@pytest.mark.parametrize("key", ["ordinary", " padded ", "nul\x00key"])
def test_nonempty_external_key_lookup_keeps_native_result(tmp_path, key):
    source_path = tmp_path / "source.sqlite3"
    with sqlite3.connect(source_path) as source:
        objects = ACTUAL_SOURCE["sent_replies"]["objects"]
        for obj in sorted(objects, key=lambda item: item["name"] != "sent_replies"):
            source.execute(obj["sql"])
        source.execute("insert into sent_replies(id,conversation_id,trigger_message_id,reply_text,external_action_key) "
                       "values(1,'conversation','message','Delivered',?)", (key,))
        native = source.execute("select id,reply_text from sent_replies where external_action_key=?",
                                (key,)).fetchone()
    store = AutoReplyStore(tmp_path / "target.sqlite3")
    with store._connect() as db:
        insert_action(db, "sent_replies", {
            "id": 1, "conversation_id": "conversation", "trigger_message_id": "message",
            "reply_text": "Delivered", "external_action_key": key,
        })
        projected = db.execute(
            f"select id,reply_text from ({SENT_REPLY_SQL}) "
            "where external_action_key=? and external_action_key<>''", (key,),
        ).fetchone()
    assert tuple(projected) == native
