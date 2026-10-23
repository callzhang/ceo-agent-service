import sqlite3
from contextlib import contextmanager

import pytest

from app.store import AutoReplyStore, MEETING_JOB_SQL
from app.task_run_entity_assembly import task_run_entity_assembly_objects


class _AssembledMeetingStore(AutoReplyStore):
    def __init__(self, path):
        self.path = path
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute("pragma foreign_keys=on")
        from app.task8_storage import register_task8_functions
        register_task8_functions(self.db)
        objects = task_run_entity_assembly_objects()
        for ddl in objects.values():
            if ddl.lstrip().lower().startswith("create table"):
                self.db.execute(ddl)
        for ddl in objects.values():
            if not ddl.lstrip().lower().startswith("create table"):
                self.db.execute(ddl)

    @contextmanager
    def _connect(self):
        yield self.db

    @contextmanager
    def _immediate_write_transaction(self):
        self.db.execute("begin immediate")
        try:
            yield self.db
        except BaseException:
            self.db.rollback()
            raise
        else:
            self.db.commit()

    @contextmanager
    def _agent_run_write_transaction(self, _now):
        self.db.execute("begin immediate")
        try:
            yield self.db, (None, "now")
        except BaseException:
            self.db.rollback()
            raise
        else:
            self.db.commit()


def test_meeting_writer_claim_update_and_delivery_identity_use_canonical_task_id(tmp_path):
    store = _AssembledMeetingStore(tmp_path / "assembled-target.sqlite3")
    # The frozen source reader is the actual pre-cutover call-site query.
    with pytest.raises(sqlite3.OperationalError, match="no such column: legacy_key"):
        store.db.execute(f"select * from ({MEETING_JOB_SQL}) limit 1").fetchall()
    # This unrelated native Task makes the Meeting ID differ from every source key.
    store.db.execute("insert into tasks(kind,status,parameters_json,created_at,updated_at) "
                     "values('reply','pending',jsonb('{}'),'now','now')")
    store.db.commit()
    task_id = store.upsert_meeting_alignment_job(
        meeting_id="meeting-native", title="Review", source_json="{}", participants_json="[]",
        ended_at="2026-10-23T10:00:00", eligible_at="2026-10-23T10:01:00", status="pending",
    )
    assert task_id == 2
    assert store.get_meeting_delivery_key(task_id) == "meeting-alignment:2:meeting-native"
    assert store.get_meeting_alignment_job(task_id).id == task_id
    claimed = store.claim_meeting_alignment_jobs(1, "2026-10-23T10:02:00")
    assert [job.id for job in claimed] == [task_id]
    assert claimed[0].status == "processing" and claimed[0].attempts == 1
    run_id = store.claim_professional_run(task_id)
    store.finish_professional_run(run_id, status="ready_to_send", session_id="session-1",
                                  transcript_start=3, transcript_end=9)
    run = store.get_meeting_alignment_run(run_id)
    assert run is not None
    assert (run.id, run.task_id, run.job_id, run.codex_session_id) == (run_id, task_id, None, "session-1")
    assert (run.codex_transcript_start_line, run.codex_transcript_end_line, run.status) == (3, 9, "ready_to_send")
    assert store.list_meeting_alignment_runs(task_id)[0].id == run_id
    store.update_meeting_alignment_job(task_id, status="ready_to_send", decision_json="{}", final_message="summary")
    saved = store.get_meeting_alignment_job(task_id)
    assert saved.status == "ready_to_send"
    assert saved.final_message == "summary"
    assert store.db.execute("select business_key from task_business_keys where task_id=?", (task_id,)).fetchone()[0] == "meeting-alignment:2:meeting-native"
    assert tuple(store.db.execute("select decision_json,final_message from task_results where task_id=? and result_kind='meeting_alignment'", (task_id,)).fetchone()) == ("{}", "summary")


