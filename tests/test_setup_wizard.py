import subprocess
from pathlib import Path
from typing import get_args

import pytest
from pydantic import ValidationError

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
    # Memory Connector is optional for local channel setup, and WeChat can be
    # connected as soon as the local checkout passes preflight.
    assert get_step_definition("service_config").depends_on == ("cli_components",)
    assert get_step_definition("wechat_connection").depends_on == ("preflight",)
    assert get_step_definition("data_corpus").depends_on == ("service_config",)
    assert [a.id for a in get_step_definition("wechat_connection").actions] == [
        "check_wechat_connection",
        "connect_wechat",
        "verify_wechat",
    ]
    assert get_step_definition("launchd").depends_on == ("dry_run",)
    assert get_step_definition("live_send").depends_on == ("dry_run",)
    assert get_action_definition("setup_cli_components").step_id == "cli_components"
    assert get_action_definition("setup_mcp").step_id == "mcp"


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
        "verify_wechat",
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
            ("verify_wechat", "Save and verify", "wechat_connection", "run", False, False),
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


def test_check_setup_step_dispatches_real_service_config_checker(tmp_path: Path):
    result = check_setup_step("service_config", repo_root=tmp_path)

    assert result.step_id == "service_config"
    assert result.status == "needs_action"
    assert result.summary == ".env is missing."


def test_check_service_config_accepts_env_and_directories(tmp_path: Path):
    (tmp_path / ".env").write_text(
        "CEO_WORKSPACE=workspace\nCEO_WORKER_DB=data/auto-reply.sqlite3\nCEO_CORPUS_DIR=data/corpus\nCEO_NOT_SEND_MESSAGE=1\n",
        encoding="utf-8",
    )
    (tmp_path / "workspace").mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "corpus").mkdir()

    result = check_service_config(repo_root=tmp_path)

    assert result.status == "done"
    assert result.summary == "Service config and runtime directories are ready."
    assert result.evidence["dry_run_enabled"] is True


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
        "CEO_NOT_SEND_MESSAGE=1\n",
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
    assert (tmp_path / "data" / "work-profile" / "work_profile.md").exists()
    assert "CEO_DEVELOPER_PROMPT_TEMPLATE_PATH=data/prompts/developer_prompt.md" in (
        tmp_path / ".env"
    ).read_text(encoding="utf-8")
    assert "CEO_USER_PROMPT_TEMPLATE_PATH=data/prompts/user_prompt.md" in (
        tmp_path / ".env"
    ).read_text(encoding="utf-8")
    assert "CEO_WORK_PROFILE_PATH=data/work-profile/work_profile.md" in (
        tmp_path / ".env"
    ).read_text(encoding="utf-8")
    assert "CEO_NOT_SEND_MESSAGE=1" in (tmp_path / ".env").read_text(
        encoding="utf-8"
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


def test_run_setup_mcp_writes_codex_config(tmp_path: Path):
    codex_config = tmp_path / "config.toml"

    event = run_setup_action(
        "setup_mcp",
        repo_root=tmp_path,
        env={
            "MEMORY_CONNECTOR_URL": "https://memory.example/mcp/",
            "CODEX_CONFIG_PATH": str(codex_config),
            "CLAUDE_CONFIG_PATH": str(tmp_path / "claude.json"),
        },
    )

    assert event.status == "done"
    assert "memory_connector" in codex_config.read_text(encoding="utf-8")
    assert event.evidence["codex_config"] == "[REDACTED_PATH]"


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


def test_run_setup_mcp_uses_os_config_path_and_redacts_output(
    monkeypatch,
    tmp_path: Path,
):
    codex_config = tmp_path / "config.toml"
    monkeypatch.setenv("MEMORY_CONNECTOR_URL", "https://memory.example/mcp/")
    monkeypatch.setenv("CODEX_CONFIG_PATH", str(codex_config))
    monkeypatch.setenv("CLAUDE_CONFIG_PATH", str(tmp_path / "claude.json"))

    event = run_setup_action("setup_mcp", repo_root=tmp_path, env={})

    assert event.status == "done"
    assert "memory_connector" in codex_config.read_text(encoding="utf-8")
    assert event.evidence["codex_config"] == "[REDACTED_PATH]"
    assert str(tmp_path) not in event.stdout_excerpt
    assert "[REDACTED_PATH]" in event.stdout_excerpt


def test_run_setup_mcp_handles_missing_and_failed_setup(monkeypatch, tmp_path: Path):
    missing = run_setup_action(
        "setup_mcp",
        repo_root=tmp_path,
        env={"MEMORY_CONNECTOR_URL": "   "},
    )

    assert missing.status == "failed"
    assert missing.summary == "MEMORY_CONNECTOR_URL is missing."

    def fail_setup(**kwargs):
        del kwargs
        raise OSError("cannot write /tmp/config.toml")

    monkeypatch.setattr("app.setup_wizard.setup_memory_connector_command", fail_setup)
    failed = run_setup_action(
        "setup_mcp",
        repo_root=tmp_path,
        env={
            "MEMORY_CONNECTOR_URL": "https://memory.example/mcp/",
            "CODEX_CONFIG_PATH": str(tmp_path / "config.toml"),
        },
    )

    assert failed.status == "failed"
    assert "cannot write [REDACTED_PATH]" in failed.summary


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
