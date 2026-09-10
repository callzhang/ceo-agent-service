from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.agent_cron.options import ScheduledTaskOptionService
from app.agent_cron.models import ScheduledTaskSkillRef
import app.audit_web as audit_web_module
from app.audit_web import create_audit_app
from app.agent_runtime_contracts import (
    LOCAL_SERVICE_RUNTIME_CAPABILITIES,
    PROBE_VERIFIED_RUNTIME_CAPABILITIES,
    RuntimeCapabilitySnapshot,
)
from app.audit_agent import AuditAgentRunner
from app.business_skills import installed_business_skill_catalog
from app.codex_decision import DECISION_RUNTIME_CAPABILITIES
from app.consumer_agent import (
    CONSUMER_BASE_RUNTIME_CAPABILITIES,
    CONSUMER_ROLE_BOUNDARY,
    ConsumerAgentRunner,
)
from app.managed_skills import RuntimeSkillSnapshot
from app.skill_files import SkillFileService
from app.store import AutoReplyStore
from app.web_api.registration import register_console_routes
from app.wechat.decision_runner import WechatDecisionRunner
from app.wechat.prompt import WECHAT_TURN_INSTRUCTIONS


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
    runtime_id: str = "codex_oauth",
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
            runtime_id: RuntimeCapabilitySnapshot(
                route_name=runtime_id,
                capabilities=PROBE_VERIFIED_RUNTIME_CAPABILITIES,
                healthy=runtime_healthy,
                checked_at=NOW.isoformat(),
                expires_at=(NOW + timedelta(minutes=5)).isoformat(),
            )
        }
        if include_runtime_snapshot
        else {}
    )
    runtime_environment = {
        "CEO_AGENT_RUNTIME_ROUTES": runtime_id,
        "CEO_CODEX_MODEL": "gpt-5.6-sol",
    }
    if runtime_id == "claude_api":
        runtime_environment["CEO_CLAUDE_API_KEY"] = "secret"
    elif runtime_id == "friday_runtime":
        runtime_environment.update(
            {
                "CEO_FRIDAY_RUNTIME_PROJECT_ID": "project-1",
                "CEO_FRIDAY_RUNTIME_AUTH_DISABLED": "1",
            }
        )
    option_service = ScheduledTaskOptionService(
        store=store,
        environment=runtime_environment,
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
        "runtime_options": {"thinking": "high"},
        "required_runtime_capabilities": [],
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


def test_api_rejects_friday_for_local_service_task_on_create_update_and_enable(
    tmp_path: Path,
) -> None:
    client, store, ids, _wakes = _client(
        tmp_path,
        runtime_id="friday_runtime",
    )
    required = sorted(LOCAL_SERVICE_RUNTIME_CAPABILITIES)
    payload = _create_payload(ids)
    payload.update(
        {
            "runtime_id": "friday_runtime",
            "runtime_options": {},
            "required_runtime_capabilities": required,
        }
    )
    managed_seed = store.create_scheduled_task(
        migration_key="managed-local-producer-v1",
        name="Managed local producer",
        prompt="Run the exact local producer.",
        cron_expression="0 * * * * *",
        timezone_name="Asia/Shanghai",
        runtime_id="friday_runtime",
        runtime_options={},
        required_runtime_capabilities=required,
        working_directory=str(tmp_path),
        skill_refs=(
            ScheduledTaskSkillRef(
                skill_source="managed",
                skill_name="ceo-test",
                managed_skill_id=ids["skill_id"],
                managed_revision_id=ids["revision_id"],
                position=0,
            ),
        ),
        enabled=False,
        now=NOW,
    )
    update = {
        **payload,
        "name": managed_seed.name,
        "prompt": managed_seed.prompt,
        "enabled": False,
        "version": managed_seed.version,
        "skill_refs": [
            {
                "skill_source": "managed",
                "skill_name": "ceo-test",
                "managed_skill_id": ids["skill_id"],
                "managed_revision_id": ids["revision_id"],
                "position": 0,
            }
        ],
    }

    with client:
        created = client.post("/api/console/scheduled-tasks", json=payload)
        updated = client.put(
            f"/api/console/scheduled-tasks/{managed_seed.id}", json=update
        )
        enabled = client.post(
            f"/api/console/scheduled-tasks/{managed_seed.id}/enable",
            json={"version": managed_seed.version},
        )

    for response in (created, updated, enabled):
        assert response.status_code == 422
        assert response.json()["code"] == "validation_error"
        assert "missing_capabilities" in response.json()["message"]


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
    assert item["schedule_description"] == "每分钟执行 · Asia/Shanghai"
    assert item["next_run_at"] == "2026-09-08T12:01:00Z"
    assert item["runtime_options"] == {"thinking": "high"}
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
            "supported_thinking": ["low", "medium", "high", "xhigh"],
            "capabilities": [
                "local_process_execution",
                "local_service_database_access",
                "local_workspace_access",
            ],
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


@pytest.mark.parametrize(
    ("runtime_id", "supported_thinking", "thinking_status", "empty_status"),
    (
        ("codex_oauth", ["low", "medium", "high", "xhigh"], 201, 201),
        ("claude_api", [], 422, 201),
        ("friday_runtime", [], 422, 201),
    ),
)
def test_runtime_thinking_capability_is_explicit_and_enforced_without_fallback(
    tmp_path: Path,
    runtime_id: str,
    supported_thinking: list[str],
    thinking_status: int,
    empty_status: int,
) -> None:
    client, _store, ids, _wakes = _client(tmp_path, runtime_id=runtime_id)
    with_thinking = _create_payload(ids)
    with_thinking["runtime_id"] = runtime_id
    without_thinking = _create_payload(ids)
    without_thinking.update(
        {
            "name": "without thinking",
            "runtime_id": runtime_id,
            "runtime_options": {},
        }
    )

    with client:
        options = client.get("/api/console/scheduled-task-options")
        thinking = client.post("/api/console/scheduled-tasks", json=with_thinking)
        empty = client.post("/api/console/scheduled-tasks", json=without_thinking)
        update_with_thinking = {
            **with_thinking,
            "version": empty.json()["item"]["version"],
        }
        updated = client.put(
            f"/api/console/scheduled-tasks/{empty.json()['item']['id']}",
            json=update_with_thinking,
        )

    assert options.json()["runtime_options"][0]["supported_thinking"] == supported_thinking
    assert thinking.status_code == thinking_status
    assert empty.status_code == empty_status
    assert updated.status_code == (200 if thinking_status == 201 else 422)
    if thinking_status == 422:
        assert thinking.json()["code"] == "validation_error"


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
    duplicate_capabilities = _create_payload(ids)
    duplicate_capabilities["required_runtime_capabilities"] = ["local", "local"]
    invalid_cases.append(duplicate_capabilities)
    unsorted_capabilities = _create_payload(ids)
    unsorted_capabilities["required_runtime_capabilities"] = ["z", "a"]
    invalid_cases.append(unsorted_capabilities)
    invalid_capability_type = _create_payload(ids)
    invalid_capability_type["required_runtime_capabilities"] = [1]
    invalid_cases.append(invalid_capability_type)
    for forbidden_options in (
        {"api_key": "top-secret"},
        {"token": "top-secret"},
        {"secret": "top-secret"},
        {"thinking": "ultra"},
        {"thinking": {"nested": "high"}},
        {"model": "unconfigured-model"},
    ):
        bad_options = _create_payload(ids)
        bad_options["runtime_options"] = forbidden_options
        invalid_cases.append(bad_options)

    with client:
        accepted = client.post(
            "/api/console/scheduled-tasks",
            json=unavailable_but_configured,
        )
        rejected = [
            client.post("/api/console/scheduled-tasks", json=payload)
            for payload in invalid_cases
        ]
        forbidden_update = _create_payload(ids)
        forbidden_update["version"] = accepted.json()["item"]["version"]
        forbidden_update["runtime_options"] = {"token": "top-secret"}
        rejected_update = client.put(
            f"/api/console/scheduled-tasks/{accepted.json()['item']['id']}",
            json=forbidden_update,
        )

    assert accepted.status_code == 201
    assert all(response.status_code == 422 for response in rejected)
    assert all(response.json()["code"] == "validation_error" for response in rejected)
    assert rejected_update.status_code == 422
    assert rejected_update.json()["code"] == "validation_error"
    assert len(store.list_scheduled_tasks()) == 1


def test_api_never_echoes_legacy_arbitrary_runtime_options_or_secrets(
    tmp_path: Path,
) -> None:
    client, store, ids, _wakes = _client(tmp_path)
    task = store.create_scheduled_task(
        name="legacy",
        prompt="legacy task",
        cron_expression="0 * * * * *",
        timezone_name="UTC",
        runtime_id="codex_oauth",
        runtime_options={
            "thinking": "high",
            "api_key": "top-secret",
            "nested": {"token": "nested-secret"},
        },
        working_directory="",
        skill_refs=(
            ScheduledTaskSkillRef(
                skill_source="managed",
                skill_name="ceo-test",
                managed_skill_id=ids["skill_id"],
                managed_revision_id=ids["revision_id"],
                position=0,
            ),
        ),
        now=NOW,
    )
    store.create_scheduled_task_run(
        task.id,
        trigger_kind="manual",
        scheduled_for=NOW,
        now=NOW,
    )

    with client:
        detail = client.get(f"/api/console/scheduled-tasks/{task.id}")
        history = client.get(f"/api/console/scheduled-tasks/{task.id}/runs")

    rendered = detail.text + history.text
    assert "top-secret" not in rendered
    assert "nested-secret" not in rendered
    assert detail.json()["item"]["runtime_options"] == {}
    assert history.json()["items"][0]["snapshot"]["runtime_options"] == {}


def test_list_uses_one_batched_latest_run_query_instead_of_full_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, store, ids, _wakes = _client(tmp_path)
    for name in ("one", "two"):
        payload = _create_payload(ids)
        payload["name"] = name
        response = client.post("/api/console/scheduled-tasks", json=payload)
        store.create_scheduled_task_run(
            response.json()["item"]["id"],
            trigger_kind="manual",
            scheduled_for=NOW,
            now=NOW,
        )
    monkeypatch.setattr(
        store,
        "list_scheduled_task_runs",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("list endpoint loaded full run history")
        ),
    )

    with client:
        response = client.get("/api/console/scheduled-tasks")

    assert response.status_code == 200
    assert len(response.json()["items"]) == 2
    assert all(item["recent_run"] is not None for item in response.json()["items"])