def test_ready_delivery_claims_are_native_separate_from_analysis_and_release_by_token(tmp_path):
    store = _AssembledMeetingStore(tmp_path / "assembled-delivery-claims.sqlite3")
    store.db.execute("insert into tasks(kind,status,parameters_json,created_at,updated_at) "
                     "values('reply','pending',jsonb('{}'),'now','now')")
    store.db.commit()
    task_ids = [store.upsert_meeting_alignment_job(
        meeting_id=f"ready-{number}", title="Review", source_json="{}", participants_json="[]",
        ended_at="2026-10-23T10:00:00", eligible_at="2026-10-23T10:01:00", status="ready_to_send",
    ) for number in range(3)]
    assert task_ids == [2, 3, 4]

    bulk = store.claim_ready_to_send_meeting_alignment_jobs(2, "2026-10-23T10:02:00")
    assert [job.id for job in bulk] == task_ids[:2]
    assert all(job.status == "ready_to_send" and job.locked_at and job.delivery_claim_token for job in bulk)
    assert store.claim_meeting_alignment_jobs(10, "2026-10-23T10:02:00") == []
    assert [row[0] for row in store.db.execute(
        "select td_claim_job_id from actions where record_type='meeting_alignment_delivery_claims' order by td_claim_job_id"
    )] == task_ids[:2]

    store.release_ready_to_send_meeting_alignment_claims(bulk)
    assert all(store.get_meeting_alignment_job(task_id).locked_at is None for task_id in task_ids[:2])
    assert store.db.execute("select count(*) from actions where record_type='meeting_alignment_delivery_claims'").fetchone()[0] == 0

    single = store.claim_ready_to_send_meeting_alignment_job(task_ids[0], now="2026-10-23T10:02:00")
    assert single is not None and single.id == task_ids[0] and single.locked_at
    retried = store.schedule_ready_to_send_meeting_alignment_retry(
        single.id, error='{"kind":"provider_timeout"}', available_at="2026-10-23T10:03:00",
    )
    assert retried.status == "ready_to_send" and retried.attempts == 1
    assert retried.locked_at is None and retried.available_at == "2026-10-23T10:03:00"
    store.release_ready_to_send_meeting_alignment_claims([single])
    assert store.db.execute("select count(*) from actions where record_type='meeting_alignment_delivery_claims'").fetchone()[0] == 0
    assert store.claim_ready_to_send_meeting_alignment_job(task_ids[0], now="2026-10-23T10:02:30") is None
    assert store.claim_ready_to_send_meeting_alignment_job(task_ids[1], now="2026-10-23T10:02:30") is not None
    reset = store.reset_ready_to_send_meeting_alignment_jobs()
    assert {job.id for job in reset} == {task_ids[1]}


def test_meeting_sender_receipt_interruption_retries_same_saved_key_and_uuid_once(tmp_path):
    from uuid import NAMESPACE_URL, uuid5

    from app.meeting_alignment_delivery import deliver_meeting_alignment
    from app.service_message_sender import ServiceMessageSender
    from tests.test_meeting_alignment_delivery import FakeDws, meeting_source, send_decision

    class IdempotentDws(FakeDws):
        def __init__(self):
            super().__init__()
            self.calls = []
            self.effects = {}

        def send_message(self, conversation_id, text, **kwargs):
            request_id = kwargs["idempotency_uuid"]
            self.calls.append((request_id, conversation_id, text))
            if request_id not in self.effects:
                self.effects[request_id] = {
                    "success": True,
                    "result": {"openMessageId": f"message-{len(self.effects) + 1}"},
                }
            return self.effects[request_id]

    store = _AssembledMeetingStore(tmp_path / "assembled-sender.sqlite3")
    store.db.execute("insert into tasks(kind,status,parameters_json,created_at,updated_at) "
                     "values('reply','pending',jsonb('{}'),'now','now')")
    store.db.commit()
    task_id = store.upsert_meeting_alignment_job(
        meeting_id="minutes-1", title="Review", source_json="{}", participants_json="[]",
        ended_at="2026-10-23T10:00:00", eligible_at="2026-10-23T10:01:00", status="ready_to_send",
    )
    key = store.get_meeting_delivery_key(task_id)
    dws = IdempotentDws()
    sender = ServiceMessageSender(store=store, dingtalk=dws)
    store.db.execute("create temp trigger interrupt_meeting_receipt before insert on action_receipts "
                     "begin select raise(abort,'simulated process interruption before receipt commit'); end")
    with pytest.raises(sqlite3.IntegrityError, match="simulated process interruption"):
        deliver_meeting_alignment(send_decision(), meeting_source(), dws,
                                  message_sender=sender, delivery_key=key)
    assert len(dws.effects) == 1
    assert store.get_outbound_postfix_receipt("dingtalk", key) is None
    prepared = store.get_outbound_postfix("dingtalk", key)
    assert prepared is not None
    store.db.execute("drop trigger interrupt_meeting_receipt")

    delivered = deliver_meeting_alignment(send_decision(), meeting_source(), dws,
                                          message_sender=sender, delivery_key=key)
    replay = deliver_meeting_alignment(send_decision(), meeting_source(), dws,
                                       message_sender=sender, delivery_key=key)
    expected_uuid = str(uuid5(NAMESPACE_URL, f"dingtalk:{key}"))
    assert [call[0] for call in dws.calls] == [expected_uuid, expected_uuid]
    assert len(dws.effects) == 1
    assert store.get_outbound_postfix_receipt("dingtalk", key) == delivered.send_result == replay.send_result
    assert delivered.message_text == replay.message_text == prepared.final_body


