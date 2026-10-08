import copy
import json

from jsonschema import Draft202012Validator
from pydantic import BaseModel, Field

from app.agent_context import AgentTaskContext
from app.agent_wire_contracts import ConsumerAgentWireResult, AuditAgentWireResult
from app.consumer_agent import _schema_json, _compact_prompt_schema


def test_compact_schema_preserves_actual_title_and_default_fields():
    class Document(BaseModel):
        title: str = Field(description="The document title")
        default: str = "draft"

    raw = Document.model_json_schema()
    compact = json.loads(_schema_json(Document))
    assert "title" not in compact
    assert set(compact["properties"]) == {"title", "default"}
    assert compact["properties"]["title"]["description"] == "The document title"
    assert "title" not in compact["properties"]["title"]
    assert "default" not in compact["properties"]["default"]
    for value in ({"title": "A"}, {"title": "A", "default": "B"}, {"default": "B"}):
        assert Draft202012Validator(raw).is_valid(value) == Draft202012Validator(compact).is_valid(value)


def test_wire_schema_compaction_retains_validation_constraints():
    common = dict(summary="fixture", error_code=None, error_retryable=False,
                  error_authorization_required=False, risk="low", confidence=0.9,
                  rule_coverage=1.0, information_completeness=1.0)
    consumer = dict(common, outcome="no_action", proposal=None, durable_memories=[])
    audit = dict(common, outcome="approve", proposal_revision=0,
                 candidate_digest="0" * 64, feedback=None)
    for model, valid in ((ConsumerAgentWireResult, consumer), (AuditAgentWireResult, audit)):
        raw = model.model_json_schema()
        compact = json.loads(_schema_json(model))
        Draft202012Validator.check_schema(compact)
        assert Draft202012Validator(compact).is_valid(valid)
        assert len(json.dumps(compact)) < len(json.dumps(raw))
        cases = [valid, dict(valid, confidence=1.5), dict(valid, surprise=True), dict(valid, outcome="unknown")]
        missing = copy.deepcopy(valid)
        del missing["summary"]
        cases.append(missing)
        for value in cases:
            assert Draft202012Validator(raw).is_valid(value) == Draft202012Validator(compact).is_valid(value)
        model.model_validate_json(json.dumps(valid))


def test_task_context_has_facts_and_stage_without_duplicate_result_rules():
    context = AgentTaskContext(
        task_id=1, channel="dingtalk", conversation_id="conversation", conversation_title="fixture",
        single_chat=True, trigger_message_id="message", trigger_sender="sender",
        trigger_text="UNMODIFIED_SOURCE", trigger_create_time="2026-10-07 10:00:00",
        messages=(), materials=(), prior_receipts=(), trigger_raw_payload={"source_fact": "KEEP"},
    )
    rendered = context.render(current_time="2026-10-07T10:00:00+08:00")
    assert "## Application Result Contract" not in rendered
    assert "UNMODIFIED_SOURCE" in rendered
    assert '"source_fact": "KEEP"' in rendered
    assert "### Execution stage" in rendered


def test_schema_annotations_do_not_remove_literal_object_keys_or_dependencies():
    schema = {
        "type": "object",
        "properties": {"title": {"type": "string"}, "default": {"type": "string"}},
        "enum": [{"title": "A", "default": "B"}],
        "dependentRequired": {"title": ["default"]},
    }
    compact = _compact_prompt_schema(schema)
    assert compact["enum"] == schema["enum"]
    assert compact["dependentRequired"] == schema["dependentRequired"]
    for value in ({"title": "A", "default": "B"}, {"title": "A"}, {}):
        assert Draft202012Validator(schema).is_valid(value) == Draft202012Validator(compact).is_valid(value)


def test_role_command_suppresses_project_docs_without_changing_native_home(monkeypatch):
    from app.wechat.codex_safety import make_role_agent_command
    monkeypatch.setattr("app.wechat.codex_safety.inherited_native_server_names", lambda: ())
    monkeypatch.setattr("app.wechat.codex_safety.codex_skill_config_override", lambda names: None)
    for role in ("consumer", "audit"):
        command = ["codex", "exec", "-c", "project_doc_max_bytes=32768", "task"]
        make_role_agent_command(command, role=role, task_workspace="/task" if role == "consumer" else None)
        options = [command[i+1] for i, item in enumerate(command[:-1]) if item == "-c"]
        assert [x for x in options if x.startswith("project_doc_max_bytes=")] == ["project_doc_max_bytes=0"]
        assert not any("home=" in x or "model_instructions_file=" in x for x in options)


def test_contract_fingerprint_includes_details_beyond_catalog_names(monkeypatch):
    import app.consumer_agent as consumer
    before = consumer.consumer_wire_contract_hash()
    document = consumer.system_action_contract_document()
    monkeypatch.setattr(consumer, "system_action_contract_document", lambda: document + "\nA changed operation detail.")
    assert consumer.consumer_wire_contract_hash() != before
