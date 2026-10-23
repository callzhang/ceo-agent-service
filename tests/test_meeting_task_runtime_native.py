import sqlite3
from contextlib import contextmanager

import pytest

from app.store import AutoReplyStore, MEETING_JOB_SQL
from app.task_run_entity_assembly import task_run_entity_assembly_objects


class _AssembledMeetingStore(AutoReplyStore):
    def __init__(self):
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


def test_meeting_writer_claim_update_and_delivery_identity_use_canonical_task_id():
    store = _AssembledMeetingStore()
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