def test_failed_delivery_requeue_uses_native_saved_key_and_preserves_review(tmp_path):
    from tests.test_meeting_alignment_delivery import send_decision

    store = _AssembledMeetingStore(tmp_path / "assembled-requeue.sqlite3")
    store.db.execute("insert into tasks(kind,status,parameters_json,created_at,updated_at) "
                     "values('reply','pending',jsonb('{}'),'now','now')")
    store.db.commit()
    task_id = store.upsert_meeting_alignment_job(
        meeting_id="meeting-requeue", title="Review", source_json="{}", participants_json="[]",
        ended_at="2026-10-23T10:00:00", eligible_at="2026-10-23T10:01:00", status="failed",
    )
    decision = send_decision()
    store.update_meeting_alignment_job(task_id, decision_json=decision.model_dump_json(),
        target_kind="group", target_id="group-native", target_title="Group",
        final_message=decision.final_message, error="provider unavailable")
    key = store.get_meeting_delivery_key(task_id)
    assert key == f"meeting-alignment:{task_id}:meeting-requeue"
    reopened = store.requeue_failed_meeting_delivery(task_id)
    assert reopened is not None and reopened.status == "ready_to_send"
    assert reopened.decision_json == decision.model_dump_json()
    assert (reopened.target_kind, reopened.target_id, reopened.final_message) == (
        "group", "group-native", decision.final_message,
    )
    assert store.requeue_failed_meeting_delivery(task_id) is None


def test_confirmed_delivery_restore_requires_exact_native_saved_key(tmp_path):
    from app.meeting_alignment_delivery import MeetingDeliveryResult
    from tests.test_meeting_alignment_delivery import send_decision

    store = _AssembledMeetingStore(tmp_path / "assembled-confirmed-restore.sqlite3")
    store.db.execute("insert into tasks(kind,status,parameters_json,created_at,updated_at) "
                     "values('reply','pending',jsonb('{}'),'now','now')")
    store.db.commit()
    task_id = store.upsert_meeting_alignment_job(
        meeting_id="meeting-confirmed", title="Review", source_json="{}", participants_json="[]",
        ended_at="2026-10-23T10:00:00", eligible_at="2026-10-23T10:01:00", status="failed",
    )
    decision = send_decision()
    store.update_meeting_alignment_job(task_id, decision_json=decision.model_dump_json(),
        target_kind="group", target_id="group-native", target_title="Group",
        final_message=decision.final_message)
    key = store.get_meeting_delivery_key(task_id)
    prepared = store.prepare_meeting_alignment_message(delivery_key=key, body="Delivered body",
        original_text="", target_kind="group", target_id="group-native", feedback_base_url="")
    receipt = {"result": {"openMessageId": "confirmed-message"}}
    store.record_outbound_postfix_receipt("dingtalk", key, receipt, operation="send_message")
    job = store.get_meeting_alignment_job(task_id)
    result_json = MeetingDeliveryResult(target_kind="group", target_id="group-native",
        target_title="Group", resolved_mentions=[], unresolved_mention_names=[],
        send_result=receipt, message_text=prepared.final_body).model_dump_json()
    assert not store.restore_confirmed_meeting_delivery(job, key=f"{key}:wrong", prepared=prepared,
        receipt=receipt, result_json=result_json)
    assert store.get_meeting_alignment_job(task_id).status == "failed"
    assert store.restore_confirmed_meeting_delivery(job, key=key, prepared=prepared,
        receipt=receipt, result_json=result_json)
    restored = store.get_meeting_alignment_job(task_id)
    assert restored.status == "ready_to_send"
    assert restored.send_result_json == result_json
    assert restored.final_message == prepared.final_body
