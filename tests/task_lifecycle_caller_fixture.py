"""Deterministic, local DAO calls executable unchanged by the archived source."""
from contextlib import closing
from datetime import datetime, timezone, timedelta
import itertools
from pathlib import Path
import sqlite3
from uuid import UUID


def configure_clock():
    import app.store as store
    import app.workbench.store as workbench

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            value = cls(2026, 10, 19, 12, tzinfo=timezone.utc)
            return value if tz is not None else value.replace(tzinfo=None)

    store.datetime = workbench.datetime = Clock
    ids = itertools.count(101)
    workbench.uuid4 = store.uuid4 = lambda: UUID(int=next(ids))
    original = sqlite3.connect

    def connect(*args, **kwargs):
        db = original(*args, **kwargs)
        db.create_function("current_timestamp", 0, lambda: "2026-10-19 12:00:00")
        return db

    sqlite3.connect = connect
    return Clock.now(timezone.utc)


def facts(path):
    """Exact physical SQLite cells, with current Task owners reconstructed."""
    from app.task20_definitions import TASK20_FIELDS, task20_projection
    with closing(sqlite3.connect(path)) as db:
        def encode(value):
            if isinstance(value, bytes):
                return ["blob", value.hex()]
            if isinstance(value, float):
                return ["real", value.hex()]
            return [type(value).__name__, value]

        def read(sql):
            return [[encode(v) for v in row] for row in db.execute(sql)]

        public = {kind: read("select " + ",".join('"' + f + '"' for f in fields)
                            + " from (" + task20_projection(kind) + ") order by "
                            + ",".join(str(i + 1) for i in range(len(fields))))
                  for kind, fields in TASK20_FIELDS.items()}
        physical = {}
        for (table,) in db.execute("select name from sqlite_schema where type='table' and name not like '%_fts%' order by name"):
            columns = [r[1] for r in db.execute('pragma table_xinfo("' + table + '")') if not r[6]]
            if table == "tasks" and "status" in columns:
                from app.task_lifecycle_schema import _inverse_payload
                from app.task_lifecycle_definitions import SOURCE_FIELDS
                from app.task_lifecycle_jsonb import register
                register(db)
                payload = ("case record_type " + " ".join(
                    "when " + repr(kind) + " then " + _inverse_payload(kind) for kind in SOURCE_FIELDS
                ) + " else payload_json end")
                select = "id,_kind,legacy_key," + payload + ",parent_id,current_input_id,claimed_input_id"
            else:
                select = ",".join('"' + f + '"' for f in columns)
            where = " where not(record_type='service_state' and key='store_schema_version')" if table == "state" else ""
            physical[table] = read("select rowid," + select + ' from "' + table + '"' + where + " order by rowid")
        return {"public": public, "physical": physical,
                "fk": [list(row) for row in db.execute("pragma foreign_key_check")]}


