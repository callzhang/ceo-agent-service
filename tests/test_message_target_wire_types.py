import json

import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError
from pydantic import ValidationError

from app.agent_contracts import ConsumerAgentResult
from app.agent_result import ResultParseError
from app.agent_wire_contracts import ConsumerAgentWireResult, parse_consumer_agent_wire_result
from tests.test_agent_contracts import _consumer_wire_payload, _proposal


IDENTIFIERS = (
    "conversation_id", "message_id", "source_message_id", "open_dingtalk_id",
    "user_id", "recipient_open_dingtalk_id", "sender_open_dingtalk_id",
    "verified_participant_open_dingtalk_id",
)


def payload_for(target):
    proposal = _proposal()
    proposal["actions"][0].update(
        capability="dingtalk-chat", operation="send_direct_message",
        target=target, payload={"content": "Your materials have been received."},
    )
    return _consumer_wire_payload(outcome="proposal", proposal=proposal)


@pytest.mark.parametrize("field", IDENTIFIERS)
@pytest.mark.parametrize("wrong", [104265682397, 1.5, True, False, {"id": "recipient"}, ["recipient"], ""])
def test_new_wire_rejects_invalid_message_identifier_types(field, wrong):
    payload = payload_for({"user_id": "verified-user", "open_dingtalk_id": "00104", field: wrong})
    with pytest.raises(ValidationError, match=field):
        ConsumerAgentWireResult.model_validate(payload)
    with pytest.raises(JsonSchemaValidationError):
        Draft202012Validator(ConsumerAgentWireResult.model_json_schema()).validate(payload)


def test_wire_rejects_blank_identifier_even_with_another_valid_recipient():
    payload = payload_for({"user_id": "verified-user", "open_dingtalk_id": "  "})
    with pytest.raises(ValidationError):
        ConsumerAgentWireResult.model_validate(payload)
    with pytest.raises(JsonSchemaValidationError):
        Draft202012Validator(ConsumerAgentWireResult.model_json_schema()).validate(payload)


def test_digit_strings_are_preserved_exactly_and_optional_null_is_unset():
    target = {"open_dingtalk_id": "000104265682397", "conversation_id": None,
              "source_metadata": {"a/b~c": [7, False, None]}}
    payload = payload_for(target)
    Draft202012Validator(ConsumerAgentWireResult.model_json_schema()).validate(payload)
    result = ConsumerAgentWireResult.model_validate(payload).to_result()
    assert result.proposal.actions[0].target == target


def test_native_jsonl_parser_rejects_numeric_recipient_before_audit():
    payload = payload_for({"open_dingtalk_id": 104265682397})
    stream = json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(payload)}})
    with pytest.raises(ResultParseError):
        parse_consumer_agent_wire_result(stream)


def test_historical_domain_result_keeps_original_numeric_target_readable():
    payload = payload_for({"open_dingtalk_id": 104265682397})
    domain = {k: v for k, v in payload.items() if not k.startswith("error_")}
    domain["error"] = {"code": "", "retryable": False, "authorization_required": False}
    result = ConsumerAgentResult.model_validate_json(json.dumps(domain))
    assert result.proposal.actions[0].target["open_dingtalk_id"] == 104265682397


def test_other_capabilities_keep_json_target_values():
    payload = payload_for({"open_dingtalk_id": {"vendor": [7, None, False]}})
    payload["proposal"]["actions"][0]["capability"] = "extension-tool"
    ConsumerAgentWireResult.model_validate(payload)
    Draft202012Validator(ConsumerAgentWireResult.model_json_schema()).validate(payload)


def test_new_wire_validates_executable_human_branches():
    proposal = payload_for({"open_dingtalk_id": 104265682397})["proposal"]
    options = [
        {"key": "send", "label": "Notify", "instruction": "Use the selected rule",
         "consequence": "Recipient receives the notice", "plan": proposal},
        {"key": "stop", "label": "Stop", "instruction": "Do not send",
         "consequence": "No message", "terminal_outcome": "skipped", "reason": "Not required"},
    ]
    payload = _consumer_wire_payload(outcome="needs_human", decision_options=options)
    with pytest.raises(ValidationError, match="open_dingtalk_id"):
        ConsumerAgentWireResult.model_validate(payload)
    with pytest.raises(JsonSchemaValidationError):
        Draft202012Validator(ConsumerAgentWireResult.model_json_schema()).validate(payload)
