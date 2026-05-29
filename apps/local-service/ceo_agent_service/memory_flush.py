from ceo_agent_service.memory_connector import (
    MemoryConnectorClient,
    extract_memory_episode_id,
    memory_connector_user_id,
)
from ceo_agent_service.store import AutoReplyStore


def flush_memory_events(
    store: AutoReplyStore, client: MemoryConnectorClient, limit: int
) -> int:
    sent_count = 0
    for event in store.claim_memory_write_events(limit=limit):
        try:
            payload = client.memory_write(
                data=event.payload_json,
                type="json",
                created_at=event.created_at,
                user_id=memory_connector_user_id(),
                source_description=(
                    f"ceo-agent-service:{event.event_type}:{event.attempt_id}"
                ),
                source_metadata={
                    "service": "ceo-agent-service",
                    "event_type": event.event_type,
                    "attempt_id": event.attempt_id,
                    "outbox_event_id": event.id,
                },
                provenance_metadata={
                    "source": "ceo-agent-service",
                    "outbox_event_id": event.id,
                },
            )
        except Exception as exc:
            store.mark_memory_write_event_failed(event.id, str(exc))
            continue
        store.mark_memory_write_event_sent(event.id, extract_memory_episode_id(payload))
        sent_count += 1
    return sent_count
