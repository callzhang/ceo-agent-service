"""Compose anonymous business inputs with the actual context and candidate types."""

from __future__ import annotations

from app.agent_context import AgentTaskContext, AuditTurnContext, MaterialReference
from app.agent_contracts import ConsumerAgentResult
from app.reviewed_candidates import candidate_digest
from app.reviewed_sources import context_source


def task_context(case: dict, *, task_id: int = 1) -> AgentTaskContext:
    trigger = case["trigger"]
    materials = ()
    if "document" in case["phases"]["cold"]:
        document = case["phases"]["cold"]["document"]
        materials = (
            MaterialReference(
                kind="document",
                reference=document["node_id"],
                source_message_id=trigger["message_id"],
                read_commands=(
                    'agent_cli.read_dingtalk_document(node_id="'
                    + document["node_id"]
                    + '")',
                ),
            ),
        )
    return AgentTaskContext(
        task_id=task_id,
        channel=trigger["channel"],
        conversation_id=trigger["conversation_id"],
        conversation_title=trigger["conversation_title"],
        single_chat=False,
        trigger_message_id=trigger["message_id"],
        trigger_sender=trigger["sender"],
        trigger_text=trigger["text"],
        trigger_create_time=trigger["create_time"],
        messages=(),
        materials=materials,
        prior_receipts=(),
        trigger_raw_payload=trigger.get("raw_payload", {}),
        skill_names=(case["skill_name"],),
    )


def audit_context(case: dict, *, audit_rules: str) -> AuditTurnContext:
    task = task_context(case)
    facts = case["phases"]["cold"]
    false_claim = facts["candidate"]
    document = facts["document"]
    candidate = ConsumerAgentResult.model_validate(
        {
            "outcome": "proposal",
            "summary": false_claim["summary"],
            "proposal": {
                "objective": "Reply to the source document review request",
                "actions": [
                    {
                        "description": "Reply to the original document request",
                        "action_identity": "anonymous-document-review-reply",
                        "capability": "dingtalk-chat",
                        "operation": "reply_to_message",
                        "target": {
                            "conversation_id": task.conversation_id,
                            "message_id": task.trigger_message_id,
                        },
                        "payload": {"content": false_claim["reply_body"]},
                        "effect": "external",
                    }
                ],
                "sourced_facts": [
                    {
                        "assertion": document["content"],
                        "references": [document["node_id"]],
                    }
                ],
                "authored_judgment": false_claim["summary"],
            },
            "error": {"code": "", "retryable": False, "authorization_required": False},
            "risk": "low",
            "confidence": 1.0,
            "rule_coverage": 1.0,
            "information_completeness": 1.0,
            "source_bindings": [
                {
                    "provider": "task_context",
                    "object_ref": task.trigger_message_id,
                    "value": context_source(task),
                },
                {
                    "provider": "dingtalk-doc",
                    "object_ref": document["node_id"],
                    "value": document,
                },
            ],
        }
    )
    return AuditTurnContext(
        task=task,
        proposal_revision=0,
        operation_id="anonymous-audit-document-review",
        candidate=candidate,
        candidate_digest=candidate_digest(candidate),
        audit_rules=audit_rules,
    )
