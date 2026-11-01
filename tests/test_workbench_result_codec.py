"""Internal Workbench text persistence retains legitimate local references."""
import json
from types import SimpleNamespace

import pytest

from app.agent_runtime_router import (
    MAX_RUNTIME_RESULT_ENVELOPE_BYTES, RoutedResultCodec, RoutedResultEnvelopeTooLarge,
)
from app.workbench.runtime import RuntimeRequest
from app.workbench.service_runtime import ServiceWorkbenchRuntime, _WORKBENCH_RESULT_CODEC


@pytest.fixture(autouse=True)
def local_reference_boundary(monkeypatch):
    monkeypatch.setenv("CEO_FORBIDDEN_PATH_PREFIXES", "/Users/derek/")


LOCAL_ANSWER = "\n".join((
    "已核对相关规则：",
    "[OA](</Users/derek/.agents/skills/ceo-document-review/SKILL.md>)",
    "[摘要](</Users/derek/.agents/skills/ceo-daily-report/SKILL.md>)",
    "[反馈](</Users/derek/.agents/skills/ceo-feedback-processing/SKILL.md>)",
))


def test_workbench_codec_roundtrip_preserves_local_markdown_references(tmp_path):
    saved = tmp_path / "result.json"
    saved.write_text(_WORKBENCH_RESULT_CODEC.encode(LOCAL_ANSWER))
    assert _WORKBENCH_RESULT_CODEC.decode(saved.read_text()) == LOCAL_ANSWER
    assert json.loads(saved.read_text())["schema_id"] == "workbench.result.v2"


@pytest.mark.parametrize("direction", ["encode", "decode"])
def test_workbench_internal_text_still_rejects_credentials(direction):
    credential = "Authorization: Bearer synthetic-test-credential"
    with pytest.raises(ValueError, match="sensitive runtime data"):
        if direction == "encode":
            _WORKBENCH_RESULT_CODEC.encode(credential)
        else:
            _WORKBENCH_RESULT_CODEC.decode(json.dumps({
                "schema_id": _WORKBENCH_RESULT_CODEC.schema_id, "value": credential,
            }))


@pytest.mark.parametrize("direction", ["encode", "decode"])
def test_workbench_internal_text_still_rejects_oversized_envelopes(direction):
    text = "a" * MAX_RUNTIME_RESULT_ENVELOPE_BYTES
    with pytest.raises(RoutedResultEnvelopeTooLarge):
        if direction == "encode":
            _WORKBENCH_RESULT_CODEC.encode(text)
        else:
            _WORKBENCH_RESULT_CODEC.decode(json.dumps({
                "schema_id": _WORKBENCH_RESULT_CODEC.schema_id, "value": text,
            }))


@pytest.mark.parametrize("value", [None, 1, {}, []])
def test_workbench_internal_text_rejects_nontext(value):
    with pytest.raises((TypeError, ValueError)):
        _WORKBENCH_RESULT_CODEC.encode(value)
    with pytest.raises((TypeError, ValueError)):
        _WORKBENCH_RESULT_CODEC.decode(json.dumps({
            "schema_id": _WORKBENCH_RESULT_CODEC.schema_id, "value": value,
        }))


@pytest.mark.parametrize("allow_evidence_source_refs", [False, True])
def test_ordinary_text_codec_keeps_local_path_rejection(allow_evidence_source_refs):
    codec = RoutedResultCodec.text(
        schema_id="external.text.v1", allow_evidence_source_refs=allow_evidence_source_refs,
    )
    with pytest.raises(ValueError, match="sensitive runtime data"):
        codec.encode(LOCAL_ANSWER)
    with pytest.raises(ValueError, match="sensitive runtime data"):
        codec.decode(json.dumps({"schema_id": codec.schema_id, "value": LOCAL_ANSWER}))


def test_service_runtime_persists_parsed_final_text_through_real_codec(tmp_path):
    persisted = tmp_path / "native-result.json"

    class PersistingExecution:
        def execute(self, **kwargs):
            raw = json.dumps({"type": "item.completed", "item": {
                "type": "agent_message", "text": LOCAL_ANSWER,
            }})
            value = kwargs["parser"](raw)
            codec = kwargs["result_codec"]
            persisted.write_text(codec.encode(value))
            return SimpleNamespace(value=codec.decode(persisted.read_text()), session_id="native-session")

    runtime = ServiceWorkbenchRuntime(
        workspace=tmp_path, routed_execution_factory=lambda **kwargs: PersistingExecution(),
    )
    request = RuntimeRequest(turn_id="2", conversation_id="1", workspace=tmp_path, prompt="核对规则")
    result = runtime.wait(runtime.start(request, on_event=lambda event: None))
    assert result.status == "completed", (result.error_code, result.error_detail)
    assert result.final_text == LOCAL_ANSWER
    assert result.provider_session_ref == "native-session"
    assert json.loads(persisted.read_text())["schema_id"] == "workbench.result.v2"


def test_internal_text_named_constructor_is_generic():
    codec = RoutedResultCodec.internal_text(schema_id="internal.notes.v1")
    assert codec.decode(codec.encode(LOCAL_ANSWER)) == LOCAL_ANSWER
    with pytest.raises(ValueError, match="sensitive runtime data"):
        codec.encode("Authorization: Bearer synthetic-test-credential")