def exercise(path):
    """All nine executable kinds, source API transitions, no external clients."""
    from app.store import AutoReplyStore
    from app.email_store import EmailStore
    from app.workbench.store import WorkbenchStore
    from app.dispatcher.adapters import (
        ReplyQueueAdapter, ScheduledExecutionQueueAdapter, WorkSummaryQueueAdapter,
        MeetingQueueAdapter, OkrReviewQueueAdapter,
    )
    from app.email_task_adapter import EmailClassificationTaskAdapter, EmailClassificationTaskInput
    from tests.feedback_processing_fixtures import resolve_feedback_fixture

    now = configure_clock()
    store, email, workbench = AutoReplyStore(Path(path)), EmailStore(Path(path)), WorkbenchStore(Path(path))
    observations = []

    def observe(label):
        observations.append([label, facts(path)])

    def claim(adapter):
        result = adapter.claim(now, owner="worker", owner_pid=37, lease=timedelta(minutes=5))
        assert result is not None
        return result

    for channel, adapter_type in (("dingtalk", ReplyQueueAdapter), ("scheduled", ScheduledExecutionQueueAdapter)):
        args = dict(channel=channel, conversation_id=channel, conversation_title=channel,
                    single_chat=True, trigger_message_id="trigger", trigger_create_time="clock",
                    trigger_sender="source", trigger_text="original", execution_generation="business-generation")
        store.enqueue_reply_task(**args)
        adapter = adapter_type(store, owner_alive=lambda _: False)
        first = claim(adapter)
        observe(channel + "-claim")
        adapter.release(first, owner="worker", now=now)
        observe(channel + "-release")
        second = claim(adapter)
        # Same business generation, fresh input snapshot while the original is claimed.
        store.ensure_reply_task(**{**args, "trigger_text": "new input"})
        observe(channel + "-input")
        adapter.renew(second, owner="worker", now=now, lease=timedelta(minutes=5))
        adapter.handle_handler_error(second, owner="worker", now=now, error="handler-error")
        observe(channel + "-retry")

    summary_id = store.enqueue_work_summary_input("local_file", "source", '{"summary":"body"}')
    summary = WorkSummaryQueueAdapter(store, owner_alive=lambda _: False)
    first = claim(summary)
    observe("summary-claim")
    summary.release(first, owner="worker", now=now)
    observe("summary-release")
    second = claim(summary)
    run_id = store.begin_task_agent_run(summary_id)
    store.mark_work_summary_input_done(summary_id)
    observe("summary-domain-terminal-held-claim")
    summary.complete(second, owner="worker", now=now)
    assert run_id > 0

    meeting_id = store.upsert_meeting_alignment_job(meeting_id="meeting", title="Meeting",
        source_json="{}", participants_json="[]", ended_at="2026-10-19 11:00:00",
        eligible_at="2026-10-19 11:10:00", status="pending")
    meeting = MeetingQueueAdapter(store, owner_alive=lambda _: False)
    first = claim(meeting)
    meeting.release(first, owner="worker", now=now)
    second = claim(meeting)
    store.update_meeting_alignment_job(meeting_id, status="ready_to_send")
    observe("meeting-ready-held-consumer-claim")
    meeting.complete(second, owner="worker", now=now)
    assert meeting.claim(now, owner="other", owner_pid=18, lease=timedelta(minutes=5)) is None
    sending = store.claim_ready_to_send_meeting_alignment_job(meeting_id, now="2026-10-19 12:00:00")
    assert sending is not None
    observe("meeting-send-claimed")
    store.release_ready_to_send_meeting_alignment_claims([sending])
    sending = store.claim_ready_to_send_meeting_alignment_job(meeting_id, now="2026-10-19 12:00:00")
    assert sending is not None
    store.update_meeting_alignment_job(meeting_id, status="sent",
        send_result_json='{"status":"sent","message_id":"substitute-receipt"}')
    store.release_ready_to_send_meeting_alignment_claims([sending])
    protected = store.upsert_meeting_alignment_job(meeting_id="quarantine", title="Protected",
        source_json="{}", participants_json="[]", ended_at="2026-10-19 11:00:00",
        eligible_at="2026-10-19 11:10:00", status="quarantined")
    assert protected != meeting_id
    assert meeting.claim(now, owner="other", owner_pid=18, lease=timedelta(minutes=5)) is None
    observe("meeting-send-phase")

    request = store.create_okr_review_request(conversation_id="okr", conversation_title="OKR",
        trigger_message_id="okr", trigger_sender="sender", trigger_sender_user_id="manager",
        trigger_text="Review", period_label="Q4", period_start="2026-10-01",
        period_end="2026-12-31", okr_source_json="{}")
    okr = OkrReviewQueueAdapter(store, owner_alive=lambda _: False)
    first = claim(okr)
    okr.release(first, owner="worker", now=now)
    second = claim(okr)
    assert second.attempt == 2
    store.mark_okr_review_request_failed(request, "source-error")
    okr.record_lease_error(second, owner="worker", error="history-error", now=now)
    observe("okr-terminal-held-ledger")
    okr.complete(second, owner="worker", now=now)

    args = dict(week_end="2026-10-18", manager_user_id="manager", source_digest="a" * 64, now=now)
    weekly = store.begin_weekly_okr_analysis_job(**args, owner="first")
    observe("weekly-claimed")
    assert store.begin_weekly_okr_analysis_job(**args, owner="second").outcome == "in_progress"
    store.finish_weekly_okr_analysis_job(weekly.job_id, status="failed", error="source-error", owner="first", now=now)
    observe("weekly-failed")
    reopened = store.begin_weekly_okr_analysis_job(**args, owner="second")
    observe("weekly-reopened")
    try:
        store.finish_weekly_okr_analysis_job(reopened.job_id, status="completed", owner="first", now=now)
    except ValueError:
        pass
    else:
        raise AssertionError("stale weekly owner completed")
    store.finish_weekly_okr_analysis_job(reopened.job_id, status="completed", owner="second", now=now)
    assert store.begin_weekly_okr_analysis_job(**args, owner="third").outcome == "cache_hit"

    task = workbench.create_task(title="Task", runtime_kind="codex")
    turn = workbench.create_turn(task.id, user_text="Prepare", client_request_id="one")
    assert workbench.claim_next_turn(owner="workbench", now=now).id == turn.id
    observe("workbench-claimed")
    workbench.request_stop(turn.id, now=now)
    assert workbench.get_turn(turn.id).status == "stopped"
    observe("workbench-stopped")

    adapter = EmailClassificationTaskAdapter(email, now=lambda: now, retry_base_seconds=0)
    value = EmailClassificationTaskInput.from_message(
        {"accountId": "account", "folder": "INBOX", "uidValidity": 42, "uid": 7,
         "messageId": "<classification@example.test>", "providerUnread": True},
        allowed_category_keys=("work",), category_descriptions={"work": {"core": "Business"}},
        folder_targets={}, config_version="config", unsubscribe_candidates=())
    task = adapter.ensure_task(value)
    first = adapter.claim_next(owner="email")
    observe("classification-claimed")
    assert first.task_id == task.task_id and isinstance(task.task_id, str)
    adapter.fail(first, error="retry", retryable=True)
    observe("classification-retry")
    second = adapter.claim_next(owner="email-two")
    try:
        adapter.complete(first, {"classification": "stale"})
    except ValueError:
        pass
    else:
        raise AssertionError("stale classification owner completed")
    adapter.complete(second, {"classification": "work"})
    observe("classification-completed")

    store.upsert_feedback_event(key="feedback", feedback_token="token", comment="Feedback")
    resolve_feedback_fixture(store, "feedback")
    observe("feedback-resolved-with-evidence")
    job = store.begin_wechat_memory_import_job(import_run_id="import", account_id="wechat")
    observe("wechat-running")
    store.finish_wechat_memory_import_job(job, status="completed")
    observe("wechat-completed")
    return observations


