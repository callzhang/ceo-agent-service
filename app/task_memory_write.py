"""Write the durable memories finished tasks named to Memory.

The Consumer names what should outlive the turn (``durable_memories`` in its
result); finishing the task queues it (``task_memory_write_events``); the
dispatcher claims the row straight away (``TaskMemoryWriteQueueAdapter``) and
this module writes each item through the service's own connector client, the
same way delivered meeting conclusions are written. Nothing reviews them
first, and it is automatic system behaviour rather than a scheduled task
(Derek 2026-09-24).
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
import json
from typing import Any

from app.external_retry import retry_delay_seconds
from app.memory_connector_client import MemoryConnectorError, write_memory
from app.store import AutoReplyStore, ReplyTask, TaskMemoryWriteEvent


TASK_MEMORY_WRITE_RETRY_BASE_SECONDS = 60.0
TASK_MEMORY_WRITE_MAX_DELAY_SECONDS = 15 * 60
# About five hours of retrying at the delay ceiling before the row becomes a
# visible `failed` entry, the same bound the meeting writes use.
TASK_MEMORY_WRITE_MAX_ATTEMPTS = 20


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


def write_claimed_task_memories(
    store: AutoReplyStore,
    event_id: int,
    *,
    owner: str,
    now: Callable[[], datetime] | None = None,
    memory_writer: Callable[..., Any] | None = None,
) -> str:
    """Write one claimed row's remaining memories; return the status it now has."""
    clock = now or (lambda: datetime.now(timezone.utc))
    event = store.get_task_memory_write_event(event_id)
    if event is None:
        raise ValueError(f"task Memory write event {event_id} does not exist")
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
        return "pending"
    store.complete_task_memory_write_event(event.id, owner=owner)
    return "written"
