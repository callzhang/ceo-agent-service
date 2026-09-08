from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.agent_cron.options import ScheduledTaskOptionService
from app.audit_web import create_audit_app
from app.agent_runtime_contracts import (
    PROBE_VERIFIED_RUNTIME_CAPABILITIES,
    RuntimeCapabilitySnapshot,
)
from app.managed_skills import RuntimeSkillSnapshot
from app.skill_files import SkillFileService
from app.store import AutoReplyStore
from app.web_api.registration import register_console_routes


NOW = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)


def _managed_content(name: str) -> str:
    return f"""---
name: {name}
description: Managed scheduled task capability
metadata:
  managed_by: ceo-agent-service
---

# Managed capability
"""


def _client(
    tmp_path: Path,
    *,
    runtime_healthy: bool = True,
    include_runtime_snapshot: bool = True,
) -> tuple[TestClient, AutoReplyStore, dict[str, int], list[str]]:
    store = AutoReplyStore(tmp_path / "cron-api.sqlite3")
    skill = store.create_managed_skill("ceo-test", "CEO Test")
    revision = store.create_managed_skill_revision(
        skill.id,
        _managed_content(skill.name),
        source="settings",
    )
    config = store.create_runtime_skill_config(
        {skill.id: revision.id},
        expected_parent_id=None,
    )
    store.record_runtime_skill_load(
        config.id,
        pid=123,
        loaded={skill.id: revision.sha256},
    )
    operation_root = tmp_path / "operation-skills"
    operation_path = operation_root / "dingtalk-chat" / "SKILL.md"
    operation_path.parent.mkdir(parents=True)
    operation_path.write_text(
        "---\nname: dingtalk-chat\ndescription: Read DingTalk messages\n---\n# Chat\n",
        encoding="utf-8",
    )
    snapshots = (
        {
            "codex_oauth": RuntimeCapabilitySnapshot(
                route_name="codex_oauth",
                capabilities=PROBE_VERIFIED_RUNTIME_CAPABILITIES,
                healthy=runtime_healthy,
                checked_at=NOW.isoformat(),
                expires_at=(NOW + timedelta(minutes=5)).isoformat(),
            )
        }
        if include_runtime_snapshot
        else {}
    )
    option_service = ScheduledTaskOptionService(
        store=store,
        environment={
            "CEO_AGENT_RUNTIME_ROUTES": "codex_oauth",
            "CEO_CODEX_MODEL": "gpt-5.6-sol",
        },
        runtime_snapshots=snapshots,
        operation_skill_files=SkillFileService(operation_root),
        runtime_skill_snapshot=RuntimeSkillSnapshot(config.id, (revision,)),
        now=lambda: NOW,
    )
    wakes: list[str] = []
    app = FastAPI()
    register_console_routes(
        app,
        store_factory=lambda: store,
        status_payload_factory=lambda: {},
        feedback_backlog_factory=lambda: {
            "processing": 0,
            "failed": 0,
            "retryable": 0,
        },
        attention_rows_factory=lambda: [],
        scheduled_task_option_service_factory=lambda: option_service,
        scheduled_task_wake_callback=lambda: wakes.append("wake"),
        scheduled_task_now=lambda: NOW,
    )
    return TestClient(app), store, {
        "skill_id": skill.id,
        "revision_id": revision.id,
    }, wakes


def _create_payload(ids: dict[str, int]) -> dict[str, object]:
    return {
        "name": "检查钉钉消息",
        "prompt": "检查新的钉钉消息并处理需要 CEO 关注的内容。",
        "cron_expression": "0 * * * * *",
        "timezone_name": "Asia/Shanghai",
        "runtime_id": "codex_oauth",
        "runtime_options": {"reasoning_effort": "high"},
        "working_directory": "/tmp/ceo-agent",
        "enabled": True,
        "skill_refs": [
            {
                "skill_source": "managed",
                "skill_name": "ceo-test",
                "managed_skill_id": ids["skill_id"],
                "managed_revision_id": ids["revision_id"],
                "position": 0,
            },
            {
                "skill_source": "operation",
                "skill_name": "dingtalk-chat",
                "position": 1,
            },
        ],
    }


