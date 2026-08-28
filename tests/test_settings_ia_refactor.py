from pathlib import Path

from fastapi.testclient import TestClient

from app.audit_rules import render_audit_rules, validate_audit_rules_text
from app.audit_web import create_audit_app, render_settings_page
from app.config import read_env_file
from app.consumer_agent import consumer_developer_instructions
from app.developer_prompt import render_user_prompt_template
from app.store import AgentRole, AutoReplyStore


def _store(tmp_path: Path) -> AutoReplyStore:
    return AutoReplyStore(tmp_path / "worker.sqlite3")


def test_info_moves_current_values_into_configuration(tmp_path: Path):
    store = _store(tmp_path)

    info = render_settings_page(store, active_tab="info")
    configuration = render_settings_page(store, active_tab="configuration")

    assert '<table class="config-info-values">' not in info
    assert "Producer 路由配置" in info
    assert "Prompt config" not in info
    assert "Configuration" in configuration
    assert "CEO_MENTION_ALIASES" in configuration
    assert "CEO_PROMPT_VAR_RESPONSIBILITY_SUMMARY" in configuration


def test_prompts_page_uses_pill_tabs_and_template_preview_toggle(tmp_path: Path):
    store = _store(tmp_path)

    response = TestClient(create_audit_app(store.path)).get(
        "/settings?tab=prompts&prompt=user&view=preview"
    )

    assert response.status_code == 200
    assert 'aria-label="Prompt sections"' in response.text
    assert 'href="/settings?tab=prompts&prompt=developer&view=preview"' in response.text
    assert 'href="/settings?tab=prompts&prompt=user&view=preview"' in response.text
    assert "Rendered preview · sample runtime context" in response.text
    assert "Prompt config" not in response.text


def test_user_prompt_editor_exposes_named_template_and_explains_runtime_variables(
    tmp_path: Path, monkeypatch
):
    template_path = tmp_path / "user_prompt.md"
    template_path.write_text(
        "{{style_lines}}\n---\n{{current_message}}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CEO_USER_PROMPT_TEMPLATE_PATH", str(template_path))
    page = TestClient(create_audit_app(tmp_path / "worker.sqlite3")).get(
        "/settings?tab=prompts&prompt=user&view=template"
    )

    assert page.status_code == 200
    assert "{{style_lines}}" in page.text
    assert "服务在运行时自动替换" in page.text
    assert "会话名和会话类型" in page.text
    assert "不要手动填写" in page.text


def test_connectors_page_contains_wechat_without_separate_settings_tab(tmp_path: Path):
    store = _store(tmp_path)

    page = render_settings_page(store, active_tab="connectors", connector="wechat")

    assert "Connectors" in page
    assert 'aria-label="Connector sections"' in page
    assert 'href="/settings?tab=connectors&connector=wechat"' in page
    assert "微信自动回复对象" in page
    assert 'href="/settings?tab=wechat"' not in page


def test_status_and_attention_are_separate_pages(tmp_path: Path):
    store = _store(tmp_path)
    store.enqueue_work_summary_input("reply_attempt", "1", '{"summary":"待处理"}')

    status = render_settings_page(store, active_tab="status")
    attention = render_settings_page(store, active_tab="attention")

    assert "Runtime Monitor" in status
    assert "No pending, processing, or failed queue items." not in status
    assert "Attention" in attention
    assert "待处理" in attention


def test_user_prompt_named_runtime_variables_render_with_legacy_function_compatibility():
    rendered = render_user_prompt_template(
        "{{current_message}}\n<code: app.user_prompt_blocks:sender_org_block()>",
        {
            "current_message": "当前消息",
            "sender_org": "组织信息",
        },
    )

    assert "当前消息" in rendered
    assert "组织信息" in rendered


def test_audit_rules_render_configured_principal_with_allowlisted_placeholder(
    tmp_path: Path,
    monkeypatch,
):
    path = tmp_path / "audit_rules.md"
    path.write_text("Needs {{principal}}'s handling.", encoding="utf-8")
    monkeypatch.setenv("CEO_AUDIT_RULES_TEMPLATE_PATH", str(path))
    monkeypatch.setenv("USER_ALIAS", "Alex")

    validate_audit_rules_text("Needs {{principal}}'s handling.")
    rendered = render_audit_rules(AgentRole.CONSUMER)

    assert "Alex's handling" in rendered
    assert "Derek" not in rendered


def test_active_consumer_boundary_uses_configured_principal(monkeypatch):
    monkeypatch.setenv("USER_ALIAS", "Alex")
    instructions = consumer_developer_instructions("Check the candidate.")

    assert "ask Alex how to finish" in instructions
    assert "ask Derek how to finish" not in instructions


def test_configuration_post_persists_system_and_prompt_values_to_same_env_file(
    tmp_path: Path, monkeypatch
):
    env_path = tmp_path / ".env"
    monkeypatch.setenv("CEO_ENV_FILE", str(env_path))
    monkeypatch.setenv("USER_ALIAS", "")
    monkeypatch.setenv("CEO_PROMPT_VAR_RESPONSIBILITY_SUMMARY", "")
    client = TestClient(create_audit_app(tmp_path / "worker.sqlite3"))

    response = client.post(
        "/config/configuration",
        data={
            "config_key": ["USER_ALIAS", "CEO_PROMPT_VAR_RESPONSIBILITY_SUMMARY"],
            "config_value": ["Alex", "Handle only the configured scope."],
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    values = read_env_file(env_path)
    assert values["USER_ALIAS"] == "Alex"
    assert values["CEO_PROMPT_VAR_RESPONSIBILITY_SUMMARY"] == (
        "Handle only the configured scope."
    )


def test_configuration_post_rejects_invalid_scheduling_value_without_overwrite(
    tmp_path: Path, monkeypatch
):
    env_path = tmp_path / ".env"
    env_path.write_text("CEO_PRODUCER_INTERVAL_SECONDS=60\n", encoding="utf-8")
    monkeypatch.setenv("CEO_ENV_FILE", str(env_path))
    client = TestClient(create_audit_app(tmp_path / "worker.sqlite3"))

    response = client.post(
        "/config/configuration",
        data={
            "config_key": ["CEO_PRODUCER_INTERVAL_SECONDS"],
            "config_value": ["not-a-number"],
        },
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert read_env_file(env_path)["CEO_PRODUCER_INTERVAL_SECONDS"] == "60"
