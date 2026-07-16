import json

import pytest
from pydantic import ValidationError

from app.agent_envelope import (
    AgentEnvelope,
    AgentKind,
    DwsMailReplyAction,
    DwsMarkdownDocumentReplyAction,
    DwsMessageReactionAction,
    QueueOkrReviewAction,
    SendDingTalkReplyAction,
)


def test_agent_envelope_accepts_mail_reply_action():
    envelope = AgentEnvelope.model_validate(
        {
            "kind": "reply",
            "user_response": {
                "mode": "send_reply",
                "text": "邮件已审阅并回复。",
                "sensitivity_kind": "general",
            },
            "system_actions": [
                {
                    "type": "dws_mail_reply",
                    "mailbox": "derek@example.com",
                    "message_id": "mail-1",
                    "subject": "Re: 评奖结果",
                    "content": "评奖结果确认无误，可以按此发布。",
                },
                {"type": "send_dingtalk_reply", "reply_text_ref": "user_response.text"},
            ],
            "domain_payload": {},
            "audit": {
                "summary": "已读取原邮件和评奖材料。",
                "documents": [],
                "confidence": 0.9,
            },
        }
    )

    assert isinstance(envelope.system_actions[0], DwsMailReplyAction)


def test_agent_envelope_rejects_mail_reply_without_send_reply_mode():
    with pytest.raises(ValidationError):
        AgentEnvelope.model_validate(
            {
                "kind": "no_action",
                "user_response": {
                    "mode": "no_reply",
                    "text": "",
                    "sensitivity_kind": "general",
                },
                "system_actions": [
                    {
                        "type": "dws_mail_reply",
                        "mailbox": "derek@example.com",
                        "message_id": "mail-1",
                        "subject": "Re: 评奖结果",
                        "content": "确认无误。",
                    }
                ],
                "domain_payload": {},
                "audit": {
                    "summary": "invalid mail reply",
                    "documents": [],
                    "confidence": 0.9,
                },
            }
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


def test_agent_envelope_accepts_clarifying_question_reply_action():
    envelope = AgentEnvelope.model_validate(
        {
            "kind": "reply",
            "user_response": {
                "mode": "ask_clarifying_question",
                "text": "你给一个时间段和时长，我来约。",
                "sensitivity_kind": "general",
            },
            "system_actions": [
                {"type": "send_dingtalk_reply", "reply_text_ref": "user_response.text"}
            ],
            "domain_payload": {},
            "audit": {
                "summary": "对方要求安排会议，但缺少时间边界，需要追问。",
                "documents": [],
                "confidence": 0.9,
            },
        }
    )

    assert isinstance(envelope.system_actions[0], SendDingTalkReplyAction)
    assert envelope.user_response.mode == "ask_clarifying_question"


def test_agent_envelope_accepts_markdown_document_reply_action():
    envelope = AgentEnvelope.model_validate(
        {
            "kind": "reply",
            "user_response": {
                "mode": "send_reply",
                "text": "# 方案\n\n先按 A 路径推进。",
                "sensitivity_kind": "general",
            },
            "system_actions": [
                {"type": "send_dingtalk_reply", "reply_text_ref": "user_response.text"},
                {
                    "type": "dws_markdown_document_reply",
                    "reply_text_ref": "user_response.text",
                    "title": "方案建议",
                },
            ],
            "domain_payload": {},
            "audit": {
                "summary": "对方要求写方案，适合创建文档回复。",
                "documents": [],
                "confidence": 0.9,
            },
        }
    )

    assert isinstance(envelope.system_actions[1], DwsMarkdownDocumentReplyAction)
    assert envelope.system_actions[1].title == "方案建议"


def test_agent_envelope_accepts_queue_okr_review_action():
    envelope = AgentEnvelope.model_validate(
        {
            "kind": "okr_review",
            "user_response": {
                "mode": "no_reply",
                "text": "",
                "sensitivity_kind": "internal_personnel",
            },
            "system_actions": [{"type": "queue_okr_review"}],
            "domain_payload": {},
            "audit": {
                "summary": "用户明确要求审核自己的 OKR。",
                "documents": [],
                "confidence": 0.9,
            },
        }
    )

    assert envelope.kind == AgentKind.OKR_REVIEW
    assert isinstance(envelope.system_actions[0], QueueOkrReviewAction)


def test_agent_envelope_rejects_markdown_document_reply_without_reply_text():
    with pytest.raises(ValidationError):
        AgentEnvelope.model_validate(
            {
                "kind": "no_action",
                "user_response": {
                    "mode": "no_reply",
                    "text": "",
                    "sensitivity_kind": "general",
                },
                "system_actions": [
                    {
                        "type": "dws_markdown_document_reply",
                        "reply_text_ref": "user_response.text",
                        "title": "方案建议",
                    }
                ],
                "domain_payload": {},
                "audit": {
                    "summary": "invalid markdown document reply",
                    "documents": [],
                    "confidence": 0.9,
                },
            }
        )


def test_agent_envelope_normalizes_string_audit_documents():
    envelope = AgentEnvelope.model_validate(
        {
            "kind": "reply",
            "user_response": {
                "mode": "no_reply",
                "text": "",
                "sensitivity_kind": "general",
            },
            "system_actions": [],
            "domain_payload": {},
            "audit": {
                "summary": "已查看上下文。",
                "documents": ["OA审批表单详情"],
                "confidence": 0.8,
            },
        }
    )

    assert envelope.audit.documents[0].title == "OA审批表单详情"
    assert envelope.audit.documents[0].url == ""
    assert envelope.audit.documents[0].relevance == "mentioned"


def test_agent_envelope_accepts_no_reply_message_reaction_action():
    envelope = AgentEnvelope.model_validate(
        {
            "kind": "no_action",
            "user_response": {
                "mode": "no_reply",
                "text": "",
                "sensitivity_kind": "general",
            },
            "system_actions": [
                {
                    "type": "dws_message_reaction",
                    "reaction_type": "emoji",
                    "emoji": "👍",
                }
            ],
            "domain_payload": {},
            "audit": {
                "summary": "公告无需正式回复，但适合用表情表示支持。",
                "documents": [],
                "confidence": 0.9,
            },
        }
    )

    assert isinstance(envelope.system_actions[0], DwsMessageReactionAction)


def test_agent_envelope_accepts_text_emotion_without_precreated_ids():
    envelope = AgentEnvelope.model_validate(
        {
            "kind": "no_action",
            "user_response": {
                "mode": "no_reply",
                "text": "",
                "sensitivity_kind": "general",
            },
            "system_actions": [
                {
                    "type": "dws_message_reaction",
                    "reaction_type": "text_emotion",
                    "text": "我去摇人",
                }
            ],
            "domain_payload": {},
            "audit": {
                "summary": "只需要轻量承接真人处理。",
                "documents": [],
                "confidence": 0.9,
            },
        }
    )

    assert isinstance(envelope.system_actions[0], DwsMessageReactionAction)
    assert envelope.system_actions[0].text == "我去摇人"


def test_agent_envelope_normalizes_named_audit_documents():
    envelope = AgentEnvelope.model_validate(
        {
            "kind": "reply",
            "user_response": {
                "mode": "no_reply",
                "text": "",
                "sensitivity_kind": "general",
            },
            "system_actions": [],
            "domain_payload": {},
            "audit": {
                "summary": "已查看上下文。",
                "documents": [
                    {
                        "name": "OA 表单详情",
                        "status": "read",
                        "summary": "确认当前任务属于 Derek。",
                    }
                ],
                "confidence": 0.8,
            },
        }
    )

    assert envelope.audit.documents[0].title == "OA 表单详情"
    assert envelope.audit.documents[0].url == ""
    assert envelope.audit.documents[0].relevance == "确认当前任务属于 Derek。"


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


def test_agent_envelope_schema_uses_strict_object_shapes_required_by_codex():
    schema = json.loads(
        open("app/schemas/agent_envelope.schema.json", encoding="utf-8").read()
    )

    def assert_strict_objects(node: object) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" in node:
                assert node.get("additionalProperties") is not None
                assert set(node.get("required", [])) == set(node["properties"])
            for value in node.values():
                assert_strict_objects(value)
        elif isinstance(node, list):
            for item in node:
                assert_strict_objects(item)

    assert_strict_objects(schema)
