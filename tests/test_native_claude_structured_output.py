import json

import pytest

from app.agent_result import parse_agent_text_result
from app.native_trajectory import read_native_result_stream


def candidate(value, ident="output-1"):
    return {"type": "assistant", "message": {"content": [{
        "type": "tool_use", "name": "StructuredOutput", "id": ident, "input": value,
    }]}}


def receipt(ident="output-1", **fields):
    return {"type": "user", "message": {"content": [{
        "type": "tool_result", "tool_use_id": ident,
        "content": "Structured output provided successfully", **fields,
    }]}}


def read_rows(tmp_path, monkeypatch, rows, start=0, end=None):
    path = tmp_path / "native.jsonl"
    path.write_text("\n".join(map(json.dumps, rows)) + "\n")
    monkeypatch.setattr("app.native_trajectory.claude_session_path", lambda sid: path)
    return read_native_result_stream("claude_cli", "native-session", start,
                                     len(rows) if end is None else end)


def test_native_claude_result_requires_successful_structured_output_receipt(tmp_path, monkeypatch):
    value = {"ok": True}
    rows = [candidate(value), receipt(), {"type": "assistant", "message": {
        "content": [{"type": "text", "text": "Final human explanation"}],
    }}]
    assert json.loads(parse_agent_text_result(read_rows(tmp_path, monkeypatch, rows))) == value


@pytest.mark.parametrize("result", [[], [receipt(is_error=True)], [receipt("other")]])
def test_unaccepted_structured_input_is_not_a_final_result(tmp_path, monkeypatch, result):
    assert read_rows(tmp_path, monkeypatch, [candidate({"ok": True}), *result]) == ""


def test_native_structured_readback_respects_both_candidate_and_receipt_bounds(tmp_path, monkeypatch):
    rows = [candidate({"summary": "old turn"}, "old"), receipt("old"),
            candidate({"summary": "current"}), receipt(),
            candidate({"summary": "later turn"}, "later"), receipt("later")]
    raw = read_rows(tmp_path, monkeypatch, rows, 2, 4)
    assert json.loads(parse_agent_text_result(raw)) == {"summary": "current"}
    assert read_rows(tmp_path, monkeypatch, rows, 2, 3) == ""
    assert read_rows(tmp_path, monkeypatch, rows, 3, 4) == ""
