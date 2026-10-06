from app.agent_runtime_config import load_runtime_config
from app.prompt_preview import current_prompt_preview, historical_prompt_preview
from app.store import AgentRole, AutoReplyStore


def test_current_preview_renders_full_role_without_creating_profile_or_rule_files(monkeypatch, tmp_path):
    monkeypatch.setenv("CEO_WORK_PROFILE_PATH", str(tmp_path / "missing-profile.md"))
    monkeypatch.setenv("CEO_AUDIT_RULES_TEMPLATE_PATH", str(tmp_path / "missing-rules.md"))
    monkeypatch.setenv("CEO_WORKSPACE", str(tmp_path / "materials"))
    config = load_runtime_config({"CEO_AGENT_RUNTIME_ROUTES": "codex_oauth,claude_oauth"})
    store = AutoReplyStore(tmp_path / "preview.sqlite3")
    consumer = current_prompt_preview(store, role="consumer", config=config, route_name="codex_oauth", current_time="2026-10-05T18:00:00-07:00")
    audit = current_prompt_preview(store, role="audit", config=config, route_name="claude_oauth", current_time="2026-10-05T18:00:00-07:00")
    assert "## Pydantic Wire Contract" in consumer["developer_instructions"]
    assert consumer["runtime_context"] in consumer["developer_instructions"]
    assert "所选 runtime 的配置预览" in consumer["runtime_context"]
    assert "实际 runtime：" not in consumer["runtime_context"]
    assert "对方时区" in consumer["developer_instructions"]
    assert "agent_cli.consumer_artifact_write" in consumer["runtime_context"]
    assert "agent_cli.consumer_artifact_write" not in audit["runtime_context"]
    assert "只读审核" in audit["runtime_context"]
    assert "<developer-instructions>" in audit["submitted_input"]
    assert consumer["task_id"] is None and "未绑定" in consumer["task_prompt"]
    assert not (tmp_path / "missing-profile.md").exists()
    assert not (tmp_path / "missing-rules.md").exists()
    assert not (tmp_path / "materials").exists()


def task_and_run(store):
    store.enqueue_reply_task(conversation_id="g", conversation_title="Group", single_chat=False,
        trigger_message_id="m", trigger_create_time="2026-10-05T12:00:00+00:00",
        trigger_sender="Source", trigger_text="Original request", execution_generation="g1")
    task = store.claim_reply_tasks(limit=1)[0]
    run = store.claim_agent_run(task.id, task.execution_generation, role=AgentRole.CONSUMER,
        proposal_revision=0, turn_attempt=0, parent_agent_run_id=None, operation_id="", owner="test").run
    return task, run


def test_historical_preview_reads_saved_input_not_current_templates(tmp_path, monkeypatch):
    store = AutoReplyStore(tmp_path / "preview.sqlite3")
    task, run = task_and_run(store)
    event = {"type": "runtime.prompt", "role": "consumer", "task_id": task.id,
        "runtime_kind": "codex_cli", "route_name": "old-route", "model": "old-model",
        "rendered_at": "2026-10-05T10:00:00+00:00", "developer_instructions": "Old instructions",
        "task_prompt": "Original submitted task", "submitted_input": "Original submitted task",
        "runtime_context": "Old context", "redacted": False}
    event.update(runtime_attempt_id=81, execution_generation="g1", proposal_revision=2,
                 invocation_facts={"stage_index": 1})
    store.append_agent_run_event(run.id, event, owner="test")
    monkeypatch.setenv("USER_ALIAS", "Different principal today")
    result = historical_prompt_preview(store, run_id=run.id)
    assert result["developer_instructions"] == "Old instructions"
    assert result["route_name"] == "old-route"
    assert result["rendered_at"] == event["rendered_at"]
    assert result["mode"] == "historical" and result["run_id"] == run.id
    assert result["runtime_attempt_id"] == 81 and result["execution_generation"] == "g1"
    assert result["proposal_revision"] == 2 and result["stage_index"] == 1
    assert result["submission_state"] == "prepared" and "不能称为模型已经收到" in result["reason"]
    assert store.get_reply_task(task.id).status == task.status
    assert len(store.get_agent_run(run.id).tool_events) == 1


def test_old_run_without_input_is_unavailable_and_not_rebuilt(tmp_path):
    store = AutoReplyStore(tmp_path / "preview.sqlite3")
    task, run = task_and_run(store)
    result = historical_prompt_preview(store, run_id=run.id)
    assert result["status"] == "unavailable"
    assert result["developer_instructions"] == ""
    assert "未保存" in result["reason"]
    assert store.get_agent_run(run.id).tool_events == []


def test_settings_full_preview_endpoint_is_read_only_and_role_specific(tmp_path, monkeypatch):
    from tests.test_console_web_api import _client

    monkeypatch.setenv("CEO_WORKSPACE", str(tmp_path / "materials"))
    monkeypatch.setenv("CEO_AGENT_RUNTIME_ROUTES", "codex_oauth,claude_oauth")
    with _client(tmp_path) as client:
        response = client.get("/api/console/settings/prompt-preview?role=consumer&route_name=codex_oauth")
        assert response.status_code == 200
        item = response.json()["item"]
        assert "## Pydantic Wire Contract" in item["developer_instructions"]
        assert "运行环境与能力说明" in item["runtime_context"]
        assert item["mode"] == "current" and item["task_id"] is None
        assert client.get("/api/console/settings/prompt-preview?role=invalid").status_code == 422
        assert client.get("/api/console/settings/prompt-preview?task_id=99999").status_code == 404
        assert client.get("/api/console/settings/prompt-preview?route_name=not_configured").status_code == 422
        assert client.get("/api/console/settings/prompt-preview?run_id=0").status_code == 422


