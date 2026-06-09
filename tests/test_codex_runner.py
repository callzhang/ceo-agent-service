import base64
import json
from pathlib import Path

import pytest

from app.codex_runner import (
    AGENT_ENVELOPE_SCHEMA_PATH,
    CODEX_DECISION_SCHEMA_PATH,
    CodexRunner,
    codex_developer_instructions,
)
from app.config import repo_root


@pytest.fixture(autouse=True)
def _isolate_memory_connector_env(tmp_path: Path, monkeypatch):
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.delenv("CONNECTOR_API_KEY", raising=False)
    monkeypatch.delenv("MEMORY_CONNECTOR_URL", raising=False)
    monkeypatch.delenv("MEMORY_CONNECTOR_USER_ID", raising=False)


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


def _unsigned_jwt(payload: dict) -> str:
    header = {"alg": "none"}

    def encode(value: dict) -> str:
        raw = json.dumps(value, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    return f"{encode(header)}.{encode(payload)}."


def test_codex_command_exposes_memory_connector_mcp(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("MEMORY_CONNECTOR_URL", "https://memory.example/mcp/")
    monkeypatch.setenv("CONNECTOR_API_KEY", "secret-token")
    runner = CodexRunner(workspace=tmp_path, codex_bin="codex")

    command = runner.build_command(prompt="hello", session_id=None)

    assert "--ignore-user-config" in command
    assert command[command.index("--disable") + 1] == "hooks"
    assert (
        'mcp_servers.memory_connector.url="https://memory.example/mcp/"'
        in command
    )
    assert (
        'mcp_servers.memory_connector.bearer_token_env_var="CONNECTOR_API_KEY"'
        in command
    )
    assert "x-memory-user-id" not in " ".join(command)


def test_codex_developer_instructions_classify_dws_login_as_tool_issue():
    instructions = codex_developer_instructions()

    assert "not_authenticated" in instructions
    assert "exit code 2" in instructions
    assert "DWS login/tool issue" in instructions
    assert "not as missing material" in instructions


def test_codex_command_does_not_use_agent_envelope_schema_by_default(tmp_path: Path):
    runner = CodexRunner(workspace=tmp_path, codex_bin="codex")

    command = runner.build_command(prompt="hello", session_id=None)

    assert "--output-schema" not in command
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


def test_codex_runner_env_loads_memory_connector_env_file(
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

    assert env["CONNECTOR_API_KEY"] == "secret-token"
    assert env["MEMORY_CONNECTOR_URL"] == "https://memory.example/mcp/"
    assert "MEMORY_CONNECTOR_USER_ID" not in env
    assert "UNRELATED_SECRET" not in env


def test_codex_runner_env_loads_memory_connector_from_codex_config(
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

    assert env["CONNECTOR_API_KEY"] == "secret-token"
    assert env["MEMORY_CONNECTOR_URL"] == "https://memory.example/mcp/"
    assert "--ignore-user-config" in command
    assert "secret-token" not in command


def test_codex_runner_skips_expired_memory_connector_token_from_codex_config(
    tmp_path: Path, monkeypatch
):
    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    expired_token = _unsigned_jwt({"exp": 1})
    (codex_home / "config.toml").write_text(
        "\n".join(
            [
                "[mcp_servers.memory_connector]",
                'url = "https://memory.example/mcp/"',
                "",
                "[mcp_servers.memory_connector.http_headers]",
                f'Authorization = "Bearer {expired_token}"',
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
    assert "MEMORY_CONNECTOR_URL" in env
    assert not any("mcp_servers.memory_connector" in item for item in command)


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


def test_codex_developer_instructions_include_dws_material_reading_guidance():
    instructions = codex_developer_instructions()

    assert "DingTalk material reading" in instructions
    assert "dws doc info --node" in instructions
    assert "dws doc read --node" in instructions
    assert "dws minutes get info --id" in instructions
    assert "record why each material command was used" in instructions
    assert "Do not expose tokens" in instructions


def test_codex_command_reads_memory_connector_mcp_url_from_env_file(
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
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.delenv("CONNECTOR_API_KEY", raising=False)
    monkeypatch.delenv("MEMORY_CONNECTOR_URL", raising=False)
    monkeypatch.delenv("MEMORY_CONNECTOR_USER_ID", raising=False)
    runner = CodexRunner(workspace=tmp_path, codex_bin="codex")

    command = runner.build_command(prompt="hello", session_id=None)

    assert (
        'mcp_servers.memory_connector.url="https://memory.example/mcp/"'
        in command
    )
    assert (
        'mcp_servers.memory_connector.bearer_token_env_var="CONNECTOR_API_KEY"'
        in command
    )
    assert "x-memory-user-id" not in " ".join(command)


def test_builds_new_thread_command(tmp_path: Path):
    runner = CodexRunner(workspace=tmp_path, codex_bin="codex")

    command = runner.build_command(prompt="hello", session_id=None)

    developer_arg = _developer_instructions_arg(command)
    assert "你是 明哥 的钉钉自动回复分身" in developer_arg
    assert "默认不了解当前业务背景" in developer_arg
    assert "当前待处理消息" not in developer_arg
    assert "\\n" in developer_arg
    assert "memory_connector MCP 可用" in developer_arg
    assert "memory_write 记录一条业务 episode" in developer_arg

    assert _without_developer_instructions(command) == [
        "codex",
        "exec",
        "--json",
        "-m",
        "gpt-5.5",
        "--ignore-user-config",
        "--ignore-rules",
        "--disable",
        "hooks",
        "--disable",
        "plugins",
        "-c",
        'approval_policy="untrusted"',
        "-c",
        'approvals_reviewer="auto_review"',
        "-c",
        'model_reasoning_summary="concise"',
        "-c",
        "include_permissions_instructions=false",
        "-c",
        "include_apps_instructions=false",
        "-c",
        "include_environment_context=false",
        "--dangerously-bypass-approvals-and-sandbox",
        "--cd",
        str(tmp_path),
        "-",
    ]
    assert "hello" not in command


def test_builds_resume_command(tmp_path: Path):
    runner = CodexRunner(workspace=tmp_path, codex_bin="codex")

    command = runner.build_command(prompt="next", session_id="abc")

    developer_arg = _developer_instructions_arg(command)
    assert "你是 明哥 的钉钉自动回复分身" in developer_arg
    assert "默认不了解当前业务背景" in developer_arg
    assert "当前待处理消息" not in developer_arg

    assert _without_developer_instructions(command) == [
        "codex",
        "exec",
        "resume",
        "--json",
        "-m",
        "gpt-5.5",
        "--ignore-user-config",
        "--ignore-rules",
        "--disable",
        "hooks",
        "--disable",
        "plugins",
        "-c",
        'approval_policy="untrusted"',
        "-c",
        'approvals_reviewer="auto_review"',
        "-c",
        'model_reasoning_summary="concise"',
        "-c",
        "include_permissions_instructions=false",
        "-c",
        "include_apps_instructions=false",
        "-c",
        "include_environment_context=false",
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

    assert instructions.startswith("You are the local CEO DingTalk reply worker.")
    assert "你是 明哥 的钉钉自动回复分身" in instructions
    assert "默认不了解当前业务背景" in instructions
    assert "本地文件" in instructions
    assert "dws aisearch" in instructions
    assert "graphify query" in instructions
    assert "星尘数据的CEO，负责算法部、售前部、市场部、HR部的工作。" in instructions
    assert "只回答“新消息”提出的问题" in instructions
    assert "audit.documents 用于声明直接依据的材料" in instructions
    assert "user_response.text 不要引用来源" in instructions
    assert "不要加脚注编号" in instructions
    assert "`workspace`" in instructions
    assert "`source=`" in instructions
    assert "当前待处理消息" not in instructions


def test_codex_developer_instructions_inject_work_profile_content_without_path(
    monkeypatch,
):
    monkeypatch.setenv(
        "CEO_WORK_PROFILE_PATH",
        str(repo_root() / "profiles" / "work_profile.md"),
    )

    instructions = codex_developer_instructions()

    assert "明哥 工作人格 Profile" in instructions
    assert (
        "/Users/principal/Documents/Projects/ceo-agent-service/profiles/work_profile.md"
        not in instructions
    )
    assert "# Work Profile" in instructions
    assert "Core Operating Loop" in instructions
    assert "不要再尝试读取 profile 文件路径" in instructions
    assert "心智模型、决策启发式、表达DNA" in instructions
    assert "不能覆盖既有硬规则" in instructions


def test_codex_developer_instructions_uses_template_variable_values():
    instructions = codex_developer_instructions()

    assert "你是 明哥 的钉钉自动回复分身" in instructions
    assert "让 明哥 本人接管" in instructions


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
