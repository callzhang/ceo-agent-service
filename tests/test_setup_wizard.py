import json
import subprocess
from pathlib import Path
from typing import get_args

import pytest
from pydantic import ValidationError

from app.channel_gate import ChannelGateResult, ChannelGateState
from app.service_codex_config import load_service_mcp_servers
from app.setup_wizard import (
    SETUP_WIZARD_STEPS,
    build_wizard_status,
    check_data_corpus,
    check_service_config,
    check_setup_step,
    check_work_profile,
    get_action_definition,
    get_step_definition,
    redact_setup_output,
    run_setup_action,
    runtime_route_setup_statuses,
)
from app.setup_wizard_models import (
    SetupAction,
    SetupActionStatus,
    SetupStepStatus,
    SetupWizardEvent,
    SetupWizardStatus,
)
from app.store import AutoReplyStore


def test_setup_wizard_steps_are_ordered_and_gated():
    assert [step.id for step in SETUP_WIZARD_STEPS] == [
        "preflight",
        "cli_components",
        "dingtalk_cli",
        "lark_cli",
        "mcp",
        "service_config",
        "wechat_connection",
        "data_corpus",
        "work_profile",
        "dry_run",
        "launchd",
        "live_send",
    ]
    assert get_step_definition("mcp").depends_on == ("cli_components",)
    assert get_step_definition("dingtalk_cli").depends_on == ("preflight",)
    assert get_step_definition("lark_cli").depends_on == ("preflight",)
    # Memory Connector is optional for local channel setup, and WeChat can be
    # connected as soon as the local checkout passes preflight.
    assert get_step_definition("service_config").depends_on == ("cli_components",)
    assert get_step_definition("wechat_connection").depends_on == ("preflight",)
    assert get_step_definition("data_corpus").depends_on == ("service_config",)
    assert [a.id for a in get_step_definition("wechat_connection").actions] == [
        "check_wechat_connection",
        "connect_wechat",
    ]
    assert get_step_definition("launchd").depends_on == ("dry_run",)
    assert get_step_definition("live_send").depends_on == ("dry_run",)
    assert get_action_definition("setup_cli_components").step_id == "cli_components"
    assert get_action_definition("setup_mcp").step_id == "mcp"


def test_preflight_accepts_central_conda_python(monkeypatch, tmp_path: Path):
    (tmp_path / "README.md").write_text("ready\n", encoding="utf-8")
    (tmp_path / "app").mkdir()
    (tmp_path / "tests").mkdir()
    python = tmp_path / "conda" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    python.chmod(0o755)
    monkeypatch.setenv("CEO_PYTHON", str(python))

    status = check_setup_step("preflight", repo_root=tmp_path)

    assert status.status == "done"
    assert status.summary == "Repository checkout and central Conda Python are ready."
    assert status.evidence == {"conda_python": True}


