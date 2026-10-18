from contextlib import contextmanager
import sqlite3

from app.action_storage import create_action_schema
from app.store import AutoReplyStore
from app.task_semantic_service import (
    SourceSignal,
    TaskMutationResult,
    TaskSemanticService,
)


def test_business_task_event_store_reads_and_writes_typed_actions(tmp_path):
    path = tmp_path / "task-events.sqlite3"
    db = sqlite3.connect(path, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("pragma foreign_keys=on")
    for name in ("tasks", "runs", "email_classifications"):
        db.execute(
            f"create table {name}(id integer primary key"
            + (",record_type text)" if name in ("tasks", "runs") else ")")
        )
    db.execute(
        "create table business_task_signals(id integer primary key, dedupe_key text unique)"
    )
    db.execute("create table business(task_public_id integer primary key)")
    db.execute("insert into business(task_public_id) values (42)")
    db.execute("insert into business_task_signals(id,dedupe_key) values (7,'event-7')")
    create_action_schema(
        db,
        include_reply=True,
        reply_variant="fresh_memory_fk",
        include_email=True,
        include_followup_sync=True,
        include_business_sync=True,
    )

    store = object.__new__(AutoReplyStore)

    @contextmanager
    def connect():
        yield db

    store._connect = connect
    event_id = store.append_business_task_event(
        task_id=42,
        event_type="created",
        signal_id=7,
        before_json="{}",
        after_json='{"status":"open"}',
        reason="source evidence confirmed",
        _db=db,
    )

    events = store.list_business_task_events(42)
    assert [event.id for event in events] == [event_id]
    assert events[0].event_type.value == "created"
    assert events[0].signal_id == 7
    assert events[0].after_json == '{"status":"open"}'
    assert db.execute(
        "select count(*) from actions where record_type='business_task_events'"
    ).fetchone()[0] == 1
    assert db.execute(
        "select 1 from sqlite_schema where type='table' and name='business_task_events'"
    ).fetchone() is None

    service = object.__new__(TaskSemanticService)
    service.store = type(
        "StoreStub",
        (),
        {"get_business_task_signal_by_dedupe_key": lambda *_args, **_kwargs: None},
    )()
    replay = service._replay_result(
        signal=SourceSignal(
            source_type="test",
            source_ref="test:7",
            evidence_text="evidence",
            dedupe_key="event-7",
        ),
        db=db,
    )
    assert replay == TaskMutationResult(task_id=42, signal_id=7, created=False)
    db.close()