def test_runs_are_bounded_and_cursor_paginated_newest_first(tmp_path: Path) -> None:
    client, store, ids, _wakes = _client(tmp_path)
    created = client.post("/api/console/scheduled-tasks", json=_create_payload(ids))
    task_id = created.json()["item"]["id"]
    for offset in range(3):
        store.create_scheduled_task_run(
            task_id,
            trigger_kind="manual",
            scheduled_for=NOW + timedelta(seconds=offset),
            now=NOW + timedelta(seconds=offset),
        )

    with client:
        first = client.get(
            f"/api/console/scheduled-tasks/{task_id}/runs",
            params={"page_size": 2},
        )
        second = client.get(
            f"/api/console/scheduled-tasks/{task_id}/runs",
            params={"page_size": 2, "cursor": first.json()["meta"]["next_cursor"]},
        )

    assert [item["id"] for item in first.json()["items"]] == [3, 2]
    assert first.json()["meta"]["has_more"] is True
    assert first.json()["meta"]["next_cursor"] == "2"
    assert [item["id"] for item in second.json()["items"]] == [1]
    assert second.json()["meta"]["has_more"] is False
    assert second.json()["meta"]["next_cursor"] == ""


def test_update_maps_concurrent_soft_delete_to_not_found(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, store, ids, _wakes = _client(tmp_path)
    created = client.post("/api/console/scheduled-tasks", json=_create_payload(ids))
    task_id = created.json()["item"]["id"]
    version = created.json()["item"]["version"]
    original_update = store.update_scheduled_task

    def delete_then_update(*args, **kwargs):
        store.delete_scheduled_task(task_id, expected_version=version, now=NOW)
        return original_update(*args, **kwargs)

    monkeypatch.setattr(store, "update_scheduled_task", delete_then_update)
    payload = _create_payload(ids)
    payload["version"] = version

    with client:
        response = client.put(
            f"/api/console/scheduled-tasks/{task_id}",
            json=payload,
        )

    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


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
    dingtalk = response.json()["service_command_options"][0]["downstream"]
    assert dingtalk["loads_skills"] is True
    assert dingtalk["skills_from_runtime_snapshot"] is False
    assert [skill["name"] for skill in dingtalk["skills"]] == [
        entry.name for entry in installed_business_skill_catalog()
    ]
    assert dingtalk["runtime_routes"][0]["unavailable_reason"] == "snapshot_missing"


def test_audit_app_reads_only_current_main_pid_runtime_and_skill_receipts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "process-state.sqlite3"
    store = AutoReplyStore(db_path)
    skill = store.create_managed_skill("ceo-process-test", "Process Test")
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
        pid=701,
        loaded={skill.id: revision.sha256},
    )
    store.record_runtime_capability_snapshot(
        RuntimeCapabilitySnapshot(
            route_name="codex_oauth",
            capabilities=PROBE_VERIFIED_RUNTIME_CAPABILITIES,
            healthy=True,
            checked_at=NOW.isoformat(),
            expires_at=(NOW + timedelta(minutes=5)).isoformat(),
        ),
        pid=701,
    )
    main_pid = 701
    monkeypatch.setattr(
        audit_web_module,
        "_launchd_service_status",
        lambda _label: {"ok": True, "pid": str(main_pid)},
    )
    monkeypatch.setenv("CEO_AGENT_RUNTIME_ROUTES", "codex_oauth")
    monkeypatch.setenv("CEO_CODEX_MODEL", "gpt-5.6-sol")
    client = TestClient(create_audit_app(db_path, scheduled_task_now=lambda: NOW))

    current = client.get("/api/console/scheduled-task-options")
    main_pid = 702
    mismatched = client.get("/api/console/scheduled-task-options")
    store.record_runtime_capability_snapshot(
        RuntimeCapabilitySnapshot(
            route_name="codex_oauth",
            capabilities=PROBE_VERIFIED_RUNTIME_CAPABILITIES,
            healthy=True,
            checked_at=(NOW - timedelta(minutes=10)).isoformat(),
            expires_at=(NOW - timedelta(minutes=5)).isoformat(),
        ),
        pid=702,
    )
    stale = client.get("/api/console/scheduled-task-options")

    assert current.json()["runtime_options"][0]["available"] is True
    current_skill = next(
        item
        for item in current.json()["managed_skill_options"]
        if item["name"] == skill.name
    )
    assert current_skill["revisions"][0]["available"] is True
    assert mismatched.json()["runtime_options"][0]["unavailable_reason"] == "snapshot_missing"
    mismatched_skill = next(
        item
        for item in mismatched.json()["managed_skill_options"]
        if item["name"] == skill.name
    )
    assert mismatched_skill["revisions"][0]["unavailable_reason"] == (
        "runtime_skill_snapshot_missing"
    )
    assert stale.json()["runtime_options"][0]["unavailable_reason"] == "snapshot_expired"


