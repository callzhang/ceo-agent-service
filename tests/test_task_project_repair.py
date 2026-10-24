from tests.support.legacy_todo_facts import seed_legacy_work_project, seed_legacy_work_todo, seed_legacy_work_update
import json
import sqlite3
from datetime import datetime, timedelta

from app.run_entity_storage import create_runtime_attempt
from app import native_trajectory
from app.store import AutoReplyStore
from app.task_project_repair import build_repair_manifest


def _set_project_times(db_path, project_id, *, created_at):
    with sqlite3.connect(db_path) as db:
        from app.business_storage import update_business
        db.execute("begin immediate")
        update_business(db, "work_project", project_id, {
            "created_at": created_at,
            "updated_at": created_at,
            "last_activity_at": created_at,
        })


def _record_agent_value(db_path, project_id, *, title, created_at):
    store = AutoReplyStore(db_path)
    parent = store.enqueue_work_summary_input("local_file", f"repair:{project_id}:{created_at}", "{}")
    run_id = store.create_professional_run(parent)
    with sqlite3.connect(db_path) as db:
        db.row_factory = sqlite3.Row
        db.execute("update runs set created_at=? where id=?", (created_at, run_id))
        create_runtime_attempt(db, values={
            "parent_id": run_id, "workload_kind": "agent_run", "workload_key": str(run_id),
            "attempt_number": 1, "route_name": "test", "runtime_kind": "codex_cli",
            "credential_mode": "local_oauth", "model": "test", "session_mode": "fresh",
            "source_session_id": "", "attempt_purpose": "normal",
            "validation_retry_policy_id": "", "validation_result_schema_id": "",
            "session_id": "native-task", "transcript_reference": "",
            "transcript_start": 1, "transcript_end": 2, "status": "completed",
            "failure_class": "", "failure_code": "", "failover_permitted": 0,
            "lease_owner": "", "lease_expires_at": "", "first_effect_started_at": "",
            "result_schema_id": "", "started_at": created_at, "finished_at": created_at,
            "created_at": created_at, "updated_at": created_at,
        })


def test_manifest_plans_latest_traceable_value(tmp_path, monkeypatch):
    native = tmp_path / "native-task.jsonl"
    native.write_text("\n".join(json.dumps({"type": "response_item", "payload": {
        "type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]
    }}, ensure_ascii=False) for text in (
        '{"project":{"id":999,"title":"wrong turn"}}',
        '{"project":{"id":1,"title":"最近 Agent 标题"}}',
    )) + "\n")
    monkeypatch.setattr(native_trajectory, "find_codex_session_path", lambda *args, **kwargs: native)
    current_path = tmp_path / "current.sqlite3"
    historical_path = tmp_path / "historical.sqlite3"
    current = AutoReplyStore(current_path)
    historical = AutoReplyStore(historical_path)
    project_id = seed_legacy_work_project(current, title="", goal="")
    historical_id = seed_legacy_work_project(historical,
        title="历史快照标题",
        goal="历史快照目标",
    )
    assert historical_id == project_id
    _record_agent_value(
        current_path,
        project_id,
        title="最近 Agent 标题",
        created_at="2026-09-01 10:00:00",
    )

    manifest = build_repair_manifest(current_path, historical_path)

    restorations = {(item.project_id, item.field): item for item in manifest.restorations}
    assert restorations[(project_id, "title")].replacement == "最近 Agent 标题"
    assert restorations[(project_id, "title")].evidence_kind == "task_agent_run"
    assert restorations[(project_id, "goal")].replacement == "历史快照目标"
    assert restorations[(project_id, "goal")].evidence_kind == "historical_snapshot"

    assert current.get_work_project(project_id).title == ""
    assert current.get_work_project(project_id).goal == ""


def test_manifest_archives_only_old_local_file_only_project(tmp_path):
    db_path = tmp_path / "current.sqlite3"
    store = AutoReplyStore(db_path)
    eligible_id = seed_legacy_work_project(store, title="历史材料项目")
    excluded_id = seed_legacy_work_project(store, title="仍有 TODO 的历史材料项目")
    old_source_time = datetime(2026, 1, 1, 9, 0, 0)
    project_time = old_source_time + timedelta(days=60)
    for project_id in (eligible_id, excluded_id):
        _set_project_times(
            db_path,
            project_id,
            created_at=project_time.strftime("%Y-%m-%d %H:%M:%S"),
        )
        source_ref = f"/tmp/history-{project_id}.md#sha256=test"
        seed_legacy_work_update(store,
            project_id=project_id,
            source_type="local_file",
            source_ref=source_ref,
            summary="历史材料",
        )
        store.enqueue_work_summary_input(
            "local_file",
            source_ref,
            json.dumps({
                "source": {
                    "type": "local_file",
                    "ref": source_ref,
                    "created_at": old_source_time.isoformat(),
                },
            }),
        )
    seed_legacy_work_todo(store, project_id=excluded_id, title="真实 TODO")

    manifest = build_repair_manifest(db_path)

    assert [item.project_id for item in manifest.archives] == [eligible_id]
    assert manifest.archives[0].reason == "historical_local_file_only"

    assert store.get_work_project(eligible_id).status.value == "active"
    assert store.get_work_project(excluded_id).status.value == "active"


def test_repair_uses_source_created_at_after_input_compaction(tmp_path):
    db_path = tmp_path / "repair.sqlite3"
    store = AutoReplyStore(db_path)
    project_id = seed_legacy_work_project(store, title="历史材料项目")
    _set_project_times(db_path, project_id, created_at="2026-03-01 09:00:00")
    source_ref = "/tmp/historical.md#sha256=v1"
    seed_legacy_work_update(store, project_id=project_id, source_type="local_file",
                             source_ref=source_ref, summary="历史材料")
    input_id = store.enqueue_work_summary_input("local_file", source_ref, json.dumps({
        "source": {"type": "local_file", "ref": source_ref,
                   "created_at": "2026-01-01T09:00:00"},
        "summary": "source body",
    }))
    store.mark_work_summary_input_done(input_id)
    assert store.get_work_summary_input(input_id).payload_json == "{}"
    manifest = build_repair_manifest(db_path)
    assert [item.project_id for item in manifest.archives] == [project_id]
