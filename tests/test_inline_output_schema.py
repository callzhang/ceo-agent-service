import json

import pytest

from app.agent_runtime_router import CodexCommandFactory, _claude_turn_text, _friday_turn_text


@pytest.mark.parametrize("bridge", [_claude_turn_text, _friday_turn_text])
def test_inline_output_schema_is_lossless_and_compact(tmp_path, bridge):
    schema = {"type": "object", "properties": {"特殊/key~": {"description": "Keep  spaces\nnewlines and \\literal", "type": "string"}}, "required": ["特殊/key~"], "additionalProperties": False}
    path = tmp_path / "pretty.schema.json"
    path.write_text(json.dumps(schema, ensure_ascii=False, indent=8))
    factory = CodexCommandFactory.standard(developer_instructions="ROLE", output_schema_path=path, use_output_schema=True)
    submitted = bridge(factory, "EXACT_FACT")
    compact = json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
    assert submitted.count(f"```json\n{compact}\n```") == 1
    assert "EXACT_FACT" in submitted
    assert json.loads(submitted.split("```json\n", 1)[1].split("\n```", 1)[0]) == schema
    assert json.loads(path.read_text()) == schema
