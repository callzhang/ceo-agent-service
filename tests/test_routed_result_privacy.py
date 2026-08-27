import json

import pytest

from app.meeting_alignment_agent import _encode_meeting_alignment_result
from app.agent_runtime_router import RoutedResultCodec, RoutedResultValidationError
from app.structured_agent import _encode_structured_result
from app.task_agent import _encode_task_agent_result


def _raw_with_sensitive_audit_event(result: dict) -> str:
    return "\n".join(
        [
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "type": "tool_call",
                        "tool_name": "memory_recall",
                        "call_id": "call-private",
                        "arguments": {
                            "query": "private business evidence",
                            "path": "/Users/derek/Documents/private-plan.md",
                        },
                        "output": "private business evidence from local document",
                    },
                },
                ensure_ascii=False,
            ),
            json.dumps(result, ensure_ascii=False),
        ]
    )


def _assert_private_audit_material_is_not_persisted(encoded: str) -> None:
    assert "private business evidence" not in encoded
    assert "/Users/derek/Documents/private-plan.md" not in encoded
    payload = json.loads(encoded)
    assert payload["audit_tool_events"] == [
        {"tool": "memory_recall", "call_id": "call-private"}
    ]


def test_structured_result_codec_persists_only_audit_references():
    encoded = _encode_structured_result(
        _raw_with_sensitive_audit_event(
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
                    "summary": "valid",
                    "documents": [
                        {
                            "title": "private business evidence",
                            "url": "file:///Users/derek/Documents/private-plan.md",
                            "relevance": "private business evidence",
                        }
                    ],
                    "confidence": 1,
                },
            }
        )
    )

    _assert_private_audit_material_is_not_persisted(encoded)


def test_task_result_codec_persists_only_audit_references():
    encoded = _encode_task_agent_result(
        _raw_with_sensitive_audit_event(
            {
                "action": "discard",
                "discard_reason": "no durable task",
                "project": None,
                "todo_changes": [],
                "follow_up_drafts": [],
                "follow_up_changes": [],
                "update_summary": "discarded",
                "merge_reason": "",
                "memory_recall_used": False,
                "confidence": 0.8,
            }
        )
    )

    _assert_private_audit_material_is_not_persisted(encoded)


def test_task_result_parser_rejects_runtime_paths_before_persistence(monkeypatch):
    monkeypatch.setenv("CEO_FORBIDDEN_PATH_PREFIXES", "/Users/derek/")
    raw = json.dumps(
        {
            "action": "update_project",
            "project": {
                "id": 769,
                "title": "Memory",
                "facts": [
                    {
                        "description": (
                            "读取失败：/Users/derek/.dws/.data.lock，"
                            "无法确认当前状态。"
                        ),
                        "source": "live_read",
                    }
                ],
            },
            "todo_changes": [],
            "follow_up_drafts": [],
            "follow_up_changes": [],
            "update_summary": "保留当前项目，不创建新任务。",
            "merge_reason": "",
            "memory_recall_used": False,
            "confidence": 0.8,
            "failure_risk": "",
            "failure_risk_score": 0.2,
        },
        ensure_ascii=False,
    )

    with pytest.raises(RoutedResultValidationError, match="runtime path"):
        _encode_task_agent_result(raw)


def test_meeting_result_codec_persists_only_audit_references():
    encoded = _encode_meeting_alignment_result(
        _raw_with_sensitive_audit_event(
            {
                "action": "no_action",
                "trigger_reasons": [],
                "topics": [],
                "derek_viewpoint": None,
                "key_questions": [],
                "mention_names": [],
                "target": None,
                "final_message": "",
                "audit_summary": "no material disagreement",
                "confidence": 0.9,
            }
        )
    )

    _assert_private_audit_material_is_not_persisted(encoded)


def test_task_result_codec_allows_only_evidence_source_reference_paths(monkeypatch):
    monkeypatch.setenv("CEO_FORBIDDEN_PATH_PREFIXES", "/Users/derek/")
    codec = RoutedResultCodec.text(
        schema_id="task_agent.test.v1",
        allow_evidence_source_refs=True,
    )
    value = json.dumps(
        {
            "project": {
                "evidence": {
                    "source": "/Users/derek/Documents/memory/okr.md",
                }
            },
            "description": "keep this decision durable",
        },
        ensure_ascii=False,
    )

    encoded = codec.encode(value)
    assert codec.decode(encoded) == value

    with pytest.raises(ValueError, match="sensitive runtime data"):
        codec.encode(
            json.dumps(
                {"description": "/Users/derek/.codex/sessions/private.jsonl"},
                ensure_ascii=False,
            )
        )


def test_generic_result_codec_still_rejects_evidence_paths(monkeypatch):
    monkeypatch.setenv("CEO_FORBIDDEN_PATH_PREFIXES", "/Users/derek/")
    codec = RoutedResultCodec.text(schema_id="generic.test.v1")
    with pytest.raises(ValueError, match="sensitive runtime data"):
        codec.encode(
            json.dumps(
                {"source": "/Users/derek/Documents/memory/okr.md"},
                ensure_ascii=False,
            )
        )
