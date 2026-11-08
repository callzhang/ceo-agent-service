import json
from pathlib import Path

import pytest

from app.agent_runtime_config import load_runtime_config
from app.claude_runtime_adapter import ClaudeCommandPolicy, ClaudeRuntimeAdapter


@pytest.mark.parametrize("filename", [
    "meeting_alignment_audit.schema.json", "meeting_alignment_decision.schema.json",
])
def test_native_cli_schema_preserves_constraints_without_unsupported_dialect(
    tmp_path, monkeypatch, filename,
):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    config = load_runtime_config({"CEO_AGENT_RUNTIME_ROUTES": "claude_oauth"})
    adapter = ClaudeRuntimeAdapter(
        workspace=tmp_path, config=config, service_mcp_servers=(),
    )
    path = Path(__file__).resolve().parents[1] / "app" / "schemas" / filename
    declared = json.loads(path.read_text())
    original = json.dumps(declared, sort_keys=True)
    command = adapter.build_command(
        route=config.routes[0], session_id=None, max_turns=5,
        policy=ClaudeCommandPolicy.no_tools(), json_schema=declared,
    )
    delivered = json.loads(command[command.index("--json-schema") + 1])
    # The real CLI rejects the fixed files' 2020-12 metaschema URI before
    # starting a model. Every actual field constraint must still reach it.
    assert "$schema" not in delivered
    assert set(delivered) == set(declared) - {"$schema"}
    for key in delivered:
        assert delivered[key] == declared[key]
    assert json.dumps(declared, sort_keys=True) == original
    assert command[command.index("--tools") + 1] == ""


def test_declared_meeting_schema_reaches_cli_and_returns_strict_audit_result(
    tmp_path, monkeypatch,
):
    from app.agent_result import parse_agent_text_result
    from app.agent_runtime_router import (
        AgentRuntimeRouter, CodexCommandFactory, RoutedCodexExecution,
    )
    from app.codex_runtime_adapter import CodexRuntimeAdapter
    from app.meeting_alignment_agent import MEETING_ALIGNMENT_AUDIT_SCHEMA_PATH
    from app.meeting_alignment_models import MeetingAlignmentAuditResult
    from app.process_runner import ProcessRunResult
    from app.store import AutoReplyStore

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    config = load_runtime_config({"CEO_AGENT_RUNTIME_ROUTES": "claude_oauth"})
    session = "42563f55-53d5-4c18-9612-43a0be406d0c"
    expected = {"outcome": "approve", "summary": "synthetic format probe", "feedback": None}

    def execute(command, **kwargs):
        assert "--json-schema" in command
        delivered = json.loads(command[command.index("--json-schema") + 1])
        assert "$schema" not in delivered
        assert delivered["required"] == ["outcome", "summary", "feedback"]
        assert command[command.index("--tools") + 1] == ""
        records = [
            {"type": "system", "subtype": "init", "session_id": session},
            {"type": "result", "subtype": "success", "is_error": False,
             "session_id": session, "result": "```json\nnot the native payload\n```",
             "structured_output": expected},
        ]
        return ProcessRunResult(0, "\n".join(map(json.dumps, records)), "")

    store = AutoReplyStore(tmp_path / "schema-wire.sqlite3")
    routed = RoutedCodexExecution(
        store=store, config=config,
        router=AgentRuntimeRouter(routes=config.routes, store=store, snapshots={}),
        adapter=CodexRuntimeAdapter(tmp_path, config),
        claude_adapter=ClaudeRuntimeAdapter(
            workspace=tmp_path, config=config, service_mcp_servers=(),
        ),
        executor=execute,
    )
    process, session_id = routed._run_claude_turn(
        route=config.routes[0], session_id=None,
        command_factory=CodexCommandFactory.standard(
            developer_instructions="Synthetic audit output contract",
            output_schema_path=MEETING_ALIGNMENT_AUDIT_SCHEMA_PATH,
            use_output_schema=True, model_only=True,
        ),
        prompt="Synthetic format probe only", on_stdout_line=lambda line: None,
    )
    actual = MeetingAlignmentAuditResult.model_validate_json(
        parse_agent_text_result(process.stdout)
    )
    assert session_id == session
    assert actual.model_dump() == expected
