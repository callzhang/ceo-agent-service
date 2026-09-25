"""Write the durable memories finished tasks named to Memory.

The Consumer names what should outlive the turn (``durable_memories`` in its
result); finishing the task queues it (``task_memory_write_events``); this
pass writes each one through the service's own connector client, the same way
delivered meeting conclusions are written. Nothing reviews them first
(Derek 2026-09-24). Every step is a fixed rule, so this is a service command,
not an Agent turn.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
import json
from typing import Any
from uuid import uuid4

from app.external_retry import retry_delay_seconds
from app.memory_connector_client import MemoryConnectorError, write_memory
from app.store import AutoReplyStore, ReplyTask, TaskMemoryWriteEvent


TASK_MEMORY_WRITE_RETRY_BASE_SECONDS = 60.0
TASK_MEMORY_WRITE_MAX_DELAY_SECONDS = 15 * 60
# About five hours of retrying at the delay ceiling before the row becomes a
# visible `failed` entry, the same bound the meeting writes use.
TASK_MEMORY_WRITE_MAX_ATTEMPTS = 20
# One connector write measured 74-95 seconds for meetings; a task names a
# handful at most.
TASK_MEMORY_WRITE_LEASE_SECONDS = 2700
TASK_MEMORY_WRITE_PASS_LIMIT = 50


def memory_write_arguments(
    memory: dict[str, Any],
    *,
    task: ReplyTask,
    event: TaskMemoryWriteEvent,
    route: str,
    model: str,
) -> dict[str, Any]:
    """The memory_write call for one item: its content from the Agent, the rest from records."""
    arguments: dict[str, Any] = {
        "data": f"{memory['title']}\n\n{memory['content']}",
        "type": "text",
        "created_at": memory["source_time"],
        "source_description": memory["title"],
        "thread_id": task.conversation_id,
        "source_metadata": {
            "kind": "manual_source",
            "schema_version": 1,
            "payload": {
                "channel": task.channel,
                "conversation_id": task.conversation_id,
                "conversation_title": task.conversation_title,
                "trigger_message_id": task.trigger_message_id,
                "reply_task_id": task.id,
                "source_refs": list(memory["source_refs"]),
            },
        },
        "provenance_metadata": {
            "kind": "manual_provenance",
            "schema_version": 1,
            "payload": {
                "actor": "ceo-agent-service consumer",
                "agent_run_id": event.consumer_run_id,
                "execution_generation": event.execution_generation,
                "route": route,
                "model": model,
                "intent": "task_durable_memory",
            },
        },
    }
    subject = memory.get("subject")
    if subject:
        arguments["entity_type"] = subject["type"]
        arguments["entity_attributes"] = {"name": subject["name"]}
    return arguments


def process_task_memory_writes(
    store: AutoReplyStore,
    *,
    now: Callable[[], datetime] | None = None,
    memory_writer: Callable[..., Any] | None = None,
    limit: int = TASK_MEMORY_WRITE_PASS_LIMIT,
) -> str:
    """Write every due queued memory once; return a one-line summary."""
    clock = now or (lambda: datetime.now(timezone.utc))
    owner = f"task-memory-write:{uuid4().hex}"
    events = store.claim_due_task_memory_write_events(
        now=clock(), limit=limit, owner=owner,
        lease_seconds=TASK_MEMORY_WRITE_LEASE_SECONDS,
    )
    counts = {"written": 0, "retry": 0, "failed": 0}
    for event in events:
        counts[_write_event(store, event, owner=owner, clock=clock,
                            memory_writer=memory_writer)] += 1
    return (
        f"claimed={len(events)} written={counts['written']} "
        f"retry={counts['retry']} failed={counts['failed']}"
    )


def _write_event(
    store: AutoReplyStore,
    event: TaskMemoryWriteEvent,
    *,
    owner: str,
    clock: Callable[[], datetime],
    memory_writer: Callable[..., Any] | None,
) -> str:
    # Resolved per call, not bound as a default, so a test stubbing this
    # module's ``write_memory`` is honoured.
    writer = memory_writer or write_memory
    task = store.get_reply_task(event.reply_task_id)
    if task is None:
        store.fail_task_memory_write_event(
            event.id, owner=owner, error="reply task no longer exists"
        )
        return "failed"
    route, model = (
        store.latest_runtime_route_for_agent_run(event.consumer_run_id)
        if event.consumer_run_id is not None
        else ("", "")
    )
    memories = json.loads(event.memories_json)
    written = list(json.loads(event.written_memory_ids_json))
    try:
        for memory in memories[len(written):]:
            receipt = writer(**memory_write_arguments(
                memory, task=task, event=event, route=route, model=model,
            ))
            written.append(receipt.episode_uuid)
            store.record_task_memory_written_ids(
                event.id, owner=owner, written_memory_ids=written
            )
    except MemoryConnectorError as exc:
        if event.attempts + 1 >= TASK_MEMORY_WRITE_MAX_ATTEMPTS:
            store.fail_task_memory_write_event(event.id, owner=owner, error=str(exc))
            return "failed"
        delay = retry_delay_seconds(
            TASK_MEMORY_WRITE_RETRY_BASE_SECONDS,
            event.attempts,
            max_delay_seconds=TASK_MEMORY_WRITE_MAX_DELAY_SECONDS,
        )
        store.retry_task_memory_write_event(
            event.id, owner=owner, error=str(exc),
            available_at=(clock() + timedelta(seconds=delay)).isoformat(),
        )
        return "retry"
    store.complete_task_memory_write_event(event.id, owner=owner)
    return "written"
