import json

import pytest
from pydantic import ValidationError

from app.agent_envelope import (
    AgentEnvelope,
    AgentKind,
    SendDingTalkReplyAction,
)


def test_agent_envelope_accepts_typed_system_action():
    envelope = AgentEnvelope.model_validate(
        {
            "kind": "reply",
            "user_response": {
                "mode": "send_reply",
                "text": "收到，我来处理。",
                "sensitivity_kind": "general",
            },
            "system_actions": [
                {"type": "send_dingtalk_reply", "reply_text_ref": "user_response.text"}
            ],
            "domain_payload": {},
            "audit": {
                "summary": "只需上下文判断。",
                "documents": [],
                "confidence": 0.9,
            },
        }
    )

    assert envelope.kind == AgentKind.REPLY
    assert isinstance(envelope.system_actions[0], SendDingTalkReplyAction)
    assert envelope.user_response.text == "收到，我来处理。"


def test_agent_envelope_requires_non_empty_audit_summary():
    with pytest.raises(ValidationError):
        AgentEnvelope.model_validate(
            {
                "kind": "reply",
                "user_response": {
                    "mode": "no_reply",
                    "text": "",
                    "sensitivity_kind": "general",
                },
                "system_actions": [],
                "domain_payload": {},
                "audit": {"summary": "", "documents": [], "confidence": 0.5},
            }
        )


def test_agent_envelope_accepts_handoff_response_mode():
    envelope = AgentEnvelope.model_validate(
        {
            "kind": "reply",
            "user_response": {
                "mode": "handoff_to_human",
                "text": "",
                "sensitivity_kind": "general",
            },
            "system_actions": [],
            "domain_payload": {},
            "audit": {
                "summary": "现实动作需要本人接管。",
                "documents": [],
                "confidence": 0.9,
            },
        }
    )

    assert envelope.user_response.mode == "handoff_to_human"


def test_agent_envelope_rejects_no_action_with_reply_mode():
    with pytest.raises(ValidationError, match="no_action"):
        AgentEnvelope.model_validate(
            {
                "kind": "no_action",
                "user_response": {
                    "mode": "send_reply",
                    "text": "不该发送",
                    "sensitivity_kind": "general",
                },
                "system_actions": [],
                "domain_payload": {},
                "audit": {
                    "summary": "无需回复。",
                    "documents": [],
                    "confidence": 0.9,
                },
            }
        )


def test_agent_envelope_rejects_error_with_reply_mode():
    with pytest.raises(ValidationError, match="error"):
        AgentEnvelope.model_validate(
            {
                "kind": "error",
                "user_response": {
                    "mode": "send_reply",
                    "text": "不该发送",
                    "sensitivity_kind": "general",
                },
                "system_actions": [],
                "domain_payload": {},
                "audit": {
                    "summary": "内部错误。",
                    "documents": [],
                    "confidence": 0.2,
                },
            }
        )


def test_agent_envelope_rejects_unknown_system_action():
    with pytest.raises(ValidationError):
        AgentEnvelope.model_validate(
            {
                "kind": "reply",
                "user_response": {
                    "mode": "send_reply",
                    "text": "ok",
                    "sensitivity_kind": "general",
                },
                "system_actions": [{"type": "unknown_action"}],
                "domain_payload": {},
                "audit": {
                    "summary": "valid summary",
                    "documents": [],
                    "confidence": 0.8,
                },
            }
        )


def test_agent_envelope_rejects_reply_action_without_send_reply_mode():
    with pytest.raises(ValidationError):
        AgentEnvelope.model_validate(
            {
                "kind": "reply",
                "user_response": {
                    "mode": "no_reply",
                    "text": "ok",
                    "sensitivity_kind": "general",
                },
                "system_actions": [
                    {
                        "type": "send_dingtalk_reply",
                        "reply_text_ref": "user_response.text",
                    }
                ],
                "domain_payload": {},
                "audit": {
                    "summary": "valid summary",
                    "documents": [],
                    "confidence": 0.8,
                },
            }
        )


def test_agent_envelope_rejects_reply_action_with_blank_text():
    with pytest.raises(ValidationError):
        AgentEnvelope.model_validate(
            {
                "kind": "reply",
                "user_response": {
                    "mode": "send_reply",
                    "text": " ",
                    "sensitivity_kind": "general",
                },
                "system_actions": [
                    {
                        "type": "send_dingtalk_reply",
                        "reply_text_ref": "user_response.text",
                    }
                ],
                "domain_payload": {},
                "audit": {
                    "summary": "valid summary",
                    "documents": [],
                    "confidence": 0.8,
                },
            }
        )


def test_agent_envelope_schema_is_strict():
    schema = json.loads(
        open("app/schemas/agent_envelope.schema.json", encoding="utf-8").read()
    )

    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    assert set(schema["$defs"]["UserResponse"]["required"]) == {
        "mode",
        "text",
        "sensitivity_kind",
    }
    assert set(schema["$defs"]["AgentAudit"]["required"]) == {
        "summary",
        "documents",
        "confidence",
    }
    assert set(schema["$defs"]["AgentAuditDocument"]["required"]) == {
        "title",
        "url",
        "relevance",
    }
    assert "handoff_to_human" in schema["$defs"]["UserResponseMode"]["enum"]