def test_scheduled_task_crud_returns_derived_schedule_and_exact_refs(
    tmp_path: Path,
) -> None:
    client, _store, ids, _wakes = _client(tmp_path)

    with client:
        created = client.post(
            "/api/console/scheduled-tasks",
            json=_create_payload(ids),
        )
        task_id = created.json()["item"]["id"]
        detail = client.get(f"/api/console/scheduled-tasks/{task_id}")
        listed = client.get("/api/console/scheduled-tasks")
        update_payload = _create_payload(ids)
        update_payload.update(
            {
                "version": created.json()["item"]["version"],
                "name": "检查重要钉钉消息",
                "cron_expression": "30 * * * * *",
            }
        )
        updated = client.put(
            f"/api/console/scheduled-tasks/{task_id}",
            json=update_payload,
        )

    assert created.status_code == 201
    item = created.json()["item"]
    assert item["schedule_description"] == "0 * * * * * · Asia/Shanghai"
    assert item["next_run_at"] == "2026-09-08T12:01:00Z"
    assert item["runtime_options"] == {"reasoning_effort": "high"}
    assert item["recent_run"] is None
    assert item["skill_refs"] == [
        {
            "skill_source": "managed",
            "skill_name": "ceo-test",
            "managed_skill_id": ids["skill_id"],
            "managed_revision_id": ids["revision_id"],
            "position": 0,
        },
        {
            "skill_source": "operation",
            "skill_name": "dingtalk-chat",
            "managed_skill_id": None,
            "managed_revision_id": None,
            "position": 1,
        },
    ]
    assert detail.status_code == 200
    assert detail.json()["item"] == item
    assert listed.status_code == 200
    assert listed.json()["items"] == [item]
    assert updated.status_code == 200
    assert updated.json()["item"]["name"] == "检查重要钉钉消息"
    assert updated.json()["item"]["version"] == item["version"] + 1
    assert updated.json()["item"]["next_run_at"] == "2026-09-08T12:00:30Z"


def test_scheduled_task_version_conflicts_are_409_for_mutations(tmp_path: Path) -> None:
    client, _store, ids, _wakes = _client(tmp_path)

    with client:
        created = client.post("/api/console/scheduled-tasks", json=_create_payload(ids))
        task_id = created.json()["item"]["id"]
        stale = _create_payload(ids)
        stale["version"] = 999
        updated = client.put(f"/api/console/scheduled-tasks/{task_id}", json=stale)
        enabled = client.post(
            f"/api/console/scheduled-tasks/{task_id}/enable",
            json={"version": 999},
        )
        deleted = client.delete(
            f"/api/console/scheduled-tasks/{task_id}",
            params={"version": 999},
        )

    for response in (updated, enabled, deleted):
        assert response.status_code == 409
        assert response.json()["code"] == "conflict"


def test_update_cannot_silently_change_enabled_state(tmp_path: Path) -> None:
    client, store, ids, _wakes = _client(tmp_path)

    with client:
        created = client.post("/api/console/scheduled-tasks", json=_create_payload(ids))
        task_id = created.json()["item"]["id"]
        update = _create_payload(ids)
        update["version"] = created.json()["item"]["version"]
        update["enabled"] = False
        response = client.put(
            f"/api/console/scheduled-tasks/{task_id}",
            json=update,
        )

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"
    assert store.get_scheduled_task(task_id).enabled is True


def test_enable_disable_manual_run_delete_and_deleted_history(tmp_path: Path) -> None:
    client, store, ids, wakes = _client(tmp_path)

    with client:
        created = client.post("/api/console/scheduled-tasks", json=_create_payload(ids))
        task_id = created.json()["item"]["id"]
        disabled = client.post(
            f"/api/console/scheduled-tasks/{task_id}/disable",
            json={"version": created.json()["item"]["version"]},
        )
        enabled = client.post(
            f"/api/console/scheduled-tasks/{task_id}/enable",
            json={"version": disabled.json()["item"]["version"]},
        )
        manual = client.post(f"/api/console/scheduled-tasks/{task_id}/run")
        before_delete = client.get(f"/api/console/scheduled-tasks/{task_id}")
        deleted = client.delete(
            f"/api/console/scheduled-tasks/{task_id}",
            params={"version": enabled.json()["item"]["version"]},
        )
        missing = client.get(f"/api/console/scheduled-tasks/{task_id}")
        history = client.get(f"/api/console/scheduled-tasks/{task_id}/runs")

    assert disabled.status_code == 200
    assert disabled.json()["item"]["enabled"] is False
    assert disabled.json()["item"]["next_run_at"] is None
    assert enabled.status_code == 200
    assert enabled.json()["item"]["enabled"] is True
    assert manual.status_code == 201
    assert manual.json()["item"]["trigger_kind"] == "manual"
    assert manual.json()["item"]["dispatch_status"] == "pending"
    assert manual.json()["item"]["scheduled_for"] == "2026-09-08T12:00:00Z"
    assert wakes == ["wake"]
    assert before_delete.json()["item"]["next_run_at"] == "2026-09-08T12:01:00Z"
    assert before_delete.json()["item"]["recent_run"] == manual.json()["item"]
    assert deleted.status_code == 200
    assert deleted.json()["item"]["deleted_at"] == "2026-09-08T12:00:00Z"
    assert missing.status_code == 404
    assert history.status_code == 200
    assert history.json()["scheduled_task"]["id"] == task_id
    assert history.json()["scheduled_task"]["deleted_at"] is not None
    assert history.json()["items"] == [manual.json()["item"]]
    assert store.list_scheduled_task_runs(task_id)[0].trigger_kind == "manual"