def prune_cycle(path, *, claim_again=False):
    from app.store import AutoReplyStore
    from app.dispatcher.adapters import WorkSummaryQueueAdapter
    from app.state_storage import insert_state
    now = configure_clock()
    store = AutoReplyStore(Path(path))
    adapter = WorkSummaryQueueAdapter(store, owner_alive=lambda _: False)
    if claim_again:
        claim = adapter.claim(now, owner="again", owner_pid=17, lease=timedelta(minutes=5))
        return claim.generation
    item = store.enqueue_work_summary_input("local_file", "retention", "{}")
    envelope = adapter.claim(now, owner="first", owner_pid=16, lease=timedelta(minutes=5))
    with store._immediate_write_transaction() as db:
        insert_state(db, "dispatcher_claim_leases", {"adapter_name": "history", "source_id": "template",
            "terminal_at": "2099-01-01", "created_at": "clock", "updated_at": "clock"}, source_rowid=100000)
        db.execute("""
            with recursive numbers(n) as (values(1) union all select n+1 from numbers where n<9999)
            insert into state(record_type,legacy_key,payload_json)
            select 'dispatcher_claim_leases',cast(100000+n as text),
                   json_set(template.payload_json,'$.source_id','history-'||n)
            from state template,numbers
            where template.record_type='dispatcher_claim_leases' and template.legacy_key='100000'
        """)
    adapter.complete(envelope, owner="first", now=now)
    store.schedule_work_summary_input_retry(item, "retention-retry", available_at="")
    with store._connect() as db:
        assert db.execute("select count(*) from state where record_type='dispatcher_claim_leases'").fetchone()[0] == 10000
        assert db.execute("select 1 from state where record_type='dispatcher_claim_leases' "
                          "and adapter_name='work_summary' and source_id=?", (str(item),)).fetchone() is None
    return item


