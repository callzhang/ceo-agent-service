import json
from pathlib import Path

import pytest

from app.process_runner import ProcessRunResult
from app.universal_context import UniversalContextMessage, UniversalTaskContext


def _context() -> UniversalTaskContext:
    return UniversalTaskContext(
        task_id=42,
        conversation_id="cid-1",
        conversation_title="Operations",
        single_chat=False,
        trigger_message_id="msg-2",
        trigger_sender="Derek",
        trigger_text="Please review the supplier request.",
        context_messages=(
            UniversalContextMessage(
                sender_name="Derek",
                open_message_id="msg-2",
                content="Please review the supplier request.",
            ),
        ),
        required_dependencies=("dws",),
        force_new_decision=False,
        dry_run=False,
    )


def _plan_payload(**overrides):
    payload = {
        "planner_version": "2026-07-20",
        "task_kind": "supplier_review",
        "reason": "The request needs a reviewed reply.",
        "dependencies": ["dws"],
        "actions": [
            {
                "kind": "send_reply",
                "reason": "Reply after the service executes this plan.",
                "target": {},
                "payload": {"text": "I will review it today."},
            }
        ],
        "audit": {
            "summary": "The trigger provides enough context to plan a reply.",
            "documents": [],
            "confidence": 0.8,
        },
    }
    payload.update(overrides)
    return payload


def _config_values(command: list[str]) -> list[str]:
    return [
        command[index + 1]
        for index, value in enumerate(command[:-1])
        if value == "-c"
    ]


def test_parse_universal_plan_json_accepts_direct_and_newest_nested_jsonl_payload():
    from app.universal_planner import parse_universal_plan_json

    direct = parse_universal_plan_json(json.dumps(_plan_payload()))
    nested = parse_universal_plan_json(
        "\n".join(
            [
                json.dumps({"type": "thread.started", "thread_id": "thread-1"}),
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {"text": json.dumps(_plan_payload(task_kind="old"))},
                    }
                ),
                json.dumps({"message": json.dumps(_plan_payload())}),
            ]
        )
    )

    assert direct.task_kind == "supplier_review"
    assert nested.task_kind == "supplier_review"


def test_parse_universal_plan_json_rejects_malformed_and_extra_field_payloads():
    from app.universal_planner import parse_universal_plan_json

    with pytest.raises(ValueError):
        parse_universal_plan_json("not json")

    with pytest.raises(ValueError):
        parse_universal_plan_json(json.dumps(_plan_payload(unexpected=True)))

    with pytest.raises(ValueError):
        parse_universal_plan_json(
            json.dumps(
                _plan_payload(message=json.dumps(_plan_payload(task_kind="nested")))
            )
        )


def test_build_prompt_sets_planner_boundary_and_includes_schema_and_context():
    from app.universal_planner import UNIVERSAL_PLAN_SCHEMA_HINT, UniversalPlanner

    prompt = UniversalPlanner(workspace=Path("/tmp/workspace")).build_prompt(_context())

    assert "must not directly execute externally visible side effects" in prompt
    assert "DWS is blocking for DingTalk" in prompt
    assert "must already be service-checked" in prompt
    assert "dws auth login" in prompt
    assert "dws auth reset" in prompt
    assert "dws auth logout" in prompt
    assert "only UniversalPlan JSON" in prompt
    assert UNIVERSAL_PLAN_SCHEMA_HINT in prompt
    assert "at least one action" in prompt
    assert "0.0..1.0" in prompt
    assert "Please review the supplier request." in prompt


