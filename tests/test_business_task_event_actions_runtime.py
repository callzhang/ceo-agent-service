from app.store import AutoReplyStore
from app.task_semantic_service import (
    SourceSignal,
    TaskMutationResult,
    TaskSemanticService,
)
from tests.support.business_task_seed import seed_task, seed_task_signal


def test_business_task_event_store_reads_and_writes_typed_actions(tmp_path):
    store = AutoReplyStore(tmp_path / "task-events.sqlite3")
    task_id = seed_task(store, title="Task", stage="candidate")
    signal_id = seed_task_signal(store,
        source_type="test",
        source_ref="test:7",
        evidence_text="evidence",
        dedupe_key="event-7",
    )
    with store.business_task_transaction() as db:
        event_id = store.append_business_task_event(
            task_id=task_id,
            event_type="created",
            signal_id=signal_id,
            before_json="{}",
            after_json='{"status":"open"}',
            reason="source evidence confirmed",
            _db=db,
        )

    events = store.list_business_task_events(task_id)
    assert [event.id for event in events] == [event_id]
    assert events[0].event_type.value == "created"
    assert events[0].signal_id == signal_id
    assert events[0].after_json == '{"status":"open"}'
    with store._connect() as db:
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
    with store._connect() as db:
        replay = service._replay_result(
            signal=SourceSignal(
                source_type="test",
                source_ref="test:7",
                evidence_text="evidence",
                dedupe_key="event-7",
            ),
            db=db,
        )
    assert replay == TaskMutationResult(task_id=task_id, signal_id=signal_id, created=False)