def test_wechat_setup_is_available_without_mcp_or_service_config(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.upsert_setup_wizard_step(
        step_id="preflight",
        status="done",
        summary="ready",
    )

    status = build_wizard_status(store)
    wechat = next(step for step in status.steps if step.step_id == "wechat_connection")

    assert wechat.status == "not_started"
    assert [action.id for action in wechat.available_actions] == [
        "check_wechat_connection",
        "connect_wechat",
    ]


def test_setup_wizard_action_metadata_is_gated():
    assert {
        step.id: [
            (
                action.id,
                action.label,
                action.step_id,
                action.kind,
                action.destructive,
                action.external_side_effect,
            )
            for action in step.actions
        ]
        for step in SETUP_WIZARD_STEPS
    } == {
        "preflight": [
            ("check_preflight", "Check", "preflight", "check", False, False),
        ],
        "cli_components": [
            ("check_cli_components", "Check", "cli_components", "check", False, False),
            (
                "setup_cli_components",
                "Fix automatically",
                "cli_components",
                "run",
                False,
                False,
            ),
        ],
        "dingtalk_cli": [
            ("check_dingtalk_cli", "Check", "dingtalk_cli", "check", False, False),
            (
                "setup_dingtalk_cli",
                "Install or configure",
                "dingtalk_cli",
                "run",
                False,
                True,
            ),
        ],
        "lark_cli": [
            ("check_lark_cli", "Check", "lark_cli", "check", False, False),
            (
                "setup_lark_cli",
                "Install or configure",
                "lark_cli",
                "run",
                False,
                True,
            ),
        ],
        "mcp": [
            ("check_mcp", "Check", "mcp", "check", False, False),
            ("setup_mcp", "Fix automatically", "mcp", "run", False, False),
        ],
        "service_config": [
            ("check_service_config", "Check", "service_config", "check", False, False),
            (
                "setup_service_config",
                "Fix automatically",
                "service_config",
                "run",
                False,
                False,
            ),
        ],
        "wechat_connection": [
            ("check_wechat_connection", "Check", "wechat_connection", "check", False, False),
            ("connect_wechat", "Connect WeChat", "wechat_connection", "run", False, False),
        ],
        "data_corpus": [
            ("check_data_corpus", "Check", "data_corpus", "check", False, False),
            ("build_data_corpus", "Run", "data_corpus", "run", False, False),
        ],
        "work_profile": [
            ("check_work_profile", "Check", "work_profile", "check", False, False),
            ("build_work_profile", "Run", "work_profile", "run", False, False),
        ],
        "dry_run": [
            ("check_dry_run", "Check", "dry_run", "check", False, False),
            ("run_dry_run", "Run", "dry_run", "run", False, False),
        ],
        "launchd": [
            ("check_launchd", "Check", "launchd", "check", False, False),
            ("install_launchd", "Run", "launchd", "run", False, True),
        ],
        "live_send": [
            ("check_live_send", "Check", "live_send", "check", False, False),
            ("verify_live_send", "Run", "live_send", "run", False, True),
            (
                "confirm_live_send",
                "Confirm after page inspection",
                "live_send",
                "confirm",
                False,
                False,
            ),
        ],
    }


def test_get_step_definition_rejects_unknown_step():
    with pytest.raises(KeyError) as error:
        get_step_definition("unknown")

    assert error.value.args == ("unknown",)


def test_setup_wizard_static_definitions_are_immutable():
    preflight = get_step_definition("preflight")

    with pytest.raises(AttributeError):
        preflight.actions.append(
            SetupAction(
                id="mutate",
                label="Mutate",
                step_id="preflight",
                kind="run",
            )
        )
    with pytest.raises(ValidationError):
        preflight.actions[0].label = "Mutated"


def test_setup_step_status_defaults_to_not_started():
    status = SetupStepStatus(step_id="mcp", title="MCP")

    assert status.status == "not_started"
    assert status.summary == ""
    assert status.available_actions == []
    assert status.manual_confirmation_allowed is False


def test_setup_wizard_status_serializes_steps():
    status = SetupWizardStatus(
        steps=[
            SetupStepStatus(
                step_id="preflight",
                title="Preflight",
                status="done",
                summary="Python is available",
            )
        ]
    )

    payload = status.model_dump()

    assert payload["steps"][0]["step_id"] == "preflight"
    assert payload["steps"][0]["status"] == "done"
    assert payload["steps"][0]["summary"] == "Python is available"


def test_setup_wizard_event_defaults_and_serialization():
    event = SetupWizardEvent(
        step_id="mcp",
        action_id="setup_mcp",
        status="done",
        evidence={"configured": True},
    )

    payload = event.model_dump()

    assert payload["id"] == 0
    assert payload["step_id"] == "mcp"
    assert payload["action_id"] == "setup_mcp"
    assert payload["status"] == "done"
    assert payload["summary"] == ""
    assert payload["evidence"] == {"configured": True}
    assert payload["stdout_excerpt"] == ""
    assert payload["stderr_excerpt"] == ""


def test_setup_action_status_values_are_locked():
    assert get_args(SetupActionStatus) == ("not_started", "running", "done", "failed")


@pytest.mark.parametrize("status", ["not_started", "running", "done", "failed"])
def test_setup_wizard_event_accepts_action_statuses(status: str):
    event = SetupWizardEvent(step_id="mcp", action_id="setup_mcp", status=status)

    assert event.status == status


def test_setup_wizard_event_rejects_unknown_action_status():
    with pytest.raises(ValidationError):
        SetupWizardEvent(step_id="mcp", action_id="setup_mcp", status="skipped")


def test_build_wizard_status_blocks_dependent_steps(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    status = build_wizard_status(store)
    steps = {step.step_id: step for step in status.steps}

    assert steps["preflight"].status == "not_started"
    assert steps["mcp"].status == "blocked"
    assert steps["mcp"].summary == "Blocked until CLI Components is complete."
    assert steps["mcp"].available_actions == []


def test_build_wizard_status_allows_next_step_after_dependency_done(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.upsert_setup_wizard_step(
        step_id="preflight",
        status="done",
        summary="ok",
    )

    status = build_wizard_status(store)
    steps = {step.step_id: step for step in status.steps}

    assert steps["cli_components"].status == "not_started"
    assert [action.id for action in steps["cli_components"].available_actions] == [
        "check_cli_components",
        "setup_cli_components",
    ]


def test_build_wizard_status_allows_live_send_manual_confirmation(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.upsert_setup_wizard_step(
        step_id="dry_run",
        status="done",
        summary="ok",
    )

    status = build_wizard_status(store)
    steps = {step.step_id: step for step in status.steps}

    assert steps["live_send"].manual_confirmation_allowed is True
    assert [action.id for action in steps["live_send"].available_actions] == [
        "check_live_send",
        "verify_live_send",
        "confirm_live_send",
    ]


def test_build_wizard_status_handles_unknown_persisted_status(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.upsert_setup_wizard_step(
        step_id="preflight",
        status="stale",
        summary="old state",
    )

    status = build_wizard_status(store)
    steps = {step.step_id: step for step in status.steps}

    assert steps["preflight"].status == "failed"
    assert steps["preflight"].summary == "Invalid persisted status: stale"
    assert steps["cli_components"].status == "blocked"


def test_redact_setup_output_removes_secrets_and_session_ids():
    text = (
        "Authorization: Bearer abc.def token=secret123 "
        "session_id=019eb3e7-dfc2 path=/Users/derek/Documents/private.md"
    )

    redacted = redact_setup_output(text)

    assert "abc.def" not in redacted
    assert "secret123" not in redacted
    assert "019eb3e7-dfc2" not in redacted
    assert "/Users/derek/Documents/private.md" not in redacted
    assert "[REDACTED_BEARER]" in redacted
    assert "[REDACTED_TOKEN]" in redacted
    assert "[REDACTED_SESSION]" in redacted
    assert "[REDACTED_PATH]" in redacted


def test_redact_setup_output_removes_common_secret_shapes_and_tmp_paths():
    text = (
        'api_key: sk-abc secret: nope token: abc "token": "json-secret" '
        "apiKey=camel /tmp/config.toml /private/tmp/agent.log"
    )

    redacted = redact_setup_output(text)

    assert "sk-abc" not in redacted
    assert "nope" not in redacted
    assert "abc" not in redacted
    assert "json-secret" not in redacted
    assert "camel" not in redacted
    assert "/tmp/config.toml" not in redacted
    assert "/private/tmp/agent.log" not in redacted
    assert redacted.count("[REDACTED_TOKEN]") == 5
    assert redacted.count("[REDACTED_PATH]") == 2


def test_check_service_config_detects_missing_env(tmp_path: Path):
    result = check_service_config(repo_root=tmp_path)

    assert result.status == "needs_action"
    assert result.summary == ".env is missing."
    assert result.evidence["env_exists"] is False


def test_runtime_route_setup_statuses_are_secret_safe():
    from app.agent_runtime_contracts import RuntimeCapabilitySnapshot

    snapshot = RuntimeCapabilitySnapshot(
        route_name="codex_oauth",
        capabilities=frozenset({"structured_output"}),
        healthy=True,
        checked_at="2026-08-21T10:00:00+00:00",
        expires_at="2026-08-21T10:05:00+00:00",
    )
    statuses = runtime_route_setup_statuses(
        env={
            "CEO_AGENT_RUNTIME_ROUTES": "codex_oauth,codex_api",
            "CEO_CODEX_API_KEY": "top-secret-value",
        },
        snapshots={"codex_oauth": snapshot},
    )

    assert statuses == (
        {
            "route_name": "codex_oauth",
            "status": "ready",
            "secret_configured": False,
        },
        {
            "route_name": "codex_api",
            "status": "probe_failed",
            "secret_configured": True,
        },
    )
    assert "top-secret-value" not in json.dumps(statuses)


def test_runtime_route_setup_statuses_distinguish_disabled_and_missing_secret():
    statuses = runtime_route_setup_statuses(
        env={"CEO_AGENT_RUNTIME_ROUTES": "codex_api"},
        snapshots={},
    )

    assert statuses == (
        {
            "route_name": "codex_oauth",
            "status": "disabled",
            "secret_configured": False,
        },
        {
            "route_name": "codex_api",
            "status": "missing_secret",
            "secret_configured": False,
        },
    )


def test_setup_service_config_accepts_runtime_secret_without_rendering_it(tmp_path: Path):
    secret = "setup-only-secret-value"

    event = run_setup_action(
        "setup_service_config",
        repo_root=tmp_path,
        env={
            "CEO_AGENT_RUNTIME_ROUTES": "codex_oauth,codex_api",
            "CEO_CODEX_API_KEY": secret,
        },
    )

    assert event.status == "done"
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "CEO_AGENT_RUNTIME_ROUTES=codex_oauth,codex_api" in env_text
    assert f"CEO_CODEX_API_KEY={secret}" in env_text
    assert secret not in event.model_dump_json()
    assert json.loads(event.evidence["runtime_routes_json"]) == [
        {
            "route_name": "codex_oauth",
            "status": "probe_failed",
            "secret_configured": False,
        },
        {
            "route_name": "codex_api",
            "status": "probe_failed",
            "secret_configured": True,
        },
    ]


def test_check_setup_step_dispatches_real_service_config_checker(tmp_path: Path):
    result = check_setup_step("service_config", repo_root=tmp_path)

    assert result.step_id == "service_config"
    assert result.status == "needs_action"
    assert result.summary == ".env is missing."


def test_check_dry_run_passes_without_failed_or_processing_backlog(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    result = check_setup_step("dry_run", repo_root=tmp_path, store=store)

    assert result.status == "done"
    assert result.summary == "Dry-run audit state has no unresolved backlog."
    assert result.evidence == {
        "processing_reply_tasks": 0,
        "failed_reply_tasks": 0,
        "recoverable_blocked_attempts": 0,
        "due_follow_up_drafts": 0,
    }


def test_check_dry_run_reports_processing_backlog(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="测试群",
        single_chat=False,
        trigger_message_id="msg-1",
        trigger_create_time="2026-07-21 12:00:00",
        trigger_sender="测试用户",
        trigger_text="测试消息",
    )
    store.claim_reply_tasks(limit=1)

    result = check_setup_step("dry_run", repo_root=tmp_path, store=store)

    assert result.status == "needs_action"
    assert result.summary == "Unresolved reply, action, or follow-up backlog exists."
    assert result.evidence["processing_reply_tasks"] == 1


def test_check_dry_run_reports_recoverable_blocked_attempt(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="测试群",
        trigger_message_id="msg-1",
        trigger_sender="测试用户",
        trigger_text="测试消息",
        action="blocked",
        sensitivity_kind="general",
        send_status="blocked",
    )

    result = check_setup_step("dry_run", repo_root=tmp_path, store=store)

    assert result.status == "needs_action"
    assert result.evidence["recoverable_blocked_attempts"] == 1


def test_check_dry_run_reports_explained_blocked_attempt(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="测试群",
        trigger_message_id="msg-1",
        trigger_sender="测试用户",
        trigger_text="测试消息",
        action="blocked",
        sensitivity_kind="general",
        send_status="blocked",
    )
    attempt = store.get_latest_reply_attempt_for_trigger("cid-1", "msg-1")
    assert attempt is not None
    store.update_reply_attempt(
        attempt.id,
        send_error="blocked_unrecoverable_external_auth: not current user",
    )

    result = check_setup_step("dry_run", repo_root=tmp_path, store=store)

    assert result.status == "needs_action"
    assert result.evidence["recoverable_blocked_attempts"] == 1


def test_check_dry_run_reports_due_follow_up_backlog(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    project_id = store.create_work_project(
        title="宝马项目周末攻坚与客户Demo推进",
        category="sales",
        status="active",
        priority="P0",
        risk_level="high",
    )
    store.create_follow_up_draft(
        project_id=project_id,
        owner_name="Claire Huang",
        target_kind="direct",
        question_text="准备宝马专家邀请材料了吗？",
        scheduled_at="2000-01-01 01:00:00",
        status="draft",
    )

    result = check_setup_step("dry_run", repo_root=tmp_path, store=store)

    assert result.status == "needs_action"
    assert result.summary == "Unresolved reply, action, or follow-up backlog exists."
    assert result.evidence["due_follow_up_drafts"] == 1


def test_check_service_config_accepts_env_and_directories(tmp_path: Path):
    (tmp_path / ".env").write_text(
        "CEO_WORKSPACE=workspace\n"
        "CEO_WORKER_DB=data/auto-reply.sqlite3\n"
        "CEO_CORPUS_DIR=data/corpus\n"
        "CEO_SERVICE_MCP_CONFIG_PATH=data/config/service-mcp.json\n"
        "CEO_NOT_SEND_MESSAGE=1\n",
        encoding="utf-8",
    )
    (tmp_path / "workspace").mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "corpus").mkdir()
    (tmp_path / "data" / "config").mkdir()
    (tmp_path / "data" / "config" / "service-mcp.json").write_text(
        '{"servers": {}}',
        encoding="utf-8",
    )

    result = check_service_config(repo_root=tmp_path, runtime_snapshots={})

    assert result.status == "done"
    assert result.summary == "Service config and runtime directories are ready."
    assert result.evidence["dry_run_enabled"] is True
    assert json.loads(result.evidence["runtime_routes_json"]) == [
        {
            "route_name": "codex_oauth",
            "status": "probe_failed",
            "secret_configured": False,
        },
        {
            "route_name": "codex_api",
            "status": "disabled",
            "secret_configured": False,
        },
    ]


def test_check_service_config_expands_home_environment_value(
    monkeypatch,
    tmp_path: Path,
):
    home = tmp_path / "home"
    workspace = home / "Documents" / "memory"
    workspace.mkdir(parents=True)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "corpus").mkdir()
    (tmp_path / ".env").write_text(
        "CEO_WORKSPACE=$HOME/Documents/memory\n"
        "CEO_WORKER_DB=data/auto-reply.sqlite3\n"
        "CEO_CORPUS_DIR=data/corpus\n"
        "CEO_SERVICE_MCP_CONFIG_PATH=data/config/service-mcp.json\n"
        "CEO_NOT_SEND_MESSAGE=1\n",
        encoding="utf-8",
    )
    (tmp_path / "data" / "config").mkdir()
    (tmp_path / "data" / "config" / "service-mcp.json").write_text(
        '{"servers": {}}',
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))

    result = check_service_config(repo_root=tmp_path)

    assert result.status == "done"


def test_check_data_corpus_requires_style_corpus(tmp_path: Path):
    result = check_data_corpus(repo_root=tmp_path)

    assert result.status == "needs_action"
    assert result.summary == "data/corpus/style_corpus.csv is missing."


def test_check_data_corpus_uses_configured_corpus_dir(tmp_path: Path):
    external_corpus = tmp_path / "external-corpus"
    external_corpus.mkdir()
    (external_corpus / "style_corpus.csv").write_text("source,text\n", encoding="utf-8")
    (tmp_path / ".env").write_text(
        f"CEO_CORPUS_DIR={external_corpus}\n",
        encoding="utf-8",
    )

    result = check_data_corpus(repo_root=tmp_path)

    assert result.status == "done"


def test_check_work_profile_requires_profile_and_evidence(tmp_path: Path):
    result = check_work_profile(repo_root=tmp_path)

    assert result.status == "needs_action"
    assert result.summary == "data/work-profile/work_profile.md is missing."


def test_check_work_profile_flags_leaked_local_path(tmp_path: Path):
    (tmp_path / "data" / "work-profile").mkdir(parents=True)
    (tmp_path / "data" / "profile-evidence").mkdir(parents=True)
    (tmp_path / "data" / "corpus").mkdir(parents=True)
    (tmp_path / "data" / "work-profile" / "work_profile.md").write_text(
        "Evidence from /Users/derek/Documents/private.md",
        encoding="utf-8",
    )
    (tmp_path / "data" / "profile-evidence" / "evidence_index.jsonl").write_text(
        "{}\n",
        encoding="utf-8",
    )
    (tmp_path / "data" / "corpus" / "style_corpus.csv").write_text(
        "source,text\n",
        encoding="utf-8",
    )

    result = check_work_profile(repo_root=tmp_path)

    assert result.status == "failed"
    assert result.summary == "data/work-profile/work_profile.md contains sensitive local evidence."


def test_check_work_profile_uses_configured_corpus_dir_and_redaction_patterns(
    tmp_path: Path,
):
    external_corpus = tmp_path / "external-corpus"
    external_corpus.mkdir()
    (external_corpus / "style_corpus.csv").write_text("source,text\n", encoding="utf-8")
    (tmp_path / ".env").write_text(
        f"CEO_CORPUS_DIR={external_corpus}\n",
        encoding="utf-8",
    )
    (tmp_path / "data" / "work-profile").mkdir(parents=True)
    (tmp_path / "data" / "profile-evidence").mkdir(parents=True)
    (tmp_path / "data" / "work-profile" / "work_profile.md").write_text(
        "api_key: sk-secret /tmp/private-cache "
        "019eb3e7-dfc2-7fd2-8deb-81f76fcfcdf1",
        encoding="utf-8",
    )
    (tmp_path / "data" / "profile-evidence" / "evidence_index.jsonl").write_text(
        "{}\n",
        encoding="utf-8",
    )

    result = check_work_profile(repo_root=tmp_path)

    assert result.status == "failed"
    assert result.summary == "data/work-profile/work_profile.md contains sensitive local evidence."


def test_run_setup_service_config_creates_env_and_directories(tmp_path: Path):
    (tmp_path / ".env.example").write_text(
        "CEO_WORKSPACE=\nCEO_WORKER_DB=\nCEO_CORPUS_DIR=\nCEO_NOT_SEND_MESSAGE=\n",
        encoding="utf-8",
    )
    event = run_setup_action(
        "setup_service_config",
        repo_root=tmp_path,
        env={
            "CEO_WORKSPACE": "workspace",
            "CEO_WORKER_DB": "data/auto-reply.sqlite3",
            "CEO_CORPUS_DIR": "data/corpus",
            "CEO_NOT_SEND_MESSAGE": "1",
        },
    )

    assert event.status == "done"
    assert (tmp_path / ".env").exists()
    assert (tmp_path / "workspace").is_dir()
    assert (tmp_path / "data").is_dir()
    assert (tmp_path / "data" / "corpus").is_dir()
    assert (tmp_path / "data" / "prompts" / "developer_prompt.md").exists()
    assert (tmp_path / "data" / "prompts" / "user_prompt.md").exists()
    assert (tmp_path / "data" / "prompts" / "audit_rules.md").exists()
    assert (tmp_path / "data" / "work-profile" / "work_profile.md").exists()
    assert "CEO_DEVELOPER_PROMPT_TEMPLATE_PATH=data/prompts/developer_prompt.md" in (
        tmp_path / ".env"
    ).read_text(encoding="utf-8")
    assert "CEO_USER_PROMPT_TEMPLATE_PATH=data/prompts/user_prompt.md" in (
        tmp_path / ".env"
    ).read_text(encoding="utf-8")
    assert "CEO_AUDIT_RULES_TEMPLATE_PATH=data/prompts/audit_rules.md" in (
        tmp_path / ".env"
    ).read_text(encoding="utf-8")
    assert "CEO_WORK_PROFILE_PATH=data/work-profile/work_profile.md" in (
        tmp_path / ".env"
    ).read_text(encoding="utf-8")
    assert "CEO_NOT_SEND_MESSAGE=1" in (tmp_path / ".env").read_text(
        encoding="utf-8"
    )
    assert "CEO_SERVICE_MCP_CONFIG_PATH=data/config/service-mcp.json" in (
        tmp_path / ".env"
    ).read_text(encoding="utf-8")
    service_manifest = tmp_path / "data" / "config" / "service-mcp.json"
    assert service_manifest.is_file()
    payload = json.loads(service_manifest.read_text(encoding="utf-8"))
    assert payload == {
        "servers": {"exa": {"url": "https://mcp.exa.ai/mcp"}}
    }
    assert [server.name for server in load_service_mcp_servers(service_manifest, env={})] == [
        "exa"
    ]


def test_run_setup_service_config_defaults_database_to_application_support(
    monkeypatch,
    tmp_path: Path,
):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    (tmp_path / ".env.example").write_text(
        "CEO_WORKSPACE=\nCEO_WORKER_DB=\nCEO_CORPUS_DIR=\nCEO_NOT_SEND_MESSAGE=\n",
        encoding="utf-8",
    )

    event = run_setup_action("setup_service_config", repo_root=tmp_path, env={})

    expected = home / "Library" / "Application Support" / "ceo-agent-service"
    assert event.status == "done"
    assert expected.is_dir()
    assert (
        "CEO_WORKER_DB=$HOME/Library/Application Support/ceo-agent-service/auto-reply.sqlite3"
        in (tmp_path / ".env").read_text(encoding="utf-8")
    )


def test_run_setup_service_config_expands_example_environment_values(
    monkeypatch,
    tmp_path: Path,
):
    home = tmp_path / "home"
    (tmp_path / ".env.example").write_text(
        "CEO_WORKSPACE=$HOME/Documents/memory\n"
        "CEO_WORKER_DB=data/auto-reply.sqlite3\n"
        "CEO_CORPUS_DIR=data/corpus\n"
        "CEO_NOT_SEND_MESSAGE=1\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))

    event = run_setup_action("setup_service_config", repo_root=tmp_path, env={})
    check = check_service_config(repo_root=tmp_path)

    assert event.status == "done"
    assert (home / "Documents" / "memory").is_dir()
    assert not (tmp_path / "$HOME").exists()
    assert check.status == "done"
    assert "[REDACTED_PATH]" in event.evidence["workspace"]
    assert str(home) not in event.evidence["workspace"]


def test_run_setup_mcp_writes_service_manifest_and_environment(tmp_path: Path):
    personal_config = tmp_path / "personal-config.toml"

    event = run_setup_action(
        "setup_mcp",
        repo_root=tmp_path,
        env={
            "MEMORY_CONNECTOR_URL": "https://memory.example/mcp/",
            "CODEX_CONFIG_PATH": str(personal_config),
            "CEO_SERVICE_MCP_CONFIG_PATH": "data/config/service-mcp.json",
        },
    )

    assert event.status == "done"
    assert not personal_config.exists()
    manifest = tmp_path / "data" / "config" / "service-mcp.json"
    assert json.loads(manifest.read_text(encoding="utf-8")) == {
        "servers": {"exa": {"url": "https://mcp.exa.ai/mcp"}}
    }
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "CEO_SERVICE_MCP_CONFIG_PATH=data/config/service-mcp.json" in env_text
    assert "MEMORY_CONNECTOR_URL=https://memory.example/mcp/" in env_text
    assert event.evidence["service_mcp_config"] == "[REDACTED_PATH]"


def test_run_setup_mcp_adds_only_fully_configured_optional_servers(tmp_path: Path):
    event = run_setup_action(
        "setup_mcp",
        repo_root=tmp_path,
        env={
            "MEMORY_CONNECTOR_URL": "https://memory.example/mcp/",
            "CONNECTOR_API_KEY": "service-token",
            "MEMORY_CONNECTOR_AUTH_TYPE": "mcp_access_token",
            "MEMORY_CONNECTOR_CONTENT_TYPE": "application/json",
            "CEO_XIAOQING_MCP_COMMAND": "/opt/service/xiaoqing-mcp",
            "CEO_XIAOQING_MCP_ARGS_JSON": "[]",
            "CEO_SERVICE_MCP_CONFIG_PATH": "data/config/service-mcp.json",
        },
    )

    assert event.status == "done"
    manifest = tmp_path / "data/config/service-mcp.json"
    assert [
        server.name
        for server in load_service_mcp_servers(
            manifest,
            env={
                "MEMORY_CONNECTOR_URL": "https://memory.example/mcp/",
                "CONNECTOR_API_KEY": "service-token",
                "MEMORY_CONNECTOR_AUTH_TYPE": "mcp_access_token",
                "MEMORY_CONNECTOR_CONTENT_TYPE": "application/json",
                "CEO_XIAOQING_MCP_COMMAND": "/opt/service/xiaoqing-mcp",
                "CEO_XIAOQING_MCP_ARGS_JSON": "[]",
            },
        )
    ] == ["exa", "memory_connector", "xiaoqing_interview"]


def test_run_setup_cli_components_runs_bootstrap_script(monkeypatch, tmp_path: Path):
    script = tmp_path / "scripts" / "bootstrap-local-components.sh"
    script.parent.mkdir()
    script.write_text("#!/usr/bin/env bash\n", encoding="utf-8")

    def fake_run(args, **kwargs):
        assert args == [str(script), "--format", "json"]
        assert kwargs["cwd"] == tmp_path
        assert kwargs["env"]["DWS_INSTALL_COMMAND"] == "install dws"
        return subprocess.CompletedProcess(
            args,
            0,
            stdout=(
                '{"summary":"installed token=secret",'
                '"components":[{"name":"dws","status":"done","detail":"ok"}]}'
            ),
            stderr="",
        )

    monkeypatch.setattr("app.setup_wizard.subprocess.run", fake_run)

    event = run_setup_action(
        "setup_cli_components",
        repo_root=tmp_path,
        env={"DWS_INSTALL_COMMAND": "install dws"},
    )

    assert event.status == "done"
    assert event.summary == "installed token=[REDACTED_TOKEN]"
    assert event.evidence["returncode"] == 0
    assert event.evidence["components_json"] == (
        '[{"detail": "ok", "name": "dws", "status": "done"}]'
    )
    assert "secret" not in event.stdout_excerpt


def test_run_setup_cli_components_records_bootstrap_failure(
    monkeypatch,
    tmp_path: Path,
):
    script = tmp_path / "scripts" / "bootstrap-local-components.sh"
    script.parent.mkdir()
    script.write_text("#!/usr/bin/env bash\n", encoding="utf-8")

    def fake_run(args, **kwargs):
        del kwargs
        return subprocess.CompletedProcess(
            args,
            1,
            stdout='{"summary":"missing dws","components":[]}',
            stderr="cannot install /tmp/dws.pkg",
        )

    monkeypatch.setattr("app.setup_wizard.subprocess.run", fake_run)

    event = run_setup_action("setup_cli_components", repo_root=tmp_path, env={})

    assert event.status == "failed"
    assert event.summary == "missing dws"
    assert event.evidence["returncode"] == 1
    assert event.stderr_excerpt == "cannot install [REDACTED_PATH]"


def test_setup_cli_components_propagates_business_skill_install_conflict(
    monkeypatch,
    tmp_path: Path,
):
    script = tmp_path / "scripts" / "bootstrap-local-components.sh"
    script.parent.mkdir()
    script.write_text("#!/usr/bin/env bash\n", encoding="utf-8")

    def fake_run(args, **kwargs):
        del kwargs
        return subprocess.CompletedProcess(
            args,
            1,
            stdout=(
                '{"status":"failed","summary":"Business Skill installation failed.",'
                '"components":[{"name":"ceo-business-skills","status":"failed",'
                '"detail":"refusing to overwrite user-owned Skill: '
                'ceo-calendar-invite"}]}'
            ),
            stderr="",
        )

    monkeypatch.setattr("app.setup_wizard.subprocess.run", fake_run)

    event = run_setup_action("setup_cli_components", repo_root=tmp_path, env={})

    assert event.status == "failed"
    assert event.summary == "Business Skill installation failed."
    assert "ceo-business-skills" in event.evidence["components_json"]
    assert "ceo-calendar-invite" in event.evidence["components_json"]


def test_check_dingtalk_cli_reports_missing_binary(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(
        "app.setup_wizard.shutil.which",
        lambda name: None if name == "dws" else f"/usr/bin/{name}",
    )

    status = check_setup_step("dingtalk_cli", repo_root=tmp_path)

    assert status.status == "needs_action"
    assert status.evidence["installed"] is False


def test_setup_lark_cli_installs_then_launches_configuration(
    monkeypatch,
    tmp_path: Path,
):
    installed = False

    def fake_which(name):
        if name == "lark-cli":
            return "/usr/local/bin/lark-cli" if installed else None
        if name == "npm":
            return "/usr/local/bin/npm"
        return f"/usr/bin/{name}"

    def fake_run(args, **kwargs):
        nonlocal installed
        assert args == [
            "/usr/local/bin/npm",
            "install",
            "-g",
            "@larksuite/cli",
        ]
        installed = True
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    class FakeGate:
        def check(self):
            return ChannelGateResult(
                channel="lark",
                state=ChannelGateState.NEEDS_LOGIN,
                reason_code="status_auth_invalid",
            )

    launched = []

    class FakeProcess:
        pid = 123

    monkeypatch.setattr("app.setup_wizard.shutil.which", fake_which)
    monkeypatch.setattr("app.setup_wizard.subprocess.run", fake_run)
    monkeypatch.setattr(
        "app.setup_wizard.default_channel_gates",
        lambda **kwargs: {"lark": FakeGate()},
    )
    monkeypatch.setattr(
        "app.setup_wizard.subprocess.Popen",
        lambda args, **kwargs: launched.append((args, kwargs)) or FakeProcess(),
    )

    event = run_setup_action("setup_lark_cli", repo_root=tmp_path, env={})

    assert event.status == "done"
    assert event.next_step_status == "needs_action"
    assert launched[0][0] == ["lark-cli", "auth", "login"]
    assert event.evidence["login_started"] is True


def test_setup_dingtalk_cli_uses_configured_installer_and_finishes_when_ready(
    monkeypatch,
    tmp_path: Path,
):
    installed = False

    def fake_which(name):
        if name == "dws":
            return "/usr/local/bin/dws" if installed else None
        return f"/usr/bin/{name}"

    def fake_run(args, **kwargs):
        nonlocal installed
        assert args == ["/bin/zsh", "-lc", "install-company-dws"]
        installed = True
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    class FakeGate:
        def check(self):
            return ChannelGateResult(
                channel="dingtalk",
                state=ChannelGateState.READY,
                reason_code="ready",
            )

    monkeypatch.setattr("app.setup_wizard.shutil.which", fake_which)
    monkeypatch.setattr("app.setup_wizard.subprocess.run", fake_run)
    monkeypatch.setattr(
        "app.setup_wizard.default_channel_gates",
        lambda **kwargs: {"dingtalk": FakeGate()},
    )

    event = run_setup_action(
        "setup_dingtalk_cli",
        repo_root=tmp_path,
        env={"DWS_INSTALL_COMMAND": "install-company-dws"},
    )

    assert event.status == "done"
    assert event.next_step_status == "done"
    assert event.evidence["installed"] is True
    assert event.evidence["channel_state"] == "ready"


def test_run_setup_mcp_uses_os_service_path_and_redacts_output(
    monkeypatch,
    tmp_path: Path,
):
    service_config = tmp_path / "local" / "service-mcp.json"
    monkeypatch.setenv("MEMORY_CONNECTOR_URL", "https://memory.example/mcp/")
    monkeypatch.setenv("CEO_SERVICE_MCP_CONFIG_PATH", str(service_config))

    event = run_setup_action("setup_mcp", repo_root=tmp_path, env={})

    assert event.status == "done"
    assert json.loads(service_config.read_text(encoding="utf-8")) == {
        "servers": {"exa": {"url": "https://mcp.exa.ai/mcp"}}
    }
    assert event.evidence["service_mcp_config"] == "[REDACTED_PATH]"
    assert str(tmp_path) not in json.dumps(event.evidence)


def test_run_setup_mcp_does_not_copy_personal_literal_transports(
    monkeypatch,
    tmp_path: Path,
):
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    codex_config = codex_home / "config.toml"
    codex_config.write_text(
        "[mcp_servers.memory_connector]\n"
        'url = "https://personal.example/mcp/literal-secret"\n'
        'http_headers = { Authorization = "Bearer literal-secret" }\n'
        "[mcp_servers.xiaoqing_interview]\n"
        'command = "/personal/bin/xiaoqing"\n'
        'args = ["--token", "literal-secret"]\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.delenv("MEMORY_CONNECTOR_URL", raising=False)

    event = run_setup_action(
        "setup_mcp",
        repo_root=tmp_path,
        env={"CEO_SERVICE_MCP_CONFIG_PATH": "data/config/service-mcp.json"},
    )

    assert event.status == "done"
    assert event.summary == "Service MCP manifest was configured and validated."
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    manifest_text = (tmp_path / "data/config/service-mcp.json").read_text(
        encoding="utf-8"
    )
    for literal in ("personal.example", "/personal/bin/xiaoqing", "literal-secret"):
        assert literal not in env_text
        assert literal not in manifest_text


def test_run_setup_mcp_uses_persisted_service_url_without_personal_config(
    monkeypatch,
    tmp_path: Path,
):
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    codex_config = codex_home / "config.toml"
    codex_config.write_text(
        '[mcp_servers.memory_connector]\nurl = "https://personal.example/mcp/"\n',
        encoding="utf-8",
    )
    service_config = tmp_path / "data" / "config" / "service-mcp.json"
    (tmp_path / ".env").write_text(
        "CEO_SERVICE_MCP_CONFIG_PATH=data/config/service-mcp.json\n"
        "MEMORY_CONNECTOR_URL=https://service.example/mcp/\n",
        encoding="utf-8",
    )
    action_env = {
        "CODEX_HOME": str(codex_home),
        "CEO_SERVICE_MCP_CONFIG_PATH": str(service_config),
    }
    monkeypatch.delenv("MEMORY_CONNECTOR_URL", raising=False)

    event = run_setup_action("setup_mcp", repo_root=tmp_path, env=action_env)

    assert event.status == "done"
    assert event.evidence["memory_url_source"] == "service_env_file"
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "MEMORY_CONNECTOR_URL=https://service.example/mcp/" in env_text
    assert "personal.example" not in env_text


def test_run_setup_mcp_blank_submissions_preserve_persisted_optional_servers(
    monkeypatch,
    tmp_path: Path,
):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "empty-codex-home"))
    (tmp_path / ".env").write_text(
        "CEO_SERVICE_MCP_CONFIG_PATH=data/config/service-mcp.json\n"
        "MEMORY_CONNECTOR_URL=https://service.example/mcp/\n"
        "CONNECTOR_API_KEY=service-token\n"
        "MEMORY_CONNECTOR_AUTH_TYPE=mcp_access_token\n"
        "MEMORY_CONNECTOR_CONTENT_TYPE=application/json\n"
        "CEO_XIAOQING_MCP_COMMAND=/service/bin/xiaoqing\n"
        'CEO_XIAOQING_MCP_ARGS_JSON=["serve"]\n',
        encoding="utf-8",
    )

    event = run_setup_action(
        "setup_mcp",
        repo_root=tmp_path,
        env={
            "MEMORY_CONNECTOR_URL": "   ",
            "CONNECTOR_API_KEY": " ",
            "MEMORY_CONNECTOR_AUTH_TYPE": "\t",
            "MEMORY_CONNECTOR_CONTENT_TYPE": "\n",
            "CEO_XIAOQING_MCP_COMMAND": "   ",
            "CEO_XIAOQING_MCP_ARGS_JSON": " ",
        },
    )

    assert event.status == "done"
    manifest = json.loads(
        (tmp_path / "data/config/service-mcp.json").read_text(encoding="utf-8")
    )
    assert set(manifest["servers"]) == {
        "exa",
        "memory_connector",
        "xiaoqing_interview",
    }
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "CONNECTOR_API_KEY=service-token" in env_text
    assert "CEO_XIAOQING_MCP_COMMAND=/service/bin/xiaoqing" in env_text


def test_run_setup_mcp_reports_missing_memory_url(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "empty-codex-home"))
    monkeypatch.setenv("CLAUDE_CONFIG_PATH", str(tmp_path / "claude.json"))
    missing = run_setup_action(
        "setup_mcp",
        repo_root=tmp_path,
        env={
            "MEMORY_CONNECTOR_URL": "   ",
            "CEO_SERVICE_MCP_CONFIG_PATH": "data/config/service-mcp.json",
        },
    )

    assert missing.status == "done"
    manifest = tmp_path / "data/config/service-mcp.json"
    assert json.loads(manifest.read_text(encoding="utf-8")) == {
        "servers": {"exa": {"url": "https://mcp.exa.ai/mcp"}}
    }


def test_run_setup_mcp_does_not_import_secret_bearing_personal_url(
    monkeypatch,
    tmp_path: Path,
):
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    secret = "must-not-appear"
    (codex_home / "config.toml").write_text(
        "[mcp_servers.memory_connector]\n"
        f'url = "https://{secret}.memory.example/mcp/{secret}"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.delenv("MEMORY_CONNECTOR_URL", raising=False)

    event = run_setup_action("setup_mcp", repo_root=tmp_path, env={})

    assert event.status == "done"
    assert event.summary == "Service MCP manifest was configured and validated."
    assert secret not in event.summary
    assert secret not in (tmp_path / ".env").read_text(encoding="utf-8")


def test_setup_mcp_gate_reports_present_xiaoqing_without_command(
    tmp_path: Path,
    monkeypatch,
):
    manifest = tmp_path / "data" / "config" / "service-mcp.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps(
            {
                "servers": {
                    "xiaoqing_interview": {
                        "command_env": "CEO_XIAOQING_MCP_COMMAND",
                        "args_env": "CEO_XIAOQING_MCP_ARGS_JSON",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text(
        "CEO_SERVICE_MCP_CONFIG_PATH=data/config/service-mcp.json\n"
        "CEO_XIAOQING_MCP_ARGS_JSON=[]\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("CEO_XIAOQING_MCP_COMMAND", raising=False)

    status = check_setup_step("mcp", repo_root=tmp_path)

    assert status.status == "needs_action"
    assert status.summary == "service transport command is not configured"


def test_run_setup_action_dispatches_wechat_connect(monkeypatch, tmp_path: Path):
    from app.wechat.setup import WechatSetupResult

    class _FakeSetup:
        def connect(self, selected_account_id: str = ""):
            return WechatSetupResult(
                action_id="connect_wechat",
                status="done",
                next_step_status="blocked",
                summary="key unavailable",
                evidence={"database_status": "blocked"},
            )

    monkeypatch.setattr(
        "app.wechat.service.build_setup_service", lambda store: _FakeSetup()
    )
    monkeypatch.setenv("CEO_WORKER_DB", str(tmp_path / "worker.sqlite3"))

    event = run_setup_action("connect_wechat", repo_root=tmp_path, env={})

    assert event.step_id == "wechat_connection"
    assert event.action_id == "connect_wechat"
    assert event.status == "done"
    assert event.next_step_status == "blocked"  # blocked reader -> step stays blocked
    assert event.summary == "key unavailable"


def test_run_setup_action_executes_dry_run_without_sending(
    monkeypatch,
    tmp_path: Path,
):
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(
            args,
            0,
            stdout='{"counts":{"reply_attempts":0,"sent_replies":0,"errors":0}}\n',
            stderr="",
        )

    monkeypatch.setattr("app.setup_wizard.subprocess.run", fake_run)

    event = run_setup_action(
        "run_dry_run",
        repo_root=tmp_path,
        env={
            "CEO_NOT_SEND_MESSAGE": "0",
            "CEO_PYTHON": "/central/conda/bin/python",
        },
    )

    args, kwargs = calls[0]
    assert args == [
        "/central/conda/bin/python",
        "-m",
        "app.cli",
        "run-once",
        "--not-send-message",
    ]
    assert kwargs["cwd"] == tmp_path
    assert kwargs["env"]["CEO_NOT_SEND_MESSAGE"] == "1"
    assert kwargs["timeout"] == 900
    assert event.status == "done"
    assert event.summary == "Dry-run validation completed."
    assert event.evidence["returncode"] == 0


def test_run_setup_action_redacts_dry_run_failure_output(
    monkeypatch,
    tmp_path: Path,
):
    def fake_run(args, **kwargs):
        del kwargs
        return subprocess.CompletedProcess(
            args,
            1,
            stdout="",
            stderr="token=secret path=/Users/derek/private.md",
        )

    monkeypatch.setattr("app.setup_wizard.subprocess.run", fake_run)

    event = run_setup_action("run_dry_run", repo_root=tmp_path, env={})

    assert event.status == "failed"
    assert event.summary == "Dry-run validation failed with exit code 1."
    assert "secret" not in event.stderr_excerpt
    assert "/Users/derek/private.md" not in event.stderr_excerpt


def test_run_setup_action_starts_launchd_install_in_background(
    monkeypatch,
    tmp_path: Path,
):
    calls = []

    class FakeProcess:
        pid = 12345

    def fake_popen(args, **kwargs):
        calls.append((args, kwargs))
        return FakeProcess()

    monkeypatch.setattr("app.setup_wizard.subprocess.Popen", fake_popen)

    event = run_setup_action("install_launchd", repo_root=tmp_path, env={})

    args, kwargs = calls[0]
    assert args == [
        "/bin/zsh",
        "-lc",
        "sleep 1; exec scripts/install-auto-reply-agents.sh",
    ]
    assert kwargs["cwd"] == tmp_path
    assert kwargs["start_new_session"] is True
    assert event.step_id == "launchd"
    assert event.status == "done"
    assert event.summary == "Launchd service install started in background."
    assert event.evidence["pid"] == 12345
    assert event.evidence["log_path"] == "[REDACTED_PATH]"


def test_run_setup_action_rejects_unknown_action(tmp_path: Path):
    event = run_setup_action("unknown", repo_root=tmp_path, env={})

    assert event.status == "failed"
    assert event.step_id == "unknown"


def test_run_setup_action_keeps_known_unimplemented_action_on_own_step(
    tmp_path: Path,
):
    event = run_setup_action("build_data_corpus", repo_root=tmp_path, env={})

    assert event.status == "failed"
    assert event.step_id == "data_corpus"
    assert event.summary == "Run is not automated yet."
