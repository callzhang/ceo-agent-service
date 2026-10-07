"""Read contracts for complete Project evidence and original Task signals."""

import json
from datetime import datetime, timezone

from app.store import AutoReplyStore
from tests.test_console_web_api import _client


def _project(store: AutoReplyStore, *, title: str = "Evidence project") -> int:
    with store.business_task_transaction() as db:
        anchor_id = store.create_business_anchor_in_transaction(
            anchor_type="project",
            anchor_ref=f"project:{title}",
            title=title,
            _db=db,
        )
        return store.create_business_project_in_transaction(
            canonical_anchor_id=anchor_id,
            title=title,
            registry_source="registry",
            _db=db,
        )


def _signal(
    store: AutoReplyStore,
    *,
    key: str,
    evidence_text: str | None = None,
    source_ref: str | None = None,
    source_time: str = "2026-10-07T12:00:00+00:00",
) -> int:
    return store.create_business_task_signal(
        source_type="message",
        source_ref=source_ref or f"message:{key}",
        source_time=source_time,
        conversation_id="conversation-1",
        conversation_title="Project room",
        author_user_id="author-1",
        author_name="Source author",
        author_kind="human",
        evidence_text=evidence_text or f"Evidence {key}",
        context_json=json.dumps({"version": key}),
        dedupe_key=f"evidence-read:{key}",
        now=datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc),
    )


def _business_snapshot(store: AutoReplyStore) -> dict[str, tuple[tuple[object, ...], ...]]:
    with store._connect() as db:
        tables = [
            str(row[0])
            for row in db.execute(
                "select name from sqlite_master where type='table' and name like 'business_%' "
                "order by name"
            )
        ]
        return {
            table: tuple(tuple(row) for row in db.execute(f'select * from "{table}" order by rowid'))
            for table in tables
        }


def test_project_evidence_pages_cover_newest_window_without_overlap(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    project_id = _project(store)
    signal_ids = [_signal(store, key=str(index)) for index in range(25)]
    with store.business_task_transaction() as db:
        for index, signal_id in enumerate(signal_ids):
            db.execute(
                "insert into business_project_evidence(project_id, signal_id, created_at) "
                "values (?, ?, ?)",
                (project_id, signal_id, f"2026-10-07T12:{index:02d}:00+00:00"),
            )

    with _client(tmp_path) as client:
        first = client.get(
            f"/api/console/tasks/projects/{project_id}/evidence",
            params={"page": 1, "page_size": 20},
        )
        second = client.get(
            f"/api/console/tasks/projects/{project_id}/evidence",
            params={"page": 2, "page_size": 20},
        )

    assert first.status_code == second.status_code == 200
    first_payload = first.json()
    second_payload = second.json()
    first_ids = [item["signal_id"] for item in first_payload["items"]]
    second_ids = [item["signal_id"] for item in second_payload["items"]]
    assert first_ids == signal_ids[5:]
    assert second_ids == signal_ids[:5]
    assert set(first_ids).isdisjoint(second_ids)
    assert set(first_ids + second_ids) == set(signal_ids)
    assert all(set(item) == {"project_id", "signal_id", "created_at"} for item in first_payload["items"])
    assert first_payload["meta"] | {"snapshot_at": "ignored"} == {
        "snapshot_at": "ignored",
        "page": 1,
        "page_size": 20,
        "total": 25,
        "next_cursor": "2",
        "has_more": True,
    }
    assert second_payload["meta"] | {"snapshot_at": "ignored"} == {
        "snapshot_at": "ignored",
        "page": 2,
        "page_size": 20,
        "total": 25,
        "next_cursor": "",
        "has_more": False,
    }


def test_project_evidence_missing_empty_and_invalid_requests(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    project_id = _project(store, title="Empty project")

    with _client(tmp_path) as client:
        missing = client.get("/api/console/tasks/projects/999999/evidence")
        empty = client.get(f"/api/console/tasks/projects/{project_id}/evidence")
        invalid_page = client.get(
            f"/api/console/tasks/projects/{project_id}/evidence", params={"page": 0}
        )
        invalid_size = client.get(
            f"/api/console/tasks/projects/{project_id}/evidence", params={"page_size": 101}
        )

    assert missing.status_code == 404
    assert empty.status_code == 200
    assert empty.json()["items"] == []
    assert empty.json()["meta"]["total"] == 0
    assert empty.json()["meta"]["has_more"] is False
    assert invalid_page.status_code == invalid_size.status_code == 422


def test_signal_read_returns_complete_original_source_and_distinct_versions(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    decisive_middle = "DECISIVE-MIDDLE-" + ("原始证据" * 80)
    raw_body = json.dumps(
        {"prefix": "x" * 2300, "decision": decisive_middle, "suffix": "y" * 2300},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    first_id = _signal(
        store,
        key="version-1",
        evidence_text=raw_body,
        source_ref="message:shared",
        source_time="2026-10-07T12:00:00+00:00",
    )
    second_id = _signal(
        store,
        key="version-2",
        evidence_text=raw_body + "\nsecond version",
        source_ref="message:shared",
        source_time="2026-10-07T12:01:00+00:00",
    )
    first_saved = store.get_business_task_signal(first_id)

    with _client(tmp_path) as client:
        first = client.get(f"/api/console/tasks/signals/{first_id}")
        second = client.get(f"/api/console/tasks/signals/{second_id}")
        missing = client.get("/api/console/tasks/signals/999999")

    assert first.status_code == second.status_code == 200
    assert missing.status_code == 404
    first_item = first.json()["item"]
    second_item = second.json()["item"]
    assert first_item == first_saved.model_dump(mode="json")
    assert first_item["evidence_text"].encode() == raw_body.encode()
    assert decisive_middle in first_item["evidence_text"]
    assert first_item["source_document_id"] != second_item["source_document_id"]
    assert first_item["source_ref"] == second_item["source_ref"] == "message:shared"
    assert first_item["source_time"] != second_item["source_time"]
    assert first_item["evidence_text"] != second_item["evidence_text"]


def test_evidence_and_signal_reads_do_not_mutate_business_domain_tables(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    project_id = _project(store, title="Read only")
    signal_id = _signal(store, key="read-only")
    with store.business_task_transaction() as db:
        db.execute(
            "insert into business_project_evidence(project_id, signal_id) values (?, ?)",
            (project_id, signal_id),
        )

    with _client(tmp_path) as client:
        before = _business_snapshot(store)
        assert client.get(f"/api/console/tasks/projects/{project_id}/evidence").status_code == 200
        assert client.get(f"/api/console/tasks/signals/{signal_id}").status_code == 200
        after = _business_snapshot(store)

    assert after == before
