from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import json

import pytest

from app.agent_runtime_router import RoutedCodexExecutionError
from app.codex_memory_write import (
    CodexMemoryWriteFailed,
    memory_result_from_typed_output,
    run_codex_memory_write,
)
from app.store import AutoReplyStore


class _FakeRoutedExecution:
    def __init__(self, value: str | None = None) -> None:
        self.value = value or json.dumps(
            {
                "status": "success",
                "memory_id": "episode-1",
                "retryable": False,
                "source_code": "",
                "detail": "",
            }
        )
        self.calls: list[dict[str, object]] = []

    def execute(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(value=self.value)


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
    assert call["required_capabilities"] == frozenset(
        {"structured_output", "mcp:memory_connector:memory_write"}
    )
    with store._connect() as db:
        row = db.execute(
            "select status, memory_episode_id from memory_write_events where id=?",
            (event_id,),
        ).fetchone()
    assert dict(row) == {"status": "written", "memory_episode_id": "episode-1"}


def test_memory_write_typed_result_ignores_unrelated_tool_events() -> None:
    raw = "\n".join(
        [
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "type": "mcp_tool_call",
                        "tool": "some_future_tool",
                        "result": {"ok": True},
                    },
                }
            ),
            json.dumps(
                {
                    "status": "success",
                    "memory_id": "episode-2",
                    "retryable": False,
                    "source_code": "",
                    "detail": "",
                }
            ),
        ]
    )

    result = memory_result_from_typed_output(raw)

    assert result.episode_uuid == "episode-2"


def test_memory_write_typed_failure_preserves_provider_error() -> None:
    raw = json.dumps(
        {
            "status": "failed",
            "memory_id": "",
            "retryable": True,
            "source_code": "memory_backend_unavailable",
            "detail": "provider connection refused",
        }
    )

    with pytest.raises(CodexMemoryWriteFailed) as caught:
        memory_result_from_typed_output(raw)

    assert caught.value.retryable is True
    assert caught.value.source_code == "memory_backend_unavailable"
    assert str(caught.value) == "provider connection refused"


def test_runtime_failure_is_explicit_and_preserves_runtime_code(tmp_path: Path) -> None:
    class FailedRoutedExecution:
        def execute(self, **_kwargs):
            raise RoutedCodexExecutionError(
                "runtime_execution_failed",
                "provider timeout",
                failure_code="codex_process_timeout",
                retryable_external_dependency=True,
            )

    store = AutoReplyStore(tmp_path / "store.sqlite3")
    event_id = _seed_memory_write_event(store)

    with pytest.raises(CodexMemoryWriteFailed) as caught:
        run_codex_memory_write(
            workspace=tmp_path,
            store=store,
            event_id=event_id,
            data="durable statement",
            type="text",
            created_at="2026-08-21",
            source_description="reply audit",
            routed_execution=FailedRoutedExecution(),
        )

    assert caught.value.retryable is True
    assert caught.value.source_code == "codex_process_timeout"
    assert str(caught.value) == "provider timeout"
