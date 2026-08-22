import json
from pathlib import Path

import pytest

import app.codex_runner as codex_runner_module
from app.codex_decision import CodexDecisionRunner
from app.codex_runner import (
    AGENT_ENVELOPE_SCHEMA_PATH,
    CODEX_DECISION_SCHEMA_PATH,
    CodexRunner,
    codex_developer_instructions,
    codex_model_config_options,
    memory_connector_config_issue,
)
from app.consumer_agent import CORE_DYNAMIC_SKILL_BODY
from app.dingtalk_models import CodexAction, CodexDecision
from app.dws_client import DWS_AGENT_CODE_ENV
from tests.prompt_structure import validate_prompt_structure


@pytest.fixture(autouse=True)
def _isolate_memory_connector_env(tmp_path: Path, monkeypatch):
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.delenv("CONNECTOR_API_KEY", raising=False)
    monkeypatch.delenv("MEMORY_CONNECTOR_AUTH_TYPE", raising=False)
    monkeypatch.delenv("MEMORY_CONNECTOR_CONTENT_TYPE", raising=False)
    monkeypatch.delenv("MEMORY_CONNECTOR_URL", raising=False)
    monkeypatch.delenv("MEMORY_CONNECTOR_USER_ID", raising=False)
    monkeypatch.delenv("CEO_CODEX_MODEL", raising=False)
    monkeypatch.delenv("CEO_CODEX_MODEL_PROVIDER", raising=False)
    monkeypatch.delenv("CEO_CODEX_MODEL_REASONING_EFFORT", raising=False)
    monkeypatch.delenv("CEO_CODEX_PROFILE", raising=False)
    monkeypatch.setenv(
        "CEO_DEVELOPER_PROMPT_TEMPLATE_PATH",
        str(Path(__file__).resolve().parents[1] / "app" / "defaults" / "developer_prompt.md"),
    )


def _developer_instructions_arg(command: list[str]) -> str:
    for index, item in enumerate(command):
        if item != "-c":
            continue
        value = command[index + 1]
        if value.startswith("developer_instructions="):
            return value
    raise AssertionError("developer_instructions config missing")


def _without_developer_instructions(command: list[str]) -> list[str]:
    cleaned: list[str] = []
    skip_next = False
    for index, item in enumerate(command):
        if skip_next:
            skip_next = False
            continue
        if item == "-c" and command[index + 1].startswith("developer_instructions="):
            skip_next = True
            continue
        cleaned.append(item)
    return cleaned


def test_codex_command_inherits_principal_codex_config_and_skills(tmp_path: Path):
    runner = CodexRunner(workspace=tmp_path, codex_bin="codex")

    command = runner.build_command(prompt="hello", session_id=None)

    assert "--ignore-user-config" not in command
    assert "--ignore-rules" not in command
    assert "hooks" not in command
    assert "features.plugins=false" not in command
    assert "features.apps=false" not in command


def test_codex_command_supports_strict_read_only_policy(tmp_path: Path):
    runner = CodexRunner(workspace=tmp_path, codex_bin="codex")

    command = runner.build_command(
        prompt="hello",
        session_id=None,
        approval_policy="never",
        developer_instructions="Read-only invocation. Do not write.",
    )

    assert 'approval_policy="never"' in command
    assert 'approval_policy="untrusted"' not in command
    assert "--dangerously-bypass-approvals-and-sandbox" not in command
    assert "Read-only invocation. Do not write." in _developer_instructions_arg(command)


def test_codex_command_uses_current_effectful_approval_policy(tmp_path: Path):
    runner = CodexRunner(workspace=tmp_path, codex_bin="codex")

    command = runner.build_command(prompt="hello", session_id=None)

    assert 'approval_policy="on-failure"' in command
    assert 'approval_policy="untrusted"' not in command
    assert 'approvals_reviewer="auto_review"' in command

    with pytest.raises(ValueError, match="unsupported approval policy"):
        runner.build_command(
            prompt="hello", session_id=None, approval_policy="untrusted"
        )