def _command_payload() -> dict[str, object]:
    return {
        "name": "检查 DingTalk 消息",
        "command": "produce-once",
        "cron_expression": "0 * * * * *",
        "timezone_name": "Asia/Shanghai",
        "enabled": True,
    }


def test_service_command_task_needs_no_runtime_and_lists_its_catalog(
    tmp_path: Path,
) -> None:
    client, store, _ids, wakes = _client(tmp_path, include_runtime_snapshot=False)

    with client:
        created = client.post("/api/console/scheduled-tasks", json=_command_payload())
        assert created.status_code == 201, created.json()
        item = created.json()["item"]
        task_id = item["id"]
        disabled = client.post(
            f"/api/console/scheduled-tasks/{task_id}/disable",
            json={"version": item["version"]},
        )
        enabled = client.post(
            f"/api/console/scheduled-tasks/{task_id}/enable",
            json={"version": disabled.json()["item"]["version"]},
        )
        run = client.post(f"/api/console/scheduled-tasks/{task_id}/run")
        detail = client.get(f"/api/console/scheduled-tasks/{task_id}")
        options = client.get("/api/console/scheduled-task-options")

    assert item["command"] == "produce-once"
    assert item["prompt"] == "" and item["runtime_id"] == ""
    assert item["skill_refs"] == [] and item["required_runtime_capabilities"] == []
    assert disabled.status_code == 200 and enabled.status_code == 200
    assert enabled.json()["item"]["enabled"] is True
    assert run.status_code == 201 and wakes == ["wake"]
    assert run.json()["item"]["snapshot"]["command"] == "produce-once"
    assert detail.json()["item"]["recent_run"]["snapshot"]["command"] == "produce-once"
    catalog = options.json()["service_command_options"]
    assert [entry["name"] for entry in catalog] == [
        "produce-once",
        "wechat-produce-once",
        "scan-meetings-once",
        "scan-oa-approvals",
        "scan-work-sources-once",
    ]
    assert [entry["display_name"] for entry in catalog] == [
        "检查钉钉消息",
        "检查微信消息",
        "检查 DingTalk 会议",
        "检查 DingTalk OA 审批",
        "扫描工作来源",
    ]
    assert all(entry["description"].strip() for entry in catalog)
    assert store.get_scheduled_task(task_id).command == "produce-once"


