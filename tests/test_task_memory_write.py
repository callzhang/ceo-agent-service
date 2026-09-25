from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import sqlite3

import pytest

from app.store import AgentRole
from app.memory_connector_client import MemoryConnectorError, MemoryWriteReceipt
from app import store as store_module
from app.store import AutoReplyStore
from app.task_memory_write import (
    TASK_MEMORY_WRITE_MAX_ATTEMPTS,
    memory_write_arguments,
    process_task_memory_writes,
)


NOW = datetime(2026, 9, 25, 2, 0, tzinfo=UTC)
MEMORY = {
    "title": "日报每晚 21:00 发",
    "content": "Derek 定下 CEO 每日总结每晚 21:00（北京时间）发。",
    "source_time": "2026-09-24T10:00:00+08:00",
    "source_refs": ["msg-42"],
    "subject": {"type": "Person", "name": "Derek"},
}
OTHER = {
    "title": "周报写入当周管理层周会",
    "content": "周报直接写入尚未召开的当周管理层周会文档。",
    "source_time": "2026-09-24T11:00:00+08:00",
    "source_refs": ["msg-43"],
    "subject": None,
}


def _finished_task(store: AutoReplyStore, durable_memories_json: str | None):
    store.enqueue_reply_task(
        conversation_id="cid-daily",
        conversation_title="CEO 日报",
        single_chat=True,
        trigger_message_id="msg-daily",
        trigger_create_time="2026-09-24 10:00:00",
        trigger_sender="Derek",
        trigger_text="text",
    )
    [task] = store.claim_reply_tasks(limit=1)
    consumer = store.claim_agent_run(
        task.id, task.execution_generation, role=AgentRole.CONSUMER,
        proposal_revision=0, turn_attempt=0, parent_agent_run_id=None,
        operation_id="", owner="consumer",
    ).run
    store.complete_agent_run(consumer.id, {"outcome": "no_action"}, owner="consumer")
    store.finalize_orchestrated_reply_task(
        task_id=task.id, expected_execution_generation=task.execution_generation,
        run_id=consumer.id, task_status="done", task_error="", available_at="",
        conversation_id=task.conversation_id, conversation_title=task.conversation_title,
        trigger_message_id=task.trigger_message_id, trigger_sender=task.trigger_sender,
        trigger_text=task.trigger_text, codex_reason="", codex_session_id="",
        codex_transcript_start_line=0, codex_transcript_end_line=0,
        audit_tool_events_json="[]", audit_summary="", send_status="skipped",
        send_error="", channel="dingtalk",
        durable_memories_json=durable_memories_json,
    )
    return task, consumer


def _rows(store: AutoReplyStore):
    with store._connect() as db:
        return [dict(row) for row in db.execute("select * from task_memory_write_events")]


class _Writer:
    def __init__(self, fail_after: int | None = None) -> None:
        self.calls: list[dict] = []
        self.fail_after = fail_after

    def __call__(self, **arguments):
        if self.fail_after is not None and len(self.calls) >= self.fail_after:
            raise MemoryConnectorError("connector unavailable")
        self.calls.append(arguments)
        return MemoryWriteReceipt(
            episode_uuid=f"episode-{len(self.calls)}", processing_status="queued"
        )


def test_a_finished_task_queues_what_its_consumer_asked_to_remember(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "memory.sqlite3")
    task, consumer = _finished_task(store, json.dumps([MEMORY, OTHER]))

    [row] = _rows(store)
    assert row["reply_task_id"] == task.id
    assert row["execution_generation"] == task.execution_generation
    assert row["consumer_run_id"] == consumer.id
    assert row["status"] == "pending"
    assert json.loads(row["memories_json"]) == [MEMORY, OTHER]


@pytest.mark.parametrize(
    ("durable_memories_json", "skip_reason"),
    [("[]", "no_durable_memories"), (None, "no_consumer_result")],
)
def test_a_finished_task_with_nothing_to_remember_still_gets_a_decision(
    tmp_path: Path, durable_memories_json, skip_reason
):
    store = AutoReplyStore(tmp_path / "memory.sqlite3")
    _finished_task(store, durable_memories_json)

    [row] = _rows(store)
    assert (row["status"], row["skip_reason"]) == ("skipped", skip_reason)