def test_native_read_fixture_command_ignores_user_config_and_exposes_only_reads(
    tmp_path: Path,
):
    from tests.support.native_codex_read_fixture import (
        assert_isolated_read_only_fixture_command,
        isolate_read_only_fixture_command,
    )

    command = CodexRunner(workspace=tmp_path).build_command(
        prompt="review fixture",
        session_id=None,
        approval_policy="never",
        use_approval_bypass=False,
    )
    insert_at = command.index("--cd")
    command[insert_at:insert_at] = [
        "-c",
        'features.plugins=true',
        "-c",
        'features.apps=true',
        "-c",
        'mcp_servers.foreign.command="unsafe-server"',
        "-c",
        'mcp_servers.foreign.enabled_tools=["write"]',
    ]
    isolated = isolate_read_only_fixture_command(
        command,
        server_command="python",
        server_args=("-m", "tests.support.fixture"),
        server_cwd=str(tmp_path),
    )

    assert_isolated_read_only_fixture_command(isolated)
    assert "--ignore-user-config" in isolated
    assert "--ignore-rules" in isolated
    assert "--ephemeral" in isolated
    assert isolated[isolated.index("--sandbox") + 1] == "read-only"
    assert "features.plugins=false" in isolated
    assert "features.apps=false" in isolated
    assert "tools.enabled_tools=[]" in isolated
    assert 'web_search="disabled"' in isolated
    assert 'approval_policy="on-failure"' in isolated
    assert 'approvals_reviewer="auto_review"' in isolated
    assert (
        'mcp_servers.agent_cli.enabled_tools=["read_skill","execute_reviewed_read"]'
        in isolated
    )
    assert all("execute_reviewed_write" not in item for item in isolated)
    assert all("mcp_servers.foreign" not in item for item in isolated)


def test_codex_command_read_only_resume_inherits_native_user_config(tmp_path: Path):
    command = CodexRunner(workspace=tmp_path).build_command(
        prompt="hello",
        session_id="session-1",
        approval_policy="never",
    )

    assert command[:3] == ["codex", "exec", "resume"]
    assert "--ignore-user-config" not in command
    assert "--dangerously-bypass-approvals-and-sandbox" not in command


def test_codex_command_read_only_resume_uses_config_sandbox_override(
    tmp_path: Path,
):
    command = CodexRunner(workspace=tmp_path).build_command(
        prompt="hello",
        session_id="session-1",
        approval_policy="never",
        sandbox_mode="read-only",
    )

    assert "--sandbox" not in command
    assert 'sandbox_mode="read-only"' in command


