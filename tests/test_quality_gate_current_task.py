import pytest

from app.quality_gate import scan_hourly_quality
from app.store import AutoReplyStore
from app.task_entity_storage import update_task_cas
from tests.test_quality_gate import (
    NOW,
    _insert_needs_human_projection,
    _structured_needs_human_result,
)


@pytest.mark.parametrize("task_status", ["done", "skipped", "failed"])
def test_terminal_task_does_not_keep_needs_human_quality_item(
    tmp_path, monkeypatch, task_status,
):
    store = AutoReplyStore(tmp_path / f"{task_status}.sqlite3")
    _insert_needs_human_projection(
        store,
        monkeypatch=monkeypatch,
        result=_structured_needs_human_result(),
        task_status=task_status,
    )

    report = scan_hourly_quality(store.path, now=NOW)

    assert not any(
        item.source == "reply_attempts"
        and item.code in {"needs_human", "invalid_needs_human_result"}
        for item in [*report.attention, *report.violations]
    )


def test_old_generation_attempt_does_not_attach_to_current_needs_human_task(
    tmp_path, monkeypatch,
):
    store = AutoReplyStore(tmp_path / "older-generation.sqlite3")
    _insert_needs_human_projection(
        store,
        monkeypatch=monkeypatch,
        result=_structured_needs_human_result(),
    )
    with store._connect() as db:
        task = db.execute(
            "select tasks.id, tasks.execution_generation from tasks "
            "join task_inputs inputs on inputs.id=tasks.current_input_id "
            "where tasks.kind='reply' and inputs.conversation_id='conversation' "
            "and inputs.trigger_message_id='message'"
        ).fetchone()
        assert update_task_cas(
            db,
            task_id=task["id"],
            expected={
                "status": "needs_human",
                "execution_generation": task["execution_generation"],
            },
            changes={"execution_generation": "new-generation"},
        ) is not None

    report = scan_hourly_quality(store.path, now=NOW)

    assert not any(
        item.source == "reply_attempts"
        and item.code in {"needs_human", "invalid_needs_human_result"}
        for item in [*report.attention, *report.violations]
    )


def test_current_needs_human_task_keeps_structured_result_quality_check(
    tmp_path, monkeypatch,
):
    store = AutoReplyStore(tmp_path / "current-generation.sqlite3")
    _insert_needs_human_projection(
        store,
        monkeypatch=monkeypatch,
        result=_structured_needs_human_result(),
    )

    report = scan_hourly_quality(store.path, now=NOW)

    assert ("reply_attempts", "invalid_needs_human_result", 1) in {
        (item.source, item.code, item.count) for item in report.violations
    }
