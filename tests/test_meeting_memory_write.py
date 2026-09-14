from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from app.meeting_memory_write import (
    enqueue_sent_meeting_memory_writes,
    meeting_memory_payload,
    process_meeting_memory_writes,
)
from app.store import AutoReplyStore


class _FakeRoutedExecution:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def execute(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            value=json.dumps(
                {
                    "status": "success",
                    "memory_id": "meeting-memory-7",
                    "retryable": False,
                    "source_code": "",
                    "detail": "",
                }
            )
        )


def _sent_job() -> SimpleNamespace:
    return SimpleNamespace(
        id=7,
        status="sent",
        meeting_id="minutes-7",
        title="经营复盘",
        ended_at="2026-09-14T09:30:00+08:00",
        updated_at="2026-09-14T10:00:00+08:00",
        final_message="【经营复盘结论】\n本周先完成客户验证。",
        source_json=json.dumps({"summary": "这是听记摘要"}, ensure_ascii=False),
        decision_json=json.dumps(
            {"derek_viewpoint": {"expressed_view": "这不是已发送文本"}},
            ensure_ascii=False,
        ),
    )


def _store_sent_job(store: AutoReplyStore) -> int:
    job_id = store.upsert_meeting_alignment_job(
        meeting_id="minutes-7",
        title="经营复盘",
        source_json=json.dumps({"summary": "这是听记摘要"}, ensure_ascii=False),
        participants_json="[]",
        ended_at="2026-09-14T09:30:00+08:00",
        eligible_at="2026-09-14T09:40:00+08:00",
        status="pending",
    )
    store.update_meeting_alignment_job(
        job_id,
        status="sent",
        final_message="【经营复盘结论】\n本周先完成客户验证。",
    )
    return job_id


def test_meeting_memory_payload_contains_only_delivered_conclusion() -> None:
    payload = meeting_memory_payload(_sent_job())

    assert payload["created_at"] == "2026-09-14T09:30:00+08:00"
    assert payload["source_description"] == "本周先完成客户验证"
    assert payload["data"].splitlines()[0] == "本周先完成客户验证"
    assert "本周先完成客户验证" in payload["data"]
    assert "听记摘要" not in payload["data"]
    assert "这不是已发送文本" not in payload["data"]
    assert payload["data"].endswith("[meeting-alignment:minutes-7]")


def test_meeting_memory_title_uses_first_substantive_conclusion() -> None:
    job = _sent_job()
    job.final_message = (
        "【会议跟进】投资人跟进话术（2026-07-17 05:30-05:50）\n\n"
        "我收一下刚才几个口径，后面按这个执行：\n\n"
        "1. 阳光保险今天这轮先按财务和公司价值沟通处理。\n\n"
        "2. 后续需要稳定运行本地服务。"
    )

    payload = meeting_memory_payload(job)

    assert payload["source_description"] == "阳光保险今天这轮先按财务和公司价值沟通处理"
    assert payload["data"].splitlines()[0] == "阳光保险今天这轮先按财务和公司价值沟通处理"


def test_meeting_memory_title_stops_after_the_first_conclusion_sentence() -> None:
    job = _sent_job()
    job.final_message = "【会议结论】\n\n1. 先完成客户验证。下周再扩大投入。"

    payload = meeting_memory_payload(job)

    assert payload["source_description"] == "先完成客户验证"


def test_sent_meetings_are_queued_once_and_written_to_memory(tmp_path: Path) -> None:
    store = AutoReplyStore(tmp_path / "store.sqlite3")
    job_id = _store_sent_job(store)

    assert enqueue_sent_meeting_memory_writes(store) == 1
    assert enqueue_sent_meeting_memory_writes(store) == 0
    with store._connect() as db:
        event = db.execute(
            "select * from meeting_memory_write_events where meeting_job_id=?", (job_id,)
        ).fetchone()
    assert event is not None
    assert event["status"] == "pending"
    assert event["execution_generation"]
    assert event["payload_json"] == "{}"

    routed = _FakeRoutedExecution()
    processed = process_meeting_memory_writes(
        store,
        workspace=tmp_path,
        routed_execution=routed,
        now=datetime.fromisoformat("2026-09-14T10:00:00+08:00"),
    )

    assert processed == 1
    assert routed.calls[0]["workload_key"] == (
        f"meeting_memory_write_event:{event['id']}:"
        f"{event['execution_generation']}"
    )
    assert "The source is 本周先完成客户验证." in routed.calls[0]["prompt"]
    with store._connect() as db:
        written = db.execute(
            "select status, memory_id from meeting_memory_write_events where id=?",
            (event["id"],),
        ).fetchone()
        meeting = db.execute(
            "select status from meeting_alignment_jobs where id=?", (job_id,)
        ).fetchone()
    assert dict(written) == {"status": "done", "memory_id": "meeting-memory-7"}
    assert meeting["status"] == "sent"


def test_failed_meeting_memory_write_can_be_requeued_only_for_sent_conclusion(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "store.sqlite3")
    job_id = _store_sent_job(store)
    assert enqueue_sent_meeting_memory_writes(store) == 1
    with store._connect() as db:
        created_event = db.execute(
            "select id, execution_generation from meeting_memory_write_events "
            "where meeting_job_id=?",
            (job_id,),
        ).fetchone()
    assert created_event is not None
    event_id = int(created_event["id"])
    original_generation = str(created_event["execution_generation"])

    store.fail_meeting_memory_write_event(event_id, error="provider configuration failed")

    assert store.requeue_failed_meeting_memory_write_event(
        event_id,
        reason="provider configuration repaired",
    )
    assert not store.requeue_failed_meeting_memory_write_event(
        event_id,
        reason="duplicate recovery",
    )
    with store._connect() as db:
        event = db.execute(
            "select status, attempts, error, available_at, execution_generation "
            "from meeting_memory_write_events where id=?",
            (event_id,),
        ).fetchone()
    assert event["status"] == "pending"
    assert event["attempts"] == 1
    assert event["error"] == "provider configuration repaired"
    assert event["available_at"] == ""
    assert event["execution_generation"]
    assert event["execution_generation"] != original_generation


def test_failed_meeting_memory_write_can_be_closed_from_verified_memory_readback(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "store.sqlite3")
    job_id = _store_sent_job(store)
    assert enqueue_sent_meeting_memory_writes(store) == 1
    with store._connect() as db:
        event_id = int(
            db.execute(
                "select id from meeting_memory_write_events where meeting_job_id=?",
                (job_id,),
            ).fetchone()["id"]
        )
    store.fail_meeting_memory_write_event(event_id, error="result parser failed")

    assert store.reconcile_failed_meeting_memory_write_event(
        event_id,
        memory_id="verified-memory-7",
    )
    assert not store.reconcile_failed_meeting_memory_write_event(
        event_id,
        memory_id="verified-memory-7",
    )
    with store._connect() as db:
        event = db.execute(
            "select status, attempts, error, memory_id from meeting_memory_write_events "
            "where id=?",
            (event_id,),
        ).fetchone()
    assert dict(event) == {
        "status": "done",
        "attempts": 1,
        "error": "",
        "memory_id": "verified-memory-7",
    }