def test_codex_command_can_preserve_native_model_config(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CEO_CODEX_MODEL", "codex-MiniMax-M2.7")
    monkeypatch.setenv("CEO_CODEX_MODEL_PROVIDER", "minimax")

    command = CodexRunner(workspace=tmp_path).build_command(
        prompt="hello",
        session_id=None,
        preserve_native_model_config=True,
    )

    command_text = " ".join(command)
    assert "MiniMax" not in command_text
    assert "minimax" not in command_text
    assert "model_provider" not in command_text


def test_codex_model_options_accept_explicit_route_overrides(tmp_path: Path):
    runner = CodexRunner(workspace=tmp_path, codex_bin="codex")

    options = codex_model_config_options(
        model="gpt-5.5-api",
        provider="openai",
        reasoning_effort="high",
    )
    command = runner.build_command(
        prompt="hello",
        session_id=None,
        model="gpt-5.5-api",
        provider="openai",
        reasoning_effort="high",
    )

    assert options == [
        "-m",
        "gpt-5.5-api",
        "-c",
        'model_provider="openai"',
        "-c",
        'model_reasoning_effort="high"',
    ]
    assert command[command.index("-m") + 1] == "gpt-5.5-api"
    assert 'model_provider="openai"' in command
    assert 'model_reasoning_effort="high"' in command


def test_codex_command_accepts_explicit_provider_settings_and_shell_policy(
    tmp_path: Path,
):
    command = CodexRunner(workspace=tmp_path).build_command(
        prompt="hello",
        session_id=None,
        model="gpt-5.5",
        provider="ceo_openai_api",
        model_provider_settings={
            "name": "CEO OpenAI API fallback",
            "base_url": "https://api.openai.com/v1",
            "env_key": "OPENAI_API_KEY",
            "wire_api": "responses",
        },
        shell_environment_policy_core=True,
    )

    assert 'model_provider="ceo_openai_api"' in command
    assert 'model_providers.ceo_openai_api.env_key="OPENAI_API_KEY"' in command
    assert 'shell_environment_policy.inherit="core"' in command
    assert "shell_environment_policy.ignore_default_excludes=false" in command


def test_codex_model_options_without_arguments_keep_environment_defaults(monkeypatch):
    monkeypatch.setenv("CEO_CODEX_MODEL", "gpt-5.5-default")
    monkeypatch.setenv("CEO_CODEX_MODEL_PROVIDER", "default-provider")
    monkeypatch.setenv("CEO_CODEX_MODEL_REASONING_EFFORT", "low")

    assert codex_model_config_options() == [
        "-m",
        "gpt-5.5-default",
        "-c",
        'model_provider="default-provider"',
        "-c",
        'model_reasoning_effort="low"',
    ]


def test_preserving_native_instructions_does_not_read_workbench_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    def unexpected_prompt_read() -> str:
        raise AssertionError("native instruction mode must not read Workbench prompt")

    monkeypatch.setattr(
        codex_runner_module,
        "codex_developer_instructions",
        unexpected_prompt_read,
    )

    command = CodexRunner(workspace=tmp_path).build_command(
        prompt="hello",
        session_id=None,
        preserve_native_instructions=True,
    )

    assert not any(item.startswith("developer_instructions=") for item in command)


def test_codex_command_does_not_require_reasoning_summary_support(tmp_path: Path):
    command = CodexRunner(workspace=tmp_path).build_command(
        prompt="hello",
        session_id=None,
        preserve_native_model_config=True,
    )

    assert not any(
        item.startswith("model_reasoning_summary=")
        for item in command
    )


def test_codex_command_does_not_copy_principal_mcp_configuration(
    tmp_path: Path, monkeypatch
):
    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    (codex_home / "config.toml").write_text(
        "\n".join(
            [
                "[mcp_servers.xiaoqing_interview]",
                'url = "https://interview.hr.startask.net/mcp/"',
                "",
                "[mcp_servers.exa]",
                'command = "npx"',
                'args = ["-y", "exa-mcp-server"]',
                'startup_timeout_sec = 30',
                "",
                "[mcp_servers.exa.env]",
                'EXA_API_KEY = "secret-key"',
                "",
                "[mcp_servers.unrelated_business_tool]",
                'url = "https://unrelated.example/mcp/"',
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    command = CodexRunner(workspace=tmp_path).build_command(
        prompt="hello", session_id=None
    )

    assert "--ignore-user-config" not in command
    assert not any("EXA_API_KEY" in item for item in command)
    assert not any("secret-key" in item for item in command)
    assert not any("mcp_servers." in item for item in command)


def test_codex_developer_instructions_classify_dws_login_as_tool_issue():
    instructions = codex_developer_instructions()

    assert "not_authenticated" in instructions
    assert "exit code 2" in instructions
    assert "DWS login/tool issue" in instructions
    assert "Dependency Authentication" in instructions
    assert "Never run login, reset, or logout" in instructions
    assert "AGENT_CODE_NOT_EXISTS" in instructions
    assert "unavailable Memory dependency never triggers login" in instructions


def test_codex_runner_blocks_reply_when_only_dws_material_read_fails(tmp_path: Path):
    envelope = {
        "kind": "reply",
        "user_response": {
            "mode": "send_reply",
            "text": "这个涉及个人敏感信息，单独同步我。",
            "sensitivity_kind": "internal_personnel",
        },
        "system_actions": [],
        "domain_payload": {},
        "audit": {
            "summary": "听记检索超时，未取得可用正文。",
            "documents": [],
            "confidence": 0.2,
        },
    }
    raw = "\n".join(
        [
            json.dumps(
                {
                    "type": "response_item",
                    "item": {
                        "type": "function_call",
                        "name": "exec_command",
                        "call_id": "call-dws",
                        "arguments": json.dumps(
                            {
                                "cmd": (
                                    "dws minutes list all --query 连航 "
                                    "--timeout 900 --format json"
                                )
                            }
                        ),
                    },
                }
            ),
            json.dumps(
                {
                    "type": "response_item",
                    "item": {
                        "type": "function_call_output",
                        "call_id": "call-dws",
                        "output": (
                            "Process exited with code 6\n"
                            '{"error":{"category":"discovery",'
                            '"reason":"request_failed"}}'
                        ),
                    },
                }
            ),
            json.dumps(
                {
                    "type": "response_item",
                    "item": {
                        "type": "agent_message",
                        "text": json.dumps(envelope, ensure_ascii=False),
                    },
                },
                ensure_ascii=False,
            ),
        ]
    )
    runner = CodexDecisionRunner(
        workspace=tmp_path,
        executor=lambda _command, _prompt: raw,
    )

    decision = runner.decide("整理近一个月和连航有关的听记", session_id=None)

    assert decision.action == CodexAction.STOP_WITH_ERROR
    assert decision.reason.startswith("dws_transient_dependency_unavailable:")
    assert decision.reply_text == ""
    assert len(runner.last_audit_tool_events) == 2


def test_codex_developer_instructions_leave_read_options_to_operation_skills():
    instructions = codex_developer_instructions()

    assert "--timeout 900" not in instructions
    assert CORE_DYNAMIC_SKILL_BODY in instructions
    assert instructions.count("[dynamic-skill]") == 1


def test_codex_composed_prompt_keeps_runtime_invariants_not_domain_workflows():
    instructions = codex_developer_instructions()

    validate_prompt_structure(
        instructions,
        contract_models=(("Pydantic Wire/Result Contract", CodexDecision),),
        dynamic_skill_body=CORE_DYNAMIC_SKILL_BODY,
        audit_rules=None,
        context_facts=None,
        size_limit=5_000,
    )


def test_codex_developer_instructions_leave_interview_workflow_to_skills():
    instructions = codex_developer_instructions()

    assert "Xiaoqing interview material reading" not in instructions
    assert "https://interview.hr.startask.net/candidates/" not in instructions
    assert "search_candidates" not in instructions
    assert "xiaoqing_interview" not in instructions
    assert CORE_DYNAMIC_SKILL_BODY in instructions


def test_codex_command_does_not_use_agent_envelope_schema_by_default(tmp_path: Path):
    runner = CodexRunner(workspace=tmp_path, codex_bin="codex")

    command = runner.build_command(
        prompt="hello",
        session_id=None,
    )

    assert "--output-schema" in command
    assert str(CODEX_DECISION_SCHEMA_PATH) in command
    assert str(AGENT_ENVELOPE_SCHEMA_PATH) not in command


def test_codex_command_can_use_explicit_output_schema(tmp_path: Path):
    runner = CodexRunner(workspace=tmp_path, codex_bin="codex")
    schema = tmp_path / "strict.schema.json"

    command = runner.build_command(
        prompt="hello",
        session_id=None,
        output_schema_path=schema,
    )

    schema_index = command.index("--output-schema") + 1
    assert command[schema_index] == str(schema)


def test_codex_command_can_skip_output_schema_for_service_result_validation(
    tmp_path: Path,
):
    runner = CodexRunner(workspace=tmp_path, codex_bin="codex")
    schema = tmp_path / "strict.schema.json"

    command = runner.build_command(
        prompt="hello",
        session_id=None,
        output_schema_path=schema,
        use_output_schema=False,
    )

    assert "--output-schema" not in command


def test_codex_runner_env_does_not_load_personal_memory_connector_env_file(
    tmp_path: Path, monkeypatch
):
    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    (codex_home / "memory_connector.env").write_text(
        "\n".join(
            [
                "export CONNECTOR_API_KEY='secret-token'",
                "export MEMORY_CONNECTOR_URL='https://memory.example/mcp/'",
                "export MEMORY_CONNECTOR_USER_ID='principal'",
                "export UNRELATED_SECRET='do-not-forward'",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.delenv("CONNECTOR_API_KEY", raising=False)
    monkeypatch.delenv("MEMORY_CONNECTOR_URL", raising=False)
    monkeypatch.delenv("MEMORY_CONNECTOR_USER_ID", raising=False)
    runner = CodexRunner(workspace=tmp_path, codex_bin="codex")

    env = runner.build_env()

    assert "CONNECTOR_API_KEY" not in env
    assert "MEMORY_CONNECTOR_URL" not in env
    assert "MEMORY_CONNECTOR_USER_ID" not in env
    assert "UNRELATED_SECRET" not in env


def test_codex_runner_env_preserves_process_auth_env_while_stripping_tool_secrets(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("CODEX_LOGIN_MARKER", "desktop-session")
    monkeypatch.setenv("DWS_CLIENT_SECRET", "dws-secret")
    monkeypatch.setenv("DINGTALK_APP_SECRET", "ding-secret")
    monkeypatch.setenv("MEMORY_CONNECTOR_USER_ID", "legacy-user")
    runner = CodexRunner(workspace=tmp_path, codex_bin="codex")

    env = runner.build_env()

    assert env["CODEX_LOGIN_MARKER"] == "desktop-session"
    assert "DWS_CLIENT_SECRET" not in env
    assert "DINGTALK_APP_SECRET" not in env
    assert "MEMORY_CONNECTOR_USER_ID" not in env


def test_codex_runner_env_reuses_default_user_dws_pat_scope(
    tmp_path: Path, monkeypatch
):
    monkeypatch.delenv(DWS_AGENT_CODE_ENV, raising=False)
    monkeypatch.delenv("CEO_DWS_AGENT_CODE", raising=False)
    runner = CodexRunner(workspace=tmp_path, codex_bin="codex")

    env = runner.build_env()

    assert DWS_AGENT_CODE_ENV not in env


def test_codex_runner_can_preserve_native_local_cli_auth_environment(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("LARK_CLI_AUTH_HOME", "/safe/lark-auth")
    monkeypatch.setenv(DWS_AGENT_CODE_ENV, "legacy-agent-code")
    runner = CodexRunner(workspace=tmp_path, codex_bin="codex")

    env = runner.build_env(preserve_local_cli_auth=True)

    assert env["LARK_CLI_AUTH_HOME"] == "/safe/lark-auth"
    assert DWS_AGENT_CODE_ENV not in env


def test_codex_runner_inherits_personal_mcp_auth_without_copying_it(
    tmp_path: Path, monkeypatch
):
    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    (codex_home / "config.toml").write_text(
        "\n".join(
            [
                "[mcp_servers.memory_connector]",
                'url = "https://memory.example/mcp/"',
                "",
                "[mcp_servers.memory_connector.http_headers]",
                'Authorization = "Bearer secret-token"',
                'X-Friday-Memory-Auth-Type = "api_key"',
                'Content-Type = "application/json"',
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.delenv("CONNECTOR_API_KEY", raising=False)
    monkeypatch.delenv("MEMORY_CONNECTOR_URL", raising=False)
    runner = CodexRunner(workspace=tmp_path, codex_bin="codex")

    env = runner.build_env()
    command = runner.build_command(prompt="hello", session_id=None)

    assert "CONNECTOR_API_KEY" not in env
    assert "MEMORY_CONNECTOR_AUTH_TYPE" not in env
    assert "MEMORY_CONNECTOR_CONTENT_TYPE" not in env
    assert "MEMORY_CONNECTOR_URL" not in env
    assert "--ignore-user-config" not in command
    assert "secret-token" not in command
    assert not any("mcp_servers.memory_connector" in item for item in command)


def test_codex_command_treats_native_memory_oauth_as_runtime_owned(
    tmp_path: Path, monkeypatch
):
    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    (codex_home / "config.toml").write_text(
        "\n".join(
            [
                "[mcp_servers.memory_connector]",
                'url = "https://memory.example/mcp/"',
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.delenv("CONNECTOR_API_KEY", raising=False)
    monkeypatch.delenv("MEMORY_CONNECTOR_URL", raising=False)
    runner = CodexRunner(workspace=tmp_path, codex_bin="codex")

    command = runner.build_command(prompt="hello", session_id=None)
    developer_arg = _developer_instructions_arg(command)

    assert not any("mcp_servers.memory_connector" in item for item in command)
    assert memory_connector_config_issue() == ""
    assert "unavailable Memory dependency never triggers login" in developer_arg


def test_codex_command_does_not_auto_fallback_to_configured_profile(
    tmp_path: Path, monkeypatch
):
    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    (codex_home / "config.toml").write_text(
        "\n".join(
            [
                "[profiles.m27]",
                'model = "codex-MiniMax-M2.7"',
                'model_provider = "minimax"',
                "",
                "[model_providers.minimax]",
                'name = "MiniMax Chat Completions API"',
                'base_url = "https://api.minimaxi.com/v1"',
                'env_key = "MINIMAX_API_KEY"',
                'wire_api = "responses"',
                "requires_openai_auth = false",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    runner = CodexRunner(workspace=tmp_path, codex_bin="codex")

    command = runner.build_command(prompt="hello", session_id=None)

    assert command[command.index("-m") + 1] == "gpt-5.5"
    assert 'model_reasoning_effort="medium"' in command
    assert 'model_provider="minimax"' not in command


def test_codex_command_ignores_legacy_profile_env_for_service_default_model(
    tmp_path: Path, monkeypatch
):
    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    (codex_home / "config.toml").write_text(
        "\n".join(
            [
                "[profiles.m27]",
                'model = "codex-MiniMax-M2.7"',
                'model_provider = "minimax"',
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("CEO_CODEX_PROFILE", "m27")
    runner = CodexRunner(workspace=tmp_path, codex_bin="codex")

    command = runner.build_command(prompt="hello", session_id=None)

    assert command[command.index("-m") + 1] == "gpt-5.5"
    assert 'model_reasoning_effort="medium"' in command
    assert 'model_provider="minimax"' not in command


def test_command_does_not_copy_personal_model_provider_definition(
    tmp_path: Path, monkeypatch
):
    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    (codex_home / "config.toml").write_text(
        "\n".join(
            [
                "[profiles.m27]",
                'model = "codex-MiniMax-M2.7"',
                'model_provider = "minimax"',
                "",
                "[model_providers.minimax]",
                'name = "MiniMax Chat Completions API"',
                'base_url = "https://api.minimaxi.com/v1"',
                'env_key = "MINIMAX_API_KEY"',
                'wire_api = "responses"',
                "requires_openai_auth = false",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("CEO_CODEX_MODEL", "codex-MiniMax-M2.7")
    monkeypatch.setenv("CEO_CODEX_MODEL_PROVIDER", "minimax")
    runner = CodexRunner(workspace=tmp_path, codex_bin="codex")

    command = runner.build_command(prompt="hello", session_id=None)

    assert "--ignore-user-config" not in command
    assert command[command.index("-m") + 1] == "codex-MiniMax-M2.7"
    assert 'model_provider="minimax"' in command
    assert 'model_reasoning_effort="medium"' in command
    assert not any("model_providers.minimax.base_url" in item for item in command)
    assert not any("model_providers.minimax.env_key" in item for item in command)
    assert not any("api.minimaxi.com" in item for item in command)


def test_codex_runner_does_not_forward_memory_user_id(
    tmp_path: Path,
    monkeypatch,
):
    monkeypatch.setenv("CEO_PRINCIPAL_NAME", "Executive")
    monkeypatch.setenv("MEMORY_CONNECTOR_USER_ID", "principal")
    runner = CodexRunner(workspace=tmp_path, codex_bin="codex")

    env = runner.build_env()

    assert "MEMORY_CONNECTOR_USER_ID" not in env
    command = runner.build_command(prompt="hello", session_id=None)
    assert "x-memory-user-id" not in " ".join(command)


def test_codex_runner_does_not_forward_dws_oauth_override_env(
    tmp_path: Path,
    monkeypatch,
):
    monkeypatch.setenv("DWS_CLIENT_ID", "wrong-client-id")
    monkeypatch.setenv("DWS_CLIENT_SECRET", "wrong-client-secret")
    monkeypatch.setenv("DINGTALK_APP_KEY", "wrong-app-key")
    monkeypatch.setenv("DINGTALK_APP_SECRET", "wrong-app-secret")
    runner = CodexRunner(workspace=tmp_path, codex_bin="codex")

    env = runner.build_env()

    assert "DWS_CLIENT_ID" not in env
    assert "DWS_CLIENT_SECRET" not in env
    assert "DINGTALK_APP_KEY" not in env
    assert "DINGTALK_APP_SECRET" not in env


def test_codex_developer_instructions_fold_dependency_guidance_into_invariant_eight():
    instructions = codex_developer_instructions()

    assert "Runtime dependency handling" not in instructions
    assert "8. [dependency_auth] Dependency Authentication:" in instructions


def test_codex_developer_instructions_delegate_operation_syntax_to_skills():
    instructions = codex_developer_instructions()

    assert "DingTalk mail handling" not in instructions
    assert "mailbox list" not in instructions
    assert "mail message search" not in instructions
    assert "mail message get" not in instructions
    assert "dws doc info --node" not in instructions
    assert "dws doc read --node" not in instructions
    assert "dws minutes get info --id" not in instructions
    assert "Use the exact read command supplied in the task context" not in instructions
    assert CORE_DYNAMIC_SKILL_BODY in instructions
    assert "as a DWS login/tool issue" in instructions
    assert "as DWS authorization/configuration unavailable" in instructions
    assert "External Secrecy" in instructions


def test_builds_new_thread_command(tmp_path: Path):
    runner = CodexRunner(workspace=tmp_path, codex_bin="codex")

    command = runner.build_command(prompt="hello", session_id=None)

    developer_arg = _developer_instructions_arg(command)
    assert "Consumer Agent A is 明哥's read-only representative" in developer_arg
    assert "Pydantic output contract" in developer_arg
    assert "当前待处理消息" not in developer_arg
    assert "\\n" in developer_arg
    assert "memory_write" not in developer_arg
    assert "memory_recall" not in developer_arg

    assert _without_developer_instructions(command) == [
        "codex",
        "exec",
        "--json",
        "-m",
        "gpt-5.5",
        "-c",
        'model_reasoning_effort="medium"',
        "-c",
        'approval_policy="on-failure"',
        "-c",
        'approvals_reviewer="auto_review"',
        "-c",
        "include_permissions_instructions=false",
        "--dangerously-bypass-approvals-and-sandbox",
        "--output-schema",
        str(CODEX_DECISION_SCHEMA_PATH),
        "--cd",
        str(tmp_path),
        "-",
    ]
    assert "hello" not in command


def test_builds_resume_command(tmp_path: Path):
    runner = CodexRunner(workspace=tmp_path, codex_bin="codex")

    command = runner.build_command(prompt="next", session_id="abc")

    developer_arg = _developer_instructions_arg(command)
    assert "Consumer Agent A is 明哥's read-only representative" in developer_arg
    assert "Pydantic output contract" in developer_arg
    assert "当前待处理消息" not in developer_arg

    assert _without_developer_instructions(command) == [
        "codex",
        "exec",
        "resume",
        "--json",
        "-m",
        "gpt-5.5",
        "-c",
        'model_reasoning_effort="medium"',
        "-c",
        'approval_policy="on-failure"',
        "-c",
        'approvals_reviewer="auto_review"',
        "-c",
        "include_permissions_instructions=false",
        "--dangerously-bypass-approvals-and-sandbox",
        "abc",
        "-",
    ]
    assert "next" not in command


def test_builds_new_thread_command_with_images(tmp_path: Path):
    runner = CodexRunner(workspace=tmp_path, codex_bin="codex")
    first_image = tmp_path / "first.png"
    second_image = tmp_path / "second.jpg"

    command = runner.build_command(
        prompt="hello",
        session_id=None,
        image_paths=[first_image, second_image],
    )

    assert command[-7:] == [
        "--image",
        str(first_image),
        "--image",
        str(second_image),
        "--cd",
        str(tmp_path),
        "-",
    ]


def test_builds_resume_command_with_images(tmp_path: Path):
    runner = CodexRunner(workspace=tmp_path, codex_bin="codex")
    image = tmp_path / "diagram.png"

    command = runner.build_command(
        prompt="next",
        session_id="abc",
        image_paths=[image],
    )

    assert command[-4:] == [
        "--image",
        str(image),
        "abc",
        "-",
    ]


def test_codex_developer_instructions_hold_thread_prompt_not_turn_message(monkeypatch):
    monkeypatch.setenv(
        "CEO_PROMPT_VAR_RESPONSIBILITY_SUMMARY",
        "星尘数据的CEO，负责算法部、售前部、市场部、HR部的工作。",
    )
    instructions = codex_developer_instructions()

    assert instructions.startswith("## Runtime Invariants\n")
    assert "Consumer Agent A is 明哥's read-only representative" in instructions
    assert "agent_cli.read_skill" in instructions
    assert "星尘数据的CEO，负责算法部、售前部、市场部、HR部的工作。" not in instructions
    assert "当前待处理消息" not in instructions


def test_codex_developer_instructions_do_not_always_load_work_profile(
    monkeypatch,
    tmp_path,
):
    profile = tmp_path / "work_profile.md"
    profile.write_text(
        "# Work Profile\n\n"
        "## Core Operating Loop\n\n"
        "- Keep the loop tight.\n\n"
        "心智模型、决策启发式、表达DNA\n",
        encoding="utf-8",
    )
    monkeypatch.setenv(
        "CEO_WORK_PROFILE_PATH",
        str(profile),
    )

    instructions = codex_developer_instructions()

    assert "明哥 工作人格 Profile" not in instructions
    assert "# Work Profile" not in instructions
    assert "Core Operating Loop" not in instructions
    assert "心智模型、决策启发式、表达DNA" not in instructions


def test_codex_developer_instructions_uses_template_variable_values():
    instructions = codex_developer_instructions()

    assert (
        "1. [role_boundary] Role Boundary: Consumer Agent A is 明哥's read-only "
        "representative; Audit Agent B is the only role allowed to execute an "
        "accepted candidate."
    ) in instructions


def test_codex_decision_schema_file_exists():
    assert CODEX_DECISION_SCHEMA_PATH.exists()
    text = CODEX_DECISION_SCHEMA_PATH.read_text(encoding="utf-8")
    assert '"audit_summary"' in text
    assert '"minLength": 1' in text
    schema = json.loads(text)
    assert set(schema["required"]) == set(schema["properties"])


def test_preserves_process_home_environment(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HOME", "/Users/principal")
    runner = CodexRunner(workspace=tmp_path, codex_bin="codex")

    env = runner.build_env()

    assert env["HOME"] == "/Users/principal"
