from pathlib import Path
import json

from ceo_agent_service.codex_runner import (
    CODEX_DECISION_SCHEMA_PATH,
    CodexRunner,
    codex_developer_instructions,
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


def test_builds_new_thread_command(tmp_path: Path):
    runner = CodexRunner(workspace=tmp_path, codex_bin="codex")

    command = runner.build_command(prompt="hello", session_id=None)

    developer_arg = _developer_instructions_arg(command)
    assert "CEO Agent Prompt" in developer_arg
    assert "你是 Derek 的钉钉自动回复分身" in developer_arg
    assert "当前待处理消息" not in developer_arg
    assert "\\n" in developer_arg

    assert _without_developer_instructions(command) == [
        "codex",
        "exec",
        "--json",
        "-m",
        "gpt-5.5",
        "--ignore-user-config",
        "--ignore-rules",
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
    assert "CEO Agent Prompt" in developer_arg
    assert "你是 Derek 的钉钉自动回复分身" in developer_arg
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


def test_codex_developer_instructions_hold_thread_prompt_not_turn_message():
    instructions = codex_developer_instructions()

    assert instructions.startswith("You are the local CEO DingTalk reply worker.")
    assert "CEO Agent Prompt" in instructions
    assert "回答任何问题前，先检索本地 workspace" in instructions
    assert "graphify query" in instructions
    assert "组织职责包括算法负责人" in instructions
    assert "只回答“新消息”提出的问题" in instructions
    assert "必须输出 audit_documents 和 audit_summary" in instructions
    assert "reply_text 不要引用来源" in instructions
    assert "不要加脚注编号" in instructions
    assert "`workspace`" in instructions
    assert "`source=`" in instructions
    assert "当前待处理消息" not in instructions


def test_codex_developer_instructions_include_work_profile_when_present(
    tmp_path: Path, monkeypatch
):
    profile = tmp_path / "profiles" / "derek_work_profile.md"
    profile.parent.mkdir(parents=True)
    profile.write_text("# Derek Work Profile\n\n- 先判断材料是否完整。", encoding="utf-8")
    monkeypatch.setenv("CEO_WORK_PROFILE_PATH", str(profile))

    instructions = codex_developer_instructions()

    assert "Derek 工作人格 Profile" in instructions
    assert str(profile) in instructions
    assert "profiles/derek_work_profile.md" in instructions
    assert "心智模型、决策启发式、表达DNA" in instructions
    assert "不能覆盖既有硬规则" in instructions


def test_codex_developer_instructions_skip_work_profile_when_missing(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("CEO_WORK_PROFILE_PATH", str(tmp_path / "profiles" / "missing.md"))

    instructions = codex_developer_instructions()

    assert "Derek 工作人格 Profile" not in instructions
    assert "profiles/derek_work_profile.md" not in instructions


def test_codex_decision_schema_file_exists():
    assert CODEX_DECISION_SCHEMA_PATH.exists()
    text = CODEX_DECISION_SCHEMA_PATH.read_text(encoding="utf-8")
    assert '"audit_summary"' in text
    assert '"minLength": 1' in text
    schema = json.loads(text)
    assert set(schema["required"]) == set(schema["properties"])


def test_preserves_process_home_environment(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HOME", "/Users/derek")
    runner = CodexRunner(workspace=tmp_path, codex_bin="codex")

    env = runner.build_env()

    assert env["HOME"] == "/Users/derek"