def test_current_task_preview_preserves_original_source_binding(tmp_path, monkeypatch):
    monkeypatch.setenv("CEO_WORKSPACE", str(tmp_path / "materials"))
    store = AutoReplyStore(tmp_path / "preview.sqlite3")
    task, run = task_and_run(store)
    store.append_agent_run_event(run.id, {"type": "runtime.prompt", "role": "consumer", "rendered_at": "2026-10-05T10:00:00+00:00",
        "task_prompt": 'Original request\n"message_id": "m"\nFull materials, receipts and Audit feedback',
        "invocation_facts": {"skill_protocol": "Pinned source Skill", "skill_protocol_source": "task_override", "stage_index": 0}}, owner="test")
    result = current_prompt_preview(store, role="consumer", config=load_runtime_config({}), task_id=task.id,
        current_time="2026-10-05T18:00:00-07:00")
    assert "Original request" in result["task_prompt"]
    assert '"message_id": "m"' in result["task_prompt"]
    assert result["task_id"] == task.id
    assert str(tmp_path / "materials" / "consumer-artifacts" / str(task.id) / "g1") in result["runtime_context"]
    assert "Full materials, receipts and Audit feedback" in result["task_prompt"]
    assert "Pinned source Skill" in result["developer_instructions"]
    assert len(store.get_agent_run(run.id).tool_events) == 1


def test_current_bound_task_without_complete_saved_input_is_unavailable(tmp_path):
    store = AutoReplyStore(tmp_path / "preview.sqlite3")
    task, run = task_and_run(store)
    result = current_prompt_preview(store, role="consumer", config=load_runtime_config({}), task_id=task.id)
    assert result["status"] == "unavailable"
    assert result["task_prompt"] == ""
    assert "未保存" in result["reason"]
    assert store.get_agent_run(run.id).tool_events == []


def test_current_default_skill_catalog_is_rendered_from_current_configuration(tmp_path, monkeypatch):
    store = AutoReplyStore(tmp_path / "preview.sqlite3")
    task, run = task_and_run(store)
    store.append_agent_run_event(run.id, {"type": "runtime.prompt", "role": "consumer", "rendered_at": "2026-10-05T10:00:00+00:00",
        "task_prompt": "Complete original task", "invocation_facts": {"skill_protocol": "Old default catalog",
        "skill_protocol_source": "runtime_catalog"}}, owner="test")
    monkeypatch.setattr("app.prompt_preview.default_consumer_skill_protocol", lambda: "Current default catalog")
    result = current_prompt_preview(store, role="consumer", config=load_runtime_config({}), task_id=task.id)
    assert "Current default catalog" in result["developer_instructions"]
    assert "Old default catalog" not in result["developer_instructions"]


def test_historical_preview_can_select_earlier_invoked_attempt(tmp_path):
    import pytest

    store = AutoReplyStore(tmp_path / "preview.sqlite3")
    task, run = task_and_run(store)
    for attempt in (81, 82):
        store.append_agent_run_event(run.id, {"type": "runtime.prompt", "role": "consumer", "task_id": task.id,
            "runtime_attempt_id": attempt, "runtime_kind": "codex_cli", "route_name": f"route-{attempt}", "model": "model",
            "rendered_at": "2026-10-05T10:00:00+00:00", "developer_instructions": f"instructions-{attempt}",
            "task_prompt": "task", "submitted_input": "task", "runtime_context": "context"}, owner="test")
        if attempt == 81:
            store.append_agent_run_event(run.id, {"type": "runtime.prompt.invoked", "runtime_attempt_id": attempt}, owner="test")
    latest = historical_prompt_preview(store, run_id=run.id)
    assert latest["runtime_attempt_id"] == 82 and latest["submission_state"] == "prepared"
    assert [item["runtime_attempt_id"] for item in latest["attempts"]] == [81, 82]
    earlier = historical_prompt_preview(store, run_id=run.id, runtime_attempt_id=81)
    assert earlier["developer_instructions"] == "instructions-81" and earlier["submission_state"] == "invoked"
    with pytest.raises(LookupError):
        historical_prompt_preview(store, run_id=run.id, runtime_attempt_id=99)


def test_history_api_selects_attempt_and_rejects_mismatched_binding(tmp_path):
    from tests.test_console_web_api import _client

    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task, run = task_and_run(store)
    store.append_agent_run_event(run.id, {"type": "runtime.prompt", "role": "consumer", "runtime_attempt_id": 81,
        "runtime_kind": "codex_cli", "route_name": "saved-route", "model": "saved-model",
        "rendered_at": "2026-10-05T10:00:00+00:00", "developer_instructions": "Saved instructions",
        "task_prompt": "Saved task", "submitted_input": "Saved task", "runtime_context": "Saved context"}, owner="test")
    with _client(tmp_path) as client:
        url = f"/api/console/settings/prompt-preview?run_id={run.id}"
        response = client.get(url + "&runtime_attempt_id=81")
        assert response.status_code == 200
        assert response.json()["item"]["runtime_attempt_id"] == 81
        assert client.get(url + "&runtime_attempt_id=99").status_code == 404
        assert client.get(url + "&role=audit").status_code == 422
        assert client.get(url + "&task_id=999").status_code == 422
        assert client.get(url + "&route_name=current-route").status_code == 422
        assert client.get("/api/console/settings/prompt-preview?runtime_attempt_id=81").status_code == 422
    assert len(store.get_agent_run(run.id).tool_events) == 1
    assert store.get_reply_task(task.id).status == task.status