def test_service_command_task_rejects_agent_fields_and_unknown_commands(
    tmp_path: Path,
) -> None:
    client, _store, ids, _wakes = _client(tmp_path)
    mixed = {**_command_payload(), "prompt": "also an Agent prompt"}
    with_runtime = {**_command_payload(), "runtime_id": "codex_oauth"}
    with_refs = {**_command_payload(), "skill_refs": _create_payload(ids)["skill_refs"]}
    unknown = {**_command_payload(), "command": "unknown-service-command"}
    agent_without_runtime = {**_create_payload(ids), "runtime_id": ""}

    with client:
        responses = [
            client.post("/api/console/scheduled-tasks", json=payload)
            for payload in (mixed, with_runtime, with_refs, unknown, agent_without_runtime)
        ]

    assert [response.status_code for response in responses] == [422] * 5
    assert all(response.json()["code"] == "validation_error" for response in responses)
    assert "must not carry" in responses[0].json()["message"]
    assert "service_command_not_registered" in responses[3].json()["message"]
    assert "runtime must be nonempty" in responses[4].json()["message"]


def test_repository_seeded_service_command_is_immutable_through_the_api(
    tmp_path: Path,
) -> None:
    client, store, ids, _wakes = _client(tmp_path)
    seeded = store.create_scheduled_task(
        migration_key="dingtalk-message-check-v1",
        name="检查 DingTalk 消息",
        command="produce-once",
        cron_expression="0 * * * * *",
        timezone_name="Asia/Shanghai",
        enabled=True,
        now=NOW,
    )
    renamed = {
        **_command_payload(),
        "name": "每两分钟检查消息",
        "cron_expression": "0 */2 * * * *",
        "version": seeded.version,
    }
    switched = {
        **renamed,
        "command": "",
        "prompt": "改成 Agent",
        "runtime_id": "codex_oauth",
        "skill_refs": _create_payload(ids)["skill_refs"],
    }

    with client:
        accepted = client.put(f"/api/console/scheduled-tasks/{seeded.id}", json=renamed)
        rejected = client.put(
            f"/api/console/scheduled-tasks/{seeded.id}",
            json={**switched, "version": accepted.json()["item"]["version"]},
        )

    assert accepted.status_code == 200, accepted.json()
    assert accepted.json()["item"]["name"] == "每两分钟检查消息"
    assert accepted.json()["item"]["command"] == "produce-once"
    assert rejected.status_code == 422
    assert "service command is immutable" in rejected.json()["message"]