def test_options_expose_runtime_and_skill_availability_without_fabrication(
    tmp_path: Path,
) -> None:
    client, _store, ids, _wakes = _client(
        tmp_path,
        include_runtime_snapshot=False,
    )

    with client:
        response = client.get("/api/console/scheduled-task-options")

    assert response.status_code == 200
    payload = response.json()
    assert payload["runtime_options"] == [
        {
            "route_name": "codex_oauth",
            "runtime_kind": "codex_cli",
            "credential_mode": "local_oauth",
            "model": "gpt-5.6-sol",
            "available": False,
            "unavailable_reason": "snapshot_missing",
        }
    ]
    assert payload["managed_skill_options"][0]["revisions"] == [
        {
            "revision_id": ids["revision_id"],
            "revision_number": 1,
            "sha256": payload["managed_skill_options"][0]["revisions"][0]["sha256"],
            "source": "settings",
            "available": True,
            "unavailable_reason": None,
        }
    ]
    assert payload["operation_skill_options"][0]["name"] == "dingtalk-chat"


def test_scheduled_task_validation_rejects_bad_schedule_runtime_refs_and_extras(
    tmp_path: Path,
) -> None:
    client, store, ids, _wakes = _client(tmp_path, runtime_healthy=False)
    unavailable_but_configured = _create_payload(ids)
    invalid_cases = []
    bad_cron = _create_payload(ids)
    bad_cron["cron_expression"] = "* * * * *"
    invalid_cases.append(bad_cron)
    bad_timezone = _create_payload(ids)
    bad_timezone["timezone_name"] = "Mars/Olympus"
    invalid_cases.append(bad_timezone)
    bad_runtime = _create_payload(ids)
    bad_runtime["runtime_id"] = "not_configured"
    invalid_cases.append(bad_runtime)
    bad_managed = _create_payload(ids)
    bad_managed["skill_refs"] = [
        {
            "skill_source": "managed",
            "skill_name": "ceo-test",
            "managed_skill_id": ids["skill_id"],
            "managed_revision_id": 999,
            "position": 0,
        }
    ]
    invalid_cases.append(bad_managed)
    bad_operation = _create_payload(ids)
    bad_operation["skill_refs"] = [
        {
            "skill_source": "operation",
            "skill_name": "missing-operation",
            "position": 0,
        }
    ]
    invalid_cases.append(bad_operation)
    extra = _create_payload(ids)
    extra["connector"] = "dingtalk"
    invalid_cases.append(extra)
    no_skills = _create_payload(ids)
    no_skills["skill_refs"] = []
    invalid_cases.append(no_skills)

    with client:
        accepted = client.post(
            "/api/console/scheduled-tasks",
            json=unavailable_but_configured,
        )
        rejected = [
            client.post("/api/console/scheduled-tasks", json=payload)
            for payload in invalid_cases
        ]

    assert accepted.status_code == 201
    assert all(response.status_code == 422 for response in rejected)
    assert all(response.json()["code"] == "validation_error" for response in rejected)
    assert len(store.list_scheduled_tasks()) == 1


def test_audit_app_mounts_cron_api_without_fabricating_process_snapshots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CEO_AGENT_RUNTIME_ROUTES", "codex_oauth")
    monkeypatch.setenv("CEO_CODEX_MODEL", "gpt-5.6-sol")
    app = create_audit_app(tmp_path / "audit.sqlite3")

    response = TestClient(app).get("/api/console/scheduled-task-options")

    assert response.status_code == 200
    assert response.json()["runtime_options"][0]["available"] is False
    assert response.json()["runtime_options"][0]["unavailable_reason"] == "snapshot_missing"
    revisions = [
        revision
        for skill in response.json()["managed_skill_options"]
        for revision in skill["revisions"]
    ]
    assert revisions
    assert all(revision["available"] is False for revision in revisions)
    assert {
        revision["unavailable_reason"] for revision in revisions
    } == {"runtime_skill_snapshot_missing"}
