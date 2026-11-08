import json

from pathlib import Path

from app.agent_runtime_config import load_runtime_config
from app.claude_runtime_adapter import ClaudeRuntimeAdapter, ClaudeCommandPolicy

from app.agent_runtime_router import CodexCommandFactory, _claude_turn_text, _friday_turn_text


def test_friday_inline_output_schema_is_lossless_and_compact(tmp_path):
    schema = {"type": "object", "properties": {"特殊/key~": {"description": "Keep  spaces\nnewlines and \\literal", "type": "string"}}, "required": ["特殊/key~"], "additionalProperties": False}
    path = tmp_path / "pretty.schema.json"
    path.write_text(json.dumps(schema, ensure_ascii=False, indent=8))
    factory = CodexCommandFactory.standard(developer_instructions="ROLE", output_schema_path=path, use_output_schema=True)
    submitted = _friday_turn_text(factory, "EXACT_FACT")
    compact = json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
    assert submitted.count(f"```json\n{compact}\n```") == 1
    assert "EXACT_FACT" in submitted
    assert json.loads(submitted.split("```json\n", 1)[1].split("\n```", 1)[0]) == schema
    assert json.loads(path.read_text()) == schema


def test_claude_native_schema_argument_is_lossless_and_not_duplicated_in_text(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    schema = {"type": "object", "properties": {"特殊/key~": {
        "description": "Keep  spaces\nnewlines and \\literal", "type": "string",
    }}, "required": ["特殊/key~"], "additionalProperties": False}
    path = tmp_path / "pretty.schema.json"
    path.write_text(json.dumps(schema, ensure_ascii=False, indent=8))
    factory = CodexCommandFactory.standard(
        developer_instructions="ROLE", output_schema_path=path, use_output_schema=True,
    )
    config = load_runtime_config({"CEO_AGENT_RUNTIME_ROUTES": "claude_oauth"})
    adapter = ClaudeRuntimeAdapter(workspace=tmp_path, config=config, service_mcp_servers=())
    command = adapter.build_command(
        route=config.routes[0], session_id=None, max_turns=5,
        policy=ClaudeCommandPolicy.no_tools(), json_schema=json.loads(factory.output_schema_path.read_text()),
    )
    encoded = command[command.index("--json-schema") + 1]
    assert encoded == json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
    assert json.loads(encoded) == json.loads(path.read_text()) == schema
    submitted = _claude_turn_text(factory, "EXACT_FACT")
    assert "ROLE" in submitted and "EXACT_FACT" in submitted
    assert "# Output JSON Schema" not in submitted
    assert "特殊/key~" not in submitted
