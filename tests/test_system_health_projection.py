from pathlib import Path
from types import SimpleNamespace

import app.audit_web as audit_web_module
from app.audit_web import build_worker_status_payload
from app.store import AutoReplyStore
from app.web_api.attention import group_attention_rows


def _running_service(_label: str) -> dict[str, object]:
    return {"ok": True, "state": "running", "detail": "running"}


def test_recent_service_errors_project_to_system_health_observation(
    monkeypatch, tmp_path: Path
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    monkeypatch.setattr(audit_web_module, "_launchd_service_status", _running_service)
    monkeypatch.setattr(
        audit_web_module,
        "scan_hourly_quality",
        lambda _path: SimpleNamespace(
            checked_at="2026-08-29T22:00:00+00:00",
            violations=(
                SimpleNamespace(
                    source="errors",
                    code="recent_error",
                    count=2,
                    detail="a service error was recorded within the four-hour repair window",
                ),
            ),
        ),
    )

    payload = build_worker_status_payload(store)

    assert payload["service"]["state"] == "running"
    assert payload["system_health"] == {
        "state": "observing",
        "detail": "2 recent service errors remain in the four-hour health observation window.",
        "checked_at": "2026-08-29T22:00:00+00:00",
        "violations": 2,
    }


def test_non_error_quality_violation_projects_to_degraded_system_health(
    monkeypatch, tmp_path: Path
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    monkeypatch.setattr(audit_web_module, "_launchd_service_status", _running_service)
    monkeypatch.setattr(
        audit_web_module,
        "scan_hourly_quality",
        lambda _path: SimpleNamespace(
            checked_at="2026-08-29T22:00:00+00:00",
            violations=(
                SimpleNamespace(
                    source="reply_tasks",
                    code="failed",
                    count=1,
                    detail="a reply task needs recovery",
                ),
            ),
        ),
    )

    assert build_worker_status_payload(store)["system_health"]["state"] == "degraded"


def test_pending_work_item_explains_its_status_instead_of_missing_error():
    [group] = group_attention_rows([
        {
            "category": "Work item",
            "id": "7",
            "status": "pending",
            "context": "ai_minutes",
            "summary": "Review the meeting follow-up.",
            "updated_at": "2026-08-29 22:00:00",
            "error": "",
        },
    ])

    assert group.root_cause == "已入队，等待执行。"
    assert group.detail_label == "状态"
    assert group.detail == "已入队，等待执行。"
    assert group.error == ""


def test_missing_failed_error_is_rendered_as_a_recording_defect():
    [group] = group_attention_rows([
        {
            "category": "Work item",
            "id": "8",
            "status": "failed",
            "context": "ai_minutes",
            "summary": "Review the meeting follow-up.",
            "updated_at": "2026-08-29 22:00:00",
            "error": "",
        },
    ])

    assert group.detail_label == "错误"
    assert group.detail == "任务失败，但未记录具体错误；请检查执行历史和服务日志。"
