import json
import sqlite3
from datetime import datetime, timedelta

from app.store import AutoReplyStore
from app.task_project_repair import apply_manifest, build_repair_manifest


def _set_project_times(db_path, project_id, *, created_at):
    with sqlite3.connect(db_path) as db:
        db.execute(
            "update work_projects set created_at=?, updated_at=?, last_activity_at=? where id=?",
            (created_at, created_at, created_at, project_id),
        )


def _record_agent_value(db_path, project_id, *, title, created_at):
    decision = {
        "action": "update_project",
        "project": {"id": project_id, "title": title},
    }
    with sqlite3.connect(db_path) as db:
        db.execute(
            "insert into task_agent_runs(summary_input_id, decision_json, status, created_at) values(?, ?, 'completed', ?)",
            (project_id, json.dumps(decision, ensure_ascii=False), created_at),
        )


def test_manifest_restores_latest_traceable_value_then_is_idempotent(tmp_path):
    current_path = tmp_path / "current.sqlite3"
    historical_path = tmp_path / "historical.sqlite3"
    current = AutoReplyStore(current_path)
    historical = AutoReplyStore(historical_path)
    project_id = current.create_work_project(title="", goal="")
    historical_id = historical.create_work_project(
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

    first = apply_manifest(current_path, manifest)
    second = apply_manifest(current_path, manifest)

    restored = current.get_work_project(project_id)
    assert restored is not None
    assert restored.title == "最近 Agent 标题"
    assert restored.goal == "历史快照目标"
    assert first.changed_fields == 2
    assert first.audit_updates == 1
    assert second.changed_fields == 0
    assert second.audit_updates == 0


def test_manifest_archives_only_old_local_file_only_project(tmp_path):
    db_path = tmp_path / "current.sqlite3"
    store = AutoReplyStore(db_path)
    eligible_id = store.create_work_project(title="历史材料项目")
    excluded_id = store.create_work_project(title="仍有 TODO 的历史材料项目")
    old_source_time = datetime(2026, 1, 1, 9, 0, 0)
    project_time = old_source_time + timedelta(days=60)
    for project_id in (eligible_id, excluded_id):
        _set_project_times(
            db_path,
            project_id,
            created_at=project_time.strftime("%Y-%m-%d %H:%M:%S"),
        )
        source_ref = f"/tmp/history-{project_id}.md#sha256=test"
        store.create_work_update(
            project_id=project_id,
            source_type="local_file",
            source_ref=source_ref,
            summary="历史材料",
        )
        with sqlite3.connect(db_path) as db:
            db.execute(
                "insert into work_summary_inputs(source_type, source_ref, payload_json, status) values('local_file', ?, ?, 'done')",
                (
                    source_ref,
                    json.dumps(
                        {
                            "source": {
                                "type": "local_file",
                                "ref": source_ref,
                                "created_at": old_source_time.isoformat(),
                            }
                        }
                    ),
                ),
            )
    store.create_work_todo(project_id=excluded_id, title="真实 TODO")

    manifest = build_repair_manifest(db_path)

    assert [item.project_id for item in manifest.archives] == [eligible_id]
    assert manifest.archives[0].reason == "historical_local_file_only"

    result = apply_manifest(db_path, manifest)
    assert result.archived_projects == 1
    assert store.get_work_project(eligible_id).status.value == "archived"
    assert store.get_work_project(excluded_id).status.value == "active"