def write_cost(path):
    """Bounded real claims, with and without 3,000 unrelated journal Tasks."""
    import statistics
    import time
    from app.store import AutoReplyStore
    from app.email_store import EmailStore
    from app.dispatcher.adapters import ReplyQueueAdapter, WorkSummaryQueueAdapter
    from app.email_task_adapter import EmailClassificationTaskAdapter, EmailClassificationTaskInput
    from app.task12_storage import insert_task12
    from app.task20_definitions import TASK20_LAYOUTS

    now = configure_clock()
    store, email = AutoReplyStore(Path(path)), EmailStore(Path(path))
    store.enqueue_reply_task(conversation_id="cost", conversation_title="Cost", single_chat=True,
        trigger_message_id="cost", trigger_create_time="clock", trigger_sender="sender", trigger_text="x" * 256)
    store.enqueue_work_summary_input("local_file", "cost", '{"body":"' + "x" * 65536 + '"}')
    reply = ReplyQueueAdapter(store, owner_alive=lambda _: False)
    summary = WorkSummaryQueueAdapter(store, owner_alive=lambda _: False)
    classifier = EmailClassificationTaskAdapter(email, now=lambda: now, retry_base_seconds=0)
    classifier.ensure_task(EmailClassificationTaskInput.from_message(
        {"accountId": "cost", "folder": "INBOX", "uidValidity": 1, "uid": 1,
         "messageId": "<cost@example.test>", "providerUnread": True},
        allowed_category_keys=("work",), category_descriptions={"work": {"core": "Business"}},
        folder_targets={}, config_version="cost", unsubscribe_candidates=()))
    connect = sqlite3.connect
    active = {"vm": False, "steps": 0, "updates": 0, "statements": []}

    class MeasuredConnection(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            if active["vm"] and sql.lstrip().lower().startswith("update tasks"):
                active["updates"] += 1
                active["statements"].append((sql, parameters))
            return super().execute(sql, parameters)

    def progress():
        active["steps"] += 100

    def measured_connect(*args, **kwargs):
        db = connect(*args, **kwargs, factory=MeasuredConnection)
        if active["vm"]:
            db.set_progress_handler(progress, 100)
        return db

    sqlite3.connect = measured_connect

    def measure(adapter, *, email_task=False):
        elapsed = []
        for _ in range(3):
            start = time.perf_counter()
            claim = (adapter.claim_next(owner="cost") if email_task else
                     adapter.claim(now, owner="cost", owner_pid=7, lease=timedelta(minutes=5)))
            elapsed.append((time.perf_counter() - start) * 1000)
            assert claim is not None
            if email_task:
                adapter.fail(claim, error="retry", retryable=True)
            else:
                adapter.release(claim, owner="cost", now=now)
        active.update(vm=True, steps=0, updates=0, statements=[])
        claim = (adapter.claim_next(owner="cost") if email_task else
                 adapter.claim(now, owner="cost", owner_pid=7, lease=timedelta(minutes=5)))
        result = {"vm": active["steps"], "task_updates": active["updates"],
                  "median_ms": round(statistics.median(elapsed), 3)}
        active["vm"] = False
        with store._connect() as db:
            assert not db.in_transaction
            assert db.execute("pragma foreign_keys").fetchone()[0] == 1
            statement, parameters = active["statements"][0]
            result["update_plan"] = [row[3] for row in db.execute(
                "explain query plan " + statement, parameters)]
            # Compile only, in this owned test fixture: isolate the inherited
            # generated-parent FK validation from indexed row selection.
            db.execute("pragma foreign_keys=off")
            result["selection_plan_without_fk"] = [row[3] for row in db.execute(
                "explain query plan " + statement, parameters)]
            db.execute("pragma foreign_keys=on")
        if email_task:
            adapter.fail(claim, error="retry", retryable=True)
        else:
            adapter.release(claim, owner="cost", now=now)
        return result

    def group():
        return {"reply": measure(reply), "summary_64k": measure(summary),
                "email": measure(classifier, email_task=True)}

    before = group()
    with store._immediate_write_transaction() as db:
        prototype = insert_task12(db, "oa_notification_events", {
            "event_kind": "system_notification", "conversation_id": "cost-journal", "trigger_message_id": "template"})
        path = TASK20_LAYOUTS["oa_notification_events"].path("trigger_message_id")
        db.execute(
            "with recursive n(i) as(values(1) union all select i+1 from n where i<3000) "
            "insert into tasks(_kind,legacy_key,payload_json) select original._kind,cast(n.i+1 as text),"
            "jsonb_set(original.payload_json,?, 'cost-'||n.i) from tasks original,n where original.id=?",
            (path, prototype.physical_id))
    after = group()
    return {"sqlite_version": sqlite3.sqlite_version,
            "before": before, "with_3000_unrelated": after}