def test_plan_uses_new_and_resume_commands_with_configured_mcps(tmp_path, monkeypatch):
    from app.codex_runner import CODEX_BYPASS_APPROVALS_AND_SANDBOX
    from app.universal_planner import UniversalPlanner

    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    (codex_home / "config.toml").write_text(
        "\n".join(
            [
                "[mcp_servers.exa]",
                'url = "https://exa.example/mcp"',
                "[mcp_servers.memory_connector]",
                'url = "https://memory.example/mcp"',
                '[mcp_servers.memory_connector.http_headers]',
                'Authorization = "Bearer memory-token"',
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("CEO_CODEX_MODEL", "planner-model")
    monkeypatch.setenv("CEO_CODEX_MODEL_REASONING_EFFORT", "high")
    calls = []

    def executor(command, prompt, env):
        calls.append((command, prompt, env))
        return json.dumps(_plan_payload())

    planner = UniversalPlanner(workspace=tmp_path, codex_bin="/opt/bin/codex", executor=executor)
    planner.plan(_context())
    planner.plan(_context(), session_id="session-1")

    new_command, _, _ = calls[0]
    resume_command, _, _ = calls[1]
    assert new_command[:2] == ["/opt/bin/codex", "exec"]
    assert "resume" not in new_command[:3]
    assert new_command[-3:] == ["--cd", str(tmp_path), "-"]
    assert resume_command[:3] == ["/opt/bin/codex", "exec", "resume"]
    assert resume_command[-2:] == ["session-1", "-"]
    for command in (new_command, resume_command):
        assert "--json" in command
        assert "--ignore-user-config" in command
        assert "--ignore-rules" in command
        assert CODEX_BYPASS_APPROVALS_AND_SANDBOX in command
        assert command[command.index("-m") + 1] == "planner-model"
        assert [command[index + 1] for index, value in enumerate(command[:-1]) if value == "--disable"] == ["hooks", "plugins"]
        assert any("mcp_servers.exa.url" in value for value in _config_values(command))
        assert any("mcp_servers.memory_connector.url" in value for value in _config_values(command))
        assert 'model_reasoning_effort="high"' in _config_values(command)


def test_plan_passes_noninteractive_environment_to_executor_and_returns_plan(tmp_path):
    from app.dws_client import DWS_AGENT_CODE_ENV
    from app.universal_planner import UniversalPlanner

    received = {}

    def executor(command, prompt, env):
        received.update(command=command, prompt=prompt, env=env)
        return json.dumps(_plan_payload())

    planner = UniversalPlanner(workspace=tmp_path, executor=executor)
    plan = planner.plan(_context())

    assert plan.task_kind == "supplier_review"
    assert received["env"][DWS_AGENT_CODE_ENV] == "ceo-agent-service"
    assert planner.last_raw_output == json.dumps(_plan_payload())


def test_plan_repairs_invalid_output_once_using_available_session(tmp_path):
    from app.universal_planner import UniversalPlanner

    calls = []

    def executor(command, prompt, env):
        calls.append((command, prompt, env))
        if len(calls) == 1:
            return "\n".join(
                [
                    json.dumps({"type": "thread.started", "thread_id": "thread-1"}),
                    json.dumps({"message": "not json"}),
                ]
            )
        return json.dumps(_plan_payload())

    planner = UniversalPlanner(workspace=tmp_path, executor=executor)
    plan = planner.plan(_context())

    assert plan.task_kind == "supplier_review"
    assert len(calls) == 2
    assert calls[1][0][:3] == ["codex", "exec", "resume"]
    assert calls[1][0][-2:] == ["thread-1", "-"]
    assert "only corrected UniversalPlan JSON" in calls[1][1]
    assert "must not re-run business side effects" in calls[1][1]
    assert planner.last_session_id == "thread-1"


def test_plan_strips_whitespace_from_supplied_session_id_before_resume(tmp_path):
    from app.universal_planner import UniversalPlanner

    calls = []

    def executor(command, prompt, env):
        calls.append(command)
        return json.dumps(_plan_payload())

    planner = UniversalPlanner(workspace=tmp_path, executor=executor)
    planner.plan(_context(), session_id="  session-1\n")

    assert calls[0][:3] == ["codex", "exec", "resume"]
    assert calls[0][-2:] == ["session-1", "-"]
    assert planner.last_session_id == "session-1"


def test_plan_does_not_repair_invalid_output_without_a_usable_session(tmp_path):
    from app.universal_planner import UniversalPlanner

    calls = []

    def executor(command, prompt, env):
        calls.append(command)
        return "\n".join(
            [
                json.dumps({"type": "thread.started", "thread_id": "  \t"}),
                json.dumps({"message": "not json"}),
            ]
        )

    planner = UniversalPlanner(workspace=tmp_path, executor=executor)

    with pytest.raises(ValueError, match="No valid UniversalPlan JSON"):
        planner.plan(_context(), session_id=" \n")

    assert len(calls) == 1
    assert "resume" not in calls[0][:3]
    assert planner.last_session_id is None


def test_plan_stops_after_one_invalid_repair_attempt(tmp_path):
    from app.universal_planner import UniversalPlanner

    calls = []

    def executor(command, prompt, env):
        calls.append(command)
        return "\n".join(
            [
                json.dumps({"type": "thread.started", "thread_id": "thread-1"}),
                json.dumps({"message": "not json"}),
            ]
        )

    planner = UniversalPlanner(workspace=tmp_path, executor=executor)

    with pytest.raises(ValueError, match="No valid UniversalPlan JSON"):
        planner.plan(_context())

    assert len(calls) == 2
    assert calls[1][:3] == ["codex", "exec", "resume"]


def test_plan_raises_clear_timeout_and_nonzero_process_errors(tmp_path):
    from app.universal_planner import UniversalPlanner

    planner = UniversalPlanner(workspace=tmp_path)
    calls = []

    def timeout_runner(*args, **kwargs):
        calls.append((args, kwargs))
        return ProcessRunResult(
            returncode=-15,
            stdout="",
            stderr="",
            timed_out=True,
            timeout_reason="process produced no output for 900 seconds",
        )

    planner._run_process_with_idle_timeout = timeout_runner
    with pytest.raises(RuntimeError, match="no output for 900 seconds"):
        planner.plan(_context())
    assert calls[0][1]["total_timeout_seconds"] == 1200
    assert calls[0][1]["idle_timeout_seconds"] >= 900

    planner._run_process_with_idle_timeout = lambda *args, **kwargs: ProcessRunResult(
        returncode=1,
        stdout="",
        stderr="codex command failed",
    )
    with pytest.raises(RuntimeError, match="exited with status 1"):
        planner.plan(_context())