def test_writes_carry_the_agents_content_and_the_services_own_provenance(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "memory.sqlite3")
    task, consumer = _finished_task(store, json.dumps([MEMORY, OTHER]))
    writer = _Writer()

    summary = process_task_memory_writes(store, now=lambda: NOW, memory_writer=writer)

    assert summary == "claimed=1 written=1 retry=0 failed=0"
    first, second = writer.calls
    assert first["data"] == f"{MEMORY['title']}\n\n{MEMORY['content']}"
    assert first["created_at"] == MEMORY["source_time"]
    assert first["source_description"] == MEMORY["title"]
    assert first["thread_id"] == task.conversation_id
    assert first["entity_type"] == "Person"
    assert first["entity_attributes"] == {"name": "Derek"}
    assert first["source_metadata"]["payload"]["reply_task_id"] == task.id
    assert first["source_metadata"]["payload"]["source_refs"] == ["msg-42"]
    provenance = first["provenance_metadata"]["payload"]
    assert provenance["agent_run_id"] == consumer.id
    assert provenance["execution_generation"] == task.execution_generation
    assert provenance["intent"] == "task_durable_memory"
    assert "entity_type" not in second
    [row] = _rows(store)
    assert row["status"] == "written"
    assert json.loads(row["written_memory_ids_json"]) == ["episode-1", "episode-2"]


def test_a_retry_writes_only_the_memories_not_yet_written(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "memory.sqlite3")
    _finished_task(store, json.dumps([MEMORY, OTHER]))

    first_pass = process_task_memory_writes(
        store, now=lambda: NOW, memory_writer=_Writer(fail_after=1)
    )
    assert first_pass == "claimed=1 written=0 retry=1 failed=0"
    [row] = _rows(store)
    assert row["status"] == "pending"
    assert json.loads(row["written_memory_ids_json"]) == ["episode-1"]

    writer = _Writer()
    later = NOW + timedelta(hours=1)
    assert process_task_memory_writes(
        store, now=lambda: later, memory_writer=writer
    ) == "claimed=1 written=1 retry=0 failed=0"
    assert [call["source_description"] for call in writer.calls] == [OTHER["title"]]


def test_a_write_that_keeps_failing_becomes_visible(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "memory.sqlite3")
    _finished_task(store, json.dumps([MEMORY]))
    with store._connect() as db:
        db.execute(
            "update task_memory_write_events set attempts=?",
            (TASK_MEMORY_WRITE_MAX_ATTEMPTS - 1,),
        )

    summary = process_task_memory_writes(
        store, now=lambda: NOW, memory_writer=_Writer(fail_after=0)
    )

    assert summary == "claimed=1 written=0 retry=0 failed=1"
    [failed] = store.list_failed_task_memory_write_events()
    assert failed.error == "connector unavailable"


def test_the_retired_never_written_queue_shape_is_rebuilt(tmp_path: Path):
    path = tmp_path / "memory.sqlite3"
    AutoReplyStore(path)
    with sqlite3.connect(path) as db:
        db.execute("drop table task_memory_write_events")
        db.execute(
            "create table task_memory_write_events (id integer primary key, "
            "reply_task_id integer not null unique, execution_generation text not null, "
            "memory_id text not null default '')"
        )
    store_module._INITIALIZED_STORE_PATHS.clear()

    AutoReplyStore(path)

    with sqlite3.connect(path) as db:
        columns = {row[1] for row in db.execute("pragma table_info(task_memory_write_events)")}
    assert {"memories_json", "written_memory_ids_json"} <= columns
    assert "memory_id" not in columns


def test_memory_write_arguments_leave_out_an_absent_subject(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "memory.sqlite3")
    task, _consumer = _finished_task(store, json.dumps([OTHER]))
    [event] = store.claim_due_task_memory_write_events(
        now=NOW, limit=1, owner="test", lease_seconds=60
    )

    arguments = memory_write_arguments(
        OTHER, task=task, event=event, route="codex_oauth", model="gpt-5.6-sol"
    )

    assert "entity_type" not in arguments and "entity_attributes" not in arguments
    assert arguments["provenance_metadata"]["payload"]["route"] == "codex_oauth"