def test_service_command_catalog_exposes_the_live_downstream_consumer(
    tmp_path: Path,
) -> None:
    client, _store, ids, _wakes = _client(tmp_path)

    with client:
        payload = client.get("/api/console/scheduled-task-options").json()

    dingtalk, wechat, meeting, oa, work_sources = payload[
        "service_command_options"
    ]
    routes = [
        {
            "route_name": option["route_name"],
            "model": option["model"],
            "available": option["available"],
            "unavailable_reason": option["unavailable_reason"],
        }
        for option in payload["runtime_options"]
    ]
    assert routes == [
        {
            "route_name": "codex_oauth",
            "model": "gpt-5.6-sol",
            "available": True,
            "unavailable_reason": None,
        }
    ]
    assert (dingtalk["name"], dingtalk["channel"]) == ("produce-once", "dingtalk")
    assert dingtalk["downstream"] == {
        "channel": "dingtalk",
        "consumer_runners": [ConsumerAgentRunner.__name__, AuditAgentRunner.__name__],
        "instructions": CONSUMER_ROLE_BOUNDARY,
        "required_capabilities": sorted(CONSUMER_BASE_RUNTIME_CAPABILITIES),
        "loads_skills": True,
        "skills": [
            {"name": "ceo-test", "revision_id": ids["revision_id"], "revision_number": 1}
        ],
        "skills_from_runtime_snapshot": True,
        "runtime_routes": routes,
    }
    assert (wechat["name"], wechat["channel"]) == ("wechat-produce-once", "wechat")
    assert wechat["downstream"] == {
        "channel": "wechat",
        "consumer_runners": [WechatDecisionRunner.__name__],
        "instructions": WECHAT_TURN_INSTRUCTIONS,
        "required_capabilities": sorted(DECISION_RUNTIME_CAPABILITIES),
        "loads_skills": False,
        "skills": [],
        "skills_from_runtime_snapshot": False,
        "runtime_routes": routes,
    }
    assert (meeting["name"], meeting["channel"]) == ("scan-meetings-once", "meeting")
    assert meeting["downstream"]["consumer_runners"] == ["MeetingAlignmentCodexRunner"]
    assert meeting["downstream"]["loads_skills"] is False
    assert meeting["downstream"]["skills"] == []
    assert meeting["downstream"]["instructions"] is None
    assert meeting["downstream"]["instructions"] is None
    assert (oa["name"], oa["channel"]) == ("scan-oa-approvals", "dingtalk")
    assert oa["downstream"]["consumer_runners"] == [
        ConsumerAgentRunner.__name__,
        AuditAgentRunner.__name__,
    ]
    assert (work_sources["name"], work_sources["channel"]) == (
        "scan-work-sources-once",
        "work_summary",
    )
    assert work_sources["downstream"]["consumer_runners"] == ["TaskAgentRunner"]
    assert work_sources["downstream"]["loads_skills"] is False
    assert work_sources["downstream"]["instructions"] is None
