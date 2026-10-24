import json

import pytest
from jsonschema import Draft202012Validator

from app.agent_result import ResultParseError
from app.agent_wire_contracts import consumer_wire_model, parse_consumer_agent_wire_result
from app.native_agent_output import (
    _decode,
    native_output_schema,
    native_output_schema_path,
    parse_native_output,
    parse_native_result,
)


def _event(value):
    return json.dumps({"type": "item.completed", "item": {
        "type": "agent_message", "text": json.dumps(value, ensure_ascii=False),
    }}, ensure_ascii=False)


def _consumer(action="no_action", *, purpose="general", target=None, payload=None):
    wire = consumer_wire_model(purpose).model_validate({
        "action": action, "reason": "Current source checked", "target": target or {},
        "payload": payload or {}, "evidence_refs": ["source:1"],
        "summary": "Current source checked", "risk": "low", "confidence": 1.0,
        "rule_coverage": 1.0, "information_completeness": 1.0,
        
    }).model_dump(mode="json")
    wire["decision"] = {name: wire.pop(name) for name in ("action", "target", "payload")}
    return wire


def _encode_dynamic(value):
    if isinstance(value, dict):
        return {"entries": [{"key": key, "value": _encode_dynamic(item)}
                            for key, item in value.items()]}
    if isinstance(value, list):
        return [_encode_dynamic(item) for item in value]
    return value


def test_native_consumer_schema_tracks_both_current_purposes():
    general = native_output_schema(purpose="general")
    oa = native_output_schema(purpose="oa")
    Draft202012Validator.check_schema(general)
    Draft202012Validator.check_schema(oa)

    def actions(schema):
        return {branch["properties"]["action"]["enum"][0]
                for branch in schema["properties"]["decision"]["anyOf"]}

    assert actions(oa) - actions(general) == {"approve", "reject", "return", "add_approver", "comment"}


@pytest.mark.parametrize("purpose", ["general", "oa"])
def test_committed_native_schema_matches_current_wire_contract(purpose):
    assert json.loads(native_output_schema_path(purpose=purpose).read_text()) == (
        native_output_schema(purpose=purpose)
    )


def test_native_consumer_schema_rejects_flat_result_that_can_lose_root_fields():
    malformed = _consumer()
    malformed.pop("decision")
    malformed.update({"action": "no_action", "target": {}, "payload": {}})

    assert not Draft202012Validator(native_output_schema()).is_valid(malformed)


@pytest.mark.parametrize("purpose", ["general", "oa"])
def test_native_consumer_schema_preserves_exact_current_wire_values(purpose):
    target = ({"process_instance_id": "p", "task_id": "001", "target_activity_id": "a"}
              if purpose == "oa" else {"conversation_id": "c", "message_id": "m"})
    payload = ({"remark": "请补充材料", "revert_action": "REVERT_FOR_RESUBMIT"}
               if purpose == "oa" else {"content": "草稿"})
    native = _consumer("return" if purpose == "oa" else "reply", purpose=purpose,
                       target=target, payload=payload)
    assert Draft202012Validator(native_output_schema(purpose=purpose)).is_valid(native)

    result = parse_native_result(
        _event(native), "consumer", purpose=purpose,
        parser=lambda raw: parse_consumer_agent_wire_result(raw, purpose=purpose),
    )
    assert result.purpose_output["target"] == target
    assert result.purpose_output["payload"] == payload


def test_native_consumer_restores_nested_dynamic_json_without_losing_special_keys():
    target = {"conversation_id": "c", "message_id": "m"}
    payload = {"content": "草稿", "a/~b": [{"nested": [None, True, 1.5, 2]}]}
    native = _consumer("plan", payload={
        "objective": "交付草稿", "actions": [{
            "description": "交付草稿", "action_identity": "draft-1",
            "capability": "dingtalk-chat", "operation": "reply_to_message",
            "target": target, "payload": payload, "effect": "external",
        }],
        "sourced_facts": [], "authored_judgment": "已核实来源",
    })
    action = native["decision"]["payload"]["actions"][0]
    for name in ("target", "payload"):
        action[name] = _encode_dynamic(action[name])

    assert Draft202012Validator(native_output_schema()).is_valid(native)
    restored = json.loads(parse_native_output(_event(native)))
    restored_action = restored["payload"]["actions"][0]
    assert restored_action["target"] == target
    assert restored_action["payload"] == payload


@pytest.mark.parametrize(
    ("encoded", "message"),
    [
        ({"entries": [{"key": "x", "value": "first"},
                       {"key": "x", "value": "second"}]}, "duplicate"),
        ({"entries": [{"key": 1, "value": "value"}]}, "string key and value"),
        ({"entries": [{"key": "x"}]}, "string key and value"),
    ],
)
def test_native_consumer_rejects_ambiguous_or_malformed_dynamic_json_entries(
    encoded, message,
):
    dynamic_object = {
        "type": "object",
        "additionalProperties": {"type": "string"},
    }
    with pytest.raises(ValueError, match=message):
        _decode(encoded, dynamic_object, {})


def test_native_consumer_rejects_malformed_suffix_instead_of_salvaging_it():
    native = _consumer()
    with pytest.raises(ResultParseError):
        parse_native_output(_event(native)[:-2] + "}")
