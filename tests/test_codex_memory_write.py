from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.codex_memory_write import run_codex_memory_write
from app.store import AutoReplyStore


class _FakeRoutedExecution:
    def __init__(self, episode_uuid: str = "episode-1") -> None:
        self.episode_uuid = episode_uuid
        self.calls: list[dict[str, object]] = []

    def execute(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(value=self.episode_uuid)


def _seed_memory_write_event(store: AutoReplyStore) -> int:
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="Friday",
        trigger_message_id="msg-1",
        trigger_sender="sender",
        trigger_text="remember",
        action="no_reply",
        sensitivity_kind="general",
    )
    with store._connect() as db:
        cursor = db.execute(
            """
            insert into memory_write_events (attempt_id, event_type, payload_json)
            values (?, 'memory_write', '{}')
            """,
            (attempt_id,),
        )
        return int(cursor.lastrowid)


def test_memory_write_uses_source_parent_and_persists_only_routed_result(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "store.sqlite3")
    event_id = _seed_memory_write_event(store)
    routed = _FakeRoutedExecution()

    result = run_codex_memory_write(
        workspace=tmp_path,
        store=store,
        event_id=event_id,
        data="durable statement",
        type="text",
        created_at="2026-08-21",
        source_description="reply audit",
        routed_execution=routed,
    )

    assert result.episode_uuid == "episode-1"
    call = routed.calls[0]
    assert call["workload_kind"] == "memory"
    assert call["workload_key"] == f"memory_write_event:{event_id}"
    assert call["command_factory"].required_reviewed_mcp_servers == frozenset(
        {"memory_connector"}
    )
    with store._connect() as db:
        row = db.execute(
            "select status, memory_episode_id from memory_write_events where id=?",
            (event_id,),
        ).fetchone()
    assert dict(row) == {"status": "written", "memory_episode_id": "episode-1"}
